# Running the lab on Modal

Two compute-heavy parts can run on [Modal](https://modal.com) instead of this laptop:

1. **Candidate evaluation** for the research loop: `ModalEvalPool` in
   `lab/evalpool/modal_backend.py`, one remote call per candidate, up to 50 at once.
2. **The live sealed jobs**: `lab/modal_app.py` runs one coast cycle every 15 minutes and one
   turbulence cycle at :05 and :35, with the ledgers, state files, caches and promoted
   forecasters on a Modal Volume.

Everything here was written and tested on 2026-11-14 **without a Modal account**. The tests use a
fake adapter that runs the container-side code in-process. Nothing has run on Modal yet; see
"Untested until a real account exists" at the end.

## Saturday morning, in order

All commands run from the repository root.

1. **Create the account** at https://modal.com/signup. The Starter plan has no monthly fee and
   includes $30 of compute a month.
2. **Install and log in**:
   ```
   uv pip install --python .venv/bin/python modal        # or: .venv/bin/pip install -e '.[modal]'
   .venv/bin/modal token new                              # opens a browser, writes ~/.modal.toml
   ```
3. **Pick the workspace and environment.** If the account has more than one workspace, run
   `.venv/bin/modal profile list` and `.venv/bin/modal profile activate <workspace>`.
   Everything below uses the default environment (`main`).
4. **Deploy without schedules** (this is safe while the laptop loops run):
   ```
   .venv/bin/modal deploy -m lab.modal_app
   ```
   This creates the app `foresight-lab` with four functions: `evaluate_candidate`, `coast_cycle`,
   `turb_cycle` and `live_summary`. It also creates two Volumes: `lab-eval-cases` and
   `lab-live-data`.
5. **Try the evaluator** (the laptop loops can keep running):
   ```
   .venv/bin/python -m lab.swarm.run --problem coast_water --evaluator modal --generations 1 --proposals 2
   ```
   The first call uploads the encoded cases once (about 91 MB for `coast_water`); after that each
   candidate sends only its source. Compare a result with the laptop by running the same source
   with `--evaluator local`. The `EvalResult`s should be identical apart from `protections` and
   `duration_s`.
6. **Stop the laptop loops**: `lab.tasks.coast.live run` and `lab.tasks.turb.live run`.
   Wait until neither process exists (`ps aux | grep live`). Two writers must never append to one
   ledger: a loop still running on the laptop would fork the hash chain.
7. **Seed the Volume** with the laptop's ledgers, promoted forecasters and caches (once):
   ```
   .venv/bin/python -m lab.modal_app seed-volume
   ```
   It refuses if the Volume already holds a ledger (`--force` overrides; use it only if Modal has
   never run a cycle).
8. **Run one cycle of each by hand** and check that the chain continued:
   ```
   .venv/bin/modal run -m lab.modal_app::coast_cycle
   .venv/bin/modal run -m lab.modal_app::turb_cycle
   .venv/bin/python -m lab.modal_app status       # records, head hash, verify_ok per ledger
   ```
   The record counts must be the laptop's counts plus the new forecasts, with `verify_ok: true`.
9. **Turn the schedule on**:
   ```
   LAB_SCHEDULE=1 .venv/bin/modal deploy -m lab.modal_app
   ```
10. **Point the dashboard at Modal's data.** Run `.venv/bin/python -m lab.modal_app pull` whenever
    you want fresh data (or every few minutes from a shell loop). It copies each ledger and
    `state.json` from the Volume into `runs/<task>/live/`, and the existing dashboard server reads
    them as before. Do not run `pull` while a laptop loop writes to the same directory.
11. **Point the lab at Modal**: add `--evaluator modal` to `python -m lab.swarm.run`. The
    `--workers` flag only affects `--evaluator local`; Modal runs up to 50 candidates at once.

## Checking that it works

- `python -m lab.modal_app status`: for each ledger, the record count, head hash, `verify_ok` and
  the time of the last `state.json`. During a healthy schedule the coast state time advances
  every 15 minutes and the turbulence state time every 30 minutes.
- In the Modal web UI (Apps, then `foresight-lab`), each function has a log per run. A coast run
  ends with a line like `sealed ... water + ... wave, ... verify True`.
- `python -m lab.modal_app pull`, then open the dashboard as usual.
- To pause both cycles without redeploying: `python -m lab.modal_app pause`, and later `resume`.
  This writes or removes `/control/paused` on the Volume.

## Falling back to the laptop

1. `python -m lab.modal_app pause`, then `.venv/bin/modal deploy -m lab.modal_app` (without
   `LAB_SCHEDULE`) to remove the schedules.
2. Wait until any running cycle has finished (Modal UI, or `status` stops changing).
3. `python -m lab.modal_app pull`. This brings back every ledger file and `state.json`.
   `cache/` is not pulled; the laptop's own cache is older but works.
4. Promoted forecasters added on the Modal side are not pulled either; copy them with
   `modal volume get lab-live-data runs/lab/promoted runs/lab/promoted` if needed.
5. Restart the laptop loops (`lab.tasks.coast.live run`, `lab.tasks.turb.live run`). The chain
   continues from the pulled head.
6. Research runs: drop `--evaluator modal`.

## What it costs (pricing page, read 2026-11-14)

The rates are CPU $0.0000131 per physical core per second, memory $0.00000222 per GiB per second,
and Volumes $0.09 per GiB per month with 1 TiB a month free. The Starter plan includes $30 of
compute a month, 100 containers and limited scheduled and web functions. The estimates below
assume billing at the requested CPU and memory. Container start-up time is not included, and it is
unknown until measured.

| part | request | time per run (measured on the laptop) | per run | per month |
|---|---|---|---|---|
| one candidate evaluation | 1 core, 3 GiB | about 5 s of work | about $0.0001 to $0.0002 | about $0.03 for a 120-candidate research run |
| coast cycle, every 15 min | 2 cores, 4 GiB | about 26 s, plus about 10 s once an hour for promoted forecasters | about $0.001 | about $3 |
| turbulence cycle, every 30 min | 2 cores, 4 GiB | about 48 s; longer when the forecast fields are downloaded (every 3 h) | about $0.0017 | about $2.5 |
| Volumes | about 0.25 GB (ledgers, caches) | | | free (inside 1 TiB) |

All of this together should stay well inside the $30 monthly credit. Check the real numbers on the
Modal usage page after the first day.

## How the evaluator works

- The caller lints the candidate. A lint failure never leaves the laptop.
- The encoded cases for the requested windows are uploaded once to `lab-eval-cases`, under the
  SHA-256 of their bytes. They hold train cases, train truths, and only the public fields of the
  select and confirm cases. `runner.prepare_cases` has no argument for evaluation truths.
- Each evaluation sends `{key, source, time_limit_s, memory_limit_mb}` to `evaluate_candidate`.
  The container runs the same sandbox child runner as the laptop and returns predictions only.
- Losses are computed on the laptop from the select and confirm truths, which never leave it. The
  function that does this (`score_predictions`) is shared with the local evaluator.
- Results are cached by source hash, as in `EvalPool`. Remote outages (`error` starting with
  `remote:`), timeouts and memory kills are not cached.

## Isolation on Linux

What protects us when a candidate runs on Modal:

| layer | evaluator (`evaluate_candidate`) | live coast cycle (promoted forecasters) |
|---|---|---|
| lint, before anything runs | yes | yes |
| isolated interpreter, restricted builtins | yes | yes |
| wall-clock kill, resident-memory watchdog | yes | yes |
| rlimits (CPU, file size 0, no core dumps, address space) | yes (address space is enforced on Linux, unlike macOS) | yes |
| macOS `sandbox-exec` (no network, no file writes, read allowlist) | **absent on Linux** | **absent on Linux** |
| no outbound network | yes: `block_network=True` on the function; Modal drops all outbound traffic | **no**: the cycle must fetch gauges and weather |
| no access to other Modal resources | yes: `restrict_modal_access=True` | no |
| what the candidate's process could reach | the prepared cases on `lab-eval-cases` (truths of train only) | the whole `lab-live-data` Volume, including the ledgers |
| Modal container isolation | the Sandbox docs name gVisor or a VM as the runtime; I did not find the same statement for Functions | same |

The live coast cycle is the weak spot. A promoted forecaster that got past both the lint and the
restricted builtins would have network access and could read the ledgers. It could not write
bytes to a file (file-size rlimit 0), but it could rename or delete files on the Volume. The fix is
small and not yet made: let `PromotedRunner` (in `lab/tasks/coast/promote.py`) send its candidates
to `evaluate_candidate`, which is network-blocked and has no ledger mounted, instead of running
them in the cycle's own container. Until then only promote candidates a person has read.

## Doc pages relied on (read 2026-11-14)

- App and `App.function` parameters (`image`, `schedule`, `volumes`, `cpu`, `memory`,
  `max_containers`, `timeout`, `block_network`, `restrict_modal_access`):
  https://modal.com/docs/reference/modal.App (now served at `/docs/sdk/py/latest/App`).
- `block_network=True` drops all outbound traffic; Sandboxes run under gVisor or a VM:
  https://modal.com/docs/guide/sandbox-networking.
- Volumes: commit and reload, background commits, a final commit on container shutdown,
  last-write-wins for concurrent writers, no distributed file locking:
  https://modal.com/docs/guide/volumes.
- Prices and the Starter plan: https://modal.com/pricing.
- From the installed SDK (modal 1.6.4) signatures and docstrings:
  - `Image.debian_slim(python_version=)`, `Image.uv_pip_install`, `Image.add_local_python_source`;
  - `Volume.from_name(create_if_missing=)`, `Volume.batch_upload(force=)` with `put_file`,
    `Volume.listdir(recursive=)`, `Volume.read_file`, `Volume.remove_file`, `Volume.commit`,
    `Volume.reload`;
  - `Function.from_name`, `Function.remote` (`Function.map` and `Function.spawn` exist; I used
    `.remote` in pool threads so each candidate gets its own future);
  - `modal.Cron("*/15 * * * *")` (`modal.Period` exists too);
  - `modal deploy -m <module>`.
- Secrets are not used: the live jobs read public data only, and the research loop's LLM keys stay
  on the laptop.

## Untested until a real account exists

- Nothing has been deployed. Image builds, `add_local_python_source("lab")`, the pinned versions
  (numpy, pandas, scipy, httpx taken from this venv) and cold-start times are untested.
- The sandbox child runner on Linux, in particular the address-space rlimit at 2048 MB with numpy
  and a 91 MB payload. Docker was not running on this Mac, so I could not try it in a Linux
  container either.
- Whether `block_network=True` and `restrict_modal_access=True` still allow `Volume.reload()`
  inside `evaluate_candidate`. It is only needed when a warm container started before new cases
  were uploaded; if it fails, the evaluation returns a `remote:` error that is not cached.
- Linking `runs/` and `cache/` into the repo root in the container (`link_data_dirs`). This
  assumes the `lab` package is placed in a writable directory such as `/root`.
- The ledger's `fcntl` lock on a Modal Volume. Only one container writes each ledger
  (`max_containers=1`), so the lock should not matter, but it has not been exercised there.
- Volume commit timing: a cycle commits at the end, and Modal also commits in the background. A
  cycle killed mid-write could leave a partly written line; `verify` would then report it.
- `pull`, `seed-volume`, `status`, `pause` and `resume` against a real Volume. They are tested
  only against a directory-backed fake.
- The cost table above. The true duration of a cycle on Modal's CPUs is unknown.
