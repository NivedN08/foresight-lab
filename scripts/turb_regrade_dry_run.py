"""Dry run: which turbulence grades were formed without full report coverage,
and what the scoreboard would be with corrected truths. READ-ONLY on the ledger.

    .venv/bin/python scripts/turb_regrade_dry_run.py [--live-dir DIR] [--out FILE]

The selection and the corrected truths come from lab/tasks/turb/regrade.py
(shared with scripts/turb_regrade.py, which can append the corrections):
a grade is suspect if
  * its truth has n == 0 although the IEM archive holds upper-level reports
    for its window, or
  * it was written in the first grading cycle after a gap of more than six
    hours between seals (the store then held only the last few hours).
For each suspect grade the truth is recomputed from the IEM archive (one CSV
per UTC day; read from a settled day cache when present, else fetched) merged
with the live report store, where the archive covers the whole window. The
ledger files are only opened for reading; nothing is written next to them.
The scoreboards here use the original grade lines ('as_is'), whatever
regrades.jsonl holds.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab.core.timeutil import to_iso, utcnow  # noqa: E402
from lab.tasks.turb import live as L  # noqa: E402
from lab.tasks.turb import pireps as P  # noqa: E402
from lab.tasks.turb import regrade as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _key(rep: dict) -> tuple:
    return (rep["time"], rep["lat"], rep["lon"], rep["fl"], rep["code"])


def main(argv=None) -> int:
    main_root = R.main_checkout(ROOT)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-dir", type=Path, default=main_root / "runs" / "turb" / "live")
    ap.add_argument("--store", type=Path, default=main_root / "cache" / "turb" / "pireps_live_reports.csv",
                    help="the live report store (read only)")
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / "turb_regrade_dry_run.json")
    a = ap.parse_args(argv)
    now = utcnow().replace(microsecond=0)

    fetch_day = R.archive_day([ROOT / "cache" / "turb" / "iem", main_root / "cache" / "turb" / "iem"],
                              ROOT / "cache" / "turb" / "iem")
    legacy = a.store.parent / P.LEGACY_STORE.name
    p = R.plan(a.live_dir, a.store, fetch_day, now, legacy=legacy if legacy.exists() else None)

    changes = []
    for seq, new in p.truths.items():
        old, r = p.grades[seq]["truth"], p.by_seq[seq]
        if {_key(x) for x in old["reports"]} != {_key(x) for x in new["reports"]}:
            changes.append({"seq": seq, "model": r["model"], "valid_time": r["target_time"],
                            "graded_at": p.grades[seq]["graded_at"], "why": p.suspect[seq],
                            "n_old": old["n"], "n_new": new["n"]})

    def joined(mode):
        out = []
        for r in p.sealed:
            g = p.grades.get(r["seq"])
            if g is not None and r["seq"] in p.suspect:
                if mode == "excluded":
                    g = None
                elif mode == "corrected" and r["seq"] in p.truths:
                    g = {**g, "truth": p.truths[r["seq"]]}
            out.append({**r, "grade": g})
        return out

    boards = {mode: L.scoreboard(L.graded_scores(R.View(joined(mode))))
              for mode in ("as_is", "corrected", "excluded")}
    headline = {m: {model: boards[m].get(model, {}).get("at_least_1h") for model in ("SeedBlend", "Ellrod1", "GAirmet",
                                                                                      "Climatology")}
                for m in boards}
    dn = [c["n_new"] - c["n_old"] for c in changes]
    report = {
        "generated_at": to_iso(now), "live_dir": str(a.live_dir), "store": str(a.store), "archive_days": p.days,
        "grades_total": len(p.grades),
        "gaps_over_6h": [{"from": to_iso(x), "to": to_iso(y), "hours": round((y - x).total_seconds() / 3600, 2)}
                         for x, y in p.gaps],
        "first_grading_after_gap": sorted(to_iso(t) for t in p.first_after),
        "suspect_grades": len(p.suspect), "suspect_by_reason": p.reasons,
        "suspect_not_covered_by_archive": len(p.uncovered),
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
                "grade's truth with the archive + live-store reports in its window (where the archive covers "
                "it); 'excluded' drops those grades.",
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("grades_total", "gaps_over_6h", "first_grading_after_gap",
                                              "suspect_grades", "suspect_by_reason", "suspect_not_covered_by_archive",
                                              "grades_that_would_change", "report_count_change",
                                              "scoreboard_at_least_1h")}, indent=1))
    print(f"written {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
