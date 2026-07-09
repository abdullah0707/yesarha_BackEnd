"""
Pipeline Specialist — /specialist/pipeline/ask

الـ Pipeline التعليمي المتكامل:
  1. Education RAG  → نص
  2. smart_tashkeel + Edge-TTS → صوت WAV (URL)
  3. Mind Map SVG عند طلب بصري (URL)
  4. حفظ الملفات مع TTL 24 ساعة

الفرق عن /specialist/education/pipeline:
- يُرجع URLs بدلاً من base64
- يستخدم Edge-TTS مباشرةً (لا استنساخ)
- يدعم الخرائط الذهنية عند الطلب
"""
import logging
import re
import struct
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.intelligence.api_keys import get_pipeline_auth
from app.core.prompts import (
    build_system_prompt,
    build_sd_prompt,
    get_sd_system_prompt,
    detect_language,
    is_identity_question,
    is_image_gen_request,
    is_visual_request,
    passes_quality_gate,
)
from app.core.rate_limit import DEFAULT_RATE_LIMIT, limiter
from app.core.responses import AppError, ErrorCodes, success
from app.db.session import get_db
from app.models.education import SyncedContent, StudentQuestion
from app.models.specialist import GatewayRequestLog, SpecialistBundle, SpecialistModel
from app.services.education.retriever import build_context_from_chunks, retrieve_relevant_chunks
from app.services.ollama_client import OllamaClient
from app.services.runtime_config import runtime_cfg

logger = logging.getLogger("yesarha.pipeline")

_AUDIO_CACHE_DIR = Path("/app/data/audio_cache")
_AUDIO_TTL = 24 * 3600
_VISUAL_TTL = 24 * 3600
_SPEED_OPTIONS  = {"temperature": 0.1, "num_predict": 1024}
_RETRY_OPTIONS  = {"temperature": 0.5, "num_predict": 1024}
_RETRY_OPTIONS2 = {"temperature": 0.7, "num_predict": 1024}

# تنظيف الكاش مرة كل 10 دقائق كحد أقصى
_last_audio_cleanup: float = 0.0
_CLEANUP_INTERVAL = 600
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# pool مشترك — يُعاد استخدامه عبر كل الطلبات
_executor = ThreadPoolExecutor(max_workers=4)

_DEFAULT_PROMPT = (
    "أنت مساعد تعليمي. اشرح للمتعلم من نصوص الدرس المرفقة فقط. "
    "لا تُجب من معرفتك العامة."
)

router = APIRouter(prefix="/specialist/pipeline", tags=["Public - Pipeline"])


# ── Audio cache ───────────────────────────────────────────────────────────────


def _audio_dir() -> Path:
    _AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIO_CACHE_DIR


