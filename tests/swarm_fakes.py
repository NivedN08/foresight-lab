"""In-memory stand-ins for the research loop's tests: a toy problem, an
evaluator that honours the lab.evalpool contract by running candidates
in-process, a scripted LLM, and a canned search backend. No network, no real
LLM, no sandbox child processes."""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from lab.core.problem import Splits, Window
from lab.core.stats import paired_block_bootstrap
from lab.core.task import Case
from lab.llm import FakeBackend, LLMError, LLMResponse
from lab.sandbox.lint import lint
from lab.search import Hit

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ToyProblem:
    """truth y = 2x + 1 + noise; leads of 1, 2, 3 hours; windows ordered in time."""

    name = "toy_line"
    title = "Predict y from x on a noisy straight line."
    target_key = "y"
    loss_name = "absolute error, units of y"

    def __init__(self, n=(240, 180, 180, 90), noise=0.5, seed=1):
        rng = np.random.default_rng(seed)
        windows, t = [], T0
        for size in n:
            cases, truths, blocks = [], [], []
            for i in range(size):
                lead = 1 + i % 3
                x = float(rng.uniform(0, 10))
                c = Case(f"c{len(windows)}_{i}", t, t + timedelta(hours=lead), {"x": x})
                cases.append(c)
                truths.append({"y": 2 * x + 1 + float(rng.normal(0, noise))})
                blocks.append(t.strftime("%Y-%m-%d"))
                t += timedelta(hours=1)
            windows.append(Window(cases, truths, blocks))
            t += timedelta(hours=6)
        self._splits = Splits(*windows)
        self._splits.check_order()

    def describe(self):
        return ("# Toy line\nEach case has inputs `x` (float, 0 to 10). Predict `y` (float). "
                "Loss: absolute error. Baseline: the training mean of y.")

    def splits(self):
        return self._splits

    def losses(self, predictions, truths):
        return np.array([abs(p["y"] - t["y"]) for p, t in zip(predictions, truths)], dtype=float)

    def summarize(self, cases, predictions, truths):
        loss = self.losses(predictions, truths)
        by_lead = {}
        for lead in sorted({c.lead_hours for c in cases}):
            m = np.array([c.lead_hours == lead for c in cases])
            by_lead[f"{lead:g}h"] = {"mean_loss": float(loss[m].mean()), "n": int(m.sum())}
        return {"mean_loss": float(loss.mean()), "n": len(loss), "by_lead": by_lead}

    def seed_source(self):
        return SEED

    def baseline_sources(self):
        return {"train_mean": SEED}


SEED = '''
class Forecaster:
    name = "train_mean"

    def fit(self, cases, truths):
        ys = [t["y"] for t in truths]
        self.m = sum(ys) / len(ys)

    def predict(self, case):
        return {"y": float(self.m)}
'''

BROKEN = '''
class Forecaster:
    name = "broken"

    def fit(self, cases, truths):
        self.m = 0.0

    def predict(self, case):
        return {"y": float(case.inputs["no_such_input"])}
'''

WORSE = '''
class Forecaster:
    name = "always_zero"

    def fit(self, cases, truths):
        pass

    def predict(self, case):
        return {"y": 0.0}
'''

BETTER = '''
import numpy as np


class Forecaster:
    name = "least_squares_line"

    def fit(self, cases, truths):
        x = np.array([c.inputs["x"] for c in cases])
        y = np.array([t["y"] for t in truths])
        a = np.vstack([x, np.ones_like(x)]).T
        self.coef = np.linalg.lstsq(a, y, rcond=None)[0]

    def predict(self, case):
        return {"y": float(self.coef[0] * case.inputs["x"] + self.coef[1])}
'''

# a second good candidate, slightly worse than BETTER (slope only, intercept from the mean)
BETTER_SLOPE = '''
import numpy as np


class Forecaster:
    name = "slope_only"

    def fit(self, cases, truths):
        x = np.array([c.inputs["x"] for c in cases])
        y = np.array([t["y"] for t in truths])
        self.b = float(np.sum((x - x.mean()) * (y - y.mean())) / np.sum((x - x.mean()) ** 2)) * 0.97
        self.a = float(y.mean() - self.b * x.mean())

    def predict(self, case):
        return {"y": float(self.b * case.inputs["x"] + self.a)}
'''


# a hand-tuned line with constants and several terms, for the random-mutation baseline
TUNABLE = '''
class Forecaster:
    name = "hand_line"

    def fit(self, cases, truths):
        self.slope = 1.7
        self.offset = 0.4

    def predict(self, case):
        x = case.inputs["x"]
        return {"y": float(self.slope * x + self.offset + 0.05 * x - 0.2)}
'''

