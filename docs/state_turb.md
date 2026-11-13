# Aviation turbulence: live state file

`runs/turb/live/state.json` is rewritten at the end of every live cycle by
`.venv/bin/python -m lab.tasks.turb.live run --interval 1800` (cycles start at
:05 and :35 past each hour). The dashboard only reads this file. It is about
0.2-0.5 MB.

## What is being forecast

Where aircraft over the continental US (24-50 N, 126-66 W) will report
moderate-or-greater turbulence in the next 1-6 hours. Every cycle, each model
seals one risk grid per valid hour (+1 ... +6 h) in the hash-chained ledger
`runs/turb/live` (`lab/core/ledger.py`). When a valid hour has passed by at
least 45 minutes, the record is graded with the pilot reports at flight level
200 and above within 30 minutes of the valid time.

Read these before trusting any number:

* **Pilot reports are a biased sample.** They come from where aircraft fly
  (busy routes, not the whole grid), and pilots report bumps far more often
  than smooth air. A base rate or AUC describes reported turbulence, not all
  airspace.
* **Classes.** Intensity codes: NEG/smooth 0, light 2, light-moderate 3,
  moderate 4, moderate-severe 5, severe 6, extreme 8 (NEG with light = 1;
  severe-extreme = 7). Positive = code >= 4. Negative = code <= 2.
  Light-moderate (3) is dropped as ambiguous; a group naming two adjacent
  intensities ("LGT OCNL MOD", "MOD CHOP OCNL LGT") counts as light-moderate.
