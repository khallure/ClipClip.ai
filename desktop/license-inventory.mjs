import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { PROJECT, checkedPath, directory } = require('./lab-boundaries.cjs');

/** Keep notices omitted by runtime file tracing; this is not a source offer. */
export function collectLicenseNotices(payload) {
  const destination = directory(path.join(payload, 'licenses'), PROJECT);
  const notices = [];
  const seen = new Set();
  function copyFile(source, relative) {
    checkedPath(source, PROJECT);
    const target = checkedPath(path.join(destination, relative), PROJECT);
    if (seen.has(target)) return;
    directory(path.dirname(target), PROJECT);
    fs.copyFileSync(source, target, fs.constants.COPYFILE_EXCL);
    notices.push({ path: relative.replaceAll('\\', '/'), bytes: fs.statSync(source).size });
    seen.add(target);
  }
  function licenseFiles(source, relative, recurse = false) {
    checkedPath(source, PROJECT);
    if (!fs.existsSync(source)) return;
    for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
      if (entry.name === '__pycache__') continue;
      const file = checkedPath(path.join(source, entry.name), PROJECT);
      if (entry.isSymbolicLink()) throw new Error('Linked license input refused.');
      if (entry.isFile() && /^(licen[sc]e|copying|notice|authors)(?:[._-]|$)/i.test(entry.name)) {
        copyFile(file, path.join(relative, entry.name));
      } else if (entry.isDirectory() && (recurse || /^licen[sc]es?$/i.test(entry.name))) {
        licenseFiles(file, path.join(relative, entry.name), true);
      }
    }
  }
  const bundled = path.join(payload, 'frontend/node_modules');
  const sourceModules = path.join(PROJECT, 'frontend/node_modules');
  const pending = [bundled];
  const packages = [];
  while (pending.length) {
    const current = checkedPath(pending.pop(), PROJECT);
    const metadata = path.join(current, 'package.json');
    if (fs.existsSync(metadata)) {
      const relative = path.relative(bundled, current);
      const source = checkedPath(path.join(sourceModules, relative), PROJECT);
      if (fs.existsSync(path.join(source, 'package.json'))) {
        const pkg = JSON.parse(fs.readFileSync(metadata, 'utf8'));
        const original = JSON.parse(fs.readFileSync(path.join(source, 'package.json'), 'utf8'));
        if (pkg.name !== original.name || pkg.version !== original.version) throw new Error('License package version mismatch.');
        const before = notices.length;
        licenseFiles(source, path.join('frontend', relative));
        packages.push({ name: pkg.name || relative, version: pkg.version || null,
          license: original.license || null, notice_files: notices.length - before });
      }
    }
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.isDirectory()) pending.push(checkedPath(path.join(current, entry.name), PROJECT));
      else if (entry.isSymbolicLink()) throw new Error('Linked bundled package refused.');
    }
  }
  // Next also distributes embedded vendors without separate top-level packages.
  licenseFiles(path.join(sourceModules, 'next/dist/compiled'), 'frontend/next/dist/compiled', true);
  const sitePackages = path.join(PROJECT, '.venv/Lib/site-packages');
  for (const entry of fs.readdirSync(sitePackages, { withFileTypes: true })) {
    if (entry.isDirectory() && entry.name.endsWith('.dist-info')) {
      licenseFiles(path.join(sitePackages, entry.name), path.join('python', entry.name));
    }
  }
  copyFile(path.join(PROJECT, '.tools/python/LICENSE.txt'), 'Python-LICENSE.txt');
  copyFile(path.join(PROJECT, 'backend/assets/fonts/OFL.txt'), 'Poppins-OFL.txt');
  copyFile(path.join(PROJECT, 'backend/assets/models/LICENSE.YuNet.txt'), 'YuNet-MIT.txt');
  fs.writeFileSync(path.join(destination, 'inventory.json'), JSON.stringify({
    packages, files: notices, notice: 'License retention for a private development build. This inventory does not establish GPL corresponding-source completeness or authorize publication.'
  }, null, 2));
  if (!notices.length) throw new Error('No third-party notices were retained.');
  return notices.length;
}
