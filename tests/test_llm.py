"""LLM backend tests. No real network or CLI calls: HTTP goes through
httpx.MockTransport and the CLI backend is pointed at a stub shell script."""
from __future__ import annotations

import json
import os
import stat
import threading

import httpx
import pytest

from lab.llm import (AnthropicBackend, Budget, ClaudeCLIBackend, FakeBackend, LLMError,
                     LLMResponse, OpenAIBackend, complete, extract_code, get_backend)


# ------------------------------------------------------------------------- fake

def test_fake_backend_matches_substrings_and_records_calls():
    b = get_backend("fake", replies={"forecast": "```python\nx = 1\n```", "hello": "hi"}, default="?")
    assert isinstance(b, FakeBackend)
    r = b.complete("please write a forecast model", system="sys", model="m1", max_tokens=10)
    assert isinstance(r, LLMResponse)
    assert r.text.startswith("```python") and r.backend == "fake" and r.model == "m1"
    assert r.cost_usd == 0.0 and r.input_tokens == 6 and r.output_tokens == 5
    assert b.complete("hello there").text == "hi"
    assert b.complete("nothing matches").text == "?"
    assert [c["prompt"] for c in b.calls] == ["please write a forecast model", "hello there", "nothing matches"]
    assert b.calls[0]["system"] == "sys" and b.calls[0]["max_tokens"] == 10


def test_complete_with_backend_object_records_budget():
    budget = Budget()
    fake = FakeBackend(default="one two three")
    r = complete("a b", backend=fake, budget=budget, label="proposer")
    assert r.text == "one two three"
    snap = budget.snapshot()
    assert snap["by_label"]["proposer"]["calls"] == 1
    assert snap["by_label"]["proposer"]["output_tokens"] == 3
    assert snap["total"]["input_tokens"] == 2


def test_unknown_backend_and_default_backend(monkeypatch):
    with pytest.raises(LLMError, match="unknown LLM backend"):
        get_backend("nope")
    monkeypatch.delenv("LAB_LLM_BACKEND", raising=False)
    assert isinstance(get_backend(), ClaudeCLIBackend)
    monkeypatch.setenv("LAB_LLM_BACKEND", "fake")
    assert isinstance(get_backend(), FakeBackend)


# ----------------------------------------------------------------- extract_code

def test_extract_code_fenced_python():
    reply = "Here is my model:\n```python\nclass Forecaster:\n    name = 'a'\n```\nThanks!"
    assert extract_code(reply) == "class Forecaster:\n    name = 'a'\n"


def test_extract_code_prefers_forecaster_block_and_python_tag():
    reply = ("```\nnot python at all {{{\n```\n"
             "```python\nimport math\n```\n"
             "```py\nimport numpy as np\n\nclass Forecaster:\n    pass\n```\n")
    assert "class Forecaster" in extract_code(reply)
    assert extract_code("```\nx = 1\n```") == "x = 1\n"


def test_extract_code_unfenced():
    src = "import math\n\nclass Forecaster:\n    name = 'b'\n"
    assert extract_code(src) == src
    assert extract_code("  x = 1  \n") == "x = 1\n"


def test_extract_code_no_code():
    assert extract_code("I could not come up with a model, sorry.") is None
    assert extract_code("ok") is None  # parses as a bare name, but it is prose
    assert extract_code("") is None and extract_code(None) is None
    assert extract_code("```python\ndef broken(:\n```") is None


# ----------------------------------------------------------------------- Budget

def test_budget_under_threads():
    budget = Budget()
    n_threads, per_thread = 8, 500

    def work(i):
        for _ in range(per_thread):
            budget.record(f"label{i % 2}", LLMResponse(text="", model="m", input_tokens=3,
                                                       output_tokens=2, cost_usd=0.001,
                                                       duration_s=0.01, backend="fake"))
            budget.record("unknown", cost_usd=None, input_tokens=1)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = budget.snapshot()
    total_calls = n_threads * per_thread
    assert snap["by_label"]["label0"]["calls"] + snap["by_label"]["label1"]["calls"] == total_calls
    assert snap["by_label"]["label0"]["input_tokens"] == 3 * total_calls // 2
    assert snap["by_label"]["unknown"]["cost_unknown_calls"] == total_calls
    assert snap["total"]["calls"] == 2 * total_calls
    assert snap["total"]["cost_usd"] == pytest.approx(0.001 * total_calls)
    assert snap["total"]["seconds"] == pytest.approx(0.01 * total_calls)
    snap["by_label"]["label0"]["calls"] = -1  # snapshot is a copy
    assert budget.snapshot()["by_label"]["label0"]["calls"] > 0


