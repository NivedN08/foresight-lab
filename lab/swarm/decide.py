"""The acceptance rule, in code. No LLM has a say in it."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    p_min: float = 0.95                 # select: share of bootstrap resamples where the candidate wins
    min_rel_improvement: float = 0.003  # select: (incumbent - candidate) / incumbent mean loss
    confirm_max_diff: float = 0.0       # confirm: candidate - incumbent mean loss must be <= this


def relative_improvement(select) -> float:
    a, b = select.mean_a, select.mean_b
    if b is None or a is None or math.isnan(a) or math.isnan(b):
        return float("nan")
    if b == 0:
        return 0.0 if a == 0 else -float("inf")
    return (b - a) / abs(b)


def decide(ok: bool, select, confirm, th: Thresholds = Thresholds()) -> tuple[bool, str]:
    """Accept only if the evaluation ran, the candidate wins on select with
    P >= p_min and at least min_rel_improvement relative improvement, and is not
    worse on confirm. `select`/`confirm` are PairedResults with a = candidate,
    b = incumbent. Returns (accepted, reason)."""
    if not ok:
        return False, "evaluation failed"
    if select is None or confirm is None:
        return False, "no comparison available"
    rel = relative_improvement(select)
    p = select.p_a_better
    d_conf = confirm.diff.mean
    reasons = []
    if not (p >= th.p_min):
        reasons.append(f"select: P(candidate better) {p:.3f} < {th.p_min}")
    if not (rel >= th.min_rel_improvement):
        reasons.append(f"select: relative improvement {rel:.2%} < {th.min_rel_improvement:.2%}")
    if not (d_conf <= th.confirm_max_diff):
        reasons.append(f"confirm: mean difference {d_conf:+.4g} > {th.confirm_max_diff:+.4g} (worse)")
    if reasons:
        return False, "rejected: " + "; ".join(reasons)
    return True, (f"accepted: select P(better) {p:.3f}, relative improvement {rel:.2%}, "
                  f"confirm difference {d_conf:+.4g}")
