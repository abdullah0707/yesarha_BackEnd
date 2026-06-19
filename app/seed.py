"""Seed script — YESARHA Core v3"""
from app.db.session import SessionLocal, engine
from app.db.session import Base
from app.models import *  # noqa
from app.core.security import hash_password
from app.models.user import Admin
from app.models.ai import AIModel, Agent
from app.models.specialist import SpecialistModel

ALL_PERMISSIONS = ["models", "agents", "analytics", "system", "admins", "specialists", "core"]


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # ── Super Admin ──
        if not db.query(Admin).filter(Admin.email == "admin@yesarha.ai").first():
            db.add(Admin(
                email="admin@yesarha.ai",
                password_hash=hash_password("ChangeMe123!"),
                full_name="Super Admin",
                role="super_admin",
                permissions=ALL_PERMISSIONS,
                status="active",
                preferred_language="ar"
            ))
            db.commit()
            print("✅ Created super_admin: admin@yesarha.ai / ChangeMe123!")
        else:
            print("⚠️  Super admin exists, skipping.")

        # ── Yesarha Core Model (qwen3:8b) ──
        core_model = db.query(AIModel).filter(AIModel.name == "qwen3:8b").first()
        if not core_model:
            core_model = AIModel(
                name="qwen3:8b",
                version="8b",
                type="reasoning",
                status="active",
                is_default=True,
                endpoint_url="http://ollama:11434",
                description="Yesarha Core — العقل الرئيسي للمنظومة"
            )
            db.add(core_model)
            db.commit()
            db.refresh(core_model)
            print("✅ Created core model: qwen3:8b (Yesarha Core)")
        else:
            print("⚠️  Core model exists, skipping.")

        # ── Default Agents ──
        agents = [
            ("Planner Agent",  "planner",
             "أنت وكيل التخطيط في Yesarha Core. مهمتك تحليل الطلب وتقسيمه لخطوات واضحة."),
            ("Executor Agent", "executor",
             "أنت وكيل التنفيذ في Yesarha Core. مهمتك تنفيذ الخطة وإنتاج النتيجة."),
            ("Critic Agent",   "critic",
             "أنت وكيل التقييم في Yesarha Core. قيّم النتيجة وأعط درجة من 0 إلى 1 مع ملاحظات."),
        ]
        for name, atype, prompt in agents:
            if not db.query(Agent).filter(Agent.agent_type == atype).first():
                db.add(Agent(
                    name=name, model_id=core_model.id,
                    agent_type=atype,
                    config_json={"system_prompt": prompt},
                    status="active"
                ))
                db.commit()
                print(f"✅ Created agent: {name}")
            else:
                print(f"⚠️  Agent '{atype}' exists, skipping.")

        print("\n🚀 YESARHA Core v3.0 is ready!")
        print("=" * 50)
        print("Admin:    admin@yesarha.ai")
        print("Password: ChangeMe123!  ← غيّر فوراً!")
        print("Docs:     http://localhost:8000/docs")
        print("=" * 50)

    finally:
        db.close()


if __name__ == "__main__":
    run()
