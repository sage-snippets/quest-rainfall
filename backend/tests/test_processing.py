"""Independent numerical tests of the precipitation processing.

Expected values are derived analytically or by hand. They are not produced by
the code under test.
"""

from pathlib import Path

import eccodes
import numpy as np
import pytest

from app.processing.precip import (
    DataInconsistencyError, GribValidationError, TpField, area_weighted_stats, interval,
    normalise_grid, packing_quantum_m, point_value, read_tp, subset, validate_meta,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ifs_tp_20261115_00z_steps_0_6_12.grib2"

LATS = np.array([10.0, 0.0, -10.0])
LONS = np.array([-10.0, 0.0, 10.0, 20.0])


def field(step: int, mm, quantum_mm: float = 0.1, run=(20261115, 0)) -> TpField:
    v = np.asarray(mm, dtype=float)
    return TpField(run[0], run[1], 0, step, LATS, LONS, v, quantum_mm, {})


# ------------------------------------------------------------------ interval accumulation

def test_interval_is_difference_of_cumulative_fields():
    a = field(6, [[0, 1, 2, 3], [4, 5, 6, 7], [0, 0, 0, 0]])
    b = field(12, [[1, 1, 5, 3], [4, 9, 6, 17], [2.5, 0, 0, 0]])
    iv = interval(a, b)
    np.testing.assert_allclose(iv.values_mm, [[1, 0, 3, 0], [0, 4, 0, 10], [2.5, 0, 0, 0]])
    assert (iv.start_step, iv.end_step) == (6, 12)
    assert iv.n_clamped == 0


def test_successive_intervals_sum_to_total_accumulation():
    c = [field(s, np.full((3, 4), x)) for s, x in [(0, 0.0), (3, 1.5), (6, 4.0), (12, 10.25)]]
    parts = sum(interval(p, q).values_mm for p, q in zip(c, c[1:]))
    np.testing.assert_allclose(parts, c[-1].values_mm - c[0].values_mm)


def test_packing_noise_negatives_clamped_and_counted():
    a = field(6, np.full((3, 4), 5.0), quantum_mm=0.25)
    b_vals = np.full((3, 4), 5.0)
    b_vals[0, 0] = 5.0 - 0.25   # exactly one quantum lower: harmless
    b_vals[1, 1] = 5.0 - 0.1
    iv = interval(a, field(12, b_vals, quantum_mm=0.25))
    assert iv.n_clamped == 2
    assert iv.values_mm.min() == 0.0
    assert iv.min_raw_mm == pytest.approx(-0.25)


def test_tolerance_uses_coarser_of_the_two_fields():
    a = field(6, np.full((3, 4), 5.0), quantum_mm=0.06)
    b_vals = np.full((3, 4), 5.0); b_vals[2, 3] = 4.8          # -0.2 mm
    interval(a, field(12, b_vals, quantum_mm=0.25))            # ok: within 0.25
    with pytest.raises(DataInconsistencyError):
        interval(a, field(12, b_vals, quantum_mm=0.06))        # not ok: beyond 0.06


def test_decrease_beyond_packing_precision_raises():
    a = field(6, np.full((3, 4), 5.0), quantum_mm=0.25)
    b_vals = np.full((3, 4), 5.0); b_vals[0, 2] = 4.0          # -1 mm: impossible
    with pytest.raises(DataInconsistencyError, match="lat=10.0, lon=10.0"):
        interval(a, field(12, b_vals, quantum_mm=0.25))


def test_interval_rejects_wrong_order_and_mixed_runs():
    a, b = field(6, np.zeros((3, 4))), field(12, np.zeros((3, 4)))
    with pytest.raises(ValueError):
        interval(b, a)
    with pytest.raises(ValueError):
        interval(a, field(12, np.zeros((3, 4)), run=(20261115, 1200)))


def test_missing_values_propagate_and_are_not_clamped():
    a_vals = np.full((3, 4), 1.0); a_vals[0, 0] = np.nan
    b_vals = np.full((3, 4), 3.0); b_vals[2, 2] = np.nan
    iv = interval(field(6, a_vals), field(12, b_vals))
    assert np.isnan(iv.values_mm[0, 0]) and np.isnan(iv.values_mm[2, 2])
    assert np.nansum(iv.values_mm) == pytest.approx(2.0 * 10)
    assert iv.n_clamped == 0


# ------------------------------------------------------------------ units & packing

def test_packing_quantum():
    assert packing_quantum_m({"bitsPerValue": 12, "binaryScaleFactor": -13, "decimalScaleFactor": 0}) == 2.0 ** -13
    assert packing_quantum_m({"bitsPerValue": 16, "binaryScaleFactor": 2, "decimalScaleFactor": 3}) == pytest.approx(0.004)
    # constant field (e.g. tp at step 0) is exact regardless of scale factors
    assert packing_quantum_m({"bitsPerValue": 0, "binaryScaleFactor": 0, "decimalScaleFactor": 0}) == 0.0


def test_units_converted_from_metres_to_mm():
    fields = read_tp(FIXTURE)
    raw = {}
    with FIXTURE.open("rb") as f:
        while (g := eccodes.codes_grib_new_from_file(f)) is not None:
            raw[eccodes.codes_get(g, "endStep")] = eccodes.codes_get_values(g).max()
            eccodes.codes_release(g)
    for fl in fields:
        assert np.nanmax(fl.values_mm) == pytest.approx(raw[fl.end_step] * 1000.0)
    assert fields[1].quantum_mm == pytest.approx(2.0 ** -13 * 1000)   # 0.1221 mm


# ------------------------------------------------------------------ metadata validation

GOOD_META = {
    "shortName": "tp", "units": "m", "stepType": "accum", "stepRange": "0-6", "startStep": 0,
    "stepUnits": 1, "gridType": "regular_ll", "Ni": 1440, "Nj": 721, "jPointsAreConsecutive": 0,
    "iDirectionIncrementInDegrees": 0.25, "jDirectionIncrementInDegrees": 0.25,
    "latitudeOfFirstGridPointInDegrees": 90.0, "latitudeOfLastGridPointInDegrees": -90.0,
}


def test_validate_meta_accepts_ifs_tp():
    validate_meta(GOOD_META)


@pytest.mark.parametrize("key,bad", [
    ("shortName", "cp"), ("units", "kg m**-2 s**-1"), ("units", "mm"), ("stepType", "instant"), ("startStep", 6),
    ("stepUnits", 0), ("gridType", "reduced_gg"), ("jPointsAreConsecutive", 1), ("Ni", 1439),
])
def test_validate_meta_rejects(key, bad):
    with pytest.raises(GribValidationError):
        validate_meta({**GOOD_META, key: bad})


# ------------------------------------------------------------------ grid orientation

def test_longitude_normalisation_0_360_to_pm180():
    lons = np.arange(0.0, 360.0, 90.0)              # 0, 90, 180, 270
    lats = np.array([-45.0, 45.0])                  # ascending (south-first)
    vals = np.array([[1, 2, 3, 4], [5, 6, 7, 8]], dtype=float)  # value encodes (lat, lon)
    v, la, lo = normalise_grid(vals, lats, lons)
    np.testing.assert_array_equal(lo, [-180, -90, 0, 90])
    np.testing.assert_array_equal(la, [45, -45])
    # lon 270 -> -90 ; lat 45 row first
    np.testing.assert_array_equal(v, [[7, 8, 5, 6], [3, 4, 1, 2]])


def _synthetic_grib(tmp_path: Path, *, south_first: bool, missing_cell: tuple[int, int] | None) -> Path:
    """Clone the real fixture's tp message and overwrite geometry/values with a known pattern."""
    with FIXTURE.open("rb") as f:
        base = eccodes.codes_grib_new_from_file(f)
        while eccodes.codes_get(base, "endStep") != 6:
            eccodes.codes_release(base); base = eccodes.codes_grib_new_from_file(f)
    g = eccodes.codes_clone(base); eccodes.codes_release(base)
    ni, nj = 1440, 721
    eccodes.codes_set(g, "packingType", "grid_simple")
    eccodes.codes_set(g, "bitsPerValue", 24)
    if south_first:
        eccodes.codes_set(g, "jScansPositively", 1)
        eccodes.codes_set(g, "latitudeOfFirstGridPointInDegrees", -90.0)
        eccodes.codes_set(g, "latitudeOfLastGridPointInDegrees", 90.0)
    eccodes.codes_set(g, "longitudeOfFirstGridPointInDegrees", 0.0)
    eccodes.codes_set(g, "longitudeOfLastGridPointInDegrees", 359.75)
    native_lat = (-90 + 0.25 * np.arange(nj)) if south_first else (90 - 0.25 * np.arange(nj))
    native_lon = 0.25 * np.arange(ni)
    # value (m) = (lat + 90)/1000 + lon_0_360 / 1e6  -> mm = lat + 90 + lon/1000
    vals = (native_lat[:, None] + 90) / 1000 + native_lon[None, :] / 1e6
    if missing_cell:
        eccodes.codes_set(g, "bitmapPresent", 1)
        eccodes.codes_set(g, "missingValue", 9999)
        vals[missing_cell] = 9999
    eccodes.codes_set_values(g, vals.ravel())
    out = tmp_path / "synthetic.grib2"
    with out.open("wb") as f:
        eccodes.codes_write(g, f)
    eccodes.codes_release(g)
    return out


@pytest.mark.parametrize("south_first", [False, True])
def test_read_tp_orientation_from_header(tmp_path, south_first):
    (fl,) = read_tp(_synthetic_grib(tmp_path, south_first=south_first, missing_cell=None))
    assert fl.lats[0] == 90 and fl.lats[-1] == -90
    assert fl.lons[0] == -180 and fl.lons[-1] == 179.75
    expected = (fl.lats[:, None] + 90) + (fl.lons[None, :] % 360) / 1000
    np.testing.assert_allclose(fl.values_mm, expected, atol=1e-3)
    # spot check a named location: 12.75N 77.5E (near Bengaluru)
    j, i = np.flatnonzero(fl.lats == 12.75)[0], np.flatnonzero(fl.lons == 77.5)[0]
    assert fl.values_mm[j, i] == pytest.approx(102.75 + 0.0775, abs=1e-3)


def test_read_tp_bitmap_missing_values_become_nan(tmp_path):
    # native north-first row 0 = 90N; col 4 = lon 1.0
    (fl,) = read_tp(_synthetic_grib(tmp_path, south_first=False, missing_cell=(0, 4)))
    j, i = 0, np.flatnonzero(fl.lons == 1.0)[0]
    assert np.isnan(fl.values_mm[j, i])
    assert np.isnan(fl.values_mm).sum() == 1


# ------------------------------------------------------------------ regional filtering & stats

def test_subset_simple_box():
    v = np.arange(12.0).reshape(3, 4)
    s, la, lo = subset(v, LATS, LONS, (0, -5, 10, 15))
    np.testing.assert_array_equal(la, [10, 0])
    np.testing.assert_array_equal(lo, [0, 10])
    np.testing.assert_array_equal(s, [[1, 2], [5, 6]])


def test_subset_across_antimeridian_keeps_west_to_east_order():
    lons = np.array([-179.5, -90.0, 0.0, 90.0, 179.5])
    v = np.arange(5.0)[None, :]
    s, _, lo = subset(v, np.array([0.0]), lons, (170, -1, -170, 1))
    np.testing.assert_array_equal(lo, [179.5, -179.5])
    np.testing.assert_array_equal(s, [[4, 0]])


@pytest.mark.parametrize("bbox", [(0, 20, 10, 10), (0, -95, 10, 0), (10, 0, 10, 5), (0, 50, 10, 60), (200, 0, 210, 5)])
def test_subset_invalid_bbox(bbox):
    with pytest.raises(ValueError):
        subset(np.zeros((3, 4)), LATS, LONS, bbox)


def test_area_weighted_mean_analytic():
    # Rows at 0 and 60 deg: weights 1 and 0.5. Field 0 at equator, 3 at 60 -> mean = 1.5/1.5 = 1.0
    v = np.array([[0.0, 0.0], [3.0, 3.0]])
    st = area_weighted_stats(v, np.array([0.0, 60.0]))
    assert st["mean_mm"] == pytest.approx(1.0)
    assert (st["min_mm"], st["max_mm"]) == (0.0, 3.0)
    assert st["wet_area_fraction"] == pytest.approx(0.5 / 1.5)


def test_area_weighted_global_band_fraction():
    # Wet everywhere north of 30N on a fine grid -> area fraction = (1 - sin 30)/2 = 0.25
    lats = np.linspace(89.875, -89.875, 720)
    v = np.where(lats[:, None] > 30, 5.0, 0.0) * np.ones((1, 10))
    st = area_weighted_stats(v, lats)
    assert st["wet_area_fraction"] == pytest.approx(0.25, abs=2e-3)
    assert st["mean_mm"] == pytest.approx(1.25, abs=1e-2)


def test_area_weighted_stats_skip_missing():
    v = np.array([[np.nan, 2.0], [4.0, np.nan]])
    st = area_weighted_stats(v, np.array([0.0, 0.0]))
    assert st["n_valid"] == 2 and st["mean_mm"] == pytest.approx(3.0)
    assert area_weighted_stats(np.full((2, 2), np.nan), np.array([0.0, 1.0]))["mean_mm"] is None


def test_point_value_nearest_and_dateline_wrap():
    v = np.arange(12.0).reshape(3, 4)
    assert point_value(v, LATS, LONS, 9.0, 11.0) == (2.0, 10.0, 10.0)
    lons = np.array([-180.0, -90.0, 0.0, 90.0])
    assert point_value(v, LATS, lons, 0.0, 179.9)[2] == -180.0
    with pytest.raises(ValueError):
        point_value(v, LATS, LONS, 91, 0)
