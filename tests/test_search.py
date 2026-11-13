"""Literature search backends against recorded replies (no network)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from lab.search import (ArxivSearch, ExaSearch, NoSearch, OpenAlexSearch, SearchError, get_search,
                        parse_arxiv, rebuild_abstract)

QUERY = "storm surge inverse barometer effect"

# Trimmed from the real OpenAlex reply to QUERY on 2026-11-13 (2 of 5 works; abstract cut at 25 words).
OPENALEX_REPLY = {"meta": {"count": 833, "per_page": 5}, "results": [
    {"id": "https://openalex.org/W7130548000", "doi": "https://doi.org/10.1016/j.ocemod.2026.102710",
     "display_name": "Diagnostic vs dynamic representation of the inverse barometer effect in a global ocean "
                     "model and its potential for probabilistic storm surge forecasting",
     "publication_year": 2026,
     "primary_location": {"landing_page_url": "https://doi.org/10.1016/j.ocemod.2026.102710"},
     "abstract_inverted_index": {
         "The": [0], "global": [1, 20], "ocean": [2], "model": [3], "NEMO": [4], "is": [5], "run": [6],
         "in": [7], "a": [8], "series": [9], "of": [10], "stand-alone": [11], "configurations": [12],
         "(2015–2022)": [13], "to": [14], "investigate": [15], "the": [16], "potential": [17],
         "for": [18], "improving": [19], "medium-range": [21], "storm": [22], "surge": [23],
         "forecasts": [24]}},
    {"id": "https://openalex.org/W4252902832", "doi": "https://doi.org/10.1007/978-3-319-93806-6_298",
     "display_name": "Storm Surge", "publication_year": 2019,
     "primary_location": {"landing_page_url": "https://doi.org/10.1007/978-3-319-93806-6_298"},
     "abstract_inverted_index": None}]}

# Trimmed from the real arXiv reply to QUERY on 2026-11-13 (2 of 5 entries; summaries cut, authors dropped).
ARXIV_REPLY = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <id>https://arxiv.org/api/N2jQc34O/5SOzBPUGR3PV13R1Ek</id>
  <title>arXiv Query: search_query=all:storm OR all:surge OR all:inverse OR all:barometer OR all:effect&amp;id_list=&amp;start=0&amp;max_results=5</title>
  <updated>2026-11-13T22:45:27Z</updated>
  <opensearch:itemsPerPage>5</opensearch:itemsPerPage>
  <opensearch:totalResults>779291</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <entry>
    <id>http://arxiv.org/abs/2510.25481v2</id>
    <title>Diagnostic vs dynamic representation of the inverse barometer effect in a global ocean model and its potential for probabilistic storm surge forecasting</title>
    <updated>2026-01-23T09:10:41Z</updated>
    <link href="https://arxiv.org/abs/2510.25481v2" rel="alternate" type="text/html"/>
    <summary>The global ocean model NEMO is run in a series of stand-alone configurations (2015-2022) to investigate the potential for improving global medium-range storm surge forecasts by including the inverse barometer effect.</summary>
    <category term="physics.ao-ph" scheme="http://arxiv.org/schemas/atom"/>
    <published>2025-10-29T13:04:25Z</published>
    <arxiv:primary_category term="physics.ao-ph"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2506.09078v3</id>
    <title>The spatially variable effects of mangroves on flood depths and losses from storm surges in Florida</title>
    <updated>2025-10-14T16:18:18Z</updated>
    <summary>Mangroves modify storm surges with impacts on property damages, but these effects vary spatially and by storm intensity.</summary>
    <published>2025-06-10T02:57:38Z</published>
  </entry>
</feed>"""

EXA_REPLY = {"requestId": "r1", "results": [
    {"title": "Inverse barometer correction", "url": "https://example.org/ib", "publishedDate": "2021-05-04T00:00:00.000Z",
     "text": "Full text here.", "highlights": ["Sea level rises about 1 cm per hPa drop.", "Applies at long periods."]},
    {"title": "No date", "url": "https://example.org/nd", "text": "Only text."}]}


class Recorder:
    def __init__(self, reply):
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        if isinstance(self.reply, str):
            return httpx.Response(200, text=self.reply, headers={"content-type": "application/atom+xml"})
        return httpx.Response(200, json=self.reply)


def test_openalex_parses_recorded_reply(tmp_path):
    rec = Recorder(OPENALEX_REPLY)
    s = OpenAlexSearch(transport=httpx.MockTransport(rec), cache_dir=tmp_path)
    hits = s.search(QUERY, k=5)
    assert len(hits) == 2
    assert hits[0].title.startswith("Diagnostic vs dynamic representation of the inverse barometer")
    assert hits[0].url == "https://doi.org/10.1016/j.ocemod.2026.102710" and hits[0].year == 2026
    assert hits[0].text.startswith("The global ocean model NEMO is run in a series")
    assert hits[0].text.endswith("medium-range storm surge forecasts")
    assert hits[1].text == "" and hits[1].year == 2019
    req = rec.requests[0]
    assert req.url.host == "api.openalex.org" and req.url.path == "/works"
    assert req.url.params["search"] == QUERY and req.url.params["per-page"] == "5"
    assert "filter" not in req.url.params


