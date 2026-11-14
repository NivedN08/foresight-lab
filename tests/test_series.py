"""SeriesProblem: reading, step inference, gaps, case layout and leak safety, splits,
baselines and seed, describe(), the registry entry and the live seal/grade command.
Small synthetic tables only (written to tmp_path); no network."""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab.core.ledger import SealedLedger
from lab.core.task import Case
from lab.evalpool.evaluate import evaluate
from lab.sandbox import lint, run_candidate
from lab.tasks.series import candidates as C
from lab.tasks.series.cases import MAX_PEERS, build_case, choose_peers
from lab.tasks.series.data import build_grid, infer_step, parse_times, read_table
from lab.tasks.series.live import grade_live, seal_live
from lab.tasks.series.problem import SeriesProblem, stratified_pick, window_bounds

FIX = Path(__file__).parent / "fixtures" / "series"
T0 = pd.Timestamp("2026-01-05", tz="UTC")       # a Monday


# ------------------------------------------------------------------ fixtures
def hourly_frame(n_types=2, n_regions=3, days=60, seed=0, gaps=True, season_amp=0.3, start=T0):
    """Prices with a strong daily cycle (planted), a weekly effect, a shared shock per type,
    noise, and a few gaps."""
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=days * 24, freq="h")
    hour = t.hour.to_numpy()
    rows = []
    for a in range(n_types):
        shock = np.cumsum(rng.normal(0, 0.01, len(t)))
        for b in range(n_regions):
            base = 1.0 + a + 0.2 * b
            y = base * (1 + season_amp * np.sin(2 * np.pi * (hour + 3 * b) / 24)) + shock + rng.normal(0, 0.02, len(t))
            keep = np.ones(len(t), bool)
            if gaps:
                keep[rng.random(len(t)) < 0.02] = False
                g0 = rng.integers(48, len(t) - 20)
                keep[g0:g0 + 6] = False
            rows.append(pd.DataFrame({"ts": t[keep], "gpu": f"G{a}", "region": f"R{b}", "price": y[keep],
                                      "units": 50 - 10 * np.sin(2 * np.pi * hour[keep] / 24),
                                      "weekend": (t[keep].day_of_week >= 5).astype(int)}))
    return pd.concat(rows, ignore_index=True)


def daily_frame(n_series=12, days=420, seed=1):
    rng = np.random.default_rng(seed)
    t = pd.date_range(T0, periods=days, freq="D")
    dow = t.day_of_week.to_numpy()
    rows = []
    for s in range(n_series):
        y = 2.0 + 0.3 * s + 0.25 * (dow >= 5) + np.cumsum(rng.normal(0, 0.02, days)) + rng.normal(0, 0.02, days)
        rows.append(pd.DataFrame({"date": t.strftime("%Y-%m-%d"), "sku": f"S{s:02d}", "price": y}))
    return pd.concat(rows, ignore_index=True)


def write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    return path


def hourly_problem(tmp_path, df=None, **kw) -> SeriesProblem:
    path = write(df if df is not None else hourly_frame(), tmp_path / "hourly.csv")
    args = dict(time_col="ts", value_col="price", id_cols=("gpu", "region"), covariate_cols=("units",),
                known_future_cols=("weekend",), history=30, max_cases=600, cache_dir=tmp_path / "cache",
                log=lambda *a: None)
    args.update(kw)
    return SeriesProblem(path, **args)


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


# ------------------------------------------------------- reading and the grid
def test_step_inference():
    h = np.arange(0, 100) * 3600 * 10**9
    assert infer_step([h])[0] == pd.Timedelta("1h")
    jitter = h + np.random.default_rng(0).integers(-120, 120, h.size) * 10**9   # +-2 minutes
    assert infer_step([jitter])[0] == pd.Timedelta("1h")
    assert infer_step([np.arange(0, 50) * 86400 * 10**9])[0] == pd.Timedelta("1D")
    assert infer_step([np.arange(0, 50) * 900 * 10**9])[0] == pd.Timedelta("15min")
    odd = np.arange(0, 50) * 5000 * 10**9                                    # 83 min: no round step
    assert infer_step([odd])[0] == pd.Timedelta(seconds=5000)
    with pytest.raises(ValueError):
        infer_step([np.array([0])])


