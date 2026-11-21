'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { PROJECT, PROFILE } = require('../lab-boundaries.cjs');
const portable = require('../app/boundaries.cjs');
const PACKAGE_ROOT = path.join(PROJECT, 'desktop');
const PACKAGED_PROFILE = path.join(PROFILE, 'temp', 'desktop-boundaries-fixture', portable.APP_NAME);
const boundary = portable.createBoundaries({schema:1,profile_id:'installer-dev',build:'phase3a',
  package_root:PACKAGE_ROOT,project_root:PACKAGE_ROOT,profile_root:PACKAGED_PROFILE,profile_instance:null,
  windows_root:process.env.SystemRoot}, PACKAGE_ROOT);

test('path guards reject foreign writable roots, sibling-prefix escapes and relative paths', () => {
  for (const candidate of ['C:\\PODCUTAI-InstallerDev-Escape\\data', `${boundary.PROFILE}-other\\data`,
    path.join(boundary.PROFILE, '..', 'outside'), 'relative/data']) {
    assert.throws(() => boundary.checkedPath(candidate, boundary.PROFILE));
  }
  assert.equal(boundary.checkedPath(path.join(boundary.PROFILE, 'temp'), boundary.PROFILE), path.join(boundary.PROFILE, 'temp'));
});

test('inherited application redirects fail before sanitation instead of being masked', () => {
  for (const key of ['DATA_DIR', 'MODELS_DIR', 'PODCUT_CACHE_DIR', 'PODCUT_ENV_FILE', 'PODCUT_PROFILE_ROOT']) {
    assert.throws(() => boundary.assertInheritedPaths({ [key]: 'C:\\Forbidden-Desktop-Redirect' }));
  }
  assert.throws(() => boundary.assertInheritedPaths({ PODCUT_PROJECT_ROOT: 'D:\\OtherProject' }));
  boundary.assertInheritedPaths({ DATA_DIR: path.join(boundary.PROFILE, 'data'), PODCUT_PROJECT_ROOT: boundary.PROJECT });
});

test('child services use only bundled executables and scoped writable directories', () => {
  const packageRoot = path.join(boundary.PROJECT, 'dist', 'fixture', 'resources', 'podcut');
  const runId = crypto.randomUUID(), token = 'a'.repeat(64), uiToken = 'b'.repeat(64);
  const inherited = { SystemRoot: process.env.SystemRoot || 'C:\\Windows', PATH: 'C:\\Forbidden',
    GEMINI_API_KEY: 'dummy', PRIVATE_TOKEN: 'dummy', HTTP_PROXY: 'http://127.0.0.1:9',
    PYTHONPATH: 'C:\\Forbidden', NODE_OPTIONS: '--require=C:\\Forbidden\\loader.js', PIP_TARGET: 'C:\\Forbidden' };
  for (const role of ['host', 'backend', 'frontend']) {
    const env = boundary.childEnvironment({ packageRoot, runId, token, uiToken, role }, inherited);
    for (const name of ['GEMINI_API_KEY', 'PRIVATE_TOKEN', 'HTTP_PROXY', 'PYTHONPATH', 'NODE_OPTIONS', 'PIP_TARGET']) assert.equal(env[name], undefined);
    assert.equal(env.PODCUT_RUN_TOKEN, token);
    assert.equal(env.PODCUT_UI_TOKEN, role === 'frontend' ? uiToken : undefined);
    assert.equal(env.PODCUT_RUN_ID, runId);
    assert.equal(env.PORT, '3300');
    assert.equal(env.PODCUT_BACKEND_PORT, '8300');
    assert.equal(env.HF_HUB_OFFLINE, '1');
    assert.equal(env.FFMPEG_PATH, path.join(packageRoot, 'tools/ffmpeg.exe'));
    assert.equal(env.NODE_PATH, path.join(packageRoot, 'node/node.exe'));
    for (const name of ['DATA_DIR', 'MODELS_DIR', 'TEMP', 'TMP', 'HF_HOME', 'TORCH_HOME', 'XDG_CACHE_HOME', 'PODCUT_RUNTIME_DIR']) {
      boundary.checkedPath(env[name], boundary.PROFILE);
    }
    for (const item of env.PATH.split(path.delimiter)) boundary.checkedPath(item, packageRoot);
  }
});

test('an apparently valid health record still needs exact packaged identity and run', () => {
  const packageRoot = path.join(boundary.PROJECT, 'dist/fixture/resources/podcut');
  const runId = crypto.randomUUID();
  const identity = { profile_id: 'installer-dev', project_root: boundary.PROJECT, profile_root: boundary.PROFILE,
    run_id: runId, port: 8300, frontend_port: 3300, backend_host: '127.0.0.1',
    protocol: 'podcut-installer-dev-v1', build: 'phase3a', packaged: true, packaged_root: packageRoot };
  assert.equal(boundary.matchesIdentity(identity, packageRoot, runId), true);
  for (const [key, value] of [['run_id', crypto.randomUUID()], ['port', 8000], ['build', 'phase2a'],
    ['packaged', false], ['packaged_root', boundary.PROJECT], ['project_root', 'D:\\OtherProject']]) {
    assert.equal(boundary.matchesIdentity({ ...identity, [key]: value }, packageRoot, runId), false);
  }
});

test('renderer navigation and download targets cannot escape the private frontend/profile', () => {
  for (const url of ['http://127.0.0.1:8300/api/jobs', 'http://localhost:3300/', 'https://example.org/',
    'file:///C:/Windows/win.ini', 'http://127.0.0.1:3300.example.org/']) assert.equal(boundary.isLocalUi(url), false);
  assert.equal(boundary.isLocalUi('http://127.0.0.1:3300/api/jobs'), true);
  const root = path.join(boundary.PROFILE, 'data/downloads');
  const result = boundary.safeDownload(root, '..\\..\\original.mp4');
  boundary.checkedPath(result, root);
  assert.equal(path.basename(result).endsWith('-original.mp4'), true);
  assert.throws(() => boundary.safeDownload('C:\\Forbidden', 'clip.mp4'));
});

test('desktop package validation requires real bundled artifacts and compatible manifest', () => {
  const root = path.join(boundary.PROJECT, `.package-fixture-${crypto.randomUUID()}`);
  boundary.directory(root, boundary.PROJECT);
  try {
    const resources = path.join(root, 'resources');
    const payload = boundary.directory(path.join(resources, 'podcut'), boundary.PROJECT);
    const exe = path.join(root, 'PODCUTAI-InstallerDev.exe');
    fs.writeFileSync(exe, 'fixture');
    const marker = { schema: 1, profile_id: 'installer-dev', build: 'phase3a', backend: 'backend/PODCUTBackend.exe',
      frontend: 'frontend/server.js', node: 'node/node.exe' };
    fs.writeFileSync(path.join(payload, 'podcut-package.json'), JSON.stringify(marker));
    assert.throws(() => boundary.readPackage(resources, exe));
    for (const relative of [marker.backend, marker.frontend, marker.node, 'tools/ffmpeg.exe', 'tools/ffprobe.exe']) {
      const target = path.join(payload, relative);
      boundary.directory(path.dirname(target), boundary.PROJECT);
      fs.writeFileSync(target, 'fixture');
    }
    assert.equal(boundary.readPackage(resources, exe), payload);
    fs.writeFileSync(path.join(payload, 'podcut-package.json'), JSON.stringify({ ...marker, build: 'foreign' }));
    assert.throws(() => boundary.readPackage(resources, exe));
  } finally {
    boundary.checkedPath(root, boundary.PROJECT);
    fs.rmSync(root, { recursive: true });
  }
});
