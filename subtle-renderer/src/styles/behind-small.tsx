import React from 'react';
import {Word, Xf, emphasisOf, f30, riseIn, styleShadow} from '../engine';
import type {PageProps} from '../engine';
import {reflow, smallIdx} from './behind-kit';

/** The small caption line of a behind style (front layer): the page's words except the giant one on screen, centred. */
export const SmallLine: React.FC<PageProps & {space?: number}> = ({st, f, page, L, ws, frame, captions, layer, space}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const idx = smallIdx(page.words, captions, st, frame, f.fps, layer);
  const boxes = reflow(L, idx, L.cx, space ?? L.fs * 0.3);
  return (
    <Xf filter={styleShadow(st, f)}>
      {boxes.map((box) => {
        const r = riseIn(f, ws[box.i].since, f30(a.smallIn ?? 5, f.fps), a.smallRise ?? 8);
        return <Word key={box.i} box={box} fill={page.words[box.i].emphasis ? e.color ?? st.color : st.color} opacity={r.opacity} dy={r.dy} />;
      })}
    </Xf>
  );
};
