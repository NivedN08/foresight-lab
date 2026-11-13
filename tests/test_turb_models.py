import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from lab.core.task import Case
from lab.tasks.turb.gairmet import parse_gairmets, point_in_polygon, risk_at, snapshot_for
from lab.tasks.turb.models import (Climatology, Ellrod1, Richardson, SeedBlend, Shear, WindSpeed,
                                   fit_logistic, sigmoid)

FIX = Path(__file__).parent / "fixtures" / "turb"
T0 = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def test_logistic_fit_recovers_known_coefficients():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(40000, 3))
    w0, w = -0.5, np.array([1.0, -2.0, 0.5])
    y = (rng.random(40000) < sigmoid(w0 + X @ w)).astype(float)
    b, coef = fit_logistic(X, y)
    assert b == pytest.approx(w0, abs=0.06)
    assert np.allclose(coef, w, atol=0.07)


def _case(i, **feats):
    base = dict(speed=30.0, vws=4.0, n2=1.2, ri=8.0, deform=4.0, div=0.0, ti1=1.6, tgrad=0.5)
    base.update(feats)
    return Case(f"c{i}", T0, T0, {"features": base})


def test_single_diagnostic_models_rank_the_right_way():
    calm, rough = _case(0), _case(1, speed=60.0, vws=12.0, ri=0.5, ti1=9.0)
    for m in (WindSpeed(), Shear(), Richardson(), Ellrod1()):
        assert m.predict(rough)["risk"] > m.predict(calm)["risk"], m.name
    assert Richardson().predict(_case(2, ri=-3.0))["risk"] == 1.0   # unstable: maximal risk


def test_climatology_and_seedblend_fit():
    rng = np.random.default_rng(0)
    cases, truths = [], []
    for i in range(3000):
        vws = float(rng.lognormal(1.3, 0.5))
        p = sigmoid(-2.0 + 0.4 * vws)
        cases.append(_case(i, vws=vws, ti1=vws * 0.4, ri=12.0 / vws ** 2 * 10,
                           speed=float(rng.normal(30, 8)), tgrad=float(rng.lognormal(-0.7, 0.4))))
        truths.append({"code": 4, "label": float(rng.random() < p)})
    clim = Climatology()
    clim.fit(cases, truths)
    assert clim.rate == pytest.approx(np.mean([t["label"] for t in truths]))
    blend = SeedBlend()
    blend.fit(cases, truths)
    assert blend.coefficients()["log_vws"] > 0.3
    r = blend.predict(_case(9999, vws=15.0, ri=0.5))["risk"]
    assert 0 < r < 1 and r > blend.predict(_case(9998, vws=1.0, ri=120.0))["risk"]
    assert "intercept" in blend.describe()


def test_gairmet_polygons_and_point_in_polygon():
    polys = parse_gairmets(json.loads((FIX / "gairmet_sample.json").read_text()))
    assert polys and all(p["coords"] for p in polys)
    square = [(30, -100), (30, -90), (40, -90), (40, -100), (30, -100)]
    assert point_in_polygon(35, -95, square)
    assert not point_in_polygon(45, -95, square)
    one = [{"valid_time": "2026-11-13T12:00:00Z", "base_fl": 180, "top_fl": 340, "coords": square}]
    assert risk_at(one, 35, -95, 300)[()] == 1.0
    assert risk_at(one, 35, -95, 380)[()] == 0.0      # above the altitude band
    assert snapshot_for(one, datetime(2026, 11, 13, 13, tzinfo=timezone.utc)) == one
    assert snapshot_for(one, datetime(2026, 11, 13, 15, tzinfo=timezone.utc)) is None
