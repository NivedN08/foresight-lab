"""Live sealed turbulence forecasts.

    .venv/bin/python -m lab.tasks.turb.live once
    .venv/bin/python -m lab.tasks.turb.live run --interval 1800

Each cycle:
  1. refresh the forecast fields (Open-Meteo; at most every 3 hours to stay
     inside the free daily budget);
  2. fit the models on reports strictly before now;
  3. for each model and each valid hour +1..+6 h, seal one risk grid
     (level x lat x lon) in SealedLedger("runs/turb/live");
  4. fetch the latest pilot reports (Aviation Weather Center + Iowa
     Environmental Mesonet), keep each raw response under cache/turb/raw/ and
     append the reports to cache/turb/pireps_live_reports.csv (append-only;
     duplicates are removed when it is read);
  5. grade every sealed record whose valid hour passed at least 75 minutes
     ago (so late reports count), with the upper-level report layers within
     +-30 minutes of the valid time;
  6. verify the ledger and write runs/turb/live/state.json for the dashboard.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from lab.core.ledger import SealedLedger
from lab.core.stats import auc
from lab.core.timeutil import from_iso, to_iso, utcnow

from . import fields as F
from . import gairmet as GA
from . import net
from . import pireps as P
from .cases import build_cases, labelled
from .diagnostics import diagnose
from .interp import fl_to_pressure, interp_grid3
from .models import all_models

TASK = "turb"
LEADS = range(1, 7)
GRADE_DELAY = timedelta(minutes=75)
LEAD_STEPS = range(1, 7)  # scoreboard: latest record sealed at least k hours before the valid time
TRUTH_WINDOW = timedelta(minutes=30)
FIELD_REFRESH = timedelta(hours=3)
STATE_MODELS = ("SeedBlend", "Ellrod1")
TOP_SHARE = 0.20
LEDGER_DIR = net.RUNS / "live"


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def _round_grid(a: np.ndarray, nd: int = 3) -> list:
    a = np.round(np.asarray(a, float), nd)
    return [[[None if not np.isfinite(v) else float(v) for v in row] for row in lev] for lev in a]


@dataclass
class Clients:
    """Everything that touches the network, so tests can swap in fakes."""
    fetch_fields: Callable[[datetime], F.FieldSet]          # forecast download at `now`
    fetch_recent_reports: Callable[[datetime], pd.DataFrame]
    fetch_history_reports: Callable[[datetime], pd.DataFrame]
    fetch_gairmets: Callable[[datetime], list] | None = None


def real_clients(log=print) -> Clients:
    def fields_now(now):
        return F.fetch_open_meteo(now, past_hours=6, forecast_hours=12, batch=44, log=log)

    def recent(now):
        frames = []
        try:
            frames.append(P.fetch_awc_reports(now, age_hours=2, log=log))
        except net.FetchError as e:
            log(f"  AWC reports failed: {e}")
        try:
            frames.append(P.fetch_iem_reports(now, hours=6, log=log))
        except net.FetchError as e:
            log(f"  IEM recent reports failed: {e}")
        return pd.concat(frames, ignore_index=True) if frames else P._frame([], P.REPORT_COLUMNS)

    def history(now):
        return P.load_iem_days(31, now, log=log)

    return Clients(fields_now, recent, history, lambda now: GA.fetch_gairmets(log=log, now=now))


class LiveJob:
    def __init__(self, clients: Clients, ledger_dir: Path = LEDGER_DIR,
                 field_store: F.FieldStore | None = None, report_store: Path = P.LIVE_STORE,
                 backtest_path: Path = net.RUNS / "backtest.json", log=print,
                 clock: Callable[[], datetime] = utcnow):
        self.clients = clients
        self.clock = clock
        self.ledger = SealedLedger(ledger_dir)
        self.store = field_store or F.FieldStore()
        self.report_store = Path(report_store)
        self.legacy_store = P.LEGACY_STORE if Path(report_store) == P.LIVE_STORE else None
        self.backtest_path = Path(backtest_path)
        self.state_path = Path(ledger_dir) / "state.json"
        self.log = log

    # ------------------------------------------------------------ fields
    def refresh_fields(self, now: datetime) -> None:
        last = self.store.latest_fetch("live")
        merged = self.store.known_at(now)
        need = int((_hour(now) + timedelta(hours=max(LEADS))).timestamp())
        covered = merged is not None and need in set(merged.times.tolist())
        if last is not None and now - last < FIELD_REFRESH and covered:
            self.log(f"  fields: using download from {to_iso(last)}")
            return
        try:
            fs = self.clients.fetch_fields(now)
            self.store.add(F.drop_nan_hours(fs), "live", now)
            self.log(f"  fields: downloaded {len(fs.times)} hours")
        except Exception as e:  # keep going with older fields
            self.log(f"  fields: download failed ({e}); using what is cached")

    # --------------------------------------------------------------- fit
    def fit_models(self, now: datetime):
        hist = P.dedupe(pd.concat([self.clients.fetch_history_reports(now),
                                   P.read_store(self.report_store, legacy=self.legacy_store)],
                                  ignore_index=True))
        hist = P.upper(P.in_domain(hist))
        hist = hist[hist["time"] < pd.Timestamp(now)]
        cases, truths, _ = build_cases(hist, self.store.chunks())
        cases, truths = labelled(cases, truths)
        cases_truths = [(c, t) for c, t in zip(cases, truths) if c.target_time < now]
        models = all_models()
        if len(cases_truths) < 50:
            raise RuntimeError(f"only {len(cases_truths)} training reports before {to_iso(now)}")
        cs, ts = [c for c, _ in cases_truths], [t for _, t in cases_truths]
        for m in models:
            m.fit(cs, ts)
        self.log(f"  fitted on {len(cs)} reports before {to_iso(now)} "
                 f"({int(sum(t['label'] for t in ts))} moderate or greater)")
        return models, len(cs)

    # -------------------------------------------------------------- seal
    def seal(self, now: datetime, models, n_train: int) -> list[dict]:
        fs = self.store.known_at(now)
        spec = F.grid_spec()
        polys = []
        if self.clients.fetch_gairmets is not None:
            try:
                polys = self.clients.fetch_gairmets(now)
            except Exception as e:
                self.log(f"  G-AIRMET fetch failed: {e}")
                polys = None
        out = []
        for k in LEADS:
            valid = _hour(now) + timedelta(hours=k)
            meta = {"valid_hour_offset": k, "train_reports": n_train}
            rows = np.flatnonzero(fs.times == int(valid.timestamp())) if fs is not None else []
            if len(rows):
                one = fs.select(rows[:1])
                diag = {name: arr[0] for name, arr in diagnose(one).items()}
                meta["fields_available_at"] = to_iso(datetime.fromtimestamp(int(one.avail[0]), tz=valid.tzinfo))
                for m in models:
                    out.append(self._record(m.name, now, valid, {"risk": _round_grid(m.risk_from(diag)),
                                                                  "units": m.units, "grid": spec}, meta))
            else:
                self.log(f"  no fields for {to_iso(valid)}; only Climatology sealed")
                clim = next(m for m in models if m.name == "Climatology")
                shape = (len(spec["levels_hpa"]), len(spec["lats"]), len(spec["lons"]))
                out.append(self._record("Climatology", now, valid,
                                        {"risk": _round_grid(np.full(shape, clim.rate)),
                                         "units": clim.units, "grid": spec}, meta))
            snap = GA.snapshot_for(polys, valid) if polys else None
            if snap is not None:
                grid = GA.risk_grid(snap, spec["lats"], spec["lons"], spec["flight_levels"])
                out.append(self._record("GAirmet", now, valid,
                                        {"risk": _round_grid(grid, 0), "units": "0/1 inside TURB-HI polygon",
                                         "grid": spec, "polygons": snap,
                                         "snapshot_valid_time": snap[0]["valid_time"]}, meta))
        sealed_at = self.clock()
        for r in out:  # the real lead: valid time minus the time of sealing
            r["meta"]["lead_minutes"] = round((r["target_time"] - sealed_at).total_seconds() / 60, 1)
        late = [r for r in out if r["target_time"] <= sealed_at + timedelta(minutes=1)]
        if late:
            self.log(f"  dropped {len(late)} records whose valid hour is about to pass")
        sealed = self.ledger.seal_many([r for r in out if r not in late], now=sealed_at)
        self.log(f"  sealed {len(sealed)} records for valid hours "
                 f"{to_iso(_hour(now) + timedelta(hours=1))} .. {to_iso(_hour(now) + timedelta(hours=6))}")
        return sealed

    @staticmethod
    def _record(model, now, valid, prediction, meta) -> dict:
        return {"task": TASK, "model": model,
                "case_id": f"{TASK}/{model}/{valid:%Y-%m-%dT%HZ}/issued-{now:%Y%m%dT%H%M%SZ}",
                "issued_at": now, "target_time": valid, "prediction": prediction, "meta": dict(meta)}

    # ------------------------------------------------------------- grade
    def update_reports(self, now: datetime) -> pd.DataFrame:
        try:
            new = self.clients.fetch_recent_reports(now)
            new = new[new["time"] <= pd.Timestamp(now)]
        except Exception as e:
            self.log(f"  report fetch failed: {e}")
            new = P._frame([], P.REPORT_COLUMNS)
        P.append_to_store(new, self.report_store)
        df = P.read_store(self.report_store, legacy=self.legacy_store)
        self.log(f"  reports: {len(new)} fetched, {len(df)} labelled layers in the live store")
        return df

    def grade(self, now: datetime, reports: pd.DataFrame) -> int:
        due = [r for r in self.ledger.ungraded_due(now)
               if r["task"] == TASK and from_iso(r["target_time"]) + GRADE_DELAY <= now]
        if not due:
            return 0
        up = P.upper(P.in_domain(reports))
        up = up[up["time"] <= pd.Timestamp(now)]
        items = []
        for r in due:
            v = pd.Timestamp(from_iso(r["target_time"]))
            w = up[(up["time"] - v).abs() <= TRUTH_WINDOW]
            truth = {"window_minutes": int(TRUTH_WINDOW.total_seconds() // 60),
                     "n": int(len(w)),
                     "reports": [{"time": to_iso(t.to_pydatetime()), "lat": round(float(a), 4),
                                  "lon": round(float(b), 4), "fl": float(f), "code": int(c), "layer": int(l)}
                                 for t, a, b, f, c, l in zip(w["time"], w["lat"], w["lon"], w["fl"],
                                                             w["code"], w["layer"])]}
            items.append((r["seq"], truth))
        self.ledger.grade_many(items, now=max(now, self.clock()))
        self.log(f"  graded {len(items)} records")
        return len(items)

    # ------------------------------------------------------------- cycle
    def cycle(self, now: datetime | None = None) -> dict:
        now = (now or self.clock()).replace(microsecond=0)
        self.log(f"cycle at {to_iso(now)}")
        self.refresh_fields(now)
        models, n_train = self.fit_models(now)
        sealed = self.seal(now, models, n_train)
        reports = self.update_reports(now)
        graded = self.grade(now, reports)
        ok, n, msg = self.ledger.verify()
        self.log(f"  verify: {ok} ({n} records, {msg})")
        blend = next(m for m in models if m.name == "SeedBlend")
        state = build_state(self.ledger, reports, self.store.known_at(now), now,
                            backtest_path=self.backtest_path, coefficients=blend.coefficients(),
                            verify=(ok, n, msg))
        self.state_path.write_text(json.dumps(state, separators=(",", ":")))
        self.log(f"  state: {self.state_path} ({self.state_path.stat().st_size / 1e6:.2f} MB)")
        return {"sealed": len(sealed), "graded": graded, "verify": ok}


# ------------------------------------------------------------------ scoring
def score_record(record: dict, lat, lon, fl) -> dict[str, np.ndarray]:
    """Risk a sealed record gave at report positions, its percentile within the
    sealed grid (at the grid level nearest the report), and whether it was in
    the top 20% of that level's grid."""
    pred = record["prediction"]
    grid = np.array([[[np.nan if v is None else v for v in row] for row in lev] for lev in pred["risk"]], float)
    spec = pred["grid"]
    levels = np.array(spec["levels_hpa"], float)
    lat, lon, fl = (np.atleast_1d(np.asarray(a, float)) for a in (lat, lon, fl))
    p = fl_to_pressure(fl)
    if "polygons" in pred:
        risk = GA.risk_at(pred["polygons"], lat, lon, fl)
    else:
        risk = interp_grid3(grid, levels, spec["lats"], spec["lons"], p, lat, lon)
    li = np.abs(np.log(levels)[None, :] - np.log(p)[:, None]).argmin(axis=1)
    pct = np.empty(len(lat))
    for k in np.unique(li):
        s = np.sort(grid[k][np.isfinite(grid[k])])
        j = li == k
        pct[j] = 100.0 * np.searchsorted(s, risk[j] - 1e-12, side="left") / max(len(s), 1)
    return {"risk": risk, "percentile": pct, "top": pct >= 100 * (1 - TOP_SHARE)}


