# Sponsor playbook

One section per prize: what the sponsor asked for, what we show, the number to put on the slide, the experiment that produces it, and the honest weak point. Every number comes from a run log; none is filled in by hand.

## Main prizes (top 3)

**How it is scored:** 100 points, 20 each for Technicality, Creativity, Usefulness, Demo, and Track / sponsor alignment. The finals take the top 2 of each track, then the challenge winners.

**What we show**
- A forecast locked in before the fact, next to what then happened, live on the dashboard.
- The test-window table: the lab's forecaster against the official reference and the strongest simple rule, with intervals that resample whole station-days.
- The leak our own checker found in our first backtest, and what the numbers became after the fix.

**Slide number:** mean error of sealed forecasts, lab vs official reference, with the count of graded forecasts.

**Weak point to say first:** one weekend of weather; at one hour ahead the simple rule is nearly as good.

## Track 1: Build Your Own Autoresearch Framework

**The brief:** "Build a system that runs the research loop on its own: proposes a hypothesis, runs the experiment, reads the result and decides what to try next."

**What we show**
- The loop, with the brief's four verbs as its four stages. Each proposal is one change with a stated reason (the hypothesis). The experiment is a refit in a sandbox scored on later data. The result is read by code: a failure digest and a decision rule. What to try next comes from the digest, the notebook and the ideas board.
- That the loop is not tied to the coast: a problem is four time-ordered windows and a loss.
- The control that makes it research and not search: random code changes, same budget, never passed.

**Slide number:** error on the test window: tide table, best simple rule, hand-written formula, the lab's formula.

**Weak point to say first:** more machinery did not help. A three-role swarm lost to one model; the notebook and free search added nothing measurable in rehearsal.

Pacific's track (GPU prices two to three years out) is not part of this plan.

## Exa: "find what it doesn't already know"

**The condition:** "At least one Exa API call must run in your project's main loop, and your demo must show your sources." So Exa is on in the default loop, and the lab view shows the source link of every change that used a board idea.

**What rehearsal showed with free scholarly search (not Exa):** 19 of 96 proposals took an idea from search; 5 passed; 4 of those 5 were an idea the model had already listed before seeing any result; final error was no lower. The Exa arm is the test of whether a better search changes that.

**What we show**
- Search is how the lab gets ideas to test. Each proposal states a knowledge gap, answers it closed-book first, then searches; the claim it tests is marked as already known to the model or new.
- A ledger of every claim tested, with its source page and its measured effect, including the failures.
- A lineage view: each accepted change links to the page it came from.

**Slide numbers**
- Share of accepted changes that came from claims the model did not know closed-book.
- Useful claims per 100 searches: Exa against keyless scholarly search (Crossref, arXiv; OpenAlex with a key), same lab, same seeds.
- Loss after a fixed budget with search on and off.

**Experiment:** claim 3. Needs the Exa key; until then the same experiment runs with Crossref and arXiv, which become the comparison baseline. (OpenAlex's keyless daily allowance ran out during the first experiment, so that arm now needs a key.)

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

**What rehearsal showed for the three local rows (2 seeds, confirm window):** 3.05, 2.93 and 3.00 cm; all six runs reached the target in 12 to 20 proposals; the notebook cost about 25% more per run. Over five seeds the notebook arm averaged 2.73 cm against 2.89 without it, lower on 4 of 5 seeds, not significant. So expect a small or null effect and plan five seeds.

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
