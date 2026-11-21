# The Revolutionaries, Track 1: what they built and how they beat records

Source: `github.com/ArthurAnache/Hackathon-AI-Reasearch`, read from a local clone on 2026-11-15. Paths below are
relative to the repo root. When a file lives on a branch other than `main`, the branch is given as `branch:path`.
I ran no code from the repo. Everything here comes from reading files and git history. Inferences are marked.

## One-paragraph summary

The Revolutionaries, a team of four, entered Track 1. They built a lot of verification and orchestration around
several search loops. They headline two results. First, a Tao constant 1d upper bound of **1.4540711000681048**,
6.0e-4 below the best open claim in Tao's repository (1.4546733294, pull request #371). A small autonomous loop
(`autoloop`) with a deterministic rules policy found it, starting from a memory of 239 solutions the team had
already made. Second, an EinsteinArena third-autocorrelation candidate of **1.4490529129656413**, 5.69 δ below the
live #1. A "problem scout" found it by block-averaging the public #1 to a coarse grid, repeating it back, and
re-annealing. Neither result was submitted. Their main build, a genetic loop called Knockout Evolution, did not beat
anything: its best gain was 0.6 % of the threshold, and a plain hill-climbing control arm did slightly better. What
made the team stand out was rigour and honesty more than margin. They had frozen, hash-checked copies of the live
verifiers, a second exact-rational verifier for every problem, one arbiter script that issues every verdict, a gated
submit script (it blocked a teammate's real submit attempt), replayable ledgers, and a README that labels every
number and lists its failures.

## Team, track and challenges entered

- **Team:** "The Revolutionaries", 4 people (`CLAUDE.md`: "Team of 4"). Commit authors: Arthur (73 commits plus 2
  GitHub merges), mariefleurdelort (32), murilo-lm (20), Thibault Verny (7). The README and the briefs refer to them
  as Arthur, Marie, Murilo and Thibault.
- **Track:** Track 1, autonomous research loop that beats a known mathematical record (`README.md`, line 3).
- **Workstreams** (`CLAUDE.md`): WS1 problem selection and frozen verifier (Arthur); WS2 research loop (Arthur's
  Knockout Evolution, Marie's evolution loop and past.dev memory); WS3 optimizers and Modal (Thibault); WS4 cloud and
  integrations (Murilo, who also built `autoloop` and the Tao 1d results).
- **Sponsor challenges:** Exa (literature check, `selection/literature_check.py`), past.dev (shared memory,
  `knockout/pastdev.py`, plus a "past.dev" README section on branch `docs/readme-pastdev`), Modal (all campaigns).
  The repo does not say which prizes they formally applied for.

## The problems and the records

Track 1 allowed three sources of records: EinsteinArena, Tao's table, and AlphaEvolve. The team used two of them.

**Tao constant 1d (signed autocorrelation).** Pick a step function f with N equal cells, allowed to go negative.
Minimise max|f∗f| / (∫f)². For integer cell heights v this is `2N · max_k |(v∗v)_k| / (Σv)²`
(`ws4-research-map:records/1d/README.md`). Lower is better. Any value is an upper bound on the constant. The
reference is the best open, unmerged pull request on `teorth/optimizationproblems`: 1.4546733414 (PR #345) at claim
time, then 1.4546733294 (PR #371). The table on Tao's page was higher: 1.457854884921989 by README, and 1.4554649 as
parsed by the loop's reference reader on 14 Nov 22:25 PT (`ws4-research-map:autoloop/runs/C_tournament/rules/ledger.jsonl`).
Verifiers: `records/1d/highres/c1d_exact.py` (Kronecker substitution, a direct sum at the deciding point, a check
modulo a 61-bit prime) and `records/1d/highres/check_independent.py` (written separately, exact `Fraction`).

**EinsteinArena third-autocorrelation (constant C′).** Minimise |max(f∗f)| / (∫f)² over signed step functions, with
at most 100,000 values and δ = 1e-5. The `abs` sits outside the `max`, so this is a different constant from Tao's
1d. The team checked this: the EinsteinArena #1 scores 4.6127 under Tao's definition (`README1.md` §7.2). The record
was the live #1, 1.449109789331911 (Atris, solution 2617). MILO (arXiv:2609.38349) claims 1.4488860143 but has not
published the construction. Verifier: the pinned EinsteinArena `verifier_pinned.py` (numpy `np.convolve`), plus the
exact-rational `selection/scout_third_autocorr_exact.py` and
`ws1/freeze-third-autocorrelation:problems/third-autocorrelation-inequality/independent.py`.

**Second-autocorrelation (the main problem they chose, EinsteinArena).** Maximise ‖f∗f‖₂² / (‖f∗f‖₁ · ‖f∗f‖∞) for
f ≥ 0, with up to 2,000,000 values and δ = 1e-5. The live #1 moved during the event: 0.963638668 (solution 2782) when
the problem was frozen, then 0.9636615620620852 (solution 2783, MLAI_YONSEI) from 14 Nov 22:41 UTC
(`knockout/README.md`, "Open decisions").

**Circles in a rectangle (backup).** Place 21 circles in a rectangle of perimeter 4 and maximise the sum of radii.
δ = 1e-10. Record 2.3658323759098576 (ExoMind-TTS). The forum describes it as a certified KKT point
(`selection/digests/circles-rectangle.md`).

**Others tried:** ring-loading-15 (exact rationals, record 9/8, δ = 0) and Tao 1a (first autocorrelation).

## What they built, mechanism in detail

The work spans five separate search systems, all wrapped by one shared verification layer. I describe the layer
first, because every result passes through it.

### 1. Problem selection (WS1, `selection/`)

In plain words, they decided where to dig before digging. They screened all 34 EinsteinArena problems in four
stages, each with a script and a JSONL registry:

1. `stage1_filter.py`: drop Lean-proof, exactly-known and stochastic-verifier problems. Mark integer records as
   `moonshot_only`. Liveness comes from the live API, because the source `hidden` flag "turned out to be wrong in
   both directions" (`README1.md` §4). Result: 14 kept, 4 reserve, 13 moonshot, 3 eliminated.
2. `stage2_profile.py`: extract each verifier from the pinned EinsteinArena commit `5baf636`, check it is
   byte-identical to the live one, and reproduce the #1 score in a clean process (within 8e-16). Also time one
   evaluation and record δ and the gap between #1 and #2.
3. `stage3_probe.py`: run 10 minutes of generic multi-scale local search from each #1, on 9 local processes. No
   problem reached δ. The best was 0.58 % of δ.
4. `decision.md`: score the problems with weights fixed in advance (margin 30 %, cost 20 %, verifier 15 %, baseline
   15 %, code 10 %, activity 10 %). The 0–5 rating rules were written after the data, and the file says so. The
   result was a tie, 3.589 vs 3.585. Second-autocorrelation became the main problem.

Third-autocorrelation scored lowest of the scored problems at this stage (2.18). Yet it later produced the
EinsteinArena candidate. The selection method did not predict where the room was.

### 2. Frozen verifiers and the arbiter (WS1, `problems/`, `selection/verify_claim.py`)

In plain words, the team made it impossible for a search loop to fool itself about a score.

- `problems/<slug>/` holds the pinned verifier, the #1 solution as a gzipped certificate, an independent verifier,
  `problem.py` (the API and the server's zod schema), and `meta.json` with sha256 values.
  `problems/_frozen.py` refuses to load a changed file (`FrozenFileChanged`).
  `tests/test_ws1_frozen_problems.py` checks every hash listed in `problems/MANIFEST.json`.
- The independent verifiers share no code with the pinned ones. For second-autocorrelation: float64 values become
  integers over a common power of two, the convolution is exact (Kronecker substitution squared with libmpdec), and
  the integrals have a closed form. It takes 4.5 s and about 1 GB for 2M values. For circles: exact `Fraction`
  checks. For ring loading: integer enumeration.
- The tests include known exploits from the forum: overlap slack of 2^-31, perimeter excess of 2^-31, and 21
  circles at 2^50+1, where float arithmetic yields 2.625. Both verifiers reject all three (`README1.md` §11.2).
- `selection/verify_claim.py <slug> <candidate>` checks the frozen hashes, runs the pinned verifier in a clean
  process, runs the independent verifier, re-reads the live #1 and δ, and appends one verdict to
  `selection/claims.jsonl`. There are four verdicts: `invalid`, `verifier_disagreement`, `valid_below_record` and
  `beats_record`. Only the last counts. It needs gain ≥ δ, gain > 0 and gain > tolerance for both verifiers. Ties
  never win, which mirrors the server rule the team read in `web/src/lib/evaluate.ts`.
- `integrations/submit.py` repeats these checks as gates (a) to (e) before sending anything. It re-reads the
  leaderboard right before the send, caps submissions at 2M values, allows at most 10 sends per 30 minutes, and
  needs a hand-typed `--confirm`.

### 3. Knockout Evolution v1 (WS2, `knockout/`, main branch)

In plain words: an LLM rewrites an optimizer program, a sandbox runs it, and the frozen verifier scores what it
outputs. A "gene" is a one-line hypothesis about why a program improved. A gene gains influence only if a
controlled with/without experiment shows a gain.

Step by step (`knockout/README.md`, "Both arms"):

1. **Two arms on one budget.** The control arm (`control.py`, "Hill Sampling") gets a fixed 20 % of the spend: the
   LLM edits one incumbent program with four operators (`refine`, `restructure`, `reformulate`, `fresh`). The best
   edit is kept if it beats a replay of the incumbent by more than `eps_rel`. The main arm (`main_arm.py`, 1,361
   lines) breeds a population across "islands".
2. **Each main-arm batch** works like this. A Thompson bandit picks the island. More bandits pick the operator and
   the model. Parents are drawn by rank. The genome is a Thompson draw per validated gene, plus up to five refuted
   genes shown as "do not retry".
3. **Knock-out gate** (`decisions.gate_verdict`). Two children share parent, operator, model and base genome; one
   prompt has the gene and one does not. After at least three pairs, the gene is validated if P(θ > 0.5) under
   Beta(1+wins, 1+losses) is at least 0.90. It is refuted if that is at most 0.10. After 8 pairs with no verdict it
   is retired.
4. **Credit.** A child scores 1 if it beats its parent from the same warm start. Gene posteriors decay with γ, and
   ancestors get credit at weight 0.5^depth.
5. **Gene birth.** One reviewer call per improving child returns 0–2 candidate genes from the diff.
6. **Strategist** (`meta.py`). When progress stalls, one LLM call proposes a config patch. Only allow-listed keys
   pass, values are range-checked, and the patch is reverted if the next window does worse. The same file holds an
   Exa "scout" that turns search results into up to five literature genes.
7. **Safety.** LLM-written programs run in `sandbox.py` (timeouts, rlimits, an empty environment, no network). On
   Modal they run in `evaluate_job` containers with `block_network=True` and no secrets. Every candidate is
   re-scored in float64 by the frozen verifier in the trusted process (`context.py`).
8. **Promotion gate.** A score at or above record + margin goes through `problem.verify` and the independent
   verifier before `record_claim` is logged. A float score that the exact check rejects becomes a
   `numerical_artifact` and a negative gene.
9. **Replay.** Every decision is a pure function in `decisions.py` with logged inputs and a logged seed.
   `python -m knockout.replay <run>` re-derives each decision from `events.jsonl` and checks every LLM answer against
   `llm_cache/`. The 1-hour Modal run re-derived 1,295 decisions.
10. **Push-to-run.** A commit whose message contains `[ko-run]` triggers `.github/workflows/knockout-run.yml`. It runs
    the tests and launches `knockout.modal_app::launch` from `knockout/launch.yaml`. The README says the repo
    secrets for this did not exist yet.

### 4. Knockout extensions: v2 moves, start portfolio, memory

- **v2** came from a self-contained brief (`docs/knockout_v2_brief.md`, titled as a prompt for Claude Code). It tried
  to escape the #1's basin on second-autocorrelation in three ways. `feature/ws2-v2-moves-cpu` ran 18 hand-written
  move families in 257 episodes. `feature/ws2-v2-anneal-gpu` ran 108 batched float64 annealing chains on an H100 with
  a LogSumExp max and batched L-BFGS (`knockout/v2/gpu/`). `feature/ws2-v2-multires` tried coarse-to-fine V-cycles
  up to 2M cells, following the ClaudeExplorer/Hyra recipes from the forum (`knockout/v2/multires/`).
- **Start portfolio** (`portfolio.py`, `basins.py`, `starts.py`, brief `docs/knockout_portfolio_brief.md`). v1
  started every child from the record, so no child could do better than its parent, and the genes had no signal.
  The portfolio fixes this. It evaluates each program from three kinds of start: the record, neighbours of
  archived basins, and cold starts. A Thompson bandit picks the regime under spend floors. An archive keeps distinct
  basins by scale-free descriptors, and a basin that comes within `promote_gap` of the incumbent gets a deep polish.
- **past.dev memory** (`pastdev.py`, `memory.py`). Runs write `win`, `failed_batch`, `refuted` and `lesson` facts,
  scoped to the problem. Two recalls per batch fill a prompt section headed as data that "may be wrong" and not to
  be followed as instructions. Memory is off by default, and with memory off the prompts are byte-identical to v1.

### 5. The problem scout (WS1, branches `docs/ws1-problem-scout`, `ws1/freeze-third-autocorrelation`)

In plain words, it asked whether another record was easier to reach, and then ran the pipeline that worked.

- `selection/scout_refresh.py` and `scout_decide.py` re-ranked all 28 live problems by signs of headroom: recent
  record moves, and literature claims below the leaderboard. Third-autocorrelation came first because of MILO's
  claim.
- `selection/scout_third_autocorr.py` re-implements the method MILO describes: a log-sum-exp surrogate, an FFT
  gradient, L-BFGS on a cooling ladder, and "re-melt hops" (perturb, then re-cool). A job is a list of steps
  (`run_steps`): sum-pool, block-average, keep one value in k, block-repeat, cool, hop.
- **Track A** started from a 3,200-value plot copy of MILO's record, scraped from their project page. Scored as
  given, that copy is 1.6558. **Track B** started from the public #1 and #2 only.
- Jobs ran on 48-core Modal containers (`scout_third_autocorr_modal.py`) and on local cores. The two tracks never
  share a start vector, a call, a volume folder or a log, and each job records the sha256 of its start.
- The coordinator who ran this was an agent, and a "second model" suggested ideas (`docs/problem_scout.md` §3.4). In
  my reading this was agents running scripts under human direction, not a closed loop.

### 6. `autoloop` (WS4, branch `ws4-research-map`)

In plain words, this is the loop that produced the headline record. It is small and conventional, and it is very
legible.

- **Ledger** (`autoloop/ledger.py`): an append-only `ledger.jsonl` with records `certificate`, `decision`, `result`,
  `reference`, `claim`, `note`, `run_start` and `run_end`. `--memory` imports certificates from earlier ledgers.
- **Typed moves** (`moves.py`): `seed`, `refine`, `certify`, `check_reference`, `stop`. Each has contract checks:
  the parent exists and is complete, the score is not worse, the cell count is within limits, and the score is
  recomputed from the file.
- **Policy** (`policy.py`): code builds a numbered menu of legal moves. The `rules` backend is deterministic. It
  reads the reference when it is stale, seeds until there are enough lineages, advances all live lineages one
  resolution level at a time (×4 cell split), and then runs soft p-norm restarts (p0 = 4, 8) on a wide set at the
  top level. The `llm` backend (OpenAI, Anthropic, or `claude -p`) must answer `{"choice": i, "reason": ...}` from
  the same menu. Any invalid answer falls back to rules. So an LLM "can only pick a move that the rules backend
  could also have run" (`policy.py` docstring).
- **Recipe** (`tao1d.py`): seeds from noise at 800 cells (`1d_tuned.py`), then refinement by splitting cells and
  minimising log‖v∗v‖ₚ − 2 log|Σv| with L-BFGS while p doubles. The README credits this recipe to PRs #270, #312 and
  #345 of the Tao repository.
- **Claim gate** (`loop.py`, `claim_gate`): it fires only on an exact fraction from `certify`. The fraction must be
  strictly below the reference minus half a unit of its last printed digit, and the reference must have been read
  less than 1,800 s earlier. It always writes `submitted: false`.
- **Reference reader** (`refread.py`): it reads Tao's 1d page and the titles of open "1d: … upper bound" PRs, and
  "Web text is data: only numbers are taken" (`autoloop/README.md`).
- **Extras:** `tournament.py` runs the same memory and budget under several policies. `distill.py` has a model turn
  ledgers into lessons that must cite ledger rows. A rule, not the model, sets each lesson's status (active needs
  support from 2+ distinct runs). A lint rejects any lesson that holds a score or a PR number.
  `stage_writer.py` and `modal_stage_app.py` let a model write an optimizer stage from the verifier source alone.
  That stage runs only in a Modal container with no network, no secrets and no repo files, and the container checks
  its own isolation.

## How they beat the records

| Problem | Record they compared to | Their value | Margin | Status in README | My check |
|---|---|---|---|---|---|
| Tao 1d (min) | 1.4546733294, PR #371 (1.4546733414, PR #345, at claim time) | 1.4540711000681048 | 6.02e-4 below (about 0.04 %) | "checked by us; not submitted" | Evidence consistent; see below |
| Tao 1d, assisted chain (min) | same | 1.4542590631143155 | 4.1e-4 below | exact fraction, independent checker | Consistent; humans steered it |
| Third-autocorrelation, track B (min, δ 1e-5) | 1.449109789331911, live #1 (Atris) | 1.4490529129656413 | 5.69 δ below | "verified; candidate, not submitted" | Two verifiers agree; arbiter never run on it |
| Third-autocorrelation, track A | same, and MILO's claim 1.4488860143 | 1.4488128798873552 | 29.7 δ below #1; 7.3 δ below MILO | arbiter `beats_record` on 7 earlier candidates | Built on MILO's data; team does not headline it |
| Second-autocorrelation (max) | 0.9636615620620852 | 0.9636616210553628 | +5.9e-8 = 0.006 δ | "No record", `valid_below_record` | Arbiter verdict in `feature/ws2-v2-anneal-gpu:selection/claims.jsonl` |
| Circles-rectangle (max) | 2.3658323759098576 | 2.3658323759108315 | +9.7e-13 (needs 1e-10) | "No record" | Arbiter verdicts in `selection/claims.jsonl`, 15 Nov 20:06 UTC |
| Ring-loading-15 | 9/8 | 9/8 | 0 | no gain | — |
| Tao 1a (min) | 1.5026648336 | 1.506687890866 | 4.0e-3 short | not passed | `autoloop/RESULTS.md` |

### My check of the Tao 1d evidence

- **The solution file exists.** `ws4-research-map:records/1d/autonomous/c006_31bbf628_int56.json` is a single
  `values` array of integers (841,678 bytes). The README says 51,200 cells with max |v| = 2^56.
- **The claim record matches.** `records/1d/autonomous/CLAIM_PASSED.json` gives the exact fraction
  21267647941510998263770007610303168512 / 14626277862557668614837367632277156521 = 1.4540711000681048. It records
  the reference as 1.4546733414 from PR #345, read 7 s before the claim, with threshold 1.45467334135,
  `submitted: false`. The timestamp is 14 Nov 23:06:40 PT, inside the event.
- **The ledger is coherent.** `autoloop/runs/C_tournament/rules/ledger.jsonl` shows 7 decisions:
  `check_reference` → `refine` (7,200 CPU-s) → `certify` (first claim, 1.4541470) → `refine` → `check_reference` →
  `certify` (1.4540711) → `refine`. The run ended on the 5,400 s wall-clock cap. Each decision lists its menu and its
  reason. Modal cost was 0.47 USD.
- **The verifier logic is sound by reading.** `check_independent.py` computes the full autoconvolution of the
  integer vector exactly. It asserts that Σc = (Σv)², checks the deciding point against a direct sum, spot-checks
  other points, and returns `Fraction(2n·|c_k|, S²)`. For a step function, f∗f is piecewise linear between grid
  points, so its maximum sits on one of them. The formula is therefore the right discretisation. I did not run it.
- **What weakens the "autonomous" headline.** The memory held 239 certificates (`autoloop/runs/C_memory/ledger.jsonl`
  has 239 certificate lines; `RESULTS.md` says 235). Its best was 1.4542590631. That value was already 4.1e-4 below
  the reference, and it came from a chain where "every decision … was made by Claude Code agents under human
  supervision" (`records/1d/README.md`). The loop's own contribution was a further 1.9e-4. Four cold starts with
  empty memory reached only 1.4557078. The team states all of this openly.
- **The winning policy was hand-written rules, not an LLM.** In the same tournament, Claude as policy reached
  1.4541046 and gpt-6.1-sol reached 1.4541163 with no claim, "because it never chose to read the reference".
- **"Record" means best open claim.** PRs #345 and #371 are unmerged claims, not accepted values. "Not submitted"
  means no PR to Tao's repository, so no third party has replayed the certificate, and any entry "would carry an
  asterisk until a second party replays it" (`records/1d/README.md`). There is no arbiter pack for 1d, so the team
  arbiter never judged it. The label "checked by us" is correct.

### My check of the third-autocorrelation evidence

- **The files exist.** `docs/ws1-problem-scout:selection/scout_candidates/third-autocorrelation-inequality/MANIFEST.json`
  lists `trackB_r4_B035_longrepeat2` with 51,200 values (under the 100,000 schema limit). The pinned verifier gives
  1.4490529129656413 and the exact scorer 1.4490529129656424, a difference of 1.1e-15. The gap below the live #1 is
  5.69 δ, and the start sha256 is recorded. The MANIFEST's own status line reads "not arbitrated by
  selection/verify_claim.py".
- **The arbiter never saw track B.** I read every committed `selection/claims.jsonl`. The only third-autocorrelation
  verdicts are on `ws1/freeze-third-autocorrelation` (15 Nov 05:57 UTC): `beats_record` for the seven early track A
  candidates. The track B best was produced at 06:35 UTC. README's "verified" therefore means "two verifiers
  agree", which is the README's own definition. It does not mean "passed the arbiter".
- **The live #1 was last read at 06:20 UTC on 15 Nov.** I found no later read in the committed scout logs. The
  scout itself notes the record moved by 1.2 δ and 169 δ in October, so the 5.69 δ lead may not have survived to
  judging.
- **The team's own rule cuts against it.** WS1's rule is that the record to beat is the best known value. Track B is
  16.7 δ above MILO's claimed 1.4488860, and the scout says track B "would top the leaderboard, not the literature".
  Track A clears MILO's claim, but it starts from MILO's published copy, and Arthur never ruled on whether that
  counts.
- **"Not submitted"** here means the EinsteinArena server never scored the file and it never appeared on the
  leaderboard. The server's numpy version is not pinned. The margin (5.7e-5) is far above float error, so a server
  score would very likely agree. That is my inference.

### The submit gate worked on a real attempt

On `feature/savant-v3-structural`, commit `e8473f2` (15 Nov 12:59 PT), Thibault ran `integrations/submit.py` with
`--confirm` on a second-autocorrelation file scoring 0.9636616152231015. Gate (c) blocked it because the gain
5.316e-8 was below δ, and the attempt is logged in `selection/submissions.jsonl` on that branch. The safety system
held against a teammate in practice, not only in tests.

## Experiments, ablations and failures

- **Genes vs control (Knockout v1).** In the 1-hour Modal run (442 experiments, 16.8 USD), the control arm finished
  ahead (+5.27e-8 vs +5.19e-8) at 3.0 USD against 13.8 USD, and it led at every equal spend
  (`knockout/README.md`, Results). The ledger diagnosis: 284 children, 13 beat their parent, and the median gain
  over a replay of the parent was 0. `fresh` children won 0 of 84. Islands opened by `reformulate` produced 160
  children and no win. The gate tested only 2 of 19 genes and validated none.
- **Start portfolio (circles-rectangle, two models, 55 min).** From the record, 0 of 92 children improved. From
  neighbour and cold starts, 34–57 % did, and the regime bandit learned to favour cold starts. Every basin climbed
  back to the same value, which suggests (by the team's reading) that the certificate is the global optimum. No
  gene was validated. The astra run was cut at 25 min by a Modal worker pre-emption.
- **Memory A/B (toy, 3 seed pairs, equal budget).** Children beating their reference: 5/16 with memory, 3/12
  without. The team calls this noise and points out that the pair with the most memory did worse. The measurable
  effect was cost: +0.05–0.09 USD per run.
- **v2 on second-autocorrelation.** Every method stops between +5.2e-8 and +5.9e-8. Smoothed objectives with β from
  1e6 to 1e10 are already stationary at the leader's solutions. The GPU chains (108 of them) did best, at +5.90e-8.
- **Basin screen on third-autocorrelation.** The README reports 957 episodes and 0 improving, with LLM-written
  moves no better than random (p = 0.82) and 79 % of LLM moves returning the parent almost unchanged. **That branch
  (`feature/ws2-v2-basin-screen`) is not on the remote**, so I could not check these numbers.
- **Research-map loop vs older loop (1d, 800 cells).** It won 85 of 100 paired seeds against the older loop and 86 of
  100 against one long tuned run (`records/1d/comparison_modal_100seeds.csv`). The best single values were level,
  within 3e-4. The map improves the typical run, not the best one, and the team says so.
- **Scout lessons.** Polishing the #1 on its own grid gained 0 δ. A coarser copy did better: 1,600 values beat 3,200
  and 6,400, though only 3 jobs ran at 1,600. Keeping one value in 8, instead of averaging, failed by 33 δ. On track
  A the first cooling path delivered 28 of the 29.7 δ.
- **Operational failures, all logged.** A Modal image layout bug and a 2 MiB return limit hit a worker with no
  network (fixed by returning 1 MiB chunks). 48 threads per process made 4 scout rounds run about 250× slow and
  wasted about 5.4 USD. Every 1d Modal call of round 3 was cancelled by an external signal. past.dev rejected
  `occurredFrom` with HTTP 400. A test run wrote about 57 test facts to the live past.dev project.

## How they used Exa, past.dev and Modal

**Exa: peripheral.** `integrations/exa_client.py` wraps `exa-py` and logs every call with "the decision it served"
to `selection/exa_log.jsonl`. The log has 31 calls. 25 are six distinct literature-check queries (three for
ring-loading, three for third-autocorrelation), each run about four times. The other 6 are Knockout scout calls on
a toy problem and the main problem. Exa found the
MILO claim that steered the scout. Search results become candidate values only, which a human curates with URL and
quote (`selection/literature_check.py`). The Knockout Exa scout (`knockout/meta.py`) is skipped on Modal because the
key is not passed there. Branch `ws4-research-map` adds `integrations/evidence.py` (Exa only on first sight of a
problem and on plateaus), multi-key failover and `sponsor_metrics.py`. Verdict: real but not central; the main loop
does not depend on it.

**past.dev: built carefully, effect not shown.** `knockout/pastdev.py` uses the Memory API (`POST /ingest`,
`GET /ingest/{id}`, `POST /recall`) and has a local store with the same interface. Facts are scoped per problem,
memory is labelled as data in the prompt, and the key goes only to the orchestrator. Every call is logged, and
replay still passes. Verified checks: cross-run recall (4 of 4 recalls returned the earlier run's facts), 84
facts backfilled from v1 ledgers with original timestamps (`memory_backfill.py`), and a main-problem run whose
prompts carried 40 facts from other runs. Total use was 238 credits. `autoloop` also mirrors its distilled lessons
to past.dev scopes, but the record run did not read past.dev (`--pastdev` is off by default). The README section the
sponsor asked for exists only on `docs/readme-pastdev` (commit `d4d45ae`, 13:55 PT), not on `main`. Verdict: the
cleanest integration of the three, but bolted onto a loop that produced no record.

**Modal: central, and used in several new ways.**
- Knockout: an orchestrator holds the only secret, and workers run untrusted LLM code with `block_network=True` and
  `unshare -rn`, returning results in chunks so the network can stay blocked (`knockout/modal_app.py`,
  `executor.py`). Runs launch from a git push tag via GitHub Actions.
- Isolated model-written stages: no repo files, no volume, no secret, and a self-check inside the container
  (`ws4-research-map:autoloop/modal_stage_app.py`).
- 100 containers × 2 CPUs for the 1d seed sweeps (`records/1d/modal_seeds.py`), and a hybrid executor that sends 1d
  jobs above 12,800 cells to Modal.
- H100 float64 annealing (`knockout/v2/gpu/modal_app.py`), with every GPU score re-computed on CPU.
- 48-core scout containers.

The rule "the GPU searches, the CPU scores" (`CLAUDE.md`, WS3) is the reusable idea. A Modal prize would most
plausibly rest on the sandboxing of LLM-written code. That is my inference.

## Autonomy and self-improvement

- **What ran unattended:** the autoloop tournament (90 min, 3 arms, 14 Nov 22:25 to 15 Nov 00:06 PT), the cold runs
  overnight (until 08:11 PT), and the Knockout campaigns (1 h each). Each started from a human command with caps on
  wall clock, decisions, compute USD and LLM USD.
- **What humans did:** they chose problems (Arthur, by a written rule), wrote design briefs as prompts for coding
  agents (`docs/knockout_*_brief.md`), approved spend caps ("Arthur's go-ahead: up to 100 USD of OpenAI and 350 EUR of
  Modal"), and ran the 1d high-resolution chain that seeded the memory. The scout's rounds were planned by a
  coordinator agent at a person's request.
- **Memory of past solutions:** this is the decisive ingredient for 1d. The README says it plainly: "Our 1d result
  needs a memory of earlier solutions". `autoloop` imports certificates as lineages and the policy continues them.
- **Learning from its own results:**
  - Knockout's bandits and gene posteriors update online, but they had no signal on the main problem.
  - The portfolio bandit did learn to prefer cold starts.
  - `autoloop/distill.py` writes lessons with cited ledger rows and a rule-based status. One prospective test (cold4,
    lesson G11m) ended worse, so the lesson stayed a "hint".
  - The `rules` policy encodes one lesson from the hand-run chain (do not cut lineages before the soft restarts) as
    code, labelled "E12".
- **Net:** the loop that beat the record learns across runs only through imported certificates and hand-encoded
  rules. The loop that learns most elaborately (Knockout) did not beat anything.

## Presentation and judge-facing craft

- **Status labels on every number.** `README.md` defines **verified** ("reproduced by us with two independent
  verifiers"), **checked by us** and **claim**. `README1.md` and `knockout/README.md` use verified / hypothesis /
  limitation. MILO's value is always marked "claim, not reproduced".
- **Results first, failures in full.** "Results at a glance" opens with a six-row table that includes the two
  misses. A section titled "What failed, and what we learned" lists seven items. The README admits the main build's
  weakness on its first screen: "the plain control arm ended slightly ahead of the gene arm".
- **A "What this is not" box for each record** (`records/1d/README.md`): not from scratch, not submitted, not seen
  by the arbiter. This is a technique worth copying. It pre-empts the judge's first objection.
- **A branch map** ("Where everything is") points to 8 branch groups. Three named branches were never pushed:
  `feature/ws2-v2-basin-screen`, `feature/ws2-lab-agents` and `feature/ws2-start-portfolio` (the last survives as
  `genetic-method-start-portfolio`). So the basin-screen numbers and "the lab" cannot be checked from the repo.
- **Logs a judge can follow.** `autoloop.report` prints the decision table. Each autoloop decision stores its full
  menu and a reason sentence. `knockout.replay` re-derives each decision and exits 1 on the first mismatch. Run
  ledgers sit in `knockout/results/` (5 runs, about 2,440 files: experiments, events, LLM cache, programs, reports).
- **Figures** for the demo video: `docs/figures/knockout_progress_1h.png` and `knockout_progress_all_runs.png`. The
  README warns that "The y axis is zoomed".
- **Provenance honesty.** They keep track A (MILO-derived) and track B apart and headline only B. They credit the 1d
  refinement recipe to the Tao PRs: "the code and the starting solutions are ours".
- **Small inconsistencies:** 239 vs 235 certificates in memory; README headlines PR #371 while the claim file
  records #345; the final README revision is on `docs/readme-pastdev` (14:25 PT), not `main` (13:49 PT).

## Why it stood out, scored against the five criteria and the Track 1 criteria

| Criterion | Assessment |
|---|---|
| Research rigour | Strongest point. Frozen, hash-checked verifiers byte-identical to live; exact second verifiers; exploit tests; one arbiter with 4 verdicts; pre-registered selection weights with an admitted post-hoc rating rule; paired seeds (85/100) |
| Novelty | Knockout's gene knock-out gate is a new idea but did not work. Coarsen-and-repeat comes from MILO and the forum. The 1d recipe comes from the Tao PRs. Novelty sits mostly in the verification and sandboxing design |
| Results | Two records against a baseline, both modest and unsubmitted: 6.0e-4 below an open claim on 1d, and 5.69 δ on third-autocorrelation. Margins were measured against live references read minutes before |
| Autonomy and self-improvement | A loop with no human in it produced the 1d claim, from human-made memory and with a rules policy. Lessons distillation exists but is unproven. Cold starts failed |
| Interpretability and reproducibility | Very strong: replayable ledgers, decision reasons, stored certificates, `report.md` per run, commands in every README |

**Track 1 criteria.** "How far past the record": small. For 1d, about 0.04 % below the best open claim. For
third-autocorrelation, 5.7 δ on a leaderboard that had moved 169 δ in one October day. "How new your loop is":
moderate. `autoloop` is a typed-move loop with a claim gate; Knockout is new but failed. "Can we follow each
decision": excellent. This is where the team is hard to beat.

My inference on why judges would notice them: nearly every competitor can show a number, while few can show that
their number cannot be a verifier artefact. This team could, and they also showed which numbers fail that test.

## Build process

- **Timeline (PT, author dates):** first commit 14 Nov 11:07. Selection stages 1–3 by 12:45. Problem chosen and
  frozen by 14:28 (V2). Knockout M0–M5 committed at 17:52 (V3). First full Modal run at 18:16 after two failed
  attempts. 1-hour campaign at 18:37. Handover and v2 brief at 20:17 (V4). v2 and scout burst from 20:46 to 22:44
  (43 commits in two hours). 1d tournament overnight. Autoloop and SAVANT at 10:18–10:36 on 15 Nov. Portfolio
  campaign 11:08–12:05. Memory merged at 12:57 (V5). README rewrite at 13:43. PR #2 merged to `main` at 13:49. Last
  README commit at 14:25.
- **Volume:** 134 commits across 23 remote branches, 32 on `main`. Commit trailers name Claude Fable 5.2 (51), Claude
  Opus 5.5 (42), Claude Mythos 5.2 (16) and Cursor (6). Nearly all code was written by coding agents. Arthur "wrote
  v1 with Claude Code in one day" (`knockout/README.md`).
- **Agent rules in the repo.** `CLAUDE.md` (26 KB) acts as the team's shared memory and contract. It holds the
  workstream table, the four rules (never claim from an LLM's judgment; independent check; frozen verifier; no
  secrets), the shared JSONL fields, a WS3 rulebook for GPU runs (11 rules) and the decision log. `GIT_RULES.md`
  tells every agent to work on one task branch, inspect before acting, stage explicit paths, and never force-push.
  Commits follow Conventional Commits with a repo-wide version tag `(V<major>.<minor>)`, bumped by reading
  `git log --all` first. A major bump marks a milestone. The scheme slipped under parallel work: six tags are reused (V4.40,
  V4.41, V4.95, V4.96, V4.97, V5.1), and some versions are out of order.
- **Briefs as prompts.** Big redesigns were written as self-contained prompts for a fresh coding agent, with
  calibration gates and stop rules (`docs/knockout_v2_brief.md` §8 and §12). Agent personas appear by name in docs
  ("Lupa", "Meridian"). My inference is that these are named sub-agents.
- **Workstreams in parallel:** selection and arbitration (Arthur), the genetic loop (Arthur), memory (Marie),
  integrations, research map and autoloop (Murilo), Modal benchmarks and SAVANT (Thibault). They integrated through
  merges into `genetic-method-final` and one PR to `main`. Most of the record evidence (1d, third-autocorrelation)
  never reached `main`.

## Lessons we can reuse

Ranked by expected value for a Track-1-style event.

1. **Build the arbiter before the search.** Freeze the live verifier by hash. Write a second exact verifier that
   shares no code with it. Route every claim through one script that writes one verdict line. This lets you
   publish modest margins with confidence and kill false records early.
2. **Write a "what this is not" box next to every headline number:** not from scratch, not submitted, which
   reference, read when. It turns a judge's objection into a point for you.
3. **Seed the autonomous loop with your own best artefacts, and say so.** The 1d record needed a memory of 239
   certificates. Cold starts failed. Make memory import a first-class move with `origin: imported`.
4. **Check structural moves before mutation loops.** Coarsen-and-repeat followed by re-annealing found 5.7 δ where
   polishing found 0. Restarting from memory beat LLM program evolution on every problem here. Try a multigrid or
   basin-change move in the first hour.
5. **Re-rank problems by live headroom signals, not by a 10-minute probe.** Recent record moves and literature
   claims below the leaderboard matter most. The pre-registered score ranked third-autocorrelation near the bottom,
   and it became their EinsteinArena candidate.
6. **Constrain the LLM policy to a menu of typed, contract-checked moves, with a rules fallback.** Log menu, choice
   and reason for every decision. Run a rules baseline in the same tournament. Here the rules policy beat both LLMs.
7. **Read the reference live inside the claim gate** (younger than 30 min, conservative rounding of the printed
   digits). Fail closed when the read fails.
8. **Sandbox LLM-written code on Modal with no network and no secrets**, and re-score every output in the trusted
   process. Watch thread counts: a 48-core container exports `OMP_NUM_THREADS=48`.
9. **Run a control arm at equal spend.** It exposed that the gene machinery added nothing, and the team reported
   that honestly.
10. **Push every branch you cite.** Three README branches are missing, and with them the evidence for two claims.

## Open questions / things I could not verify

- I ran none of the verifiers, so all scores are as stored. I checked the logic of the 1d and third-autocorrelation
  exact checkers by reading the code only.
- Whether the track B third-autocorrelation lead still held at judging time. The last committed live read is 15 Nov
  06:20 UTC.
- Whether PR #371 or a later PR on Tao's repository moved below 1.4540711 before judging.
- "Reproduced by a second agent from a clean checkout" (1d README): I found no log of this.
- The basin-screen results (957 episodes, p = 0.82) and "the lab": their branches are not on the remote.
- Modal and LLM costs are the governor's estimates from list prices, not bills. The README says this.
- Which sponsor prizes the team formally entered, and what was shown in the live demo.
- Whether a MILO-derived result (track A) would count for EinsteinArena or for Track 1. The team left this open.
