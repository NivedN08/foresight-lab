"""Search backends: none, openalex, arxiv, crossref, exa."""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from lab.core.timeutil import from_iso, to_iso
from lab.search.base import Hit, Searcher, SearchError, SearchOutcome, clip


class NoSearch(Searcher):
    """Search switched off: always returns nothing and counts nothing."""

    name = "none"

    def __init__(self, **kwargs):
        kwargs["cache_dir"] = None
        super().__init__(**kwargs)

    def search_detailed(self, query, *, k=5, before=None):
        return SearchOutcome([], None)

    def search(self, query, *, k=5, before=None):
        return []


def rebuild_abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; put the words back in order."""
    if not inverted:
        return ""
    pos = {}
    for word, places in inverted.items():
        for p in places:
            pos[p] = word
    return " ".join(pos[i] for i in sorted(pos))


def _last_day_before(before: datetime) -> str:
    """Inclusive date filter that keeps everything strictly before `before`."""
    return (before - timedelta(microseconds=1)).date().isoformat()


class OpenAlexSearch(Searcher):
    """https://api.openalex.org/works?search=...&per-page=...

    Works without a key, but anonymous search is rate-limited (HTTP 429). With
    OPENALEX_API_KEY set, the key is sent as `Authorization: Bearer <key>` (one of
    the two ways OpenAlex documents; a header keeps it out of URLs and logs).
    OPENALEX_MAILTO, if set, is sent as the `mailto` parameter; current OpenAlex
    docs no longer mention it, so it is harmless at worst."""

    name = "openalex"
    URL = "https://api.openalex.org/works"

    def __init__(self, *, api_key: str | None = None, mailto: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY") or None
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO") or None
        if self.api_key:
            self.client.headers["Authorization"] = f"Bearer {self.api_key}"

    def _fetch(self, query, k, before):
        params = {"search": query, "per-page": str(max(1, min(50, k)))}
        if before is not None:
            params["filter"] = f"to_publication_date:{_last_day_before(before)}"
        if self.mailto:
            params["mailto"] = self.mailto
        data = self._get(self.URL, params=params).json()
        hits = []
        for w in data.get("results") or []:
            loc = w.get("primary_location") or {}
            url = w.get("doi") or loc.get("landing_page_url") or w.get("id") or ""
            text = rebuild_abstract(w.get("abstract_inverted_index"))
            hits.append(Hit(title=clip(w.get("display_name") or w.get("title") or "", 300), url=url,
                            year=w.get("publication_year"), text=clip(text)))
        return hits


_ATOM = {"a": "http://www.w3.org/2005/Atom"}


class ArxivSearch(Searcher):
    """export.arxiv.org/api/query?search_query=all:... (keyless, Atom XML). The
    plain-http address answers with a redirect to https, so https is used
    directly. arXiv ORs the words and sorts by relevance."""

    name = "arxiv"
    URL = "https://export.arxiv.org/api/query"
    min_interval_s = 3.0     # arXiv asks for three seconds between requests; faster ones time out or get 429

    def _fetch(self, query, k, before):
        q = f"all:{query}"
        if before is not None:
            end = (before - timedelta(minutes=1)).strftime("%Y%m%d%H%M")
            q = f"({q}) AND submittedDate:[190001010000 TO {end}]"
        params = {"search_query": q, "start": "0", "max_results": str(max(1, min(50, k))),
                  "sortBy": "relevance"}
        return parse_arxiv(self._get(self.URL, params=params).text, before)


def parse_arxiv(xml_text: str, before: datetime | None = None) -> list[Hit]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SearchError(f"arxiv: unreadable reply ({e})") from e
    hits = []
    for e in root.findall("a:entry", _ATOM):
        published = (e.findtext("a:published", default="", namespaces=_ATOM) or "").strip()
        when = from_iso(published) if published else None
        if before is not None and when is not None and when >= before:
            continue
        url = (e.findtext("a:id", default="", namespaces=_ATOM) or "").strip()
        title = e.findtext("a:title", default="", namespaces=_ATOM) or ""
        summary = e.findtext("a:summary", default="", namespaces=_ATOM) or ""
        if not url or title.strip().lower() == "error":
            continue
        hits.append(Hit(title=clip(title, 300), url=url, year=when.year if when else None,
                        text=clip(summary)))
    return hits


_TAG = re.compile(r"<[^>]+>")


def strip_jats(text: str | None) -> str:
    """Crossref abstracts are JATS XML; keep the words and drop a leading 'Abstract' heading."""
    plain = " ".join(_TAG.sub(" ", text or "").split())
    plain = re.sub(r"\s+([.,;:)])", r"\1", plain)      # a closing tag before punctuation leaves a gap
    return re.sub(r"^abstract\b[:.]?\s*", "", plain, flags=re.I)


def _issued(work: dict) -> tuple[int, ...]:
    parts = ((work.get("issued") or {}).get("date-parts") or [[]])[0] or []
    return tuple(int(p) for p in parts if p is not None)


class CrossrefSearch(Searcher):
    """https://api.crossref.org/works?query=... (keyless). Journal articles and
    preprints registered with Crossref; only works that carry an abstract are
    asked for, since the scouts need text to read. Dates can be a bare year or
    year-month; a work is dropped only when its date is certainly not before
    `before` (the API filter has already applied the same cut by day)."""

    name = "crossref"
    URL = "https://api.crossref.org/works"
    min_interval_s = 0.5

    def _fetch(self, query, k, before):
        filters = ["has-abstract:true"]
        if before is not None:
            filters.append(f"until-pub-date:{_last_day_before(before)}")
        params = {"query": query, "rows": str(max(1, min(50, k))), "filter": ",".join(filters),
                  "select": "title,abstract,DOI,URL,issued"}
        items = (self._get(self.URL, params=params).json().get("message") or {}).get("items") or []
        hits = []
        for w in items:
            date = _issued(w)
            if before is not None and date:
                cut = (before.year, before.month, before.day)[: len(date)]
                if date > cut:
                    continue
            title = " ".join(w.get("title") or [])
            url = f"https://doi.org/{w['DOI']}" if w.get("DOI") else (w.get("URL") or "")
            text = strip_jats(w.get("abstract"))
            if not title or not url or not text:
                continue
            hits.append(Hit(title=clip(title, 300), url=url, year=date[0] if date else None, text=clip(text)))
        return hits


class ExaSearch(Searcher):
    """POST https://api.exa.ai/search with x-api-key (EXA_API_KEY)."""

    name = "exa"
    URL = "https://api.exa.ai/search"

    def __init__(self, *, api_key: str | None = None, max_characters: int = 1500, **kwargs):
        api_key = api_key or os.environ.get("EXA_API_KEY")
        if not api_key:
            raise SearchError("exa search needs an API key: set EXA_API_KEY (or pass api_key=...)")
        super().__init__(**kwargs)
        self.api_key = api_key
        self.max_characters = max_characters

    def _fetch(self, query, k, before):
        body = {"query": query, "numResults": max(1, min(100, k)), "type": "auto",
                "contents": {"highlights": True, "text": {"maxCharacters": self.max_characters}}}
        if before is not None:
            body["endPublishedDate"] = to_iso(before - timedelta(seconds=1))
        r = self._request("POST", self.URL, json=body, headers={"x-api-key": self.api_key})
        hits = []
        for res in r.json().get("results") or []:
            published = res.get("publishedDate")
            year = None
            if published:
                try:
                    year = from_iso(published).year
                except ValueError:
                    year = None
            highlights = " … ".join(res.get("highlights") or [])
            text = highlights or res.get("text") or ""
            hits.append(Hit(title=clip(res.get("title") or "", 300), url=res.get("url") or "",
                            year=year, text=clip(text)))
        return hits