def test_openalex_before_filter(tmp_path):
    rec = Recorder(OPENALEX_REPLY)
    s = OpenAlexSearch(transport=httpx.MockTransport(rec), cache_dir=None)
    s.search(QUERY, k=3, before=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert rec.requests[0].url.params["filter"] == "to_publication_date:2023-12-31"


def test_rebuild_abstract():
    assert rebuild_abstract({"b": [1], "a": [0, 2]}) == "a b a"
    assert rebuild_abstract(None) == ""


def test_arxiv_parses_recorded_reply(tmp_path):
    rec = Recorder(ARXIV_REPLY)
    s = ArxivSearch(transport=httpx.MockTransport(rec), cache_dir=tmp_path)
    hits = s.search(QUERY, k=5)
    assert [h.url for h in hits] == ["http://arxiv.org/abs/2510.25481v2", "http://arxiv.org/abs/2506.09078v3"]
    assert hits[0].year == 2025 and "inverse barometer" in hits[0].title
    assert hits[1].text.startswith("Mangroves modify storm surges")
    req = rec.requests[0]
    assert req.url.host == "export.arxiv.org" and req.url.params["search_query"] == f"all:{QUERY}"
    assert req.url.params["max_results"] == "5"


def test_arxiv_before_filters_query_and_results():
    rec = Recorder(ARXIV_REPLY)
    s = ArxivSearch(transport=httpx.MockTransport(rec), cache_dir=None)
    hits = s.search(QUERY, k=5, before=datetime(2025, 9, 1, tzinfo=timezone.utc))
    assert [h.year for h in hits] == [2025] and "mangroves" in hits[0].title
    assert "submittedDate:[190001010000 TO 202508312359]" in rec.requests[0].url.params["search_query"]
    assert parse_arxiv(ARXIV_REPLY, datetime(2020, 1, 1, tzinfo=timezone.utc)) == []
    with pytest.raises(SearchError):
        parse_arxiv("<not xml")


def test_exa_sends_documented_request(tmp_path):
    rec = Recorder(EXA_REPLY)
    s = ExaSearch(api_key="exa-test", transport=httpx.MockTransport(rec), cache_dir=tmp_path, max_characters=800)
    hits = s.search(QUERY, k=4, before=datetime(2024, 6, 1, tzinfo=timezone.utc))
    req = rec.requests[0]
    assert req.method == "POST" and str(req.url) == "https://api.exa.ai/search"
    assert req.headers["x-api-key"] == "exa-test"
    body = json.loads(req.content)
    assert body == {"query": QUERY, "numResults": 4, "type": "auto",
                    "contents": {"highlights": True, "text": {"maxCharacters": 800}},
                    "endPublishedDate": "2024-05-31T23:59:59Z"}
    assert hits[0].year == 2021 and "1 cm per hPa" in hits[0].text
    assert hits[1].year is None and hits[1].text == "Only text."


def test_exa_without_key_raises(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(SearchError, match="EXA_API_KEY"):
        ExaSearch()


def test_cache_and_counter(tmp_path):
    rec = Recorder(OPENALEX_REPLY)
    s = OpenAlexSearch(transport=httpx.MockTransport(rec), cache_dir=tmp_path)
    a = s.search(QUERY, k=5)
    b = s.search(QUERY, k=5)
    assert a == b and len(rec.requests) == 1
    assert s.stats() == {"backend": "openalex", "searches": 1, "cache_hits": 1}
    assert len(list((tmp_path / "openalex").glob("*.json"))) == 1
    s.search(QUERY, k=3)                                   # different k: a new search
    assert s.searches == 2
    # a fresh object reads the same cache from disk
    s2 = OpenAlexSearch(transport=httpx.MockTransport(rec), cache_dir=tmp_path)
    s2.search(QUERY, k=5)
    assert s2.stats()["cache_hits"] == 1 and len(rec.requests) == 2


def test_http_error_is_clear():
    s = OpenAlexSearch(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="busy")), cache_dir=None)
    with pytest.raises(SearchError, match="HTTP 503"):
        s.search(QUERY)


def test_none_backend_and_registry():
    s = get_search("none")
    assert isinstance(s, NoSearch) and s.search(QUERY) == [] and s.stats()["searches"] == 0
    with pytest.raises(ValueError, match="unknown search backend"):
        get_search("bing")
