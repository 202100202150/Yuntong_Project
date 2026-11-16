const { app, BrowserWindow, dialog, ipcMain, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { sameOrigin, paneFromUrl } = require('./product-window-security.cjs');

const developmentRoot = path.resolve(__dirname, '..', '..');
let backend;
let window;
let shutdownStarted = false;
let shutdownComplete = false;
let localOrigin;
const recoveryPending = new WeakSet();
const knownWindows = new Map();
const paneWindows = new Map();

app.setName('云瞳 AIRSPACE');
if (process.env.YUNTONG_PROFILE_DIR && !path.isAbsolute(process.env.YUNTONG_PROFILE_DIR)) throw new Error('YUNTONG_PROFILE_DIR 必须使用绝对路径');
const profileDirectory = process.env.YUNTONG_PROFILE_DIR || path.join(app.getPath('appData'), 'YuntongAIRSPACE');
fs.mkdirSync(profileDirectory, { recursive: true });
app.setPath('userData', profileDirectory);
app.setPath('sessionData', profileDirectory);
const ownsLock = app.requestSingleInstanceLock();
if (!ownsLock) app.quit();

function configureRuntime() {
  const runtimeRoot = app.isPackaged ? path.join(process.resourcesPath, 'runtime') : developmentRoot;
  const dataDirectory = process.env.YUNTONG_DATA_DIR || path.join(app.getPath('userData'), 'data');
  const configFile = path.join(app.getPath('userData'), 'runtime-config.json');
  const allowed = ['YUNTONG_CAMERA_PYTHON', 'YUNTONG_CAMERA_PROJECT_DIR', 'YUNTONG_DRONE_MODEL', 'YUNTONG_CALIBRATION_PYTHON', 'YUNTONG_MODEL_PYTHON', 'YUNTONG_FFMPEG', 'YUNTONG_FFPROBE'];
  if (fs.existsSync(configFile)) {
    const config = JSON.parse(fs.readFileSync(configFile, 'utf8'));
    for (const key of allowed) {
      if (!process.env[key] && typeof config[key] === 'string' && config[key].trim()) {
        if (!path.isAbsolute(config[key])) throw new Error(key + ' 必须使用绝对路径');
        process.env[key] = config[key];
      }
    }
  }
  process.env.YUNTONG_ROOT ||= path.join(runtimeRoot, 'desktop-app');
  process.env.NODE_ENV = 'production';
  process.env.YUNTONG_CAMERA_PROJECT_DIR ||= path.join(runtimeRoot, 'camera-rtsp-low-latency');
  process.env.YUNTONG_TENSORRT_CACHE ||= path.join(dataDirectory, 'tensorrt');
  return { runtimeRoot, dataDirectory };
}

function isSameOrigin(url) {
  return sameOrigin(url, localOrigin);
}

function entryUrl(pane = 'main') {
  return localOrigin + '/workbench' + (pane === 'main' ? '' : '?pane=' + pane);
}

async function recoverWindow(affectedWindow, message) {
  if (shutdownStarted || recoveryPending.has(affectedWindow) || !affectedWindow || affectedWindow.isDestroyed()) return;
  recoveryPending.add(affectedWindow);
  try {
    const { response } = await dialog.showMessageBox(affectedWindow, {
      type: 'error', title: '云瞳 AIRSPACE 页面异常', message,
      detail: '重新加载会恢复当前本地服务的界面，尚未保存的表单内容需要重新填写。',
      buttons: ['重新加载', '关闭应用'], defaultId: 0, cancelId: 1, noLink: true,
    });
    if (shutdownStarted || affectedWindow.isDestroyed()) return;
    if (response === 0) await affectedWindow.loadURL(entryUrl(knownWindows.get(affectedWindow)));
    else if (affectedWindow === window) app.quit();
    else affectedWindow.close();
  } catch (error) {
    if (shutdownStarted || affectedWindow.isDestroyed()) return;
    dialog.showErrorBox('云瞳 AIRSPACE 无法恢复页面', error.message);
    if (affectedWindow === window) app.quit();
    else affectedWindow.close();
  } finally { recoveryPending.delete(affectedWindow); }
}

function windowOptions(pane = 'main') {
  return {
    width: pane === 'main' ? 1672 : 1280, height: pane === 'main' ? 941 : 860,
    minWidth: pane === 'main' ? 1040 : 960, minHeight: 600,
    title: '云瞳 AIRSPACE', backgroundColor: '#f6f8fc', frame: false,
    icon: path.join(__dirname, 'assets', 'icon.ico'), show: false,
    autoHideMenuBar: true,
    webPreferences: {
      partition: 'persist:yuntong-product', contextIsolation: true,
      nodeIntegration: false, sandbox: true, webSecurity: true,
      allowRunningInsecureContent: false,
      preload: path.join(__dirname, 'product-preload.cjs'),
    },
  };
}

function windowState(target) {
  return { maximized: target.isMaximized(), minimized: target.isMinimized(), fullscreen: target.isFullScreen(), pane: knownWindows.get(target) || 'main' };
}

function bindWindow(target, pane) {
  knownWindows.set(target, pane);
  if (pane !== 'main') paneWindows.set(pane, target);
  target.setMenu(null);
  target.webContents.setWindowOpenHandler(({ url }) => {
    const childPane = paneFromUrl(url, localOrigin);
    if (!childPane || shutdownStarted || !window || window.isDestroyed()) return { action: 'deny' };
    const existing = paneWindows.get(childPane);
    if (existing && !existing.isDestroyed()) {
      if (existing.isMinimized()) existing.restore();
      existing.show(); existing.focus();
      return { action: 'deny' };
    }
    return { action: 'allow', overrideBrowserWindowOptions: { ...windowOptions(childPane), parent: window } };
  });
  target.webContents.on('did-create-window', (child, details) => {
    const childPane = paneFromUrl(details.url, localOrigin);
    if (!childPane || shutdownStarted) { child.destroy(); return; }
    bindWindow(child, childPane);
  });
  target.webContents.on('will-navigate', (event, url) => { if (!isSameOrigin(url)) event.preventDefault(); });
  target.webContents.on('will-redirect', (event, url) => { if (!isSameOrigin(url)) event.preventDefault(); });
  target.webContents.on('will-attach-webview', (event) => event.preventDefault());
  let hasLoadedPage = false;
  target.webContents.on('did-finish-load', () => { hasLoadedPage = true; });
  target.webContents.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
    if (hasLoadedPage && isMainFrame && code !== -3) void recoverWindow(target, '页面加载失败：' + description);
  });
  target.webContents.on('render-process-gone', (_event, details) => {
    if (!shutdownStarted) void recoverWindow(target, '页面进程已退出（' + details.reason + '）。');
  });
  for (const event of ['maximize', 'unmaximize', 'minimize', 'restore', 'enter-full-screen', 'leave-full-screen']) {
    target.on(event, () => { if (!target.isDestroyed()) target.webContents.send('yuntong:window-state-changed', windowState(target)); });
  }
  target.once('ready-to-show', () => target.show());
  target.on('closed', () => {
    knownWindows.delete(target);
    if (paneWindows.get(pane) === target) paneWindows.delete(pane);
    if (target === window) {
      window = null;
      for (const child of paneWindows.values()) if (!child.isDestroyed()) child.destroy();
      paneWindows.clear();
    }
  });
}

