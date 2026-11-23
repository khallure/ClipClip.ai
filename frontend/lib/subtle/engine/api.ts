import type React from 'react';
import type {CBig, CPage, CaptionsFile, Frame, Layer, Style} from './types';
import type {Layout, LayoutOpts, WordSpec} from './layout';
import type {PageTiming, WordState} from './time';

/** Everything a style's Page component receives for the page that is on screen. */
export type PageProps = {
  st: Style;
  f: Frame;
  page: CPage;
  pageIndex: number;
  L: Layout; // laid-out words (one box per word, in page order)
  t: PageTiming;
  frame: number;
  since: number; // frames since the page started
  left: number; // frames until the page ends
  ws: WordState[]; // per-word state: upcoming | active | spoken, progress, since
  mode: 'visible' | 'mask'; // mask = draw only solid white container shapes (backdrop blur mask)
  captions: CaptionsFile;
  layer?: Layer; // which layer is being drawn: all (no cut-out: giant words sit in front), front or behind
};

export type BigProps = {st: Style; f: Frame; big: CBig[]; frame: number; captions?: CaptionsFile; layer?: Layer};

/**
 * One style = one object like this, registered in styles/index.ts under `anim.type`.
 * Only `Page` is required.
 */
export type StyleDef = {
  /** Per-word overrides before layout: mixed fonts, scaled words, replaced text. */
  spec?: (page: CPage, st: Style) => WordSpec[];
  /** Layout options (alignment, padding kept free for a container, size, position). */
  layout?: (st: Style, f: Frame) => LayoutOpts;
  /** Draw the page. Return null for frames that show nothing. */
  Page: React.FC<PageProps>;
  /** Draw the page's containers only (white shapes) when mode === 'mask'. Set when `Page` handles mask mode. */
  hasMask?: boolean;
  /** Giant words drawn behind the speaker (`--layer behind`). */
  Big?: React.FC<BigProps>;
  /** Minimum frames (at 30 fps) a page stays up, even if its words end sooner. */
  minShow?: number;
};