def test_reader_handles_messy_csv_and_buckets_to_the_step():
    df = read_table(FIX / "irregular.csv")          # semicolons, a comment line, "$1,000.00", offsets
    notes = []
    g = build_grid(df, time_col="when", value_col="price", id_cols=("provider", "gpu"), notes=notes)
    assert g.step == pd.Timedelta("1h") and g.origin == pd.Timestamp("2026-03-01", tz="UTC")
    assert g.series == ["acme|A100", "zen|A100"]
    assert g.ids[0] == {"provider": "acme", "gpu": "A100"}
    a = g.y[0]
    # value at t = mean of observations in (t - 1 h, t]: 01:02, 01:58 and 02:00 all belong to 02:00
    assert a[0] == pytest.approx(1.0) and math.isnan(a[1])
    assert a[2] == pytest.approx((1.10 + 1.20 + 1.40) / 3)
    assert a[3] == pytest.approx(1000.0)            # "$1,000.00"
    assert a[7] == pytest.approx(2.0)               # 09:00+02:00 is 07:00 UTC
    assert math.isnan(a[8])                         # "unknown"
    assert np.allclose(g.y[1][:5], [0.90, 0.95, 1.05, 1.15, 1.25])
    text = " ".join(notes)
    assert "unreadable timestamp" in text and "not numbers" in text and "step inferred" in text


def test_gaps_stay_nan_unless_a_short_fill_is_asked_for():
    t = pd.date_range(T0, periods=20, freq="h")
    keep = np.r_[0:5, 10:20]                        # a 5-step gap (indices 5..9)
    df = pd.DataFrame({"ts": t[keep], "v": np.arange(20.0)[keep]})
    g0 = build_grid(df, time_col="ts", value_col="v")
    assert np.isnan(g0.y[0, 5:10]).all() and g0.y[0, 4] == 4.0 and g0.y[0, 10] == 10.0
    g2 = build_grid(df, time_col="ts", value_col="v", max_fill_steps=2)
    assert list(g2.y[0, 5:7]) == [4.0, 4.0] and np.isnan(g2.y[0, 7:10]).all()
    # nothing is filled after the last value of a series (only gaps with data on both sides)
    df2 = pd.concat([df, pd.DataFrame({"ts": t[:20], "v": 1.0}).assign(id="b")]).fillna({"id": "a"})
    df2 = df2[~((df2["id"] == "a") & (df2["ts"] > t[12]))]
    g3 = build_grid(df2, time_col="ts", value_col="v", id_cols=("id",), max_fill_steps=3)
    assert np.isnan(g3.y[0, 13:]).all()


def test_parse_times_reads_unix_seconds_and_naive_text_as_utc():
    s = parse_times(pd.Series([1767225600, 1767229200]))
    assert s.iloc[0] == pd.Timestamp("2026-01-01", tz="UTC") and s.iloc[1].hour == 1
    n = parse_times(pd.Series(["2026-01-01 05:00"]))
    assert n.iloc[0] == pd.Timestamp("2026-01-01 05:00", tz="UTC")


def test_parquet_without_pyarrow_says_what_to_do(tmp_path):
    try:
        import pyarrow  # noqa: F401
        pytest.skip("pyarrow installed: Parquet is read directly")
    except ImportError:
        pass
    p = tmp_path / "x.parquet"
    p.write_bytes(b"PAR1")
    with pytest.raises(RuntimeError, match="pyarrow"):
        read_table(p)


