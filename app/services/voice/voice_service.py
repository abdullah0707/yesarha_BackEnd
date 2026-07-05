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
import re
import logging
import subprocess
import tempfile
from dataclasses import dataclass

# XTTS-v2 يطلب قبول الـ Terms of Service — نوافق تلقائياً في بيئة السيرفر
os.environ.setdefault("COQUI_TOS_AGREED", "1")

# torch 2.6+ غيّر default weights_only=True لكن TTS 0.22.0 مكتوب للنسخة القديمة
# نُصلح ذلك بـ monkey-patch آمن — TTS مصدر موثوق
def _patch_torch_load():
    """torch 2.6+ غيّر default weights_only=True — TTS مصدر موثوق نُصلح ذلك."""
    try:
        import torch
        _orig = torch.load
        def _safe_load(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return _orig(*args, **kwargs)
        torch.load = _safe_load
    except Exception:
        pass

def _patch_torchaudio_load():
    """
    torchaudio 2.9+ استبدلت torchaudio.load بـ TorchCodec كـ backend افتراضي.
    TTS 0.22.0 تستدعي torchaudio.load لتحميل ملفات الصوت المرجعية.
    نُعيد torchaudio.load لاستخدام soundfile (مكتبة Python خالصة، لا تحتاج torchcodec).
    """
    try:
        import torch
        import soundfile as sf

        def _sf_load(uri, frame_offset=0, num_frames=-1, normalize=True,
                     channels_first=True, format=None, buffer_size=4096, backend=None):
            path = str(uri) if not isinstance(uri, str) else uri
            data, sr = sf.read(path, always_2d=True,
                               start=frame_offset,
                               frames=num_frames if num_frames > 0 else -1,
                               dtype="float32")
            waveform = torch.from_numpy(data.T)   # (channels, samples)
            return waveform, sr

        import torchaudio
        torchaudio.load = _sf_load

        # أيضاً patch الدالة المباشرة في _torchcodec module
        try:
            import torchaudio._torchcodec as _tc
            _tc.load_with_torchcodec = _sf_load
        except Exception:
            pass

    except Exception:
        pass

_patch_torch_load()
_patch_torchaudio_load()
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("yesarha.voice")

# ── Lazy-loaded models (لا يُحمَّلان حتى الطلب الأول) ───────────────────────

_whisper_model = None
_xtts_model    = None
_xtts_config   = None


def _get_whisper_model_name() -> str:
    """يقرأ حجم Whisper من runtime_cfg (قابل للتغيير من Dashboard بدون restart)"""
    try:
        from app.services.runtime_config import runtime_cfg
        return runtime_cfg.get("WHISPER_MODEL") or settings.WHISPER_MODEL
    except Exception:
        return settings.WHISPER_MODEL


def _get_whisper():
    """
    تحميل Whisper عند أول استخدام (lazy loading).
    يتحقق من runtime_cfg لمعرفة الحجم المطلوب — لو تغيّر يُعيد التحميل.
    """
    global _whisper_model
    model_name = _get_whisper_model_name()

    # إعادة تحميل لو تغيّر الحجم من Dashboard
    if _whisper_model is not None:
        current_name = getattr(_whisper_model, "_model_name", model_name)
        if current_name == model_name:
            return _whisper_model
        logger.info(f"Whisper model changed → {model_name}, reloading...")
        _whisper_model = None

    try:
        import whisper
        logger.info(f"Loading Whisper '{model_name}'...")
        model = whisper.load_model(
            model_name,
            device="cuda" if _has_cuda() else "cpu"
        )
        model._model_name = model_name   # tag للمقارنة لاحقاً
        _whisper_model = model
        logger.info(f"✅ Whisper '{model_name}' loaded")
        return _whisper_model
    except ImportError:
        raise RuntimeError("Whisper غير مثبَّت — تأكد من VOICE_INSTALL=true عند Docker build")
    except Exception as e:
        raise RuntimeError(f"فشل تحميل Whisper '{model_name}': {e}")


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


# ── Arabic Text Preprocessing ────────────────────────────────────────────────

def _normalize_arabic(text: str) -> str:
    """
    تنظيف النص العربي قبل إرساله لـ XTTS-v2:
    - توحيد أشكال الألف (أ إ آ → ا)
    - إزالة التطويل (ـ)
    - توحيد التاء المربوطة والهاء
    - إزالة رموز غير ضرورية
    - الحفاظ على التشكيل إن وُجد
    """
    # إزالة التطويل
    text = text.replace("ـ", "")
    # توحيد أشكال الألف
    text = re.sub(r"[أإآ]", "ا", text)
    # توحيد الياء
    text = text.replace("ى", "ي")
    # إزالة رموز غير عربية/إنجليزية (احتفظ بالمسافات والأرقام والترقيم)
    text = re.sub(r"[^؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
                  r"a-zA-Z0-9\s\.,!?؟،؛:\-\(\)]", " ", text)
    # تقليص المسافات المتعددة
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _arabic_to_phonemes(text: str) -> str:
    """
    يحوّل النص العربي إلى IPA phonemes باستخدام espeak-ng.
    يُستخدم كـ fallback لتحسين نطق XTTS-v2 للعربية.
    يُرجع النص الأصلي في حالة فشل espeak-ng.
    """
    try:
        result = subprocess.run(
            ["espeak-ng", "-v", "ar", "--ipa", "-q", "--", text],
            capture_output=True, text=True, timeout=10
        )
        phonemes = result.stdout.strip()
        if phonemes:
            return phonemes
    except Exception:
        pass
    return text


def preprocess_arabic_for_tts(text: str, use_phonemes: bool = False) -> str:
    """
    المعالجة المسبقة الكاملة للنص العربي قبل TTS:
    1. تنظيف وتوحيد الحروف
    2. (اختياري) تحويل لـ IPA phonemes عبر espeak-ng
    """
    cleaned = _normalize_arabic(text)
    if use_phonemes and cleaned:
        return _arabic_to_phonemes(cleaned)
    return cleaned


# ── Layer 2: Reference Audio Enhancer ────────────────────────────────────────

def enhance_voice_sample(audio_bytes: bytes) -> bytes:
    """
    Layer 2 — تنقية عينة الصوت المرجعية قبل Voice Cloning:
    1. إزالة الضوضاء الخلفية (noisereduce)
    2. تحويل إلى mono 24kHz (معيار XTTS-v2 المثالي)
    3. توحيد مستوى الصوت (peak normalization -3dB)
    4. اختيار أفضل 15 ثانية إن كانت العينة > 30s

    يُرجع الـ bytes الأصلية إن فشل أي خطوة — لا يُوقف العملية أبداً.
    """
    try:
        import numpy as np
        import soundfile as sf
        import noisereduce as nr

        with io.BytesIO(audio_bytes) as buf:
            audio, sr = sf.read(buf, always_2d=True, dtype="float32")

        # تحويل إلى mono
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        # إزالة الضوضاء
        audio = nr.reduce_noise(y=audio, sr=sr, stationary=False, prop_decrease=0.75)

        # Resample إلى 24kHz إن لزم
        target_sr = 24000
        if sr != target_sr:
            try:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)
            except ImportError:
                # librosa غير متاح — نستخدم scipy إن وُجد
                try:
                    from scipy.signal import resample_poly
                    from math import gcd
                    g = gcd(int(sr), target_sr)
                    audio = resample_poly(audio, target_sr // g, int(sr) // g)
                except ImportError:
                    pass  # نبقى على sr الأصلي
            sr = target_sr

        # اختيار أفضل 15 ثانية إن كانت العينة أطول من 30s
        max_samples    = 30 * sr
        target_samples = 15 * sr

        if len(audio) > max_samples:
            hop = sr  # step = ثانية واحدة
            best_start  = 0
            best_energy = -1.0
            end_limit   = len(audio) - target_samples

            for start in range(0, end_limit, hop):
                seg    = audio[start : start + target_samples]
                energy = float(np.sqrt(np.mean(seg ** 2)))
                if energy > best_energy:
                    best_energy = energy
                    best_start  = start

            audio = audio[best_start : best_start + target_samples]
            logger.info(f"Audio enhancer: extracted best 15s segment (start={best_start/sr:.1f}s)")

        # Peak normalization → -3 dB
        peak = float(np.abs(audio).max())
        if peak > 0:
            audio = audio * (0.7079 / peak)

        out = io.BytesIO()
        sf.write(out, audio, sr, format="WAV", subtype="PCM_16")
        enhanced = out.getvalue()
        logger.info(f"✅ Audio enhanced: {len(audio)/sr:.1f}s @ {sr}Hz")
        return enhanced

    except ImportError:
        logger.warning("noisereduce غير مثبَّت — سيتخطى تنقية الصوت (pip install noisereduce)")
        return audio_bytes
    except Exception as e:
        logger.warning(f"Audio enhancement failed ({e}) — using original sample")
        return audio_bytes


# ── Layer 3: Arabic Prosody Engine ────────────────────────────────────────────
# يُقسّم النص لمجموعات نَفَس ويُحدد نبرة كل مقطع حسب السياق التعليمي

@dataclass
class Chunk:
    text: str
    pause_after_ms: int = 350


CONTEXT_LEXICON: dict[str, list[str]] = {
    "encouragement": [
        "أحسنت", "ممتاز", "رائع", "صحيح تماماً", "هذا صحيح",
        "أنت على صواب", "جيد جداً", "بارك الله", "سؤال ممتاز",
        "إجابة رائعة", "تفكير ممتاز", "عظيم", "بالضبط", "نعم بالضبط",
    ],
    "correction": [
        "في الواقع", "الصواب هو", "لا، بل", "ليس كذلك",
        "دعنا نصحح", "للتصحيح", "تنبيه", "خطأ شائع", "يُخطئ كثيرون",
        "الفرق هو", "لكن الحقيقة",
    ],
    "explanation": [
        "دعني أشرح", "يعني أن", "بمعنى", "لاحظ أن",
        "انتبه إلى", "تذكر أن", "على سبيل المثال", "مثلاً",
        "بشكل عام", "من المهم أن نفهم", "الفكرة الأساسية",
        "يمكن توضيح", "بعبارة أخرى", "السبب في ذلك",
    ],
    "summary": [
        "إذاً", "باختصار", "خلاصة القول",
        "في المجمل", "النقاط الرئيسية", "مما سبق",
        "وبالتالي", "الخلاصة", "في النهاية",
    ],
}

# rate/pitch/volume مُمرَّران مباشرةً لـ edge_tts.Communicate
PROSODY_PROFILES: dict[str, dict[str, str]] = {
    "encouragement": {"rate": "+15%", "pitch": "+35Hz", "volume": "+5%"},
    "explanation":   {"rate": "-12%", "pitch": "+0Hz",  "volume": "+0%"},
    "correction":    {"rate": "-18%", "pitch": "-20Hz", "volume": "+2%"},
    "summary":       {"rate": "-5%",  "pitch": "-10Hz", "volume": "-2%"},
    "neutral":       {"rate": "+0%",  "pitch": "+0Hz",  "volume": "+0%"},
}

# الأصوات العربية لكل لهجة: (أنثى, ذكر)
DIALECT_VOICES: dict[str, tuple[str, str]] = {
    "ar-SA": ("ar-SA-ZariyahNeural", "ar-SA-HamedNeural"),
    "ar-EG": ("ar-EG-SalmaNeural",   "ar-EG-ShakirNeural"),
    "ar-AE": ("ar-AE-FatimaNeural",  "ar-AE-HamdanNeural"),
    "ar-IQ": ("ar-IQ-RanaNeural",    "ar-IQ-BasselNeural"),
    "ar-JO": ("ar-JO-SanaNeural",    "ar-JO-TaimNeural"),
    "ar-LB": ("ar-LB-LaylaNeural",   "ar-LB-RamiNeural"),
    "ar-MA": ("ar-MA-MounaNeural",   "ar-MA-JamalNeural"),
    "ar-KW": ("ar-KW-NouraNeural",   "ar-KW-FahedNeural"),
    "ar-QA": ("ar-QA-AmalNeural",    "ar-QA-MoazNeural"),
    "ar-SY": ("ar-SY-AmanyNeural",   "ar-SY-LaithNeural"),
    "ar-BH": ("ar-BH-LailaNeural",   "ar-BH-AliNeural"),
    "ar-TN": ("ar-TN-ReemNeural",    "ar-TN-HediNeural"),
    "ar-YE": ("ar-YE-MaryamNeural",  "ar-YE-SalehNeural"),
    "ar-DZ": ("ar-DZ-AminaNeural",   "ar-DZ-IsmaelNeural"),
    "ar-LY": ("ar-LY-ImanNeural",    "ar-LY-OmarNeural"),
    "ar-OM": ("ar-OM-AyshaNeural",   "ar-OM-AbdullahNeural"),
}

# اللهجات الفصيحة التي يُفعَّل معها التشكيل (Mishkal)
MSA_DIALECTS: set[str] = {"ar-SA", "ar-QA", "ar-KW", "ar-BH", "ar-OM"}

_PAUSE_MAP: dict[str, int] = {
    "؟": 700, "?": 700,
    "!": 600,
    ".": 600,
    "،": 280,
    "؛": 420, ";": 420,
    ":": 300,
}

_TRANSITION_WORDS = [
    "ثم ", "إذاً", "وعليه", "بناءً على ذلك",
    "من ناحية أخرى", "بالإضافة إلى ذلك",
    "خلاصة القول", "باختصار",
]

_SPLIT_CONJUNCTIONS = {"و", "أو", "لكن", "بل", "حتى"}

# Mishkal instance مُخزَّن لتجنّب إعادة تحميل المعجم في كل طلب
_mishkal_instance = None


def _apply_tashkeel(text: str) -> str:
    """يُشكّل النص العربي الفصيح باستخدام Mishkal (إن كان مثبَّتاً)."""
    global _mishkal_instance
    try:
        if _mishkal_instance is None:
            from mishkal.tashkeel import TashkeelClass
            _mishkal_instance = TashkeelClass()
        return _mishkal_instance.tashkeel(text)
    except ImportError:
        return text
    except Exception as e:
        logger.debug(f"Mishkal tashkeel failed: {e}")
        return text


def classify_context(text: str) -> str:
    """يُحدد السياق التعليمي للنص بمطابقة معجم الكلمات المفتاحية."""
    for context in ["encouragement", "correction", "explanation", "summary"]:
        for kw in CONTEXT_LEXICON[context]:
            if kw in text:
                return context
    return "neutral"


def split_breath_groups(text: str) -> list[Chunk]:
    """
    يُقسّم النص لمجموعات نَفَس طبيعية.
    كل مقطع يحمل النص ومدة التوقف الذي يعقبه بالميلي ثانية.
    """
    if not text.strip():
        return []

    chunks: list[Chunk] = []
    current = ""

    for ch in text:
        current += ch
        if ch in _PAUSE_MAP:
            segment = current.strip()
            # تجاهل المقاطع التي لا تحتوي إلا على علامة الترقيم نفسها
            if segment and segment not in ".،؛؟!:?;":
                chunks.extend(_finalize_segment(segment, _PAUSE_MAP[ch]))
            current = ""

    if current.strip():
        chunks.extend(_finalize_segment(current.strip(), 400))

    return [c for c in chunks if c.text.strip()]


def _finalize_segment(text: str, pause_ms: int) -> list[Chunk]:
    """يُعالج مقطعاً: يُدرج توقفات انتقالية ويكسر الجمل الطويلة."""
    for word in _TRANSITION_WORDS:
        if word in text:
            parts = text.split(word, 1)
            before = parts[0].strip()
            after  = (word + parts[1]).strip()
            result = []
            if before:
                result.extend(_split_long_segment(before, 350))
            result.extend(_split_long_segment(after, pause_ms))
            return result
    return _split_long_segment(text, pause_ms)


def _split_long_segment(text: str, pause_ms: int) -> list[Chunk]:
    """يكسر المقطع إن تجاوز 12 كلمة، عند أقرب حرف عطف."""
    words = text.split()
    if len(words) <= 12:
        return [Chunk(text=text, pause_after_ms=pause_ms)]

    split_idx = 8
    for idx in range(min(10, len(words) - 1), 5, -1):
        if words[idx] in _SPLIT_CONJUNCTIONS:
            split_idx = idx
            break

    part1 = " ".join(words[:split_idx]).strip()
    part2 = " ".join(words[split_idx:]).strip()
    result = [Chunk(text=part1, pause_after_ms=280)]
    if part2:
        result.append(Chunk(text=part2, pause_after_ms=pause_ms))
    return result


def _generate_silence_wav(ms: int, sr: int = 22050) -> bytes:
    """يُولّد ملف WAV صامت بمدة محددة بالميلي ثانية."""
    try:
        import numpy as np
        import soundfile as sf

        samples = int(sr * ms / 1000)
        silence = np.zeros(samples, dtype="float32")
        buf = io.BytesIO()
        sf.write(buf, silence, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"generate_silence failed ({e})")
        return b""


def _ffmpeg_concat_wavs(wav_parts: list[bytes]) -> bytes:
    """يدمج قائمة WAV bytes في ملف واحد باستخدام ffmpeg concat demuxer."""
    non_empty = [w for w in wav_parts if w]
    if not non_empty:
        raise ValueError("لا توجد مقاطع صوتية للدمج")
    if len(non_empty) == 1:
        return non_empty[0]

    import shutil
    tmp_dir = tempfile.mkdtemp()
    try:
        file_paths: list[str] = []
        for i, wav in enumerate(non_empty):
            p = os.path.join(tmp_dir, f"part_{i:04d}.wav")
            with open(p, "wb") as f:
                f.write(wav)
            file_paths.append(p)

        list_file = os.path.join(tmp_dir, "concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in file_paths:
                f.write(f"file '{p}'\n")

        out_path = os.path.join(tmp_dir, "output.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-ar", "22050", "-ac", "1", out_path],
            capture_output=True, timeout=60, check=True
        )
        return Path(out_path).read_bytes()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg concat فشل: {e.stderr.decode()[:300]}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Layer 4: Speed Control ────────────────────────────────────────────────────

def apply_speed_control(audio_bytes: bytes, speed: float) -> bytes:
    """
    Layer 4 — تغيير سرعة الكلام مع الحفاظ على الـ pitch:
    speed: 0.5 = نصف السرعة | 1.0 = طبيعي | 2.0 = ضعف السرعة

    يستخدم librosa.effects.time_stretch (STFT-based, pitch-preserving).
    يُرجع الـ bytes الأصلية إن فشل.
    """
    if abs(speed - 1.0) < 0.01:
        return audio_bytes

    try:
        import numpy as np
        import soundfile as sf
        import librosa

        with io.BytesIO(audio_bytes) as buf:
            audio, sr = sf.read(buf, dtype="float32", always_2d=False)

        # time_stretch: rate > 1 = أسرع، rate < 1 = أبطأ
        audio_stretched = librosa.effects.time_stretch(audio, rate=speed)

        out = io.BytesIO()
        sf.write(out, audio_stretched, sr, format="WAV", subtype="PCM_16")
        logger.info(f"Speed control applied: {speed}x")
        return out.getvalue()

    except ImportError:
        logger.warning("librosa غير مثبَّت — سيتخطى التحكم في السرعة (pip install librosa)")
        return audio_bytes
    except Exception as e:
        logger.warning(f"Speed control failed ({e}) — using original speed")
        return audio_bytes


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


# ── Edge-TTS (Microsoft Neural TTS) ──────────────────────────────────────────
# أفضل جودة نطق عربي/إنجليزي — يعمل عبر الإنترنت بدون GPU

EDGE_TTS_VOICES = {
    "ar": "ar-SA-ZariyahNeural",    # عربية سعودية أنثى — نطق طبيعي جداً
    "ar-male": "ar-SA-HamedNeural", # عربية سعودية ذكر
    "en": "en-US-JennyNeural",      # إنجليزية أمريكية أنثى
    "en-male": "en-US-GuyNeural",   # إنجليزية أمريكية ذكر
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "tr": "tr-TR-EmelNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
}


def _speed_to_edge_rate(speed: float) -> str:
    """
    يحوّل speed float إلى Edge-TTS SSML prosody rate string.
    1.0 → "+0%"   (طبيعي)
    0.75 → "-25%" (أبطأ)
    1.5  → "+50%" (أسرع)
    """
    pct = int(round((speed - 1.0) * 100))
    return f"{pct:+d}%"


def _edge_tts_single_call(
    text: str,
    voice: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> bytes:
    """
    نداء واحد لـ Edge-TTS بـ prosody كامل — يُرجع WAV bytes.
    دالة داخلية تُستدعى من synthesize_with_edge_tts وsynthesize_human_voice.
    تعمل في thread منفصل لتجنّب تعارض event loop مع FastAPI.
    """
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts غير مثبَّت — pip install edge-tts")

    mp3_chunks: list[bytes] = []
    exc: list[Exception] = []

    def _run() -> None:
        async def _stream() -> None:
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    mp3_chunks.append(chunk["data"])
        try:
            import asyncio
            asyncio.run(_stream())
        except Exception as e:
            exc.append(e)

    import threading
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=90)

    if exc:
        raise RuntimeError(f"Edge-TTS فشل: {exc[0]}")
    if not mp3_chunks:
        raise RuntimeError("Edge-TTS: لم يُرجع أي بيانات صوتية")

    mp3_bytes = b"".join(mp3_chunks)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_bytes)
        mp3_path = f.name

    wav_path = mp3_path.replace(".mp3", ".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path,
             "-ar", "22050", "-ac", "1", wav_path],
            capture_output=True, timeout=30, check=True
        )
        logger.info(f"Edge-TTS call: voice={voice}, rate={rate}, pitch={pitch}")
        return Path(wav_path).read_bytes()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg فشل في التحويل: {e.stderr.decode()[:200]}")
    finally:
        for p in [mp3_path, wav_path]:
            if os.path.exists(p):
                os.unlink(p)


