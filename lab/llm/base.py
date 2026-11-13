"""The one interface every LLM backend implements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    """A backend could not produce a reply (bad config, HTTP error, CLI failure)."""


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    duration_s: float
    backend: str
    raw: dict[str, Any] | None = field(default=None, repr=False)


class Backend:
    name = "base"

    def complete(self, prompt: str, *, system: str | None = None, model: str | None = None,
                 max_tokens: int = 4000, temperature: float | None = None) -> LLMResponse:
        raise NotImplementedError
