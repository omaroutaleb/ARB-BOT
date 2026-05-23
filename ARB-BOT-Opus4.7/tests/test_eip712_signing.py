"""EIP-712 order-signing tests for both venues.

Validates:
  - Polymarket: domain {name:"Polymarket CTF Exchange", v=1, chainId=137}
  - Limitless:  domain {name:"Limitless CTF Exchange", v=1, chainId=8453}
  - Same private key + same logical Order ⇒ deterministic signature
  - Tick rounding for Polymarket
  - Cent rounding for Limitless
"""

import pytest
from eth_account import Account

from src.platforms.limitless.orders import (
    LimitlessOrderArgs,
    OrderType as LimOT,
    Side as LimSide,
    build_unsigned_order as lim_build,
    sign_order as lim_sign,
)
from src.platforms.polymarket.orders import (
    OrderArgs,
    OrderType,
    Side,
    _round_to_tick,
    build_unsigned_order,
    sign_order,
)
from src.config import PolymarketSignatureType


TEST_PRIVKEY = "0x" + "11" * 32           # deterministic test key — NEVER funded
TEST_PUBADDR = Account.from_key(TEST_PRIVKEY).address
EXCHANGE_POLY = "0x" + "ab" * 20
EXCHANGE_LIM = "0x" + "cd" * 20


class TestPolymarketSigning:
    def test_signature_is_65_bytes_hex(self):
        unsigned = build_unsigned_order(
            OrderArgs(token_id="1", price=0.50, size=10, side=Side.BUY,
                      fee_rate_bps=72, exchange_address=EXCHANGE_POLY),
            maker=TEST_PUBADDR, signer=TEST_PUBADDR,
            signature_type=PolymarketSignatureType.EOA, tick_size=0.01,
        )
        signed = sign_order(unsigned, private_key=TEST_PRIVKEY,
                            exchange_address=EXCHANGE_POLY, chain_id=137)
        sig = signed["signature"]
        assert sig.startswith("0x")
        # 65 bytes = 130 hex chars + "0x"
        assert len(sig) == 132

    def test_signature_deterministic_for_same_salt(self):
        unsigned = build_unsigned_order(
            OrderArgs(token_id="1", price=0.50, size=10, side=Side.BUY,
                      fee_rate_bps=72, exchange_address=EXCHANGE_POLY),
            maker=TEST_PUBADDR, signer=TEST_PUBADDR,
            signature_type=PolymarketSignatureType.EOA, tick_size=0.01,
        )
        s1 = sign_order(unsigned, private_key=TEST_PRIVKEY,
                        exchange_address=EXCHANGE_POLY, chain_id=137)
        s2 = sign_order(unsigned, private_key=TEST_PRIVKEY,
                        exchange_address=EXCHANGE_POLY, chain_id=137)
        assert s1["signature"] == s2["signature"]

    def test_tick_size_rounding(self):
        # Polymarket: prices must be tick-aligned
        assert _round_to_tick(0.5234, 0.01) == 0.52
        assert _round_to_tick(0.5249, 0.01) == 0.52
        assert _round_to_tick(0.5260, 0.01) == 0.53      # clearly > halfway
        assert _round_to_tick(0.5234, 0.001) == 0.523
        assert _round_to_tick(0.5234, 0.0001) == 0.5234

    def test_price_outside_open_interval_rejected(self):
        with pytest.raises(ValueError):
            build_unsigned_order(
                OrderArgs(token_id="1", price=1.0, size=10, side=Side.BUY,
                          fee_rate_bps=72, exchange_address=EXCHANGE_POLY),
                maker=TEST_PUBADDR, signer=TEST_PUBADDR,
                signature_type=PolymarketSignatureType.EOA, tick_size=0.01,
            )
        with pytest.raises(ValueError):
            build_unsigned_order(
                OrderArgs(token_id="1", price=0.0, size=10, side=Side.BUY,
                          fee_rate_bps=72, exchange_address=EXCHANGE_POLY),
                maker=TEST_PUBADDR, signer=TEST_PUBADDR,
                signature_type=PolymarketSignatureType.EOA, tick_size=0.01,
            )

    def test_zero_size_rejected(self):
        with pytest.raises(ValueError):
            build_unsigned_order(
                OrderArgs(token_id="1", price=0.5, size=0, side=Side.BUY,
                          fee_rate_bps=72, exchange_address=EXCHANGE_POLY),
                maker=TEST_PUBADDR, signer=TEST_PUBADDR,
                signature_type=PolymarketSignatureType.EOA, tick_size=0.01,
            )

    def test_buy_maker_amount_is_collateral(self):
        # BUY: maker pays collateral (=size * price), receives shares (=size).
        unsigned = build_unsigned_order(
            OrderArgs(token_id="1", price=0.50, size=10, side=Side.BUY,
                      fee_rate_bps=72, exchange_address=EXCHANGE_POLY),
            maker=TEST_PUBADDR, signer=TEST_PUBADDR,
            signature_type=PolymarketSignatureType.EOA, tick_size=0.01,
        )
        # 10 shares × $0.50 = $5.00 = 5_000_000 base units (USDC 6dp)
        assert int(unsigned["makerAmount"]) == 5_000_000
        assert int(unsigned["takerAmount"]) == 10_000_000

    def test_sell_inverts_amounts(self):
        unsigned = build_unsigned_order(
            OrderArgs(token_id="1", price=0.50, size=10, side=Side.SELL,
                      fee_rate_bps=72, exchange_address=EXCHANGE_POLY),
            maker=TEST_PUBADDR, signer=TEST_PUBADDR,
            signature_type=PolymarketSignatureType.EOA, tick_size=0.01,
        )
        assert int(unsigned["makerAmount"]) == 10_000_000     # selling 10 shares
        assert int(unsigned["takerAmount"]) == 5_000_000      # for $5 USDC


