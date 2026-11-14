"""Sanity check of a Problem through the evaluator.

    .venv/bin/python -m lab.evalpool.check --problem coast_water

Evaluates the baselines and the seed in the sandbox on the select and confirm
windows and prints mean loss overall and by lead, the evaluation time, and the
paired block-bootstrap comparison of the seed against persistence on select.
"""
from __future__ import annotations

import argparse
import time

from .evaluate import compare
from .pool import EvalPool
from .registry import get_problem


def fmt_row(name: str, summary: dict, leads: list[str]) -> str:
    return f"{name:<20}{summary['mean_loss']:>8.2f}" + "".join(
        f"{summary['by_lead'].get(h, {}).get('mean_loss', float('nan')):>8.2f}" for h in leads)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", default="coast_water")
    ap.add_argument("--until", default=None, help="pin the problem's data end (ISO UTC)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    kw = {"until": a.until} if a.until else {}
    t0 = time.monotonic()
    problem = get_problem(a.problem, **kw)
    sp = problem.splits()
    print(f"problem {problem.name}: {problem.title}")
    print(f"loss: {problem.loss_name}; splits ready in {time.monotonic() - t0:.1f}s")
    for w in ("train", "select", "confirm", "test"):
        win = getattr(sp, w)
        if win.cases:
            print(f"  {w:<8}{len(win):>7} cases  {len(set(win.blocks)):>5} blocks  "
                  f"{min(c.issue_time for c in win.cases):%Y-%m-%d %H:%M} .. "
                  f"{max(c.target_time for c in win.cases):%Y-%m-%d %H:%M} UTC")
    sources = {**problem.baseline_sources(), "seed": problem.seed_source()}
    names = list(sources)
    t1 = time.monotonic()
    with EvalPool(problem, sp, max_workers=a.workers) as pool:
        results = dict(zip(names, pool.map([sources[n] for n in names])))
    wall = time.monotonic() - t1
    ok = [n for n in names if results[n].ok]
    # Problems without lead times (for example turbulence) have no by-lead breakdown.
    by_lead = results[ok[0]].summary["select"].get("by_lead") if ok else None
    leads = sorted(by_lead, key=lambda h: float(h)) if by_lead else []
    for w in ("select", "confirm"):
        print(f"\n{w}: mean {problem.loss_name}")
        print(f"{'candidate':<20}{'all':>8}" + "".join(f"{h + ' h':>8}" for h in leads))
        for n in names:
            r = results[n]
            if not r.ok:
                print(f"{n:<20} FAILED: {r.error}")
            elif leads:
                print(fmt_row(n, r.summary[w], leads))
            else:
                extra = "".join(f"  {k}={v:.3f}" for k, v in r.summary[w].items()
                                if k != "mean_loss" and isinstance(v, float))
                print(f"{n:<20}{r.summary[w]['mean_loss']:>8.2f}{extra}")
        if ok:
            s0 = results[ok[0]].summary[w]
            print(f"{'cases':<20}{s0['n']:>8}" + "".join(f"{s0['by_lead'][h]['n']:>8}" for h in leads))
    print("\nevaluation time per candidate (fit on train + predict select and confirm, in the sandbox):")
    for n in names:
        print(f"  {n:<20}{results[n].duration_s:>6.1f}s")
    print(f"  all {len(names)} in parallel ({a.workers} workers): {wall:.1f}s wall clock")
    baselines = [n for n in ok if n != "seed"]
    if results["seed"].ok and baselines:
        best = min(baselines, key=lambda n: results[n].summary["select"]["mean_loss"])
        pr = compare(results["seed"], results[best], sp, "select")
        verdict = "better" if pr.diff.high < 0 else ("worse" if pr.diff.low > 0 else "no clear difference")
        print(f"\nseed minus {best} (the best baseline on select), paired bootstrap over {pr.diff.n} blocks:")
        print(f"  {pr.diff.mean:+.3f} [95%: {pr.diff.low:+.3f}, {pr.diff.high:+.3f}]  "
              f"P(seed better) = {pr.p_a_better:.3f}  ({verdict}; n = {pr.n} cases)")


if __name__ == "__main__":
    main()
