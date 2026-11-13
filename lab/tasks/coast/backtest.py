"""Backtest the water-level forecasters on recent history.

    .venv/bin/python -m lab.tasks.coast.backtest --days 90

Cases are hourly issue times at every station for leads 1, 3, 6, 12, 24 h.
They are split by time (train / validation / test); models are fitted on the
train part and scored on the test part. Error is absolute error in cm.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from lab.core.stats import bootstrap_ci, paired_bootstrap
from lab.core.task import time_split
from lab.core.timeutil import to_iso, utcnow

from .data import load_stations
from .http import Client
from .models import SeedPhysics, all_models
from .water import LEADS, StationFeatures, build_history_cases

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "runs" / "coast" / "backtest.json"

WEATHER_NOTE = ("Past weather values from the Open-Meteo forecast API are short-lead forecasts "
                "stitched together, so backtest skill at long leads is slightly optimistic for "
                "SeedPhysics. The live sealed forecasts are the clean test.")


def score(models, test_cases, test_truths):
    errs = {m.name: defaultdict(list) for m in models}
    for c, t in zip(test_cases, test_truths):
        lead = int(c.inputs["lead_h"])
        for m in models:
            p = m.predict(c)["residual_m"]
            errs[m.name][lead].append(abs(p - t["residual_m"]) * 100.0)
    return errs


def run(days: int, now=None, client: Client | None = None, out: Path = OUT, log=print) -> dict:
    client = client or Client()
    now = now or utcnow()
    log(f"loading {days} days of gauge, tide and weather data ...")
    stations = load_stations(client, now, days, log=log)
    start = pd.Timestamp(now - timedelta(days=days))
    feats = {sid: StationFeatures(sd, start - pd.Timedelta(days=4), pd.Timestamp(now))
             for sid, sd in stations.items()}
    weather_ok = sum(f.has_weather for f in feats.values())
    log(f"stations: {len(feats)} (weather for {weather_ok})")
    cases, truths = build_history_cases(feats, start.to_pydatetime(), now)
    truth_of = {c.case_id: t for c, t in zip(cases, truths)}
    tr, va, te = time_split(cases, 0.6, 0.2)
    log(f"cases: {len(cases)}  train {len(tr)}  validation {len(va)}  test {len(te)}")
    models = all_models()
    for m in models:
        m.fit(tr, [truth_of[c.case_id] for c in tr])
    errs = score(models, te, [truth_of[c.case_id] for c in te])

    table = {}
    for m in models:
        table[m.name] = {}
        for lead in LEADS:
            ci = bootstrap_ci(errs[m.name][lead])
            table[m.name][str(lead)] = {"mae_cm": ci.mean, "low": ci.low, "high": ci.high, "n": ci.n}
    paired = {}
    for other in ("Persistence", "DampedPersistence"):
        paired[other] = {}
        for lead in LEADS:
            pr = paired_bootstrap(errs["SeedPhysics"][lead], errs[other][lead])
            paired[other][str(lead)] = {"seed_mae": pr.mean_a, "other_mae": pr.mean_b,
                                        "diff_cm": pr.diff.mean, "low": pr.diff.low, "high": pr.diff.high,
                                        "p_seed_better": pr.p_a_better, "n": pr.n}

    # ---- print
    log("")
    log("Test MAE (cm) of the water level above the tide table, mean [95% bootstrap interval]")
    head = f"{'model':<18}" + "".join(f"{str(h) + ' h':>22}" for h in LEADS)
    log(head)
    for m in models:
        row = f"{m.name:<18}"
        for lead in LEADS:
            r = table[m.name][str(lead)]
            row += f"{r['mae_cm']:>8.2f} [{r['low']:5.2f},{r['high']:5.2f}]"
        log(row)
    log(f"{'cases per lead':<18}" + "".join(f"{table['Persistence'][str(h)]['n']:>22}" for h in LEADS))
    log("")
    log("Paired: SeedPhysics minus other, cm (negative = SeedPhysics better)")
    for other, rows in paired.items():
        row = f"vs {other:<16}"
        for lead in LEADS:
            r = rows[str(lead)]
            verdict = "better" if r["high"] < 0 else ("worse" if r["low"] > 0 else "tie")
            row += f" {r['diff_cm']:+6.2f} [{r['low']:+5.2f},{r['high']:+5.2f}] {verdict:<6}"
        log(row)
    log("(Bootstrap resamples cases independently; neighbouring hours and stations are correlated, "
        "so the intervals are narrower than they should be.)")
    seed: SeedPhysics = next(m for m in models if m.name == "SeedPhysics")
    damped = next(m for m in models if m.name == "DampedPersistence")
    log("")
    log("DampedPersistence a_h: " + ", ".join(f"{h}h={damped.a.get(h, float('nan')):.3f}" for h in LEADS))
    log("SeedPhysics fitted coefficients (train window):")
    for line in seed.describe():
        log("  " + line)
    pcoefs = [seed.coef[h]["pressure"] * 100 for h in LEADS if h in seed.coef]
    if pcoefs:
        near = all(-1.5 <= p <= -0.5 for p in pcoefs)
        log(f"Pressure coefficient across leads: {', '.join(f'{p:+.2f}' for p in pcoefs)} cm/hPa; "
            f"inverse-barometer physics says about -1 cm/hPa: "
            f"{'near it at every lead' if near else 'NOT near it at every lead'}.")
    else:
        log("Pressure coefficient: not fitted (no weather data).")
    log("")
    log("Note: " + WEATHER_NOTE)

    names = {sid: sd.name for sid, sd in stations.items()}
    result = {
        "generated_at": to_iso(utcnow()),
        "data_until": to_iso(now),
        "days": days,
        "stations": names,
        "weather_stations": weather_ok,
        "n_cases": {"train": len(tr), "validation": len(va), "test": len(te)},
        "test_window": [to_iso(min(c.issue_time for c in te)), to_iso(max(c.target_time for c in te))] if te else None,
        "unit": "cm, mean absolute error",
        "table": table,
        "paired_seed_vs": paired,
        "damped_a": {str(h): damped.a.get(h) for h in LEADS},
        "seed_coefficients": {
            str(h): {"a": float(k["a"]), "pressure_cm_per_hpa": float(k["pressure"] * 100),
                     "trend": float(k["trend"]), "intercept_cm": float(k["intercept"] * 100),
                     "wind_toward_deg_and_cm_per_m2s2": {s: list(v) for s, v in seed.wind_directions(h).items()}}
            for h, k in seed.coef.items()},
        "note": WEATHER_NOTE,
        "fetch_log": client.log[-20:],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    log(f"saved {out}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    run(args.days)


if __name__ == "__main__":
    main()
