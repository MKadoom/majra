"""Prometheus instrumentation.

Exposed at **/_metrics**, not /metrics — /metrics is the ad-performance query
API. Point your Prometheus scrape config at /_metrics.

The pipeline metrics are the ones that matter operationally: if `majra_sync_rows`
stops climbing or `majra_sync_total{status="failed"}` starts, a tributary has
broken and the channel strips in the dashboard will show gaps tomorrow.
"""
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# --- HTTP ---
REQUESTS = Counter(
    "majra_http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "majra_http_request_seconds",
    "HTTP request duration",
    ["method", "path"],
)

# --- pipeline ---
SYNCS = Counter(
    "majra_sync_total",
    "Sync jobs finished",
    ["platform", "status"],
)
SYNC_SECONDS = Histogram(
    "majra_sync_seconds",
    "Sync job duration",
    ["platform"],
)
SYNC_ROWS = Counter(
    "majra_sync_rows_total",
    "Fact rows written by syncs",
    ["platform"],
)
REPORTS = Counter(
    "majra_report_total",
    "Saved reports delivered",
    ["destination", "status"],
)
BUILD = Gauge("majra_build_info", "Build info", ["version"])


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Label by ROUTE TEMPLATE, never the raw path — otherwise /syncs/1,
        # /syncs/2 … each become their own time series and the cardinality
        # explodes.
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - start
        REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        LATENCY.labels(request.method, path).observe(elapsed)
        return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
