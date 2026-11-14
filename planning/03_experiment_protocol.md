# Experiment protocol

This is the protocol of the Nov 13 rehearsal, kept as its record. The event build (Track 1, coast only) follows the same rules for windows, the decision rule, baselines and the test window, and runs claims 1, 3 and 4. It does not run claim 2 or the turbulence problem.

The rules every number in the pitch must have followed. Written before the experiments are run; changes after that are logged at the bottom with the reason.

## 1. What a forecast is

A **case** is one question asked at one moment: given only what was known at the issue time, what will the value be at the target time? Everything a model sees for a case must have been knowable at the issue time. That includes weather inputs: a forecast input must come from a model run that existed at the issue time, not from values published later.

## 2. Two kinds of evidence

| | Rewind test (backtest) | Sealed forecasts (live) |
|---|---|---|
| What | The lab is cut off at a date and predicts later data it has not seen | Predictions about things that have not happened yet, hashed into a chain before the truth exists |
| Strength | Large sample, many conditions | Cannot be leaked or tuned; graded by measurements we do not control |
| Weakness | Only as clean as the inputs; easy to leak the future by accident | Small sample, one weekend of weather |

Both are reported. Where they disagree, the sealed forecasts win.

## 3. Windows

History is cut into four windows, oldest first, with no overlap between the last target of one and the first issue time of the next.

| Window | Used for | Who can see the truths |
|---|---|---|
| Train | Fitting a candidate's coefficients | The candidate, inside the sandbox |
| Select | Deciding whether a change is kept | The evaluator only |
| Confirm | A kept change must not lose here | The evaluator only |
| Test | The number we report | No model, ever. A person scores it with `lab.experiments.final_test`, which logs every look |

## 4. When a change is kept

Decided by code, never by a model's opinion:

1. The candidate passes the static checks and runs inside the sandbox within its time and memory limits.
2. On the select window it beats the current best on the same cases, with at least 95% of resamples in its favour and at least 0.3% lower mean loss.
3. On the confirm window it is not worse on average.

Statistics resample whole station-days (or the equivalent block), because errors an hour apart at the same place are strongly correlated and resampling single cases makes intervals far too narrow.

## 5. Baselines

Every table includes the official reference, the strongest trivial rule, and the hand-written starting formula. For the coast: the tide table, "the current gap continues", damped persistence, and the starting physics formula. For turbulence: climatology, wind speed, vertical shear, Richardson number, the Ellrod index, and (live only) the official turbulence advisories. A lab result is only a result if it beats the strongest of these on the test window.

## 6. The four claims and how each is tested

**Claim 1: the lab's forecaster beats the baselines on a later window it never saw, and on live forecasts made after it was built.**
Test window table (mean loss by lead, block-bootstrap 95% intervals, paired difference against the best baseline) plus the live sealed scoreboard. Which forecasters get the one look at the test window is fixed before the look: (a) the final forecaster of the finished coast run with the lowest confirm-window loss across the ladder, memory and search experiments (ties: lower select-window loss), and (b) the forecaster that is issuing the live sealed forecasts. Every baseline is scored in the same look. Nothing else is scored on the test window. The comparison is a ladder, each rung adding one thing: random non-LLM mutation of the starting formula; a single agent; the lab's researcher with nothing extra; with the notebook; with search; with both; and the older three-role pipeline.

**Claim 2: the lab gets better at research, not only at one problem.**
A lab is "fresh" (no notebook, no playbook) or "warm" (carries the notebook and playbook from a problem it has already worked on). Both get the same budget on a problem neither has seen.

| Arm | What it controls for |
|---|---|
| Fresh | The reference |
| Fresh, handed the old best forecaster and a cheat sheet of the code interface | Whether the warm lab's edge is just a carried-over answer or familiarity with the code |
| Warm | The claim |
| Warm with a shuffled notebook (findings attached to the wrong outcomes) | Whether the content of memory matters or only its presence |

Measured: loss on the confirm window after a fixed budget, and evaluations, dollars and minutes to reach a target fixed in advance. Five seeds for fresh, fresh-plus-old-forecaster and warm; three for the shuffled arm. Runs that never reach the target are reported as such. If warm does not beat "fresh plus old forecaster", we report that the lab transfers answers, not skill.

**Claim 3: search finds what the model did not already know.**
For every claim tested, the model is first asked closed-book what it knows; the tested claim is marked as already known or new. Reported: share of accepted changes that came from search, acceptance rate of closed-book versus search-derived claims, useful claims per 100 searches for each search backend, and the full ledger of claims with their measured effect, including the ones that failed.

**Claim 4: shared memory pays.**
Same lab, same seeds, four memory setups: none, full context, plain vector store, past.dev. Reported separately, as past.dev's own method requires: final loss (accuracy), minutes to target (latency), dollars and tokens (cost), plus recall time per call.

## 7. Integrity checks

