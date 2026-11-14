"""Promote a forecaster written by the research lab into the live sealed job.

    .venv/bin/python -m lab.tasks.coast.promote --run runs/lab/coast_water/<run_id> [--name lab-a]

copies the run's `incumbent.py`, lints it, evaluates it once on the select and
confirm windows through `lab.evalpool`, and writes

    runs/lab/promoted/coast_water/<name>.py     the candidate source, unchanged
    runs/lab/promoted/coast_water/<name>.json   sidecar: source hash, run, generation,
                                                select and confirm loss, promoted time

A candidate that fails the lint or the evaluation is refused (nothing written).

In the live job (`lab.tasks.coast.live`), `PromotedRunner` gives every promoted
forecaster exactly the inputs `CoastWaterProblem` gives candidates: the enriched
cases, history built with previous-run weather, and the newest forecast for live
cases. Once an hour it fits each one in the sandbox on history strictly before now
(a stratified sample of the live fit window, the size of the lab's train window)
and predicts the live cases; the predictions are sealed under the model name
`Lab:<name>`. A forecaster that fails or times out is logged and marked in the
state, and nothing is sealed for it that hour.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from lab.core.timeutil import to_iso, utcnow
from lab.sandbox import lint
from lab.sandbox import runner as sandbox

from .problem import (CoastData, CoastWaterProblem, build_problem_case, issue_ok, load_buoys_for_cases,
                      stratified_pick)
from .water import LEADS, TASK

ROOT = Path(__file__).resolve().parents[3]
PROMOTED_DIR = ROOT / "runs" / "lab" / "promoted" / "coast_water"
MODEL_PREFIX = "Lab:"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
N_HISTORY = 12000          # the size of the lab's train window
HISTORY_SEED = 20261114


class Refused(Exception):
    pass


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _generation(run_dir: Path, source: str) -> int | None:
    """First generation at which this source became the run's incumbent."""
    try:
        summary = json.loads((run_dir / "summary.json").read_text())
    except (OSError, ValueError):
        return None
    short = sha256(source)[:16]
    for row in summary.get("curve", []):
        if row.get("incumbent_hash") == short:
            return int(row["generation"])
    return None


