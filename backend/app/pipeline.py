"""Ingest one forecast run: download tp, validate, convert to interval precipitation, store.

Store layout (<data_root>/<model>/<YYYYMMDDHH>/):
  tp.grib2                 raw ECMWF messages (kept for provenance/reprocessing)
  lats.npy, lons.npy       grid axes (lat descending, lon ascending in [-180, 180))
  interval_<a>_<b>.npy     float32 mm that fell between steps a and b
  cumulative_<b>.npy       float32 mm accumulated 0 -> b
  manifest.json            run metadata, source URLs, per-interval QC diagnostics

Grids are plain .npy so the API can memory-map them (low RAM on small hosts).
A run is written to "<run>.partial" and renamed into place only when complete, so
readers never see a half-written run and a failed ingest leaves older runs intact.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .acquisition.ecmwf import IFS_STEPS, fetch_tp, latest_run
from .processing.precip import interval, iter_tp

log = logging.getLogger(__name__)
DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "store"


def run_dir(model: str, run: datetime, root: Path = DATA_ROOT) -> Path:
    return root / model / run.strftime("%Y%m%d%H")


def steps_up_to(max_step: int) -> list[int]:
    """All published IFS steps from 0 to max_step (3-hourly to 144 h, then 6-hourly)."""
    return [s for s in IFS_STEPS if s <= max_step]


def process_file(grib: Path, out: Path, model: str, retrieval: dict | None = None) -> dict:
    """Decode a tp GRIB file step by step and write cumulative + interval grids and a manifest.

    Only two decoded fields are held in memory at a time.
    """
    out.mkdir(parents=True, exist_ok=True)
    fields = iter_tp(grib)
    prev = next(fields)
    if prev.end_step != 0:
        # Step 0 is identically zero by definition; we still require it so the first
        # interval is computed from data rather than an assumption.
        raise ValueError("step 0 must be included to compute the first interval")
    meta0 = prev.meta
    run = datetime.strptime(f"{prev.run_date}{prev.run_time:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    np.save(out / "lats.npy", prev.lats.astype(np.float32))
    np.save(out / "lons.npy", prev.lons.astype(np.float32))
    grid = {"type": meta0["gridType"], "ni": len(prev.lons), "nj": len(prev.lats),
            "lat_first": float(prev.lats[0]), "lat_last": float(prev.lats[-1]),
            "lon_first": float(prev.lons[0]), "lon_last": float(prev.lons[-1])}

    steps, intervals = [0], []
    for cur in fields:
        iv = interval(prev, cur)
        np.save(out / f"cumulative_{cur.end_step}.npy", cur.values_mm.astype(np.float32))
        np.save(out / f"interval_{prev.end_step}_{cur.end_step}.npy", iv.values_mm.astype(np.float32))
        intervals.append({
            "start_step": prev.end_step, "end_step": cur.end_step,
            "valid_from": (run + timedelta(hours=prev.end_step)).isoformat(),
            "valid_to": (run + timedelta(hours=cur.end_step)).isoformat(),
            "n_clamped_negatives": iv.n_clamped,
            "min_raw_diff_mm": iv.min_raw_mm,
            "packing_tolerance_mm": max(prev.quantum_mm, cur.quantum_mm),
            "max_mm": float(np.nanmax(iv.values_mm)),
        })
        steps.append(cur.end_step)
        prev = cur

    manifest = {
        "model": model,
        "run": run.isoformat(),
        "variable": "tp (total precipitation: rain + snow, liquid water equivalent)",
        "units_source": meta0["units"],
        "units_output": "mm",
        "accumulation": "from forecast start; intervals = differences of consecutive steps",
        "steps": steps,
        "grid": grid,
        "intervals": intervals,
        "retrieval": retrieval,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "Contains ECMWF Open Data (CC BY 4.0). Forecast, not observations.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def ingest(model: str = "ifs", steps: list[int] | None = None, run: datetime | None = None,
           root: Path = DATA_ROOT) -> dict:
    """Download and process a run, publishing it atomically. Returns the manifest."""
    steps = sorted(set(steps or [0, 6, 12, 18, 24]))
    if steps[0] != 0:
        steps = [0] + steps
    run = run or latest_run(model, last_step=steps[-1])
    final = run_dir(model, run, root)
    tmp = final.with_name(final.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        retrieval = fetch_tp(run, steps, tmp / "tp.grib2", model=model)
        retrieval.path = str(final / "tp.grib2")
        manifest = process_file(tmp / "tp.grib2", tmp, model, retrieval.to_dict())
        if final.exists():                       # re-ingest of the same run: replace it
            shutil.rmtree(final)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)   # never leave partial runs behind
        raise
    log.info("ingested run", extra={"model": model, "run": manifest["run"], "steps": len(steps)})
    return manifest


def prune(model: str, keep: int, root: Path = DATA_ROOT) -> list[str]:
    """Delete all but the newest `keep` complete runs (and any stale .partial dirs)."""
    d = root / model
    if not d.is_dir():
        return []
    runs = sorted((p for p in d.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 10
                   and (p / "manifest.json").exists()), key=lambda p: p.name, reverse=True)
    removed = []
    for p in runs[keep:] + [p for p in d.iterdir() if p.name.endswith(".partial")]:
        shutil.rmtree(p, ignore_errors=True)
        removed.append(p.name)
    return removed
