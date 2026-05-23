"""Shared fixtures. Forces DRY_RUN before settings is imported."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("STRATEGY_PHASE", "1")
os.environ.setdefault("BANKROLL_USD", "500")
os.environ.setdefault("STATE_FILE", str(Path(tempfile.gettempdir()) / "arbbot_test_Trade.json"))

import pytest
import pytest_asyncio

from src.config import reload_settings
from src.state.positions import TradeStore


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "Trade.json"))
    monkeypatch.setenv("BANKROLL_USD", "500")
    monkeypatch.setenv("MAX_POSITION_USD", "40")
    monkeypatch.setenv("MAX_CONCURRENT_ARBS", "3")
    monkeypatch.setenv("MAX_PLATFORM_EXPOSURE_USD", "300")
    monkeypatch.setenv("MIN_PLATFORM_RESERVE_USD", "50")
    monkeypatch.setenv("DAILY_LOSS_STOP_USD", "50")
    monkeypatch.setenv("TOTAL_DRAWDOWN_STOP_USD", "150")
    monkeypatch.setenv("STRATEGY_PHASE", "1")
    reload_settings()
    yield
    reload_settings()


@pytest_asyncio.fixture
async def store(tmp_path) -> TradeStore:
    s = TradeStore(path=tmp_path / "Trade.json")
    await s.load()
    return s
