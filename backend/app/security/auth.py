from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict, deque
from datetime import UTC, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import AdminSession, utc_now

auth_router = APIRouter()
password_hasher = PasswordHasher()
login_attempts: dict[str, deque] = defaultdict(deque)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DbSession = Annotated[Session, Depends(get_db)]


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    username: str
    csrf_token: str


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def validate_auth_settings() -> None:
    settings = get_settings()
    if not settings.admin_password_hash:
        raise RuntimeError("WORKFLOW_ADMIN_PASSWORD_HASH is required")


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> deque:
    now = utc_now()
    attempts = login_attempts[_client_key(request)]
    while attempts and attempts[0] < now - timedelta(minutes=5):
        attempts.popleft()
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    return attempts


def get_admin_session(request: Request, db: Session) -> AdminSession:
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    session = db.scalar(
        select(AdminSession).where(AdminSession.token_hash == hash_token(raw_token))
    )
    expires_at = session.expires_at if session is not None else None
    now = utc_now()
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if session is None or expires_at is None or expires_at <= now:
        if session is not None:
            db.delete(session)
            db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    return session


def require_admin(request: Request) -> AdminSession:
    with SessionLocal() as db:
        session = get_admin_session(request, db)
        db.expunge(session)
    if request.method not in SAFE_METHODS:
        csrf_token = request.headers.get("X-CSRF-Token")
        if not csrf_token or not secrets.compare_digest(csrf_token, session.csrf_token):
            raise HTTPException(status_code=403, detail="CSRF validation failed")
    return session


@auth_router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> AuthResponse:
    settings = get_settings()
    attempts = _check_rate_limit(request)
    try:
        password_valid = password_hasher.verify(
            settings.admin_password_hash, payload.password
        )
    except VerificationError:
        password_valid = False
    valid = secrets.compare_digest(payload.username, settings.admin_username) and password_valid
    if not valid:
        attempts.append(utc_now())
        raise HTTPException(status_code=401, detail="Invalid username or password")
    attempts.clear()
    raw_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(hours=settings.session_ttl_hours)
    db.execute(delete(AdminSession).where(AdminSession.expires_at <= utc_now()))
    db.add(
        AdminSession(
            token_hash=hash_token(raw_token),
            csrf_token=csrf_token,
            expires_at=expires_at,
        )
    )
    db.commit()
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return AuthResponse(username=settings.admin_username, csrf_token=csrf_token)


@auth_router.get("/me", response_model=AuthResponse)
def me(session: Annotated[AdminSession, Depends(require_admin)]) -> AuthResponse:
    return AuthResponse(username=get_settings().admin_username, csrf_token=session.csrf_token)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: Annotated[AdminSession, Depends(require_admin)],
    db: DbSession,
) -> None:
    db.delete(session)
    db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
