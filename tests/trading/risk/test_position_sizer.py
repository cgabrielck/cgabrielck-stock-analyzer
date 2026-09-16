from backend.trading.risk.position_sizer import cap_shares_to_notional


def test_cap_shares_floors_to_notional_limit():
    # 33 * 349.4 = 11,530.2 > 10_000 → 28 shares * 349.4 = 9,783.2
    assert cap_shares_to_notional(33, 349.4, 10_000) == 28


def test_cap_shares_leaves_small_orders():
    assert cap_shares_to_notional(10, 200.0, 10_000) == 10


def test_cap_shares_zero_when_one_share_too_expensive():
    assert cap_shares_to_notional(5, 12_000.0, 10_000) == 0