def synthesize_with_edge_tts(
    text: str,
    language: str = "ar",
    voice_id: Optional[str] = None,
    speed: float = 1.0,
) -> bytes:
    """
    يُولّد صوتاً باستخدام Edge-TTS بدون prosody متقدم.
    للاستخدام المباشر أو الـ fallback — يُفضَّل synthesize_human_voice للعربية.
    """
    voice = voice_id or EDGE_TTS_VOICES.get(language, EDGE_TTS_VOICES["en"])
    rate  = _speed_to_edge_rate(speed)
    return _edge_tts_single_call(text, voice, rate=rate)


def synthesize_human_voice(
    text: str,
    language: str = "ar",
    dialect: str = "ar-SA",
    gender: str = "female",
    voice_id: Optional[str] = None,
    speed: float = 1.0,
) -> bytes:
    """
    يُولّد صوتاً بشرياً طبيعياً — استدعاء Edge-TTS واحد للنص كاملاً.
    Edge-TTS يُدير التوقفات عند علامات الترقيم تلقائياً بدون تقطيع أو stitching.
    """
    # 1. تشكيل للهجات الفصيحة
    if dialect in MSA_DIALECTS:
        text = _apply_tashkeel(text)

    # 2. تصنيف السياق العام وتحديد نبرة موحَّدة للنص كله
    context = classify_context(text)
    profile = PROSODY_PROFILES[context]

    # 3. Rate مُركَّب: نبرة السياق + سرعة المستخدم
    speed_offset = int(round((speed - 1.0) * 100))
    try:
        p_rate = int(profile["rate"].replace("%", ""))
    except ValueError:
        p_rate = 0
    combined_rate = f"{p_rate + speed_offset:+d}%"

    # 4. اختيار الصوت
    if voice_id:
        voice = voice_id
    elif dialect in DIALECT_VOICES:
        voice = DIALECT_VOICES[dialect][0 if gender == "female" else 1]
    else:
        voice = EDGE_TTS_VOICES.get(language, EDGE_TTS_VOICES["en"])

    logger.info(f"Human voice: voice={voice}, context={context}, rate={combined_rate}, dialect={dialect}")

    # 5. استدعاء واحد بالنص الكامل — لا تقطيع ولا stitching ولا artifacts
    return _edge_tts_single_call(text, voice, rate=combined_rate, pitch=profile["pitch"], volume=profile["volume"])


