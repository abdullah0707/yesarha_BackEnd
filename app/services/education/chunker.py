"""
Education Content Chunker
يقسّم محتوى الفصل التعليمي لفقرات (chunks) بناءً على البنية
(مقدمة، أهداف، دروس، إلخ) بدون أي نموذج إضافي — منطق نصي سريع
"""
import re

# عناوين شائعة تُستخدم لتقسيم المحتوى التعليمي عربياً وإنجليزياً
SECTION_MARKERS = [
    r"مقدمة", r"المقدمة", r"introduction",
    r"الأهداف", r"أهداف الدرس", r"objectives", r"goals",
    r"الدرس\s*\d*", r"lesson\s*\d*",
    r"الفصل\s*\d*", r"chapter\s*\d*",
    r"ملخص", r"الملخص", r"summary",
    r"تمارين", r"التمارين", r"exercises",
    r"مصطلحات", r"المصطلحات", r"key terms",
    r"خاتمة", r"الخاتمة", r"conclusion",
]

HEADER_PATTERN = re.compile(
    r"^(?:#{1,4}\s*)?(" + "|".join(SECTION_MARKERS) + r")[:\s]*.*$",
    re.IGNORECASE | re.MULTILINE
)

MAX_CHUNK_CHARS = 1800   # حجم آمن لكل قطعة (يحافظ على السياق دون إثقال الموديل)
MIN_CHUNK_CHARS = 15      # أي قسم له عنوان واضح يُحتفظ به مهما كان قصيراً


def split_by_structure(content: str) -> list[dict]:
    """
    يقسّم النص بناءً على العناوين المعروفة (مقدمة/أهداف/دروس...).
    إن لم توجد عناوين واضحة، يُقسَّم بالحجم فقط (fallback).
    """
    content = content.strip()
    if not content:
        return []

    matches = list(HEADER_PATTERN.finditer(content))

    chunks = []

    if matches:
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            section_title = m.group(1).strip()
            section_text = content[start:end].strip()

            if len(section_text) < MIN_CHUNK_CHARS:
                continue

            # إذا القسم نفسه طويل جداً، قسّمه أكثر بالحجم
            if len(section_text) > MAX_CHUNK_CHARS:
                sub_chunks = _split_by_size(section_text)
                for j, sub in enumerate(sub_chunks):
                    chunks.append({
                        "section": section_title,
                        "part": j + 1,
                        "text": sub,
                    })
            else:
                chunks.append({
                    "section": section_title,
                    "part": 1,
                    "text": section_text,
                })
    else:
        # لا توجد عناوين معروفة — قسّم بالحجم فقط
        size_chunks = _split_by_size(content)
        for i, c in enumerate(size_chunks):
            chunks.append({
                "section": f"جزء {i + 1}",
                "part": 1,
                "text": c,
            })

    return chunks


def _split_by_size(text: str) -> list[str]:
    """يقسّم نصاً طويلاً لقطع بحجم آمن، محافظاً على حدود الفقرات قدر الإمكان"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""

    for p in paragraphs:
        if len(current) + len(p) + 2 <= MAX_CHUNK_CHARS:
            current = f"{current}\n\n{p}" if current else p
        else:
            if current:
                chunks.append(current)
            current = p

    if current:
        chunks.append(current)

    return chunks if chunks else [text[:MAX_CHUNK_CHARS]]
