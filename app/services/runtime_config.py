"""
RuntimeConfigService — مصدر الإعدادات الديناميكية للنظام.

عند بدء التشغيل:
  1. يقرأ الإعدادات الافتراضية من .env (عبر settings)
  2. يُنشئ السجلات في DB إذا لم تكن موجودة
  3. يحمّلها في ذاكرة الكاش

عند التعديل من لوحة التحكم:
  1. يحفظ في DB
  2. يُحدّث الكاش فوراً (hot reload — بدون restart)

الاستخدام في الكود:
  from app.services.runtime_config import runtime_cfg
  url = runtime_cfg.get("OLLAMA_BASE_URL")
"""
import logging
import threading
from typing import Optional

import httpx

from app.core.config import settings

_log = logging.getLogger("yesarha.runtime")


# ── التعريفات الافتراضية لكل الإعدادات الديناميكية ──────────────

SETTING_DEFINITIONS: list[dict] = [
    # ── Connections ──
    {
        "key": "OLLAMA_BASE_URL",
        "group": "connections",
        "label_ar": "رابط Ollama",
        "label_en": "Ollama URL",
        "description": "عنوان خدمة Ollama — http://ollama:11434 محلياً أو رابط السيرفر السحابي",
        "value_type": "string",
        "is_secret": False,
        "default": lambda: settings.OLLAMA_BASE_URL,
    },
    {
        "key": "REDIS_URL",
        "group": "connections",
        "label_ar": "رابط Redis",
        "label_en": "Redis URL",
        "description": "عنوان خدمة Redis للكاش والطوابير",
        "value_type": "string",
        "is_secret": False,
        "default": lambda: settings.REDIS_URL,
    },
    {
        "key": "SEARXNG_URL",
        "group": "connections",
        "label_ar": "رابط SearXNG",
        "label_en": "SearXNG URL",
        "description": "محرك البحث المحلي — يُستخدم لتدريب النماذج وبحث Core",
        "value_type": "string",
        "is_secret": False,
        "default": lambda: settings.SEARXNG_URL,
    },
    # ── Models ──
    {
        "key": "CORE_MODEL",
        "group": "models",
        "label_ar": "نموذج العقل المركزي",
        "label_en": "Core (Orchestrator) Model",
        "description": "موديل Ollama الذي يعمل كعقل مركزي — qwen3:8b الافتراضي",
        "value_type": "string",
        "is_secret": False,
        "default": lambda: settings.CORE_MODEL,
    },
    {
        "key": "WHISPER_MODEL",
        "group": "models",
        "label_ar": "نموذج Whisper (صوت→نص)",
        "label_en": "Whisper Model (STT)",
        "description": "حجم نموذج Whisper: tiny/base/small/medium/large-v3",
        "value_type": "string",
        "is_secret": False,
        "default": lambda: settings.WHISPER_MODEL,
    },
    {
        "key": "VRAM_TOTAL_GB",
        "group": "models",
        "label_ar": "حجم VRAM (GB)",
        "label_en": "Total VRAM (GB)",
        "description": "إجمالي ذاكرة GPU المتاحة — يُستخدم لاختيار النموذج المناسب تلقائياً",
        "value_type": "float",
        "is_secret": False,
        "default": lambda: str(settings.VRAM_TOTAL_GB),
    },
    # ── Security ──
    {
        "key": "CORS_ORIGINS",
        "group": "security",
        "label_ar": "النطاقات المسموح بها (CORS)",
        "label_en": "Allowed CORS Origins",
        "description": 'قائمة النطاقات المسموح لها بالاتصال — ["*"] للسماح بالكل',
        "value_type": "json",
        "is_secret": False,
        "default": lambda: '["*"]',
    },
    {
        "key": "RATE_LIMIT_PER_MINUTE",
        "group": "security",
        "label_ar": "الحد الأقصى للطلبات في الدقيقة",
        "label_en": "Rate Limit (per minute)",
        "description": "عدد الطلبات المسموح بها لكل IP في الدقيقة",
        "value_type": "int",
        "is_secret": False,
        "default": lambda: str(settings.RATE_LIMIT_PER_MINUTE),
    },
]


