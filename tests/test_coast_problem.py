"""CoastWaterProblem: leak safety of every new input, splits, subsample, seed
and baselines. Synthetic data only, except one optional check on the cached
real splits (skipped when they have not been built)."""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from coast_fakes import resid_fn, tide_fn
from lab.core.task import Case
from lab.sandbox import lint, run_candidate
from lab.tasks.coast.models import DampedPersistence, SeedPhysics
from lab.tasks.coast.problem import (BASELINE_SOURCES, SEED_SOURCE, Buoy, CoastData, CoastWaterProblem,
                                     build_problem_case, build_splits, stratified_pick)
from lab.tasks.coast.water import RUN_DELAY_H, StationData, run_offset_days

START = pd.Timestamp("2026-10-01", tz="UTC")
END = pd.Timestamp("2026-10-20", tz="UTC")
ISSUE = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
STATIONS = [("S1", 36.0, -121.9, 0.0), ("S2", 37.8, -122.5, 1.0), ("S3", 38.9, -123.7, 2.0),
            ("S4", 40.8, -124.2, 3.0)]


def _weather(idx, offset_k: float, phase: float) -> pd.DataFrame:
    h = np.arange(len(idx))
    return pd.DataFrame({"pressure_msl": 1010 + 6 * np.sin(h / 17.0 + phase) + offset_k,
                         "wind_speed_10m": 5 + 3 * np.sin(h / 7.0 + phase + offset_k),
                         "wind_direction_10m": (250 + 40 * np.sin(h / 11.0 + offset_k)) % 360}, index=idx)


def make_station(sid, lat, lon, phase, obs=None, runs=None, hindsight=None) -> StationData:
    idx = pd.date_range(START, END, freq="6min", tz="UTC")
    tide = pd.Series([tide_fn(t) for t in idx], index=idx)
    if obs is None:
        obs = pd.Series([tide_fn(t) + resid_fn(t) + 0.02 * math.sin(phase + k / 40) for k, t in enumerate(idx)],
                        index=idx)
    hidx = pd.date_range(START, END + pd.Timedelta(days=3), freq="h", tz="UTC")
    if runs is None:
        runs = {k: _weather(hidx, float(k), phase) for k in (1, 2)}
    if hindsight is None:
        hindsight = _weather(hidx, 5.0, phase)
    return StationData(sid, sid, lat, lon, obs, tide, hindsight, runs)


def make_buoy(obs=None, runs=None) -> Buoy:
    idx = pd.date_range(START, END, freq="30min", tz="UTC")
    if obs is None:
        obs = pd.DataFrame({"WVHT": 2 + np.sin(np.arange(len(idx)) / 30.0),
                            "DPD": 12 + np.cos(np.arange(len(idx)) / 20.0)}, index=idx)
    hidx = pd.date_range(START, END + pd.Timedelta(days=3), freq="h", tz="UTC")
    if runs is None:
        runs = {k: pd.DataFrame({"wave_height": 2 + 0.5 * np.sin(np.arange(len(hidx)) / 9.0 + k)}, index=hidx)
                for k in (1, 2)}
    return Buoy("B1", 37.7, -122.8, obs, runs)


def make_data(stations=None, buoy=None) -> CoastData:
    stations = stations or {s[0]: make_station(*s) for s in STATIONS}
    buoy = buoy or make_buoy()
    return CoastData.from_parts(stations, {"B1": buoy}, START, END)


# ------------------------------------------------------------------ helpers
def deep_equal(a, b) -> bool:
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return isinstance(b, (list, tuple)) and len(a) == len(b) and all(deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, np.ndarray):
        return isinstance(b, np.ndarray) and a.shape == b.shape and np.array_equal(a, b, equal_nan=True)
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    return a == b


def run_published(valid: pd.DatetimeIndex, k: int) -> pd.DatetimeIndex:
    """Latest time the run behind each day-k value could have been published."""
    return valid - pd.Timedelta(hours=24 * k) + pd.Timedelta(hours=RUN_DELAY_H)


