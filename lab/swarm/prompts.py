"""Every prompt the research loop sends, in one place.

Each builder returns (system, prompt). The analyst, closed-book, scout and judge
roles reply in JSON; the engineer replies with one complete Python file.
"""
from __future__ import annotations

import json

# One per proposal, rotated, so that concurrent proposals look in different directions.
STRATEGIES = [
    "Use information the incumbent ignores: find an input field (or a combination of fields) "
    "that the incumbent does not use and that should carry signal.",
    "Fix the worst lead: target the lead time (or other breakdown group) where the incumbent's "
    "loss is highest.",
    "Simplify or remove a term: find a part of the incumbent that likely adds noise or overfits, "
    "and remove or shrink it.",
    "Borrow a technique from the literature: find a published method for this kind of forecast "
    "and adapt its core idea.",
    "Change how coefficients are fitted: robust loss, shrinkage, per-group fits, recency "
    "weighting, or a different training subset.",
]

SANDBOX_RULES = """\
Sandbox rules (a candidate that breaks any of these is rejected before it runs):
- The file defines a top-level `class Forecaster` with a `name` attribute (a short string),
  `fit(self, cases, truths)` and `predict(self, case) -> dict`.
- `fit` is called once with past cases and their truth dicts. `predict` is called once per
  later case and must return a dict containing the key {target_key!r} (plus any other keys
  you like), every value a finite Python float.
- A case has `case_id`, `issue_time`, `target_time` (timezone-aware datetimes), `lead_hours`
  (float) and `inputs` (dict). `predict` may use only these; truths are never available there.
- Imports allowed: math, numpy, statistics, dataclasses, typing, datetime, collections,
  itertools, functools. Nothing else (no pandas, scipy, sklearn, os, sys, io, json, random).
- No names, attributes or definitions starting with `__`, except `__init__`.
- Do not call or mention: open, eval, exec, compile, __import__, input, globals, locals, vars,
  getattr, setattr, delattr, breakpoint.
- Do not use these names as attributes or methods, even your own: load, save, savez, dump,
  fromfile, tofile, memmap, genfromtxt, loadtxt, savetxt, testing, io, types, os, sys,
  inspect, platform. Rename your own helpers if needed.
- At most 400 lines and 30,000 characters. No literal list/dict/tuple/set with more than 200
  elements or more than 2,000 characters of strings (no lookup tables of answers).
- No files, no network, no printing results. It must finish within the time limit on CPU.
- Handle missing or non-finite inputs: fall back to a sensible value instead of raising."""

ANALYST_SYSTEM = ("You are the analyst in a forecasting research lab. You read the current model, "
                  "its measured errors and the lab notebook, and propose ONE specific, testable change. "
                  "Reply with a single JSON object and nothing else.")

CLOSED_BOOK_SYSTEM = ("You are a forecasting methods expert. Answer from what you already know; you "
                      "have no search. Reply with a single JSON object and nothing else.")

SCOUT_SYSTEM = ("You are the literature scout in a forecasting research lab. You read search results "
                "and extract ONE concrete, testable technique. Reply with a single JSON object and "
                "nothing else.")

JUDGE_SYSTEM = "You compare two lists of techniques. Reply with a single JSON object and nothing else."

ENGINEER_SYSTEM = ("You are the engineer in a forecasting research lab. You turn one claim into a "
                   "complete, working Python file that obeys the sandbox rules exactly. Reply with "
                   "the full file in one ```python block.")

PLAYBOOK_SYSTEM = ("You write short, practical playbooks for a forecasting research lab, based only on "
                   "measured results.")


def _playbook_block(playbook: str | None) -> str:
    if not playbook:
        return ""
    return f"\n## Playbook from earlier runs (advice, not facts; the measurements below win)\n{playbook.strip()}\n"


