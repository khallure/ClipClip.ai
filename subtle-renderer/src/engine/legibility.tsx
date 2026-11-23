import React, {createContext, useContext} from 'react';
import type {CaptionsFile, Frame, Style} from './types';
import type {Layout} from './layout';
import {px} from './time';

// Automatic legibility. scripts/legibility.py measures, for every page, how hard the footage behind the caption makes the
// ink to read and writes `boost` (0..1) into captions.json; this file turns that number into help drawn in the style's own
// taste. At boost 0 (a calm, dark background) nothing is drawn and the render is exactly what it was.
//
// A preset may declare   "legibility": {"mode": "halo" | "scrim" | "stroke" | "plate" | "none", "max": 1, "strength": 1}
//   halo    layered soft shadow hugging the glyphs (default for unboxed text)
//   scrim   a feathered dark band behind the caption area, never a hard box
//   stroke  a thin outline round the glyphs
//   plate   darken the style's own container (the style reads it through `useLegibility`, see `plateFill`)
//   none    boxed or full-frame styles that carry their own background (default when the preset has a `container`)
// `max` caps the boost for this style (a delicate style asks for less); `strength` scales the effect.
//
// Palette: any colour in a preset may be {"auto": "accent" | "ink" | "shade", "fallback": "#rrggbb"}. captions.json's `palette`
// (picked from the footage, see scripts/legibility.py) fills it; without a palette the fallback is used.

export type LgMode = 'halo' | 'scrim' | 'stroke' | 'plate' | 'none';
export type LgPage = {page: number; contrast: number; busy: number; bg: string; boost: number};
export type Palette = {accent?: string; ink?: string; shade?: string; bg?: string; contrast?: number};
export type LgState = {mode: LgMode; /** 0..1 after `max` and `strength`; 0 means draw nothing */ k: number; /** dark colour for shadows (the scene's own shade) */ shade: string};

type Cfg = {mode?: LgMode; max?: number; strength?: number};
const cfgOf = (st: Style): Cfg => ((st as any).legibility ?? {}) as Cfg;
const MODES: LgMode[] = ['halo', 'scrim', 'stroke', 'plate', 'none'];

export const lgModeOf = (st: Style): LgMode => {
  const m = cfgOf(st).mode;
  if (m && MODES.includes(m)) return m;
  return st.container ? 'none' : 'halo';
};

/** The help page `pageIndex` gets. `k` is 0 when the file carries no measurement or the style opts out. */
export function legibilityFor(captions: CaptionsFile, st: Style, pageIndex: number): LgState {
  const mode = lgModeOf(st);
  const rows: LgPage[] | undefined = (captions as any).legibility;
  const pal: Palette | undefined = (captions as any).palette;
  const c = cfgOf(st);
  const row = rows?.find((r) => r.page === pageIndex) ?? rows?.[pageIndex];
  const raw = mode === 'none' || !row ? 0 : Math.min(row.boost, c.max ?? 1) * (c.strength ?? 1);
  return {mode, k: raw < 0.04 ? 0 : Math.min(1, raw), shade: pal?.shade ?? '#000000'};
}

/** Replace every {"auto": "accent", "fallback": "#…"} in a preset with the footage palette (or the fallback). */
export function resolvePalette<T>(obj: T, palette?: Palette): T {
  if (Array.isArray(obj)) return obj.map((v) => resolvePalette(v, palette)) as any;
  if (obj && typeof obj === 'object') {
    const o = obj as any;
    if (typeof o.auto === 'string') return ((palette as any)?.[o.auto] ?? o.fallback) as any;
    const out: any = {};
    for (const k of Object.keys(o)) out[k] = resolvePalette(o[k], palette);
    return out;
  }
  return obj;
}

const LgContext = createContext<LgState>({mode: 'none', k: 0, shade: '#000000'});
export const LegibilityProvider = LgContext.Provider;
/** For a style that handles the help itself (plate mode): the state of the page being drawn. */
export const useLegibility = () => useContext(LgContext);