def _outcome(label: float, top: bool) -> str:
    if label != label:
        return "top20" if top else "not_top20"
    if label == 1:
        return "hit" if top else "miss"
    return "false_alarm" if top else "correct_negative"


def lead_minutes(record: dict) -> float:
    """Valid time minus sealing time, in minutes (works for every record)."""
    return (from_iso(record["target_time"]) - from_iso(record["sealed_at"])).total_seconds() / 60.0


def graded_scores(ledger: SealedLedger) -> pd.DataFrame:
    """One row per (graded record, truth report layer)."""
    rows = []
    for r in ledger.joined():
        if r["task"] != TASK or r["grade"] is None or not r["grade"]["truth"]["reports"]:
            continue
        reps = r["grade"]["truth"]["reports"]
        s = score_record(r, [x["lat"] for x in reps], [x["lon"] for x in reps], [x["fl"] for x in reps])
        lm = lead_minutes(r)
        for j, x in enumerate(reps):
            rows.append({"model": r["model"], "seq": r["seq"], "lead_minutes": round(lm, 1),
                         "valid_time": r["target_time"], "time": x["time"], "lat": x["lat"], "lon": x["lon"],
                         "fl": x["fl"], "code": x["code"], "label": P.label(x["code"]),
                         "risk": float(s["risk"][j]), "percentile": float(s["percentile"][j]),
                         "top": bool(s["top"][j])})
    return pd.DataFrame(rows)


