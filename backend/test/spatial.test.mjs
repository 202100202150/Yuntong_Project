import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSpatial } from '../src/normalize/spatial.mjs';
import { normalizeStatus } from '../src/normalize/status.mjs';

const target = { id: 'drone1', class: 'drone', camera1Id: 'track-1', camera2Id: 'track-2', x: -1.2, y: 1.7, z: 4,
  rangeMeters: Math.hypot(1.2, 1.7, 4), keypointCount: 1, reprojectionErrorPx: 0.4, frameSkewMs: 20, quality: 'calibrated' };

test('no calibration or service error cannot create metric targets', () => {
  assert.equal(normalizeSpatial(null).targets.length, 0);
  assert.equal(normalizeSpatial({ state: 'uncalibrated', targets: [target] }).targets.length, 0);
  assert.equal(normalizeSpatial({ state: 'error', targets: [target] }).targets.length, 0);
});

test('calibrated signed X/Y/Z survive in the midpoint frame', () => {
  const result = normalizeSpatial({ state: 'ready', targets: [target], cameras: { camera1: { x: -0.3349550712, y: 0, z: 0 } } });
  assert.equal(result.targets.length, 1);
  assert.equal(result.targets[0].x, -1.2);
  assert.equal(result.targets[0].z, 4);
  assert.equal(result.targets[0].y, 1.7);
  assert.ok(result.targets[0].azimuthDeg < 0);
  assert.ok(result.targets[0].elevationDeg > 0);
  assert.equal(result.cameras.camera1.y, 0);
});

test('nonfinite, uncalibrated and negative error measurements are excluded', () => {
  const result = normalizeSpatial({ state: 'ready', targets: [
    { ...target, x: NaN }, { ...target, z: Infinity }, { ...target, quality: 'estimated' },
    { ...target, frameSkewMs: -1 }, { ...target, reprojectionErrorPx: -1 },
  ] });
  assert.deepEqual(result.targets, []);
});

test('status normalization passes the spatial payload without breaking camera data', () => {
  const result = normalizeStatus({ detection: { running: true }, cameras: {
    camera1: { detection: { ageMs: 120, fps: 7, latencyMs: 28 }, objects: [] },
    camera2: { objects: [] },
  },
    spatial: { state: 'ready', message: '定位正常', targets: [target] } });
  assert.equal(result.detecting, true);
  assert.equal(result.spatial.targets[0].x, -1.2);
  assert.deepEqual(result.cameras.camera1.objects, []);
  assert.equal(result.cameras.camera1.detectionAgeMs, 120);
  assert.equal(result.cameras.camera1.detectionFps, 7);
  assert.equal(result.cameras.camera1.latency, 28);
});

test('camera status and spatial targets retain the same session names', () => {
  const object = (id, displayId) => ({
    id, displayId, class: 'drone', confidence: 0.9,
    bbox: { x: 0.1, y: 0.1, width: 0.2, height: 0.4 },
  });
  const result = normalizeStatus({
    cameras: {
      camera1: { objects: [object('track-1', 'drone1'), object('track-2', 'drone2')] },
      camera2: { objects: [object('track-8', 'drone2'), object('track-9', 'drone1')] },
    },
    spatial: {
      state: 'ready',
      targets: [
        { ...target, camera1Id: 'track-1', camera2Id: 'track-9', label: 'drone1', trackingState: 'held' },
        { ...target, id: 'drone2', camera1Id: 'track-2', camera2Id: 'track-8', label: 'drone2' },
      ],
    },
  });

  for (const point of result.spatial.targets) {
    assert.equal(result.cameras.camera1.objects.find((o) => o.id === point.camera1Id)?.displayId, point.label);
    assert.equal(result.cameras.camera2.objects.find((o) => o.id === point.camera2Id)?.displayId, point.label);
  }
  assert.deepEqual(result.spatial.targets.map((point) => point.label), ['drone1', 'drone2']);
  assert.deepEqual(result.spatial.targets.map((point) => point.trackingState), ['held', 'measured']);
});
