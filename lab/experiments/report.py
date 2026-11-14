"""Turn a matrix's runs into the tables the pitch needs.

    .venv/bin/python -m lab.experiments.report <matrix name> [--target <loss> | --target-rel 0.05] [--compare A,B]

The time-to-target goal is fixed in advance by the protocol, not taken from the
results: 5% below the starting formula's loss on the confirm window
(target = seed_confirm * 0.95), where seed_confirm is the generation-0 confirm
loss in every run's curve. A matrix config may carry {"target": {"rel": ...}};
--target-rel changes the fraction and --target sets the loss directly.

"Same budget" means the same number of proposals, which is not the same money,
so arms are shown both at equal proposals (final loss) and at equal dollars
(loss at the spend of the cheapest arm's cheapest run).

Reads runs/exp/<name>/<arm>/seed<k>/summary.json and findings.jsonl, prints the
report, and writes report.md and report.json next to the manifest. Works while the
matrix is still running (unfinished runs are labelled partial) and on runs written
in the older findings format (missing fields show as "n/a").
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from lab.experiments.matrix import DEFAULT_ROOT, FINISHED
from lab.swarm.runs_index import _read_json, _read_jsonl

MIN_SEEDS_FOR_TEST = 5
DEFAULT_TARGET_REL = 0.05
SEARCH_ROLES = ("scout", "judge")      # model calls that exist only because of search


# ------------------------------------------------------------------ loading


def load_runs(root: Path, name: str) -> tuple[dict, list[dict]]:
    base = Path(root) / name
    manifest = _read_json(base / "manifest.json") or {}
    cfg = manifest.get("config") or {}
    runs = []
    arm_dirs = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    order = [a["name"] for a in cfg.get("arms", [])]
    arm_dirs.sort(key=lambda p: (order.index(p.name) if p.name in order else len(order), p.name))
    for arm_dir in arm_dirs:
        for run_dir in sorted(p for p in arm_dir.iterdir() if p.is_dir() and p.name.startswith("seed")):
            summary = _read_json(run_dir / "summary.json")
            if summary is None:
                continue
            try:
                seed = int(run_dir.name[4:])
            except ValueError:
                continue
            runs.append({"arm": arm_dir.name, "seed": seed, "dir": str(run_dir), "summary": summary,
                         "findings": _read_jsonl(run_dir / "findings.jsonl")})
    return manifest, runs


def is_finished(run: dict) -> bool:
    return run["summary"].get("status") in FINISHED


# ------------------------------------------------------------------ small helpers


def _fmt(x, nd=3) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _mean_range(vals) -> dict:
    v = [float(x) for x in vals if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not v:
        return {"mean": None, "min": None, "max": None, "n": 0}
    return {"mean": sum(v) / len(v), "min": min(v), "max": max(v), "n": len(v)}


def _mr(d: dict, nd=3) -> str:
    if d["mean"] is None:
        return "n/a"
    if d["n"] == 1:
        return _fmt(d["mean"], nd)
    return f"{_fmt(d['mean'], nd)} ({_fmt(d['min'], nd)} to {_fmt(d['max'], nd)})"


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def real_findings(findings: list[dict]) -> list[dict]:
    """One row per proposal: drops the old-format follow-up rows (accepted with `supersedes`)."""
    return [f for f in findings if not (f.get("supersedes") and f.get("accepted"))]


def became_incumbent_count(findings: list[dict]):
    rows = real_findings(findings)
    if not rows:
        return 0
    if any("became_incumbent" not in f for f in rows):
        return None                      # older format: not recorded
    return sum(1 for f in rows if f.get("became_incumbent"))


# ------------------------------------------------------------------ tables


def arm_table(runs: list[dict], planned: dict) -> list[dict]:
    out = []
    for arm in dict.fromkeys(r["arm"] for r in runs).keys() | planned.keys():
        rs = [r for r in runs if r["arm"] == arm]
        fin = [r for r in rs if is_finished(r)]
        use = fin or rs
        rows = [real_findings(r["findings"]) for r in use]
        bi = [became_incumbent_count(r["findings"]) for r in use]
        attempted = [attempted_proposals(r) for r in use]
        out.append({
            "arm": arm, "planned": planned.get(arm, len(rs)), "finished": len(fin), "runs_seen": len(rs),
            "partial": len(fin) < len(rs) or not fin,
            "seeds": sorted(r["seed"] for r in use),
            "select": _mean_range(r["summary"].get("incumbent", {}).get("select") for r in use),
            "confirm": _mean_range(r["summary"].get("incumbent", {}).get("confirm") for r in use),
            "proposals": sum(len(f) for f in rows),
            "accepted": sum(1 for f in rows for x in f if x.get("accepted")),
            "became_incumbent": None if not use or any(b is None for b in bi) else sum(bi),
            "cost_usd": _mean_range(r["summary"].get("cost_usd") for r in use),
            "minutes": _mean_range((r["summary"].get("wall_s") or 0) / 60 for r in use),
            "usd_per_proposal": _mean_range(float(r["summary"].get("cost_usd") or 0) / n
                                            for r, n in zip(use, attempted) if n),
            "minutes_per_proposal": _mean_range(float(r["summary"].get("wall_s") or 0) / 60 / n
                                                for r, n in zip(use, attempted) if n),
            "evaluations": _mean_range(r["summary"].get("evaluations") for r in use),
        })
    order = list(planned) + [a["arm"] for a in out if a["arm"] not in planned]
    out.sort(key=lambda a: order.index(a["arm"]))
    return out


def attempted_proposals(run: dict) -> int:
    s = run["summary"]
    return int(s.get("generations_done") or 0) * int(s.get("proposals_per_generation") or 0)


def seed_confirm(runs: list[dict]) -> dict:
    """The starting formula's confirm loss (generation 0 of every run's curve)."""
    vals = []
    for r in runs:
        curve = r["summary"].get("curve") or []
        if curve and curve[0].get("generation") == 0 and curve[0].get("confirm") is not None:
            vals.append(float(curve[0]["confirm"]))
    if not vals:
        return {"value": None, "same": True, "values": [], "n": 0}
    distinct = sorted(set(round(v, 9) for v in vals))
    return {"value": statistics.median(vals), "same": len(distinct) == 1, "values": distinct, "n": len(vals)}


def fixed_target(runs: list[dict], rel: float) -> tuple[float | None, str, dict]:
    sc = seed_confirm(runs)
    if sc["value"] is None:
        return None, "no run has a generation-0 confirm loss yet", sc
    t = sc["value"] * (1 - rel)
    note = (f"fixed in advance by the protocol: {rel:.0%} below the starting formula's confirm loss "
            f"({sc['value']:.4f} x {1 - rel:.2f} = {t:.4f}), not derived from the results")
    if sc["same"]:
        note += f"; the starting loss is the same in all {sc['n']} run(s)"
    else:
        note += (f". WARNING: the starting loss differs between runs ({', '.join(f'{v:.4f}' for v in sc['values'])});"
                 " the median is used")
    return t, note, sc


def time_to_target(run: dict, target: float) -> dict:
    s = run["summary"]
    per_gen = int(s.get("proposals_per_generation") or 0)
    for c in s.get("curve") or []:
        conf = c.get("confirm")
        if conf is not None and conf <= target:
            g = int(c.get("generation") or 0)
            return {"reached": True, "generation": g, "proposals": g * per_gen,
                    "usd": c.get("cost_usd"), "minutes": (c.get("wall_s") or 0) / 60}
    return {"reached": False, "generation": None, "proposals": None, "usd": None, "minutes": None,
            "finished": is_finished(run)}


def search_table(runs: list[dict]) -> list[dict]:
    out = []
    for arm in dict.fromkeys(r["arm"] for r in runs):
        rs = [r for r in runs if r["arm"] == arm]
        by: dict[str, dict] = {}
        for r in rs:
            st = r["summary"].get("search") or {}
            per = st.get("by_backend") or ({st["backend"]: st} if st.get("backend") else {})
            for name, s in per.items():
                if name == "none":
                    continue
                b = by.setdefault(name, {"searches": 0, "failed": 0, "claims": 0, "accepted": 0,
                                         "accepted_new": 0, "accepted_known_unrecorded": 0})
                b["searches"] += int(s.get("searches") or 0)
                b["failed"] += int(s.get("failed") or 0)
            for f in real_findings(r["findings"]):
                if f.get("origin") != "search":
                    continue
                name = f.get("search_backend") or (st.get("backend") if not st.get("by_backend") else None) \
                    or "unknown"
                b = by.setdefault(name, {"searches": 0, "failed": 0, "claims": 0, "accepted": 0,
                                         "accepted_new": 0, "accepted_known_unrecorded": 0})
                b["claims"] += 1
                if f.get("accepted"):
                    b["accepted"] += 1
                    if f.get("known_closed_book") is False:
                        b["accepted_new"] += 1
                    elif f.get("known_closed_book") is None:
                        b["accepted_known_unrecorded"] += 1
        for name, b in by.items():
            b["share_accepted_new"] = (b["accepted_new"] / b["accepted"]) if b["accepted"] else None
            b["useful_per_100_searches"] = (100 * b["accepted"] / b["searches"]) if b["searches"] else None
            out.append({"arm": arm, "backend": name, "seeds": len(rs), **b})
    return out


def memory_table(runs: list[dict]) -> list[dict]:
    out = []
    for arm in dict.fromkeys(r["arm"] for r in runs):
        rs = [r for r in runs if r["arm"] == arm]
        calls = sum(int((r["summary"].get("recall") or {}).get("calls") or 0) for r in rs)
        lat = sum(float((r["summary"].get("recall") or {}).get("latency_s") or 0) for r in rs)
        chars = sum(int((r["summary"].get("recall") or {}).get("chars") or 0) for r in rs)
        mem = sorted({str(r["summary"].get("memory")) for r in rs})
        out.append({"arm": arm, "memory": ", ".join(mem), "seeds": len(rs), "recall_calls": calls,
                    "mean_latency_ms": 1000 * lat / calls if calls else None,
                    "mean_chars": chars / calls if calls else None})
    return out


def _confirm_at_cost(run: dict, budget_usd: float) -> dict:
    """The incumbent's confirm loss at the last generation whose cumulative cost was within budget."""
    best = None
    for c in run["summary"].get("curve") or []:
        if c.get("cost_usd") is not None and float(c["cost_usd"]) <= budget_usd + 1e-9:
            best = c
    if best is None:
        return {"generation": None, "confirm": None, "usd": None, "proposals": None}
    g = int(best.get("generation") or 0)
    return {"generation": g, "confirm": best.get("confirm"), "usd": best.get("cost_usd"),
            "proposals": g * int(run["summary"].get("proposals_per_generation") or 0)}


