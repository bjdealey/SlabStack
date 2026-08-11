# SlabStack — one image, one process, one port.
#
# Stage 1 builds the UI, stage 2 runs the API and serves that build, so a
# packaged install has nothing to wire together. The database and images live on
# a mounted volume at /data, never inside the image: rebuilding or upgrading the
# container must never touch the collection.

# ---- Stage 1: build the UI --------------------------------------------------
FROM node:22-alpine AS ui

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---- Stage 2: runtime -------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SLABSTACK_DATA_DIR=/data \
    SLABSTACK_STATIC_DIR=/app/static \
    SLABSTACK_HOST=0.0.0.0 \
    SLABSTACK_PORT=8000

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=ui /build/dist ./static

# The API writes the database, images and SQLite WAL files here. Runs as a
# non-root user, so the mounted directory must be owned by it.
RUN useradd --create-home --uid 10001 slabstack \
    && mkdir -p /data \
    && chown -R slabstack:slabstack /app /data
USER slabstack

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