# --------------------------------------------------------- cases and leaks
def test_case_layout(tmp_path):
    p = hourly_problem(tmp_path, leads=(1, 3, 7))
    g = p.grid()
    peers, rule = choose_peers(g, p.id_cols, 400)
    s, i = 1, 300
    c = build_case(g, s, i, 3, peers[s], 30, ("units",), ("weekend",))
    x = c.inputs
    assert c.issue_time == g.time(i) and c.target_time == g.time(i + 3) and c.lead_hours == 3.0
    assert (x["lead"], x["lead_h"], x["lead_hours"], x["step_hours"]) == (3, 3, 3.0, 1.0)
    assert x["y_hist"].shape == (30,) and np.array_equal(x["y_hist"], g.y[s, i - np.arange(30)], equal_nan=True)
    assert np.array_equal(x["t_hist_hours"], np.arange(30.0))
    k = np.flatnonzero(np.isfinite(x["y_hist"]))[0]
    assert x["y_last"] == x["y_hist"][k] and x["y_last_age_steps"] == k
    tt = c.target_time
    assert (x["target_hour"], x["target_dow"], x["target_dom"], x["target_month"]) == \
        (tt.hour, tt.weekday(), tt.day, tt.month)
    assert x["cov_hist"]["units"].shape == (30,)
    assert x["known_future"]["weekend"] == float(tt.weekday() >= 5) or math.isnan(x["known_future"]["weekend"])
    assert x["ids"] == {"gpu": "G0", "region": "R1"} and x["series_id"] == "G0|R1"
    assert [q["series_id"] for q in x["peers"]] == ["G0|R0", "G0|R2"]      # same first id, by id
    assert all(q["y_hist7"].shape == (7,) for q in x["peers"])
    assert "gpu" in rule
    # before the start of the data the history is NaN
    c0 = build_case(g, s, 2, 1, peers[s], 30)
    assert np.isnan(c0.inputs["y_hist"][3:]).all()


def corrupted_copy(df: pd.DataFrame, after: pd.Timestamp, time_col="ts", seed=0, drop_after=None) -> pd.DataFrame:
    """Every value, covariate and known-future column of every series changed after `after`,
    and some rows later than `drop_after` (default `after`) removed (the last rows kept so the
    data still ends at the same time)."""
    rng = np.random.default_rng(seed)
    bad = df.copy()
    t = pd.to_datetime(bad[time_col], utc=True)
    late = (t > after).to_numpy()
    for col in bad.columns:
        if col in (time_col, "gpu", "region", "sku"):
            continue
        bad[col] = bad[col].astype(float)
        bad.loc[late, col] = rng.normal(100, 30, late.sum())
    later = (t > (drop_after if drop_after is not None else after)).to_numpy()
    drop = np.flatnonzero(later)[:-50:7]
    return bad.drop(bad.index[drop])


@pytest.mark.parametrize("window", ["train", "select"])
def test_corrupting_everything_after_issue_leaves_inputs_unchanged(tmp_path, window):
    """Target series, covariates and peers. Known-future columns are corrupted only after
    the target (they are read AT the target by design)."""
    df = hourly_frame()
    p1 = hourly_problem(tmp_path, df)
    g1 = p1.grid()
    fit_end = int(math.floor(window_bounds(g1.end_index)[0]))
    i = 200 if window == "train" else fit_end + 40
    issue = g1.time(i)
    lead_max = 7
    good = df.copy()
    bad = corrupted_copy(df, pd.Timestamp(issue), drop_after=pd.Timestamp(issue) + pd.Timedelta(hours=lead_max))
    # known-future values are allowed up to the target time: restore them there
    bt = pd.to_datetime(bad["ts"], utc=True)
    keep_known = (bt <= pd.Timestamp(issue) + pd.Timedelta(hours=lead_max)).to_numpy()
    restored = bad.loc[keep_known, ["ts", "gpu", "region"]].merge(good, on=["ts", "gpu", "region"], how="left")
    bad.loc[keep_known, "weekend"] = restored["weekend"].to_numpy()
    (tmp_path / "bad").mkdir()
    p2 = hourly_problem(tmp_path / "bad", bad)
    g2 = p2.grid()
    assert g2.end_index == g1.end_index and g2.origin == g1.origin
    peers1, _ = choose_peers(g1, p1.id_cols, fit_end)
    peers2, _ = choose_peers(g2, p2.id_cols, fit_end)
    assert peers1 == peers2
    changed_truth = 0
    for s in range(len(g1.series)):
        for lead in (1, 3, lead_max):
            c1 = build_case(g1, s, i, lead, peers1[s], 30, ("units",), ("weekend",))
            c2 = build_case(g2, s, i, lead, peers2[s], 30, ("units",), ("weekend",))
            assert c1 is not None and deep_equal(c1.inputs, c2.inputs), (s, lead)
            assert len(c1.inputs["peers"]) == 2 and c1.inputs["cov_hist"]["units"].shape == (30,)
            changed_truth += not np.isclose(g1.y[s, i + lead], g2.y[s, i + lead], equal_nan=True)
    assert changed_truth > 10                      # the corruption is real


