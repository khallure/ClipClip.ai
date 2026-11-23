import React from 'react';
import {Xf, capHeight, clamp01, dropShadow, easeOut, f30, mix, px, textWidth} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// A tilted pile: every word has its own size and rotation, the words overlap down a diagonal, key words are yellow.
// A word pops in already rotated at 130% and settles; earlier words stay put. Alternate pages mirror the tilt
// and the side of the frame the pile hangs from.

const SIZE = [0.85, 1.0, 1.2, 0.9, 1.1, 0.95];
const ROT = [-9, 5, -12, 7, -6, 8];
const SHIFT = [0, 0.16, 0.04, 0.2, 0.08, 0.22];

const Page: React.FC<PageProps> = ({st, f, page, pageIndex, L, ws}) => {
  const a = st.anim;
  const mirror = pageIndex % 2 ? -1 : 1;
  const font = st.font;
  const ls = st.letterSpacing ?? 0;
  const emph = page.words.map((w) => !!w.emphasis);
  const text = L.words.map((b) => b.text);
  const mult = text.map((_, i) => (emph[i] ? a.keyScale : SIZE[i % 6]));
  let fs = mult.map((m) => L.fs * m);
  const maxW = f.W * (a.pileWidth / 100);
  const w0 = text.map((t, i) => textWidth(t, font, fs[i], ls));
  const k = Math.min(1, maxW / Math.max(...w0));
  fs = fs.map((v) => v * k);
  const w = w0.map((v) => v * k);
  const cap = fs.map((v) => capHeight(font) * v);
  const total = cap.reduce((s, c) => s + c * a.pitch, 0);
  const y0 = (f.H * st.position.y) / 100 - total / 2;
  const edge = (f.W * st.position.marginX) / 100;
  const dur = f30(a.popIn, f.fps);
  let y = y0;
  return (
    <Xf>
      {text.map((t, i) => {
        const cy = y + (cap[i] * a.pitch) / 2;
        y += cap[i] * a.pitch;
        const slack = Math.max(0, maxW - w[i]);
        const sh = Math.min(SHIFT[i % 6] * maxW, slack);
        const cx = mirror > 0 ? edge + w[i] / 2 + sh : f.W - edge - w[i] / 2 - sh;
        if (ws[i].since < 0) return null;
        const p = clamp01((ws[i].since + 1) / (dur + 1));
        const sc = mix(a.popFrom, 1, easeOut(p));
        const rot = mirror * ROT[i % 6];
        const yellow = emph[i];
        const props = {
          x: -w[i] / 2,
          y: cap[i] / 2,
          fontFamily: `"${font.family}"`,
          fontWeight: font.weight,
          fontSize: fs[i],
          letterSpacing: ls * fs[i],
          style: {fontSynthesis: 'none', whiteSpace: 'pre'} as React.CSSProperties,
        };
        const sw = px(f, a.stroke);
        return (
          <g key={i} transform={`translate(${cx} ${cy}) rotate(${rot}) scale(${sc})`} filter={yellow ? dropShadow(f, 0, 0, 12, 'rgba(255,255,255,0.3)') : undefined}>
            <text {...props} transform={`translate(${px(f, 2.5)} ${px(f, a.shadowY)})`} fill="rgba(0,0,0,0.72)" stroke="rgba(0,0,0,0.72)" strokeWidth={sw} strokeLinejoin="round">{t}</text>
            <text {...props} fill={yellow ? st.activeColor! : st.color} stroke="#000" strokeWidth={sw} strokeLinejoin="round" paintOrder="stroke">{t}</text>
          </g>
        );
      })}
    </Xf>
  );
};

export const stack: StyleDef = {Page};
