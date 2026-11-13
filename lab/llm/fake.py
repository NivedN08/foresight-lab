"""Deterministic canned replies for tests."""
from __future__ import annotations

import time

from lab.llm.base import Backend, LLMResponse


class FakeBackend(Backend):
    """Replies with the first entry of `replies` whose key is a substring of the
    prompt (checked in insertion order), else `default`. Token counts are word
    counts, cost is zero, and every call is recorded in `calls`."""

    name = "fake"

    def __init__(self, replies: dict[str, str] | None = None, default: str = "ok",
                 model: str = "fake-model"):
        self.replies = dict(replies or {})
        self.default = default
        self.model = model
        self.calls: list[dict] = []

    def complete(self, prompt, *, system=None, model=None, max_tokens=4000, temperature=None):
        t0 = time.monotonic()
        self.calls.append({"prompt": prompt, "system": system, "model": model,
                           "max_tokens": max_tokens, "temperature": temperature})
        text = next((r for k, r in self.replies.items() if k in prompt), self.default)
        return LLMResponse(text=text, model=model or self.model,
                           input_tokens=len(prompt.split()) + len((system or "").split()),
                           output_tokens=len(text.split()), cost_usd=0.0,
                           duration_s=time.monotonic() - t0, backend=self.name)
