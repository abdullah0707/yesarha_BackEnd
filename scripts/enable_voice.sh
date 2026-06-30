#!/usr/bin/env bash
# enable_voice.sh — تفعيل نموذج الصوت (Whisper STT + XTTS-v2 TTS)
# الاستخدام: bash scripts/enable_voice.sh
set -e

echo "🎙️  بناء Yesarha Backend مع تفعيل نموذج الصوت..."
echo "⚠️  الحجم ~4-6 GB — قد يستغرق 10-20 دقيقة أول مرة"
echo ""

# فعّل BuildKit للاستفادة من pip cache
export DOCKER_BUILDKIT=1

docker compose build \
  --build-arg VOICE_INSTALL=true \
  backend

echo ""
echo "✅ تم البناء — أعِد تشغيل الـ backend:"
echo "   docker compose up -d backend"
echo ""
echo "📋 للتحقق من الجاهزية بعد التشغيل:"
echo "   curl http://localhost:8000/api/v1/specialist/voice/status"