def corrupt_after(issue: datetime, seed: int = 0):
    """Every station, buoy and model run with everything after `issue` corrupted
    (values changed and some removed), plus the hindsight weather corrupted everywhere."""
    rng = np.random.default_rng(seed)
    t = pd.Timestamp(issue)
    stations = {}
    for sid, lat, lon, phase in STATIONS:
        good = make_station(sid, lat, lon, phase)
        obs = good.obs.copy()
        after = obs.index > t
        obs[after] = rng.normal(40, 10, after.sum())
        obs = obs.drop(obs.index[after][::5])
        runs = {}
        for k, df in good.weather_runs.items():
            df = df.copy()
            late = run_published(df.index, k) > t
            df.loc[late, "pressure_msl"] = rng.normal(900, 30, late.sum())
            df.loc[late, "wind_speed_10m"] = rng.uniform(30, 60, late.sum())
            df.loc[late, "wind_direction_10m"] = rng.uniform(0, 360, late.sum())
            runs[k] = df
        hind = good.weather.copy()
        hind["pressure_msl"] = rng.normal(950, 30, len(hind))
        hind["wind_speed_10m"] = rng.uniform(20, 50, len(hind))
        stations[sid] = make_station(sid, lat, lon, phase, obs=obs, runs=runs, hindsight=hind)
    b = make_buoy()
    obs = b.obs.copy()
    after = obs.index > t
    obs.loc[after, "WVHT"] = rng.uniform(8, 12, after.sum())
    obs.loc[after, "DPD"] = rng.uniform(2, 4, after.sum())
    runs = {}
    for k, df in b.model_runs.items():
        df = df.copy()
        late = run_published(df.index, k) > t
        df.loc[late, "wave_height"] = rng.uniform(9, 15, late.sum())
        runs[k] = df
    return make_data(stations, make_buoy(obs, runs))


# ---------------------------------------------------------------- leak tests
@pytest.mark.parametrize("lead", [1, 3, 6, 12, 24])
def test_corrupting_everything_after_issue_leaves_inputs_unchanged(lead):
    clean = make_data()
    c1 = build_problem_case(clean, "S2", ISSUE, lead)
    assert c1 is not None and c1.inputs["has_weather"]
    for key in ("lat", "lon", "r_hist72", "tide_fwd", "wx_p", "wx_u", "wx_v", "nbr", "buoy_hs_now",
                "buoy_tp_now", "wave_fc_hs_target"):
        assert key in c1.inputs
    assert np.isfinite(c1.inputs["buoy_hs_now"]) and np.isfinite(c1.inputs["wave_fc_hs_target"])
    assert all(np.isfinite(n["r_now"]) for n in c1.inputs["nbr"] if n["id"])

    bad = corrupt_after(ISSUE)
    c2 = build_problem_case(bad, "S2", ISSUE, lead)
    assert deep_equal(c1.inputs, c2.inputs)
    # the corruption is real: the truth at the target moved
    assert bad.feats["S2"].truth_at(c1.target_time) != pytest.approx(clean.feats["S2"].truth_at(c1.target_time))


def test_weather_comes_from_runs_published_before_issue():
    """dp_hpa, taux, tauy and wx_* use only the previous-run series; the hindsight
    series is never read (it is corrupted everywhere in corrupt_after)."""
    for lead in (1, 12, 24):
        k = run_offset_days(lead)
        assert 24 * k - RUN_DELAY_H >= lead
        assert k == (1 if lead <= 18 else 2)
    clean = make_data()
    c = build_problem_case(clean, "S2", ISSUE, 24)
    assert c.inputs["wx_run_age_h"] == 48.0
    f = clean.feats["S2"]
    df = f.sd.weather_runs[2]
    hours = pd.date_range(pd.Timestamp(ISSUE), periods=25, freq="h")
    assert np.allclose(c.inputs["wx_p"], df.loc[hours, "pressure_msl"].to_numpy(), atol=0.006)
    assert c.inputs["p_issue"] == pytest.approx(df.loc[hours[0], "pressure_msl"])
    assert c.inputs["p_target"] == pytest.approx(df.loc[hours[-1], "pressure_msl"])
    assert c.inputs["dp_hpa"] == pytest.approx(c.inputs["p_target"] - c.inputs["p_issue"])


@pytest.mark.parametrize("lead", [1, 6, 24])
def test_wx_and_tide_fwd_cover_issue_to_target_only(lead):
    data = make_data()
    c = build_problem_case(data, "S2", ISSUE, lead)
    for key in ("tide_fwd", "wx_p", "wx_u", "wx_v"):
        assert c.inputs[key].shape == (lead + 1,)
    want = [tide_fn(ISSUE + timedelta(hours=k)) for k in range(lead + 1)]
    assert np.allclose(c.inputs["tide_fwd"], want, atol=6e-5)
    assert c.inputs["tide_fwd"][0] == pytest.approx(c.inputs["tide_now"], abs=1e-4)
    assert c.inputs["tide_fwd"][-1] == pytest.approx(c.inputs["tide_target"], abs=1e-4)
    assert c.inputs["wx_p"][0] == pytest.approx(c.inputs["p_issue"], abs=0.006)
    assert c.inputs["wx_p"][-1] == pytest.approx(c.inputs["p_target"], abs=0.006)


