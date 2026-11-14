"""Read-only views of lab run directories (runs/lab/<problem>/<run_id>/) for the dashboard.

Tolerant of a run being written while it is read: a summary.json caught
mid-write is retried briefly and otherwise reported as unreadable, and a
partial last line of findings.jsonl is skipped.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _read_json(path: Path, tries: int = 3, wait_s: float = 0.05) -> dict | None:
    for i in range(tries):
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (ValueError, OSError):
            if i + 1 < tries:
                time.sleep(wait_s)
    return None


def _read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except (FileNotFoundError, OSError):
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue           # a line still being written
        if isinstance(row, dict):
            out.append(row)
    return out


def _row(run_dir: Path) -> dict | None:
    summary = _read_json(run_dir / "summary.json")
    config = _read_json(run_dir / "config.json")
    if summary is None and config is None:
        if not (run_dir / "summary.json").exists() and not (run_dir / "config.json").exists():
            return None
    s, c = summary or {}, config or {}
    inc = s.get("incumbent") or {}
    status = s.get("status") or ("unreadable" if (run_dir / "summary.json").exists() else "starting")
    return {
        "problem": run_dir.parent.name, "run_id": run_dir.name,
        "mode": s.get("mode") or c.get("mode") or "pipeline", "status": status,
        "loss_name": s.get("loss_name") or c.get("loss_name"),
        "baseline_select": (s.get("baseline_incumbent") or {}).get("select"),
        "baseline_confirm": (s.get("baseline_incumbent") or {}).get("confirm"),
        "generations_done": s.get("generations_done"),
        "generations_planned": s.get("generations_planned", c.get("generations")),
        "incumbent_select": inc.get("select"), "incumbent_confirm": inc.get("confirm"),
        "cost_usd": s.get("cost_usd"), "evaluations": s.get("evaluations"),
        "accepted_total": s.get("accepted_total"), "became_incumbent_total": s.get("became_incumbent_total"),
        "memory": s.get("memory", c.get("memory")), "search": c.get("search"),
        "started_at": s.get("started_at") or c.get("started_at"), "updated_at": s.get("updated_at"),
    }


def list_runs(root: str | Path) -> list[dict]:
    """One row per run directory under root/<problem>/<run_id>, newest first."""
    root = Path(root)
    rows = []
    if not root.is_dir():
        return rows
    for problem_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in problem_dir.iterdir() if p.is_dir()):
            row = _row(run_dir)
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda r: (r.get("started_at") or "", r["run_id"]), reverse=True)
    return rows


def run_detail(root: str | Path, problem: str, run_id: str) -> dict | None:
    """summary.json, config.json, findings and the incumbent source of one run;
    None if the names are not plain directory names or the run does not exist."""
    if not (SAFE.match(problem or "") and SAFE.match(run_id or "")) or ".." in (problem + run_id):
        return None
    run_dir = Path(root) / problem / run_id
    if not run_dir.is_dir():
        return None
    try:
        incumbent = (run_dir / "incumbent.py").read_text()
    except (FileNotFoundError, OSError):
        incumbent = None
    summary = _read_json(run_dir / "summary.json")
    return {"problem": problem, "run_id": run_id,
            "summary": summary, "summary_readable": summary is not None or not (run_dir / "summary.json").exists(),
            "config": _read_json(run_dir / "config.json"),
            "findings": _read_jsonl(run_dir / "findings.jsonl"),
            "incumbent_source": incumbent}
