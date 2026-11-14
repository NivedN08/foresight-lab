# Weekend schedule

Times are Pacific. Sunday times assume judging starts mid-afternoon; shift them once the real schedule is announced. Roles are a starting suggestion; swap freely.

## Who owns what

| Owner | Area | Prize it serves |
|---|---|---|
| Nived | The lab runs and the experiments for claims 1 and 2; Pacific's GPU-price task; the pitch | Main, Pacific |
| Smaran | Sponsor integrations: Exa search, past.dev memory, Modal fan-out; experiments for claims 3 and 4 | Exa, past.dev, Modal |
| Atul | Live forecast jobs, data sources, dashboard, backup video, submission page, photo for X | Main, all demos |

One laptop is the "live machine": plugged in, lid open, running the sealing jobs and the dashboard. Nobody develops on it.

## Saturday

| Time | What | Done when |
|---|---|---|
| 8:30 | Arrive, power, Wi-Fi, live machine up | Dashboard shows forecasts sealed overnight and the chain verified |
| 9:00–9:45 | Kickoff. Ask the questions in `08_kickoff_checklist.md` | Rule on pre-written code is known; Pacific's task and data in hand |
| 9:45–10:30 | Keys into `.env`; live checks for Exa, past.dev, Modal; decide: use the rehearsal repo or rebuild from `09_rebuild_guide.md` | Each adapter has made one real call |
| 10:30–12:30 | Nived: adapter for Pacific's data, baselines, first rewind test. Smaran: evaluations running on Modal. Atul: live jobs moved to a scheduled Modal function | Pacific baseline table printed; 100 sandboxed evaluations run on Modal; sealing no longer depends on a laptop |
| 12:30–13:00 | Lunch. Sponsor conversations: show each one their slide draft and ask what is missing | Notes in the playbook |
| 13:00–16:00 | Launch the experiment matrix on Modal: claim 1 (lab vs baselines, single agent, random mutation), claim 3 (search on/off, Exa vs keyless), claim 4 (four memory setups) | All arms running with 3+ seeds; a table script reads partial results |
| 16:00–18:00 | Claim 2: fresh vs warm lab on Pacific's data with the two controls. Atul: lab view on the dashboard (score curve, claims ledger with sources) | First two seeds finished; dashboard shows a run in progress |
| 18:00–19:00 | Dinner. Photo for X with the dashboard on a big screen | Posted |
| 19:00–22:00 | Fill gaps: remaining seeds, failed arms rerun; first draft of slides from real partial numbers; Q&A list | Every slide has a number or a named run that will produce it |
| 22:00 | Launch overnight: remaining seeds, the longest lab run, and (only if there are 300+ records) the Tinker fine-tune | Jobs confirmed running on Modal; live machine plugged in, lid open |

**Checkpoint at 18:00 Saturday.** If any of these is not true, apply the cut order in `07_risks_and_cut_order.md`: lab beats baselines on select and confirm for the headline problem; Pacific adapter evaluates candidates; at least one sponsor experiment has a complete table.

## Sunday

| Time | What | Done when |
|---|---|---|
| 8:00 | Read overnight results. Regenerate every table with the script | Tables in the repo, dated |
| 8:30 | Score the test window once per problem with `lab.experiments.final_test` | Result files written with code hashes; nobody tunes after this |
| 9:00–11:00 | Slides and dashboard polish from final numbers. Limitations slide written | Deck complete; every number links to a run log |
| 11:00–12:00 | Record the 2-minute backup video. Fill in the submission page (description, repo, video, sponsor-use paragraphs) | Video uploaded; submission saved as draft |
| 12:00 | Feature freeze | Only fixes to things that are broken |
| 12:00–13:30 | Rehearse three times with a timer. Assign who answers which question | Under time twice in a row |
| 13:30 | Submit | Confirmation seen |
| From judging start | Demo at the table: live scoreboard first, then the evidence, then limits | |

## Demo order (3 minutes)

1. **Problem (20 s).** One sentence and one live number.
2. **Live proof (40 s).** The dashboard: forecasts sealed hours ago, graded by measurements we do not control; the chain verifies.
3. **How the lab works (30 s).** Reads, proposes a change, tests it on the past, keeps it only if it wins on later data.
4. **Evidence (50 s).** Test-window table; the fresh-versus-warm result; one sponsor table each at about ten seconds.
5. **Limits (20 s).** The leak we caught in our own backtest; one weekend of weather; where the simple rule is just as good.
6. **Next (20 s).** Sealed forecasts keep being graded after the event; the same lab on Pacific's prices.