# ── Habibi-TTS: Voice Cloning (Primary) ──────────────────────────────────────
# F5-TTS fine-tuned على 12 لهجة عربية — يعمل محلياً على RTX 4060

# خريطة من locale-code الخاص بنا إلى Dialect ID في Habibi-TTS
HABIBI_DIALECT_MAP: dict[str, str] = {
    "ar-SA": "SAU",   # السعودية
    "ar-AE": "UAE",   # الإمارات
    "ar-EG": "EGY",   # مصر
    "ar-IQ": "IRQ",   # العراق
    "ar-MA": "MAR",   # المغرب
    "ar-OM": "OMN",   # عُمان
    "ar-TN": "TUN",   # تونس
    "ar-LY": "LBY",   # ليبيا
    "ar-DZ": "ALG",   # الجزائر
    "ar-JO": "LEV",   # الأردن → Levantine
    "ar-LB": "LEV",   # لبنان → Levantine
    "ar-SY": "LEV",   # سوريا → Levantine
    "ar-KW": "MSA",   # الكويت → فصحى
    "ar-QA": "MSA",   # قطر → فصحى
    "ar-BH": "MSA",   # البحرين → فصحى
    "ar-YE": "MSA",   # اليمن → فصحى
}


def _habibi_get_ref_text(ref_audio_bytes: bytes) -> str:
    """يستخرج نص الصوت المرجعي باستخدام Whisper المثبَّت مسبقاً."""
    try:
        result = transcribe_audio(ref_audio_bytes, language="ar")
        text = result.get("text", "").strip()
        logger.info(f"Habibi ref transcription: '{text[:60]}'")
        return text
    except Exception as e:
        logger.warning(f"Habibi ref transcription failed ({e}) — using empty ref_text")
        return ""


