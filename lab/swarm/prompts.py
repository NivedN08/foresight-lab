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


CLAIM_FIELDS = """  "technique": "short name",
  "change": "the ONE change to the current forecaster, in one sentence",
  "inputs": "which fields of case.inputs (or lead_hours / times) it uses, by their exact names",
  "rule": "the formula or rule in words, specific enough to code without guessing",
  "leads": "which lead times it should help, and why",
  "expected_effect": "what should improve and roughly by how much\""""

CONCRETE = """A claim must name ONE concrete, testable change to the current forecaster: which inputs, what
formula or rule, and why it should lower the error at which leads. Not acceptable: a general
methodology ("use cross-validation", "try machine learning", "tune hyperparameters", "ensemble
several models"), a rewrite of the whole model, or anything needing inputs the problem does not
provide."""


def closed_book(describe: str, incumbent: str, gap: str, hypothesis: str) -> tuple[str, str]:
    prompt = f"""# Problem
{describe.strip()}

# Current forecaster
```python
{incumbent.strip()}
```

Hypothesis under consideration: {hypothesis.strip()}
Question: {gap.strip()}

Without searching, give two or three changes you already know that answer the question.
{CONCRETE}

Reply with JSON exactly like this:
{{"techniques": [{{
{CLAIM_FIELDS}
}}]}}"""
    return CLOSED_BOOK_SYSTEM, prompt


def scout(describe: str, incumbent: str, hypothesis: str, gap: str, hits: list[dict]) -> tuple[str, str]:
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h.get('title')} ({h.get('year')}) {h.get('url')}\n{h.get('text') or '(no abstract)'}")
    prompt = f"""# Problem
{describe.strip()}

# Current forecaster
```python
{incumbent.strip()}
```

Hypothesis under consideration: {hypothesis.strip()}
Search query: {gap.strip()}

# Search results
{chr(10).join(lines)}

First judge every result: "domain" if it is actually about this problem's subject (the physical or
real-world process being forecast), "method" if it is a generic statistical or machine-learning
method that is not about this subject, "unrelated" otherwise.
Then pick ONE result to extract a claim from. Prefer a "domain" result whenever any domain result
gives a usable change; use a "method" result only if no domain result does; never use an
"unrelated" result. If no result gives a usable change, set "chosen" to null.
{CONCRETE}
Only use what the chosen result says; do not invent results.

Reply with JSON exactly like this:
{{"relevance": [{{"n": 1, "relevance": "domain"}}],
  "chosen": 1,
  "source_relevance": "domain",
{CLAIM_FIELDS},
  "source_title": "title of the chosen result",
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
Implement ONLY the change in the claim, on top of the incumbent. Keep every other line identical:
the same features, the same fitting, the same fallbacks, the same helper names. Change the `name`
attribute to say what changed. Do not rewrite, restructure or "clean up" anything else; the point
is to measure this one change.
The first line of the file must be a one-line comment that says what changed, like:
# Change: add the 24 h pressure tendency as a term in the residual fit
If the claim cannot be implemented with the available inputs, implement the closest faithful
version and say so in that comment.
Return the COMPLETE file (not a diff, no placeholders, no "rest unchanged") in one ```python block."""
    return ENGINEER_SYSTEM, prompt


SINGLE_AGENT_SYSTEM = ("You improve forecasting models. You propose one change and write the complete, "
                       "working Python file that obeys the sandbox rules exactly. Reply with the full file "
                       "in one ```python block.")


def single_agent(describe: str, incumbent: str, digest: str, target_key: str) -> tuple[str, str]:
    prompt = f"""# Problem
{describe.strip()}

# Current model (the incumbent)
```python
{incumbent.strip()}
```

# Where the incumbent loses (select window; aggregates only)
{digest.strip()}

# {SANDBOX_RULES.format(target_key=target_key)}

# Your task (single agent)
Choose ONE concrete change that should lower the loss (which inputs, what formula or rule, which
leads it helps) and implement it on top of the incumbent, keeping everything else the same.
The first line of the file must be a one-line comment that says what changed, like:
# Change: add the 24 h pressure tendency as a term in the residual fit
Return the COMPLETE file in one ```python block."""
    return SINGLE_AGENT_SYSTEM, prompt


def repair(source: str, error: str, target_key: str, keep_header: bool = False) -> tuple[str, str]:
    keep_header = ("\nKeep the comment lines at the top of the file (# Change:, # Idea source:, # Why:) "
                   "as they are, unless the fix changes what they say.") if keep_header else ""
    prompt = f"""This candidate failed.

# Error
{error.strip()[:3000]}

# Candidate
```python
{source.strip()}
```

# {SANDBOX_RULES.format(target_key=target_key)}

Fix the problem and return the COMPLETE corrected file in one ```python block.{keep_header}"""
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
Keep only what the findings support. A change counts as "did not pay off" only if it was measured
(it has a select delta); a candidate that failed to run says nothing about its idea, so list it
under pitfalls instead. "Passed, not adopted" means it worked but another change won that
generation. Plain sentences."""
    return PLAYBOOK_SYSTEM, prompt


# ====================================================================== researcher mode
#
# One strong call per proposal that chooses its own change, with two optional inputs it
# may use or ignore: the notebook (what was tried and what it did) and an ideas board
# scouted from search once per generation.

RESEARCHER_SYSTEM = ("You are a forecasting researcher. You study the current model and where it loses, "
                     "choose ONE change you expect to lower the loss, and write the complete, working Python "
                     "file that obeys the sandbox rules exactly. Reply with the full file in one ```python block.")

HEADER_RULES = """The file must start with exactly these three comment lines:
# Change: <one line: what you changed>
# Idea source: own | notebook:<finding id> | search:<idea id>
# Why: <one line: why it should lower the loss, and at which leads>
Use "own" for your own idea, "notebook:<id>" if it builds on a notebook finding (use its id in
square brackets), "search:<id>" if it implements or adapts a board idea (use its id)."""


def researcher(describe: str, incumbent: str, digest: str, strategy: str, target_key: str, *,
               notebook: str | None = None, board: str | None = None, playbook: str | None = None) -> tuple[str, str]:
    parts = [f"""# Problem
{describe.strip()}

