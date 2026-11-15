"""Pipeline on real ECMWF data (fixture), acquisition logic (mocked network), optional live test."""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.acquisition import ecmwf
from app.pipeline import process_file
from app.processing.precip import area_weighted_stats, subset
from app.regions import REGIONS

FIXTURE = Path(__file__).parent / "fixtures" / "ifs_tp_20261115_00z_steps_0_6_12.grib2"
RUN = datetime(2026, 11, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    shutil.copy(FIXTURE, out / "tp.grib2")
    return out, process_file(out / "tp.grib2", out, "ifs")


def load(out, name):
    return np.load(out / name)


def test_manifest_metadata(processed):
    _, m = processed
    assert m["run"] == "2026-11-15T00:00:00+00:00"
    assert m["steps"] == [0, 6, 12]
    assert m["units_source"] == "m" and m["units_output"] == "mm"
    assert m["grid"]["lon_first"] == -180.0 and m["grid"]["lat_first"] == 90.0
    iv = {(i["start_step"], i["end_step"]): i for i in m["intervals"]}
    assert iv[(0, 6)]["valid_to"] == "2026-11-15T06:00:00+00:00"
    # Hand-verified in M0 with raw ecCodes:
    assert iv[(0, 6)]["packing_tolerance_mm"] == pytest.approx(0.1220703125)
    assert iv[(6, 12)]["n_clamped_negatives"] == 28225
    assert iv[(6, 12)]["min_raw_diff_mm"] == pytest.approx(-0.1220703125)
    assert iv[(0, 6)]["max_mm"] == pytest.approx(330.6884765625, rel=1e-6)


def test_real_intervals_reconstruct_cumulative(processed):
    out, _ = processed
    total = load(out, "interval_0_6.npy") + load(out, "interval_6_12.npy")
    cum = load(out, "cumulative_12.npy")
    # differ only where packing-noise negatives were clamped (<= one 6 h quantum)
    assert np.abs(total - cum).max() <= 0.1221 + 1e-4
    assert (load(out, "interval_6_12.npy") >= 0).all()


def test_real_max_location(processed):
    out, _ = processed
    lats, lons, v = np.load(out / "lats.npy"), np.load(out / "lons.npy"), load(out, "interval_0_6.npy")
    j, i = np.unravel_index(v.argmax(), v.shape)
    assert (lats[j], lons[i]) == (9.0, 83.75)


def test_real_regional_summaries(processed):
    out, _ = processed
    lats, lons, v = np.load(out / "lats.npy"), np.load(out / "lons.npy"), load(out, "interval_0_6.npy")
    for key, reg in REGIONS.items():
        s, la, _ = subset(v, lats, lons, reg["bbox"])
        st = area_weighted_stats(s, la)
        assert st["n_valid"] == s.size > 0, key
        assert 0 <= st["min_mm"] <= st["mean_mm"] <= st["max_mm"], key


# ------------------------------------------------------------------ acquisition (mocked network)

class FakeResult:
    urls = [("https://example/x.grib2", ((0, 10),))]


def test_fetch_rejects_unpublished_steps_and_runs(tmp_path):
    with pytest.raises(ValueError, match="not published"):
        ecmwf.fetch_tp(RUN, [0, 145], tmp_path / "x.grib2")
    with pytest.raises(ValueError, match="00z and 12z"):
        ecmwf.fetch_tp(RUN.replace(hour=6), [0, 6], tmp_path / "x.grib2")


def test_fetch_retries_then_falls_back_to_mirror(tmp_path, monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, source, model, resol):
            self.source = source

        def retrieve(self, target, **kw):
            calls.append(self.source)
            if self.source == "ecmwf":
                raise ConnectionError("boom")
            Path(target).write_bytes(b"GRIB")
            return FakeResult()

    monkeypatch.setattr(ecmwf, "Client", FakeClient)
    r = ecmwf.fetch_tp(RUN, [0, 6], tmp_path / "x.grib2", retries=2, backoff_s=0)
    assert calls == ["ecmwf", "ecmwf", "aws"]
    assert r.source == "aws" and (tmp_path / "x.grib2").read_bytes() == b"GRIB"
    assert json.dumps(r.to_dict())  # serialisable provenance record


def test_fetch_all_sources_fail_leaves_no_partial_file(tmp_path, monkeypatch):
    class FailingClient:
        def __init__(self, *a, **k): ...

        def retrieve(self, target, **kw):
            Path(target).write_bytes(b"partial")
            raise TimeoutError("down")

    monkeypatch.setattr(ecmwf, "Client", FailingClient)
    with pytest.raises(ecmwf.AcquisitionError, match="all sources failed"):
        ecmwf.fetch_tp(RUN, [0, 6], tmp_path / "x.grib2", retries=1, backoff_s=0)
    assert list(tmp_path.iterdir()) == []


def _probe_client(available: set[str], status_other: int = 404):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        run = req.url.path.split("/")[2] + req.url.path.split("/")[3]   # YYYYMMDD + HHz
        if status_other >= 500:
            return httpx.Response(status_other)
        return httpx.Response(200 if run in available else 404)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_latest_run_only_considers_complete_00_12z_runs():
    now = datetime(2026, 11, 15, 13, 5, tzinfo=timezone.utc)
    c, seen = _probe_client({"2026111500z"})
    assert ecmwf.latest_run("ifs", 72, now=now, client=c) == RUN
    # probed 12z first (not yet published), then 00z; never 06z/18z
    assert [p.split("/")[3] for p in seen] == ["12z", "00z"]
    assert all("-72h-oper-fc.index" in p for p in seen)


def test_latest_run_steps_back_and_gives_up_outside_retention():
    now = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)
    c, _ = _probe_client({"2026111412z"})
    assert ecmwf.latest_run("ifs", 72, now=now, client=c) == datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    c, seen = _probe_client(set())
    with pytest.raises(ecmwf.AcquisitionError, match="no complete 00/12z"):
        ecmwf.latest_run("ifs", 72, now=now, client=c, lookback_h=48)
    # cutoff = now - 48 h = 13 Nov 01:00 -> 15/00, 14/12, 14/00, 13/12
    assert [p.split("/")[2] + p.split("/")[3] for p in seen] == ["2026111500z", "2026111412z", "2026111400z", "2026111312z"]


def test_latest_run_server_error_propagates_for_retry():
    c, _ = _probe_client(set(), status_other=503)
    with pytest.raises(httpx.HTTPStatusError):
        ecmwf.latest_run("ifs", 72, now=datetime(2026, 11, 15, 13, tzinfo=timezone.utc), client=c)


# ------------------------------------------------------------------ live (opt-in: RUN_LIVE=1)

@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to hit ECMWF")
def test_live_latest_run_and_retrieval(tmp_path):
    run = ecmwf.latest_run("ifs", last_step=6)
    # a run far in the future must not exist: the server answers 404, not an HTML page
    future = ecmwf.INDEX_URL.format(d=datetime(2099, 1, 1, tzinfo=timezone.utc), model="ifs", step=6)
    assert httpx.head(future, timeout=15, follow_redirects=True).status_code == 404
    assert run.hour in (0, 12) and (datetime.now(timezone.utc) - run).days < 4
    r = ecmwf.fetch_tp(run, [0, 6], tmp_path / "tp.grib2")
    m = process_file(Path(r.path), tmp_path, "ifs", r.to_dict())
    assert m["steps"] == [0, 6] and m["intervals"][0]["max_mm"] > 0
