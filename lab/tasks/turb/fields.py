"""Upper-air fields on a fixed continental-US grid, from Open-Meteo.

Each download is stored as one compressed chunk (cache/turb/fields/*.npz).
Every valid hour in a chunk carries an "available" time:
  * hours after the download time are forecasts, available at the download
    time (true: the values existed then);
  * hours at or before the download time are Open-Meteo's past values. These
    are stitched together from model runs started at or after each valid hour
    (and those runs may have assimilated aircraft observations). We record them
    as available one hour before their valid time only so that they can be
    ordered and merged; that is a convention, not when they really existed.
    Anything evaluated on these past values is an analysis-time
    (hindsight-field) comparison, not a forecast test.
`FieldStore.known_at(t)` merges the chunks using only values recorded as
available at or before t, preferring the most recently available value.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import net

LEVELS = np.array([150, 200, 250, 300, 400, 500], dtype=float)   # hPa, top to bottom
LATS = np.round(np.arange(25.0, 50.0 + 1e-9, 2.5), 3)
LONS = np.round(np.arange(-125.0, -67.5 + 1e-9, 2.5), 3)
VARS = ("speed", "direction", "temp", "height")
_OM_NAME = {"speed": "wind_speed_{p}hPa", "direction": "wind_direction_{p}hPa",
            "temp": "temperature_{p}hPa", "height": "geopotential_height_{p}hPa"}
FIELD_DIR = net.CACHE / "fields"


def _to_epoch(t: datetime) -> int:
    return int(t.timestamp())


def _from_epoch(s) -> datetime:
    return datetime.fromtimestamp(int(s), tz=timezone.utc)


@dataclass
class FieldSet:
    """Gridded fields. Arrays are (time, level, lat, lon); temp in deg C,
    height = geopotential height in m, speed in m/s, direction in degrees
    (meteorological: where the wind blows from). times/avail are epoch seconds."""
    times: np.ndarray
    avail: np.ndarray
    levels: np.ndarray
    lats: np.ndarray
    lons: np.ndarray
    speed: np.ndarray
    direction: np.ndarray
    temp: np.ndarray
    height: np.ndarray

    def known_at(self, t: datetime) -> "FieldSet":
        return self.select(self.avail <= _to_epoch(t))

    def select(self, mask) -> "FieldSet":
        return replace(self, times=self.times[mask], avail=self.avail[mask],
                       **{v: getattr(self, v)[mask] for v in VARS})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, times=self.times, avail=self.avail, levels=self.levels,
                            lats=self.lats, lons=self.lons,
                            **{v: getattr(self, v).astype(np.float32) for v in VARS})

    @classmethod
    def load(cls, path: Path) -> "FieldSet":
        z = np.load(path)
        return cls(**{k: z[k] for k in ("times", "avail", "levels", "lats", "lons")},
                   **{v: z[v].astype(np.float64) for v in VARS})

    @property
    def datetimes(self) -> list[datetime]:
        return [_from_epoch(s) for s in self.times]


def merge(chunks: list[FieldSet], as_of: datetime | None = None) -> FieldSet | None:
    """One FieldSet with, for each valid hour, the value available most recently
    (and, if as_of is given, no later than as_of)."""
    best: dict[int, tuple[int, int, int]] = {}  # valid -> (avail, chunk index, row)
    for ci, c in enumerate(chunks):
        for row, (tv, ta) in enumerate(zip(c.times, c.avail)):
            if as_of is not None and ta > _to_epoch(as_of):
                continue
            key = (int(ta), ci, row)
            if int(tv) not in best or key > best[int(tv)]:
                best[int(tv)] = key
    if not best:
        return None
    order = sorted(best)
    ref = chunks[0]
    arrays = {v: np.stack([getattr(chunks[best[t][1]], v)[best[t][2]] for t in order]) for v in VARS}
    return FieldSet(times=np.array(order, dtype=np.int64),
                    avail=np.array([best[t][0] for t in order], dtype=np.int64),
                    levels=ref.levels, lats=ref.lats, lons=ref.lons, **arrays)


class FieldStore:
    def __init__(self, directory: Path = FIELD_DIR):
        self.dir = Path(directory)

    def paths(self) -> list[Path]:
        return sorted(self.dir.glob("*.npz")) if self.dir.exists() else []

    def chunks(self) -> list[FieldSet]:
        return [FieldSet.load(p) for p in self.paths()]

    def add(self, fs: FieldSet, kind: str, fetched_at: datetime) -> Path:
        path = self.dir / f"{kind}_{fetched_at:%Y%m%dT%H%M%SZ}.npz"
        fs.save(path)
        return path

    def known_at(self, t: datetime | None = None) -> FieldSet | None:
        return merge(self.chunks(), as_of=t)

    def latest_fetch(self, kind: str) -> datetime | None:
        names = [p.stem.split("_", 1)[1] for p in self.paths() if p.stem.startswith(kind + "_")]
        if not names:
            return None
        return datetime.strptime(max(names), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- download
def _availability(times: np.ndarray, fetched_at: datetime) -> np.ndarray:
    """Forecast hours: the download time. Past (hindsight) hours: one hour before
    the valid time, a bookkeeping convention (see the module docstring)."""
    f = _to_epoch(fetched_at)
    return np.where(times <= f, times - 3600, f).astype(np.int64)


def fetch_open_meteo(fetched_at: datetime, *, past_days: int = 0, past_hours: int | None = None,
                     forecast_days: int | None = None, forecast_hours: int | None = None,
                     batch: int = 40, log=print) -> FieldSet:
    """Download the whole grid. Requests are batched (several coordinates per
    call) and paced with Open-Meteo's X-RateLimit headers (600 per minute)."""
    hourly = ",".join(_OM_NAME[v].format(p=int(p)) for p in LEVELS for v in VARS)
    pts = [(la, lo) for la in LATS for lo in LONS]
    params_common = {"hourly": hourly, "wind_speed_unit": "ms", "timezone": "GMT"}
    if past_hours is not None:
        params_common["past_hours"] = past_hours
    else:
        params_common["past_days"] = past_days
    if forecast_hours is not None:
        params_common["forecast_hours"] = forecast_hours
    else:
        params_common["forecast_days"] = forecast_days or 1
    results: list[dict] = []
    total_cost = 0.0
    for i in range(0, len(pts), batch):
        part = pts[i:i + batch]
        params = dict(params_common, latitude=",".join(f"{a:g}" for a, _ in part),
                      longitude=",".join(f"{b:g}" for _, b in part))
        r = net.get("https://api.open-meteo.com/v1/forecast", params=params, min_gap=1.0,
                    timeout=180, log=log)
        data = r.json()
        data = data if isinstance(data, list) else [data]
        if len(data) != len(part):
            raise net.FetchError(f"Open-Meteo returned {len(data)} locations for {len(part)}")
        results.extend(data)
        cost = float(r.headers.get("X-RateLimit-Cost", "nan"))
        remaining = float(r.headers.get("X-RateLimit-Remaining", "nan"))
        reset = float(r.headers.get("X-RateLimit-Reset", "nan"))
        total_cost += cost if cost == cost else 0.0
        log(f"  Open-Meteo {i + len(part)}/{len(pts)} points, cost {cost:.1f}, minute budget left {remaining:.0f}")
        if i + batch < len(pts) and remaining == remaining and remaining < cost * 1.1:
            wait = max(5.0, (reset - time.time()) + 2 if reset == reset else 61.0)
            log(f"  pacing: waiting {wait:.0f}s for the per-minute budget")
            time.sleep(wait)
    log(f"  Open-Meteo total cost {total_cost:.0f} (free tier: 600/minute, about 10,000/day)")
    times = np.array([_to_epoch(datetime.fromisoformat(s).replace(tzinfo=timezone.utc))
                      for s in results[0]["hourly"]["time"]], dtype=np.int64)
    T, L, Y, X = len(times), len(LEVELS), len(LATS), len(LONS)
    arrays = {v: np.full((T, L, Y, X), np.nan) for v in VARS}
    for k, d in enumerate(results):
        y, x = divmod(k, X)
        h = d["hourly"]
        if len(h["time"]) != T:
            raise net.FetchError("Open-Meteo locations returned different time axes")
        for li, p in enumerate(LEVELS):
            for v in VARS:
                vals = h.get(_OM_NAME[v].format(p=int(p)))
                if vals is not None:
                    arrays[v][:, li, y, x] = np.array([np.nan if a is None else a for a in vals], float)
    return FieldSet(times=times, avail=_availability(times, fetched_at), levels=LEVELS.copy(),
                    lats=LATS.copy(), lons=LONS.copy(), **arrays)


def grid_spec() -> dict:
    from .interp import level_to_fl
    return {"lats": [float(a) for a in LATS], "lons": [float(a) for a in LONS],
            "levels_hpa": [int(p) for p in LEVELS],
            "flight_levels": [int(round(level_to_fl(p))) for p in LEVELS],
            "resolution_deg": 2.5}


def drop_nan_hours(fs: FieldSet) -> FieldSet:
    ok = np.array([np.isfinite(fs.speed[i]).mean() > 0.95 for i in range(len(fs.times))], bool)
    return fs.select(ok)


def ensure_backfill(store: FieldStore, now: datetime, days: int = 31, log=print) -> None:
    """Download the past `days` days once (about 1,450 Open-Meteo call units)."""
    if store.latest_fetch("backfill") is not None:
        return
    log(f"downloading {days} days of upper-air fields on the {len(LATS)}x{len(LONS)} grid")
    fs = fetch_open_meteo(now, past_days=days, forecast_days=1, batch=40, log=log)
    store.add(drop_nan_hours(fs), "backfill", now)


def hours_floor(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)