def equal_dollars(runs: list[dict]) -> dict:
    """Every arm at the spend of the cheapest paid run: the smallest per-run total cost among
    the arms that cost anything. Arms whose runs cost nothing are excluded."""
    fin = [r for r in runs if is_finished(r)] or runs
    arms = list(dict.fromkeys(r["arm"] for r in fin))
    cost = {a: [float(r["summary"].get("cost_usd") or 0) for r in fin if r["arm"] == a] for a in arms}
    free = [a for a in arms if cost[a] and max(cost[a]) == 0]
    paid = [r for r in fin if r["arm"] not in free and float(r["summary"].get("cost_usd") or 0) > 0]
    if not paid:
        return {"budget_usd": None, "from_run": None, "free_arms": free, "arms": [], "per_run": [],
                "partial": fin is runs and any(not is_finished(r) for r in runs)}
    cheapest = min(paid, key=lambda r: float(r["summary"]["cost_usd"]))
    b = float(cheapest["summary"]["cost_usd"])
    per_run, out = [], []
    for a in arms:
        if a in free:
            continue
        rs = [r for r in fin if r["arm"] == a]
        at = [dict(_confirm_at_cost(r, b), arm=a, seed=r["seed"]) for r in rs]
        per_run += at
        out.append({"arm": a, "seeds": len(rs), "confirm": _mean_range(x["confirm"] for x in at),
                    "generation": _mean_range(x["generation"] for x in at),
                    "proposals": _mean_range(x["proposals"] for x in at)})
    return {"budget_usd": b, "from_run": f"{cheapest['arm']} seed {cheapest['seed']}", "free_arms": free,
            "arms": out, "per_run": per_run, "partial": any(not is_finished(r) for r in runs)}


