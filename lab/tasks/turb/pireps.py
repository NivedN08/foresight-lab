"""Pilot reports (PIREPs): parse the turbulence group and fetch reports.

Intensity codes (the turbulence group, `/TB ...`):
    NEG / SMOOTH = 0, light = 2, light-moderate = 3, moderate = 4,
    moderate-severe = 5, severe = 6, severe-extreme = 7, extreme = 8.

A group can describe several layers ("LGT 240-260 MOD 300-340",
"LGT OCNL MOD 370 NEG 390"). Each layer becomes its own labelled point:
    * intensity: the words of that layer; two adjacent intensities in one layer
      ("LGT-MOD", "LGT -MOD", "CONS LGT OCNL MOD", "MOD CHOP OCNL LGT",
      "LIGHT TO MOD") give the in-between code, as the Aviation Weather Center
      does (NEG with light gives 1, still light or less);
    * altitude: the middle of the layer's range, or its single level; with only
      a bound ("BLO 230", "ABV 060") the aircraft's own flight level, if it
      satisfies the bound, else the layer is dropped; with nothing, the
      aircraft's flight level. A bound never moves a report to another level.

Classes used by the forecasters: positive = code >= 4 (moderate or greater),
negative = code <= 2 (light or less); code 3 (light-moderate) is dropped as
ambiguous. Standard reports are read from their slashed `/TB` group (which
runs to the next known group such as /IC or /RM); automated airline AIREPs
("ARP UAL286 ... F390 ... TB CONT SMTH IC") from the text between "TB" and
the icing group "IC" (an empty group, as in "TB IC MOD RIME", has no
turbulence intensity).

Positions come from the data sources (both decode the /OV group); this module
does not decode navaid radials itself.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from lab.core.timeutil import from_iso, to_iso

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
_BELOW = {"BLO", "BLW", "BELOW", "BLWO"}
_ABOVE = {"ABV", "ABOVE", "AOA"}
_GROUP_IDS = "OV|TM|FL|TP|SK|WX|TA|WV|TB|IC|RM"
_TB_GROUP = re.compile(r"/\s*TB\b(.*?)(?=/\s*(?:" + _GROUP_IDS + r")\b|$)")
_FL = re.compile(r"/\s*FL\s*(\d{2,3})\b")
_TM = re.compile(r"/\s*TM\s*(\d{4})\b")
_TOKEN = re.compile(r"FL\d{3}|\d+|[A-Z]+|-")
_ARP_TB = re.compile(r"\bTB\b(.*?)(?=\bIC\b|$)")
_ARP_FL = re.compile(r"\bF(\d{3})\b")


def combine_intensities(found: list[int]) -> int | None:
    """One layer's code from the intensity words it names."""
    if not found:
        return None
    vals = sorted(set(found))
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2 and vals in ([0, 2], [2, 4], [4, 6], [6, 8]):
        return (vals[0] + vals[1]) // 2
    return vals[-1]


def _altitude(tok: str) -> int | None:
    if tok == "SFC":
        return 0
    m = re.fullmatch(r"(?:FL)?(\d{3})", tok)
    if m and int(m.group(1)) <= 600:
        return int(m.group(1))
    return None


def split_layers(text: str) -> list[dict]:
    """Layers of one turbulence group: [{"code", "alts", "bound"}].
    A new layer starts when an intensity word, or a bound, follows a layer that
    already has an intensity and an altitude or bound."""
    tokens = _TOKEN.findall(text.upper().replace("-", " - "))
    layers: list[dict] = []
    cur = {"ints": [], "alts": [], "bound": None}
    pending = None
    for tok in tokens:
        done = bool(cur["ints"]) and bool(cur["alts"] or cur["bound"])
        if tok in _WORDS:
            if done:
                layers.append(cur)
                cur = {"ints": [], "alts": [], "bound": None}
            cur["ints"].append(_WORDS[tok])
        elif tok in _BELOW or tok in _ABOVE:
            if done:  # "MOD 280 BLO 275 NEG": the bound opens the next layer
                layers.append(cur)
                cur = {"ints": [], "alts": [], "bound": None}
            pending = "below" if tok in _BELOW else "above"
        else:
            alt = _altitude(tok)
            if alt is None:
                continue
            if pending:
                cur["bound"] = (pending, alt)
                pending = None
            else:
                cur["alts"].append(alt)
    layers.append(cur)
    return [{"code": combine_intensities(l["ints"]), "alts": l["alts"], "bound": l["bound"]}
            for l in layers if l["ints"]]


