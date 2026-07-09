from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from app.db.session import Base


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id           = Column(Integer, primary_key=True, index=True)
    ip           = Column(String(45), index=True, nullable=False)
    event_type   = Column(String(50), nullable=False)
    severity     = Column(String(10), nullable=False, default="medium")
    path         = Column(String(500), nullable=True)
    user_agent   = Column(String(500), nullable=True)
    details      = Column(String(1000), nullable=True)
    auto_blocked = Column(Boolean, default=False)
    created_at   = Column(DateTime, default=func.now())


class BlockedIP(Base):
    __tablename__ = "blocked_ips"

    id               = Column(Integer, primary_key=True, index=True)
    ip               = Column(String(45), unique=True, index=True, nullable=False)
    reason           = Column(String(300), nullable=False)
    event_type       = Column(String(50), nullable=False)
    severity         = Column(String(10), nullable=False, default="high")
    blocked_at       = Column(DateTime, default=func.now())
    expires_at       = Column(DateTime, nullable=True)       # None = دائم حتى رفع الحجب
    unblocked_at     = Column(DateTime, nullable=True)
    unblocked_by_id  = Column(Integer, nullable=True)
    is_active        = Column(Boolean, default=True, index=True)

    # حقول قرار AI
    is_ai_decision   = Column(Boolean, default=False)        # هل القرار من AI أم rule-based؟
    ai_reasoning     = Column(Text, nullable=True)           # تفسير AI بالعربي
    ai_review_status = Column(String(20), nullable=True)     # pending | confirmed | cancelled


class TrustedIP(Base):
    """IPs موثوقة (باك إند العملاء وغيرها) — لا تُطبَّق عليها أي قواعد حجب."""
    __tablename__ = "trusted_ips"

    id         = Column(Integer, primary_key=True, index=True)
    ip         = Column(String(45), unique=True, index=True, nullable=False)
    label      = Column(String(200), nullable=True)          # وصف مثل "باك إند العملاء"
    added_by   = Column(Integer, nullable=True)              # admin_id
    created_at = Column(DateTime, default=func.now())
