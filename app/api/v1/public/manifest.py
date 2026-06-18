"""
Dashboard manifest — describes all available modules, endpoints, and field schemas
in both Arabic and English so the dashboard can build itself dynamically.
"""
from fastapi import APIRouter, Depends
from app.core.config import settings
from app.core.deps import get_current_admin
from app.core.responses import success
from app.core.i18n import L

router = APIRouter(prefix="/dashboard", tags=["Manifest"])


def _f(type_: str, ar: str, en: str, **kwargs) -> dict:
    field = {"type": type_, "label": L(ar, en)}
    field.update(kwargs)
    return field


def _dropdown(ar: str, en: str, source: str, value_field: str = "id", label_field: str = "name", **kwargs) -> dict:
    return _f("dropdown_ref", ar, en, source=source, value_field=value_field, label_field=label_field, **kwargs)


p = settings.API_PREFIX


@router.get("/manifest")
def get_manifest(_admin=Depends(get_current_admin)):
    return success({
        "system": {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "description": L(
                "نظام تشغيل الذكاء الاصطناعي التنفيذي — يسرها",
                "Executive Intelligence Operating System — YESARHA"
            )
        },
        "auth": {
            "login_endpoint": f"{p}/auth/login",
            "refresh_endpoint": f"{p}/auth/refresh",
            "me_endpoint": f"{p}/auth/me",
            "token_type": "bearer"
        },
        "modules": [
            {
                "key": "dashboard",
                "label": L("الرئيسية", "Dashboard"),
                "icon": "home",
                "endpoints": {
                    "summary": f"{p}/admin/dashboard/summary"
                }
            },
            {
                "key": "models",
                "label": L("النماذج", "Models"),
                "icon": "cpu",
                "endpoints": {
                    "list":   f"{p}/admin/models",
                    "create": f"{p}/admin/models",
                    "update": f"{p}/admin/models/{{id}}",
                    "delete": f"{p}/admin/models/{{id}}"
                },
                "schema": {
                    "name":         _f("string",  "اسم النموذج",         "Model Name",      required=True),
                    "version":      _f("string",  "الإصدار",             "Version",         nullable=True),
                    "type":         _f("enum",    "نوع النموذج",          "Model Type",
                                       options=["reasoning","planning","general","vision","custom"]),
                    "status":       _f("enum",    "الحالة",              "Status",
                                       options=["active","inactive"]),
                    "is_default":   _f("boolean", "نموذج افتراضي",       "Default Model"),
                    "endpoint_url": _f("string",  "رابط Ollama",         "Ollama Endpoint", nullable=True),
                    "description":  _f("textarea","وصف النموذج",         "Description",     nullable=True),
                }
            },
            {
                "key": "agents",
                "label": L("الوكلاء", "Agents"),
                "icon": "brain",
                "endpoints": {
                    "list":        f"{p}/admin/agents",
                    "create":      f"{p}/admin/agents",
                    "update":      f"{p}/admin/agents/{{id}}",
                    "delete":      f"{p}/admin/agents/{{id}}",
                    "performance": f"{p}/admin/agents/{{id}}/performance",
                    "logs":        f"{p}/admin/agents/{{id}}/logs"
                },
                "schema": {
                    "name":         _f("string",    "اسم الوكيل",        "Agent Name",    required=True),
                    "model_id":     _dropdown(      "النموذج",           "Model",
                                                    source=f"{p}/admin/models"),
                    "agent_type":   _f("enum",      "نوع الوكيل",        "Agent Type",
                                       options=["planner","executor","critic","custom"]),
                    "system_prompt":_f("textarea",  "System Prompt",     "System Prompt", nullable=True),
                    "temperature":  _f("number",    "درجة الإبداعية",    "Temperature",   nullable=True, min=0, max=2),
                    "status":       _f("enum",      "الحالة",            "Status",
                                       options=["active","inactive"]),
                }
            },
            {
                "key": "test_tools",
                "label": L("أدوات الاختبار", "Test Tools"),
                "icon": "flask",
                "endpoints": {
                    "chat": f"{p}/admin/test/chat",
                    "run":  f"{p}/admin/test/run"
                },
                "schema": {
                    "chat": {
                        "message":       _f("string",       "الرسالة",              "Message",       required=True),
                        "model_id":      _dropdown(         "النموذج (اختياري)",    "Model (opt.)",
                                                            source=f"{p}/admin/models", nullable=True),
                        "agent_id":      _dropdown(         "الوكيل (اختياري)",     "Agent (opt.)",
                                                            source=f"{p}/admin/agents", nullable=True),
                        "system_prompt": _f("textarea",     "System Prompt (اختياري)", "System Prompt (opt.)", nullable=True)
                    },
                    "run": {
                        "goal":         _f("string",   "الهدف / المهمة",           "Goal / Task",   required=True),
                        "use_planner":  _f("boolean",  "تفعيل Planner Agent",      "Use Planner",   default=True),
                        "use_critic":   _f("boolean",  "تفعيل Critic Agent",       "Use Critic",    default=True)
                    }
                },
                "note": L(
                    "أدوات اختبار داخلية — لا يوجد احتساب كريدت",
                    "Internal test tools — no credit deduction"
                )
            },
            {
                "key": "analytics",
                "label": L("التحليلات", "Analytics"),
                "icon": "chart-bar",
                "endpoints": {
                    "tool_performance":    f"{p}/admin/analytics/tool-performance",
                    "intent_distribution": f"{p}/admin/analytics/intent-distribution",
                    "failures":            f"{p}/admin/analytics/failures",
                    "insights":            f"{p}/admin/analytics/insights",
                    "goals":               f"{p}/admin/goals"
                }
            },
            {
                "key": "system",
                "label": L("موارد النظام", "System Resources"),
                "icon": "server",
                "polling_interval_seconds": 10,
                "endpoints": {
                    "resources": f"{p}/admin/system/resources",
                    "health":    f"{p}/admin/system/health"
                }
            },
            {
                "key": "admins",
                "label": L("إدارة الأدمنز", "Admins Management"),
                "icon": "users",
                "required_role": "super_admin",
                "endpoints": {
                    "list":   f"{p}/admin/admins",
                    "create": f"{p}/admin/admins",
                    "update": f"{p}/admin/admins/{{id}}",
                    "delete": f"{p}/admin/admins/{{id}}"
                },
                "schema": {
                    "email":              _f("email",    "البريد الإلكتروني",   "Email",              required=True),
                    "full_name":          _f("string",   "الاسم الكامل",        "Full Name",          nullable=True),
                    "password":           _f("password", "كلمة المرور",         "Password",           required=True),
                    "role":               _f("enum",     "الدور",               "Role",
                                             options=["super_admin","admin","viewer"]),
                    "permissions":        _f("multi_select", "الصلاحيات",       "Permissions",
                                             options=["models","agents","analytics","system","admins"]),
                    "preferred_language": _f("enum",     "اللغة المفضلة",       "Preferred Language",
                                             options=["ar","en"])
                }
            }
        ],
        "error_codes": {
            "UNAUTHORIZED":       L("غير مصرح",                    "Unauthorized"),
            "FORBIDDEN":          L("ليس لديك صلاحية",             "Forbidden"),
            "NOT_FOUND":          L("العنصر غير موجود",            "Not Found"),
            "ALREADY_EXISTS":     L("موجود مسبقاً",                "Already Exists"),
            "VALIDATION_ERROR":   L("خطأ في البيانات",             "Validation Error"),
            "INVALID_CREDENTIALS":L("بريد أو كلمة مرور خاطئة",    "Invalid Credentials"),
            "TOKEN_EXPIRED":      L("انتهت صلاحية الجلسة",         "Token Expired"),
            "TOKEN_INVALID":      L("جلسة غير صالحة",              "Invalid Token"),
            "MODEL_UNAVAILABLE":  L("النموذج غير متاح",            "Model Unavailable"),
            "OLLAMA_UNREACHABLE": L("لا يمكن الوصول لـ Ollama",    "Ollama Unreachable"),
            "INTERNAL_ERROR":     L("خطأ داخلي في الخادم",         "Internal Server Error")
        }
    })
