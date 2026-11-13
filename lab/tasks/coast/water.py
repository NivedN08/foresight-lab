"""Water level above the tide table: data assembly, cases and truth.

Quantity: residual = observed water level minus predicted tide (metres).

Every station is put on a regular 6-minute grid. Features for a case issued at
time T are read only from grid cells at or before T (observations) or from the
tide table and the weather forecast (allowed: both are knowable at T). Truth at
target time is the mean of the 6-minute observed residuals within +-6 minutes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from lab.core.task import Case

STEP = pd.Timedelta(minutes=6)
PER_HOUR = 10
LEADS = (1, 3, 6, 12, 24)
HIST_HOURS = 24
MEAN_HOURS = 72
TASK = "coast_water"


def residual(obs: pd.Series, tide: pd.Series) -> pd.Series:
    """Observed minus predicted on the times both exist (metres)."""
    both = pd.concat([obs.rename("obs"), tide.rename("tide")], axis=1, sort=True).dropna()
    return (both["obs"] - both["tide"]).rename("residual")


def _epoch(t) -> float:
    return pd.Timestamp(t).timestamp()


@dataclass
class StationData:
    """Raw series for one gauge. `weather` is an hourly DataFrame with
    pressure_msl (hPa), wind_speed_10m (m/s), wind_direction_10m (deg, from),
    or None when the weather service was unavailable."""
    id: str
    name: str
    lat: float
    lon: float
    obs: pd.Series
    tide: pd.Series
    weather: pd.DataFrame | None = None


class StationFeatures:
    """Precomputed arrays on a 6-minute grid. Every array value at index i
    depends only on observations at grid indices <= i."""

    def __init__(self, sd: StationData, start: pd.Timestamp, end: pd.Timestamp):
        self.sd = sd
        self.start = start.floor("6min")
        self.end = end.ceil("6min")
        self.grid = pd.date_range(self.start, self.end, freq="6min")
        res = residual(sd.obs, sd.tide)
        self.r = res.reindex(self.grid).to_numpy(dtype=float)
        tide = sd.tide.reindex(self.grid)
        self.tide = tide.to_numpy(dtype=float)
        n = len(self.grid)
        # observation-side value at i: mean of the cells i-2..i (the 12 minutes up to i)
        win = pd.Series(self.r).rolling(3, min_periods=1).mean().to_numpy()
        self.r_now = win
        self.r_mean = pd.Series(self.r).rolling(MEAN_HOURS * PER_HOUR, min_periods=PER_HOUR * 12).mean().to_numpy()
        self.n = n
        self.t0 = _epoch(self.start)
        # weather on hourly times (forecast product: values after T are a forecast)
        self.has_weather = False
        if sd.weather is not None and len(sd.weather):
            w = sd.weather.dropna(subset=["pressure_msl"])
            if len(w) > 24:
                self.has_weather = True
                self.w_t = np.array([_epoch(t) for t in w.index])
                self.w_p = w["pressure_msl"].to_numpy(float)
                spd = w["wind_speed_10m"].to_numpy(float)
                dirn = np.deg2rad(w["wind_direction_10m"].to_numpy(float))
                u = -spd * np.sin(dirn)   # direction is where the wind comes FROM
                v = -spd * np.cos(dirn)
                self.w_tx = spd * u       # wind stress proxy, m^2/s^2, toward east
                self.w_ty = spd * v       # toward north

    def index(self, t: datetime) -> int:
        return int(round((_epoch(t) - self.t0) / 360.0))

    def time(self, i: int) -> datetime:
        return (self.start + STEP * i).to_pydatetime()

    def weather_at(self, t_epoch: np.ndarray, arr: np.ndarray) -> np.ndarray:
        out = np.interp(t_epoch, self.w_t, arr, left=np.nan, right=np.nan)
        return out

    def truth_at(self, t: datetime) -> float | None:
        i = self.index(t)
        if i < 1 or i + 1 >= self.n:
            return None
        v = self.r[i - 1:i + 2]
        v = v[~np.isnan(v)]
        return float(v.mean()) if v.size else None

    def tide_at(self, t: datetime) -> float:
        i = self.index(t)
        if 0 <= i < self.n and not np.isnan(self.tide[i]):
            return float(self.tide[i])
        s = self.sd.tide
        return float(np.interp(_epoch(t), [_epoch(x) for x in s.index], s.to_numpy()))


def current_residual(f: StationFeatures, i: int, max_age_steps: int = 10) -> tuple[float, int] | None:
    """Mean of the newest (up to three) residuals in the hour before index i.
    Returns (value, age in 6-minute steps of the newest value)."""
    lo = max(0, i - max_age_steps)
    seg = f.r[lo:i + 1]
    ok = np.where(~np.isnan(seg))[0]
    if ok.size == 0:
        return None
    newest = ok[-1]
    vals = seg[ok[ok >= newest - 2]]
    return float(vals.mean()), int(len(seg) - 1 - newest)


def build_case(f: StationFeatures, issue: datetime, lead_h: int) -> Case | None:
    """One case: station f, issued at `issue`, target issue + lead. Uses only
    observations at or before `issue`, plus tide table and weather forecast."""
    i = f.index(issue)
    if i < 0 or i >= f.n:
        return None
    cur = current_residual(f, i)
    if cur is None:
        return None
    r0, age = cur
    m = f.r_mean[i]
    if np.isnan(m):
        return None
    hist_idx = i - PER_HOUR * np.arange(HIST_HOURS)
    hist = np.where(hist_idx >= 0, f.r_now[np.clip(hist_idx, 0, None)], np.nan)
    r3 = hist[3]
    trend = r0 - r3 if not np.isnan(r3) else 0.0
    target = issue + timedelta(hours=lead_h)
    inputs = {
        "station": f.sd.id,
        "lead_h": lead_h,
        "r_now": r0,
        "r_age_min": age * 6,
        "r_hist": hist,                   # hourly, newest first, metres
        "r_mean": float(m),
        "trend3": float(trend),
        "tide_now": f.tide_at(issue),
        "tide_target": f.tide_at(target),
        "hour_utc": issue.hour,
        "doy": issue.timetuple().tm_yday,
        "has_weather": False,
        "dp_hpa": 0.0, "taux": 0.0, "tauy": 0.0, "p_issue": float("nan"), "p_target": float("nan"),
    }
    if f.has_weather:
        t_i = _epoch(issue)
        t_win = t_i + 3600.0 * np.arange(1, lead_h + 1)
        p_i = float(f.weather_at(np.array([t_i]), f.w_p)[0])
        p_t = float(f.weather_at(np.array([t_win[-1]]), f.w_p)[0])
        tx = f.weather_at(t_win, f.w_tx)
        ty = f.weather_at(t_win, f.w_ty)
        if not (np.isnan(p_i) or np.isnan(p_t) or np.isnan(tx).any() or np.isnan(ty).any()):
            inputs.update(has_weather=True, p_issue=p_i, p_target=p_t, dp_hpa=p_t - p_i,
                          taux=float(tx.mean()), tauy=float(ty.mean()))
    cid = f"{f.sd.id}|{issue:%Y%m%dT%H%MZ}|{lead_h}h"
    return Case(case_id=cid, issue_time=issue, target_time=target, inputs=inputs)


def build_history_cases(feats: dict[str, StationFeatures], start: datetime, end: datetime,
                        leads=LEADS) -> tuple[list[Case], list[dict]]:
    """Hourly issue times in [start, end - lead]; keeps cases whose truth exists."""
    cases, truths = [], []
    for sid, f in feats.items():
        t = pd.Timestamp(start).ceil("h").to_pydatetime()
        while t <= end:
            for h in leads:
                target = t + timedelta(hours=h)
                if target > end:
                    continue
                tr = f.truth_at(target)
                if tr is None:
                    continue
                c = build_case(f, t, h)
                if c is None:
                    continue
                cases.append(c)
                truths.append({"residual_m": tr})
            t += timedelta(hours=1)
    return cases, truths
