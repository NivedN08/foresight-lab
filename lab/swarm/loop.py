"""The research loop: generations of concurrent proposals against one incumbent.

See docs/swarm.md. Modes (all share everything from "evaluate" on):
  researcher (default): one strong call per proposal chooses its own change; it
      may use or ignore the notebook (memory) and an ideas board scouted from
      search once per generation, and names its idea's source in a header.
  pipeline: failure digest -> analyst -> closed-book -> scout (search) ->
      engineer (+ one repair). The first design; kept as a documented negative
      result. "swarm" is accepted as its old name.
  single_agent: one model call writes the candidate. random_mutation: no model.

At the end of a generation the best accepted proposal becomes the incumbent,
then every proposal's finding is written once, saying whether it became the
incumbent and whether it duplicates another proposal of the same generation.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from lab.core.timeutil import to_iso, utcnow
from lab.llm import Backend, Budget, complete, extract_code, get_backend
from lab.memory import Finding, get_memory, load_findings, read_jsonl, shuffle_outcomes
from lab.sandbox.lint import lint
from lab.search import get_search
from lab.swarm import prompts
from lab.swarm.decide import Thresholds, decide, relative_improvement
from lab.swarm.digest import failure_digest, worst_groups
from lab.swarm.mutate import mutate

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS = {"analyst": "haiku", "closed_book": "haiku", "scout": "haiku", "judge": "haiku",
                  "engineer": "sonnet", "single_agent": "sonnet", "playbook": "haiku",
                  "researcher": "sonnet", "anticipate": "sonnet", "scout_queries": "haiku",
                  "scout_board": "haiku", "board_judge": "haiku"}
MODES = ("researcher", "pipeline", "single_agent", "random_mutation")
MODE_ALIASES = {"swarm": "pipeline"}          # the pipeline's name before the researcher mode existed
STRONG_ROLES = ("engineer", "single_agent", "researcher", "anticipate")
SMALL_ROLES = ("analyst", "closed_book", "scout", "judge", "playbook", "scout_queries", "scout_board", "board_judge")
RELEVANCE = ("domain", "method", "unrelated")
MAX_LOGGED_REPLY = 20_000
IDENTICAL_REASON = "rejected: candidate is identical to the incumbent (ignoring whitespace, comments and `name`)"


class CostCapReached(RuntimeError):
    pass


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def _jsonable(o: Any):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        v = float(o)
        return None if math.isnan(v) else v
    if isinstance(o, np.ndarray):
        return o.tolist()
    if is_dataclass(o):
        return _scrub(asdict(o))
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def _dumps(obj) -> str:
    return json.dumps(_scrub(obj), default=_jsonable)


def _scrub(obj):
    """NaN/inf -> None so every line is strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _scrub(obj.tolist())
    if isinstance(obj, np.floating):
        return _scrub(float(obj))
    return obj


def parse_json(text: str) -> dict | None:
    """The first JSON object in a reply (bare or inside a ``` fence)."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fence.group(1)] if fence else []
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    for c in candidates:
        try:
            v = json.loads(c)
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    return None


def normalized(source: str) -> str:
    """What "the same candidate" means: the syntax tree without comments, formatting
    or the Forecaster's `name` attribute (falls back to collapsed whitespace if the
    source does not parse)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return " ".join(source.split())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Forecaster":
            node.body = [n for n in node.body
                         if not (isinstance(n, (ast.Assign, ast.AnnAssign))
                                 and any(isinstance(t, ast.Name) and t.id == "name"
                                         for t in (n.targets if isinstance(n, ast.Assign) else [n.target])))]
    return ast.dump(tree)


def change_comment(source: str | None) -> str | None:
    """The text of a leading '# Change: ...' comment, if the file starts with one."""
    if not source:
        return None
    for line in source.splitlines():
        if line.strip():
            m = re.match(r"\s*#\s*change\s*:\s*(.+)", line, re.I)
            return m.group(1).strip() if m else None
    return None


def parse_header(source: str | None) -> dict:
    """The researcher's three header lines: '# Change:', '# Idea source:', '# Why:' (any order,
    among the leading comment lines). Missing lines are None."""
    out = {"change": None, "source": None, "why": None}
    if not source:
        return out
    for line in source.splitlines():
        t = line.strip()
        if not t:
            continue
        if not t.startswith("#"):
            break
        m = re.match(r"#\s*(change|idea\s*source|why)\s*:\s*(.*)", t, re.I)
        if m:
            key = "source" if m.group(1).lower().startswith("idea") else m.group(1).lower()
            out[key] = m.group(2).strip() or None
    return out


def parse_idea_source(text: str | None, notebook_ids, board_ids) -> tuple[str, str | None]:
    """('own' | 'notebook' | 'search' | 'unstated', the id named). A missing line, an unknown
    kind or an id that was not shown in this prompt gives 'unstated'."""
    if not text:
        return "unstated", None
    m = re.match(r"\s*(own|notebook|search)\s*(?::\s*\[?([A-Za-z0-9_.-]+)\]?)?", text, re.I)
    if not m:
        return "unstated", None
    kind, ref = m.group(1).lower(), m.group(2)
    if kind == "own":
        return "own", None
    if kind == "notebook" and ref in set(notebook_ids):
        return "notebook", ref
    if kind == "search" and ref in set(board_ids):
        return "search", ref
    return "unstated", ref


def claim_text(claim: dict) -> str:
    def g(k):
        return str(claim.get(k) or "").strip().rstrip(".")
    text = g("technique") or "unnamed change"
    if g("change"):
        text += f": {g('change')}."
    elif g("rule"):
        text += f": {g('rule')}."
    else:
        text += "."
    for label, key in (("Inputs", "inputs"), ("Rule", "rule"), ("Leads", "leads"), ("Expected", "expected_effect")):
        if g(key) and not (key == "rule" and not g("change")):
            text += f" {label}: {g(key)}."
    return text


