"""
Test configuration — يعمل مع SQLite في الذاكرة بدون Postgres أو Ollama.

env vars يجب تُعيَّن قبل أي import من app.
"""
import os
from unittest.mock import MagicMock, patch

# ── Override settings BEFORE any app import ────────────────────────────────────
os.environ.setdefault("DATABASE_URL",         "sqlite:///./test_yesarha.db")
os.environ.setdefault("JWT_SECRET_KEY",       "test-jwt-secret-not-for-production")
os.environ.setdefault("INTERNAL_API_KEY",     "test-internal-key-12345")
os.environ.setdefault("REDIS_URL",            "redis://localhost:6379/0")
os.environ.setdefault("SEARXNG_URL",          "http://localhost:8080")
os.environ.setdefault("OLLAMA_BASE_URL",      "http://localhost:11434")
os.environ.setdefault("SEED_ADMIN_PASSWORD",  "TestAdmin123!")
os.environ.setdefault("VRAM_TOTAL_GB",        "8.0")
os.environ.setdefault("CORE_MODEL",           "qwen3:8b")
os.environ.setdefault("CORS_ORIGINS",         '["*"]')

# ── Now import app (SQLite engine built here) ──────────────────────────────────
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base, get_db
from app.main import create_app
from app.core.security import hash_password, create_access_token
from app.models.user import Admin
from app.models.specialist import SpecialistModel

# ── Shared test DB (file-based SQLite, cleaned up per session) ─────────────────
TEST_DB_URL = "sqlite:///./test_yesarha.db"
test_engine  = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSession  = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

ALL_PERMISSIONS = ["models", "agents", "analytics", "system", "admins", "specialists", "core"]


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """قاعدة بيانات جديدة لكل جلسة اختبار."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    try:
        os.remove("./test_yesarha.db")
    except (FileNotFoundError, PermissionError):
        pass


@pytest.fixture(scope="session")
def db():
    """جلسة DB مشتركة بين كل الاختبارات."""
    session = TestSession()
    yield session
    session.close()


def override_get_db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def app():
    """FastAPI app مع DB override."""
    _app = create_app()
    _app.dependency_overrides[get_db] = override_get_db
    return _app


@pytest.fixture(scope="session")
def client(app):
    """HTTP TestClient مشترك."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Admin Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def super_admin(db):
    """Super admin محفوظ في DB للاختبارات."""
    admin = db.query(Admin).filter(Admin.email == "test_super@yesarha.ai").first()
    if not admin:
        admin = Admin(
            email="test_super@yesarha.ai",
            password_hash=hash_password("TestAdmin123!"),
            full_name="Test Super Admin",
            role="super_admin",
            permissions=ALL_PERMISSIONS,
            status="active",
            preferred_language="ar",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return admin


@pytest.fixture(scope="session")
def admin_token(super_admin):
    """JWT token للـ super admin."""
    return create_access_token(super_admin.id, super_admin.role)


@pytest.fixture(scope="session")
def auth_headers(admin_token):
    """Authorization headers جاهزة للاستخدام."""
    return {"Authorization": f"Bearer {admin_token}"}


# ── Specialist Fixture ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def active_specialist(db):
    """نموذج متخصص نشط للاختبارات التي تحتاجه."""
    spec = db.query(SpecialistModel).filter(
        SpecialistModel.name == "test-code-specialist"
    ).first()
    if not spec:
        spec = SpecialistModel(
            name="test-code-specialist",
            display_name="Test Code Specialist",
            specialization="code",
            base_model="qwen2.5-coder:7b",
            status="active",
            api_key="yesk_code_test1234567890abcdef12345678",
            api_endpoint="/specialist/ask",
            system_prompt="أنت مساعد برمجة. ساعد المستخدم في الكود.",
        )
        db.add(spec)
        db.commit()
        db.refresh(spec)
    return spec
