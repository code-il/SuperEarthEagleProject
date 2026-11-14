# SPHEREx Explorer: one container serving the viewer and the API on port 8000.
#   docker build -t spherex-explorer .
#   docker run -p 8000:8000 -v spherex-cache:/data/cache spherex-explorer

# --- 1. build the viewer ------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Python runtime ----------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    SPHEREX_CACHE_DIR=/data/cache SPHEREX_FRONTEND_DIST=/app/frontend/dist \
    HOST=0.0.0.0 PORT=8000 LOG_LEVEL=INFO
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY --from=web /web/dist frontend/dist
RUN useradd --create-home app && mkdir -p /data/cache && chown -R app /data
USER app
VOLUME /data/cache
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health',timeout=4)"
# One worker: the rate limiter, job queue and in-memory caches are per process.
CMD ["sh", "-c", "uvicorn backend.api.main:app --host $HOST --port $PORT --timeout-keep-alive 75 --proxy-headers"]
