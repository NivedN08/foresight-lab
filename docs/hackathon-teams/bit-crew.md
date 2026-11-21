# Bit Crew (team GLHF, Track 3): what they built and how they did

Repository: `github.com/itsbrendandang/agent-test`, read from a local clone (main at `2e6468a`, plus the
remote branches that came with the clone). Nothing in the repository was run. Every number below is
either read from a file named next to it, or computed by me from committed records with `git show` and
`jq`. Inferences are marked as such.

## One-paragraph summary

Bit Crew is a web app plus engine in which a "team review" of AI agents (a lead scientist and six to
seven members) researches a biology question, and every statement an agent makes is checked against
its cited source by a model from a different company, with code (not a model) deciding the mark. The
headline in the README is a benchmark score: a 7-agent team scored 0.537 (132 of 246) against one
agent at 0.240 (59 of 246), "above the record of 0.523" for BixBench3. That number is the weakest part
of the submission. The run records are not in the repository, and the evidence in the repository
says the run was on the public BixBench v1 dataset (20 capsules, 82 questions, 3 repeats), not on
BixBench3. The team's agent ran uncapped on Claude models with R installed. The baseline was gpt-5.1
with no R. What made the project stand out is everything around that number: a very polished app (a
live animated "Agent Lab", a group-chat log of every agent turn, 3D claim graphs, protein structures),
a genuinely careful verification design (cross-vendor quote checks, pre-registered falsification
tests with e-values, a blind critic enforced by a filesystem sweep), and an unusually explicit
honesty apparatus ("Demo and Real", "Where things stand", a status table with evidence for every
feature). Several of the strongest honesty caveats were removed from the README in the last hour.

## Team, track and challenges entered

- Team GLHF: Brendan Dang, Anh Lam and Josh Bustamante (`README.md` line 6). Commits also come from
  "Josue Bustamante" (a different email); I infer this is the same person as Josh.
- Track 3, AI Scientist Swarms. The README quotes the track brief in full (`README.md`, "The brief").
- Sponsor tools: Exa, past.dev and Modal are all described in a "Sponsor tools" table and a "How Modal,
  Exa and past.dev work in the system" section (`README.md` lines 517-618). There is no README section
  titled "past.dev challenge" with Agents, Layers, Results and Caveats, so they did not follow that
  sponsor's requested format. Tinker is listed as "Not used".
- Product name: "AI Scientist Bot" until 12:47 PT on 15 Nov, then "Bit Crew" (commit `2f2a9b6`).

## The problem and the benchmark(s)

Three different benchmarks appear in the repository. Keeping them apart is the key to reading it.

| | BixBench3 | BixBench v1 / v1.5 | Their demo case |
|---|---|---|---|
| Source | Edison Scientific; vendored copy in `glhf/bixbench/` at upstream commit `d0e0bbb` | FutureHouse, Hugging Face `futurehouse/BixBench`: 205 questions on about 60 data capsules | `glhf/demo_case/` |
| Task | Rebuild a published study from raw data; 20 tasks | Answer questions about one capsule's data | Toy task |
| Scoring | Output artifacts graded against published tables with checks such as F1 and CCC, plus a process judge (`glhf/bixbench/README.md`) | Per-question correct or not; LLM judge (gpt-4o) for open answers | Official grader |
| Runtime | One GCP VM per task, "up to 24 hours, with an average of approximately 8 hours" (`glhf/bixbench/README.md`) | Minutes per capsule | Seconds |
| Record | 0.523, the number Track 3 asks teams to beat | No record used here | n/a |

The track judges "score vs. the record and a single-agent baseline on the same compute budget, rigour,
reproducibility" and asks teams to report what the swarm rejected (`README.md` lines 435-455). The
record 0.523 is hard-coded as `RECORD` in `engine/src/glhf/types.ts` line 170. The headline chart
labels it "gemini-4-argon" (`docs/images/bixbench3-results.png`); nothing else in the repository names
the record holder.

## What they built, mechanism in detail

The repository holds five separate systems that share a dashboard. Only the first is the product a
user sees; the others are benchmark harnesses.

### 1. The engine and the team review (`engine/`, `board/`)

