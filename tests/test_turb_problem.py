"""The turbulence lab problem: inputs, leaks, splits, scoring and candidate files."""
from __future__ import annotations

import importlib.util
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab.core.task import Case
from lab.sandbox import lint, run_candidate
from lab.tasks.turb import fields as F
from lab.tasks.turb.diagnostics import DIAGNOSTICS
from lab.tasks.turb.models import Ellrod1, SeedBlend
from lab.tasks.turb.problem import (BASELINE_SOURCES, MAX_CASES, SEED_SOURCE, TurbProblem, block_of, bracket,
                                    build_cases, build_splits, hour_features, render_description)

_spec = importlib.util.spec_from_file_location(
    "turb_synthetic", Path(__file__).parent / "fixtures" / "turb" / "synthetic.py")
syn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(syn)

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def deep_equal(a, b) -> bool:
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return np.array_equal(np.asarray(a), np.asarray(b), equal_nan=True)
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    return a == b


def _labelled(df):
    return df[df["code"] != 3].reset_index(drop=True)


def _data(days=2, per_hour=25, seed=4):
    fs = syn.fieldset(T0, days * 24 + 2, fetched_at=T0 + timedelta(days=days + 1))
    reps = _labelled(syn.reports(T0, T0 + timedelta(days=days), per_hour=per_hour, seed=seed))
    return fs, reps


# ----------------------------------------------------------------- inputs
def test_bracket_and_level_values():
    levels = F.LEVELS
    i, w = bracket(levels, np.array([150.0, 250.0, 376.0, 500.0, 120.0, 600.0]))
    assert i.tolist() == [0, 2, 3, 4, 0, 4]
    assert w[1] == pytest.approx(0.0) and w[3] == pytest.approx(1.0)
    assert w[4] == pytest.approx(0.0) and w[5] == pytest.approx(1.0)  # clamped at the ends


def test_hour_features_on_a_uniform_column():
    fs, _ = _data(days=1)
    one = fs.select(np.array([3]))
    for v in F.VARS:   # make every level horizontally uniform
        arr = getattr(one, v)
        arr[:] = arr[:, :, :1, :1]
    out = hour_features(one, np.array([38.0, 30.0]), np.array([-100.0, -80.0]), np.array([250.0, 330.0]))
    k = list(F.LEVELS).index(250)
    assert out["speed"][0] == pytest.approx(one.speed[0, k, 0, 0])
    assert np.allclose(out["prof_speed"][0], one.speed[0, :, 0, 0])
    assert np.allclose(out["deform"], 0.0, atol=1e-9) and np.allclose(out["speed_grad"], 0.0, atol=1e-9)
    assert out["above_hpa"].tolist() == [250.0, 300.0] and out["below_hpa"].tolist() == [300.0, 400.0]
    assert np.isnan(hour_features(one, np.array([38.0]), np.array([-100.0]), np.array([160.0]))["shear_above"][0])
    # direction from the interpolated wind agrees with the stored direction
    assert out["direction"][0] == pytest.approx(one.direction[0, k, 0, 0] % 360, abs=1e-6)


def test_case_layout():
    fs, reps = _data(days=1, per_hour=5)
    cases, truths, skipped = build_cases(reps, fs)
    assert skipped == 0 and len(cases) == len(reps)
    c = cases[0]
    assert c.issue_time == c.target_time.replace(minute=0, second=0)
    for k in ("lat", "lon", "flight_level", "pressure_hpa", "hour_utc", "solar_hour", "doy", "field_age_min",
              *DIAGNOSTICS, "direction", "u", "v", "shear_above", "shear_below", "shear_layer",
              "speed_grad", "above_ti1", "below_ri"):
        assert k in c.inputs, k
    for k in ("prof_speed", "prof_dir", "prof_temp_c", "prof_height_m", "levels_hpa"):
        assert c.inputs[k].shape == (6,)
    assert all(t["mog"] in (0, 1) for t in truths)
    assert len({c.case_id for c in cases}) == len(cases)


# ------------------------------------------------------------------- leaks
def test_shuffling_all_labels_leaves_inputs_unchanged():
    fs, reps = _data()
    a, ta, _ = build_cases(reps, fs)
    shuffled = reps.copy()
    shuffled["code"] = np.random.default_rng(0).permutation(shuffled["code"].to_numpy())
    b, tb, _ = build_cases(shuffled, fs)
    assert [c.case_id for c in a] == [c.case_id for c in b]
    assert all(deep_equal(x.inputs, y.inputs) for x, y in zip(a, b))
    assert [t["mog"] for t in ta] != [t["mog"] for t in tb]     # only the labels moved


