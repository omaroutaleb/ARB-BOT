"""Centralized typed configuration. Every other module imports from here.

All defaults trace to STRATEGY_SYNTHESIS.md §1.9, §1.12, §1.8.
Secrets must come from environment; never hard-code.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrategyPhase(IntEnum):
    PHASE_1_SINGLE_VENUE = 1
    PHASE_2_PLUS_MAKER = 2
    PHASE_3_CROSS_VENUE = 3


class PolymarketSignatureType(IntEnum):
    EOA = 0
    POLY_PROXY = 1
    POLY_GNOSIS_SAFE = 2
    POLY_1271 = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ----- General -----
    LOG_LEVEL: str = "INFO"
    DRY_RUN: bool = True
    STRATEGY_PHASE: StrategyPhase = StrategyPhase.PHASE_1_SINGLE_VENUE
    METRICS_PORT: int = 9090
    STATE_FILE: Path = Path("/app/state/Trade.json")

    # ----- Bankroll & risk -----
    BANKROLL_USD: float = 500.0
    MAX_POSITION_USD: float = 40.0
    MAX_CONCURRENT_ARBS: int = 3
    MAX_PLATFORM_EXPOSURE_USD: float = 300.0
    MIN_PLATFORM_RESERVE_USD: float = 50.0
    MIN_EDGE_DAILY_PCT: float = 2.0
    MIN_EDGE_1H_PCT: float = 2.5
    MIN_EDGE_30M_PCT: float = 3.0
    DAILY_LOSS_STOP_USD: float = 50.0
    TOTAL_DRAWDOWN_STOP_USD: float = 150.0

    PHASE2_MIN_CLOSED_ARBS: int = 10
    PHASE2_MIN_WIN_RATE: float = 0.80
    PHASE3_MIN_MAKER_FILLS: int = 30

    # ----- Polymarket -----
    POLY_PRIVATE_KEY: SecretStr | None = None
    POLY_WALLET_ADDRESS: str | None = None
    POLY_SIGNATURE_TYPE: PolymarketSignatureType = PolymarketSignatureType.EOA
    POLY_CHAIN_ID: int = 137
    POLY_FUNDER_ADDRESS: str | None = None

    POLY_GAMMA_URL: str = "https://gamma-api.polymarket.com"
    POLY_CLOB_URL: str = "https://clob.polymarket.com"
    POLY_DATA_URL: str = "https://data-api.polymarket.com"
    POLY_BRIDGE_URL: str = "https://bridge.polymarket.com"
    POLY_WS_MARKET: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    POLY_WS_USER: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    POLY_WS_RTDS: str = "wss://ws-live-data.polymarket.com"
    POLY_GEOBLOCK_URL: str = "https://polymarket.com/api/geoblock"

    POLY_HEARTBEAT_INTERVAL_SEC: int = 5

    # ----- Limitless -----
    LIMITLESS_API_KEY: SecretStr | None = None
    LIMITLESS_PRIVATE_KEY: SecretStr | None = None
    LIMITLESS_WALLET_ADDRESS: str | None = None
    LIMITLESS_CHAIN_ID: int = 8453
    LIMITLESS_USDC_ADDRESS: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    LIMITLESS_REST_URL: str = "https://api.limitless.exchange"
    LIMITLESS_WS_URL: str = "wss://ws.limitless.exchange"

    LIMITLESS_MAX_CONCURRENT: int = 2
    LIMITLESS_MIN_INTERVAL_MS: int = 300

    # ----- Docs validation -----
    POLY_DOCS_LLMS: str = "https://docs.polymarket.com/llms.txt"
    LIMITLESS_DOCS_LLMS: str = "https://docs.limitless.exchange/llms.txt"

    # ----- Phase 1 -----
    PHASE1_EDGE_THRESHOLD: float = 0.985
    PHASE1_MAX_POSITION_USD: float = 20.0
    PHASE1_MIN_24H_VOLUME_USD: float = 1000.0
    PHASE1_ALLOWED_DURATIONS: str = "daily,hourly"

    # ----- Phase 2 -----
    PHASE2_MARKETS: str = "BTC"
    PHASE2_DURATIONS: str = "15m"
    PHASE2_QUOTE_SIZE_USD: float = 10.0
    PHASE2_TICK_INSIDE: int = 1

    # ----- Phase 3 -----
    PHASE3_MIN_EDGE_PCT: float = 3.0
    PHASE3_ORACLE_HAIRCUT_PCT: float = 0.5
    PHASE3_STRIKE_TOLERANCE_PCT: float = 0.5
    PHASE3_EXPIRY_TOLERANCE_SEC: int = 3600
    PHASE3_MAX_CONCURRENT: int = 1
    PHASE3_POSITION_USD: float = 25.0

    # ----- Validators -----
    @field_validator("LOG_LEVEL")
    @classmethod
    def _valid_log_level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid LOG_LEVEL: {v}")
        return v

    @field_validator("PHASE1_ALLOWED_DURATIONS", "PHASE2_MARKETS", "PHASE2_DURATIONS")
    @classmethod
    def _csv_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("comma-separated list cannot be empty")
        return v

    def phase1_durations(self) -> list[str]:
        return [s.strip() for s in self.PHASE1_ALLOWED_DURATIONS.split(",") if s.strip()]

    def phase2_markets(self) -> list[str]:
        return [s.strip() for s in self.PHASE2_MARKETS.split(",") if s.strip()]

    def phase2_durations(self) -> list[str]:
        return [s.strip() for s in self.PHASE2_DURATIONS.split(",") if s.strip()]

    def require_polymarket_creds(self) -> None:
        if self.DRY_RUN:
            return
        if not self.POLY_PRIVATE_KEY or not self.POLY_PRIVATE_KEY.get_secret_value():
            raise RuntimeError("POLY_PRIVATE_KEY required when DRY_RUN=false")
        if not self.POLY_WALLET_ADDRESS:
            raise RuntimeError("POLY_WALLET_ADDRESS required when DRY_RUN=false")

    def require_limitless_creds(self) -> None:
        if self.DRY_RUN:
            return
        if not self.LIMITLESS_API_KEY or not self.LIMITLESS_API_KEY.get_secret_value():
            raise RuntimeError("LIMITLESS_API_KEY required when DRY_RUN=false")
        if not self.LIMITLESS_PRIVATE_KEY or not self.LIMITLESS_PRIVATE_KEY.get_secret_value():
            raise RuntimeError("LIMITLESS_PRIVATE_KEY required when DRY_RUN=false")
        if not self.LIMITLESS_WALLET_ADDRESS:
            raise RuntimeError("LIMITLESS_WALLET_ADDRESS required when DRY_RUN=false")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    global _settings
    _settings = Settings()
    return _settings