def layer_level(layer: dict, aircraft_fl) -> int | None:
    """Flight level a layer's label applies to (None if unknown)."""
    if layer["alts"]:
        return int(math.floor((min(layer["alts"]) + max(layer["alts"])) / 2 + 0.5))
    if aircraft_fl is None:
        return None
    if layer["bound"]:
        kind, v = layer["bound"]
        ok = aircraft_fl <= v if kind == "below" else aircraft_fl >= v
        return int(aircraft_fl) if ok else None
    return int(aircraft_fl)


def intensity_from_group(text: str) -> int | None:
    """Worst code over a group's layers (a summary; labels use the layers)."""
    codes = [l["code"] for l in split_layers(text)]
    return max(codes) if codes else None


def parse_raw(raw: str, source_fl=None) -> dict:
    """Parse one raw report: the aircraft's flight level (/FL, or "F390" in an
    AIREP, else `source_fl`), /TM hhmm, the turbulence group text, and the
    labelled layers [{"code", "fl"}] (layers with an unknown level are left out)."""
    out = {"fl": None, "tm": None, "tb_text": None, "layers": []}
    if not raw:
        return out
    up = str(raw).upper()
    m = _FL.search(up)
    if m:
        out["fl"] = int(m.group(1))
    m = _TM.search(up)
    if m:
        out["tm"] = m.group(1)
    m = _TB_GROUP.search(up)
    if re.match(r"^\s*ARP\b", up) is not None or " ARP " in up[:40]:
        fm = _ARP_FL.search(up)  # automated AIREP: "... F390 160/043KT TB CONT SMTH IC"
        if fm:
            out["fl"] = int(fm.group(1))
        m = _ARP_TB.search(up)
    if out["fl"] is None and source_fl is not None and source_fl == source_fl:
        out["fl"] = int(round(float(source_fl)))
    if m:
        out["tb_text"] = m.group(1).strip()
        for layer in split_layers(out["tb_text"]):
            level = layer_level(layer, out["fl"])
            if level is not None:
                out["layers"].append({"code": layer["code"], "fl": level})
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


