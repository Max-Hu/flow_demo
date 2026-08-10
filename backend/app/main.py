import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from app.api import router
from app.config import get_settings
from app.database import SessionLocal
from app.nodes import get_registry_count, get_registry_fingerprint
from app.nodes.audit import require_published_nodes_available
from app.security.auth import auth_router, require_admin, validate_auth_settings
from app.security.crypto import key_ring_fingerprint, validate_key_ring
from app.seed import seed_demo_flow

settings = get_settings()
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_settings()
    validate_key_ring(settings)
    with SessionLocal() as db:
        seed_demo_flow(db)
        require_published_nodes_available(db)
    logger.info(
        "Node registry loaded count=%s fingerprint=%s credential_key_ring=%s",
        get_registry_count(),
        get_registry_fingerprint(),
        key_ring_fingerprint(settings),
    )
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router, prefix="/api/auth")
app.include_router(router, prefix="/api", dependencies=[Depends(require_admin)])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_admin)])
def protected_openapi() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_admin)])
def protected_docs():
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{settings.app_name} Docs")
