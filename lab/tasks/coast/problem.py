"""Coast water level as a lab Problem (see lab/core/problem.py).

The lab's agents write candidate forecasters; this module gives them the
question, the data windows and the scoring. Quantity: the residual, observed
water level minus the official tide prediction (metres), at 23 US West Coast
tide gauges, 1, 3, 6, 12 and 24 hours ahead. Loss: absolute error in cm.

Every value in `Case.inputs` is knowable at the case's issue time:

  * gauge values (own station and neighbours) are read only at or before the
    issue time;
  * the tide table is deterministic astronomy, published years ahead;
  * weather and wave-model values come from the Open-Meteo previous-run
    archives, from a run that was already published at the issue time
    (`water.run_offset_days`): never the stitched hindsight series;
  * buoy reports are read only at or before the issue time.

`tests/test_coast_problem.py` corrupts everything after the issue time (and
every model run made after it) and checks that `Case.inputs` does not change.
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from lab.core.problem import Splits, Window
from lab.core.task import Case

from . import sources as S
from .http import Client
from .water import (LEADS, PER_HOUR, RUN_DELAY_H, StationData, StationFeatures, _epoch, build_case,
                    current_residual, run_offset_days)

ROOT = Path(__file__).resolve().parents[3]
PROBLEM_CACHE = ROOT / "cache" / "coast" / "problem"
BUILD_VERSION = 1           # bump when the case layout changes; old caches are then ignored
HIST72 = 72
NBR_HIST = 24
BUOY_MAX_AGE_H = 3.0
FRACTIONS = (0.60, 0.15, 0.10, 0.15)   # train, select, confirm, test (of the time span)
WINDOWS = ("train", "select", "confirm", "test")


# ------------------------------------------------------------------ helpers
def great_circle_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def neighbours(stations: dict[str, StationData]) -> dict[str, list[tuple[str, float, str]]]:
    """For each station: [(id, km, side)] for the two nearest gauges to the south
    (lower latitude) then the two nearest to the north. Missing slots are
    (None, nan, side)."""
    out = {}
    for sid, sd in stations.items():
        sides = {"south": [], "north": []}
        for oid, od in stations.items():
            if oid == sid:
                continue
            side = "south" if od.lat < sd.lat else "north"
            sides[side].append((great_circle_km(sd.lat, sd.lon, od.lat, od.lon), oid))
        row = []
        for side in ("south", "north"):
            near = sorted(sides[side])[:2]
            row += [(oid, km, side) for km, oid in near]
            row += [(None, float("nan"), side)] * (2 - len(near))
        out[sid] = row
    return out


@dataclass
class Buoy:
    """One coastal buoy: its reports and the leak-free wave-model runs at its position."""
    id: str
    lat: float
    lon: float
    obs: pd.DataFrame                     # NDBC realtime2 (WVHT m, DPD s), UTC index
    model_runs: dict[int, pd.DataFrame] = field(default_factory=dict)  # k -> wave_height (m)

    def __post_init__(self):
        h = self.obs["WVHT"] if "WVHT" in self.obs else pd.Series(dtype=float)
        p = self.obs["DPD"] if "DPD" in self.obs else pd.Series(np.nan, index=self.obs.index)
        d = pd.DataFrame({"h": h, "p": p}).dropna(subset=["h"]).sort_index()
        self.t = np.array([_epoch(x) for x in d.index])
        self.h = d["h"].to_numpy(float)
        self.p = d["p"].to_numpy(float)
        self.runs = {}
        for k, df in (self.model_runs or {}).items():
            if df is not None and "wave_height" in df:
                m = df["wave_height"].dropna()
                self.runs[int(k)] = (np.array([_epoch(x) for x in m.index]), m.to_numpy(float))

    def now(self, issue_epoch: float) -> tuple[float, float]:
        """Newest report at or before the issue time (height, period), NaN if
        older than BUOY_MAX_AGE_H."""
        j = int(np.searchsorted(self.t, issue_epoch, side="right")) - 1
        if j < 0 or issue_epoch - self.t[j] > BUOY_MAX_AGE_H * 3600:
            return float("nan"), float("nan")
        return float(self.h[j]), float(self.p[j])

    def model_target(self, lead_h: int, target_epoch: float) -> float:
        src = self.runs.get(run_offset_days(lead_h))
        if src is None:
            return float("nan")
        return float(np.interp(target_epoch, src[0], src[1], left=np.nan, right=np.nan))


@dataclass
class CoastData:
    """Everything the case builder reads. Built from the cache, or by tests."""
    feats: dict[str, StationFeatures]
    buoys: dict[str, Buoy]
    nearest_buoy: dict[str, tuple[str, float]]   # station -> (buoy id, km)
    nbrs: dict[str, list[tuple[str, float, str]]]

    @classmethod
    def from_parts(cls, stations: dict[str, StationData], buoys: dict[str, Buoy],
                   start: pd.Timestamp, end: pd.Timestamp) -> "CoastData":
        feats = {sid: StationFeatures(sd, start, end) for sid, sd in stations.items()}
        near = {}
        for sid, sd in stations.items():
            if buoys:
                b = min(buoys.values(), key=lambda b: great_circle_km(sd.lat, sd.lon, b.lat, b.lon))
                near[sid] = (b.id, great_circle_km(sd.lat, sd.lon, b.lat, b.lon))
        return cls(feats, buoys, near, neighbours(stations))


def _hist(f: StationFeatures, i: int, n: int) -> np.ndarray:
    idx = i - PER_HOUR * np.arange(n)
    return np.where(idx >= 0, f.r_now[np.clip(idx, 0, None)], np.nan)


def _r4(a) -> np.ndarray:
    return np.round(np.asarray(a, dtype=float), 4)


def issue_ok(f: StationFeatures, i: int) -> bool:
    """build_case gives a case at index i (independent of the lead)."""
    return 0 <= i < f.n and current_residual(f, i) is not None and not np.isnan(f.r_mean[i])


def build_problem_case(data: CoastData, sid: str, issue: datetime, lead_h: int) -> Case | None:
    """The base water case (weather from previous runs only) plus the richer
    inputs. Every value is knowable at `issue`."""
    f = data.feats[sid]
    base = build_case(f, issue, lead_h, weather="runs")
    if base is None:
        return None
    i = f.index(issue)
    inp = dict(base.inputs)
    sd = f.sd
    inp["lat"], inp["lon"] = float(sd.lat), float(sd.lon)
    # own gauge: 72 hourly residuals, newest first (index 0 = issue time)
    inp["r_hist72"] = _r4(_hist(f, i, HIST72))
    # tide table for each hour issue..target (deterministic, allowed)
    inp["tide_fwd"] = _r4([f.tide_at(issue + timedelta(hours=k)) for k in range(lead_h + 1)])
    # weather for each hour issue..target from one run published before issue
    w = f.weather_hours(issue, lead_h, weather="runs")
    nanv = np.full(lead_h + 1, np.nan)
    inp["wx_p"] = np.round(w["p"], 2) if w else nanv
    inp["wx_u"] = np.round(w["u"], 3) if w else nanv.copy()
    inp["wx_v"] = np.round(w["v"], 3) if w else nanv.copy()
    inp["wx_run_age_h"] = float(w["run_age_h"]) if w else float("nan")
    # neighbouring gauges along the coast
    nb = []
    for oid, km, side in data.nbrs.get(sid, []):
        g = data.feats.get(oid) if oid else None
        r_now, hist = float("nan"), np.full(NBR_HIST, np.nan)
        if g is not None:
            j = g.index(issue)
            if 0 <= j < g.n:
                cur = current_residual(g, j)
                r_now = float(cur[0]) if cur else float("nan")
                hist = _r4(_hist(g, j, NBR_HIST))
        nb.append({"id": oid, "side": side, "dist_km": round(float(km), 1), "r_now": r_now, "r_hist24": hist})
    inp["nbr"] = nb
    # nearest coastal buoy: report at issue, and the wave model's forecast for the target
    hs = tp = fc = km = float("nan")
    bid = None
    if sid in data.nearest_buoy:
        bid, km = data.nearest_buoy[sid]
        b = data.buoys[bid]
        hs, tp = b.now(_epoch(issue))
        fc = b.model_target(lead_h, _epoch(base.target_time))
    inp.update(buoy_id=bid, buoy_dist_km=round(float(km), 1), buoy_hs_now=hs, buoy_tp_now=tp,
               wave_fc_hs_target=fc)
    return Case(base.case_id, base.issue_time, base.target_time, inp)


# ------------------------------------------------------------------ splits
def window_bounds(t0: datetime, t1: datetime, fractions=FRACTIONS) -> list[datetime]:
    span = t1 - t0
    cuts, acc = [], 0.0
    for fr in fractions[:-1]:
        acc += fr
        cuts.append(t0 + span * acc)
    return cuts


def assign_window(issue: datetime, target: datetime, cuts: list[datetime]) -> int | None:
    """Window index, or None for cases that straddle a boundary (that is the gap)."""
    lo = None
    for w in range(len(cuts) + 1):
        start_ok = w == 0 or issue > cuts[w - 1]
        end_ok = w == len(cuts) or target <= cuts[w]
        if start_ok and end_ok:
            lo = w
            break
    return lo


def stratified_pick(groups: dict[tuple, list[int]], n: int, rng: np.random.Generator) -> list[int]:
    """Pick about n items, each group in proportion to its size (largest remainder)."""
    total = sum(len(v) for v in groups.values())
    if total <= n:
        return sorted(i for v in groups.values() for i in v)
    keys = sorted(groups)
    exact = {k: n * len(groups[k]) / total for k in keys}
    take = {k: int(math.floor(exact[k])) for k in keys}
    rest = n - sum(take.values())
    for k in sorted(keys, key=lambda k: (-(exact[k] - take[k]), k))[:rest]:
        take[k] += 1
    out = []
    for k in keys:
        idx = groups[k]
        out += [idx[j] for j in rng.choice(len(idx), size=take[k], replace=False)]
    return sorted(out)


def build_splits(data: CoastData, start: datetime, end: datetime, sizes: dict[str, int], seed: int,
                 leads=LEADS) -> Splits:
    """Hourly issue times in [start, end]; windows by time; stratified subsample."""
    cuts = window_bounds(start, end)
    keys: list[tuple[str, datetime, int, int]] = []     # (station, issue, lead, window)
    for sid, f in data.feats.items():
        t = pd.Timestamp(start).ceil("h").to_pydatetime()
        while t <= end:
            if issue_ok(f, f.index(t)):
                for h in leads:
                    target = t + timedelta(hours=h)
                    if target > end:
                        continue
                    w = assign_window(t, target, cuts)
                    if w is None or f.truth_at(target) is None:
                        continue
                    keys.append((sid, t, h, w))
            t += timedelta(hours=1)
    windows = {}
    for w, name in enumerate(WINDOWS):
        groups: dict[tuple, list[int]] = {}
        for k, (sid, _, h, ww) in enumerate(keys):
            if ww == w:
                groups.setdefault((sid, h), []).append(k)
        rng = np.random.default_rng([seed, w])
        picked = stratified_pick(groups, sizes[name], rng)
        picked.sort(key=lambda k: (keys[k][1], keys[k][0], keys[k][2]))
        cases, truths, blocks = [], [], []
        for k in picked:
            sid, t, h, _ = keys[k]
            c = build_problem_case(data, sid, t, h)
            if c is None:     # cannot happen after issue_ok, kept as a guard
                continue
            cases.append(c)
            truths.append({"residual_m": float(data.feats[sid].truth_at(c.target_time))})
            blocks.append(f"{sid}|{c.target_time:%Y-%m-%d}")
        windows[name] = Window(cases, truths, blocks)
    sp = Splits(**windows)
    sp.check_order()
    return sp


# --------------------------------------------------------------- data load
def load_coast_data(client: Client, until: datetime, days: int, log=print) -> CoastData:
    """Gauges, tide table, previous-run weather, buoys and previous-run wave model."""
    from .data import load_stations
    from .waves import BUOY_META_URL, COASTAL, parse_buoy_meta
    stations = load_stations(client, until, days, with_weather=False, weather_runs=True, log=log)
    buoys: dict[str, Buoy] = {}
    try:
        meta = parse_buoy_meta(client.get_text(BUOY_META_URL, ttl=30 * 86400.0))
    except Exception as e:  # noqa: BLE001 - buoys are optional inputs
        log(f"  buoy list unavailable: {e}")
        meta = {}
    raw = {}
    for b in COASTAL:
        if b not in meta:
            continue
        try:
            df = S.fetch_ndbc(client, b, "txt", ttl=600.0)
        except Exception as e:  # noqa: BLE001
            log(f"  buoy {b} unavailable: {e}")
            continue
        if df.empty or "WVHT" not in df:
            continue
        raw[b] = df[df.index <= pd.Timestamp(until)]
    pts = [(meta[b]["lat"], meta[b]["lon"]) for b in raw]
    runs = S.fetch_marine_runs(client, pts, (until - timedelta(days=days + 4)).date(), until.date(),
                               S.now_utc()) if pts else None
    for j, b in enumerate(raw):
        buoys[b] = Buoy(b, meta[b]["lat"], meta[b]["lon"], raw[b],
                        {k: dfs[j] for k, dfs in runs.items()} if runs else {})
    start = pd.Timestamp(until) - pd.Timedelta(days=days + 4)
    return CoastData.from_parts(stations, buoys, start, pd.Timestamp(until))


def data_until(client: Client, now: datetime, days: int, log=print) -> datetime:
    """Last full hour for which gauge data exist (the cache key)."""
    from .data import load_stations
    st = load_stations(client, now, days, with_weather=False, log=lambda *a: None)
    last = max(sd.obs.index[-1] for sd in st.values())
    return pd.Timestamp(min(pd.Timestamp(now), last)).floor("h").to_pydatetime()


# ------------------------------------------------------------- the Problem
class CoastWaterProblem:
    name = "coast_water"
    title = ("Forecast how far the water at 23 US West Coast tide gauges will run above the "
             "official tide table, 1 to 24 hours ahead.")
    target_key = "residual_m"
    loss_name = "absolute error, cm"

    def __init__(self, days: int = 90, until: datetime | str | None = None, n_train: int = 12000,
                 n_select: int = 6000, n_confirm: int = 6000, n_test: int = 8000, seed: int = 20261113,
                 client: Client | None = None, cache_dir: Path = PROBLEM_CACHE, refresh: bool = False,
                 log=print):
        if isinstance(until, str):
            until = pd.Timestamp(until).tz_convert("UTC").to_pydatetime() if pd.Timestamp(until).tzinfo \
                else pd.Timestamp(until).tz_localize("UTC").to_pydatetime()
        self.days, self.until, self.seed = days, until, seed
        self.sizes = {"train": n_train, "select": n_select, "confirm": n_confirm, "test": n_test}
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.refresh = refresh
        self.log = log
        self._splits: Splits | None = None

    # ---- caching
    def _params(self) -> str:
        s = "_".join(f"{k}{v}" for k, v in self.sizes.items())
        return f"days{self.days}_{s}_seed{self.seed}_v{BUILD_VERSION}"

    def _key(self, until: datetime) -> str:
        return f"{until:%Y%m%dT%H%MZ}_{self._params()}"

    def _latest_path(self) -> Path:
        return self.cache_dir / f"latest_{self._params()}.json"

    def cache_key(self) -> str:
        self.splits()
        return self._key(self.until)

    def splits(self) -> Splits:
        if self._splits is not None:
            return self._splits
        if self.until is None and not self.refresh and self._latest_path().exists():
            self.until = pd.Timestamp(json.loads(self._latest_path().read_text())["until"]).to_pydatetime()
        if self.until is None:
            now = pd.Timestamp.now(tz="UTC").floor("h").to_pydatetime()
            self.until = data_until(self.client or Client(), now, self.days, self.log)
        path = self.cache_dir / f"{self._key(self.until)}.pkl"
        if path.exists() and not self.refresh:
            # Trusted: this file is written only by this method, in the lab's own cache
            # directory; nothing from a candidate or the network is ever unpickled.
            with open(path, "rb") as fh:
                self._splits = pickle.load(fh)
            return self._splits
        client = self.client or Client()
        self.log(f"building {self.name} splits until {self.until:%Y-%m-%dT%H:%MZ} ({self.days} days) ...")
        data = load_coast_data(client, self.until, self.days, log=self.log)
        start = self.until - timedelta(days=self.days)
        self._splits = build_splits(data, start, self.until, self.sizes, self.seed)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(self._splits, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
        self._latest_path().write_text(json.dumps({"until": self.until.isoformat(), "key": path.stem}))
        return self._splits

    # ---- scoring
    def losses(self, predictions: Sequence[dict], truths: Sequence[dict]) -> np.ndarray:
        if len(predictions) != len(truths):
            raise ValueError("predictions and truths differ in length")
        out = np.full(len(truths), np.nan)
        for k, (p, t) in enumerate(zip(predictions, truths)):
            v = (p or {}).get(self.target_key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out[k] = abs(float(v) - float(t[self.target_key])) * 100.0
        return out

    def summarize(self, cases: Sequence[Case], predictions: Sequence[dict], truths: Sequence[dict]) -> dict:
        loss = self.losses(predictions, truths)
        leads = np.array([int(c.inputs["lead_h"]) for c in cases])
        by = {}
        for h in sorted(set(leads.tolist())):
            v = loss[leads == h]
            by[str(h)] = {"mean_loss": float(np.nanmean(v)) if np.isfinite(v).any() else float("nan"),
                          "n": int(np.isfinite(v).sum())}
        return {"mean_loss": float(np.nanmean(loss)) if np.isfinite(loss).any() else float("nan"),
                "n": int(np.isfinite(loss).sum()), "n_missing": int(np.isnan(loss).sum()), "by_lead": by}

    def source_files(self) -> list[str]:
        """Code that defines the cases, truths and loss (hashed by final_test)."""
        here = Path(__file__).resolve().parent
        return [str(here / f) for f in ("problem.py", "water.py", "data.py", "sources.py", "waves.py")]

    # ---- candidates
    def seed_source(self) -> str:
        return SEED_SOURCE

    def baseline_sources(self) -> dict[str, str]:
        return dict(BASELINE_SOURCES)

    # ---- description
    def score_table(self, windows=("select",)) -> dict:
        """{candidate: {window: summary}} for the baselines and the seed, from real
        sandbox evaluations; cached next to the splits."""
        sources = {**self.baseline_sources(), "seed": self.seed_source()}
        h = hashlib.sha256(json.dumps([sources, list(windows)], sort_keys=True).encode()).hexdigest()[:12]
        path = self.cache_dir / f"{self.cache_key()}_scores_{h}.json"
        if path.exists():
            return json.loads(path.read_text())
        from lab.evalpool.pool import EvalPool
        sp = self.splits()
        pool = EvalPool(self, sp, max_workers=4)
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
        table = self.score_table(("select",))
        return render_description(self, sp, table)


# ------------------------------------------------------------ description
def _span(w: Window) -> str:
    if not w.cases:
        return "empty"
    a = min(c.issue_time for c in w.cases)
    b = max(c.target_time for c in w.cases)
    return f"{a:%Y-%m-%d %H:%M} to {b:%Y-%m-%d %H:%M} UTC, {len(w)} cases, {len(set(w.blocks))} station-days"


def render_description(problem: CoastWaterProblem, sp: Splits, table: dict) -> str:
    from lab.sandbox.lint import ALLOWED_IMPORTS
    leads = [str(h) for h in LEADS]
    rows = []
    order = ["tide_table", "persistence", "damped_persistence", "seed"]
    for n in [x for x in order if x in table] + [x for x in table if x not in order]:
        s = table[n]["select"]
        rows.append(f"| {n} | {s['mean_loss']:.2f} | " + " | ".join(
            f"{s['by_lead'].get(h, {}).get('mean_loss', float('nan')):.2f}" for h in leads) + " |")
    score_md = ("| candidate | all leads | " + " | ".join(f"{h} h" for h in leads) + " |\n|"
                + "---|" * (len(leads) + 2) + "\n" + "\n".join(rows))
    train = sp.train
    wx_share = np.mean([c.inputs["has_weather"] for c in train.cases]) if train.cases else float("nan")
    buoy_share = np.mean([np.isfinite(c.inputs["buoy_hs_now"]) for c in train.cases]) if train.cases else 0
    buoy_share_sel = np.mean([np.isfinite(c.inputs["buoy_hs_now"]) for c in sp.select.cases]) \
        if sp.select.cases else 0
    stations = sorted({c.inputs["station"] for c in train.cases})
    imports = ", ".join(f"`{m}`" for m in sorted(ALLOWED_IMPORTS))
    return textwrap.dedent(f"""\
    # Coast water level: how far the sea runs above the tide table

    ## The question
    NOAA publishes an official tide prediction for every tide gauge: pure astronomy, computed
    years ahead. The real water level differs from it because of weather (low pressure lifts
    the sea about 1 cm per hPa; wind piles water against the coast), river flow, ocean
    temperature and long, slow ocean waves. That difference is the **residual** (observed minus
    predicted, metres). Forecasting it is what turns a tide table into a flood warning.

    For each case you predict the residual at one of {len(stations)} West Coast gauges
    (San Diego to Seattle) at `target_time`, given only what was known at `issue_time`.
    Leads are 1, 3, 6, 12 and 24 hours. `predict(case)` returns `{{"residual_m": float}}`.

    Note: this season the tide table is off by about +24 to +32 cm on average (an unusually high
    sea-level season; the offset is positive at every gauge and has risen since summer), so
    "residual = 0" is a poor forecast and the gauge's recent mean matters a lot.

    ## Loss
    Absolute error in **cm**: `|predicted residual_m - observed residual_m| * 100`, averaged over
    cases. The observed residual at the target is the mean of the 6-minute residuals within
    +-6 minutes of `target_time`. Lower is better.

    ## Windows (time-ordered, never overlapping)
    - train: {_span(sp.train)}. `fit(cases, truths)` gets these cases and their truths
      (`truths[i] = {{"residual_m": float}}`).
    - select: {_span(sp.select)}. You never see truths; a change is judged here first.
    - confirm: {_span(sp.confirm)}. Later still; a kept change must not lose here.
    - test: held back; scored once, by a person, at the end.

    Cases are a fixed random subsample of all hourly issue times, stratified by station and lead.

    ## Inputs: `case.inputs`
    `case.issue_time`, `case.target_time` (timezone-aware UTC datetimes), `case.lead_hours`
    (float) and `case.case_id` are also available. Arrays are numpy float arrays; NaN means missing.
    Every value was knowable at `issue_time`.

    | key | type / shape | meaning |
    |---|---|---|
    | `station` | str | NOAA gauge id, e.g. "9414290" (San Francisco) |
    | `lat`, `lon` | float | gauge position, degrees |
    | `lead_h` | int | 1, 3, 6, 12 or 24 |
    | `r_now` | float, m | current residual: mean of the newest (up to 3) 6-minute values in the hour before issue |
    | `r_age_min` | int, minutes | age of the newest gauge value at issue |
    | `r_hist` | (24,), m | hourly residual, newest first: `r_hist[k]` is k hours before issue (12-minute mean) |
    | `r_hist72` | (72,), m | the same for 72 hours (rounded to 0.1 mm); `r_hist72[12]` and `[25]` are about one and two tidal cycles (12.4 h, 24.8 h) back |
    | `r_mean` | float, m | mean residual over the last 72 hours |
    | `trend3` | float, m | `r_now - r_hist[3]` (0 if missing) |
    | `tide_now`, `tide_target` | float, m above MLLW | official tide prediction at issue and target |
    | `tide_fwd` | (lead_h+1,), m | tide prediction for every hour from issue (index 0) to target (index lead_h) |
    | `hour_utc`, `doy` | int | hour of day and day of year of the issue time |
    | `has_weather` | bool | True when the weather forecast below is complete for this case |
    | `wx_p` | (lead_h+1,), hPa | forecast sea-level pressure at the gauge for every hour issue..target |
    | `wx_u`, `wx_v` | (lead_h+1,), m/s | forecast 10 m wind toward east / toward north, same hours |
    | `wx_run_age_h` | float, h | the weather forecast comes from a model run at least this old (24 for leads up to 12 h, 48 for 24 h) |
    | `p_issue`, `p_target` | float, hPa | `wx_p[0]`, `wx_p[-1]` (NaN without weather) |
    | `dp_hpa` | float, hPa | `p_target - p_issue` (0 without weather) |
    | `taux`, `tauy` | float, m^2/s^2 | mean over hours 1..lead of `|U| u` and `|U| v`, a wind-stress proxy (0 without weather) |
    | `nbr` | list of 4 dicts | two nearest gauges to the south, then two to the north (by latitude): `id` (str or None), `side`, `dist_km`, `r_now` (m), `r_hist24` ((24,), m, hourly, newest first) |
    | `buoy_id`, `buoy_dist_km` | str, km | nearest NDBC coastal wave buoy |
    | `buoy_hs_now` | float, m | its significant wave height at issue (newest report, at most 3 h old) |
    | `buoy_tp_now` | float, s | its dominant wave period at issue |
    | `wave_fc_hs_target` | float, m | wave-model forecast of significant wave height at the buoy at target time, from a run published before issue |

    Weather and wave-model values are deliberately conservative: they come from runs at least 24 h
    (48 h for the 24 h lead) old, so that no case ever sees a forecast that was not yet published.
    Weather was available for {wx_share:.0%} of train cases. Buoy reports exist only for the most
    recent ~45 days (the NDBC files roll over): {buoy_share:.0%} of train cases and
    {buoy_share_sel:.0%} of select cases have `buoy_hs_now`.

    **Not available:** anything after `issue_time` (gauges, buoys, analyses), the observed water
    level itself (only the residual), river flow, ocean temperature, other weather variables,
    the internet, files, and the truths of any window except train.

    ## Baselines and the seed (select window, mean absolute error, cm)
    {{score_table}}

    - `tide_table`: residual 0 (what the official table implies).
    - `persistence`: `r_now`.
    - `damped_persistence`: `r_mean + a_lead (r_now - r_mean)`, `a_lead` by least squares per lead.
    - `seed` (SeedPhysics): per lead, least squares of `truth - r_mean` on `r_now - r_mean`,
      `dp_hpa` (inverse barometer), `trend3`, a constant, and per-station wind setup
      `w_x taux + w_y tauy`; a gauge-only fit is used when weather is missing.

    ## Sandbox rules for a candidate
    One Python file defining a top-level `class Forecaster` with a `name` attribute,
    `fit(self, cases, truths)` (called once) and `predict(self, case) -> dict`. It runs in a fresh
    locked-down process: imports only from {imports}; no file, network or process access; no
    `open`, `eval`, `exec`, `getattr`/`setattr`, `globals` and similar; no names starting with `__`
    (except `__init__`); at most 400 lines and 30,000 characters; no literal with more than 200
    elements or 2,000 characters of strings. Time limit about 3 minutes for fit plus all
    predictions; resident memory about 1.5 GB (the data alone take about 0.7 GB). Every
    prediction must be a dict of finite floats containing `residual_m`.

    ## How a change is accepted
    A candidate is fitted on train and predicts select and confirm. It is kept only if it beats the
    current best on the **select** window and does **not lose** on the **confirm** window. Both
    judgements use a paired block bootstrap: the per-case loss differences are resampled by whole
    **station-days** (station + UTC date of the target), because errors an hour apart at one gauge
    are strongly correlated. The test window is scored once at the end, by a person.
    """).replace("{score_table}", score_md)


# ------------------------------------------------------------ candidate files
SEED_SOURCE = '''\
"""SeedPhysics: damped persistence plus inverse barometer, trend and wind setup.