- `engine/` is a TypeScript server on port 8787 (about 15,900 lines in `engine/src`), built on "Pi
  Durable", a durable agent harness. Every paid or outside step is stored under a stable key, so an
  interrupted review resumes without paying twice (`engine/src/research/pipeline.ts` header comment;
  `docs/pi-setup.md`).
- A team review is called "board" in code (`engine/src/board/board.ts`, 1,002 lines). The roster is in
  `board/config.yaml` and the prompts in `board/agents/*.md`:
  - **Lead scientist** (Claude Sonnet 5.5): frames the question, addresses the board with `converse`,
    names the experimenter's mode, ends with `write_memo`.
  - **Scout** (Claude Haiku 5.5): the only member with web search (`exa_search`), plus free literature
    and database searches. It replies first in every round.
  - **Evidence verifier** (Haiku, added 15 Nov, commit `d982f59`): rechecks each scout source (does the
    URL come from a real search, does it say what the scout says, is it current or retracted) and
    searches for missed or contrary evidence. Ends with Confirmed, Wrong and Missing lists
    (`board/agents/evidence-verifier.md`).
  - **Proposer, experimenter (pilot, full and revise modes), methods auditor, reporter** on Sonnet.
  - **Critic** on OpenAI `gpt-5-mini`, deliberately "a different vendor from `lead`".
- Rounds: round 1 is blind; from round 2 each member sees the others' last replies. Caps: 4 rounds,
  20 lead calls, $5 and 15 minutes per review, $20 per day (`board/config.yaml`, `meeting:`).
- The board has a simple memory: after each run, each member's checked claims are appended to
  `board/expertise/<member>.md` and read into that member's next run (`memory:` in `board/config.yaml`).
  The committed expertise files are empty headers.

### 2. The claim-checking pipeline ("a second AI model, from a different company")

This is the README's lead mechanism (`README.md` lines 19-22). The code matches the description.

1. Each member ends its reply with a `CLAIMS:` section, one line per claim, tagged `[literature]`,
   `[computed]` or `[brief]` with `(source: ...)`. Plain code parses it; no extra model call
   (`engine/src/board/claims.ts`).
2. **First pass** (`engine/src/board/triage.ts`): a cheap model answers in strict JSON with a verdict
   word and a verbatim quote from the source. The model is chosen by `firstPassModel` in
   `engine/src/providers.ts`: it must be from a different vendor than the member that made the claim
   (Haiku checks the OpenAI critic; `gpt-4o-mini` checks everyone else).
3. **Code decides** in `route()`: a "supports" ruling becomes `verified` only if the quote is at least
   20 characters, is found in the source text after normalising case, spacing and quote marks, and
   contains every number in the claim (with sign and decimal rules, so "5" does not match "5.8").
   Malformed JSON, a missing quote, a missing number, or no independent vendor all become `escalate`.
   "Nothing malformed is ever verified" (triage.ts header).
4. **Escalation** (`engine/src/board/checkers.ts`): contested literature claims go to an OpenAI model
   (or Claude, if the author was on OpenAI), which must open with `VERDICT: verified|unsupported|
   contradicted`; anything else is recorded as unsupported. Computed claims go to a headless Claude Code
   in `--restricted` mode with an allow-listed environment in an empty temp folder. Because restricted
   mode cannot run code, computed claims are recorded as not checked, with the reason. At most 12
   escalations per run.
5. The code states its own limit plainly: a real quote "does not prove that the passage supports the
   claim", and a web page can be written to be quoted (triage.ts header).

The **direct research answer** (`engine/src/research/pipeline.ts`, 1,669 lines) reuses the same rule.
A planner writes 2 to 4 competing hypotheses, each with `supportIf` and `contradictIf`. At most 3 go
forward, one per Exa search. One investigator per hypothesis files sentences "For" or "Against". A
statement counts only if it passes the quote check and a model from another vendor, not told the side,
agrees on its direction (`check:stance`). `markOf` (line 491) then gives Supported, Contradicted,
Insufficient (including "the sources disagree") or Not checked, and flags a mark resting on one source
as "thin". A writer drafts; a statement checker and a critic, both from another vendor, review; one
revision; changed sentences are checked again. Literature comes from six free sources at once (Europe
PMC, OpenAlex, Semantic Scholar, arXiv, Crossref, bioRxiv/medRxiv; `engine/src/search/scholarly.ts`),
retracted papers are dropped via OpenAlex (`830654b`), and six public databases (ChEMBL,
ClinicalTrials.gov, openFDA, Open Targets, UniProt, PubChem) are queried without keys
(`engine/src/sources/`). Status: "No real AI model has written a direct answer yet" (`README.md` lines
418-419). It is switched off behind `RESEARCH_PREVIEW`.

