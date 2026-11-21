# Five SF AI Research Hackathon projects: what they did and why they stood out

Five public repos from the SF AI Research Hackathon (Nov 14-15, 2026), read closely after the event. Holdfast
won first place overall. I do not know how the other four placed.

**The short answer: Holdfast won on rigour and presentation, not on a large margin.** It took an open video
world model and, without changing its weights, beat the closed leader's published score on PlayWorld. Its
honest held-out gain is small, and its confidence interval touches zero. It still won because:

- it had one clear claim against a famous number;
- it had one frame that explained everything ("programming the hidden state");
- it tested on a held-out half that it tuned nothing on;
- it measured the judge's noise first;
- it included an arm showing that most of the gain was a plain prompt fix;
- it logged about twenty failures with their numbers;
- its README walks a judge through the project in five minutes.

The other four projects share most of these habits. Each also has a headline number that is weaker than it
first looks.

## The documents

| File | Project | Track |
|---|---|---|
| [holdfast.md](holdfast.md) | Holdfast: editing a world model's hidden state at run time. **First place overall** | 4, World Models |
| [crux.md](crux.md) | Crux: agents settle disagreements by running the most informative experiment | 3, AI Scientist Swarms |
| [revolutionaries-track1.md](revolutionaries-track1.md) | The Revolutionaries: search loops plus a strict verification layer for math records | 1, Autoresearch |
| [bit-crew.md](bit-crew.md) | Bit Crew (team GLHF): a research app whose agents' statements are checked against sources | 3, AI Scientist Swarms |
| [caffeinated.md](caffeinated.md) | caffeinated: a swarm with a "boredom meter" that forces new ideas | 3 (also Ig Nobel task) |

Each document has the same sections, in this order:

- summary
- the benchmark
- the mechanism, with file paths
- how they beat the benchmark, with a check of the evidence
- experiments and failures
- how they used Exa, past.dev and Modal
- autonomy
- presentation
- a score against the five judging criteria
- build process
- lessons
- open questions

**How they were made.** Each repo was cloned and read as files only. None of their code was run, and no
website was visited. Every number was checked against the repo's own result files, and the key claims were
spot-checked a second time. The clones are in a temporary folder and may be deleted. They were read at these
commits:

| Repo | Commit | Last commit time |
|---|---|---|
| github.com/Hcoder10/holdfast | `427801d` | Nov 15, 15:35 |
| github.com/mian-abd/Crux | `708f95b` | Nov 15, 11:31 |
| github.com/ArthurAnache/Hackathon-AI-Reasearch | `0b1cc20` | Nov 15, 13:49 |
| github.com/itsbrendandang/agent-test | `2e6468a` | Nov 15, 14:56 |
| github.com/arii/caffeinated | `0b880e2` | Nov 15, 14:06 |

All times are Pacific. Several repos have commits after the 14:30 deadline, so what judges saw at submission may
differ slightly.

## The five at a glance

| | Headline claim | What the evidence shows | Core idea |
|---|---|---|---|
| **Holdfast** | 2.31 on PlayWorld against 2.12 published for Genie 3; untouched model 2.10 | Numbers match the files. Held-out gain +0.21 (interval -0.04 to +0.47). Most of it (+0.14) is putting the user's request into the prompt. The Genie 3 number comes from a different judging setup: their judge scores the untouched model 0.28 above the paper | Between video chunks, read and write the model's attention and latent state; each edit fixes one visible failure |
| **Crux** | Swarm 0.817 against single agent 0.671 on BixBench3 task 9, same $2 budget | Recomputed and correct. But the whole gap comes from data faults they planted themselves, with checks they wrote for those faults; on clean data the arms tie (0.864 against 0.868). Their main idea, choosing the most informative experiment, did no better than random | Turn each analysis choice into competing options that predict check outcomes in advance; run the best-value check; update beliefs by Bayes |
| **The Revolutionaries** | Tao constant 1d: 1.4540711, 6.0e-4 below the best open claim. EinsteinArena third-autocorrelation: 5.69 δ below the live #1 | Two exact verifiers agree on the stored solutions. Neither was submitted. The 1d loop started from 239 of the team's own solutions, whose best already beat the claim. Their main build, a genetic loop, beat nothing, and a plain hill-climbing control did better | Frozen copies of the official verifiers, a second exact verifier per problem, one arbiter script for every verdict, a gated submit |
| **Bit Crew** | A 7-agent team at 0.537 on BixBench3, above the 0.523 record; one agent at 0.240 | The run was on BixBench v1, not BixBench3. The team's run records are not in the repo. The team ran uncapped on Claude models with R installed; the baseline was gpt-5.1 without R and lost 40 answers to missing R. A like-for-like test (71 against 64) was a statistical tie and is not in the README | Every agent statement is checked against its cited source by a model from another company; code, not a model, decides the mark |
| **caffeinated** | A boredom meter that moves the swarm from refining to new ideas | Honest non-result: the two orderings they report disagree (swarm 0.26 against single 0.24; or single 0.30 against swarm 0.06), with 3 runs each and nothing significant. All 11 kept improvements came from the normal mode; all 69 "bored mode" proposals failed | A deterministic score from the experiment ledger: B = 0.45 × (1 − novelty) + 0.45 × stagnation + 0.10 × overload |

