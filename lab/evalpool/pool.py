"""Run many candidate evaluations in parallel.

Each evaluation is a sandbox child process, so threads are enough: a thread
only waits on its child. Results are cached by source hash (and windows), so
submitting the same source twice runs it once. Failures caused by the machine
(timeouts, memory) are not cached and run again when resubmitted.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Sequence

from lab.core.problem import Splits

from .evaluate import EVAL_WINDOWS, EvalResult, check_windows, evaluate, prepared_cases, source_hash


class EvalPool:
    def __init__(self, problem, splits: Splits, max_workers: int = 4, *, time_limit_s: float = 180,
                 log_path=None):
        self.problem = problem
        self.splits = splits
        self.time_limit_s = time_limit_s
        self.log_path = log_path
        self._ex = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="evalpool")
        self._cache: dict[tuple[str, tuple[str, ...]], Future] = {}
        self._lock = threading.Lock()
        self.runs = 0          # evaluations actually started (cache misses)

    def submit(self, source: str, windows: Sequence[str] = EVAL_WINDOWS) -> "Future[EvalResult]":
        windows = check_windows(windows, allow_test=False)
        key = (source_hash(source), windows)
        with self._lock:
            fut = self._cache.get(key)
            if fut is not None:
                return fut
            self.runs += 1
            fut = self._ex.submit(self._run, source, windows)
            self._cache[key] = fut
        fut.add_done_callback(lambda f, key=key: self._forget_if_transient(key, f))
        return fut

    def map(self, sources: Sequence[str], windows: Sequence[str] = EVAL_WINDOWS) -> list[EvalResult]:
        futs = [self.submit(s, windows) for s in sources]
        return [f.result() for f in futs]

    def _run(self, source: str, windows: tuple[str, ...]) -> EvalResult:
        prepared_cases(self.splits, windows)   # encode once, shared by every thread
        return evaluate(self.problem, source, self.splits, windows, time_limit_s=self.time_limit_s,
                        log_path=self.log_path)

    def _forget_if_transient(self, key, fut: Future) -> None:
        try:
            r = fut.result()
            transient = (not r.ok) and (r.error or "").startswith(("timeout", "memory", "crash: child killed"))
        except Exception:  # noqa: BLE001 - a crash in the evaluator itself: allow a retry
            transient = True
        if transient:
            with self._lock:
                if self._cache.get(key) is fut:
                    del self._cache[key]

    def close(self) -> None:
        self._ex.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
