# Pointing the lab at a new time-series file

`lab/tasks/series/` turns one table (CSV or Parquet) of timestamped values into a lab `Problem`
(`SeriesProblem`). It was built for the hackathon's GPU rental price task before we had the data,
so it assumes only this: one timestamp column, one value column, and optionally some id columns
(GPU model, provider, region, ...) that split the table into series.

## Five minutes from file to lab

1. **Write a config** (JSON; keys are the `SeriesProblem` arguments):

   ```json
   {
     "path": "data/gpu_prices.csv",
     "time_col": "timestamp",
     "value_col": "price_usd_hr",
     "id_cols": ["gpu_model", "provider", "region"],
     "leads": [1, 3, 7],
     "lead_unit": "steps",
     "history": 56,
     "name": "gpu_prices"
   }
   ```

   | key | default | meaning |
   |---|---|---|
   | `path` | required | CSV or Parquet. Relative paths: the config file's folder if the file is there, else the project root |
   | `time_col`, `value_col` | required | timestamp column; the value to forecast |
   | `id_cols` | `[]` | each distinct combination is one series; the **first** one also picks the peers (below) |
   | `freq` | `null` | grid step such as `"1h"` or `"1D"`; `null` infers it from the data (median spacing, snapped to a round step within 10%) and says so |
   | `leads` | `[1, 3, 7]` | forecast horizons |
   | `lead_unit` | `"steps"` | `"steps"`, `"hours"` or `"days"` (must be whole numbers of steps) |
   | `history` | `56` | steps of history in each case |
   | `covariate_cols` | `[]` | other numeric columns; their history (never later than the issue time) goes into each case |
   | `known_future_cols` | `[]` | columns genuinely fixed in advance (see the leak rules); their value **at the target** goes into each case |
   | `loss` | `"mae"` | `"mae"` absolute error, `"mape"` absolute % error (true value 0 is not scored), `"rmse"` squared error |
   | `log_target` | `false` | forecast the natural log of the value (values <= 0 become missing) |
   | `max_fill_steps` | `0` | in each case's history (series and covariates), carry the last real observation forward at most this many steps; truths are never filled |
   | `max_cases` | `null` | cases per window: `null` = 10,000 train / 5,000 select / 5,000 confirm / 5,000 test; a number caps every window; a dict sets each |
   | `seed`, `name`, `title`, `cache_dir` | | subsample seed; problem name (also names the cache files); one-line title; where splits are cached (`cache/series/problem/`) |

2. **Sanity-check it** (prints the data summary, the windows, and the baseline and seed scores on
   select and confirm, evaluated in the sandbox):

   ```
   .venv/bin/python -m lab.tasks.series.inspect path/to/config.json
   ```

3. **Point the lab at it** through the registry name `series`; the config path comes from the
   environment variable `LAB_SERIES_CONFIG`:

   ```
   LAB_SERIES_CONFIG=path/to/config.json .venv/bin/python -m lab.evalpool.check --problem series
   ```

   Anything that calls `lab.evalpool.registry.get_problem("series")` works the same way
   (`lab.experiments.final_test --problem series`, the experiment matrix). `lab.swarm.run` keeps its
   own name map; to use it there add `"series": "lab.tasks.series.problem:from_env"` to its
   `PROBLEMS` (the factory takes no arguments).

4. **Live sealed forecasts** for the next steps after the end of the file:

   ```
   .venv/bin/python -m lab.tasks.series.live config.json --candidate best.py [--candidate other.py] \
       [--ledger runs/series/live]
   .venv/bin/python -m lab.tasks.series.live config.json --grade newer_copy.csv
   ```

   Each candidate and every baseline is fitted in the sandbox on cases from the whole file, then
   forecasts every series and lead from the last time in the file. Forecasts go into a hash-chained
   `SealedLedger` (`lab/core/ledger.py`). A target value is the mean over (target - step, target],
   so a forecast is sealed only if that whole interval is still ahead: `target - step >= now`.
   Forecasts whose target has already passed (a stale file) and forecasts whose interval has
   already begun are skipped, and the command reports the two counts separately. With hourly data
   this means a lead-1 forecast can be sealed only within the hour of the data's last timestamp.
   `--grade` reads a newer copy of the data, grades every due forecast whose value is now present,
   prints mean loss per candidate and lead, and verifies the chain.

## How the data become cases

