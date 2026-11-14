# On-site build plan

The event's rule is "Build entirely during the event. No prior commits to the repo." So this is the plan, not a fallback. On Saturday at 10:20 we start an empty repository and write everything in it.

**What we bring and what we do not.** We bring what we learned on Friday: this plan, the test lists, which data sources work, and the traps below. We do not bring files. Nothing is copied from the rehearsal repository into the event repository, by hand or by a tool, and the rehearsal's numbers are not event results. The README and the pitch say that the design was rehearsed the day before and that every line in the repository was written at the event.

Each block lists what it is, the tests that prove it, and the traps we already hit. Times assume one person with a coding assistant.

## Order and lanes

| # | Block | Time | Lane | Needed for |
|---|---|---|---|---|
| 1 | Sealed ledger | 30 min | Smaran | Everything live |
| 2 | Problem interface, time windows, block statistics | 45 min | Nived | Everything else |
| 3 | The coast problem end to end: data, cases, baselines, starting formula, rewind test | 2–3 h | Atul | The result |
| 4 | Sealing job and state file | 1–1.5 h | Atul | The demo |
| 5 | Sandbox | 1.5 h | Nived | Running model-written code |
| 6 | Evaluator and decision rule | 1 h | Nived | The lab |
| 7 | Research loop: one strong model per proposal | 1.5 h | Nived | Track 1 |
| 8 | Dashboard: coast view, then lab view | 2–3 h | Smaran | The demo |
| 9 | Exa ideas board in the main loop, with sources kept | 1 h | Smaran | Exa challenge |
| 10 | Notebook: local store, then past.dev | 1–1.5 h | Smaran | past.dev challenge |
| 11 | Experiment runner, report, one-time test scorer | 1.5 h | Nived | The tables |
| 12 | Modal: sandboxed evaluations and the scheduled sealing job | 1–2 h | whoever is free | Modal prize |

Blocks 1, 2, 3 and 5 start at once, in parallel. Block 3 is the long pole, and sealing has a deadline that changes its order: build the gauge client, the residual and the two simple rules first, and seal those by 12:00 Saturday with a first version of block 4. A 24-hour forecast sealed at noon is graded at noon Sunday; one sealed after about 14:00 is not graded before submissions close at 14:30. The weather inputs, the full case list and the starting formula follow in the afternoon.

**Not rebuilt:** the turbulence application, the generic time-series adapter, the three-role pipeline, the transfer experiment. The rehearsal showed the first is a second demo we cannot afford, the second belongs to another track, and the last two did not work.

## What Friday's rehearsal measured (so we do not rediscover it)

- One strong model per proposal plus a strict judge did the work. On a window the lab never saw, the best of 35 runs was 19% below the hand-written formula (3.17 against 3.92 cm; best simple rule 4.45; tide table 32.7). Two forecasters not picked that way were 12 to 13% below it: expect that from a typical run.
- A three-role pipeline (analyst, scout, engineer) was worse and cost twice as much. Random code changes never passed.
- The notebook, free scholarly search, and carrying a notebook to a second problem showed no measurable effect. Whether Exa or past.dev do is the open question the event runs answer; report it either way.
- The same setup with the same seed moved by 0.1 to 0.55 cm between runs. Two seeds say nothing. Use five, and compare arms seed by seed.
- On live forecasts in a storm the lab's forecaster was level with "the gap continues" at one hour and worse at three. Find out why early: look at what its extra terms do when the residual is far outside the training range.
- A run of 24 proposals takes about four minutes and about one dollar of model use. The whole ladder (23 runs) took about an hour and a half on one laptop.

## 1. Sealed ledger

Append-only file; each record holds the hash of the one before it. Truth goes in a separate file.

Rules: a forecast can be sealed only for a target time still in the future; graded only at or after its target; graded once.

Tests: chain verifies; editing a record is detected; deleting a record is detected; sealing the past is refused; early and double grading are refused.

## 2. Problem interface and statistics

A case is (id, issue time, target time, inputs). Inputs hold only what was known at the issue time. Four windows in time order: train, select, confirm, test.

Statistics: paired comparison on the same cases, resampling whole blocks (station-day).

Trap: resampling single cases gave intervals several times too narrow, because errors an hour apart at the same place move together.

