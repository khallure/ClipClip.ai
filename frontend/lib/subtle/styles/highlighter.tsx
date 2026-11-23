import React from 'react';
import {Word, activeSegment, applyCase, baseFontSize, capHeight, clamp01, emphasisOf, f30, fitRun, placeBox, segmentsOf, styleShadow} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Bold caps. One flat box sits behind the word being spoken and hops to the next word with a short hand-off
// (the old box fades out while the new one fades in). A finished word turns lime.
// An emphasised word is its own segment: shown alone, large, in the finished colour, with the box behind it.

const Page: React.FC<PageProps> = ({st, f, page, t, frame, ws}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const segs = segmentsOf(page);
  const k = activeSegment(segs, t.wordS, frame);
  const seg = segs[k];
  const avail = f.W * (1 - (2 * st.position.marginX) / 100);
  const base = baseFontSize(st.size, st, f) * (seg.hot ? e.scale ?? 1.9 : 1);
  const padX = a.padX * base;
  const texts = seg.idx.map((i) => applyCase(page.words[i].text, st.case));
  const run = fitRun(texts, st.font, base, {ls: st.letterSpacing, gap: a.gap, maxW: avail - 2 * padX});
  const fs = run.fs;
  const cap = capHeight(st.font) * fs;
  const bh = cap + 2 * a.padY * fs;
  const cx = (f.W * (st.position.x ?? 50)) / 100;
  const cy = (f.H * st.position.y) / 100;
  const hand = f30(a.handoff, f.fps);
  const boxFor = (j: number) => {
    const s = ws[seg.idx[j]];
    const inK = s.since < 0 ? 0 : clamp01((s.since + 1) / (hand + 1));
    const outK = s.until > 0 ? 1 : clamp01(1 - (-s.until + 1) / (hand + 1));
    return Math.min(inK, outK);
  };
  return (
    <g>
      {run.boxes.map((b, j) => {
        const o = boxFor(j);
        return o > 0.001 ? <rect key={j} x={cx - run.w / 2 + b.left - padX} y={cy - bh / 2} width={b.w + 2 * padX} height={bh} fill={st.activeColor} opacity={o} /> : null;
      })}
      <g filter={styleShadow(st, f)}>
      {run.boxes.map((b, j) => {
        const done = ws[seg.idx[j]].state === 'spoken';
        const lime = seg.hot || done;
        return <Word key={j} box={placeBox(b, cx - run.w / 2, cy + cap / 2)} fill={lime ? e.color ?? st.color : st.color} />;
      })}
      </g>
    </g>
  );
};

export const highlighter: StyleDef = {Page};
