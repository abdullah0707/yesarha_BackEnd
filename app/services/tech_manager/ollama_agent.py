"""
Tech Manager — Ollama Agent  v3.0
المدير التقني: يعمل على Ollama self-hosted (لا Groq، لا APIs خارجية مدفوعة)

المبادئ:
- يُشغّل نماذج Ollama المتاحة على الخادم (qwen3:8b أو أفضل)
- أدوات آمنة: لا write_file، لا propose_fix بمحتوى كامل
- propose_line_edit فقط — تعديل جراحي بـ old_content + new_content
- التحقق يحدث عند إنشاء الاقتراح وعند تطبيقه
- Temperature=0 للحصول على نتائج حتمية
- ReAct loop: يفكر → يختار أداة → يُنفّذها → يكرر (max 12 دورة)
- SSE streaming لكل خطوة
"""
import asyncio
import json
import re
from datetime import datetime
from typing import AsyncGenerator

import httpx

from app.services.tech_manager import patch_engine, scanner

# ── إعدادات الوكيل ─────────────────────────────────────────────────────
MAX_ITERS     = 12          # الحد الأقصى للدورات
TIMEOUT_SEC   = 120         # timeout لكل طلب Ollama
MAX_PROPOSALS = 6           # حد أقصى للاقتراحات في جلسة واحدة

# الملفات المحمية — Ollama يعرفها ويتجنب تعديلها
_PROTECTED = ", ".join(sorted(patch_engine.PROTECTED_FILES))

SYSTEM_PROMPT = f"""\
أنت المدير التقني لـ Yesarha Core — نظام ذكاء اصطناعي self-hosted.
دورك: تشخيص المشاكل التقنية واقتراح إصلاحات جراحية دقيقة.

━━━ قواعد أساسية (حرفية لا تُخالَف) ━━━
1. لا تُعدّل ملفاً مباشرة — كل تعديل يمر عبر اقتراح يوافق عليه المشرف
2. للاقتراح: استخدم propose_line_edit أو propose_append أو propose_env_reminder
3. old_content في propose_line_edit يجب أن يكون نسخة حرفية من read_file
4. الملفات المحمية ({_PROTECTED}): استخدم propose_env_reminder فقط، لا تحاول تعديلها
5. requirements.txt: استخدم propose_append فقط (لإضافة مكتبة جديدة)
6. حد أقصى {MAX_PROPOSALS} اقتراحات في الجلسة
7. للمشاكل في config.py: الإصلاح في .env وليس في الكود

━━━ استدعاء الأدوات ━━━
لاستدعاء أداة، أخرج JSON array فقط (لا نص آخر):
[{{"name": "TOOL_NAME", "arguments": {{}}}}]

━━━ الأدوات المتاحة ━━━
• run_security_scan {{"scope": "all"}}
  — فحص أمني شامل بـ regex (لا يحتاج LLM)
  — يُرجع: scanned_files, total_findings, findings[]

• run_package_check {{}}
  — يقارن requirements.txt بالمكتبات المثبتة
  — يُرجع: missing[], present[]

• read_file {{"path": "relative/path.py"}}
  — يقرأ الملف مع أرقام الأسطر
  — يُرجع: content مع أرقام كـ "   1 | from fastapi..."

• list_directory {{"path": "relative/path/"}}
  — يسرد ملفات مجلد

• propose_line_edit {{
    "file_path": "api/v1/admin/users.py",
    "old_content": "النص الحرفي الموجود في الملف",
    "new_content": "النص البديل",
    "reason": "لماذا هذا التعديل ضروري",
    "severity": "critical|warning|info",
    "title": "عنوان الاقتراح"
  }}
  — اقتراح تعديل جراحي (يُرفض إذا old_content غير موجود)

• propose_append {{
    "file_path": "requirements.txt",
    "new_content": "اسم_المكتبة",
    "reason": "لماذا نحتاج هذه المكتبة",
    "severity": "warning",
    "title": "عنوان الاقتراح"
  }}
  — إضافة محتوى آمن في نهاية ملف (لا يُزيل شيئاً)

• propose_env_reminder {{
    "key": "JWT_SECRET_KEY",
    "issue": "وصف المشكلة",
    "recommendation": "ماذا يجب تغييره في .env"
  }}
  — تنبيه يدوي للمشرف لتعديل متغير بيئي (للملفات المحمية)

━━━ خطوات العمل المثلى ━━━
1. شغّل run_security_scan و run_package_check أولاً
2. لكل مشكلة وجدتها: اقرأ الملف بـ read_file للحصول على السياق
3. اقترح إصلاحاً محدداً لكل مشكلة
4. في الرد النهائي: لخّص ما وجدته وما اقترحته
"""