def test_correlation_peers_use_only_the_train_window(tmp_path):
    """With no id columns and more than MAX_PEERS other series, peers are the most
    correlated on the train window; corrupting the data after the train window (and
    after a select-window issue time) changes neither the choice nor the inputs."""
    rng = np.random.default_rng(2)
    t = pd.date_range(T0, periods=24 * 30, freq="h")
    frames = []
    for grp in range(3):                                          # 3 groups x 4 series, one shared walk per group
        walk = np.cumsum(rng.normal(0, 0.05, len(t)))
        for k in range(4):
            frames.append(pd.DataFrame({"ts": t, "name": f"T{grp}-{k}", "price": 5 + walk + rng.normal(0, 0.01, len(t)),
                                        "units": 1.0}))
    df = pd.concat(frames, ignore_index=True)
    p1 = SeriesProblem(write(df, tmp_path / "a.csv"), time_col="ts", value_col="price", id_cols=("name",),
                       history=30, cache_dir=tmp_path / "c", log=lambda *a: None)
    # every name is unique, so no series has a partner with the same first id: all others are candidates
    g1 = p1.grid()
    fit_end = int(math.floor(window_bounds(g1.end_index)[0]))
    peers1, rule = choose_peers(g1, p1.id_cols, fit_end)
    assert all(len(x) == MAX_PEERS for x in peers1) and "correlate" in rule
    # same-type series share a shock, so they should be chosen first
    types = [s.split("-")[0] for s in g1.series]
    for s, ps in enumerate(peers1):
        assert sum(types[o] == types[s] for o in ps) == 3
    i = fit_end + 30
    bad = corrupted_copy(df.rename(columns={"name": "gpu"}), pd.Timestamp(g1.time(i))).rename(columns={"gpu": "name"})
    p2 = SeriesProblem(write(bad, tmp_path / "b.csv"), time_col="ts", value_col="price", id_cols=("name",),
                       history=30, cache_dir=tmp_path / "c2", log=lambda *a: None)
    g2 = p2.grid()
    peers2, _ = choose_peers(g2, p2.id_cols, fit_end)
    assert peers1 == peers2
    for s in range(len(g1.series)):
        assert deep_equal(build_case(g1, s, i, 3, peers1[s], 30).inputs, build_case(g2, s, i, 3, peers2[s], 30).inputs)


