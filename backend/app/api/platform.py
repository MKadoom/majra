from datetime import UTC, datetime

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.connectors.base import CONNECTORS, PermanentError, get_connector
from app.core.config import settings
from app.core.db import get_db
from app.core.security import (
    create_connect_invite,
    create_oauth_state,
    decode_token,
    encrypt_tokens,
)
from app.models import Connection, User

router = APIRouter(tags=["connections"])


def _redirect_uri(platform: str) -> str:
    return f"{settings.public_base_url}/connect/{platform}/callback"


@router.get("/platforms")
def platforms(user: User = Depends(get_current_user)):
    """Which platforms majra can connect to, and how each authenticates.

    Gated behind auth: it discloses internal connector capability details that
    an anonymous caller has no need to see (see SPEC §2). The Connections page,
    its only consumer, is already behind a session.
    """
    return {
        "platforms": [
            {"platform": p, "auth_kind": c.auth_kind}
            for p, c in sorted(CONNECTORS.items())
        ]
    }


@router.get("/connect/{platform}")
def connect(platform: str, user: User = Depends(get_current_user)):
    """Returns the provider consent URL. Open it in a browser; the callback
    below stores the encrypted tokens and creates the connection."""
    if platform not in CONNECTORS:
        raise HTTPException(404, f"unknown platform '{platform}'")
    connector = get_connector(platform)
    if connector.auth_kind != "oauth":
        raise HTTPException(
            400, f"'{platform}' uses {connector.auth_kind}; "
                 "use POST /connections/service-account"
        )
    state = create_oauth_state(user.id, platform)
    try:
        auth_url = connector.auth_url(state, _redirect_uri(platform))
    except PermanentError as exc:
        # e.g. the platform's app credentials aren't configured on this instance.
        raise HTTPException(400, str(exc)) from exc
    return {"auth_url": auth_url}


@router.get("/connect/{platform}/callback", response_class=HTMLResponse)
def callback(
    platform: str,
    state: str,
    code: str | None = None,
    auth_code: str | None = None,  # TikTok returns the code under this name
    db: Session = Depends(get_db),
):
    auth = code or auth_code
    if not auth:
        raise HTTPException(400, "missing authorization code")
    try:
        payload = decode_token(state, "oauth_state")
    except pyjwt.PyJWTError as exc:
        raise HTTPException(400, "invalid or expired state") from exc
    if payload.get("platform") != platform:
        raise HTTPException(400, "state/platform mismatch")

    connector = get_connector(platform)
    try:
        tokens = connector.exchange_code(auth, _redirect_uri(platform))
    except PermanentError as exc:
        raise HTTPException(400, str(exc)) from exc

    conn = Connection(
        user_id=int(payload["sub"]),
        platform=platform,
        name=f"{platform} · {datetime.now(UTC):%Y-%m-%d %H:%M}",
        encrypted_tokens=encrypt_tokens(tokens.to_dict()),
    )
    db.add(conn)
    db.commit()
    return (
        "<html><body style='font-family:sans-serif'>"
        f"<h2>✓ {platform} connected</h2>"
        f"<p>Connection #{conn.id} created. You can close this tab.</p>"
        "</body></html>"
    )


@router.get("/connections")
def list_connections(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(Connection).where(Connection.user_id == user.id).order_by(Connection.id)
    )
    return [
        {
            "id": c.id,
            "platform": c.platform,
            "name": c.name,
            "status": c.status,
            "synced_account_ids": c.synced_account_ids,
            "created_at": c.created_at,
        }
        for c in rows
    ]


class SyncedAccounts(BaseModel):
    # Account external_ids to restrict this connection's syncs to. Empty list
    # clears the allow-list (sync every visible account again).
    account_external_ids: list[str]


