import pytest
from astropy.time import Time

from backend.data_acquisition import coords
from backend.data_acquisition.coords import (CoordinateError, ResolvedTarget, parse_coordinate_text,
                                             parse_coordinates, parse_date_range, parse_sesame_xml,
                                             parse_wavelength_range, predicted_positions,
                                             resolve_target, validate_fov)

WISE0855_RA = 133.7951292  # 08h55m10.831s
WISE0855_DEC = -7.2451472  # -07d14m42.53s


@pytest.mark.parametrize("ra,dec", [
    ("08h55m10.831s", "-7d14m42.53s"),
    ("08:55:10.831", "-07:14:42.53"),
    ("08 55 10.831", "-07 14 42.53"),
    (WISE0855_RA, WISE0855_DEC),
    (str(WISE0855_RA), str(WISE0855_DEC)),
])
def test_parse_coordinates_formats_agree(ra, dec):
    c = parse_coordinates(ra, dec)
    assert c.ra.deg == pytest.approx(WISE0855_RA, abs=1e-6)
    assert c.dec.deg == pytest.approx(WISE0855_DEC, abs=1e-6)


@pytest.mark.parametrize("ra,dec", [(360, 0), (-1, 0), (10, 91), (10, -90.5), (None, 1), ("", "1"),
                                     ("abc", "1"), (float("nan"), 0)])
def test_parse_coordinates_rejects_invalid(ra, dec):
    with pytest.raises(CoordinateError):
        parse_coordinates(ra, dec)


@pytest.mark.parametrize("text", ["133.7951292 -7.2451472", "133.7951292, -7.2451472",
                                  "08 55 10.831 -07 14 42.53", "08h55m10.831s -7d14m42.53s"])
def test_parse_coordinate_text(text):
    c = parse_coordinate_text(text)
    assert c.ra.deg == pytest.approx(WISE0855_RA, abs=1e-5)
    assert c.dec.deg == pytest.approx(WISE0855_DEC, abs=1e-5)


def test_parse_coordinate_text_rejects_names():
    with pytest.raises(CoordinateError):
        parse_coordinate_text("Barnard's Star")


@pytest.mark.parametrize("fov", [0, -1, 5, "x", None, float("inf")])
def test_validate_fov_rejects(fov):
    with pytest.raises(CoordinateError):
        validate_fov(fov)


def test_validate_fov_ok():
    assert validate_fov("0.1") == 0.1


def test_date_and_wavelength_ranges():
    t0, t1 = parse_date_range(("2025-08-01", None))
    assert t0.isot.startswith("2025-08-01") and t1 > t0
    t0, t1 = parse_date_range("2025-08-01/2026-01-01")
    assert t1.isot.startswith("2026-01-01")
    t0, _ = parse_date_range((60902.5, None))
    assert t0.mjd == pytest.approx(60902.5)
    with pytest.raises(CoordinateError):
        parse_date_range(("2026-01-01", "2025-01-01"))
    assert parse_wavelength_range((None, 2.0)) == (0.0, 2.0)
    with pytest.raises(CoordinateError):
        parse_wavelength_range((3, 1))


SESAME_XML = """<?xml version="1.0"?><Sesame><Target option="S"><name>Barnard's Star</name>
<Resolver name="S=Simbad"><otype>BY*</otype><oname>NAME Barnard's star</oname>
<jradeg>269.4520769586187</jradeg><jdedeg>4.69336496657667</jdedeg>
<pm><v>10393.349</v><pmRA>-801.551</pmRA><pmDE>10362.394</pmDE></pm>
<plx><v>546.9759</v></plx><Vel><v>-110.11</v></Vel></Resolver></Target></Sesame>"""


def test_parse_sesame_xml():
    t = parse_sesame_xml(SESAME_XML, "Barnard's Star")
    assert t.name == "NAME Barnard's star"
    assert t.pm_dec_mas_yr == pytest.approx(10362.394)
    assert t.parallax_mas == pytest.approx(546.9759)
    assert t.object_type == "BY*"


def test_proper_motion_propagation_barnard():
    t = parse_sesame_xml(SESAME_XML, "x")
    c2000 = t.skycoord()
    c2026 = t.position_at("2026-01-01")
    sep = c2000.separation(c2026).arcsec
    # ~10.39"/yr * 26 yr ~ 270"
    assert 260 < sep < 280
    # moving north
    assert c2026.dec.deg > c2000.dec.deg
    preds = predicted_positions(t, [Time("2025-08-15").mjd, "2026-08-15"])
    assert len(preds) == 2
    # ~10.4" per year along Dec
    assert (preds[1][1] - preds[0][1]) * 3600 == pytest.approx(10.36, abs=0.2)


def test_no_proper_motion_returns_reference_position():
    t = ResolvedTarget(query="x", name="x", ra=10.0, dec=20.0)
    c = t.position_at("2030-01-01")
    assert c.ra.deg == 10.0 and c.dec.deg == 20.0
    assert predicted_positions(t, ["2025-01-01", "2026-01-01"]) == [(10.0, 20.0)] * 2


def test_resolve_target_coordinates_skip_network(monkeypatch):
    monkeypatch.setattr(coords, "resolve_name", lambda *a, **k: pytest.fail("should not resolve"))
    out = resolve_target("08 55 10.831 -07 14 42.53")
    assert out["kind"] == "coordinates"
    assert out["ra"] == pytest.approx(WISE0855_RA, abs=1e-5)


def test_resolve_name_uses_sesame_and_caches(monkeypatch):
    calls = []

    class Resp:
        text = SESAME_XML

    def fake(session, method, url, **kw):
        calls.append(url)
        return Resp()

    monkeypatch.setattr(coords, "request_with_retries", fake)
    a = coords.resolve_name("Barnard's Star")
    b = coords.resolve_name("Barnard's Star")
    assert a.ra == b.ra
    assert len(calls) == 1  # second call served from cache


def test_resolve_name_fallback_for_wise0855(monkeypatch):
    from backend.data_acquisition.http import HttpError

    def boom(*a, **k):
        raise HttpError("down")

    monkeypatch.setattr(coords, "request_with_retries", boom)
    t = coords.resolve_name("WISE 0855", use_cache=False)
    assert t.source == "challenge statement"
    assert t.ra == pytest.approx(WISE0855_RA, abs=1e-5)
    with pytest.raises(CoordinateError):
        coords.resolve_name("Not A Real Object", use_cache=False)
