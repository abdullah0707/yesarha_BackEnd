"""
Tool Executor — ينفذ الأدوات التي يطلبها Core
"""
import json
from sqlalchemy.orm import Session

from app.services.web.searxng_client import WebIntelligence
from app.services.models.model_manager import model_manager
from app.models.specialist import SpecialistModel, ModelPerformanceLog
from app.core.config import settings


class ToolExecutor:

    def __init__(self, db: Session):
        self.db = db
        self.web = WebIntelligence(db=db)

    def execute(self, tool_name: str, parameters: dict) -> dict:
        """ينفذ الأداة ويرجع النتيجة"""
        executors = {
            "web_search":             self._web_search,
            "create_specialist_model": self._create_specialist_model,
            "list_specialist_models":  self._list_specialist_models,
            "get_model_performance":   self._get_model_performance,
            "get_system_status":       self._get_system_status,
        }

        fn = executors.get(tool_name)
        if not fn:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            return fn(**parameters)
        except Exception as e:
            return {"error": str(e), "tool": tool_name}

    # ── Tools Implementation ──────────────────────────────────────

    def _web_search(self, query: str, max_results: int = 5) -> dict:
        results = self.web.search(query, max_results=max_results)
        return {
            "query": query,
            "results_count": len(results),
            "results": results
        }

    def _create_specialist_model(
        self, name: str, display_name: str,
        specialization: str, description: str = ""
    ) -> dict:
        """ينشئ نموذجاً متخصصاً جديداً في قاعدة البيانات"""

        # فحص إذا موجود مسبقاً
        existing = self.db.query(SpecialistModel).filter(
            SpecialistModel.name == name
        ).first()
        if existing:
            return {
                "status": "already_exists",
                "model_id": existing.id,
                "message": f"النموذج '{name}' موجود بالفعل"
            }

        # تحديد النموذج الأساسي حسب التخصص
        base_models = {
            "code":      "qwen2.5-coder:7b",
            "voice":     settings.CORE_MODEL,   # Core يتولى الصوت مع XTTS
            "image":     settings.CORE_MODEL,   # Core يوجه لـ image service
            "education": settings.CORE_MODEL,
            "custom":    settings.CORE_MODEL,
        }

        vram_map = {
            "code": 5.5, "voice": 2.0,
            "image": 6.0, "education": 5.5, "custom": 5.0
        }

        # البحث عن معلومات التخصص
        knowledge = self.web.search_for_specialist(specialization)

        # بناء system prompt من المعلومات المجمعة
        system_prompt = self._build_specialist_prompt(
            specialization, display_name, knowledge.get("knowledge_base", "")
        )

        # إنشاء في قاعدة البيانات
        specialist = SpecialistModel(
            name=name,
            display_name=display_name,
            specialization=specialization,
            description=description,
            base_model=base_models.get(specialization, settings.CORE_MODEL),
            system_prompt=system_prompt,
            config_json={
                "temperature": 0.7,
                "top_p": 0.9,
                "max_tokens": 4096,
            },
            vram_required_gb=vram_map.get(specialization, 5.0),
            status="created",
            is_public_api=True,
            api_endpoint=f"/api/v1/specialist/{name.replace('yesarha-', '')}",
            training_data_sources=knowledge.get("sources", []),
        )
        self.db.add(specialist)
        self.db.commit()
        self.db.refresh(specialist)

        return {
            "status": "created",
            "model_id": specialist.id,
            "name": specialist.name,
            "display_name": specialist.display_name,
            "base_model": specialist.base_model,
            "api_endpoint": specialist.api_endpoint,
            "knowledge_sources": len(knowledge.get("sources", [])),
            "message": f"✅ تم إنشاء النموذج '{display_name}' بنجاح. البيانات التدريبية جاهزة من {len(knowledge.get('sources', []))} مصدر."
        }

    def _build_specialist_prompt(
        self, specialization: str, name: str, knowledge: str
    ) -> str:
        base_prompts = {
            "code": f"""أنت {name} — نموذج متخصص في البرمجة وهندسة البرمجيات من يسرها.

## تخصصك:
- كتابة كود نظيف واحترافي بجميع لغات البرمجة
- تصميم معمارية البرمجيات والأنظمة
- مراجعة الكود وإيجاد الأخطاء وإصلاحها
- التخطيط لمشاريع البرمجة من الصفر

## قواعدك:
- دائماً أضف تعليقات توضيحية للكود
- اقترح أفضل الممارسات والمعايير
- فسّر الكود بالعربية عند الطلب
- استخدم أحدث إصدارات المكتبات

## معلومات إضافية تم جمعها:
{knowledge[:2000]}""",

            "voice": f"""أنت {name} — نموذج متخصص في معالجة الصوت واستنساخه من يسرها.

## تخصصك:
- تحويل النص إلى صوت بصوت المستخدم نفسه
- تحويل الصوت إلى نص (عربي وإنجليزي)
- تحسين جودة الصوت
- الرد الصوتي الطبيعي

## قواعدك:
- حافظ على طبيعية الصوت والتنغيم
- ادعم اللهجات العربية المختلفة
- كن دقيقاً في نطق الأسماء والمصطلحات""",

            "education": f"""أنت {name} — نموذج متخصص في التعليم والشرح من يسرها.

## تخصصك:
- شرح المفاهيم المعقدة بأسلوب بسيط
- الرد على أسئلة الطلاب بصبر ووضوح
- تقديم أمثلة وتمارين تفاعلية
- التعليم بالعربية والإنجليزية

## قواعدك:
- استخدم أسلوب سقراط (الأسئلة التوجيهية)
- تحقق من فهم الطالب قبل المتابعة
- قدّم مستويات شرح مختلفة (مبتدئ، متوسط، متقدم)""",
        }

        return base_prompts.get(specialization, f"""أنت {name} من يسرها للذكاء الاصطناعي.
متخصص في: {specialization}

{knowledge[:1000]}""")

    def _list_specialist_models(self, status: str = "all") -> dict:
        query = self.db.query(SpecialistModel)
        if status != "all":
            query = query.filter(SpecialistModel.status == status)

        models = query.all()
        return {
            "total": len(models),
            "models": [{
                "id": m.id,
                "name": m.name,
                "display_name": m.display_name,
                "specialization": m.specialization,
                "status": m.status,
                "api_endpoint": m.api_endpoint,
                "total_requests": m.total_requests,
                "success_rate": m.success_rate,
            } for m in models]
        }

    def _get_model_performance(self, model_name: str) -> dict:
        model = self.db.query(SpecialistModel).filter(
            SpecialistModel.name == model_name
        ).first()

        if not model:
            return {"error": f"النموذج '{model_name}' غير موجود"}

        logs = self.db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_name == model_name
        ).order_by(ModelPerformanceLog.created_at.desc()).limit(100).all()

        if not logs:
            return {
                "model": model_name,
                "message": "لا توجد بيانات أداء بعد"
            }

        avg_response = sum(l.response_ms for l in logs) / len(logs)
        success_count = sum(1 for l in logs if l.status == "success")
        issues = []
        for log in logs:
            if log.issues_detected:
                issues.extend(log.issues_detected)

        return {
            "model": model_name,
            "total_requests": len(logs),
            "success_rate": f"{(success_count / len(logs)) * 100:.1f}%",
            "avg_response_ms": int(avg_response),
            "common_issues": list(set(issues))[:5],
            "recommendation": "يحتاج تدريب إضافي" if success_count / len(logs) < 0.85 else "الأداء جيد"
        }

    def _get_system_status(self) -> dict:
        status = model_manager.get_status()
        specialist_count = self.db.query(SpecialistModel).count()
        active_count = self.db.query(SpecialistModel).filter(
            SpecialistModel.status == "active"
        ).count()

        return {
            **status,
            "specialist_models": {
                "total": specialist_count,
                "active": active_count,
            }
        }
