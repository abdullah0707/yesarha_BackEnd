# syntax=docker/dockerfile:1.4
FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Core requirements (BuildKit pip cache = سريع جداً عند rebuild) ──
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ── Voice requirements (ثقيلة — layer منفصل + cache) ──
# VOICE_INSTALL=true لتفعيل التثبيت عند الـ build
# VOICE_CACHE_BUST=<timestamp> لكسر cache الـ Docker layer عند الحاجة
ARG VOICE_INSTALL=false
ARG VOICE_CACHE_BUST=1
COPY requirements-voice.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    echo "VOICE_INSTALL=$VOICE_INSTALL | BUST=$VOICE_CACHE_BUST" && \
    if [ "$VOICE_INSTALL" = "true" ]; then \
      pip install -r requirements-voice.txt && \
      pip install habibi-tts --no-deps && \
      pip install f5-tts --no-deps; \
    fi

COPY app ./app

ENV PYTHONUNBUFFERED=1
ENV XTTS_MODEL_PATH=/app/data/xtts

EXPOSE 8000

CMD ["sh", "-c", "python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
