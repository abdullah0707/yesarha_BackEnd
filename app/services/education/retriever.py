"""
Lightweight Local Retriever
يبحث عن الفقرات الأكثر صلة بسؤال الطالب باستخدام TF-IDF
(سريع جداً، يعمل بدون GPU، بدون نموذج إضافي، مناسب تماماً لحجم الفصل الواحد)
"""
import re
from collections import Counter
from math import log

# كلمات وقف عربية + إنجليزية شائعة لتحسين دقة البحث
STOPWORDS = {
    "في", "من", "إلى", "على", "عن", "مع", "هذا", "هذه", "ذلك", "التي", "الذي",
    "كان", "يكون", "هو", "هي", "أن", "إن", "لا", "ما", "كل", "بعد", "قبل",
    "the", "is", "are", "was", "were", "in", "on", "at", "to", "for", "of",
    "and", "or", "a", "an", "this", "that", "with", "by", "as",
}

# \u0646\u0633\u062A\u062E\u062F\u0645 \u0646\u0637\u0627\u0642\u0627\u062A \u0627\u0644\u062D\u0631\u0648\u0641 \u0627\u0644\u0639\u0631\u0628\u064A\u0629 \u0641\u0642\u0637 \u2014 \u0644\u0627 \u0627\u0644\u0643\u062A\u0644\u0629 \u0643\u0627\u0645\u0644\u0629 (U+0600-U+06FF \u062A\u0634\u0645\u0644 \u0639\u0644\u0627\u0645\u0627\u062A \u062A\u0631\u0642\u064A\u0645 \u0639\u0631\u0628\u064A\u0629 \u0643\u0640\u061F \u0648\u060C)
WORD_PATTERN = re.compile(r"[a-zA-Z0-9\u0621-\u063A\u0641-\u064A\u0660-\u0669\u0671-\u06D3]+")


def _tokenize(text: str) -> list[str]:
    words = WORD_PATTERN.findall(text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _term_freq(tokens: list[str]) -> Counter:
    return Counter(tokens)


def retrieve_relevant_chunks(
    chunks: list[dict], question: str, top_k: int = 3
) -> list[dict]:
    """
    يرجّع أفضل top_k فقرات الأكثر صلة بسؤال الطالب.
    يستخدم TF-IDF بسيط — سريع ودقيق بما يكفي لحجم محتوى الدورة الواحدة.
    """
    if not chunks:
        return []

    # إذا المحتوى صغير أصلاً (3 فقرات أو أقل) أرسل الكل — لا داعي للبحث
    if len(chunks) <= top_k:
        return chunks

    q_tokens = _tokenize(question)
    if not q_tokens:
        return chunks[:top_k]

    # حساب IDF عبر كل الفقرات
    doc_count = len(chunks)
    doc_freq = Counter()
    chunk_tokens_list = []

    for c in chunks:
        tokens = _tokenize(c.get("text") or c.get("content", ""))
        chunk_tokens_list.append(tokens)
        for term in set(tokens):
            doc_freq[term] += 1

    def idf(term: str) -> float:
        return log((doc_count + 1) / (doc_freq.get(term, 0) + 1)) + 1

    scores = []
    for i, (chunk, tokens) in enumerate(zip(chunks, chunk_tokens_list)):
        tf = _term_freq(tokens)
        score = sum(
            tf.get(qt, 0) * idf(qt)
            for qt in q_tokens
        )
        # مكافأة متناسبة مع عدد كلمات السؤال الموجودة في عنوان الفقرة
        # كلما تطابق العنوان مع السؤال أكثر → أولوية أعلى
        section_lower = chunk.get("section", "").lower()
        title_matches = sum(1 for qt in q_tokens if qt in section_lower)
        score += title_matches * 2.0

        scores.append((score, i, chunk))

    scores.sort(key=lambda x: x[0], reverse=True)

    # خذ أعلى top_k، لكن لو كل الدرجات صفر → لا يوجد محتوى ذي صلة → أرجع قائمة فارغة
    # (الكود الاستدعائي يكتشف هذا ويرد برسالة "خارج نطاق الدرس" بدون استدعاء النموذج)
    top = [c for score, i, c in scores[:top_k] if score > 0]
    return top


def build_context_from_chunks(chunks: list[dict]) -> str:
    """يبني نص السياق المُرسَل للنموذج من الفقرات المختارة"""
    parts = []
    for c in chunks:
        parts.append(f"### {c.get('section', '')}\n{c.get('text') or c.get('content', '')}")
    return "\n\n".join(parts)
