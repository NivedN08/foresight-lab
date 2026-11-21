# caffeinated: "Ig Nobel Prize Winning ASS (Artificial Scientist Swarms)"

Source: the repository `github.com/arii/caffeinated`, read from a local clone (391 commits on `main`).
Nothing was run. Every number below was read from a file in the repo, and the file is named.
"Inference" marks my own reading where the repo does not say it directly.

## One-paragraph summary

caffeinated is a multi-agent research swarm with a "boredom meter". The meter is a deterministic
score B in [0, 1], computed from the shared experiment ledger. When B gets high, the conductor
moves the four LLM workers from refining the best result ("exploit") to structurally different
ideas ("BOREDOM_SNAP", then "PARADIGM_BREAKOUT"). They tested it on small CPU proxies of the
hackathon benchmarks: Hadamard determinants, a BixBench subset and a synthetic PlayWorld. They also
pointed it at an "Ig Nobel" task, where agents pre-register Spearman tests on public datasets and
LLM judges rate the confirmed ones for laugh and think. The headline result is honest and mostly
negative. On the Track 3 check (swarm vs a single agent, BixBench held-out, 3 runs each), the two
reported orderings disagree. The preregistered primary set gives boredom swarm 0.26, single agent
0.24, swarm without meter 0.11. The only complete set gives single agent 0.30, no meter 0.09,
boredom swarm 0.06. Neither is significant. What made it stand out was not the score. It was three
things: a clean, psychology-framed and fully auditable control signal; a large amount of rigour
apparatus (preregistration with timestamped deviations, held-out splits, a gold-answer leak guard,
a one-command results verifier, a pinned Docker image, a Hugging Face dataset); and a playful
first-person 3D "lab" that replays the recorded agent trace, with self-deprecating branding
throughout.

## Team, track and challenges entered

- Team caffeinated: Ariel Anders, Jialin Feng, Neeraj Shah, Masha Baidachna (`README.md`,
  `data/pages/intro.json`).
- Track: Track 3, AI Scientist Swarms (swarm vs single agent on BixBench). The README calls its
  BixBench result the "Track 3" result (`README.md`, `experiments/confirmatory/RESULTS.md`).
  They also built small proxies of the Track 1 math record (Hadamard) and Track 4 world model
  (PlayWorld) tasks (`testcases/README.md`).
- Sponsor challenges: past.dev (a full "past.dev challenge" README section with Agents, Layers,
  Results and Caveats); Exa (scouting, prior-art checks, committee prior-work search); Tinker
  (every published run uses Kimi K3.1 Pro through Tinker; judges are gpt-oss-120b and Qwen3.5 on
  Tinker). Modal appears only in early commits (an autoresearch GPU runner on Nov 14, and a study
  sandbox on a side branch); it is not on the final code path.
- Tooling credit: the Lab and video credit Devin with building and testing "the swarm, the Lab
  and this site" (`data/pages/intro.json`, `video/SPEC.md`). 27 commits are by Devin accounts.

## The problem and the benchmark(s)

The stated hypothesis (`README.md`): AI scientist swarms "get stuck because they are bored",
trapped in repetitive low-payoff loops. A meter that detects this and forces a "seeking state"
should help.

Benchmarks are CPU-sized proxies, each with an editable file the agents change and a host-side
grader (`testcases/README.md`, `testcases/*/task.json`):

| Task | What is scored | Reference |
|---|---|---|
| `hadamard19` | loss vs the known optimal determinant of a 19x19 +-1 matrix | loss 0 = known optimum (a ceiling, not a record) |
| `hadamard29` | `log10(Barba bound) - log10|det|` | bound unreachable; no verified record shipped |
| `bixbench_mini` | 17 questions from public BixBench capsules, 10 dev / 7 test | full-BixBench 0.523 is "context", not comparable |
| `bixbench_numeric` | 45 numeric questions from 10 capsules, 27 dev / 18 held-out | same |
| `playworld_mini` | synthetic 9x9 gridworld, predict 20 future frames, 200 test seeds | dev-only constant 0.3598 of unknown origin |
| `ignobel` | pre-registered Spearman test on a public CSV, then committee | open-ended, no reference |

The Track 3 record named by the event (BixBench3 0.523) is never claimed as beaten. The repo says
plainly the subset "is not directly comparable" (`testcases/README.md`).

Ig Nobel scoring (`testcases/ignobel/task.json`, `testcases/ignobel/evaluate.py`,
`testcases/ignobel/committee.py`):