@router.put("/connections/{connection_id}/synced-accounts")
def set_synced_accounts(
    connection_id: int,
    body: SyncedAccounts,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Restrict which ad accounts a connection syncs (or clear the restriction).

    Useful when a token exposes many accounts but only some matter: focusing the
    sync conserves the platform's per-user API rate budget and drops read-only /
    empty accounts from the warehouse.
    """
    conn = db.get(Connection, connection_id)
    if conn is None or conn.user_id != user.id:
        raise HTTPException(404, "connection not found")
    conn.synced_account_ids = body.account_external_ids or None
    db.commit()
    return {
        "id": conn.id,
        "platform": conn.platform,
        "synced_account_ids": conn.synced_account_ids,
    }


class ServiceAccountConnection(BaseModel):
    platform: str
    service_account: dict          # the full service-account JSON key
    property_id: str | None = None  # GA4 property id; optional (GTM: account filter)
    name: str | None = None


@router.post("/connections/service-account", status_code=201)
def create_service_account_connection(
    body: ServiceAccountConnection,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a connection for a service-account platform (GA4, GTM): the user
    supplies the SA JSON (granted access on their property/container) rather than
    going through a browser OAuth flow."""
    if body.platform not in CONNECTORS:
        raise HTTPException(404, f"unknown platform '{body.platform}'")
    connector = get_connector(body.platform)
    if connector.auth_kind != "service_account":
        raise HTTPException(
            400, f"'{body.platform}' uses OAuth; use GET /connect/{body.platform}"
        )
    try:
        tokens = connector.credentials_from_input(
            {"service_account": body.service_account, "property_id": body.property_id}
        )
    except PermanentError as exc:
        raise HTTPException(422, str(exc)) from exc

    label = body.name or (
        f"{body.platform} · property {body.property_id}" if body.property_id
        else body.platform
    )
    conn = Connection(
        user_id=user.id,
        platform=body.platform,
        name=label,
        encrypted_tokens=encrypt_tokens(tokens.to_dict()),
    )
    db.add(conn)
    db.commit()
    return {"id": conn.id, "platform": conn.platform, "name": conn.name,
            "status": conn.status, "created_at": conn.created_at}


# ----------------------------------------------------- external auth links
#
# The problem this solves: onboarding a client's ad account normally means
# either they hand you their platform password (bad) or you walk them through a
# screen-share. Instead the owner mints a link, the client opens it, authorises
# on the platform they already trust, and the connection lands in the owner's
# majra. Nobody shares a credential, and the client never sees the dashboard.


class ConnectLink(BaseModel):
    platform: str
    days: int = Field(7, ge=1, le=90, description="How long the link stays usable")


@router.post("/connect-links", status_code=201)
def create_connect_link(
    body: ConnectLink, user: User = Depends(get_current_user)
):
    """Mint a link a client can open to attach their own account to your majra."""
    if body.platform not in CONNECTORS:
        raise HTTPException(404, f"unknown platform '{body.platform}'")
    if get_connector(body.platform).auth_kind != "oauth":
        raise HTTPException(
            400,
            f"'{body.platform}' authenticates with a service account, which "
            "cannot be delegated by link — send those credentials yourself.",
        )
    token = create_connect_invite(user.id, body.platform, body.days)
    return {
        "url": f"{settings.public_base_url}/invite/{token}",
        "platform": body.platform,
        "expires_in_days": body.days,
    }


def _invite(token: str) -> dict:
    try:
        return decode_token(token, "connect_invite")
    except pyjwt.PyJWTError as exc:
        raise HTTPException(400, "this invite link is invalid or has expired") from exc


@router.get("/invite/{token}", response_class=HTMLResponse)
def invite_landing(token: str):
    """What the client sees. Plain, self-contained, and honest about the ask."""
    platform = _invite(token).get("platform", "")
    name = platform.replace("_", " ").title()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect your {name} account</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;
   background:#0F1419;color:#E6EDF3;
   font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}}
 .card{{max-width:34rem;padding:2.5rem;border:1px solid #232C36;
   border-radius:14px;background:#161C23;margin:1rem}}
 h1{{margin:0 0 .75rem;font-size:1.4rem}}
 p{{color:#8B98A5;margin:0 0 1rem}}
 ul{{color:#8B98A5;padding-inline-start:1.1rem;margin:0 0 1.5rem}}
 li{{margin:.35rem 0}}
 a.btn{{display:inline-block;background:#57C4D8;color:#0F1419;
   padding:.7rem 1.15rem;border-radius:8px;text-decoration:none;font-weight:600}}
 a.btn:focus-visible{{outline:2px solid #57C4D8;outline-offset:3px}}
</style></head><body>
<main class="card">
  <h1>Connect your {name} account</h1>
  <p>You've been asked to give a reporting tool read access to your
     {name} advertising data.</p>
  <ul>
    <li>You sign in with {name} — your password is never shared.</li>
    <li>Access is <strong>read-only</strong>: reporting figures, nothing else.</li>
    <li>You can revoke it any time from your {name} account settings.</li>
  </ul>
  <a class="btn" href="/invite/{token}/start">Continue to {name}</a>
</main></body></html>"""


@router.get("/invite/{token}/start")
def invite_start(token: str):
    """Hand the client to the provider, carrying the *owner* as the subject.

    The OAuth state is minted for the inviting user, so the existing callback
    files the resulting connection under their account without knowing or
    caring that a third party clicked the button.
    """
    payload = _invite(token)
    platform = payload.get("platform", "")
    if platform not in CONNECTORS:
        raise HTTPException(400, "this invite names a platform that no longer exists")
    connector = get_connector(platform)
    state = create_oauth_state(int(payload["sub"]), platform)
    return RedirectResponse(
        connector.auth_url(state, _redirect_uri(platform)), status_code=307
    )
