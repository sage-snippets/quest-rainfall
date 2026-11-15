"""API tests on a store built from the real ECMWF fixture (IFS 2026-11-15 00z, steps 0/6/12)."""

import shutil
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.geocode import Geocoder
from app.config import Settings
from app.main import create_app
from app.pipeline import process_file
from app.processing.precip import area_weighted_stats, subset
from app.regions import REGIONS
from app.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "ifs_tp_20261115_00z_steps_0_6_12.grib2"
RUN = "2026111500"
BASE = f"/api/forecast/ifs/{RUN}"


@pytest.fixture(scope="module")
def store_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("store")
    out = root / "ifs" / RUN
    out.mkdir(parents=True)
    shutil.copy(FIXTURE, out / "tp.grib2")
    process_file(out / "tp.grib2", out, "ifs",
                 {"source": "ecmwf", "urls": ["https://data.ecmwf.int/forecasts/20261115/00z/ifs/0p25/oper/x"],
                  "retrieved_at": "2026-11-15T11:00:00+00:00"})
    return root


class FakeGeo:
    """Mock Open-Meteo transport: counts calls, can be switched to fail."""
    def __init__(self):
        self.calls, self.mode = 0, "ok"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "500":
            return httpx.Response(500, text="upstream down")
        if self.mode == "timeout":
            raise httpx.ConnectTimeout("timeout", request=request)
        if self.mode == "garbage":
            return httpx.Response(200, text="<html>")
        name = request.url.params["name"]
        return httpx.Response(200, json={"results": [
            {"name": name.title(), "country": "India", "admin1": "Karnataka",
             "latitude": 12.97194, "longitude": 77.59369}]})


@pytest.fixture
def geo():
    return FakeGeo()


def make_client(root, geo, **kw):
    s = Settings(data_root=root, cors_origins=["https://app.example"], **kw)
    g = Geocoder(s.geocode_url, 1.0, 3600, transport=httpx.MockTransport(geo))
    return TestClient(create_app(s, g), raise_server_exceptions=False)


@pytest.fixture
def client(store_root, geo):
    return make_client(store_root, geo)


def arrays(root):
    st = Store(root)
    lats, lons = st.axes("ifs", RUN)
    return st, lats, lons


# ------------------------------------------------------------------ health / models / latest

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "latest_runs": {"ifs": RUN, "aifs-single": None}, "data_available": True}
    assert r.headers["X-Request-ID"]


def test_health_without_data(tmp_path, geo):
    r = make_client(tmp_path, geo).get("/api/health")
    assert r.json()["data_available"] is False


def test_models_ifs_operational_aifs_pending(client):
    m = {x["id"]: x for x in client.get("/api/models").json()}
    assert m["ifs"]["status"] == "operational" and m["ifs"]["runs_available"] == 1
    assert m["aifs-single"]["status"] == "pending" and m["aifs-single"]["runs_available"] == 0


def test_latest_metadata(client):
    r = client.get("/api/forecast/latest")
    assert r.status_code == 200
    d = r.json()
    assert d["run_id"] == RUN and d["run"] == "2026-11-15T00:00:00+00:00"
    assert d["steps"] == [0, 6, 12] and d["units"] == "mm"
    assert d["source"] == "ecmwf" and d["source_urls"][0].startswith("https://data.ecmwf.int/")
    assert "not observ" in d["note"] and "CC BY 4.0" in d["attribution"]
    assert [(i["start_step"], i["end_step"]) for i in d["intervals"]] == [(0, 6), (6, 12)]


def test_latest_404_when_nothing_ingested(tmp_path, geo):
    r = make_client(tmp_path, geo).get("/api/forecast/latest")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_aifs_not_served_while_pending(client):
    for url in ["/api/forecast/latest?model=aifs-single", f"/api/forecast/aifs-single/{RUN}/summary?region=india"]:
        r = client.get(url)
        assert r.status_code == 409 and r.json()["error"]["code"] == "model_unavailable"


def test_unknown_model_rejected(client):
    assert client.get(f"/api/forecast/gfs/{RUN}/summary?region=india").status_code == 422


# ------------------------------------------------------------------ grid

