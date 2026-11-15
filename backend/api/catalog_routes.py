"""Reference-catalogue overlay routes (Gaia DR3, CatWISE2020) for the viewer."""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.api.data_routes import _http_error, _position
from backend.catalogs import cone_search

router = APIRouter(prefix="/api", tags=["catalogues"])


@router.get("/catalog/{catalog}")
def catalog(catalog: str, q: str | None = None, ra: str | None = None, dec: str | None = None,
            radius: float = Query(0.05, gt=0, le=0.5, description="Cone radius in degrees"),
            epoch: str | None = Query(None, description="Also return positions propagated to this date"),
            limit: int = Query(500, ge=1, le=5000), max_mag: float | None = None):
    """Sources from ``gaia`` (Gaia DR3) or ``catwise`` (CatWISE2020) with proper motions."""
    try:
        pos = _position(ra, dec, q, None)
        return cone_search(catalog, pos["ra"], pos["dec"], radius, epoch=epoch, limit=limit, max_mag=max_mag)
    except Exception as exc:
        raise _http_error(exc)
