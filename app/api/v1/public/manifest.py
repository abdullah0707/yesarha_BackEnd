from fastapi import APIRouter, Depends
from app.core.config import settings
from app.core.deps import get_current_admin
from app.core.responses import success
from app.core.i18n import L

router = APIRouter(prefix="/dashboard", tags=["Manifest"])
p = settings.API_PREFIX


def _f(type_: str, ar: str, en: str, **kw) -> dict:
    return {"type": type_, "label": L(ar, en), **kw}


def _drop(ar, en, source, value="id", label="name", **kw):
    return _f("dropdown_ref", ar, en, source=source, value_field=value, label_field=label, **kw)


@router.get("/manifest")
def get_manifest(_admin=Depends(get_current_admin)):
    return success({
        "system": {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "core_model": settings.CORE_MODEL,
            "description": L(
                "نظام تشغيل الذكاء الاصطناعي التنفيذي — يسرها",
                "Executive AI Operating System — YESARHA"
            )
        },
        "auth": {
            "login_endpoint":   f"{p}/auth/login",
            "refresh_endpoint": f"{p}/auth/refresh",
            "me_endpoint":      f"{p}/auth/me",
            "token_type":       "bearer"
        },
        "streaming": {
            "core_chat_stream": f"{p}/core/chat",
            "specialist_stream": f"{p}/core/specialist/chat",
            "protocol": "SSE",
            "events": ["thinking","token","tool_start","tool_executing","tool_done","stats","done","error"]
        },
        "modules": [
            {
                "key": "dashboard",
                "label": L("الرئيسية", "Dashboard"),
                "icon": "home",
                "endpoints": {"summary": f"{p}/admin/dashboard/summary"}
            },
            {
                "key": "core_chat",
                "label": L("Yesarha Core", "Yesarha Core"),
                "icon": "brain-circuit",
                "description": L("العقل الرئيسي — مع Tool Calling وبحث الإنترنت", "Core Intelligence — with Tool Calling & Web Search"),
                "endpoints": {
                    "chat": f"{p}/core/chat",
                    "specialist_chat": f"{p}/core/specialist/chat",
                },
                "schema": {
                    "chat": {
                        "message":       _f("string",  "الرسالة", "Message", required=True),
                        "stream":        _f("boolean", "بث فوري", "Streaming", default=True),
                        "enable_tools":  _f("boolean", "تفعيل الأدوات (بحث، إنشاء نماذج)", "Enable Tools", default=True),
                    }
                }
            },
            {
                "key": "specialists",
                "label": L("النماذج المتخصصة", "Specialist Models"),
                "icon": "layers",
                "description": L("نماذج متخصصة تُنشأ بواسطة Core", "Specialist models created by Core"),
                "endpoints": {
                    "list":        f"{p}/admin/specialists",
                    "create":      f"{p}/admin/specialists",
                    "update":      f"{p}/admin/specialists/{{id}}",
                    "delete":      f"{p}/admin/specialists/{{id}}",
                    "performance": f"{p}/admin/specialists/{{id}}/performance",
                    "train":       f"{p}/admin/specialists/{{id}}/trigger-training",
                },
                "schema": {
                    "name":          _f("string", "اسم النموذج (بالإنجليزية)", "Model Name (English)", required=True, placeholder="yesarha-code"),
                    "display_name":  _f("string", "الاسم المعروض", "Display Name", required=True),
                    "display_name_ar": _f("string", "الاسم بالعربية", "Arabic Name", nullable=True),
                    "specialization": _f("enum",  "التخصص", "Specialization",
                                         options=["code","voice","image","education","custom"]),
                    "description":   _f("textarea", "الوصف", "Description", nullable=True),
                }
            },
            {
                "key": "models",
                "label": L("النماذج الأساسية", "Base Models"),
                "icon": "cpu",
                "endpoints": {
                    "list":   f"{p}/admin/models",
                    "create": f"{p}/admin/models",
                    "update": f"{p}/admin/models/{{id}}",
                    "delete": f"{p}/admin/models/{{id}}",
                },
                "schema": {
                    "name":         _f("string",  "اسم النموذج", "Model Name", required=True),
                    "type":         _f("enum",    "النوع", "Type",
                                       options=["reasoning","planning","general","vision","custom"]),
                    "status":       _f("enum",    "الحالة", "Status",
                                       options=["active","inactive"]),
                    "is_default":   _f("boolean", "نموذج افتراضي", "Default Model"),
                    "endpoint_url": _f("string",  "رابط Ollama", "Ollama Endpoint", nullable=True),
                }
            },
            {
                "key": "agents",
                "label": L("الوكلاء", "Agents"),
                "icon": "bot",
                "endpoints": {
                    "list":        f"{p}/admin/agents",
                    "create":      f"{p}/admin/agents",
                    "update":      f"{p}/admin/agents/{{id}}",
                    "delete":      f"{p}/admin/agents/{{id}}",
                    "performance": f"{p}/admin/agents/{{id}}/performance",
                    "logs":        f"{p}/admin/agents/{{id}}/logs",
                }
            },
            {
                "key": "test_tools",
                "label": L("أدوات الاختبار", "Test Tools"),
                "icon": "flask-conical",
                "endpoints": {
                    "chat": f"{p}/admin/test/chat",
                    "run":  f"{p}/admin/test/run",
                },
                "note": L("بدون احتساب كريدت — للاختبار الداخلي فقط", "No billing — internal testing only")
            },
            {
                "key": "analytics",
                "label": L("التحليلات", "Analytics"),
                "icon": "bar-chart-3",
                "endpoints": {
                    "tool_performance":    f"{p}/admin/analytics/tool-performance",
                    "intent_distribution": f"{p}/admin/analytics/intent-distribution",
                    "failures":            f"{p}/admin/analytics/failures",
                    "insights":            f"{p}/admin/analytics/insights",
                    "goals":               f"{p}/admin/analytics/goals",
                }
            },
            {
                "key": "system",
                "label": L("موارد النظام", "System Resources"),
                "icon": "server",
                "polling_interval_seconds": 10,
                "endpoints": {
                    "resources": f"{p}/admin/system/resources",
                    "health":    f"{p}/admin/system/health",
                }
            },
            {
                "key": "admins",
                "label": L("إدارة الأدمنز", "Admins"),
                "icon": "users",
                "required_role": "super_admin",
                "endpoints": {
                    "list":   f"{p}/admin/admins",
                    "create": f"{p}/admin/admins",
                    "update": f"{p}/admin/admins/{{id}}",
                    "delete": f"{p}/admin/admins/{{id}}",
                },
                "schema": {
                    "email":       _f("email",    "البريد الإلكتروني", "Email", required=True),
                    "password":    _f("password", "كلمة المرور", "Password", required=True),
                    "full_name":   _f("string",   "الاسم الكامل", "Full Name", nullable=True),
                    "role":        _f("enum",     "الدور", "Role",
                                      options=["super_admin","admin","viewer"]),
                    "permissions": _f("multi_select", "الصلاحيات", "Permissions",
                                      options=["models","agents","analytics","system","admins","specialists","core"]),
                }
            }
        ],
        "error_codes": {
            "UNAUTHORIZED":        L("غير مصرح", "Unauthorized"),
            "FORBIDDEN":           L("ليس لديك صلاحية", "Forbidden"),
            "NOT_FOUND":           L("غير موجود", "Not Found"),
            "ALREADY_EXISTS":      L("موجود مسبقاً", "Already Exists"),
            "VALIDATION_ERROR":    L("خطأ في البيانات", "Validation Error"),
            "INVALID_CREDENTIALS": L("بيانات خاطئة", "Invalid Credentials"),
            "TOKEN_INVALID":       L("جلسة غير صالحة", "Invalid Token"),
            "MODEL_UNAVAILABLE":   L("النموذج غير متاح", "Model Unavailable"),
            "OLLAMA_UNREACHABLE":  L("Ollama غير متاح", "Ollama Unreachable"),
            "INTERNAL_ERROR":      L("خطأ داخلي", "Internal Server Error"),
        }
    })
