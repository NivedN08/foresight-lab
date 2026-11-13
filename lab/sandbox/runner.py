"""Run one untrusted candidate in a fresh, locked-down child process.

Layers (each reported in SandboxResult.protections when active):
  lint                  static AST checks before anything runs (lint.py)
  isolated_interpreter  `python -I -S -X utf8`, minimal environment, fresh temp cwd
  restricted_builtins   candidate code runs with a guarded __import__ and without
                        open/eval/exec/getattr/... in its builtins
  wall_clock_kill       the child's process group is killed at the time limit
  rss_watchdog          the parent polls the child's resident memory and kills it
                        above the memory limit
  rlimit_*              resource limits set inside the child where the OS accepts them
  sandbox_exec_*        macOS only: the child runs under sandbox-exec with a profile
                        that denies network, file writes, and file reads outside the
                        Python installation, numpy and the temp input directory

Results come back as JSON only; nothing from the child is ever unpickled.
"""
from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from lab.core.timeutil import utcnow
from lab.sandbox.lint import ALLOWED_IMPORTS, lint

_CHILD_SOURCE = (Path(__file__).with_name("_child.py")).read_text(encoding="utf-8")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_MAX_RESULT_BYTES = 64 * 2**20
_STDERR_TAIL_BYTES = 8_000
_LOG_LOCK = threading.Lock()


@dataclass
class SandboxResult:
    ok: bool
    predictions: list[dict] | None
    error: str | None
    lint: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    protections: list[str] = field(default_factory=list)
    stdout_tail: str = ""
    kind: str | None = None  # None on success; lint|timeout|memory|crash|bad_output
    name: str | None = None  # the candidate's `name` attribute when it ran


# --------------------------------------------------------------------- encoding

def _encode(obj: Any) -> Any:
    """Turn case inputs / truths into JSON-safe data the child can decode."""
    import numpy as np

    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, np.generic):
        return _encode(obj.item())
    if isinstance(obj, _dt.datetime):
        if obj.tzinfo is None:
            raise ValueError("naive datetime in case data; lab times must be UTC-aware")
        return {"$t": "dt", "v": obj.isoformat()}
    if isinstance(obj, _dt.date):
        return {"$t": "date", "v": obj.isoformat()}
    if isinstance(obj, np.ndarray):
        if obj.dtype.kind in "biuf":
            data = obj.ravel().tolist()
        elif obj.dtype.kind in "mM":
            data = obj.view("int64").ravel().tolist()
        elif obj.dtype.kind == "U":
            data = obj.ravel().tolist()
        else:
            raise TypeError(f"cannot send numpy array of dtype {obj.dtype} to the sandbox")
        return {"$t": "nd", "v": {"dtype": obj.dtype.str, "shape": list(obj.shape), "data": data}}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"dict keys sent to the sandbox must be strings, got {type(k).__name__}")
            out[k] = _encode(v)
        return {"$t": "dict", "v": out} if "$t" in out else out
    raise TypeError(f"cannot send {type(obj).__name__} to the sandbox")


def _encode_case(case: Any) -> dict:
    lead = getattr(case, "lead_hours", None)
    if lead is None:
        lead = (case.target_time - case.issue_time).total_seconds() / 3600.0
    for t in (case.issue_time, case.target_time):
        if t.tzinfo is None:
            raise ValueError(f"case {case.case_id}: naive datetime")
    return {
        "case_id": str(case.case_id),
        "issue_time": case.issue_time.isoformat(),
        "target_time": case.target_time.isoformat(),
        "lead_hours": float(lead),
        "inputs": _encode(dict(case.inputs or {})),
    }


