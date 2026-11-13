"""The past.dev memory API as a lab notebook backend.

    POST /ingest            {"id", "content", "label", "timestamp", "audience"} -> {"ingestionId", ...}
    GET  /ingest/{ingestionId}   poll until "status" is "completed" ("failed" carries "failure")
    POST /recall            {"query", "identity", "level", "queryTimestamp", "limit"}
                            -> {"results": [{"content", "occurredAt", "sources": [{"sourceId", ...}]}]}

`id` is the finding id, so recall results map back to findings through
`sources[].sourceId`. `queryTimestamp` gives as-of recall; as a second guard,
results whose `occurredAt` is after `as_of` are dropped on our side too.
Superseded facts are handled by the service. Findings are also kept locally
and written to the JSONL file like every other backend.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import httpx

from lab.core.timeutil import from_iso, to_iso, utcnow
from lab.memory.base import Finding, LocalMemory, RecallResult

BASE_URL = "https://api.past.dev/api/v1"


class PastDevError(RuntimeError):
    pass


class PastDevMemory(LocalMemory):
    name = "pastdev"

    def __init__(self, path=None, *, api_key: str | None = None, base_url: str = BASE_URL,
                 transport: httpx.BaseTransport | None = None, audience: str = "project",
                 level: str = "low", wait: bool = True, poll_interval_s: float = 2.0,
                 poll_timeout_s: float = 300.0, timeout_s: float = 60.0, max_chars: int = 12_000,
                 sleep=time.sleep):
        api_key = api_key or os.environ.get("PAST_API_KEY")
        if not api_key:
            raise PastDevError("pastdev memory needs an API key: set PAST_API_KEY "
                               "(or pass api_key=...)")
        super().__init__(path)
        self.audience = audience
        self.level = level
        self.wait = wait
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s
        self.max_chars = max_chars
        self._sleep = sleep
        self.client = httpx.Client(base_url=base_url, transport=transport, timeout=timeout_s,
                                   headers={"Authorization": f"Bearer {api_key}"})

    def _check(self, r: httpx.Response, what: str) -> dict:
        if r.status_code >= 400:
            raise PastDevError(f"past.dev {what} failed: HTTP {r.status_code} {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            raise PastDevError(f"past.dev {what}: non-JSON reply {r.text[:300]}") from None

    def add(self, finding: Finding) -> None:
        super().add(finding)          # local copy + JSONL first, so nothing is lost if the call fails
        label = f"{finding.problem} gen {finding.generation} {'accepted' if finding.accepted else 'rejected'}"
        body = {"id": finding.id, "content": finding.text, "label": label[:200],
                "timestamp": to_iso(finding.time), "audience": self.audience}
        data = self._check(self.client.post("/ingest", json=body), "ingest")
        ingestion_id = data.get("ingestionId")
        if not self.wait or data.get("status") == "completed" or not ingestion_id:
            return
        deadline = time.monotonic() + self.poll_timeout_s
        while True:
            st = self._check(self.client.get(f"/ingest/{ingestion_id}"), "ingest status")
            status = st.get("status")
            if status == "completed":
                return
            if status == "failed":
                raise PastDevError(f"past.dev ingest of {finding.id} failed: {st.get('failure')}")
            if time.monotonic() > deadline:
                raise PastDevError(f"past.dev ingest of {finding.id} still {status!r} "
                                   f"after {self.poll_timeout_s:g}s")
            self._sleep(self.poll_interval_s)

    def recall(self, query: str, *, as_of: datetime | None = None, k: int = 8,
               identity: str = "lab") -> RecallResult:
        t0 = time.perf_counter()
        body = {"query": query, "identity": identity, "level": self.level,
                "queryTimestamp": to_iso(as_of or utcnow()), "limit": max(1, min(200, k))}
        data = self._check(self.client.post("/recall", json=body), "recall")
        by_id = {f.id: f for f in self.all()}
        findings, seen, chunks, used = [], set(), [], 0
        for res in data.get("results") or []:
            occurred = res.get("occurredAt")
            if as_of is not None and occurred and from_iso(occurred) > as_of:
                continue
            content = str(res.get("content") or "").strip()
            if content and used + len(content) + 3 <= self.max_chars:
                chunks.append(f"- {content}")
                used += len(content) + 3
            for src in res.get("sources") or []:
                f = by_id.get(src.get("sourceId"))
                if f and f.id not in seen and (as_of is None or f.time <= as_of) and len(findings) < k:
                    seen.add(f.id)
                    findings.append(f)
        text = "\n".join(chunks)
        return RecallResult(findings, text, time.perf_counter() - t0, len(text))

    def close(self) -> None:
        self.client.close()
