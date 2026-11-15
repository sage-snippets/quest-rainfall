"""Ingest one forecast run: download tp, validate, convert to interval precipitation, store.

Store layout (backend/data/store/<model>/<YYYYMMDDHH>/):
  tp.grib2                 raw ECMWF messages (kept for provenance/reprocessing)
  interval_<a>_<b>.npz     float32 mm that fell between steps a and b (lat desc, lon asc)
  cumulative_<b>.npz       float32 mm accumulated 0 -> b
  manifest.json            run metadata, source URLs, per-interval QC diagnostics
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .acquisition.ecmwf import fetch_tp, latest_run
from .processing.precip import interval, read_tp

log = logging.getLogger(__name__)
DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "store"


def run_dir(model: str, run: datetime, root: Path = DATA_ROOT) -> Path:
    return root / model / run.strftime("%Y%m%d%H")


def process_file(grib: Path, out: Path, model: str, retrieval: dict | None = None) -> dict:
    """Decode a tp GRIB file and write cumulative + interval grids and a manifest."""
    fields = read_tp(grib)
    f0 = fields[0]
    if f0.end_step != 0:
        # Step 0 is identically zero by definition; we still require it so the first
        # interval is computed from data rather than an assumption.
        raise ValueError("step 0 must be included to compute the first interval")
    run = datetime.strptime(f"{f0.run_date}{f0.run_time:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "lats.npy", f0.lats.astype(np.float32))
    np.save(out / "lons.npy", f0.lons.astype(np.float32))

    intervals = []
    for f in fields[1:]:
        np.savez_compressed(out / f"cumulative_{f.end_step}.npz", v=f.values_mm.astype(np.float32))
    for a, b in zip(fields, fields[1:]):
        iv = interval(a, b)
        np.savez_compressed(out / f"interval_{a.end_step}_{b.end_step}.npz", v=iv.values_mm.astype(np.float32))
        intervals.append({
            "start_step": a.end_step, "end_step": b.end_step,
            "valid_from": (run + timedelta(hours=a.end_step)).isoformat(),
            "valid_to": (run + timedelta(hours=b.end_step)).isoformat(),
            "n_clamped_negatives": iv.n_clamped,
            "min_raw_diff_mm": iv.min_raw_mm,
            "packing_tolerance_mm": max(a.quantum_mm, b.quantum_mm),
            "max_mm": float(np.nanmax(iv.values_mm)),
        })

    manifest = {
        "model": model,
        "run": run.isoformat(),
        "variable": "tp (total precipitation: rain + snow, liquid water equivalent)",
        "units_source": f0.meta["units"],
        "units_output": "mm",
        "accumulation": "from forecast start; intervals = differences of consecutive steps",
        "steps": [f.end_step for f in fields],
        "grid": {"type": f0.meta["gridType"], "ni": len(f0.lons), "nj": len(f0.lats),
                 "lat_first": float(f0.lats[0]), "lat_last": float(f0.lats[-1]),
                 "lon_first": float(f0.lons[0]), "lon_last": float(f0.lons[-1])},
        "intervals": intervals,
        "retrieval": retrieval,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "Contains ECMWF Open Data (CC BY 4.0). Forecast, not observations.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def ingest(model: str = "ifs", steps: list[int] | None = None, run: datetime | None = None,
           root: Path = DATA_ROOT) -> dict:
    steps = sorted(set(steps or [0, 6, 12, 18, 24]))
    if steps[0] != 0:
        steps = [0] + steps
    run = run or latest_run(model, last_step=steps[-1])
    out = run_dir(model, run, root)
    retrieval = fetch_tp(run, steps, out / "tp.grib2", model=model)
    return process_file(out / "tp.grib2", out, model, retrieval.to_dict())