# ------------------------------------------------------------------ splits
def test_splits_are_ordered_deterministic_and_stratified(tmp_path):
    p = hourly_problem(tmp_path, max_cases=500, seed=3)
    a = p.splits()
    b = hourly_problem(tmp_path, max_cases=500, seed=3, cache_dir=tmp_path / "other").splits()
    a.check_order()
    assert p.block_unit == "day"
    for w in ("train", "select", "confirm", "test"):
        wa, wb = getattr(a, w), getattr(b, w)
        assert len(wa) == 500
        assert [c.case_id for c in wa.cases] == [c.case_id for c in wb.cases]
        assert [t["y"] for t in wa.truths] == [t["y"] for t in wb.truths]
        assert wa.blocks == [f"{c.inputs['series_id']}|{c.target_time:%Y-%m-%d}" for c in wa.cases]
        assert [c.issue_time for c in wa.cases] == sorted(c.issue_time for c in wa.cases)
        assert len(set(wa.blocks)) >= 30
    order = [a.train, a.select, a.confirm, a.test]
    for x, y in zip(order, order[1:]):
        assert max(c.target_time for c in x.cases) < min(c.issue_time for c in y.cases)
    # truths are the grid values at the target
    g = p.grid()
    pos = {s: k for k, s in enumerate(g.series)}
    for c, t in list(zip(a.select.cases, a.select.truths))[:50]:
        assert t["y"] == g.y[pos[c.inputs["series_id"]], int(g.index_of(c.target_time))]
    # a different seed gives a different subsample
    other = hourly_problem(tmp_path, max_cases=500, seed=4, cache_dir=tmp_path / "o4").splits()
    assert [c.case_id for c in other.train.cases] != [c.case_id for c in a.train.cases]
    # proportional to every (series, lead) group of the full population
    full = hourly_problem(tmp_path, max_cases=10**9, seed=3, cache_dir=tmp_path / "full").splits()
    for w in ("train", "select", "confirm", "test"):
        win, pop = getattr(a, w), getattr(full, w)
        got = Counter((c.inputs["series_id"], c.inputs["lead"]) for c in win.cases)
        have = Counter((c.inputs["series_id"], c.inputs["lead"]) for c in pop.cases)
        assert set(got) == set(have) and len(have) == 6 * 3
        for k, n in have.items():
            assert abs(got[k] - len(win) * n / len(pop)) <= 1.0
    # the pickle cache returns the same splits
    again = hourly_problem(tmp_path, max_cases=500, seed=3)
    assert [c.case_id for c in again.splits().select.cases] == [c.case_id for c in a.select.cases]
    assert again.block_unit == "day"


def test_stratified_pick_is_proportional():
    groups = {("A", 1): list(range(0, 600)), ("A", 3): list(range(600, 900)), ("B", 1): list(range(900, 1000))}
    picked = stratified_pick(groups, 100, np.random.default_rng(0))
    counts = Counter(next(k for k, v in groups.items() if i in v) for i in picked)
    assert counts == {("A", 1): 60, ("A", 3): 30, ("B", 1): 10}


# ------------------------------------------------------- baselines and seed
def test_candidate_sources_pass_the_lint():
    assert lint(C.SEED_SOURCE) == []
    for step_h, names in ((1.0, {"last_value", "moving_average", "seasonal_naive", "drift"}),
                          (24.0, {"last_value", "moving_average", "seasonal_naive", "drift"}),
                          (6.0, {"last_value", "moving_average", "drift"})):
        src = C.baseline_sources(step_h)
        assert set(src) == names
        assert all(lint(s) == [] for s in src.values())
    assert "SEASON = 24" in C.baseline_sources(1.0)["seasonal_naive"]
    assert "SEASON = 7" in C.baseline_sources(24.0)["seasonal_naive"]


def _toy_case(k, hist, lead, step_h=1.0):
    h = np.asarray(hist, dtype=float)
    ok = np.flatnonzero(np.isfinite(h))
    t = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=k)
    return Case(f"c{k}", t, t + timedelta(hours=lead * step_h),
                {"y_hist": h, "y_last": float(h[ok[0]]), "lead": lead, "lead_h": int(lead * step_h),
                 "lead_hours": lead * step_h, "step_hours": step_h})


