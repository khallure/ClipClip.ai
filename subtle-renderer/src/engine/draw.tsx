import React from 'react';
import type {Box, Frame, Layer} from './types';
import type {WordBox} from './layout';
import {clamp01, easeInOut, easeOut, easeOutBack, mix} from './ease';
import {px} from './time';

// ---------- the canvas ----------
/** Full-frame SVG. In `mask` mode the background is opaque black so shapes drawn white become a blur mask. */
export const Plate: React.FC<{f: Frame; mask?: boolean; children?: React.ReactNode}> = ({f, mask, children}) => (
  <svg width={f.W} height={f.H} viewBox={`0 0 ${f.W} ${f.H}`} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
    {mask ? <rect x={0} y={0} width={f.W} height={f.H} fill="#000" /> : null}
    {children}
  </svg>
);

/** CSS filter pieces. All lengths are px at 1080p and scale with the frame. */
export const dropShadow = (f: Frame, x: number, y: number, blur: number, color: string) =>
  `drop-shadow(${px(f, x).toFixed(2)}px ${px(f, y).toFixed(2)}px ${px(f, blur / 2).toFixed(2)}px ${color})`;
export const blurCss = (f: Frame, v: number) => (v > 0.01 ? `blur(${px(f, v).toFixed(2)}px)` : '');
export const joinFilters = (...parts: (string | undefined | false)[]) => parts.filter(Boolean).join(' ') || undefined;

/** Scale / fade / shift a group about a point. */
export const Xf: React.FC<{
  ox?: number;
  oy?: number;
  scale?: number;
  dx?: number;
  dy?: number;
  opacity?: number;
  filter?: string;
  mask?: string;
  children: React.ReactNode;
}> = ({ox = 0, oy = 0, scale = 1, dx = 0, dy = 0, opacity = 1, filter, mask, children}) => (
  <g
    opacity={opacity}
    transform={`translate(${dx} ${dy}) translate(${ox} ${oy}) scale(${scale}) translate(${-ox} ${-oy})`}
    style={{filter}}
    mask={mask}
  >
    {children}
  </g>
);

// ---------- words ----------
export type WordProps = {
  box: WordBox;
  fill: string;
  text?: string; // override (type-on slices)
  opacity?: number;
  dx?: number;
  dy?: number;
  scale?: number; // about the word's baseline-left corner
  blur?: number; // px at 1080p
  filter?: string;
  mask?: string; // url(#id) from <FeatherMask>
  italic?: boolean;
};

/** One word as SVG text, placed by its baseline. */
export const Word: React.FC<WordProps> = ({box, fill, text, opacity = 1, dx = 0, dy = 0, scale = 1, filter, mask}) => (
  <Xf ox={box.left} oy={box.baseline} scale={scale} dx={dx} dy={dy} opacity={opacity} filter={filter} mask={mask}>
    <text
      x={box.left}
      y={box.baseline}
      fontFamily={`"${box.font.family}"`}
      fontWeight={box.font.weight}
      fontSize={box.fs}
      letterSpacing={box.ls * box.fs}
      fill={fill}
      style={{fontSynthesis: 'none', textRendering: 'geometricPrecision', whiteSpace: 'pre'}}
    >
      {text ?? box.text}
    </text>
  </Xf>
);

// ---------- reveals (pure functions of frames since a start) ----------
export type Reveal = {opacity: number; dy: number; scale: number; blur: number};
const id: Reveal = {opacity: 1, dy: 0, scale: 1, blur: 0};

/** Fade in over `dur` frames; the first frame already shows a sliver so a word never lags its audio. */
export const fadeIn = (since: number, dur: number): Reveal => ({...id, opacity: since < 0 ? 0 : easeInOut((since + 1) / (dur + 1))});
/** Fade in while drifting up by `rise` px (at 1080p). */
export const riseIn = (f: Frame, since: number, dur: number, rise: number): Reveal => {
  const k = since < 0 ? 0 : easeOut((since + 1) / (dur + 1));
  return {...id, opacity: k, dy: px(f, rise) * (1 - k)};
};
/** Fade in while a blur resolves. */
export const blurIn = (f: Frame, since: number, dur: number, blur: number): Reveal => {
  const k = since < 0 ? 0 : easeOut((since + 1) / (dur + 1));
  return {...id, opacity: k, blur: blur * (1 - k)};
};
/** Grow from `from` (e.g. 0.1) to 1 about the baseline-left corner, with a slight overshoot. */
export const scaleFromBaseline = (since: number, dur: number, from = 0.1, fadeFrames = 2): Reveal => {
  if (since < 0) return {...id, opacity: 0, scale: from};
  const k = clamp01((since + 1) / (dur + 1));
  return {opacity: clamp01((since + 1) / fadeFrames), dy: 0, blur: 0, scale: mix(from, 1, easeOutBack(k, 1.6))};
};
/** Page-level fade out over the last `dur` frames (frames left until the page ends). */
export const fadeOut = (left: number, dur: number) => clamp01(left / Math.max(1, dur));

