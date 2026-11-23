import React from 'react';
import {Container, Word, colorMix, emphasisOf, Xf, baseFontSize, blockBox, easeOut, fadeIn, fadeOut, f30, mix, styleShadow, plateFill, useLegibility} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// One frosted pill per page. Upcoming words are dim and brighten as they are spoken.
// The blur under the pill comes from the mask layer (see `backdropBlur` in the preset).

const Page: React.FC<PageProps> = ({st, f, page, L, ws, since, left, mode}) => {
  const a = st.anim;
  const c = st.container!;
  const lg = useLegibility(); // plate mode: the pill darkens on a bright background
  const e = emphasisOf(st);
  const box = blockBox(L, c.padX * L.fs, c.padY * L.fs);
  const inR = fadeIn(since, f30(a.pageIn, f.fps));
  const op = Math.min(inR.opacity, fadeOut(left, f30(a.pageOut, f.fps)));
  const scale = mix(a.pageScaleFrom, 1, easeOut((since + 1) / (f30(a.pageIn, f.fps) + 1)));
  return (
    <>
      <Container kind="pill" mode={mode} box={box} f={f} radius={c.radius * L.fs} fill={plateFill(c.fill, lg)} highlight={c.highlight} opacity={op} scale={scale} uid="glass" />
      {mode === 'visible' ? (
        <Xf ox={box.x + box.w / 2} oy={box.y + box.h / 2} scale={scale} opacity={op} filter={styleShadow(st, f)}>
          {L.words.map((box, i) => {
            const k = ws[i].since < 0 ? 0 : easeOut((ws[i].since + 1) / (f30(a.wordIn, f.fps) + 1));
            const fill = page.words[i].emphasis ? colorMix(k, st.color, e.color ?? st.color) : st.color;
            return <Word key={i} box={box} fill={fill} opacity={mix(a.dim, 1, k)} />;
          })}
        </Xf>
      ) : null}
    </>
  );
};

export const glass: StyleDef = {
  // an emphasised word is set in a heavier weight; the layout measures it that way
  spec: (page, st) => page.words.map((w) => (w.emphasis && emphasisOf(st).font ? {font: emphasisOf(st).font} : {})),
  layout: (st, f) => ({padX: st.container!.padX * baseFontSize(st.size, st, f)}),
  hasMask: true,
  Page,
};