## Why Holdfast won

Holdfast is one person driving one long Claude Code session: 140 of its 143 commits are co-authored by Claude
Fable 5.2. By score margin it is not the strongest project here. What set it apart:

1. **One claim a judge can repeat.** "An open model, no training, beats the closed leader." The rubric's Results
   line asks for exactly that: a measurable result on a real benchmark, against a baseline.
2. **One frame for everything.** It calls the whole project "hidden-state programming", one level up from editing
   weights. Three plain failures ("It does not listen", "It drifts", "It forgets") each get one fix, one
   before-and-after GIF and one number.
3. **A held-out protocol that holds up.** It split the benchmark by seed before any result and tuned on one half.
   It froze the settings in a commit at 03:17 and ran the other half once, blind, judged twice. The two halves
   agree (+0.19 and +0.21).
4. **It measured the judge first.** The same video scored 5, 3 and 1 on repeat judging. That finding shaped the
   whole protocol, and the README says so.
5. **It found its own cheap explanation before a judge could.** The prompt-only arm showed that most of the gain
   was a prompt fix. Reporting it made the rest more believable.
6. **Failures as evidence.** About twenty judged ideas that did not work are in a table with numbers. A post hoc
   gain that failed to replicate on the rest of the benchmark is reported as failing.
7. **A README built for a judge with five minutes.** It opens with "Hey judges!" and a "5 minute plan". It gives
   three reasons to pick it, then a short path (a video, three pictures, one table). Everything else is in a
   second document. An FAQ asks "Is 'beats Genie 3' a fair claim?" before anyone else can.
8. **A live demo.** An upload-a-photo page on Modal shows the untouched model and Holdfast side by side.

Its weak spots are real, and the write-up names them:

- the Genie 3 comparison crosses judging setups;
- the held-out interval crosses zero;
- seven "held-out" cases had been generated in Saturday's pilots;
- Exa and past.dev did not raise the score;
- the one human commit removed some caveats for the pitch.

The lesson is that judges rewarded a clean, honest, well-told experiment with a modest effect over bigger claims
on weaker evidence.

## What the strong projects had in common

- **A pre-registered test set.** Holdfast froze its settings before the held-out run. Crux committed each batch
  plan before it ran. caffeinated kept a table of every deviation from its plan. The Revolutionaries hash-froze
  their verifiers. This was the main source of credibility, and it costs only discipline.
- **Their own negative results, stated prominently.** Holdfast: most of the gain is a prompt fix. Crux: its
  selection rule matched random. The Revolutionaries: the control arm beat the genetic loop. caffeinated: no
  significant result. None of these hurt the projects. Each made the rest more believable.
- **A cheap control arm.** A prompt-only arm, a random-selection arm, a hill-climbing arm at equal spend. The
  strongest single habit across all five.
- **Code, not models, makes the final call.** The Revolutionaries' arbiter, Crux's outcome bins, Bit Crew's quote
  check and caffeinated's deterministic meter are all decided by code.
- **Every number traceable to a file.** Logs, result JSON, replay scripts. caffeinated ships one command that
  recomputes every headline number, and Crux's replay recomputes every belief update.
- **A judge-facing document.** A five-minute tour, a map from each criterion to its evidence, a "not measured"
  list, a "what this is not" box beside each record, a hard-questions Q&A.
- **Something to watch.** GIFs and a live app (Holdfast), a live "Agent Lab" and 3D claim graphs (Bit Crew), a
  first-person 3D replay of the agents' run (caffeinated), a live site (Crux).

