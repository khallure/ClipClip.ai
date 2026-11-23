export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
export const mix = (a: number, b: number, t: number) => a + (b - a) * t;

export const easeOut = (t: number) => {
  const u = 1 - clamp01(t);
  return 1 - u * u * u;
};

export const easeOutQuart = (t: number) => {
  const u = 1 - clamp01(t);
  return 1 - u * u * u * u;
};

function solveCubicBezierX(x: number, x1: number, x2: number): number {
  let u = x;
  for (let i = 0; i < 8; i += 1) {
    const cx = 3 * x1;
    const bx = 3 * (x2 - x1) - cx;
    const ax = 1 - cx - bx;
    const curX = ((ax * u + bx) * u + cx) * u - x;
    const dx = (3 * ax * u + 2 * bx) * u + cx;
    if (Math.abs(curX) < 1e-6 || Math.abs(dx) < 1e-6) break;
    u -= curX / dx;
  }
  return clamp01(u);
}

function cubicBezierY(t: number, x1: number, y1: number, x2: number, y2: number): number {
  const c = clamp01(t);
  if (c <= 0) return 0;
  if (c >= 1) return 1;
  const u = solveCubicBezierX(c, x1, x2);
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  return ((ay * u + by) * u + cy) * u;
}

export const easeOutExpo = (t: number) => cubicBezierY(t, 0.16, 1, 0.3, 1);

export const easeInOut = (t: number) => (1 - Math.cos(Math.PI * clamp01(t))) / 2;

/** Ease out with a small overshoot, settles at 1 (matches Remotion Easing.out(Easing.back(s))). */
export const easeOutBack = (t: number, s = 1.2) => {
  const u = clamp01(t) - 1;
  return 1 + (s + 1) * u * u * u + s * u * u;
};

const NAMED_COLORS: Record<string, [number, number, number, number]> = {
  transparent: [0, 0, 0, 0],
  black: [0, 0, 0, 1],
  white: [255, 255, 255, 1],
};

function parseCssColor(input: string): [number, number, number, number] {
  const s = (input || '').trim().toLowerCase();
  if (NAMED_COLORS[s]) return NAMED_COLORS[s];
  if (s.startsWith('#')) {
    const hex = s.slice(1);
    if (hex.length === 3 || hex.length === 4) {
      const r = parseInt(hex[0] + hex[0], 16);
      const g = parseInt(hex[1] + hex[1], 16);
      const b = parseInt(hex[2] + hex[2], 16);
      const a = hex.length === 4 ? parseInt(hex[3] + hex[3], 16) / 255 : 1;
      return [r, g, b, a];
    }
    if (hex.length === 6 || hex.length === 8) {
      const r = parseInt(hex.slice(0, 2), 16);
      const g = parseInt(hex.slice(2, 4), 16);
      const b = parseInt(hex.slice(4, 6), 16);
      const a = hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1;
      return [r, g, b, a];
    }
  }
  const m = s.match(/^rgba?\(\s*([0-9.]+)\s*[,\s]\s*([0-9.]+)\s*[,\s]\s*([0-9.]+)(?:\s*[,/]\s*([0-9.]+%?))?\s*\)$/);
  if (m) {
    const aRaw = m[4];
    const a = aRaw == null ? 1 : aRaw.endsWith('%') ? parseFloat(aRaw) / 100 : parseFloat(aRaw);
    return [Number(m[1]), Number(m[2]), Number(m[3]), Number.isFinite(a) ? clamp01(a) : 1];
  }
  return [255, 255, 255, 1];
}

export const colorMix = (t: number, a: string, b: string) => {
  const k = clamp01(t);
  const ca = parseCssColor(a);
  const cb = parseCssColor(b);
  const r = Math.round(mix(ca[0], cb[0], k));
  const g = Math.round(mix(ca[1], cb[1], k));
  const bl = Math.round(mix(ca[2], cb[2], k));
  const al = Number(mix(ca[3], cb[3], k).toFixed(4));
  return al >= 0.9999 ? `rgb(${r}, ${g}, ${bl})` : `rgba(${r}, ${g}, ${bl}, ${al})`;
};
