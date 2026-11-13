"""Small, dependency-light statistics used to decide whether one forecaster
really beats another."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Interval:
    mean: float
    low: float
    high: float
    n: int


def bootstrap_ci(values, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> Interval:
    """Mean of `values` with a percentile bootstrap confidence interval."""
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if v.size == 0:
        return Interval(float("nan"), float("nan"), float("nan"), 0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, v.size, size=(n_boot, v.size))
    means = v[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return Interval(float(v.mean()), float(lo), float(hi), int(v.size))


@dataclass(frozen=True)
class PairedResult:
    mean_a: float
    mean_b: float
    diff: Interval          # mean of (a - b); negative means a has lower error
    p_a_better: float       # share of bootstrap resamples in which a's mean error is lower
    n: int


def paired_bootstrap(errors_a, errors_b, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> PairedResult:
    """Compare two forecasters on the SAME cases (errors are per-case, lower is better)."""
    a = np.asarray(errors_a, dtype=float)
    b = np.asarray(errors_b, dtype=float)
    if a.shape != b.shape:
        raise ValueError("paired comparison needs the same cases for both forecasters")
    keep = ~(np.isnan(a) | np.isnan(b))
    a, b = a[keep], b[keep]
    if a.size == 0:
        nan = float("nan")
        return PairedResult(nan, nan, Interval(nan, nan, nan, 0), nan, 0)
    d = a - b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, d.size, size=(n_boot, d.size))
    means = d[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return PairedResult(
        mean_a=float(a.mean()),
        mean_b=float(b.mean()),
        diff=Interval(float(d.mean()), float(lo), float(hi), int(d.size)),
        p_a_better=float((means < 0).mean()),
        n=int(d.size),
    )


def auc(scores, labels) -> float:
    """Area under the ROC curve (probability a positive outranks a negative)."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    keep = ~np.isnan(s)
    s, y = s[keep], y[keep]
    n_pos, n_neg = int(y.sum()), int((1 - y).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(s.size, dtype=float)
    sorted_s = s[order]
    i = 0
    while i < s.size:  # average ranks over ties
        j = i
        while j + 1 < s.size and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))