# ---------------------------------------------------------------- HTTP backends

def _capture(response_json, status=200):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(status, json=response_json)

    return httpx.MockTransport(handler), seen


def test_openai_backend_request_and_usage():
    transport, seen = _capture({
        "model": "gpt-test-1", "choices": [{"message": {"role": "assistant", "content": "pong"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13}})
    b = OpenAIBackend(api_key="sk-test", base_url="https://tinker.example/v1/", transport=transport, env={})
    r = b.complete("ping", system="be brief", model="my-model", max_tokens=50, temperature=0.3)
    assert seen["url"] == "https://tinker.example/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer sk-test"
    assert seen["body"] == {"model": "my-model", "max_tokens": 50, "temperature": 0.3,
                            "messages": [{"role": "system", "content": "be brief"},
                                         {"role": "user", "content": "ping"}]}
    assert (r.text, r.model, r.input_tokens, r.output_tokens, r.cost_usd, r.backend) == \
           ("pong", "gpt-test-1", 11, 2, None, "openai")


def test_openai_backend_env_config_and_no_temperature():
    transport, seen = _capture({"choices": [{"message": {"content": "x"}}], "usage": {}})
    env = {"OPENAI_API_KEY": "k-env", "OPENAI_BASE_URL": "http://localhost:9999/v1", "OPENAI_MODEL": "m-env"}
    OpenAIBackend(transport=transport, env=env).complete("hi")
    assert seen["url"] == "http://localhost:9999/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer k-env"
    assert seen["body"]["model"] == "m-env" and "temperature" not in seen["body"]
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_backend_request_and_usage():
    transport, seen = _capture({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": "po"}, {"type": "text", "text": "ng"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 5}})
    b = AnthropicBackend(api_key="ak-test", transport=transport, env={})
    r = b.complete("ping", system="be brief", max_tokens=64, temperature=0.9)
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "ak-test"
    assert seen["headers"]["anthropic-version"] == "2026-10-21"
    assert seen["body"] == {"model": "claude-haiku-5-5", "max_tokens": 64, "system": "be brief",
                            "messages": [{"role": "user", "content": "ping"}]}
    assert (r.text, r.input_tokens, r.output_tokens, r.backend) == ("pong", 12, 3, "anthropic")


@pytest.mark.parametrize("cls,var", [(OpenAIBackend, "OPENAI_API_KEY"), (AnthropicBackend, "ANTHROPIC_API_KEY")])
def test_http_backend_missing_key_is_clear(cls, var):
    def handler(request):
        raise AssertionError("no request should be sent without a key")

    b = cls(transport=httpx.MockTransport(handler), env={})
    with pytest.raises(LLMError, match=var):
        b.complete("hi")


def test_http_errors_and_refusal_raise():
    transport, _ = _capture({"error": {"message": "bad"}}, status=401)
    with pytest.raises(LLMError, match="HTTP 401"):
        AnthropicBackend(api_key="k", transport=transport, env={}).complete("x")
    transport, _ = _capture({"content": [], "stop_reason": "refusal",
                             "stop_details": {"type": "refusal", "category": "cyber"}, "usage": {}})
    with pytest.raises(LLMError, match="refused"):
        AnthropicBackend(api_key="k", transport=transport, env={}).complete("x")


# ------------------------------------------------------- claude_cli (stub CLI)

CLI_OK = {"type": "result", "subtype": "success", "is_error": False, "result": "ok",
          "total_cost_usd": 0.00042, "duration_ms": 900,
          "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 1000, "output_tokens": 4},
          "modelUsage": {"claude-haiku-5-5": {"inputTokens": 10, "outputTokens": 4}}}


@pytest.fixture
def stub_cli(tmp_path, monkeypatch):
    """A fake `claude` that records argv, cwd, stdin and env, then prints the
    next canned response from responses.txt (one JSON per line)."""
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        'n=$(cat "$STUB_DIR/count" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$STUB_DIR/count"\n'
        'printf "%s\\n" "$@" > "$STUB_DIR/args"\n'
        'pwd -P > "$STUB_DIR/cwd"; ls -A > "$STUB_DIR/cwd_listing"\n'
        'echo "$CLAUDE_CODE_MAX_OUTPUT_TOKENS|${CLAUDECODE:-unset}" > "$STUB_DIR/env"\n'
        'cat > "$STUB_DIR/stdin"\n'
        'sed -n "${n}p" "$STUB_DIR/responses.txt"\n'
        'exit $(sed -n "${n}p" "$STUB_DIR/codes.txt")\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STUB_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDECODE", "1")

    def set_responses(*pairs):
        (tmp_path / "responses.txt").write_text("".join(json.dumps(r) + "\n" for r, _ in pairs))
        (tmp_path / "codes.txt").write_text("".join(f"{c}\n" for _, c in pairs))

    return str(script), tmp_path, set_responses


def test_claude_cli_flags_and_parsing(stub_cli):
    path, d, set_responses = stub_cli
    set_responses((CLI_OK, 0))
    r = ClaudeCLIBackend(cli_path=path).complete("Reply with ok", system="sys!", model="sonnet", max_tokens=123)
    args = (d / "args").read_text().splitlines()
    assert args[:2] == ["-p", "--output-format"] and "json" in args
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--system-prompt") + 1] == "sys!"
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    for flag in ("--safe-mode", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in args
    assert (d / "stdin").read_text() == "Reply with ok"
    assert (d / "cwd_listing").read_text() == ""  # empty working directory
    assert os.path.realpath((d / "cwd").read_text().strip()) != os.path.realpath(os.getcwd())
    assert (d / "env").read_text().strip() == "123|unset"  # nesting marker stripped
    assert (r.text, r.model, r.input_tokens, r.output_tokens, r.cost_usd, r.backend) == \
           ("ok", "claude-haiku-5-5", 1110, 4, 0.00042, "claude_cli")


def test_claude_cli_default_model_is_haiku(stub_cli):
    path, d, set_responses = stub_cli
    set_responses((CLI_OK, 0))
    ClaudeCLIBackend(cli_path=path).complete("x")
    args = (d / "args").read_text().splitlines()
    assert args[args.index("--model") + 1] == "haiku"


def test_claude_cli_retries_once_then_fails(stub_cli):
    path, d, set_responses = stub_cli
    err = {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "overloaded"}
    set_responses((err, 1), (CLI_OK, 0))
    assert ClaudeCLIBackend(cli_path=path).complete("x").text == "ok"
    assert (d / "count").read_text().strip() == "2"

    (d / "count").unlink()
    set_responses((err, 1), (err, 1), (CLI_OK, 0))
    with pytest.raises(LLMError, match="after 2 attempt"):
        ClaudeCLIBackend(cli_path=path).complete("x")
    assert (d / "count").read_text().strip() == "2"


def test_claude_cli_auth_error_is_not_retried(stub_cli):
    path, d, set_responses = stub_cli
    auth = {"type": "result", "subtype": "success", "is_error": True,
            "result": "Failed to authenticate: OAuth session expired and could not be refreshed"}
    set_responses((auth, 1), (CLI_OK, 0))
    with pytest.raises(LLMError, match="claude auth login"):
        ClaudeCLIBackend(cli_path=path).complete("x")
    assert (d / "count").read_text().strip() == "1"


def test_claude_cli_timeout(tmp_path):
    script = tmp_path / "slowclaude"
    script.write_text("#!/bin/sh\nsleep 5\n")
    script.chmod(0o755)
    with pytest.raises(LLMError, match="timed out"):
        ClaudeCLIBackend(cli_path=str(script), timeout_s=0.3).complete("x")