### 3. The nine-role swarm for BixBench3 (`glhf/bixbench/src/bixbench3/inspect_harness/swarm/`)

The most rigorous design in the repository, and never run on a real BixBench3 task.

- Nine phases fixed by code, no supervisor model: scout, proposer (3 to 8 claims), pilot (subsample),
  experimenter, methods auditor, critic, reviser, finisher, reporter. Each has an empty context and a
  fixed share of the time budget (`glhf/docs/DESIGN.md` section 2).
- The **claim ledger** (`ledger.py`, 790 lines) follows the Popper paper. The critic registers each test
  as a script with its SHA-256 hash before any result; a separate relevance judge rates the test without
  seeing a result; the harness, not a model, runs the registered file and reads
  `GLHF_RESULT p_value=...` from the last line; `e = 0.5 / sqrt(p)`; the arithmetic mean of e-values
  across tests (valid under any dependence); Bonferroni over claims at alpha 0.1. A favourable result
  typed in by a model is refused; a contradicting one is always accepted.
- A simulation in `tests/unit/test_glhf_ledger.py` shows the false-support rate under eight critic
  behaviours, including the one the code cannot stop (trying 100 scripts privately before registering:
  0.224 for one claim). `DESIGN.md` states this limit plainly.
- A "contract check" reads each output table exactly the way the grader does, and a finisher rescues
  missing artifacts with real data only.
- Status: "No BixBench3 task has been run" (`glhf/docs/DESIGN.md` line 6). A real model ran it on the
  demo case only: swarm 1.0 with 284,532 tokens, single agent 1.0 with 18,644 (`docs/status.md` lines
  27-28). A branch pushed at 16:33 PT, after the deadline, tunes it for artifact score
  (`origin/benchmark-score-improvements`, "Untested on real data").

### 4. The three-role swarm for BixBench v1.5 (`glhf/bixbench_v1/`)

An analyst derives answers; a **blind critic** derives them again from nothing; a reconciler must
re-derive any disagreement by a script the harness runs. Blindness is enforced in code: before the
critic starts, root kills the analyst's processes, moves (not hides) every file the analyst's user owns,
empties `/outputs`, `/dev/mqueue` and System V IPC objects, then verifies all of this as the critic's
user. Agreement by the same method (token overlap of the one-line method descriptions of 0.5 or more) is
flagged as weaker. Budgets are equal by default; an "extended" run is labelled "more budget". The report
pairs runs by capsule and uses a sign-flip permutation test (`glhf/bixbench_v1/README.md` lines
164-345). These changes came from two pilot failures documented in `docs/submission/WRITEUP.md`:
"budget starvation" and "agreement without independence" (on capsule bix-29 all 4 "replicated" answers
were wrong). The fixes were tested with a scripted model only.

### 5. The board on BixBench v1 (`glhf/bixbench_board/`, branches only)

This is the system behind the 0.537. It exists only on the unmerged branches
`origin/board-cell-timeout` and `origin/devin/1794762286-bixbench-board`. It plugs the app's board
(lead plus the `board:` list) into the single-agent harness in `bixbench/bixbase/`, sharing loader,
sandbox, budget and grader. On the benchmark the scout and evidence verifier cannot search (they read
the data), the experimenter is the only member that runs code (Python or R), and "there are no claim
checkers" (`glhf/bixbench_board/bixbase/board.py` docstring). So the cross-vendor checking that defines
the product played no part in the benchmark result.

### The app (`dashboard/`, about 33,000 lines of TypeScript, 61 test files)

Read from code and the README, not run:

