const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const pinia = require('pinia');

function loadTs(file, globals) {
  const source = fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const module = { exports: {} };
  vm.runInNewContext(outputText, { module, exports: module.exports, URL, ...globals }, { filename: file });
  return module.exports;
}

function cameraStore() {
  const bridge = {};
  pinia.setActivePinia(pinia.createPinia());
  const { useCameraStore } = loadTs('stores/camera.ts', {
    require: name => name === 'pinia' ? pinia : name === '@/api/camera-bridge' ? { cameraBridge: bridge } : require(name),
    setTimeout: () => 0,
  });
  const store = useCameraStore();
  const status = (extra = {}) => ({ ...JSON.parse(JSON.stringify(store.status)), serviceState: 'ready', ...extra });
  bridge.getStatus = async () => status();
  return { bridge, store, status };
}

test('a 200 response that did not start detection must not report success', async () => {
  const { bridge, store, status } = cameraStore();
  bridge.startDetection = async () => status({ detecting: false, engineState: 'ready' });
  await store.toggleDetection();
  assert.equal(store.status.detecting, false);
  assert.equal(store.detectionBusy, false);
  assert.equal(store.toasts.some(t => t.tone === 'success'), false);
  assert.match(store.toasts.at(-1).message, /未确认/);
});

test('lazy model preparation is reported as pending, not running successfully', async () => {
  const { bridge, store, status } = cameraStore();
  bridge.startDetection = async () => status({ detecting: true, engineState: 'loading' });
  await store.toggleDetection();
  assert.equal(store.status.detecting, true);
  assert.equal(store.toasts.at(-1).tone, 'info');
  assert.match(store.toasts.at(-1).message, /准备/);
});

test('double clicks and status polling cannot issue a duplicate detection command', async () => {
  const { bridge, store, status } = cameraStore();
  let calls = 0, complete;
  bridge.startDetection = () => { calls++; return new Promise(resolve => { complete = resolve; }); };
  const pending = store.toggleDetection();
  await store.refreshStatus();
  await store.toggleDetection();
  assert.equal(calls, 1);
  assert.equal(store.detectionBusy, true);
  assert.equal(store.runState, 'starting');
  complete(status({ detecting: true, engineState: 'ready' }));
  await pending;
  assert.equal(store.detectionBusy, false);
  assert.equal(store.runState, 'running');
});

test('a failed stop retains the service state and never reports paused', async () => {
  const { bridge, store, status } = cameraStore();
  store.status.detecting = true;
  bridge.stopDetection = async () => status({ detecting: true });
  await store.stopDetection();
  assert.equal(store.status.detecting, true);
  assert.equal(store.runState, 'running');
  assert.equal(store.toasts.at(-1).tone, 'error');
});

test('armed and stopping recording states are not reported as completed actions', async () => {
  const { bridge, store } = cameraStore();
  bridge.toggleRecording = async () => ({ recording: false, requestedRecording: true, cameras: { camera1: { state: 'armed' }, camera2: { state: 'armed' } } });
  await store.toggleRecording();
  assert.equal(store.status.recording, false);
  assert.equal(store.status.cameras.camera1.recordingState, 'armed');
  assert.equal(store.toasts.at(-1).tone, 'info');
  assert.match(store.toasts.at(-1).message, /等待/);
  bridge.toggleRecording = async () => ({ recording: false, requestedRecording: false, cameras: { camera1: { state: 'stopping' }, camera2: { state: 'idle' } } });
  await store.toggleRecording();
  assert.match(store.toasts.at(-1).message, /正在结束/);
  assert.equal(store.recordingBusy, false);
});

test('queued snapshots never become a saved-success toast or download result', async () => {
  const { bridge, store } = cameraStore();
  bridge.saveSnapshots = async () => ({ results: ['camera1', 'camera2'].map(cameraId => ({ cameraId, saved: false, queued: true })) });
  const results = await store.captureBoth();
  assert.equal(results.some(r => r.saved || r.downloadUrl), false);
  assert.equal(store.toasts.some(t => t.tone === 'success'), false);
  assert.match(store.toasts.at(-1).message, /尚未确认/);
});

test('confirmed snapshot results preserve the actual download identifiers', async () => {
  const { bridge, store } = cameraStore();
  const result = { cameraId: 'camera1', saved: true, mediaId: 'real-image', name: 'capture.png', downloadUrl: '/api/media/real-image/download' };
  bridge.saveSnapshots = async () => ({ results: [result, { cameraId: 'camera2', saved: false, error: '相机未连接' }] });
  const results = await store.captureBoth();
  assert.equal(results[0].downloadUrl, result.downloadUrl);
  assert.equal(results[0].mediaId, result.mediaId);
  assert.equal(store.toasts.at(-1).tone, 'error');
});

function downloader(response) {
  const requests = [], clicks = [];
  const { downloadFile } = loadTs('lib/download-file.ts', {
    window: { location: { href: 'http://127.0.0.1:3000/workbench', origin: 'http://127.0.0.1:3000' } },
    fetch: async (url, options) => { requests.push({ url, options }); return response; },
    document: { body: { appendChild() {} }, createElement() { return { click() { clicks.push({ href: this.href, name: this.download }); }, remove() {} }; } },
  });
  return { downloadFile, requests, clicks };
}

test('failed or cross-origin downloads never start a browser download', async () => {
  const { downloadFile, requests, clicks } = downloader({ ok: false, status: 403 });
  await assert.rejects(downloadFile('/api/media/x/download'), /没有下载权限/);
  await assert.rejects(downloadFile('https://example.com/file'), /地址无效/);
  assert.equal(requests.length, 1);
  assert.equal(clicks.length, 0);
});

test('a validated download streams natively instead of loading the entire file into memory', async () => {
  const { downloadFile, requests, clicks } = downloader({ ok: true, headers: { get: name => name === 'content-length' ? '987654321' : 'video/mp4' } });
  await downloadFile('/api/media/x/download', 'camera.mp4');
  assert.equal(requests[0].options.method, 'HEAD');
  assert.equal(clicks.length, 1);
  assert.equal(clicks[0].name, 'camera.mp4');
});
