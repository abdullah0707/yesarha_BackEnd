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
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core.config import settings
from app.core.deps import get_api_key_specialist
from app.core.responses import success, AppError, ErrorCodes
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.models.specialist import SpecialistModel
from app.services.voice.voice_service import (
    transcribe_audio,
    synthesize_speech,
    synthesize_with_edge_tts,
    synthesize_with_habibi,
    warmup_habibi,
    save_voice_sample,
    get_voice_sample,
    is_voice_ready,
    reset_voice_cache,
    preprocess_arabic_for_tts,
    enhance_voice_sample,
    apply_speed_control,
    EDGE_TTS_VOICES,
    DIALECT_VOICES,
    MSA_DIALECTS,
    HABIBI_DIALECT_MAP,
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


@router.post("/arabic/analyze")
async def analyze_arabic(
    request: Request,
    text: str = Form(...),
    _: str = Depends(get_api_key_specialist),
):
    """
    يُحلّل النص العربي ويُظهر كيف سيُعالَج قبل TTS.
    مفيد للتشخيص وفهم سبب ضعف النطق.
    """
    normalized = preprocess_arabic_for_tts(text, use_phonemes=False)
    phonemes = preprocess_arabic_for_tts(text, use_phonemes=True)
    return success({
        "original": text,
        "normalized": normalized,
        "espeak_phonemes": phonemes,
        "will_use_phonemes": phonemes != normalized,
    })


@router.get("/voices")
def list_voices():
    """قائمة الأصوات واللهجات المتاحة"""
    return success({
        "dialect_voices": {
            locale: {"female": voices[0], "male": voices[1]}
            for locale, voices in DIALECT_VOICES.items()
        },
        "msa_dialects": list(MSA_DIALECTS),
        "other_languages": EDGE_TTS_VOICES,
        "default_dialect": "ar-SA",
        "note": "أرسل dialect + gender في /synthesize لاختيار اللهجة والجنس",
    })


@router.post("/status/refresh")
def refresh_voice_status(
    db: Session = Depends(get_db),
    _: str = Depends(get_api_key_specialist),
):
    """إعادة فحص جاهزية الصوت — يُستخدم بعد تثبيت مكتبات جديدة بدون restart"""
    reset_voice_cache()
    return voice_status(db)


@router.get("/config")
def get_voice_config(
    db: Session = Depends(get_db),
    _: str = Depends(get_api_key_specialist),
):
    """إعدادات النموذج الصوتي الحالية"""
    spec = _get_voice_specialist(db)
    cfg  = spec.config_json or {}
    return success({
        "voice_speed":    cfg.get("voice_speed", 1.0),
        "voice_id":       cfg.get("voice_id", None),
        "dialect":        cfg.get("dialect", "ar-SA"),
        "gender":         cfg.get("gender", "female"),
        "speed_note":     "0.5 = بطيء جداً | 0.8 = بطيء | 1.0 = طبيعي | 1.2 = سريع | 1.5 = سريع جداً",
        "edge_rate":      f"{int((cfg.get('voice_speed', 1.0) - 1.0) * 100):+d}%",
    })


@router.put("/config")
def update_voice_config(
    db:          Session         = Depends(get_db),
    _:           str             = Depends(get_api_key_specialist),
    voice_speed: Optional[float] = None,
    voice_id:    Optional[str]   = None,
    dialect:     Optional[str]   = None,
    gender:      Optional[str]   = None,
):
    """
    تحديث الإعدادات الافتراضية للنموذج الصوتي.
    - voice_speed: 0.5 → 2.0
    - dialect: ar-SA | ar-EG | ar-AE | ar-IQ | ar-JO | ar-LB | ar-MA | ar-KW ...
    - gender: female | male
    """
    if voice_speed is not None and not (0.5 <= voice_speed <= 2.0):
        raise HTTPException(400, "voice_speed يجب أن يكون بين 0.5 و 2.0")
    if dialect is not None and dialect not in DIALECT_VOICES:
        raise HTTPException(400, f"dialect غير معروف: {dialect} — انظر /voices للقائمة")
    if gender is not None and gender not in ("female", "male"):
        raise HTTPException(400, "gender يجب أن يكون 'female' أو 'male'")

    spec = _get_voice_specialist(db)
    cfg  = dict(spec.config_json or {})

    if voice_speed is not None: cfg["voice_speed"] = round(voice_speed, 2)
    if voice_id   is not None:  cfg["voice_id"]    = voice_id
    if dialect    is not None:  cfg["dialect"]     = dialect
    if gender     is not None:  cfg["gender"]      = gender

    spec.config_json = cfg
    db.commit()

    return success({
        "message":     "✅ تم تحديث إعدادات الصوت",
        "voice_speed": cfg.get("voice_speed", 1.0),
        "voice_id":    cfg.get("voice_id"),
        "dialect":     cfg.get("dialect", "ar-SA"),
        "gender":      cfg.get("gender", "female"),
        "edge_rate":   f"{int((cfg.get('voice_speed', 1.0) - 1.0) * 100):+d}%",
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
    request:    Request,
    text:       str             = Form(...,       description="النص المراد تحويله"),
    language:   str             = Form("ar",      description="ar | en | fr | ..."),
    dialect:    Optional[str]   = Form(None,      description="ar-SA | ar-EG | ar-AE | ar-IQ | ... — None يستخدم إعداد النموذج"),
    gender:     Optional[str]   = Form(None,      description="female | male — None يستخدم إعداد النموذج"),
    use_cloned: bool            = Form(False,     description="استخدم الصوت المستنسَخ إن وُجد"),
    voice_id:   Optional[str]   = Form(None,      description="Edge-TTS voice ID مباشر — يتجاوز dialect/gender"),
    speed:      Optional[float] = Form(None,      description="سرعة النطق 0.5-2.0 — None يستخدم إعداد النموذج"),
    db:         Session         = Depends(get_db),
    _spec:      SpecialistModel = Depends(get_api_key_specialist),
):
    """
    تحويل نص → صوت WAV بشري طبيعي.
    - dialect: اللهجة العربية (ar-SA, ar-EG, ar-AE...). بدون قيمة = إعداد النموذج الافتراضي.
    - gender: female | male. بدون قيمة = إعداد النموذج الافتراضي.
    - speed: يتجاوز الإعداد الافتراضي للنموذج.
    """
    if not text.strip():
        raise HTTPException(400, "النص فارغ")
    if len(text) > 5000:
        raise HTTPException(400, "النص طويل جداً (حد أقصى 5000 حرف)")
    if speed is not None and not (0.5 <= speed <= 2.0):
        raise HTTPException(400, "speed يجب أن يكون بين 0.5 و 2.0")
    if dialect is not None and dialect not in DIALECT_VOICES:
        raise HTTPException(400, f"dialect غير معروف: {dialect}")
    if gender is not None and gender not in ("female", "male"):
        raise HTTPException(400, "gender يجب أن يكون 'female' أو 'male'")

    try:
        spec = _get_voice_specialist(db)
        cfg  = spec.config_json or {}

        effective_speed   = speed   if speed   is not None else float(cfg.get("voice_speed", 1.0))
        effective_dialect = dialect if dialect is not None else cfg.get("dialect", "ar-SA")
        effective_gender  = gender  if gender  is not None else cfg.get("gender",  "female")
        effective_voice   = voice_id or cfg.get("voice_id") or None

        speaker_wav = get_voice_sample(spec.name) if use_cloned else None

        audio_bytes = synthesize_speech(
            text=text,
            language=language,
            dialect=effective_dialect,
            gender=effective_gender,
            speaker_wav_bytes=speaker_wav,
            voice_id=effective_voice,
            speed=effective_speed,
        )

        spec.total_requests = (spec.total_requests or 0) + 1
        db.commit()

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=yesarha_voice.wav",
                "X-Text-Length":  str(len(text)),
                "X-Language":     language,
                "X-Dialect":      effective_dialect,
                "X-Gender":       effective_gender,
                "X-Cloned":       str(use_cloned and speaker_wav is not None),
                "X-Speed":        str(effective_speed),
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


# ── Habibi-TTS Direct Test ───────────────────────────────────────────────────

@router.post("/clone/test")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def test_habibi_clone(
    request:  Request,
    sample:   UploadFile = File(..., description="عينة صوت المحاضر (WAV)"),
    text:     str        = Form(..., description="النص المراد توليده بصوت المحاضر"),
    dialect:  str        = Form("ar-SA", description="ar-SA | ar-EG | ar-AE | ..."),
    db:       Session    = Depends(get_db),
    _spec:    SpecialistModel = Depends(get_api_key_specialist),
):
    """
    اختبار مباشر لـ Habibi-TTS: رفع عينة + نص → استنساخ صوت المحاضر.
    عند الاستدعاء الأول يُحمَّل النموذج (~2GB) — انتظر حتى 3 دقائق.
    """
    import shutil
    if not shutil.which("habibi-tts_infer-cli"):
        raise HTTPException(
            503,
            "habibi-tts غير مثبَّت في الـ container — "
            "أعد بناء الـ Docker image بـ: "
            "docker compose build --build-arg VOICE_INSTALL=true --build-arg VOICE_CACHE_BUST=$(date +%s) backend"
        )

    if dialect not in HABIBI_DIALECT_MAP:
        raise HTTPException(400, f"dialect غير معروف: {dialect}")

    sample_bytes = await sample.read()
    if len(sample_bytes) < 8000:
        raise HTTPException(400, "العينة قصيرة جداً — يجب 6+ ثوانٍ على الأقل")

    try:
        from app.services.voice.voice_service import enhance_voice_sample
        enhanced = enhance_voice_sample(sample_bytes)
        audio_bytes = synthesize_with_habibi(
            text=text,
            ref_audio_bytes=enhanced,
            dialect=dialect,
        )
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=habibi_clone_test.wav",
                "X-Dialect": dialect,
                "X-Habibi-Dialect": HABIBI_DIALECT_MAP[dialect],
            }
        )
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"خطأ في Habibi-TTS: {str(e)[:300]}")


