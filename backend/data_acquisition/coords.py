"""Shared coordinate convention, validation and name resolution.

Convention used across the whole project
----------------------------------------
* Internally every position is an ICRS ``SkyCoord`` / decimal degrees.
* Numbers (or numeric strings) are always **degrees** for both RA and Dec.
* Sexagesimal strings: RA is in **hours** (``"08h55m10.831s"``,
  ``"08:55:10.831"``, ``"08 55 10.831"``); Dec is in **degrees**
  (``"-7d14m42.53s"``, ``"-07:14:42.53"``, ``"-07 14 42.53"``).
* A single combined string such as ``"08 55 10.8 -07 14 42"`` or
  ``"133.795 -7.245"`` is accepted by :func:`parse_coordinate_text`.

Proper motion
-------------
SIMBAD (via CDS Sesame) gives positions at epoch J2000. Nearby objects move
a lot since then (Barnard's Star: ~4.5 arcmin by 2026), so resolved targets
carry their proper motion and parallax and can be propagated to any date
with :meth:`ResolvedTarget.position_at`.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import astropy.units as u
import numpy as np
from astropy.coordinates import Distance, SkyCoord
from astropy.time import Time

from .cache import JsonCache
from .config import get_settings
from .http import HttpError, get_session, request_with_retries

log = logging.getLogger(__name__)


class CoordinateError(ValueError):
    """Raised for invalid coordinates, field sizes or unresolvable names."""


# --------------------------------------------------------------------------
# Parsing & validation
# --------------------------------------------------------------------------
_NUMERIC = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _is_numeric(value: Any) -> bool:
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        return True
    return isinstance(value, str) and bool(_NUMERIC.match(value.strip()))


def parse_coordinates(ra: Any, dec: Any) -> SkyCoord:
    """Parse an RA/Dec pair (decimal degrees or sexagesimal) into an ICRS SkyCoord.

    >>> parse_coordinates(269.452, 4.693)
    >>> parse_coordinates("08h55m10.831s", "-7d14m42.53s")
    >>> parse_coordinates("08:55:10.831", "-07:14:42.53")
    """
    if ra is None or dec is None or (isinstance(ra, str) and not ra.strip()) or (
        isinstance(dec, str) and not dec.strip()
    ):
        raise CoordinateError("Both RA and Dec are required.")
    try:
        if _is_numeric(ra):
            ra_angle = float(ra) * u.deg
        else:
            ra_angle = _parse_angle(str(ra), u.hourangle)
        if _is_numeric(dec):
            dec_angle = float(dec) * u.deg
        else:
            dec_angle = _parse_angle(str(dec), u.deg)
    except CoordinateError:
        raise
    except Exception as exc:  # astropy raises several types
        raise CoordinateError(f"Could not parse coordinates RA={ra!r}, Dec={dec!r}: {exc}") from exc
    _validate_angles(ra_angle.to_value(u.deg), dec_angle.to_value(u.deg))
    return SkyCoord(ra=ra_angle, dec=dec_angle, frame="icrs")


def _parse_angle(text: str, unit: u.Unit) -> u.Quantity:
    from astropy.coordinates import Angle

    t = text.strip()
    # "08 55 10.8" -> "08:55:10.8" so Angle parses it with the intended unit
    if re.match(r"^[+-]?\d+\s+\d+(\s+[\d.]+)?$", t):
        t = ":".join(t.split())
    has_explicit_unit = bool(re.search(r"[hdms°'\"]", t, re.IGNORECASE))
    ang = Angle(t) if has_explicit_unit else Angle(t, unit=unit)
    return ang.to(u.deg)


def _validate_angles(ra_deg: float, dec_deg: float) -> None:
    if not np.isfinite(ra_deg) or not np.isfinite(dec_deg):
        raise CoordinateError("Coordinates must be finite numbers.")
    if not 0.0 <= ra_deg < 360.0:
        raise CoordinateError(f"RA must be in [0, 360) degrees (or 0h-24h); got {ra_deg:.6f} deg.")
    if not -90.0 <= dec_deg <= 90.0:
        raise CoordinateError(f"Dec must be in [-90, +90] degrees; got {dec_deg:.6f} deg.")


def parse_coordinate_text(text: str) -> SkyCoord:
    """Parse a single string holding both RA and Dec.

    Accepts ``"133.795 -7.245"``, ``"133.795, -7.245"``,
    ``"08 55 10.831 -07 14 42.53"``, ``"08h55m10.831s -7d14m42.53s"``,
    ``"08:55:10.831 -07:14:42.53"``.
    """
    t = text.strip().replace(",", " ")
    parts = t.split()
    if len(parts) == 2:
        return parse_coordinates(parts[0], parts[1])
    if len(parts) == 6:
        return parse_coordinates(" ".join(parts[:3]), " ".join(parts[3:]))
    # Let astropy try anything else (e.g. "08h55m10s -07d14m42s" variants)
    try:
        c = SkyCoord(t, unit=(u.hourangle, u.deg), frame="icrs")
    except Exception as exc:
        raise CoordinateError(f"Could not parse coordinate string {text!r}.") from exc
    _validate_angles(c.ra.deg, c.dec.deg)
    return c


def looks_like_coordinates(text: str) -> bool:
    try:
        parse_coordinate_text(text)
        return True
    except CoordinateError:
        return False


def validate_fov(fov: Any) -> float:
    """Validate a field-of-view width in degrees and return it as float."""
    s = get_settings()
    try:
        f = float(fov)
    except (TypeError, ValueError) as exc:
        raise CoordinateError(f"Field of view must be a number of degrees; got {fov!r}.") from exc
    if not np.isfinite(f) or f <= 0:
        raise CoordinateError("Field of view must be a positive number of degrees.")
    if f < s.min_fov_deg:
        raise CoordinateError(f"Field of view must be at least {s.min_fov_deg} deg.")
    if f > s.max_fov_deg:
        raise CoordinateError(f"Field of view must be at most {s.max_fov_deg} deg.")
    return f


def parse_time(value: Any) -> Time:
    """Parse an ISO date/time string, a ``Time``, or an MJD number."""
    if isinstance(value, Time):
        return value
    if _is_numeric(value):
        return Time(float(value), format="mjd", scale="utc")
    try:
        return Time(str(value).strip(), scale="utc")
    except Exception as exc:
        raise CoordinateError(f"Could not parse date/time {value!r}; use ISO 8601 or MJD.") from exc


def parse_date_range(date_range: Any) -> tuple[Time, Time] | None:
    """``None`` | ``(start, end)`` (either side may be None) -> (Time, Time) or None."""
    if date_range is None:
        return None
    if isinstance(date_range, str):
        date_range = [p for p in re.split(r"\s*(?:/|,|\.\.)\s*", date_range.strip()) if p != ""]
    start, end = (list(date_range) + [None, None])[:2]
    t0 = parse_time(start) if start not in (None, "") else Time("2024-01-01", scale="utc")
    t1 = parse_time(end) if end not in (None, "") else Time("2100-01-01", scale="utc")
    if t1 < t0:
        raise CoordinateError("Date range end is before its start.")
    return t0, t1


def parse_wavelength_range(wavelength_range: Any) -> tuple[float, float] | None:
    """Wavelength range in micrometres. Either side may be ``None``."""
    if wavelength_range is None:
        return None
    lo, hi = (list(wavelength_range) + [None, None])[:2]
    lo = 0.0 if lo in (None, "") else float(lo)
    hi = 100.0 if hi in (None, "") else float(hi)
    if lo < 0 or hi <= lo:
        raise CoordinateError("Wavelength range must satisfy 0 <= min < max (micrometres).")
    return lo, hi


# --------------------------------------------------------------------------
# Name resolution (CDS Sesame -> SIMBAD/NED/VizieR)
# --------------------------------------------------------------------------
@dataclass
class ResolvedTarget:
    query: str
    name: str
    ra: float
    """ICRS RA (deg) at ``ref_epoch``."""
    dec: float
    ref_epoch: str = "J2000.0"
    pm_ra_cosdec_mas_yr: float | None = None
    pm_dec_mas_yr: float | None = None
    parallax_mas: float | None = None
    radial_velocity_km_s: float | None = None
    object_type: str | None = None
    source: str = "CDS Sesame"
    note: str | None = None

    @property
    def has_proper_motion(self) -> bool:
        return self.pm_ra_cosdec_mas_yr is not None and self.pm_dec_mas_yr is not None

    def skycoord(self) -> SkyCoord:
        return SkyCoord(self.ra * u.deg, self.dec * u.deg, frame="icrs")

    def position_at(self, when: Any) -> SkyCoord:
        """Position propagated to ``when`` (Time / ISO / MJD) using proper motion + parallax.

        Without proper motion the reference position is returned unchanged.
        Note: annual parallax wobble is *not* applied here (barycentric position).
        """
        if not self.has_proper_motion:
            return self.skycoord()
        t = parse_time(when)
        plx = self.parallax_mas if self.parallax_mas and self.parallax_mas > 0 else None
        dist = Distance(parallax=plx * u.mas) if plx else Distance(1e6, u.pc)
        rv = (self.radial_velocity_km_s or 0.0) * u.km / u.s
        c = SkyCoord(
            ra=self.ra * u.deg, dec=self.dec * u.deg,
            pm_ra_cosdec=self.pm_ra_cosdec_mas_yr * u.mas / u.yr,
            pm_dec=self.pm_dec_mas_yr * u.mas / u.yr,
            distance=dist, radial_velocity=rv,
            obstime=Time(self.ref_epoch), frame="icrs",
        )
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            moved = c.apply_space_motion(new_obstime=t)
        return SkyCoord(moved.ra, moved.dec, frame="icrs")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# The five mandatory challenge targets. Names are resolved live (no hard-coded,
# possibly outdated coordinates); WISE 0855 also carries the coordinates given
# in the challenge statement as a fallback if the resolver is unreachable.
MANDATORY_TARGETS: list[dict[str, Any]] = [
    {"key": "barnards-star", "label": "Barnard's Star", "query": "Barnard's Star",
     "why": "Fastest proper motion of any star (~10.4 arcsec/yr)."},
    {"key": "epsilon-indi-b", "label": "Epsilon Indi B", "query": "eps Ind B",
     "why": "Nearby brown-dwarf binary (Ba/Bb), ~4.7 arcsec/yr."},
    {"key": "wise-0855", "label": "WISE 0855", "query": "WISE J085510.83-071442.5",
     "why": "Coldest known brown dwarf (~285 K), ~8.1 arcsec/yr.",
     "fallback": {"ra": "08h55m10.831s", "dec": "-07d14m42.53s",
                  "note": "Coordinates as supplied in the challenge statement; epoch not "
                          "stated (consistent with the ~2010 WISE epoch). No proper motion applied."}},
    {"key": "rw-aurigae", "label": "RW Aurigae", "query": "RW Aur",
     "why": "Young T Tauri binary with strong variability."},
    {"key": "r-draconis", "label": "R Draconis", "query": "R Dra",
     "why": "Mira variable star (~245-day period)."},
]


def _float(el: ET.Element | None, path: str) -> float | None:
    if el is None:
        return None
    node = el.find(path)
    if node is None or node.text is None or not node.text.strip():
        return None
    try:
        return float(node.text)
    except ValueError:
        return None


def parse_sesame_xml(xml_text: str, query: str) -> ResolvedTarget | None:
    """Extract the first successful resolver answer from a Sesame ``-oxp`` XML response."""
    root = ET.fromstring(xml_text)
    for resolver in root.iter("Resolver"):
        ra = _float(resolver, "jradeg")
        dec = _float(resolver, "jdedeg")
        if ra is None or dec is None:
            continue
        pm = resolver.find("pm")
        name = (resolver.findtext("oname") or query).strip()
        otype = resolver.findtext("otype")
        return ResolvedTarget(
            query=query,
            name=name,
            ra=ra,
            dec=dec,
            pm_ra_cosdec_mas_yr=_float(pm, "pmRA"),
            pm_dec_mas_yr=_float(pm, "pmDE"),
            parallax_mas=_float(resolver.find("plx"), "v"),
            radial_velocity_km_s=_float(resolver.find("Vel"), "v"),
            object_type=otype.strip() if otype else None,
            source=f"CDS Sesame ({resolver.get('name', 'resolver')})",
        )
    return None


def resolve_name(name: str, use_cache: bool = True) -> ResolvedTarget:
    """Resolve an object name with CDS Sesame (SIMBAD first, then NED / VizieR).

    Raises :class:`CoordinateError` when the name cannot be resolved.
    """
    name = (name or "").strip()
    if not name:
        raise CoordinateError("Object name is empty.")
    s = get_settings()
    cache = JsonCache(s.resolver_cache_dir, s.resolver_cache_ttl_s)
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:80] or "blank"
    if use_cache:
        hit = cache.get(key)
        if hit:
            return ResolvedTarget(**hit)

    q = urllib.parse.quote(name)
    errors: list[str] = []
    for template in s.sesame_urls:
        url = template.format(q=q)
        try:
            resp = request_with_retries(get_session(), "GET", url, retries=2, timeout=30)
            target = parse_sesame_xml(resp.text, name)
        except (HttpError, ET.ParseError) as exc:
            errors.append(f"{url}: {exc}")
            continue
        if target is not None:
            cache.set(key, target.to_dict())
            return target
        errors.append(f"{url}: no match")
    # Fallback for mandatory targets with challenge-supplied coordinates
    for t in MANDATORY_TARGETS:
        fb = t.get("fallback")
        if fb and name.lower() in {t["key"], t["label"].lower(), t["query"].lower()}:
            c = parse_coordinates(fb["ra"], fb["dec"])
            return ResolvedTarget(query=name, name=t["label"], ra=c.ra.deg, dec=c.dec.deg,
                                  ref_epoch="unknown", source="challenge statement", note=fb["note"])
    raise CoordinateError(f"Could not resolve object name {name!r}. " + "; ".join(errors[-2:]))


def resolve_target(text: str, epoch: Any | None = None) -> dict[str, Any]:
    """Resolve free text (coordinates *or* a name) to a position.

    Returns a dict with ``ra``, ``dec`` (deg, propagated to ``epoch`` when the
    target has proper motion; default epoch is now) and the resolver details.
    """
    text = (text or "").strip()
    if not text:
        raise CoordinateError("Empty search text.")
    # Mandatory-target shortcuts (e.g. "wise-0855")
    for t in MANDATORY_TARGETS:
        if text.lower() in {t["key"], t["label"].lower()}:
            text = t["query"]
            break
    if looks_like_coordinates(text):
        c = parse_coordinate_text(text)
        return {"query": text, "ra": c.ra.deg, "dec": c.dec.deg, "kind": "coordinates",
                "name": None, "target": None, "epoch": None}
    target = resolve_name(text)
    when = parse_time(epoch) if epoch is not None else Time.now()
    c = target.position_at(when)
    return {"query": text, "ra": float(c.ra.deg), "dec": float(c.dec.deg), "kind": "name",
            "name": target.name, "epoch": when.isot, "target": target.to_dict()}


def predicted_positions(target: ResolvedTarget, times: Iterable[Any]) -> list[tuple[float, float]]:
    """Proper-motion-propagated (ra, dec) of ``target`` at each time (vectorised)."""
    times = list(times)
    if not times:
        return []
    if not target.has_proper_motion:
        return [(target.ra, target.dec)] * len(times)
    t = Time([parse_time(x).utc.mjd for x in times], format="mjd", scale="utc")
    c = target.position_at(t)
    return [(float(a), float(b)) for a, b in zip(np.atleast_1d(c.ra.deg), np.atleast_1d(c.dec.deg))]
