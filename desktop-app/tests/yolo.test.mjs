import assert from 'node:assert/strict';
import test from 'node:test';
import { isDetectedObject } from '../lib/annotation-types.ts';
import { PersonTracker } from '../lib/object-tracker.ts';
import { groupSpatialTracks } from '../lib/spatial-tracks.ts';
import { calculateLetterbox, rgbaToNchw } from '../lib/yolo-preprocess.ts';
import {
  nonMaximumSuppression,
  parseYoloChannelMajor,
  restoreYoloDetections,
} from '../lib/yolo-postprocess.ts';

test('3D scene scopes local camera IDs and merges only explicit cross-camera matches', () => {
  const detected = (id, globalId) => ({
    id,
    ...(globalId ? { globalId } : {}),
    class: 'person',
    confidence: 0.9,
    bbox: { x: 0.4, y: 0.2, width: 0.2, height: 0.6 },
    frameSequence: 1,
  });

  const sameLocalId = groupSpatialTracks({
    camera1: [detected('person-1', 'camera1:person-1')],
    camera2: [detected('person-1', 'camera2:person-1')],
  });
  assert.equal(sameLocalId.length, 1);
  assert.equal(sameLocalId.filter((track) => track.left && track.right).length, 1);

  const matched = groupSpatialTracks({
    camera1: [detected('person-1', 'stereo:camera1/person-1|camera2/person-4')],
    camera2: [detected('person-4', 'stereo:camera1/person-1|camera2/person-4')],
  });
  assert.equal(matched.length, 1);
  assert.ok(matched[0].left);
  assert.ok(matched[0].right);
});

test('current-frame reconciliation caps an unmatched two-camera view at the larger count', () => {
  const detectedAt = (id, x, y = 0.2) => ({
    id,
    globalId: 'camera1:' + id,
    class: 'person',
    confidence: 0.9,
    bbox: { x, y, width: 0.16, height: 0.6 },
    frameSequence: 4,
  });
  const left = [detectedAt('left-1', 0.25), detectedAt('left-2', 0.58)];
  const right = [
    { ...detectedAt('right-1', 0.27), globalId: 'camera2:right-1' },
    { ...detectedAt('right-2', 0.6), globalId: 'camera2:right-2' },
  ];
  const tracks = groupSpatialTracks({ camera1: left, camera2: right });
  assert.equal(tracks.length, 2);
  assert.equal(tracks.filter((track) => track.left && track.right).length, 2);
});

test('current-frame reconciliation keeps one extra person when one camera misses the other', () => {
  const object = (id, x, camera) => ({
    id,
    globalId: camera + ':' + id,
    class: 'person',
    confidence: 0.9,
    bbox: { x, y: 0.2, width: 0.16, height: 0.6 },
    frameSequence: 5,
  });
  const tracks = groupSpatialTracks({
    camera1: [object('left-1', 0.25, 'camera1'), object('left-2', 0.58, 'camera1')],
    camera2: [object('right-1', 0.27, 'camera2')],
  });
  assert.equal(tracks.length, 2);
  assert.equal(tracks.filter((track) => track.left && track.right).length, 1);
});

test('current-frame reconciliation clears departed targets immediately', () => {
  assert.deepEqual(groupSpatialTracks({ camera1: [], camera2: [] }), []);
});

test('current-frame reconciliation does not collapse duplicate local IDs', () => {
  const duplicate = (x, camera) => ({
    id: 'person-1',
    globalId: camera + ':person-1',
    class: 'person',
    confidence: 0.9,
    bbox: { x, y: 0.2, width: 0.14, height: 0.6 },
    frameSequence: 6,
  });
  const tracks = groupSpatialTracks({
    camera1: [duplicate(0.2, 'camera1'), duplicate(0.6, 'camera1')],
    camera2: [duplicate(0.21, 'camera2'), duplicate(0.61, 'camera2')],
  });
  assert.equal(tracks.length, 2);
});

