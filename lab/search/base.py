"""Shared plumbing for literature search: the Hit record, an on-disk cache, retry
on rate limits, a fallback chain, and counters of searches made and failed."""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from lab.core.timeutil import to_iso

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "cache" / "search"
USER_AGENT = "foresight-lab/0.1 (research prototype)"
MAX_TEXT = 1500
MAX_WAIT_S = 20.0        # never wait longer than this for one rate-limit retry
RATE_LIMIT_RETRIES = 2   # retries after the first 429
DEFAULT_WAIT_S = 5.0     # wait when a 429 gives no hint
DOWN_S = 1800.0          # after a "daily limit used up" reply, skip that backend this long


class SearchError(RuntimeError):
    pass


@dataclass
class Hit:
    title: str
    url: str
    year: int | None
    text: str
    backend: str = ""    # which search backend produced this hit

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchOutcome:
    hits: list[Hit]
    backend: str | None                 # backend whose hits were used (None: nothing worked)
    failures: list[dict] = field(default_factory=list)   # [{"backend", "error"}] tried before it
    from_cache: bool = False


def retry_after_s(r: httpx.Response) -> float:
    """Seconds a 429 asks us to wait: Retry-After header, else 'retry in Ns' in the body."""
    h = r.headers.get("retry-after")
    if h:
        try:
            return float(h)
        except ValueError:
            pass
    m = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", r.text or "", re.I)
    return float(m.group(1)) if m else DEFAULT_WAIT_S


def daily_limit_used_up(r: httpx.Response) -> bool:
    """A 429 that says the day's free allowance is gone; waiting a few seconds cannot help."""
    text = (r.text or "").lower()
    return "daily" in text and any(w in text for w in ("budget", "quota", "limit"))


class Searcher:
    """Subclasses implement `_fetch(query, k, before) -> list[Hit]` with `self.client`.

    `search` answers from the cache when it can, otherwise calls `_fetch` and
    stores the result. Counters: `searches` (real searches attempted, one per
    query), `failed` (searches that raised after retries), `cache_hits`, and
    `retries` (rate-limit retries). Pass `cache_dir=None` to turn the cache off;
    `sleep` and `clock` are injectable for tests.

    `min_interval_s` spaces real requests to one backend, across every process
    that shares the cache directory (a lock file holds the time of the last
    request). A 429 that says the daily allowance is used up is not retried and
    switches the backend off for DOWN_S seconds, so later queries fail at once
    instead of waiting."""

    name = "base"
    min_interval_s = 0.0

    def __init__(self, *, transport: httpx.BaseTransport | None = None,
                 cache_dir: str | Path | None = DEFAULT_CACHE, timeout_s: float = 30.0,
                 sleep=time.sleep, max_wait_s: float = MAX_WAIT_S, retries: int = RATE_LIMIT_RETRIES,
                 clock=time.time, min_interval_s: float | None = None):
        self.client = httpx.Client(transport=transport, timeout=timeout_s, follow_redirects=True,
                                   headers={"User-Agent": USER_AGENT})
        self.cache_dir = Path(cache_dir) / self.name if cache_dir is not None else None
        self._lock = threading.Lock()
        self._sleep = sleep
        self._now = clock
        if min_interval_s is not None:
            self.min_interval_s = min_interval_s
        self._down_until = 0.0
        self.max_wait_s = max_wait_s
        self.retries_allowed = retries
        self.searches = 0
        self.failed = 0
        self.cache_hits = 0
        self.retries = 0

    def _key(self, query: str, k: int, before: datetime | None) -> str:
        raw = json.dumps([self.name, query, k, to_iso(before) if before else None])
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _bump(self, counter: str) -> None:
        with self._lock:
            setattr(self, counter, getattr(self, counter) + 1)

    def search_detailed(self, query: str, *, k: int = 5, before: datetime | None = None) -> SearchOutcome:
        path = self.cache_dir / f"{self._key(query, k, before)}.json" if self.cache_dir else None
        if path is not None and path.exists():
            try:
                stored = json.loads(path.read_text())["hits"]
            except (ValueError, KeyError):
                stored = None
            if stored is not None:
                self._bump("cache_hits")
                hits = [Hit(**{**h, "backend": h.get("backend") or self.name}) for h in stored]
                return SearchOutcome(hits, self.name, from_cache=True)
        self._bump("searches")
        try:
            hits = self._fetch(query, k, before)[:k]
        except SearchError:
            self._bump("failed")
            raise
        except Exception as e:      # anything unexpected counts as a failed search too
            self._bump("failed")
            raise SearchError(f"{self.name} search failed: {type(e).__name__}: {e}") from e
        for h in hits:
            h.backend = self.name
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"backend": self.name, "query": query, "k": k,
                                       "before": to_iso(before) if before else None,
                                       "hits": [h.to_dict() for h in hits]}, indent=1))
            tmp.replace(path)
        return SearchOutcome(hits, self.name)

    def search(self, query: str, *, k: int = 5, before: datetime | None = None) -> list[Hit]:
        return self.search_detailed(query, k=k, before=before).hits

    def _fetch(self, query: str, k: int, before: datetime | None) -> list[Hit]:
        raise NotImplementedError

    def _throttle(self) -> None:
        """Wait until `min_interval_s` has passed since the last request by any process."""
        if not self.min_interval_s or self.cache_dir is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cache_dir / ".throttle", "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)       # held while waiting, so callers queue up
            f.seek(0)
            try:
                last = float(f.read().strip())
            except ValueError:
                last = 0.0
            wait = last + self.min_interval_s - self._now()
            if wait > 0:
                self._sleep(min(wait, self.min_interval_s))
            f.seek(0)
            f.truncate()
            f.write(repr(self._now()))

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """One HTTP call; on 429 wait for the hinted time (capped) and retry."""
        if self._now() < self._down_until:
            raise SearchError(f"{self.name} search skipped: its daily limit was reached earlier in this run")
        for attempt in range(self.retries_allowed + 1):
            self._throttle()
            try:
                r = self.client.request(method, url, **kw)
            except httpx.HTTPError as e:
                raise SearchError(f"{self.name} search failed: {type(e).__name__}: {e}") from e
            if r.status_code == 429 and daily_limit_used_up(r):
                self._down_until = self._now() + DOWN_S
                raise SearchError(f"{self.name} search failed: HTTP 429 daily limit used up: {r.text[:200]}")
            if r.status_code == 429 and attempt < self.retries_allowed:
                self._bump("retries")
                self._sleep(min(retry_after_s(r), self.max_wait_s))
                continue
            if r.status_code >= 400:
                raise SearchError(f"{self.name} search failed: HTTP {r.status_code} {r.text[:300]}")
            return r
        raise SearchError(f"{self.name} search failed: still rate-limited")   # not reached

    def _get(self, url: str, **kw) -> httpx.Response:
        return self._request("GET", url, **kw)

    def stats(self) -> dict:
        with self._lock:
            return {"backend": self.name, "searches": self.searches, "failed": self.failed,
                    "cache_hits": self.cache_hits, "retries": self.retries}


