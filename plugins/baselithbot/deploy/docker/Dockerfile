FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Install the browsers to a shared location instead of root's HOME, so the
    # unprivileged runtime user below can actually reach them.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

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
 && playwright install --with-deps chromium \
 && chmod -R a+rX /ms-playwright

# Non-root runtime user (Trivy DS-0002): a container escape from a browser or
# computer-use process should not land on a root shell. Only the writable
# surfaces the bot actually needs are handed over. Both compose volume targets
# (/var/log/baselithbot, /var/lib/baselithbot) are created and chowned here on
# purpose: Docker seeds a fresh named volume from the image directory, keeping
# ownership, but a path missing from the image is created root-owned and would
# be unwritable. The app tree is included because the editable install writes
# its .egg-info there.
RUN groupadd --system --gid 1001 baselith \
 && useradd --system --uid 1001 --gid baselith --home-dir /home/baselith \
            --create-home --shell /usr/sbin/nologin baselith \
 && mkdir -p /var/log/baselithbot /var/lib/baselithbot \
 && chown -R baselith:baselith /var/log/baselithbot /var/lib/baselithbot \
                               /app /home/baselith

ENV BASELITHBOT_HEADLESS=true \
    BASELITHBOT_AUDIT_LOG=/var/log/baselithbot/computer_use.jsonl \
    HOME=/home/baselith

USER baselith

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/baselithbot/status || exit 1

CMD ["python", "backend.py"]
