"""
Core prompt utilities for Yesarha specialists.

Architectural rule: Code enforces all critical behaviors (identity, out-of-scope,
confidentiality). The model's only job is natural language generation.
"""

_IDENTITY_KEYWORDS = (
    "من أنت", "من انت", "من أنتِ", "من أنتَ",
    "عرّفني بنفسك", "عرفني بنفسك", "عرفنى بنفسك",
    "ما اسمك", "ما هو اسمك", "ما هويتك", "ما هي هويتك",
    "ماذا تفعل", "ما دورك", "ما وظيفتك",
    "عرف نفسك", "عرّف نفسك",
    "who are you", "introduce yourself", "what are you",
    "tell me about yourself", "what is your name", "what do you do",
)


def is_identity_question(question: str) -> bool:
    """Detected before reaching the model — bypasses model call entirely."""
    q = question.strip().lower()
    return any(kw in q for kw in _IDENTITY_KEYWORDS)


def detect_language(text: str) -> str:
    if not text:
        return "unknown"
    arabic = sum(1 for c in text if "؀" <= c <= "ۿ")
    ratio = arabic / max(len(text.strip()), 1)
    return "ar" if ratio > 0.15 else "en"


def build_system_prompt(
    specialist_prompt: str,
    intro_text: str | None = None,
    detected_lang: str = "unknown",
) -> str:
    """
    Builds a minimal system prompt.
    Identity/confidentiality/out-of-scope are enforced by code — not here.
    """
    base = specialist_prompt.strip() if specialist_prompt else ""

    if detected_lang == "ar":
        lang_line = "أجب بالعربية فقط."
    elif detected_lang == "en":
        lang_line = "Answer in English only."
    else:
        lang_line = "Always respond in the same language the user used."

    return f"{base}\n{lang_line}" if base else lang_line


_INTRO_KEYWORDS = (
    "مقدمة", "مقدمه", "مقدمه للدورة", "مقدمة الدرس", "اشرح المقدمة", "اعمل مقدمة",
    "عرفني بالدورة", "عرفني بهذه الدورة", "عن الدورة", "نبذة عن الدورة",
    "ما هي الدورة", "ما هذه الدورة", "ما هذا الدرس", "ما الدرس",
    "introduction", "intro", "overview", "about this course", "what is this course",
    "tell me about this", "summarize this course",
)

_OBJECTIVES_KEYWORDS = (
    "أهداف", "اهداف", "الأهداف", "ما الأهداف", "ما هي الأهداف", "ما هيا الاهداف",
    "نواتج التعلم", "نواتج الدوره", "نواتج الدورة", "نواتج التعليم",
    "ما سأتعلم", "ماذا سأتعلم", "ماذا سوف اتعلم", "ماذا ستتعلم",
    "ما الذي سأتعلمه", "هيتعلم ايه", "هتتعلم ايه", "هيستفيد ايه",
    "learning objectives", "learning outcomes", "what will i learn",
    "goals", "course goals", "what do i gain", "what can i do after",
)


def is_intro_request(question: str) -> bool:
    """يكتشف طلبات المقدمة/النظرة العامة — يُستخدم كل المحتوى كسياق."""
    q = question.strip().lower()
    return any(kw in q for kw in _INTRO_KEYWORDS)


def is_objectives_request(question: str) -> bool:
    """يكتشف طلبات أهداف الدورة/نواتج التعلم — يُستخدم كل المحتوى كسياق."""
    q = question.strip().lower()
    return any(kw in q for kw in _OBJECTIVES_KEYWORDS)


_VISUAL_REQUEST_KEYWORDS = (
    "أرسم", "ارسم", "خريطة ذهنية", "خريطه ذهنيه", "خريطه", "مخطط", "رسم",
    "صورة توضيحية", "صوره توضيحيه", "وضح بصورة", "مثّل بيانياً", "رسم بياني",
    "شكل توضيحي", "diagram", "mind map", "mindmap", "draw", "visualize",
    "image", "picture", "chart", "illustration",
)


def is_visual_request(question: str) -> bool:
    """Detects student requests for visual content (mind maps, diagrams, images)."""
    q = question.strip().lower()
    return any(kw in q for kw in _VISUAL_REQUEST_KEYWORDS)


# ── Image Generation Detection ────────────────────────────────────────────────

_IMAGE_GEN_KEYWORDS = (
    # عربي — طلب صورة فنية/إيلوستريشن صريح
    "صمم صورة", "اعمل صورة", "ارسم صورة", "أنشئ صورة", "ولد صورة",
    "صمم رسمة", "اعمل رسمة", "ارسم رسمة",
    "إيلوستريشن", "صورة فنية", "صورة إبداعية", "صورة لمفهوم",
    "صور توضيحية", "رسمة توضيحية", "تصميم صورة",
    "generate image", "create image", "make image",
    "generate illustration", "create illustration",
    "draw an image", "draw a picture", "design an image",
)

