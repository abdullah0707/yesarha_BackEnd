"""
قواعد مشتركة تُحقن في كل system prompt لأي متخصص.
هذا الملف هو المصدر الوحيد للقواعد الإلزامية العابرة لجميع النماذج.
"""

# تُضاف هذه القاعدة في بداية كل system prompt تلقائياً — لا تعتمد على ما في قاعدة البيانات
LANGUAGE_RULE = """\
════════════════════════════════════════════════════════════
⚠️  قاعدة اللغة — أولوية مطلقة لا تُلغى أبداً
⚠️  LANGUAGE RULE — ABSOLUTE PRIORITY, NEVER OVERRIDE
════════════════════════════════════════════════════════════

اكتشف لغة رسالة المستخدم وأجب حصراً بنفس اللغة.
Detect the language of the user's message and respond EXCLUSIVELY in that same language.

• المستخدم يكتب بالعربية    → ردّ بالعربية الكاملة
• المستخدم يكتب بالإنجليزية  → ردّ بالإنجليزية الكاملة
• User writes in Arabic      → respond entirely in Arabic
• User writes in English     → respond entirely in English
• أي لغة أخرى (فرنسية، تركية...) → ردّ بنفس تلك اللغة
• Any other language (French, Turkish…) → respond in that language

❌ لا تتحول للإنجليزية تلقائياً حتى لو البرومبت بالعربية
❌ NEVER default to English just because the system prompt is in Arabic
❌ لا تترجم ردك — استخدم لغة المستخدم مباشرة
❌ Do NOT translate your answer — use the user's language directly

════════════════════════════════════════════════════════════\
"""


def build_system_prompt(specialist_prompt: str) -> str:
    """
    يُضيف قاعدة اللغة الإلزامية في بداية أي system prompt.
    يُستدعى من جميع نقاط الدخول العامة للمتخصصين.
    """
    base = specialist_prompt.strip() if specialist_prompt else ""
    if not base:
        return LANGUAGE_RULE
    return f"{LANGUAGE_RULE}\n\n{base}"
