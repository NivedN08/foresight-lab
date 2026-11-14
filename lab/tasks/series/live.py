"""Sealed live forecasts for a SeriesProblem.

    .venv/bin/python -m lab.tasks.series.live CONFIG.json --candidate a.py [--candidate b.py] \
        [--ledger runs/series/live]
    .venv/bin/python -m lab.tasks.series.live CONFIG.json --grade newer_copy.csv [--ledger runs/series/live]

Sealing: each candidate file and each baseline is fitted, in the sandbox, on cases drawn
from ALL the data in the file (every target up to the end of the data), then predicts one
case per series and lead issued at the last grid time of the data. Each prediction is
appended to a SealedLedger (lab/core/ledger.py), hash-chained. A grid value at time t is
the mean of the observations in (t - step, t], so a forecast is sealed only if that whole
interval is still in the future: target - step >= now. Two kinds of skip are counted and
reported separately: targets already in the past (the file is stale), and targets whose
interval has already begun (part of what will be averaged has already happened).

Grading: reads a newer copy of the data, puts it on the same grid (same step and origin as
recorded in each sealed record), and grades every due record whose target value is now in
the file. Then prints the mean loss per candidate and lead and verifies the whole chain.
"""
from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from lab.core.ledger import SealedLedger
from lab.core.timeutil import from_iso, to_iso, utcnow
from lab.sandbox import run_candidate

from .problem import ROOT, SeriesProblem

DEFAULT_LEDGER = ROOT / "runs" / "series" / "live"


def task_name(p: SeriesProblem) -> str:
    return f"series:{p.name}"


def seal_live(p: SeriesProblem, candidates: dict[str, str], ledger_dir: str | Path, *,
              baselines: bool = True, now: datetime | None = None, time_limit_s: float = 300,
              log=print) -> dict:
    """Fit each candidate (name -> source) and the baselines on all data in the file and
    seal forecasts for every series and lead after the end of the data. Returns counts."""
    g = p.grid()
    now = now or utcnow()
    end = g.end_index
    data_end = g.time(end)
    train_cases, train_truths = p.all_history_cases(g)
    live, no_data = p.live_cases(g)
    log(f"data ends {to_iso(data_end)}; fitting on {len(train_cases)} cases; {len(live)} live cases "
        f"({len(no_data)} series skipped: no value in the last {p.history} steps)")
    sources = dict(candidates)
    if baselines:
        sources.update({f"baseline:{n}": s for n, s in p.baseline_sources().items()})
    ledger = SealedLedger(ledger_dir)
    out = {"sealed": 0, "skipped_past": 0, "skipped_open_bucket": 0, "failed": [], "data_end": to_iso(data_end),
           "no_data": no_data}
    for name, src in sources.items():
        res = run_candidate(src, train_cases, train_truths, live, time_limit_s=time_limit_s, memory_limit_mb=1536)
        if not res.ok:
            log(f"  {name}: FAILED ({res.error})")
            out["failed"].append(name)
            continue
        sha = hashlib.sha256(src.encode("utf-8", "replace")).hexdigest()
        recs, past, open_bucket = [], 0, 0
        for c, pred in zip(live, res.predictions):
            if c.target_time <= now:
                past += 1
                continue
            if c.target_time - g.step.to_pytimedelta() < now:
                open_bucket += 1
                continue
            recs.append({
                "task": task_name(p), "model": name, "case_id": c.case_id, "issued_at": c.issue_time,
                "target_time": c.target_time, "prediction": {"y": float(pred["y"])},
                "meta": {"series_id": c.inputs["series_id"], "lead": int(c.inputs["lead"]),
                         "lead_h": int(c.inputs["lead_h"]), "source_sha256": sha, "data_sha256": p.file_hash(),
                         "data_end": to_iso(data_end), "origin": to_iso(g.origin.to_pydatetime()),
                         "step_s": int(g.step.total_seconds()), "log_target": p.log_target},
            })
        if recs:
            ledger.seal_many(recs, now=now)
        out["sealed"] += len(recs)
        out["skipped_past"] += past
        out["skipped_open_bucket"] += open_bucket
        log(f"  {name}: sealed {len(recs)}; skipped {past} whose target is not after now ({to_iso(now)}) and "
            f"{open_bucket} whose averaging interval (target - step, target] has already begun")
    if out["skipped_past"]:
        log(f"skipped {out['skipped_past']} forecasts in total because their target time had already passed "
            "(the data ends too long before now for those leads)")
    if out["skipped_open_bucket"]:
        log(f"skipped {out['skipped_open_bucket']} forecasts in total because the interval averaged into their "
            "target value had already begun (only targets with target - step >= now are sealed)")
    ok, n, msg = ledger.verify()
    log(f"ledger {ledger.dir}: {n} records, chain {msg}")
    out["verified"] = ok
    return out


