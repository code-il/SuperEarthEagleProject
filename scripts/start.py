"""Start SPHEREx Explorer with one command.

    python -m scripts.start            # build the viewer if needed, serve app + API at http://127.0.0.1:8000
    python -m scripts.start --dev      # API with auto-reload on :8000 + Vite dev server on :5173 (hot reload)
    python -m scripts.start --no-build # serve whatever frontend/dist holds (or the API only)
    python -m scripts.start --check    # verify the installation (Python packages, Node, viewer build) and exit

Settings come from the environment and from ``.env`` at the project root
(see ``.env.example``); variables already set in the environment win.
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
REQUIRED = ["astropy", "pyvo", "astroquery", "numpy", "requests", "fastapi", "uvicorn", "pydantic",
            "reproject", "scipy", "PIL", "photutils"]


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _npm() -> str | None:
    found = os.environ.get("NPM") or shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if found or os.name != "nt":
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("APPDATA")):
        p = Path(base or "") / ("npm" if base == os.environ.get("APPDATA") else "nodejs") / "npm.cmd"
        if base and p.is_file():
            return str(p)
    return None


def _newest_mtime(*paths: Path) -> float:
    m = 0.0
    for p in paths:
        if p.is_file():
            m = max(m, p.stat().st_mtime)
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    m = max(m, f.stat().st_mtime)
    return m


def viewer_is_stale() -> bool:
    index = DIST / "index.html"
    if not index.is_file():
        return True
    src = _newest_mtime(FRONTEND / "src", FRONTEND / "public", FRONTEND / "index.html", FRONTEND / "package.json",
                        FRONTEND / "vite.config.ts")
    return src > index.stat().st_mtime


def build_viewer() -> bool:
    npm = _npm()
    if not npm:
        print("! Node.js/npm not found: cannot build the viewer. Install Node 20.19+ or use a prebuilt frontend/dist.")
        return False
    if not (FRONTEND / "node_modules").is_dir():
        print("> npm ci (installing viewer dependencies)…", flush=True)
        if subprocess.call([npm, "ci"], cwd=FRONTEND) != 0:
            return False
    print("> npm run build (viewer)…", flush=True)
    return subprocess.call([npm, "run", "build"], cwd=FRONTEND) == 0


def check() -> int:
    ok = True
    print(f"Python {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 11):
        print("! Python 3.11+ is required"); ok = False
    for mod in REQUIRED:
        try:
            m = importlib.import_module(mod)
            print(f"  ok  {mod} {getattr(m, '__version__', '')}")
        except ImportError as exc:
            print(f"  !!  {mod}: {exc}"); ok = False
    npm = _npm()
    print(f"npm: {npm or 'not found (needed only to build the viewer)'}")
    node = shutil.which("node") or next((str(c) for c in (Path(npm).parent / n for n in ("node.exe", "node")) if npm and c.is_file()), None)
    if node:
        try:
            v = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
            major, minor = (int(x) for x in v.lstrip("v").split(".")[:2])
            if (major, minor) < (20, 19):
                print(f"! Node {v} is too old for the viewer build (Vite 8 needs 20.19+ or 22.12+)"); ok = False
            else:
                print(f"  ok  node {v}")
        except Exception:  # pragma: no cover - diagnostic
            pass
    galaxy = ROOT / "data" / "galaxy" / "catalog.npz"
    pub = FRONTEND / "public" / "galaxy"
    missing = [str(p.relative_to(ROOT)) for p in (galaxy, pub / "meta.json", pub / "stars.bin", pub / "systems.json") if not p.is_file()]
    if missing:
        print(f"  !!  3D Atlas data missing: {', '.join(missing)} (git pull, or rebuild with python -m scripts.build_galaxy_map)"); ok = False
    else:
        print("  ok  3D Atlas data")
    print(f"viewer build: {'present' if (DIST / 'index.html').is_file() else 'missing'}"
          f"{' (older than sources)' if (DIST / 'index.html').is_file() and viewer_is_stale() else ''}")
    try:
        sys.path.insert(0, str(ROOT))
        from backend.api.main import app  # noqa: F401  (imports every workstream)
        print("  ok  backend imports (all workstreams)")
    except Exception as exc:  # pragma: no cover - diagnostic
        print(f"  !!  backend import failed: {exc}"); ok = False
    print("OK" if ok else "Problems found (see above). Install with: pip install -r requirements.txt")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", action="store_true", help="API with reload + Vite dev server")
    ap.add_argument("--no-build", action="store_true", help="do not (re)build the viewer")
    ap.add_argument("--check", action="store_true", help="check the installation and exit")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args(argv)
    load_env()
    os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
    if a.check:
        return check()
    host = a.host or os.environ.get("HOST", "127.0.0.1")
    port = a.port or int(os.environ.get("PORT", "8000"))
    uvicorn = [sys.executable, "-m", "uvicorn", "backend.api.main:app", "--host", host, "--port", str(port)]

    if a.dev:
        npm = _npm()
        if not npm:
            print("! npm not found; the dev server needs Node.js 20.19+.")
            return 1
        if not (FRONTEND / "node_modules").is_dir():
            subprocess.check_call([npm, "ci"], cwd=FRONTEND)
        env = {**os.environ, "SPHEREX_API": f"http://127.0.0.1:{port}"}
        api = subprocess.Popen(uvicorn + ["--reload", "--reload-dir", str(ROOT / "backend")], cwd=ROOT)
        web = subprocess.Popen([npm, "run", "dev", "--", "--port", "5173"], cwd=FRONTEND, env=env)
        print(f"\nSPHEREx Explorer (dev): viewer http://localhost:5173 · API http://{host}:{port}/docs\n", flush=True)
        try:
            return api.wait()
        except KeyboardInterrupt:
            return 0
        finally:
            for p in (web, api):
                p.terminate()

    if not a.no_build and viewer_is_stale() and not build_viewer():
        if not (DIST / "index.html").is_file():
            print("! Starting the API only (no viewer build). The API docs are at /docs.")
    print(f"\nSPHEREx Explorer: http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port} "
          f"(API docs at /docs)\n", flush=True)
    try:
        return subprocess.call(uvicorn, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
