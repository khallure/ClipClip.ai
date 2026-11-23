import type {BigProps, CBig, CaptionsFile, Layer, Style, WordBox} from '../engine';
import {capHeight, textWidth, spaceWidth} from '../engine';
import type {Layout} from '../engine';

// Shared by the styles that put giant words behind the speaker (cover, outline, poster, numeral, rise, keyword...).

export const bare = (s: string) => s.replace(/[^\p{L}\p{N}']+/gu, '').toLowerCase();
export const toF = (ms: number, fps: number) => Math.floor((ms * fps) / 1000 + 1e-6);

/** Index of the giant word on screen at `frame`, or -1. */
export const activeBig = (big: CBig[], frame: number, fps: number) =>
  big.findIndex((b) => {
    const s = toF(b.startMs, fps);
    return frame >= s && frame < Math.max(toF(b.endMs, fps), s + 1);
  });

/** The word is drawn in front of the speaker (a close-up, a number, or no cut-out in this browser). */
export const inFront = (b: CBig, layer?: Layer) => !!b.front || layer === 'all';

/** Whether a giant word is drawn at all. A style with `behind.skipFront` drops it in front, and the word stays in the small line. */
export const bigDrawn = (b: CBig, st: Style, layer?: Layer) => !(inFront(b, layer) && (st.behind as any)?.skipFront);

/** Indexes of the page's words that are not the giant word on screen (those stay in the small line). */
export function smallIdx(words: {text: string; startMs: number}[], captions: CaptionsFile, st: Style, frame: number, fps: number, layer?: Layer): number[] {
  const big = captions.big ?? [];
  const out: number[] = [];
  words.forEach((w, i) => {
    const hit = big.some((b) => w.startMs >= b.startMs - 20 && w.startMs < b.endMs && bigDrawn(b, st, layer) && bare(b.text).split(/\s+/).some((t) => t && bare(w.text).includes(t.replace(/-/g, ''))));
    if (!hit) out.push(i);
  });
  return out;
}

/** Re-flow the chosen words of a laid-out page: each line centred on `cx`, lines keep their baselines. */
export function reflow(L: Layout, idx: number[], cx: number, space?: number): WordBox[] {
  const byLine = new Map<number, number[]>();
  idx.forEach((i) => byLine.set(L.words[i].line, [...(byLine.get(L.words[i].line) ?? []), i]));
  const sp = space ?? L.fs * 0.28;
  const out: WordBox[] = [];
  for (const [, ids] of [...byLine.entries()].sort((a, b) => a[0] - b[0])) {
    const w = ids.reduce((s, i) => s + L.words[i].w, 0) + sp * (ids.length - 1);
    let x = cx - w / 2;
    for (const i of ids) {
      out.push({...L.words[i], left: x, cx: x + L.words[i].w / 2});
      x += L.words[i].w + sp;
    }
  }
  return out;
}

/** Hard-fit a string: font size so it spans `widthPx`, capped at `maxFs`; returns size, width and cap height. */
export function fitTo(text: string, font: any, widthPx: number, maxFs: number, ls = 0) {
  const w100 = textWidth(text, font, 100, ls);
  const fs = Math.min(maxFs, (100 * widthPx) / w100);
  return {fs, w: textWidth(text, font, fs, ls), cap: capHeight(font) * fs};
}
export {spaceWidth};

/**
 * Left edge (px) for a giant word of width `w`, so the speaker's head covers about `overlap` of it and both ends stay clear.
 * A word nearly as wide as the frame stays centred. Uses the shot's face box; without one the word is centred.
 */
export function placeX(shot: {face?: number[]} | undefined, W: number, w: number, overlap = 0.28, margin = 0.04): number {
  const centred = (W - w) / 2;
  const face = shot?.face;
  if (!face || w > W * 0.9) return centred;
  const fx0 = (face[0] - face[2] * 0.15) * W, fx1 = (face[0] + face[2] * 1.15) * W; // hair and ears
  const lo = W * margin, hi = W - W * margin - w;
  // candidate lefts: the head's right edge covers `overlap` of the word's left end, or its left edge covers the right end
  const fromRight = fx1 - overlap * w;
  const fromLeft = fx0 - (1 - overlap) * w;
  const ok = (l: number) => l >= lo - 1 && l <= hi + 1;
  const cands = [fromRight, fromLeft].map((l) => Math.min(hi, Math.max(lo, l)));
  void ok;
  if (!cands.length) return centred;
  // prefer the side with more room (the word stays closest to the middle of the frame)
  return cands.sort((a, b) => Math.abs(a + w / 2 - W / 2) - Math.abs(b + w / 2 - W / 2))[0];
}
