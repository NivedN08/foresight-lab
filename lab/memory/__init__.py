"""The shared lab notebook. One interface, four interchangeable backends:

    none          records findings, recalls nothing
    full_context  every finding up to as_of, newest first, cut at a character budget
    vector        TF-IDF cosine similarity (numpy only)
    pastdev       the past.dev memory API (needs PAST_API_KEY)

    mem = get_memory("vector", path=run_dir / "memory.jsonl")
    mem.add(Finding(claim="...", problem="coast_water", generation=1))
    mem.recall("tide residual by lead", as_of=some_utc_datetime, k=8)
"""
from __future__ import annotations

from lab.memory.base import Finding, LocalMemory, Memory, RecallResult, load_findings, read_jsonl
from lab.memory.local import FullContextMemory, NoMemory, VectorMemory, tokenize
from lab.memory.pastdev import PastDevError, PastDevMemory

BACKENDS = {
    "none": NoMemory,
    "full_context": FullContextMemory,
    "vector": VectorMemory,
    "pastdev": PastDevMemory,
}


def get_memory(name: str, path=None, **kwargs) -> Memory:
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise ValueError(f"unknown memory backend {name!r}; choose from {sorted(BACKENDS)}") from None
    return cls(path, **kwargs)


__all__ = ["BACKENDS", "Finding", "FullContextMemory", "LocalMemory", "Memory", "NoMemory",
           "PastDevError", "PastDevMemory", "RecallResult", "VectorMemory", "get_memory",
           "load_findings", "read_jsonl", "tokenize"]
