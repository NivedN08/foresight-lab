"""Literature search for the lab's scouts.

    s = get_search("openalex")                 # or "arxiv", "exa" (EXA_API_KEY), "none"
    hits = s.search("storm surge inverse barometer effect", k=5, before=cutoff)
    s.stats()                                  # {"searches": real calls, "cache_hits": ...}

Results are cached on disk under cache/search/<backend>/; the HTTP transport can
be injected (`transport=httpx.MockTransport(...)`) for tests.
"""
from __future__ import annotations

from lab.search.backends import ArxivSearch, ExaSearch, NoSearch, OpenAlexSearch, parse_arxiv, rebuild_abstract
from lab.search.base import DEFAULT_CACHE, Hit, Searcher, SearchError

BACKENDS = {
    "none": NoSearch,
    "openalex": OpenAlexSearch,
    "arxiv": ArxivSearch,
    "exa": ExaSearch,
}


def get_search(name: str, **kwargs) -> Searcher:
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise ValueError(f"unknown search backend {name!r}; choose from {sorted(BACKENDS)}") from None
    return cls(**kwargs)


def search(query: str, *, k: int = 5, before=None, backend: str = "openalex", **kwargs) -> list[Hit]:
    """One-off search with a fresh backend object (use get_search to keep the counter)."""
    return get_search(backend, **kwargs).search(query, k=k, before=before)


__all__ = ["ArxivSearch", "BACKENDS", "DEFAULT_CACHE", "ExaSearch", "Hit", "NoSearch", "OpenAlexSearch",
           "SearchError", "Searcher", "get_search", "parse_arxiv", "rebuild_abstract", "search"]
