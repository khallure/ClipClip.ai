'use strict';

const http = require('node:http');
const net = require('node:net');

/** Connect without credentials, prove the actual TCP peer, then use that socket. */
async function privateIdentityRequest({ base, role, pid, headers, prove }) {
  const expected = role === 'backend' ? 'http://127.0.0.1:8300' : role === 'frontend' ? 'http://127.0.0.1:3300' : '';
  if (base !== expected || !expected || !Number.isInteger(pid) || pid <= 0) throw new Error('Invalid private desktop HTTP target.');
  const port = role === 'backend' ? 8300 : 3300;
  const socket = net.createConnection({ host: '127.0.0.1', port });
  socket.setTimeout(1500, () => socket.destroy(new Error('Local readiness connection timed out.')));
  try {
    await new Promise((resolve, reject) => { socket.once('connect', resolve); socket.once('error', reject); });
    socket.pause();
    const proof = await prove({ port, client_port: socket.localPort });
    const connection = proof.connection;
    if (!connection || connection.port !== port || connection.client_port !== socket.localPort ||
        connection.owned !== true || connection.role !== role || connection.pid !== pid ||
        proof.pids?.[role] !== pid || socket.destroyed) {
      throw new Error('The connected local socket does not belong to this desktop service. No credential was sent.');
    }
    return await new Promise((resolve, reject) => {
      const agent = new http.Agent({ keepAlive: false, maxSockets: 1 });
      // Never reconnect or retry against a listener that might have rebound.
      agent.createConnection = (_options, callback) => { callback(null, socket); };
      const req = http.get(`${base}/api/runtime-identity`, { headers, timeout: 1500, agent }, response => {
        const chunks = []; let length = 0;
        response.on('data', chunk => {
          length += chunk.length;
          if (length > 65536) { response.destroy(); reject(new Error('The private local identity response is too large.')); }
          else chunks.push(chunk);
        });
        response.on('end', () => {
          if (response.statusCode !== 200) return reject(new Error('The private local service did not accept its launch credential.'));
          try { resolve({ body: JSON.parse(Buffer.concat(chunks).toString('utf8')), headers: response.headers }); }
          catch { reject(new Error('The private local service returned an invalid identity.')); }
        });
        response.on('error', reject);
        response.on('aborted', () => reject(new Error('The private local identity response was interrupted.')));
      });
      req.on('error', reject);
      req.on('timeout', () => req.destroy(new Error('Local readiness request timed out.')));
      req.once('close', () => agent.destroy());
      socket.resume();
    });
  } finally { socket.destroy(); }
}

module.exports = { privateIdentityRequest };