1. An agent writes `prereg.json`: field, study type, question, hypothesis, mechanism, at least two
   verified citations, a public CSV URL with exact column names, the direction and a predicted
   |rho|. No agent code runs.
2. The host downloads the data once (open-data hosts or URLs found by Exa, at most 5 MB, frozen by
   sha256). It runs one Spearman test on all rows. Significant means p < 0.005 (and, in the current
   version, |rho| >= 0.1) in the registered direction. "Split-half consistent" means the same sign
   with p < 0.05 in each of two fixed hash halves. Each dataset gets one test per run, so agents
   cannot fish through columns.
3. Confirmed studies go to a two-judge committee. Judge A (gpt-oss-120b) is "a senior member of an
   Ig Nobel-style committee". Judge B (Qwen3.5) is a science journalist. Each rates LAUGH and THINK
   from 1 to 10 and flags `targets_group`. The judges see only a host-written abstract plus five
   Exa prior-work results, never the agent's prose, so "a study cannot talk the judges up".
   Textbook results must get THINK <= 3.
4. Score = `0.25 + 0.75 * committee`, where committee = the lower judge's `min(LAUGH, THINK)/10`.
   A group-targeting flag scores 0. Loss = 1 - score.
5. A separate post-run "IgJudge" (`testcases/ignobel/igjudge.py`, `igjudge_prompt.md`) asks one
   OpenAI model to act as six judges over three internal rounds. The criteria are humour 2,
   plausibility 2, originality 1.5, insight 2, appeal 1, alignment 1.5, giving a 0-100 total. Only
   studies that passed the gates and are not the baseline can win (`rankable()`).

## What they built, mechanism in detail

### The boredom meter (`boredom_meter/meter.py`, documented in `docs/boredom.md`)

Inputs are only two files: the append-only `ledger.jsonl` (one attempt per line: seq, agent,
status, score, descriptor, sources) and a per-task `boredom_profile.json`. No clock, network, LLM,
embedding or randomness is read when scoring. Every output carries `ledger_sha256` and
`profile_sha256`, so any agent can recompute another agent's B (`status --at <seq>`).

**Descriptors, not diffs.** Each attempt declares where it sits in a closed vocabulary, for example
`{"mechanism": "search", "method": "tabu", "params": {"value": 4000}}`. Workers must put it in a
`Descriptor:` commit trailer, and unknown vocabulary is rejected. Distance is hierarchical
(`level_distance`): 1.0 if the mechanism differs, 0.5 if only the method differs, and otherwise a
parameter term of at most `params_weight` (0.25). So retuning one knob is never novel. This
replaced an earlier token-diff meter. The boredom README says that meter scored routine knob
changes at novelty 0.94-0.96 and never exceeded B = 0.25. A later Ig Nobel version blends in an
idea-tag Jaccard distance (`idea_weight` 0.7, tags from the dataset URL and variable names).

**Signals**, over a rolling window of the last k = 5 attempts (`fold()`):

- Novelty N: the mean "sparseness" of each window attempt, meaning its mean distance to its
  `knn` = 3 nearest neighbours in the whole archive of all agents (after Lehman and Stanley).
  Going back to an old basin, yours or another agent's, counts as not novel. The "novelty deficit"
  is 1 - N.
- Stagnation S: `min(1, units_since_significant_improvement / patience)`. "Significant" means
  beating the best by more than `eps`, a per-task noise floor (0.005 for Hadamard and PlayWorld,
  0.03-0.05 for BixBench). In all shipped profiles the unit is a round and patience is 3 (5 for
  Ig Nobel).
- Overload O: `max(fail_rate, trivial_rate)`. Crashes and invalid runs make it rise, and so do
  near-repeats (distance < 0.05 or a `duplicate` status). This is their version of Eastwood's
  U-shape: both too much chaos and too much repetition. In Ig Nobel a "safe pick" (predicted
  |rho| > 0.5) also counts as trivial (`safe_param`).
- Reported but not in B: frustration (failure share), Simpson diversity of mechanisms, and
  frontier stall (attempts since the best changed at all).

**Formula**: `B = 0.45*(1 - N) + 0.45*S + 0.10*O`, clamped to [0, 1]. Ig Nobel uses weights
0.4/0.4/0.2.

**Regimes** form a hysteretic state machine folded over the ledger in seq order. The thresholds
are snap 0.70, breakout 0.85 and exit 0.55:

- EXPLOIT: right after a significant improvement, or before `min_attempts`.
- CONVERGED: frontier stall >= 3 x patience and every required mechanism tried. It is sticky. The
  gate then allows only exact re-runs of the best.
