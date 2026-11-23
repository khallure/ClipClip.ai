import React from 'react';
import {FeatherMask, Word, emphasisOf, Xf, blurCss, dropShadow, fadeOut, f30, joinFilters, easeInOut, clamp01, styleShadow} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// One warm colour. Each word is revealed by a soft left-to-right wipe; the page dissolves with a blur.

const Page: React.FC<PageProps> = ({st, f, page, pageIndex, L, ws, left}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const outFrames = f30(a.pageOut, f.fps);
  const o = clamp01(left / outFrames);
  const dis = 1 - easeInOut(o);
  return (
    <Xf opacity={1 - dis * 0.65} filter={joinFilters(styleShadow(st, f), blurCss(f, a.pageBlur * dis))}>
      {L.words.map((box, i) => {
        const p = clamp01((ws[i].since + 1) / (f30(a.wipe, f.fps) + 1));
        if (ws[i].since < 0) return null;
        const id = `wm-${pageIndex}-${i}`;
        const h = box.fs * 1.5;
        return (
          <React.Fragment key={i}>
            <FeatherMask id={id} x={box.left} y={box.baseline - box.fs * 1.05} w={box.w} h={h} p={easeInOut(p)} feather={a.feather} />
            <Word box={box} fill={page.words[i].emphasis ? e.color ?? st.color : st.color} mask={`url(#${id})`} filter={blurCss(f, a.wordBlur * (1 - p))} />
          </React.Fragment>
        );
      })}
    </Xf>
  );
};

export const whisper: StyleDef = {
  // an emphasised word is a little heavier (and white, see Page); the layout measures it that way
  spec: (page, st) => page.words.map((w) => (w.emphasis && emphasisOf(st).font ? {font: emphasisOf(st).font} : {})),
  Page,
};
void dropShadow;
void fadeOut;
