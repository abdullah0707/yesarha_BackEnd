"""
Mind Map Service — توليد خرائط ذهنية SVG من محتوى الدرس

الخطوات:
  1. LLM يستخرج هيكل JSON من الـ chunks
  2. Pure Python SVG renderer — لا dependencies إضافية
  3. حفظ SVG في /app/data/visual_cache/ مع TTL 24h
"""
import json
import logging
import math
import re
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("yesarha.mindmap")

_VISUAL_CACHE_DIR = Path("/app/data/visual_cache")
_TTL_SECONDS = 24 * 3600
_last_cleanup: float = 0.0
_CLEANUP_INTERVAL = 600

# ألوان الفروع (تعمل على الخلفية البيضاء)
_BRANCH_COLORS = ["#4A90D9", "#27AE60", "#E67E22", "#8E44AD", "#C0392B"]
_BRANCH_LIGHT   = ["#D6E9F8", "#D5F5E3", "#FDEBD0", "#E8DAEF", "#FADBD8"]

# ── LLM extraction ────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
اقرأ محتوى الدرس التالي وأنشئ هيكل خريطة ذهنية.
أجب بـ JSON فقط — بدون أي كلام آخر قبله أو بعده.

الصيغة المطلوبة:
{
  "title": "عنوان قصير للدرس (3-5 كلمات)",
  "branches": [
    {"label": "الفرع الأول", "points": ["نقطة 1", "نقطة 2"]},
    {"label": "الفرع الثاني", "points": ["نقطة 1", "نقطة 2"]}
  ]
}

القواعد:
- عدد الفروع: من 3 إلى 5
- عدد النقاط لكل فرع: من 2 إلى 3
- كل نقطة: 2 إلى 5 كلمات فقط
- كل فرع: كلمة أو كلمتان فقط
- استخدم لغة المحتوى (عربي أو إنجليزي)
- إذا كان المحتوى قصيراً أو فارغاً، استخدم عنوان الدرس فقط مع فروع عامة

محتوى الدرس:
"""


def _extract_structure(chunks: list[dict], content_title: str) -> dict:
    """يستخدم LLM لاستخراج هيكل الخريطة الذهنية من الـ chunks."""
    try:
        from app.services.ollama_client import OllamaClient
        from app.services.runtime_config import runtime_cfg

        text = "\n".join(
            c.get("content", "") or c.get("text", "")
            for c in (chunks[:6] if chunks else [])
        ).strip()

        if not text:
            text = content_title or "محتوى الدرس"

        # اقتصار على 2000 حرف لتجنب context overflow
        text = text[:2000]

        client = OllamaClient()
        result = client.chat(
            model=runtime_cfg.get_core_model(),
            messages=[{"role": "user", "content": _EXTRACT_PROMPT + text}],
            options={"temperature": 0.2, "num_predict": 600},
            think=False,
            timeout=30,
        )
        raw = result.get("content", "")

        # استخراج JSON — raw_decode يتعامل بشكل صحيح مع } داخل النصوص
        start = raw.find("{")
        if start >= 0:
            try:
                data, _ = json.JSONDecoder().raw_decode(raw, start)
                branches = data.get("branches", [])
                if "title" in data and isinstance(branches, list) and len(branches) > 0:
                    data["branches"] = branches[:5]
                    for b in data["branches"]:
                        b["points"] = b.get("points", [])[:3]
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

    except Exception as e:
        logger.warning(f"Mind map LLM extraction failed: {e}")

    # Fallback — هيكل بسيط من العنوان
    return {
        "title": content_title or "خريطة الدرس",
        "branches": [
            {"label": "المحاور الرئيسية", "points": ["انظر محتوى الدرس"]},
        ],
    }


# ── SVG Renderer ──────────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _wrap_text(text: str, max_chars: int = 18) -> list[str]:
    """يقسم النص إلى سطور لا تتجاوز max_chars."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines or [text]