- PARADIGM_BREAKOUT: B >= 0.85 and frontier stall >= patience. It stays while B >= 0.70.
- BOREDOM_SNAP: B >= 0.70.
- Hold the current non-EXPLOIT regime while B >= 0.55. Otherwise EXPLOIT.

The pitch's "seeking state" (Bench and Lench) is this regime switch. `boredom_meter/README.md`
maps each signal to a theory: MAC meaning gap to stagnation, MAC attention mismatch and Eastwood's
U-shape to overload, Berlyne and Zuckerman to the novelty deficit.

**Gate** (`gate()`): outside EXPLOIT, a divergent proposal is rejected if it is within 0.35 of a
recent attempt or within 0.25 of a failed or discarded one. BREAKOUT also rejects any top-level
mechanism used in the last k attempts. "Stuck" cells (2 of the last 3 attempts failed) are
blocked. `suggest()` returns the least-visited cells in a fixed order, so bored agents get
concrete targets.

### The swarm loop (`swarm.py`, 5,420 lines; `docs/orchestration.md`; `program.md`)

The conductor is plain code. Only scouts, workers and the critic call an LLM. One round:

1. **Measure.** `meter.status(ledger)` gives the swarm B and regime, plus a per-agent B scored
   against the shared archive.
2. **Assign** (`allocate_roles`). EXPLOIT gives 3 exploit and 1 divergent worker. SNAP gives 1
   exploit and 3 divergent. BREAKOUT gives 4 divergent. Agents with B >= 0.70 are picked first for
   divergent and scout slots, and ties break by name, so allocation is reproducible. Logged to
   `assignments.jsonl`.
3. **Scout** (`run_scouts`). A scout recalls "exhausted directions" from memory. An LLM writes a
   paper query and a code query. Exa searches both. An LLM picks one source, Exa `/contents`
   extracts it, and an LLM writes a "lead" (recipe, claimed effect, predicted delta, risk) to
   shared memory.
4. **Propose.** Each worker gets recalled results, leads, other agents' claims, its search cell,
   regime-specific "hard rules" (`hard_rules`) and the current file. It returns JSON: descriptor,
   idea, rationale, source ids, predicted delta and exact string edits. Bad JSON gets one repair.
5. **Gate.** The boredom gate plus an Exa prior-art search on the idea.
6. **Commit.** One git commit per candidate, with `Descriptor:`, `Source:` and `Prior-art:`
   trailers.
7. **Evaluate.** In a Docker sandbox with `--network none`, a read-only root, all capabilities
   dropped and limits of 4 GB, 1 CPU and 64 PIDs. The host grades bounded output and never
   imports candidate code (`testcases/sandbox.py`, `docs/architecture.md`).
8. **Critic.** A provisional new best needs a second evaluation plus an LLM review. A
   "contradicted" verdict vetoes it. The repo admits that for deterministic tasks the second run
   "reuses the first score" and is not a replicate.
9. **Record.** The ledger, `results.tsv`, `critic.tsv`, memory writes, round tags and a resume
   checkpoint are all written before the next round.

Arms (`docs/orchestration.md`): A has the meter off (fixed 3/1), B has it on, C and D are B
without Exa or without past.dev, and S is a single agent (1 worker, no meter, scouts or critic)
with 4x the rounds to match candidate count. Only A, B and S were ever run. In the confirmatory
runs every arm had Exa off and local memory.

Later additions, apparently unused in the published Kimi runs (inference from commit dates and
ledger fields): a "PI" that steers stuck agents and spawns up to two workers (`run_pi_steering`),
and an OpenAI supervisor-plus-8-workers mode with a $10 cost ledger (`docs/openai-swarm.md`).

### The judges

Committee verdicts are cached by (model, prompt, message), so a re-grade is identical. A missing
key or an HTTP 402 raises `grader_error` instead of quietly scoring 1.0; gate-only scoring needs
an explicit `IGNOBEL_COMMITTEE=off`. IgJudge writes `data/pages/igjudge/<run>.json`.

A separate, earlier Ig Nobel system lives off `main`. It has "mother" department heads (biology,
chemistry, physics) who meet "over coffee" and found "daughter labs" (commit `4642576`; branch
`agent/devin/families-pastdev`). It used a different boredom formula,
`B = clip(0.35R + 0.25S + 0.20U + 0.20K - 0.30E)`. Its four recordings ship in
`data/replays/ignobel-real-runs.zip` and `docs/recordings/`, generated with
`thinkingmachines/Inkling-2-Small` on Tinker.

