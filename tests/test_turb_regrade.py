"""Correcting turbulence grades formed from incomplete reports: selection, the
regrade script, and what the scoreboard and state show (fake archive, no network)."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from lab.core.ledger import SealedLedger
from lab.core.timeutil import from_iso, to_iso
from lab.tasks.turb import fields as F
from lab.tasks.turb import live as L
from lab.tasks.turb import pireps as P
from lab.tasks.turb import regrade as R

_HERE = Path(__file__).parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


syn = _load("turb_synthetic", _HERE / "fixtures" / "turb" / "synthetic.py")
script = _load("turb_regrade_script", _HERE.parent / "scripts" / "turb_regrade.py")

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=3)
REPORTS = syn.reports(T0 - timedelta(days=1), T0 + timedelta(days=3), per_hour=30, seed=5)
UP = P.upper(P.in_domain(REPORTS))
MODELS = ("SeedBlend", "Climatology")
HOLE = (datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc), datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc))
FROZEN = set(range(4, 10))     # batch B: graded in the first cycle after the gap


def _prediction(model):
    spec = F.grid_spec()
    shape = (len(spec["levels_hpa"]), len(spec["lats"]), len(spec["lons"]))
    lat = np.array(spec["lats"], float)
    rough = np.exp(-((lat - 38) / 5) ** 2)[None, :, None] * np.ones(shape)   # where the synthetic air is rough
    risk = rough if model == "SeedBlend" else np.full(shape, 0.2)
    return {"risk": L._round_grid(risk), "units": "probability", "grid": spec}


def _seal(led, at, hours):
    led.seal_many([{"task": L.TASK, "model": m, "case_id": f"turb/{m}/{h}/issued-{to_iso(at)}", "issued_at": at,
                    "target_time": at + timedelta(hours=h), "prediction": _prediction(m), "meta": {}}
                   for h in hours for m in MODELS], now=at)


def _expected_n(valid: str) -> int:
    return int(((UP["time"] - pd.Timestamp(from_iso(valid))).abs() <= L.TRUTH_WINDOW).sum())


def _labelled(valid: str) -> int:
    """Report layers in the window with a class (light-moderate is not scored)."""
    w = UP[(UP["time"] - pd.Timestamp(from_iso(valid))).abs() <= L.TRUTH_WINDOW]
    return int(w["code"].map(P.label).notna().sum())


def _frozen(tmp_path) -> SealedLedger:
    """A ledger as the old job left it: batch A graded normally; batch B graded in
    the first cycle after a 37-hour gap against a store that held nothing (16:00,
    18:00) or only part of the window (17:00)."""
    led = SealedLedger(tmp_path / "live")
    _seal(led, T0, (1, 2))                                   # seq 0-3, valid 13:00 and 14:00
    led.grade_many([(r["seq"], L.window_truth(r["target_time"], UP, ["iem"])) for r in led.sealed()],
                   now=T0 + timedelta(hours=4))
    _seal(led, T0 + timedelta(hours=3), (1, 2, 3))           # seq 4-9, valid 16:00, 17:00, 18:00
    _seal(led, T0 + timedelta(hours=40), (1,))               # seq 10-11: the first seal after the gap
    old = []
    for r in led.sealed()[4:10]:
        full = L.window_truth(r["target_time"], UP)
        if r["target_time"].startswith("2026-11-13T17"):
            old.append((r["seq"], {**full, "n": 2, "reports": full["reports"][:2]}))
        else:
            old.append((r["seq"], {**full, "n": 0, "reports": []}))
    led.grade_many(old, now=T0 + timedelta(hours=40, seconds=10))
    return led


def _archive(hole=None, calls=None):
    """A fake IEM archive: (report rows of the UTC day up to now, time of the fetch)."""
    def fetch_day(day, now):
        if calls is not None:
            calls.append(day)
        t = REPORTS["time"]
        keep = (t >= pd.Timestamp(day)) & (t < pd.Timestamp(min(day + timedelta(days=1), now)))
        if hole is not None:
            keep &= ~((t >= pd.Timestamp(hole[0])) & (t < pd.Timestamp(hole[1])))
        return syn.report_rows(REPORTS[keep]), now
    return fetch_day


def _state(led):
    return L.build_state(led, P.read_store(led.dir / "no_store.csv", legacy=None), None, NOW,
                         backtest_path=led.dir / "no_backtest.json", coefficients={}, verify=led.verify())


def test_selection_finds_the_grades_of_the_first_cycle_after_the_gap(tmp_path):
    led = _frozen(tmp_path)
    calls = []
    plan = R.plan(led.dir, tmp_path / "store.csv", _archive(calls=calls), NOW)
    assert plan.gaps == [(T0 + timedelta(hours=3), T0 + timedelta(hours=40))]
    assert plan.first_after == {T0 + timedelta(hours=40, seconds=10)}
    assert set(plan.suspect) == FROZEN
    assert plan.reasons == {"n0_with_archive_reports": 4, "first_cycle_after_gap": 6}
    assert calls == [datetime(2026, 11, 13, tzinfo=timezone.utc)]   # only the days graded windows touch
    assert set(plan.truths) == FROZEN and plan.uncovered == []
    for seq, t in plan.truths.items():
        valid = plan.by_seq[seq]["target_time"]
        assert t == L.window_truth(valid, UP, ["iem"]) and t["n"] == _expected_n(valid) > 2
        assert t["coverage"] == "complete"


def test_records_the_archive_does_not_cover_are_left_alone(tmp_path):
    led = _frozen(tmp_path)
    plan = R.plan(led.dir, tmp_path / "store.csv", _archive(hole=HOLE), NOW)
    assert set(plan.suspect) == FROZEN
    covered = {s for s in FROZEN if plan.by_seq[s]["target_time"] == "2026-11-13T16:00:00Z"}
    assert set(plan.truths) == covered and set(plan.uncovered) == FROZEN - covered


def test_script_writes_each_regrade_once_and_never_touches_sealed_or_grades(tmp_path, capsys):
    led = _frozen(tmp_path)
    store = tmp_path / "store.csv"
    untouched = {p: p.read_bytes() for p in (led.sealed_path, led.grades_path)}
    before = {m: v["at_least_1h"] for m, v in L.scoreboard(L.graded_scores(led)).items()}

    args = ["--live-dir", str(led.dir), "--store", str(store)]
    assert script.main(args, fetch_day=_archive(), now=NOW) == 0          # no --apply: prints only
    dry = json.loads(capsys.readouterr().out)
    assert (dry["applied"], dry["selected"], dry["would_write"], dry["written"]) == (False, 6, 6, 0)
    assert not led.regrades_path.exists()
    assert dry["scoreboard_before"] == before
    assert dry["scoreboard_after"]["SeedBlend"]["n"] > before["SeedBlend"]["n"]

    assert script.main(args + ["--apply"], fetch_day=_archive(), now=NOW) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["applied"], out["written"], out["already_regraded"]) == (True, 6, 0)
    assert out["scoreboard_before"] == before and out["scoreboard_after"] == dry["scoreboard_after"]
    assert out["verify"] == {"ok": True, "checked": 12, "message": "ok"}
    lines = led.regrades()
    assert sorted(r["seq"] for r in lines) == sorted(FROZEN) and {r["reason"] for r in lines} == {R.REASON}
    assert {r["replaces"]["graded_at"] for r in lines} == {"2026-11-15T04:00:10Z"}
    assert all(p.read_bytes() == b for p, b in untouched.items())
    assert {m: v["at_least_1h"] for m, v in L.scoreboard(L.graded_scores(led)).items()} == out["scoreboard_after"]

    written = led.regrades_path.read_bytes()
    assert script.main(args + ["--apply"], fetch_day=_archive(), now=NOW) == 0   # a second run writes nothing
    again = json.loads(capsys.readouterr().out)
    assert (again["written"], again["would_write"], again["already_regraded"]) == (0, 0, 6)
    assert again["scoreboard_before"] == again["scoreboard_after"] == out["scoreboard_after"]
    assert led.regrades_path.read_bytes() == written
    assert all(p.read_bytes() == b for p, b in untouched.items())


def test_state_and_scoreboard_use_the_corrected_truth(tmp_path):
    led = _frozen(tmp_path)
    assert len(led.check_gap(L.TASK, timedelta(hours=6), now=T0 + timedelta(hours=40))) == 1
    led.update_gaps(NOW)
    s0 = _state(led)
    assert s0["regrades"] == {"count": 0, "reasons": [], "valid_from": None, "valid_to": None}
    assert (s0["gaps"][0]["graded_late"], s0["gaps"][0]["regraded"]) == (6, 0)
    scores0 = L.graded_scores(led)
    assert set(scores0["seq"]) & FROZEN == {s for s in FROZEN if led.sealed()[s]["target_time"].endswith("17:00:00Z")}

    plan = R.plan(led.dir, tmp_path / "store.csv", _archive(), NOW)
    led.regrade_many(sorted(plan.truths.items()), R.REASON, now=NOW)
    led.update_gaps(NOW)
    s1 = _state(led)
    assert s1["regrades"] == {"count": 6, "reasons": [R.REASON],
                              "valid_from": "2026-11-13T16:00:00Z", "valid_to": "2026-11-13T18:00:00Z"}
    assert (s1["gaps"][0]["graded_late"], s1["gaps"][0]["regraded"]) == (6, 6)
    assert s1["ledger"]["graded"] == s0["ledger"]["graded"] == 10 and s1["ledger"]["verify"]["ok"]
    scores1 = L.graded_scores(led)
    for seq in FROZEN:                                     # one scored row per corrected report layer
        assert (scores1["seq"] == seq).sum() == _expected_n(led.sealed()[seq]["target_time"])
    n0, n1 = (s["scoreboard"]["SeedBlend"]["at_least_1h"]["n"] for s in (s0, s1))
    assert n1 == sum(_labelled(f"2026-11-13T{h}:00:00Z") for h in (13, 14, 16, 17, 18)) > n0
