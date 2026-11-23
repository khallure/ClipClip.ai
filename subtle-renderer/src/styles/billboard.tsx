import React from 'react';
import {applyCase, beatGrid, beatNumber, capHeight, easeOutBack, f30, fitWord, fontWithGlyphs, mix, snapToBeat, clamp01} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// One or two lowercase words in a fat display serif, so large the loudest one bleeds off the frame edges.
// Three flat tones: text and background swap on every page, and the pair changes every `flipBeats` beats (a page
// counter stands in when there are no beats). The page cuts in on the beat with the big word already there; the small
// word pops from `popFrom` to full size in `popFrames` frames on the beat at or just before it is sung. The block sits
// top-left, bottom-right or centred, in turn. The big word is the emphasised one, else the longest.

const Page: React.FC<PageProps> = ({st, f, page, ws, frame, since, pageIndex, captions}) => {
  const a = st.anim;
  const grid = beatGrid(captions, f.fps);
  const pageStart = frame - since;

  // ---- colours
  const flips = grid ? Math.floor((beatNumber(grid, frame) - beatNumber(grid, pageStart)) / a.flipBeats) : 0;
  const pairs = a.pairs as [string, string][];
  const [bgName, fgName] = pairs[(pageIndex + Math.max(0, flips)) % pairs.length];
  const bg = a.tones[bgName];
  const fg = a.tones[fgName];

  // ---- which word is the big one
  const texts = page.words.map((w) => applyCase(w.text, st.case));
  const letters = (i: number) => texts[i].replace(/[^a-z0-9]/gi, '').length;
  let big = page.words.findIndex((w) => w.emphasis);
  if (big < 0) big = texts.reduce((b, _, i) => (letters(i) >= letters(b) ? i : b), 0);
  if (texts.length === 1) big = 0;

  // ---- sizes
  const e = (st.emphasis ?? {}) as Record<string, number>;
  const tall = f.H > f.W * 1.2;
  const items = texts.map((text, i) => {
    const font = fontWithGlyphs(st.font, text);
    const isBig = i === big;
    // a very short word is the hardest bleed, a long word is narrower so it keeps its letters
    const nl = text.replace(/[^a-z0-9]/gi, '').length;
    const bleed = nl <= 3 ? 1.15 : nl <= 6 ? 1 : 0.76;
    // a number token (25,000, 10%, ₹1 lakh) must draw whole: it never bleeds
    const num = /[0-9$₹€£%]/.test(text);
    const wMax = num || !isBig ? f.W * 0.9 : nl > 3 ? f.W * 0.94 : Infinity;
    const raw = tall
      ? fitWord(text, font, f.W * (isBig ? a.tallBig * Math.min(1, bleed) : a.tallSmall), f.H * (isBig ? 0.58 : 0.24), st.letterSpacing)
      : fitWord(text, font, f.W * (isBig ? (page.words[i].emphasis ? e.fill ?? a.fillBig : a.fillBig) * bleed : a.fillSmall), (f.H * (isBig ? a.maxBig : a.maxSmall)) / 100, st.letterSpacing);
    const fit = raw.w > wMax ? fitWord(text, font, wMax, f.H, st.letterSpacing) : raw;
    return {i, text, font, fs: fit.fs, w: fit.w, cap: capHeight(font) * fit.fs, isBig, num};
  });
  const spot = a.spots[pageIndex % a.spots.length] as string;
  if (tall) {
    // on a page with an emphasised word that word is the largest: the others never get a bigger type size
    if (page.words.some((w) => w.emphasis)) {
      for (const it of items) if (!it.isBig && it.fs > items[big].fs) { const k = items[big].fs / it.fs; it.fs *= k; it.w *= k; it.cap *= k; }
    }
    const hasEm = page.words.some((w) => w.emphasis);
    const wt = (it: (typeof items)[number]) => (it.isBig && hasEm ? a.tallEmWeight : 1);
    const hMax = f.H * a.tallStack;
    const lineGap = (c: number) => c * a.tallGap;
    const sum = (k: number) => items.reduce((t, it, i) => t + it.cap * k * wt(it) + (i ? lineGap(it.cap * k * wt(it)) : 0), 0);
    // the words are width-limited, so the stack is stretched taller (a fat display serif takes it) to own the frame,
    // then shrunk if it overshoots its height budget; the emphasised word gets extra height
    let stretch = Math.min(a.tallStretch, (f.H * a.tallMin) / sum(1));
    stretch = Math.max(1, stretch);
    if (sum(stretch) > hMax) stretch = hMax / sum(1);
    const tot = sum(stretch);
    let ty = spot === 'tl' ? f.H * 0.05 : spot === 'br' ? f.H * 0.95 - tot - items[items.length - 1].cap * stretch * 0.2 : f.H / 2 - tot / 2;
    const pop = f30(a.popFrames, f.fps);
    const tl = grid ? 0.3 * grid.beatFrames : 0;
    const bl = f.W * 0.025;
    const els = items.map((it, k) => {
      const ys = stretch * wt(it);
      const cap = it.cap * ys;
      const baseline = ty + cap;
      ty += cap + lineGap(cap);
      const edge = it.w > f.W ? -bl : f.W * 0.05;
      const x = spot === 'tl' ? edge : spot === 'br' ? f.W - edge - it.w : f.W / 2 - it.w / 2;
      let scale = 1;
      if (!it.isBig) {
        const start = Math.max(pageStart, snapToBeat(grid, frame - ws[it.i].since, tl));
        const sp = frame - start;
        if (sp < 0) return null;
        scale = mix(a.popFrom, 1, easeOutBack(clamp01(sp / pop), 1.5));
      }
      const cx = x + it.w / 2;
      const cy = baseline - cap / 2;
      return (
        <g key={it.i} transform={`translate(${cx} ${cy}) scale(${scale}) translate(${-cx} ${-cy})`}>
          <g transform={`translate(0 ${baseline}) scale(1 ${ys}) translate(0 ${-baseline})`}>
            <text x={x} y={baseline} fontFamily={`"${it.font.family}"`} fontWeight={it.font.weight} fontSize={it.fs} letterSpacing={(st.letterSpacing ?? 0) * it.fs} fill={fg} style={{fontSynthesis: 'none', whiteSpace: 'pre'}}>
              {it.text}
            </text>
          </g>
        </g>
      );
    });
    return (
      <g>
        <rect x={-2} y={-2} width={f.W + 4} height={f.H + 4} fill={bg} />
        {els}
      </g>
    );
  }
  const bigCap = items[big].cap;
  const gap = bigCap * 0.12;
  const total = items.reduce((s, it) => s + it.cap, 0) + gap * (items.length - 1);
  const mx = f.W * 0.05;
  let y = spot === 'tl' ? f.H * 0.04 : spot === 'br' ? f.H * 0.965 - total - bigCap * 0.18 : f.H / 2 - total / 2;
  const popDur = f30(a.popFrames, f.fps);
  const tol = grid ? 0.3 * grid.beatFrames : 0;

  const out = items.map((it) => {
    const baseline = y + it.cap;
    y += it.cap + gap;
    let x: number;
    if (spot === 'tl') x = it.isBig ? -f.W * 0.035 : mx;
    else if (spot === 'br') x = (it.isBig ? f.W * 1.035 : f.W - mx) - it.w;
    else x = f.W / 2 - it.w / 2;
    if (it.w <= f.W * 0.94) x = Math.min(Math.max(x, f.W * 0.03), f.W * 0.97 - it.w);
    // the big word is there with the page; a small word pops in on the beat at or before it is sung
    let scale = 1;
    if (!it.isBig) {
      const start = Math.max(pageStart, snapToBeat(grid, frame - ws[it.i].since, tol));
      const s = frame - start;
      if (s < 0) return null;
      scale = mix(a.popFrom, 1, easeOutBack(clamp01(s / popDur), 1.5));
    }
    const cx = x + it.w / 2;
    const cy = baseline - it.cap / 2;
    return (
      <g key={it.i} transform={`translate(${cx} ${cy}) scale(${scale}) translate(${-cx} ${-cy})`}>
        <text x={x} y={baseline} fontFamily={`"${it.font.family}"`} fontWeight={it.font.weight} fontSize={it.fs} letterSpacing={(st.letterSpacing ?? 0) * it.fs} fill={fg} style={{fontSynthesis: 'none', whiteSpace: 'pre'}}>
          {it.text}
        </text>
      </g>
    );
  });
  return (
    <g>
      <rect x={-2} y={-2} width={f.W + 4} height={f.H + 4} fill={bg} />
      {out}
    </g>
  );
};

export const billboard: StyleDef = {Page};