## How they did on the benchmark(s)

I recomputed each headline from the committed files.

**Track 3 confirmatory set** (`experiments/confirmatory/`). The model was Kimi K3.1 Pro through
Tinker, with research off for BixBench and 18 held-out questions per run.

- Complete strict-guard set (all 12/12/48 rounds). Per seed 3001/3002/3003, from
  `results/bixbench_strict/seed-*/*/heldout.json`: A 0.167/0.056/0.056, B 0.056/0.056/0.056,
  S 0.333/0.222/0.333. The means are A 0.093, B 0.056, S 0.296. **Checks out.**
- Guard2 "preregistered primary" (truncated by the 402 cutoff, compared at matched budgets of 24,
  20 and 28 candidates), from `results/truncated_partial_runs.json`: A 0/0.167/0.167,
  B 0.278/0.333/0.167, S 0/0.444/0.278. The means are A 0.111, B 0.259, S 0.241. **Checks out.**
  Both zeros (A and S on 3001) are guard "crashes" on `bix-52-q3` from the `range(50)` loop-bound
  false positive. Unguarded, both are 0.333.
- `results/heldout_by_grader.tsv` shows the strict-set S lead appears only under the final grader
  fix `a0d4fcd`. The S means are 0.074, then 0.074, then 0.296 across the three graders. **Checks
  out.** The team flags this itself.
- Minimum sign-flip p with 3 pairs = 2/8 = 0.25. Nothing can be significant, and they say so.
- Tokens: B used 1,227k prompt tokens vs 692k (A) and 705k (S), so "about 1.75x" checks out.
  B's gate rejected about 19 of 48 proposals before evaluation.
- hadamard29: a 1-1-1 tie at the common round. Both arms land on the same two constructions, with
  losses 0.0998 and 0.0698 (`truncated_partial_runs.json`). **Checks out.**

**Single real runs** (`experiments/real-runs/`, all arm B, Kimi):

- BixBench mini: held-out baseline 1/7 (Wilson 0.026-0.513), best 6/7 (0.487-0.974), dev loss
  0.10 (`bixbench_mini-v2/held_out_test.json`). **Checks out.** One run, n = 7.
- PlayWorld mini: test loss 0.87225 to 0.2535 on 200 seeds, dev 0.28825
  (`playworld_mini/held_out_test.json`). **Checks out.** Synthetic.
- Hadamard19: loss 0 at round 5, at the known optimum (`hadamard19/results.tsv`,
  `converged.json`). **Checks out.**

**Ig Nobel Kimi run** (`experiments/real-runs/ignobel/ignobel_studies.jsonl`): 22 rows, of which
1 is a critic re-run, giving 21 registrations. 13 produced a test result and 11 were confirmed.
Ten have |rho| >= 0.1. The odd one is the street-sound x/y study, with rho = 0.070 and
p = 2.5e-85 on a large n. The largest confirmed p is 3.39e-7 (hermit-crab-style shell distance
vs aggression). **All check out.** Eight registrations failed before testing: three on
citations, five on dataset access (404, too few rows, non-open host). Cost is in
`ignobel/cost.json`: 116 Kimi calls (498,397 prompt and 137,636 completion tokens), 119 Exa calls
($0.761, p50 414 ms), 157 past.dev recalls (p50 577 ms) and 123 writes. **Checks out.** The run
stopped after 13 of 15 rounds when Tinker credit ran out (`run.json` status `stopped`).

**IgJudge on the Kimi run** (`data/pages/igjudge/50bbe2bb-....json`, gpt-5.5): 11 rankable
studies. The winner is the shell-distance aggression study at 47/100, classed "Low Potential".
Second is the weak x/y coordinate study at 42. So the post-run judge ranked the known failure case
second.

**past.dev vs local memory** (`experiments/memory-comparison/results.json`): hit@1 0.65 vs 0.30,
hit@8 0.95 vs 0.825, p50 latency 562 ms vs 15 ms, on 419 records and 40 probes. **Checks out.**
The probes were hand-written by the lead agent, which favours semantic retrieval. There is no
local-embedding baseline.

**Weak or unverifiable points I found:**

- **No kept improvement came from a bored regime.** I counted statuses by regime tag in the four
  real-run `results.tsv` files. All 11 kept candidates are `[EXPLOIT]`: BixBench 3, PlayWorld 5,
  Hadamard 2, Ig Nobel 1. All 69 SNAP and BREAKOUT proposals were discarded, rejected, duplicates
  or crashes. The repo never states this. It is the most direct evidence on the core hypothesis,
  and it does not support it.