# ── Habibi Warmup ─────────────────────────────────────────────────────────────

@router.post("/warmup")
@limiter.limit("2/minute")
async def warmup_voice(
    request: Request,
    db:      Session = Depends(get_db),
    _spec:   SpecialistModel = Depends(get_api_key_specialist),
):
    """
    تحميل Habibi-TTS في CUDA مسبقاً لتسريع أول طلب استنساخ.
    يستغرق ~25-30 ثانية في أول مرة، أقل من 5 ثوانٍ بعدها.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, warmup_habibi)
    if result.get("status") == "error":
        raise HTTPException(503, result.get("error", "Warmup failed"))
    return success(result)


# ── Voice Chat (STT → LLM → TTS) ─────────────────────────────────────────────

@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def voice_ask(
    request:           Request,
    message:           str  = Form(...,  description="النص المراد إرساله للنموذج"),
    language:          str  = Form("ar", description="ar | en"),
    use_cloned_voice:  bool = Form(False, description="استخدم الصوت المستنسَخ"),
    return_audio:      bool = Form(True,  description="أرجع صوتاً أم نصاً فقط"),
    db:                Session = Depends(get_db),
    _spec:             SpecialistModel = Depends(get_api_key_specialist),
):
    """
    شات صوتي نصي كامل: نص → LLM → صوت.
    يُستخدَم من لوحة التحكم أو من System 2.
    """
    from app.services.ollama_client import OllamaClient
    from app.core.prompts import build_system_prompt

    spec = _get_voice_specialist(db)
    client = OllamaClient()

    try:
        from app.services.runtime_config import runtime_cfg
        core_model = runtime_cfg.get_core_model()
    except Exception:
        core_model = settings.CORE_MODEL

    messages = [
        {"role": "system", "content": build_system_prompt(spec.system_prompt or "أنت مساعد صوتي ذكي من يسرها.")},
        {"role": "user",   "content": message},
    ]

    llm_result = client.chat(model=spec.base_model or core_model, messages=messages, think=False)
    response_text = llm_result.get("content", "")

    spec.total_requests = (spec.total_requests or 0) + 1
    db.commit()

    if not return_audio:
        return success({
            "text":    response_text,
            "input":   message,
            "language": language,
            "model":   spec.base_model,
        })

    try:
        speaker_wav = get_voice_sample(spec.name) if use_cloned_voice else None
        audio_bytes = synthesize_speech(
            text=response_text,
            language=language,
            speaker_wav_bytes=speaker_wav,
        )
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "X-Response-Text": response_text[:200],
                "X-Language": language,
            }
        )
    except RuntimeError:
        return success({"text": response_text, "warning": "TTS غير متاح — يُرجَع النص فقط"})
