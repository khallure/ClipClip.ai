import React from 'react';
import {Word, Xf, applyCase, baseFontSize, capHeight, emphasisOf, f30, fadeOut, fontWithGlyphs, easeOut, subjectIn, styleShadow, textWidth} from '../engine';
import type {FontSpec, PageProps, StyleDef, WordBox} from '../engine';

// Words alternate to the two sides of the speaker, stepping down the frame like a staircase around the head. A word goes
// beside the face when it fits there; when neither side has room it drops below the chin, where the whole width is free.

const Page: React.FC<PageProps> = ({st, f, page, ws, left: leftF, captions, t}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const sub = subjectIn(captions, page.startMs, page.endMs);
  const face = sub.face;
  const margin = (f.W * st.position.marginX) / 100;
  const gap = f.H * a.faceGap;
  // the head: the face box with hair, ears and neck added
  const hx0 = face ? (face[0] - face[2] * 0.32) * f.W : f.W * 0.5, hx1 = face ? (face[0] + face[2] * 1.32) * f.W : f.W * 0.5;
  const hy0 = face ? (face[1] - face[3] * 0.5) * f.H : 0, hy1 = face ? (face[1] + face[3] * 1.3) * f.H : 0;
  const base = baseFontSize(st.size, st, f);
  const serif: FontSpec = a.serifFont;
  const n = page.words.length;
  const items = page.words.map((w, i) => {
    const key = !!w.emphasis;
    const text = key ? applyCase(w.text, 'lower') : applyCase(w.text, st.case);
    const font = fontWithGlyphs(key ? serif : st.font, text);
    const fs = base * (key ? a.keyScale : 1);
    return {i, text, font, fs, w: textWidth(text, font, fs, 0), cap: capHeight(font) * fs, key};
  });
  const lineH = Math.max(...items.map((x) => x.cap)) * a.rowGap;
  let y = (f.H * st.position.y) / 100 - ((n - 1) * lineH) / 2 + items[0].cap / 2;
  const placed: {it: (typeof items)[0]; x: number; baseline: number; side: -1 | 1}[] = [];
  items.forEach((it, k) => {
    let side: -1 | 1 = k % 2 === 0 ? -1 : 1;
    const roomL = hx0 - gap - margin, roomR = f.W - margin - hx1 - gap;
    for (;;) {
      const top = y - it.cap, bot = y;
      const touches = !!face && bot > hy0 && top < hy1;
      if (!touches) break; // below (or above) the head: the whole width is free
      if (it.w <= (side < 0 ? roomL : roomR)) break;
      if (it.w <= (side < 0 ? roomR : roomL)) { side = (side * -1) as -1 | 1; break; }
      y = hy1 + it.cap + gap * 0.6; // no room beside the face: step below the chin
    }
    const touches = !!face && y > hy0 && y - it.cap < hy1;
    let x: number;
    if (touches) x = side < 0 ? hx0 - gap - it.w : hx1 + gap; // hugging the head
    else x = side < 0 ? margin : f.W - margin - it.w; // free rows: out at the margins
    placed.push({it, x, baseline: y, side});
    y += lineH;
  });
  void t;
  return (
    <Xf filter={styleShadow(st, f)} opacity={fadeOut(leftF, f30(a.pageOut, f.fps))}>
      {placed.map(({it, x, baseline, side}) => {
        const p = ws[it.i].since < 0 ? 0 : easeOut((ws[it.i].since + 1) / (f30(a.wordIn, f.fps) + 1));
        const box: WordBox = {i: it.i, text: it.text, font: it.font, fs: it.fs, ls: 0, left: x, w: it.w, cx: x + it.w / 2, line: 0, baseline};
        return <Word key={it.i} box={box} fill={it.key ? e.color ?? st.color : st.color} opacity={p} dx={side * f.W * a.slide * (1 - p)} />;
      })}
    </Xf>
  );
};

export const split: StyleDef = {Page};
