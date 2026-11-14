"""Literature search for the lab's scouts.

    s = get_search("openalex")                 # or "arxiv", "crossref", "exa" (EXA_API_KEY), "none"
    hits = s.search("storm surge inverse barometer effect", k=5, before=cutoff)
    s.stats()                                  # searches, failed, cache_hits, retries (per backend)

`get_search("openalex")` is a chain: OpenAlex first, then Crossref, then arXiv,
each tried when the one before fails (after retrying rate limits) or finds nothing. `search_detailed` also says
which backend produced the hits and which failed before it.

Results are cached on disk under cache/search/<backend>/; the HTTP transport can
be injected (`transport=httpx.MockTransport(...)`) for tests.
"""
from __future__ import annotations

from lab.search.backends import (ArxivSearch, CrossrefSearch, ExaSearch, NoSearch, OpenAlexSearch, parse_arxiv,
                                 rebuild_abstract, strip_jats)
from lab.search.base import DEFAULT_CACHE, ChainSearch, Hit, Searcher, SearchError, SearchOutcome, retry_after_s

BACKENDS = {
    "none": NoSearch,
    "openalex": OpenAlexSearch,
    "arxiv": ArxivSearch,
    "crossref": CrossrefSearch,
    "exa": ExaSearch,
}


# what to try next when a backend fails or finds nothing
# Crossref before arXiv: arXiv ORs the query words, so its hits are often off-topic
DEFAULT_FALLBACK = {"openalex": ("crossref", "arxiv")}


def _one(name: str, **kwargs) -> Searcher:
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise ValueError(f"unknown search backend {name!r}; choose from {sorted(BACKENDS)}") from None
    return cls(**kwargs)


def get_search(name: str, *, fallback=None, **kwargs):
    """A backend, wrapped in a ChainSearch when it has fallbacks.

    `fallback=None` uses DEFAULT_FALLBACK (openalex -> crossref -> arxiv); pass a list of
    names to choose the chain, or `()` for no fallback. `kwargs` go to every
    backend in the chain (for example `transport`, `cache_dir`, `sleep`)."""
    chain = tuple(DEFAULT_FALLBACK.get(name, ())) if fallback is None else tuple(fallback)
    chain = tuple(n for n in chain if n not in ("none", name))
    if name == "none" or not chain:
        return _one(name, **kwargs)
    return ChainSearch([_one(name, **kwargs)] + [_one(n, **kwargs) for n in chain])


def search(query: str, *, k: int = 5, before=None, backend: str = "openalex", **kwargs) -> list[Hit]:
    """One-off search with a fresh backend object (use get_search to keep the counter)."""
    return get_search(backend, **kwargs).search(query, k=k, before=before)


__all__ = ["ArxivSearch", "BACKENDS", "ChainSearch", "CrossrefSearch", "DEFAULT_CACHE", "DEFAULT_FALLBACK", "ExaSearch", "Hit",
           "NoSearch", "OpenAlexSearch", "SearchError", "SearchOutcome", "Searcher", "get_search", "parse_arxiv",
           "rebuild_abstract", "retry_after_s", "search", "strip_jats"]
