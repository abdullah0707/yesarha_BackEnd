from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime

from app.db.session import Base


class Admin(Base):
    """
    System 1 admins — completely separate from any customer-facing users.
    role: 'super_admin' | 'admin' | 'viewer'
    permissions: list of module keys e.g. ["models","agents","analytics","system","admins"]
    """
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=True)

    role = Column(String, default="admin", nullable=False)
    # 'super_admin' | 'admin' | 'viewer'

    permissions = Column(JSON, default=list)
    # e.g. ["models","agents","analytics","system","admins"]
    # super_admin: all modules always
    # admin/viewer: restricted by this list

    status = Column(String, default="active", nullable=False)
    # 'active' | 'suspended'

    preferred_language = Column(String, default="ar")
    # 'ar' | 'en'

    last_login_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
