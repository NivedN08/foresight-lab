# Sponsor playbook

One section per prize: what the sponsor asked for, what we show, the number to put on the slide, the experiment that produces it, and the honest weak point. Every number comes from a run log; none is filled in by hand.

## Main prizes (top 3)

**What judges are told to look for:** experimental design, benchmark performance, reproducibility, baseline comparisons, quality of conclusions.

**What we show**
- A forecast locked in before the fact, next to what then happened, live on the dashboard.
- The test-window table: the lab's forecaster against the official reference and the strongest simple rule, with intervals that resample whole station-days.
- The leak our own checker found in our first backtest, and what the numbers became after the fix.

**Slide number:** mean error of sealed forecasts, lab vs official reference, with the count of graded forecasts.

**Weak point to say first:** one weekend of weather; at one hour ahead the simple rule is nearly as good.

## Pacific: Track 2, Recursive Self-Improvement (GPU price forecasting)

**What the CEO said:** "Our track is on GPU price forecasting and I'll be judging it on the Sunday."

**What we show**
- The same lab pointed at Pacific's price data on Saturday, through the generic time-series adapter (`lab/tasks/series/`): seed forecaster, baselines (last value, seasonal naive, moving average), the rewind test, and sealed forecasts for prices that do not exist yet.
- The self-improvement evidence: a lab that has already worked on other problems against a fresh lab, same budget, on Pacific's data, which neither has seen.

**Slide numbers**
- Forecast error on Pacific's held-out window against their baseline or the best naive rule.
- Evaluations and dollars to reach a target fixed in advance: fresh, fresh plus old forecaster, warm, warm with shuffled notebook.

**Experiment:** claim 2 in `03_experiment_protocol.md`.

**Questions for Pacific at kickoff:** the horizon and score they care about; whether they prefer accuracy on their data or the self-improvement evidence; whether sealed forecasts for next week's prices would interest them.

**Weak point:** if the warm lab does not beat "fresh plus old forecaster", the honest claim is that the lab reuses answers, not that it improves itself. We report whichever happens.

## Exa: "find what it doesn't already know"

**What we show**
- Search is how the lab gets ideas to test. Each proposal states a knowledge gap, answers it closed-book first, then searches; the claim it tests is marked as already known to the model or new.
- A ledger of every claim tested, with its source page and its measured effect, including the failures.
- A lineage view: each accepted change links to the page it came from.

**Slide numbers**
- Share of accepted changes that came from claims the model did not know closed-book.
- Useful claims per 100 searches: Exa against keyless scholarly search (OpenAlex, arXiv), same lab, same seeds.
- Loss after a fixed budget with search on and off.

**Experiment:** claim 3. Needs the Exa key; until then the same experiment runs with OpenAlex and arXiv, which become the comparison baseline.

**Why Exa should do better here:** in our first long run, keyless arXiv search kept returning generic forecasting-competition papers for an oceanography problem. The useful material is domain papers and practitioner write-ups (forecast-office notes, operator manuals), which a general web index covers and arXiv does not.

**Weak point:** if search-derived claims are accepted no more often than closed-book ones, we say so and show the ledger anyway.

## past.dev: shared memory for a swarm, with proof

**What they asked for:** "prove better accuracy, latency gains and cost reduction than the common existing approaches". Their own method requires full-context and plain-RAG baselines and reports accuracy, cost and latency separately.

**What we show**
- The lab notebook: dated findings shared by all agents; later findings supersede earlier ones.
- As-of recall (`queryTimestamp`): when the lab is replayed from an earlier date, the notebook is read as of that date, so no agent sees findings from its own future. This is the memory feature the rewind test needs.
- The head-to-head: none, full context, plain vector store, past.dev. Same lab, same seeds.

**Slide table**

| Memory | Final loss | Minutes to target | Dollars to target | Recall time per call |
|---|---|---|---|---|
| None | | | | |
| Full context | | | | |
| Vector store | | | | |
| past.dev | | | | |

**Experiment:** claim 4. Needs the past.dev key; the three local backends run before then.

**Moment to show:** a rule that held for one group (for example open-coast gauges) and was later contradicted for another (bay gauges); which backends still return the stale rule.

**Weak point:** in one of past.dev's own published runs it was more accurate but slower and costlier. If that happens here we report it; the argument then rests on accuracy and on as-of recall.

## Modal: best use

**What has won before:** real GPU work, large parallel fan-out, long-lived sandboxed agents. Thin wrappers do not win.

**What we show**
- Every candidate the agents write is untrusted code. On Modal each evaluation runs in its own sandbox with the network blocked; the experiment matrix (arms x seeds x generations) fans out across containers and scales to zero.
- A scheduled Modal function seals forecasts and grades due ones every few minutes all weekend, so the demo does not depend on a laptop. (On Friday the laptop's lid was closed for two stretches and the live jobs missed cycles; that is the argument for this.)

**Slide numbers:** sandboxed evaluations run, peak parallel containers, wall-clock against the same matrix run serially on one laptop, total cost.

**Weak point:** this is a fan-out and sandbox story, not a GPU story. We do not bolt on a GPU job to tick a box; if a learned correction term measurably helps, it trains on a Modal GPU and we show the before and after.

## Tinker (part of the 1st-place prize)

**Optional, first to be cut.** Fine-tune a small open model on the lab's own records of (current forecaster, proposed change, measured effect) so it proposes changes that are accepted more often. Report the acceptance rate of its proposals against the base model on a problem it was not trained on, with a control trained on shuffled outcomes. Only worth doing if the lab has produced a few hundred records by Saturday evening.

## Best picture on X ($500)

Take it Saturday while the room is full: the dashboard on the big screen with sealed forecasts landing.
