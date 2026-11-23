import React from 'react';
import {Xf, clamp01, easeOut, easeOutBack, f30, mix, px, textWidth} from '../engine';
import type {PageProps, StyleDef, WordBox} from '../engine';

// Heavy round caps. Every letter bounces in along an arc (rises from below, scales 40% -> 110% -> 100%, a little rotation),
// each letter one frame behind the one before. Hard offset shadow plus a thick outer stroke: a sticker look.

const Letter: React.FC<{box: WordBox; ch: string; x: number; fill: string; f: PageProps['f']; since: number; dur: number; a: Record<string, any>; alt: number}> = ({box, ch, x, fill, f, since, dur, a, alt}) => {
  if (since < 0) return null;
  const k = clamp01((since + 1) / (dur + 1));
  const e = easeOut(k);
  const sc = mix(a.scaleFrom, 1, easeOutBack(k, 2.4));
  const rot = a.rotate * (1 - e) * alt;
  const dy = px(f, a.rise) * (1 - e) - Math.sin(Math.PI * k) * px(f, a.arc);
  const dx = -px(f, a.drift) * (1 - e);
  const w = textWidth(ch, box.font, box.fs, box.ls);
  const pcx = x + w / 2;
  const pcy = box.baseline - box.fs * 0.34;
  const op = clamp01((since + 1) / 2);
  const common = {
    x,
    y: box.baseline,
    fontFamily: `"${box.font.family}"`,
    fontWeight: box.font.weight,
    fontSize: box.fs,
    style: {fontSynthesis: 'none', whiteSpace: 'pre'} as React.CSSProperties,
  };
  const sw = px(f, a.stroke);
  return (
    <g opacity={op} transform={`translate(${pcx + dx} ${pcy + dy}) rotate(${rot}) scale(${sc}) translate(${-pcx} ${-pcy})`}>
      <text {...common} transform={`translate(0 ${px(f, a.shadowY)})`} fill={a.shadowColor} stroke={a.shadowColor} strokeWidth={sw} strokeLinejoin="round">{ch}</text>
      <text {...common} fill={fill} stroke={a.strokeColor} strokeWidth={sw} strokeLinejoin="round" paintOrder="stroke">{ch}</text>
    </g>
  );
};

const Page: React.FC<PageProps> = ({st, f, page, L, ws}) => {
  const a = st.anim;
  const dur = f30(a.letterIn, f.fps);
  const stagger = Math.max(1, f30(a.stagger, f.fps));
  return (
    <Xf>
      {L.words.map((box, i) => {
        const chars = [...box.text];
        const fill = page.words[i].emphasis ? st.activeColor! : st.color;
        return (
          <g key={i}>
            {chars.map((ch, j) => {
              const x = box.left + (j ? textWidth(chars.slice(0, j).join(''), box.font, box.fs, box.ls) : 0);
              return <Letter key={j} box={box} ch={ch} x={x} fill={fill} f={f} since={ws[i].since - j * stagger} dur={dur} a={a} alt={j % 2 ? 0.6 : 1} />;
            })}
          </g>
        );
      })}
    </Xf>
  );
};

export const impact: StyleDef = {layout: () => ({align: 'left'}), Page};