// ---------- feathered left-to-right wipe ----------
/**
 * Defines an SVG mask that reveals `box` from left to right with a soft edge.
 * p = 0 hides everything, p = 1 shows everything. Reference it as `mask={`url(#${id})`}`.
 */
export const FeatherMask: React.FC<{id: string; x: number; y: number; w: number; h: number; p: number; feather?: number}> = ({id, x, y, w, h, p, feather = 0.3}) => {
  const fw = Math.max(2, feather * w);
  const e = x + p * (w + fw);
  return (
    <defs>
      <linearGradient id={`${id}-g`} gradientUnits="userSpaceOnUse" x1={e - fw} x2={e} y1={0} y2={0}>
        <stop offset="0" stopColor="#fff" />
        <stop offset="1" stopColor="#000" />
      </linearGradient>
      <mask id={id} maskUnits="userSpaceOnUse" x={x - fw} y={y} width={w + 2 * fw} height={h}>
        <rect x={x - fw - 2} y={y} width={w + 2 * fw + 4} height={h} fill={`url(#${id}-g)`} />
      </mask>
    </defs>
  );
};

// ---------- containers ----------
export type ContainerKind = 'pill' | 'chip' | 'block';
export const radiusFor = (kind: ContainerKind, h: number, f: Frame) => (kind === 'pill' ? h / 2 : kind === 'chip' ? h * 0.28 : px(f, 4));

/**
 * A shape behind the text. In `visible` mode it draws the tint, edge highlight and shadow.
 * In `mask` mode it draws the same geometry as a solid white shape (for the ffmpeg backdrop-blur mask).
 */
export const Container: React.FC<{
  kind: ContainerKind;
  mode: 'visible' | 'mask';
  box: Box;
  f: Frame;
  radius?: number;
  fill?: string;
  highlight?: string; // top-edge highlight colour (visible only)
  shadow?: string; // css drop-shadow(...) (visible only)
  opacity?: number;
  scale?: number;
  uid?: string;
}> = ({kind, mode, box, f, radius, fill = 'rgba(255,255,255,0.2)', highlight, shadow, opacity = 1, scale = 1, uid = 'ct'}) => {
  const r = radius ?? radiusFor(kind, box.h, f);
  const cx = box.x + box.w / 2;
  const cy = box.y + box.h / 2;
  if (mode === 'mask') {
    return (
      <Xf ox={cx} oy={cy} scale={scale} opacity={opacity}>
        <rect x={box.x} y={box.y} width={box.w} height={box.h} rx={r} fill="#fff" />
      </Xf>
    );
  }
  return (
    <Xf ox={cx} oy={cy} scale={scale} opacity={opacity} filter={shadow}>
      <defs>
        <linearGradient id={`${uid}-hl`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={highlight ?? '#fff'} />
          <stop offset="0.45" stopColor={highlight ?? '#fff'} stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect x={box.x} y={box.y} width={box.w} height={box.h} rx={r} fill={fill} />
      {highlight ? <rect x={box.x + 0.5} y={box.y + 0.5} width={box.w - 1} height={box.h - 1} rx={r} fill="none" stroke={`url(#${uid}-hl)`} strokeWidth={Math.max(1, px(f, 1.2))} /> : null}
    </Xf>
  );
};

export type {Layer};

/** The preset's `shadow` ({x, y, blur} in % of frame height) as a css drop-shadow filter. */
export const styleShadow = (st: {shadow?: {x: number; y: number; blur: number; color: string} | null}, f: Frame) =>
  st.shadow ? `drop-shadow(${((st.shadow.x / 100) * f.H).toFixed(2)}px ${((st.shadow.y / 100) * f.H).toFixed(2)}px ${(((st.shadow.blur / 100) * f.H) / 2).toFixed(2)}px ${st.shadow.color})` : undefined;
