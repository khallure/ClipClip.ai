import React from 'react';
import {
  Container,
  FeatherMask,
  Xf,
  applyCase,
  blockBox,
  baseFontSize,
  blurCss,
  clamp01,
  colorMix,
  easeInOut,
  easeOut,
  easeOutBack,
  easeOutExpo,
  f30,
  styleShadow,
} from '../engine';
import type {FontSpec, Frame, PageProps, Style, StyleDef, WordBox, WordSpec} from '../engine';

// The generic, data-driven caption style. Everything is read from the style JSON's `recipe` block
// (schema and defaults: docs/ARCHITECTURE.md, "Recipe styles"). Lengths are in em of the font size, frames at 30 fps.

type R = {
  unit: 'word' | 'line' | 'page';
  enter: {kind: string; frames: number; ease: string; distance: number; overshoot: number; stagger: number};
  exit: {kind: string; frames: number};
  upcoming: {opacity: number; color: string | null};
  active: {color: string | null; scale: number; plate: any; underline: any; opacity: number};
  spoken: {color: string | null; opacity: number};
  plate: any;
  stroke: {width: number; color: string} | null;
  emphasis: {color: string | null; scale: number; font: FontSpec | null; plate: any; underline: any; case: string | null};
  align: 'center' | 'left' | 'right';
  rotate: number;
  uppercaseEmphasisOnly: boolean;
};

export const recipeOf = (st: Style): R => {
  const r = st.recipe ?? {};
  return {
    unit: r.unit ?? 'word',
    enter: {kind: 'fade', frames: 6, ease: 'out-cubic', distance: 0.4, overshoot: 1.12, stagger: 0, ...r.enter},
    exit: {kind: 'cut', frames: 4, ...r.exit},
    upcoming: {opacity: 0, color: null, ...r.upcoming},
    active: {color: null, scale: 1, plate: null, underline: null, opacity: 1, ...r.active},
    spoken: {color: null, opacity: 1, ...r.spoken},
    plate: r.plate ?? null,
    stroke: r.stroke ?? null,
    emphasis: {color: null, scale: 1, font: null, plate: null, underline: null, case: null, ...r.emphasis},
    align: r.align ?? 'center',
    rotate: r.rotate ?? 0,
    uppercaseEmphasisOnly: !!r.uppercaseEmphasisOnly,
  };
};

const PLATE = {kind: 'page', color: 'rgba(0,0,0,0.6)', radius: 0.3, padX: 0.4, padY: 0.15, backdropBlur: 0};
const plateOf = (r: R) => (r.plate ? {...PLATE, ...r.plate} : null);
const shapeOf = (p: any) => ({color: 'rgba(255,255,255,0.9)', radius: 0.2, padX: 0.2, padY: 0.1, ...p});

const caseFor = (r: R, st: Style, emph: boolean): Style['case'] => {
  const c = emph && r.emphasis.case ? r.emphasis.case : r.uppercaseEmphasisOnly ? (emph ? 'upper' : 'sentence') : st.case;
  return c as Style['case'];
};

const ease = (name: string, t: number) => {
  switch (name) {
    case 'out-back':
      return easeOutBack(t, 1.5);
    case 'out-expo':
      return easeOutExpo(t);
    case 'linear':
      return clamp01(t);
    default:
      return easeOut(t);
  }
};

type Fx = {opacity: number; dx: number; dy: number; scale: number; blur: number; wipe: number | null; chars: number | null};
const IDENT: Fx = {opacity: 1, dx: 0, dy: 0, scale: 1, blur: 0, wipe: null, chars: null};