**What a forecaster is.** One Python source file defining `class Forecaster` with a `name`, `fit(cases, truths)` (called once with the train window) and `predict(case)` (called once per later case; returns a dict with the target key, a finite float). It may import `math` and `numpy` only, is capped in size, and may not carry tables of literal numbers. Baselines, the starting formula and every model-written candidate use this one interface, so they are all scored by the same code.

## 3. First problem end to end

For water level: residual = observed minus the official tide prediction. Leads 1, 3, 6, 12, 24 hours. Baselines: tide table (zero), current gap continues, damped version, physics formula (damped persistence, pressure change, wind, recent trend; least squares per lead).

**What each case gives the forecaster** (everything knowable at the issue time; this list is what let the lab find its gains, so build all of it):

| Input | Meaning |
|---|---|
| station, lat, lon, lead in hours | which gauge and how far ahead (1, 3, 6, 12, 24) |
| current residual and its age in minutes | newest gauge reading minus the tide prediction |
| hourly residual for the last 72 hours | the gauge's own recent history; 12 and 25 hours back are one and two tidal cycles |
| 72-hour mean and 3-hour trend of the residual | the season's offset and where it is heading |
| tide prediction now, at the target, and for every hour between | the astronomical tide the residual rides on |
| forecast pressure and wind at the gauge for every hour from issue to target | from a weather run that existed at the issue time; a flag says whether it is complete |
| the same recent residuals at the neighbouring gauges to the north and south | surges and slow coastal waves show up next door first; this was the lab's most reused idea |
| wave height at the nearest buoy, when there is one | missing for many gauges; say so |
| hour of day, day of year | |

Cases are a fixed random sample of hourly issue times, spread evenly over stations and leads: 12,000 train, 6,000 select, 6,000 confirm, 8,000 test, over 90 days. The description shown to the model must not quote any figure from the test period.

Data sources and what we learned about each:

| Source | Notes |
|---|---|
| NOAA tide gauges (CO-OPS API) | 6-minute data; about a month per request, so request in chunks. Observations and predictions share datum and time zone if both are requested as MLLW, metric, GMT. |
| NOAA buoys (NDBC realtime files) | Only about 45 days of history. "MM" means missing. |
| Open-Meteo weather | The main host stopped answering from our network for hours. The previous-runs host kept working and also serves the current forecast. Cache everything. |
| Open-Meteo previous runs | Gives each hour as forecast one or two days earlier. This is what makes the backtest honest. |
| Open-Meteo marine | Wave model forecasts; previous-run variables work on the marine host itself. |

Traps:
- **Future weather in the backtest.** Past values from a forecast API are near-final values, not forecasts. Using them made the 24-hour error look like 3.7 cm when the honest figure is 4.0 cm (and 5.1 cm with no weather at all). Use forecasts from runs that existed at the issue time; for a 24-hour lead that means a run at least two days old.
- **A split by time needs a gap.** The last training target must be earlier than the first selection issue time.
- **Backtests need a fixed end time** (`--until`), or two runs differ.
- **Keep people off the test period too.** On Friday our first rewind tests scored the starting formula on dates that later became the test window, and its weather inputs were fixed after seeing them. Cut the four windows first; run every exploratory rewind test on train and select only.

Tests: parsers on saved samples; residual arithmetic; a leak test that corrupts everything after the issue time and shows the inputs do not change; coefficients recovered on synthetic data.

## 4. Live job

Every cycle: refresh data, fit on history strictly before now, build cases for now, seal one record per station, model, lead and target hour, grade what is due, verify the chain, write a state file for the dashboard.

Traps:
- **A long sleep call does not advance while the computer sleeps.** Schedule on the wall clock in short naps.
- **One dead host froze a cycle for an hour.** Short timeouts, and skip a host for ten minutes after two failures.
- **Keep the machine awake** (`caffeinate -i -w <pid>`), lid open, plugged in. The laptop slept four times on Friday and each time the jobs stopped. Better: run the job on a scheduled Modal function.
- **Lead labels:** measure lead as target time minus sealing time, exactly.

## 5. Sandbox

Agent-written code runs in a child process: static checks first (allowed imports only, no double-underscore access, no file or network helpers, size caps, no large literal tables), then an isolated interpreter with a minimal environment, time and memory limits, and on macOS `sandbox-exec` denying network, file writes and reads outside an allowlist. Results come back as JSON only. Truths for the windows being judged are never sent to the child.

