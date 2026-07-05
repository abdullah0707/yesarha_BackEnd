"""
Tech Manager — Deterministic Scanner
فحص حقيقي بدون LLM: أنماط الأمان + المكتبات المفقودة
لا هلوسة، لا اختراع مشاكل — فقط ما هو موجود فعلاً في الكود.
"""
import json
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path("/app/app")
REQUIREMENTS_FILE = Path("/app/requirements.txt")

_SKIP_DIRS = frozenset({
    "__pycache__", ".git", "migrations", "tests", "test",
    "tech_manager", "alembic", ".mypy_cache", ".pytest_cache",
})

# (pattern, description, severity)
# الأنماط مرتبة من الأخطر للأقل خطورة
SECURITY_PATTERNS = [
    # مفاتيح API مكشوفة
    (r'sk-[A-Za-z0-9]{20,}',                                            "Hardcoded OpenAI API key",               "critical"),
    (r'gsk_[A-Za-z0-9]{20,}',                                           "Hardcoded Groq API key",                 "critical"),
    # بيانات اعتماد ثابتة
    (r'(?:password|passwd)\s*=\s*["\'][^"\']{1,40}["\']',              "Hardcoded password",                     "critical"),
    (r'secret_key\s*=\s*["\'][^"\']{1,40}["\']',                       "Hardcoded secret key",                   "critical"),
    # إعدادات JWT ضعيفة
    (r'JWT_SECRET_KEY\s*:\s*str\s*=\s*["\'](?:CHANGE_ME|secret|test|default|password)[^"\']*["\']',
                                                                          "Weak JWT_SECRET_KEY default value",      "critical"),
    # تشغيل كود خطير
    (r'\beval\s*\(',                                                      "eval() — arbitrary code execution",      "critical"),
    (r'\bexec\s*\(',                                                      "exec() — arbitrary code execution",      "critical"),
    (r'subprocess\.(?:call|run|Popen)\b[^)]*shell\s*=\s*True',         "shell=True — command injection risk",    "critical"),
    (r'pickle\.loads?\s*\(',                                             "pickle.loads — deserialization risk",    "warning"),
    # إعدادات خاطئة
    (r'verify\s*=\s*False',                                              "SSL certificate verification disabled",  "critical"),
    (r'CORS_ORIGINS\s*(?::|=)\s*(?:\[)?\s*["\*]',                      "CORS wildcard — all origins allowed",    "warning"),
    (r'\bDEBUG\s*(?::|=)\s*True',                                       "Debug mode enabled",                     "warning"),
    # JWT ضعيف — HS256 مع مفاتيح قصيرة
    (r'ACCESS_TOKEN_EXPIRE_MINUTES\s*=\s*\d{5,}',                       "Token expiry too long (>= 10000 min)",   "warning"),
]


def scan_security(scope: str = "all") -> dict:
    """
    فحص أمني حقيقي بـ regex — بدون LLM.
    يُرجع النتائج مرتبة حسب الخطورة.
    """
    findings: list[dict] = []
    files_scanned = 0
    errors = []

    # تحديد نطاق الفحص
    if scope == "all":
        root = PROJECT_ROOT
    else:
        root = PROJECT_ROOT / scope.lstrip("/")

    if not root.exists():
        return {"error": f"Directory not found: {scope}", "scanned_files": 0, "findings": []}

    for py_file in root.rglob("*.py"):
        # تخطي المجلدات المحظورة
        if any(skip in py_file.parts for skip in _SKIP_DIRS):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            files_scanned += 1

            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                for pat, desc, sev in SECURITY_PATTERNS:
                    if re.search(pat, line, re.IGNORECASE):
                        rel_path = str(py_file.relative_to(PROJECT_ROOT))
                        findings.append({
                            "file": rel_path,
                            "line": lineno,
                            "line_content": stripped[:250],
                            "issue": desc,
                            "severity": sev,
                        })
                        break  # واحدة لكل سطر تكفي
        except Exception as ex:
            errors.append(str(ex))

    # ترتيب: critical أولاً
    findings.sort(key=lambda f: (0 if f["severity"] == "critical" else 1, f["file"], f["line"]))

    return {
        "scanned_files": files_scanned,
        "total_findings": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        "findings": findings,
        "scan_errors": errors,
    }


def check_packages() -> dict:
    """
    يقارن requirements.txt بالمكتبات المثبتة فعلاً.
    لا يخترع مكتبات — فقط ما هو في requirements.txt حرفياً.
    """
    # جلب المكتبات المثبتة
    try:
        result = subprocess.run(
            ["pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"error": f"pip list failed: {result.stderr[:200]}"}
        installed_raw = json.loads(result.stdout)
        installed = {
            _normalize_pkg(p["name"]): p["version"]
            for p in installed_raw
        }
    except Exception as ex:
        return {"error": f"Cannot run pip: {ex}"}

    # قراءة requirements.txt
    if not REQUIREMENTS_FILE.exists():
        return {"error": f"requirements.txt not found at {REQUIREMENTS_FILE}"}

    missing, present = [], []
    req_lines = REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines()

    for raw_line in req_lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # استخراج اسم الحزمة بدون version spec
        pkg_name = re.split(r"[>=<![\s@;]", line)[0].strip()
        if not pkg_name:
            continue

        norm = _normalize_pkg(pkg_name)
        if norm in installed:
            present.append({"name": pkg_name, "version": installed[norm]})
        else:
            missing.append(pkg_name)

    return {
        "installed_count": len(installed),
        "requirements_count": len(present) + len(missing),
        "present_count": len(present),
        "missing_count": len(missing),
        "missing": missing,
        "present": present,
    }


def read_file_with_lines(relative_path: str) -> dict:
    """
    يقرأ ملفاً بأرقام الأسطر — للوكيل يستخدمها لتحديد الكود الدقيق.
    """
    # ابحث في app أولاً، ثم في /app
    candidates = [
        PROJECT_ROOT / relative_path,
        Path("/app") / relative_path,
    ]
    for target in candidates:
        if target.exists() and target.is_file():
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
                return {
                    "path": relative_path,
                    "lines_count": len(lines),
                    "content": numbered,
                    "raw": content,  # للاستخدام الداخلي في التحقق
                }
            except Exception as ex:
                return {"error": f"Cannot read {relative_path}: {ex}"}
    return {"error": f"File not found: {relative_path}"}


def list_directory(relative_path: str = "") -> dict:
    """يسرد محتويات مجلد."""
    target = PROJECT_ROOT / relative_path if relative_path else PROJECT_ROOT
    if not target.exists():
        alt = Path("/app") / relative_path
        if alt.exists():
            target = alt
        else:
            return {"error": f"Directory not found: {relative_path}"}
    if not target.is_dir():
        return {"error": f"Not a directory: {relative_path}"}
    try:
        entries = []
        for item in sorted(target.iterdir()):
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"path": relative_path or ".", "entries": entries}
    except Exception as ex:
        return {"error": str(ex)}


def _normalize_pkg(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")