- **Overview**: a "Let's research together!" box with Quick, Standard and Deep; "Try asking" questions
  pulled from Google Trends with a word filter (`engine/src/search/trending.ts`); a six-room **Agent
  Lab** (Discovery, Planning, Experiment bench, Evidence review, Quality control, Report studio) in which
  characters walk only when the engine reports a step (`dashboard/src/lab/`).
- **Research**: a streaming chat answer with numbered sources, a mark beside each cited sentence,
  hypotheses with sources for and against, "What is missing", source images, and an inspector with
  Evidence, Hypotheses, Results, Activity and Structure tabs. The Structure tab folds proteins with
  ESMFold or falls back to AlphaFold DB, coloured by pLDDT (`engine/src/structure/fold.ts`).
- **Agent Log**: every event as a group chat with agent portraits, "Show thinking", filters, and
  JSON/CSV download. **Team review**: recommendation first, then counts of checked statements.
  **Graph**: a 3D graph of members and statements, with "Next contradicted one". **Agents**: member
  cards. Every agent, run, source and statement links to the others through the URL hash
  (`dashboard/src/lib/links.ts`).
- **Settings**: Demo or Real with a typed confirmation, spend today against limits, light and dark.
- **Benchmarks** and **Compare** are hidden from the sidebar and open only by address.
- Hosted on Modal in Demo mode behind per-teammate access codes (`deploy/modal_host/`); an optional
  Supabase copy of records (`supabase/migrations/`, `engine/src/db/supabase.ts`).

## How they did on the benchmark(s)

### What the README claims

| Claim (`README.md` lines 409-477) | Evidence in the repository | My check |
|---|---|---|
| BixBench3: 7-agent team 132/246 (0.537), one agent 59/246 (0.240), record 0.523 | A chart image (`docs/images/bixbench3-results.png`) and text. "Run folder on the cloud machine only" (`docs/status.md` on `origin/board-cell-timeout`) | Single agent verified. Team not verifiable. Benchmark mislabelled. See below. |
| v1.5 pilot: 4/8 vs 4/8, Sonnet 5.5, 317,110 vs 532,394 tokens | `glhf/bixbench_v1/runs/pilot-sonnet-5-5/report.md`, gitignored | Not in the repository. `CLAIMS.md` says a teammate read it. |
| v1.5 grid: Qwen3.6 35B on Modal, 15 vs 19, 42 of 82 ungraded, p = 0.29 | `glhf/openmodels/README.md` lines 232-252; run folder not in git | Consistent between docs. The openmodels README itself says "Do not quote a score from this". |
| One real team review: 204 statements, 71 supported, $1.31, 8 minutes | README text only (commit `dc37c8d`) | Run folder not kept. `docs/status.md` line 169 still says "The repository holds no record of a finished live run." |

### The 0.537 run, checked

1. **It is BixBench v1, not BixBench3.** The bixbench_board README on the branch gives the command:
   `--studies bix-1,...,bix-14,bix-16,...,bix-21 --repeats 3`, judged by gpt-4o, on "BixBench v1
   questions". The chart's 20 labels are exactly bix-1 to bix-21 without bix-15. These are v1 capsule
   names (`glhf/openmodels/capsules.json` lists them with question counts). BixBench3 numbers tasks 1
   to 20 and scores artifacts, not "fraction of questions judged correct", which is the chart's x-axis.
2. **246 is 82 questions times 3 repeats, not 246 questions.** Using the question counts in
   `capsules.json` and the single-agent records, the 20 capsules hold 82 questions. I read each blue
   dot off the chart, multiplied by 3 times that capsule's question count, and the sum is exactly 132.
   The same for the grey dots gives exactly 59. So the chart is internally consistent with v1 capsules.
3. **The single-agent number checks out.** Branch `origin/bixbench-nobudget-baseline` (commit
   `3713242`, authored by Devin, 14 Nov 23:29 PT) commits 60 `result.json` files. Summing
   `metrics.n_correct` and `n_total` gives 59 of 246 and 4,981,176 tokens, gpt-5.1, no token or time
   cap. 40 of its answers say the analysis "cannot be determined", mostly because R and Bioconductor were
   missing (for example `bix-1/rep0`).
