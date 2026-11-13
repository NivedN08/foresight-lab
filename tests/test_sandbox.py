"""Sandbox tests. Every run_candidate call here starts a real child process."""
from __future__ import annotations

import json
import os
import socket
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lab.core.task import Case
import lab.sandbox.runner as runner
from lab.sandbox import lint, run_candidate
from lab.sandbox.runner import _probe, _rss_mb

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_cases(n, start=0):
    cases = [Case(f"c{i:03d}", BASE + timedelta(hours=i), BASE + timedelta(hours=i + 6),
                  {"x": float(i), "series": np.arange(4, dtype=float) * i, "station": "A"})
             for i in range(start, start + n)]
    truths = [{"value": 3.0 * i + 1.0} for i in range(start, start + n)]
    return cases, truths


TRAIN, TRAIN_T = make_cases(20)
EVAL, EVAL_T = make_cases(5, start=20)

GOOD = textwrap.dedent('''
    import numpy as np
    from datetime import timezone

    class Forecaster:
        name = "linear"

        def fit(self, cases, truths):
            x = np.array([c.inputs["x"] for c in cases])
            y = np.array([t["value"] for t in truths])
            self.slope, self.intercept = np.polyfit(x, y, 1)

        def predict(self, case):
            assert case.issue_time.tzinfo is not None
            assert case.lead_hours == 6.0
            assert case.inputs["series"].shape == (4,)
            return {"value": float(self.slope * case.inputs["x"] + self.intercept)}
''')


def candidate(predict_body: str, fit_body: str = "pass", header: str = "") -> str:
    return (header + "\nclass Forecaster:\n    name = 't'\n"
            "    def fit(self, cases, truths):\n" + textwrap.indent(textwrap.dedent(fit_body), " " * 8) + "\n"
            "    def predict(self, case):\n" + textwrap.indent(textwrap.dedent(predict_body), " " * 8) + "\n")


# ------------------------------------------------------------------ happy path

def test_correct_candidate_returns_predictions_in_order():
    res = run_candidate(GOOD, TRAIN, TRAIN_T, EVAL)
    assert res.ok, res.error
    assert res.kind is None and res.name == "linear"
    got = [p["value"] for p in res.predictions]
    want = [t["value"] for t in EVAL_T]
    assert got == pytest.approx(want)
    assert "isolated_interpreter" in res.protections and "lint" in res.protections
    assert res.duration_s > 0


def test_candidate_print_goes_to_tail_not_result():
    src = candidate('print("{\\"ok\\": true, \\"predictions\\": []}")\nreturn {"v": 1.0}',
                    'print("hello from fit")')
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL)
    assert res.ok, res.error
    assert res.predictions == [{"v": 1.0}] * len(EVAL)
    assert "hello from fit" in res.stdout_tail


# ------------------------------------------------------------------------ lint

LINT_CASES = [
    ("import os", "import of 'os' is not allowed"),
    ("import socket", "import of 'socket' is not allowed"),
    ("from subprocess import run", "import from 'subprocess' is not allowed"),
    ("x = open('/etc/passwd')", "use of 'open'"),
    ("x = eval('1+1')", "use of 'eval'"),
    ("x = ().__class__.__bases__", "attribute '__class__' starts with a double underscore"),
    ("x = getattr(object(), '__dict__')", "use of 'getattr'"),
    ("import numpy\nx = numpy.load('a.npy')", "'.load' is a numpy file/network helper"),
    ("import numpy as np\nnp.save('a.npy', 1)", "'.save' is a numpy file/network helper"),
    ("from numpy import fromfile", "importing 'fromfile' from 'numpy' is not allowed"),
    ("import numpy as np\nx = np.lib.npyio", "'.npyio'"),
    ("import typing\nx = typing.sys", "attribute '.sys' is not allowed"),
    ("x = [" + ", ".join(str(i) for i in range(500)) + "]", "list literal with 500 elements"),
    ("x = {" + ", ".join(f"'k{i}': {i}" for i in range(300)) + "}", "dict literal with 300 elements"),
    ("x = [[i for i in range(3)]] + [" + ", ".join(f"[{i}, {i}]" for i in range(100)) + "]", "list literal with 300 elements"),
    ("x = '" + "a" * 2500 + "'", "string literal of 2500 characters"),
]