Tests must really run the child: each banned thing is caught; an infinite loop is killed; too much memory fails without killing the parent; a network call and a file write that slip past the static checks are blocked at run time.

Trap: macOS refuses an address-space limit, so memory is enforced by watching the child's size.

## 6. Evaluator

Fit on train inside the sandbox; predict select and confirm; compute losses in the parent. The test window is refused unless explicitly allowed. Prepare the encoded cases once and reuse them: this cut one evaluation from about 10 s to about 5 s.

## 7. Research loop

Per proposal: a failure digest computed by code, then ONE call to a strong model that sees the problem, the current forecaster, the digest, the notebook (if on) and a board of scouted ideas (if search is on), and writes the full candidate with a header saying what changed and where the idea came from (own, notebook, search). Evaluate; decide in code; write a finding. Scouting runs once per generation with a small model.

Decision rule: wins on select (95% of block resamples, at least 0.3% lower loss) and is not worse on confirm.

Lessons from the rehearsal runs:
- The confirm window matters: the first proposal that improved select was worse on confirm.
- Make each proposal change one thing, or findings cannot be interpreted.
- **Make the proposals in one generation differ.** With a notebook, up to all four proposals in a generation picked the same entry and wrote the same change. Give each slot a different place to look, and show each one what the others in its generation chose if they run in sequence.
- Free scholarly search is fragile. OpenAlex's keyless daily allowance is shared by network address and ran out; arXiv needs three seconds between requests and returns off-topic papers because it ORs the words; Crossref worked every time. Space the requests, do not retry a "daily limit" reply, and record which source answered. With an Exa key, use Exa.
- Ask the model, before it sees any search result, to list what it would try. Mark each search-derived change as already listed or new. In the rehearsal four of five search-derived changes that passed were already listed.
- Do not build the three-role pipeline (analyst, scout, engineer made to implement the scouted idea). We built it first and a single strong model beat it at a third of the cost per proposal. Give the strong model the notebook and the scouted ideas as optional inputs instead.
- Keep confirm-window figures out of everything the agents read; tell them only whether the later-window check passed.

## 8. Dashboard

Static page, no build step, libraries and fonts bundled. Reads the state file through a small local server. The look we settled on: a printed nautical chart; magenta only for what was sealed in advance, ink for what was measured, blue for the official tide table.

Traps:
- **A scoreboard that averages each model over its own forecasts misleads.** The lab's forecaster starts hours after the hand-written ones, so its column covers different hours. Compare it with the others only on forecasts they all made, and say how many those are.
- The demo is 90 seconds. Build the coast view first: one station's curve with sealed marks and the measured line, the scoreboard, the chain status. The lab view (score curve, list of changes with sources) is second.

## 9. Exa in the main loop

Once per generation a small model writes two or three queries from the failure digest; Exa answers; the hits become a short board of ideas, each with its source link; the proposing model may use one or ignore the board and must say which. The rule for the challenge: at least one Exa call in the main loop, and the demo shows the sources. Keep every hit's title and link with the change that used it, so the lab view can show them.

## 10. Notebook

One finding per tested change: what was tried, where the idea came from, the measured effect on the select window, and whether the later-window check passed. Keep confirm-window figures out. Local store first (plain text search is enough), then past.dev behind the same two calls (write a finding, recall for a query). For the past.dev challenge the comparison is: no notebook, everything pasted into the prompt, local store, past.dev; report error, time and cost separately, five seeds.

## 11. Experiment runner, report, test scorer

Arms x seeds, each run in its own process, resumable, with a cost cap. The report compares arms seed by seed and refuses to call a difference with fewer than five seeds. The time-to-target goal is fixed before the runs: 5% below the starting formula on the confirm window.

Test scorer traps:
- Write down which forecaster gets the look before looking (lowest confirm loss across finished runs).
- Score every baseline **and the starting formula** in the same look. On Friday the scorer left the starting formula out and it took a second look to get it.
- Log every look with the hash of the code.

## 12. Modal

Evaluations: one sandbox per candidate, many at once. Sealing job: a scheduled function writing the ledger to a volume, so no laptop has to stay awake. Untested before the event; budget time to read their docs.
