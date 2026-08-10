from collections.abc import Generator

from argon2 import PasswordHasher
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base, get_db
from app.security import auth


def test_session_cookie_and_csrf_protect_writes(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    def db_override() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    configured = Settings(
        admin_username="admin",
        admin_password_hash=PasswordHasher().hash("correct-password"),
    )
    monkeypatch.setattr(auth, "get_settings", lambda: configured)
    monkeypatch.setattr(auth, "SessionLocal", session_factory)
    auth.login_attempts.clear()
    app = FastAPI()
    app.dependency_overrides[get_db] = db_override
    app.include_router(auth.auth_router, prefix="/api/auth")

    @app.post("/api/protected", dependencies=[Depends(auth.require_admin)])
    def protected() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert client.post("/api/protected").status_code == 403
        assert client.post(
            "/api/protected",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        ).json() == {"ok": True}
