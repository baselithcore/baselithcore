FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libnss3 libxss1 libasound2 libatk1.0-0 libatk-bridge2.0-0 libgtk-3-0 \
    libgbm1 libdrm2 fonts-liberation \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY core/ core/
COPY plugins/ plugins/
COPY backend.py ./

RUN pip install -e ".[dev]" \
 && pip install playwright>=1.45.0 playwright-stealth>=1.0.6 \
                pyautogui>=0.9.54 mss>=9.0.1 Pillow>=10.0.0 \
                httpx>=0.27.0 psutil>=5.9.0 \
 && playwright install --with-deps chromium

ENV BASELITHBOT_HEADLESS=true \
    BASELITHBOT_AUDIT_LOG=/var/log/baselithbot/computer_use.jsonl

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/baselithbot/status || exit 1

CMD ["python", "backend.py"]
