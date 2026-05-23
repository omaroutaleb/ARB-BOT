from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


StrategyPhase = Literal[1, 2, 3]


class Settings(BaseSettings):
    """Environment-backed settings shared by every bot component."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    dry_run: bool = True
    strategy_phase: StrategyPhase = 1

    @field_validator("strategy_phase", mode="before")
    @classmethod
    def _coerce_strategy_phase(cls, v):
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return v
    enable_cross_venue: bool = False
    log_level: str = "INFO"
    data_dir: Path = Path("./data")
    trade_journal_path: Path = Path("./Trade.json")
    database_path: Path = Path("./data/bot.sqlite3")
    metrics_port: int = 9090

    bankroll_usd: float = 500.0
    max_position_usd: float = 40.0
    max_concurrent_arbs: int = 3
    max_single_platform_exposure_usd: float = 300.0
    reserve_cash_usd: float = 50.0
    daily_loss_stop_usd: float = -50.0
    total_drawdown_stop_usd: float = -150.0
    oracle_mismatch_haircut_bps: int = 50

    phase1_trade_size_usd: float = 20.0
    phase2_maker_min_usd: float = 5.0
    phase2_maker_max_usd: float = 10.0
    phase3_cross_venue_min_usd: float = 20.0
    phase3_cross_venue_max_usd: float = 30.0
    edge_daily_bps: int = 200
    edge_1h_bps: int = Field(default=250, alias="EDGE_1H_BPS")
    edge_30m_bps: int = Field(default=300, alias="EDGE_30M_BPS")
    limitless_complement_ask_max: float = 0.985

    polymarket_private_key: SecretStr | None = None
    polymarket_address: str | None = None
    polymarket_api_key: SecretStr | None = None
    polymarket_api_secret: SecretStr | None = None
    polymarket_api_passphrase: SecretStr | None = None
    polymarket_signature_type: int = 0
    polymarket_funder: str | None = None
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_data_url: str = "https://data-api.polymarket.com"
    polymarket_bridge_url: str = "https://bridge.polymarket.com"
    polymarket_geoblock_url: str = "https://polymarket.com/api/geoblock"
    polymarket_market_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    polymarket_user_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    polymarket_heartbeat_seconds: float = 5.0

    limitless_api_key: SecretStr | None = None
    limitless_private_key: SecretStr | None = None
    limitless_address: str | None = None
    limitless_api_url: str = "https://api.limitless.exchange"
    limitless_ws_url: str = "wss://ws.limitless.exchange"
    limitless_request_min_delay_ms: int = 300
    limitless_max_concurrent_requests: int = 2

    validate_endpoints_on_start: bool = True
    polymarket_llms_url: str = "https://docs.polymarket.com/llms.txt"
    limitless_llms_url: str = "https://docs.limitless.exchange/llms.txt"

    @field_validator("data_dir", "database_path", "trade_journal_path", mode="before")
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def live_trading(self) -> bool:
        return not self.dry_run

    @property
    def polymarket_auth_ready(self) -> bool:
        return all(
            [
                self.polymarket_address,
                self.polymarket_api_key,
                self.polymarket_api_secret,
                self.polymarket_api_passphrase,
            ]
        )

    @property
    def limitless_auth_ready(self) -> bool:
        return bool(self.limitless_api_key)

    def ensure_runtime_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.trade_journal_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings


POLYMARKET_ENDPOINTS = {
    "gamma_markets": "https://gamma-api.polymarket.com/markets",
    "gamma_events": "https://gamma-api.polymarket.com/events",
    "clob_book": "https://clob.polymarket.com/book",
    "clob_midpoint": "https://clob.polymarket.com/midpoint",
    "clob_price": "https://clob.polymarket.com/price",
    "clob_spread": "https://clob.polymarket.com/spread",
    "clob_prices_history": "https://clob.polymarket.com/prices-history",
    "clob_tick_size": "https://clob.polymarket.com/tick-size/{token_id}",
    "clob_order": "https://clob.polymarket.com/order",
    "clob_orders": "https://clob.polymarket.com/orders",
    "clob_cancel_all": "https://clob.polymarket.com/cancel-all",
    "clob_cancel_market_orders": "https://clob.polymarket.com/cancel-market-orders",
    "clob_heartbeat": "https://clob.polymarket.com/heartbeat",
    "market_ws": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "user_ws": "wss://ws-subscriptions-clob.polymarket.com/ws/user",
}

LIMITLESS_ENDPOINTS = {
    "active_markets": "https://api.limitless.exchange/markets/active",
    "active_slugs": "https://api.limitless.exchange/markets/active/slugs",
    "market_detail": "https://api.limitless.exchange/markets/{slug}",
    "market_search": "https://api.limitless.exchange/markets/search",
    "market_orderbook": "https://api.limitless.exchange/markets/{slug}/orderbook",
    "historical_price": "https://api.limitless.exchange/markets/{slug}/historical-price",
    "oracle_candles": "https://api.limitless.exchange/markets/{slug}/oracle-candles",
    "market_events": "https://api.limitless.exchange/markets/{slug}/events",
    "orders": "https://api.limitless.exchange/orders",
    "cancel_order": "https://api.limitless.exchange/orders/{orderId}",
    "cancel_batch": "https://api.limitless.exchange/orders/cancel-batch",
    "cancel_all_market": "https://api.limitless.exchange/orders/all/{slug}",
    "order_status_batch": "https://api.limitless.exchange/orders/status/batch",
    "profile": "https://api.limitless.exchange/portfolio/profile",
    "positions": "https://api.limitless.exchange/portfolio/positions",
    "trades": "https://api.limitless.exchange/portfolio/trades",
    "allowance": "https://api.limitless.exchange/portfolio/allowance",
    "redeem": "https://api.limitless.exchange/portfolio/redeem",
    "withdraw": "https://api.limitless.exchange/portfolio/withdraw",
    "ws": "wss://ws.limitless.exchange",
}
