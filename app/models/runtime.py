"""
نموذج الإعدادات الديناميكية — تُخزَّن في DB وتُعدَّل من لوحة التحكم.
فقط DATABASE_URL و JWT_SECRET_KEY يبقيان في .env
كل شيء آخر (Ollama, Redis, SearXNG, ...) يكون هنا.
"""
from sqlalchemy import Column, String, Text, Boolean, DateTime
from datetime import datetime
from app.db.session import Base


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key         = Column(String, primary_key=True, index=True)
    value       = Column(Text, nullable=True)
    value_type  = Column(String, default="string")   # string | bool | int | float | json
    group       = Column(String, default="general")  # connections | models | security | general
    label_ar    = Column(String, nullable=True)
    label_en    = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    is_secret   = Column(Boolean, default=False)     # لا يُعرض بالكامل في الـ API
    is_readonly = Column(Boolean, default=False)     # للعرض فقط
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
