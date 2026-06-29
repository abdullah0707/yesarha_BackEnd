"""
Auto Monitor — Core يراقب النماذج تلقائياً ويتحرك عند الحاجة
يشغّل background tasks دورية:
  - كل 6 ساعات: يفحص أداء كل النماذج
  - إذا نموذج أداؤه < 70%: يُحدِّث System Prompt تلقائياً
  - كل أسبوع: تقرير شامل + إعادة بحث للمعلومات الجديدة
  - عند بدء التشغيل: يتحقق من صحة كل النماذج
"""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.specialist import SpecialistModel, ModelPerformanceLog, CoreTask, TrainingSession

logger = logging.getLogger("yesarha.monitor")


# ── Constants ─────────────────────────────────────────────────────────────────

POOR_PERFORMANCE_THRESHOLD = 0.70     # أقل من 70% → تحديث تلقائي فوري
WEAK_PERFORMANCE_THRESHOLD  = 0.85    # أقل من 85% → تسجيل تحذير
MIN_REQUESTS_FOR_EVAL       = 5       # أقل من 5 طلبات → لا يُقيَّم
CHECK_INTERVAL_HOURS        = 6       # الفحص كل 6 ساعات
WEEKLY_RETRAIN_DAYS         = 7       # إعادة تدريب كاملة كل أسبوع


# ── Core Monitor Class ────────────────────────────────────────────────────────

