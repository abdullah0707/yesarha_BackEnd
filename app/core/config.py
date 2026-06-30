from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ── APP ───────────────────────────────────────────────────────
    APP_NAME: str = "YESARHA Core"
    APP_VERSION: str = "3.0"
    API_PREFIX: str = "/api/v1"
    ENV: str = "development"

    # ── DATABASE ──────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+psycopg2://yesarha:yesarha@postgres:5432/yesarha_core"
    USE_SQLITE_FALLBACK: bool = False
    SQLITE_PATH: str = "data/yesarha.db"

    # ── AUTH / JWT ────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "CHANGE_ME_SUPER_SECRET_KEY"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30

    # ── OLLAMA ────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://ollama:11434"
    CORE_MODEL: str = "qwen3:8b"          # Yesarha Core العقل الرئيسي
    CORE_MODEL_TIMEOUT: int = 300          # 5 دقائق max للردود الطويلة

    # ── VRAM MANAGEMENT ───────────────────────────────────────────
    VRAM_TOTAL_GB: float = 8.0

    # Voice Specialist (Phase 3)
    WHISPER_MODEL: str = "large-v3"          # large-v3 أفضل دقة، base للأجهزة الضعيفة
    XTTS_MODEL_PATH: str = "data/xtts"       # مجلد تخزين نماذج الصوت وعينات الاستنساخ
    VOICE_SAMPLE_MIN_SECONDS: float = 6.0    # الحد الأدنى لعينة الاستنساخ
    VRAM_CORE_RESERVED_GB: float = 5.5    # محجوز لـ Core دائماً
    MODEL_IDLE_TIMEOUT_SECONDS: int = 300  # تفريغ النموذج بعد 5 دقائق خمول

    # ── WEB INTELLIGENCE ─────────────────────────────────────────
    SEARXNG_URL: str = "http://searxng:8080"
    WEB_SEARCH_MAX_RESULTS: int = 10
    WEB_SEARCH_TIMEOUT: int = 15
    WEEKLY_SCAN_ENABLED: bool = True
    WEEKLY_SCAN_DAY: str = "monday"        # يوم الفحص الأسبوعي

    # ── REDIS ─────────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"
    CACHE_TTL_SECONDS: int = 3600          # cache المعلومات ساعة واحدة

    # ── STREAMING ────────────────────────────────────────────────
    STREAM_CHUNK_SIZE: int = 50            # عدد tokens في كل chunk
    STREAM_HEARTBEAT_SECONDS: int = 15     # keep-alive للاتصالات

    # ── SECURITY ─────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["*"]
    RATE_LIMIT_PER_MINUTE: int = 60
    INTERNAL_API_KEY: str = "CHANGE_ME_INTERNAL_KEY"  # للاتصالات الداخلية

    # ── SPECIALIST MODELS ─────────────────────────────────────────
    SPECIALIST_BASE_PATH: str = "data/specialists"

    # ── PHASE 5 — BILLING / PAYMENTS ─────────────────────────────
    DEFAULT_MODEL: str = "qwen3:8b"
    PAYMENT_CURRENCY_DEFAULT: str = "EGP"

    # Stripe (اختياري — اترك فارغاً لتعطيله)
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None

    # Paymob (اختياري — اترك فارغاً لتعطيله)
    PAYMOB_API_KEY: Optional[str] = None
    PAYMOB_INTEGRATION_ID: Optional[str] = None
    PAYMOB_HMAC_SECRET: Optional[str] = None

    # Subscription scheduler
    SUBSCRIPTION_RENEWAL_CHECK_HOURS: int = 1  # كل ساعة

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