@pytest.mark.parametrize("snippet,expected", LINT_CASES, ids=[c[1][:40] for c in LINT_CASES])
def test_lint_catches_banned_things(snippet, expected):
    src = snippet + "\n" + GOOD
    problems = lint(src)
    assert any(expected in p for p in problems), problems
    # every message names a line
    assert all(p.startswith("line ") for p in problems)


def test_lint_message_names_the_line():
    src = GOOD + "\nx = 1\ny = eval('2')\n"
    line = src.splitlines().index("y = eval('2')") + 1
    assert any(p.startswith(f"line {line}:") and "eval" in p for p in lint(src))


def test_lint_oversized_file():
    long_src = GOOD + "\n".join(f"x{i} = {i}" for i in range(450))
    assert any("lines; the limit is 400" in p for p in lint(long_src))
    wide_src = GOOD + "\n".join("y = '" + "b" * 1900 + "'" for _ in range(17))
    assert wide_src.count("\n") < 400
    assert any("characters; the limit is 30000" in p for p in lint(wide_src))


def test_lint_requires_forecaster_and_accepts_good():
    assert lint(GOOD) == []
    assert any("no top-level 'class Forecaster'" in p for p in lint("x = 1\n"))
    assert any("no 'predict' method" in p for p in lint("class Forecaster:\n    def fit(self, c, t):\n        pass\n"))
    assert any("syntax error" in p for p in lint("def (:\n"))


def test_lint_failure_executes_nothing(tmp_path):
    marker = tmp_path / "ran"
    src = f"import os\nos.system('touch {marker}')\n" + GOOD
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL)
    assert not res.ok and res.kind == "lint" and res.error.startswith("lint:")
    assert res.lint and res.predictions is None
    assert not marker.exists()


# ---------------------------------------------------------------- truth access

def test_eval_truths_never_reach_the_child(monkeypatch):
    sentinel = "SENTINEL-7f3a9c-EVAL-TRUTH"
    eval_truths = [{"value": t["value"], "tag": sentinel} for t in EVAL_T]  # parent-only
    sent: list[bytes] = []
    real_build = runner.build_payload

    def spy(*args, **kwargs):
        b = real_build(*args, **kwargs)
        sent.append(b)
        return b

    monkeypatch.setattr(runner, "build_payload", spy)
    src = textwrap.dedent('''
        import statistics

        class Forecaster:
            name = "peek"

            def fit(self, cases, truths):
                self.mean = statistics.fmean(t["value"] for t in truths)

            def predict(self, case):
                found = 0.0
                for attr in dir(case):
                    if "truth" in attr or "target_value" in attr or "label" in attr:
                        found = 1.0
                if any("truth" in k for k in case.inputs):
                    found = 1.0
                return {"value": self.mean, "found_truth": found}
    ''')
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL)
    assert res.ok, res.error
    assert len(sent) == 1
    assert sentinel.encode() not in sent[0]
    payload = json.loads(sent[0])
    assert set(payload) == {"source", "allowed_imports", "train_cases", "train_truths", "eval_cases"}
    assert len(payload["train_truths"]) == len(TRAIN) and len(payload["eval_cases"]) == len(EVAL)
    for c in payload["eval_cases"]:
        assert set(c) == {"case_id", "issue_time", "target_time", "lead_hours", "inputs"}
    # the best it can do is the train mean
    train_mean = float(np.mean([t["value"] for t in TRAIN_T]))
    assert all(p["value"] == pytest.approx(train_mean) for p in res.predictions)
    assert all(p["found_truth"] == 0.0 for p in res.predictions)
    assert eval_truths[0]["tag"] == sentinel


def test_case_objects_reject_new_attributes():
    src = candidate('case.truth = 1.0\nreturn {"v": 1.0}')
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL)
    assert not res.ok and res.kind == "crash" and "AttributeError" in res.error


# --------------------------------------------------------------- run-time limits

def test_infinite_loop_is_killed_at_time_limit(tmp_path):
    src = candidate("while True:\n    pass\nreturn {}")
    t0 = time.monotonic()
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL, time_limit_s=2, log_path=tmp_path / "log.jsonl")
    elapsed = time.monotonic() - t0
    assert not res.ok and res.kind == "timeout"
    assert res.error.startswith("timeout")
    assert 2 <= elapsed < 10


