"""Flight level <-> pressure (International Standard Atmosphere) and
interpolation of gridded fields to a report's position, pressure and time."""
from __future__ import annotations

import numpy as np

FT = 0.3048
P0, T0, LAPSE = 1013.25, 288.15, 0.0065
G, R_DRY = 9.80665, 287.053
P_TROP, Z_TROP, T_TROP = 226.321, 11000.0, 216.65
_EXP = G / (R_DRY * LAPSE)  # 5.2559


def fl_to_pressure(fl):
    """Pressure (hPa) at a flight level (hundreds of feet, standard atmosphere)."""
    z = np.asarray(fl, dtype=float) * 100.0 * FT
    tropo = P0 * (1.0 - LAPSE * z / T0) ** _EXP
    strato = P_TROP * np.exp(-(z - Z_TROP) * G / (R_DRY * T_TROP))
    return np.where(z <= Z_TROP, tropo, strato)


def pressure_to_fl(p):
    p = np.asarray(p, dtype=float)
    z_tropo = T0 / LAPSE * (1.0 - (p / P0) ** (1.0 / _EXP))
    z_strato = Z_TROP - np.log(p / P_TROP) * R_DRY * T_TROP / G
    z = np.where(p >= P_TROP, z_tropo, z_strato)
    return z / FT / 100.0


def level_to_fl(p_hpa: float) -> float:
    return float(pressure_to_fl(p_hpa))


def _axis_weights(grid: np.ndarray, x: np.ndarray):
    """Index of the lower neighbour and the weight of the upper one, for an
    increasing grid. Points outside the grid are clamped to the edge."""
    x = np.clip(x, grid[0], grid[-1])
    i = np.clip(np.searchsorted(grid, x, side="right") - 1, 0, len(grid) - 2)
    w = (x - grid[i]) / (grid[i + 1] - grid[i])
    return i, w


def interp_points(arr: np.ndarray, times: np.ndarray, levels: np.ndarray, lats: np.ndarray,
                  lons: np.ndarray, t, p, lat, lon) -> np.ndarray:
    """Interpolate arr (time, level, lat, lon) to points.

    Linear in time (epoch seconds), in log pressure, in latitude and in
    longitude. Points outside the grid take the nearest edge value; callers
    decide which points are in range."""
    t, p, lat, lon = (np.atleast_1d(np.asarray(a, dtype=float)) for a in (t, p, lat, lon))
    lev_order = np.argsort(np.log(levels))
    logp = np.log(levels[lev_order])
    a = arr[:, lev_order]
    if len(times) == 1:
        ti, tw = np.zeros(t.shape, int), np.zeros(t.shape)
        a = np.concatenate([a, a], axis=0)
    else:
        ti, tw = _axis_weights(times.astype(float), t)
    li, lw = _axis_weights(logp, np.log(p))
    yi, yw = _axis_weights(lats, lat)
    xi, xw = _axis_weights(lons, lon)
    out = np.zeros(t.shape)
    for dt, wt in ((0, 1 - tw), (1, tw)):
        for dl, wl in ((0, 1 - lw), (1, lw)):
            for dy, wy in ((0, 1 - yw), (1, yw)):
                for dx, wx in ((0, 1 - xw), (1, xw)):
                    out += wt * wl * wy * wx * a[ti + dt, li + dl, yi + dy, xi + dx]
    return out


def interp_grid3(grid: np.ndarray, levels, lats, lons, p, lat, lon) -> np.ndarray:
    """Interpolate one (level, lat, lon) grid to points (no time axis)."""
    arr = np.asarray(grid, dtype=float)[None]
    return interp_points(arr, np.array([0]), np.asarray(levels, float), np.asarray(lats, float),
                         np.asarray(lons, float), np.zeros(np.shape(np.atleast_1d(p))), p, lat, lon)
