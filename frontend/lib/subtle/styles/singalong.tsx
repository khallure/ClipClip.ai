import React from 'react';
import {Word, Xf, beatGrid, clamp01, colorMix, easeInOut, easeOut, emphasisOf, f30, fadeOut, fadeIn, mix, sinceBeat, styleShadow, textWidth} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Default (`mode: wipe`): the block of up to four lines sits on a dark field. Unsung text is off-white; as each word is
// sung its letters fill with a soft colour from left to right, one letter at a time across the word's own timing.
// The block cuts in on the bar (hard cut). The line being sung gives a small scale pulse on every beat.
// `mode: lit` (variant `lit`): no field, no fill. Upcoming words are dim and each lights up as it is sung, over the picture,
// with a soft shadow; the page fades in and out.
// An emphasised word is a little bigger (room reserved in the layout), and fills with a warmer, brighter colour.

const Page: React.FC<PageProps> = ({st, f, page, L, ws, frame, since, left, pageIndex, captions}) => {
  const a = st.anim;
  const e = emphasisOf(st);
  const lit = a.mode === 'lit';
  const grid = beatGrid(captions, f.fps);
  const emScale = e.scale ?? 1.06;

  // the line being sung (else the next one to be sung)
  const act = ws.findIndex((w) => w.state === 'active');
  const next = ws.findIndex((w) => w.state === 'upcoming');
  const curWord = act >= 0 ? act : next >= 0 ? next : ws.length - 1;
  const curLine = L.words[curWord].line;
  const pulse = a.pulse ? 1 + a.pulse * Math.max(0, 1 - sinceBeat(grid, frame, frame - since) / f30(a.pulseFrames, f.fps)) : 1;

  const op = lit ? Math.min(fadeIn(since, f30(a.pageIn, f.fps)).opacity, fadeOut(left, f30(a.pageOut, f.fps))) : 1;

  const words = L.words.map((box, i) => {
    const w = ws[i];
    const em = !!page.words[i].emphasis;
    const sc = em ? 1 : 1;
    void sc;
    if (lit) {
      const k = w.since < 0 ? 0 : easeInOut((w.since + 1) / (f30(a.litIn, f.fps) + 1));
      return <Word key={i} box={box} fill={em ? e.color ?? st.color : st.color} opacity={mix(a.dim, 1, k)} />;
    }
    const sung = em ? e.color ?? st.activeColor! : st.activeColor!;
    const n = [...box.text].length;
    const lettersDone = w.state === 'spoken' ? n : w.state === 'upcoming' ? 0 : Math.min(n, Math.floor(w.progress * n) + 1);
    const id = `sa-${pageIndex}-${i}`;
    return (
      <g key={i}>
        <Word box={box} fill={st.color} />
        {lettersDone > 0 ? (
          lettersDone >= n ? (
            <Word box={box} fill={sung} />
          ) : (
            <>
              <clipPath id={id}>
                <rect x={box.left - box.fs * 0.1} y={box.baseline - box.fs * 1.1} width={textWidth([...box.text].slice(0, lettersDone).join(''), box.font, box.fs, box.ls) + box.fs * 0.1} height={box.fs * 1.6} />
              </clipPath>
              <g clipPath={`url(#${id})`}>
                <Word box={box} fill={sung} />
              </g>
            </>
          )
        ) : null}
      </g>
    );
  });

  // group the words by line so the active line can pulse about its own centre
  const byLine = new Map<number, React.ReactNode[]>();
  L.words.forEach((b, i) => byLine.set(b.line, [...(byLine.get(b.line) ?? []), words[i]]));
  return (
    <g>
      {a.field ? <rect x={-2} y={-2} width={f.W + 4} height={f.H + 4} fill={a.field} /> : null}
      <Xf opacity={op} filter={styleShadow(st, f)}>
        {[...byLine.entries()].map(([li, nodes]) => {
          const ln = L.lines[li];
          const s = li === curLine ? pulse : 1;
          return (
            <Xf key={li} ox={ln.left + ln.w / 2} oy={ln.y} scale={s}>
              {nodes}
            </Xf>
          );
        })}
      </Xf>
    </g>
  );
};

export const singalong: StyleDef = {
  spec: (page, st) => page.words.map((w) => (w.emphasis ? {size: emphasisOf(st).scale ?? 1.06} : {})),
  Page,
};
void clamp01;
void colorMix;
void easeOut;
