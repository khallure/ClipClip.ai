import React from 'react';
import type {CPage, FontSpec} from './types';
import {Xf} from './draw';
import type {WordBox} from './layout';
import {spaceWidth, textWidth} from './layout';
import {fontWithGlyphs} from './fonts';

/** A page split at render time: each emphasised word is its own segment, each run of other words is one segment. */
export type Segment = {idx: number[]; hot: boolean};
export function segmentsOf(page: CPage): Segment[] {
  const out: Segment[] = [];
  page.words.forEach((w, i) => {
    const hot = !!w.emphasis;
    const last = out[out.length - 1];
    if (!hot && last && !last.hot) last.idx.push(i);
    else out.push({idx: [i], hot});
  });
  return out;
}

/** Index of the segment holding the word being spoken (the last segment whose first word has started; the first one before that). */
export function activeSegment(segs: Segment[], wordStartF: number[], frame: number): number {
  let k = 0;
  segs.forEach((s, j) => {
    if (frame >= wordStartF[s.idx[0]]) k = j;
  });
  return k;
}

export type Run = {boxes: WordBox[]; w: number; fs: number};
/**
 * Lay words out on one line, left edge at 0 and baseline 0, shrunk so the run is never wider than `maxW`.
 * A word with a character its font lacks is measured and drawn in the fallback font.
 */
export function fitRun(texts: string[], font: FontSpec, fs: number, o: {ls?: number; gap?: number; maxW: number}): Run {
  const ls = o.ls ?? 0;
  const gap = o.gap ?? 1;
  const fonts = texts.map((t) => fontWithGlyphs(font, t));
  const measure = (s: number) => {
    const ws = texts.map((t, i) => textWidth(t, fonts[i], s, ls));
    const sp = spaceWidth(font, s, ls) * gap;
    return {ws, sp, tot: ws.reduce((a, b) => a + b, 0) + sp * (texts.length - 1)};
  };
  let m = measure(fs);
  if (m.tot > o.maxW) {
    fs *= (o.maxW / m.tot) * 0.995;
    m = measure(fs);
  }
  let x = 0;
  const boxes = texts.map((t, i) => {
    const b: WordBox = {i, text: t, font: fonts[i], fs, ls, left: x, w: m.ws[i], cx: x + m.ws[i] / 2, line: 0, baseline: 0};
    x += m.ws[i] + m.sp;
    return b;
  });
  return {boxes, w: m.tot, fs};
}

/** Move a box by (dx, baseline). */
export const placeBox = (b: WordBox, dx: number, baseline: number): WordBox => ({...b, left: b.left + dx, cx: b.cx + dx, baseline});

/** "bakery" -> "Bakery"; numbers and signs are left alone. */
export const titleCase = (t: string) => t.replace(/^([^\p{L}]*)(\p{L})/u, (_, a, b) => a + b.toUpperCase());

/** One word like `Word`, but with lining figures (some serifs default to old-style figures, which read badly in numbers). */
export const LiningWord: React.FC<{box: WordBox; fill: string; scale?: number; opacity?: number; filter?: string}> = ({box, fill, scale = 1, opacity = 1, filter}) => (
  <Xf ox={box.left} oy={box.baseline} scale={scale} opacity={opacity} filter={filter}>
    <text
      x={box.left}
      y={box.baseline}
      fontFamily={`"${box.font.family}"`}
      fontWeight={box.font.weight}
      fontSize={box.fs}
      letterSpacing={box.ls * box.fs}
      fill={fill}
      style={{fontSynthesis: 'none', textRendering: 'geometricPrecision', whiteSpace: 'pre', fontFeatureSettings: '"lnum" 1', fontVariantNumeric: 'lining-nums'}}
    >
      {box.text}
    </text>
  </Xf>
);