/** '#rrggbb' + alpha -> 'rgba(r,g,b,a)'. */
export const withAlpha = (hex: string, a: number) => rgba(hex, a);
const rgba = (hex: string, a: number) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  const v = m ? parseInt(m[1], 16) : 0;
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a.toFixed(3)})`;
};

/**
 * Darken a translucent plate (a glass pill, a chip). `fill` is "rgba(r,g,b,a)"; at k = 1 its alpha rises by `lift` and
 * its colour moves `lean` of the way to the shade, so the type on it keeps its contrast on a bright background.
 */
export function plateFill(fill: string, lg: LgState, lift = 0.32, lean = 0.55): string {
  const m = /rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*\)/.exec(fill);
  if (!m || lg.k <= 0) return fill;
  const s = /^#?([0-9a-f]{6})$/i.exec(lg.shade);
  const sv = s ? parseInt(s[1], 16) : 0;
  const sh = [(sv >> 16) & 255, (sv >> 8) & 255, sv & 255];
  const c = [1, 2, 3].map((i, j) => Math.round(+m[i] + (sh[j] - +m[i]) * lean * lg.k));
  const a = Math.min(0.92, (m[4] != null ? +m[4] : 1) + lift * lg.k);
  return `rgba(${c[0]},${c[1]},${c[2]},${a.toFixed(3)})`;
}

/** CSS filter for the halo: soft dark shadows stacked so they build up close to the glyphs and thin out away from them. */
function haloFilter(f: Frame, k: number, shade: string): string {
  const s = (v: number) => px(f, v).toFixed(2);
  const L = (blur: number, a: number, dy = 0) => `drop-shadow(0px ${s(dy)}px ${s(blur)}px ${rgba(shade, Math.min(1, a * k))})`;
  return [L(1.4, 0.95), L(3.5, 0.9, 0.5), L(8, 0.85, 1.5), L(18, 0.7, 3)].join(' ');
}

/** A feathered band behind the caption block: full width, soft top and bottom edge, darkest in the middle. */
const Scrim: React.FC<{f: Frame; L: Layout; k: number; shade: string; fade: number; id: string; max: number}> = ({f, L, k, shade, fade, id, max}) => {
  const pad = L.lineH * 1.6;
  const y0 = L.top - pad;
  const h = L.blockH + 2 * pad;
  const a = max * k * fade;
  return (
    <>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          {[0, 0.12, 0.3, 0.5, 0.7, 0.88, 1].map((o, i) => (
            <stop key={i} offset={o} stopColor={shade} stopOpacity={[0, 0.2, 0.75, 1, 0.75, 0.2, 0][i]} />
          ))}
        </linearGradient>
      </defs>
      <rect x={0} y={y0} width={f.W} height={h} fill={`url(#${id})`} opacity={a} />
    </>
  );
};

/**
 * Wrap a page: draws the scrim (if the style asks for one) before it and the halo or stroke around it. Does nothing at k = 0.
 * `fade` frames in and out so a scrim never pops: pass the page's `since` and `left` (frames).
 */
export const Legible: React.FC<{lg: LgState; f: Frame; L: Layout; since: number; left: number; pageIndex: number; children: React.ReactNode}> = ({lg, f, L, since, left, pageIndex, children}) => {
  if (lg.k <= 0 || lg.mode === 'none' || lg.mode === 'plate') return <LgContext.Provider value={lg}>{children}</LgContext.Provider>;
  const fade = Math.max(0, Math.min(1, (since + 1) / 4, left / 4));
  if (lg.mode === 'scrim') {
    return (
      <>
        <Scrim f={f} L={L} k={lg.k} shade={lg.shade} fade={fade} id={`lg-scrim-${pageIndex}`} max={0.55} />
        {children}
      </>
    );
  }
  if (lg.mode === 'stroke') {
    const r = px(f, 0.7 + 1.5 * lg.k);
    const id = `lg-stroke-${pageIndex}`;
    return (
      <>
        <defs>
          <filter id={id} filterUnits="userSpaceOnUse" x={0} y={0} width={f.W} height={f.H} colorInterpolationFilters="sRGB">
            <feMorphology in="SourceAlpha" operator="dilate" radius={r} result="fat" />
            <feGaussianBlur in="fat" stdDeviation={r * 0.35} result="soft" />
            <feFlood floodColor={lg.shade} floodOpacity={Math.min(1, 0.9 * lg.k + 0.1)} result="ink" />
            <feComposite in="ink" in2="soft" operator="in" result="edge" />
            <feMerge>
              <feMergeNode in="edge" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <g filter={`url(#${id})`}>{children}</g>
      </>
    );
  }
  // halo
  return (
    <g style={{filter: haloFilter(f, lg.k, lg.shade)}}>
      {children}
    </g>
  );
};
