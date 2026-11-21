'use strict';
// Build/test tooling only. This module is never copied into the desktop app.
const fs = require('node:fs');
const path = require('node:path');
const { checkedPath, directory: makeDirectory } = require('./app/boundaries.cjs');
const root = path.resolve(__dirname, '..');
const PROJECT = checkedPath(root, root);
const markerPath = checkedPath(path.join(PROJECT, 'podcut-dev-workspace.json'), PROJECT);
const marker = JSON.parse(fs.readFileSync(markerPath, 'utf8'));
if (marker.schema !== 1 || marker.kind !== 'podcut-multipc-dev') {
  throw new Error('Build tooling requires a Multi-PC development workspace.');
}
const PROFILE = checkedPath(path.join(PROJECT, '.dev', 'profile'), PROJECT);
module.exports = { PROJECT, PROFILE, checkedPath,
  directory: (candidate, root = PROFILE) => makeDirectory(candidate, root) };
