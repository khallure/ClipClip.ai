import React from 'react';
import {Word, baseFontSize, emphasisOf, Xf, blurCss, clamp01, colorMix, dropShadow, easeOut, easeOutBack, f30, joinFilters, mix, px} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// Each line sits in its own coloured block with depth (gradient, edge highlight, shadow).
// A new line rises in with motion blur and the lines accumulate as a stack; the stack drops out together.
// Block colour: `container.fill`. Alignment of the stack: `container.align` (right | center | left).

const Page: React.FC<PageProps> = ({st, f, page, L, ws, left, pageIndex}) => {
  const a = st.anim;
  const c = st.container!;
  const padX = c.padX * L.fs;
  const h = c.height * L.fs;
  const r = px(f, c.radius ?? 4);
  const ec = emphasisOf(st);
  const dur = f30(a.lineIn, f.fps);
  const outK = clamp01(left / f30(a.pageOut, f.fps));
  const drop = 1 - easeOut(outK);
  const mx = (f.W * st.position.marginX) / 100;
  return (
    <g>
      {L.lines.map((ln, k) => {
        const since = ws[ln.idx[0]].since;
        if (since < 0) return null;
        const bw = ln.w + 2 * padX;
        const bx = c.align === 'left' ? mx : c.align === 'center' ? f.W / 2 - bw / 2 : f.W - mx - bw;
        const shift = bx + padX - ln.left;
        const p = clamp01((since + 1) / (dur + 1));
        const e = easeOutBack(p, 1.5);
        const rise = px(f, a.rise) * (1 - e);
        const blur = a.blur * Math.pow(1 - p, 1.4);
        // the line that holds an emphasised word takes the second block colour
        const fill = ec.fill && ln.idx.some((i) => page.words[i].emphasis) ? ec.fill : c.fill;
        const top = colorMix(0.2, fill, '#ffffff');
        const bottom = colorMix(0.18, fill, '#000000');
        const id = `bl-${pageIndex}-${k}`;
        const by = ln.y - h / 2;
        return (
          <Xf
            key={k}
            dy={rise + px(f, a.exitDrop) * drop}
            opacity={Math.min(clamp01(p * 3), outK)}
            filter={joinFilters(dropShadow(f, 0, 3, 8, 'rgba(0,0,0,0.38)'), blurCss(f, blur))}
          >
            <defs>
              <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor={top} />
                <stop offset="0.55" stopColor={fill} />
                <stop offset="1" stopColor={bottom} />
              </linearGradient>
              <linearGradient id={`${id}-e`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="rgba(255,255,255,0.7)" />
                <stop offset="0.35" stopColor="rgba(255,255,255,0)" />
                <stop offset="1" stopColor="rgba(0,0,0,0.18)" />
              </linearGradient>
            </defs>
            <rect x={bx} y={by} width={bw} height={h} rx={r} fill={`url(#${id})`} />
            <rect x={bx + 0.5} y={by + 0.5} width={bw - 1} height={h - 1} rx={r} fill="none" stroke={`url(#${id}-e)`} strokeWidth={Math.max(1, px(f, 1.3))} />
            <g filter={dropShadow(f, 0, 1.4, 2, 'rgba(110,40,0,0.35)')}>
              {ln.idx.map((i) => (
                <Word key={i} box={{...L.words[i], left: L.words[i].left + shift, cx: L.words[i].cx + shift}} fill={st.color} />
              ))}
            </g>
          </Xf>
        );
      })}
    </g>
  );
};

// the blocks' side padding counts against the margins, so a long line shrinks until its block fits
export const block: StyleDef = {layout: (st, f) => ({padX: st.container!.padX * baseFontSize(st.size, st, f)}), Page};
void mix;
