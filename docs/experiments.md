# Experiments: matrices of lab runs and their report

The protocol (`planning/03_experiment_protocol.md`) makes four claims. Each claim is tested by a
**matrix**: a set of **arms** (ways of running the lab), each run once per **seed**, all with the
same problem and budget. `lab.experiments.matrix` runs a matrix; `lab.experiments.report` turns
its runs into tables.

```
.venv/bin/python -m lab.experiments.matrix run lab/experiments/configs/claim1_coast.json --dry-run
.venv/bin/python -m lab.experiments.matrix run lab/experiments/configs/claim1_coast.json [--parallel 2] [--only swarm]
.venv/bin/python -m lab.experiments.report claim1_coast [--target 2.5] [--compare swarm,single_agent]
```

## How a matrix runs

- Each (arm, seed) is one lab run (`lab.swarm.run_lab`) in its own process, written to
  `runs/exp/<matrix>/<arm>/seed<k>/`. That directory holds the usual run files (see
  `docs/swarm.md`) plus `spec.json` (exactly what the run was given) and `worker.log`.
- At most `--parallel` runs at once (default 2: model calls and evaluations share one laptop).
  Each run's evaluator uses `eval_workers` processes (default 2). Set `"evaluator": "modal"` in
  the config to run candidates on Modal instead (`lab.evalpool.registry.make_pool`, docs/modal.md).
- `runs/exp/<matrix>/manifest.json` records the config, start times and each run's directory,
  status (`queued`, `running`, `finished`, `stopped_cost_cap`, `crashed`, `skipped`, `blocked`),
  exit code, duration and cost.
- Re-running the same command is safe. Finished runs are skipped, and a run with a summary,
  config and incumbent but not finished is resumed from its last finished generation. A crashed
  run is marked `crashed` with the end of its log and does not stop the others.
- An arm that needs a key that is not set (`EXA_API_KEY` for Exa search, `PAST_API_KEY` for
  past.dev memory, or anything in `requires_env`) is skipped with a message saying which key.
- An arm that carries knowledge from another run (`warm_from`) waits, marked `blocked`, until
  that run has finished. Run the source matrix first.
- `--dry-run` prints the plan and an estimate and writes nothing.

## The config

```json
{"name": "claim1_coast", "comment": "...", "problem": "coast_water",
 "budget": {"generations": 6, "proposals": 4, "max_cost_usd": 5.0},
 "seeds": [1, 2],
 "arms": [{"name": "swarm", "mode": "swarm", "memory": "vector", "search": "openalex",
           "search_fallback": ["arxiv"]}, ...]}
```

Arm keys:
- `mode`: `swarm`, `single_agent` or `random_mutation`.
- `memory` and `search`: as in `docs/swarm.md`.
- `search_fallback`: a list; `[]` means no fallback.
- `models` and `llm`.
- `start_source`: a file holding the starting candidate.
- `carry` and `warm_from`: see below.
- `shuffle_memory`, `requires_env`, `comment`.

`max_cost_usd` is a cap per run. Problems come from `lab/evalpool/registry.py`
(`coast_water`, `turb`, `series`).

### Carrying knowledge between problems (`carry`)

`warm_from` names the run to carry from. It is either a run directory, or
`{"matrix": "claim1_coast", "arm": "swarm", "seed": "same"}`, meaning that arm's run with the
same seed.

| `carry` | What the run starts with | What it isolates |
|---|---|---|
| `none` | nothing | the fresh reference |
| `playbook_only` | the old run's `playbook.md` | what the distilled advice alone is worth |
| `notebook_only` | the old run's findings in its notebook | what the raw notebook alone is worth |
| `both` | notebook and playbook | the warm lab (the claim) |
| `shuffled_notebook` | the old notebook with outcomes shuffled across findings, no playbook | whether the *content* of memory matters or only its presence; compare against `notebook_only` |
| `cheat_sheet` | no notebook, no playbook; a fixed text in the playbook's place | whether the warm lab's edge is only familiarity with the candidate interface |

The cheat sheet is generated from the new problem's own `describe()`. It holds a fixed paragraph
on the candidate interface (`class Forecaster`, `fit`, `predict`, the target key) plus the
description's sandbox-rules and inputs sections, cut to about a playbook's length
(`cheat_sheet_chars`, default 2,000 characters). It contains no findings and no scores. It is
shown under the same heading as a carried playbook, so an agent cannot tell which arm it is in.
It is saved as `cheat_sheet.md` in the run directory.

