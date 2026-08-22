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
from app.enums import GroupRole
from app.models import AdminSession, Group, GroupMember, User, utc_now
from app.schemas import AuthUserResponse, GroupResponse

auth_router = APIRouter()
password_hasher = PasswordHasher()
login_attempts: dict[str, deque] = defaultdict(deque)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DbSession = Annotated[Session, Depends(get_db)]


class LoginRequest(BaseModel):
    username: str
    password: str


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


def current_user_from_session(db: Session, session: AdminSession) -> User:
    user = db.get(User, session.user_id) if session.user_id else None
    if user is None or not user.enabled:
        raise HTTPException(status_code=401, detail="Session user is no longer available")
    return user


def auth_response(db: Session, user: User, csrf_token: str) -> AuthUserResponse:
    rows = db.query(Group, GroupMember.role).join(
        GroupMember, GroupMember.group_id == Group.id
    ).filter(GroupMember.user_id == user.id).all()
    grouped: dict[str, GroupResponse] = {}
    for group, role in rows:
        item = grouped.setdefault(
            group.id,
            GroupResponse(
                id=group.id,
                name=group.name,
                description=group.description,
                roles=[],
            ),
        )
        item.roles.append(role)
    groups = list(grouped.values())
    return AuthUserResponse(
        username=user.username,
        csrf_token=csrf_token,
        is_super_admin=user.is_super_admin,
        groups=groups,
        current_group_id=groups[0].id if groups else None,
    )


def _bootstrap_admin_user(db: Session, settings) -> User:
    user = User(
        username=settings.admin_username,
        password_hash=settings.admin_password_hash,
        is_super_admin=True,
        enabled=True,
    )
    group = db.get(Group, "default")
    if group is None:
        group = Group(
            id="default",
            name=settings.default_group_name,
            description="Default workspace",
        )
        db.add(group)
    db.add(user)
    db.flush()
    db.add(
        GroupMember(
            group_id=group.id,
            user_id=user.id,
            role=GroupRole.GROUP_ADMIN,
        )
    )
    db.flush()
    return user


def require_admin(request: Request) -> AdminSession:
    with SessionLocal() as db:
        session = get_admin_session(request, db)
        db.expunge(session)
    if request.method not in SAFE_METHODS:
        csrf_token = request.headers.get("X-CSRF-Token")
        if not csrf_token or not secrets.compare_digest(csrf_token, session.csrf_token):
            raise HTTPException(status_code=403, detail="CSRF validation failed")
    return session


def require_user(request: Request) -> User:
    with SessionLocal() as db:
        session = get_admin_session(request, db)
        if request.method not in SAFE_METHODS:
            csrf_token = request.headers.get("X-CSRF-Token")
            if not csrf_token or not secrets.compare_digest(csrf_token, session.csrf_token):
                raise HTTPException(status_code=403, detail="CSRF validation failed")
        user = current_user_from_session(db, session)
        db.expunge(user)
    return user


@auth_router.post("/login", response_model=AuthUserResponse)
def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> AuthUserResponse:
    settings = get_settings()
    attempts = _check_rate_limit(request)
    user = db.scalar(select(User).where(User.username == payload.username))
    password_hash = user.password_hash if user is not None else settings.admin_password_hash
    try:
        password_valid = password_hasher.verify(password_hash, payload.password)
    except VerificationError:
        password_valid = False
    bootstrap_admin = (
        user is None
        and payload.username == settings.admin_username
        and bool(settings.admin_password_hash)
        and password_valid
    )
    if bootstrap_admin:
        user = _bootstrap_admin_user(db, settings)
    valid = user is not None and user.enabled and password_valid
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
            user_id=user.id,
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
    return auth_response(db, user, csrf_token)


@auth_router.get("/me", response_model=AuthUserResponse)
def me(
    session: Annotated[AdminSession, Depends(require_admin)], db: DbSession
) -> AuthUserResponse:
    user = current_user_from_session(db, session)
    return auth_response(db, user, session.csrf_token)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: Annotated[AdminSession, Depends(require_admin)],
    db: DbSession,
) -> None:
    db.delete(session)
    db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
