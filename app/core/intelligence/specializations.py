"""
مصدر واحد موثوق لكل التخصصات المتاحة لإنشاء نماذج متخصصة.
أي تخصص جديد يُضاف هنا فقط، وينعكس تلقائياً في كل مكان آخر بالنظام.
"""

SPECIALIZATIONS = {
    "code": {
        "label_ar": "البرمجة والأكواد",
        "label_en": "Code & Software",
        "base_model": "qwen2.5-coder:7b",
        "vram_gb": 5.5,
    },
    "voice": {
        "label_ar": "الصوت واستنساخه",
        "label_en": "Voice & Cloning",
        "base_model": "qwen3:8b",   # سيُستبدل بـ XTTS عند بناء الـ specialist الصوتي فعلياً
        "vram_gb": 2.0,
    },
    "image": {
        "label_ar": "الصور والفيديو",
        "label_en": "Image & Video",
        "base_model": "qwen3:8b",
        "vram_gb": 6.0,
    },
    "education": {
        "label_ar": "التعليم والدورات",
        "label_en": "Education & Courses",
        "base_model": "qwen3:8b",
        "vram_gb": 5.5,
    },
    "media": {
        "label_ar": "الميديا والمحتوى",
        "label_en": "Media & Content",
        "base_model": "qwen3:8b",
        "vram_gb": 5.5,
    },
    "business": {
        "label_ar": "الأعمال والإدارة",
        "label_en": "Business & Management",
        "base_model": "qwen3:8b",
        "vram_gb": 5.0,
    },
    "custom": {
        "label_ar": "تخصص مخصص",
        "label_en": "Custom",
        "base_model": "qwen3:8b",
        "vram_gb": 5.0,
    },
}

VALID_SPECIALIZATIONS = list(SPECIALIZATIONS.keys())


def get_base_model(specialization: str) -> str:
    return SPECIALIZATIONS.get(specialization, SPECIALIZATIONS["custom"])["base_model"]


def get_vram_required(specialization: str) -> float:
    return SPECIALIZATIONS.get(specialization, SPECIALIZATIONS["custom"])["vram_gb"]
