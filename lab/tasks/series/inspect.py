"""Sanity-check a new dataset in one command.

    .venv/bin/python -m lab.tasks.series.inspect path/to/config.json [--workers 4]

Prints what the data looks like, the four windows (dates, cases, blocks), and the
baselines' and the seed's scores on select and confirm, evaluated in the sandbox
exactly as the lab evaluates candidates (fit on train, predict select and confirm).
"""
from __future__ import annotations

import argparse
import time

from .problem import BLOCK_WORDS, SeriesProblem, lead_label


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", help="JSON config file (keys = SeriesProblem arguments)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-scores", action="store_true", help="skip the sandbox evaluations")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    p = SeriesProblem.from_config(a.config)
    sp = p.splits()
    print(f"problem {p.name}: {p.title}")
    print(f"data: {p.path}")
    print(f"loss: {p.loss_name}; cases ready in {time.monotonic() - t0:.1f}s\n")
    print("## What the data looks like\n")
    print(p.data_summary())
    print(f"peers: {p.peer_rule}")
    print(f"blocks for the bootstrap: series + {BLOCK_WORDS[p.block_unit][1]}\n")
    print("## Windows")
    for w in ("train", "select", "confirm", "test"):
        win = getattr(sp, w)
        print(f"  {w:<8}{len(win):>7} cases  {len(set(win.blocks)):>6} blocks  "
              f"{min(c.issue_time for c in win.cases):%Y-%m-%d %H:%M} .. "
              f"{max(c.target_time for c in win.cases):%Y-%m-%d %H:%M} UTC")
    if a.no_scores:
        return 0
    from lab.evalpool.pool import EvalPool
    sources = {**p.baseline_sources(), "seed": p.seed_source()}
    names = list(sources)
    t1 = time.monotonic()
    with EvalPool(p, sp, max_workers=a.workers) as pool:
        results = dict(zip(names, pool.map([sources[n] for n in names])))
    wall = time.monotonic() - t1
    leads = p.leads()
    keys = [str(int(round(x * p.grid().step_hours))) for x in leads]
    for w in ("select", "confirm"):
        print(f"\n## {w}: mean {p.loss_name}")
        print(f"{'candidate':<18}{'all':>10}" + "".join(f"{lead_label(p, x):>18}" for x in leads) + f"{'MAPE %':>10}")
        for n in names:
            r = results[n]
            if not r.ok:
                print(f"{n:<18} FAILED: {r.error}")
                continue
            s = r.summary[w]
            print(f"{n:<18}{s['mean_loss']:>10.4g}" + "".join(
                f"{s['by_lead'].get(k, {}).get('mean_loss', float('nan')):>18.4g}" for k in keys)
                + (f"{s['mape_pct']:>10.2f}" if "mape_pct" in s else f"{'n/a':>10}"))
        ok = next((results[n] for n in names if results[n].ok), None)
        if ok:
            print(f"{'cases':<18}{ok.summary[w]['n']:>10}" + "".join(
                f"{ok.summary[w]['by_lead'].get(k, {}).get('n', 0):>18}" for k in keys))
    print("\nevaluation time per candidate (fit on train + predict select and confirm, in the sandbox):")
    for n in names:
        print(f"  {n:<18}{results[n].duration_s:>6.1f}s")
    print(f"  all {len(names)} in parallel ({a.workers} workers): {wall:.1f}s wall clock")
    if results["seed"].ok and results["last_value"].ok:
        from lab.evalpool.evaluate import compare
        pr = compare(results["seed"], results["last_value"], sp, "select")
        verdict = "better" if pr.diff.high < 0 else ("worse" if pr.diff.low > 0 else "no clear difference")
        print(f"\nseed minus last_value on select, paired block bootstrap over {pr.diff.n} blocks:")
        print(f"  {pr.diff.mean:+.4g} [95%: {pr.diff.low:+.4g}, {pr.diff.high:+.4g}]  "
              f"P(seed better) = {pr.p_a_better:.3f}  ({verdict}; n = {pr.n} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
