import React from 'react';
import {Word, Xf, dropShadow, emphasisOf, f30, monoAdvance, px, textWidth} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Mono text typed one character at a time (queued behind the speech) with a blinking block cursor.
// An emphasised word is drawn in inverse video: a green block behind it, dark text.

const Page: React.FC<PageProps> = ({st, f, page, L, ws, frame}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const cpf = a.cps / f.fps; // characters per frame
  // time (frame) each character appears: not before its word starts, not faster than cps
  const chars: {w: number; k: number; t: number}[] = [];
  let prev = -Infinity;
  page.words.forEach((_, wi) => {
    const text = L.words[wi].text;
    for (let k = 0; k < text.length; k++) {
      const t = Math.max(ws[wi].startF, prev + 1 / cpf);
      chars.push({w: wi, k, t});
      prev = t;
    }
  });
  const typed = chars.filter((c) => frame >= c.t - 1e-6);
  const shown = L.words.map((b, wi) => typed.filter((c) => c.w === wi).length);
  const last = typed[typed.length - 1];
  const cw = last ? last.w : 0;
  const cb = L.words[cw];
  const adv = monoAdvance(cb.font, cb.fs);
  const emCur = !!page.words[cw]?.emphasis && shown[cw] > 0;
  // measured, not counted: a word in a fallback font (a glyph Courier lacks) has its own advance
  const cx = cb.left + (last && shown[cw] ? textWidth(cb.text.slice(0, shown[cw]), cb.font, cb.fs, cb.ls) : 0) + (emCur ? adv * 0.18 : 0);
  const cyc = f30(a.blink, f.fps);
  const blink = 0.75 + 0.25 * Math.cos((2 * Math.PI * (frame % (cyc * 2))) / (cyc * 2));
  const glow = dropShadow(f, 0, 0, 5, 'rgba(0,0,0,0.75)');
  return (
    <Xf filter={glow}>
      {L.words.map((box, i) => {
        if (!shown[i]) return null;
        const slice = box.text.slice(0, shown[i]);
        if (!page.words[i].emphasis) return <Word key={i} box={box} fill={st.color} text={slice} />;
        const w = textWidth(slice, box.font, box.fs, box.ls);
        const ov = box.fs * 0.1;
        return (
          <g key={i}>
            <rect x={box.left - ov} y={box.baseline - box.fs * 0.86} width={w + 2 * ov} height={box.fs * 1.12} fill={e.fill ?? st.color} />
            <Word box={box} fill={e.color ?? '#06140A'} text={slice} />
          </g>
        );
      })}
      <rect x={cx + adv * 0.04} y={cb.baseline - cb.fs * 0.8} width={adv * 0.92} height={cb.fs * 1.02} fill={st.activeColor} opacity={blink} />
    </Xf>
  );
};

export const terminal: StyleDef = {
  layout: () => ({align: 'left'}),
  Page,
};
void px;