def _text_block(x: float, y: float, lines: list[str],
                font_size: int, fill: str, anchor: str = "middle",
                line_height: int = 0) -> str:
    lh = line_height or int(font_size * 1.4)
    offset = -(len(lines) - 1) * lh / 2
    parts = []
    for i, line in enumerate(lines):
        dy = offset + i * lh
        parts.append(
            f'<text x="{x:.1f}" y="{y + dy:.1f}" '
            f'text-anchor="{anchor}" dominant-baseline="central" '
            f'font-size="{font_size}" fill="{fill}" '
            f'font-family="Tahoma, Arial, sans-serif" '
            f'direction="rtl" unicode-bidi="embed">'
            f'{_escape(line)}</text>'
        )
    return "\n".join(parts)


def render_svg(structure: dict) -> bytes:
    """يُولّد SVG bytes من هيكل الخريطة الذهنية."""
    W, H = 1000, 680
    CX, CY = W / 2, H / 2
    CENTER_RX, CENTER_RY = 110, 50
    BRANCH_R = 220          # مسافة الفروع عن المركز
    POINT_EXTRA = 115       # مسافة إضافية للنقاط عن الفرع

    title = structure.get("title", "الخريطة الذهنية")
    branches = structure.get("branches", [])
    n = max(len(branches), 1)

    parts: list[str] = []

    # ── خلفية ──
    parts.append(
        f'<rect width="{W}" height="{H}" rx="16" fill="#FAFAFA" stroke="#E0E0E0" stroke-width="1.5"/>'
    )

    # ── حواف خارجية جمالية ──
    parts.append(
        f'<rect x="8" y="8" width="{W-16}" height="{H-16}" rx="12" '
        f'fill="none" stroke="#D5D5D5" stroke-width="0.8" stroke-dasharray="6,4"/>'
    )

    for i, branch in enumerate(branches):
        color = _BRANCH_COLORS[i % len(_BRANCH_COLORS)]
        light = _BRANCH_LIGHT[i % len(_BRANCH_LIGHT)]

        # زاوية الفرع — نبدأ من الأعلى، موزَّعة بالتساوي
        angle_deg = (360 / n) * i - 90
        angle_rad = math.radians(angle_deg)

        bx = CX + BRANCH_R * math.cos(angle_rad)
        by = CY + BRANCH_R * math.sin(angle_rad)

        # ── خط الوصل: المركز → الفرع ──
        parts.append(
            f'<line x1="{CX:.1f}" y1="{CY:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
            f'stroke="{color}" stroke-width="2.5" stroke-opacity="0.6"/>'
        )

        # ── مستطيل الفرع ──
        label = (branch.get("label") or "").strip() or "—"
        label_lines = _wrap_text(label, 12)
        # 16px/حرف — يُراعي عرض الحروف العربية في Tahoma
        brect_w = max(130, len(max(label_lines, key=len)) * 16 + 24)
        brect_h = 36 + (len(label_lines) - 1) * 18

        parts.append(
            f'<rect x="{bx - brect_w/2:.1f}" y="{by - brect_h/2:.1f}" '
            f'width="{brect_w:.1f}" height="{brect_h:.1f}" rx="10" '
            f'fill="{color}" stroke="{color}" stroke-width="1.5"/>'
        )
        parts.append(_text_block(bx, by, label_lines, 13, "#FFFFFF"))

        # ── النقاط الفرعية ──
        points = branch.get("points", [])
        np_ = len(points)
        if np_ == 0:
            continue

        spread_deg = min(30, 20 + np_ * 5)
        for j, point in enumerate(points):
            if np_ == 1:
                p_angle_rad = angle_rad
            else:
                offset_deg = spread_deg * (j - (np_ - 1) / 2)
                p_angle_rad = math.radians(angle_deg + offset_deg)

            px = bx + POINT_EXTRA * math.cos(p_angle_rad)
            py = by + POINT_EXTRA * math.sin(p_angle_rad)

            # خط الفرع → النقطة
            parts.append(
                f'<line x1="{bx:.1f}" y1="{by:.1f}" x2="{px:.1f}" y2="{py:.1f}" '
                f'stroke="{color}" stroke-width="1.5" stroke-opacity="0.45" stroke-dasharray="4,3"/>'
            )

            # مستطيل النقطة
            point_text = (point or "").strip() or "—"
            p_lines = _wrap_text(point_text, 16)
            # 13px/حرف للنقاط الفرعية (خط أصغر)
            prect_w = max(110, len(max(p_lines, key=len)) * 13 + 20)
            prect_h = 28 + (len(p_lines) - 1) * 16

            parts.append(
                f'<rect x="{px - prect_w/2:.1f}" y="{py - prect_h/2:.1f}" '
                f'width="{prect_w:.1f}" height="{prect_h:.1f}" rx="8" '
                f'fill="{light}" stroke="{color}" stroke-width="1.2"/>'
            )
            parts.append(_text_block(px, py, p_lines, 11, "#2C3E50"))

    # ── العقدة المركزية (فوق كل شيء) ──
    title_lines = _wrap_text(title, 16)
    c_h = CENTER_RY * 2 + (len(title_lines) - 1) * 8

    parts.append(
        f'<ellipse cx="{CX:.1f}" cy="{CY:.1f}" rx="{CENTER_RX}" ry="{c_h/2:.1f}" '
        f'fill="#2C3E50" stroke="#1A252F" stroke-width="2"/>'
    )
    # هالة خفيفة
    parts.append(
        f'<ellipse cx="{CX:.1f}" cy="{CY:.1f}" rx="{CENTER_RX + 6}" ry="{c_h/2 + 6:.1f}" '
        f'fill="none" stroke="#2C3E50" stroke-width="1" stroke-opacity="0.3"/>'
    )
    parts.append(_text_block(CX, CY, title_lines, 15, "#FFFFFF", line_height=20))

    # ── تجميع SVG ──
    body = "\n".join(parts)
    svg = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="{_escape(title)}">\n'
        f'<title>{_escape(title)}</title>\n'
        f'{body}\n'
        f'</svg>'
    )
    return svg.encode("utf-8")


