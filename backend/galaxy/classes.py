"""Object classes for the 3D galaxy map, and the rules that assign them.

Three sources of a class, in order of trust (recorded per object as ``class_source``):

* ``spherex``   measured from the object's own SPHEREx DR1+QR3 spectrum (``spectral.py``);
* ``catalogue`` a published spectral type / object type from SIMBAD;
* ``model``     predicted from Gaia DR3 photometry (colour + absolute magnitude) by a
                k-nearest-neighbour model trained on the catalogued stars (``model.py``).

Objects with no Gaia colour and no catalogue type stay ``unclassified``: these are the
"uncharted" objects the map invites users to survey.
"""
from __future__ import annotations

import re

# id, key, label, colour (hex) — the order is the on-disk class id, do not reorder.
CLASSES: list[dict] = [
    {"id": 0, "key": "ob", "label": "O/B star", "color": "#9bb7ff", "desc": "Hot, massive, blue-white; > 10,000 K"},
    {"id": 1, "key": "a", "label": "A star", "color": "#cad7ff", "desc": "White; 7,500–10,000 K (e.g. Sirius, Vega)"},
    {"id": 2, "key": "f", "label": "F star", "color": "#f8f7ff", "desc": "Yellow-white; 6,000–7,500 K"},
    {"id": 3, "key": "g", "label": "G star", "color": "#fff4ea", "desc": "Sun-like; 5,200–6,000 K"},
    {"id": 4, "key": "k", "label": "K star", "color": "#ffd2a1", "desc": "Orange dwarf; 3,700–5,200 K"},
    {"id": 5, "key": "m", "label": "M dwarf", "color": "#ffad66", "desc": "Red dwarf; 2,400–3,700 K, the most common star"},
    {"id": 6, "key": "bd", "label": "Brown dwarf (L/T/Y)", "color": "#d1495b", "desc": "Failed star; < 2,400 K, glows in the infrared"},
    {"id": 7, "key": "wd", "label": "White dwarf", "color": "#7fe7ff", "desc": "Dead stellar core, Earth-sized"},
    {"id": 8, "key": "giant", "label": "Giant / subgiant", "color": "#ff7b54", "desc": "Evolved star with a swollen envelope"},
    {"id": 9, "key": "yso", "label": "Young stellar object", "color": "#c77dff", "desc": "Still forming; T Tauri / protostar"},
    {"id": 10, "key": "unknown", "label": "Unclassified", "color": "#6b7a90", "desc": "No colour or type yet: uncharted"},
]
CLASS_ID = {c["key"]: c["id"] for c in CLASSES}

# Flag bits stored per object.
FLAGS = {
    "planet_host": 1,     # NASA Exoplanet Archive host
    "variable": 2,        # SIMBAD variable-star type
    "multiple": 4,        # SIMBAD binary / multiple
    "spherex": 8,         # has a SPHEREx DR1+QR3 spectrum in the survey file
    "named": 16,          # has a SIMBAD name
    "high_velocity": 32,  # tangential velocity > 100 km/s (likely halo / thick disk)
    "simbad_only": 64,    # not in the Gaia table (e.g. cold brown dwarfs Gaia cannot see)
    "low_confidence": 128,  # model prediction with < 60 % vote
}

_VAR_OTYPES = {"V*", "Mi*", "LP*", "RR*", "Ce*", "BY*", "Er*", "Ro*", "Fl*", "Pu*", "EB*", "dS*", "SX*", "gD*",
               "a2*", "El*", "Ir*", "Or*", "RS*", "sr*", "C*", "S*", "WV*", "RV*", "bC*", "cC*", "Ae*"}
_MULT_OTYPES = {"**", "SB*", "EB*", "El*", "**?", "SB?", "EB?"}
_YSO_OTYPES = {"Y*O", "Y*?", "TT*", "TT?", "Or*", "Ae*", "Ae?", "out", "of?"}
_GIANT_OTYPES = {"RG*", "HB*", "AB*", "s*r", "s*y", "s*b", "SG*", "RB?", "AB?", "C*", "S*", "Mi*", "LP*", "WR*"}


def simbad_flags(otype: str | None) -> int:
    o = (otype or "").strip()
    f = 0
    if o in _VAR_OTYPES:
        f |= FLAGS["variable"]
    if o in _MULT_OTYPES:
        f |= FLAGS["multiple"]
    return f


_SP = re.compile(r"^\s*(?:sd|d|esd|usd)?\s*([OBAFGKMLTY])\s*(\d+(?:\.\d+)?)?\s*(.*)$", re.I)


def class_from_simbad(otype: str | None, sp_type: str | None) -> int | None:
    """Class from a SIMBAD object type and spectral type, or None if they say nothing useful."""
    o = (otype or "").strip()
    sp = (sp_type or "").strip()
    if o in ("WD*", "WD?") or re.match(r"^\s*D[ABOQZCX]", sp):
        return CLASS_ID["wd"]
    if o in ("BD*", "BD?"):
        return CLASS_ID["bd"]
    if o in _YSO_OTYPES:
        return CLASS_ID["yso"]
    m = _SP.match(sp)
    if m:
        letter, rest = m.group(1).upper(), (m.group(3) or "")
        if letter in "LTY":
            return CLASS_ID["bd"]
        lum = re.search(r"\b(Ia|Ib|I|II|III|IV)\b", rest)
        if lum and letter in "GKM" or (o in _GIANT_OTYPES and letter in "FGKM"):
            return CLASS_ID["giant"]
        return {"O": 0, "B": 0, "A": 1, "F": 2, "G": 3, "K": 4, "M": 5}[letter]
    if o in _GIANT_OTYPES:
        return CLASS_ID["giant"]
    return None


def class_from_hr(bp_rp: float, abs_g: float) -> int:
    """Rule-of-thumb class from the Gaia HR diagram (used only as a fallback to the model)."""
    if abs_g > 3.33 * bp_rp + 9.5 and bp_rp < 1.6:
        return CLASS_ID["wd"]
    if abs_g < 3.5 and bp_rp > 0.9:
        return CLASS_ID["giant"]
    if bp_rp > 4.0 or abs_g > 15.5:
        return CLASS_ID["bd"]
    for edge, cid in ((-0.05, 0), (0.33, 1), (0.75, 2), (0.98, 3), (1.84, 4)):
        if bp_rp < edge:
            return cid
    return CLASS_ID["m"]
