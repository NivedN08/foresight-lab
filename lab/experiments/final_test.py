"""Score candidates on the held-back test window. A person runs this, once.

    .venv/bin/python -m lab.experiments.final_test --problem coast_water \\
        --candidate path/to/a.py [--candidate path/to/b.py ...] [--again]

Each candidate is fitted on train and scored on the test window together with
every baseline and the problem's starting formula (listed as
`baseline:starting_formula`, but never chosen as the "best baseline": it is the
lab's own starting point, and each candidate is also compared with it). Printed: mean loss overall and by lead with 95% intervals from
a block bootstrap (blocks = the window's station-days), and the paired block
bootstrap of each candidate against the best baseline.

Written: runs/lab/final_test/<problem>_<UTC timestamp>.json with the SHA-256 of
each candidate source and of the evaluator and problem source files, so the
result is tied to exact code. One line per run is appended to
runs/lab/final_test/log.jsonl. A candidate whose hash is already in that log is
refused unless --again is given: every look at the test window is counted.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from lab.core.stats import paired_block_bootstrap
from lab.evalpool.evaluate import evaluate, source_hash

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "runs" / "lab" / "final_test"
EVALUATOR_FILES = ["lab/evalpool/evaluate.py", "lab/sandbox/runner.py", "lab/sandbox/_child.py",
                   "lab/sandbox/lint.py", "lab/core/stats.py", "lab/core/problem.py",
                   "lab/experiments/final_test.py"]


START = "starting_formula"


class AlreadyScored(SystemExit):
    pass


def file_hashes(paths) -> dict[str, str]:
    out = {}
    for p in paths:
        p = Path(p)
        full = p if p.is_absolute() else ROOT / p
        try:
            key = str(full.resolve().relative_to(ROOT))
        except ValueError:
            key = str(full.resolve())
        out[key] = hashlib.sha256(full.read_bytes()).hexdigest()
    return out


def problem_files(problem) -> list[str]:
    if hasattr(problem, "source_files"):
        return list(problem.source_files())
    return [inspect.getsourcefile(type(problem))]


def read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def block_interval(loss: np.ndarray, blocks) -> dict:
    """Mean loss with a 95% block-bootstrap interval (paired against zero)."""
    pr = paired_block_bootstrap(loss, np.zeros_like(loss), blocks)
    return {"mean": pr.diff.mean, "low": pr.diff.low, "high": pr.diff.high, "n": pr.n, "blocks": pr.diff.n}


def score_one(problem, splits, res) -> dict:
    win = splits.test
    loss = res.losses["test"]
    leads = np.array([int(c.inputs["lead_h"]) for c in win.cases]) if win.cases and \
        "lead_h" in win.cases[0].inputs else np.zeros(len(win), dtype=int)
    blocks = np.asarray(win.blocks, dtype=object)
    by = {}
    for h in sorted(set(leads.tolist())):
        m = leads == h
        by[str(h)] = block_interval(loss[m], blocks[m].tolist())
    return {"overall": block_interval(loss, win.blocks), "by_lead": by, "summary": res.summary["test"]}


def run(problem, candidates: list[str], *, again: bool = False, out_dir: Path | None = None, log=print,
        time_limit_s: float = 600) -> dict:
    """`candidates` are file paths. Returns the written record."""
    out_dir = Path(out_dir or OUT_DIR)
    log_path = out_dir / "log.jsonl"
    sources = {}
    for path in candidates:
        src = Path(path).read_text(encoding="utf-8")
        sources[str(path)] = src
    hashes = {p: source_hash(s) for p, s in sources.items()}
    history = read_log(log_path)
    looks = sum(1 for r in history if r.get("problem") == problem.name)
    seen = {c["sha256"] for r in history if r.get("problem") == problem.name for c in r.get("candidates", [])}
    log(f"the {problem.name} test window has been looked at {looks} time(s) before this run")
    repeats = [p for p, h in hashes.items() if h in seen]
    if repeats and not again:
        raise AlreadyScored(f"refused: already scored on the test window: {', '.join(repeats)} "
                            f"(pass --again to look again; it will be counted)")

    splits = problem.splits()
    baselines = dict(problem.baseline_sources())
    if hasattr(problem, "seed_source") and START not in baselines:
        baselines[START] = problem.seed_source()        # the first real look left this out
    names = list(sources) + [f"baseline:{n}" for n in baselines]
    all_sources = {**sources, **{f"baseline:{n}": s for n, s in baselines.items()}}
    results, scores = {}, {}
    for n in names:
        r = evaluate(problem, all_sources[n], splits, ("test",), allow_test=True, time_limit_s=time_limit_s)
        results[n] = r
        if not r.ok:
            log(f"{n}: FAILED: {r.error}")
            scores[n] = {"ok": False, "error": r.error}
            continue
        scores[n] = {"ok": True, "duration_s": r.duration_s, **score_one(problem, splits, r)}

    start = f"baseline:{START}"
    base_ok = [n for n in names if n.startswith("baseline:") and n != start and scores[n]["ok"]]
    best = min(base_ok, key=lambda n: scores[n]["overall"]["mean"]) if base_ok else None

    def pair(p, other):
        pr = paired_block_bootstrap(results[p].losses["test"], results[other].losses["test"], splits.test.blocks)
        return {"vs": other, "diff": pr.diff.mean, "low": pr.diff.low, "high": pr.diff.high,
                "p_candidate_better": pr.p_a_better, "blocks": pr.diff.n, "n": pr.n}
    paired, paired_start = {}, {}
    for p in sources:
        if best and scores[p]["ok"]:
            paired[p] = pair(p, best)
        if start in scores and scores[start]["ok"] and scores[p]["ok"]:
            paired_start[p] = pair(p, start)

    # ---- print
    leads = sorted({h for n in names if scores[n]["ok"] for h in scores[n]["by_lead"]}, key=float)
    log(f"\ntest window: {len(splits.test)} cases, {len(set(splits.test.blocks))} blocks; "
        f"{problem.loss_name}, mean [95% block-bootstrap interval]")
    log(f"{'candidate':<34}{'all':>22}" + "".join(f"{h + ' h':>22}" for h in leads))
    for n in names:
        s = scores[n]
        if not s["ok"]:
            log(f"{n:<34} FAILED")
            continue
        cell = lambda d: f"{d['mean']:>8.2f} [{d['low']:5.2f},{d['high']:5.2f}]"  # noqa: E731
        log(f"{n:<34}{cell(s['overall'])}" + "".join(cell(s["by_lead"][h]) if h in s["by_lead"] else " " * 22
                                                      for h in leads))
    for p, d in list(paired.items()) + list(paired_start.items()):
        verdict = "better" if d["high"] < 0 else ("worse" if d["low"] > 0 else "no clear difference")
        log(f"\n{p} minus {d['vs']}: {d['diff']:+.3f} [{d['low']:+.3f}, {d['high']:+.3f}]  "
            f"P(candidate better) = {d['p_candidate_better']:.3f}  ({verdict}, {d['blocks']} blocks)")

    # ---- record
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{problem.name}_{stamp}.json"
    k = 1
    while path.exists():
        path = out_dir / f"{problem.name}_{stamp}_{k}.json"
        k += 1
    record = {
        "time": stamp, "problem": problem.name, "again": again, "previous_looks": looks,
        "candidates": [{"path": p, "sha256": hashes[p]} for p in sources],
        "baselines": {n: source_hash(s) for n, s in baselines.items()},
        "evaluator_sha256": file_hashes(EVALUATOR_FILES),
        "problem_sha256": file_hashes(problem_files(problem)),
        "splits_key": problem.cache_key() if hasattr(problem, "cache_key") else None,
        "test_window": {"cases": len(splits.test), "blocks": len(set(splits.test.blocks)),
                        "from": min(c.issue_time for c in splits.test.cases).isoformat() if splits.test.cases else None,
                        "to": max(c.target_time for c in splits.test.cases).isoformat() if splits.test.cases else None},
        "loss": problem.loss_name, "best_baseline": best, "scores": scores, "paired_vs_best_baseline": paired,
        "paired_vs_starting_formula": paired_start,
    }
    path.write_text(json.dumps(record, indent=1, default=str))
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": stamp, "problem": problem.name, "again": again,
                            "candidates": record["candidates"], "result": path.name}) + "\n")
    log(f"\nsaved {path}; this was look number {looks + 1} at the {problem.name} test window")
    record["path"] = str(path)
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score candidates on the held-back test window (once).")
    ap.add_argument("--problem", required=True)
    ap.add_argument("--candidate", action="append", required=True)
    ap.add_argument("--again", action="store_true", help="allow a candidate already scored on the test window")
    ap.add_argument("--until", default=None, help="pin the problem's data end (ISO UTC)")
    a = ap.parse_args(argv)
    from lab.evalpool.registry import get_problem
    problem = get_problem(a.problem, **({"until": a.until} if a.until else {}))
    try:
        run(problem, a.candidate, again=a.again)
    except AlreadyScored as e:
        print(e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
