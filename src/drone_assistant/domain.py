from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class Status(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PRE_ORDER = "pre_order"
    UNKNOWN = "unknown"


FAVORABLE = {Status.IN_STOCK, Status.PRE_ORDER}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def money_minor(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = re.sub(r"[₹,\s]", "", str(value)).removeprefix("INR")
    try:
        amount = Decimal(text)
        if not amount.is_finite() or amount < 0 or amount > 100_000_000:
            return None
        return int((amount * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


def format_money(minor: int | None, currency: str = "INR") -> str:
    if minor is None:
        return "price unknown"
    return f"{'₹' if currency == 'INR' else currency + ' '}{minor / 100:,.2f}"


def category_for(title: str) -> str:
    title = title.casefold()
    if any(word in title for word in ("hat", "carrier", "expansion board")):
        return "hats_carrier_boards"
    if any(word in title for word in ("pixhawk", "flight controller", "autopilot")):
        return "flight_controllers"
    if any(word in title for word in ("raspberry pi", "jetson", "companion computer")):
        return "companion_computers"
    return "other"


def query_matches(query: str, text: str) -> bool:
    """AND matching with whole terms: Pi 5 must not match Pi 500."""
    terms = re.findall(r"[a-z0-9]+", query.casefold())
    words = set(re.findall(r"[a-z0-9]+", text.casefold()))
    return bool(terms) and all(t in words or (t.endswith("s") and t[:-1] in words) for t in terms)


@dataclass
class Snapshot:
    title: str
    status: Status = Status.UNKNOWN
    price_minor: int | None = None
    currency: str = "INR"
    attributes: dict[str, str] = field(default_factory=dict)
    evidence: str = "No unambiguous availability signal found."
    confidence: float = 0.0
    method: str = "unknown"
    error: str | None = None
    observed_at: str = field(default_factory=utcnow)


@dataclass
class Intent:
    action: str = "search"
    query: str = ""
    ids: list[int] = field(default_factory=list)
    max_price_minor: int | None = None
    status: str | None = None
    source: str | None = None
    min_ram_gb: int | None = None


class FetchError(Exception):
    """A safe error code, never a raw request containing authentication headers."""