def promote(run_dir: str | Path, name: str | None = None, *, out_dir: Path | None = None,
            problem=None, log=print) -> dict:
    """Lint, evaluate once on select + confirm, then write <name>.py and <name>.json."""
    from lab.evalpool.evaluate import evaluate
    run_dir = Path(run_dir)
    out_dir = Path(out_dir or PROMOTED_DIR)
    src_path = run_dir / "incumbent.py"
    if not src_path.exists():
        raise Refused(f"no incumbent.py in {run_dir}")
    source = src_path.read_text(encoding="utf-8")
    run_id = run_dir.name
    name = name or ("lab-" + run_id.split("-")[-1]).lower()
    if not NAME_RE.match(name):
        raise Refused(f"name {name!r} must be lower-case letters, digits and dashes (at most 40)")
    problems = lint(source)
    if problems:
        raise Refused(f"lint failed ({len(problems)} problem(s)); first: {problems[0]}")
    problem = problem or CoastWaterProblem()
    splits = problem.splits()
    log(f"evaluating {src_path} on select and confirm ...")
    res = evaluate(problem, source, splits, ("select", "confirm"))
    if not res.ok:
        raise Refused(f"evaluation failed: {res.error}")
    sel, con = res.summary["select"], res.summary["confirm"]
    if not (np.isfinite(sel["mean_loss"]) and np.isfinite(con["mean_loss"])):
        raise Refused("evaluation gave no finite loss")
    sidecar = {
        "name": name,
        "model": MODEL_PREFIX + name,
        "source_sha256": sha256(source),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "generation": _generation(run_dir, source),
        "select_loss": sel["mean_loss"],
        "confirm_loss": con["mean_loss"],
        "select_by_lead": {h: v["mean_loss"] for h, v in sel.get("by_lead", {}).items()},
        "confirm_by_lead": {h: v["mean_loss"] for h, v in con.get("by_lead", {}).items()},
        "loss": problem.loss_name,
        "splits": problem.cache_key() if hasattr(problem, "cache_key") else None,
        "promoted_at": to_iso(utcnow()),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".{name}.py.tmp"
    tmp.write_text(source, encoding="utf-8")
    tmp.replace(out_dir / f"{name}.py")
    (out_dir / f"{name}.json").write_text(json.dumps(sidecar, indent=1))
    log(f"promoted {name}: select {sidecar['select_loss']:.3f}, confirm {sidecar['confirm_loss']:.3f} "
        f"({problem.loss_name}); generation {sidecar['generation']} of run {run_id}")
    return sidecar


@dataclass
class Promoted:
    name: str
    source: str
    sidecar: dict

    @property
    def model(self) -> str:
        return MODEL_PREFIX + self.name


def load_promoted(directory: Path, log=print) -> list[Promoted]:
    """Every <name>.py with a matching sidecar whose hash agrees with the source."""
    out = []
    directory = Path(directory)
    if not directory.is_dir():
        return out
    for js in sorted(directory.glob("*.json")):
        name = js.stem
        py = directory / f"{name}.py"
        if not NAME_RE.match(name) or not py.exists():
            continue
        try:
            sidecar = json.loads(js.read_text())
            source = py.read_text(encoding="utf-8")
        except (OSError, ValueError) as e:
            log(f"  promoted {name}: unreadable ({e}); skipped")
            continue
        if sidecar.get("source_sha256") != sha256(source):
            log(f"  promoted {name}: source does not match its sidecar hash; skipped")
            continue
        out.append(Promoted(name, source, sidecar))
    return out


def floor6(t: datetime) -> datetime:
    t = t.replace(second=0, microsecond=0)
    return t - timedelta(minutes=t.minute % 6)


class PromotedRunner:
    """Runs the promoted forecasters inside the live cycle, at most once an hour."""

    def __init__(self, client, directory: Path = PROMOTED_DIR, fit_days: int = 45, n_history: int = N_HISTORY,
                 time_limit_s: float = 180, memory_limit_mb: int = 1536, log=print, log_path=None):
        self.client = client
        self.directory = Path(directory)
        self.fit_days = fit_days
        self.n_history = n_history
        self.time_limit_s = time_limit_s
        self.memory_limit_mb = memory_limit_mb
        self.log = log
        self.log_path = log_path
        self.ran_hour: datetime | None = None
        self.status: dict[str, dict] = {}
        self.promoted: list[Promoted] = []
        self.timing: dict = {}

    # ---- cases
    def history(self, data: CoastData, now: datetime):
        """Stratified sample of hourly cases in the fit window with target < now."""
        start = now - timedelta(days=self.fit_days)
        keys = []
        for sid, f in data.feats.items():
            t = pd.Timestamp(start).ceil("h").to_pydatetime()
            while t < now:
                if issue_ok(f, f.index(t)):
                    for h in LEADS:
                        target = t + timedelta(hours=h)
                        if target < now and f.truth_at(target) is not None:
                            keys.append((sid, t, h))
                t += timedelta(hours=1)
        groups: dict[tuple, list[int]] = {}
        for k, (sid, _, h) in enumerate(keys):
            groups.setdefault((sid, h), []).append(k)
        picked = stratified_pick(groups, self.n_history, np.random.default_rng(HISTORY_SEED))
        picked.sort(key=lambda k: (keys[k][1], keys[k][0], keys[k][2]))
        cases, truths = [], []
        for k in picked:
            sid, t, h = keys[k]
            c = build_problem_case(data, sid, t, h, weather="runs")
            if c is None:
                continue
            cases.append(c)
            truths.append({"residual_m": float(data.feats[sid].truth_at(c.target_time))})
        return cases, truths

    def live_cases(self, data: CoastData, now: datetime):
        issue = floor6(now)
        out = []
        for sid in data.feats:
            for h in LEADS:
                c = build_problem_case(data, sid, issue, h, weather="latest")
                if c is not None:
                    out.append(c)
        return out

    # ---- the hourly run
    def forecasts(self, now: datetime, feats: dict, existing: set) -> list[dict]:
        hour = now.replace(minute=0, second=0, microsecond=0)
        if self.ran_hour == hour:
            return []
        self.promoted = load_promoted(self.directory, log=self.log)
        if not self.promoted:
            return []
        self.ran_hour = hour
        t0 = time.time()
        buoys = load_buoys_for_cases(self.client, now, self.fit_days, log=self.log, latest=True)
        data = CoastData.from_feats(feats, buoys)
        hist, truths = self.history(data, now)
        live = self.live_cases(data, now)
        t1 = time.time()
        prepared = sandbox.prepare_cases(hist, truths, live)    # once per refit, shared by all
        t2 = time.time()
        self.timing = {"cases_s": round(t1 - t0, 2), "encode_s": round(t2 - t1, 2), "history_cases": len(hist),
                       "live_cases": len(live), "runs_s": {}}
        out = []
        for p in self.promoted:
            t3 = time.time()
            try:
                res = sandbox.run_prepared(p.source, prepared, time_limit_s=self.time_limit_s,
                                           memory_limit_mb=self.memory_limit_mb, log_path=self.log_path)
                err = res.error if not res.ok else None
                preds = res.predictions if res.ok else None
                if preds is not None:
                    bad = [i for i, q in enumerate(preds) if "residual_m" not in q]
                    if bad:
                        err, preds = f"bad output: {len(bad)} prediction(s) lack residual_m", None
            except Exception as e:  # noqa: BLE001 - one forecaster must never break the cycle
                err, preds = f"{type(e).__name__}: {e}", None
            dt = round(time.time() - t3, 2)
            self.timing["runs_s"][p.name] = dt
            st = {"last_run": to_iso(now), "seconds": dt, "ok": err is None, "error": err, "sealed": 0}
            if err is not None:
                self.log(f"  promoted {p.model} failed: {err.splitlines()[0][:300]}")
                self.status[p.name] = st
                continue
            for c, q in zip(live, preds):
                f = feats[c.inputs["station"]]
                key = (c.inputs["station"], p.model, c.inputs["lead_h"], c.target_time.strftime("%Y%m%d%H"))
                if key in existing:
                    continue
                r = float(q["residual_m"])
                tide = f.tide_at(c.target_time)
                out.append({
                    "task": TASK, "model": p.model, "case_id": c.case_id,
                    "issued_at": c.issue_time, "target_time": c.target_time,
                    "prediction": {"residual_m": round(r, 4), "tide_m": round(tide, 4),
                                   "water_level_m": round(tide + r, 4)},
                    "meta": {"station": c.inputs["station"], "station_name": f.sd.name,
                             "lead_h": c.inputs["lead_h"], "r_now_m": round(c.inputs["r_now"], 4),
                             "data_age_min": c.inputs["r_age_min"], "has_weather": c.inputs["has_weather"],
                             "promoted": p.name, "source_sha256": p.sidecar.get("source_sha256")},
                })
                existing.add(key)
                st["sealed"] += 1
            self.status[p.name] = st
        self.timing["total_s"] = round(time.time() - t0, 2)
        self.log(f"  promoted forecasters: {len(out)} forecasts from {len(self.promoted)} "
                 f"({self.timing['history_cases']} history cases) in {self.timing['total_s']} s")
        return out

    def state(self) -> list[dict]:
        rows = []
        for p in self.promoted:
            s = p.sidecar
            rows.append({"name": p.name, "model": p.model, "source_sha256": s.get("source_sha256"),
                         "run_id": s.get("run_id"), "generation": s.get("generation"),
                         "select_loss": s.get("select_loss"), "confirm_loss": s.get("confirm_loss"),
                         "promoted_at": s.get("promoted_at"), "status": self.status.get(p.name)})
        return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Promote a lab run's incumbent into the live coast job.")
    ap.add_argument("--run", required=True, help="runs/lab/coast_water/<run_id>")
    ap.add_argument("--name", default=None, help="lower-case name, e.g. lab-a (default lab-<run suffix>)")
    ap.add_argument("--out", type=Path, default=None, help=f"default {PROMOTED_DIR.relative_to(ROOT)}")
    a = ap.parse_args(argv)
    try:
        promote(a.run, a.name, out_dir=a.out)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
