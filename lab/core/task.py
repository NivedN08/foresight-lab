"""The shape every forecasting problem takes in the lab.

A Case is one question asked at one moment: "given only what was known at
issue_time, what will the value be at target_time?". Everything in `inputs`
must have been knowable at issue_time. Truth is looked up separately and is
never placed inside a Case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class Case:
    case_id: str
    issue_time: datetime
    target_time: datetime
    inputs: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def lead_hours(self) -> float:
        return (self.target_time - self.issue_time).total_seconds() / 3600.0


@runtime_checkable
class Forecaster(Protocol):
    """A readable forecasting model. `fit` may estimate coefficients from past
    cases and their truths; `predict` must use only `case.inputs`."""

    name: str

    def fit(self, cases: Sequence[Case], truths: Sequence[dict]) -> None: ...

    def predict(self, case: Case) -> dict: ...


def time_split(cases: Sequence[Case], train: float = 0.6, validate: float = 0.2):
    """Split cases by target time into train / validation / test, oldest first.

    Splitting on target time (not issue time) guarantees that no truth used for
    fitting comes from a moment later than any validation or test target.
    """
    if not 0 < train < 1 or not 0 < validate < 1 or train + validate >= 1:
        raise ValueError("fractions must be positive and leave room for a test set")
    ordered = sorted(cases, key=lambda c: (c.target_time, c.case_id))
    if not ordered:
        return [], [], []
    t0, t1 = ordered[0].target_time, ordered[-1].target_time
    span = t1 - t0
    cut_a = t0 + span * train
    cut_b = t0 + span * (train + validate)
    tr = [c for c in ordered if c.target_time <= cut_a]
    va = [c for c in ordered if cut_a < c.issue_time and c.target_time <= cut_b]
    te = [c for c in ordered if cut_b < c.issue_time]
    return tr, va, te
