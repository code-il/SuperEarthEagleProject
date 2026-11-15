"""SPHEREx Explorer API: the integration layer for all five workstreams.

Run from the project root::

    python -m scripts.start                 # one command: builds the viewer if needed, serves app + API on :8000
    uvicorn backend.api.main:app --port 8000  # API only (use the Vite dev server for the viewer)

Interactive API docs: http://localhost:8000/docs  ·  contracts: docs/API.md

Routers
    data_routes        Person 1: name/coordinate search, observations, metadata, FITS cutouts
    processing_routes  Person 2: processed series, stacks, difference/ratio images
    catalog_routes     Gaia DR3 / CatWISE2020 overlays (viewer, Person 3)
    analysis_routes    Person 4: sources, motion, light curves, spectra, classes

When ``frontend/dist`` exists (``npm run build``), the viewer is served from
the same origin, with client-side routes (``/explore``, ``/about``) falling
back to ``index.html``.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .analysis_routes import router as analysis_router
from .catalog_routes import router as catalog_router
from .data_routes import router as data_router
from .galaxy_routes import router as galaxy_router
from .game_routes import router as game_router
from .orbit_routes import router as orbit_router
from .processing_routes import router as processing_router

APP_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[2]


def _configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO")
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)
    log_file = os.environ.get("SPHEREX_LOG_FILE")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter(fmt))
        h.setLevel(logging.INFO)
        logging.getLogger().addHandler(h)


_configure_logging()
log = logging.getLogger("spherex.api")

app = FastAPI(
    title="SPHEREx Explorer API",
    version=APP_VERSION,
    description="Search NASA SPHEREx spectral images at IRSA, retrieve FITS cutouts with metadata, align, stack, "
                "difference and render them on a common sky grid, and analyse the time series for moving and "
                "variable sources. Candidates are not discoveries. Contracts: docs/API.md.",
)

_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Request limits: per-client rate limit on archive-touching routes, and a cap on
# concurrent heavy jobs (processing/analysis download and reproject many cutouts).
# ---------------------------------------------------------------------------

HEAVY_PREFIXES = ("/api/process", "/api/compare", "/api/analysis", "/api/light-curve", "/api/spectrum", "/api/cutouts",
                  "/api/galaxy/spectrum")
LIMITED_PREFIXES = HEAVY_PREFIXES + ("/api/search", "/api/observations", "/api/resolve", "/api/catalog")


class RateLimiter:
    """Sliding-window request counter per client (in-process; use a proxy limiter for multi-worker deploys)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> tuple[bool, float]:
        if self.per_minute <= 0:
            return True, 0.0
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(client, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False, 60 - (now - q[0])
            q.append(now)
            if len(self._hits) > 10_000:  # forget idle clients
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60][:5000]:
                    self._hits.pop(k, None)
            return True, 0.0


limiter = RateLimiter(int(os.environ.get("SPHEREX_RATE_LIMIT_PER_MIN", "120")))
_max_jobs = int(os.environ.get("SPHEREX_MAX_CONCURRENT_JOBS", "3"))
_job_wait_s = float(os.environ.get("SPHEREX_JOB_QUEUE_TIMEOUT_S", "300"))
_jobs = asyncio.Semaphore(_max_jobs) if _max_jobs > 0 else None


def _client(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") if os.environ.get("SPHEREX_TRUST_PROXY") else None
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "unknown")


@app.middleware("http")
async def limits_and_logging(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    path = request.url.path
    t0 = time.perf_counter()
    if path.startswith(LIMITED_PREFIXES) and not path.startswith(("/api/files", "/api/health")):
        ok, retry = limiter.allow(_client(request))
        if not ok:
            log.warning("rate limited %s %s (%s)", _client(request), path, rid)
            return JSONResponse({"detail": f"Too many requests; retry in {retry:.0f} s."}, status_code=429,
                                headers={"Retry-After": str(int(retry) + 1), "X-Request-ID": rid})
    heavy = _jobs is not None and path.startswith(HEAVY_PREFIXES)
    if heavy:
        try:
            await asyncio.wait_for(_jobs.acquire(), timeout=_job_wait_s)
        except asyncio.TimeoutError:
            return JSONResponse({"detail": "The server is busy processing other fields; please retry in a minute."},
                                status_code=503, headers={"Retry-After": "60", "X-Request-ID": rid})
    try:
        response = await call_next(request)
    except Exception:  # route errors are normally HTTPExceptions; this is the last resort
        log.exception("unhandled error on %s %s (%s)", request.method, path, rid)
        response = JSONResponse({"detail": f"Internal server error (request {rid}); details are in the server log."},
                                status_code=500)
    finally:
        if heavy:
            _jobs.release()
    dt = time.perf_counter() - t0
    response.headers["X-Request-ID"] = rid
    if path.startswith("/api/"):
        lvl = logging.WARNING if response.status_code >= 500 else logging.INFO
        if response.status_code >= 400 or dt > 5 or not path.startswith("/api/files"):
            log.log(lvl, "%s %s -> %d in %.2fs (%s)", request.method, path, response.status_code, dt, rid)
    return response


app.include_router(data_router)
app.include_router(processing_router)
app.include_router(catalog_router)
app.include_router(analysis_router)
app.include_router(galaxy_router)
app.include_router(orbit_router)
app.include_router(game_router)


@app.get("/api/version", tags=["meta"])
def version():
    """App and component versions, for bug reports and reproducibility."""
    from backend.analysis import ANALYSIS_VERSION
    from backend.image_processing.pipeline import PROCESSING_VERSION

    return {"app": APP_VERSION, "processing": PROCESSING_VERSION, "analysis": ANALYSIS_VERSION,
            "frontend_bundled": _DIST is not None}


# ---------------------------------------------------------------------------
# The built viewer (single-origin deploy)
# ---------------------------------------------------------------------------

def _dist_dir() -> Path | None:
    d = Path(os.environ.get("SPHEREX_FRONTEND_DIST", ROOT / "frontend" / "dist"))
    return d if (d / "index.html").is_file() else None


_DIST = _dist_dir()
if _DIST is not None:
    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        f = (_DIST / full_path).resolve()
        if full_path and f.is_file() and _DIST.resolve() in f.parents:
            return FileResponse(f)
        index = _DIST / "index.html"
        if not index.is_file():  # e.g. while `npm run build` rewrites dist/
            return JSONResponse({"detail": "The viewer is being rebuilt; reload in a few seconds."}, status_code=503,
                                headers={"Retry-After": "5"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