# ── تعريف الأدوات لـ Ollama (صيغة OpenAI-compatible) ─────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_security_scan",
            "description": "Run automated security pattern scan across Python files",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "all | specific_dir", "default": "all"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_package_check",
            "description": "Compare requirements.txt with actually installed packages",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a project file with line numbers for precise editing",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from project root"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and subdirectories",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative directory path"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_line_edit",
            "description": "Propose a surgical edit: replace old_content with new_content in a file. old_content MUST be exact copy from read_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path":   {"type": "string"},
                    "old_content": {"type": "string", "description": "Exact text currently in the file"},
                    "new_content": {"type": "string", "description": "Replacement text"},
                    "reason":      {"type": "string"},
                    "severity":    {"type": "string", "enum": ["critical", "warning", "info"]},
                    "title":       {"type": "string"},
                },
                "required": ["file_path", "old_content", "new_content", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_append",
            "description": "Safely append content at end of file (never removes existing content)",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path":   {"type": "string"},
                    "new_content": {"type": "string"},
                    "reason":      {"type": "string"},
                    "severity":    {"type": "string", "enum": ["critical", "warning", "info"]},
                    "title":       {"type": "string"},
                },
                "required": ["file_path", "new_content", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_env_reminder",
            "description": "For protected files: alert the admin to update an environment variable in .env",
            "parameters": {
                "type": "object",
                "properties": {
                    "key":            {"type": "string"},
                    "issue":          {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["key", "issue", "recommendation"],
            },
        },
    },
]

_VALID_TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

# ══════════════════════════════════════════════════════════════════════════
# Tool Executors
# ══════════════════════════════════════════════════════════════════════════

def _exec_run_security_scan(args: dict) -> dict:
    scope = args.get("scope", "all")
    return scanner.scan_security(scope)


def _exec_run_package_check(_args: dict) -> dict:
    return scanner.check_packages()


def _exec_read_file(args: dict) -> dict:
    path = args.get("path", "")
    if not path:
        return {"error": "path is required"}
    result = scanner.read_file_with_lines(path)
    # أرجع content فقط (بدون raw لتوفير tokens)
    if "raw" in result:
        result = {k: v for k, v in result.items() if k != "raw"}
    return result


def _exec_list_directory(args: dict) -> dict:
    return scanner.list_directory(args.get("path", ""))


def _exec_propose_line_edit(args: dict) -> dict:
    result = patch_engine.add_proposal(
        title=args.get("title", f"تعديل في {args.get('file_path', '?')}"),
        description=args.get("reason", ""),
        file_path=args.get("file_path", ""),
        patch_type="line_edit",
        reason=args.get("reason", ""),
        severity=args.get("severity", "warning"),
        old_content=args.get("old_content"),
        new_content=args.get("new_content"),
    )
    return result


def _exec_propose_append(args: dict) -> dict:
    result = patch_engine.add_proposal(
        title=args.get("title", f"إضافة إلى {args.get('file_path', '?')}"),
        description=args.get("reason", ""),
        file_path=args.get("file_path", ""),
        patch_type="append",
        reason=args.get("reason", ""),
        severity=args.get("severity", "warning"),
        new_content=args.get("new_content"),
    )
    return result


