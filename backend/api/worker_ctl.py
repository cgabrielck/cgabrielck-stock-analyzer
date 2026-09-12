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
        if status.get("scan_stale") or skips.get("research_stale"):
            code = "research_stale"
            reasons.append(
                "Paper auto is ON, but the Scan book is past the session freshness window. "
                "research_list will not open new buys; Stable still uses the last real fund scores (as-of)."
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
        "research_stale": "紙上自動交易已開，但掃描名單超過盤中新鮮度視窗。research_list 不開新倉；Stable 仍用上次真實分數（as-of）。請等待自動掃描或手動掃描。",
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
        "research_stale": "Paper auto is on, but Scan is past the session freshness window. research_list blocks new buys; Stable keeps last real fund scores (as-of).",
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
        "universe_cap": hb.get("universe_cap"),
        "universe_size": hb.get("universe_size"),
        "fetched": hb.get("fetched"),
        "interval_seconds": hb.get("interval_seconds"),
        "log_file": str(LOG_PATH),
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
    payload["explain"] = explain({**payload, "halted": payload["halted"] or halted}, position_count)
    return payload


def start(strategy: str = "stable", interval: int = 60) -> Dict[str, Any]:
    if not _is_paper():
        raise RuntimeError("Live trading is blocked. Keep APCA_PAPER=true.")
    if strategy not in KNOWN_STRATEGIES:
        raise ValueError("Unknown strategy")
    interval = max(15, min(600, int(interval)))
    current = status()
    if current.get("running"):
        return {**current, "already_running": True}

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
    deadline = time.time() + 8
    last = status()
    while time.time() < deadline:
        hb = _read_heartbeat()
        hb_pid = None
        try:
            hb_pid = int(hb["pid"]) if hb.get("pid") is not None else None
        except (TypeError, ValueError):
            hb_pid = None
        if hb_pid and pid_alive(hb_pid):
            PID_PATH.write_text(str(hb_pid), encoding="utf-8")
        last = status()
        if last.get("running") and last.get("strategy"):
            break
        if proc.poll() is not None and not (hb_pid and pid_alive(hb_pid)):
            break
        time.sleep(0.4)
    if not last.get("running"):
        tail = ""
        try:
            tail = LOG_PATH.read_text(encoding="utf-8")[-1200:]
        except OSError:
            pass
        raise RuntimeError(f"Worker failed to start (code {proc.poll()}). {tail}")
    return {**last, "already_running": False, "started_pid": last.get("pid") or proc.pid}


def stop() -> Dict[str, Any]:
    hb = _read_heartbeat()
    pids = []
    for candidate in (_read_pid_file(), hb.get("pid")):
        try:
            n = int(candidate)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in pids:
            pids.append(n)
    for pid in pids:
        if not pid_alive(pid):
            continue
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    time.sleep(0.4)
    try:
        if PID_PATH.exists():
            PID_PATH.unlink()
    except OSError:
        pass
    # Mark heartbeat stopped so UI does not treat a leftover file as live.
    try:
        payload = {**hb, "running": False, "ts": datetime.now(timezone.utc).isoformat()}
        HEARTBEAT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
    return status()
