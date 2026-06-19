from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

from app.core.config import settings

BASE_DIR = Path(__file__).resolve().parents[2]


def _build_engine():
    if settings.DATABASE_URL.startswith("postgresql"):
        try:
            eng = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return eng
        except Exception:
            if not settings.USE_SQLITE_FALLBACK:
                raise
            # Fallback to SQLite
            pass

    sqlite_path = BASE_DIR / settings.SQLITE_PATH
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False}
    )


engine = _build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
