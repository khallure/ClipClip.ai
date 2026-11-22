'use strict';

const { app, BrowserWindow, Menu, dialog, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const { spawn, spawnSync } = require('node:child_process');
const readline = require('node:readline');
const { createBoundaries, readPackage, isolatedProfileArgument, validatePendingUpdate, FRONTEND, BACKEND, APP_NAME, BUILD } = require('./boundaries.cjs');
const { privateIdentityRequest } = require('./private-http.cjs');

let PROFILE, PROJECT, checkedPath, directory, assertInheritedPaths, childEnvironment,
  matchesIdentity, isLocalUi, safeDownload, profileInstance;

function nativeBootstrap() {
  if (!app.isPackaged) throw new Error('Use the packaged desktop executable; source Electron runs are disabled.');
  const packageRoot = readPackage(process.resourcesPath, process.execPath);
  const selected = isolatedProfileArgument(process.argv);
  const args = ['--bootstrap-info', ...(selected ? ['--isolated-profile', selected] : [])];
  const inherited = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    ['SYSTEMROOT', 'WINDIR', 'OS', 'PROCESSOR_ARCHITECTURE'].includes(key.toUpperCase())));
  const response = spawnSync(path.join(packageRoot, 'backend/PODCUTBackend.exe'), args, {
    cwd: path.join(packageRoot, 'backend'), env: inherited, windowsHide: true, shell: false,
    encoding: 'utf8', timeout: 20000, maxBuffer: 32768,
  });
  if (response.error || response.status !== 0) throw new Error('Runtime ClipClip.ai tidak lengkap atau lokasi profil tidak aman. Gunakan folder paket utuh.');
  let record;
  try { record = JSON.parse(response.stdout); } catch { throw new Error('Native runtime bootstrap returned an invalid identity.'); }
  const boundary = createBoundaries(record, packageRoot);
  if (boundary.instance !== selected) throw new Error('Native profile instance did not match the requested instance.');
  ({ PROFILE, PROJECT, checkedPath, directory, assertInheritedPaths, childEnvironment,
    matchesIdentity, isLocalUi, safeDownload } = boundary);
  profileInstance = selected;
}

let host;
let mainWindow;
let shutdownPromise;
let allowQuit = false;
let logPath;
let runtimeRecord;
let runId;
const pending = new Map();
const owned = new Map();
let hostReady = false;
let hostExited = false;
let jobClosedCleanly = false;
let updateTimer = null;
let updateApplying = false;

function log(message) {
  if (logPath) fs.appendFileSync(checkedPath(logPath, PROFILE), `${new Date().toISOString()} ${message}\n`);
}

function writeRuntime(status, extra = {}) {
  if (!runtimeRecord) return;
  const record = { schema: 1, profile_id: 'installer-dev', build: BUILD, run_id: runId,
    desktop_pid: process.pid, host_pid: host?.pid || null, services: Object.fromEntries(owned), status,
    updated_at: new Date().toISOString(), ...extra };
  const temporary = checkedPath(`${runtimeRecord}.tmp`, PROFILE);
  fs.writeFileSync(temporary, JSON.stringify(record, null, 2));
  fs.renameSync(temporary, checkedPath(runtimeRecord, PROFILE));
}

function waitEvent(key, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(key); reject(new Error(`Desktop service did not acknowledge ${key}.`)); }, timeout);
    pending.set(key, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
  });
}

function emitHost(message) {
  if (!host || hostExited || !host.stdin.writable) throw new Error('Desktop process supervisor is unavailable.');
  host.stdin.write(`${JSON.stringify(message)}\n`);
}

async function status(connection) {
  const nonce = crypto.randomUUID();
  const key = `status:${nonce}`;
  const awaiting = waitEvent(key, 4000);
  try { emitHost({ op: 'status', nonce, ...(connection ? { connection } : {}) }); }
  catch (error) { const waiter = pending.get(key); pending.delete(key); waiter?.reject(error); }
  return awaiting;
}

async function verifyBothListeners() {
  const proof = await status();
  for (const [role, port] of [['backend', 8300], ['frontend', 3300]]) {
    const rows = proof.listeners?.[String(port)];
    if (!owned.has(role) || proof.pids?.[role] !== owned.get(role) || !Array.isArray(rows) || !rows.length ||
        rows.some(row => row.owned !== true || row.role !== role || row.pid !== owned.get(role))) {
      throw new Error('A local desktop listener no longer belongs to this run.');
    }
  }
}

async function available(port) {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', () => reject(new Error(`Port lokal ${port} sedang dipakai. Tutup instance lain yang memakainya; ClipClip.ai tidak akan menghentikan proses lain.`)));
    probe.listen({ host: '127.0.0.1', port, exclusive: true }, () => probe.close(resolve));
  });
}

