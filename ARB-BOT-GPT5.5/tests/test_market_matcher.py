from src.matching.market_matcher import evaluate_pair, find_pairs, normalize_market


def test_normalize_extracts_core_fields() -> None:
    market = normalize_market(
        "limitless",
        {
            "slug": "btc-above-100k",
            "title": "Will BTC be above $100,000 on May 23?",
            "deadline": "2026-05-23T23:59:00Z",
            "oracle": "Pyth BTC/USD",
            "positionIds": ["yes-token", "no-token"],
            "tickSize": "0.01",
        },
    )
    assert market.asset == "BTC"
    assert market.direction == "above"
    assert market.strike_usd == 100000
    assert market.yes_token_id == "yes-token"


def test_delta_from_open_does_not_match_fixed_strike() -> None:
    poly = normalize_market(
        "polymarket",
        {"slug": "btc-updown", "title": "BTC Up or Down 5m", "oracle": "Chainlink BTC/USD", "deadline": "2026-05-23T10:00:00Z"},
    )
    lim = normalize_market(
        "limitless",
        {"slug": "btc-above", "title": "BTC above $100,000", "oracle": "Pyth BTC/USD", "deadline": "2026-05-23T10:00:00Z"},
    )
    assert evaluate_pair(poly, lim) is None


def test_chainlink_pyth_pair_is_relative_value_not_hard_arb() -> None:
    poly = normalize_market(
        "polymarket",
        {"slug": "p", "title": "BTC above $100,000", "oracle": "Chainlink BTC/USD", "deadline": "2026-05-23T10:00:00Z"},
    )
    lim = normalize_market(
        "limitless",
        {"slug": "l", "title": "BTC above $100,000", "oracle": "Pyth BTC/USD", "deadline": "2026-05-23T10:00:00Z"},
    )
    pair = evaluate_pair(poly, lim)
    assert pair is not None
    assert pair.hard_arb is False
    assert pair.haircut_bps == 50


def test_find_pairs_requires_strike_tolerance() -> None:
    poly = normalize_market(
        "polymarket",
        {"slug": "p", "title": "BTC above $100,000", "oracle": "Chainlink", "deadline": "2026-05-23T10:00:00Z"},
    )
    lim = normalize_market(
        "limitless",
        {"slug": "l", "title": "BTC above $105,000", "oracle": "Chainlink", "deadline": "2026-05-23T10:00:00Z"},
    )
    assert find_pairs([poly], [lim]) == []

