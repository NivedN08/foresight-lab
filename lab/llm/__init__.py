"""One small LLM interface over several providers.

    from lab.llm import complete, get_backend, Budget, extract_code
    r = complete("Reply with the single word: ok")          # claude_cli by default
    r = get_backend("fake", replies={"hi": "hello"}).complete("hi")

The default backend is `claude_cli` unless LAB_LLM_BACKEND names another.
"""
from __future__ import annotations

import os

from lab.llm.base import Backend, LLMError, LLMResponse
from lab.llm.budget import Budget
from lab.llm.claude_cli import ClaudeCLIBackend
from lab.llm.extract import extract_code
from lab.llm.fake import FakeBackend
from lab.llm.http_backends import AnthropicBackend, OpenAIBackend

BACKENDS = {
    "claude_cli": ClaudeCLIBackend,
    "openai": OpenAIBackend,
    "anthropic": AnthropicBackend,
    "fake": FakeBackend,
}


def get_backend(name: str | None = None, **kwargs) -> Backend:
    name = name or os.environ.get("LAB_LLM_BACKEND") or "claude_cli"
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise LLMError(f"unknown LLM backend {name!r}; choose from {sorted(BACKENDS)}") from None
    return cls(**kwargs)


def complete(prompt: str, *, system: str | None = None, model: str | None = None,
             max_tokens: int = 4000, temperature: float | None = None,
             backend: str | Backend | None = None, budget: Budget | None = None,
             label: str = "default") -> LLMResponse:
    b = backend if isinstance(backend, Backend) else get_backend(backend)
    response = b.complete(prompt, system=system, model=model, max_tokens=max_tokens,
                          temperature=temperature)
    if budget is not None:
        budget.record(label, response)
    return response


__all__ = ["AnthropicBackend", "BACKENDS", "Backend", "Budget", "ClaudeCLIBackend", "FakeBackend",
           "LLMError", "LLMResponse", "OpenAIBackend", "complete", "extract_code", "get_backend"]