async function ready(url, headers, packageRoot, frontend = false) {
  const began = Date.now();
  while (Date.now() - began < 60000) {
    if (hostExited || !hostReady) throw new Error('The desktop service supervisor exited during startup.');
    if (!owned.has(frontend ? 'frontend' : 'backend')) throw new Error('An owned desktop service exited during startup.');
    try {
      const role = frontend ? 'frontend' : 'backend';
      const response = await privateIdentityRequest({ base: url, role, pid: owned.get(role), headers, prove: status });
      if (matchesIdentity(response.body, packageRoot, runId) &&
          (!frontend || response.headers['x-podcut-proxy-profile'] === 'installer-dev' &&
            response.headers['x-podcut-proxy-run-id'] === runId)) return;
    } catch { /* A bounded retry is allowed only while our own supervisor lives. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('Service ClipClip.ai belum siap. Lihat log desktop pada profil aplikasi.');
}

async function shutdown() {
  if (updateTimer) { clearInterval(updateTimer); updateTimer = null; }
  if (shutdownPromise) return shutdownPromise;
  shutdownPromise = (async () => {
    writeRuntime('stopping');
    if (host && !hostExited) {
      try { emitHost({ op: 'shutdown' }); host.stdin.end(); } catch { /* EOF also closes the owned job. */ }
      const exited = await Promise.race([
        new Promise(resolve => host.once('exit', () => resolve(true))),
        new Promise(resolve => setTimeout(() => resolve(false), 18000)),
      ]);
      if (!exited && !hostExited) {
        // This ChildProcess handle is our own directly-created supervisor only.
        // Its non-inherited Job Object closes on termination and stops its children.
        host.kill();
        await new Promise(resolve => { if (hostExited) resolve(); else { host.once('exit', resolve); setTimeout(resolve, 3000); } });
      }
    }
    owned.clear();
    writeRuntime(hostExited || !host ? 'stopped' : 'cleanup-unconfirmed',
      { cleanup_confirmed: !host || jobClosedCleanly, supervisor_exited: hostExited });
    log('Desktop shutdown completed; no port, process-name or external PID stop was used.');
  })();
  return shutdownPromise;
}

async function startSupervisor(packageRoot, token) {
  const executable = checkedPath(path.join(packageRoot, 'backend/PODCUTBackend.exe'), packageRoot);
  const work = directory(path.join(PROFILE, `temp/desktop-${runId}`));
  const awaiting = waitEvent('ready');
  host = spawn(executable, ['--job-host', '--package-root', packageRoot, '--run-id', runId, '--profile-root', PROFILE,
    ...(profileInstance ? ['--isolated-profile', profileInstance] : [])], {
    cwd: work, env: childEnvironment({ packageRoot, runId, token, role: 'host' }), windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'], shell: false,
  });
  host.on('error', error => {
    hostExited = true;
    log('Supervisor could not start.');
    for (const waiter of pending.values()) waiter.reject(new Error('The bundled process supervisor could not start.'));
    pending.clear();
  });
  host.on('exit', (code, signal) => {
    hostExited = true; hostReady = false;
    log(`Supervisor exited code=${code} signal=${signal || 'none'}.`);
    for (const waiter of pending.values()) waiter.reject(new Error('The bundled process supervisor stopped.'));
    pending.clear();
    if (!shutdownPromise && mainWindow) { dialog.showErrorBox('ClipClip.ai', 'Service lokal berhenti. Buka ulang ClipClip.ai dan periksa log.'); app.quit(); }
  });
  host.stderr.on('data', data => {
    // Supervisor errors are deliberately fixed messages; no command/environment dump.
    log(`Supervisor diagnostic: ${String(data).slice(0, 1500).replace(/[\r\n]+/g, ' ')}`);
  });
  host.stdin.on('error', () => {});
  readline.createInterface({ input: host.stdout }).on('line', line => {
    try {
      if (line.length > 32768) throw new Error('oversized');
      const message = JSON.parse(line);
      if (message.event === 'ready') hostReady = true;
      if (message.event === 'closed') jobClosedCleanly = message.clean === true && message.active_processes === 0;
      if (message.event === 'started' && ['backend', 'frontend'].includes(message.id) && Number.isInteger(message.pid) && message.pid > 0) owned.set(message.id, message.pid);
      if (message.event === 'exit' && ['backend', 'frontend'].includes(message.id)) {
        owned.delete(message.id);
        if (!shutdownPromise) { log(`Owned ${message.id} exited.`); if (mainWindow) app.quit(); }
      }
      const key = message.event === 'started' ? `started:${message.id}` :
        message.event === 'status' ? `status:${message.nonce}` : message.event;
      const waiter = pending.get(key);
      if (waiter) { pending.delete(key); waiter.resolve(message); }
      if (message.event === 'error') {
        for (const item of pending.values()) item.reject(new Error('The private process supervisor rejected a service request.'));
        pending.clear();
      }
      writeRuntime('starting');
    } catch { log('Supervisor emitted an invalid control record.'); host.stdin.end(); }
  });
  await awaiting;
}