function registerWindowControls() {
  const senderWindow = event => {
    const sender = event.sender;
    const target = BrowserWindow.fromWebContents(sender);
    if (!target || target.isDestroyed() || !knownWindows.has(target) || !event.senderFrame
      || event.senderFrame !== sender.mainFrame || !isSameOrigin(event.senderFrame.url)) {
      throw new Error('Window control request is not permitted');
    }
    return target;
  };
  ipcMain.handle('yuntong:window-state', event => windowState(senderWindow(event)));
  ipcMain.handle('yuntong:window-minimize', event => {
    const target = senderWindow(event); target.minimize(); return windowState(target);
  });
  ipcMain.handle('yuntong:window-maximize', event => {
    const target = senderWindow(event);
    if (target.isMaximized()) target.unmaximize(); else target.maximize();
    return windowState(target);
  });
  ipcMain.handle('yuntong:window-close', event => {
    const target = senderWindow(event); setImmediate(() => { if (!target.isDestroyed()) target.close(); });
  });
}

function createWindow() {
  window = new BrowserWindow(windowOptions());
  bindWindow(window, 'main');
  return window.loadURL(entryUrl());
}

async function shutdown() {
  if (shutdownStarted) return;
  shutdownStarted = true;
  for (const child of paneWindows.values()) if (!child.isDestroyed()) child.destroy();
  if (window && !window.isDestroyed()) window.destroy();
  try { await backend?.close(); }
  catch (error) { console.error('[desktop] 后端关闭失败:', error.message); }
  finally { shutdownComplete = true; app.quit(); }
}

