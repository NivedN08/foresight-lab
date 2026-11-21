# Crux (Track 3, AI Scientist Swarms)

Repository: github.com/mian-abd/Crux, read from a local clone. I ran none of its code. Every number below comes from a file I read, and paths are relative to the repo root. Where I could recompute a number from raw files with `jq`/`awk`, I say so. Inferences are marked as inferences.

## One-paragraph summary

Crux is a multi-agent loop for differential-expression analysis. It does not let agents vote on analysis choices. Each choice becomes a question with competing options plus a "none of these" option. Each option must state, ahead of time, what every candidate check will show. The loop runs the check expected to tell the options apart best per unit of cost, in a network-blocked Modal sandbox. A blind referee then validates the run, and a Bayes update follows. The headline result is on BixBench3 task 9 only, starting from published counts. On 52 freshly generated cases, each with a planted data fault or none, the swarm scored 0.817 against 0.671 for a single gpt-5.5 agent on the same $2 budget. The paired difference was +0.146 (95% CI +0.084 to +0.215). The swarm caught 48 of 48 planted faults; the single agent caught 14 (`eval/results/task9_batch05.md`). I recomputed both means and both mean costs from the raw per-run files, and they match. What made Crux stand out was not the size of that gap. It was the evaluation discipline around it: every batch was pre-registered in a commit before it ran, and failed runs count as zero. They also report their own negatives prominently: information-gain selection did no better than random selection, on Task 9 and on BoxingGym. The big caveat, which they state themselves, is that they wrote the fault suite and the check library to cover the same six fault types. On clean data the two arms are tied.

## Team, track and challenges entered

- **Team** (README "Credits"): Mian Abdullah (mian-abd), Gavin Zhao (gavinzhao30), Xiaozhi "Jason" Xue (Jason-xxz) and OrientalMagixx. Four people.
- **Track:** 3, AI Scientist Swarms (`docs/SUBMISSION.md`, first line).
- **Sponsor challenges claimed:** Exa, past.dev and Modal (`docs/SUBMISSION.md`, "Sponsor challenges entered"). They also used Tinker (Thinking Machines) to fine-tune a Qwen3-8B predictor, Langfuse for tracing, and a Jetson Orin Nano for a physical demo.
- **Who did what**, inferred from commit authorship. Mian Abdullah: the orchestrator, Task 9 wiring, the result batches and the site. Gavin Zhao: the v2 contracts, the proposers, the BoxingGym study (DU-41), the evaluation harness (DU-23) and provenance (DU-45). Xiaozhi Xue: the Modal image and executor, the grader wrapper, the single-agent baseline, the fault suite, leakage controls, the EIG scorer (DU-6) and calibration (DU-31). OrientalMagixx: the past.dev memory, the lessons, and the headless Jetson bench.
- **Placement:** the repo says nothing about placement. The brief says the overall winner was a Track 4 team. Whether Crux placed in Track 3 or won a sponsor prize cannot be verified from the repo.

## The problem and the benchmark(s)

- **BixBench3 task 9** (Edison Scientific): reproduce a differential-expression analysis from GEO GSE234912. That is a Tag-Seq count matrix of 60,708 genes and 16 samples: T98G Ctrl, C10, F7, G2 and D4, and HEK293 Ctrl, D10 and E6, two replicates each (`eval/suites/task9/make_cases.py`, `GROUPS`). Edison's deterministic grader, pinned at commit `d0e0bbb`, scores 9 required artifacts: 7 DE tables and 2 shared or specific gene sets. A run's score is the mean of the 9 (`eval/results/task9_runs.md`, `eval/grade_bixbench3.py`).
- **The record** is 0.523. It covers all 20 tasks and starts from raw reads. Crux does one task from counts, so its numbers are not comparable. The team says so and declines to compare (`docs/SUBMISSION.md`, "The record").
- **Their own benchmark variation.** The clean case alone leaves little room: both arms reach about 0.86 to 0.87. So they built a planted-fault suite (`eval/suites/task9/make_cases.py`). Each suite has 13 blinded cases: one clean case plus two each of six faults. The six faults are a control/treated sample swap, a control/treated label inversion in one cell line, a duplicated library, a library thinned 50x, a cross-cell-line swap, and mixed versioned/unversioned Ensembl IDs. Sample IDs are re-blinded as `S01..S16` in every case. The grader compares outputs to the clean references, so a run scores well only if it detects and undoes the fault. A second, model-judged score comes from `eval/suites/task9/score_faults.py`. There a gpt-5.5 judge reads the run's `METHODS.md` next to the truth key and marks the fault caught, noticed or missed.
- **The baseline** (`eval/baseline_agent.py`) is a single ReAct agent on gpt-5.5. Its actions are python, r, bash, search and finish. It gets the same Modal image, inputs, fallback prompt, grader, $2 cap and 40-call limit. Its output is capped at 4,096 tokens per call.
- **Secondary tests:** a port of BoxingGym Location Finding (`labs/boxing/`), a claims audit of four DESeq2-paper claims (`labs/claims/`), the 12 diagnostics run on three public datasets (`eval/results/real_data_diagnostics.md`), and two physical Jetson demos (`labs/bench/`, `labs/oneletter/`).