def build_payload(source: str, train_cases: Sequence, train_truths: Sequence[dict],
                  eval_cases: Sequence) -> bytes:
    """Exactly the bytes written to the child's input file. Eval cases are
    encoded from their public fields only; there is no truth argument for
    them, so they cannot carry one."""
    if len(train_cases) != len(train_truths):
        raise ValueError(f"{len(train_cases)} train cases but {len(train_truths)} truths")
    payload = {
        "source": source,
        "allowed_imports": sorted(ALLOWED_IMPORTS),
        "train_cases": [_encode_case(c) for c in train_cases],
        "train_truths": [_encode(dict(t)) for t in train_truths],
        "eval_cases": [_encode_case(c) for c in eval_cases],
    }
    return json.dumps(payload, allow_nan=True).encode("utf-8")


# ----------------------------------------------------------------- platform caps

def _python_real() -> str:
    return os.path.realpath(sys.executable)


def _numpy_dir() -> str | None:
    try:
        import numpy
    except ImportError:
        return None
    return os.path.realpath(os.path.dirname(numpy.__file__))


def _sb_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sandbox_profile(work_dir: str) -> str:
    reads = {
        os.path.realpath(sys.base_prefix),
        os.path.realpath(sys.prefix),
        os.path.dirname(_python_real()),
        os.path.realpath(sysconfig.get_paths()["stdlib"]),
        os.path.realpath(work_dir),
        "/usr/lib", "/System", "/private/var/db/dyld",
    }
    nd = _numpy_dir()
    if nd:
        reads.add(nd)
    subpaths = " ".join(f"(subpath {_sb_quote(p)})" for p in sorted(reads))
    return f"""(version 1)
(deny default)
(allow process-exec (literal {_sb_quote(_python_real())}))
(allow file-read* {subpaths}
  (literal "/") (literal "/dev/urandom") (literal "/dev/random") (literal "/dev/null"))
(allow file-read-metadata)
(allow sysctl-read)
(allow file-write-data (literal "/dev/null"))
"""


def _rlimits(time_limit_s: float, memory_limit_mb: int) -> dict[str, int]:
    return {
        "RLIMIT_AS": int(memory_limit_mb) * 2**20,
        "RLIMIT_CPU": int(math.ceil(time_limit_s)) + 2,
        "RLIMIT_FSIZE": 0,
        "RLIMIT_CORE": 0,
    }


