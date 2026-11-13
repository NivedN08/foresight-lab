"""Time parsing for the formats the turbulence data sources use."""
from __future__ import annotations

from datetime import datetime, timezone


def parse_awc_time(s: str) -> datetime:
    """'2026-11-13T21:00:00.000Z' or '2026-11-13T21:00:00Z' -> aware UTC datetime."""
    s = s.strip().replace("Z", "+00:00")
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)