class TestLimitlessSigning:
    def test_signature_is_65_bytes_hex(self):
        unsigned = lim_build(
            LimitlessOrderArgs(market_slug="x", token_id="1", price=0.50,
                               size=10, side=LimSide.BUY, fee_rate_bps=100,
                               exchange_address=EXCHANGE_LIM,
                               order_type=LimOT.GTC),
            maker=TEST_PUBADDR, signer=TEST_PUBADDR,
        )
        signed = lim_sign(unsigned, private_key=TEST_PRIVKEY,
                          exchange_address=EXCHANGE_LIM, chain_id=8453)
        sig = signed["signature"]
        assert sig.startswith("0x")
        assert len(sig) == 132

    def test_polymarket_and_limitless_sigs_differ_for_same_order(self):
        """Different domain (name + chainId) → different sig."""
        # Same logical order, different domains:
        unsigned = lim_build(
            LimitlessOrderArgs(market_slug="x", token_id="1", price=0.50,
                               size=10, side=LimSide.BUY, fee_rate_bps=100,
                               exchange_address=EXCHANGE_LIM,
                               order_type=LimOT.GTC),
            maker=TEST_PUBADDR, signer=TEST_PUBADDR,
        )
        sig_poly = sign_order(unsigned, private_key=TEST_PRIVKEY,
                              exchange_address=EXCHANGE_POLY, chain_id=137)
        sig_lim = lim_sign(unsigned, private_key=TEST_PRIVKEY,
                           exchange_address=EXCHANGE_LIM, chain_id=8453)
        assert sig_poly["signature"] != sig_lim["signature"]

    def test_below_cent_floor_rejected(self):
        with pytest.raises(ValueError):
            lim_build(
                LimitlessOrderArgs(market_slug="x", token_id="1", price=0.005,
                                   size=10, side=LimSide.BUY, fee_rate_bps=100,
                                   exchange_address=EXCHANGE_LIM),
                maker=TEST_PUBADDR, signer=TEST_PUBADDR,
            )

    def test_above_99c_ceiling_rejected(self):
        with pytest.raises(ValueError):
            lim_build(
                LimitlessOrderArgs(market_slug="x", token_id="1", price=0.995,
                                   size=10, side=LimSide.BUY, fee_rate_bps=100,
                                   exchange_address=EXCHANGE_LIM),
                maker=TEST_PUBADDR, signer=TEST_PUBADDR,
            )

    def test_cent_rounding(self):
        unsigned = lim_build(
            LimitlessOrderArgs(market_slug="x", token_id="1", price=0.5249,
                               size=10, side=LimSide.BUY, fee_rate_bps=100,
                               exchange_address=EXCHANGE_LIM),
            maker=TEST_PUBADDR, signer=TEST_PUBADDR,
        )
        # Rounded to $0.52 → maker amount 0.52 × 10 = 5.2 USDC = 5_200_000
        assert int(unsigned["makerAmount"]) == 5_200_000
