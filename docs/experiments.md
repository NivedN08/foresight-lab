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

## What the ladder showed

`runs/exp/claim1_ladder_coast` (6 generations x 4 proposals; all 23 runs finished). It first ran
with seeds 1 and 2 for every arm. Two seeds proved too noisy to rank the arms (see the memory
experiment below), so seeds 3 to 5 were added for the three arms without search.
Confirm loss of the final forecaster, cm; the starting formula scores 3.239.

| arm | seed 1 | seed 2 | seed 3 | seed 4 | seed 5 | mean | $ per run |
|---|---|---|---|---|---|---|---|
| random_mutation | 3.239 | 3.239 | | | | 3.239 | 0 |
| pipeline (the first design) | 3.129 | 3.043 | | | | 3.086 | 2.29 |
| single_agent | 2.679 | 2.693 | 2.729 | 2.979 | 3.006 | 2.817 | 0.87 |
| researcher_plain | 2.820 | 2.713 | 2.968 | 3.028 | 2.902 | 2.886 | 1.13 |
| researcher_memory | 3.116 | 2.636 | 2.658 | 2.806 | 2.426 | 2.729 | 1.32 |
| researcher_search (search failed, see below) | 2.825 | 2.382 | | | | 2.603 | 1.23 |
| researcher_full (search failed, see below) | 2.710 | 2.362 | | | | 2.536 | 1.74 |

Paired by seed over five seeds (`lab.experiments.report claim1_ladder_coast --compare A,B`):

| comparison | lower loss in | mean difference | two-sided sign test |
|---|---|---|---|
| single_agent vs researcher_plain | single agent in 4 of 5 | 0.07 cm | p = 0.375 |
| researcher_memory vs researcher_plain | notebook in 4 of 5 | 0.16 cm | p = 0.375 |
| researcher_memory vs single_agent | notebook in 4 of 5 | 0.09 cm | p = 0.375 |

None of the three is separated from the others. At equal dollars the notebook's edge over the
single agent disappears (2 of 5 seeds).

What this supports, and what it does not:

- Random code changes never passed the decision rule (0 of 48). Every arm with one strong model
  call per proposal ended between 2.36 and 3.12 cm, below the starting formula in all 19 runs.
- The first design, `pipeline`, was again the worst arm that uses a model, at twice the cost.
- **The two search arms did not test search.** OpenAlex refused all 72 calls (its keyless daily
  allowance was used up) and arXiv answered 21 of 72. No scouted idea was offered in
  `researcher_search`; in `researcher_full` 23 were offered, 4 were used and none passed. Those
  arms are in effect two more runs each of `researcher_plain` and `researcher_memory`.
- The spread between seeds (up to 0.7 cm within one arm) is larger than any difference between
  arms. Five seeds do not rank single agent, researcher, or researcher with notebook.
- Ideas the researcher marked as coming from the notebook passed the rule more often than its
  own (19 of 27 against 22 of 65 in the two notebook arms), but fewer of them were adopted per
  pass (6 of 19 against 11 of 22). The 27 notebook-sourced proposals drew on only 12 distinct
  notebook entries per generation: up to all four proposals in a generation picked the same
  entry and wrote the same change. The pass rate counts one idea several times, so it is not
  evidence of better ideas. Proposals in one generation should be made to differ.

The search failure was fixed before the search experiment ran: Crossref was added as a keyless
source, requests are spaced, and a used-up daily allowance is no longer retried (`docs/swarm.md`).

## What the memory experiment showed

`runs/exp/claim4_memory_coast` (researcher mode, search off, seeds 1 and 2; the past.dev arm was
skipped because no key is set). Confirm loss of the final forecaster, cm:

| arm | seed 1 | seed 2 | $ per run | recall time per call |
|---|---|---|---|---|
| memory_none | 3.069 | 3.026 | 1.08 | n/a |
| memory_full_context | 3.047 | 2.819 | 1.36 | under 0.1 ms |
| memory_vector | 2.996 | 3.012 | 1.31 | 1.4 ms |

- Both notebook arms ended lower than no notebook on both seeds, by 0.02 to 0.21 cm. That is
  inside the noise measured below, so it is not a result.
- All six runs reached the target (5% below the starting formula). The notebook did not make
  that faster: 12 to 20 proposals with or without it.
- The notebook costs about 25% more per run, because the prompt is longer.

### How much one run moves by chance

`memory_none` here and `researcher_plain` in the ladder are the same arm with the same seeds, run
twice. So are `memory_vector` and `researcher_memory`. The model is not deterministic, so the two
copies differ:

| setup | seed | first run | second run |
|---|---|---|---|
| researcher, no notebook | 1 | 2.820 | 3.069 |
| researcher, no notebook | 2 | 2.713 | 3.026 |
| researcher, vector notebook | 1 | 3.116 | 2.996 |
| researcher, vector notebook | 2 | 2.636 | 3.012 |

Repeating one arm with one seed moves the final loss by 0.1 to 0.4 cm. Differences between arms
in the ladder and in this experiment are the same size or smaller. Pooling every finished
researcher run so far, counting the two ladder arms whose search failed as runs without search:
no notebook 2.806 cm (6 runs), vector notebook 2.805 cm (6 runs). The ladder was then extended to five seeds for the three arms without search so that
this could be tested rather than eyeballed; the result is in the ladder section above: the
notebook arm is lowest on average (2.73 against 2.82 and 2.89 cm) but not separated.

