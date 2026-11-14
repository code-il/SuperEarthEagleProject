# Deploying SPHEREx Explorer

The app is one Python process. It serves the built React viewer and the API from the same origin,
and it keeps a disk cache of everything it downloads or computes. **No credentials are involved**:
IRSA, CDS (Sesame, SIMBAD), the Gaia archive and IMCCE SkyBoT are public and anonymous. Never add
tokens to the frontend; nothing the browser needs is secret.

## Option A: one container (recommended for the demo)

```bash
docker compose up --build
```

Open http://localhost:8000. The `Dockerfile` has two stages:

1. `node:22` builds `frontend/dist`.
2. `python:3.11-slim` installs `requirements.txt`, copies `backend/` and the build, and runs
   `uvicorn` as a non-root user, with a health check on `/api/health`.

The cache lives in the `spherex-cache` volume at `/data/cache`.

The same image runs on any container host: Render, Fly.io, Railway, Google Cloud Run, Azure
Container Apps, or a VM.

- **Port:** set `PORT` if the platform assigns one; the container reads it.
- **Cache storage:** attach persistent storage at `/data/cache`, otherwise every restart
  re-downloads.
- **Memory:** budget 2 GB RAM. Reprojection and analysis of a 50–100-frame field peak at around
  1 GB.
- **Workers:** run **one** worker per container. The rate limiter, the job queue and the in-memory
  analysis cache are per process. Scale by running more containers behind the platform's load
  balancer, and give them a shared volume or accept per-instance caches.

## Option B: static viewer + API container

Host `frontend/dist` on a static host (Netlify, Vercel, GitHub Pages, S3+CloudFront). Run the API
container separately.

1. Build the viewer with the API's public URL:
   ```bash
   cd frontend
   ```
   ```bash
   VITE_API_BASE=https://api.example.org npm run build
   ```
2. On the API, allow the viewer's origin: `CORS_ORIGINS=https://explorer.example.org`.
3. Configure the static host to serve `index.html` for unknown paths. `/explore` and `/about` are
   client-side routes; Netlify needs `/* /index.html 200`.

## Option C: a plain server (no Docker)

```bash
python -m venv .venv
```
```bash
.venv/bin/pip install -r requirements.txt
```
```bash
cp .env.example .env
```
```bash
.venv/bin/python -m scripts.start --host 0.0.0.0
```

Put nginx or Caddy in front for TLS. Set `SPHEREX_TRUST_PROXY=1` so rate limits use
`X-Forwarded-For`. Allow long proxy timeouts (≥ 600 s, see below), and run it under systemd with
`Restart=always`.

## Operational notes

| Concern | How it is handled | What to configure |
|---|---|---|
| Temporary FITS storage | Cutouts (~0.1–0.5 MB each) and processed frames are cached under `SPHEREX_CACHE_DIR`. A busy field is 50–150 MB, and the five demo targets take ~2.5 GB with calibration files | Put the volume on a disk with ≥ 10 GB free. Prune old `cache/cutouts` and `cache/processed` folders with a cron job (`find cache -mtime +30 -delete`). Everything can be regenerated |
| Caching of frequent coordinates | Archive queries are cached for 6 h, name resolutions for 30 days, processed series and analyses on disk keyed by their inputs, plus an in-memory LRU of analyses | `SPHEREX_QUERY_CACHE_TTL_S`. **Pre-warm the demo** by running `python -m scripts.demo_workflow` once after deploying |
| Request limits | Sliding-window limit per client on archive-touching routes (429 + `Retry-After`). At most N heavy jobs at once; others queue, then get a 503 | `SPHEREX_RATE_LIMIT_PER_MIN`, `SPHEREX_MAX_CONCURRENT_JOBS`, `SPHEREX_JOB_QUEUE_TIMEOUT_S`. Add a proxy-level limit for public deployments |
| Large downloads | Only cutouts of the requested field are fetched, never full 2040×2040 images. Detector D6 is the default, the field is capped at `SPHEREX_MAX_FOV_DEG`, and requests at `SPHEREX_MAX_CUTOUTS_PER_REQUEST`. Downloads are parallel (`SPHEREX_MAX_WORKERS`), written atomically (no half-written files in the cache) and checksummed | Keep the defaults for a public demo; raise them only for trusted users |
| API timeouts | Upstream calls time out after `SPHEREX_HTTP_TIMEOUT_S` and retry with back-off (IRSA returns 503 under load). A first visit to a new field takes 1–3 min | Set proxy/platform request timeouts to ≥ 600 s (Cloud Run: `--timeout=900`; nginx: `proxy_read_timeout 900s`) |
| Error logging | Each request is logged with status, duration and an `X-Request-ID`. Unhandled errors log a stack trace server-side while the client gets a short message with the id | `LOG_LEVEL`, and `SPHEREX_LOG_FILE` for a rotating log file. Platforms capture stdout |
| Secrets | None needed. `.env` is git-ignored, and `.env.example` documents every variable | Do not set `VITE_*` variables to anything secret; they are compiled into public JavaScript |
| Health | `GET /api/health` (liveness) and `GET /api/version` | Point the platform's health check at `/api/health` |

## Checking a deployment

```bash
python -m scripts.demo_workflow --base https://your-deployment.example.org
```

This runs the twelve acceptance steps for all five targets and writes `examples/demo/demo_report.md`.
