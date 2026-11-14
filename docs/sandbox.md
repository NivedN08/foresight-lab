# The candidate sandbox

Agent-written forecasting code ("candidates") is untrusted. `lab.sandbox.run_candidate`
fits a candidate on past cases and predicts later cases in a fresh child process. The goals:
the candidate must not see the truth for evaluation cases, must not read or write files or
use the network, and must not run forever or eat the machine's memory.

This page says what each layer does, which layers are active on which platform, and where
the gaps are. The macOS column was checked on this machine (Apple silicon, macOS 26 /
Darwin 25.5) by `tests/test_sandbox.py`. The Linux column has **not** been run; it describes
what the code does there.

## How a run works

1. **Lint** (`lab/sandbox/lint.py`). Static checks on the source; if any fail, nothing runs
   and the result carries every message (`"line N: ..."`).
2. The parent writes one JSON input file into a new temp directory: the source, train
   cases with their truths, and eval cases **without truths**. `run_candidate` has no
   argument for eval truths at all, and eval cases are encoded from their public fields
   only (`case_id`, `issue_time`, `target_time`, `lead_hours`, `inputs`).
3. It starts `<real python> -I -S -X utf8 -c <child runner> <input> <pkgs dir> <limits>`
   with a minimal environment (`PATH=/usr/bin:/bin`, `HOME` and `TMPDIR` set to the temp
   dir, BLAS threads set to 1, nothing else; `-I` also makes Python ignore `PYTHON*`
   variables). `pkgs dir` holds only a symlink to numpy, so the child's import path is the
   standard library plus numpy. On macOS the command is wrapped in `sandbox-exec`.