def mean_loss(result, window: str) -> float:
    s = (getattr(result, "summary", None) or {}).get(window) or {}
    if "mean_loss" in s and s["mean_loss"] is not None:
        return float(s["mean_loss"])
    arr = (getattr(result, "losses", None) or {}).get(window)
    if arr is None:
        return float("nan")
    arr = np.asarray(arr, dtype=float)
    return float(np.nanmean(arr)) if np.any(~np.isnan(arr)) else float("nan")


def _paired(p) -> dict | None:
    if p is None:
        return None
    return {"mean_a": p.mean_a, "mean_b": p.mean_b, "diff_mean": p.diff.mean, "diff_low": p.diff.low,
            "diff_high": p.diff.high, "p_a_better": p.p_a_better, "n": p.n}


def _same_losses(a, b) -> bool:
    if a is None or b is None or not a.ok or not b.ok:
        return False
    for w in ("select", "confirm"):
        x, y = (a.losses or {}).get(w), (b.losses or {}).get(w)
        if x is None or y is None or not np.array_equal(np.asarray(x), np.asarray(y), equal_nan=True):
            return False
    return True


class Lab:
    def __init__(self, problem, evaluator, compare: Callable, *, llm_backend, memory, search,
                 generations: int, proposals: int, seed: int, run_dir, models, max_cost_usd,
                 start_source, start_memory, playbook, resume: bool, splits, thresholds: Thresholds,
                 search_k: int, recall_k: int, runs_root, mode: str = "researcher", shuffle_memory: bool = False,
                 search_fallback=None, scout_queries: int = 3, board_size: int | None = None):
        mode = MODE_ALIASES.get(mode, mode)
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; choose from {MODES}")
        self.problem = problem
        self.evaluator = evaluator
        self.compare = compare
        self.mode = mode
        self.shuffle_memory = bool(shuffle_memory)
        self.scout_queries = int(scout_queries)
        self.board_size = int(board_size) if board_size else int(proposals)
        self.generations = int(generations)
        self.proposals = int(proposals)
        self.seed = int(seed)
        self.models = {**DEFAULT_MODELS, **(models or {})}
        self.max_cost_usd = max_cost_usd
        self.th = thresholds
        self.search_k = search_k
        self.recall_k = recall_k
        self.playbook_in = playbook
        if mode == "random_mutation":
            self.backend = None
        else:
            self.backend = llm_backend if isinstance(llm_backend, Backend) else get_backend(llm_backend)
        self.budget = Budget()
        self._lock = threading.Lock()
        self._splits = splits
        self.start_memory = start_memory

        if resume:
            if run_dir is None or not (Path(run_dir) / "config.json").exists():
                raise ValueError("resume=True needs the run_dir of an existing run (with config.json)")
            self.run_dir = Path(run_dir)
            self.run_id = json.loads((self.run_dir / "config.json").read_text())["run_id"]
        else:
            self.run_id = f"{utcnow().strftime('%Y%m%dT%H%M%SZ')}-s{seed}-{random.Random().randrange(16**4):04x}"
            root = Path(runs_root) if runs_root else PROJECT_ROOT / "runs" / "lab"
            self.run_dir = Path(run_dir) if run_dir else root / problem.name / self.run_id
            self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "candidates").mkdir(exist_ok=True)
        self.resume = resume

        self.memory = memory if not isinstance(memory, str) else get_memory(memory, path=self.run_dir / "memory.jsonl")
        self.memory_name = getattr(self.memory, "name", type(self.memory).__name__)
        self.memory_on = self.memory_name != "none"
        if mode in ("single_agent", "random_mutation"):
            search = "none"           # the baselines never search
        self.searcher = search if not isinstance(search, str) else get_search(search, fallback=search_fallback)
        self.search_name = getattr(self.searcher, "name", type(self.searcher).__name__)
        self.search_chain = [getattr(b, "name", "?") for b in getattr(self.searcher, "backends", [])] \
            or [self.search_name]
        self.search_on = self.search_name != "none"

        self.strategies = list(prompts.STRATEGIES)
        random.Random(seed).shuffle(self.strategies)

        self.prev = json.loads((self.run_dir / "summary.json").read_text()) \
            if resume and (self.run_dir / "summary.json").exists() else {}
        self.cost_offset = float(self.prev.get("cost_usd") or 0.0)
        self.evals_offset = int(self.prev.get("evaluations") or 0)
        self.wall_offset = float(self.prev.get("wall_s") or 0.0)
        self.evaluations = 0
        self.recall_stats = {"calls": 0, "latency_s": 0.0, "chars": 0}
        self.board_offered = int((self.prev.get("board") or {}).get("offered") or 0)
        self.t0 = time.monotonic()
        self.started_at = self.prev.get("started_at") or to_iso(utcnow())
        self.status = "running"
        self.start_source = start_source

    # ------------------------------------------------------------------ logging

    def event(self, event_name: str, /, **fields) -> None:
        row = {"time": to_iso(utcnow()), "event": event_name, **fields}
        line = _dumps(row)
        with self._lock:
            with (self.run_dir / "events.jsonl").open("a") as fh:
                fh.write(line + "\n")

    def _write(self, name: str, text: str) -> None:
        tmp = self.run_dir / (name + ".tmp")
        tmp.write_text(text)
        tmp.replace(self.run_dir / name)

    def cost(self) -> float:
        return self.cost_offset + self.budget.snapshot()["total"]["cost_usd"]

    def over_cap(self) -> bool:
        return self.max_cost_usd is not None and self.cost() >= self.max_cost_usd

    # ------------------------------------------------------------------ helpers

    @property
    def splits(self):
        if self._splits is None:
            self._splits = getattr(self.evaluator, "splits", None) or self.problem.splits()
        return self._splits

    def llm(self, role: str, system: str, prompt: str, ctx: dict) -> str:
        if self.backend is None:
            raise RuntimeError(f"no LLM in mode {self.mode!r}")
        if self.over_cap():
            raise CostCapReached(f"cost cap ${self.max_cost_usd} reached (spent ${self.cost():.4f})")
        t0 = time.monotonic()
        try:
            r = complete(prompt, system=system, model=self.models.get(role), backend=self.backend,
                         budget=self.budget, label=role,
                         max_tokens=8000 if role in ("engineer", "single_agent", "researcher") else 2000)
        except Exception as e:
            self.event("llm_error", role=role, error=f"{type(e).__name__}: {e}", **ctx,
                       duration_s=time.monotonic() - t0)
            raise
        self.event("llm", role=role, model=r.model, prompt_chars=len(prompt), system_chars=len(system or ""),
                   reply_chars=len(r.text), reply=r.text[:MAX_LOGGED_REPLY], input_tokens=r.input_tokens,
                   output_tokens=r.output_tokens, cost_usd=r.cost_usd, duration_s=r.duration_s, **ctx)
        return r.text

    def evaluate(self, source: str, ctx: dict):
        h = source_hash(source)
        path = self.run_dir / "candidates" / f"{h}.py"
        if not path.exists():
            path.write_text(source)
        t0 = time.monotonic()
        with self._lock:
            self.evaluations += 1
        try:
            res = self.evaluator.submit(source).result()
        except Exception as e:
            self.event("eval_error", hash=h, error=f"{type(e).__name__}: {e}", **ctx)
            raise
        self.event("evaluation", hash=h, ok=res.ok, error=res.error, lint=list(res.lint or []),
                   duration_s=res.duration_s, wall_s=time.monotonic() - t0,
                   protections=list(res.protections or []), summary=res.summary, **ctx)
        return res

    def counts(self) -> dict:
        fs = read_jsonl(self.run_dir / "findings.jsonl")
        by_origin: dict[str, dict] = {}
        for f in fs:
            if f.supersedes:
                continue
            row = by_origin.setdefault(f.origin or "unstated", {"proposals": 0, "passed": 0, "adopted": 0})
            row["proposals"] += 1
            row["passed"] += int(bool(f.accepted))
            row["adopted"] += int(bool(f.became_incumbent))
        used = [f for f in fs if self.mode == "researcher" and f.origin == "search"]
        passed = [f for f in used if f.accepted]
        board = {"offered": self.board_offered, "used": len(used), "used_passed": len(passed),
                 "used_adopted": sum(1 for f in used if f.became_incumbent),
                 "passed_not_known_closed_book": sum(1 for f in passed if f.known_closed_book is False)}
        return {"by_origin": by_origin, "board": board, "findings_total": len(fs),
                "accepted_total": sum(1 for f in fs if f.accepted and not f.supersedes),
                "became_incumbent_total": sum(1 for f in fs if f.became_incumbent),
                "duplicates_total": sum(1 for f in fs if f.duplicate_of),
                "claims_from_search": sum(1 for f in fs if f.origin == "search"),
                "accepted_from_search": sum(1 for f in fs if f.origin == "search" and f.accepted)}

    def write_summary(self, incumbent_hash: str, curve: list, done: int, extra: dict | None = None) -> None:
        last = curve[-1] if curve else {}
        snap = self.budget.snapshot()
        summary = {
            "run_id": self.run_id, "problem": self.problem.name, "mode": self.mode, "status": self.status,
            "loss_name": getattr(self.problem, "loss_name", None),
            "baseline_incumbent": {"select": (curve[0] if curve else {}).get("select"),
                                   "confirm": (curve[0] if curve else {}).get("confirm")},
            "started_at": self.started_at, "updated_at": to_iso(utcnow()),
            "generations_planned": self.generations, "generations_done": done,
            "proposals_per_generation": self.proposals,
            "incumbent_hash": incumbent_hash,
            "incumbent": {"select": last.get("select"), "confirm": last.get("confirm")},
            "curve": curve,
            "cost_usd": self.cost(), "evaluations": self.evals_offset + self.evaluations,
            "wall_s": self.wall_offset + (time.monotonic() - self.t0),
            "llm_by_role": snap["by_label"],
            "search": self.searcher.stats() if hasattr(self.searcher, "stats") else {},
            "recall": dict(self.recall_stats),
            "memory": self.memory_name,
            **self.counts(),
        }
        merged = {k: dict(v) for k, v in (self.prev.get("llm_by_role_all") or {}).items()}
        for role, row in snap["by_label"].items():
            base = merged.setdefault(role, {k: 0 for k in row})
            for k, v in row.items():
                base[k] = base.get(k, 0) + v
        summary["llm_by_role_all"] = merged
        if extra:
            summary.update(extra)
        self._write("summary.json", json.dumps(_scrub(summary), default=_jsonable, indent=1))

    def record_finding(self, finding: Finding, ctx: dict) -> None:
        with self._lock:
            with (self.run_dir / "findings.jsonl").open("a") as fh:
                fh.write(json.dumps(_scrub(finding.to_dict())) + "\n")
        try:
            self.memory.add(finding)
        except Exception as e:
            self.event("memory_error", finding=finding.id, error=f"{type(e).__name__}: {e}", **ctx)

    # ------------------------------------------------------------------ proposals

    def proposal(self, gen, idx, incumbent_src, incumbent_res, digest, worst, board=None) -> dict:
        if self.mode == "single_agent":
            return self._proposal_single(gen, idx, incumbent_src, incumbent_res, digest)
        if self.mode == "random_mutation":
            return self._proposal_mutation(gen, idx, incumbent_src, incumbent_res)
        if self.mode == "researcher":
            return self._proposal_researcher(gen, idx, incumbent_src, incumbent_res, digest, worst, board or [])
        return self._proposal_pipeline(gen, idx, incumbent_src, incumbent_res, digest, worst)

    def _strategy(self, gen: int, idx: int) -> str:
        return self.strategies[((gen - 1) * self.proposals + idx) % len(self.strategies)]

    def _recall(self, query: str, ctx: dict):
        rec = self.memory.recall(query, k=self.recall_k)
        with self._lock:
            self.recall_stats["calls"] += 1
            self.recall_stats["latency_s"] += rec.latency_s
            self.recall_stats["chars"] += rec.chars
        self.event("recall", query=query, n=len(rec.findings), ids=[f.id for f in rec.findings],
                   chars=rec.chars, latency_s=rec.latency_s, **ctx)
        return rec

    # ---------------------------------------------------------------- researcher

    def scout_board(self, gen: int, incumbent_src: str, digest: str) -> list[dict]:
        """Once per generation (search on): the strong model's closed-book list of next changes,
        search queries from a small model, the hits distilled into an ideas board, and a
        judge marking each idea as already on the closed-book list or not."""
        ctx = {"generation": gen, "stage": "board"}
        describe = self.problem.describe()
        sys_, p = prompts.anticipate(describe, incumbent_src, digest)
        anticipated = [str(c) for c in ((parse_json(self.llm("anticipate", sys_, p, ctx)) or {}).get("changes") or [])
                       if str(c).strip()]
        short = f"Target: {self.problem.target_key}. Loss: {self.problem.loss_name}."
        sys_, p = prompts.scout_queries(self.problem.title, short, incumbent_src[:2500], digest, self.scout_queries)
        queries = [str(q).strip() for q in ((parse_json(self.llm("scout_queries", sys_, p, ctx)) or {})
                                            .get("queries") or []) if str(q).strip()][: self.scout_queries]
        hits, seen = [], set()
        for q in queries:
            failures, backend, n = [], None, 0
            try:
                if hasattr(self.searcher, "search_detailed"):
                    o = self.searcher.search_detailed(q, k=self.search_k)
                    found, backend, failures = o.hits, o.backend, o.failures
                else:
                    found = self.searcher.search(q, k=self.search_k)
                    backend = getattr(self.searcher, "name", None) if found else None
            except Exception as e:
                found, failures = [], [{"backend": self.search_name, "error": f"{type(e).__name__}: {e}"}]
            for fl in failures:
                self.event("search_error", query=q, backend=fl.get("backend"), error=fl.get("error"), **ctx)
            for h in found:
                if h.url not in seen:
                    seen.add(h.url)
                    if not getattr(h, "backend", None):
                        h.backend = backend or ""
                    hits.append(h)
                    n += 1
            self.event("search", query=q, n=len(found), new=n, search_backend=backend, failures=failures,
                       hits=[{"title": h.title, "url": h.url, "year": h.year} for h in found], **ctx)
        hits = hits[:12]
        board: list[dict] = []
        if hits:
            sys_, p = prompts.scout_board(self.problem.title, describe, [h.to_dict() for h in hits], self.board_size)
            raw = (parse_json(self.llm("scout_board", sys_, p, ctx)) or {}).get("ideas") or []
            for item in raw:
                if not isinstance(item, dict) or not str(item.get("change") or "").strip():
                    continue
                rel = str(item.get("source_relevance") or "").strip().lower()
                if rel == "unrelated":
                    continue
                hit = None
                try:
                    hit = hits[int(item.get("hit")) - 1]
                except (TypeError, ValueError, IndexError):
                    hit = next((h for h in hits if h.url == item.get("source_url")), None)
                board.append({"id": f"g{gen}i{len(board) + 1}", "change": str(item["change"]).strip(),
                              "why": str(item.get("why") or "").strip(), "leads": str(item.get("leads") or "").strip(),
                              "source_title": str(item.get("source_title") or (hit.title if hit else "")),
                              "source_url": str(item.get("source_url") or (hit.url if hit else "")),
                              "source_relevance": rel if rel in RELEVANCE else None,
                              "search_backend": getattr(hit, "backend", None) if hit else None,
                              "known_closed_book": None})
                if len(board) >= self.board_size:
                    break
        if board:
            sys_, p = prompts.board_judge(board, anticipated)
            known = (parse_json(self.llm("board_judge", sys_, p, ctx)) or {}).get("known") or {}
            for idea in board:
                k = known.get(idea["id"]) if isinstance(known, dict) else None
                idea["known_closed_book"] = k if isinstance(k, bool) else None
        with self._lock:
            self.board_offered += len(board)
        self.event("board", generation=gen, queries=queries, hits=len(hits), anticipated=anticipated, ideas=board)
        return board

    @staticmethod
    def board_text(board: list[dict]) -> str:
        lines = []
        for i in board:
            line = f"- [{i['id']}] {i['change']}"
            if i.get("why"):
                line += f" Why: {i['why']}"
            if i.get("leads"):
                line += f" Leads: {i['leads']}"
            src = " ".join(x for x in (i.get("source_title"), i.get("source_url")) if x)
            if src:
                line += f" Source: {src}" + (f" ({i['source_relevance']})" if i.get("source_relevance") else "")
            lines.append(line)
        return "\n".join(lines)

    def _proposal_researcher(self, gen, idx, incumbent_src, incumbent_res, digest, worst, board) -> dict:
        ctx = {"generation": gen, "proposal": idx}
        strategy = self._strategy(gen, idx)
        out: dict[str, Any] = {"generation": gen, "proposal": idx, "strategy": strategy}
        notebook, notebook_ids = None, []
        if self.memory_on:
            rec = self._recall(f"{strategy} {self.problem.title} " + " ".join(worst), ctx)
            notebook_ids = [f.id for f in rec.findings]
            notebook = "\n".join(f"- [{f.id}] {f.text}" for f in rec.findings)
        board_block = self.board_text(board) if self.search_on else None
        sys_, p = prompts.researcher(self.problem.describe(), incumbent_src, digest, strategy, self.problem.target_key,
                                     notebook=notebook, board=board_block, playbook=self.playbook_in)
        source = extract_code(self.llm("researcher", sys_, p, ctx))
        board_ids = [i["id"] for i in board]

        def repair(src, error):
            sys2, p2 = prompts.repair(src or "(no code)", error, self.problem.target_key, keep_header=True)
            return extract_code(self.llm("researcher", sys2, p2, {**ctx, "repair": True}))

        def finding_fields(final_source):
            h = parse_header(final_source)
            origin, ref = parse_idea_source(h["source"], notebook_ids, board_ids)
            if origin == "unstated":
                self.event("origin_unstated", header=h, named=ref, **ctx)
            claim = (h["change"] or "researcher change (no '# Change:' line)").rstrip(".") + "."
            if h["why"]:
                claim += f" Why: {h['why'].rstrip('.')}."
            fields = dict(claim=claim, origin=origin, idea_ref=ref)
            if origin == "search":
                idea = next(i for i in board if i["id"] == ref)
                fields.update(source_title=idea["source_title"], source_url=idea["source_url"],
                              source_relevance=idea["source_relevance"], search_backend=idea["search_backend"],
                              known_closed_book=idea["known_closed_book"])
            out.update(origin=origin, idea_ref=ref, header=h)
            return fields

        return self._test(out, ctx, source, incumbent_src, incumbent_res, repair=repair, finding=finding_fields)

    # ---------------------------------------------------------------- pipeline

    def _proposal_pipeline(self, gen, idx, incumbent_src, incumbent_res, digest, worst) -> dict:
        ctx = {"generation": gen, "proposal": idx}
        strategy = self._strategy(gen, idx)
        out: dict[str, Any] = {"generation": gen, "proposal": idx, "strategy": strategy}
        describe = self.problem.describe()

        # b. recall + analyst
        rec = self._recall(f"{strategy} {self.problem.title} " + " ".join(worst), ctx)
        sys_, p = prompts.analyst(describe, incumbent_src, digest, rec.text, strategy, self.playbook_in)
        reply = self.llm("analyst", sys_, p, ctx)
        a = parse_json(reply)
        if a is None:
            self.event("parse_fallback", role="analyst", **ctx)
            a = {"diagnosis": reply[:500], "hypothesis": reply[:300],
                 "knowledge_gap": f"{self.problem.title} forecasting improvement", "strategy": strategy}
        hypothesis = str(a.get("hypothesis") or "")
        gap = str(a.get("knowledge_gap") or f"{self.problem.title} forecasting").strip()
        out.update(diagnosis=a.get("diagnosis"), hypothesis=hypothesis, knowledge_gap=gap)

        # c. closed book
        sys_, p = prompts.closed_book(describe, incumbent_src, gap, hypothesis)
        cb = parse_json(self.llm("closed_book", sys_, p, ctx)) or {}
        techniques = [t for t in (cb.get("techniques") or []) if isinstance(t, dict)]
        if not techniques:
            self.event("parse_fallback", role="closed_book", **ctx)

        # d. scout
        claim, origin, known, backend, relevance = None, "closed_book", None, None, None
        if self.search_on:
            hits, failures, from_cache = [], [], False
            try:
                if hasattr(self.searcher, "search_detailed"):
                    o = self.searcher.search_detailed(gap, k=self.search_k)
                    hits, backend, failures, from_cache = o.hits, o.backend, o.failures, o.from_cache
                else:
                    hits = self.searcher.search(gap, k=self.search_k)
                    backend = getattr(self.searcher, "name", None) if hits else None
            except Exception as e:
                failures = [{"backend": self.search_name, "error": f"{type(e).__name__}: {e}"}]
            for fl in failures:
                self.event("search_error", query=gap, backend=fl.get("backend"), error=fl.get("error"), **ctx)
            self.event("search", query=gap, n=len(hits), search_backend=backend, from_cache=from_cache,
                       failures=failures, hits=[{"title": h.title, "url": h.url, "year": h.year} for h in hits],
                       **ctx)
            if hits:
                sys_, p = prompts.scout(describe, incumbent_src, hypothesis, gap, [h.to_dict() for h in hits])
                c = parse_json(self.llm("scout", sys_, p, ctx))
                rel = str((c or {}).get("source_relevance") or "").strip().lower() or None
                if rel is not None and rel not in RELEVANCE:
                    rel = None
                usable = bool(c and c.get("technique") and c.get("chosen") is not None and rel != "unrelated")
                self.event("scout", usable=usable, source_relevance=rel, chosen=(c or {}).get("chosen"),
                           relevance=(c or {}).get("relevance"), **ctx)
                if usable:
                    claim, origin, relevance = c, "search", rel
                    sys_, p = prompts.judge(c, techniques)
                    j = parse_json(self.llm("judge", sys_, p, ctx)) or {}
                    kc = j.get("known_closed_book")
                    known = kc if isinstance(kc, bool) else None
                else:
                    self.event("scout_no_usable_hit", source_relevance=rel, **ctx)
        if claim is None:
            backend = None
            claim = dict(techniques[0]) if techniques else \
                {"technique": "analyst hypothesis", "change": hypothesis}
            claim.setdefault("source_title", "")
            claim.setdefault("source_url", "")
        text = claim_text(claim)
        out.update(claim=claim, origin=origin, known_closed_book=known, search_backend=backend,
                   source_relevance=relevance)
        self.event("claim", claim=claim, claim_text=text, origin=origin, known_closed_book=known,
                   search_backend=backend, source_relevance=relevance, **ctx)

        # e. engineer (+ one repair)
        sys_, p = prompts.engineer(describe, incumbent_src, text, hypothesis, self.problem.target_key,
                                   self.playbook_in)
        source = extract_code(self.llm("engineer", sys_, p, ctx))

        def repair(src, error):
            sys2, p2 = prompts.repair(src or "(no code)", error, self.problem.target_key)
            return extract_code(self.llm("engineer", sys2, p2, {**ctx, "repair": True}))

        return self._test(out, ctx, source, incumbent_src, incumbent_res, repair=repair,
                          finding=dict(claim=text, origin=origin, known_closed_book=known,
                                       source_title=str(claim.get("source_title") or ""),
                                       source_url=str(claim.get("source_url") or ""),
                                       search_backend=backend, source_relevance=relevance))

    def _proposal_single(self, gen, idx, incumbent_src, incumbent_res, digest) -> dict:
        ctx = {"generation": gen, "proposal": idx}
        out: dict[str, Any] = {"generation": gen, "proposal": idx}
        sys_, p = prompts.single_agent(self.problem.describe(), incumbent_src, digest, self.problem.target_key)
        source = extract_code(self.llm("single_agent", sys_, p, ctx))
        text = change_comment(source) or "single-agent change (no description given)"
        return self._test(out, ctx, source, incumbent_src, incumbent_res, repair=None,
                          finding=dict(claim=f"{text.rstrip('.')}.", origin="single_agent"))

    def _proposal_mutation(self, gen, idx, incumbent_src, incumbent_res) -> dict:
        ctx = {"generation": gen, "proposal": idx}
        out: dict[str, Any] = {"generation": gen, "proposal": idx}
        rng = random.Random(self.seed * 1_000_003 + gen * 1_009 + idx)
        m = mutate(incumbent_src, rng)
        if m is None:
            self.event("mutation_failed", reason="no lint-clean mutation found", **ctx)
            source, text = None, "random mutation: none found"
        else:
            self.event("mutation", kind=m.kind, description=m.description, **ctx)
            source, text = m.source, f"Random mutation ({m.kind}): {m.description}"
        return self._test(out, ctx, source, incumbent_src, incumbent_res, repair=None,
                          finding=dict(claim=text, origin="random_mutation"))

    # ------------------------------------------------------------------ shared tail

    def _test(self, out: dict, ctx: dict, source, incumbent_src, incumbent_res, *, repair, finding: dict) -> dict:
        """Lint, identical-to-incumbent check, evaluate (+ one repair if `repair`),
        compare, decide. Returns the outcome with an unrecorded Finding."""
        res, sel, conf, error = None, None, None, None
        repaired, identical = False, False
        while True:
            if source is None:
                error, lint_msgs = "no candidate code (the reply had no Python block that parses)", []
            else:
                lint_msgs = lint(source)
                error = ("lint: " + "; ".join(lint_msgs)) if lint_msgs else None
            if source is not None and not lint_msgs and normalized(source) == normalized(incumbent_src):
                identical = True
                self.event("identical_to_incumbent", hash=source_hash(source), repaired=repaired, **ctx)
                break
            if lint_msgs or source is None:
                self.event("lint_failed", hash=source_hash(source) if source else None, lint=lint_msgs,
                           error=error, repaired=repaired, **ctx)
            else:
                res = self.evaluate(source, {**ctx, "repaired": repaired})
                error = None if res.ok else (res.error or "evaluation failed") + \
                    (("\nlint: " + "; ".join(res.lint)) if res.lint else "")
            if error is None or repaired or repair is None:
                break
            repaired = True
            source = repair(source, error)
        ok = bool(res is not None and res.ok and error is None and not identical)
        if ok:
            sel = self.compare(res, incumbent_res, self.splits, "select")
            conf = self.compare(res, incumbent_res, self.splits, "confirm")
        if identical:
            accepted, reason = False, IDENTICAL_REASON
        else:
            accepted, reason = decide(ok, sel, conf, self.th)
        rel = relative_improvement(sel) if sel is not None else None
        h = source_hash(source) if source else None
        cc = change_comment(source)
        out.update(hash=h, ok=ok, error=error, repaired=repaired, identical=identical, accepted=accepted,
                   reason=reason, select=_paired(sel), confirm=_paired(conf), relative_improvement=rel,
                   source=source, result=res, change_comment=cc)
        self.event("decision", hash=h, accepted=accepted, reason=reason, select=_paired(sel),
                   confirm=_paired(conf), relative_improvement=rel, error=error, identical=identical,
                   change_comment=cc, **ctx)
        if callable(finding):
            finding = finding(source)
        f = Finding(problem=self.problem.name, generation=ctx["generation"], accepted=accepted,
                    select_delta=sel.diff.mean if sel else None, select_low=sel.diff.low if sel else None,
                    select_high=sel.diff.high if sel else None, confirm_delta=conf.diff.mean if conf else None,
                    **{**finding, "claim": finding["claim"][:600]})
        if identical:
            f.text = f.render() + " Not measured: the candidate was identical to the incumbent."
        elif not ok:
            f.text = f.render() + f" Failure: {str(error)[:300]}"
        elif not accepted:
            f.text = f.render() + f" Why rejected: {reason[len('rejected: '):]}"
        out["finding"] = f
        return out

    # ------------------------------------------------------------------ generation

    def resolve_generation(self, gen: int, outcomes: list[dict]) -> dict | None:
        """Pick the new incumbent, mark duplicates, and write each finding once."""
        outcomes = sorted(outcomes, key=lambda o: o["proposal"])
        firsts: list[dict] = []
        for o in outcomes:
            o["duplicate_of"] = None
            if o.get("hash") is None:
                continue
            twin = next((d for d in firsts if d["hash"] == o["hash"] or _same_losses(d.get("result"), o.get("result"))),
                        None)
            if twin is not None:
                o["duplicate_of"] = twin
            else:
                firsts.append(o)
        accepted = [o for o in outcomes if o.get("accepted")]
        best = min(accepted, key=lambda o: (o["select"]["diff_mean"], o["proposal"])) if accepted else None
        for o in outcomes:
            f: Finding = o["finding"]
            f.became_incumbent = (o is best) if o.get("accepted") else False
            twin = o.get("duplicate_of")
            f.duplicate_of = twin["finding"].id if twin else None
            extra = ""
            if o.get("accepted") and o is not best:
                extra += (f" Passed the test but was not adopted: proposal {best['proposal']} won generation {gen};"
                          " not tested on top of it.")
            if twin is not None:
                same = "same code" if twin["hash"] == o["hash"] else "identical predictions"
                extra += f" Duplicate of proposal {twin['proposal']} ({same})."
            base = f.text.split("] ", 1)[1] if f.text.startswith("[") else f.text
            f.text = f"[{f.verdict()}] " + base + extra
            self.record_finding(f, {"generation": gen, "proposal": o["proposal"]})
            if o.get("accepted") and o is not best:
                self.event("not_adopted", generation=gen, proposal=o["proposal"], hash=o["hash"],
                           by_hash=best["hash"], finding=f.id, duplicate_of=f.duplicate_of)
        self.event("generation_resolved", generation=gen,
                   findings=[{"proposal": o["proposal"], "finding": o["finding"].id, "accepted": o.get("accepted"),
                              "became_incumbent": o["finding"].became_incumbent,
                              "duplicate_of": o["finding"].duplicate_of} for o in outcomes])
        return best

    # ------------------------------------------------------------------ the run

    def run(self) -> dict:
        prev_curve = list(self.prev.get("curve") or [])
        done = int(self.prev.get("generations_done") or 0)
        if self.resume:
            incumbent_src = (self.run_dir / "incumbent.py").read_text()
            self.event("run_resume", generations_done=done, generations_planned=self.generations)
        else:
            incumbent_src = self.start_source or self.problem.seed_source()
            config = {"run_id": self.run_id, "problem": self.problem.name, "mode": self.mode,
                      "loss_name": getattr(self.problem, "loss_name", None),
                      "scout_queries": self.scout_queries, "board_size": self.board_size,
                      "generations": self.generations, "proposals": self.proposals, "seed": self.seed,
                      "llm_backend": self.backend.name if self.backend else None,
                      "models": self.models, "memory": self.memory_name, "search": self.search_name,
                      "search_chain": self.search_chain,
                      "max_cost_usd": self.max_cost_usd, "thresholds": asdict(self.th),
                      "search_k": self.search_k, "recall_k": self.recall_k,
                      "start_source_given": self.start_source is not None,
                      "start_memory": str(self.start_memory) if isinstance(self.start_memory, (str, Path))
                      else (f"{len(self.start_memory)} findings" if self.start_memory else None),
                      "shuffle_memory": self.shuffle_memory,
                      "playbook_given": bool(self.playbook_in), "started_at": self.started_at}
            self._write("config.json", json.dumps(config, indent=1, default=_jsonable))
            if self.playbook_in:
                self._write("playbook_in.md", self.playbook_in)
            imported = load_findings(self.start_memory)
            for f in imported:          # rebuild text from fields: older texts carried confirm-window figures
                f.text = f.render()
            if self.shuffle_memory and imported:
                imported = shuffle_outcomes(imported, self.seed)
            for f in imported:
                try:
                    self.memory.add(f)
                except Exception as e:
                    self.event("memory_error", finding=f.id, error=f"{type(e).__name__}: {e}")
            self.event("run_start", run_dir=str(self.run_dir), mode=self.mode, imported_findings=len(imported),
                       shuffled=self.shuffle_memory and bool(imported))
        if self.resume and (self.run_dir / "playbook_in.md").exists() and not self.playbook_in:
            self.playbook_in = (self.run_dir / "playbook_in.md").read_text()

        inc_res = self.evaluate(incumbent_src, {"generation": done, "stage": "incumbent"})
        if not inc_res.ok:
            self.status = "failed"
            self.write_summary(source_hash(incumbent_src), prev_curve, done,
                               {"error": f"incumbent failed to evaluate: {inc_res.error}"})
            raise RuntimeError(f"incumbent failed to evaluate: {inc_res.error}")
        self._write("incumbent.py", incumbent_src)
        inc_hash = source_hash(incumbent_src)
        curve = prev_curve or [{"generation": 0, "select": mean_loss(inc_res, "select"),
                                "confirm": mean_loss(inc_res, "confirm"), "incumbent_hash": inc_hash,
                                "accepted": 0, "proposals": 0, "cost_usd": self.cost(),
                                "evaluations": self.evals_offset + self.evaluations,
                                "wall_s": self.wall_offset + time.monotonic() - self.t0}]
        self.write_summary(inc_hash, curve, done)

        for gen in range(done + 1, self.generations + 1):
            if self.over_cap():
                self.status = "stopped_cost_cap"
                self.event("cost_cap", generation=gen, cost_usd=self.cost(), cap=self.max_cost_usd)
                break
            self.event("generation_start", generation=gen, incumbent_hash=inc_hash)
            digest = failure_digest(inc_res, "select", self.problem.loss_name)
            worst = worst_groups((inc_res.summary or {}).get("select") or {})
            self.event("digest", generation=gen, digest=digest)
            board = []
            if self.mode == "researcher" and self.search_on:
                try:
                    board = self.scout_board(gen, incumbent_src, digest)
                except CostCapReached as e:
                    self.event("board_stopped", generation=gen, reason=str(e))
                except Exception as e:
                    self.event("board_failed", generation=gen, error=f"{type(e).__name__}: {e}")
            outcomes = []
            with ThreadPoolExecutor(max_workers=max(1, self.proposals)) as pool:
                futs = [pool.submit(self.proposal, gen, i, incumbent_src, inc_res, digest, worst, board)
                        for i in range(self.proposals)]
                for i, fut in enumerate(futs):
                    try:
                        outcomes.append(fut.result())
                    except CostCapReached as e:
                        self.event("proposal_stopped", generation=gen, proposal=i, reason=str(e))
                    except Exception as e:
                        self.event("proposal_failed", generation=gen, proposal=i,
                                   error=f"{type(e).__name__}: {e}")
            best = self.resolve_generation(gen, outcomes)
            n_accepted = sum(1 for o in outcomes if o.get("accepted"))
            if best is not None:
                incumbent_src, inc_res, inc_hash = best["source"], best["result"], best["hash"]
                self._write("incumbent.py", incumbent_src)
                self.event("new_incumbent", generation=gen, hash=inc_hash, proposal=best["proposal"],
                           finding=best["finding"].id, select=best["select"], confirm=best["confirm"])
            curve.append({"generation": gen, "select": mean_loss(inc_res, "select"),
                          "confirm": mean_loss(inc_res, "confirm"), "incumbent_hash": inc_hash,
                          "accepted": n_accepted, "proposals": len(outcomes), "cost_usd": self.cost(),
                          "evaluations": self.evals_offset + self.evaluations,
                          "wall_s": self.wall_offset + time.monotonic() - self.t0})
            done = gen
            if self.over_cap():
                self.status = "stopped_cost_cap"
                self.event("cost_cap", generation=gen, cost_usd=self.cost(), cap=self.max_cost_usd)
            self.write_summary(inc_hash, curve, done)
            self.event("generation_end", generation=gen, accepted=n_accepted, incumbent_hash=inc_hash)
            if self.status == "stopped_cost_cap":
                break

        if self.status == "running":
            self.status = "finished"
        self.write_playbook()
        self.write_summary(inc_hash, curve, done)
        self.event("run_end", status=self.status, generations_done=done)
        return json.loads((self.run_dir / "summary.json").read_text())

    def write_playbook(self) -> None:
        """A model-written playbook in swarm mode; the baselines (and a run stopped by
        the cost cap, or a failed call) get a plain list of measured findings."""
        findings = read_jsonl(self.run_dir / "findings.jsonl")
        block = "\n".join(f"- {f.text}" for f in findings)
        text = None
        if self.mode in ("researcher", "pipeline") and self.status != "stopped_cost_cap" and findings:
            try:
                sys_, p = prompts.playbook(self.problem.name, block[:30_000], self.playbook_in)
                text = self.llm("playbook", sys_, p, {"stage": "end_of_run"}).strip() or None
            except Exception as e:
                self.event("playbook_fallback", error=f"{type(e).__name__}: {e}")
                text = None
        if text is None:
            def line(f):
                return f"- {f.claim} (select delta {f.select_delta})"
            adopted = [f for f in findings if f.became_incumbent]
            passed = [f for f in findings if f.accepted and not f.became_incumbent and not f.supersedes]
            measured = [f for f in findings if not f.accepted and f.select_delta is not None]
            failed = [f for f in findings if not f.accepted and f.select_delta is None]
            text = "\n".join(
                [f"# Playbook for {self.problem.name} (written without a model: list of measured findings)", "",
                 "## Became the incumbent"] + [line(f) for f in adopted]
                + ["", "## Passed but not adopted"] + [line(f) for f in passed]
                + ["", "## Measured and rejected"] + [line(f) for f in measured]
                + ["", "## Did not run or not measured (says nothing about the idea)"] + [line(f) for f in failed])
        self._write("playbook.md", text + "\n")