/** The entrance of one unit. c = frames since its clock started (negative: not yet). */
function enterFx(e: R['enter'], c: number, f: Frame, fs: number): Fx {
  if (c < 0) return {...IDENT, opacity: 0, wipe: 0, chars: 0};
  const dur = f30(e.frames, f.fps);
  const kr = clamp01((c + 1) / (dur + 1));
  const name = e.kind === 'typewriter' ? 'linear' : e.ease;
  const k = ease(name, kr);
  const fast = clamp01((c + 1) / Math.min(3, dur + 1)); // a quick fade so motion never pops
  const d = e.distance * fs;
  switch (e.kind) {
    case 'none':
      return IDENT;
    case 'fade':
      return {...IDENT, opacity: easeInOut(kr)};
    case 'pop': {
      const from = 0.6;
      const o = Math.max(1, e.overshoot);
      let s: number;
      if (o > 1.001) s = kr < 0.55 ? from + (o - from) * easeOut(kr / 0.55) : o + (1 - o) * easeInOut((kr - 0.55) / 0.45);
      else s = from + (1 - from) * k;
      return {...IDENT, scale: s, opacity: fast};
    }
    case 'rise':
      return {...IDENT, dy: d * (1 - k), opacity: Math.max(clamp01(k), fast * 0.5)};
    case 'drop':
      return {...IDENT, dy: -d * (1 - k), opacity: Math.max(clamp01(k), fast * 0.5)};
    case 'slide-left':
      return {...IDENT, dx: d * (1 - k), opacity: Math.max(clamp01(k), fast * 0.5)};
    case 'slide-right':
      return {...IDENT, dx: -d * (1 - k), opacity: Math.max(clamp01(k), fast * 0.5)};
    case 'wipe':
      return {...IDENT, wipe: kr < 1 ? easeInOut(kr) : 1};
    case 'blur-in':
      return {...IDENT, opacity: clamp01(k), blur: d * 0.35 * (1 - k)};
    case 'typewriter':
      return {...IDENT, chars: kr};
    case 'scale-down':
      return {...IDENT, scale: 1 + e.distance * (1 - k), opacity: Math.max(clamp01(k), fast * 0.5)};
    default:
      return IDENT;
  }
}

/** How the whole page leaves over its last frames. */
function exitFx(x: R['exit'], left: number, f: Frame, fs: number): Fx {
  const dur = f30(x.frames, f.fps);
  if (x.kind === 'cut' || left > dur) return IDENT;
  const k = clamp01(left / dur); // 1 -> 0
  const o = easeInOut(k);
  switch (x.kind) {
    case 'fade':
      return {...IDENT, opacity: o};
    case 'drop':
      return {...IDENT, opacity: o, dy: (1 - k) * fs * 0.3};
    case 'rise':
      return {...IDENT, opacity: o, dy: -(1 - k) * fs * 0.3};
    case 'blur-out':
      return {...IDENT, opacity: o, blur: (1 - k) * fs * 0.12};
    case 'scale-up':
      return {...IDENT, opacity: o, scale: 1 + (1 - k) * 0.08};
    default:
      return IDENT;
  }
}

