# The research loop

`lab.swarm.run_lab` improves one forecasting candidate (the **incumbent**) over a number of
**generations**. Each generation runs several **proposals** at once (threads). A proposal is
one idea, written as code, measured against the incumbent, and kept or rejected by a fixed rule.
Everything is written to the run directory as it happens.

```
.venv/bin/python -m lab.swarm.run --problem coast_water --generations 3 --proposals 4 \
    --memory vector --search openalex [--llm claude_cli] [--max-cost 5] [--warm-from <run_dir>]
.venv/bin/python -m lab.swarm.run --resume <run_dir> [--generations 5]
```

The evaluator is injected: anything with `submit(source) -> Future[EvalResult]` plus a
`compare(a, b, splits, window) -> PairedResult` function (a = candidate, b = incumbent;
negative `diff.mean` means the candidate has lower loss). The CLI builds `lab.evalpool.EvalPool`;
the tests use an in-process fake with the same contract (`tests/swarm_fakes.py`).

## One proposal

| Step | Who | What |
|---|---|---|
| a. Failure digest | code | From the incumbent's select-window evaluation: mean loss, every breakdown in `summary["select"]` (by lead, and any others the problem reports), quantiles of the per-case loss, the share of loss from the worst 10% of cases, and the highest-loss groups. Aggregates only; agents never see a case's truth. |
| b. Analyst | small model | Gets the problem description, the incumbent source, the digest, the playbook (if any), the findings recalled from memory, and its **strategy**. Replies in JSON: `diagnosis`, `hypothesis`, `knowledge_gap` (a search query), `strategy`. |
| c. Closed book | small model | Lists two or three techniques it already knows that answer the knowledge gap, without search. |
| d. Scout | small model + search | Searches the knowledge gap, reads the hits, and extracts **one** claim: technique, rule in words, expected effect, source title and URL. A short judge call then records `known_closed_book`: was this already among the closed-book techniques? With search off (or no hits), the claim is the first closed-book technique and `origin` is `closed_book`. |
| e. Engineer | stronger model | Writes the complete new candidate file from the description, the incumbent and the claim. The prompt restates the sandbox rules. The loop lints the code itself first; on a missing code block, a lint failure or a failed run, the engineer gets **one** repair attempt with the error shown. |
| f. Evaluate | evaluator | Runs the candidate, then `compare` against the incumbent on `select` and on `confirm`. |
| g. Decide | code | See below. |
| h. Record | code | A `Finding` (accepted or not, with the measured deltas and interval) goes to memory and to `findings.jsonl`. |

Strategies rotate through a short list (use information the incumbent ignores; fix the worst
lead; simplify or remove a term; borrow a technique from the literature; change how
coefficients are fitted), shuffled once by `seed`, so concurrent proposals look in different
directions. Default models: `haiku` for analyst, closed-book, scout, judge and playbook;
`sonnet` for the engineer (override with `models={...}` or `--analyst-model/--engineer-model`).

## The decision rule

Written in code (`lab/swarm/decide.py`); no model has a say. A candidate is accepted only if

