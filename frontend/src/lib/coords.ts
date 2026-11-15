/**
 * Coordinate input parsing and formatting.
 *
 * Same convention as the backend (backend/data_acquisition/coords.py):
 * plain numbers are decimal degrees; sexagesimal RA is in hours.
 */

export type ParsedQuery =
  | { kind: "coordinates"; ra: number; dec: number; text: string }
  | { kind: "name"; name: string; text: string };

export class CoordinateInputError extends Error {}

const NUM = String.raw`[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?`;
const DECIMAL_PAIR = new RegExp(String.raw`^\s*(${NUM})\s*(?:deg|d|°)?\s*[,\s]\s*(${NUM})\s*(?:deg|d|°)?\s*$`, "i");

function sexToNumber(parts: string[], sign: number): number {
  const [a, b = "0", c = "0"] = parts;
  const v = Math.abs(Number(a)) + Number(b) / 60 + Number(c) / 3600;
  if ([a, b, c].some((x) => Number.isNaN(Number(x)))) throw new CoordinateInputError("Bad sexagesimal value");
  if (Number(b) >= 60 || Number(c) >= 60) throw new CoordinateInputError("Minutes and seconds must be below 60");
  return sign * v;
}

function splitSex(s: string): { parts: string[]; sign: number } {
  const t = s.trim();
  const sign = t.startsWith("-") || t.startsWith("−") ? -1 : 1;
  const parts = t.replace(/^[+\-−]/, "").split(/[hdms:°'′"″\s]+/i).filter(Boolean);
  return { parts, sign };
}

/** Parse an RA string: decimal degrees, or sexagesimal hours ("17h57m48.5s", "17:57:48.5", "17 57 48.5"). */
export function parseRa(s: string): number {
  const t = s.trim();
  if (new RegExp(`^${NUM}\\s*(deg|d|°)?$`, "i").test(t)) return validateRa(Number.parseFloat(t));
  const { parts } = splitSex(t);
  if (parts.length < 2 || parts.length > 3) throw new CoordinateInputError(`Cannot read RA "${s}"`);
  if (Number(parts[0]) >= 24) throw new CoordinateInputError("RA hours must be below 24");
  return validateRa(sexToNumber(parts, 1) * 15);
}

/** Parse a Dec string: decimal degrees or sexagesimal degrees ("+04d41m36s", "-07:14:42.5"). */
export function parseDec(s: string): number {
  const t = s.trim();
  if (new RegExp(`^${NUM}\\s*(deg|d|°)?$`, "i").test(t)) return validateDec(Number.parseFloat(t));
  const { parts, sign } = splitSex(t);
  if (parts.length < 2 || parts.length > 3) throw new CoordinateInputError(`Cannot read Dec "${s}"`);
  return validateDec(sexToNumber(parts, sign));
}

export function validateRa(ra: number): number {
  if (!Number.isFinite(ra) || ra < 0 || ra >= 360) throw new CoordinateInputError("RA must be in [0, 360) degrees");
  return ra;
}

export function validateDec(dec: number): number {
  if (!Number.isFinite(dec) || dec < -90 || dec > 90) throw new CoordinateInputError("Dec must be in [-90, 90] degrees");
  return dec;
}

export const FOV_MIN = 0.005;
export const FOV_MAX = 2.0;

export function validateFov(fov: number): number {
  if (!Number.isFinite(fov) || fov < FOV_MIN || fov > FOV_MAX)
    throw new CoordinateInputError(`Field of view must be between ${FOV_MIN} and ${FOV_MAX} degrees`);
  return fov;
}

/**
 * Parse free text from the search box. Returns coordinates when the text looks
 * like a coordinate pair, otherwise a name for the backend resolver (Sesame).
 * Throws CoordinateInputError when it looks like coordinates but is invalid.
 */
export function parseQuery(text: string): ParsedQuery {
  const t = text.trim();
  if (!t) throw new CoordinateInputError("Enter a target name or coordinates");
  const m = DECIMAL_PAIR.exec(t);
  if (m) return { kind: "coordinates", ra: validateRa(Number(m[1])), dec: validateDec(Number(m[2])), text: t };

  // Sexagesimal: "17h57m48.5s +04d41m36s", "17:57:48.5 +04:41:36", "17 57 48.5 +04 41 36"
  const hms = /^\s*(\d{1,2}\s*[h:\s]\s*\d{1,2}(?:\s*[m:\s]\s*\d{1,2}(?:\.\d*)?)?\s*s?)\s*,?\s*([+\-−]?\s*\d{1,2}\s*[d°:\s]\s*\d{1,2}(?:\s*[m'′:\s]\s*\d{1,2}(?:\.\d*)?)?\s*["″s]?)\s*$/i.exec(t);
  if (hms) return { kind: "coordinates", ra: parseRa(hms[1]), dec: parseDec(hms[2].replace(/\s+/g, " ")), text: t };

  const tokens = t.split(/\s+/);
  if (tokens.length === 6 && tokens.every((x) => /^[+\-−]?\d+(\.\d+)?$/.test(x))) {
    return { kind: "coordinates", ra: parseRa(tokens.slice(0, 3).join(":")), dec: parseDec(tokens.slice(3).join(":")), text: t };
  }
  if (/^[\d\s.:+\-hdms°'"]+$/i.test(t) && /\d/.test(t)) throw new CoordinateInputError(`Cannot read coordinates "${t}"`);
  return { kind: "name", name: t, text: t };
}

function pad(n: number, w: number, d = 0): string {
  const s = n.toFixed(d);
  const [i, f] = s.split(".");
  return i.padStart(w, "0") + (f !== undefined ? "." + f : "");
}

export function formatRaHms(ra: number, digits = 2): string {
  let s = (((ra % 360) + 360) % 360) / 15 * 3600;
  s = Math.round(s * 10 ** digits) / 10 ** digits;
  const h = Math.floor(s / 3600) % 24, m = Math.floor((s % 3600) / 60), sec = s % 60;
  return `${pad(h, 2)}h${pad(m, 2)}m${pad(sec, 2, digits)}s`;
}

export function formatDecDms(dec: number, digits = 1): string {
  const sign = dec < 0 ? "−" : "+";
  let s = Math.abs(dec) * 3600;
  s = Math.round(s * 10 ** digits) / 10 ** digits;
  const d = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return `${sign}${pad(d, 2)}°${pad(m, 2)}′${pad(sec, 2, digits)}″`;
}

/** Human field-of-view size: degrees, arcmin or arcsec. */
export function formatAngle(deg: number): string {
  const a = Math.abs(deg);
  if (a >= 1) return `${deg.toFixed(2)}°`;
  if (a * 60 >= 1) return `${(deg * 60).toFixed(2)}′`;
  return `${(deg * 3600).toFixed(1)}″`;
}
