"""Backtest the wave-height forecasters on the buoys' recent history.

    .venv/bin/python -m lab.tasks.coast.backtest_waves --days 44

NDBC realtime2 files hold about 45 days, so that is the longest window.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from lab.core.stats import bootstrap_ci, paired_bootstrap
from lab.core.task import time_split
from lab.core.timeutil import to_iso, utcnow

from .http import Client
from .waves import LEADS, all_wave_models, build_wave_history, load_buoys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "runs" / "coast" / "backtest_waves.json"
NOTE = ("Past values from the Open-Meteo marine API are short-lead model forecasts stitched together, "
        "so the WaveModel and SeedSwell backtest numbers at long leads are optimistic. "
        "The live sealed forecasts are the clean test.")


def run(days: int, out: Path = OUT, log=print) -> dict:
    client = Client()
    now = utcnow()
    log(f"loading buoys and the wave model ({days} days) ...")
    feats, offshore, _ = load_buoys(client, now, days, log=log)
    log(f"coastal buoys: {len(feats)} (wave model for {sum(f.model is not None for f in feats.values())}); "
        f"offshore: {sorted(offshore)}")
    for b, f in feats.items():
        if f.upstream:
            log(f"  {b} upstream {f.upstream.id}: {f.upstream.distance_m / 1000:.0f} km, "
                f"travel at 14 s period {f.upstream.distance_m / (9.81 * 14 / (4 * 3.14159)) / 3600:.0f} h")
    cases, truths = build_wave_history(feats, now - timedelta(days=days), now)
    truth_of = {c.case_id: t for c, t in zip(cases, truths)}
    tr, va, te = time_split(cases, 0.6, 0.2)
    log(f"cases: {len(cases)}  train {len(tr)}  validation {len(va)}  test {len(te)}")
    models = all_wave_models()
    for m in models:
        m.fit(tr, [truth_of[c.case_id] for c in tr])
    errs = {m.name: defaultdict(list) for m in models}
    for c in te:
        t = truth_of[c.case_id]["wave_height_m"]
        for m in models:
            errs[m.name][int(c.inputs["lead_h"])].append(abs(m.predict(c)["wave_height_m"] - t) * 100)
    table = {m.name: {str(h): (lambda ci: {"mae_cm": ci.mean, "low": ci.low, "high": ci.high, "n": ci.n})(
        bootstrap_ci(errs[m.name][h])) for h in LEADS} for m in models}
    paired = {}
    for other in ("Persistence", "WaveModel"):
        paired[other] = {}
        for h in LEADS:
            pr = paired_bootstrap(errs["SeedSwell"][h], errs[other][h])
            paired[other][str(h)] = {"seed_mae": pr.mean_a, "other_mae": pr.mean_b, "diff_cm": pr.diff.mean,
                                     "low": pr.diff.low, "high": pr.diff.high, "p_seed_better": pr.p_a_better,
                                     "n": pr.n}
    up_share = sum(c.inputs["has_upstream"] for c in te) / max(1, len(te))
    log("")
    log("Test MAE (cm) of significant wave height, mean [95% bootstrap interval]")
    log(f"{'model':<14}" + "".join(f"{str(h) + ' h':>22}" for h in LEADS))
    for m in models:
        log(f"{m.name:<14}" + "".join(
            f"{table[m.name][str(h)]['mae_cm']:>8.1f} [{table[m.name][str(h)]['low']:5.1f},{table[m.name][str(h)]['high']:5.1f}]"
            for h in LEADS))
    log(f"{'cases per lead':<14}" + "".join(f"{table['Persistence'][str(h)]['n']:>22}" for h in LEADS))
    log("")
    log("Paired: SeedSwell minus other, cm (negative = SeedSwell better)")
    for other, rows in paired.items():
        line = f"vs {other:<12}"
        for h in LEADS:
            r = rows[str(h)]
            v = "better" if r["high"] < 0 else ("worse" if r["low"] > 0 else "tie")
            line += f" {r['diff_cm']:+6.1f} [{r['low']:+5.1f},{r['high']:+5.1f}] {v:<6}"
        log(line)
    log(f"Share of test cases with an upstream value: {up_share:.0%}")
    seed = next(m for m in models if m.name == "SeedSwell")
    log("SeedSwell coefficients (full version, train window):")
    for line in seed.describe():
        log("  " + line)
    log("Note: " + NOTE)
    result = {"generated_at": to_iso(utcnow()), "data_until": to_iso(now), "days": days,
              "buoys": sorted(feats), "offshore": sorted(offshore),
              "n_cases": {"train": len(tr), "validation": len(va), "test": len(te)},
              "unit": "cm, mean absolute error of significant wave height",
              "table": table, "paired_seed_vs": paired, "upstream_share_test": up_share,
              "seed_coefficients": {f"{k[0]}h|model={k[1]}|upstream={k[2]}": v for k, v in seed.coef.items()},
              "note": NOTE, "fetch_log": client.log[-20:]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    log(f"saved {out}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=44)
    run(ap.parse_args().days)


if __name__ == "__main__":
    main()
