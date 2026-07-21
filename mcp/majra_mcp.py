"""majra MCP server — ask your own marketing data questions in an AI chat.

This is the "AI insights" door onto a majra instance, the same idea Windsor.ai
ships as its MCP: instead of exporting a CSV and pasting it into a chat, the
model queries the blended warehouse directly and reasons over live numbers.

It is deliberately read-only. Every tool here maps to a majra endpoint that
accepts an API key (`get_read_principal`), so an MCP client can never mutate a
connection, trigger a sync, or spend money on a platform.

Setup (Claude Desktop / any MCP client):

    {
      "mcpServers": {
        "majra": {
          "command": "python",
          "args": ["/absolute/path/to/majra/mcp/majra_mcp.py"],
          "env": {
            "MAJRA_URL": "https://your-majra.up.railway.app",
            "MAJRA_API_KEY": "mjr_..."
          }
        }
      }
    }

Create the key in majra under Settings → API keys.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------- config

BASE = os.environ.get("MAJRA_URL", "").rstrip("/")
KEY = os.environ.get("MAJRA_API_KEY", "")

if not BASE or not KEY:  # fail loudly at startup, not on the first question
    raise SystemExit(
        "majra-mcp: set MAJRA_URL (e.g. https://your-majra.up.railway.app) "
        "and MAJRA_API_KEY (Settings → API keys) in the MCP server env."
    )

# The public, API-key surface is served at /v1/* (see frontend/next.config.mjs).
API = f"{BASE}/v1"
TIMEOUT = 60

mcp = FastMCP("majra")


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """One GET against majra, with the API key attached and errors made legible.

    Empty values are dropped so optional filters don't turn into `?platform=`.
    Lists are passed through as repeated params, which is what FastAPI expects.
    """
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    try:
        r = httpx.get(
            f"{API}{path}",
            params=clean,
            headers={"Authorization": f"Bearer {KEY}"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"majra unreachable at {API}: {exc}") from exc

    if r.status_code == 401:
        raise RuntimeError("majra rejected the API key (401). Is MAJRA_API_KEY current?")
    if r.status_code == 422:
        raise RuntimeError(f"majra rejected the query (422): {r.text}")
    if r.status_code >= 400:
        raise RuntimeError(f"majra returned {r.status_code}: {r.text[:400]}")
    return r.json()


def _default_range(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Default to the last 30 complete days — yesterday is the last full day."""
    end = date.fromisoformat(date_to) if date_to else date.today() - timedelta(days=1)
    start = date.fromisoformat(date_from) if date_from else end - timedelta(days=29)
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------- tools


@mcp.tool()
def get_ad_metrics(
    date_from: str | None = None,
    date_to: str | None = None,
    platform: list[str] | None = None,
    group_by: list[str] | None = None,
    breakdown: str = "none",
    sort: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Blended paid-advertising performance across every connected ad platform.

    This is the main tool. It returns spend, impressions, clicks, conversions,
    conversion value, video views, leads and purchases — already normalised to
    one shape, so Meta, Google Ads, TikTok and Snapchat are directly comparable
    in a single answer.

    Args:
        date_from: start date, YYYY-MM-DD. Defaults to 30 days before date_to.
        date_to: end date, YYYY-MM-DD. Defaults to yesterday (last full day).
        platform: filter to specific platforms, e.g. ["meta", "snapchat"].
            Valid: meta, google_ads, tiktok, snapchat. Omit for all.
        group_by: split the result, e.g. ["platform"] to compare channels,
            ["date"] for a time series, ["campaign"] for campaign detail, or
            combinations like ["platform", "date"].
        breakdown: "none" (default), "country", "device" or "age_gender".
            Only ask for a breakdown when the question needs that dimension —
            it multiplies the rows returned.
        sort: order rows, e.g. "-spend" for biggest spender first.
        page_size: max rows (up to 1000).

    Tip: to answer "which channel spent the most", pass group_by=["platform"]
    and sort="-spend" rather than pulling every row and summing them yourself.
    """
    start, end = _default_range(date_from, date_to)
    return _get(
        "/metrics",
        {
            "date_from": start,
            "date_to": end,
            "platform": platform,
            "group_by": group_by,
            "breakdown": breakdown,
            "sort": sort,
            "page_size": page_size,
        },
    )


@mcp.tool()
def get_web_metrics(
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: list[str] | None = None,
    sort: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Website analytics (GA4): sessions, users, engaged sessions, conversions.

    Dimensioned by traffic source / medium / campaign, so it answers "where did
    the visitors come from" — and pairs with get_ad_metrics to compare what a
    channel cost against the sessions it actually delivered.

    Args:
        date_from: start date, YYYY-MM-DD. Defaults to 30 days before date_to.
        date_to: end date, YYYY-MM-DD. Defaults to yesterday.
        group_by: e.g. ["source"], ["medium"], ["campaign"], or ["date"].
        sort: e.g. "-sessions".
        page_size: max rows (up to 1000).
    """
    start, end = _default_range(date_from, date_to)
    return _get(
        "/web-metrics",
        {
            "date_from": start,
            "date_to": end,
            "group_by": group_by,
            "sort": sort,
            "page_size": page_size,
        },
    )


@mcp.tool()
def get_social_metrics(
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: list[str] | None = None,
    sort: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Organic social performance for Facebook Pages and Instagram.

    Reach, impressions, engagement, followers — the unpaid side, which the paid
    numbers in get_ad_metrics do not include.

    Args:
        date_from: start date, YYYY-MM-DD. Defaults to 30 days before date_to.
        date_to: end date, YYYY-MM-DD. Defaults to yesterday.
        group_by: e.g. ["account"] or ["date"].
        sort: e.g. "-impressions".
        page_size: max rows (up to 1000).
    """
    start, end = _default_range(date_from, date_to)
    return _get(
        "/social-metrics",
        {
            "date_from": start,
            "date_to": end,
            "group_by": group_by,
            "sort": sort,
            "page_size": page_size,
        },
    )


@mcp.tool()
def list_accounts() -> dict[str, Any]:
    """Every ad/analytics account majra can see, with its platform and currency.

    Call this first when a question names a specific account, or when you need
    to know which platforms are actually connected before reporting a total —
    a platform absent here simply has no data, which is different from a zero.
    """
    return _get("/accounts", {})


@mcp.tool()
def get_tag_snapshot() -> dict[str, Any]:
    """Latest Google Tag Manager snapshot: containers, tags, triggers, variables.

    Useful for tracking/measurement questions — "is the purchase tag live", "what
    fires on this trigger" — rather than performance questions.
    """
    return _get("/gtm-resources", {})


if __name__ == "__main__":
    mcp.run()
