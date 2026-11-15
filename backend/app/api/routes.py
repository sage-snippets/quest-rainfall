from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..processing.precip import area_weighted_stats, point_value, subset
from ..regions import REGIONS
from ..store import NotFound
from .geocode import GeocoderUnavailable
from .schemas import (GeocodeResponse, GridResponse, ModelInfo, RunMeta, Stats, SummaryResponse,
                      TimelinePoint, TimelineResponse, Window)

router = APIRouter(prefix="/api")

MODELS = {
    "ifs": {"name": "ECMWF IFS HRES (0.25°)", "status": "operational",
            "description": "Physics-based Integrated Forecasting System, 00/12 UTC runs. "
                           "Total precipitation ingested and validated."},
    "aifs-single": {"name": "ECMWF AIFS Single (0.25°)", "status": "pending",
                    "description": "Data-driven model. Not yet integrated: its GRIB metadata "
                                   "has not been validated in this application."},
}
ModelId = Literal["ifs", "aifs-single"]
IMMUTABLE = "public, max-age=3600"     # a run's data never changes once ingested


def _store(req: Request):
    return req.app.state.store


def _operational(model: str) -> None:
    if MODELS[model]["status"] != "operational":
        raise HTTPException(409, f"model '{model}' is not available yet (status: pending)")


def _manifest(req: Request, model: str, run: str) -> dict:
    _operational(model)
    try:
        return _store(req).manifest(model, run)
    except NotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox must be 'west,south,east,north' in degrees")
    if not all(np.isfinite([w, s, e, n])):
        raise HTTPException(422, "bbox values must be finite")
    return w, s, e, n


def _window(req: Request, m: dict, model: str, run: str, start: int | None, end: int | None):
    steps = m["steps"]
    if end is None:
        end = steps[1] if len(steps) > 1 else None
    if end is None or end not in steps[1:]:
        raise HTTPException(422, f"end_step must be one of {steps[1:]}")
    if start is None:   # default: the native interval ending at `end`
        start = steps[steps.index(end) - 1]
    try:
        values = _store(req).window(model, run, start, end)
    except ValueError as e:
        raise HTTPException(422, str(e))
    t0 = datetime.fromisoformat(m["run"])
    w = Window(start_step=start, end_step=end, hours=end - start,
               valid_from=(t0 + timedelta(hours=start)).isoformat(),
               valid_to=(t0 + timedelta(hours=end)).isoformat())
    return values, w


def _cut(req: Request, model: str, run: str, values, bbox):
    lats, lons = _store(req).axes(model, run)
    try:
        return subset(values, lats, lons, bbox)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ------------------------------------------------------------------------- endpoints

@router.get("/health")
def health(req: Request):
    latest = {}
    for m in MODELS:
        runs = _store(req).runs(m)
        latest[m] = runs[0] if runs else None
    return {"status": "ok", "latest_runs": latest, "data_available": latest["ifs"] is not None}


@router.get("/models", response_model=list[ModelInfo])
def models(req: Request):
    return [ModelInfo(id=k, runs_available=len(_store(req).runs(k)), **v) for k, v in MODELS.items()]


@router.get("/forecast/latest", response_model=RunMeta)
def latest(req: Request, response: Response, model: ModelId = "ifs"):
    _operational(model)
    try:
        run = _store(req).latest(model)
    except NotFound as e:
        raise HTTPException(404, f"{e}; the backend has not ingested a forecast yet")
    m = _store(req).manifest(model, run)
    r = m.get("retrieval") or {}
    response.headers["Cache-Control"] = "public, max-age=60"
    return RunMeta(model=model, run_id=run, run=m["run"], steps=m["steps"],
                   intervals=m["intervals"], variable=m["variable"], accumulation=m["accumulation"],
                   source=r.get("source"), source_urls=r.get("urls", []),
                   retrieved_at=r.get("retrieved_at"), processed_at=m["processed_at"],
                   attribution=m["attribution"])


