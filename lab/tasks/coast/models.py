"""Forecasters for the water level above the tide table (residual, metres).

Each model reads only `case.inputs`. Leads are fitted separately because the
memory of a surge decays with time.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Sequence

import numpy as np

from lab.core.task import Case


def _by_lead(cases: Sequence[Case], truths: Sequence[dict]):
    groups: dict[int, list[tuple[Case, float]]] = defaultdict(list)
    for c, t in zip(cases, truths):
        groups[int(c.inputs["lead_h"])].append((c, float(t["residual_m"])))
    return groups


class TideTable:
    """What the official tide table implies: no departure from the prediction."""
    name = "TideTable"

    def fit(self, cases, truths):
        pass

    def predict(self, case: Case) -> dict:
        return {"residual_m": 0.0}


class Persistence:
    """The current departure from the tide table stays as it is."""
    name = "Persistence"

    def fit(self, cases, truths):
        pass

    def predict(self, case: Case) -> dict:
        return {"residual_m": float(case.inputs["r_now"])}


class DampedPersistence:
    """a_h * current residual + (1 - a_h) * the station's 72-hour mean residual.
    a_h is fitted per lead by least squares on (truth - mean) ~ a (now - mean)."""
    name = "DampedPersistence"

    def __init__(self):
        self.a: dict[int, float] = {}

    def fit(self, cases, truths):
        for lead, rows in _by_lead(cases, truths).items():
            x = np.array([c.inputs["r_now"] - c.inputs["r_mean"] for c, _ in rows])
            y = np.array([t - c.inputs["r_mean"] for c, t in rows])
            self.a[lead] = float(x @ y / (x @ x)) if x @ x > 0 else 1.0

    def predict(self, case: Case) -> dict:
        a = self.a.get(int(case.inputs["lead_h"]), 1.0)
        m = case.inputs["r_mean"]
        return {"residual_m": float(m + a * (case.inputs["r_now"] - m))}


class SeedPhysics:
    """Damped persistence plus three physical terms, fitted per lead:

        residual(target) = mean + a (now - mean)
                           + b * (forecast pressure change, hPa)      inverse barometer
                           + c * (residual change over the last 3 h)   trend
                           + w_x[s] * tau_x + w_y[s] * tau_y           wind setup, per station
                           + d

    tau = |U| * U is the forecast 10 m wind stress proxy (m^2/s^2) averaged over
    the lead window; each station gets its own (w_x, w_y), which is the same as
    fitting an onshore direction and a strength. Physics says b is about
    -1 cm/hPa (sea level rises as pressure falls). A gauge-only version of the
    model (no weather terms) is fitted alongside and used when the weather
    forecast is missing.
    """
    name = "SeedPhysics"

    def __init__(self, ridge: float = 1e-5):
        self.ridge = ridge
        self.coef: dict[int, dict] = {}
        self.coef_gauge: dict[int, dict] = {}
        self.stations: list[str] = []

    @staticmethod
    def _base(c: Case) -> list[float]:
        i = c.inputs
        return [i["r_now"] - i["r_mean"], i["trend3"], 1.0]

    def _design(self, rows, weather: bool):
        if not weather:
            X = np.array([self._base(c) for c, _ in rows])
        else:
            ns = len(self.stations)
            pos = {s: k for k, s in enumerate(self.stations)}
            X = np.zeros((len(rows), 4 + 2 * ns))
            for r, (c, _) in enumerate(rows):
                i = c.inputs
                X[r, :4] = [i["r_now"] - i["r_mean"], i["dp_hpa"], i["trend3"], 1.0]
                k = pos.get(i["station"])
                if k is not None:
                    X[r, 4 + 2 * k] = i["taux"]
                    X[r, 5 + 2 * k] = i["tauy"]
        y = np.array([t - c.inputs["r_mean"] for c, t in rows])
        return X, y

    def _solve(self, X, y):
        scale = np.sqrt((X ** 2).mean(axis=0)) + 1e-12
        Xs = X / scale
        # a tiny ridge on standardized columns only keeps empty station columns solvable
        A = Xs.T @ Xs + self.ridge * len(y) * np.eye(X.shape[1])
        beta = np.linalg.solve(A, Xs.T @ y)
        return beta / scale

    def fit(self, cases, truths):
        self.stations = sorted({c.inputs["station"] for c in cases})
        for lead, rows in _by_lead(cases, truths).items():
            Xg, yg = self._design(rows, weather=False)
            bg = self._solve(Xg, yg)
            self.coef_gauge[lead] = {"a": bg[0], "trend": bg[1], "intercept": bg[2]}
            wrows = [(c, t) for c, t in rows if c.inputs["has_weather"]]
            if len(wrows) >= 50:
                X, y = self._design(wrows, weather=True)
                b = self._solve(X, y)
                wind = {s: (float(b[4 + 2 * k]), float(b[5 + 2 * k])) for k, s in enumerate(self.stations)}
                self.coef[lead] = {"a": b[0], "pressure": b[1], "trend": b[2], "intercept": b[3], "wind": wind}

    def predict(self, case: Case) -> dict:
        i = case.inputs
        lead = int(i["lead_h"])
        m = i["r_mean"]
        k = self.coef.get(lead)
        if i["has_weather"] and k is not None:
            wx, wy = k["wind"].get(i["station"], (0.0, 0.0))
            y = (k["a"] * (i["r_now"] - m) + k["pressure"] * i["dp_hpa"] + k["trend"] * i["trend3"]
                 + k["intercept"] + wx * i["taux"] + wy * i["tauy"])
        else:
            g = self.coef_gauge.get(lead, {"a": 1.0, "trend": 0.0, "intercept": 0.0})
            y = g["a"] * (i["r_now"] - m) + g["trend"] * i["trend3"] + g["intercept"]
        return {"residual_m": float(m + y)}

    def describe(self) -> list[str]:
        """Fitted coefficients in readable units."""
        out = []
        for lead in sorted(self.coef):
            k = self.coef[lead]
            w = np.array(list(k["wind"].values()))
            mag = np.hypot(w[:, 0], w[:, 1]) * 100 if len(w) else np.array([0.0])
            out.append(
                f"lead {lead:>2}h: a={k['a']:.3f}  pressure={k['pressure'] * 100:+.2f} cm/hPa  "
                f"trend={k['trend']:+.3f} (per m of 3-h change)  intercept={k['intercept'] * 100:+.2f} cm  "
                f"wind setup strength median {np.median(mag):.3f} cm per m^2/s^2 (max {mag.max():.3f})")
        return out

    def wind_directions(self, lead: int) -> dict[str, tuple[float, float]]:
        """Per station: (compass direction the wind blows TOWARD that raises water most,
        strength in cm per m^2/s^2)."""
        k = self.coef.get(lead)
        if not k:
            return {}
        return {s: (float(np.degrees(np.arctan2(wx, wy)) % 360), float(np.hypot(wx, wy) * 100))
                for s, (wx, wy) in k["wind"].items()}


def all_models():
    return [TideTable(), Persistence(), DampedPersistence(), SeedPhysics()]
