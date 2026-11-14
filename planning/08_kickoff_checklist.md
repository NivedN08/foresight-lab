# Kickoff checklist (Saturday morning)

> **Status, Nov 13 11:30pm.** The organizers published Track 1's task late on Friday: beat a best-known result in mathematics. This document was written for the coast application, which no longer fits Track 1 (or any track's scoring). The loop, the on-site build order, the rules and the lessons still apply; the problem the loop is pointed at, the demo and the pitch do not. Current facts are in `00_event_brief.md`; candidate math targets are in `10_track1_math_targets.md`.

Doors open at 09:00 at Wefunder, 1885 Mission St. Kickoff is at 09:30. Hacking starts at 10:00.

## Before leaving home

- [ ] Laptop chargers; one laptop will stay plugged in with the lid open from Saturday afternoon (it runs the sealing job).
- [ ] Coding tools signed in and working on every laptop; Python 3.12 and `uv` installed.
- [ ] Accounts created for GitHub, Modal, Exa, past.dev, OpenAI and Tinker. Keys go in a local `.env` at the event, never in a commit.
- [ ] This planning folder readable on every laptop (the plans, not the rehearsal code).
- [ ] Phone hotspot ready in case the venue Wi-Fi blocks something.

## Already answered by the Resources tab

| Question | Answer |
|---|---|
| Is code written before the event allowed? | No. "Build entirely during the event. No prior commits to the repo." We build from `09_rebuild_guide.md` in a new repository |
| How is it scored? | Five criteria, 20% each: research rigour; novelty; results; autonomy and self-improvement; interpretability and reproducibility |
| Pitch format? | Round 1: pitch 3:00, questions 2:00. Finals: 3 minutes, pitch and live demo, no questions |
| What must be submitted, and when? | A 2-minute demo video, the repository link, a short description, by Sunday 14:30 |
| What is Track 1's task? | Beat a best-known result in mathematics with a loop that runs on its own (EinsteinArena, Tao's table of constants, or the AlphaEvolve problems) |
| What is Track 2's task? | GPU rental and resale prices; forecasts at 1, 3 and 7 days are scored on Sunday. Not this plan's track |
| Do prizes stack? | Yes: track and challenge prizes come on top of the top 3 overall |
| Exa's condition? | At least one Exa call in the main loop; point to it in the repo; the demo shows what Exa found and how it was used |
| past.dev's condition? | More than one agent writes, more than one reads, and one uses another's data; a README section titled "past.dev challenge" |

## First things at the venue

1. Create the team on the platform's Teams panel and have everyone join it (the list closes Sunday 12:00).
2. Join the Discord from the Resources tab; all news goes there.
3. Claim credits before hacking starts (the links and codes are on the Resources tab; nothing was redeemed on Friday): Modal, Devin, Tinker, Exa, and past.dev's 500,000 credits per team.
4. Take the photo for the "best picture on X" prize early, while the room is full.

## Still to ask the organizers

| Question | Answer | What changes |
|---|---|---|
| Can Modal credits be pooled into one workspace? | | Sets how many parallel runs we can afford |
| We rehearsed the design on Friday; all code is written here. Anything we should do to make that clear? | | We state it in the README and pitch either way |
| Must the repository be public? | | Visibility of the new repository |

## Questions to ask each sponsor (lunch, 13:00)

- **Exa:** what have you not seen before that you would like to see? Is a comparison against free scholarly search useful to you?
- **past.dev:** which baselines do you count as the "common existing approaches"? Is as-of-date recall fair to use as the core of the entry?
- **Modal:** is large parallel fan-out of sandboxed runs enough, or do you want GPU work?

## At 10:00

1. One person creates the empty repository and pushes a README: the idea in three sentences, and that the design was rehearsed beforehand while all code is written at the event.
2. Everyone opens `09_rebuild_guide.md` and starts their lane.