class RuntimeConfigService:
    """
    Singleton service للإعدادات الديناميكية.
    Thread-safe — يستخدم Lock للكتابة.
    """

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self, db) -> None:
        """
        يُستدعى مرة واحدة عند بدء التطبيق.
        يُنشئ السجلات الافتراضية في DB إذا لم تكن موجودة، ثم يحمّل الكاش.
        """
        from app.models.runtime import RuntimeSetting

        with self._lock:
            # إنشاء السجلات الافتراضية إن لم تكن موجودة
            for defn in SETTING_DEFINITIONS:
                existing = db.query(RuntimeSetting).filter(
                    RuntimeSetting.key == defn["key"]
                ).first()
                if not existing:
                    default_val = defn["default"]()
                    db.add(RuntimeSetting(
                        key=defn["key"],
                        value=default_val,
                        value_type=defn["value_type"],
                        group=defn["group"],
                        label_ar=defn["label_ar"],
                        label_en=defn["label_en"],
                        description=defn["description"],
                        is_secret=defn.get("is_secret", False),
                    ))
            db.commit()

            # تحميل الكاش
            all_settings = db.query(RuntimeSetting).all()
            self._cache = {s.key: s.value for s in all_settings if s.value is not None}
            self._initialized = True

        # تحقق من CORE_MODEL وصعّد تلقائياً لأفضل موديل متاح
        self._verify_core_model(db)

    # ── Model priority (best first) ────────────────────────────────────────────
    _MODEL_PRIORITY = ["qwen3:8b", "qwen3:4b", "mistral:7b", "llama3.1:8b", "llama3:8b", "gemma2:9b"]

    def _verify_core_model(self, db=None) -> None:
        """
        عند البدء: يختار أفضل موديل متاح في Ollama.
        - إذا الموديل الأمثل موجود في Ollama → يُحدّث DB والكاش تلقائياً
        - إذا الموديل المضبوط غير موجود → يستخدم أفضل بديل متاح
        يُشغَّل في كل restart → يصعّد تلقائياً لـ qwen3:8b حين يكتمل تحميله
        """
        configured = self._cache.get("CORE_MODEL", "")
        try:
            ollama_url = (self._cache.get("OLLAMA_BASE_URL") or settings.OLLAMA_BASE_URL).rstrip("/")
            r = httpx.get(f"{ollama_url}/api/tags", timeout=5)
            if not r.is_success:
                return
            available = [m.get("name", "") for m in r.json().get("models", [])]
            if not available:
                return

            # أفضل موديل متاح بحسب قائمة الأولوية
            best = next((p for p in self._MODEL_PRIORITY if p in available), available[0])

            conf_rank = next((i for i, p in enumerate(self._MODEL_PRIORITY) if p == configured), 999)
            best_rank = next((i for i, p in enumerate(self._MODEL_PRIORITY) if p == best),     999)

            if configured == best:
                _log.info(f"✅ CORE_MODEL '{configured}' — optimal, confirmed in Ollama")
                return

            if configured not in available:
                reason = f"'{configured}' not in Ollama"
            elif best_rank < conf_rank:
                reason = f"'{best}' is higher priority than '{configured}'"
            else:
                _log.info(f"✅ CORE_MODEL '{configured}' confirmed in Ollama")
                return

            _log.warning(f"🔄 CORE_MODEL auto-select: {reason} → switching to '{best}'")
            self._cache["CORE_MODEL"] = best

            if db:
                self._persist_core_model(db, best)
        except Exception as e:
            _log.debug(f"Model verification skipped: {e}")

    def _persist_core_model(self, db, model_name: str) -> None:
        """يحفظ CORE_MODEL الجديد في DB ويضبط الـ AIModel default."""
        try:
            from app.models.runtime import RuntimeSetting
            from app.models.ai import AIModel

            s = db.query(RuntimeSetting).filter(RuntimeSetting.key == "CORE_MODEL").first()
            if s:
                s.value = model_name
            db.commit()

            for m in db.query(AIModel).all():
                m.is_default = (m.name == model_name)
            db.commit()
            _log.info(f"✅ Persisted CORE_MODEL='{model_name}' to DB")
        except Exception as e:
            _log.warning(f"Could not persist CORE_MODEL: {e}")

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """قراءة سريعة من الكاش — بدون DB hit"""
        return self._cache.get(key, default)

    def get_ollama_url(self) -> str:
        return self.get("OLLAMA_BASE_URL") or settings.OLLAMA_BASE_URL

    def get_core_model(self) -> str:
        return self.get("CORE_MODEL") or settings.CORE_MODEL

    def get_searxng_url(self) -> str:
        return self.get("SEARXNG_URL") or settings.SEARXNG_URL

    def get_redis_url(self) -> str:
        return self.get("REDIS_URL") or settings.REDIS_URL

    def get_vram_gb(self) -> float:
        try:
            return float(self.get("VRAM_TOTAL_GB") or settings.VRAM_TOTAL_GB)
        except (ValueError, TypeError):
            return settings.VRAM_TOTAL_GB

    def set(self, key: str, value: str, db) -> None:
        """حفظ في DB + تحديث الكاش فوراً (hot reload)"""
        from app.models.runtime import RuntimeSetting

        with self._lock:
            record = db.query(RuntimeSetting).filter(RuntimeSetting.key == key).first()
            if record:
                record.value = value
            else:
                record = RuntimeSetting(key=key, value=value)
                db.add(record)
            db.commit()
            self._cache[key] = value

    def set_many(self, updates: dict[str, str], db) -> None:
        """حفظ عدة إعدادات دفعة واحدة"""
        for key, value in updates.items():
            self.set(key, value, db)

    def reload(self, db) -> None:
        """إعادة تحميل الكاش من DB"""
        from app.models.runtime import RuntimeSetting
        with self._lock:
            all_settings = db.query(RuntimeSetting).all()
            self._cache = {s.key: s.value for s in all_settings if s.value is not None}

    def get_all_definitions(self) -> list[dict]:
        """يُرجع كل الإعدادات مع قيمها الحالية — للـ Dashboard"""
        result = []
        for defn in SETTING_DEFINITIONS:
            current = self._cache.get(defn["key"])
            result.append({
                "key": defn["key"],
                "value": current,
                "group": defn["group"],
                "label_ar": defn["label_ar"],
                "label_en": defn["label_en"],
                "description": defn["description"],
                "value_type": defn["value_type"],
                "is_secret": defn.get("is_secret", False),
                "default": defn["default"](),
            })
        return result


# ── Singleton ──────────────────────────────────────────────────────
runtime_cfg = RuntimeConfigService()