if (ownsLock) {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', (event) => {
    if (shutdownComplete || !backend) return;
    event.preventDefault();
    void shutdown();
  });
  app.whenReady().then(async () => {
    const { runtimeRoot, dataDirectory } = configureRuntime();
    const staticDirectory = path.join(runtimeRoot, 'frontend', 'dist');
    if (!fs.existsSync(path.join(staticDirectory, 'index.html'))) throw new Error('前端构建不存在，请先运行 npm run desktop:build-renderer。');
    const { startBackend } = await import(pathToFileURL(path.join(runtimeRoot, 'backend', 'src', 'index.mjs')).href);
    backend = await startBackend({ port: 0, host: '127.0.0.1', dataDirectory, staticDirectory, startBridge: true });
    localOrigin = new URL(backend.url).origin;
    registerWindowControls();
    const productSession = session.fromPartition('persist:yuntong-product');
    const localFullscreen = (contents, permission, requestingUrl) => {
      if (permission !== 'fullscreen' || !contents || contents.isDestroyed()) return false;
      const owner = BrowserWindow.fromWebContents(contents);
      return Boolean(owner && knownWindows.has(owner) && isSameOrigin(contents.getURL()) && (!requestingUrl || isSameOrigin(requestingUrl)));
    };
    productSession.setPermissionRequestHandler((contents, permission, callback, details) => callback(localFullscreen(contents, permission, details.requestingUrl)));
    productSession.setPermissionCheckHandler((contents, permission, requestingOrigin) => localFullscreen(contents, permission, requestingOrigin));
    productSession.webRequest.onBeforeRequest((details, callback) => {
      const protocol = (() => { try { return new URL(details.url).protocol; } catch { return ''; } })();
      const websocket = protocol === 'ws:' && details.url.startsWith(localOrigin.replace('http:', 'ws:') + '/');
      callback({ cancel: !isSameOrigin(details.url) && !websocket && !['blob:', 'data:', 'devtools:'].includes(protocol) });
    });
    productSession.on('will-download', (_event, item) => {
      item.setSaveDialogOptions({ title: '保存导出文件', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())) });
      item.once('done', (_downloadEvent, state) => {
        if (state === 'interrupted' && !shutdownStarted) {
          dialog.showErrorBox('导出文件保存失败', '“' + item.getFilename() + '”未能完整保存。请检查目标目录是否可写、磁盘空间是否充足，然后重新下载。');
        }
      });
    });
    await createWindow();
  }).catch(async (error) => {
    console.error('[desktop] 启动失败:', error);
    dialog.showErrorBox('云瞳 AIRSPACE 启动失败', error.message);
    await shutdown();
  });
}
