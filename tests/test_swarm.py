"""The research loop on a toy problem, with a scripted LLM and an in-process evaluator."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from lab.core.stats import Interval, PairedResult
from lab.memory import FullContextMemory, read_jsonl
from lab.swarm import Thresholds, decide, failure_digest, run_lab, source_hash
from lab.swarm.loop import parse_json
from swarm_fakes import (BETTER, BETTER_SLOPE, BROKEN, RAISE, SEED, WORSE, CannedSearch, FakeEvalPool,
                         ScriptedLLM, ToyProblem, compare, evaluate, fence)


def events(run_dir, kind=None):
    rows = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    return [r for r in rows if kind is None or r["event"] == kind]


@pytest.fixture(scope="module")
def toy():
    return ToyProblem()


# ----------------------------------------------------------------- the main story


@pytest.fixture(scope="module")
def story(toy, tmp_path_factory):
    """Gen 1: broken (repair also broken). Gen 2: worse. Gen 3: better."""
    run_dir = tmp_path_factory.mktemp("story")
    llm = ScriptedLLM({"engineer": [fence(BROKEN), fence(WORSE), fence(BETTER)], "repair": [fence(BROKEN)]})
    search = CannedSearch()
    summary = run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="vector", search=search,
                      generations=3, proposals=1, run_dir=run_dir)
    return run_dir, summary, llm, search


def test_run_directory_layout(story):
    run_dir, summary, _, _ = story
    for name in ("config.json", "events.jsonl", "incumbent.py", "findings.jsonl", "summary.json",
                 "playbook.md", "memory.jsonl"):
        assert (run_dir / name).exists(), name
    for src in (SEED, BROKEN, WORSE, BETTER):
        assert (run_dir / "candidates" / f"{source_hash(src)}.py").read_text() == src
    cfg = json.loads((run_dir / "config.json").read_text())
    assert cfg["problem"] == "toy_line" and cfg["memory"] == "vector" and cfg["search"] == "canned"
    assert summary["status"] == "finished" and summary["generations_done"] == 3


def test_broken_candidate_is_logged_and_run_continues(story):
    run_dir, _, llm, _ = story
    d1 = [e for e in events(run_dir, "decision") if e["generation"] == 1][0]
    assert d1["accepted"] is False and d1["reason"] == "evaluation failed"
    assert "KeyError" in d1["error"]
    assert llm.roles.count("repair") == 1          # exactly one repair attempt
    evals = [e for e in events(run_dir, "evaluation") if e.get("generation") == 1 and not e["ok"]]
    assert len(evals) == 2 and {e["repaired"] for e in evals} == {False, True}
    f1 = [f for f in read_jsonl(run_dir / "findings.jsonl") if f.generation == 1][0]
    assert f1.accepted is False and f1.select_delta is None and "Failure" in f1.text


def test_worse_candidate_rejected_with_reason(story):
    run_dir, _, _, _ = story
    d2 = [e for e in events(run_dir, "decision") if e["generation"] == 2][0]
    assert d2["accepted"] is False
    assert d2["reason"].startswith("rejected: ")
    assert "P(candidate better)" in d2["reason"] and "confirm" in d2["reason"]


def test_better_candidate_accepted_and_becomes_incumbent(story, toy):
    run_dir, summary, _, _ = story
    d3 = [e for e in events(run_dir, "decision") if e["generation"] == 3][0]
    assert d3["accepted"] is True and d3["reason"].startswith("accepted")
    assert (run_dir / "incumbent.py").read_text() == BETTER
    assert summary["incumbent_hash"] == source_hash(BETTER)
    assert [e["hash"] for e in events(run_dir, "new_incumbent")] == [source_hash(BETTER)]


def test_findings_have_measured_deltas(story, toy):
    run_dir, _, _, _ = story
    splits = toy.splits()
    seed_res, worse_res, better_res = (evaluate(toy, s, splits) for s in (SEED, WORSE, BETTER))
    findings = {f.generation: f for f in read_jsonl(run_dir / "findings.jsonl")}
    assert set(findings) == {1, 2, 3}
    for gen, res in ((2, worse_res), (3, better_res)):
        sel = compare(res, seed_res, splits, "select")
        conf = compare(res, seed_res, splits, "confirm")
        f = findings[gen]
        assert f.select_delta == pytest.approx(sel.diff.mean)
        assert f.select_low == pytest.approx(sel.diff.low) and f.select_high == pytest.approx(sel.diff.high)
        assert f.confirm_delta == pytest.approx(conf.diff.mean)
        assert f.origin == "search" and f.source_url == "https://example.org/ls"
        assert f.known_closed_book is True and f.problem == "toy_line"
    assert findings[3].accepted and findings[3].select_delta < 0
    assert not findings[2].accepted and findings[2].select_delta > 0
    # the notebook backend got the same findings
    assert {f.id for f in read_jsonl(run_dir / "memory.jsonl")} == {f.id for f in findings.values()}


def test_summary_has_curve(story, toy):
    run_dir, summary, _, _ = story
    on_disk = json.loads((run_dir / "summary.json").read_text())
    assert on_disk == summary
    curve = summary["curve"]
    assert [c["generation"] for c in curve] == [0, 1, 2, 3]
    seed_res = evaluate(toy, SEED, toy.splits())
    assert curve[0]["select"] == pytest.approx(seed_res.summary["select"]["mean_loss"])
    assert curve[2]["select"] == curve[0]["select"]
    assert curve[3]["select"] < 0.2 * curve[0]["select"]
    assert curve[3]["confirm"] < curve[0]["confirm"]
    assert [c["evaluations"] for c in curve] == [1, 3, 4, 5]
    assert summary["evaluations"] == 5 and summary["search"]["searches"] == 3
    assert set(summary["llm_by_role"]) >= {"analyst", "closed_book", "scout", "judge", "engineer", "playbook"}
    assert summary["recall"]["calls"] == 3


def test_agents_see_digest_not_truths(story, toy):
    _, _, llm, _ = story
    analyst = [c for c in llm.calls if c["role"] == "analyst"]
    assert "Breakdown by_lead" in analyst[0]["prompt"]
    truth = toy.splits().select.truths[0]["y"]
    assert all(repr(truth) not in c["prompt"] for c in llm.calls)


def test_engineer_prompt_restates_sandbox_rules(story):
    _, _, llm, _ = story
    p = [c for c in llm.calls if c["role"] == "engineer"][0]["prompt"]
    assert "Sandbox rules" in p and "COMPLETE" in p and "class Forecaster" in p and "'y'" in p


def test_later_proposals_recall_earlier_findings(story):
    run_dir, _, _, _ = story
    recalls = events(run_dir, "recall")
    assert recalls[0]["n"] == 0 and recalls[-1]["n"] >= 1


def test_playbook_written_by_model(story):
    run_dir, _, _, _ = story
    assert (run_dir / "playbook.md").read_text().startswith("# Playbook")


# ----------------------------------------------------------------- concurrency, failures


def test_concurrent_generation_with_llm_failure(toy, tmp_path):
    llm = ScriptedLLM({"analyst": [RAISE, *(['{"hypothesis": "h", "knowledge_gap": "q"}'] * 3)],
                       "engineer": [fence(WORSE), fence(BETTER), fence(BETTER_SLOPE)]})
    s = run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="full_context", search="none",
                generations=1, proposals=4, run_dir=tmp_path)
    assert s["status"] == "finished"
    failed = events(tmp_path, "proposal_failed")
    assert len(failed) == 1 and "LLMError" in failed[0]["error"]
    assert len(events(tmp_path, "llm_error")) == 1
    decisions = events(tmp_path, "decision")
    assert len(decisions) == 3 and sum(d["accepted"] for d in decisions) == 2
    # strategies differ across proposals
    assert len({c["prompt"].split("# Your direction for this proposal")[1][:80]
                for c in llm.calls if c["role"] == "analyst"}) == 4
    assert (tmp_path / "incumbent.py").read_text() == BETTER
    sup = events(tmp_path, "superseded_this_generation")
    assert len(sup) == 1 and sup[0]["hash"] == source_hash(BETTER_SLOPE)
    findings = read_jsonl(tmp_path / "findings.jsonl")
    note = [f for f in findings if f.supersedes]
    assert len(note) == 1 and "not adopted" in note[0].text
    # search off: claims come from the closed-book step
    assert all(f.origin == "closed_book" for f in findings)
    assert s["curve"][1]["accepted"] == 2 and s["curve"][1]["proposals"] == 3


def test_cost_cap_stops_run_cleanly(toy, tmp_path):
    llm = ScriptedLLM({"engineer": [fence(WORSE)]}, cost_per_call=0.01)
    s = run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="none", search="none",
                generations=5, proposals=1, run_dir=tmp_path, max_cost_usd=0.05)
    assert s["status"] == "stopped_cost_cap"
    assert s["generations_done"] < 5
    assert s["cost_usd"] <= 0.05 + 1e-9
    assert events(tmp_path, "cost_cap")
    assert json.loads((tmp_path / "summary.json").read_text())["status"] == "stopped_cost_cap"
    assert (tmp_path / "playbook.md").exists()      # mechanical playbook, no extra spend
    assert "playbook" not in llm.roles


def test_resume_does_not_redo_finished_generations(toy, tmp_path):
    llm = ScriptedLLM({"engineer": [fence(WORSE), fence(BETTER)]})
    s1 = run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="vector", search="none",
                 generations=2, proposals=1, run_dir=tmp_path)
    assert s1["generations_done"] == 2 and s1["incumbent_hash"] == source_hash(BETTER)
    llm2 = ScriptedLLM({"engineer": [fence(WORSE)]})
    pool2 = FakeEvalPool(toy)
    s2 = run_lab(toy, pool2, compare, llm_backend=llm2, memory="vector", search="none",
                 generations=3, proposals=1, run_dir=tmp_path, resume=True)
    assert s2["generations_done"] == 3 and s2["status"] == "finished"
    assert [c["generation"] for c in s2["curve"]] == [0, 1, 2, 3]
    starts = [e["generation"] for e in events(tmp_path, "generation_start")]
    assert starts == [1, 2, 3]
    assert events(tmp_path, "run_resume")
    assert pool2.submitted[0] == BETTER           # resumed from the saved incumbent
    assert llm2.roles.count("engineer") == 1        # only generation 3 was run
    assert s2["evaluations"] == s1["evaluations"] + 2
    # the notebook was reloaded from disk: gen 3's analyst saw earlier findings
    assert len(read_jsonl(tmp_path / "findings.jsonl")) == 3
    assert events(tmp_path, "recall")[-1]["n"] >= 1


def test_warm_start_carries_findings_and_playbook(toy, tmp_path):
    cold = tmp_path / "cold"
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=ScriptedLLM({"engineer": [fence(BETTER)]}),
            memory="vector", search="none", generations=1, proposals=1, run_dir=cold)
    llm = ScriptedLLM({"engineer": [fence(WORSE)]})
    warm = tmp_path / "warm"
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="vector", search="none",
            generations=1, proposals=1, run_dir=warm, start_memory=cold,
            playbook=(cold / "playbook.md").read_text())
    analyst = [c for c in llm.calls if c["role"] == "analyst"][0]["prompt"]
    engineer = [c for c in llm.calls if c["role"] == "engineer"][0]["prompt"]
    assert "Playbook from earlier runs" in analyst and "Fitting a line in x paid off" in analyst
    assert "Fitting a line in x paid off" in engineer
    assert "[ACCEPTED] toy_line gen 1" in analyst            # recalled from the earlier run
    assert len(read_jsonl(warm / "findings.jsonl")) == 1      # this run's own findings only
    assert len(read_jsonl(warm / "memory.jsonl")) == 2        # notebook includes the imported one
    assert json.loads((warm / "config.json").read_text())["playbook_given"] is True


def test_memory_object_can_be_injected(toy, tmp_path):
    mem = FullContextMemory(tmp_path / "shared.jsonl")
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=ScriptedLLM(), memory=mem, search="none",
            generations=1, proposals=2, run_dir=tmp_path / "run")
    assert len(read_jsonl(tmp_path / "shared.jsonl")) >= 2


# ----------------------------------------------------------------- pieces


def _pr(mean_a, mean_b, diff, p):
    return PairedResult(mean_a, mean_b, Interval(diff, diff - 0.1, diff + 0.1, 10), p, 100)


def test_decide_rule():
    th = Thresholds()
    good_sel, good_conf = _pr(9.0, 10.0, -1.0, 0.99), _pr(9.5, 10.0, -0.5, 0.9)
    assert decide(True, good_sel, good_conf, th)[0] is True
    assert decide(False, good_sel, good_conf, th) == (False, "evaluation failed")
    ok, why = decide(True, _pr(9.0, 10.0, -1.0, 0.90), good_conf, th)
    assert not ok and "P(candidate better) 0.900 < 0.95" in why
    ok, why = decide(True, _pr(9.99, 10.0, -0.01, 0.99), good_conf, th)      # 0.1% < 0.3%
    assert not ok and "relative improvement" in why
    ok, why = decide(True, good_sel, _pr(10.1, 10.0, 0.1, 0.1), th)
    assert not ok and "confirm" in why
    ok, _ = decide(True, _pr(9.99, 10.0, -0.01, 0.99), good_conf, Thresholds(min_rel_improvement=0.0005))
    assert ok
    ok, why = decide(True, _pr(math.nan, math.nan, math.nan, math.nan), good_conf, th)
    assert not ok


def test_failure_digest_aggregates_only(toy):
    res = evaluate(toy, SEED, toy.splits())
    d = failure_digest(res, "select", toy.loss_name)
    assert "mean_loss" in d and "Breakdown by_lead" in d and "1h" in d and "90th pct" in d
    assert "Highest-loss groups: by_lead" in d
    assert str(toy.splits().select.cases[0].case_id) not in d


def test_parse_json():
    assert parse_json('Sure.\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    assert parse_json("no json here") is None


def test_incumbent_that_fails_raises(toy, tmp_path):
    with pytest.raises(RuntimeError, match="incumbent failed"):
        run_lab(toy, FakeEvalPool(toy), compare, llm_backend=ScriptedLLM(), memory="none", search="none",
                generations=1, proposals=1, run_dir=tmp_path, start_source=BROKEN)
    assert json.loads((tmp_path / "summary.json").read_text())["status"] == "failed"


def test_evaluator_contract_fake_matches(toy):
    """The fake returns the fields the loop relies on from lab.evalpool.evaluate.EvalResult."""
    res = FakeEvalPool(toy).map([SEED])[0]
    for name in ("ok", "error", "lint", "duration_s", "protections", "source_hash", "losses", "summary"):
        assert hasattr(res, name)
    assert isinstance(res.losses["select"], np.ndarray)
    assert compare(res, res, toy.splits(), "select").diff.mean == 0.0
