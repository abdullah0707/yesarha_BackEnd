FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Core requirements (سريعة) ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Voice requirements (ثقيلة — تُبنى في layer منفصل) ──
# VOICE_INSTALL=true لتفعيل التثبيت عند الـ build
ARG VOICE_INSTALL=false
RUN if [ "$VOICE_INSTALL" = "true" ]; then \
      pip install --no-cache-dir \
        torch torchaudio --index-url https://download.pytorch.org/whl/cu121 && \
      pip install --no-cache-dir openai-whisper TTS; \
    fi

COPY app ./app

ENV PYTHONUNBUFFERED=1
ENV XTTS_MODEL_PATH=/app/data/xtts

EXPOSE 8000

CMD ["sh", "-c", "python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
