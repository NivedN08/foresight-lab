"""Synthetic fields and pilot reports for offline tests (no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from lab.tasks.turb import fields as F
from lab.tasks.turb.interp import pressure_to_fl


def fieldset(start: datetime, hours: int, fetched_at: datetime, bump: float = 0.0) -> F.FieldSet:
    times = np.array([int((start + timedelta(hours=h)).timestamp()) for h in range(hours)], dtype=np.int64)
    th = (times - times[0])[:, None, None, None] / 3600.0
    lev = F.LEVELS[None, :, None, None]
    lat = F.LATS[None, None, :, None]
    lon = F.LONS[None, None, None, :]
    z_std = pressure_to_fl(F.LEVELS)[None, :, None, None] * 30.48
    jet = np.exp(-((lat - 38.0 - 3 * np.sin(th / 9.0)) / 5.0) ** 2) * np.exp(-((lev - 250.0) / 120.0) ** 2)
    speed = 12.0 + 45.0 * jet * (1 + 0.2 * np.sin(lon / 7.0)) + bump
    direction = 270.0 + 25.0 * np.sin(lon / 9.0 + th / 12.0)
    height = z_std + 80.0 * np.sin(np.deg2rad(lat) * 3) + 0.0 * lon
    temp = np.maximum(15.0 - 0.0065 * z_std, -56.5) - 0.3 * (lat - 37.0) + 0.0 * lon + 0.0 * th
    shape = (len(times), len(F.LEVELS), len(F.LATS), len(F.LONS))
    b = lambda a: np.broadcast_to(a, shape).astype(float).copy()
    return F.FieldSet(times=times, avail=F._availability(times, fetched_at), levels=F.LEVELS.copy(),
                      lats=F.LATS.copy(), lons=F.LONS.copy(), speed=b(speed), direction=b(direction),
                      temp=b(temp), height=b(height))


def reports(start: datetime, end: datetime, per_hour: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = int((end - start).total_seconds() / 3600 * per_hour)
    secs = np.sort(rng.uniform(0, (end - start).total_seconds(), n))
    lat = rng.uniform(26, 49, n)
    lon = rng.uniform(-124, -68, n)
    fl = rng.choice([210, 250, 300, 340, 370, 400], n).astype(float)
    p_mog = 1 / (1 + np.exp(-(-1.5 + 3.0 * np.exp(-((lat - 38) / 5) ** 2))))
    mog = rng.random(n) < p_mog
    code = np.where(mog, rng.choice([4, 4, 5, 6], n), rng.choice([0, 2, 2, 3], n))
    return pd.DataFrame({
        "time": pd.to_datetime([start + timedelta(seconds=float(s)) for s in secs], utc=True).floor("min"),
        "lat": lat.round(3), "lon": lon.round(3), "fl": fl, "code": code.astype(float),
        "raw": [f"SYN UA /OV XYZ/TM 0000/FL{int(f):03d}/TP B738/TB {'MOD' if m else 'LGT'}" for f, m in zip(fl, mog)],
        "source": "synthetic"})


T0 = datetime(2026, 11, 13, 12, 10, tzinfo=timezone.utc)
