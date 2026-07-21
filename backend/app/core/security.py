import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

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
    now = datetime.now(UTC)
    payload = {"sub": sub, "typ": typ, "iat": now, "exp": now + delta, **extra}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGO)


def create_access_token(user_id: int) -> str:
    return _token(str(user_id), "access", timedelta(minutes=settings.access_token_minutes))


def create_refresh_token(user_id: int) -> str:
    return _token(str(user_id), "refresh", timedelta(days=settings.refresh_token_days))


def create_oauth_state(user_id: int, platform: str) -> str:
    """CSRF-safe OAuth state carrying the initiating user (10 min TTL)."""
    return _token(str(user_id), "oauth_state", timedelta(minutes=10), platform=platform)


def create_connect_invite(user_id: int, platform: str, days: int) -> str:
    """A link a client can open to attach *their* ad account to *your* majra.

    `sub` is the inviting owner, so when the OAuth callback fires it creates the
    connection under that owner — the client never needs a majra login, and you
    never need their platform password.

    The token grants exactly one power: adding a connection. It carries no read
    access, so it cannot expose data already in the instance. It is stateless,
    which means it works until it expires rather than being consumable once —
    keep the TTL short and treat the link as a secret.
    """
    return _token(
        str(user_id), "connect_invite", timedelta(days=days), platform=platform
    )


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


# ---------------------------------------------------------------- API keys
# A key is high-entropy random, not a password, so a single SHA-256 is the right
# hash: bcrypt would add ~100ms to every API request for no security gain
# against a 256-bit secret. The plaintext key is shown once and never stored.
API_KEY_PREFIX = "mk_"
_PREFIX_LEN = 8  # non-secret head, used to find the row before hashing


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, sha256_hash). Only the last two are stored."""
    body = secrets.token_urlsafe(32)
    full = f"{API_KEY_PREFIX}{body}"
    return full, full[: len(API_KEY_PREFIX) + _PREFIX_LEN], hash_api_key(full)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def api_key_prefix(key: str) -> str:
    return key[: len(API_KEY_PREFIX) + _PREFIX_LEN]
