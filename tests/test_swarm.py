"""The research loop on a toy problem, with a scripted LLM and an in-process evaluator."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from lab.core.stats import Interval, PairedResult
import httpx

from lab.memory import FullContextMemory, read_jsonl
from lab.sandbox.lint import lint
from lab.search import ChainSearch, OpenAlexSearch
from lab.swarm import Thresholds, decide, failure_digest, run_lab, source_hash
from lab.swarm.loop import IDENTICAL_REASON, change_comment, claim_text, normalized, parse_json
from swarm_fakes import (BETTER, BETTER_COMMENTED, BETTER_SLOPE, BROKEN, RAISE, SEED, SEED_RENAMED, TUNABLE,
                         WORSE, CannedSearch, FakeEvalPool, ScriptedLLM, ToyProblem, compare, evaluate, fence,
                         scout_json)


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
        assert f.search_backend == "canned" and f.source_relevance == "domain"
    assert findings[3].accepted and findings[3].select_delta < 0 and findings[3].became_incumbent is True
    assert not findings[2].accepted and findings[2].select_delta > 0 and findings[2].became_incumbent is False
    assert findings[3].text.startswith("[ACCEPTED, became incumbent] toy_line gen 3")
    assert "Inputs: x. Rule: regress y on x. Leads: all leads equally." in findings[3].claim
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
    not_adopted = events(tmp_path, "not_adopted")
    assert len(not_adopted) == 1 and not_adopted[0]["hash"] == source_hash(BETTER_SLOPE)
    findings = read_jsonl(tmp_path / "findings.jsonl")
    assert len(findings) == 3 and not any(f.supersedes for f in findings)   # one finding per proposal
    adopted = [f for f in findings if f.became_incumbent]
    passed = [f for f in findings if f.accepted and not f.became_incumbent]
    assert len(adopted) == 1 and len(passed) == 1
    assert passed[0].text.startswith("[passed, not adopted]") and "not adopted" in passed[0].text
    assert passed[0].select_delta != adopted[0].select_delta           # each keeps its own measurement
    # search off: claims come from the closed-book step
    assert all(f.origin == "closed_book" for f in findings)
    assert s["curve"][1]["accepted"] == 2 and s["curve"][1]["proposals"] == 3
    assert s["accepted_total"] == 2 and s["became_incumbent_total"] == 1 and s["findings_total"] == 3


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
    assert "[ACCEPTED, became incumbent] toy_line gen 1" in analyst    # recalled from the earlier run
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


# ----------------------------------------------------------------- one finding per proposal, duplicates


def test_identical_code_in_one_generation_is_marked_duplicate(toy, tmp_path):
    llm = ScriptedLLM({"engineer": [fence(BETTER), fence(BETTER)]})
    s = run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="none", search="none",
                generations=1, proposals=2, run_dir=tmp_path)
    f0, f1 = sorted(read_jsonl(tmp_path / "findings.jsonl"), key=lambda f: f.duplicate_of is not None)
    assert f0.accepted and f1.accepted and f0.select_delta == f1.select_delta
    assert f0.became_incumbent is True and f1.became_incumbent is False
    assert f1.duplicate_of == f0.id and f0.duplicate_of is None
    assert "Duplicate of proposal" in f1.text and "same code" in f1.text
    assert s["accepted_total"] == 2 and s["became_incumbent_total"] == 1 and s["duplicates_total"] == 1


def test_identical_predictions_count_as_duplicate(toy, tmp_path):
    renamed = BETTER.replace('name = "least_squares_line"', 'name = "other_name"').replace(
        "self.coef = ", "coef = ").replace("[0]\n", "[0]\n        self.coef = coef\n", 1)
    assert renamed != BETTER and source_hash(renamed) != source_hash(BETTER)
    llm = ScriptedLLM({"engineer": [fence(BETTER), fence(renamed)]})
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="none", search="none",
            generations=1, proposals=2, run_dir=tmp_path)
    fs = read_jsonl(tmp_path / "findings.jsonl")
    dup = [f for f in fs if f.duplicate_of]
    assert len(dup) == 1 and "identical predictions" in dup[0].text


def test_candidate_identical_to_incumbent_is_rejected_without_evaluation(toy, tmp_path):
    pool = FakeEvalPool(toy)
    llm = ScriptedLLM({"engineer": [fence(SEED_RENAMED)]})
    run_lab(toy, pool, compare, llm_backend=llm, memory="none", search="none", generations=1, proposals=1,
            run_dir=tmp_path)
    assert pool.submitted == [SEED]                       # only the incumbent was evaluated
    d = events(tmp_path, "decision")[0]
    assert d["accepted"] is False and d["reason"] == IDENTICAL_REASON and d["identical"] is True
    assert events(tmp_path, "identical_to_incumbent")
    f = read_jsonl(tmp_path / "findings.jsonl")[0]
    assert f.select_delta is None and "identical to the incumbent" in f.text


def test_normalized_ignores_whitespace_comments_and_name():
    assert normalized(SEED) == normalized(SEED_RENAMED)
    assert normalized(SEED) == normalized(SEED.replace("    ", "        ").replace("\n\n", "\n"))
    assert normalized(SEED) != normalized(BETTER)


def test_change_comment_and_claim_text():
    assert change_comment(BETTER_COMMENTED) == "fit a least-squares line in x"
    assert change_comment(BETTER) is None and change_comment(None) is None
    t = claim_text({"technique": "IB", "change": "add pressure term", "inputs": "pressure_hpa",
                    "rule": "-1 cm per hPa", "leads": "all", "expected_effect": "lower error"})
    assert t == "IB: add pressure term. Inputs: pressure_hpa. Rule: -1 cm per hPa. Leads: all. Expected: lower error."
    assert claim_text({"technique": "x", "rule": "r"}) == "x: r."


def test_engineer_records_change_comment(toy, tmp_path):
    llm = ScriptedLLM({"engineer": [fence(BETTER_COMMENTED)]})
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="none", search="none",
            generations=1, proposals=1, run_dir=tmp_path)
    assert events(tmp_path, "decision")[0]["change_comment"] == "fit a least-squares line in x"
    p = [c for c in llm.calls if c["role"] == "engineer"][0]["prompt"]
    assert "Implement ONLY the change in the claim" in p and "# Change:" in p


# ----------------------------------------------------------------- scout relevance and search fallback


def test_scout_unrelated_hit_falls_back_to_closed_book(toy, tmp_path):
    llm = ScriptedLLM({"scout": [scout_json("unrelated")]})
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="none", search=CannedSearch(),
            generations=1, proposals=1, run_dir=tmp_path)
    f = read_jsonl(tmp_path / "findings.jsonl")[0]
    assert f.origin == "closed_book" and f.source_relevance is None and f.search_backend is None
    assert events(tmp_path, "scout_no_usable_hit")
    assert "judge" not in llm.roles
    scout_prompt = [c for c in llm.calls if c["role"] == "scout"][0]["prompt"]
    assert '"domain"' in scout_prompt and "Prefer a \"domain\" result" in scout_prompt


def test_scout_method_hit_is_used_and_recorded(toy, tmp_path):
    llm = ScriptedLLM({"scout": [scout_json("method")]})
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="none", search=CannedSearch(),
            generations=1, proposals=1, run_dir=tmp_path)
    f = read_jsonl(tmp_path / "findings.jsonl")[0]
    assert f.origin == "search" and f.source_relevance == "method"


def test_scout_with_no_chosen_hit_falls_back(toy, tmp_path):
    llm = ScriptedLLM({"scout": [scout_json("domain", chosen=None)]})
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="none", search=CannedSearch(),
            generations=1, proposals=1, run_dir=tmp_path)
    assert read_jsonl(tmp_path / "findings.jsonl")[0].origin == "closed_book"


def test_rate_limited_search_falls_back_and_is_recorded(toy, tmp_path):
    def limited(request):
        return httpx.Response(429, json={"message": "Anonymous search is temporarily rate-limited. Please retry in 14s"})
    slept = []
    openalex = OpenAlexSearch(transport=httpx.MockTransport(limited), cache_dir=None, sleep=slept.append)
    chain = ChainSearch([openalex, CannedSearch()])
    s = run_lab(toy, FakeEvalPool(toy), compare, llm_backend=ScriptedLLM(), memory="none", search=chain,
                generations=1, proposals=1, run_dir=tmp_path)
    assert slept == [14.0, 14.0]
    f = read_jsonl(tmp_path / "findings.jsonl")[0]
    assert f.origin == "search" and f.search_backend == "canned"
    ev = events(tmp_path, "search")[0]
    assert ev["search_backend"] == "canned" and ev["failures"][0]["backend"] == "openalex"
    assert "429" in events(tmp_path, "search_error")[0]["error"]
    st = s["search"]
    assert st["chain"] == ["openalex", "canned"] and st["failed"] == 1 and st["retries"] == 2
    assert st["by_backend"]["openalex"]["failed"] == 1 and st["by_backend"]["canned"]["searches"] == 1
    assert json.loads((tmp_path / "config.json").read_text())["search_chain"] == ["openalex", "canned"]


# ----------------------------------------------------------------- baselines


def test_single_agent_mode(toy, tmp_path):
    llm = ScriptedLLM({"single_agent": [fence(WORSE), fence(BETTER_COMMENTED)]})
    s = run_lab(toy, FakeEvalPool(toy), compare, llm_backend=llm, memory="vector", search="openalex",
                generations=2, proposals=1, run_dir=tmp_path, mode="single_agent")
    assert set(llm.roles) == {"single_agent"} and len(llm.roles) == 2      # one call per proposal, nothing else
    p = llm.calls[0]["prompt"]
    assert "Breakdown by_lead" in p and "class Forecaster" in p and "Sandbox rules" in p
    assert not events(tmp_path, "recall") and not events(tmp_path, "search")
    fs = sorted(read_jsonl(tmp_path / "findings.jsonl"), key=lambda f: f.generation)
    assert [f.origin for f in fs] == ["single_agent", "single_agent"]
    assert not fs[0].accepted and fs[1].accepted and fs[1].became_incumbent
    assert fs[1].claim == "fit a least-squares line in x."
    assert s["mode"] == "single_agent" and s["incumbent_hash"] == source_hash(BETTER_COMMENTED)
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["mode"] == "single_agent" and cfg["search"] == "none"
    assert (tmp_path / "playbook.md").read_text().startswith("# Playbook for toy_line (written without a model")


def _mutation_run(toy, run_dir, seed=3):
    llm = ScriptedLLM()
    pool = FakeEvalPool(toy)
    s = run_lab(toy, pool, compare, llm_backend=llm, memory="none", search="none", generations=3, proposals=3,
                run_dir=run_dir, mode="random_mutation", start_source=TUNABLE, seed=seed)
    return s, llm, pool


def test_random_mutation_mode(toy, tmp_path):
    s, llm, pool = _mutation_run(toy, tmp_path / "a")
    assert llm.calls == [] and s["llm_by_role"] == {}                       # no model at all
    assert s["status"] == "finished" and s["generations_done"] == 3
    muts = events(tmp_path / "a", "mutation")
    assert len(muts) == 9 and {m["kind"] for m in muts} <= {"perturb", "drop_term"}
    assert all(lint(src) == [] for src in pool.submitted)
    decisions = events(tmp_path / "a", "decision")
    assert len(decisions) == 9 and all(d["reason"] for d in decisions)
    fs = read_jsonl(tmp_path / "a" / "findings.jsonl")
    assert len(fs) == 9 and all(f.origin == "random_mutation" for f in fs)
    assert all(f.claim.startswith("Random mutation (") for f in fs)
    assert sum(bool(f.became_incumbent) for f in fs) == s["became_incumbent_total"]
    # a mutation never re-submits the incumbent itself
    inc_hashes = {c["incumbent_hash"] for c in s["curve"]}
    for d, m in zip(decisions, muts):
        assert d["hash"] not in inc_hashes or d["accepted"]
    # same seed, same mutations
    s2, _, pool2 = _mutation_run(toy, tmp_path / "b")
    assert pool2.submitted == pool.submitted and s2["curve"][-1]["select"] == s["curve"][-1]["select"]


# ----------------------------------------------------------------- shuffled notebook


def test_shuffle_memory_permutes_imported_outcomes(toy, tmp_path):
    cold = tmp_path / "cold"
    run_lab(toy, FakeEvalPool(toy), compare,
            llm_backend=ScriptedLLM({"engineer": [fence(WORSE), fence(BETTER), fence(BROKEN)],
                                     "repair": [fence(BROKEN)]}),
            memory="vector", search="none", generations=3, proposals=1, run_dir=cold)
    original = {f.id: f for f in read_jsonl(cold / "findings.jsonl")}
    warm = tmp_path / "warm"
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=ScriptedLLM(), memory="vector", search="none",
            generations=1, proposals=1, run_dir=warm, start_memory=cold, shuffle_memory=True, seed=5)
    cfg = json.loads((warm / "config.json").read_text())
    assert cfg["shuffle_memory"] is True
    assert events(warm, "run_start")[0]["shuffled"] is True
    imported = [f for f in read_jsonl(warm / "memory.jsonl") if f.id in original]
    assert len(imported) == 3
    def outcome(f):
        return (f.accepted, f.became_incumbent, f.select_delta, f.confirm_delta)
    assert sorted(map(outcome, imported), key=repr) == sorted(map(outcome, original.values()), key=repr)
    assert any(outcome(f) != outcome(original[f.id]) for f in imported)
    for f in imported:
        assert f.claim == original[f.id].claim
        assert f.text == f.render()                           # text rebuilt from the shuffled fields
    # the unshuffled warm run imports the outcomes untouched
    plain = tmp_path / "plain"
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=ScriptedLLM(), memory="vector", search="none",
            generations=1, proposals=1, run_dir=plain, start_memory=cold)
    for f in read_jsonl(plain / "memory.jsonl"):
        if f.id in original:
            assert outcome(f) == outcome(original[f.id])


def test_mutate_unit():
    import random as _r

    from lab.swarm.mutate import mutate
    kinds, seen = set(), set()
    for seed in range(40):
        m = mutate(TUNABLE, _r.Random(seed))
        assert m is not None and lint(m.source) == [] and normalized(m.source) != normalized(TUNABLE)
        kinds.add(m.kind)
        seen.add(m.source)
        if m.kind == "drop_term":
            assert m.description.startswith("drop term at line")
    assert kinds == {"perturb", "drop_term"} and len(seen) > 20
    assert mutate(TUNABLE, _r.Random(7)).source == mutate(TUNABLE, _r.Random(7)).source
    assert mutate(SEED, _r.Random(0)) is None                 # no float constants and no sums: nothing to do


# ----------------------------------------------------------------- run index for the dashboard


@pytest.fixture
def lab_root(toy, tmp_path):
    root = tmp_path / "lab"
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=ScriptedLLM(), memory="none", search="none",
            generations=1, proposals=1, runs_root=root)
    return root


def test_runs_index_lists_and_details(lab_root):
    from lab.swarm.runs_index import list_runs, run_detail
    rows = list_runs(lab_root)
    assert len(rows) == 1
    r = rows[0]
    assert r["problem"] == "toy_line" and r["status"] == "finished" and r["generations_done"] == 1
    assert r["mode"] == "swarm" and r["incumbent_select"] < r["incumbent_confirm"] + 10 and r["cost_usd"] == 0.0
    assert r["started_at"] and r["updated_at"] and r["became_incumbent_total"] == 1
    d = run_detail(lab_root, "toy_line", r["run_id"])
    assert d["summary"]["run_id"] == r["run_id"] and d["config"]["problem"] == "toy_line"
    assert len(d["findings"]) == 1 and d["findings"][0]["became_incumbent"] is True
    assert d["incumbent_source"] == BETTER
    assert run_detail(lab_root, "toy_line", "nope") is None
    assert run_detail(lab_root, "..", r["run_id"]) is None and run_detail(lab_root, "toy_line", "../x") is None
    assert list_runs(lab_root / "missing") == []


def test_runs_index_tolerates_files_mid_write(lab_root):
    from lab.swarm.runs_index import list_runs, run_detail
    run_dir = next((lab_root / "toy_line").iterdir())
    with (run_dir / "findings.jsonl").open("a") as fh:
        fh.write('{"claim": "half a li')                    # a line still being written
    (run_dir / "summary.json").write_text('{"run_id": "x", "stat')
    row = list_runs(lab_root)[0]
    assert row["status"] == "unreadable" and row["mode"] == "swarm" and row["generations_planned"] == 1
    d = run_detail(lab_root, "toy_line", run_dir.name)
    assert d["summary"] is None and d["summary_readable"] is False and len(d["findings"]) == 1
    (lab_root / "toy_line" / "fresh-run").mkdir()
    (lab_root / "toy_line" / "fresh-run" / "config.json").write_text('{"mode": "single_agent", "generations": 4}')
    starting = [r for r in list_runs(lab_root) if r["run_id"] == "fresh-run"][0]
    assert starting["status"] == "starting" and starting["mode"] == "single_agent"


def test_server_lab_endpoints(lab_root, monkeypatch):
    from fastapi.testclient import TestClient

    import lab.server as server
    monkeypatch.setattr(server, "LAB_RUNS", lab_root)
    client = TestClient(server.app)
    r = client.get("/api/lab/runs")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    runs = r.json()["runs"]
    assert len(runs) == 1 and runs[0]["problem"] == "toy_line"
    d = client.get(f"/api/lab/runs/toy_line/{runs[0]['run_id']}")
    assert d.status_code == 200
    body = d.json()
    assert body["summary"]["status"] == "finished" and body["incumbent_source"] == BETTER
    assert len(body["findings"]) == 1
    assert client.get("/api/lab/runs/toy_line/missing").status_code == 404
