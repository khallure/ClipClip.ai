import type {FontSpec, Style} from './types';

const loaded = new Set<string>();
const pendingLoads = new Map<string, Promise<void>>();

/** Every {family, file, weight} object anywhere in the style JSON is a font to load from ClipClip.ai backend. */
export function fontSpecs(style: unknown, out: FontSpec[] = []): FontSpec[] {
  if (style && typeof style === 'object') {
    const o = style as Record<string, any>;
    if (typeof o.file === 'string' && typeof o.family === 'string') out.push(o as FontSpec);
    else for (const v of Object.values(o)) fontSpecs(v, out);
  }
  return out;
}

const PLAYFAIR_ITALIC: FontSpec = {family: 'Playfair Display Medium Italic', file: 'PlayfairDisplay-MediumItalic.ttf', weight: 500};
export const GLYPH_GAPS: Record<string, {missing: string; fallback: FontSpec}> = {
  'CourierPrime-Bold.ttf': {missing: '₹', fallback: {family: 'Inter Bold', file: 'Inter-Bold.ttf', weight: 700}},
  'CourierPrime-Regular.ttf': {missing: '₹', fallback: {family: 'Inter Regular', file: 'Inter-Regular.ttf', weight: 400}},
  'InstrumentSerif-Italic.ttf': {missing: '₹', fallback: PLAYFAIR_ITALIC},
  'InstrumentSerif-Regular.ttf': {missing: '₹', fallback: PLAYFAIR_ITALIC},
  'ArchivoBlack-Regular.ttf': {missing: '₹', fallback: {family: 'Montserrat Black', file: 'Montserrat-Black.ttf', weight: 900}},
  'PinyonScript-Regular.ttf': {missing: '₹', fallback: PLAYFAIR_ITALIC},
  'AbrilFatface-Regular.ttf': {missing: '₹', fallback: {family: 'Playfair Display Black Italic', file: 'PlayfairDisplay-BlackItalic.ttf', weight: 900}},
  'Corben-Bold.ttf': {missing: '₹', fallback: {family: 'Playfair Display Black Italic', file: 'PlayfairDisplay-BlackItalic.ttf', weight: 900}},
  'GloriaHallelujah-Regular.ttf': {missing: '₹', fallback: {family: 'Caveat SemiBold', file: 'Caveat-SemiBold.ttf', weight: 600}},
  'LibreBaskerville-wght.ttf': {missing: '₹', fallback: PLAYFAIR_ITALIC},
  'ReenieBeanie-Regular.ttf': {missing: '₹', fallback: {family: 'Caveat SemiBold', file: 'Caveat-SemiBold.ttf', weight: 600}},
};

/** `font`, or its fallback when `text` has a character the font lacks. */
export function fontWithGlyphs(font: FontSpec, text: string): FontSpec {
  const g = GLYPH_GAPS[font.file];
  return g && [...g.missing].some((ch) => text.includes(ch)) ? g.fallback : font;
}

export async function loadFonts(style: Style) {
  if (typeof document === 'undefined' || typeof FontFace === 'undefined') return;
  const specs = fontSpecs(style);
  for (const f of [...specs]) if (GLYPH_GAPS[f.file]) specs.push(GLYPH_GAPS[f.file].fallback);
  await Promise.all(
    specs.map(async (f) => {
      const key = `${f.family}|${f.file}`;
      if (loaded.has(key)) return;
      let promise = pendingLoads.get(key);
      if (!promise) {
        promise = (async () => {
          const weight = /wght/.test(f.file) ? '100 900' : String(f.weight);
          const url = `/api/caption-fonts/${encodeURIComponent(f.file)}`;
          const face = new FontFace(f.family, `url(${url})`, {weight});
          await face.load();
          document.fonts.add(face);
          loaded.add(key);
        })().finally(() => {
          pendingLoads.delete(key);
        });
        pendingLoads.set(key, promise);
      }
      await promise;
    }),
  );
  await document.fonts.ready;
}
