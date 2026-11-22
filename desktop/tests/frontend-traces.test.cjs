'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { PROJECT } = require('../lab-boundaries.cjs');
const { validateTraceFiles, verifyFrontendTraces } = require('../frontend-traces.cjs');

const root = path.join(PROJECT, 'frontend');
const manifest = path.join(root, '.next/server/app/api/[...path]/route.js.nft.json');

test('trace validation rejects profile assets, dotenv and malformed drive paths', () => {
  for (const file of ['../../../../../C:/Users/synthetic-user/AppData/Local/ClipClip.ai/data/project.json',
    'C:/Users/synthetic-user/AppData/Local/ClipClip.ai/config/backend.env',
    'C:/Users/synthetic-user/AppData/Local/PODCUTAI-InstallerDev/config/backend.env',
    '../../../../../../../backend/.env', '../../../../../../.env.local',
    '\\\\server\\share\\private.json']) assert.throws(() => validateTraceFiles(manifest, [file], root));
  assert.throws(() => validateTraceFiles(manifest, null, root));
  validateTraceFiles(manifest, ['../../../../../node_modules/next/package.json'], root);
});

test('the actual standalone trace graph contains only frontend dependencies', () => {
  assert.ok(verifyFrontendTraces(path.join(root, '.next')) > 0);
});