@functools.lru_cache(maxsize=1)
def _probe() -> dict:
    """Find out once which protections this machine actually supports."""
    caps = {"sandbox_exec": False, "rlimits": []}
    work = tempfile.mkdtemp(prefix="lab-sbx-probe-")
    try:
        # which rlimits does the OS accept?
        code = ("import json,resource\nout=[]\n"
                "for n,v in json.loads(%r).items():\n"
                "    r=getattr(resource,n,None)\n"
                "    try:\n"
                "        h=resource.getrlimit(r)[1]\n"
                "        resource.setrlimit(r,(v,h if h!=resource.RLIM_INFINITY and h<v else v)); out.append(n)\n"
                "    except Exception: pass\n"
                "print(json.dumps(out))") % json.dumps(_rlimits(60, 1024))
        try:
            p = subprocess.run([_python_real(), "-I", "-S", "-c", code], capture_output=True,
                               text=True, timeout=30, env=_child_env(work))
            caps["rlimits"] = json.loads(p.stdout.strip() or "[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        # does sandbox-exec exist and still let python + numpy start?
        if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
            pkgs = _make_pkgs_dir(work)
            code = "import sys; sys.path.insert(0, sys.argv[1]); import numpy; print('ok')"
            try:
                p = subprocess.run([SANDBOX_EXEC, "-p", _sandbox_profile(work), _python_real(),
                                    "-I", "-S", "-c", code, pkgs], capture_output=True, text=True,
                                   timeout=60, env=_child_env(work), cwd=work)
                caps["sandbox_exec"] = p.returncode == 0 and p.stdout.strip() == "ok"
            except (OSError, subprocess.SubprocessError):
                pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return caps


def _child_env(work_dir: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": work_dir,
        "TMPDIR": work_dir,
        "LC_ALL": "C.UTF-8" if sys.platform != "darwin" else "en_US.UTF-8",
    }
    for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[k] = "1"
    return env


def _make_pkgs_dir(work_dir: str) -> str:
    pkgs = os.path.join(work_dir, "pkgs")
    os.makedirs(pkgs, exist_ok=True)
    nd = _numpy_dir()
    if nd and not os.path.exists(os.path.join(pkgs, "numpy")):
        os.symlink(nd, os.path.join(pkgs, "numpy"))
    return pkgs


def _rss_mb(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    try:
        out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return int(out) / 1024.0 if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------------------ logging

def _log_failure(log_path, source: str, result: SandboxResult) -> None:
    if not log_path:
        return
    entry = {
        "time": utcnow().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "source_sha256": hashlib.sha256(source.encode("utf-8", "replace")).hexdigest(),
        "kind": result.kind,
        "error": result.error,
        "lint": result.lint,
        "duration_s": round(result.duration_s, 3),
        "protections": result.protections,
        "name": result.name,
    }
    line = json.dumps(entry) + "\n"
    with _LOG_LOCK:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)


# ------------------------------------------------------------------------- runner

def _validate_predictions(preds: Any, n_eval: int) -> str | None:
    if not isinstance(preds, list):
        return "child returned no prediction list"
    if len(preds) != n_eval:
        return f"got {len(preds)} predictions for {n_eval} eval cases"
    for i, p in enumerate(preds):
        if not isinstance(p, dict) or not p:
            return f"prediction {i} is not a non-empty dict"
        for k, v in p.items():
            if not isinstance(k, str):
                return f"prediction {i} has a non-string key"
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return f"prediction {i} field {k!r} is {type(v).__name__}, expected a float"
            if not math.isfinite(v):
                return f"prediction {i} field {k!r} is not finite ({v})"
    return None


def run_candidate(source: str, train_cases: Sequence, train_truths: Sequence[dict],
                  eval_cases: Sequence, *, time_limit_s: float = 120,
                  memory_limit_mb: int = 1024, log_path: str | os.PathLike | None = None,
                  skip_lint: bool = False, use_os_sandbox: bool = True) -> SandboxResult:
    """Lint, then fit and predict in a fresh child process.

    `skip_lint` and `use_os_sandbox=False` exist only so tests can show what
    the run-time layers catch on their own; production callers leave them.
    """
    t0 = time.monotonic()
    protections: list[str] = []

    if not skip_lint:
        problems = lint(source)
        protections.append("lint")
        if problems:
            res = SandboxResult(ok=False, predictions=None, kind="lint",
                                error=f"lint: {len(problems)} problem(s); first: {problems[0]}",
                                lint=problems, duration_s=time.monotonic() - t0,
                                protections=protections)
            _log_failure(log_path, source, res)
            return res

    caps = _probe()
    payload = build_payload(source, train_cases, train_truths, eval_cases)
    work = tempfile.mkdtemp(prefix="lab-sbx-")
    try:
        payload_path = os.path.join(work, "input.json")
        with open(payload_path, "wb") as f:
            f.write(payload)
        pkgs = _make_pkgs_dir(work)
        limits = _rlimits(time_limit_s, memory_limit_mb)
        cmd = [_python_real(), "-I", "-S", "-X", "utf8", "-c", _CHILD_SOURCE,
               payload_path, pkgs, json.dumps(limits)]
        protections += ["isolated_interpreter", "restricted_builtins", "wall_clock_kill", "rss_watchdog"]
        protections += [r.lower() for r in caps["rlimits"]]
        if use_os_sandbox and caps["sandbox_exec"]:
            cmd = [SANDBOX_EXEC, "-p", _sandbox_profile(work)] + cmd
            protections += ["sandbox_exec_no_network", "sandbox_exec_no_file_write",
                            "sandbox_exec_read_allowlist"]

        res = _run_child(cmd, work, time_limit_s, memory_limit_mb, len(eval_cases))
        res.protections = protections
        res.duration_s = time.monotonic() - t0
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not res.ok:
        _log_failure(log_path, source, res)
    return res


def _run_child(cmd, work, time_limit_s, memory_limit_mb, n_eval) -> SandboxResult:
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=work, env=_child_env(work),
                            start_new_session=True, close_fds=True)
    out_chunks: list[bytes] = []
    out_size = [0]
    err_tail = bytearray()

    def read_out():
        while True:
            b = proc.stdout.read(65536)
            if not b:
                break
            out_size[0] += len(b)
            if out_size[0] <= _MAX_RESULT_BYTES:
                out_chunks.append(b)

    def read_err():
        while True:
            b = proc.stderr.read(4096)
            if not b:
                break
            err_tail.extend(b)
            if len(err_tail) > 4 * _STDERR_TAIL_BYTES:
                del err_tail[:-_STDERR_TAIL_BYTES]

    readers = [threading.Thread(target=read_out, daemon=True),
               threading.Thread(target=read_err, daemon=True)]
    for t in readers:
        t.start()

    start = time.monotonic()
    killed: str | None = None
    peak_rss = 0.0
    next_rss = 0.0
    while proc.poll() is None:
        now = time.monotonic()
        if now - start > time_limit_s:
            killed = "timeout"
        elif now >= next_rss:
            rss = _rss_mb(proc.pid)
            next_rss = now + 0.1
            if rss is not None:
                peak_rss = max(peak_rss, rss)
                if rss > memory_limit_mb:
                    killed = "memory"
        if out_size[0] > _MAX_RESULT_BYTES:
            killed = "bad_output"
        if killed:
            _kill(proc)
            break
        time.sleep(0.02)
    proc.wait()
    for t in readers:
        t.join(timeout=5)

    stderr_text = bytes(err_tail[-_STDERR_TAIL_BYTES:]).decode("utf-8", "replace")
    tail = "\n".join(stderr_text.rstrip().splitlines()[-30:])

    def fail(kind, error, name=None):
        return SandboxResult(ok=False, predictions=None, error=error, kind=kind,
                             stdout_tail=tail, name=name)

    if killed == "timeout":
        return fail("timeout", f"timeout: killed after {time_limit_s:g}s wall clock")
    if killed == "memory":
        return fail("memory", f"memory: resident memory {peak_rss:.0f} MB exceeded the {memory_limit_mb} MB limit")
    if killed == "bad_output":
        return fail("bad_output", f"bad output: child wrote more than {_MAX_RESULT_BYTES} bytes of results")

    raw = b"".join(out_chunks)
    try:
        msg = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        msg = None
    if not isinstance(msg, dict):
        rc = proc.returncode
        how = f"signal {-rc}" if rc is not None and rc < 0 else f"exit code {rc}"
        if rc is not None and rc < 0 and -rc in (9, 24):  # SIGKILL / SIGXCPU from rlimits
            return fail("timeout" if -rc == 24 else "crash",
                        f"crash: child killed by {how} (resource limit)\n{tail[-1500:]}")
        if "MemoryError" in tail:
            return fail("memory", f"memory: child ran out of memory ({how})\n{tail[-1500:]}")
        return fail("crash", f"crash: child exited with {how} and no result\n{tail[-1500:]}")

    if not msg.get("ok"):
        kind = msg.get("kind") if msg.get("kind") in ("memory", "bad_output", "crash") else "crash"
        stage = msg.get("stage", "?")
        prefix = {"bad_output": "bad output", "memory": "memory", "crash": "crash"}[kind]
        return fail(kind, f"{prefix} in {stage}: {msg.get('error')}\n{msg.get('traceback', '')}".rstrip(),
                    name=msg.get("name"))

    preds = msg.get("predictions")
    problem = _validate_predictions(preds, n_eval)
    if problem:
        return fail("bad_output", f"bad output: {problem}", name=msg.get("name"))
    return SandboxResult(ok=True, predictions=[{k: float(v) for k, v in p.items()} for p in preds],
                         error=None, stdout_tail=tail, name=msg.get("name"))


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
