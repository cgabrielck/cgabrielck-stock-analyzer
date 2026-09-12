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
