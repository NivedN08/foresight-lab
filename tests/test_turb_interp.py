import numpy as np
import pytest

from lab.tasks.turb.interp import fl_to_pressure, interp_grid3, interp_points, pressure_to_fl


def test_standard_atmosphere():
    assert float(fl_to_pressure(0)) == pytest.approx(1013.25)
    assert float(fl_to_pressure(100)) == pytest.approx(696.8, abs=0.2)
    assert float(fl_to_pressure(340)) == pytest.approx(250.0, abs=0.5)
    assert float(fl_to_pressure(390)) == pytest.approx(196.8, abs=0.3)   # above the tropopause
    fl = np.array([50.0, 180.0, 300.0, 361.0, 450.0])
    assert np.allclose(pressure_to_fl(fl_to_pressure(fl)), fl, atol=1e-6)


def test_interpolation_recovers_a_linear_field():
    times = np.array([0.0, 3600.0, 7200.0])
    levels = np.array([150.0, 200.0, 250.0, 300.0])
    lats = np.array([30.0, 32.5, 35.0])
    lons = np.array([-100.0, -97.5, -95.0, -92.5])
    T, L, Y, X = np.meshgrid(times, np.log(levels), lats, lons, indexing="ij")
    arr = 1.0 + T / 3600.0 + 2.0 * L + 0.5 * Y - 0.3 * X
    t, p, la, lo = 5000.0, 222.0, 33.1, -96.2
    expect = 1.0 + t / 3600.0 + 2.0 * np.log(p) + 0.5 * la - 0.3 * lo
    got = interp_points(arr, times, levels, lats, lons, t, p, la, lo)
    assert got[0] == pytest.approx(expect)


def test_interpolation_at_grid_points_and_clamping():
    levels = np.array([200.0, 300.0])
    lats = np.array([30.0, 35.0])
    lons = np.array([-100.0, -95.0])
    grid = np.arange(8, dtype=float).reshape(2, 2, 2)
    assert interp_grid3(grid, levels, lats, lons, 300.0, 35.0, -95.0)[0] == pytest.approx(7.0)
    # outside the grid: nearest edge value
    assert interp_grid3(grid, levels, lats, lons, 100.0, 40.0, -90.0)[0] == pytest.approx(3.0)