def _exec_propose_env_reminder(args: dict) -> dict:
    key = args.get("key", "UNKNOWN_KEY")
    issue = args.get("issue", "")
    recommendation = args.get("recommendation", "")
    result = patch_engine.add_proposal(
        title=f"تنبيه يدوي: تحديث {key} في .env",
        description=issue,
        file_path="core/config.py",   # للتوثيق فقط
        patch_type="env_reminder",
        reason=f"{issue}\n\nالتوصية: {recommendation}",
        severity="critical",
    )
    return result


TOOL_MAP = {
    "run_security_scan":  _exec_run_security_scan,
    "run_package_check":  _exec_run_package_check,
    "read_file":          _exec_read_file,
    "list_directory":     _exec_list_directory,
    "propose_line_edit":  _exec_propose_line_edit,
    "propose_append":     _exec_propose_append,
    "propose_env_reminder": _exec_propose_env_reminder,
}

# ══════════════════════════════════════════════════════════════════════════
# Fallback Text Parser (للنماذج التي ترجع JSON كنص)
# ══════════════════════════════════════════════════════════════════════════

def _extract_tool_calls_from_text(content: str) -> list[dict]:
    """يستخرج tool calls من نص خام (fallback)."""
    blocks = []
    for block in re.findall(r"```(?:javascript|json|tool_call)?\s*\n([\s\S]*?)```", content):
        blocks.append(block.strip())
    stripped = content.strip()
    if stripped.startswith("["):
        blocks.append(stripped)

    for block in blocks:
        if not block.strip().startswith("["):
            continue
        try:
            parsed = json.loads(block)
            if not isinstance(parsed, list):
                continue
            result = []
            for item in parsed:
                name = item.get("name") or item.get("function", {}).get("name", "")
                if name not in _VALID_TOOL_NAMES:
                    continue
                args = (
                    item.get("arguments")
                    or item.get("parameters")
                    or item.get("args")
                    or {}
                )
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                result.append({"function": {"name": name, "arguments": args}})
            if result:
                return result
        except Exception:
            continue
    return []


# ══════════════════════════════════════════════════════════════════════════
# SSE Summary Helpers
# ══════════════════════════════════════════════════════════════════════════

def _summarize_result(tool_name: str, result: dict) -> str:
    if "error" in result:
        return f"خطأ: {result['error']}"

    if tool_name == "run_security_scan":
        n = result.get("total_findings", 0)
        c = result.get("critical", 0)
        w = result.get("warnings", 0)
        files = result.get("scanned_files", 0)
        if n == 0:
            return f"فحصت {files} ملفاً — لم أجد مشاكل أمنية"
        return (
            f"فحصت {files} ملفاً — وجدت {n} مشكلة "
            f"({c} حرجة، {w} تحذير)"
        )

    if tool_name == "run_package_check":
        missing = result.get("missing_count", 0)
        present = result.get("present_count", 0)
        if missing == 0:
            return f"جميع المكتبات ({present}) مثبتة"
        return f"مكتبات مفقودة ({missing}): {', '.join(result.get('missing', []))}"

    if tool_name == "read_file":
        lines = result.get("lines_count", 0)
        path = result.get("path", "?")
        return f"قرأت {path} ({lines} سطر)"

    if tool_name == "list_directory":
        entries = result.get("entries", [])
        return f"وجدت {len(entries)} عنصر"

    if tool_name in ("propose_line_edit", "propose_append", "propose_env_reminder"):
        if result.get("success"):
            pid = result.get("proposal_id", "?")
            return f"✅ أُنشئ اقتراح: {pid}"
        return f"❌ فشل الاقتراح: {result.get('error', 'unknown')}"

    return json.dumps(result, ensure_ascii=False)[:300]


# ══════════════════════════════════════════════════════════════════════════
# Main Agent Loop
# ══════════════════════════════════════════════════════════════════════════

