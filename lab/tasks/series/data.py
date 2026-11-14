"""Read a table of timestamped values and put every series on one regular time grid.

A "series" is one distinct combination of the id columns (for example GPU model +
provider + region); with no id columns the whole table is one series called "all".

The grid: times `origin + k * step`, k = 0, 1, 2, ... The value at grid time t is
the mean of the observations in the interval (t - step, t]. That interval ends AT t,
so the value at t is fully known at time t (an observation made exactly at t counts
for t). Steps with no observation are NaN. The grid is never filled: it holds only
real observations, so truths are always observed values. Any forward-filling of
history is done per case, causally, in cases.py (`max_fill_steps`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

NICE_STEPS = tuple(pd.Timedelta(x) for x in (
    "1min", "2min", "5min", "10min", "15min", "20min", "30min", "1h", "2h", "3h", "4h", "6h",
    "8h", "12h", "1D", "7D"))
SNAP_TOLERANCE = 0.10      # a median spacing within 10% of a round step is snapped to it


# ------------------------------------------------------------------ reading
def read_table(path: str | Path) -> pd.DataFrame:
    """CSV (comma, semicolon or tab separated; leading '#' comment lines skipped) or Parquet."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"data file not found: {p}")
    if p.suffix.lower() in (".parquet", ".pq"):
        try:
            return pd.read_parquet(p)
        except ImportError as e:
            raise RuntimeError("reading Parquet needs pyarrow, which is not installed here: run "
                               "`.venv/bin/python -m pip install pyarrow`, or convert the file to CSV") from e
    skip, header = 0, ""
    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                skip += 1
                continue
            header = line
            break
    sep = max([",", ";", "\t"], key=header.count) if header else ","
    return pd.read_csv(p, skiprows=skip, sep=sep)


def parse_times(col: pd.Series) -> pd.Series:
    """UTC timestamps. Numbers are read as Unix time (seconds, ms, us or ns by size);
    text without a time zone is read as UTC."""
    if pd.api.types.is_numeric_dtype(col):
        v = pd.to_numeric(col, errors="coerce")
        big = float(np.nanmax(np.abs(v.to_numpy(float)))) if v.notna().any() else 0.0
        unit = "s" if big < 1e11 else "ms" if big < 1e14 else "us" if big < 1e17 else "ns"
        return pd.to_datetime(v, unit=unit, utc=True)
    try:
        return pd.to_datetime(col, utc=True, errors="coerce")
    except (ValueError, TypeError):
        return pd.to_datetime(col, utc=True, errors="coerce", format="mixed")


def parse_numbers(col: pd.Series) -> pd.Series:
    """Floats. Text such as "$1,234.50" or "1.2 USD" is cleaned first; anything else -> NaN."""
    if pd.api.types.is_bool_dtype(col):
        return col.astype(float)
    if pd.api.types.is_numeric_dtype(col):
        return pd.to_numeric(col, errors="coerce").astype(float)
    s = col.astype(str).str.replace(r"[\s$,€£]|USD|usd", "", regex=True)
    s = s.replace({"True": "1", "False": "0", "true": "1", "false": "0"})
    return pd.to_numeric(s, errors="coerce").astype(float)


