/** Assemble a private portable desktop directory. No installer/update/signing step. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { packager } from '@electron/packager';
import { flipFuses, FuseVersion, FuseV1Options } from '@electron/fuses';
import { collectLicenseNotices } from './license-inventory.mjs';

const require = createRequire(import.meta.url);
const { PROJECT, PROFILE, checkedPath, directory } = require('./lab-boundaries.cjs');
const { verifyFrontendTraces } = require('./frontend-traces.cjs');
const { relocateFrontend } = require('./relocate-frontend.cjs');
const desktopRoot = path.dirname(fileURLToPath(import.meta.url));
if (desktopRoot !== path.join(PROJECT, 'desktop')) throw new Error('Desktop packaging must run inside the approved DEV tree.');
const args = process.argv.slice(2);
const quality = args[0] === '--quality-upgrade' && args.length === 2 &&
  /^[a-f\d]{8}(-[a-f\d]{4}){3}-[a-f\d]{12}$/.test(args[1]);
if (args.length && !quality) throw new Error('Expected no arguments or --quality-upgrade <build UUID>.');
const run = quality ? checkedPath(path.join(PROJECT, 'quality-upgrade/runs', args[1]), PROJECT) : null;
const frontendRoot = quality ? path.join(run, 'work/frontend') : path.join(PROJECT, 'frontend');

function guardTree(root, allowed = PROJECT) {
  checkedPath(root, allowed);
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = checkedPath(path.join(root, entry.name), allowed);
    if (entry.isSymbolicLink()) throw new Error('Packaging cannot consume linked files.');
    if (entry.isDirectory()) guardTree(file, allowed);
  }
}

function copy(source, target) {
  checkedPath(source, PROJECT); checkedPath(target, PROJECT);
  if (fs.statSync(source).isDirectory()) guardTree(source);
  fs.cpSync(source, target, { recursive: true, dereference: false, force: false, errorOnExist: true });
}

function requiredFile(file) {
  checkedPath(file, PROJECT);
  if (!fs.statSync(file).isFile()) throw new Error(`Required packaging artifact is missing: ${path.basename(file)}`);
  return file;
}

function findMediaTool(name) {
  const tools = checkedPath(path.join(PROJECT, '.tools/ffmpeg'), PROJECT);
  const candidates = [path.join(tools, 'bin', name), ...fs.readdirSync(tools).map(entry => path.join(tools, entry, 'bin', name))];
  return requiredFile(candidates.find(candidate => fs.existsSync(candidate)) || path.join(tools, name));
}

function stripDotenvFiles(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = checkedPath(path.join(root, entry.name), PROJECT);
    if (entry.isDirectory()) stripDotenvFiles(file);
    else if (/^\.env(?:\.|$)/i.test(entry.name)) fs.unlinkSync(file);
  }
}

const outputRoot = directory(quality ? path.join(run, 'dist') : path.join(desktopRoot, 'dist/phase3a'), PROJECT);
const output = checkedPath(path.join(outputRoot, 'ClipClip.ai-win32-x64'), PROJECT);
if (fs.existsSync(output)) throw new Error('Desktop output already exists. Preserve it explicitly before a separate packaging run; this script never overwrites a package.');
const stagedRoot = directory(quality ? path.join(run, 'staging') : path.join(desktopRoot, 'staging'), PROJECT);
const staged = directory(path.join(stagedRoot, `${quality ? 'quality' : 'phase3a'}-${crypto.randomUUID()}`), PROJECT);
const appStage = directory(path.join(staged, 'app'), PROJECT);
const payload = directory(path.join(staged, 'podcut'), PROJECT);
const standalone = checkedPath(path.join(frontendRoot, '.next/standalone'), PROJECT);
const backend = checkedPath(quality ? path.join(run, 'work/backend-dist/PODCUTBackend') :
  path.join(PROJECT, 'desktop/backend/dist-phase3a/PODCUTBackend'), PROJECT);
requiredFile(path.join(standalone, 'server.js'));
requiredFile(path.join(backend, 'PODCUTBackend.exe'));
verifyFrontendTraces(path.join(frontendRoot, '.next'), frontendRoot);
guardTree(standalone);
guardTree(backend);

for (const name of ['main.cjs', 'boundaries.cjs', 'private-http.cjs', 'package.json']) copy(path.join(desktopRoot, 'app', name), path.join(appStage, name));
copy(requiredFile(path.join(desktopRoot, 'assets', 'icon.png')), path.join(appStage, 'icon.png'));
copy(backend, path.join(payload, 'backend'));
copy(standalone, path.join(payload, 'frontend'));
const front = path.join(payload, 'frontend');
copy(path.join(frontendRoot, 'public'), path.join(front, 'public'));
copy(path.join(frontendRoot, '.next/static'), path.join(front, '.next/static'));
if (!fs.existsSync(path.join(front, 'desktop-cache.cjs'))) copy(path.join(frontendRoot, 'desktop-cache.cjs'), path.join(front, 'desktop-cache.cjs'));
// Tracing may include source dotenv files. Runtime credentials belong exclusively
// to the external profile; no dotenv file is shipped in the executable directory.
stripDotenvFiles(front);
relocateFrontend(front, frontendRoot);
const nodeRoot = directory(path.join(payload, 'node'), PROJECT);
copy(path.join(PROJECT, '.tools/node/node.exe'), path.join(nodeRoot, 'node.exe'));
copy(path.join(PROJECT, '.tools/node/LICENSE'), path.join(nodeRoot, 'LICENSE'));
const toolsRoot = directory(path.join(payload, 'tools'), PROJECT);
for (const name of ['ffmpeg.exe', 'ffprobe.exe']) copy(findMediaTool(name), path.join(toolsRoot, name));
const ffmpegPackage = path.dirname(path.dirname(findMediaTool('ffmpeg.exe')));
for (const name of ['LICENSE', 'README.txt']) {
  if (fs.existsSync(path.join(ffmpegPackage, name))) copy(path.join(ffmpegPackage, name), path.join(toolsRoot, `FFmpeg-${name}`));
}
fs.writeFileSync(path.join(payload, 'podcut-package.json'), JSON.stringify({ schema: 1, profile_id: 'installer-dev',
  build: 'phase3a', backend: 'backend/PODCUTBackend.exe', frontend: 'frontend/server.js', node: 'node/node.exe',
  ...(quality ? { edition: 'quality-upgrade' } : {}),
  ffmpeg: 'tools/ffmpeg.exe', ffprobe: 'tools/ffprobe.exe', no_installer: true, no_updater: true }, null, 2));
fs.writeFileSync(path.join(payload, 'PRIVATE-DEVELOPMENT-BUILD.txt'),
  `ClipClip.ai private ${quality ? 'Quality Upgrade' : 'Phase 3A'} desktop build. Bundled FFmpeg and PyAV codec libraries require corresponding-source/license review before public redistribution. User/model data lives outside the program directory.\n`);
console.log(`Retained third-party notices: ${collectLicenseNotices(payload)}`);

const scratch = run || PROFILE;
const temporaryRoot = directory(path.join(scratch, 'temp/electron-packager'), scratch);
const electronCache = directory(path.join(scratch, 'cache/electron'), scratch);
const packages = await packager({ dir: appStage, name: 'ClipClip.ai', executableName: 'ClipClip.ai',
  icon: requiredFile(path.join(desktopRoot, 'assets', 'icon.ico')),
  out: outputRoot, platform: 'win32', arch: 'x64', electronVersion: '45.3.4', asar: true,
  ...(quality ? { electronZipDir: checkedPath(path.join(run, 'work/electron-zip'), run) } : {}),
  overwrite: false, prune: false, derefSymlinks: false, extraResource: [payload], tmpdir: temporaryRoot,
  download: { cacheRoot: electronCache }, appVersion: '1.0.0', buildVersion: '1.0.0',
  win32metadata: { CompanyName: 'ClipClip.ai', ProductName: 'ClipClip.ai',
    FileDescription: 'ClipClip.ai — Studio Klip Podcast Lokal' },
});
if (packages.length !== 1 || path.resolve(packages[0]) !== output) throw new Error('Packager produced an unexpected output directory.');
const executable = requiredFile(path.join(output, 'ClipClip.ai.exe'));
await flipFuses(executable, { version: FuseVersion.V1,
  [FuseV1Options.RunAsNode]: false, [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
  [FuseV1Options.EnableNodeCliInspectArguments]: false, [FuseV1Options.OnlyLoadAppFromAsar]: true,
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true, [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
});
guardTree(output);
const reportRoot = run || directory(path.join(PROFILE, 'runtime/phase3a'));
const inventory = [];
function inventoryDirectory(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const item = checkedPath(path.join(root, entry.name), PROJECT);
    if (entry.isDirectory()) inventoryDirectory(item);
    else inventory.push({ path: path.relative(output, item).replaceAll('\\', '/'), bytes: fs.statSync(item).size,
      sha256: crypto.createHash('sha256').update(fs.readFileSync(item)).digest('hex') });
  }
}
inventoryDirectory(output);
fs.writeFileSync(path.join(reportRoot, 'desktop-package-manifest.json'), JSON.stringify({ schema: 1, output,
  electron: '45.3.4', node: '24.22.3', created_at: new Date().toISOString(), files: inventory,
  ...(quality ? { edition: 'quality-upgrade' } : {}),
  total_bytes: inventory.reduce((sum, file) => sum + file.bytes, 0), installer: false, updater: false }, null, 2),
  quality ? { flag: 'wx' } : undefined);
console.log(`Desktop directory ready: ${output}`);
console.log(`Files: ${inventory.length}; bytes: ${inventory.reduce((sum, file) => sum + file.bytes, 0)}`);