async def run_tech_manager(
    problem: str,
    ollama_base_url: str,
    model: str,
) -> AsyncGenerator[dict, None]:
    """
    يُشغّل المدير التقني كـ ReAct loop ويبثّ SSE events:
    - thinking: جملة تفكير
    - tool_start: بدء أداة
    - tool_result: نتيجة أداة
    - proposal: اقتراح جديد
    - done: انتهى مع الملخص
    - error: خطأ
    """
    url = f"{ollama_base_url.rstrip('/')}/api/chat"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": problem},
    ]

    proposals_created = 0
    tool_calls_log: list[str] = []  # لمنع تكرار نفس الأداة بنفس المعاملات

    yield {"type": "thinking", "content": f"🔍 بدأ التحليل: {problem[:100]}"}
    yield {"type": "thinking", "content": f"🤖 النموذج: {model} | الخادم: {ollama_base_url}"}

    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        for iteration in range(MAX_ITERS):
            # حد أقصى للاقتراحات
            if proposals_created >= MAX_PROPOSALS:
                yield {"type": "thinking", "content": f"⏹️ وصلت للحد الأقصى ({MAX_PROPOSALS} اقتراحات)"}
                break

            # ── استدعاء Ollama ─────────────────────────────────────────
            payload = {
                "model":    model,
                "messages": messages,
                "tools":    TOOLS,
                "stream":   False,
                "options":  {"temperature": 0},
            }

            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as ex:
                yield {"type": "error", "content": f"Ollama HTTP error: {ex.response.status_code}"}
                return
            except Exception as ex:
                yield {"type": "error", "content": f"Ollama error: {ex}"}
                return

            msg = data.get("message", {})
            content = msg.get("content", "")
            raw_tool_calls = msg.get("tool_calls", [])

            # ── محاولة fallback parsing إذا لم ترجع tool_calls ───────
            if not raw_tool_calls and content:
                raw_tool_calls = _extract_tool_calls_from_text(content)

            # ── لا أدوات = رد نهائي ────────────────────────────────────
            if not raw_tool_calls:
                final = content or "اكتمل التحليل."
                yield {"type": "done", "content": final, "proposals_created": proposals_created}
                return

            # ── تنفيذ الأدوات ──────────────────────────────────────────
            tool_results_for_model = []
            any_new_tool = False

            for tc in raw_tool_calls:
                fn   = tc.get("function", tc)
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                if name not in _VALID_TOOL_NAMES:
                    continue

                # منع التكرار الحرفي
                call_sig = f"{name}:{json.dumps(args, sort_keys=True)}"
                if call_sig in tool_calls_log:
                    yield {"type": "thinking", "content": f"⏭️ تخطي تكرار: {name}"}
                    continue

                tool_calls_log.append(call_sig)
                any_new_tool = True

                yield {"type": "tool_start", "tool": name, "args": args}

                # ── تنفيذ الأداة في thread منفصل (لا يحجب event loop) ──
                executor = TOOL_MAP.get(name)
                if executor is None:
                    result = {"error": f"Unknown tool: {name}"}
                else:
                    try:
                        result = await asyncio.to_thread(executor, args)
                    except Exception as ex:
                        result = {"error": str(ex)}

                summary = _summarize_result(name, result)
                yield {"type": "tool_result", "tool": name, "summary": summary, "result": result}

                # تتبع الاقتراحات
                if name in ("propose_line_edit", "propose_append", "propose_env_reminder"):
                    if result.get("success"):
                        proposals_created += 1
                        yield {
                            "type":     "proposal",
                            "proposal": result.get("proposal", {}),
                            "id":       result.get("proposal_id"),
                        }

                # أضف نتيجة الأداة للسياق
                tool_results_for_model.append({
                    "role":    "tool",
                    "content": json.dumps(result, ensure_ascii=False, default=str)[:2000],
                    "name":    name,
                })

            if not any_new_tool:
                yield {"type": "done", "content": "اكتمل التحليل — لا أدوات جديدة.", "proposals_created": proposals_created}
                return

            # ── أضف رسالة الوكيل + النتائج للتاريخ ───────────────────
            messages.append({"role": "assistant", "content": content, "tool_calls": raw_tool_calls})
            messages.extend(tool_results_for_model)

        # انتهت الدورات
        yield {
            "type":    "done",
            "content": f"انتهى التحليل ({MAX_ITERS} دورة). أُنشئت {proposals_created} اقتراحات.",
            "proposals_created": proposals_created,
        }