const Page: React.FC<PageProps> = ({st, f, page, pageIndex, L, t, frame, since, left, ws, mode}) => {
  const r = recipeOf(st);
  const pl = plateOf(r);
  const fs = L.fs;
  const sameRot = r.rotate ? `rotate(${r.rotate} ${L.cx} ${L.cy})` : undefined;
  const ex = exitFx(r.exit, left, f, fs);
  const pageIn = clamp01((since + 1) / (f30(3, f.fps) + 1));
  const pageOp = ex.opacity;

  // ---- plates that sit behind the page or its lines (also the backdrop-blur mask) ----
  const plates: React.ReactNode[] = [];
  const lineClock = (li: number) => {
    const first = L.lines[li].idx[0];
    return frame - ws[first].startF;
  };
  if (pl && pl.kind === 'page') {
    const box = blockBox(L, pl.padX * fs, pl.padY * fs);
    const e = r.unit === 'page' ? enterFx(r.enter, since, f, fs) : {...IDENT, opacity: pageIn};
    plates.push(
      <Xf key="pp" ox={L.cx} oy={L.cy} scale={e.scale * ex.scale} dx={e.dx} dy={e.dy + ex.dy} opacity={Math.min(1, e.opacity) * pageOp}>
        <Container kind="chip" mode={mode} box={box} f={f} radius={Math.min(pl.radius * fs, box.h / 2)} fill={pl.color} uid={`rp${pageIndex}`} />
      </Xf>,
    );
  }
  if (pl && pl.kind === 'line') {
    L.lines.forEach((ln, li) => {
      const h = L.lineH + 2 * pl.padY * fs;
      const box = {x: ln.left - pl.padX * fs, y: ln.y - h / 2, w: ln.w + 2 * pl.padX * fs, h};
      const c = r.unit === 'page' || r.upcoming.opacity > 0.01 ? since : lineClock(li);
      const op = clamp01((c + 1) / (f30(3, f.fps) + 1));
      plates.push(
        <Xf key={`lp${li}`} ox={L.cx} oy={L.cy} scale={ex.scale} dy={ex.dy} opacity={op * pageOp}>
          <Container kind="chip" mode={mode} box={box} f={f} radius={Math.min(pl.radius * fs, box.h / 2)} fill={pl.color} uid={`rl${pageIndex}-${li}`} />
        </Xf>,
      );
    });
  }
  if (mode === 'mask') return <g transform={sameRot}>{plates}</g>;

  // ---- words ----
  const rankInUnit = (i: number) => {
    if (r.unit === 'page') return i;
    if (r.unit === 'line') return L.lines[L.words[i].line].idx.indexOf(i);
    return i;
  };
  const gated = r.upcoming.opacity > 0.01;
  const upOp = gated ? r.upcoming.opacity : r.unit === 'word' ? 0 : 1;
  const tr = f30(3, f.fps);
  const lineShift = (b: WordBox) => {
    if (r.align !== 'right') return 0;
    const ln = L.lines[b.line];
    return L.blockW - ln.w;
  };
  const wordTexts = page.words.map((w) => w.text);
  const layers: React.ReactNode[] = [];
  const unders: React.ReactNode[] = [];
  const wordPlates: React.ReactNode[] = [];
  const wipes: React.ReactNode[] = [];

  // typewriter bookkeeping: characters of a unit are revealed in order
  const unitChars = (i: number) => {
    const idx = r.unit === 'word' ? [i] : r.unit === 'line' ? L.lines[L.words[i].line].idx : L.words.map((_, k) => k);
    const total = idx.reduce((s, k) => s + L.words[k].text.length, 0);
    const before = idx.filter((k) => k < i).reduce((s, k) => s + L.words[k].text.length, 0);
    return {total, before};
  };

  L.words.forEach((b, i) => {
    const w = ws[i];
    const isEmph = !!page.words[i].emphasis;
    const rank = rankInUnit(i);
    const stagger = r.unit === 'word' && !gated ? 0 : r.enter.stagger * rank;
    // the clock the entrance runs on
    let c: number;
    if (r.unit === 'word') c = gated ? since - stagger : w.since;
    else if (r.unit === 'line') c = (gated ? since : lineClock(b.line)) - stagger;
    else c = since - stagger;
    const fx = enterFx(r.enter, c, f, fs);

    // spoken-state blend
    const inK = w.since < 0 ? 0 : clamp01((w.since + 1) / (tr + 1));
    const outK = w.until > 0 ? 1 : 1 - clamp01((-w.until + 1) / (tr + 1));
    const base = st.color;
    const em = r.emphasis;
    const col = (up: string | null, ac: string | null, sp: string | null) => {
      if (isEmph && em.color) return em.color;
      const cu = up ?? base, ca = ac ?? base, cs = sp ?? base;
      const c1 = cu === ca ? ca : colorMix(inK, cu, ca);
      return c1 === cs ? c1 : outK >= 1 ? c1 : colorMix(1 - outK, c1, cs);
    };
    const fill = col(r.upcoming.color, r.active.color, r.spoken.color);
    const mixN = (a: number, b: number, k: number) => a + (b - a) * k;
    const stateOp = mixN(mixN(upOp, r.active.opacity, inK), r.spoken.opacity, 1 - outK);
    const aK = Math.min(inK, outK);
    const actScale = isEmph ? Math.max(r.active.scale, 1) : r.active.scale;
    const stateScale = mixN(1, actScale, aK);

    const cy = b.baseline - L.capH / 2;
    const op = clamp01(fx.opacity) * stateOp;
    const transform = {ox: b.cx, oy: cy, scale: fx.scale * stateScale, dx: fx.dx + lineShift(b), dy: fx.dy};

    // wipe masks (per unit: word, line or page box)
    let maskUrl: string | undefined;
    if (fx.wipe != null && fx.wipe < 1) {
      const ln = L.lines[b.line];
      const unitBox = r.unit === 'word' ? {x: b.left, w: b.w} : r.unit === 'line' ? {x: ln.left, w: ln.w} : {x: L.left, w: L.blockW};
      const id = `rw-${pageIndex}-${r.unit === 'word' ? i : r.unit === 'line' ? `l${b.line}` : 'p'}`;
      maskUrl = `url(#${id})`;
      if (r.unit === 'word' || rank === 0)
        wipes.push(<FeatherMask key={id} id={id} x={unitBox.x} y={cy - b.fs} w={unitBox.w} h={b.fs * 2} p={fx.wipe} feather={0.25} />);
    }

    // text, optionally typed on
    let text = b.text;
    if (fx.chars != null) {
      const {total, before} = unitChars(i);
      const shown = Math.round(fx.chars * total) - before;
      text = b.text.slice(0, Math.max(0, Math.min(b.text.length, shown)));
      if (!text) return;
    }

    // plates behind this word
    const wp = pl && pl.kind === 'word' ? pl : null;
    const aPlate = aK > 0.01 && r.active.plate ? shapeOf(r.active.plate) : null;
    const ePlate = isEmph && em.plate ? shapeOf(em.plate) : null;
    const drawPlate = (p: any, key: string, k: number) => {
      const h = L.capH + 2 * p.padY * b.fs;
      const x = b.left - p.padX * b.fs;
      wordPlates.push(
        <Xf key={key} ox={b.cx} oy={cy} scale={fx.scale * stateScale} dx={fx.dx + lineShift(b)} dy={fx.dy} opacity={clamp01(k) * clamp01(fx.opacity) * pageOp}>
          <rect x={x} y={cy - h / 2} width={b.w + 2 * p.padX * b.fs} height={h} rx={Math.min(p.radius * b.fs, h / 2)} fill={p.color} />
        </Xf>,
      );
    };
    if (wp && fx.opacity > 0) drawPlate({...wp, color: wp.color}, `wp${i}`, 1);
    if (ePlate) drawPlate(ePlate, `ep${i}`, fx.opacity);
    if (aPlate) drawPlate(aPlate, `ap${i}`, aK);

    // underlines grow with the word
    const ul = (isEmph && em.underline ? em.underline : null) ?? (aK > 0.01 ? r.active.underline : null);
    if (ul) {
      const u = {color: fill, thickness: 0.07, offset: 0.14, ...ul};
      const grow = isEmph && em.underline ? clamp01((w.since + 1) / (f30(5, f.fps) + 1)) : clamp01((w.since + 1) / (Math.max(2, w.endF - w.startF) + 1));
      unders.push(
        <Xf key={`u${i}`} opacity={clamp01(fx.opacity) * stateOp * pageOp * (isEmph && em.underline ? 1 : aK)} dx={lineShift(b)}>
          <rect x={b.left} y={b.baseline + u.offset * b.fs} width={b.w * (w.since < 0 ? 0 : grow)} height={u.thickness * b.fs} rx={(u.thickness * b.fs) / 2} fill={u.color} />
        </Xf>,
      );
    }

    const sw = r.stroke ? 2 * r.stroke.width * b.fs : 0;
    layers.push(
      <Xf key={`w${i}`} {...transform} opacity={op * pageOp} filter={blurCss(f, fx.blur) || undefined} mask={maskUrl}>
        <text
          x={b.left}
          y={b.baseline}
          fontFamily={`"${b.font.family}"`}
          fontWeight={b.font.weight}
          fontSize={b.fs}
          letterSpacing={b.ls * b.fs}
          fill={fill}
          stroke={r.stroke ? r.stroke.color : undefined}
          strokeWidth={sw || undefined}
          strokeLinejoin="round"
          paintOrder="stroke fill"
          style={{fontSynthesis: 'none', textRendering: 'geometricPrecision', whiteSpace: 'pre'}}
        >
          {text}
        </text>
      </Xf>,
    );
  });
  void wordTexts;
  void t;

  return (
    <g transform={sameRot}>
      <defs>{wipes}</defs>
      <Xf ox={L.cx} oy={L.cy} scale={ex.scale} dy={ex.dy} opacity={1} filter={blurCss(f, ex.blur) || undefined}>
        {plates}
        <g>{wordPlates}</g>
        <g>{unders}</g>
        <Xf filter={styleShadow(st, f)}>{layers}</Xf>
      </Xf>
    </g>
  );
};

