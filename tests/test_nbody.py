"""N-body engine: physics checks, Horizons parsing, and the archive correction loop (offline)."""
import numpy as np
import pytest

from backend.orbits import exo
from backend.orbits.horizons import parse_vectors
from backend.orbits.nbody import MU_SUN, System, elements_to_state, energy, integrate, kepler_advance, state_to_elements


def _two_body(a=1.0, e=0.3, mu=MU_SUN, gr=False):
    r, v = elements_to_state(mu, a, e, 0.4, 1.0, 0.2, 0.5)
    return System(["star", "p"], np.array([mu, 0.0]), np.array([np.zeros(3), r]), np.array([np.zeros(3), v]), 0.0, gr=gr)


def test_dop853_matches_analytic_kepler():
    s = _two_body()
    out, _ = integrate(s, 3650.0)
    r_ref, _ = kepler_advance(MU_SUN, s.r[1], s.v[1], 3650.0)
    assert np.linalg.norm(out.r[1] - r_ref) < 1e-9          # AU (~150 m) after 10 orbits


def test_rk4_is_fourth_order_and_less_accurate():
    s = _two_body(a=0.4, e=0.2)
    r_ref, _ = kepler_advance(MU_SUN, s.r[1], s.v[1], 200.0)
    err = [np.linalg.norm(integrate(s, 200.0, method="rk4", step=h)[0].r[1] - r_ref) for h in (0.5, 0.25)]
    assert 13 < err[0] / err[1] < 19                          # halving the step: ~16x smaller error
    assert err[1] > np.linalg.norm(integrate(s, 200.0)[0].r[1] - r_ref)


def test_energy_conserved():
    s = _two_body()
    s.mu[1] = 3e-6 * MU_SUN
    out, _ = integrate(s, 3650.0)
    assert abs(energy(out) / energy(s) - 1) < 1e-10


def test_general_relativity_gives_mercury_perihelion_advance():
    # 1PN term must reproduce Mercury's 43"/century (here over 20 years: ~8.6").
    r, v = elements_to_state(MU_SUN, 0.387098, 0.205630, 0.0, 0.0, 0.0, 0.0)
    s = System(["Sun", "Mercury"], np.array([MU_SUN, 0.0]), np.array([np.zeros(3), r]), np.array([np.zeros(3), v]), 0.0, gr=True)
    out, _ = integrate(s, 20 * 365.25)
    def peri_angle(r, v):
        h = np.cross(r, v); ev = np.cross(v, h) / MU_SUN - r / np.linalg.norm(r)
        return np.degrees(np.arctan2(ev[1], ev[0])) * 3600
    adv = peri_angle(out.r[1], out.v[1]) - peri_angle(r, v)
    assert adv == pytest.approx(42.98 / 5, rel=0.03)


def test_elements_roundtrip():
    r, v = elements_to_state(MU_SUN, 0.05, 0.1, 1.5, 0.3, 0.0, 2.0)
    el = state_to_elements(MU_SUN, r, v)
    assert el["a"] == pytest.approx(0.05, rel=1e-12) and el["e"] == pytest.approx(0.1, rel=1e-10)


HORIZONS_SAMPLE = """header
$$SOE
2461041.500000000, A.D. 2026-Jan-01 00:00:00.0000, -1.0E+00, 2.0E+00, 3.0E-01, -4.0E-03, 5.0E-03, 6.0E-04,
2461042.500000000, A.D. 2026-Jan-02 00:00:00.0000, -1.1E+00, 2.1E+00, 3.1E-01, -4.1E-03, 5.1E-03, 6.1E-04,
$$EOE
footer"""


def test_parse_horizons_vectors():
    rows = parse_vectors(HORIZONS_SAMPLE)
    assert rows.shape == (2, 7)
    assert rows[1, 0] == 2461042.5 and rows[0, 1] == -1.0 and rows[1, 6] == pytest.approx(6.1e-4)


def _planet(name, P, m, T0, e=None, ecc_err=None, w=None, inc=89.5):
    return {"name": name, "period_d": P, "mass_me": m, "t_transit": T0, "t_transit_err": 2e-4, "period_err": 2e-5,
            "ecc": e, "ecc_err": ecc_err, "omega_deg": w, "incl_deg": inc}


def test_single_planet_reproduces_its_ephemeris():
    sys = {"host": "S", "st_mass": 0.5, "planets": [_planet("S b", 4.0, 3.0, 2459000.0, e=0.1, ecc_err=0.02, w=40.0)]}
    planets, problems, _ = exo.check(sys)
    assert not problems
    s0 = exo.build(sys, planets, 2459000.0 - 20)
    _, sol = integrate(s0, 2459000.0 + 20, dense=True, rtol=1e-10, atol=1e-12)
    tt = exo.transit_times(sol, 1, 2, 2459000.0 - 20, 2459000.0 + 20, 4.0)
    lin = 2459000.0 + np.round((tt - 2459000.0) / 4.0) * 4.0
    assert np.max(np.abs(tt - lin)) * 1440 < 0.05         # minutes
    assert len(tt) == 9                                   # -16, -12, ..., +16 days


def test_refuses_what_is_not_measured_or_not_physical():
    no_mass = {"host": "S", "st_mass": 0.5, "planets": [_planet("S b", 4.0, None, 2459000.0)]}
    assert any("mass not measured" in p for p in exo.check(no_mass)[1])
    crossing = {"host": "S", "st_mass": 0.3, "planets": [_planet("S b", 3.0, 2.0, 2459000.0, e=0.25, ecc_err=0.05, w=10),
                                                         _planet("S c", 4.0, 2.0, 2459001.0, e=0.25, ecc_err=0.05, w=200)]}
    assert any("crossing" in p or "Hill" in p for p in exo.check(crossing)[1])
    no_omega = {"host": "S", "st_mass": 0.5, "planets": [_planet("S b", 4.0, 3.0, 2459000.0, e=0.2, ecc_err=0.05)]}
    assert any("omega" in p for p in exo.check(no_omega)[1])


def test_correction_loop_converges_to_archive_ephemeris():
    sys = {"host": "S", "st_mass": 0.6, "planets": [_planet("S b", 8.0, 10.0, 2459000.0), _planet("S c", 21.0, 15.0, 2459005.0)]}
    fit = exo.fit_to_archive(sys, baseline=200.0, max_iter=12)
    assert fit["ok"] and fit["converged"]
    last = fit["iterations"][-1]["planets"]
    assert all(abs(r["dT0_min"]) < r["T0_err_min"] and abs(r["dP_s"]) < r["P_err_s"] for r in last)


def test_positions_for_viewer_are_star_relative_and_continue_from_cursor():
    sys = {"host": "S", "st_mass": 0.6, "planets": [_planet("S b", 8.0, 10.0, 2459000.0), _planet("S c", 21.0, 15.0, 2459005.0)]}
    fit = exo.fit_to_archive(sys, baseline=200.0, max_iter=12)
    a = exo.positions(fit, 2459300.0, 20.0, 0.4)
    assert a["n"] == 51 and [p["name"] for p in a["planets"]] == ["S b", "S c"]
    r = np.array(a["planets"][0]["pos"]).reshape(-1, 3)
    a_b = (0.6 * (8.0 / 365.25) ** 2) ** (1 / 3)
    assert np.allclose(np.linalg.norm(r, axis=1), a_b, rtol=2e-3)       # circular orbit around the star
    assert fit["cursor"].t == 2459300.0
    b = exo.positions(fit, 2459310.0, 10.0, 0.4)                          # continues from the cursor
    assert np.allclose(np.array(b["planets"][0]["pos"][:3]), r[25], atol=1e-9)
