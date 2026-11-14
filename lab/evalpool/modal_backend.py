"""Evaluate candidates on Modal instead of this machine.

    pool = ModalEvalPool(problem, splits, max_containers=50)
    fut = pool.submit(source)            # Future[EvalResult], same as EvalPool
    results = pool.map(sources)

What goes where
  * The caller (this machine) lints the source first; a lint failure never
    leaves the machine.
  * The encoded cases for a set of windows (train cases, train truths, and the
    PUBLIC fields of the select/confirm cases) are uploaded once to a Modal
    Volume, under a key that is the SHA-256 of their bytes. They are built by
    `runner.prepare_cases`, which has no argument for evaluation truths.
  * Each evaluation sends only {key, source, limits} to one remote function call.
    In the container the candidate runs through the same sandbox child runner as
    on the laptop (isolated interpreter, restricted builtins, wall-clock kill,
    RSS watchdog, rlimits). The macOS `sandbox-exec` layer does not exist on
    Linux; instead the Modal function is created with `block_network=True`
    (Modal drops all outbound traffic from the container) and
    `restrict_modal_access=True` (the container cannot reach other Modal
    resources). See lab/modal_app.py and docs/modal.md.
  * The container returns predictions only. Losses are computed here, by
    `score_predictions`, from select/confirm truths that never leave this
    machine. The EvalResult is the same as the local evaluator's.

The Modal calls sit behind a small adapter (`ModalAdapter`: has_cases,
upload_cases, call). Tests use `LocalFakeAdapter`, which runs the same
container-side function (`run_remote`) in-process and records every byte sent.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Sequence

from lab.core.problem import Splits
from lab.sandbox import lint
from lab.sandbox import runner

from .evaluate import EvalResult, prepared_cases, score_predictions, source_hash
from .pool import EvalPool

APP_NAME = "foresight-lab"
EVAL_FUNCTION = "evaluate_candidate"
CASES_VOLUME = "lab-eval-cases"
CASES_MOUNT = "/cases"
REMOTE_MEMORY_MB = 2048
REMOTE_PROTECTIONS = ("modal_block_network", "modal_restrict_modal_access")

SETUP_HELP = """Modal is not set up on this machine.
  1. .venv/bin/python -m pip install modal   (or: uv pip install --python .venv/bin/python modal)
  2. .venv/bin/modal token new                (opens a browser; writes ~/.modal.toml)
  3. .venv/bin/modal deploy -m lab.modal_app  (from the repo root; creates the app "foresight-lab")