def test_grid_matches_stored_interval(client, store_root):
    r = client.get(f"{BASE}/grid", params={"bbox": "77,12,78,13.5", "end_step": 6})
    assert r.status_code == 200
    d = r.json()
    assert d["window"] == {"start_step": 0, "end_step": 6, "hours": 6,
                           "valid_from": "2026-11-15T00:00:00+00:00", "valid_to": "2026-11-15T06:00:00+00:00"}
    assert d["lats"] == [13.5, 13.25, 13.0, 12.75, 12.5, 12.25, 12.0]   # north -> south
    assert d["lons"] == [77.0, 77.25, 77.5, 77.75, 78.0]
    st, lats, lons = arrays(store_root)
    exp, _, _ = subset(st.cumulative("ifs", RUN, 6), lats, lons, (77, 12, 78, 13.5))
    np.testing.assert_allclose(np.array(d["values"]), exp, atol=0.005)


def test_grid_default_and_explicit_windows(client, store_root):
    st, lats, lons = arrays(store_root)
    box = (80, 5, 90, 15)
    q = {"bbox": "80,5,90,15"}
    # end_step=12 with default start -> native interval 6-12
    d = client.get(f"{BASE}/grid", params={**q, "end_step": 12}).json()
    assert (d["window"]["start_step"], d["window"]["end_step"]) == (6, 12)
    np.testing.assert_allclose(d["values"], subset(st.interval("ifs", RUN, 6, 12), lats, lons, box)[0], atol=0.005)
    # 0-12 -> raw accumulated field
    d = client.get(f"{BASE}/grid", params={**q, "start_step": 0, "end_step": 12}).json()
    assert d["window"]["hours"] == 12
    np.testing.assert_allclose(d["values"], subset(st.cumulative("ifs", RUN, 12), lats, lons, box)[0], atol=0.005)


def test_grid_antimeridian(client):
    d = client.get(f"{BASE}/grid", params={"bbox": "179,-1,-179,1"}).json()
    assert d["lons"] == [179.0, 179.25, 179.5, 179.75, -180.0, -179.75, -179.5, -179.25, -179.0]


@pytest.mark.parametrize("params,status", [
    ({"bbox": "-180,-90,180,90"}, 413),                       # whole globe: too many cells
    ({"bbox": "1,2,3"}, 422), ({"bbox": "a,b,c,d"}, 422), ({"bbox": "0,0,nan,1"}, 422),
    ({"bbox": "0,10,5,5"}, 422),                              # south > north
    ({"bbox": "0,0,5,5", "end_step": 7}, 422),                # not an available step
    ({"bbox": "0,0,5,5", "end_step": 0}, 422),
    ({"bbox": "0,0,5,5", "start_step": 12, "end_step": 6}, 422),
    ({}, 422),                                                # bbox required
])
def test_grid_invalid_inputs(client, params, status):
    r = client.get(f"{BASE}/grid", params=params)
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] in {"invalid_input", "region_too_large"}


@pytest.mark.parametrize("run,status", [("2026111412", 404), ("20261115", 422), ("..%2F..%2Fetc", 404),
                                        ("2026111500x", 422)])
def test_unknown_or_malformed_run(client, run, status):
    r = client.get(f"/api/forecast/ifs/{run}/grid", params={"bbox": "0,0,1,1"})
    assert r.status_code == status, r.text
    assert "error" in r.json()


# ------------------------------------------------------------------ summary

def test_summary_preset_matches_independent_computation(client, store_root):
    d = client.get(f"{BASE}/summary", params={"region": "india", "end_step": 6}).json()
    st, lats, lons = arrays(store_root)
    sub, la, _ = subset(st.cumulative("ifs", RUN, 6), lats, lons, REGIONS["india"]["bbox"])
    exp = area_weighted_stats(sub, la)
    for k in ("n_valid", "min_mm", "max_mm", "mean_mm", "wet_area_fraction"):
        assert d["stats"][k] == pytest.approx(exp[k], rel=1e-6), k
    assert d["region_name"] == "India" and d["max_location"] == [9.0, 83.75]
    assert d["stats"]["max_mm"] == pytest.approx(330.69, abs=0.01)
    assert any("approximate bounding boxes" in n for n in d["notes"])


def test_summary_dry_region_has_no_max_location(client):
    # Sahara box: dry in this run (checked: max 0.0 mm for 0-6 h)
    d = client.get(f"{BASE}/summary", params={"bbox": "10,22,20,28", "end_step": 6}).json()
    assert d["stats"]["max_mm"] == 0.0 and d["max_location"] is None


def test_summary_custom_bbox_and_threshold(client):
    d = client.get(f"{BASE}/summary", params={"bbox": "73,8,77.5,21.5", "end_step": 12,
                                              "start_step": 0, "wet_threshold_mm": 0.5}).json()
    assert d["region_name"] == "Custom box" and d["stats"]["wet_threshold_mm"] == 0.5
    assert 0 <= d["stats"]["wet_area_fraction"] <= 1