## What they built, mechanism in detail

**The core idea, plainly.** When two agents disagree, for example "two samples look swapped" against "keep the labels", Crux does not argue. Each side writes down what a given check would show if it were right. Crux runs the check whose possible results would best separate the sides, discounted by how long the check takes. It moves its beliefs only by Bayes' rule on the result that came back. A side is dropped once its probability falls below 2%, and the drop is tied to the evidence id that caused it.

**Precisely.**
- Each question has options h_i with priors, plus a null option h0 at prior 0.15 that predicts a uniform distribution over outcomes.
- Each candidate check has outcome bins, and each option supplies a row P(bin | h_i).
- The expected information gain (EIG) is the mutual information between the option and the bin: H(Σ_i p_i P(·|h_i)) − Σ_i p_i H(P(·|h_i)) (`core/eig.py`, `expected_info_gain`).
- Options that lead to the same deliverable share an `answer_key` and are grouped before the EIG is computed. That makes the gain information about the decision, not about the hypotheses.
- h0 is left out of the EIG unless fewer than two real options remain (`core/scoring.py` docstring). Otherwise a check on which every real option agrees would look informative against the uniform null.
- The ranking score is `impact × EIG / (1 + 0.2 × cost_s / 60)` (`core/eig.py`, `score`; `core/config.py`). Despite the "per dollar" tagline, the code discounts by estimated seconds, not dollars.
- Thresholds (`core/config.py`): settle at posterior 0.9, reject below 0.02, stop below 0.05 bits or after 4 experiments per question.

**The loop, step by step** (`core/orchestrator.py`):

