# The research loop

`lab.swarm.run_lab` improves one forecasting candidate (the **incumbent**) over a number of
**generations**. Each generation runs several **proposals** at once (threads). A proposal is
one idea, written as code, measured against the incumbent, and kept or rejected by a fixed rule.
Everything is written to the run directory as it happens.

```
.venv/bin/python -m lab.swarm.run --problem coast_water --generations 3 --proposals 4 \
    --memory vector --search openalex [--llm claude_cli] [--max-cost 5] [--warm-from <run_dir>]
.venv/bin/python -m lab.swarm.run --resume <run_dir> [--generations 5]
.venv/bin/python -m lab.swarm.run --mode single_agent ...      # baseline
.venv/bin/python -m lab.swarm.run --mode random_mutation ...   # baseline, no model
.venv/bin/python -m lab.swarm.run --warm-from <run_dir> --shuffle-memory ...   # control arm
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
| c. Closed book | small model | Sees the description and the incumbent; lists two or three changes it already knows that answer the knowledge gap, without search. |
| d. Scout | small model + search | Searches the knowledge gap. The model first grades every hit `domain` (about the problem's subject), `method` (a generic statistical method) or `unrelated`, prefers domain hits, never uses unrelated ones, and extracts **one** claim. A short judge call then records `known_closed_book`: was this already among the closed-book changes? With search off, no hits, or no usable hit, the claim is the first closed-book change and `origin` is `closed_book`. |
| e. Engineer | stronger model | Implements **only** the claimed change on top of the incumbent, keeping every other line, and starts the file with `# Change: <what changed>`. The prompt restates the sandbox rules. The loop lints the code itself first; on a missing code block, a lint failure or a failed run, the engineer gets **one** repair attempt with the error shown. |
| f. Evaluate | evaluator | Runs the candidate, then `compare` against the incumbent on `select` and on `confirm`. A candidate identical to the incumbent (same syntax tree, ignoring whitespace, comments and the `name` attribute) is not evaluated. |
| g. Decide | code | See below. |
| h. Record | code | At the end of the generation: one `Finding` per proposal (see "Findings" below) goes to memory and to `findings.jsonl`. |

Every claim, from search or closed book, must name ONE concrete change to the forecaster:
`technique`, `change` (one sentence), `inputs` (field names), `rule` (formula or rule in words),
`leads` (which leads it should help and why) and `expected_effect`. General methodology
("use cross-validation", "ensemble models") is ruled out in the prompt.

Strategies rotate through a short list (use information the incumbent ignores; fix the worst
lead; simplify or remove a term; borrow a technique from the literature; change how
coefficients are fitted), shuffled once by `seed`, so concurrent proposals look in different
directions. Default models: `haiku` for analyst, closed-book, scout, judge and playbook;
`sonnet` for the engineer and the single agent (override with `models={...}` or
`--analyst-model/--engineer-model`).

## Baseline modes

`mode` (`--mode`) swaps how a proposal produces its candidate. Everything from evaluation on
(identical check, evaluation, comparison, decision rule, findings, logging, generation
resolution) is shared, so budgets and numbers are comparable.

- `swarm` (default): the steps above.
- `single_agent`: one model call per proposal (role `single_agent`, the engineer's model) sees
  the description, the incumbent and the digest and writes the new candidate. No analyst,
  closed-book step, scout, search, memory recall or repair. The claim recorded is the file's
  `# Change:` line.
- `random_mutation`: no model at all. The incumbent's syntax tree is changed at random (seeded by
  run seed, generation and proposal): either one to three float constants are multiplied by
  exp(N(0, 0.3)), or one `a + b` / `a - b` inside a method is replaced by one operand. The result
  must pass the lint and differ from the incumbent (up to 30 draws). Comments are lost because
  the source is regenerated from the tree.

Both baselines skip search and write `playbook.md` without a model.

## The decision rule

Written in code (`lab/swarm/decide.py`); no model has a say. A candidate is accepted only if

