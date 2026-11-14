"""Coast task: parsing, residuals, leak safety, model fitting, and a full
seal -> grade cycle with a fake clock and a fake client. No network."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from coast_fakes import FIX, T0, FakeClient, FakeClock, resid_fn, tide_fn
from lab.core.ledger import SealedLedger
from lab.core.task import Case
from lab.tasks.coast import sources as S
from lab.tasks.coast.models import DampedPersistence, Persistence, SeedPhysics, TideTable
from lab.tasks.coast.water import StationData, StationFeatures, build_case, residual


# ------------------------------------------------------------------ parsing
def test_parse_coops_water_level_drops_blanks():
    s = S.parse_coops((FIX / "coops_water_level.json").read_text())
    assert len(s) == 29  # 30 rows, one blank value
    assert str(s.index.tz) == "UTC"
    assert s.index.is_monotonic_increasing
    assert s.index[1] - s.index[0] == pd.Timedelta(minutes=6)
    assert s.between(-3, 5).all()


def test_parse_coops_predictions():
    s = S.parse_coops((FIX / "coops_predictions.json").read_text())
    assert len(s) == 30 and s.dtype == float


def test_parse_coops_error_payload_is_empty():
    assert len(S.parse_coops({"error": {"message": "No data was found"}})) == 0


def test_parse_station_meta():
    m = S.parse_station_meta((FIX / "coops_stations.json").read_text())
    assert m["9414290"]["name"] == "San Francisco"
    assert 37 < m["9414290"]["lat"] < 38 and -123 < m["9414290"]["lon"] < -122


def test_parse_ndbc_standard():
    df = S.parse_ndbc((FIX / "ndbc_46026.txt").read_text())
    assert len(df) == 12
    assert df.index.is_monotonic_increasing and str(df.index.tz) == "UTC"
    assert df["WVHT"].dtype == float and df["WVHT"].isna().any()  # 'MM' -> NaN
    assert df["WVHT"].max() < 20


def test_parse_ndbc_spectral():
    df = S.parse_ndbc((FIX / "ndbc_46026.spec").read_text())
    assert {"SwH", "SwP", "WWH", "SwD"} <= set(df.columns)
    assert df["SwH"].dtype == float
    assert not pd.api.types.is_numeric_dtype(df["SwD"])  # compass letters stay text
    assert df["SwD"].iloc[-1] in {"N", "NNW", "NW", "WNW", "W", "WSW", "SW", "SSW", "S"}


def test_parse_open_meteo_weather_two_locations():
    dfs = S.parse_open_meteo((FIX / "open_meteo_weather.json").read_text())
    assert len(dfs) == 2
    p = dfs[0]["pressure_msl"].dropna()
    assert len(p) > 24 and p.between(950, 1060).all()
    assert str(dfs[0].index.tz) == "UTC"


def test_parse_open_meteo_marine():
    (df,) = S.parse_open_meteo((FIX / "open_meteo_marine.json").read_text())
    assert {"wave_height", "swell_wave_period"} <= set(df.columns)
    assert df["wave_height"].dropna().between(0, 25).all()


def test_parse_alerts_keeps_coastal_only():
    a = S.parse_alerts((FIX / "nws_alerts.json").read_text())
    events = {x["event"] for x in a}
    assert "Wind Advisory" not in events
    assert {"High Surf Warning", "Coastal Flood Advisory"} <= events


def test_chunks_are_aligned_and_cover_range():
    from datetime import date
    ch = list(S._chunks(date(2026, 8, 1), date(2026, 11, 13)))
    assert ch[0][0] <= date(2026, 8, 1) and ch[-1][1] >= date(2026, 11, 13)
    assert all((b - a).days == S.CHUNK_DAYS - 1 for a, b in ch)
    assert all((a - S.EPOCH).days % S.CHUNK_DAYS == 0 for a, _ in ch)


# ---------------------------------------------------------------- residuals
def test_residual_is_observed_minus_tide_on_common_times():
    idx = pd.date_range("2026-11-13", periods=5, freq="6min", tz="UTC")
    obs = pd.Series([1.0, 1.1, np.nan, 1.3, 1.4], index=idx)
    tide = pd.Series([0.5, 0.5, 0.5, 0.5], index=idx[:4])
    r = residual(obs, tide)
    assert list(r.index) == [idx[0], idx[1], idx[3]]
    assert np.allclose(r.to_numpy(), [0.5, 0.6, 0.8])


def _synthetic_station(start, end, with_weather=True):
    idx = pd.date_range(start, end, freq="6min", tz="UTC")
    tide = pd.Series([tide_fn(t) for t in idx], index=idx)
    obs = pd.Series([tide_fn(t) + resid_fn(t) for t in idx], index=idx)
    w = None
    if with_weather:
        hidx = pd.date_range(start, end + pd.Timedelta(days=2), freq="h", tz="UTC")
        w = pd.DataFrame({"pressure_msl": 1013 + np.sin(np.arange(len(hidx)) / 9.0),
                          "wind_speed_10m": 6.0, "wind_direction_10m": 300.0}, index=hidx)
    return StationData("9999999", "Test", 37.0, -122.0, obs, tide, w)


def test_truth_is_mean_within_six_minutes():
    sd = _synthetic_station(pd.Timestamp("2026-11-01", tz="UTC"), pd.Timestamp("2026-11-05", tz="UTC"))
    f = StationFeatures(sd, pd.Timestamp("2026-11-01", tz="UTC"), pd.Timestamp("2026-11-05", tz="UTC"))
    t = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    want = np.mean([resid_fn(t + timedelta(minutes=k)) for k in (-6, 0, 6)])
    assert f.truth_at(t) == pytest.approx(want, abs=1e-9)


# ---------------------------------------------------------------- leak test
def _inputs_equal(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, np.ndarray):
            if not np.array_equal(x, y, equal_nan=True):
                return False
        elif isinstance(x, float) and np.isnan(x):
            if not (isinstance(y, float) and np.isnan(y)):
                return False
        elif x != y:
            return False
    return True


@pytest.mark.parametrize("lead", [1, 3, 6, 12, 24])
def test_corrupting_the_future_leaves_inputs_unchanged(lead):
    start, end = pd.Timestamp("2026-10-20", tz="UTC"), pd.Timestamp("2026-11-08", tz="UTC")
    sd = _synthetic_station(start, end)
    issue = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    f1 = StationFeatures(sd, start, end)
    c1 = build_case(f1, issue, lead)
    assert c1 is not None and c1.inputs["has_weather"]

    # corrupt every observation after the issue time (values and gaps)
    bad = sd.obs.copy()
    after = bad.index > pd.Timestamp(issue)
    rng = np.random.default_rng(0)
    bad[after] = rng.normal(50, 20, after.sum())
    bad = bad.drop(bad.index[after][::7])
    sd2 = StationData(sd.id, sd.name, sd.lat, sd.lon, bad, sd.tide, sd.weather)
    f2 = StationFeatures(sd2, start, end)
    c2 = build_case(f2, issue, lead)
    assert _inputs_equal(c1.inputs, c2.inputs)
    # the corruption is real: the truth at the target moved
    assert f2.truth_at(c1.target_time) != pytest.approx(f1.truth_at(c1.target_time))


# --------------------------------------------------------- model recovery
def _synthetic_cases(n=3000, seed=1, truth_fn=None):
    rng = np.random.default_rng(seed)
    cases, truths = [], []
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for k in range(n):
        lead = [1, 6, 24][k % 3]
        m = rng.normal(0.2, 0.1)
        r0 = m + rng.normal(0, 0.15)
        inp = {"station": ["A", "B"][k % 2], "lead_h": lead, "r_now": r0, "r_mean": m,
               "trend3": rng.normal(0, 0.05), "dp_hpa": rng.normal(0, 4), "taux": rng.normal(0, 40),
               "tauy": rng.normal(0, 40), "has_weather": True}
        c = Case(f"c{k}", t0 + timedelta(hours=k), t0 + timedelta(hours=k + lead), inp)
        cases.append(c)
        truths.append({"residual_m": truth_fn(inp) + rng.normal(0, 0.005)})
    return cases, truths


def test_damped_persistence_recovers_a():
    cases, truths = _synthetic_cases(truth_fn=lambda i: i["r_mean"] + 0.7 * (i["r_now"] - i["r_mean"]))
    m = DampedPersistence()
    m.fit(cases, truths)
    for lead in (1, 6, 24):
        assert m.a[lead] == pytest.approx(0.7, abs=0.02)


def test_seed_physics_recovers_known_coefficients():
    wind = {"A": (0.0004, -0.0002), "B": (-0.0001, 0.0005)}  # metres per m^2/s^2

    def truth(i):
        wx, wy = wind[i["station"]]
        return (i["r_mean"] + 0.6 * (i["r_now"] - i["r_mean"]) - 0.01 * i["dp_hpa"]
                + 0.3 * i["trend3"] + wx * i["taux"] + wy * i["tauy"] + 0.02)

    cases, truths = _synthetic_cases(truth_fn=truth)
    m = SeedPhysics()
    m.fit(cases, truths)
    for lead in (1, 6, 24):
        k = m.coef[lead]
        assert k["a"] == pytest.approx(0.6, abs=0.02)
        assert k["pressure"] * 100 == pytest.approx(-1.0, abs=0.05)  # cm/hPa
        assert k["trend"] == pytest.approx(0.3, abs=0.05)
        assert k["intercept"] == pytest.approx(0.02, abs=0.005)
        for s, (wx, wy) in wind.items():
            assert k["wind"][s][0] == pytest.approx(wx, abs=3e-5)
            assert k["wind"][s][1] == pytest.approx(wy, abs=3e-5)
    pred = m.predict(cases[0])["residual_m"]
    assert pred == pytest.approx(truths[0]["residual_m"], abs=0.03)


def test_seed_physics_falls_back_without_weather():
    cases, truths = _synthetic_cases(truth_fn=lambda i: i["r_mean"] + 0.5 * (i["r_now"] - i["r_mean"]))
    m = SeedPhysics()
    m.fit(cases, truths)
    c = cases[0]
    no_wx = Case(c.case_id, c.issue_time, c.target_time, {**c.inputs, "has_weather": False})
    want = c.inputs["r_mean"] + 0.5 * (c.inputs["r_now"] - c.inputs["r_mean"])
    assert m.predict(no_wx)["residual_m"] == pytest.approx(want, abs=0.01)


def test_baselines():
    c = Case("x", T0, T0 + timedelta(hours=1), {"r_now": 0.42, "lead_h": 1})
    assert TideTable().predict(c)["residual_m"] == 0.0
    assert Persistence().predict(c)["residual_m"] == 0.42


# ------------------------------------------------------------ live cycle
def test_full_seal_grade_cycle_with_fake_clock(tmp_path):
    from lab.tasks.coast.live import WaterCycle, cycle

    clock = FakeClock(T0)
    # the second gauge stops reporting two hours after the first cycle
    client = FakeClient(clock, cutoff={"9410170": T0 + timedelta(hours=2)})
    wc = WaterCycle(client, fit_days=6, stations=["9414290", "9410170"], log=lambda *a: None)
    ledger_dir = tmp_path / "live"

    s1 = cycle(clock=clock, client=client, ledger_dir=ledger_dir, wc=wc, log=lambda *a: None)
    n_first = s1["ledger"]["records"]
    assert n_first == 2 * 5 * 4  # stations x leads x models
    assert s1["ledger"]["verify"]["ok"] and s1["ledger"]["graded"] == 0

    # same hour again: no duplicate forecasts
    clock.advance(minutes=15)
    s2 = cycle(clock=clock, client=client, ledger_dir=ledger_dir, wc=wc, log=lambda *a: None)
    assert s2["ledger"]["records"] == n_first

    # a day later: everything due from the healthy gauge gets graded
    clock.advance(hours=25)
    s3 = cycle(clock=clock, client=client, ledger_dir=ledger_dir, wc=wc, log=lambda *a: None)
    led = SealedLedger(ledger_dir)
    ok, n, msg = led.verify()
    assert ok, msg
    joined = led.joined()
    first = [r for r in joined if r["seq"] < n_first]
    sf = [r for r in first if r["meta"]["station"] == "9414290"]
    assert all(r["grade"] is not None for r in sf)
    # grades are honest: truth is the synthetic residual at the target time
    for r in sf:
        t = datetime.fromisoformat(r["target_time"].replace("Z", "+00:00"))
        want = np.mean([resid_fn(t + timedelta(minutes=k)) for k in (-6, 0, 6)])
        assert r["grade"]["truth"]["residual_m"] == pytest.approx(want, abs=1e-3)
        assert r["grade"]["truth"]["abs_error_cm"] == pytest.approx(
            abs(r["prediction"]["residual_m"] - r["grade"]["truth"]["residual_m"]) * 100, abs=0.01)
    # the silent gauge: targets after its cutoff stay ungraded and are listed as stale
    sd = [r for r in first if r["meta"]["station"] == "9410170"
          and datetime.fromisoformat(r["target_time"].replace("Z", "+00:00")) > T0 + timedelta(hours=2, minutes=6)]
    assert sd and all(r["grade"] is None for r in sd)
    stale_seqs = {x["seq"] for x in s3["water"]["stale"]}
    old = {r["seq"] for r in sd
           if clock() - datetime.fromisoformat(r["target_time"].replace("Z", "+00:00")) > timedelta(hours=6)}
    recent = {r["seq"] for r in sd} - old
    assert old and old <= stale_seqs          # silent for more than 6 h: stale
    assert recent and not (recent & stale_seqs)  # recently due: still waiting
    # state file written and consistent with the ledger
    state = json.loads((ledger_dir / "state.json").read_text())
    assert state["ledger"]["records"] == led.head()[0]
    assert state["ledger"]["head_hash"] == led.head()[1]
    assert state["water"]["scoreboard"]["Persistence"]["1"]["graded"] >= 1
    st = {s["id"]: s for s in state["water"]["stations"]}
    assert st["9414290"]["tide"]["step_min"] == 12
    assert "Persistence" in st["9414290"]["forecasts"]


def test_cycle_survives_missing_weather(tmp_path):
    from lab.tasks.coast.live import WaterCycle, cycle

    clock = FakeClock(T0)

    class NoWeather(FakeClient):
        def get_text(self, url, ttl=None):
            if "api.open-meteo.com" in url:
                raise S.FetchError("connection failed")
            return super().get_text(url, ttl)

    client = NoWeather(clock)
    wc = WaterCycle(client, fit_days=6, stations=["9414290"], log=lambda *a: None)
    s = cycle(clock=clock, client=client, ledger_dir=tmp_path / "live", wc=wc, log=lambda *a: None)
    assert s["ledger"]["records"] == 5 * 4
    assert s["ledger"]["verify"]["ok"]


# ------------------------------------------------------------------- waves
from coast_fakes import wave_fn  # noqa: E402
from lab.tasks.coast import waves as W  # noqa: E402


def test_parse_buoy_meta():
    m = W.parse_buoy_meta((FIX / "ndbc_activestations.xml").read_text())
    assert set(W.COASTAL + W.OFFSHORE) <= set(m)
    assert m["46026"]["lat"] == pytest.approx(37.75) and m["46026"]["lon"] == pytest.approx(-122.838)


def test_group_speed_and_travel_time():
    # deep water: c_g = g T / (4 pi); a 14 s swell moves about 10.9 m/s
    assert float(W.group_speed(14.0)) == pytest.approx(9.81 * 14 / (4 * np.pi))
    assert float(W.travel_hours(600_000, 14.0)) == pytest.approx(600_000 / (9.81 * 14 / (4 * np.pi)) / 3600)
    d = W.great_circle_m(37.75, -122.838, 38.067, -129.895)
    assert 600_000 < d < 640_000  # 46026 to 46059


def test_upstream_uses_only_reports_before_issue():
    t0 = pd.Timestamp("2026-11-10", tz="UTC")
    idx = pd.date_range(t0, periods=200, freq="h")
    obs = pd.DataFrame({"WVHT": np.arange(200, dtype=float) / 10 + 1, "DPD": 14.0}, index=idx)
    up = W.Upstream("X", obs, distance_m=float(W.group_speed(14.0)) * 3600 * 10)  # exactly 10 h travel
    issue = (t0 + pd.Timedelta(hours=100)).timestamp()
    h, hours = up.at(issue, issue + 6 * 3600)
    assert hours == pytest.approx(10.0)
    assert h == pytest.approx(obs["WVHT"].iloc[96])  # observed 4 h before issue, arrives at the target
    h2, _ = up.at(issue, issue + 30 * 3600)  # would need a report from after the issue time
    assert np.isnan(h2)


def _wave_feats(obs, model=None, start=None, end=None):
    meta = {"name": "t", "lat": 37.0, "lon": -122.0}
    return W.BuoyFeatures("46026", meta, obs, model, None, start, end)


@pytest.mark.parametrize("lead", [1, 6, 24])
def test_wave_cases_ignore_the_future(lead):
    start, end = pd.Timestamp("2026-10-20", tz="UTC"), pd.Timestamp("2026-11-08", tz="UTC")
    idx = pd.date_range(start, end, freq="30min")
    obs = pd.DataFrame({"WVHT": [wave_fn(t) for t in idx], "DPD": 14.0}, index=idx)
    hidx = pd.date_range(start, end + pd.Timedelta(days=2), freq="h")
    model = pd.DataFrame({"wave_height": 2.0 + np.sin(np.arange(len(hidx)) / 8)}, index=hidx)
    issue = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    c1 = W.build_wave_case(_wave_feats(obs, model, start, end), issue, lead)
    bad = obs.copy()
    bad.loc[bad.index > pd.Timestamp(issue), "WVHT"] = 99.0
    c2 = W.build_wave_case(_wave_feats(bad, model, start, end), issue, lead)
    assert c1 is not None and c1.inputs["has_model"]
    assert _inputs_equal(c1.inputs, c2.inputs)


def test_seed_swell_recovers_blend():
    rng = np.random.default_rng(3)
    cases, truths = [], []
    for k in range(2000):
        inp = {"buoy": "b", "lead_h": 6, "h_now": rng.uniform(1, 4), "h_mean": rng.uniform(1, 3),
               "model_target": rng.uniform(1, 4), "model_issue": rng.uniform(1, 4),
               "upstream_h": rng.uniform(1, 5), "has_model": True, "has_upstream": True}
        y = (0.9 * inp["model_target"] - 0.4 * inp["model_issue"] + 0.4 * inp["h_now"]
             + 0.1 * inp["h_mean"] + 0.2 * inp["upstream_h"] + 0.05)
        cases.append(Case(f"w{k}", T0, T0 + timedelta(hours=6), inp))
        truths.append({"wave_height_m": y + rng.normal(0, 0.01)})
    m = W.SeedSwell(ridge=0.0)
    m.fit(cases, truths)
    k = m.coef[(6, True, True)]
    for name, want in [("model_target", 0.9), ("model_issue", -0.4), ("h_now", 0.4),
                       ("h_mean", 0.1), ("upstream_h", 0.2), ("intercept", 0.05)]:
        assert k[name] == pytest.approx(want, abs=0.02), name
    no_up = Case("x", T0, T0, {**cases[0].inputs, "has_upstream": False, "upstream_h": float("nan")})
    assert np.isfinite(m.predict(no_up)["wave_height_m"])


def test_full_cycle_with_waves(tmp_path):
    from lab.tasks.coast.live import WaterCycle, cycle

    clock = FakeClock(T0)
    client = FakeClient(clock)
    wc = WaterCycle(client, fit_days=6, stations=["9414290"], log=lambda *a: None)
    wave = W.WaveCycle(client, fit_days=6, log=lambda *a: None)
    s1 = cycle(clock=clock, client=client, ledger_dir=tmp_path / "live", wc=wc, wave_cycle=wave,
               log=lambda *a: None)
    n_buoys = len(s1["waves"]["buoys"])
    assert n_buoys == len(W.COASTAL)
    assert s1["ledger"]["records"] == 5 * 4 + n_buoys * 5 * 3
    clock.advance(hours=26)
    s2 = cycle(clock=clock, client=client, ledger_dir=tmp_path / "live", wc=wc, wave_cycle=wave,
               log=lambda *a: None)
    led = SealedLedger(tmp_path / "live")
    assert led.verify()[0]
    first_waves = [r for r in led.joined() if r["seq"] < s1["ledger"]["records"] and r["task"] == W.TASK]
    assert first_waves and all(r["grade"] is not None for r in first_waves)
    for r in first_waves[:20]:
        t = datetime.fromisoformat(r["target_time"].replace("Z", "+00:00"))
        assert r["grade"]["truth"]["wave_height_m"] == pytest.approx(wave_fn(t), abs=0.05)
    assert s2["waves"]["scoreboard"]["SeedSwell"]["24"]["graded"] == n_buoys


def _fake_state(tmp_path, monkeypatch=None, max_bytes=None):
    from lab.tasks.coast import live
    if max_bytes is not None:
        monkeypatch.setattr(live, "MAX_STATE_BYTES", max_bytes)
    clock = FakeClock(T0)
    client = FakeClient(clock)
    wc = live.WaterCycle(client, fit_days=6, stations=["9414290"], log=lambda *a: None)
    wave = W.WaveCycle(client, fit_days=6, log=lambda *a: None)
    live.cycle(clock=clock, client=client, ledger_dir=tmp_path / "live", wc=wc, wave_cycle=wave, log=lambda *a: None)
    clock.advance(hours=3)
    live.cycle(clock=clock, client=client, ledger_dir=tmp_path / "live", wc=wc, wave_cycle=wave, log=lambda *a: None)
    return json.loads((tmp_path / "live" / "state.json").read_text()), clock


def test_state_fields_are_documented(tmp_path):
    from pathlib import Path
    import re
    doc = (Path(__file__).parents[1] / "docs" / "state_coast.md").read_text().split("## Trimmed real example")[0]
    s, _ = _fake_state(tmp_path)
    groups = [s, s["ledger"], s["ledger"]["verify"], s["cycle"], s["water"], s["water"]["stations"][0],
              s["water"]["stations"][0]["tide"], s["waves"], s["waves"]["buoys"][0], s["waves"]["offshore"][0],
              s["alerts"][0]]
    groups += list(s["water"]["stations"][0]["forecasts"].values())[:1]
    groups += list(s["waves"]["buoys"][0]["forecasts"].values())[:1]
    missing = sorted({k for g in groups for k in g if not re.search(rf"`[^`]*\b{re.escape(k)}\b[^`]*`", doc)})
    assert not missing, f"undocumented state fields: {missing}"
    assert s["water"]["scoreboard"]["Persistence"]["1"]["graded"] == 1


def test_state_size_guard_shortens_window(tmp_path, monkeypatch):
    s, clock = _fake_state(tmp_path, monkeypatch, max_bytes=1000)
    frm = datetime.fromisoformat(s["water"]["forecast_window"]["from"].replace("Z", "+00:00"))
    assert clock() - frm == timedelta(hours=2)


# ------------------------------------------------- leak-free weather history
def _with_runs(sd, rng=None, issue=None):
    """Copy of sd with previous-run weather (k = 1, 2). With `issue`, every value
    from a run that could have been published after `issue` is corrupted, and the
    hindsight series is corrupted everywhere."""
    from lab.tasks.coast.water import RUN_DELAY_H
    runs = {}
    for k in (1, 2):
        w = sd.weather.copy()
        w["pressure_msl"] = w["pressure_msl"] + 0.5 * k
        if issue is not None:
            late = (w.index - pd.Timedelta(hours=24 * k - RUN_DELAY_H)) > pd.Timestamp(issue)
            w.loc[late, "pressure_msl"] = rng.normal(900, 20, late.sum())
            w.loc[late, "wind_speed_10m"] = rng.uniform(20, 40, late.sum())
        runs[k] = w
    hind = sd.weather.copy()
    if issue is not None:
        hind["pressure_msl"] = rng.normal(950, 20, len(hind))
    return StationData(sd.id, sd.name, sd.lat, sd.lon, sd.obs, sd.tide, hind, runs)


@pytest.mark.parametrize("lead", [1, 3, 6, 12, 24])
@pytest.mark.parametrize("mode", ["runs", "auto"])
def test_weather_runs_after_issue_cannot_change_inputs(lead, mode):
    start, end = pd.Timestamp("2026-10-20", tz="UTC"), pd.Timestamp("2026-11-08", tz="UTC")
    sd = _synthetic_station(start, end)
    issue = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    c1 = build_case(StationFeatures(_with_runs(sd), start, end), issue, lead, weather=mode)
    assert c1.inputs["has_weather"]
    bad = _with_runs(sd, np.random.default_rng(1), issue)
    c2 = build_case(StationFeatures(bad, start, end), issue, lead, weather=mode)
    assert _inputs_equal(c1.inputs, c2.inputs)
    # and the hindsight series really differs: using it would have changed the inputs
    c3 = build_case(StationFeatures(bad, start, end), issue, lead, weather="hindsight")
    assert not _inputs_equal(c1.inputs, c3.inputs)


def test_live_path_without_runs_is_unchanged():
    """The live loop loads no runs: "auto" must then read the forecast endpoint
    exactly as before (same numbers as weather="hindsight")."""
    start, end = pd.Timestamp("2026-10-20", tz="UTC"), pd.Timestamp("2026-11-08", tz="UTC")
    f = StationFeatures(_synthetic_station(start, end), start, end)
    issue = datetime(2026, 11, 4, 9, 6, tzinfo=timezone.utc)
    for lead in (1, 24):
        a = build_case(f, issue, lead)
        b = build_case(f, issue, lead, weather="hindsight")
        assert a.inputs["has_weather"] and _inputs_equal(a.inputs, b.inputs)
        assert not build_case(f, issue, lead, weather="runs").inputs["has_weather"]
        assert not build_case(f, issue, lead, weather="none").inputs["has_weather"]


@pytest.mark.parametrize("lead", [1, 6, 24])
def test_wave_model_runs_after_issue_cannot_change_inputs(lead):
    from lab.tasks.coast.water import RUN_DELAY_H
    start, end = pd.Timestamp("2026-10-20", tz="UTC"), pd.Timestamp("2026-11-08", tz="UTC")
    idx = pd.date_range(start, end, freq="30min")
    obs = pd.DataFrame({"WVHT": [wave_fn(t) for t in idx], "DPD": 14.0}, index=idx)
    hidx = pd.date_range(start, end + pd.Timedelta(days=2), freq="h")
    model = pd.DataFrame({"wave_height": 2.0 + np.sin(np.arange(len(hidx)) / 8)}, index=hidx)
    runs = {k: pd.DataFrame({"wave_height": 2.0 + np.sin(np.arange(len(hidx)) / 8 + 0.3 * k)}, index=hidx)
            for k in (1, 2)}
    issue = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    meta = {"lat": 37.75, "lon": -122.84, "name": "x"}
    f1 = W.BuoyFeatures("46026", meta, obs, model, None, start, end, model_runs=runs)
    c1 = W.build_wave_case(f1, issue, lead)                       # auto -> runs
    assert c1.inputs["has_model"]
    bad_runs = {}
    for k, df in runs.items():
        df = df.copy()
        late = (df.index - pd.Timedelta(hours=24 * k - RUN_DELAY_H)) > pd.Timestamp(issue)
        df.loc[late, "wave_height"] = 50.0
        bad_runs[k] = df
    bad_model = model.copy()
    bad_model["wave_height"] = 77.0                                # hindsight must not be read
    f2 = W.BuoyFeatures("46026", meta, obs, bad_model, None, start, end, model_runs=bad_runs)
    assert _inputs_equal(c1.inputs, W.build_wave_case(f2, issue, lead, model="runs").inputs)
    assert _inputs_equal(c1.inputs, W.build_wave_case(f2, issue, lead).inputs)
    assert W.build_wave_case(f2, issue, lead, model="hindsight").inputs["model_target"] == 77.0
