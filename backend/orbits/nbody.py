"""Newtonian N-body engine (proof of concept).

Units: AU, days, and gravitational parameters mu = G*M in AU^3/day^2.

Physics: every body attracts every other (Newton). An optional first post-Newtonian term
for the central star (Schwarzschild, 1PN) adds general relativity's perihelion advance;
it is what makes Mercury match JPL to ~km level instead of ~100 km over decades.

Integrators:
  * ``rk4``: classical fixed-step 4th-order Runge-Kutta. Simple and transparent, but not
    symplectic: energy drifts secularly, so it is used here only as a baseline.
  * ``dop853``: adaptive 8th-order Dormand-Prince (scipy) with tight tolerances and dense
    output, used for the results and for finding transit times.

Not included (state these whenever the results are shown): asteroids and dwarf planets,
tides, solar oblateness (J2), and 1PN terms between planets.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.integrate import solve_ivp

K_GAUSS = 0.01720209895                      # IAU Gaussian gravitational constant
MU_SUN = K_GAUSS**2                          # AU^3/d^2 for one solar mass
MU_EARTH_PER_SUN = 3.003489596e-6            # Earth / Sun mass ratio (IAU 2015 nominal)
C_AU_PER_DAY = 299792.458 * 86400 / 1.495978707e8


@dataclass
class System:
    """Bodies in an inertial frame. Body 0 is the central star (used for the GR term)."""
    names: list[str]
    mu: np.ndarray            # (n,)
    r: np.ndarray             # (n, 3) AU
    v: np.ndarray             # (n, 3) AU/day
    t: float                  # epoch, JD (TDB)
    gr: bool = False
    notes: list[str] = field(default_factory=list)

    def copy(self) -> "System":
        return System(list(self.names), self.mu.copy(), self.r.copy(), self.v.copy(), self.t, self.gr, list(self.notes))

    @property
    def state(self) -> np.ndarray:
        return np.concatenate([self.r.ravel(), self.v.ravel()])


def accelerations(r: np.ndarray, v: np.ndarray, mu: np.ndarray, gr: bool = False) -> np.ndarray:
    d = r[None, :, :] - r[:, None, :]                      # d[i, j] = r_j - r_i
    dist3 = np.sum(d * d, axis=-1) ** 1.5
    np.fill_diagonal(dist3, np.inf)
    a = np.sum(mu[None, :, None] * d / dist3[:, :, None], axis=1)
    if gr:
        # 1PN Schwarzschild term from the central body (0) on every other body.
        rel, vel = r[1:] - r[0], v[1:] - v[0]
        rn = np.linalg.norm(rel, axis=1)[:, None]
        v2 = np.sum(vel * vel, axis=1)[:, None]
        rv = np.sum(rel * vel, axis=1)[:, None]
        m0 = mu[0]
        a[1:] += m0 / (C_AU_PER_DAY**2 * rn**3) * ((4 * m0 / rn - v2) * rel + 4 * rv * vel)
    return a


def _deriv(n: int, mu: np.ndarray, gr: bool):
    def f(_t: float, y: np.ndarray) -> np.ndarray:
        r, v = y[: 3 * n].reshape(n, 3), y[3 * n:].reshape(n, 3)
        return np.concatenate([v.ravel(), accelerations(r, v, mu, gr).ravel()])
    return f


def energy(s: System) -> float:
    ke = 0.5 * np.sum(s.mu * np.sum(s.v * s.v, axis=1))
    pe = 0.0
    for i in range(len(s.mu)):
        for j in range(i + 1, len(s.mu)):
            pe -= s.mu[i] * s.mu[j] / np.linalg.norm(s.r[i] - s.r[j])
    return ke + pe   # in units of mu (i.e. G*E); fine for relative drift


def integrate(s: System, t_end: float, method: str = "dop853", step: float = 0.5,
              rtol: float = 1e-12, atol: float = 1e-14, dense: bool = False):
    """Advance a copy of ``s`` to ``t_end``. Returns (new System, dense solution or None)."""
    n = len(s.mu)
    f = _deriv(n, s.mu, s.gr)
    y0 = s.state
    if method == "rk4":
        span = t_end - s.t
        steps = max(1, int(np.ceil(abs(span) / step)))
        h = span / steps
        y, t = y0.copy(), s.t
        for _ in range(steps):
            k1 = f(t, y); k2 = f(t + h / 2, y + h / 2 * k1); k3 = f(t + h / 2, y + h / 2 * k2); k4 = f(t + h, y + h * k3)
            y = y + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
            t += h
        sol = None
    elif method == "dop853":
        res = solve_ivp(f, (s.t, t_end), y0, method="DOP853", rtol=rtol, atol=atol, dense_output=dense)
        if not res.success:
            raise RuntimeError(res.message)
        y, sol = res.y[:, -1], (res.sol if dense else None)
    else:
        raise ValueError(method)
    out = s.copy()
    out.r, out.v, out.t = y[: 3 * n].reshape(n, 3).copy(), y[3 * n:].reshape(n, 3).copy(), t_end
    return out, sol


# --- Kepler elements <-> state (relative to the central body) ---------------------------------

def solve_kepler(M: float, e: float) -> float:
    E = M if e < 0.8 else np.pi
    for _ in range(50):
        d = (E - e * np.sin(E) - M) / (1 - e * np.cos(E))
        E -= d
        if abs(d) < 1e-15:
            break
    return E


def elements_to_state(mu: float, a: float, e: float, inc: float, omega: float, Omega: float, M: float):
    """Position and velocity relative to the central body. Frame: x = line of nodes (Omega=0),
    z = towards the observer, so a transit happens at omega + f = 90 deg (planet in front)."""
    E = solve_kepler(M, e)
    n = np.sqrt(mu / a**3)
    den = 1 - e * np.cos(E)
    p = np.array([a * (np.cos(E) - e), a * np.sqrt(1 - e * e) * np.sin(E), 0.0])
    q = np.array([-np.sin(E), np.sqrt(1 - e * e) * np.cos(E), 0.0]) * n * a / den
    R = _rot_z(Omega) @ _rot_x(inc) @ _rot_z(omega)
    # Rx(i) tips the orbit so the out-of-plane component is y*sin(i) along +z (towards us).
    return R @ p, R @ q


def state_to_elements(mu: float, r: np.ndarray, v: np.ndarray) -> dict[str, float]:
    h = np.cross(r, v)
    rn, v2 = np.linalg.norm(r), v @ v
    evec = np.cross(v, h) / mu - r / rn
    e = float(np.linalg.norm(evec))
    a = 1 / (2 / rn - v2 / mu)
    E = np.arctan2((r @ v) / np.sqrt(mu * a), 1 - rn / a)
    M = E - e * np.sin(E)
    return {"a": float(a), "e": e, "M": float(M), "P": float(2 * np.pi * np.sqrt(a**3 / mu))}


def kepler_advance(mu: float, r: np.ndarray, v: np.ndarray, dt: float):
    """Two-body propagation of a relative state by dt (days), via f and g functions."""
    el = state_to_elements(mu, r, v)
    a, e = el["a"], el["e"]
    n = np.sqrt(mu / a**3)
    E0 = solve_kepler(el["M"], e)
    E1 = solve_kepler(el["M"] + n * dt, e)
    dE = E1 - E0
    rn = np.linalg.norm(r)
    f = 1 - a / rn * (1 - np.cos(dE))
    g = dt - (dE - np.sin(dE)) / n
    r1 = f * r + g * v
    r1n = np.linalg.norm(r1)
    fd = -np.sqrt(mu * a) / (r1n * rn) * np.sin(dE)
    gd = 1 - a / r1n * (1 - np.cos(dE))
    return r1, fd * r + gd * v


def _rot_z(t: float) -> np.ndarray:
    c, s = np.cos(t), np.sin(t)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def _rot_x(t: float) -> np.ndarray:
    # Maps in-plane y to (0, y cos i, y sin i): edge-on (i = 90 deg) puts the orbit's y along z.
    c, s = np.cos(t), np.sin(t)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
