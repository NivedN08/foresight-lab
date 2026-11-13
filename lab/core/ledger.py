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
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from .timeutil import from_iso, to_iso, utcnow

GENESIS = "0" * 64


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
        """Sealed records with their grade (or None) attached under 'grade'."""
        graded = self.grades()
        return [{**r, "grade": graded.get(r["seq"])} for r in self.sealed()]

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
        for seq, g in self.grades().items():
            if seq >= len(records) or g["sealed_hash"] != records[seq]["hash"]:
                return False, seq, f"grade for record {seq} does not match the sealed record"
            if from_iso(g["graded_at"]) < from_iso(records[seq]["target_time"]):
                return False, seq, f"grade for record {seq} predates its target time"
        return True, len(records), "ok"

    def anchor(self, note: str = "") -> dict:
        """Record the current head hash with a timestamp. Publishing this line
        somewhere public (a commit, a post) proves what had been sealed by then."""
        n, h = self.head()
        a = {"at": to_iso(utcnow()), "records": n, "head": h, "note": note}
        with self._locked(), open(self.anchors_path, "a") as fh:
            fh.write(_canonical(a) + "\n")
        return a
