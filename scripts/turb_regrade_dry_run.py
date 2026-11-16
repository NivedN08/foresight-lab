"""Dry run: which turbulence grades were formed without full report coverage,
and what the scoreboard would be with corrected truths. READ-ONLY on the ledger.

    .venv/bin/python scripts/turb_regrade_dry_run.py [--live-dir DIR] [--out FILE]

A grade is suspect if
  * its truth has n == 0 although the IEM archive holds upper-level reports
    for its window, or
  * it was written in the first grading cycle after a gap of more than six
    hours between seals (the store then held only the last few hours).
For each suspect grade the truth is recomputed from the IEM archive (one CSV
per UTC day; read from an existing day cache when present, else fetched with
the project's fetch function) merged with the live report store. The ledger
files are only opened for reading; nothing is written next to them.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from lab.core.timeutil import from_iso, to_iso, utcnow
from lab.tasks.turb import live as L
from lab.tasks.turb import pireps as P

ROOT = Path(__file__).resolve().parents[1]
GAP = timedelta(hours=6)


def main_checkout() -> Path:
    """The main checkout of this repository (this file may sit in a worktree)."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return ROOT


def read_jsonl(path: Path) -> list[dict]:
    with open(path) as fh:  # read only
        return [json.loads(line) for line in fh if line.strip()]


class View:
    """Stands in for SealedLedger in live.graded_scores (which only calls joined())."""

    def __init__(self, joined):
        self._joined = joined

    def joined(self):
        return self._joined


def day_text(d: datetime, caches: list[Path], save_to: Path, now: datetime) -> str:
    for c in caches:
        p = c / f"{d:%Y-%m-%d}.csv"
        if p.exists():
            return p.read_text()
    text = P.fetch_iem(d, min(d + timedelta(days=1), now), log=lambda m: None)
    if now >= d + timedelta(days=1) + P.IEM_SETTLED and len(text.strip().splitlines()) > 1:
        save_to.mkdir(parents=True, exist_ok=True)
        (save_to / f"{d:%Y-%m-%d}.csv").write_text(text)
    return text


