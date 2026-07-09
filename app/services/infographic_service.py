"""
Infographic generation — LLM extracts JSON → Python renders SVG.
No external dependencies. SVG served directly; Chromium/Electron renders it natively.
"""
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("yesarha.infographic")

_IMAGE_CACHE_DIR = Path("/app/data/image_cache")
_DEFAULT_COLORS = ["#2563EB", "#16A34A", "#D97706", "#DC2626", "#7C3AED", "#0891B2"]
_FONT = "'Segoe UI', Arial, Tahoma, 'Helvetica Neue', sans-serif"


def _cache_dir() -> Path:
    _IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _IMAGE_CACHE_DIR


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _lighten(hex_color: str, factor: float = 0.88) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#{:02X}{:02X}{:02X}".format(
        int(r + (255 - r) * factor),
        int(g + (255 - g) * factor),
        int(b + (255 - b) * factor),
    )


def _parse_colors(color_palette: str) -> list:
    hexes = re.findall(r"#[0-9A-Fa-f]{6}", color_palette)
    return hexes if hexes else _DEFAULT_COLORS[:]


def _t(x, y, text, size=16, fill="#1F2937", weight="normal", anchor="middle", clip=0):
    """SVG text with Arabic RTL support."""
    if clip and len(str(text)) > clip:
        text = str(text)[: clip - 1] + "…"
    return (
        f'<text x="{x}" y="{y}" font-family="{_FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
        f'direction="rtl" unicode-bidi="embed">{_esc(str(text))}</text>'
    )


def _parse_json(raw: str) -> Optional[dict]:
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None


# ── Sequential layout (مراحل / خطوات) ────────────────────────────────────────

