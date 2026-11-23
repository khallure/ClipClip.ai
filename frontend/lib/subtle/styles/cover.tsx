import React from 'react';
import {Word, Xf, applyCase, baseFontSize, blurCss, behindY, capHeight, easeOut, fadeOut, f30, fadeIn, riseIn, subjectIn, styleShadow, emphasisOf} from '../engine';
import type {BigProps, PageProps, StyleDef, WordBox, WordSpec} from '../engine';
import {activeBig, bigDrawn, inFront, placeX, reflow, smallIdx, toF, fitTo} from './behind-kit';

// A magazine cover: one huge serif italic word behind the speaker's head, like a masthead the head overlaps.
// The rest of the line is small, clean, tracked sans in front; an emphasised small word turns serif italic.

const cap1 = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const Big: React.FC<BigProps> = ({st, f, big, frame, captions, layer}) => {
  const i = activeBig(big, frame, f.fps);
  if (i < 0) return null;
  const b = big[i];
  if (!bigDrawn(b, st, layer)) return null;
  const bh = st.behind!;
  const a = st.anim;
  const font = bh.bigFont ?? st.font;
  const text = cap1(b.text);
  const fit = fitTo(text, font, (f.W * (bh.bigWidth ?? 94)) / 100, baseFontSize(bh.bigMaxSize ?? 30, st, f), a.bigTracking ?? -0.01);
  const sub = subjectIn(captions, b.startMs, b.endMs);
  const shot = {startMs: 0, endMs: 0, bands: sub.bands};
  const yPct = inFront(b, layer) ? (bh as any).frontY ?? 62 : behindY(shot, ((fit.cap * 1.25) / f.H) * 100, bh.bigY ?? 20, (bh as any).target ?? 0.3);
  const baseline = (f.H * yPct) / 100 + fit.cap / 2;
  const s = toF(b.startMs, f.fps);
  const e = Math.max(toF(b.endMs, f.fps), s + 1);
  const k = easeOut((frame - s + 1) / (f30(a.bigIn, f.fps) + 1));
  const op = Math.min(k, fadeOut(e - frame, f30(a.bigOut, f.fps))) * (bh.bigOpacity ?? 1);
  const left = inFront(b, layer) ? (f.W - fit.w) / 2 : placeX(sub, f.W, fit.w, (bh as any).overlap ?? 0.28);
  const box: WordBox = {i: 0, text, font, fs: fit.fs, ls: a.bigTracking ?? -0.01, left, w: fit.w, cx: left + fit.w / 2, line: 0, baseline};
  return (
    <Xf ox={left + fit.w / 2} oy={baseline - fit.cap / 2} scale={1 + (a.bigScale ?? 0.06) * (1 - k)} opacity={op} filter={blurCss(f, (a.bigBlur ?? 14) * (1 - k))}>
      <Word box={box} fill={bh.bigColor ?? st.color} />
    </Xf>
  );
};

const Page: React.FC<PageProps> = ({st, f, page, L, ws, frame, captions, layer}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const idx = smallIdx(page.words, captions, st, frame, f.fps, layer);
  const boxes = reflow(L, idx, L.cx, L.fs * 0.5);
  const rule = L.top - L.fs * 1.1;
  return (
    <Xf filter={styleShadow(st, f)}>
      {a.rule !== false && boxes.length > 0 && <rect x={L.cx - f.H * 0.012} y={rule} width={f.H * 0.024} height={Math.max(1, f.H * 0.0012)} fill={st.color} opacity={0.7 * fadeIn(ws[idx[0]]?.since ?? -1, 6).opacity} />}
      {boxes.map((box) => {
        const r = riseIn(f, ws[box.i].since, f30(a.smallIn, f.fps), a.smallRise);
        return <Word key={box.i} box={box} fill={page.words[box.i].emphasis ? e.color ?? st.color : st.color} opacity={r.opacity} dy={r.dy} />;
      })}
    </Xf>
  );
};

const spec = (page: {words: {text: string; emphasis?: boolean}[]}, st: any): WordSpec[] => {
  const e = st.emphasis ?? {};
  return page.words.map((w) => (w.emphasis && e.font ? {font: e.font, size: e.scale ?? 1.3, text: applyCase(w.text, 'sentence')} : {}));
};

export const cover: StyleDef = {Page, Big, spec: spec as any};
