"""Pilot reports (PIREPs): parse the turbulence group and fetch reports.

Intensity codes (the turbulence group, `/TB ...`):
    NEG / SMOOTH = 0, light = 2, light-moderate = 3, moderate = 4,
    moderate-severe = 5, severe = 6, severe-extreme = 7, extreme = 8.
A group that names two adjacent intensities ("LGT-MOD", "LGT OCNL MOD",
"MOD TO SEV") gets the in-between code, as the Aviation Weather Center does.
NEG together with light gives 1 (still "light or less").

Classes used by the forecasters: positive = code >= 4 (moderate or greater),
negative = code <= 2 (light or less); code 3 (light-moderate) is dropped as
ambiguous. Standard reports are read from their slashed `/TB` group;
automated airline AIREPs ("ARP UAL286 ... F390 ... TB CONT SMTH IC") from the
text between "TB" and the icing group "IC" (an empty group, as in
"TB IC MOD RIME", has no turbulence intensity).

Positions come from the data sources (both decode the /OV group); this module
does not decode navaid radials itself.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import net

# Continental US box used everywhere in this task (matches the field grid).
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 24.0, 50.0, -126.0, -66.0
UPPER_FL = 200  # headline evaluation: flight level 200 and above

_WORDS = {
    "NEG": 0, "NONE": 0, "NIL": 0, "SMOOTH": 0, "SMTH": 0, "SMOTH": 0, "SMOOTHE": 0,
    "LGT": 2, "LT": 2, "LIGHT": 2, "LGHT": 2, "LIGT": 2,
    "MOD": 4, "MDT": 4, "MODERATE": 4, "MODT": 4,
    "SEV": 6, "SVR": 6, "SEVERE": 6,
    "EXTRM": 8, "EXTREME": 8, "XTRM": 8, "EXTM": 8,
}
_TB_GROUP = re.compile(r"/\s*TB\b([^/]*)")
_FL = re.compile(r"/\s*FL\s*(\d{2,3})\b")
_TM = re.compile(r"/\s*TM\s*(\d{4})\b")
_ALT = re.compile(r"(?<![\d.])(\d{3})(?![\d.])")
_ARP_TB = re.compile(r"\bTB\b(.*?)(?=\bIC\b|$)")
_ARP_FL = re.compile(r"\bF(\d{3})\b")


def intensity_from_group(text: str) -> int | None:
    """Intensity code of one turbulence group's text (without the "/TB")."""
    t = text.upper().replace("-", " - ")
    found = [_WORDS[w] for w in re.findall(r"[A-Z]+", t) if w in _WORDS]
    if not found:
        return None
    vals = sorted(set(found))
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2:
        lo, hi = vals
        if (lo, hi) in ((0, 2), (2, 4), (4, 6), (6, 8)):
            return (lo + hi) // 2
    return vals[-1]


def parse_raw(raw: str) -> dict:
    """Parse one raw report. Returns code (None if no turbulence group),
    the group text, the flight level of the report (/FL), the turbulence
    layer's middle flight level if the group gives altitudes, and /TM hhmm."""
    out = {"code": None, "tb_text": None, "fl": None, "tb_fl": None, "tm": None}
    if not raw:
        return out
    up = raw.upper()
    m = _FL.search(up)
    if m:
        out["fl"] = int(m.group(1))
    m = _TM.search(up)
    if m:
        out["tm"] = m.group(1)
    m = _TB_GROUP.search(up)
    is_airep = re.match(r"^\s*ARP\b", up) is not None or " ARP " in up[:40]
    if is_airep:  # automated AIREP: "... F390 160/043KT TB CONT SMTH IC"
        fm = _ARP_FL.search(up)
        if fm:
            out["fl"] = int(fm.group(1))
        m = _ARP_TB.search(up)
    if m:
        text = m.group(1).strip()
        out["tb_text"] = text
        out["code"] = intensity_from_group(text)
        alts = [int(a) for a in _ALT.findall(text)]
        alts = [a for a in alts if 0 <= a <= 600]
        if alts:
            out["tb_fl"] = int(round((min(alts) + max(alts)) / 2))
    return out


