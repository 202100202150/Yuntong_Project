import test from 'node:test';
import assert from 'node:assert/strict';
import {
  makeDemoTracks,
  parseTrack,
  parseHealth,
  mergeTrack,
  projectPoint,
  normalizeBackend,
  recordingPaths,
  speedOf,
} from '../lib/airspace.ts';

test('all four simulated classes satisfy the real API schema', () => {
  const tracks = makeDemoTracks();
  assert.equal(new Set(tracks.map((t) => t.class)).size, 4);
  for (const t of tracks) {
    assert.ok(parseTrack(t));
    assert.equal(t.history.length, 70);
    assert.deepEqual(t.history.at(-1), t.positionEnuM);
  }
});
test('invalid geometry, confidence and classes are rejected', () => {
  const t = makeDemoTracks()[0];
  for (const patch of [
    { positionEnuM: [NaN, 0, 0] },
    { positionEnuM: [0, 1] },
    { class: 'fighter' },
    { classConfidence: 1.1 },
    { positionStdM: [-1, 1, 1] },
    { timestampUtc: 'invalid' },
    { state: 'alert' },
    { reprojectionErrorPx: Infinity },
  ])
    assert.equal(parseTrack({ ...t, ...patch }), null);
});
test('duplicate and out-of-order packets do not overwrite a newer track', () => {
  const t = makeDemoTracks()[0];
  assert.equal(mergeTrack(t, { ...t, positionEnuM: [0, 0, 0] }), t);
  assert.equal(
    mergeTrack(t, { ...t, timestampUtc: '2020-01-01T00:00:00Z' }),
    t,
  );
  const newer = {
    ...t,
    timestampUtc: '2026-08-31T06:32:09Z',
    positionEnuM: [1, 2, 3],
  };
  const result = mergeTrack(t, newer);
  assert.deepEqual(result.history.at(-1), [1, 2, 3]);
  assert.equal(result.timestampUtc, newer.timestampUtc);
});
test('trajectory history has a fixed memory bound', () => {
  const t = makeDemoTracks()[0];
  let current = t;
  for (let i = 1; i < 400; i++)
    current = mergeTrack(current, {
      ...t,
      timestampUtc: new Date(
        Date.parse(t.timestampUtc) + i * 100,
      ).toISOString(),
    });
  assert.equal(current.history.length, 100);
});
test('demo velocity agrees with the analytic trajectory', () => {
  const now = makeDemoTracks(10),
    next = makeDemoTracks(10.001);
  now.forEach((t, i) =>
    t.velocityEnuMps.forEach((v, k) =>
      assert.ok(
        Math.abs((next[i].positionEnuM[k] - t.positionEnuM[k]) / 0.001 - v) <
          0.005,
      ),
    ),
  );
  assert.ok(speedOf(now[0]) > 0);
});
test('projection respects east, north, altitude and focus center', () => {
  assert.deepEqual(projectPoint([0, 0, 0], 0, 0, 1), [410, 256, 0]);
  assert.deepEqual(projectPoint([100, 0, 0], 0, 0, 1), [510, 256, 0]);
  assert.deepEqual(projectPoint([0, 0, 100], 0, 0, 1), [410, 156, 0]);
  assert.deepEqual(
    projectPoint([30, 40, 50], 0, 0, 1, [30, 40, 50]),
    [410, 256, 0],
  );
  assert.ok(projectPoint([0, 100, 0], 0, Math.PI / 2, 1)[1] > 256);
});
test('gateway config excludes passwords, unsafe schemes and mixed content', () => {
  assert.equal(normalizeBackend(''), '');
  assert.equal(
    normalizeBackend('https://example.org/gateway/', 'https:'),
    'https://example.org/gateway',
  );
  for (const url of [
    'rtsp://camera/live',
    'javascript:alert(1)',
    'https://user:secret@example.org',
    'https://example.org?token=secret',
  ])
    assert.throws(() => normalizeBackend(url));
  assert.throws(() => normalizeBackend('http://example.org', 'https:'));
});
test('recordings remain relative safe gateway paths', () => {
  const t = makeDemoTracks()[0];
  assert.deepEqual(
    recordingPaths({
      ...t,
      metadata: {
        recordingSegments: {
          cam01: '2026/clip.mp4',
          cam02: '../password',
          cam03: 'https://external.example/a.mp4',
        },
      },
    }),
    ['2026/clip.mp4'],
  );
});
test('health schema handles missing or invalid measurements safely', () => {
  assert.equal(parseHealth({ status: 'healthy', cameras: [null] }), null);
  assert.equal(parseHealth(null), null);
  const h = parseHealth({
    status: 'degraded',
    cameras: [{ cameraId: 'cam01', status: 'offline', averageFps: 'bad' }],
    stereo: { syncResidualP95Ms: NaN },
  });
  assert.equal(h.cameras[0].averageFps, undefined);
  assert.equal(h.stereo.syncResidualP95Ms, null);
});
