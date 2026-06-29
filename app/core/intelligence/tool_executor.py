"""
Tool Executor — ينفذ الأدوات التي يطلبها Core
كل أداة متصلة بقاعدة البيانات مباشرة
"""
import json
from sqlalchemy.orm import Session

from app.services.web.searxng_client import WebIntelligence
from app.services.models.model_manager import model_manager
from app.models.specialist import SpecialistModel, ModelPerformanceLog, TrainingSession
from app.core.config import settings
from app.core.intelligence.specializations import SPECIALIZATIONS


class ToolExecutor:

    def __init__(self, db: Session):
        self.db = db
        self.web = WebIntelligence(db=db)

    def execute(self, tool_name: str, parameters: dict) -> dict:
        executors = {
            "web_search":              self._web_search,
            "create_specialist_model": self._create_specialist_model,
            "list_specialist_models":  self._list_specialist_models,
            "update_specialist_prompt": self._update_specialist_prompt,
            "get_training_report":     self._get_training_report,
            "get_model_performance":   self._get_model_performance,
            "get_system_status":       self._get_system_status,
        }
        fn = executors.get(tool_name)
        if not fn:
            return {"error": f"أداة غير موجودة: {tool_name}"}
        try:
            return fn(**parameters)
        except TypeError as e:
            return {"error": f"معاملات خاطئة للأداة {tool_name}: {str(e)}"}
        except Exception as e:
            return {"error": str(e), "tool": tool_name}

    # ── web_search ────────────────────────────────────────────────

    def _web_search(self, query: str, max_results: int = 5) -> dict:
        results = self.web.search(query, max_results=max_results)
        return {"query": query, "results_count": len(results), "results": results}

    # ── create_specialist_model ───────────────────────────────────

    def _create_specialist_model(
        self, name: str, display_name: str,
        specialization: str, description: str = ""
    ) -> dict:
        """
        ينشئ النموذج في DB ويطلق _background_specialist_setup في thread منفصل.
        الـ setup يشمل: بحث إنترنت → system prompt → Pull الموديل → API Key.
        """
        from app.core.intelligence.api_keys import generate_api_key
        from app.models.specialist import CoreTask
        from app.core.intelligence.specializations import get_base_model, get_vram_required

        existing = self.db.query(SpecialistModel).filter(
            SpecialistModel.name == name
        ).first()
        if existing:
            return {
                "status": "already_exists",
                "model_id": existing.id,
                "name": existing.name,
                "current_status": existing.status,
                "message": f"النموذج '{name}' موجود بالفعل — الحالة: {existing.status}"
            }

        specialist = SpecialistModel(
            name=name,
            display_name=display_name,
            specialization=specialization,
            description=description,
            base_model=get_base_model(specialization),
            vram_required_gb=get_vram_required(specialization),
            status="creating",
            is_public_api=True,
            created_by_core=True,
            api_endpoint=f"/specialist/{name.replace('yesarha-', '')}",
            config_json={
                "setup_progress": 0,
                "setup_log": "⏳ Core يبدأ الإعداد...",
                "setup_status": "creating"
            }
        )
        self.db.add(specialist)
        self.db.commit()
        self.db.refresh(specialist)

        task = CoreTask(
            task_type="model_creation",
            target_model_id=specialist.id,
            status="pending",
            input_data={"specialization": specialization, "name": name, "source": "core_chat"}
        )
        self.db.add(task)
        self.db.commit()

        # إطلاق الـ setup في background thread
        # import داخل الدالة تجنباً للـ circular import
        import threading
        def _run_setup(sid: int):
            from app.api.v1.admin.specialists import _background_specialist_setup
            _background_specialist_setup(sid)
        t = threading.Thread(target=_run_setup, args=(specialist.id,), daemon=True)
        t.start()

        spec_info = SPECIALIZATIONS.get(specialization, {})
        return {
            "status": "creating",
            "model_id": specialist.id,
            "name": specialist.name,
            "display_name": specialist.display_name,
            "specialization": specialization,
            "base_model": specialist.base_model,
            "vram_required_gb": specialist.vram_required_gb,
            "api_endpoint": specialist.api_endpoint,
            "message": (
                f"✅ بدأ إنشاء '{display_name}' بنجاح!\n"
                f"• الموديل: {specialist.base_model}\n"
                f"• Core يبحث على الإنترنت ويبني System Prompt تلقائياً\n"
                f"• سيعمل Pull لـ '{specialist.base_model}' إن لم يكن موجوداً على القرص\n"
                f"• API Key سيُولَّد تلقائياً عند الانتهاء\n"
                f"• تابع التقدم من لوحة التحكم ← النماذج المتخصصة"
            )
        }

    # ── list_specialist_models ────────────────────────────────────

    def _list_specialist_models(self, status: str = "all") -> dict:
        query = self.db.query(SpecialistModel)
        if status and status != "all":
            query = query.filter(SpecialistModel.status == status)

        models = query.order_by(SpecialistModel.created_at.desc()).all()

        status_labels = {
            "creating": "إنشاء", "downloading": "تحميل الموديل",
            "training": "تحضير", "active": "نشط ✅",
            "inactive": "متوقف", "error": "خطأ ❌"
        }

        return {
            "total": len(models),
            "filter": status,
            "models": [{
                "id": m.id,
                "name": m.name,
                "display_name": m.display_name,
                "specialization": m.specialization,
                "status": status_labels.get(m.status, m.status),
                "base_model": m.base_model,
                "has_api_key": bool(m.api_key),
                "api_endpoint": m.api_endpoint,
                "total_requests": m.total_requests or 0,
                "setup_progress": (m.config_json or {}).get("setup_progress", 100 if m.status == "active" else 0),
            } for m in models]
        }

    # ── update_specialist_prompt ──────────────────────────────────

    def _update_specialist_prompt(self, model_name: str, new_prompt: str) -> dict:
        specialist = self.db.query(SpecialistModel).filter(
            SpecialistModel.name == model_name
        ).first()

        if not specialist:
            specialist = self.db.query(SpecialistModel).filter(
                SpecialistModel.name.ilike(f"%{model_name}%")
            ).first()

        if not specialist:
            available = [m.name for m in self.db.query(SpecialistModel).all()]
            return {
                "error": f"النموذج '{model_name}' غير موجود",
                "available_models": available
            }

        old_len = len(specialist.system_prompt or "")
        specialist.system_prompt = new_prompt
        self.db.commit()

        return {
            "status": "updated",
            "model": model_name,
            "display_name": specialist.display_name,
            "old_prompt_length": old_len,
            "new_prompt_length": len(new_prompt),
            "message": f"✅ تم تحديث System Prompt لـ '{specialist.display_name}' — مُفعَّل فوراً على كل طلب جديد"
        }

    # ── get_training_report ───────────────────────────────────────

    def _get_training_report(self, model_name: str = None) -> dict:
        if model_name:
            specialist = self.db.query(SpecialistModel).filter(
                SpecialistModel.name == model_name
            ).first()
            if not specialist:
                specialist = self.db.query(SpecialistModel).filter(
                    SpecialistModel.name.ilike(f"%{model_name}%")
                ).first()
            if not specialist:
                return {"error": f"النموذج '{model_name}' غير موجود"}

            logs = self.db.query(ModelPerformanceLog).filter(
                ModelPerformanceLog.model_id == specialist.id
            ).order_by(ModelPerformanceLog.created_at.desc()).limit(200).all()

            sessions = self.db.query(TrainingSession).filter(
                TrainingSession.model_id == specialist.id
            ).count()

            if not logs:
                return {
                    "model": model_name,
                    "display_name": specialist.display_name,
                    "status": specialist.status,
                    "total_requests": 0,
                    "training_sessions": sessions,
                    "recommendation": "لا توجد بيانات أداء بعد — النموذج لم يُستخدَم",
                    "action": "جرّب النموذج من لوحة التحكم لجمع بيانات الأداء"
                }

            success_logs = [l for l in logs if l.status == "success"]
            success_rate = (len(success_logs) / len(logs)) * 100
            avg_ms = int(sum(l.response_ms or 0 for l in logs) / len(logs))
            all_issues = []
            for log in logs:
                if log.issues_detected:
                    all_issues.extend(log.issues_detected)

            if success_rate < 70:
                recommendation = "الأداء ضعيف — يحتاج تحديث System Prompt فوراً"
                action = "استخدم update_specialist_prompt بـ prompt محسّن"
            elif success_rate < 85:
                recommendation = "الأداء متوسط — يُنصح بالتحسين"
                action = "راجع common_issues وعدّل System Prompt"
            else:
                recommendation = "الأداء ممتاز ✅"
                action = "استمر في المراقبة الأسبوعية"

            return {
                "model": model_name,
                "display_name": specialist.display_name,
                "status": specialist.status,
                "base_model": specialist.base_model,
                "total_requests": len(logs),
                "success_rate": f"{success_rate:.1f}%",
                "avg_response_ms": avg_ms,
                "common_issues": list(set(all_issues))[:8],
                "training_sessions": sessions,
                "last_trained": specialist.last_trained_at.isoformat() if specialist.last_trained_at else "لم يُدرَّب",
                "recommendation": recommendation,
                "action": action
            }

        else:
            # تقرير عام لكل النماذج
            all_models = self.db.query(SpecialistModel).all()
            if not all_models:
                return {"message": "لا توجد نماذج متخصصة بعد", "total": 0}

            needs_attention = []
            report = []
            total_requests = 0

            for m in all_models:
                logs_count = self.db.query(ModelPerformanceLog).filter(
                    ModelPerformanceLog.model_id == m.id
                ).count()
                total_requests += logs_count

                if m.status == "error":
                    health = "❌ خطأ"
                    needs_attention.append(m.name)
                elif m.status in ("creating", "downloading", "training"):
                    health = "⏳ قيد الإعداد"
                elif logs_count > 0 and (m.success_rate or 0) < 0.85:
                    health = "⚠️ يحتاج تحسين"
                    needs_attention.append(m.name)
                else:
                    health = "✅ جيد"

                report.append({
                    "name": m.name,
                    "display_name": m.display_name,
                    "status": m.status,
                    "health": health,
                    "total_requests": logs_count,
                    "success_rate": f"{(m.success_rate or 0) * 100:.1f}%",
                    "has_api_key": bool(m.api_key),
                })

            return {
                "total_models": len(all_models),
                "active_models": sum(1 for m in all_models if m.status == "active"),
                "total_requests": total_requests,
                "needs_attention": needs_attention,
                "models": report,
                "summary": (
                    f"المنظومة تعمل بـ {len(all_models)} نموذج، "
                    f"{sum(1 for m in all_models if m.status == 'active')} منها نشط. "
                    + (f"تحتاج انتباهاً: {', '.join(needs_attention)}" if needs_attention else "كل النماذج بصحة جيدة.")
                )
            }

    # ── get_model_performance ─────────────────────────────────────

    def _get_model_performance(self, model_name: str) -> dict:
        model = self.db.query(SpecialistModel).filter(
            SpecialistModel.name == model_name
        ).first()
        if not model:
            model = self.db.query(SpecialistModel).filter(
                SpecialistModel.name.ilike(f"%{model_name}%")
            ).first()
        if not model:
            return {"error": f"النموذج '{model_name}' غير موجود"}

        logs = self.db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_name == model_name
        ).order_by(ModelPerformanceLog.created_at.desc()).limit(100).all()

        if not logs:
            return {
                "model": model_name,
                "display_name": model.display_name,
                "status": model.status,
                "total_requests": model.total_requests or 0,
                "message": "لا توجد بيانات أداء تفصيلية بعد"
            }

        avg_response = sum(l.response_ms or 0 for l in logs) / len(logs)
        success_count = sum(1 for l in logs if l.status == "success")
        issues = []
        for log in logs:
            if log.issues_detected:
                issues.extend(log.issues_detected)

        return {
            "model": model_name,
            "display_name": model.display_name,
            "base_model": model.base_model,
            "total_requests": len(logs),
            "success_rate": f"{(success_count / len(logs)) * 100:.1f}%",
            "avg_response_ms": int(avg_response),
            "common_issues": list(set(issues))[:5],
            "recommendation": "يحتاج تدريب إضافي" if success_count / len(logs) < 0.85 else "الأداء جيد ✅"
        }

    # ── get_system_status ─────────────────────────────────────────

    def _get_system_status(self) -> dict:
        status = model_manager.get_status()
        total = self.db.query(SpecialistModel).count()
        active = self.db.query(SpecialistModel).filter(
            SpecialistModel.status == "active"
        ).count()
        creating = self.db.query(SpecialistModel).filter(
            SpecialistModel.status.in_(["creating", "downloading", "training"])
        ).count()

        return {
            **status,
            "specialist_models": {
                "total": total,
                "active": active,
                "in_progress": creating,
                "inactive": total - active - creating,
            }
        }

    # ── helper ────────────────────────────────────────────────────

    def _build_specialist_prompt(self, specialization: str, name: str, knowledge: str) -> str:
        knowledge_section = f"\n\n## معلومات محدَّثة من الإنترنت:\n{knowledge[:2500]}" if knowledge else ""

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
- استخدم أحدث إصدارات المكتبات{knowledge_section}""",

            "voice": f"""أنت {name} — نموذج متخصص في معالجة الصوت واستنساخه من يسرها.

