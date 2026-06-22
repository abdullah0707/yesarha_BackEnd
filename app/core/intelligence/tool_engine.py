"""
Tool Calling Engine — قلب ذكاء Core
يستخدم صيغة Ollama الحقيقية لاستدعاء الأدوات (OpenAI-compatible function calling)
بدلاً من تحليل نص حر — هذا أوثق وأكثر استقراراً ويمنع الهلوسة.
"""
from app.core.intelligence.specializations import VALID_SPECIALIZATIONS


# ── تعريف الأدوات بصيغة Ollama/OpenAI الرسمية ────────────────────
# كل أداة: {"type": "function", "function": {name, description, parameters}}

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
            "description": "أنشئ نموذجاً متخصصاً جديداً عند طلب المستخدم تخصصاً معيناً.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "اسم النموذج بالإنجليزية مثل yesarha-code"},
                    "display_name": {"type": "string", "description": "الاسم المعروض"},
                    "specialization": {
                        "type": "string",
                        "enum": VALID_SPECIALIZATIONS,
                    },
                    "description": {"type": "string"}
                },
                "required": ["name", "display_name", "specialization"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_specialist_models",
            "description": "اعرض قائمة النماذج المتخصصة المتاحة وحالتها.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "active | creating | all"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_model_performance",
            "description": "احصل على تقرير أداء نموذج متخصص معين.",
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
            "description": "احصل على حالة النظام: VRAM، النماذج المحمّلة، قاعدة البيانات.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


# ── System Prompt لـ Core (مختصر ومباشر — يقلل الهلوسة) ───────────

CORE_SYSTEM_PROMPT = """أنت "Yesarha Core" — العقل التنفيذي والمدير التقني لمنظومة يسرها للذكاء الاصطناعي. شركة يسرها قائمة عليك كعقلها المركزي.

هويتك: مدير تنفيذي وتقني وإداري واحد. تتحدث العربية والإنجليزية باحترافية. عند سؤالك "من أنت؟" أجب بإيجاز: أنت Yesarha Core، العقل التنفيذي ليسرها.

مهامك: إدارة النماذج المتخصصة (كود، صوت، صور، تعليم)، البحث عند الحاجة لمعلومات حديثة، مراقبة الأداء، التخطيط والتحليل.

قواعد:
- أجب مباشرة وبإيجاز ما لم يُطلب التفصيل.
- استخدم أداة web_search فقط عند الحاجة الفعلية لمعلومة حديثة لا تعرفها — لا تستخدمها لكل سؤال.
- لا تكرر نفس الجملة أو الكلمة. لا تخترع نصاً عشوائياً.
- استخدم لغة المستخدم نفسها (عربي أو إنجليزي).
- لا تقل "بصفتي نموذج ذكاء اصطناعي" — تحدث كـ Yesarha Core مباشرة."""


def build_messages(user_message: str, history: list[dict] = None,
                    tool_context: str = None) -> list[dict]:
    """يبني قائمة الرسائل الأساسية لـ Core"""
    messages = [{"role": "system", "content": CORE_SYSTEM_PROMPT}]

    if tool_context:
        messages.append({
            "role": "system",
            "content": f"نتائج الأدوات التي استُدعيت:\n{tool_context}"
        })

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": user_message})
    return messages
