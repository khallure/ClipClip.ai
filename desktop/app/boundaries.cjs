'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const FRONTEND = 'http://127.0.0.1:3300';
const BACKEND = 'http://127.0.0.1:8300';
const APP_NAME = 'ClipClip.ai';
const BUILD = 'phase3a';
const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/;

function contains(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function checkedPath(candidate, root) {
  if (typeof candidate !== 'string' || !path.isAbsolute(candidate) || !contains(root, candidate) ||
      !/^[A-Za-z]:[\\/]$/.test(path.parse(path.resolve(candidate)).root)) {
    throw new Error('Desktop path is outside its approved development root.');
  }
  const absolute = path.resolve(candidate);
  let cursor = absolute;
  for (;;) {
    try {
      const info = fs.lstatSync(cursor);
      if (info.isSymbolicLink()) throw new Error('Desktop paths cannot traverse a link or junction.');
      if (info.isFile() && info.nlink > 1) throw new Error('Desktop paths cannot use hardlinked files.');
      if (fs.realpathSync.native(cursor).toLowerCase() !== cursor.toLowerCase()) {
        throw new Error('Desktop path resolution does not match its approved development location.');
      }
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const parent = path.dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  return absolute;
}

function directory(candidate, root) {
  const absolute = checkedPath(candidate, root);
  fs.mkdirSync(absolute, { recursive: true });
  return checkedPath(absolute, root);
}

function readPackage(resourcesPath, executable) {
  const shellRoot = path.dirname(path.resolve(executable));
  const root = checkedPath(path.join(resourcesPath, 'podcut'), shellRoot);
  const shell = checkedPath(executable, shellRoot);
  if (path.resolve(resourcesPath).toLowerCase() !== path.join(shellRoot, 'resources').toLowerCase()) {
    throw new Error('Desktop resources must belong to this executable.');
  }
  if (path.dirname(shell).toLowerCase() !== path.dirname(resourcesPath).toLowerCase() ||
      path.basename(shell).toLowerCase() !== `${APP_NAME}.exe`.toLowerCase()) {
    throw new Error('Run the packaged ClipClip.ai executable from its own desktop package.');
  }
  const markerPath = checkedPath(path.join(root, 'podcut-package.json'), root);
  if (fs.statSync(markerPath).size > 16384) throw new Error('The desktop package marker is too large.');
  const marker = JSON.parse(fs.readFileSync(markerPath, 'utf8'));
  if (marker.schema !== 1 || marker.profile_id !== 'installer-dev' || marker.build !== BUILD ||
      marker.backend !== 'backend/PODCUTBackend.exe' || marker.frontend !== 'frontend/server.js' ||
      marker.node !== 'node/node.exe') {
    throw new Error('The desktop package manifest is missing or incompatible.');
  }
  for (const relative of [marker.backend, marker.frontend, marker.node, 'tools/ffmpeg.exe', 'tools/ffprobe.exe']) {
    const file = checkedPath(path.join(root, relative), root);
    if (!fs.statSync(file).isFile()) throw new Error('The desktop package is incomplete.');
  }
  return root;
}

function isolatedProfileArgument(argv) {
  const positions = argv.flatMap((value, index) => value === '--isolated-profile' ? [index] : []);
  if (!positions.length) return null;
  if (positions.length !== 1 || !UUID.test(argv[positions[0] + 1] || '')) throw new Error('Invalid isolated profile identity.');
  return argv[positions[0] + 1];
}

/** Native bootstrap is obtained directly from our bundled executable, not HTTP/env. */
function createBoundaries(record, expectedPackageRoot) {
  if (!record || record.schema !== 1 || record.profile_id !== 'installer-dev' || record.build !== BUILD ||
      typeof record.package_root !== 'string' || typeof record.project_root !== 'string' ||
      path.resolve(record.package_root).toLowerCase() !== path.resolve(expectedPackageRoot).toLowerCase() ||
      path.resolve(record.project_root).toLowerCase() !== path.resolve(expectedPackageRoot).toLowerCase() ||
      (record.profile_instance !== null && !UUID.test(record.profile_instance || ''))) {
    throw new Error('The bundled native bootstrap identity is incompatible.');
  }
  const PROJECT = checkedPath(record.package_root, record.package_root);
  const PROFILE = checkedPath(record.profile_root, record.profile_root);
  const WINDOWS = checkedPath(record.windows_root, record.windows_root);
  const instance = record.profile_instance;
  const canonical = instance ? path.dirname(path.dirname(PROFILE)) : PROFILE;
  if (path.basename(canonical) !== APP_NAME || (instance &&
      (path.basename(PROFILE) !== instance || path.basename(path.dirname(PROFILE)) !== 'profiles')) ||
      contains(PROJECT, PROFILE) || contains(PROFILE, PROJECT)) {
    throw new Error('Program resources and user data must have independent roots.');
  }

function assertInheritedPaths(environment) {
  for (const [name, value] of Object.entries(environment)) {
    if (!value) continue;
    if (name === 'PODCUT_PROJECT_ROOT') {
      if (path.resolve(value).toLowerCase() !== PROJECT.toLowerCase()) throw new Error('Inherited packaged identity is invalid.');
    } else if (/^(DATA_DIR|MODELS_DIR|CACHE_DIR|LOG_DIR|TEMP_DIR|RUNTIME_DIR|PODCUT_.*_DIR|PODCUT_PROFILE_ROOT|PODCUT_.*ENV_FILE)$/i.test(name)) {
      checkedPath(value, PROFILE);
    } else if (/^(FFMPEG_PATH|FFPROBE_PATH|NODE_PATH|PODCUT_PACKAGED_ROOT)$/i.test(name)) {
      checkedPath(value, PROJECT);
    }
  }
}

function childEnvironment({ packageRoot, runId, token, uiToken, role }, inherited = process.env) {
  checkedPath(packageRoot, PROJECT);
  if (!/^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/i.test(runId) || !/^[a-f\d]{64}$/i.test(token) ||
      !['host', 'backend', 'frontend'].includes(role)) throw new Error('Invalid desktop service identity.');
  const allowed = new Set(['PROCESSOR_ARCHITECTURE',
    'PROCESSOR_IDENTIFIER', 'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL']);
  const env = Object.fromEntries(Object.entries(inherited).filter(([name]) => allowed.has(name.toUpperCase())));
  const value = relative => checkedPath(path.join(PROFILE, relative), PROFILE);
  Object.assign(env, {
    SystemRoot: WINDOWS, WINDIR: WINDOWS, COMSPEC: path.join(WINDOWS, 'System32/cmd.exe'), OS: 'Windows_NT', PATHEXT: '.COM;.EXE;.BAT;.CMD',
    PATH: [path.join(packageRoot, 'node'), path.join(packageRoot, 'tools'), path.join(packageRoot, 'backend')].join(path.delimiter),
    PODCUT_PROFILE: 'installer-dev', PODCUT_PROFILE_ID: 'installer-dev', PODCUT_PROJECT_ROOT: PROJECT,
    PODCUT_PROFILE_ROOT: PROFILE, PODCUT_PACKAGED_ROOT: packageRoot,
    PODCUT_RUN_ID: runId, PODCUT_RUN_TOKEN: token,
    DATA_DIR: value('data'), MODELS_DIR: value('models'), CACHE_DIR: value('cache'), LOG_DIR: value('logs'),
    TEMP_DIR: value('temp'), RUNTIME_DIR: value('runtime'),
    PODCUT_CONFIG_DIR: value('config'), PODCUT_ENV_FILE: value('config/backend.env'), PODCUT_BACKEND_ENV_FILE: value('config/backend.env'),
    PODCUT_MODELS_DIR: value('models'), PODCUT_CACHE_DIR: value('cache'), PODCUT_LOG_DIR: value('logs'),
    PODCUT_TEMP_DIR: value('temp'), PODCUT_RUNTIME_DIR: value('runtime'),
    TEMP: value('temp'), TMP: value('temp'), HF_HOME: value('cache/huggingface'),
    HF_HUB_CACHE: value('cache/huggingface/hub'), HUGGINGFACE_HUB_CACHE: value('cache/huggingface/hub'),
    TRANSFORMERS_CACHE: value('cache/huggingface/transformers'), TORCH_HOME: value('cache/torch'),
    TORCH_EXTENSIONS_DIR: value('cache/torch/extensions'), NUMBA_CACHE_DIR: value('cache/numba'),
    XDG_CACHE_HOME: value('cache/xdg'), MPLCONFIGDIR: value('cache/matplotlib'),
    PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
    HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', HF_HUB_DISABLE_TELEMETRY: '1', DO_NOT_TRACK: '1',
    AI_PROVIDER: 'heuristic', NODE_ENV: 'production', HOSTNAME: '127.0.0.1', PORT: '3300',
    PODCUT_FRONTEND_PORT: '3300', PODCUT_BACKEND_PORT: '8300', UVICORN_HOST: '127.0.0.1', UVICORN_PORT: '8300',
    PODCUT_API_ORIGIN: BACKEND, NEXT_TELEMETRY_DISABLED: '1',
    FFMPEG_PATH: checkedPath(path.join(packageRoot, 'tools/ffmpeg.exe'), packageRoot),
    FFPROBE_PATH: checkedPath(path.join(packageRoot, 'tools/ffprobe.exe'), packageRoot),
    NODE_PATH: checkedPath(path.join(packageRoot, 'node/node.exe'), packageRoot),
  });
  if (instance) env.PODCUT_PROFILE_INSTANCE = instance;
  if (role === 'frontend') {
    if (!/^[a-f\d]{64}$/i.test(uiToken || '')) throw new Error('Invalid desktop UI credential.');
    env.PODCUT_UI_TOKEN = uiToken;
  }
  return env;
}

function matchesIdentity(identity, packageRoot, runId) {
  return identity && identity.profile_id === 'installer-dev' && identity.project_root === PROJECT &&
    identity.profile_root === PROFILE && identity.run_id === runId && identity.port === 8300 &&
    identity.frontend_port === 3300 && identity.backend_host === '127.0.0.1' &&
    identity.protocol === 'podcut-installer-dev-v1' && identity.build === BUILD && identity.packaged === true &&
    identity.packaged_root === packageRoot;
}

function isLocalUi(url) {
  try { return new URL(url).origin === FRONTEND; } catch { return false; }
}

function safeDownload(root, requestedName) {
  const name = path.basename(requestedName || 'clipclip-download.mp4').replace(/[^A-Za-z\d._-]/g, '_').slice(-120);
  return checkedPath(path.join(root, `${crypto.randomUUID()}-${name}`), PROFILE);
}

return { PROJECT, PROFILE, WINDOWS, instance, FRONTEND, BACKEND, APP_NAME, BUILD, checkedPath,
  directory: (candidate, root = PROFILE) => directory(candidate, root), readPackage,
  assertInheritedPaths, childEnvironment, matchesIdentity, isLocalUi, safeDownload };
}

module.exports = { createBoundaries, checkedPath, directory, readPackage, isolatedProfileArgument,
  FRONTEND, BACKEND, APP_NAME, BUILD };
