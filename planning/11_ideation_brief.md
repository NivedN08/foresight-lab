# Ideation brief (start here)

Written Nov 13, 2026 at 11:50pm, after the event's Resources tab changed for the second time that evening. Everything decided before that is open again. Full wording of the rules, tracks and challenges is in `00_event_brief.md`; re-read the Resources tab before relying on it, because it has changed twice.

## The event in ten lines

- SF AI Research Hackathon, Sat Nov 14 to Sun Nov 15, Wefunder, 1885 Mission St. Doors 9:00, kickoff 9:30, hacking from 10:00 Saturday to 14:30 Sunday.
- Team: Nived, Smaran, Atul.
- **Everything is built at the event. No prior commits.** The rehearsal repository in this folder is never submitted and no file from it is copied. What carries over is knowledge.
- Submit: one track, the repo link, a 2-minute demo video, a short description.
- Round 1: 3:00 pitch, 2:00 questions. The top 2 of each track reach the finals: 3 minutes, pitch and live demo, no questions.
- Judging, 20% each: research rigour; novelty; results (a measurable result against a baseline, a working live demo); autonomy and self-improvement; interpretability and reproducibility (logs, sources, failures reported honestly).
- Prizes stack: top 3 overall, plus track and challenge prizes.
- Credits exist for Modal, Devin, Tinker, Exa and past.dev. **None redeemed; the user asked to wait.** Links and codes are on the Resources tab only.

## The four tracks

| Track | The question | What is scored |
|---|---|---|
| 1. Autoresearch framework | Can your system beat a best-known result in mathematics on its own? Records: EinsteinArena, Tao's table of optimization constants, the 67 AlphaEvolve problems | How far past the record; how new the loop is; whether each decision can be followed from the log |
| 2. Recursive self-improvement (Pacific) | What will H100 and A100 GPUs resell and rent for? | Forecasts from Nov 6 at 1, 3 and 7 days, scored Sunday by pinball loss against a last-value baseline, from Pacific's data pack and starter kit; plus an argued 2 to 3 year view; rigour, economic insight, reproducibility, originality |
| 3. AI scientist swarms | Can a team of agents reproduce a computational biology study from raw data better than one frontier model? | BixBench3 score against the record (0.523) and a single-agent baseline on the same compute |
| 4. World models | How long can a generated world hold together while an agent plays in it? | PlayWorld score against the record and the starting model |

## Sponsor challenges (optional, any track, all can be entered)

- **Exa:** at least one Exa call in the main loop; point to it in the repo; the demo shows what Exa found and how it was used. Scored 30% on "the project would break, or get clearly worse, without search".
- **past.dev:** more than one agent writes to shared memory, more than one reads, and one uses another's data. No need to prove it helps; explain in the README if it did not. The README needs a "past.dev challenge" section (Agents, Layers, Results, Caveats).
- **Modal:** best use for training, inference, or many experiments and sandboxes in parallel.

## What Friday's rehearsal taught us (assets for any idea)

The rehearsal built a research loop that improved a forecasting formula for coastal water level, with forecasts locked in a hash chain and graded by real gauge readings. Results are in `docs/experiments.md`. What transfers:

- **A loop that works:** one strong model proposes one change with a reason; the change runs in a sandbox; code scores it on later data; it is kept only if it wins on one window and does not lose on a second. On a window it never saw, the best of 35 runs was 19% below our hand-written formula and a typical run 12 to 13%.
- **What did not help:** a three-role swarm (worse than one model, at twice the cost), a notebook of past findings, free scholarly search, and carrying a notebook to a second problem. Random code changes never passed.
- **Noise:** the same setup with the same seed moved by 0.1 to 0.55 cm between runs, as much as the arms differed. Five seeds, compared seed by seed, or no claim.
- **Honesty machinery that judges reward:** time-ordered windows, a test window scored by a written rule, block statistics, a like-for-like live scoreboard, an independent checker that found 20 overstatements in our own write-up.
- **A weakness:** on live forecasts in a storm the lab's forecaster was not better than the simple rule, and worse at three hours.
- **Practical:** a run of 24 proposals took about four minutes and one dollar; the laptop sleeping stopped jobs four times; free scholarly search is fragile.

The coast application itself fits no track's scoring as now published.

## Open questions for ideation

1. Which track? Track 1 needs a math record to fall in 26 hours (survey in `10_track1_math_targets.md`, if finished). Track 2's scoring (short forecasts graded on Sunday, a starter kit, "learns from its previous predictions") is the closest match to the engine we rehearsed. Tracks 3 and 4 are new ground.
2. What makes the entry new, given that most teams will use the same models and the same starter kits?
3. Which sponsor challenges fit without bending the project? The rehearsal says search and memory must do real work to be worth showing.
4. What is the live demo, and what number does it end on?

## Housekeeping

- Rehearsal jobs may still be running on this Mac (live sealing jobs, a dashboard on port 8765). They matter only as a rehearsal. Stop them with `pkill -f lab.tasks` if they are in the way.
- The earlier coast plans (`02`, `04` to `09`) carry a status note at the top; `01` and `03` are history.
