import type {CPage, FontSpec, Style} from './types';

/** Tokens with a digit or a currency / percent sign ("25,000", "$5,000", "10%.", "₹1 lakh"). */
export const hasNumeral = (text: string) => /[\d$₹€£%]/.test(text);
export const isEmph = (page: CPage, i: number) => !!page.words[i]?.emphasis;

/** The preset's `emphasis` block (colours, fonts, scale per style). Keys are private to each style. */
export const emphasisOf = (st: Style): Record<string, any> => st.emphasis ?? {};
export type EmphasisFont = FontSpec | undefined;
