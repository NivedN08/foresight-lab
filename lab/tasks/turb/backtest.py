"""Backtest the turbulence forecasters on the last N days of pilot reports.

    .venv/bin/python -m lab.tasks.turb.backtest --days 30

One case per labelled upper-level report layer (flight level 200 and above),
split by time with lab.core.task.time_split (oldest 60% train, next 20%
validation, newest 20% test). Models are fitted on the training part and
scored on the test part; intervals resample whole UTC days.

The fields are Open-Meteo's past values (hindsight fields, see fields.py), so
this is an analysis-time comparison of indices, not a forecast test.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta

import numpy as np

from lab.core.stats import auc, paired_block_bootstrap
from lab.core.task import time_split
from lab.core.timeutil import to_iso, utcnow

from . import fields as F
from . import net
from .cases import build_cases, labelled
from .models import all_models
from .pireps import in_domain, load_iem_days

CAVEAT = ("Hindsight fields: Open-Meteo's past values are stitched from model runs started at or after "
          "each valid hour, which may have assimilated the aircraft observations being predicted. This "
          "backtest is an analysis-time comparison of turbulence indices: fair between models (all share "
          "the same inputs) but not evidence of forecast skill. Only the live sealed forecasts test "
          "forecast skill.")
PREVIOUS_RUNS = ("A forecast-field test was tried: Open-Meteo's Previous Runs API "
                 "(previous-runs-api.open-meteo.com, variables suffixed _previous_day1) rejects "
                 "pressure-level variables (checked 2026-11-13 for the default, GFS and ECMWF models), "
                 "so no day-old upper-air fields are available from it.")
BIAS = ("Pilot reports are biased toward where aircraft fly and toward reporting bumps rather "
        "than smooth air, so the base rate and AUC describe reported turbulence, not all airspace.")
INTERVALS = ("Intervals resample whole UTC days of the test window (reports cluster by hour and day); "
             "hour-block intervals are shown as a second line. With about seven test days the "
             "day-block intervals are themselves rough.")


def _block_index(blocks) -> list[np.ndarray]:
    labels = np.asarray([str(b) for b in blocks])
    _, inv = np.unique(labels, return_inverse=True)
    return [np.flatnonzero(inv == k) for k in range(inv.max() + 1)]


def _block_draws(groups: list[np.ndarray], n_boot: int, seed: int):
    rng = np.random.default_rng(seed)
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        yield np.concatenate([groups[k] for k in pick])


def auc_interval(scores, labels, blocks, n_boot: int = 1000, seed: int = 0) -> dict:
    """AUC with a percentile bootstrap interval that resamples whole blocks."""
    s, y = np.asarray(scores, float), np.asarray(labels, int)
    groups = _block_index(blocks)
    boots = [a for i in _block_draws(groups, n_boot, seed) if (a := auc(s[i], y[i])) == a]
    lo, hi = np.quantile(boots, [0.025, 0.975]) if boots else (float("nan"), float("nan"))
    return {"auc": auc(s, y), "low": float(lo), "high": float(hi), "blocks": len(groups)}


def paired_auc(scores_a, scores_b, labels, blocks, n_boot: int = 1000, seed: int = 0) -> dict:
    """AUC(a) - AUC(b) on the same reports, resampling whole blocks jointly."""
    a, b, y = (np.asarray(v, float) for v in (scores_a, scores_b, labels))
    groups = _block_index(blocks)
    d = [x for i in _block_draws(groups, n_boot, seed) if (x := auc(a[i], y[i]) - auc(b[i], y[i])) == x]
    return {"diff": auc(a, y) - auc(b, y), "low": float(np.quantile(d, 0.025)),
            "high": float(np.quantile(d, 0.975)), "blocks": len(groups), "n_boot": int(len(d))}


def verdict(name_a: str, name_b: str, low: float, high: float, what: str = "day-block") -> str:
    """A plain sentence generated from the interval for AUC(a) - AUC(b)."""
    if low > 0:
        return (f"{name_a} ranks reports better than {name_b}: the {what} interval for the "
                f"difference in AUC ({low:+.3f} to {high:+.3f}) is above zero.")
    if high < 0:
        return (f"{name_b} ranks reports better than {name_a}: the {what} interval for the "
                f"difference in AUC ({low:+.3f} to {high:+.3f}) is below zero.")
    return (f"{name_a} and {name_b} are not separated: the {what} interval for the difference "
            f"in AUC ({low:+.3f} to {high:+.3f}) includes zero.")


def score_models(models, cases, truths, n_boot: int) -> tuple[dict, dict, dict]:
    """Per model: AUC with day-block and hour-block intervals."""
    y = np.array([t["label"] for t in truths], int)
    days = [c.target_time.strftime("%Y-%m-%d") for c in cases]
    hours = [c.target_time.strftime("%Y-%m-%dT%H") for c in cases]
    scores = {m.name: np.array([m.predict(c)["risk"] for c in cases]) for m in models}
    by_day = {name: auc_interval(s, y, days, n_boot) for name, s in scores.items()}
    by_hour = {name: auc_interval(s, y, hours, n_boot) for name, s in scores.items()}
    return by_day, by_hour, scores


def run(days: int = 30, n_boot: int = 1000, log=print) -> dict:
    now = utcnow().replace(microsecond=0)
    log(f"backtest over the last {days} days, run at {to_iso(now)}")
    store = F.FieldStore()
    F.ensure_backfill(store, now, days=max(31, days + 1), log=log)
    chunks = store.chunks()
    reports = in_domain(load_iem_days(days, now, log=log))
    reports = reports[reports["time"] >= reports["time"].max() - timedelta(days=days)]
    log(f"  {len(reports)} turbulence reports in the continental US box")

    up = reports[reports["fl"] >= 200]
    low = reports[(reports["fl"] >= 100) & (reports["fl"] < 200)]
    cases_all, truths_all, skipped = build_cases(up, chunks, log=log)
    n_lm = sum(1 for t in truths_all if t["code"] == 3)
    cases, truths = labelled(cases_all, truths_all)
    train, _val, test = time_split(cases)
    tmap = {c.case_id: t for c, t in zip(cases, truths)}
    tr_truth = [tmap[c.case_id] for c in train]
    te_truth = [tmap[c.case_id] for c in test]
    models = all_models()
    for m in models:
        m.fit(train, tr_truth)
    blend = next(m for m in models if m.name == "SeedBlend")

    table, table_hour, scores = score_models(models, test, te_truth, n_boot)
    y = np.array([t["label"] for t in te_truth], int)
    days_ = [c.target_time.strftime("%Y-%m-%d") for c in test]
    hours_ = [c.target_time.strftime("%Y-%m-%dT%H") for c in test]
    err_b = (scores["SeedBlend"] - y) ** 2
    err_c = (scores["Climatology"] - y) ** 2
    brier = paired_block_bootstrap(err_b, err_c, days_, n_boot=n_boot)
    paired = paired_auc(scores["SeedBlend"], scores["Ellrod1"], y, days_, n_boot)
    paired_hour = paired_auc(scores["SeedBlend"], scores["Ellrod1"], y, hours_, n_boot)
    paired["verdict"] = verdict("SeedBlend", "Ellrod1", paired["low"], paired["high"], "day-block")
    paired_hour["verdict"] = verdict("SeedBlend", "Ellrod1", paired_hour["low"], paired_hour["high"], "hour-block")

    low_cases, low_truths, _ = build_cases(low, chunks)
    low_cases, low_truths = labelled(low_cases, low_truths)
    t_cut = min(c.issue_time for c in test) if test else now
    low_test = [(c, t) for c, t in zip(low_cases, low_truths) if c.issue_time >= t_cut]
    low_table = {}
    if low_test:
        low_table, _, _ = score_models(models, [c for c, _ in low_test], [t for _, t in low_test], n_boot)

    pos = int(sum(t["label"] for t in truths))
    result = {
        "generated_at": to_iso(now),
        "days": days,
        "reports_in_box": int(len(reports)),
        "upper_level_reports": int(len(up)),
        "cases_built": len(cases_all),
        "skipped_no_fields": int(skipped),
        "dropped_light_moderate": int(n_lm),
        "labelled_cases": len(cases),
        "positives": pos,
        "base_rate": pos / len(cases) if cases else float("nan"),
        "train": {"n": len(train), "positives": int(sum(t["label"] for t in tr_truth)),
                  "from": to_iso(train[0].target_time), "to": to_iso(train[-1].target_time)},
        "test": {"n": len(test), "positives": int(y.sum()),
                 "from": to_iso(test[0].issue_time), "to": to_iso(test[-1].target_time)},
        "interval_method": INTERVALS,
        "table": [{"model": name, **{k: round(v, 4) for k, v in r.items()},
                   "hour_block_low": round(table_hour[name]["low"], 4),
                   "hour_block_high": round(table_hour[name]["high"], 4),
                   "n": len(test), "positives": int(y.sum())} for name, r in table.items()],
        "brier": {"SeedBlend": brier.mean_a, "Climatology": brier.mean_b,
                  "difference": brier.diff.mean, "difference_low": brier.diff.low,
                  "difference_high": brier.diff.high, "blocks": "UTC days",
                  "skill_vs_climatology": 1 - brier.mean_a / brier.mean_b},
        "paired_SeedBlend_vs_Ellrod1": paired,
        "paired_SeedBlend_vs_Ellrod1_hour_blocks": paired_hour,
        "verdict": paired["verdict"],
        "seedblend_coefficients": blend.coefficients(),
        "lower_levels": {"note": "FL100-FL199 test-period reports; fields stop at 500 hPa (about FL183), "
                                 "so lower points use the 500 hPa values. Models fitted on upper levels.",
                         "n": len(low_test),
                         "positives": int(sum(t["label"] for _, t in low_test)),
                         "table": [{"model": k, **{a: round(b, 4) for a, b in v.items()}}
                                   for k, v in low_table.items()]},
        "caveats": [CAVEAT, BIAS, PREVIOUS_RUNS, INTERVALS,
                    "Deformation on a 2.5-degree grid is far coarser than in the operational Ellrod "
                    "index, which uses model grids of tens of kilometres.",
                    "Light-moderate reports (code 3) are dropped as ambiguous; a report with several "
                    "layers gives one labelled point per layer.",
                    "Reports come from the Iowa Environmental Mesonet PIREP archive."],
        "n_boot": n_boot,
    }
    out = net.RUNS / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    print_report(result, blend.describe(), log)
    log(f"saved {out.relative_to(net.ROOT)}")
    return result


def print_report(r: dict, coef_text: str, log=print) -> None:
    log("")
    log(f"Reports: {r['upper_level_reports']} upper-level (FL200+) labelled report layers; "
        f"{r['labelled_cases']} with a class ({r['positives']} moderate-or-greater, "
        f"base rate {r['base_rate']:.3f}); {r['dropped_light_moderate']} light-moderate dropped; "
        f"{r['skipped_no_fields']} without fields.")
    log(f"Train {r['train']['n']} ({r['train']['from']} to {r['train']['to']}), "
        f"test {r['test']['n']} ({r['test']['from']} to {r['test']['to']}), "
        f"{r['test']['positives']} positives in test.")
    log("Hindsight fields: an analysis-time comparison of indices, not a forecast test (see caveats).")
    log("")
    log(f"{'model':<12} {'test AUC':>8}   day-block 95%    hour-block 95%")
    for row in r["table"]:
        log(f"{row['model']:<12} {row['auc']:>8.3f}   [{row['low']:.3f}, {row['high']:.3f}]   "
            f"[{row['hour_block_low']:.3f}, {row['hour_block_high']:.3f}]")
    b = r["brier"]
    log("")
    log(f"Brier SeedBlend {b['SeedBlend']:.4f} vs Climatology {b['Climatology']:.4f} "
        f"(difference {b['difference']:+.4f}, day-block 95% [{b['difference_low']:+.4f}, {b['difference_high']:+.4f}]); "
        f"skill {b['skill_vs_climatology']:+.3f}")
    p, ph = r["paired_SeedBlend_vs_Ellrod1"], r["paired_SeedBlend_vs_Ellrod1_hour_blocks"]
    log(f"AUC SeedBlend - Ellrod1: {p['diff']:+.3f}, day-block 95% [{p['low']:+.3f}, {p['high']:+.3f}] "
        f"({p['blocks']} days); hour-block [{ph['low']:+.3f}, {ph['high']:+.3f}] ({ph['blocks']} hours)")
    log(f"Verdict: {r['verdict']}")
    lo = r["lower_levels"]
    if lo["table"]:
        log("")
        log(f"Lower levels (FL100-FL199, separate; {lo['n']} layers, {lo['positives']} positive), day-block 95%:")
        for row in lo["table"]:
            log(f"  {row['model']:<12} {row['auc']:.3f} [{row['low']:.3f}, {row['high']:.3f}]")
    log("")
    log(coef_text)
    log("")
    for c in r["caveats"][:3]:
        log(c)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args(argv)
    run(a.days, a.boot)


if __name__ == "__main__":
    main()
