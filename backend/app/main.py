"""FastAPI application factory. Run: uvicorn app.main:app"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.geocode import Geocoder
from .api.routes import router
from .config import Settings
from .logging_setup import setup_logging
from .store import Store

log = logging.getLogger("app")

_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 409: "model_unavailable",
          413: "region_too_large", 422: "invalid_input", 502: "upstream_unavailable"}


def _err(status: int, message, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status, headers={"X-Request-ID": request_id},
                        content={"error": {"code": _CODES.get(status, "error"), "message": message,
                                           "request_id": request_id}})


def create_app(settings: Settings | None = None, geocoder: Geocoder | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="QUEST Rainfall API", version="0.3.0",
                  description="ECMWF Open Data precipitation forecasts (IFS). Forecasts, not observations.")
    app.state.settings = settings
    app.state.store = Store(settings.data_root, settings.array_cache_size)
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
    return app


app = create_app()
