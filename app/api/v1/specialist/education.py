"""
Education Specialist API
يستقبل سؤال المتعلم + content_id، يقرأ المحتوى المُزامَن محلياً
(من باك إند المستخدمين عبر webhook المزامنة)، يبحث محلياً عن السياق
ذي الصلة، ويشرح للمتعلم — استريم كامل في نفس الـ request.

هذا الـ endpoint عام (Public) ويُستدعى عبر X-API-Key الخاص بنموذج تعليمي
محدد، تماماً كأي نموذج متخصص آخر — وليس endpoint إداري.
"""
import base64
import json
import time
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.core.responses import success, AppError, ErrorCodes
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.models.specialist import SpecialistModel
from app.models.education import SyncedContent, StudentQuestion
from app.services.education.retriever import retrieve_relevant_chunks, build_context_from_chunks
from app.services.ollama_client import OllamaClient
from app.services.runtime_config import runtime_cfg
from app.core.intelligence.async_bridge import sync_gen_to_async
from app.core.intelligence.api_keys import get_specialist_by_api_key
from app.core.prompts import (
    build_system_prompt, detect_language,
    is_identity_question, passes_quality_gate, is_visual_request,
    is_intro_request, is_objectives_request,
)

_SPEED_OPTIONS = {"temperature": 0.1, "num_predict": 1024}
_RETRY_OPTIONS = {"temperature": 0.5, "num_predict": 1024}

router = APIRouter(prefix="/specialist/education", tags=["Public - Education"])


DEFAULT_EDUCATION_PROMPT = "أنت مساعد تعليمي. اشرح للمتعلم من نصوص الدرس المرفقة فقط. لا تُجب من معرفتك العامة."




class AskRequest(BaseModel):
    content_id: str
    question: str
    stream: bool = True


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _canned_stream(message: str):
    """يُرسل رداً جاهزاً كـ SSE stream (للأسئلة خارج النطاق)"""
    yield _sse({"type": "context", "used_sections": []})
    yield _sse({"type": "token", "content": message})
    yield _sse({"type": "done", "full_response": message})
    yield "data: [DONE]\n\n"


