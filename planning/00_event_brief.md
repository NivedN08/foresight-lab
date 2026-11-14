# Event brief: SF AI Research Hackathon

Compiled Nov 13, 2026 from the event page, its Resources tab (read at 10:30pm and again at 11:15pm, when it had changed), the Exa and past.dev challenge briefs, the Luma page, organizer posts and sponsor documentation. Items marked "unconfirmed" are not published anywhere we could find and must be asked at kickoff.

## Basics

- **When:** Sat Nov 14 (doors 9:00, kickoff 9:30, hacking from 10:00) to Sun Nov 15 (submissions due 14:30, prizes 18:00).
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

**Track for this plan: 1**, whose task was published late on Friday night and is about records in mathematics. See "The tracks, as now published" below.

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

## How it is judged (Resources tab as of Nov 13, 11:15pm; it changed during the evening)

Five criteria, 20% each:

| Criterion | In the organizers' words |
|---|---|
| Research rigour | clear question, sound experiment design, baselines or ablations, claims backed by results |
| Novelty | a new idea, method or use case, beyond existing papers and open-source baselines |
| Results | a measurable result on a real problem or benchmark, ideally against a baseline; a working live demo |
| Autonomy & self-improvement | how much of the research loop runs without a human; extra credit if it gets better from its own results |
| Interpretability & reproducibility | decisions can be followed (logs, traces, sources), code runs, failures reported honestly |

(An earlier version of the page, at 10:30pm, listed Technicality, Creativity, Usefulness, Demo and Track / sponsor alignment. The list above replaced it.)

**Round 1** (Sunday 14:45, six rooms): 5 minutes per team: pitch 3:00, questions 2:00. The top 2 of each track go to the finals.
**Finals** (Sunday 16:30, on stage): 3 minutes per team, pitch and live demo, no questions. Then the challenge winners demo for 2 minutes each. The final panel picks the top 3 overall.
**To submit by Sunday 14:30:** one track plus any challenges entered; the GitHub repo link; a 2-minute demo video (YouTube, Drive or Loom); a short description.
**Prizes stack:** track and challenge prizes come on top of the top 3 overall.

## Rules that bind us

- **"Build entirely during the event. No prior commits to the repo."** The rehearsal repository cannot be submitted, and no file from it goes into the event repository. We start an empty repository after kickoff and write everything there. What we bring is what we learned: the plans, the test designs, and the mistakes already made. Numbers from the rehearsal are not event results and are not presented as such.
- One track per team; sponsor challenges are optional and can all be entered.
- Open-source libraries, APIs and pre-trained models are allowed if credited.
- All team members on site. Team of 3 to 5, created on the platform's Teams panel; the team list closes Sunday 12:00.

## The tracks, as now published

**Track 1: Build Your Own Autoresearch Framework.** "The question: can your system beat a best-known result in mathematics on its own?" Build "a system that runs the research loop by itself: it picks what to try, runs it, reads the result and decides what to try next, until it beats a record." The reference is Karpathy's autoresearch (an agent edits one training script, runs 5-minute experiments, keeps only changes that lower the validation loss); "take it further, for example with better search over ideas, memory of what failed, multi-objective evaluation, or a new domain." Records to beat: any problem on EinsteinArena (public verifier and live leaderboard), any constant in Terence Tao's table of optimization constants, or any of the 67 problems in the AlphaEvolve repository of problems; "improving a known bound is welcome." Judged on: "how far past the record you get, how new your loop is, and whether we can follow each decision (show the log)."

**Track 2: Recursive Self-Improvement by Pacific: An AI Scientist for GPU Value.** What will H100 and A100 GPUs resell for, and what will cloud rentals cost, in 2 to 3 years? An AI scientist that forms hypotheses about what drives GPU prices, tests them on real data, and improves its own forecasts with each iteration. Three directions: self-improving forecasts; market signals; your own. Pacific supplies a data pack (3.7M+ posted rental offers, price indices, hardware data; cutoff Nov 6) and a starter kit that builds price series, runs baselines and scores forecasts. Scored: forecasts from Nov 6 at 1, 3 and 7 days, against posted rental prices collected after the cutoff, by pinball loss at quantiles 0.1, 0.5 and 0.9, as skill against a last-value baseline. The 2 to 3 year view is argued from public evidence. Submit forecasts in the starter-kit format, one command that reproduces them, and concise findings including what the system learned across iterations. Judged on rigour, useful economic insight, reproducibility and originality.

