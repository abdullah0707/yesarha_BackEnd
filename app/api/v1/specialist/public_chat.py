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
from app.core.responses import success
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.models.specialist import SpecialistModel, ModelPerformanceLog, GatewayRequestLog
from app.services.ollama_client import OllamaClient
from app.services.runtime_config import runtime_cfg
from app.core.intelligence.async_bridge import sync_gen_to_async
from app.core.intelligence.api_keys import get_specialist_by_api_key
from app.core.prompts import build_system_prompt

_SPEED_OPTIONS = {"temperature": 0.1, "num_predict": 1024}

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
    api_key = request.headers.get("X-API-Key", "")
    client_ip = request.client.host if request.client else None

    if payload.stream:
        return StreamingResponse(
            _stream_response(payload, specialist, db, api_key=api_key, client_ip=client_ip),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    client = OllamaClient()
    messages = [{"role": "system", "content": build_system_prompt(specialist.system_prompt or "")}]
    if payload.history:
        messages.extend(payload.history[-6:])
    messages.append({"role": "user", "content": payload.message})

    start = time.perf_counter()
    try:
        result = client.chat(
            model=specialist.base_model or runtime_cfg.get_core_model(),
            messages=messages,
            options=_SPEED_OPTIONS,
            think=False,
        )
        response_ms = int((time.perf_counter() - start) * 1000)
        _log_and_count(
            db, specialist, payload.message, result["content"], response_ms,
            tokens_in=result.get("tokens_input", 0),
            tokens_out=result.get("tokens_output", 0),
            api_key=api_key, ip_address=client_ip,
        )
        return success({
            "answer": result["content"],
            "specialist": specialist.display_name,
            "response_ms": response_ms,
        })
    except Exception as e:
        response_ms = int((time.perf_counter() - start) * 1000)
        _log_and_count(
            db, specialist, payload.message, f"ERROR: {str(e)[:200]}", response_ms,
            status="failed", api_key=api_key, ip_address=client_ip,
        )
        raise


async def _stream_response(
    payload: PublicAskRequest,
    specialist: SpecialistModel,
    db: Session,
    *,
    api_key: str = "",
    client_ip: str | None = None,
):
    client = OllamaClient()
    messages = [{"role": "system", "content": build_system_prompt(specialist.system_prompt or "")}]
    if payload.history:
        messages.extend(payload.history[-6:])
    messages.append({"role": "user", "content": payload.message})

    full_response = ""
    tokens_in = tokens_out = 0
    start = time.perf_counter()
    stream_failed = False

    try:
        async for chunk in sync_gen_to_async(
            client.chat_stream,
            model=specialist.base_model or runtime_cfg.get_core_model(),
            messages=messages,
            options=_SPEED_OPTIONS,
            think=False,
        ):
            if chunk["type"] == "token":
                full_response += chunk["content"]
                yield _sse(chunk)
            elif chunk["type"] == "done":
                tokens_in = chunk.get("tokens_input", 0)
                tokens_out = chunk.get("tokens_output", 0)
                yield _sse(chunk)
            elif chunk["type"] == "error":
                stream_failed = True
                yield _sse(chunk)
    except Exception as e:
        stream_failed = True
        full_response = f"ERROR: {str(e)[:200]}"

    response_ms = int((time.perf_counter() - start) * 1000)
    _log_and_count(
        db, specialist, payload.message, full_response, response_ms,
        tokens_in=tokens_in, tokens_out=tokens_out,
        status="failed" if stream_failed else "success",
        api_key=api_key, ip_address=client_ip,
    )

    if not stream_failed:
        yield _sse({"type": "done", "full_response": full_response})
    yield "data: [DONE]\n\n"


def _log_and_count(
    db: Session,
    specialist: SpecialistModel,
    user_input: str,
    output: str,
    response_ms: int,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    status: str = "success",
    api_key: str = "",
    ip_address: str | None = None,
):
    """يسجّل الطلب لإحصائيات الأداء + Gateway log، ويُحدِّث avg_response_ms"""
    try:
        n = (specialist.total_requests or 0) + 1
        specialist.total_requests = n
        specialist.avg_response_ms = int(
            ((specialist.avg_response_ms or 0) * (n - 1) + response_ms) / n
        )
        db.add(ModelPerformanceLog(
            model_id=specialist.id,
            model_name=specialist.name,
            user_input=user_input[:500],
            model_output=output[:500],
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            response_ms=response_ms,
            status=status,
        ))
        if api_key:
            db.add(GatewayRequestLog(
                key_prefix=api_key[:24],
                key_type="specialist",
                specialist_id=specialist.id,
                endpoint="/specialist/ask",
                specialists_used=[specialist.specialization],
                response_ms=response_ms,
                status=status,
                ip_address=ip_address,
            ))
        db.commit()
    except Exception:
        db.rollback()