@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
def ask_question(
    request: Request,
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

    lang = detect_language(payload.question)
    cfg = specialist.config_json or {}
    intro = cfg.get("intro_text") or (
        f"أنا {specialist.display_name}، مساعد تعليمي من يسرها. "
        "هنا لمساعدتك في فهم محتوى دروسك والإجابة على أسئلتك."
    )

    # ── سؤال الهوية → ردّ مباشر بدون نموذج ──
    if is_identity_question(payload.question):
        _log_question(db, payload, specialist, intro, [], 0)
        if payload.stream:
            return StreamingResponse(
                _canned_stream(intro),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )
        return success({"answer": intro, "used_sections": [], "response_ms": 0})

    chunks = content.chunks_json or []
    all_context = build_context_from_chunks(chunks)   # كل المحتوى للمقدمة/الأهداف

    # ── مقدمة الدرس → النموذج يقرأ كل المحتوى ويكتب مقدمة ──
    if is_intro_request(payload.question) and chunks:
        messages, used = _build_special_messages(
            specialist, all_context, lang,
            "اكتب مقدمة موجزة وبسيطة تُعرِّف المتعلم بموضوع هذا الدرس ومحاوره الرئيسية. "
            "اجعلها واضحة وسهلة الفهم في فقرة أو فقرتين.",
        )
        return _run_and_respond(messages, chunks, payload, specialist, db, lang, stream=payload.stream)

    # ── أهداف الدرس / نواتج التعلم → النموذج يقرأ كل المحتوى ويستخرج الأهداف ──
    if is_objectives_request(payload.question) and chunks:
        messages, used = _build_special_messages(
            specialist, all_context, lang,
            "بناءً على محتوى الدرس، اكتب قائمة نقطية واضحة بأهداف التعلم: "
            "ماذا سيعرف المتعلم وماذا سيكون قادراً على فعله بعد إتمام هذا الدرس؟",
        )
        return _run_and_respond(messages, chunks, payload, specialist, db, lang, stream=payload.stream)

    # ── استرجاع السياق ذي الصلة ──
    relevant = retrieve_relevant_chunks(chunks, payload.question, top_k=3)
    context = build_context_from_chunks(relevant)

    # ── سؤال خارج النطاق → النموذج يُعرِّف بالدورة ويشجّع الطالب ──
    if len(relevant) == 0 and len(chunks) > 0:
        course_title = content.title or "هذه الدورة"
        messages, _ = _build_special_messages(
            specialist, all_context, lang,
            f"سأل الطالب: «{payload.question}»\n"
            f"هذا السؤال خارج محتوى الدرس «{course_title}».\n"
            "أجب بأسلوب ودود: أخبره أن سؤاله مهم لكن الأفضل البقاء في محتوى الدورة، "
            "واذكر موضوع الدورة وما سيكتسبه المتعلم منها باختصار. لا تُجب على السؤال نفسه.",
        )
        return _run_and_respond(messages, [], payload, specialist, db, lang, stream=payload.stream)

    system_prompt = build_system_prompt(
        specialist.system_prompt or DEFAULT_EDUCATION_PROMPT,
        intro_text=intro,
        detected_lang=lang,
    )

    messages = [{"role": "system", "content": system_prompt}]
    if context.strip():
        messages.append({"role": "user", "content": f"نصوص الدرس:\n\n{context}"})
        messages.append({"role": "assistant", "content": "حسناً، اطلعت على نصوص الدرس. سأجيب منها فقط."})
    messages.append({"role": "user", "content": payload.question})

    if payload.stream:
        return StreamingResponse(
            _stream_answer(messages, payload, specialist, relevant, db),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    model = specialist.base_model or runtime_cfg.get_core_model()
    client = OllamaClient()
    start = time.perf_counter()
    result = client.chat(model=model, messages=messages, options=_SPEED_OPTIONS, think=False)
    answer = result["content"]

    if not passes_quality_gate(answer, lang):
        result = client.chat(model=model, messages=messages, options=_RETRY_OPTIONS, think=False)
        answer = result["content"]

    response_ms = int((time.perf_counter() - start) * 1000)
    _log_question(db, payload, specialist, answer, relevant, response_ms)

    return success({
        "answer": answer,
        "used_sections": [c["section"] for c in relevant],
        "response_ms": response_ms,
    })


async def _stream_answer(messages, payload: AskRequest, specialist: SpecialistModel,
                          relevant: list[dict], db: Session):
    client = OllamaClient()
    full_response = ""
    start = time.perf_counter()

    yield _sse({"type": "context", "used_sections": [c["section"] for c in relevant]})

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
        elif chunk["type"] in ("done", "error"):
            yield _sse(chunk)

    response_ms = int((time.perf_counter() - start) * 1000)
    _log_question(db, payload, specialist, full_response, relevant, response_ms)

    yield _sse({"type": "done", "full_response": full_response})
    yield "data: [DONE]\n\n"


def _build_special_messages(
    specialist: SpecialistModel, all_context: str, lang: str, task_instruction: str
) -> tuple[list[dict], list[dict]]:
    """يبني رسائل نموذج للمهام الخاصة (مقدمة / أهداف / خارج النطاق) باستخدام كل المحتوى."""
    cfg = specialist.config_json or {}
    intro = cfg.get("intro_text") or f"أنا {specialist.display_name}، مساعد تعليمي."
    system = build_system_prompt(
        specialist.system_prompt or DEFAULT_EDUCATION_PROMPT,
        intro_text=intro,
        detected_lang=lang,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"محتوى الدرس كاملاً:\n\n{all_context}"},
        {"role": "assistant", "content": "اطلعت على محتوى الدرس. جاهز."},
        {"role": "user", "content": task_instruction},
    ]
    return messages, []


def _run_and_respond(messages, used_chunks, payload, specialist, db, lang, stream=False):
    """يُشغّل النموذج ويُرجع الرد المناسب (stream أو JSON)."""
    model = specialist.base_model or runtime_cfg.get_core_model()
    client = OllamaClient()
    start = time.perf_counter()

    if stream:
        return StreamingResponse(
            _stream_answer(messages, payload, specialist, used_chunks, db),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = client.chat(model=model, messages=messages, options=_SPEED_OPTIONS, think=False)
    answer = result["content"]
    if not passes_quality_gate(answer, lang):
        result = client.chat(model=model, messages=messages, options=_RETRY_OPTIONS, think=False)
        answer = result["content"]

    response_ms = int((time.perf_counter() - start) * 1000)
    _log_question_raw(db, payload.content_id, payload.question, specialist, answer, used_chunks, response_ms)
    return success({
        "answer": answer,
        "used_sections": [c["section"] for c in used_chunks],
        "response_ms": response_ms,
    })


def _log_question_raw(db, content_id, question, specialist, answer, relevant, response_ms):
    try:
        specialist.total_requests = (specialist.total_requests or 0) + 1
        db.add(StudentQuestion(
            external_content_id=content_id,
            specialist_id=specialist.id,
            question=question,
            answer=answer[:1000],
            used_sections=[c["section"] for c in relevant],
            response_ms=response_ms,
        ))
        db.commit()
    except Exception:
        db.rollback()


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


# ── Voice Sample Upload ────────────────────────────────────────────────────────

@router.post("/voice-sample")
@limiter.limit("20/minute")
async def upload_voice_sample(
    request: Request,
    sample: UploadFile = File(..., description="عينة صوت المحاضر (WAV/MP3، 10-60 ثانية)"),
    instructor_name: str = Form(..., description="اسم المحاضر"),
    specialist: SpecialistModel = Depends(get_specialist_by_api_key),
    db: Session = Depends(get_db),
):
    """
    رفع عينة صوت المحاضر. يُرجع voice_id يُحفظ في النظام 1 ويُرسَل مع كل سؤال.
    حجم العينة المُوصى به: 15-60 ثانية من صوت المحاضر الواضح.
    """
    from app.services.voice.voice_service import save_pipeline_voice_sample, enhance_voice_sample

    sample_bytes = await sample.read()
    if len(sample_bytes) < 8000:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "العينة قصيرة جداً — يجب 6+ ثوانٍ على الأقل", 400)
    if len(sample_bytes) > 50 * 1024 * 1024:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "حجم الملف كبير جداً (حد أقصى 50MB)", 400)

    voice_id = str(uuid.uuid4())
    try:
        enhanced = enhance_voice_sample(sample_bytes)
        save_pipeline_voice_sample(voice_id, enhanced)
    except Exception as e:
        raise AppError(ErrorCodes.INTERNAL_ERROR, f"خطأ في حفظ عينة الصوت: {str(e)[:200]}", 500)

    duration_estimate = round(len(sample_bytes) / (16000 * 2), 1)
    return success({
        "voice_id": voice_id,
        "instructor_name": instructor_name,
        "duration_estimate": duration_estimate,
        "message": "عينة الصوت محفوظة. احتفظ بـ voice_id وأرسله مع كل سؤال في pipeline.",
    })


