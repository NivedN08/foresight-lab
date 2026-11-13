"""The lab notebook: four backends behind one interface."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from lab.core.timeutil import from_iso, to_iso
from lab.memory import (Finding, FullContextMemory, NoMemory, PastDevError, PastDevMemory, VectorMemory,
                        get_memory, load_findings, read_jsonl)

T = datetime(2026, 3, 1, tzinfo=timezone.utc)


def F(claim, hours, **kw):
    return Finding(claim=claim, problem="coast_water", generation=1, time=T + timedelta(hours=hours), **kw)


NOTEBOOK = [
    F("Add the inverse barometer term: sea level rises 1 cm per hPa of pressure drop.", 0, accepted=True,
      select_delta=-0.8, select_low=-1.1, select_high=-0.5, confirm_delta=-0.6),
    F("Shrink the wind stress coefficient toward zero for short leads.", 1, select_delta=0.1),
    F("Fit tide harmonics per station instead of one global set.", 2, accepted=True, select_delta=-0.3),
    F("Use river discharge upstream as an extra input.", 3, select_delta=0.02),
]


# ----------------------------------------------------------------- pastdev fake service


class FakePastDev:
    """A tiny in-memory past.dev: records requests, honours queryTimestamp."""

    def __init__(self, polls_before_complete=2):
        self.requests: list[httpx.Request] = []
        self.docs: dict[str, dict] = {}
        self.polls = {}
        self.polls_before_complete = polls_before_complete

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/ingest"):
            body = json.loads(request.content)
            ing = f"ing-{body['id']}"
            self.docs[body["id"]] = body
            self.polls[ing] = 0
            return httpx.Response(202, json={"ingestionId": ing, "projectId": 1, "sourceId": body["id"],
                                             "status": "queued", "unchanged": None})
        if request.method == "GET" and "/ingest/" in path:
            ing = path.rsplit("/", 1)[1]
            self.polls[ing] += 1
            status = "completed" if self.polls[ing] > self.polls_before_complete else "processing"
            return httpx.Response(200, json={"status": status, "settled": status == "completed",
                                             "blocked": False, "failure": None})
        if request.method == "POST" and path.endswith("/recall"):
            body = json.loads(request.content)
            cutoff = from_iso(body["queryTimestamp"])
            words = set(body["query"].lower().split())
            docs = [d for d in self.docs.values() if from_iso(d["timestamp"]) <= cutoff]
            docs.sort(key=lambda d: -len(words & set(d["content"].lower().split())))
            results = [{"id": f"r{i}", "rank": i + 1, "occurredAt": d["timestamp"], "content": d["content"],
                        "artifact": {"id": f"a{i}", "kind": "source", "occurredAt": d["timestamp"]},
                        "sources": [{"sourceId": d["id"], "occurredAt": d["timestamp"], "label": d["label"],
                                     "excerpts": [d["content"][:50]]}]}
                       for i, d in enumerate(docs[: body.get("limit", 100)])]
            return httpx.Response(200, json={"asOf": body["queryTimestamp"], "usedEvidenceTokens": 10,
                                             "results": results})
        return httpx.Response(404, json={"code": "not-found"})


def make(name, tmp_path, service=None):
    path = tmp_path / f"{name}.jsonl"
    if name == "pastdev":
        return PastDevMemory(path, api_key="test-key", transport=httpx.MockTransport(service or FakePastDev()),
                             poll_interval_s=0, sleep=lambda s: None)
    return get_memory(name, path=path)


ALL = ["none", "full_context", "vector", "pastdev"]


@pytest.mark.parametrize("name", ALL)
def test_as_of_hides_later_findings(name, tmp_path):
    mem = make(name, tmp_path)
    for f in NOTEBOOK:
        mem.add(f)
    cutoff = T + timedelta(hours=1, minutes=30)
    r = mem.recall("inverse barometer wind tide river", as_of=cutoff, k=10)
    assert all(f.time <= cutoff for f in r.findings)
    assert NOTEBOOK[2].claim not in r.text and NOTEBOOK[3].claim not in r.text
    if name != "none":
        assert {f.id for f in r.findings} <= {NOTEBOOK[0].id, NOTEBOOK[1].id}
        assert r.findings and r.chars == len(r.text) and r.latency_s >= 0
        everything = mem.recall("inverse barometer wind tide river", k=10)
        assert NOTEBOOK[3].id in {f.id for f in everything.findings}


@pytest.mark.parametrize("name", ALL)
def test_every_backend_persists_jsonl(name, tmp_path):
    mem = make(name, tmp_path)
    for f in NOTEBOOK:
        mem.add(f)
    rows = read_jsonl(tmp_path / f"{name}.jsonl")
    assert [f.id for f in rows] == [f.id for f in NOTEBOOK]
    assert rows[0].select_delta == -0.8 and rows[0].time == NOTEBOOK[0].time and rows[0].accepted is True


def test_none_recalls_nothing(tmp_path):
    mem = NoMemory(tmp_path / "n.jsonl")
    mem.add(NOTEBOOK[0])
    r = mem.recall("inverse barometer")
    assert r.findings == [] and r.text == "" and r.chars == 0


def test_full_context_newest_first_and_char_budget(tmp_path):
    mem = FullContextMemory(tmp_path / "f.jsonl", max_chars=10_000)
    for f in NOTEBOOK:
        mem.add(f)
    r = mem.recall("anything", k=1)                       # k ignored: the whole notebook
    assert [f.id for f in r.findings] == [f.id for f in reversed(NOTEBOOK)]
    small = FullContextMemory(tmp_path / "f.jsonl", max_chars=len(NOTEBOOK[3].text) + 10)
    r2 = small.recall("anything")
    assert [f.id for f in r2.findings] == [NOTEBOOK[3].id] and r2.chars <= small.max_chars


def test_vector_ranks_relevant_finding_first(tmp_path):
    mem = VectorMemory(tmp_path / "v.jsonl")
    for f in NOTEBOOK:
        mem.add(f)
    assert mem.recall("pressure inverse barometer hPa", k=3).findings[0].id == NOTEBOOK[0].id
    assert mem.recall("harmonics per station tide", k=3).findings[0].id == NOTEBOOK[2].id
    assert mem.recall("river discharge", k=3).findings[0].id == NOTEBOOK[3].id
    assert mem.recall("zzz qqq", k=3).findings == []
    assert len(mem.recall("the station tide wind river", k=2).findings) <= 2


def test_reopening_restores_notebook(tmp_path):
    mem = VectorMemory(tmp_path / "v.jsonl")
    for f in NOTEBOOK:
        mem.add(f)
    again = VectorMemory(tmp_path / "v.jsonl")
    assert again.recall("river discharge").findings[0].id == NOTEBOOK[3].id


def test_supersedes_hides_old_finding_once_visible(tmp_path):
    mem = FullContextMemory(tmp_path / "s.jsonl")
    old = F("Inverse barometer helps.", 0, accepted=True)
    new = F("Inverse barometer helps only at long leads.", 5, supersedes=old.id)
    mem.add(old)
    mem.add(new)
    assert [f.id for f in mem.recall("x").findings] == [new.id]
    assert [f.id for f in mem.recall("x", as_of=T + timedelta(hours=1)).findings] == [old.id]


def test_finding_roundtrip_and_text():
    f = NOTEBOOK[0]
    g = Finding.from_dict(json.loads(json.dumps(f.to_dict())))
    assert g == f
    assert "[ACCEPTED]" in f.text and "-0.8" in f.text and "Origin: closed_book" in f.text
    with pytest.raises(ValueError):
        Finding(claim="x", time=datetime(2026, 1, 1))


def test_load_findings_from_run_dir(tmp_path):
    (tmp_path / "findings.jsonl").write_text("\n".join(json.dumps(f.to_dict()) for f in NOTEBOOK[:2]) + "\n")
    assert [f.id for f in load_findings(tmp_path)] == [f.id for f in NOTEBOOK[:2]]
    (tmp_path / "memory.jsonl").write_text("\n".join(json.dumps(f.to_dict()) for f in NOTEBOOK + NOTEBOOK) + "\n")
    assert len(load_findings(tmp_path)) == 4          # memory.jsonl preferred, duplicates dropped
    assert load_findings(None) == []


# ----------------------------------------------------------------- pastdev requests


def test_pastdev_sends_documented_requests(tmp_path):
    svc = FakePastDev(polls_before_complete=2)
    mem = make("pastdev", tmp_path, svc)
    mem.add(NOTEBOOK[0])
    ingest, *polls = svc.requests
    assert ingest.method == "POST" and str(ingest.url) == "https://api.past.dev/api/v1/ingest"
    assert ingest.headers["authorization"] == "Bearer test-key"
    body = json.loads(ingest.content)
    assert set(body) == {"id", "content", "label", "timestamp", "audience"}
    assert body["id"] == NOTEBOOK[0].id and body["content"] == NOTEBOOK[0].text
    assert body["timestamp"] == to_iso(NOTEBOOK[0].time) and body["audience"] == "project"
    assert [p.method for p in polls] == ["GET"] * 3            # processing, processing, completed
    assert str(polls[0].url) == f"https://api.past.dev/api/v1/ingest/ing-{NOTEBOOK[0].id}"

    cutoff = T + timedelta(hours=2)
    r = mem.recall("inverse barometer", as_of=cutoff, k=5, identity="analyst-1")
    rec = svc.requests[-1]
    assert rec.method == "POST" and str(rec.url) == "https://api.past.dev/api/v1/recall"
    rb = json.loads(rec.content)
    assert rb["query"] == "inverse barometer" and rb["identity"] == "analyst-1"
    assert rb["level"] == "low" and rb["queryTimestamp"] == to_iso(cutoff) and rb["limit"] == 5
    assert r.findings[0].id == NOTEBOOK[0].id and NOTEBOOK[0].text in r.text


def test_pastdev_drops_results_after_as_of_even_if_service_returns_them(tmp_path):
    def lenient(request):
        if request.url.path.endswith("/recall"):
            return httpx.Response(200, json={"results": [
                {"content": "late fact", "occurredAt": to_iso(T + timedelta(days=9)),
                 "sources": [{"sourceId": "late"}]},
                {"content": "early fact", "occurredAt": to_iso(T), "sources": [{"sourceId": "early"}]}]})
        return httpx.Response(200, json={"ingestionId": "x", "status": "completed"})
    mem = PastDevMemory(tmp_path / "p.jsonl", api_key="k", transport=httpx.MockTransport(lenient))
    r = mem.recall("fact", as_of=T + timedelta(days=1))
    assert "late fact" not in r.text and "early fact" in r.text


def test_pastdev_failed_ingest_raises_after_local_copy(tmp_path):
    def failing(request):
        if request.method == "POST":
            return httpx.Response(202, json={"ingestionId": "i1", "status": "queued"})
        return httpx.Response(200, json={"status": "failed", "failure": {"code": "internal", "message": "boom"}})
    mem = PastDevMemory(tmp_path / "p.jsonl", api_key="k", transport=httpx.MockTransport(failing),
                        sleep=lambda s: None)
    with pytest.raises(PastDevError, match="internal"):
        mem.add(NOTEBOOK[0])
    assert len(read_jsonl(tmp_path / "p.jsonl")) == 1          # nothing lost


def test_pastdev_http_error_is_clear(tmp_path):
    mem = PastDevMemory(None, api_key="k",
                        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"code": "unauthorized"})))
    with pytest.raises(PastDevError, match="HTTP 401"):
        mem.recall("x")


def test_pastdev_without_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("PAST_API_KEY", raising=False)
    with pytest.raises(PastDevError, match="PAST_API_KEY"):
        PastDevMemory(tmp_path / "p.jsonl")


def test_unknown_backend():
    with pytest.raises(ValueError, match="unknown memory backend"):
        get_memory("nope")