# ── File cache helpers ────────────────────────────────────────────────────────

def _cache_dir() -> Path:
    _VISUAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _VISUAL_CACHE_DIR


def _cleanup_expired() -> None:
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    cutoff = now - _TTL_SECONDS
    try:
        for f in _cache_dir().glob("*.svg"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def _save_svg(svg_bytes: bytes) -> str:
    _cleanup_expired()
    file_id = str(uuid.uuid4())
    (_cache_dir() / f"{file_id}.svg").write_bytes(svg_bytes)
    return file_id


# ── Public API ────────────────────────────────────────────────────────────────

def generate_mindmap(
    chunks: list[dict],
    content_title: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    يُولّد خريطة ذهنية SVG من محتوى الدرس.
    يُرجع (visual_url, file_id). (None, None) عند الفشل.
    """
    try:
        structure = _extract_structure(chunks, content_title)
        svg_bytes = render_svg(structure)
        file_id = _save_svg(svg_bytes)
        url = f"/api/v1/specialist/pipeline/visual/{file_id}"
        return url, file_id
    except Exception as e:
        logger.warning(f"Mind map generation failed: {e}")
        return None, None


def get_cached_svg(file_id: str) -> Optional[Path]:
    """يُرجع Path للملف إذا كان موجوداً وضمن TTL، وإلا None."""
    if not re.fullmatch(r"[0-9a-f\-]{36}", file_id):
        return None
    path = _cache_dir() / f"{file_id}.svg"
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > _TTL_SECONDS:
        path.unlink(missing_ok=True)
        return None
    return path
