import logging

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

# Parquet export is optional (see requirements.txt). When pyarrow IS installed it
# must load before the other native extensions — importing it lazily inside a
# request can clash with an already-loaded numpy and take the worker down.
try:  # pragma: no cover
    import pyarrow  # noqa: F401
except ImportError:
    pass

from app.api import auth, data, keys, platform, reports
from app.core.config import settings
from app.core.db import engine
from app.core.limiter import limiter
from app.core.observability import BUILD, PrometheusMiddleware, metrics_response

VERSION = "1.0.0"

# httpx logs full request URLs at INFO; TikTok/Meta put secrets and tokens in the query.
logging.getLogger("httpx").setLevel(logging.WARNING)

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if settings.hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


app = FastAPI(
    title="majra",
    version=VERSION,
    description=(
        "Self-hosted marketing data platform. Connect ad, analytics and social "
        "platforms; query them blended through one API.\n\n"
        "**Auth:** a session JWT, or a read-only API key (`Authorization: Bearer "
        "mk_…`). Keys work on the read endpoints only.\n\n"
        "**Note:** `/metrics` is the ad-performance query API. Prometheus scrapes "
        "`/_metrics`."
    ),
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
if settings.rate_limit_enabled:
    app.add_middleware(SlowAPIMiddleware)

app.add_middleware(SecurityHeaders)
if settings.metrics_enabled:
    app.add_middleware(PrometheusMiddleware)

# Empty by default: the dashboard proxies server-side, so no browser origin needs
# direct access. Add one only for a separate browser app using an API key.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # API keys, not cookies
        allow_methods=["GET"],
        allow_headers=["authorization", "content-type"],
    )

app.include_router(auth.router)
app.include_router(platform.router)
app.include_router(data.router)
app.include_router(keys.router)
app.include_router(reports.router)

BUILD.labels(version=VERSION).set(1)


@app.get("/health", tags=["ops"], summary="Liveness and dependency check")
def health():
    db_ok = redis_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    try:
        import redis as redis_lib

        redis_lib.Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
        redis_ok = True
    except Exception:
        pass
    return {
        "status": "ok" if db_ok else "degraded",
        "version": VERSION,
        "environment": settings.environment,
        "db": db_ok,
        "redis": redis_ok,
    }


@app.get("/_metrics", tags=["ops"], include_in_schema=False)
def prometheus_metrics():
    """Prometheus scrape target. Deliberately NOT /metrics — that path is the
    ad-performance query API."""
    return metrics_response()
