# foresight-lab

An AI research lab that cuts itself off at a date, learns only from before it, locks in forecasts about things that have not happened yet, and is graded by the real world.

Private rehearsal repository for the SF AI Research Hackathon (Nov 14–15, 2026). Plans are in `planning/`; start with `planning/02_project_spec.md`.

## What is here

| Folder | What |
|---|---|
| `lab/core/` | Sealed forecast ledger, problem interface, statistics |
| `lab/tasks/coast/` | Sea level above the tide table and wave height on the US West Coast |
| `lab/tasks/turb/` | Aviation turbulence over the continental US |
| `lab/tasks/series/` | Any time series from a CSV (for the track task) |
| `lab/sandbox/` | Runs agent-written code with no network, files or truths |
| `lab/evalpool/` | Fits and scores candidate forecasters |
| `lab/swarm/`, `lab/memory/`, `lab/search/`, `lab/llm/` | The research loop, its notebook, literature search, model backends |
| `lab/experiments/` | Experiment matrix, report, one-time test scorer |
| `dashboard/`, `lab/server.py` | Live dashboard |
| `planning/` | Event brief, idea log, spec, protocol, sponsor playbook, schedule, pitch, risks, checklists, rebuild guide |
| `docs/` | How each part works |

## Set up

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest
```

Keys go in a local `.env` (never committed): `EXA_API_KEY`, `PAST_API_KEY`, `OPENAI_API_KEY`, Modal token via `modal token new`. Without keys, the lab uses the Claude command-line tool for model calls and keyless scholarly search.

## Run things

Live forecast jobs (seal forecasts, grade the ones that have come due):

```bash
.venv/bin/python -u -m lab.tasks.coast.live run --interval 900
.venv/bin/python -m lab.tasks.turb.live run --interval 1800
```

Dashboard at http://localhost:8765:

```bash
.venv/bin/python -m uvicorn lab.server:app --port 8765
```

Check a problem through the evaluator (baselines and the starting formula on the select and confirm windows):

```bash
.venv/bin/python -m lab.evalpool.check --problem coast_water
```

Run the research lab:

```bash
.venv/bin/python -m lab.swarm.run --problem coast_water --generations 10 --proposals 6 --memory vector --search openalex
```

Put the lab's best forecaster into the live job:

```bash
.venv/bin/python -m lab.tasks.coast.promote --run runs/lab/coast_water/<run_id> --name lab-a
```

Backtests (fixed end time makes them reproducible):

```bash
.venv/bin/python -m lab.tasks.coast.backtest --days 90 --until 2026-11-13T22:00:00Z
.venv/bin/python -m lab.tasks.turb.backtest --days 30
```

Score the test window, once, at the end:

```bash
.venv/bin/python -m lab.experiments.final_test --problem coast_water --candidate runs/lab/coast_water/<run_id>/incumbent.py
```

## Rules the code enforces

- A forecast can be sealed only for a time still in the future, and graded only once that time has passed. Editing or deleting a sealed forecast breaks the hash chain.
- A forecaster sees only what was known at the issue time. Weather inputs in backtests come from forecast runs that existed then.
- A change to the forecaster is kept only if it wins on later data and does not lose on still later data.
- The last window of history is scored once, by a person, and every look is logged.
