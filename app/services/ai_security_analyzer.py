"""
AI Security Analyzer — يحلل أنماط الأحداث الأمنية ويتخذ قرارات حجب ذكية.

يعمل في background thread كل 5 دقائق.
يستخدم Ollama (نموذج محلي) لتحليل الأنماط غير الواضحة للـ rule-based.
القرارات تُحفظ بحالة "pending" — الأدمن يراجعها ويقرر التأكيد أو الإلغاء.
"""
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("yesarha.ai_security")

_ANALYSIS_INTERVAL = 300   # 5 دقائق
_LOOKBACK_MINUTES  = 15    # تحليل آخر 15 دقيقة
_MIN_EVENTS        = 3     # لا تحليل إذا أقل من 3 أحداث

_running = False
_thread: Optional[threading.Thread] = None

# ── System prompt للنموذج ─────────────────────────────────────────────────────

_SYSTEM_PROMPT = """أنت محلل أمني متخصص في كشف التهديدات السيبرانية.
مهمتك: تحليل أحداث أمنية وتحديد ما إذا كانت تشكّل تهديداً حقيقياً يستوجب الحجب.

قواعد صارمة:
1. ردّك يجب أن يكون JSON فقط بدون أي نص خارجه
2. لا تحجب إلا إذا كنت متأكداً — الحجب الخاطئ يؤثر على المستخدمين الشرعيين
3. اعتبر أن الطلبات تأتي من باك إند العملاء (ليس مستخدمين مباشرين)

شكل الرد:
{
  "threat_detected": true/false,
  "threat_level": "low|medium|high|critical",
  "ips_to_block": [
    {
      "ip": "x.x.x.x",
      "reasoning": "سبب الحجب بالعربي في جملة واحدة واضحة",
      "severity": "medium|high|critical"
    }
  ],
  "summary": "ملخص التحليل بالعربي في جملة أو جملتين"
}

إذا لا يوجد تهديد: {"threat_detected": false, "ips_to_block": [], "summary": "لا تهديدات"}"""


# ── بناء تقرير الأحداث للنموذج ───────────────────────────────────────────────

def _build_events_report(events: list) -> str:
    """يحوّل قائمة الأحداث لتقرير نصي مضغوط للنموذج."""
    by_ip: dict = defaultdict(list)
    for ev in events:
        by_ip[ev.ip].append({
            "type": ev.event_type,
            "path": ev.path or "",
            "severity": ev.severity,
            "time": ev.created_at.strftime("%H:%M:%S") if ev.created_at else "",
        })

    lines = [f"أحداث الـ {_LOOKBACK_MINUTES} دقيقة الأخيرة ({len(events)} حدث من {len(by_ip)} IP):"]
    for ip, evs in sorted(by_ip.items(), key=lambda x: -len(x[1])):
        types = ", ".join(set(e["type"] for e in evs))
        paths = list({e["path"] for e in evs if e["path"]})[:3]
        lines.append(f"• {ip}: {len(evs)} حدث | أنواع: {types} | مسارات: {paths}")

    return "\n".join(lines)


# ── التحليل الفعلي ────────────────────────────────────────────────────────────

def _analyze_once() -> None:
    """دورة تحليل واحدة — تُستدعى كل 5 دقائق."""
    try:
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            _run_analysis(db)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[AI Security] Analysis cycle failed: {e}")


def _run_analysis(db) -> None:
    from app.models.security import SecurityEvent, BlockedIP
    from app.services.security_service import block_ip_ai, is_ip_trusted, is_ip_blocked_fast
    from app.services.runtime_config import runtime_cfg

    cutoff = datetime.utcnow() - timedelta(minutes=_LOOKBACK_MINUTES)
    events = db.query(SecurityEvent).filter(
        SecurityEvent.created_at >= cutoff
    ).order_by(SecurityEvent.created_at).all()

    if len(events) < _MIN_EVENTS:
        return

    # تجاهل الأحداث من IPs مسبقة الحجب أو موثوقة
    already_blocked = {
        b.ip for b in db.query(BlockedIP).filter(BlockedIP.is_active == True).all()
    }
    fresh_events = [e for e in events if e.ip not in already_blocked and not is_ip_trusted(e.ip)]

    if len(fresh_events) < _MIN_EVENTS:
        return

    report = _build_events_report(fresh_events)

    try:
        from app.services.ollama_client import OllamaClient
        client = OllamaClient()
        model = runtime_cfg.get_core_model()

        result = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": report},
            ],
            options={"temperature": 0.1, "num_predict": 512},
            think=False,
            timeout=60,
        )

        raw = result.get("content", "").strip()
        # استخراج JSON من الرد
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            logger.warning("[AI Security] No JSON in response")
            return

        data = json.loads(raw[start:end])

        if not data.get("threat_detected"):
            logger.info(f"[AI Security] No threat: {data.get('summary', '')}")
            return

        summary = data.get("summary", "")
        logger.warning(f"[AI Security] Threat detected: {summary}")

        for entry in data.get("ips_to_block", []):
            ip        = str(entry.get("ip", "")).strip()
            reasoning = str(entry.get("reasoning", "تهديد مكتشف من AI")).strip()
            severity  = entry.get("severity", "high")
            if not ip:
                continue
            if is_ip_trusted(ip) or is_ip_blocked_fast(ip):
                continue
            # سجّل حدث AI قبل الحجب
            from app.services.security_service import log_event
            log_event(db, ip, "ai_detected",
                      details=f"[AI] {reasoning} | {summary[:100]}",
                      auto_blocked=True)
            block_ip_ai(db, ip, reasoning, severity)

    except json.JSONDecodeError as e:
        logger.warning(f"[AI Security] JSON parse error: {e}")
    except Exception as e:
        logger.error(f"[AI Security] Ollama call failed: {e}")


# ── Background thread ─────────────────────────────────────────────────────────

def _loop() -> None:
    global _running
    logger.info("[AI Security] Analyzer started — interval: 5 min")
    while _running:
        try:
            _analyze_once()
        except Exception as e:
            logger.error(f"[AI Security] Loop error: {e}")
        # انتظر الفترة القادمة أو حتى إيقاف التشغيل
        for _ in range(_ANALYSIS_INTERVAL):
            if not _running:
                break
            time.sleep(1)
    logger.info("[AI Security] Analyzer stopped")


def start_ai_analyzer() -> None:
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True, name="ai-security-analyzer")
    _thread.start()


def stop_ai_analyzer() -> None:
    global _running
    _running = False
