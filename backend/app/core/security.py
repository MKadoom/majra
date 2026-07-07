import json
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet

from app.core.config import settings

ALGO = "HS256"


# ---------------------------------------------------------------- passwords
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- JWT
def _token(sub: str, typ: str, delta: timedelta, **extra) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "typ": typ, "iat": now, "exp": now + delta, **extra}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGO)


def create_access_token(user_id: int) -> str:
    return _token(str(user_id), "access", timedelta(minutes=settings.access_token_minutes))


def create_refresh_token(user_id: int) -> str:
    return _token(str(user_id), "refresh", timedelta(days=settings.refresh_token_days))


def create_oauth_state(user_id: int, platform: str) -> str:
    """CSRF-safe OAuth state carrying the initiating user (10 min TTL)."""
    return _token(str(user_id), "oauth_state", timedelta(minutes=10), platform=platform)


def decode_token(token: str, expected_typ: str) -> dict:
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGO])
    if payload.get("typ") != expected_typ:
        raise jwt.InvalidTokenError(f"expected {expected_typ} token")
    return payload


# ---------------------------------------------------------------- Fernet
def _fernet() -> Fernet:
    if not settings.encryption_key:
        raise RuntimeError("MAJRA_ENCRYPTION_KEY is not set")
    return Fernet(settings.encryption_key.encode())


def encrypt_tokens(tokens: dict) -> bytes:
    return _fernet().encrypt(json.dumps(tokens).encode())


def decrypt_tokens(blob: bytes) -> dict:
    return json.loads(_fernet().decrypt(bytes(blob)))