4. **The team number cannot be checked.** No board `result.json`, transcript or summary is committed
   on any branch. `docs/status.md` (main, line 30) says "reported, not checked" and "the model, the
   budgets and the grading are not known here".
5. **The comparison is not like for like.** From the branch's `docs/status.md`: the board ran uncapped
   (no token or time limit, tool calls 10^9) on Sonnet 5.5, Haiku 5.5 and gpt-5-mini, with R 4.4.3 and
   DESeq2 in a Modal sandbox, using 18.1M tokens. The baseline ran on gpt-5.1, without R, with network
   in its cells, using 4.98M tokens (3.6 times less). Two board runs were rerun after fixes (bix-16 rep0,
   bix-4 rep0). The README's own fairness rules ("Same compute budget", "Same rules for both. Same
   model") describe the v1 adapter's design, not this run.
6. **The closest controlled comparison was a tie.** A 5-member board with gpt-5.1 for every member, under
   the baseline's caps (500k tokens, 90 tool calls, 600 s): 0.289 (71/246) against 0.260 (64/246); better
   on 22 questions and worse on 18 of 82, sign test p = 0.64, "not significant" (commit `96ae7aa`, branch
   `origin/devin/1794762286-bixbench-board`). The board had R and the baseline did not, even there.
7. **The caveats were written, then removed.** Commit `5ecae64` (13:10 PT) said "The models differ, so the
   gap is not the effect of the board alone." Commit `f23c5cc` (14:00) reworded it as "the optimized
   system's gain" of 29.7 points. Commit `25ae511` (14:27) added the result to the README with a list of
   what could not be checked, including that the study labels "are not BixBench3's task numbers". The
   squash of PR #57 (merged 14:32, two minutes after the deadline) dropped those notes ("The caveats
   will be presented in person") and removed the mentions of Devin. The final README says "Done on
   BixBench3: one run".
8. **Other files disagree with the README.** `docs/submission/WRITEUP.md` still opens "We do not know"
   and says BixBench3 "has never been run". The app's own Benchmarks screen would show "We have not run a
   real BixBench3 task", because it counts only run records (`dashboard/src/overview/Benchmarks.tsx` line
   94, `benchmarkNumbers.ts` `record3Of`). The README says that screen "shows these rows"; it does not
   show the 0.537.

Bottom line: the evidence supports "on 20 BixBench v1 capsules, an uncapped Claude-based board with R
scored 132 of 246 graded attempts; an uncapped gpt-5.1 agent without R scored 59". It does not support
a comparison with the BixBench3 record, or a claim that the team structure caused the gap.

## Experiments, ablations and failures

- **Demo case, real model:** swarm and single agent both 1.0; the swarm used about 15 times the tokens.
  A swarm capped at 70,000 tokens scored 0.0; at 200,000, 1.0 (`docs/status.md` lines 27-29).
- **v1.5 pilot (8 questions):** tie at 4 of 8; two failures found and fixed in code (above).
- **Capped vs uncapped single agent:** 64/246 vs 59/246, "within noise" (`bixbench/AGENTS.md` on
  branch `3713242`). A real ablation, with records committed.
- **5-member board vs single, same model and caps:** 71 vs 64, not significant (above). The most
  informative experiment in the repository, and it is not in the README.
- **Ablation flags** `--no-auditor`, `--no-pilot`, `--no-retriage` exist in the claims engine
  (`swarm/flags.py`) but were never run with a real model.
- **Open-model grid:** Qwen on Modal, 40 runs, $6.14; most runs hit the token cap and gave forced answers.
- **Self-reported mistakes** (`WRITEUP.md` "What we got wrong"): nine roles built before one was
  measured; planning for BixBench3 without access to it; 42% derivation shares too small; "replicated"
  answers that had seen a hint; docs that drift.

## How they used Exa, past.dev and Modal

**Exa: central to the app, absent from the benchmark result.** `engine/src/search/exa.ts` follows
Exa's own skill (query plus `objective`, highlights, type auto) and is guarded by a lifetime spend
ledger with a $40 ceiling (`engine/src/paid/exaledger.ts`). Uses: the scout opens every team review with
one Exa search; in research answers each hypothesis gets its own Exa search and the cap of 3 searches
decides which hypotheses are tested ("Exa: triaging hypotheses", `README.md` line 572); in the swarm,
`search_docs` limits Exa to 9 documentation sites and refuses queries naming the study, so it finds
manuals, not answers (`swarm/tools.py`, `swarm/guards.py`). Real calls: 3 searches in the one real
team review, plus searches by hand.

