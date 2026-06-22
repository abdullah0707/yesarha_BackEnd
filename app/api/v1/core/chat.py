"""
Core Chat API — مع Streaming كامل وTool Calling حقيقي عبر Ollama

التصميم:
1. مرحلة تحديد الأدوات: استدعاء non-streaming سريع مع tools= لمعرفة
   هل Core يحتاج أداة أم لا (يعتمد على tool_calls البنيوية من Ollama
   نفسها، وليس تحليل نص حر — هذا يمنع الهلوسة والتكرار).
2. إن وُجدت أدوات: تُنفَّذ، تُضاف نتائجها للسياق، ثم نكرر حتى 3 مرات.
3. الرد النهائي يُبَث (stream) حقيقياً للعميل توكناً توكناً.
"""
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, AppError, ErrorCodes
from app.core.config import settings
from app.models.user import Admin
from app.models.operations import Execution
from app.services.ollama_client import OllamaClient
from app.core.intelligence.tool_engine import build_messages, CORE_TOOLS
from app.core.intelligence.tool_executor import ToolExecutor
from app.core.intelligence.async_bridge import sync_gen_to_async

router = APIRouter(prefix="/core", tags=["Core Intelligence"])

MAX_TOOL_ITERATIONS = 3


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class CoreChatRequest(BaseModel):
    message: str
    history: Optional[list[dict]] = None
    stream: bool = True
    enable_tools: bool = True


class SpecialistChatRequest(BaseModel):
    message: str
    specialist_name: str
    history: Optional[list[dict]] = None
    stream: bool = True


def _get_client() -> OllamaClient:
    return OllamaClient(base_url=settings.OLLAMA_BASE_URL)


def _resolve_tool_context(tool_results: list[dict]) -> str:
    return "\n\n".join(
        f"### {tr['tool']}:\n{json.dumps(tr['result'], ensure_ascii=False, indent=2)}"
        for tr in tool_results
    )


# ── Core Chat ───────────────────────────────────────────────────

@router.post("/chat")
async def core_chat(
    payload: CoreChatRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    if payload.stream:
        return StreamingResponse(
            _stream_core_response(payload, db, admin),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        )

    result = await _run_core_chat(payload, db, admin)
    return success(result)


async def _resolve_tools_phase(payload: CoreChatRequest, executor: ToolExecutor):
    """
    مرحلة تحديد الأدوات: استدعاء سريع non-streaming.
    يُرجع (tool_results, tool_context) — فارغة إذا لم تُستخدَم أدوات.
    يُنفَّذ في thread منفصل حتى لا يحجب event loop.
    """
    client = _get_client()
    tool_results: list[dict] = []
    tool_context = ""

    if not payload.enable_tools:
        return tool_results, tool_context

    messages = build_messages(payload.message, payload.history)

    for _ in range(MAX_TOOL_ITERATIONS):
        result = await sync_gen_to_async_single(
            client.chat, model=settings.CORE_MODEL,
            messages=messages, tools=CORE_TOOLS
        )

        calls = result.get("tool_calls") or []
        if not calls:
            return tool_results, tool_context

        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}

            tool_result = executor.execute(name, args)
            tool_results.append({"tool": name, "result": tool_result})

        tool_context = _resolve_tool_context(tool_results)
        messages = build_messages(payload.message, payload.history, tool_context)

    return tool_results, tool_context


