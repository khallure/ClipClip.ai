import type {Box, CPage, FontSpec, Frame, Style} from './types';
import {fontWithGlyphs} from './fonts';

export const applyCase = (text: string, c: Style['case']) =>
  c === 'upper' ? text.toUpperCase() : c === 'lower' ? text.toLowerCase() : text;

let measureCtx: CanvasRenderingContext2D | null = null;
function getMeasureCtx(): CanvasRenderingContext2D | null {
  if (typeof document === 'undefined') return null;
  if (!measureCtx) {
    measureCtx = document.createElement('canvas').getContext('2d');
  }
  return measureCtx;
}

const capCache = new Map<string, number>();
/** Cap height as a fraction of the font size, measured from the real glyph "H". */
export function capHeight(font: FontSpec) {
  const key = `${font.family}|${font.weight}`;
  let v = capCache.get(key);
  if (v == null) {
    const ctx = getMeasureCtx();
    if (!ctx) return 0.7;
    if ('letterSpacing' in ctx) (ctx as any).letterSpacing = '0px';
    ctx.font = `${font.weight} 200px "${font.family}"`;
    v = ctx.measureText('H').actualBoundingBoxAscent / 200;
    if (!(v > 0.3)) v = 0.7;
    capCache.set(key, v);
  }
  return v;
}

export function textWidth(text: string, font: FontSpec, fs: number, ls = 0) {
  const ctx = getMeasureCtx();
  if (!ctx) return Math.max(0, text.length * fs * 0.56);
  const c = ctx as CanvasRenderingContext2D & {letterSpacing?: string};
  c.font = `${font.weight} ${fs}px "${font.family}"`;
  if (typeof c.letterSpacing === 'string') {
    c.letterSpacing = `${(ls * fs).toFixed(3)}px`;
    return c.measureText(text).width;
  }
  const extra = text.length > 1 ? (text.length - 1) * (ls * fs) : 0;
  return Math.max(0, ctx.measureText(text).width + extra);
}

export function spaceWidth(font: FontSpec, fs: number, ls = 0) {
  const ctx = getMeasureCtx();
  if (!ctx) return fs * 0.28 + ls * fs;
  if ('letterSpacing' in ctx) (ctx as any).letterSpacing = '0px';
  ctx.font = `${font.weight} 200px "${font.family}"`;
  return ((ctx.measureText('n n').width - ctx.measureText('nn').width) / 200) * fs + ls * fs;
}

/** Advance of one character in a monospace font. */
export const monoAdvance = (font: FontSpec, fs: number) => textWidth('MMMMMMMMMM', font, fs) / 10;

export const baseFontSize = (sizePct: number, st: Style, f: Frame) =>
  (sizePct / 100) * f.H * (f.wide ? st.wideScale ?? 1.35 : 1);

export type WordSpec = {text?: string; font?: FontSpec; size?: number; ls?: number; dy?: number; pad?: number};
export type WordBox = {
  i: number;
  text: string;
  font: FontSpec;
  fs: number;
  ls: number;
  left: number;
  w: number;
  cx: number;
  line: number;
  baseline: number;
};
export type LineBox = {y: number; baseline: number; left: number; w: number; idx: number[]};
export type Layout = {
  fs: number;
  words: WordBox[];
  lines: LineBox[];
  cx: number;
  cy: number;
  left: number;
  top: number;
  blockW: number;
  blockH: number;
  lineH: number;
  capH: number;
};
export type LayoutOpts = {
  specs?: WordSpec[];
  sizePct?: number;
  x?: number;
  y?: number;
  padX?: number;
  align?: 'center' | 'left';
  gap?: number;
  maxLines?: number;
};

function breakLines(widths: number[], space: number): number[] {
  if (widths.length < 2) return widths.map(() => 0);
  let best = 1;
  let bestMax = Infinity;
  for (let k = 1; k < widths.length; k++) {
    const a = widths.slice(0, k).reduce((x, y) => x + y, 0) + space * (k - 1);
    const b = widths.slice(k).reduce((x, y) => x + y, 0) + space * (widths.length - k - 1);
    if (Math.max(a, b) < bestMax) {
      bestMax = Math.max(a, b);
      best = k;
    }
  }
  return widths.map((_, i) => (i < best ? 0 : 1));
}

