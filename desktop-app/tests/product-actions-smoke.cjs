const { app, BrowserWindow, dialog } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { once } = require('node:events');
const { randomBytes } = require('node:crypto');

// Exercise the production main entry with an isolated profile and no camera process.
const desktopRoot = path.resolve(__dirname, '..');
fs.mkdirSync(path.join(desktopRoot, 'work'), { recursive: true });
const runDirectory = fs.mkdtempSync(path.join(desktopRoot, 'work', 'product-actions-'));
app.setPath('appData', runDirectory);
process.env.YUNTONG_DATA_DIR = path.join(runDirectory, 'data');
process.env.YUNTONG_CAMERA_PROJECT_DIR = path.join(runDirectory, 'no-camera-project');
process.env.YUNTONG_CAMERA_PYTHON = path.join(runDirectory, 'no-python.exe');
const checks = [], errorDialogs = [], recoveryDialogs = [];
let recoveryResponse = 0, productWindow, origin, closing = false, failed = false;
const timeout = setTimeout(() => fail(new Error('Desktop actions smoke timed out')), 60000);
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
dialog.showErrorBox = (title, content) => { errorDialogs.push({ title, content }); };
dialog.showMessageBox = async (_window, options) => {
  recoveryDialogs.push(options);
  return { response: recoveryResponse, checkboxChecked: false };
};
function fail(error) {
  if (failed) return;
  failed = true; clearTimeout(timeout); console.error(error.stack || error);
  app.exit(1);
}
async function until(fn, message) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await fn()) return;
    await delay(50);
  }
  throw new Error(message);
}
async function download(filename, destination) {
  const completed = new Promise((resolve, reject) => {
    productWindow.webContents.session.once('will-download', (_event, item) => {
      try {
        assert.equal(item.getSaveDialogOptions().title, '保存导出文件');
        item.setSavePath(destination);
        item.once('done', (_downloadEvent, state) => resolve(state));
      } catch (error) { reject(error); }
    });
  });
  await productWindow.webContents.executeJavaScript(`(() => {
    const url=URL.createObjectURL(new Blob(['actual-export-bytes'],{type:'text/plain'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download=${JSON.stringify(filename)};anchor.click();
    setTimeout(()=>URL.revokeObjectURL(url),5000);
  })()`);
  return completed;
}
app.on('browser-window-created', (_event, window) => {
  productWindow = window;
  window.show = () => {}; // Keep this independent test window hidden.
  window.webContents.once('did-finish-load', () => run().catch(fail));
});
app.on('will-quit', event => {
  if (!closing || failed) return;
  event.preventDefault();
  (async () => {
    await assert.rejects(fetch(origin + '/api/auth/status', { signal: AbortSignal.timeout(1500) }));
    checks.push({ name: 'close-recovery-action-stops-local-backend-and-exits', passed: true });
    clearTimeout(timeout);
    console.log(JSON.stringify({ ok: true, productionMain: 'desktop/product-main.cjs', runDirectory, checks, recoveredCrashes: recoveryDialogs.length, downloadErrorDialogs: errorDialogs.length }, null, 2));
    app.exit(0);
  })().catch(fail);
});
async function run() {
  origin = new URL(productWindow.webContents.getURL()).origin;
  await productWindow.webContents.executeJavaScript('document.fonts.ready');
  assert.equal(await productWindow.webContents.executeJavaScript('typeof require'), 'undefined');
  const password = randomBytes(20).toString('hex');
  const setupStatus = await productWindow.webContents.executeJavaScript(`fetch('/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'actionsadmin',displayName:'桌面验收',password:${JSON.stringify(password)}})}).then(response=>response.status)`);
  assert.equal(setupStatus, 201);
  checks.push({ name: 'actual-product-entry-creates-isolated-administrator', passed: true });

  productWindow.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Alt' });
  productWindow.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Alt' });
  await delay(100); assert.equal(productWindow.isMenuBarVisible(), false);
  checks.push({ name: 'alt-does-not-reveal-default-electron-menu', passed: true });

  const windowCount = BrowserWindow.getAllWindows().length;
  await productWindow.webContents.executeJavaScript("window.open('https://example.invalid/blocked')");
  await delay(100); assert.equal(BrowserWindow.getAllWindows().length, windowCount);
  await productWindow.webContents.executeJavaScript("location.href='https://example.invalid/blocked'");
  await delay(100); assert.equal(new URL(productWindow.webContents.getURL()).origin, origin);
  assert.equal(await productWindow.webContents.executeJavaScript("fetch('https://example.invalid/blocked').then(()=>false,()=>true)"), true);
  checks.push({ name: 'external-popup-navigation-and-network-remain-blocked', passed: true });

  const permission = await productWindow.webContents.executeJavaScript('Notification.requestPermission()');
  assert.equal(permission, 'denied');
  checks.push({ name: 'browser-permission-request-is-actually-denied', passed: true });

  const exported = path.join(runDirectory, 'export-success.txt');
  assert.equal(await download('export-success.txt', exported), 'completed');
  assert.equal(fs.readFileSync(exported, 'utf8'), 'actual-export-bytes');
  checks.push({ name: 'download-produces-exact-file-through-native-download-item', passed: true });
  const blockedDestination = path.join(runDirectory, 'directory-not-file');
  fs.mkdirSync(blockedDestination);
  assert.equal(await download('export-failure.txt', blockedDestination), 'interrupted');
  await until(() => errorDialogs.length === 1, 'Interrupted download did not display an error');
  assert.match(errorDialogs[0].title, /保存失败/);
  checks.push({ name: 'interrupted-file-save-displays-actionable-error', passed: true });

  const reloaded = once(productWindow.webContents, 'did-finish-load');
  productWindow.webContents.forcefullyCrashRenderer();
  await reloaded;
  await until(() => recoveryDialogs.length === 1, 'Renderer crash did not offer recovery');
  assert.deepEqual(recoveryDialogs[0].buttons, ['重新加载', '关闭应用']);
  assert.equal(await productWindow.webContents.executeJavaScript("fetch('/api/auth/status').then(r=>r.json()).then(s=>s.user?.username)"), 'actionsadmin');
  checks.push({ name: 'renderer-crash-reload-restores-real-page-and-session', passed: true });

  recoveryResponse = 1; closing = true;
  productWindow.webContents.forcefullyCrashRenderer();
}
require('../desktop/product-main.cjs');
