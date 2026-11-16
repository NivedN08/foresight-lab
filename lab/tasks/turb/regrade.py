"""Turbulence grades formed without full report coverage, and their corrected truths.

Shared by scripts/turb_regrade_dry_run.py (read only) and scripts/turb_regrade.py
(appends regrades with --apply).

A grade is selected if
  * its truth has n == 0 although the IEM archive holds upper-level reports
    for its window, or
  * it was written in the first grading cycle after a gap of more than six
    hours between seals (the store then held only the last few hours).
Each selected grade's truth is recomputed with the job's own grading function
(live.window_truth) from the IEM archive merged with the live report store,
but only where the archive fetch covers the whole window by the job's rule
(pireps.covered_spans); a window that is not covered is left for a later run.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from lab.core.ledger import SealedLedger
from lab.core.timeutil import from_iso, to_iso

from . import live as L
from . import pireps as P

REASON = "graded without report coverage after a 35.7 h freeze; corrected from the IEM archive"
GAP = timedelta(hours=6)

# (UTC day, now) -> (report rows of that day up to now, the time they were fetched)
FetchDay = Callable[[datetime, datetime], tuple[pd.DataFrame, datetime]]


def main_checkout(root: Path) -> Path:
    """The main checkout of the repository at `root` (which may be a worktree):
    where the live jobs keep their state."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return Path(root)


def read_jsonl(path: Path) -> list[dict]:
    """Lines of a JSON-lines file, opened for reading only (missing file: none)."""
    if not Path(path).exists():
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


class View:
    """Stands in for SealedLedger in live.graded_scores (which only calls joined())."""

    def __init__(self, joined):
        self._joined = joined

    def joined(self):
        return self._joined


def headline(rows: list[dict]) -> dict:
    """The 'at least 1 h' scoreboard row per model for these joined rows."""
    return {m: v.get("at_least_1h") for m, v in L.scoreboard(L.graded_scores(View(rows))).items()}


def archive_day(caches: list[Path], save_to: Path, log=lambda m: None) -> FetchDay:
    """The IEM archive for one UTC day: a cached CSV (under any of `caches`)
    counts only if it was written at least pireps.IEM_SETTLED after the day
    ended and holds reports (the rule of pireps.iem_day_text), and then its
    fetch time is its modification time; otherwise the day is fetched now and
    kept under `save_to` only if it is settled."""
    def fetch(day: datetime, now: datetime) -> tuple[pd.DataFrame, datetime]:
        end = day + timedelta(days=1)
        for c in caches:
            p = Path(c) / f"{day:%Y-%m-%d}.csv"
            if p.exists():
                written = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                text = p.read_text()
                if written >= end + P.IEM_SETTLED and P._has_reports(text):
                    return P.iem_reports(text), written
        text = P.fetch_iem(day, min(end, now), log=log)
        if now >= end + P.IEM_SETTLED and P._has_reports(text):
            save_to.mkdir(parents=True, exist_ok=True)
            (save_to / f"{day:%Y-%m-%d}.csv").write_text(text)
        return P.iem_reports(text), now
    return fetch


def gaps_between_seals(sealed: list[dict], gap: timedelta = GAP) -> list[tuple[datetime, datetime]]:
    times = sorted({from_iso(r["sealed_at"]) for r in sealed})
    return [(a, b) for a, b in zip(times, times[1:]) if b - a > gap]


def first_grading_after(gaps, grades: dict[int, dict]) -> set[datetime]:
    """The first grading time at or after the end of each gap."""
    times = sorted({from_iso(g["graded_at"]) for g in grades.values()})
    return {next(t for t in times if t >= b) for _, b in gaps if any(t >= b for t in times)}


@dataclass
class Plan:
    sealed: list[dict]
    grades: dict[int, dict]                 # the original grade lines
    by_seq: dict[int, dict]
    regraded: set[int]                      # seqs already in regrades.jsonl
    days: list[str]
    gaps: list[tuple[datetime, datetime]]
    first_after: set[datetime]
    suspect: dict[int, list[str]]           # seq -> why it was selected
    reasons: dict[str, int]
    truths: dict[int, dict] = field(default_factory=dict)   # selected and covered: the corrected truth
    uncovered: list[int] = field(default_factory=list)      # selected, but the archive does not cover the window