* **Headline altitude.** Scoring uses reports at FL200 and above (clear-air
  turbulence altitudes). Lower reports are stored but scored separately (only
  in the backtest's `lower_levels` block).
* **Backtest lead.** Open-Meteo's past field values are its short-lead
  forecasts stitched together, so the backtest uses fields at lead under about
  one hour. Only the live sealed forecasts test 1-6 hour lead skill.

## Data

| Source | Use | Notes |
|---|---|---|
| Iowa Environmental Mesonet PIREP archive (`/cgi-bin/request/gis/pireps.py?fmt=csv&sts=..&ets=..&artcc=_ALL`) | 31-day training history, recent reports | about 4,700 reports a day, 10-minute delay; unquoted CSV; no airline AIREPs |
| Aviation Weather Center `api/data/pirep?format=json&age=2&bbox=..` | recent reports | at most 400 reports per call, so the box is split into four; includes automated airline AIREPs ("ARP ...") |
| Open-Meteo `v1/forecast` pressure levels 150, 200, 250, 300, 400, 500 hPa (wind speed, direction, temperature, geopotential height) | fields | grid 2.5 degrees, 11 x 24 = 264 points; see budget below |
| AWC `api/data/gairmet?format=json&hazard=turb-hi&fore=0,3,6,9,12` | official baseline (live only) | TURB-HI polygons with base/top flight levels |

The same report from both pilot-report sources is kept once (same minute and
same report body from "/OV" on, or same minute, position, level and code).
Positions are the ones the sources decode; this code does not decode navaid
radials.

**Open-Meteo budget (measured from the `X-RateLimit-Cost` header).** A call
costs (variables / 10) x max(1, days / 14) per location. With 24 variables:
the 32-day backfill costs 1,448 units once; a live refresh (6 past hours + 12
forecast hours) costs 634 units. The free tier allows 600 units per minute
(the downloader paces itself on `X-RateLimit-Remaining`) and about 10,000 a
day, so the live job refreshes fields at most every 3 hours (about 5,100 units
a day). Cycles in between reuse the latest download.

**Available time.** Every stored field hour has the time it could have been
known: forecast hours at the download time, past hours one hour before their
valid time. Cases and live grids only use values available at their issue
time (`tests/test_turb_cases.py` checks this by corrupting everything later).

## Models

| Model | Risk (higher = more likely moderate or greater) | Units in grids |
|---|---|---|
| Climatology | base rate of the training window | probability |
| WindSpeed | wind speed | m/s |
| Shear | vertical wind shear between adjacent levels (geopotential thickness) | m/s per km |
| Richardson | 1 / (1 + max(Ri, 0)) | 0..1 |
| Ellrod1 | TI1 = vertical shear x horizontal deformation | 1e-7 s^-2 |
| SeedBlend | logistic regression on standardized speed, log shear, log deformation, log Ri, N^2, divergence, log temperature gradient | probability |
| GAirmet | 1 inside a TURB-HI G-AIRMET polygon whose altitude band holds the level, else 0; nearest 3-hourly snapshot within 90 minutes | 0/1 |

Diagnostics are computed on the grid levels (layer values averaged onto
levels) and interpolated linearly in time, log pressure, latitude and
longitude. Flight level is converted to pressure with the standard atmosphere.

## Ledger records

One sealed record per model and valid hour per cycle (about 42 per cycle).

* `case_id`: `turb/<model>/<valid hour>/issued-<issue time>`
* `target_time`: the valid hour; `issued_at`: the cycle time; `sealed_at`:
  the wall-clock time of writing (a record whose valid hour would be less
  than a minute away is dropped, never sealed late)
* `prediction.risk`: grid `[level][lat][lon]`, rounded to 3 decimals
  (`null` where fields were missing); `prediction.grid`: the grid spec;
  `prediction.units`; GAirmet records also carry `polygons` and
  `snapshot_valid_time`
* `meta`: `lead_hours` (1-6, valid hour minus the hour of issue),
  `lead_exact_hours`, `fields_available_at`, `train_reports`
* grade `truth`: `window_minutes` (30), `n`, and `reports`: list of
  `{time, lat, lon, fl, code}` for upper-level reports in the window

## state.json fields

| Field | Meaning |
|---|---|
| `generated_at` | cycle time (UTC, ISO 8601 with Z) |
| `task` | `"turb"` |
| `ledger.records` | sealed records in `runs/turb/live` |
| `ledger.graded` | records with a grade |
| `ledger.head` | hash of the last sealed record |
| `ledger.verify` | `{ok, checked, message}` from `SealedLedger.verify()` |
| `grid.lats`, `grid.lons` | grid axes (degrees; 25.0..50.0 and -125.0..-67.5 every 2.5) |
| `grid.levels_hpa`, `grid.flight_levels` | the six levels and the flight level each stands for (standard atmosphere); `grid.levels` is the same as a list of `{hpa, flight_level}` |
| `grid.resolution_deg` | 2.5 |
| `issued_at` | issue time of the latest cycle's records |
| `forecasts.SeedBlend`, `forecasts.Ellrod1` | the latest cycle's sealed grids, one entry per valid hour: `{valid_time, lead_hours, seq, risk}`; `risk` is `[level][lat][lon]` in the order of `grid` (heat map with a time slider over entries and a level selector over the first index) |
| `risk_units` | units of the two grids above |
| `jet_250hpa` | for the same valid hours: `{valid_time, speed_ms, direction_deg}`, each `[lat][lon]` at 250 hPa (direction the wind blows from) |
| `reports_12h` | upper-level pilot reports of the last 12 hours, oldest first: `time, lat, lon, fl, code, label` (1, 0 or null for light-moderate), `raw` (first 200 characters), `graded` |
| `reports_12h[].graded` | null until a record covering the report is graded; then per model: `risk` (sealed value at the report), `percentile` (share of that grid level's values below it, 0-100), `lead_hours`, `valid_time`, `seq`, `in_top20`, `outcome` (`hit` / `miss` for positives, `false_alarm` / `correct_negative` for negatives, `top20` / `not_top20` for light-moderate). The shortest-lead record is shown |
| `scoreboard.<model>.<lead>` | `lead` is "1".."6" or "all": `n` graded reports with a class, `positives`, `auc`, `hit_rate` (positives in the top 20%), `false_alarm_rate` (negatives in the top 20%). Per model, valid hour and lead only the latest-sealed record counts |
| `seedblend_coefficients` | coefficients of the SeedBlend fitted this cycle (per standard deviation) |
| `backtest` | copy of `runs/turb/backtest.json`: `generated_at, days, labelled_cases, positives, base_rate, train, test, table` (per model `auc, low, high, n, positives`), `brier`, `paired_SeedBlend_vs_Ellrod1`, `seedblend_coefficients`, `caveats` |
| `notes` | the caveats above in one line each |

## Trimmed real example

From the cycle at 2026-11-13T22:12:09Z (arrays cut with `"..."`; no record had
been graded yet, so `scoreboard` is empty and `graded` is null).

```json
{
 "generated_at": "2026-11-13T22:12:09Z",
 "task": "turb",
 "ledger": {"records": 161, "graded": 0,
            "head": "f9b03186a4e4854ff8fa1a525bb105fbbccc86609d51bcbde1ab28d16dda3819",
            "verify": {"ok": true, "checked": 161, "message": "ok"}},
 "grid": {"lats": [25.0, 27.5, 30.0, "..."], "lons": [-125.0, -122.5, -120.0, "..."],
          "levels_hpa": [150, 200, 250, 300, 400, 500],
          "flight_levels": [446, 387, 340, 301, 236, 183], "resolution_deg": 2.5,
          "levels": [{"hpa": 150, "flight_level": 446}, "..."]},
 "issued_at": "2026-11-13T22:12:09Z",
 "forecasts": {
  "SeedBlend": [{"valid_time": "2026-11-13T23:00:00Z", "lead_hours": 1, "seq": 124,
                 "risk": [[[0.013, 0.06, 0.103, "..."], [0.027, 0.015, 0.01, "..."], "..."], "..."]},
                "... 5 more valid hours"],
  "Ellrod1": [{"valid_time": "2026-11-13T23:00:00Z", "lead_hours": 1, "seq": 123,
               "risk": [[[0.082, 0.629, 1.548, "..."], [0.254, 0.193, 0.08, "..."], "..."], "..."]},
              "... 5 more valid hours"]},
 "risk_units": {"Ellrod1": "TI1, 1e-7 s^-2", "SeedBlend": "probability"},
 "jet_250hpa": [{"valid_time": "2026-11-13T23:00:00Z",
                 "speed_ms": [[26.1, 20.4, 21.2, 24.4, "..."], "..."],
                 "direction_deg": [[260.0, 270.0, 275.0, 279.0, "..."], "..."]}, "..."],
 "reports_12h": [
  {"time": "2026-11-13T22:07:00Z", "lat": 26.7875, "lon": -93.0027, "fl": 300.0, "code": 0, "label": 0,
   "raw": "GLS UA /OV VUH140179/TM 2207/FL300/TP B738/TB NEG/RM ZHU", "graded": null}],
 "scoreboard": {},
 "seedblend_coefficients": {"intercept": -1.1891, "speed": -0.0566, "log_vws": 0.7263, "log_deform": 0.4067,
                            "log_ri": 0.0758, "n2": -0.1416, "div": 0.0348, "log_tgrad": 0.2524},
 "backtest": {"generated_at": "2026-11-13T22:00:51Z", "days": 30, "labelled_cases": 26182, "positives": 7128,
              "base_rate": 0.2722,
              "train": {"n": 13412, "positives": 3606, "from": "2026-10-14T21:51:00Z", "to": "2026-11-01T21:18:00Z"},
              "test": {"n": 6002, "positives": 1654, "from": "2026-11-07T22:00:00Z", "to": "2026-11-13T20:58:00Z"},
              "table": [{"model": "Ellrod1", "auc": 0.6863, "low": 0.6716, "high": 0.7012, "n": 6002, "positives": 1654},
                        {"model": "SeedBlend", "auc": 0.6947, "low": 0.6813, "high": 0.7097, "n": 6002, "positives": 1654}],
              "brier": {"SeedBlend": {"mean": 0.1796, "low": 0.1744, "high": 0.1846},
                        "Climatology": {"mean": 0.1997, "low": 0.1941, "high": 0.2048},
                        "skill_vs_climatology": 0.1003},
              "paired_SeedBlend_vs_Ellrod1": {"diff": 0.0084, "low": -0.0001, "high": 0.0171,
                                              "share_a_better": 0.974, "n_boot": 1000},
              "seedblend_coefficients": {"intercept": -1.2382, "...": "..."},
              "caveats": ["Caveat: Open-Meteo's past field values are its short-lead forecasts stitched together, ..."]},
 "notes": ["Pilot reports are biased toward where aircraft fly and toward reporting bumps rather than smooth air.", "..."]
}
```

A graded report entry looks like this (shape from `tests/test_turb_live.py`):

```json
{"time": "...", "lat": 38.1, "lon": -97.2, "fl": 340.0, "code": 4, "label": 1, "raw": "...",
 "graded": {"SeedBlend": {"risk": 0.412, "percentile": 91.3, "lead_hours": 1,
                          "valid_time": "2026-11-13T23:00:00Z", "seq": 124,
                          "in_top20": true, "outcome": "hit"},
            "Ellrod1": {"...": "..."}, "GAirmet": {"...": "..."}}}
```

## Commands

```
.venv/bin/python -m lab.tasks.turb.backtest --days 30     # table, writes runs/turb/backtest.json
.venv/bin/python -m lab.tasks.turb.live once              # one cycle
nohup .venv/bin/python -m lab.tasks.turb.live run --interval 1800 > runs/turb/live/loop.log 2>&1 &
.venv/bin/python -m pytest tests/test_turb_*.py
```

Caches: `cache/turb/iem/` (one CSV per settled UTC day), `cache/turb/fields/`
(compressed `.npz` chunks: `backfill_*` once, `live_*` every 3 hours),
`cache/turb/pireps_live.csv.gz` (reports fetched by the live job).