1. its evaluation ran (`ok`),
2. on **select**: `p_a_better >= p_min` (default 0.95; share of block-bootstrap resamples in which
   the candidate's mean loss is lower), **and** relative improvement
   `(incumbent mean - candidate mean) / incumbent mean >= min_rel_improvement` (default 0.3%),
3. on **confirm**: mean difference (candidate minus incumbent) `<= confirm_max_diff` (default 0).

All three thresholds are parameters of `run_lab`. Every rejection carries a reason naming each
condition that failed.

A candidate identical to the incumbent is rejected with the reason "candidate is identical to the
incumbent" and the event `identical_to_incumbent`.

At the end of a generation the accepted proposal with the most negative select difference (ties:
lower proposal number) becomes the new incumbent; the others that passed get the event
`not_adopted`.

## Findings

Each proposal produces exactly one finding, written when its generation is resolved:

- `accepted`: passed the decision rule against the incumbent it was tested on.
- `became_incumbent`: true for the one adopted proposal, false for every other proposal.
- `duplicate_of`: the id of an earlier proposal in the same generation with the same code
  (same hash) or identical losses on select and confirm; null otherwise.
- `select_delta`, `select_low`, `select_high`, `confirm_delta`: that proposal's own measurement.
- `origin` (`search`, `closed_book`, `single_agent`, `random_mutation`), `search_backend` (which
  backend's hits the claim came from), `source_relevance` (`domain`, `method`; search claims
  only), `known_closed_book`.
- `text` starts with `[ACCEPTED, became incumbent]`, `[passed, not adopted]` or `[rejected]`.

So "accepted" and "became incumbent" can be counted separately from `findings.jsonl`, and
`summary.json` carries `accepted_total`, `became_incumbent_total` and `duplicates_total`.

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
Results are cached under `cache/search/<backend>/` (failures are not cached).

- Rate limits: on HTTP 429 a backend waits for the time the reply asks for (`Retry-After`, or
  "retry in Ns" in the body; 5 s if neither), at most 20 s, and retries up to twice.
- Fallback: `get_search("openalex")` is a chain, OpenAlex then arXiv. If a backend still fails,
  or finds nothing, the next one is tried. Choose the chain with `search_fallback=[...]`
  (`--search-fallback arxiv,exa`, or `none`).
- OpenAlex works without a key but anonymous search is rate-limited. `OPENALEX_API_KEY`, if set,
  is sent as `Authorization: Bearer <key>` (one of the two ways OpenAlex documents).
  `OPENALEX_MAILTO`, if set, is sent as `mailto`; the current OpenAlex docs no longer mention it.
- Counting: per backend, `searches` (real searches attempted), `failed` (raised after retries),
  `cache_hits` and `retries`; the chain adds `queries` and `queries_without_hits`. Useful
  claims per 100 searches can be computed from `findings.jsonl` (`origin`, `search_backend`,
  `accepted`) and `summary.json` (`search`).

## What is written

`runs/lab/<problem>/<run_id>/` (or `run_dir`):

- `config.json`: problem, mode, generations, proposals, seed, backend, models, memory, search
  and its fallback chain, thresholds, cost cap, whether a start source, start memory or playbook
  was given, and whether the imported notebook was shuffled.
- `events.jsonl`: one JSON line per event, with time, generation and proposal. Events cover:
  digest, recall (query, ids, chars, latency), every LLM call (role, model, prompt and reply
  sizes, the reply itself, tokens, cost, duration), search (query, hits, `search_backend`,
  failures), `search_error`, scout (relevance of every hit), claim, mutation, lint failures,
  `identical_to_incumbent`, evaluations (hash, ok, error, lint, duration, summary), decision
  (reason, select and confirm comparisons, relative improvement, the `# Change:` line),
  `generation_resolved`, `not_adopted`, new incumbent, errors, cost cap, run start, resume and end.
- `candidates/<hash>.py`: every candidate evaluated (hash = first 16 hex digits of SHA-256).
- `incumbent.py`: the current incumbent.
- `findings.jsonl`: this run's findings. `memory.jsonl`: everything the notebook holds,
  including imported findings.
- `summary.json`: rewritten after every generation so a dashboard can follow a run. It holds
  status (`running`, `finished`, `stopped_cost_cap`, `failed`), generations done, the curve
  (incumbent mean loss on select and confirm per generation, with cumulative cost, evaluations
  and wall-clock), the LLM token and cost use per role, search and recall statistics.
- `playbook.md`: written at the end (see below). `playbook_in.md`: the playbook the run started with.

The dashboard server reads these read-only: `GET /api/lab/runs` lists every run under
`runs/lab/*/*` (problem, run id, mode, status, generations, incumbent select and confirm loss,
cost, started and updated times) and `GET /api/lab/runs/{problem}/{run_id}` returns that run's
`summary.json`, `config.json`, findings and incumbent source. A file caught mid-write is
retried briefly, then reported as unreadable; a partial last line of `findings.jsonl` is
skipped (`lab/swarm/runs_index.py`).

## Robustness

- An LLM, search, memory or evaluation failure inside one proposal is logged and does not stop
  the generation. Unparseable JSON replies fall back to defaults (logged as `parse_fallback`).
- `max_cost_usd`: checked before every LLM call; once reached, open proposals stop, the
  generation is closed, and the run ends with status `stopped_cost_cap`. The end-of-run
  playbook is then written without a model.
- `resume=True` with `run_dir` continues after the last finished generation: it reloads
  `incumbent.py`, the notebook and the summary (cost, evaluations and wall-clock carry over),
  re-evaluates the incumbent once, and starts the next generation. An interrupted generation is
  run again from the start; its findings were never written, because findings are written when
  a generation is resolved.

## Fresh lab versus warm lab

A **fresh** run starts with an empty notebook and no playbook. A **warm** run
(`--warm-from <run_dir>`, or `start_memory=` and `playbook=`) differs in two ways only:

1. its notebook starts with the earlier run's findings, so recall can return them;
2. the earlier run's `playbook.md` is injected into the analyst and engineer prompts.

Both start from the problem's seed candidate unless `start_source` (`--start-source`) is given,
so a warm-versus-fresh comparison measures what the carried-over knowledge is worth.

**Shuffled-notebook control** (`--shuffle-memory`, `shuffle_memory=True`, needs `--warm-from`): the
imported findings keep their claims, sources and ids, but their outcome fields (`accepted`,
`became_incumbent` and the four deltas) are permuted across findings with the run's seed (never
the identity when there are two or more), and each imported finding's text is rebuilt from the
shuffled fields. `config.json` records `shuffle_memory: true`; the text shown to agents does not
say it was shuffled. This tests whether the content of memory matters or only its presence.

At the end
of every run the model writes a short playbook from that run's findings (and the playbook it
was given): what kinds of changes paid off, what did not, and pitfalls of the codebase. The prompt
says a candidate that failed to run is a pitfall, not evidence against its idea. If that call
fails, the cost cap was reached, or the run is a baseline, a plain list of the findings is
written instead, grouped as became incumbent / passed but not adopted / measured and rejected /
did not run.
