import React from 'react';
import {Word, emphasisOf, Xf, blurIn, capHeight, f30, fadeOut, dropShadow, joinFilters, blurCss} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Display serif italic filled with a top-lit silver gradient (white at the top of the letters, grey at the baseline side).
// Quiet: each word fades up out of a soft blur, a faint shadow keeps it readable on video.

const Page: React.FC<PageProps> = ({st, f, page, pageIndex, L, ws, left}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const out = fadeOut(left, f30(a.pageOut, f.fps));
  return (
    <Xf opacity={out} filter={joinFilters(dropShadow(f, 0, 2, a.shadowBlur, a.shadowColor), dropShadow(f, 0, 1, a.shadowBlur * 0.3, a.shadowColor))}>
      {L.words.map((box, i) => {
        const id = `sg-${pageIndex}-${i}`;
        const em = !!page.words[i].emphasis;
        const cap = capHeight(box.font) * box.fs;
        const r = blurIn(f, ws[i].since, f30(a.wordIn, f.fps), a.wordBlur);
        return (
          <React.Fragment key={i}>
            <defs>
              <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="0" x2="0" y1={box.baseline - cap * 1.1} y2={box.baseline + box.fs * 0.24}>
                <stop offset="0" stopColor={em ? e.gradTop ?? a.gradTop : a.gradTop} />
                <stop offset="1" stopColor={em ? e.gradBottom ?? a.gradBottom : a.gradBottom} />
              </linearGradient>
            </defs>
            <Word box={box} fill={`url(#${id})`} opacity={r.opacity} filter={joinFilters(blurCss(f, r.blur))} dy={(1 - r.opacity) * f.H * 0.004} />
          </React.Fragment>
        );
      })}
    </Xf>
  );
};

export const serif: StyleDef = {Page};
