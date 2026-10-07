# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Valve data helpers. Pure Python (no Home Assistant imports)."""

from __future__ import annotations

from typing import Any

# Valve type codes (``dlvt``) as published in Chandler's Signature API guide.
VALVE_TYPES: dict[int, str] = {
    1: "Metered Softener",
    2: "Timeclock Softener",
    3: "Metered Softener",
    4: "Backwashing Filter",
    5: "Backwashing Filter",
    6: "HydroxR Filter",
    7: "ReactR Filter",
    8: "Ultra Filter",
    9: "Aeration Filter",
    10: "Aeration Filter",
    11: "Aeration Filter",
    12: "Aeration Filter",
    13: "Aeration Filter",
    14: "Aeration Filter",
    15: "Aeration Filter",
    16: "Aeration Filter",
    17: "Metered Softener",
    18: "Backwashing Filter",
    19: "Metered Softener",
    20: "Backwashing Filter",
    21: "Metered Softener",
    22: "Backwashing Filter",
    23: "Aeration Filter",
    24: "Aeration Filter",
    25: "Aeration Filter",
    26: "Backwashing Filter",
    27: "Backwashing Filter",
}
METERED_SOFTENER_TYPES = frozenset({1, 3, 17, 19, 21})
SOFTENER_TYPES = METERED_SOFTENER_TYPES | {2}

VALVE_SERIES: dict[int, str] = {
    2: "Series 2 (D12)",
    3: "Series 3 (D15)",
    4: "Series 4 (CS125)",
    5: "Series 5 (CS150)",
    6: "Series 6 (CS121)",
}


def valve_type_name(state: dict[str, Any]) -> str:
    """Human readable valve type, e.g. "Metered Softener"."""
    return VALVE_TYPES.get(state.get("dlvt", -1), "Water Treatment Valve")


def is_softener(state: dict[str, Any]) -> bool:
    return state.get("dlvt") in SOFTENER_TYPES


def is_metered_softener(state: dict[str, Any]) -> bool:
    return state.get("dlvt") in METERED_SOFTENER_TYPES


def firmware_version(state: dict[str, Any]) -> str | None:
    """Firmware as shown in the app, e.g. 618 -> "C6.18"."""
    raw = state.get("dlf")
    if not isinstance(raw, int) or raw <= 0:
        return None
    return f"C{raw // 100}.{raw % 100:02d}"


def serial_number(state: dict[str, Any]) -> str | None:
    """Serial number: the two 32-bit halves written in hex and joined."""
    a, b = state.get("dlsa"), state.get("dlsb")
    if not isinstance(a, int) or not isinstance(b, int):
        return None
    return f"{a:X}{b:X}"


def scaled(state: dict[str, Any], key: str, divisor: float) -> float | None:
    """Return state[key] / divisor, or None when the key is missing."""
    value = state.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(value / divisor, 3)


def battery_volts(state: dict[str, Any]) -> float | None:
    """Battery voltage; None for valves with no battery reading (reported as 0)."""
    value = scaled(state, "dbl", 1000)
    return value if value else None


def restore_water_total(
    extra: dict[str, Any] | None,
    state: str | None,
    attributes: dict[str, Any] | None,
) -> tuple[float, float | None]:
    """Work out (running total, last daily reading) after a restart, tolerating junk.

    Prefers the stored extra data. Falls back to the last entity state and its attribute.
    Anything missing, ``unknown`` or ``unavailable`` means "start fresh" instead of raising.
    """

    def number(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if extra:
        total = number(extra.get("total"))
        if total is not None:
            return total, number(extra.get("last"))
    total = number(state)
    if total is None:
        return 0.0, None
    return total, number((attributes or {}).get("last_daily_reading"))


def seconds_to_next_minute(second: int, microsecond: int = 0) -> float:
    """Seconds from ``hh:mm:second.microsecond`` until the next minute begins."""
    return 60.0 - second - microsecond / 1_000_000


def accumulate_daily(total: float, last: float | None, new: float) -> float:
    """Fold a counter that resets to zero every day into a running total.

    ``last`` is the previous reading (None for the first one, which only sets the
    baseline). A reading below the previous one means the counter reset, so the new
    reading is all usage since the reset.
    """
    if last is None:
        return total
    delta = new - last if new >= last else new
    return round(total + delta, 3)


def salt_percent(state: dict[str, Any]) -> float | None:
    """Salt remaining as a percentage of the brine tank capacity."""
    remaining = scaled(state, "dbtr", 10)
    capacity = state.get("dbts")
    if remaining is None or not isinstance(capacity, (int, float)) or capacity <= 0:
        return None
    return round(min(remaining / capacity * 100, 100), 1)