# Current model (the incumbent)
```python
{incumbent.strip()}
```

# Where the incumbent loses (select window; aggregates only)
{digest.strip()}
"""]
    if notebook is not None:
        parts.append(f"""# Lab notebook: changes already tried on this problem
Each entry has an id in square brackets, what was tried, its measured effect on the select window, and
whether it passed the later-window check. "ACCEPTED, became incumbent" means it is already in the model.
Do not repeat a change that was tested and rejected unless you say in "# Why:" what is different this time.
{notebook.strip() or "(nothing recorded yet)"}
""")
    if board is not None:
        parts.append(f"""# Ideas board: changes scouted from the literature this generation
You may implement one board idea, adapt one, or ignore the board entirely. Each has an id.
{board.strip() or "(the scouts found nothing usable this generation)"}
""")
    parts.append(_playbook_block(playbook))
    parts.append(f"""# A place you might look (a suggestion, not an order)
{strategy}

# {SANDBOX_RULES.format(target_key=target_key)}

# Your task
Choose ONE concrete change that should lower the loss (which inputs, what formula or rule, which leads it
helps) and implement it on top of the incumbent, keeping everything else the same. Change the `name`
attribute to say what changed.
{HEADER_RULES}
Return the COMPLETE file in one ```python block.""")
    return RESEARCHER_SYSTEM, "\n".join(x for x in parts if x)


ANTICIPATE_SYSTEM = ("You are a forecasting researcher. Answer from what you already know; you have no search. "
                     "Reply with a single JSON object and nothing else.")


def anticipate(describe: str, incumbent: str, digest: str) -> tuple[str, str]:
    """Closed-book, once per generation, before the ideas board is shown: what would you try next?"""
    prompt = f"""# Problem
{describe.strip()}

# Current model (the incumbent)
```python
{incumbent.strip()}
```

# Where the incumbent loses (select window; aggregates only)
{digest.strip()}

Without searching, list the five to eight concrete changes you would try next on this model, each in one
sentence (which inputs, what formula or rule).

Reply with JSON exactly like this: {{"changes": ["...", "..."]}}"""
    return ANTICIPATE_SYSTEM, prompt


SCOUT_QUERIES_SYSTEM = ("You write literature search queries for a forecasting research lab. Reply with a "
                        "single JSON object and nothing else.")


def scout_queries(title: str, describe_short: str, incumbent_summary: str, digest: str, n: int) -> tuple[str, str]:
    prompt = f"""Problem: {title}
{describe_short.strip()}

The current model, in short:
{incumbent_summary.strip()}

Where it loses (select window):
{digest.strip()}

Write up to {n} literature search queries (4 to 10 words each) that would find concrete techniques for THIS
problem's subject (the physical or real-world process being forecast), aimed at where the model loses.
Prefer domain terms over generic statistics words.

Reply with JSON exactly like this: {{"queries": ["...", "..."]}}"""
    return SCOUT_QUERIES_SYSTEM, prompt


BOARD_SYSTEM = ("You distil search results into a short board of concrete ideas for a forecasting research "
                "lab. Reply with a single JSON object and nothing else.")


def scout_board(title: str, describe_short: str, hits: list[dict], n: int) -> tuple[str, str]:
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h.get('title')} ({h.get('year')}) {h.get('url')}\n{h.get('text') or '(no abstract)'}")
    prompt = f"""Problem: {title}
{describe_short.strip()}

# Search results
{chr(10).join(lines)}

Extract at most {n} ideas, each ONE concrete change to a forecaster for this problem, stated in one or two
sentences (which inputs, what formula or rule), with why it should help and at which leads. Judge each
idea's source: "domain" if the result is about this problem's subject, "method" if it is a generic
statistical or machine-learning method, "unrelated" otherwise. Prefer domain results. Use only what the
results say; do not invent results. General methodology ("use cross-validation", "ensemble models") is
not an idea.

Reply with JSON exactly like this:
{{"ideas": [{{"hit": 1, "change": "...", "why": "...", "leads": "...", "source_relevance": "domain",
             "source_title": "...", "source_url": "..."}}]}}"""
    return BOARD_SYSTEM, prompt


def board_judge(ideas: list[dict], anticipated: list[str]) -> tuple[str, str]:
    listed = "\n".join(f"- {c}" for c in anticipated) or "(none)"
    board = "\n".join(f"- {i['id']}: {i['change']}" for i in ideas)
    prompt = f"""Changes a strong model said it would try next, before seeing any search results:
{listed}

Ideas found by search:
{board}

For each search idea, was it essentially already on the first list (same core change, even if worded
differently)?

Reply with JSON exactly like this: {{"known": {{"<idea id>": true}}}}"""
    return JUDGE_SYSTEM, prompt
