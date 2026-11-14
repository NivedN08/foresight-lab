"""Evaluator, pool and final test on a small synthetic problem. Every
evaluation starts a real sandbox child process."""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import lab.evalpool.evaluate as E
import lab.sandbox.runner as runner
from lab.core.problem import Splits, Window
from lab.core.task import Case
from lab.evalpool import EvalPool, HeldOutWindowRefused, compare
from lab.evalpool.evaluate import evaluate
from lab.experiments import final_test

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SENTINEL = "SENTINEL-c0a57-EVAL-TRUTH"


class SynthProblem:
    """y = 2 x + 3 + noise; blocks are 'station-days' of 6 cases."""
    name = "synth"
    title = "A tiny synthetic problem."
    target_key = "y"
    loss_name = "absolute error"

    def __init__(self, sentinel: bool = False):
        self.sentinel = sentinel
        self._splits = None

    def splits(self) -> Splits:
        if self._splits is None:
            rng = np.random.default_rng(0)
            wins, k = {}, 0
            for name, n in (("train", 60), ("select", 30), ("confirm", 30), ("test", 30)):
                cases, truths, blocks = [], [], []
                for _ in range(n):
                    x = float(rng.normal())
                    lead = [1, 6][len(cases) % 2]
                    issue = T0 + timedelta(hours=2 * k)
                    cases.append(Case(f"c{k}", issue, issue + timedelta(hours=lead),
                                      {"x": x, "lead_h": lead, "station": "A"}))
                    t = {"y": 2 * x + 3 + float(rng.normal(0, 0.1))}
                    if self.sentinel and name != "train":
                        t["tag"] = SENTINEL
                    truths.append(t)
                    blocks.append(f"A|{len(blocks) // 6}")
                    k += 4
                wins[name] = Window(cases, truths, blocks)
            self._splits = Splits(**wins)
            self._splits.check_order()
        return self._splits

    def losses(self, predictions, truths):
        return np.array([abs(p["y"] - t["y"]) for p, t in zip(predictions, truths)], dtype=float)

    def summarize(self, cases, predictions, truths):
        loss = self.losses(predictions, truths)
        leads = np.array([c.inputs["lead_h"] for c in cases])
        return {"mean_loss": float(loss.mean()), "n": int(loss.size),
                "by_lead": {str(h): {"mean_loss": float(loss[leads == h].mean()), "n": int((leads == h).sum())}
                            for h in sorted(set(leads.tolist()))}}

    def seed_source(self):
        return LINEAR

    def baseline_sources(self):
        return {"zero": ZERO, "mean": MEAN}


ZERO = "class Forecaster:\n    name = 'zero'\n    def fit(self, cases, truths):\n        pass\n" \
       "    def predict(self, case):\n        return {'y': 0.0}\n"
MEAN = textwrap.dedent('''
    class Forecaster:
        name = "mean"
        def fit(self, cases, truths):
            self.m = sum(t["y"] for t in truths) / len(truths)
        def predict(self, case):
            return {"y": self.m}
''')
LINEAR = textwrap.dedent('''
    import numpy as np

    class Forecaster:
        name = "linear"
        def fit(self, cases, truths):
            x = np.array([c.inputs["x"] for c in cases])
            y = np.array([t["y"] for t in truths])
            self.b, self.a = np.polyfit(x, y, 1)
        def predict(self, case):
            return {"y": float(self.a + self.b * case.inputs["x"])}
''')


# ------------------------------------------------------------------ evaluate
def test_evaluate_scores_select_and_confirm():
    p = SynthProblem()
    sp = p.splits()
    r = evaluate(p, LINEAR, sp)
    assert r.ok, r.error
    assert r.windows == ("select", "confirm") and set(r.losses) == {"select", "confirm"}
    assert len(r.losses["select"]) == len(sp.select) and r.summary["select"]["mean_loss"] < 0.2
    assert r.summary["confirm"]["mean_loss"] == pytest.approx(float(np.mean(r.losses["confirm"])))
    assert "lint" in r.protections and len(r.source_hash) == 64
    z = evaluate(p, ZERO, sp, ("select",))
    assert z.summary["select"]["mean_loss"] > 0.5 and set(z.losses) == {"select"}


def test_evaluation_truths_never_reach_the_child(monkeypatch):
    p = SynthProblem(sentinel=True)
    sp = p.splits()
    sent: list[bytes] = []
    real = runner.payload_from_prepared

    def spy(source, prepared):
        b = real(source, prepared)
        sent.append(b)
        return b

    monkeypatch.setattr(runner, "payload_from_prepared", spy)
    E._PREPARED.clear()
    for windows in (("select", "confirm"), ("test",)):
        r = evaluate(p, MEAN, sp, windows, allow_test=True)
        assert r.ok, r.error
    assert len(sent) == 2
    for b in sent:
        assert SENTINEL.encode() not in b
        payload = json.loads(b)
        assert set(payload) == {"source", "allowed_imports", "train_cases", "train_truths", "eval_cases"}
        assert len(payload["train_truths"]) == len(sp.train)
        assert payload["train_truths"][0]["y"] == pytest.approx(sp.train.truths[0]["y"])
    assert len(json.loads(sent[0])["eval_cases"]) == len(sp.select) + len(sp.confirm)
    # the sentinel really is in the parent's truths, and losses still use them
    assert sp.select.truths[0]["tag"] == SENTINEL


