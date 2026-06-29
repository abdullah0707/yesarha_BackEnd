"""
Web Intelligence — SearXNG Client
Core يستخدم هذا للبحث التلقائي عن معلومات النماذج المتخصصة
مع fallback لو SearXNG غير متاح
"""
import hashlib
from datetime import datetime, timedelta
from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.specialist import WebSearchCache


class WebIntelligence:

    def __init__(self, db: Optional[Session] = None):
        self.base_url = settings.SEARXNG_URL
        self.timeout = settings.WEB_SEARCH_TIMEOUT
        self.db = db

        # Headers مطلوبة لـ SearXNG داخل Docker
        self.headers = {
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "User-Agent": "YesarhaCore/1.0 (Internal Search)",
        }

    def _cache_key(self, query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()

    def _get_cache(self, query: str) -> Optional[list]:
        if not self.db:
            return None
        key = self._cache_key(query)
        cached = self.db.query(WebSearchCache).filter(
            WebSearchCache.query_hash == key,
            WebSearchCache.expires_at > datetime.utcnow()
        ).first()
        return cached.results_json if cached else None

    def _set_cache(self, query: str, results: list, ttl_hours: int = 24):
        if not self.db:
            return
        key = self._cache_key(query)
        existing = self.db.query(WebSearchCache).filter(
            WebSearchCache.query_hash == key
        ).first()
        expires = datetime.utcnow() + timedelta(hours=ttl_hours)
        if existing:
            existing.results_json = results
            existing.expires_at = expires
        else:
            self.db.add(WebSearchCache(
                query_hash=key, query=query,
                results_json=results, expires_at=expires
            ))
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()

    def search(self, query: str, max_results: int = 10, use_cache: bool = True) -> list[dict]:
        """
        ابحث وارجع قائمة نتائج.
        إذا فشل SearXNG — يرجع نتائج placeholder بدلاً من crash.
        """
        if use_cache:
            cached = self._get_cache(query)
            if cached:
                return cached[:max_results]

        try:
            resp = requests.get(
                f"{self.base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": "auto",
                    "time_range": "",
                    "safesearch": "0",
                    "categories": "general,science,it",
                },
                headers=self.headers,
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for r in data.get("results", [])[:max_results]:
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "content": r.get("content", "")[:500],
                    "snippet": r.get("content", "")[:200],
                    "score":   r.get("score", 0),
                    "engine":  r.get("engine", ""),
                })

            if use_cache and results:
                self._set_cache(query, results)

            return results

        except requests.exceptions.ConnectionError:
            # SearXNG غير متاح — نرجع قائمة فارغة بدون error
            return self._fallback_results(query)
        except Exception:
            return self._fallback_results(query)

    def _fallback_results(self, query: str) -> list[dict]:
        """
        Fallback لو SearXNG غير متاح.
        يرجع معلومات عامة من الكود بدلاً من crash.
        النموذج سيُبنى بـ system prompt افتراضي ولكن سيعمل.
        """
        fallback_knowledge = {
            "code": "Best practices in software development include clean code, SOLID principles, testing, and documentation. Focus on readability and maintainability.",
            "education": "Effective teaching uses the Socratic method, spaced repetition, and adaptive learning. Break complex concepts into simple steps.",
            "business": "Business analysis requires understanding KPIs, market dynamics, SWOT analysis, and strategic planning frameworks like OKR.",
            "media": "Content creation best practices include storytelling, SEO optimization, audience targeting, and consistent brand voice.",
            "image": "Image generation prompting requires specific style descriptors, quality markers, and composition guidelines.",
            "voice": "Voice synthesis requires natural prosody, correct phonetics, and context-aware intonation patterns.",
            "custom": "Provide accurate, helpful, and professional responses tailored to the user's specific needs.",
        }

        # اعرف التخصص من الـ query
        knowledge = ""
        for spec, text in fallback_knowledge.items():
            if spec in query.lower():
                knowledge = text
                break
        if not knowledge:
            knowledge = "Provide professional, accurate, and helpful responses to user queries."

        return [{
            "title": f"General knowledge: {query[:50]}",
            "url": "",
            "content": knowledge,
            "snippet": knowledge[:200],
            "score": 0.5,
            "engine": "fallback",
        }]

    def search_for_specialist(self, specialization: str) -> dict:
        """
        يبحث عن كل المعلومات التي يحتاجها نموذج متخصص.
        يُستدعى عند إنشاء نموذج جديد.
        """
        queries = {
            "code": [
                "best practices software development clean code 2024",
                "software architecture patterns microservices API design",
            ],
            "voice": [
                "voice cloning TTS Arabic English best practices",
                "neural text to speech quality improvement",
            ],
            "image": [
                "AI image generation prompt engineering stable diffusion",
                "image description analysis computer vision techniques",
            ],
            "education": [
                "AI tutoring adaptive learning personalized education",
                "Arabic teaching methods online learning best practices",
            ],
            "business": [
                "business analysis strategy KPI OKR frameworks",
                "financial reporting business intelligence 2024",
            ],
            "media": [
                "content creation social media marketing best practices",
                "video script writing storytelling techniques",
            ],
        }

        target_queries = queries.get(specialization, [
            f"{specialization} AI assistant best practices 2024",
        ])

        all_results = []
        for q in target_queries:
            results = self.search(q, max_results=5)
            all_results.extend(results)

        knowledge = "\n\n".join([
            f"### {r['title']}\n{r['content']}"
            for r in all_results if r.get("content")
        ])

        return {
            "specialization": specialization,
            "results_count": len(all_results),
            "knowledge_base": knowledge,
            "sources": [r["url"] for r in all_results if r.get("url")],
        }

    def weekly_ai_scan(self) -> dict:
        """فحص أسبوعي تلقائي لأحدث تطورات الذكاء الاصطناعي"""
        scan_queries = [
            "latest open source AI models 2024 2025",
            "Arabic NLP models advances 2024",
            "LLM fine-tuning techniques new methods",
        ]

        all_findings = []
        for q in scan_queries:
            results = self.search(q, max_results=3, use_cache=False)
            all_findings.extend(results)

        return {
            "scan_date": datetime.utcnow().isoformat(),
            "total_findings": len(all_findings),
            "findings": all_findings,
        }