# the seed again, renamed and with a change comment: identical for the lab's purposes
SEED_RENAMED = '''
# Change: nothing really
class Forecaster:
    name = "train_mean_v2"

    def fit(self, cases, truths):
        ys = [t["y"] for t in truths]
        self.m = sum(ys) / len(ys)

    def predict(self, case):
        return {"y": float(self.m)}
'''

# normalise like lab.llm.extract_code does, so extract_code(fence(X)) == X
SEED, BROKEN, WORSE, BETTER, BETTER_SLOPE, TUNABLE, SEED_RENAMED = (
    s.strip() + "\n" for s in (SEED, BROKEN, WORSE, BETTER, BETTER_SLOPE, TUNABLE, SEED_RENAMED))
BETTER_COMMENTED = "# Change: fit a least-squares line in x\n" + BETTER


def fence(src: str) -> str:
    return f"Here is the file.\n```python\n{src.strip()}\n```\n"


# ----------------------------------------------------------------- evaluator


@dataclass
class EvalResult:
    ok: bool
    error: str | None
    lint: list[str]
    duration_s: float
    protections: list[str]
    source_hash: str
    losses: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)


def evaluate(problem, source, splits, windows=("select", "confirm"), *, time_limit_s=180, log_path=None):
    t0 = time.monotonic()
    h = hashlib.sha256(source.encode()).hexdigest()
    msgs = lint(source)
    if msgs:
        return EvalResult(False, f"lint: {msgs[0]}", msgs, time.monotonic() - t0, ["lint"], h)
    try:
        ns: dict = {}
        exec(compile(source, "<candidate>", "exec"), ns)   # trusted test code only
        model = ns["Forecaster"]()
        model.fit(splits.train.cases, splits.train.truths)
        losses, summary = {}, {}
        for w in windows:
            win = getattr(splits, w)
            preds = [model.predict(c) for c in win.cases]
            losses[w] = problem.losses(preds, win.truths)
            summary[w] = problem.summarize(win.cases, preds, win.truths)
    except Exception as e:
        return EvalResult(False, f"crash: {type(e).__name__}: {e}", [], time.monotonic() - t0, ["lint"], h)
    return EvalResult(True, None, [], time.monotonic() - t0, ["lint", "in_process_fake"], h, losses, summary)


def compare(a, b, splits, window):
    return paired_block_bootstrap(a.losses[window], b.losses[window], getattr(splits, window).blocks)


class FakeEvalPool:
    """EvalPool(problem, splits): submit(source) -> Future[EvalResult]; map(sources)."""

    def __init__(self, problem, splits=None, max_workers=4):
        self.problem = problem
        self.splits = splits or problem.splits()
        self.submitted: list[str] = []
        self._lock = threading.Lock()

    def submit(self, source):
        with self._lock:
            self.submitted.append(source)
        fut: Future = Future()
        fut.set_result(evaluate(self.problem, source, self.splits))
        return fut

    def map(self, sources):
        return [self.submit(s).result() for s in sources]


# ----------------------------------------------------------------- LLM

RAISE = object()   # put in a script to make that call fail

# substrings that identify each role's prompt (see lab/swarm/prompts.py)
ROLE_KEYS = {
    "researcher": "# A place you might look (a suggestion, not an order)",
    "anticipate": "Without searching, list the five to eight",
    "scout_queries": "literature search queries (4 to 10 words each)",
    "scout_board": "Extract at most",
    "board_judge": "Changes a strong model said it would try next",
    "single_agent": "# Your task (single agent)",
    "playbook": "Write a short playbook",
    "repair": "This candidate failed.",
    "engineer": "# The claim to test",
    "judge": "Techniques named earlier from memory",
    "scout": "# Search results",
    "closed_book": "Without searching",
    "analyst": "Your direction for this proposal",
}

ANALYST_JSON = ('{"diagnosis": "The incumbent ignores x.", "hypothesis": "Fit y as a line in x.", '
                '"knowledge_gap": "linear regression least squares", "strategy": "use x"}')
CLOSED_BOOK_JSON = ('{"techniques": [{"technique": "ordinary least squares", "rule": "fit y = a x + b by '
                    'minimising squared error", "expected_effect": "large drop in error"}]}')
SCOUT_JSON = ('{"relevance": [{"n": 1, "relevance": "domain"}], "chosen": 1, "source_relevance": "domain", '
              '"technique": "least squares line", "change": "predict y as a fitted line in x", "inputs": "x", '
              '"rule": "regress y on x", "leads": "all leads equally", "expected_effect": "lower error", '
              '"source_title": "Method of least squares", "source_url": "https://example.org/ls"}')