def _ns(t: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    return pd.DatetimeIndex(t).as_unit("ns").asi8


def infer_step(times_ns: Sequence[np.ndarray]) -> tuple[pd.Timedelta, str]:
    """Median spacing between consecutive distinct timestamps within each series,
    snapped to a round step when within 10%. Returns (step, explanation)."""
    diffs = [np.diff(np.unique(t)) for t in times_ns if len(np.unique(t)) > 1]
    diffs = np.concatenate(diffs) if diffs else np.array([], dtype=np.int64)
    diffs = diffs[diffs > 0]
    if diffs.size == 0:
        raise ValueError("cannot infer the step: no series has two distinct timestamps; pass freq")
    med = pd.Timedelta(int(np.median(diffs)), unit="ns")
    best = min(NICE_STEPS, key=lambda s: abs(s - med) / s)
    if abs(best - med) / best <= SNAP_TOLERANCE:
        step = best
        how = f"median spacing {med.to_pytimedelta()}, snapped to {fmt_step(best)}"
    else:
        step = pd.Timedelta(seconds=round(med.total_seconds()))
        how = f"median spacing {med.to_pytimedelta()} (no round step within 10%, used as is)"
    share_regular = float(np.mean(np.abs(diffs - step.value) <= 0.01 * step.value))
    return step, f"{how}; {share_regular:.0%} of spacings equal the step"


def fmt_step(step: pd.Timedelta) -> str:
    s = step.total_seconds()
    for unit, sec in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= sec and s % sec == 0:
            n = int(s // sec)
            return f"{n} {unit}" + ("s" if n != 1 else "")
    return f"{s:g} seconds"


# --------------------------------------------------------------------- grid
@dataclass
class Grid:
    origin: pd.Timestamp                  # time of index 0 (UTC)
    step: pd.Timedelta
    series: list[str]                     # series ids, sorted
    ids: list[dict[str, Any]]             # id column values per series
    y: np.ndarray                         # (n_series, n_t) value (log value if log_target)
    cov: dict[str, np.ndarray] = field(default_factory=dict)     # covariate -> (n_series, n_t)
    known: dict[str, np.ndarray] = field(default_factory=dict)   # known-future column -> (n_series, n_t)
    log_target: bool = False
    n_rows: int = 0                       # rows read (before bucketing)

    @property
    def n_t(self) -> int:
        return int(self.y.shape[1])

    @property
    def step_hours(self) -> float:
        return self.step.total_seconds() / 3600.0

    @property
    def end_index(self) -> int:
        """Last grid index at which any series has a value (the end of the data)."""
        has = np.isfinite(self.y).any(axis=0)
        return int(np.flatnonzero(has)[-1]) if has.any() else -1

    def time(self, k: int) -> datetime:
        return (self.origin + int(k) * self.step).to_pydatetime()

    def index_of(self, t) -> float:
        """Grid position of time t (a float; integer when t is on the grid)."""
        return (pd.Timestamp(t) - self.origin) / self.step

    def raw(self, a: np.ndarray) -> np.ndarray:
        """Values on the original scale (undo the log when log_target)."""
        return np.exp(a) if self.log_target else a


def series_ids(df: pd.DataFrame, id_cols: Sequence[str]) -> pd.Series:
    if not id_cols:
        return pd.Series("all", index=df.index)
    parts = [df[c].astype(str).str.strip() for c in id_cols]
    out = parts[0]
    for p in parts[1:]:
        out = out + "|" + p
    return out


def build_grid(df: pd.DataFrame, *, time_col: str, value_col: str, id_cols: Sequence[str] = (),
               covariate_cols: Sequence[str] = (), known_future_cols: Sequence[str] = (),
               step: pd.Timedelta | None = None, origin: pd.Timestamp | None = None,
               log_target: bool = False, notes: list[str] | None = None) -> Grid:
    notes = notes if notes is not None else []
    need = [time_col, value_col, *id_cols, *covariate_cols, *known_future_cols]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise KeyError(f"columns not in the file: {missing}; the file has {list(df.columns)}")
    t = parse_times(df[time_col])
    bad_t = int(t.isna().sum())
    if bad_t:
        notes.append(f"{bad_t} rows with an unreadable timestamp were dropped")
    d = pd.DataFrame({"t": t, "sid": series_ids(df, id_cols)})
    d["y"] = parse_numbers(df[value_col])
    bad_v = int((d["y"].isna() & df[value_col].notna()).sum())
    if bad_v:
        notes.append(f"{bad_v} values in `{value_col}` were not numbers and were treated as missing")
    for c in covariate_cols:
        d["c:" + c] = parse_numbers(df[c])
    for c in known_future_cols:
        d["k:" + c] = parse_numbers(df[c])
    for c in id_cols:
        d["id:" + c] = df[c]
    d = d[d["t"].notna()]
    if d.empty:
        raise ValueError("no rows with a readable timestamp")
    if log_target:
        nonpos = int((d["y"] <= 0).sum())
        if nonpos:
            notes.append(f"log_target: {nonpos} values <= 0 were treated as missing")
        d.loc[d["y"] <= 0, "y"] = np.nan
        d["y"] = np.log(d["y"])

    t_ns = _ns(d["t"])
    sids = sorted(d["sid"].unique().tolist())
    code = {s: k for k, s in enumerate(sids)}
    sc = d["sid"].map(code).to_numpy(int)
    if step is None:
        step, how = infer_step([t_ns[sc == k] for k in range(len(sids))])
        notes.append(f"step inferred from the data: {fmt_step(step)} ({how})")
    step = pd.Timedelta(step)
    if origin is None:
        origin = pd.Timestamp(int(t_ns.min()), unit="ns", tz="UTC")
    origin = pd.Timestamp(origin)
    origin = origin.tz_localize("UTC") if origin.tzinfo is None else origin.tz_convert("UTC")
    rel = t_ns - origin.as_unit("ns").value
    k = -(-rel // step.value)                # ceil: value at grid time t covers (t - step, t]
    keep = k >= 0
    if not keep.all():
        notes.append(f"{int((~keep).sum())} rows before the grid origin were dropped")
    d, k, sc = d[keep], k[keep], sc[keep]
    n_t = int(k.max()) + 1
    d = d.assign(k=k, s=sc)
    agg_cols = ["y"] + [c for c in d.columns if c.startswith(("c:", "k:"))]
    g = d.groupby(["s", "k"])[agg_cols].mean()
    dup = int(len(d) - len(g))
    if dup:
        notes.append(f"{dup} rows shared a grid step with another row of the same series and were averaged")
    si = g.index.get_level_values(0).to_numpy(int)
    ki = g.index.get_level_values(1).to_numpy(int)

    def scatter(col: str) -> np.ndarray:
        a = np.full((len(sids), n_t), np.nan)
        a[si, ki] = g[col].to_numpy(float)
        return a

    y = scatter("y")
    cov = {c: scatter("c:" + c) for c in covariate_cols}
    known = {c: scatter("k:" + c) for c in known_future_cols}
    first = d.drop_duplicates("sid").set_index("sid")
    ids = [{c: _plain(first.loc[s, "id:" + c]) for c in id_cols} for s in sids]
    return Grid(origin, step, sids, ids, y, cov, known, log_target, n_rows=len(df))


def _plain(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)


# ------------------------------------------------------------------ summary
def md_cell(text: str) -> str:
    """Escape the vertical bar so a series id fits in a markdown table cell."""
    return str(text).replace("|", "\\|")


def data_summary(grid: Grid, value_col: str, notes: Sequence[str] = ()) -> str:
    """Markdown: what the data looks like (computed from the data, nothing assumed)."""
    end = grid.end_index
    y = grid.raw(grid.y[:, :end + 1])
    vals = y[np.isfinite(y)]
    present = np.isfinite(y)
    first = np.array([np.flatnonzero(r)[0] if r.any() else end + 1 for r in present])
    last = np.array([np.flatnonzero(r)[-1] if r.any() else -1 for r in present])
    span_cells = np.clip(last - first + 1, 0, None).sum()
    miss_inside = 1 - present.sum() / span_cells if span_cells else float("nan")
    miss_grid = 1 - present.mean() if present.size else float("nan")
    with np.errstate(all="ignore"):
        mean = np.array([np.nanmean(r) if np.isfinite(r).any() else np.nan for r in y])
        std = np.array([np.nanstd(r) if np.isfinite(r).sum() > 1 else np.nan for r in y])
        cv = std / np.abs(mean)
    order = [i for i in np.argsort(-np.nan_to_num(cv, nan=-1.0)) if np.isfinite(cv[i])]

    def row(i):
        return (f"| {md_cell(grid.series[i])} | {mean[i]:.4g} | {std[i]:.4g} | {cv[i]:.3f} | "
                f"{int(present[i].sum())} |")

    head = "| series | mean | std | std / mean | values |\n|---|---|---|---|---|"
    most = "\n".join(row(i) for i in order[:5])
    least = "\n".join(row(i) for i in order[::-1][:5])
    lines = [
        f"- series: {len(grid.series)}" + (f" (ids like `{grid.series[0]}`)" if grid.series else ""),
        f"- span: {grid.time(0):%Y-%m-%d %H:%M} to {grid.time(end):%Y-%m-%d %H:%M} UTC "
        f"({(grid.time(end) - grid.time(0)).total_seconds() / 86400:.1f} days, {end + 1} steps)",
        f"- step: {fmt_step(grid.step)}; rows read: {grid.n_rows}",
        f"- missing: {miss_inside:.1%} of steps between each series' first and last value "
        f"({miss_grid:.1%} of the whole grid, which also counts series that start late or end early)",
        f"- `{value_col}`: min {vals.min():.4g}, median {np.median(vals):.4g}, max {vals.max():.4g}"
        if vals.size else f"- `{value_col}`: no values",
    ]
    lines += [f"- note: {n}" for n in notes]
    return ("\n".join(lines) + "\n\nMost variation (std / mean of the series' values):\n\n" + head + "\n"
            + most + "\n\nLeast variation:\n\n" + head + "\n" + least + "\n")
