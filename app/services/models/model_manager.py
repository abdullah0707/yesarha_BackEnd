"""
Model Manager — Hot-Swap System
يدير تحميل/تفريغ النماذج ذكياً بناءً على VRAM المتاح
Core دائماً له الأولوية في VRAM
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional
import requests

from app.core.config import settings


class ModelManager:
    """
    يدير دورة حياة النماذج في Ollama:
    - Core (qwen3:8b) محمّل دائماً ما أمكن
    - النماذج المتخصصة تُحمَّل عند الطلب وتُفرَّغ بعد خمول
    """

    def __init__(self):
        self.ollama_url = settings.OLLAMA_BASE_URL
        self._loaded_models: dict[str, datetime] = {}  # model_name → last_used
        self._loading: set[str] = set()                 # نماذج قيد التحميل
        self._core_model = settings.CORE_MODEL

    # ── Ollama API helpers ────────────────────────────────────────

    def _ollama_get(self, path: str, timeout: int = 5) -> Optional[dict]:
        try:
            r = requests.get(f"{self.ollama_url}{path}", timeout=timeout)
            return r.json() if r.ok else None
        except Exception:
            return None

    def _ollama_post(self, path: str, data: dict, timeout: int = 60) -> Optional[dict]:
        try:
            r = requests.post(f"{self.ollama_url}{path}", json=data, timeout=timeout)
            return r.json() if r.ok else None
        except Exception:
            return None

    # ── Model State ───────────────────────────────────────────────

    def get_loaded_models(self) -> list[str]:
        """النماذج المحمّلة حالياً في Ollama"""
        data = self._ollama_get("/api/ps")
        if not data:
            return []
        return [m.get("name", "") for m in data.get("models", [])]

    def is_model_available(self, model_name: str) -> bool:
        """هل النموذج مُحمَّل وجاهز؟"""
        return model_name in self.get_loaded_models()

    def is_model_downloaded(self, model_name: str) -> bool:
        """هل النموذج مُنزَّل على القرص؟"""
        data = self._ollama_get("/api/tags")
        if not data:
            return False
        models = [m.get("name", "") for m in data.get("models", [])]
        return any(model_name in m for m in models)

    def get_vram_usage_gb(self) -> float:
        """تقدير استخدام VRAM الحالي"""
        data = self._ollama_get("/api/ps")
        if not data:
            return 0.0
        total = sum(
            m.get("size_vram", 0)
            for m in data.get("models", [])
        )
        return total / (1024 ** 3)

    # ── Model Lifecycle ───────────────────────────────────────────

    def pull_model(self, model_name: str) -> bool:
        """تحميل نموذج من Ollama (قد يأخذ وقتاً طويلاً)"""
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/pull",
                json={"name": model_name, "stream": False},
                timeout=3600  # ساعة كاملة للتحميل الكبير
            )
            return resp.ok
        except Exception:
            return False

    def unload_model(self, model_name: str) -> bool:
        """
        تفريغ نموذج من الذاكرة (VRAM) بدون حذفه من القرص
        Ollama يدعم keep_alive=0 لتفريغ النموذج فوراً
        """
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={"model": model_name, "keep_alive": 0},
                timeout=30
            )
            return resp.ok
        except Exception:
            return False

    def ensure_model_loaded(self, model_name: str, reserve_core: bool = True) -> bool:
        """
        يضمن أن النموذج جاهز للاستخدام.
        إذا VRAM ممتلئ، يفرّغ نماذج غير Core لإفساح المجال.
        """
        # إذا محمّل بالفعل
        if self.is_model_available(model_name):
            self._loaded_models[model_name] = datetime.utcnow()
            return True

        # فحص إذا مُنزَّل على القرص
        if not self.is_model_downloaded(model_name):
            return False  # يجب التحميل أولاً via pull_model()

        # إفساح VRAM إذا لزم
        if reserve_core and model_name != self._core_model:
            # فرّغ النماذج غير الأساسية
            loaded = self.get_loaded_models()
            for m in loaded:
                if m != self._core_model and m != model_name:
                    self.unload_model(m)

        # تفعيل النموذج بإرسال رسالة فارغة (warm-up)
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={"model": model_name, "prompt": "", "keep_alive": "10m"},
                timeout=120
            )
            if resp.ok:
                self._loaded_models[model_name] = datetime.utcnow()
                return True
        except Exception:
            pass
        return False

    def cleanup_idle_models(self):
        """
        يُنظَّف بواسطة scheduler — يفرّغ النماذج الخاملة
        Core لا يُفرَّغ أبداً تلقائياً
        """
        idle_threshold = datetime.utcnow() - timedelta(
            seconds=settings.MODEL_IDLE_TIMEOUT_SECONDS
        )
        for model_name, last_used in list(self._loaded_models.items()):
            if model_name == self._core_model:
                continue
            if last_used < idle_threshold:
                self.unload_model(model_name)
                del self._loaded_models[model_name]

    def get_status(self) -> dict:
        """حالة كاملة لـ Model Manager"""
        loaded = self.get_loaded_models()
        vram_used = self.get_vram_usage_gb()
        return {
            "loaded_models": loaded,
            "core_model": self._core_model,
            "core_loaded": self._core_model in loaded,
            "vram_used_gb": round(vram_used, 2),
            "vram_total_gb": settings.VRAM_TOTAL_GB,
            "vram_free_gb": round(settings.VRAM_TOTAL_GB - vram_used, 2),
        }


# Singleton instance
model_manager = ModelManager()
