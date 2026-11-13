# Kickoff checklist (Saturday morning)

## Before leaving home

- [ ] Laptop chargers; one laptop stays plugged in and open all weekend (it runs the live forecast jobs; a closed lid stops them).
- [ ] The repo pulled on every laptop: `git clone https://github.com/NivedN08/foresight-lab` (private; add Smaran and Atul as collaborators first).
- [ ] Environment built on every laptop: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"`, then `.venv/bin/python -m pytest`.
- [ ] Accounts created and keys saved in a local `.env` (never committed): Modal, Exa, past.dev, Tinker, OpenAI.
- [ ] Phone hotspot ready in case venue Wi-Fi blocks something.

## Questions to ask the organizers in the first 30 minutes

Write the answers here; several decisions depend on them.

| Question | Answer | What changes |
|---|---|---|
| Is code written before the event allowed? | | If no: rebuild from `09_rebuild_guide.md`; submit only code written on site |
| Can one team win more than one prize? | | If no: decide which prize to aim at |
| Are Track 2 teams eligible for the main prizes? | | If no: weigh Track 2 against Track 3 |
| What is the Track 2 task exactly (data, horizon, scoring, deadline)? | | Add it as the third problem in the lab |
| Can Modal credits be pooled into one workspace? | | Sets how many parallel runs we can afford |
| How long is the pitch, and when is the first judging round? | | Fixes the feature freeze time |
| What must be submitted (repo, video, slides, description)? | | Assign an owner |

## Questions to ask each sponsor

- **Pacific:** what would a winning Track 2 entry show you? Is forecast accuracy on your data the score, or the self-improvement evidence, or both?
- **Exa:** what have you not seen before that you would like to see? Do you want a comparison against another search setup?
- **past.dev:** which baselines do you consider the "common existing approaches"? Is as-of-date recall (`queryTimestamp`) fair to use as the core of the entry?
- **Modal:** is large parallel fan-out of sandboxed runs enough, or do you want GPU work?
- **Tinker:** which base model do you recommend for a small proposer fine-tune that must finish in a few hours?

## First hour, in order

1. Confirm the rule on pre-written code.
2. Put the keys in `.env`; run the live checks for each sponsor adapter.
3. Check the live forecast jobs are still running and the seal chain verifies.
4. Get the Track 2 data; one person starts the adapter for it.
5. Take the photo for the "best picture on X" prize early, while the room is full.
