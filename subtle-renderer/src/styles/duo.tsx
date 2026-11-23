import React from 'react';
import {Word, Xf, applyCase, baseFontSize, capHeight, fadeIn, fadeOut, f30, fitRun, hasNumeral, placeBox, styleShadow} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// A big condensed caps headline word, a tiny spaced-out lead-in above it (the words before) and a bold italic tail under it
// (the words after). Lead-in fades in, the headline cuts in, the tail fades in; the page fades out.
// The headline is the page's emphasised word, or the longest word when none is marked.

const letters = (s: string) => s.replace(/[^\p{L}\p{N}]/gu, '').length;

const Page: React.FC<PageProps> = ({st, f, page, t, frame, ws, left}) => {
  const a = st.anim;
  const n = page.words.length;
  let h = page.words.findIndex((w) => w.emphasis);
  if (h < 0) h = page.words.reduce((best, w, i) => (letters(w.text) >= letters(page.words[best].text) ? i : best), 0);
  const avail = f.W * (1 - (2 * st.position.marginX) / 100);
  const cx = (f.W * (st.position.x ?? 50)) / 100;
  const cy = (f.H * st.position.y) / 100;
  const fsHero = baseFontSize(st.size, st, f);
  const hero = fitRun([applyCase(page.words[h].text, 'upper')], st.font, fsHero, {ls: st.letterSpacing, maxW: avail});
  const capH = capHeight(st.font) * hero.fs;
  const base = cy + capH / 2;
  const k = hero.fs / fsHero; // shrink of the headline carries to the small pieces
  const heroOn = frame >= t.wordS[h];

  const leadIdx = Array.from({length: h}, (_, i) => i);
  const tailIdx = Array.from({length: n - h - 1}, (_, i) => h + 1 + i);
  const leadFont = a.leadFont;
  const tailFont = a.tailFont;
  const lead = leadIdx.length
    ? fitRun(leadIdx.map((i) => applyCase(page.words[i].text, 'upper')), leadFont, baseFontSize(a.leadSize, st, f) * k, {ls: a.leadTrack, gap: 1.8, maxW: avail})
    : null;
  const tail = tailIdx.length
    ? fitRun(tailIdx.map((i) => applyCase(page.words[i].text, 'lower')), tailFont, baseFontSize(a.tailSize, st, f) * k, {ls: 0, maxW: avail})
    : null;
  const out = fadeOut(left, f30(a.pageOut, f.fps));
  return (
    <Xf opacity={out} filter={styleShadow(st, f)}>
      {lead ? (
        <Xf opacity={fadeIn(ws[leadIdx[0]].since, f30(a.leadIn, f.fps)).opacity}>
          {lead.boxes.map((b, j) => (
            <Word key={j} box={placeBox(b, cx - lead.w / 2, base - capH - 0.2 * hero.fs)} fill={a.softColor} />
          ))}
        </Xf>
      ) : null}
      {heroOn ? <Word box={placeBox(hero.boxes[0], cx - hero.w / 2, base)} fill={st.color} /> : null}
      {tail ? (
        <Xf opacity={fadeIn(ws[tailIdx[0]].since, f30(a.tailIn, f.fps)).opacity}>
          {tail.boxes.map((b, j) => (
            <Word key={j} box={placeBox(b, cx - tail.w / 2, base + 0.22 * hero.fs + capHeight(tailFont) * tail.fs)} fill={a.softColor} />
          ))}
        </Xf>
      ) : null}
    </Xf>
  );
};
void hasNumeral;

export const duo: StyleDef = {Page};
