import { bundle } from '@remotion/bundler';
import { ensureBrowser } from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
process.chdir(here);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith('--')) continue;
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith('--')) {
      out[key] = true;
    } else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const entryPoint = path.resolve(here, 'src', 'index.ts');
const outDir = path.resolve(here, String(args['out-dir'] || 'bundle'));
const fontsDir = path.resolve(here, String(args['fonts-dir'] || path.join('..', 'backend', 'assets', 'subtle_fonts')));
const skipBrowser = Boolean(args['skip-browser']);

if (!fs.existsSync(fontsDir)) {
  throw new Error(`Subtle fonts directory not found: ${fontsDir}`);
}

const fontFiles = fs.readdirSync(fontsDir).filter((name) => name.toLowerCase().endsWith('.ttf'));
if (fontFiles.length === 0) {
  throw new Error(`No .ttf fonts found in ${fontsDir}`);
}

fs.rmSync(outDir, { recursive: true, force: true });
const served = await bundle({
  entryPoint,
  outDir,
  publicDir: fontsDir,
});

// Strip license text files if copied into bundle public assets? No harm keeping small OFL notices,
// and verify the primary font files landed in the bundle output.
const bundledFonts = fontFiles.filter((f) => fs.existsSync(path.join(served, 'public', f)));
if (bundledFonts.length !== fontFiles.length) {
  throw new Error(`Expected ${fontFiles.length} fonts in bundle, found ${bundledFonts.length}`);
}

let browserInfo = null;
if (!skipBrowser) {
  browserInfo = await ensureBrowser({
    chromeMode: 'headless-shell',
  });
}

console.log(
  JSON.stringify(
    {
      ok: true,
      bundleDir: served,
      fontsCount: bundledFonts.length,
      browser: browserInfo,
    },
    null,
    2,
  ),
);
