throw new Error('Historical random-port proxy test is disabled in Phase 2A. Use the verified Installer DEV launch on ports 3300/8300; see docs/PHASE2A-TESTING.md.');

import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Exercise the real production Route Handler, not an imitation of its logic.
const directory = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const fixture = Buffer.alloc(64 * 1024, 0xa7);
const totalChunks = 256; // 16 MiB: exceeds Next's default 10 MiB rewrite clone limit.
const expectedHash = createHash('sha256');
for (let index = 0; index < totalChunks; index++) expectedHash.update(fixture);
const backend = createServer(async (request, response) => {
  if (request.url === '/api/health') {
    response.setHeader('content-type', 'application/json'); response.end('{"status":"ok"}'); return;
  }
  if (request.url === '/api/range') {
    assert.equal(request.headers.range, 'bytes=4-7');
    response.writeHead(206, { 'content-range': 'bytes 4-7/12', 'accept-ranges': 'bytes', 'content-length': '4', 'content-type': 'video/mp4' });
    response.end('test'); return;
  }
  let bytes = 0;
  const hash = createHash('sha256');
  for await (const chunk of request) { bytes += chunk.length; hash.update(chunk); }
  response.setHeader('content-type', 'application/json'); response.end(JSON.stringify({ bytes, sha256: hash.digest('hex') }));
});
await new Promise(resolveListen => backend.listen(0, '127.0.0.1', resolveListen));
const backendPort = backend.address().port;
const reservation = createServer();
await new Promise(resolveListen => reservation.listen(0, '127.0.0.1', resolveListen));
const frontendPort = reservation.address().port;
await new Promise(resolveClose => reservation.close(resolveClose));
const frontend = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(frontendPort)], {
  cwd: directory,
  env: { ...process.env, PODCUT_API_ORIGIN: `http://127.0.0.1:${backendPort}`, NEXT_TELEMETRY_DISABLED: '1' },
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = '';
frontend.stdout.on('data', data => { logs += data; });
frontend.stderr.on('data', data => { logs += data; });
const base = `http://127.0.0.1:${frontendPort}`;
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { const response = await fetch(`${base}/api/health`); if (response.ok) { ready = true; break; } } catch { /* server booting */ }
    if (frontend.exitCode !== null) throw new Error(logs);
    await new Promise(resolveWait => setTimeout(resolveWait, 100));
  }
  assert.ok(ready, `Production frontend did not start. ${logs}`);
  let index = 0;
  const body = new ReadableStream({ pull(controller) { if (index++ < totalChunks) controller.enqueue(fixture); else controller.close(); } });
  const response = await fetch(`${base}/api/jobs/proxy-test/source`, {
    method: 'POST', headers: { 'content-type': 'application/octet-stream', 'content-length': String(fixture.length * totalChunks) },
    body, duplex: 'half',
  });
  assert.equal(response.status, 200);
  const received = await response.json();
  assert.equal(received.bytes, fixture.length * totalChunks, 'Upload was truncated');
  assert.equal(received.sha256, expectedHash.digest('hex'), 'Upload bytes changed');
  const range = await fetch(`${base}/api/range`, { headers: { range: 'bytes=4-7' } });
  assert.equal(range.status, 206);
  assert.equal(range.headers.get('content-range'), 'bytes 4-7/12');
  assert.equal(range.headers.get('accept-ranges'), 'bytes');
  assert.equal(await range.text(), 'test');
  console.log('PASS: real Next production server streamed all 16 MiB unchanged; HTTP 206 status, Content-Range, Accept-Ranges and bytes preserved.');
} finally {
  frontend.kill();
  backend.closeAllConnections();
  await new Promise(resolveClose => backend.close(resolveClose));
}
