import type { TargetInfo } from "../types";

/** The five mandatory challenge targets (mirrors GET /api/targets, usable offline). */
export const MANDATORY_TARGETS: TargetInfo[] = [
  { key: "barnards-star", label: "Barnard's Star", query: "Barnard's Star", why: "Fastest proper motion of any star (~10.4″/yr)." },
  { key: "epsilon-indi-b", label: "Epsilon Indi B", query: "eps Ind B", why: "Nearby brown-dwarf binary, ~4.7″/yr." },
  { key: "wise-0855", label: "WISE 0855", query: "WISE J085510.83-071442.5", why: "Coldest known brown dwarf (~285 K), ~8.1″/yr." },
  { key: "rw-aurigae", label: "RW Aurigae", query: "RW Aur", why: "Young T Tauri binary with strong variability." },
  { key: "r-draconis", label: "R Draconis", query: "R Dra", why: "Mira variable (~245-day period). Saturated at long wavelengths." },
];

/** WISE 0855 at the coordinates supplied by the challenge (sexagesimal, as given). */
export const WISE0855_COORDS = { ra: 133.795129, dec: -7.245147, raText: "08h55m10.831s", decText: "-07d14m42.53s" };