## تخصصك:
- تحويل النص إلى صوت بصوت المستخدم نفسه (voice cloning)
- تحويل الصوت إلى نص بدقة عالية (عربي وإنجليزي)
- تحسين جودة الصوت وتنظيفه

## قواعدك:
- حافظ على طبيعية الصوت والتنغيم
- ادعم اللهجات العربية المختلفة{knowledge_section}""",

            "education": f"""أنت {name} — نموذج متخصص في التعليم والشرح من يسرها.

## تخصصك:
- شرح المفاهيم المعقدة بأسلوب بسيط ومتدرج
- الرد على أسئلة الطلاب بصبر ووضوح
- تقديم أمثلة وتمارين تفاعلية
- التعليم بالعربية والإنجليزية

## قواعدك:
- استخدم أسلوب سقراط (الأسئلة التوجيهية)
- تحقق من فهم الطالب قبل المتابعة
- قدّم مستويات شرح مختلفة (مبتدئ، متوسط، متقدم){knowledge_section}""",

            "image": f"""أنت {name} — نموذج متخصص في الصور والفيديو من يسرها.

## تخصصك:
- توليد صور احترافية بناءً على الوصف النصي
- تحليل الصور وفهم محتواها
- إنشاء prompts فعّالة لنماذج توليد الصور{knowledge_section}""",

            "media": f"""أنت {name} — نموذج متخصص في الميديا وإنتاج المحتوى من يسرها.

## تخصصك:
- كتابة سكريبتات فيديو ومحتوى إبداعي
- إنتاج محتوى سوشيال ميديا
- استراتيجيات المحتوى والتسويق الرقمي

## قواعدك:
- اكتب بأسلوب جذاب يناسب الجمهور المستهدف
- راعِ خصائص كل منصة (YouTube, Instagram, TikTok){knowledge_section}""",

            "business": f"""أنت {name} — نموذج متخصص في الأعمال والإدارة من يسرها.

## تخصصك:
- التخطيط الاستراتيجي وتحليل السوق
- إعداد خطط الأعمال والتقارير
- إدارة المشاريع والفرق

## قواعدك:
- استخدم مناهج إدارية معتمدة (SWOT, OKR, KPI)
- قدّم توصيات عملية قابلة للتنفيذ{knowledge_section}""",
        }

        return base_prompts.get(
            specialization,
            f"""أنت {name} من يسرها للذكاء الاصطناعي.
متخصص في: {specialization}
قدّم مساعدة احترافية ودقيقة في مجال تخصصك.{knowledge_section}"""
        )