def test_memory_hog_fails_without_taking_parent_down():
    caps = _probe()
    if _rss_mb(os.getpid()) is None and "RLIMIT_AS" not in caps["rlimits"]:
        pytest.skip("no way to enforce a memory limit here: no RSS readout and RLIMIT_AS is refused")
    src = candidate(
        'return {"v": 1.0}',
        fit_body=("import numpy as np\nself.blocks = []\n"
                  "for _ in range(80):\n    self.blocks.append(np.ones(50_000_000))\n"),  # 80 x 400 MB
        header="import numpy as np")
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL, memory_limit_mb=300, time_limit_s=60)
    assert not res.ok and res.kind == "memory", res.error
    # the parent is fine and can still run a good candidate
    assert run_candidate(GOOD, TRAIN, TRAIN_T, EVAL).ok


def test_crash_reports_traceback_tail():
    src = candidate('return {"v": 1.0}', fit_body="raise ValueError('bad fit ' + str(len(cases)))")
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL)
    assert not res.ok and res.kind == "crash"
    assert "crash in fit" in res.error and "ValueError: bad fit 20" in res.error
    assert "<candidate>" in res.error  # traceback lines point into the candidate


def test_restricted_builtins_hold_when_lint_is_skipped():
    for body in ('open("/etc/hosts")\nreturn {"v": 1.0}',
                 'import os\nreturn {"v": 1.0}'):
        res = run_candidate(candidate(body), TRAIN, TRAIN_T, EVAL, skip_lint=True)
        assert not res.ok and res.kind == "crash", res.error
        assert "NameError" in res.error or "ImportError" in res.error


# ------------------------------------------------- OS sandbox (macOS sandbox-exec)

# Gets the real __import__ through the object graph, which the lint forbids
# (double-underscore attributes) and the restricted builtins cannot stop.
ESCAPE = textwrap.dedent('''
    def real_import(name):
        for cls in ().__class__.__base__.__subclasses__():
            try:
                g = cls.__init__.__globals__
            except AttributeError:
                continue
            b = g.get("__builtins__")
            if b is None:
                continue
            imp = b["__import__"] if isinstance(b, dict) else b.__import__
            if imp.__name__ == "__import__":
                return imp(name)
        raise RuntimeError("no escape")
''')

needs_sandbox_exec = pytest.mark.skipif(not _probe()["sandbox_exec"],
                                        reason="sandbox-exec is not available or not working on this machine")


@pytest.fixture
def listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    accepted = []

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            accepted.append(1)
            conn.close()

    threading.Thread(target=loop, daemon=True).start()
    yield srv.getsockname()[1], accepted
    srv.close()


def _net_candidate(port):
    return candidate(
        textwrap.dedent(f'''
            socket = real_import("socket")
            s = socket.socket()
            s.settimeout(3)
            try:
                s.connect(("127.0.0.1", {port}))
                ok = 1.0
            except OSError:
                ok = 0.0
            return {{"connected": ok}}
        '''), header=ESCAPE)


def _write_candidate(path):
    return candidate(
        textwrap.dedent(f'''
            b = real_import("builtins")
            created = wrote = 0.0
            try:
                with b.open({str(path)!r}, "w") as f:
                    created = 1.0
                    f.write("pwned")
                    f.flush()
                    wrote = 1.0
            except OSError:
                pass
            return {{"created": created, "wrote": wrote}}
        '''), header=ESCAPE)


def _read_candidate(path):
    return candidate(
        textwrap.dedent(f'''
            b = real_import("builtins")
            try:
                with b.open({str(path)!r}) as f:
                    ok = 1.0 if f.read() == "secret" else 0.5
            except OSError:
                ok = 0.0
            return {{"read": ok}}
        '''), header=ESCAPE)


def test_escape_candidates_are_caught_by_lint(tmp_path):
    for src in (_net_candidate(1), _write_candidate(tmp_path / "x"), _read_candidate(tmp_path / "y")):
        assert any("double underscore" in p for p in lint(src))


@needs_sandbox_exec
def test_network_call_past_lint_is_blocked_at_run_time(listener):
    port, accepted = listener
    src = _net_candidate(port)
    control = run_candidate(src, TRAIN, TRAIN_T, EVAL[:1], skip_lint=True, use_os_sandbox=False)
    assert control.ok, control.error
    assert control.predictions == [{"connected": 1.0}], "control run could not connect; test is meaningless"
    n_before = len(accepted)
    res = run_candidate(src, TRAIN, TRAIN_T, EVAL[:1], skip_lint=True)
    assert "sandbox_exec_no_network" in res.protections
    assert res.ok, res.error
    assert res.predictions == [{"connected": 0.0}]
    time.sleep(0.2)
    assert len(accepted) == n_before


