"""A full seal -> grade cycle with a fake clock and fake clients (no network)."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from lab.core.timeutil import from_iso, to_iso
from lab.tasks.turb import fields as F
from lab.tasks.turb import pireps as P
from lab.tasks.turb.live import Clients, LiveJob

_spec = importlib.util.spec_from_file_location(
    "turb_synthetic", Path(__file__).parent / "fixtures" / "turb" / "synthetic.py")
syn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(syn)

T0 = syn.T0  # 2026-11-13 12:10 UTC
SQUARE = [[30, -110], [30, -90], [45, -90], [45, -110], [30, -110]]


ALL_REPORTS = syn.reports(T0 - timedelta(days=3), T0 + timedelta(hours=12), per_hour=30, seed=3)


def _between(a, b, hole=None):
    """Synthetic reports with a <= time < b, as received (one row per report);
    `hole` = (start, end) is a stretch the source has nothing for."""
    t = ALL_REPORTS["time"]
    keep = (t >= pd.Timestamp(a)) & (t < pd.Timestamp(b))
    if hole is not None:
        keep &= ~((t >= pd.Timestamp(hole[0])) & (t < pd.Timestamp(hole[1])))
    return syn.report_rows(ALL_REPORTS[keep])


def _job(tmp_path, clock, archive_hole=None, with_archive=True):
    all_reports = ALL_REPORTS
    store = F.FieldStore(tmp_path / "fields")
    store.add(syn.fieldset(T0 - timedelta(days=3), 72, fetched_at=T0.replace(minute=0)), "backfill", T0 - timedelta(hours=1))
    calls = {"fields": 0, "archive": []}

    def fetch_fields(now):
        calls["fields"] += 1
        start = now.replace(minute=0, second=0) - timedelta(hours=6)
        return syn.fieldset(start, 18, fetched_at=now)

    def recent(now):  # as the real sources: AWC keeps 2 hours, the IEM request asks for 6
        end = now + timedelta(seconds=1)
        return [P.Fetched("awc", now - timedelta(hours=2), now, _between(now - timedelta(hours=2), end)),
                P.Fetched("iem", now - timedelta(hours=6), now, _between(now - timedelta(hours=6), end))]

    def archive(day, now):  # the IEM archive for one UTC day, up to now
        calls["archive"].append(day)
        return _between(day, min(day + timedelta(days=1), now + timedelta(seconds=1)), archive_hole)

    def history(now):
        return all_reports[all_reports["time"] < pd.Timestamp(now - timedelta(hours=2))]

    def gairmets(now):
        base = now.replace(minute=0, second=0)
        return [{"valid_time": (base + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z"), "issue_time": 0,
                 "base_fl": 180, "top_fl": 380, "severity": "MOD", "coords": SQUARE} for h in (0, 3, 6, 9)]

    job = LiveJob(Clients(fetch_fields, recent, history, gairmets, archive if with_archive else None),
                  ledger_dir=tmp_path / "live", field_store=store, report_store=tmp_path / "pireps.csv.gz",
                  backtest_path=tmp_path / "no_backtest.json", log=lambda m: None, clock=clock)
    return job, calls


def _expected_n(valid: str) -> int:
    """Upper-level report layers within 30 minutes of a valid hour (all synthetic
    reports are one layer at FL210 or above, inside the domain)."""
    v = pd.Timestamp(from_iso(valid))
    return int(((ALL_REPORTS["time"] - v).abs() <= pd.Timedelta(minutes=30)).sum())


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

    # 14:20: the 13:00 records are 75 minutes past their valid hour, but their window
    # reaches into 13:00-14:00, which counts as covered only from 15:00 (late reports)
    now[0] = T0 + timedelta(minutes=130)
    out = job.cycle(now[0])
    assert out["graded"] == 0
    assert calls["fields"] == 1  # fields reused within 3 hours

    # seven hours later (19:10): every record valid up to 17:00 is due, 18:00 is not yet
    now[0] = T0 + timedelta(hours=7)
    out = job.cycle(now[0])
    assert calls["fields"] == 2
    grades = job.ledger.grades()
    for r in job.ledger.sealed():
        due = from_iso(r["target_time"]) + timedelta(minutes=75) <= now[0]
        assert (r["seq"] in grades) == due, r["case_id"]
    assert len(grades) == 7 * 5 + 7 * 3  # cycle 1: 13-17 h; cycle 2 (14:20): 15-17 h
    truth = next(g for s, g in grades.items() if job.ledger.sealed()[s]["target_time"] == "2026-11-13T13:00:00Z")["truth"]
    assert truth["n"] == _expected_n("2026-11-13T13:00:00Z") > 0 and truth["window_minutes"] == 30
    assert all(abs(from_iso(x["time"]) - from_iso("2026-11-13T13:00:00Z")) <= timedelta(minutes=30)
               and x["fl"] >= 200 for x in truth["reports"])
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


# ------------------------------------------------------- frozen jobs and coverage
HOLE = (datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc), datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc))
IN_HOLE = {"2026-11-13T16:00:00Z", "2026-11-13T17:00:00Z"}   # their +-30 minute windows touch 16:00-17:00


def test_frozen_job_grades_the_gap_from_the_archive(tmp_path):
    now = [T0]
    job, calls = _job(tmp_path, lambda: now[0], archive_hole=HOLE)
    job.cycle(T0)                                    # seals valid hours 13:00 .. 18:00
    first = {r["seq"]: r for r in job.ledger.sealed()}
    assert len(first) == 42

    now[0] = T0 + timedelta(hours=36)                # nothing ran for 36 hours
    job.cycle(now[0])
    grades = job.ledger.grades()
    for seq, r in first.items():
        if r["target_time"] in IN_HOLE:              # the archive has nothing for that hour: wait
            assert seq not in grades, r["case_id"]
            continue
        truth = grades[seq]["truth"]
        assert truth["n"] == _expected_n(r["target_time"]) > 0, r["case_id"]
        assert truth["sources"] == ["iem"] and truth["coverage"] == "complete"
    assert sum(s in grades for s in first) == 4 * 7
    # every day from the oldest uncovered window to now
    assert calls["archive"] == [datetime(2026, 11, d, tzinfo=timezone.utc) for d in (13, 14, 15)]

    [gap] = job.ledger.gaps()
    assert (gap["task"], gap["from"], gap["to"], gap["hours"]) == \
        ("turb", first[41]["sealed_at"], to_iso(now[0]), 36.0)
    assert (gap["seq_before"], gap["head_before"], gap["seq_after"]) == (41, first[41]["hash"], 42)
    assert (gap["due_while_away"], gap["graded_late"], gap["unrecoverable"]) == (42, 28, 0)
    state = json.loads((tmp_path / "live" / "state.json").read_text())
    assert state["gaps"] == job.ledger.gaps()

    # half an hour later: retried, still uncovered, nothing graded twice, no second gap
    now[0] += timedelta(minutes=30)
    job.cycle(now[0])
    assert {s for s in first if s in job.ledger.grades()} == {s for s in first if s in grades}
    assert len(job.ledger.gaps()) == 1
    assert job.ledger.verify()[0]

    # more than 7 days past their windows and still uncovered: never graded, counted as lost
    now[0] = T0 + timedelta(days=9, hours=12)
    job.cycle(now[0])
    lost = [s for s, r in first.items() if r["target_time"] in IN_HOLE]
    assert not any(s in job.ledger.grades() for s in lost)
    g = job.ledger.gaps()[0]
    assert (g["graded_late"], g["unrecoverable"]) == (28, len(lost))
    assert len(job.ledger.gaps_path.read_text().splitlines()) >= 3   # first lines plus follow-ups
    ok, n, msg = job.ledger.verify()
    assert ok, msg


def test_no_grade_without_coverage(tmp_path):
    """Before the fix, this cycle graded all 42 overdue records, most with n == 0."""
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0], with_archive=False)
    job.cycle(T0)
    now[0] = T0 + timedelta(hours=36)
    out = job.cycle(now[0])
    assert out["graded"] == 0 and not job.ledger.grades()
    assert job.ledger.verify()[0]


def test_cycle_interrupted_before_sealing_starts_again(tmp_path):
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0])
    job.cycle(T0)
    t1 = T0 + timedelta(hours=2)
    now[0] = t1
    history, jumps = job.clients.fetch_history_reports, []

    def sleepy_history(at):          # the machine sleeps for 40 minutes while the models are fitted
        if not jumps:
            jumps.append(at)
            now[0] += timedelta(minutes=40)
        return history(at)

    job.clients.fetch_history_reports = sleepy_history
    out = job.cycle(t1)
    new = [r for r in job.ledger.sealed() if r["seq"] >= 42]
    assert out["sealed"] == len(new) == 42
    assert {r["issued_at"] for r in new} == {to_iso(t1 + timedelta(minutes=40))}   # the fresh cycle's time
    assert all(from_iso(r["sealed_at"]) - from_iso(r["issued_at"]) <= timedelta(minutes=10)
               for r in job.ledger.sealed())
    assert len(job.ledger.gaps()) == 1           # seen by both attempts, written once
    assert job.ledger.verify()[0]


def test_fields_are_stamped_with_the_download_time(tmp_path):
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0])
    fetch = job.clients.fetch_fields

    def slow_fetch(at):              # the download finishes 20 minutes after the cycle started
        fs = fetch(at)
        now[0] += timedelta(minutes=20)
        return fs

    job.clients.fetch_fields = slow_fetch
    job.cycle(T0)
    t_dl = T0 + timedelta(minutes=20)
    assert job.store.latest_fetch("live") == t_dl
    live = [c for p, c in zip(job.store.paths(), job.store.chunks()) if p.stem.startswith("live_")]
    assert len(live) == 1 and int(live[0].avail.max()) == int(t_dl.timestamp())
    sealed = job.ledger.sealed()
    assert len(sealed) == 42 and {r["issued_at"] for r in sealed} == {to_iso(t_dl)}
    assert all(from_iso(r["meta"]["fields_available_at"]) <= t_dl for r in sealed if "fields_available_at" in r["meta"])


def test_gap_ends_at_the_first_seal_after_it(tmp_path):
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0])
    job.cycle(T0)
    history = job.clients.fetch_history_reports
    now[0] = T0 + timedelta(hours=36)

    def broken(at):
        raise RuntimeError("history fetch failed")

    job.clients.fetch_history_reports = broken          # the first cycle back fails before sealing
    try:
        job.cycle(now[0])
    except RuntimeError:
        pass
    assert job.ledger.gaps() == []                       # its end is not known yet
    job.clients.fetch_history_reports = history
    now[0] = T0 + timedelta(hours=40)
    job.cycle(now[0])
    [g] = job.ledger.gaps()
    first_after = next(r for r in job.ledger.sealed() if r["seq"] == 42)
    assert (g["from"], g["to"], g["hours"], g["seq_after"]) == \
        (to_iso(T0), first_after["sealed_at"], 40.0, 42)
    assert g["cause"] == "no forecast sealed in this period"


def test_coverage_waits_for_late_reports(tmp_path):
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0], with_archive=False)
    job.cycle(T0)
    first13 = {r["seq"] for r in job.ledger.sealed() if r["target_time"] == "2026-11-13T13:00:00Z"}
    now[0] = datetime(2026, 11, 13, 14, 5, tzinfo=timezone.utc)   # 35 minutes after the 13:00 window ended
    job.cycle(now[0])
    assert not first13 & set(job.ledger.grades())
    now[0] = datetime(2026, 11, 13, 15, 5, tzinfo=timezone.utc)   # a later fetch, after the hour settled
    job.cycle(now[0])
    assert first13 <= set(job.ledger.grades())


def test_coverage_file_stays_small(tmp_path):
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0], with_archive=False)
    for k in range(20):
        now[0] = T0 + timedelta(minutes=30 * k)
        job.update_reports(now[0])
    cov = P.read_coverage(job.report_store)
    lines = P.coverage_path(job.report_store).read_text().splitlines()
    assert len(lines) == len(cov)                       # the file holds merged spans only
    assert [c for c in cov if c["source"] == "iem"] == [json.loads(x) for x in lines if '"iem"' in x]
    assert len([c for c in cov if c["source"] == "iem"]) == 1   # 20 overlapping IEM fetches: one line
    assert P.window_coverage(cov, T0, T0 + timedelta(hours=7)) == (True, ["awc", "iem"])
    now[0] += timedelta(days=15)                        # spans no record can still use are dropped
    job.update_reports(now[0])
    assert P.coverage_path(job.report_store).read_text().splitlines() == []


def test_given_up_records_stay_given_up_after_a_restart(tmp_path):
    now = [T0]
    job, _ = _job(tmp_path, lambda: now[0], with_archive=False)
    job.cycle(T0)
    first = {r["seq"] for r in job.ledger.sealed()}
    now[0] = T0 + timedelta(days=9)                    # never covered, 9 days later
    job.cycle(now[0])
    assert job.lost() == first and not first & set(job.ledger.grades())
    lines = job.lost_path.read_text().splitlines()

    job2, _ = _job(tmp_path, lambda: now[0], with_archive=False)   # a restarted process
    assert not first & {r["seq"] for r in job2._due(now[0])}
    P.record_coverage(job2.report_store, "iem", [(T0 - timedelta(days=1), now[0])], now[0])   # coverage turns up
    now[0] += timedelta(minutes=30)
    job2.cycle(now[0])
    assert not first & set(job2.ledger.grades())        # still never graded
    assert job2.lost_path.read_text().splitlines()[:len(lines)] == lines
    assert job2.lost() >= first and len(job2.lost_path.read_text().splitlines()) == len(job2.lost())


def test_iem_day_cache_keeps_only_settled_days(tmp_path, monkeypatch):
    import os
    from lab.tasks.turb import net
    monkeypatch.setattr(net, "CACHE", tmp_path)
    day = datetime(2026, 11, 13, tzinfo=timezone.utc)
    full = "VALID,URGENT,AIRCRAFT,REPORT,ICING,TURBULENCE,ATRCC,PRODUCT_ID,FL,LAT,LON\nrow\n"
    empty = "VALID,URGENT,AIRCRAFT,REPORT,ICING,TURBULENCE,ATRCC,PRODUCT_ID,FL,LAT,LON\n"
    reply, fetches = [full], []

    def fake_fetch(a, b, log=print):
        fetches.append(a)
        return reply[0]

    monkeypatch.setattr(P, "fetch_iem", fake_fetch)
    path = tmp_path / "iem" / "2026-11-13.csv"
    soon = day + timedelta(days=1, hours=4)              # 4 hours after the day ended: not kept
    assert P.iem_day_text(day, soon, log=lambda m: None) == full and not path.exists()
    P.iem_day_text(day, soon, log=lambda m: None)
    assert len(fetches) == 2
    reply[0] = empty                                     # settled but empty: not kept
    late = day + timedelta(days=2, hours=1)
    P.iem_day_text(day, late, log=lambda m: None)
    assert not path.exists()
    reply[0] = full                                      # settled with reports: kept for good
    P.iem_day_text(day, late, log=lambda m: None)
    assert path.exists() and len(fetches) == 4
    t = (day + timedelta(days=2, hours=1)).timestamp()
    os.utime(path, (t, t))                               # an existing file written after the rule: read
    assert P.iem_day_text(day, late, log=lambda m: None) == full and len(fetches) == 4
    t = (day + timedelta(days=1, hours=3)).timestamp()
    os.utime(path, (t, t))                               # an old-rule file (3 hours after): fetched again
    P.iem_day_text(day, late, log=lambda m: None)
    assert len(fetches) == 5