- Leak test per problem: corrupt every value after the issue time and show the model inputs do not change.
- Agent-written code runs with no network, no file access and no truths for the windows it is judged on. Blocked attempts are logged and counted.
- The sealed ledger is verified on every cycle; a break is shown on the dashboard.
- An independent checker reviews each stage and tries to break the claims before we repeat them.

## 8. What we will say out loud

- A simulator or a backtest is not the real world; the sealed forecasts are the part reality graded.
- One weekend is a small sample of weather.
- Pilot reports are biased toward where aircraft fly and toward reporting bumps.
- For water level, most of the skill at one hour comes from "the current gap continues"; the hand-written formula adds nothing there.
- The lab sees each change's score on the select and confirm windows (they appear in its notebook), so both are selected on. The lab never sees the test window. In the rehearsal, people did score the starting formula and the simple rules on that period before the windows were fixed (the first rewind tests), so the starting formula's test score is not clean.

## Change log

| When | Change | Why |
|---|---|---|
| Nov 13, night | Three departures from the test-window rule as written. (1) The starting formula was scored in a second look, 47 seconds after the first. (2) The rule said "the forecaster that is issuing the live sealed forecasts"; two were (lab-a and lab-b) and both were scored. (3) The best run's forecaster was promoted to the live job as lab-c three and a half minutes after the look | (1) The rule said every baseline is scored in the same look, and section 5 counts the starting formula as one, but the scorer listed only the tide table and the two persistence rules. The second look broke the rule as written; nothing was chosen after it, both looks are in `runs/lab/final_test/log.jsonl`, and the scorer now always includes the starting formula. (2) Wording. (3) So its live record starts after its test score was known; say so when quoting its live results. When the rule was committed (41 minutes before the look) the run it would pick was already known. |
| Nov 13, night | The ladder got three more seeds (3, 4, 5) for single agent, researcher, and researcher with notebook | Repeating one setup with one seed moved the final loss by 0.1 to 0.55 cm, as large as the gaps between arms, so two seeds could not rank them. Decided after seeing the two-seed results. The added runs finished before the test-window pick and were eligible for it; none had the lowest confirm loss. |
| Nov 13, night | Rule added for which forecasters are scored on the test window (claim 1, above) | The rule did not exist yet. Written after the ladder and before the search experiment finished, and before any look at the test window. |
| Nov 13, night | Search experiment arms changed to: off, Crossref, arXiv, plus OpenAlex and Exa when their keys are set. Crossref added as a third keyless source; requests to one source are now spaced, and a "daily allowance used up" reply is not retried | In the ladder experiment the two arms with search got almost no results: all 72 OpenAlex calls were refused (its keyless daily allowance, shared by network address, was used up) and 51 of 72 arXiv calls timed out or were refused. Those two arms are therefore extra samples of the arms without search, and the ladder says nothing about search. Changed before the search experiment started. |
| Nov 13, evening | The lab's default proposal mode changed from a three-role pipeline (analyst, scout, engineer forced to implement the scouted idea) to a single researcher that may use the notebook and a board of scouted ideas or ignore them. Claim 1 became a ladder; the earlier matrices were re-planned under new names | In the first controlled comparison (2 seeds, 48 proposals) the single agent beat the pipeline on the confirm window at equal proposals (2.70 vs 3.00 cm) and at equal dollars. The old result is kept in `runs/exp/claim1_coast` and quoted in `docs/experiments.md`. Agents also no longer see confirm-window figures in the notebook, only whether the later-window check passed. |
| Nov 13, evening | Claim 2 control changed: "fresh, handed the old best forecaster" replaced by "fresh with a cheat sheet" (the interface and sandbox rules, no findings). Rehearsal runs use 2 seeds, not 5 | A forecaster for one problem cannot run on another, so it cannot be handed over. Two seeds is all one laptop can do overnight; the event runs use 5. Two seeds support no significance claim and the report says so. |
| Nov 13, evening | Time-to-target goal fixed by rule: 5% below the starting formula's loss on the confirm window, per problem | The report's first version set the goal after the fact from the reference arm's own results. The rule was set while the first matrix (lab vs baselines on the coast) was partly finished; the other three had not started. |
| Nov 13, evening | "Same budget" defined as the same number of proposals; dollars and minutes are reported beside it | A single-agent proposal costs about a third of a swarm proposal, so equal proposals is not equal money. Both views are reported. |
| Nov 13, evening | The problem description shown to agents no longer quotes the tide-table offset of the most recent weeks | That figure came from a period overlapping the test window. Changed after some runs of the first matrix had finished; the effect is one sentence of context. |
| Nov 13, afternoon | Weather inputs in the backtest switched from stitched near-final values to forecasts issued before the issue time | Our own checker found that most of the first formula's 24-hour gain (5.1 cm without weather terms, 3.7 cm with) came from weather values that were not available at the issue time. The live sealed forecasts were not affected. |
