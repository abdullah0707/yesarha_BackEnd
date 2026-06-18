from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.db.session import engine
from app.models import *  # noqa: F401,F403 — registers all models with Base

# Public endpoints (no auth)
from app.api.v1.public.health import router as health_router
from app.api.v1.public.auth import router as auth_router
from app.api.v1.public.manifest import router as manifest_router

# Admin endpoints (auth required)
from app.api.v1.admin.users import router as admins_router
from app.api.v1.admin.models import router as models_router
from app.api.v1.admin.agents import router as agents_router
from app.api.v1.admin.dashboard import router as dashboard_router
from app.api.v1.admin.analytics import router as analytics_router
from app.api.v1.admin.system import router as system_router
from app.api.v1.admin.test_tools import router as test_tools_router


def create_app() -> FastAPI:
    from app.db.session import Base
    Base.metadata.create_all(bind=engine)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    prefix = settings.API_PREFIX

    # ── Public ──
    app.include_router(health_router, prefix=prefix)
    app.include_router(auth_router, prefix=prefix)
    app.include_router(manifest_router, prefix=prefix)

    # ── Admin ──
    app.include_router(admins_router, prefix=prefix)
    app.include_router(models_router, prefix=prefix)
    app.include_router(agents_router, prefix=prefix)
    app.include_router(dashboard_router, prefix=prefix)
    app.include_router(analytics_router, prefix=prefix)
    app.include_router(system_router, prefix=prefix)
    app.include_router(test_tools_router, prefix=prefix)

    return app


app = create_app()
