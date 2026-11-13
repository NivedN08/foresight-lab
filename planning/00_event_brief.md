# Event brief: SF AI Research Hackathon

Compiled Nov 13, 2026 from the event page, the Luma page, organizer posts and sponsor documentation. Items marked "unconfirmed" are not published anywhere we could find and must be asked at kickoff.

## Basics

- **When:** Sat Nov 14 (doors 8:30, kickoff 9:00) to Sun Nov 15. The event site says Sunday ends 6pm; Luma says 7pm.
- **Where:** San Francisco, in person only. Every team member must be on site the whole time.
- **Team:** 3–5 people. Ours: Nived, Smaran, Atul.
- **Organizer:** Iterate, with Boardy.
- **Theme:** "push AI to do research on its own: systems that improve themselves, run their own experiments, and work together as swarms of research agents".

## Tracks (pick one)

1. Build Your Own Autoresearch Framework
2. Recursive Self-Improvement, by Pacific. Pacific's CEO wrote publicly: "Our track is on GPU price forecasting and I'll be judging it on the Sunday."
3. AI Scientist Swarms
4. World Models, by Applied Reality (we are not entering this one)

"Benchmarks, datasets and starter resources for each track" are promised. They are behind the event login and were not posted as of Friday afternoon.

**Our track: 2.** It has its own prize pool, and its task (forecasting) is the same shape as our project.

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

## How it is judged

No rubric is published for this event. What the organizer has written elsewhere:

- Research events should be judged on "experimental design, benchmark performance, reproducibility, baseline comparisons, and the quality of the conclusions".
- "A polished interface or persuasive demo is not enough." Teams must say what they tested, how it was evaluated, and whether the evidence supports the hypothesis.
- Negative results count. At the previous research event one team was praised for finding a data leak planted in the benchmark and refusing to use it.
- Suggested demo structure: problem, approach, demo, evidence, limitations, next step. A backup recording is fine but should not replace proof that the system works.
- Format seen at the last event: a first round, then finalists pitch on stage.
- Past winners shared three things: a rigorous benchmark (one with hidden ground truth), honest baselines including negative results, and verified results.

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

## Ask at kickoff

1. Is code written before the event allowed? If not, we rebuild on site from `09_rebuild_guide.md`.
2. Can one team win more than one prize? Are Track 2 teams eligible for the main prizes?
3. Can Modal credits be pooled into one workspace?
4. What exactly is Pacific's task, data and scoring?
5. Pitch length and judging schedule.