class CoreAutoMonitor:
    """
    العقل الصامت خلف Yesarha Core.
    يعمل في الخلفية بدون تدخل — ينبّه، يُصلح، يُحسِّن.
    """

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self):
        """يُشغَّل عند بدء التطبيق من main.py"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        logger.info("🧠 Core Auto Monitor started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("🛑 Core Auto Monitor stopped")

    async def _main_loop(self):
        """الحلقة الرئيسية — تعمل طالما التطبيق شغّال"""
        # انتظر 30 ثانية بعد البدء حتى تكتمل قاعدة البيانات
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._run_cycle()
            except Exception as e:
                logger.error(f"Monitor cycle error: {e}")

            # انتظر 6 ساعات قبل الدورة التالية
            await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)

    async def _run_cycle(self):
        """دورة كاملة للمراقبة"""
        logger.info("🔄 Core Monitor: starting performance check cycle")
        loop = asyncio.get_running_loop()

        # شغّل كل العمليات في thread منفصل لتجنب حجب event loop
        await loop.run_in_executor(None, self._check_all_models)
        await loop.run_in_executor(None, self._check_weekly_retrain)
        await loop.run_in_executor(None, self._cleanup_old_tasks)

    # ── Performance Check ─────────────────────────────────────────────────────

    def _check_all_models(self):
        """يفحص أداء كل النماذج ويتصرف حسب النتيجة"""
        db = SessionLocal()
        try:
            models = db.query(SpecialistModel).filter(
                SpecialistModel.status == "active"
            ).all()

            for model in models:
                self._evaluate_model(model, db)

        except Exception as e:
            logger.error(f"Check all models error: {e}")
        finally:
            db.close()

    def _evaluate_model(self, model: SpecialistModel, db: Session):
        """يُقيِّم نموذجاً واحداً ويتصرف"""
        # آخر 100 طلب
        logs = db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_id == model.id
        ).order_by(ModelPerformanceLog.created_at.desc()).limit(100).all()

        if len(logs) < MIN_REQUESTS_FOR_EVAL:
            return  # بيانات غير كافية

        success_count = sum(1 for l in logs if l.status == "success")
        success_rate = success_count / len(logs)

        # جمع المشاكل الشائعة
        all_issues = []
        for log in logs:
            if log.issues_detected:
                all_issues.extend(log.issues_detected)

        # تحديث success_rate في الداتابيز
        model.success_rate = success_rate
        db.commit()

        if success_rate < POOR_PERFORMANCE_THRESHOLD:
            logger.warning(
                f"⚠️ {model.name}: poor performance {success_rate:.1%} "
                f"— triggering auto-fix"
            )
            self._auto_fix_model(model, all_issues, success_rate, db)

        elif success_rate < WEAK_PERFORMANCE_THRESHOLD:
            logger.info(f"📊 {model.name}: weak performance {success_rate:.1%} — logged")
            self._log_task(db, "performance_eval", model.id, {
                "success_rate": success_rate,
                "issues": list(set(all_issues))[:5],
                "action": "monitoring",
            })

        else:
            logger.info(f"✅ {model.name}: healthy {success_rate:.1%}")

    # ── Auto Fix ──────────────────────────────────────────────────────────────

    def _auto_fix_model(
        self,
        model: SpecialistModel,
        issues: list,
        success_rate: float,
        db: Session
    ):
        """
        Core يُصلح النموذج تلقائياً:
        1. يبحث على الإنترنت عن حلول للمشاكل المرصودة
        2. يبني System Prompt محسَّن
        3. يطبّقه فوراً
        4. يسجّل TrainingSession للتوثيق
        """
        from app.services.web.searxng_client import WebIntelligence
        from app.core.intelligence.tool_executor import ToolExecutor

        try:
            # بحث محدَّد بالمشاكل
            web = WebIntelligence(db=db)
            common_issues = list(set(issues))[:5]

            search_query = (
                f"how to improve AI {model.specialization} model performance "
                f"prompt engineering best practices"
            )
            if common_issues:
                search_query += f" issues: {', '.join(common_issues[:3])}"

            knowledge = web.search(search_query, max_results=5)

            # بناء System Prompt محسَّن
            executor = ToolExecutor(db=db)
            knowledge_text = "\n".join([
                r.get("snippet", "") for r in knowledge if r.get("snippet")
            ])
            improved_prompt = executor._build_specialist_prompt(
                model.specialization,
                model.display_name,
                knowledge_text
            )

            # إضافة تعليمات تعالج المشاكل المرصودة
            if common_issues:
                improved_prompt += (
                    f"\n\n## تعليمات خاصة (أُضيفت تلقائياً بواسطة Core):\n"
                    f"المشاكل التي رصدها Core في أدائك السابق:\n"
                    + "\n".join(f"- {issue}" for issue in common_issues[:5])
                    + "\nاحرص على تجنّب هذه المشاكل بشكل خاص."
                )

            # تطبيق الـ prompt الجديد
            old_prompt = model.system_prompt or ""
            model.system_prompt = improved_prompt
            model.last_trained_at = datetime.utcnow()
            db.commit()

            # توثيق جلسة التدريب
            session = TrainingSession(
                model_id=model.id,
                session_type="prompt",
                data_sources=[r.get("url", "") for r in knowledge if r.get("url")],
                status="completed",
                before_score=success_rate,
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
            db.add(session)

            # تسجيل CoreTask
            self._log_task(db, "auto_fix", model.id, {
                "trigger": "poor_performance",
                "success_rate_before": success_rate,
                "issues_found": common_issues,
                "prompt_length_before": len(old_prompt),
                "prompt_length_after": len(improved_prompt),
                "knowledge_sources": len(knowledge),
                "action": "system_prompt_updated",
            }, status="done")

            logger.info(
                f"✅ Auto-fixed {model.name}: "
                f"new prompt {len(improved_prompt)} chars, "
                f"{len(knowledge)} sources used"
            )

        except Exception as e:
            logger.error(f"Auto-fix failed for {model.name}: {e}")
            self._log_task(db, "auto_fix", model.id, {
                "error": str(e),
                "action": "failed",
            }, status="failed")

    # ── Weekly Retrain ────────────────────────────────────────────────────────

    def _check_weekly_retrain(self):
        """يُعيد تدريب النماذج التي مضى على تدريبها أكثر من أسبوع"""
        db = SessionLocal()
        try:
            week_ago = datetime.utcnow() - timedelta(days=WEEKLY_RETRAIN_DAYS)

            models_to_retrain = db.query(SpecialistModel).filter(
                SpecialistModel.status == "active",
                (SpecialistModel.last_trained_at == None) |  # noqa
                (SpecialistModel.last_trained_at < week_ago)
            ).all()

            for model in models_to_retrain:
                logger.info(f"📅 Weekly retrain: {model.name}")
                self._retrain_with_fresh_data(model, db)

        except Exception as e:
            logger.error(f"Weekly retrain error: {e}")
        finally:
            db.close()

    def _retrain_with_fresh_data(self, model: SpecialistModel, db: Session):
        """إعادة تدريب بأحدث معلومات من الإنترنت"""
        from app.services.web.searxng_client import WebIntelligence
        from app.core.intelligence.tool_executor import ToolExecutor

        try:
            web = WebIntelligence(db=db)
            knowledge = web.search_for_specialist(model.specialization)

            executor = ToolExecutor(db=db)
            new_prompt = executor._build_specialist_prompt(
                model.specialization,
                model.display_name,
                knowledge.get("knowledge_base", "")
            )

            model.system_prompt = new_prompt
            model.training_data_sources = knowledge.get("sources", [])
            model.last_trained_at = datetime.utcnow()
            model.next_training_at = datetime.utcnow() + timedelta(days=WEEKLY_RETRAIN_DAYS)
            db.commit()

            self._log_task(db, "weekly_retrain", model.id, {
                "sources_count": len(knowledge.get("sources", [])),
                "prompt_length": len(new_prompt),
                "action": "weekly_prompt_refresh",
            }, status="done")

            logger.info(f"✅ Weekly retrain done: {model.name}")

        except Exception as e:
            logger.error(f"Retrain failed for {model.name}: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log_task(
        self,
        db: Session,
        task_type: str,
        model_id: int | None,
        output: dict,
        status: str = "done"
    ):
        """يسجّل CoreTask في الداتابيز"""
        try:
            task = CoreTask(
                task_type=task_type,
                target_model_id=model_id,
                status=status,
                output_data=output,
                completed_at=datetime.utcnow(),
            )
            db.add(task)
            db.commit()
        except Exception as e:
            logger.error(f"Log task error: {e}")

    def _cleanup_old_tasks(self):
        """يحذف CoreTasks الأقدم من 30 يوماً لتوفير مساحة"""
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=30)
            db.query(CoreTask).filter(
                CoreTask.created_at < cutoff,
                CoreTask.status.in_(["done", "failed"])
            ).delete()
            db.commit()
        except Exception:
            pass
        finally:
            db.close()


# Singleton
core_monitor = CoreAutoMonitor()
