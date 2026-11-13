"""Polite HTTP for the turbulence task: generic User-Agent, retries with
exponential backoff, and a small pause between calls to the same host."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

USER_AGENT = "foresight-lab research client"
ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "cache" / "turb"
RUNS = ROOT / "runs" / "turb"

_last_call: dict[str, float] = {}


class FetchError(Exception):
    pass


def get(url: str, params: dict | None = None, *, tries: int = 6, timeout: float = 120.0,
        min_gap: float = 1.0, log=print) -> httpx.Response:
    """GET with retries. Waits at least `min_gap` seconds between calls to one host.
    Retries on connection errors, timeouts, 429 and 5xx; raises FetchError at the end."""
    host = httpx.URL(url).host
    delay = 5.0
    last = ""
    for attempt in range(tries):
        gap = time.monotonic() - _last_call.get(host, 0.0)
        if gap < min_gap:
            time.sleep(min_gap - gap)
        _last_call[host] = time.monotonic()
        try:
            r = httpx.get(url, params=params, timeout=timeout,
                          headers={"User-Agent": USER_AGENT}, follow_redirects=True)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}: " + " ".join(r.text[:200].split())
            if r.status_code not in (429, 500, 502, 503, 504):
                raise FetchError(f"{url}: {last}")
            if r.status_code == 429:
                delay = max(delay, 65.0)  # Open-Meteo's window is one minute
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = f"{type(e).__name__}: {e}"
        log(f"  fetch retry {attempt + 1}/{tries} for {host}: {last}; waiting {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * 2, 300.0)
    raise FetchError(f"{url}: gave up after {tries} tries ({last})")
