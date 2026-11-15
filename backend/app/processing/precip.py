"""GRIB2 total-precipitation decoding and scientific processing.

Conventions (all validated from GRIB headers, never assumed):
- `tp` is accumulated from forecast start (stepType=accum, startStep=0), in metres (IFS)
  or kg m**-2 (AIFS); both are converted to mm using the units key of each message.
- Output grids are mm, latitude descending (north->south), longitude in [-180, 180) ascending.
- Interval precipitation P(t1->t2) = tp(0->t2) - tp(0->t1).
  Small negatives produced by GRIB packing (lossy quantisation) are clamped to 0 only when
  their magnitude is within the packing precision of the two fields; anything larger is a
  real data inconsistency and raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import eccodes
import numpy as np

M_TO_MM = 1000.0
# Accepted `tp` units and their factor to mm of water. IFS encodes tp in metres (paramId 228);
# AIFS encodes it in kg m**-2 (paramId 228228), and 1 kg m**-2 of water == 1 mm.
UNIT_TO_MM = {"m": M_TO_MM, "kg m**-2": 1.0}
# Relative slack on the packing quantum to absorb float rounding in the subtraction itself.
_QUANTUM_SLACK = 1e-6


class GribValidationError(ValueError):
    """GRIB message does not match what the pipeline expects."""


class DataInconsistencyError(ValueError):
    """Values are physically inconsistent beyond packing precision."""


@dataclass
class TpField:
    """One accumulated tp field (0 -> end_step hours), in mm, on a normalised grid."""
    run_date: int           # YYYYMMDD
    run_time: int           # HHMM
    start_step: int
    end_step: int
    lats: np.ndarray        # (Nj,) descending
    lons: np.ndarray        # (Ni,) ascending in [-180, 180)
    values_mm: np.ndarray   # (Nj, Ni) float64, NaN = missing
    quantum_mm: float       # GRIB packing precision of this field, mm
    meta: dict


@dataclass
class IntervalField:
    start_step: int
    end_step: int
    lats: np.ndarray
    lons: np.ndarray
    values_mm: np.ndarray
    n_clamped: int          # packing-noise negatives set to 0
    min_raw_mm: float       # most negative raw difference before clamping (diagnostic)


_KEYS = (
    "shortName", "units", "stepType", "stepRange", "startStep", "endStep", "stepUnits",
    "gridType", "Ni", "Nj", "dataDate", "dataTime",
    "latitudeOfFirstGridPointInDegrees", "latitudeOfLastGridPointInDegrees",
    "longitudeOfFirstGridPointInDegrees", "longitudeOfLastGridPointInDegrees",
    "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees",
    "iScansNegatively", "jScansPositively", "jPointsAreConsecutive",
    "bitmapPresent", "missingValue", "binaryScaleFactor", "decimalScaleFactor", "bitsPerValue",
)


def validate_meta(m: dict) -> None:
    """Fail loudly if a message is not accumulated total precipitation on a regular grid."""
    checks = {
        "shortName == tp": m["shortName"] == "tp",
        "units in (m, kg m**-2)": m["units"] in UNIT_TO_MM,
        "stepType == accum": m["stepType"] == "accum",
        "startStep == 0 (accumulated from forecast start)": m["startStep"] == 0,
        "stepUnits == hours": m["stepUnits"] == 1,
        "gridType == regular_ll": m["gridType"] == "regular_ll",
        "row-major (jPointsAreConsecutive == 0)": m["jPointsAreConsecutive"] == 0,
        "i increment * Ni == 360": abs(m["iDirectionIncrementInDegrees"] * m["Ni"] - 360) < 1e-6,
        "j increment * (Nj-1) == lat span": abs(
            m["jDirectionIncrementInDegrees"] * (m["Nj"] - 1)
            - abs(m["latitudeOfFirstGridPointInDegrees"] - m["latitudeOfLastGridPointInDegrees"])
        ) < 1e-6,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise GribValidationError(f"tp message step {m.get('stepRange')} failed: {failed}")


def packing_quantum_m(m: dict) -> float:
    """Precision of simple/CCSDS-packed GRIB values: 2**E * 10**-D, in the field's own units.

    A field with bitsPerValue == 0 is constant (e.g. tp at step 0 == 0) and is stored
    exactly, so its quantum is 0 - the scale factors are meaningless in that case.
    """
    if m["bitsPerValue"] == 0:
        return 0.0
    return 2.0 ** m["binaryScaleFactor"] * 10.0 ** (-m["decimalScaleFactor"])


def _axes(m: dict) -> tuple[np.ndarray, np.ndarray]:
    """Latitude/longitude of the native grid order, derived from the header."""
    di, dj = m["iDirectionIncrementInDegrees"], m["jDirectionIncrementInDegrees"]
    lat0, lon0 = m["latitudeOfFirstGridPointInDegrees"], m["longitudeOfFirstGridPointInDegrees"]
    jsign = 1.0 if m["jScansPositively"] else -1.0
    isign = -1.0 if m["iScansNegatively"] else 1.0
    lats = lat0 + jsign * dj * np.arange(m["Nj"])
    lons = lon0 + isign * di * np.arange(m["Ni"])
    return lats, lons


def normalise_grid(values: np.ndarray, lats: np.ndarray, lons: np.ndarray):
    """Reorder to latitude descending and longitude ascending in [-180, 180)."""
    lons = ((lons + 180.0) % 360.0) - 180.0
    order = np.argsort(lons, kind="stable")
    lons, values = lons[order], values[:, order]
    if lats[0] < lats[-1]:
        lats, values = lats[::-1], values[::-1, :]
    return np.ascontiguousarray(values), lats.copy(), lons


def _next_message(f):
    """Next GRIB handle from an open file, or None at EOF.

    ecCodes read errors are re-raised as GribValidationError *after* leaving the except
    block: the original traceback references ecCodes frames that keep its native file
    handle open, which leaks the handle and (on Windows) blocks deleting the file.
    ecCodes ties that handle to the Python file object, so callers must also drop their
    reference to `f` when an error propagates (see iter_tp).
    """
    try:
        return eccodes.codes_grib_new_from_file(f)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    del f
    raise GribValidationError(f"undecodable GRIB message: {err}")


def _decode(gid) -> TpField:
    m = {k: eccodes.codes_get(gid, k) for k in _KEYS}
    validate_meta(m)
    vals = eccodes.codes_get_values(gid).astype(np.float64).reshape(m["Nj"], m["Ni"])
    if m["bitmapPresent"]:
        vals[vals == m["missingValue"]] = np.nan
    lats, lons = _axes(m)
    vals, lats, lons = normalise_grid(vals, lats, lons)
    return TpField(
        run_date=m["dataDate"], run_time=m["dataTime"],
        start_step=m["startStep"], end_step=m["endStep"],
        lats=lats, lons=lons, values_mm=vals * UNIT_TO_MM[m["units"]],
        quantum_mm=packing_quantum_m(m) * UNIT_TO_MM[m["units"]], meta=m,
    )


def iter_tp(path: str | Path):
    """Yield validated tp fields one at a time, in file order (bounded memory).

    Requires a single forecast run and strictly increasing steps, which is how
    app.acquisition writes files. Use read_tp() for arbitrary message order.
    """
    first = prev = None
    f = open(path, "rb")
    try:
        while (gid := _next_message(f)) is not None:
            try:
                fld = _decode(gid)
            finally:
                eccodes.codes_release(gid)
            if first is None:
                first = fld
            elif (fld.run_date, fld.run_time) != (first.run_date, first.run_time):
                raise GribValidationError("file mixes forecast runs")
            if prev is not None and fld.end_step <= prev.end_step:
                raise GribValidationError(f"steps not strictly increasing: {prev.end_step} -> {fld.end_step}")
            prev = fld
            yield fld
    finally:
        f.close()
        f = None  # release ecCodes' native handle even while an exception propagates
    if first is None:
        raise GribValidationError(f"no tp messages in {path}")


def read_tp(path: str | Path) -> list[TpField]:
    """Decode and validate every tp message in a GRIB2 file, sorted by end step."""
    fields: list[TpField] = []
    f = open(path, "rb")
    try:
        while (gid := _next_message(f)) is not None:
            try:
                fields.append(_decode(gid))
            finally:
                eccodes.codes_release(gid)
    finally:
        f.close()
        f = None  # see iter_tp
    if not fields:
        raise GribValidationError(f"no tp messages in {path}")
    runs = {(x.run_date, x.run_time) for x in fields}
    if len(runs) != 1:
        raise GribValidationError(f"file mixes forecast runs: {sorted(runs)}")
    fields.sort(key=lambda x: x.end_step)
    steps = [x.end_step for x in fields]
    if len(set(steps)) != len(steps):
        raise GribValidationError(f"duplicate steps: {steps}")
    return fields


def interval(a: TpField, b: TpField) -> IntervalField:
    """Precipitation that fell between a.end_step and b.end_step (mm).

    Negative differences within the combined packing precision are numerical noise and
    clamped to 0. A larger negative means the accumulated field *decreased*, which is
    physically impossible -> DataInconsistencyError.
    """
    if b.end_step <= a.end_step:
        raise ValueError(f"interval end {b.end_step} must be after start {a.end_step}")
    if (a.run_date, a.run_time) != (b.run_date, b.run_time):
        raise ValueError("fields belong to different forecast runs")
    if not (np.array_equal(a.lats, b.lats) and np.array_equal(a.lons, b.lons)):
        raise ValueError("fields are on different grids")

    diff = b.values_mm - a.values_mm
    tol = max(a.quantum_mm, b.quantum_mm) * (1 + _QUANTUM_SLACK)
    with np.errstate(invalid="ignore"):
        neg = diff < 0
        bad = diff < -tol
    if bad.any():
        j, i = np.unravel_index(np.nanargmin(diff), diff.shape)
        raise DataInconsistencyError(
            f"tp decreased by {-diff[j, i]:.4f} mm (> packing tolerance {tol:.4f} mm) between "
            f"steps {a.end_step} and {b.end_step} at lat={a.lats[j]}, lon={a.lons[i]} "
            f"({int(bad.sum())} cells)"
        )
    min_raw = float(np.nanmin(diff)) if np.isfinite(diff).any() else float("nan")
    out = np.where(neg, 0.0, diff)
    return IntervalField(a.end_step, b.end_step, a.lats, a.lons, out, int(neg.sum()), min_raw)


# ---------------------------------------------------------------- regional tools

def subset(values: np.ndarray, lats: np.ndarray, lons: np.ndarray,
           bbox: tuple[float, float, float, float]):
    """Cut a (west, south, east, north) box. west > east means the box crosses the antimeridian."""
    w, s, e, n = bbox
    if not (-90 <= s < n <= 90):
        raise ValueError(f"invalid latitude bounds south={s} north={n}")
    if not (-180 <= w <= 180 and -180 <= e <= 180) or w == e:
        raise ValueError(f"invalid longitude bounds west={w} east={e}")
    rows = (lats >= s) & (lats <= n)
    cols = (lons >= w) & (lons <= e) if w < e else (lons >= w) | (lons <= e)
    if not rows.any() or not cols.any():
        raise ValueError("bbox contains no grid points")
    col_idx = np.flatnonzero(cols)
    if w > e:  # keep geographic west->east order across the dateline
        col_idx = np.concatenate([col_idx[lons[col_idx] >= w], col_idx[lons[col_idx] <= e]])
    return values[np.ix_(rows, col_idx)], lats[rows], lons[col_idx]


def area_weighted_stats(values: np.ndarray, lats: np.ndarray, wet_threshold_mm: float = 1.0) -> dict:
    """Area-weighted mean (cos(latitude) weights) plus min/max over valid cells.

    On a regular lat/lon grid a cell's area is proportional to cos(lat); an unweighted
    mean would over-represent high latitudes. Missing (NaN) cells are excluded.
    """
    w = np.broadcast_to(np.cos(np.deg2rad(lats))[:, None], values.shape)
    valid = np.isfinite(values)
    n_valid = int(valid.sum())
    if n_valid == 0:
        return {"n_cells": int(values.size), "n_valid": 0, "min_mm": None, "max_mm": None,
                "mean_mm": None, "wet_area_fraction": None}
    v, wv = values[valid], w[valid]
    wsum = wv.sum()
    return {
        "n_cells": int(values.size),
        "n_valid": n_valid,
        "min_mm": float(v.min()),
        "max_mm": float(v.max()),
        "mean_mm": float((v * wv).sum() / wsum) if wsum > 0 else float(v.mean()),
        "wet_area_fraction": float(wv[v >= wet_threshold_mm].sum() / wsum) if wsum > 0 else None,
        "wet_threshold_mm": wet_threshold_mm,
    }


def point_value(values: np.ndarray, lats: np.ndarray, lons: np.ndarray, lat: float, lon: float):
    """Nearest-grid-cell value (no interpolation) and the cell centre actually used."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range")
    lon = ((lon + 180.0) % 360.0) - 180.0
    j = int(np.abs(lats - lat).argmin())
    dlon = np.abs(((lons - lon) + 180.0) % 360.0 - 180.0)  # wrap-aware distance
    i = int(dlon.argmin())
    v = values[j, i]
    return (None if np.isnan(v) else float(v)), float(lats[j]), float(lons[i])
