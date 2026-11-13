"""Backtest the turbulence forecasters on the last N days of pilot reports.

    .venv/bin/python -m lab.tasks.turb.backtest --days 30

One case per upper-level report (flight level 200 and above), split by time
with lab.core.task.time_split (oldest 60% train, next 20% validation, newest
20% test). Models are fitted on the training part and scored on the test part.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta

import numpy as np

from lab.core.stats import auc, bootstrap_ci
from lab.core.task import time_split
from lab.core.timeutil import to_iso, utcnow

from . import fields as F
from . import net
from .cases import build_cases, labelled
from .models import all_models
from .pireps import in_domain, load_iem_days

CAVEAT = ("Caveat: Open-Meteo's past field values are its short-lead forecasts stitched together, "
          "so this backtest uses fields at lead under about one hour. It does not test multi-hour "
          "lead skill; the live sealed forecasts (leads 1-6 h) do.")
BIAS = ("Pilot reports are biased toward where aircraft fly and toward reporting bumps rather "
        "than smooth air, so the base rate and AUC describe reported turbulence, not all airspace.")


def auc_interval(scores, labels, n_boot: int = 1000, seed: int = 0) -> dict:
    """AUC with a percentile bootstrap interval (reports resampled with replacement)."""
    s, y = np.asarray(scores, float), np.asarray(labels, int)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        i = rng.integers(0, len(s), len(s))
        a = auc(s[i], y[i])
        if a == a:
            boots.append(a)
    lo, hi = np.quantile(boots, [0.025, 0.975]) if boots else (float("nan"), float("nan"))
    return {"auc": auc(s, y), "low": float(lo), "high": float(hi)}


def paired_auc(scores_a, scores_b, labels, n_boot: int = 1000, seed: int = 0) -> dict:
    """AUC(a) - AUC(b) on the same reports, resampling reports jointly."""
    a, b, y = (np.asarray(v, float) for v in (scores_a, scores_b, labels))
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(n_boot):
        i = rng.integers(0, len(y), len(y))
        da = auc(a[i], y[i]) - auc(b[i], y[i])
        if da == da:
            d.append(da)
    d = np.array(d)
    return {"diff": auc(a, y) - auc(b, y), "low": float(np.quantile(d, 0.025)),
            "high": float(np.quantile(d, 0.975)), "share_a_better": float((d > 0).mean()),
            "n_boot": int(len(d))}


def score_models(models, cases, truths, n_boot: int) -> tuple[dict, dict]:
    y = np.array([t["label"] for t in truths], int)
    scores = {m.name: np.array([m.predict(c)["risk"] for c in cases]) for m in models}
    table = {name: auc_interval(s, y, n_boot) for name, s in scores.items()}
    return table, scores


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

    table, scores = score_models(models, test, te_truth, n_boot)
    y = np.array([t["label"] for t in te_truth], int)
    brier = bootstrap_ci((scores["SeedBlend"] - y) ** 2, n_boot=n_boot)
    brier_clim = bootstrap_ci((scores["Climatology"] - y) ** 2, n_boot=n_boot)
    paired = paired_auc(scores["SeedBlend"], scores["Ellrod1"], y, n_boot)

    low_cases, low_truths, _ = build_cases(low, chunks)
    low_cases, low_truths = labelled(low_cases, low_truths)
    t_cut = min(c.issue_time for c in test) if test else now
    low_test = [(c, t) for c, t in zip(low_cases, low_truths) if c.issue_time >= t_cut]
    low_table = {}
    if low_test:
        low_table, _ = score_models(models, [c for c, _ in low_test], [t for _, t in low_test], n_boot)

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
        "table": [{"model": name, **{k: round(v, 4) for k, v in r.items()}, "n": len(test),
                   "positives": int(y.sum())} for name, r in table.items()],
        "brier": {"SeedBlend": {"mean": brier.mean, "low": brier.low, "high": brier.high},
                  "Climatology": {"mean": brier_clim.mean, "low": brier_clim.low, "high": brier_clim.high},
                  "skill_vs_climatology": 1 - brier.mean / brier_clim.mean},
        "paired_SeedBlend_vs_Ellrod1": paired,
        "seedblend_coefficients": blend.coefficients(),
        "lower_levels": {"note": "FL100-FL199 test-period reports; fields stop at 500 hPa (about FL183), "
                                 "so lower points use the 500 hPa values. Models fitted on upper levels.",
                         "n": len(low_test),
                         "positives": int(sum(t["label"] for _, t in low_test)),
                         "table": [{"model": k, **{a: round(b, 4) for a, b in v.items()}}
                                   for k, v in low_table.items()]},
        "caveats": [CAVEAT, BIAS,
                    "Light-moderate reports (code 3) are dropped as ambiguous.",
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
    log(f"Reports: {r['upper_level_reports']} upper-level (FL200+) turbulence reports; "
        f"{r['labelled_cases']} with a class ({r['positives']} moderate-or-greater, "
        f"base rate {r['base_rate']:.3f}); {r['dropped_light_moderate']} light-moderate dropped; "
        f"{r['skipped_no_fields']} without fields.")
    log(f"Train {r['train']['n']} reports ({r['train']['from']} to {r['train']['to']}), "
        f"test {r['test']['n']} reports ({r['test']['from']} to {r['test']['to']}), "
        f"{r['test']['positives']} positives in test.")
    log("")
    log(f"{'model':<12} {'test AUC':>8}   95% interval")
    for row in r["table"]:
        log(f"{row['model']:<12} {row['auc']:>8.3f}   [{row['low']:.3f}, {row['high']:.3f}]")
    b = r["brier"]
    log("")
    log(f"Brier SeedBlend {b['SeedBlend']['mean']:.4f} [{b['SeedBlend']['low']:.4f}, {b['SeedBlend']['high']:.4f}]"
        f"  vs Climatology {b['Climatology']['mean']:.4f}; skill {b['skill_vs_climatology']:+.3f}")
    p = r["paired_SeedBlend_vs_Ellrod1"]
    log(f"Paired AUC SeedBlend - Ellrod1: {p['diff']:+.3f} [{p['low']:+.3f}, {p['high']:+.3f}], "
        f"SeedBlend better in {100 * p['share_a_better']:.1f}% of resamples")
    lo = r["lower_levels"]
    if lo["table"]:
        log("")
        log(f"Lower levels (FL100-FL199, separate; {lo['n']} reports, {lo['positives']} positive):")
        for row in lo["table"]:
            log(f"  {row['model']:<12} {row['auc']:.3f} [{row['low']:.3f}, {row['high']:.3f}]")
    log("")
    log(coef_text)
    log("")
    for c in r["caveats"][:2]:
        log(c)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args(argv)
    run(a.days, a.boot)


if __name__ == "__main__":
    main()
