from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.db.session import engine
from app.models import *  # noqa

# Public
from app.api.v1.public.health import router as health_router
from app.api.v1.public.auth import router as auth_router
from app.api.v1.public.manifest import router as manifest_router

# Admin
from app.api.v1.admin.users import router as admins_router
from app.api.v1.admin.models import router as models_router
from app.api.v1.admin.agents import router as agents_router
from app.api.v1.admin.dashboard import router as dashboard_router
from app.api.v1.admin.analytics import router as analytics_router
from app.api.v1.admin.system import router as system_router
from app.api.v1.admin.test_tools import router as test_tools_router
from app.api.v1.admin.specialists import router as specialists_router
from app.api.v1.admin.synced_content import router as synced_content_router
from app.api.v1.admin.core_settings import router as core_settings_router
from app.api.v1.admin.monitor import router as monitor_router

# Core Intelligence
from app.api.v1.core.chat import router as core_chat_router

# Specialists
from app.api.v1.specialist.education import router as education_router
from app.api.v1.specialist.public_chat import router as public_specialist_router
from app.api.v1.specialist.content_sync import router as content_sync_router
from app.api.v1.specialist.voice import router as voice_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """يُشغَّل عند بدء التطبيق وعند إيقافه"""
    # ── Startup ──
    from app.core.intelligence.auto_monitor import core_monitor
    core_monitor.start()

    yield  # التطبيق يعمل هنا

    # ── Shutdown ──
    core_monitor.stop()


def create_app() -> FastAPI:
    from app.db.session import Base
    Base.metadata.create_all(bind=engine)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    p = settings.API_PREFIX

    app.include_router(health_router,            prefix=p)
    app.include_router(auth_router,              prefix=p)
    app.include_router(manifest_router,          prefix=p)
    app.include_router(admins_router,            prefix=p)
    app.include_router(models_router,            prefix=p)
    app.include_router(agents_router,            prefix=p)
    app.include_router(dashboard_router,         prefix=p)
    app.include_router(analytics_router,         prefix=p)
    app.include_router(system_router,            prefix=p)
    app.include_router(test_tools_router,        prefix=p)
    app.include_router(specialists_router,       prefix=p)
    app.include_router(synced_content_router,    prefix=p)
    app.include_router(core_settings_router,     prefix=p)
    app.include_router(monitor_router,           prefix=p)
    app.include_router(core_chat_router,         prefix=p)
    app.include_router(education_router,         prefix=p)
    app.include_router(public_specialist_router, prefix=p)
    app.include_router(content_sync_router,      prefix=p)
    app.include_router(voice_router,             prefix=p)

    return app


app = create_app()
