'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { PROJECT, checkedPath } = require('./lab-boundaries.cjs');

function validateTraceFiles(manifestPath, files, frontendRoot) {
  checkedPath(manifestPath, frontendRoot);
  if (!Array.isArray(files)) throw new Error('Invalid frontend trace file list.');
  for (const relative of files) {
    if (typeof relative !== 'string' || /[A-Za-z]:/.test(relative) || path.isAbsolute(relative)) {
      throw new Error('Frontend trace contains an external absolute path; packaging stopped.');
    }
    checkedPath(path.resolve(path.dirname(manifestPath), relative), frontendRoot);
    if (/^\.env(?:\.|$)/i.test(path.basename(relative))) throw new Error('Frontend trace contains a dotenv file; packaging stopped.');
  }
}

function verifyFrontendTraces(nextDirectory, sourceRoot = path.join(PROJECT, 'frontend')) {
  const frontendRoot = checkedPath(sourceRoot, PROJECT);
  let manifests = 0;
  function inspect(directoryPath) {
    for (const entry of fs.readdirSync(directoryPath, { withFileTypes: true })) {
      const file = checkedPath(path.join(directoryPath, entry.name), frontendRoot);
      if (entry.isDirectory()) {
        if (!['standalone', 'cache', 'static'].includes(entry.name)) inspect(file);
      } else if (entry.name.endsWith('.nft.json')) {
        const trace = JSON.parse(fs.readFileSync(file, 'utf8'));
        validateTraceFiles(file, trace.files, frontendRoot);
        manifests++;
      }
    }
  }
  inspect(checkedPath(nextDirectory, frontendRoot));
  if (!manifests) throw new Error('The standalone build has no trace manifests.');
  return manifests;
}

module.exports = { validateTraceFiles, verifyFrontendTraces };