# One row per report as received (what the live store keeps) ...
REPORT_COLUMNS = ["time", "lat", "lon", "ac_fl", "raw", "source"]
# ... and one row per labelled layer (what models and grading use).
COLUMNS = ["time", "lat", "lon", "fl", "code", "raw", "source", "layer"]
_DTYPES = {"time": "datetime64[ns, UTC]", "lat": float, "lon": float, "fl": float, "code": float,
           "raw": str, "source": str, "layer": int, "ac_fl": float}


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=_DTYPES[c]) for c in columns})
    df = pd.DataFrame(rows, columns=columns)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for c in ("lat", "lon", "fl", "code", "ac_fl"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    return _frame(rows, COLUMNS)


def expand_layers(reports: pd.DataFrame) -> pd.DataFrame:
    """Report rows -> one row per labelled layer."""
    rows = []
    for t, la, lo, acf, raw, src in zip(reports["time"], reports["lat"], reports["lon"],
                                        reports["ac_fl"], reports["raw"], reports["source"]):
        p = parse_raw(raw, acf)
        for i, layer in enumerate(p["layers"]):
            rows.append({"time": t, "lat": la, "lon": lo, "fl": float(layer["fl"]), "code": layer["code"],
                         "raw": raw, "source": src, "layer": i})
    return _rows_to_frame(rows)


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


def iem_reports(text: str) -> pd.DataFrame:
    """Report rows (one per report) from an IEM CSV response."""
    rows = []
    for rec in _iem_records(text):
        raw = rec.get("REPORT") or ""
        try:
            t = datetime.strptime(rec["VALID"], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            lat, lon = float(rec["LAT"]), float(rec["LON"])
        except (KeyError, ValueError, TypeError):
            continue
        fl_ft = rec.get("FL")
        ac_fl = float(fl_ft) / 100.0 if fl_ft not in (None, "", "None") else float("nan")
        rows.append({"time": t, "lat": lat, "lon": lon, "ac_fl": ac_fl, "raw": raw, "source": "iem"})
    return _frame(rows, REPORT_COLUMNS)


def parse_iem_csv(text: str) -> pd.DataFrame:
    """Labelled layer rows from an IEM CSV response."""
    return expand_layers(iem_reports(text))


def fetch_iem(start: datetime, end: datetime, log=print) -> str:
    r = net.get("https://mesonet.agron.iastate.edu/cgi-bin/request/gis/pireps.py",
                params={"fmt": "csv", "artcc": "_ALL",
                        "sts": start.strftime("%Y-%m-%dT%H:%MZ"),
                        "ets": end.strftime("%Y-%m-%dT%H:%MZ")},
                min_gap=2.0, log=log)
    return r.text


IEM_SETTLED = timedelta(hours=24)  # a day fetched this long after it ended is kept for good


def _has_reports(text: str) -> bool:
    return len(text.strip().splitlines()) > 1  # more than the header


def iem_day_text(d: datetime, now: datetime, log=print) -> str:
    """The IEM archive CSV for the UTC day starting at `d`, up to `now`. A day
    is cached under cache/turb/iem/ for good only if it was fetched at least
    IEM_SETTLED after it ended and holds reports; otherwise it is fetched again
    the next time it is needed. A cache file counts only if its modification
    time (when it was written) meets the same rule."""
    cache = net.CACHE / "iem"
    cache.mkdir(parents=True, exist_ok=True)
    d_end = d + timedelta(days=1)
    path = cache / f"{d:%Y-%m-%d}.csv"
    if path.exists():
        written = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        text = path.read_text()
        if written >= d_end + IEM_SETTLED and _has_reports(text):
            return text
    log(f"  IEM reports {d:%Y-%m-%d}")
    text = fetch_iem(d, min(d_end, now), log=log)
    if now >= d_end + IEM_SETTLED and _has_reports(text):  # archive settled; keep it
        path.write_text(text)
    return text


def load_iem_days(days: int, now: datetime, log=print) -> pd.DataFrame:
    """Turbulence reports for the last `days` UTC days up to `now`. Whole past
    days are cached under cache/turb/iem/; the current day is always refetched."""
    day0 = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    frames = []
    d = day0
    while d < now:
        frames.append(parse_iem_csv(iem_day_text(d, now, log=log)))
        d = d + timedelta(days=1)
    df = pd.concat(frames, ignore_index=True) if frames else _rows_to_frame([])
    return dedupe(df[df["time"] <= pd.Timestamp(now)])


# ---------------------------------------------------------------- AWC live
AWC_BOXES = [(20, -130, 37.5, -95), (20, -95, 37.5, -60), (37.5, -130, 55, -95), (37.5, -95, 55, -60)]


def awc_reports(items: list[dict]) -> pd.DataFrame:
    rows = []
    for x in items:
        if x.get("lat") is None or x.get("lon") is None or x.get("obsTime") is None:
            continue
        acf = x.get("fltLvl")
        rows.append({"time": datetime.fromtimestamp(int(x["obsTime"]), tz=timezone.utc),
                     "lat": float(x["lat"]), "lon": float(x["lon"]),
                     "ac_fl": float(acf) if acf is not None else float("nan"),
                     "raw": x.get("rawOb") or "", "source": "awc"})
    return _frame(rows, REPORT_COLUMNS)


def parse_awc_json(items: list[dict]) -> pd.DataFrame:
    return expand_layers(awc_reports(items))


def save_raw(kind: str, text: str, now: datetime, suffix: str = "", ext: str = "json") -> Path:
    """Keep every API response as received, so truth can be re-derived later."""
    d = net.CACHE / "raw" / kind
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{now:%Y%m%dT%H%M%SZ}{suffix}.{ext}"
    path.write_text(text)
    return path


def fetch_awc_reports(now: datetime, age_hours: int = 2, log=print) -> pd.DataFrame:
    """Recent report rows from the Aviation Weather Center. The API returns at
    most 400 reports per call, so the box is split into four."""
    frames = []
    for k, (la0, lo0, la1, lo1) in enumerate(AWC_BOXES):
        r = net.get("https://aviationweather.gov/api/data/pirep",
                    params={"format": "json", "age": age_hours, "bbox": f"{la0},{lo0},{la1},{lo1}"},
                    min_gap=1.0, log=log)
        save_raw("awc", r.text, now, suffix=f"_box{k}")
        items = r.json() if r.text.strip() else []
        if len(items) >= 400:
            log(f"  warning: AWC box {la0},{lo0},{la1},{lo1} hit the 400-report cap")
        frames.append(awc_reports(items))
    return pd.concat(frames, ignore_index=True)


def fetch_iem_reports(now: datetime, hours: int = 6, log=print) -> pd.DataFrame:
    text = fetch_iem(now - timedelta(hours=hours), now, log=log)
    save_raw("iem", text, now, ext="csv")
    return iem_reports(text)


# ------------------------------------------------------------------- store
def report_key(raw: str) -> str:
    """The report body from /OV on, without spaces. The same report reaches us
    with different bulletin headers (and sometimes positions decoded
    differently) from the two sources; this key matches them."""
    up = str(raw).upper()
    i = up.find("/OV")
    return re.sub(r"\s+", "", up[i:] if i >= 0 else up)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """One row per labelled layer (first copy kept). Two rows are the same if
    they share the minute, the report body and the layer, or the minute,
    position (0.1 degree), level and code (corrected re-transmissions)."""
    if df.empty:
        return df.reset_index(drop=True)
    t = df["time"].dt.floor("min")
    layer = df["layer"] if "layer" in df else 0
    body = pd.DataFrame({"t": t, "r": df["raw"].map(report_key), "l": layer})
    place = pd.DataFrame({"t": t, "la": df["lat"].round(1), "lo": df["lon"].round(1),
                          "fl": df["fl"].round(0), "c": df["code"]})
    keep = ~(body.duplicated() | place.duplicated())
    return df[keep].sort_values("time", kind="mergesort").reset_index(drop=True)


def in_domain(df: pd.DataFrame) -> pd.DataFrame:
    m = df["lat"].between(LAT_MIN, LAT_MAX) & df["lon"].between(LON_MIN, LON_MAX)
    return df[m]


def upper(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["fl"] >= UPPER_FL]


# The live store is append-only: rows are only ever added, never rewritten.
LIVE_STORE = net.CACHE / "pireps_live_reports.csv"
LEGACY_STORE = net.CACHE / "pireps_live.csv.gz"  # first version (rewritten on each merge); read only


def append_to_store(reports: pd.DataFrame, path: Path = LIVE_STORE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = reports[REPORT_COLUMNS].copy()
    out["time"] = out["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(path, mode="a", header=not path.exists(), index=False)


def _store_rows(path: Path, legacy: Path | None) -> pd.DataFrame:
    """Report rows of the store as written (one per report, duplicates kept)."""
    frames = []
    if legacy is not None and Path(legacy).exists():
        old = pd.read_csv(legacy, keep_default_na=False, na_values=[""])
        old["ac_fl"] = float("nan")  # the raw text carries the flight level
        frames.append(old)
    if Path(path).exists():
        frames.append(pd.read_csv(path, keep_default_na=False, na_values=[""]))
    if not frames:
        return _frame([], REPORT_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["raw"] = df["raw"].astype(str)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df[REPORT_COLUMNS]


def read_store(path: Path = LIVE_STORE, legacy: Path | None = LEGACY_STORE) -> pd.DataFrame:
    """Labelled layer rows from the store, re-parsed from the raw text and de-duplicated."""
    df = _store_rows(path, legacy)
    if df.empty:
        return _rows_to_frame([])
    return dedupe(expand_layers(df))


def _row_keys(df: pd.DataFrame) -> list[tuple]:
    return list(zip(df["time"].dt.floor("min"), df["raw"].astype(str).map(report_key)))


def append_new_to_store(reports: pd.DataFrame, path: Path = LIVE_STORE, legacy: Path | None = LEGACY_STORE) -> int:
    """Append only the reports the store does not hold yet (same minute and
    report body); returns how many were appended."""
    if reports.empty:
        return 0
    seen = set(_row_keys(_store_rows(path, legacy)))
    keep = []
    for k in _row_keys(reports):
        keep.append(k not in seen)
        seen.add(k)
    new = reports[keep]
    if len(new):
        append_to_store(new, path)
    return int(len(new))


# ---------------------------------------------------------------- coverage
# Next to the store, the time spans each source has delivered in full:
# {"source", "from", "to", "fetched_at"}. New spans are appended; once a cycle
# the file is rewritten with overlapping or adjacent spans of a source merged
# and spans older than any record can still need dropped.
# Records are graded only where the spans cover their whole truth window.
COVERAGE_SETTLE = timedelta(minutes=60)  # late reports: an hour counts only if fetched this long after it ended
@dataclass
class Fetched:
    """Report rows from one source and the time span the request covered."""
    source: str
    start: datetime
    end: datetime
    reports: pd.DataFrame  # REPORT_COLUMNS


def coverage_path(store: Path) -> Path:
    store = Path(store)
    return store.with_name(store.name + ".coverage.jsonl")


def covered_spans(start: datetime, end: datetime, times, fetched_at: datetime,
                  settle: timedelta = COVERAGE_SETTLE) -> list[tuple[datetime, datetime]]:
    """The parts of [start, end] a fetch made at `fetched_at` covers. A clock hour
    (or the part of it inside the span) counts only if the fetch was made at
    least `settle` after the hour ended, so late reports had time to arrive,
    and if the fetch returned at least one report in it: across the whole
    country an empty hour means the source had nothing yet, not calm air."""
    have = {t.to_pydatetime() for t in pd.to_datetime(pd.Series(times), utc=True).dt.floor("h")}
    spans: list[list[datetime]] = []
    t = start
    while t < end:
        h0 = t.replace(minute=0, second=0, microsecond=0)
        h1 = min(h0 + timedelta(hours=1), end)
        if h0 in have and fetched_at >= h0 + timedelta(hours=1) + settle:
            if spans and spans[-1][1] == t:
                spans[-1][1] = h1
            else:
                spans.append([t, h1])
        t = h1
    return [(a, b) for a, b in spans]


def record_coverage(store: Path, source: str, spans, fetched_at: datetime) -> None:
    if not spans:
        return
    with open(coverage_path(store), "a") as fh:
        for a, b in spans:
            fh.write(json.dumps({"source": source, "from": to_iso(a), "to": to_iso(b),
                                 "fetched_at": to_iso(fetched_at)}) + "\n")


def _merge(lines: list[dict]) -> list[dict]:
    """Per source, overlapping or adjacent spans merged (latest fetched_at kept), sorted."""
    out: list[dict] = []
    for c in sorted(lines, key=lambda c: (c["source"], from_iso(c["from"]))):
        last = out[-1] if out else None
        if last and last["source"] == c["source"] and from_iso(c["from"]) <= from_iso(last["to"]):
            if from_iso(c["to"]) > from_iso(last["to"]):
                last["to"] = c["to"]
            last["fetched_at"] = max(last["fetched_at"], c["fetched_at"])
        else:
            out.append(dict(c))
    return out


def read_coverage(store: Path) -> list[dict]:
    """The covered spans, merged per source."""
    p = coverage_path(store)
    if not p.exists():
        return []
    return _merge([json.loads(line) for line in p.read_text().splitlines() if line.strip()])


def compact_coverage(store: Path, keep_after: datetime | None = None) -> int:
    """Rewrite the coverage file with merged spans, dropping spans that ended
    before `keep_after` (atomically); returns the line count."""
    p = coverage_path(store)
    if not p.exists():
        return 0
    merged = [c for c in read_coverage(store) if keep_after is None or from_iso(c["to"]) >= keep_after]
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("".join(json.dumps(c) + "\n" for c in merged))
    tmp.replace(p)
    return len(merged)


def window_coverage(coverage: list[dict], start: datetime, end: datetime) -> tuple[bool, list[str]]:
    """(whether the spans together cover [start, end], the sources with spans in it)."""
    spans = sorted((from_iso(c["from"]), from_iso(c["to"]), c["source"]) for c in coverage)
    spans = [s for s in spans if s[1] >= start and s[0] <= end]
    t = start
    for a, b, _ in spans:
        if a > t:
            break
        t = max(t, b)
    return t >= end, sorted({s for a, b, s in spans if b > start and a < end})
