"""Configuration for the SPHEREx data acquisition engine.

Everything that is likely to change between SPHEREx data releases lives here,
so a new release (e.g. "DR2", "QR4") can be added without touching the
downloader or the query code:

* Add a :class:`Release` to ``DEFAULT_RELEASES``; or
* point the ``SPHEREX_RELEASES_FILE`` environment variable at a JSON file
  holding a list of release objects (same fields as :class:`Release`); and
* optionally set ``SPHEREX_DEFAULT_RELEASES`` (e.g. ``"DR1,QR3"``) to choose
  which releases are searched when the caller does not specify any.

All other settings can be overridden with ``SPHEREX_*`` environment variables.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Release:
    """One SPHEREx data release, mapped to an IRSA SIA2 collection."""

    key: str
    """Short label returned to clients, e.g. ``"DR1"``."""
    collection: str
    """IRSA SIA2 collection name, e.g. ``"spherex_dr1"``."""
    priority: int
    """When the same exposure appears in two releases, the higher priority wins."""
    deep: bool = False
    """True for the deep-field (NEP/SEP) collections."""
    description: str = ""


DEFAULT_RELEASES: tuple[Release, ...] = (
    Release("DR1", "spherex_dr1", 20, False,
            "Data Release 1: uniformly reprocessed all-sky spectral images."),
    Release("DR1_DEEP", "spherex_dr1_deep", 21, True,
            "Data Release 1, deep-field (ecliptic pole) images."),
    Release("QR3", "spherex_qr3", 10, False,
            "Quick Release 3: recent weekly spectral images not yet in DR1."),
    Release("QR3_DEEP", "spherex_qr3_deep", 11, True,
            "Quick Release 3, deep-field (ecliptic pole) images."),
)

# DR1 + QR3 together form the uniform all-sky set (see project brief).
DEFAULT_SELECTION: tuple[str, ...] = ("DR1", "QR3")


def _env(name: str, default: str) -> str:
    return os.environ.get(f"SPHEREX_{name}", default)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    sia_url: str = field(default_factory=lambda: _env("SIA_URL", "https://irsa.ipac.caltech.edu/SIA"))
    tap_url: str = field(default_factory=lambda: _env("TAP_URL", "https://irsa.ipac.caltech.edu/TAP"))
    sesame_urls: tuple[str, ...] = field(default_factory=lambda: tuple(_env(
        "SESAME_URLS",
        "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?{q},"
        "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/SNV?{q}",
    ).split(",")))
    cache_dir: Path = field(default_factory=lambda: Path(_env("CACHE_DIR", str(_project_root() / "cache"))))
    query_cache_ttl_s: float = field(default_factory=lambda: float(_env("QUERY_CACHE_TTL_S", str(6 * 3600))))
    resolver_cache_ttl_s: float = field(default_factory=lambda: float(_env("RESOLVER_CACHE_TTL_S", str(30 * 86400))))
    http_timeout_s: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT_S", "120")))
    http_retries: int = field(default_factory=lambda: int(_env("HTTP_RETRIES", "6")))
    http_backoff_s: float = field(default_factory=lambda: float(_env("HTTP_BACKOFF_S", "1.5")))
    max_workers: int = field(default_factory=lambda: int(_env("MAX_WORKERS", "6")))
    max_fov_deg: float = field(default_factory=lambda: float(_env("MAX_FOV_DEG", "2.0")))
    min_fov_deg: float = field(default_factory=lambda: float(_env("MIN_FOV_DEG", "0.005")))
    max_cutouts_per_request: int = field(default_factory=lambda: int(_env("MAX_CUTOUTS_PER_REQUEST", "400")))
    reference_epoch: str = field(default_factory=lambda: _env("REFERENCE_EPOCH", "2026-03-01"))
    use_spectral_wcs_calibration: bool = field(default_factory=lambda: _env(
        "USE_SPECTRAL_WCS_CAL", "1").lower() not in ("0", "false", "no"))
    """Use CWAVE/CBAND calibration maps for per-pixel wavelengths (IRSA's science recommendation)."""
    """Date to which named targets are propagated when choosing a search centre
    (roughly the middle of DR1+QR3 coverage)."""
    epoch_gap_days: float =field(default_factory=lambda: float(_env("EPOCH_GAP_DAYS", "20")))
    user_agent: str = field(default_factory=lambda: _env("USER_AGENT", "SPHEREx-Explorer/0.1 (data-acquisition)"))
    releases_file: str | None = field(default_factory=lambda: os.environ.get("SPHEREX_RELEASES_FILE"))
    default_releases: tuple[str, ...] = field(default_factory=lambda: tuple(
        s.strip().upper() for s in _env("DEFAULT_RELEASES", ",".join(DEFAULT_SELECTION)).split(",") if s.strip()
    ))

    # -- releases -----------------------------------------------------------
    def releases(self) -> dict[str, Release]:
        """All known releases, keyed by upper-case release key."""
        rels = list(DEFAULT_RELEASES)
        if self.releases_file:
            with open(self.releases_file, encoding="utf-8") as fh:
                rels = [Release(**r) for r in json.load(fh)]
        return {r.key.upper(): r for r in rels}

    def resolve_releases(self, release: str | list[str] | tuple[str, ...] | None) -> list[Release]:
        """Turn a user-facing release selector into Release objects.

        Accepts ``None`` (defaults), ``"all"``, ``"DR1"``, ``"DR1,QR3"``,
        a list of keys, or raw collection names such as ``"spherex_dr1"``.
        """
        known = self.releases()
        if release is None or (isinstance(release, str) and not release.strip()):
            keys = list(self.default_releases)
        elif isinstance(release, str):
            keys = [s.strip() for s in release.split(",") if s.strip()]
        else:
            keys = [str(s).strip() for s in release]
        if any(k.lower() == "all" for k in keys):
            return sorted(known.values(), key=lambda r: -r.priority)
        by_collection = {r.collection.lower(): r for r in known.values()}
        out: list[Release] = []
        for k in keys:
            rel = known.get(k.upper()) or by_collection.get(k.lower())
            if rel is None:
                raise ValueError(
                    f"Unknown data release {k!r}. Known releases: {', '.join(sorted(known))} (or 'all')."
                )
            if rel not in out:
                out.append(rel)
        return out

    # -- cache layout -------------------------------------------------------
    @property
    def query_cache_dir(self) -> Path:
        return self.cache_dir / "queries"

    @property
    def record_cache_dir(self) -> Path:
        return self.cache_dir / "records"

    @property
    def cutout_dir(self) -> Path:
        return self.cache_dir / "cutouts"

    @property
    def full_image_dir(self) -> Path:
        return self.cache_dir / "full"

    @property
    def resolver_cache_dir(self) -> Path:
        return self.cache_dir / "resolver"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Replace the global settings (used by tests and by the API on startup)."""
    global _settings
    _settings = settings
