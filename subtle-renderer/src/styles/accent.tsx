import React from 'react';
import {Word, Xf, fadeOut, f30, riseIn, styleShadow} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Very short pages in a light sans; the key word swaps to a light serif italic. Soft fade with a few px of drift.

const italicIdx = (page: PageProps['page']) => {
  const s = new Set<number>();
  page.words.forEach((w, i) => w.emphasis && s.add(i));
  if (!s.size) {
    let best = 0;
    page.words.forEach((w, i) => w.text.replace(/\W/g, '').length > page.words[best].text.replace(/\W/g, '').length && (best = i));
    s.add(best);
  }
  return s;
};

const Page: React.FC<PageProps> = ({st, f, L, since, left}) => {
  const a = st.anim;
  const r = riseIn(f, since, f30(a.pageIn, f.fps), a.drift);
  const o = Math.min(r.opacity, fadeOut(left, f30(a.pageOut, f.fps)));
  return (
    <Xf opacity={o} dy={r.dy} filter={styleShadow(st, f)}>
      {L.words.map((box, i) => (
        <Word key={i} box={box} fill={st.color} />
      ))}
    </Xf>
  );
};

export const accent: StyleDef = {
  spec: (page, st) => {
    const it = italicIdx(page);
    return page.words.map((_, i) => (it.has(i) ? {font: st.emphasisFont, size: st.anim.italicScale} : {}));
  },
  Page,
};
