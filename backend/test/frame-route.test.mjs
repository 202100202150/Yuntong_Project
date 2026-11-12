import test from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import { createFrameRouter } from '../src/routes/frame.mjs';

test('paired preview keeps both frames and their cursor in one binary response', async (t) => {
  const calls = [];
  const epoch = 'a'.repeat(32);
  const payload = Buffer.from('firstsecond');
  const app = express();
  app.use('/api', createFrameRouter(async (path, options) => {
    calls.push({ path, options });
    if (calls.length > 1) return { statusCode: 204, data: null };
    return {
      statusCode: 200,
      data: payload,
      headers: {
        'x-camera1-length': '5',
        'x-camera1-sequence': '42',
        'x-camera2-sequence': '79',
        'x-frame-skew-ms': '14.500',
        'x-preview-epoch': epoch,
      },
    };
  }));
  const server = app.listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}/api/cameras/preview-pair`;
  const invalid = await fetch(base + '?waitMs=1001');
  assert.equal(invalid.status, 400);
  assert.equal(calls.length, 0);

  const result = await fetch(base + `?afterCamera1Sequence=40&afterCamera2Sequence=77&epoch=${epoch}`);
  assert.equal(result.status, 200);
  assert.equal(result.headers.get('x-camera1-length'), '5');
  assert.equal(result.headers.get('x-camera1-sequence'), '42');
  assert.equal(result.headers.get('x-camera2-sequence'), '79');
  assert.equal(result.headers.get('x-preview-epoch'), epoch);
  assert.match(result.headers.get('access-control-expose-headers'), /X-Camera1-Length/);
  assert.deepEqual(Buffer.from(await result.arrayBuffer()), payload);
  assert.equal(calls[0].path, '/v1/cameras/preview-pair');
  assert.deepEqual(calls[0].options.requestHeaders, {
    'X-After-Camera1-Sequence': '40',
    'X-After-Camera2-Sequence': '77',
    'X-Wait-Milliseconds': '750',
    'X-Preview-Epoch': epoch,
  });
  assert.equal((await fetch(base)).status, 204);
});
