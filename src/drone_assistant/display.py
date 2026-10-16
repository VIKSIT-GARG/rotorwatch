"""Human timestamps use IST. Database and API timestamps remain UTC ISO values."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")
ISO_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b")


def ist_time(value: str | None) -> str:
    if not value:
        return "not recorded"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    except (ValueError, TypeError, AttributeError):
        return "unverified time"


def ist_text(text: str) -> str:
    return ISO_TIME.sub(lambda match: ist_time(match.group()), text)
