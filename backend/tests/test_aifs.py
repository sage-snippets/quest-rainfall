"""AIFS-single support, validated on a real ECMWF AIFS GRIB2 fixture.

Fixture: aifs-single 2026-11-15 06z, tp steps 0, 0-6, 0-12, 0-24 (see fixtures/README.md).
Key difference from IFS (inspected with raw ecCodes): tp is encoded in kg m**-2
(paramId 228228, class ai), i.e. already mm of water, not metres.
"""
import shutil
from datetime import datetime, timezone
from pathlib import Path

import eccodes
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.acquisition import ecmwf
from app.config import Settings
from app.main import create_app
from app.pipeline import process_file, steps_up_to
from app.processing.precip import area_weighted_stats, read_tp, subset

FIX = Path(__file__).parent / "fixtures"
AIFS = FIX / "aifs-single_tp_20261115_06z_steps_0_6_12_24.grib2"
IFS = FIX / "ifs_tp_20261115_00z_steps_0_6_12.grib2"


def raw(path):
    out = []
    with path.open("rb") as f:
        while (g := eccodes.codes_grib_new_from_file(f)) is not None:
            out.append(({k: eccodes.codes_get(g, k) for k in ("units", "paramId", "stepRange", "dataTime")},
                        eccodes.codes_get_values(g)))
            eccodes.codes_release(g)
    return out


def test_fixture_metadata_differs_from_ifs_in_units_only():
    a, i = raw(AIFS), raw(IFS)
    assert {m["units"] for m, _ in a} == {"kg m**-2"} and {m["paramId"] for m, _ in a} == {228228}
    assert {m["units"] for m, _ in i} == {"m"} and {m["paramId"] for m, _ in i} == {228}
    assert [m["stepRange"] for m, _ in a] == ["0", "0-6", "0-12", "0-24"]
    assert a[0][0]["dataTime"] == 600                 # AIFS publishes full 06z runs


def test_aifs_kg_m2_is_not_multiplied_by_1000():
    fields = read_tp(AIFS)
    for f, (_, v) in zip(fields, raw(AIFS)):
        assert np.nanmax(f.values_mm) == pytest.approx(v.max())          # factor 1, not 1000
    f24 = fields[-1]
    assert f24.quantum_mm == pytest.approx(2.0 ** -4)                     # 0.0625 kg m**-2 = mm
    # independent physical check: global area-weighted 24 h mean ~3 mm (climatology ~2.7-2.9)
    mean = area_weighted_stats(f24.values_mm, f24.lats)["mean_mm"]
    assert 2.0 < mean < 4.5, mean


def test_aifs_grid_identical_to_ifs():
    a, i = read_tp(AIFS)[1], read_tp(IFS)[1]
    np.testing.assert_array_equal(a.lats, i.lats)
    np.testing.assert_array_equal(a.lons, i.lons)


def test_aifs_pipeline_intervals_and_qc(tmp_path):
    out = tmp_path / "aifs-single" / "2026111506"
    out.mkdir(parents=True)
    shutil.copy(AIFS, out / "tp.grib2")
    m = process_file(out / "tp.grib2", out, "aifs-single")
    assert m["run"] == "2026-11-15T06:00:00+00:00" and m["units_source"] == "kg m**-2"
    iv = {(x["start_step"], x["end_step"]): x for x in m["intervals"]}
    assert set(iv) == {(0, 6), (6, 12), (12, 24)}
    # Verified by hand with raw ecCodes: 6->12 has 21531 packing-noise negatives of -0.03125
    assert iv[(6, 12)]["n_clamped_negatives"] == 21531
    assert iv[(6, 12)]["min_raw_diff_mm"] == pytest.approx(-0.03125)
    assert iv[(6, 12)]["packing_tolerance_mm"] == pytest.approx(0.0625)
    assert iv[(0, 6)]["max_mm"] == pytest.approx(115.46875)
    total = sum(np.load(out / f"interval_{a}_{b}.npy") for a, b in iv)
    assert np.abs(total - np.load(out / "cumulative_24.npy")).max() <= 0.0625 + 1e-6


def test_aifs_steps_and_runs():
    assert steps_up_to(24, "aifs-single") == [0, 6, 12, 18, 24]
    run = datetime(2026, 11, 15, 6, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="not published for aifs-single"):
        ecmwf.fetch_tp(run, [0, 3], Path("unused.grib2"), model="aifs-single")
    with pytest.raises(ValueError, match="only 00z and 12z ifs runs"):   # IFS still refuses 06z
        ecmwf.fetch_tp(run, [0, 6], Path("unused.grib2"), model="ifs")


def test_latest_run_aifs_probes_6_hourly_runs():
    seen = []

    def handler(req):
        seen.append(req.url.path.split("/")[3])
        return httpx.Response(200 if req.url.path.split("/")[3] == "06z" else 404)
    c = httpx.Client(transport=httpx.MockTransport(handler))
    now = datetime(2026, 11, 15, 13, 30, tzinfo=timezone.utc)
    assert ecmwf.latest_run("aifs-single", 72, now=now, client=c) == datetime(2026, 11, 15, 6, tzinfo=timezone.utc)
    assert seen == ["12z", "06z"]
    assert all("/aifs-single/0p25/oper/" in u for u in [ecmwf.INDEX_URL.format(d=now, model="aifs-single", step=6)])


def test_api_serves_both_models_from_their_own_runs(tmp_path):
    for model, fix, rid in [("ifs", IFS, "2026111500"), ("aifs-single", AIFS, "2026111506")]:
        out = tmp_path / model / rid
        out.mkdir(parents=True)
        shutil.copy(fix, out / "tp.grib2")
        process_file(out / "tp.grib2", out, model)
    c = TestClient(create_app(Settings(data_root=tmp_path)))
    h = c.get("/api/health").json()
    assert h["latest_runs"] == {"ifs": "2026111500", "aifs-single": "2026111506"}
    meta = c.get("/api/forecast/latest", params={"model": "aifs-single"}).json()
    assert meta["steps"] == [0, 6, 12, 24] and meta["run"] == "2026-11-15T06:00:00+00:00"
    s = c.get("/api/forecast/aifs-single/2026111506/summary", params={"region": "india", "end_step": 24, "start_step": 0}).json()
    st = read_tp(AIFS)[-1]
    sub, la, _ = subset(st.values_mm, st.lats, st.lons, (68.0, 6.0, 97.5, 37.5))
    assert s["stats"]["max_mm"] == pytest.approx(float(np.nanmax(sub)), rel=1e-6)
    assert s["stats"]["mean_mm"] == pytest.approx(area_weighted_stats(sub, la)["mean_mm"], rel=1e-5)
    t = c.get("/api/forecast/aifs-single/2026111506/tile/3/5/3.png", params={"start_step": 0, "end_step": 24})
    assert t.status_code == 200 and t.content[:4] == b"\x89PNG"
    # IFS unchanged next to AIFS
    assert c.get("/api/forecast/ifs/2026111500/summary", params={"region": "india", "end_step": 6}).json()["stats"]["max_mm"] == pytest.approx(330.69, abs=0.01)
