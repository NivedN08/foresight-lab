# Holdfast (Track 4, World Models): first place overall

Repo: github.com/Hcoder10/holdfast. Read from a local clone, files only; nothing in it was run. Every claim below
gives the file it comes from. Inferences are marked as such.

## One-paragraph summary

Holdfast takes an open video world model, LingBot-World 2 (14B, causal-fast), and raises its PlayWorld score
without training or changing a single weight. A controller runs the model in 0.75-second chunks. Between chunks
it reads the model's state and writes small corrections back into the forward pass. The scored program does four
things. It adds the user's request to the prompt, which is a plain prompt fix. It turns up the model's attention
to that request eight times. It pulls each chunk's colour statistics halfway back toward the first chunk's. On
first-person cases it fades the caption out as the camera turns away, and fades in a sentence built from an Exa
answer when the camera comes back. The headline is 2.31 on all 171 PlayWorld cases against 2.10 for the
untouched model under the same judge, and 2.12 published for Genie 3. The 95-case held-out half also gives 2.31.
In my reading it won for five reasons. It claims a clear "beat the closed leader" result. It uses one framing,
"programming the hidden state", that carries the whole story. The held-out protocol is unusually rigorous for a
hackathon. Every failure is written down with numbers. The README walks a judge through the project in five
minutes. The weak spot is the comparison with Genie 3. Their own judge scores the untouched model 0.28 above the
paper's 1.82, so 2.31 against 2.12 compares numbers from two different judging setups. They say this themselves.

## Team, track and challenges entered

- **Team.** One person. The README is written in the first person ("I"). All but one of the 143 commits come from
  one author; the remaining commit is a GitHub web edit by the account `Hcoder10`. 140 of the 143 commit messages
  carry `Co-Authored-By: Claude Fable 5.2` and the same Claude Code session link. In practice one person drove one
  long Claude Code session that did most of the research and writing (see Build process).
- **Track.** Track 4, World Models: make an open world model hold together longer on PlayWorld.
- **Challenges.** Exa, past.dev and Modal, all three (`README.md` header). The past.dev section uses the required
  headings Agents, Layers, Results and Caveats, but in `docs/DETAILS.md`, not in the README itself.

## The problem and the benchmark

**PlayWorld** (arXiv 2608.13552, `docs/references.md`) tests a world model with an agent player. The model gets
a first frame and a text objective. A Claude Haiku 4.5 agent watches the generated frames and picks W/A/S/D,
arrow keys or WAIT. An episode ends on FINISH or after 40 steps. There are 171 cases in four dimensions:
Geometry Consistency (GC, 48 cases), Interaction Fidelity (IF, 50), Insight Evolution (IE, 30; "wait and observe"
with a still camera) and Out-of-sight Evolution (OE, 43; does the world move on while the camera looks away).

**Scoring.** Gemini 3.1 Pro answers yes/no rubric questions over contact sheets sampled at 10 fps and 0.5 fps.
A dimension score is 1 + 4 times the weighted yes-fraction, so the scale runs 1 to 5. GC, OE and third-person IF
cases first pass an instruction-following gate (pass, partial or fail), and a failed gate scores 1. Overall is
the unweighted mean of the four dimension means. That means the 30 IE cases count as much as the 50 IF cases.

**The record.** PlayWorld Table 2 (`docs/DETAILS.md`, checked against the live paper in
`results/scout_claims.json`):

| Model | GC | IF | IE | OE | Overall |
|---|---|---|---|---|---|
| Genie 3 (closed, top) | 2.74 | 2.40 | 1.51 | 1.81 | 2.12 |
| LingBot-World 2 (open, their base) | 2.04 | 2.13 | 1.95 | 1.16 | 1.82 |
| Matrix-Game 3.0 (an organiser-named starting model) | 1.30 | 1.25 | 1.00 | 1.00 | 1.14 |

`docs/references.md` notes that the paper scored LingBot-World 2 through its web interface, so local weights may
behave differently. The first README (commit `0c17a11`) lists Matrix-Game 3.0 and SANA-WM as candidate starting
models. The author picked the strongest open model with downloadable weights.

**Judge noise.** The paper reports small judge variance (0.011). The team measured much more noise on their own
videos. Byte-identical videos scored 1.0 / 5.0 / 4.2 on one case and 5.0 / 3.0 / 1.0 on another
(`results/hillclimb_log.md`). On the held-out set, two judging passes of the same videos differ by 0.48 points per
video on average (`results/held_out.json`, `repeat_judging_mean_abs_diff`). This finding shaped their whole
protocol.

