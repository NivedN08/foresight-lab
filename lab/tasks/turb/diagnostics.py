"""Clear-air turbulence diagnostics from gridded upper-air fields.

Each function is small and works on numpy arrays of any matching shape.
`diagnose` applies them to a FieldSet and returns everything on the pressure
levels of the grid, in readable units:

    speed      wind speed, m/s
    vws        vertical wind shear, m/s per km
    n2         static stability N^2, 1e-4 s^-2
    ri         Richardson number N^2 / VWS^2 (dimensionless)
    deform     total horizontal deformation, 1e-5 s^-1
    div        horizontal divergence, 1e-5 s^-1
    ti1        Ellrod index TI1 = VWS x DEF, 1e-7 s^-2
    tgrad      horizontal temperature gradient, K per 100 km
"""
from __future__ import annotations

import numpy as np

G = 9.80665
R_DRY = 287.053
CP = 1004.0
KAPPA = R_DRY / CP
EARTH_R = 6.371e6


def wind_components(speed, direction_deg):
    """u (towards east) and v (towards north) from speed and the meteorological
    direction the wind blows FROM."""
    d = np.deg2rad(direction_deg)
    return -speed * np.sin(d), -speed * np.cos(d)


def potential_temperature(temp_k, p_hpa):
    return temp_k * (1000.0 / p_hpa) ** KAPPA


def layer_shear(u_lo, v_lo, z_lo, u_hi, v_hi, z_hi):
    """Magnitude of the vector wind difference over the layer thickness (s^-1).
    Thickness comes from geopotential height."""
    dz = z_hi - z_lo
    return np.hypot(u_hi - u_lo, v_hi - v_lo) / dz


def layer_n2(theta_lo, theta_hi, z_lo, z_hi):
    """Brunt-Vaisala frequency squared, g / theta * d(theta)/dz (s^-2)."""
    return G / (0.5 * (theta_lo + theta_hi)) * (theta_hi - theta_lo) / (z_hi - z_lo)


def richardson(n2, vws, floor: float = 1e-6):
    """Gradient Richardson number; shear below `floor` s^-1 is raised to it."""
    return n2 / np.maximum(vws, floor) ** 2


def horizontal_derivatives(f, lats_deg, lons_deg):
    """(df/dx, df/dy) in units of f per metre, on a regular lat-lon grid.
    The last two axes of f are (lat, lon); x is eastward, y northward."""
    phi = np.deg2rad(np.asarray(lats_deg, float))
    lam = np.deg2rad(np.asarray(lons_deg, float))
    df_dlam = np.gradient(f, lam, axis=-1)
    df_dphi = np.gradient(f, phi, axis=-2)
    dfdx = df_dlam / (EARTH_R * np.cos(phi))[:, None]
    dfdy = df_dphi / EARTH_R
    return dfdx, dfdy


def deformation_divergence(u, v, lats_deg, lons_deg):
    """(total deformation, divergence) in s^-1.
    stretching = du/dx - dv/dy, shearing = dv/dx + du/dy,
    deformation = sqrt(stretching^2 + shearing^2), divergence = du/dx + dv/dy."""
    dudx, dudy = horizontal_derivatives(u, lats_deg, lons_deg)
    dvdx, dvdy = horizontal_derivatives(v, lats_deg, lons_deg)
    return np.hypot(dudx - dvdy, dvdx + dudy), dudx + dvdy


def temperature_gradient(temp, lats_deg, lons_deg):
    dtdx, dtdy = horizontal_derivatives(temp, lats_deg, lons_deg)
    return np.hypot(dtdx, dtdy)


def ellrod_ti1(vws, deformation):
    return vws * deformation


def layers_to_levels(layer_vals, axis: int = 1):
    """Values for the n-1 layers between n levels -> values on the n levels:
    the mean of the layer above and below (one layer at the ends)."""
    a = np.moveaxis(layer_vals, axis, 0)
    out = np.empty((a.shape[0] + 1,) + a.shape[1:])
    out[0], out[-1] = a[0], a[-1]
    out[1:-1] = 0.5 * (a[:-1] + a[1:])
    return np.moveaxis(out, 0, axis)


def diagnose(fs) -> dict[str, np.ndarray]:
    """All diagnostics on the FieldSet's levels, shape (time, level, lat, lon)."""
    order = np.argsort(fs.levels)  # increasing pressure = decreasing height
    if not np.array_equal(order, np.arange(len(fs.levels))):
        raise ValueError("FieldSet levels must be ordered from the top (low pressure) down")
    u, v = wind_components(fs.speed, fs.direction)
    tk = fs.temp + 273.15
    p = fs.levels[None, :, None, None]
    theta = potential_temperature(tk, p)
    z = fs.height
    up, lo = slice(0, -1), slice(1, None)  # level k is above level k+1
    vws_layer = layer_shear(u[:, lo], v[:, lo], z[:, lo], u[:, up], v[:, up], z[:, up])
    n2_layer = layer_n2(theta[:, lo], theta[:, up], z[:, lo], z[:, up])
    vws = layers_to_levels(vws_layer)
    n2 = layers_to_levels(n2_layer)
    deform, div = deformation_divergence(u, v, fs.lats, fs.lons)
    return {
        "speed": fs.speed,
        "vws": vws * 1e3,
        "n2": n2 * 1e4,
        "ri": richardson(n2, vws),
        "deform": deform * 1e5,
        "div": div * 1e5,
        "ti1": ellrod_ti1(vws, deform) * 1e7,
        "tgrad": temperature_gradient(fs.temp, fs.lats, fs.lons) * 1e5,
    }


DIAGNOSTICS = ("speed", "vws", "n2", "ri", "deform", "div", "ti1", "tgrad")
