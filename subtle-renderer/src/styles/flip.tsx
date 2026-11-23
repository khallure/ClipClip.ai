import React from 'react';
import {Xf, capHeight, colorMix, emphasisOf, f30, fadeOut, styleShadow, textWidth, easeOutBack} from '../engine';
import type {PageProps, StyleDef, WordBox} from '../engine';

// Every letter flips in like a split-flap tile: it turns edge-on and settles upright, with a short trail of ghost copies
// for motion blur and a shade that darkens it while it is turned away from the light.

const Letter: React.FC<{ch: string; box: WordBox; x: number; p: number; fill: string; shade: string; trail: number; capH: number}> = ({ch, box, x, p, fill, shade, trail, capH}) => {
  if (p <= 0) return null;
  const cy = box.baseline - capH / 2;
  const draw = (pp: number, op: number, k: string) => {
    const th = (Math.PI * 150 * (1 - Math.min(1, Math.max(0, pp)))) / 180;
    const sy = Math.cos(th);
    const turned = 1 - Math.abs(sy);
    return (
      <g key={k} opacity={op} transform={`translate(0 ${cy}) scale(1 ${Math.abs(sy) < 0.02 ? 0.02 * Math.sign(sy || 1) : sy}) translate(0 ${-cy})`}>
        <text x={x} y={box.baseline} fontFamily={`"${box.font.family}"`} fontWeight={box.font.weight} fontSize={box.fs} fill={colorMix(turned * 0.65, fill, shade)} style={{fontSynthesis: 'none', whiteSpace: 'pre'}}>{ch}</text>
      </g>
    );
  };
  return (
    <g>
      {p < 1 && draw(p - trail * 2, 0.16, 'g2')}
      {p < 1 && draw(p - trail, 0.3, 'g1')}
      {draw(p, 1, 'main')}
    </g>
  );
};

const Page: React.FC<PageProps> = ({st, f, page, L, ws, left}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const dur = f30(a.letterIn, f.fps);
  const stag = a.stagger * (f.fps / 30);
  return (
    <Xf filter={styleShadow(st, f)} opacity={fadeOut(left, f30(a.pageOut, f.fps))}>
      {L.words.map((box, i) => {
        const capH = capHeight(box.font) * box.fs;
        const fill = page.words[i].emphasis ? e.color ?? st.color : st.color;
        return (
          <g key={i}>
            {[...box.text].map((ch, k) => {
              const x = box.left + textWidth(box.text.slice(0, k), box.font, box.fs, box.ls);
              const t = (ws[i].since - k * stag + 1) / (dur + 1);
              return <Letter key={k} ch={ch} box={box} x={x} p={ws[i].since < 0 ? 0 : easeOutBack(t, a.overshoot)} fill={fill} shade={a.shade} trail={a.trail} capH={capH} />;
            })}
          </g>
        );
      })}
    </Xf>
  );
};

export const flip: StyleDef = {Page};