_REPORT = ["valid_time", "time", "lat", "lon", "fl"]


def at_least(scores: pd.DataFrame, hours: float) -> pd.DataFrame:
    """For each model and graded report, the latest record sealed at least
    `hours` before the valid time."""
    h = scores[scores["lead_minutes"] >= 60 * hours]
    return h.sort_values("seq").drop_duplicates(["model", *_REPORT], keep="last")


def scoreboard(scores: pd.DataFrame) -> dict:
    """Per model, per "at least k h" lead: graded report layers, AUC, hit and
    false-alarm rates (top 20% of the grid)."""
    board: dict[str, dict] = {}
    if scores.empty:
        return board
    lab = scores[scores["label"].notna()]
    for k in LEAD_STEPS:
        for model, h in at_least(lab, k).groupby("model"):
            y = h["label"].to_numpy(int)
            a = auc(h["risk"].to_numpy(float), y)
            board.setdefault(model, {})[f"at_least_{k}h"] = {
                "n": int(len(h)), "positives": int(y.sum()),
                "auc": None if a != a else round(a, 4),
                "hit_rate": None if y.sum() == 0 else round(float(h.loc[h["label"] == 1, "top"].mean()), 4),
                "false_alarm_rate": None if (y == 0).sum() == 0 else
                round(float(h.loc[h["label"] == 0, "top"].mean()), 4)}
    return board