1. its evaluation ran (`ok`),
2. on **select**: `p_a_better >= p_min` (default 0.95; share of block-bootstrap resamples in which
   the candidate's mean loss is lower), **and** relative improvement
   `(incumbent mean - candidate mean) / incumbent mean >= min_rel_improvement` (default 0.3%),
3. on **confirm**: mean difference (candidate minus incumbent) `<= confirm_max_diff` (default 0).

All three thresholds are parameters of `run_lab`. Every rejection carries a reason naming each
condition that failed.

At the end of a generation the accepted proposal with the most negative select difference becomes
the new incumbent. Any other accepted proposal is marked "superseded this generation": an event
is logged and a follow-up finding (with `supersedes` = the original finding's id) notes that it
passed but was not adopted and has not been tested on top of the new incumbent.

## Memory and search

The notebook (`lab/memory/`) has one interface, `add(finding)` and
`recall(query, as_of=None, k=8, identity="lab") -> RecallResult(findings, text, latency_s, chars)`,
and four backends so they can be compared head to head:

- `none`: recalls nothing (the lab without a notebook).
- `full_context`: every finding up to `as_of`, newest first, cut at a character budget.
- `vector`: TF-IDF cosine similarity in numpy over claim, text and source title.
- `pastdev`: the past.dev API (`POST /ingest`, poll `GET /ingest/{ingestionId}`, `POST /recall`
  with `queryTimestamp`); needs `PAST_API_KEY`.

`as_of` hides every finding recorded after that moment, in every backend. The local backends
also hide a finding once a visible later finding supersedes it. Every backend appends every
finding it is given to a JSONL file (`memory.jsonl` in the run directory), and reopening the
file reloads it.

Search (`lab/search/`): `openalex` and `arxiv` (keyless), `exa` (`EXA_API_KEY`) and `none`.
Results are cached under `cache/search/<backend>/`; `stats()` counts real calls and cache hits.

## What is written

`runs/lab/<problem>/<run_id>/` (or `run_dir`):

- `config.json`: problem, generations, proposals, seed, backend, models, memory, search,
  thresholds, cost cap, whether a start source, start memory or playbook was given.
- `events.jsonl`: one JSON line per event, with time, generation and proposal. Events cover:
  digest, recall (query, ids, chars, latency), every LLM call (role, model, prompt and reply
  sizes, the reply itself, tokens, cost, duration), search (query and hits), claim, lint
  failures, evaluations (hash, ok, error, lint, duration, summary), decision (reason, select and
  confirm comparisons, relative improvement), new incumbent, superseded proposals, errors,
  cost cap, run start, resume and end.
- `candidates/<hash>.py`: every candidate evaluated (hash = first 16 hex digits of SHA-256).
- `incumbent.py`: the current incumbent.
- `findings.jsonl`: this run's findings. `memory.jsonl`: everything the notebook holds,
  including imported findings.
- `summary.json`: rewritten after every generation so a dashboard can follow a run. It holds
  status (`running`, `finished`, `stopped_cost_cap`, `failed`), generations done, the curve
  (incumbent mean loss on select and confirm per generation, with cumulative cost, evaluations
  and wall-clock), the LLM token and cost use per role, search and recall statistics.
- `playbook.md`: written at the end (see below). `playbook_in.md`: the playbook the run started with.

## Robustness

- An LLM, search, memory or evaluation failure inside one proposal is logged and does not stop
  the generation. Unparseable JSON replies fall back to defaults (logged as `parse_fallback`).
- `max_cost_usd`: checked before every LLM call; once reached, open proposals stop, the
  generation is closed, and the run ends with status `stopped_cost_cap`. The end-of-run
  playbook is then written without a model.
- `resume=True` with `run_dir` continues after the last finished generation: it reloads
  `incumbent.py`, the notebook and the summary (cost, evaluations and wall-clock carry over),
  re-evaluates the incumbent once, and starts the next generation. An interrupted generation is
  run again from the start.

## Fresh lab versus warm lab

A **fresh** run starts with an empty notebook and no playbook. A **warm** run
(`--warm-from <run_dir>`, or `start_memory=` and `playbook=`) differs in two ways only:

1. its notebook starts with the earlier run's findings, so recall can return them;
2. the earlier run's `playbook.md` is injected into the analyst and engineer prompts.

Both start from the problem's seed candidate unless `start_source` (`--start-source`) is given,
so a warm-versus-fresh comparison measures what the carried-over knowledge is worth. At the end
of every run the model writes a short playbook from that run's findings (and the playbook it
was given): what kinds of changes paid off, what did not, and pitfalls of the codebase. If that
call fails, or the cost cap was reached, a plain list of the measured findings is written instead.