@needs_sandbox_exec
def test_file_write_past_lint_is_blocked_at_run_time(tmp_path):
    control_target = tmp_path / "control.txt"
    control = run_candidate(_write_candidate(control_target), TRAIN, TRAIN_T, EVAL[:1],
                            skip_lint=True, use_os_sandbox=False)
    # without sandbox-exec the file gets created; RLIMIT_FSIZE=0 (where active)
    # still stops any bytes from being written into it
    assert control.ok and control.predictions[0]["created"] == 1.0, control.error
    assert control_target.exists()
    if "rlimit_fsize" in control.protections:
        assert control.predictions[0]["wrote"] == 0.0 and control_target.read_text() == ""

    target = tmp_path / "pwned.txt"
    res = run_candidate(_write_candidate(target), TRAIN, TRAIN_T, EVAL[:1], skip_lint=True)
    assert "sandbox_exec_no_file_write" in res.protections
    assert res.ok and res.predictions == [{"created": 0.0, "wrote": 0.0}], res.error
    assert not target.exists()


@needs_sandbox_exec
def test_file_read_outside_allowlist_is_blocked_at_run_time(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    control = run_candidate(_read_candidate(secret), TRAIN, TRAIN_T, EVAL[:1],
                            skip_lint=True, use_os_sandbox=False)
    assert control.ok and control.predictions == [{"read": 1.0}], control.error
    res = run_candidate(_read_candidate(secret), TRAIN, TRAIN_T, EVAL[:1], skip_lint=True)
    assert res.ok and res.predictions == [{"read": 0.0}], res.error


# ------------------------------------------------------------- output checking

@pytest.mark.parametrize("body,expected", [
    ('return {"v": float("nan")}', "not finite"),
    ('return {"v": float("inf")}', "not finite"),
    ('return [1.0]', "expected a dict"),
    ('return 3.0', "expected a dict"),
    ('return {"v": "high"}', "expected a float"),
    ('return {"v": True}', "expected a float"),
    ('return {}', "not a non-empty dict"),
])
def test_bad_predictions_are_rejected(body, expected):
    res = run_candidate(candidate(body), TRAIN, TRAIN_T, EVAL)
    assert not res.ok and res.kind == "bad_output", res.error
    assert res.predictions is None
    assert expected in res.error


def test_numpy_scalars_are_accepted():
    res = run_candidate(candidate('return {"v": np.float32(2.5), "n": np.int64(3)}', header="import numpy as np"),
                        TRAIN, TRAIN_T, EVAL)
    assert res.ok, res.error
    assert res.predictions[0] == {"v": 2.5, "n": 3.0}


# ----------------------------------------------------------------- attempt log

def test_attempt_log_gets_one_line_per_failure(tmp_path):
    log = tmp_path / "attempts.jsonl"
    lint_src = "import os\n" + GOOD
    crash_src = candidate('return {"v": 1.0}', fit_body="raise RuntimeError('boom')")
    loop_src = candidate("while True:\n    pass")
    nan_src = candidate('return {"v": float("nan")}')

    assert run_candidate(GOOD, TRAIN, TRAIN_T, EVAL, log_path=log).ok
    assert not log.exists()  # success is not logged
    assert run_candidate(lint_src, TRAIN, TRAIN_T, EVAL, log_path=log).kind == "lint"
    assert run_candidate(crash_src, TRAIN, TRAIN_T, EVAL, log_path=log).kind == "crash"
    assert run_candidate(loop_src, TRAIN, TRAIN_T, EVAL, log_path=log, time_limit_s=1).kind == "timeout"
    assert run_candidate(nan_src, TRAIN, TRAIN_T, EVAL, log_path=log).kind == "bad_output"

    lines = log.read_text().splitlines()
    assert len(lines) == 4
    entries = [json.loads(line) for line in lines]
    assert [e["kind"] for e in entries] == ["lint", "crash", "timeout", "bad_output"]
    import hashlib
    assert entries[0]["source_sha256"] == hashlib.sha256(lint_src.encode()).hexdigest()
    for e in entries:
        assert e["time"].endswith("Z") and e["error"]
        datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