def test_a_reports_inputs_do_not_depend_on_other_reports():
    fs, reps = _data(days=1)
    full, _, _ = build_cases(reps, fs)
    by_id = {c.case_id: c for c in full}
    for k in (0, len(reps) // 2, len(reps) - 1):
        alone, _, _ = build_cases(reps.iloc[[k]], fs)
        assert deep_equal(alone[0].inputs, by_id[alone[0].case_id].inputs)


def _corrupt(fs, mask):
    new = F.FieldSet(**{k: np.array(getattr(fs, k), copy=True) for k in
                        ("times", "avail", "levels", "lats", "lons", *F.VARS)})
    for v in F.VARS:
        getattr(new, v)[mask] = 777.0
    return new


def test_fields_after_the_report_hour_are_never_read():
    fs, reps = _data(days=1)
    hour = T0 + timedelta(hours=10)
    one_hour = reps[(reps["time"] >= pd.Timestamp(hour)) & (reps["time"] < pd.Timestamp(hour + timedelta(hours=1)))]
    assert len(one_hour) > 5
    a, _, _ = build_cases(one_hour, fs)
    later = fs.times > int(hour.timestamp())
    b, _, _ = build_cases(one_hour, _corrupt(fs, later))
    assert all(deep_equal(x.inputs, y.inputs) for x, y in zip(a, b))
    # the test is sensitive: corrupting the report's own hour changes the inputs
    c, _, _ = build_cases(one_hour, _corrupt(fs, fs.times == int(hour.timestamp())))
    assert any(not deep_equal(x.inputs, y.inputs) for x, y in zip(a, c))


# ------------------------------------------------------------------ splits
def test_splits_are_ordered_blocked_and_deterministic():
    fs, reps = _data(days=6, per_hour=15)
    cases, truths, _ = build_cases(reps, fs)
    end = T0 + timedelta(days=6)
    sp, info = build_splits(cases, truths, T0, end, seed=1)
    sp.check_order()
    for w in ("train", "select", "confirm", "test"):
        win = getattr(sp, w)
        assert len(win) == info[w]["used"] == info[w]["available"]         # no subsampling below the cap
        assert win.blocks == [block_of(c.target_time) for c in win.cases]
        assert [c.target_time for c in win.cases] == sorted(c.target_time for c in win.cases)
    assert block_of(datetime(2026, 11, 3, 13, 5, tzinfo=timezone.utc)) == "2026-11-03|12h"
    total = len(sp.train) + len(sp.select) + len(sp.confirm) + len(sp.test)
    assert total + info["dropped_at_cuts"] == len(cases)
    assert len(sp.train) / total == pytest.approx(0.60, abs=0.03)
    sp2, _ = build_splits(cases, truths, T0, end, seed=1)
    assert [c.case_id for c in sp2.select.cases] == [c.case_id for c in sp.select.cases]


def test_only_windows_above_the_cap_are_subsampled():
    fs, reps = _data(days=6, per_hour=15)
    cases, truths, _ = build_cases(reps, fs)
    sp, info = build_splits(cases, truths, T0, T0 + timedelta(days=6), seed=1, max_cases=300)
    for w in ("train", "select", "confirm", "test"):
        assert info[w]["used"] == min(info[w]["available"], 300)
    sp.check_order()
    assert MAX_CASES == 12000


# ----------------------------------------------------------------- scoring
def _cases(fls):
    t = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return [Case(f"c{k}", t, t + timedelta(minutes=k), {"flight_level": float(f)}) for k, f in enumerate(fls)]


def test_losses_and_summary():
    p = TurbProblem()
    truths = [{"mog": 1}, {"mog": 0}, {"mog": 0}, {"mog": 1}]
    preds = [{"risk": 0.8}, {"risk": 0.1}, {"risk": 0.3}, {"risk": 0.6}]
    loss = p.losses(preds, truths)
    assert loss == pytest.approx([4.0, 1.0, 9.0, 16.0])
    s = p.summarize(_cases([250, 250, 350, 400]), preds, truths)
    assert s["mean_loss"] == pytest.approx(7.5) and s["auc"] == pytest.approx(1.0)
    assert s["base_rate"] == 0.5 and s["n"] == 4 and s["positives"] == 2
    assert s["by_fl_band"]["FL200-269"]["n"] == 2 and s["by_fl_band"]["FL380+"]["positives"] == 1


@pytest.mark.parametrize("bad", [1.2, -0.01, float("nan"), float("inf"), None, True, "0.5"])
def test_an_invalid_probability_fails_the_whole_evaluation(bad):
    p = TurbProblem()
    truths = [{"mog": 1}, {"mog": 0}]
    preds = [{"risk": 0.5}, {"risk": bad} if bad is not None else {}]
    assert np.isnan(p.losses(preds, truths)).all()
    s = p.summarize(_cases([300, 300]), preds, truths)
    assert "failed" in s and math.isnan(s["mean_loss"])


# -------------------------------------------------------- candidate files
def _problem_cases(days=4):
    fs, reps = _data(days=days, per_hour=20, seed=11)
    cases, truths, _ = build_cases(reps, fs)
    return cases, truths


def test_candidate_sources_pass_the_lint():
    assert lint(SEED_SOURCE) == []
    assert set(BASELINE_SOURCES) == {"climatology", "ellrod1", "shear", "richardson"}
    for src in BASELINE_SOURCES.values():
        assert lint(src) == []
    p = TurbProblem()
    assert p.seed_source() == SEED_SOURCE and p.baseline_sources() == BASELINE_SOURCES


def _as_seedblend_case(c):
    return Case(c.case_id, c.issue_time, c.target_time, {"features": {k: c.inputs[k] for k in DIAGNOSTICS}})


def test_seed_source_matches_seedblend():
    cases, truths = _problem_cases()
    n = len(cases) * 2 // 3
    train, ev = cases[:n], cases[n:]
    ref = SeedBlend()
    ref.fit([_as_seedblend_case(c) for c in train], [{"label": float(t["mog"])} for t in truths[:n]])
    want = np.array([ref.predict(_as_seedblend_case(c))["risk"] for c in ev])
    res = run_candidate(SEED_SOURCE, train, truths[:n], ev)
    assert res.ok, res.error
    got = np.array([q["risk"] for q in res.predictions])
    assert np.abs(got - want).max() < 1e-4


def test_baseline_sources_behave():
    cases, truths = _problem_cases(days=3)
    n = len(cases) * 2 // 3
    train, ev = cases[:n], cases[n:]
    out = {name: run_candidate(s, train, truths[:n], ev) for name, s in BASELINE_SOURCES.items()}
    assert all(r.ok for r in out.values()), {k: r.error for k, r in out.items()}
    rate = np.mean([t["mog"] for t in truths[:n]])
    assert all(q["risk"] == pytest.approx(rate) for q in out["climatology"].predictions)
    for name in ("ellrod1", "shear", "richardson"):
        r = np.array([q["risk"] for q in out[name].predictions])
        assert ((r > 0) & (r < 1)).all()
    # ellrod1 is a monotone calibration of TI1: same ranking as the Ellrod1 model
    ti1 = np.array([c.inputs["ti1"] for c in ev])
    r = np.array([q["risk"] for q in out["ellrod1"].predictions])
    order = np.argsort(ti1)
    assert np.all(np.diff(r[order]) >= -1e-12) or np.all(np.diff(r[order]) <= 1e-12)
    ref = Ellrod1()
    assert np.corrcoef(r, [ref.predict(_as_seedblend_case(c))["risk"] for c in ev])[0, 1] > 0.5


def test_seed_matches_seedblend_on_cached_real_splits():
    p = TurbProblem(until="2026-11-13T00:00:00Z")
    if not (p.cache_dir / f"{p._key(p.until)}.pkl").exists():
        pytest.skip("real splits not built (run `python -m lab.tasks.turb.problem check` once)")
    sp = p.splits()
    train, tt = sp.train.cases[:4000], sp.train.truths[:4000]
    ev = sp.select.cases[:1000]
    ref = SeedBlend()
    ref.fit([_as_seedblend_case(c) for c in train], [{"label": float(t["mog"])} for t in tt])
    want = np.array([ref.predict(_as_seedblend_case(c))["risk"] for c in ev])
    res = run_candidate(SEED_SOURCE, train, tt, ev, time_limit_s=120)
    assert res.ok, res.error
    assert np.abs(np.array([q["risk"] for q in res.predictions]) - want).max() < 1e-4


# -------------------------------------------------------------- description
def test_description_mentions_the_caveats_and_every_input():
    fs, reps = _data(days=6, per_hour=10)
    cases, truths, _ = build_cases(reps, fs)
    sp, info = build_splits(cases, truths, T0, T0 + timedelta(days=6), seed=1)
    p = TurbProblem()
    p.info = info
    fake = {"select": {"mean_loss": 19.0, "auc": 0.6, "by_fl_band": {"FL200-269": {"auc": 0.55}}}}
    text = render_description(p, sp, {n: fake for n in ("climatology", "shear", "richardson", "ellrod1", "seed")})
    assert "hindsight" in text and "not a forecast test" in text and "biased" in text
    assert "terrain" in text.lower()
    for k in sp.train.cases[0].inputs:
        if not k.startswith(("above_", "below_")) or k in ("above_hpa", "below_hpa"):
            assert f"`{k}`" in text, k
    assert "| seed | 19.00 | 0.600 |" in text


def test_registry_knows_turb():
    from lab.evalpool.registry import get_problem
    assert isinstance(get_problem("turb"), TurbProblem)
