# Experiments: matrices of lab runs and their report

The protocol (`planning/03_experiment_protocol.md`) makes four claims. Each claim is tested by a
**matrix**: a set of **arms** (ways of running the lab), each run once per **seed**, all with the
same problem and budget. `lab.experiments.matrix` runs a matrix; `lab.experiments.report` turns
its runs into tables.

```
.venv/bin/python -m lab.experiments.matrix run lab/experiments/configs/claim1_ladder_coast.json --dry-run
.venv/bin/python -m lab.experiments.matrix run lab/experiments/configs/claim1_ladder_coast.json [--parallel 2] [--only researcher_full]
.venv/bin/python -m lab.experiments.report claim1_ladder_coast [--target-rel 0.05] [--compare researcher_full,single_agent]
scripts/run_overnight.sh     # all four matrices in order, each followed by its report
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
{"name": "claim1_ladder_coast", "comment": "...", "problem": "coast_water",
 "budget": {"generations": 6, "proposals": 4, "max_cost_usd": 4.0},
 "target": {"rule": "...", "rel": 0.05},
 "seeds": [1, 2],
 "arms": [{"name": "researcher_full", "mode": "researcher", "memory": "vector", "search": "openalex",
           "search_fallback": ["arxiv"]}, ...]}
```

Arm keys:
- `mode`: `researcher` (the default), `pipeline` (`swarm` is its old name), `single_agent` or
  `random_mutation`. See `docs/swarm.md`.
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
`{"matrix": "claim1_ladder_coast", "arm": "researcher_memory", "seed": "same"}`, meaning that
arm's run with the same seed.

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

## What the first design showed

The first claim 1 matrix (`runs/exp/claim1_coast`, 6 generations x 4 proposals, seeds 1 and 2) put
the first design, now `pipeline`, against two baselines. The pipeline was analyst, closed-book,
scout from keyless scholarly search, and an engineer forced to implement the scouted claim.
Results, by final confirm loss (cm):

| arm | equal proposals (final) | equal dollars ($0.82, the cheapest run) | $ per proposal |
|---|---|---|---|
| pipeline (then "swarm") | 2.999 (2.972 to 3.026) | 3.015 (3.003 to 3.026) | 0.115 |
| single_agent | 2.698 (2.692 to 2.703) | 2.698 (2.692 to 2.703) | 0.038 |
| random_mutation | 3.239 (no change from the seed) | n/a (costs nothing) | 0 |

The single agent had the lower loss on both seeds, on both bases. That is too few seeds to test,
but the direction was clear. Forcing a strong model to implement a claim scouted by a small model
from keyless scholarly search did worse than letting the strong model choose its own change.

So the design changed. The `researcher` mode keeps the single strong call and offers the lab's
two assets, the notebook and the ideas board, as inputs it may use or ignore. The experiments
became a ladder that adds one asset at a time. The old result directories (`runs/exp/claim1_coast`,
`runs/exp/claim2_transfer_turb`) are kept; the new matrices write to new names.

## The ready-made matrices

