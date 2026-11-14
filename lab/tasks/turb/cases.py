"""Turn pilot reports into Cases.

One Case per labelled report layer. The issue time is the start of the
report's hour; the target time is the report time. The inputs are the
diagnostics interpolated to the layer's position, pressure (flight level
through the standard atmosphere) and time, built only from field values whose
recorded "available" time is at or before the issue time.

What that guarantees depends on the fields. For live forecast downloads it is
real: those values existed at the download time. For the history (Open-Meteo's
past values) the available time is a bookkeeping convention, not a fact: the
stitched history comes from model runs started at or after each valid hour,
which may have assimilated the very aircraft observations being predicted.
Cases built on history are therefore an analysis-time (hindsight-field)
comparison of indices: fair between models, because all share the same
inputs, but not evidence of forecast skill. The truth (intensity code and
class) is kept outside the Case.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from lab.core.task import Case

from . import fields as F
from .diagnostics import DIAGNOSTICS, diagnose
from .interp import fl_to_pressure, interp_points
from .pireps import label


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def fields_for_issue(chunks: list[F.FieldSet], issue: datetime, valid: list[datetime]):
    """The fields for the given valid hours as known at `issue`, or None if any
    of those hours was not available yet."""
    fs = F.merge(chunks, as_of=issue)
    if fs is None:
        return None
    want = [int(v.timestamp()) for v in valid]
    idx = [np.flatnonzero(fs.times == w) for w in want]
    if any(len(i) == 0 for i in idx):
        return None
    return fs.select(np.array([int(i[0]) for i in idx]))


def point_features(diag: dict, fs: F.FieldSet, t, p, lat, lon) -> dict[str, np.ndarray]:
    return {k: interp_points(diag[k], fs.times, fs.levels, fs.lats, fs.lons, t, p, lat, lon)
            for k in DIAGNOSTICS}


def build_cases(reports: pd.DataFrame, chunks: list[F.FieldSet], log=None):
    """Cases and truths for every report layer whose two surrounding field hours
    are recorded as available at its issue time. Returns (cases, truths, skipped)."""
    cases, truths = [], []
    seen: dict[str, int] = {}
    skipped = 0
    if reports.empty:
        return cases, truths, 0
    rep = reports.sort_values("time", kind="mergesort")
    issue_hours = rep["time"].dt.floor("h")
    for issue_ts, grp in rep.groupby(issue_hours, sort=True):
        issue = issue_ts.to_pydatetime()
        fs = fields_for_issue(chunks, issue, [issue, issue + timedelta(hours=1)])
        if fs is None:
            skipped += len(grp)
            continue
        diag = diagnose(fs)
        t = grp["time"].map(lambda x: x.timestamp()).to_numpy(float)
        p = fl_to_pressure(grp["fl"].to_numpy(float))
        feats = point_features(diag, fs, t, p, grp["lat"].to_numpy(float), grp["lon"].to_numpy(float))
        for j, (_, r) in enumerate(grp.iterrows()):
            target = r["time"].to_pydatetime()
            digest = hashlib.sha1(f"{r['raw']}|{r['source']}".encode()).hexdigest()[:8]
            layer = int(r["layer"]) if "layer" in r and r["layer"] == r["layer"] else 0
            cid = f"turb-{target:%Y%m%dT%H%M}-{r['lat']:.3f}-{r['lon']:.3f}-FL{int(r['fl'])}-{digest}-L{layer}"
            if cid in seen:  # identical rows that survived de-duplication stay distinct
                seen[cid] += 1
                cid = f"{cid}-{seen[cid]}"
            else:
                seen[cid] = 0
            inputs = {"lat": float(r["lat"]), "lon": float(r["lon"]), "fl": float(r["fl"]),
                      "pressure_hpa": float(p[j]),
                      "features": {k: float(feats[k][j]) for k in DIAGNOSTICS}}
            cases.append(Case(cid, issue, target, inputs))
            truths.append({"code": int(r["code"]), "label": label(r["code"])})
    if log:
        log(f"  built {len(cases)} cases; {skipped} reports had no fields available at their issue time")
    return cases, truths, skipped


def labelled(cases, truths):
    """Keep cases with a class (drops light-moderate and unparsed)."""
    keep = [i for i, tr in enumerate(truths) if tr["label"] == tr["label"]]
    return [cases[i] for i in keep], [truths[i] for i in keep]


def utc(ts) -> datetime:
    return pd.Timestamp(ts).to_pydatetime().astimezone(timezone.utc)
