import React from 'react';
import {Word, Xf, fadeIn, fadeOut, f30, styleShadow} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Calm, restrained line: medium weight, soft shadow, no outline, hard cut in.
// A page that carries a key word turns yellow (anim.mode "page"), or only the key word does (anim.mode "word").

const Page: React.FC<PageProps> = ({st, f, page, L, since, left}) => {
  const a = st.anim;
  const keyPage = page.words.some((w) => w.emphasis);
  const inR = fadeIn(since, f30(a.pageIn ?? 1, f.fps));
  const op = Math.min(inR.opacity, fadeOut(left, f30(a.pageOut ?? 1, f.fps)));
  return (
    <Xf opacity={op} filter={styleShadow(st, f)}>
      {L.words.map((box, i) => {
        const yellow = a.mode === 'word' ? !!page.words[i].emphasis : keyPage;
        return <Word key={i} box={box} fill={yellow ? st.activeColor! : st.color} />;
      })}
    </Xf>
  );
};

export const hype: StyleDef = {Page};
