import React from 'react';
import {Word, Xf, applyCase, baseFontSize, capHeight, emphasisOf, f30, fadeOut, fontWithGlyphs, riseIn, spaceWidth, styleShadow, textWidth} from '../engine';
import type {FontSpec, PageProps, StyleDef, WordBox} from '../engine';

// Ad supers: the page is a tight justified block. Keywords are big serif italics, the glue words small grotesk capitals,
// and every line is scaled to the same width, so the block has hard edges like a magazine headline.

const GLUE = new Set('a an and are as at be but by for from had has have he her his i i\'m in is it it\'s its me my of on or our she so than that the their them then there these they this to us was we were what when who will with you your'.split(' '));
const bare = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}'-]+/gu, '');

type Item = {i: number; text: string; font: FontSpec; mul: number; serif: boolean};

const Page: React.FC<PageProps> = ({st, f, page, ws, left}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const serif: FontSpec = a.serifFont;
  const sans: FontSpec = st.font;
  const base = baseFontSize(st.size, st, f);
  const items: Item[] = page.words.map((w, i) => {
    const glue = GLUE.has(bare(w.text)) && !w.emphasis;
    const sf = !glue;
    const text = sf ? applyCase(w.text, 'lower') : applyCase(w.text, 'upper');
    const font = fontWithGlyphs(sf ? serif : sans, text);
    return {i, text, font, mul: w.emphasis ? a.keyScale : sf ? a.serifScale : a.glueScale, serif: sf};
  });
  // lines: the page's own breaks (group.py balances them); at most 3
  const lineOf = (k: number) => Math.min(page.words[k].line ?? 0, 2);
  const ids = [...new Set(items.map((_, k) => lineOf(k)))].sort();
  const blockW = (f.W * a.blockWidth) / 100;
  const gapEm = a.wordGap;
  const lines = ids.map((id) => {
    const its = items.filter((_, k) => lineOf(k) === id);
    const natural = (fs: number) => its.reduce((s, it) => s + textWidth(it.text, it.font, fs * it.mul, 0), 0) + (its.length - 1) * spaceWidth(sans, fs) * gapEm;
    let fs = base;
    const w0 = natural(fs);
    const k = Math.max(a.minStretch, Math.min(a.maxStretch, blockW / w0));
    fs = base * k;
    const w = natural(fs);
    const fit = w > blockW ? (fs * blockW) / w : fs; // never wider than the block
    const fsF = Math.min(fs, fit);
    const cap = Math.max(...its.map((it) => capHeight(it.font) * fsF * it.mul));
    return {its, fs: fsF, w: natural(fsF), cap};
  });
  const gap = f.H * a.lineGap;
  const total = lines.reduce((s, l) => s + l.cap, 0) + gap * (lines.length - 1);
  let y = (f.H * st.position.y) / 100 - total / 2;
  const cx = f.W / 2;
  const dur = f30(a.wordIn, f.fps);
  return (
    <Xf filter={styleShadow(st, f)} opacity={fadeOut(left, f30(a.pageOut, f.fps))}>
      {lines.map((l, li) => {
        const baseline = y + l.cap;
        y += l.cap + gap;
        const justified = l.w >= blockW * 0.97;
        const sp = l.its.length > 1 ? (justified ? (blockW - l.its.reduce((s, it) => s + textWidth(it.text, it.font, l.fs * it.mul, 0), 0)) / (l.its.length - 1) : spaceWidth(sans, l.fs) * gapEm) : 0;
        const lw = l.its.reduce((s, it) => s + textWidth(it.text, it.font, l.fs * it.mul, 0), 0) + sp * (l.its.length - 1);
        let x = cx - lw / 2;
        return (
          <g key={li}>
            {l.its.map((it) => {
              const w = textWidth(it.text, it.font, l.fs * it.mul, 0);
              const box: WordBox = {i: it.i, text: it.text, font: it.font, fs: l.fs * it.mul, ls: 0, left: x, w, cx: x + w / 2, line: li, baseline};
              x += w + sp;
              const r = riseIn(f, ws[it.i].since, dur, a.rise);
              return <Word key={it.i} box={box} fill={page.words[it.i].emphasis ? e.color ?? st.color : it.serif ? st.color : a.glueColor ?? st.color} opacity={r.opacity * (it.serif ? 1 : a.glueOpacity ?? 0.9)} dy={r.dy} />;
            })}
          </g>
        );
      })}
    </Xf>
  );
};

export const magazine: StyleDef = {Page};
