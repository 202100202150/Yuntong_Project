import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (relativePath) => readFileSync(path.join(root, relativePath), 'utf8');

test('desktop renderer exposes only the dual-camera workspace', () => {
  const renderer = read('desktop/renderer.tsx');
  const workspace = read('components/dual-camera-workspace.tsx');

  assert.match(renderer, /import DualCameraWorkspace/);
  assert.match(renderer, /<DualCameraWorkspace\s*\/>/);
  for (const removedText of ['+ Add', 'Object Properties', 'ONNX PERSON', '13 minutes ago']) {
    assert.doesNotMatch(workspace, new RegExp(removedText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'));
  }
  assert.match(workspace, /cameraIds\.map/);
  assert.match(workspace, /Stop Recording/);
  assert.match(workspace, /Screenshot/);
  assert.match(workspace, /'Pause'/);
  assert.match(workspace, /'Start'/);
  assert.match(workspace, /<Square \/> Stop/);
});

test('camera column owns exactly the right half and inactive canvas controls stay disabled', () => {
  const styles = read('components/dual-camera-workspace.css');
  const workspace = read('components/dual-camera-workspace.tsx');

  assert.match(styles, /grid-template-columns:\s*72px\s+minmax\(0,\s*calc\(50% - 72px\)\)\s+50%/);
  assert.match(styles, /\.dc-cameras\s*\{[^}]*grid-column:\s*3/s);
  assert.match(workspace, /disabled aria-label="缩小暂未启用"/);
  assert.match(workspace, /disabled aria-label="放大暂未启用"/);
  assert.match(workspace, /disabled title="Fit 暂未启用"/);
  assert.match(workspace, /disabled title="Reset 暂未启用"/);
});

test('renderer bridge never receives credentials, loopback details, ports, or local paths', () => {
  const preload = read('desktop/preload.cjs');
  const cameraTypes = read('lib/camera-types.ts');
  const main = read('desktop/main.cjs');

  for (const forbidden of [
    'CAMERA_BRIDGE_TOKEN',
    'Bearer ',
    '127.0.0.1',
    'camera-rtsp-low-latency',
    'YUNTONG_CAMERA_PROJECT_DIR',
  ]) {
    assert.doesNotMatch(preload, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(cameraTypes, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }

  assert.doesNotMatch(cameraTypes, /\bpath\??\s*:/);
  assert.doesNotMatch(main, /\{\s*path:\s*item\.path\s*\}/);
});

test('dual preview uses one paired poll and paints both decoded frames together', () => {
  const workspace = read('components/dual-camera-workspace.tsx');
  const preload = read('desktop/preload.cjs');

  assert.match(preload, /ipcRenderer\.invoke\('camera:get-preview-pair'/);
  assert.match(workspace, /await bridge\.getPreviewPair\(cursors, epoch\)/);
  assert.match(workspace, /Promise\.allSettled\(\[\s*decodeFrame\(pair\.camera1\),\s*decodeFrame\(pair\.camera2\),\s*\]\)/);
  assert.match(workspace, /showFrame\('camera1', pair\.camera1, first\.value, now\);\s*showFrame\('camera2', pair\.camera2, second\.value, now\)/);
  assert.match(workspace, /await bridge\.getFrame\(cameraId, latestSequenceRef\.current\[cameraId\]\)/);
  assert.match(workspace, /status\.state === 'streaming' && status\.frameAvailable/);
});

test('paired preview IPC returns both JPEGs, handles no pair, and accepts restarted sequences', async () => {
  const handlers = new Map();
  const requests = [];
  const require = createRequire(import.meta.url);
  const mockElectron = {
    app: { requestSingleInstanceLock: () => false, quit() {} },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    protocol: { registerSchemesAsPrivileged() {} },
  };
  let response;
  runInNewContext(read('desktop/main.cjs') + '\nrequestCameraBridge = mockRequest;', {
    __dirname: path.join(root, 'desktop'),
    require: (id) => id === 'electron' ? mockElectron : require(id),
    mockRequest: (pathname, options) => {
      requests.push([pathname, options]);
      return Promise.resolve(response);
    },
  });

  const handler = handlers.get('camera:get-preview-pair');
  const senderFrame = { url: 'app://bundle/index.html' };
  const event = { senderFrame, sender: { mainFrame: senderFrame } };
  const input = { afterSequences: { camera1: 101, camera2: 151 }, epoch: 'a'.repeat(32) };
  const fresh = {
    statusCode: 200,
    headers: {
      'x-preview-epoch': 'b'.repeat(32),
      'x-camera1-length': '3',
      'x-camera1-sequence': '2',
      'x-camera2-sequence': '3',
    },
    data: Buffer.from([1, 2, 3, 4, 5]),
  };

  response = { statusCode: 204 };
  assert.equal(await handler(event, input), null);
  assert.equal(requests[0][0], '/v1/cameras/preview-pair');
  assert.deepEqual({ ...requests[0][1].requestHeaders }, {
    'X-After-Camera1-Sequence': '101',
    'X-After-Camera2-Sequence': '151',
    'X-Wait-Milliseconds': '750',
    'X-Preview-Epoch': input.epoch,
  });
  response = fresh;
  const pair = await handler(event, input);
  assert.equal(pair.epoch, 'b'.repeat(32));
  assert.equal(pair.camera1.sequence, 2);
  assert.equal(pair.camera2.sequence, 3);
  assert.deepEqual([...pair.camera1.jpegBytes], [1, 2, 3]);
  assert.deepEqual([...pair.camera2.jpegBytes], [4, 5]);

  response = { ...fresh, headers: { ...fresh.headers, 'x-preview-epoch': input.epoch } };
  assert.equal(await handler(event, input), null);
  response = { ...fresh, headers: { ...fresh.headers, 'x-camera1-length': '5' } };
  await assert.rejects(handler(event, input), /无效的双路预览/);
  await assert.rejects(handler({ ...event, sender: {} }, input), /拒绝非本地页面/);
});

test('desktop sidecar uses a 30 FPS CUDA-friendly preview profile', () => {
  const main = read('desktop/main.cjs');

  assert.match(main, /'--preview-width',\s*'768'/);
  assert.match(main, /'--preview-height',\s*'432'/);
  assert.match(main, /'--preview-jpeg-quality',\s*'78'/);
  assert.match(main, /'--preview-max-fps',\s*'30'/);
  assert.match(main, /'--detection-fps',\s*'12\.5'/);
});

test('desktop and GPU scripts resolve the sidecar from the portable monorepo layout', () => {
  const main = read('desktop/main.cjs');
  const gpuCommon = read('scripts/gpu/common.ps1');

  assert.match(main, /process\.env\.YUNTONG_CAMERA_PROJECT_DIR/);
  assert.match(
    main,
    /path\.resolve\(softwareRootPath,\s*'\.\.',\s*'camera-rtsp-low-latency'\)/,
  );
  assert.match(main, /fs\.existsSync\(path\.join\(monorepoProjectPath,\s*'camera_bridge\.py'\)\)/);

  assert.match(gpuCommon, /\$env:YUNTONG_CAMERA_PROJECT_DIR/);
  assert.match(
    gpuCommon,
    /Join-Path \$softwareRoot '\.\.\\camera-rtsp-low-latency'/,
  );
  assert.match(gpuCommon, /Test-Path -LiteralPath \(Join-Path \$monorepoCameraRoot 'requirements-gpu\.txt'\)/);
});

test('renderer reports measured unique display FPS and explicit inference backend state', () => {
  const workspace = read('components/dual-camera-workspace.tsx');
  const cameraTypes = read('lib/camera-types.ts');
  const main = read('desktop/main.cjs');

  assert.match(workspace, /frames\.push\(\{ sequence: frame\.sequence, at: now \}\)/);
  assert.match(workspace, /<span>\{measuredFps\} FPS<\/span>/);
  assert.match(workspace, /TensorRT\$\{precisionLabel\} \/ CUDA/);
  assert.match(workspace, /GPU 状态未上报/);
  for (const field of [
    'inferenceProvider',
    'precision',
    'previewProcessor',
    'previewFps',
    'detectionFps',
    'latency',
    'engineState',
  ]) {
    assert.match(cameraTypes, new RegExp(`\\b${field}\\b`));
    assert.match(main, new RegExp(`\\b${field}\\b`));
  }
});

test('desktop scene receives measured spatial targets from the camera bridge', () => {
  const main = read('desktop/main.cjs');
  const workspace = read('components/dual-camera-workspace.tsx');
  const spatialScene = read('components/spatial-scene.tsx');

  assert.match(main, /typeof item\.globalId === 'string'[\s\S]*globalId: item\.globalId/);
  assert.match(main, /spatial: normalizeSpatial\(payload\?\.spatial\)/);
  assert.match(workspace, /spatial=\{bridgeStatus\.spatial\}/);
  assert.match(spatialScene, /spatial\.targets/);
  assert.doesNotMatch(spatialScene, /triangulate|groupSpatialTracks/);
});

test('Electron IPC preserves a calibrated drone and cannot derive a position from a preview box', () => {
  const require = createRequire(import.meta.url);
  const mockElectron = {
    app: { requestSingleInstanceLock: () => false, quit() {} },
    ipcMain: { handle() {} },
    protocol: { registerSchemesAsPrivileged() {} },
  };
  const target = { id: 'drone1', class: 'drone', quality: 'calibrated', label: 'drone1',
    trackingState: 'held', x: -1.2, y: 1.7, z: 4 };
  const payload = { cameras: { camera1: { objects: [{
    id: 'track-1', class: 'drone', displayId: 'drone1', bbox: { x: 0.2, y: 0.4, width: 0.04, height: 0.02 },
  }] } }, spatial: { state: 'ready', targets: [target] } };

  const normalize = runInNewContext(read('desktop/main.cjs') + '\nnormalizeStatus;', {
    __dirname: path.join(root, 'desktop'),
    require: (id) => id === 'electron' ? mockElectron : require(id),
  });
  const result = normalize(payload);
  assert.equal(result.cameras.camera1.objects[0].displayId, 'drone1');
  assert.equal(result.spatial.targets.length, 1);
  assert.equal(result.spatial.targets[0].y, 1.7);
  assert.equal(result.spatial.targets[0].trackingState, 'held');
  assert.equal(normalize({ cameras: payload.cameras }).spatial.targets.length, 0);
});
