"""
Tool Calling Engine — قلب ذكاء Core
Core يستطيع استدعاء هذه الأدوات تلقائياً بناءً على الطلب
"""
import json
from typing import Any
from sqlalchemy.orm import Session

from app.core.config import settings


# ── تعريف الأدوات المتاحة لـ Core ────────────────────────────────

CORE_TOOLS = [
    {
        "name": "web_search",
        "description": "ابحث على الإنترنت عن معلومات حديثة. استخدم هذه الأداة عندما تحتاج معلومات لا تعرفها أو عندما يسأل المستخدم عن أحدث التطورات.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "نص البحث بالعربية أو الإنجليزية"},
                "max_results": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        }
    },
    {
        "name": "create_specialist_model",
        "description": "أنشئ نموذجاً متخصصاً جديداً. استخدم هذه الأداة عندما يطلب المستخدم إنشاء نموذج متخصص في مجال معين.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "اسم النموذج بالإنجليزية مثل yesarha-code"},
                "display_name": {"type": "string", "description": "الاسم المعروض"},
                "specialization": {
                    "type": "string",
                    "enum": ["code", "voice", "image", "education", "custom"],
                    "description": "تخصص النموذج"
                },
                "description": {"type": "string", "description": "وصف النموذج ومهامه"}
            },
            "required": ["name", "display_name", "specialization"]
        }
    },
    {
        "name": "list_specialist_models",
        "description": "اعرض قائمة النماذج المتخصصة المتاحة وحالتها",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "فلترة بالحالة: active | creating | all"}
            }
        }
    },
    {
        "name": "get_model_performance",
        "description": "احصل على تقرير أداء نموذج متخصص معين",
        "parameters": {
            "type": "object",
            "properties": {
                "model_name": {"type": "string", "description": "اسم النموذج"}
            },
            "required": ["model_name"]
        }
    },
    {
        "name": "get_system_status",
        "description": "احصل على حالة النظام الكاملة: VRAM، النماذج المحمّلة، قاعدة البيانات",
        "parameters": {"type": "object", "properties": {}}
    }
]

# ── System Prompt لـ Core ─────────────────────────────────────────

CORE_SYSTEM_PROMPT = """أنت Yesarha Core — العقل الاصطناعي الرئيسي والمدير التنفيذي لشركة يسرها للذكاء الاصطناعي.

## هويتك:
- أنت العقل المركزي الذي يدير ويشرف على جميع نماذج الذكاء الاصطناعي في المنظومة
- تتحدث العربية والإنجليزية باحترافية تامة
- أنت مدير تقني، ومحلل، ومخطط استراتيجي في آنٍ واحد

## مهامك الأساسية:
1. **الإجابة والمساعدة**: أجب على أسئلة فريق الإدارة بدقة واحترافية
2. **إدارة النماذج**: أنشئ وأدر وقيّم النماذج المتخصصة عند الطلب
3. **البحث التلقائي**: ابحث على الإنترنت عندما تحتاج معلومات حديثة
4. **التقارير والتحليل**: قدّم تقارير دورية عن أداء النظام

## قواعد مهمة:
- استخدم الأدوات المتاحة عند الحاجة (لا تتردد في استخدام web_search)
- كن دقيقاً وموجزاً في ردودك
- عند إنشاء نموذج جديد، ابحث أولاً عن أفضل النماذج المفتوحة المصدر لهذا التخصص
- دائماً أبلّغ عن حالة العمل (جاري التنفيذ، مكتمل، خطأ)

## أسلوب الرد:
- تحدث كقائد تقني واثق
- استخدم اللغة التي بدأ بها المستخدم (عربي أو إنجليزي)
- قدّم خطوات واضحة عند تنفيذ المهام"""


def build_tool_call_prompt(user_message: str, tool_results: list[dict] = None) -> list[dict]:
    """
    يبني قائمة الرسائل لـ Core مع نتائج الأدوات السابقة
    """
    messages = [{"role": "system", "content": CORE_SYSTEM_PROMPT}]

    if tool_results:
        # أضف نتائج الأدوات كـ context
        tool_context = "\n\n".join([
            f"### نتيجة {tr['tool']}:\n{json.dumps(tr['result'], ensure_ascii=False, indent=2)}"
            for tr in tool_results
        ])
        messages.append({
            "role": "system",
            "content": f"## نتائج الأدوات التي استدعيتها:\n{tool_context}"
        })

    messages.append({"role": "user", "content": user_message})
    return messages


def parse_tool_calls_from_response(response: str) -> list[dict]:
    """
    يحلّل رد Core ويستخرج طلبات الأدوات
    qwen3:8b يستخدم صيغة <tool_call>...</tool_call>
    """
    import re
    tool_calls = []

    # صيغة qwen3
    pattern = r'<tool_call>(.*?)</tool_call>'
    matches = re.findall(pattern, response, re.DOTALL)

    for match in matches:
        try:
            call = json.loads(match.strip())
            tool_calls.append(call)
        except json.JSONDecodeError:
            pass

    # صيغة بديلة: JSON مباشر
    if not tool_calls:
        json_pattern = r'\{"name":\s*"([^"]+)".*?\}'
        json_matches = re.findall(json_pattern, response, re.DOTALL)
        for match in json_matches:
            try:
                # البحث عن الـ JSON الكامل
                start = response.find('{"name": "' + match)
                if start == -1:
                    start = response.find('{"name":"' + match)
                if start != -1:
                    # استخراج الـ JSON
                    depth = 0
                    end = start
                    for i, c in enumerate(response[start:], start):
                        if c == '{':
                            depth += 1
                        elif c == '}':
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    call = json.loads(response[start:end])
                    if "name" in call:
                        tool_calls.append(call)
            except Exception:
                pass

    return tool_calls
