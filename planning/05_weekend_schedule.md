# Weekend schedule (Track 1, built on site)

Times are Pacific and follow the organizers' schedule. All code is written at the event in a new, empty repository; `09_rebuild_guide.md` is the build plan. Roles are a suggestion; swap freely.

## The clock

Hacking runs from Saturday 10:20 to Sunday 14:30: 28 hours, minus sleep. The plan needs a working loop and a sealing job by Saturday dinner, so that experiments run overnight and forecasts collect grades for 20 hours before judging.

## Who owns what

| Owner | Lane | Builds |
|---|---|---|
| Nived | The lab | Problem windows and statistics, sandbox, evaluator, research loop, experiment runner, the one-time test scorer; the pitch |
| Atul | The sea | Data clients with a cache, cases, baselines, the starting formula, the rewind test, the sealing job and its state file |
| Smaran | The room | Sealed ledger (first hour), dashboard, sponsor backends (Exa in the loop, past.dev notebook, Modal), demo video, submission page |

One laptop is the live machine: plugged in, lid open, running the sealing job and the dashboard from the moment the job works. Nobody develops on it. If Modal credits arrive, the sealing job moves to a scheduled Modal function and the laptop stops mattering.

## Saturday

| Time | What | Done when |
|---|---|---|
| 08:00 | Check in. Create the team on the platform's Teams panel. Join the Discord. Claim credits as the links appear | Team exists with all three members |
| 09:00–10:20 | Kickoff, sponsor intros, talk. Ask the questions in `08_kickoff_checklist.md`. Note what each sponsor says a winning entry shows | Answers written down |
| 10:20 | Create the empty repository (public or private as the organizers ask); first commit is the README stating the idea and that the design was rehearsed beforehand | Repository exists; nothing copied in |
| 10:20–13:00 | Smaran: sealed ledger with its tests, then the dashboard shell reading a state file. Atul: tide-gauge and weather clients with a cache; residual; baselines table over 90 days. Nived: problem interface, four windows, block bootstrap; sandbox with tests that really run a child | Ledger tamper tests pass; baseline table printed from real data; an infinite loop in the sandbox is killed |
| 13:00 | Lunch and sponsor office hours: Exa (what must the demo show?), past.dev (which baselines count?), Modal | Notes in `04_sponsor_playbook.md` |
| 13:30–16:00 | Atul: starting formula and the sealing job; **first forecasts sealed by 15:00**. Nived: evaluator, decision rule, research loop with one strong model per proposal. Smaran: coast view on live data | Chain verifies; first lab generation runs end to end |
| 16:00–19:00 | Nived: first full lab run; random-change baseline. Smaran: Exa ideas board in the main loop with sources stored; notebook backend (local, then past.dev). Atul: promote the lab's best forecaster into the sealing job; like-for-like live scoreboard | A lab forecaster beats the starting formula on the confirm window; an Exa call runs every generation; a lab forecast is sealed |
| 19:00 | Dinner. Photo for X with the dashboard on a big screen | Posted |
| 19:30–22:00 | Experiment runner and report. Lab view on the dashboard. Fix what the first runs broke | One command runs arms x seeds and prints the table |
| 22:00 | Launch overnight: the ladder with five seeds per arm (random changes; one model; with notebook; with Exa; with past.dev; with both). Sleep in shifts | Runs confirmed going; live machine plugged in, lid open |

**Checkpoint at 19:00 Saturday.** If any of these is false, apply the cut order in `07_risks_and_cut_order.md`: forecasts are being sealed and graded; the lab beats the starting formula on the confirm window; the dashboard shows sealed against measured.

## Sunday

| Time | What | Done when |
|---|---|---|
| 08:00 | Read the overnight results; regenerate every table with the script | Tables in the repository, dated |
| 08:30 | Write down which forecaster gets the test window (lowest confirm loss across finished runs), then score it once, with every baseline and the starting formula in the same look | Result file written with code hashes; nobody tunes after this |
| 09:00–11:00 | Slides (five) and dashboard polish from the final numbers | Every number on a slide links to a run |
| 11:00–12:00 | Record the 2-minute demo video. Write the description. Check the team list on the platform before it closes at 12:00 | Video uploaded; submission saved |
| 12:00 | Feature freeze: only fixes | |
| 12:00–13:30 | Rehearse with a timer: 1:30 pitch, 1:30 demo, then questions. Three times | Under time twice in a row |
| 13:30 | Read the judging schedule on Discord: room and time | |
| 14:00 | Submit: video, repository link, description | Confirmation seen, 30 minutes early |
| 14:45 | Round 1 in our room | |
| 16:30 | Finals, if called: 3 minutes, no questions | |

## The five minutes in round 1

**Pitch, 1:30.** The problem in one live number; the idea (locked in, then graded); the loop in one picture, in the track's own words: proposes a hypothesis, runs the experiment, reads the result, decides what to try next.
**Demo, 1:30.** Coast chart: a forecast sealed hours ago, the measurement that graded it, the chain verifying. Lab view: a change proposed, tested, kept or thrown out, with its source. Then the test-window table.
**Questions, 2:00.** `06_pitch_and_demo.md` has the answers. Say the limits before they are asked.
