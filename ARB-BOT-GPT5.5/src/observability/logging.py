from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any

import structlog


SECRET_KEYS = {
    "api_key",
    "apikey",
    "apiSecret",
    "api_secret",
    "secret",
    "passphrase",
    "private_key",
    "signature",
    "poly_signature",
    "x-api-key",
    "authorization",
}


def redact_secrets(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    def clean(value: Any, key: str | None = None) -> Any:
        if key and key.lower() in {item.lower() for item in SECRET_KEYS}:
            return "[REDACTED]"
        if isinstance(value, Mapping):
            return {str(k): clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return {key: clean(value, key) for key, value in event_dict.items()}


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            redact_secrets,
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

