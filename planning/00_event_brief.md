# Event brief: SF AI Research Hackathon

Compiled Nov 13, 2026 from the event page, its Resources tab (read at 10:30pm), the Luma page, organizer posts and sponsor documentation. Items marked "unconfirmed" are not published anywhere we could find and must be asked at kickoff.

## Basics

- **When:** Sat Nov 14 (check-in 8:00, kickoff 9:00, hacking from 10:20) to Sun Nov 15 (submissions due 14:30, prizes 18:00).
- **Where:** San Francisco, in person only. Every team member must be on site the whole time.
- **Team:** 3–5 people. Ours: Nived, Smaran, Atul.
- **Organizer:** Iterate, with Boardy.
- **Theme:** "push AI to do research on its own: systems that improve themselves, run their own experiments, and work together as swarms of research agents".

## Tracks (pick one)

1. Build Your Own Autoresearch Framework
2. Recursive Self-Improvement, by Pacific. Pacific's CEO wrote publicly: "Our track is on GPU price forecasting and I'll be judging it on the Sunday."
3. AI Scientist Swarms
4. World Models, by Applied Reality (we are not entering this one)

"Benchmarks, datasets and starter resources for each track" are promised. As of Friday night the Resources tab (behind the event login) had the rules, rubric and schedule but no datasets or starter repos.

**Track for this plan: 1.** Track 2's task turned out to be fixed (GPU prices, two to three years out), so the coast project does not fit it. The Track 1 wording describes our loop step for step.

## Sponsor challenges (open to every track)

| Sponsor | Brief, in their words | Prize |
|---|---|---|
| Exa | "a research agent, AI scientist or self-improving system that uses Exa to find what it doesn't already know" | $5k / $3k / $1k credits plus swag |
| past.dev | "build a shared memory layer for a multi-agent setup or swarm using past.dev, and prove better accuracy, latency gains and cost reduction than the common existing approaches" | 3D printer per member plus $1k credits |
| Modal | "best use of Modal" | $5k credits |

## Prizes

| Prize | Award |
|---|---|
| 1st | $3,000 cash, $5,000 Tinker credits, OpenAI prize, a year of Boardy Pro |
| 2nd | $1,500 cash, OpenAI prize, a year of Boardy Pro |
| 3rd | $500 cash, OpenAI prize, a year of Boardy Pro |
| Pacific track 1st / 2nd / 3rd | $3,000 / $1,500 / $500 in Pacific compute hours |
| Exa 1st / 2nd / 3rd | see above |
| past.dev | see above |
| Best use of Modal | see above |
| Best picture on X | $500 cash |

## Credits per hacker

$250 Modal, $200 Devin, $100 Tinker, $50 Exa, plus OpenAI API credits.

## How it is judged (from the event's Resources tab, read Nov 13 at 10:30pm)

100 points, five equal criteria of 20 each:

| Criterion | What we show for it |
|---|---|
| Technicality | Sealed ledger, sandbox, time-ordered windows, block statistics, the research loop |
| Creativity | Forecasts locked in before the answer exists and graded by measurements we do not control |
| Usefulness | The sea is running 30 to 55 cm above the tide tables; the event's test-window table says how much the lab's formula cuts the error (in rehearsal: below every simple rule) |
| Demo | The live chart: sealed versus measured, updating in the room |
| Track / sponsor alignment | Track 1's own wording, step for step; Exa in the main loop; past.dev as the shared notebook; Modal for sandboxes and the sealing job |

**Round 1** (Sunday 14:45, in a room): 5 minutes per team. Pitch 1:30 (the problem, the approach, a high-level scheme), live demo 1:30, questions 2:00.
**Finals** (Sunday 16:30, on stage): the top 2 of each track, then the challenge winners; 8 to 10 teams; 3 minutes each (pitch 1:30, demo 1:30), no questions.
**To submit by Sunday 14:30:** a 2-minute demo video, the GitHub repo link, a short description.

The organizer's earlier writing about research events still describes what the judges like: experimental design, baselines, honest negative results, "a polished interface or persuasive demo is not enough". It is not the rubric.

## Rules that bind us

