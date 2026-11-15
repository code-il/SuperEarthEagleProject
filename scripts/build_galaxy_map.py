"""Build the data behind the 3D galaxy map (``/map``) from real catalogues.

Sources (all public, anonymous):

* **Gaia DR3** (ESA TAP): every star within 100 pc with parallax/error >= 10, plus a
  one-in-three sample of bright (G < 10.5) stars out to 4 kpc for the large-scale view.
  Distances are 1/parallax; positions are heliocentric Galactic cartesian, in parsecs.
* **SIMBAD** (TAP, Harvard mirror first): names, object types and spectral types of everything
  with a parallax >= 10 mas (joined to Gaia by Gaia DR3 id), plus the bright named stars further out.
  Objects SIMBAD has but Gaia does not (cold brown dwarfs such as WISE 0855) are added from SIMBAD.
* **NASA Exoplanet Archive** (``pscomppars``): every confirmed planet and its host.
* **SPHEREx DR1+QR3** spectra: merged from ``frontend/public/galaxy/spherex_survey.json``
  (built by ``python -m scripts.spherex_survey``) when it exists.

Then a k-NN model trained on the SIMBAD-typed Gaia stars predicts a class for every other
Gaia star, and the held-out accuracy is written to the metadata.

Outputs (``frontend/public/galaxy/``): ``stars.bin`` (render buffer), ``meta.json``,
``objects.json`` (named objects), ``systems.json`` (planetary systems); and
``data/galaxy/catalog.npz`` (full per-object columns for ``/api/galaxy/object/{i}``). All of these are
committed, so a fresh clone runs without rebuilding.

    python -m scripts.build_galaxy_map           # uses cached downloads
    python -m scripts.build_galaxy_map --refresh # downloads again
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from astropy.coordinates import SkyCoord
import astropy.units as u

from backend.galaxy.classes import CLASS_ID, CLASSES, FLAGS, class_from_hr, class_from_simbad, simbad_flags
from backend.galaxy.catalog import save_catalog
from backend.galaxy.model import KNNClassifier, features, train_and_evaluate

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "cache" / "galaxy"
OUT = ROOT / "frontend" / "public" / "galaxy"
GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
SIMBAD_TAP = ("https://simbad.harvard.edu/simbad/sim-tap/sync", "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
EXO_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
UA = {"User-Agent": "spherex-explorer-galaxy-map/1.0"}

GAIA_COLS = ("source_id, ra, dec, parallax, parallax_error, pmra, pmdec, radial_velocity, "
             "phot_g_mean_mag, bp_rp, bp_g, g_rp, teff_gspphot, ruwe")
TIERS = {
    "near": "parallax >= 10 AND parallax_over_error >= 10",
    "far": "parallax >= 0.25 AND parallax < 10 AND parallax_over_error >= 10 AND phot_g_mean_mag < 10.5 "
           "AND MOD(random_index, 3) = 0",
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def tap(url: str, query: str, timeout: int = 600) -> list[dict[str, str]]:
    for attempt in range(4):
        try:
            r = requests.post(url, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query},
                              timeout=timeout, headers=UA)
            if r.status_code == 200 and not r.text.lstrip().startswith("<"):
                return list(csv.DictReader(io.StringIO(r.text)))
            err = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.RequestException as exc:
            err = str(exc)
        log(f"  retry {attempt + 1} ({err[:120]})")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"TAP query failed at {url}")


def cached(name: str, fetch, refresh: bool) -> list[dict[str, str]]:
    f = RAW / f"{name}.csv"
    if f.exists() and not refresh:
        with f.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    rows = fetch()
    RAW.mkdir(parents=True, exist_ok=True)
    if rows:
        with f.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return rows


def ra_chunks(step: float):
    return [(a, a + step) for a in np.arange(0, 360, step)]


def fetch_gaia(tier: str) -> list[dict[str, str]]:
    where = TIERS[tier]

    def one(rng):
        q = f"SELECT {GAIA_COLS} FROM gaiadr3.gaia_source WHERE {where} AND ra >= {rng[0]} AND ra < {rng[1]}"
        rows = tap(GAIA_TAP, q)
        log(f"  gaia {tier} ra {rng[0]:.0f}-{rng[1]:.0f}: {len(rows)}")
        return rows

    with ThreadPoolExecutor(4) as ex:
        return [r for part in ex.map(one, ra_chunks(10.0)) for r in part]


def simbad(query: str) -> list[dict[str, str]]:
    last = None
    for url in SIMBAD_TAP:
        try:
            return tap(url, query, timeout=300)
        except RuntimeError as exc:
            last = exc
    raise last  # type: ignore[misc]


def fetch_simbad_near() -> list[dict[str, str]]:
    """Every SIMBAD object with plx >= 10 mas, with its Gaia DR3 id (if any) and proper name (if any)."""
    def one(rng):
        base = (f"FROM basic b WHERE b.plx_value >= 10 AND b.ra >= {rng[0]} AND b.ra < {rng[1]} "
                "AND b.otype NOT IN ('Pl', 'Pl?', 'G', 'GiG', 'GiC', 'ClG')")
        rows = simbad(f"SELECT b.oid, b.main_id, b.ra, b.dec, b.plx_value, b.pmra, b.pmdec, b.otype, b.sp_type {base}")
        gid = simbad(f"SELECT i.oidref AS oid, i.id FROM ident i JOIN basic b ON i.oidref = b.oid "
                     f"WHERE i.id LIKE 'Gaia DR3 %' AND b.plx_value >= 10 AND b.ra >= {rng[0]} AND b.ra < {rng[1]}")
        nm = simbad(f"SELECT i.oidref AS oid, i.id FROM ident i JOIN basic b ON i.oidref = b.oid "
                    f"WHERE i.id LIKE 'NAME %' AND b.plx_value >= 10 AND b.ra >= {rng[0]} AND b.ra < {rng[1]}")
        g = {r["oid"]: r["id"].split()[-1] for r in gid}
        n: dict[str, str] = {}
        for r in nm:
            n.setdefault(r["oid"], r["id"][5:].strip())
        for r in rows:
            r["gaia_id"] = g.get(r["oid"], "")
            r["name"] = n.get(r["oid"], "")
        log(f"  simbad ra {rng[0]:.0f}-{rng[1]:.0f}: {len(rows)}")
        return rows

    with ThreadPoolExecutor(3) as ex:
        return [r for part in ex.map(one, ra_chunks(15.0)) for r in part]


def fetch_simbad_bright() -> list[dict[str, str]]:
    """Bright (V < 5) stars beyond 100 pc: the landmarks of the large-scale view."""
    rows = simbad("SELECT b.oid, b.main_id, b.ra, b.dec, b.plx_value, b.pmra, b.pmdec, b.otype, b.sp_type, f.V "
                  "FROM basic b JOIN allfluxes f ON f.oidref = b.oid "
                  "WHERE f.V < 5 AND b.plx_value > 0.2 AND b.plx_value < 10")
    nm = simbad("SELECT i.oidref AS oid, i.id FROM ident i JOIN allfluxes f ON f.oidref = i.oidref "
                "JOIN basic b ON b.oid = i.oidref WHERE f.V < 5 AND b.plx_value > 0.2 AND b.plx_value < 10 "
                "AND i.id LIKE 'NAME %'")
    n: dict[str, str] = {}
    for r in nm:
        n.setdefault(r["oid"], r["id"][5:].strip())
    for r in rows:
        r["name"], r["gaia_id"] = n.get(r["oid"], ""), ""
    return rows


def fetch_exoplanets() -> list[dict[str, str]]:
    q = ("SELECT pl_name, hostname, gaia_dr3_id, ra, dec, sy_dist, sy_plx, pl_orbsmax, pl_orbper, pl_rade, pl_bmasse, "
         "pl_eqt, pl_orbeccen, discoverymethod, disc_year, st_spectype, st_teff, st_rad, st_mass, sy_pnum, "
         "pl_orbpererr1, pl_orbpererr2, pl_orbsmaxerr1, pl_orbsmaxerr2, pl_orbeccenerr1, pl_orbeccenerr2, "
         "pl_tranmid, pl_tranmiderr1, pl_tranmiderr2, pl_orbtper, pl_orbtpererr1, pl_orbtpererr2, pl_orblper, "
         "pl_orbincl, st_masserr1, st_masserr2 "
         "FROM pscomppars")
    r = requests.get(EXO_TAP, params={"query": q, "format": "csv"}, timeout=300, headers=UA)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def serr(row: dict[str, str], base: str) -> float:
    """Symmetric 1-sigma from an archive's ``<base>1`` (+) and ``<base>2`` (-) columns."""
    e = [abs(f(row.get(base + "1"))), abs(f(row.get(base + "2")))]
    e = [x for x in e if math.isfinite(x)]
    return float(np.mean(e)) if e else float("nan")


