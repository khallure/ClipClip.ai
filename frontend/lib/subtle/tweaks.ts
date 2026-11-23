import type {CPage, CaptionsFile, Style} from './engine';
export type Preset = Style & {group?: string; variants?: Record<string, Record<string, any>>};

/** `shadow: false` turns off the text's drop shadow and the automatic legibility halo (the clean, flat look). */
export type Tweaks = {accent?: string; size?: number; y?: number; case?: 'upper' | 'sentence' | 'lower'; shadow?: false};
export const isTweaked = (t?: Tweaks) => !!t && (t.accent != null || (t.size != null && t.size !== 100) || t.y != null || t.case != null || t.shadow === false);

type Obj = Record<string, any>;
export function deepMerge(base: any, over: any): any {
  if (!isObj(base) || !isObj(over)) return over;
  const out: Obj = {...base};
  for (const [k, v] of Object.entries(over)) out[k] = isObj(v) && isObj(out[k]) ? deepMerge(out[k], v) : v;
  return out;
}
const isObj = (v: any) => v && typeof v === 'object' && !Array.isArray(v);
function setPath(o: Obj, path: string, v: any) {
  const ks = path.split('.');
  let c = o;
  ks.slice(0, -1).forEach((k) => (c = c[k] ??= {}));
  c[ks[ks.length - 1]] = v;
}
function getPath(o: Obj, path: string) {
  return path.split('.').reduce((c: any, k) => (c == null ? c : c[k]), o);
}

// ---- colour maths (hex only) ----
const hex = (h: string) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(h.trim());
  const n = m ? parseInt(m[1], 16) : 0xffffff;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const toHex = (c: number[]) => '#' + c.map((x) => Math.round(Math.max(0, Math.min(255, x))).toString(16).padStart(2, '0')).join('').toUpperCase();
const mix = (a: string, b: string, t: number) => toHex(hex(a).map((x, i) => x + (hex(b)[i] - x) * t));

/**
 * Where each style keeps its accent colour. A colour the preset takes from the footage ({auto: "accent"}) is always
 * an accent; the rest is listed by hand. [path, mode]: light = a lighter tint, dark = a darker shade.
 */
const MANUAL: Record<string, [string, ('light' | 'dark')?][]> = {
  stack: [['activeColor']],
  bottom: [['activeColor']],
  impact: [['activeColor']],
  highlighter: [['activeColor']],
  editorial: [['emphasis.color']],
  cards: [['recipe.emphasis.color']],
  glass: [['emphasis.color']],
  title: [['emphasis.color']],
  whisper: [['emphasis.color']],
  ticket: [['emphasis.color']],
  quiet: [['recipe.emphasis.color']],
  punch: [['recipe.active.color'], ['recipe.emphasis.color']],
  newsroom: [['color']],
  duo: [['color']],
  terminal: [['color'], ['activeColor'], ['emphasis.fill']],
  sticker: [['container.fill'], ['emphasis.fill', 'light']],
  block: [['container.fill']],
  chip: [['emphasis.fill', 'dark']],
  serif: [['emphasis.gradTop', 'light'], ['emphasis.gradBottom']],
  singalong: [['activeColor']],
};

type AccentPath = {path: string; mode?: 'light' | 'dark'};
export function accentPaths(p: Preset): AccentPath[] {
  const out: AccentPath[] = [];
  const walk = (o: any, path: string) => {
    if (!isObj(o)) return;
    if (typeof o.auto === 'string') return void out.push({path});
    for (const [k, v] of Object.entries(o)) if (k !== 'variants') walk(v, path ? `${path}.${k}` : k);
  };
  walk(p, '');
  for (const [path, mode] of MANUAL[p.name] ?? []) if (!out.some((x) => x.path === path)) out.push({path, mode});
  return out;
}
export const hasAccent = (p: Preset) => accentPaths(p).length > 0;

/** The accent the preset shows right now (for the picker's starting value). */
export function currentAccent(p: Preset, palette?: Record<string, string>): string {
  const first = accentPaths(p).find((a) => !a.mode) ?? accentPaths(p)[0];
  if (!first) return '#FFFFFF';
  const v = getPath(p, first.path);
  if (typeof v === 'string' && v.startsWith('#')) return v.length === 7 ? v.toUpperCase() : '#FFFFFF';
  if (v && typeof v === 'object' && v.auto) return (palette?.[v.auto] ?? v.fallback ?? '#FFFFFF').toUpperCase();
  return '#FFFFFF';
}

