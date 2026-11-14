"""Aviation turbulence as a lab Problem (see lab/core/problem.py).

Question: given the upper-air fields at a pilot report's place, pressure and
hour, what is the probability that the report is moderate-or-greater
turbulence? One case per labelled report layer at flight level 200 and above,
over the cached 30 days (no downloads when the cache is warm). Loss: Brier
score (squared error of the probability) x 100.

What the inputs can and cannot know:

  * Inputs come only from the gridded fields (Open-Meteo's past values, the
    one backfill download in cache/turb/fields/) at the report's hour, never
    from any pilot report: labels are the only thing that comes from reports
    (besides the report's own time, place and level, which define the
    question).
  * Fields are taken at the start of the report's hour (no later hour is
    read). They are hindsight values: stitched from model runs started at or
    after that hour, which may have assimilated aircraft observations. So this
    problem measures how well a formula ranks turbulence given good fields; it
    is not a forecast test.

`tests/test_turb_problem.py` shuffles every label and corrupts every field hour
after a report's hour, and checks that the inputs do not change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pickle
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from lab.core.problem import Splits, Window
from lab.core.stats import auc
from lab.core.task import Case

from . import fields as F
from . import net
from . import pireps as P
from .diagnostics import DIAGNOSTICS, diagnose, horizontal_derivatives, layer_shear, wind_components
from .interp import fl_to_pressure

PROBLEM_CACHE = net.CACHE / "problem"
BUILD_VERSION = 1           # bump when the case layout changes; old caches are then ignored
FRACTIONS = (0.60, 0.15, 0.10, 0.15)   # train, select, confirm, test (of the time span)
WINDOWS = ("train", "select", "confirm", "test")
MAX_CASES = 12000           # a window larger than this is subsampled (with a fixed seed)
FL_BANDS = ((200, 270), (270, 330), (330, 380), (380, 999))
ADJ_KEYS = ("speed", "vws", "ri", "n2", "deform", "div", "ti1", "tgrad", "temp_c")


# ------------------------------------------------------------------- data
def cached_report_layers(start: datetime, end: datetime, iem_dir: Path | None = None) -> pd.DataFrame:
    """Labelled upper-level report layers in [start, end) from the cached IEM day
    files only (never downloads), inside the continental-US box."""
    iem_dir = Path(iem_dir or net.CACHE / "iem")
    frames = []
    d = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while d < end:
        path = iem_dir / f"{d:%Y-%m-%d}.csv"
        if path.exists():
            frames.append(P.parse_iem_csv(path.read_text()))
        d += timedelta(days=1)
    if not frames:
        return P._rows_to_frame([])
    df = P.dedupe(pd.concat(frames, ignore_index=True))
    df = P.upper(P.in_domain(df))
    df = df[(df["time"] >= pd.Timestamp(start)) & (df["time"] < pd.Timestamp(end))]
    return df[df["code"].map(P.label).notna()].reset_index(drop=True)


def cached_iem_end(iem_dir: Path | None = None) -> datetime | None:
    """End (exclusive) of the last cached IEM day."""
    names = sorted(p.stem for p in Path(iem_dir or net.CACHE / "iem").glob("*.csv"))
    if not names:
        return None
    return datetime.strptime(names[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)


def backfill_fields(store: F.FieldStore | None = None) -> F.FieldSet:
    """The one stored backfill download (fixed, so the problem is deterministic)."""
    store = store or F.FieldStore()
    paths = [p for p in store.paths() if p.stem.startswith("backfill_")]
    if not paths:
        raise FileNotFoundError("no backfill fields in cache/turb/fields; run the backtest once")
    return F.FieldSet.load(sorted(paths)[-1])


# ------------------------------------------------------------- the inputs
def horizontal(field: np.ndarray, lats, lons, lat, lon) -> np.ndarray:
    """Bilinear interpolation of a (K, lat, lon) stack to points: (n, K).
    Points outside the grid take the nearest edge value."""
    lats, lons = np.asarray(lats, float), np.asarray(lons, float)
    la = np.clip(np.asarray(lat, float), lats[0], lats[-1])
    lo = np.clip(np.asarray(lon, float), lons[0], lons[-1])
    i = np.clip(np.searchsorted(lats, la, side="right") - 1, 0, len(lats) - 2)
    j = np.clip(np.searchsorted(lons, lo, side="right") - 1, 0, len(lons) - 2)
    wy = (la - lats[i]) / (lats[i + 1] - lats[i])
    wx = (lo - lons[j]) / (lons[j + 1] - lons[j])
    f = np.asarray(field, float)
    out = ((1 - wy) * (1 - wx))[:, None] * f[:, i, j].T + ((1 - wy) * wx)[:, None] * f[:, i, j + 1].T \
        + (wy * (1 - wx))[:, None] * f[:, i + 1, j].T + (wy * wx)[:, None] * f[:, i + 1, j + 1].T
    return out


def bracket(levels: np.ndarray, p: np.ndarray):
    """For each pressure: index of the grid level above it (lower pressure), and
    the weight of the level below, linear in log pressure (clamped at the ends)."""
    logl = np.log(levels)
    lp = np.clip(np.log(p), logl[0], logl[-1])
    i = np.clip(np.searchsorted(logl, lp, side="right") - 1, 0, len(levels) - 2)
    w = (lp - logl[i]) / (logl[i + 1] - logl[i])
    return i, w


def _at(prof: np.ndarray, i: np.ndarray, w: np.ndarray) -> np.ndarray:
    r = np.arange(len(i))
    return prof[r, i] * (1 - w) + prof[r, i + 1] * w


def hour_features(fs1: F.FieldSet, lat, lon, p) -> dict[str, np.ndarray]:
    """Inputs for points at one field hour (fs1 holds exactly one time)."""
    if len(fs1.times) != 1:
        raise ValueError("hour_features needs a FieldSet with one time")
    levels = np.asarray(fs1.levels, float)
    diag = {k: v[0] for k, v in diagnose(fs1).items()}             # (L, Y, X) each
    u, v = (a[0] for a in wind_components(fs1.speed, fs1.direction))
    z, temp = fs1.height[0], fs1.temp[0]
    gx, gy = horizontal_derivatives(fs1.speed[0], fs1.lats, fs1.lons)
    shear_layers = layer_shear(u[1:], v[1:], z[1:], u[:-1], v[:-1], z[:-1]) * 1e3   # (L-1, Y, X), m/s per km
    H = lambda f: horizontal(f, fs1.lats, fs1.lons, lat, lon)
    prof = {k: H(diag[k]) for k in DIAGNOSTICS}
    prof.update(u=H(u), v=H(v), height_m=H(z), temp_c=H(temp), gx=H(gx * 1e5), gy=H(gy * 1e5))
    lay = H(shear_layers)                                            # (n, L-1)
    i, w = bracket(levels, p)
    n = len(i)
    out = {k: _at(prof[k], i, w) for k in (*DIAGNOSTICS, "u", "v", "height_m", "temp_c")}
    out["direction"] = np.mod(np.degrees(np.arctan2(-out["u"], -out["v"])), 360.0)
    out["speed_grad_x"] = _at(prof["gx"], i, w)
    out["speed_grad_y"] = _at(prof["gy"], i, w)
    out["speed_grad"] = np.hypot(out["speed_grad_x"], out["speed_grad_y"])
    r = np.arange(n)
    out["shear_layer"] = lay[r, i]
    out["shear_above"] = np.where(i >= 1, lay[r, np.maximum(i - 1, 0)], np.nan)
    out["shear_below"] = np.where(i + 1 <= lay.shape[1] - 1, lay[r, np.minimum(i + 1, lay.shape[1] - 1)], np.nan)
    out["above_hpa"] = levels[i]
    out["below_hpa"] = levels[i + 1]
    for k in ADJ_KEYS:
        out[f"above_{k}"] = prof[k][r, i]
        out[f"below_{k}"] = prof[k][r, i + 1]
    dirs = np.mod(np.degrees(np.arctan2(-prof["u"], -prof["v"])), 360.0)
    out["prof_speed"], out["prof_dir"] = prof["speed"], dirs
    out["prof_temp_c"], out["prof_height_m"] = prof["temp_c"], prof["height_m"]
    return out


PROFILE_KEYS = ("prof_speed", "prof_dir", "prof_temp_c", "prof_height_m")


def _r(x: float, nd: int = 5) -> float:
    return float(round(float(x), nd)) if math.isfinite(float(x)) else float("nan")


def build_cases(reports: pd.DataFrame, fs: F.FieldSet):
    """(cases, truths, skipped) for report layers, using only the field at each
    report's hour (the start of its hour)."""
    cases, truths, seen = [], [], {}
    skipped = 0
    if reports.empty:
        return cases, truths, 0
    rep = reports.sort_values("time", kind="mergesort")
    hours = rep["time"].dt.floor("h")
    index = {int(t): k for k, t in enumerate(fs.times)}
    for h_ts, grp in rep.groupby(hours, sort=True):
        h = h_ts.to_pydatetime()
        k = index.get(int(h.timestamp()))
        if k is None:
            skipped += len(grp)
            continue
        fs1 = fs.select(np.array([k]))
        p = fl_to_pressure(grp["fl"].to_numpy(float))
        feats = hour_features(fs1, grp["lat"].to_numpy(float), grp["lon"].to_numpy(float), p)
        for j, (_, row) in enumerate(grp.iterrows()):
            t = row["time"].to_pydatetime()
            minute = t.hour * 60 + t.minute
            inp = {"lat": float(row["lat"]), "lon": float(row["lon"]), "flight_level": float(row["fl"]),
                   "pressure_hpa": _r(p[j], 2), "hour_utc": int(t.hour),
                   "solar_hour": _r(((minute / 60.0) + float(row["lon"]) / 15.0) % 24.0, 3),
                   "doy": int(t.timetuple().tm_yday),
                   "field_age_min": int((t - h).total_seconds() // 60),
                   "levels_hpa": np.asarray(fs.levels, float)}
            for key, arr in feats.items():
                if key in PROFILE_KEYS:
                    inp[key] = np.round(arr[j], 4)
                else:
                    inp[key] = _r(arr[j])
            digest = hashlib.sha1(f"{row['raw']}|{row['source']}".encode()).hexdigest()[:8]
            cid = f"turb-{t:%Y%m%dT%H%M}-{row['lat']:.3f}-{row['lon']:.3f}-FL{int(row['fl'])}-{digest}-L{int(row['layer'])}"
            if cid in seen:
                seen[cid] += 1
                cid = f"{cid}-{seen[cid]}"
            else:
                seen[cid] = 0
            cases.append(Case(cid, h, t, inp))
            truths.append({"mog": int(P.label(row["code"]))})
    return cases, truths, skipped


# ------------------------------------------------------------------ splits
def window_bounds(t0: datetime, t1: datetime, fractions=FRACTIONS) -> list[datetime]:
    span, cuts, acc = t1 - t0, [], 0.0
    for fr in fractions[:-1]:
        acc += fr
        cuts.append(t0 + span * acc)
    return cuts


def assign_window(issue: datetime, target: datetime, cuts: list[datetime]) -> int | None:
    """Window index, or None for a case whose hour straddles a cut (the gap)."""
    for w in range(len(cuts) + 1):
        if (w == 0 or issue > cuts[w - 1]) and (w == len(cuts) or target <= cuts[w]):
            return w
    return None


def block_of(t: datetime) -> str:
    """UTC date + 6-hour period of the report (reports cluster in time)."""
    return f"{t:%Y-%m-%d}|{t.hour // 6 * 6:02d}h"


def build_splits(cases: list[Case], truths: list[dict], start: datetime, end: datetime,
                 seed: int, max_cases: int = MAX_CASES) -> tuple[Splits, dict]:
    cuts = window_bounds(start, end)
    groups: dict[int, list[int]] = {w: [] for w in range(len(WINDOWS))}
    dropped = 0
    for k, c in enumerate(cases):
        w = assign_window(c.issue_time, c.target_time, cuts)
        if w is None or c.issue_time < start or c.target_time > end:
            dropped += 1
            continue
        groups[w].append(k)
    windows, info = {}, {"cuts": [c.isoformat() for c in cuts], "dropped_at_cuts": dropped}
    for w, name in enumerate(WINDOWS):
        idx = groups[w]
        full = len(idx)
        if len(idx) > max_cases:
            rng = np.random.default_rng([seed, w])
            idx = sorted(rng.choice(idx, size=max_cases, replace=False).tolist())
        idx.sort(key=lambda k: (cases[k].target_time, cases[k].case_id))
        windows[name] = Window([cases[k] for k in idx], [truths[k] for k in idx],
                               [block_of(cases[k].target_time) for k in idx])
        info[name] = {"available": full, "used": len(idx)}
    sp = Splits(**windows)
    sp.check_order()
    return sp, info


# ------------------------------------------------------------- the Problem
class TurbProblem:
    name = "turb"
    title = ("Give the probability that a pilot report at flight level 200 or above over the continental "
             "US is moderate-or-greater turbulence, from the upper-air fields at its place and hour.")
    target_key = "risk"
    loss_name = "Brier score x 100 (squared error of the probability, times 100)"

    def __init__(self, days: int = 30, until: datetime | str | None = None, seed: int = 20261114,
                 cache_dir: Path = PROBLEM_CACHE, refresh: bool = False, iem_dir: Path | None = None,
                 field_store: F.FieldStore | None = None, max_cases: int = MAX_CASES, log=print):
        if isinstance(until, str):
            ts = pd.Timestamp(until)
            until = (ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")).to_pydatetime()
        self.days, self.until, self.seed, self.max_cases = days, until, seed, max_cases
        self.cache_dir = Path(cache_dir)
        self.refresh = refresh
        self.iem_dir = iem_dir
        self.field_store = field_store
        self.log = log
        self._splits: Splits | None = None
        self.info: dict = {}

    # ---- caching
    def _params(self) -> str:
        return f"days{self.days}_max{self.max_cases}_seed{self.seed}_v{BUILD_VERSION}"

    def _key(self, until: datetime) -> str:
        return f"{until:%Y%m%dT%H%MZ}_{self._params()}"

    def _latest_path(self) -> Path:
        return self.cache_dir / f"latest_{self._params()}.json"

    def cache_key(self) -> str:
        self.splits()
        return self._key(self.until)

    def default_until(self, fs: F.FieldSet) -> datetime:
        """End of the last cached IEM day, but no later than the hindsight part of
        the backfill fields (hours before its download)."""
        end = cached_iem_end(self.iem_dir)
        hind = datetime.fromtimestamp(int(fs.times[fs.avail < fs.times].max()), tz=timezone.utc)
        return min(end, hind + timedelta(hours=1)) if end else hind

    def splits(self) -> Splits:
        if self._splits is not None:
            return self._splits
        if self.until is None and not self.refresh and self._latest_path().exists():
            self.until = pd.Timestamp(json.loads(self._latest_path().read_text())["until"]).to_pydatetime()
        fs = None
        if self.until is None:
            fs = backfill_fields(self.field_store)
            self.until = self.default_until(fs)
        path = self.cache_dir / f"{self._key(self.until)}.pkl"
        if path.exists() and not self.refresh:
            # Trusted: written only by this method into the lab's own cache directory;
            # nothing from a candidate or the network is ever unpickled.
            with open(path, "rb") as fh:
                self._splits, self.info = pickle.load(fh)
            return self._splits
        fs = fs or backfill_fields(self.field_store)
        start = self.until - timedelta(days=self.days)
        self.log(f"building {self.name} cases {start:%Y-%m-%d %H:%M} .. {self.until:%Y-%m-%d %H:%M} UTC ...")
        reps = cached_report_layers(start, self.until, self.iem_dir)
        cases, truths, skipped = build_cases(reps, fs)
        self._splits, info = build_splits(cases, truths, start, self.until, self.seed, self.max_cases)
        self.info = {**info, "report_layers": int(len(reps)), "skipped_no_fields": int(skipped),
                     "start": start.isoformat(), "until": self.until.isoformat()}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump((self._splits, self.info), fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
        self._latest_path().write_text(json.dumps({"until": self.until.isoformat(), "key": path.stem}))
        return self._splits

    # ---- scoring
    @staticmethod
    def _valid(v) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0.0 <= v <= 1.0

    def losses(self, predictions: Sequence[dict], truths: Sequence[dict]) -> np.ndarray:
        """Brier score x 100 per case. If any probability is missing, non-finite or
        outside [0, 1], the whole evaluation fails: every loss is NaN."""
        if len(predictions) != len(truths):
            raise ValueError("predictions and truths differ in length")
        p = [(q or {}).get(self.target_key) for q in predictions]
        if not all(self._valid(v) for v in p):
            return np.full(len(truths), np.nan)
        y = np.array([float(t["mog"]) for t in truths])
        return (np.array(p, float) - y) ** 2 * 100.0

    def summarize(self, cases: Sequence[Case], predictions: Sequence[dict], truths: Sequence[dict]) -> dict:
        p = [(q or {}).get(self.target_key) for q in predictions]
        bad = sum(1 for v in p if not self._valid(v))
        y = np.array([int(t["mog"]) for t in truths])
        out = {"n": int(len(y)), "positives": int(y.sum()),
               "base_rate": float(y.mean()) if len(y) else float("nan")}
        if bad:
            return {**out, "failed": f"{bad} probabilities missing, non-finite or outside [0, 1]",
                    "n_invalid": bad, "mean_loss": float("nan"), "auc": float("nan"), "by_fl_band": {}}
        prob = np.array(p, float)
        loss = (prob - y) ** 2 * 100.0
        fl = np.array([c.inputs["flight_level"] for c in cases], float)
        by = {}
        for lo, hi in FL_BANDS:
            m = (fl >= lo) & (fl < hi)
            if m.any():
                a = auc(prob[m], y[m])
                by[f"FL{lo}-{hi - 1}" if hi < 999 else f"FL{lo}+"] = {
                    "mean_loss": float(loss[m].mean()), "auc": None if a != a else float(a),
                    "n": int(m.sum()), "positives": int(y[m].sum())}
        a = auc(prob, y)
        return {**out, "mean_loss": float(loss.mean()), "brier_x100": float(loss.mean()),
                "auc": None if a != a else float(a), "mean_probability": float(prob.mean()),
                "by_fl_band": by}

    def source_files(self) -> list[str]:
        here = Path(__file__).resolve().parent
        return [str(here / f) for f in ("problem.py", "pireps.py", "fields.py", "diagnostics.py", "interp.py")]

    # ---- candidates
    def seed_source(self) -> str:
        return SEED_SOURCE

    def baseline_sources(self) -> dict[str, str]:
        return dict(BASELINE_SOURCES)

    # ---- description
    def score_table(self, windows=("select",)) -> dict:
        sources = {**self.baseline_sources(), "seed": self.seed_source()}
        h = hashlib.sha256(json.dumps([sources, list(windows)], sort_keys=True).encode()).hexdigest()[:12]
        path = self.cache_dir / f"{self.cache_key()}_scores_{h}.json"
        if path.exists():
            return json.loads(path.read_text())
        from lab.evalpool.pool import EvalPool
        sp = self.splits()
        with EvalPool(self, sp, max_workers=4) as pool:
            names = list(sources)
            results = pool.map([sources[n] for n in names], windows=windows)
        table = {}
        for n, r in zip(names, results):
            if not r.ok:
                raise RuntimeError(f"{n} failed to evaluate: {r.error}")
            table[n] = {w: r.summary[w] for w in windows}
        path.write_text(json.dumps(table, indent=1))
        return table

    def describe(self) -> str:
        sp = self.splits()
        return render_description(self, sp, self.score_table(("select",)))


# ------------------------------------------------------------ description
def _span(w: Window) -> str:
    if not w.cases:
        return "empty"
    a = min(c.issue_time for c in w.cases)
    b = max(c.target_time for c in w.cases)
    pos = sum(t["mog"] for t in w.truths)
    return (f"{a:%Y-%m-%d %H:%M} to {b:%Y-%m-%d %H:%M} UTC, {len(w)} cases ({pos} moderate or greater, "
            f"{pos / len(w):.1%}), {len(set(w.blocks))} six-hour blocks")


def render_description(problem: TurbProblem, sp: Splits, table: dict) -> str:
    from lab.sandbox.lint import ALLOWED_IMPORTS
    bands = [f"FL{lo}-{hi - 1}" if hi < 999 else f"FL{lo}+" for lo, hi in FL_BANDS]
    order = ["climatology", "shear", "richardson", "ellrod1", "seed"]
    rows = []
    for n in [x for x in order if x in table] + [x for x in table if x not in order]:
        s = table[n]["select"]
        rows.append(f"| {n} | {s['mean_loss']:.2f} | {s['auc']:.3f} | " + " | ".join(
            f"{s['by_fl_band'].get(b, {}).get('auc') or float('nan'):.3f}" for b in bands) + " |")
    score_md = ("| candidate | Brier x100 | AUC | " + " | ".join(f"AUC {b}" for b in bands) + " |\n|"
                + "---|" * (len(bands) + 3) + "\n" + "\n".join(rows))
    imports = ", ".join(f"`{m}`" for m in sorted(ALLOWED_IMPORTS))
    sub = problem.info.get("train", {})
    sub_note = (f" Train had {sub.get('available')} cases and was subsampled to {sub.get('used')} with a "
                f"fixed seed." if sub and sub.get("available", 0) > sub.get("used", 0) else "")
    return textwrap.dedent(f"""\
    # Aviation turbulence: which pilot reports are moderate or worse

    ## The question
    Pilots report turbulence as they fly ("pilot reports"). For each report at flight level 200 or
    above (about 20,000 ft and up, where clear-air turbulence matters) over the continental US,
    give the probability that it was **moderate or greater** (moderate, moderate-severe, severe or
    extreme), rather than light or smooth. `predict(case)` returns `{{"risk": float}}`, a probability
    in [0, 1]. Light-moderate reports are left out as ambiguous. A report that describes several
    layers gives one case per layer, at that layer's altitude.

    **Read this first.**
    - The fields are **analysis-time (hindsight) values**: Open-Meteo's past values, stitched from
      model runs started at or after the report's hour, which may even have assimilated aircraft
      observations. This problem measures how well a formula ranks turbulence **given good
      fields**. It is not a forecast test; a separate live job tests forecasts.
    - Pilot reports are biased: they come from where aircraft fly, and pilots report bumps far
      more often than smooth air. The base rate (about a quarter of cases are positive) describes
      reports, not the sky.
    - The grid is coarse (2.5 degrees, about 250 km; six pressure levels). Turbulence happens at
      scales of a few km, so every diagnostic here is a smoothed proxy. Deformation on this grid is
      far coarser than in the operational Ellrod index.

    ## Loss
    **Brier score x 100**: `(risk - mog)^2 * 100` per case, where `mog` is 1 for moderate or greater
    and 0 otherwise; averaged over cases; lower is better. A constant probability equal to a
    window's own base rate b scores 100 * b * (1 - b) there (about 19 for b = 0.26); the base rate
    differs between windows, so a constant fitted on train scores worse. Any probability that is missing, non-finite or outside
    [0, 1] makes the whole evaluation fail. People also look at AUC (how well the risk ranks
    positives above negatives) overall and by flight-level band.

    ## Windows (time-ordered, never overlapping)
    - train: {_span(sp.train)}. `fit(cases, truths)` gets these cases and their truths
      (`truths[i] = {{"mog": 0 or 1}}`).{sub_note}
    - select: {_span(sp.select)}. You never see truths; a change is judged here first.
    - confirm: {_span(sp.confirm)}. Later still; a kept change must not lose here.
    - test: held back; scored once, by a person, at the end.

    ## Inputs: `case.inputs`
    `case.issue_time` is the start of the report's hour (the hour of the fields); `case.target_time`
    is the report time (both timezone-aware UTC). Values are floats unless noted; NaN means
    missing. All field values are at the start of the report's hour, interpolated bilinearly to
    the report's position; "at the report's level" means linear in log pressure between the two
    grid levels around it. Grid levels: 150, 200, 250, 300, 400, 500 hPa (about FL446, 387, 340,
    301, 236, 183). Reports above 150 hPa or below 500 hPa take the edge values.

    | key | units | meaning |
    |---|---|---|
    | `lat`, `lon` | degrees | report position (as decoded by the data source) |
    | `flight_level` | hundreds of ft | the layer's flight level (range middle, stated level, or the aircraft's level) |
    | `pressure_hpa` | hPa | standard-atmosphere pressure of that flight level |
    | `hour_utc` | int | hour of the report, UTC |
    | `solar_hour` | hours | local solar time, `hour_utc + minute/60 + lon/15`, modulo 24 |
    | `doy` | int | day of year |
    | `field_age_min` | int, minutes | report time minus the field hour (0-59) |
    | `speed`, `direction` | m/s, degrees | wind at the report's level; direction the wind blows **from** |
    | `u`, `v` | m/s | wind toward east, toward north |
    | `temp_c`, `height_m` | deg C, m | temperature and geopotential height at the report's level |
    | `vws` | m/s per km | vertical wind shear on the grid levels (mean of the layers above and below each level), at the report's level |
    | `shear_layer` | m/s per km | shear of the grid layer that contains the report (vector wind difference / thickness) |
    | `shear_above`, `shear_below` | m/s per km | shear of the grid layer above / below that one (NaN at the top / bottom) |
    | `ri` | none | Richardson number N^2 / vws^2 (can be negative or very large) |
    | `n2` | 1e-4 s^-2 | static stability N^2 |
    | `deform` | 1e-5 s^-1 | total horizontal deformation |
    | `div` | 1e-5 s^-1 | horizontal divergence |
    | `ti1` | 1e-7 s^-2 | Ellrod index TI1 = vws x deform |
    | `tgrad` | K per 100 km | horizontal temperature gradient |
    | `speed_grad_x`, `speed_grad_y`, `speed_grad` | m/s per 100 km | horizontal gradient of wind speed (east, north, magnitude) |
    | `above_hpa`, `below_hpa` | hPa | the grid levels just above and just below the report |
    | `above_<k>`, `below_<k>` | as above | the diagnostic `<k>` at those two grid levels, for `<k>` in speed, vws, ri, n2, deform, div, ti1, tgrad, temp_c |
    | `levels_hpa` | (6,) hPa | the grid levels, top to bottom |
    | `prof_speed`, `prof_dir`, `prof_temp_c`, `prof_height_m` | (6,) | wind speed (m/s), direction (deg), temperature (deg C) and height (m) at the six levels at the report's position |

    **Not available:** terrain (mountain waves are a known cause of turbulence; there is no
    terrain height here), convection and clouds, aircraft type, any other pilot report (no input
    depends on any report: only the labels come from reports), fields at later hours, the
    internet, files, and the truths of any window except train.

    ## Baselines and the seed (select window)
    {{score_table}}

    - `climatology`: the train base rate for every case.
    - `shear`, `ellrod1`, `richardson`: one diagnostic (`log vws`, `log ti1`, and
      `1 / (1 + max(ri, 0))`) turned into a probability by a one-variable logistic fit on train,
      so the Brier score is fair; their AUC is the diagnostic's own ranking.
    - `seed` (SeedBlend): logistic regression on standardized speed, log vws, log deform, log ri,
      n2, div and log tgrad, with a small ridge penalty, fitted on train.

    ## Sandbox rules for a candidate
    One Python file defining a top-level `class Forecaster` with a `name` attribute,
    `fit(self, cases, truths)` (called once) and `predict(self, case) -> dict`. It runs in a fresh
    locked-down process: imports only from {imports}; no file, network or process access; no
    `open`, `eval`, `exec`, `getattr`/`setattr`, `globals` and similar; no names starting with `__`
    (except `__init__`); at most 400 lines and 30,000 characters; no literal with more than 200
    elements or 2,000 characters of strings. Time limit about 3 minutes for fit plus all
    predictions; resident memory about 1.5 GB. Every prediction must be a dict of finite floats
    containing `risk`, between 0 and 1.

    ## How a change is accepted
    A candidate is fitted on train and predicts select and confirm. It is kept only if it beats the
    current best Brier score on **select** and does **not lose** on **confirm**. Both judgements use
    a paired block bootstrap: per-case loss differences are resampled by whole **six-hour blocks**
    (UTC date + 00/06/12/18 h period of the report), because reports close in time share the same
    weather. The test window is scored once at the end, by a person.
    """).replace("{score_table}", score_md)


# ------------------------------------------------------------ candidate files
SEED_SOURCE = '''\
"""SeedBlend: logistic regression on standardized diagnostics at the report's level.

Terms: speed, log vws, log deform, log ri (clipped to 0.01..10000), n2, div, log tgrad.
Columns are standardized on train; the slopes (not the intercept) carry an L2
penalty of 0.001; the mean log-loss plus penalty is minimised by Newton's method.
"""
import math

import numpy as np

TERMS = ("speed", "log_vws", "log_deform", "log_ri", "n2", "div", "log_tgrad")


def terms(i):
    return [
        i["speed"],
        math.log(max(i["vws"], 1e-3)),
        math.log(max(i["deform"], 1e-3)),
        math.log(min(max(i["ri"], 1e-2), 1e4)),
        i["n2"],
        i["div"],
        math.log(max(i["tgrad"], 1e-3)),
    ]


class Forecaster:
    name = "seed_blend"

    def __init__(self):
        self.l2 = 1e-3
        self.mean = None
        self.std = None
        self.w = None

    def fit(self, cases, truths):
        X = np.array([terms(c.inputs) for c in cases], dtype=float)
        y = np.array([float(t["mog"]) for t in truths])
        ok = np.all(np.isfinite(X), axis=1)
        X, y = X[ok], y[ok]
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0) + 1e-12
        Z = np.hstack([np.ones((len(y), 1)), (X - self.mean) / self.std])
        n, k = Z.shape
        pen = np.full(k, self.l2)
        pen[0] = 0.0
        w = np.zeros(k)
        for _ in range(50):
            p = 1.0 / (1.0 + np.exp(-(Z @ w)))
            grad = Z.T @ (p - y) / n + pen * w
            hess = (Z * (p * (1 - p))[:, None]).T @ Z / n + np.diag(pen)
            step = np.linalg.solve(hess, grad)
            w = w - step
            if np.max(np.abs(step)) < 1e-10:
                break
        self.w = w

    def predict(self, case):
        x = (np.array(terms(case.inputs), dtype=float) - self.mean) / self.std
        x = np.where(np.isfinite(x), x, 0.0)
        z = self.w[0] + float(x @ self.w[1:])
        return {"risk": float(1.0 / (1.0 + math.exp(-z)))}
'''


def _one_variable(name: str, doc: str, expr: str) -> str:
    return f'''\
"""{doc}
A one-variable logistic fit on train turns it into a probability."""
import math

import numpy as np


def x_of(i):
    return {expr}


class Forecaster:
    name = "{name}"

    def __init__(self):
        self.a = 0.0
        self.b = 0.0
        self.fill = 0.0

    def fit(self, cases, truths):
        x = np.array([x_of(c.inputs) for c in cases], dtype=float)
        y = np.array([float(t["mog"]) for t in truths])
        ok = np.isfinite(x)
        x, y = x[ok], y[ok]
        self.fill = float(np.median(x))
        Z = np.vstack([np.ones(len(x)), x]).T
        w = np.zeros(2)
        for _ in range(50):
            p = 1.0 / (1.0 + np.exp(-(Z @ w)))
            grad = Z.T @ (p - y) / len(y)
            hess = (Z * (p * (1 - p))[:, None]).T @ Z / len(y) + 1e-9 * np.eye(2)
            step = np.linalg.solve(hess, grad)
            w = w - step
            if np.max(np.abs(step)) < 1e-10:
                break
        self.a, self.b = float(w[0]), float(w[1])

    def predict(self, case):
        x = x_of(case.inputs)
        if not math.isfinite(x):
            x = self.fill
        z = min(max(self.a + self.b * x, -50.0), 50.0)
        return {{"risk": float(1.0 / (1.0 + math.exp(-z)))}}
'''


BASELINE_SOURCES = {
    "climatology": '''\
"""The share of train reports that were moderate or greater, for every case."""


class Forecaster:
    name = "climatology"

    def __init__(self):
        self.rate = 0.5

    def fit(self, cases, truths):
        self.rate = sum(t["mog"] for t in truths) / max(len(truths), 1)

    def predict(self, case):
        return {"risk": float(self.rate)}
''',
    "shear": _one_variable("shear", "Vertical wind shear: risk from log vws.",
                           'math.log(max(i["vws"], 1e-3))'),
    "ellrod1": _one_variable("ellrod1", "Ellrod index TI1 (shear x deformation): risk from log ti1.",
                             'math.log(max(i["ti1"], 1e-3))'),
    "richardson": _one_variable("richardson", "Low Richardson number: risk from 1 / (1 + max(ri, 0)).",
                                '1.0 / (1.0 + max(i["ri"], 0.0))'),
}


# ------------------------------------------------------------------- check
def check(workers: int = 4) -> dict:
    """What lab.evalpool.check prints, for this problem (that script reads coast-only
    fields: lead_h, by_lead and a 'persistence' baseline)."""
    from lab.evalpool.evaluate import compare
    from lab.evalpool.pool import EvalPool
    t0 = time.monotonic()
    problem = TurbProblem()
    sp = problem.splits()
    print(f"problem {problem.name}: {problem.title}")
    print(f"loss: {problem.loss_name}; splits ready in {time.monotonic() - t0:.1f}s")
    for w in WINDOWS:
        win = getattr(sp, w)
        if win.cases:
            pos = sum(t["mog"] for t in win.truths)
            print(f"  {w:<8}{len(win):>7} cases ({pos} positive)  {len(set(win.blocks)):>4} six-hour blocks  "
                  f"{min(c.issue_time for c in win.cases):%Y-%m-%d %H:%M} .. "
                  f"{max(c.target_time for c in win.cases):%Y-%m-%d %H:%M} UTC")
    print(f"  build info: {json.dumps({k: v for k, v in problem.info.items() if k not in ('cuts',)})}")
    sources = {**problem.baseline_sources(), "seed": problem.seed_source()}
    names = list(sources)
    t1 = time.monotonic()
    with EvalPool(problem, sp, max_workers=workers) as pool:
        results = dict(zip(names, pool.map([sources[n] for n in names])))
    wall = time.monotonic() - t1
    bands = [f"FL{lo}-{hi - 1}" if hi < 999 else f"FL{lo}+" for lo, hi in FL_BANDS]
    for w in ("select", "confirm"):
        print(f"\n{w}: Brier x100 and AUC (AUC by flight-level band)")
        print(f"{'candidate':<14}{'Brier':>8}{'AUC':>8}" + "".join(f"{b:>12}" for b in bands))
        for n in names:
            r = results[n]
            if not r.ok:
                print(f"{n:<14} FAILED: {r.error}")
                continue
            s = r.summary[w]
            print(f"{n:<14}{s['mean_loss']:>8.2f}{s['auc']:>8.3f}" + "".join(
                f"{(s['by_fl_band'].get(b, {}).get('auc') or float('nan')):>12.3f}" for b in bands))
        s0 = results[names[0]].summary[w]
        print(f"{'cases':<14}{s0['n']:>8}{'':>8}" + "".join(f"{s0['by_fl_band'].get(b, {}).get('n', 0):>12}"
                                                            for b in bands))
    print("\nevaluation time per candidate (fit on train + predict select and confirm, in the sandbox):")
    for n in names:
        print(f"  {n:<14}{results[n].duration_s:>6.1f}s")
    print(f"  all {len(names)} in parallel ({workers} workers): {wall:.1f}s wall clock")
    out = {"results": {n: {w: results[n].summary.get(w) for w in ("select", "confirm")} for n in names},
           "duration_s": {n: results[n].duration_s for n in names}, "wall_s": wall}
    if results["seed"].ok and results["ellrod1"].ok:
        for w in ("select", "confirm"):
            pr = compare(results["seed"], results["ellrod1"], sp, w)
            verdict = "better" if pr.diff.high < 0 else ("worse" if pr.diff.low > 0 else "no clear difference")
            print(f"\nseed minus ellrod1 on {w}, Brier x100, paired block bootstrap over {pr.diff.n} six-hour blocks:")
            print(f"  {pr.diff.mean:+.3f} [95%: {pr.diff.low:+.3f}, {pr.diff.high:+.3f}]  ({verdict}; n = {pr.n} cases)")
            out[f"seed_vs_ellrod1_{w}"] = {"diff": pr.diff.mean, "low": pr.diff.low, "high": pr.diff.high,
                                          "blocks": pr.diff.n, "verdict": verdict}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="The turbulence lab problem")
    ap.add_argument("cmd", choices=("check", "describe"))
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    if a.cmd == "check":
        check(a.workers)
    else:
        print(TurbProblem().describe())


if __name__ == "__main__":
    main()
