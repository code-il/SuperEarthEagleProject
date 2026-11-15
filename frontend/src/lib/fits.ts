/**
 * Minimal FITS reader for the viewer: image HDUs only (BITPIX 8/16/32/-32/-64).
 *
 * The processed products from the image-processing engine are small MEF files
 * (SCI, VAR, MASK, FLAGS, RAW, WAVE; or DIFF/RATIO, SIG, MASK). Reading them in
 * the browser lets the viewer re-stretch the *science values* and show per-pixel
 * MJy/sr readouts without asking the backend for new PNGs. Nothing is modified.
 */
import type { FitsWcs } from "../types";

const BLOCK = 2880;
const CARD = 80;

export type HeaderValue = string | number | boolean;
export type FitsHeader = Record<string, HeaderValue>;

export interface FitsHdu {
  name: string;
  header: FitsHeader;
  width: number;
  height: number;
  /** Physical values (BSCALE/BZERO applied); null for header-only HDUs. */
  data: Float32Array | null;
}

function parseValue(raw: string): HeaderValue {
  const s = raw.trim();
  if (s.startsWith("'")) {
    // String: '' is an escaped quote; value ends at the first lone quote.
    let out = "";
    for (let i = 1; i < s.length; i++) {
      if (s[i] === "'") {
        if (s[i + 1] === "'") {
          out += "'";
          i++;
        } else break;
      } else out += s[i];
    }
    return out.trimEnd();
  }
  const v = s.split("/")[0].trim();
  if (v === "T") return true;
  if (v === "F") return false;
  const n = Number(v.replace(/D/g, "E"));
  return Number.isNaN(n) ? v : n;
}

/** Parse header cards starting at `offset`; returns the header and the offset of the data unit. */
export function parseHeader(buf: ArrayBuffer, offset: number): { header: FitsHeader; dataOffset: number } {
  const bytes = new Uint8Array(buf);
  const header: FitsHeader = {};
  let pos = offset;
  for (;;) {
    if (pos + CARD > bytes.length) throw new Error("FITS: header runs past end of file");
    const card = String.fromCharCode(...bytes.subarray(pos, pos + CARD));
    pos += CARD;
    const key = card.slice(0, 8).trim();
    if (key === "END") break;
    if (card.slice(8, 10) === "= " && key) header[key] = parseValue(card.slice(10));
  }
  const dataOffset = Math.ceil((pos - offset) / BLOCK) * BLOCK + offset;
  return { header, dataOffset };
}

function readData(view: DataView, offset: number, n: number, bitpix: number, bscale: number, bzero: number, blank?: number): Float32Array {
  const out = new Float32Array(n);
  const step = Math.abs(bitpix) / 8;
  for (let i = 0; i < n; i++) {
    const p = offset + i * step;
    let v: number;
    switch (bitpix) {
      case 8: v = view.getUint8(p); break;
      case 16: v = view.getInt16(p, false); break;
      case 32: v = view.getInt32(p, false); break;
      case -32: v = view.getFloat32(p, false); break;
      case -64: v = view.getFloat64(p, false); break;
      default: throw new Error(`FITS: unsupported BITPIX ${bitpix}`);
    }
    if (blank !== undefined && bitpix > 0 && v === blank) out[i] = NaN;
    else out[i] = v * bscale + bzero;
  }
  return out;
}

/** Read every HDU of a FITS file. */
export function readFits(buf: ArrayBuffer): FitsHdu[] {
  const view = new DataView(buf);
  const hdus: FitsHdu[] = [];
  let offset = 0;
  while (offset + BLOCK <= buf.byteLength) {
    const { header, dataOffset } = parseHeader(buf, offset);
    const bitpix = Number(header.BITPIX ?? 8);
    const naxis = Number(header.NAXIS ?? 0);
    const dims: number[] = [];
    for (let i = 1; i <= naxis; i++) dims.push(Number(header[`NAXIS${i}`] ?? 0));
    const n = naxis ? dims.reduce((a, b) => a * b, 1) : 0;
    const pcount = Number(header.PCOUNT ?? 0);
    const gcount = Number(header.GCOUNT ?? 1);
    const nbytes = (Math.abs(bitpix) / 8) * gcount * (pcount + n);
    const isImage = naxis >= 2 && (hdus.length === 0 || header.XTENSION === "IMAGE");
    let data: Float32Array | null = null;
    if (isImage && n > 0) {
      if (dataOffset + n * (Math.abs(bitpix) / 8) > buf.byteLength) throw new Error("FITS: data unit truncated");
      data = readData(view, dataOffset, n, bitpix, Number(header.BSCALE ?? 1), Number(header.BZERO ?? 0),
        header.BLANK === undefined ? undefined : Number(header.BLANK));
    }
    hdus.push({
      name: String(header.EXTNAME ?? (hdus.length === 0 ? "PRIMARY" : `HDU${hdus.length}`)).trim().toUpperCase(),
      header,
      width: dims[0] ?? 0,
      height: dims[1] ?? 0,
      data,
    });
    offset = dataOffset + Math.ceil(nbytes / BLOCK) * BLOCK;
  }
  if (!hdus.length) throw new Error("FITS: no HDUs found");
  return hdus;
}

export function findHdu(hdus: FitsHdu[], ...names: string[]): FitsHdu | undefined {
  for (const n of names) {
    const h = hdus.find((x) => x.name === n && x.data);
    if (h) return h;
  }
  return undefined;
}

/** First image HDU with data (fallback when no EXTNAME matches). */
export function firstImage(hdus: FitsHdu[]): FitsHdu | undefined {
  return hdus.find((h) => h.data);
}

const WCS_KEYS = /^(CTYPE|CRPIX|CRVAL|CDELT|CUNIT|PC\d_|CD\d_|LONPOLE|LATPOLE|RADESYS|EQUINOX)/;

export function headerWcs(h: FitsHeader): FitsWcs {
  const w: Record<string, HeaderValue> = {};
  for (const [k, v] of Object.entries(h)) if (WCS_KEYS.test(k)) w[k] = v;
  return w as unknown as FitsWcs;
}
