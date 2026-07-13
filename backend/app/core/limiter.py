"""The rate limiter lives here rather than in main.py so that routers can apply
their own stricter limits without importing main (which imports them — a cycle).
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


def rate_key(request: Request) -> str:
    """Throttle per API key when there is one, otherwise per IP — so one noisy
    script can't spend another user's budget."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer mk_"):
        return auth_header[7:30]  # the key's non-secret prefix is enough to bucket by
    return get_remote_address(request)


limiter = Limiter(
    key_func=rate_key,
    storage_uri=settings.rate_limit_storage,
    default_limits=[settings.rate_limit_read] if settings.rate_limit_enabled else [],
    enabled=settings.rate_limit_enabled,
)