**past.dev: designed carefully, never used in a real run.** `swarm/past_memory.py` sends only lessons a
person approved, recalls with a fixed query so task text never leaves the machine, and drops lessons
from the same task or any task in their "context". A code filter removes lines that look like results
(`swarm/memory.py`). The committed `glhf/memory/lessons.md` holds seven process lessons from the demo
task. The engine, team review and chat do not use past.dev (`docs/proposals/memory-and-store.md`).
Bolted on.

**Modal: real and useful.** Three uses with evidence: (1) the hosted Demo app, with a spend ledger of
about $0.03 to $0.06 (`deploy/modal_host/LEDGER.md`); (2) Qwen3.6 35B served with vLLM on an L40S for
the 82-question grid, $6.14 (`glhf/openmodels/`); (3) code cells for the 0.537 board run: one sandbox
per run with a pinned R image, no network, data on a Volume, and all 60 runs at once, "about 41 minutes
from start to end, for 244 minutes of agent time summed over the runs, and 1.61 USD" (bixbench_board
README on the branch). The swarm's `ANALYSIS_BACKEND=modal` path is tested with fakes only. The late
README edits (`#58`, 14:56 PT) stress that Modal runs "experiments in tandem".

## Autonomy and self-improvement

- **Autonomy within a run** is code-orchestrated, not model-orchestrated. The swarm's phase order,
  budgets and stop rule are fixed in `swarm/solver.py`; the board's lead decides tasks but code enforces
  caps and checks the memo's sections. The engine resumes after a crash without repeating paid steps.
- **Self-correction** is real in design: refuted claims automatically raise a major issue that sends the
  reviser back (`flag_refuted` in `ledger.py`); in answers, sentences that fail their check after
  revision are not shown as holding up.
- **Self-improvement across runs** is thin: board expertise notes (empty in the repo), and swarm
  lessons gated by a human. A standalone `autoresearch.py` ("AI proposes + runs its own experiments") sits
  on an unmerged branch (`origin/add-autoresearch`, 13:36 PT).
- **The team itself was highly agent-driven** (see Build process), which is arguably their most
  autonomous loop.

## Presentation and judge-facing craft

- **Lead sentence**: "Ask a science question. A team of AI agents researches it." The "Why we built it"
  section explains the product in four numbered steps.
- **"Demo and Real"**: a four-row table (answers, sources, cost, keys) and a step list for turning Real
  on. Demo runs are labelled "Practice" everywhere, and examples say "Example · practice, $0". The
  engine starts with paid calls off (`NO_PAID_CALLS`, `engine/src/paid/paid.ts`), so a judge can run the
  whole app free.
- **"Where things stand"** opens with "We say only what was really done." and splits items into "Works
  today", "Tested with real AI models, in a small way", "Done once", "Built, and tested in Demo mode
  only", "Not done yet". One line reads "One run is not evidence that the reviews are good."
- **"Words"**: an eight-row plain glossary (agent, team review, swarm, practice run, engine).
- **"Limits you should know"**: "Supported" only means a second model found a real passage; it is
  "not medical advice".
- **`docs/status.md`**: a table of every feature with Status and Evidence columns, often in bold "Not
  used in a real-model run". `docs/submission/CLAIMS.md` tags each claim "verified-by-me", "taken from
  docs" or "untested". The demo script tells the presenter to say the scripted demo "shows nothing
  about science".
- **Screenshots** of every screen in `docs/images/`, a Mermaid system diagram, and a headline chart with
  the record line drawn between the two scores.
- **The tension**: this honesty style is the project's signature, and the final README breaks it at
  the most important point by presenting an unchecked, mislabelled, unequal-budget number as beating the
  record. A judge who opens `docs/status.md` or `WRITEUP.md` finds the contradiction.

## Why it stood out, scored against the five criteria

