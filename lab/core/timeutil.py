"""UTC time helpers. Every timestamp in the lab is timezone-aware UTC."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(t: datetime) -> str:
    """Format as 2026-11-13T21:30:00Z (second resolution, always UTC)."""
    if t.tzinfo is None:
        raise ValueError("naive datetime; all lab times must be UTC-aware")
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str) -> datetime:
    """Parse the format written by to_iso (also accepts +00:00 offsets)."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)
