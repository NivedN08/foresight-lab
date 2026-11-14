"""Problems the lab knows by name."""
from __future__ import annotations


def get_problem(name: str, **kw):
    if name == "coast_water":
        from lab.tasks.coast.problem import CoastWaterProblem
        return CoastWaterProblem(**kw)
    if name == "series":   # a CSV/Parquet table; config file named by $LAB_SERIES_CONFIG (docs/series.md)
        from lab.tasks.series.problem import from_env
        return from_env(**kw)
    if name == "turb":
        from lab.tasks.turb.problem import TurbProblem
        return TurbProblem(**kw)
    raise SystemExit(f"unknown problem {name!r}; known: coast_water, turb, series")


def make_pool(problem, splits, evaluator: str = "local", workers: int = 4, max_containers: int = 50):
    """An evaluation pool: "local" (sandbox child processes on this machine) or
    "modal" (one Modal function call per candidate; see docs/modal.md)."""
    if evaluator == "local":
        from lab.evalpool.pool import EvalPool
        return EvalPool(problem, splits, max_workers=workers)
    if evaluator == "modal":
        from lab.evalpool.modal_backend import ModalEvalPool
        return ModalEvalPool(problem, splits, max_containers=max_containers)
    raise SystemExit(f"unknown evaluator {evaluator!r}; known: local, modal")
