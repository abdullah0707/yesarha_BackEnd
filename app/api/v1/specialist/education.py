"""
Education Specialist API
يستقبل سؤال المتعلم + content_id، يقرأ المحتوى المُزامَن محلياً
(من باك إند المستخدمين عبر webhook المزامنة)، يبحث محلياً عن السياق
ذي الصلة، ويشرح للمتعلم — استريم كامل في نفس الـ request.

هذا الـ endpoint عام (Public) ويُستدعى عبر X-API-Key الخاص بنموذج تعليمي
محدد، تماماً كأي نموذج متخصص آخر — وليس endpoint إداري.
"""
import json
import time
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.core.config import settings
from app.core.responses import success, AppError, ErrorCodes
from app.models.specialist import SpecialistModel
from app.models.education import SyncedContent, StudentQuestion
from app.services.education.retriever import retrieve_relevant_chunks, build_context_from_chunks
from app.services.ollama_client import OllamaClient
from app.core.intelligence.async_bridge import sync_gen_to_async
from app.core.intelligence.api_keys import get_specialist_by_api_key

router = APIRouter(prefix="/specialist/education", tags=["Public - Education"])


DEFAULT_EDUCATION_PROMPT = """أنت مساعد تعليمي ذكي من يسرها. مهمتك شرح محتوى الدرس للمتعلم والإجابة على أسئلته.

قواعد صارمة:
- أجب فقط بناءً على محتوى الدرس المرفق أدناه. لا تستخدم معلومات من خارجه.
- إذا كان السؤال خارج نطاق المحتوى المرفق، أخبر المتعلم بوضوح أن هذا غير مذكور في الدرس الحالي.
- اشرح بأسلوب بسيط وواضح يناسب متعلماً لأول مرة.
- استخدم أمثلة من المحتوى نفسه عند الشرح."""


class AskRequest(BaseModel):
    content_id: str
    question: str
    stream: bool = True


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask")
def ask_question(
    payload: AskRequest,
    specialist: SpecialistModel = Depends(get_specialist_by_api_key),
    db: Session = Depends(get_db),
):
    """
    نقطة الدخول العامة لنموذج تعليمي — يُحدَّد النموذج عبر X-API-Key.
    يقرأ المحتوى من قاعدة بيانات يسرها كور المحلية (مُزامَنة مسبقاً عبر webhook).
    """
    content = db.query(SyncedContent).filter(
        SyncedContent.external_content_id == payload.content_id
    ).first()

    if not content:
        raise AppError(ErrorCodes.NOT_FOUND,
                       f"المحتوى ذو المعرّف '{payload.content_id}' غير موجود — تأكد من إرساله عبر webhook المزامنة أولاً", 404)

    chunks = content.chunks_json or []
    relevant = retrieve_relevant_chunks(chunks, payload.question, top_k=3)
    context = build_context_from_chunks(relevant)

    system_prompt = specialist.system_prompt or DEFAULT_EDUCATION_PROMPT
    full_system = f"{system_prompt}\n\n## محتوى الدرس ذو الصلة:\n{context}"

    messages = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": payload.question},
    ]

    if payload.stream:
        return StreamingResponse(
            _stream_answer(messages, payload, specialist, relevant, db),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    client = OllamaClient(base_url=settings.OLLAMA_BASE_URL)
    start = time.perf_counter()
    result = client.chat(model=specialist.base_model or settings.CORE_MODEL, messages=messages)
    response_ms = int((time.perf_counter() - start) * 1000)

    _log_question(db, payload, specialist, result["content"], relevant, response_ms)

    return success({
        "answer": result["content"],
        "used_sections": [c["section"] for c in relevant],
        "response_ms": response_ms,
    })


async def _stream_answer(messages, payload: AskRequest, specialist: SpecialistModel,
                          relevant: list[dict], db: Session):
    client = OllamaClient(base_url=settings.OLLAMA_BASE_URL)
    full_response = ""
    start = time.perf_counter()

    yield _sse({"type": "context", "used_sections": [c["section"] for c in relevant]})

    async for chunk in sync_gen_to_async(
        client.chat_stream, model=specialist.base_model or settings.CORE_MODEL, messages=messages
    ):
        if chunk["type"] == "token":
            full_response += chunk["content"]
            yield _sse(chunk)
        elif chunk["type"] in ("done", "error"):
            yield _sse(chunk)

    response_ms = int((time.perf_counter() - start) * 1000)
    _log_question(db, payload, specialist, full_response, relevant, response_ms)

    yield _sse({"type": "done", "full_response": full_response})
    yield "data: [DONE]\n\n"


def _log_question(db: Session, payload: AskRequest, specialist: SpecialistModel,
                   answer: str, relevant: list[dict], response_ms: int):
    try:
        specialist.total_requests = (specialist.total_requests or 0) + 1
        db.add(StudentQuestion(
            external_content_id=payload.content_id,
            specialist_id=specialist.id,
            question=payload.question,
            answer=answer[:1000],
            used_sections=[c["section"] for c in relevant],
            response_ms=response_ms,
        ))
        db.commit()
    except Exception:
        db.rollback()
