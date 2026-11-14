"""A generic time-series forecasting Problem built from one table (CSV or Parquet).

    SeriesProblem("prices.csv", time_col="ts", value_col="price", id_cols=("gpu", "region"))

Each distinct combination of `id_cols` is one series. The values are put on a regular
grid (data.py: the value at grid time t is the mean of observations in (t - step, t]).
A case asks: given only what was known at the issue time, what will the value be
`lead` steps later? See docs/series.md for the five-minute recipe and the leak rules.

Every value in `Case.inputs` is read at or before the issue time, except the target's
calendar fields and the `known_future_cols` values at the target (only for columns that
are genuinely fixed in advance). `tests/test_series.py` corrupts everything after the
issue time and checks that the inputs do not change.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import textwrap
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from lab.core.problem import Splits, Window
from lab.core.task import Case

from . import candidates as C
from .cases import MAX_PEERS, PEER_HIST, build_case, choose_peers, issue_mask
from .data import Grid, build_grid, data_summary, fmt_step, read_table

ROOT = Path(__file__).resolve().parents[3]
PROBLEM_CACHE = ROOT / "cache" / "series" / "problem"
BUILD_VERSION = 2           # bump when the case layout changes; old caches are then ignored
FRACTIONS = (0.60, 0.15, 0.10, 0.15)   # train, select, confirm, test (of the time span)
WINDOWS = ("train", "select", "confirm", "test")
DEFAULT_MAX_CASES = {"train": 10000, "select": 5000, "confirm": 5000, "test": 5000}
MIN_BLOCKS = 30
LOSSES = ("mae", "mape", "rmse")
CONFIG_ENV = "LAB_SERIES_CONFIG"
CONFIG_KEYS = ("path", "time_col", "value_col", "id_cols", "freq", "leads", "lead_unit", "history",
               "covariate_cols", "known_future_cols", "loss", "name", "title", "max_cases", "seed",
               "log_target", "max_fill_steps", "cache_dir")


# ------------------------------------------------------------------ helpers
def window_bounds(n: int, fractions=FRACTIONS) -> list[float]:
    """Cut positions (grid index, float) for a span of grid indices 0..n."""
    cuts, acc = [], 0.0
    for fr in fractions[:-1]:
        acc += fr
        cuts.append(n * acc)
    return cuts


def assign_windows(i: np.ndarray, j: np.ndarray, cuts: Sequence[float]) -> np.ndarray:
    """Window index per case (issue index i, target index j); -1 for cases whose lead
    straddles a cut (that is the gap between windows)."""
    w = np.full(i.shape, -1)
    edges = [-math.inf, *cuts, math.inf]
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        m = (i > lo if k else np.ones(i.shape, bool)) & (j <= hi)
        w[(w < 0) & m] = k
    return w


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


BLOCK_HOURS = {"week": 168.0, "day": 24.0, "12h": 12.0, "6h": 6.0}
BLOCK_WORDS = {"week": ("series-week", "calendar week (ISO, UTC)"), "day": ("series-day", "calendar day (UTC)"),
               "12h": ("series-half-day", "half day (00-12 or 12-24 UTC)"),
               "6h": ("series-quarter-day", "quarter day (UTC)"), "step": ("series-step", "target time")}


def block_label(series_id: str, t, unit: str) -> str:
    """Block of a case: its series plus the calendar week / day / half or quarter day
    (UTC) of its target time, or the target time itself ("step")."""
    if unit == "week":
        y, w, _ = t.isocalendar()
        return f"{series_id}|{y}-W{w:02d}"
    if unit == "day":
        return f"{series_id}|{t:%Y-%m-%d}"
    if unit in ("12h", "6h"):
        n = int(BLOCK_HOURS[unit])
        return f"{series_id}|{t:%Y-%m-%d}T{t.hour // n * n:02d}"
    return f"{series_id}|{t:%Y-%m-%dT%H:%M}"


def resolve_path(p: str | Path, base: Path | None = None) -> Path:
    """Absolute paths as they are; relative ones against the config file's folder when the
    file exists there, else against the project root."""
    p = Path(p).expanduser()
    if p.is_absolute():
        return p
    if base is not None and (base / p).exists():
        return base / p
    return ROOT / p


def _mean(v: np.ndarray) -> float:
    return float(np.nanmean(v)) if np.isfinite(v).any() else float("nan")


# ------------------------------------------------------------- the Problem
class SeriesProblem:
    target_key = "y"

    def __init__(self, path, *, time_col: str, value_col: str, id_cols: Sequence[str] = (), freq=None,
                 leads: Sequence[int] = (1, 3, 7), lead_unit: str = "steps", history: int = 56,
                 covariate_cols: Sequence[str] = (), known_future_cols: Sequence[str] = (),
                 loss: str = "mae", name: str = "series", title: str | None = None, max_cases=None,
                 seed: int = 0, log_target: bool = False, max_fill_steps: int = 0,
                 cache_dir: str | Path = PROBLEM_CACHE, log=print):
        if loss not in LOSSES:
            raise ValueError(f"loss must be one of {LOSSES}, not {loss!r}")
        if lead_unit not in ("steps", "hours", "days"):
            raise ValueError("lead_unit must be 'steps', 'hours' or 'days'")
        if isinstance(id_cols, str) or isinstance(covariate_cols, str) or isinstance(known_future_cols, str):
            raise ValueError("id_cols, covariate_cols and known_future_cols are lists of column names")
        if not leads or any(float(x) <= 0 for x in leads):
            raise ValueError("leads must be positive")
        if int(history) < 2:
            raise ValueError("history must be at least 2")
        self.path = Path(path)
        self.time_col, self.value_col = time_col, value_col
        self.id_cols = tuple(id_cols)
        self.freq = freq
        self.lead_spec = tuple(leads)
        self.lead_unit = lead_unit
        self.history = int(history)
        self.covariate_cols = tuple(covariate_cols)
        self.known_future_cols = tuple(known_future_cols)
        self.loss = loss
        self.name = name
        self.log_target = bool(log_target)
        self.max_fill_steps = int(max_fill_steps)
        self.seed = int(seed)
        if max_cases is None:
            self.sizes = dict(DEFAULT_MAX_CASES)
        elif isinstance(max_cases, dict):
            self.sizes = {**DEFAULT_MAX_CASES, **{k: int(v) for k, v in max_cases.items()}}
        else:
            self.sizes = {w: int(max_cases) for w in WINDOWS}
        self.cache_dir = Path(cache_dir)
        self.log = log
        series = " / ".join(f"`{c}`" for c in self.id_cols) or "the single"
        self.title = title or (f"Forecast `{value_col}` for every {series} series, "
                               f"{', '.join(str(x) for x in self.lead_spec)} {lead_unit} ahead.")
        log_part = "log of " if self.log_target else ""
        self.loss_name = {
            "mae": f"absolute error of {log_part}{value_col}",
            "mape": "absolute percentage error, % (of the true value)",
            "rmse": f"squared error of {log_part}{value_col} (the window mean is the MSE; RMSE = its root)",
        }[loss]
        self.notes: list[str] = []
        self._grid: Grid | None = None
        self._splits: Splits | None = None
        self._file_hash: str | None = None
        self.block_unit: str | None = None
        self.peer_rule: str | None = None

    # ---- config
    @classmethod
    def from_config(cls, config: str | Path | dict, **overrides) -> "SeriesProblem":
        """Build from a JSON file (or dict) whose keys are the constructor arguments."""
        base = None
        if not isinstance(config, dict):
            cpath = Path(config).expanduser().resolve()
            base = cpath.parent
            config = json.loads(cpath.read_text())
        cfg = {**config, **overrides}
        unknown = sorted(set(cfg) - set(CONFIG_KEYS) - {"log"})
        if unknown:
            raise ValueError(f"unknown config keys {unknown}; known: {list(CONFIG_KEYS)}")
        for k in ("path", "time_col", "value_col"):
            if k not in cfg:
                raise ValueError(f"config needs {k!r}")
        cfg["path"] = resolve_path(cfg["path"], base)
        if "cache_dir" in cfg:
            cfg["cache_dir"] = resolve_path(cfg["cache_dir"], base)
        for k in ("id_cols", "leads", "covariate_cols", "known_future_cols"):
            if k in cfg and cfg[k] is not None:
                cfg[k] = tuple(cfg[k])
        path = cfg.pop("path")
        return cls(path, **cfg)

    def config(self) -> dict:
        return {"path": str(self.path), "time_col": self.time_col, "value_col": self.value_col,
                "id_cols": list(self.id_cols), "freq": self.freq, "leads": list(self.lead_spec),
                "lead_unit": self.lead_unit, "history": self.history,
                "covariate_cols": list(self.covariate_cols), "known_future_cols": list(self.known_future_cols),
                "loss": self.loss, "name": self.name, "title": self.title, "max_cases": self.sizes,
                "seed": self.seed, "log_target": self.log_target, "max_fill_steps": self.max_fill_steps}

    # ---- data
    def file_hash(self) -> str:
        if self._file_hash is None:
            h = hashlib.sha256()
            with open(self.path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            self._file_hash = h.hexdigest()
        return self._file_hash

    def load_grid(self, path: str | Path | None = None, *, step=None, origin=None, notes=None) -> Grid:
        df = read_table(path or self.path)
        return build_grid(df, time_col=self.time_col, value_col=self.value_col, id_cols=self.id_cols,
                          covariate_cols=self.covariate_cols, known_future_cols=self.known_future_cols,
                          step=step if step is not None else (pd.Timedelta(self.freq) if self.freq else None),
                          origin=origin, log_target=self.log_target,
                          notes=notes)

    def grid(self) -> Grid:
        if self._grid is None:
            notes: list[str] = []
            if self.freq:
                notes.append(f"step given in the config: {fmt_step(pd.Timedelta(self.freq))}")
            self._grid = self.load_grid(notes=notes)
            self.notes = notes + [n for n in self.notes if n not in notes]   # keep notes restored from the cache
            for n in notes:
                self.log(f"[{self.name}] {n}")
            if self._grid.step < pd.Timedelta("1h"):
                lh = [int(round(x * self._grid.step_hours)) for x in self.leads()]
                if len(set(lh)) < len(lh):
                    self.notes.append("the step is shorter than an hour and two leads round to the same "
                                      "whole hour, so lab tables keyed by `lead_h` merge them")
        return self._grid

    def leads(self) -> list[int]:
        """Leads in grid steps."""
        if self.lead_unit == "steps":
            return [int(x) for x in self.lead_spec]
        step_h = self.grid().step_hours
        per = {"hours": 1.0, "days": 24.0}[self.lead_unit]
        out = []
        for x in self.lead_spec:
            k = float(x) * per / step_h
            if abs(k - round(k)) > 1e-9 or round(k) < 1:
                raise ValueError(f"lead {x} {self.lead_unit} is not a whole number of {fmt_step(self.grid().step)} steps")
            out.append(int(round(k)))
        return out

    # ---- caching
    def _params(self) -> str:
        cfg = {k: v for k, v in self.config().items() if k not in ("path", "title")}
        return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def cache_key(self) -> str:
        return f"{self.name}_{self.file_hash()[:16]}_{self._params()}_v{BUILD_VERSION}"

    def splits(self) -> Splits:
        if self._splits is not None:
            return self._splits
        path = self.cache_dir / f"{self.cache_key()}.pkl"
        if path.exists():
            # Trusted: this file is written only by this method, in the lab's own cache
            # directory, keyed by the data file's SHA-256 and the config.
            with open(path, "rb") as fh:
                blob = pickle.load(fh)
            self._splits, self.block_unit, self.peer_rule, self.notes = (
                blob["splits"], blob["block_unit"], blob["peer_rule"], blob["notes"])
            return self._splits
        self._splits = self.build_splits()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump({"splits": self._splits, "block_unit": self.block_unit, "peer_rule": self.peer_rule,
                         "notes": self.notes}, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
        return self._splits

    # ---- cases
    def case_keys(self, grid: Grid, end: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(series, issue index, lead) of every case with a truth whose target <= end."""
        S, I, L = [], [], []
        for s in range(len(grid.series)):
            ok = issue_mask(grid, s, self.history)
            fin = np.isfinite(grid.y[s])
            for lead in self.leads():
                i = np.flatnonzero(ok[: max(end - lead + 1, 0)])
                i = i[fin[i + lead]]
                S.append(np.full(i.size, s))
                I.append(i)
                L.append(np.full(i.size, lead))
        cat = lambda xs: np.concatenate(xs) if xs else np.array([], dtype=int)  # noqa: E731
        return cat(S), cat(I), cat(L)

    def build_splits(self) -> Splits:
        g = self.grid()
        end = g.end_index
        if end < 10:
            raise ValueError("too little data: fewer than 10 grid steps")
        cuts = window_bounds(end)
        self.peers, self.peer_rule = choose_peers(g, self.id_cols, int(math.floor(cuts[0])))
        S, I, L = self.case_keys(g, end)
        W = assign_windows(I, I + L, cuts)
        picked_by_window = {}
        for w, name in enumerate(WINDOWS):
            groups: dict[tuple, list[int]] = {}
            for k in np.flatnonzero(W == w):
                groups.setdefault((int(S[k]), int(L[k])), []).append(int(k))
            rng = np.random.default_rng([self.seed, w])
            picked = stratified_pick(groups, self.sizes[name], rng)
            picked.sort(key=lambda k: (I[k], g.series[S[k]], L[k]))
            picked_by_window[name] = picked
        self.block_unit = self.choose_block_unit(g, S, I, L, picked_by_window)
        windows = {}
        for name in WINDOWS:
            cases, truths, blocks = [], [], []
            for k in picked_by_window[name]:
                s, i, lead = int(S[k]), int(I[k]), int(L[k])
                c = build_case(g, s, i, lead, self.peers[s], self.history, self.covariate_cols,
                               self.known_future_cols, self.max_fill_steps)
                if c is None:      # cannot happen after issue_mask, kept as a guard
                    continue
                cases.append(c)
                truths.append({"y": float(g.y[s, i + lead])})
                blocks.append(block_label(g.series[s], c.target_time, self.block_unit))
            windows[name] = Window(cases, truths, blocks)
        sp = Splits(**windows)
        sp.check_order()
        empty = [w for w in WINDOWS if not getattr(sp, w).cases]
        if empty:
            raise ValueError(f"windows without cases: {empty}; the data span is too short for these leads")
        return sp

    def choose_block_unit(self, g: Grid, S, I, L, picked: dict[str, list[int]]) -> str:
        """Series + calendar week when the step is a day or longer, else series + calendar day;
        a shorter block (day, 12 h, 6 h, one step) when that would leave fewer than MIN_BLOCKS
        blocks in select or confirm."""
        first = "week" if g.step >= pd.Timedelta("1D") else "day"
        units = [u for u in BLOCK_HOURS if g.step_hours <= BLOCK_HOURS[u] <= BLOCK_HOURS[first]]
        if not units or BLOCK_HOURS[units[-1]] > g.step_hours:
            units.append("step")
        for u in units:
            counts = []
            for name in ("select", "confirm"):
                labels = {block_label(g.series[S[k]], g.time(I[k] + L[k]), u) for k in picked[name]}
                counts.append(len(labels))
            if min(counts) >= MIN_BLOCKS:
                if u != units[0]:
                    self.notes.append(f"blocks: series + {BLOCK_WORDS[u][1]} (series + {BLOCK_WORDS[units[0]][1]} "
                                      f"would leave fewer than {MIN_BLOCKS} blocks in select or confirm)")
                return u
        self.notes.append(f"blocks: series + one step; even so select or confirm has fewer than {MIN_BLOCKS} "
                          "blocks, so comparisons will be noisy")
        return "step"

    def live_cases(self, grid: Grid | None = None) -> tuple[list[Case], list[str]]:
        """One case per series and lead issued at the end of the data (live forecasts).
        Returns (cases, ids of series skipped because they have no value in the last
        `history` steps)."""
        g = grid or self.grid()
        end = g.end_index
        peers, _ = choose_peers(g, self.id_cols, end)
        out, skipped = [], []
        for s in range(len(g.series)):
            row = []
            for lead in self.leads():
                c = build_case(g, s, end, lead, peers[s], self.history, self.covariate_cols,
                               self.known_future_cols, self.max_fill_steps)
                if c is not None:
                    row.append(c)
            if row:
                out += row
            else:
                skipped.append(g.series[s])
        return out, skipped

    def all_history_cases(self, grid: Grid | None = None, cap: int | None = None) -> tuple[list[Case], list[dict]]:
        """Cases over the whole file (every target <= the end of the data), subsampled to
        `cap` stratified by series and lead: the training set for live forecasts."""
        g = grid or self.grid()
        end = g.end_index
        peers, _ = choose_peers(g, self.id_cols, end)
        S, I, L = self.case_keys(g, end)
        groups: dict[tuple, list[int]] = {}
        for k in range(S.size):
            groups.setdefault((int(S[k]), int(L[k])), []).append(k)
        picked = stratified_pick(groups, cap or self.sizes["train"], np.random.default_rng([self.seed, 99]))
        picked.sort(key=lambda k: (I[k], g.series[S[k]], L[k]))
        cases, truths = [], []
        for k in picked:
            s, i, lead = int(S[k]), int(I[k]), int(L[k])
            c = build_case(g, s, i, lead, peers[s], self.history, self.covariate_cols, self.known_future_cols,
                           self.max_fill_steps)
            if c is not None:
                cases.append(c)
                truths.append({"y": float(g.y[s, i + lead])})
        return cases, truths

    # ---- scoring
    def losses(self, predictions: Sequence[dict], truths: Sequence[dict]) -> np.ndarray:
        if len(predictions) != len(truths):
            raise ValueError("predictions and truths differ in length")
        out = np.full(len(truths), np.nan)
        for k, (p, t) in enumerate(zip(predictions, truths)):
            v = (p or {}).get(self.target_key)
            y = t.get(self.target_key)
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)):
                continue
            if y is None or not math.isfinite(float(y)):
                continue
            v, y = float(v), float(y)
            if self.loss == "mae":
                out[k] = abs(v - y)
            elif self.loss == "rmse":
                out[k] = (v - y) ** 2
            else:   # mape, on the original scale
                if self.log_target:
                    v, y = math.exp(min(v, 700.0)), math.exp(y)
                if abs(y) > 1e-12:
                    out[k] = abs(v - y) / abs(y) * 100.0
        return out

    def summarize(self, cases: Sequence[Case], predictions: Sequence[dict], truths: Sequence[dict]) -> dict:
        loss = self.losses(predictions, truths)
        leads = np.array([int(c.inputs["lead_h"]) for c in cases])
        by = {}
        for h in sorted(set(leads.tolist())):
            v = loss[leads == h]
            by[str(h)] = {"mean_loss": _mean(v), "n": int(np.isfinite(v).sum())}
        out = {"mean_loss": _mean(loss), "n": int(np.isfinite(loss).sum()),
               "n_missing": int(np.isnan(loss).sum()), "by_lead": by}
        # plain-scale extras for people: MAE, RMSE and (when every truth is positive) MAPE
        pv = np.array([_num((p or {}).get(self.target_key)) for p in predictions], dtype=float)
        tv = np.array([_num(t.get(self.target_key)) for t in truths], dtype=float)
        if self.log_target:
            pv, tv = np.exp(np.clip(pv, -700, 700)), np.exp(tv)
        ok = np.isfinite(pv) & np.isfinite(tv)
        if ok.any():
            e = pv[ok] - tv[ok]
            out["mae"] = float(np.abs(e).mean())
            out["rmse"] = float(np.sqrt((e ** 2).mean()))
            if (tv[ok] > 0).all():
                out["mape_pct"] = float((np.abs(e) / tv[ok]).mean() * 100.0)
        return out

    def source_files(self) -> list[str]:
        """Code that defines the cases, truths and loss (hashed by final_test)."""
        here = Path(__file__).resolve().parent
        return [str(here / f) for f in ("problem.py", "data.py", "cases.py", "candidates.py")]

    # ---- candidates
    def seed_source(self) -> str:
        return C.SEED_SOURCE

    def baseline_sources(self) -> dict[str, str]:
        return C.baseline_sources(self.grid().step_hours)

    # ---- description
    def score_table(self, windows=("select",)) -> dict:
        """{candidate: {window: summary, "duration_s": float}} for the baselines and the seed,
        from real sandbox evaluations; cached next to the splits."""
        sources = {**self.baseline_sources(), "seed": self.seed_source()}
        h = hashlib.sha256(json.dumps([sources, list(windows)], sort_keys=True).encode()).hexdigest()[:12]
        sp = self.splits()
        path = self.cache_dir / f"{self.cache_key()}_scores_{h}.json"
        if path.exists():
            return json.loads(path.read_text())
        from lab.evalpool.pool import EvalPool
        names = list(sources)
        with EvalPool(self, sp, max_workers=4) as pool:
            results = pool.map([sources[n] for n in names], windows=windows)
        table = {}
        for n, r in zip(names, results):
            if not r.ok:
                raise RuntimeError(f"{n} failed to evaluate: {r.error}")
            table[n] = {**{w: r.summary[w] for w in windows}, "duration_s": r.duration_s}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table, indent=1))
        return table

    def data_summary(self) -> str:
        self.splits()
        return data_summary(self.grid(), self.value_col, self.notes)

    def describe(self) -> str:
        sp = self.splits()
        table = self.score_table(("select",))
        return render_description(self, sp, table)


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) else float("nan")


