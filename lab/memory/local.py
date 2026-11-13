"""Backends that keep the notebook on this machine: none, full_context, vector."""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from datetime import datetime

import numpy as np

from lab.memory.base import Finding, LocalMemory, RecallResult, render_block


class NoMemory(LocalMemory):
    """Records findings (to the JSONL file) but recalls nothing: the lab without a notebook."""

    name = "none"

    def recall(self, query: str, *, as_of: datetime | None = None, k: int = 8,
               identity: str = "lab") -> RecallResult:
        t0 = time.perf_counter()
        return RecallResult([], "", time.perf_counter() - t0, 0)


class FullContextMemory(LocalMemory):
    """Everything up to `as_of`, newest first, cut at `max_chars`. `k` is ignored:
    this is the "paste the whole notebook" baseline."""

    name = "full_context"

    def __init__(self, path=None, max_chars: int = 12_000):
        super().__init__(path)
        self.max_chars = max_chars

    def recall(self, query: str, *, as_of: datetime | None = None, k: int = 8,
               identity: str = "lab") -> RecallResult:
        t0 = time.perf_counter()
        items = sorted(self.visible(as_of), key=lambda f: f.time, reverse=True)
        kept, used = [], 0
        for f in items:
            cost = len(f.text) + 3
            if used + cost > self.max_chars:
                break
            kept.append(f)
            used += cost
        text = render_block(kept)
        return RecallResult(kept, text, time.perf_counter() - t0, len(text))


_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = frozenset("""a an and are as at be by for from has have in is it its of on or that the
this to was were with not did does do no yes into than then their there these those which""".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


class VectorMemory(LocalMemory):
    """TF-IDF over each finding's claim + text + source title, cosine similarity,
    top `k` with a positive score (ties: newer first). Plain numpy; the matrix is
    rebuilt on every recall over the findings visible at `as_of`, which is cheap
    at notebook sizes (hundreds of findings)."""

    name = "vector"

    @staticmethod
    def _doc(f: Finding) -> str:
        return f"{f.claim} {f.text} {f.source_title}"

    def recall(self, query: str, *, as_of: datetime | None = None, k: int = 8,
               identity: str = "lab") -> RecallResult:
        t0 = time.perf_counter()
        items = self.visible(as_of)
        q = Counter(tokenize(query))
        if not items or not q:
            return RecallResult([], "", time.perf_counter() - t0, 0)
        docs = [Counter(tokenize(self._doc(f))) for f in items]
        vocab = {t: i for i, t in enumerate(sorted(set().union(*docs) | set(q)))}
        n = len(docs)
        df = np.zeros(len(vocab))
        for d in docs:
            for t in d:
                df[vocab[t]] += 1
        idf = np.log((1 + n) / (1 + df)) + 1.0

        def vec(counts: Counter) -> np.ndarray:
            v = np.zeros(len(vocab))
            for t, c in counts.items():
                v[vocab[t]] = 1.0 + math.log(c)
            v *= idf
            norm = np.linalg.norm(v)
            return v / norm if norm > 0 else v

        mat = np.vstack([vec(d) for d in docs])
        scores = mat @ vec(q)
        order = sorted(range(n), key=lambda i: (-scores[i], -items[i].time.timestamp()))
        kept = [items[i] for i in order[:k] if scores[i] > 0]
        text = render_block(kept)
        return RecallResult(kept, text, time.perf_counter() - t0, len(text))
