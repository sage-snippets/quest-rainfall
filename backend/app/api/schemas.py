from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FORECAST_NOTE = "ECMWF model forecast, not observed rainfall."


class ModelInfo(BaseModel):
    id: str
    name: str
    status: Literal["operational", "pending"]
    description: str
    runs_available: int


class Interval(BaseModel):
    start_step: int
    end_step: int
    valid_from: str
    valid_to: str
    max_mm: float
    n_clamped_negatives: int
    packing_tolerance_mm: float


class RunMeta(BaseModel):
    model: str
    run_id: str
    run: str
    steps: list[int]
    intervals: list[Interval]
    variable: str
    units: str = "mm"
    accumulation: str
    source: str | None
    source_urls: list[str]
    retrieved_at: str | None
    processed_at: str
    attribution: str
    note: str = FORECAST_NOTE


class Window(BaseModel):
    start_step: int
    end_step: int
    valid_from: str
    valid_to: str
    hours: int


class GridResponse(BaseModel):
    model: str
    run: str
    window: Window
    bbox: tuple[float, float, float, float]
    units: str = "mm"
    lats: list[float]
    lons: list[float]
    values: list[list[float | None]] = Field(description="rows north->south, null = missing")
    note: str = FORECAST_NOTE


class Stats(BaseModel):
    n_cells: int
    n_valid: int
    min_mm: float | None
    max_mm: float | None
    mean_mm: float | None = Field(description="area-weighted (cos latitude)")
    wet_area_fraction: float | None
    wet_threshold_mm: float | None = None


class SummaryResponse(BaseModel):
    model: str
    run: str
    window: Window
    region: str | None
    region_name: str
    bbox: tuple[float, float, float, float]
    stats: Stats
    max_location: tuple[float, float] | None = Field(description="(lat, lon) of max cell")
    notes: list[str]


class TimelinePoint(BaseModel):
    start_step: int
    end_step: int
    valid_from: str
    valid_to: str
    interval_mm: float | None
    cumulative_mm: float | None


class TimelineResponse(BaseModel):
    model: str
    run: str
    requested: tuple[float, float]
    grid_cell: tuple[float, float] = Field(description="centre of the nearest 0.25° cell used")
    method: str = "nearest grid cell (no interpolation)"
    units: str = "mm"
    series: list[TimelinePoint]
    note: str = FORECAST_NOTE


class GeocodeResult(BaseModel):
    name: str | None
    country: str | None
    admin1: str | None
    lat: float
    lon: float


class GeocodeResponse(BaseModel):
    query: str
    cached: bool
    results: list[GeocodeResult]
