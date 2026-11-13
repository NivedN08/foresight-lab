"""Turbulence forecasters. Each returns {"risk": float}; higher risk means
moderate-or-greater turbulence is more likely. `risk_from` maps a dict of
diagnostic arrays (any shape) to risk, so the same model scores single
reports and whole grids."""
from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy.optimize import minimize

from lab.core.task import Case


def _features(case: Case) -> dict[str, np.ndarray]:
    return {k: np.asarray(v, float) for k, v in case.inputs["features"].items()}


def _labels(truths: Sequence[dict]) -> np.ndarray:
    return np.array([t["label"] for t in truths], float)


class _Model:
    name = "base"
    units = ""

    def fit(self, cases: Sequence[Case], truths: Sequence[dict]) -> None:
        pass

    def risk_from(self, feats: dict[str, np.ndarray]) -> np.ndarray:
        raise NotImplementedError

    def predict(self, case: Case) -> dict:
        return {"risk": float(self.risk_from(_features(case)))}


class Climatology(_Model):
    """The share of reports in the training window that were moderate or greater."""
    name = "Climatology"
    units = "probability"

    def __init__(self):
        self.rate = 0.5

    def fit(self, cases, truths):
        y = _labels(truths)
        y = y[~np.isnan(y)]
        self.rate = float(y.mean()) if y.size else 0.5

    def risk_from(self, feats):
        shape = np.shape(next(iter(feats.values())))
        return np.full(shape, self.rate)


class WindSpeed(_Model):
    name = "WindSpeed"
    units = "m/s"

    def risk_from(self, feats):
        return np.asarray(feats["speed"], float)


class Shear(_Model):
    name = "Shear"
    units = "vertical wind shear, m/s per km"

    def risk_from(self, feats):
        return np.asarray(feats["vws"], float)


class Richardson(_Model):
    """Low Richardson number = high risk: risk = 1 / (1 + max(Ri, 0))."""
    name = "Richardson"
    units = "1/(1+Ri), 0..1"

    def risk_from(self, feats):
        return 1.0 / (1.0 + np.maximum(np.asarray(feats["ri"], float), 0.0))


class Ellrod1(_Model):
    name = "Ellrod1"
    units = "TI1, 1e-7 s^-2"

    def risk_from(self, feats):
        return np.asarray(feats["ti1"], float)


# ------------------------------------------------------------- logistic fit
def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 0.0) -> tuple[float, np.ndarray]:
    """Maximum-likelihood logistic regression with an optional L2 penalty on
    the slopes (not the intercept). Returns (intercept, slopes)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])

    def loss(w):
        z = Xb @ w
        nll = np.mean(np.logaddexp(0.0, z) - y * z)
        grad = Xb.T @ (1.0 / (1.0 + np.exp(-z)) - y) / n
        nll += 0.5 * l2 * np.sum(w[1:] ** 2)
        grad[1:] += l2 * w[1:]
        return nll, grad

    res = minimize(loss, np.zeros(k + 1), jac=True, method="L-BFGS-B",
                   options={"maxiter": 2000, "gtol": 1e-9})
    return float(res.x[0]), res.x[1:]


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


# Transforms used by SeedBlend: (name, function of the diagnostics dict).
BLEND_TERMS = {
    "speed": lambda f: f["speed"],
    "log_vws": lambda f: np.log(np.maximum(f["vws"], 1e-3)),
    "log_deform": lambda f: np.log(np.maximum(f["deform"], 1e-3)),
    "log_ri": lambda f: np.log(np.clip(f["ri"], 1e-2, 1e4)),
    "n2": lambda f: f["n2"],
    "div": lambda f: f["div"],
    "log_tgrad": lambda f: np.log(np.maximum(f["tgrad"], 1e-3)),
}


class SeedBlend(_Model):
    """Logistic regression on standardized (log where sensible) diagnostics.
    TI1 is not a separate term: log TI1 = log VWS + log DEF."""
    name = "SeedBlend"
    units = "probability"

    def __init__(self, l2: float = 1e-3):
        self.l2 = l2
        self.terms = list(BLEND_TERMS)
        self.mean = self.std = None
        self.intercept = 0.0
        self.coef = np.zeros(len(self.terms))
        self.n_train = 0

    def _design(self, feats) -> np.ndarray:
        cols = [np.asarray(BLEND_TERMS[t](feats), float) for t in self.terms]
        return np.stack(cols, axis=-1)

    def fit(self, cases, truths):
        y = _labels(truths)
        keep = ~np.isnan(y)
        feats = {k: np.array([c.inputs["features"][k] for c, kp in zip(cases, keep) if kp])
                 for k in cases[0].inputs["features"]}
        X = self._design(feats)
        y = y[keep]
        ok = np.all(np.isfinite(X), axis=1)
        X, y = X[ok], y[ok]
        self.mean, self.std = X.mean(axis=0), X.std(axis=0) + 1e-12
        self.intercept, self.coef = fit_logistic((X - self.mean) / self.std, y, l2=self.l2)
        self.n_train = int(len(y))

    def risk_from(self, feats):
        X = (self._design(feats) - self.mean) / self.std
        X = np.nan_to_num(X, nan=0.0)
        return sigmoid(self.intercept + X @ self.coef)

    def coefficients(self) -> dict:
        return {"intercept": round(self.intercept, 4),
                **{t: round(float(c), 4) for t, c in zip(self.terms, self.coef)}}

    def describe(self) -> str:
        lines = [f"SeedBlend logistic regression on {self.n_train} training reports "
                 f"(standardized terms; coefficient = change in log-odds per standard deviation):",
                 f"  intercept      {self.intercept:+.3f}"]
        lines += [f"  {t:<14} {c:+.3f}" for t, c in zip(self.terms, self.coef)]
        return "\n".join(lines)


def all_models() -> list[_Model]:
    return [Climatology(), WindSpeed(), Shear(), Richardson(), Ellrod1(), SeedBlend()]
