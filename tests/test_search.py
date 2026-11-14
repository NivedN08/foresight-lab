"""Literature search backends against recorded replies (no network)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from lab.search import (ArxivSearch, ChainSearch, ExaSearch, NoSearch, OpenAlexSearch, SearchError, get_search,
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
    assert s.stats() == {"backend": "openalex", "searches": 1, "failed": 0, "cache_hits": 1, "retries": 0}
    assert a[0].backend == "openalex" and b[0].backend == "openalex"
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
    assert s.search_detailed(QUERY).backend is None
    with pytest.raises(ValueError, match="unknown search backend"):
        get_search("bing")


# ----------------------------------------------------------------- rate limits and fallback

RATE_LIMITED = {"error": "Rate limit exceeded",
                "message": "Anonymous search is temporarily rate-limited while the search cluster is under "
                           "elevated load. Please retry in 14s, or use a free API key"}


class Sequence429:
    """Answers 429 `n` times, then the recorded reply."""

    def __init__(self, n, reply=OPENALEX_REPLY, headers=None, body=RATE_LIMITED):
        self.n, self.reply, self.headers, self.body = n, reply, headers or {}, body
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if len(self.requests) <= self.n:
            return httpx.Response(429, json=self.body, headers=self.headers)
        return httpx.Response(200, json=self.reply)


def test_429_waits_for_hint_and_retries():
    slept = []
    t = Sequence429(2)
    s = OpenAlexSearch(transport=httpx.MockTransport(t), cache_dir=None, sleep=slept.append)
    hits = s.search(QUERY)
    assert len(hits) == 2 and slept == [14.0, 14.0] and len(t.requests) == 3
    assert s.stats() == {"backend": "openalex", "searches": 1, "failed": 0, "cache_hits": 0, "retries": 2}


def test_429_wait_is_capped_and_retry_after_header_wins():
    slept = []
    s = OpenAlexSearch(transport=httpx.MockTransport(Sequence429(1, headers={"Retry-After": "90"})),
                       cache_dir=None, sleep=slept.append)
    s.search(QUERY)
    assert slept == [20.0]
    slept.clear()
    s2 = OpenAlexSearch(transport=httpx.MockTransport(Sequence429(1, body={"message": "slow down"})),
                        cache_dir=None, sleep=slept.append)
    s2.search(QUERY)
    assert slept == [5.0]                                  # no hint: default wait


def test_429_three_times_fails_and_counts_failure(tmp_path):
    slept = []
    t = Sequence429(3)
    s = OpenAlexSearch(transport=httpx.MockTransport(t), cache_dir=tmp_path, sleep=slept.append)
    with pytest.raises(SearchError, match="HTTP 429"):
        s.search(QUERY)
    assert len(t.requests) == 3 and len(slept) == 2
    assert s.stats() == {"backend": "openalex", "searches": 1, "failed": 1, "cache_hits": 0, "retries": 2}
    assert not list(tmp_path.rglob("*.json"))              # failures are not cached


def test_chain_falls_back_to_arxiv_on_failure():
    oa = OpenAlexSearch(transport=httpx.MockTransport(Sequence429(99)), cache_dir=None, sleep=lambda s: None)
    ax = ArxivSearch(transport=httpx.MockTransport(Recorder(ARXIV_REPLY)), cache_dir=None)
    chain = ChainSearch([oa, ax])
    out = chain.search_detailed(QUERY, k=5)
    assert out.backend == "arxiv" and len(out.hits) == 2 and all(h.backend == "arxiv" for h in out.hits)
    assert out.failures[0]["backend"] == "openalex" and "429" in out.failures[0]["error"]
    st = chain.stats()
    assert st["chain"] == ["openalex", "arxiv"] and st["queries"] == 1 and st["queries_without_hits"] == 0
    assert st["searches"] == 2 and st["failed"] == 1 and st["retries"] == 2
    assert st["by_backend"]["openalex"]["failed"] == 1 and st["by_backend"]["arxiv"]["failed"] == 0


def test_chain_falls_back_on_empty_and_reports_nothing_found():
    empty = OpenAlexSearch(transport=httpx.MockTransport(Recorder({"results": []})), cache_dir=None)
    ax = ArxivSearch(transport=httpx.MockTransport(Recorder(ARXIV_REPLY)), cache_dir=None)
    out = ChainSearch([empty, ax]).search_detailed(QUERY)
    assert out.backend == "arxiv" and out.failures == [{"backend": "openalex", "error": "no hits"}]
    ax_empty = ArxivSearch(transport=httpx.MockTransport(Recorder(ARXIV_REPLY.split("<entry>")[0] + "</feed>")),
                           cache_dir=None)
    chain = ChainSearch([empty, ax_empty])
    none = chain.search_detailed(QUERY)
    assert none.hits == [] and none.backend is None and chain.stats()["queries_without_hits"] == 1


def test_get_search_default_chain():
    s = get_search("openalex", cache_dir=None)
    assert isinstance(s, ChainSearch) and s.name == "openalex"
    assert [b.name for b in s.backends] == ["openalex", "crossref", "arxiv"]
    assert isinstance(get_search("openalex", fallback=(), cache_dir=None), OpenAlexSearch)
    assert [b.name for b in get_search("arxiv", fallback=["openalex"], cache_dir=None).backends] == ["arxiv", "openalex"]
    assert isinstance(get_search("arxiv", cache_dir=None), ArxivSearch)


def test_openalex_key_goes_in_header_not_url(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "oa-secret")
    monkeypatch.setenv("OPENALEX_MAILTO", "lab@example.org")
    rec = Recorder(OPENALEX_REPLY)
    OpenAlexSearch(transport=httpx.MockTransport(rec), cache_dir=None).search(QUERY)
    req = rec.requests[0]
    assert req.headers["authorization"] == "Bearer oa-secret"
    assert "oa-secret" not in str(req.url) and req.url.params["mailto"] == "lab@example.org"
    monkeypatch.delenv("OPENALEX_API_KEY")
    monkeypatch.delenv("OPENALEX_MAILTO")
    rec2 = Recorder(OPENALEX_REPLY)
    OpenAlexSearch(transport=httpx.MockTransport(rec2), cache_dir=None).search(QUERY)
    assert "authorization" not in rec2.requests[0].headers and "mailto" not in rec2.requests[0].url.params


def test_old_cache_files_without_backend_still_load(tmp_path):
    s = OpenAlexSearch(transport=httpx.MockTransport(Recorder(OPENALEX_REPLY)), cache_dir=tmp_path)
    path = tmp_path / "openalex" / f"{s._key(QUERY, 5, None)}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hits": [{"title": "t", "url": "u", "year": 2020, "text": "x"}]}))
    out = s.search_detailed(QUERY, k=5)
    assert out.from_cache and out.hits[0].backend == "openalex" and s.searches == 0


def test_exa_retries_429_too():
    slept = []
    s = ExaSearch(api_key="k", transport=httpx.MockTransport(Sequence429(1, reply=EXA_REPLY)), cache_dir=None,
                  sleep=slept.append)
    assert len(s.search(QUERY)) == 2 and slept == [14.0]


# --- Crossref, request spacing, daily limits ---

CROSSREF_REPLY = {"status": "ok", "message": {"items": [
    {"title": ["Storm surge forecasts from a global tide-gauge network"], "DOI": "10.1000/surge",
     "abstract": "<jats:title>Abstract</jats:title>\n <jats:p>Surge follows   pressure and <jats:italic>wind</jats:italic>.</jats:p>",
     "issued": {"date-parts": [[2021, 5, 4]]}},
    {"title": ["Year only"], "DOI": "10.1000/year", "abstract": "<p>Abstract: Shelf waves.</p>",
     "issued": {"date-parts": [[2022]]}},
    {"title": ["Too late"], "DOI": "10.1000/late", "abstract": "<p>Later work.</p>",
     "issued": {"date-parts": [[2023, 2]]}},
    {"title": ["No abstract"], "DOI": "10.1000/none", "issued": {"date-parts": [[2020]]}}]}}


def test_crossref_parses_recorded_reply_and_filters_by_date():
    from lab.search import CrossrefSearch, strip_jats
    rec = Recorder(CROSSREF_REPLY)
    s = CrossrefSearch(transport=httpx.MockTransport(rec), cache_dir=None)
    hits = s.search(QUERY, k=5, before=datetime(2022, 6, 1, tzinfo=timezone.utc))
    assert [h.url for h in hits] == ["https://doi.org/10.1000/surge", "https://doi.org/10.1000/year"]
    assert hits[0].text == "Surge follows pressure and wind." and hits[0].year == 2021 and hits[0].backend == "crossref"
    assert hits[1].text == "Shelf waves." and hits[1].year == 2022
    q = rec.requests[0].url.params
    assert q["filter"] == "has-abstract:true,until-pub-date:2022-05-31" and q["rows"] == "5"
    assert "mailto" not in q                              # nothing that identifies the user is sent
    assert strip_jats(None) == ""
    assert get_search("crossref", cache_dir=None).name == "crossref"


def test_daily_limit_is_not_retried_and_switches_the_backend_off(tmp_path):
    body = {"error": "Rate limit exceeded", "message": "Insufficient budget. The free daily budget is used up; "
                                                       "resets at midnight UTC."}
    slept, now = [], [1000.0]
    t = Sequence429(99, body=body)
    s = OpenAlexSearch(transport=httpx.MockTransport(t), cache_dir=tmp_path, sleep=slept.append, clock=lambda: now[0])
    with pytest.raises(SearchError, match="daily limit used up"):
        s.search(QUERY)
    assert len(t.requests) == 1 and slept == []            # no waiting, no retry
    with pytest.raises(SearchError, match="skipped"):
        s.search("another query")
    assert len(t.requests) == 1                            # switched off: no request made
    now[0] += 1801
    with pytest.raises(SearchError, match="daily limit used up"):
        s.search("a third query")
    assert len(t.requests) == 2                            # tried again after the pause
    assert s.stats()["failed"] == 3 and s.stats()["retries"] == 0


def test_requests_are_spaced_across_instances_sharing_a_cache(tmp_path):
    slept, now = [], [500.0]

    def sleep(sec):
        slept.append(round(sec, 3))
        now[0] += sec

    def make():
        return ArxivSearch(transport=httpx.MockTransport(Recorder(ARXIV_REPLY)), cache_dir=tmp_path,
                           sleep=sleep, clock=lambda: now[0])
    a, b = make(), make()
    a.search("first")
    assert slept == []                                     # nothing to wait for on the first request
    now[0] += 1.0
    b.search("second")                                     # another instance, same cache directory
    assert slept == [2.0]
    now[0] += 10.0
    a.search("third")
    assert slept == [2.0]                                  # long enough since the last one
    assert ArxivSearch.min_interval_s == 3.0 and OpenAlexSearch.min_interval_s == 0.0


def test_chain_reaches_crossref_when_the_first_two_fail():
    from lab.search import CrossrefSearch
    oa = OpenAlexSearch(transport=httpx.MockTransport(Sequence429(99)), cache_dir=None, sleep=lambda s: None)
    ax = ArxivSearch(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="busy")), cache_dir=None)
    cr = CrossrefSearch(transport=httpx.MockTransport(Recorder(CROSSREF_REPLY)), cache_dir=None)
    out = ChainSearch([oa, ax, cr]).search_detailed(QUERY, k=5)
    assert out.backend == "crossref" and len(out.hits) == 3
    assert [f["backend"] for f in out.failures] == ["openalex", "arxiv"]
