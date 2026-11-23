import React from 'react';
import {LiningWord, applyCase, baseFontSize, capHeight, emphasisOf, fitRun, placeBox, styleShadow, titleCase} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Exactly one small condensed serif word at a time, hard cuts, no box. An emphasised word is set in the italic and a lighter yellow.
// A page with more than one word shows the word being spoken.

const Page: React.FC<PageProps> = ({st, f, page, t, frame}) => {
  const e = emphasisOf(st);
  let i = 0;
  t.wordS.forEach((s, j) => {
    if (frame >= s) i = j;
  });
  const hot = !!page.words[i].emphasis;
  const font = hot ? e.font ?? st.font : st.font;
  const base = baseFontSize(st.size, st, f) * (hot ? e.scale ?? 1 : 1);
  const avail = f.W * (1 - (2 * st.position.marginX) / 100);
  const run = fitRun([titleCase(applyCase(page.words[i].text, st.case))], font, base, {ls: st.letterSpacing, maxW: avail});
  const cx = (f.W * (st.position.x ?? 50)) / 100;
  const cy = (f.H * st.position.y) / 100;
  const cap = capHeight(font) * run.fs;
  return (
    <g filter={styleShadow(st, f)} stroke="#1B1905" strokeWidth={run.fs * (st.anim.stroke ?? 0.045)} paintOrder="stroke fill">
      <LiningWord box={placeBox(run.boxes[0], cx - run.w / 2, cy + cap / 2)} fill={hot ? e.color ?? st.color : st.color} />
      {/* the serif is hairline thin: the same word again with a thin stroke in its own colour thickens it */}
      {st.anim.bold ? (
        <g stroke={hot ? e.color ?? st.color : st.color} strokeWidth={run.fs * st.anim.bold} paintOrder="normal">
          <LiningWord box={placeBox(run.boxes[0], cx - run.w / 2, cy + cap / 2)} fill={hot ? e.color ?? st.color : st.color} />
        </g>
      ) : null}
    </g>
  );
};

export const haiku: StyleDef = {Page};
