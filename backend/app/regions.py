"""Region presets as (west, south, east, north) bounding boxes in degrees.

Boxes are rectangles on the lat/lon grid, so statistics include every grid cell in the
box (e.g. "India" includes parts of neighbouring countries and sea). City boxes are
±0.5° (~55 km) around the city centre, enough to cover several 0.25° cells.
"""

def _city(lat: float, lon: float, half: float = 0.5) -> tuple[float, float, float, float]:
    return (lon - half, lat - half, lon + half, lat + half)

REGIONS: dict[str, dict] = {
    "india":         {"name": "India",         "bbox": (68.0, 6.0, 97.5, 37.5)},
    "hyderabad":     {"name": "Hyderabad",     "bbox": _city(17.385, 78.487)},
    "bengaluru":     {"name": "Bengaluru",     "bbox": _city(12.972, 77.595)},
    "mumbai":        {"name": "Mumbai",        "bbox": _city(19.076, 72.878)},
    "delhi":         {"name": "Delhi",         "bbox": _city(28.614, 77.209)},
    "karnataka":     {"name": "Karnataka",     "bbox": (74.0, 11.5, 78.6, 18.5)},
    "western_ghats": {"name": "Western Ghats", "bbox": (73.0, 8.0, 77.5, 21.5)},
    "global":        {"name": "Global",        "bbox": (-180.0, -90.0, 180.0, 90.0)},
}