**Track 3: AI Scientist Swarms.** Can a team of agents reproduce a full computational biology study from raw data better than the best single frontier model? Record to beat: BixBench3, best score 0.523. Judged on score against the record and a single-agent baseline on the same compute budget.

**Track 4: World Models by Applied Reality.** Make an open world model hold together longer; record to beat on PlayWorld.

**What this means for the coast project.** It no longer fits Track 1 as published: Track 1 is now about beating a record in mathematics, and its first judging line is how far past the record the system gets. The coast forecaster fits none of the four tracks' scoring. The loop itself (propose a change, run it in a sandbox, score it with code, keep what wins, log every decision) is what Track 1 asks for; the problem it is pointed at has to change. Candidate math targets are surveyed in `10_track1_math_targets.md`.

## Sponsor challenges, as now published

**Exa.** Requirements: at least one Exa API call (search, contents, find similar, answer, research or Websets) runs in the project's main loop, not only in a side feature; submit a repo link and point to where Exa is called; the demo makes clear what the system found through Exa and how it used it. Judged: Exa is central, "the project would break, or get clearly worse, without search" (30%); research value (25%); creativity (25%); checkable output, "results trace back to real sources, and it works in a live demo" (20%). Their idea for autoresearch agents: an agent that decides what to search next from what it has already found, or that searches prior work to see whether a hypothesis was already tested before spending compute on it.

**past.dev.** A shared memory layer added to the main project. Requirements: more than one agent writes to it; more than one agent reads from it; at least one agent reads data written by a different agent and uses it. "You don't have to prove the memory improves your results: if it didn't, explain why in your README." No metrics, no leaderboard. The README needs a section titled "past.dev challenge" with four headings: Agents (what each writes and reads, its identity and audiences), Layers (which memory technique holds what, and why), Results, Caveats. In round 1, show one read in which an agent uses another agent's data. Judged 1 to 5 on: a real swarm (read boundaries must hold when the judge tests them; "one agent with memory calls is not a swarm"), a memory design with reasons (including the result you expected before the run), evidence (a trace, a boundary that holds, a failure with its explanation), and honesty (caveats in the README, not found by the judge). Judge: past.dev's CEO. Mentor on site. Limits: 1 project, 500 requests a minute, 8,000 tokens per recall; ingestion can be slow, so keep the data to tens of documents. API: write a fact with content, timestamp, identity and audience (202 with an ingestion id; readable when ingestion completes; batch up to 1,000); read with query and identity (optionally "as of" a time); results carry `occurredAt`, `supersededAt` and source excerpts.

**Modal.** Best use for training, inference, or running large numbers of experiments and agent sandboxes in parallel.

## Credits

Modal $250 and Devin $200 per hacker by claim links; Tinker $100 by a personal code; Exa $50 by a sign-up code; past.dev 500,000 credits per team by a code; OpenAI credits to be confirmed. The links and codes are on the Resources tab, not in this repository. Nothing has been redeemed.

## Schedule (all times Pacific; venue: Wefunder, 1885 Mission St, San Francisco)

| When | What |
|---|---|
| Sat 09:00 | Doors open, check-in, breakfast |
| Sat 09:30 | Kickoff and sponsor challenge intros |
| Sat 10:00 | Hacking begins |
| Sat 12:30 | Lunch and sponsor office hours |
| Sat 19:00 | Dinner, then overnight hacking |
| Sun 08:00 | Breakfast |
| Sun 12:00 | Lunch; team list closes on the platform |
| Sun 13:30 | Judging schedule posted on Discord (room and time) |
| Sun 14:30 | Hacking ends, submissions due |
| Sun 14:45 | Round 1 |
| Sun 16:30 | Finals |
| Sun 18:00 | Prizes and closing |

(The past.dev brief gives a start of 08:30 Saturday and awards at 17:30 Sunday; the Resources tab times above are the organizers'.) News is posted on the event's Discord. The opening slides are linked from the Resources tab but were not open to our account on Friday night.

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

1. Track 1: does "a new domain" in the brief mean a record outside mathematics can count, or must the record be one of the three listed sources?
2. Track 1: what counts as beating a record on Tao's table or the AlphaEvolve problems: our own verifier output, or an accepted submission?
3. Can Modal credits be pooled into one workspace?
4. Is it fine that we rehearsed a design on Friday, given that all code is written at the event? (We will say so in the submission either way.)