def f(v: str | None, default=np.nan) -> float:
    try:
        x = float(v)  # type: ignore[arg-type]
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def clean_name(main_id: str) -> str:
    s = " ".join(main_id.replace("V* ", "").replace("* ", "").split())
    return s[5:] if s.startswith("NAME ") else s


def fetch_simbad_names() -> list[dict[str, str]]:
    """Every SIMBAD ``NAME`` alias of the near (plx >= 10) and bright (V < 5) objects."""
    near = simbad("SELECT i.oidref AS oid, i.id FROM ident i JOIN basic b ON i.oidref = b.oid "
                  "WHERE i.id LIKE 'NAME %' AND b.plx_value >= 10")
    bright = simbad("SELECT i.oidref AS oid, i.id FROM ident i JOIN allfluxes f ON f.oidref = i.oidref "
                    "JOIN basic b ON b.oid = i.oidref WHERE f.V < 5 AND b.plx_value > 0.2 AND b.plx_value < 10 "
                    "AND i.id LIKE 'NAME %'")
    return near + bright


_COMPONENT = re.compile(r"\s([A-D]{1,2})$")


def best_name(main_id: str, names: list[str]) -> str:
    """Display name: SIMBAD's own NAME main id, else the alias matching the component (Sirius B), else the shortest."""
    if main_id.startswith("NAME "):
        return main_id[5:].strip()
    if not names:
        return clean_name(main_id)
    comp = _COMPONENT.search(main_id)
    if comp:
        hit = [n for n in names if n.endswith(" " + comp.group(1))]
        if hit:
            return min(hit, key=len)
    plain = [n for n in names if not _COMPONENT.search(n)] or names
    # Prefer real words over catalogue abbreviations ("vMA 2"), then possessive common names
    # ("van Maanen's Star"), then the shortest ("Polaris" over "North Star").
    worded = [n for n in plain if re.search(r"[a-z]{4,}", n)] or plain
    return min(worded, key=lambda n: ("'s " not in n, len(n), n))


