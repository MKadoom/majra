import structlog
from fastapi import FastAPI
from sqlalchemy import text

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

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

app = FastAPI(title="majra", version="0.1.0")
app.include_router(auth.router)
app.include_router(platform.router)
app.include_router(data.router)
app.include_router(keys.router)
app.include_router(reports.router)


@app.get("/health", tags=["ops"])
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
        "version": app.version,
        "environment": settings.environment,
        "db": db_ok,
        "redis": redis_ok,
    }