## What they built, mechanism in detail

### The core idea in plain words

A world model like LingBot-World 2 makes video a little at a time. Each new piece is conditioned on four things: a
text prompt (through cross-attention), the recent frames it has already made (through a self-attention cache), a
"this frame is given" image-condition input, and the camera motion. All four are tensors that exist only while
the model runs. Holdfast treats them as a programming surface. The author's own term for this is "hidden-state
programming". The controller does not retrain anything. It edits those tensors between pieces of video, and each
edit targets one failure a viewer can see. Switch the edits off and you have the original model again. The set
of edits for an episode is called a "state program".

### Step by step, with files

1. **Chunked driver: `adapters/lingbot/stepper.py`.** The stock pipeline takes the whole camera path up front.
   `LingBotStepper` instead keeps the model's caches alive between calls, so each `step()` adds one chunk:
   3 latent frames = 12 video frames = 0.75 s at 16 fps, with 4 denoising steps (`FAST_TIMESTEP_INDEX`). This
   lets the PlayWorld player react to what it sees, and it gives the controller a point between chunks to read
   and write state. The self-attention cache holds 18 latent frames, 6 of them permanent "sink" frames from the
   episode start. That is the model's whole memory, about 3 seconds.
2. **Attention patching: `adapters/lingbot/sdpa_shim.py`.** This replaces LingBot's `flash_attention` with PyTorch
   SDPA, so the controller can add per-token biases or masks to the attention logits. A global `CROSS_BIAS`
   tensor adds `log(gain)` to the logits of chosen text tokens in every cross-attention call (all 40 blocks, all
   sampling steps). The shim counts every application (`BIAS_HITS`). That counter caught a real bug: the sink-gain
   patch had been installed in a module the fast model never calls, and the run logged zero hits
   (`results/hillclimb_log.md`, sweep4).
