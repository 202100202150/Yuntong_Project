import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { startBackend } from '../src/index.mjs';
import { openProductDatabase } from '../src/product/database.mjs';
import { createMonitorCapturePool, cameraSourceUrl } from '../src/product/monitor-capture.mjs';

const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 0xff, 0xd9]);
const deviceBody = { label: 'Camera at north gate', host: '192.0.2.8', port: 554, username: 'operator', password: 'never-print-this-password', channel: 2, stream: 'sub', path: '/live/main' };

async function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'yuntong-monitor-'));
  const frames = new Map(), stopped = [], bridgeRequests = [], applied = [];
  let frameAge = 0;
  const pool = { state: id => ({ state: frames.has(id) ? 'streaming' : 'stopped', frameAvailable: frames.has(id), sequence: frames.get(id)?.sequence || 0, lastFrameAt: frames.get(id)?.receivedAt }),
    frame: (device, after) => { const frame = frames.get(device.id); return frame && frame.sequence > after ? frame : null; }, stop: id => stopped.push(id), close() { stopped.push('*'); } };
  const bridge = { configure: value => applied.push(value), restart: async () => {}, stop: async () => {}, request: async (route, options) => {
    bridgeRequests.push({ route, options });
    if (route.endsWith('/frame.jpg')) return { statusCode: 200, data: JPEG, headers: { 'x-frame-sequence': '17', 'x-frame-width': '64', 'x-frame-height': '48', 'x-frame-received-at': String(Date.now() - frameAge) } };
    throw Error('offline');
  } };
  const runtime = await startBackend({ port: 0, dataDirectory: directory, staticDirectory: path.join(directory, 'no-ui'), startBridge: false, bridge, monitorOptions: { capturePool: pool } });
  let cookie = '';
  const call = async (route, method = 'GET', body) => {
    const response = await fetch(runtime.url + '/api' + route, { method, headers: { ...(cookie ? { cookie } : {}), ...(body !== undefined ? { 'content-type': 'application/json' } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
    const value = response.status === 204 ? null : response.headers.get('content-type')?.includes('application/json') ? await response.json() : Buffer.from(await response.arrayBuffer());
    return { status: response.status, value, headers: response.headers };
  };
  const setup = async () => { const result = await call('/auth/setup', 'POST', { username: 'admin', displayName: 'Admin', password: 'strong-password-2026' }); cookie = result.headers.get('set-cookie').split(';')[0]; };
  t.after(async () => { await runtime.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  return { runtime, directory, call, setup, frames, stopped, bridgeRequests, applied, setCookie: value => { cookie = value; }, setFrameAge: value => { frameAge = value; } };
}

test('monitor device configuration is durable, sanitized, permission checked and independent of the stereo slots', async t => {
  const f = await fixture(t);
  assert.equal((await f.call('/monitor/devices')).status, 401);
  await f.setup();
  const initial = await f.call('/monitor/devices');
  assert.equal(initial.value.devices.length, 2);
  assert.ok(initial.value.devices.every(device => device.isStereo && !device.runtime.frameAvailable));
  assert.equal((await f.call('/monitor/devices', 'POST', { ...deviceBody, host: '127.0.0.1;echo secret' })).status, 400);
  const created = await f.call('/monitor/devices', 'POST', deviceBody);
  assert.equal(created.status, 201);
  assert.equal(created.value.device.id, 'camera3');
  assert.equal(created.value.device.code, 'C3');
  assert.equal(created.value.device.isStereo, false);
  assert.equal(created.value.device.passwordConfigured, true);
  assert.ok(!JSON.stringify(created.value).includes(deviceBody.password));
  const otherConnection = openProductDatabase(f.directory);
  assert.equal(otherConnection.get('monitorDevices')[0].password, deviceBody.password);
  otherConnection.db.close();
  assert.equal((await f.call('/monitor/devices/camera3', 'PATCH', { label: 'Renamed', password: '' })).value.device.label, 'Renamed');
  assert.equal(f.runtime.store.get('monitorDevices')[0].password, deviceBody.password);
  assert.ok(f.stopped.includes('camera3'));
  assert.equal((await f.call('/monitor/devices/camera1', 'DELETE')).status, 409);
  const fixed = await f.call('/monitor/devices/camera1', 'PATCH', { host: '192.0.2.2', path: '/custom/stream', password: 'stereo-secret' });
  assert.equal(fixed.value.applied, true);
  assert.equal(f.applied.at(-1).env.CAMERA1_RTSP_PATH, '/custom/stream');
  assert.equal((await f.call('/monitor/devices/camera3', 'DELETE')).status, 200);
  assert.equal((await f.call('/monitor/devices', 'POST', deviceBody)).value.device.id, 'camera4');
  await f.call('/users', 'POST', { username: 'viewer', displayName: 'Viewer', password: 'strong-password-2026', role: 'operator' });
  const login = await f.call('/auth/login', 'POST', { username: 'viewer', password: 'strong-password-2026' }); f.setCookie(login.headers.get('set-cookie').split(';')[0]);
  assert.equal((await f.call('/monitor/devices')).status, 200);
  assert.equal((await f.call('/monitor/devices', 'POST', deviceBody)).status, 403);
  assert.equal((await f.call('/monitor/devices/camera4', 'DELETE')).status, 403);
});

test('scene registration persists only finite explicit input and never invents default camera fields', async t => {
  const f = await fixture(t); await f.setup();
  const initial = (await f.call('/monitor/scene-config')).value.config;
  assert.equal(initial.registered, false); assert.deepEqual(initial.cameraDirections, {});
  const config = { ...initial, registered: true, origin: { x: 100, y: 2, z: -20 }, yawDeg: 45, scale: 0.01,
    cameraDirections: { camera1: { yawDeg: 15, pitchDeg: 5, horizontalFovDeg: 68, verticalFovDeg: 42, rangeMeters: 80 } }, cameraPositions: { camera2: { x: 2, y: 3, z: 4 } } };
  assert.equal((await f.call('/monitor/scene-config', 'PUT', { config })).status, 200);
  assert.deepEqual((await f.call('/monitor/scene-config')).value.config, config);
  for (const invalid of [{ ...config, scale: 0 }, { ...config, registered: 'true' }, { ...config, origin: { x: null, y: 0, z: 0 } }, { ...config, cameraDirections: { missing: {} } }, { ...config, cameraDirections: { camera1: { horizontalFovDeg: 180 } } }, { ...config, cameraPositions: { missing: { x: 1, y: 2, z: 3 } } }, { ...config, cameraPositions: { camera1: { x: null, y: 1, z: 2 } } }]) {
    assert.equal((await f.call('/monitor/scene-config', 'PUT', invalid)).status, 400);
  }
  assert.deepEqual((await f.call('/monitor/scene-config')).value.config, config);
  const added = await f.call('/monitor/devices', 'POST', deviceBody);
  config.cameraPositions[added.value.device.id] = { x: 5, y: 6, z: 7 };
  config.cameraDirections[added.value.device.id] = { yawDeg: 10, pitchDeg: 5, horizontalFovDeg: 60, verticalFovDeg: 40, rangeMeters: 100 };
  assert.equal((await f.call('/monitor/scene-config', 'PUT', config)).status, 200);
  assert.equal((await f.call('/monitor/devices/' + added.value.device.id, 'DELETE')).status, 200);
  const afterDelete = (await f.call('/monitor/scene-config')).value.config;
  assert.equal(afterDelete.cameraPositions[added.value.device.id], undefined);
  assert.equal(afterDelete.cameraDirections[added.value.device.id], undefined);
});

test('monitor frames and snapshots use real provider bytes; unavailable and stale streams do not produce images', async t => {
  const f = await fixture(t); await f.setup();
  await f.call('/monitor/devices', 'POST', deviceBody);
  assert.equal((await f.call('/monitor/cameras/camera3/frame.jpg')).status, 204);
  assert.equal((await f.call('/monitor/cameras/camera3/snapshot', 'POST')).status, 409);
  assert.equal((await f.call('/monitor/cameras/unknown/frame.jpg')).status, 404);
  assert.equal((await f.call('/monitor/cameras/camera3/frame.jpg?afterSequence=-1')).status, 400);
  f.frames.set('camera3', { jpeg: JPEG, sequence: 9, width: 64, height: 48, receivedAt: Date.now() });
  const image = await f.call('/monitor/cameras/camera3/frame.jpg?afterSequence=8');
  assert.equal(image.status, 200); assert.deepEqual(image.value, JPEG); assert.equal(image.headers.get('x-frame-sequence'), '9');
  assert.equal((await f.call('/monitor/cameras/camera3/frame.jpg?afterSequence=9')).status, 204);
  const snapshot = await f.call('/monitor/cameras/camera3/snapshot', 'POST');
  assert.equal(snapshot.status, 200); assert.deepEqual(snapshot.value, JPEG); assert.match(snapshot.headers.get('content-disposition'), /attachment/);
  f.frames.get('camera3').receivedAt = Date.now() - 10_000;
  assert.equal((await f.call('/monitor/cameras/camera3/snapshot', 'POST')).status, 409);
  const stereo = await f.call('/monitor/cameras/camera1/frame.jpg?afterSequence=16');
  assert.equal(stereo.status, 200); assert.deepEqual(stereo.value, JPEG);
  const request = f.bridgeRequests.find(request => request.route === '/v1/cameras/camera1/frame.jpg');
  assert.equal(request.options.requestHeaders['X-After-Frame-Sequence'], '16');
  // The legacy endpoint must also honor the injected runtime instead of a global sidecar.
  assert.equal((await f.call('/cameras/camera2/frame.jpg')).status, 200);
  f.setFrameAge(60_000);
  assert.equal((await f.call('/monitor/cameras/camera1/frame.jpg')).status, 204);
  assert.equal((await f.call('/monitor/cameras/camera1/snapshot', 'POST')).status, 409);
});

test('on-demand decoder has bounded fragmented framing, stale detection and deterministic process cleanup', async () => {
  let now = Date.now(); const launches = [];
  function launch(executable, args, options) {
    const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.killed = false; child.kill = () => { child.killed = true; }; let input = '';
    child.stdin.on('data', value => { input += value; });
    launches.push({ executable, args, options, child, input: () => input }); return child;
  }
  const pool = createMonitorCapturePool({ project: '/camera-project', python: 'python', spawnCapture: launch, now: () => now, idleMs: 25, freshMs: 10 });
  try {
    const device = { ...deviceBody, id: 'camera3' };
    assert.equal(pool.frame(device, 0), null); assert.equal(launches.length, 1);
    const launched = launches[0];
    assert.equal(launched.options.shell, false); assert.equal(launched.options.windowsHide, true);
    assert.ok(!launched.args.join(' ').includes(device.password));
    assert.equal(new URL(JSON.parse(launched.input()).url).password, device.password);
    assert.equal(pool.state(device.id).frameAvailable, false);
    const header = Buffer.alloc(24); header.write('YTF1'); header.writeUInt32BE(JPEG.length, 4); header.writeUInt32BE(64, 8); header.writeUInt32BE(48, 12); header.writeBigUInt64BE(BigInt(now), 16);
    const packet = Buffer.concat([header, JPEG]);
    launched.child.stdout.write(packet.subarray(0, 17)); assert.equal(pool.frame(device, 0), null);
    launched.child.stdout.write(packet.subarray(17)); assert.deepEqual(pool.frame(device, 0).jpeg, JPEG);
    assert.equal(pool.frame(device, 1), null); assert.equal(pool.state(device.id).state, 'streaming');
    now += 11; assert.equal(pool.state(device.id).frameAvailable, false);
    now += 30; await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(launched.child.killed, true); assert.equal(pool.state(device.id).state, 'stopped');
    pool.frame(device, 0); assert.equal(launches.length, 2);
    pool.close(); assert.equal(launches[1].child.killed, true);
  } finally { pool.close(); }
});

test('camera URLs encode credentials and preserve camera path queries without shell processing', () => {
  const url = new URL(cameraSourceUrl({ ...deviceBody, password: 'a:b@c/#', username: 'test user', path: '/cam/realmonitor?channel=1&subtype=0' }));
  assert.equal(decodeURIComponent(url.password), 'a:b@c/#'); assert.equal(decodeURIComponent(url.username), 'test user');
  assert.equal(url.pathname, '/cam/realmonitor'); assert.equal(url.searchParams.get('subtype'), '0');
});