- **"Build entirely during the event. No prior commits to the repo."** The rehearsal repository cannot be submitted, and no file from it goes into the event repository. We start an empty repository after kickoff and write everything there. What we bring is what we learned: the plans, the test designs, which data sources work, and the mistakes already made (`09_rebuild_guide.md`). Numbers from the rehearsal are not event results and are not presented as such.
- One track per team; sponsor challenges are optional and can all be entered.
- Open-source libraries, APIs and pre-trained models are allowed if credited.
- All team members on site for the whole event. Team of 3 to 5, created on the platform's Teams panel; the team list closes Sunday 12:00.
- Exa challenge: "At least one Exa API call must run in your project's main loop, and your demo must show your sources."

## Tracks in the organizers' words

1. **Build Your Own Autoresearch Framework.** "Build a system that runs the research loop on its own: proposes a hypothesis, runs the experiment, reads the result and decides what to try next." **This is the track this plan is for.**
2. Recursive Self-Improvement by Pacific. "An AI scientist that forecasts H100 and A100 resale and rental prices 2-3 years out, and learns from its own past forecasting errors."
3. AI Scientist Swarms. "A team of agents that forms hypotheses, runs experiments and actively tries to prove its own results wrong."
4. World Models by Applied Reality. Brief not posted.

## Schedule (all times Pacific; venue: 1885 Mission St, San Francisco)

| When | What |
|---|---|
| Sat 08:00 | Check-in, breakfast, team formation |
| Sat 09:00 | Kickoff and sponsor challenge intros |
| Sat 10:00 | Talk (personal agents, memory, continual learning) |
| Sat 10:20 | Hacking begins |
| Sat 13:00 | Lunch and sponsor office hours |
| Sat 19:00 | Dinner, then overnight hacking |
| Sun 08:00 | Breakfast |
| Sun 12:00 | Team list closes on the platform |
| Sun 13:30 | Judging schedule posted on Discord (room and time) |
| Sun 14:30 | Hacking ends, submissions due |
| Sun 14:45 | Round 1 |
| Sun 16:30 | Finals |
| Sun 18:00 | Prizes and closing |

News is posted on the event's Discord (link on the Resources tab, with the venue Wi-Fi details). Credit claim links for Modal, Devin, Tinker, Exa and OpenAI were still placeholders on Friday night; past.dev API access was "to be confirmed".

## Judges worth designing for

- Pacific's CEO: judges Track 2 himself.
- past.dev's founding go-to-market lead: will likely judge the memory challenge.
- A founder whose product lets agents search 2M papers to speed up research: our literature-testing ledger speaks to him.
- A researcher who co-authored work on autonomous agents for scientific discovery.
- Researchers from OpenAI, Meta Superintelligence Labs and Scale AI: expect questions on held-out data, baselines and leakage.
- Several investors: expect "who uses this and what is it worth".

## Sponsor tool facts that shape the build

- **past.dev.** Plain HTTP: `POST /ingest` (id, content, timestamp, audience), poll until completed, `POST /recall` (query, identity, level). Facts can be superseded; recall can be asked "as of" a past time (`queryTimestamp`); audiences limit who can read what. Derived memories appear within minutes. Their own benchmark method requires full-context and plain-RAG baselines, with accuracy, cost and latency reported separately. In one of their own published runs past.dev was more accurate but slower and costlier than a rival, so we measure latency honestly.
- **Exa.** Search types auto / lite / deep; published-date filters; include and exclude domains; highlights. `/findSimilar` is deprecated. Exa's own showcase reports search efficiency (fewer searches and tokens per correct answer).
- **Modal.** Sandboxes and GPU functions. Starter plan allows 100 containers and 10 GPUs at once. Past winners ran real GPU work, large parallel fan-out, or long-lived sandboxed agents.
- **Tinker.** LoRA fine-tuning only. gpt-oss-20b / 120b and Qwen 3.5 models. A fine-tuned model can be sampled through an OpenAI-compatible endpoint.

## Still to ask at kickoff

1. Can one team win a main prize and a sponsor challenge?
2. Can Modal credits be pooled into one workspace?
3. Where are the credit claim links and the past.dev API access?
4. Is it fine that we rehearsed the design on Friday, given that all code is written at the event? (We will say so in the submission either way.)
