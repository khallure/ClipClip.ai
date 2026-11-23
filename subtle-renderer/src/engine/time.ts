import type {CPage, Frame} from './types';
import {clamp01} from './ease';

/** Duration in frames-at-30fps -> frames at the video's fps (at least 1). */
export const f30 = (n: number, fps: number) => Math.max(1, Math.round((n * fps) / 30));
/** ms -> frame (floor, epsilon so 1000 ms at 24 fps is exactly frame 24). */
export const msToF = (ms: number, fps: number) => Math.floor((ms * fps) / 1000 + 1e-6);

export type PageTiming = {s: number; e: number; wordS: number[]; wordE: number[]};

/**
 * `onGrid`: page starts and ends were snapped to a beat grid (the captions carry `beats`). They are whole frames written as
 * truncated milliseconds, so they round to the nearest frame instead of flooring (a floor puts the cut one frame early).
 */
export function pageTimings(pages: CPage[], fps: number, minShow = 0, onGrid = false): PageTiming[] {
  const pf = onGrid ? (ms: number) => Math.round((ms * fps) / 1000) : (ms: number) => msToF(ms, fps);
  return pages.map((p, i) => {
    const s = pf(p.startMs);
    let e = Math.max(pf(p.endMs), s + 1);
    // a held page runs right up to the next one: an end within a frame of the next start is the same cut
    if (onGrid && pages[i + 1]) {
      const n = pf(pages[i + 1].startMs);
      if (n > e && n - e <= 1) e = n;
    }
    if (minShow > 0) {
      const next = pages[i + 1] ? pf(pages[i + 1].startMs) : Infinity;
      e = Math.max(e, Math.min(s + minShow, next));
    }
    const wordS = p.words.map((w) => msToF(w.startMs, fps));
    const wordE = p.words.map((w, k) => Math.max(msToF(w.endMs, fps), wordS[k] + 1));
    return {s, e, wordS, wordE};
  });
}

export type WordState = {
  i: number;
  state: 'upcoming' | 'active' | 'spoken';
  /** 0..1 while the word is being spoken (0 before, 1 after). */
  progress: number;
  /** frames since the word started (negative before it starts). */
  since: number;
  /** frames until the word ends (negative after it ended). */
  until: number;
  startF: number;
  endF: number;
};

export function wordStates(t: PageTiming, frame: number): WordState[] {
  return t.wordS.map((startF, i) => {
    const endF = t.wordE[i];
    const state = frame < startF ? 'upcoming' : frame >= endF ? 'spoken' : 'active';
    return {i, state, progress: clamp01((frame - startF) / (endF - startF)), since: frame - startF, until: endF - frame, startF, endF};
  });
}

export const frameInfo = (width: number, height: number, fps: number): Frame => ({W: width, H: height, fps, wide: width > height});
/** px at 1080p -> px at this frame height. */
export const px = (f: Frame, v: number) => (v * f.H) / 1080;
