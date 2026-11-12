import test from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import { createServer } from 'node:http';
import { createSpatialRouter } from '../src/routes/spatial.mjs';

async function startServer(t, requestBridge) {
  const app = express();
  app.use(express.json());
  app.use('/api', createSpatialRouter(requestBridge));
  const server = createServer(app);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}`;
}

test('GET forwards to sidecar and returns its spatial config', async (t) => {
  const calls = [];
  const base = await startServer(t, async (...args) => {
    calls.push(args);
    return { data: { config: { version: 1, units: 'm' } } };
  });
  const response = await fetch(`${base}/api/spatial/config`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { config: { version: 1, units: 'm' } });
  assert.deepEqual(calls, [['/v1/spatial/config']]);
});

test('POST forwards the config and malformed bodies return 400 without a sidecar call', async (t) => {
  const calls = [];
  const base = await startServer(t, async (...args) => {
    calls.push(args);
    return { data: { config: args[1].body.config } };
  });
  const config = { version: 1, units: 'm', rig: { baselineMeters: 0.18 } };
  const response = await fetch(`${base}/api/spatial/config`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ config }),
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { config });
  assert.equal(calls[0][0], '/v1/spatial/config');
  assert.deepEqual(calls[0][1], { method: 'POST', body: { config } });

  const invalid = await fetch(`${base}/api/spatial/config`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ config: [] }),
  });
  assert.equal(invalid.status, 400);
  assert.equal(calls.length, 1);
});

test('POST derive forwards the draft to the sidecar derivation endpoint', async (t) => {
  const calls = [];
  const config = { version: 1, units: 'm', extrinsics: { enabled: true } };
  const derived = { ...config, rig: { geometrySource: 'extrinsics', baselineMeters: 4 } };
  const base = await startServer(t, async (...args) => {
    calls.push(args);
    return { data: { config: derived } };
  });
  const response = await fetch(`${base}/api/spatial/config/derive`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ config }),
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { config: derived });
  assert.deepEqual(calls, [['/v1/spatial/config/derive', { method: 'POST', body: { config } }]]);
});

test('sidecar validation errors map to 400 and sidecar outages map to 503', async (t) => {
  const base = await startServer(t, async () => {
    const error = new Error('invalid config');
    error.statusCode = 400;
    throw error;
  });
  const invalid = await fetch(`${base}/api/spatial/config`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ config: {} }),
  });
  assert.equal(invalid.status, 400);

  const offline = await startServer(t, async () => { throw new Error('sidecar unavailable'); });
  const unavailable = await fetch(`${offline}/api/spatial/config`);
  assert.equal(unavailable.status, 503);
});
