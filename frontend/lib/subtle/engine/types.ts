export type CWord = {text: string; startMs: number; endMs: number; line?: number; emphasis?: boolean};
/** `y`: where this page sits, % of the frame height (set by scripts/placement.py); absent = the style's `position.y`. */
export type CPage = {startMs: number; endMs: number; words: CWord[]; y?: number};
/** `front`: draw this giant word in front of the speaker (numbers), not behind. */
export type CBig = {text: string; startMs: number; endMs: number; front?: boolean; close?: boolean};
/** Per-shot subject data (scripts/shots.py): how much of each of 24 horizontal bands the person covers, the face box, and whether a giant word fits behind. */
export type CShot = {startMs: number; endMs: number; bands?: number[]; face?: [number, number, number, number]; behind?: boolean; y?: number; cover?: number};
export type CaptionsFile = {
  version: number;
  video: {width: number; height: number; fps: number; durationMs: number};
  style: string;
  pages: CPage[];
  big?: CBig[];
  shots?: CShot[];
  subject?: CSubject[];
  /** Music styles: tempo, every beat and every bar start in ms (from scripts/beats.py). Absent when the audio has no clear pulse. */
  beats?: {bpm: number; times: number[]; downbeats: number[]};
  /** Hard cuts in the picture (ms, first frame of the new shot): no page straddles one. */
  cuts?: number[];
};
export type FontSpec = {family: string; file: string; weight: number};
export type Layer = 'all' | 'behind' | 'front' | 'mask';

/** A style preset (styles/<name>.json). Sizes are % of frame height. Style-specific keys live in `anim` and friends. */
export type Style = {
  name: string;
  font: FontSpec;
  emphasisFont?: FontSpec;
  /** How an emphasised word looks in this style: colours, font, scale. Keys are private to each style. */
  emphasis?: Record<string, any>;
  noteFont?: FontSpec;
  case: 'upper' | 'sentence' | 'lower';
  size: number;
  wideScale?: number;
  lineHeight?: number;
  letterSpacing?: number;
  color: string;
  activeColor?: string;
  dimColor?: string | null;
  shadow?: {x: number; y: number; blur: number; color: string} | null;
  container?: Record<string, any> | null;
  backdropBlur?: number;
  /** `flex`: the range placement may move a page within, [low, high] in % of height; 0 = fixed; absent = a band around y. */
  position: {x?: number; y: number; marginX: number; flex?: [number, number] | 0};
  page: {maxLines?: number; minFrames?: number};
  anim: Record<string, any> & {type: string};
  /** Data-driven caption treatment read by the generic `recipe` style (`anim.type: "recipe"`). See docs/ARCHITECTURE.md, "Recipe styles". */
  recipe?: Record<string, any>;
  behind?: {
    enabled: boolean;
    bigY?: number;
    frontY?: number;
    frontWidth?: number;
    bigWidth?: number;
    bigMaxSize?: number;
    bigOpacity?: number;
    bigFont?: FontSpec;
    bigColor?: string;
  };
};

/** Frame geometry: pixel size, fps, and whether the video is landscape. */
export type Frame = {W: number; H: number; fps: number; wide: boolean};
export type Box = {x: number; y: number; w: number; h: number};
/** The person every 400 ms (scripts/shots.py): per-band coverage and the largest face box. */
export type CSubject = {ms: number; bands: number[]; face?: [number, number, number, number]};
