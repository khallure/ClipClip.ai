import React from 'react';
import {Xf, emphasisOf, baseFontSize, capHeight, clamp01, easeOut, f30, fadeOut, fitWord, mix, px, styleShadow, textWidth} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// A typed title whose letter spacing collapses as the letters arrive, and a small handwritten note under it.
// The title is the first half of the page's words, the note is the rest.

const split = (n: number) => (n <= 1 ? n : Math.ceil(n / 2));
/** A small hand-drawn curved arrow pointing down and right, about `s` px tall. */
const arrow = (x: number, y: number, s: number) =>
  `M ${x} ${y} C ${x - s * 0.1} ${y + s * 0.7}, ${x + s * 0.3} ${y + s * 1.2}, ${x + s * 1.0} ${y + s * 1.25} M ${x + s * 0.72} ${y + s * 1.0} L ${x + s * 1.02} ${y + s * 1.25} L ${x + s * 0.7} ${y + s * 1.45}`;

const Page: React.FC<PageProps> = ({st, f, page, since, left}) => {
  const a = st.anim;
  const k = split(page.words.length);
  const e = emphasisOf(st);
  const title = page.words.slice(0, k).map((w) => w.text).join(' ');
  const note = page.words.slice(k).map((w) => w.text.toLowerCase()).join(' ');
  const accent = e.color ?? st.color;
  // which characters of the title belong to an emphasised word
  const titleEm: boolean[] = page.words.slice(0, k).flatMap((w, wi) => [...(wi ? [false] : []), ...[...w.text].map(() => !!w.emphasis)]);
  const avail = f.W * (1 - (2 * st.position.marginX) / 100);
  const tf = fitWord(title, st.font, avail, baseFontSize(st.size, st, f));
  const nFont = st.noteFont!;
  const nfs = Math.min(baseFontSize(a.noteSize, st, f), tf.fs * 0.8);
  const left0 = (f.W * (st.position.x ?? 50)) / 100 - tf.w / 2;
  const baseline = (f.H * st.position.y) / 100 + (capHeight(st.font) * tf.fs) / 2;
  const frames = left + since;
  const tDur = Math.min(Math.max(8, 0.34 * frames), f30(a.typeMax, f.fps));
  const ls = mix(a.trackFrom, 0, easeOut(since / (tDur + f30(6, f.fps)))) * tf.fs;
  const fadeF = f30(2, f.fps);
  const chars = [...title];
  const items = chars.map((ch, j) => ({
    ch,
    x: left0 + textWidth(title.slice(0, j), st.font, tf.fs, 0) + j * ls,
    p: clamp01((since - (j * tDur) / chars.length + 1) / (fadeF + 1)),
  }));

  // the note, wrapped into at most two lines
  const nStart = tDur * 0.75 + f30(2, f.fps);
  const nDur = Math.min(Math.max(8, 0.4 * (frames - nStart)), f30(a.noteTypeMax, f.fps));
  const lines: string[] = [];
  const lineEm: boolean[][] = []; // per character: part of an emphasised word
  if (note) {
    const maxW = Math.min(avail - nfs * 1.8, Math.max(tf.w * 1.05, f.W * 0.4));
    let cur = '';
    let curEm: boolean[] = [];
    page.words.slice(k).forEach((pw) => {
      const w = pw.text.toLowerCase();
      const test = cur ? cur + ' ' + w : w;
      if (cur && textWidth(test, nFont, nfs, 0) > maxW) {
        lines.push(cur);
        lineEm.push(curEm);
        cur = w;
        curEm = [];
      } else {
        cur = test;
        if (curEm.length) curEm.push(false);
      }
      curEm.push(...[...w].map(() => !!pw.emphasis));
    });
    lines.push(cur);
    lineEm.push(curEm);
  }
  const nLeft = left0 + nfs * 1.7;
  const nBase = baseline + tf.fs * 0.55 + nfs * 1.1;
  const typed = Math.round(clamp01((since - nStart) / nDur) * lines.join('').length);
  const draw = clamp01((since - nStart + 1) / nDur);
  return (
    <Xf opacity={fadeOut(left, f30(a.pageOut, f.fps))} filter={styleShadow(st, f)}>
      {items.map((it, j) =>
        it.p > 0 && it.ch !== ' ' ? (
          <text key={j} x={it.x + px(f, a.slideX) * (1 - easeOut(it.p))} y={baseline} opacity={it.p} fill={titleEm[j] ? accent : st.color} fontFamily={`"${st.font.family}"`} fontWeight={st.font.weight} fontSize={tf.fs} style={{whiteSpace: 'pre', fontSynthesis: 'none'}}>
            {it.ch}
          </text>
        ) : null,
      )}
      {lines.length ? (
        <>
          <path
            d={arrow(left0 + nfs * 0.1, baseline + tf.fs * 0.5, nfs)}
            fill="none"
            stroke={st.activeColor}
            strokeWidth={Math.max(1.5, nfs * 0.07)}
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength={1}
            strokeDasharray={1}
            strokeDashoffset={1 - draw}
            opacity={draw > 0 ? 1 : 0}
          />
          {lines.map((ln, li) => {
            const n = Math.max(0, Math.min(ln.length, typed - lines.slice(0, li).join('').length));
            if (n <= 0) return null;
            // runs of the same colour, so an emphasised word keeps its accent while the line is typed
            const runs: {t: string; em: boolean}[] = [];
            for (let c = 0; c < n; c++) {
              const em = !!lineEm[li][c];
              if (runs.length && runs[runs.length - 1].em === em) runs[runs.length - 1].t += ln[c];
              else runs.push({t: ln[c], em});
            }
            return (
              <text key={li} x={nLeft} y={nBase + li * nfs * 1.05} fill={st.activeColor} fontFamily={`"${nFont.family}"`} fontWeight={nFont.weight} fontSize={nfs} style={{whiteSpace: 'pre', fontSynthesis: 'none'}}>
                {runs.map((r, ri) => (
                  <tspan key={ri} fill={r.em ? accent : st.activeColor}>
                    {r.t}
                  </tspan>
                ))}
              </text>
            );
          })}
        </>
      ) : null}
    </Xf>
  );
};

export const title: StyleDef = {Page, minShow: 20};