def _render_sequential(items: list, title: str, colors: list) -> str:
    n = min(len(items), 5)
    items = items[:n]
    W, H = 900, 400
    ARROW_W = 28
    PAD_X = 32
    item_w = (W - 2 * PAD_X - (n - 1) * ARROW_W) // n
    ITEM_H = 178
    BOX_Y = 112

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
        f'<rect width="{W}" height="{H}" fill="#F9FAFB"/>',
        f'<rect width="{W}" height="80" fill="{colors[0]}"/>',
        f'<rect x="0" y="76" width="{W}" height="4" fill="{_lighten(colors[0], 0.3)}"/>',
        _t(W // 2, 48, title, size=22, fill="#FFFFFF", weight="bold"),
    ]

    for i, item in enumerate(items):
        color = colors[i % len(colors)]
        light = _lighten(color)
        x = PAD_X + i * (item_w + ARROW_W)
        cx = x + item_w // 2

        # Card shadow effect
        out.append(f'<rect x="{x+3}" y="{BOX_Y+3}" width="{item_w}" height="{ITEM_H}" rx="12" fill="#00000015"/>')
        # Card
        out.append(f'<rect x="{x}" y="{BOX_Y}" width="{item_w}" height="{ITEM_H}" rx="12" fill="white" stroke="{color}" stroke-width="2"/>')
        # Colored header strip
        out.append(f'<rect x="{x}" y="{BOX_Y}" width="{item_w}" height="46" rx="12" fill="{color}"/>')
        out.append(f'<rect x="{x}" y="{BOX_Y + 34}" width="{item_w}" height="12" fill="{color}"/>')
        # Number bubble
        out.append(f'<circle cx="{cx}" cy="{BOX_Y - 18}" r="18" fill="{color}" stroke="#F9FAFB" stroke-width="3"/>')
        out.append(_t(cx, BOX_Y - 11, i + 1, size=15, fill="#FFFFFF", weight="bold"))
        # Heading
        out.append(_t(cx, BOX_Y + 30, item.get("heading", ""), size=13, fill="#FFFFFF", weight="bold", clip=18))
        # Body (split into 2 lines)
        body = item.get("body", "")
        words = body.split()
        half = max(1, len(words) // 2)
        line1 = " ".join(words[:half])
        line2 = " ".join(words[half:])
        out.append(_t(cx, BOX_Y + 82, line1, size=11, fill="#374151", clip=22))
        if line2:
            out.append(_t(cx, BOX_Y + 99, line2, size=11, fill="#374151", clip=22))

        # Arrow (triangle) between cards
        if i < n - 1:
            ax = x + item_w + ARROW_W // 2
            ay = BOX_Y + ITEM_H // 2
            out.append(f'<polygon points="{ax-9},{ay-9} {ax+9},{ay} {ax-9},{ay+9}" fill="#CBD5E1"/>')

    out.append("</svg>")
    return "\n".join(out)


# ── Grid layout (أنواع / فوائد / مكونات) ────────────────────────────────────

def _render_grid(items: list, title: str, colors: list) -> str:
    n = min(len(items), 8)
    items = items[:n]
    COLS = 2
    rows = (n + COLS - 1) // COLS
    W = 900
    CARD_W, CARD_H = 402, 112
    GAP_X, GAP_Y = 16, 14
    PAD_X = (W - COLS * CARD_W - (COLS - 1) * GAP_X) // 2
    HEADER_H = 80
    H = HEADER_H + 18 + rows * CARD_H + (rows - 1) * GAP_Y + 28

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
        f'<rect width="{W}" height="{H}" fill="#F9FAFB"/>',
        f'<rect width="{W}" height="{HEADER_H}" fill="{colors[0]}"/>',
        f'<rect x="0" y="{HEADER_H - 4}" width="{W}" height="4" fill="{_lighten(colors[0], 0.3)}"/>',
        _t(W // 2, 48, title, size=22, fill="#FFFFFF", weight="bold"),
    ]

    for i, item in enumerate(items):
        col = i % COLS
        row = i // COLS
        color = colors[i % len(colors)]
        x = PAD_X + col * (CARD_W + GAP_X)
        y = HEADER_H + 18 + row * (CARD_H + GAP_Y)
        cx = x + CARD_W // 2

        out.append(f'<rect x="{x+2}" y="{y+2}" width="{CARD_W}" height="{CARD_H}" rx="10" fill="#00000012"/>')
        out.append(f'<rect x="{x}" y="{y}" width="{CARD_W}" height="{CARD_H}" rx="10" fill="white" stroke="{color}" stroke-width="1.5"/>')
        # Left accent bar
        out.append(f'<rect x="{x}" y="{y}" width="7" height="{CARD_H}" rx="4" fill="{color}"/>')
        out.append(f'<rect x="{x+3}" y="{y}" width="4" height="{CARD_H}" fill="{color}"/>')
        # Number circle
        out.append(f'<circle cx="{x + 36}" cy="{y + CARD_H // 2}" r="18" fill="{_lighten(color, 0.6)}" stroke="{color}" stroke-width="1.5"/>')
        out.append(_t(x + 36, y + CARD_H // 2 + 5, i + 1, size=13, fill=color, weight="bold"))
        # Text (centered in right portion)
        tx = x + 36 + (CARD_W - 36) // 2 + 20
        out.append(_t(tx, y + 40, item.get("heading", ""), size=14, fill="#111827", weight="bold", clip=24))
        out.append(_t(tx, y + 64, item.get("body", ""), size=11, fill="#6B7280", clip=38))

    out.append("</svg>")
    return "\n".join(out)


# ── Comparison layout (الفرق بين) ────────────────────────────────────────────

def _render_comparison(data: dict, colors: list) -> str:
    title = data.get("title", "")
    left_title = data.get("left_title", "")
    right_title = data.get("right_title", "")
    items = data.get("items", [])[:7]
    n = len(items)
    W = 900
    COL_W, GAP = 378, 24
    PAD_X = (W - 2 * COL_W - GAP) // 2
    ROW_H = 54
    H = 80 + 58 + n * ROW_H + 40
    c0 = colors[0] if colors else "#2563EB"
    c1 = colors[1] if len(colors) > 1 else "#DC2626"

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
        f'<rect width="{W}" height="{H}" fill="#F9FAFB"/>',
        f'<rect width="{W}" height="72" fill="#1F2937"/>',
        _t(W // 2, 44, title, size=20, fill="#FFFFFF", weight="bold"),
        # Column headers
        f'<rect x="{PAD_X}" y="82" width="{COL_W}" height="48" rx="8" fill="{c0}"/>',
        f'<rect x="{PAD_X + COL_W + GAP}" y="82" width="{COL_W}" height="48" rx="8" fill="{c1}"/>',
        _t(PAD_X + COL_W // 2, 113, left_title, size=16, fill="#FFFFFF", weight="bold"),
        _t(PAD_X + COL_W + GAP + COL_W // 2, 113, right_title, size=16, fill="#FFFFFF", weight="bold"),
    ]

    for i, item in enumerate(items):
        y = 140 + i * ROW_H
        bg_l = _lighten(c0, 0.94) if i % 2 == 0 else "#FFFFFF"
        bg_r = _lighten(c1, 0.94) if i % 2 == 0 else "#FFFFFF"
        cx_l = PAD_X + COL_W // 2
        cx_r = PAD_X + COL_W + GAP + COL_W // 2

        out.append(f'<rect x="{PAD_X}" y="{y}" width="{COL_W}" height="{ROW_H}" fill="{bg_l}"/>')
        out.append(f'<rect x="{PAD_X + COL_W + GAP}" y="{y}" width="{COL_W}" height="{ROW_H}" fill="{bg_r}"/>')
        out.append(_t(cx_l, y + ROW_H // 2 + 5, item.get("left", ""), size=13, fill="#1F2937", clip=30))
        out.append(_t(cx_r, y + ROW_H // 2 + 5, item.get("right", ""), size=13, fill="#1F2937", clip=30))
        sep_y = y + ROW_H
        out.append(f'<line x1="{PAD_X}" y1="{sep_y}" x2="{PAD_X + COL_W}" y2="{sep_y}" stroke="#E5E7EB" stroke-width="1"/>')
        out.append(f'<line x1="{PAD_X+COL_W+GAP}" y1="{sep_y}" x2="{PAD_X+2*COL_W+GAP}" y2="{sep_y}" stroke="#E5E7EB" stroke-width="1"/>')

    out.append("</svg>")
    return "\n".join(out)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _render_svg(data: dict, color_palette: str = "") -> Optional[str]:
    colors = _parse_colors(color_palette) if color_palette.strip() else _DEFAULT_COLORS[:]
    layout = data.get("layout", "grid")
    title = data.get("title", "")
    items = data.get("items", [])

    if layout == "comparison":
        return _render_comparison(data, colors)
    elif layout == "sequential" and len(items) <= 5:
        return _render_sequential(items, title, colors)
    else:
        return _render_grid(items, title, colors)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_infographic(
    arabic_request: str,
    context: str = "",
    color_palette: str = "",
) -> Optional[tuple]:
    """
    Generates an SVG infographic from an Arabic educational request.
    Returns (url, image_id) or None on failure.
    """
    try:
        from app.services.ollama_client import OllamaClient
        from app.services.runtime_config import runtime_cfg
        from app.core.prompts import get_infographic_system_prompt, build_infographic_prompt

        client = OllamaClient()
        model = runtime_cfg.get_core_model()
        result = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": get_infographic_system_prompt()},
                {"role": "user", "content": build_infographic_prompt(arabic_request, context)},
            ],
            options={"temperature": 0.2, "num_predict": 700},
            think=False,
            timeout=45,
        )

        data = _parse_json(result["content"])
        if not data or "items" not in data:
            logger.warning(f"Infographic: invalid JSON: {result['content'][:120]}")
            return None

        svg = _render_svg(data, color_palette)
        if not svg:
            return None

        image_id = str(uuid.uuid4())
        (_cache_dir() / f"{image_id}.svg").write_text(svg, encoding="utf-8")
        logger.info(f"Infographic saved: {image_id} layout={data.get('layout')}")
        return f"/api/v1/specialist/image/{image_id}", image_id

    except Exception as e:
        logger.warning(f"Infographic generation failed: {e}")
        return None
