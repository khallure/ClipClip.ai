'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { privateIdentityRequest } = require('../app/private-http.cjs');

async function listener(handler) {
  const server = http.createServer(handler);
  const sockets = new Set();
  server.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)); });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(3300, '127.0.0.1', resolve); });
  return { server, sockets, close: async () => { for (const socket of sockets) socket.destroy(); await new Promise(resolve => server.close(resolve)); } };
}

test('a foreign connected peer receives no HTTP request or credential when native ownership proof fails', async () => {
  let requests = 0, receivedBytes = 0;
  const fixture = await listener((_req, response) => { requests++; response.end('{}'); });
  fixture.server.on('connection', socket => socket.on('data', bytes => { receivedBytes += bytes.length; }));
  try {
    await assert.rejects(privateIdentityRequest({ base: 'http://127.0.0.1:3300', role: 'frontend', pid: 123,
      headers: { 'x-podcut-ui-token': 'synthetic-not-a-real-credential' },
      prove: async connection => ({ pids: { frontend: 123 }, connection: { ...connection, owned: false, pid: 456, role: null } }) }), /No credential was sent/);
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(requests, 0);
    assert.equal(receivedBytes, 0);
  } finally { await fixture.close(); }
});

test('verified private HTTP uses the same proven connection and returns bounded JSON', async () => {
  let connections = 0, requests = 0, verifiedClientPort;
  const fixture = await listener((request, response) => {
    requests++;
    assert.equal(request.url, '/api/runtime-identity');
    assert.equal(request.socket.remotePort, verifiedClientPort);
    assert.equal(request.headers['x-podcut-ui-token'], 'synthetic-test-credential');
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({ example: 'owned-fixture' }));
  });
  fixture.server.on('connection', () => connections++);
  try {
    const response = await privateIdentityRequest({ base: 'http://127.0.0.1:3300', role: 'frontend', pid: 123,
      headers: { 'x-podcut-ui-token': 'synthetic-test-credential' },
      prove: async connection => {
        verifiedClientPort = connection.client_port;
        return { pids: { frontend: 123 }, connection: { ...connection, owned: true, pid: 123, role: 'frontend' } };
      } });
    assert.deepEqual(response.body, { example: 'owned-fixture' });
    assert.equal(connections, 1);
    assert.equal(requests, 1);
  } finally { await fixture.close(); }
});

test('private HTTP never follows redirects or accepts oversized identity bodies', async () => {
  for (const mode of ['redirect', 'oversized']) {
    let requests = 0;
    const fixture = await listener((_request, response) => {
      requests++;
      if (mode === 'redirect') { response.writeHead(302, { location: 'http://127.0.0.1:8300/secret' }); response.end(); }
      else { response.setHeader('content-type', 'application/json'); response.end('x'.repeat(70000)); }
    });
    try {
      await assert.rejects(privateIdentityRequest({ base: 'http://127.0.0.1:3300', role: 'frontend', pid: 123,
        headers: {}, prove: async connection => ({ pids: { frontend: 123 }, connection: { ...connection, owned: true, pid: 123, role: 'frontend' } }) }));
      assert.equal(requests, 1);
    } finally { await fixture.close(); }
  }
});