Per lead h, least squares (tiny ridge on standardised columns) of
    truth - r_mean = a (r_now - r_mean) + b dp_hpa + c trend3 + d
                     + w_x[station] taux + w_y[station] tauy
on cases with weather; a gauge-only fit (a, c, d) is used when weather is missing.
"""
import numpy as np


class Forecaster:
    name = "seed_physics"

    def __init__(self):
        self.ridge = 1e-5
        self.coef = {}
        self.coef_gauge = {}
        self.stations = []

    def _solve(self, X, y):
        scale = np.sqrt((X ** 2).mean(axis=0)) + 1e-12
        Xs = X / scale
        A = Xs.T @ Xs + self.ridge * len(y) * np.eye(X.shape[1])
        beta = np.linalg.solve(A, Xs.T @ y)
        return beta / scale

    def fit(self, cases, truths):
        self.stations = sorted({c.inputs["station"] for c in cases})
        pos = {s: k for k, s in enumerate(self.stations)}
        ns = len(self.stations)
        groups = {}
        for c, t in zip(cases, truths):
            groups.setdefault(int(c.inputs["lead_h"]), []).append((c, float(t["residual_m"])))
        for lead, rows in groups.items():
            Xg = np.array([[c.inputs["r_now"] - c.inputs["r_mean"], c.inputs["trend3"], 1.0]
                           for c, _ in rows])
            yg = np.array([t - c.inputs["r_mean"] for c, t in rows])
            bg = self._solve(Xg, yg)
            self.coef_gauge[lead] = (float(bg[0]), float(bg[1]), float(bg[2]))
            wrows = [(c, t) for c, t in rows if c.inputs["has_weather"]]
            if len(wrows) < 50:
                continue
            X = np.zeros((len(wrows), 4 + 2 * ns))
            for r, (c, _) in enumerate(wrows):
                i = c.inputs
                X[r, :4] = [i["r_now"] - i["r_mean"], i["dp_hpa"], i["trend3"], 1.0]
                k = pos.get(i["station"])
                if k is not None:
                    X[r, 4 + 2 * k] = i["taux"]
                    X[r, 5 + 2 * k] = i["tauy"]
            y = np.array([t - c.inputs["r_mean"] for c, t in wrows])
            b = self._solve(X, y)
            wind = {s: (float(b[4 + 2 * k]), float(b[5 + 2 * k])) for k, s in enumerate(self.stations)}
            self.coef[lead] = (float(b[0]), float(b[1]), float(b[2]), float(b[3]), wind)

    def predict(self, case):
        i = case.inputs
        lead = int(i["lead_h"])
        m = i["r_mean"]
        k = self.coef.get(lead)
        if i["has_weather"] and k is not None:
            a, bp, ct, d, wind = k
            wx, wy = wind.get(i["station"], (0.0, 0.0))
            y = a * (i["r_now"] - m) + bp * i["dp_hpa"] + ct * i["trend3"] + d + wx * i["taux"] + wy * i["tauy"]
        else:
            a, ct, d = self.coef_gauge.get(lead, (1.0, 0.0, 0.0))
            y = a * (i["r_now"] - m) + ct * i["trend3"] + d
        return {"residual_m": float(m + y)}
'''

BASELINE_SOURCES = {
    "tide_table": '''\
"""What the official tide table implies: no departure from the prediction."""


class Forecaster:
    name = "tide_table"

    def fit(self, cases, truths):
        pass

    def predict(self, case):
        return {"residual_m": 0.0}
''',
    "persistence": '''\
"""The current departure from the tide table stays as it is."""


class Forecaster:
    name = "persistence"

    def fit(self, cases, truths):
        pass

    def predict(self, case):
        return {"residual_m": float(case.inputs["r_now"])}
''',
    "damped_persistence": '''\
"""a_h * current residual + (1 - a_h) * 72-hour mean residual; a_h per lead by
least squares on (truth - mean) ~ a (now - mean)."""


class Forecaster:
    name = "damped_persistence"

    def __init__(self):
        self.a = {}

    def fit(self, cases, truths):
        sxx, sxy = {}, {}
        for c, t in zip(cases, truths):
            lead = int(c.inputs["lead_h"])
            x = c.inputs["r_now"] - c.inputs["r_mean"]
            y = t["residual_m"] - c.inputs["r_mean"]
            sxx[lead] = sxx.get(lead, 0.0) + x * x
            sxy[lead] = sxy.get(lead, 0.0) + x * y
        for lead in sxx:
            self.a[lead] = sxy[lead] / sxx[lead] if sxx[lead] > 0 else 1.0

    def predict(self, case):
        a = self.a.get(int(case.inputs["lead_h"]), 1.0)
        m = case.inputs["r_mean"]
        return {"residual_m": float(m + a * (case.inputs["r_now"] - m))}
''',
}
