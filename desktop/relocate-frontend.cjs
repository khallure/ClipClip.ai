'use strict';
// Packaging-time rewrite of Next's generated build-directory metadata only.
const fs = require('node:fs');
const path = require('node:path');
const { checkedPath, PROJECT } = require('./lab-boundaries.cjs');

function portableConfig(input) {
  const result = structuredClone(input);
  result.outputFileTracingRoot = '.';
  result.repoRoot = '.';
  if (result.turbopack) result.turbopack.root = '.';
  return result;
}

function relocateFrontend(root, sourceRoot = path.join(PROJECT, 'frontend')) {
  checkedPath(root, PROJECT);
  const server = checkedPath(path.join(root, 'server.js'), root);
  const source = fs.readFileSync(server, 'utf8');
  const expression = /^const nextConfig = (.+)\r?$/m;
  const found = expression.exec(source);
  if (!found) throw new Error('Unrecognized Next standalone bootstrap; refusing an unsafe packaging rewrite.');
  const config = portableConfig(JSON.parse(found[1].replace(/;$/, '')));
  const replacement = `const nextConfig = ${JSON.stringify(config)}\n` +
    'nextConfig.outputFileTracingRoot = __dirname; nextConfig.repoRoot = __dirname;\n' +
    'if (nextConfig.turbopack) nextConfig.turbopack.root = __dirname;';
  fs.writeFileSync(server, source.replace(expression, () => replacement));
  const metadata = checkedPath(path.join(root, '.next/required-server-files.json'), root);
  const required = JSON.parse(fs.readFileSync(metadata, 'utf8'));
  required.config = portableConfig(required.config);
  required.appDir = '.';
  fs.writeFileSync(metadata, JSON.stringify(required, null, 2));
  normalizeComponentIds(root, sourceRoot);
}

function normalizeComponentIds(root, sourceRoot = path.join(PROJECT, 'frontend')) {
  // Next emits the same absolute component IDs in both RSC references and
  // their lookup tables. Normalize both sides in the staged build only.
  const frontend = checkedPath(sourceRoot, PROJECT);
  const escaped = JSON.stringify(frontend).slice(1,-1);
  const prefixes = [JSON.stringify(escaped).slice(1,-1), escaped, frontend.replaceAll('\\','/')];
  const pending = [path.join(root,'.next')];
  while (pending.length) {
    for (const entry of fs.readdirSync(pending.pop(), {withFileTypes:true})) {
      const file = checkedPath(path.join(entry.parentPath, entry.name), root);
      if (entry.isDirectory()) { pending.push(file); continue; }
      if (!/\.(js|json|html|rsc|txt)$/.test(entry.name)) continue;
      const previous = fs.readFileSync(file,'utf8');
      let updated = previous;
      for (const prefix of prefixes) updated = updated.split(prefix).join('.');
      if (updated !== previous) fs.writeFileSync(file,updated);
    }
  }
}

module.exports = { portableConfig, relocateFrontend, normalizeComponentIds };
