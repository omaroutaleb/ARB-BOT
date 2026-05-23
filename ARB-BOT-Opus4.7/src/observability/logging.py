"""Structured JSON logging via structlog. Container-friendly stdout."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog
from structlog.types import EventDict

_SECRET_KEY_PATTERN = re.compile(
    r"^(api[_-]?key|private[_-]?key|secret|passphrase|signature|x[_-]?api[_-]?key|"
    r"poly_signature|poly_passphrase|poly_api_key|lmts_signature|authorization|"
    r"bearer|password|seed|mnemonic)$",
    re.IGNORECASE,
)

_SECRET_VALUE_PATTERN = re.compile(
    r"(0x[a-fA-F0-9]{40,})|(lmts_[A-Za-z0-9_-]{8,})|([A-Za-z0-9+/]{40,}=*)"
)


def _redact_secrets(_, __, event_dict: EventDict) -> EventDict:
    """Walk the event dict and redact any value whose key matches a known
    secret pattern, or whose value looks like one (long hex / lmts_ / base64).

    Conservative: redact ANY string ≥40 chars that matches the value pattern.
    """
    return {k: _redact_one(k, v) for k, v in event_dict.items()}


def _redact_one(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact_one(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_one(key, v) for v in value]
    if isinstance(value, str):
        if _SECRET_KEY_PATTERN.search(key or ""):
            return "<redacted>"
        if len(value) >= 40 and _SECRET_VALUE_PATTERN.search(value):
            return value[:6] + "…<redacted>"
    return value


def configure_logging(level: str = "INFO") -> None:
    """Idempotent — safe to call multiple times."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name) if name else structlog.get_logger()
