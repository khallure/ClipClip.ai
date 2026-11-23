import React from 'react';
import {Word, activeSegment, applyCase, baseFontSize, capHeight, emphasisOf, fitRun, placeBox, segmentsOf} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// A hard-edged label tight around the text, tilted a different angle on every page, hard cuts.
// The page is split here: an emphasised word gets a big label alone, a run of other words gets a small one.
// The segment holding the word being spoken is the one on screen.

const TILT = [-3, 2.5, -1.5, 4, -2, 3, -0.5, 1.5, -2.5, 3.5];

const Page: React.FC<PageProps> = ({st, f, page, pageIndex, t, frame}) => {
  const c = st.container!;
  const e = emphasisOf(st);
  const segs = segmentsOf(page);
  const k = activeSegment(segs, t.wordS, frame);
  const seg = segs[k];
  const avail = f.W * (1 - (2 * st.position.marginX) / 100);
  const base = baseFontSize(st.size, st, f) * (seg.hot ? e.scale ?? 1.7 : 1);
  const texts = seg.idx.map((i) => applyCase(page.words[i].text, st.case));
  const run = fitRun(texts, st.font, base, {ls: st.letterSpacing, maxW: avail - 2 * c.padX * base - 0.04 * f.W});
  const fs = run.fs;
  const cap = capHeight(st.font) * fs;
  const lw = run.w + 2 * c.padX * fs;
  const lh = cap + 2 * c.padY * fs;
  const cx = (f.W * (st.position.x ?? 50)) / 100;
  const cy = (f.H * st.position.y) / 100;
  const ang = TILT[(pageIndex * 3 + k) % TILT.length];
  return (
    <g transform={`translate(${cx} ${cy}) rotate(${ang})`}>
      <rect x={-lw / 2} y={-lh / 2} width={lw} height={lh} fill={c.fill} />
      {run.boxes.map((b, j) => (
        <Word key={j} box={placeBox(b, -run.w / 2, cap / 2)} fill={seg.hot ? e.color ?? st.color : st.color} />
      ))}
    </g>
  );
};

export const ticket: StyleDef = {Page};
