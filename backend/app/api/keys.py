"""Read-only API keys.

A key authenticates the same way a JWT does — `Authorization: Bearer mk_…` — but
is only accepted by READ endpoints. That's enforced structurally, not by a scope
check: write endpoints depend on `get_current_user` (JWT only), read endpoints
depend on `get_read_principal` (JWT *or* key). A key therefore cannot start a
sync, create a connection, or mint another key, no matter what it claims.
"""
from datetime import UTC, datetime

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.db import get_db
from app.core.security import (
    API_KEY_PREFIX,
    api_key_prefix,
    decode_token,
    generate_api_key,
    hash_api_key,
)
from app.models import ApiKey, User

router = APIRouter(prefix="/api-keys", tags=["api keys"])
bearer = HTTPBearer(auto_error=False)


def get_read_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """Accept either a session JWT or a read-only API key."""
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    token = creds.credentials

    if token.startswith(API_KEY_PREFIX):
        key = db.scalar(
            select(ApiKey).where(ApiKey.prefix == api_key_prefix(token))
        )
        if (
            key is None
            or key.revoked_at is not None
            or key.key_hash != hash_api_key(token)
        ):
            raise HTTPException(401, "invalid or revoked API key")
        key.last_used_at = datetime.now(UTC)
        db.commit()
        return db.get(User, key.user_id)

    try:
        payload = decode_token(token, "access")
    except pyjwt.PyJWTError as exc:
        raise HTTPException(401, "invalid or expired token") from exc
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "user not found")
    return user


class NewKey(BaseModel):
    name: str


def _out(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "name": k.name,
        "prefix": k.prefix,
        "scopes": k.scopes,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
        "revoked_at": k.revoked_at,
    }


@router.post("", status_code=201, summary="Create a read-only API key")
def create_key(
    body: NewKey,
    user: User = Depends(get_current_user),  # JWT only — a key can't mint a key
    db: Session = Depends(get_db),
):
    """The full key is returned **once**. majra stores only its hash, so it
    cannot be shown again — copy it now."""
    full, prefix, key_hash = generate_api_key()
    key = ApiKey(
        user_id=user.id, name=body.name, prefix=prefix, key_hash=key_hash,
        scopes=["read"],
    )
    db.add(key)
    db.commit()
    return {**_out(key), "key": full}


@router.get("", summary="List your API keys")
def list_keys(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.id.desc())
    )
    return [_out(k) for k in rows]


@router.delete("/{key_id}", summary="Revoke an API key")
def revoke_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(404, "API key not found")
    key.revoked_at = datetime.now(UTC)
    db.commit()
    return {"id": key.id, "revoked_at": key.revoked_at}