export const recipe: StyleDef = {
  spec: (page, st) => {
    const r = recipeOf(st);
    const pl = plateOf(r);
    const gap = Math.max(pl?.kind === 'word' ? pl.padX * 1.1 : 0, r.active.plate ? shapeOf(r.active.plate).padX * 1.1 : 0, r.emphasis.plate ? shapeOf(r.emphasis.plate).padX * 1.1 : 0);
    return page.words.map((w, i): WordSpec => {
      const emph = !!w.emphasis;
      const s: WordSpec = {text: applyCase(w.text, caseFor(r, st, emph))};
      if (emph) {
        if (r.emphasis.font) s.font = r.emphasis.font;
        if (r.emphasis.scale !== 1) s.size = r.emphasis.scale;
      }
      if (gap > 0 && i < page.words.length - 1) s.pad = gap * 0.5;
      return s;
    });
  },
  layout: (st, f) => {
    const r = recipeOf(st);
    const pl = plateOf(r);
    const fs = baseFontSize(st.size, st, f);
    const grow = Math.max(0, r.active.scale - 1); // the spoken word swells about its centre: keep room for it
    const pop = r.unit === 'word' && r.enter.kind === 'pop' ? Math.max(1, r.enter.overshoot) : 1; // the pop overshoots on top of the swell
    const edge = Math.max(0, r.active.scale * pop - 1);
    const pad = (pl && pl.kind !== 'word' ? pl.padX * fs : 0) + edge * fs * 2;
    return {align: r.align === 'center' ? 'center' : 'left', padX: pad, gap: 1 + grow * 4 + (r.stroke ? r.stroke.width * 6 : 0)};
  },
  hasMask: true,
  Page,
};