# الخريطة الذهنية تبقى في is_visual_request — هنا فقط توليد صور SD
def is_image_gen_request(question: str) -> bool:
    """
    يكتشف طلبات توليد صور فنية عبر Stable Diffusion.
    مختلف عن is_visual_request() الذي يكتشف طلبات الخرائط/المخططات.
    """
    q = question.strip().lower()
    return any(kw in q for kw in _IMAGE_GEN_KEYWORDS)


_SD_PROMPT_SYSTEM = """\
أنت خبير في توليد صور تعليمية باستخدام Stable Diffusion.
مهمتك: تحليل الطلب واختيار النوع الصحيح للصورة ثم كتابة prompt إنجليزي احترافي.

══ قاعدة اختيار النوع ══

النوع A — INFOGRAPHIC:
متى: الطلب يحتوي مراحل / عناصر متعددة / مقارنة / قائمة / خطوات
الشكل: أشكال هندسية متدرجة + أيقونات مسطحة + لون مختلف لكل قسم + تخطيط شبكي أو عمودي
كلمات إلزامية: flat infographic layout, color-coded sections, icons, white background, clean modern design

النوع B — SCENE:
متى: الطلب يشرح مفهوماً واحداً أو ظاهرة أو آلية عمل
الشكل: مشهد بصري يُظهر الآلية والسبب والنتيجة معاً — ليس رمزاً أو أيقونة للمفهوم
كلمات إلزامية: cross-section view OR force diagram OR before-after scene, educational illustration, high detail

══ القاعدة الذهبية للنوع B ══
اسأل نفسك: "هل هذه الصورة تشرح الآلية أم تختار رمزاً؟"
❌ خطأ: الجاذبية = تفاحة ساقطة (رمز فقط)
✅ صح: الجاذبية = مقطع عرضي للأرض مع خطوط قوى الجذب المنحنية تسحب أجساماً بأحجام مختلفة نحو المركز
❌ خطأ: الكهرباء = برق (رمز)
✅ صح: مقطع عرضي لسلك يُظهر إلكترونات تتحرك في اتجاه واحد مع حقل مغناطيسي دائري حوله

══ قواعد ثابتة ══
- أجب بـ prompt إنجليزي فقط — لا كلام قبله أو بعده
- لا تطلب نصوصاً أو labels مكتوبة — SD لا يرسم نصاً واضحاً
- الصورة مفيدة علمياً أولاً — جميلة ثانياً
- لا تستخدم: "draw", "create", "make", "write", "show text"
- الطول: 25-45 كلمة

══ أمثلة ══

طلب: "صمم صورة لمراحل دورة المياه" → INFOGRAPHIC
prompt: "water cycle flat infographic, color-coded sections showing evaporation ocean blue, cloud formation gray, rainfall green arrows, river flow returning to sea, icons each stage, clean white background, modern educational design"

طلب: "صمم صورة توضح مفهوم الجاذبية" → SCENE (آلية لا رمز)
prompt: "cross-section view of Earth with visible curved gravitational field lines pulling objects of different sizes toward planet core, small satellite moon large asteroid all bending toward center, space background, force arrows showing acceleration, educational physics visualization, high detail"

طلب: "صمم صورة لأنواع التربة الثلاثة" → INFOGRAPHIC
prompt: "three soil types flat infographic, side-by-side vertical cross-sections sandy soil tan loose particles, clay soil dark dense, loam soil layered, color-coded columns, texture details visible, white background, clean educational design"

طلب: "صمم صورة لكيفية عمل القلب" → SCENE (آلية)
prompt: "detailed anatomical cross-section of human heart showing four chambers, blue deoxygenated blood arrows entering right side, red oxygenated blood exiting left side, valves opening closing, lung circulation loop visible, educational medical illustration, high detail white background"
"""


def build_sd_prompt(arabic_request: str, context: str = "", color_palette: str = "") -> str:
    """
    يبني user message لتحويل الطلب العربي لـ SD prompt.
    color_palette: ألوان الدورة مثل "blue #1E3A5F, gold #D4A017, white"
    """
    user_content = f"طلب: {arabic_request}"
    if context.strip():
        user_content += f"\n\nسياق الدرس:\n{context[:400]}"
    if color_palette.strip():
        user_content += f"\n\nألوان الدورة المطلوب استخدامها: {color_palette}"
    return user_content


def get_sd_system_prompt() -> str:
    return _SD_PROMPT_SYSTEM


# ── Infographic Detection ──────────────────────────────────────────────────────