- **Grid.** Times `origin + k * step`, origin = the earliest timestamp. The value at grid time t
  is the mean of the observations in (t - step, t], so it is fully known at t. Duplicates are
  averaged; steps without data are NaN. Text values such as `"$1,234.50"` are cleaned; other text
  becomes NaN, and each of these events appears as a note in the inspect output.
  Timestamps without a time zone are read as UTC.
- **Cases.** For each series, each grid step at which the series has a real observation within
  the last `history` steps (the issue time), and each lead whose target has a real observation:
  one case. The grid is never filled, so a target inside a gap has no case and every truth is
  an observed value.
- **Windows.** The span is cut by time into 60% train, 15% select, 10% confirm and 15% test.
  Cases whose lead crosses a cut are dropped, so each window ends before the next begins. Each window is a
  fixed subsample stratified by series and lead.
- **Blocks** for the paired bootstrap: series + calendar day of the target (series + ISO week when
  the step is a day or longer). If that leaves fewer than 30 blocks in select or confirm, the
  next shorter block is used (day, 12 h, 6 h, one step), and the inspect output says which.

## Inputs (`case.inputs`)

`series_id`, `ids` (dict of id values), `lead` (steps), `lead_h` (whole hours; the lab's score
tables are keyed by it), `lead_hours`, `step_hours`, `y_hist` (`history` values, newest first,
NaN where missing, carried forward up to `max_fill_steps`), `y_hist_fill_steps` (0 = observed at
that step, k = carried k steps, NaN = missing), `y_last` (newest real observation) and
`y_last_age_steps`, `t_hist_hours`, the target's UTC `target_hour`, `target_dow`, `target_dom`,
`target_month`, `cov_hist` and `cov_fill_steps` (covariate histories, filled the same way),
`known_future` (values at the target) and `peers` (never filled). Peers are up to 8 other
series with the same value of the first id column, or all other series when a series has no
such partner. Where there are more than 8, the 8 whose step-to-step changes correlate most on the
train window are used. Each peer carries `y_last`, `y_last_age_steps` and `y_hist7`.
`describe()` gives the full table to the agents.

## Leak rules

- Every history (the series, covariates, peers) stops at the issue time. `tests/test_series.py`
  corrupts and deletes everything after the issue time and checks that no input changes.
- The value at a grid time covers observations up to and including that time, never later.
- **`known_future_cols` are read at the target time, by design.** Only list a column there if its
  future values really are fixed in advance (a calendar flag, a published price schedule, a
  planned maintenance window). Listing a column that is measured as time passes (another price,
  utilisation, demand) hands the agents the future and makes every score meaningless.
- Peer *selection* (not values) may use the train window. That is fine: candidates see all
  train truths anyway, and every later window lies after the train window.
- Forward-filling is off by default. With `max_fill_steps` > 0 it applies only to the history
  arrays a forecaster sees, never to truths. It is strictly causal: a value is carried at most
  `max_fill_steps` steps from the last real observation, decided from data at or before the issue
  time, so whether a gap is filled never depends on whether data come back later.
  `y_hist_fill_steps` and `cov_fill_steps` mark carried values. Tested by corrupting, and
  separately deleting, everything after issue times before, inside and after a gap.

## Demo data (synthetic)

`.venv/bin/python scripts/make_demo_series.py` writes `cache/series/demo_prices_synthetic.csv`
and `cache/series/demo_config_synthetic.json`: **made-up** hourly prices for 8 GPU types x 5
regions over 120 days. They have a trend, daily and weekly cycles, shared shocks per type and
region, step changes, noise and gaps. The data exist only to test the plumbing.
**No number computed from them is a result.**

## What can still break on a real file

- Parquet needs `pyarrow`, which is not installed in `.venv`. Convert to CSV, or install it.
- Wide tables (one column per GPU) must be reshaped to long form (timestamp, id..., value) first.
- Steps shorter than an hour: leads that round to the same whole hour share a `lead_h` and
  merge in the lab's by-lead tables (a note says so).
- Very irregular sampling (for example prices recorded only when they change) is averaged per
  step and leaves NaN between changes. In that case `max_fill_steps` should be large: a recorded price
  holds until the next change, so filling it forward is correct there.
- Many series (hundreds or more) make the default 10,000 train cases thin per series; raise
  `max_cases` and watch the 3-minute sandbox limit (on the demo, 10,000 train plus 10,000
  select and confirm cases took 2 to 3 s per candidate).
