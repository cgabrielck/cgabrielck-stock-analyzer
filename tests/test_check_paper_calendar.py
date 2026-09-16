"""check_paper_calendar prints polygon_configured without a paid API call."""
import importlib.util
from pathlib import Path


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_paper_calendar.py"
    spec = importlib.util.spec_from_file_location("check_paper_calendar_mod", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_check_paper_calendar_prints_polygon_configured(capsys, monkeypatch):
    monkeypatch.setattr("backend.agents.polygon_equity.is_configured", lambda: False)
    mod = _load_script()
    rc = mod.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "polygon_configured=" in out
    assert "calendar=" in out
    assert "us_equity" in out
    assert "yahoo" in out.lower()
