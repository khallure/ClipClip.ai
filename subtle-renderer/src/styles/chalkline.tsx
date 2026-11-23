import React from 'react';
import {applyCase, clamp01, emphasisOf, f30, fontWithGlyphs, px, spaceWidth, textWidth} from '../engine';
import type {FontSpec, PageProps, StyleDef} from '../engine';

// Hand-written lines on a chalk-dark (or marker-on-paper) field. A page is a block of up to four lyric lines that
// piles up from the top-left, each line indented a step further, then the page clears.
// Write-on: a font outline cannot be stroked along its centre line, so each word is revealed left to right through a
// mask with a soft, irregular edge, in 2-6 discrete steps over the word's own timing. Everything is redrawn with a tiny
// seeded jitter every `boilFps` (default 12) so the lines "boil" like hand-drawn animation: a turbulence displacement
// seeded by the boil tick, a small per-word shift and tilt, and (chalk) a grain. All of it is a function of the frame.
// The emphasised word is bigger, upper-case, and gets a real SVG path underline drawn with stroke-dashoffset.

/** Deterministic noise in [-1, 1] from two integers. */
const rnd = (a: number, b = 0) => {
  let h = (Math.imul(a + 1, 374761393) + Math.imul(b + 7, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return (((h ^ (h >>> 16)) >>> 0) % 20001) / 10000 - 1;
};

const Page: React.FC<PageProps> = ({st, f, page, ws, frame, since, left, pageIndex}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const ink = (li: number) => (a.inks ? a.inks[(pageIndex + li) % a.inks.length] : st.color);
  const boilLen = Math.max(1, f.fps / (a.boilFps ?? 12));
  const tick = Math.floor(frame / boilLen);
  const tickFrame = Math.floor(tick * boilLen);
  const unit = px(f, 1);

  // ---- layout: words flow left to right inside a line, lines flow down from the top-left, each indented a step
  const marginX = (f.W * st.position.marginX) / 100;
  const lineIds = [...new Set(page.words.map((w) => w.line ?? 0))].sort((x, y) => x - y);
  // a grouped line longer than the preset's character budget (plain speech has long lines) wraps onto more lines
  const chars = st.page as {maxCharsPerLine?: number; maxCharsPerLineWide?: number};
  const budget = ((f.wide ? chars.maxCharsPerLineWide : undefined) ?? chars.maxCharsPerLine ?? 18) * 1.3;
  const vlines: number[][] = [];
  for (const id of lineIds) {
    let cur: number[] = [];
    let len = 0;
    page.words.forEach((w, i) => {
      if ((w.line ?? 0) !== id) return;
      if (cur.length && len + w.text.length + 1 > budget) {
        vlines.push(cur);
        cur = [];
        len = 0;
      }
      cur.push(i);
      len += w.text.length + 1;
    });
    if (cur.length) vlines.push(cur);
  }
  const texts = page.words.map((w) => (w.emphasis && e.caps ? w.text.toUpperCase() : applyCase(w.text, st.case)));
  const fonts: FontSpec[] = page.words.map((w, i) => fontWithGlyphs(w.emphasis && st.emphasisFont ? st.emphasisFont : st.font, texts[i]));
  const mult = page.words.map((w) => (w.emphasis ? e.scale ?? 1.6 : 1));
  const indentPx = (k: number) => (f.W * (a.indent[k % a.indent.length] ?? 0)) / 100;
  const measure = (fs: number) => {
    const sp = spaceWidth(st.font, fs);
    const widths = texts.map((t, i) => textWidth(t, fonts[i], fs * mult[i]));
    const need = vlines.map((idx, k) => {
      return {idx, w: idx.reduce((s, i) => s + widths[i], 0) + sp * (idx.length - 1) + indentPx(k)};
    });
    return {sp, widths, need};
  };
  let fs = (st.size / 100) * f.H * (f.wide ? st.wideScale ?? 1 : 1);
  let m = measure(fs);
  const avail = f.W - 2 * marginX;
  const widthScale = Math.min(...m.need.map(({w}, k) => (avail - indentPx(k)) / (w - indentPx(k))));
  const heightOf = (idx: number[]) => Math.max(st.lineHeight ?? 1.5, Math.max(...idx.map((i) => mult[i])) * 1.25);
  const heightScale = (f.H * (0.92 - st.position.y / 100)) / (fs * vlines.reduce((n, idx) => n + heightOf(idx), 0));
  fs *= Math.min(1, widthScale, heightScale);
  m = measure(fs);
  const pos: {x: number; baseline: number; fs: number; li: number}[] = [];
  let top = (f.H * st.position.y) / 100;
  vlines.forEach((_, k) => {
    const idx = m.need[k].idx;
    const lh = fs * heightOf(idx);
    const baseline = top + lh * 0.72;
    let x = marginX + indentPx(k);
    idx.forEach((i) => {
      pos[i] = {x, baseline, fs: fs * mult[i], li: k};
      x += m.widths[i] + m.sp;
    });
    top += lh;
  });

  // ---- write-on progress per word, in discrete steps
  const seed = pageIndex * 131;
  const progress = (i: number) => {
    const w = ws[i];
    const dur = Math.max(3, w.endF - w.startF);
    const steps = Math.min(a.maxSteps, Math.max(a.minSteps, Math.round(dur / a.stepFrames)));
    const stepLen = dur / steps;
    const s = tickFrame - w.startF; // steps happen on boil ticks too, so everything moves at the hand-drawn rate
    if (frame < w.startF) return 0;
    return Math.min(1, (Math.floor(Math.max(0, s) / stepLen) + 1) / steps);
  };

  const words = page.words.map((w, i) => {
    const p = progress(i);
    if (p <= 0) return null;
    const {x, baseline, fs: wfs, li} = pos[i];
    const wW = m.widths[i];
    const top = baseline - wfs * 1.05;
    const bot = baseline + wfs * 0.45;
    const jx = rnd(seed + i, tick) * unit * 1.1;
    const jy = rnd(seed + i, tick + 977) * unit * 1.1;
    const rot = rnd(seed + i, tick + 31) * 0.9;
    const maskId = `cl-${pageIndex}-${i}`;
    const pad = wfs * 0.18;
    const edge = x - pad + p * (wW + 2 * pad);
    const N = 7;
    const pts = (extra: number) => {
      const up = [`${x - pad * 2},${top}`];
      for (let j = 0; j <= N; j++) up.push(`${edge + extra + rnd(seed + i * 7 + j, tick) * wfs * 0.09 * (p < 1 ? 1 : 0)},${top + ((bot - top) * j) / N}`);
      up.push(`${x - pad * 2},${bot}`);
      return up.join(' ');
    };
    const full = p >= 1;
    const cxw = x + wW / 2;
    const underline = e.underline && w.emphasis;
    const uStart = ws[i].endF - 1;
    const uq = underline ? clamp01((tickFrame - uStart) / f30(a.underlineFrames, f.fps)) : 0;
    const uSteps = a.underlineSteps ?? 3;
    const uP = uq <= 0 ? 0 : Math.ceil(uq * uSteps) / uSteps;
    const uy = baseline + wfs * 0.2;
    const j = (k: number) => rnd(seed + i * 3 + k, tick) * wfs * 0.035;
    const path = `M ${x - wfs * 0.04} ${uy + j(0)} C ${x + wW * 0.3} ${uy - wfs * 0.07 + j(1)}, ${x + wW * 0.62} ${uy + wfs * 0.06 + j(2)}, ${x + wW + wfs * 0.05} ${uy - wfs * 0.03 + j(3)}`;
    return (
      <g key={i}>
        {!full ? (
          <mask id={maskId} maskUnits="userSpaceOnUse" x={x - wfs} y={top - wfs * 0.3} width={wW + wfs * 2} height={bot - top + wfs * 0.6}>
            <polygon points={pts(wfs * 0.14)} fill="#fff" fillOpacity={0.22} />
            <polygon points={pts(wfs * 0.07)} fill="#fff" fillOpacity={0.5} />
            <polygon points={pts(0)} fill="#fff" />
          </mask>
        ) : null}
        <g transform={`translate(${jx} ${jy}) rotate(${rot} ${cxw} ${baseline})`} mask={full ? undefined : `url(#${maskId})`}>
          <text x={x} y={baseline} fontFamily={`"${fonts[i].family}"`} fontWeight={fonts[i].weight} fontSize={wfs} fill={ink(li)} style={{fontSynthesis: 'none', whiteSpace: 'pre'}}>
            {texts[i]}
          </text>
        </g>
        {uP > 0 ? (
          <g transform={`translate(${jx} ${jy})`}>
            <path d={path} pathLength={1} fill="none" stroke={ink(li)} strokeWidth={Math.max(1.5, wfs * 0.06)} strokeLinecap="round" strokeDasharray={1} strokeDashoffset={1 - uP} />
          </g>
        ) : null}
      </g>
    );
  });

  const fo = clamp01(left / f30(a.clearFrames, f.fps));
  const filt = `cl-boil-${pageIndex}`;
  const wob = (a.wobble ?? 3) * unit;
  return (
    <g>
      <rect x={-2} y={-2} width={f.W + 4} height={f.H + 4} fill={a.bg} />
      {a.vignette ? (
        <>
          <defs>
            <radialGradient id="cl-vig" gradientUnits="userSpaceOnUse" cx={f.W / 2} cy={f.H / 2} r={Math.hypot(f.W, f.H) / 2}>
              <stop offset="0.55" stopColor="#000" stopOpacity="0" />
              <stop offset="1" stopColor="#000" stopOpacity={a.vignette} />
            </radialGradient>
          </defs>
          <rect x={0} y={0} width={f.W} height={f.H} fill="url(#cl-vig)" />
        </>
      ) : null}
      <defs>
        <filter id={filt} x="-3%" y="-3%" width="106%" height="106%" colorInterpolationFilters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.014" numOctaves="2" seed={(tick % 997) + 1} result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale={wob} xChannelSelector="R" yChannelSelector="G" result="d" />
          {a.grain ? (
            <>
              <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="1" seed={(tick % 313) + 3} result="g" />
              <feColorMatrix in="g" type="matrix" values={`0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 ${-2.2 * a.grain} ${1 + 0.9 * a.grain}`} result="ga" />
              <feComposite in="d" in2="ga" operator="in" />
            </>
          ) : null}
        </filter>
      </defs>
      <g opacity={fo} style={{filter: `url(#${filt})`}}>
        {words}
      </g>
    </g>
  );
};

export const chalkline: StyleDef = {
  spec: (page, st) => {
    const e = emphasisOf(st);
    return page.words.map((w) => (w.emphasis ? {size: e.scale ?? 1.6} : {}));
  },
  Page,
};