- **Most gate rejections were schema errors, not boredom.** In the Ig Nobel ledger all 23
  rejections were "source_ids must use the S<number> format" (14) or "lead_id must reference a
  lead offered this round" (9). BixBench rejections were mostly out-of-vocabulary descriptors.
  Only a handful were real novelty-gate rejections (PlayWorld 4, BixBench 2).
- **The Ig Nobel meter saturated at once.** The Kimi Ig Nobel run used the pass/fail loss. It hit
  loss 0 at seq 2 (the x/y study) and frontier stall then only grew (15 by round 5). The meter was
  pinned in SNAP from round 5 on. Inference: in that run, boredom measured "nothing can beat 0",
  not real stagnation. The run also used profile version 2; the committed profile is version 3
  with different weights and the idea-tag novelty.
- **The pilot Ig Nobel A/B favoured the meter-off arm.** Arm A ran 28/22/31 studies vs B's
  23/20/27, and confirmed 21/12/17 vs 9/10/15 (`RESULTS.md` section 4).
- **The committee never scored the Kimi run.** It did score the off-`main` "families" runs:
  17 rows across `docs/recordings/*/artifacts/scores.jsonl`, max score 6 of 10, 8 "supported".
  But both judges there were the same model (Inkling-2-Small), so they were not independent.
- **The boredom README's synthetic bio-lab result** (meter-guided swarm finds the global basin
  20/20 vs 0/20) is a scripted toy with hand-placed targets (`boredom_meter/sim_biolab_swarm.py`).
  The "replay of Nov 14 autoresearch arms" evidence cites `replay_autoresearch.py`, which is not on
  `main`.
- **Held-out is weak by design.** Agents see the held-out question text and all the data; only the
  grades are hidden. They state this.

## Experiments, ablations and failures

- **Preregistration with a deviation log.** `PREREGISTRATION.md` was committed at 00:46 PST on
  Sunday (`2f7d634`), after launch but "before any confirmatory result was inspected". It fixes
  arms, seeds (3001-3006 and 2001-2008), rounds, a Holm-corrected primary family and a power note.
  `RESULTS.md` then lists six deviations, each with a commit, a time and a "direction of effect":
  a scope cut after a 402, a guard change, a primary-set switch, a held-out grader fix, an unfixed
  false-positive class, and lost slots. It also corrects two wrong timestamps in its own
  preregistration. The whole set is relabelled "exploratory after deviations".
- **The gold-literal guard is the main failure story.** An AST guard rejects source code that
  contains a gold answer as a numeric literal. It rejected 198 candidates (about 45% of attempts in
  every arm), all false positives (`power=0.8`, `ppf(0.975)`, `1e-10`, `range(50)`). They fixed
  two classes and reported unguarded scores for the third.
- **Confounds named.** S has no critic, so S > B "may reflect these acceptance rules". The critic
  is Kimi reviewing Kimi. Exa had "no measured effect (no ablation)". The same Orca paper appears
  as six Exa source cards. The Pima `bmi~skin` re-run on the test half "is close to tautological".
- **Ablations not run.** Arms C (no Exa) and D (local memory) exist but were never run. There is
  no ablation of individual meter components and no sweep of the weights.
- **past.dev update benchmark** (`experiments/memory-updates/updates.py`, README). With two ids
  per idea, past.dev returned the stale status 27% of the time. The stable-id arm "did not finish"
  because an update was still ingesting after 180 s.
- **Credit exhaustion shaped everything.** Both Tinker keys returned HTTP 402 around 00:53 and
  01:45 PST. That cut seeds from 6 to 3 and stopped the committee-scored rerun.

## How they used Exa, past.dev, Modal (and Tinker)

**Exa: central to the loop on non-BixBench tasks, with an unmeasured effect.**
- `research.py` owns all Exa calls (`/search`, `/findSimilar` with a fallback, `/contents`). It
  has a request cache, a call budget (`check_budget`) and stable ids (`Q<n>` for queries, `S<n>`
  for sources), all logged in `research_log.jsonl`.
- Scouts run a paper search, a code/dataset search restricted to open-data domains, then an
  extract (`swarm.py::run_scouts`). Workers in SNAP/BREAKOUT search their suggested cell
  (`worker_search`, at most 2). Each proposal gets an Exa prior-art search on its idea.
- For Ig Nobel, Exa results decide whether a dataset URL is downloadable: a host that is not an
  open-data host is allowed only if this run's Exa research cited it (`external.py`). The committee
  runs one Exa search per headline to supply prior-work evidence (`committee.py::_exa_search`).
