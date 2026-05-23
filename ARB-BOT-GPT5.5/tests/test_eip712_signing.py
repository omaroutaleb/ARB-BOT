from eth_account import Account
from eth_account.messages import encode_typed_data

from src.platforms.limitless.orders import create_limitless_order, limitless_typed_data, sign_limitless_order
from src.platforms.polymarket.orders import create_polymarket_order, polymarket_typed_data, sign_polymarket_order


PRIVATE_KEY = "0x59c6995e998f97a5a0044966f0945380dfd19e86dae6a9e39053ec9f80bf64a5"
ADDRESS = Account.from_key(PRIVATE_KEY).address
VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000001"


def test_limitless_order_signature_recovers_reference_address() -> None:
    order = create_limitless_order(
        maker=ADDRESS,
        signer=ADDRESS,
        token_id=123,
        price=0.5,
        size=10,
        side="BUY",
        fee_rate_bps=20,
        salt=1,
    )
    typed = limitless_typed_data(order, verifying_contract=VERIFYING_CONTRACT)
    signature = sign_limitless_order(PRIVATE_KEY, order, verifying_contract=VERIFYING_CONTRACT)
    recovered = Account.recover_message(encode_typed_data(full_message=typed), signature=signature)
    assert recovered == ADDRESS
    assert typed["domain"]["name"] == "Limitless CTF Exchange"
    assert typed["domain"]["chainId"] == 8453


def test_polymarket_order_signature_recovers_reference_address() -> None:
    order = create_polymarket_order(
        maker=ADDRESS,
        signer=ADDRESS,
        token_id=456,
        price=0.4,
        size=10,
        side="SELL",
        fee_rate_bps=20,
        salt=2,
    )
    typed = polymarket_typed_data(order, verifying_contract=VERIFYING_CONTRACT)
    signature = sign_polymarket_order(PRIVATE_KEY, order, verifying_contract=VERIFYING_CONTRACT)
    recovered = Account.recover_message(encode_typed_data(full_message=typed), signature=signature)
    assert recovered == ADDRESS
    assert typed["domain"]["chainId"] == 137