3. **Write 1, the objective register: `stepper.set_register` and `_cross_bias`.** The request ("the woman walks
   into the pond") is encoded on its own and placed after the caption in the 512-token text context. It gets
   `log(8)` added to its attention logits, so image tokens attend to it eight times as much. This applies to
   third-person cases only (`episode.py`, `cfg["register"]["when"]`). On first-person cases the words "camera" or
   "turn" got rendered as objects: a tripod camera appeared in the scene (sweep7).
4. **The prompt fix: `episode.py`, `prompt == "caption+objective"`.** PlayWorld's own released adapter passes the
   objective to the model, but their LingBot setup gave it the caption only. Appending the objective to the
   caption is the largest single piece of the gain (see the next section). When the register carries the
   objective (`replace: true`), it is not also appended.
5. **Write 2, latent re-anchoring: `stepper._denoise`, `anchor_stats`.** After each chunk is denoised, its
   per-channel latent mean (over time, height and width, 16 channels) is pulled halfway back to the first
   chunk's: `x0 += 0.5 * (mu0 - mu)`. This happens before the chunk is written into the attention cache, so the
   correction carries forward. It stops slow colour drift (a hall sliding from red to deep blue over 30 s) while
   leaving content free to change. It runs on every case.
6. **Heading measurement: `adapters/lingbot/odometry.py`.** A SIFT homography between consecutive frames gives
   the rotation the frames actually show (`H = K R K^-1`). It accumulates from the episode start. A bug fixed at
   02:03 Sunday: the heading had been reset at every walk step.
7. **Caption gate: `episode._ret_update`, `cap_gate`.** First-person cases with camera motion only. The caption
   moves into a register whose gain falls log-linearly from 1 (within 25 degrees of the start view) to 0.1 (60
   degrees and beyond). A neutral "surrounding area of the same place" sentence becomes the base prompt. This
   targets one failure: the caption kept painting the starting subject into every direction, so the judge read a
   turn as "the camera stayed still".
8. **Return register with Exa: `adapters/lingbot/outsight.py` and `episode._ret_update`.** At step 0 a Claude Haiku
   "cartographer" names the thing in the first frame that is mid-process and writes a web question ("How far can
   a Labrador walk in 10 seconds?"). Exa `/answer` returns a sourced fact. A "director" turns it into one 18-32
   word sentence for how the scene should look about 8 s later. That sentence becomes a second register: gain
   0.01 while the camera is away, ramped to 4 when the measured heading comes back to the start. On a later run
   of the same scene, a "composer" recalls the sentence from past.dev instead of asking Exa again.
9. **The player: `adapters/lingbot/player.py`.** This is PlayWorld's Haiku agent prompt, copied from the released
   adapter. It is consulted at segment boundaries, with at most two extra chunks per preset segment, instead of
   after every chunk, to bound cost. This is a deliberate protocol difference from the paper. Both arms use it.
10. **Data hygiene: `episode.ALLOWED_CASE_FIELDS`.** Generation reads only the task id, category, prompt, image,
    caption, perspective and action. The rubric is read only in `eval/judge_prep.py`.

The scored configuration is in `results/final1_plan.json`:
`{"prompt": "caption+objective", "register": {"gain": 8, "when": "third person", "replace": true},
"anchor_stats": 0.5, "cap_gate": {"away": 0.1}, "ret": {"gain": 4}}`.

Note that the README presents the scored program as "the prompt fix plus Fix 1 plus Fix 2". The caption gate and
the Exa return register are also in the scored program on first-person cases. The README's "Fix 3" (landmark
checkpoints) is not.

### Other machinery built (not in the scored run)

- **Shadow world** (`episode._shadow_world`, `stepper.fork`). When the camera turns away, the whole generation
  state is copied and the copy keeps generating the abandoned view. On return, the arriving frame is pinned to the
  copy's frame through the image-condition channel.
- **Landmarks** (`episode._landmarks`): checkpoints saved where the camera stops, written back on return. This is
  the README's "Fix 3" and the live demo's visualizer.
- **Camera guidance** (`camguide.py`): classifier-free-guidance-style extrapolation of each block's camera scale
  and shift. **Residual steering** (`acts.py`, `loop/directions.py`): directions from pairs of judged reruns.
  **Spectral self-anchoring** (`sdpa_shim.SSA`), ported from a paper the Exa scout found. **Read probes**
  (`loop/read_probes.py`). **Planner, critic, repair loop, debugger**: see Autonomy.

## How they beat the benchmark

### The numbers

`results/full_benchmark.json` (171 cases; held-out half judged twice and averaged, development half once):

| | GC | IF | IE | OE | Overall |
|---|---|---|---|---|---|
| LingBot-World 2 untouched, their driver and judge | 2.35 | 2.05 | 2.30 | 1.68 | 2.10 |
| Holdfast | 2.45 | 2.30 | 2.57 | 1.94 | **2.31** |
| Genie 3, published | 2.74 | 2.40 | 1.51 | 1.81 | 2.12 |

Paired over 171 cases: +0.218, 95% bootstrap interval +0.029 to +0.401, 62 cases better and 40 worse. The
interval for Holdfast's own overall is 2.14 to 2.50. I checked the arithmetic. The overall is the mean of the
four dimension means: (2.448 + 2.298 + 2.57 + 1.936) / 4 = 2.313. Each 171-case dimension is the case-weighted
mix of the two halves; for example IF = (15 x 2.445 + 35 x 2.236) / 50 = 2.299. Every README number checks out
against the JSON.

Holdfast beats Genie 3's published number on IE (2.57 against 1.51) and OE (1.94 against 1.81). It is behind on
GC and IF. The overall lead comes from IE, which only 30 cases feed but which carries a quarter of the weight.

### Evaluation protocol

- **Split.** `eval/sample_cases.py` draws half of each dimension with seed 20261114. The draw depends only on the
  task ids. The interaction split was cut from 25 to 15 cases "decided before any of these cases was run"
  (`results/half_benchmark_cases.json`), so the development half is 76 cases and the held-out half 95 (24 GC, 35
  IF, 15 IE, 21 OE).
- **Development.** Everything was tuned on the 76-case half. Much of it was tuned by the orchestrating Claude
  reading frame strips against the rubric wording, not by the judge (`results/hillclimb_log.md`, "State-write
  sweeps"). For example, gain 8 for the register was chosen from frame-read scores on seven development IF cases
  (3.2 to 3.9-4.0).
- **Freeze, then run once.** Commit `1c6c155` (Sun 03:17) fixed the configuration and launched the held-out run.
  Three arms ran on the 95 cases: untouched, objective-only and final. They were blinded together, shuffled into
  anonymous folders (`eval/blind.py`), and judged twice through Google's API with PlayWorld's own prompts and
  contact sheets (`eval/judge_prep.py`, `eval/openrouter_judge.py`). Scores are converted with PlayWorld's
  `normalized_score` (`eval/held_out.py`).
- **A blinding bug.** It was caught at the first tally. The blinding script had replaced the arm names "final" and
  "objective" inside the prompts, so "final score" became "system_under_test score" in about 62 places per
  prompt. The 140 affected prompts were restored, the 190 affected scores were set aside (kept on disk) and
  re-judged (`results/hillclimb_log.md`, 04:50).

### Held-out half (`results/held_out.json`)

| Arm | GC | IF | IE | OE | Overall |
|---|---|---|---|---|---|
| Untouched | 2.40 | 1.89 | 2.19 | 1.92 | 2.10 |
| Objective in the prompt only | 2.66 | 2.04 | 2.11 | 2.13 | 2.24 |
| Final program | 2.48 | 2.24 | 2.47 | 2.06 | 2.31 |

- Final minus untouched: +0.21, interval **-0.04 to +0.47**. 36 better, 23 worse.
- Objective-only minus untouched: +0.14 (-0.13 to +0.39).
- Final minus objective-only: +0.08 (-0.14 to +0.30). The extra writes gain on IF (+0.19) and IE (+0.37) and lose
  on GC (-0.18) and OE (-0.07). GC gate failures rise from 1 to 4.

### My check: does the evidence support the claim?

What holds up:

- The files reproduce every README number. The per-case scores are in the JSON, and the tally code
  (`eval/held_out.py`) is short and readable.
- The two halves agree: +0.19 on development and +0.21 on held-out. So selection on the development half did not
  obviously inflate the result.
- Re-anchoring is the cleanest single effect. On IE cases ("wait and observe") the only write that differs is
  re-anchoring, because the request is never appended or registered for those cases. IE rises in both halves:
  2.40 to 2.67 on development, 2.19 to 2.47 on held-out. Each half has only 15 cases, though.

What is weaker than the README suggests:

- **Genie 3 is not compared like for like.** Their judging gives the untouched model 2.10; the paper gives it 1.82.
  The 0.28 gap comes from some mix of the judge transport, their player protocol (segment-boundary consults) and
  their driver. Genie 3's 2.12 comes from the paper's setup. If the same offset applied to Holdfast, it would sit
  near 2.03 on the paper's scale. That is an inference, not a measurement. The README states the caveat ("the fair
  comparison is 2.10 to 2.31"), but the headline and the "(we beat Genie!!)" line lean on 2.31 against 2.12.
- **On the clean data, the interval crosses zero.** The README's "+0.22 (+0.03 to +0.40)" pools the development
  half, where the choices were made. On the 95 held-out cases alone, the interval is -0.04 to +0.47. The README
  says "The gain held" without that interval; `docs/DETAILS.md` gives it.
- **"Fix 1: Interaction 2.05 to 2.30" mixes two changes.** That figure includes the prompt fix. The register's own
  effect shows only on the held-out half (2.04 to 2.24 over objective-only). On the development half, interaction
  barely moved from untouched to final: 2.43 to 2.45 (`results/dev_half.json`).
- **Mixed judging.** The 171-case figure mixes two judging passes (held-out) with one (development). The
  development baseline videos come from Saturday's run (`half1`) and were re-judged alongside the final arm. Their
  numbers moved between the two judgings (IF 1.98 to 2.43, IE 2.85 to 2.40; `results/half1_tally.json` against
  `results/dev_half.json`). That is the judge noise again.
- **"Never used for development" is not strictly true for 7 held-out cases.** GC013, GC022, GC037, GC041, OE014,
  OE015 and OE024 were generated and judged in Saturday's pilots (`results/ledger.jsonl`), most before the split
  existed at 16:59. GC022 is also among the pairs used to extract the GC steering direction
  (`results/steer/report.json`). Steering is not in the final program, so the effect is probably small. But
  `docs/DETAILS.md` says the 95 cases "were not generated or looked at" before the freeze.
- **The objective-only arm (`conf1`).** The case list for the "confirmation run" on the untouched half was
  committed at 23:25 Saturday (`bb850e3`). I cannot tell from the repo when the `conf1` videos were generated, or
  whether anyone looked at them before the 03:17 freeze.
- **The Exa register rarely fired.** In 21 of the 52 first-person episodes that got an Exa-derived sentence, the
  register was active on 0 chunks (`results/exa_sources_final.jsonl`, `chunks_with_register_active`). Several
  questions name the camera itself as the moving thing. Some sources are weak (a homework-help site, wikiHow).

## Experiments, ablations and failures

The project ran about 35 sweeps. Most ideas failed, and the repo says so in tables with numbers
(`docs/DETAILS.md`, "What did not work"; `results/hillclimb_log.md`). The arc matters:

- **The first approach failed late.** Saturday afternoon was spent on rotational scene memory, a Claude "director"
  rewriting the prompt as the scene progressed, and world-anchored cross-attention. The first real half-benchmark
  run, at 21:02 Saturday, eleven hours in: untouched 2.12, memory plus online director 2.02 (paired -0.11). The
  director's prompt switches caused hard resets.
- **The pivot.** At 21:21 a judged hill-climb loop began. Trial 3 (objective in the prompt) was the only effect
  clearly outside the judge noise: IF +1.5 on the pooled baseline. Trials with the shadow rollout, a step-0 Exa
  brief, the watchdog and rotation memory were rejected. The residual-stream work started at 22:09; the DETAILS
  file says a blog post on weight-space programming, found by web search, prompted it. From 23:29 to 03:00 the
  state writes were built and swept one at a time.
- **Measuring the judge** (21:40-23:20). Identical videos scored up to 4 points apart, so they stopped re-judging
  unchanged videos, gave them a zero difference, and pooled baseline judgings (`loop/pool_baseline.py`).

Ablations with the benchmark judge (each named in `docs/DETAILS.md` and the log):

| Tried | Result |
|---|---|
| Objective-only arm on held-out (the main ablation) | 2.24 against final 2.31; most of the gain is the prompt fix |
| Residual "alive" write, frozen-case test (sweep21) | Frozen cases 1.87 to 1.93; healthy controls 3.00 to 2.45 |
| Launch-time planner choosing the program (sweep21b) | 1.59 against 2.19: its menu described "alive" as a fix |
| Shadow world, post hoc, on held-out | 2.40, then 2.32 on all 171; the held-out gain did not replicate |
| Landmarks on top of the shadow world | +0.81 on 10 development cases, -0.04 on 9 held-out; 2.32 overall either way |
| Exa action sentence, constant gain 8, IE cases | IE 2.86 to 2.40; the subject loops one motion |
| Same as a three-stage timeline / as an impulse | 2.14 / 2.75 against 2.51; impulse +0.07 on held-out |
| Object ledger (region write-back) | 1.55 against 1.88 on 11 cases |
| Self-selection by a Sonnet critic among programs | Held-out +0.08 (-0.06 to +0.22), development -0.10 |
| Predicting the judge from activations | 0.41-0.44 at picking the better rerun (chance 0.50) |

Frame-read failures: attention gain on the sink frames had no effect up to 16x and smeared at 1000x. A direct
diagnostic showed the model already puts 17-21% of its attention there. Also: a heading-indexed view bank, larger
attention windows, forward speed above 1.0, motion warped into the starting noise, camera guidance (it fixes
ignored strafes but over-rotates good cases), and cross-attention masks.

Bugs found through instrumentation: the sink-gain patch in the wrong module (zero hits logged); residual writes on
late sampling steps causing colour collapse; the heading reset at every walk step; the landmark trigger counting
the player's top-up steps; the start view pinned with the VAE's one-frame first latent into a four-frame slot
(it decoded washed out); a judging chain that counted folders before clips had finished. Each is logged with
its fix.

Read probes (`results/read_probes.json`): the chunk's action decodes at 0.92 accuracy (block 29) against a 0.22
majority baseline, with cases held out. "Back home after an excursion" reaches AUC 0.88 against 0.78 for a time
and action baseline. This is a read result, not a score gain.

## How they used Exa, past.dev and Modal

### Exa: used in the main loop, but its effect on the score is unproven

- **Inside generation** (`adapters/lingbot/outsight.py` calls `tools/exa_search.answer`). Every first-person episode
  in the scored program asks Exa how fast the visible process moves. The answer becomes the return-register
  sentence. This makes web search part of the main loop, as the challenge requires. `results/exa_sources_final.jsonl`
  holds, per episode, the question, the answer, the source URL, the sentence and the active-chunk count. Their own
  test says it did not help: the final arm's OE score (2.06) is below the objective-only arm's (2.13) on held-out.
  The README admits this.
- **Scout** (`loop/scout.py methods`). It groups the judge's held-out summaries into failure modes by regex (the
  judge writes in Chinese), then searches Exa with `startPublishedDate` 2025-01-01. A small model maps each hit
  onto one of the controller's eight "write ports". Output: 15 proposals with URLs in `results/scout_report.md`.
  One proposal, FreqForcing's spectral self-anchoring, was implemented (`sdpa_shim.SSA`, commit `b722c9e`). No
  judged result for it is reported.
- **Claim checker** (`loop/scout.py claims`). It fetches pages with Exa `/contents` and checks the four numbers the
  README quotes. All four were found (`results/scout_claims.json`). It is a neat way to make a writeup verifiable.
- **Logging.** Every call goes to `results/exa_log.jsonl`.

### past.dev: real use with read boundaries, but not on the scoring path

- **Code.** `tools/mem.py` is a thin client. Nine identities (controller, critic, planner, cartographer, director,
  composer, proposer, experimenter, scout). Three audiences used as read boundaries: `wh-world` (sourced facts
  and sentences), `wh-programs` (calibration, programs, defects, beliefs and rules) and `wh-lab` (hypotheses and
  judged lessons). The lab layer is kept out of running episodes so nothing learned from the judge can leak into
  generation. Every call is logged to `results/memory_log.jsonl` (76 writes, 165 recalls from the local machine).
- **Uses on the scoring path.** The composer recalled cached Exa sentences in 4 of the 52 scored episodes
  (`past.dev:` sources in `results/exa_sources_final.jsonl`). Recall took about 1 s against 5-11 s cold
  (`results/pastdev_scene_cache.json`, 4 cases). The controller also recalls a measured turn gain, but only in
  closed or memory modes, and the scored run used open mode.
- **Demos.** `tools/planner_memory_demo.py` shows a refuted belief flipping another agent's choices: 8 of 8 cases
  with the harmful write before the experimenter's measurement and rule, 0 of 8 after, read with past.dev's
  `queryTimestamp`. The records were ingested for the demo with set timestamps; they replay real events.
  `tools/mem_bench.py` compares the planner with no memory (77% of 48 controls right), with the whole log pasted
  in (100%) and with past.dev recall (92% with about a fifth of the prompt). One run, 12 scenes, and the right
  answers are hard-coded from their own measurements. The README states the latency loss.
- **Caveats they list.** Audiences were added late, so earlier records are visible to every agent. Identity is
  declared, not authenticated. No later episode was shown to score better because of a recalled program.

### Modal: the compute backbone

- `modal_app/wh_modal.py`. Weights and benchmark data live on a volume. Jobs fan out to H200 shards. `orchestrate`
  runs the fan-out inside Modal "so the run survives the launching machine going offline" (added after a dropped
  connection killed a run). Shards skip finished cases, so a run resumes by relaunching. One model load can serve
  several configurations, and `replay_shard` re-encodes finished videos to record activations.
- `modal_app/live_app.py` is a live web page behind an access code. Upload a photo and two H200 containers
  generate the untouched model and Holdfast side by side. The live variant is the shadow-world plus landmarks
  program, not the scored one. `modal_app/hf_upload.py` publishes the rollouts as a Hugging Face dataset.
- The README says "every rollout ran on Modal" and up to 19 GPUs ran at once. Saturday's first pilots ran on a
  leased box (`scripts/box_setup.sh`, `scripts/serve_jobs.py`) before the Modal app existed at 15:48, so "every"
  is slightly loose. All benchmark-scale runs were on Modal. The budget is logged: the owner's limit was $250 at
  an assumed $4.50 per GPU-hour, the held-out judging cost $125 on Gemini, and the last morning cost about $100
  on Modal and $45 on Gemini (`results/hillclimb_log.md`).

## Autonomy and self-improvement

- **Inside an episode, fully automatic.** Once launched, the controller measures heading, sets register gains each
  chunk, calls Haiku and Exa at step 0 and writes the sentence, with no human in the loop. The PlayWorld player is
  an agent too.
- **The research loop: mostly an AI agent driven by hand.** `loop/hillclimb.py` is a full proposer, experimenter,
  judge and critic loop with Exa search and past.dev lessons. A manual single-trial mode was added five minutes
  after it was created, and the log shows five trials. After that, "A model in the driver's seat (Claude, as the
  orchestrating agent) ran the loop by hand" (`docs/DETAILS.md`). That Claude session launched sweeps, read frame
  strips, scored them against the rubric wording, and kept or dropped writes. So the research was largely
  automated by one long Claude Code session, with the human setting direction and budget (inference from the
  commit trailers and the DETAILS wording).
