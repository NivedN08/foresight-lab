# Aviation turbulence: live state file

`runs/turb/live/state.json` is rewritten at the end of every live cycle by
`.venv/bin/python -m lab.tasks.turb.live run --interval 1800` (cycles start at
:05 and :35 past each hour). The dashboard only reads this file. It is about
0.2-0.5 MB.

## What is being forecast

Where aircraft over the continental US (24-50 N, 126-66 W) will report
moderate-or-greater turbulence in the next 1-6 hours. Every cycle, each model
seals one risk grid per valid hour (the next six whole hours) in the
hash-chained ledger `runs/turb/live` (`lab/core/ledger.py`). When a valid hour
has passed by at least 75 minutes (so late reports count), the record is
graded with the pilot-report layers at flight level 200 and above within 30
minutes of the valid time, but only if the report store covers that whole
window (see "Coverage, gaps and interrupted cycles").

Read these before trusting any number:

* **Pilot reports are a biased sample.** They come from where aircraft fly
  (busy routes, not the whole grid), and pilots report bumps far more often
  than smooth air. A base rate or AUC describes reported turbulence, not all
  airspace.
* **The backtest is not a forecast test.** Open-Meteo's past field values are
  stitched from model runs started at or after each valid hour, and those runs
  may have assimilated the aircraft observations being predicted. The
  backtest is therefore an analysis-time (hindsight-field) comparison of
  turbulence indices: fair between models, because all share the same inputs,
  but not evidence of forecast skill. Only the live sealed forecasts test
  forecast skill. A test with day-old forecast fields was tried: Open-Meteo's
  Previous Runs API rejects pressure-level variables (`wind_speed_250hPa_previous_day1`
  is "invalid" for the default, GFS and ECMWF models, checked 2026-11-13), so
  no such fields were available.
* **Intervals.** Backtest intervals resample whole UTC days of the test
  window (reports cluster by hour and day); hour-block intervals are a second
  line. With about seven test days the day-block intervals are themselves
  rough. The backtest prints a verdict sentence generated from the day-block
  interval for SeedBlend minus Ellrod1; the same sentence is in `state.json`.
* **Coarse deformation.** Deformation on a 2.5-degree grid is far coarser than
  in the operational Ellrod index, which uses model grids of tens of
  kilometres, so Ellrod1 here is a weak version of the operational index.

## Labels

Intensity codes: NEG/smooth 0, light 2, light-moderate 3, moderate 4,
moderate-severe 5, severe 6, extreme 8 (NEG with light in one layer = 1,
severe-extreme = 7). Positive = code >= 4. Negative = code <= 2.
Light-moderate (3) is dropped as ambiguous.

A turbulence group can describe several layers. Each layer becomes its own
labelled point:

| Group (aircraft level) | Labelled points (code, flight level) |
|---|---|
| `CONS LGT OCNL MOD 370 NEG 390` (FL370) | (3, 370), (0, 390) |
| `CONT LGT -MOD BLO 460 NEG ABV 460` (FL465) | (0, 465); the light-moderate layer is below the aircraft's level and is dropped |
| `LGT 240-260 MOD 300-340` (FL300) | (2, 250), (4, 320) |
| `LGT CHOP BLW 230` (FL070) | (2, 70): a bound keeps the aircraft's level and never moves a report to the upper levels |
| `LGT SFC-060/ LGT ABV 060` (FL100) | (2, 30), (2, 100) |

