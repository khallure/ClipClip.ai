import type {CaptionsFile} from './types';
import {msToF} from './time';

/** Beat and bar starts of a music file, as frames of the video. */
export type BeatGrid = {beats: number[]; bars: number[]; bpm: number; beatFrames: number};

const cache = new WeakMap<CaptionsFile, Map<number, BeatGrid | null>>();

/** The captions' beats converted to frames at `fps`, or null when the file has none (every music style must cope with null). */
export function beatGrid(c: CaptionsFile, fps: number): BeatGrid | null {
  let m = cache.get(c);
  if (!m) cache.set(c, (m = new Map()));
  if (m.has(fps)) return m.get(fps)!;
  const b = c.beats;
  let g: BeatGrid | null = null;
  if (b && b.times && b.times.length >= 2) {
    const beats = b.times.map((t) => msToF(t, fps));
    const bars = (b.downbeats ?? []).map((t) => msToF(t, fps));
    g = {beats, bars, bpm: b.bpm, beatFrames: ((b.times[b.times.length - 1] - b.times[0]) / (b.times.length - 1)) * (fps / 1000)};
  }
  m.set(fps, g);
  return g;
}

/** Index of the last entry of a sorted array that is <= v (-1 when v is before the first). */
const lastAtOrBefore = (a: number[], v: number) => {
  let lo = 0;
  let hi = a.length - 1;
  let r = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] <= v) {
      r = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return r;
};

/** Index of the beat that is on screen at `frame` (-1 before the first beat; -1 without a grid). */
export const beatIndex = (g: BeatGrid | null, frame: number) => (g ? lastAtOrBefore(g.beats, frame) : -1);

/** Frames since the last beat. Without a grid: frames since `fallbackStart` (the page's own start). */
export function sinceBeat(g: BeatGrid | null, frame: number, fallbackStart = 0) {
  if (!g) return frame - fallbackStart;
  const i = lastAtOrBefore(g.beats, frame);
  return i < 0 ? frame - fallbackStart : frame - g.beats[i];
}

/** Bar number (0-based) at `frame`. Without a grid: `fallback` (a page counter divided by the caller). */
export function barIndex(g: BeatGrid | null, frame: number, fallback = 0) {
  if (!g || !g.bars.length) return fallback;
  return Math.max(0, lastAtOrBefore(g.bars, frame));
}

/** Beat number at `frame` counted from the first beat (0 without a grid). */
export const beatNumber = (g: BeatGrid | null, frame: number) => Math.max(0, beatIndex(g, frame));

/** The frame of the beat at or just before `frame + tol` (a singer may push a little). Without a grid: `frame`. */
export function snapToBeat(g: BeatGrid | null, frame: number, tol = 0) {
  if (!g) return frame;
  const i = lastAtOrBefore(g.beats, frame + tol);
  return i < 0 ? frame : g.beats[i];
}
