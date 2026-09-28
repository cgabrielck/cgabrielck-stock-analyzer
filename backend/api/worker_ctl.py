"""Paper TradingWorker process control — start, stop, and status for the Cgab UI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.trading.strategies.registry import KNOWN_STRATEGIES
from backend.utils.constants import DATA_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]
HEARTBEAT_PATH = Path(DATA_DIR) / "worker_heartbeat.json"
PID_PATH = Path(DATA_DIR) / "worker.pid"
LOG_PATH = Path(DATA_DIR) / "worker.log"
STALE_AFTER_SEC = 90
STOP_WAIT_SEC = 12.0
STOP_POLL_SEC = 0.35
START_WAIT_SEC = 30.0
START_POLL_SEC = 0.4


def _is_paper() -> bool:
    return os.getenv("APCA_PAPER", "true").strip().lower() in ("1", "true", "yes")


def _ignore_hours() -> bool:
    return os.getenv("IGNORE_MARKET_HOURS", "").strip().lower() in ("1", "true", "yes")


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and int(code.value) == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _normalize_strategy(sid: Any) -> str:
    aliases = {
        "aggressive": "breakout",
        "hybrid": "adaptive",
        "reversion": "stable",
    }
    key = str(sid or "").strip().lower()
    return aliases.get(key, key)


def _kill_pid(pid: int) -> None:
    if not pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def _pids_from_python_cmdlines() -> List[int]:
    """Find leftover workers by command line (venv stub + interpreter child)."""
    marker = "backend.trading.engine.worker"
    found: List[int] = []
    if os.name == "nt":
        # Query python processes only — do not embed `marker` in the child command
        # line or this helper would match itself.
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -match '^python' } | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            raw = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return []
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            cmd = str((row or {}).get("CommandLine") or "")
            if marker not in cmd:
                continue
            try:
                found.append(int(row.get("ProcessId")))
            except (TypeError, ValueError):
                continue
        return found
    try:
        raw = subprocess.check_output(["pgrep", "-f", marker], timeout=5, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return []
    for line in raw.decode().split():
        try:
            found.append(int(line))
        except ValueError:
            continue
    return found


def _all_worker_pids() -> List[int]:
    pids: List[int] = []
    for candidate in (_read_pid_file(), _read_heartbeat().get("pid"), *_pids_from_python_cmdlines()):
        try:
            n = int(candidate)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in pids and pid_alive(n):
            pids.append(n)
    return pids


def _read_pid_file() -> Optional[int]:
    if not PID_PATH.exists():
        return None
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip().split()[0])
    except (ValueError, OSError):
        return None


def _read_heartbeat() -> Dict[str, Any]:
    if not HEARTBEAT_PATH.exists():
        return {}
    try:
        data = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def explain(status: Dict[str, Any], position_count: int = 0) -> Dict[str, Any]:
    """Human-readable why auto paper trading looks idle or empty."""
    reasons: List[str] = []
    code = "unknown"

    if not status.get("paper"):
        code = "not_paper"
        reasons.append("Account is not in Alpaca paper mode.")
    elif status.get("stale"):
        code = "stale"
        reasons.append("A worker PID exists but the heartbeat is stale — the process likely crashed.")
    elif not status.get("running"):
        code = "not_running"
        reasons.append("The paper worker is not running. Open AI Mode and click Start paper auto.")
    elif status.get("halted"):
        code = "halted"
        reasons.append("Kill switch is ON, so new buys are blocked. Exits can still run.")
    elif not status.get("market_hours") and not status.get("ignore_market_hours"):
        code = "off_hours"
        reasons.append(
            "Worker is alive on paper, but it only scans during US regular hours "
            "(about 09:30–16:00 ET) unless IGNORE_MARKET_HOURS=true."
        )
    elif position_count == 0:
        skips = status.get("skip_counts") if isinstance(status.get("skip_counts"), dict) else {}
        if skips.get("cadence_wait"):
            code = "cadence_wait"
            reasons.append(
                "Paper auto is ON, but weekly cadence is waiting for a US Monday rebalance "
                "(PANIC / high_volatility still trades). Default remains the 24h demo until you pick weekly."
            )
        elif status.get("scan_stale") or skips.get("research_stale"):
            code = "research_stale"
            reasons.append(
                "Paper auto is ON, but Scan/signal as-of is past SIGNAL max-age. "
                "No new buys (all strategies); last real fund scores are kept (as-of)."
            )
        else:
            code = "scanning_empty"
            reasons.append(
                "Paper auto is ON and scanning. Empty holdings means no fill yet — "
                "signals must pass entry threshold, Kelly size, RiskEngine, and mandate."
            )
            from backend.trading.strategies.skip_codes import format_skip_lines

            line = format_skip_lines(skips, lang="en")
            if line:
                reasons.append(line)
    else:
        code = "active"
        reasons.append("Paper auto is ON. Holdings below are Alpaca paper fills.")

    zh = {
        "not_paper": "目前不是 Alpaca 模擬盤（paper）。請確認 APCA_PAPER=true。",
        "not_running": "自動交易尚未啟動。請到「AI 模式」按下「啟動紙上自動交易」。",
        "stale": "偵測到舊的工作行程，但心跳已過期，行程可能已當掉。請先停止再重新啟動。",
        "halted": "緊急停止（Kill switch）已開啟，不會開新倉。可在 Desk 解除。",
        "off_hours": "紙上自動交易已在跑，但美股盤外會暫停掃描（約美東 09:30–16:00）。若 .env 設 IGNORE_MARKET_HOURS=true 則全日掃描。",
        "cadence_wait": "紙上自動交易已開，但週頻節奏要等到美東週一再平衡（PANIC／高波動仍可交易）。預設仍是全日 demo，除非你在 AI 模式改成 weekly。",
        "research_stale": "紙上自動交易已開，但掃描／訊號 as-of 超過 SIGNAL 新鮮度視窗。所有策略不開新倉；上次真實分數保留。請等待自動掃描或手動掃描。",
        "scanning_empty": "紙上自動交易已開啟並正在掃描。持倉空白代表還沒有成交——看 Why-no-trade 代碼。",
        "active": "紙上自動交易進行中。下方持倉來自 Alpaca 模擬成交。",
        "unknown": "無法判斷自動交易狀態。",
    }
    en = {
        "not_paper": "Not Alpaca paper mode. Keep APCA_PAPER=true.",
        "not_running": "Auto trading is off. Open AI Mode and click Start paper auto.",
        "stale": "A worker was started but its heartbeat is stale. Stop and start again.",
        "halted": "Kill switch is on — no new buys. Resume from Desk if that was accidental.",
        "off_hours": "Paper worker is alive but waiting for US regular hours (09:30–16:00 ET) unless IGNORE_MARKET_HOURS=true.",
        "cadence_wait": "Paper auto is on, but weekly cadence waits for a US Monday rebalance (PANIC still trades).",
        "research_stale": "Paper auto is on, but Scan/signal as-of is past SIGNAL max-age. No new buys; last real fund scores are kept (as-of).",
        "scanning_empty": "Paper auto is on and scanning. Empty book is normal until a signal fills — see why-no-trade codes.",
        "active": "Paper auto is on. Holdings are Alpaca paper fills.",
        "unknown": "Could not determine auto-trading status.",
    }
    return {
        "code": code,
        "reasons": reasons,
        "message_en": en.get(code, en["unknown"]),
        "message_zh": zh.get(code, zh["unknown"]),
    }


def status(position_count: int = 0, halted: bool = False) -> Dict[str, Any]:
    hb = _read_heartbeat()
    file_pid = _read_pid_file()
    hb_pid = hb.get("pid")
    try:
        hb_pid_i = int(hb_pid) if hb_pid is not None else None
    except (TypeError, ValueError):
        hb_pid_i = None
    pid = hb_pid_i or file_pid
    alive = pid_alive(pid) if pid else False
    ts = _parse_ts(hb.get("ts") or hb.get("last_run"))
    age = None
    if ts:
        age = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    stale = bool(alive and age is not None and age > STALE_AFTER_SEC)
    heartbeat_fresh = bool(age is not None and age <= STALE_AFTER_SEC)
    if alive:
        running = True
    elif hb_pid_i is None and heartbeat_fresh and hb.get("running") is True:
        # Legacy CLI worker that wrote a heartbeat without pid.
        running = True
    else:
        running = False
    paper = bool(hb.get("paper", _is_paper()))
    market_hours = bool(hb.get("market_hours")) if hb else False
    payload = {
        "running": running,
        "alive": alive,
        "stale": stale,
        "pid": pid if alive else None,
        "paper": paper,
        "mode": hb.get("mode") or ("paper" if paper else "unknown"),
        "strategy": hb.get("strategy"),
        "market_hours": market_hours,
        "ignore_market_hours": bool(hb.get("ignore_market_hours", _ignore_hours())),
        "last_run": hb.get("last_run") or hb.get("ts"),
        "heartbeat_ts": hb.get("ts"),
        "heartbeat_age_sec": round(age, 1) if age is not None else None,
        "halted": bool(hb.get("halted", halted)),
        "last_signals": hb.get("last_signals") or [],
        "skip_counts": hb.get("skip_counts") if isinstance(hb.get("skip_counts"), dict) else {},
        "pending_buys": [str(s) for s in (hb.get("pending_buys") or []) if s],
        "skip_by_ticker": (
            {str(k).upper(): str(v) for k, v in hb.get("skip_by_ticker").items() if k}
            if isinstance(hb.get("skip_by_ticker"), dict)
            else {}
        ),
        "universe_cap": hb.get("universe_cap"),
        "universe_size": hb.get("universe_size"),
        "fetched": hb.get("fetched"),
        "interval_seconds": hb.get("interval_seconds"),
        "log_file": str(LOG_PATH),
        "cadence": hb.get("cadence") or "intraday",
    }
    scan_stale = bool(hb.get("scan_stale"))
    scan_as_of = None
    try:
        from backend.api.research_jobs import latest_scan
        from backend.api import scan_scheduler

        scan = latest_scan()
        if not scan.get("available") or scan.get("stale"):
            scan_stale = True
        payload["scan_ts"] = scan.get("ts")
        payload["scan_as_of"] = scan.get("ts")
        payload["scan_top5"] = scan.get("top5_tickers") or []
        scan_as_of = scan.get("ts")
        sched = scan_scheduler.status()
        payload.update(
            {
                "scan_auto": sched.get("scan_auto"),
                "next_scan_at": sched.get("next_scan_at"),
                "last_auto_scan_at": sched.get("last_auto_scan_at"),
                "scan_interval_min": sched.get("scan_interval_min"),
                "in_scan_window": sched.get("in_scan_window"),
            }
        )
    except Exception:
        scan_stale = True
        payload.setdefault("scan_auto", False)
        payload.setdefault("next_scan_at", None)
    payload["scan_stale"] = scan_stale
    if scan_as_of and "scan_as_of" not in payload:
        payload["scan_as_of"] = scan_as_of
    if hb.get("signal_max_age_hours") is not None:
        payload["signal_max_age_hours"] = hb.get("signal_max_age_hours")
    else:
        try:
            from backend.api.research_jobs import signal_max_age_hours as _signal_max_age

            payload["signal_max_age_hours"] = _signal_max_age()
        except Exception:
            payload["signal_max_age_hours"] = None
    payload["explain"] = explain({**payload, "halted": payload["halted"] or halted}, position_count)
    return payload


def start(strategy: str = "breakout", interval: int = 60) -> Dict[str, Any]:
    if not _is_paper():
        raise RuntimeError("Live trading is blocked. Keep APCA_PAPER=true.")
    if strategy not in KNOWN_STRATEGIES:
        raise ValueError("Unknown strategy")
    interval = max(15, min(600, int(interval)))
    current = status()
    live_pids = _all_worker_pids()
    same = _normalize_strategy(current.get("strategy")) == _normalize_strategy(strategy)
    # Windows venv launches a stub + interpreter child (2 PIDs). More than that is stray.
    if current.get("running") and same and len(live_pids) <= 2:
        return {**current, "already_running": True}
    if live_pids:
        stop()

    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["APCA_PAPER"] = "true"
    pythonpath = os.pathsep.join(
        [p for p in [str(REPO_ROOT), env.get("PYTHONPATH") or ""] if p]
    )
    env["PYTHONPATH"] = pythonpath

    cmd = [
        sys.executable,
        "-m",
        "backend.trading.engine.worker",
        "--mode",
        "paper",
        "--strategy",
        strategy,
        "--interval",
        str(interval),
        "--heartbeat-file",
        str(HEARTBEAT_PATH),
    ]
    logf = open(LOG_PATH, "a", encoding="utf-8")
    logf.write(
        f"\n--- start {datetime.now(timezone.utc).isoformat()} strategy={strategy} interval={interval} ---\n"
    )
    logf.flush()
    kwargs: Dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "env": env,
        "stdout": logf,
        "stderr": subprocess.STDOUT,
        "close_fds": False if os.name == "nt" else True,
    }
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = flags | no_window
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + float(START_WAIT_SEC)
    last = status()
    hb_pid: Optional[int] = None
    while time.time() < deadline:
        hb = _read_heartbeat()
        try:
            hb_pid = int(hb["pid"]) if hb.get("pid") is not None else None
        except (TypeError, ValueError):
            hb_pid = None
        if hb_pid and pid_alive(hb_pid):
            PID_PATH.write_text(str(hb_pid), encoding="utf-8")
            last = status()
            break
        last = status()
        if proc.poll() is not None and not (hb_pid and pid_alive(hb_pid)):
            break
        time.sleep(float(START_POLL_SEC))
    if not (hb_pid and pid_alive(hb_pid)):
        _kill_pid(proc.pid)
        for extra in _all_worker_pids():
            _kill_pid(extra)
        tail = ""
        try:
            tail = LOG_PATH.read_text(encoding="utf-8")[-1200:]
        except OSError:
            pass
        raise RuntimeError(f"Worker failed to start (code {proc.poll()}). {tail}")
    return {**last, "already_running": False, "started_pid": hb_pid or proc.pid}


def restart(strategy: str = "breakout", interval: int = 60) -> Dict[str, Any]:
    """Stop every leftover worker then start with ``strategy``."""
    stop()
    return start(strategy=strategy, interval=interval)


def _mark_heartbeat_stopped() -> None:
    hb = _read_heartbeat()
    payload = {**hb, "running": False, "pid": None, "ts": datetime.now(timezone.utc).isoformat()}
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def stop() -> Dict[str, Any]:
    """Kill pidfile + heartbeat + cmdline workers until none remain.

    Raises if any worker PID is still alive after STOP_WAIT_SEC so Telegram/Desk
    cannot report a successful stop while a venv stub or interpreter survives.
    """
    deadline = time.time() + float(STOP_WAIT_SEC)
    leftover: List[int] = []
    while True:
        leftover = _all_worker_pids()
        if not leftover:
            break
        for pid in leftover:
            _kill_pid(pid)
        leftover = _all_worker_pids()
        if not leftover:
            break
        if time.time() >= deadline:
            raise RuntimeError(f"Worker did not stop; leftover pids {leftover}")
        time.sleep(float(STOP_POLL_SEC))
    try:
        if PID_PATH.exists():
            PID_PATH.unlink()
    except OSError:
        pass
    try:
        _mark_heartbeat_stopped()
    except OSError:
        pass
    return status()
