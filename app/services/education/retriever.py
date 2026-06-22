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

WORD_PATTERN = re.compile(r"[\w\u0600-\u06FF]+")


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
        tokens = _tokenize(c["text"])
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
        # مكافأة بسيطة لو عنوان الفقرة يطابق كلمة من السؤال
        section_lower = chunk.get("section", "").lower()
        if any(qt in section_lower for qt in q_tokens):
            score += 2.0

        scores.append((score, i, chunk))

    scores.sort(key=lambda x: x[0], reverse=True)

    # خذ أعلى top_k، لكن لو كل الدرجات صفر (سؤال عام جداً) أرسل أول الفقرات
    top = [c for score, i, c in scores[:top_k] if score > 0]
    if not top:
        return chunks[:top_k]

    return top


def build_context_from_chunks(chunks: list[dict]) -> str:
    """يبني نص السياق المُرسَل للنموذج من الفقرات المختارة"""
    parts = []
    for c in chunks:
        parts.append(f"### {c['section']}\n{c['text']}")
    return "\n\n".join(parts)
