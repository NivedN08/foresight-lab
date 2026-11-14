# `runs/coast/live/state.json`

Written by `python -m lab.tasks.coast.live` at the end of every cycle (default every 15 minutes).
The file is replaced atomically (written to `state.json.tmp`, then renamed), so a reader never
sees half a file. All times are UTC ISO strings like `2026-11-13T21:52:18Z`. Missing values are
`null`. The file is kept under about 1.9 MB: if it would be larger, the window of past forecasts
is shortened (24 h, then 12, 6, 2 h) and `forecast_window` says which window was used.

Two forecasting problems share one sealed ledger (`runs/coast/live/sealed.jsonl`, task names
`coast_water` and `coast_waves`):

* **water**: how far the water runs above the official tide table (the *residual* = observed water
  level minus predicted tide, metres) at NOAA tide gauges.
* **waves**: significant wave height (metres) at NDBC coastal buoys.

Leads are 1, 3, 6, 12 and 24 hours. Each cycle seals one forecast per station (or buoy), lead and
model, at most one per target hour. Errors are absolute errors in **cm**.

## Top level

| field | meaning |
|---|---|
| `generated_at` | when this file was written |
| `ledger` | status of the sealed ledger (below) |
| `cycle` | what the last cycle did (below) |
| `water` | tide gauges, their forecasts, the live scoreboard and the backtest |
| `waves` | buoys, their forecasts and scoreboard; `null` if the wave part did not run this cycle |
| `backtest_waves` | wave backtest: `generated_at`, `days`, `unit`, `table`, `paired_seed_vs` (same layout as `water.backtest`) and `note` (how the wave-model history was obtained, and the same caveat that the intervals are too narrow); `null` until `backtest_waves` has been run |
| `alerts` | active National Weather Service coastal alerts for CA, OR, WA (may be empty) |

## `ledger`

| field | meaning |
|---|---|
| `path` | ledger directory, relative to the project root |
| `records` | number of sealed forecasts (water and waves together) |
| `graded` | how many of them have been graded against a real measurement |
| `head_hash` | SHA-256 hash of the newest sealed record; it chains to every earlier record |
| `verify.ok` | `true` if recomputing the whole hash chain and all grades found no tampering |
| `verify.checked` | records checked |
| `verify.message` | `ok`, or what failed |

## `cycle`

| field | meaning |
|---|---|
| `now` | the cycle's issue time |
| `sealed_this_cycle` / `graded_this_cycle` | water forecasts sealed / graded in this cycle |
| `wave_sealed_this_cycle` / `wave_graded_this_cycle` | the same for waves |
| `stations_sealed_this_cycle` | tide gauges that got new forecasts this cycle (0 when the hour was already covered) |
| `seconds` | how long the cycle took |
| `fetch_log` | recent data-source problems (failed requests, stale cache used, weather unavailable) |

## `water`

| field | meaning |
|---|---|
| `quantity` | plain-language description of the forecast quantity |
| `leads_h` | `[1, 3, 6, 12, 24]` |
| `models` | `TideTable` (residual 0, what the official table implies), `Persistence`, `DampedPersistence`, `SeedPhysics`. `TideTable` is off by about 32 cm because this is an unusually high sea-level season: the offset is positive at every gauge and has been rising since summer (San Francisco +24 cm over 90 days, +37 cm in November; the same weeks of 2024 and 2025 gave 0 to +8.5 cm) |
| `fit` | `fitted_at`, `train_cases`, `train_from`: the models are refitted every hour on cases whose target time is before now |
| `stations` | one entry per tide gauge (below) |
| `scoreboard` | live results from graded sealed forecasts: `scoreboard[model][lead] = {"graded": n, "mae_cm": x}`; empty until the first forecasts come due |
| `forecast_window` | `from` / `to`: sealed forecasts with target times in this window are listed under each station |
| `stale` | due forecasts whose gauge has not reported for more than 6 hours after the target: `{seq, station, model, target_time}`; they stay ungraded |
| `backtest` | copy of `runs/coast/backtest.json`: `table[model][lead] = {mae_cm, low, high, n}` (95% bootstrap interval), `paired_seed_vs[Persistence or DampedPersistence][lead] = {seed_mae, other_mae, diff_cm, low, high, p_seed_better, n}` (negative `diff_cm` means SeedPhysics has the lower error), `days`, `unit`, `note`. The intervals resample single cases; neighbouring hours and stations are correlated, so they are narrower than they should be. Since 2026-11-13 the backtest's weather inputs come from the Open-Meteo Previous Runs archive (a run published before each issue time); the older stitched "hindsight" weather leaked the future and is kept only as `runs/coast/backtest_hindsight.json` |

### `water.stations[]`

