"""Candidate sources for SeriesProblem: the seed and the baselines.

Each is one Python file defining `class Forecaster`; they run only in the sandbox
(numpy and the standard library's math). They read `case.inputs` keys documented in
SeriesProblem.describe(): y_hist (newest first, NaN where missing), y_last,
lead, lead_hours, step_hours.
"""
from __future__ import annotations

SEED_SOURCE = '''\
"""Seed: per-lead ridge regression on the last value, lags, moving averages and seasonal lags.

Everything is measured relative to the last value: the target is y - y_last and each
feature is (something from the history) - y_last, so one model serves series with very
different levels. Features:
    lags 1, 2, 3            y_hist[1], y_hist[2], y_hist[3]
    short moving average    mean of the newest 6 values
    long moving average     mean of the whole history
    seasonal lags           the value one day and one week before the target, when the
                            step divides the season, the season is at least 2 steps and
                            that value lies inside the history
Missing values become y_last (feature 0). Per lead, least squares with a small ridge on
standardised columns and an unpenalised intercept. numpy only.
"""
import math

import numpy as np

LAGS = (1, 2, 3)
SHORT = 6
SEASON_HOURS = (24.0, 168.0)
RIDGE = 1e-3


def mean_or(values, default):
    ok = values[np.isfinite(values)]
    return float(ok.mean()) if ok.size else default


def seasonal_index(season_h, lead_h, step_h, n):
    """Position in y_hist of the value one season before the target, or None."""
    steps = season_h / step_h
    if steps < 2 or abs(steps - round(steps)) > 1e-9:
        return None
    m = math.ceil(lead_h / season_h - 1e-9)
    k = int(round((m * season_h - lead_h) / step_h))
    return k if 0 <= k < n else None


def features(inp):
    h = np.asarray(inp["y_hist"], dtype=float)
    last = float(inp["y_last"])
    row = [h[k] if k < h.size else np.nan for k in LAGS]
    row.append(mean_or(h[:SHORT], last))
    row.append(mean_or(h, last))
    for s in SEASON_HOURS:
        k = seasonal_index(s, float(inp["lead_hours"]), float(inp["step_hours"]), h.size)
        if k is not None:
            row.append(h[k])
    x = np.array(row, dtype=float) - last
    x[~np.isfinite(x)] = 0.0
    return x


class Forecaster:
    name = "seed_ridge"

    def __init__(self):
        self.coef = {}

    def fit(self, cases, truths):
        rows = {}
        for c, t in zip(cases, truths):
            y = float(t["y"]) - float(c.inputs["y_last"])
            if math.isfinite(y):
                rows.setdefault(int(c.inputs["lead"]), []).append((features(c.inputs), y))
        for lead, data in rows.items():
            X = np.array([x for x, _ in data])
            y = np.array([v for _, v in data])
            mx, my = X.mean(axis=0), y.mean()
            Xc = X - mx
            scale = Xc.std(axis=0) + 1e-12
            Xs = Xc / scale
            A = Xs.T @ Xs + RIDGE * len(y) * np.eye(X.shape[1])
            b = np.linalg.solve(A, Xs.T @ (y - my)) / scale
            self.coef[lead] = (b, float(my - mx @ b))

    def predict(self, case):
        inp = case.inputs
        last = float(inp["y_last"])
        fit = self.coef.get(int(inp["lead"]))
        if fit is None:
            return {"y": last}
        b, b0 = fit
        x = features(inp)
        if x.size != b.size:
            return {"y": last}
        return {"y": float(last + b0 + x @ b)}
'''

LAST_VALUE = '''\
"""The newest value in the history stays as it is (persistence)."""


class Forecaster:
    name = "last_value"

    def fit(self, cases, truths):
        pass

    def predict(self, case):
        return {"y": float(case.inputs["y_last"])}
'''

MOVING_AVERAGE = '''\
"""Mean of the newest w values; w chosen per lead on the train window (smallest mean
absolute error among a few windows)."""
import numpy as np

WINDOWS = (2, 3, 4, 6, 8, 12, 24, 48)


def window_mean(h, w, default):
    v = h[:w]
    v = v[np.isfinite(v)]
    return float(v.mean()) if v.size else default


class Forecaster:
    name = "moving_average"

    def __init__(self):
        self.w = {}

    def fit(self, cases, truths):
        err = {}
        for c, t in zip(cases, truths):
            h = np.asarray(c.inputs["y_hist"], dtype=float)
            lead = int(c.inputs["lead"])
            for w in WINDOWS:
                if w <= h.size:
                    e = abs(window_mean(h, w, c.inputs["y_last"]) - float(t["y"]))
                    s = err.setdefault(lead, {})
                    s[w] = s.get(w, 0.0) + e
        for lead, s in err.items():
            self.w[lead] = min(s, key=lambda w: (s[w], w))

    def predict(self, case):
        h = np.asarray(case.inputs["y_hist"], dtype=float)
        w = self.w.get(int(case.inputs["lead"]), 1)
        return {"y": window_mean(h, w, float(case.inputs["y_last"]))}
'''

SEASONAL_NAIVE = '''\
"""The value one season (SEASON steps) before the target; when the lead is longer than
a season, the same phase in the most recent season inside the history. Falls back to
the last value when that value is missing."""
import math

SEASON = {season}


class Forecaster:
    name = "seasonal_naive"

    def fit(self, cases, truths):
        pass

    def predict(self, case):
        h = case.inputs["y_hist"]
        lead = int(case.inputs["lead"])
        k = math.ceil(lead / SEASON) * SEASON - lead
        if k < len(h) and math.isfinite(float(h[k])):
            return {{"y": float(h[k])}}
        return {{"y": float(case.inputs["y_last"])}}
'''

DRIFT = '''\
"""Last value plus the average change per step over the history, times the lead:
y_last + lead * (y_last - oldest value) / (steps between them)."""
import numpy as np


class Forecaster:
    name = "drift"

    def fit(self, cases, truths):
        pass

    def predict(self, case):
        h = np.asarray(case.inputs["y_hist"], dtype=float)
        ok = np.flatnonzero(np.isfinite(h))
        last = float(case.inputs["y_last"])
        if ok.size < 2:
            return {"y": last}
        newest, oldest = ok[0], ok[-1]
        slope = (h[newest] - h[oldest]) / float(oldest - newest)
        return {"y": float(last + slope * (int(case.inputs["lead"]) + newest))}
'''


def season_steps(step_hours: float) -> int | None:
    """24 for an hourly step, 7 for a daily step, otherwise None (no seasonal baseline)."""
    if abs(step_hours - 1.0) < 1e-9:
        return 24
    if abs(step_hours - 24.0) < 1e-9:
        return 7
    return None


def baseline_sources(step_hours: float) -> dict[str, str]:
    out = {"last_value": LAST_VALUE, "moving_average": MOVING_AVERAGE}
    season = season_steps(step_hours)
    if season:
        out["seasonal_naive"] = SEASONAL_NAIVE.format(season=season)
    out["drift"] = DRIFT
    return out
