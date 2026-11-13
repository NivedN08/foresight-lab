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


@app.get("/")
def index():
    return FileResponse(DASHBOARD / "index.html")


app.mount("/", StaticFiles(directory=DASHBOARD), name="dashboard")
