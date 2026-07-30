from pathlib import Path


def test_option_lifecycle_migration_contains_required_safety_contracts() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "backend/persistence/migrations/007_option_alert_lifecycle.sql"
    ).read_text()

    for event_type in ("option_entry", "option_stop", "option_target_1", "option_target_2"):
        assert f"'{event_type}'" in migration
    assert "create table if not exists public.option_positions" in migration
    assert "lifecycle_state = 'entry_alerted'" in migration
    assert "confirm_option_position_entry" in migration
    assert "monitoring_enabled = true" in migration
    assert "event_type in ('option_stop', 'option_target_1', 'option_target_2')" in migration
    assert "set monitoring_enabled = false, armed = false" in migration
    assert "option position must be resolved before replacing alert rules" in migration
    assert "option position must be resolved before replacing saved plan" in migration
    assert "lifecycle_state = 'stopped'" in migration
    assert "lifecycle_state = 'closed'" in migration
    assert "ae.user_id = p_user_id" in migration
