'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { PROJECT } = require('../lab-boundaries.cjs');

test('frontend/lib/subtle/styles matches subtle-renderer/src/styles byte-for-byte with zero drift', () => {
  const rendererStylesDir = path.join(PROJECT, 'subtle-renderer/src/styles');
  const frontendStylesDir = path.join(PROJECT, 'frontend/lib/subtle/styles');
  const rendererFiles = fs.readdirSync(rendererStylesDir).sort();
  const frontendFiles = fs.readdirSync(frontendStylesDir).sort();

  assert.deepEqual(frontendFiles, rendererFiles);
  assert.ok(rendererFiles.length >= 36);
  for (const name of rendererFiles) {
    const a = fs.readFileSync(path.join(rendererStylesDir, name));
    const b = fs.readFileSync(path.join(frontendStylesDir, name));
    assert.equal(Buffer.compare(a, b), 0, `Style drift detected in ${name}`);
  }
});

test('frontend/lib/subtle/engine shared files match subtle-renderer/src/engine and forbid remotion imports', () => {
  const sharedEngineFiles = [
    'api.ts',
    'beats.ts',
    'captions-extra.tsx',
    'draw.tsx',
    'emphasis.ts',
    'index.ts',
    'legibility.tsx',
    'shots.ts',
    'time.ts',
    'types.ts',
  ];
  const rendererEngine = path.join(PROJECT, 'subtle-renderer/src/engine');
  const frontendSubtle = path.join(PROJECT, 'frontend/lib/subtle');
  const frontendEngine = path.join(frontendSubtle, 'engine');

  for (const name of sharedEngineFiles) {
    const a = fs.readFileSync(path.join(rendererEngine, name));
    const b = fs.readFileSync(path.join(frontendEngine, name));
    assert.equal(Buffer.compare(a, b), 0, `Engine drift detected in ${name}`);
  }

  const fontsAdapter = fs.readFileSync(path.join(frontendEngine, 'fonts.ts'), 'utf8');
  assert.ok(fontsAdapter.includes('/api/caption-fonts/'), 'Frontend fonts adapter must load via /api/caption-fonts/');

  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      assert.equal(entry.isSymbolicLink(), false, `Symlink forbidden in frontend subtle copy: ${full}`);
      if (entry.isDirectory()) walk(full);
      else if (/\.(ts|tsx|js|jsx|mjs)$/.test(entry.name)) {
        const src = fs.readFileSync(full, 'utf8');
        assert.equal(
          /from\s+['"]@?remotion\b/.test(src),
          false,
          `Remotion import forbidden in frontend subtle file: ${full}`,
        );
      }
    }
  };
  walk(frontendSubtle);
});

test('bundled Subtle styles, OFL fonts, licenses, and Windows ONNX assets are complete', () => {
  const stylesDir = path.join(PROJECT, 'backend/assets/subtle_styles');
  const fontsDir = path.join(PROJECT, 'backend/assets/subtle_fonts');
  const segDir = path.join(PROJECT, 'backend/assets/models/segmentation');
  const licDir = path.join(PROJECT, 'backend/assets/licenses');

  const jsonStyles = fs.readdirSync(stylesDir).filter((f) => f.endsWith('.json'));
  assert.equal(jsonStyles.length, 39);

  const fontEntries = fs.readdirSync(fontsDir);
  assert.equal(fontEntries.filter((f) => f.endsWith('.ttf')).length, 30);
  assert.equal(fontEntries.filter((f) => /^OFL-.*\.txt$/i.test(f)).length, 16);

  assert.equal(fs.existsSync(path.join(segDir, 'selfie_segmentation.onnx')), true);
  assert.equal(fs.existsSync(path.join(segDir, 'segmentation-manifest.json')), true);
  assert.equal(fs.existsSync(path.join(segDir, 'LICENSE.Segmentation-Apache-2.0.txt')), true);
  assert.equal(fs.existsSync(path.join(licDir, 'LICENSE.Subtle-MIT.txt')), true);
  assert.equal(fs.existsSync(path.join(licDir, 'THIRD_PARTY.Subtle.md')), true);
});

test('desktop main process download allowlist permits Subtle captions.json and preview.jpg', () => {
  const mainSrc = fs.readFileSync(path.join(PROJECT, 'desktop/app/main.cjs'), 'utf8');
  const match = mainSrc.match(/const\s+allowed\s*=\s*(\/[^;]+\/)\.test\(url\.pathname\)/);
  assert.ok(match, 'will-download allowlist regex must be present in desktop/app/main.cjs');
  const regexLiteral = match[1];
  const lastSlash = regexLiteral.lastIndexOf('/');
  const allowRegex = new RegExp(regexLiteral.slice(1, lastSlash), regexLiteral.slice(lastSlash + 1));

  const id = '0123456789abcdef0123456789abcdef';
  for (const okPath of [
    `/api/jobs/${id}/exports/${id}/file`,
    `/api/jobs/${id}/exports/${id}/captions.srt`,
    `/api/jobs/${id}/exports/${id}/captions.ass`,
    `/api/jobs/${id}/exports/${id}/captions.vtt`,
    `/api/jobs/${id}/exports/${id}/captions.json`,
    `/api/jobs/${id}/exports/${id}/preview.jpg`,
  ]) {
    assert.equal(allowRegex.test(okPath), true, `Expected download allowlist to permit ${okPath}`);
  }
  for (const badPath of [
    `/api/jobs/${id}/exports/${id}/clip.partial.mp4`,
    `/api/jobs/${id}/exports/${id}/secret.env`,
    '/api/models/whisper/other',
  ]) {
    assert.equal(allowRegex.test(badPath), false, `Expected download allowlist to block ${badPath}`);
  }
});
