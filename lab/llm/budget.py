"""Thread-safe accounting of LLM calls, tokens, cost and time per label."""
from __future__ import annotations

import threading
from collections import defaultdict

from lab.llm.base import LLMResponse


class Budget:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_label: dict[str, dict] = defaultdict(self._empty)

    @staticmethod
    def _empty() -> dict:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                "cost_unknown_calls": 0, "seconds": 0.0}

    def record(self, label: str, response: LLMResponse | None = None, *, calls: int = 1,
               input_tokens: int = 0, output_tokens: int = 0, cost_usd: float | None = 0.0,
               seconds: float = 0.0) -> None:
        if response is not None:
            input_tokens = response.input_tokens or 0
            output_tokens = response.output_tokens or 0
            cost_usd = response.cost_usd
            seconds = response.duration_s
        with self._lock:
            row = self._by_label[label]
            row["calls"] += calls
            row["input_tokens"] += int(input_tokens)
            row["output_tokens"] += int(output_tokens)
            if cost_usd is None:
                row["cost_unknown_calls"] += calls
            else:
                row["cost_usd"] += float(cost_usd)
            row["seconds"] += float(seconds)

    def snapshot(self) -> dict:
        """{"by_label": {label: totals}, "total": totals} - a copy, safe to keep."""
        with self._lock:
            by_label = {k: dict(v) for k, v in self._by_label.items()}
        total = self._empty()
        for row in by_label.values():
            for k in total:
                total[k] += row[k]
        return {"by_label": by_label, "total": total}
