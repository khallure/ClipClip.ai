import React from 'react';
import {Word, Xf, blurCss, capHeight, clamp01, dropShadow, easeInOut, fadeIn, fadeOut, f30, joinFilters, mix} from '../engine';
import type {PageProps, StyleDef} from '../engine';

// The same serif italic in one accent colour. As each word arrives a hard-edged diagonal band of light
// travels across it, turning the letters white where it passes; a soft white glow then flashes and fades.

const Page: React.FC<PageProps> = ({st, f, page, pageIndex, L, ws, left}) => {
  const a = st.anim;
  const sweep = f30(a.sweep, f.fps);
  const glowN = f30(a.glow, f.fps);
  const out = fadeOut(left, f30(a.pageOut, f.fps));
  return (
    <Xf opacity={out} filter={joinFilters(dropShadow(f, 0, 2, a.shadowBlur, 'rgba(0,0,0,0.78)'), dropShadow(f, 0, 1, a.shadowBlur * 0.3, 'rgba(0,0,0,0.7)'))}>
      {L.words.map((box, i) => {
        const since = ws[i].since - f30(a.delay, f.fps);
        const base = fadeIn(ws[i].since, f30(a.wordIn, f.fps)).opacity;
        const id = `sh-${pageIndex}-${i}`;
        const y0 = box.baseline - capHeight(box.font) * box.fs * 1.2;
        const y1 = box.baseline + box.fs * 0.3;
        const d = y1 - y0; // 45 degrees
        const bw = box.fs * a.band;
        const p = clamp01((since + 1) / (sweep + 1));
        const bx = mix(box.left - bw - d, box.left + box.w, easeInOut(p));
        const sweeping = since >= 0 && p < 1;
        const g = since >= sweep ? clamp01(1 - (since - sweep) / glowN) : since >= 0 ? 0 : 0;
        const key = page.words[i].emphasis ? 1.35 : 1;
        const glow = g > 0.02 ? dropShadow(f, 0, 0, a.glowBlur, `rgba(255,255,255,${(0.75 * g * key).toFixed(3)})`) : undefined;
        return (
          <g key={i} opacity={base}>
            <Word box={box} fill={st.color} filter={glow} />
            {sweeping ? (
              <>
                <defs>
                  <mask id={id} maskUnits="userSpaceOnUse" x={box.left - box.fs} y={y0} width={box.w + 2 * box.fs} height={d}>
                    <rect x={box.left - box.fs} y={y0} width={box.w + 2 * box.fs} height={d} fill="#000" />
                    <polygon points={`${bx},${y0} ${bx + bw},${y0} ${bx + bw + d},${y1} ${bx + d},${y1}`} fill="#fff" style={{filter: blurCss(f, 0.8)}} />
                  </mask>
                </defs>
                <Word box={box} fill={a.lightColor} mask={`url(#${id})`} />
              </>
            ) : null}
          </g>
        );
      })}
    </Xf>
  );
};

export const shine: StyleDef = {Page};