Then run the research loop with --evaluator modal. See docs/modal.md."""


class ModalSetupError(RuntimeError):
    pass


def check_modal_setup():
    """Return the imported `modal` module, or raise ModalSetupError with the steps."""
    try:
        import modal
    except ImportError as e:
        raise ModalSetupError(f"{SETUP_HELP}\n(import failed: {e})") from None
    token = os.environ.get("MODAL_TOKEN_ID")
    if not token:
        try:
            from modal.config import config
            token = config.get("token_id")
        except Exception:  # noqa: BLE001
            token = None
    if not token:
        raise ModalSetupError(f"{SETUP_HELP}\n(no Modal token: neither MODAL_TOKEN_ID nor ~/.modal.toml)")
    return modal


def cases_key(prepared: runner.PreparedCases, prefix: str = "") -> str:
    """Content key of encoded cases: 32 hex digits, optionally under one prefix
    directory (the live job uses "live/")."""
    h = hashlib.sha256(prepared.body).hexdigest()[:32]
    return f"{prefix}/{h}" if prefix else h


KEY_RE = re.compile(r"^(?:[a-z]+/)?[0-9a-f]{32}$")


# ------------------------------------------------------------- container side
def run_remote(request: dict, cases_root: str | Path, extra_protections: Sequence[str] = ()) -> dict:
    """Runs in the Modal container (or in-process for tests). Reads the encoded
    cases by key, runs the candidate through the sandbox child runner, and
    returns plain JSON-safe data. No truths of evaluation windows exist here."""
    key = str(request.get("key", ""))
    if not KEY_RE.match(key):
        return {"ok": False, "error": "crash: malformed cases key", "kind": "crash", "lint": [],
                "predictions": None, "protections": list(extra_protections), "name": None}
    root = Path(cases_root) / key
    meta = json.loads((root / "meta.json").read_text())
    body = (root / "body.bin").read_bytes()
    if hashlib.sha256(body).hexdigest()[:32] != key.rsplit("/", 1)[-1]:
        return {"ok": False, "error": "crash: encoded cases do not match their key", "kind": "crash",
                "lint": [], "predictions": None, "protections": list(extra_protections), "name": None}
    prepared = runner.PreparedCases(body, int(meta["n_train"]), int(meta["n_eval"]))
    res = runner.run_prepared(request["source"], prepared, time_limit_s=float(request["time_limit_s"]),
                              memory_limit_mb=int(request["memory_limit_mb"]))
    return {"ok": res.ok, "error": res.error, "kind": res.kind, "lint": list(res.lint),
            "predictions": res.predictions, "protections": list(res.protections) + list(extra_protections),
            "name": res.name, "duration_s": res.duration_s, "tail": (res.stdout_tail or "")[-2000:]}


# ------------------------------------------------------------------ adapters
class ModalAdapter:
    """The real thing: a deployed function and a Volume (see lab/modal_app.py)."""

    def __init__(self, app_name: str = APP_NAME, function_name: str = EVAL_FUNCTION,
                 volume_name: str = CASES_VOLUME):
        modal = check_modal_setup()
        self.fn = modal.Function.from_name(app_name, function_name)
        self.vol = modal.Volume.from_name(volume_name, create_if_missing=True)

    def has_cases(self, key: str) -> bool:
        try:
            names = {Path(e.path).name for e in self.vol.listdir(f"/{key}")}
        except Exception:  # noqa: BLE001 - not found
            return False
        return {"body.bin", "meta.json"} <= names

    def upload_cases(self, key: str, body: bytes, meta: dict) -> None:
        with self.vol.batch_upload(force=True) as batch:
            batch.put_file(io.BytesIO(body), f"/{key}/body.bin")
            batch.put_file(io.BytesIO(json.dumps(meta).encode()), f"/{key}/meta.json")

    def call(self, request: dict) -> dict:
        return self.fn.remote(request)


class VolumeMountAdapter:
    """Used INSIDE a Modal container that mounts the cases Volume (the live coast
    cycle): cases are written to the mount and committed, and each candidate is
    sent to the network-blocked `evaluate_candidate` function. Only the newest
    `keep` case sets under the same prefix are kept, so hourly uploads do not pile up."""

    def __init__(self, mount: str | Path, volume, function, keep: int = 2):
        self.mount = Path(mount)
        self.volume = volume
        self.fn = function
        self.keep = keep

    def has_cases(self, key: str) -> bool:
        return (self.mount / key / "body.bin").exists() and (self.mount / key / "meta.json").exists()

    def upload_cases(self, key: str, body: bytes, meta: dict) -> None:
        d = self.mount / key
        d.mkdir(parents=True, exist_ok=True)
        (d / "body.bin").write_bytes(body)
        (d / "meta.json").write_text(json.dumps({**meta, "uploaded_ns": time.time_ns()}))
        if "/" in key:     # prune older sets under the same prefix, oldest upload first
            def uploaded(x: Path) -> int:
                try:
                    return int(json.loads((x / "meta.json").read_text()).get("uploaded_ns", 0))
                except (OSError, ValueError):
                    return 0
            older = sorted((x for x in d.parent.iterdir() if x.is_dir() and x != d), key=uploaded)
            for x in older[: max(0, len(older) - (self.keep - 1))]:
                shutil.rmtree(x, ignore_errors=True)
        self.volume.commit()

    def call(self, request: dict) -> dict:
        return self.fn.remote(request)


class RemoteCandidateExecutor:
    """Runs candidate code somewhere else, with the signature of
    `runner.run_prepared`: (source, prepared, *, time_limit_s, memory_limit_mb,
    log_path) -> SandboxResult. The live coast cycle on Modal uses it so that
    agent-written code never executes in the container that has the network and
    the ledgers. The source is linted here first; a lint failure is never sent."""

    def __init__(self, adapter, prefix: str = "live"):
        self.adapter = adapter
        self.prefix = prefix
        self._lock = threading.Lock()

    def __call__(self, source: str, prepared: runner.PreparedCases, *, time_limit_s: float = 180,
                 memory_limit_mb: int = REMOTE_MEMORY_MB, log_path=None) -> runner.SandboxResult:
        t0 = time.monotonic()
        problems = lint(source)
        if problems:
            res = runner.SandboxResult(ok=False, predictions=None, kind="lint", lint=problems,
                                       error=f"lint: {len(problems)} problem(s); first: {problems[0]}",
                                       protections=["lint"], duration_s=time.monotonic() - t0)
            runner._log_failure(log_path, source, res)
            return res
        key = cases_key(prepared, self.prefix)
        try:
            with self._lock:
                if not self.adapter.has_cases(key):
                    self.adapter.upload_cases(key, prepared.body, {"n_train": prepared.n_train,
                                                                   "n_eval": prepared.n_eval})
            reply = self.adapter.call({"key": key, "source": source, "time_limit_s": time_limit_s,
                                       "memory_limit_mb": memory_limit_mb})
        except Exception as e:  # noqa: BLE001 - outage: this candidate fails, the caller carries on
            return runner.SandboxResult(ok=False, predictions=None, kind="crash",
                                        error=f"remote: {type(e).__name__}: {e}", protections=["lint"],
                                        duration_s=time.monotonic() - t0)
        preds = reply.get("predictions")
        ok = bool(reply.get("ok"))
        err = reply.get("error")
        if ok and (not isinstance(preds, list) or len(preds) != prepared.n_eval):
            ok, preds, err = False, None, "bad output: remote returned the wrong number of predictions"
        res = runner.SandboxResult(ok=ok, predictions=preds if ok else None, error=None if ok else err,
                                   lint=list(reply.get("lint") or []),
                                   protections=list(dict.fromkeys(["lint"] + list(reply.get("protections") or []))),
                                   kind=None if ok else (reply.get("kind") or "crash"), name=reply.get("name"),
                                   duration_s=time.monotonic() - t0)
        if not ok:
            runner._log_failure(log_path, source, res)
        return res


class LocalFakeAdapter:
    """Stands in for Modal in tests: the 'volume' is a temp directory and `call`
    runs `run_remote` in this process. Everything sent is kept in `sent`."""

    def __init__(self, root: str | Path | None = None, extra_protections: Sequence[str] = ()):
        self.root = Path(root or tempfile.mkdtemp(prefix="lab-fake-modal-"))
        self.extra_protections = tuple(extra_protections)
        self.sent: list[bytes] = []
        self.calls = 0
        self.uploads = 0

    def has_cases(self, key: str) -> bool:
        return (self.root / key / "body.bin").exists() and (self.root / key / "meta.json").exists()

    def upload_cases(self, key: str, body: bytes, meta: dict) -> None:
        self.uploads += 1
        d = self.root / key
        d.mkdir(parents=True, exist_ok=True)
        m = json.dumps(meta).encode()
        self.sent += [body, m]
        (d / "body.bin").write_bytes(body)
        (d / "meta.json").write_bytes(m)

    def call(self, request: dict) -> dict:
        self.calls += 1
        self.sent.append(json.dumps(request).encode())
        reply = run_remote(json.loads(json.dumps(request)), self.root, self.extra_protections)
        return json.loads(json.dumps(reply))     # what crosses the wire is plain JSON


# -------------------------------------------------------------------- the pool
class ModalEvalPool(EvalPool):
    """Same interface and caching as EvalPool; each evaluation is one remote call."""

    def __init__(self, problem, splits: Splits, max_containers: int = 50, *, adapter=None,
                 time_limit_s: float = 180, memory_limit_mb: int = REMOTE_MEMORY_MB, log_path=None):
        self.adapter = adapter if adapter is not None else ModalAdapter()
        super().__init__(problem, splits, max_workers=max_containers, time_limit_s=time_limit_s,
                         log_path=log_path)
        self.memory_limit_mb = memory_limit_mb
        self._uploaded: set[str] = set()
        self._upload_lock = threading.Lock()

    def _ensure_cases(self, windows: tuple[str, ...]) -> tuple[str, runner.PreparedCases]:
        prep = prepared_cases(self.splits, windows)
        key = cases_key(prep)
        with self._upload_lock:
            if key not in self._uploaded:
                if not self.adapter.has_cases(key):
                    self.adapter.upload_cases(key, prep.body, {"n_train": prep.n_train, "n_eval": prep.n_eval,
                                                               "windows": list(windows)})
                self._uploaded.add(key)
        return key, prep

    def _run(self, source: str, windows: tuple[str, ...]) -> EvalResult:
        h = source_hash(source)
        t0 = time.monotonic()
        problems = lint(source)
        if problems:      # never sent anywhere
            res = runner.SandboxResult(ok=False, predictions=None, kind="lint", lint=problems,
                                       error=f"lint: {len(problems)} problem(s); first: {problems[0]}",
                                       protections=["lint"])
            runner._log_failure(self.log_path, source, res)
            return EvalResult(ok=False, error=res.error, lint=problems, duration_s=time.monotonic() - t0,
                              protections=["lint"], source_hash=h, windows=windows)
        key, prep = self._ensure_cases(windows)
        try:
            reply = self.adapter.call({"key": key, "source": source, "time_limit_s": self.time_limit_s,
                                       "memory_limit_mb": self.memory_limit_mb})
        except Exception as e:  # noqa: BLE001 - network, Modal outage: report, do not cache
            return EvalResult(ok=False, error=f"remote: {type(e).__name__}: {e}", lint=[],
                              duration_s=time.monotonic() - t0, protections=["lint"], source_hash=h,
                              windows=windows)
        out = EvalResult(ok=bool(reply.get("ok")), error=reply.get("error"), lint=list(reply.get("lint") or []),
                         duration_s=0.0, protections=list(dict.fromkeys(reply.get("protections") or [])),
                         source_hash=h, name=reply.get("name"), windows=windows)
        if out.ok:
            preds = reply.get("predictions")
            if not isinstance(preds, list) or len(preds) != prep.n_eval:
                out.ok, out.error = False, "bad output: remote returned the wrong number of predictions"
            else:
                score_predictions(self.problem, self.splits, windows, preds, out)
        out.duration_s = time.monotonic() - t0
        return out

    def _forget_if_transient(self, key, fut) -> None:
        super()._forget_if_transient(key, fut)
        try:
            r = fut.result()
        except Exception:  # noqa: BLE001
            return
        if not r.ok and (r.error or "").startswith("remote:"):
            with self._lock:
                if self._cache.get(key) is fut:
                    del self._cache[key]