def scout_json(relevance: str, chosen=1) -> str:
    import json
    d = json.loads(SCOUT_JSON)
    d.update(source_relevance=relevance, chosen=chosen)
    return json.dumps(d)
JUDGE_JSON = '{"known_closed_book": true, "why": "same idea"}'
ANTICIPATE_JSON = '{"changes": ["fit y as a straight line in x", "shrink toward the mean"]}'
QUERIES_JSON = '{"queries": ["least squares line fit", "linear regression noise"]}'
BOARD_JSON = ('{"ideas": [{"hit": 1, "change": "fit y as a least-squares line in x", "why": "y is linear in x", '
              '"leads": "all", "source_relevance": "domain", "source_title": "Method of least squares", '
              '"source_url": "https://example.org/ls"}, '
              '{"hit": 1, "change": "predict the sign of x", "why": "no reason", "leads": "none", '
              '"source_relevance": "unrelated", "source_title": "Unrelated", "source_url": "https://example.org/u"}, '
              '{"hit": 1, "change": "robust Huber fit of y on x", "why": "outliers", "leads": "all", '
              '"source_relevance": "method", "source_title": "Robust statistics", "source_url": "https://example.org/r"}]}')
BOARD_JUDGE_JSON = '{"known": {"g1i1": true, "g1i2": false}}'


def researched(src: str, source: str = "own", change: str = "fit a least-squares line in x",
               why: str = "y is linear in x at all leads") -> str:
    """A researcher reply: the header lines, then the file."""
    return fence(f"# Change: {change}\n# Idea source: {source}\n# Why: {why}\n{src}")


class ScriptedLLM(FakeBackend):
    """FakeBackend whose replies come from per-role queues, in order (the last
    reply repeats when a queue runs out). `RAISE` in a queue makes that call
    raise LLMError. Thread-safe. Each call costs `cost_per_call` dollars."""

    name = "fake"

    def __init__(self, scripts: dict[str, list] | None = None, cost_per_call: float = 0.0):
        super().__init__()
        defaults = {"analyst": [ANALYST_JSON], "closed_book": [CLOSED_BOOK_JSON], "scout": [SCOUT_JSON],
                    "judge": [JUDGE_JSON], "engineer": [fence(BETTER)], "repair": [fence(BETTER)],
                    "single_agent": [fence(BETTER)], "researcher": [researched(BETTER)],
                    "anticipate": [ANTICIPATE_JSON], "scout_queries": [QUERIES_JSON], "scout_board": [BOARD_JSON],
                    "board_judge": [BOARD_JUDGE_JSON],
                    "playbook": ["# Playbook\n- Fitting a line in x paid off."]}
        self.queues = {k: list(v) for k, v in {**defaults, **(scripts or {})}.items()}
        self.cost_per_call = cost_per_call
        self.roles: list[str] = []
        self._lock = threading.Lock()

    def role_of(self, prompt: str) -> str:
        return next((r for r, key in ROLE_KEYS.items() if key in prompt), "unknown")

    def complete(self, prompt, *, system=None, model=None, max_tokens=4000, temperature=None):
        role = self.role_of(prompt)
        with self._lock:
            self.calls.append({"prompt": prompt, "system": system, "model": model, "role": role})
            self.roles.append(role)
            q = self.queues.get(role) or ["ok"]
            reply = q.pop(0) if len(q) > 1 else q[0]
        if reply is RAISE:
            raise LLMError(f"scripted failure for role {role}")
        return LLMResponse(text=reply, model=model or "fake-model", input_tokens=len(prompt.split()),
                           output_tokens=len(reply.split()), cost_usd=self.cost_per_call, duration_s=0.0,
                           backend=self.name)


class CannedSearch:
    """A search backend object with the Searcher interface and fixed hits."""

    name = "canned"

    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [
            Hit("Method of least squares", "https://example.org/ls", 1805, "Fit a line by least squares.")]
        self.searches = 0
        self.queries: list[str] = []

    def search(self, query, *, k=5, before=None):
        self.searches += 1
        self.queries.append(query)
        return list(self.hits[:k])

    def stats(self):
        return {"backend": self.name, "searches": self.searches, "cache_hits": 0}


def researched_source(src: str, **kw) -> str:
    """The candidate file the loop stores for a `researched` reply."""
    from lab.llm import extract_code
    return extract_code(researched(src, **kw))
