import React from 'react';
import {barIndex, beatGrid, capHeight, easeOut, easeOutBack, f30, fitWord, fontWithGlyphs, isEmph, textWidth, applyCase, px, clamp01} from '../engine';
import type {CWord, FontSpec, PageProps, StyleDef} from '../engine';

// Flat colour fields, full-frame bold caps, a different bold sans on every line. Pages begin on the beat (the grouping
// snaps them); the lines of a page enter together with it, two frames apart, each over a few frames with overshoot, then hold. The field flips colour every `fieldBars` bars (every `fieldPages`
// pages when the audio has no beats). A page with an emphasised word goes onto the accent field and gives the word its
// own, largest line. Pages end with a hard cut.

type Line = {idx: number[]; em: boolean};

/** Linear interpolation through evenly spaced keyframes, t in 0..1. */
const keys = (t: number, v: number[]) => {
  const x = clamp01(t) * (v.length - 1);
  const i = Math.min(v.length - 2, Math.floor(x));
  return v[i] + (v[i + 1] - v[i]) * (x - i);
};

/** Words of a page stacked into up to 3 lines, one word each (an emphasised word is always alone; extra short words join a neighbour). */
function buildLines(words: CWord[]): Line[] {
  const short = (i: number) => words[i].text.replace(/[^A-Za-z0-9]/g, '').length <= 3;
  const lines: Line[] = words.map((w, i) => ({idx: [i], em: !!w.emphasis}));
  while (lines.length > 3) {
    // too many lines: join a short word to the word beside it
    let k = lines.findIndex((l, j) => j < lines.length - 1 && !l.em && !lines[j + 1].em && (short(l.idx[0]) || short(lines[j + 1].idx[0])));
    if (k < 0) k = lines.findIndex((l, j) => j < lines.length - 1 && !l.em && !lines[j + 1].em);
    if (k < 0) k = 0;
    lines.splice(k, 2, {idx: [...lines[k].idx, ...lines[k + 1].idx], em: lines[k].em || lines[k + 1].em});
  }
  return lines;
}