All four use small budgets: 6 generations x 4 proposals, seeds 1 and 2, a $4 cap per run, and the
fixed target rule (5% below the seed's confirm loss). Raise `generations` and `seeds` for the runs
that go in the pitch. The protocol asks for five seeds for fresh and warm and three for the
shuffled arm, and the report will not test a difference on fewer than five.

| Config | Claim | Arms | Compare |
|---|---|---|---|
| `claim1_ladder_coast.json` | 1: the lab beats simpler uses of the same budget, and each asset pays | `random_mutation`, `single_agent`, `pipeline`, `researcher_plain`, `researcher_memory`, `researcher_search`, `researcher_full` | up the ladder, at equal proposals and at equal dollars |
| `claim4_memory_coast.json` | 4: shared memory pays | researcher, search off: `memory_none`, `memory_full_context`, `memory_vector`, `memory_pastdev` (needs `PAST_API_KEY`) | final loss, minutes to target, dollars, recall latency: reported separately |
| `claim3_search_coast.json` | 3: search finds what the model did not know | researcher, vector memory: `search_off`, `search_openalex`, `search_arxiv`, `search_exa` (needs `EXA_API_KEY`) | origin and board tables; each arm uses one backend, no fallback |
| `claim2_transfer_turb_v2.json` | 2: the lab gets better at research | researcher, vector memory, search off on turbulence: `fresh`, `cheat_sheet`, `warm_notebook`, `warm_shuffled`, `warm`, carrying from claim 1's `researcher_memory` arm | warm against fresh and cheat_sheet; warm_shuffled against warm_notebook |

What each rung of the ladder isolates:
- `single_agent` vs `random_mutation`: what a strong model adds over blind search at the same
  number of evaluations.
- `researcher_plain` vs `single_agent`: the researcher prompt itself. That is the strategy
  suggestion, the header and one repair attempt.
- `researcher_memory` vs `researcher_plain`: the notebook.
- `researcher_search` vs `researcher_plain`: the ideas board.
- `researcher_full`: both assets.
- `pipeline`: the first design, for the record.

`scripts/run_overnight.sh` runs claim 1, claim 4, claim 3, then claim 2, each with `--parallel 2`
and followed by its report. Claim 2's warm arms wait, marked `blocked`, until their claim 1 source
run has finished.

## Reading the report

`report.md` and `report.json` are written to `runs/exp/<matrix>/`. Every table says how many seeds
it rests on. If any planned run is unfinished the report opens with **PARTIAL RESULTS**, and an
arm with no finished run is marked partial. Runs written before `became_incumbent` existed show
"n/a" there; their old follow-up rows are not counted as proposals.

- **Results by arm (equal proposals)**: runs finished of planned; final incumbent loss on select
  and confirm (mean and range across seeds); proposals, accepted (passed the rule), became incumbent
  (adopted); dollars and minutes per run and **per proposal**; evaluations per run.
- **Equal proposals vs equal dollars**: "same budget" means the same number of proposals, which is
  not the same money; a single-agent proposal costs a fraction of a swarm proposal. So the confirm
  loss is shown on two bases side by side:
  - **equal proposals**: the final loss after the full budget of proposals;
  - **equal dollars**: for each run, the incumbent's loss at the last generation whose cumulative
    cost was within the smallest total cost of any paid run in the matrix. The report states that
    amount and the run it came from.

  Arms that cost nothing (random mutation) are left out of the equal-dollars view, with a note.
  Any statement that one arm is better must say on which basis.
- **Time to target**: the target is fixed in advance by the protocol, not taken from the results.
  It is a confirm loss 5% below the starting formula's: `target = seed_confirm x 0.95`, where
  `seed_confirm` is the generation-0 confirm loss in each run's curve.
  - The report checks that `seed_confirm` is the same in every run and prints a warning if it is not.
  - The fraction comes from the config's `"target": {"rel": 0.05}` block (the runner ignores that
    key), or from `--target-rel`.
  - `--target <loss>` overrides the rule, and the report then also prints what the rule would have given.

  For each run, the report gives the proposals, dollars and minutes until the incumbent first
  reached the target. Runs that never reached it are listed as "not reached" (or "not reached
  (yet)" while running) and counted in "k of n reached", never dropped.
- **Where the ideas came from**: per arm and origin (own, notebook, search, unstated for the
  researcher; search or closed_book for the pipeline; single_agent; random_mutation), the number of
  proposals, passed and adopted. For researcher arms with search, a board table gives:
  - ideas offered and used;
  - how many used ideas passed and how many were adopted;
  - the share of passed search-origin changes that the strong model had not listed closed-book
    before seeing the board.
- **Search**, by arm and backend: searches made (real calls, cache hits excluded) and failed,
  claims from search, accepted, the share of accepted search claims that were not already known
  closed-book, and accepted claims per 100 searches. When summaries record cost per role, a second
  table gives accepted search claims per dollar of search-role model cost. That cost is the scout
  and judge calls, which exist only because of search.
- **Memory**: recall calls, mean recall latency, mean characters returned.
- **Paired comparison** (`--compare A,B`, default `warm,fresh` or the first two arms): per seed, A
  minus B in confirm loss at equal proposals and, when both arms cost money, at equal dollars.
  For each basis it counts the seeds in which each arm had the lower loss. With fewer than five
  paired seeds it says "too few seeds to test"; with five or more it gives a two-sided sign test.
  It makes no other significance claim.

## Cost and time to expect

`--dry-run` estimates from runs already on disk for the same problem, preferring finished runs of
the same mode. Measured on coast water, per proposal:

| mode | $ per proposal | time | measured on |
|---|---|---|---|
| researcher (memory + search) | 0.071, of which scouting 0.015 | ~1.3 min per generation of 4 | smoke run `runs/lab/coast_water/20261114T025918Z-s0-4c97`, 2 x 4 proposals |
| single_agent | 0.034 to 0.041 | ~0.4 min per generation of 4 | `runs/exp/claim1_coast/single_agent` |
| pipeline | 0.114 to 0.141 | ~1.3 to 1.6 min per generation | `runs/exp/claim1_coast/swarm`, the long run `...000629Z-s1-90f1` |
| random_mutation | 0 | ~0.15 min per generation | `runs/exp/claim1_coast/random_mutation` |

In the smoke run, the researcher call itself cost about $0.055 per proposal. Scouting costs about
$0.06 per generation (one Sonnet `anticipate` call and three Haiku calls), and it is paid only when
search is on.

The dry runs of 14 Nov estimated:
- `claim1_ladder_coast`: about $22, ~44 min with `--parallel 2`;
- `claim4_memory_coast`: about $10, ~24 min (the past.dev arm skipped without `PAST_API_KEY`);
- `claim3_search_coast`: about $10, ~24 min (the Exa arm skipped without `EXA_API_KEY`);
- `claim2_transfer_turb_v2`: about $8.50, ~14 min. Its warm arms wait for claim 1.

So the full overnight script comes to about $50 and about 1.8 hours with `--parallel 2`.

Every researcher arm is priced from the one measured researcher run, which had memory and search
on. Arms without search should come in about $0.015 per proposal cheaper. The cap is $4 per run.
