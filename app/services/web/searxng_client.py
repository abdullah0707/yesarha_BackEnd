"""
Web Intelligence — SearXNG Client
Core يستخدم هذا للبحث التلقائي عن معلومات النماذج المتخصصة
والتطورات الأسبوعية في الذكاء الاصطناعي
"""
import hashlib
import json
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
        self.db.commit()

    def search(self, query: str, max_results: int = 10, use_cache: bool = True) -> list[dict]:
        """
        ابحث عن معلومات وارجع قائمة من النتائج
        كل نتيجة: {title, url, content, score}
        """
        # فحص الـ cache أولاً
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
                timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for r in data.get("results", [])[:max_results]:
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "content": r.get("content", "")[:500],  # أول 500 حرف
                    "score":   r.get("score", 0),
                    "engine":  r.get("engine", ""),
                })

            if use_cache and results:
                self._set_cache(query, results)

            return results

        except Exception as e:
            return [{"title": "Search Error", "url": "", "content": str(e), "score": 0, "engine": ""}]

    def search_for_specialist(self, specialization: str, language: str = "ar+en") -> dict:
        """
        يبحث عن كل المعلومات التي يحتاجها نموذج متخصص
        يُستدعى عند إنشاء نموذج جديد
        """
        queries = {
            "code": [
                "best practices software development 2024 2025",
                "clean code principles software architecture",
                "أفضل ممارسات البرمجة وهندسة البرمجيات",
                "Python best practices advanced patterns",
            ],
            "voice": [
                "voice cloning techniques Arabic English TTS",
                "XTTS voice synthesis best practices",
                "Arabic text to speech neural network",
                "voice quality improvement techniques 2024",
            ],
            "image": [
                "stable diffusion prompt engineering 2024",
                "image generation best practices artistic styles",
                "Arabic text in images generation techniques",
                "SDXL optimization quality settings",
            ],
            "education": [
                "AI tutoring best practices adaptive learning",
                "educational content Arabic explanation techniques",
                "Socratic method AI implementation",
                "personalized learning AI 2024",
            ],
        }

        target_queries = queries.get(specialization, [
            f"{specialization} AI model best practices 2024",
            f"أفضل ممارسات نموذج الذكاء الاصطناعي {specialization}",
        ])

        all_results = []
        for q in target_queries:
            results = self.search(q, max_results=5)
            all_results.extend(results)

        # تلخيص المعلومات كـ knowledge base
        knowledge = "\n\n".join([
            f"### {r['title']}\n{r['content']}"
            for r in all_results if r.get('content')
        ])

        return {
            "specialization": specialization,
            "queries_used": target_queries,
            "results_count": len(all_results),
            "knowledge_base": knowledge,
            "sources": [r["url"] for r in all_results if r.get("url")],
        }

    def weekly_ai_scan(self) -> dict:
        """
        فحص أسبوعي تلقائي لأحدث تطورات الذكاء الاصطناعي
        يُستدعى تلقائياً كل أسبوع بواسطة Core
        """
        scan_queries = [
            "latest AI models released 2024 2025",
            "open source LLM advances this week",
            "Arabic NLP models advances 2024",
            "voice cloning AI advances 2024",
            "image generation AI new models 2024",
            "AI fine-tuning techniques new methods",
            "تطورات الذكاء الاصطناعي الأسبوع",
        ]

        all_findings = []
        for q in scan_queries:
            results = self.search(q, max_results=3, use_cache=False)
            all_findings.extend(results)

        return {
            "scan_date": datetime.utcnow().isoformat(),
            "total_findings": len(all_findings),
            "findings": all_findings,
            "summary": "\n".join([
                f"- {r['title']}: {r['content'][:200]}"
                for r in all_findings[:10]
            ])
        }
