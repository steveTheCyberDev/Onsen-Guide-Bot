# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Onsen Guide Bot — backend image
#
# Base: python:3.12-slim (Debian Bookworm)
# Workdir: /app  (all source lives here at the same paths as locally)
#
# NOTE: chromadb>=0.6.0 depends on onnxruntime — expect a ~1-2 GB image.
#       That is expected and acceptable for V1.
#
# SECRETS — never baked in. All keys are injected by the runtime host
# (Railway env panel). The list below is documentation only:
#   OPENAI_API_KEY
#   ANTHROPIC_API_KEY         (reserved for future Claude migration)
#   GOOGLE_MAPS_API_KEY
#   RAKUTEN_APP_ID
#   RAKUTEN_ACCESS_KEY
#   RAKUTEN_HOTEL_URL
#   CORS_ORIGINS              (comma-separated; pydantic-settings parses the list)
#
# ChromaDB persistence — the image does NOT bake in chroma_db/.
# Railway mounts a persistent disk at /app/chroma_db at runtime.
# Run ingest.py once as a one-off job after first deploy to populate it.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Keeps Python output unbuffered so Railway / Docker logs are immediate.
ENV PYTHONUNBUFFERED=1

# In a container, persist ChromaDB to the volume mounted at /app/chroma_db.
# Locally (no override) the app uses the relative default and reads backend/chroma_db.
# Both the app and the ingest job read settings.chroma_path, so they always agree.
ENV CHROMA_PATH=/app/chroma_db

WORKDIR /app

# ── 1. Install dependencies ──────────────────────────────────────────────────
# Copy only the requirements file first so Docker can cache this layer.
# A code change that doesn't touch requirements.txt skips this expensive step.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── 2. Copy application source ───────────────────────────────────────────────
# Copies only the packages needed to run (and ingest).
# chroma_db/, .env, .venv/, tests/, and result*.json are excluded via
# .dockerignore — they must not appear in the image.
COPY api/          api/
COPY agent/        agent/
COPY services/     services/
COPY vectorstore/  vectorstore/
COPY core/         core/
# scripts/ + data/ are included so the same image can run ingest.py
# as a one-off Railway job without needing a separate image.
COPY scripts/      scripts/
COPY data/         data/

# ── 3. Expose & launch ───────────────────────────────────────────────────────
EXPOSE 8000

# Bind to 0.0.0.0 so Railway's load balancer can reach the container.
# Respect $PORT (Railway injects it); fall back to 8000 for local runs.
# Shell form is required so ${PORT} is expanded at runtime.
# --workers 1 keeps the embedded ChromaDB PersistentClient single-process safe.
# Increase workers only after migrating ChromaDB to HTTP-server mode (V3).
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