def plan(live_dir: Path, store: Path, fetch_day: FetchDay, now: datetime, legacy: Path | None = None) -> Plan:
    """Select the grades and recompute their truths. Only reads the ledger files."""
    live_dir = Path(live_dir)
    sealed = [r for r in read_jsonl(live_dir / "sealed.jsonl") if r["task"] == L.TASK]
    by_seq = {r["seq"]: r for r in sealed}
    grades = {g["seq"]: g for g in read_jsonl(live_dir / "grades.jsonl") if g["seq"] in by_seq}
    regraded = {g["seq"] for g in read_jsonl(live_dir / "regrades.jsonl")}
    gaps = gaps_between_seals(sealed)
    first_after = first_grading_after(gaps, grades)
    p = Plan(sealed, grades, by_seq, regraded, [], gaps, first_after, {},
             {"n0_with_archive_reports": 0, "first_cycle_after_gap": 0})
    if not grades:
        return p

    # the IEM archive for every day a graded window touches, and the spans it covers
    targets = [from_iso(by_seq[s]["target_time"]) for s in grades]
    d = (min(targets) - L.TRUTH_WINDOW).replace(hour=0, minute=0, second=0, microsecond=0)
    last = max(targets) + L.TRUTH_WINDOW
    frames, coverage = [], []
    while d <= last:
        rows, fetched_at = fetch_day(d, now)
        frames.append(P.expand_layers(rows))
        coverage += [{"source": "iem", "from": to_iso(a), "to": to_iso(b), "fetched_at": to_iso(fetched_at)}
                     for a, b in P.covered_spans(d, min(d + timedelta(days=1), now), rows["time"], fetched_at)]
        p.days.append(f"{d:%Y-%m-%d}")
        d += timedelta(days=1)
    raw = P.dedupe(pd.concat(frames, ignore_index=True))
    archive = P.upper(P.in_domain(raw))
    merged = P.upper(P.in_domain(P.dedupe(pd.concat([P.read_store(store, legacy=legacy), raw], ignore_index=True))))
    merged = merged[merged["time"] <= pd.Timestamp(now)]
    coverage += P.read_coverage(store)

    for seq, g in grades.items():
        r = by_seq[seq]
        why = []
        if g["truth"].get("n") == 0 and L.window_truth(r["target_time"], archive)["n"] > 0:
            why.append("n0_with_archive_reports")
        if from_iso(g["graded_at"]) in first_after:
            why.append("first_cycle_after_gap")
        if why:
            p.suspect[seq] = why
            for w in why:
                p.reasons[w] += 1

    for seq in sorted(p.suspect):
        v = from_iso(by_seq[seq]["target_time"])
        complete, sources = P.window_coverage(coverage, v - L.TRUTH_WINDOW, v + L.TRUTH_WINDOW)
        if complete:
            p.truths[seq] = L.window_truth(by_seq[seq]["target_time"], merged, sources)
        else:
            p.uncovered.append(seq)
    return p


def with_truths(rows: list[dict], truths: dict[int, dict]) -> list[dict]:
    """Joined rows with these truths put in place, as joined() shows a regrade."""
    return [{**r, "grade": {**r["grade"], "truth": truths[r["seq"]]}, "regraded": True}
            if r["seq"] in truths and r["grade"] is not None else r for r in rows]


def run(live_dir: Path, store: Path, fetch_day: FetchDay, now: datetime, apply: bool = False,
        legacy: Path | None = None, reason: str = REASON) -> dict:
    """Regrade every selected, covered grade that has no regrade yet. Without
    `apply` nothing is written. Never touches sealed.jsonl or grades.jsonl."""
    p = plan(live_dir, store, fetch_day, now, legacy=legacy)
    led = SealedLedger(live_dir)
    ok, checked, msg = led.verify()
    if not ok:
        raise SystemExit(f"the ledger in {live_dir} does not verify ({msg}); nothing written")
    rows = led.joined()
    todo = {s: t for s, t in p.truths.items() if s not in p.regraded}
    before = headline(rows)
    written = []
    if apply and todo:
        written = led.regrade_many(sorted(todo.items()), reason, now=now)
        rows = led.joined()
        ok, checked, msg = led.verify()
        after = headline(rows)
    else:
        after = headline(with_truths(rows, todo))
    affected = sorted(p.by_seq[s]["target_time"] for s in p.suspect)
    return {"applied": bool(apply), "live_dir": str(live_dir), "archive_days": p.days,
            "selected": len(p.suspect), "selected_by_reason": p.reasons,
            "valid_from": affected[0] if affected else None, "valid_to": affected[-1] if affected else None,
            "not_covered": len(p.uncovered), "already_regraded": len(set(p.truths) & p.regraded),
            "would_write": len(todo), "written": len(written), "reason": reason,
            "verify": {"ok": ok, "checked": checked, "message": msg},
            "scoreboard_before": before, "scoreboard_after": after}
