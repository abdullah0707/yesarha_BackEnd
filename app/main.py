from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.rate_limit import limiter
from app.db.session import engine
from app.models import *  # noqa

MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # 60 MB — كافٍ لأي ملف صوتي عملي

# Public
from app.api.v1.public.health import router as health_router
from app.api.v1.public.auth import router as auth_router
from app.api.v1.public.manifest import router as manifest_router

# Admin
from app.api.v1.admin.users import router as admins_router
from app.api.v1.admin.dashboard import router as dashboard_router
from app.api.v1.admin.analytics import router as analytics_router
from app.api.v1.admin.system import router as system_router
from app.api.v1.admin.specialists import router as specialists_router
from app.api.v1.admin.synced_content import router as synced_content_router
from app.api.v1.admin.core_settings import router as core_settings_router
from app.api.v1.admin.monitor import router as monitor_router
from app.api.v1.admin.bundles import router as bundles_router
from app.api.v1.admin.runtime_config import router as runtime_config_router
from app.api.v1.admin.connections import router as connections_router

# Core Intelligence
from app.api.v1.core.chat import router as core_chat_router

# Specialists
from app.api.v1.specialist.education import router as education_router
from app.api.v1.specialist.public_chat import router as public_specialist_router
from app.api.v1.specialist.content_sync import router as content_sync_router
from app.api.v1.specialist.voice import router as voice_router
from app.api.v1.specialist.vision import router as vision_router
from app.api.v1.specialist.bundle_chat import router as bundle_chat_router
from app.api.v1.specialist.orchestrate import router as orchestrate_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """يُشغَّل عند بدء التطبيق وعند إيقافه"""
    # ── Startup ──
    from app.core.intelligence.auto_monitor import core_monitor
    from app.services.scheduler import start_scheduler, stop_scheduler
    from app.services.runtime_config import runtime_cfg
    from app.db.session import SessionLocal

    # تحميل الإعدادات الديناميكية من DB مرة واحدة عند البدء
    try:
        db = SessionLocal()
        runtime_cfg.initialize(db)
        db.close()
    except Exception as e:
        import logging
        logging.getLogger("runtime_config").warning(f"Runtime config init failed: {e}")

    core_monitor.start()
    start_scheduler()

    yield  # التطبيق يعمل هنا

    # ── Shutdown ──
    core_monitor.stop()
    stop_scheduler()


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

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.middleware("http")
    async def enforce_max_body_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                status_code=413,
                content={"success": False, "error": "حجم الطلب يتجاوز الحد المسموح (60MB)"}
            )
        return await call_next(request)

    register_exception_handlers(app)
    p = settings.API_PREFIX

    app.include_router(health_router,            prefix=p)
    app.include_router(auth_router,              prefix=p)
    app.include_router(manifest_router,          prefix=p)
    app.include_router(admins_router,            prefix=p)
    app.include_router(dashboard_router,         prefix=p)
    app.include_router(analytics_router,         prefix=p)
    app.include_router(system_router,            prefix=p)
    app.include_router(specialists_router,       prefix=p)
    app.include_router(synced_content_router,    prefix=p)
    app.include_router(core_settings_router,     prefix=p)
    app.include_router(monitor_router,           prefix=p)
    app.include_router(bundles_router,           prefix=p)
    app.include_router(runtime_config_router,    prefix=p)
    app.include_router(connections_router,       prefix=p)
    app.include_router(core_chat_router,         prefix=p)
    app.include_router(education_router,         prefix=p)
    app.include_router(public_specialist_router, prefix=p)
    app.include_router(content_sync_router,      prefix=p)
    app.include_router(voice_router,             prefix=p)
    app.include_router(vision_router,            prefix=p)
    app.include_router(bundle_chat_router,       prefix=p)
    app.include_router(orchestrate_router,       prefix=p)

    return app


app = create_app()
