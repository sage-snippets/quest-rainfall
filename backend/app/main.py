"""FastAPI application factory. Run: uvicorn app.main:app"""
from __future__ import annotations

import logging
import mimetypes
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.geocode import Geocoder
from .api.routes import router
from .config import Settings
from .logging_setup import setup_logging
from .refresh import Refresher
from .store import Store

# Some platforms (Windows registry, slim images) lack these; browsers refuse module
# workers such as MapLibre's maplibre-gl-worker.mjs unless served as JavaScript.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

log = logging.getLogger("app")

_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 409: "model_unavailable",
          413: "region_too_large", 422: "invalid_input", 502: "upstream_unavailable"}


def _err(status: int, message, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status, headers={"X-Request-ID": request_id},
                        content={"error": {"code": _CODES.get(status, "error"), "message": message,
                                           "request_id": request_id}})


class LRUBytes:
    """Small LRU for rendered tiles; locked because sync routes run in a thread pool."""
    def __init__(self, maxsize: int):
        self.maxsize, self._d, self._lock = maxsize, OrderedDict(), threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def __len__(self):
        return len(self._d)


def create_app(settings: Settings | None = None, geocoder: Geocoder | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level)
    store = Store(settings.data_root, settings.array_cache_size)
    refresher = Refresher(store, settings.data_root, models=settings.models,
                          max_step=settings.ingest_max_step,
                          interval_s=settings.refresh_interval_s, keep_runs=settings.keep_runs)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.refresh_enabled:
            refresher.start()          # non-blocking: the API is up while the first ingest runs
        yield
        refresher.stop()

    app = FastAPI(title="QUEST Rainfall API", version="0.5.0", lifespan=lifespan,
                  description="ECMWF Open Data precipitation forecasts (IFS). Forecasts, not observations.")
    app.state.settings = settings
    app.state.store = store
    app.state.refresher = refresher
    app.state.tile_cache = LRUBytes(settings.tile_cache_size)
    app.state.geocoder = geocoder or Geocoder(settings.geocode_url, settings.geocode_timeout_s,
                                              settings.geocode_cache_ttl_s)

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        rid = request.headers.get("X-Request-ID", "")[:64] or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        t = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        log.info("request", extra={"request_id": rid, "method": request.method,
                                   "path": request.url.path, "query": request.url.query,
                                   "status": response.status_code,
                                   "duration_ms": round((time.perf_counter() - t) * 1000, 1)})
        return response

    # Added after the logging middleware so they wrap it (outermost = last added).
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET"], allow_headers=["*"], expose_headers=["X-Request-ID"])

    def rid(request: Request) -> str:
        return getattr(request.state, "request_id", "-")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return _err(exc.status_code, exc.detail, rid(request))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _err(422, msg, rid(request))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": rid(request)})
        return _err(500, "internal server error", rid(request))   # never leak internals

    app.include_router(router)
    if settings.static_dir:
        _mount_spa(app, Path(settings.static_dir))
    return app


def _mount_spa(app: FastAPI, root: Path) -> None:
    """Serve the built React app from the same origin as the API.

    Real files are returned as-is (hashed assets get long-lived caching); any other
    non-API path returns index.html so client-side routes work. /api/* never falls back.
    """
    root = root.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"static_dir {root} has no index.html; build the frontend first")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str, request: Request):
        if path == "api" or path.startswith("api/"):
            return _err(404, "Not Found", getattr(request.state, "request_id", "-"))
        target = (root / path).resolve()
        if path and target.is_file() and target.is_relative_to(root):
            immutable = target.parent.name == "assets"     # Vite content-hashed files
            return FileResponse(target, headers={
                "Cache-Control": "public, max-age=31536000, immutable" if immutable else "public, max-age=300"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