/** Measure a page, wrap it into lines, shrink to fit the side margins, return one box per word. */
export function layoutPage(page: CPage, st: Style, f: Frame, o: LayoutOpts = {}): Layout {
  const ls0 = st.letterSpacing ?? 0;
  const specs = page.words.map((_, i) => o.specs?.[i] ?? {});
  const texts = page.words.map((w, i) => specs[i].text ?? applyCase(w.text, st.case));
  const fonts = specs.map((s, i) => fontWithGlyphs(s.font ?? st.font, texts[i]));
  const muls = specs.map((s) => s.size ?? 1);
  const lss = specs.map((s) => s.ls ?? ls0);
  const avail = f.W * (1 - (2 * st.position.marginX) / 100) - 2 * (o.padX ?? 0);
  const maxLines = o.maxLines ?? Math.max(2, st.page?.maxLines ?? 2);
  let fs = baseFontSize(o.sizePct ?? st.size, st, f);
  let wordLine = page.words.map((w) => Math.min(w.line ?? 0, 3));

  const measure = (size: number) => {
    const widths = texts.map((t, i) => textWidth(t, fonts[i], size * muls[i], lss[i]) + (specs[i].pad ?? 0) * size);
    const space = spaceWidth(st.font, size, ls0) * (o.gap ?? 1);
    return {widths, space};
  };
  const widest = (wl: number[], m: {widths: number[]; space: number}) => {
    const sums = new Map<number, number>();
    wl.forEach((l, i) => sums.set(l, (sums.get(l) ?? -m.space) + m.widths[i] + m.space));
    return Math.max(...sums.values());
  };
  let m = measure(fs);
  let w = widest(wordLine, m);
  if (w > avail) {
    let scale = avail / w;
    if (scale < 0.8 && new Set(wordLine).size < 2 && texts.length > 1 && maxLines > 1) {
      wordLine = breakLines(m.widths, m.space);
      w = widest(wordLine, m);
      scale = Math.min(1, avail / w);
    }
    fs *= scale;
    m = measure(fs);
    w = widest(wordLine, m);
    if (w > avail) {
      fs *= (avail / w) * 0.995;
      m = measure(fs);
    }
  }

  const lineIds = [...new Set(wordLine)].sort((a, b) => a - b);
  const lh = fs * (st.lineHeight ?? 1.15);
  const capH = capHeight(st.font) * fs;
  const cx = (f.W * (o.x ?? st.position.x ?? 50)) / 100;
  const cy = (f.H * (o.y ?? page.y ?? st.position.y)) / 100;
  const blockH = lh * lineIds.length;
  const blockW = widest(wordLine, m);
  const words: WordBox[] = [];
  const lines: LineBox[] = lineIds.map((id, li) => {
    const idx = texts.map((_, i) => i).filter((i) => wordLine[i] === id);
    const lw = idx.reduce((s, i) => s + m.widths[i], 0) + m.space * (idx.length - 1);
    const y = cy - blockH / 2 + lh * (li + 0.5);
    const baseline = y + capH / 2;
    let x = o.align === 'left' ? cx - blockW / 2 : cx - lw / 2;
    for (const i of idx) {
      words[i] = {
        i,
        text: texts[i],
        font: fonts[i],
        fs: fs * muls[i],
        ls: lss[i],
        left: x,
        w: m.widths[i] - (specs[i].pad ?? 0) * fs,
        cx: x + (m.widths[i] - (specs[i].pad ?? 0) * fs) / 2,
        line: li,
        baseline: baseline + (specs[i].dy ?? 0) * fs,
      };
      x += m.widths[i] + m.space;
    }
    return {y, baseline, left: o.align === 'left' ? cx - blockW / 2 : cx - lw / 2, w: lw, idx};
  });
  return {fs, words, lines, cx, cy, left: cx - blockW / 2, top: cy - blockH / 2, blockW, blockH, lineH: lh, capH};
}

/** The block's bounding box grown by a padding (for containers). */
export const blockBox = (L: Layout, padX: number, padY: number): Box => ({
  x: L.left - padX,
  y: L.top - padY,
  w: L.blockW + 2 * padX,
  h: L.blockH + 2 * padY,
});

/** Size one word so it spans widthPx, but never above maxFs. */
export function fitWord(text: string, font: FontSpec, widthPx: number, maxFs: number, ls = 0) {
  const w100 = textWidth(text, font, 100, ls);
  const fs = Math.min(maxFs, (100 * widthPx) / w100);
  return {fs, w: textWidth(text, font, fs, ls)};
}

/** The style as it applies to one page: `position.y` replaced by the page's own y when placement set one. */
export const pageStyle = (st: Style, page: CPage): Style =>
  page.y == null ? st : {...st, position: {...st.position, y: page.y}};
