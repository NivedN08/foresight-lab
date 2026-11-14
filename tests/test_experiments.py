"""The experiment runner and report, on the toy problem with the fake LLM.

Matrix runs start real subprocesses (`python -m lab.experiments.matrix _worker`);
the factories below are what those workers import instead of the real problem,
evaluator and model (tests/ is put on their PYTHONPATH)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.experiments import report as rpt
from lab.experiments.matrix import (CARRY, check_config, cheat_sheet, dry_run, load_config, measured_rates, plan,
                                    run_matrix, run_state)
from lab.memory import read_jsonl
from swarm_fakes import BETTER, WORSE, FakeEvalPool, ScriptedLLM, ToyProblem, compare, fence

TESTS = Path(__file__).resolve().parent
CONFIGS = TESTS.parent / "lab" / "experiments" / "configs"


# ----------------------------------------------------------------- factories the workers import


def make_problem():
    return ToyProblem()


def make_evaluator(problem):
    return FakeEvalPool(problem), compare, problem.splits()


def make_llm(spec):
    # seed 1 finds the better line in generation 2, seed 2 in generation 1
    script = [fence(WORSE), fence(BETTER)] if spec["seed"] == 1 else [fence(BETTER)]
    return ScriptedLLM({"engineer": script, "single_agent": script}, cost_per_call=0.01)


def config(name="m", arms=None, generations=2, seeds=(1, 2)):
    cfg = {"name": name, "problem": "toy_line",
           "budget": {"generations": generations, "proposals": 1},
           "seeds": list(seeds), "pythonpath": [str(TESTS)],
           "factories": {"problem": "test_experiments:make_problem",
                         "evaluator": "test_experiments:make_evaluator",
                         "llm": "test_experiments:make_llm"},
           "arms": arms or [{"name": "swarm", "mode": "swarm", "memory": "vector", "search": "none"},
                            {"name": "single_agent", "mode": "single_agent", "memory": "none", "search": "none"}]}
    check_config(cfg)
    return cfg


def events(run_dir, kind):
    return [json.loads(x) for x in (Path(run_dir) / "events.jsonl").read_text().splitlines()
            if json.loads(x)["event"] == kind]


# ----------------------------------------------------------------- the matrix


@pytest.fixture(scope="module")
def matrix_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("exp")
    out = []
    runs = run_matrix(config(), root=root, parallel=2, echo=out.append)
    return root, runs, out


def test_two_arms_two_seeds_run(matrix_root):
    root, runs, out = matrix_root
    assert [(r.arm, r.seed, r.action) for r in runs] == [("swarm", 1, "new"), ("swarm", 2, "new"),
                                                         ("single_agent", 1, "new"), ("single_agent", 2, "new")]
    for arm in ("swarm", "single_agent"):
        for seed in (1, 2):
            d = root / "m" / arm / f"seed{seed}"
            s = json.loads((d / "summary.json").read_text())
            # the arm named "swarm" uses mode "swarm", the old name of the pipeline mode
            assert s["status"] == "finished" and s["generations_done"] == 2
            assert s["mode"] == {"swarm": "pipeline"}.get(arm, arm)
            assert json.loads((d / "spec.json").read_text())["seed"] == seed
            assert (d / "worker.log").exists()
            assert run_state(d) == "finished"
    man = json.loads((root / "m" / "manifest.json").read_text())
    assert {r["key"]: r["status"] for r in man["runs"]} == {
        "swarm/seed1": "finished", "swarm/seed2": "finished",
        "single_agent/seed1": "finished", "single_agent/seed2": "finished"}
    assert all(r["returncode"] == 0 for r in man["runs"]) and man["config"]["name"] == "m"
    assert any("exit 0" in line for line in out)


def test_second_invocation_skips_finished(matrix_root):
    root, _, _ = matrix_root
    before = {p: p.stat().st_size for p in (root / "m").rglob("events.jsonl")}
    out = []
    runs = run_matrix(config(), root=root, parallel=2, echo=out.append)
    assert {r.action for r in runs} == {"done"}
    assert {p: p.stat().st_size for p in (root / "m").rglob("events.jsonl")} == before
    assert all("already finished" in line for line in out)


def test_interrupted_run_resumes(tmp_path):
    from lab.swarm import run_lab
    toy = ToyProblem()
    run_dir = tmp_path / "m" / "swarm" / "seed1"
    run_lab(toy, FakeEvalPool(toy), compare, llm_backend=make_llm({"seed": 1}), memory="vector", search="none",
            generations=1, proposals=1, seed=1, run_dir=run_dir)
    s = json.loads((run_dir / "summary.json").read_text())
    s.update(status="running", generations_planned=2)          # as if the process died after generation 1
    (run_dir / "summary.json").write_text(json.dumps(s))
    cfg = config(arms=[{"name": "swarm", "mode": "swarm", "memory": "vector", "search": "none"}], seeds=[1])
    assert [r.action for r in plan(cfg, tmp_path)] == ["resume"]
    run_matrix(cfg, root=tmp_path, parallel=1, echo=lambda s: None)
    assert [e["generation"] for e in events(run_dir, "generation_start")] == [1, 2]
    assert events(run_dir, "run_resume")
    assert json.loads((run_dir / "summary.json").read_text())["generations_done"] == 2


def test_missing_key_arm_is_skipped_with_message(tmp_path, monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("PAST_API_KEY", raising=False)
    cfg = config(arms=[{"name": "search_exa", "mode": "swarm", "search": "exa", "requires_env": ["EXA_API_KEY"]},
                       {"name": "memory_pastdev", "mode": "swarm", "memory": "pastdev", "search": "none"}], seeds=[1])
    out = []
    runs = run_matrix(cfg, root=tmp_path, echo=out.append)
    assert [r.action for r in runs] == ["skipped", "skipped"]
    assert "needs EXA_API_KEY, which is not set; skipped" in out[0]
    assert "needs PAST_API_KEY" in out[1]
    man = json.loads((tmp_path / "m" / "manifest.json").read_text())
    assert {r["status"] for r in man["runs"]} == {"skipped"}
    assert not (tmp_path / "m" / "search_exa" / "seed1").exists()
    assert [r.action for r in plan(cfg, tmp_path, env={"EXA_API_KEY": "x", "PAST_API_KEY": "y"})] == ["new", "new"]
    rep = rpt.build(tmp_path, "m")
    assert len(rep["skipped"]) == 2 and "PAST_API_KEY" in rpt.render(rep)
    assert all(a["became_incumbent"] is None and a["confirm"]["mean"] is None for a in rep["arms"])  # no runs: n/a


def test_warm_from_waits_then_carries(tmp_path, matrix_root):
    src_root, _, _ = matrix_root
    warm_arm = {"name": "warm", "mode": "swarm", "memory": "vector", "search": "none", "carry": "both",
                "warm_from": {"matrix": "m", "arm": "swarm", "seed": "same"}}
    cfg = config(name="t", arms=[warm_arm], seeds=[1], generations=1)
    blocked = plan(cfg, tmp_path)
    assert blocked[0].action == "blocked" and "waiting for" in blocked[0].message
    shuffled = dict(warm_arm, name="warm_shuffled", carry="shuffled_notebook")
    cfg = config(name="t", arms=[warm_arm, shuffled], seeds=[1], generations=1)
    run_matrix(cfg, root=src_root, echo=lambda s: None)
    src = src_root / "m" / "swarm" / "seed1"
    warm = src_root / "t" / "warm" / "seed1"
    src_ids = {f.id for f in read_jsonl(src / "findings.jsonl")}
    assert src_ids <= {f.id for f in read_jsonl(warm / "memory.jsonl")}
    assert (warm / "playbook_in.md").read_text() == (src / "playbook.md").read_text()
    sh = src_root / "t" / "warm_shuffled" / "seed1"
    assert json.loads((sh / "config.json").read_text())["shuffle_memory"] is True
    assert not (sh / "playbook_in.md").exists()                    # notebook only, no playbook
    assert json.loads((sh / "spec.json").read_text())["warm_from"] == str(src)


def test_cheat_sheet_arm_and_text(tmp_path):
    cfg = config(arms=[{"name": "cheat_sheet", "mode": "swarm", "memory": "vector", "search": "none",
                        "carry": "cheat_sheet"}], seeds=[1], generations=1)
    run_matrix(cfg, root=tmp_path, echo=lambda s: None)
    d = tmp_path / "m" / "cheat_sheet" / "seed1"
    sheet = (d / "cheat_sheet.md").read_text()
    assert (d / "playbook_in.md").read_text() == sheet
    assert sheet.startswith("# Cheat sheet") and "class Forecaster" in sheet

    class Described:
        target_key = "residual_cm"

        def describe(self):
            return ("# P\n## The question\nwhy\n## Inputs: `case.inputs`\n- `pressure_hpa` (hPa)\n"
                    "## Baselines and the seed\n| seed | 2.80 |\n## Sandbox rules for a candidate\n- no files\n"
                    "## How a change is accepted\nP >= 0.95\n")
    text = cheat_sheet(Described(), max_chars=2000)
    assert "pressure_hpa" in text and "no files" in text and "'residual_cm'" in text
    assert "2.80" not in text and "P >= 0.95" not in text and "why" not in text
    assert text.index("Sandbox rules") < text.index("Inputs")
    long = cheat_sheet(type("L", (), {"target_key": "y", "describe": lambda self: "## Inputs\n" + "- x\n" * 2000})(),
                       max_chars=600)
    assert len(long) <= 600 and long.endswith("(cut to length)\n")


def test_config_validation_and_shipped_configs(tmp_path):
    assert sorted(p.stem for p in CONFIGS.glob("*.json")) == [
        "claim1_ladder_coast", "claim2_transfer_turb_v2", "claim3_search_coast", "claim4_memory_coast"]
    for p in sorted(CONFIGS.glob("*.json")):
        cfg = load_config(p)
        assert cfg["name"] == p.stem and cfg["comment"]
        assert cfg["budget"] == {"generations": 6, "proposals": 4, "max_cost_usd": 4.0}
        assert cfg["seeds"] == ([1, 2, 3, 4, 5] if p.stem == "claim1_ladder_coast" else [1, 2])
    arms = {p.stem: {a["name"]: a for a in load_config(p)["arms"]} for p in CONFIGS.glob("*.json")}
    assert list(arms["claim1_ladder_coast"]) == ["random_mutation", "single_agent", "pipeline", "researcher_plain",
                                                 "researcher_memory", "researcher_search", "researcher_full"]
    ladder = arms["claim1_ladder_coast"]
    # three arms run five seeds; the others keep their own list of two
    assert {n: a.get("seeds") for n, a in ladder.items()} == {
        "random_mutation": [1, 2], "single_agent": None, "pipeline": [1, 2], "researcher_plain": None,
        "researcher_memory": None, "researcher_search": [1, 2], "researcher_full": [1, 2]}
    from lab.experiments.matrix import check_config, plan
    planned = plan(load_config(CONFIGS / "claim1_ladder_coast.json"), root=tmp_path, env={})
    assert len(planned) == 3 * 5 + 4 * 2
    assert sorted(r.seed for r in planned if r.arm == "pipeline") == [1, 2]
    with pytest.raises(ValueError, match="seeds must be"):
        check_config({"problem": "x", "budget": {}, "seeds": [1], "arms": [{"name": "a", "seeds": []}]})
    assert (ladder["researcher_plain"]["memory"], ladder["researcher_plain"]["search"]) == ("none", "none")
    assert (ladder["researcher_memory"]["memory"], ladder["researcher_memory"]["search"]) == ("vector", "none")
    assert (ladder["researcher_search"]["memory"], ladder["researcher_search"]["search"]) == ("none", "openalex")
    assert ladder["researcher_full"]["search_fallback"] == ["arxiv"] and ladder["pipeline"]["mode"] == "pipeline"
    c3 = arms["claim3_search_coast"]
    assert list(c3) == ["search_off", "search_crossref", "search_arxiv", "search_openalex", "search_exa"]
    assert {n: a.get("requires_env") for n, a in c3.items() if a.get("requires_env")} == {
        "search_openalex": ["OPENALEX_API_KEY"], "search_exa": ["EXA_API_KEY"]}
    assert all(a["search_fallback"] == [] for n, a in c3.items() if n != "search_off")   # one backend per arm
    assert {a["mode"] for a in arms["claim3_search_coast"].values()} == {"researcher"}
    assert list(arms["claim4_memory_coast"]) == ["memory_none", "memory_full_context", "memory_vector", "memory_pastdev"]
    assert {a["search"] for a in arms["claim4_memory_coast"].values()} == {"none"}
    c2 = arms["claim2_transfer_turb_v2"]
    assert list(c2) == ["fresh", "cheat_sheet", "warm_notebook", "warm_shuffled", "warm"]
    assert {(a["mode"], a["memory"], a["search"]) for a in c2.values()} == {("researcher", "vector", "none")}
    assert {a["warm_from"]["arm"] for a in c2.values() if "warm_from" in a} == {"researcher_memory"}
    with pytest.raises(ValueError, match="needs warm_from"):
        check_config(dict(config(), arms=[{"name": "w", "carry": "both"}]))
    with pytest.raises(ValueError, match="carry must be"):
        check_config(dict(config(), arms=[{"name": "w", "carry": "everything"}]))
    with pytest.raises(ValueError, match="unknown keys"):
        check_config(dict(config(), arms=[{"name": "w", "colour": "red"}]))
    assert set(CARRY) == {"none", "playbook_only", "notebook_only", "both", "shuffled_notebook", "cheat_sheet"}


def test_claim2_plan_blocks_on_claim1(tmp_path):
    cfg = load_config(CONFIGS / "claim2_transfer_turb_v2.json")
    acts = {(r.arm, r.seed): r.action for r in plan(cfg, tmp_path, env={})}
    assert acts[("fresh", 1)] == "new" and acts[("cheat_sheet", 2)] == "new"
    assert acts[("warm", 1)] == "blocked" and acts[("warm_shuffled", 2)] == "blocked"
    blocked = next(r for r in plan(cfg, tmp_path, env={}) if r.arm == "warm_notebook")
    assert "claim1_ladder_coast/researcher_memory/seed1" in blocked.message


def test_ladder_configs_dry_run(tmp_path):
    rates = [{"run": "r", "status": "finished", "mode": "researcher", "proposals": 8, "proposals_per_generation": 4,
              "usd_per_proposal": 0.05, "engineer_usd_per_proposal": 0.04, "s_per_generation": 60.0}]
    for p in sorted(CONFIGS.glob("*.json")):
        out = []
        res = dry_run(load_config(p), root=tmp_path, echo=out.append, rates=rates)
        text = "\n".join(out)
        assert f"Matrix {p.stem}" in text and res["runs"]
    assert not any(tmp_path.iterdir())                                   # a dry run writes nothing
    ladder = dry_run(load_config(CONFIGS / "claim1_ladder_coast.json"), root=tmp_path, echo=lambda s: None,
                     rates=rates)
    assert len(ladder["runs"]) == 23 and {r["action"] for r in ladder["runs"]} == {"new"}   # 3 arms x 5 seeds + 4 x 2


def test_dry_run_estimates(tmp_path, matrix_root):
    src_root, _, _ = matrix_root
    rates = measured_rates("toy_line", roots=[src_root])
    assert rates and rates[0]["status"] == "finished" and rates[0]["usd_per_proposal"] > 0
    out = []
    res = dry_run(config(name="new"), root=tmp_path, echo=out.append, rates=rates)
    text = "\n".join(out)
    assert "swarm" in text and "seed1" in text and "will start" in text and "total: ~$" in text
    assert res["estimate"]["usd"] > 0 and not (tmp_path / "new").exists()       # nothing launched or written
    out = []
    dry_run(config(name="new"), root=tmp_path, echo=out.append, rates=[])
    assert "unknown" in "\n".join(out)
    mut = config(name="new", arms=[{"name": "random_mutation", "mode": "random_mutation", "search": "none",
                                    "memory": "none"}])
    assert dry_run(mut, root=tmp_path, echo=lambda s: None, rates=rates)["estimate"]["usd"] == 0.0


# ----------------------------------------------------------------- the report on hand-made runs


def write_run(root, arm, seed, curve, status="finished", findings=(), per_gen=2, **extra):
    d = Path(root) / "x" / arm / f"seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    last = curve[-1]
    s = {"status": status, "generations_done": last["generation"], "generations_planned": 3,
         "proposals_per_generation": per_gen, "curve": curve, "mode": "swarm",
         "incumbent": {"select": last["select"], "confirm": last["confirm"]}, "cost_usd": last["cost_usd"],
         "wall_s": last["wall_s"], "evaluations": 10, "memory": "vector",
         "recall": {"calls": 4, "latency_s": 0.02, "chars": 400}, **extra}
    (d / "summary.json").write_text(json.dumps(s))
    (d / "findings.jsonl").write_text("".join(json.dumps(f) + "\n" for f in findings))
    return d


def curve(confirms, usd=0.5, s=60):
    return [{"generation": g, "select": c + 0.1, "confirm": c, "cost_usd": g * usd, "wall_s": g * s, "proposals": 2}
            for g, c in enumerate(confirms)]


NEW = {"accepted": True, "became_incumbent": True, "origin": "search", "search_backend": "arxiv",
       "known_closed_book": False}


@pytest.fixture
def report_root(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "manifest.json").write_text(json.dumps({"config": {
        "name": "x", "problem": "toy", "seeds": [1, 2],
        "arms": [{"name": "fresh"}, {"name": "warm"}]}, "runs": []}))
    write_run(tmp_path, "fresh", 1, curve([3.0, 2.8, 2.6, 2.5]), findings=[NEW, {"accepted": False, "became_incumbent": False, "origin": "closed_book"}],
              search={"backend": "openalex", "chain": ["openalex", "arxiv"], "searches": 5, "failed": 1,
                      "by_backend": {"openalex": {"searches": 3, "failed": 1}, "arxiv": {"searches": 2, "failed": 0}}})
    write_run(tmp_path, "fresh", 2, curve([3.0, 2.9, 2.7, 2.7]))
    write_run(tmp_path, "warm", 1, curve([3.0, 2.5, 2.4, 2.3]))
    write_run(tmp_path, "warm", 2, curve([3.0, 2.95, 2.9, 2.9]))
    return tmp_path


def test_report_time_to_target_counts_not_reached(report_root):
    rep = rpt.build(report_root, "x", target=2.6)
    t = {(x["arm"], x["seed"]): x for x in rep["time_to_target"]}
    assert t[("fresh", 1)]["reached"] and t[("fresh", 1)]["generation"] == 2
    assert t[("fresh", 1)]["proposals"] == 4 and t[("fresh", 1)]["usd"] == 1.0 and t[("fresh", 1)]["minutes"] == 2.0
    assert t[("warm", 1)]["generation"] == 1
    assert not t[("warm", 2)]["reached"] and not t[("fresh", 2)]["reached"]
    md = rpt.render(rep)
    assert "not reached" in md and "warm: 1 of 2 reached" in md and "fresh: 1 of 2 reached" in md
    assert len(rep["time_to_target"]) == 4                       # nobody dropped


def test_target_is_fixed_in_advance_from_the_seed(report_root):
    rep = rpt.build(report_root, "x")
    assert rep["target"] == pytest.approx(3.0 * 0.95) and rep["target_rel"] == 0.05
    assert rep["seed_confirm"]["same"] and rep["seed_confirm"]["n"] == 4
    note = rep["target_note"]
    assert "fixed in advance" in note and "not derived from the results" in note and "all 4 run(s)" in note
    md = rpt.render(rep)
    assert "Target: confirm loss <= 2.8500" in md and "5% below the starting formula" in md
    assert "median" not in note
    assert rpt.build(report_root, "x", target_rel=0.10)["target"] == pytest.approx(2.7)
    over = rpt.build(report_root, "x", target=2.6)
    assert over["target"] == 2.6 and "command line" in over["target_note"] and "2.8500" in over["target_note"]


def test_target_rel_from_config_block_and_config_file(report_root, tmp_path):
    man_path = report_root / "x" / "manifest.json"
    man = json.loads(man_path.read_text())
    man["config"]["target"] = {"rel": 0.08}
    man_path.write_text(json.dumps(man))
    assert rpt.build(report_root, "x")["target"] == pytest.approx(3.0 * 0.92)
    del man["config"]["target"]
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({"target": {"rel": 0.2}}))
    man["config_path"] = str(cfg_file)
    man_path.write_text(json.dumps(man))
    assert rpt.build(report_root, "x")["target"] == pytest.approx(2.4)


def test_target_warns_when_starting_losses_differ(report_root):
    write_run(report_root, "warm", 3, curve([3.2, 2.9]))
    rep = rpt.build(report_root, "x")
    assert not rep["seed_confirm"]["same"] and "WARNING" in rep["target_note"] and "3.2000" in rep["target_note"]


def test_shipped_configs_carry_the_target_rule():
    for p in CONFIGS.glob("*.json"):
        block = json.loads(p.read_text())["target"]
        assert block["rel"] == 0.05 and "seed" in block["rule"]


def cost_root(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "manifest.json").write_text(json.dumps({"config": {
        "name": "x", "problem": "toy", "seeds": [1, 2],
        "arms": [{"name": "swarm"}, {"name": "single"}, {"name": "mutation"}]}, "runs": []}))
    roles = {"scout": {"cost_usd": 0.2}, "judge": {"cost_usd": 0.05}, "engineer": {"cost_usd": 1.0}}
    found = [dict(NEW, search_backend="openalex"), {"accepted": False, "became_incumbent": False, "origin": "search"}]
    write_run(tmp_path, "swarm", 1, curve([3.0, 2.9, 2.6, 2.4], usd=0.5), findings=found, llm_by_role_all=roles)
    write_run(tmp_path, "swarm", 2, curve([3.0, 2.95, 2.7, 2.5], usd=0.6), llm_by_role_all=roles)
    write_run(tmp_path, "single", 1, curve([3.0, 2.8, 2.7, 2.65], usd=0.1))
    write_run(tmp_path, "single", 2, curve([3.0, 2.85, 2.8, 2.75], usd=0.12))
    write_run(tmp_path, "mutation", 1, curve([3.0, 3.0, 2.95, 2.95], usd=0.0))
    write_run(tmp_path, "mutation", 2, curve([3.0, 3.0, 3.0, 3.0], usd=0.0))
    return tmp_path


def test_per_proposal_cost_and_time(tmp_path):
    rep = rpt.build(cost_root(tmp_path), "x")
    a = {x["arm"]: x for x in rep["arms"]}
    # 3 generations x 2 proposals = 6 proposals; swarm seed1 costs 1.5, seed2 1.8
    assert a["swarm"]["usd_per_proposal"]["mean"] == pytest.approx((1.5 / 6 + 1.8 / 6) / 2)
    assert a["single"]["usd_per_proposal"]["mean"] == pytest.approx((0.3 / 6 + 0.36 / 6) / 2)
    assert a["swarm"]["minutes_per_proposal"]["mean"] == pytest.approx(3.0 / 6)
    md = rpt.render(rep)
    assert "| $ per proposal |" in md and "| minutes per proposal |" in md


def test_equal_dollars_view(tmp_path):
    rep = rpt.build(cost_root(tmp_path), "x")
    eq = rep["equal_dollars"]
    assert eq["budget_usd"] == pytest.approx(0.3) and eq["from_run"] == "single seed 1"
    assert eq["free_arms"] == ["mutation"]
    per = {(x["arm"], x["seed"]): x for x in eq["per_run"]}
    assert per[("single", 1)]["generation"] == 3 and per[("single", 1)]["confirm"] == 2.65    # its whole run
    assert per[("single", 2)]["generation"] == 2 and per[("single", 2)]["confirm"] == 2.8     # 0.36 > 0.30
    assert per[("swarm", 1)]["generation"] == 0 and per[("swarm", 1)]["confirm"] == 3.0      # 0.5 > 0.30
    arms = {x["arm"]: x for x in eq["arms"]}
    assert set(arms) == {"swarm", "single"} and arms["single"]["confirm"]["mean"] == pytest.approx(2.725)
    md = rpt.render(rep)
    assert "## Equal proposals vs equal dollars" in md and "within $0.30" in md
    assert "confirm loss at equal dollars ($0.30)" in md and "confirm loss at equal proposals (final)" in md
    assert "mutation costs nothing" in md and "must name its basis" in md


def test_paired_comparison_on_both_bases(tmp_path):
    root = cost_root(tmp_path)
    p = rpt.build(root, "x", compare=("swarm", "single"))["paired"]
    assert p["equal_proposals"]["a_wins"] == 2                         # swarm lower final loss
    assert p["equal_dollars"]["b_wins"] == 2                           # single lower at $0.30
    md = rpt.render(rpt.build(root, "x", compare=("swarm", "single")))
    assert "At equal proposals: swarm had the lower loss in 2 seed(s)" in md
    assert "At equal dollars ($0.30): swarm had the lower loss in 0 seed(s), single in 2" in md
    assert "better" not in md.split("## Paired comparison")[1]
    free = rpt.build(root, "x", compare=("swarm", "mutation"))["paired"]
    assert free["equal_dollars"] is None and "mutation costs nothing" in free["equal_dollars_note"]
    assert "At equal dollars: no equal-dollars basis" in rpt.render(rpt.build(root, "x", compare=("swarm", "mutation")))


def test_search_cost_per_dollar(tmp_path):
    rep = rpt.build(cost_root(tmp_path), "x")
    sc = {x["arm"]: x for x in rep["search_cost"]}
    assert set(sc) == {"swarm"}                                        # only arms whose summaries record roles
    assert sc["swarm"]["search_role_usd"] == pytest.approx(0.5) and sc["swarm"]["accepted_from_search"] == 1
    assert sc["swarm"]["accepted_per_usd"] == pytest.approx(2.0)
    assert "accepted per $" in rpt.render(rep)


def test_search_cost_left_out_without_role_costs(report_root):
    rep = rpt.build(report_root, "x")
    assert rep["search_cost"] == [] and "accepted per $" not in rpt.render(rep)


def test_paired_comparison_refuses_with_two_seeds(report_root):
    rep = rpt.build(report_root, "x")
    p = rep["paired"]
    assert (p["a"], p["b"]) == ("warm", "fresh") and p["seeds"] == 2
    assert p["a_wins"] == 1 and p["b_wins"] == 1 and p["p_value"] is None
    assert "too few seeds to test" in p["test"] and "Too few seeds to test" in rpt.render(rep)
    assert "At equal proposals: warm had the lower loss in 1 seed(s)" in rpt.render(rep)


def test_paired_comparison_tests_with_five_seeds(tmp_path):
    for seed in range(1, 6):
        write_run(tmp_path, "a", seed, curve([3.0, 2.0]))
        write_run(tmp_path, "b", seed, curve([3.0, 2.5]))
    p = rpt.build(tmp_path, "x", compare=("a", "b"))["paired"]
    assert p["a_wins"] == 5 and p["p_value"] == pytest.approx(0.0625) and "sign test" in p["test"]


def test_report_tables_state_seeds_and_search_memory(report_root):
    rep = rpt.build(report_root, "x", target=2.6)
    fresh = next(a for a in rep["arms"] if a["arm"] == "fresh")
    assert fresh["finished"] == 2 and fresh["planned"] == 2 and fresh["seeds"] == [1, 2]
    assert fresh["confirm"]["mean"] == pytest.approx(2.6) and fresh["confirm"]["min"] == 2.5
    assert fresh["proposals"] == 2 and fresh["accepted"] == 1 and fresh["became_incumbent"] == 1
    s = {(x["arm"], x["backend"]): x for x in rep["search"]}
    assert s[("fresh", "openalex")]["searches"] == 3 and s[("fresh", "openalex")]["failed"] == 1
    assert s[("fresh", "arxiv")]["accepted"] == 1 and s[("fresh", "arxiv")]["share_accepted_new"] == 1.0
    assert s[("fresh", "arxiv")]["useful_per_100_searches"] == 50.0
    m = {x["arm"]: x for x in rep["memory"]}
    assert m["warm"]["recall_calls"] == 8 and m["warm"]["mean_latency_ms"] == pytest.approx(5.0)
    md = rpt.render(rep)
    assert "| arm | finished | seeds |" in md and "| arm | backend | seeds |" in md
    assert "| arm | memory | seeds |" in md and "Rests on 2 seed(s)" in md
    assert "PARTIAL" not in md


def test_report_partial_and_old_format(report_root):
    old = [{"claim": "a", "accepted": True, "origin": "search", "known_closed_book": True},
           {"claim": "a", "accepted": True, "origin": "search", "supersedes": "id1"},   # old follow-up row
           {"claim": "b", "accepted": False, "origin": "closed_book"}]
    write_run(report_root, "legacy", 1, curve([3.0, 2.7]), findings=old,
              search={"backend": "openalex", "searches": 4, "cache_hits": 1})
    with open(Path(report_root) / "x" / "legacy" / "seed1" / "findings.jsonl", "a") as fh:
        fh.write('{"claim": "half')                                  # a line still being written
    write_run(report_root, "warm", 3, curve([3.0, 2.7]), status="running")
    rep = rpt.build(report_root, "x", target=2.6)
    legacy = next(a for a in rep["arms"] if a["arm"] == "legacy")
    assert legacy["became_incumbent"] is None and legacy["proposals"] == 2 and legacy["accepted"] == 1
    warm = next(a for a in rep["arms"] if a["arm"] == "warm")
    assert warm["finished"] == 2 and warm["runs_seen"] == 3 and warm["seeds"] == [1, 2]
    md = rpt.render(rep)
    assert "PARTIAL RESULTS" in md
    assert "| warm | 3 | running | not reached (yet) |" in md
    legacy_row = next(line for line in md.splitlines() if line.startswith("| legacy |"))
    assert "| n/a |" in legacy_row                                    # became incumbent: not recorded
    t = next(x for x in rep["time_to_target"] if x["arm"] == "warm" and x["seed"] == 3)
    assert t["status"] == "running" and not t["reached"] and t["finished"] is False
    s = {(x["arm"], x["backend"]): x for x in rep["search"]}
    assert s[("legacy", "openalex")]["searches"] == 4 and s[("legacy", "openalex")]["claims"] == 1
    assert s[("legacy", "openalex")]["accepted"] == 1 and s[("legacy", "openalex")]["share_accepted_new"] == 0.0


def test_report_cli_writes_files(report_root, capsys):
    assert rpt.main(["x", "--root", str(report_root), "--target", "2.6"]) == 0
    assert (report_root / "x" / "report.md").read_text().startswith("# Report: x")
    js = json.loads((report_root / "x" / "report.json").read_text())
    assert js["target"] == 2.6 and len(js["arms"]) == 2
    assert "Time to target" in capsys.readouterr().out


def test_report_on_the_real_matrix_output(matrix_root):
    root, _, _ = matrix_root
    rep = rpt.build(root, "m")
    assert rep["runs_finished"] == 4 and not rep["partial"]
    sw = next(a for a in rep["arms"] if a["arm"] == "swarm")
    assert sw["became_incumbent"] == 2 and sw["cost_usd"]["mean"] > 0
    t = {(x["arm"], x["seed"]): x for x in rpt.build(root, "m", target=1.0)["time_to_target"]}
    assert t[("swarm", 2)]["generation"] == 1 and t[("swarm", 1)]["generation"] == 2


# ----------------------------------------------------------------- origin and board tables


def test_origin_and_board_tables(tmp_path):
    (tmp_path / "x").mkdir()
    rows = [{"accepted": True, "became_incumbent": True, "origin": "search", "known_closed_book": False},
            {"accepted": True, "became_incumbent": False, "origin": "own"},
            {"accepted": False, "became_incumbent": False, "origin": "notebook"},
            {"accepted": False, "became_incumbent": False, "origin": "unstated"}]
    board = {"offered": 8, "used": 1, "used_passed": 1, "used_adopted": 1, "passed_not_known_closed_book": 1}
    write_run(tmp_path, "researcher_full", 1, curve([3.0, 2.8]), findings=rows, board=board, mode="researcher")
    write_run(tmp_path, "researcher_full", 2, curve([3.0, 2.9]), findings=rows[:2], board=dict(board, offered=4),
              mode="researcher")
    old = [{"accepted": True, "origin": "search"}, {"accepted": True, "origin": "search", "supersedes": "a"},
           {"accepted": False, "origin": "closed_book"}]
    write_run(tmp_path, "old_swarm", 1, curve([3.0, 2.95]), findings=old, mode="swarm")
    rep = rpt.build(tmp_path, "x")
    o = {(r["arm"], r["origin"]): r for r in rep["origins"]}
    assert o[("researcher_full", "search")] == {"arm": "researcher_full", "origin": "search", "seeds": 2,
                                                 "proposals": 2, "passed": 2, "adopted": 2}
    assert o[("researcher_full", "unstated")]["proposals"] == 1 and o[("researcher_full", "own")]["adopted"] == 0
    assert o[("old_swarm", "search")] == {"arm": "old_swarm", "origin": "search", "seeds": 1, "proposals": 1,
                                          "passed": 1, "adopted": None}       # old format: n/a, follow-up dropped
    b = {r["arm"]: r for r in rep["board"]}
    assert set(b) == {"researcher_full"} and b["researcher_full"]["offered"] == 12
    assert b["researcher_full"]["used"] == 2 and b["researcher_full"]["share_passed_not_known"] == 1.0
    md = rpt.render(rep)
    assert "## Where the ideas came from" in md and "| old_swarm | search | 1 | 1 | 1 | n/a |" in md
    assert "Ideas board (researcher arms with search)" in md and "| researcher_full | 2 | 12 | 2 | 2 | 2 | 1.00 |" in md
