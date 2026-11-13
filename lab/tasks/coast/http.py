"""Cached, polite HTTP client for the coast data sources.

Every response is written under `cache/coast/` keyed by a hash of the URL.
A cached response is reused while it is younger than `ttl` seconds
(`ttl=None` means forever, used for historical chunks that no longer change).
Failed requests are retried with exponential backoff. If every retry fails
and an older cached copy exists, the stale copy is returned instead.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

USER_AGENT = "foresight-lab research client"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = ROOT / "cache" / "coast"


class FetchError(Exception):
    pass


class Client:
    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE, retries: int = 4,
                 pause: float = 0.25, timeout: float = 40.0, offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.retries = retries
        self.pause = pause
        self.offline = offline
        self._http = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT},
                                  follow_redirects=True)
        self._last = 0.0
        self.log: list[str] = []

    def _path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self.cache_dir / f"{h}.txt"

    def get_text(self, url: str, ttl: float | None = None) -> str:
        path = self._path(url)
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if ttl is None or age < ttl or self.offline:
                return path.read_text()
        if self.offline:
            raise FetchError(f"offline and not cached: {url}")
        err: Exception | None = None
        for attempt in range(self.retries):
            wait = self.pause - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self._http.get(url)
                if r.status_code == 200:
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(r.text)
                    tmp.replace(path)
                    path.with_suffix(".url").write_text(url)
                    return r.text
                if r.status_code in (400, 404):
                    raise FetchError(f"HTTP {r.status_code} for {url}: {r.text[:200]}")
                err = FetchError(f"HTTP {r.status_code} for {url}")
            except FetchError:
                raise
            except Exception as e:  # network errors, timeouts
                err = e
            time.sleep(min(30.0, 1.5 * 2 ** attempt))
        if path.exists():
            self.log.append(f"stale cache used for {url}: {err}")
            return path.read_text()
        raise FetchError(f"failed after {self.retries} tries: {url}: {err}")

    def get_json(self, url: str, ttl: float | None = None):
        return json.loads(self.get_text(url, ttl=ttl))