test('letterbox preserves landscape and portrait aspect ratios', () => {
  assert.deepEqual(calculateLetterbox(1280, 720), {
    originalWidth: 1280,
    originalHeight: 720,
    inputSize: 640,
    resizedWidth: 640,
    resizedHeight: 360,
    scale: 0.5,
    padX: 0,
    padY: 140,
  });
  assert.deepEqual(calculateLetterbox(720, 1280), {
    originalWidth: 720,
    originalHeight: 1280,
    inputSize: 640,
    resizedWidth: 360,
    resizedHeight: 640,
    scale: 0.5,
    padX: 140,
    padY: 0,
  });
});

test('RGBA pixels are converted to normalized RGB channel-first data', () => {
  const data = rgbaToNchw(
    new Uint8ClampedArray([255, 0, 128, 0, 0, 64, 255, 255]),
    2,
    1,
  );
  assert.equal(data.length, 6);
  assert.deepEqual([...data].map((value) => Math.round(value * 255)), [255, 0, 0, 64, 128, 255]);
});

test('channel-major YOLO output filters confidence and keeps person class', () => {
  const data = new Float32Array([
    100, 102, 500,
    100, 102, 500,
    100, 100, 40,
    100, 100, 80,
    0.9, 0.8, 0.249,
  ]);
  const boxes = parseYoloChannelMajor(data, [1, 5, 3], 0.25, 0);
  assert.equal(boxes.length, 2);
  assert.equal(boxes[0].candidateIndex, 0);
  assert.ok(Math.abs(boxes[0].confidence - 0.9) < 1e-6);
  assert.deepEqual(boxes[0], {
    candidateIndex: 0,
    x1: 50,
    y1: 50,
    x2: 150,
    y2: 150,
    confidence: boxes[0].confidence,
  });
});

test('NMS removes overlapping lower-confidence boxes without mutating input', () => {
  const input = [
    { candidateIndex: 0, x1: 0, y1: 0, x2: 100, y2: 100, confidence: 0.9 },
    { candidateIndex: 1, x1: 10, y1: 10, x2: 110, y2: 110, confidence: 0.8 },
    { candidateIndex: 2, x1: 200, y1: 200, x2: 240, y2: 280, confidence: 0.7 },
  ];
  const selected = nonMaximumSuppression(input, 0.45, 100);
  assert.deepEqual(selected.map((box) => box.candidateIndex), [0, 2]);
  assert.equal(input.length, 3);
});

test('letterbox coordinates restore to normalized media coordinates', () => {
  const meta = calculateLetterbox(1280, 720);
  const detections = restoreYoloDetections(
    [{ candidateIndex: 7, x1: 64, y1: 176, x2: 320, y2: 320, confidence: 0.9 }],
    meta,
    1.5,
  );
  assert.equal(detections.length, 1);
  assert.ok(isDetectedObject(detections[0]));
  assert.deepEqual(detections[0].bbox, { x: 0.1, y: 0.1, width: 0.4, height: 0.4 });
  assert.equal(detections[0].source, 'model');
  assert.equal(detections[0].frameTime, 1.5);
});

test('video tracker reuses IDs for overlapping detections', () => {
  const tracker = new PersonTracker();
  const makeDetection = (x) => ({
    id: 'candidate',
    class: 'person',
    className: 'person',
    label: 'person',
    confidence: 0.9,
    bbox: { x, y: 0.1, width: 0.2, height: 0.5 },
    source: 'model',
    visible: true,
  });
  const first = tracker.update('video-a', 'video', [makeDetection(0.1)]);
  const second = tracker.update('video-a', 'video', [makeDetection(0.12)]);
  assert.equal(first[0].id, second[0].id);
  const newMedia = tracker.update('video-b', 'video', [makeDetection(0.1)]);
  assert.equal(newMedia[0].id, 'person-1');
});
