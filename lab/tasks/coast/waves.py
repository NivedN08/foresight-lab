"""Significant wave height at coastal buoys.

Quantity: WVHT (metres) from NDBC realtime2 files. Truth at a target time is the
mean of the buoy's WVHT reports within +-30 minutes.

Forecasters:
  * Persistence  - the latest observed wave height stays.
  * WaveModel    - the Open-Meteo marine wave model at the target time.
  * SeedSwell    - least-squares blend (per lead) of the wave model, damped
                   persistence, and an upstream term: the wave height an
                   offshore buoy saw earlier, delayed by the deep-water swell
                   travel time (group speed g T / (4 pi), T = dominant period).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Sequence

import numpy as np
import pandas as pd

from lab.core.task import Case
from lab.core.timeutil import from_iso, to_iso

from . import sources as S
from .http import Client, FetchError

TASK = "coast_waves"
LEADS = (1, 3, 6, 12, 24)
COASTAL = ["46026", "46012", "46013", "46014", "46042", "46214", "46237", "46011", "46028",
           "46022", "46027", "46050", "46089"]
OFFSHORE = ["46059", "46006", "46002"]
STEP_MIN = 10
PER_HOUR = 6
G = 9.81
STALE_HOURS = 6
BUOY_META_URL = "https://www.ndbc.noaa.gov/activestations.xml"


# ------------------------------------------------------------------ helpers
def parse_buoy_meta(xml: str) -> dict[str, dict]:
    out = {}
    for m in re.finditer(r'<station id="([^"]+)" lat="([-\d.]+)" lon="([-\d.]+)"[^>]*?name="([^"]*)"', xml):
        out[m.group(1).upper()] = {"id": m.group(1).upper(), "lat": float(m.group(2)),
                                   "lon": float(m.group(3)), "name": m.group(4)}
    return out


def great_circle_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(math.sqrt(a))


def group_speed(period_s):
    """Deep-water group speed (m/s) for wave period T: g T / (4 pi)."""
    return G * np.asarray(period_s, dtype=float) / (4 * math.pi)


def travel_hours(distance_m: float, period_s) -> np.ndarray:
    return distance_m / group_speed(period_s) / 3600.0


def _epoch(t) -> float:
    return pd.Timestamp(t).timestamp()


# ------------------------------------------------------------------ features
class Upstream:
    """Arrival times at one coastal buoy of what an offshore buoy observed."""

    def __init__(self, offshore_id: str, obs: pd.DataFrame, distance_m: float):
        self.id = offshore_id
        self.distance_m = distance_m
        d = obs[["WVHT", "DPD"]].dropna()
        d = d[(d["DPD"] > 3) & (d["WVHT"] > 0)]
        self.t_obs = np.array([_epoch(t) for t in d.index])
        self.h = d["WVHT"].to_numpy(float)
        self.period = d["DPD"].to_numpy(float)
        self.t_arrive = self.t_obs + travel_hours(distance_m, self.period) * 3600.0

    def at(self, issue_epoch: float, target_epoch: float, tol_h: float = 2.0) -> tuple[float, float]:
        """(height, travel hours) of the observation made at or before issue whose
        swell arrives closest to the target; NaN if none arrives within tol_h."""
        ok = self.t_obs <= issue_epoch
        if not ok.any():
            return float("nan"), float("nan")
        gap = np.abs(self.t_arrive[ok] - target_epoch)
        k = int(np.argmin(gap))
        if gap[k] > tol_h * 3600:
            return float("nan"), float("nan")
        return float(self.h[ok][k]), float((self.t_arrive[ok][k] - self.t_obs[ok][k]) / 3600.0)


class BuoyFeatures:
    def __init__(self, bid: str, meta: dict, obs: pd.DataFrame, model: pd.DataFrame | None,
                 upstream: Upstream | None, start: pd.Timestamp, end: pd.Timestamp):
        self.id, self.meta, self.obs_df = bid, meta, obs
        self.start = start.floor(f"{STEP_MIN}min")
        self.grid = pd.date_range(self.start, end.ceil(f"{STEP_MIN}min"), freq=f"{STEP_MIN}min")
        w = obs["WVHT"].dropna() if "WVHT" in obs else pd.Series(dtype=float)
        w = w.groupby(w.index.round(f"{STEP_MIN}min")).mean()
        self.h = w.reindex(self.grid).to_numpy(float)
        self.h_mean = pd.Series(self.h).rolling(72 * PER_HOUR, min_periods=12 * PER_HOUR).mean().to_numpy()
        self.n = len(self.grid)
        self.t0 = _epoch(self.start)
        self.model = None
        if model is not None and "wave_height" in model and model["wave_height"].notna().sum() > 24:
            m = model["wave_height"].dropna()
            self.model_t = np.array([_epoch(t) for t in m.index])
            self.model_h = m.to_numpy(float)
            self.model = m
        self.upstream = upstream

    def index(self, t) -> int:
        return int(round((_epoch(t) - self.t0) / (STEP_MIN * 60)))

    def model_at(self, t) -> float:
        if self.model is None:
            return float("nan")
        return float(np.interp(_epoch(t), self.model_t, self.model_h, left=np.nan, right=np.nan))

    def truth_at(self, t) -> float | None:
        i = self.index(t)
        if i < 3 or i + 3 >= self.n:
            return None
        v = self.h[i - 3:i + 4]
        v = v[~np.isnan(v)]
        return float(v.mean()) if v.size else None


def current_height(f: BuoyFeatures, i: int, max_age_steps: int = 3 * PER_HOUR):
    lo = max(0, i - max_age_steps)
    seg = f.h[lo:i + 1]
    ok = np.where(~np.isnan(seg))[0]
    if ok.size == 0:
        return None
    newest = ok[-1]
    vals = seg[ok[ok >= newest - PER_HOUR]]
    return float(vals.mean()), int((len(seg) - 1 - newest) * STEP_MIN)


def build_wave_case(f: BuoyFeatures, issue: datetime, lead_h: int) -> Case | None:
    i = f.index(issue)
    if i < 0 or i >= f.n:
        return None
    cur = current_height(f, i)
    if cur is None or np.isnan(f.h_mean[i]):
        return None
    h0, age = cur
    target = issue + timedelta(hours=lead_h)
    up, up_hours = (float("nan"), float("nan"))
    if f.upstream is not None:
        up, up_hours = f.upstream.at(_epoch(issue), _epoch(target))
    m_t, m_i = f.model_at(target), f.model_at(issue)
    inputs = {"buoy": f.id, "lead_h": lead_h, "h_now": h0, "h_age_min": age,
              "h_mean": float(f.h_mean[i]), "model_target": m_t, "model_issue": m_i,
              "has_model": not (np.isnan(m_t) or np.isnan(m_i)),
              "upstream_h": up, "upstream_travel_h": up_hours,
              "upstream_buoy": f.upstream.id if f.upstream else None,
              "has_upstream": not np.isnan(up)}
    return Case(f"{f.id}|{issue:%Y%m%dT%H%MZ}|{lead_h}h", issue, target, inputs)


def build_wave_history(feats: dict[str, BuoyFeatures], start: datetime, end: datetime, leads=LEADS):
    cases, truths = [], []
    for f in feats.values():
        t = pd.Timestamp(start).ceil("h").to_pydatetime()
        while t <= end:
            for h in leads:
                target = t + timedelta(hours=h)
                if target > end:
                    continue
                tr = f.truth_at(target)
                if tr is None:
                    continue
                c = build_wave_case(f, t, h)
                if c is None:
                    continue
                cases.append(c)
                truths.append({"wave_height_m": tr})
            t += timedelta(hours=1)
    return cases, truths


# ------------------------------------------------------------------- models
class Persistence:
    name = "Persistence"

    def fit(self, cases, truths):
        pass

    def predict(self, case: Case) -> dict:
        return {"wave_height_m": float(case.inputs["h_now"])}


class WaveModel:
    """The operational wave model (Open-Meteo marine) at the target time. Falls
    back to persistence when the model has no value for this buoy."""
    name = "WaveModel"

    def fit(self, cases, truths):
        pass

    def predict(self, case: Case) -> dict:
        i = case.inputs
        return {"wave_height_m": float(i["model_target"] if i["has_model"] else i["h_now"])}


class SeedSwell:
    """Per lead, least squares on
        height(target) = c1 model(target) + c2 model(issue) + c3 now + c4 72-h mean
                         + c5 upstream (offshore buoy, delayed by swell travel time) + c0
    A reduced version is fitted for each missing input (no model, no upstream)."""
    name = "SeedSwell"
    COLS = {(True, True): ["model_target", "model_issue", "h_now", "h_mean", "upstream_h"],
            (True, False): ["model_target", "model_issue", "h_now", "h_mean"],
            (False, True): ["h_now", "h_mean", "upstream_h"],
            (False, False): ["h_now", "h_mean"]}

    def __init__(self, ridge: float = 1e-4):
        self.ridge = ridge
        self.coef: dict[tuple, dict] = {}

    def fit(self, cases: Sequence[Case], truths: Sequence[dict]):
        groups = defaultdict(list)
        for c, t in zip(cases, truths):
            groups[int(c.inputs["lead_h"])].append((c, float(t["wave_height_m"])))
        for lead, rows in groups.items():
            for (need_m, need_u), cols in self.COLS.items():
                use = [(c, t) for c, t in rows
                       if (not need_m or c.inputs["has_model"]) and (not need_u or c.inputs["has_upstream"])]
                if len(use) < 30:
                    continue
                X = np.array([[c.inputs[k] for k in cols] + [1.0] for c, _ in use])
                y = np.array([t for _, t in use])
                A = X.T @ X + self.ridge * len(y) * np.eye(X.shape[1])
                b = np.linalg.solve(A, X.T @ y)
                self.coef[(lead, need_m, need_u)] = dict(zip(cols + ["intercept"], map(float, b)))

    def predict(self, case: Case) -> dict:
        i = case.inputs
        lead = int(i["lead_h"])
        for key in [(lead, i["has_model"], i["has_upstream"]), (lead, i["has_model"], False),
                    (lead, False, False)]:
            k = self.coef.get(key)
            if k is not None:
                y = k["intercept"] + sum(v * i[c] for c, v in k.items() if c != "intercept")
                return {"wave_height_m": float(max(0.0, y))}
        return {"wave_height_m": float(i["h_now"])}

    def describe(self) -> list[str]:
        out = []
        for (lead, m, u), k in sorted(self.coef.items()):
            if m and u:
                out.append(f"lead {lead:>2}h: " + "  ".join(f"{c}={v:+.3f}" for c, v in k.items()))
        return out


def all_wave_models():
    return [Persistence(), WaveModel(), SeedSwell()]


# --------------------------------------------------------------------- data
def load_buoys(client: Client, now: datetime, days: int, log=print):
    meta = parse_buoy_meta(client.get_text(BUOY_META_URL, ttl=30 * 86400.0))
    raw = {}
    for b in COASTAL + OFFSHORE:
        try:
            df = S.fetch_ndbc(client, b, "txt", ttl=600.0)
        except FetchError as e:
            log(f"  buoy {b} unavailable: {e}")
            continue
        if df.empty or "WVHT" not in df or df["WVHT"].notna().sum() < 48:
            log(f"  dropping buoy {b}: too few wave reports")
            continue
        raw[b] = df[df.index <= pd.Timestamp(now)]
    coastal = [b for b in COASTAL if b in raw and b in meta]
    pts = [(meta[b]["lat"], meta[b]["lon"]) for b in coastal]
    model = S.fetch_marine(client, pts, past_days=min(92, days + 3)) if pts else None
    if model is None:
        log("  wave model unavailable: SeedSwell runs on buoy data alone")
    start = pd.Timestamp(now) - pd.Timedelta(days=days + 4)
    end = pd.Timestamp(now) + pd.Timedelta(days=2)
    feats = {}
    for k, b in enumerate(coastal):
        m = meta[b]
        up = None
        offs = [o for o in OFFSHORE if o in raw and o in meta]
        if offs:
            o = min(offs, key=lambda o: great_circle_m(m["lat"], m["lon"], meta[o]["lat"], meta[o]["lon"]))
            dist = great_circle_m(m["lat"], m["lon"], meta[o]["lat"], meta[o]["lon"])
            up = Upstream(o, raw[o], dist)
        feats[b] = BuoyFeatures(b, m, raw[b], model[k] if model else None, up, start, end)
    offshore = {o: raw[o] for o in OFFSHORE if o in raw}
    return feats, offshore, meta


# --------------------------------------------------------------- live cycle
def _series(s: pd.Series, start, end, step_min=60) -> dict:
    s = s.dropna()
    s = s.groupby(s.index.floor(f"{step_min}min")).mean()
    grid = pd.date_range(pd.Timestamp(start).floor(f"{step_min}min"), pd.Timestamp(end), freq=f"{step_min}min")
    v = s.reindex(grid).to_numpy(float)
    return {"start": to_iso(grid[0].to_pydatetime()) if len(grid) else None, "step_min": step_min,
            "values": [None if np.isnan(x) else round(float(x), 2) for x in v]}


class WaveCycle:
    def __init__(self, client: Client, fit_days: int = 40, log=print):
        self.client, self.fit_days, self.log = client, fit_days, log
        self.models = None
        self.fitted_hour = None
        self.fit_info = {}
        self.last_stale = []

    def run(self, now: datetime, ledger, clock):
        from .live import _existing_keys, _scoreboard, floor6, grade_due
        self.feats, self.offshore, meta = load_buoys(self.client, now, self.fit_days, log=self.log)
        hour = now.replace(minute=0, second=0, microsecond=0)
        if self.models is None or self.fitted_hour != hour:
            cases, truths = build_wave_history(self.feats, now - timedelta(days=self.fit_days), now)
            pairs = [(c, t) for c, t in zip(cases, truths) if c.target_time < now]
            models = all_wave_models()
            for m in models:
                m.fit([c for c, _ in pairs], [t for _, t in pairs])
            self.models, self.fitted_hour = models, hour
            self.fit_info = {"fitted_at": to_iso(now), "train_cases": len(pairs)}
            self.log(f"  fitted wave models on {len(pairs)} cases")
        issue = floor6(now)
        existing = _existing_keys(ledger.sealed(), now, TASK)
        out = []
        for b, f in self.feats.items():
            for lead in LEADS:
                c = build_wave_case(f, issue, lead)
                if c is None:
                    continue
                for m in self.models:
                    key = (b, m.name, lead, c.target_time.strftime("%Y%m%d%H"))
                    if key in existing:
                        continue
                    p = m.predict(c)["wave_height_m"]
                    out.append({"task": TASK, "model": m.name, "case_id": c.case_id, "issued_at": issue,
                                "target_time": c.target_time, "prediction": {"wave_height_m": round(p, 3)},
                                "meta": {"buoy": b, "buoy_name": f.meta.get("name"), "lead_h": lead,
                                         "h_now_m": round(c.inputs["h_now"], 3), "data_age_min": c.inputs["h_age_min"],
                                         "has_model": c.inputs["has_model"], "has_upstream": c.inputs["has_upstream"],
                                         "upstream_buoy": c.inputs["upstream_buoy"]}})
                    existing.add(key)
        if out:
            ledger.seal_many(out, now=clock())
        n_graded, stale = grade_due(ledger, clock(), self.truth, TASK)
        self.last_stale = stale
        return len(out), n_graded, self.state(clock(), ledger, stale)

    def truth(self, rec, now):
        f = self.feats.get(rec["meta"].get("buoy"))
        target = from_iso(rec["target_time"])
        stale = now - target > timedelta(hours=STALE_HOURS)
        if f is None or now < target + timedelta(minutes=40):
            return None, stale
        tr = f.truth_at(target)
        if tr is None:
            return None, stale
        # wait for a report at or after the target unless the buoy is clearly late
        i = f.index(target)
        if np.isnan(f.h[i:i + 4]).all() and now < target + timedelta(hours=3):
            return None, stale
        p = rec["prediction"]["wave_height_m"]
        return {"wave_height_m": round(tr, 3), "abs_error_cm": round(abs(p - tr) * 100, 2)}, False

    def state(self, now, ledger, stale, past_hours: int = 24) -> dict:
        from .live import _scoreboard
        joined = ledger.joined()
        lo, hi = now - timedelta(hours=past_hours), now + timedelta(hours=25)
        per = defaultdict(lambda: defaultdict(lambda: {k: [] for k in
                                                       ("lead_h", "target", "issued", "wave_height_m",
                                                        "truth_wave_height_m", "error_cm")}))
        for r in joined:
            if r["task"] != TASK:
                continue
            t = from_iso(r["target_time"])
            if not (lo <= t <= hi):
                continue
            d = per[r["meta"]["buoy"]][r["model"]]
            g = r["grade"]
            d["lead_h"].append(r["meta"]["lead_h"])
            d["target"].append(r["target_time"])
            d["issued"].append(r["issued_at"])
            d["wave_height_m"].append(r["prediction"]["wave_height_m"])
            d["truth_wave_height_m"].append(g["truth"]["wave_height_m"] if g else None)
            d["error_cm"].append(g["truth"]["abs_error_cm"] if g else None)
        buoys = []
        for b, f in self.feats.items():
            w = f.obs_df["WVHT"].dropna()
            last = w.index[-1] if len(w) else None
            dpd = f.obs_df["DPD"].dropna() if "DPD" in f.obs_df else pd.Series(dtype=float)
            buoys.append({
                "id": b, "name": f.meta.get("name"), "lat": f.meta.get("lat"), "lon": f.meta.get("lon"),
                "latest_obs_time": to_iso(last.to_pydatetime()) if last is not None else None,
                "latest_wave_height_m": round(float(w.iloc[-1]), 2) if len(w) else None,
                "latest_dominant_period_s": round(float(dpd.iloc[-1]), 1) if len(dpd) else None,
                "upstream_buoy": f.upstream.id if f.upstream else None,
                "upstream_distance_km": round(f.upstream.distance_m / 1000) if f.upstream else None,
                "observed": _series(w, now - timedelta(hours=48), now),
                "wave_model": _series(f.model, now - timedelta(hours=24), now + timedelta(hours=48))
                if f.model is not None else None,
                "forecasts": {m: dict(v) for m, v in per.get(b, {}).items()},
            })
        offshore = []
        for o, df in self.offshore.items():
            w = df["WVHT"].dropna()
            offshore.append({"id": o, "latest_obs_time": to_iso(w.index[-1].to_pydatetime()) if len(w) else None,
                             "latest_wave_height_m": round(float(w.iloc[-1]), 2) if len(w) else None,
                             "observed": _series(w, now - timedelta(hours=48), now)})
        return {"quantity": "significant wave height (WVHT), metres; errors in cm",
                "leads_h": list(LEADS), "models": [m.name for m in self.models or []],
                "fit": self.fit_info, "forecast_window": {"from": to_iso(lo), "to": to_iso(hi)},
                "buoys": buoys, "offshore": offshore,
                "scoreboard": _scoreboard(joined, TASK, "abs_error_cm"), "stale": stale}
