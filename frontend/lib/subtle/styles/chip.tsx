import React from 'react';
import {Container, Word, Xf, baseFontSize, blockBox, clamp01, colorMix, dropShadow, easeOutBack, emphasisOf, fadeIn, fadeOut, f30} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// A light chip hugging the page. Words turn from grey to black as they are spoken.
// An emphasised word gets an inverted chip of its own: a dark rounded rectangle behind it, light text, popping in as it is spoken.

const Page: React.FC<PageProps> = ({st, f, page, L, ws, since, left, mode}) => {
  const a = st.anim;
  const c = st.container!;
  const e = emphasisOf(st);
  const box = blockBox(L, c.padX * L.fs, c.padY * L.fs);
  const op = Math.min(fadeIn(since, f30(a.pageIn, f.fps)).opacity, fadeOut(left, f30(a.pageOut, f.fps)));
  return (
    <>
      <Container kind="chip" mode={mode} box={box} f={f} radius={c.radius * box.h} fill={c.fill} shadow={dropShadow(f, 0, 4, 14, 'rgba(0,0,0,0.15)')} opacity={op} uid="chip" />
      {mode === 'visible' ? (
        <Xf opacity={op}>
          {L.words.map((b, i) => {
            const k = ws[i].since < 0 ? 0 : (ws[i].since + 1) / (f30(a.wordIn, f.fps) + 1);
            if (!page.words[i].emphasis) return <Word key={i} box={b} fill={colorMix(k, st.dimColor!, st.color)} />;
            const p = ws[i].since < 0 ? 0 : clamp01((ws[i].since + 1) / (f30(e.chipIn ?? 4, f.fps) + 1));
            const sc = 0.88 + 0.12 * easeOutBack(p, 1.8);
            const h = b.fs * (e.chipHeight ?? 1.42);
            const px = b.fs * (e.padX ?? 0.3);
            const cy = b.baseline - L.capH / 2;
            const rx = Math.min(h * 0.28, b.fs * 0.4);
            return (
              <g key={i}>
                <Xf ox={b.cx} oy={cy} scale={sc} opacity={p} filter={dropShadow(f, 0, 2, 6, 'rgba(0,0,0,0.25)')}>
                  <rect x={b.left - px} y={cy - h / 2} width={b.w + 2 * px} height={h} rx={rx} fill={e.fill ?? '#141414'} />
                </Xf>
                <Word box={b} fill={colorMix(p, st.dimColor!, e.color ?? '#F6F6F4')} />
              </g>
            );
          })}
        </Xf>
      ) : null}
    </>
  );
};

export const chip: StyleDef = {
  // room beside an emphasised word for its chip, so the chip never touches the words next to it
  spec: (page, st) => {
    const e = emphasisOf(st);
    const room = (e.padX ?? 0.3) * 0.75;
    return page.words.map((_, i) => {
      const next = page.words[i + 1];
      const pad = (page.words[i].emphasis && i < page.words.length - 1 ? room : 0) + (next?.emphasis ? room : 0);
      return pad ? {pad} : {};
    });
  },
  layout: (st, f) => ({padX: st.container!.padX * baseFontSize(st.size, st, f)}),
  hasMask: true,
  Page,
};
