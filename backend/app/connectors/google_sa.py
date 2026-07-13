"""Shared Google service-account auth (JWT-bearer grant).

Used by connectors that authenticate with a service account (GA4, GTM): builds a
short-lived access token by signing a JWT (RS256) with the SA private key and
exchanging it. Hand-rolled with httpx + pyjwt so it's mockable with respx and
needs no extra dependency.
"""
import time

import httpx
import jwt

from app.connectors.base import PermanentError, TransientError

TOKEN_URI = "https://oauth2.googleapis.com/token"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"


def mint_access(service_account: dict, scope: str) -> str:
    sa = service_account
    if not isinstance(sa, dict) or not sa.get("client_email") or not sa.get("private_key"):
        raise PermanentError("service account missing client_email/private_key")
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": sa["client_email"],
            "scope": scope,
            "aud": sa.get("token_uri", TOKEN_URI),
            "iat": now,
            "exp": now + 3600,
        },
        sa["private_key"],
        algorithm="RS256",
        headers={"kid": sa.get("private_key_id")} if sa.get("private_key_id") else None,
    )
    try:
        resp = httpx.post(
            sa.get("token_uri", TOKEN_URI),
            data={"grant_type": JWT_BEARER, "assertion": assertion},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise TransientError(f"service-account token network error {exc}") from exc
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        detail = data.get("error_description") or data.get("error") or resp.status_code
        raise PermanentError(f"service-account token exchange failed — {detail}")
    return data["access_token"]
