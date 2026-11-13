"""The research loop: generations of concurrent proposals against one incumbent.

See docs/swarm.md. One proposal = failure digest -> analyst -> closed-book ->
scout (search) -> engineer (+ one repair) -> evaluate -> compare -> decide (code)
-> finding. At the end of a generation the best accepted proposal becomes the
incumbent. Everything is written under the run directory as it happens.
"""
from __future__ import annotations

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
from lab.llm import Backend, Budget, LLMError, complete, extract_code, get_backend
from lab.memory import Finding, get_memory, load_findings, read_jsonl
from lab.sandbox.lint import lint
from lab.search import get_search
from lab.swarm import prompts
from lab.swarm.decide import Thresholds, decide, relative_improvement
from lab.swarm.digest import failure_digest, worst_groups

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS = {"analyst": "haiku", "closed_book": "haiku", "scout": "haiku", "judge": "haiku",
                  "engineer": "sonnet", "playbook": "haiku"}
MAX_LOGGED_REPLY = 20_000


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


class Lab:
    def __init__(self, problem, evaluator, compare: Callable, *, llm_backend, memory, search,
                 generations: int, proposals: int, seed: int, run_dir, models, max_cost_usd,
                 start_source, start_memory, playbook, resume: bool, splits, thresholds: Thresholds,
                 search_k: int, recall_k: int, runs_root):
        self.problem = problem
        self.evaluator = evaluator
        self.compare = compare
        self.generations = int(generations)
        self.proposals = int(proposals)
        self.seed = int(seed)
        self.models = {**DEFAULT_MODELS, **(models or {})}
        self.max_cost_usd = max_cost_usd
        self.th = thresholds
        self.search_k = search_k
        self.recall_k = recall_k
        self.playbook_in = playbook
        self.backend: Backend = llm_backend if isinstance(llm_backend, Backend) else get_backend(llm_backend)
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
        self.searcher = search if not isinstance(search, str) else get_search(search)
        self.search_name = getattr(self.searcher, "name", type(self.searcher).__name__)
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
        self.t0 = time.monotonic()
        self.started_at = self.prev.get("started_at") or to_iso(utcnow())
        self.status = "running"
        self.start_source = start_source

    # ------------------------------------------------------------------ logging

    def event(self, kind: str, **fields) -> None:
        row = {"time": to_iso(utcnow()), "event": kind, **fields}
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
        if self.over_cap():
            raise CostCapReached(f"cost cap ${self.max_cost_usd} reached (spent ${self.cost():.4f})")
        t0 = time.monotonic()
        try:
            r = complete(prompt, system=system, model=self.models.get(role), backend=self.backend,
                         budget=self.budget, label=role, max_tokens=8000 if role == "engineer" else 2000)
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

    def write_summary(self, incumbent_hash: str, curve: list, done: int, extra: dict | None = None) -> None:
        last = curve[-1] if curve else {}
        snap = self.budget.snapshot()
        summary = {
            "run_id": self.run_id, "problem": self.problem.name, "status": self.status,
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
            "accepted_total": self._count_accepted(),
            "findings_total": len(read_jsonl(self.run_dir / "findings.jsonl")),
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

    def _count_accepted(self) -> int:
        return sum(1 for f in read_jsonl(self.run_dir / "findings.jsonl") if f.accepted and not f.supersedes)

    def record_finding(self, finding: Finding, ctx: dict) -> None:
        with self._lock:
            with (self.run_dir / "findings.jsonl").open("a") as fh:
                fh.write(json.dumps(_scrub(finding.to_dict())) + "\n")
        try:
            self.memory.add(finding)
        except Exception as e:
            self.event("memory_error", finding=finding.id, error=f"{type(e).__name__}: {e}", **ctx)

    # ------------------------------------------------------------------ one proposal

    def proposal(self, gen: int, idx: int, incumbent_src: str, incumbent_res, digest: str,
                 worst: list[str]) -> dict:
        ctx = {"generation": gen, "proposal": idx}
        out: dict[str, Any] = {"generation": gen, "proposal": idx, "accepted": False}
        strategy = self.strategies[((gen - 1) * self.proposals + idx) % len(self.strategies)]
        out["strategy"] = strategy
        short = f"{self.problem.title} Target: {self.problem.target_key}. Loss: {self.problem.loss_name}."

        # b. recall + analyst
        query = f"{strategy} {self.problem.title} " + " ".join(worst)
        rec = self.memory.recall(query, k=self.recall_k)
        with self._lock:
            self.recall_stats["calls"] += 1
            self.recall_stats["latency_s"] += rec.latency_s
            self.recall_stats["chars"] += rec.chars
        self.event("recall", query=query, n=len(rec.findings), ids=[f.id for f in rec.findings],
                   chars=rec.chars, latency_s=rec.latency_s, **ctx)
        sys_, p = prompts.analyst(self.problem.describe(), incumbent_src, digest, rec.text, strategy,
                                  self.playbook_in)
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
        sys_, p = prompts.closed_book(short, gap, hypothesis)
        cb = parse_json(self.llm("closed_book", sys_, p, ctx)) or {}
        techniques = [t for t in (cb.get("techniques") or []) if isinstance(t, dict)]
        if not techniques:
            self.event("parse_fallback", role="closed_book", **ctx)

        # d. scout
        claim, origin, known = None, "closed_book", None
        if self.search_on:
            try:
                hits = self.searcher.search(gap, k=self.search_k)
            except Exception as e:
                self.event("search_error", query=gap, error=f"{type(e).__name__}: {e}", **ctx)
                hits = []
            self.event("search", query=gap, n=len(hits), hits=[{"title": h.title, "url": h.url, "year": h.year}
                                                                for h in hits], **ctx)
            if hits:
                sys_, p = prompts.scout(short, hypothesis, gap, [h.to_dict() for h in hits])
                c = parse_json(self.llm("scout", sys_, p, ctx))
                if c and c.get("technique"):
                    claim, origin = c, "search"
                    sys_, p = prompts.judge(c, techniques)
                    j = parse_json(self.llm("judge", sys_, p, ctx)) or {}
                    kc = j.get("known_closed_book")
                    known = kc if isinstance(kc, bool) else None
                else:
                    self.event("parse_fallback", role="scout", **ctx)
        if claim is None:
            claim = dict(techniques[0]) if techniques else \
                {"technique": "analyst hypothesis", "rule": hypothesis, "expected_effect": ""}
            claim.setdefault("source_title", "")
            claim.setdefault("source_url", "")
        claim_text = f"{str(claim.get('technique') or '').strip()}: {str(claim.get('rule') or '').strip().rstrip('.')}."
        if str(claim.get("expected_effect") or "").strip():
            claim_text += f" Expected: {str(claim['expected_effect']).strip().rstrip('.')}."
        out.update(claim=claim, origin=origin, known_closed_book=known)
        self.event("claim", claim=claim, origin=origin, known_closed_book=known, **ctx)

        # e. engineer (+ one repair)
        res, source, sel, conf = None, None, None, None
        error = None
        sys_, p = prompts.engineer(self.problem.describe(), incumbent_src, claim_text, hypothesis,
                                   self.problem.target_key, self.playbook_in)
        source = extract_code(self.llm("engineer", sys_, p, ctx))
        repaired = False
        while True:
            if source is None:
                error, lint_msgs = "the reply contained no Python code block that parses", []
            else:
                lint_msgs = lint(source)
                error = ("lint: " + "; ".join(lint_msgs)) if lint_msgs else None
            if lint_msgs or source is None:
                self.event("lint_failed", hash=source_hash(source) if source else None, lint=lint_msgs,
                           error=error, repaired=repaired, **ctx)
            else:
                res = self.evaluate(source, {**ctx, "repaired": repaired})
                error = None if res.ok else (res.error or "evaluation failed") + \
                    (("\nlint: " + "; ".join(res.lint)) if res.lint else "")
            if error is None or repaired:
                break
            repaired = True
            sys_, p = prompts.repair(source or "(no code)", error, self.problem.target_key)
            fixed = extract_code(self.llm("engineer", sys_, p, {**ctx, "repair": True}))
            source = fixed
        ok = bool(res is not None and res.ok and error is None)
        out.update(hash=source_hash(source) if source else None, ok=ok, error=error, repaired=repaired)

        # f. compare
        if ok:
            sel = self.compare(res, incumbent_res, self.splits, "select")
            conf = self.compare(res, incumbent_res, self.splits, "confirm")

        # g. decide
        accepted, reason = decide(ok, sel, conf, self.th)
        rel = relative_improvement(sel) if sel is not None else None
        out.update(accepted=accepted, reason=reason, select=_paired(sel), confirm=_paired(conf),
                   relative_improvement=rel, source=source, result=res)
        self.event("decision", hash=out["hash"], accepted=accepted, reason=reason, select=_paired(sel),
                   confirm=_paired(conf), relative_improvement=rel, error=error, **ctx)

        # h. finding
        f = Finding(claim=claim_text[:600], problem=self.problem.name, generation=gen,
                    source_title=str(claim.get("source_title") or ""), source_url=str(claim.get("source_url") or ""),
                    origin=origin, known_closed_book=known, accepted=accepted,
                    select_delta=sel.diff.mean if sel else None, select_low=sel.diff.low if sel else None,
                    select_high=sel.diff.high if sel else None, confirm_delta=conf.diff.mean if conf else None)
        if not ok:
            f.text = f.render() + f" Failure: {str(error)[:300]}"
        elif not accepted:
            f.text = f.render() + f" Why rejected: {reason[len('rejected: '):]}"
        out["finding"] = f
        self.record_finding(f, ctx)
        return out

    # ------------------------------------------------------------------ the run

    def run(self) -> dict:
        prev_curve = list(self.prev.get("curve") or [])
        done = int(self.prev.get("generations_done") or 0)
        if self.resume:
            incumbent_src = (self.run_dir / "incumbent.py").read_text()
            self.event("run_resume", generations_done=done, generations_planned=self.generations)
        else:
            incumbent_src = self.start_source or self.problem.seed_source()
            config = {"run_id": self.run_id, "problem": self.problem.name, "generations": self.generations,
                      "proposals": self.proposals, "seed": self.seed, "llm_backend": self.backend.name,
                      "models": self.models, "memory": self.memory_name, "search": self.search_name,
                      "max_cost_usd": self.max_cost_usd, "thresholds": asdict(self.th),
                      "search_k": self.search_k, "recall_k": self.recall_k,
                      "start_source_given": self.start_source is not None,
                      "start_memory": str(self.start_memory) if isinstance(self.start_memory, (str, Path))
                      else (f"{len(self.start_memory)} findings" if self.start_memory else None),
                      "playbook_given": bool(self.playbook_in), "started_at": self.started_at}
            self._write("config.json", json.dumps(config, indent=1, default=_jsonable))
            if self.playbook_in:
                self._write("playbook_in.md", self.playbook_in)
            imported = load_findings(self.start_memory)
            for f in imported:
                try:
                    self.memory.add(f)
                except Exception as e:
                    self.event("memory_error", finding=f.id, error=f"{type(e).__name__}: {e}")
            self.event("run_start", run_dir=str(self.run_dir), imported_findings=len(imported))
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
            outcomes = []
            with ThreadPoolExecutor(max_workers=max(1, self.proposals)) as pool:
                futs = [pool.submit(self.proposal, gen, i, incumbent_src, inc_res, digest, worst)
                        for i in range(self.proposals)]
                for i, fut in enumerate(futs):
                    try:
                        outcomes.append(fut.result())
                    except CostCapReached as e:
                        self.event("proposal_stopped", generation=gen, proposal=i, reason=str(e))
                    except Exception as e:
                        self.event("proposal_failed", generation=gen, proposal=i,
                                   error=f"{type(e).__name__}: {e}")
            accepted = [o for o in outcomes if o.get("accepted")]
            if accepted:
                best = min(accepted, key=lambda o: o["select"]["diff_mean"])
                for o in accepted:
                    if o is best:
                        continue
                    old = o["finding"]
                    note = Finding(claim=old.claim, problem=old.problem, generation=gen,
                                   source_title=old.source_title, source_url=old.source_url, origin=old.origin,
                                   known_closed_book=old.known_closed_book, accepted=True,
                                   select_delta=old.select_delta, select_low=old.select_low,
                                   select_high=old.select_high, confirm_delta=old.confirm_delta,
                                   supersedes=old.id)
                    note.text = old.text + (f" Passed the test but was not adopted: proposal {best['proposal']} "
                                            f"won generation {gen}; this change has not been tested on top of it.")
                    self.record_finding(note, {"generation": gen, "proposal": o["proposal"]})
                    self.event("superseded_this_generation", generation=gen, proposal=o["proposal"],
                               hash=o["hash"], by_hash=best["hash"], finding=old.id, note=note.id)
                incumbent_src, inc_res, inc_hash = best["source"], best["result"], best["hash"]
                self._write("incumbent.py", incumbent_src)
                self.event("new_incumbent", generation=gen, hash=inc_hash, proposal=best["proposal"],
                           select=best["select"], confirm=best["confirm"])
            curve.append({"generation": gen, "select": mean_loss(inc_res, "select"),
                          "confirm": mean_loss(inc_res, "confirm"), "incumbent_hash": inc_hash,
                          "accepted": len(accepted), "proposals": len(outcomes), "cost_usd": self.cost(),
                          "evaluations": self.evals_offset + self.evaluations,
                          "wall_s": self.wall_offset + time.monotonic() - self.t0})
            done = gen
            if self.over_cap():
                self.status = "stopped_cost_cap"
                self.event("cost_cap", generation=gen, cost_usd=self.cost(), cap=self.max_cost_usd)
            self.write_summary(inc_hash, curve, done)
            self.event("generation_end", generation=gen, accepted=len(accepted), incumbent_hash=inc_hash)
            if self.status == "stopped_cost_cap":
                break

        if self.status == "running":
            self.status = "finished"
        self.write_playbook()
        self.write_summary(inc_hash, curve, done)
        self.event("run_end", status=self.status, generations_done=done)
        return json.loads((self.run_dir / "summary.json").read_text())

    def write_playbook(self) -> None:
        findings = read_jsonl(self.run_dir / "findings.jsonl")
        block = "\n".join(f"- {f.text}" for f in findings)
        text = None
        if self.status != "stopped_cost_cap" and findings:
            try:
                sys_, p = prompts.playbook(self.problem.name, block[:30_000], self.playbook_in)
                text = self.llm("playbook", sys_, p, {"stage": "end_of_run"}).strip() or None
            except Exception as e:
                self.event("playbook_fallback", error=f"{type(e).__name__}: {e}")
                text = None
        if text is None:   # mechanical fallback: no LLM, just the measured findings
            acc = [f for f in findings if f.accepted and not f.supersedes]
            rej = [f for f in findings if not f.accepted]
            lines = [f"# Playbook for {self.problem.name} (written without a model: list of measured findings)", "",
                     "## Accepted"] + [f"- {f.claim} (select delta {f.select_delta})" for f in acc] + \
                    ["", "## Rejected or failed"] + [f"- {f.claim} (select delta {f.select_delta})" for f in rej]
            text = "\n".join(lines)
        self._write("playbook.md", text + "\n")


def run_lab(problem, evaluator, compare, *, llm_backend="claude_cli", memory="vector", search="openalex",
            generations=5, proposals=4, seed=0, run_dir=None, models=None, max_cost_usd=None,
            start_source=None, start_memory=None, playbook=None, resume=False, splits=None,
            p_min=0.95, min_rel_improvement=0.003, confirm_max_diff=0.0, search_k=5, recall_k=8,
            runs_root=None) -> dict:
    """Run the research lab; returns the final summary dict (also in <run_dir>/summary.json).

    `evaluator` needs `submit(source) -> Future[EvalResult]`; `compare(a, b, splits, window)`
    returns a PairedResult (a = candidate, b = incumbent). `llm_backend`, `memory` and `search`
    take a name or a ready object. `splits` defaults to `evaluator.splits`, else
    `problem.splits()`. `resume=True` continues the run in `run_dir` after its last finished
    generation."""
    lab = Lab(problem, evaluator, compare, llm_backend=llm_backend, memory=memory, search=search,
              generations=generations, proposals=proposals, seed=seed, run_dir=run_dir, models=models,
              max_cost_usd=max_cost_usd, start_source=start_source, start_memory=start_memory,
              playbook=playbook, resume=resume, splits=splits,
              thresholds=Thresholds(p_min, min_rel_improvement, confirm_max_diff),
              search_k=search_k, recall_k=recall_k, runs_root=runs_root)
    return lab.run()