4. The child applies resource limits, points fd 1 and `sys.stdout` at stderr (so the
   candidate's prints cannot forge the result), executes the candidate with restricted
   builtins, calls `fit` once and `predict` per eval case, and writes **one JSON object**
   to its private copy of the original stdout.
5. The parent watches wall-clock time and resident memory, kills the child's whole process
   group on a breach, parses the JSON (never unpickles anything), and checks that there is
   one non-empty dict of finite floats per eval case.
6. Every failure (lint, timeout, memory, crash, bad output) is appended as one JSON line to
   `log_path` if given: UTC timestamp, SHA-256 of the source, kind, error, lint messages,
   duration, protections, candidate name. Successes are not logged.

`SandboxResult` has `ok`, `predictions`, `error`, `lint`, `duration_s`, `protections`,
`stdout_tail` (the tail of the child's stderr, which includes anything the candidate
printed), plus `kind` (`lint|timeout|memory|crash|bad_output`, `None` on success) and `name`.

## The layers

| Layer (`protections` entry) | What it does | macOS (tested) | Linux (untested) |
|---|---|---|---|
| `lint` | AST checks, see below | active | active |
| `isolated_interpreter` | `-I -S -X utf8`, minimal env, fresh empty temp cwd, sys.path = stdlib + numpy | active | active |
| `restricted_builtins` | candidate's builtins lack `open`, `eval`, `exec`, `compile`, `getattr`/`setattr`/`delattr`, `globals`/`locals`/`vars`, `input`, `breakpoint`, `help`, `exit`, `memoryview`; `__import__` only allows the lint's import list | active | active |
| `wall_clock_kill` | SIGKILL to the child's process group at `time_limit_s` | active | active |
| `rss_watchdog` | parent reads the child's resident memory every 0.1 s (`ps` on macOS, `/proc` on Linux) and kills it above `memory_limit_mb` | active | active |
| `rlimit_cpu` | CPU seconds capped at `time_limit_s + 2` | active | expected |
| `rlimit_fsize` | max file size 0: no bytes can be written to any regular file | active | expected |
| `rlimit_core` | no core dumps | active | expected |
| `rlimit_as` | address-space cap at `memory_limit_mb` | **refused by macOS** (`setrlimit` fails), so not listed | expected |
| `sandbox_exec_no_network` | `sandbox-exec` profile denies all network | active | not available |
| `sandbox_exec_no_file_write` | profile denies file creation and writes (only `/dev/null`) | active | not available |
| `sandbox_exec_read_allowlist` | profile allows reading only the Python installation, the numpy package, the temp input dir, `/usr/lib`, `/System`, the dyld cache, `/dev/urandom`, `/dev/random`, `/dev/null` and `/` itself | active | not available |
| `modal_block_network` | Linux on Modal only (`evaluate_candidate` in `lab/modal_app.py`): the function is created with `block_network=True`, so Modal drops all outbound traffic from the container; this replaces `sandbox_exec_no_network` there | not applicable | listed when run on Modal (untested until an account exists) |
| `modal_restrict_modal_access` | Linux on Modal only: the function is created with `restrict_modal_access=True`, so the container cannot reach other Modal resources (the ledgers' Volume is not mounted at all). There is no file-read allowlist or file-write block beyond `rlimit_fsize`; see docs/modal.md | not applicable | listed when run on Modal (untested) |

Which `rlimit_*` and `sandbox_exec_*` entries appear is decided by a probe run once per
process (`runner._probe`): it tries the rlimits in a throwaway child and checks that Python
plus numpy start under the real `sandbox-exec` profile. If `sandbox-exec` is missing or
broken, the run still happens without it and the `sandbox_exec_*` entries are absent.

The `sandbox-exec` profile is `(deny default)` plus the allowances above, `process-exec`
of the real Python binary only, `sysctl-read`, and `file-read-metadata` everywhere. Measured
here with a candidate that bypasses the lint and the restricted builtins (tests marked
"past lint", plus a manual check):

- TCP connect to a listener on 127.0.0.1: blocked (works in the control run without the profile).
- Creating a file in a temp dir: blocked; the file does not appear (created in the control run).
- Reading a file outside the allowlist: blocked (read in the control run).
- `os.fork()` and running `/bin/echo` via `subprocess`: blocked.
- Listing directories outside the allowlist (home, project, `/tmp`, `/etc`): blocked.
- `os.stat` on any path: **allowed** (see gaps).

## Lint rules

- Imports only from `math`, `numpy`, `statistics`, `dataclasses`, `typing`, `datetime`,
  `collections`, `itertools`, `functools`; no relative imports and no `import *`.
- No name, attribute, argument, keyword or definition starting with `__` except `__init__`.
- No use (call or reference) of `open`, `eval`, `exec`, `compile`, `__import__`, `input`,
  `globals`, `locals`, `vars`, `getattr`, `setattr`, `delattr`, `breakpoint`.
- No numpy file/network/foreign-code helpers, matched as attribute names on any object and
  as names imported from numpy: `load`, `save`, `savez`, `savez_compressed`, `fromfile`,
  `tofile`, `memmap`, `open_memmap`, `genfromtxt`, `loadtxt`, `savetxt`, `fromregex`,
  `DataSource`, `ctypeslib`, `npyio`, `dump`, `f2py`, `testing`, `distutils`.
- No attribute named after a dangerous module that allowed modules re-export (for example
  `typing.sys`, `dataclasses.inspect`, `statistics.sys`, the `os`/`subprocess` reachable
  under `numpy.f2py`), nor frame/code attributes (`gi_frame`, `f_globals`, `f_back`,
  `tb_frame`, `co_code`, ...), nor `get_type_hints` / `ForwardRef` (which evaluate strings).
- Size: at most 400 lines and 30,000 characters.
- No single literal display (list, tuple, set, dict, counting everything nested inside it)
  with more than 200 elements or more than 2,000 characters of strings, and no string literal
  over 2,000 characters, so a candidate cannot carry a table of answers.
- A top-level `class Forecaster` with `fit` and `predict` must exist.

Known false positives: a candidate's own attribute or method named like a banned helper
(`self.load`, `self.save`, `self.types`, `self.testing`, `self.io`, ...) is rejected; rename it.

## What is NOT blocked

On every platform:

- **The lint and restricted builtins are not a jail.** `tests/test_sandbox.py` contains a
  candidate that recovers the real `__import__` through `().__class__.__base__.__subclasses__()`.
  The lint rejects it (double-underscore attributes), but if a future bypass of the lint
  exists, the restricted builtins will not stop it. On macOS, `sandbox-exec` is the real
  boundary; on Linux there is no OS-level boundary (below).
- Strings are not checked: `"{0.__class__}".format(x)` passes the lint. It can only produce
  text, not objects, so it does not reach a module, but it is an unchecked channel.
- The candidate sees all **train** truths (by design) and its own inputs; it can return
  anything derived from them.
- CPU: the child can use a full core (and numpy can use more if a BLAS ignores the
  `*_NUM_THREADS=1` settings) until the wall-clock or CPU limit.
- Memory is enforced by polling resident memory every 0.1 s, so a fast allocation can
  overshoot the limit for up to one poll before the kill (the test allocates 400 MB blocks
  against a 300 MB limit and is killed). Untouched virtual memory is not counted. On Linux
  `RLIMIT_AS` should also cap it (untested here).
- Timing: the candidate can measure time and the host's CPU speed.

macOS specifically:

- `file-read-metadata` is allowed everywhere, so the candidate can learn whether a path
  exists and its size and timestamps (`os.stat`), though it cannot list directories outside
  the allowlist or read file contents. Python's startup needs metadata reads; narrowing this
  was not attempted.
- The whole Python installation is readable (here `/opt/anaconda3`, including its other
  site-packages), plus numpy, `/usr/lib` and `/System`. None of this holds lab data, but it is
  more than the candidate needs.
- `sysctl-read` is allowed (hardware and OS details).
- `sandbox-exec` is deprecated by Apple; it works on this macOS version but could stop
  working in a future release. The probe detects that and drops the `sandbox_exec_*`
  protections rather than failing.

Linux specifically (falls back to process limits only; none of this has been run on Linux):

- **Network is not blocked.** A candidate that gets past the lint and restricted builtins
  can open sockets.
- **File reads are not blocked.** It can read anything the lab's user can read.
- File writes are only partly blocked: `RLIMIT_FSIZE=0` stops bytes being written to
  regular files, but files can still be created, truncated, renamed or deleted.
- Process creation is not blocked (no `RLIMIT_NPROC`, which would also count threads).
- To close these, run the child under a container, `bwrap`/`nsjail`, or at least
  `unshare -rn` (no network) — not implemented.

## Tests

`tests/test_sandbox.py` starts real child processes for every `run_candidate` call. It covers:
predictions in order; each lint rule with its message; that the sentinel placed only in eval
truths never appears in the bytes sent to the child, and that a truth-seeking candidate can do
no better than the train mean; the infinite-loop kill; the memory kill (skipped only if neither
RSS polling nor `RLIMIT_AS` works); network, file-write and file-read attempts past the lint
(`skip_lint=True`), each with a control run (`use_os_sandbox=False`) that shows the attempt
would otherwise succeed; rejection of non-finite, non-dict, non-float and empty predictions;
and one attempt-log line per failure. `skip_lint` and `use_os_sandbox` exist for those tests;
production callers should leave both at their defaults.
