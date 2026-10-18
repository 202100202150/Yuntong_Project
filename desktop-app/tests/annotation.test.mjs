import assert from 'node:assert/strict';
import test from 'node:test';
import { DemoDetector } from '../lib/detector.ts';
import { normalizeDetectedClass } from '../lib/annotation-types.ts';
import { describeAbsolutePath } from '../lib/file-access-adapter.ts';
import { selectMediaAfterRemoval } from '../lib/media-store.ts';

test('legacy class names are normalized to person', () => {
  assert.equal(normalizeDetectedClass('person'), 'person');
  assert.equal(normalizeDetectedClass('male'), 'person');
  assert.equal(normalizeDetectedClass('female'), 'person');
  assert.equal(normalizeDetectedClass('famale'), 'person');
  assert.equal(normalizeDetectedClass('unknown'), null);
});

test('demo image detections are repeatable and normalized', async () => {
  const detector = new DemoDetector('test-seed');
  const input = { mediaId: 'photo-a', width: 1280, height: 720, kind: 'image', frameTime: 0 };
  const first = await detector.detect(input);
  const second = await detector.detect(input);
  assert.deepEqual(first, second);
  assert.ok(first.length >= 1 && first.length <= 3);
  assert.ok(first.every((item) => item.class === 'person'));
  assert.ok(first.every((item) => item.className === 'person'));
  assert.ok(first.every((item) => item.label === 'person'));
  assert.ok(first.every((item) => item.bbox.x + item.bbox.width <= 1));
});

test('demo video detections change deterministically by sampled frame', async () => {
  const detector = new DemoDetector('test-seed');
  const start = await detector.detect({ mediaId: 'clip-a', width: 1920, height: 1080, kind: 'video', frameTime: 0 });
  const later = await detector.detect({ mediaId: 'clip-a', width: 1920, height: 1080, kind: 'video', frameTime: 8 });
  assert.notDeepEqual(start, later);
  assert.deepEqual(later, await detector.detect({ mediaId: 'clip-a', width: 1920, height: 1080, kind: 'video', frameTime: 8 }));
});

test('plain browser files never claim a fake absolute path', () => {
  const file = new File(['x'], 'person.png', { type: 'image/png' });
  const description = describeAbsolutePath(file);
  assert.equal(description.isAbsolute, false);
  assert.match(description.value, /当前浏览器无法提供绝对路径/);
  assert.doesNotMatch(description.value, /fakepath/i);
});

test('removing the active media selects the next item, then the previous item', () => {
  const items = [{ id: 'first' }, { id: 'middle' }, { id: 'last' }];
  assert.equal(selectMediaAfterRemoval(items, 'middle', 'middle'), 'last');
  assert.equal(selectMediaAfterRemoval(items, 'last', 'last'), 'middle');
  assert.equal(selectMediaAfterRemoval([{ id: 'only' }], 'only', 'only'), null);
});

test('removing an inactive media keeps the current selection', () => {
  const items = [{ id: 'first' }, { id: 'second' }];
  assert.equal(selectMediaAfterRemoval(items, 'first', 'second'), 'second');
});
