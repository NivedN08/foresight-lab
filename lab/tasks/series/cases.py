"""Build cases from a Grid. Every input is read at grid index <= the issue index,
except the target's calendar fields (pure arithmetic on the target time) and the
`known_future_cols` values at the target (by design: only for columns that are
genuinely fixed in advance).

`tests/test_series.py` corrupts everything after the issue time (target series,
covariates and peers) and checks that `Case.inputs` does not change.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from lab.core.task import Case
from lab.core.timeutil import to_iso

from .data import Grid

MAX_PEERS = 8
PEER_HIST = 7
MIN_CORR_POINTS = 20


def hist(row: np.ndarray, i: int, n: int) -> np.ndarray:
    """row[i], row[i-1], ..., row[i-n+1] (newest first); NaN before the start of the row."""
    idx = i - np.arange(n)
    return np.where(idx >= 0, row[np.clip(idx, 0, None)], np.nan)


def causal_fill(row: np.ndarray, i: int, n: int, max_fill: int) -> tuple[np.ndarray, np.ndarray]:
    """History of `row` at index i (newest first, like `hist`) with each missing value
    replaced by the last real observation at or before that position, if it is at most
    `max_fill` steps older. Reads only row[:i + 1]. Returns (values, fill_steps): fill_steps
    is 0 for a real observation, k for a value carried forward k steps, NaN where missing."""
    lo = max(0, i - n - max_fill + 1)
    seg = row[lo:i + 1]
    ok = np.isfinite(seg)
    last = np.maximum.accumulate(np.where(ok, np.arange(seg.size), -1))   # last observation <= each position
    pos = (i - lo) - np.arange(n)                                          # newest first, may be < 0
    valid = pos >= 0
    src = np.where(valid, last[np.clip(pos, 0, None)], -1)
    age = pos - src
    use = valid & (src >= 0) & (age <= max_fill)
    vals = np.where(use, seg[np.clip(src, 0, None)], np.nan)
    steps = np.where(use, age, np.nan).astype(float)
    return vals, steps


def newest(h: np.ndarray) -> tuple[float, int]:
    """(newest finite value, its age in steps) of a newest-first array; (nan, -1) if none."""
    ok = np.flatnonzero(np.isfinite(h))
    if ok.size == 0:
        return float("nan"), -1
    return float(h[ok[0]]), int(ok[0])


def choose_peers(grid: Grid, id_cols: Sequence[str], fit_end: int) -> tuple[list[list[int]], str]:
    """Up to MAX_PEERS other series for each series.

    Candidates are the series with the same value of the first id column. If a series
    has no such partner (or there are no id columns), every other series is a candidate.
    When there are at most MAX_PEERS candidates they are all used, in id order (no data
    involved). When there are more, the MAX_PEERS whose step-to-step changes correlate
    most with the series' own over grid indices [0, fit_end] are used: that choice uses
    data up to `fit_end` only (the end of the train window, or the end of the file for
    live forecasts), never anything later.
    """
    n = len(grid.series)
    first = id_cols[0] if id_cols else None
    corr = None

    def correlations():
        d = np.diff(grid.y[:, :max(fit_end, 0) + 1], axis=1)
        return pd.DataFrame(d.T).corr(min_periods=MIN_CORR_POINTS).to_numpy()

    out, used_corr, used_all = [], False, False
    for s in range(n):
        cand = [o for o in range(n) if o != s and first is not None
                and grid.ids[o].get(first) == grid.ids[s].get(first)]
        if not cand:
            cand = [o for o in range(n) if o != s]
            used_all = used_all or bool(cand)
        if len(cand) > MAX_PEERS:
            if corr is None:
                corr = correlations()
            c = np.nan_to_num(corr[s, cand], nan=-2.0)
            order = np.argsort(-c, kind="stable")
            cand = sorted(cand[j] for j in order[:MAX_PEERS])
            used_corr = True
        out.append(sorted(cand, key=lambda o: grid.series[o]))
    rule = (f"series with the same `{first}`" if first else "all other series")
    if first and used_all:
        rule += " (all other series when a series has no partner with the same value)"
    if used_corr:
        rule += (f"; where there are more than {MAX_PEERS}, the {MAX_PEERS} whose step-to-step "
                 "changes correlate most with the series' own on the train window")
    return out, rule


def build_case(grid: Grid, s: int, i: int, lead: int, peers: Sequence[int], history: int,
               cov_cols: Sequence[str] = (), known_cols: Sequence[str] = (), max_fill: int = 0) -> Case | None:
    """The case issued at grid index i for series s, lead `lead` steps. None when the
    series has no real observation in the last `history` steps. The target index may lie
    beyond the grid (live forecasts); the truth is not part of the case.

    With max_fill > 0, missing history values (of the series and the covariates) are
    carried forward from the last real observation, at most max_fill steps, using only
    data at or before each position (so never anything after the issue time); the
    `*_fill_steps` arrays say which values were carried. Peers are never filled."""
    y_last, age = newest(hist(grid.y[s], i, history))      # newest REAL observation
    y_hist, y_fill = causal_fill(grid.y[s], i, history, max_fill)
    if age < 0:
        return None
    issue = grid.time(i)
    target = grid.time(i + lead)
    step_h = grid.step_hours
    lead_hours = lead * step_h
    inp = {
        "series_id": grid.series[s],
        "ids": dict(grid.ids[s]),
        "lead": int(lead),
        "lead_h": int(round(lead_hours)),
        "lead_hours": float(lead_hours),
        "step_hours": float(step_h),
        "y_hist": y_hist,
        "y_hist_fill_steps": y_fill,
        "y_last": y_last,
        "y_last_age_steps": age,
        "t_hist_hours": np.arange(history, dtype=float) * step_h,
        "target_hour": target.hour,
        "target_dow": target.weekday(),
        "target_dom": target.day,
        "target_month": target.month,
        "cov_hist": {},
        "cov_fill_steps": {},
        "known_future": {c: _at(grid.known[c][s], i + lead) for c in known_cols},
        "peers": [],
    }
    for c in cov_cols:
        inp["cov_hist"][c], inp["cov_fill_steps"][c] = causal_fill(grid.cov[c][s], i, history, max_fill)
    for o in peers:
        h7 = hist(grid.y[o], i, PEER_HIST)
        v, a = newest(hist(grid.y[o], i, history))
        inp["peers"].append({"series_id": grid.series[o], "y_last": v, "y_last_age_steps": a, "y_hist7": h7})
    cid = f"{grid.series[s]}|{to_iso(issue)}|{lead}"
    return Case(cid, issue, target, inp)


def _at(row: np.ndarray, k: int) -> float:
    if 0 <= k < row.size:
        v = float(row[k])
        return v if math.isfinite(v) else float("nan")
    return float("nan")


def issue_mask(grid: Grid, s: int, history: int) -> np.ndarray:
    """Grid indices at which series s has a value within the last `history` steps."""
    n = grid.n_t
    ok = np.isfinite(grid.y[s])
    last = np.maximum.accumulate(np.where(ok, np.arange(n), -1))
    return (last >= 0) & (np.arange(n) - last < history)
