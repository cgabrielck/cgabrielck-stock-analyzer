import json

import pytest

from backend.api.worker_ctl import explain


def test_explain_not_running():
    out = explain({"paper": True, "running": False, "stale": False, "halted": False, "market_hours": False})
    assert out["code"] == "not_running"
    assert "AI Mode" in out["message_en"]
    assert "AI 模式" in out["message_zh"]


def test_explain_off_hours():
    out = explain({"paper": True, "running": True, "stale": False, "halted": False, "market_hours": False}, 0)
    assert out["code"] == "off_hours"


def test_explain_scanning_empty():
    out = explain({"paper": True, "running": True, "stale": False, "halted": False, "market_hours": True}, 0)
    assert out["code"] == "scanning_empty"


def test_explain_active_with_positions():
    out = explain({"paper": True, "running": True, "stale": False, "halted": False, "market_hours": True}, 2)
    assert out["code"] == "active"


def test_explain_stale():
    out = explain({"paper": True, "running": False, "stale": True, "halted": False, "market_hours": True}, 0)
    assert out["code"] == "stale"


def test_explain_ignore_hours_counts_as_scanning():
    out = explain(
        {
            "paper": True,
            "running": True,
            "stale": False,
            "halted": False,
            "market_hours": False,
            "ignore_market_hours": True,
        },
        0,
    )
    assert out["code"] == "scanning_empty"


def test_explain_halted():
    out = explain({"paper": True, "running": True, "stale": False, "halted": True, "market_hours": True}, 0)
    assert out["code"] == "halted"


def test_explain_research_stale_when_empty_book():
    out = explain(
        {
            "paper": True,
            "running": True,
            "stale": False,
            "halted": False,
            "market_hours": True,
            "scan_stale": True,
        },
        0,
    )
    assert out["code"] == "research_stale"
    assert "as-of" in out["message_en"].lower() or "research_list" in out["message_en"].lower()


def test_explain_active_even_if_scan_stale_with_positions():
    out = explain(
        {
            "paper": True,
            "running": True,
            "stale": False,
            "halted": False,
            "market_hours": True,
            "scan_stale": True,
        },
        2,
    )
    assert out["code"] == "active"


def test_explain_cadence_wait():
    out = explain(
        {
            "paper": True,
            "running": True,
            "stale": False,
            "halted": False,
            "market_hours": True,
            "skip_counts": {"cadence_wait": 8},
        },
        0,
    )
    assert out["code"] == "cadence_wait"
    assert "Monday" in out["message_en"] or "weekly" in out["message_en"]
    assert "週" in out["message_zh"] or "PANIC" in out["message_zh"]


def test_restart_stops_then_starts(monkeypatch):
    from backend.api import worker_ctl

    seq = []
    monkeypatch.setattr(worker_ctl, "_all_worker_pids", lambda: [])
    monkeypatch.setattr(worker_ctl, "status", lambda: {"running": False})
    monkeypatch.setattr(worker_ctl, "stop", lambda: seq.append("stop") or {"running": False})
    monkeypatch.setattr(
        worker_ctl,
        "start",
        lambda strategy="breakout", interval=60: seq.append(strategy) or {"running": True, "strategy": strategy},
    )
    out = worker_ctl.restart("research_list", 60)
    assert seq == ["stop", "research_list"]
    assert out["strategy"] == "research_list"


def test_stop_raises_when_pids_survive(monkeypatch, tmp_path):
    from backend.api import worker_ctl

    hb = tmp_path / "worker_heartbeat.json"
    pidf = tmp_path / "worker.pid"
    hb.write_text(json.dumps({"running": True, "pid": 2152, "strategy": "breakout"}), encoding="utf-8")
    pidf.write_text("9612", encoding="utf-8")
    monkeypatch.setattr(worker_ctl, "HEARTBEAT_PATH", hb)
    monkeypatch.setattr(worker_ctl, "PID_PATH", pidf)
    monkeypatch.setattr(worker_ctl, "STOP_WAIT_SEC", 0)
    monkeypatch.setattr(worker_ctl.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_ctl, "_all_worker_pids", lambda: [9612, 2152])
    killed = []
    monkeypatch.setattr(worker_ctl, "_kill_pid", lambda pid: killed.append(pid))

    with pytest.raises(RuntimeError, match="did not stop"):
        worker_ctl.stop()

    assert 9612 in killed
    assert 2152 in killed
    leftover = json.loads(hb.read_text(encoding="utf-8"))
    assert leftover.get("pid") == 2152
    assert leftover.get("running") is True
    assert pidf.exists()