def analyst(describe: str, incumbent: str, digest: str, recalled: str, strategy: str,
            playbook: str | None) -> tuple[str, str]:
    prompt = f"""# Problem
{describe.strip()}

# Current model (the incumbent)
```python
{incumbent.strip()}
```

# Where the incumbent loses (select window; aggregates only)
{digest.strip()}

# Lab notebook: related findings (accepted ones were adopted or passed the test; rejected ones failed)
{recalled.strip() or "(nothing recorded yet)"}
{_playbook_block(playbook)}
# Your direction for this proposal
{strategy}

Do not repeat a change the notebook shows was already rejected unless you say what is different.

Reply with JSON exactly like this:
{{"diagnosis": "one or two sentences on what limits the incumbent now",
  "hypothesis": "one sentence: the change and why it should lower the loss",
  "knowledge_gap": "a short literature search query (4 to 10 words) that would help",
  "strategy": "the direction you followed, in a few words"}}"""
    return ANALYST_SYSTEM, prompt


def closed_book(describe_short: str, gap: str, hypothesis: str) -> tuple[str, str]:
    prompt = f"""Problem: {describe_short.strip()}
Hypothesis under consideration: {hypothesis.strip()}
Question: {gap.strip()}

Without searching, list two or three concrete techniques you already know that answer the question.
For each give the rule or formula in words and the effect you expect on the error.

Reply with JSON exactly like this:
{{"techniques": [{{"technique": "name", "rule": "the formula or rule in words",
                   "expected_effect": "what should improve and roughly by how much"}}]}}"""
    return CLOSED_BOOK_SYSTEM, prompt


def scout(describe_short: str, hypothesis: str, gap: str, hits: list[dict]) -> tuple[str, str]:
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h.get('title')} ({h.get('year')}) {h.get('url')}\n{h.get('text') or '(no abstract)'}")
    prompt = f"""Problem: {describe_short.strip()}
Hypothesis under consideration: {hypothesis.strip()}
Search query: {gap.strip()}

# Search results
{chr(10).join(lines)}

Pick the ONE result that gives the most useful technique to test in this model, and extract it.
Only use what the result says; do not invent results.

Reply with JSON exactly like this:
{{"technique": "name", "rule": "the formula or rule in words, specific enough to code",
  "expected_effect": "what should improve", "source_title": "title of the result",
  "source_url": "its url"}}"""
    return SCOUT_SYSTEM, prompt


def judge(claim: dict, techniques: list[dict]) -> tuple[str, str]:
    prompt = f"""Technique found in the literature:
{json.dumps(claim, indent=1)}

Techniques named earlier from memory, without search:
{json.dumps(techniques, indent=1)}

Was the literature technique essentially already among the techniques named from memory
(same core idea, even if worded differently)?

Reply with JSON exactly like this: {{"known_closed_book": true, "why": "one sentence"}}"""
    return JUDGE_SYSTEM, prompt


def engineer(describe: str, incumbent: str, claim: str, hypothesis: str, target_key: str,
             playbook: str | None) -> tuple[str, str]:
    prompt = f"""# Problem
{describe.strip()}

# Current model (the incumbent)
```python
{incumbent.strip()}
```

# The claim to test
{claim.strip()}

Why: {hypothesis.strip()}
{_playbook_block(playbook)}
# {SANDBOX_RULES.format(target_key=target_key)}

# Task
Write the COMPLETE new candidate file: the incumbent with the claim implemented. Change only what
the claim needs; keep everything else that works. Give the class a new `name` that says what changed.
Return the whole file (not a diff, no placeholders, no "rest unchanged") in one ```python block."""
    return ENGINEER_SYSTEM, prompt


def repair(source: str, error: str, target_key: str) -> tuple[str, str]:
    prompt = f"""This candidate failed.

# Error
{error.strip()[:3000]}

# Candidate
```python
{source.strip()}
```

# {SANDBOX_RULES.format(target_key=target_key)}

Fix the problem and return the COMPLETE corrected file in one ```python block."""
    return ENGINEER_SYSTEM, prompt


def playbook(problem_name: str, findings_text: str, old_playbook: str | None) -> tuple[str, str]:
    prompt = f"""Research run on problem `{problem_name}` is over. These are all its findings, each with
its measured effect (loss units; negative delta is better; accepted ones passed the test):

{findings_text.strip() or "(no findings)"}
{_playbook_block(old_playbook)}
Write a short playbook (at most 25 lines of markdown) for the next run on this problem:
1. Kinds of changes that paid off, with the measured size.
2. Kinds of changes that did not, so they are not retried blindly.
3. Pitfalls of this codebase and sandbox (errors that wasted attempts).
Keep only what the findings support. Plain sentences."""
    return PLAYBOOK_SYSTEM, prompt