The protocol's control "fresh, handed the old best forecaster and a cheat sheet" is replaced by
`cheat_sheet`. A forecaster written for one problem (coast water levels) cannot run on another
(turbulence): its inputs and target do not exist there. So there is no "old answer" to hand over.
What remains to control for is familiarity with the code interface, and that is what
`cheat_sheet` gives.

## The ready-made matrices

All four use small budgets (6 generations x 4 proposals, seeds 1 and 2, $5 cap per run). Raise
`generations` and `seeds` for the runs that go in the pitch: the protocol asks for five seeds for
fresh and warm and three for the shuffled arm, and the report will not test a difference on fewer
than five.

| Config | Claim | Arms | Compare |
|---|---|---|---|
| `claim1_coast.json` | 1: the lab beats simpler uses of the same budget | `swarm`, `single_agent`, `random_mutation` | swarm against each baseline: final confirm loss, time to target |
| `claim3_search_coast.json` | 3: search finds what the model did not know | `search_openalex`, `search_arxiv`, `search_off`, `search_exa` (needs `EXA_API_KEY`) | the Search table; each arm uses one backend, no fallback |
| `claim4_memory_coast.json` | 4: shared memory pays | `memory_none`, `memory_full_context`, `memory_vector`, `memory_pastdev` (needs `PAST_API_KEY`) | final loss, minutes to target, dollars, recall latency: reported separately |
| `claim2_transfer_turb.json` | 2: the lab gets better at research | `fresh`, `cheat_sheet`, `warm`, `warm_notebook`, `warm_shuffled` on turbulence, carrying from claim 1's `swarm` arm | warm against fresh and cheat_sheet; warm_shuffled against warm_notebook |

`warm_notebook` was added to claim 2 so the shuffled arm has a comparator that differs only in
the notebook's content. Run claim 1 before claim 2; until a source run has finished, its warm
arms are `blocked`.

## Reading the report

`report.md` and `report.json` are written to `runs/exp/<matrix>/`. Every table says how many seeds
it rests on. If any planned run is unfinished the report opens with **PARTIAL RESULTS**, and an
arm with no finished run is marked partial. Runs written before `became_incumbent` existed show
"n/a" there; their old follow-up rows are not counted as proposals.

- **Results by arm**: runs finished of planned; final incumbent loss on select and confirm
  (mean and range across seeds); proposals, accepted (passed the rule), became incumbent
  (adopted); cost, minutes and evaluations per run.
- **Time to target**: the target is a confirm loss, given with `--target` or else the median final
  confirm loss of the arm named `fresh` (or the first arm). For each run: proposals, dollars and
  minutes until the incumbent first reached it. Runs that never reached it are listed as
  "not reached" (or "not reached (yet)" while running) and counted in "k of n reached", never dropped.
- **Search**, by arm and backend: searches made (real calls, cache hits excluded) and failed,
  claims from search, accepted, the share of accepted search claims that were not already known
  closed-book, and accepted claims per 100 searches.
- **Memory**: recall calls, mean recall latency, mean characters returned.
- **Paired comparison** (`--compare A,B`, default `warm,fresh` or the first two arms): the
  difference in final confirm loss per seed and how many seeds each arm won. With fewer than five
  paired seeds it says "too few seeds to test"; with five or more it gives a two-sided sign test.
  It makes no other significance claim.

## Cost and time to expect

`--dry-run` estimates from runs already on disk for the same problem, preferring finished runs of
the same mode. Measured so far, from the coast run `runs/lab/coast_water/20261114T000629Z-s1-90f1`
(18 generations x 6 proposals, stopped at its cost cap):

- about **$0.14 per swarm proposal**; the engineer's calls (Sonnet) are about $0.10 of that;
- about **94 s per generation** of six concurrent proposals.

That run used the prompts from before the closed-book and scout steps were shown the description
and the incumbent, so current proposals will cost somewhat more. The dry run of
`claim1_coast.json` on 14 Nov estimated:

- swarm: ~$3.38 and ~9 min per run;
- single_agent: ~$2.49 per run, priced at the engineer calls;
- random_mutation: $0;
- about **$12 for the whole matrix**, and ~28 min with `--parallel 2`.

The times for the baselines are upper bounds, borrowed from swarm runs. The dry runs of claims 3
and 4 estimate about $20 each at the default budget (three swarm arms x 2 seeds), because the
keyed arm is skipped without its key. With the key set, add about $7 for that arm's two runs.
Claim 2 has five swarm arms on turbulence, which has no measured run yet, so its dry run reports
the cost as unknown. Its warm arms show as blocked until claim 1's swarm runs have finished.
