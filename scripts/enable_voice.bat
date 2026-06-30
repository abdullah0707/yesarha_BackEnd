@echo off
REM enable_voice.bat — تفعيل نموذج الصوت على Windows
REM الاستخدام: scripts\enable_voice.bat

echo 🎙️  بناء Yesarha Backend مع تفعيل نموذج الصوت...
echo ⚠️  الحجم ~4-6 GB — قد يستغرق 10-20 دقيقة أول مرة
echo.

SET DOCKER_BUILDKIT=1

docker compose build --build-arg VOICE_INSTALL=true backend

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ فشل البناء — تحقق من إن Docker Desktop شغّال وعنده اتصال بالإنترنت
    exit /b 1
)

echo.
echo ✅ تم البناء — أعِد تشغيل الـ backend:
echo    docker compose up -d backend
echo.
echo 📋 للتحقق من الجاهزية بعد التشغيل:
echo    curl http://localhost:8000/api/v1/specialist/voice/status