const Page: React.FC<PageProps> = ({st, f, page, ws, frame, since, pageIndex, captions}) => {
  const a = st.anim;
  const e = (st.emphasis ?? {}) as Record<string, number>;
  const fonts: FontSpec[] = (st as any).lineFonts ?? [st.font];
  const grid = beatGrid(captions, f.fps);
  const pageStart = frame - since;
  const hasEm = page.words.some((_, i) => isEmph(page, i));

  // ---- the field
  const fields = a.fields as {bg: string; fg: string}[];
  const phrase = grid ? Math.floor(barIndex(grid, pageStart) / a.fieldBars) : Math.floor(pageIndex / a.fieldPages);
  const field = hasEm ? fields[a.accentField] : fields[phrase % fields.length];

  // ---- geometry
  const lines = buildLines(page.words);
  const tall = f.H > f.W * 1.2; // vertical frame: poster-sized lines, tight gaps
  const marginW = tall ? f.W * (a.maxWidthTall ?? 0.95) : f.W * (1 - (2 * st.position.marginX) / 100);
  const geo = lines.map((ln, li) => {
    const text = applyCase(ln.idx.map((i) => page.words[i].text).join(' '), st.case);
    // an emphasised line on a tall frame takes a condensed face so a long word can stand tall
    const cond = (a.condensedTall as number[] | undefined) ?? [];
    const face = ln.em && tall && cond.length ? fonts[cond[(pageIndex + li) % cond.length]] : fonts[(pageIndex + li) % fonts.length];
    const font = fontWithGlyphs(face, text);
    const connector = !ln.em && lines.length > 1 && ln.idx.length === 1 && text.replace(/[^A-Z0-9]/gi, '').length <= 3;
    const fills = tall && a.fillsTall ? (a.fillsTall as number[]) : (a.fills as number[]);
    const fill = ln.em ? e.fill ?? 0.92 : connector ? (tall ? a.connectorFillTall ?? 0.34 : 0.26) : lines.length === 1 ? (tall ? 0.9 : 0.8) : fills[li % fills.length];
    const maxSize = tall ? a.maxSizeTall ?? a.maxSize : a.maxSize;
    const maxFs = (f.H * (ln.em ? (tall ? e.maxSizeTall ?? 34 : e.maxSize ?? 21) : connector ? (tall ? a.connectorSizeTall ?? a.connectorSize : a.connectorSize) : maxSize)) / 100;
    const fit = fitWord(text, font, Math.min(marginW, f.W * fill), maxFs, st.letterSpacing);
    return {ln, text, font, fs: fit.fs, w: fit.w, cap: capHeight(font) * fit.fs, connector};
  });
  if (tall && hasEm) {
    const headlineCap = Math.max(...geo.filter((line) => line.ln.em).map((line) => line.cap));
    for (const line of geo) {
      if (line.ln.em || line.cap <= headlineCap * 0.88) continue;
      const scale = headlineCap * 0.88 / line.cap;
      line.fs *= scale;
      line.w *= scale;
      line.cap *= scale;
    }
  }
  const gapOf = (k: number) => Math.max(geo[k].cap, geo[k + 1]?.cap ?? 0) * (geo[k].connector || geo[k + 1]?.connector ? 0.16 : 0.14) * (tall ? a.gapTall ?? 0.35 : 1);
  let total = geo.reduce((s, g, k) => s + g.cap + (k < geo.length - 1 ? gapOf(k) : 0), 0);
  const maxH = f.H * (tall ? a.maxStackTall ?? 0.72 : 0.84);
  const sc = total > maxH ? maxH / total : 1;
  total *= sc;
  // tall frame: long words are width-limited, so stretch the full-size lines taller (condensed caps take it) until the stack owns the frame
  let sy = 1;
  if (tall) {
    const fixed = geo.reduce((t, g, k) => t + (g.connector ? g.cap * sc : 0) + (k < geo.length - 1 ? gapOf(k) * sc : 0), 0);
    const flex = geo.reduce((t, g) => t + (g.connector ? 0 : g.cap * sc), 0);
    sy = Math.max(1, Math.min(a.maxStretchTall ?? 1.7, (f.H * (a.minStackTall ?? 0.55) - fixed) / Math.max(1, flex)));
    total = fixed + flex * sy;
  }
  const left = pageIndex % 3 === 2 && geo.length > 1; // every third page: a left-aligned block
  const blockW = Math.max(...geo.map((g) => g.w * sc));
  let y = f.H * 0.5 - total / 2;
  const dur = f30(a.inFrames, f.fps);
  const els = geo.map((g, li) => {
    const fs = g.fs * sc;
    const w = g.w * sc;
    const ys = g.connector ? 1 : sy;
    const cap = g.cap * sc * ys;
    const baseline = y + cap;
    y += cap + (li < geo.length - 1 ? gapOf(li) * sc : 0);
    const x = left ? f.W / 2 - blockW / 2 : f.W / 2 - w / 2;
    // every line of the page enters with the page, on its beat, a couple of frames apart
    const s = frame - (pageStart + li * f30(a.stagger, f.fps));
    if (s < 0) return null;
    const t = clamp01(s / dur);
    const o = easeOutBack(t, 2.2);
    const move = a.moves[(pageIndex + li) % a.moves.length] as string;
    const cx = x + w / 2;
    const cy = baseline - cap / 2;
    let transform = '';
    let clip: React.ReactNode = null;
    if (move === 'spin') {
      // wobble-spin: a quarter turn, back past upright, then settle (keys at thirds of the entry)
      const rot = keys(t, [90, -30, 12, 0]);
      const sz = keys(t, [1.3, 1.18, 1.05, 1]);
      transform = `translate(${cx} ${cy}) rotate(${rot}) scale(${sz}) translate(${-cx} ${-cy})`;
    }
    else if (move === 'up') transform = `translate(0 ${cap * 1.6 * (1 - o)})`;
    else if (move === 'down') transform = `translate(0 ${-cap * 1.6 * (1 - o)})`;
    else if (move === 'left') transform = `translate(${-f.W * 0.4 * (1 - o)} 0)`;
    else if (move === 'wipe') {
      const id = `db-${pageIndex}-${li}`;
      clip = (
        <clipPath id={id}>
          <rect x={x - fs * 0.1} y={baseline - cap * 1.3} width={(w + fs * 0.2) * easeOut(t)} height={cap * 1.6} />
        </clipPath>
      );
      transform = '';
      return (
        <g key={li} clipPath={`url(#${id})`}>
          {clip}
          <g transform={`translate(0 ${baseline}) scale(1 ${ys}) translate(0 ${-baseline})`}>
            <text x={x} y={baseline} fontFamily={`"${g.font.family}"`} fontWeight={g.font.weight} fontSize={fs} letterSpacing={(st.letterSpacing ?? 0) * fs} fill={field.fg} style={{fontSynthesis: 'none', whiteSpace: 'pre'}}>
              {g.text}
            </text>
          </g>
        </g>
      );
    }
    return (
      <g key={li} transform={transform}>
        <g transform={`translate(0 ${baseline}) scale(1 ${ys}) translate(0 ${-baseline})`}>
          <text x={x} y={baseline} fontFamily={`"${g.font.family}"`} fontWeight={g.font.weight} fontSize={fs} letterSpacing={(st.letterSpacing ?? 0) * fs} fill={field.fg} style={{fontSynthesis: 'none', whiteSpace: 'pre'}}>
            {g.text}
          </text>
        </g>
      </g>
    );
  });
  void px;
  void textWidth;
  return (
    <g>
      <rect x={-2} y={-2} width={f.W + 4} height={f.H + 4} fill={field.bg} />
      {els}
    </g>
  );
};

export const downbeat: StyleDef = {Page};