def test_history_and_neighbours_layout():
    data = make_data()
    c = build_problem_case(data, "S2", ISSUE, 6)
    i = c.inputs
    assert i["r_hist72"].shape == (72,)
    assert i["r_hist72"][0] == pytest.approx(i["r_hist"][0], abs=1e-4)
    assert np.allclose(i["r_hist72"][:24], i["r_hist"], atol=6e-5)
    sides = [n["side"] for n in i["nbr"]]
    assert sides == ["south", "south", "north", "north"]
    assert [n["id"] for n in i["nbr"]] == ["S1", None, "S3", "S4"]
    assert i["nbr"][0]["dist_km"] < 250 and math.isnan(i["nbr"][1]["dist_km"])
    assert i["nbr"][2]["dist_km"] < i["nbr"][3]["dist_km"]
    assert all(n["r_hist24"].shape == (24,) for n in i["nbr"])
    assert np.isnan(i["nbr"][1]["r_now"]) and np.isnan(i["nbr"][1]["r_hist24"]).all()
    assert (i["lat"], i["lon"]) == (37.8, -122.5)
    assert i["buoy_id"] == "B1"


def test_buoy_report_older_than_three_hours_is_nan():
    b = make_buoy()
    obs = b.obs[b.obs.index <= pd.Timestamp(ISSUE) - pd.Timedelta(hours=4)]
    data = make_data(buoy=make_buoy(obs=obs))
    c = build_problem_case(data, "S2", ISSUE, 3)
    assert math.isnan(c.inputs["buoy_hs_now"]) and math.isnan(c.inputs["buoy_tp_now"])
    assert np.isfinite(c.inputs["wave_fc_hs_target"])


# ------------------------------------------------------------------ splits
SIZES = {"train": 600, "select": 200, "confirm": 150, "test": 200}


def _splits(seed=7):
    data = make_data()
    return build_splits(data, START.to_pydatetime() + timedelta(days=4), END.to_pydatetime(), SIZES, seed)


def test_splits_are_ordered_and_deterministic():
    a, b = _splits(), _splits()
    a.check_order()
    for w in ("train", "select", "confirm", "test"):
        wa, wb = getattr(a, w), getattr(b, w)
        assert len(wa) == SIZES[w]
        assert [c.case_id for c in wa.cases] == [c.case_id for c in wb.cases]
        assert [t["residual_m"] for t in wa.truths] == [t["residual_m"] for t in wb.truths]
        assert wa.blocks == [f"{c.inputs['station']}|{c.target_time:%Y-%m-%d}" for c in wa.cases]
        issues = [c.issue_time for c in wa.cases]
        assert issues == sorted(issues)
    order = [a.train, a.select, a.confirm, a.test]
    for x, y in zip(order, order[1:]):
        assert max(c.target_time for c in x.cases) < min(c.issue_time for c in y.cases)
    c = _splits(seed=8)
    assert [x.case_id for x in c.train.cases] != [x.case_id for x in a.train.cases]


def test_subsample_is_stratified_by_station_and_lead():
    groups = {("A", 1): list(range(0, 600)), ("A", 24): list(range(600, 900)), ("B", 1): list(range(900, 1000))}
    rng = np.random.default_rng(0)
    picked = stratified_pick(groups, 100, rng)
    assert len(picked) == 100 and len(set(picked)) == 100
    counts = Counter(next(k for k, v in groups.items() if i in v) for i in picked)
    assert counts == {("A", 1): 60, ("A", 24): 30, ("B", 1): 10}

    sp = _splits()
    full = build_splits(make_data(), START.to_pydatetime() + timedelta(days=4), END.to_pydatetime(),
                        {w: 10**9 for w in SIZES}, 7)       # no subsampling: the whole population
    for w in ("train", "select", "confirm", "test"):
        win, pop = getattr(sp, w), getattr(full, w)
        assert len(pop) > len(win)
        got = Counter((c.inputs["station"], c.inputs["lead_h"]) for c in win.cases)
        have = Counter((c.inputs["station"], c.inputs["lead_h"]) for c in pop.cases)
        assert set(got) == set(have) and len(have) == len(STATIONS) * 5
        for g, n in have.items():
            assert abs(got[g] - len(win) * n / len(pop)) <= 1.0     # proportional, up to rounding