## Where the claims were weaker than they looked

Every headline here needs a qualifier. The weak points fall into four kinds:

- **A comparison across setups.** Holdfast against Genie 3 used a different judging setup. Bit Crew's team and
  baseline differed in model, tools and budget.
- **A benchmark that is not the one named.** Bit Crew ran BixBench v1 but called it BixBench3. Crux built its own
  planted-fault suite on one BixBench3 task.
- **A record that was never submitted.** Both of the Revolutionaries' records were checked by the team's own
  verifiers only.
- **Caveats dropped near the deadline.** It happened in Holdfast's slides, and Bit Crew's README lost its
  strongest caveats in the last hour.

Sponsor tools rarely moved a score. In all five projects, Exa and past.dev were well built and logged, but none
showed them raising the headline score. Holdfast and caffeinated did measure memory on small benchmarks of their
own.
Modal was the one tool that mattered everywhere: GPU fan-out, sandboxes with no network, parallel repeats.

## How summit-lab compares

summit-lab (our Track 3 entry) ran a lab with these parts:

- two blind teams with separated past.dev memory;
- a director who challenges agreed answers;
- a referee who must run a test, not vote;
- official graders, scored only after every agent had exited.

Results:

| Benchmark | Lab | Best single model | Cost of the lab |
|---|---|---|---|
| GeneBench-Pro, 8 test problems | 3 of 8 | Fable alone 4 of 8 | about 3.4 times Fable alone |
| scBench, 33 test tasks | 23 of 33 | Opus alone 24 of 33; Fable alone 23 of 33 | about 11 times |
| BioDSA-1K, 36 test tasks | 27 of 36 | Fable alone 26 of 36 | about 7 times |

**What we already do as well as the strong projects:**

- the dev/test split with the design frozen first;
- official graders and sealed answers;
- every number tied to a measurement entry in `explanation.md`;
- negative results stated in the README headline;
- read boundaries that were tested.

Our past.dev design (blind teams whose memory the other team cannot read) is arguably the most substantive use
of read boundaries among these projects. Crux's "settle by experiment, not by vote" is the same core idea as our
referee.

**What we lack, compared with Holdfast:**

1. **One claim against a record.** We never ran BixBench3, the track's own record, and our best result is "one
   task ahead of Fable alone". The track asked for a score against 0.523.
2. **One frame and one picture.** We have a careful log but no single image of the lab rescuing an answer. The
   bd36 case (O-11) is that picture: both teams and every single attempt said "Not verifiable", the director
   pointed at two unused columns, and both teams changed to the right answer.
3. **A cheap control arm.** We did not test the obvious cheaper explanation, for example one Fable agent that
   gets the director's challenge questions on its own answer. If that matches the lab, most of the lab is not
   needed. If it does not, our story is stronger.
4. **A benchmark variant where our design should win.** Crux made its gap by planting data faults. Our lab's
   claimed strength is catching shared mistakes. A small, stated, planted-fault suite would test that directly.
5. **Something to watch,** plus a five-minute judge path at the top of the README.

## Lessons for next time, ranked

1. **Freeze the design and split the data before any result, then say so in the first screen of the README.**
2. **Measure the judge or grader's noise before tuning anything.**
3. **Run the cheapest explanation of your gain as its own arm,** and report it even if it explains most of it.
4. **Make one claim against the track's own record,** compared like for like. If the leader cannot be rerun,
   lead with your paired gain and show the leader's number as context.
5. **Log every failure with its number,** and re-test any post hoc win on fresh data.
6. **Let code decide verdicts** (verifiers, outcome bins, quote checks), and keep models for proposing and
   judging validity.
7. **Give the work one name, one frame and one picture per idea.** Put a five-minute tour for judges at the top
   of the README and the detail in a second document.
8. **Ship a command that recomputes every published number** from the committed raw results, and commit those
   results.
9. **Write the caveat for each headline number once, before the pitch,** and use the same sentence everywhere.
   Do not remove caveats in the last hour.
10. **Use Modal for parallel repeats and cheap controls.** Holdfast ran 19 GPUs at once. Bit Crew ran 60 runs in 41
    minutes for $1.61.
11. **Put sponsor tools where a judge can trace them, and say plainly when they did not move the score.**
12. **Push every branch and run folder the README cites.** The Revolutionaries and Bit Crew both lost evidence this
    way.
