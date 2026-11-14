# Pitch and demo

Draft from rehearsal numbers (Nov 13). Numbers marked **[final]** must be replaced from the Sunday-morning report and the one-time test score; do not present rehearsal figures as final.

## One-line pitch

We built an AI research lab that is graded by the future: it cuts itself off at a date, learns only from before it, locks in forecasts, and lets real measurements score them.

## Three-minute script

**0:00 Problem (20 s).**
"Right now the Pacific at the Golden Gate is about half a metre higher than the official tide table says. Tide tables predict astronomy, not the ocean's mood. And when anyone claims an AI can forecast, you can't tell whether it predicted or remembered. So we made it predict things that haven't happened yet."

**0:20 Live proof (40 s).** Dashboard, Coast view.
"Every hour the lab seals forecasts for 23 tide gauges, one to twenty-four hours ahead, in a hash chain, and every fifteen minutes it grades the ones that have come due. It can't edit them afterwards. Then NOAA's gauges grade them. Magenta is what we sealed; ink is what the sea did; blue is the tide table."
Point at a filled seal mark; hover it: sealed time, predicted level, measured level.
Number to say: sealed forecasts graded so far, then the sentence under the scoreboard, which compares the lab's forecaster with the other models on the same forecasts. Read that sentence, not the row: the lab's forecaster started later, so its column covers fewer hours. **[final]** (Rehearsal, Friday 10pm, one hour ahead, 46 shared forecasts: tide table 58.3 cm, "the gap continues" 3.32 cm, the lab's forecaster 3.64 cm; difference +0.32 cm, interval −0.20 to +0.92. Not separated, during a storm. Say whichever way it stands on Sunday.)

**1:00 How the lab works (30 s).** Dashboard, Lab view.
"The forecaster is a readable Python formula. Each proposal is one model that sees the current formula, where it fails, the lab's notebook of what has been tried, and a board of ideas scouted from search. It changes one thing, says where the idea came from, and the change is tested on the past: fit on one window, kept only if it wins on a later window and doesn't lose on a still later one. The agents never see those answers; the code runs in a sandbox with no network and no files."
Show the score curve stepping down and the ledger of claims with sources.
Number to say: the test window. **[final]** (Rehearsal: on two weeks the lab never saw, 8,000 forecasts, the tide table was off by 32.7 cm, the best simple rule by 4.45 cm, our hand-written formula by 3.92 cm and the lab's formula by 3.17 cm. A run is 24 proposals, about four minutes and about one dollar.)

**1:30 Evidence (50 s).** One table each, about ten seconds.
- Test window, scored once: lab vs starting formula vs best simple rule vs tide table. **[final]** (Rehearsal: 3.17 vs 3.92 vs 4.45 vs 32.66 cm; the lab is 1.28 cm below the best simple rule on the same forecasts, interval 1.06 to 1.50; better at every lead from 1 to 12 hours, level with our formula at 24.)
- What does the work? A ladder, five seeds: random code changes (no gain), single agent, researcher, researcher with notebook. **[final]** (Rehearsal: 3.24 cm with no gain, then 2.82, 2.89 and 2.73 cm. The three that use a model are not separated.)
- Does the lab get better at research? Fresh lab vs a lab that already worked on another problem, same budget, on a problem neither had seen, with a control that gets only a cheat sheet. **[final]**
- Search: share of kept changes that came from ideas the model did not know closed-book; useful ideas per 100 searches by search engine. **[final]** (Rehearsal, free scholarly search only: 19 of 96 proposals took an idea from search, 5 passed, and 4 of those 5 were an idea the model had already listed without search. Final error was no lower with search. Exa not yet tested.)
- Memory: none, full context, vector store, past.dev; error, minutes and dollars to a fixed target. **[final]** (Rehearsal: 3.05, 2.93 and 3.00 cm; inside run-to-run noise; about 25% more cost. past.dev not yet tested.)

**2:20 Limits (20 s).**
"Four things we want you to know. Zero: our first design, a three-role swarm, lost to a single agent in our own controlled test, so we rebuilt it and measured what memory and search add. **[final: say what the ladder showed, including if they added nothing]** (Rehearsal: so far, nothing we can measure. One strong model and a strict judge did the work. The same arm run twice differs by up to 0.4 cm, which is as big as the gaps between arms, so we ran five seeds and still could not rank them.) One: our own checker caught our first backtest using weather that wasn't available at the time; fixed, and the 24-hour number went from 3.7 to 4.0 cm. Two: the hand-written starting formula is no better than 'the current gap continues' at one hour ahead; whether the lab's forecaster is better there is decided by the test window and the sealed forecasts, and we show whichever it is. Three: it's one season and one weekend."

**2:40 Next (20 s).**
"The same lab ran on [Pacific's GPU prices] this weekend: here are its sealed forecasts for next week's prices. The chain keeps running after we leave, so you can check us."

## The demo, click by click

1. Coast view open on San Francisco, 6-hour lead. Let a poll land.
2. Hover one filled seal mark and one struck one. Say both.
3. Switch the scoreboard to "Backtest"; point at the note about the leak fix.
4. Lab view: curve, then filter the ledger to "kept"; open one source link.
5. Open the current forecaster's source; scroll for two seconds. "It's a formula a person can read."
6. Turbulence view for ten seconds: "Second problem. Same lab. Pilot reports grade it."
7. Back to Coast for questions.

If the network dies: the dashboard reads local files and keeps working; the backup video is on the desktop.

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
| Who would use this? | Harbor operations, coastal road and flood crews, and anyone who forecasts a measured quantity with a formula: the same loop ran on turbulence and on GPU prices. |
| What was built before the event? | **[answer truthfully once the rule is known: either "we rehearsed and rebuilt on site" or "we prepared this repository beforehand, which the rules allow"]** |

## Slides (seven)

1. The gap: tide table versus the sea, today.
2. Sealed, then graded: the live scoreboard.
3. The lab loop in one picture.
4. Test-window table.
5. Does it get better at research? The controlled comparison.
6. Search and memory: two small tables.
7. Limits and what happens next.

## Submission text (draft, fill numbers on Sunday)

**What it does.** foresight-lab is an AI research lab that is graded by the future. It reads the literature on a forecasting problem, turns one idea at a time into a change to a readable forecasting formula, keeps the change only if it wins on later data, and then seals live forecasts in a hash chain that real measurements grade.

**Sponsor use.** Exa supplies the ideas the lab tests, and every kept change links to its source. past.dev is the lab's shared, dated notebook, read "as of" a past time when the lab is replayed. Modal runs each untrusted candidate in its own sandbox and runs the sealing job on a schedule. The Track 2 task runs through the same loop.
