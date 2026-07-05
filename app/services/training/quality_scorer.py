"""
Quality Scorer — Core يُقيِّم جودة ردود النماذج المتخصصة

يعمل في دورة المراقبة (كل 6 ساعات):
  1. يجلب ModelPerformanceLogs التي لم تُقيَّم بعد (quality_score IS NULL)
  2. يُرسل كل رد لـ Core Model ليُقيِّمه (0.0 – 1.0)
  3. يُحدِّث quality_score + issues_detected + improvement_notes

لا يحجب ردود المستخدمين — يعمل فقط في الخلفية.
"""
import json
import logging
import re
from sqlalchemy.orm import Session

from app.models.specialist import ModelPerformanceLog, SpecialistModel
from app.services.ollama_client import OllamaClient
from app.services.runtime_config import runtime_cfg

log = logging.getLogger("yesarha.quality_scorer")

_SCORE_SYSTEM = (
    "أنت مقيِّم جودة ردود AI. مهمتك تقييم رد نموذج متخصص وإرجاع JSON فقط، "
    "بدون أي نص إضافي."
)

_SCORE_TEMPLATE = """\
قيِّم الرد التالي لنموذج {specialization}.

السؤال: {user_input}

الرد: {model_output}

أجب بـ JSON فقط (لا تضف أي نص آخر):
{{
  "score": <رقم 0.0 إلى 1.0 يمثل جودة الرد>,
  "issues": ["مشكلة إن وجدت", "..."],
  "notes": "ملاحظة مختصرة"
}}

معيار التقييم:
- 0.9-1.0: ممتاز — دقيق، واضح، شامل
- 0.7-0.9: جيد — يؤدي الغرض مع هفوات بسيطة
- 0.5-0.7: مقبول — ناقص أو غير دقيق جزئياً
- 0.0-0.5: ضعيف — مضلل أو فارغ أو خاطئ
"""


def _extract_json(text: str) -> dict | None:
    """يستخرج أول JSON object من النص حتى لو أحاط به نص إضافي"""
    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except Exception:
        return None


def score_one(
    user_input: str,
    model_output: str,
    specialization: str,
) -> dict:
    """
    يُقيِّم رداً واحداً — يُرجع dict مع score + issues + notes.
    يُرجع قيماً افتراضية إن فشل الاتصال بـ Core Model.
    """
    prompt = _SCORE_TEMPLATE.format(
        specialization=specialization,
        user_input=(user_input or "")[:400],
        model_output=(model_output or "")[:600],
    )
    try:
        client = OllamaClient()
        result = client.chat(
            model=runtime_cfg.get_core_model(),
            messages=[
                {"role": "system", "content": _SCORE_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            options={"temperature": 0.0, "num_predict": 256},
            timeout=20,
        )
        parsed = _extract_json(result.get("content", ""))
        if not parsed:
            return {"score": None, "issues": [], "notes": "parse_failed"}

        score = float(parsed.get("score", 0))
        score = max(0.0, min(1.0, score))  # clamp to [0, 1]
        issues = parsed.get("issues", [])
        if not isinstance(issues, list):
            issues = []

        return {
            "score":  round(score, 3),
            "issues": [str(i) for i in issues if i][:5],
            "notes":  str(parsed.get("notes", ""))[:300],
        }
    except Exception as e:
        log.debug(f"score_one failed: {e}")
        return {"score": None, "issues": [], "notes": f"error: {str(e)[:100]}"}


def score_pending_logs(db: Session, limit: int = 30) -> int:
    """
    يُقيِّم الـ logs التي لم تُقيَّم بعد.
    يُرجع عدد السجلات التي تم تقييمها.
    """
    # جلب logs نجحت ولم تُقيَّم بعد
    pending = (
        db.query(ModelPerformanceLog)
        .filter(
            ModelPerformanceLog.quality_score.is_(None),
            ModelPerformanceLog.status == "success",
            ModelPerformanceLog.user_input.isnot(None),
            ModelPerformanceLog.model_output.isnot(None),
        )
        .order_by(ModelPerformanceLog.created_at.desc())
        .limit(limit)
        .all()
    )

    if not pending:
        return 0

    # جلب تخصصات النماذج دفعة واحدة
    model_ids = {lg.model_id for lg in pending if lg.model_id}
    spec_map: dict[int, str] = {}
    if model_ids:
        specs = db.query(SpecialistModel.id, SpecialistModel.specialization).filter(
            SpecialistModel.id.in_(model_ids)
        ).all()
        spec_map = {s.id: s.specialization for s in specs}

    scored = 0
    for lg in pending:
        specialization = spec_map.get(lg.model_id or 0, lg.model_name or "general")
        result = score_one(
            user_input=lg.user_input or "",
            model_output=lg.model_output or "",
            specialization=specialization,
        )
        lg.quality_score     = result["score"]
        lg.issues_detected   = result["issues"]
        lg.improvement_notes = result["notes"]
        scored += 1

    try:
        db.commit()
        log.info(f"Quality scorer: scored {scored} logs")
    except Exception as e:
        db.rollback()
        log.error(f"Quality scorer commit failed: {e}")
        scored = 0

    return scored