- `literature.py` is a keyless fallback (OpenAlex, arXiv, PubMed) using the same `S<n>` format.
- BixBench turns research off (`"research": "off"`) to avoid answer leakage.
- Weakness: no Exa-on vs Exa-off arm was run, and the team says Exa "had no measured effect".

**past.dev: the deepest sponsor integration, with its own benchmark.**
- `memory.py` writes to the past.dev ingest endpoint and recalls with `queryTimestamp`,
  `occurredFrom`, `occurredTo` and `sort`.
- Scope is enforced by past.dev, not by local filtering (`ensure_native_scope`). Each run gets a
  reader identity `reader-<arm>-<run>` with traits, and two rule audiences: `run-<arm>-<run>` for
  claims, leads and assignments, and `lab-<arm>-<task>` for lessons, verdicts and failed results
  that later runs reuse. Spawned workers get private `solo-...` audiences.
- Each idea is one stable `hypothesis` record (`hyp-<arm>-<sha1(idea)>`) updated from "being
  tested" to confirmed, refuted or crashed. `rollback_memories` deletes abandoned rounds on resume.
- They mapped memory layers explicitly: working memory is the prompt, episodic is past.dev data
  points, semantic is past.dev memories, procedural is `program.md` and the prompts. The score
  ledger stays local as the source of truth.
- Measured: the retrieval comparison above, a before/after scope-leak table (267 out-of-scope
  results dropped by the local filter on `main`, 40 on the branch), and the stale-update test. The
  confirmatory runs used local memory, so past.dev never touched the Track 3 numbers.

**Modal: bolted on, then dropped.** Early commits on Nov 14 added a "configurable Modal GPU runner"
for a karpathy/autoresearch import (`be7aa39`, `60809b4`, `7017194`). A side branch ran Ig Nobel
study code in a Modal sandbox (`e5906fe`, `8a8725b`). `main` has no `import modal`; sandboxing is
local Docker.

**Tinker: the compute for every published run.** Kimi K3.1 Pro (`moonshotai/Kimi-K3.1-Pro:peft:262144`)
through Tinker's OpenAI-compatible HTTP endpoint, with no SDK or torch (`llm.py`,
`docs/autoresearch-backend.md`). The judges are gpt-oss-120b and Qwen3.5. The families runs used
Inkling-2-Small. Sampling only; no fine-tuning. Running out of Tinker credit is the main reason
the study was cut short.

## Autonomy and self-improvement

- A round needs no human, and runs resume from checkpoints (`swarm.py --resume`). Missing keys
  or a missing sandbox image are reported before anything starts.
- The meter is self-regulation in a narrow sense: the swarm changes its own role mix from its own
  ledger. Workers can extend the vocabulary with `other/<slug>`, logged to
  `vocab_extensions.jsonl`.
- The team explicitly does **not** claim self-improvement. "Cross-run lessons exist in code but
  were not active in the reported runs" (`docs/judge-qa.md`).
- The development process was heavily agentic. Devin opened and merged many PRs. `.agents/skills/`
  holds "scar-tissue rules from adversarial reviews". `docs/judge-qa.md` says it answers questions
  from "two hostile-judge reviews". Inference: they ran AI reviewers as adversarial judges and fed
  the findings back into code and docs.

## Presentation and judge-facing craft

**The Lab viewer** (`src/main.ts`, `src/engine/*.ts`, `backend/replay.py`; built into `docs/` for
GitHub Pages). It is a Three.js, TypeScript and Vite first-person room that replays a recorded
`swarm-viewer/v1` archive (`docs/swarm-viewer-api.md`).
- `backend/replay.py` folds native events into per-tick snapshots. It deliberately does not trust
  tick-zero keyframes, because "they already contain later state". Zod validates the boundary.
- Controls: Play, scrub and pace; drag-to-look, WASD and Q/E during playback; "Walk inside" when
  paused; press E near a researcher. Speech popups quote recorded statements only.
- Agents are "happy cartoon androids" with deterministic head, face and antenna variants. Boredom
  drives their body language (`src/engine/affect.ts`): walk speed drops, the head droops, the body
  slumps, colour desaturates, and above 80% they show "zzz".
- A regime swimlane, an evidence panel, Exa source cards that resolve `S<n>` back to the logged
  query, a provenance badge that detects credit errors (`provenanceBadge.ts`), and an "empty
  pre-committee podium" with a "How it's judged" sheet (`finalPodium.ts`, `igJudge.ts`).
