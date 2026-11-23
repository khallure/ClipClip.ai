import React from 'react';
import {Word, applyCase, baseFontSize, capHeight, emphasisOf, fontWithGlyphs, spaceWidth, textWidth} from '../engine';
import type {PageProps, StyleDef, WordBox} from '../engine';

// Rounded bubbles, one per line, each new line offset to the right of the one above and stacked like typed messages.
// The whole stack tilts a little more on every page. Hard cuts: a bubble appears when its first word starts.
// Words not yet spoken are dark, spoken words are white. A line holding an emphasised word gets a brighter bubble and a larger word.
// Lines are broken here (2 to 3 words each, up to 3 lines), so a page of 6 to 8 words stacks three bubbles.

const TILT = [0.4, 1.3, 2.2, 3];

/** Greedy line breaks by character budget, at most `maxLines` lines; the last line takes whatever is left. */
function breakWords(texts: string[], budget: number, maxLines: number): number[][] {
  const lines: number[][] = [[]];
  let len = 0;
  texts.forEach((t, i) => {
    const cur = lines[lines.length - 1];
    const next = len + (cur.length ? 1 : 0) + t.length;
    if (cur.length && next > budget && lines.length < maxLines) {
      lines.push([i]);
      len = t.length;
    } else {
      cur.push(i);
      len = next;
    }
  });
  // do not leave a single short word alone on the last line when the line above can give one up
  const n = lines.length;
  if (n > 1 && lines[n - 1].length === 1 && lines[n - 2].length > 2) lines[n - 1].unshift(lines[n - 2].pop()!);
  return lines;
}

type Line = {idx: number[]; boxes: WordBox[]; w: number};

function layoutLines(page: PageProps['page'], st: PageProps['st'], f: PageProps['f']) {
  const c = st.container!;
  const e = emphasisOf(st);
  const texts = page.words.map((w) => applyCase(w.text, st.case));
  const mult = page.words.map((w) => (w.emphasis ? e.scale ?? 1.2 : 1));
  const budget = ((f.wide ? (st.page as any).maxCharsPerLineWide : (st.page as any).maxCharsPerLine) as number | undefined) ?? 14;
  const groups = breakWords(texts, budget, Math.min(3, st.page.maxLines ?? 3));
  const fonts = texts.map((t) => fontWithGlyphs(st.font, t));
  const avail = f.W * (1 - (2 * st.position.marginX) / 100);
  let fs = baseFontSize(st.size, st, f);
  const measure = (size: number) =>
    groups.map((g) => {
      const ws = g.map((i) => textWidth(texts[i], fonts[i], size * mult[i], st.letterSpacing ?? 0));
      const sp = spaceWidth(st.font, size, st.letterSpacing ?? 0);
      return {ws, sp, w: ws.reduce((a, b) => a + b, 0) + sp * (g.length - 1)};
    });
  let m = measure(fs);
  const widest = (mm: typeof m) => Math.max(...mm.map((x) => x.w)) + 2 * c.padX * fs;
  if (widest(m) > avail) {
    fs *= (avail / widest(m)) * 0.99;
    m = measure(fs);
  }
  const lh = fs * (st.lineHeight ?? 1.3);
  const cap = capHeight(st.font) * fs;
  const cy = (f.H * st.position.y) / 100;
  const top = cy - (lh * groups.length) / 2;
  const lines: Line[] = groups.map((g, k) => {
    let x = 0;
    const boxes = g.map((i, j) => {
      const b: WordBox = {i, text: texts[i], font: fonts[i], fs: fs * mult[i], ls: st.letterSpacing ?? 0, left: x, w: m[k].ws[j], cx: x + m[k].ws[j] / 2, line: k, baseline: top + lh * (k + 0.5) + cap / 2};
      x += m[k].ws[j] + m[k].sp;
      return b;
    });
    return {idx: g, boxes, w: m[k].w};
  });
  return {lines, fs, lh, cy};
}

const Page: React.FC<PageProps> = ({st, f, page, pageIndex, ws}) => {
  const c = st.container!;
  const e = emphasisOf(st);
  const {lines, fs, lh, cy} = layoutLines(page, st, f);
  const padX = c.padX * fs;
  const h = fs * (c.height ?? 1.45);
  const r = h * c.radius;
  const mx = (f.W * st.position.marginX) / 100;
  const bw = lines.map((ln) => ln.w + 2 * padX);
  // left edge of each bubble: a quarter of the bubble above to its right, kept inside the margins
  const xs: number[] = [];
  bw.forEach((_, k) => xs.push(k === 0 ? 0 : xs[k - 1] + c.offset * bw[k - 1]));
  const hi = Math.max(...xs.map((x, k) => x + bw[k]));
  const lo = Math.min(...xs);
  const span = hi - lo;
  const x0 = Math.min(f.W - mx - span, Math.max(mx, (f.W * (st.position.x ?? 50)) / 100 - span / 2)) - lo;
  const rot = -TILT[pageIndex % TILT.length];
  const cx = x0 + (lo + hi) / 2;
  return (
    <g transform={`rotate(${rot} ${cx} ${cy})`}>
      {lines.map((ln, k) => {
        if (ws[ln.idx[0]].since < 0) return null;
        const bx = x0 + xs[k];
        const hot = ln.idx.some((i) => page.words[i].emphasis);
        const y = cy - (lh * lines.length) / 2 + lh * (k + 0.5);
        return (
          <g key={k}>
            <rect x={bx} y={y - h / 2} width={bw[k]} height={h} rx={r} fill={hot ? e.fill ?? c.fill : c.fill} />
            {ln.boxes.map((b, j) => (
              <Word key={j} box={{...b, left: b.left + bx + padX, cx: b.cx + bx + padX}} fill={ws[ln.idx[j]].since >= 0 ? st.color : st.dimColor!} />
            ))}
          </g>
        );
      })}
    </g>
  );
};

export const sticker: StyleDef = {Page};
