"""Web Mercator (EPSG:3857) XYZ tiles rendered from a regular lat/lon precipitation grid.

Every output pixel is reprojected: its centre in global Web-Mercator pixel space is
inverted to (lat, lon) and the containing 0.25° grid cell is looked up (nearest cell,
no interpolation, so each pixel shows a genuine model value). Because Mercator is
separable (lat depends only on the pixel row, lon only on the column), a tile is a
single numpy fancy-index: values[rows[:, None], cols[None, :]].

Colours use one absolute scale (mm accumulated in the window) for every tile.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

TILE = 256
MAX_ZOOM = 10            # 0.25° ≈ 28 km; z10 pixels are ~150 m, deeper adds nothing
MERCATOR_MAX_LAT = math.degrees(math.atan(math.sinh(math.pi)))   # 85.0511...

# ---------------------------------------------------------------- absolute colour scale
# Lower bin edges in mm (accumulated over the selected window). Below the first edge
# (< 0.1 mm, the WMO "trace" threshold) is transparent. The last bin is open-ended.
BINS_MM = [0.1, 0.5, 1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 250]
COLORS_RGBA = [
    (166, 214, 255, 110),   # 0.1–0.5   trace / very light
    (115, 182, 250, 150),   # 0.5–1
    (64, 145, 240, 180),    # 1–2       light
    (28, 104, 214, 200),    # 2–5
    (20, 168, 92, 210),     # 5–10      moderate
    (128, 204, 40, 215),    # 10–20
    (250, 228, 40, 220),    # 20–30     heavy
    (250, 168, 30, 225),    # 30–50
    (238, 98, 22, 230),     # 50–75     very heavy
    (214, 30, 34, 235),     # 75–100
    (186, 0, 112, 240),     # 100–150   extreme
    (140, 0, 180, 245),     # 150–250
    (255, 170, 255, 250),   # >= 250    exceptional
]
LABELS = ["Trace", "", "Light", "", "Moderate", "", "Heavy", "", "Very heavy", "",
          "Extreme", "", "Exceptional"]
assert len(BINS_MM) == len(COLORS_RGBA) == len(LABELS)

# Palette index 0 = transparent (dry or missing); index k = bin k-1.
_PALETTE = [(0, 0, 0, 0)] + COLORS_RGBA
_PALETTE_FLAT = bytes(c for rgba in _PALETTE for c in rgba)
_EDGES = np.asarray(BINS_MM, dtype=np.float64)


def colorscale() -> dict:
    """The single source of truth for the frontend legend."""
    return {
        "units": "mm accumulated over the selected window",
        "transparent_below_mm": BINS_MM[0],
        "bins": [{
            "min_mm": lo,
            "max_mm": BINS_MM[k + 1] if k + 1 < len(BINS_MM) else None,
            "rgba": list(COLORS_RGBA[k]),
            "hex": "#{:02x}{:02x}{:02x}".format(*COLORS_RGBA[k][:3]),
            "label": LABELS[k],
        } for k, lo in enumerate(BINS_MM)],
    }


def classify(values: np.ndarray) -> np.ndarray:
    """mm -> palette index (uint8). NaN and < 0.1 mm -> 0 (transparent)."""
    v = np.nan_to_num(values, nan=-1.0)
    return np.searchsorted(_EDGES, v, side="right").astype(np.uint8)


# ---------------------------------------------------------------- geometry
@dataclass(frozen=True)
class Grid:
    """Regular grid: lat[j] = lat0 - j*dlat (descending), lon[i] = lon0 + i*dlon, global in lon."""
    lat0: float
    dlat: float
    nlat: int
    lon0: float
    dlon: float
    nlon: int

    @classmethod
    def from_axes(cls, lats: np.ndarray, lons: np.ndarray) -> "Grid":
        dlat, dlon = float(lats[0] - lats[1]), float(lons[1] - lons[0])
        if not (dlat > 0 and np.allclose(np.diff(lats), -dlat) and np.allclose(np.diff(lons), dlon)):
            raise ValueError("grid must be regular, latitude descending, longitude ascending")
        if abs(dlon * lons.size - 360) > 1e-6:
            raise ValueError("tiles need a global longitude axis")
        return cls(float(lats[0]), dlat, lats.size, float(lons[0]), dlon, lons.size)


def pixel_lat(gy: np.ndarray, z: int) -> np.ndarray:
    """Latitude (deg) of global pixel-space y coordinates at zoom z (inverse Web Mercator)."""
    n = TILE * 2 ** z
    return np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * gy / n))))


def pixel_lon(gx: np.ndarray, z: int) -> np.ndarray:
    n = TILE * 2 ** z
    return gx / n * 360.0 - 180.0


def rows_cols(grid: Grid, z: int, px0: int, py0: int, w: int, h: int):
    """Grid indices for a w×h block whose top-left global pixel is (px0, py0)."""
    lat = pixel_lat(py0 + np.arange(h) + 0.5, z)       # pixel centres
    lon = pixel_lon(px0 + np.arange(w) + 0.5, z)
    rows = np.rint((grid.lat0 - lat) / grid.dlat).astype(np.int64)
    valid_rows = (rows >= 0) & (rows < grid.nlat)
    cols = np.rint((lon - grid.lon0) / grid.dlon).astype(np.int64) % grid.nlon   # wraps at ±180
    return np.clip(rows, 0, grid.nlat - 1), valid_rows, cols


def render_block(values: np.ndarray, grid: Grid, z: int, px0: int, py0: int, w: int, h: int) -> np.ndarray:
    """Palette indices (h×w uint8) for any block of global pixel space. Tiles are 256×256 blocks."""
    rows, valid_rows, cols = rows_cols(grid, z, px0, py0, w, h)
    idx = classify(values[rows[:, None], cols[None, :]])
    idx[~valid_rows, :] = 0
    return idx


def encode_png(idx: np.ndarray) -> bytes:
    im = Image.fromarray(idx, mode="P")
    im.putpalette(_PALETTE_FLAT, rawmode="RGBA")
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def render_tile(values: np.ndarray, grid: Grid, z: int, x: int, y: int) -> bytes:
    if not (0 <= z <= MAX_ZOOM):
        raise ValueError(f"z must be between 0 and {MAX_ZOOM}")
    n = 2 ** z
    if not (0 <= x < n and 0 <= y < n):
        raise ValueError(f"x and y must be in [0, {n - 1}] at z={z}")
    return encode_png(render_block(values, grid, z, x * TILE, y * TILE, TILE, TILE))


EMPTY_TILE = encode_png(np.zeros((TILE, TILE), dtype=np.uint8))
