"""
Public Specialist API — للمستخدمين النهائيين عبر API Key
لا يحتاج توكن أدمن إطلاقاً — فقط X-API-Key الخاص بالنموذج المتخصص.

هذا المسار للنماذج العامة التي لا تعتمد على مصدر محتوى خارجي
(مثل business أو media). النماذج المرتبطة بمحتوى مُزامَن (مثل education)
لها مسارها الخاص في app/api/v1/specialist/education.py
"""
import json
import time
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db.session import get_db
from app.core.config import settings
from app.core.responses import success
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.models.specialist import SpecialistModel, ModelPerformanceLog
from app.services.ollama_client import OllamaClient
from app.core.intelligence.async_bridge import sync_gen_to_async
from app.core.intelligence.api_keys import get_specialist_by_api_key

router = APIRouter(prefix="/specialist", tags=["Public - Specialist API"])


class PublicAskRequest(BaseModel):
    message: str
    history: Optional[list[dict]] = None
    stream: bool = True


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def ask_specialist(
    request: Request,
    payload: PublicAskRequest,
    specialist: SpecialistModel = Depends(get_specialist_by_api_key),
    db: Session = Depends(get_db),
):
    """
    نقطة الدخول العامة لأي نموذج متخصص — يُحدَّد النموذج تلقائياً عبر X-API-Key.
    هذا ما يستخدمه باك إند المستخدمين لإرسال رسالة المستخدم وأخذ الرد.
    """
    if payload.stream:
        return StreamingResponse(
            _stream_response(payload, specialist, db),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    client = OllamaClient(base_url=settings.OLLAMA_BASE_URL)
    messages = [{"role": "system", "content": specialist.system_prompt or ""}]
    if payload.history:
        messages.extend(payload.history)
    messages.append({"role": "user", "content": payload.message})

    start = time.perf_counter()
    result = client.chat(model=specialist.base_model or settings.CORE_MODEL, messages=messages)
    response_ms = int((time.perf_counter() - start) * 1000)

    _log_and_count(db, specialist, payload.message, result["content"], response_ms)

    return success({
        "answer": result["content"],
        "specialist": specialist.display_name,
        "response_ms": response_ms,
    })


async def _stream_response(payload: PublicAskRequest, specialist: SpecialistModel, db: Session):
    client = OllamaClient(base_url=settings.OLLAMA_BASE_URL)
    messages = [{"role": "system", "content": specialist.system_prompt or ""}]
    if payload.history:
        messages.extend(payload.history)
    messages.append({"role": "user", "content": payload.message})

    full_response = ""
    start = time.perf_counter()

    async for chunk in sync_gen_to_async(
        client.chat_stream,
        model=specialist.base_model or settings.CORE_MODEL,
        messages=messages,
    ):
        if chunk["type"] == "token":
            full_response += chunk["content"]
            yield _sse(chunk)
        elif chunk["type"] in ("done", "error"):
            yield _sse(chunk)

    response_ms = int((time.perf_counter() - start) * 1000)
    _log_and_count(db, specialist, payload.message, full_response, response_ms)

    yield _sse({"type": "done", "full_response": full_response})
    yield "data: [DONE]\n\n"


def _log_and_count(db: Session, specialist: SpecialistModel, user_input: str,
                   output: str, response_ms: int):
    """يسجّل الطلب لإحصائيات الأداء التي يستخدمها Core للمراقبة"""
    try:
        specialist.total_requests = (specialist.total_requests or 0) + 1
        db.add(ModelPerformanceLog(
            model_id=specialist.id,
            model_name=specialist.name,
            user_input=user_input[:500],
            model_output=output[:500],
            response_ms=response_ms,
            status="success",
        ))
        db.commit()
    except Exception:
        db.rollback()
