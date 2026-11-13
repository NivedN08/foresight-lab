# Idea iterations

How we got from the first two ideas to the current project. Each round lists what was considered, the flaw that killed it, and what was kept.

## What we are optimising for

1. A demo that works live and is graded by something we do not control.
2. Not what other teams with the same tools will build.
3. One sentence to explain, and a real use.
4. Evidence the organizer rewards: a clear test, honest baselines, held-out data, stated limits.
5. As many prize lines as possible: main, Pacific (Track 2), Exa, past.dev, Modal.

Patterns already common at 2026 hackathons, which we avoid as the headline: keep-or-revert loops pointed at a new domain, prompt rewriting from failure logs, an LLM judging soft outputs, agents that build agents, trading-strategy evolution, paper-writing "AI scientists", literature-review bots.

## Round 0: the two starting ideas

| Idea | What it was | Why it fell |
|---|---|---|
| Hindcast | Agents predict which approved drugs will be reported to work for a disease, using search capped at a past date; graded on later papers | Capping search at a date does not cap what the model learned in training, so the score partly measures memory. The judge of "confirmed" is another LLM. |
| Canary | Agents flag papers likely to be retracted using only earlier information; graded on later retractions | Same leak. Publisher and comment-count metadata alone already reach about 0.78 AUROC, so a swarm adds little. Naming real authors is awkward. |

**Kept:** the best part of both: cut the system off at a date, let it predict, and let what really happened grade it.

## Round 1: other research-integrity ideas

| Idea | Why it fell |
|---|---|
| Saboteur vs auditor (one agent plants methodology flaws in ML experiments, a swarm finds them) | Close prior work exists; the auditor learns one saboteur's habits; the real-case test set is tiny |
| Benchmark leak hunter (find test items leaked on the web) | A leaked page does not prove a model trained on it; we would be grading ourselves |

## Round 2: three independent critics

A research judge, a sponsor-and-investor panel and a feasibility engineer each attacked the shortlist. What they agreed on, and what we kept:

- The score must come from something the agents cannot touch.
- Held-out data is scored once.
- Baselines must include the obvious strong ones, not only weak ones.
- "The system improves itself" has to be tested directly: a lab that has done one problem versus a fresh lab, same budget, on a problem neither has seen, with controls for simply carrying the old answer over.
- Search must be the mechanism, with a record of which source led to which gain, not a decoration.
- A memory comparison needs a full-context baseline, and accuracy, speed and cost reported separately.

## Round 3: simulator-based control problems

Considered: traffic signals (already done with LLM-written controllers), train rescheduling (operations-research planners set a bar Python code will not reach), dosing problems in medicine (crowded and risky to present). Dropped.

## Round 4: predict a physical future

The rewind idea works properly when the "future" is a physical measurement that cannot be in any model's training data. First candidates:

| Candidate | Verdict |
|---|---|
| Weather-balloon landings | Works and is unusual; graded within the hour; small real-world payoff. Backup. |
| Ocean drift of floating objects | Real stakes; machine-learning work already exists and most error comes from the ocean-current data. Backup. |
| Aftershock forecasting | Machine learning rarely beats the standard statistical model, and anything near "earthquake prediction" invites doubt. Dropped. |

## Round 5: a sweep of about 40 applications

Three parallel sweeps (sky and space; earth and water; moving things, living things and anything else) scored every candidate on eight criteria: real-world potential, demo, uniqueness, one-sentence explainability, ease of data, grading speed and volume this weekend, room to beat the official forecast, and fit for readable physics formulas. Each data feed was tested live.

Ruled out because nothing would happen this weekend: aurora and geomagnetic storms (quiet sun forecast), solar flares, volcano episodes (next one due after the event), wildfire spread, bird migration, mountain snow. Ruled out as crowded or commercial: aircraft and transit arrival times, river floods. Ruled out as a toy: geyser eruptions. Hard to explain: long-range radio propagation, although it had the densest grading of anything (300,000 real radio contacts an hour).

**The top two:**

| | A. The sea on the California coast | B. Aviation turbulence |
|---|---|---|
| Predict | How far the water runs above the official tide table at 20–30 gauges; wave height at coastal buoys | Where aircraft will report moderate-or-worse turbulence in the next 1–6 hours |
| Graded by | NOAA tide gauges every 6 minutes; buoys every 30–60 minutes | Pilot reports, about 1,500 over the event |
| Why now | San Francisco is reading about 0.6 m above the tide table; a High Surf Warning runs Friday 6pm to Sunday 4am | Works any weekend; late autumn has a strong jet stream |
| Official forecast to beat | Tide table; NOAA surge model; operational wave model | Published turbulence indices; official turbulence advisories |
| Stakes | Coastal flooding and dangerous surf | Turbulence is the leading cause of injuries on airliners |
| Main risk | A calm Sunday; at short lead "the current gap continues" is hard to beat | Pilot reports are noisy and biased toward where planes fly; heavier data work |

**Decision:** build both far enough to compare the live demos side by side, then pick the headline. The other becomes the second problem in the "does the lab get faster" test. Pacific's GPU-price task is added on Saturday as the unseen third problem.
