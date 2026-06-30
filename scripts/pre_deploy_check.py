#!/usr/bin/env python3
"""
pre_deploy_check.py — فحص جاهزية الإنتاج قبل النشر
الاستخدام:
    python scripts/pre_deploy_check.py --env .env.production

يتحقق من:
  1. كل متغيرات البيئة الإلزامية موجودة وغير افتراضية
  2. docker-compose.prod.yml صالح YAML
  3. Caddyfile موجود
  4. Docker متاح
  5. DNS resolution للدومين (اختياري)
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

PASS  = "✅"
FAIL  = "❌"
WARN  = "⚠️ "
INFO  = "ℹ️ "

results = []


def check(label: str, ok: bool, detail: str = "", critical: bool = True):
    icon = PASS if ok else (FAIL if critical else WARN)
    line = f"{icon}  {label}"
    if detail:
        line += f"\n       {detail}"
    results.append((ok, critical, line))
    print(line)


# ─────────────────────────────────────────────────────────────────────────────

def load_env(env_file: str) -> dict:
    env = {}
    path = Path(env_file)
    if not path.exists():
        print(f"{FAIL}  Env file not found: {env_file}")
        sys.exit(1)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


REQUIRED_VARS = {
    "API_DOMAIN":          (r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",  "domain without protocol, e.g. api.yesarha.ai"),
    "POSTGRES_USER":       (r"^.{1,}$",                           "any non-empty string"),
    "POSTGRES_PASSWORD":   (r"^.{12,}$",                          "at least 12 characters"),
    "POSTGRES_DB":         (r"^.{1,}$",                           "any non-empty string"),
    "REDIS_PASSWORD":      (r"^.{12,}$",                          "at least 12 characters"),
    "JWT_SECRET_KEY":      (r"^.{32,}$",                          "at least 32 characters"),
    "INTERNAL_API_KEY":    (r"^.{16,}$",                          "at least 16 characters"),
    "CORS_ORIGINS":        (r'^\[".+"\]$',                        'e.g. ["https://dashboard.yourdomain.com"]'),
    "SEED_ADMIN_PASSWORD": (r"^.{8,}$",                           "at least 8 characters"),
}

FORBIDDEN_DEFAULTS = [
    "CHANGE_ME", "change_this", "change_internal_key",
    "STRONG_RANDOM", "your_", "example.com",
]


def check_env_vars(env: dict):
    print("\n── متغيرات البيئة ─────────────────────────────────────────────")
    for var, (pattern, hint) in REQUIRED_VARS.items():
        val = env.get(var, "")
        if not val:
            check(f"  {var}", False, f"مفقود — {hint}")
            continue
        if any(d.lower() in val.lower() for d in FORBIDDEN_DEFAULTS):
            check(f"  {var}", False, f"قيمة افتراضية غير آمنة: {val[:30]}...", critical=True)
            continue
        if not re.match(pattern, val):
            check(f"  {var}", False, f"تنسيق غير صحيح — {hint}")
        else:
            check(f"  {var}", True)

    # إضافي: تحذير لو CORS_ORIGINS = ["*"]
    if env.get("CORS_ORIGINS", "").strip() in ('["*"]', "['*']", "*"):
        check("  CORS_ORIGINS wildcard", False, 'استخدام ["*"] في الإنتاج يسمح لأي موقع بالاتصال', critical=False)


def check_compose_file():
    print("\n── ملفات Docker Compose ────────────────────────────────────────")
    prod_compose = BASE_DIR / "docker-compose.prod.yml"
    check("  docker-compose.prod.yml موجود", prod_compose.exists())
    if prod_compose.exists():
        try:
            data = yaml.safe_load(prod_compose.read_text(encoding="utf-8"))
            services = list(data.get("services", {}).keys())
            required = {"postgres", "redis", "ollama", "backend", "caddy"}
            missing = required - set(services)
            check(
                f"  Services: {', '.join(services)}",
                not missing,
                f"خدمات ناقصة: {missing}" if missing else ""
            )
            # تأكد أن postgres/redis ليس لديهم ports مفتوحة للخارج
            for svc in ("postgres", "redis"):
                svc_cfg = data["services"].get(svc, {})
                has_ports = bool(svc_cfg.get("ports"))
                check(
                    f"  {svc}: لا ports مفتوحة للخارج",
                    not has_ports,
                    f"تحذير: {svc} لديه ports مفتوحة — خطر أمني" if has_ports else "",
                    critical=False
                )
        except Exception as e:
            check("  docker-compose.prod.yml YAML صالح", False, str(e))

    caddyfile = BASE_DIR / "Caddyfile"
    check("  Caddyfile موجود", caddyfile.exists())
    if caddyfile.exists():
        content = caddyfile.read_text(encoding="utf-8")
        check("  Caddyfile: reverse_proxy موجود", "reverse_proxy" in content)
        check("  Caddyfile: HTTPS security headers", "Strict-Transport-Security" in content)


def check_docker():
    print("\n── Docker ──────────────────────────────────────────────────────")
    has_docker = shutil.which("docker") is not None
    check("  docker CLI متاح", has_docker)
    if has_docker:
        try:
            r = subprocess.run(
                ["docker", "info"],
                capture_output=True, timeout=10
            )
            check("  Docker daemon يعمل", r.returncode == 0)
        except Exception:
            check("  Docker daemon يعمل", False)

        has_compose = shutil.which("docker-compose") or True
        try:
            r = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True, timeout=5
            )
            check("  Docker Compose v2 متاح", r.returncode == 0)
        except Exception:
            check("  Docker Compose v2 متاح", False)

        try:
            r = subprocess.run(
                ["docker", "info", "--format", "{{.Runtimes}}"],
                capture_output=True, timeout=5
            )
            has_nvidia = b"nvidia" in r.stdout.lower()
            check("  NVIDIA runtime (GPU)", has_nvidia, "الـ GPU ضروري لـ Ollama بأداء جيد", critical=False)
        except Exception:
            pass


def check_dns(domain: str):
    print("\n── DNS ─────────────────────────────────────────────────────────")
    try:
        ip = socket.gethostbyname(domain)
        check(f"  {domain} → {ip}", True)
    except socket.gaierror:
        check(
            f"  {domain} DNS resolution",
            False,
            "الدومين غير محلول — Let's Encrypt لن يتمكن من إصدار شهادة",
            critical=False
        )


def check_required_files():
    print("\n── ملفات المشروع ───────────────────────────────────────────────")
    files = [
        ("Dockerfile",            True),
        ("requirements.txt",      True),
        ("requirements-voice.txt", True),
        ("docker-compose.yml",    True),
        ("docker-compose.prod.yml", True),
        ("Caddyfile",             True),
        ("DEPLOYMENT.md",         False),
        ("searxng/settings.yml",  True),
        ("data/",                 False),
    ]
    for fname, critical in files:
        path = BASE_DIR / fname
        check(f"  {fname}", path.exists(), critical=critical)


# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Yesarha Core — pre-deployment check")
    parser.add_argument("--env", default=".env.production", help="مسار ملف البيئة")
    parser.add_argument("--skip-dns", action="store_true", help="تجاهل فحص DNS")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║   YESARHA Core — فحص جاهزية الإنتاج                        ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    env_path = BASE_DIR / args.env
    env = load_env(str(env_path))

    check_required_files()
    check_env_vars(env)
    check_compose_file()
    check_docker()

    if not args.skip_dns and env.get("API_DOMAIN"):
        check_dns(env["API_DOMAIN"])

    # ── Summary ───────────────────────────────────────────────────────────────
    total      = len(results)
    passed     = sum(1 for ok, _, _ in results if ok)
    critical_fails = [line for ok, crit, line in results if not ok and crit]

    print("\n" + "═" * 64)
    print(f"النتيجة: {passed}/{total} فحص نجح")

    if critical_fails:
        print(f"\n{FAIL}  {len(critical_fails)} مشكلة حرجة يجب إصلاحها قبل النشر:")
        for line in critical_fails:
            print(f"   {line.strip()}")
        print("\n")
        sys.exit(1)
    else:
        print(f"\n{PASS}  جميع الفحوصات الحرجة نجحت — المشروع جاهز للنشر!")
        print("   شغّل: docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