My estimate of how a judge would see it, with what holds up under a close read.

| Criterion | Likely judge view | What holds up |
|---|---|---|
| Research rigour | High: e-values, pre-registration, blind critic, paired tests | The design is rigorous; none of it produced the headline number. The controlled comparison was a tie. |
| Novelty | High: cross-vendor quote-checked claims; hypotheses triaged by search budget; Popper-style ledger in code | Fair. Each piece has precedent (Popper, Kosmos); the combination and code enforcement are the novelty. |
| Results | High on the surface: 0.537 vs 0.240 and above 0.523, plus a working live demo | Weak. Wrong benchmark for the record, records missing, different models and tools. |
| Autonomy and self-improvement | Medium: code-run phases, durable resume, approved lessons | Self-improvement is mostly designed, not exercised. |
| Interpretability and reproducibility | Very high: every step visible in the app, every claim linked to its quote, status tables, one-command setup | Strong for the app. Weak for results: run folders are gitignored or left on a cloud machine. |

The likely reasons it stood out: a single striking number next to the record line, a product that
looks finished and shows its agents working live, and documentation that reads as scrupulously honest.

## Comparison with a blind-replication lab

Our lab: two blind teams with separated memory, a director that challenges agreed answers, and a referee
that must run a test.

- **Blindness.** Their v1 adapter's critic is closest to our blind teams, and it is stronger on
  enforcement: they sweep files, processes, message queues and shared memory, then verify the sweep as
  the critic's user before it may run. Their board, which produced the headline, is not blind after
  round 1. Their pilot shows why this matters: a critic that saw a one-line hint "replicated" four wrong
  answers.
- **Challenging agreement.** Their reconciler plays our director's role but only for agreements by the
  same method (token overlap of 0.5 or more), which it must re-derive by script. Our director challenges
  every agreed answer. Their rule is a cheap, explicit trigger worth borrowing.
- **The referee runs a test.** Their ledger is a stricter version of our referee: the test must be
  registered with a hash before results, judged relevant before it runs, executed by the harness, and
  combined by mean e-value. It ran on the scripted model only.