- **Learning from its own results.** Several real cases. The judge-noise measurement changed the protocol. The
  planner failure (it trusted a belief) produced a rule in memory that the planner then obeyed. The scout maps
  judged failure modes to new methods. The repair loop and the debugger (`adapters/lingbot/repair.py`,
  `debugger.py`) patch the program from a critic's verdict and write winners to past.dev. But none of the
  self-improving parts are in the scored run. The debugger ran on six cases and was scored only by its own Haiku
  critic, which the team calls weak (`results/debugger_sessions.md`).

## Presentation and judge-facing craft

- **A guided tour, not a dump.** The README opens with "Hey judges!", then "A 5 minute plan to see what this is and
  whether it is real." It gives three numbered reasons to pick the project, then three steps: watch the 2-minute
  video, look at three pictures, skim one table. The full write-up is pushed to `docs/DETAILS.md`. The judge
  rewrite came at 13:46 Sunday, 45 minutes before the deadline (commit `efd70bc`).
- **One problem, one fix, one picture.** Three plain-language failures ("It does not listen", "It drifts", "It
  forgets"). Each gets a before/after GIF from a real case (`docs/media/*.gif`), a hand-drawn-style explainer
  GIF made by code (`scripts/make_explainers.py`), and one number.
- **A borrowed frame.** "Weight-space programming ... one level up" ties the work to a recent idea judges may know.
  The slides close on "Hidden state is a programming surface." (`scripts/make_slides.py`).
- **Honesty as a feature.** Repeated "Straight talk:" and "Being straight with you:" paragraphs admit that Fix 3
  is not in the score, that Exa did not add to it, and that past.dev was slower. An FAQ answers "Is 'beats Genie 3'
  a fair claim?" before anyone asks. `docs/DETAILS.md` has a "What did not work" table with about twenty rows and a
  "Where it still breaks" list.
- **Instrumentation as proof.** "Every write records how many times it was actually applied", so a silent no-op
  shows up as a zero (`docs/DETAILS.md`). The landmark GIF's plot is drawn from the run log.
- **Easy to verify.** Every number points at a JSON file. A one-line `modal run` command reproduces one case. A
  live upload-a-photo page exists. An HF dataset script publishes all 171 rollouts with their logs and scores.
- **Caveats softened for the pitch.** The one human commit (`d44d17e`, 14:45) adds "(we beat Genie!!)" and removes
  the line "the claim I stand behind most is 2.10 to 2.31". It also changes "The biggest single piece" to "One of
  the biggest pieces". Commit `cff5fbe` ("Slides: caveats left for the talk", 15:19) strips the 1.82 caveat, the
  Exa no-gain caveat, the landmark caveat and "What still breaks" from the slides. The README keeps most caveats;
  the slides lead with the win.
- **Demo assets.** The 90-second demo video is built by `scripts/make_demo.py`. It is not in git; the README says
  it is on the submission page. The venue clips are held back because they show bystanders.

## Why it stood out, scored against the five criteria

**Research rigour.** This is the strongest part. There is a pre-registered development/held-out split, a frozen
configuration, three arms on the held-out half (untouched, prompt-only, final), blinded judging, two judging
passes, bootstrap intervals, wins/losses counts, a measured judge-noise floor, and a post hoc gain that was
re-tested on the rest of the benchmark and reported as not replicating. Weaknesses: the held-out interval crosses
zero; much of the tuning came from an AI's frame reading rather than the judge; 7 held-out cases had been touched
in pilots; and the Genie 3 comparison crosses judging setups.

**Novelty.** Editing the inference-time state of a video world model through attention-logit gains, latent
statistics and state forks is a fresh frame for a hackathon. The objective register and re-anchoring are simple.
Calling them a "state program" with named write ports is what makes them feel like a method. The prompt fix is
not novel, and they say it is the biggest piece.

**Results.** It shows a measurable gain against a strong baseline on the full benchmark, a headline above the
closed leader's published number, and a working live demo. That is exactly what the rubric rewards, even though
the cleanest reading is "+0.21 on held-out, interval touching zero".

**Autonomy and self-improvement.** The episode loop is autonomous, and the research loop was run by a Claude
agent overnight. There are real examples of the system changing its decisions from its own measured results (the
planner rule, the scout). Judges likely credited the debugger and the memory demos, even though none of it feeds
the score.

**Interpretability and reproducibility.** This is excellent: per-write application counters, run logs that drive
the visualizer, every Exa call and memory call logged with sources, a ledger of every judged case, a 380-line
hill-climb log, the exact config in JSON, and Modal commands to rerun. The model weights are external and
non-commercial, and a full rerun needs Modal, Gemini and Anthropic keys, but that is unavoidable.

## Build process

- **143 commits**, Saturday 10:08 to Sunday 15:35 Pacific, with no gap longer than about two hours
  (19:17 to 21:02 Saturday). About 23,800 lines were added in total, including result files. The busiest hours
  were Saturday 15:00 (9 commits) and Sunday 10:00 (9).
- **Sat 10:00-15:00:** plan, the chunked stepper, odometry, rotation memory, judge prep, a Haiku-judged pilot on
  the 1.3B model, then 14B runs on a leased box. **Sat 15:00-21:00:** grafts, the Modal app (15:48), the agent
  player, the online director, the half-benchmark draw (16:59), and a negative first result (21:02).
- **Sat 21:00-Sun 03:20:** the judged hill-climb, the prompt fix, steering, the text register (23:29), state writes
  (00:02), the caption gate, the final plan (03:07) and the held-out launch. **Sun 03:20-06:05:** the blinding fix,
  the held-out result (05:05), the full 171-case result (06:01).
- **Sun 06:00-13:30:** post hoc work (planner, shadow world, scout, ledger, landmarks, debugger, probes, past.dev
  benchmark), all reported, none added to the score. **Sun 13:46-14:45:** the judge-facing README, the rename to
  holdfast, the human edit.
- **After the 14:30 deadline** (commits until 15:35): the live web app, slides, and the HF dataset upload. My
  inference is that these prepared the live pitch round rather than changing the submission.
- **Tooling.** One Claude Code session (Claude Fable 5.2) co-authored almost every commit. There is no CLAUDE.md
  or AGENTS.md in the repo. The in-loop agents use Claude Haiku 4.5 (`tools/llm.py`), the self-selection critic
  uses Sonnet, and the judge is Gemini 3.1 Pro.

## Lessons we can reuse

1. **Run a pre-registered held-out split and say so loudly.** Draw the split by seed before any result, tune on
   one half, freeze the config in a committed file, run the other half once, and report both halves. It was the
   single most persuasive thing in the repo, and it cost nothing but discipline.
2. **Measure your judge's noise first, then design around it.** Re-judge identical outputs, report the repeat
   spread, judge the final comparison twice, and treat unchanged outputs as zero difference. This turned a noisy
   LLM judge into usable evidence and let them reject false wins early.
3. **Always include the "dumb fix" arm.** Their objective-only arm showed that most of the gain was a prompt fix.
   Reporting it made the rest more believable, not less. Find the cheap baseline that explains your gain before a
   judge does.
4. **Give the work one frame and one picture per idea.** "Hidden-state programming" plus three
   problem/fix/GIF/number triples made a messy weekend of 35 sweeps legible in five minutes.
5. **Write the README as a five-minute tour for judges.** Open with why it should win, give a timed path through
   it, push detail to a second document, and answer the obvious skeptical question in an FAQ.
6. **Instrument every intervention with an application counter.** A write that silently does nothing is the
   commonest bug in this kind of work, and a counter that logs zero catches it.
7. **Log every failure with its number.** A "What did not work" table with about twenty judged rows read as rigour,
   not weakness. Re-test post hoc wins on fresh data and publish the non-replication.
8. **Put sponsor tools where a judge can trace them, and be honest when they do not move the score.** Exa calls
   logged with source URLs per episode; past.dev with named identities, audiences and a time-travel demo. Both
   sponsor stories are concrete even though neither raised the score.
9. **Let an agent run the overnight sweep loop, but keep a human-readable log it must append to.** The
   `hillclimb_log.md` pattern (each sweep: what, cases, result, verdict) is what made the agent's night auditable.
10. **Be careful with "we beat X" across judging setups.** They got away with it by stating the caveat, but a
    sharp judge could have pressed on it. If we cannot rerun the leader, lead with the paired gain and present the
    leaderboard number as context.

## Open questions / things I could not verify

- Why their untouched baseline scores 0.28 above the paper's (2.10 against 1.82), and whether the judges weighed
  that when accepting "beats Genie 3". The candidates are the judge transport, the segment-boundary player, the
  local weights against the paper's web interface, and their driver. The repo does not isolate this.
- When the objective-only arm (`conf1`) on the held-out cases was generated, and whether anyone looked at it
  before the 03:17 freeze.
- How much the 7 held-out cases touched in Saturday's pilots (GC013, GC022, GC037, GC041, OE014, OE015, OE024)
  influenced choices. It is probably little, but it contradicts "not generated or looked at".
- The demo video, the live app and the HF dataset are not in the repo (videos are gitignored), so I could not
  check that they match the claims. I did not open the GIFs frame by frame.
- Whether spectral self-anchoring (FreqForcing port) was ever judged. I found the code and the commit, but no
  results file.
- The pitch itself. The slides removed caveats "for the talk", and I cannot know what was said.
