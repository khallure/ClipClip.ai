import React from 'react';
import {Word, Xf, styleShadow} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Big bold caps low in the frame, built word by word: a word is on screen from the frame it is spoken,
// no fade, no scale. The emphasis word is yellow. The line hangs from the left margin so it grows to the right.

const Page: React.FC<PageProps> = ({st, f, page, L, ws}) => {
  const dx = (f.W * st.position.marginX) / 100 - L.left;
  return (
    <Xf dx={dx} filter={styleShadow(st, f)}>
      {L.words.map((box, i) =>
        ws[i].since < 0 ? null : <Word key={i} box={box} fill={page.words[i].emphasis ? st.activeColor! : st.color} />,
      )}
    </Xf>
  );
};

export const bottom: StyleDef = {layout: () => ({align: 'left'}), Page};
