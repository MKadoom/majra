import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)



class Credentials(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    try:
        payload = decode_token(creds.credentials, "access")
    except pyjwt.PyJWTError as exc:
        raise HTTPException(401, "invalid or expired token") from exc
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "user not found")
    return user


@router.post("/register", response_model=TokenPair, status_code=201)
@limiter.limit(settings.rate_limit_auth)
def register(request: Request, body: Credentials, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "email already registered")
    if len(body.password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return _pair(user)


@router.post("/login", response_model=TokenPair)
@limiter.limit(settings.rate_limit_auth)
def login(request: Request, body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid email or password")
    return _pair(user)


class RefreshBody(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenPair)
@limiter.limit(settings.rate_limit_auth)
def refresh(request: Request, body: RefreshBody, db: Session = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except pyjwt.PyJWTError as exc:
        raise HTTPException(401, "invalid refresh token") from exc
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "user not found")
    return _pair(user)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "created_at": user.created_at}