def test_baselines_behave_as_defined():
    rng = np.random.default_rng(0)
    train, truths = [], []
    for k in range(300):                       # truth = mean of the newest 4 values: w = 4 should win
        h = rng.normal(5, 1, 30)
        lead = [1, 3][k % 2]
        train.append(_toy_case(k, h, lead))
        truths.append({"y": float(h[:4].mean())})
    hist_a = np.arange(30.0)[::-1] * 0.5 + 1.0          # newest first: 15.5, 15.0, ... rising 0.5 per step
    hist_b = hist_a.copy()
    hist_b[0] = np.nan                                   # newest value missing
    ev = [_toy_case(1000, hist_a, 1), _toy_case(1001, hist_a, 3), _toy_case(1002, hist_b, 3)]
    src = C.baseline_sources(1.0)
    out = {n: run_candidate(s, train, truths, ev) for n, s in src.items()}
    assert all(r.ok for r in out.values()), {n: r.error for n, r in out.items()}
    y = {n: [p["y"] for p in r.predictions] for n, r in out.items()}
    assert y["last_value"] == [15.5, 15.5, 15.0]
    assert y["moving_average"][0] == pytest.approx(hist_a[:4].mean())
    assert y["moving_average"][2] == pytest.approx(np.nanmean(hist_b[:4]))
    assert y["seasonal_naive"][0] == hist_a[23] and y["seasonal_naive"][1] == hist_a[21]
    assert y["drift"][0] == pytest.approx(16.0) and y["drift"][1] == pytest.approx(17.0)
    assert y["drift"][2] == pytest.approx(15.0 + 0.5 * 4)            # 4 steps from the newest valid value
    # seasonal fallback: the value one season back is missing -> last value
    hist_c = hist_a.copy()
    hist_c[23] = np.nan
    r = run_candidate(src["seasonal_naive"], train[:5], truths[:5], [_toy_case(9, hist_c, 1)])
    assert r.predictions[0]["y"] == 15.5
    # daily step: season 7
    r = run_candidate(C.baseline_sources(24.0)["seasonal_naive"], train[:5], truths[:5],
                      [_toy_case(9, hist_a, 2, step_h=24.0)])
    assert r.predictions[0]["y"] == hist_a[5]


def test_seed_beats_last_value_on_a_planted_daily_cycle(tmp_path):
    p = hourly_problem(tmp_path, hourly_frame(season_amp=0.3, seed=5), history=56, max_cases=1500)
    sp = p.splits()
    seed = evaluate(p, p.seed_source(), sp, ("select",))
    last = evaluate(p, p.baseline_sources()["last_value"], sp, ("select",))
    assert seed.ok and last.ok, (seed.error, last.error)
    s, b = seed.summary["select"], last.summary["select"]
    assert s["mean_loss"] < 0.7 * b["mean_loss"]
    for lead in ("3", "7"):
        assert s["by_lead"][lead]["mean_loss"] < b["by_lead"][lead]["mean_loss"]