def test_stop_clears_heartbeat_pid_when_workers_die(monkeypatch, tmp_path):
    from backend.api import worker_ctl

    hb = tmp_path / "worker_heartbeat.json"
    pidf = tmp_path / "worker.pid"
    hb.write_text(json.dumps({"running": True, "pid": 2152, "strategy": "breakout"}), encoding="utf-8")
    pidf.write_text("9612", encoding="utf-8")
    monkeypatch.setattr(worker_ctl, "HEARTBEAT_PATH", hb)
    monkeypatch.setattr(worker_ctl, "PID_PATH", pidf)
    monkeypatch.setattr(worker_ctl, "_all_worker_pids", lambda: [])
    monkeypatch.setattr(worker_ctl, "_kill_pid", lambda pid: None)
    monkeypatch.setattr(worker_ctl, "status", lambda: {"running": False, "pid": None})

    out = worker_ctl.stop()
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["running"] is False
    assert data.get("pid") is None
    assert not pidf.exists()
    assert out["running"] is False


def test_stop_retries_until_all_pids_gone(monkeypatch, tmp_path):
    from backend.api import worker_ctl

    hb = tmp_path / "worker_heartbeat.json"
    pidf = tmp_path / "worker.pid"
    hb.write_text(json.dumps({"running": True, "pid": 2152}), encoding="utf-8")
    pidf.write_text("9612", encoding="utf-8")
    monkeypatch.setattr(worker_ctl, "HEARTBEAT_PATH", hb)
    monkeypatch.setattr(worker_ctl, "PID_PATH", pidf)
    monkeypatch.setattr(worker_ctl.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_ctl, "status", lambda: {"running": False})

    snapshots = [
        [9612, 2152],
        [9612, 2152],
        [2152],
        [2152],
        [],
    ]

    def fake_pids():
        return list(snapshots.pop(0)) if snapshots else []

    killed = []
    monkeypatch.setattr(worker_ctl, "_all_worker_pids", fake_pids)
    monkeypatch.setattr(worker_ctl, "_kill_pid", lambda pid: killed.append(pid))

    worker_ctl.stop()
    assert 9612 in killed
    assert 2152 in killed
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["running"] is False
    assert data.get("pid") is None


class _FakeProc:
    def __init__(self, pid: int = 9612, exit_code=None):
        self.pid = pid
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code


def _patch_start_io(monkeypatch, tmp_path, worker_ctl):
    hb = tmp_path / "worker_heartbeat.json"
    pidf = tmp_path / "worker.pid"
    logf = tmp_path / "worker.log"
    logf.write_text("", encoding="utf-8")
    monkeypatch.setattr(worker_ctl, "HEARTBEAT_PATH", hb)
    monkeypatch.setattr(worker_ctl, "PID_PATH", pidf)
    monkeypatch.setattr(worker_ctl, "LOG_PATH", logf)
    monkeypatch.setattr(worker_ctl, "_is_paper", lambda: True)
    monkeypatch.setattr(worker_ctl.time, "sleep", lambda *_a, **_k: None)
    return hb, pidf, logf


def test_start_timeout_kills_spawned_child(monkeypatch, tmp_path):
    from backend.api import worker_ctl

    _patch_start_io(monkeypatch, tmp_path, worker_ctl)
    monkeypatch.setattr(worker_ctl, "START_WAIT_SEC", 0)
    monkeypatch.setattr(worker_ctl, "_all_worker_pids", lambda: [])
    monkeypatch.setattr(worker_ctl, "status", lambda: {"running": False, "strategy": None})
    monkeypatch.setattr(worker_ctl, "_read_heartbeat", lambda: {})
    monkeypatch.setattr(worker_ctl, "pid_alive", lambda pid: False)
    killed = []
    monkeypatch.setattr(worker_ctl, "_kill_pid", lambda pid: killed.append(pid))
    monkeypatch.setattr(worker_ctl.subprocess, "Popen", lambda *a, **k: _FakeProc(9612))

    with pytest.raises(RuntimeError, match="failed to start"):
        worker_ctl.start("breakout", 60)
    assert 9612 in killed


def test_start_succeeds_when_heartbeat_pid_alive(monkeypatch, tmp_path):
    from backend.api import worker_ctl

    hb, pidf, _logf = _patch_start_io(monkeypatch, tmp_path, worker_ctl)
    hb.write_text(
        json.dumps({"running": True, "pid": 2152, "strategy": "breakout"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(worker_ctl, "_all_worker_pids", lambda: [])
    monkeypatch.setattr(worker_ctl, "pid_alive", lambda pid: pid == 2152)

    calls = {"n": 0}

    def fake_status():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"running": False, "strategy": None, "pid": None}
        return {"running": True, "strategy": "breakout", "pid": 2152}

    monkeypatch.setattr(worker_ctl, "status", fake_status)
    monkeypatch.setattr(worker_ctl.subprocess, "Popen", lambda *a, **k: _FakeProc(9612))
    monkeypatch.setattr(worker_ctl, "_kill_pid", lambda pid: (_ for _ in ()).throw(AssertionError("should not kill")))

    out = worker_ctl.start("breakout", 60)
    assert out["already_running"] is False
    assert out["started_pid"] == 2152
    assert pidf.read_text(encoding="utf-8").strip() == "2152"
