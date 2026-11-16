#!/usr/bin/env python3
"""Write compact JSON snapshots of foresight-lab's real data for the display page.

Reads (never writes) the live state files, the sealed ledgers and their grades, the gap and
correction files when they exist, the research runs, the experiment reports and the map
geometry, all under --root. Writes only into web/public/data/ next to this file.

    python3 web/build_data.py [--root PATH] [--leads 1]

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "public" / "data"
DEFAULT_ROOT = Path("/Users/nivednandakumar/Downloads/Ai research hacks")

# Map extents (degrees). The sky covers the turbulence grid with a margin; the coast covers the
# West Coast gauges and buoys with a margin.
SKY_BOX = (-130.0, 21.0, -62.0, 53.0)
COAST_BOX = (-128.5, 31.0, -115.5, 50.0)

missing: list[str] = []
sources: list[dict] = []
ROOT: Path | None = None  # set in main(); paths on the page are shown relative to it


def shown(path: Path) -> str:
    """The path as the page shows it: relative to the checkout, so no home folder is published."""
    try:
        return str(path.resolve().relative_to(ROOT)) if ROOT else path.name
    except ValueError:
        return path.name


# ---------------------------------------------------------------- helpers

def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def minute(s: str | None) -> int | None:
    """Minutes since the Unix epoch, the page's time unit."""
    t = parse_time(s)
    return None if t is None else int(t.timestamp() // 60)


def note_source(path: Path) -> None:
    st = path.stat()
    sources.append({
        "path": shown(path),
        "bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def read_json(path: Path):
    if not path.exists():
        missing.append(shown(path))
        return None
    note_source(path)
    with open(path) as fh:
        return json.load(fh)


def read_jsonl(path: Path, required: bool = True) -> list[dict]:
    if not path.exists():
        if required:
            missing.append(shown(path))
        return []
    note_source(path)
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def r(x, nd=3):
    return None if x is None else round(float(x), nd)


def write(name: str, data) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return len(text)


# ---------------------------------------------------------------- gaps and corrections

def gaps_from_records(gap_lines: list[dict]) -> list[dict]:
    """Recorded gaps (gaps.jsonl), each with the counts of its latest follow-up line."""
    base = [g for g in gap_lines if "follow_up_of" not in g]
    for g in base:
        for f in gap_lines:
            fu = f.get("follow_up_of")
            if fu and fu.get("task") == g.get("task") and fu.get("from") == g.get("from"):
                for k in ("graded_late", "unrecoverable"):
                    if k in f:
                        g[k] = f[k]
                g["updated_at"] = f.get("recorded_at")
    return base


def gaps_from_seal_times(times: list[str], threshold_h: float, task: str) -> list[dict]:
    """Stretches with nothing sealed, found by scanning the ledger's seal times. Same rule as the
    live jobs' gap record: more than `threshold_h` hours between consecutive seals."""
    ts = sorted({t for t in times if t})
    out = []
    for a, b in zip(ts, ts[1:]):
        h = (parse_time(b) - parse_time(a)).total_seconds() / 3600
        if h > threshold_h:
            out.append({"task": task, "from": a, "to": b, "hours": round(h, 2)})
    return out


def normalise_gaps(gs: list[dict], origin: str) -> list[dict]:
    out = []
    for g in gs:
        out.append({
            "task": g.get("task"),
            "from": g.get("from"),
            "to": g.get("to"),
            "from_min": minute(g.get("from")),
            "to_min": minute(g.get("to")),
            "hours": g.get("hours"),
            "cause": g.get("cause"),
            "due_while_away": g.get("due_while_away"),
            "graded_late": g.get("graded_late"),
            "unrecoverable": g.get("unrecoverable"),
            "recorded_at": g.get("recorded_at"),
            "origin": origin,
        })
    return out


def correction_lines(state: dict | None, live: Path) -> tuple[list[dict], str | None]:
    """Corrected grades, from state['regrades'] or a regrades file beside the ledger, if any.
    A correction is appended beside the ledger; the original grade is never rewritten."""
    if state and isinstance(state.get("regrades"), list) and state["regrades"]:
        return [x for x in state["regrades"] if isinstance(x, dict)], "state.json"
    if state and isinstance(state.get("regrades"), dict):
        d = state["regrades"]
        for key in ("records", "changes", "grades", "items"):
            if isinstance(d.get(key), list) and d[key]:
                return [x for x in d[key] if isinstance(x, dict)], "state.json"
    for name in ("regrades.jsonl", "regraded.jsonl", "corrections.jsonl"):
        p = live / name
        if p.exists():
            return read_jsonl(p), shown(p)
    return [], None


def correction_time(x: dict) -> str | None:
    return next((x.get(k) for k in ("recorded_at", "regraded_at", "corrected_at", "graded_at", "time") if x.get(k)), None)


def corrections(lines: list[dict], src: str | None) -> dict:
    items = []
    for x in lines:
        before = (x.get("replaces") or {}).get("n")
        after = (x.get("truth") or {}).get("n")
        when = correction_time(x)
        items.append({"seq": x.get("seq"), "time": when, "time_min": minute(when), "before_n": before, "after_n": after})
    return {
        "source": src,
        "count": len(items),
        "reasons": [[k, v] for k, v in Counter(x.get("reason") or "no reason given" for x in lines).most_common()],
        "replaced_graded_at": [[k, v] for k, v in Counter((x.get("replaces") or {}).get("graded_at") for x in lines if x.get("replaces")).most_common()],
        "items": items,
    }


# ---------------------------------------------------------------- ledger timeline

def ledger_events(sealed: list[dict], grades: list[dict]) -> dict:
    """Seal and grade events per minute: [[minute, count], ...]."""
    s = Counter(minute(x.get("sealed_at")) for x in sealed)
    g = Counter(minute(x.get("graded_at")) for x in grades)
    s.pop(None, None)
    g.pop(None, None)
    return {"seals": sorted([k, v] for k, v in s.items()), "grades": sorted([k, v] for k, v in g.items())}


def first_cycle_after(gaps: list[dict], grades: list[dict], zero_key=None) -> list[dict]:
    """Grades written in the first grading cycle after each gap ended."""
    out = []
    times = sorted({x.get("graded_at") for x in grades if x.get("graded_at")})
    for gp in gaps:
        end = gp.get("to")
        after = [t for t in times if end and t >= end]
        if not after:
            continue
        t0 = after[0]
        batch = [x for x in grades if x.get("graded_at") == t0]
        row = {"gap_from": gp.get("from"), "graded_at": t0, "graded_at_min": minute(t0), "grades": len(batch)}
        if zero_key:
            row["with_no_reports"] = sum(1 for x in batch if zero_key(x))
        out.append(row)
    return out


def after_gap_with_fixes(rows: list[dict], grades: list[dict], fixes: dict) -> list[dict]:
    """How many of each first-after-gap batch were later corrected, and how many still have no reports."""
    for row in rows:
        batch = [g for g in grades if g.get("graded_at") == row["graded_at"]]
        fixed = [fixes[g["seq"]] for g in batch if g.get("seq") in fixes]
        row["corrected"] = len(fixed)
        row["no_reports_after_correction"] = sum(
            1 for g in batch if (((fixes.get(g.get("seq")) or g).get("truth") or {}).get("n") or 0) == 0)
    return rows


# ---------------------------------------------------------------- coast

def build_coast(root: Path, leads: set[int]) -> dict | None:
    live = root / "runs" / "coast" / "live"
    state = read_json(live / "state.json")
    sealed = read_jsonl(live / "sealed.jsonl")
    grades = read_jsonl(live / "grades.jsonl")
    if state is None:
        return None
    by_seq = {g["seq"]: g for g in grades if "seq" in g}

    water = state.get("water") or {}
    waves = state.get("waves") or {}
    w_models = list(water.get("models") or [])
    v_models = list(waves.get("models") or [])
    for x in sealed:
        m = x.get("model")
        if x.get("task") == "coast_water" and m not in w_models:
            w_models.append(m)
        if x.get("task") == "coast_waves" and m not in v_models:
            v_models.append(m)

    # History for the scene: every sealed forecast at the chosen leads, with its grade.
    # Row: [issued, sealed, target, model index, forecast cm, truth cm, error cm, graded, seq, hash, lead]
    hist: dict[str, list] = defaultdict(list)
    for x in sealed:
        lead = (x.get("meta") or {}).get("lead_h")
        if lead not in leads:
            continue
        task = x.get("task")
        meta = x.get("meta") or {}
        pred = x.get("prediction") or {}
        g = by_seq.get(x.get("seq"))
        truth = (g or {}).get("truth") or {}
        if task == "coast_water":
            key = "w:" + str(meta.get("station"))
            fc = pred.get("residual_m")
            tv = truth.get("residual_m")
            err = truth.get("abs_error_cm")
            mi = w_models.index(x.get("model"))
        elif task == "coast_waves":
            key = "v:" + str(meta.get("buoy"))
            fc = pred.get("wave_height_m")
            tv = truth.get("wave_height_m")
            err = truth.get("abs_error_cm")
            mi = v_models.index(x.get("model"))
        else:
            continue
        hist[key].append([
            minute(x.get("issued_at")), minute(x.get("sealed_at")), minute(x.get("target_time")), mi,
            None if fc is None else round(fc * 100, 1), None if tv is None else round(tv * 100, 1),
            r(err, 2), minute((g or {}).get("graded_at")), x.get("seq"), (x.get("hash") or "")[:16], lead,
        ])

    stations = []
    for s in water.get("stations") or []:
        stations.append({
            "id": s.get("id"), "name": s.get("name"), "lat": s.get("lat"), "lon": s.get("lon"),
            "latest_obs_time": s.get("latest_obs_time"), "current_residual_m": s.get("current_residual_m"),
            "hist": sorted(hist.get("w:" + str(s.get("id")), []), key=lambda row: (row[1], row[3])),
        })
    buoys = []
    for b in waves.get("buoys") or []:
        buoys.append({
            "id": b.get("id"), "name": b.get("name"), "lat": b.get("lat"), "lon": b.get("lon"),
            "latest_obs_time": b.get("latest_obs_time"), "latest_wave_height_m": b.get("latest_wave_height_m"),
            "latest_dominant_period_s": b.get("latest_dominant_period_s"),
            "hist": sorted(hist.get("v:" + str(b.get("id")), []), key=lambda row: (row[1], row[3])),
        })

    gap_lines = read_jsonl(live / "gaps.jsonl", required=False)
    if state.get("gaps"):
        gaps = normalise_gaps(state["gaps"], "recorded")
    elif gap_lines:
        gaps = normalise_gaps(gaps_from_records(gap_lines), "recorded")
    else:
        # Water seals once per target hour, so the job's own rule is more than two hours.
        gaps = normalise_gaps(gaps_from_seal_times([x.get("sealed_at") for x in sealed], 2.0, "coast"), "derived")

    promoted = []
    for p in water.get("promoted") or []:
        st = p.get("status") or {}
        promoted.append({
            "name": p.get("name"), "model": p.get("model"), "run_id": p.get("run_id"), "generation": p.get("generation"),
            "select_loss": p.get("select_loss"), "confirm_loss": p.get("confirm_loss"), "promoted_at": p.get("promoted_at"),
            "source_sha256": p.get("source_sha256"),
            "status": {"last_run": st.get("last_run"), "ok": st.get("ok"), "error": st.get("error"), "sealed": st.get("sealed")},
        })

    bt = water.get("backtest") or {}
    btw = state.get("backtest_waves") or {}
    return {
        "generated_at": state.get("generated_at"),
        "ledger": state.get("ledger"),
        "cycle": {k: (state.get("cycle") or {}).get(k) for k in ("now", "sealed_this_cycle", "graded_this_cycle", "wave_sealed_this_cycle", "wave_graded_this_cycle", "seconds")},
        "leads": sorted(leads),
        "water": {
            "quantity": water.get("quantity"), "leads_h": water.get("leads_h"), "models": w_models,
            "listed_models": water.get("models"), "promoted": promoted, "fit": water.get("fit"),
            "scoreboard": water.get("scoreboard"), "scoreboard_paired": water.get("scoreboard_paired"),
            "stations": stations, "stale": len(water.get("stale") or []),
            "backtest": {"days": bt.get("days"), "unit": bt.get("unit"), "table": bt.get("table"), "generated_at": bt.get("generated_at")},
        },
        "waves": {
            "quantity": waves.get("quantity"), "leads_h": waves.get("leads_h"), "models": v_models,
            "listed_models": waves.get("models"), "scoreboard": waves.get("scoreboard"), "buoys": buoys,
            "offshore": [{"id": o.get("id"), "latest_wave_height_m": o.get("latest_wave_height_m")} for o in waves.get("offshore") or []],
            "backtest": {"days": btw.get("days"), "unit": btw.get("unit"), "table": btw.get("table"), "generated_at": btw.get("generated_at")},
        },
        "alerts": [{k: a.get(k) for k in ("event", "area", "severity", "onset", "ends", "sender")} for a in (state.get("alerts") or [])][:20],
        "gaps": gaps,
        "first_cycle_after_gap": first_cycle_after(gaps, grades),
        "corrections": corrections(*correction_lines(state, live)),
        "events": ledger_events(sealed, grades),
        "tasks": dict(Counter(x.get("task") for x in sealed)),
        "first_seal": min((x.get("sealed_at") for x in sealed if x.get("sealed_at")), default=None),
        "last_seal": max((x.get("sealed_at") for x in sealed if x.get("sealed_at")), default=None),
    }


# ---------------------------------------------------------------- turbulence

def nearest_level(fl: float, levels: list[int]) -> int:
    return min(range(len(levels)), key=lambda i: abs(levels[i] - fl))


def build_turb(root: Path) -> dict | None:
    live = root / "runs" / "turb" / "live"
    state = read_json(live / "state.json")
    sealed = read_jsonl(live / "sealed.jsonl")
    grades = read_jsonl(live / "grades.jsonl")
    if state is None:
        return None
    by_seq = {g["seq"]: g for g in grades if "seq" in g}
    grid = state.get("grid") or {}

    models = sorted({x.get("model") for x in sealed if x.get("model")})
    featured = "SeedBlend" if "SeedBlend" in models else (models[0] if models else None)

    corr_lines, corr_src = correction_lines(state, live)
    corr_by_seq = {x.get("seq"): x for x in corr_lines if x.get("seq") is not None}

    # One sealed grid per cycle for the scene: the featured model's nearest valid hour.
    per_cycle: dict[str, dict] = {}
    for x in sealed:
        if x.get("model") != featured:
            continue
        meta = x.get("meta") or {}
        off = meta.get("valid_hour_offset", meta.get("lead_hours"))
        key = x.get("issued_at")
        cur = per_cycle.get(key)
        cur_off = None if cur is None else cur["_off"]
        if cur is None or (off is not None and (cur_off is None or off < cur_off)):
            per_cycle[key] = {"_off": off, "rec": x}
    cycles = []
    for key in sorted(per_cycle):
        x = per_cycle[key]["rec"]
        pred = x.get("prediction") or {}
        risk = pred.get("risk") or []
        flat = []
        for lev in risk:
            for row in lev:
                for v in row:
                    flat.append(-1 if v is None else int(round(float(v) * 1000)))
        g = by_seq.get(x.get("seq"))
        truth = (g or {}).get("truth") or {}
        fix = corr_by_seq.get(x.get("seq"))
        corrected = None
        if fix:
            # The scene shows the corrected reports and says so; the first grade is kept beside them.
            corrected = {"at": minute(correction_time(fix)), "before_n": (fix.get("replaces") or {}).get("n"),
                         "before_coverage": truth.get("coverage"), "reason": fix.get("reason")}
            truth = fix.get("truth") or truth
        reps = [[r(p.get("lat"), 2), r(p.get("lon"), 2), p.get("fl"), p.get("code"), minute(p.get("time"))] for p in truth.get("reports") or []]
        lead_min = None
        st, tt = parse_time(x.get("sealed_at")), parse_time(x.get("target_time"))
        if st and tt:
            lead_min = round((tt - st).total_seconds() / 60, 1)
        cycles.append({
            "seq": x.get("seq"), "hash": (x.get("hash") or "")[:16], "issued": minute(x.get("issued_at")),
            "sealed": minute(x.get("sealed_at")), "valid": minute(x.get("target_time")), "offset": per_cycle[key]["_off"],
            "lead_min": lead_min, "risk": flat,
            "graded": minute((g or {}).get("graded_at")), "n_reports": truth.get("n"),
            "coverage": truth.get("coverage"), "report_sources": truth.get("sources"), "reports": reps,
            "corrected": corrected,
        })

    gap_lines = read_jsonl(live / "gaps.jsonl", required=False)
    if state.get("gaps"):
        gaps = normalise_gaps(state["gaps"], "recorded")
    elif gap_lines:
        gaps = normalise_gaps(gaps_from_records(gap_lines), "recorded")
    else:
        # Turbulence seals every 30 minutes; the job's rule is more than two intervals.
        gaps = normalise_gaps(gaps_from_seal_times([x.get("sealed_at") for x in sealed], 1.0, "turb"), "derived")

    reports = []
    for p in state.get("reports_12h") or []:
        gr = p.get("graded") or {}
        reports.append({
            "time": p.get("time"), "lat": p.get("lat"), "lon": p.get("lon"), "fl": p.get("fl"), "code": p.get("code"),
            "label": p.get("label"), "raw": (p.get("raw") or "")[:140],
            "graded": {m: {k: v.get(k) for k in ("risk", "percentile", "outcome", "lead_minutes", "seq")} for m, v in gr.items()} if gr else None,
        })

    bt = state.get("backtest") or {}
    return {
        "generated_at": state.get("generated_at"),
        "ledger": state.get("ledger"),
        "grid": {k: grid.get(k) for k in ("lats", "lons", "levels_hpa", "flight_levels", "resolution_deg")},
        "issued_at": state.get("issued_at"),
        "risk_units": state.get("risk_units"),
        "models": models,
        "featured": featured,
        "scoreboard": state.get("scoreboard"),
        "notes": state.get("notes"),
        "backtest": {k: bt.get(k) for k in ("generated_at", "days", "labelled_cases", "positives", "base_rate", "table", "brier", "verdict", "test")},
        "reports_12h": reports,
        "cycles": cycles,
        "gaps": gaps,
        "first_cycle_after_gap": after_gap_with_fixes(first_cycle_after(gaps, grades, zero_key=lambda g: ((g.get("truth") or {}).get("n") or 0) == 0), grades, corr_by_seq),
        "corrections": corrections(corr_lines, corr_src),
        "events": ledger_events(sealed, grades),
        "first_seal": min((x.get("sealed_at") for x in sealed if x.get("sealed_at")), default=None),
        "last_seal": max((x.get("sealed_at") for x in sealed if x.get("sealed_at")), default=None),
    }


# ---------------------------------------------------------------- research

def first_sentence(s: str, n: int = 160) -> str:
    s = " ".join((s or "").split())
    head = s.split(": ", 1)[0] if ": " in s[:120] else s
    return head if len(head) <= n else head[: n - 1].rsplit(" ", 1)[0] + "…"


def build_research(root: Path) -> dict:
    runs_out = []
    lab = root / "runs" / "lab"
    for summary_path in sorted(lab.glob("*/*/summary.json")):
        run_dir = summary_path.parent
        summary = read_json(summary_path) or {}
        findings = read_jsonl(run_dir / "findings.jsonl", required=False)
        events = read_jsonl(run_dir / "events.jsonl", required=False)
        kinds = Counter(e.get("event") for e in events)
        adopted = [{"generation": e.get("generation"), "hash": e.get("hash"), "time": e.get("time"),
                    "confirm": (e.get("confirm") or {}).get("mean_a")} for e in events if e.get("event") == "new_incumbent"]
        proposals = []
        for f in findings:
            proposals.append({
                "generation": f.get("generation"), "idea": first_sentence(f.get("claim", "")), "origin": f.get("origin"),
                "accepted": f.get("accepted"), "select_delta": r(f.get("select_delta"), 4), "confirm_delta": r(f.get("confirm_delta"), 4),
                "source_title": f.get("source_title"), "time": f.get("time"),
            })
        runs_out.append({
            "problem": run_dir.parent.name, "run_id": summary.get("run_id", run_dir.name), "status": summary.get("status"),
            "started_at": summary.get("started_at"), "updated_at": summary.get("updated_at"),
            "generations_done": summary.get("generations_done"), "proposals_per_generation": summary.get("proposals_per_generation"),
            "incumbent": summary.get("incumbent"),
            "curve": [{k: c.get(k) for k in ("generation", "select", "confirm", "accepted", "proposals", "cost_usd")} for c in summary.get("curve") or []],
            "adopted": adopted, "proposals": proposals,
            "counts": {"evaluations": kinds.get("evaluation", 0), "decisions": kinds.get("decision", 0), "lint_failed": kinds.get("lint_failed", 0),
                       "new_incumbent": kinds.get("new_incumbent", 0), "cost_cap": kinds.get("cost_cap", 0)},
        })

    promoted = []
    for p in sorted((lab / "promoted").glob("*/*.json")):
        d = read_json(p) or {}
        promoted.append({k: d.get(k) for k in ("name", "model", "run_id", "run_dir", "generation", "select_loss", "confirm_loss", "promoted_at", "source_sha256")} | {"problem": p.parent.name})

    # Experiment matrices: every report, and for each claim number the most recent one.
    exps = []
    for rp in sorted((root / "runs" / "exp").glob("*/report.json")):
        d = read_json(rp) or {}
        arms = []
        for a in d.get("arms") or []:
            arms.append({
                "arm": a.get("arm"), "planned": a.get("planned"), "finished": a.get("finished"), "seeds": a.get("seeds"),
                "confirm": a.get("confirm"), "proposals": a.get("proposals"), "accepted": a.get("accepted"),
                "became_incumbent": a.get("became_incumbent"), "cost_usd": (a.get("cost_usd") or {}).get("mean"),
            })
        pr = d.get("paired") or {}
        ep = pr.get("equal_proposals") or {}
        board = d.get("board") or []
        search = d.get("search") or []
        exps.append({
            "matrix": d.get("matrix", rp.parent.name), "problem": d.get("problem"), "modified": datetime.fromtimestamp(rp.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "planned_runs": d.get("planned_runs"), "runs_finished": d.get("runs_finished"), "partial": d.get("partial"),
            "skipped": d.get("skipped"), "target": d.get("target"), "seed_confirm": (d.get("seed_confirm") or {}).get("value"),
            "arms": arms,
            "paired": {"a": pr.get("a"), "b": pr.get("b"), "seeds": pr.get("seeds"),
                       "a_wins": pr.get("a_wins", ep.get("a_wins")), "b_wins": pr.get("b_wins", ep.get("b_wins")),
                       "mean_diff": pr.get("mean_diff", ep.get("mean_diff")), "test": pr.get("test", ep.get("test"))},
            "board": board, "search": [{k: s.get(k) for k in ("arm", "backend", "searches", "failed")} for s in search],
        })
    latest: dict[str, dict] = {}
    for e in exps:
        name = e["matrix"] or ""
        claim = name.split("_", 1)[0] if name.startswith("claim") else name
        if claim not in latest or e["modified"] > latest[claim]["modified"]:
            latest[claim] = e
    for e in exps:
        name = e["matrix"] or ""
        claim = name.split("_", 1)[0] if name.startswith("claim") else name
        e["claim"] = claim
        e["current"] = latest.get(claim) is e

    # The one-look test window, every look.
    looks = []
    for fp in sorted((lab / "final_test").glob("*.json")):
        d = read_json(fp) or {}
        scores = {}
        for k, v in (d.get("scores") or {}).items():
            ov = v.get("overall") or {}
            scores[k] = {"mean": ov.get("mean"), "low": ov.get("low"), "high": ov.get("high"), "n": ov.get("n"),
                         "by_lead": {lk: lv.get("mean") for lk, lv in (v.get("by_lead") or {}).items()}}
        looks.append({"time": d.get("time"), "problem": d.get("problem"), "test_window": d.get("test_window"),
                      "best_baseline": d.get("best_baseline"), "scores": scores,
                      "paired": d.get("paired_vs_best_baseline"), "candidates": d.get("candidates")})

    return {"runs": runs_out, "promoted": promoted, "experiments": exps, "final_test": looks}


# ---------------------------------------------------------------- geometry

def decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def ring_coords(ring: list[int], arcs) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in ring:
        a = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
        pts.extend(a if not pts else a[1:])
    return pts


def in_box(pts, box, pad=1.0) -> bool:
    x0, y0, x1, y1 = box
    return any(x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad for x, y in pts)


def simplify(pts, step: float) -> list[list[float]]:
    out: list[list[float]] = []
    for x, y in pts:
        p = [round(round(x / step) * step, 3), round(round(y / step) * step, 3)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def clip_ring(pts, box):
    """Sutherland-Hodgman clip of a closed ring to an axis-aligned box."""
    x0, y0, x1, y1 = box
    edges = [
        (lambda p: p[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
        (lambda p: p[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
        (lambda p: p[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
        (lambda p: p[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1)),
    ]
    out = list(pts)
    for inside, cut in edges:
        if not out:
            break
        src, out = out, []
        prev = src[-1]
        for cur in src:
            if inside(cur):
                if not inside(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cut(prev, cur))
            prev = cur
    return out


def land_rings(topo: dict, box, step: float) -> list[list[list[float]]]:
    arcs = decode_arcs(topo)
    rings = []
    x0, y0, x1, y1 = box
    clip_box = (x0 - 0.5, y0 - 0.5, x1 + 0.5, y1 + 0.5)
    for obj in topo["objects"].values():
        geoms = obj.get("geometries") or [obj]
        for g in geoms:
            polys = g.get("arcs") or []
            if g.get("type") == "Polygon":
                polys = [polys]
            for poly in polys:
                for ring in poly:
                    pts = ring_coords(ring, arcs)
                    if in_box(pts, box):
                        s = simplify(clip_ring(pts, clip_box), step)
                        if len(s) >= 4:
                            rings.append(s)
    return rings


def state_borders(topo: dict, box, step: float) -> list[list[list[float]]]:
    """Arcs shared by two states: the interior borders."""
    arcs = decode_arcs(topo)
    use = Counter()
    for g in topo["objects"]["states"]["geometries"]:
        polys = g.get("arcs") or []
        if g.get("type") == "Polygon":
            polys = [polys]
        for poly in polys:
            for ring in poly:
                for i in ring:
                    use[i if i >= 0 else ~i] += 1
    lines = []
    for i, n in use.items():
        if n > 1 and in_box(arcs[i], box, 0):
            s = simplify(arcs[i], step)
            if len(s) >= 2:
                lines.append(s)
    return lines


def build_geo(root: Path) -> dict | None:
    geo = root / "dashboard" / "geo"
    l50 = read_json(geo / "land-50m.json")
    l10 = read_json(geo / "land-10m.json")
    st = read_json(geo / "states-10m.json")
    if l50 is None:
        return None
    return {
        "source": "Natural Earth land and US states, from the project's dashboard/geo",
        "sky_box": SKY_BOX,
        "coast_box": COAST_BOX,
        "sky_land": land_rings(l50, SKY_BOX, 0.1),
        "sky_borders": state_borders(st, SKY_BOX, 0.1) if st else [],
        "coast_land": land_rings(l10 or l50, COAST_BOX, 0.03),
        "coast_borders": state_borders(st, COAST_BOX, 0.03) if st else [],
    }


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="project checkout that holds runs/ and dashboard/geo (read only)")
    ap.add_argument("--leads", default="1", help="coast forecast leads (hours) kept for the scene, comma separated")
    args = ap.parse_args(argv)
    global ROOT
    root = args.root.resolve()
    ROOT = root
    if not (root / "runs").exists():
        print(f"no runs/ under {root}", file=sys.stderr)
        return 1
    leads = {int(x) for x in args.leads.split(",") if x.strip()}

    sizes = {}
    coast = build_coast(root, leads)
    turb = build_turb(root)
    research = build_research(root)
    geo = build_geo(root)
    if coast is not None:
        sizes["coast.json"] = write("coast.json", coast)
    if turb is not None:
        sizes["turb.json"] = write("turb.json", turb)
    sizes["research.json"] = write("research.json", research)
    if geo is not None:
        sizes["geo.json"] = write("geo.json", geo)
    meta = {
        "snapshot_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": root.name,
        "files": sizes,
        "sources": sources,
        "missing": missing,
        "coast_leads": sorted(leads),
    }
    write("meta.json", meta)
    for k, v in sizes.items():
        print(f"{k:14s} {v / 1024:8.1f} KB")
    if missing:
        print("missing:", *missing, sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
