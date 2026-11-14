"""Paired live scoreboard: lab forecasters against the hand-written models on identical cases."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lab.core.stats import paired_block_bootstrap
from lab.core.timeutil import to_iso
from lab.tasks.coast.live import _paired_scoreboard
from lab.tasks.coast.water import TASK

T0 = datetime(2026, 11, 14, 2, 18, tzinfo=timezone.utc)
MODELS = ["TideTable", "Persistence", "Lab:lab-a"]


def rec(model, station, issued, lead, err, graded=True, task=TASK):
    target = issued + timedelta(hours=lead)
    grade = {"truth": {"abs_error_cm": err}} if graded else None
    return {"task": task, "model": model, "issued_at": to_iso(issued), "target_time": to_iso(target),
            "meta": {"station": station, "lead_h": lead}, "grade": grade}


def case(station, issued, lead, errs, graded=None):
    """One record per model for one case; errs = {model: error}."""
    graded = graded or {}
    return [rec(m, station, issued, lead, e, graded.get(m, True)) for m, e in errs.items()]


def test_means_are_over_identical_cases_only():
    joined = []
    # three cases every model made
    for k, (tt, p, lab) in enumerate([(30.0, 2.0, 1.0), (32.0, 4.0, 3.0), (34.0, 6.0, 5.0)]):
        joined += case("A", T0 + timedelta(hours=k), 1, {"TideTable": tt, "Persistence": p, "Lab:lab-a": lab})
    # earlier hours with only the hand-written models (the lab had not started yet)
    for k in range(5):
        joined += case("A", T0 - timedelta(hours=10 + k), 1, {"TideTable": 99.0, "Persistence": 99.0})
    row = _paired_scoreboard(joined, TASK, MODELS)["Lab:lab-a"]["1"]
    assert row["n"] == 3
    assert row["mae_cm"] == {"TideTable": 32.0, "Persistence": 4.0, "Lab:lab-a": 3.0}


def test_a_case_missing_for_one_baseline_is_dropped_for_all():
    joined = []
    joined += case("A", T0, 3, {"TideTable": 30.0, "Persistence": 2.0, "Lab:lab-a": 1.0})
    # Persistence sealed this one but it is not graded yet; and another case Persistence never sealed
    joined += case("A", T0 + timedelta(hours=1), 3, {"TideTable": 50.0, "Persistence": 50.0, "Lab:lab-a": 50.0},
                   graded={"Persistence": False})
    joined += case("B", T0, 3, {"TideTable": 70.0, "Lab:lab-a": 70.0})
    row = _paired_scoreboard(joined, TASK, MODELS)["Lab:lab-a"]["3"]
    assert row["n"] == 1 and row["blocks"] == 1
    assert row["mae_cm"] == {"TideTable": 30.0, "Persistence": 2.0, "Lab:lab-a": 1.0}


def test_other_tasks_and_unlisted_models_are_ignored():
    joined = case("A", T0, 1, {"TideTable": 30.0, "Persistence": 2.0, "Lab:lab-a": 1.0})
    joined.append(rec("Persistence", "A", T0, 1, 500.0, task="coast_waves"))
    joined.append(rec("SomethingElse", "A", T0 + timedelta(hours=2), 1, 1.0))
    row = _paired_scoreboard(joined, TASK, MODELS)["Lab:lab-a"]["1"]
    assert row["n"] == 1 and row["mae_cm"]["Persistence"] == 2.0


def test_best_baseline_is_the_lowest_non_lab_mean_on_the_shared_cases():
    models = ["TideTable", "Persistence", "DampedPersistence", "Lab:lab-a", "Lab:lab-b"]
    joined = []
    for k, (p, d, a, b) in enumerate([(4.0, 3.0, 2.0, 1.0), (6.0, 5.0, 4.0, 0.5)]):
        joined += case("A", T0 + timedelta(hours=k), 6,
                       {"TideTable": 30.0, "Persistence": p, "DampedPersistence": d, "Lab:lab-a": a, "Lab:lab-b": b})
    out = _paired_scoreboard(joined, TASK, models)
    assert set(out) == {"Lab:lab-a", "Lab:lab-b"}
    va, vb = out["Lab:lab-a"]["6"]["vs_best"], out["Lab:lab-b"]["6"]["vs_best"]
    # the other lab model is better still, but only non-lab models count as "best"
    assert va["model"] == vb["model"] == "DampedPersistence"
    assert va["best_mae_cm"] == 4.0 and va["lab_mae_cm"] == 3.0 and va["diff_cm"] == -1.0
    assert vb["diff_cm"] == -3.25


def test_too_few_station_days_gives_no_interval():
    joined = []
    # 4 station-days: stations A and B on two UTC target dates
    for s in ("A", "B"):
        for day in range(2):
            joined += case(s, T0 + timedelta(days=day), 1, {"TideTable": 30.0, "Persistence": 3.0, "Lab:lab-a": 2.0})
    vs = _paired_scoreboard(joined, TASK, MODELS)["Lab:lab-a"]["1"]
    assert vs["blocks"] == 4
    v = vs["vs_best"]
    assert v["too_few_blocks"] is True and "low" not in v and "high" not in v
    assert v["model"] == "Persistence" and v["diff_cm"] == -1.0


def test_blocks_are_station_and_utc_date_of_the_target():
    # issued 23:30 UTC, 1 h ahead: the target falls on the next UTC day
    late = datetime(2026, 11, 14, 23, 30, tzinfo=timezone.utc)
    joined = case("A", late, 1, {"TideTable": 30.0, "Persistence": 3.0, "Lab:lab-a": 2.0})
    joined += case("A", late - timedelta(hours=2), 1, {"TideTable": 30.0, "Persistence": 3.0, "Lab:lab-a": 2.0})
    assert _paired_scoreboard(joined, TASK, MODELS)["Lab:lab-a"]["1"]["blocks"] == 2


def test_interval_comes_from_the_block_bootstrap():
    rng = np.random.default_rng(3)
    joined, lab, per, blocks = [], [], [], []
    for s in ("A", "B", "C"):
        for day in range(3):
            for hour in range(4):
                issued = T0 + timedelta(days=day, hours=hour)
                a, p = float(rng.uniform(1, 4)), float(rng.uniform(2, 5))
                joined += case(s, issued, 12, {"TideTable": 30.0, "Persistence": p, "Lab:lab-a": a})
                lab.append(a), per.append(p), blocks.append(f"{s}|{(issued + timedelta(hours=12)).date()}")
    row = _paired_scoreboard(joined, TASK, MODELS)["Lab:lab-a"]["12"]
    assert row["n"] == 36
    v = row["vs_best"]
    assert v["too_few_blocks"] is False
    pr = paired_block_bootstrap(np.array(lab), np.array(per), blocks)
    assert row["blocks"] == pr.diff.n
    assert v["diff_cm"] == pytest.approx(pr.diff.mean, abs=1e-3)
    assert v["low"] == pytest.approx(pr.diff.low, abs=1e-3) and v["high"] == pytest.approx(pr.diff.high, abs=1e-3)
    assert v["low"] <= v["diff_cm"] <= v["high"]


def test_empty_ledger():
    assert _paired_scoreboard([], TASK, MODELS) == {"Lab:lab-a": {}}
    assert _paired_scoreboard([], TASK, ["TideTable", "Persistence"]) == {}


def test_no_hand_written_models_gives_no_comparison():
    joined = case("A", T0, 1, {"Lab:lab-a": 1.0})
    row = _paired_scoreboard(joined, TASK, ["Lab:lab-a"])["Lab:lab-a"]["1"]
    assert row["n"] == 1 and row["vs_best"] is None


def test_another_lab_model_without_grades_does_not_empty_the_table():
    joined = case("A", T0, 1, {"TideTable": 30.0, "Persistence": 2.0, "Lab:lab-a": 1.0})
    out = _paired_scoreboard(joined, TASK, MODELS + ["Lab:new"])
    assert out["Lab:new"] == {}
    row = out["Lab:lab-a"]["1"]
    assert row["n"] == 1 and row["mae_cm"] == {"TideTable": 30.0, "Persistence": 2.0, "Lab:lab-a": 1.0}
