"""The lab on Modal: candidate evaluation and the scheduled live jobs.

Deploy (see docs/modal.md for the full Saturday-morning order):

    .venv/bin/modal deploy -m lab.modal_app                    # evaluator + live functions, NO schedule
    .venv/bin/python -m lab.modal_app seed-volume              # copy ledgers, promoted, caches up (once)
    #  ... stop the laptop loops ...
    LAB_SCHEDULE=1 .venv/bin/modal deploy -m lab.modal_app     # same app, schedules on
    .venv/bin/python -m lab.modal_app status                   # records, head hash, last state time
    .venv/bin/python -m lab.modal_app pull                     # state + ledger -> runs/<task>/live/

Functions in the app "foresight-lab":

  evaluate_candidate   one candidate on prepared cases (lab/evalpool/modal_backend.py).
                       block_network=True, restrict_modal_access=True; up to 50 at once.
  coast_cycle          one coast live cycle (lab.tasks.coast.live.cycle), every 15 minutes
                       when deployed with LAB_SCHEDULE=1.
  turb_cycle           one turbulence live cycle (lab.tasks.turb.live.LiveJob.cycle), at :05
                       and :35 when deployed with LAB_SCHEDULE=1.
  live_summary         records, head hash and state time of each ledger (for `status`).

The live functions keep runs/ and cache/ on the Volume "lab-live-data" (mounted at
/data and linked into the repo root inside the container), so the hash chains
continue from the laptop's. Each live function runs with max_containers=1, so
one ledger never has two writers on Modal; the laptop loops must be stopped
before the schedule is turned on. A file /data/control/paused stops both cycles.

The live functions need the network (gauges, weather, pilot reports), so they
are NOT network-blocked. Promoted lab forecasters run inside the coast cycle
through the same sandbox child runner as on the laptop, minus the macOS
sandbox-exec layer: see docs/modal.md, "Isolation on Linux".
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import modal
except ImportError:      # the module stays importable for tests and for `--help`
    modal = None

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "foresight-lab"
DATA_VOLUME = "lab-live-data"
DATA = "/data"
TASK_DIRS = {"coast": "runs/coast/live", "turb": "runs/turb/live"}
SEED_DIRS = ["runs/coast/live", "runs/turb/live", "runs/lab/promoted", "cache/coast", "cache/turb"]
PAUSE_FILE = "control/paused"
PROMOTED_MARKER = "promoted_ran_hour.txt"
SCHEDULED = os.environ.get("LAB_SCHEDULE") == "1"


def _pins(*names: str) -> list[str]:
    """Exact versions of this machine's packages, so Modal runs what the laptop runs."""
    from importlib.metadata import version
    return [f"{n}=={version(n)}" for n in names]


# ------------------------------------------------------- container-side helpers
def link_data_dirs(data: str | Path = DATA, root: Path | None = None) -> Path:
    """Make <repo root>/runs and /cache point at the Volume. The lab's code finds
    its files relative to the repo root, so nothing else needs to change."""
    if root is None:
        import lab
        root = Path(lab.__file__).resolve().parents[1]
    for name in ("runs", "cache"):
        target = Path(data) / name
        target.mkdir(parents=True, exist_ok=True)
        link = Path(root) / name
        if link.is_symlink():
            if Path(os.readlink(link)) != target:
                raise RuntimeError(f"{link} points at {os.readlink(link)}, expected {target}")
            continue
        if link.exists():
            raise RuntimeError(f"{link} already exists in the container; refusing to shadow it")
        link.symlink_to(target, target_is_directory=True)
    return Path(root)


def paused(data: str | Path = DATA) -> bool:
    return (Path(data) / PAUSE_FILE).exists()


