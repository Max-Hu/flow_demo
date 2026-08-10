from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKFLOW_", case_sensitive=False)

    app_name: str = "FlowForge MVP"
    database_url: str = "postgresql+psycopg://workflow:workflow@localhost:5432/workflow"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    worker_name: str = "worker-1"
    worker_poll_seconds: float = 0.5
    worker_lease_seconds: int = 30
    admin_username: str = "admin"
    admin_password_hash: str = ""
    session_cookie_name: str = "flowforge_session"
    session_ttl_hours: int = 12
    session_cookie_secure: bool = False
    credential_keys: dict[str, str] = Field(default_factory=dict)
    active_credential_key_id: str = ""
    demo_partner_token: str = "flowforge-local-demo-token"


@lru_cache
def get_settings() -> Settings:
    return Settings()
