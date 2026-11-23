import React from 'react';
import {Xf, applyCase, baseFontSize, behindY, easeOutExpo, fadeOut, f30, subjectIn, textWidth} from '../engine';
import type {BigProps, StyleDef} from '../engine';
import {activeBig, bigDrawn, inFront, placeX, toF, fitTo} from './behind-kit';
import {SmallLine} from './behind-small';

// Numbers, years and names go giant behind the speaker; everything else is small in front. Digits roll like an odometer
// into place; letters and dashes in a name (GPT-4) rise in.

const Big: React.FC<BigProps> = ({st, f, big, frame, captions, layer}) => {
  const i = activeBig(big, frame, f.fps);
  if (i < 0) return null;
  const b = big[i];
  if (!bigDrawn(b, st, layer)) return null;
  const bh = st.behind!;
  const a = st.anim;
  const font = bh.bigFont ?? st.font;
  const text = applyCase(b.text, 'upper');
  const fit = fitTo(text, font, (f.W * (bh.bigWidth ?? 80)) / 100, baseFontSize(bh.bigMaxSize ?? 30, st, f), 0.01);
  const sub = subjectIn(captions, b.startMs, b.endMs);
  const front = inFront(b, layer);
  const yPct = front ? (bh as any).frontY ?? 60 : behindY({startMs: 0, endMs: 0, bands: sub.bands}, ((fit.cap * 1.1) / f.H) * 100, bh.bigY ?? 20, (bh as any).target ?? 0.25);
  const left = front ? (f.W - fit.w) / 2 : placeX(sub, f.W, fit.w, (bh as any).overlap ?? 0.25);
  const baseline = (f.H * yPct) / 100 + fit.cap / 2;
  const s = toF(b.startMs, f.fps);
  const e = Math.max(toF(b.endMs, f.fps), s + 1);
  const t = (frame - s + 1) / (f30(a.roll, f.fps) + 1);
  const p = easeOutExpo(t);
  const op = fadeOut(e - frame, f30(a.bigOut, f.fps));
  const lh = fit.fs * 1.0;
  const id = `nm${b.startMs}`;
  const chars = [...text];
  let digit = 0;
  return (
    <Xf opacity={op * (bh.bigOpacity ?? 1)}>
      <defs>
        <clipPath id={id}><rect x={left - fit.fs * 0.1} y={baseline - fit.cap - fit.fs * 0.04} width={fit.w + fit.fs * 0.2} height={fit.cap + fit.fs * 0.08} /></clipPath>
      </defs>
      <g clipPath={`url(#${id})`}>
        {chars.map((ch, k) => {
          const x = left + textWidth(text.slice(0, k), font, fit.fs, 0.01);
          const common = {x, fontFamily: `"${font.family}"`, fontWeight: font.weight, fontSize: fit.fs, letterSpacing: 0.01 * fit.fs, fill: bh.bigColor ?? st.color, style: {fontSynthesis: 'none', whiteSpace: 'pre'} as React.CSSProperties};
          if (/\d/.test(ch)) {
            const d = Number(ch);
            const steps = (a.spins ?? 7) + digit * (a.spinStep ?? 3);
            digit++;
            const pos = steps * (1 - p); // digits still to scroll
            const out: React.ReactNode[] = [];
            for (let j = Math.floor(pos) - 1; j <= Math.ceil(pos) + 1; j++) {
              const v = (((d + j) % 10) + 10) % 10;
              out.push(<text key={j} {...common} y={baseline + (j - pos) * lh * -1 * -1}>{v}</text>);
            }
            return <g key={k}>{out}</g>;
          }
          const q = Math.min(1, Math.max(0, p * 1.4 - k * 0.05));
          return <text key={k} {...common} y={baseline + (1 - q) * fit.cap * 0.9} opacity={q}>{ch}</text>;
        })}
      </g>
    </Xf>
  );
};

export const numeral: StyleDef = {Page: SmallLine as any, Big};
