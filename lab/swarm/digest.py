"""The failure digest: where the incumbent loses, from aggregates only.

Computed by code from the incumbent's evaluation on the select window. It
shows means overall and per group (by lead and any other breakdown the
problem's summary offers) and a few quantiles of the per-case loss; it never
shows an individual case or truth.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

MAX_GROUPS = 24


def _f(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return "nan" if math.isnan(v) else f"{v:.4g}"


def _group_value(v: Any) -> tuple[float | None, str]:
    """A breakdown entry is either a number or a dict like {"mean_loss": .., "n": ..}."""
    if isinstance(v, dict):
        main = next((v[k] for k in ("mean_loss", "mean", "mae", "loss") if k in v), None)
        rest = ", ".join(f"{k} {_f(x)}" for k, x in v.items() if not isinstance(x, (dict, list)))
        try:
            return (float(main) if main is not None else None), rest
        except (TypeError, ValueError):
            return None, rest
    try:
        return float(v), _f(v)
    except (TypeError, ValueError):
        return None, str(v)


def worst_groups(summary: dict, n: int = 3) -> list[str]:
    """Labels like 'by_lead 24' for the groups with the highest loss in each breakdown."""
    out = []
    for key, val in summary.items():
        if not isinstance(val, dict) or not val:
            continue
        scored = [(g, _group_value(v)[0]) for g, v in val.items()]
        scored = [(g, s) for g, s in scored if s is not None and not math.isnan(s)]
        scored.sort(key=lambda gs: -gs[1])
        out += [f"{key} {g}" for g, _ in scored[:n]]
    return out


def failure_digest(result, window: str = "select", loss_name: str = "") -> str:
    summary = dict((getattr(result, "summary", None) or {}).get(window) or {})
    losses = (getattr(result, "losses", None) or {}).get(window)
    lines = [f"Window: {window}" + (f"; loss: {loss_name}" if loss_name else "")]
    scalars = {k: v for k, v in summary.items() if not isinstance(v, (dict, list))}
    if scalars:
        lines.append("Overall: " + ", ".join(f"{k} {_f(v)}" for k, v in scalars.items()))
    if losses is not None:
        arr = np.asarray(losses, dtype=float)
        ok = arr[~np.isnan(arr)]
        if ok.size:
            q = np.quantile(ok, [0.5, 0.9, 0.99])
            lines.append(f"Per-case loss: mean {_f(ok.mean())}, median {_f(q[0])}, 90th pct {_f(q[1])}, "
                         f"99th pct {_f(q[2])}, max {_f(ok.max())}; scored {ok.size} of {arr.size} cases")
            top = np.sort(ok)[::-1]
            k = max(1, ok.size // 10)
            lines.append(f"Share of total loss from the worst 10% of cases: {_f(top[:k].sum() / max(ok.sum(), 1e-12))}")
    for key, val in summary.items():
        if not isinstance(val, dict) or not val:
            continue
        rows = []
        items = list(val.items())
        for g, v in items[:MAX_GROUPS]:
            main, rest = _group_value(v)
            rows.append(f"  {g}: {rest if isinstance(v, dict) else _f(main)}")
        if len(items) > MAX_GROUPS:
            rows.append(f"  ... {len(items) - MAX_GROUPS} more groups")
        lines.append(f"Breakdown {key}:")
        lines += rows
    worst = worst_groups(summary)
    if worst:
        lines.append("Highest-loss groups: " + "; ".join(worst))
    return "\n".join(lines)
