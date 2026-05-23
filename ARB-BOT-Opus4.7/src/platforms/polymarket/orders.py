"""Polymarket EIP-712 order signing against CTF Exchange V2 on Polygon.

STRATEGY_SYNTHESIS.md §1.2 step 4. Domain:
    name = "Polymarket CTF Exchange"
    version = "1"
    chainId = 137
    verifyingContract = <CTF Exchange V2 address, fetched at runtime>

Order shape (matches the official py-clob-client v2 types):
    salt, maker, signer, taker, tokenId, makerAmount, takerAmount,
    expiration, nonce, feeRateBps, side (0=BUY,1=SELL),
    signatureType (0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE, 3=POLY_1271)
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from src.config import PolymarketSignatureType, get_settings
from src.observability.logging import get_logger
from src.platforms.polymarket.client import PolymarketClient

log = get_logger(__name__)


class Side(IntEnum):
    BUY = 0
    SELL = 1


class OrderType:
    GTC = "GTC"
    GTD = "GTD"
    FOK = "FOK"
    FAK = "FAK"


@dataclass(slots=True)
class OrderArgs:
    token_id: str               # CLOB token id (decimal string)
    price: float                # 0 < p < 1, tick-aligned
    size: float                 # in shares
    side: Side
    fee_rate_bps: int           # pulled from market metadata
    exchange_address: str       # CTF Exchange V2 address, fetched at runtime
    order_type: str = OrderType.GTC
    post_only: bool = True
    expiration: int = 0         # 0 = GTC; unix timestamp for GTD


def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 6)


def _shares_to_base_units(shares: float, decimals: int = 6) -> int:
    """USDC = 6 decimals. CTF shares mirror collateral decimals."""
    return int(Decimal(str(shares)).scaleb(decimals))


def build_unsigned_order(
    args: OrderArgs,
    *,
    maker: str,
    signer: str,
    signature_type: PolymarketSignatureType,
    tick_size: float,
) -> dict[str, Any]:
    """Compute the order dict that EIP-712 will sign. All amounts in base units (6 dp)."""
    price = _round_to_tick(args.price, tick_size)
    if not (0 < price < 1):
        raise ValueError(f"price {price} out of (0,1)")
    if args.size <= 0:
        raise ValueError(f"size must be > 0, got {args.size}")

    if args.side == Side.BUY:
        maker_amount = _shares_to_base_units(args.size * price)
        taker_amount = _shares_to_base_units(args.size)
    else:
        maker_amount = _shares_to_base_units(args.size)
        taker_amount = _shares_to_base_units(args.size * price)

    salt = secrets.randbits(64)

    return {
        "salt": str(salt),
        "maker": maker,
        "signer": signer,
        "taker": "0x0000000000000000000000000000000000000000",
        "tokenId": args.token_id,
        "makerAmount": str(maker_amount),
        "takerAmount": str(taker_amount),
        "expiration": str(args.expiration),
        "nonce": "0",
        "feeRateBps": str(args.fee_rate_bps),
        "side": int(args.side),
        "signatureType": int(signature_type),
    }


def sign_order(unsigned: dict[str, Any], *, private_key: str, exchange_address: str, chain_id: int) -> dict[str, Any]:
    """Attach the EIP-712 signature; return the full submittable payload."""
    types = {
        "Order": [
            {"name": "salt", "type": "uint256"},
            {"name": "maker", "type": "address"},
            {"name": "signer", "type": "address"},
            {"name": "taker", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
            {"name": "makerAmount", "type": "uint256"},
            {"name": "takerAmount", "type": "uint256"},
            {"name": "expiration", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "feeRateBps", "type": "uint256"},
            {"name": "side", "type": "uint8"},
            {"name": "signatureType", "type": "uint8"},
        ]
    }
    domain = {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": exchange_address,
    }
    # web3 expects uint values as ints, not strings, for typed-data encoding
    message = {
        "salt": int(unsigned["salt"]),
        "maker": unsigned["maker"],
        "signer": unsigned["signer"],
        "taker": unsigned["taker"],
        "tokenId": int(unsigned["tokenId"]),
        "makerAmount": int(unsigned["makerAmount"]),
        "takerAmount": int(unsigned["takerAmount"]),
        "expiration": int(unsigned["expiration"]),
        "nonce": int(unsigned["nonce"]),
        "feeRateBps": int(unsigned["feeRateBps"]),
        "side": int(unsigned["side"]),
        "signatureType": int(unsigned["signatureType"]),
    }
    encoded = encode_typed_data(domain_data=domain, message_types=types, message_data=message)
    signed = Account.from_key(private_key).sign_message(encoded)

    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex
    return {
        **unsigned,
        "signature": sig_hex,
    }


async def build_and_sign(
    client: PolymarketClient,
    args: OrderArgs,
) -> dict[str, Any]:
    """End-to-end: fetch tick size, build unsigned, sign, return submittable order
    wrapped with the required `orderType` field for POST /order."""
    settings = get_settings()
    if settings.POLY_PRIVATE_KEY is None:
        raise RuntimeError("POLY_PRIVATE_KEY not set")
    priv = settings.POLY_PRIVATE_KEY.get_secret_value()

    tick = await client.tick_size(args.token_id)
    sig_type = client.signature_type()
    funder = client.funder_address()
    signer_addr = settings.POLY_WALLET_ADDRESS
    if signer_addr is None:
        raise RuntimeError("POLY_WALLET_ADDRESS not set")

    unsigned = build_unsigned_order(
        args,
        maker=funder,           # the wallet holding collateral
        signer=signer_addr,     # the wallet producing the signature
        signature_type=sig_type,
        tick_size=tick,
    )
    signed = sign_order(
        unsigned,
        private_key=priv,
        exchange_address=args.exchange_address,
        chain_id=settings.POLY_CHAIN_ID,
    )

    return {
        "order": signed,
        "owner": funder,
        "orderType": args.order_type,
    }
