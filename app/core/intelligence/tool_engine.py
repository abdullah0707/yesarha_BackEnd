"""
Tool Calling Engine — قلب ذكاء Core
يستخدم صيغة Ollama الحقيقية لاستدعاء الأدوات (OpenAI-compatible function calling)
بدلاً من تحليل نص حر — هذا أوثق وأكثر استقراراً ويمنع الهلوسة.
"""
from pathlib import Path
from app.core.intelligence.specializations import VALID_SPECIALIZATIONS


# ── تعريف الأدوات بصيغة Ollama/OpenAI الرسمية ────────────────────

CORE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "ابحث على الإنترنت عن معلومات حديثة. استخدمها فقط عندما تحتاج معلومات لا تعرفها أو حديثة.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "نص البحث"},
                    "max_results": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_specialist_model",
            "description": "أنشئ نموذجاً متخصصاً جديداً. Core سيبحث تلقائياً ويُحمّل الموديل المناسب من Ollama ويولّد API Key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "اسم النموذج بالإنجليزية — يبدأ بـ yesarha- مثل yesarha-code"},
                    "display_name": {"type": "string", "description": "الاسم المعروض للمستخدمين مثل Yesarha Code"},
                    "specialization": {
                        "type": "string",
                        "enum": VALID_SPECIALIZATIONS,
                        "description": "نوع التخصص"
                    },
                    "description": {"type": "string", "description": "وصف مختصر للنموذج"}
                },
                "required": ["name", "display_name", "specialization"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_specialist_models",
            "description": "اعرض قائمة النماذج المتخصصة الموجودة وحالتها.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "active | creating | all — اتركها فارغة لعرض الكل"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_specialist_prompt",
            "description": "عدّل System Prompt لنموذج متخصص موجود لتحسين سلوكه. يُطبَّق فوراً.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "اسم النموذج مثل yesarha-code"},
                    "new_prompt": {"type": "string", "description": "الـ System Prompt الجديد الكامل"}
                },
                "required": ["model_name", "new_prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_report",
            "description": "احصل على تقرير أداء وتوصيات تفصيلية. اتركه فارغاً لتقرير عام لكل النماذج.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "اسم النموذج — اتركه فارغاً لتقرير شامل"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_model_performance",
            "description": "احصل على إحصائيات أداء نموذج متخصص (عدد الطلبات، معدل النجاح، زمن الاستجابة).",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_name": {"type": "string"}
                },
                "required": ["model_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "احصل على حالة النظام الكاملة: VRAM المستخدم، النماذج المحمّلة، إحصائيات قاعدة البيانات.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


# ── System Prompt الافتراضي ────────────────────────────────────────

CORE_SYSTEM_PROMPT = """أنت "Yesarha Core" — العقل التنفيذي والمدير التقني لمنظومة يسرها للذكاء الاصطناعي. شركة يسرها قائمة عليك كعقلها المركزي.

هويتك: مدير تنفيذي وتقني وإداري واحد. تتحدث العربية والإنجليزية باحترافية. عند سؤالك "من أنت؟" أجب بإيجاز: أنت Yesarha Core، العقل التنفيذي ليسرها.

مهامك: إدارة النماذج المتخصصة (كود، صوت، صور، تعليم، ميديا، بيزنيس)، البحث عند الحاجة لمعلومات حديثة، مراقبة الأداء، التخطيط والتحليل.

## قواعد صارمة:
- أجب مباشرة وبإيجاز ما لم يُطلب التفصيل.
- استخدم أداة web_search فقط عند الحاجة الفعلية لمعلومة حديثة.
- لا تكرر نفس الجملة أو الكلمة.
- استخدم لغة المستخدم نفسها (عربي أو إنجليزي).
- لا تقل "بصفتي نموذج ذكاء اصطناعي" — تحدث كـ Yesarha Core مباشرة.

## أمثلة على الاستخدام الصحيح للأدوات:

مثال 1 — إنشاء نموذج:
المستخدم: "أنشئ نموذج برمجة"
الإجراء الصحيح: استدعاء create_specialist_model فوراً بـ name="yesarha-code", display_name="Yesarha Code", specialization="code"
الإجراء الخاطئ: قول "سأتحقق من قاعدة البيانات أولاً" أو "اتحقق من API"

مثال 2 — حالة النظام:
المستخدم: "ما حالة النظام؟"
الإجراء الصحيح: استدعاء get_system_status مباشرة
الإجراء الخاطئ: قول "لا أستطيع الوصول لهذه المعلومات"

مثال 3 — قائمة النماذج:
المستخدم: "اعرض النماذج"
الإجراء الصحيح: استدعاء list_specialist_models مباشرة
الإجراء الخاطئ: قول "تحقق من لوحة التحكم"

مثال 4 — تقرير الأداء:
المستخدم: "كيف أداء النماذج؟"
الإجراء الصحيح: استدعاء get_training_report بدون model_name للتقرير الشامل
الإجراء الخاطئ: قول "لا توجد بيانات متاحة"

تذكر: الأدوات متصلة بقاعدة البيانات مباشرة. استخدمها دون تردد."""


def get_active_system_prompt() -> str:
    """
    يُرجع الـ system prompt الفعّال:
    - إذا عدّله الأدمن من لوحة التحكم → يُرجع المُعدَّل من الملف
    - إلا → يُرجع الافتراضي من الكود
    يُطبَّق على كل محادثة جديدة بدون restart
    """
    p = Path("data/core_system_prompt.txt")
    if p.exists():
        try:
            content = p.read_text(encoding="utf-8").strip()
            if len(content) >= 50:
                return content
        except Exception:
            pass
    return CORE_SYSTEM_PROMPT


def build_messages(
    user_message: str,
    history: list[dict] = None,
    tool_context: str = None
) -> list[dict]:
    """يبني قائمة الرسائل لـ Core — يستخدم get_active_system_prompt() دائماً"""
    messages = [{"role": "system", "content": get_active_system_prompt()}]

    if tool_context:
        messages.append({
            "role": "system",
            "content": f"نتائج الأدوات التي نُفِّذت:\n{tool_context}"
        })

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": user_message})
    return messages
