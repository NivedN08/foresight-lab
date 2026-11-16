"""Modal backend and app, tested without a Modal account: the adapter is
replaced by LocalFakeAdapter, which runs the container-side function in this
process (real sandbox child processes) and records every byte 'sent'."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from test_evalpool import LINEAR, MEAN, SENTINEL, ZERO, SynthProblem
from lab.evalpool import modal_backend as MB
from lab.evalpool.evaluate import evaluate


def _pool(problem, adapter=None, **kw):
    return MB.ModalEvalPool(problem, problem.splits(), max_containers=4,
                            adapter=adapter or MB.LocalFakeAdapter(), **kw)


def _same(a, b):
    assert (a.ok, a.error, a.lint, a.name, a.source_hash, a.windows) == (b.ok, b.error, b.lint, b.name,
                                                                         b.source_hash, b.windows)
    assert set(a.losses) == set(b.losses)
    for w in a.losses:
        assert np.array_equal(a.losses[w], b.losses[w], equal_nan=True)
    assert a.summary == b.summary


def test_fake_backed_pool_matches_local_evaluator():
    p = SynthProblem()
    pool = _pool(p)
    for src in (LINEAR, MEAN):      # the 'seed' and a baseline of the synthetic problem
        remote = pool.submit(src).result()
        local = evaluate(p, src, p.splits())
        assert remote.ok, remote.error
        _same(remote, local)
        assert set(remote.protections) == set(local.protections)   # the fake adds nothing it lacks
    pool.close()


def test_coast_seed_and_baseline_match_local_evaluator_on_real_splits():
    from lab.tasks.coast.problem import CoastWaterProblem
    from lab.core.problem import Splits, Window
    p = CoastWaterProblem(until="2026-11-13T22:00:00Z")
    if not (p.cache_dir / f"{p._key(p.until)}.pkl").exists():
        pytest.skip("real splits not built")
    sp = p.splits()
    small = Splits(Window(sp.train.cases[:2000], sp.train.truths[:2000], sp.train.blocks[:2000]),
                   Window(sp.select.cases[:500], sp.select.truths[:500], sp.select.blocks[:500]),
                   Window(sp.confirm.cases[:500], sp.confirm.truths[:500], sp.confirm.blocks[:500]),
                   sp.test)
    pool = MB.ModalEvalPool(p, small, max_containers=2, adapter=MB.LocalFakeAdapter())
    for src in (p.seed_source(), p.baseline_sources()["damped_persistence"]):
        _same(pool.submit(src).result(), evaluate(p, src, small))
    pool.close()


def test_evaluation_truths_never_leave_the_caller():
    p = SynthProblem(sentinel=True)
    sp = p.splits()
    fake = MB.LocalFakeAdapter()
    pool = _pool(p, fake)
    r = pool.submit(MEAN).result()
    assert r.ok, r.error
    assert fake.uploads == 1 and fake.calls == 1 and fake.sent
    for blob in fake.sent:
        assert SENTINEL.encode() not in blob
    request = json.loads(fake.sent[-1])
    assert set(request) == {"key", "source", "time_limit_s", "memory_limit_mb"}
    body = json.loads(b"{" + fake.sent[0] + b"}")
    assert len(body["train_truths"]) == len(sp.train)
    assert len(body["eval_cases"]) == len(sp.select) + len(sp.confirm)
    assert all(set(c) == {"case_id", "issue_time", "target_time", "lead_hours", "inputs"} for c in body["eval_cases"])
    # losses are still computed here, from the caller's truths
    local = evaluate(p, MEAN, sp)
    assert np.array_equal(r.losses["select"], local.losses["select"])
    assert sp.select.truths[0]["tag"] == SENTINEL
    pool.close()


def test_cases_are_uploaded_once_and_results_cached_by_source_hash():
    p = SynthProblem()
    fake = MB.LocalFakeAdapter()
    pool = _pool(p, fake)
    a = pool.map([LINEAR, MEAN, LINEAR, ZERO, MEAN])
    assert pool.runs == 3 and fake.calls == 3 and fake.uploads == 1
    assert a[0] is a[2] and a[1] is a[4]
    # a second pool (a new process, say) finds the cases already on the 'volume'
    pool2 = _pool(p, fake)
    pool2.submit(ZERO).result()
    assert fake.uploads == 1 and fake.calls == 4
    pool.close()
    pool2.close()


def test_failing_and_lint_failing_candidates_report_the_failure():
    p = SynthProblem()
    fake = MB.LocalFakeAdapter()
    pool = _pool(p, fake)
    crash = ("class Forecaster:\n    name = 'c'\n    def fit(self, cases, truths):\n        raise ValueError('nope')\n"
             "    def predict(self, case):\n        return {'y': 0.0}\n")
    r = pool.submit(crash).result()
    assert not r.ok and "nope" in r.error and r.losses == {} and r.summary == {}
    lint_bad = "import os\n" + crash
    calls = fake.calls
    r2 = pool.submit(lint_bad).result()
    assert not r2.ok and r2.lint and r2.error.startswith("lint")
    assert fake.calls == calls                 # never sent to the remote side
    wrong = ("class Forecaster:\n    name = 'w'\n    def fit(self, cases, truths):\n        pass\n"
             "    def predict(self, case):\n        return {'y': float('nan')}\n")
    r3 = pool.submit(wrong).result()
    assert not r3.ok and r3.error.startswith("bad output")
    pool.close()


def test_remote_outage_is_reported_and_not_cached():
    p = SynthProblem()

    class Flaky(MB.LocalFakeAdapter):
        fail = True

        def call(self, request):
            if self.fail:
                raise ConnectionError("Modal unreachable")
            return super().call(request)

    fake = Flaky()
    pool = _pool(p, fake)
    r = pool.submit(LINEAR).result()
    assert not r.ok and r.error.startswith("remote:")
    fake.fail = False
    r2 = pool.submit(LINEAR).result()
    assert r2.ok and pool.runs == 2
    pool.close()


def test_remote_protections_are_reported():
    p = SynthProblem()
    pool = _pool(p, MB.LocalFakeAdapter(extra_protections=MB.REMOTE_PROTECTIONS))
    r = pool.submit(LINEAR).result()
    assert {"lint", "restricted_builtins", "wall_clock_kill", "modal_block_network",
            "modal_restrict_modal_access"} <= set(r.protections)
    pool.close()


def test_missing_modal_or_token_gives_setup_instructions(monkeypatch):
    # A stand-in `modal` package, so the result does not depend on whether this
    # machine has Modal installed or a token in ~/.modal.toml.
    import sys
    import types
    fake_modal = types.ModuleType("modal")
    fake_config = types.ModuleType("modal.config")
    saved = {}
    fake_config.config = SimpleNamespace(get=lambda key, *a, **k: saved.get(key))
    fake_modal.config = fake_config
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    monkeypatch.setitem(sys.modules, "modal.config", fake_config)

    p = SynthProblem()
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    with pytest.raises(MB.ModalSetupError, match="modal token new"):
        MB.ModalEvalPool(p, p.splits())
    saved["token_id"] = "ak-test"                 # a token in the config file is enough
    assert MB.check_modal_setup() is fake_modal
    saved.clear()
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")   # ... and so is the environment variable
    assert MB.check_modal_setup() is fake_modal
    monkeypatch.delenv("MODAL_TOKEN_ID")
    import builtins
    real_import = builtins.__import__

    def no_modal(name, *a, **k):
        if name == "modal" or name.startswith("modal."):
            raise ImportError("No module named 'modal'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_modal)
    with pytest.raises(MB.ModalSetupError, match="pip install modal"):
        MB.check_modal_setup()


def test_registry_and_research_loop_flag():
    from lab.evalpool.pool import EvalPool
    from lab.evalpool.registry import make_pool
    p = SynthProblem()
    assert type(make_pool(p, p.splits(), "local", workers=1)) is EvalPool
    with pytest.raises(SystemExit):
        make_pool(p, p.splits(), "cloud")
    import inspect
    import lab.swarm.run as R
    assert "--evaluator" in inspect.getsource(R.main)
    assert "evaluator" in inspect.signature(R.build_evaluator).parameters


# ---------------------------------------------------------------- the app
class FakeVolume:
    """The parts of modal.Volume the laptop commands use, backed by a directory."""

    def __init__(self, root):
        self.root = root

    def listdir(self, path, recursive=False):
        d = self.root / path.lstrip("/")
        if not d.exists():
            raise FileNotFoundError(path)
        items = d.rglob("*") if recursive else d.iterdir()
        return [SimpleNamespace(path=str(f.relative_to(self.root)),
                                type=SimpleNamespace(name="FILE" if f.is_file() else "DIRECTORY"))
                for f in items]

    def read_file(self, path):
        data = (self.root / path.lstrip("/")).read_bytes()
        yield data[: len(data) // 2]
        yield data[len(data) // 2:]

    def batch_upload(self, force=False):
        vol = self

        class Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put_file(self, src, dest):
                out = vol.root / dest.lstrip("/")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(src.read() if hasattr(src, "read") else open(src, "rb").read())

        return Batch()


def _fake_repo(root):
    from lab.core.ledger import SealedLedger
    from datetime import datetime, timedelta, timezone
    t = datetime(2026, 11, 14, tzinfo=timezone.utc)
    for task in ("coast", "turb"):
        led = SealedLedger(root / "runs" / task / "live")
        led.seal_many([{"task": task, "model": "M", "case_id": f"{task}{k}", "issued_at": t,
                        "target_time": t + timedelta(hours=1), "prediction": {"v": float(k)}, "meta": {}}
                       for k in range(3)], now=t)
        (root / "runs" / task / "live" / "state.json").write_text(json.dumps({"generated_at": "2026-11-14T00:00:00Z"}))
    (root / "runs" / "lab" / "promoted").mkdir(parents=True)
    (root / "runs" / "lab" / "promoted" / "lab-a.py").write_text("x = 1\n")
    (root / "cache" / "coast").mkdir(parents=True)
    (root / "cache" / "coast" / "a.txt").write_text("cached")


def test_seed_volume_then_pull_keeps_the_chain(tmp_path):
    import lab.modal_app as M
    from lab.core.ledger import SealedLedger
    laptop, volroot, other = tmp_path / "laptop", tmp_path / "vol", tmp_path / "other"
    _fake_repo(laptop)
    vol = FakeVolume(volroot)
    n = M.seed_volume(vol, root=laptop, log=lambda *a: None)
    assert n == 6 and (volroot / "runs/coast/live/sealed.jsonl").exists()   # 2 ledgers + 2 states + 2 others
    assert not (volroot / "runs/coast/live/.lock").exists()
    assert (volroot / "runs/lab/promoted/lab-a.py").exists() and (volroot / "cache/coast/a.txt").exists()
    with pytest.raises(SystemExit, match="refusing"):
        M.seed_volume(vol, root=laptop, log=lambda *a: None)
    # a cycle on Modal appends to the same chain ...
    led = SealedLedger(volroot / "runs/coast/live")
    head_before = led.head()
    from datetime import datetime, timedelta, timezone
    t = datetime(2026, 11, 14, 1, tzinfo=timezone.utc)
    led.seal_many([{"task": "coast", "model": "M", "case_id": "c9", "issued_at": t,
                    "target_time": t + timedelta(hours=1),
                    "prediction": {"v": 9.0}, "meta": {}}], now=t)
    # ... and pull brings it back intact
    assert M.pull(vol, root=other, log=lambda *a: None) >= 4
    pulled = SealedLedger(other / "runs/coast/live")
    ok, n_checked, msg = pulled.verify()
    assert ok, msg
    assert pulled.head()[0] == head_before[0] + 1
    summary = M.ledger_summary(other)
    assert summary["coast"]["records"] == 4 and summary["coast"]["verify_ok"]
    assert summary["turb"]["state_generated_at"] == "2026-11-14T00:00:00Z"


def test_link_data_dirs_and_pause(tmp_path):
    import lab.modal_app as M
    root, data = tmp_path / "repo", tmp_path / "data"
    root.mkdir()
    M.link_data_dirs(data, root)
    M.link_data_dirs(data, root)          # idempotent
    assert (root / "runs").resolve() == (data / "runs").resolve()
    (root / "runs" / "x.txt").write_text("1")
    assert (data / "runs" / "x.txt").exists()
    assert not M.paused(data)
    (data / "control").mkdir()
    (data / M.PAUSE_FILE).write_text("now")
    assert M.paused(data)
    clash = tmp_path / "repo2"
    (clash / "runs").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="refusing"):
        M.link_data_dirs(data, clash)


def test_modal_app_declares_the_documented_functions():
    import lab.modal_app as M
    if M.modal is None:
        pytest.skip("modal not installed")
    for name in ("evaluate_candidate", "coast_cycle", "turb_cycle", "live_summary"):
        assert isinstance(getattr(M, name), M.modal.Function), name


# ------------------------------------- promoted forecasters routed to the remote function
def _coast_cycle_with_remote(tmp_path, monkeypatch, adapter, sources):
    from coast_fakes import T0, FakeClient, FakeClock
    from test_coast import _promote_files
    from lab.tasks.coast import promote as P
    from lab.tasks.coast.live import WaterCycle

    def forbidden(*a, **k):
        raise AssertionError("promoted code ran in the live container")

    monkeypatch.setattr(P, "local_executor", forbidden)
    pdir = tmp_path / "promoted"
    for name, src in sources.items():
        _promote_files(pdir, name, src)
    clock = FakeClock(T0)
    client = FakeClient(clock)
    wc = WaterCycle(client, fit_days=6, stations=["9414290", "9410170"], log=lambda *a: None, promoted_dir=pdir)
    wc.promoted.executor = MB.RemoteCandidateExecutor(adapter)
    return clock, client, wc


def test_live_cycle_sends_promoted_code_to_the_restricted_function(tmp_path, monkeypatch):
    from test_coast import PROMOTED_BAD, PROMOTED_GOOD
    from lab.core.ledger import SealedLedger
    from lab.tasks.coast.live import cycle
    fake = MB.LocalFakeAdapter(extra_protections=MB.REMOTE_PROTECTIONS)
    clock, client, wc = _coast_cycle_with_remote(tmp_path, monkeypatch, fake,
                                                 {"lab-t": PROMOTED_GOOD, "lab-bad": PROMOTED_BAD})
    led_dir = tmp_path / "live"
    s1 = cycle(clock=clock, client=client, ledger_dir=led_dir, wc=wc, log=lambda *a: None)
    assert s1["ledger"]["records"] == 2 * 5 * (4 + 1) and s1["ledger"]["verify"]["ok"]
    assert fake.calls == 2 and fake.uploads == 1          # one case upload shared by both forecasters
    keys = {json.loads(b)["key"] for b in fake.sent if b.startswith(b'{"key"')}
    assert len(keys) == 1 and next(iter(keys)).startswith("live/")
    prom = {p["name"]: p["status"] for p in s1["water"]["promoted"]}
    assert prom["lab-t"]["ok"] and prom["lab-t"]["sealed"] == 10
    assert "modal_block_network" in prom["lab-t"]["protections"]
    assert not prom["lab-bad"]["ok"] and "always fails" in prom["lab-bad"]["error"]
    # same hour: no second remote run
    clock.advance(minutes=15)
    cycle(clock=clock, client=client, ledger_dir=led_dir, wc=wc, log=lambda *a: None)
    assert fake.calls == 2
    # next day: the remote forecaster's records are graded and the chain verifies
    clock.advance(hours=25)
    s3 = cycle(clock=clock, client=client, ledger_dir=led_dir, wc=wc, log=lambda *a: None)
    assert fake.calls == 4 and s3["ledger"]["verify"]["ok"]
    assert s3["water"]["scoreboard"]["Lab:lab-t"]["1"]["graded"] >= 1
    ok, _, msg = SealedLedger(led_dir).verify()
    assert ok, msg


def test_remote_outage_fails_only_the_promoted_forecaster(tmp_path, monkeypatch):
    from test_coast import PROMOTED_GOOD
    from lab.tasks.coast.live import cycle

    class Down(MB.LocalFakeAdapter):
        def call(self, request):
            raise ConnectionError("evaluate_candidate unreachable")

    clock, client, wc = _coast_cycle_with_remote(tmp_path, monkeypatch, Down(), {"lab-t": PROMOTED_GOOD})
    s = cycle(clock=clock, client=client, ledger_dir=tmp_path / "live", wc=wc, log=lambda *a: None)
    assert s["ledger"]["records"] == 2 * 5 * 4 and s["ledger"]["verify"]["ok"]
    (row,) = s["water"]["promoted"]
    assert row["status"]["ok"] is False and row["status"]["error"].startswith("remote:")


def test_remote_executor_lints_locally_and_checks_output():
    from lab.sandbox import runner
    p = SynthProblem()
    sp = p.splits()
    prep = runner.prepare_cases(sp.train.cases, sp.train.truths, sp.select.cases)
    fake = MB.LocalFakeAdapter()
    ex = MB.RemoteCandidateExecutor(fake)
    r = ex("import os\nclass Forecaster:\n    pass\n", prep)
    assert not r.ok and r.kind == "lint" and fake.calls == 0 and fake.uploads == 0
    r = ex(LINEAR, prep, time_limit_s=60, memory_limit_mb=1024)
    assert r.ok and len(r.predictions) == len(sp.select)
    local = runner.run_prepared(LINEAR, prep)
    assert r.predictions == local.predictions

    class Short(MB.LocalFakeAdapter):
        def call(self, request):
            reply = super().call(request)
            reply["predictions"] = reply["predictions"][:-1]
            return reply

    r = MB.RemoteCandidateExecutor(Short())(LINEAR, prep)
    assert not r.ok and r.error.startswith("bad output")


def test_volume_mount_adapter_commits_and_prunes(tmp_path):
    from lab.sandbox import runner
    p = SynthProblem()
    sp = p.splits()

    class Vol:
        commits = 0

        def commit(self):
            Vol.commits += 1

    class Fn:
        def remote(self, request):
            return MB.run_remote(request, tmp_path, MB.REMOTE_PROTECTIONS)

    ad = MB.VolumeMountAdapter(tmp_path, Vol(), Fn(), keep=2)
    ex = MB.RemoteCandidateExecutor(ad)
    keys = []
    for n in (20, 25, 30):      # three different hourly case sets
        prep = runner.prepare_cases(sp.train.cases[:n], sp.train.truths[:n], sp.select.cases)
        assert ex(MEAN, prep).ok
        keys.append(MB.cases_key(prep, "live"))
    assert Vol.commits == 3
    left = sorted(x.name for x in (tmp_path / "live").iterdir())
    assert len(left) == 2 and keys[-1].split("/")[1] in left and keys[0].split("/")[1] not in left


def test_run_remote_rejects_malformed_keys(tmp_path):
    for key in ("../etc", "live/../../x", "LIVE/" + "a" * 32, "a" * 31):
        r = MB.run_remote({"key": key, "source": LINEAR, "time_limit_s": 10, "memory_limit_mb": 512}, tmp_path)
        assert not r["ok"] and "malformed" in r["error"]


def test_modal_coast_cycle_wires_the_remote_executor(tmp_path, monkeypatch):
    import lab.modal_app as M
    from lab.tasks.coast import live as L
    seen = {}

    def fake_cycle(**kw):
        seen["wc"] = kw["wc"]
        return {"ledger": {"records": 0, "verify": {"ok": True}}, "generated_at": "x"}

    monkeypatch.setattr(L, "cycle", fake_cycle)
    monkeypatch.setattr(L, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(L, "_wave_cycle", lambda client, log: None)
    sentinel_executor = MB.RemoteCandidateExecutor(MB.LocalFakeAdapter())
    M.run_coast_cycle(log=lambda *a: None, executor=sentinel_executor)
    assert seen["wc"].promoted is not None and seen["wc"].promoted.executor is sentinel_executor
    import inspect
    src = inspect.getsource(M)
    assert "RemoteCandidateExecutor(VolumeMountAdapter(LIVE_CASES_MOUNT, cases_volume, evaluate_candidate))" in src
