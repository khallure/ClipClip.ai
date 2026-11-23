import type {CBig, CShot, CaptionsFile} from './types';

/** The shot holding a moment (ms), from `captions.shots` (scripts/shots.py). Undefined when the file has none. */
export const shotAt = (captions: CaptionsFile | undefined, ms: number): CShot | undefined => captions?.shots?.find((s) => ms >= s.startMs && ms < s.endMs);

const cover = (bands: number[], c: number, h: number) => {
  const n = bands.length;
  const top = Math.max(0, c - h / 2) * n;
  const bot = Math.min(1, c + h / 2) * n;
  let tot = 0, ws = 0;
  for (let i = Math.floor(top); i < Math.min(n, Math.floor(bot) + 1); i++) {
    const w = Math.min(i + 1, bot) - Math.max(i, top);
    if (w > 0) { tot += bands[i] * w; ws += w; }
  }
  return ws ? tot / ws : 0;
};

/** Where (% of height) a word `hPct` % tall should sit so the person covers about `target` of it. Falls back to `dflt`. */
export function behindY(shot: CShot | undefined, hPct: number, dflt: number, target = 0.3, lo = 0, hi = 70): number {
  if (!shot?.bands) return dflt;
  const h = hPct / 100;
  let best = dflt, bc = Infinity;
  for (let y = Math.max(lo, hPct / 2 + 2); y <= Math.min(hi, 98 - hPct / 2); y += 0.5) {
    const c = Math.abs(cover(shot.bands, y / 100, h) - target) + 0.0002 * y;
    if (c < bc - 1e-9) { bc = c; best = y; }
  }
  return best;
}

/** % of height where the person's body becomes solid (the shoulders): first band covered 60% or more. */
export function shoulderY(shot: CShot | undefined, dflt: number): number {
  const b = shot?.bands;
  if (!b) return dflt;
  const i = b.findIndex((v) => v >= 0.6);
  return i < 0 ? dflt : (i / b.length) * 100;
}
/** % of height where the head starts: first band covered 5% or more. */
export function headTopY(shot: CShot | undefined, dflt: number): number {
  const b = shot?.bands;
  if (!b) return dflt;
  const i = b.findIndex((v) => v >= 0.05);
  return i < 0 ? dflt : (i / b.length) * 100;
}

/** True when this giant word is really behind the person: its layer is `behind` and the shot leaves room. */
export const isBehind = (b: CBig, layer?: string) => !b.front && layer !== 'all' && layer !== 'front';

/** The person while a word is up: bands averaged and the face box (median) over the samples between `fromMs` and `toMs`. */
export function subjectIn(captions: CaptionsFile | undefined, fromMs: number, toMs: number): {bands?: number[]; face?: [number, number, number, number]} {
  const all = captions?.subject;
  if (!all?.length) return {};
  let s = all.filter((x) => x.ms >= fromMs - 200 && x.ms <= toMs);
  if (!s.length) s = [all.reduce((a, b) => (Math.abs(b.ms - fromMs) < Math.abs(a.ms - fromMs) ? b : a))];
  const n = s[0].bands.length;
  const bands = n ? Array.from({length: n}, (_, i) => s.reduce((t, x) => t + x.bands[i], 0) / s.length) : undefined;
  const faces = s.filter((x) => x.face).map((x) => x.face!);
  const med = (k: number) => faces.map((r) => r[k]).sort((a, b) => a - b)[Math.floor(faces.length / 2)];
  return {bands, face: faces.length ? [med(0), med(1), med(2), med(3)] : undefined};
}
