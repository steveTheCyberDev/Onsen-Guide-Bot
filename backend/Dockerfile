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
# After a deploy that changes data, run the one-off ingest job to populate it:
#   python -m scripts.ingest_all   (onsen regions + Layer 2 knowledge base)
# Both sub-ingests are idempotent (upsert). The KB docs ship at
# /app/data/knowledge (via COPY data/ + DATA_PATH below) and settings.kb_data_dir
# resolves there automatically, so no extra env var is required.
# IMPORTANT: run this BEFORE flipping ASK_ENABLED, or ask mode answers against an
# empty KB collection and always returns the "I don't know" fallback.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Keeps Python output unbuffered so Railway / Docker logs are immediate.
ENV PYTHONUNBUFFERED=1

# In a container, persist ChromaDB to the volume mounted at /app/chroma_db.
# Locally (no override) the app uses the relative default and reads backend/chroma_db.
# Both the app and the ingest job read settings.chroma_path, so they always agree.
ENV CHROMA_PATH=/app/chroma_db

# Onsen data dir for the ingest scripts. COPY data/ data/ (below) lands at
# /app/data, so pin DATA_PATH there. Locally (no override) settings.data_dir
# falls back to backend/data. Mirrors CHROMA_PATH above.
ENV DATA_PATH=/app/data

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
# scripts/ + data/ are included so the same image can run the ingest jobs
# (scripts.ingest_all) as a one-off Railway job without needing a separate image.
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