def label(code) -> float:
    """1 = moderate or greater, 0 = light or less, nan = no report / light-moderate."""
    if code is None or (isinstance(code, float) and np.isnan(code)):
        return float("nan")
    if code >= 4:
        return 1.0
    if code <= 2:
        return 0.0
    return float("nan")


COLUMNS = ["time", "lat", "lon", "fl", "code", "raw", "source"]


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=t) for c, t in
                             zip(COLUMNS, ["datetime64[ns, UTC]", float, float, float, float, str, str])})
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for c in ("lat", "lon", "fl", "code"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _effective_fl(parsed: dict, source_fl) -> float:
    if parsed["tb_fl"] is not None:
        return float(parsed["tb_fl"])
    if parsed["fl"] is not None:
        return float(parsed["fl"])
    if source_fl is not None and source_fl == source_fl:
        return float(source_fl)
    return float("nan")


# ------------------------------------------------------------- IEM archive
_IEM_HEAD = ["VALID", "URGENT", "AIRCRAFT"]
_IEM_TAIL = ["ICING", "TURBULENCE", "ATRCC", "PRODUCT_ID", "FL", "LAT", "LON"]


def _iem_records(text: str):
    """The IEM CSV is not quoted: report text may contain quote marks and
    (rarely) commas. Split each line into 3 leading fields, 7 trailing fields
    and the report text in between."""
    reader = csv.reader(io.StringIO(text), quoting=csv.QUOTE_NONE)
    header = next(reader, None)
    if not header:
        return
    for parts in reader:
        if len(parts) < 11:
            continue
        rec = dict(zip(_IEM_HEAD, parts[:3]))
        rec.update(zip(_IEM_TAIL, parts[-7:]))
        rec["REPORT"] = ",".join(parts[3:-7])
        yield rec


def parse_iem_csv(text: str) -> pd.DataFrame:
    rows = []
    for rec in _iem_records(text):
        raw = rec.get("REPORT") or ""
        p = parse_raw(raw)
        if p["code"] is None:
            continue
        try:
            t = datetime.strptime(rec["VALID"], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            lat, lon = float(rec["LAT"]), float(rec["LON"])
        except (KeyError, ValueError, TypeError):
            continue
        fl_ft = rec.get("FL")
        src_fl = float(fl_ft) / 100.0 if fl_ft not in (None, "", "None") else None
        rows.append({"time": t, "lat": lat, "lon": lon, "fl": _effective_fl(p, src_fl),
                     "code": p["code"], "raw": raw, "source": "iem"})
    return _rows_to_frame(rows)


def fetch_iem(start: datetime, end: datetime, log=print) -> str:
    r = net.get("https://mesonet.agron.iastate.edu/cgi-bin/request/gis/pireps.py",
                params={"fmt": "csv", "artcc": "_ALL",
                        "sts": start.strftime("%Y-%m-%dT%H:%MZ"),
                        "ets": end.strftime("%Y-%m-%dT%H:%MZ")},
                min_gap=2.0, log=log)
    return r.text


def load_iem_days(days: int, now: datetime, log=print) -> pd.DataFrame:
    """Turbulence reports for the last `days` UTC days up to `now`. Whole past
    days are cached under cache/turb/iem/; the current day is always refetched."""
    cache = net.CACHE / "iem"
    cache.mkdir(parents=True, exist_ok=True)
    day0 = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    frames = []
    d = day0
    while d < now:
        d_end = d + timedelta(days=1)
        path = cache / f"{d:%Y-%m-%d}.csv"
        if path.exists():
            text = path.read_text()
        else:
            log(f"  IEM reports {d:%Y-%m-%d}")
            text = fetch_iem(d, min(d_end, now), log=log)
            if d_end <= now - timedelta(hours=3):  # archive settled; keep it
                path.write_text(text)
        frames.append(parse_iem_csv(text))
        d = d_end
    df = pd.concat(frames, ignore_index=True) if frames else _rows_to_frame([])
    return dedupe(df[df["time"] <= pd.Timestamp(now)])


# ---------------------------------------------------------------- AWC live
AWC_BOXES = [(20, -130, 37.5, -95), (20, -95, 37.5, -60), (37.5, -130, 55, -95), (37.5, -95, 55, -60)]


def parse_awc_json(items: list[dict]) -> pd.DataFrame:
    rows = []
    for x in items:
        raw = x.get("rawOb") or ""
        p = parse_raw(raw)
        if p["code"] is None or x.get("lat") is None or x.get("lon") is None:
            continue
        t = datetime.fromtimestamp(int(x["obsTime"]), tz=timezone.utc)
        rows.append({"time": t, "lat": float(x["lat"]), "lon": float(x["lon"]),
                     "fl": _effective_fl(p, x.get("fltLvl")), "code": p["code"],
                     "raw": raw, "source": "awc"})
    return _rows_to_frame(rows)


def fetch_awc(age_hours: int = 2, log=print) -> pd.DataFrame:
    """Recent reports from the Aviation Weather Center. The API returns at most
    400 reports per call, so the box is split into four."""
    frames = []
    for (la0, lo0, la1, lo1) in AWC_BOXES:
        r = net.get("https://aviationweather.gov/api/data/pirep",
                    params={"format": "json", "age": age_hours, "bbox": f"{la0},{lo0},{la1},{lo1}"},
                    min_gap=1.0, log=log)
        items = r.json() if r.text.strip() else []
        if len(items) >= 400:
            log(f"  warning: AWC box {la0},{lo0},{la1},{lo1} hit the 400-report cap")
        frames.append(parse_awc_json(items))
    return dedupe(pd.concat(frames, ignore_index=True))


# ------------------------------------------------------------------- store
def report_key(raw: str) -> str:
    """The report body from /OV on, without spaces. The same report reaches us
    with different bulletin headers (and sometimes positions decoded
    differently) from the two sources; this key matches them."""
    up = str(raw).upper()
    i = up.find("/OV")
    return re.sub(r"\s+", "", up[i:] if i >= 0 else up)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """One row per report (first copy kept). Two rows are the same report if they
    share the minute and the report body, or the minute, position (0.1 degree),
    level and code (corrected re-transmissions)."""
    if df.empty:
        return df.reset_index(drop=True)
    t = df["time"].dt.floor("min")
    body = pd.DataFrame({"t": t, "r": df["raw"].map(report_key)})
    place = pd.DataFrame({"t": t, "la": df["lat"].round(1), "lo": df["lon"].round(1),
                          "fl": df["fl"].round(0), "c": df["code"]})
    keep = ~(body.duplicated() | place.duplicated())
    return df[keep].sort_values("time", kind="mergesort").reset_index(drop=True)


def in_domain(df: pd.DataFrame) -> pd.DataFrame:
    m = df["lat"].between(LAT_MIN, LAT_MAX) & df["lon"].between(LON_MIN, LON_MAX)
    return df[m]


def upper(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["fl"] >= UPPER_FL]


LIVE_STORE = net.CACHE / "pireps_live.csv.gz"


def read_store(path: Path = LIVE_STORE) -> pd.DataFrame:
    if not Path(path).exists():
        return _rows_to_frame([])
    df = pd.read_csv(path, keep_default_na=False, na_values=[""])
    df["raw"] = df["raw"].astype(str)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df[COLUMNS]


def merge_into_store(new: pd.DataFrame, path: Path = LIVE_STORE) -> pd.DataFrame:
    df = dedupe(pd.concat([read_store(path), new], ignore_index=True))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, compression="gzip")
    return df