def _cleanup_audio() -> None:
    global _last_audio_cleanup
    now = time.time()
    if now - _last_audio_cleanup < _CLEANUP_INTERVAL:
        return
    _last_audio_cleanup = now
    cutoff = now - _AUDIO_TTL
    try:
        for f in _audio_dir().glob("*.wav"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def _save_audio(audio_bytes: bytes) -> str:
    _cleanup_audio()
    audio_id = str(uuid.uuid4())
    (_audio_dir() / f"{audio_id}.wav").write_bytes(audio_bytes)
    return audio_id


def _wav_duration(wav_bytes: bytes) -> Optional[float]:
    try:
        if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF":
            return None
        byte_rate = struct.unpack_from("<I", wav_bytes, 28)[0]
        i = 12
        while i + 8 <= len(wav_bytes):
            chunk_id = wav_bytes[i : i + 4]
            chunk_size = struct.unpack_from("<I", wav_bytes, i + 4)[0]
            if chunk_id == b"data":
                return round(chunk_size / byte_rate, 2) if byte_rate else None
            i += 8 + chunk_size
    except Exception:
        pass
    return None


# ── TTS ───────────────────────────────────────────────────────────────────────


def _tts(
    text: str,
    dialect: str,
    gender: str,
    voice_id: Optional[str],
    speed: float,
) -> tuple[Optional[str], Optional[float]]:
    try:
        from app.services.voice.voice_service import synthesize_human_voice

        audio_bytes = synthesize_human_voice(
            text=text, language="ar",
            dialect=dialect, gender=gender,
            voice_id=voice_id, speed=speed,
        )
        audio_id = _save_audio(audio_bytes)
        return f"/api/v1/specialist/pipeline/audio/{audio_id}", _wav_duration(audio_bytes)
    except Exception as e:
        logger.warning(f"Pipeline TTS failed: {e}")
        return None, None


# ── Mind Map ──────────────────────────────────────────────────────────────────


def _mindmap(chunks: list[dict], title: str) -> Optional[str]:
    from app.services.mind_map_service import generate_mindmap
    url, _ = generate_mindmap(chunks, title)
    return url


# ── Image Generation ───────────────────────────────────────────────────────────


def _generate_image(arabic_request: str, context: str, color_palette: str = "") -> Optional[str]:
    """يختار تلقائياً: إنفوجراف SVG للطلبات متعددة العناصر، SD للمشاهد الفنية."""
    try:
        from app.core.prompts import is_infographic_request

        if is_infographic_request(arabic_request):
            from app.services.infographic_service import generate_infographic
            result = generate_infographic(arabic_request, context, color_palette)
            return result[0] if result else None

        from app.core.config import settings
        if not settings.SD_ENABLED:
            return None
        from app.services.ollama_client import OllamaClient
        from app.services.sd_client import generate_image as sd_gen
        from app.core.prompts import build_sd_prompt, get_sd_system_prompt

        client = OllamaClient()
        result = client.chat(
            model=runtime_cfg.get_core_model(),
            messages=[
                {"role": "system", "content": get_sd_system_prompt()},
                {"role": "user", "content": build_sd_prompt(arabic_request, context, color_palette)},
            ],
            options={"temperature": 0.4, "num_predict": 120},
            think=False,
            timeout=30,
        )
        sd_prompt = result["content"].strip().strip('"').strip()
        img_result = sd_gen(prompt=sd_prompt, sd_url=settings.SD_BASE_URL)
        return img_result[0] if img_result else None
    except Exception as e:
        logger.warning(f"Pipeline image gen failed: {e}")
        return None


# ── Request schema ────────────────────────────────────────────────────────────


class PipelineAskRequest(BaseModel):
    content_id: str
    question: str
    voice_id: Optional[str] = None
    dialect: str = "ar-SA"
    gender: str = "female"
    speed: float = 1.0


# ── POST /ask ─────────────────────────────────────────────────────────────────


@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
def pipeline_ask(
    request: Request,
    payload: PipelineAskRequest,
    auth: tuple = Depends(get_pipeline_auth),
    db: Session = Depends(get_db),
):
    """
    Pipeline تعليمي متكامل — نص + صوت + خريطة ذهنية (عند الطلب).
    يقبل X-API-Key لنموذج متخصص أو مفتاح حزمة (yesk_bundle_*).

    الرد: {text, audio_url, duration, visual_url, used_sections, response_ms}
    visual_url يُرجع SVG فقط عندما يطلب الطالب شرحاً بصرياً.
    """
    specialist, bundle = auth
    _api_key = request.headers.get("X-API-Key", "")
    _client_ip = request.client.host if request.client else None
    content = db.query(SyncedContent).filter(
        SyncedContent.external_content_id == payload.content_id
    ).first()
    if not content:
        raise AppError(
            ErrorCodes.NOT_FOUND,
            f"المحتوى '{payload.content_id}' غير موجود — أرسله عبر webhook المزامنة أولاً",
            404,
        )

    lang = detect_language(payload.question)
    cfg = specialist.config_json or {}
    intro = cfg.get("intro_text") or (
        f"أنا {specialist.display_name}، مساعد تعليمي من يسرها. "
        "هنا لمساعدتك في فهم محتوى دروسك والإجابة على أسئلتك."
    )

    # ── هوية المساعد ──
    if is_identity_question(payload.question):
        audio_url, duration = _tts(intro, payload.dialect, payload.gender, payload.voice_id, payload.speed)
        _log(db, payload, specialist, intro, [], 0)
        if bundle:
            _log_gateway_bundle(db, bundle, _api_key, 0, _client_ip)
        return success({
            "text": intro, "audio_url": audio_url, "duration": duration,
            "visual_url": None, "used_sections": [], "response_ms": 0,
        })

    chunks = content.chunks_json or []

    # ── محتوى فارغ (لم يُزامَن بعد) ──
    if not chunks:
        no_content_msg = (
            "محتوى هذا الدرس لم يُزامَن بعد. يرجى المحاولة لاحقاً."
            if lang == "ar" else
            "This lesson has no synced content yet. Please try again later."
        )
        audio_url, duration = _tts(no_content_msg, payload.dialect, payload.gender, payload.voice_id, payload.speed)
        _log(db, payload, specialist, no_content_msg, [], 0)
        if bundle:
            _log_gateway_bundle(db, bundle, _api_key, 0, _client_ip)
        return success({
            "text": no_content_msg, "audio_url": audio_url, "duration": duration,
            "visual_url": None, "used_sections": [], "response_ms": 0,
        })

    visual_wanted = is_visual_request(payload.question)
    relevant = retrieve_relevant_chunks(chunks, payload.question, top_k=3)
    context = build_context_from_chunks(relevant)

    # ── سؤال خارج النطاق ──
    if not relevant:
        if visual_wanted:
            # لا توجد فقرات ذات صلة لكن المتعلم طلب شرحاً بصرياً → خريطة كاملة للدرس
            mm_title = (content.title or "").strip()
            if not mm_title or _UUID_RE.match(mm_title):
                mm_title = "خريطة الدرس"
            out_msg = (
                "هذه خريطة ذهنية لمحاور الدرس."
                if lang == "ar" else
                "Here is a full lesson mind map."
            )
            f_audio = _executor.submit(
                _tts, out_msg, payload.dialect, payload.gender, payload.voice_id, payload.speed
            )
            f_mm = _executor.submit(_mindmap, chunks, mm_title)
            audio_url, duration = f_audio.result()
            visual_url = f_mm.result()
            _log(db, payload, specialist, out_msg, [], 0)
            if bundle:
                _log_gateway_bundle(db, bundle, _api_key, 0, _client_ip)
            return success({
                "text": out_msg, "audio_url": audio_url, "duration": duration,
                "visual_url": visual_url, "used_sections": [], "response_ms": 0,
            })
        out_msg = (
            "هذا السؤال خارج نطاق درسنا الحالي. يمكنني مساعدتك فقط في مواضيع هذا الدرس."
            if lang == "ar" else
            "This question is outside our current lesson scope."
        )
        audio_url, duration = _tts(out_msg, payload.dialect, payload.gender, payload.voice_id, payload.speed)
        _log(db, payload, specialist, out_msg, [], 0)
        if bundle:
            _log_gateway_bundle(db, bundle, _api_key, 0, _client_ip)
        return success({
            "text": out_msg, "audio_url": audio_url, "duration": duration,
            "visual_url": None, "used_sections": [], "response_ms": 0,
        })

    # ── RAG → نص ──
    system_prompt = build_system_prompt(
        specialist.system_prompt or _DEFAULT_PROMPT,
        intro_text=intro, detected_lang=lang,
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
    gate_retries = 0

    if not passes_quality_gate(answer, lang):
        gate_retries = 1
        logger.warning("Quality gate failed — retry 1 (temp=0.5)")
        result = client.chat(model=model, messages=messages, options=_RETRY_OPTIONS, think=False)
        answer = result["content"]

        if not passes_quality_gate(answer, lang):
            gate_retries = 2
            logger.warning("Quality gate failed — retry 2 (temp=0.7)")
            result = client.chat(model=model, messages=messages, options=_RETRY_OPTIONS2, think=False)
            answer = result["content"]
            if not passes_quality_gate(answer, lang):
                logger.error("Quality gate: all retries failed — short retry prompt sent")
                gate_retries = -1
                answer = "لم أفهم السؤال جيداً، هل يمكنك إعادة صياغته؟" if lang == "ar" else "Could you rephrase your question?"

    text_ms = int((time.perf_counter() - start) * 1000)

    image_wanted = is_image_gen_request(payload.question)
    mm_title = content.title if content.title and not _UUID_RE.match(content.title) else None

    # ── TTS + Mind Map + Image بالتوازي (مستقلَّان) ──
    future_tts = _executor.submit(
        _tts, answer, payload.dialect, payload.gender, payload.voice_id, payload.speed
    )
    future_mm = (
        _executor.submit(_mindmap, chunks, mm_title or "خريطة الدرس")
        if visual_wanted else None
    )
    course_colors = content.color_palette or ""
    future_img = (
        _executor.submit(_generate_image, payload.question, context, course_colors)
        if image_wanted else None
    )

    audio_url, duration = future_tts.result()
    visual_url = future_mm.result() if future_mm is not None else None
    image_url = future_img.result() if future_img is not None else None

    total_ms = int((time.perf_counter() - start) * 1000)
    _log(db, payload, specialist, answer, relevant, total_ms)
    if bundle:
        _log_gateway_bundle(db, bundle, _api_key, total_ms, _client_ip)

    return success({
        "text": answer,
        "audio_url": audio_url,
        "duration": duration,
        "visual_url": visual_url,
        "image_url": image_url,
        "used_sections": [c.get("section", "") for c in relevant],
        "response_ms": total_ms,
        "text_ms": text_ms,
        "gate_retries": gate_retries,
    })


# ── GET /audio/{audio_id} ─────────────────────────────────────────────────────


@router.get("/audio/{audio_id}")
def get_audio(audio_id: str):
    """يُرجع ملف WAV — متاح 24 ساعة من وقت الإنشاء."""
    if not _UUID_RE.match(audio_id):
        raise AppError(ErrorCodes.NOT_FOUND, "معرّف الصوت غير صالح", 404)

    path = _audio_dir() / f"{audio_id}.wav"
    if not path.exists():
        raise AppError(ErrorCodes.NOT_FOUND, "ملف الصوت غير موجود أو منتهي الصلاحية", 404)
    if time.time() - path.stat().st_mtime > _AUDIO_TTL:
        path.unlink(missing_ok=True)
        raise AppError(ErrorCodes.NOT_FOUND, "انتهت صلاحية ملف الصوت", 404)

    return FileResponse(path, media_type="audio/wav",
                        headers={"Cache-Control": f"max-age={_AUDIO_TTL}"})


# ── GET /visual/{file_id} ─────────────────────────────────────────────────────


@router.get("/visual/{file_id}")
def get_visual(file_id: str):
    """يُرجع ملف SVG للخريطة الذهنية — متاح 24 ساعة."""
    from app.services.mind_map_service import get_cached_svg

    svg_path = get_cached_svg(file_id)
    if svg_path is None:
        raise AppError(ErrorCodes.NOT_FOUND, "الخريطة الذهنية غير موجودة أو منتهية الصلاحية", 404)

    try:
        svg_bytes = svg_path.read_bytes()
    except (FileNotFoundError, OSError):
        raise AppError(ErrorCodes.NOT_FOUND, "الخريطة الذهنية غير موجودة", 404)

    return Response(
        content=svg_bytes,
        media_type="image/svg+xml",
        headers={"Cache-Control": f"max-age={_VISUAL_TTL}"},
    )


# ── GET /image/{image_id} ─────────────────────────────────────────────────────


@router.get("/image/{image_id}")
def get_image(image_id: str):
    """يُرجع صورة PNG مُولَّدة من Stable Diffusion — متاحة 24 ساعة."""
    if not _UUID_RE.match(image_id):
        raise AppError(ErrorCodes.NOT_FOUND, "معرّف الصورة غير صالح", 404)

    from app.services.sd_client import get_cached_image
    path = get_cached_image(image_id)
    if path is None:
        raise AppError(ErrorCodes.NOT_FOUND, "الصورة غير موجودة أو انتهت صلاحيتها", 404)

    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "max-age=86400"},
    )


# ── Logging helpers ───────────────────────────────────────────────────────────


def _log_gateway_bundle(
    db: Session,
    bundle: SpecialistBundle,
    api_key: str,
    response_ms: int,
    ip: str | None,
) -> None:
    try:
        db.add(GatewayRequestLog(
            key_prefix=api_key[:24] if api_key else "unknown",
            key_type="bundle",
            bundle_id=bundle.id,
            endpoint="/specialist/pipeline/ask",
            specialists_used=["education"],
            response_ms=response_ms,
            status="success",
            ip_address=ip,
        ))
        bundle.total_requests = (bundle.total_requests or 0) + 1
        db.commit()
    except Exception:
        db.rollback()


def _log(
    db: Session,
    payload: PipelineAskRequest,
    specialist: SpecialistModel,
    answer: str,
    relevant: list[dict],
    response_ms: int,
) -> None:
    try:
        specialist.total_requests = (specialist.total_requests or 0) + 1
        db.add(StudentQuestion(
            external_content_id=payload.content_id,
            specialist_id=specialist.id,
            question=payload.question,
            answer=answer[:1000],
            used_sections=[c.get("section", "") for c in relevant],
            response_ms=response_ms,
        ))
        db.commit()
    except Exception:
        db.rollback()
