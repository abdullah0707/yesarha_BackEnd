"""
نماذج قاعدة البيانات للنماذج المتخصصة وذكاء Core
"""
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, Float, Boolean, ForeignKey
from datetime import datetime
from app.db.session import Base


class SpecialistModel(Base):
    """نموذج متخصص تم إنشاؤه بواسطة Yesarha Core"""
    __tablename__ = "specialist_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)           # "yesarha-code", "yesarha-voice"
    display_name = Column(String, nullable=False)                 # "Yesarha Code"
    display_name_ar = Column(String, nullable=True)              # "يسرها للكود"
    specialization = Column(String, nullable=False)              # "code" | "voice" | "image" | "education"
    description = Column(Text, nullable=True)
    description_ar = Column(Text, nullable=True)

    # النموذج الأساسي من Ollama
    base_model = Column(String, nullable=False)                  # "qwen2.5-coder:7b"
    ollama_model_name = Column(String, nullable=True)            # بعد تحميله

    # الضبط والإعداد
    system_prompt = Column(Text, nullable=True)                  # System prompt المتخصص (يُولَّد تلقائياً أو يُحرَّر يدوياً)
    system_prompt_ar = Column(Text, nullable=True)
    config_json = Column(JSON, default=dict)                     # إعدادات إضافية
    tools_enabled = Column(JSON, default=list)                   # الأدوات المتاحة

    # مصدر بيانات خارجي (لنماذج مثل التعليم التي تتصل بباك إند المستخدمين)
    content_source_url = Column(String, nullable=True)           # رابط API لجلب المحتوى، مثل https://users-api.yesarha.ai/content
    content_source_api_key = Column(String, nullable=True)       # مفتاح المصادقة عند الاتصال بهذا المصدر (إن وُجد)
    uses_external_content = Column(Boolean, default=False)       # هل هذا النموذج يعتمد على مصدر بيانات خارجي؟

    # دور النموذج في المنظومة (أساس Phase A — Orchestrator)
    agent_role = Column(String, default="specialist")
    # "specialist"   — نموذج متخصص يُستدعى من الـ Orchestrator
    # "orchestrator" — العقل المركزي الذي يُحلّل ويُوجّه (Phase A)
    # "hybrid"       — يعمل بالدورين حسب السياق
    can_call_specialists = Column(JSON, default=list)           # قائمة IDs المتخصصين الذين يمكن لهذا النموذج استدعاؤهم

    # الحالة
    status = Column(String, default="creating")
    # creating | downloading | training | active | inactive | error
    is_public_api = Column(Boolean, default=True)               # هل له API عام؟
    api_endpoint = Column(String, nullable=True)                 # /api/v1/specialist/code
    api_key = Column(String, nullable=True, unique=True, index=True)  # مفتاح API للمستخدمين الخارجيين

    # المعلومات التقنية
    vram_required_gb = Column(Float, default=5.0)
    avg_response_ms = Column(Integer, default=0)
    total_requests = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)

    # معلومات الإنشاء
    created_by_core = Column(Boolean, default=True)              # أنشأه Core تلقائياً؟
    training_data_sources = Column(JSON, default=list)           # مصادر التدريب من البحث
    last_trained_at = Column(DateTime, nullable=True)
    next_training_at = Column(DateTime, nullable=True)           # التدريب الأسبوعي التالي

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SpecialistBundle(Base):
    """
    حزمة نماذج متخصصة تحت مفتاح API واحد.
    Admin يُنشئها من لوحة التحكم، يختار النماذج، النظام يُولّد المفتاح.
    """
    __tablename__ = "specialist_bundles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    api_key = Column(String, nullable=True, unique=True, index=True)

    # قائمة IDs النماذج المتخصصة في هذه الحزمة — JSON Array
    specialist_ids = Column(JSON, default=list)

    # هل يمر الطلب عبر الـ Orchestrator لتحديد المتخصص تلقائياً؟
    use_orchestrator = Column(Boolean, default=True)

    status = Column(String, default="active")       # active | inactive
    total_requests = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebSearchCache(Base):
    """cache لنتائج البحث على الإنترنت"""
    __tablename__ = "web_search_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_hash = Column(String, nullable=False, unique=True, index=True)
    query = Column(Text, nullable=False)
    results_json = Column(JSON, default=list)
    source = Column(String, default="searxng")
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CoreTask(Base):
    """مهام Core التلقائية (بحث أسبوعي، تدريب، تقييم)"""
    __tablename__ = "core_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String, nullable=False)
    # "weekly_scan" | "model_training" | "performance_eval" | "model_creation"

    target_model_id = Column(Integer, ForeignKey("specialist_models.id"), nullable=True)
    status = Column(String, default="pending")  # pending | running | done | failed
    priority = Column(Integer, default=5)        # 1=عاجل، 10=منخفض

    input_data = Column(JSON, default=dict)
    output_data = Column(JSON, default=dict)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ModelPerformanceLog(Base):
    """سجل أداء النماذج لتقارير Core"""
    __tablename__ = "model_performance_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("specialist_models.id"), nullable=True)
    model_name = Column(String, nullable=False)

    request_id = Column(String, nullable=True)
    user_input = Column(Text, nullable=True)
    model_output = Column(Text, nullable=True)
    response_ms = Column(Integer, default=0)
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)

    # تقييم Core التلقائي
    quality_score = Column(Float, nullable=True)      # 0-1
    issues_detected = Column(JSON, default=list)      # مشاكل رصدها Core
    improvement_notes = Column(Text, nullable=True)   # ملاحظات للتحسين

    language = Column(String, default="ar")           # ar | en | mixed
    status = Column(String, default="success")        # success | failed | timeout

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class TrainingSession(Base):
    """جلسات تدريب النماذج"""
    __tablename__ = "training_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("specialist_models.id"), nullable=False)

    session_type = Column(String, default="lora")    # lora | prompt | rag
    training_data = Column(JSON, default=list)        # البيانات المستخدمة للتدريب
    data_sources = Column(JSON, default=list)         # مصادر البيانات (URLs بحث)

    status = Column(String, default="pending")
    before_score = Column(Float, nullable=True)       # الأداء قبل التدريب
    after_score = Column(Float, nullable=True)        # الأداء بعد التدريب
    improvement_percent = Column(Float, nullable=True)

    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
