"""Run a matrix of lab runs: arms x seeds, each in its own subprocess.

    .venv/bin/python -m lab.experiments.matrix run <config.json> [--parallel N] [--only a,b] [--dry-run]

A config (see lab/experiments/configs/ and docs/experiments.md):

    {"name": "claim1_coast", "problem": "coast_water",
     "budget": {"generations": 6, "proposals": 4, "max_cost_usd": 5},
     "seeds": [1, 2],
     "arms": [{"name": "researcher_full", "mode": "researcher", "memory": "vector", "search": "openalex"}, ...]}

Arm keys: mode, memory, search, search_fallback, models, llm, start_source (a file),
carry (none | playbook_only | notebook_only | both | shuffled_notebook | cheat_sheet),
warm_from (a run directory, or {"matrix": m, "arm": a, "seed": "same" | k}),
shuffle_memory, requires_env, comment.

Each run goes to runs/exp/<name>/<arm>/seed<k>/. A run that finished is skipped, one
that was interrupted is resumed, so the command can simply be re-run. Every run is a
separate process (`_worker`), so one crash cannot take the others down.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lab.core.timeutil import to_iso, utcnow

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "runs" / "exp"
FINISHED = ("finished", "stopped_cost_cap")
CARRY = ("none", "playbook_only", "notebook_only", "both", "shuffled_notebook", "cheat_sheet")
ARM_KEYS = {"name", "comment", "mode", "memory", "search", "search_fallback", "models", "llm", "start_source",
            "carry", "warm_from", "shuffle_memory", "requires_env", "seeds"}
CHEAT_SHEET_CHARS = 2000


# ------------------------------------------------------------------ config


def load_config(path) -> dict:
    cfg = json.loads(Path(path).read_text())
    cfg.setdefault("name", Path(path).stem)
    check_config(cfg)
    return cfg


def check_config(cfg: dict) -> None:
    for key in ("problem", "budget", "seeds", "arms"):
        if key not in cfg:
            raise ValueError(f"matrix config needs {key!r}")
    names = [a.get("name") for a in cfg["arms"]]
    if not all(names) or len(set(names)) != len(names):
        raise ValueError("every arm needs a unique name")
    for a in cfg["arms"]:
        unknown = set(a) - ARM_KEYS
        if unknown:
            raise ValueError(f"arm {a['name']}: unknown keys {sorted(unknown)}")
        own = a.get("seeds")
        if own is not None and (not isinstance(own, list) or not own or not all(isinstance(x, int) for x in own)):
            raise ValueError(f"arm {a['name']}: seeds must be a non-empty list of whole numbers")
        carry = a.get("carry", "none")
        if carry not in CARRY:
            raise ValueError(f"arm {a['name']}: carry must be one of {CARRY}")
        if carry in ("playbook_only", "notebook_only", "both", "shuffled_notebook") and not a.get("warm_from"):
            raise ValueError(f"arm {a['name']}: carry={carry!r} needs warm_from")


def required_env(arm: dict) -> list[str]:
    need = list(arm.get("requires_env") or [])
    searches = [arm.get("search") or ""] + list(arm.get("search_fallback") or [])
    if "exa" in searches:
        need.append("EXA_API_KEY")
    if arm.get("memory") == "pastdev":
        need.append("PAST_API_KEY")
    return sorted(set(need))


def resolve_warm_from(spec, seed: int, root: Path) -> Path | None:
    if not spec:
        return None
    if isinstance(spec, dict):
        s = spec.get("seed", "same")
        k = seed if s == "same" else int(s)
        return Path(root) / spec["matrix"] / spec["arm"] / f"seed{k}"
    p = Path(spec)
    return p if p.is_absolute() else PROJECT_ROOT / p


# ------------------------------------------------------------------ run state


def _read_json(path: Path) -> dict | None:
    from lab.swarm.runs_index import _read_json as rj
    return rj(path)


def run_state(run_dir: Path) -> str:
    """'finished' / 'stopped_cost_cap' (done), 'resume' (interrupted, resumable) or 'new'."""
    summary = _read_json(run_dir / "summary.json")
    if summary and summary.get("status") in FINISHED and \
            int(summary.get("generations_done") or 0) >= int(summary.get("generations_planned") or 0):
        return summary["status"]
    if summary is not None and (run_dir / "config.json").exists() and (run_dir / "incumbent.py").exists():
        return "resume"
    return "new"


@dataclass
class PlannedRun:
    arm: str
    seed: int
    run_dir: str
    action: str              # new | resume | done | skipped | blocked
    message: str = ""
    spec: dict = field(default_factory=dict)


def plan(cfg: dict, root: Path, only: list[str] | None = None, env=None) -> list[PlannedRun]:
    env = os.environ if env is None else env
    root = Path(root)
    budget = cfg["budget"]
    out = []
    for arm in cfg["arms"]:
        if only and arm["name"] not in only:
            continue
        for seed in arm.get("seeds") or cfg["seeds"]:     # an arm may have its own seed list
            run_dir = root / cfg["name"] / arm["name"] / f"seed{seed}"
            missing = [k for k in required_env(arm) if not env.get(k)]
            if missing:
                out.append(PlannedRun(arm["name"], seed, str(run_dir), "skipped",
                                      f"arm {arm['name']} needs {', '.join(missing)}, which is not set; skipped"))
                continue
            carry = arm.get("carry", "none")
            warm = resolve_warm_from(arm.get("warm_from"), seed, root)
            if warm is not None and carry not in ("none", "cheat_sheet"):
                st = run_state(warm)
                if st not in FINISHED:
                    out.append(PlannedRun(arm["name"], seed, str(run_dir), "blocked",
                                          f"waiting for {warm} to finish (state: {st})"))
                    continue
            state = run_state(run_dir)
            action = "done" if state in FINISHED else state
            spec = {
                "matrix": cfg["name"], "arm": arm["name"], "seed": seed, "run_dir": str(run_dir),
                "problem": cfg["problem"], "factories": cfg.get("factories") or {},
                "llm": arm.get("llm", cfg.get("llm", "claude_cli")),
                "eval_workers": int(cfg.get("eval_workers", 2)),
                "evaluator": cfg.get("evaluator", "local"),
                "generations": int(budget["generations"]), "proposals": int(budget["proposals"]),
                "max_cost_usd": budget.get("max_cost_usd"),
                "mode": arm.get("mode", "researcher"), "memory": arm.get("memory", "vector"),
                "search": arm.get("search", "openalex"), "search_fallback": arm.get("search_fallback"),
                "models": arm.get("models") or cfg.get("models"),
                "start_source": arm.get("start_source"),
                "carry": carry, "warm_from": str(warm) if warm else None,
                "shuffle_memory": bool(arm.get("shuffle_memory")) or carry == "shuffled_notebook",
                "cheat_sheet_chars": int(cfg.get("cheat_sheet_chars", CHEAT_SHEET_CHARS)),
                "resume": action == "resume",
            }
            msg = {"done": f"already {state}", "resume": "interrupted earlier; will resume",
                   "new": "will start"}[action]
            out.append(PlannedRun(arm["name"], seed, str(run_dir), action, msg, spec))
    return out


# ------------------------------------------------------------------ manifest


class Manifest:
    def __init__(self, path: Path, cfg: dict, config_path: str | None):
        self.path = path
        self._lock = threading.Lock()
        old = _read_json(path) or {}
        self.data = {"name": cfg["name"], "config": cfg, "config_path": config_path,
                     "first_started_at": old.get("first_started_at") or to_iso(utcnow()),
                     "started_at": to_iso(utcnow()), "updated_at": to_iso(utcnow()),
                     "runs": {r["key"]: r for r in old.get("runs", [])} if isinstance(old.get("runs"), list) else {}}

    def set(self, run: PlannedRun, status: str, **extra) -> None:
        key = f"{run.arm}/seed{run.seed}"
        with self._lock:
            row = self.data["runs"].get(key, {"key": key, "arm": run.arm, "seed": run.seed, "run_dir": run.run_dir})
            row.update(status=status, message=extra.pop("message", run.message), **extra)
            self.data["runs"][key] = row
            self.data["updated_at"] = to_iso(utcnow())
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = dict(self.data, runs=list(self.data["runs"].values()))
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1, default=str))
        tmp.replace(self.path)


# ------------------------------------------------------------------ launching


def _env(cfg: dict) -> dict:
    env = dict(os.environ)
    paths = [str(PROJECT_ROOT)] + [str(p) for p in (cfg.get("pythonpath") or [])]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def launch(run: PlannedRun, cfg: dict, manifest: Manifest, python: str) -> int:
    run_dir = Path(run.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "spec.json"
    spec_path.write_text(json.dumps(run.spec, indent=1))
    manifest.set(run, "running", started_at=to_iso(utcnow()), message=run.message)
    t0 = time.monotonic()
    with (run_dir / "worker.log").open("a") as log:
        log.write(f"\n=== {to_iso(utcnow())} {'resume' if run.spec.get('resume') else 'start'} ===\n")
        log.flush()
        p = subprocess.run([python, "-m", "lab.experiments.matrix", "_worker", str(spec_path)],
                           cwd=str(PROJECT_ROOT), env=_env(cfg), stdout=log, stderr=subprocess.STDOUT)
    summary = _read_json(run_dir / "summary.json") or {}
    status = summary.get("status") if p.returncode == 0 else "crashed"
    tail = ""
    if p.returncode != 0:
        try:
            tail = (run_dir / "worker.log").read_text()[-800:]
        except OSError:
            pass
    manifest.set(run, status or "unknown", returncode=p.returncode, ended_at=to_iso(utcnow()),
                 seconds=round(time.monotonic() - t0, 1), message=tail or run.message,
                 cost_usd=summary.get("cost_usd"))
    return p.returncode


def run_matrix(cfg: dict, *, root=DEFAULT_ROOT, parallel: int = 2, only=None, config_path=None,
               python: str = sys.executable, echo=print) -> list[PlannedRun]:
    root = Path(root)
    runs = plan(cfg, root, only)
    manifest = Manifest(root / cfg["name"] / "manifest.json", cfg, config_path)
    todo = []
    for r in runs:
        if r.action in ("new", "resume"):
            todo.append(r)
            manifest.set(r, "queued")
        else:
            manifest.set(r, {"done": run_state(Path(r.run_dir))}.get(r.action, r.action))
        echo(f"{r.arm:<22} seed{r.seed:<4} {r.action:<8} {r.message}")
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futs = {pool.submit(launch, r, cfg, manifest, python): r for r in todo}
        for fut, r in futs.items():
            rc = fut.result()
            echo(f"{r.arm:<22} seed{r.seed:<4} {'exit ' + str(rc):<8} "
                 f"{manifest.data['runs'][f'{r.arm}/seed{r.seed}']['status']}")
    return runs


# ------------------------------------------------------------------ estimates


def measured_rates(problem: str, roots=None) -> list[dict]:
    """Per-proposal cost and per-generation wall-clock from existing runs of `problem`,
    finished runs first, then by number of proposals measured."""
    roots = roots or [PROJECT_ROOT / "runs" / "lab" / problem, DEFAULT_ROOT]
    found = []
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for summ in root.rglob("summary.json"):
            s = _read_json(summ)
            if not s or s.get("problem") != problem or not s.get("generations_done"):
                continue
            n_prop = int(s["generations_done"]) * int(s.get("proposals_per_generation") or 0)
            if n_prop <= 0:
                continue
            roles = s.get("llm_by_role_all") or s.get("llm_by_role") or {}
            eng = (roles.get("engineer") or roles.get("single_agent") or {}).get("cost_usd")
            found.append({"run": str(summ.parent.relative_to(PROJECT_ROOT)) if summ.is_relative_to(PROJECT_ROOT)
                          else str(summ.parent),
                          "status": s.get("status"), "mode": {"swarm": "pipeline"}.get(s.get("mode") or "pipeline",
                                                                       s.get("mode") or "pipeline"),
                          "proposals": n_prop, "proposals_per_generation": s.get("proposals_per_generation"),
                          "usd_per_proposal": float(s.get("cost_usd") or 0) / n_prop,
                          "engineer_usd_per_proposal": float(eng) / n_prop if eng is not None else None,
                          "s_per_generation": float(s.get("wall_s") or 0) / int(s["generations_done"])})
    found.sort(key=lambda r: (r["status"] not in FINISHED, -r["proposals"]))
    return found


def estimate(cfg: dict, runs: list[PlannedRun], parallel: int, rates=None) -> tuple[list[str], dict]:
    """Cost and time per arm from measured runs of the same problem. A run of the same
    mode is used when one has finished; otherwise the largest finished run, with these
    adjustments: random_mutation costs $0 (no model); single_agent is priced at the
    measured engineer cost per proposal (its one call uses the engineer's model); a time
    borrowed from another mode is labelled an upper bound."""
    rates = measured_rates(cfg["problem"]) if rates is None else rates
    budget = cfg["budget"]
    gens, props = int(budget["generations"]), int(budget["proposals"])
    lines, total_usd, total_s, unknown = [], 0.0, 0.0, False
    finished = [r for r in rates if r["status"] in FINISHED]
    for arm in cfg["arms"]:
        n = sum(1 for r in runs if r.arm == arm["name"] and r.action in ("new", "resume"))
        if not n:
            continue
        mode = {"swarm": "pipeline"}.get(arm.get("mode", "researcher"), arm.get("mode", "researcher"))
        same = next((r for r in finished if r["mode"] == mode), None)
        pick = same or (finished[0] if finished else None)
        notes = []
        if mode == "random_mutation":
            usd = 0.0
            notes.append("no model, $0")
        elif pick is None:
            usd = None
        elif same is None and mode == "single_agent" and pick.get("engineer_usd_per_proposal") is not None:
            usd = pick["engineer_usd_per_proposal"] * gens * props
            notes.append(f"priced at the engineer calls of {pick['run']}")
        else:
            usd = pick["usd_per_proposal"] * gens * props
            notes.append(f"rates from {pick['run']}" + ("" if same else f" (mode {pick['mode']})"))
        sec = pick["s_per_generation"] * gens if pick else None
        if pick is not None and same is None:
            notes.append(f"time is an upper bound measured on mode {pick['mode']}")
        if usd is None or sec is None:
            unknown = True
            notes.append("no finished run to measure")
        cap = budget.get("max_cost_usd")
        if usd is not None and cap is not None and usd > float(cap):
            usd = float(cap)
            notes.append("capped by max_cost_usd")
        lines.append(f"  {arm['name']:<22} {n} run(s) x ~" + (f"${usd:.2f}" if usd is not None else "$unknown")
                     + " and ~" + (f"{sec / 60:.0f} min" if sec is not None else "unknown time")
                     + f" per run  ({'; '.join(notes)})")
        if usd is not None:
            total_usd += n * usd
        if sec is not None:
            total_s += n * sec
    return lines, {"usd": None if unknown and total_usd == 0 else total_usd,
                   "minutes_serial": total_s / 60, "minutes_parallel": total_s / 60 / max(1, parallel),
                   "unknown_parts": unknown, "rates": rates[:3]}


def dry_run(cfg: dict, *, root=DEFAULT_ROOT, parallel: int = 2, only=None, echo=print, rates=None) -> dict:
    runs = plan(cfg, Path(root), only)
    echo(f"Matrix {cfg['name']}: problem {cfg['problem']}, {len(cfg['arms'])} arm(s) x {len(cfg['seeds'])} seed(s), "
         f"{cfg['budget']['generations']} generations x {cfg['budget']['proposals']} proposals per run, "
         f"cost cap per run: {cfg['budget'].get('max_cost_usd', 'none')}")
    echo(f"Run directories under {Path(root) / cfg['name']}")
    for r in runs:
        echo(f"  {r.arm:<22} seed{r.seed:<4} {r.action:<8} {r.message}")
    lines, est = estimate(cfg, runs, parallel, rates)
    echo("Estimate (from measured runs; proposals run concurrently within a generation):")
    for line in lines:
        echo(line)
    if est["usd"] is None:
        echo("  total cost: unknown (no finished run of this problem to measure)")
    else:
        echo(f"  total: ~${est['usd']:.2f}" + (" plus unknown parts" if est["unknown_parts"] else "")
             + f"; ~{est['minutes_serial']:.0f} min one at a time, ~{est['minutes_parallel']:.0f} min "
             f"with --parallel {parallel}")
    if est["rates"]:
        echo("  measured runs available:")
        for r in est["rates"]:
            echo(f"    {r['run']} ({r['status']}, mode {r['mode']}): {r['proposals']} proposals, "
                 f"${r['usd_per_proposal']:.3f} per proposal, {r['s_per_generation']:.0f} s per generation "
                 f"of {r['proposals_per_generation']}")
    return {"runs": [asdict(r) for r in runs], "estimate": est}


# ------------------------------------------------------------------ the worker (one run, in its own process)


def _factory(spec: str):
    module, name = spec.split(":")
    return getattr(importlib.import_module(module), name)


def cheat_sheet(problem, max_chars: int = CHEAT_SHEET_CHARS) -> str:
    """The cheat-sheet control: the candidate interface and the problem's own inputs
    and sandbox sections, cut to about a playbook's length. Contains no findings."""
    target = getattr(problem, "target_key", "?")
    head = (f"# Cheat sheet: the candidate interface (general advice, no results from any run)\n"
            f"- The file defines a top-level `class Forecaster` with a `name` string, `fit(self, cases, truths)` "
            f"called once with past cases and truth dicts, and `predict(self, case)` returning a dict with "
            f"the key {target!r} as a finite float.\n"
            f"- `predict` may use only case.case_id, issue_time, target_time, lead_hours and inputs.\n"
            f"- Keep every change small and testable; fall back to a safe value when an input is missing.\n")
    desc = problem.describe() if hasattr(problem, "describe") else ""
    sections, cur = [], None
    for line in desc.splitlines():
        if line.startswith("## "):
            cur = [line]
            sections.append(cur)
        elif cur is not None:
            cur.append(line)
    keep = [s for s in sections if any(w in s[0].lower() for w in ("sandbox", "input", "interface"))]
    keep.sort(key=lambda s: 0 if "sandbox" in s[0].lower() else 1)     # rules first, then inputs
    text = head
    for s in keep:
        block = "\n" + "\n".join(s).strip() + "\n"
        if len(text) + len(block) <= max_chars:
            text += block
            continue
        for line in block.splitlines(keepends=True):
            if len(text) + len(line) > max_chars - 40:
                text += "(cut to length)\n"
                return text
            text += line
    return text


def worker(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text())
    fac = spec.get("factories") or {}
    if fac.get("problem"):
        problem = _factory(fac["problem"])()
    else:
        from lab.evalpool.registry import get_problem
        problem = get_problem(spec["problem"])
    if fac.get("evaluator"):
        evaluator, compare, splits = _factory(fac["evaluator"])(problem)
    else:
        from lab.evalpool.evaluate import compare
        from lab.evalpool.registry import make_pool
        splits = problem.splits()
        evaluator = make_pool(problem, splits, spec.get("evaluator", "local"), workers=spec.get("eval_workers", 2))
    llm = _factory(fac["llm"])(spec) if fac.get("llm") else spec.get("llm") or "claude_cli"

    run_dir = Path(spec["run_dir"])
    carry, warm = spec.get("carry", "none"), spec.get("warm_from")
    start_memory = warm if carry in ("notebook_only", "both", "shuffled_notebook") else None
    playbook = None
    if carry in ("playbook_only", "both"):
        pb = Path(warm) / "playbook.md"
        playbook = pb.read_text() if pb.exists() else None
    elif carry == "cheat_sheet":
        playbook = cheat_sheet(problem, spec.get("cheat_sheet_chars", CHEAT_SHEET_CHARS))
        (run_dir / "cheat_sheet.md").write_text(playbook)
    start_source = None
    if spec.get("start_source"):
        p = Path(spec["start_source"])
        start_source = (p if p.is_absolute() else PROJECT_ROOT / p).read_text()

    from lab.swarm import run_lab
    summary = run_lab(problem, evaluator, compare, llm_backend=llm, memory=spec["memory"], search=spec["search"],
                      search_fallback=spec.get("search_fallback"), generations=spec["generations"],
                      proposals=spec["proposals"], seed=spec["seed"], run_dir=run_dir, models=spec.get("models"),
                      max_cost_usd=spec.get("max_cost_usd"), start_source=None if spec.get("resume") else start_source,
                      start_memory=start_memory, playbook=playbook,
                      shuffle_memory=bool(spec.get("shuffle_memory")) and start_memory is not None,
                      mode=spec["mode"], resume=bool(spec.get("resume")), splits=splits)
    print(json.dumps({k: summary.get(k) for k in ("status", "generations_done", "cost_usd")}))
    return 0


# ------------------------------------------------------------------ CLI


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run a matrix of lab runs (arms x seeds).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("config")
    r.add_argument("--parallel", type=int, default=2, help="runs at once (default 2)")
    r.add_argument("--only", default=None, help="comma-separated arm names")
    r.add_argument("--dry-run", action="store_true", help="print the plan and an estimate; run nothing")
    r.add_argument("--root", default=str(DEFAULT_ROOT), help="where matrices are written (default runs/exp)")
    w = sub.add_parser("_worker")
    w.add_argument("spec")
    args = ap.parse_args(argv)
    if args.cmd == "_worker":
        return worker(args.spec)
    cfg = load_config(args.config)
    only = [a.strip() for a in args.only.split(",")] if args.only else None
    if args.dry_run:
        dry_run(cfg, root=args.root, parallel=args.parallel, only=only)
        return 0
    runs = run_matrix(cfg, root=args.root, parallel=args.parallel, only=only, config_path=args.config)
    bad = [r for r in runs if r.action in ("new", "resume")]
    print(f"done: {len(bad)} run(s) launched; see {Path(args.root) / cfg['name'] / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
