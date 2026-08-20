"""Tests for kill_switch.py — emergency trading halt (module-function API)."""
import pytest
from pathlib import Path

from backend.trading.safety import kill_switch


@pytest.fixture(autouse=True)
def isolated_switch(tmp_path, monkeypatch):
    """Redirect the kill-switch sentinel file into a temp dir per test."""
    fake_file = tmp_path / "KILL_SWITCH"
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", fake_file)
    yield fake_file


def test_initial_state_disengaged():
    """By default, the kill switch is off."""
    assert not kill_switch.is_halted()
    assert kill_switch.get_reason() is None


def test_engage_and_disengage():
    """Engage with a reason, then disengage."""
    kill_switch.engage("Market crash detected")
    assert kill_switch.is_halted()
    assert kill_switch.get_reason() == "Market crash detected"

    kill_switch.disengage()
    assert not kill_switch.is_halted()
    assert kill_switch.get_reason() is None


def test_persistence_via_file(isolated_switch):
    """State is written to disk (the sentinel file exists when engaged)."""
    kill_switch.engage("API outage")
    assert isolated_switch.exists()
    assert kill_switch.is_halted()


def test_engage_default_reason():
    """Engage with no reason writes a default message."""
    kill_switch.engage()
    assert kill_switch.is_halted()
    assert "Manual halt" in (kill_switch.get_reason() or "")


def test_re_engage_overwrites_reason():
    """Re-engaging updates the reason."""
    kill_switch.engage("first reason")
    kill_switch.engage("second reason")
    assert kill_switch.get_reason() == "second reason"


def test_disengage_when_already_off():
    """Idempotent — disengage when off is a no-op."""
    kill_switch.disengage()
    assert not kill_switch.is_halted()


def test_engage_creates_parent_dir(tmp_path, monkeypatch):
    """engage() creates the parent directory if missing."""
    nested = tmp_path / "deep" / "nested" / "KILL_SWITCH"
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", nested)
    kill_switch.engage("test")
    assert nested.exists()
    assert kill_switch.is_halted()