1. **Profile.** `_Run._main` calls `Task9Lab.profile` (`labs/bixbench3/task9.py`) to profile the staged data inside the sandbox. The profile is written to memory as experimenter evidence.
2. **Propose.** `SwarmRoles.propose` (`agents/swarm.py`) calls three gpt-4.1-mini personas: statistician, bioinformatician and sceptic (`agents/proposer.py`, `agents/prompts/*.md`). A merge model groups their questions. Then the six **library forks** from `diagnostics/de_from_counts/library.py` are appended, and these skip the merge. The forks are sample labels, duplicates, sample quality, cell line, gene IDs and group direction. Each fork has fixed options, hand-set impacts (for example 0.95 for labels) and two diagnostics with different code. Each diagnostic has hand-written "stated" or "exact" likelihood rows. `_init_options` adds h0.
3. **Pick a question.** `_categorical_loop` takes the open question with the highest impact × entropy.
4. **Design.** `SwarmRoles.design` is a stand-in; there is no `designer.py`. It returns only the pre-registered library diagnostics attached to the question, plus pre-registered Exa searches for five documentation questions (`search/task9.py`, `search_diagnostics`). The proposers' own questions get no diagnostic, so they end "unresolved".
5. **Predict.** `_fill_likelihoods` gets P(bin | option). Library options use the stated rows, floored at 0.02 (`agents/predictor.py`, `LIBRARY_FLOOR`). Other options are sampled 8 times from gpt-4.1-mini, each sample blind to the other options. Optionally a Tinker LoRA (`agents/tinker_predictor.py`) fills them instead; it is off by default.
6. **Filter and rank.** `_separates` drops a check unless two options' rows differ by total variation at least `RELEVANCE_TV`. `_prepare` drops any check whose spec hash already ran. `_rank` scores the rest. If the best EIG is below 0.05 bits, the question becomes unresolved. `_select` takes the top check, or a random one under `selection="random"`, which is the ablation arm.
7. **Run.** `_run_categorical` runs the script in the sandbox (`execution/sandbox.py`). The script prints one `RESULT: {json}` line. **Code** maps the result to a bin using exhaustive, mutually exclusive rules (`labs/bixbench3/bins.py`); a model never picks the bin.
8. **Referee.** `agents/referee.py` runs code checks first: a valid bin, no traceback. Then gpt-5.5 judges three things. Did it run? Does the code do what the goal says? Is the result derived from the data? The model sees only the allowlisted `SPEC_FIELDS = ("kind", "goal", "language", "code", "query")`, the bin ids and rules, the result and a log tail. It never sees options, priors or posteriors. An invalid run moves no belief, but its cost still counts.
9. **Update and stop.** `_apply` makes the Bayes update and `_reject_low` drops options below 0.02. `_check_closed` settles a question at 0.9. `_check_robust` closes it when all live options share an `answer_key`. A question is unresolved after 4 experiments, below 0.05 bits, with no diagnostic left, or when every option is rejected. A surprise (low predictive probability, or h0 above 0.4) calls `_regenerate`. In the swarm that is a stand-in that returns no options.
10. **Deliver.** `agents/deliverer.py` gives gpt-5.5 the settled decisions, the flagged sample ids and the unresolved list, and it writes R/DESeq2 code. Its prompt says to follow settled decisions. An untested question keeps DESeq2 defaults (PR #47).
11. **Critique.** `agents/critic.py` checks the artifact contract in code. Breaking a rule gives a `block`. A model then reviews `METHODS.md`; its findings are only `warn` unless they quote the broken contract text word for word. One repair round follows (`max_critic_rounds = 2`).
12. **Report.** `agents/reporter.py` writes the methods only from logged facts. Prose that cites a number not in the facts is replaced by a template.

**Provenance.** Every step emits an event (`docs/EVENTS.md`). `core/replay.py` recomputes every Bayes update from `events.jsonl` and fails on a corrupted log. `core/manifest.py` records model ids, prompt hashes, input digests, the git revision, and `tools_connected`, meaning what the run actually had rather than what its config claimed.

**What actually decides things.** This is my reading of run 1's log. All 11 experiments in `runs/demo/task9_real_swarm_run1.jsonl` were library diagnostics. Each question had at most two candidates, with nearly equal EIG; for example the duplicates fork's two checks both scored 0.919 bits. The other check of each fork ran in a later round anyway. All 12 proposer-raised questions ended unresolved. In practice, then, the decisions in graded batches come from a hand-written 12-check QC library plus Bayesian bookkeeping. The EIG rule only orders checks that all get run. That explains why random selection matched EIG exactly (see below).

**The check library is tailored to the task** (`diagnostics/de_from_counts/scripts/`). The two group-direction checks rely on this study's structure: two cell lines that share a knockout. `direction_signature.py` asks whether each treated group's fold change correlates positively with the other line's. `direction_marker.py` looks for a gene that drops in every knocked-out group. On real single-line datasets these checks abstain (`eval/results/real_data_diagnostics.md`). This is design knowledge, not leaked ground truth, but no generic agent would arrive with it.

## How they beat (or did not beat) the benchmark

**They did not beat or approach the BixBench3 record.** They never ran the full suite or started from raw reads. What they beat is their own single-agent baseline on their own planted-fault variant of one task.

**Headline protocol.** Batch-04 was pre-registered in PR #59 (commit `95aaa11`, Nov 15 05:56) on a fresh seed-44 suite. Batch-05 was pre-registered in PR #72 (commit `3f09a28`, 09:25) on three more suites, seeds 45 to 47. The protocol names the combined result as the headline: "This combined number becomes the headline, whatever it shows" (`eval/results/task9_protocol_batch05.md`). Each arm ran once per case, 104 runs in total. Exa and past.dev were stripped from both arms. Batch-05 took 78 runs, about 20 minutes and $104.94.

| Measure (52 cases) | Swarm | Single agent |
| --- | --- | --- |
| Mean score | 0.817 | 0.671 |
| Paired difference (bootstrap 95% CI) | +0.146 (+0.084 to +0.215) | |
| Clean cases (4 runs each) | 0.864 | 0.868 |
| Faults caught / noticed / missed, of 48 | 48 / 0 / 0 | 14 / 6 / 28 |
| Mean cost per run | $1.18 | $1.47 |
| Runs stopped by the $2 cap | 0 | 27 |

**My check of the evidence.**

- **Recomputed and matching.** From the four raw files `eval/results/task9_batch04/suite_results.json` and `task9_batch05_h4{5,6,7}/suite_results.json` (52 runs per arm) I get a swarm mean of 0.8171 and a single-agent mean of 0.6709. Mean costs are $1.182 and $1.473. These match `eval/results/task9_heldout_summary.json`. 27 single-agent runs have status `spend_limit`. One swarm run, `h46-case-12`, has status `budget_exhausted` and scored 0.46.
- **Pre-registration holds in git.** Each protocol file was committed once and never edited afterwards. Each results file landed in a later commit: batch-04 at 06:26, batch-05 at 10:12. Commit timestamps are self-reported, so this is consistent but not proof.
- **Where the gap comes from.** My tabulation of `eval/results/task9_heldout_all/suite_fault_detection.json`, mean score by fault type (swarm vs single agent, 8 cases each):

| Fault | Swarm | Single agent | Single agent caught |
| --- | --- | --- | --- |
| label inversion | 0.863 | 0.505 | 0 of 8 |
| sample swap | 0.738 | 0.474 | 0 of 8 |
| cell-line swap | 0.865 | 0.588 | 3 of 8 |
| low depth | 0.774 | 0.688 | 3 of 8 |
| duplicate library | 0.783 | **0.806** | 1 (5 noticed) |
| gene version | 0.856 | **0.866** | 7 of 8 |

  The whole advantage sits in label and sample faults, the ones that silently corrupt group assignments. On duplicates and gene IDs the single agent is level or slightly ahead, even though it "caught" fewer duplicates. On clean data the arms are tied.
- **Weak or unverifiable points.**
  1. **Circular design.** The team wrote both the six fault types and the checks for them (they say a check library "catches the faults it was written for", README). The fault suite cannot show detection of anything else.
  2. **"Fresh" means new fault instances, not new data.** All 52 cases come from the same 16-sample matrix, with new random fault placements. The case-level bootstrap CI says little about other datasets.
  3. **The baseline was handicapped by the harness.** It had a 4,096-token output cap, which crashed four batch-02 runs before PR #40 fixed it. It hit the $2 cap in 27 of 52 runs. In `h44-case-01` it scored 0 because it decided `/data` was empty: the sandbox mounts `/data` as a symlink and its listings did not follow it. The team reports a sensitivity analysis that drops all three zero cases: +0.104 (+0.057 to +0.154).
  4. **The missing ablation.** No arm gives the single agent the same 12 checks, or runs the swarm without the library. Random selection matched EIG on batch-03. So I cannot tell how much of the gain is the checklist and how much is the multi-agent Bayesian loop. My guess is mostly the checklist.
  5. **The fault judge** is gpt-5.5 reading `METHODS.md`, and it is not blind to the arm: swarm methods name the diagnostics. The grader score, which carries the headline, is deterministic, so this affects only the 48-vs-14 count.
  6. **Tuning.** Two prompt rules were written after the team saw clean-case grades: one DESeq2 fit per cell line (PR #30), and untested questions keep defaults (PR #47). The team labels clean-case scores from then on as tuned.
  7. The official task prompt is gated, so both arms used a local fallback prompt (`labs/bixbench3/task9_contract.py`, `load_prompt`). That prompt tells both arms to check sample labels and library quality first.
  8. Possible model memorisation of the 2024 study is not measured.

**The claims audit.** The README section "Claims audit (MVP)" is a second lab that aims the same orchestrator at statements in a paper rather than at data (`labs/claims/`, `docs/CLAIMS.md`). It covers four hand-curated claims from Love, Huber & Anders (2014). The diagnostics are SymPy derivations and NumPy/SciPy simulations, with deterministic code roles and no LLM. It costs $0 and takes about 10 s. The results:
- The NB variance μ + αμ² holds at posterior 0.956.
- Wald p-values with 3 replicates hold only with moderated dispersion, at 0.941. The false-positive rate is 0.114 with per-gene dispersion and 0.058 with moderated dispersion.
- BH controls the FDR, at 0.951.
- The team planted a false claim: "BH controls the FWER". Its `holds` option is rejected at 0.017 using a 3-test counterexample (FWER 0.0659). The question itself ends unresolved because the last check was worth 0.047 bits, just under the 0.05 floor. They report that rather than change the pre-registered row.

I checked these against `runs/demo/claims_deseq2.jsonl`: the `question.settled`, `hypothesis.rejected` and `question.unresolved` events give exactly those posteriors. So the audit holds as described. But it is a demo of generality, not a result. The claims are textbook facts plus one planted falsehood. The options, checks and likelihood rows were all written by hand. The Wald test is a simplified stand-in, not DESeq2. On the site, extracting claims from uploaded papers only matches a paper to these four claims.

## Experiments, ablations and failures

- **Progression on the clean case** (README "Progression"). The team says plainly that this is not a learning curve: each step is a human fix made between batches.

| Runs | Swarm | Single agent | Fix that came before |
| --- | --- | --- | --- |
| Run 1 | 0.586 | not run | none (no gene annotation, so two artifacts scored 0 by construction) |
| Batch-01, 3 runs | 0.628 | not graded | none |
| DU-22 pair | 0.727 | 0.862 | added a gene-symbol map |
| Batch-02, 3 runs | 0.833 | 0.868 | one DESeq2 fit per cell line (PR #30) |
| Batch-03, 3 runs | 0.862 (crashed run rerun) | 0.860 | untested questions keep defaults (PR #47) |
| Batch-04 and 05, 4 runs | 0.864 | 0.868 | bad merge plan keeps proposals (PR #54) |
- **Random vs EIG selection** (batch-03, 13 cases): −0.000 (95% CI −0.001 to 0.001). Scores were 0.810 for both, costs were $1.13 vs $1.14, and random caught 11 of 12 faults against 12. This is a null result on the system's headline idea (`eval/results/task9_batch03.md`).
- **BoxingGym** (`eval/results/boxing/README.md`), 6 arms, 20 paired seeds. Crux EIG had a mean log10 MSE of 2.946, against 2.392 for Crux-random and 0.984 for plain random design. Plain random had the best median MSE (8.737). Crux EIG had 3 failed episodes of 20, one of them a Windows NumPy DLL failure.
- **Lessons on vs off** (9 held-out cases): +0.010 (95% CI −0.059 to 0.089), no effect. The reviewer had seen batch-02's test-case results.
- **Tinker predictor.** In distribution, log loss fell from 0.600 to 0.472, beating the library rows at 0.557. Under shift there was no edge. On held-out real COVID data the library rows won: 0.503 against 0.591 to 1.230 for the fine-tunes. It stays off (`eval/results/predictor_calibration.md`, `predictor_real_data.md`).
- **Real public data.** The checks caught 26 of 30 planted faults, with 1 false alarm in 33 cases. All 4 misses are label inversions on single-line designs, where the direction checks abstain. In airway (GSE52778) the checks found an apparent, unconfirmed albuterol / albuterol+dex label swap in two donors, backed by an independent dexamethasone-signature score. They also found and fixed a `cell_line_pc1` false alarm (`eval/results/real_data_diagnostics.md`).
- **Failures they own.** A merge-step crash killed three batch-03 swarm runs. The pre-registered swarm score was 0.678 against 0.742, then 0.810 after the PR #54 fix, rerun and labelled as a deviation. Batch-02's single-agent crashes came from a harness bug. Run 1's config said memory and search were on when neither was connected. In the batch-05 cloud run, fault scoring matched no cases because of a file-name mismatch and was re-run by hand.
- **Never run:** the `crux_no_critic` preset on Task 9, the role-only memory comparison, and any score effect of search or memory. The DU-23 harness runs (`eval/results/task9/du23-*`) are acceptance tests where most scores are 0, not results.

## How they used Exa, past.dev and Modal

**Exa: in the loop, as an experiment, but off in every graded batch.**
- A search competes with compute checks on EIG per cost. It is costed at 4 s (`SEARCH_COST_S`) and an estimated $0.007. Its bins are code rules over the returned text (`search/task9.py`).
- The call path runs from `_Run._run_search` to `GuardedSearch.find` (`search/task9_policy.py`), then to `ExaSearch.lookup` (`search/exa.py`).
- The leakage policy allows only documentation hosts. It blocks the source paper, its DOI, GEO/SRA, PubMed and study terms, in the query, the URL and the snippet.
- Only one run, `tools-run-02` (score 0.815), had it on. In that run, three searches ran, one rejected an option, and 11 support-forum URLs were blocked (`runs/demo/task9_tools_run2.jsonl`, 3 `search.done` events).
- The site has a separate "Find sources with Exa" button on each extracted claim (`site/lib/server/exa.ts`), labelled as reading material, not a verdict.
- **Verdict:** well designed to be central, but in evidence terms bolted on: one run, no measured effect.

**past.dev: real adapter, almost no effect on decisions.**
- `memory/pastdev.py` uses one audience per run, role-specific readers, a durable SQLite outbox, and objections that are always recalled.
- Referee, experimenter and critic write. Only the critic's prompt actually uses another role's records (8 referee records in `tools-run-02`). The designer stand-in recalls 24 times and ignores what it gets.
- The README has no section with the challenge's required headings (Agents, Layers, Results, Caveats). Similar content sits under "Shared memory (past.dev)" and "Which memory holds what".
- They say outright that there is no proven gain, and the role-only comparison arm was never run.
- **Verdict:** bolted on, honestly labelled.

**Modal: central.**
- Every run gets its own `modal.Sandbox` with `block_network=True`, 4 CPU, 16 GiB, and hashed inputs mounted read-only. A script checks every input digest before any code runs (`execution/sandbox.py`, `execution/staging.py`). The R 4.5 + DESeq2 image is built in `execution/image.py`.
- The cloud runner (`eval/cloud.py`) is one non-preemptible orchestrator container per launch. It clones Edison's grader at the pinned commit and keeps references and truth keys on a `crux-evaluator` volume that sandboxes never mount.
- Each run reserves its $2 against a launch-wide `--max-total-usd` cap before it starts. Every run has its own LLM cache, so repeats never replay each other.
- Batch-05 ran three launches in parallel: 78 runs in about 20 minutes. Modal spend and sandbox hours are listed as "not measured".
- **Verdict:** the one sponsor tool every graded number depends on.

## Autonomy and self-improvement

- **Autonomous within a run.** One CLI command runs profile, propose, experiments, delivery, critique and report, capped at $2, 40 calls and 30 minutes (`core/config.py`). One cloud command runs and grades a whole batch.
- **Self-correction.** Within a run there is the critic's one repair round: in run 1 it fixed versioned IDs in 7 tables, leaving one `_PAR_Y` ID in 4. Across runs, the predictor fine-tuned on its own diagnostic outcomes is not used by default, and lessons drafted from its own dev runs had no measured effect.
- **The improvements were human-made.** They frame this as "Where it fell short, and what changed" (README): a table of failure, log evidence, fix and next batch. Surprise-driven regeneration and the designer are stand-ins, so the swarm cannot invent a new check or a new option.

## Presentation and judge-facing craft

- **README** (867 lines). The tagline is "Disagreements settled by experiment, not by vote." It has live-link badges, Mermaid bar charts of the headline, flowcharts, a sequence diagram, a state diagram, an architecture diagram and a mindmap. It also has a "What is real and what is synthetic" table and a full "Reproduce" section with exact commands.
- **SUBMISSION.md** has a 150-word description, a "Where to look" list, and a table mapping each judging criterion, Track 3 ask and sponsor challenge to evidence. It also lists "What we rejected ourselves" and ends with a "Not measured" list. It opens with "Every number below comes from a file in this repository".
- **Honesty as technique.** Labelled deviations sit next to the pre-registered numbers, never in place of them. Negatives get headings ("Honest negative: EIG selection did not beat random selection here"). Caveats repeat in the README, SUBMISSION.md and the landing page.
- **Live site** (`site/`, Next.js 16, Better Auth with SQLite, deployed on Railway). The landing page (`site/app/page.tsx`) shows:
  - a scoreboard (`components/scoreboard.tsx`);
  - a "Clean case, first run to now" growth chart (`components/growth-chart.tsx`), which shows human fixes as growth with the change named on each bar;
  - a "What it does not show" caveats box, with each number linked to its repo file;
  - a graded-run preview and the loop diagram;
  - a sponsor section;
  - a Jetson section headlined "We swapped the DNA mid-run. It noticed.", with a recorded video (`web/public/fixtures/jetson-demo-muted.mp4`).

  Signed-in users can:
  - upload a PDF, have claims extracted by gpt-4.1-mini, and see a quote flagged when it is not word for word in the paper (`lib/extract.ts`);
  - run the free claims audit and watch it stream (`app/app/runs/[id]/page.tsx`);
  - replay graded Task 9 runs (`app/app/graded/page.tsx`, `components/graded-runs.tsx`);
  - start a **live Task 9 run**, limited to 3 per user per day, 20 per day and 2 at once (`site/lib/server/live-runs.ts`). The API also requires an `X-Crux-Run-Token` (`api/server.py`).

  There is also an Explore gallery of 3D views (a DNA helix, Mol* protein structures, an evidence graph), which is mostly visual polish. When the API is down, the run page plays a hand-written fixture and says so (`site/README.md`).
- **Run viewer** (`web/`, Vite + React). Its panels cover questions with animated posterior bars, the experiment ranking, a console, memory and search with blocked URLs struck through, critic findings, the rejected ledger, and belief history. All are driven only by events, and replay works offline.

## Why it stood out, scored against the five criteria

**Research rigour (strong).** Each batch was pre-registered in its own commit, on fresh held-out suites. Failures count as zero, and deviations are reported next to the pre-registered numbers. They report paired bootstrap CIs, a sensitivity analysis, and grader isolation backed by tests. The weakness is structural: they wrote both the fault suite and its checks, and the ablation that would isolate the checklist's contribution is missing.

**Novelty (moderate).** They admit that LLM-guided experimental design is prior work (BED-LLM, HypoAgents, in `docs/research/DU-37-rigour.md`). What is new is the combination: analysis forks as questions, code-computed outcome bins, a referee blind to the options, "unresolved" as a reportable result, and every rejection tied to its evidence. Their own data says the novel part, the EIG ranking, adds nothing measurable.

**Results (strong on paper).** The score gap is clear and pre-registered, with 48 of 48 faults caught. There is a live demo that can start real paid runs. But it covers one task, one dataset and a benchmark variant they built themselves, and it does not compare to the record.

**Autonomy and self-improvement (moderate).** Each run is fully unattended and batches run in the cloud. The self-improvement attempts (the predictor fine-tune, lessons) were measured and showed no effect. The visible improvement was human-made.

**Interpretability and reproducibility (very strong).** There is an event log with a replay that recomputes every Bayes update. Manifests record `tools_connected`. Methods text is built from facts only. Every result file sits in the repo and every command is listed. This is probably the best-documented project of the event.

## Comparison with a blind-replication lab

Here, a blind-replication lab means two blind teams, a director who challenges agreed answers, and a referee who must run a test.

**What Crux does that such a lab does not:**
- **It challenges the default assumption on every run, cheaply.** Each library fork tests "the data is as given" even when no agent doubts it. That is a director's challenge made systematic and pre-written. It is exactly what won the fault cases: the single agent kept bad labels and scored about 0.47 to 0.59.
- **It turns disagreement into numbers before any test runs.** Options commit to likelihood rows, EIG ranks the checks, and posteriors and a 0.02 rejection bar are logged. A lab director's challenge is usually prose.
- **Outcomes are decided by code**, through a `RESULT:` line and bin rules. The referee is blind to what each side expects. In a lab, the referee usually knows both answers.
- **It is cheaper.** It needs one delivery pass, at $1.18 against $1.47 for the single agent. Two full blind replications cost about twice one.
- **It has "unresolved" and "robust" exits.** A question can close without a winner when both options give the same deliverable, or when nothing worth 0.05 bits remains.

**What a blind-replication lab does that Crux does not:**
- **Independent re-derivation.** Crux has one delivery pass by one gpt-5.5 call. An error that no pre-written check covers goes through unseen, for example the DU-22 per-pair refits that recovered only 427 of 1,232 genes. Two blind teams would likely disagree on such a pipeline choice, and the referee would have to test it.
- **Tests invented on the spot.** Crux's designer is a stand-in, so it can only run checks written before the event. A lab referee who must design and run a test is closer to a real designer.
- **Disagreement over finished answers.** Crux settles forks before the analysis. A lab compares finished outputs, which catches mistakes in implementation as well as in assumptions.

**What to borrow:** give our lab a small pre-registered fault-check library that every run executes, plus code-computed outcome bins. Keep blind replication for the errors those checks cannot see.

## Build process

- **Timeline** (git, Pacific time):

| When | What |
| --- | --- |
| Nov 14 12:24 | Initial commit |
| 13:23 | ERD with task briefs |
| 14:17 to 16:56 | Core modules merged through parallel PRs, one per Linear issue (DU-n) |
| 18:09 | First graded Task 9 run (0.586) |
| 18:42 | Batch-01 (3 swarm runs) |
| 19:49 | Batch-02 pre-registered |
| 20:48 | Batch-03 pre-registered, lessons frozen |
| 21:47 | Modal cloud runner |
| 22:53 to 23:02 | Claims lab, then the consumer site |
| 23:22 | Batch-03 results (tied; random = EIG) |
| Nov 15 00:43 to 02:13 | BoxingGym repair and the evaluation harness |
| 05:56 / 06:26 | Batch-04 pre-registered / results |
| 08:43 onward | Site redesign |
| 09:25 / 10:12 | Batch-05 pre-registered / results |
| 11:31 | Final commit, three hours before the 14:30 deadline |
- **Volume.** 194 commits and 88 PRs. Mian Abdullah made about 120 (under two identities), Gavin Zhao 40, Xiaozhi Xue 28 and OrientalMagixx 7. About 49k lines of Python, 489 test functions in 45 files, and 31k lines of TypeScript.
- **Process.** `docs/ERD.md` opens "Read this before writing any code". It gives the stack, a data model, interfaces, conventions and independent task briefs A to H, each with acceptance tests. Rules include: every ablation is a config flag, a model never picks a bin, and never read the ground-truth bucket. There is no CLAUDE.md or AGENTS.md. On authorship the ERD says: "If a tool produced a change, review it, then commit it yourself." It also forbids co-author trailers. **Inference:** about 80k lines in 23 hours by four people, built from self-contained briefs, points to heavy use of AI coding agents with the attribution removed by policy. Two commit bodies mention "generated", both about fixture generators.

## Lessons we can reuse

1. **Pre-register each batch in its own commit, on a held-out set made after the code froze, and name the headline in advance "whatever it shows".** This was their single biggest credibility lever. It cost almost nothing.
2. **Build a benchmark variant where the architecture's advantage can show, then say why.** Their clean-case scores were tied. The planted-fault suite created a measurable gap. Our lab should plant reproducible faults (swapped labels, a wrong reference, a duplicate sample) in a BixBench task and measure catches, and be explicit that the checks were written for those faults.
3. **Make code, not a model, decide outcomes.** One `RESULT:` line per check and exhaustive bin rules evaluated by code. A model referee only judges validity and never sees which answer anyone wants.
4. **Run the ablation that tests your own idea, and headline it if it fails.** Random versus EIG, lessons on versus off. Also run the one Crux skipped: single agent plus the same checklist.
5. **Keep failed runs at zero, and rerun only as a labelled deviation next to the pre-registered number.**
6. **Write an event log that a replay script can recompute, and record what the run actually had** (`tools_connected`), not what its config claimed.
7. **Keep the grader isolated.** Keep references on a volume the agents never mount, run the grader in its own process, and add a test that fails if agent code imports it.
8. **Put the spend cap in the runner.** Reserve each run's cap before it starts, against a launch-wide cap, and give each run its own LLM cache so repeats are independent.
9. **Hand the judges a map from each criterion to evidence, a "not measured" list, and a "what we rejected ourselves" list.**
10. **Make sponsor tools compete inside the loop** (Exa as a costed experiment) rather than run on the side. Then actually run them in a graded arm, which Crux did not.
11. **Write the plan as independent briefs with acceptance tests before coding.** That let four people, and their tools, merge 88 PRs in 23 hours.

## Open questions / things I could not verify

- Whether Crux placed in Track 3 or won a sponsor prize.
- Whether the live site, API and viewer worked during judging; I did not visit them.
- Whether the PR timestamps on GitHub match the commit dates. Git dates are self-reported.
- Whether the bootstrap CIs are right. I recomputed means and costs, not the CIs, and did not run `eval/heldout_summary.py`.
- How much of the +0.146 comes from the check library versus the multi-agent Bayesian loop. No ablation separates them.
- Whether the gain would hold on another dataset, another task, or unplanned faults. Not tested.
- Whether gpt-5.5 had seen the 2024 study during training (memorisation). Not measured.
- The official Task 9 prompt is gated. How the fallback prompt differs from it is unknown.
- The fault judge's reliability and its blindness to the arm. Not reported.
