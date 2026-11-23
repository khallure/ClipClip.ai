import React from 'react';
import {Word, emphasisOf, Xf, applyCase, baseFontSize, capHeight, easeOutBack, fadeOut, f30, fitWord, riseIn, styleShadow} from '../engine';
import type {BigProps, CBig, Frame, PageProps, Style, StyleDef, WordBox} from '../engine';

// One giant keyword that sits behind the speaker (the "behind" layer); the other words are small and stacked
// under its right end (the "front" layer).

const bare = (s: string) => s.replace(/[^\p{L}\p{N}']+/gu, '').toLowerCase();
const toF = (ms: number, fps: number) => Math.floor((ms * fps) / 1000 + 1e-6);

function bigGeom(b: CBig, st: Style, f: Frame) {
  const bh = st.behind!;
  const font = bh.bigFont ?? st.font;
  const text = applyCase(b.text, 'upper');
  // a `front` word (a number) sits lower, in front of the speaker's chest, a little narrower: it must not cover the face
  const {fs, w} = fitWord(text, font, (f.W * (b.front ? bh.frontWidth ?? 78 : bh.bigWidth ?? 90)) / 100, baseFontSize(bh.bigMaxSize ?? 30, st, f), 0.01);
  const cy = (f.H * (b.front ? bh.frontY ?? 60 : bh.bigY ?? 40)) / 100;
  const baseline = cy + (capHeight(font) * fs) / 2;
  return {text, font, fs, w, left: (f.W - w) / 2, baseline};
}
const activeBig = (big: CBig[], frame: number, fps: number) =>
  big.findIndex((b) => {
    const s = toF(b.startMs, fps);
    return frame >= s && frame < Math.max(toF(b.endMs, fps), s + 1);
  });

const Big: React.FC<BigProps> = ({st, f, big, frame}) => {
  const i = activeBig(big, frame, f.fps);
  if (i < 0) return null;
  const b = big[i];
  const g = bigGeom(b, st, f);
  const a = st.anim;
  const s = toF(b.startMs, f.fps);
  const e = Math.max(toF(b.endMs, f.fps), s + 1);
  const k = easeOutBack((frame - s + 1) / (f30(a.bigIn, f.fps) + 1), a.overshoot ?? 1.1);
  const dir = Math.round(b.startMs / 100) % 2 === 0 ? -1 : 1; // alternate: slide down from above, rise from below
  const dy = dir * f.H * (a.slide / 100) * (1 - k);
  const op = fadeOut(e - frame, f30(a.bigOut, f.fps));
  const box: WordBox = {i: 0, text: g.text, font: g.font, fs: g.fs, ls: 0.01, left: g.left, w: g.w, cx: g.left + g.w / 2, line: 0, baseline: g.baseline};
  return (
    <Xf dy={dy} opacity={op * (st.behind!.bigOpacity ?? 1)}>
      <Word box={box} fill={st.behind!.bigColor ?? st.color} />
    </Xf>
  );
};

const Page: React.FC<PageProps> = ({st, f, page, L, ws, frame, captions}) => {
  const a = st.anim;
  const big = captions.big ?? [];
  const cur = activeBig(big, frame, f.fps);
  const geo = cur >= 0 ? bigGeom(big[cur], st, f) : null;
  const small = page.words
    .map((w, i) => ({w, i}))
    .filter(({w}) => !big.some((b) => w.startMs >= b.startMs && w.startMs < b.endMs && bare(b.text).includes(bare(w.text))));
  const bh = st.behind!;
  const right = geo ? geo.left + geo.w : f.W * 0.5 + (f.W * (bh.bigWidth ?? 90)) / 200;
  const top = geo ? geo.baseline + L.fs * 1.7 : (f.H * ((bh.bigY ?? 40) + 12)) / 100;
  const lh = L.fs * 1.5;
  const e = emphasisOf(st);
  return (
    <Xf filter={styleShadow(st, f)}>
      {small.map(({i}, slot) => {
        const box = L.words[i];
        const r = riseIn(f, ws[i].since, f30(a.smallIn, f.fps), a.smallRise);
        const x = right - box.w;
        return <Word key={i} box={{...box, left: x, cx: x + box.w / 2, baseline: top + lh * slot}} fill={page.words[i].emphasis ? e.color ?? st.color : st.color} opacity={r.opacity} dy={r.dy} />;
      })}
    </Xf>
  );
};

export const keyword: StyleDef = {Page, Big};
