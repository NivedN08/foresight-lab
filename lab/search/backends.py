"""Search backends: none, openalex, arxiv, exa."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from lab.core.timeutil import from_iso, to_iso
from lab.search.base import Hit, Searcher, SearchError, clip


class NoSearch(Searcher):
    """Search switched off: always returns nothing and counts nothing."""

    name = "none"

    def __init__(self, **kwargs):
        kwargs["cache_dir"] = None
        super().__init__(**kwargs)

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
    """https://api.openalex.org/works?search=...&per-page=... (keyless)."""

    name = "openalex"
    URL = "https://api.openalex.org/works"

    def _fetch(self, query, k, before):
        params = {"search": query, "per-page": str(max(1, min(50, k)))}
        if before is not None:
            params["filter"] = f"to_publication_date:{_last_day_before(before)}"
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
        try:
            r = self.client.post(self.URL, json=body, headers={"x-api-key": self.api_key})
        except Exception as e:  # httpx errors
            raise SearchError(f"exa search failed: {e}") from e
        if r.status_code >= 400:
            raise SearchError(f"exa search failed: HTTP {r.status_code} {r.text[:300]}")
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
