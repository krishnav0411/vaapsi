# Vaapsi public-demo image: FastAPI + built React dashboard (frontend/dist)
# + SQLite. The store lives in data/, which .dockerignore excludes — so a
# container always boots with no database and seed-on-boot (VAAPSI_PUBLIC_DEMO=1)
# builds the sanitized demo store from scripts/seed_demo.py. No secrets are
# copied into the image: .env never ships, and demo mode refuses to boot
# with credentials anyway (app/demo_mode.py).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first — this layer caches across code-only changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code, the built SPA, and the demo seeder (seed-on-boot
# imports scripts.seed_demo). tools/ is a 53MB Windows-only cloudflared
# binary — useless in a Linux container, so it stays out of the image.
COPY app ./app
COPY scripts/seed_demo.py ./scripts/seed_demo.py
COPY frontend/dist ./frontend/dist

EXPOSE 8000

# Render/Koyeb-style: honor the injected $PORT, default 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
