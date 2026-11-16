"""Correct turbulence grades that were formed without report coverage.

    .venv/bin/python scripts/turb_regrade.py --live-dir DIR [--store FILE] [--apply]

Selects the same grades as scripts/turb_regrade_dry_run.py, recomputes each
truth from the IEM archive (merged with the live report store) with the job's
own grading function, and only where the archive covers the whole window. With
--apply the corrections are appended to DIR/regrades.jsonl with the reason
"graded without report coverage after a 35.7 h freeze; corrected from the IEM
archive"; without it nothing is written. sealed.jsonl and grades.jsonl are
never touched, and a record that already has a regrade is skipped, so a second
run writes nothing. Prints, as JSON, how many regrades were (or would be)
written and the "at least 1 h" scoreboard before and after.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab.core.timeutil import utcnow  # noqa: E402
from lab.tasks.turb import pireps as P  # noqa: E402
from lab.tasks.turb import regrade as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None, fetch_day: R.FetchDay | None = None, now=None) -> int:
    main_root = R.main_checkout(ROOT)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-dir", type=Path, required=True, help="the ledger directory (runs/turb/live)")
    ap.add_argument("--store", type=Path, default=main_root / "cache" / "turb" / "pireps_live_reports.csv",
                    help="the live report store (read only)")
    ap.add_argument("--apply", action="store_true", help="append the regrades (default: only print)")
    a = ap.parse_args(argv)
    now = now or utcnow().replace(microsecond=0)
    if fetch_day is None:
        fetch_day = R.archive_day([ROOT / "cache" / "turb" / "iem", main_root / "cache" / "turb" / "iem"],
                                  ROOT / "cache" / "turb" / "iem")
    legacy = a.store.parent / P.LEGACY_STORE.name
    out = R.run(a.live_dir, a.store, fetch_day, now, apply=a.apply, legacy=legacy if legacy.exists() else None)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