def grade_live(p: SeriesProblem, newer_path: str | Path, ledger_dir: str | Path, *,
               now: datetime | None = None, log=print) -> dict:
    """Grade the due records of this problem from a newer copy of the data."""
    now = now or utcnow()
    ledger = SealedLedger(ledger_dir)
    due = [r for r in ledger.ungraded_due(now) if r["task"] == task_name(p)]
    grids: dict[tuple[str, int], object] = {}
    items, missing = [], 0
    for r in due:
        m = r["meta"]
        key = (m["origin"], m["step_s"])
        if key not in grids:
            grids[key] = p.load_grid(newer_path, step=pd.Timedelta(seconds=m["step_s"]),
                                     origin=pd.Timestamp(from_iso(m["origin"])), notes=[])
        g = grids[key]
        pos = {s: k for k, s in enumerate(g.series)}
        k = g.index_of(from_iso(r["target_time"]))
        s = pos.get(m["series_id"])
        if s is None or abs(k - round(k)) > 1e-9 or not (0 <= round(k) < g.n_t) \
                or not np.isfinite(g.y[s, int(round(k))]):
            missing += 1
            continue
        items.append((r["seq"], {"y": float(g.y[s, int(round(k))])}))
    if items:
        ledger.grade_many(items, now=now)
    log(f"graded {len(items)} due records; {missing} due records have no value in {newer_path} yet")
    report = scores(p, ledger)
    ok, n, msg = ledger.verify()
    log(f"ledger {ledger.dir}: {n} records, chain {msg}")
    return {"graded": len(items), "missing": missing, "scores": report, "verified": ok}


def scores(p: SeriesProblem, ledger: SealedLedger, log=print) -> dict:
    """Mean loss per model and lead over every graded record of this problem."""
    rows = defaultdict(lambda: ([], []))
    for r in ledger.joined():
        if r["task"] != task_name(p) or r["grade"] is None:
            continue
        for key in (str(r["meta"]["lead"]), "all"):
            preds, truths = rows[(r["model"], key)]
            preds.append(r["prediction"])
            truths.append(r["grade"]["truth"])
    out: dict[str, dict] = {}
    for (model, lead), (preds, truths) in rows.items():
        loss = p.losses(preds, truths)
        out.setdefault(model, {})[lead] = {"mean_loss": float(np.nanmean(loss)) if np.isfinite(loss).any()
                                           else float("nan"), "n": int(np.isfinite(loss).sum())}
    if out:
        leads = sorted({k for v in out.values() for k in v if k != "all"}, key=int)
        log(f"\ngraded live forecasts, mean {p.loss_name}:")
        log(f"{'model':<34}{'all':>10}" + "".join(f"{'lead ' + k:>10}" for k in leads))
        for model in sorted(out):
            v = out[model]
            log(f"{model:<34}{v['all']['mean_loss']:>10.4g}" + "".join(
                f"{v[k]['mean_loss']:>10.4g}" if k in v else f"{'':>10}" for k in leads)
                + f"   (n = {v['all']['n']})")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--candidate", action="append", default=[], help="candidate file (repeatable)")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--grade", default=None, metavar="NEWER_FILE", help="grade due records from a newer copy")
    ap.add_argument("--no-baselines", action="store_true")
    a = ap.parse_args(argv)
    p = SeriesProblem.from_config(a.config)
    ledger = Path(a.ledger)
    if not ledger.is_absolute():
        ledger = ROOT / ledger
    if a.grade:
        res = grade_live(p, a.grade, ledger)
        return 0 if res["verified"] else 1
    if not a.candidate and a.no_baselines:
        ap.error("nothing to seal: give --candidate or drop --no-baselines")
    cands = {}
    for path in a.candidate:
        cands[Path(path).stem] = Path(path).read_text(encoding="utf-8")
    res = seal_live(p, cands, ledger, baselines=not a.no_baselines)
    return 0 if res["verified"] and not res["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
