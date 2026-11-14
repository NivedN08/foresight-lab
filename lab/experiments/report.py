"""Turn a matrix's runs into the tables the pitch needs.

    .venv/bin/python -m lab.experiments.report <matrix name> [--target <loss>] [--compare A,B] [--root runs/exp]

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
        out.append({
            "arm": arm, "planned": planned.get(arm, len(rs)), "finished": len(fin), "runs_seen": len(rs),
            "partial": len(fin) < len(rs) or not fin,
            "seeds": sorted(r["seed"] for r in use),
            "select": _mean_range(r["summary"].get("incumbent", {}).get("select") for r in use),
            "confirm": _mean_range(r["summary"].get("incumbent", {}).get("confirm") for r in use),
            "proposals": sum(len(f) for f in rows),
            "accepted": sum(1 for f in rows for x in f if x.get("accepted")),
            "became_incumbent": None if any(b is None for b in bi) else sum(bi),
            "cost_usd": _mean_range(r["summary"].get("cost_usd") for r in use),
            "minutes": _mean_range((r["summary"].get("wall_s") or 0) / 60 for r in use),
            "evaluations": _mean_range(r["summary"].get("evaluations") for r in use),
        })
    order = list(planned) + [a["arm"] for a in out if a["arm"] not in planned]
    out.sort(key=lambda a: order.index(a["arm"]))
    return out


def default_target(runs: list[dict], arms: list[str]) -> tuple[float | None, str]:
    ref = "fresh" if "fresh" in arms else (arms[0] if arms else None)
    if ref is None:
        return None, "no runs"
    rs = [r for r in runs if r["arm"] == ref]
    fin = [r for r in rs if is_finished(r)] or rs
    vals = [r["summary"].get("incumbent", {}).get("confirm") for r in fin]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, f"arm {ref} has no confirm loss yet"
    return float(statistics.median(vals)), f"median final confirm loss of arm {ref} ({len(vals)} run(s))"


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


def _sign_test_p(wins: int, n: int) -> float:
    """Two-sided exact binomial (sign) test with p = 0.5."""
    k = min(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired(runs: list[dict], a: str, b: str) -> dict:
    fa = {r["seed"]: r for r in runs if r["arm"] == a and is_finished(r)}
    fb = {r["seed"]: r for r in runs if r["arm"] == b and is_finished(r)}
    seeds = sorted(set(fa) & set(fb))
    rows, a_wins, b_wins, ties = [], 0, 0, 0
    for s in seeds:
        ca = fa[s]["summary"].get("incumbent", {}).get("confirm")
        cb = fb[s]["summary"].get("incumbent", {}).get("confirm")
        if ca is None or cb is None:
            continue
        d = ca - cb
        rows.append({"seed": s, a: ca, b: cb, "diff": d})
        if d < 0:
            a_wins += 1
        elif d > 0:
            b_wins += 1
        else:
            ties += 1
    n = len(rows)
    out = {"a": a, "b": b, "seeds": n, "rows": rows, "a_wins": a_wins, "b_wins": b_wins, "ties": ties,
           "mean_diff": sum(r["diff"] for r in rows) / n if n else None}
    if n < MIN_SEEDS_FOR_TEST:
        out["test"] = f"too few seeds to test ({n} paired seed(s); at least {MIN_SEEDS_FOR_TEST} needed)"
        out["p_value"] = None
    else:
        decisive = a_wins + b_wins
        out["p_value"] = _sign_test_p(a_wins, decisive) if decisive else 1.0
        out["test"] = f"two-sided sign test over {decisive} seed(s) with a winner: p = {out['p_value']:.3f}"
    return out


# ------------------------------------------------------------------ the report


def build(root, name: str, target: float | None = None, compare: tuple[str, str] | None = None) -> dict:
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
    target_note = "given on the command line"
    if target is None:
        target, target_note = default_target(runs, arm_names)
    ttt = []
    if target is not None:
        for r in runs:
            t = time_to_target(r, target)
            ttt.append({"arm": r["arm"], "seed": r["seed"], "status": r["summary"].get("status"), **t})
    if compare is None:
        if "warm" in arm_names and "fresh" in arm_names:
            compare = ("warm", "fresh")
        elif len(arm_names) >= 2:
            compare = (arm_names[0], arm_names[1])
    pair = paired(runs, *compare) if compare else None
    finished = sum(1 for r in runs if is_finished(r))
    return {"matrix": name, "problem": cfg.get("problem"), "planned_runs": sum(planned.values()),
            "runs_seen": len(runs), "runs_finished": finished,
            "partial": finished < sum(planned.values()) or any(not is_finished(r) for r in runs),
            "skipped": [{k: r.get(k) for k in ("arm", "seed", "status", "message")} for r in skipped],
            "arms": arms, "target": target, "target_note": target_note, "time_to_target": ttt,
            "search": search_table(runs), "memory": memory_table(runs), "paired": pair}


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

    L += ["## Results by arm", "",
          "Final incumbent loss: mean across seeds (range). Unfinished runs are used only when an arm has "
          "no finished run, and the row is marked partial.", ""]
    rows = []
    for a in rep["arms"]:
        rows.append([a["arm"] + (" (partial)" if a["partial"] else ""), f"{a['finished']} of {a['planned']}",
                     len(a["seeds"]), _mr(a["select"]), _mr(a["confirm"]), a["proposals"], a["accepted"],
                     _fmt(a["became_incumbent"]), _mr(a["cost_usd"], 2), _mr(a["minutes"], 1),
                     _mr(a["evaluations"], 0)])
    L += [_table(["arm", "finished", "seeds", "select loss", "confirm loss", "proposals", "accepted",
                  "became incumbent", "cost $ per run", "minutes per run", "evaluations per run"], rows), ""]

    L += ["## Time to target", ""]
    if rep["target"] is None:
        L += [f"No target: {rep['target_note']}.", ""]
    else:
        L += [f"Target: confirm loss <= {rep['target']:.4f} ({rep['target_note']}). Proposals, dollars and "
              "minutes until the incumbent first reached it. Runs that never reached it are listed, not dropped.", ""]
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

    L += ["## Memory", ""]
    rows = [[m["arm"], m["memory"], m["seeds"], m["recall_calls"], _fmt(m["mean_latency_ms"], 1),
             _fmt(m["mean_chars"], 0)] for m in rep["memory"]]
    L += [_table(["arm", "memory", "seeds", "recall calls", "mean latency ms", "mean characters returned"], rows), ""]

    p = rep["paired"]
    if p:
        L += [f"## Paired comparison: {p['a']} vs {p['b']}", "",
              f"Final confirm loss per seed, finished runs only; difference = {p['a']} minus {p['b']} "
              f"(negative: {p['a']} better). Rests on {p['seeds']} seed(s).", ""]
        rows = [[r["seed"], _fmt(r[p["a"]], 4), _fmt(r[p["b"]], 4), _fmt(r["diff"], 4)] for r in p["rows"]]
        if rows:
            L += [_table(["seed", p["a"], p["b"], "difference"], rows), ""]
        L += [f"{p['a']} won {p['a_wins']}, {p['b']} won {p['b_wins']}, ties {p['ties']}; "
              f"mean difference {_fmt(p['mean_diff'], 4)}. {p['test'][0].upper() + p['test'][1:]}.", ""]
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
    ap.add_argument("--target", type=float, default=None, help="confirm-window loss to reach")
    ap.add_argument("--compare", default=None, help="two arm names, A,B (default warm,fresh or the first two)")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    args = ap.parse_args(argv)
    cmp_ = tuple(x.strip() for x in args.compare.split(","))[:2] if args.compare else None
    rep = build(args.root, args.matrix, args.target, cmp_)
    print(render(rep))
    md, js = write(args.root, args.matrix, rep)
    print(f"\nwritten: {md} and {js}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