/** The tweaks as a style-JSON override: only the keys that changed, to be deep-merged over the preset. */
export function buildOverride(p: Preset, t: Tweaks | undefined): Obj {
  const o: Obj = {};
  if (!t) return o;
  if (t.accent) for (const a of accentPaths(p)) setPath(o, a.path, a.mode === 'light' ? mix(t.accent, '#FFFFFF', 0.55) : a.mode === 'dark' ? mix(t.accent, '#000000', 0.78) : t.accent);
  if (t.size != null && t.size !== 100) o.size = Math.round(p.size * (t.size / 100) * 100) / 100;
  if (t.y != null) o.position = {y: t.y};
  if (t.case) o.case = t.case;
  if (t.shadow === false) { o.shadow = null; o.legibility = {mode: 'none'}; }
  return o;
}
export const mergedStyle = (p: Preset, over: Obj, patch?: Obj): Style => {
  const {group: _g, ...base} = p as any;
  return deepMerge(deepMerge(base, patch ?? {}), over) as Style;
};

// ---- the captions the Player draws: edits, highlight switch, vertical position ----
export type Edits = Record<number, string>;
const bare = (s: string) => s.replace(/[^\p{L}\p{N}']+/gu, '').toLowerCase();

export function applyEdit(page: CPage, text: string): CPage {
  const toks = text.trim().split(/\s+/).filter(Boolean);
  if (!toks.length) return page;
  const old = page.words, n = toks.length;
  const t0 = old[0].startMs, t1 = old[old.length - 1].endMs;
  const lineOf = (i: number) => {
    const maxLine = Math.max(...old.map((w) => w.line ?? 0));
    if (!maxLine) return 0;
    const first = old.filter((w) => (w.line ?? 0) === 0).length;
    return i < Math.max(1, Math.round((n * first) / old.length)) ? 0 : 1;
  };
  const words = toks.map((tok, i) => {
    let s: number, e: number, emphasis: boolean | undefined, reason: string | undefined;
    if (n === old.length) {
      ({startMs: s, endMs: e} = old[i]);
      emphasis = old[i].emphasis; reason = (old[i] as any).reason;
    } else {
      s = Math.round(t0 + ((t1 - t0) * i) / n);
      e = Math.round(t0 + ((t1 - t0) * (i + 1)) / n);
      const m = old.find((w) => w.emphasis && bare(w.text) === bare(tok));
      emphasis = m ? true : undefined; reason = m ? (m as any).reason : undefined;
    }
    const w: any = {text: tok, startMs: s, endMs: e, line: n === old.length ? old[i].line ?? 0 : lineOf(i)};
    if (emphasis) { w.emphasis = true; if (reason) w.reason = reason; }
    return w;
  });
  return {...page, words};
}

export function prepareCaptions(base: Omit<CaptionsFile, 'video' | 'version'>, video: CaptionsFile['video'], opts: {edits: Edits; highlights: boolean; y?: number}): CaptionsFile {
  let pages = base.pages.map((p, i) => (opts.edits[i] != null ? applyEdit(p, opts.edits[i]) : p));
  let big = base.big;
  if (Object.keys(opts.edits).length && big) {
    // a giant word follows its edited word
    big = big.map((b) => {
      for (const p of pages) for (const w of p.words) if (w.startMs === b.startMs && bare(w.text) !== bare(b.text) && w.text) return {...b, text: w.text.replace(/[^\p{L}\p{N}'$%₹.,-]+/gu, '')};
      return b;
    });
  }
  if (!opts.highlights) {
    pages = pages.map((p) => ({...p, words: p.words.map(({emphasis: _e, reason: _r, ...w}: any) => w)}));
    big = [];
  }
  if (opts.y != null) pages = pages.map((p) => ({...p, y: opts.y}));
  return {version: 1, video, ...base, pages, big} as CaptionsFile;
}

export const pageText = (p: CPage) => p.words.map((w) => w.text).join(' ');
