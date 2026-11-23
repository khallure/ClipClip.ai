import React from 'react';
import {Xf, applyCase, baseFontSize, behindY, blurCss, capHeight, easeOut, fadeOut, f30, subjectIn} from '../engine';
import type {BigProps, FontSpec, StyleDef} from '../engine';
import {activeBig, bigDrawn, inFront, placeX, toF, fitTo} from './behind-kit';
import {SmallLine} from './behind-small';

// Two or three words stacked behind the speaker, every line fitted to the same width, each in its own face and weight,
// like the title block of a film poster. Letter spacing closes up as each line lands.

type LineSpec = {font: FontSpec; case: 'upper' | 'lower' | 'sentence'; track: number; color?: string};

const Big: React.FC<BigProps> = ({st, f, big, frame, captions, layer}) => {
  const i = activeBig(big, frame, f.fps);
  if (i < 0) return null;
  const b = big[i];
  if (!bigDrawn(b, st, layer)) return null;
  const bh = st.behind!;
  const a = st.anim;
  const words = b.text.split(/\s+/).filter(Boolean).slice(0, 3);
  const specs: LineSpec[] = (a.lines as LineSpec[]) ?? [];
  const spec = (k: number): LineSpec => specs[(words.length === 1 ? 1 : k) % specs.length];
  const width = (f.W * (bh.bigWidth ?? 74)) / 100;
  const maxFs = baseFontSize(bh.bigMaxSize ?? 20, st, f);
  const gap = f.H * (a.gap ?? 0.012);
  // one size per line so every line spans the same width (a short word is tall, a long one small)
  const lines = words.map((w, k) => {
    const sp = spec(k);
    const text = applyCase(w, sp.case);
    const fit = fitTo(text, sp.font, width, maxFs * (k === 1 ? 1 : 0.8), sp.track);
    return {text, sp, ...fit};
  });
  const total = lines.reduce((s, l) => s + l.cap, 0) + gap * (lines.length - 1);
  const sub = subjectIn(captions, b.startMs, b.endMs);
  const front = inFront(b, layer);
  const yPct = front ? (bh as any).frontY ?? 60 : behindY({startMs: 0, endMs: 0, bands: sub.bands}, ((total * 1.05) / f.H) * 100, bh.bigY ?? 18, (bh as any).target ?? 0.28);
  const widest = Math.max(...lines.map((l) => l.w));
  const left = front ? (f.W - widest) / 2 : placeX(sub, f.W, widest, (bh as any).overlap ?? 0.3);
  const cx = left + widest / 2;
  const s = toF(b.startMs, f.fps);
  const e = Math.max(toF(b.endMs, f.fps), s + 1);
  const out = fadeOut(e - frame, f30(a.bigOut, f.fps));
  let y = (f.H * yPct) / 100 - total / 2;
  return (
    <Xf opacity={out * (bh.bigOpacity ?? 1)}>
      {lines.map((l, k) => {
        const t = frame - s - k * f30(a.stagger, f.fps) + 1;
        const p = easeOut(t / (f30(a.lineIn, f.fps) + 1));
        const baseline = y + l.cap;
        y += l.cap + gap;
        const track = l.sp.track + (a.trackFrom ?? 0.25) * (1 - p);
        return (
          <Xf key={k} opacity={p} filter={blurCss(f, (a.blur ?? 10) * (1 - p))}>
            <text
              x={cx}
              y={baseline + (1 - p) * l.cap * 0.35}
              textAnchor="middle"
              fontFamily={`"${l.sp.font.family}"`}
              fontWeight={l.sp.font.weight}
              fontSize={l.fs}
              letterSpacing={track * l.fs}
              fill={l.sp.color ?? bh.bigColor ?? st.color}
              style={{fontSynthesis: 'none', whiteSpace: 'pre'}}
            >
              {l.text}
            </text>
          </Xf>
        );
      })}
    </Xf>
  );
};

export const poster: StyleDef = {Page: SmallLine as any, Big};
