"""
Seed script — System 1 (YESARHA Core Admin Backend).
Creates default super_admin, default AI model, and default agents.
Run once: python -m app.seed
"""
from app.db.session import SessionLocal, engine
from app.models import *  # noqa: F401,F403
from app.db.session import Base
from app.core.security import hash_password
from app.models.user import Admin
from app.models.ai import AIModel, Agent


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # ── Default Super Admin ──
        existing = db.query(Admin).filter(Admin.email == "admin@yesarha.ai").first()
        if not existing:
            admin = Admin(
                email="admin@yesarha.ai",
                password_hash=hash_password("ChangeMe123!"),
                full_name="Super Admin",
                role="super_admin",
                permissions=["models", "agents", "analytics", "system", "admins"],
                status="active",
                preferred_language="ar"
            )
            db.add(admin)
            db.commit()
            print("✅ Created default super_admin: admin@yesarha.ai / ChangeMe123!")
        else:
            print("⚠️  Super admin already exists, skipping.")

        # ── Default AI Model ──
        model = db.query(AIModel).filter(AIModel.name == "qwen3:8b").first()
        if not model:
            model = AIModel(
                name="qwen3:8b",
                version="8b",
                type="reasoning",
                status="active",
                is_default=True,
                endpoint_url="http://ollama:11434"
            )
            db.add(model)
            db.commit()
            db.refresh(model)
            print("✅ Created default model: qwen3:8b")
        else:
            print("⚠️  Model qwen3:8b already exists, skipping.")

        # ── Default Agents ──
        default_agents = [
            ("Planner Agent",  "planner",  "You are the Planner agent of YESARHA Core. Break the user's request into a clear, numbered, actionable plan. Be concise and concrete."),
            ("Executor Agent", "executor", "You are the Executor agent of YESARHA Core. Given a plan or instruction, produce the concrete output/result. Do not over-explain."),
            ("Critic Agent",   "critic",   "You are the Critic agent of YESARHA Core. Evaluate the given result for correctness, quality, and completeness. Respond with a short verdict and a score from 0 to 1."),
        ]

        for name, agent_type, system_prompt in default_agents:
            existing_agent = db.query(Agent).filter(Agent.agent_type == agent_type).first()
            if not existing_agent:
                agent = Agent(
                    name=name,
                    model_id=model.id,
                    agent_type=agent_type,
                    config_json={"system_prompt": system_prompt},
                    status="active"
                )
                db.add(agent)
                db.commit()
                print(f"✅ Created agent: {name} ({agent_type})")
            else:
                print(f"⚠️  Agent '{agent_type}' already exists, skipping.")

    finally:
        db.close()


if __name__ == "__main__":
    run()
