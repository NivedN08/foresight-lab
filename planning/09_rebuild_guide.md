# Rebuild guide

Use this only if the organizers say code written before the event cannot be submitted. It lists what to build on site, in priority order, with the lessons from the rehearsal so nothing is rediscovered the hard way. Submit only what is written at the event, and say plainly what was prepared beforehand: plans, test designs, knowledge of the data sources.

Each block lists what it is, the tests that prove it, and the traps we already hit. Times assume one person with a coding assistant.

## Order

| # | Block | Time | Needed for |
|---|---|---|---|
| 1 | Sealed ledger | 30 min | Everything live |
| 2 | Problem interface, time splits, block statistics | 45 min | Everything else |
| 3 | One problem end to end: data, cases, baselines, seed formula, backtest | 2–3 h | Claim 1 |
| 4 | Live sealing job and state file | 1–1.5 h | The demo |
| 5 | Sandbox | 1.5 h | Running agent-written code |
| 6 | Evaluator | 1 h | The lab |
| 7 | Research loop with notebook and search | 2–3 h | Claims 1–4 |
| 8 | Dashboard | 2–3 h | The demo |
| 9 | Generic time-series adapter for the track task | 1.5 h | Pacific |
| 10 | Sponsor backends (Modal, Exa, past.dev) | 1 h each | Sponsor prizes |
| 11 | Experiment runners and the one-time test scorer | 1.5 h | The tables |

Blocks 1–2, 5 and 8 are independent and can be built in parallel by different people from the start. Block 3 is the long pole.

## 1. Sealed ledger

Append-only file; each record holds the hash of the one before it. Truth goes in a separate file.

Rules: a forecast can be sealed only for a target time still in the future; graded only at or after its target; graded once.

Tests: chain verifies; editing a record is detected; deleting a record is detected; sealing the past is refused; early and double grading are refused.

## 2. Problem interface and statistics

A case is (id, issue time, target time, inputs). Inputs hold only what was known at the issue time. Four windows in time order: train, select, confirm, test.

Statistics: paired comparison on the same cases, resampling whole blocks (station-day).

Trap: resampling single cases gave intervals several times too narrow, because errors an hour apart at the same place move together.

## 3. First problem end to end

For water level: residual = observed minus the official tide prediction. Leads 1, 3, 6, 12, 24 hours. Baselines: tide table (zero), current gap continues, damped version, physics formula (damped persistence, pressure change, wind, recent trend; least squares per lead).

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

Tests: parsers on saved samples; residual arithmetic; a leak test that corrupts everything after the issue time and shows the inputs do not change; coefficients recovered on synthetic data.

## 4. Live job

Every cycle: refresh data, fit on history strictly before now, build cases for now, seal one record per station, model, lead and target hour, grade what is due, verify the chain, write a state file for the dashboard.

Traps:
- **A long sleep call does not advance while the computer sleeps.** Schedule on the wall clock in short naps.
- **One dead host froze a cycle for an hour.** Short timeouts, and skip a host for ten minutes after two failures.
- **Keep the machine awake** (`caffeinate -i -w <pid>`), lid open. Better: run the job on a scheduled Modal function.
- **Lead labels:** measure lead as target time minus sealing time, exactly.

## 5. Sandbox

Agent-written code runs in a child process: static checks first (allowed imports only, no double-underscore access, no file or network helpers, size caps, no large literal tables), then an isolated interpreter with a minimal environment, time and memory limits, and on macOS `sandbox-exec` denying network, file writes and reads outside an allowlist. Results come back as JSON only. Truths for the windows being judged are never sent to the child.

Tests must really run the child: each banned thing is caught; an infinite loop is killed; too much memory fails without killing the parent; a network call and a file write that slip past the static checks are blocked at run time.

Trap: macOS refuses an address-space limit, so memory is enforced by watching the child's size.

## 6. Evaluator

Fit on train inside the sandbox; predict select and confirm; compute losses in the parent. The test window is refused unless explicitly allowed. Prepare the encoded cases once and reuse them: this cut one evaluation from about 10 s to about 5 s.

## 7. Research loop

Per proposal: a failure digest computed by code; analyst (diagnosis, hypothesis, a knowledge gap); closed-book answer; search and extraction of one claim; engineer writes the full candidate; evaluate; decide in code; write a finding.

Decision rule: wins on select (95% of block resamples, at least 0.3% lower loss) and is not worse on confirm.

Lessons from the first runs:
- The confirm window matters: the first proposal that improved select was worse on confirm.
- Ten generations of six proposals took 14 minutes and about $7 with a small model for analysis and a mid-size model for code, and cut loss by 12% on select and 15% on confirm.
- Keyless scholarly search is rate-limited and often off-topic; retry, fall back, and record which backend answered.
- Make the engineer change one thing per proposal, or findings cannot be interpreted.

## 8. Dashboard

Static page, no build step, libraries and fonts bundled. Reads each application's state file through a small local server. The design brief is `docs/dashboard_design.md`: a printed-chart look, magenta only for what was sealed in advance, ink for what was measured, blue for the official reference.

## 9–11

See `docs/series.md`, `04_sponsor_playbook.md` and `03_experiment_protocol.md`.