def run_coast_cycle(log=print) -> dict:
    """Exactly one coast live cycle with promoted forecasters, like `live once`.
    The promoted forecasters' last run hour is kept in a small file next to the
    ledger, so a fresh container does not refit them more than once an hour."""
    from lab.tasks.coast import live as L
    from lab.tasks.coast.http import Client
    from lab.tasks.coast.promote import PROMOTED_DIR
    client = Client()
    wc = L.WaterCycle(client, promoted_dir=PROMOTED_DIR, log=log)
    marker = L.LIVE_DIR / PROMOTED_MARKER
    if wc.promoted is not None and marker.exists():
        try:
            wc.promoted.ran_hour = datetime.fromisoformat(marker.read_text().strip())
        except ValueError:
            pass
    state = L.cycle(client=client, wc=wc, wave_cycle=L._wave_cycle(client, log), ledger_dir=L.LIVE_DIR, log=log)
    if wc.promoted is not None and wc.promoted.ran_hour is not None:
        marker.write_text(wc.promoted.ran_hour.isoformat())
    return {"records": state["ledger"]["records"], "verify": state["ledger"]["verify"],
            "generated_at": state["generated_at"]}


def run_turb_cycle(log=print) -> dict:
    """Exactly one turbulence live cycle, like `lab.tasks.turb.live once`."""
    from lab.core.timeutil import utcnow
    from lab.tasks.turb import fields as F
    from lab.tasks.turb import live as T
    F.ensure_backfill(F.FieldStore(), utcnow().replace(microsecond=0), log=log)
    state = T.LiveJob(T.real_clients(log), log=log).cycle()
    return {"keys": sorted(state)[:20] if isinstance(state, dict) else None}


def ledger_summary(root: Path) -> dict:
    """Records, head hash, chain check and state time of each task's ledger under `root`."""
    from lab.core.ledger import SealedLedger
    out = {}
    for task, rel in TASK_DIRS.items():
        d = Path(root) / rel
        if not (d / "sealed.jsonl").exists():
            out[task] = None
            continue
        led = SealedLedger(d)
        n, head = led.head()
        ok, checked, msg = led.verify()
        st = d / "state.json"
        gen = json.loads(st.read_text()).get("generated_at") if st.exists() else None
        out[task] = {"records": n, "head_hash": head, "verify_ok": ok, "verify_message": msg,
                     "state_generated_at": gen}
    return out


# ------------------------------------------------------------ the Modal app
if modal is not None:
    app = modal.App(APP_NAME)
    _base = modal.Image.debian_slim(python_version="3.12")
    eval_image = _base.uv_pip_install(*_pins("numpy")).add_local_python_source("lab")
    live_image = (_base.uv_pip_install(*_pins("numpy", "pandas", "scipy", "httpx"))
                  .add_local_python_source("lab"))
    cases_volume = modal.Volume.from_name("lab-eval-cases", create_if_missing=True)
    data_volume = modal.Volume.from_name(DATA_VOLUME, create_if_missing=True)

    @app.function(image=eval_image, volumes={"/cases": cases_volume}, block_network=True,
                  restrict_modal_access=True, max_containers=50, timeout=600, cpu=1.0, memory=3072)
    def evaluate_candidate(request: dict) -> dict:
        from lab.evalpool.modal_backend import CASES_MOUNT, REMOTE_PROTECTIONS, run_remote
        if not (Path(CASES_MOUNT) / request["key"] / "body.bin").exists():
            try:      # a warm container mounted the Volume before these cases were uploaded
                cases_volume.reload()
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"remote: cases {request['key']} not visible ({e})",
                        "kind": "crash", "lint": [], "predictions": None, "protections": [], "name": None}
        return run_remote(request, CASES_MOUNT, REMOTE_PROTECTIONS)

    @app.function(image=live_image, volumes={DATA: data_volume}, max_containers=1, timeout=1200,
                  cpu=2.0, memory=4096,
                  schedule=modal.Cron("*/15 * * * *") if SCHEDULED else None)
    def coast_cycle() -> dict:
        if paused():
            return {"paused": True}
        link_data_dirs()
        try:
            return run_coast_cycle()
        finally:
            data_volume.commit()

    @app.function(image=live_image, volumes={DATA: data_volume}, max_containers=1, timeout=1500,
                  cpu=2.0, memory=4096,
                  schedule=modal.Cron("5,35 * * * *") if SCHEDULED else None)
    def turb_cycle() -> dict:
        if paused():
            return {"paused": True}
        link_data_dirs()
        try:
            return run_turb_cycle()
        finally:
            data_volume.commit()

    @app.function(image=live_image, volumes={DATA: data_volume}, timeout=300)
    def live_summary() -> dict:
        data_volume.reload()
        return ledger_summary(Path(DATA))


