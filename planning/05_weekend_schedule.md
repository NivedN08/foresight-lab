# Weekend schedule (Track 1, built on site)

> **Status, Nov 13 11:30pm.** The organizers published Track 1's task late on Friday: beat a best-known result in mathematics. This document was written for the coast application, which no longer fits Track 1 (or any track's scoring). The loop, the on-site build order, the rules and the lessons still apply; the problem the loop is pointed at, the demo and the pitch do not. Current facts are in `00_event_brief.md`; candidate math targets are in `10_track1_math_targets.md`.

Times are Pacific and follow the organizers' schedule. All code is written at the event in a new, empty repository; `09_rebuild_guide.md` is the build plan. Roles are a suggestion; swap freely.

## The clock

Hacking runs from Saturday 10:20 to Sunday 14:30: 28 hours, minus sleep. Two deadlines inside that shape the order of work:

- **Seal the simple rules by noon Saturday.** A 24-hour forecast sealed at 12:00 Saturday is graded at 12:00 Sunday, in time for the video and round 1. The tide table and "the gap continues" need only the ledger and the gauge client, so they go first. Anything sealed after about 14:00 Saturday has no 24-hour grade before submissions close.
- **Seal a lab forecaster by about 17:00 Saturday.** Its 1 to 12 hour forecasts are then graded by Sunday morning. Its 24-hour forecasts will not be graded in time; say so on the scoreboard.

The estimates in `09_rebuild_guide.md` add up to 15 to 19 hours of work against about 26 person-hours before Saturday dinner. That fits only if nothing is gold-plated: build the smallest version of each block that passes its tests, then move on.

## Who owns what

| Owner | Lane | Builds |
|---|---|---|
| Nived | The lab | Problem windows and statistics, sandbox, evaluator, research loop, experiment runner, the one-time test scorer; the pitch |
| Atul | The sea | Data clients with a cache, cases, baselines, the starting formula, the rewind test, the sealing job and its state file |
| Smaran | The room | Sealed ledger (first hour), help seal the simple rules by noon, the coast view, the Exa ideas board, the notebook, demo video, submission page. Modal only if everything else is done |

One laptop is the live machine: plugged in, lid open, running the sealing job and the dashboard from the moment the job works. Nobody develops on it. Moving the sealing job to a scheduled Modal function is the first thing to cut (`07_risks_and_cut_order.md`); do it only if someone is free on Saturday evening.

## Saturday

| Time | What | Done when |
|---|---|---|
| 08:00 | Check in. Create the team on the platform's Teams panel. Join the Discord. Claim credits as the links appear | Team exists with all three members |
| 09:00–10:20 | Kickoff, sponsor intros, talk. Ask the questions in `08_kickoff_checklist.md`. Note what each sponsor says a winning entry shows | Answers written down |
| 10:20 | Create the empty repository (public or private as the organizers ask); first commit is the README stating the idea and that the design was rehearsed beforehand | Repository exists; nothing copied in |
| 10:20–12:00 | Smaran: sealed ledger with its tests. Atul: tide-gauge client with a cache; residual; the tide table and "the gap continues" as forecasters. Then together: a first sealing job for those two. Nived: problem interface, four windows, block bootstrap | Ledger tamper tests pass; **the two simple rules are sealed for all leads by 12:00** and the chain verifies |
| 12:00–13:00 | Smaran: dashboard shell reading the state file. Atul: weather client (forecasts from earlier runs), 90 days of history. Nived: sandbox with tests that really run a child | An infinite loop in the sandbox is killed; a chart shows sealed marks |
| 13:00 | Lunch and sponsor office hours: Exa (what must the demo show?), past.dev (which baselines count?), Modal | Notes in `04_sponsor_playbook.md` |
| 13:30–16:00 | Atul: full case inputs, the starting formula, the rewind test; starting formula added to the sealing job. Nived: evaluator, decision rule, research loop with one strong model per proposal. Smaran: coast view on live data | Baseline table printed from real data; first lab generation runs end to end |
| 16:00–19:00 | Nived: first full lab run; random-change baseline. Atul: **promote the lab's best forecaster into the sealing job by 17:00**; like-for-like live scoreboard. Smaran: Exa ideas board in the main loop with sources stored (it needs Nived's loop, so not before 16:00) | A lab forecaster beats the starting formula on the confirm window; a lab forecast is sealed; an Exa call runs every generation |
| 19:00 | Dinner. Photo for X with the dashboard on a big screen | Posted |
| 19:30–22:00 | Nived: experiment runner (arms x seeds, resumable) and a plain table of final losses per seed; nothing fancier. Smaran: notebook (local store, everything-in-the-prompt, then past.dev). Atul: lab view on the dashboard (error curve, list of changes with sources). Fix what the first runs broke | One command runs arms x seeds and prints the table |
| 22:00 | Launch overnight, five seeds per arm: random changes; one model; with the notebook (everything in the prompt, local store, past.dev); with Exa; with Exa and past.dev. If the launch slips, start the first three arms and add the rest when ready. Sleep in shifts | Runs confirmed going; live machine plugged in, lid open |

**Checkpoint at 19:00 Saturday.** If any of these is false, apply the cut order in `07_risks_and_cut_order.md`: forecasts are being sealed and graded; the lab beats the starting formula on the confirm window; the dashboard shows sealed against measured.

## Sunday

| Time | What | Done when |
|---|---|---|
| 08:00 | Read the overnight results; regenerate every table with the script | Tables in the repository, dated |
| 08:30 | Write down which forecaster gets the test window (lowest confirm loss across finished runs), then score it once, with every baseline and the starting formula in the same look | Result file written with code hashes; nobody tunes after this |
| 09:00–11:00 | Slides (three) and dashboard polish from the final numbers | Every number on a slide links to a run |
| 11:00–12:00 | Write the description. Rehearse with a timer: 1:30 pitch, 1:30 demo, then questions. Check the team list on the platform before it closes at 12:00 | Under time twice in a row |
| 12:00 | Feature freeze: only fixes | |
| 12:15–13:00 | Record the 2-minute demo video, after the first 24-hour forecasts have been graded at noon | Video uploaded; submission saved |
| 13:00–13:30 | Rehearse once more | |
| 13:30 | Read the judging schedule on Discord: room and time | |
| 14:00 | Submit: video, repository link, description | Confirmation seen, 30 minutes early |
| 14:45 | Round 1 in our room | |
| 16:30 | Finals, if called: 3 minutes, no questions | |

## The five minutes in round 1

**Pitch, 1:30.** The problem in one live number; the idea (locked in, then graded); the loop in one picture, in the track's own words: proposes a hypothesis, runs the experiment, reads the result, decides what to try next.
**Demo, 1:30.** Coast chart: a forecast sealed hours ago, the measurement that graded it, the chain verifying. Lab view: a change proposed, tested, kept or thrown out, with its source. Then one slide with the test-window table and the table of what does the work.
**Questions, 2:00.** `06_pitch_and_demo.md` has the answers. Say the limits before they are asked.
