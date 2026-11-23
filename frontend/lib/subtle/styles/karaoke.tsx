import React from 'react';
import {Word, Xf, colorMix, easeOut, emphasisOf, fadeIn, fadeOut, f30, mix, styleShadow} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// The whole line is visible at once in a dim value; each word turns solid as it is spoken.
// One weight, one colour, no motion: only the value changes.
// An emphasised word turns a brighter accent colour and grows a few percent as it is spoken. Its room is reserved
// in the layout (see `spec`), so the line never jumps.

const Page: React.FC<PageProps> = ({st, f, page, L, ws, since, left}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const op = Math.min(fadeIn(since, f30(a.pageIn, f.fps)).opacity, fadeOut(left, f30(a.pageOut, f.fps)));
  return (
    <Xf opacity={op} filter={styleShadow(st, f)}>
      {L.words.map((box, i) => {
        const k = ws[i].since < 0 ? 0 : easeOut((ws[i].since + 1) / (f30(a.wordIn, f.fps) + 1));
        const em = !!page.words[i].emphasis;
        const sc = em ? mix(1 / (e.scale ?? 1.07), 1, k) : 1; // the laid-out box is the grown size
        return <Word key={i} box={box} fill={em ? colorMix(k, st.color, e.color ?? st.color) : st.color} opacity={mix(a.dim, 1, k)} scale={sc} />;
      })}
    </Xf>
  );
};

export const karaoke: StyleDef = {
  spec: (page, st) => page.words.map((w) => (w.emphasis ? {size: emphasisOf(st).scale ?? 1.07} : {})),
  layout: () => ({align: 'left'}),
  Page,
};
