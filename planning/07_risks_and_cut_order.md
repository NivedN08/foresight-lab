# Risks and cut order

## What could go wrong

| Risk | How likely | What it costs | What we do |
|---|---|---|---|
| Pre-written code is not allowed | Unknown until kickoff | We cannot submit the rehearsal repo | Rebuild from `09_rebuild_guide.md` in priority order; the rehearsal told us what works. Submit only code written on site and say plainly what was prepared (plans, test designs, data access). |
| The live machine sleeps or loses network | Happened twice on Friday | Missed sealing cycles; nothing graded while it sleeps | Lid open, plugged in. Move sealing to a scheduled Modal function on Saturday morning. The jobs catch up on waking. |
| A free data source rate-limits or goes down | Happened Friday (one weather host stopped answering) | Models lose an input; a job can stall | Every source is cached; a host that fails twice is skipped for ten minutes; weather has a second host; models run on gauge data alone if needed. |
| Calm weather on Sunday | Likely after the storm passes | Smaller gaps to forecast; live differences between models shrink | Report by lead time and keep Friday night and Saturday's graded forecasts on the scoreboard. The backtest covers 90 days. |
| The lab's gain does not hold on the test window | Possible | Claim 1 weakens | Selection and confirmation windows already guard against it. If it fails we report the smaller number and what was overfit. |
| The warm lab is no better than a fresh lab given the old forecaster | Plausible | Claim 2 becomes "reuses answers" | Report it as the finding. Pacific's judge still sees forecast accuracy on their data. |
| past.dev is slower or costlier than a local vector store | Their own published run showed this once | The memory table is mixed | Report all three columns; lead with accuracy and as-of recall. |
| Model calls throttle under many parallel proposals | Possible on shared credits | Runs slow down | Cap concurrency; use the smaller model for analyst and scout; the loop resumes cleanly. |
| Pacific's task is not what we expect | Possible | Adapter work on Saturday | The generic time-series adapter needs only a table of timestamps and values. Budget two hours. |
| A judge asks whether the backtest leaks the future | Certain | Credibility | We found one leak ourselves and fixed it; show the before and after and the leak tests. |
| Agent-written code misbehaves | Low | A crashed evaluation | It runs in a sandbox with no network or file access; failures are logged and the run continues. |

## Cut order

If the Saturday 18:00 checkpoint is missed, cut from the top. Each cut removes a slide, not the project.

1. Tinker fine-tune of the proposer.
2. Learned correction terms trained on a GPU.
3. The second physical problem as a full lab run (keep its live view).
4. past.dev as an experiment arm with seeds (keep one demo run and the as-of recall demonstration).
5. Search backend comparison (keep search on versus off).
6. Shuffled-notebook control (keep fresh, fresh plus old forecaster, warm).
7. Seeds beyond three per arm.
8. The reviewer role and lineage polish on the dashboard.

## The smallest version that is still a result

- One problem with live sealed forecasts and a verified chain.
- The lab against the baselines on the test window, three seeds, intervals that resample whole days.
- Fresh versus warm on Pacific's data, three seeds.
- A dashboard showing forecasts sealed before the fact and graded after.
- A limitations slide.
