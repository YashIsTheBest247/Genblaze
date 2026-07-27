# Full-stack image: builds the React SPA and serves it from the FastAPI backend,
# so a deployment exposes ONE public URL (no CORS, no second host to configure).
#
# Build from the repository root:
#   docker build -t flux .
#   docker run -p 8000:8000 --env-file backend/.env flux
#
# Works as-is on Hugging Face Spaces (Docker), Railway, Render, Fly.io and
# Cloud Run. Media durability comes from Backblaze B2, so an ephemeral
# filesystem is fine — the library survives restarts and redeploys.

# ---------- stage 1: build the UI ----------
FROM node:20-alpine AS ui

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# No VITE_API_BASE_URL: the SPA calls the API on its own origin.
RUN npm run build


# ---------- stage 2: python runtime ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Kokoro/spaCy model downloads land here on first render.
    HF_HOME=/app/.cache/huggingface \
    NUMBA_CACHE_DIR=/tmp/numba

# System libs: build tools (sgmllib3k compiles), ffmpeg, audio + opencv runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsndfile1 \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching.
COPY backend/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Backend code, then the built UI into the directory main.py mounts.
COPY backend/ .
COPY --from=ui /ui/dist ./static/app

# Hosts like Hugging Face Spaces run the container as a non-root user, so the
# working directories the render pipeline writes to must be group-writable.
RUN mkdir -p static/videos resources/images resources/audio resources/scripts \
             resources/video resources/subtitles .cache/huggingface \
    && chmod -R 777 static resources .cache

EXPOSE 8000

# --workers 1 is deliberate: the render lock and live status are in-process.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
