import React from 'react';
import {Xf, applyCase, baseFontSize, easeOutBack, fadeOut, f30, shoulderY, subjectIn, textWidth} from '../engine';
import type {BigProps, StyleDef} from '../engine';
import {activeBig, bigDrawn, inFront, placeX, toF, fitTo} from './behind-kit';
import {SmallLine} from './behind-small';

// The giant word stands up from behind the speaker's shoulders: every letter climbs out of the body one after another
// (the person cut-out hides the part still below the shoulders) and sinks back when the word is done.

const Big: React.FC<BigProps> = ({st, f, big, frame, captions, layer}) => {
  const i = activeBig(big, frame, f.fps);
  if (i < 0) return null;
  const b = big[i];
  if (!bigDrawn(b, st, layer)) return null;
  const bh = st.behind!;
  const a = st.anim;
  const font = bh.bigFont ?? st.font;
  const text = applyCase(b.text, 'upper');
  const ls = a.tracking ?? 0.01;
  const fit = fitTo(text, font, (f.W * (bh.bigWidth ?? 84)) / 100, baseFontSize(bh.bigMaxSize ?? 20, st, f), ls);
  const sub = subjectIn(captions, b.startMs, b.endMs);
  const front = inFront(b, layer);
  const shoulder = (f.H * shoulderY({startMs: 0, endMs: 0, bands: sub.bands}, 40)) / 100;
  const rest = front ? (f.H * ((bh as any).frontY ?? 60)) / 100 : shoulder + fit.cap * (a.tuck ?? 0.18); // letter feet sit a little behind the shoulders
  const left = front ? (f.W - fit.w) / 2 : placeX(sub, f.W, fit.w, (bh as any).overlap ?? 0.25);
  const s = toF(b.startMs, f.fps);
  const e = Math.max(toF(b.endMs, f.fps), s + 1);
  const n = text.length;
  const stag = a.stagger * (f.fps / 30);
  const inF = f30(a.rise, f.fps), outF = f30(a.sink, f.fps);
  const drop = fit.cap * 1.35 + (rest - shoulder); // far enough that the whole letter is behind the body
  return (
    <Xf>
      {[...text].map((ch, k) => {
        if (ch === ' ') return null;
        const x = left + textWidth(text.slice(0, k), font, fit.fs, ls);
        const tIn = (frame - s - k * stag + 1) / (inF + 1);
        const tOut = (frame - (e - outF) - (n - 1 - k) * stag * 0.6) / (outF + 1);
        const up = easeOutBack(tIn, a.overshoot ?? 1.0);
        const down = Math.min(1, Math.max(0, tOut));
        const dy = drop * (1 - up) + drop * down * down;
        if (tIn <= 0 || dy >= drop) return null;
        return (
          <text key={k} x={x} y={rest + dy} fontFamily={`"${font.family}"`} fontWeight={font.weight} fontSize={fit.fs} letterSpacing={ls * fit.fs} fill={bh.bigColor ?? st.color} style={{fontSynthesis: 'none', whiteSpace: 'pre'}}>
            {ch}
          </text>
        );
      })}
    </Xf>
  );
};

export const rise: StyleDef = {Page: SmallLine as any, Big};
void fadeOut;