def synthesize_with_habibi(
    text: str,
    ref_audio_bytes: bytes,
    dialect: str = "ar-SA",
) -> bytes:
    """
    يُولّد صوتاً بصوت المحاضر باستخدام Habibi-TTS.
    يتطلب: pip install habibi-tts (مثبَّت في requirements-voice.txt).
    عند الاستدعاء الأول يُحمَّل النموذج من HuggingFace (~2GB — مرة واحدة فقط).

    Args:
        text:           النص المراد توليده بصوت المحاضر.
        ref_audio_bytes: عينة صوت المحاضر (WAV — معالجة مسبقاً بـ Layer 2).
        dialect:        locale code مثل 'ar-SA' — يُحوَّل لـ Habibi dialect ID.

    Returns:
        WAV bytes للصوت المُولَّد.
    """
    import shutil

    habibi_dialect = HABIBI_DIALECT_MAP.get(dialect, "MSA")

    # 1. استخراج نص الصوت المرجعي بـ Whisper
    ref_text = _habibi_get_ref_text(ref_audio_bytes)

    tmp_dir = tempfile.mkdtemp(prefix="habibi_")
    try:
        # 2. كتابة الصوت المرجعي لملف مؤقت
        ref_path = os.path.join(tmp_dir, "ref.wav")
        with open(ref_path, "wb") as f:
            f.write(ref_audio_bytes)

        out_dir = os.path.join(tmp_dir, "out")
        os.makedirs(out_dir, exist_ok=True)

        # 3. استدعاء Habibi-TTS CLI
        cmd = [
            "habibi-tts_infer-cli",
            "--ref_audio",  ref_path,
            "--ref_text",   ref_text,
            "--gen_text",   text,
            "--dialect",    habibi_dialect,
            "--output_dir", out_dir,
            "--nfe_step",   "32",       # جودة معقولة + سرعة
            "--speed",      "1.0",
        ]
        logger.info(f"Habibi-TTS: dialect={habibi_dialect}, ref_text='{ref_text[:40]}'")

        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=600,    # أول تشغيل يُحمِّل النموذج (~500MB) — نعطيه 10 دقائق
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"habibi-tts_infer-cli فشل (code {result.returncode}): "
                f"{result.stderr[-400:]}"
            )

        # 4. قراءة ملف الناتج (أحدث WAV في مجلد الخرج)
        wav_files = sorted(
            [f for f in os.listdir(out_dir) if f.endswith(".wav")],
            key=lambda f: os.path.getmtime(os.path.join(out_dir, f))
        )
        if not wav_files:
            raise RuntimeError("Habibi-TTS: لم يُنتج أي ملف WAV في مجلد الخرج")

        out_path = os.path.join(out_dir, wav_files[-1])
        audio_bytes = Path(out_path).read_bytes()
        logger.info(f"✅ Habibi-TTS done: {len(audio_bytes)//1024}KB, dialect={habibi_dialect}")
        return audio_bytes

    except FileNotFoundError:
        raise RuntimeError(
            "habibi-tts_infer-cli غير موجود — ثبّته بـ: pip install habibi-tts"
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def warmup_habibi() -> dict:
    """
    يُشغّل inference قصير لـ Habibi-TTS لتحميل النموذج في CUDA cache.
    يُستدعى من endpoint /warmup (يعمل داخل thread pool executor).
    يُرجع dict بالوقت المستغرق أو رسالة خطأ واضحة.
    """
    import shutil, time

    # تحقق أولاً من وجود habibi-tts_infer-cli قبل أي عملية
    if not shutil.which("habibi-tts_infer-cli"):
        return {
            "status": "error",
            "error": "habibi-tts_infer-cli غير موجود — أعد بناء Docker image بـ VOICE_INSTALL=true",
        }

    warmup_text    = "مرحبا"
    warmup_dialect = "ar-SA"

    # نُولّد ملف صوتي مرجعي بـ Edge-TTS عبر _edge_tts_single_call (thread-safe)
    try:
        ref_bytes = _edge_tts_single_call(warmup_text, "ar-SA-ZariyahNeural")
    except Exception as e:
        return {"status": "error", "error": f"فشل توليد الصوت المرجعي (Edge-TTS): {e}"}

    t0 = time.time()
    try:
        synthesize_with_habibi(warmup_text, ref_bytes, warmup_dialect)
        elapsed = round(time.time() - t0, 1)
        logger.info(f"✅ Habibi warmup done in {elapsed}s")
        return {"status": "ready", "warmup_time_s": elapsed}
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        logger.warning(f"Habibi warmup failed in {elapsed}s: {e}")
        return {"status": "error", "error": str(e)[:300], "elapsed_s": elapsed}


# ── TTS: XTTS-v2 ──────────────────────────────────────────────────────────────

_XTTS_FALLBACK_SPEAKER = "Claribel Dervla"   # متحدث افتراضي مضمون في XTTS-v2


def _get_xtts_default_speaker(tts) -> str:
    """يجلب أول متحدث متاح في XTTS-v2 أو يُرجع الافتراضي."""
    try:
        # المسار الأول: TTS.speakers (قد يكون None في بعض الإصدارات)
        if tts.speakers:
            return tts.speakers[0]
        # المسار الثاني: عبر speaker_manager الداخلي
        mgr = tts.synthesizer.tts_model.speaker_manager
        names = list(mgr.name_to_id.keys())
        if names:
            return names[0]
    except Exception:
        pass
    return _XTTS_FALLBACK_SPEAKER


def synthesize_speech(
    text: str,
    language: str = "ar",
    dialect: str = "ar-SA",
    gender: str = "female",
    speaker_wav_bytes: Optional[bytes] = None,
    speaker_name: Optional[str] = None,
    voice_id: Optional[str] = None,
    speed: float = 1.0,
) -> bytes:
    """
    تحويل نص → صوت بشري طبيعي.
    - بدون عينة → synthesize_human_voice (prosody engine + breath groups + ffmpeg stitch).
    - مع عينة → Layer2 (Audio Enhancer) → XTTS-v2 (Voice Cloning) + Layer4 (Speed).

    Args:
        dialect: لهجة عربية مثل 'ar-SA' أو 'ar-EG' — انظر DIALECT_VOICES للقائمة الكاملة.
        gender:  'female' أو 'male'.
        speed:   0.5 (بطيء) → 1.0 (طبيعي) → 2.0 (سريع).

    Returns:
        bytes (WAV audio)
    """
    if not speaker_wav_bytes:
        return synthesize_human_voice(
            text=text,
            language=language,
            dialect=dialect,
            gender=gender,
            voice_id=voice_id,
            speed=speed,
        )

    # Layer 2: تنقية عينة الصوت المرجعية
    enhanced_sample = enhance_voice_sample(speaker_wav_bytes)

    # محاولة Habibi-TTS أولاً (جودة أعلى للعربية)
    is_arabic = language.startswith("ar")
    if is_arabic:
        try:
            audio = synthesize_with_habibi(
                text=text,
                ref_audio_bytes=enhanced_sample,
                dialect=dialect,
            )
            return apply_speed_control(audio, speed)
        except Exception as e:
            logger.warning(f"Habibi-TTS failed ({e}) — falling back to XTTS-v2")

    # Fallback: XTTS-v2
    processed_text = preprocess_arabic_for_tts(text, use_phonemes=False) if is_arabic else text
    tts, _ = _get_xtts()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_file:
        out_path = out_file.name

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as spk_file:
            spk_file.write(enhanced_sample)
            spk_path = spk_file.name

        try:
            tts.tts_to_file(
                text=processed_text,
                file_path=out_path,
                speaker_wav=spk_path,
                language=language,
                split_sentences=True,
            )
        finally:
            os.unlink(spk_path)

        audio = Path(out_path).read_bytes()
        return apply_speed_control(audio, speed)

    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


# ── Voice Clone Storage ───────────────────────────────────────────────────────

def _safe_specialist_filename(specialist_name: str) -> str:
    """يضمن أن اسم النموذج لا يحتوي على مسارات متنقلة (path traversal)."""
    return Path(specialist_name).name.replace("..", "").strip() or "unknown"


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

    safe_name = _safe_specialist_filename(specialist_name)
    sample_path = samples_dir / f"{safe_name}.wav"

    if not sample_path.resolve().is_relative_to(samples_dir.resolve()):
        raise ValueError(f"اسم النموذج غير صالح: {specialist_name}")

    sample_path.write_bytes(audio_bytes)

    logger.info(f"✅ Voice sample saved for {safe_name}")
    return str(sample_path)


def get_voice_sample(specialist_name: str) -> Optional[bytes]:
    """يُرجع عينة الصوت المحفوظة للنموذج المتخصص"""
    safe_name = _safe_specialist_filename(specialist_name)
    sample_path = Path(settings.XTTS_MODEL_PATH) / "voice_samples" / f"{safe_name}.wav"
    if sample_path.exists():
        return sample_path.read_bytes()
    return None


# نتيجة is_voice_ready مخزّنة — TTS import بطيء (~30s) لذا نتحقق مرة واحدة فقط
_voice_ready_cache: Optional[dict] = None


def reset_voice_cache() -> None:
    """يُلغي الـ cache حتى يُعاد الفحص في المرة القادمة (بعد تثبيت مكتبات جديدة)."""
    global _voice_ready_cache
    _voice_ready_cache = None


def _check_edge_tts_connectivity() -> bool:
    """يتحقق من إمكانية الوصول لخوادم Edge-TTS (Microsoft)."""
    import socket
    try:
        socket.setdefaulttimeout(5)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(
            ("speech.platform.bing.com", 443)
        )
        return True
    except Exception:
        return False


def is_voice_ready() -> dict:
    """
    يتحقق من جاهزية نموذج الصوت — فحص حقيقي لكل مكوّن.
    النتيجة مُخزَّنة في cache لأن الفحص الأول يستغرق عدة ثوانٍ.
    """
    global _voice_ready_cache
    if _voice_ready_cache is not None:
        return _voice_ready_cache

    import shutil

    whisper_pkg   = False
    xtts_pkg      = False
    habibi_cli    = bool(shutil.which("habibi-tts_infer-cli"))
    edge_online   = _check_edge_tts_connectivity()
    issues: list[str] = []

    try:
        import whisper  # noqa
        whisper_pkg = True
    except ImportError:
        issues.append("Whisper غير مثبَّت")

    try:
        from TTS.api import TTS  # noqa
        xtts_pkg = True
    except (ImportError, Exception):
        issues.append("XTTS-v2 غير مثبَّت")

    if not habibi_cli:
        issues.append("habibi-tts غير مثبَّت (clone test لن يعمل)")

    if not edge_online:
        issues.append("Edge-TTS: لا يوجد وصول لشبكة Microsoft (TTS الأساسي لن يعمل)")

    # الوضع الفعلي: TTS الأساسي هو Edge-TTS وليس XTTS-v2
    tts_functional = edge_online   # Edge-TTS هو المسار الفعلي للتوليد
    stt_functional = whisper_pkg   # Whisper للتحويل الصوتي

    if tts_functional and stt_functional and not issues:
        message = "✅ جاهز تماماً — Edge-TTS + Whisper + Habibi-TTS"
    elif tts_functional and stt_functional:
        message = f"⚠️ يعمل جزئياً — {' | '.join(issues)}"
    elif tts_functional and not stt_functional:
        message = "⚠️ TTS يعمل لكن STT (Whisper) غير متاح"
    elif not tts_functional and stt_functional:
        message = "❌ Edge-TTS لا يصل للإنترنت — توليد الصوت لن يعمل"
    else:
        message = f"❌ غير جاهز — {' | '.join(issues)}"

    status = {
        "whisper_available":   whisper_pkg,
        "xtts_available":      xtts_pkg,
        "habibi_available":    habibi_cli,
        "edge_tts_online":     edge_online,
        "cuda_available":      _has_cuda(),
        "whisper_model":       _get_whisper_model_name(),
        "tts_functional":      tts_functional,
        "stt_functional":      stt_functional,
        "message":             message,
        "issues":              issues,
    }

    _voice_ready_cache = status
    return status