# ── Pipeline Request Schema ────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    content_id: str
    question: str
    voice_id: Optional[str] = None
    stream: bool = False


# ── Pipeline Endpoint ──────────────────────────────────────────────────────────

@router.post("/pipeline")
@limiter.limit(DEFAULT_RATE_LIMIT)
def pipeline_ask(
    request: Request,
    payload: PipelineRequest,
    specialist: SpecialistModel = Depends(get_specialist_by_api_key),
    db: Session = Depends(get_db),
):
    """
    الـ Pipeline التعليمي الكامل:
      1. نموذج التعليم (RAG) → نص
      2. استنساخ صوت المحاضر (Habibi-TTS) → audio_base64
      3. كشف طلب بصري → visual_requested

    يُرجع: {text, audio_base64?, visual_requested, used_sections, response_ms}
    """
    content = db.query(SyncedContent).filter(
        SyncedContent.external_content_id == payload.content_id
    ).first()
    if not content:
        raise AppError(ErrorCodes.NOT_FOUND,
                       f"المحتوى '{payload.content_id}' غير موجود — أرسله عبر webhook المزامنة أولاً", 404)

    lang = detect_language(payload.question)
    cfg = specialist.config_json or {}
    intro = cfg.get("intro_text") or (
        f"أنا {specialist.display_name}، مساعد تعليمي من يسرها. "
        "هنا لمساعدتك في فهم محتوى دروسك والإجابة على أسئلتك."
    )

    # ── المرحلة 1: الهوية (بدون نموذج) ──
    if is_identity_question(payload.question):
        _log_pipeline(db, payload, specialist, intro, [], 0)
        return success({
            "text": intro,
            "audio_base64": None,
            "visual_requested": False,
            "used_sections": [],
            "response_ms": 0,
        })

    chunks = content.chunks_json or []
    relevant = retrieve_relevant_chunks(chunks, payload.question, top_k=3)
    context = build_context_from_chunks(relevant)

    # ── المرحلة 2: خارج النطاق (بدون نموذج) ──
    if len(relevant) == 0 and len(chunks) > 0:
        out_msg = (
            "هذا السؤال خارج نطاق درسنا الحالي. يمكنني مساعدتك فقط في مواضيع هذا الدرس."
            if lang == "ar"
            else "This question is outside our current lesson scope."
        )
        _log_pipeline(db, payload, specialist, out_msg, [], 0)
        return success({
            "text": out_msg,
            "audio_base64": None,
            "visual_requested": False,
            "used_sections": [],
            "response_ms": 0,
        })

    # ── المرحلة 3: التعليم (نموذج RAG) ──
    system_prompt = build_system_prompt(
        specialist.system_prompt or DEFAULT_EDUCATION_PROMPT,
        intro_text=intro,
        detected_lang=lang,
    )
    messages = [{"role": "system", "content": system_prompt}]
    if context.strip():
        messages.append({"role": "user", "content": f"نصوص الدرس:\n\n{context}"})
        messages.append({"role": "assistant", "content": "حسناً، اطلعت على نصوص الدرس. سأجيب منها فقط."})
    messages.append({"role": "user", "content": payload.question})

    model = specialist.base_model or runtime_cfg.get_core_model()
    client = OllamaClient()
    start = time.perf_counter()
    result = client.chat(model=model, messages=messages, options=_SPEED_OPTIONS, think=False)
    answer = result["content"]

    if not passes_quality_gate(answer, lang):
        result = client.chat(model=model, messages=messages, options=_SPEED_OPTIONS, think=False)
        answer = result["content"]

    text_ms = int((time.perf_counter() - start) * 1000)

    # ── المرحلة 4: كشف طلب بصري ──
    visual_requested = is_visual_request(payload.question)

    # ── المرحلة 5: استنساخ صوت المحاضر ──
    audio_b64 = None
    if payload.voice_id:
        audio_b64 = _synthesize_voice(answer, payload.voice_id)

    total_ms = int((time.perf_counter() - start) * 1000)
    _log_pipeline(db, payload, specialist, answer, relevant, total_ms)

    return success({
        "text": answer,
        "audio_base64": audio_b64,
        "visual_requested": visual_requested,
        "used_sections": [c["section"] for c in relevant],
        "response_ms": total_ms,
        "text_ms": text_ms,
    })


def _synthesize_voice(text: str, voice_id: str) -> Optional[str]:
    """يستنسخ صوت المحاضر ويُرجع base64، أو None عند الفشل."""
    try:
        from app.services.voice.voice_service import (
            synthesize_with_habibi, get_pipeline_voice_sample,
        )
        sample_bytes = get_pipeline_voice_sample(voice_id)
        if not sample_bytes:
            return None
        audio_bytes = synthesize_with_habibi(text=text, ref_audio_bytes=sample_bytes, dialect="ar-SA")
        return base64.b64encode(audio_bytes).decode("utf-8")
    except Exception:
        return None


def _log_pipeline(db: Session, payload: PipelineRequest, specialist: SpecialistModel,
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
