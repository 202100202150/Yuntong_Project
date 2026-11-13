const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '..', '..');
const runtimeRoot = process.env.YUNTONG_SMOKE_RUNTIME || root;
const runDirectory = path.join(root, 'desktop-app', 'work', 'product-smoke-' + Date.now());
fs.mkdirSync(runDirectory, { recursive: true });
app.setPath('userData', path.join(runDirectory, 'profile'));
let backend;
let window;
let finished = false;
const timeout = setTimeout(() => finish(new Error('Electron smoke test timed out')), 45000);
async function finish(error) {
  if (finished) return;
  finished = true;
  clearTimeout(timeout);
  if (window && !window.isDestroyed()) window.destroy();
  try { await backend?.close(); } catch (closeError) { error ||= closeError; }
  if (error) console.error(error.stack || error);
  app.exit(error ? 1 : 0);
}
app.whenReady().then(async () => {
  const { startBackend } = await import(pathToFileURL(path.join(runtimeRoot, 'backend', 'src', 'index.mjs')).href);
  backend = await startBackend({
    port: 0, host: '127.0.0.1',
    staticDirectory: path.join(runtimeRoot, 'frontend', 'dist'),
    dataDirectory: path.join(runDirectory, 'data'),
    startBridge: false,
  });
  window = new BrowserWindow({
    show: false, width: 1672, height: 941, useContentSize: true,
    autoHideMenuBar: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  });
  window.setMenu(null);
  window.setContentSize(1672, 941);
  const errors = [];
  window.webContents.on('console-message', event => { if (event.level === 'error') errors.push(event.message); });
  await window.loadURL(backend.url);
  await new Promise(resolve => setTimeout(resolve, 1200));
  const first = await window.webContents.executeJavaScript(`(async()=>({
    text: document.body.innerText,
    requireType: typeof require,
    processType: typeof process,
    viewport: {width: innerWidth, height: innerHeight},
    scripts: document.querySelectorAll('script[type="module"]').length,
    setup: await fetch('/api/auth/status').then(r=>r.json())
  }))()`);
  assert.ok(first.text.includes('云瞳'), 'shared Vue application was not rendered');
  assert.equal(first.requireType, 'undefined');
  assert.equal(first.processType, 'undefined');
  assert.deepEqual(first.viewport, {width:1672, height:941});
  assert.ok(first.scripts > 0);
  assert.equal(first.setup.setupRequired, true);
  const outputDirectory = process.env.YUNTONG_SMOKE_OUTPUT || runDirectory;
  fs.mkdirSync(outputDirectory, { recursive: true });
  fs.writeFileSync(path.join(outputDirectory, 'electron-login.png'), (await window.webContents.capturePage()).toPNG());
  const setup = await window.webContents.executeJavaScript(`(async()=> {
    const response = await fetch('/api/auth/setup', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:'smokeadmin',displayName:'系统管理员',password:'SmokeTest-Only-123!'})
    });
    return { status:response.status, body:await response.json(), session:await fetch('/api/auth/status').then(r=>r.json()) };
  })()`);
  assert.equal(setup.status, 201);
  assert.equal(setup.session.user.username, 'smokeadmin');
  assert.ok(setup.session.user.permissions.includes('devices'));
  await window.loadURL(new URL('/home', backend.url).href);
  await new Promise(resolve => setTimeout(resolve, 1500));
  const dashboard = await window.webContents.executeJavaScript('document.body.innerText');
  fs.writeFileSync(path.join(outputDirectory, 'electron-overview.png'), (await window.webContents.capturePage()).toPNG());
  assert.ok(dashboard.includes('运行总览'), 'authenticated overview was not rendered: ' + JSON.stringify({text:dashboard.slice(0,1500),errors}));
  console.log(JSON.stringify({ ok:true, electron:process.versions.electron, node:process.versions.node, sandbox:true, sqlite:true, sameOriginSession:true, viewport:first.viewport, runtimeRoot, screenshotDirectory:outputDirectory, consoleErrors:errors }, null, 2));
  await finish();
}).catch(finish);
