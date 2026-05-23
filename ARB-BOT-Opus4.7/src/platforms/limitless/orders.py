"""Limitless EIP-712 order signing against per-market `venue.exchange`.

STRATEGY_SYNTHESIS.md §1.2 Limitless block:
    Domain {name:"Limitless CTF Exchange", version:"1", chainId:8453,
            verifyingContract: venue.exchange (per-market)}
    Order fields: salt, maker, signer, taker, tokenId, makerAmount, takerAmount,
                  expiration, nonce, feeRateBps, side (0=BUY,1=SELL), signatureType (0=EOA)
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from src.config import get_settings
from src.observability.logging import get_logger

log = get_logger(__name__)


class Side(IntEnum):
    BUY = 0
    SELL = 1


class OrderType:
    GTC = "GTC"
    FAK = "FAK"
    FOK = "FOK"


@dataclass(slots=True)
class LimitlessOrderArgs:
    market_slug: str
    token_id: str               # YES or NO position id
    price: float                # 0.01–0.99 in 0.01 steps
    size: float                 # shares
    side: Side
    fee_rate_bps: int
    exchange_address: str       # market.venue.exchange
    order_type: str = OrderType.GTC
    post_only: bool = False
    expiration: int = 0


def _round_to_cent(p: float) -> float:
    return round(round(p * 100) / 100.0, 2)


def _to_units(qty: float, decimals: int = 6) -> int:
    return int(Decimal(str(qty)).scaleb(decimals))


def build_unsigned_order(
    args: LimitlessOrderArgs,
    *,
    maker: str,
    signer: str,
) -> dict[str, Any]:
    price = _round_to_cent(args.price)
    if not (0.01 <= price <= 0.99):
        raise ValueError(f"Limitless requires 0.01 ≤ price ≤ 0.99, got {price}")
    if args.size <= 0:
        raise ValueError(f"size must be > 0, got {args.size}")

    if args.side == Side.BUY:
        maker_amount = _to_units(args.size * price)
        taker_amount = _to_units(args.size)
    else:
        maker_amount = _to_units(args.size)
        taker_amount = _to_units(args.size * price)

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
        "signatureType": 0,
    }


def sign_order(
    unsigned: dict[str, Any],
    *,
    private_key: str,
    exchange_address: str,
    chain_id: int,
) -> dict[str, Any]:
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
        "name": "Limitless CTF Exchange",
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": exchange_address,
    }
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
    return {**unsigned, "signature": sig_hex}


def build_signed_payload(
    args: LimitlessOrderArgs,
    *,
    owner_id: str,
    client_order_id: str | None = None,
    on_behalf_of: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.LIMITLESS_PRIVATE_KEY is None or settings.LIMITLESS_WALLET_ADDRESS is None:
        raise RuntimeError("LIMITLESS_PRIVATE_KEY and LIMITLESS_WALLET_ADDRESS required")
    priv = settings.LIMITLESS_PRIVATE_KEY.get_secret_value()
    wallet = settings.LIMITLESS_WALLET_ADDRESS

    unsigned = build_unsigned_order(args, maker=wallet, signer=wallet)
    signed = sign_order(
        unsigned,
        private_key=priv,
        exchange_address=args.exchange_address,
        chain_id=settings.LIMITLESS_CHAIN_ID,
    )

    payload: dict[str, Any] = {
        "ownerId": owner_id,
        "orderType": args.order_type,
        "marketSlug": args.market_slug,
        "order": signed,
        "postOnly": args.post_only,
    }
    if client_order_id:
        payload["clientOrderId"] = client_order_id
    if on_behalf_of:
        payload["onBehalfOf"] = on_behalf_of
    return payload