# -------------------------------------------------------------------- state
NOTES = [
    "Pilot reports are biased toward where aircraft fly and toward reporting bumps rather than smooth air.",
    "Positive = moderate or greater (code >= 4); negative = light or less (code <= 2); light-moderate (3) is not scored.",
    "Lead = valid time minus the time the forecast was sealed. Scoreboard row 'at least k h' uses, for each "
    "report, the latest record sealed at least k hours before the valid time.",
    "The backtest uses hindsight fields (analysis-time comparison of indices), not forecasts; only these "
    "live sealed forecasts test forecast skill.",
    "A hit means the sealed risk at the report was in the top 20% of that forecast's grid at the report's level.",
    "Records are graded 75 minutes after their valid hour, with report layers within 30 minutes of it.",
]


def build_state(ledger: SealedLedger, reports: pd.DataFrame, fields_now, now: datetime, *,
                backtest_path: Path, coefficients: dict, verify) -> dict:
    sealed = [r for r in ledger.sealed() if r["task"] == TASK]
    graded = ledger.grades()
    n, head = ledger.head()
    last_issue = max((r["issued_at"] for r in sealed), default=None)
    latest = [r for r in sealed if r["issued_at"] == last_issue]
    spec = F.grid_spec()

    forecasts = {m: [] for m in STATE_MODELS}
    valid_times = []
    for r in sorted(latest, key=lambda r: r["target_time"]):
        if r["model"] in forecasts:
            forecasts[r["model"]].append({
                "valid_time": r["target_time"], "sealed_at": r["sealed_at"],
                "valid_hour_offset": r["meta"].get("valid_hour_offset", r["meta"].get("lead_hours")),
                "lead_minutes": round(lead_minutes(r), 1), "seq": r["seq"], "risk": r["prediction"]["risk"]})
        if r["target_time"] not in valid_times:
            valid_times.append(r["target_time"])

    jet = []
    if fields_now is not None:
        li = int(np.flatnonzero(fields_now.levels == 250)[0])
        for v in valid_times:
            rows = np.flatnonzero(fields_now.times == int(from_iso(v).timestamp()))
            if len(rows):
                jet.append({"valid_time": v,
                            "speed_ms": np.round(fields_now.speed[rows[0], li], 1).tolist(),
                            "direction_deg": np.round(fields_now.direction[rows[0], li], 0).tolist()})

    scores = graded_scores(ledger)
    recent = P.upper(P.in_domain(reports))
    recent = recent[(recent["time"] >= pd.Timestamp(now - timedelta(hours=12))) &
                    (recent["time"] <= pd.Timestamp(now))].sort_values("time")
    by_report: dict[tuple, dict] = {}
    if not scores.empty:
        s = scores.sort_values("seq")  # the latest-sealed record (shortest lead) is written last and wins
        for row in s.itertuples():
            by_report.setdefault((row.time, round(row.lat, 4), round(row.lon, 4), row.fl), {})[row.model] = {
                "risk": round(row.risk, 4), "percentile": round(row.percentile, 1),
                "lead_minutes": row.lead_minutes, "valid_time": row.valid_time, "seq": row.seq,
                "in_top20": row.top, "outcome": _outcome(row.label, row.top)}
    rep_out = []
    for t, a, b, f, c, raw in zip(recent["time"], recent["lat"], recent["lon"], recent["fl"],
                                  recent["code"], recent["raw"]):
        key = (to_iso(t.to_pydatetime()), round(float(a), 4), round(float(b), 4), float(f))
        lab = P.label(c)
        rep_out.append({"time": key[0], "lat": key[1], "lon": key[2], "fl": key[3], "code": int(c),
                        "label": None if lab != lab else int(lab), "raw": str(raw)[:200],
                        "graded": by_report.get(key)})

    backtest = None
    if backtest_path.exists():
        bt = json.loads(backtest_path.read_text())
        backtest = {k: bt.get(k) for k in ("generated_at", "days", "labelled_cases", "positives", "base_rate",
                                           "train", "test", "interval_method", "table", "brier",
                                           "paired_SeedBlend_vs_Ellrod1", "verdict",
                                           "seedblend_coefficients", "caveats")}
    notes = list(NOTES)
    if backtest and backtest.get("verdict"):
        notes.append("Backtest (hindsight fields): " + backtest["verdict"])
    return {
        "generated_at": to_iso(now),
        "task": TASK,
        "ledger": {"records": n, "graded": len(graded), "head": head,
                   "verify": {"ok": verify[0], "checked": verify[1], "message": verify[2]}},
        "grid": {**spec, "levels": [{"hpa": p, "flight_level": f} for p, f in
                                    zip(spec["levels_hpa"], spec["flight_levels"])]},
        "issued_at": last_issue,
        "forecasts": forecasts,
        "risk_units": {m.name: m.units for m in all_models() if m.name in STATE_MODELS},
        "jet_250hpa": jet,
        "reports_12h": rep_out,
        "scoreboard": scoreboard(scores),
        "seedblend_coefficients": coefficients,
        "backtest": backtest,
        "notes": notes,
    }


# ---------------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description="Live sealed turbulence forecasts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("once")
    run = sub.add_parser("run")
    run.add_argument("--interval", type=int, default=1800)
    a = ap.parse_args(argv)

    def log(msg):
        print(f"{to_iso(utcnow())} {msg}", flush=True)

    F.ensure_backfill(F.FieldStore(), utcnow().replace(microsecond=0), log=log)
    job = LiveJob(real_clients(log), log=log)
    if a.cmd == "once":
        job.cycle()
        return
    while True:
        try:
            job.cycle()
        except Exception:
            log("cycle failed:\n" + traceback.format_exc())
        wake = time.time() + seconds_to_next_slot(time.time(), a.interval)
        while time.time() < wake:  # short naps on the wall clock: a long sleep can overrun on macOS
            time.sleep(min(15.0, max(0.0, wake - time.time())))


def seconds_to_next_slot(now_epoch: float, interval: int, offset: int = 300) -> float:
    """Cycles start `offset` seconds past each multiple of `interval` (for 1800 s:
    at :05 and :35), so a cycle never runs into the top of the hour it forecasts."""
    k = (now_epoch - offset) // interval + 1
    return max(30.0, k * interval + offset - now_epoch)


if __name__ == "__main__":
    main()
