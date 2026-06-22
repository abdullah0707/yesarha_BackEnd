"""
نماذج قاعدة البيانات لمنظومة التعليم
المحتوى يُزامَن (sync) من باك إند المستخدمين عبر content_id الخارجي —
وليس إدخالاً يدوياً من لوحة التحكم. نفس content_id المُستخدَم في باك
إند المستخدمين هو نفسه المفتاح هنا، فيسهل التحديث (upsert) عند أي تعديل.
"""
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey, Index
from datetime import datetime
from app.db.session import Base


class SyncedContent(Base):
    """
    محتوى تعليمي مُزامَن من باك إند المستخدمين.
    external_content_id = نفس الـ ID المستخدَم في قاعدة بيانات باك إند المستخدمين.
    chunks_json = البنية بعد التطبيع إلى [{"section": ..., "part": N, "text": ...}]
                  (تُبنى تلقائياً من raw_payload عبر نفس منطق external_content.py)
    """
    __tablename__ = "synced_content"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_content_id = Column(String, nullable=False, unique=True, index=True)

    title = Column(String, nullable=True)            # اختياري، للعرض في اللوحة فقط
    raw_payload = Column(JSON, nullable=False)         # البيانات الخام كما وصلت من باك إند المستخدمين
    chunks_json = Column(JSON, default=list)           # بعد التطبيع — جاهزة للبحث المحلي مباشرة

    synced_at = Column(DateTime, default=datetime.utcnow)       # آخر مرة وصل فيها تحديث
    created_at = Column(DateTime, default=datetime.utcnow)


class StudentQuestion(Base):
    """سجل أسئلة الطلاب وردود النموذج — لتحليل الأداء لاحقاً"""
    __tablename__ = "student_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_content_id = Column(String, nullable=False, index=True)
    specialist_id = Column(Integer, nullable=True, index=True)   # أي نموذج متخصص أجاب

    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    used_sections = Column(JSON, default=list)

    response_ms = Column(Integer, default=0)
    language = Column(String, default="ar")

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