async function startService(id, packageRoot, token, uiToken) {
  const executable = path.join(packageRoot, id === 'backend' ? 'backend/PODCUTBackend.exe' : 'node/node.exe');
  const args = id === 'backend' ? ['--serve', '--run-id', runId, ...(profileInstance ? ['--isolated-profile', profileInstance] : [])] : [path.join(packageRoot, 'frontend/server.js')];
  const cwd = id === 'backend' ? directory(path.join(PROFILE, `temp/desktop-${runId}/backend`)) : path.join(packageRoot, 'frontend');
  const awaiting = waitEvent(`started:${id}`);
  emitHost({ op: 'start', id, executable: checkedPath(executable, packageRoot), args, cwd,
    env: childEnvironment({ packageRoot, runId, token, uiToken, role: id }) });
  await awaiting;
}

function browser(token) {
  const browserSession = session.fromPartition(`podcut-installer-dev-${runId}`, { cache: false });
  browserSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  browserSession.setPermissionCheckHandler(() => false);
  mainWindow = new BrowserWindow({ width: 1450, height: 960, minWidth: 980, minHeight: 700,
    show: false, title: 'ClipClip.ai — Studio Klip Podcast', icon: path.join(__dirname, 'icon.png'), autoHideMenuBar: true,
    webPreferences: { session: browserSession, sandbox: true, nodeIntegration: false,
      contextIsolation: true, webSecurity: true, devTools: false, spellcheck: false,
      allowRunningInsecureContent: false, navigateOnDragDrop: false },
  });
  const contentsId = mainWindow.webContents.id;
  browserSession.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: details.webContentsId !== contentsId || !isLocalUi(details.url) });
  });
  browserSession.webRequest.onBeforeSendHeaders((details, callback) => {
    if (details.webContentsId !== contentsId || !isLocalUi(details.url)) return callback({ cancel: true });
    const headers = { ...details.requestHeaders };
    for (const name of Object.keys(headers)) if (/^x-podcut-(ui|run)-token$/i.test(name)) delete headers[name];
    headers['x-podcut-ui-token'] = token;
    callback({ requestHeaders: headers });
  });
  browserSession.on('will-download', (event, item, contents) => {
    const url = new URL(item.getURL());
    const allowed = /^\/api\/jobs\/[a-f\d]{32}\/exports\/[a-f\d]{32}\/(file|captions\.(srt|ass|vtt))$/.test(url.pathname) ||
      url.pathname === '/api/models/whisper/offline-package';
    if (contents?.id !== contentsId || !isLocalUi(url.href) || !allowed || url.search) {
      event.preventDefault(); return;
    }
    item.setSavePath(safeDownload(directory(path.join(PROFILE, 'data/downloads')), item.getFilename()));
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-attach-webview', event => event.preventDefault());
  mainWindow.webContents.on('will-navigate', (event, url) => { if (!isLocalUi(url)) event.preventDefault(); });
  mainWindow.webContents.on('will-redirect', (event, url) => { if (!isLocalUi(url)) event.preventDefault(); });
  mainWindow.on('page-title-updated', event => { event.preventDefault(); mainWindow.setTitle('ClipClip.ai — Studio Klip Podcast'); });
  mainWindow.webContents.on('render-process-gone', () => app.quit());
  mainWindow.on('closed', () => { mainWindow = null; });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  return mainWindow.loadURL(FRONTEND);
}

function startUpdateWatcher() {
  if (profileInstance || updateTimer) return;
  const handoffPath = checkedPath(path.join(PROFILE, 'runtime', 'pending-update.json'), PROFILE);
  updateTimer = setInterval(async () => {
    if (updateApplying || shutdownPromise || !fs.existsSync(handoffPath)) return;
    updateApplying = true;
    try {
      const checked = checkedPath(handoffPath, PROFILE);
      const statInfo = fs.lstatSync(checked);
      if (statInfo.isSymbolicLink() || !statInfo.isFile() || statInfo.size > 16384) {
        throw new Error('Pending update descriptor is unsafe or oversized.');
      }
      const raw = fs.readFileSync(checked, 'utf8');
      fs.unlinkSync(checked);
      const record = JSON.parse(raw);
      const verified = await validatePendingUpdate(record, {
        profileRoot: PROFILE, runId, currentVersion: app.getVersion(),
      });
      log(`Verified ClipClip.ai update v${verified.version}; shutting down services before installer handoff.`);
      if (updateTimer) { clearInterval(updateTimer); updateTimer = null; }
      await shutdown();
      const child = spawn(
        verified.installerPath,
        ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', '/RELAUNCH=1'],
        { cwd: path.dirname(verified.installerPath), detached: true, stdio: 'ignore', shell: false },
      );
      child.unref();
      allowQuit = true;
      app.exit(0);
    } catch (error) {
      try { if (fs.existsSync(handoffPath)) fs.unlinkSync(checkedPath(handoffPath, PROFILE)); } catch {}
      log(`Rejected pending update handoff: ${error.message}`);
      updateApplying = false;
      if (mainWindow && !shutdownPromise) {
        dialog.showErrorBox('ClipClip.ai', `Update ditolak karena verifikasi integritas gagal: ${error.message}`);
      }
    }
  }, 1500);
}

try {
  nativeBootstrap();
  assertInheritedPaths(process.env);
  checkedPath(PROFILE, PROFILE);
  directory(PROFILE);
  app.setName(APP_NAME);
  for (const [name, relative] of Object.entries({ appData: 'config/electron-appdata', userData: 'config/electron',
    sessionData: 'cache/electron-session', crashDumps: 'logs/crash-dumps', logs: 'logs/electron', temp: 'temp/electron',
    downloads: 'data/downloads' })) app.setPath(name, directory(path.join(PROFILE, relative)));
  process.env.TEMP = directory(path.join(PROFILE, 'temp/electron'));
  process.env.TMP = process.env.TEMP;
  // Phase 2B validates CPU rendering and keeps DirectX/GPU driver cache writes
  // out of the main Windows profile. Backend compute remains a separate service.
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch('disable-component-update');
  app.commandLine.appendSwitch('disable-background-networking');
  app.commandLine.appendSwitch('disable-domain-reliability');
  app.commandLine.appendSwitch('disable-sync');
  app.commandLine.appendSwitch('no-first-run');
  app.commandLine.appendSwitch('disk-cache-dir', directory(path.join(PROFILE, 'cache/electron-http')));
  app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
  app.commandLine.appendSwitch('user-data-dir', app.getPath('userData'));
  app.setAppUserModelId('ai.clipclip.desktop');
  if (!app.requestSingleInstanceLock()) app.exit(0);
  else {
    app.on('second-instance', () => { if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus(); } });
    app.on('before-quit', event => {
      if (allowQuit) return;
      event.preventDefault();
      shutdown().finally(() => { allowQuit = true; app.quit(); });
    });
    app.on('window-all-closed', () => app.quit());
    app.whenReady().then(async () => {
      if (!app.isPackaged) throw new Error('Use the packaged desktop executable; source Electron runs are disabled.');
      const packageRoot = PROJECT;
      runId = crypto.randomUUID();
      const backendToken = crypto.randomBytes(32).toString('hex');
      const uiToken = crypto.randomBytes(32).toString('hex');
      const logRoot = directory(path.join(PROFILE, `logs/desktop-${runId}`));
      logPath = checkedPath(path.join(logRoot, 'desktop.log'), PROFILE);
      runtimeRecord = checkedPath(path.join(directory(path.join(PROFILE, 'runtime/desktop')), `${runId}.json`), PROFILE);
      writeRuntime('preflight');
      Menu.setApplicationMenu(null);
      await available(8300); await available(3300);
      await startSupervisor(packageRoot, backendToken);
      await startService('backend', packageRoot, backendToken);
      await ready(BACKEND, { 'x-podcut-run-token': backendToken }, packageRoot);
      await startService('frontend', packageRoot, backendToken, uiToken);
      await ready(FRONTEND, { 'x-podcut-ui-token': uiToken }, packageRoot, true);
      await verifyBothListeners();
      writeRuntime('ready');
      log('Verified private frontend and backend; opening isolated desktop UI.');
      startUpdateWatcher();
      await browser(uiToken);
    }).catch(async error => {
      log(`Startup failed: ${error.message}`);
      await shutdown();
      dialog.showErrorBox('ClipClip.ai', `${error.message}\n\nAplikasi lain tidak dihentikan. Log: ${path.join(PROFILE, 'logs')}`);
      allowQuit = true; app.quit();
    });
  }
} catch (error) {
  dialog.showErrorBox('ClipClip.ai', error.message);
  app.exit(1);
}
