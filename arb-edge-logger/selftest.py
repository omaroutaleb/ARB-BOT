"""Self-test for math_core.py.

These tests inject synthetic order books with KNOWN PROPERTIES and verify the
logger reports the right answer. If any test fails, the logger is not trusted
and the entrypoint script (logger.py) refuses to start.

The point: before any real-API data is recorded, prove the math is right.

Run standalone:
    python -m selftest
Exit code 0 = all pass, 1 = any failure.
"""

from __future__ import annotations

import sys

from math_core import (
    BookLevel,
    SKEW_RELIABLE_THRESHOLD_MS,
    evaluate_cross_venue,
    evaluate_yes_no_complementarity,
    limitless_taker_fee_usd,
    polymarket_taker_fee_usd,
    walk_asks_for_usd_buy,
    walk_bids_for_usd_sell,
)


# ---------- Helpers ----------

class FailedTest(Exception):
    pass


def expect(label: str, actual, expected, tol: float = 1e-6):
    if expected is None:
        ok = actual is None
    elif actual is None:
        ok = False
    elif isinstance(expected, bool):
        ok = actual is expected
    else:
        ok = abs(float(actual) - float(expected)) <= tol
    if not ok:
        raise FailedTest(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  ok  {label}: {actual!r}")


def section(name: str):
    print(f"\n=== {name} ===")


# ---------- Book-walk tests ----------

def test_walk_asks_single_level_partial():
    section("walk_asks: single level, partial fill")
    asks = [BookLevel(price=0.50, size=1000)]
    r = walk_asks_for_usd_buy(asks, target_usd=100)
    expect("filled_shares", r.filled_shares, 200.0)
    expect("avg_price", r.avg_price, 0.50)
    expect("depth_exhausted", r.depth_exhausted, False)


def test_walk_asks_eats_through_two_levels():
    section("walk_asks: walks through two levels, weighted average")
    asks = [
        BookLevel(price=0.40, size=100),    # $40 capacity
        BookLevel(price=0.50, size=500),    # $250 capacity
    ]
    # Want $100 worth. Eat all of level 1 ($40 -> 100 shares), then $60 of level 2 -> 120 shares
    # Total: 220 shares for $100 spent. Avg price = 100/220 = 0.4545...
    r = walk_asks_for_usd_buy(asks, target_usd=100)
    expect("filled_shares", r.filled_shares, 220.0)
    expect("filled_usd", r.filled_usd, 100.0)
    expect("avg_price", r.avg_price, 100.0 / 220.0)
    expect("depth_exhausted", r.depth_exhausted, False)


def test_walk_asks_exhausts_book():
    section("walk_asks: depth exhausted, flagged")
    asks = [BookLevel(price=0.40, size=100)]      # only $40 capacity
    r = walk_asks_for_usd_buy(asks, target_usd=500)
    expect("filled_shares", r.filled_shares, 100.0)
    expect("filled_usd", r.filled_usd, 40.0)
    expect("avg_price", r.avg_price, 0.40)
    expect("depth_exhausted", r.depth_exhausted, True)


def test_walk_asks_empty_book():
    section("walk_asks: empty book")
    r = walk_asks_for_usd_buy([], target_usd=100)
    expect("filled_shares", r.filled_shares, 0.0)
    expect("avg_price", r.avg_price, None)
    expect("depth_exhausted", r.depth_exhausted, True)


def test_walk_asks_rejects_unsorted():
    section("walk_asks: rejects unsorted input (catches programmer error)")
    asks = [BookLevel(price=0.60, size=100), BookLevel(price=0.40, size=100)]
    try:
        walk_asks_for_usd_buy(asks, target_usd=100)
    except ValueError:
        print("  ok  raised ValueError on misordered input")
        return
    raise FailedTest("expected ValueError on misordered asks, got none")


def test_walk_bids():
    section("walk_bids: descending, partial fill")
    bids = [BookLevel(price=0.55, size=100), BookLevel(price=0.50, size=200)]
    r = walk_bids_for_usd_sell(bids, target_usd=100)
    # Take $55 at p=0.55 (100 shares × 0.55), then need $45 more at p=0.50 -> 90 shares
    expect("filled_shares", r.filled_shares, 190.0)
    expect("filled_usd", r.filled_usd, 100.0)
    expect("avg_price", r.avg_price, 100.0 / 190.0)


def test_walk_skips_zero_levels():
    section("walk_asks: skips zero-price and zero-size levels (defensive)")
    asks = [
        BookLevel(price=0, size=1000),
        BookLevel(price=0.50, size=0),
        BookLevel(price=0.50, size=1000),
    ]
    r = walk_asks_for_usd_buy(asks, target_usd=100)
    expect("filled_shares", r.filled_shares, 200.0)
    expect("avg_price", r.avg_price, 0.50)


# ---------- Fee tests ----------

def test_limitless_uses_market_meta_when_provided():
    section("limitless fee: prefers market metadata over fallback curve")
    fee, src = limitless_taker_fee_usd(
        notional_usd=100, price=0.50, is_buy=True, market_meta_fee_bps=100,
    )
    expect("fee_usd", fee, 1.0)                  # 100 bps = 1%
    assert "market_meta_bps=100" in src, src


def test_limitless_fallback_curve_buy_peak():
    section("limitless fallback: buy fee peaks at p=0.50 -> ~3.00% of notional")
    fee, src = limitless_taker_fee_usd(notional_usd=100, price=0.50, is_buy=True)
    expect("fee_usd (peak)", fee, 3.00, tol=0.01)


def test_limitless_fallback_curve_buy_edge():
    section("limitless fallback: buy fee at edge p=0.01 -> ~0.40%")
    fee, src = limitless_taker_fee_usd(notional_usd=100, price=0.01, is_buy=True)
    # distance from mid = 0.98, pct = 3.00 - (3.00 - 0.40) * 0.98 ≈ 0.4520
    expect("fee_usd (edge)", fee, 0.4520, tol=0.01)


def test_polymarket_uses_market_meta():
    section("polymarket fee: uses fd.rate if provided")
    fee, src = polymarket_taker_fee_usd(
        notional_usd=100, price=0.50, market_meta={"fd": {"rate": 0.03}},
    )
    # formula: 100 * 0.5 * 0.03 * (0.25)^1 = 0.375
    expect("fee_usd", fee, 0.375)
    assert "fd.rate=0.03" in src, src


def test_polymarket_fallback():
    section("polymarket fee: fallback to crypto category (0.072 rate)")
    fee, src = polymarket_taker_fee_usd(notional_usd=100, price=0.50)
    # 100 * 0.5 * 0.072 * 0.25 = 0.9
    expect("fee_usd", fee, 0.9, tol=0.001)


# ---------- YES+NO complementarity tests ----------

def test_yesno_no_edge_when_sum_above_one():
    section("yes+no: NO edge when realistic sum_asks > 1.00")
    yes_asks = [BookLevel(price=0.55, size=10_000)]
    no_asks = [BookLevel(price=0.55, size=10_000)]
    obs = evaluate_yes_no_complementarity(
        venue="limitless", market_key="test/no-edge",
        yes_asks=yes_asks, no_asks=no_asks, size_usd=100,
        yes_top_ask=0.55, no_top_ask=0.55,
    )
    expect("realistic_sum_avg_asks", obs.realistic_sum_avg_asks, 1.10)
    expect("net_edge_usd negative", obs.net_edge_usd < 0, True)
    expect("depth_ok", obs.depth_ok, True)


def test_yesno_clear_edge_when_sum_well_below_one():
    section("yes+no: CLEAR edge when realistic sum_asks << 1.00")
    yes_asks = [BookLevel(price=0.40, size=10_000)]
    no_asks = [BookLevel(price=0.40, size=10_000)]
    obs = evaluate_yes_no_complementarity(
        venue="limitless", market_key="test/clear-edge",
        yes_asks=yes_asks, no_asks=no_asks, size_usd=100,
        yes_top_ask=0.40, no_top_ask=0.40,
    )
    # size_usd=100 split 50/50: $50 buys 125 shares at p=0.40 on each side.
    # 125 shares pair * $1 redeem = $125; cost = 125 * 0.80 = $100; gross = $25.
    # Limitless fee at p=0.40: distance_from_mid=0.2, pct = 3.0 - 2.6*0.2 = 2.48%
    # Fee on $50 notional per side: $1.24 each, $2.48 total.
    # Net ~= $25 - $2.48 = $22.52.
    expect("realistic_sum_avg_asks", obs.realistic_sum_avg_asks, 0.80)
    expect("net_edge_usd positive", obs.net_edge_usd > 0, True)
    print(f"      [info] net_edge_usd = {obs.net_edge_usd:.4f}, fees = {obs.fees_yes_usd + obs.fees_no_usd:.4f}")


def test_yesno_depth_exhaustion_marked():
    section("yes+no: depth exhausted on one side -> depth_ok=False")
    yes_asks = [BookLevel(price=0.40, size=10)]      # only $4 capacity
    no_asks = [BookLevel(price=0.40, size=10_000)]   # deep
    obs = evaluate_yes_no_complementarity(
        venue="limitless", market_key="test/thin-yes",
        yes_asks=yes_asks, no_asks=no_asks, size_usd=100,
        yes_top_ask=0.40, no_top_ask=0.40,
    )
    expect("yes_walk depth_exhausted", obs.yes_walk.depth_exhausted, True)
    expect("depth_ok", obs.depth_ok, False)
    # But we should still compute an answer for the depth we DID get.
    expect("net_edge_usd is computed", obs.net_edge_usd is not None, True)


def test_yesno_walked_avg_differs_from_top():
    section("yes+no: walked avg price > top-of-book when book is layered (fake edge detection)")
    yes_asks = [
        BookLevel(price=0.40, size=10),       # only $4 here at 0.40
        BookLevel(price=0.50, size=10_000),   # everything else at 0.50
    ]
    no_asks = [BookLevel(price=0.40, size=10_000)]
    obs = evaluate_yes_no_complementarity(
        venue="limitless", market_key="test/layered-yes",
        yes_asks=yes_asks, no_asks=no_asks, size_usd=100,
        yes_top_ask=0.40, no_top_ask=0.40,
    )
    # Naive view: 0.40 + 0.40 = 0.80, would look like fake edge
    expect("naive_sum_top_asks", obs.naive_sum_top_asks, 0.80)
    # Realistic walked YES avg = much closer to 0.50 (since only $4 was at 0.40,
    # remaining $46 of $50 budget eaten at 0.50 -> weighted avg ~0.491)
    assert obs.realistic_sum_avg_asks > 0.85, f"expected realistic close to 0.90, got {obs.realistic_sum_avg_asks}"
    print(f"      [info] naive=0.80, realistic={obs.realistic_sum_avg_asks:.4f}  <- fake edge correctly detected")


# ---------- Cross-venue tests ----------

def test_cross_venue_skew_unreliable_flagged():
    section("cross-venue: large skew flagged as unreliable")
    a_asks = [BookLevel(price=0.40, size=10_000)]
    b_asks = [BookLevel(price=0.40, size=10_000)]
    obs = evaluate_cross_venue(
        pair_key="test/skew",
        venue_a="limitless", venue_b="polymarket",
        a_yes_asks=a_asks, b_no_asks=b_asks, size_usd=100,
        a_response_ts_ns=1_000_000_000_000,
        b_response_ts_ns=1_000_000_000_000 + 500_000_000,  # 500ms apart
    )
    expect("skew_ms", obs.skew_ms, 500.0)
    expect("skew_unreliable", obs.skew_unreliable, True)


def test_cross_venue_haircut_eats_edge():
    section("cross-venue: oracle haircut subtracts from edge (conservative)")
    a_asks = [BookLevel(price=0.40, size=10_000)]
    b_asks = [BookLevel(price=0.40, size=10_000)]
    obs = evaluate_cross_venue(
        pair_key="test/haircut",
        venue_a="limitless", venue_b="polymarket",
        a_yes_asks=a_asks, b_no_asks=b_asks, size_usd=100,
        a_response_ts_ns=1_000_000_000_000, b_response_ts_ns=1_000_000_000_000,
        oracle_haircut_pct=0.5,
    )
    expect("oracle_haircut_usd > 0", obs.oracle_haircut_usd > 0, True)
    # cost ~= $100 total round-trip; haircut = 0.5% * cost = $0.50
    print(f"      [info] haircut=${obs.oracle_haircut_usd:.4f} (0.5% conservative)")


# ---------- Run all ----------

ALL_TESTS = [
    test_walk_asks_single_level_partial,
    test_walk_asks_eats_through_two_levels,
    test_walk_asks_exhausts_book,
    test_walk_asks_empty_book,
    test_walk_asks_rejects_unsorted,
    test_walk_bids,
    test_walk_skips_zero_levels,
    test_limitless_uses_market_meta_when_provided,
    test_limitless_fallback_curve_buy_peak,
    test_limitless_fallback_curve_buy_edge,
    test_polymarket_uses_market_meta,
    test_polymarket_fallback,
    test_yesno_no_edge_when_sum_above_one,
    test_yesno_clear_edge_when_sum_well_below_one,
    test_yesno_depth_exhaustion_marked,
    test_yesno_walked_avg_differs_from_top,
    test_cross_venue_skew_unreliable_flagged,
    test_cross_venue_haircut_eats_edge,
]


def run() -> int:
    passed = 0
    failed = 0
    for t in ALL_TESTS:
        try:
            t()
            passed += 1
        except FailedTest as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n----")
    print(f"  passed: {passed}")
    print(f"  failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
