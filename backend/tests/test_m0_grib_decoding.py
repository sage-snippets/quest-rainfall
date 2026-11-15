"""M0: ecCodes/cfgrib decode a real ECMWF IFS GRIB2 `tp` file correctly.

Expected values were established by inspecting the fixture with raw ecCodes
(scripts/verify_grib.py) and are asserted here as a regression guard.
"""

from pathlib import Path

import eccodes
import numpy as np
import pytest
import xarray as xr

FIXTURE = Path(__file__).parent / "fixtures" / "ifs_tp_20261115_00z_steps_0_6_12.grib2"


def _messages(path: Path) -> list[dict]:
    out = []
    with path.open("rb") as f:
        while (gid := eccodes.codes_grib_new_from_file(f)) is not None:
            try:
                m = {k: eccodes.codes_get(gid, k) for k in (
                    "shortName", "units", "stepType", "stepRange", "startStep", "endStep",
                    "Ni", "Nj", "latitudeOfFirstGridPointInDegrees",
                    "longitudeOfFirstGridPointInDegrees", "latitudeOfLastGridPointInDegrees",
                    "longitudeOfLastGridPointInDegrees", "iScansNegatively", "jScansPositively",
                    "binaryScaleFactor", "decimalScaleFactor", "bitmapPresent", "dataDate", "dataTime",
                )}
                m["values"] = eccodes.codes_get_values(gid).reshape(m["Nj"], m["Ni"])
                out.append(m)
            finally:
                eccodes.codes_release(gid)
    return out


@pytest.fixture(scope="module")
def msgs():
    assert FIXTURE.exists(), f"missing fixture {FIXTURE}"
    return _messages(FIXTURE)


def test_native_library_loads():
    assert eccodes.codes_get_api_version().count(".") == 2


def test_message_metadata(msgs):
    assert [m["stepRange"] for m in msgs] == ["0", "0-6", "0-12"]
    for m in msgs:
        assert m["shortName"] == "tp"
        assert m["units"] == "m"                       # metres of water equivalent
        assert m["stepType"] == "accum"                 # accumulated...
        assert m["startStep"] == 0                      # ...from forecast start
        assert (m["dataDate"], m["dataTime"]) == (20261115, 0)
        assert m["bitmapPresent"] == 0


def test_grid_geometry(msgs):
    for m in msgs:
        assert (m["Ni"], m["Nj"]) == (1440, 721)
        # North-to-south rows, west-to-east columns starting at the antimeridian.
        assert m["latitudeOfFirstGridPointInDegrees"] == 90.0
        assert m["latitudeOfLastGridPointInDegrees"] == -90.0
        assert m["longitudeOfFirstGridPointInDegrees"] == 180.0
        assert m["longitudeOfLastGridPointInDegrees"] == 179.75
        assert m["iScansNegatively"] == 0 and m["jScansPositively"] == 0


def test_step0_is_zero(msgs):
    assert np.all(msgs[0]["values"] == 0.0)


def test_values_physical(msgs):
    for m in msgs[1:]:
        v = m["values"]
        assert np.isfinite(v).all()
        assert v.min() >= 0.0
        assert v.max() < 1.0   # < 1 m accumulated in 12 h


def test_cfgrib_matches_eccodes(msgs):
    ds = xr.open_dataset(FIXTURE, engine="cfgrib", backend_kwargs={"indexpath": ""})
    tp = ds["tp"]
    assert tp.attrs["units"] == "m"
    assert float(ds.latitude[0]) == 90.0 and float(ds.latitude[-1]) == -90.0
    assert float(ds.longitude[0]) == -180.0 and float(ds.longitude[-1]) == 179.75
    for i, m in enumerate(msgs):
        np.testing.assert_array_equal(tp.isel(step=i).values, m["values"].astype(np.float32))


def test_accumulation_negatives_bounded_by_packing_quantum(msgs):
    """Consecutive accumulated fields may decrease only by GRIB packing noise."""
    for prev, cur in zip(msgs[1:], msgs[2:]):
        diff = cur["values"] - prev["values"]
        quantum = max(2.0 ** p["binaryScaleFactor"] * 10.0 ** -p["decimalScaleFactor"] for p in (prev, cur))
        assert diff.min() >= -quantum * (1 + 1e-9)