# ------------------------------------------------------------ laptop commands
def _is_file(entry) -> bool:
    return getattr(entry.type, "name", str(entry.type)) == "FILE"


def _data_volume():
    from lab.evalpool.modal_backend import check_modal_setup
    m = check_modal_setup()
    return m.Volume.from_name(DATA_VOLUME, create_if_missing=True)


def seed_volume(vol, root: Path = ROOT, dirs=SEED_DIRS, force: bool = False, log=print) -> int:
    """Upload the laptop's ledgers, promoted forecasters and caches (once).
    Refuses if the Volume already holds a ledger, unless force=True."""
    if not force:
        for rel in TASK_DIRS.values():
            try:
                existing = [e for e in vol.listdir(f"/{rel}") if Path(e.path).name == "sealed.jsonl"]
            except Exception:  # noqa: BLE001 - not found
                existing = []
            if existing:
                raise SystemExit(f"the Volume already has /{rel}/sealed.jsonl; refusing to overwrite a "
                                 f"ledger (pass --force only if Modal has never run a cycle)")
    n = 0
    with vol.batch_upload(force=True) as batch:
        for rel in dirs:
            d = Path(root) / rel
            if not d.is_dir():
                log(f"  skip {rel}: not found")
                continue
            for f in sorted(d.rglob("*")):
                if f.is_file() and f.name != ".lock" and not f.name.endswith(".tmp"):
                    batch.put_file(str(f), "/" + str(f.relative_to(root)))
                    n += 1
    log(f"uploaded {n} files to the Volume {DATA_VOLUME}")
    return n


def pull(vol, root: Path = ROOT, tasks=tuple(TASK_DIRS), log=print) -> int:
    """Download each task's ledger and state.json from the Volume into runs/<task>/live/."""
    n = 0
    for task in tasks:
        rel = TASK_DIRS[task]
        try:
            entries = [e for e in vol.listdir(f"/{rel}", recursive=True) if _is_file(e)]
        except Exception as e:  # noqa: BLE001
            log(f"  {task}: nothing on the Volume ({e})")
            continue
        for e in entries:
            path = e.path.lstrip("/")
            if Path(path).name == ".lock":
                continue
            dest = Path(root) / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            buf = io.BytesIO()
            for chunk in vol.read_file(path):
                buf.write(chunk)
            tmp = dest.with_name(dest.name + ".pull.tmp")
            tmp.write_bytes(buf.getvalue())
            tmp.replace(dest)
            n += 1
        log(f"  {task}: {len(entries)} files -> {root / rel}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="The lab on Modal (see docs/modal.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed-volume", help="upload ledgers, promoted forecasters and caches (once)")
    s.add_argument("--force", action="store_true")
    sub.add_parser("pull", help="download state.json and ledgers into runs/<task>/live/")
    sub.add_parser("status", help="records and head hash of each ledger on Modal")
    sub.add_parser("pause", help="make the scheduled cycles skip (writes /control/paused)")
    sub.add_parser("resume", help="remove /control/paused")
    a = ap.parse_args(argv)
    from lab.evalpool.modal_backend import ModalSetupError
    try:
        vol = _data_volume()
        if a.cmd == "seed-volume":
            seed_volume(vol, force=a.force)
        elif a.cmd == "pull":
            pull(vol)
        elif a.cmd == "status":
            fn = modal.Function.from_name(APP_NAME, "live_summary")
            print(json.dumps(fn.remote(), indent=1))
        elif a.cmd == "pause":
            with vol.batch_upload(force=True) as b:
                b.put_file(io.BytesIO(datetime.now(timezone.utc).isoformat().encode()), "/" + PAUSE_FILE)
            print("paused: scheduled cycles will skip until `resume`")
        elif a.cmd == "resume":
            vol.remove_file("/" + PAUSE_FILE)
            print("resumed")
    except ModalSetupError as e:
        print(e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
