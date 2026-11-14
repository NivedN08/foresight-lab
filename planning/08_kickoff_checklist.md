# Kickoff checklist (Saturday morning)

Check-in is at 08:00 at 1885 Mission St. Kickoff is at 09:00. Hacking starts at 10:20.

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
| How is it scored? | 100 points: Technicality, Creativity, Usefulness, Demo, Track / sponsor alignment, 20 each |
| Pitch format? | Round 1: pitch 1:30, live demo 1:30, questions 2:00. Finals: 3 minutes, no questions |
| What must be submitted, and when? | A 2-minute demo video, the repository link, a short description, by Sunday 14:30 |
| What is Track 2's task? | Forecast H100 and A100 resale and rental prices two to three years out. Not this plan's track |
| Exa's condition? | At least one Exa call in the main loop; the demo must show sources |

## First things at the venue

1. Create the team on the platform's Teams panel and have everyone join it (the list closes Sunday 12:00).
2. Join the Discord from the Resources tab; all news goes there.
3. Claim credits as soon as the links are live: Modal, Devin, Tinker, Exa, OpenAI. Ask about past.dev API access.
4. Take the photo for the "best picture on X" prize early, while the room is full.

## Still to ask the organizers

| Question | Answer | What changes |
|---|---|---|
| Can one team win a main prize and a sponsor challenge? | | If no: decide which to aim at |
| Can Modal credits be pooled into one workspace? | | Sets how many parallel runs we can afford |
| We rehearsed the design on Friday; all code is written here. Anything we should do to make that clear? | | We state it in the README and pitch either way |
| Must the repository be public? | | Visibility of the new repository |

## Questions to ask each sponsor (lunch, 13:00)

- **Exa:** what have you not seen before that you would like to see? Is a comparison against free scholarly search useful to you?
- **past.dev:** which baselines do you count as the "common existing approaches"? Is as-of-date recall fair to use as the core of the entry?
- **Modal:** is large parallel fan-out of sandboxed runs enough, or do you want GPU work?

## At 10:20

1. One person creates the empty repository and pushes a README: the idea in three sentences, and that the design was rehearsed beforehand while all code is written at the event.
2. Everyone opens `09_rebuild_guide.md` and starts their lane.
