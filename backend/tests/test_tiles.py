"""Spatial validation of Web Mercator tiles.

The renderer uses the *inverse* Mercator transform (pixel -> lat/lon). These tests use
the independent *forward* transform (lat/lon -> pixel) to predict exactly which pixels a
grid cell must cover, then compare with the rendered PNG.
"""
import io
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.pipeline import process_file
from app.processing import tiles
from app.processing.tiles import COLORS_RGBA, TILE, Grid, classify, render_block, render_tile

FIXTURE = Path(__file__).parent / "fixtures" / "ifs_tp_20261115_00z_steps_0_6_12.grib2"
LATS = 90 - 0.25 * np.arange(721)
LONS = -180 + 0.25 * np.arange(1440)
GRID = Grid.from_axes(LATS, LONS)


# ------------------------------------------------------------- independent reference

def fwd_x(lon, z):
    return (lon + 180.0) / 360.0 * TILE * 2 ** z


def fwd_y(lat, z):
    r = math.radians(lat)
    return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * TILE * 2 ** z


def decode(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def palette_index(png: bytes) -> np.ndarray:
    im = Image.open(io.BytesIO(png))
    assert im.mode == "P"
    return np.asarray(im)


def field_with(cells: dict[tuple[float, float], float]) -> np.ndarray:
    v = np.zeros((721, 1440))
    for (lat, lon), mm in cells.items():
        v[int(round((90 - lat) / 0.25)), int(round((lon + 180) / 0.25))] = mm
    return v


# ------------------------------------------------------------- slippy-map reference values

@pytest.mark.parametrize("lat,lon,z,tile_xy", [
    (12.97, 77.59, 8, (183, 118)),     # Bengaluru
    (28.61, 77.21, 6, (45, 26)),       # Delhi
    (51.48, 0.0, 10, (512, 340)),      # Greenwich
    (-33.87, 151.21, 5, (29, 19)),     # Sydney (southern + eastern)
    (40.71, -74.01, 7, (37, 48)),      # New York (western)
])
def test_reference_tiles(lat, lon, z, tile_xy):
    assert (int(fwd_x(lon, z) // TILE), int(fwd_y(lat, z) // TILE)) == tile_xy


# ------------------------------------------------------------- feature placement

@pytest.mark.parametrize("lat,lon,z", [
    (13.0, 77.5, 8),      # Bengaluru cell, NE hemisphere
    (13.0, 77.5, 10),
    (-33.5, -70.75, 7),   # Santiago, SW hemisphere: catches N/S or E/W flips
    (60.0, 10.75, 6),     # high latitude: Mercator stretching
    (0.0, 0.0, 9),        # cell straddling the equator and prime meridian
])
def test_single_cell_lands_on_exact_pixels(lat, lon, z):
    v = field_with({(lat, lon): 60.0})                 # 50-75 mm bin
    # Exact pixel footprint of the cell [lat±0.125] x [lon±0.125] via forward Mercator
    x0, x1 = fwd_x(lon - 0.125, z), fwd_x(lon + 0.125, z)
    y0, y1 = fwd_y(lat + 0.125, z), fwd_y(lat - 0.125, z)    # north edge has smaller y
    px0, py0 = int(x0) - 4, int(y0) - 4
    w, h = int(x1) - px0 + 5, int(y1) - py0 + 5
    idx = render_block(v, GRID, z, px0, py0, w, h)
    cx = px0 + np.arange(w) + 0.5
    cy = py0 + np.arange(h) + 0.5
    expected = ((cy[:, None] > y0) & (cy[:, None] < y1)) & ((cx[None, :] > x0) & (cx[None, :] < x1))
    assert expected.any()
    np.testing.assert_array_equal(idx > 0, expected)
    assert set(np.unique(idx[idx > 0])) == {9}         # 60 mm -> bin index 9 (50-75)

    # ... and the same pixels through the real tile path (PNG encode/decode, tile offsets)
    tx, ty = int(cx[w // 2] // TILE), int(cy[h // 2] // TILE)
    rgba = decode(render_tile(v, GRID, z, tx, ty))
    jy, ix = int(cy[h // 2]) - ty * TILE, int(cx[w // 2]) - tx * TILE
    assert tuple(rgba[jy, ix]) == COLORS_RGBA[8]


def test_north_is_up_and_east_is_right():
    v = field_with({(40.0, 20.0): 5.0, (20.0, 20.0): 30.0, (30.0, 10.0): 1.0, (30.0, 30.0): 150.0})
    z = 3
    tile_ = palette_index(render_tile(v, GRID, z, 4, 3))   # covers ~0-45E, 0-41N
    pos = {k: np.argwhere(tile_ == k).mean(axis=0) for k in (5, 8, 3, 12)}  # bins of 5, 30, 1, 150 mm
    assert pos[5][0] < pos[8][0]        # 40N above 20N
    assert pos[3][1] < pos[12][1]       # 10E left of 30E


def test_antimeridian_cell_wraps_to_both_edges():
    # Cell centred on lon -180 spans 179.875E..180 and -180..-179.875
    v = field_with({(0.0, -180.0): 10.0})
    z = 4
    west_edge = palette_index(render_tile(v, GRID, z, 0, 8))
    east_edge = palette_index(render_tile(v, GRID, z, 2 ** z - 1, 8))
    assert west_edge[0, 0] > 0 and east_edge[0, TILE - 1] > 0
    assert west_edge[0, TILE - 1] == 0 and east_edge[0, 0] == 0
    # footprint width in pixels: 0.125° each side at z=4 -> 4096*0.125/360 = 1.42 px
    assert (west_edge[0] > 0).sum() == 1 and (east_edge[0] > 0).sum() == 1


# ------------------------------------------------------------- seams

@pytest.mark.parametrize("z,x,y", [(5, 22, 14), (7, 91, 58), (2, 3, 1)])
def test_adjacent_tiles_are_seamless(z, x, y):
    rng = np.random.default_rng(z)
    v = rng.gamma(0.3, 20.0, size=(721, 1440))
    v[rng.random(v.shape) < 0.01] = np.nan
    n = 2 ** z
    tl, tr = palette_index(render_tile(v, GRID, z, x, y)), palette_index(render_tile(v, GRID, z, (x + 1) % n, y))
    bl, br = palette_index(render_tile(v, GRID, z, x, y + 1)), palette_index(render_tile(v, GRID, z, (x + 1) % n, y + 1))
    stitched = np.block([[tl, tr], [bl, br]])
    if x + 1 < n:
        np.testing.assert_array_equal(stitched, render_block(v, GRID, z, x * TILE, y * TILE, 2 * TILE, 2 * TILE))


def test_world_wraps_seamlessly_across_antimeridian():
    rng = np.random.default_rng(1)
    v = rng.gamma(0.3, 20.0, size=(721, 1440))
    z, n = 3, 8
    east = palette_index(render_tile(v, GRID, z, n - 1, 3))
    west = palette_index(render_tile(v, GRID, z, 0, 3))
    # One block straddling the dateline: last tile's columns then first tile's
    block = np.hstack([render_block(v, GRID, z, (n - 1) * TILE, 3 * TILE, TILE, TILE),
                       render_block(v, GRID, z, 0, 3 * TILE, TILE, TILE)])
    np.testing.assert_array_equal(np.hstack([east, west]), block)


# ------------------------------------------------------------- colour scale

def test_classification_bin_edges():
    v = np.array([np.nan, -0.5, 0.0, 0.0999, 0.1, 0.5, 4.99, 5.0, 99.99, 100.0, 249.9, 250.0, 1e4])
    np.testing.assert_array_equal(classify(v), [0, 0, 0, 0, 1, 2, 4, 5, 10, 11, 12, 13, 13])


def test_colour_scale_is_absolute_across_tiles():
    # The same 20 mm value must get the same colour regardless of the tile's other values.
    a = field_with({(13.0, 77.5): 20.0})
    b = field_with({(13.0, 77.5): 20.0, (13.5, 77.5): 900.0})
    z, tx, ty = 8, 183, 118
    pa, pb = decode(render_tile(a, GRID, z, tx, ty)), decode(render_tile(b, GRID, z, tx, ty))
    j, i = int(fwd_y(13.0, z)) - ty * TILE, int(fwd_x(77.5, z)) - tx * TILE
    assert tuple(pa[j, i]) == tuple(pb[j, i]) == COLORS_RGBA[6]


def test_dry_and_missing_are_fully_transparent():
    v = np.full((721, 1440), np.nan); v[:360] = 0.0
    assert decode(render_tile(v, GRID, 0, 0, 0))[..., 3].max() == 0
    assert render_tile(np.zeros((721, 1440)), GRID, 3, 2, 2) == tiles.EMPTY_TILE


def test_colorscale_payload_matches_renderer():
    cs = tiles.colorscale()
    assert [b["min_mm"] for b in cs["bins"]] == tiles.BINS_MM
    assert [tuple(b["rgba"]) for b in cs["bins"]] == COLORS_RGBA
    assert cs["bins"][-1]["max_mm"] is None and cs["transparent_below_mm"] == 0.1


@pytest.mark.parametrize("z,x,y", [(-1, 0, 0), (11, 0, 0), (2, 4, 0), (2, 0, -1)])
def test_render_tile_rejects_invalid_coordinates(z, x, y):
    with pytest.raises(ValueError):
        render_tile(np.zeros((721, 1440)), GRID, z, x, y)


def test_grid_from_axes_rejects_non_global_or_irregular():
    with pytest.raises(ValueError):
        Grid.from_axes(LATS, LONS[:720])
    with pytest.raises(ValueError):
        Grid.from_axes(LATS[::-1], LONS)


# ------------------------------------------------------------- real ECMWF data + API

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("store")
    out = root / "ifs" / "2026111500"
    out.mkdir(parents=True)
    shutil.copy(FIXTURE, out / "tp.grib2")
    process_file(out / "tp.grib2", out, "ifs")
    return TestClient(create_app(Settings(data_root=root)), raise_server_exceptions=False)


T = "/api/forecast/ifs/2026111500/tile"


def test_real_bay_of_bengal_maximum_on_map(client):
    # 0-6 h max is 330.69 mm at 9.0N 83.75E (verified in M0/M2) -> ">= 250" colour
    z = 7
    gx, gy = fwd_x(83.75, z), fwd_y(9.0, z)
    tx, ty = int(gx // TILE), int(gy // TILE)
    r = client.get(f"{T}/{z}/{tx}/{ty}.png", params={"end_step": 6})
    assert r.status_code == 200
    rgba = decode(r.content)
    assert tuple(rgba[int(gy) - ty * TILE, int(gx) - tx * TILE]) == COLORS_RGBA[12]


def test_tile_endpoint_headers_and_cache(client):
    r1 = client.get(f"{T}/3/5/3.png", params={"end_step": 12})
    r2 = client.get(f"{T}/3/5/3.png", params={"end_step": 12})
    assert r1.status_code == 200 and r1.headers["content-type"] == "image/png"
    assert r1.headers["cache-control"] == "public, max-age=86400, immutable"
    assert r1.headers["x-window"] == "6-12h"
    assert (r1.headers["x-tile-cache"], r2.headers["x-tile-cache"]) == ("miss", "hit")
    assert r1.content == r2.content and r1.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_tile_window_changes_content(client):
    a = client.get(f"{T}/3/5/3.png", params={"end_step": 6}).content
    b = client.get(f"{T}/3/5/3.png", params={"start_step": 0, "end_step": 12}).content
    assert a != b


@pytest.mark.parametrize("path,params,status", [
    ("/3/8/0.png", {}, 422), ("/11/0/0.png", {}, 422), ("/2/0/-1.png", {}, 422),
    ("/2/0/0.png", {"end_step": 5}, 422), ("/2/0/0.png", {"start_step": 12, "end_step": 6}, 422),
])
def test_tile_invalid(client, path, params, status):
    r = client.get(T + path, params=params)
    assert r.status_code == status and r.json()["error"]["code"] == "invalid_input"


def test_tile_unknown_run_and_pending_model(client):
    assert client.get("/api/forecast/ifs/2026010100/tile/0/0/0.png").status_code == 404
    assert client.get("/api/forecast/aifs-single/2026111500/tile/0/0/0.png").status_code == 409


def test_colorscale_endpoint(client):
    assert client.get("/api/colorscale").json() == tiles.colorscale()