def truth_for(target: str, up: pd.DataFrame) -> dict:
    v = pd.Timestamp(from_iso(target))
    w = up[(up["time"] - v).abs() <= L.TRUTH_WINDOW]
    return {"window_minutes": int(L.TRUTH_WINDOW.total_seconds() // 60), "n": int(len(w)),
            "reports": [{"time": to_iso(t.to_pydatetime()), "lat": round(float(a), 4), "lon": round(float(b), 4),
                         "fl": float(f), "code": int(c), "layer": int(l)}
                        for t, a, b, f, c, l in zip(w["time"], w["lat"], w["lon"], w["fl"], w["code"], w["layer"])]}


def _key(rep: dict) -> tuple:
    return (rep["time"], rep["lat"], rep["lon"], rep["fl"], rep["code"])


def main(argv=None) -> int:
    main_root = main_checkout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-dir", type=Path, default=main_root / "runs" / "turb" / "live")
    ap.add_argument("--store", type=Path, default=main_root / "cache" / "turb" / "pireps_live_reports.csv",
                    help="the live report store (read only)")
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / "turb_regrade_dry_run.json")
    a = ap.parse_args(argv)
    now = utcnow().replace(microsecond=0)

    sealed = [r for r in read_jsonl(a.live_dir / "sealed.jsonl") if r["task"] == L.TASK]
    grades = {g["seq"]: g for g in read_jsonl(a.live_dir / "grades.jsonl")}
    by_seq = {r["seq"]: r for r in sealed}

    # gaps between seals longer than GAP, and the first grading time after each
    seal_times = sorted({from_iso(r["sealed_at"]) for r in sealed})
    gaps = [(x, y) for x, y in zip(seal_times, seal_times[1:]) if y - x > GAP]
    graded_times = sorted({from_iso(g["graded_at"]) for g in grades.values()})
    first_after = {next(t for t in graded_times if t >= y) for _, y in gaps if any(t >= y for t in graded_times)}

    # the IEM archive for every day a graded window touches, plus the live store
    targets = [from_iso(by_seq[s]["target_time"]) for s in grades if s in by_seq]
    d = (min(targets) - L.TRUTH_WINDOW).replace(hour=0, minute=0, second=0, microsecond=0)
    last = max(targets) + L.TRUTH_WINDOW
    caches = [ROOT / "cache" / "turb" / "iem", main_root / "cache" / "turb" / "iem"]
    frames, days = [], []
    while d <= last:
        frames.append(P.parse_iem_csv(day_text(d, caches, ROOT / "cache" / "turb" / "iem", now)))
        days.append(f"{d:%Y-%m-%d}")
        d += timedelta(days=1)
    archive = P.upper(P.in_domain(P.dedupe(pd.concat(frames, ignore_index=True))))
    legacy = a.store.parent / P.LEGACY_STORE.name
    store = P.read_store(a.store, legacy=legacy if legacy.exists() else None)
    merged = P.upper(P.in_domain(P.dedupe(pd.concat([store, P.dedupe(pd.concat(frames, ignore_index=True))],
                                                   ignore_index=True))))

    suspect, reasons = {}, {"n0_with_archive_reports": 0, "first_cycle_after_gap": 0}
    for seq, g in grades.items():
        r = by_seq.get(seq)
        if r is None:
            continue
        why = []
        if g["truth"]["n"] == 0 and truth_for(r["target_time"], archive)["n"] > 0:
            why.append("n0_with_archive_reports")
        if from_iso(g["graded_at"]) in first_after:
            why.append("first_cycle_after_gap")
        if why:
            suspect[seq] = why
            for w in why:
                reasons[w] += 1

    corrected, changes = {}, []
    for seq in suspect:
        old = grades[seq]["truth"]
        new = truth_for(by_seq[seq]["target_time"], merged)
        corrected[seq] = new
        if {_key(x) for x in old["reports"]} != {_key(x) for x in new["reports"]}:
            changes.append({"seq": seq, "model": by_seq[seq]["model"], "valid_time": by_seq[seq]["target_time"],
                            "graded_at": grades[seq]["graded_at"], "why": suspect[seq],
                            "n_old": old["n"], "n_new": new["n"]})

    def joined(mode):
        out = []
        for r in sealed:
            g = grades.get(r["seq"])
            if g is not None and r["seq"] in suspect:
                if mode == "excluded":
                    g = None
                elif mode == "corrected":
                    g = {**g, "truth": corrected[r["seq"]]}
            out.append({**r, "grade": g})
        return out

    boards = {mode: L.scoreboard(L.graded_scores(View(joined(mode))))
              for mode in ("as_is", "corrected", "excluded")}
    headline = {m: {model: boards[m].get(model, {}).get("at_least_1h") for model in ("SeedBlend", "Ellrod1", "GAirmet",
                                                                                      "Climatology")}
                for m in boards}
    dn = [c["n_new"] - c["n_old"] for c in changes]
    report = {
        "generated_at": to_iso(now), "live_dir": str(a.live_dir), "store": str(a.store), "archive_days": days,
        "grades_total": len(grades),
        "gaps_over_6h": [{"from": to_iso(x), "to": to_iso(y), "hours": round((y - x).total_seconds() / 3600, 2)}
                         for x, y in gaps],
        "first_grading_after_gap": sorted(to_iso(t) for t in first_after),
        "suspect_grades": len(suspect), "suspect_by_reason": reasons,
        "grades_that_would_change": len(changes),
        "report_count_change": {"total_old": sum(c["n_old"] for c in changes),
                                "total_new": sum(c["n_new"] for c in changes),
                                "sum_increase": sum(dn), "mean_increase": round(sum(dn) / len(dn), 2) if dn else 0,
                                "max_increase": max(dn, default=0), "min_increase": min(dn, default=0),
                                "were_zero": sum(1 for c in changes if c["n_old"] == 0)},
        "scoreboard_at_least_1h": headline,
        "scoreboard": boards,
        "changes": changes,
        "note": "Dry run. Nothing in the ledger directory was written. 'corrected' replaces each suspect "
                "grade's truth with the archive + live-store reports in its window; 'excluded' drops those grades.",
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("grades_total", "gaps_over_6h", "first_grading_after_gap",
                                              "suspect_grades", "suspect_by_reason", "grades_that_would_change",
                                              "report_count_change", "scoreboard_at_least_1h")}, indent=1))
    print(f"written {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
