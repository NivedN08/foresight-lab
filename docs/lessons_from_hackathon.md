# What the hackathon taught us, and what it changes here

Written Nov 16, 2026, after the SF AI Research Hackathon (Nov 14 to 15). Two later projects supply the evidence:

- **The rehearsal** the night before (private repo `research-lab-rehearsal`): an agent-run lab tried on one BixBench3 task. Its results are in that repo's `docs/MORNING_REPORT.md`.
- **The event build** (`summit-lab`): a lab of agents on computational biology benchmarks, with blind replication teams, a referee and tiered memory. Its measurements are in `explanation.md`, section 9; the entries cited below (M-11, M-13, M-14, I-12, I-13, D-14, D-17, D-20) are in that file.

Every lesson below is a measured result, not an opinion. Each says what it means for this project.

## Lessons that transfer

**1. Organising agents around one model did not beat that model.**
- summit-lab: 3 of 8 for the lab against 4 of 8 for one model (M-11); 23 of 33 against 24 of 33 at about 11 times the cost (M-13); 27 of 36 against 26 of 36 at about 7 times the cost (M-14).
- Rehearsal: 0.20 against 0.20 on one task, with 56 agent runs against 1.
- Here: this agrees with our own ladder experiment, where a three-role swarm was worse than one model at twice the cost (`docs/experiments.md`). Do not add roles to the research loop.

**2. The same model has the same blind spot.** Two blind teams agreed on 36 of 36 tasks and were both wrong on 10; "replicated" answers failed 8 times in 34 and 5 times in 21 (M-14, M-13). Agreement between copies of one model is weak evidence. Here: proposals from the same prompt repeat (27 notebook proposals held 12 distinct ideas), and the select and confirm windows come from one calm season.

**3. Steps settled by code helped; steps settled by opinion hurt.** Code-level checks rescued more answers than they damaged, 3 to 0 and 9 to 3 (M-11, M-13). In the rehearsal a head agent overrode a table its checker had accepted and lost half the score. Here: the keep-or-revert decision in `lab/swarm/decide.py` must stay free of model judgment.

**4. An infrastructure failure is not a result.** A usage limit was first logged as "no answer" and counted against the system (I-12); halting instead recorded nothing false (I-13). Here: the same mistake happened on Nov 16, when the turbulence job graded 217 forecasts against no reports at all because the job had been frozen and its report store was empty. A usage limit in the research loop would likewise be recorded as a failed proposal.

**5. Laptops sleep.** A battery sleep cost the rehearsal two hours, and nothing stops a low-battery sleep. What recovered the run was an append-only log that the state could be rebuilt from. Here: both live jobs were frozen for 35.5 hours and neither ledger nor state says so.

**6. Freeze the design, then score unseen data once** (D-14, D-17, D-20). Here: experiment arms are ranked on the confirm window's loss, which is the same quantity acceptance selects on.

**7. Search added nothing measurable.** Briefed team 19 of 33, unbriefed team 21 of 33, single attempt 24 of 33 (M-13). Our own search and notebook arms were within noise too.

## What changes here

| Change | Lesson | Status |
|---|---|---|
| Turbulence grading waits for report coverage and backfills reports from the archive; a forecast is never graded against missing data | 4, 5 | built on branch `claude/foresight-pause-handling` |
| A gap record whenever a job finds it has not sealed for more than two intervals, shown in state | 5 | built on the same branch |
| A cycle interrupted in the middle is abandoned, not sealed with a stale time | 5 | built on the same branch |
| A usage limit halts the research loop without recording a failed proposal | 4 | built on the same branch |

## Decisions still open

1. **The 280 turbulence grades written on Nov 16 against empty or truncated truth.** Regrade them from the archive in a separate append-only file, or void them. Either way the original lines stay; the choice is whether "graded once" is kept strictly.
2. **Where the live jobs run.** The laptop with gap records, or Modal's scheduled functions (`docs/modal.md`), which costs money and has never been deployed.
3. **A one-look holdout.** Data issued since the Nov 13 build has never been seen by any run. It can score every run's final forecaster once, if the candidates and the rule are written down first.
4. **A stronger-model arm.** The ladder's single-agent arm uses Sonnet. Rerunning it with a stronger model on five seeds would test lesson 1 here directly, at a usage cost.

## What not to do

- Add referee, director or blind-team roles to the loop. The evidence is against it (lesson 1).
- Back-issue forecasts for hours the job missed. A forecast written after the fact is not a forecast.
- Put gap markers inside `sealed.jsonl` or edit `grades.jsonl`. The chain already proves nothing was deleted.
- Rank arms by confirm loss, or invest further in search or the notebook: both were within noise in both projects.
