"""Read-only views of lab run directories (runs/lab/<problem>/<run_id>/) for the dashboard.

Tolerant of a run being written while it is read: a summary.json caught
mid-write is retried briefly and otherwise reported as unreadable, and a
partial last line of findings.jsonl is skipped.
"""
from __future__ import annotations

import calendar
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


# ---------------------------------------------------------------- experiments
# runs/exp/<matrix>/manifest.json, report.json, and one run directory per
# runs/exp/<matrix>/<arm>/seed<k>/ (a normal lab run directory).

SEED_DIR = re.compile(r"^seed(\d{1,6})$")
FINISHED = ("finished", "stopped_cost_cap")


def _seed_dirs(matrix_dir: Path):
    """(arm, seed, run_dir) for every runs/exp/<matrix>/<arm>/seed<k>/ directory."""
    for arm_dir in sorted(p for p in matrix_dir.iterdir() if p.is_dir() and SAFE.match(p.name)):
        for run_dir in sorted(p for p in arm_dir.iterdir() if p.is_dir()):
            m = SEED_DIR.match(run_dir.name)
            if m:
                yield arm_dir.name, int(m.group(1)), run_dir


def list_exp_runs(root: str | Path) -> list[dict]:
    """One row per experiment run (same fields as list_runs, plus source, matrix, arm, seed)."""
    root = Path(root)
    rows = []
    if not root.is_dir():
        return rows
    for matrix_dir in sorted(p for p in root.iterdir() if p.is_dir() and SAFE.match(p.name)):
        for arm, seed, run_dir in _seed_dirs(matrix_dir):
            row = _row(run_dir)
            if row is None:
                continue
            s = _read_json(run_dir / "summary.json") or {}
            c = _read_json(run_dir / "config.json") or {}
            row.update({"source": "exp", "matrix": matrix_dir.name, "arm": arm, "seed": seed,
                        "problem": s.get("problem") or c.get("problem") or row["problem"],
                        "run_id": s.get("run_id") or c.get("run_id") or run_dir.name})
            rows.append(row)
    return rows


def exp_run_detail(root: str | Path, matrix: str, arm: str, seed: str | int) -> dict | None:
    """run_detail for runs/exp/<matrix>/<arm>/seed<k>; seed may be '3' or 'seed3'."""
    seed = str(seed)
    seed = seed[4:] if seed.startswith("seed") else seed
    if not (SAFE.match(matrix or "") and SAFE.match(arm or "") and seed.isdigit()) or ".." in (matrix + arm):
        return None
    arm_dir = Path(root) / matrix / arm
    d = run_detail(arm_dir.parent, arm, f"seed{int(seed)}")
    if d is None:
        return None
    s, c = d.get("summary") or {}, d.get("config") or {}
    d.update({"source": "exp", "matrix": matrix, "arm": arm, "seed": int(seed),
              "problem": s.get("problem") or c.get("problem"),
              "run_id": s.get("run_id") or c.get("run_id") or f"seed{int(seed)}"})
    return d


def _exp_runs(matrix_dir: Path, manifest: dict) -> list[dict]:
    """Every planned or seen run of a matrix with its status and final losses, read from its
    own summary.json (the same file the report reads) or, before it has one, the manifest."""
    by_key = {}
    for m in manifest.get("runs") or []:
        if isinstance(m, dict) and m.get("arm") is not None and m.get("seed") is not None:
            by_key[(m["arm"], int(m["seed"]))] = {"arm": m["arm"], "seed": int(m["seed"]),
                                                   "manifest_status": m.get("status"),
                                                   "message": m.get("message")}
    for arm, seed, run_dir in _seed_dirs(matrix_dir):
        by_key.setdefault((arm, seed), {"arm": arm, "seed": seed, "manifest_status": None, "message": None})
        by_key[(arm, seed)]["dir"] = run_dir
    order = [a.get("name") for a in ((manifest.get("config") or {}).get("arms") or []) if isinstance(a, dict)]
    out = []
    for (arm, seed), r in sorted(by_key.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else len(order), kv[0][0], kv[0][1])):
        run_dir = r.pop("dir", None)
        s = _read_json(run_dir / "summary.json") if run_dir else None
        status = r["manifest_status"] or "queued"
        if s and s.get("status"):
            status = "crashed" if r["manifest_status"] == "crashed" and s["status"] not in FINISHED else s["status"]
        inc = (s or {}).get("incumbent") or {}
        r.update({"status": status, "has_run": s is not None,
                  "run_id": (s or {}).get("run_id"), "problem": (s or {}).get("problem"),
                  "final_select": inc.get("select"), "final_confirm": inc.get("confirm"),
                  "cost_usd": (s or {}).get("cost_usd"),
                  "generations_done": (s or {}).get("generations_done"),
                  "generations_planned": (s or {}).get("generations_planned"),
                  "updated_at": (s or {}).get("updated_at")})
        out.append(r)
    return out


SILENT_S = 45 * 60


def _age_s(iso: str | None) -> float:
    """Seconds since an ISO time like 2026-11-14T01:05:00Z; 0 if missing or unreadable."""
    if not iso:
        return 0.0
    try:
        return time.time() - calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0.0


def list_matrices(root: str | Path) -> list[dict]:
    """One row per runs/exp/<matrix>/ that has a manifest.json, most recently updated first."""
    root = Path(root)
    rows = []
    if not root.is_dir():
        return rows
    for matrix_dir in sorted(p for p in root.iterdir() if p.is_dir() and SAFE.match(p.name)):
        manifest = _read_json(matrix_dir / "manifest.json")
        if manifest is None and not (matrix_dir / "manifest.json").exists():
            continue
        manifest = manifest or {}
        cfg = manifest.get("config") or {}
        runs = _exp_runs(matrix_dir, manifest)
        report = matrix_dir / "report.json"
        updated = [manifest.get("updated_at") or ""] + [r.get("updated_at") or "" for r in runs]
        if report.exists():
            updated.append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(report.stat().st_mtime)))
        rows.append({
            "name": matrix_dir.name, "problem": cfg.get("problem"),
            "arms": [a.get("name") for a in cfg.get("arms") or [] if isinstance(a, dict)],
            "seeds": cfg.get("seeds") or [],
            "runs_planned": len(runs),
            "runs_finished": sum(1 for r in runs if r["status"] in FINISHED),
            "runs_running": sum(1 for r in runs if r["status"] == "running"),
            # marked running but no summary update for 45 minutes: the process has probably stopped
            "runs_silent": sum(1 for r in runs if r["status"] == "running" and _age_s(r.get("updated_at")) > SILENT_S),
            "has_report": report.exists(), "manifest_readable": bool(manifest),
            "updated_at": max(updated) or None,
        })
    rows.sort(key=lambda r: r["updated_at"] or "", reverse=True)
    return rows


def matrix_detail(root: str | Path, matrix: str) -> dict | None:
    """The manifest, report.json (if any) and the runs of one matrix; None if it does not exist."""
    if not SAFE.match(matrix or "") or ".." in matrix:
        return None
    matrix_dir = Path(root) / matrix
    if not (matrix_dir / "manifest.json").exists():
        return None
    manifest = _read_json(matrix_dir / "manifest.json")
    report = _read_json(matrix_dir / "report.json")
    report_path = matrix_dir / "report.json"
    return {"name": matrix, "manifest": manifest, "manifest_readable": manifest is not None,
            "report": report, "report_exists": report_path.exists(),
            "report_readable": report is not None or not report_path.exists(),
            "report_written_at": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(report_path.stat().st_mtime))
                                  if report_path.exists() else None),
            "runs": _exp_runs(matrix_dir, manifest or {})}
