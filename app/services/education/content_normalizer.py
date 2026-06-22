"""
Content Normalizer
يحوّل أي بنية JSON معقولة قادمة من باك إند المستخدمين (محتوى تعليمي)
إلى الصيغة الموحدة [{"section": ..., "part": N, "text": ...}]
التي يفهمها محرك البحث المحلي (retriever.py) مباشرة.

البنية المتوقعة (مثال):
{
    "مقدمة": "نص المقدمة...",
    "أهداف": "نص الأهداف...",
    "دروس": ["نص الدرس الأول...", "نص الدرس الثاني..."]
}
لكن الدالة مرنة وتتعامل مع أي قاموس بمفاتيح نصية أو قوائم نصوص/كائنات.
"""


def normalize_to_chunks(raw: dict | list) -> list[dict]:
    chunks: list[dict] = []

    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if isinstance(item, dict) and "text" in item:
                chunks.append({
                    "section": item.get("section", item.get("title", f"جزء {i+1}")),
                    "part": 1,
                    "text": str(item["text"]),
                })
            elif isinstance(item, str) and item.strip():
                chunks.append({"section": f"جزء {i+1}", "part": 1, "text": item.strip()})
        return chunks

    if isinstance(raw, dict):
        # حقول وصفية تُستثنى من اعتبارها محتوى قابلاً للبحث فيه
        meta_keys = {"content_id", "id", "course_id", "title", "external_content_id"}

        for key, value in raw.items():
            if key in meta_keys:
                continue

            if isinstance(value, str) and value.strip():
                chunks.append({"section": key, "part": 1, "text": value.strip()})

            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str) and item.strip():
                        chunks.append({"section": key, "part": i + 1, "text": item.strip()})
                    elif isinstance(item, dict) and "text" in item:
                        chunks.append({
                            "section": item.get("title", key),
                            "part": i + 1,
                            "text": str(item["text"]),
                        })
        return chunks

    return chunks