def search_cost_table(runs: list[dict]) -> list[dict]:
    """Accepted search claims per dollar of search-role model cost (scout + judge), for arms
    whose summaries record cost per role. Empty when none do."""
    out = []
    for arm in dict.fromkeys(r["arm"] for r in runs):
        rs = [r for r in runs if r["arm"] == arm]
        roles = [r["summary"].get("llm_by_role_all") or r["summary"].get("llm_by_role") for r in rs]
        if not any(isinstance(x, dict) and any(k in x for k in SEARCH_ROLES) for x in roles):
            continue
        usd = sum(float((x or {}).get(k, {}).get("cost_usd") or 0) for x in roles for k in SEARCH_ROLES)
        acc = sum(1 for r in rs for f in real_findings(r["findings"]) if f.get("origin") == "search" and f.get("accepted"))
        out.append({"arm": arm, "seeds": len(rs), "search_role_usd": usd, "accepted_from_search": acc,
                    "accepted_per_usd": acc / usd if usd > 0 else None})
    return out


def _sign_test_p(wins: int, n: int) -> float:
    """Two-sided exact binomial (sign) test with p = 0.5."""
    k = min(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _verdict(diffs: list[float]) -> dict:
    a_wins = sum(1 for d in diffs if d < 0)
    b_wins = sum(1 for d in diffs if d > 0)
    n = len(diffs)
    out = {"seeds": n, "a_wins": a_wins, "b_wins": b_wins, "ties": n - a_wins - b_wins,
           "mean_diff": sum(diffs) / n if n else None}
    if n < MIN_SEEDS_FOR_TEST:
        out["test"] = f"too few seeds to test ({n} paired seed(s); at least {MIN_SEEDS_FOR_TEST} needed)"
        out["p_value"] = None
    else:
        decisive = a_wins + b_wins
        out["p_value"] = _sign_test_p(a_wins, decisive) if decisive else 1.0
        out["test"] = f"two-sided sign test over {decisive} seed(s) with a winner: p = {out['p_value']:.3f}"
    return out


def paired(runs: list[dict], a: str, b: str, budget_usd: float | None = None, free_arms=()) -> dict:
    """Per seed, A minus B in confirm loss, on two bases: equal proposals (final loss) and,
    when both arms cost money, equal dollars (loss at `budget_usd`)."""
    fa = {r["seed"]: r for r in runs if r["arm"] == a and is_finished(r)}
    fb = {r["seed"]: r for r in runs if r["arm"] == b and is_finished(r)}
    dollars = budget_usd is not None and a not in free_arms and b not in free_arms
    rows = []
    for s in sorted(set(fa) & set(fb)):
        ca = fa[s]["summary"].get("incumbent", {}).get("confirm")
        cb = fb[s]["summary"].get("incumbent", {}).get("confirm")
        if ca is None or cb is None:
            continue
        row = {"seed": s, a: ca, b: cb, "diff": ca - cb}
        if dollars:
            da = _confirm_at_cost(fa[s], budget_usd)["confirm"]
            db = _confirm_at_cost(fb[s], budget_usd)["confirm"]
            row.update({f"{a}_at_usd": da, f"{b}_at_usd": db,
                        "diff_at_usd": (da - db) if da is not None and db is not None else None})
        rows.append(row)
    out = {"a": a, "b": b, "seeds": len(rows), "rows": rows,
           "equal_proposals": _verdict([r["diff"] for r in rows])}
    out["equal_dollars"] = _verdict([r["diff_at_usd"] for r in rows if r.get("diff_at_usd") is not None]) \
        if dollars else None
    out["equal_dollars_note"] = None if dollars else (
        "no equal-dollars basis: " + (f"{a if a in free_arms else b} costs nothing" if budget_usd is not None
                                      else "no run has cost anything yet"))
    # kept for older readers of report.json
    out.update({k: out["equal_proposals"][k] for k in ("a_wins", "b_wins", "ties", "mean_diff", "test", "p_value")})
    return out


# ------------------------------------------------------------------ the report


def target_rel_from(manifest: dict) -> float:
    """The fraction from the matrix config's "target" block (in the manifest, else the config file)."""
    cfg = manifest.get("config") or {}
    block = cfg.get("target")
    if block is None and manifest.get("config_path"):
        from lab.experiments.matrix import PROJECT_ROOT
        p = Path(manifest["config_path"])
        p = p if p.is_absolute() else PROJECT_ROOT / p
        try:
            block = json.loads(p.read_text()).get("target")
        except (OSError, ValueError):
            block = None
    if isinstance(block, dict) and block.get("rel") is not None:
        return float(block["rel"])
    return DEFAULT_TARGET_REL


def build(root, name: str, target: float | None = None, compare: tuple[str, str] | None = None,
          target_rel: float | None = None) -> dict:
    manifest, runs = load_runs(Path(root), name)
    cfg = manifest.get("config") or {}
    seeds = cfg.get("seeds") or sorted({r["seed"] for r in runs})
    planned = {a["name"]: len(seeds) for a in cfg.get("arms", [])}
    for arm in dict.fromkeys(r["arm"] for r in runs):      # runs outside the config still count
        planned[arm] = max(planned.get(arm, 0), len({r["seed"] for r in runs if r["arm"] == arm}))
    skipped = [r for r in (manifest.get("runs") or []) if r.get("status") in ("skipped", "blocked")]
    for r in skipped:
        planned[r["arm"]] = planned.get(r["arm"], len(seeds)) - (1 if r.get("status") == "skipped" else 0)
    arms = arm_table(runs, planned)
    arm_names = [a["arm"] for a in arms]
    rel = target_rel if target_rel is not None else target_rel_from(manifest)
    t_fixed, rule_note, sc = fixed_target(runs, rel)
    if target is not None:
        target_note = (f"set on the command line; the protocol's fixed rule would give "
                       + (f"{t_fixed:.4f}" if t_fixed is not None else "n/a"))
    else:
        target, target_note = t_fixed, rule_note
    ttt = []
    if target is not None:
        for r in runs:
            t = time_to_target(r, target)
            ttt.append({"arm": r["arm"], "seed": r["seed"], "status": r["summary"].get("status"), **t})
    eq = equal_dollars(runs)
    if compare is None:
        if "warm" in arm_names and "fresh" in arm_names:
            compare = ("warm", "fresh")
        elif len(arm_names) >= 2:
            compare = (arm_names[0], arm_names[1])
    pair = paired(runs, *compare, budget_usd=eq["budget_usd"], free_arms=eq["free_arms"]) if compare else None
    finished = sum(1 for r in runs if is_finished(r))
    return {"matrix": name, "problem": cfg.get("problem"), "planned_runs": sum(planned.values()),
            "runs_seen": len(runs), "runs_finished": finished,
            "partial": finished < sum(planned.values()) or any(not is_finished(r) for r in runs),
            "skipped": [{k: r.get(k) for k in ("arm", "seed", "status", "message")} for r in skipped],
            "arms": arms, "target": target, "target_rel": rel, "target_note": target_note,
            "seed_confirm": sc, "time_to_target": ttt, "equal_dollars": eq,
            "search": search_table(runs), "search_cost": search_cost_table(runs),
            "memory": memory_table(runs), "paired": pair}


def render(rep: dict) -> str:
    L = [f"# Report: {rep['matrix']}" + (f" ({rep['problem']})" if rep.get("problem") else ""), ""]
    status = (f"{rep['runs_finished']} of {rep['planned_runs']} planned runs finished; "
              f"{rep['runs_seen']} run(s) have results.")
    if rep["partial"]:
        status = "**PARTIAL RESULTS: the matrix is not complete.** " + status
    L += [status, ""]
    for s in rep["skipped"]:
        L.append(f"- {s['arm']} seed {s['seed']}: {s['status']}: {s['message']}")
    if rep["skipped"]:
        L.append("")

    L += ["## Results by arm (equal proposals)", "",
          "Every arm had the same number of proposals per run; that is not the same money (see the "
          "equal-dollars view below). Final incumbent loss: mean across seeds (range). Unfinished runs are "
          "used only when an arm has no finished run, and the row is marked partial.", ""]
    rows = []
    for a in rep["arms"]:
        rows.append([a["arm"] + (" (partial)" if a["partial"] else ""), f"{a['finished']} of {a['planned']}",
                     len(a["seeds"]), _mr(a["select"]), _mr(a["confirm"]), a["proposals"], a["accepted"],
                     _fmt(a["became_incumbent"]), _mr(a["cost_usd"], 2), _mr(a["usd_per_proposal"], 3),
                     _mr(a["minutes"], 1), _mr(a["minutes_per_proposal"], 2), _mr(a["evaluations"], 0)])
    L += [_table(["arm", "finished", "seeds", "select loss", "confirm loss", "proposals", "accepted",
                  "became incumbent", "$ per run", "$ per proposal", "minutes per run", "minutes per proposal",
                  "evaluations per run"], rows), ""]

    eq = rep["equal_dollars"]
    L += ["## Equal proposals vs equal dollars", ""]
    if eq["budget_usd"] is None:
        L += ["No run has cost anything yet, so there is no equal-dollars view.", ""]
    else:
        L += [f"Confirm loss of the incumbent on two bases. **Equal proposals**: the final loss after the full "
              f"budget of proposals. **Equal dollars**: the loss at the last generation whose cumulative cost was "
              f"within ${eq['budget_usd']:.2f}, the smallest total cost of any paid run (from {eq['from_run']}), so "
              "every arm is compared at the spend of the cheapest one."
              + (" Based on finished runs only." if not eq["partial"] else " PARTIAL: some runs are unfinished."), ""]
        by_arm = {a["arm"]: a for a in rep["arms"]}
        rows = []
        for e in eq["arms"]:
            a = by_arm.get(e["arm"], {})
            rows.append([e["arm"], e["seeds"], _mr(a.get("confirm", {"mean": None})),
                         _mr(e["confirm"]), _mr(e["proposals"], 1), _mr(e["generation"], 1)])
        L += [_table(["arm", "seeds", "confirm loss at equal proposals (final)",
                      f"confirm loss at equal dollars (${eq['budget_usd']:.2f})",
                      "proposals made within that spend", "generation reached"], rows), ""]
        for a in eq["free_arms"]:
            L.append(f"- {a} costs nothing (no model calls), so it is left out of the equal-dollars view; "
                     "compare it at equal proposals only.")
        if eq["free_arms"]:
            L.append("")
        L += ["Neither basis alone says which arm is better; a statement about an arm must name its basis.", ""]

    L += ["## Time to target", ""]
    if rep["target"] is None:
        L += [f"No target: {rep['target_note']}.", ""]
    else:
        L += [f"Target: confirm loss <= {rep['target']:.4f}. Rule: {rep['target_note']}.", "",
              "Proposals, dollars and minutes until the incumbent first reached it. Runs that never reached it "
              "are listed, not dropped.", ""]
        rows = []
        for arm in [a["arm"] for a in rep["arms"]]:
            ts = [t for t in rep["time_to_target"] if t["arm"] == arm]
            if not ts:
                continue
            reached = [t for t in ts if t["reached"]]
            for t in ts:
                rows.append([arm, t["seed"], t["status"],
                             "yes" if t["reached"] else ("not reached" + ("" if t.get("finished") else " (yet)")),
                             _fmt(t["proposals"]), _fmt(t["usd"], 2), _fmt(t["minutes"], 1)])
            rows.append([f"**{arm}: {len(reached)} of {len(ts)} reached**", f"{len(ts)} seed(s)", "", "",
                         _fmt(float(statistics.median([t['proposals'] for t in reached])) if reached else None, 1),
                         _fmt(statistics.median([t['usd'] or 0 for t in reached]) if reached else None, 2),
                         _fmt(statistics.median([t['minutes'] for t in reached]) if reached else None, 1)])
        L += [_table(["arm", "seed", "status", "reached", "proposals", "$", "minutes"], rows),
              "", "Summary rows give medians over the runs that reached the target.", ""]

    L += ["## Search", ""]
    if rep["search"]:
        rows = [[s["arm"], s["backend"], s["seeds"], s["searches"], s["failed"], s["claims"], s["accepted"],
                 _fmt(s["share_accepted_new"], 2) + ("" if not s["accepted_known_unrecorded"] else
                                                     f" ({s['accepted_known_unrecorded']} not recorded)"),
                 _fmt(s["useful_per_100_searches"], 1)] for s in rep["search"]]
        L += [_table(["arm", "backend", "seeds", "searches", "failed", "claims from search", "accepted",
                      "share of accepted not known closed-book", "accepted per 100 searches"], rows), "",
              "Searches count real calls (cache hits excluded); failed searches are counted in `searches` too.", ""]
    else:
        L += ["No searches in this matrix.", ""]

    if rep.get("search_cost"):
        rows = [[c["arm"], c["seeds"], _fmt(c["search_role_usd"], 2), c["accepted_from_search"],
                 _fmt(c["accepted_per_usd"], 2)] for c in rep["search_cost"]]
        L += ["Search-role model cost (scout and judge calls, which exist only because of search):", "",
              _table(["arm", "seeds", "search-role $", "accepted from search", "accepted per $"], rows), ""]

    L += ["## Memory", ""]
    rows = [[m["arm"], m["memory"], m["seeds"], m["recall_calls"], _fmt(m["mean_latency_ms"], 1),
             _fmt(m["mean_chars"], 0)] for m in rep["memory"]]
    L += [_table(["arm", "memory", "seeds", "recall calls", "mean latency ms", "mean characters returned"], rows), ""]

    p = rep["paired"]
    if p:
        L += [f"## Paired comparison: {p['a']} vs {p['b']}", "",
              f"Confirm loss per seed, finished runs only; difference = {p['a']} minus {p['b']} (negative: "
              f"{p['a']} has the lower loss on that basis). Rests on {p['seeds']} seed(s).", ""]
        dollars = p.get("equal_dollars") is not None
        eq = rep["equal_dollars"]
        heads = ["seed", f"{p['a']} (equal proposals)", f"{p['b']} (equal proposals)", "difference"]
        if dollars:
            heads += [f"{p['a']} (equal dollars)", f"{p['b']} (equal dollars)", "difference"]
        rows = []
        for r in p["rows"]:
            row = [r["seed"], _fmt(r[p["a"]], 4), _fmt(r[p["b"]], 4), _fmt(r["diff"], 4)]
            if dollars:
                row += [_fmt(r.get(f"{p['a']}_at_usd"), 4), _fmt(r.get(f"{p['b']}_at_usd"), 4),
                        _fmt(r.get("diff_at_usd"), 4)]
            rows.append(row)
        if rows:
            L += [_table(heads, rows), ""]
        bases = [("At equal proposals", p["equal_proposals"])]
        if dollars:
            bases.append((f"At equal dollars (${eq['budget_usd']:.2f})", p["equal_dollars"]))
        for label, v in bases:
            L.append(f"- {label}: {p['a']} had the lower loss in {v['a_wins']} seed(s), {p['b']} in {v['b_wins']}, "
                     f"ties {v['ties']}; mean difference {_fmt(v['mean_diff'], 4)}. "
                     f"{v['test'][0].upper() + v['test'][1:]}.")
        if not dollars and p.get("equal_dollars_note"):
            L.append(f"- At equal dollars: {p['equal_dollars_note']}.")
        L.append("")
    return "\n".join(L)


def write(root, name: str, rep: dict) -> tuple[Path, Path]:
    base = Path(root) / name
    base.mkdir(parents=True, exist_ok=True)
    md, js = base / "report.md", base / "report.json"
    md.write_text(render(rep) + "\n")
    js.write_text(json.dumps(rep, indent=1, default=str))
    return md, js


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tables for a matrix of lab runs.")
    ap.add_argument("matrix")
    ap.add_argument("--target", type=float, default=None,
                    help="confirm-window loss to reach (overrides the fixed rule)")
    ap.add_argument("--target-rel", type=float, default=None,
                    help=f"fraction below the starting formula's confirm loss (default {DEFAULT_TARGET_REL}, "
                         "or the config's target.rel)")
    ap.add_argument("--compare", default=None, help="two arm names, A,B (default warm,fresh or the first two)")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    args = ap.parse_args(argv)
    cmp_ = tuple(x.strip() for x in args.compare.split(","))[:2] if args.compare else None
    rep = build(args.root, args.matrix, args.target, cmp_, target_rel=args.target_rel)
    print(render(rep))
    md, js = write(args.root, args.matrix, rep)
    print(f"\nwritten: {md} and {js}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
