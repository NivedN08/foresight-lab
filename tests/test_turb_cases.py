"""Leak test: a Case's inputs must not change when everything that became
known after its issue time is corrupted."""
import importlib.util
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab.tasks.turb import fields as F
from lab.tasks.turb.cases import build_cases

_spec = importlib.util.spec_from_file_location(
    "turb_synthetic", Path(__file__).parent / "fixtures" / "turb" / "synthetic.py")
syn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(syn)


def _chunks(issue):
    start = issue - timedelta(hours=24)
    past = syn.fieldset(start, 30, fetched_at=issue + timedelta(hours=5))        # stitched past values
    early = syn.fieldset(issue - timedelta(hours=3), 12, fetched_at=issue - timedelta(hours=3), bump=2.0)
    later = syn.fieldset(issue, 12, fetched_at=issue + timedelta(minutes=40), bump=7.0)  # a newer download
    return [past, early, later]


def _corrupt_after(chunks, issue):
    """Copy of the chunks with every value available after `issue` replaced by garbage."""
    out = []
    t = int(issue.timestamp())
    for c in chunks:
        bad = c.avail > t
        new = F.FieldSet(**{k: np.array(getattr(c, k), copy=True) for k in
                            ("times", "avail", "levels", "lats", "lons", *F.VARS)})
        for v in F.VARS:
            getattr(new, v)[bad] = 1e6 if v != "direction" else 45.0
        out.append(new)
    return out


@pytest.mark.filterwarnings("ignore::RuntimeWarning")  # the garbage values overflow on purpose
def test_corrupting_the_future_leaves_inputs_unchanged():
    issue = syn.T0.replace(minute=0)
    reps = syn.reports(issue, issue + timedelta(minutes=59), per_hour=40)
    chunks = _chunks(issue)
    cases, truths, skipped = build_cases(reps, chunks)
    assert len(cases) == len(reps) and skipped == 0
    assert all(c.issue_time == issue and c.target_time >= issue for c in cases)

    # corrupt every field value that became available after the issue time, and all later reports
    later_reports = syn.reports(issue + timedelta(hours=1), issue + timedelta(hours=6), seed=9)
    cases2, _, _ = build_cases(pd.concat([reps, later_reports]), _corrupt_after(chunks, issue))
    by_id = {c.case_id: c for c in cases2}
    for c in cases:
        assert by_id[c.case_id].inputs == c.inputs


def test_the_leak_test_is_sensitive():
    """Corrupting values that WERE available at the issue time does change inputs."""
    issue = syn.T0.replace(minute=0)
    reps = syn.reports(issue, issue + timedelta(minutes=59), per_hour=10)
    chunks = _chunks(issue)
    cases, _, _ = build_cases(reps, chunks)
    t = int(issue.timestamp())
    past = chunks[0]  # the stitched past values are the most recent available ones
    past.speed[past.avail <= t] += 30.0
    cases2, _, _ = build_cases(reps, chunks)
    assert any(a.inputs != b.inputs for a, b in zip(cases, cases2))


def test_reports_without_available_fields_are_skipped():
    issue = syn.T0.replace(minute=0)
    reps = syn.reports(issue, issue + timedelta(minutes=59), per_hour=5)
    future = syn.fieldset(issue, 6, fetched_at=issue)
    future.avail[:] = int((issue + timedelta(minutes=20)).timestamp())  # all known only after the issue time
    only_future = [future]
    cases, _, skipped = build_cases(reps, only_future)
    assert cases == [] and skipped == len(reps)
