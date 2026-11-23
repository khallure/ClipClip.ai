import React from 'react';
import {Word, emphasisOf, hasNumeral, Xf, dropShadow, joinFilters, fadeIn, fadeOut, f30, scaleFromBaseline, styleShadow, withAlpha} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Emphasis words in a large calligraphic script, filler words small and bold, all on one baseline.
// The script word grows in from its baseline-left corner.

const Page: React.FC<PageProps> = ({st, f, page, L, ws, left}) => {
  const a = st.anim;
  const script = scriptIndexes(page);
  const out = fadeOut(left, f30(a.pageOut ?? 2, f.fps));
  const sh = styleShadow(st, f);
  const glow = joinFilters(
    dropShadow(f, 0, 0, a.glowBlur ?? 8, a.glowColor ?? withAlpha(st.activeColor!, 0.4)),
    dropShadow(f, 0, 0, (a.glowBlur ?? 8) * 3, withAlpha(st.activeColor!, 0.16)),
    sh,
  );
  const bold = a.scriptBold ?? 0; // the script is hairline thin: a few extra copies a fraction of a pixel apart thicken it
  return (
    <Xf opacity={out}>
      {L.words.map((box, i) => {
        const since = ws[i].since;
        const isScript = script.has(i);
        const r = isScript ? scaleFromBaseline(since, f30(a.scriptIn ?? 5, f.fps), a.scriptFrom ?? 0.1) : fadeIn(since, f30(a.wordIn ?? 2, f.fps));
        return (
          <g key={i}>
            {isScript && bold > 0
              ? [-1, 1].map((k) => <Word key={k} box={box} fill={st.activeColor!} opacity={r.opacity} scale={r.scale} dx={k * bold * (f.H / 1080) * 0.5} filter={glow} />)
              : null}
            <Word box={box} fill={isScript ? st.activeColor! : st.color} opacity={r.opacity} scale={r.scale} filter={isScript ? glow : sh} />
          </g>
        );
      })}
    </Xf>
  );
};

/** The words drawn in script: the flagged ones, or the longest word if the page has none. */
function scriptIndexes(page: PageProps['page']) {
  const s = new Set<number>();
  page.words.forEach((w, i) => w.emphasis && s.add(i));
  if (!s.size) {
    let best = 0;
    page.words.forEach((w, i) => w.text.replace(/\W/g, '').length > page.words[best].text.replace(/\W/g, '').length && (best = i));
    s.add(best);
  }
  return s;
}

export const glide: StyleDef = {
  spec: (page, st) => {
    const sc = scriptIndexes(page);
    const e = emphasisOf(st);
    // digits and currency signs are missing or ugly in the script: a serif italic carries them
    return page.words.map((w, i) =>
      !sc.has(i)
        ? {}
        : hasNumeral(w.text) && e.numberFont
          ? {font: e.numberFont, size: e.numberScale ?? 1.6, ls: 0, pad: 0.16}
          : {font: st.emphasisFont, size: st.anim.scriptScale ?? 2.5, ls: 0.01, pad: 0.22},
    );
  },
  Page,
};
