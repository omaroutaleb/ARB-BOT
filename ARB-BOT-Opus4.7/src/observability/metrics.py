"""Prometheus metrics. Exposed on /metrics; scraped by any Prometheus-compatible system."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ---- Counters ----
orders_submitted = Counter(
    "arbbot_orders_submitted_total",
    "Orders submitted",
    ["platform", "side", "strategy"],
)
orders_filled = Counter(
    "arbbot_orders_filled_total",
    "Orders that received any fill",
    ["platform", "side", "strategy"],
)
orders_cancelled = Counter(
    "arbbot_orders_cancelled_total",
    "Orders cancelled",
    ["platform", "reason"],
)
orders_rejected = Counter(
    "arbbot_orders_rejected_total",
    "Orders rejected by the venue",
    ["platform", "code"],
)
ws_reconnects = Counter(
    "arbbot_ws_reconnects_total",
    "WebSocket reconnect events",
    ["platform", "channel"],
)
heartbeats_sent = Counter(
    "arbbot_heartbeats_sent_total",
    "Polymarket heartbeats sent",
)
orphan_legs = Counter(
    "arbbot_orphan_legs_total",
    "Hedge legs that failed to fill",
    ["platform", "resolution"],
)
kill_switch_triggers = Counter(
    "arbbot_kill_switch_triggers_total",
    "Kill switch activations",
    ["reason"],
)

# ---- Gauges ----
open_positions = Gauge(
    "arbbot_open_positions",
    "Currently open positions",
    ["platform"],
)
bankroll_usd = Gauge(
    "arbbot_bankroll_usd",
    "Current bankroll in USD",
)
equity_usd = Gauge(
    "arbbot_equity_usd",
    "Current equity (cash + open position MTM) in USD",
)
drawdown_usd = Gauge(
    "arbbot_drawdown_usd",
    "Drawdown from peak equity in USD",
)
strategy_phase = Gauge(
    "arbbot_strategy_phase",
    "Active strategy phase (1, 2, or 3)",
)

# ---- Histograms ----
order_latency_seconds = Histogram(
    "arbbot_order_latency_seconds",
    "Order submission round-trip latency",
    ["platform"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
ws_message_lag_seconds = Histogram(
    "arbbot_ws_message_lag_seconds",
    "Wall-clock lag between event timestamp and local receive (best-effort)",
    ["platform", "channel"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

_server_started = False


def start_metrics_server(port: int) -> None:
    global _server_started
    if _server_started:
        return
    start_http_server(port)
    _server_started = True
