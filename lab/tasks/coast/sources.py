"""Parsers and fetchers for the coast data sources.

Parsers take raw text/JSON and return plain pandas objects indexed by UTC time.
Fetchers build URLs, chunk long ranges, and go through the cached `Client`.

Sources:
  * NOAA CO-OPS water levels and tide predictions (6-minute, metres, MLLW)
  * NOAA CO-OPS station metadata
  * NDBC buoy realtime2 standard (.txt) and spectral summary (.spec) files
  * Open-Meteo weather (pressure, 10 m wind) and marine (wave model) hourly series
  * National Weather Service active alerts
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from io import StringIO

import numpy as np
import pandas as pd

from .http import Client, FetchError

COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
COOPS_META = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=waterlevels"
NDBC = "https://www.ndbc.noaa.gov/data/realtime2/{buoy}.{ext}"
OM_WEATHER = "https://api.open-meteo.com/v1/forecast"
OM_MARINE = "https://marine-api.open-meteo.com/v1/marine"
NWS_ALERTS = "https://api.weather.gov/alerts/active?area={area}"

STATIONS = ["9410170", "9410230", "9410660", "9411340", "9412110", "9413450", "9414290",
            "9414750", "9414523", "9414863", "9415020", "9415144", "9416841", "9418767",
            "9419750", "9431647", "9432780", "9435380", "9439040", "9440910", "9443090",
            "9444090", "9447130"]

CHUNK_DAYS = 30
EPOCH = date(2020, 1, 1)


# ------------------------------------------------------------------ parsers
def parse_coops(payload: dict | str) -> pd.Series:
    """CO-OPS datagetter JSON (water_level or predictions) -> Series of metres
    indexed by UTC time. Missing or blank values are dropped."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if "error" in payload:
        return pd.Series(dtype=float)
    rows = payload.get("data") or payload.get("predictions") or []
    t, v = [], []
    for r in rows:
        s = r.get("v", "")
        if s in ("", None):
            continue
        t.append(r["t"])
        v.append(float(s))
    if not t:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(t, format="%Y-%m-%d %H:%M", utc=True)
    s = pd.Series(v, index=idx, dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def parse_station_meta(payload: dict | str) -> dict[str, dict]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    out = {}
    for s in payload.get("stations", []):
        out[str(s["id"])] = {"id": str(s["id"]), "name": s.get("name", ""),
                            "lat": float(s["lat"]), "lon": float(s["lng"]),
                            "state": s.get("state", "")}
    return out


def parse_ndbc(text: str) -> pd.DataFrame:
    """NDBC realtime2 .txt or .spec -> DataFrame indexed by UTC time.
    'MM' becomes NaN; numeric columns are floats, compass-letter columns stay text."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("#"):
        return pd.DataFrame()
    header = lines[0].lstrip("#").split()
    body = [ln for ln in lines if not ln.startswith("#")]
    rows = [ln.split() for ln in body]
    rows = [r for r in rows if len(r) == len(header)]
    df = pd.DataFrame(rows, columns=header)
    if df.empty:
        return df
    t = pd.to_datetime(df["YY"] + "-" + df["MM"] + "-" + df["DD"] + " " + df["hh"] + ":" + df["mm"],
                       format="%Y-%m-%d %H:%M", utc=True)
    df = df.drop(columns=["YY", "MM", "DD", "hh", "mm"])
    df = df.replace("MM", np.nan)
    for c in df.columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().sum() >= df[c].notna().sum() * 0.9:
            df[c] = conv
    df.index = t
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def parse_open_meteo(payload: list | dict | str) -> list[pd.DataFrame]:
    """Open-Meteo forecast or marine JSON (one or many locations) -> list of
    hourly DataFrames indexed by UTC time, one per requested location."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        payload = [payload]
    out = []
    for loc in payload:
        h = loc["hourly"]
        idx = pd.to_datetime(h["time"], utc=True)
        df = pd.DataFrame({k: pd.to_numeric(pd.Series(v), errors="coerce").to_numpy()
                           for k, v in h.items() if k != "time"}, index=idx)
        out.append(df)
    return out


COASTAL_EVENTS = ("Coastal Flood", "High Surf", "Beach Hazards", "Rip Current", "Storm Surge",
                  "Hazardous Seas", "Gale", "Small Craft", "Tsunami", "Storm Warning")


def parse_alerts(payload: dict | str) -> list[dict]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    out = []
    for f in payload.get("features", []):
        p = f.get("properties", {})
        ev = p.get("event", "")
        if not any(k in ev for k in COASTAL_EVENTS):
            continue
        out.append({"event": ev, "headline": p.get("headline"), "area": p.get("areaDesc"),
                    "severity": p.get("severity"), "onset": p.get("onset"),
                    "ends": p.get("ends") or p.get("expires"), "sender": p.get("senderName")})
    return out


# ----------------------------------------------------------------- fetchers
def _chunks(begin: date, end: date):
    """Fixed 30-day chunks aligned to EPOCH covering [begin, end]."""
    k = (begin - EPOCH).days // CHUNK_DAYS
    while True:
        a = EPOCH + timedelta(days=k * CHUNK_DAYS)
        b = a + timedelta(days=CHUNK_DAYS - 1)
        if a > end:
            break
        yield a, b
        k += 1


def coops_url(station: str, product: str, a: date, b: date) -> str:
    return (f"{COOPS}?station={station}&product={product}&datum=MLLW&units=metric"
            f"&time_zone=gmt&format=json&begin_date={a:%Y%m%d}&end_date={b:%Y%m%d}")


def fetch_coops(client: Client, station: str, product: str, begin: datetime, end: datetime,
                now: datetime, recent_ttl: float = 300.0) -> pd.Series:
    """Water level or predictions for [begin, end]. Old chunks are cached forever;
    chunks touching the last two days are refreshed after `recent_ttl` seconds."""
    parts = []
    today = now.date()
    for a, b in _chunks(begin.date(), end.date()):
        if product == "predictions":
            ttl = None  # the tide table is deterministic
        elif b < today - timedelta(days=2):
            ttl = None
        else:
            ttl = 6 * 3600.0
            b = min(b, today + timedelta(days=1))
        try:
            parts.append(parse_coops(client.get_text(coops_url(station, product, a, b), ttl=ttl)))
        except FetchError as e:
            client.log.append(str(e))
    if product != "predictions":
        # small, frequently refreshed tail so live cycles see the newest 6-minute values
        a = today - timedelta(days=1)
        try:
            parts.append(parse_coops(client.get_text(
                coops_url(station, product, a, today + timedelta(days=1)), ttl=recent_ttl)))
        except FetchError as e:
            client.log.append(str(e))
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[(s.index >= begin) & (s.index <= end)]


def fetch_station_meta(client: Client) -> dict[str, dict]:
    return parse_station_meta(client.get_text(COOPS_META, ttl=30 * 86400.0))


def fetch_ndbc(client: Client, buoy: str, ext: str = "txt", ttl: float = 600.0) -> pd.DataFrame:
    return parse_ndbc(client.get_text(NDBC.format(buoy=buoy, ext=ext), ttl=ttl))


def _coords(points: list[tuple[float, float]]) -> str:
    lat = ",".join(f"{p[0]:.3f}" for p in points)
    lon = ",".join(f"{p[1]:.3f}" for p in points)
    return f"latitude={lat}&longitude={lon}"


def fetch_weather(client: Client, points: list[tuple[float, float]], past_days: int = 92,
                  ttl: float = 3600.0) -> list[pd.DataFrame] | None:
    """Hourly pressure_msl (hPa) and 10 m wind (m/s, direction from) per point.
    Returns None if the service cannot be reached and nothing is cached.

    Values after the request time are a real forecast (what the live loop uses).
    Values before it are short-lead forecasts stitched together, close to an
    analysis: using them as "the forecast at issue time" in a backtest leaks the
    future. For history use `fetch_weather_runs`."""
    url = (f"{OM_WEATHER}?{_coords(points)}&hourly=pressure_msl,wind_speed_10m,wind_direction_10m"
           f"&wind_speed_unit=ms&past_days={past_days}&forecast_days=3&timezone=GMT")
    try:
        return parse_open_meteo(client.get_text(url, ttl=ttl))
    except Exception as e:  # graceful degradation: models run on gauge data alone
        client.log.append(f"weather unavailable: {e}")
        return None


OM_PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
WEATHER_VARS = ("pressure_msl", "wind_speed_10m", "wind_direction_10m")
MARINE_VARS = ("wave_height", "wave_period")
RUN_OFFSETS = (1, 2)


def _split_runs(dfs: list[pd.DataFrame], variables, offsets) -> dict[int, list[pd.DataFrame]]:
    """Columns `<var>_previous_day<k>` -> {k: [DataFrame with plain <var> columns, per location]}."""
    out = {}
    for k in offsets:
        out[k] = [df[[f"{v}_previous_day{k}" for v in variables]].rename(
            columns={f"{v}_previous_day{k}": v for v in variables}) for df in dfs]
    return out


def _range_ttl(end: date, now: datetime) -> float | None:
    """A date range that ended more than two days ago never changes again."""
    return None if end < now.date() - timedelta(days=2) else 3600.0


def fetch_weather_runs(client: Client, points: list[tuple[float, float]], start: date, end: date,
                       now: datetime, offsets=RUN_OFFSETS) -> dict[int, list[pd.DataFrame]] | None:
    """Honest weather history from the Open-Meteo Previous Runs API.

    `_previous_day<k>` is the value for a valid hour as forecast by a model run
    initialised at least 24*k hours before that hour (checked 2026-11-13: for
    valid hours too far in the future to have such a run, the API repeats the
    newest forecast instead, so only valid hours up to now are used). Returns
    {k: [hourly DataFrame with pressure_msl, wind_speed_10m, wind_direction_10m]
    per point}, or None if the service cannot be reached and nothing is cached.
    """
    hourly = ",".join(f"{v}_previous_day{k}" for k in offsets for v in WEATHER_VARS)
    url = (f"{OM_PREVIOUS_RUNS}?{_coords(points)}&hourly={hourly}&wind_speed_unit=ms"
           f"&start_date={start:%Y-%m-%d}&end_date={end:%Y-%m-%d}&timezone=GMT")
    try:
        return _split_runs(parse_open_meteo(client.get_text(url, ttl=_range_ttl(end, now))),
                           WEATHER_VARS, offsets)
    except Exception as e:
        client.log.append(f"weather previous runs unavailable: {e}")
        return None


def fetch_marine_runs(client: Client, points: list[tuple[float, float]], start: date, end: date,
                      now: datetime, offsets=RUN_OFFSETS) -> dict[int, list[pd.DataFrame]] | None:
    """Honest wave-model history: the marine API's own `_previous_day<k>`
    variables (same meaning as for weather). {k: [DataFrame with wave_height,
    wave_period per point]} or None."""
    hourly = ",".join(f"{v}_previous_day{k}" for k in offsets for v in MARINE_VARS)
    url = (f"{OM_MARINE}?{_coords(points)}&hourly={hourly}"
           f"&start_date={start:%Y-%m-%d}&end_date={end:%Y-%m-%d}&timezone=GMT")
    try:
        return _split_runs(parse_open_meteo(client.get_text(url, ttl=_range_ttl(end, now))),
                           MARINE_VARS, offsets)
    except Exception as e:
        client.log.append(f"marine previous runs unavailable: {e}")
        return None


def fetch_marine(client: Client, points: list[tuple[float, float]], past_days: int = 92,
                 ttl: float = 3600.0) -> list[pd.DataFrame] | None:
    url = (f"{OM_MARINE}?{_coords(points)}&hourly=wave_height,wave_period,swell_wave_height,"
           f"swell_wave_period,swell_wave_direction&past_days={past_days}&forecast_days=3&timezone=GMT")
    try:
        return parse_open_meteo(client.get_text(url, ttl=ttl))
    except Exception as e:
        client.log.append(f"marine model unavailable: {e}")
        return None


def fetch_alerts(client: Client, area: str = "CA", ttl: float = 900.0) -> list[dict]:
    try:
        return parse_alerts(client.get_text(NWS_ALERTS.format(area=area), ttl=ttl))
    except Exception as e:
        client.log.append(f"alerts unavailable: {e}")
        return []


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
