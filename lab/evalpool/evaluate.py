"""Judge one candidate on a Problem's windows.

The candidate runs once in the sandbox (lab/sandbox): it is fitted on the train
cases with their truths and predicts the cases of the requested windows. The
child never receives a truth from an evaluation window; losses are computed
here, in the parent, from truths the child never saw.

The test window is refused unless the caller passes `allow_test=True`; only
`lab.experiments.final_test` does that.
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from lab.core.problem import Splits
from lab.core.stats import PairedResult, paired_block_bootstrap
from lab.sandbox import runner

EVAL_WINDOWS = ("select", "confirm")
ALL_WINDOWS = ("train", "select", "confirm", "test")


class HeldOutWindowRefused(ValueError):
    pass


@dataclass
class EvalResult:
    ok: bool
    error: str | None
    lint: list[str]
    duration_s: float
    protections: list[str]
    source_hash: str
    losses: dict[str, np.ndarray] = field(default_factory=dict)   # window -> per-case loss
    summary: dict[str, dict] = field(default_factory=dict)        # window -> summarize(...) + mean_loss
    name: str | None = None
    windows: tuple[str, ...] = ()


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8", "replace")).hexdigest()


def check_windows(windows: Sequence[str], allow_test: bool) -> tuple[str, ...]:
    windows = tuple(windows)
    if not windows:
        raise ValueError("no windows requested")
    for w in windows:
        if w not in ALL_WINDOWS or w == "train":
            raise ValueError(f"cannot evaluate on window {w!r}; choose from select, confirm, test")
    if len(set(windows)) != len(windows):
        raise ValueError("a window is listed twice")
    if "test" in windows and not allow_test:
        raise HeldOutWindowRefused("the test window is scored once, by a person, through "
                                "lab.experiments.final_test; pass allow_test=True only there")
    return windows


# One prepared payload per (splits object, windows): the encoded train cases,
# train truths and evaluation cases (never evaluation truths) are reused by
# every candidate evaluated on the same splits.
_PREPARED: dict[tuple[int, tuple[str, ...]], tuple[Splits, runner.PreparedCases]] = {}
_PREPARED_LOCK = threading.Lock()


def prepared_cases(splits: Splits, windows: tuple[str, ...]) -> runner.PreparedCases:
    key = (id(splits), windows)
    with _PREPARED_LOCK:
        hit = _PREPARED.get(key)
        if hit is not None and hit[0] is splits:
            return hit[1]
        eval_cases = [c for w in windows for c in getattr(splits, w).cases]
        prep = runner.prepare_cases(splits.train.cases, splits.train.truths, eval_cases)
        if len(_PREPARED) > 8:
            _PREPARED.clear()
        _PREPARED[key] = (splits, prep)
        return prep


def evaluate(problem, source: str, splits: Splits, windows=EVAL_WINDOWS, *, time_limit_s: float = 180,
             log_path=None, allow_test: bool = False, memory_limit_mb: int = 1536) -> EvalResult:
    windows = check_windows(windows, allow_test)
    h = source_hash(source)
    prep = prepared_cases(splits, windows)   # encoded once per splits and windows; not timed
    t0 = time.monotonic()
    res = runner.run_prepared(source, prep, time_limit_s=time_limit_s, memory_limit_mb=memory_limit_mb,
                              log_path=log_path)
    out = EvalResult(ok=res.ok, error=res.error, lint=list(res.lint), duration_s=0.0,
                     protections=list(res.protections), source_hash=h, name=res.name, windows=windows)
    if res.ok:
        preds = res.predictions
        pos = 0
        for w in windows:
            win = getattr(splits, w)
            p = preds[pos:pos + len(win)]
            pos += len(win)
            loss = np.asarray(problem.losses(p, win.truths), dtype=float)
            s = dict(problem.summarize(win.cases, p, win.truths))
            s["mean_loss"] = float(np.nanmean(loss)) if np.isfinite(loss).any() else float("nan")
            out.losses[w] = loss
            out.summary[w] = s
    out.duration_s = time.monotonic() - t0
    return out


def compare(a: EvalResult, b: EvalResult, splits: Splits, window: str, n_boot: int = 2000,
            seed: int = 0) -> PairedResult:
    """Paired block bootstrap of a's loss minus b's loss on one window, resampling
    the window's blocks (station-days). Negative diff means a is better."""
    if not (a.ok and b.ok):
        raise ValueError("both results must be ok to compare")
    if window not in a.losses or window not in b.losses:
        raise ValueError(f"both results need losses on {window!r}")
    blocks = getattr(splits, window).blocks
    if not blocks:
        raise ValueError(f"window {window!r} has no blocks")
    return paired_block_bootstrap(a.losses[window], b.losses[window], blocks, n_boot=n_boot, seed=seed)
