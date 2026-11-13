"""Local server for the dashboard.

Serves the static dashboard and hands it each application's latest state file.
Run:  .venv/bin/python -m uvicorn lab.server:app --port 8765
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lab.core.ledger import SealedLedger

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
DASHBOARD = ROOT / "dashboard"
TASKS = ("coast", "turb")

app = FastAPI(title="foresight-lab")


def _task_dir(task: str) -> Path:
    if task not in TASKS:
        raise HTTPException(404, f"unknown application '{task}'")
    return RUNS / task / "live"


@app.get("/api/{task}/state")
def state(task: str):
    path = _task_dir(task) / "state.json"
    if not path.exists():
        return JSONResponse({"task": task, "empty": True,
                             "message": "No forecasts sealed yet. Start the live job for this application."})
    try:
        return JSONResponse(json.loads(path.read_text()), headers={"Cache-Control": "no-store"})
    except json.JSONDecodeError:
        # the live job may be mid-write; the dashboard retries on its next poll
        raise HTTPException(503, "state file is being written")


@app.get("/api/{task}/backtest")
def backtest(task: str):
    if task not in TASKS:
        raise HTTPException(404, f"unknown application '{task}'")
    out = {}
    for path in sorted((RUNS / task).glob("backtest*.json")):
        try:
            out[path.stem] = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/api/{task}/ledger")
def ledger(task: str, tail: int = 40):
    led = SealedLedger(_task_dir(task))
    ok, n, message = led.verify()
    joined = led.joined()
    graded = sum(1 for r in joined if r["grade"])
    slim = [
        {k: r[k] for k in ("seq", "model", "case_id", "issued_at", "target_time", "sealed_at", "hash")}
        | {"graded_at": r["grade"]["graded_at"] if r["grade"] else None}
        for r in joined[-max(1, min(tail, 500)):]
    ]
    return JSONResponse({"records": n, "graded": graded, "verified": ok, "message": message,
                         "head": led.head()[1], "tail": slim},
                        headers={"Cache-Control": "no-store"})


_RECORDS_CACHE: dict[str, tuple] = {}


def _joined_cached(task: str) -> list[dict]:
    """Sealed records joined with their grades, re-read only when a file changed."""
    led = SealedLedger(_task_dir(task))
    key = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                for p in (led.sealed_path, led.grades_path))
    hit = _RECORDS_CACHE.get(task)
    if hit is None or hit[0] != key:
        hit = (key, led.joined())
        _RECORDS_CACHE[task] = hit
    return hit[1]


@app.get("/api/{task}/records")
def records(task: str, model: str | None = None, kind: str | None = None, station: str | None = None,
            graded: str | None = None, due: bool = False, since_hours: float | None = None,
            per_target: str | None = None, prediction: bool = True, limit: int = 40):
    """Recent sealed records with their prediction and grade, for the log row and the
    turbulence time scrubber.

    model: one model name; kind: the record's task name (coast_water, coast_waves, turb);
    station: a coast station or buoy id (meta.station / meta.buoy); graded: "yes" or "no";
    due: only records whose target time has passed; since_hours: only target times within
    this many hours before now; per_target="latest": per model and target time keep only the
    latest-sealed record (the one the turbulence grading reports on); limit: newest records
    kept (max 500).
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    lo = (now - timedelta(hours=since_hours)).strftime("%Y-%m-%dT%H:%M:%SZ") if since_hours else None
    hi = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for r in _joined_cached(task):
        if model and r["model"] != model:
            continue
        if kind and r.get("task") != kind:
            continue
        meta = r.get("meta") or {}
        if station and station not in (meta.get("station"), meta.get("buoy")):
            continue
        if graded == "yes" and not r["grade"]:
            continue
        if graded == "no" and r["grade"]:
            continue
        if due and r["target_time"] > hi:
            continue
        if lo and r["target_time"] < lo:
            continue
        out.append(r)
    if per_target == "latest":
        best: dict[tuple, dict] = {}
        for r in out:
            k = (r["model"], r["target_time"])
            if k not in best or r["seq"] > best[k]["seq"]:
                best[k] = r
        out = sorted(best.values(), key=lambda r: r["seq"])
    out = out[-max(1, min(limit, 500)):]
    keep = ("seq", "task", "model", "case_id", "issued_at", "target_time", "sealed_at", "hash", "meta")
    slim = [{k: r.get(k) for k in keep}
            | ({"prediction": r["prediction"]} if prediction else {})
            | {"grade": r["grade"]} for r in out]
    return JSONResponse({"now": hi, "records": slim}, headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    return FileResponse(DASHBOARD / "index.html")


app.mount("/", StaticFiles(directory=DASHBOARD), name="dashboard")