def run_lab(problem, evaluator, compare, *, llm_backend="claude_cli", memory="vector", search="openalex",
            generations=5, proposals=4, seed=0, run_dir=None, models=None, max_cost_usd=None,
            start_source=None, start_memory=None, playbook=None, resume=False, splits=None,
            p_min=0.95, min_rel_improvement=0.003, confirm_max_diff=0.0, search_k=5, recall_k=8,
            runs_root=None, mode="researcher", shuffle_memory=False, search_fallback=None,
            scout_queries=3, board_size=None) -> dict:
    """Run the research lab; returns the final summary dict (also in <run_dir>/summary.json).

    `evaluator` needs `submit(source) -> Future[EvalResult]`; `compare(a, b, splits, window)`
    returns a PairedResult (a = candidate, b = incumbent). `llm_backend`, `memory` and `search`
    take a name or a ready object; `search_fallback` is the chain after the first backend
    (None: lab.search.DEFAULT_FALLBACK). `mode` is "researcher" (default), "pipeline"
    ("swarm" is its old name), "single_agent" or "random_mutation"; `scout_queries` and
    `board_size` (default: proposals) shape the researcher's ideas board. `shuffle_memory` permutes the outcomes of the findings imported
    from `start_memory` (the shuffled-notebook control). `splits` defaults to
    `evaluator.splits`, else `problem.splits()`. `resume=True` continues the run in
    `run_dir` after its last finished generation."""
    lab = Lab(problem, evaluator, compare, llm_backend=llm_backend, memory=memory, search=search,
              generations=generations, proposals=proposals, seed=seed, run_dir=run_dir, models=models,
              max_cost_usd=max_cost_usd, start_source=start_source, start_memory=start_memory,
              playbook=playbook, resume=resume, splits=splits,
              thresholds=Thresholds(p_min, min_rel_improvement, confirm_max_diff),
              search_k=search_k, recall_k=recall_k, runs_root=runs_root, mode=mode,
              shuffle_memory=shuffle_memory, search_fallback=search_fallback,
              scout_queries=scout_queries, board_size=board_size)
    return lab.run()
