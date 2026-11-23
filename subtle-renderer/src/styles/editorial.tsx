import React from 'react';
import {LiningWord, Word, applyCase, baseFontSize, blurCss, capHeight, emphasisOf, fitRun, mix, placeBox, styleShadow, Xf} from '../engine';
import type {PageProps, StyleDef, WordBox} from '../engine';
import {f30} from '../engine';

// A bold sans lowercase line with the keyword set about 3x larger in a high-contrast italic serif in red, overlapping the sans.
// Layout: words before the keyword above it, the keyword, words after below it; all left-aligned in the lower-left third.
// Words hard-cut in one at a time; the keyword scales 85% -> 100% in 3 frames with a one-frame blur.
// The keyword is the emphasised word, or else the longest word of the page that carries meaning.

const STOP = new Set('a an and are as at be but by for from had has have he her his i i\'m i\'ve in is it it\'s its me my of on or our she so than that the their them then there these they this to us was we were what when who will with you your you\'re'.split(' '));
const bare = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}']+/gu, '');

export function keywordIndex(words: {text: string; emphasis?: boolean}[]) {
  const e = words.findIndex((w) => w.emphasis);
  if (e >= 0) return e;
  // nothing marked: the longest word that carries meaning (the later one on a tie), never a filler word
  let best = -1;
  for (let i = 0; i < words.length; i++) {
    const w = bare(words[i].text);
    if (STOP.has(w) || w.length < 4) continue;
    if (best < 0 || w.length >= bare(words[best].text).length) best = i;
  }
  if (best >= 0) return best;
  for (let i = words.length - 1; i >= 0; i--) if (!STOP.has(bare(words[i].text))) return i;
  return words.length - 1;
}

const Page: React.FC<PageProps> = ({st, f, page, ws}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const kw = keywordIndex(page.words);
  const before = Array.from({length: kw}, (_, i) => i);
  const after = Array.from({length: page.words.length - kw - 1}, (_, i) => kw + 1 + i);
  const s0 = baseFontSize(st.size, st, f);
  const x0 = (f.W * (st.position.x ?? 9)) / 100;
  const avail = f.W - x0 - (f.W * st.position.marginX) / 100;
  const kFont = e.font ?? st.font;
  const probe = fitRun([applyCase(page.words[kw].text, st.case)], kFont, s0 * e.scale, {ls: e.letterSpacing ?? 0, maxW: avail});
  let k = probe.fs / (s0 * e.scale); // shrink factor so the keyword fits, applied to the whole block
  const txt = (idx: number[]) => idx.map((i) => applyCase(page.words[i].text, st.case));
  const rowA = before.length ? fitRun(txt(before), st.font, s0, {ls: st.letterSpacing, maxW: avail}) : null;
  const rowC = after.length ? fitRun(txt(after), st.font, s0, {ls: st.letterSpacing, maxW: avail}) : null;
  for (const r of [rowA, rowC]) if (r) k = Math.min(k, r.fs / s0);
  const s = s0 * k;
  const key = fitRun([applyCase(page.words[kw].text, st.case)], kFont, s * e.scale, {ls: e.letterSpacing ?? 0, maxW: avail});
  const A = rowA ? fitRun(txt(before), st.font, s, {ls: st.letterSpacing, maxW: avail}) : null;
  const C = rowC ? fitRun(txt(after), st.font, s, {ls: st.letterSpacing, maxW: avail}) : null;
  const capS = capHeight(st.font) * s;
  // baselines relative to the keyword baseline
  const yK = 0;
  const yA = -a.rowGap * s;
  const yC = a.afterGap * s;
  const top = A ? yA - capS : yK - key.fs * 0.75;
  const bottom = C ? yC + 0.25 * s : yK + key.fs * 0.3;
  const shift = (f.H * st.position.y) / 100 - (top + bottom) / 2;
  const kwWs = ws[kw];
  const dur = f30(a.keyIn, f.fps);
  const p = kwWs.since < 0 ? 0 : Math.min(1, (kwWs.since + 1) / (dur + 1));
  const sc = mix(a.keyFrom, 1, p);
  const blur = kwWs.since === 0 ? a.keyBlur : 0;
  return (
    <Xf filter={styleShadow(st, f)}>
      {A?.boxes.map((b, j) => (ws[before[j]].since >= 0 ? <Word key={`a${j}`} box={placeBox(b, x0, yA + shift)} fill={st.color} /> : null))}
      {kwWs.since >= 0 ? <LiningWord box={placeBox(key.boxes[0], x0 + a.keyInset * s, yK + shift)} fill={e.color ?? st.color} scale={sc} filter={blurCss(f, blur)} /> : null}
      {C?.boxes.map((b, j) => (ws[after[j]].since >= 0 ? <Word key={`c${j}`} box={placeBox(b, x0, yC + shift)} fill={st.color} /> : null))}
    </Xf>
  );
};

export const editorial: StyleDef = {Page};
