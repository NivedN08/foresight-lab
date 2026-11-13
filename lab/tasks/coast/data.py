"""Load every station's series (gauge, tide table, weather) through the cache."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import sources as S
from .http import Client
from .water import StationData


def load_stations(client: Client, now: datetime, days: int, stations=None,
                  with_weather: bool = True, log=print) -> dict[str, StationData]:
    stations = stations or S.STATIONS
    meta = S.fetch_station_meta(client)
    begin = now - timedelta(days=days + 4)
    out: dict[str, StationData] = {}
    for sid in stations:
        m = meta.get(sid, {"name": sid, "lat": float("nan"), "lon": float("nan")})
        obs = S.fetch_coops(client, sid, "water_level", begin, now, now)
        if len(obs) < 240:
            log(f"  dropping {sid} {m.get('name')}: only {len(obs)} water level values")
            continue
        tide = S.fetch_coops(client, sid, "predictions", begin, now + timedelta(days=2), now)
        if len(tide) < 240:
            log(f"  dropping {sid} {m.get('name')}: no tide predictions")
            continue
        out[sid] = StationData(id=sid, name=m["name"], lat=m["lat"], lon=m["lon"], obs=obs, tide=tide)
    if with_weather and out:
        pts = [(sd.lat, sd.lon) for sd in out.values()]
        wx = S.fetch_weather(client, pts, past_days=min(92, days + 2))
        if wx is None:
            log("  weather forecast unavailable: models run on gauge data alone")
        else:
            for sd, w in zip(out.values(), wx):
                sd.weather = w
    return out
