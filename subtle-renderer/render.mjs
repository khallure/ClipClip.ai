import { bundle } from '@remotion/bundler';
import {
  ensureBrowser,
  renderFrames,
  renderMedia,
  renderStill,
  selectComposition,
} from '@remotion/renderer';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

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

function resolveMaybeRelative(value, baseDir) {
  if (!value || typeof value !== 'string') return null;
  return path.isAbsolute(value) ? value : path.resolve(baseDir, value);
}

const callerCwd = process.cwd();
const args = parseArgs(process.argv.slice(2));

const captionsPath = resolveMaybeRelative(args.captions, callerCwd);
const stylePath = resolveMaybeRelative(args.style, callerCwd);
const outPath = resolveMaybeRelative(args.out, callerCwd);
const fontsDirArg = resolveMaybeRelative(args['fonts-dir'], callerCwd);
const bundleDirArg = resolveMaybeRelative(args['bundle-dir'], callerCwd);
const browserExecArg =
  resolveMaybeRelative(args['browser-executable'], callerCwd) ||
  process.env.REMOTION_BROWSER_EXECUTABLE ||
  null;

const layer = String(args.layer || 'all');
if (!['all', 'behind', 'front', 'mask'].includes(layer)) {
  throw new Error(`Invalid --layer "${layer}". Expected all|behind|front|mask.`);
}
if (!captionsPath || !stylePath || !outPath) {
  throw new Error(
    'Usage: node render.mjs --captions <file> --style <file> --out <file> [--fonts-dir <dir>] [--bundle-dir <dir>] [--browser-executable <path>] [--layer all|behind|front|mask] [--still <frame>] [--png] [--concurrency <n>]',
  );
}

const captions = JSON.parse(fs.readFileSync(captionsPath, 'utf8'));
const style = JSON.parse(fs.readFileSync(stylePath, 'utf8'));

const isStill = args.still !== undefined && args.still !== false && args.still !== '';
const stillFrame = isStill ? Number(args.still) : null;
if (isStill && (!Number.isInteger(stillFrame) || stillFrame < 0)) {
  throw new Error(`Invalid --still frame "${args.still}"`);
}
const forcePng = Boolean(args.png);
const concurrency =
  args.concurrency !== undefined && args.concurrency !== true
    ? Math.max(1, Number(args.concurrency) || 1)
    : undefined;

const defaultFontsDir = path.resolve(here, '..', 'backend', 'assets', 'subtle_fonts');
const fontsDir = fontsDirArg || defaultFontsDir;
const defaultBundleDir = path.join(here, 'bundle');
const candidateBundleDir = bundleDirArg || defaultBundleDir;

function findLocalBrowserExecutable(baseDir) {
  const root = path.join(baseDir, 'node_modules', '.remotion', 'chrome-headless-shell');
  if (!fs.existsSync(root)) return null;
  const targetName = process.platform === 'win32' ? 'chrome-headless-shell.exe' : 'chrome-headless-shell';
  const stack = [root];
  while (stack.length > 0) {
    const dir = stack.pop();
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile() && entry.name.toLowerCase() === targetName) return full;
    }
  }
  return null;
}

// Resolve relative paths BEFORE changing cwd so Remotion finds node_modules/.remotion locally.
try {
  if (process.cwd() !== here) process.chdir(here);
} catch {
  // Read-only or sandboxed caller cwd; explicit paths below still resolve cleanly.
}

let serveUrl = null;
let tempBundleDir = null;

try {
  const sameAsDefaultFonts =
    path.normalize(fontsDir).toLowerCase() === path.normalize(defaultFontsDir).toLowerCase();
  const canReuseBundle =
    (sameAsDefaultFonts || Boolean(bundleDirArg)) &&
    !args['force-rebundle'] &&
    fs.existsSync(path.join(candidateBundleDir, 'index.html'));

  if (canReuseBundle) {
    serveUrl = candidateBundleDir;
  } else {
    tempBundleDir = fs.mkdtempSync(path.join(os.tmpdir(), 'subtle-bundle-'));
    serveUrl = await bundle({
      entryPoint: path.join(here, 'src', 'index.ts'),
      outDir: tempBundleDir,
      publicDir: fontsDir,
    });
  }

  let browserExecutable = browserExecArg || findLocalBrowserExecutable(here);
  if (!browserExecutable) {
    const browserInfo = await ensureBrowser({
      chromeMode: 'headless-shell',
    });
    if (browserInfo && 'path' in browserInfo && browserInfo.path) {
      browserExecutable = browserInfo.path;
    }
  }

  const inputProps = { captions, style, layer };
  const composition = await selectComposition({
    serveUrl,
    id: 'Captions',
    inputProps,
    browserExecutable,
    chromeMode: 'headless-shell',
    logLevel: 'error',
  });

  fs.mkdirSync(path.dirname(outPath), { recursive: true });

  if (isStill) {
    const ext = path.extname(outPath).toLowerCase();
    const imageFormat = forcePng || ext === '.png' || layer !== 'mask' ? 'png' : 'jpeg';
    await renderStill({
      composition,
      serveUrl,
      inputProps,
      frame: stillFrame,
      output: outPath,
      imageFormat,
      overwrite: true,
      browserExecutable,
      chromeMode: 'headless-shell',
      logLevel: 'error',
    });
  } else if (forcePng) {
    fs.mkdirSync(outPath, { recursive: true });
    await renderFrames({
      composition,
      serveUrl,
      inputProps,
      outputDir: outPath,
      imageFormat: 'png',
      concurrency,
      browserExecutable,
      chromeMode: 'headless-shell',
      logLevel: 'error',
      onFrameUpdate: () => {},
      onStart: () => {},
    });
  } else {
    const isMask = layer === 'mask';
    const ext = path.extname(outPath).toLowerCase();
    if (ext === '.webm' && !isMask) {
      await renderMedia({
        composition,
        serveUrl,
        inputProps,
        codec: 'vp9',
        pixelFormat: 'yuva420p',
        imageFormat: 'png',
        muted: true,
        overwrite: true,
        outputLocation: outPath,
        concurrency,
        browserExecutable,
        chromeMode: 'headless-shell',
        logLevel: 'error',
      });
    } else if (isMask && ext === '.mp4') {
      await renderMedia({
        composition,
        serveUrl,
        inputProps,
        codec: 'h264',
        pixelFormat: 'yuv420p',
        imageFormat: 'jpeg',
        muted: true,
        overwrite: true,
        outputLocation: outPath,
        concurrency,
        browserExecutable,
        chromeMode: 'headless-shell',
        logLevel: 'error',
      });
    } else {
      await renderMedia({
        composition,
        serveUrl,
        inputProps,
        codec: 'prores',
        proResProfile: '4444',
        pixelFormat: 'yuva444p10le',
        imageFormat: 'png',
        muted: true,
        overwrite: true,
        outputLocation: outPath,
        concurrency,
        browserExecutable,
        chromeMode: 'headless-shell',
        logLevel: 'error',
      });
    }
  }

  console.log(
    JSON.stringify({
      ok: true,
      out: outPath,
      layer,
      width: composition.width,
      height: composition.height,
      fps: composition.fps,
      durationInFrames: composition.durationInFrames,
    }),
  );
} finally {
  if (tempBundleDir) {
    fs.rmSync(tempBundleDir, { recursive: true, force: true });
  }
}
