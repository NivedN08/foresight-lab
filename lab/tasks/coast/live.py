"""Live sealing job for the coast forecasts.

    .venv/bin/python -m lab.tasks.coast.live once
    .venv/bin/python -m lab.tasks.coast.live run --interval 900

Each cycle: refresh data; fit models on history strictly before now; build one
live case per station and lead (issue time = now); seal every model's
prediction; grade every due record whose truth has arrived; verify the chain;
write runs/coast/live/state.json for the dashboard.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from lab.core.ledger import SealedLedger
from lab.core.timeutil import from_iso, to_iso, utcnow

from . import sources as S
from .data import load_stations
from .http import Client
from .models import all_models
from .water import LEADS, TASK, StationFeatures, build_case, build_history_cases, current_residual

ROOT = Path(__file__).resolve().parents[3]
LIVE_DIR = ROOT / "runs" / "coast" / "live"
FIT_DAYS = 45
STALE_HOURS = 6
SERIES_STEP_MIN = 12
MAX_STATE_BYTES = 1_900_000
# The hourly refit learns from history built with previous-run weather (a run
# published before each issue time), so its coefficients match forecast-quality
# inputs; live cases use the newest forecast. See water.py.
FIT_WEATHER = "runs"
LIVE_WEATHER = "latest"


def floor6(t: datetime) -> datetime:
    t = t.replace(second=0, microsecond=0)
    return t - timedelta(minutes=t.minute % 6)


class WaterCycle:
    """Everything one cycle needs for the water-level task. Models are refitted
    when the hour changes (fitting uses only cases whose target is before now)."""

    def __init__(self, client: Client, fit_days: int = FIT_DAYS, stations=None, log=print):
        self.client = client
        self.fit_days = fit_days
        self.stations_ids = stations
        self.log = log
        self.models = None
        self.fitted_hour = None
        self.fit_info: dict = {}

    def load(self, now: datetime):
        # weather_runs: leak-free previous-run weather for the fit's history;
        # the live cases themselves use the newest forecast ("latest").
        self.stations = load_stations(self.client, now, self.fit_days + 2, stations=self.stations_ids,
                                      log=self.log, weather_runs=True)
        start = pd.Timestamp(now) - pd.Timedelta(days=self.fit_days + 4)
        end = pd.Timestamp(now) + pd.Timedelta(days=2)
        self.feats = {sid: StationFeatures(sd, start, end) for sid, sd in self.stations.items()}

    def fit(self, now: datetime):
        hour = now.replace(minute=0, second=0, microsecond=0)
        if self.models is not None and self.fitted_hour == hour:
            return
        cases, truths = build_history_cases(self.feats, now - timedelta(days=self.fit_days), now,
                                            weather=FIT_WEATHER)
        cases_truths = [(c, t) for c, t in zip(cases, truths) if c.target_time < now]
        models = all_models()
        for m in models:
            m.fit([c for c, _ in cases_truths], [t for _, t in cases_truths])
        self.models = models
        self.fitted_hour = hour
        self.fit_info = {"fitted_at": to_iso(now), "train_cases": len(cases_truths),
                         "train_from": to_iso(now - timedelta(days=self.fit_days))}
        self.log(f"  fitted {len(models)} models on {len(cases_truths)} cases")

    def live_forecasts(self, now: datetime, existing: set) -> list[dict]:
        issue = floor6(now)
        out = []
        for sid, f in self.feats.items():
            for lead in LEADS:
                case = build_case(f, issue, lead, weather=LIVE_WEATHER)
                if case is None:
                    continue
                target = case.target_time
                tide = f.tide_at(target)
                for m in self.models:
                    key = (sid, m.name, lead, target.strftime("%Y%m%d%H"))
                    if key in existing:
                        continue
                    r = float(m.predict(case)["residual_m"])
                    out.append({
                        "task": TASK, "model": m.name, "case_id": case.case_id,
                        "issued_at": issue, "target_time": target,
                        "prediction": {"residual_m": round(r, 4), "tide_m": round(tide, 4),
                                       "water_level_m": round(tide + r, 4)},
                        "meta": {"station": sid, "station_name": f.sd.name, "lead_h": lead,
                                 "r_now_m": round(case.inputs["r_now"], 4),
                                 "data_age_min": case.inputs["r_age_min"],
                                 "has_weather": case.inputs["has_weather"]},
                    })
                    existing.add(key)
        return out

    def truth(self, rec: dict, now: datetime):
        """(truth dict or None, stale flag)."""
        sid = rec["meta"].get("station")
        f = self.feats.get(sid)
        target = from_iso(rec["target_time"])
        stale = now - target > timedelta(hours=STALE_HOURS)
        if f is None:
            return None, stale
        i = f.index(target)
        if i < 1 or i + 1 >= f.n:
            return None, stale
        vals = f.r[i - 1:i + 2]
        have = int((~np.isnan(vals)).sum())
        # wait for the +6 minute value unless the gauge is clearly late
        if have == 0 or (np.isnan(vals[2]) and now - target < timedelta(minutes=60)):
            return None, stale
        tr = float(np.nanmean(vals))
        p = rec["prediction"]
        return {"residual_m": round(tr, 4), "water_level_m": round(tr + p["tide_m"], 4),
                "abs_error_cm": round(abs(p["residual_m"] - tr) * 100, 3), "n_obs": have}, False


def _existing_keys(records: list[dict], now: datetime, task: str) -> set:
    keys = set()
    for r in records:
        if r["task"] != task:
            continue
        t = from_iso(r["target_time"])
        if t < now - timedelta(hours=1):
            continue
        keys.add((r["meta"].get("station") or r["meta"].get("buoy"), r["model"], r["meta"]["lead_h"],
                  t.strftime("%Y%m%d%H")))
    return keys


def grade_due(ledger: SealedLedger, now: datetime, truth_fn, task: str) -> tuple[int, list[dict]]:
    items, stale = [], []
    for rec in ledger.ungraded_due(now):
        if rec["task"] != task:
            continue
        tr, is_stale = truth_fn(rec, now)
        if tr is not None:
            items.append((rec["seq"], tr))
        elif is_stale:
            stale.append({"seq": rec["seq"], "station": rec["meta"].get("station") or rec["meta"].get("buoy"),
                          "model": rec["model"], "target_time": rec["target_time"]})
    if items:
        ledger.grade_many(items, now=now)
    return len(items), stale


# ---------------------------------------------------------------- state
def _series(s: pd.Series, start: datetime, end: datetime, step_min: int = SERIES_STEP_MIN) -> dict:
    grid = pd.date_range(pd.Timestamp(start).floor(f"{step_min}min"), pd.Timestamp(end), freq=f"{step_min}min")
    v = s.reindex(grid)
    vals = [None if np.isnan(x) else round(float(x), 3) for x in v.to_numpy(float)]
    return {"start": to_iso(grid[0].to_pydatetime()) if len(grid) else None, "step_min": step_min, "values": vals}


def _scoreboard(joined: list[dict], task: str, err_key: str) -> dict:
    acc = defaultdict(lambda: defaultdict(list))
    for r in joined:
        if r["task"] != task or not r["grade"]:
            continue
        acc[r["model"]][str(r["meta"]["lead_h"])].append(r["grade"]["truth"][err_key])
    return {m: {lead: {"graded": len(v), "mae_cm": round(float(np.mean(v)), 3)} for lead, v in sorted(leads.items(), key=lambda x: int(x[0]))}
            for m, leads in acc.items()}


def build_state(now: datetime, ledger: SealedLedger, wc: WaterCycle, stale: list[dict],
                alerts: list[dict], cycle_info: dict, waves: dict | None = None, past_hours: int = 24) -> dict:
    ok, n_checked, msg = ledger.verify()
    n, head = ledger.head()
    joined = ledger.joined()
    graded = sum(1 for r in joined if r["grade"])
    lo, hi = now - timedelta(hours=past_hours), now + timedelta(hours=25)
    per = defaultdict(lambda: defaultdict(lambda: {k: [] for k in
                                                   ("lead_h", "target", "issued", "water_level_m",
                                                    "residual_m", "truth_water_level_m", "error_cm")}))
    for r in joined:
        if r["task"] != TASK:
            continue
        t = from_iso(r["target_time"])
        if not (lo <= t <= hi):
            continue
        d = per[r["meta"]["station"]][r["model"]]
        g = r["grade"]
        d["lead_h"].append(r["meta"]["lead_h"])
        d["target"].append(r["target_time"])
        d["issued"].append(r["issued_at"])
        d["water_level_m"].append(r["prediction"]["water_level_m"])
        d["residual_m"].append(r["prediction"]["residual_m"])
        d["truth_water_level_m"].append(g["truth"]["water_level_m"] if g else None)
        d["error_cm"].append(g["truth"]["abs_error_cm"] if g else None)
    stations = []
    for sid, f in wc.feats.items():
        sd = f.sd
        obs = sd.obs[sd.obs.index <= pd.Timestamp(now)]
        latest_t = obs.index[-1].to_pydatetime() if len(obs) else None
        cur = None
        i = f.index(floor6(now))
        c = current_residual(f, i)
        if c is not None:
            cur = round(c[0], 4)
        stations.append({
            "id": sid, "name": sd.name, "lat": sd.lat, "lon": sd.lon,
            "latest_obs_time": to_iso(latest_t) if latest_t else None,
            "latest_obs_m": round(float(obs.iloc[-1]), 3) if len(obs) else None,
            "current_residual_m": cur,
            "tide": _series(sd.tide, now - timedelta(hours=24), now + timedelta(hours=24)),
            "observed": _series(obs, now - timedelta(hours=48), now),
            "forecasts": {m: dict(v) for m, v in per.get(sid, {}).items()},
        })
    backtest = None
    bt = ROOT / "runs" / "coast" / "backtest.json"
    if bt.exists():
        b = json.loads(bt.read_text())
        backtest = {"generated_at": b.get("generated_at"), "days": b.get("days"), "unit": b.get("unit"),
                    "table": b.get("table"), "paired_seed_vs": b.get("paired_seed_vs"), "note": b.get("note")}
    backtest_waves = None
    btw = ROOT / "runs" / "coast" / "backtest_waves.json"
    if btw.exists():
        b = json.loads(btw.read_text())
        backtest_waves = {"generated_at": b.get("generated_at"), "days": b.get("days"), "unit": b.get("unit"),
                          "table": b.get("table"), "paired_seed_vs": b.get("paired_seed_vs"),
                          "note": b.get("note")}
    state = {
        "generated_at": to_iso(now),
        "ledger": {"path": str(ledger.dir.relative_to(ROOT)) if ledger.dir.is_relative_to(ROOT) else str(ledger.dir),
                   "records": n, "graded": graded, "head_hash": head,
                   "verify": {"ok": ok, "checked": n_checked, "message": msg}},
        "cycle": cycle_info,
        "water": {
            "quantity": "water level above the tide table (residual), metres; errors in cm",
            "leads_h": list(LEADS),
            "models": [m.name for m in (wc.models or [])],
            "fit": wc.fit_info,
            "stations": stations,
            "scoreboard": _scoreboard(joined, TASK, "abs_error_cm"),
            "forecast_window": {"from": to_iso(lo), "to": to_iso(hi)},
            "stale": stale,
            "backtest": backtest,
        },
        "waves": waves,
        "backtest_waves": backtest_waves,
        "alerts": alerts,
    }
    return state


# ---------------------------------------------------------------- cycle
def cycle(clock=utcnow, client: Client | None = None, ledger_dir: Path = LIVE_DIR,
          wc: WaterCycle | None = None, wave_cycle=None, fetch_alerts: bool = True, log=print) -> dict:
    """One cycle. `clock` gives the current UTC time; it is read again at the
    moment of sealing so the recorded sealing time is never earlier than the truth."""
    now = clock()
    client = client or Client()
    ledger = SealedLedger(ledger_dir)
    wc = wc or WaterCycle(client, log=log)
    t0 = time.time()
    wc.load(now)
    wc.fit(now)
    existing = _existing_keys(ledger.sealed(), now, TASK)
    forecasts = wc.live_forecasts(now, existing)
    if forecasts:
        ledger.seal_many(forecasts, now=clock())
    n_graded, stale = grade_due(ledger, clock(), wc.truth, TASK)
    waves_state = None
    n_wave_sealed = n_wave_graded = 0
    if wave_cycle is not None:
        try:
            n_wave_sealed, n_wave_graded, waves_state = wave_cycle.run(now, ledger, clock)
        except Exception as e:
            log(f"  waves failed: {e}")
            traceback.print_exc()
    alerts = S.fetch_alerts(client, "CA,OR,WA") if fetch_alerts else []
    info = {"now": to_iso(now), "sealed_this_cycle": len(forecasts), "graded_this_cycle": n_graded,
            "wave_sealed_this_cycle": n_wave_sealed, "wave_graded_this_cycle": n_wave_graded,
            "stations_sealed_this_cycle": len({f["meta"]["station"] for f in forecasts}),
            "seconds": round(time.time() - t0, 1), "fetch_log": client.log[-10:]}
    # keep the file under about 2 MB: shorten the window of past forecasts if needed
    t_state = clock()
    for past_hours in (24, 12, 6, 2):
        if wave_cycle is not None and waves_state is not None and past_hours != 24:
            waves_state = wave_cycle.state(t_state, ledger, wave_cycle.last_stale, past_hours=past_hours)
        state = build_state(t_state, ledger, wc, stale, alerts, info, waves=waves_state, past_hours=past_hours)
        text = json.dumps(state, separators=(",", ":"), allow_nan=False, default=_json_default)
        if len(text) < MAX_STATE_BYTES:
            break
    ledger_dir.mkdir(parents=True, exist_ok=True)
    tmp = ledger_dir / "state.json.tmp"
    tmp.write_text(text)
    tmp.replace(ledger_dir / "state.json")
    log(f"[{to_iso(now)}] sealed {len(forecasts)} water + {n_wave_sealed} wave, graded {n_graded} water + "
        f"{n_wave_graded} wave, ledger {state['ledger']['records']} records, verify {state['ledger']['verify']['ok']}, "
        f"{info['seconds']} s")
    client.log.clear()
    return state


def _json_default(o):
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def _wave_cycle(client: Client, log):
    try:
        from .waves import WaveCycle
    except ImportError:
        return None
    return WaveCycle(client, log=log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["once", "run"])
    ap.add_argument("--interval", type=int, default=900)
    ap.add_argument("--no-waves", action="store_true")
    args = ap.parse_args()
    client = Client()
    wc = WaterCycle(client)
    wave = None if args.no_waves else _wave_cycle(client, print)
    while True:
        start = time.time()
        try:
            cycle(client=client, wc=wc, wave_cycle=wave)
        except Exception:
            traceback.print_exc()
        if args.mode == "once":
            break
        # Wait on the wall clock in short naps. A single long sleep does not
        # advance while the computer is asleep, so cycles would be missed for
        # as long again after it wakes.
        next_due = start + args.interval
        while time.time() < max(next_due, start + 30.0):
            time.sleep(15.0)


if __name__ == "__main__":
    main()
