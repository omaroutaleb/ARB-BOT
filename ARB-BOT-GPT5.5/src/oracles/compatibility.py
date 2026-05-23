from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Oracle(StrEnum):
    CHAINLINK = "chainlink"
    PYTH = "pyth"
    UMA = "uma"
    BINANCE = "binance"
    MANUAL = "manual"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OracleCompatibility:
    hard_arb_compatible: bool
    relative_value_compatible: bool
    haircut_bps: int
    reason: str


def normalize_oracle(value: str | None) -> Oracle:
    text = (value or "").lower()
    if "chainlink" in text:
        return Oracle.CHAINLINK
    if "pyth" in text:
        return Oracle.PYTH
    if "uma" in text or "optimistic" in text:
        return Oracle.UMA
    if "binance" in text:
        return Oracle.BINANCE
    if "manual" in text or "team review" in text:
        return Oracle.MANUAL
    return Oracle.UNKNOWN


def compare_oracles(a: str | Oracle | None, b: str | Oracle | None, *, mismatch_haircut_bps: int = 50) -> OracleCompatibility:
    left = a if isinstance(a, Oracle) else normalize_oracle(a)
    right = b if isinstance(b, Oracle) else normalize_oracle(b)
    if left == right and left is not Oracle.UNKNOWN:
        return OracleCompatibility(True, True, 0, "same objective oracle")
    if {left, right} == {Oracle.CHAINLINK, Oracle.PYTH}:
        return OracleCompatibility(
            False,
            True,
            mismatch_haircut_bps,
            "objective feeds differ; conservative relative-value only",
        )
    if Oracle.UMA in {left, right} or Oracle.MANUAL in {left, right}:
        return OracleCompatibility(
            False,
            False,
            max(mismatch_haircut_bps, 100),
            "optimistic/manual resolution is not hard-arb compatible",
        )
    return OracleCompatibility(
        False,
        False,
        max(mismatch_haircut_bps, 100),
        "unknown or incompatible oracle metadata",
    )