@pytest.mark.parametrize("params", [{}, {"region": "india", "bbox": "0,0,1,1"}, {"region": "atlantis"},
                                    {"region": "india", "wet_threshold_mm": 0}])
def test_summary_invalid(client, params):
    assert client.get(f"{BASE}/summary", params=params).status_code == 422


# ------------------------------------------------------------------ timeline

def test_timeline_nearest_cell(client):
    d = client.get(f"{BASE}/timeline", params={"lat": 9.1, "lon": 83.8}).json()
    assert d["grid_cell"] == [9.0, 83.75] and d["requested"] == [9.1, 83.8]
    s = d["series"]
    assert [(p["start_step"], p["end_step"]) for p in s] == [(0, 6), (6, 12)]
    assert s[0]["interval_mm"] == pytest.approx(330.69, abs=0.01)
    assert s[0]["cumulative_mm"] == pytest.approx(s[0]["interval_mm"])
    # cumulative(12) = interval(0-6) + interval(6-12) up to clamped packing noise
    assert s[1]["cumulative_mm"] == pytest.approx(s[0]["interval_mm"] + s[1]["interval_mm"], abs=0.13)


@pytest.mark.parametrize("params", [{"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, {"lat": 0}, {"lat": "x", "lon": 0}])
def test_timeline_invalid(client, params):
    assert client.get(f"{BASE}/timeline", params=params).status_code == 422


# ------------------------------------------------------------------ multi-interval window (store)

def test_store_window_sums_native_intervals(tmp_path):
    out = tmp_path / "ifs" / RUN
    out.mkdir(parents=True)
    lats, lons = np.array([1.0, 0.0], np.float32), np.array([0.0, 1.0], np.float32)
    np.save(out / "lats.npy", lats); np.save(out / "lons.npy", lons)
    for (a, b), v in {(0, 3): 1.0, (3, 6): 2.0, (6, 9): 4.0}.items():
        np.savez_compressed(out / f"interval_{a}_{b}.npz", v=np.full((2, 2), v, np.float32))
    (out / "manifest.json").write_text('{"steps": [0, 3, 6, 9], "run": "2026-11-15T00:00:00+00:00"}')
    st = Store(tmp_path)
    np.testing.assert_array_equal(st.window("ifs", RUN, 3, 9), np.full((2, 2), 6.0))
    with pytest.raises(ValueError):
        st.window("ifs", RUN, 6, 3)


# ------------------------------------------------------------------ geocode

def test_geocode_success_and_cache(client, geo):
    r1 = client.get("/api/geocode", params={"q": "bengaluru"}).json()
    r2 = client.get("/api/geocode", params={"q": "  Bengaluru "}).json()
    assert r1["results"][0] == {"name": "Bengaluru", "country": "India", "admin1": "Karnataka",
                                "lat": 12.97194, "lon": 77.59369}
    assert (r1["cached"], r2["cached"], geo.calls) == (False, True, 1)


@pytest.mark.parametrize("mode", ["500", "timeout", "garbage"])
def test_geocode_upstream_failure(client, geo, mode):
    geo.mode = mode
    r = client.get("/api/geocode", params={"q": "mumbai"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_unavailable"


@pytest.mark.parametrize("q", ["a", "x" * 101])
def test_geocode_invalid_query(client, q):
    assert client.get("/api/geocode", params={"q": q}).status_code == 422


# ------------------------------------------------------------------ cross-cutting

def test_cors_allows_configured_origin_only(client):
    ok = client.get("/api/health", headers={"Origin": "https://app.example"})
    bad = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert ok.headers["access-control-allow-origin"] == "https://app.example"
    assert "access-control-allow-origin" not in bad.headers


def test_cache_headers(client):
    assert client.get(f"{BASE}/summary?region=delhi").headers["cache-control"] == "public, max-age=3600"
    assert client.get("/api/forecast/latest").headers["cache-control"] == "public, max-age=60"


def test_unhandled_error_is_sanitised(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internal path /etc/x")
    monkeypatch.setattr(client.app.state.store, "runs", boom)
    r = client.get("/api/health")
    assert r.status_code == 500
    assert r.json()["error"]["message"] == "internal server error" and "secret" not in r.text


def test_error_shape_has_request_id(client):
    r = client.get("/api/forecast/latest", headers={"X-Request-ID": "abc123"}, params={"model": "nope"})
    assert r.status_code == 422
    assert r.json()["error"]["request_id"] == "abc123" and r.headers["X-Request-ID"] == "abc123"