# ------------------------------------------------------------------ scoring
def test_losses_and_summary(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cases = [Case(f"c{k}", t0, t0 + timedelta(hours=1), {"lead_h": [1, 3, 1][k]}) for k in range(3)]
    truths = [{"y": 2.0}, {"y": 4.0}, {"y": 1.0}]
    preds = [{"y": 2.5}, {"y": 3.0}, {"other": 1.0}]
    mae = hourly_problem(tmp_path)
    loss = mae.losses(preds, truths)
    assert loss[0] == 0.5 and loss[1] == 1.0 and np.isnan(loss[2])
    s = mae.summarize(cases, preds, truths)
    assert s["mean_loss"] == pytest.approx(0.75) and s["n"] == 2 and s["n_missing"] == 1
    assert s["by_lead"]["1"] == {"mean_loss": 0.5, "n": 1}
    assert s["mape_pct"] == pytest.approx((25.0 + 25.0) / 2) and s["rmse"] == pytest.approx(math.sqrt(0.625))
    mape = hourly_problem(tmp_path, loss="mape")
    assert list(mape.losses(preds[:2], truths[:2])) == [25.0, 25.0]
    assert np.isnan(mape.losses([{"y": 1.0}], [{"y": 0.0}])[0])      # guarded against zero
    rmse = hourly_problem(tmp_path, loss="rmse")
    assert list(rmse.losses(preds[:2], truths[:2])) == [0.25, 1.0]
    logp = hourly_problem(tmp_path, loss="mape", log_target=True)
    assert logp.losses([{"y": math.log(2.5)}], [{"y": math.log(2.0)}])[0] == pytest.approx(25.0)
    with pytest.raises(ValueError):
        hourly_problem(tmp_path, loss="mse")


def test_log_target_transforms_values(tmp_path):
    p = hourly_problem(tmp_path, log_target=True)
    q = hourly_problem(tmp_path)
    assert np.allclose(np.exp(p.grid().y), q.grid().y, equal_nan=True)


# --------------------------------------------------- describe, daily, registry
def test_describe_lists_every_input_key_and_real_scores(tmp_path):
    p = hourly_problem(tmp_path, max_cases=400)
    text = p.describe()
    keys = set(p.splits().train.cases[0].inputs)
    for k in keys:
        assert f"`{k}`" in text, k
    for k in ("series_id", "y_last", "y_hist7"):
        assert k in text
    for n in ("last_value", "moving_average", "seasonal_naive", "drift", "seed"):
        assert f"| {n} |" in text
    assert "What the data looks like" in text and "series: 6" in text and "step: 1 hour" in text
    assert "series-days" in text and "known_future" in text and "ONLY" not in text
    assert p.score_table()["seed"]["select"]["n"] == 400            # cached real evaluation


def test_daily_and_hourly_files_both_work(tmp_path):
    daily = SeriesProblem(write(daily_frame(), tmp_path / "d.csv"), time_col="date", value_col="price",
                          id_cols=("sku",), leads=(1, 3, 7), history=28, max_cases=400,
                          cache_dir=tmp_path / "c", log=lambda *a: None)
    sp = daily.splits()
    sp.check_order()
    assert daily.grid().step == pd.Timedelta("1D")
    assert daily.block_unit == "week"
    assert {c.inputs["lead_h"] for c in sp.select.cases} == {24, 72, 168}
    assert "seasonal_naive" in daily.baseline_sources() and "SEASON = 7" in daily.baseline_sources()["seasonal_naive"]
    res = evaluate(daily, daily.baseline_sources()["seasonal_naive"], sp)
    assert res.ok and set(res.summary["select"]["by_lead"]) == {"24", "72", "168"}
    assert all(b.split("|")[1].startswith(("2026-W", "2027-W")) for b in sp.select.blocks)
    # few series: weekly blocks would be too few, so the block falls back to a day
    few = SeriesProblem(write(daily_frame(n_series=2), tmp_path / "d2.csv"), time_col="date", value_col="price",
                        id_cols=("sku",), history=28, cache_dir=tmp_path / "c2", log=lambda *a: None)
    few.splits()
    assert few.block_unit == "day" and any("blocks" in n for n in few.notes)
    # leads given in days on an hourly file
    hourly = hourly_problem(tmp_path, leads=(1,), lead_unit="days")
    assert hourly.leads() == [24]
    hs = hourly.splits()
    assert {c.inputs["lead"] for c in hs.select.cases} == {24}
    with pytest.raises(ValueError):
        hourly_problem(tmp_path, leads=(0.5,), lead_unit="hours").leads()


def test_registry_builds_from_a_config_file(tmp_path, monkeypatch):
    write(hourly_frame(), tmp_path / "prices.csv")
    cfg = {"path": "prices.csv", "time_col": "ts", "value_col": "price", "id_cols": ["gpu", "region"],
           "leads": [1, 2], "history": 24, "max_cases": 300, "name": "gpu_prices", "cache_dir": str(tmp_path / "c")}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("LAB_SERIES_CONFIG", str(tmp_path / "cfg.json"))
    from lab.evalpool.registry import get_problem
    p = get_problem("series")
    assert isinstance(p, SeriesProblem) and p.name == "gpu_prices" and p.path == tmp_path / "prices.csv"
    assert p.id_cols == ("gpu", "region") and p.leads() == [1, 2] and p.history == 24
    assert len(p.splits().select) == 300
    monkeypatch.delenv("LAB_SERIES_CONFIG")
    with pytest.raises(SystemExit, match="LAB_SERIES_CONFIG"):
        get_problem("series")
    (tmp_path / "bad.json").write_text(json.dumps({**cfg, "lead": 3}))
    with pytest.raises(ValueError, match="unknown config keys"):
        SeriesProblem.from_config(tmp_path / "bad.json")


# ---------------------------------------------------------------------- live
def test_live_seals_then_grades_from_a_newer_file(tmp_path):
    full = hourly_frame(days=30, gaps=False)
    t = pd.to_datetime(full["ts"], utc=True)
    cut = T0 + pd.Timedelta(days=25)
    old = full[t <= cut]
    p = hourly_problem(tmp_path, old, leads=(1, 3, 7), max_cases=400)
    newer = write(full, tmp_path / "newer.csv")
    ledger = tmp_path / "ledger"
    cand = C.baseline_sources(1.0)["last_value"].replace('name = "last_value"', 'name = "mine"')
    end = p.grid().time(p.grid().end_index)
    assert end == cut.to_pydatetime()
    # sealing 30 minutes after the end of the data: every target is still in the future
    out = seal_live(p, {"mine": cand}, ledger, now=end + timedelta(minutes=30), log=lambda *a: None)
    n_models = 1 + len(p.baseline_sources())
    assert out["sealed"] == 6 * 3 * n_models and out["skipped_past"] == 0 and out["verified"]
    recs = SealedLedger(ledger).sealed()
    assert {r["model"] for r in recs} == {"mine"} | {f"baseline:{n}" for n in p.baseline_sources()}
    assert all(from_iso_(r["issued_at"]) == end for r in recs)
    mine = [r for r in recs if r["model"] == "mine"]
    g = p.grid()
    pos = {s: k for k, s in enumerate(g.series)}
    for r in mine:
        assert r["prediction"]["y"] == pytest.approx(g.y[pos[r["meta"]["series_id"]], g.end_index])
    # sealing 2 hours after the end: lead 1 is in the past and skipped (and said so)
    out2 = seal_live(p, {"mine": cand}, tmp_path / "ledger2", now=end + timedelta(hours=2), log=lambda *a: None)
    assert out2["skipped_past"] == 6 * n_models and out2["sealed"] == 6 * 2 * n_models
    # grading: nothing is due before the targets
    res0 = grade_live(p, newer, ledger, now=end + timedelta(minutes=45), log=lambda *a: None)
    assert res0["graded"] == 0
    res = grade_live(p, newer, ledger, now=end + timedelta(days=3), log=lambda *a: None)
    assert res["graded"] == len(recs) and res["missing"] == 0 and res["verified"]
    led = SealedLedger(ledger)
    assert led.verify()[0]
    gn = p.load_grid(newer, step=g.step, origin=g.origin, notes=[])
    for r in led.joined():
        k = int(gn.index_of(from_iso_(r["target_time"])))
        assert r["grade"]["truth"]["y"] == gn.y[pos[r["meta"]["series_id"]], k]
    assert res["scores"]["mine"]["all"]["n"] == 18
    # a second grading finds nothing new; tampering with a sealed record is detected
    assert grade_live(p, newer, ledger, now=end + timedelta(days=3), log=lambda *a: None)["graded"] == 0
    lines = led.sealed_path.read_text().splitlines()
    rec = json.loads(lines[3])
    rec["prediction"]["y"] += 0.5                  # edit a forecast after the fact
    lines[3] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    led.sealed_path.write_text("\n".join(lines) + "\n")
    assert not SealedLedger(ledger).verify()[0]


def test_live_command_line(tmp_path, capsys):
    """The CLI end to end with a file whose data end is in the future relative to now,
    so the real clock can be used (the ledger refuses to seal the past)."""
    start = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(days=20)
    df = hourly_frame(days=21, gaps=False, start=start)           # ends about a day from now
    path = write(df, tmp_path / "live.csv")
    cfg = {"path": str(path), "time_col": "ts", "value_col": "price", "id_cols": ["gpu", "region"],
           "leads": [1, 3], "history": 24, "max_cases": 300, "name": "cli", "cache_dir": str(tmp_path / "c")}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    cand = tmp_path / "mine.py"
    cand.write_text(C.LAST_VALUE)
    from lab.tasks.series.live import main
    assert main([str(tmp_path / "cfg.json"), "--candidate", str(cand), "--ledger", str(tmp_path / "L")]) == 0
    text = capsys.readouterr().out
    assert "mine: sealed 12" in text and "chain ok" in text
    assert main([str(tmp_path / "cfg.json"), "--grade", str(path), "--ledger", str(tmp_path / "L")]) == 0
    assert "graded 0 due records" in capsys.readouterr().out


def from_iso_(s):
    from lab.core.timeutil import from_iso
    return from_iso(s)
