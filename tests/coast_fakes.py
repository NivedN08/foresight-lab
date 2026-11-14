"""Fake clock and fake HTTP client for the coast tests (no network)."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

FIX = Path(__file__).parent / "fixtures" / "coast"
T0 = datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def tide_fn(t: datetime) -> float:
    h = t.timestamp() / 3600.0
    return 1.0 + 0.8 * math.sin(2 * math.pi * h / 12.42)


def resid_fn(t: datetime) -> float:
    h = t.timestamp() / 3600.0
    return 0.3 + 0.1 * math.sin(2 * math.pi * h / 50.0)


def wave_fn(t: datetime) -> float:
    h = t.timestamp() / 3600.0
    return 2.0 + 0.8 * math.sin(2 * math.pi * h / 40.0)


class FakeClient:
    """Serves synthetic CO-OPS, Open-Meteo, NDBC and NWS responses. Water level
    observations exist only up to the fake clock (and up to `cutoff[station]`)."""

    def __init__(self, clock: FakeClock, cutoff: dict[str, datetime] | None = None):
        self.clock = clock
        self.cutoff = cutoff or {}
        self.log: list[str] = []
        self.calls: list[str] = []
        self.real_ndbc = False

    def get_json(self, url, ttl=None):
        return json.loads(self.get_text(url, ttl))

    def get_text(self, url: str, ttl=None) -> str:
        self.calls.append(url)
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if "mdapi" in url:
            return (FIX / "coops_stations.json").read_text()
        if "datagetter" in url:
            return self._coops(q)
        if "alerts" in url:
            return (FIX / "nws_alerts.json").read_text()
        if "marine-api" in url:
            return self._om(q, marine=True)
        if "open-meteo" in url:
            return self._om(q, marine=False)
        if "activestations" in url:
            return (FIX / "ndbc_activestations.xml").read_text()
        if "ndbc" in url:
            if self.real_ndbc:
                return (FIX / ("ndbc_46026.spec" if url.endswith(".spec") else "ndbc_46026.txt")).read_text()
            return self._ndbc(url.rsplit("/", 1)[1].split(".")[0])
        raise AssertionError(f"unexpected url {url}")

    def _coops(self, q):
        a = datetime.strptime(q["begin_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
        b = datetime.strptime(q["end_date"], "%Y%m%d").replace(tzinfo=timezone.utc) + timedelta(hours=23, minutes=54)
        st = q["station"]
        rows = []
        t = a
        limit = b
        if q["product"] == "water_level":
            limit = min(b, self.clock(), self.cutoff.get(st, b))
        while t <= limit:
            v = tide_fn(t) + (resid_fn(t) if q["product"] == "water_level" else 0.0)
            rows.append({"t": t.strftime("%Y-%m-%d %H:%M"), "v": f"{v:.3f}"})
            t += timedelta(minutes=6)
        key = "predictions" if q["product"] == "predictions" else "data"
        return json.dumps({key: rows})

    def _ndbc(self, buoy: str) -> str:
        """Half-hourly synthetic buoy reports for 12 days up to the clock, newest first."""
        lines = ["#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE",
                 "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft"]
        end = self.clock().replace(second=0, microsecond=0)
        end -= timedelta(minutes=end.minute % 30)
        t = end
        while t > end - timedelta(days=12):
            lines.append(f"{t:%Y %m %d %H %M} 300  5.0  6.0 {wave_fn(t):5.2f}  14.0   9.0 290 1013.0  14.0  15.0    MM   MM   MM    MM")
            t -= timedelta(minutes=30)
        return "\n".join(lines) + "\n"

    def _om(self, q, marine: bool):
        lats = q["latitude"].split(",")
        now = self.clock().replace(minute=0, second=0, microsecond=0)
        if "start_date" in q:   # previous-run requests use a date range
            start = datetime.strptime(q["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            stop = datetime.strptime(q["end_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=23)
            times = [start + timedelta(hours=k) for k in range(int((stop - start).total_seconds() // 3600) + 1)]
        else:
            start = (now - timedelta(days=int(q["past_days"]))).replace(hour=0)
            times = [start + timedelta(hours=k) for k in range(int((now - start).total_seconds() // 3600) + 72)]
        out = []
        for _ in lats:
            hrs = [t.timestamp() / 3600 for t in times]
            if marine:
                base = {"wave_height": [2.0 + math.sin(h / 10) for h in hrs],
                        "wave_period": [10.0] * len(hrs), "swell_wave_height": [1.5] * len(hrs),
                        "swell_wave_period": [14.0] * len(hrs), "swell_wave_direction": [290.0] * len(hrs)}
            else:
                base = {"pressure_msl": [1013 + 5 * math.sin(h / 30) for h in hrs],
                        "wind_speed_10m": [5 + 2 * math.sin(h / 7) for h in hrs],
                        "wind_direction_10m": [(270 + 20 * math.sin(h / 11)) % 360 for h in hrs]}
            hourly = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times]}
            for name in q.get("hourly", ",".join(base)).split(","):
                var, _, k = name.partition("_previous_day")
                if var in base:   # an older run: the same curve, slightly off
                    hourly[name] = [v + (0.3 * int(k) if k else 0.0) for v in base[var]]
            out.append({"hourly": hourly})
        return json.dumps(out if len(out) > 1 else out[0])
