"""Shared plumbing for literature search: the Hit record, an on-disk cache and a
counter of searches made."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx

from lab.core.timeutil import to_iso

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "cache" / "search"
USER_AGENT = "foresight-lab/0.1 (research prototype)"
MAX_TEXT = 1500


class SearchError(RuntimeError):
    pass


@dataclass
class Hit:
    title: str
    url: str
    year: int | None
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


class Searcher:
    """Subclasses implement `_fetch(query, k, before) -> list[Hit]` with `self.client`.

    `search` answers from the cache when it can (`cache_hits`), otherwise calls
    `_fetch` (`searches`, counted only for real calls) and stores the result.
    Pass `cache_dir=None` to turn the cache off."""

    name = "base"

    def __init__(self, *, transport: httpx.BaseTransport | None = None,
                 cache_dir: str | Path | None = DEFAULT_CACHE, timeout_s: float = 30.0):
        self.client = httpx.Client(transport=transport, timeout=timeout_s, follow_redirects=True,
                                   headers={"User-Agent": USER_AGENT})
        self.cache_dir = Path(cache_dir) / self.name if cache_dir is not None else None
        self._lock = threading.Lock()
        self.searches = 0
        self.cache_hits = 0

    def _key(self, query: str, k: int, before: datetime | None) -> str:
        raw = json.dumps([self.name, query, k, to_iso(before) if before else None])
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def search(self, query: str, *, k: int = 5, before: datetime | None = None) -> list[Hit]:
        path = self.cache_dir / f"{self._key(query, k, before)}.json" if self.cache_dir else None
        if path is not None and path.exists():
            with self._lock:
                self.cache_hits += 1
            return [Hit(**h) for h in json.loads(path.read_text())["hits"]]
        with self._lock:
            self.searches += 1
        hits = self._fetch(query, k, before)[:k]
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"backend": self.name, "query": query, "k": k,
                                        "before": to_iso(before) if before else None,
                                        "hits": [h.to_dict() for h in hits]}, indent=1))
        return hits

    def _fetch(self, query: str, k: int, before: datetime | None) -> list[Hit]:
        raise NotImplementedError

    def _get(self, url: str, **kw) -> httpx.Response:
        try:
            r = self.client.get(url, **kw)
        except httpx.HTTPError as e:
            raise SearchError(f"{self.name} search failed: {e}") from e
        if r.status_code >= 400:
            raise SearchError(f"{self.name} search failed: HTTP {r.status_code} {r.text[:300]}")
        return r

    def stats(self) -> dict:
        with self._lock:
            return {"backend": self.name, "searches": self.searches, "cache_hits": self.cache_hits}


def clip(text: str, n: int = MAX_TEXT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
