from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.state.positions import Position


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS ws_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    client_order_id TEXT PRIMARY KEY,
    arb_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    market_key TEXT NOT NULL,
    side TEXT NOT NULL,
    intended_size REAL NOT NULL,
    filled_size REAL NOT NULL,
    avg_price REAL NOT NULL,
    status TEXT NOT NULL,
    order_ids_json TEXT NOT NULL,
    oracle_source TEXT NOT NULL,
    resolution_time TEXT,
    bridge_in_flight INTEGER NOT NULL,
    directional_unhedged INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arb_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    venue TEXT NOT NULL,
    market_key TEXT NOT NULL,
    pnl_usd REAL NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0,
    profitable INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    drift_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class BotDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record_ws_event(self, venue: str, event_type: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO ws_events (venue, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (venue, event_type, _json(payload), _now()),
            )

    def upsert_position(self, position: Position) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    client_order_id, arb_id, platform, market_key, side, intended_size,
                    filled_size, avg_price, status, order_ids_json, oracle_source,
                    resolution_time, bridge_in_flight, directional_unhedged, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    arb_id=excluded.arb_id,
                    platform=excluded.platform,
                    market_key=excluded.market_key,
                    side=excluded.side,
                    intended_size=excluded.intended_size,
                    filled_size=excluded.filled_size,
                    avg_price=excluded.avg_price,
                    status=excluded.status,
                    order_ids_json=excluded.order_ids_json,
                    oracle_source=excluded.oracle_source,
                    resolution_time=excluded.resolution_time,
                    bridge_in_flight=excluded.bridge_in_flight,
                    directional_unhedged=excluded.directional_unhedged,
                    updated_at=excluded.updated_at
                """,
                (
                    position.client_order_id,
                    position.arb_id,
                    position.platform,
                    position.market_key,
                    position.side,
                    position.intended_size,
                    position.filled_size,
                    position.avg_price,
                    position.status.value,
                    _json(position.order_ids),
                    position.oracle_source,
                    position.resolution_time.isoformat() if position.resolution_time else None,
                    int(position.bridge_in_flight),
                    int(position.directional_unhedged),
                    position.updated_at.isoformat(),
                ),
            )

    def record_trade(
        self,
        *,
        arb_id: str,
        strategy: str,
        venue: str,
        market_key: str,
        pnl_usd: float,
        closed: bool,
        payload: dict[str, Any],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    arb_id, strategy, venue, market_key, pnl_usd, closed,
                    profitable, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    arb_id,
                    strategy,
                    venue,
                    market_key,
                    pnl_usd,
                    int(closed),
                    int(pnl_usd > 0),
                    _json(payload),
                    _now(),
                ),
            )

    def phase1_stats(self) -> tuple[int, float]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS closed_count,
                       COALESCE(SUM(profitable), 0) AS profitable_count
                FROM trades
                WHERE strategy = 'yes_no_complementarity' AND closed = 1
                """
            ).fetchone()
        closed_count = int(row["closed_count"])
        profitable_count = int(row["profitable_count"])
        rate = profitable_count / closed_count if closed_count else 0.0
        return closed_count, rate

    def record_reconciliation(self, venue: str, drift_count: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reconciliations (venue, drift_count, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (venue, drift_count, _json(payload), _now()),
            )


class TradeJournal:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, trade: dict[str, Any]) -> None:
        existing: list[dict[str, Any]]
        if self.path.exists():
            existing = json.loads(self.path.read_text())
            if not isinstance(existing, list):
                existing = []
        else:
            existing = []
        existing.append({"created_at": _now(), **trade})
        self.path.write_text(json.dumps(existing, indent=2, sort_keys=True))


def _json(value: Any) -> str:
    def default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "value"):
            return obj.value
        if hasattr(obj, "__dict__"):
            return asdict(obj)
        return str(obj)

    return json.dumps(value, default=default, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

