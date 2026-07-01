"""
Voice Specialist API — Whisper STT + XTTS-v2 TTS + Voice Cloning
Endpoints:
  POST /specialist/voice/transcribe  — صوت → نص
  POST /specialist/voice/synthesize  — نص → صوت
  POST /specialist/voice/clone       — رفع عينة صوت للاستنساخ
  GET  /specialist/voice/status      — جاهزية النموذج
  POST /specialist/voice/ask         — شات صوتي كامل (STT + LLM + TTS)
"""
import io
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db.session import get_db
from app.core.deps import get_api_key_specialist
from app.core.responses import success, AppError, ErrorCodes
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.models.specialist import SpecialistModel
from app.services.voice.voice_service import (
    transcribe_audio,
    synthesize_speech,
    save_voice_sample,
    get_voice_sample,
    is_voice_ready,
)

router = APIRouter(prefix="/specialist/voice", tags=["Specialist - Voice"])

SUPPORTED_AUDIO_TYPES = {
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mpeg", "audio/mp3",
    "audio/ogg", "audio/webm",
    "audio/mp4", "audio/m4a",
    "application/octet-stream",
}


def _get_voice_specialist(db: Session) -> SpecialistModel:
    """يُرجع النموذج الصوتي النشط"""
    spec = db.query(SpecialistModel).filter(
        SpecialistModel.specialization == "voice",
        SpecialistModel.status == "active"
    ).first()
    if not spec:
        raise AppError(
            ErrorCodes.NOT_FOUND,
            "نموذج الصوت غير نشط بعد — أنشئه من لوحة التحكم أولاً",
            404
        )
    return spec


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
def voice_status(db: Session = Depends(get_db)):
    """حالة نموذج الصوت — متاح بدون API Key"""
    ready = is_voice_ready()

    # هل النموذج الصوتي موجود في DB؟
    spec = db.query(SpecialistModel).filter(
        SpecialistModel.specialization == "voice"
    ).first()

    return success({
        **ready,
        "specialist_status": spec.status if spec else "not_created",
        "specialist_name": spec.display_name if spec else None,
        "has_voice_sample": bool(
            get_voice_sample(spec.name) if spec else None
        ),
        "installation_guide": {
            "whisper": "pip install openai-whisper",
            "xtts":    "pip install TTS",
            "note":    "يجب تثبيتهما داخل الـ Docker container أو الـ requirements.txt"
        } if not (ready["whisper_available"] and ready["xtts_available"]) else None,
    })


# ── Transcribe (STT) ──────────────────────────────────────────────────────────

@router.post("/transcribe")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def transcribe(
    request:  Request,
    audio:    UploadFile = File(..., description="ملف الصوت (WAV/MP3/OGG/WebM)"),
    language: Optional[str] = Form(None, description="ar | en | None (كشف تلقائي)"),
    db:       Session = Depends(get_db),
    _spec:    SpecialistModel = Depends(get_api_key_specialist),
):
    """
    تحويل صوت → نص.
    يدعم العربية والإنجليزية وكل اللغات بكشف تلقائي.
    """
    spec = _get_voice_specialist(db)

    if audio.content_type and audio.content_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(
            400,
            f"نوع الملف غير مدعوم: {audio.content_type}"
        )

    audio_bytes = await audio.read()
    if len(audio_bytes) < 1000:
        raise HTTPException(400, "الملف الصوتي صغير جداً أو فارغ")

    try:
        result = transcribe_audio(
            audio_bytes=audio_bytes,
            language=language,
            filename=audio.filename or "audio.wav"
        )

        # تحديث إحصائيات النموذج
        spec.total_requests = (spec.total_requests or 0) + 1
        db.commit()

        return success({
            "text":     result["text"],
            "language": result["language"],
            "duration": result["duration"],
            "segments": result["segments"],
            "word_count": len(result["text"].split()),
        })

    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"خطأ في التحويل: {str(e)[:200]}")


# ── Synthesize (TTS) ──────────────────────────────────────────────────────────

