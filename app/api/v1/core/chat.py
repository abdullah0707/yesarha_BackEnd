"""
Core Chat API — مع Streaming كامل وTool Calling
"""
import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, AppError, ErrorCodes
from app.core.config import settings
from app.models.user import Admin
from app.models.operations import Execution, Goal
from app.services.ollama_client import OllamaClient
from app.core.intelligence.tool_engine import (
    build_tool_call_prompt, parse_tool_calls_from_response, CORE_TOOLS
)
from app.core.intelligence.tool_executor import ToolExecutor

router = APIRouter(prefix="/core", tags=["Core Intelligence"])


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


# ── Core Chat (مع Tool Calling) ───────────────────────────────────

@router.post("/chat")
async def core_chat(
    payload: CoreChatRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    """
    محادثة مع Yesarha Core مع Tool Calling
    يدعم streaming عبر SSE
    """
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

    # Non-streaming
    result = await _run_core_chat(payload, db, admin)
    return success(result)


async def _stream_core_response(payload: CoreChatRequest, db: Session, admin: Admin):
    """
    Generator لـ SSE streaming
    """
    client = _get_client()
    executor = ToolExecutor(db=db)

    messages = build_tool_call_prompt(payload.message)
    if payload.history:
        # إدراج التاريخ قبل آخر رسالة
        messages = messages[:-1]  # remove last user msg
        messages.extend(payload.history)
        messages.append({"role": "user", "content": payload.message})

    tool_results = []
    full_response = ""
    max_iterations = 3  # حد أقصى لدورات Tool Calling

    for iteration in range(max_iterations):
        # أرسل ping للـ keep-alive
        yield f"data: {json.dumps({'type': 'thinking', 'iteration': iteration + 1})}\n\n"

        # استدعاء Core
        chunk_buffer = ""
        for chunk in client.chat_stream(
            model=settings.CORE_MODEL,
            messages=messages,
        ):
            if chunk["type"] == "token":
                token = chunk["content"]
                full_response += token
                chunk_buffer += token

                # أرسل token فوراً للـ client
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

            elif chunk["type"] == "done":
                yield f"data: {json.dumps({'type': 'stats', **{k: v for k, v in chunk.items() if k != 'type'}})}\n\n"

            elif chunk["type"] == "error":
                yield f"data: {json.dumps(chunk)}\n\n"
                return

        # فحص Tool Calls في الرد
        if payload.enable_tools:
            calls = parse_tool_calls_from_response(full_response)

            if calls:
                yield f"data: {json.dumps({'type': 'tool_start', 'tools': [c.get('name') for c in calls]})}\n\n"

                for call in calls:
                    tool_name = call.get("name", "")
                    params = call.get("parameters", call.get("arguments", {}))

                    yield f"data: {json.dumps({'type': 'tool_executing', 'tool': tool_name})}\n\n"

                    result = executor.execute(tool_name, params)
                    tool_results.append({"tool": tool_name, "result": result})

                    yield f"data: {json.dumps({'type': 'tool_done', 'tool': tool_name, 'result': result})}\n\n"

                # أضف النتائج للـ context وأعد المحاولة
                messages = build_tool_call_prompt(payload.message, tool_results)
                full_response = ""
                continue

        # لا يوجد tool calls — انتهى
        break

    # حفظ في قاعدة البيانات
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

    yield f"data: {json.dumps({'type': 'done', 'full_response': full_response})}\n\n"
    yield "data: [DONE]\n\n"


async def _run_core_chat(payload: CoreChatRequest, db: Session, admin: Admin) -> dict:
    """Non-streaming version"""
    client = _get_client()
    executor = ToolExecutor(db=db)

    messages = build_tool_call_prompt(payload.message)
    tool_results = []
    full_response = ""

    for _ in range(3):
        result = client.chat(model=settings.CORE_MODEL, messages=messages)
        full_response = result["content"]

        if payload.enable_tools:
            calls = parse_tool_calls_from_response(full_response)
            if calls:
                for call in calls:
                    tr = executor.execute(call.get("name"), call.get("parameters", {}))
                    tool_results.append({"tool": call.get("name"), "result": tr})
                messages = build_tool_call_prompt(payload.message, tool_results)
                continue
        break

    return {
        "content": full_response,
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
    """
    محادثة مع نموذج متخصص محدد مع streaming
    """
    from app.models.specialist import SpecialistModel
    from app.services.models.model_manager import model_manager

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

    # Non-streaming
    client = _get_client()
    messages = [{"role": "system", "content": specialist.system_prompt or ""}]
    if payload.history:
        messages.extend(payload.history)
    messages.append({"role": "user", "content": payload.message})

    result = client.chat(
        model=specialist.base_model or settings.CORE_MODEL,
        messages=messages
    )

    # تحديث إحصائيات النموذج
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
    yield f"data: {json.dumps({'type': 'specialist_info', 'name': specialist.display_name, 'model': specialist.base_model})}\n\n"

    for chunk in client.chat_stream(
        model=specialist.base_model or settings.CORE_MODEL,
        messages=messages
    ):
        if chunk["type"] == "token":
            full_response += chunk["content"]
            yield f"data: {json.dumps(chunk)}\n\n"
        elif chunk["type"] in ("done", "error"):
            yield f"data: {json.dumps(chunk)}\n\n"

    specialist.total_requests = (specialist.total_requests or 0) + 1
    db.commit()

    yield f"data: {json.dumps({'type': 'done', 'full_response': full_response})}\n\n"
    yield "data: [DONE]\n\n"