Two adjacent intensities in one layer ("LGT-MOD", "LGT -MOD", "CONS LGT OCNL
MOD", "MOD CHOP OCNL LGT", "LIGHT TO MOD") give the in-between code. A range
gives its middle; a single level gives that level; a bound alone ("BLO 230",
"ABV 060") gives the aircraft's own flight level if it satisfies the bound,
otherwise the layer is dropped; nothing gives the aircraft's flight level.
Headline scoring uses points at FL200 and above (clear-air turbulence
altitudes). Lower points are stored and scored separately (only in the
backtest's `lower_levels` block).

## Data

| Source | Use | Notes |
|---|---|---|
| Iowa Environmental Mesonet PIREP archive (`/cgi-bin/request/gis/pireps.py?fmt=csv&sts=..&ets=..&artcc=_ALL`) | 31-day training history, recent reports | about 4,700 reports a day, 10-minute delay; unquoted CSV; no airline AIREPs |
| Aviation Weather Center `api/data/pirep?format=json&age=2&bbox=..` | recent reports | at most 400 reports per call, so the box is split into four; includes automated airline AIREPs ("ARP ...") |
| Open-Meteo `v1/forecast` pressure levels 150, 200, 250, 300, 400, 500 hPa (wind speed, direction, temperature, geopotential height) | fields | grid 2.5 degrees, 11 x 24 = 264 points; see budget below |
| AWC `api/data/gairmet?format=json&hazard=turb-hi&fore=0,3,6,9,12` | official baseline (live only) | TURB-HI polygons with base/top flight levels |

The live job keeps every raw API response under `cache/turb/raw/<source>/`
and appends each fetched report to `cache/turb/pireps_live_reports.csv`. That
file is append-only: rows are never rewritten; duplicates are removed and the
labels re-derived from the raw text each time it is read. (The first version,
`cache/turb/pireps_live.csv.gz`, was rewritten on every merge; it is now read
only, and no raw responses were kept for its period, 2026-11-13 21:54-23:49 Z.)
The same report from both sources is kept once (same minute, report body from
"/OV" on, and layer; or same minute, position, level and code). Positions are
the ones the sources decode; this code does not decode navaid radials.

**Open-Meteo budget (measured from the `X-RateLimit-Cost` header).** A call
costs (variables / 10) x max(1, days / 14) per location. With 24 variables:
the 32-day backfill costs 1,448 units once; a live refresh (6 past hours + 12
forecast hours) costs 634 units. The free tier allows 600 units per minute
(the downloader paces itself) and about 10,000 a day, so the live job
refreshes fields at most every 3 hours (about 5,100 units a day). Cycles in
between reuse the latest download.

**Available time.** Every stored field hour carries an "available" time.
For live forecast downloads it is the download time, which is real. For the
history it is a bookkeeping convention (one hour before the valid time) used
only to order and merge chunks; it is not when those values existed (see
above). Cases use only values whose recorded time is at or before their issue
time; `tests/test_turb_cases.py` checks that this rule holds in the code, which
matters for live forecasts but cannot make hindsight fields into forecasts.

## Coverage, gaps and interrupted cycles

**Coverage.** Each fetch adds its new reports to the live store and then
appends the time spans it covered to
`cache/turb/pireps_live_reports.csv.coverage.jsonl`: `{source, from, to,
fetched_at}`, source `awc` (last 2 hours) or `iem` (last 6 hours, or one
archive day). A clock hour counts as covered by a fetch only if the fetch was
made at least 60 minutes after the hour ended (so late reports had time to
arrive) and returned at least one report in it (across the whole country an
empty hour means the source had nothing yet, not calm air). In practice a
record is graded about two hours after its valid hour, and the 2-hour AWC
fetch rarely covers a whole window on its own. Once a cycle the file is
rewritten with each source's overlapping or adjacent spans merged and spans
older than 14 days dropped, so it stays small. A record is graded only when
the spans together cover its whole window (valid time +-30 minutes). Otherwise it stays
ungraded and is tried again next cycle. If a due record's window is not
covered, the cycle backfills from the IEM archive every UTC day from that
window to now (at most 14 days back; days IEM already covers are skipped).
A record still uncovered 7 days after its window ends is never graded: it is
appended to `runs/turb/live/unrecoverable.jsonl` (`{seq, sealed_hash,
target_time, recorded_at}`), leaves the due list for good (also after a
restart) and is counted as unrecoverable. Archive days are cached under
`cache/turb/iem/` for good only if fetched at least 24 hours after the day
ended and not empty; otherwise they are fetched again when needed. This rule
exists because, after the freeze of 2026-11-14/16 (about 35.7 hours for
turbulence, 35.5 for coast), the first cycle graded 329 overdue records, 217 with
no reports at all, against a store that only held the last few hours.

**Gap record.** After a cycle seals, a stretch of more than two intervals
(one hour at the default 30 minutes) since the previous seal is recorded as
one line in
`runs/turb/live/gaps.jsonl`: `task`, `from` (last seal before the gap), `to` (the first seal after it), `hours`, `cause` (always "no forecast sealed in this period": cycles may have run and failed, and the job cannot know why), `seq_before` and `head_before` (chain position before the gap), `seq_after` (first seq sealed after it), `due_while_away` (records whose target fell inside the gap), `graded_late` (how many of those are graded now), `regraded` (how many of those graded ones later had their grade corrected, see "Regrades"; `graded_late - regraded` were graded late with complete reports), `unrecoverable` (how many never will be), `recorded_at`, and `updated_at` once a follow-up line exists. A gap is written once (same
`task` and `from`); later counts are appended as follow-up lines
`{follow_up_of: {task, from}, graded_late, unrecoverable, regraded, recorded_at}` and
no line is edited (follow-up lines written before regrades existed lack
`regraded`; it reads as 0).

**Regrades.** A grade whose truth was formed from incomplete data can be
corrected once, in `runs/turb/live/regrades.jsonl` beside the ledger:
`{seq, sealed_hash, truth, reason, replaces: {graded_at, n}, recorded_at}`
(`SealedLedger.regrade`). This is allowed only when the original truth was
formed from incomplete data, and `reason` must say what was incomplete. The
original line in `grades.jsonl` stays as it is and remains readable; a record
can be regraded only if it has a grade, and only once. The scoreboard,
`reports_12h[].graded` and every other reader of `SealedLedger.joined()` use
the corrected truth (the row carries `regraded: true` and `grade.regrade` with
the reason). `verify` also checks that every regrade names an existing sealed
record with the matching `sealed_hash` and the grade it replaces. The 329
grades of the first cycle after the 35.7-hour freeze (graded_at
2026-11-16T08:50:18Z) are the case this exists for; `scripts/turb_regrade.py`
corrects them with reason
"graded without report coverage after a 35.7 h freeze; corrected from the IEM
archive"; it recomputes each truth with the job's grading function from the
IEM archive merged with the live store, only where the archive covers the
whole window, writes nothing without `--apply`, and skips records already
regraded. Gap lines are not in `sealed.jsonl` and do not affect
`verify`. The check scans the whole chain, so the first cycle after this
change also records the gaps already in the history (on the current state:
2.1, 2.02 and 35.74 hours for turbulence; 2.13 and 35.5 hours for coast).

**Interrupted cycle.** Downloaded fields are stamped with the time the
download finished, and the cycle's issue time moves to that time, so a
forecast never uses fields stamped after its issue time. If sealing would
happen more than 10 minutes after the cycle's (possibly moved) start time,
nothing is sealed and the cycle starts again with the current time (at most
twice).

**Known limit (older than these rules).** The G-AIRMET polygons are fetched
during sealing, a little after `issued_at`, so the GAirmet baseline can see
an official product issued in those seconds.

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
The live models are refitted every cycle on the history (hindsight fields)
and applied to forecast fields.

## Ledger records

One sealed record per model and valid hour per cycle (about 42 per cycle).

* `case_id`: `turb/<model>/<valid hour>/issued-<issue time>`
* `target_time`: the valid hour; `issued_at`: the cycle time; `sealed_at`:
  the wall-clock time of writing (a record whose valid hour would be less than
  a minute away is dropped, never sealed late)
* `prediction.risk`: grid `[level][lat][lon]`, rounded to 3 decimals (`null`
  where fields were missing); `prediction.grid`: the grid spec;
  `prediction.units`; GAirmet records also carry `polygons` and
  `snapshot_valid_time`
* `meta.lead_minutes`: valid time minus sealing time, the real lead;
  `meta.valid_hour_offset`: which of the next six whole hours (1-6) this is,
  not a lead; `meta.fields_available_at`, `meta.train_reports`. Records sealed
  before 2026-11-13 23:49 Z carry `lead_hours` (= the offset) and
  `lead_exact_hours` instead; their real lead is always `target_time -
  sealed_at`, which is what the scoreboard uses for every record.
* grade `truth`: `window_minutes` (30), `n`, and `reports`: list of
  `{time, lat, lon, fl, code, layer}` for upper-level report layers in the window.
  Grades written from 2026-11-16 on also carry `sources` (the sources whose
  coverage spans fall in the window: `["awc"]`, `["iem"]` or both) and
  `coverage` (`"complete"`, the only value a grade can have). Older grades
  lack both and were not checked for coverage
* regrade (in `regrades.jsonl`, not in `grades.jsonl`): the corrected `truth`
  in the same shape, with `sources` and `coverage`, plus `reason` and
  `replaces: {graded_at, n}` of the original grade

## state.json fields

| Field | Meaning |
|---|---|
| `generated_at` | cycle time (UTC, ISO 8601 with Z) |
| `task` | `"turb"` |
| `ledger.records` | sealed records in `runs/turb/live` |
| `ledger.graded` | records with a grade |
| `ledger.head` | hash of the last sealed record |
| `ledger.verify` | `{ok, checked, message}` from `SealedLedger.verify()` |
| `gaps` | stretches with nothing sealed, from `runs/turb/live/gaps.jsonl`, oldest first, each with the counts of its latest follow-up line, including `regraded` (empty if none) |
| `regrades` | corrected grades from `runs/turb/live/regrades.jsonl`: `{count, reasons, valid_from, valid_to}`, the valid times of the earliest and latest affected forecast (`count` 0 and nulls if none) |
| `grid.lats`, `grid.lons` | grid axes (degrees; 25.0..50.0 and -125.0..-67.5 every 2.5) |
| `grid.levels_hpa`, `grid.flight_levels` | the six levels and the flight level each stands for (standard atmosphere); `grid.levels` is the same as a list of `{hpa, flight_level}` |
| `grid.resolution_deg` | 2.5 |
| `issued_at` | issue time of the latest cycle's records |
| `forecasts.SeedBlend`, `forecasts.Ellrod1` | the latest cycle's sealed grids, one entry per valid hour: `{valid_time, sealed_at, valid_hour_offset, lead_minutes, seq, risk}`; `risk` is `[level][lat][lon]` in the order of `grid` (heat map with a time slider over entries and a level selector over the first index) |
| `risk_units` | units of the two grids above |
| `jet_250hpa` | for the same valid hours: `{valid_time, speed_ms, direction_deg}`, each `[lat][lon]` at 250 hPa (direction the wind blows from) |
| `reports_12h` | upper-level report layers of the last 12 hours, oldest first: `time, lat, lon, fl` (the layer's level), `code, label` (1, 0 or null for light-moderate), `raw` (first 200 characters), `graded` |
| `reports_12h[].graded` | null until a record covering the layer is graded; then per model, from the latest-sealed graded record for that valid hour: `risk` (sealed value at the point), `percentile` (share of that grid level's values below it, 0-100), `lead_minutes`, `valid_time`, `seq`, `in_top20`, `outcome` (`hit` / `miss` for positives, `false_alarm` / `correct_negative` for negatives, `top20` / `not_top20` for light-moderate) |
| `scoreboard.<model>.at_least_<k>h` | k = 1..6. For each graded report layer and model, the latest record sealed at least k hours before the valid time: `n` layers with a class, `positives`, `auc`, `hit_rate` (positives in the top 20%), `false_alarm_rate` (negatives in the top 20%). Missing keys mean no graded record that old yet |
| `seedblend_coefficients` | coefficients of the SeedBlend fitted this cycle (per standard deviation) |
| `backtest` | from `runs/turb/backtest.json`: `generated_at, days, labelled_cases, positives, base_rate, train, test, interval_method`, `table` (per model `auc`, day-block `low`/`high`, `blocks`, `hour_block_low`/`hour_block_high`, `n`, `positives`), `brier` (`SeedBlend`, `Climatology`, `difference` with day-block `difference_low`/`difference_high`, `skill_vs_climatology`), `paired_SeedBlend_vs_Ellrod1` (`diff, low, high, blocks, n_boot, verdict`), `verdict`, `seedblend_coefficients`, `caveats` |
| `notes` | the caveats in one line each, ending with the backtest verdict |

## Trimmed real example

From the cycle at 2026-11-13T23:49:41Z (arrays cut with `"..."`). No record had been graded
yet, so `scoreboard` is empty and `graded` is null; the shape of a graded entry follows.

```json
{
 "generated_at": "2026-11-13T23:49:41Z",
 "task": "turb",
 "ledger": {
  "records": 371,
  "graded": 0,
  "head": "1248e3b246ae992c79ba7c8afd58fc763c6cb109fb51fa7c1a0f70ca40869a1e",
  "verify": {
   "ok": true,
   "checked": 371,
   "message": "ok"
  }
 },
 "grid": {
  "lats": [
   25.0,
   27.5,
   30.0,
   "..."
  ],
  "lons": [
   -125.0,
   -122.5,
   -120.0,
   "..."
  ],
  "levels_hpa": [
   150,
   200,
   250,
   300,
   400,
   500
  ],
  "flight_levels": [
   446,
   387,
   340,
   301,
   236,
   183
  ],
  "resolution_deg": 2.5,
  "levels": [
   {
    "hpa": 150,
    "flight_level": 446
   },
   "..."
  ]
 },
 "issued_at": "2026-11-13T23:49:41Z",
 "forecasts": {
  "SeedBlend": [
   {
    "valid_time": "2026-11-14T00:00:00Z",
    "sealed_at": "2026-11-13T23:50:25Z",
    "valid_hour_offset": 1,
    "lead_minutes": 9.6,
    "seq": 334,
    "risk": [
     [
      [
       0.005,
       0.031,
       0.105,
       "..."
      ],
      [
       0.005,
       0.013,
       0.002,
       "..."
      ],
      "..."
     ],
     "..."
    ]
   },
   "... 5 more valid hours"
  ],
  "Ellrod1": [
   {
    "valid_time": "2026-11-14T00:00:00Z",
    "sealed_at": "2026-11-13T23:50:25Z",
    "valid_hour_offset": 1,
    "lead_minutes": 9.6,
    "seq": 333,
    "risk": [
     [
      [
       0.026,
       0.44,
       1.515,
       "..."
      ],
      [
       0.092,
       0.212,
       0.036,
       "..."
      ],
      "..."
     ],
     "..."
    ]
   },
   "... 5 more valid hours"
  ]
 },
 "risk_units": {
  "Ellrod1": "TI1, 1e-7 s^-2",
  "SeedBlend": "probability"
 },
 "jet_250hpa": [
  {
   "valid_time": "2026-11-14T00:00:00Z",
   "speed_ms": [
    [
     26.9,
     20.9,
     21.1,
     24.8,
     "..."
    ],
    "..."
   ],
   "direction_deg": [
    [
     259.0,
     267.0,
     272.0,
     277.0,
     "..."
    ],
    "..."
   ]
  },
  "..."
 ],
 "reports_12h": [
  {
   "time": "2026-11-13T23:41:00Z",
   "lat": 34.3465,
   "lon": -119.0218,
   "fl": 210.0,
   "code": 4,
   "label": 1,
   "raw": "BIH UA /OV FIM250007/TM 2341/FL210/TP TBM/TB MOD/RM AWC-WEB",
   "graded": null
  }
 ],
 "scoreboard": {},
 "seedblend_coefficients": {
  "intercept": -1.1975,
  "speed": -0.0599,
  "log_vws": 0.7222,
  "log_deform": 0.398,
  "log_ri": 0.0673,
  "n2": -0.1396,
  "div": 0.0365,
  "log_tgrad": 0.2413
 },
 "backtest": {
  "generated_at": "2026-11-13T23:30:40Z",
  "days": 30,
  "labelled_cases": 26382,
  "positives": 7126,
  "base_rate": 0.2701084072473656,
  "train": {
   "n": 13683,
   "positives": 3662,
   "from": "2026-10-14T23:25:00Z",
   "to": "2026-11-01T23:25:00Z"
  },
  "test": {
   "n": 5980,
   "positives": 1649,
   "from": "2026-11-08T00:00:00Z",
   "to": "2026-11-13T23:25:00Z"
  },
  "interval_method": "Intervals resample whole UTC days of the test window (reports cluster by hour and day); hour-block intervals are shown as a second line. With about seven test days the day-block intervals are themselves rough.",
  "table": [
   {
    "model": "Ellrod1",
    "auc": 0.6858,
    "low": 0.6522,
    "high": 0.7396,
    "blocks": 6,
    "hour_block_low": 0.667,
    "hour_block_high": 0.7056,
    "n": 5980,
    "positives": 1649
   },
   {
    "model": "SeedBlend",
    "auc": 0.6955,
    "low": 0.6631,
    "high": 0.744,
    "blocks": 6,
    "hour_block_low": 0.6765,
    "hour_block_high": 0.7154,
    "n": 5980,
    "positives": 1649
   }
  ],
  "brier": {
   "SeedBlend": 0.17961751867276624,
   "Climatology": 0.19977901542437915,
   "difference": -0.020161496751612936,
   "difference_low": -0.030544633745593575,
   "difference_high": -0.012350936208442421,
   "blocks": "UTC days",
   "skill_vs_climatology": 0.1009189914605646
  },
  "paired_SeedBlend_vs_Ellrod1": {
   "diff": 0.009726093590442408,
   "low": -0.0037914198520305753,
   "high": 0.0237142645944598,
   "blocks": 6,
   "n_boot": 1000,
   "verdict": "SeedBlend and Ellrod1 are not separated: the day-block interval for the difference in AUC (-0.004 to +0.024) includes zero."
  },
  "verdict": "SeedBlend and Ellrod1 are not separated: the day-block interval for the difference in AUC (-0.004 to +0.024) includes zero.",
  "seedblend_coefficients": {
   "intercept": -1.2386,
   "...": "..."
  },
  "caveats": [
   "Hindsight fields: Open-Meteo's past values are stitched from model runs started at or afte ..."
  ]
 },
 "notes": [
  "Pilot reports are biased toward where aircraft fly and toward reporting bumps rather than smooth air.",
  "...",
  "Backtest (hindsight fields): SeedBlend and Ellrod1 are not separated: the day-block interval for the difference in AUC (-0.004 to +0.024) includes zero."
 ]
}
```

A graded report layer (shape from `tests/test_turb_live.py`):

```json
{"time": "...", "lat": 38.1, "lon": -97.2, "fl": 340.0, "code": 4, "label": 1, "raw": "...",
 "graded": {"SeedBlend": {"risk": 0.412, "percentile": 91.3, "lead_minutes": 52.4,
                          "valid_time": "2026-11-14T01:00:00Z", "seq": 380,
                          "in_top20": true, "outcome": "hit"},
            "Ellrod1": {"...": "..."}, "GAirmet": {"...": "..."}}}
```

A scoreboard entry: `"SeedBlend": {"at_least_1h": {"n": 812, "positives": 190, "auc": 0.68, "hit_rate": 0.41, "false_alarm_rate": 0.17}, ...}` (shape only; numbers illustrative).

## Commands

```
.venv/bin/python -m lab.tasks.turb.backtest --days 30     # table, writes runs/turb/backtest.json
.venv/bin/python -m lab.tasks.turb.live once              # one cycle
nohup .venv/bin/python -m lab.tasks.turb.live run --interval 1800 >> runs/turb/live/loop.log 2>&1 &
.venv/bin/python scripts/turb_regrade_dry_run.py           # read-only report on the unsound grades
.venv/bin/python scripts/turb_regrade.py --live-dir runs/turb/live [--apply]   # print, or append the regrades
.venv/bin/python -m pytest tests/test_turb_*.py
```

Caches: `cache/turb/iem/` (one CSV per settled UTC day), `cache/turb/fields/`
(compressed `.npz` chunks: `backfill_*` once, `live_*` every 3 hours),
`cache/turb/raw/` (every live API response), `cache/turb/pireps_live_reports.csv`
(append-only report store).
