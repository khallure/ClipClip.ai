import React from 'react';
import {Xf, easeInOut, applyCase, baseFontSize, behindY, capHeight, easeOut, fadeOut, f30, subjectIn} from '../engine';
import type {BigProps, StyleDef} from '../engine';
import {activeBig, bigDrawn, inFront, placeX, toF, fitTo} from './behind-kit';
import {SmallLine} from './behind-small';

// A giant stroke-only word behind the speaker. The outline wipes in, then the word fills with colour from the baseline up
// as it is spoken: the hollow letters becoming solid is the emphasis.

const Big: React.FC<BigProps> = ({st, f, big, frame, captions, layer}) => {
  const leadF = f30((st.anim as any).lead ?? 0, f.fps);
  let i = activeBig(big, frame, f.fps);
  if (i < 0) i = activeBig(big, frame + leadF, f.fps); // the hollow word shows a little before it is spoken
  if (i < 0) return null;
  const b = big[i];
  if (!bigDrawn(b, st, layer)) return null;
  const bh = st.behind!;
  const a = st.anim;
  const font = bh.bigFont ?? st.font;
  const text = applyCase(b.text, 'upper');
  const fit = fitTo(text, font, (f.W * (bh.bigWidth ?? 90)) / 100, baseFontSize(bh.bigMaxSize ?? 30, st, f), 0.01);
  const sub = subjectIn(captions, b.startMs, b.endMs);
  const yPct = inFront(b, layer) ? (bh as any).frontY ?? 62 : behindY({startMs: 0, endMs: 0, bands: sub.bands}, ((fit.cap * 1.15) / f.H) * 100, bh.bigY ?? 20, (bh as any).target ?? 0.3);
  const left = inFront(b, layer) ? (f.W - fit.w) / 2 : placeX(sub, f.W, fit.w, (bh as any).overlap ?? 0.28);
  const baseline = (f.H * yPct) / 100 + fit.cap / 2;
  const s = toF(b.startMs, f.fps);
  const e = Math.max(toF(b.endMs, f.fps), s + 1);
  const t = frame - s;
  const wipe = easeOut((t + leadF + 1) / (f30(a.wipe, f.fps) + 1)); // the outline draws on, left to right
  const fill = easeInOut((t - f30(a.fillDelay, f.fps) + 1) / (f30(a.fillIn, f.fps) + 1)); // the colour rises from the baseline
  const op = fadeOut(e - frame, f30(a.bigOut, f.fps));
  const sw = fit.fs * (a.stroke ?? 0.03);
  const id = `ol${Math.round(b.startMs)}`;
  const capTop = baseline - fit.cap;
  const common = {x: left, y: baseline, fontFamily: `"${font.family}"`, fontWeight: font.weight, fontSize: fit.fs, letterSpacing: 0.01 * fit.fs, style: {fontSynthesis: 'none', whiteSpace: 'pre'} as React.CSSProperties};
  return (
    <Xf opacity={op * (bh.bigOpacity ?? 1)}>
      <defs>
        <clipPath id={`${id}-w`}><rect x={left - sw * 2} y={capTop - fit.fs * 0.2} width={(fit.w + sw * 4) * wipe} height={fit.fs * 1.4} /></clipPath>
        <clipPath id={`${id}-f`}><rect x={left - sw * 2} y={baseline + fit.fs * 0.05 - (fit.cap * 1.12 + fit.fs * 0.05) * fill} width={fit.w + sw * 4} height={fit.cap * 1.12 + fit.fs * 0.2} /></clipPath>
      </defs>
      <g clipPath={`url(#${id}-w)`}>
        <text {...common} fill="none" stroke={a.strokeColor ?? '#FFFFFF'} strokeWidth={sw} strokeLinejoin="round">{text}</text>
      </g>
      {fill > 0.001 && (
        <g clipPath={`url(#${id}-f)`}>
          <text {...common} fill={bh.bigColor ?? st.color} stroke={bh.bigColor ?? st.color} strokeWidth={sw * 0.6} strokeLinejoin="round">{text}</text>
        </g>
      )}
    </Xf>
  );
};

export const outline: StyleDef = {Page: SmallLine as any, Big};