@router.post("/synthesize")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def synthesize(
    request:       Request,
    text:          str  = Form(..., description="النص المراد تحويله"),
    language:      str  = Form("ar", description="ar | en | fr | ..."),
    use_cloned:    bool = Form(False, description="استخدم الصوت المستنسَخ إن وُجد"),
    db:            Session = Depends(get_db),
    _spec:         SpecialistModel = Depends(get_api_key_specialist),
):
    """
    تحويل نص → صوت WAV.
    إذا use_cloned=True وتوجد عينة محفوظة → صوت مستنسَخ.
    """
    if not text.strip():
        raise HTTPException(400, "النص فارغ")
    if len(text) > 5000:
        raise HTTPException(400, "النص طويل جداً (حد أقصى 5000 حرف)")

    try:
        spec = _get_voice_specialist(db)
        speaker_wav = None

        if use_cloned:
            speaker_wav = get_voice_sample(spec.name)

        audio_bytes = synthesize_speech(
            text=text,
            language=language,
            speaker_wav_bytes=speaker_wav,
        )

        spec.total_requests = (spec.total_requests or 0) + 1
        db.commit()

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=yesarha_voice.wav",
                "X-Text-Length": str(len(text)),
                "X-Language": language,
                "X-Cloned": str(use_cloned and speaker_wav is not None),
            }
        )

    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"خطأ في التوليد: {str(e)[:200]}")


# ── Clone Voice ───────────────────────────────────────────────────────────────

@router.post("/clone")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def clone_voice(
    request: Request,
    sample:  UploadFile = File(..., description="عينة صوت مرجعية (WAV، 6-30 ثانية)"),
    db:      Session = Depends(get_db),
    _spec:   SpecialistModel = Depends(get_api_key_specialist),
):
    """
    رفع عينة صوت مرجعية للاستنساخ.
    كل طلب synthesize بـ use_cloned=True سيستخدم هذا الصوت.
    """
    sample_bytes = await sample.read()
    duration_estimate = len(sample_bytes) / (16000 * 2)  # تقدير تقريبي

    if duration_estimate < settings.VOICE_SAMPLE_MIN_SECONDS:
        raise HTTPException(
            400,
            f"العينة قصيرة جداً — يجب {settings.VOICE_SAMPLE_MIN_SECONDS}+ ثوانٍ "
            f"(المقدَّر: {duration_estimate:.1f}s)"
        )

    if len(sample_bytes) > 50 * 1024 * 1024:  # 50MB max
        raise HTTPException(400, "حجم الملف كبير جداً (حد أقصى 50MB)")

    try:
        spec = _get_voice_specialist(db)
        saved_path = save_voice_sample(spec.name, sample_bytes)

        return success({
            "message":         "✅ تم حفظ عينة الصوت — الاستنساخ جاهز",
            "specialist":      spec.display_name,
            "saved_path":      saved_path,
            "duration_est":    round(duration_estimate, 1),
            "usage":           "أرسل use_cloned=true في /synthesize لاستخدام هذا الصوت",
        })

    except Exception as e:
        raise HTTPException(500, f"خطأ في الحفظ: {str(e)[:200]}")


# ── Voice Chat (STT → LLM → TTS) ─────────────────────────────────────────────

class VoiceChatRequest(BaseModel):
    message: str
    language: str = "ar"
    use_cloned_voice: bool = False
    return_audio: bool = True


@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def voice_ask(
    request: Request,
    payload: VoiceChatRequest,
    db:      Session = Depends(get_db),
    _spec:   SpecialistModel = Depends(get_api_key_specialist),
):
    """
    شات صوتي نصي كامل: نص → LLM → صوت.
    يُستخدَم من لوحة التحكم أو من System 2.
    """
    from app.services.ollama_client import OllamaClient

    spec = _get_voice_specialist(db)
    client = OllamaClient()   # يقرأ URL من runtime_cfg تلقائياً

    from app.core.prompts import build_system_prompt
    try:
        from app.services.runtime_config import runtime_cfg
        core_model = runtime_cfg.get_core_model()
    except Exception:
        from app.core.config import settings
        core_model = settings.CORE_MODEL

    messages = [
        {"role": "system", "content": build_system_prompt(spec.system_prompt or "أنت مساعد صوتي ذكي من يسرها.")},
        {"role": "user",   "content": payload.message},
    ]

    # LLM Response
    llm_result = client.chat(
        model=spec.base_model or core_model,
        messages=messages
    )
    response_text = llm_result.get("content", "")

    spec.total_requests = (spec.total_requests or 0) + 1
    db.commit()

    if not payload.return_audio:
        return success({
            "text":       response_text,
            "input":      payload.message,
            "language":   payload.language,
            "model":      spec.base_model,
        })

    # TTS
    try:
        speaker_wav = get_voice_sample(spec.name) if payload.use_cloned_voice else None
        audio_bytes = synthesize_speech(
            text=response_text,
            language=payload.language,
            speaker_wav_bytes=speaker_wav,
        )
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "X-Response-Text": response_text[:200],
                "X-Language": payload.language,
            }
        )
    except RuntimeError:
        # fallback: رجّع النص لو TTS فشل
        return success({
            "text":    response_text,
            "warning": "TTS غير متاح — يُرجَع النص فقط",
        })
