# Project spec

Working name: **foresight-lab**. Headline application: the sea on the California coast (pending the team's final pick between coast and turbulence; the other stays as the second problem).

## One sentence

An AI research lab that cuts itself off at a date, learns only from before it, locks in forecasts about things that have not happened yet, and is graded by the real world.

## The problem it solves

Forecasts for many physical systems are made with formulas that were tuned once and rarely revisited, and new ideas sit in papers nobody has tested against today's data. Testing them properly is slow, and it is easy to fool yourself: a backtest can leak the future, and a model that has read the internet may already know the answer.

This lab removes both excuses. It tests ideas from the literature against measurements, keeps only what holds up on later data, and then commits to forecasts whose answers do not exist yet.

**Headline case.** This autumn the sea on the US West Coast is running far above the official tide tables (about 32 cm on average over 90 days across 23 gauges, more than 50 cm at San Francisco on Nov 13). Tide tables predict astronomy, not weather or the season's ocean state. Harbor masters, coastal road crews and flood-warning offices need the real level a few hours to a day ahead.

## What the system is

```
        literature and practitioner pages
                     |  search
   +-----------------v------------------+
   |            research lab            |      lab notebook
   |  analyst -> scout -> engineer      |<---> (dated findings,
   |      one small code change         |       shared, superseded)
   +-----------------+------------------+
                     | candidate forecaster (readable Python)
   +-----------------v------------------+
   |  sandbox + evaluator               |  truths for select, confirm
   |  fit on train, judged on later     |  and test never leave here
   |  windows; kept only if it wins     |
   +-----------------+------------------+
                     | promoted forecaster
   +-----------------v------------------+
   |  live job: seal forecasts in a     |----> dashboard
   |  hash chain; grade them when the   |
   |  real measurement arrives          |
   +------------------------------------+
```

| Part | Where | What it does |
|---|---|---|
| Sealed ledger | `lab/core/ledger.py` | Append-only, hash-chained record of forecasts; refuses to seal the past or grade early |
| Problem interface | `lab/core/problem.py` | Four time-ordered windows (train, select, confirm, test), per-case loss, blocks for honest statistics |
| Coast | `lab/tasks/coast/` | Tide gauges, buoys, weather forecasts; water-level and wave problems; live job |
| Turbulence | `lab/tasks/turb/` | Pilot reports, upper-air fields, turbulence indices; live job |
| Any time series | `lab/tasks/series/` | A CSV in, a problem out; for the track task |
| Sandbox | `lab/sandbox/` | Runs agent-written code with no network, no files, no truths |
| Evaluator | `lab/evalpool/` | Fits and scores candidates; paired block statistics |
| Research loop | `lab/swarm/` | Roles, decision rule, logging, warm starts |
| Notebook | `lab/memory/` | none, full context, vector store, past.dev |
| Search | `lab/search/` | OpenAlex, arXiv, Exa |
| Model backends | `lab/llm/` | Claude command-line tool (no key), OpenAI-compatible, Anthropic |
| Dashboard | `dashboard/`, `lab/server.py` | Live view of sealed versus measured |

## What a forecaster looks like

A single readable Python file: a class with `fit` (estimate coefficients from past cases) and `predict` (one case in, one number out). It may use `math` and `numpy` only, is capped in size, and cannot carry lookup tables. The starting formula for water level is damped persistence plus a pressure term, a wind term and a recent-trend term.

## How a change is judged

1. Static checks, then the sandbox.
2. Fit on the train window; predict the select and confirm windows.
3. Kept only if it beats the current best on select (95% of resamples of whole station-days, at least 0.3% lower error) and is not worse on confirm.
4. The test window is scored once, at the end, by a person.

## What "graded by the future" means in practice

- **Rewind test.** Windows are cut by time. Weather inputs come from forecast runs that existed at the issue time.
- **Sealed forecasts.** Every 15 minutes the live job seals forecasts for 1, 3, 6, 12 and 24 hours ahead at every gauge, for every model, and grades the ones that have come due against the gauge reading.

## Results so far (rehearsal, Nov 13)

All numbers below come from commands in the repo. Test-window results are not yet scored.

| What | Result |
|---|---|
| Official tide table, backtest test window | 32 cm mean error at every lead |
| Starting formula vs best simple rule, honest weather, 24 h | 4.0 cm vs 5.1 cm |
| Lab, 10 generations (14 minutes, about $7) | Error on the select window 2.80 to 2.47 cm; on the confirm window 3.24 to 2.74 cm |
| Live sealed forecasts, first evening | 1 h: tide table 54 cm, simple rule 2.7 cm, starting formula 2.7 cm (92 forecasts). 3 h: 54.5, 4.9, 3.9 cm (46 forecasts) |
| Leak found by our own check | With near-final weather the 24 h figure looked like 3.7 cm; fixed to 4.0 cm |

## Limits

- One season and one weekend. A calm week would show smaller differences.
- At one hour ahead, "the current gap continues" is as good as anything.
- The backtest's weather for the 24-hour lead comes from runs two days old, which is stricter than reality.
- Pilot reports (turbulence) are biased toward where aircraft fly; that application's first formula does not yet beat the standard index.
- The lab's gains are measured on windows it selected on; the test window has not been opened.
