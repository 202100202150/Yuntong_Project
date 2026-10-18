import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

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

test('preview transport uses one long-polling request per camera without an 80ms frame cap', () => {
  const workspace = read('components/dual-camera-workspace.tsx');
  const main = read('desktop/main.cjs');

  assert.match(main, /'X-After-Frame-Sequence':\s*String\(afterSequence\)/);
  assert.match(main, /'X-Wait-Milliseconds':\s*'750'/);
  assert.doesNotMatch(workspace, /80\s*-\s*\(performance\.now\(\)\s*-\s*startedAt\)/);
  assert.match(workspace, /await bridge\.getFrame\(cameraId, latestSequenceRef\.current\[cameraId\]\)/);
  assert.match(workspace, /await delay\(120\)/);
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

test('cross-camera identity flows from the bridge through Electron into the 3D scene', () => {
  const bridge = read('../camera-rtsp-low-latency/camera_bridge.py');
  const main = read('desktop/main.cjs');
  const spatialScene = read('components/spatial-scene.tsx');

  assert.match(bridge, /"globalId": global_identity\(key, detected\.object_id\)/);
  assert.match(main, /typeof item\.globalId === 'string'[\s\S]*globalId: item\.globalId/);
  assert.match(spatialScene, /groupSpatialTracks\(objects\)/);
});

test('matching diagnostics expose both scores and safe candidate IDs in the 3D scene', () => {
  const main = read('desktop/main.cjs');
  const cameraTypes = read('lib/camera-types.ts');
  const spatialScene = read('components/spatial-scene.tsx');

  for (const field of [
    'secondSimilarity', 'camera1Id', 'camera2Id', 'comparisonCount',
    'offsetMs', 'geometryStatus', 'geometryInliers', 'reasonCounts', 'recentFailures',
  ]) {
    assert.match(main, new RegExp(`\\b${field}\\b`));
    assert.match(cameraTypes, new RegExp(`\\b${field}\\b`));
  }
  assert.match(main, /\^person-\\d\{1,12\}\$/);
  assert.match(spatialScene, /最高/);
  assert.match(spatialScene, /次高/);
  assert.match(spatialScene, /位置辅助/);
  assert.match(spatialScene, /temporal-geometry-v3/);
  assert.match(spatialScene, /近30秒主要阻碍/);
  assert.match(spatialScene, /最近失败/);
});
