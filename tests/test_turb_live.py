"""A full seal -> grade cycle with a fake clock and fake clients (no network)."""
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

from lab.core.timeutil import from_iso
from lab.tasks.turb import fields as F
from lab.tasks.turb.live import Clients, LiveJob

_spec = importlib.util.spec_from_file_location(
    "turb_synthetic", Path(__file__).parent / "fixtures" / "turb" / "synthetic.py")
syn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(syn)

T0 = syn.T0  # 2026-11-13 12:10 UTC
SQUARE = [[30, -110], [30, -90], [45, -90], [45, -110], [30, -110]]


def _job(tmp_path, clock):
    all_reports = syn.reports(T0 - timedelta(days=3), T0 + timedelta(hours=12), per_hour=30, seed=3)
    store = F.FieldStore(tmp_path / "fields")
    store.add(syn.fieldset(T0 - timedelta(days=3), 72, fetched_at=T0.replace(minute=0)), "backfill", T0 - timedelta(hours=1))
    calls = {"fields": 0}

    def fetch_fields(now):
        calls["fields"] += 1
        start = now.replace(minute=0, second=0) - timedelta(hours=6)
        return syn.fieldset(start, 18, fetched_at=now)

    def recent(now):  # only reports that have happened by `now`, as received (one row per report)
        return syn.report_rows(all_reports[all_reports["time"] <= pd.Timestamp(now)])

    def history(now):
        return all_reports[all_reports["time"] < pd.Timestamp(now - timedelta(hours=2))]

    def gairmets(now):
        base = now.replace(minute=0, second=0)
        return [{"valid_time": (base + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z"), "issue_time": 0,
                 "base_fl": 180, "top_fl": 380, "severity": "MOD", "coords": SQUARE} for h in (0, 3, 6, 9)]

    job = LiveJob(Clients(fetch_fields, recent, history, gairmets), ledger_dir=tmp_path / "live",
                  field_store=store, report_store=tmp_path / "pireps.csv.gz",
                  backtest_path=tmp_path / "no_backtest.json", log=lambda m: None, clock=clock)
    return job, calls


def test_seal_then_grade_cycle(tmp_path):
    now = [T0]
    job, calls = _job(tmp_path, lambda: now[0])

    out = job.cycle(T0)
    assert out["sealed"] == 7 * 6 and out["graded"] == 0 and out["verify"]
    sealed = job.ledger.sealed()
    assert {r["model"] for r in sealed} == {"Climatology", "WindSpeed", "Shear", "Richardson",
                                             "Ellrod1", "SeedBlend", "GAirmet"}
    assert sorted({r["target_time"] for r in sealed}) == [
        f"2026-11-13T{h:02d}:00:00Z" for h in range(13, 19)]
    assert all(from_iso(r["target_time"]) > from_iso(r["sealed_at"]) for r in sealed)
    assert sorted({r["meta"]["valid_hour_offset"] for r in sealed}) == [1, 2, 3, 4, 5, 6]
    for r in sealed:  # the stored lead is the real one: valid time minus sealing time
        real = (from_iso(r["target_time"]) - from_iso(r["sealed_at"])).total_seconds() / 60
        assert r["meta"]["lead_minutes"] == round(real, 1)
    assert min(r["meta"]["lead_minutes"] for r in sealed) == 50.0

    # 14:20: only the 13:00 records are 75 minutes past their valid hour
    now[0] = T0 + timedelta(minutes=130)
    out = job.cycle(now[0])
    assert out["graded"] == 7
    assert calls["fields"] == 1  # fields reused within 3 hours
    graded = job.ledger.grades()
    assert all(job.ledger.sealed()[s]["target_time"] == "2026-11-13T13:00:00Z" for s in graded)
    truth = next(iter(graded.values()))["truth"]
    assert truth["n"] > 0 and truth["window_minutes"] == 30
    assert all(abs(from_iso(x["time"]) - from_iso("2026-11-13T13:00:00Z")) <= timedelta(minutes=30)
               and x["fl"] >= 200 for x in truth["reports"])

    # seven hours later (19:10): every record valid up to 17:00 is due, 18:00 is not yet
    now[0] = T0 + timedelta(hours=7)
    out = job.cycle(now[0])
    assert calls["fields"] == 2
    grades = job.ledger.grades()
    for r in job.ledger.sealed():
        due = from_iso(r["target_time"]) + timedelta(minutes=75) <= now[0]
        assert (r["seq"] in grades) == due, r["case_id"]
    assert len(grades) == 7 * 5 + 7 * 3  # cycle 1: 13-17 h; cycle 2 (14:20): 15-17 h
    ok, n, msg = job.ledger.verify()
    assert ok, msg

    state = json.loads((tmp_path / "live" / "state.json").read_text())
    assert state["ledger"]["verify"]["ok"] and state["ledger"]["records"] == n
    assert state["ledger"]["graded"] == len(job.ledger.grades())
    assert len(state["forecasts"]["SeedBlend"]) == 6 and len(state["forecasts"]["Ellrod1"]) == 6
    grid = state["grid"]
    assert len(state["forecasts"]["SeedBlend"][0]["risk"]) == len(grid["levels_hpa"])
    assert len(state["forecasts"]["SeedBlend"][0]["risk"][0]) == len(grid["lats"])
    assert len(state["forecasts"]["SeedBlend"][0]["risk"][0][0]) == len(grid["lons"])
    assert len(state["jet_250hpa"]) == 6
    assert state["reports_12h"] and any(r["graded"] for r in state["reports_12h"])
    g = next(r["graded"] for r in state["reports_12h"] if r["graded"])
    assert {"risk", "percentile", "lead_minutes", "in_top20", "outcome"} <= set(g["SeedBlend"])
    board = state["scoreboard"]["SeedBlend"]
    assert board["at_least_1h"]["n"] > 0
    # fewer reports qualify as the required lead grows
    assert board["at_least_1h"]["n"] >= board.get("at_least_3h", {"n": 0})["n"]
    assert {"lead_minutes", "valid_hour_offset", "sealed_at"} <= set(state["forecasts"]["SeedBlend"][0])
    assert (tmp_path / "live" / "state.json").stat().st_size < 2_000_000


def test_cannot_reseal_the_past(tmp_path):
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0])
    job.cycle(T0)
    # a slow cycle: by sealing time the first valid hour is about to pass and is dropped, not sealed late
    now[0] = T0 + timedelta(minutes=49, seconds=30)
    job.cycle(T0 + timedelta(minutes=1))
    assert all(from_iso(r["target_time"]) > from_iso(r["sealed_at"]) for r in job.ledger.sealed())
    assert job.ledger.verify()[0]


def test_loop_slots_avoid_the_top_of_the_hour():
    from datetime import datetime, timezone
    from lab.tasks.turb.live import seconds_to_next_slot
    t = datetime(2026, 11, 13, 22, 6, tzinfo=timezone.utc).timestamp()
    nxt = datetime.fromtimestamp(t + seconds_to_next_slot(t, 1800), timezone.utc)
    assert (nxt.hour, nxt.minute) == (22, 35)


def test_at_least_k_hours_uses_the_latest_record_old_enough():
    from lab.tasks.turb.live import at_least
    rows = pd.DataFrame({
        "model": ["M"] * 3, "seq": [1, 2, 3], "lead_minutes": [250.0, 130.0, 50.0],
        "valid_time": ["2026-11-13T18:00:00Z"] * 3, "time": ["2026-11-13T18:10:00Z"] * 3,
        "lat": [40.0] * 3, "lon": [-100.0] * 3, "fl": [340.0] * 3, "risk": [0.1, 0.2, 0.3]})
    assert at_least(rows, 1)["seq"].tolist() == [2]   # sealed 130 min before: the latest at least 1 h old
    assert at_least(rows, 3)["seq"].tolist() == [1]
    assert at_least(rows, 5).empty
