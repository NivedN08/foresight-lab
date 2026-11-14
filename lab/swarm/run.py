"""Command line for the research lab.

    .venv/bin/python -m lab.swarm.run --problem coast_water --generations 3 --proposals 4 \
        --memory vector --search openalex [--llm claude_cli] [--warm-from <run_dir>]
    .venv/bin/python -m lab.swarm.run --resume <run_dir> [--generations 5]
    .venv/bin/python -m lab.swarm.run --mode pipeline ...            (the first design: analyst, scout, engineer)
    .venv/bin/python -m lab.swarm.run --mode single_agent ...        (baseline: one model call per proposal)
    .venv/bin/python -m lab.swarm.run --mode random_mutation ...     (baseline: no model)
    .venv/bin/python -m lab.swarm.run --warm-from <run_dir> --shuffle-memory ...   (control arm)

The default mode is researcher: one strong call per proposal, which may use the
notebook (--memory) and an ideas board scouted from search (--search) or ignore them.
A warm run imports the earlier run's findings into its notebook and carries its
playbook.md into the prompts; it still starts from the problem's seed candidate
unless --start-source is given.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

PROBLEMS = {
    "coast_water": "lab.tasks.coast.problem:CoastWaterProblem",
    "turb": "lab.tasks.turb.problem:TurbProblem",
    # a CSV table; the config file is named by $LAB_SERIES_CONFIG (docs/series.md)
    "series": "lab.tasks.series.problem:from_env",
}


def load_problem(name: str):
    try:
        target = PROBLEMS[name]
    except KeyError:
        raise SystemExit(f"unknown problem {name!r}; known: {sorted(PROBLEMS)}") from None
    module, cls = target.split(":")
    return getattr(importlib.import_module(module), cls)()


def build_evaluator(problem, workers: int, evaluator: str = "local"):
    from lab.evalpool.evaluate import compare
    from lab.evalpool.registry import make_pool
    splits = problem.splits()
    return make_pool(problem, splits, evaluator, workers=workers), compare, splits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--problem", default="coast_water")
    ap.add_argument("--generations", type=int, default=None, help="default 3 (on --resume: as configured)")
    ap.add_argument("--proposals", type=int, default=4)
    ap.add_argument("--memory", default="vector", choices=["none", "full_context", "vector", "pastdev"])
    ap.add_argument("--search", default="openalex", choices=["none", "openalex", "arxiv", "crossref", "exa"])
    ap.add_argument("--search-fallback", default=None,
                    help="comma-separated backends to try when the first fails or finds nothing "
                         "(default for openalex: crossref, then arxiv); 'none' for no fallback")
    ap.add_argument("--mode", default="researcher",
                    choices=["researcher", "pipeline", "single_agent", "random_mutation", "swarm"],
                    help="researcher (default): one strong call per proposal with optional notebook and ideas "
                         "board; pipeline: analyst, scout, engineer (the first design; 'swarm' is its old name); "
                         "single_agent: one model call per proposal; random_mutation: no model")
    ap.add_argument("--scout-queries", type=int, default=3, help="researcher: search queries per generation")
    ap.add_argument("--board-size", type=int, default=None, help="researcher: ideas on the board (default: proposals)")
    ap.add_argument("--shuffle-memory", action="store_true",
                    help="control arm: permute the outcomes of the findings imported by --warm-from")
    ap.add_argument("--llm", default="claude_cli")
    ap.add_argument("--analyst-model", default=None)
    ap.add_argument("--engineer-model", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-cost", type=float, default=None, help="stop cleanly once this many USD are spent")
    ap.add_argument("--workers", type=int, default=4, help="evaluation worker processes")
    ap.add_argument("--evaluator", default="local", choices=["local", "modal"],
                    help="where candidates run: this machine, or Modal (docs/modal.md)")
    ap.add_argument("--warm-from", default=None, help="earlier run directory: its findings and playbook")
    ap.add_argument("--start-source", default=None, help="file with the starting candidate")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--resume", default=None, metavar="RUN_DIR", help="continue an interrupted run")
    args = ap.parse_args(argv)

    from lab.swarm.loop import run_lab

    if args.resume:
        cfg = json.loads((Path(args.resume) / "config.json").read_text())
        problem = load_problem(cfg["problem"])
        pool, compare, splits = build_evaluator(problem, args.workers, args.evaluator)
        summary = run_lab(problem, pool, compare, llm_backend=cfg.get("llm_backend") or args.llm,
                          mode=cfg.get("mode", "pipeline"),
                          scout_queries=cfg.get("scout_queries", 3), board_size=cfg.get("board_size"),
                          search_fallback=cfg["search_chain"][1:] if "search_chain" in cfg else None,
                          memory=cfg.get("memory", args.memory), search=cfg.get("search", args.search),
                          generations=args.generations or cfg["generations"],
                          proposals=cfg["proposals"], seed=cfg["seed"], models=cfg.get("models"),
                          max_cost_usd=args.max_cost if args.max_cost is not None else cfg.get("max_cost_usd"),
                          run_dir=args.resume, resume=True, splits=splits)
        print(json.dumps({k: summary[k] for k in ("status", "generations_done", "incumbent", "cost_usd")}))
        return 0

    problem = load_problem(args.problem)
    pool, compare, splits = build_evaluator(problem, args.workers, args.evaluator)
    from lab.swarm.loop import SMALL_ROLES, STRONG_ROLES
    models = {}
    if args.analyst_model:
        models.update({r: args.analyst_model for r in SMALL_ROLES})
    if args.engineer_model:
        models.update({r: args.engineer_model for r in STRONG_ROLES})
    start_memory = playbook = None
    if args.warm_from:
        warm = Path(args.warm_from)
        start_memory = warm
        if (warm / "playbook.md").exists():
            playbook = (warm / "playbook.md").read_text()
    if args.shuffle_memory and not args.warm_from:
        raise SystemExit("--shuffle-memory needs --warm-from (it shuffles the imported findings)")
    fallback = None
    if args.search_fallback is not None:
        fallback = () if args.search_fallback.strip() in ("", "none") else \
            [b.strip() for b in args.search_fallback.split(",") if b.strip()]
    start_source = Path(args.start_source).read_text() if args.start_source else None
    summary = run_lab(problem, pool, compare, llm_backend=args.llm, memory=args.memory, search=args.search,
                      generations=args.generations or 3, proposals=args.proposals, seed=args.seed,
                      run_dir=args.run_dir, models=models or None, max_cost_usd=args.max_cost,
                      start_source=start_source, start_memory=start_memory, playbook=playbook, splits=splits,
                      mode=args.mode, shuffle_memory=args.shuffle_memory, search_fallback=fallback,
                      scout_queries=args.scout_queries, board_size=args.board_size)
    print(json.dumps({k: summary[k] for k in ("run_id", "status", "generations_done", "incumbent", "cost_usd")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
