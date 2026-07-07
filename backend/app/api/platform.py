from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
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
    return {"platforms": sorted(CONNECTORS.keys())}


@router.get("/connect/{platform}")
def connect(platform: str, user: User = Depends(get_current_user)):
    """Returns the provider consent URL. Open it in a browser; the callback
    below stores the encrypted tokens and creates the connection."""
    if platform not in CONNECTORS:
        raise HTTPException(404, f"unknown platform '{platform}'")
    connector = get_connector(platform)
    state = create_oauth_state(user.id, platform)
    return {"auth_url": connector.auth_url(state, _redirect_uri(platform))}


@router.get("/connect/{platform}/callback", response_class=HTMLResponse)
def callback(platform: str, code: str, state: str, db: Session = Depends(get_db)):
    try:
        payload = decode_token(state, "oauth_state")
    except pyjwt.PyJWTError:
        raise HTTPException(400, "invalid or expired state")
    if payload.get("platform") != platform:
        raise HTTPException(400, "state/platform mismatch")

    connector = get_connector(platform)
    try:
        tokens = connector.exchange_code(code, _redirect_uri(platform))
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
