"""Evaluate candidates on a Problem's windows, in parallel. See docs/evaluator.md.

`lab.evalpool.evaluate` is the module; its function is
`lab.evalpool.evaluate.evaluate`."""
from lab.evalpool.evaluate import EvalResult, HeldOutWindowRefused, compare
from lab.evalpool.pool import EvalPool

__all__ = ["EvalPool", "EvalResult", "HeldOutWindowRefused", "compare"]
