"""Production behaviour: streaming decode, atomic ingest, refresh failure handling, SPA serving."""
import shutil
from datetime import datetime, timezone
from pathlib import Path

import eccodes
import pytest
from fastapi.testclient import TestClient

from app import pipeline, refresh
from app.acquisition.ecmwf import Retrieval
from app.config import Settings
from app.main import create_app
from app.processing.precip import GribValidationError, iter_tp, read_tp
from app.refresh import Refresher
from app.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "ifs_tp_20261115_00z_steps_0_6_12.grib2"
RUN = datetime(2026, 11, 15, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ streaming decode

def test_iter_tp_matches_read_tp():
    a, b = list(iter_tp(FIXTURE)), read_tp(FIXTURE)
    assert [f.end_step for f in a] == [f.end_step for f in b] == [0, 6, 12]
    assert all((x.values_mm == y.values_mm).all() for x, y in zip(a, b))


def test_iter_tp_rejects_out_of_order_steps(tmp_path):
    msgs = []
    with FIXTURE.open("rb") as f:
        while (g := eccodes.codes_grib_new_from_file(f)) is not None:
            msgs.append(eccodes.codes_get_message(g)); eccodes.codes_release(g)
    rev = tmp_path / "rev.grib2"
    rev.write_bytes(b"".join(reversed(msgs)))
    with pytest.raises(GribValidationError, match="strictly increasing"):
        list(iter_tp(rev))
    assert [f.end_step for f in read_tp(rev)] == [0, 6, 12]   # read_tp sorts


# ------------------------------------------------------------------ atomic ingest

def fake_fetch(source: Path):
    def fetch(run, steps, target, model="ifs"):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        return Retrieval(model, run, steps, str(target), "ecmwf", ["https://data.ecmwf.int/x"])
    return fetch


def test_ingest_publishes_complete_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_tp", fake_fetch(FIXTURE))
    m = pipeline.ingest("ifs", [0, 6, 12], RUN, tmp_path)
    d = tmp_path / "ifs" / "2026111500"
    assert m["steps"] == [0, 6, 12] and (d / "manifest.json").exists()
    assert {p.name for p in (tmp_path / "ifs").iterdir()} == {"2026111500"}   # no .partial left
    assert Store(tmp_path).window("ifs", "2026111500", 0, 12).shape == (721, 1440)


def test_failed_ingest_keeps_previous_run_and_leaves_no_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_tp", fake_fetch(FIXTURE))
    pipeline.ingest("ifs", [0, 6, 12], RUN, tmp_path)
    before = (tmp_path / "ifs" / "2026111500" / "manifest.json").read_text()
    corrupt = tmp_path / "corrupt.grib2"
    corrupt.write_bytes(b"GRIB not really")
    monkeypatch.setattr(pipeline, "fetch_tp", fake_fetch(corrupt))
    with pytest.raises(Exception):
        pipeline.ingest("ifs", [0, 6, 12], RUN.replace(hour=12), tmp_path)
    assert {p.name for p in (tmp_path / "ifs").iterdir()} == {"2026111500"}
    assert (tmp_path / "ifs" / "2026111500" / "manifest.json").read_text() == before


def test_prune_keeps_newest_runs(tmp_path):
    for rid in ["2026111300", "2026111312", "2026111400", "2026111412"]:
        (tmp_path / "ifs" / rid).mkdir(parents=True)
        (tmp_path / "ifs" / rid / "manifest.json").write_text("{}")
    (tmp_path / "ifs" / "2026111500.partial").mkdir()
    removed = pipeline.prune("ifs", 2, tmp_path)
    assert sorted(removed) == ["2026111300", "2026111312", "2026111500.partial"]
    assert Store(tmp_path).runs("ifs") == ["2026111412", "2026111400"]


def test_steps_up_to_follows_published_ifs_steps():
    assert pipeline.steps_up_to(12) == [0, 3, 6, 9, 12]
    assert pipeline.steps_up_to(156)[-4:] == [141, 144, 150, 156]


# ------------------------------------------------------------------ refresher

def test_refresher_ingests_newer_run_then_is_idempotent(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(refresh, "latest_run", lambda model, last_step: RUN)
    monkeypatch.setattr(pipeline, "fetch_tp", fake_fetch(FIXTURE))
    monkeypatch.setattr(refresh, "ingest", lambda *a: calls.append(a) or pipeline.ingest("ifs", [0, 6, 12], RUN, tmp_path))
    r = Refresher(Store(tmp_path), tmp_path, max_step=12)
    assert r.run_once() and len(calls) == 1
    assert r.run_once() and len(calls) == 1        # same run already held -> no download
    assert r.status["state"] == "idle" and r.status["last_error"] is None


def test_refresher_failure_preserves_last_valid_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_tp", fake_fetch(FIXTURE))
    pipeline.ingest("ifs", [0, 6, 12], RUN, tmp_path)

    def down(model, last_step):
        raise ConnectionError("data.ecmwf.int unreachable")
    monkeypatch.setattr(refresh, "latest_run", down)
    store = Store(tmp_path)
    r = Refresher(store, tmp_path, max_step=12)
    assert r.run_once() is False and r.run_once() is False
    assert r.status["state"] == "error" and r.status["consecutive_failures"] == 2
    assert "unreachable" in r.status["last_error"]
    assert store.latest("ifs") == "2026111500"     # still served

    client = TestClient(create_app(Settings(data_root=tmp_path)))
    client.app.state.refresher.status.update(r.status)
    h = client.get("/api/health").json()
    assert h["data_available"] is True and h["ingest"]["state"] == "error"
    assert client.get("/api/forecast/latest").status_code == 200


# ------------------------------------------------------------------ same-origin SPA

@pytest.fixture
def spa_client(tmp_path):
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><div id=root></div>")
    (static / "assets" / "index-abc.js").write_text("console.log(1)")
    (static / "assets" / "maplibre-gl-worker-xyz.mjs").write_text("self.onmessage=()=>{}")
    (static / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("nope")
    return TestClient(create_app(Settings(data_root=tmp_path / "store", static_dir=static)))


def test_spa_index_and_client_routes(spa_client):
    for path in ["/", "/some/client/route"]:
        r = spa_client.get(path)
        assert r.status_code == 200 and "id=root" in r.text
        assert r.headers["cache-control"] == "no-cache"


def test_spa_module_worker_mime_and_caching(spa_client):
    r = spa_client.get("/assets/maplibre-gl-worker-xyz.mjs")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert "immutable" in r.headers["cache-control"]
    assert spa_client.get("/assets/index-abc.js").headers["content-type"].startswith("text/javascript")
    assert spa_client.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
    h = spa_client.head("/assets/maplibre-gl-worker-xyz.mjs")      # monitors/proxies use HEAD
    assert h.status_code == 200 and h.headers["content-type"].startswith("text/javascript")
    assert spa_client.head("/").status_code == 200


def test_spa_does_not_swallow_api_errors_or_escape_root(spa_client):
    r = spa_client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert spa_client.get("/api/health").json()["status"] == "ok"
    r = spa_client.get("/..%2Fsecret.txt")
    assert "nope" not in r.text


def test_static_dir_without_index_fails_fast(tmp_path):
    with pytest.raises(RuntimeError, match="index.html"):
        create_app(Settings(data_root=tmp_path, static_dir=tmp_path))
