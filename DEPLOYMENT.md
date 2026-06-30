# YESARHA Core — Production Deployment Guide

## المتطلبات

- سيرفر Linux بـ GPU (لـ Ollama) — أو CPU فقط لو رضيت بأداء أبطأ
- Docker + Docker Compose v2
- دومين يشير بـ A record على IP السيرفر (مطلوب لـ HTTPS التلقائي)
- المنافذ 80 و443 مفتوحة في الـ firewall

## 1. إعداد المتغيرات

```bash
cp .env.production.example .env.production
```

افتح `.env.production` واملأ:

| المتغير | الوصف |
|---|---|
| `API_DOMAIN` | الدومين اللي هيشاور على السيرفر، مثال `api.yesarha.ai` |
| `POSTGRES_PASSWORD` / `REDIS_PASSWORD` | كلمات مرور قوية — ولّدها بـ `openssl rand -hex 24` |
| `JWT_SECRET_KEY` / `INTERNAL_API_KEY` | أسرار قوية — `openssl rand -hex 32` |
| `CORS_ORIGINS` | رابط لوحة التحكم فقط، **ليس** `["*"]` في الإنتاج |

لا ترفع `.env.production` لأي مكان — هو في `.gitignore` بالفعل.

## 2. التشغيل

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

هذا يشغّل: `postgres`, `redis`, `ollama`, `searxng`, `backend`, و`caddy`.
**لا** خدمة منها (غير caddy) عندها port مفتوح للخارج — كله على شبكة `internal` الداخلية.

## 3. HTTPS التلقائي (Caddy + Let's Encrypt)

`Caddyfile` يستخدم `API_DOMAIN` تلقائياً ليطلب ويجدّد شهادة TLS من Let's Encrypt
بدون أي إعداد يدوي — فقط تأكد إن الدومين يشاور على IP السيرفر **قبل** التشغيل.

تحقق من السجلات لو الشهادة لم تصدر:

```bash
docker compose -f docker-compose.prod.yml logs caddy -f
```

أسباب شائعة للفشل: الدومين غير مشاوِر بعد، أو المنفذ 80 مغلق (Let's Encrypt
يحتاج HTTP-01 challenge عبر 80 حتى لو الهدف HTTPS).

## 4. نموذج أولي (Pull) لـ Ollama

أول مرة، اسحب نموذج Core يدوياً (الإنشاء التلقائي للمتخصصين يسحب نماذجهم تلقائياً من اللوحة):

```bash
docker exec -it yesarha_ollama ollama pull qwen3:8b
```

## 5. ربط لوحة التحكم

في الداشبورد، اذهب لصفحة **الاتصال** وأدخل:

```
https://api.yourdomain.com
```

(بدون `/api/v1` — يُضاف تلقائياً). اضغط "اختبار الاتصال".

## 6. النسخ الاحتياطي

البيانات الدائمة في Docker volumes: `postgres_data`, `voice_models`, و`./data`
(system prompt overrides, voice samples). انسخها احتياطياً دورياً:

```bash
docker exec yesarha_postgres pg_dump -U yesarha yesarha_core > backup_$(date +%F).sql
```

## 7. Rate Limiting

كل الـ endpoints العامة (`/specialist/ask`, `/specialist/education/ask`,
`/specialist/voice/*`, `/specialist/content/sync`, `/auth/login`) محمية
بـ rate limit يُحسَب حسب `X-API-Key` (أو IP لو مفيش مفتاح). القيمة الافتراضية
`RATE_LIMIT_PER_MINUTE=60` — عدّلها في `.env.production` حسب الحاجة.

## 8. تحديث النشر

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build backend
```

`postgres`/`redis`/`ollama` غالباً مش محتاجين rebuild — فقط `backend`.
