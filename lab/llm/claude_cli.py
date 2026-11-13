"""Backend that shells out to the `claude` command-line tool in print mode.

The call is made to behave like a plain model call:
  -p                         non-interactive print mode; the prompt is sent on stdin
  --output-format json       one JSON result object with usage and total_cost_usd
  --model <alias|name>       haiku (default), sonnet, opus, or a full model name
  --system-prompt <text>     replaces Claude Code's own system prompt
  --tools ""                 no built-in tools
  --safe-mode                no CLAUDE.md, skills, plugins, hooks, MCP servers, memory
  --strict-mcp-config        no MCP servers (none are given with --mcp-config)
  --setting-sources ""       no user / project / local settings files
  --disable-slash-commands   no skills
  --no-session-persistence   nothing saved to disk
The process runs in a fresh empty temporary directory, so no project files are
in reach. max_tokens is passed as CLAUDE_CODE_MAX_OUTPUT_TOKENS; the CLI has no
temperature option, so `temperature` is ignored.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time

from lab.llm.base import Backend, LLMError, LLMResponse

DEFAULT_CLI = "/opt/homebrew/bin/claude"
DEFAULT_SYSTEM = "You are a helpful assistant. Answer directly."
# variables that mark "running inside a Claude Code session"; the child call
# should not think it is a nested sub-session of whoever launched the lab
_NESTING_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
                 "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_PID",
                 "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN")


def _is_auth_error(message: str) -> bool:
    m = message.lower()
    return "authenticate" in m or "not logged in" in m or "/login" in m or "oauth" in m


class ClaudeCLIBackend(Backend):
    name = "claude_cli"
    default_model = "haiku"

    def __init__(self, cli_path: str | None = None, timeout_s: float = 300, retries: int = 1):
        self.cli_path = cli_path or (DEFAULT_CLI if os.path.exists(DEFAULT_CLI) else shutil.which("claude"))
        self.timeout_s = timeout_s
        self.retries = retries

    def command(self, model: str, system: str) -> list[str]:
        return [self.cli_path, "-p", "--output-format", "json", "--model", model,
                "--system-prompt", system, "--tools", "", "--safe-mode", "--strict-mcp-config",
                "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence"]

    def env(self, max_tokens: int) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _NESTING_VARS}
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(max_tokens))
        return env

    def complete(self, prompt, *, system=None, model=None, max_tokens=4000, temperature=None):
        if not self.cli_path:
            raise LLMError("claude_cli backend: the `claude` command was not found")
        model = model or self.default_model
        cmd = self.command(model, system or DEFAULT_SYSTEM)
        t0 = time.monotonic()
        last_error = "no attempt made"
        for attempt in range(self.retries + 1):
            work = tempfile.mkdtemp(prefix="lab-claude-")
            try:
                p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                   timeout=self.timeout_s, cwd=work, env=self.env(max_tokens))
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {self.timeout_s:g}s"
                continue
            finally:
                shutil.rmtree(work, ignore_errors=True)
            try:
                data = json.loads(p.stdout)
            except ValueError:
                last_error = f"exit {p.returncode}, non-JSON output: {(p.stdout or p.stderr)[-500:]}"
                continue
            if p.returncode != 0 or data.get("is_error") or data.get("subtype") != "success":
                last_error = f"exit {p.returncode}, {data.get('subtype')}: {str(data.get('result'))[:500]}"
                if _is_auth_error(str(data.get("result"))):
                    raise LLMError(f"claude_cli backend: not authenticated ({last_error}); "
                                   "run `claude auth login` (or `claude` then /login) in a terminal")
                continue
            return self._parse(data, model, time.monotonic() - t0)
        raise LLMError(f"claude_cli backend failed after {self.retries + 1} attempt(s): {last_error}")

    def _parse(self, data: dict, model: str, duration: float) -> LLMResponse:
        usage = data.get("usage") or {}
        n_in = usage.get("input_tokens")
        if n_in is not None:
            n_in += (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        model_usage = data.get("modelUsage") or {}
        model_name = max(model_usage, key=lambda m: (model_usage[m] or {}).get("outputTokens", 0)) \
            if model_usage else model
        cost = data.get("total_cost_usd")
        return LLMResponse(text=str(data.get("result") or ""), model=model_name,
                           input_tokens=n_in, output_tokens=usage.get("output_tokens"),
                           cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
                           duration_s=duration, backend=self.name, raw=data)