@router.get("/forecast/{model}/{run}/grid", response_model=GridResponse)
def grid(req: Request, response: Response, model: ModelId, run: str,
         bbox: str = Query(..., description="west,south,east,north (deg); west>east crosses the antimeridian"),
         end_step: int | None = None, start_step: int | None = None):
    m = _manifest(req, model, run)
    box = _parse_bbox(bbox)
    values, w = _window(req, m, model, run, start_step, end_step)
    sub, la, lo = _cut(req, model, run, values, box)
    limit = req.app.state.settings.max_grid_cells
    if sub.size > limit:
        raise HTTPException(413, f"bbox covers {sub.size} cells; limit is {limit}. Use a smaller box.")
    rounded = np.round(sub.astype(np.float64), 2)
    vals = [[None if np.isnan(x) else float(x) for x in row] for row in rounded]
    response.headers["Cache-Control"] = IMMUTABLE
    return GridResponse(model=model, run=m["run"], window=w, bbox=box,
                        lats=la.tolist(), lons=lo.tolist(), values=vals)


@router.get("/forecast/{model}/{run}/summary", response_model=SummaryResponse)
def summary(req: Request, response: Response, model: ModelId, run: str,
            region: str | None = Query(None, description=f"preset: {', '.join(REGIONS)}"),
            bbox: str | None = None, end_step: int | None = None, start_step: int | None = None,
            wet_threshold_mm: float = Query(1.0, ge=0.1, le=100)):
    m = _manifest(req, model, run)
    if (region is None) == (bbox is None):
        raise HTTPException(422, "provide exactly one of 'region' or 'bbox'")
    notes = ["Statistics cover every 0.25° grid cell inside a rectangular bounding box.",
             "Mean is area-weighted by cos(latitude); values are model grid-cell forecasts."]
    if region is not None:
        if region not in REGIONS:
            raise HTTPException(422, f"unknown region '{region}'; choose from {list(REGIONS)}")
        box, name = REGIONS[region]["bbox"], REGIONS[region]["name"]
        notes.append("Preset regions are approximate bounding boxes, not administrative boundaries.")
    else:
        box, name = _parse_bbox(bbox), "Custom box"
    values, w = _window(req, m, model, run, start_step, end_step)
    sub, la, lo = _cut(req, model, run, values, box)
    st = area_weighted_stats(sub, la, wet_threshold_mm)
    loc = None   # only meaningful if any precipitation fell in the box
    if st["n_valid"] and st["max_mm"] > 0:
        j, i = np.unravel_index(np.nanargmax(sub), sub.shape)
        loc = (float(la[j]), float(lo[i]))
    response.headers["Cache-Control"] = IMMUTABLE
    return SummaryResponse(model=model, run=m["run"], window=w, region=region, region_name=name,
                           bbox=tuple(box), stats=Stats(**st), max_location=loc, notes=notes)


@router.get("/forecast/{model}/{run}/timeline", response_model=TimelineResponse)
def timeline(req: Request, response: Response, model: ModelId, run: str,
             lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    m = _manifest(req, model, run)
    st = _store(req)
    lats, lons = st.axes(model, run)
    series, cell = [], None
    for iv in m["intervals"]:
        a, b = iv["start_step"], iv["end_step"]
        v, clat, clon = point_value(st.interval(model, run, a, b), lats, lons, lat, lon)
        c, _, _ = point_value(st.cumulative(model, run, b), lats, lons, lat, lon)
        cell = (clat, clon)
        series.append(TimelinePoint(start_step=a, end_step=b, valid_from=iv["valid_from"],
                                    valid_to=iv["valid_to"], interval_mm=v, cumulative_mm=c))
    response.headers["Cache-Control"] = IMMUTABLE
    return TimelineResponse(model=model, run=m["run"], requested=(lat, lon), grid_cell=cell, series=series)


@router.get("/geocode", response_model=GeocodeResponse)
def geocode(req: Request, q: str = Query(..., min_length=2, max_length=100),
            count: int = Query(8, ge=1, le=20)):
    try:
        results, cached = req.app.state.geocoder.search(q, count)
    except GeocoderUnavailable as e:
        raise HTTPException(502, str(e))
    return GeocodeResponse(query=q, cached=cached, results=results)
