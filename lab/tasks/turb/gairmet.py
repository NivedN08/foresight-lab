"""Official baseline: G-AIRMET high-altitude turbulence (TURB-HI) polygons from
the Aviation Weather Center. A G-AIRMET is a snapshot valid at 3-hourly times;
for a valid hour we use the snapshot nearest in time (at most 90 minutes away).
Risk is 1 inside a polygon whose altitude band contains the flight level, else 0.
Live only: there is no archive in this pipeline, so it is not in the backtest."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from . import net
from .timefmt import parse_awc_time


def parse_gairmets(items: list[dict]) -> list[dict]:
    out = []
    for x in items:
        if x.get("hazard") != "TURB-HI" or (x.get("geometryType") or x.get("geom")) != "AREA":
            continue
        try:
            coords = [(float(c["lat"]), float(c["lon"])) for c in x["coords"]]
            base = 0 if x.get("base") in (None, "", "SFC") else int(x["base"])
            top = int(x["top"]) if x.get("top") not in (None, "") else 600
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"valid_time": parse_awc_time(x["validTime"]).isoformat().replace("+00:00", "Z"),
                    "issue_time": int(x.get("issueTime") or 0),
                    "base_fl": base, "top_fl": top, "severity": x.get("severity"),
                    "coords": [[round(a, 3), round(b, 3)] for a, b in coords]})
    return out


def fetch_gairmets(log=print, now: datetime | None = None) -> list[dict]:
    from .pireps import save_raw
    polys = []
    for fore in (0, 3, 6, 9, 12):
        r = net.get("https://aviationweather.gov/api/data/gairmet",
                    params={"format": "json", "hazard": "turb-hi", "fore": fore}, min_gap=1.0, log=log)
        if now is not None:
            save_raw("gairmet", r.text, now, suffix=f"_f{fore:02d}")
        items = r.json() if r.text.strip() else []
        if isinstance(items, list):
            polys.extend(parse_gairmets(items))
    return polys


def snapshot_for(polys: list[dict], valid: datetime, max_gap=timedelta(minutes=90)) -> list[dict] | None:
    """Polygons of the snapshot nearest to `valid`, or None if none is close."""
    if not polys:
        return None
    times = sorted({p["valid_time"] for p in polys})
    best = min(times, key=lambda s: abs(parse_awc_time(s) - valid))
    if abs(parse_awc_time(best) - valid) > max_gap:
        return None
    return [p for p in polys if p["valid_time"] == best]


def point_in_polygon(lat, lon, coords) -> np.ndarray:
    """Even-odd ray casting; coords is a list of (lat, lon) vertices."""
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    inside = np.zeros(np.broadcast(lat, lon).shape, bool)
    n = len(coords)
    for i in range(n):
        y1, x1 = coords[i]
        y2, x2 = coords[(i + 1) % n]
        if y1 == y2:
            continue
        crosses = (y1 > lat) != (y2 > lat)
        x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (lon < x_at)
    return inside


def risk_at(polys: list[dict], lat, lon, fl) -> np.ndarray:
    lat, lon, fl = np.broadcast_arrays(*(np.asarray(a, float) for a in (lat, lon, fl)))
    risk = np.zeros(lat.shape)
    for p in polys:
        in_band = (fl >= p["base_fl"]) & (fl <= p["top_fl"])
        risk = np.maximum(risk, (in_band & point_in_polygon(lat, lon, p["coords"])).astype(float))
    return risk


def risk_grid(polys: list[dict], lats, lons, flight_levels) -> np.ndarray:
    """(level, lat, lon) grid of 0/1."""
    FL, LA, LO = np.meshgrid(np.asarray(flight_levels, float), np.asarray(lats, float),
                             np.asarray(lons, float), indexing="ij")
    return risk_at(polys, LA, LO, FL)