# ------------------------------------------------------------ seed + baselines
def _synthetic_cases(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    cases, truths = [], []
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    wind = {"A": (0.0004, -0.0002), "B": (-0.0001, 0.0005)}
    for k in range(n):
        lead = [1, 6, 24][k % 3]
        m = rng.normal(0.2, 0.1)
        inp = {"station": ["A", "B"][k % 2], "lead_h": lead, "r_now": m + rng.normal(0, 0.15), "r_mean": m,
               "trend3": rng.normal(0, 0.05), "dp_hpa": rng.normal(0, 4), "taux": rng.normal(0, 40),
               "tauy": rng.normal(0, 40), "has_weather": bool(k % 7)}
        wx, wy = wind[inp["station"]]
        y = (m + 0.6 * (inp["r_now"] - m) - 0.01 * inp["dp_hpa"] + 0.3 * inp["trend3"]
             + wx * inp["taux"] + wy * inp["tauy"] + 0.02 + rng.normal(0, 0.01))
        cases.append(Case(f"c{k}", t0 + timedelta(hours=k), t0 + timedelta(hours=k + lead), inp))
        truths.append({"residual_m": y})
    return cases, truths


def test_candidate_sources_pass_the_lint():
    assert lint(SEED_SOURCE) == []
    assert set(BASELINE_SOURCES) == {"tide_table", "persistence", "damped_persistence"}
    for src in BASELINE_SOURCES.values():
        assert lint(src) == []
    p = CoastWaterProblem()
    assert p.seed_source() == SEED_SOURCE and p.baseline_sources() == BASELINE_SOURCES


def test_seed_source_matches_seed_physics():
    cases, truths = _synthetic_cases()
    train, ev = cases[:1000], cases[1000:]
    ref = SeedPhysics()
    ref.fit(train, truths[:1000])
    want = np.array([ref.predict(c)["residual_m"] for c in ev])
    res = run_candidate(SEED_SOURCE, train, truths[:1000], ev)
    assert res.ok, res.error
    got = np.array([p["residual_m"] for p in res.predictions])
    assert np.abs(got - want).mean() < 0.001          # within 1 mm on average
    assert np.abs(got - want).max() < 1e-6            # in fact the same numbers


def test_baseline_sources_behave():
    cases, truths = _synthetic_cases(600)
    train, ev = cases[:400], cases[400:]
    out = {n: run_candidate(s, train, truths[:400], ev) for n, s in BASELINE_SOURCES.items()}
    assert all(r.ok for r in out.values()), {n: r.error for n, r in out.items()}
    assert all(p["residual_m"] == 0.0 for p in out["tide_table"].predictions)
    assert [p["residual_m"] for p in out["persistence"].predictions] == [c.inputs["r_now"] for c in ev]
    ref = DampedPersistence()
    ref.fit(train, truths[:400])
    want = [ref.predict(c)["residual_m"] for c in ev]
    assert np.allclose([p["residual_m"] for p in out["damped_persistence"].predictions], want, atol=1e-9)


def test_losses_and_summary():
    p = CoastWaterProblem()
    cases = [Case(f"c{k}", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 1 + k, tzinfo=timezone.utc),
                  {"lead_h": [1, 3, 1][k]}) for k in range(3)]
    truths = [{"residual_m": 0.30}, {"residual_m": 0.10}, {"residual_m": 0.0}]
    preds = [{"residual_m": 0.32}, {"residual_m": 0.05}, {"other": 1.0}]
    loss = p.losses(preds, truths)
    assert loss[0] == pytest.approx(2.0) and loss[1] == pytest.approx(5.0) and np.isnan(loss[2])
    s = p.summarize(cases, preds, truths)
    assert s["mean_loss"] == pytest.approx(3.5) and s["n"] == 2 and s["n_missing"] == 1
    assert s["by_lead"]["1"] == {"mean_loss": pytest.approx(2.0), "n": 1}


def test_seed_matches_seed_physics_on_cached_real_splits():
    p = CoastWaterProblem(until="2026-11-13T22:00:00Z")
    path = p.cache_dir / f"{p._key(p.until)}.pkl"
    if not path.exists():
        pytest.skip("real splits not built (run the evalpool check once)")
    sp = p.splits()
    train, tt = sp.train.cases[:3000], sp.train.truths[:3000]
    ev = sp.select.cases[:1000]
    ref = SeedPhysics()
    ref.fit(train, tt)
    want = np.array([ref.predict(c)["residual_m"] for c in ev])
    res = run_candidate(SEED_SOURCE, train, tt, ev, time_limit_s=120)
    assert res.ok, res.error
    assert np.abs(np.array([q["residual_m"] for q in res.predictions]) - want).mean() < 0.001
