"""M0 verification: download a few real ECMWF IFS `tp` steps and decode them.

Decodes the same file two independent ways (raw ecCodes and cfgrib/xarray)
and prints the GRIB metadata needed to validate the processing pipeline
(units, stepRange, grid geometry, scan order).

Usage:  python scripts/verify_grib.py [--model ifs] [--steps 0 6 12]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import eccodes
import numpy as np
import xarray as xr
from ecmwf.opendata import Client

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "verify"

GRID_KEYS = [
    "shortName", "name", "units", "dataDate", "dataTime", "stepType", "stepRange",
    "startStep", "endStep", "stepUnits", "gridType", "Ni", "Nj",
    "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
    "latitudeOfLastGridPointInDegrees", "longitudeOfLastGridPointInDegrees",
    "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees",
    "iScansNegatively", "jScansPositively", "bitmapPresent", "packingType",
    "bitsPerValue", "missingValue", "centre", "edition",
]


def download(model: str, steps: list[int]) -> tuple[Path, object]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_DIR / f"{model}_tp_{'-'.join(map(str, steps))}.grib2"
    client = Client(source="ecmwf", model=model, resol="0p25")
    result = client.retrieve(type="fc", stream="oper", param="tp", step=steps, target=str(target))
    return target, result


def decode_eccodes(path: Path) -> list[dict]:
    out = []
    with path.open("rb") as f:
        while (gid := eccodes.codes_grib_new_from_file(f)) is not None:
            try:
                meta = {k: eccodes.codes_get(gid, k) for k in GRID_KEYS}
                vals = eccodes.codes_get_values(gid)
                meta["n_values"] = vals.size
                meta["min"], meta["max"], meta["mean"] = float(vals.min()), float(vals.max()), float(vals.mean())
                meta["n_negative"] = int((vals < 0).sum())
                meta["_values"] = vals
                out.append(meta)
            finally:
                eccodes.codes_release(gid)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="ifs")
    ap.add_argument("--steps", type=int, nargs="+", default=[0, 6, 12])
    args = ap.parse_args()

    path, result = download(args.model, args.steps)
    print(f"Downloaded: {path}  ({path.stat().st_size / 1e6:.2f} MB)")
    print(f"Run: {result.datetime:%Y-%m-%d %H:%M} UTC  urls: {result.urls}")

    msgs = decode_eccodes(path)
    print(f"\n[ecCodes] {len(msgs)} messages")
    for m in msgs:
        print("-" * 60)
        for k in GRID_KEYS + ["n_values", "min", "max", "mean", "n_negative"]:
            print(f"  {k:38s} {m[k]}")

    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    print(f"\n[cfgrib] dataset:\n{ds}")
    tp = ds["tp"]
    print(f"  attrs.units={tp.attrs.get('units')}  GRIB_stepType={tp.attrs.get('GRIB_stepType')}")
    print(f"  lat {float(ds.latitude[0])} .. {float(ds.latitude[-1])}  "
          f"lon {float(ds.longitude[0])} .. {float(ds.longitude[-1])}")

    # Cross-check: both decoders must yield identical values for every step.
    ok = True
    for i, m in enumerate(msgs):
        a = m["_values"].reshape(m["Nj"], m["Ni"])
        b = tp.isel(step=i).values if "step" in tp.dims else tp.values
        same = np.allclose(a, b, equal_nan=True, rtol=0, atol=0)
        ok &= same
        print(f"  step {m['stepRange']:>6}: ecCodes == cfgrib -> {same}")

    # Accumulation sanity: tp accumulated from step 0 must be non-decreasing (up to packing noise).
    for prev, cur in zip(msgs, msgs[1:]):
        d = cur["_values"] - prev["_values"]
        print(f"  diff {prev['stepRange']}->{cur['stepRange']}: min={d.min():.3e} m  "
              f"max={d.max()*1000:.1f} mm  n<0={(d < 0).sum()}")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
