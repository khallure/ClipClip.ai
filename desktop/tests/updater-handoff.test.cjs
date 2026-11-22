'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { PROFILE, checkedPath } = require('../lab-boundaries.cjs');
const { APP_NAME, validatePendingUpdate, parseSemver, isNewerVersion } = require('../app/boundaries.cjs');

function makeFixture() {
  const root = checkedPath(path.join(PROFILE, 'temp', `updater-handoff-${crypto.randomUUID()}`), PROFILE);
  const version = '1.0.1';
  const filename = `ClipClip.ai-Setup-${version}-x64.exe`;
  const dir = path.join(root, 'cache', 'updates', version);
  fs.mkdirSync(dir, { recursive: true });
  const payload = Buffer.from('MZ' + 'CLIPCLIP_HANDOFF_TEST_'.repeat(100));
  const installerPath = path.join(dir, filename);
  fs.writeFileSync(installerPath, payload);
  const sha256 = crypto.createHash('sha256').update(payload).digest('hex');
  const runId = crypto.randomUUID();
  const record = {
    schema: 1,
    product: APP_NAME,
    run_id: runId,
    current_version: '1.0.0',
    version,
    filename,
    installer_path: installerPath,
    bytes: payload.length,
    sha256,
  };
  return { root, installerPath, runId, record, cleanup: () => fs.rmSync(root, { recursive: true, force: true }) };
}

test('semver parser and strict upgrade comparator block invalid and downgrade versions', () => {
  assert.deepEqual(parseSemver('1.2.3'), [1, 2, 3]);
  assert.equal(isNewerVersion('1.0.1', '1.0.0'), true);
  assert.equal(isNewerVersion('1.1.0', '1.0.9'), true);
  assert.equal(isNewerVersion('2.0.0', '1.9.9'), true);
  assert.equal(isNewerVersion('1.0.0', '1.0.0'), false);
  assert.equal(isNewerVersion('0.9.9', '1.0.0'), false);
  for (const bad of ['1.0', 'v1.0.0', '01.0.0', '1.0.0-beta', '']) {
    assert.throws(() => parseSemver(bad));
  }
});

test('validatePendingUpdate accepts an exact SHA-256 verified installer inside updates cache', async () => {
  const fx = makeFixture();
  try {
    const verified = await validatePendingUpdate(fx.record, {
      profileRoot: fx.root,
      runId: fx.runId,
      currentVersion: '1.0.0',
      minBytes: 128,
    });
    assert.equal(verified.version, '1.0.1');
    assert.equal(verified.installerPath, fx.installerPath);
    assert.equal(verified.sha256, fx.record.sha256);
  } finally {
    fx.cleanup();
  }
});

test('validatePendingUpdate rejects SHA-256 tamper, wrong run_id, downgrade, foreign path, and bad filename', async () => {
  const fx = makeFixture();
  try {
    // 1. Wrong run_id
    await assert.rejects(
      validatePendingUpdate({ ...fx.record, run_id: crypto.randomUUID() }, {
        profileRoot: fx.root, runId: fx.runId, currentVersion: '1.0.0', minBytes: 128,
      }),
    );
    // 2. Downgrade / equal version
    await assert.rejects(
      validatePendingUpdate({ ...fx.record, version: '1.0.0', filename: 'ClipClip.ai-Setup-1.0.0-x64.exe' }, {
        profileRoot: fx.root, runId: fx.runId, currentVersion: '1.0.0', minBytes: 128,
      }),
    );
    // 3. Foreign executable path outside cache/updates/<version>
    const evilExe = path.join(fx.root, 'temp', 'evil.exe');
    fs.mkdirSync(path.dirname(evilExe), { recursive: true });
    fs.writeFileSync(evilExe, fs.readFileSync(fx.installerPath));
    await assert.rejects(
      validatePendingUpdate({ ...fx.record, installer_path: evilExe }, {
        profileRoot: fx.root, runId: fx.runId, currentVersion: '1.0.0', minBytes: 128,
      }),
    );
    // 4. Size mismatch
    await assert.rejects(
      validatePendingUpdate({ ...fx.record, bytes: fx.record.bytes + 1 }, {
        profileRoot: fx.root, runId: fx.runId, currentVersion: '1.0.0', minBytes: 128,
      }),
    );
    // 5. SHA-256 mismatch (file tampered on disk)
    const tampered = Buffer.from(fs.readFileSync(fx.installerPath));
    tampered[tampered.length - 1] ^= 0xff;
    fs.writeFileSync(fx.installerPath, tampered);
    await assert.rejects(
      validatePendingUpdate(fx.record, {
        profileRoot: fx.root, runId: fx.runId, currentVersion: '1.0.0', minBytes: 128,
      }),
      /SHA-256/,
    );
  } finally {
    fx.cleanup();
  }
});

test('validatePendingUpdate rejects a hardlinked installer file', async () => {
  const fx = makeFixture();
  const original = fs.lstatSync;
  fs.lstatSync = candidate => {
    if (path.resolve(candidate).toLowerCase() === path.resolve(fx.installerPath).toLowerCase()) {
      return { isSymbolicLink: () => false, isFile: () => true, nlink: 2, size: fx.record.bytes };
    }
    return original(candidate);
  };
  try {
    await assert.rejects(
      validatePendingUpdate(fx.record, {
        profileRoot: fx.root, runId: fx.runId, currentVersion: '1.0.0', minBytes: 128,
      }),
    );
  } finally {
    fs.lstatSync = original;
    fx.cleanup();
  }
});
