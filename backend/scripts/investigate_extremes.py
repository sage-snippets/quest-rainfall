"""Investigate extreme interval precipitation in a processed run.

For the hottest cells: location, neighbourhood (is it a single-pixel spike or a coherent
rain band?), temporal evolution across steps, and independent dynamical context from the
same run (mean sea-level pressure and 10 m wind) to see whether a synoptic system exists.

Usage: python scripts/investigate_extremes.py data/store/ifs/2026111500
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import eccodes
import numpy as np
from ecmwf.opendata import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.processing.precip import normalise_grid, _axes  # noqa: E402

d = Path(sys.argv[1])
man = json.loads((d / "manifest.json").read_text())
lats, lons = np.load(d / "lats.npy"), np.load(d / "lons.npy")
ivs = {(i["start_step"], i["end_step"]): np.load(d / f"interval_{i['start_step']}_{i['end_step']}.npz")["v"]
       for i in man["intervals"]}

print("== Global maximum per interval (mm)")
for (a, b), v in ivs.items():
    j, i = np.unravel_index(np.argmax(v), v.shape)
    print(f"  {a:>2}-{b:<2}h  {v[j, i]:6.1f} mm/{b-a}h at {lats[j]:6.2f}N {lons[i]:7.2f}E  "
          f"cells>50mm: {(v > 50).sum():4d}  >100mm: {(v > 100).sum():3d}")

# Focus box: south-west Bay of Bengal / Sri Lanka
box = (78.0, 4.0, 90.0, 16.0)
rows = (lats >= box[1]) & (lats <= box[3]); cols = (lons >= box[0]) & (lons <= box[2])

v = ivs[(0, 6)] if (0, 6) in ivs else ivs[(0, 3)] + ivs[(3, 6)]
sub = v[np.ix_(rows, cols)]
j, i = np.unravel_index(np.argmax(sub), sub.shape)
J, I = np.flatnonzero(rows)[j], np.flatnonzero(cols)[i]
print(f"\n== 0-6h max in Bay of Bengal box: {v[J, I]:.1f} mm at {lats[J]:.2f}N {lons[I]:.2f}E")
print("  7x7 neighbourhood (mm/6h), rows N->S, cols W->E:")
for jj in range(J - 3, J + 4):
    print("   " + " ".join(f"{v[jj, ii]:6.1f}" for ii in range(I - 3, I + 4)))
print(f"  cells in box > 100 mm/6h: {(sub > 100).sum()}, > 50: {(sub > 50).sum()}, > 20: {(sub > 20).sum()}")

print("\n== Track of the box maximum through the forecast (3-hourly)")
for (a, b), vv in ivs.items():
    s = vv[np.ix_(rows, cols)]
    jj, ii = np.unravel_index(np.argmax(s), s.shape)
    print(f"  {a:>2}-{b:<2}h  max {s[jj, ii]:6.1f} mm at {lats[rows][jj]:6.2f}N {lons[cols][ii]:6.2f}E   "
          f"box mean {s.mean():5.2f} mm   value at original cell {vv[J, I]:6.1f}")

print("\n== Dynamical context from the same run (msl, 10u, 10v)")
run = datetime.fromisoformat(man["run"])
tgt = Path("data/verify/ctx_msl_wind.grib2")
Client(source="ecmwf", model="ifs", resol="0p25").retrieve(
    date=run.strftime("%Y%m%d"), time=run.hour, type="fc", stream="oper",
    param=["msl", "10u", "10v"], step=[6, 24, 48], target=str(tgt))
fields = {}
with tgt.open("rb") as f:
    while (g := eccodes.codes_grib_new_from_file(f)) is not None:
        m = {k: eccodes.codes_get(g, k) for k in ("shortName", "units", "endStep", "Ni", "Nj",
             "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
             "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees",
             "iScansNegatively", "jScansPositively")}
        vals = eccodes.codes_get_values(g).reshape(m["Nj"], m["Ni"]); eccodes.codes_release(g)
        la, lo = _axes(m); vals, la, lo = normalise_grid(vals, la, lo)
        fields[(m["shortName"], m["endStep"])] = (vals, m["units"])
for step in (6, 24, 48):
    msl, u = fields[("msl", step)][0][np.ix_(rows, cols)] / 100, fields[("10u", step)][0][np.ix_(rows, cols)]
    ws = np.hypot(u, fields[("10v", step)][0][np.ix_(rows, cols)])
    jj, ii = np.unravel_index(np.argmin(msl), msl.shape)
    print(f"  T+{step:>2}h  min MSLP {msl.min():7.1f} hPa at {lats[rows][jj]:5.2f}N {lons[cols][ii]:5.2f}E   "
          f"box median MSLP {np.median(msl):7.1f}   max 10m wind {ws.max():4.1f} m/s")
pr = fields[("msl", 6)][1]
print(f"  (msl units in GRIB: {pr}; converted to hPa)")
