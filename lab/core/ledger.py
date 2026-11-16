"""Sealed forecast ledger.

A forecast is "sealed" by appending it to an append-only file in which every
record carries the hash of the record before it. Changing or removing any
earlier record breaks every later hash, so a forecast cannot be edited after
the truth is known without detection. Truth is written to a separate file and
never touches the sealed file.

Rules enforced here:
  * a forecast can only be sealed for a target time that is still in the future;
  * a forecast can only be graded at or after its target time;
  * a forecast is graded at most once.

Gaps: after a job seals, every stretch longer than its threshold between two
consecutive seals is recorded once in gaps.jsonl (also append-only, outside
the chain): from the last seal before it to the first seal after it, the chain
position on each side, and how many records came due meanwhile. Later counts
(graded late, regraded, never gradable) are appended as follow-up lines.

Regrades: a grade whose truth was formed from incomplete data (for example
reports that had not been fetched yet) can be corrected once, by appending a
line to regrades.jsonl: {seq, sealed_hash, truth, reason, replaces: {graded_at,
n}, recorded_at}. This is allowed only for that purpose, and the reason must
say what was incomplete. The original line in grades.jsonl is kept unchanged
and stays readable through grades(); joined() hands readers the corrected
truth and marks the row as regraded. A record can be regraded at most once,
only if it has a grade, and verify() checks every regrade against the sealed
record and the grade it replaces.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .timeutil import from_iso, to_iso, utcnow

GENESIS = "0" * 64
GAP_CAUSE = "no forecast sealed in this period"  # cycles may have run and failed; the job cannot know why


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_record(body: dict) -> str:
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


class LedgerError(Exception):
    pass


class SealedLedger:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sealed_path = self.dir / "sealed.jsonl"
        self.grades_path = self.dir / "grades.jsonl"
        self.anchors_path = self.dir / "anchors.jsonl"
        self.gaps_path = self.dir / "gaps.jsonl"
        self.regrades_path = self.dir / "regrades.jsonl"
        self._lock_path = self.dir / ".lock"

    # ------------------------------------------------------------------ io
    @contextmanager
    def _locked(self) -> Iterator[None]:
        with open(self._lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def sealed(self) -> list[dict]:
        return self._read(self.sealed_path)

    def grades(self) -> dict[int, dict]:
        return {g["seq"]: g for g in self._read(self.grades_path)}

    def head(self) -> tuple[int, str]:
        """(number of sealed records, hash of the last one)."""
        records = self.sealed()
        if not records:
            return 0, GENESIS
        return len(records), records[-1]["hash"]

    # ---------------------------------------------------------------- seal
    def seal_many(self, forecasts: Iterable[dict], now: datetime | None = None) -> list[dict]:
        """Seal a batch. Each forecast is a dict with keys:
        task, model, case_id, issued_at (datetime), target_time (datetime),
        prediction (json-able dict), and optional meta (json-able dict).
        """
        out: list[dict] = []
        with self._locked():
            n, prev = self.head()
            sealed_at = now or utcnow()
            with open(self.sealed_path, "a") as fh:
                for f in forecasts:
                    target: datetime = f["target_time"]
                    if target <= sealed_at:
                        raise LedgerError(
                            f"cannot seal {f['case_id']}: target {to_iso(target)} is not after "
                            f"the sealing time {to_iso(sealed_at)}"
                        )
                    body = {
                        "seq": n,
                        "task": f["task"],
                        "model": f["model"],
                        "case_id": f["case_id"],
                        "issued_at": to_iso(f["issued_at"]),
                        "target_time": to_iso(target),
                        "prediction": f["prediction"],
                        "meta": f.get("meta") or {},
                        "sealed_at": to_iso(sealed_at),
                        "prev_hash": prev,
                    }
                    body["hash"] = _hash_record(body)
                    fh.write(_canonical(body) + "\n")
                    out.append(body)
                    prev = body["hash"]
                    n += 1
        return out

    def seal(self, **forecast: Any) -> dict:
        now = forecast.pop("now", None)
        return self.seal_many([forecast], now=now)[0]

    # --------------------------------------------------------------- grade
    def grade(self, seq: int, truth: dict, now: datetime | None = None) -> dict:
        return self.grade_many([(seq, truth)], now=now)[0]

    def grade_many(self, items: Iterable[tuple[int, dict]], now: datetime | None = None) -> list[dict]:
        out: list[dict] = []
        with self._locked():
            records = self.sealed()
            already = self.grades()
            graded_at = now or utcnow()
            with open(self.grades_path, "a") as fh:
                for seq, truth in items:
                    if seq < 0 or seq >= len(records):
                        raise LedgerError(f"no sealed record {seq}")
                    if seq in already:
                        raise LedgerError(f"record {seq} is already graded")
                    rec = records[seq]
                    if graded_at < from_iso(rec["target_time"]):
                        raise LedgerError(
                            f"record {seq} cannot be graded before its target time {rec['target_time']}"
                        )
                    g = {
                        "seq": seq,
                        "sealed_hash": rec["hash"],
                        "truth": truth,
                        "graded_at": to_iso(graded_at),
                    }
                    fh.write(_canonical(g) + "\n")
                    already[seq] = g
                    out.append(g)
        return out

    def ungraded_due(self, now: datetime | None = None) -> list[dict]:
        """Sealed records whose target time has passed and that have no grade yet."""
        now = now or utcnow()
        graded = self.grades()
        return [
            r for r in self.sealed()
            if r["seq"] not in graded and from_iso(r["target_time"]) <= now
        ]

    def joined(self) -> list[dict]:
        """Sealed records with their grade (or None) attached under 'grade'. A
        regraded record gets the corrected truth, the regrade's reason, what it
        replaces and when under grade['regrade'], and 'regraded': True."""
        graded = self.grades()
        fixed = {g["seq"]: g for g in self.regrades()}
        out = []
        for r in self.sealed():
            g, rg = graded.get(r["seq"]), fixed.get(r["seq"])
            if g is not None and rg is not None:
                out.append({**r, "grade": {**g, "truth": rg["truth"],
                                           "regrade": {k: rg[k] for k in ("reason", "replaces", "recorded_at")}},
                            "regraded": True})
            else:
                out.append({**r, "grade": g})
        return out

    # ------------------------------------------------------------- regrade
    def regrades(self) -> list[dict]:
        """The regrade lines, oldest first (empty if there is no regrades.jsonl)."""
        return self._read(self.regrades_path)

    def regrade(self, seq: int, truth: dict, reason: str, now: datetime | None = None) -> dict:
        return self.regrade_many([(seq, truth)], reason, now=now)[0]

    def regrade_many(self, items: Iterable[tuple[int, dict]], reason: str,
                     now: datetime | None = None) -> list[dict]:
        """Correct the truth of graded records whose grade was formed from
        incomplete data, by appending to regrades.jsonl. Every item is checked
        before anything is written: each seq must have a grade and no earlier
        regrade, and `reason` must say what was incomplete. No existing line
        of any file is changed."""
        if not isinstance(reason, str) or not reason.strip():
            raise LedgerError("a regrade needs a reason (what was incomplete about the original truth)")
        items = list(items)
        with self._locked():
            records, graded = self.sealed(), self.grades()
            done = {g["seq"] for g in self.regrades()}
            recorded_at = to_iso(now or utcnow())
            out = []
            for seq, truth in items:
                if seq not in graded or not 0 <= seq < len(records):
                    raise LedgerError(f"record {seq} has no grade to correct")
                if seq in done:
                    raise LedgerError(f"record {seq} is already regraded")
                old = graded[seq]
                out.append({"seq": seq, "sealed_hash": records[seq]["hash"], "truth": truth, "reason": reason,
                            "replaces": {"graded_at": old["graded_at"], "n": old["truth"].get("n")},
                            "recorded_at": recorded_at})
                done.add(seq)
            if out:
                with open(self.regrades_path, "a") as fh:
                    fh.writelines(_canonical(g) + "\n" for g in out)
        return out

    # -------------------------------------------------------------- verify
    def verify(self) -> tuple[bool, int, str]:
        """Recompute the whole chain. Returns (ok, records checked, message)."""
        prev = GENESIS
        records = self.sealed()
        for i, rec in enumerate(records):
            if rec.get("seq") != i:
                return False, i, f"record {i} has seq {rec.get('seq')}"
            if rec.get("prev_hash") != prev:
                return False, i, f"record {i} does not chain to the record before it"
            body = {k: v for k, v in rec.items() if k != "hash"}
            if _hash_record(body) != rec.get("hash"):
                return False, i, f"record {i} was modified after sealing"
            if from_iso(rec["target_time"]) <= from_iso(rec["sealed_at"]):
                return False, i, f"record {i} was sealed after its target time"
            prev = rec["hash"]
        graded, seen = self.grades(), set()
        for seq, g in graded.items():
            if seq >= len(records) or g["sealed_hash"] != records[seq]["hash"]:
                return False, seq, f"grade for record {seq} does not match the sealed record"
            if from_iso(g["graded_at"]) < from_iso(records[seq]["target_time"]):
                return False, seq, f"grade for record {seq} predates its target time"
        for rg in self.regrades():
            seq = rg.get("seq")
            if not isinstance(seq, int) or not 0 <= seq < len(records):
                return False, seq, f"regrade for record {seq} refers to no sealed record"
            if rg.get("sealed_hash") != records[seq]["hash"]:
                return False, seq, f"regrade for record {seq} does not match the sealed record (sealed_hash differs)"
            if seq not in graded:
                return False, seq, f"regrade for record {seq} has no original grade to correct"
            if (rg.get("replaces") or {}).get("graded_at") != graded[seq]["graded_at"]:
                return False, seq, f"regrade for record {seq} does not name the grade it replaces"
            if seq in seen:
                return False, seq, f"record {seq} is regraded more than once"
            seen.add(seq)
        return True, len(records), "ok"

    def anchor(self, note: str = "") -> dict:
        """Record the current head hash with a timestamp. Publishing this line
        somewhere public (a commit, a post) proves what had been sealed by then."""
        n, h = self.head()
        a = {"at": to_iso(utcnow()), "records": n, "head": h, "note": note}
        with self._locked(), open(self.anchors_path, "a") as fh:
            fh.write(_canonical(a) + "\n")
        return a

    # ---------------------------------------------------------------- gaps
    def gaps(self) -> list[dict]:
        """Recorded gaps, oldest first, each with the counts of its latest follow-up
        line. `graded_late` counts every record due in the gap that has a grade;
        `regraded` how many of those grades were later corrected (0 when no
        line carries the count, as in lines written before regrades existed)."""
        out: dict[tuple, dict] = {}
        for g in self._read(self.gaps_path):
            ref = g.get("follow_up_of")
            if ref is None:
                out[(g["task"], g["from"])] = {**g, "regraded": g.get("regraded", 0)}
            elif (ref["task"], ref["from"]) in out:
                out[(ref["task"], ref["from"])].update(graded_late=g["graded_late"],
                                                       unrecoverable=g["unrecoverable"],
                                                       regraded=g.get("regraded", 0),
                                                       updated_at=g["recorded_at"])
        return list(out.values())

    def _due_between(self, records: list[dict], start: str, end: str) -> list[dict]:
        a, b = from_iso(start), from_iso(end)
        return [r for r in records if a < from_iso(r["target_time"]) <= b]

    def gap(self, task: str, start: datetime, end: datetime, cause: str = GAP_CAUSE,
            now: datetime | None = None) -> dict | None:
        """Record that nothing was sealed from `start` to `end`, in gaps.jsonl next
        to the chain (the chain itself is not touched). Returns the new record, or
        None if this gap (same task and start) is already recorded."""
        with self._locked():
            records = self.sealed()
            frm, to = to_iso(start), to_iso(end)
            if any(g["task"] == task and g["from"] == frm for g in self.gaps()):
                return None
            before = [r for r in records if from_iso(r["sealed_at"]) <= start]
            after = [r for r in records if from_iso(r["sealed_at"]) >= end]
            due = self._due_between(records, frm, to)
            graded = self.grades()
            g = {"task": task, "from": frm, "to": to, "hours": round((end - start).total_seconds() / 3600, 2),
                 "cause": cause,
                 "seq_before": before[-1]["seq"] if before else None,
                 "head_before": before[-1]["hash"] if before else GENESIS,
                 "seq_after": after[0]["seq"] if after else len(records),
                 "due_while_away": len(due), "graded_late": sum(r["seq"] in graded for r in due),
                 "unrecoverable": 0, "recorded_at": to_iso(now or utcnow())}
            with open(self.gaps_path, "a") as fh:
                fh.write(_canonical(g) + "\n")
        return g

    def check_gap(self, task: str, threshold: timedelta, cause: str = GAP_CAUSE,
                  now: datetime | None = None) -> list[dict]:
        """Called after a cycle has sealed: every stretch longer than `threshold`
        between two consecutive sealing times is recorded (once each), from the
        last seal before it to the first seal after it. Returns the new records."""
        times = sorted({from_iso(r["sealed_at"]) for r in self.sealed()})
        out = []
        for a, b in zip(times, times[1:]):
            if b - a > threshold:
                g = self.gap(task, a, b, cause=cause, now=now)
                if g is not None:
                    out.append(g)
        return out

    def update_gaps(self, now: datetime, unrecoverable: Callable[[dict, datetime], bool] | None = None) -> list[dict]:
        """For every gap, count the records that came due in it and have now been
        graded, how many of those grades were later corrected (regrades.jsonl),
        and those still ungraded that `unrecoverable(record, now)` says will
        never be graded. A change is appended as a follow-up line that names the
        gap; no line is ever edited. Returns the follow-up lines written."""
        written = []
        with self._locked():
            records, graded = self.sealed(), self.grades()
            fixed = {g["seq"] for g in self.regrades()}
            for g in self.gaps():
                due = self._due_between(records, g["from"], g["to"])
                late = sum(r["seq"] in graded for r in due)
                redone = sum(r["seq"] in graded and r["seq"] in fixed for r in due)
                lost = sum(1 for r in due if r["seq"] not in graded and unrecoverable and unrecoverable(r, now))
                if (late, lost, redone) == (g["graded_late"], g["unrecoverable"], g["regraded"]):
                    continue
                written.append({"follow_up_of": {"task": g["task"], "from": g["from"]},
                                "graded_late": late, "unrecoverable": lost, "regraded": redone,
                                "recorded_at": to_iso(now)})
            if written:
                with open(self.gaps_path, "a") as fh:
                    fh.writelines(_canonical(w) + "\n" for w in written)
        return written
