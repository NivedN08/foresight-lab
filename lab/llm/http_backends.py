"""HTTP backends via httpx: OpenAI-compatible Chat Completions and the
Anthropic Messages API. Pass `transport=httpx.MockTransport(...)` in tests."""
from __future__ import annotations

import os
import time

import httpx

from lab.llm.base import Backend, LLMError, LLMResponse


class OpenAIBackend(Backend):
    """Chat Completions API. Works with any OpenAI-compatible server (including
    Tinker's) via OPENAI_BASE_URL. Key from OPENAI_API_KEY; model from the call,
    else OPENAI_MODEL, else `default_model`."""

    name = "openai"
    default_model = "gpt-4o-mini"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 timeout_s: float = 300, transport: httpx.BaseTransport | None = None,
                 env: dict | None = None):
        env = os.environ if env is None else env
        self.api_key = api_key or env.get("OPENAI_API_KEY")
        self.base_url = (base_url or env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.env_model = env.get("OPENAI_MODEL")
        self.timeout_s = timeout_s
        self.transport = transport

    def complete(self, prompt, *, system=None, model=None, max_tokens=4000, temperature=None):
        if not self.api_key:
            raise LLMError("openai backend: OPENAI_API_KEY is not set (and no api_key was given)")
        model = model or self.env_model or self.default_model
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        t0 = time.monotonic()
        with httpx.Client(transport=self.transport, timeout=self.timeout_s) as client:
            r = client.post(f"{self.base_url}/chat/completions", json=body,
                            headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code != 200:
            raise LLMError(f"openai backend: HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"openai backend: unexpected response shape: {str(data)[:300]}") from e
        usage = data.get("usage") or {}
        cost = usage.get("cost")  # some compatible servers report it
        return LLMResponse(text=text, model=data.get("model", model),
                           input_tokens=usage.get("prompt_tokens"),
                           output_tokens=usage.get("completion_tokens"),
                           cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
                           duration_s=time.monotonic() - t0, backend=self.name, raw=data)


class AnthropicBackend(Backend):
    """Messages API over raw HTTP. Key from ANTHROPIC_API_KEY.

    Sends `anthropic-version: 2026-10-21`. On that version the API rejects
    `temperature`, so the `temperature` argument is accepted and ignored."""

    name = "anthropic"
    api_version = "2026-10-21"
    default_model = "claude-haiku-5-5"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 timeout_s: float = 300, transport: httpx.BaseTransport | None = None,
                 env: dict | None = None):
        env = os.environ if env is None else env
        self.api_key = api_key or env.get("ANTHROPIC_API_KEY")
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.env_model = env.get("ANTHROPIC_MODEL")
        self.timeout_s = timeout_s
        self.transport = transport

    def complete(self, prompt, *, system=None, model=None, max_tokens=4000, temperature=None):
        if not self.api_key:
            raise LLMError("anthropic backend: ANTHROPIC_API_KEY is not set (and no api_key was given)")
        model = model or self.env_model or self.default_model
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        headers = {"x-api-key": self.api_key, "anthropic-version": self.api_version,
                   "content-type": "application/json"}
        t0 = time.monotonic()
        with httpx.Client(transport=self.transport, timeout=self.timeout_s) as client:
            r = client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
        if r.status_code != 200:
            raise LLMError(f"anthropic backend: HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        if data.get("stop_reason") == "refusal":
            details = data.get("stop_details") or {}
            raise LLMError(f"anthropic backend: model refused (category {details.get('category')})")
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        usage = data.get("usage") or {}
        n_in = usage.get("input_tokens")
        if n_in is not None:
            n_in += (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        return LLMResponse(text=text, model=data.get("model", model), input_tokens=n_in,
                           output_tokens=usage.get("output_tokens"), cost_usd=None,
                           duration_s=time.monotonic() - t0, backend=self.name, raw=data)
