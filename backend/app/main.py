"""FastAPI application factory."""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.errors import install_error_handlers
from app.api.routes import router
from app.config import get_settings
from app.logging_config import configure_logging
from app.research.scheduler import RunScheduler, set_scheduler

log = logging.getLogger("app")
OPEN_PATHS = {"/api/health", "/", "/docs", "/openapi.json"}


def create_app(scheduler: RunScheduler | None = None, start_worker: bool | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(json_logs=settings.app_env == "production")
    if settings.app_env == "production" and any("localhost" in o for o in settings.cors_origins):
        log.warning("FRONTEND_ORIGIN contains localhost in production")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        active = scheduler
        if active is None and (start_worker if start_worker is not None else settings.worker_mode == "inline"):
            active = RunScheduler()
        if active is not None:
            active.start()
            set_scheduler(active)
            resumed = active.resume_active_runs()
            if resumed:
                log.info("resumed unfinished runs", extra={"count": resumed})
        if not settings.gemini_configured:
            log.warning("GEMINI_API_KEY is not set: new runs will fail with config_missing")
        yield
        if active is not None:
            active.stop()
            set_scheduler(None)

    app = FastAPI(title="Deep Research Agent API", version=__version__, lifespan=lifespan,
                  docs_url="/docs" if settings.app_env != "production" else None)
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "Idempotency-Key", "X-Access-Code", "X-Request-Id",
                                      "Last-Event-ID"],
                       expose_headers=["X-Request-Id"], max_age=600)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        code = settings.demo_access_code
        if (code and request.method != "OPTIONS" and request.url.path.startswith("/api")
                and request.url.path not in OPEN_PATHS):
            # EventSource cannot send headers, so the SSE stream may pass the code as a query parameter.
            supplied = request.headers.get("x-access-code") or request.query_params.get("access_code", "")
            if not hmac.compare_digest(supplied.encode(), code.encode()):
                return JSONResponse({"error": {"code": "access_code_required",
                                               "message": "Enter the demo access code to use this deployment.",
                                               "details": None}}, status_code=401,
                                    headers={"X-Request-Id": request_id})
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        if request.url.path != "/api/health":
            log.info("request", extra={"request_id": request_id, "method": request.method, "path": request.url.path,
                                       "status": response.status_code,
                                       "duration_ms": round((time.perf_counter() - started) * 1000)})
        return response

    app.include_router(router)

    @app.get("/")
    def root():
        return {"service": "deep-research-agent", "version": __version__, "health": "/api/health"}

    return app


app = create_app()