def main(argv: list[str]) -> None:
    refresh = "--refresh" in argv
    t0 = time.time()
    log("Gaia DR3 near tier (<= 100 pc)")
    near = cached("gaia_near", lambda: fetch_gaia("near"), refresh)
    log(f"  {len(near)} stars")
    log("Gaia DR3 far tier (bright, <= 4 kpc)")
    far = cached("gaia_far", lambda: fetch_gaia("far"), refresh)
    log(f"  {len(far)} stars")
    log("SIMBAD types and names")
    sim = cached("simbad_near", fetch_simbad_near, refresh)
    bright = cached("simbad_bright", fetch_simbad_bright, refresh)
    aliases: dict[str, list[str]] = {}
    for r in cached("simbad_names", fetch_simbad_names, refresh):
        aliases.setdefault(r["oid"], []).append(r["id"][5:].strip())
    for r in sim + bright:
        r["name"] = best_name(r["main_id"], aliases.get(r["oid"], [])) if (aliases.get(r["oid"]) or r["main_id"].startswith("NAME ")) else ""
        r["aliases"] = sorted(set(aliases.get(r["oid"], [])) | {clean_name(r["main_id"])})
    log(f"  {len(sim)} near, {len(bright)} bright, {len(aliases)} with proper names")
    log("NASA Exoplanet Archive")
    exo = cached("exoplanets", fetch_exoplanets, refresh)
    log(f"  {len(exo)} planets")

    # ---------------- Gaia table ----------------
    gaia = near + far
    seen: set[str] = set()
    rows = []
    # Faint (G > 19) sources with a poor astrometric fit (RUWE > 1.4) are mostly background stars with
    # spurious parallaxes in crowded Galactic-plane fields, not stars within 100 pc. Drop them unless
    # SIMBAD knows the object.
    known = {r.get("gaia_id") for r in sim if r.get("gaia_id")}
    n_spurious = 0
    for r in gaia:
        if r["source_id"] in seen:
            continue
        seen.add(r["source_id"])
        if f(r["phot_g_mean_mag"]) > 19 and f(r["ruwe"]) > 1.4 and r["source_id"] not in known:
            n_spurious += 1
            continue
        rows.append(r)
    log(f"  dropped {n_spurious} faint sources with unreliable astrometry (G > 19, RUWE > 1.4)")
    n_gaia = len(rows)
    col = lambda k: np.array([f(r[k]) for r in rows])  # noqa: E731
    source_id = np.array([int(r["source_id"]) for r in rows], dtype=np.int64)
    ra, dec, plx = col("ra"), col("dec"), col("parallax")
    pmra, pmdec, rv = col("pmra"), col("pmdec"), col("radial_velocity")
    gmag, bp_rp, bp_g, g_rp = col("phot_g_mean_mag"), col("bp_rp"), col("bp_g"), col("g_rp")
    teff, ruwe = col("teff_gspphot"), col("ruwe")
    name = np.array([""] * n_gaia, dtype=object)
    alias: dict[int, list[str]] = {}  # map index -> other names (search)
    otype = np.array([""] * n_gaia, dtype=object)
    sptype = np.array([""] * n_gaia, dtype=object)
    cls = np.full(n_gaia, -1, np.int16)
    src = np.array(["model"] * n_gaia, dtype=object)
    flags = np.zeros(n_gaia, np.int64)
    prob = np.ones(n_gaia, np.float32)

    # ---------------- SIMBAD join ----------------
    idx_of = {int(s): i for i, s in enumerate(source_id)}
    extra = []  # SIMBAD-only objects
    for r in sim + bright:
        gid = r.get("gaia_id") or ""
        i = idx_of.get(int(gid)) if gid.isdigit() else None
        nm = r["name"] or clean_name(r["main_id"])
        c = class_from_simbad(r["otype"], r["sp_type"])
        if i is not None:
            if not name[i] or r["name"]:
                name[i] = nm
            alias.setdefault(i, []).extend(r["aliases"])
            otype[i], sptype[i] = r["otype"], r["sp_type"]
            flags[i] |= FLAGS["named"] | simbad_flags(r["otype"])
            if c is not None:
                cls[i], src[i] = c, "catalogue"
        elif f(r["plx_value"]) > 0 and (c is not None or r["name"]) and r["otype"] not in ("Pl", "Pl?"):
            extra.append((r, nm, c))
    extra_alias = [e[0]["aliases"] for e in extra]
    # SIMBAD objects whose Gaia source is not in the tiers (no DR3 parallax, e.g. the Luhman 16 binary, or
    # parallax/error < 10) are placed with SIMBAD's astrometry.
    log(f"  {int((src == 'catalogue').sum())} Gaia stars with catalogue types, {len(extra)} SIMBAD-only objects")

    # ---------------- Model ----------------
    abs_g = gmag + 5 * np.log10(plx / 100.0)
    ok = np.isfinite(abs_g) & np.isfinite(bp_rp) & np.isfinite(bp_g) & np.isfinite(g_rp)
    lab = ok & (cls >= 0) & (cls != CLASS_ID["yso"])
    X = features(abs_g, bp_rp, bp_g, g_rp)
    model, report = train_and_evaluate(X[lab], cls[lab].astype(int))
    log(f"  model accuracy {report['accuracy']} on {report['n_test']} held-out stars")
    # Below G ~ 19 the BP flux is dominated by noise and background (the "BP excess"), which makes faint
    # red dwarfs look blue and pushes them onto the white-dwarf sequence. Those stars get a second model
    # that only uses M_G and G-RP, and are always flagged low-confidence.
    faint_g = 19.0
    todo = ok & (cls < 0)
    bright_todo, faint_todo = todo & ~(gmag > faint_g), todo & (gmag > faint_g)
    P = model.predict_proba(X[bright_todo])
    cls[bright_todo], prob[bright_todo] = P.argmax(1), P.max(1)
    Xf = np.column_stack([abs_g, g_rp])
    model_f, report_f = train_and_evaluate(Xf[lab], cls[lab].astype(int))
    faint_lab = np.where(lab & (gmag > 17.5))[0]  # how it does on faint labelled stars (resubstitution-free: k-NN on the rest)
    if len(faint_lab) >= 50:
        rest = KNNClassifier(15).fit(Xf[lab & ~(gmag > 17.5)], cls[lab & ~(gmag > 17.5)].astype(int))
        pf = rest.predict_proba(Xf[faint_lab]).argmax(1)
        report_f["accuracy_on_faint_labelled"] = round(float((pf == cls[faint_lab]).mean()), 3)
        report_f["n_faint_labelled"] = int(len(faint_lab))
    Pf = model_f.predict_proba(Xf[faint_todo])
    cls[faint_todo], prob[faint_todo] = Pf.argmax(1), Pf.max(1)
    flags[faint_todo] |= FLAGS["low_confidence"]
    report["faint_model"] = {"applies_to": f"G > {faint_g:g} (BP-RP unreliable)", "n": int(faint_todo.sum()),
                             "method": "k-nearest neighbours (k=15) on standardised [M_G, G-RP]",
                             **{k: report_f[k] for k in ("accuracy", "n_test", "accuracy_on_faint_labelled", "n_faint_labelled")
                                if k in report_f}}
    flags[todo & (prob < 0.6)] |= FLAGS["low_confidence"]
    # The HR rule is a sanity floor for very sparse regions of the diagram (no neighbours agree).
    for i in np.where(bright_todo & (prob < 0.34))[0]:
        cls[i] = class_from_hr(bp_rp[i], abs_g[i])
    # Where the model and the textbook HR-diagram boundaries disagree by more than one type, flag it uncertain.
    ti = np.where(bright_todo)[0]
    hr = np.array([class_from_hr(bp_rp[i], abs_g[i]) for i in ti])
    disagree = (hr != cls[ti]) & ~((hr <= 5) & (cls[ti] <= 5) & (np.abs(hr - cls[ti]) <= 1))
    flags[ti[disagree]] |= FLAGS["low_confidence"]
    report["hr_rule_disagreement"] = round(float(disagree.mean()), 3)
    cls[cls < 0] = CLASS_ID["unknown"]
    src[cls == CLASS_ID["unknown"]] = "none"

    # ---------------- Append SIMBAD-only ----------------
    cols = {"source_id": source_id, "ra": ra, "dec": dec, "plx": plx, "pmra": pmra, "pmdec": pmdec, "rv": rv, "gmag": gmag,
            "bp_rp": bp_rp, "teff": teff, "ruwe": ruwe, "name": name, "otype": otype, "sptype": sptype, "cls": cls,
            "src": src, "flags": flags, "prob": prob}
    defaults = {"rv": np.nan, "gmag": np.nan, "bp_rp": np.nan, "teff": np.nan, "ruwe": np.nan, "prob": 1.0}

    def append_many(rows: list[dict]) -> None:
        if not rows:
            return
        for k, arr in cols.items():
            vals = [r.get(k, defaults.get(k)) for r in rows]
            cols[k] = np.concatenate([arr, np.array(vals, dtype=arr.dtype)])

    for k, al in enumerate(extra_alias):
        alias[n_gaia + k] = al
    append_many([dict(source_id=-int(r["oid"]), ra=f(r["ra"]), dec=f(r["dec"]), plx=f(r["plx_value"]), pmra=f(r["pmra"]),
                      pmdec=f(r["pmdec"]), name=nm, otype=r["otype"], sptype=r["sp_type"],
                      cls=c if c is not None else CLASS_ID["unknown"], src="catalogue" if c is not None else "none",
                      flags=FLAGS["simbad_only"] | FLAGS["named"] | simbad_flags(r["otype"])) for r, nm, c in extra])

    # ---------------- Planetary systems ----------------
    systems: dict[str, dict] = {}
    for p in exo:
        h = p["hostname"]
        s = systems.setdefault(h, {"host": h, "gaia_dr3_id": p["gaia_dr3_id"].split()[-1] if p["gaia_dr3_id"] else "",
                                   "ra": f(p["ra"]), "dec": f(p["dec"]), "dist_pc": f(p["sy_dist"]),
                                   "st_spectype": p["st_spectype"], "st_teff": f(p["st_teff"]),
                                   "st_rad": f(p["st_rad"]), "st_mass": f(p["st_mass"]), "planets": []})
        s["planets"].append({"name": p["pl_name"], "a_au": f(p["pl_orbsmax"]), "period_d": f(p["pl_orbper"]),
                             "radius_re": f(p["pl_rade"]), "mass_me": f(p["pl_bmasse"]), "teq_k": f(p["pl_eqt"]),
                             "ecc": f(p["pl_orbeccen"]), "method": p["discoverymethod"], "year": p["disc_year"],
                             # uncertainties are symmetrised (mean of |+err| and |-err|)
                             "period_err": serr(p, "pl_orbpererr"), "a_err": serr(p, "pl_orbsmaxerr"),
                             "ecc_err": serr(p, "pl_orbeccenerr"),
                             # epochs for the orbital phase (BJD, TDB): transit midpoint or periastron time
                             "t_transit": f(p["pl_tranmid"]), "t_transit_err": serr(p, "pl_tranmiderr"),
                             "t_peri": f(p["pl_orbtper"]), "t_peri_err": serr(p, "pl_orbtpererr"),
                             "omega_deg": f(p["pl_orblper"]), "incl_deg": f(p["pl_orbincl"])})
    idx_of = {int(v): k for k, v in enumerate(cols["source_id"]) if v > 0}
    base = len(cols["source_id"])
    new_hosts: list[dict] = []
    for s in systems.values():
        gid = s["gaia_dr3_id"]
        i = idx_of.get(int(gid)) if gid.isdigit() else None
        if i is None and math.isfinite(s["dist_pc"]) and s["dist_pc"] > 0:
            # Hosts outside the Gaia tiers (most Kepler/TESS hosts are 100 pc - 2 kpc): add them as objects.
            sc = class_from_simbad("", s["st_spectype"])
            if sc is None and math.isfinite(s["st_teff"]):
                t = s["st_teff"]
                sc = 0 if t > 10000 else 1 if t > 7500 else 2 if t > 6000 else 3 if t > 5200 else 4 if t > 3700 else 5 if t > 2400 else 6
            new_hosts.append(dict(source_id=int(gid) if gid.isdigit() else 0, ra=s["ra"], dec=s["dec"], plx=1000.0 / s["dist_pc"],
                                  pmra=np.nan, pmdec=np.nan, name=s["host"], otype="", sptype=s["st_spectype"],
                                  cls=sc if sc is not None else CLASS_ID["unknown"], src="catalogue" if sc is not None else "none",
                                  flags=FLAGS["named"] | FLAGS["planet_host"]))
            i = base + len(new_hosts) - 1
        if i is None:  # no Gaia match and no distance: cannot be placed
            continue
        s["i"] = int(i)
    n_host_added = len(new_hosts)
    append_many(new_hosts)
    (source_id, ra, dec, plx, pmra, pmdec, rv, gmag, bp_rp, teff, ruwe, name, otype, sptype, cls, src, flags, prob) = (
        cols[k] for k in ("source_id", "ra", "dec", "plx", "pmra", "pmdec", "rv", "gmag", "bp_rp", "teff", "ruwe", "name",
                          "otype", "sptype", "cls", "src", "flags", "prob"))
    for s in systems.values():
        if "i" in s:
            flags[s["i"]] |= FLAGS["planet_host"]
            if not name[s["i"]]:
                name[s["i"]] = s["host"]

    # ---------------- Kinematics ----------------
    abs_g = gmag + 5 * np.log10(plx / 100.0)
    abs_g = np.where(np.isfinite(abs_g), abs_g, np.where(flags & FLAGS["planet_host"], 4.5, 18.0))
    dist = 1000.0 / plx
    vtan = 4.74047 * np.hypot(np.nan_to_num(pmra), np.nan_to_num(pmdec)) / 1000.0 * dist
    flags[vtan > 100] |= FLAGS["high_velocity"]
    for s in systems.values():
        if "i" in s:
            s["dist_pc"] = float(dist[s["i"]])
    log(f"  {len(systems)} planetary systems ({n_host_added} hosts added beyond the Gaia tiers)")

    # ---------------- SPHEREx survey ----------------
    survey_file = OUT / "spherex_survey.json"
    survey = json.loads(survey_file.read_text(encoding="utf-8")) if survey_file.exists() else {"objects": []}
    n_spx = n_spx_class = 0

    def match(o: dict) -> int | None:
        d = (ra - o["ra"]) ** 2 * np.cos(np.radians(o["dec"])) ** 2 + (dec - o["dec"]) ** 2
        i = int(np.nanargmin(d))
        return i if d[i] <= (30 / 3600) ** 2 else None  # 30" (proper motion between epochs)

    # Object indices change between builds: re-key the survey and the spectra measured live in the app.
    for o in survey.get("objects", []):
        o["i"] = match(o)
    survey["objects"] = [o for o in survey.get("objects", []) if o["i"] is not None]
    if survey_file.exists():
        survey_file.write_text(json.dumps(survey, separators=(",", ":")), encoding="utf-8")
    live_dir = RAW / "spectra"
    if live_dir.exists():
        live = []
        for fp in live_dir.glob("*.json"):
            try:
                live.append(json.loads(fp.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            fp.unlink()
        for o in live:
            o["i"] = match(o)
            if o["i"] is not None:
                (live_dir / f"{o['i']}.json").write_text(json.dumps(o), encoding="utf-8")
    for o in survey["objects"]:
        if o.get("status") != "ok":
            continue
        i = o["i"]
        flags[i] |= FLAGS["spherex"]
        spx = o.get("classification") or {}
        # A 0.75-5 um colour temperature cannot tell a white dwarf or a giant from a main-sequence star
        # of the same temperature, so SPHEREx only replaces a class that no catalogue gave, or one with
        # molecular-band evidence of a brown dwarf. Otherwise it is shown as supporting evidence.
        sc, conf = spx.get("class_id"), spx.get("confidence", 0)
        molecular = sc == CLASS_ID["bd"] and bool(spx.get("features"))
        if sc is not None and conf >= 0.5 and (src[i] in ("model", "none") or molecular):
            cls[i], src[i], prob[i] = sc, "spherex", conf
            n_spx_class += 1
        n_spx += 1
    log(f"  {n_spx} objects with SPHEREx DR1+QR3 spectra ({n_spx_class} classified by SPHEREx)")

    # ---------------- Positions & render buffer ----------------
    c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, distance=dist * u.pc, frame="icrs").galactic
    xyz = np.column_stack([c.cartesian.x.value, c.cartesian.y.value, c.cartesian.z.value]).astype(np.float32)
    n = len(xyz)
    mag_byte = np.clip((abs_g + 8) * 8, 0, 255).astype(np.uint8)      # M_G -8..+24 in 1/8 mag
    col_byte = np.clip((np.nan_to_num(bp_rp, nan=-9) + 1) * 40, 0, 255).astype(np.uint8)  # BP-RP; 0 = unknown
    col_byte[~np.isfinite(bp_rp)] = 0
    src_id = np.select([src == "spherex", src == "catalogue", src == "model"], [3, 2, 1], 0)
    cls_byte = (cls.astype(np.int64) + 16 * src_id).astype(np.uint8)   # low nibble class, high nibble source
    attrs = np.column_stack([cls_byte, (flags & 0xFF).astype(np.uint8), mag_byte, col_byte]).astype(np.uint8)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "stars.bin").open("wb") as fh:
        fh.write(xyz.tobytes())
        fh.write(attrs.tobytes())
        fh.write(np.clip(prob * 255, 0, 255).astype(np.uint8).tobytes())

    # ---------------- Named objects (search, labels) ----------------
    objects = []
    junk = ("Gaia DR", "TIC ", "UCAC", "TYC ", "2MASS J", "USNO", "GSC ", "PM J", "SDSS J", "LAMOST", "APASS", "CWISE", "UPM ",
            "SCR J", "WT ", "Gaia EDR3", "ASASSN", "ATO J", "ZTF", "PS1 ", "CRTS", "1RXS", "UGCS", "PSO J", "DENIS J", "WISEA J")
    keep_id = (FLAGS["planet_host"] | FLAGS["spherex"])
    for i in np.where(name != "")[0]:
        nm = str(name[i])
        if nm.startswith(junk) and not (int(flags[i]) & keep_id) and cls[i] != CLASS_ID["bd"]:
            continue
        o = {"i": int(i), "n": str(name[i]), "c": int(cls[i]), "f": int(flags[i]),
             "d": round(float(dist[i]), 3), "t": str(otype[i] or ""), "s": str(sptype[i] or "")}
        al = sorted({a for a in alias.get(int(i), []) if a != nm and not a.startswith(junk)})[:8]
        if al:
            o["a"] = al
        objects.append(o)
    (OUT / "objects.json").write_text(json.dumps(objects, separators=(",", ":")), encoding="utf-8")
    sys_list = sorted((s for s in systems.values() if "i" in s), key=lambda s: s["dist_pc"])
    def jclean(o):
        if isinstance(o, float):
            return None if not math.isfinite(o) else round(o, 5)
        if isinstance(o, dict):
            return {k: jclean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [jclean(v) for v in o]
        return o
    (OUT / "systems.json").write_text(json.dumps(jclean(sys_list), separators=(",", ":")), encoding="utf-8")

    counts = {c["key"]: int((cls == c["id"]).sum()) for c in CLASSES}
    by_source = {s: int((src == s).sum()) for s in ("spherex", "catalogue", "model", "none")}
    meta = {
        "version": 1, "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "count": n,
        "layout": {"positions": "float32 x,y,z (pc), heliocentric Galactic: +x to Galactic centre, +z to north Galactic pole",
                   "attributes": "uint8 class + 16*source (0 none, 1 model, 2 catalogue, 3 spherex), flags(low byte), absmag byte (M_G = b/8 - 8), colour byte (BP-RP = b/40 - 1; 0 = none)",
                   "confidence": "uint8 class probability * 255"},
        "classes": CLASSES, "flags": FLAGS, "counts": counts, "class_sources": by_source,
        "model": report,
        "provenance": {
            "gaia": {"release": "Gaia DR3", "near": TIERS["near"], "far": TIERS["far"], "n": n_gaia,
                     "dropped_unreliable": n_spurious, "quality_cut": "drop G > 19 and RUWE > 1.4 unless in SIMBAD"},
            "simbad": {"n_near": len(sim), "n_bright": len(bright), "n_simbad_only": len(extra)},
            "exoplanets": {"table": "NASA Exoplanet Archive pscomppars", "n_planets": len(exo), "n_systems": len(systems)},
            "spherex": {"releases": survey.get("releases", ["DR1", "QR3"]), "n": n_spx,
                        "file": "spherex_survey.json" if survey_file.exists() else None},
        },
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    save_catalog(source_id=source_id, ra=ra, dec=dec, plx=plx, pmra=pmra, pmdec=pmdec,
                        rv=rv, gmag=gmag, bp_rp=bp_rp, teff=teff, ruwe=ruwe, name=name.astype(str),
                        otype=otype.astype(str), sptype=sptype.astype(str), cls=cls, src=src.astype(str),
                        flags=flags, prob=prob, dist=dist, vtan=vtan, xyz=xyz)
    log(f"done: {n} objects, {(OUT / 'stars.bin').stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s")
    log(json.dumps(counts))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:])
