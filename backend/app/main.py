import structlog
from fastapi import FastAPI
from sqlalchemy import text

from app.api import auth, data, platform
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