- The README states the room's movement "is illustrative". Values, order, identities and
  statements come from source records.

**Video** (`video/`, Remotion 4, 120 s, silent, 1920x1080). `video/SPEC.md` is a fixed script with
nine scenes. Facts are pulled from `intro.json` at build time. Caveat cards must be "at least 60%
of the headline size". The live section is a real Playwright capture of the committed Pages site,
sped up by a measured factor and labelled "replay of a real run (Kimi + Exa + past.dev)".

**Humour and branding.** The project name ("ASS") is the joke. An earlier intro headline read
"the Ig Nobel research prize was discovered early by a phenomenal breakthrough" (git history of
`data/pages/intro.json`). The video tags it a "tongue-in-cheek headline — real results follow"
(`video/src/content.ts`). The hook "The secret: making AI less boring" frames the whole pitch.
Mother department heads meet "over coffee", fitting the team name. A citation card awards a
"Kitchen Physics Lab" for "heavier cars get fewer city miles per gallon", with the note "not a
prize award".

**Honesty statements as a technique.** Caveats sit next to every number, in the same sentence:
"known relationships, not discoveries", "Tiny n", "a ceiling, not a record", "No consistent winner
is shown", "Fake-LLM runs ... are never reported as results". `docs/judge-qa.md` pre-answers the
hardest questions, including "Does the swarm beat a single agent? No consistent winner is shown."
and "Ten hard questions" with unfavourable answers. The repetition is heavy. The same Track 3
caveat sentence appears near-verbatim in `README.md`, `docs/results.md`, `RESULTS.md`,
`docs/judge-qa.md` and `data/pages/intro.json`. Inference: they kept one canonical sentence and
copied it everywhere so no surface could overclaim.

**Reproducibility kit** (`REPRODUCE.md`): three levels (verify, regrade, rerun).
`scripts/verify_results.py` (standard library only) recomputes each headline from raw ledgers and
prints ok or FAIL. `docker run --rm caffeinated-repro` runs it. A release workflow publishes pinned
images to GHCR, base images are pinned by digest, and a Hugging Face dataset
(`onasafari/caffeinated-runs`) holds five JSONL tables plus checksums. The export refuses to run
if any file looks like it holds an API key.

## Why it stood out, scored against the five criteria

- **Research rigour (strong).** Preregistration, matched candidate budgets, held-out grading done
  once, a leak guard, Wilson intervals on n = 7, exact sign-flip tests with the minimum attainable
  p stated, and a deviation table with the direction of each effect. The weaknesses are real (3
  runs, post-hoc headline choice, grader fix after the winners were known), but the team named
  every one before a judge could.
- **Novelty (moderate).** Novelty search, MAP-Elites cells and stagnation triggers are prior art,
  and the team says so. What is new is the packaging: a deterministic, hash-verifiable,
  psychology-framed controller that any agent can recompute, plus the Ig Nobel "laugh then think"
  objective scored by independent judges who see only a host-written abstract.
- **Results (weak to moderate).** There is no measured benefit of the meter or the swarm. The
  positive numbers (BixBench mini 1/7 to 6/7, PlayWorld 0.87 to 0.25, Hadamard19 at the optimum)
  are single runs on proxies. The live demo works as a replay, not a live run.
- **Autonomy and self-improvement (moderate).** A fully automatic round loop with resume and
  meter-driven role allocation. Self-improvement is explicitly not claimed.
- **Interpretability and reproducibility (very strong).** This is where they spent the most effort.
  B is a pure function with hashes. Every LLM call is logged with a prompt hash. There is a replay
  UI for the full trace, a one-command verifier, pinned images, a public dataset and a knowledge
  graph (`docs/knowledge-graph.html`).

Inference on why it read well to judges: the honest negative result plus heavy reproducibility
tooling turns "we did not win" into "we can prove exactly what happened". The 3D replay and the
humour make that memorable.

## Build process

- **Timeline.** The first commit is 10:45 PST on Saturday, Nov 14, and the last is 14:06 PST on
  Sunday, before the 14:30 deadline. The busiest hours (Pacific): Saturday 16:00 (33) and 21:00
  (31); Sunday 00:00-04:00 (104 commits, the overnight runs and confirmatory study); Sunday
  11:00-13:00 (122 commits, write-up, verifier, dataset and release).
