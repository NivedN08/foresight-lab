import numpy as np
import pytest

from lab.tasks.turb import fields as F
from lab.tasks.turb.diagnostics import (CP, EARTH_R, G, R_DRY, deformation_divergence, diagnose,
                                        layer_n2, layer_shear, potential_temperature, richardson,
                                        wind_components)


def test_wind_components_meteorological_direction():
    u, v = wind_components(np.array([10.0, 10.0]), np.array([270.0, 180.0]))
    assert u[0] == pytest.approx(10.0) and v[0] == pytest.approx(0.0, abs=1e-12)  # from the west
    assert u[1] == pytest.approx(0.0, abs=1e-12) and v[1] == pytest.approx(10.0)  # from the south


def test_pure_vertical_shear_gives_known_shear():
    s = 0.004  # 4 m/s per km
    z_lo, z_hi = 9000.0, 10500.0
    assert layer_shear(s * z_lo, 0.0, z_lo, s * z_hi, 0.0, z_hi) == pytest.approx(s)
    # rotated: the same shear split between u and v
    c, d = np.cos(0.6), np.sin(0.6)
    assert layer_shear(s * z_lo * c, s * z_lo * d, z_lo, s * z_hi * c, s * z_hi * d, z_hi) == pytest.approx(s)


def test_deformation_2a_and_zero_divergence():
    a = 2e-5
    lats = np.round(np.arange(39.0, 41.0001, 0.1), 4)
    lons = np.round(np.arange(-101.0, -98.9999, 0.1), 4)
    phi, lam = np.meshgrid(np.deg2rad(lats), np.deg2rad(lons), indexing="ij")
    phi0, lam0 = np.deg2rad(40.0), np.deg2rad(-100.0)
    x = EARTH_R * np.cos(phi) * (lam - lam0)
    y = EARTH_R * (phi - phi0)
    u, v = a * x, -a * y
    deform, div = deformation_divergence(u, v, lats, lons)
    assert np.allclose(div, 0.0, atol=1e-12)
    j = int(np.argmin(np.abs(lons + 100.0)))
    assert np.allclose(deform[:, j], 2 * a, rtol=1e-9)        # exact on the central meridian
    assert np.allclose(deform, 2 * a, rtol=0.03)               # close everywhere nearby


def test_isothermal_layer_stability():
    T = 220.0
    p_lo, p_hi = 250.0, 245.0
    z_lo = 10000.0
    z_hi = z_lo + R_DRY * T / G * np.log(p_lo / p_hi)  # hydrostatic thickness
    n2 = layer_n2(potential_temperature(T, p_lo), potential_temperature(T, p_hi), z_lo, z_hi)
    assert n2 == pytest.approx(G ** 2 / (CP * T), rel=1e-3)


def test_richardson_number():
    assert richardson(1e-4, 0.005) == pytest.approx(4.0)
    assert richardson(1e-4, 0.0) == pytest.approx(1e-4 / 1e-12)  # shear floor, no division by zero


def _column_fieldset(speed_fn, temp_k=None):
    """One time, uniform horizontal fields; heights from the standard atmosphere."""
    from lab.tasks.turb.interp import pressure_to_fl
    levels = F.LEVELS
    z = pressure_to_fl(levels) * 100 * 0.3048
    shape = (1, len(levels), 3, 4)
    height = np.broadcast_to(z[None, :, None, None], shape).copy()
    temp = (np.full(shape, temp_k) if temp_k is not None else
            np.broadcast_to(np.maximum(288.15 - 0.0065 * z, 216.65)[None, :, None, None], shape)) - 273.15
    return F.FieldSet(times=np.array([0]), avail=np.array([0]), levels=levels,
                      lats=np.array([39.0, 40.0, 41.0]), lons=np.array([-102.0, -101.0, -100.0, -99.0]),
                      speed=speed_fn(height), direction=np.full(shape, 270.0), temp=temp, height=height)


def test_diagnose_on_analytic_column():
    s = 0.003
    fs = _column_fieldset(lambda z: s * z, temp_k=220.0)
    d = diagnose(fs)
    assert np.allclose(d["vws"], s * 1e3)                      # m/s per km
    assert np.allclose(d["deform"], 0.0, atol=1e-9)           # no horizontal variation
    assert np.allclose(d["div"], 0.0, atol=1e-9)
    assert np.allclose(d["ti1"], 0.0, atol=1e-9)
    # isothermal 220 K: N^2 = g^2 / (cp T); heights here are not hydrostatic, so only the sign and size
    assert np.all(d["n2"] > 0)
    assert np.allclose(d["ri"], d["n2"] * 1e-4 / (s ** 2))
