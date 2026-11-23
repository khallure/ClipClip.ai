import {Easing, interpolateColors} from 'remotion';

export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
export const mix = (a: number, b: number, t: number) => a + (b - a) * t;
export const easeOut = (t: number) => Easing.out(Easing.cubic)(clamp01(t));
export const easeOutQuart = (t: number) => Easing.out(Easing.poly(4))(clamp01(t));
export const easeOutExpo = (t: number) => Easing.bezier(0.16, 1, 0.3, 1)(clamp01(t));
export const easeInOut = (t: number) => Easing.inOut(Easing.sin)(clamp01(t));
/** Ease out with a small overshoot, settles at 1. */
export const easeOutBack = (t: number, s = 1.2) => Easing.out(Easing.back(s))(clamp01(t));
export const colorMix = (t: number, a: string, b: string) => interpolateColors(clamp01(t), [0, 1], [a, b]);