def test_test_window_is_refused_by_default():
    p = SynthProblem()
    sp = p.splits()
    for windows in (("test",), ("select", "test")):
        with pytest.raises(HeldOutWindowRefused):
            evaluate(p, MEAN, sp, windows)
    with pytest.raises(ValueError):
        evaluate(p, MEAN, sp, ("train",))
    pool = EvalPool(p, sp, max_workers=1)
    with pytest.raises(HeldOutWindowRefused):
        pool.submit(MEAN, ("test",))
    pool.close()
    r = evaluate(p, MEAN, sp, ("test",), allow_test=True)
    assert r.ok and set(r.losses) == {"test"}


def test_failed_candidate_gives_no_losses():
    p = SynthProblem()
    r = evaluate(p, "import os\nclass Forecaster:\n    pass\n", p.splits())
    assert not r.ok and r.lint and r.losses == {} and r.summary == {}


def test_compare_resamples_the_window_blocks(monkeypatch):
    p = SynthProblem()
    sp = p.splits()
    a, b = evaluate(p, LINEAR, sp), evaluate(p, MEAN, sp)
    seen = {}
    real = E.paired_block_bootstrap

    def spy(x, y, blocks, **kw):
        seen["blocks"] = list(blocks)
        return real(x, y, blocks, **kw)

    monkeypatch.setattr(E, "paired_block_bootstrap", spy)
    pr = compare(a, b, sp, "select")
    assert seen["blocks"] == sp.select.blocks
    assert pr.diff.n == len(set(sp.select.blocks)) == 5        # count of blocks, not cases
    assert pr.n == len(sp.select)
    assert pr.diff.high < 0 and pr.p_a_better == 1.0           # linear beats the mean
    assert compare(b, a, sp, "confirm").diff.low > 0


# ---------------------------------------------------------------------- pool
def test_pool_runs_in_parallel_and_caches():
    p = SynthProblem()
    pool = EvalPool(p, p.splits(), max_workers=3)
    f1 = pool.submit(LINEAR)
    f2 = pool.submit(LINEAR)
    assert f1 is f2
    res = pool.map([MEAN, LINEAR, ZERO, MEAN])
    assert pool.runs == 3
    assert res[1] is f1.result() and res[0] is res[3]
    assert all(r.ok for r in res)
    assert res[1].summary["select"]["mean_loss"] < res[0].summary["select"]["mean_loss"]
    pool.submit(LINEAR, ("select",)).result()     # different windows: a new run
    assert pool.runs == 4
    pool.close()


# ---------------------------------------------------------------- final test
def test_final_test_writes_hashes_and_refuses_a_repeat(tmp_path):
    p = SynthProblem()
    cand = tmp_path / "cand.py"
    cand.write_text(LINEAR)
    out = tmp_path / "final"
    lines = []
    rec = final_test.run(p, [str(cand)], out_dir=out, log=lines.append)
    saved = json.loads((out / rec["path"].split("/")[-1]).read_text())
    assert saved["candidates"][0]["sha256"] == E.source_hash(LINEAR)
    assert set(saved["baselines"]) == {"zero", "mean"}
    assert "lab/evalpool/evaluate.py" in saved["evaluator_sha256"]
    assert "lab/sandbox/_child.py" in saved["evaluator_sha256"]
    assert any(k.endswith("test_evalpool.py") for k in saved["problem_sha256"])
    assert all(len(h) == 64 for h in {**saved["evaluator_sha256"], **saved["problem_sha256"]}.values())
    assert saved["best_baseline"] == "baseline:mean"
    sc = saved["scores"][str(cand)]
    assert sc["overall"]["low"] <= sc["overall"]["mean"] <= sc["overall"]["high"]
    assert set(sc["by_lead"]) == {"1", "6"} and sc["overall"]["blocks"] == 5
    assert saved["paired_vs_best_baseline"][str(cand)]["diff"] < 0
    assert saved["previous_looks"] == 0
    log = (out / "log.jsonl").read_text().splitlines()
    assert len(log) == 1 and json.loads(log[0])["candidates"][0]["sha256"] == E.source_hash(LINEAR)
    assert any("looked at 0 time" in x for x in lines)

    # the same source again (even under another file name) is refused
    copy = tmp_path / "copy.py"
    copy.write_text(LINEAR)
    with pytest.raises(final_test.AlreadyScored):
        final_test.run(p, [str(copy)], out_dir=out, log=lines.append)
    assert len((out / "log.jsonl").read_text().splitlines()) == 1
    assert any("looked at 1 time" in x for x in lines)

    # --again is allowed and counted
    rec2 = final_test.run(p, [str(copy)], out_dir=out, again=True, log=lines.append)
    assert rec2["previous_looks"] == 1 and rec2["again"] is True
    assert len((out / "log.jsonl").read_text().splitlines()) == 2


def test_final_test_cli_refuses_repeat(tmp_path, monkeypatch, capsys):
    p = SynthProblem()
    cand = tmp_path / "c.py"
    cand.write_text(MEAN)
    monkeypatch.setattr(final_test, "OUT_DIR", tmp_path / "final")
    monkeypatch.setattr("lab.evalpool.registry.get_problem", lambda name, **kw: p)
    final_test.main(["--problem", "synth", "--candidate", str(cand)])
    assert len(list((tmp_path / "final").glob("synth_*.json"))) == 1
    with pytest.raises(SystemExit) as e:
        final_test.main(["--problem", "synth", "--candidate", str(cand)])
    assert e.value.code == 2
    assert "refused" in capsys.readouterr().err
