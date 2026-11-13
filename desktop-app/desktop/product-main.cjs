const { app, BrowserWindow, dialog, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const developmentRoot = path.resolve(__dirname, '..', '..');
let backend;
let window;
let shutdownStarted = false;
let shutdownComplete = false;
let localOrigin;
let recoveryPending = false;

app.setName('云瞳 AIRSPACE');
const profileDirectory = path.join(app.getPath('appData'), 'YuntongAIRSPACE');
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
  try { return new URL(url).origin === localOrigin; } catch { return false; }
}

async function recoverWindow(message) {
  const affectedWindow = window;
  if (shutdownStarted || recoveryPending || !affectedWindow || affectedWindow.isDestroyed()) return;
  recoveryPending = true;
  try {
    const { response } = await dialog.showMessageBox(affectedWindow, {
      type: 'error', title: '云瞳 AIRSPACE 页面异常', message,
      detail: '重新加载会恢复当前本地服务的界面，尚未保存的表单内容需要重新填写。',
      buttons: ['重新加载', '关闭应用'], defaultId: 0, cancelId: 1, noLink: true,
    });
    if (shutdownStarted || affectedWindow.isDestroyed()) return;
    if (response === 0) await affectedWindow.loadURL(localOrigin);
    else app.quit();
  } catch (error) {
    dialog.showErrorBox('云瞳 AIRSPACE 无法恢复页面', error.message);
    app.quit();
  } finally { recoveryPending = false; }
}

function createWindow() {
  window = new BrowserWindow({
    width: 1600, height: 1000, minWidth: 1040, minHeight: 720,
    title: '云瞳 AIRSPACE', backgroundColor: '#f3f6fb',
    icon: path.join(__dirname, 'assets', 'icon.ico'), show: false,
    autoHideMenuBar: true,
    webPreferences: {
      partition: 'persist:yuntong-product', contextIsolation: true,
      nodeIntegration: false, sandbox: true, webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });
  window.setMenu(null);
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (!isSameOrigin(url)) event.preventDefault(); });
  window.webContents.on('will-redirect', (event, url) => { if (!isSameOrigin(url)) event.preventDefault(); });
  window.webContents.on('will-attach-webview', (event) => event.preventDefault());
  let hasLoadedPage = false;
  window.webContents.on('did-finish-load', () => { hasLoadedPage = true; });
  window.webContents.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
    if (hasLoadedPage && isMainFrame && code !== -3) void recoverWindow('页面加载失败：' + description);
  });
  window.webContents.on('render-process-gone', (_event, details) => {
    if (!shutdownStarted) void recoverWindow('页面进程已退出（' + details.reason + '）。');
  });
  window.once('ready-to-show', () => window.show());
  window.on('closed', () => { window = null; });
  return window.loadURL(localOrigin);
}

async function shutdown() {
  if (shutdownStarted) return;
  shutdownStarted = true;
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
    const productSession = session.fromPartition('persist:yuntong-product');
    productSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    productSession.setPermissionCheckHandler(() => false);
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
