from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.connectors.base import CONNECTORS, PermanentError, get_connector
from app.core.config import settings
from app.core.db import get_db
from app.core.security import create_oauth_state, decode_token, encrypt_tokens
from app.models import Connection, User

router = APIRouter(tags=["connections"])


def _redirect_uri(platform: str) -> str:
    return f"{settings.public_base_url}/connect/{platform}/callback"


@router.get("/platforms")
def platforms():
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
    return {"auth_url": connector.auth_url(state, _redirect_uri(platform))}


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
    except pyjwt.PyJWTError:
        raise HTTPException(400, "invalid or expired state")
    if payload.get("platform") != platform:
        raise HTTPException(400, "state/platform mismatch")

    connector = get_connector(platform)
    try:
        tokens = connector.exchange_code(auth, _redirect_uri(platform))
    except PermanentError as exc:
        raise HTTPException(400, str(exc))

    conn = Connection(
        user_id=int(payload["sub"]),
        platform=platform,
        name=f"{platform} · {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
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
            "created_at": c.created_at,
        }
        for c in rows
    ]


class ServiceAccountConnection(BaseModel):
    platform: str
    service_account: dict          # the full service-account JSON key
    property_id: str               # GA4 property id (numeric)
    name: str | None = None


@router.post("/connections/service-account", status_code=201)
def create_service_account_connection(
    body: ServiceAccountConnection,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a connection for a service-account platform (e.g. GA4): the user
    supplies the SA JSON + property id (granted Viewer on their property) rather
    than going through a browser OAuth flow."""
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
        raise HTTPException(422, str(exc))

    conn = Connection(
        user_id=user.id,
        platform=body.platform,
        name=body.name or f"{body.platform} · property {body.property_id}",
        encrypted_tokens=encrypt_tokens(tokens.to_dict()),
    )
    db.add(conn)
    db.commit()
    return {"id": conn.id, "platform": conn.platform, "name": conn.name,
            "status": conn.status, "created_at": conn.created_at}
