# How a candidate is judged

A candidate is one Python file defining `class Forecaster` (`name`, `fit(cases, truths)`,
`predict(case) -> dict`). It never runs in the lab's own process. `lab.evalpool.evaluate.evaluate`
runs it once in the sandbox (`docs/sandbox.md`): fitted on the train window, predicting the cases
of the windows asked for. The child process receives train cases, train truths and the public
fields of the evaluation cases. It never receives a truth from an evaluation window; losses are
computed afterwards, in the parent, from truths the child never saw. A candidate that fails the
lint, crashes, times out (3 minutes), exceeds memory (1.5 GB resident) or returns a bad prediction
gets `ok = False`, the error, and no score.

## The four windows

A Problem (`lab/core/problem.py`) gives four time-ordered windows; each ends before the next one
begins (`Splits.check_order`). For `coast_water` (`lab/tasks/coast/problem.py`) the last 90 days of
hourly cases are cut by time into 60% / 15% / 10% / 15% of the span. Cases whose lead window
straddles a cut are dropped, which leaves a gap of up to 24 h. Each window is then subsampled with
a fixed seed in proportion to every (station, lead) group. The current build (data until
2026-11-13 22:00 UTC):

| window | used for | truths seen by candidates | issue / target span (UTC) | cases | station-days |
|---|---|---|---|---|---|
| train | `fit` | yes | 2026-08-15 22:00 .. 2026-10-08 22:00 | 12,000 | 1,244 |
| select | first judgement of a change | no | 2026-10-08 23:00 .. 2026-10-22 10:00 | 6,000 | 322 |
| confirm | second, later judgement | no | 2026-10-22 11:00 .. 2026-10-31 10:00 | 6,000 | 230 |
| test | one final score, by a person | no | 2026-10-31 11:00 .. 2026-11-13 21:00 | 8,000 | 322 |

The build is cached in `cache/coast/problem/`, keyed by the last hour of gauge data, and pinned
(`latest_*.json`), so every evaluation sees exactly the same cases until someone builds a newer
one (`until=` or `refresh=True`).

## The score and the acceptance rule

`problem.losses` gives one loss per case (`coast_water`: absolute error of `residual_m` in cm).
An `EvalResult` holds the per-case losses of each window and `problem.summarize` (mean overall and
by lead, counts).

A change is kept only if it **wins on select** and **does not lose on confirm**. Both are paired
comparisons on the same cases (`lab.evalpool.evaluate.compare`). The per-case loss differences are
resampled with `paired_block_bootstrap`, which draws whole **blocks**: for `coast_water` a block is
a station-day (station id + UTC date of the target), because errors an hour apart at one gauge are
strongly correlated and resampling single cases makes intervals far too narrow. `compare` returns
the mean difference (negative: the first candidate is better), its 95% interval, the share of
resamples in which the first candidate is better, and the number of blocks. The second, later
window guards against keeping a change that only fits the quirks of the select period.

`EvalPool(problem, splits, max_workers=4)` runs evaluations in parallel and caches results by the
SHA-256 of the source. The encoded cases are prepared once per set of windows and reused (they still
carry train truths only). One seed evaluation took a median 10.4 s when the cases were encoded for
every run and 4.7 s with the prepared payload (one-time encoding 6 s), on this Mac under a load
average of about 15. In a later run under light load each took 5.5 s, and the four candidates
together took 10.2 s.

`.venv/bin/python -m lab.evalpool.check --problem coast_water` (2026-11-14, mean absolute error, cm):

| candidate | select | 1 h | 3 h | 6 h | 12 h | 24 h | confirm | 1 h | 3 h | 6 h | 12 h | 24 h |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tide_table | 24.05 | 24.07 | 24.23 | 24.04 | 23.94 | 23.98 | 27.45 | 27.58 | 27.75 | 27.29 | 27.29 | 27.31 |
| persistence | 3.12 | 1.69 | 2.73 | 3.49 | 3.88 | 3.86 | 3.75 | 2.04 | 3.69 | 4.39 | 4.38 | 4.35 |
| damped_persistence | 3.26 | 1.69 | 2.92 | 3.63 | 4.19 | 3.94 | 3.64 | 2.02 | 3.62 | 4.15 | 4.25 | 4.28 |
| seed | 2.80 | 1.72 | 2.97 | 3.33 | 3.47 | 2.52 | 3.24 | 2.01 | 3.51 | 3.65 | 3.66 | 3.42 |

Seed minus persistence on select: -0.316 cm [-0.401, -0.239], 322 station-days, P(seed better) = 1.000.
The tide table is 24 to 27 cm off because this is an unusually high sea-level season (the offset is
positive at every gauge and has been rising since summer).

## How the test window is protected

- `evaluate` refuses any request that includes `test` unless the caller passes `allow_test=True`
  (`HeldOutWindowRefused`). `EvalPool` never passes it, so the search loop cannot see a test score.
- Only `lab.experiments.final_test` scores the test window, and a person runs it:
  `.venv/bin/python -m lab.experiments.final_test --problem coast_water --candidate a.py`.
  It fits each candidate on train and scores it and every baseline on test (mean loss overall and
  by lead with 95% block-bootstrap intervals, and the paired comparison with the best baseline).
  It writes `runs/lab/final_test/<problem>_<UTC timestamp>.json` with the SHA-256 of each candidate
  and of the evaluator and problem source files, tying the numbers to exact code.
- Every run appends a line to `runs/lab/final_test/log.jsonl`. A candidate whose hash is already in
  the log is refused unless `--again` is given, and each run prints how many times the test window
  has been looked at before.

## The leak that was found, and the fix

The first coast backtest read past weather from Open-Meteo's forecast endpoint. For past hours that
endpoint returns short-lead forecasts stitched together, close to what actually happened, so a case
"issued" days ago saw near-perfect weather for its lead window. The same held for the wave model.

Every input now comes from data that existed at the issue time. Past weather and wave-model values
come from Open-Meteo's previous-run archives (`_previous_day1` / `_previous_day2`: the value as
forecast by a run started at least 24 h / 48 h earlier). Allowing up to 6 h for a run to be
published, day 1 is used for leads up to 18 h and day 2 for the 24 h lead. The leak tests corrupt
every observation after the issue time and every run made after it, and check that no input
changes. Deliberately leaky variants (day 1 for the 24 h lead; the old stitched series) fail them.
The live job also fits on this history, and predicts with the newest forecast.

Backtest, 90 days until 2026-11-13 22:00 UTC, test MAE in cm at 1 / 3 / 6 / 12 / 24 h:

| SeedPhysics weather | 1 h | 3 h | 6 h | 12 h | 24 h |
|---|---|---|---|---|---|
| stitched (leaky, before) | 2.17 | 3.82 | 4.32 | 4.32 | 3.74 |
| previous runs (after) | 2.17 | 3.82 | 4.35 | 4.46 | 4.01 |
| no weather | 2.18 | 3.89 | 4.54 | 4.95 | 5.09 |
| DampedPersistence, for reference | 2.18 | 3.92 | 4.74 | 5.07 | 5.12 |

So the leak inflated the 24 h skill by about 0.3 cm, but most of the weather benefit is real:
honest weather still cuts the 24 h error from 5.09 to 4.01 cm. For waves, SeedSwell at 24 h went
from 28.4 cm (leaky) to 34.8 cm (honest), and the raw wave model from 32.1 to 39.0 cm.
