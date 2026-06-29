"""
Voice Service — Whisper STT + XTTS-v2 TTS + Voice Cloning
يعمل محلياً على RTX 4060 (8GB VRAM) وعلى السيرفر السحابي بنفس الكود.

Architecture:
- Whisper large-v3 → STT (عربي + إنجليزي بدقة عالية)
- XTTS-v2          → TTS + Voice Cloning
- كلاهما يعملان على GPU تلقائياً إن توفّر، وإلا CPU

تحميل النماذج عند أول استخدام (lazy loading) لتوفير VRAM.
"""
import io
import os
import logging
import tempfile
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("yesarha.voice")

# ── Lazy-loaded models (لا يُحمَّلان حتى الطلب الأول) ───────────────────────

_whisper_model = None
_xtts_model    = None
_xtts_config   = None


def _get_whisper():
    """تحميل Whisper عند أول استخدام"""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    try:
        import whisper
        logger.info(f"Loading Whisper {settings.WHISPER_MODEL}...")
        _whisper_model = whisper.load_model(
            settings.WHISPER_MODEL,
            device="cuda" if _has_cuda() else "cpu"
        )
        logger.info("✅ Whisper loaded")
        return _whisper_model
    except ImportError:
        raise RuntimeError(
            "Whisper غير مثبَّت. شغّل: pip install openai-whisper"
        )
    except Exception as e:
        raise RuntimeError(f"فشل تحميل Whisper: {e}")


def _get_xtts():
    """تحميل XTTS-v2 عند أول استخدام"""
    global _xtts_model, _xtts_config
    if _xtts_model is not None:
        return _xtts_model, _xtts_config

    try:
        from TTS.api import TTS
        logger.info("Loading XTTS-v2...")
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        if _has_cuda():
            tts = tts.to("cuda")
        _xtts_model  = tts
        _xtts_config = None
        logger.info("✅ XTTS-v2 loaded")
        return _xtts_model, _xtts_config
    except ImportError:
        raise RuntimeError(
            "TTS غير مثبَّت. شغّل: pip install TTS"
        )
    except Exception as e:
        raise RuntimeError(f"فشل تحميل XTTS-v2: {e}")


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


# ── STT: Whisper ──────────────────────────────────────────────────────────────

def transcribe_audio(
    audio_bytes: bytes,
    language: Optional[str] = None,
    filename: str = "audio.wav"
) -> dict:
    """
    تحويل صوت → نص باستخدام Whisper.
    يدعم العربية والإنجليزية وكل اللغات تلقائياً.

    Returns:
        {
          "text":     النص المُستخرَج,
          "language": اللغة المكتشفة,
          "segments": تفاصيل كل مقطع مع الوقت,
          "duration": مدة التسجيل بالثواني
        }
    """
    model = _get_whisper()

    # احفظ في ملف مؤقت (Whisper يحتاج مسار ملف)
    suffix = Path(filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        result = model.transcribe(
            tmp_path,
            language=language,          # None = كشف تلقائي
            task="transcribe",
            verbose=False,
            fp16=_has_cuda(),
        )
        return {
            "text":     result["text"].strip(),
            "language": result.get("language", "unknown"),
            "segments": [
                {
                    "start": s["start"],
                    "end":   s["end"],
                    "text":  s["text"].strip(),
                }
                for s in result.get("segments", [])
            ],
            "duration": result["segments"][-1]["end"] if result.get("segments") else 0,
        }
    finally:
        os.unlink(tmp_path)


# ── TTS: XTTS-v2 ──────────────────────────────────────────────────────────────

def synthesize_speech(
    text: str,
    language: str = "ar",
    speaker_wav_bytes: Optional[bytes] = None,
    speaker_name: Optional[str] = None,
) -> bytes:
    """
    تحويل نص → صوت باستخدام XTTS-v2.
    إذا أُرسلت عينة صوت (speaker_wav) → استنساخ الصوت.
    إذا لم تُرسَل → صوت افتراضي جيد.

    Returns:
        bytes (WAV audio)
    """
    tts, _ = _get_xtts()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_file:
        out_path = out_file.name

    try:
        if speaker_wav_bytes:
            # Voice Cloning
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as spk_file:
                spk_file.write(speaker_wav_bytes)
                spk_path = spk_file.name

            try:
                tts.tts_to_file(
                    text=text,
                    file_path=out_path,
                    speaker_wav=spk_path,
                    language=language,
                    split_sentences=True,
                )
            finally:
                os.unlink(spk_path)
        else:
            # صوت افتراضي
            speakers = tts.speakers if hasattr(tts, "speakers") and tts.speakers else []
            default_speaker = speakers[0] if speakers else None

            if default_speaker:
                tts.tts_to_file(
                    text=text,
                    file_path=out_path,
                    speaker=default_speaker,
                    language=language,
                    split_sentences=True,
                )
            else:
                tts.tts_to_file(
                    text=text,
                    file_path=out_path,
                    language=language,
                    split_sentences=True,
                )

        return Path(out_path).read_bytes()

    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


# ── Voice Clone Storage ───────────────────────────────────────────────────────

def save_voice_sample(
    specialist_name: str,
    audio_bytes: bytes
) -> str:
    """
    يحفظ عينة الصوت المرجعية للاستنساخ اللاحق.
    Returns: مسار الملف المحفوظ
    """
    samples_dir = Path(settings.XTTS_MODEL_PATH) / "voice_samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    sample_path = samples_dir / f"{specialist_name}.wav"
    sample_path.write_bytes(audio_bytes)

    logger.info(f"✅ Voice sample saved for {specialist_name}")
    return str(sample_path)


def get_voice_sample(specialist_name: str) -> Optional[bytes]:
    """يُرجع عينة الصوت المحفوظة للنموذج المتخصص"""
    sample_path = Path(settings.XTTS_MODEL_PATH) / "voice_samples" / f"{specialist_name}.wav"
    if sample_path.exists():
        return sample_path.read_bytes()
    return None


def is_voice_ready() -> dict:
    """
    يتحقق من جاهزية نموذج الصوت.
    يُستدعى من health check ومن لوحة التحكم.
    """
    status = {
        "whisper_available": False,
        "xtts_available":    False,
        "cuda_available":    _has_cuda(),
        "whisper_model":     settings.WHISPER_MODEL,
        "message":           "",
    }

    try:
        import whisper
        status["whisper_available"] = True
    except ImportError:
        status["message"] += "Whisper غير مثبَّت. "

    try:
        from TTS.api import TTS  # noqa
        status["xtts_available"] = True
    except ImportError:
        status["message"] += "TTS (XTTS-v2) غير مثبَّت. "

    if status["whisper_available"] and status["xtts_available"]:
        status["message"] = "✅ جاهز — Whisper + XTTS-v2 متاحان"
    elif not status["message"]:
        status["message"] = "⚠️ بعض المكونات غير متاحة"

    return status
