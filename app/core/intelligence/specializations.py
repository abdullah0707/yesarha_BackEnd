"""
مصدر واحد موثوق لكل التخصصات المتاحة.
كل تخصص له نموذج Open Source مختلف — لا نسخ من qwen3:8b أبداً.
أي تخصص جديد يُضاف هنا فقط، وينعكس تلقائياً في كل مكان.
"""

SPECIALIZATIONS = {
    "code": {
        "label_ar":  "البرمجة والأكواد",
        "label_en":  "Code & Software",
        "base_model": "deepseek-coder-v2:16b",   # متخصص في الكود — أفضل من qwen للبرمجة
        "fallback_model": "qwen2.5-coder:7b",     # إذا لم يكفِ VRAM
        "vram_gb":   12.0,
        "vram_fallback_gb": 5.5,
        "pull_priority": 1,
    },
    "education": {
        "label_ar":  "التعليم والدورات",
        "label_en":  "Education & Courses",
        "base_model": "mistral:7b",               # خفيف + دقيق + ممتاز للشرح
        "fallback_model": "mistral:7b",
        "vram_gb":   5.0,
        "vram_fallback_gb": 5.0,
        "pull_priority": 2,
    },
    "business": {
        "label_ar":  "الأعمال والإدارة",
        "label_en":  "Business & Management",
        "base_model": "mistral:7b",               # دقيق في التحليل والتقارير
        "fallback_model": "mistral:7b",
        "vram_gb":   5.0,
        "vram_fallback_gb": 5.0,
        "pull_priority": 3,
    },
    "media": {
        "label_ar":  "الميديا والمحتوى",
        "label_en":  "Media & Content",
        "base_model": "llama3.1:8b",              # ممتاز في الكتابة الإبداعية
        "fallback_model": "mistral:7b",
        "vram_gb":   6.0,
        "vram_fallback_gb": 5.0,
        "pull_priority": 4,
    },
    "image": {
        "label_ar":  "الصور والفيديو",
        "label_en":  "Image & Video",
        "base_model": "llava:13b",                # multimodal — يفهم الصور
        "fallback_model": "llava:7b",
        "vram_gb":   8.0,
        "vram_fallback_gb": 5.5,
        "pull_priority": 5,
    },
    "voice": {
        "label_ar":  "الصوت واستنساخه",
        "label_en":  "Voice & Cloning",
        "base_model": "whisper-xtts",         # Whisper STT + XTTS-v2 TTS
        "fallback_model": "mistral:7b",       # fallback نصي حتى تُثبَّت مكتبات الصوت
        "vram_gb":   4.0,                     # Whisper large-v3 ~ 1.5GB + XTTS-v2 ~ 2GB
        "vram_fallback_gb": 5.0,
        "pull_priority": 6,
        "is_voice": True,                     # يتعامل معه بشكل مختلف في setup
    },
    "custom": {
        "label_ar":  "تخصص مخصص",
        "label_en":  "Custom",
        "base_model": "mistral:7b",               # افتراضي — يمكن تغييره من لوحة التحكم
        "fallback_model": "mistral:7b",
        "vram_gb":   5.0,
        "vram_fallback_gb": 5.0,
        "pull_priority": 7,
    },
}

VALID_SPECIALIZATIONS = list(SPECIALIZATIONS.keys())


def get_base_model(specialization: str, vram_available_gb: float = 99.0) -> str:
    """
    يُرجع النموذج المناسب بناءً على VRAM المتاح.
    إذا كان VRAM غير كافٍ للنموذج الأساسي — يرجع الـ fallback.
    """
    spec = SPECIALIZATIONS.get(specialization, SPECIALIZATIONS["custom"])
    if vram_available_gb >= spec["vram_gb"]:
        return spec["base_model"]
    return spec["fallback_model"]


def get_vram_required(specialization: str) -> float:
    """VRAM المطلوب للنموذج الأساسي"""
    return SPECIALIZATIONS.get(specialization, SPECIALIZATIONS["custom"])["vram_gb"]


def get_fallback_model(specialization: str) -> str:
    """النموذج البديل لو VRAM غير كافٍ"""
    return SPECIALIZATIONS.get(specialization, SPECIALIZATIONS["custom"])["fallback_model"]


def is_voice_specialist(specialization: str) -> bool:
    """هل هذا التخصص صوتي؟"""
    return SPECIALIZATIONS.get(specialization, {}).get("is_voice", False)


def get_all_models_info() -> list[dict]:
    """قائمة بكل النماذج المطلوبة للـ Pull — مرتبة بالأولوية"""
    return sorted([
        {
            "specialization": key,
            "base_model": v["base_model"],
            "fallback_model": v["fallback_model"],
            "vram_gb": v["vram_gb"],
            "priority": v["pull_priority"],
        }
        for key, v in SPECIALIZATIONS.items()
    ], key=lambda x: x["priority"])