- **Shape of the work.** Saturday afternoon: import karpathy/autoresearch, add the meter
  (`3c718e5`), port it to descriptors (`68dcf64`); in parallel the Ig Nobel families system and the
  lab engine (`dd64038`). Saturday evening: the orchestrator (`4a66cc7`), testcases (`c829d37`),
  Tinker backend, merge to `main` (`26d4763`, 19:39). Overnight: real Kimi runs (about 00:09-01:10
  PST), preregistration, credit exhaustion, confirmatory runs. Sunday: honesty passes, verifier,
  HF dataset, release workflow, IgJudge and podium, and an Ig Nobel meter upgrade at 13:39.
- **Contributors** (git authors). Ariel Anders made 245 commits (swarm, meter, results). Jialin
  Feng's accounts made 57 (Ig Nobel, committee, knowledge graph, past.dev fixes). Masha Baidachna
  made 51 (Lab viewer, podium). An account inferred to be Neeraj Shah made 7 (testcases) and
  another unnamed account made 3. Devin accounts made 27, and one commit is by "Antigravity Agent"
  (the autoresearch import). 20 commits are GitHub PR merges. A second Ig Nobel system, with its own
  boredom formula, was built as a separate `ignobel/` package and never merged into the swarm.
- **Tooling.** `AGENTS.md` sets the rules for humans and agents together: own branch and worktree
  per task, never overwrite others' work, merge to `main` only when a human asks. `.agents/skills/`
  has a workflow skill and a guardrails skill, and `.devin/wiki.json` is present. CI
  (`.github/workflows/check.yml`) builds the sandbox image and runs `make check`, the backend
  tests, the npm build and npm tests. I count about 464 Python `def test_` functions and about 174
  JS `test(` calls (by grep, not run).

## Lessons we can reuse

Ranked by how much they would help a team like ours.

1. **Pre-write one honest caveat sentence per headline number and paste it everywhere.** The
   README, docs, UI, video and judge Q&A all say the same bounded thing, so no surface overclaims.
   Write it before the demo, not after.
2. **Ship a one-command verifier that recomputes every published number from raw artifacts**
   (`scripts/verify_results.py`, standard library, ok/FAIL per line, inside a pinned Docker image).
   It turns "trust us" into "run this".
3. **Pre-register, then keep a deviation table with commit, time and direction of effect.** Even
   when the plan collapses (credit ran out), the table keeps the result credible and lets you
   relabel honestly as exploratory.
4. **Write a hostile-judge Q&A before judging**, including "Ten hard questions" with unfavourable
   answers. Use AI reviewers as adversarial judges and save the fixes as rules for agents.
5. **Make the controller deterministic and hash-stamped.** A meter that is a pure function of
   (ledger, profile), with `ledger_sha256`, is easy to audit, replay and visualise. Measuring
   novelty on declared descriptors rather than diffs fixes the "every diff looks new" failure.
6. **Separate measurement from policy, and put sponsor tools around the score, not inside it.**
   Exa and memory are used in response to the signal, so the signal stays reproducible.
7. **Use the sponsor's native features and measure them.** Their past.dev work used identities,
   rule audiences, stable ids and time-bounded recall, plus a small retrieval benchmark with
   stated bias. That fits a sponsor rubric better than "we stored notes".
8. **Replay, don't demo live.** A deterministic replay of a real recorded run, with provenance
   badges and a credit-error detector, survives a dead API key on stage.
9. **Check your own core claim directly.** They never counted which regime produced the kept
   improvements; in their runs it was always EXPLOIT. Count this before anyone else does.
10. **Reserve model credit for the confirmatory study, and guard outputs, not source.** Two 402s
    cut 6 seeds to 3. A source-literal leak guard rejected 45% of attempts as false positives.

## Open questions / things I could not verify

- Whether the Pages site and video actually render as described (I did not open the site or run
  any build). The byte-identical Pages build claim is unverified.
- Which recording the Pages site opens by default. `README.md` says it exports "one real
  Kimi/Tinker Ig Nobel run". `docs/openai-swarm.md` says the default replay is run 3 of the
  families ZIP. `docs/recordings/` contains all five.
- How the judges scored the project; the repo does not say.
- Whether the guard2 and strict-guard runs truly used the stated commits and settings. I read the
  `run.json` files for the Ig Nobel run only.
- The "Nov 14 autoresearch replay" evidence for the meter (`replay_autoresearch.py` is not on
  `main`), and the "before/after" past.dev scope-leak table (run directories are not committed).
- Neeraj Shah's commit identity is inferred from the testcase commits; the git author names differ
  from the README names.
- No credential appeared in a filename-only scan for common key patterns (gzipped logs not
  inspected).