def from_env(**overrides) -> SeriesProblem:
    """The registry's factory: a SeriesProblem from the JSON config named by $LAB_SERIES_CONFIG."""
    path = os.environ.get(CONFIG_ENV)
    if not path:
        raise SystemExit(f"set {CONFIG_ENV} to a JSON config file for the series problem (see docs/series.md)")
    return SeriesProblem.from_config(path, **overrides)


# ------------------------------------------------------------ description
def _span(w: Window, unit: str) -> str:
    if not w.cases:
        return "empty"
    a = min(c.issue_time for c in w.cases)
    b = max(c.target_time for c in w.cases)
    return f"{a:%Y-%m-%d %H:%M} to {b:%Y-%m-%d %H:%M} UTC, {len(w)} cases, {len(set(w.blocks))} {BLOCK_WORDS[unit][0]}s"


def lead_label(problem: SeriesProblem, lead: int) -> str:
    h = lead * problem.grid().step_hours
    return f"{lead} step{'s' if lead != 1 else ''} ({h:g} h)"


def score_markdown(problem: SeriesProblem, table: dict, window: str) -> str:
    leads = problem.leads()
    keys = [str(int(round(x * problem.grid().step_hours))) for x in leads]
    order = ["last_value", "moving_average", "seasonal_naive", "drift", "seed"]
    rows = []
    for n in [x for x in order if x in table] + [x for x in table if x not in order]:
        s = table[n][window]
        extra = f"{s['mape_pct']:.2f}" if "mape_pct" in s else "n/a"
        rows.append(f"| {n} | {s['mean_loss']:.4g} | " + " | ".join(
            f"{s['by_lead'].get(k, {}).get('mean_loss', float('nan')):.4g}" for k in keys) + f" | {extra} |")
    return ("| candidate | all leads | " + " | ".join(lead_label(problem, x) for x in leads)
            + " | MAPE % |\n|" + "---|" * (len(leads) + 3) + "\n" + "\n".join(rows))