_INFOGRAPHIC_KEYWORDS = (
    # Arabic
    "مراحل", "خطوات", "أنواع", "عناصر", "مقارنة", "الفرق بين",
    "أسباب", "نتائج", "فوائد", "مزايا", "عيوب", "مكونات",
    "أجزاء", "أقسام", "تصنيف", "قائمة", "خصائص", "صفات",
    "وظائف", "طرق", "أساليب", "مبادئ", "قواعد", "مقارنه",
    # English
    "stages", "steps", "types", "elements", "comparison",
    "differences", "causes", "results", "benefits", "advantages",
    "components", "parts", "categories", "features", "list",
)


def is_infographic_request(question: str) -> bool:
    """True if the request has multiple elements → infographic (SVG). False → SD scene."""
    q = question.strip().lower()
    return any(kw in q for kw in _INFOGRAPHIC_KEYWORDS)


# ── Infographic JSON Prompt ────────────────────────────────────────────────────

_INFOGRAPHIC_JSON_SYSTEM = """\
أنت نظام توليد إنفوجراف تعليمي.
مهمتك: تحليل الطلب واستخراج المحتوى كـ JSON منظم لرسم إنفوجراف.

الأنواع المتاحة:
• "sequential" — مراحل أو خطوات متتابعة (3-5 عناصر)
• "grid"       — قائمة عناصر/أنواع/فوائد/مكونات (4-8 عناصر)
• "comparison" — مقارنة مباشرة بين شيئين

قواعد صارمة:
- أجب بـ JSON فقط — لا markdown، لا شرح، لا كلام قبله أو بعده
- title: 3-6 كلمات
- heading لكل عنصر: 1-3 كلمات فقط
- body لكل عنصر: جملة 6-12 كلمة
- أقصى عناصر: 5 للـ sequential، 8 للـ grid، 7 للـ comparison

أمثلة:

{"title":"مراحل دورة المياه","layout":"sequential","items":[{"heading":"التبخر","body":"الشمس تحوّل الماء إلى بخار مائي"},{"heading":"التكاثف","body":"البخار يبرد ويتحول إلى سحاب"},{"heading":"الهطول","body":"المطر والثلج يسقطان على الأرض"},{"heading":"التجميع","body":"الماء يعود للبحار عبر الأنهار"}]}

{"title":"أنواع الطاقة المتجددة","layout":"grid","items":[{"heading":"الطاقة الشمسية","body":"مستمدة من أشعة الشمس مباشرة"},{"heading":"طاقة الرياح","body":"من حركة الهواء عبر التوربينات"},{"heading":"الطاقة المائية","body":"من حركة المياه في السدود"},{"heading":"طاقة الأرض","body":"من الحرارة الجوفية للكرة الأرضية"}]}

{"title":"الفرق بين الخلية النباتية والحيوانية","layout":"comparison","left_title":"النباتية","right_title":"الحيوانية","items":[{"left":"لها جدار خلوي","right":"بدون جدار خلوي"},{"left":"لها بلاستيدات خضراء","right":"بدون بلاستيدات"},{"left":"فجوة مركزية كبيرة","right":"فجوات صغيرة متعددة"}]}
"""


def get_infographic_system_prompt() -> str:
    return _INFOGRAPHIC_JSON_SYSTEM


def build_infographic_prompt(arabic_request: str, context: str = "") -> str:
    user = f"طلب: {arabic_request}"
    if context.strip():
        user += f"\n\nسياق الدرس:\n{context[:400]}"
    return user


# Markers that indicate a system prompt leak in model output
_LEAK_MARKERS = (
    "══════", "STRICT RULES", "INTERNAL INSTRUCTIONS", "⛔⛔⛔", "CONFIDENTIAL",
    "System:", "Instructions:", "Prompt:",
    "قواعد صارمة", "تعليمات النظام", "أنت خبير في",
)


def _has_repetition(text: str, max_repeats: int = 3) -> bool:
    """Detects stuck-model loops where the same sentence repeats 3+ times."""
    import re as _re
    sentences = [s.strip() for s in _re.split(r'[.،\n؟?!]+', text) if len(s.strip()) > 15]
    if len(sentences) < max_repeats:
        return False
    seen: dict = {}
    for s in sentences:
        seen[s] = seen.get(s, 0) + 1
        if seen[s] >= max_repeats:
            return True
    return False


def passes_quality_gate(response: str, expected_lang: str) -> bool:
    """
    Code-level quality check on model output.
    Returns False if: empty, too short, leaks prompt structure,
    stuck in repetition loop, or uses the wrong language.
    """
    if not response or len(response.strip()) < 10:
        return False

    if any(m in response for m in _LEAK_MARKERS):
        return False

    if _has_repetition(response):
        return False

    # Language check only for responses long enough to judge
    if expected_lang in ("ar", "en") and len(response.strip()) > 30:
        actual_lang = detect_language(response)
        if actual_lang != expected_lang:
            return False

    return True