class ChainSearch:
    """Try backends in order; the first that returns hits wins. A backend that
    raises (after its own rate-limit retries) or returns nothing hands over to
    the next. `name` is the first backend's name."""

    def __init__(self, backends: list):
        if not backends:
            raise ValueError("a search chain needs at least one backend")
        self.backends = list(backends)
        self.name = getattr(self.backends[0], "name", "chain")
        self._lock = threading.Lock()
        self.queries = 0
        self.no_hits = 0      # queries for which no backend produced anything

    def search_detailed(self, query: str, *, k: int = 5, before: datetime | None = None) -> SearchOutcome:
        with self._lock:
            self.queries += 1
        failures = []
        for b in self.backends:
            try:
                out = b.search_detailed(query, k=k, before=before) if hasattr(b, "search_detailed") \
                    else SearchOutcome(b.search(query, k=k, before=before), getattr(b, "name", "?"))
            except Exception as e:
                failures.append({"backend": getattr(b, "name", "?"), "error": str(e)[:300]})
                continue
            if out.hits:
                out.failures = failures + out.failures
                return out
            failures.append({"backend": out.backend, "error": "no hits"})
        with self._lock:
            self.no_hits += 1
        return SearchOutcome([], None, failures)

    def search(self, query: str, *, k: int = 5, before: datetime | None = None) -> list[Hit]:
        return self.search_detailed(query, k=k, before=before).hits

    def stats(self) -> dict:
        per = {getattr(b, "name", str(i)): (b.stats() if hasattr(b, "stats") else {})
               for i, b in enumerate(self.backends)}
        total = {key: sum(int(s.get(key, 0)) for s in per.values())
                 for key in ("searches", "failed", "cache_hits", "retries")}
        with self._lock:
            return {"backend": self.name, "chain": [getattr(b, "name", "?") for b in self.backends],
                    "queries": self.queries, "queries_without_hits": self.no_hits, **total,
                    "by_backend": per}


def clip(text: str, n: int = MAX_TEXT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