async def sync_gen_to_async_single(fn, *args, **kwargs):
    """يُشغّل دالة sync عادية (غير generator) في thread منفصل بدون حجب event loop"""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def _stream_core_response(payload: CoreChatRequest, db: Session, admin: Admin):
    client = _get_client()
    executor = ToolExecutor(db=db)

    tool_results: list[dict] = []
    tool_context = ""

    # ── المرحلة 1: تحديد الأدوات (سريعة، non-streaming) ──
    if payload.enable_tools:
        yield _sse({"type": "thinking", "iteration": 1})
        try:
            tool_results, tool_context = await _resolve_tools_phase(payload, executor)
        except AppError as e:
            # خطأ حقيقي في الاتصال بـ Ollama — لا نُخفيه، نُبلّغ العميل فوراً
            yield _sse({"type": "error", "code": e.code, "message": e.message})
            return
        except Exception:
            # خطأ غير متوقع في منطق الأدوات نفسه (وليس Ollama) — نتابع بدون أدوات
            tool_results, tool_context = [], ""

        if tool_results:
            yield _sse({"type": "tool_start", "tools": [t["tool"] for t in tool_results]})
            for t in tool_results:
                yield _sse({"type": "tool_executing", "tool": t["tool"]})
                yield _sse({"type": "tool_done", "tool": t["tool"], "result": t["result"]})

    # ── المرحلة 2: الرد النهائي — streaming حقيقي توكناً توكناً ──
    final_messages = build_messages(payload.message, payload.history, tool_context or None)
    full_response = ""

    async for chunk in sync_gen_to_async(
        client.chat_stream,
        model=settings.CORE_MODEL,
        messages=final_messages,
    ):
        if chunk["type"] == "token":
            full_response += chunk["content"]
            yield _sse({"type": "token", "content": chunk["content"]})
        elif chunk["type"] == "done":
            yield _sse({"type": "stats", **{k: v for k, v in chunk.items() if k != "type"}})
        elif chunk["type"] == "error":
            yield _sse(chunk)
            return

    try:
        db.add(Execution(
            user_id=admin.id,
            intent="core_chat",
            tool="yesarha_core",
            tool_input={"message": payload.message[:200]},
            status="success",
            result={"response_length": len(full_response), "tools_used": len(tool_results)}
        ))
        db.commit()
    except Exception:
        pass

    yield _sse({"type": "done", "full_response": full_response})
    yield "data: [DONE]\n\n"


async def _run_core_chat(payload: CoreChatRequest, db: Session, admin: Admin) -> dict:
    """Non-streaming version — نفس منطق المرحلتين لكن بدون SSE"""
    client = _get_client()
    executor = ToolExecutor(db=db)

    tool_results, tool_context = await _resolve_tools_phase(payload, executor)

    final_messages = build_messages(payload.message, payload.history, tool_context or None)
    result = client.chat(model=settings.CORE_MODEL, messages=final_messages)

    return {
        "content": result["content"],
        "tokens_input": result.get("tokens_input", 0),
        "tokens_output": result.get("tokens_output", 0),
        "latency_ms": result.get("latency_ms", 0),
        "tools_used": [t["tool"] for t in tool_results],
    }


# ── Specialist Chat ───────────────────────────────────────────────

@router.post("/specialist/chat")
async def specialist_chat(
    payload: SpecialistChatRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    from app.models.specialist import SpecialistModel

    specialist = db.query(SpecialistModel).filter(
        SpecialistModel.name == payload.specialist_name,
        SpecialistModel.status == "active"
    ).first()

    if not specialist:
        raise AppError(ErrorCodes.NOT_FOUND,
                       f"النموذج المتخصص '{payload.specialist_name}' غير موجود أو غير نشط", 404)

    if payload.stream:
        return StreamingResponse(
            _stream_specialist(payload, specialist, db, admin),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    client = _get_client()
    messages = [{"role": "system", "content": specialist.system_prompt or ""}]
    if payload.history:
        messages.extend(payload.history)
    messages.append({"role": "user", "content": payload.message})

    result = client.chat(model=specialist.base_model or settings.CORE_MODEL, messages=messages)
    specialist.total_requests = (specialist.total_requests or 0) + 1
    db.commit()

    return success({
        "content": result["content"],
        "specialist": specialist.display_name,
        "model": specialist.base_model,
        "tokens_input": result.get("tokens_input", 0),
        "tokens_output": result.get("tokens_output", 0),
        "latency_ms": result.get("latency_ms", 0),
    })


async def _stream_specialist(payload, specialist, db: Session, admin: Admin):
    client = _get_client()
    messages = [{"role": "system", "content": specialist.system_prompt or ""}]
    if payload.history:
        messages.extend(payload.history)
    messages.append({"role": "user", "content": payload.message})

    full_response = ""
    yield _sse({"type": "specialist_info", "name": specialist.display_name, "model": specialist.base_model})

    async for chunk in sync_gen_to_async(
        client.chat_stream,
        model=specialist.base_model or settings.CORE_MODEL,
        messages=messages
    ):
        if chunk["type"] == "token":
            full_response += chunk["content"]
            yield _sse(chunk)
        elif chunk["type"] in ("done", "error"):
            yield _sse(chunk)

    specialist.total_requests = (specialist.total_requests or 0) + 1
    db.commit()

    yield _sse({"type": "done", "full_response": full_response})
    yield "data: [DONE]\n\n"
