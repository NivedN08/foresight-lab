# Risks and cut order

## What could go wrong

| Risk | How likely | What it costs | What we do |
|---|---|---|---|
| The on-site build runs late | Likely somewhere | Fewer hours of graded forecasts; fewer seeds | The rule is that everything is built at the event. `09_rebuild_guide.md` orders the work so the sealing job and one lab run exist by Saturday dinner. Cut from the list below rather than skipping tests on the ledger, the sandbox or the windows. |
| Someone is tempted to paste in rehearsal code | Possible at 3am | Disqualification, and it would be dishonest | Nothing is copied from the rehearsal repository. The README and pitch say the design was rehearsed and the code was written here. |
| The live machine sleeps or loses network | Happened four times on Friday | Missed sealing cycles; nothing graded while it sleeps | Lid open, plugged in, a keep-awake command tied to the job. The job catches up on waking. A scheduled Modal function would remove the risk but is the first thing cut. |
| A free data source rate-limits or goes down | Happened Friday (one weather host stopped answering) | Models lose an input; a job can stall | Every source is cached; a host that fails twice is skipped for ten minutes; weather has a second host; models run on gauge data alone if needed. |
| Calm weather on Sunday | Likely after the storm passes | Smaller gaps to forecast; live differences between models shrink | Report by lead time, on forecasts every model made. The rewind test covers 90 days. |
| The lab's forecaster is not better than "the gap continues" on live forecasts | Happened on Friday evening, in a storm: level at one hour ahead (46 shared forecasts), worse at three hours (23 shared forecasts, one hour's worth) | The live scoreboard does not show a win | Say so. The live chart proves the forecasts were locked in and graded; the test window carries the accuracy claim. Show both, and look at why the storm hours go wrong. |
| 24-hour forecasts are not graded in time | Certain for anything sealed after about 14:00 Saturday | No live 24-hour number for the lab's forecaster | Seal the simple rules by noon Saturday so the 24-hour column is not empty; report the lab's live results for 1 to 12 hours and say why. |
| The lab's gain does not hold on the test window | Did hold in rehearsal (best run 19% below the starting formula, typical run 12 to 13%) | The main result weakens | Selection and confirmation windows guard against it. If it fails we report the smaller number and what was overfit. |
| Exa or past.dev add nothing measurable | Likely: the notebook and free search added nothing in rehearsal | The sponsor tables are null results | Report them as measured, with five seeds. Show what they did contribute: sources for each change, the list of what was tried. Do not dress a null as a win. |
| past.dev is slower or costlier than a local vector store | Their own published run showed this once | The memory table is mixed | Report all three columns; lead with accuracy and as-of recall. |
| Model calls throttle under many parallel proposals | Possible on shared credits | Runs slow down | Cap concurrency; use the smaller model for writing search queries; the loop resumes cleanly. |
| A judge asks whether the backtest leaks the future | Certain | Credibility | We found one leak ourselves and fixed it; show the before and after and the leak tests. |
| Agent-written code misbehaves | Low | A crashed evaluation | It runs in a sandbox with no network or file access; failures are logged and the run continues. |

## Cut order

If the Saturday 19:00 checkpoint is missed, cut from the top. Each cut removes a slide, not the project.

1. Modal (run everything on laptops; keep the live machine awake).
2. past.dev as an experiment arm with seeds (keep one demo run).
3. The comparison of Exa against free scholarly search (keep Exa on versus off).
4. The lab view on the dashboard beyond a score curve and a list of changes.
5. Seeds beyond three per arm (and then say the arms cannot be ranked).
6. Weather inputs from earlier forecast runs for the rewind test (then the 24-hour lead is reported without weather terms, not with leaked ones).

## The smallest version that is still a result

- Water level at the West Coast gauges with live sealed forecasts and a verified chain.
- The loop: one model proposes a change, the code tests it on later data and keeps or rejects it.
- The lab's forecaster against the tide table, the simple rules and the starting formula on a window it never saw, with intervals that resample whole station-days.
- Random code changes as the control.
- A dashboard showing forecasts sealed before the fact and graded after.
- The limits, said out loud.
