"""What the research lab needs from a forecasting problem.

A Problem hands the lab four time-ordered windows of cases:

    train    what a candidate may fit on
    select   later than train; a change is kept only if it wins here
    confirm  later than select; a kept change must not lose here
    test     latest; never shown to the lab, scored once by a person

and a way to turn predictions into per-case losses (lower is better). The lab
never sees truths for select, confirm or test: the evaluator holds them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

import numpy as np

from .task import Case


@dataclass
class Window:
    """Cases and their truths, same order. `blocks[i]` names the group case i
    belongs to (for example station + day) so that statistics can resample
    whole groups instead of pretending neighbouring hours are independent."""
    cases: list[Case]
    truths: list[dict]
    blocks: list[str] = field(default_factory=list)

    def __post_init__(self):
        if len(self.cases) != len(self.truths):
            raise ValueError("cases and truths differ in length")
        if self.blocks and len(self.blocks) != len(self.cases):
            raise ValueError("blocks and cases differ in length")

    def __len__(self) -> int:
        return len(self.cases)


@dataclass
class Splits:
    train: Window
    select: Window
    confirm: Window
    test: Window

    def check_order(self) -> None:
        """Every window must end before the next one begins."""
        order = [("train", self.train), ("select", self.select), ("confirm", self.confirm), ("test", self.test)]
        for (na, a), (nb, b) in zip(order, order[1:]):
            if not a.cases or not b.cases:
                continue
            last_target = max(c.target_time for c in a.cases)
            first_issue = min(c.issue_time for c in b.cases)
            if last_target >= first_issue:
                raise ValueError(f"{na} overlaps {nb}: {last_target} >= {first_issue}")


@runtime_checkable
class Problem(Protocol):
    name: str            # short id, e.g. "coast_water"
    title: str           # one plain sentence
    target_key: str      # key every prediction dict must contain
    loss_name: str       # what `losses` returns, with units, e.g. "absolute error, cm"

    def describe(self) -> str:
        """Markdown for the agents: what is being predicted, every field of
        Case.inputs with units and meaning, the loss, and the baselines with
        their scores. This is all the agents know about the problem."""
        ...

    def splits(self) -> Splits:
        """The four windows. Must be deterministic for a given data cache."""
        ...

    def losses(self, predictions: Sequence[dict], truths: Sequence[dict]) -> np.ndarray:
        """Per-case loss, lower is better; NaN where a case cannot be scored."""
        ...

    def summarize(self, cases: Sequence[Case], predictions: Sequence[dict], truths: Sequence[dict]) -> dict:
        """Headline numbers for people (overall and by lead)."""
        ...

    def seed_source(self) -> str:
        """Python source of the starting candidate (defines `class Forecaster`)."""
        ...

    def baseline_sources(self) -> dict[str, str]:
        """name -> candidate source for each baseline the lab is compared with."""
        ...
