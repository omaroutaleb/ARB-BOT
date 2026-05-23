from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from eth_account import Account
from eth_account.messages import encode_typed_data


Side = Literal["BUY", "SELL"]
OrderType = Literal["GTC", "FAK", "FOK"]


@dataclass(frozen=True)
class LimitlessOrder:
    salt: int
    maker: str
    signer: str
    taker: str
    tokenId: int
    makerAmount: int
    takerAmount: int
    expiration: int
    nonce: int
    feeRateBps: int
    side: int
    signatureType: int


def round_limitless_price(price: float) -> float:
    value = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(max(Decimal("0.01"), min(Decimal("0.99"), value)))


def build_order_amounts(price: float, size: float, side: Side) -> tuple[int, int]:
    shares = Decimal(str(size))
    p = Decimal(str(price))
    scale = Decimal("1000000")
    if side == "BUY":
        maker_amount = int((shares * p * scale).to_integral_value(rounding=ROUND_HALF_UP))
        taker_amount = int((shares * scale).to_integral_value(rounding=ROUND_HALF_UP))
    else:
        maker_amount = int((shares * scale).to_integral_value(rounding=ROUND_HALF_UP))
        taker_amount = int((shares * p * scale).to_integral_value(rounding=ROUND_HALF_UP))
    return maker_amount, taker_amount


def create_limitless_order(
    *,
    maker: str,
    signer: str,
    token_id: int,
    price: float,
    size: float,
    side: Side,
    fee_rate_bps: int,
    signature_type: int = 0,
    taker: str = "0x0000000000000000000000000000000000000000",
    expiration: int = 0,
    nonce: int = 0,
    salt: int | None = None,
) -> LimitlessOrder:
    price = round_limitless_price(price)
    maker_amount, taker_amount = build_order_amounts(price, size, side)
    return LimitlessOrder(
        salt=salt if salt is not None else secrets.randbits(256),
        maker=maker,
        signer=signer,
        taker=taker,
        tokenId=int(token_id),
        makerAmount=maker_amount,
        takerAmount=taker_amount,
        expiration=expiration,
        nonce=nonce,
        feeRateBps=fee_rate_bps,
        side=0 if side == "BUY" else 1,
        signatureType=signature_type,
    )


def limitless_typed_data(
    order: LimitlessOrder,
    *,
    verifying_contract: str,
    chain_id: int = 8453,
) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": _order_type_fields(),
        },
        "primaryType": "Order",
        "domain": {
            "name": "Limitless CTF Exchange",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": verifying_contract,
        },
        "message": asdict(order),
    }


def sign_limitless_order(
    private_key: str,
    order: LimitlessOrder,
    *,
    verifying_contract: str,
    chain_id: int = 8453,
) -> str:
    typed = limitless_typed_data(
        order,
        verifying_contract=verifying_contract,
        chain_id=chain_id,
    )
    signable = encode_typed_data(full_message=typed)
    signed = Account.sign_message(signable, private_key=private_key)
    return signed.signature.hex()


def _order_type_fields() -> list[dict[str, str]]:
    return [
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