- **Separated memory.** Their swarm lessons are filtered and human-approved, and never shown to a run on
  the same task or a task in their "context". This is close to our memory boundaries, and their
  "context" rule (no leakage via a lesson written after reading another task's lessons) is a detail we
  should check we have.
- **Where we differ on evidence.** We scored on GeneBench, scBench and BioDSA-1K. They report BixBench
  only, and their only checkable numbers are v1 single-agent baselines, a v1.5 pilot of 8 questions, and
  an ungraded open-model grid. They did not report any scBench, GeneBench or BioDSA score.
- **Where they beat us on presentation.** The live Agent Lab, the group-chat log and the claim graph make
  a multi-agent process legible in seconds. A judge can watch it without reading.

## Build process

- **Volume and timing.** 238 commits on main, 458 across all branches. First commit `802cfad` at 08:25
  PT on 14 Nov with 157 files and 27,427 lines ("Drug candidate board skeleton ... Board view in the
  Sim Lab dashboard"); the second large commit (`47913c3`, 11:24 PT) vendored BixBench3. The size of
  the first commit suggests prepared scaffolding or a long agent session before it (inference). Busiest
  hour: 92 commits around 11:00 local on 15 Nov. Last main commits after the 14:30 deadline: `#57` at
  14:32, `#52` (ESMFold) at 14:35, `#58` at 14:56.
- **People.** Brendan Dang 255 commits (engine, dashboard, docs; most of the product), Josue/Josh
  Bustamante 45 (BixBench baseline, board-on-BixBench runs, claims engine), Anh Lam 25 (literature
  scripts, data, the Lab artwork, test shakedown in `TEST-FINDINGS.md`).
- **Agents did much of the typing.** Co-author lines: Devin 40, Claude Opus 5.5 34, "durability" 17.
  An author named "durability" made 130 commits on `fm/*` branches, and 108 commit subjects start with
  `no-mistakes(review|document|test|ci)`, an automated review-and-fix loop (inference from the
  messages). About 17 `devin/*` branches were merged in one burst around 09:00 PT on 15 Nov. The
  headline run itself was "made with Devin" (`docs/status.md` line 30).
- **Agent tooling as build evidence.** `AGENTS.md` (11.7 KB) sets rules for coding agents, chiefly that
  no agent may make a paid call or flip `NO_PAID_CALLS`. `.pi/` configures the Pi coding agent with four
  profiles (advisor, dashboard-qa, docs-writer, reviewer). `.agents/skills/` and `.claude/skills/` hold
  vendored skills (Exa's `build-with-exa`, Vercel React and design skills, a Biomni Lab catalogue of
  100 skills, index only); `skills-lock.json` pins them by hash.
- **Quality gates.** `npm run check` (markers, type checks, engine and dashboard tests; about 3 minutes);
  CI in `.github/workflows/ci.yml`; `TEST-FINDINGS.md` records 451 pass, 1 skip on a fresh clone.
- **Security.** No real credential appears committed; only fake test keys in test files. A review found
  the first bixbench sandbox let a cell print `ANTHROPIC_API_KEY`, read files and open sockets; this was
  fixed and written up (`bixbench/CHANGES-security.md`). The original baseline numbers are marked
  "unverified" for that reason.

## Lessons we can reuse

Ranked by value to us.

1. **Put a single number next to the record line, on one chart.** Judges remember a chart with a dotted
   record line between two dots. Do it with a number that survives a close read, and commit the run
   records (or a summary with per-item results) beside it.
2. **Parallel runs on Modal make repeats affordable.** 60 runs at once, 41 minutes wall clock, $1.61,
   one sandbox per run from a pinned image with no network. We should run our repeats this way and state
   wall time, agent time and dollars together.
3. **Tool parity decides baselines.** Their baseline lost 40 answers to missing R. Check that our single
   agent and our teams have the same packages, models and caps, and say so in one table. If a gain needs
   better tools, report "tools" and "team" as separate effects.
4. **Let code, not models, decide marks.** Quote must appear verbatim in the source; every number in the
   claim must appear in the quote; the checker must be another vendor; anything malformed escalates. This
   is about 100 lines (`engine/src/board/triage.ts` `route`, `quoteInSource`, `hasNumber`) and easy to
   explain on stage.
5. **Pre-register tests with a hash; judge relevance before results; combine with mean e-values.** A
   clean upgrade path for our referee, with a ready-made simulation of how each cheating strategy fares.
6. **Enforce blindness, then verify it.** Their critic sweep (files moved, processes killed, IPC
   cleared, then checked as the critic's user) is a concrete checklist for our blind teams.
7. **Flag agreement by the same method.** Compare one-line method summaries; send same-method agreement
   to the director for a re-derivation.
8. **Write a status table with an Evidence column**, and a claims table tagged "verified-by-me / taken
   from docs / untested". It reads as honesty and doubles as our own checklist.
9. **Make the process watchable.** A live room view and a group-chat log of agent turns explain a
   multi-agent system faster than any diagram.
10. **Guard spending in code by default.** A paid switch that is off unless set to exactly `0`, refused
    before any network call, with keys blanked in child processes, lets coding agents work freely.
11. **What not to copy.** Do not relabel a benchmark, do not drop caveats an hour before judging, and do
    not call an uncapped, different-model, better-tooled run "the optimized system's gain".

## Open questions / things I could not verify

- The board's 132 of 246: no records anywhere in the clone. I could only check that the chart's dots
  sum to 132 given v1 question counts.
- Which models the 7-agent run actually used beyond the tiers named in the branch's `docs/status.md`,
  and whether all 60 board runs used the same code (two were rerun after fixes).
- Whether any real BixBench3 task was ever run by anyone on the team. All repository evidence says no.
- The source of "gemini-4-argon" as the 0.523 record holder (chart only).
- The v1.5 pilot (4 vs 4), the Qwen grid (15 vs 19), and the one real team review (204 statements):
  their run folders are gitignored or not kept.
- Whether what was presented in person corrected the README; the commit says caveats "will be presented
  in person".
- What prize, if any, this team won. The repository does not say.
- How much code existed before the event. The first commit already holds 27,000 lines.
- Whether "Josue Bustamante" and "Josh Bustamante" are one person (different emails, same surname).
