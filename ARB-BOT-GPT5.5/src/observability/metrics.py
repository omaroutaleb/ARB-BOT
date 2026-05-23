from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server


OPPORTUNITIES = Counter(
    "arb_opportunities_total",
    "Arbitrage opportunities observed",
    ["strategy", "venue"],
)
ORDERS_SUBMITTED = Counter(
    "orders_submitted_total",
    "Orders submitted by venue and type",
    ["venue", "order_type"],
)
ORDER_FAILURES = Counter(
    "order_failures_total",
    "Order submission or cancellation failures",
    ["venue", "operation"],
)
OPEN_EXPOSURE = Gauge(
    "open_exposure_usd",
    "Open exposure in USD by venue",
    ["venue"],
)
REALIZED_PNL = Gauge("realized_pnl_usd", "Realized PnL in USD")
REQUEST_LATENCY = Histogram(
    "venue_request_latency_seconds",
    "Venue REST request latency",
    ["venue", "method", "path"],
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)