| field | meaning |
|---|---|
| `id`, `name`, `lat`, `lon` | NOAA CO-OPS station |
| `latest_obs_time`, `latest_obs_m` | newest 6-minute water level (metres above MLLW) |
| `current_residual_m` | water level minus tide table now (mean of the newest three 6-minute values within the last hour) |
| `tide` | official tide prediction from 24 h ago to 24 h ahead, metres above MLLW: `{start, step_min: 12, values: [...]}`; value *k* is at `start + k * step_min` |
| `observed` | observed water level for the last 48 h, same layout, `null` where the gauge had no value |
| `forecasts[model]` | sealed forecasts with target in `forecast_window`, as parallel arrays (index *k* is one forecast): `lead_h`, `target`, `issued`, `water_level_m` (tide + predicted residual), `residual_m`, `truth_water_level_m` and `error_cm` (`null` until graded) |

## `waves`

Same shape as `water`, with buoys instead of stations.

| field | meaning |
|---|---|
| `models` | `Persistence`, `WaveModel` (Open-Meteo marine wave model at the target time), `SeedSwell` (fitted blend of the wave model and damped persistence; it also has an offshore-buoy term delayed by the swell travel time, but that term's fitted weight is near zero, so it contributes about nothing) |
| `fit` | `fitted_at`, `train_cases` |
| `forecast_window` | as for water |
| `buoys[]` | `id`, `name`, `lat`, `lon`, `latest_obs_time`, `latest_wave_height_m`, `latest_dominant_period_s`, `upstream_buoy` and `upstream_distance_km` (offshore buoy used by SeedSwell), `observed` (hourly wave height, last 48 h), `wave_model` (hourly model wave height from 24 h ago to 48 h ahead), `forecasts[model]` with arrays `lead_h`, `target`, `issued`, `wave_height_m`, `truth_wave_height_m`, `error_cm` |
| `offshore[]` | offshore buoys: `id`, `latest_obs_time`, `latest_wave_height_m`, `observed` (hourly, last 48 h) |
| `scoreboard`, `stale` | as for water (`stale` entries carry `station` = buoy id) |

## `alerts[]`

`event`, `headline`, `area`, `severity`, `onset`, `ends`, `sender`. Only coastal and marine
events are kept (coastal flood, high surf, beach hazards, rip current, gale, hazardous seas,
small craft, storm, tsunami).

## Trimmed real example

From the first live cycles on 2026-11-13 (lists cut to two items, one station, one buoy, one model):

```json
{
 "generated_at": "2026-11-13T21:52:18Z",
 "ledger": {"path": "runs/coast/live", "records": 655, "graded": 0,
  "head_hash": "60000fc0e72628cabd37e96546eed39292de17af12cfa972609f49ffcf095843",
  "verify": {"ok": true, "checked": 655, "message": "ok"}},
 "cycle": {"now": "2026-11-13T21:52:09Z", "sealed_this_cycle": 0, "graded_this_cycle": 0,
  "wave_sealed_this_cycle": 0, "wave_graded_this_cycle": 0, "stations_sealed_this_cycle": 0,
  "seconds": 8.8, "fetch_log": []},
 "water": {
  "quantity": "water level above the tide table (residual), metres; errors in cm",
  "leads_h": [1, 3, 6, 12, 24],
  "models": ["TideTable", "Persistence", "DampedPersistence", "SeedPhysics"],
  "fit": {"fitted_at": "2026-11-13T21:52:09Z", "train_cases": 123142, "train_from": "2026-09-29T21:52:09Z"},
  "stations": [{
    "id": "9414290", "name": "San Francisco", "lat": 37.806305, "lon": -122.46589,
    "latest_obs_time": "2026-11-13T21:42:00Z", "latest_obs_m": 2.247, "current_residual_m": 0.6183,
    "tide": {"start": "2026-11-12T21:48:00Z", "step_min": 12, "values": [1.554, 1.502, "..."]},
    "observed": {"start": "2026-11-11T21:48:00Z", "step_min": 12, "values": [1.769, 1.685, "..."]},
    "forecasts": {"SeedPhysics": {
      "lead_h": [1, 3, "..."],
      "target": ["2026-11-13T22:42:00Z", "2026-11-14T00:42:00Z", "..."],
      "issued": ["2026-11-13T21:42:00Z", "2026-11-13T21:42:00Z", "..."],
      "water_level_m": [2.0437, 1.297, "..."],
      "residual_m": [0.6037, 0.562, "..."],
      "truth_water_level_m": [null, null, "..."],
      "error_cm": [null, null, "..."]}}}],
  "scoreboard": {},
  "forecast_window": {"from": "2026-11-12T21:52:18Z", "to": "2026-11-14T22:52:18Z"},
  "stale": [],
  "backtest": {"generated_at": "2026-11-13T21:43:54Z", "days": 90, "unit": "cm, mean absolute error",
   "table": {"SeedPhysics": {"1": {"mae_cm": 2.17, "low": 2.13, "high": 2.20, "n": 9913}}, "...": "..."},
   "paired_seed_vs": {"Persistence": {"24": {"seed_mae": 3.74, "other_mae": 5.20, "diff_cm": -1.45,
     "low": -1.53, "high": -1.37, "p_seed_better": 1.0, "n": 9384}}, "...": "..."},
   "note": "Weather inputs are leak-free: each value in a lead window comes from an Open-Meteo model run published before the issue time (...). Intervals resample single cases; neighbouring hours and stations are correlated, so the intervals are narrower than they should be. The tide table is off by about 32 cm on average because this is an unusually high sea-level season: (...)"}},
 "waves": {
  "quantity": "significant wave height (WVHT), metres; errors in cm",
  "leads_h": [1, 3, 6, 12, 24],
  "models": ["Persistence", "WaveModel", "SeedSwell"],
  "fit": {"fitted_at": "2026-11-13T21:52:09Z", "train_cases": 59869},
  "forecast_window": {"from": "2026-11-12T21:52:17Z", "to": "2026-11-14T22:52:17Z"},
  "buoys": [{
    "id": "46026", "name": "SAN FRANCISCO - 18NM West of San Francisco, CA", "lat": 37.75, "lon": -122.838,
    "latest_obs_time": "2026-11-13T20:40:00Z", "latest_wave_height_m": 3.8, "latest_dominant_period_s": 15.0,
    "upstream_buoy": "46059", "upstream_distance_km": 620,
    "observed": {"start": "2026-11-11T21:00:00Z", "step_min": 60, "values": [2.1, 2.1, "..."]},
    "wave_model": {"start": "2026-11-12T21:00:00Z", "step_min": 60, "values": [2.86, 2.84, "..."]},
    "forecasts": {"SeedSwell": {
      "lead_h": [1, 3, "..."],
      "target": ["2026-11-13T22:48:00Z", "2026-11-14T00:48:00Z", "..."],
      "issued": ["2026-11-13T21:48:00Z", "2026-11-13T21:48:00Z", "..."],
      "wave_height_m": [4.08, 4.204, "..."],
      "truth_wave_height_m": [null, null, "..."],
      "error_cm": [null, null, "..."]}}}],
  "offshore": [{"id": "46059", "latest_obs_time": "2026-11-13T20:20:00Z", "latest_wave_height_m": 9.8,
    "observed": {"start": "2026-11-11T21:00:00Z", "step_min": 60, "values": [4.27, 4.2, "..."]}}],
  "scoreboard": {},
  "stale": []},
 "backtest_waves": {"generated_at": "2026-11-13T21:49:39Z", "days": 44,
  "unit": "cm, mean absolute error of significant wave height",
  "table": {"SeedSwell": {"6": {"mae_cm": 26.8, "low": 26.0, "high": 27.7, "n": 2635}}, "...": "..."},
  "paired_seed_vs": "..."},
 "alerts": [{"event": "Coastal Flood Advisory",
   "headline": "Coastal Flood Advisory issued November 13 at 12:40PM PST until November 13 at 4:00PM PST by NWS Portland OR",
   "area": "South Washington Coast", "severity": "Minor", "onset": "2026-11-13T12:40:00-08:00",
   "ends": "2026-11-13T16:00:00-08:00", "sender": "NWS Portland OR"}]
}
```

Once forecasts come due, `scoreboard` looks like
`{"SeedPhysics": {"1": {"graded": 23, "mae_cm": 2.4}, "3": {...}}, "Persistence": {...}}`.

## Backtests (2026-11-13, `--until 2026-11-13T22:00:00Z`)

`python -m lab.tasks.coast.backtest --days 90 --until <ISO time> [--weather runs|hindsight|none]` and
`python -m lab.tasks.coast.backtest_waves --days 44 --until <ISO time> [--model runs|hindsight]`.
`--until` fixes the end of the data and is recorded in the JSON (`data_until`, `until_given`).
The default reads weather and wave-model history from previous model runs (published before each
issue time); `hindsight` reproduces the old leaky numbers for comparison only. NDBC buoy files
roll over after about 45 days, so a wave run can be repeated exactly only while its window is in
the file.

Test MAE (cm), SeedPhysics, leads 1 / 3 / 6 / 12 / 24 h: honest weather 2.17 / 3.82 / 4.35 / 4.46 /
4.01; hindsight weather 2.17 / 3.82 / 4.32 / 4.32 / 3.74; no weather 2.18 / 3.89 / 4.54 / 4.95 / 5.09
(DampedPersistence 2.18 / 3.92 / 4.74 / 5.07 / 5.12). Wave MAE (cm), SeedSwell: honest 18.4 / 23.8 /
28.2 / 28.9 / 34.8; hindsight 18.3 / 23.4 / 27.0 / 27.2 / 28.4; WaveModel honest 32.7 / 32.9 / 33.0 /
33.4 / 39.0. The intervals in both backtests resample single cases and are too narrow.
