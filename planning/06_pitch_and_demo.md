# Pitch and demo

Track 1: Build Your Own Autoresearch Framework. Format from the organizers: round 1 is a 1:30 pitch, a 1:30 live demo and 2:00 of questions; the finals are the same 3 minutes without questions. Also due by Sunday 14:30: a 2-minute demo video, the repository link and a short description.

Numbers marked **[event]** come from the runs made at the event. The figures in brackets are Friday's rehearsal, written here so we know what to expect; they are never said on stage as results.

## One-line pitch

An AI research lab that is graded by the future: it proposes a change to a forecasting formula, tests it on data from after its cut-off, keeps only what wins, and then locks in forecasts that real measurements score.

## What each scoring line gets (20 points each)

| Criterion | Where it lands in the five minutes |
|---|---|
| Technicality | The loop picture; the sandbox; the hash chain; intervals that resample whole station-days |
| Creativity | "Locked in before the answer exists." The seal marks on the chart |
| Usefulness | The opening number: the sea against the tide table, today, at the Golden Gate |
| Demo | A forecast sealed hours ago meeting its measurement, live |
| Track / sponsor alignment | The track's four verbs said out loud over the loop picture; Exa sources on screen; the notebook; Modal |

## Pitch (1:30)

**Problem (25 s).** "Right now the Pacific at the Golden Gate is about **[event: live number]** above the official tide table. Tide tables predict astronomy, not the ocean. Harbor crews and flood offices need the real level a few hours ahead. And when anyone says an AI can forecast, you can't tell whether it predicted or remembered."

**Idea (20 s).** "So we built a research lab that is graded by the future. It can only learn from before a cut-off date. Its forecasts are locked in a hash chain before the answer exists. Then NOAA's gauges grade them."

**The loop, one picture (30 s).** "This is the research loop, running on its own. It **proposes a hypothesis**: one change to a readable formula, with a reason. It **runs the experiment**: the formula is refitted in a sandbox and scored on later data it has never seen. It **reads the result**: where it still fails, by station and by hours ahead. And it **decides what to try next**: a change is kept only if it wins on one later window and doesn't lose on another. Code makes that call, not the model."

**Result (15 s).** "On two weeks it never saw, **[event: four numbers]**." [Rehearsal: tide table 32.7 cm, best simple rule 4.45, our hand-written formula 3.92, the lab's formula 3.17.]

## Live demo (1:30)

1. **Coast chart, San Francisco, 6 hours ahead (35 s).** "Magenta marks are forecasts we sealed. Ink is what the sea did. Blue is the tide table." Hover a filled mark: sealed at, predicted, measured. Point at the chain status: "every forecast is hashed onto the one before; we can't edit them."
2. **Scoreboard (15 s).** Read the sentence under it: the lab's forecaster against the best simple rule on the forecasts both made. Say it whichever way it stands. [Rehearsal, first evening, one hour ahead, 46 shared forecasts in a storm: not separated.]
3. **Lab view (30 s).** The error curve stepping down. Click one kept change: what it changed, where the idea came from, the Exa source link, its measured effect. Click one rejected change: "this one improved the first window and lost on the second, so it was thrown out."
4. **One table (10 s).** What does the work: random code changes, one model, with the notebook, with Exa. **[event]** [Rehearsal: random changes never passed; one model gave the whole gain; the notebook and free search added nothing we could measure.]

If the network dies: the dashboard reads local files and keeps working; the 2-minute video is on the desktop.

## Limits (say at least two, in the pitch or the first answer)

- "Our first design was a swarm of three roles. In our own controlled test it lost to a single model, so we cut it."
- "Memory and search: **[event: what the five-seed table showed, including if they added nothing]**."
- "One run moves by chance about as much as the arms differ. We use five seeds and compare seed by seed."
- "It's one season and one weekend, and this weekend is a storm."
- "We rehearsed this design on Friday. Every line in the repository was written here."

## Questions to expect

| Question | Answer |
|---|---|
| Doesn't the model already know the answer? | The targets are gauge readings and pilot reports that do not exist when the forecast is sealed. For the backtest, the agents never see truths outside the training window, and weather inputs come from runs that existed at the time. |
| Is the tide table really off by that much? | Yes. We recomputed it from raw NOAA data: a steady positive offset at every West Coast gauge this season, about 24 cm at San Francisco over 90 days and more in November. In the same weeks of 2024 and 2025 it was 0 to 8.5 cm. |
| Isn't "the gap continues" doing all the work? | It is a strong baseline and it is on every table. The hand-written starting formula only beats it from three hours out. On the test window the lab's forecaster beats it at every lead, including one hour (1.71 vs 2.09 cm). On the first evening of sealed forecasts, in a storm, it had not: 3.64 vs 3.32 cm on 46 shared forecasts, not separated. **[final]** |
| How do you know the lab's gain isn't overfitting? | A change must win on one later window and not lose on another, with statistics that resample whole station-days. The lab does see its scores on both of those windows, so they are somewhat optimistic. The final window is never shown to the lab and is scored once, with the code hash recorded. |
| Could the agents cheat? | Their code gets no truths for the windows it is judged on, no network and no files. Blocked attempts are logged. |
| What did search add? | Every tested idea is logged with its source and whether the model already knew it. The ledger includes the failures. **[final numbers]** (Rehearsal, free scholarly search: nothing measurable. The ideas it supplied that passed were ones the model already had.) |
| Why not a neural network? | Forecast offices and operators need rules they can read and audit, and a formula with a dozen coefficients can be refit every hour on a laptop. |
| Is the self-improvement real? | Two different things. The forecaster improves: 19% below our own formula on data it never saw. Whether the lab itself gets better at research from one problem to the next, we tested with controls. **[state the result plainly, including if the warm lab only matched the cheat-sheet control]** |
| Who would use this? | Harbor operations, coastal road and flood crews, and anyone who forecasts a measured quantity with a formula. The loop needs only a history, a way to ask "what was known at this time", and a measurement that arrives later. |
| What was built before the event? | No code. We rehearsed the design on Friday on our own machine, which is how we knew which data sources work and that our first swarm design was worse than a single model. The repository was empty on Saturday at 10:20 and everything in it was written here. |
| How is this an autoresearch framework and not a forecasting app? | The coast is the problem it is pointed at. The framework is the loop: a problem is four time-ordered windows and a loss; candidates are code; the judge is code. Pointing it at another measured quantity means writing one adapter. |

## Slides (three; the rest is the live dashboard)

1. The gap: tide table versus the sea, today. One number.
2. The loop in one picture, labelled with the track's four verbs.
3. The test-window table and the ladder table, side by side, with the limits underneath.

## Submission text (draft, fill numbers on Sunday)

**What it does.** foresight-lab is an AI research lab that is graded by the future. It reads the literature on a forecasting problem, turns one idea at a time into a change to a readable forecasting formula, keeps the change only if it wins on later data, and then seals live forecasts in a hash chain that real measurements grade.

**Sponsor use.** Exa runs in the main loop: every generation it supplies a board of ideas, and every change that used one links to its source. past.dev is the lab's shared notebook of what was tried and what it did. Modal runs each untrusted candidate in its own sandbox and runs the sealing job on a schedule. We report what each added as measured, including where it added nothing.

**What was prepared.** The design was rehearsed the day before the event. All code in the repository was written at the event.
