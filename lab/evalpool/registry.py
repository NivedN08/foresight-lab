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
    raise SystemExit(f"unknown problem {name!r}; known: coast_water, turb")