## What the search experiment showed

`runs/exp/claim3_search_coast` (researcher mode, vector notebook, seeds 1 and 2; one search source
per arm, no fallback; the OpenAlex and Exa arms were skipped because no key is set). This time
search worked: 71 searches, none failed. Confirm loss of the final forecaster, cm:

| arm | seed 1 | seed 2 | $ per run | minutes per run | ideas offered | used | passed | adopted |
|---|---|---|---|---|---|---|---|---|
| search_off | 2.562 | 2.765 | 1.31 | 4.0 | n/a | n/a | n/a | n/a |
| search_crossref | 2.767 | 2.822 | 1.85 | 8.8 | 45 | 8 | 3 | 2 |
| search_arxiv | 2.685 | 2.838 | 1.93 | 8.8 | 41 | 11 | 2 | 1 |

- Search did not lower the final loss. Both search arms ended slightly above the arm without
  search, inside run-to-run noise, at about 45% more cost and twice the time.
- The researcher took an idea from the board in 19 of 96 proposals. Five of those passed the
  rule and three became the forecaster in use.
- Four of the five were the same idea, adding the neighbouring gauges' recent anomalies, which
  the model had already listed closed-book before seeing the board. The sources it credited were
  loose (one was a paper on urban water demand). One passed change, lagged means of the gauge's
  own residual, was not on the closed-book list; it was not adopted.
- So with keyless scholarly search, on this problem and budget, search found nothing useful that
  the model did not already know. The Exa arm is the open question and needs a key.

## The test window, scored once

Which forecasters got the one look was fixed in the protocol before the look: the final forecaster
of the finished coast run with the lowest confirm loss across the ladder, memory and search
experiments (`researcher_full` seed 2, 2.362 cm), and the two forecasters issuing live sealed
forecasts (`lab-a`, `lab-b`, from the first long run). Test window: 8,000 cases, 322 station-days,
Oct 26 to Nov 13. Mean absolute error in cm with 95% intervals that resample station-days.

| forecaster | all leads | 1 h | 3 h | 6 h | 12 h | 24 h |
|---|---|---|---|---|---|---|
| official tide table | 32.66 [31.39, 33.86] | 32.86 | 32.50 | 32.89 | 32.38 | 32.65 |
| "the current gap continues" | 4.54 [4.29, 4.80] | 2.09 | 4.03 | 5.35 | 5.62 | 5.77 |
| damped version | 4.45 [4.16, 4.76] | 2.10 | 4.07 | 5.12 | 5.41 | 5.71 |
| hand-written starting formula | 3.92 [3.71, 4.13] | 2.11 | 3.96 | 4.65 | 4.71 | 4.23 |
| live forecaster lab-a | 3.42 [3.21, 3.62] | 1.81 | 3.40 | 4.08 | 3.76 | 4.12 |
| live forecaster lab-b | 3.44 [3.22, 3.64] | 1.81 | 3.40 | 4.17 | 3.76 | 4.12 |
| lab, best run by confirm loss | 3.17 [2.97, 3.39] | 1.71 | 2.78 | 3.53 | 3.84 | 4.12 |

- Paired with the best simple rule (damped) on the same cases: the lab's best forecaster is
  1.28 cm lower [1.06, 1.50]; lab-a 1.03 lower [0.84, 1.24]; lab-b 1.02 lower [0.82, 1.23]; the
  starting formula 0.54 lower [0.38, 0.71].
- Against the starting formula the lab's best forecaster is 0.75 cm (19%) lower and the intervals
  do not overlap. No paired interval exists for that comparison: see the next point.
- Two looks, not one. The scorer's first look left the starting formula out of its baselines, so
  it was scored in a second logged look three minutes later (`runs/lab/final_test/log.jsonl`).
  It is a reference, not a selected forecaster, and nothing was chosen after seeing it. The
  scorer now always includes it and reports the paired difference.
- The gain is at 1 to 12 hours. At 24 hours the lab's forecasters and the starting formula are
  within each other's intervals (4.12 against 4.23 cm).
- The live forecasters lab-a and lab-b give the same error at four of five leads: the eight
  generations between them changed little outside the window they were selected on.

## The ready-made matrices

All four use small budgets: 6 generations x 4 proposals, seeds 1 and 2, a $4 cap per run, and the
fixed target rule (5% below the seed's confirm loss). Raise `generations` and `seeds` for the runs
that go in the pitch. The protocol asks for five seeds for fresh and warm and three for the
shuffled arm, and the report will not test a difference on fewer than five.

| Config | Claim | Arms | Compare |
|---|---|---|---|
| `claim1_ladder_coast.json` | 1: the lab beats simpler uses of the same budget, and each asset pays | `random_mutation`, `single_agent`, `pipeline`, `researcher_plain`, `researcher_memory`, `researcher_search`, `researcher_full` | up the ladder, at equal proposals and at equal dollars |
| `claim4_memory_coast.json` | 4: shared memory pays | researcher, search off: `memory_none`, `memory_full_context`, `memory_vector`, `memory_pastdev` (needs `PAST_API_KEY`) | final loss, minutes to target, dollars, recall latency: reported separately |
| `claim3_search_coast.json` | 3: search finds what the model did not know | researcher, vector memory: `search_off`, `search_crossref`, `search_arxiv`, `search_openalex` (needs `OPENALEX_API_KEY`), `search_exa` (needs `EXA_API_KEY`) | origin and board tables; each arm uses one backend, no fallback |
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
