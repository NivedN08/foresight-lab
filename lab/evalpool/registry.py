"""Problems the lab knows by name."""
from __future__ import annotations


def get_problem(name: str, **kw):
    if name == "coast_water":
        from lab.tasks.coast.problem import CoastWaterProblem
        return CoastWaterProblem(**kw)
    raise SystemExit(f"unknown problem {name!r}; known: coast_water")