def render_description(problem: SeriesProblem, sp: Splits, table: dict) -> str:
    from lab.sandbox.lint import ALLOWED_IMPORTS
    g = problem.grid()
    step = fmt_step(g.step)
    leads = problem.leads()
    imports = ", ".join(f"`{m}`" for m in sorted(ALLOWED_IMPORTS))
    log = problem.log_target
    yname = f"log({problem.value_col})" if log else f"`{problem.value_col}`"
    ids = ", ".join(f"`{c}`" for c in problem.id_cols) or "(none: the whole file is one series)"
    covs = ", ".join(f"`{c}`" for c in problem.covariate_cols) or "none"
    known = ", ".join(f"`{c}`" for c in problem.known_future_cols) or "none"
    season = C.season_steps(g.step_hours)
    seasonal_line = (f"- `seasonal_naive`: the value one season ({season} steps) before the target "
                     "(the same phase in the newest season inside the history when the lead is longer).\n"
                     if season else "")
    loss_text = {
        "mae": f"Absolute error `|prediction - truth|` of {yname}, in its units, averaged over cases.",
        "mape": (f"Absolute percentage error `|prediction - truth| / |truth| * 100` on the original scale of "
                 f"`{problem.value_col}`; cases with a true value of 0 are not scored."),
        "rmse": f"Squared error `(prediction - truth)^2` of {yname}; the window mean is the MSE (RMSE is its root).",
    }[problem.loss]
    peer_rule = problem.peer_rule or "series with the same first id"
    fill_text = (f"; a missing value is carried forward from the last real observation at or before it, "
                 f"at most {problem.max_fill_steps} steps" if problem.max_fill_steps else "; NaN where missing")
    desc = textwrap.dedent(f"""\
    # {problem.name}: {problem.title}

    ## The question
    The data file holds timestamped values of `{problem.value_col}` for several series; one series is
    one combination of the id columns {ids}. Values are put on a regular grid with a step of **{step}**:
    the value at grid time t is the mean of the observations in (t - step, t], so it is known at t.
    For each case you predict {yname} of one series at `target_time`, {", ".join(lead_label(problem, x) for x in leads)}
    after `issue_time`, given only what was known at `issue_time`.
    `predict(case)` returns `{{"y": float}}`.

    ## Loss
    {loss_text} Lower is better.

    ## What the data looks like
    {{data_summary}}

    ## Windows (time-ordered, never overlapping)
    - train: {_span(sp.train, problem.block_unit)}. `fit(cases, truths)` gets these cases and their truths
      (`truths[i] = {{"y": float}}`).
    - select: {_span(sp.select, problem.block_unit)}. You never see truths; a change is judged here first.
    - confirm: {_span(sp.confirm, problem.block_unit)}. Later still; a kept change must not lose here.
    - test: held back; scored once, by a person, at the end.

    The span is cut by time into 60% / 15% / 10% / 15%; cases whose lead crosses a cut are dropped. Each
    window is a fixed random subsample (at most {problem.sizes['train']} / {problem.sizes['select']} /
    {problem.sizes['confirm']} / {problem.sizes['test']} cases), stratified by series and lead. Issue
    times are every grid step at which the series has a value within the last {problem.history} steps;
    the truth must be a real observation at the target (never a filled value).

    ## Inputs: `case.inputs`
    `case.issue_time`, `case.target_time` (timezone-aware UTC datetimes), `case.lead_hours` (float) and
    `case.case_id` are also available. Arrays are numpy float arrays; NaN means missing. History arrays
    are newest first: index 0 is the issue time, index k is k steps earlier.

    | key | type / shape | meaning |
    |---|---|---|
    | `series_id` | str | the id values joined by a vertical bar (`"all"` with no id columns); example below the table |
    | `ids` | dict str -> value | the id columns of this series: {ids} |
    | `lead` | int, steps | {", ".join(str(x) for x in leads)} |
    | `lead_h` | int, hours | the lead in whole hours (`lead * step_hours`, rounded); score tables are keyed by it |
    | `lead_hours` | float, hours | the lead in hours |
    | `step_hours` | float | the grid step in hours ({g.step_hours:g}) |
    | `y_hist` | ({problem.history},) | {yname} at the issue time and the {problem.history - 1} steps before it{fill_text} |
    | `y_hist_fill_steps` | ({problem.history},) | per `y_hist` value: 0 = really observed at that step, k = carried forward k steps from the last real observation, NaN = missing |
    | `y_last` | float | the newest real (not carried) observation in the history |
    | `y_last_age_steps` | int | its position in `y_hist` (0 when the value at the issue time exists) |
    | `t_hist_hours` | ({problem.history},) | age of each `y_hist` value in hours (`k * step_hours`) |
    | `target_hour`, `target_dow`, `target_dom`, `target_month` | int | UTC calendar of the target time: hour 0-23, day of week 0 = Monday, day of month, month 1-12 |
    | `cov_hist` | dict name -> ({problem.history},) | history of each covariate column ({covs}), same layout and filling as `y_hist`, never later than the issue time |
    | `cov_fill_steps` | dict name -> ({problem.history},) | like `y_hist_fill_steps`, for each covariate |
    | `known_future` | dict name -> float | value of each known-in-advance column ({known}) at the target time |
    | `peers` | list of up to {MAX_PEERS} dicts | other series: {peer_rule}. Each has `series_id`, `y_last` (newest value at or before the issue time, within the history), `y_last_age_steps`, and `y_hist7` (({PEER_HIST},), newest first) |

    Example `series_id`: `{g.series[0]}`.

    **Not available:** anything after `issue_time` (the series itself, covariates, peers), the truths of
    any window except train, the internet and files.
    {"`known_future` values are read AT the target time. They are declared by the person who set up this problem as fixed in advance (a calendar flag, a published schedule); they are not observations of the target period." if problem.known_future_cols else ""}

    ## Baselines and the seed (select window, mean loss)
    {{score_table}}

    - `last_value`: `y_last`.
    - `moving_average`: mean of the newest w values, w per lead chosen on train.
    {{seasonal_line}}- `drift`: `y_last` plus the average change per step over the history, times the lead.
    - `seed` (ridge): per lead, ridge least squares of `y - y_last` on lags 1-3, the mean of the newest 6
      values and of the whole history, and the values one day and one week before the target where the
      step and history allow, all minus `y_last`.

    ## Sandbox rules for a candidate
    One Python file defining a top-level `class Forecaster` with a `name` attribute,
    `fit(self, cases, truths)` (called once) and `predict(self, case) -> dict`. It runs in a fresh
    locked-down process: imports only from {imports}; no file, network or process access; no
    `open`, `eval`, `exec`, `getattr`/`setattr`, `globals` and similar; no names starting with `__`
    (except `__init__`); at most 400 lines and 30,000 characters; no literal with more than 200
    elements or 2,000 characters of strings. Time limit about 3 minutes for fit plus all predictions;
    resident memory about 1.5 GB. Every prediction must be a dict of finite floats containing `y`.

    ## How a change is accepted
    A candidate is fitted on train and predicts select and confirm. It is kept only if it beats the
    current best on the **select** window and does **not lose** on the **confirm** window. Both
    judgements use a paired block bootstrap: per-case loss differences are resampled by whole
    **{BLOCK_WORDS[problem.block_unit][0]}s** (series + {BLOCK_WORDS[problem.block_unit][1]} of the target time),
    because errors of one series close in time are correlated. The test window is scored once at the
    end, by a person.
    """)
    return (desc.replace("{score_table}", score_markdown(problem, table, "select"))
            .replace("{data_summary}", data_summary(g, problem.value_col, problem.notes))
            .replace("{seasonal_line}", seasonal_line))
