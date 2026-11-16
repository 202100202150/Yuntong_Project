import net from 'node:net';
import { Router } from 'express';
import { asyncRoute, fault, requiredText } from './database.mjs';
import { permission } from './auth.mjs';
import { cameraSourceUrl, createMonitorCapturePool } from './monitor-capture.mjs';

const STEREO_IDS = new Set(['camera1', 'camera2']);
const EMPTY_SCENE = { version: 1, registered: false, origin: { x: 0, y: 0, z: 0 }, yawDeg: 0, scale: 1, cameraDirections: {}, cameraPositions: {} };
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function number(value, label, min, max) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) throw fault(400, label + '超出范围');
  return value;
}
function point(value, label) {
  if (!isObject(value)) throw fault(400, label + '格式无效');
  return Object.fromEntries(['x', 'y', 'z'].map(axis => [axis, number(value[axis], label, -1_000_000, 1_000_000)]));
}
export function validateSceneConfig(value, knownIds) {
  if (!isObject(value) || typeof value.registered !== 'boolean') throw fault(400, '场景配准参数无效');
  if (!isObject(value.cameraDirections ?? {})) throw fault(400, '相机方向参数无效');
  if (!isObject(value.cameraPositions ?? {})) throw fault(400, '相机位置参数无效');
  const cameraDirections = {};
  const cameraPositions = {};
  for (const [id, position] of Object.entries(value.cameraPositions ?? {})) {
    if (!knownIds.has(id)) throw fault(400, '相机位置配置含未知设备');
    cameraPositions[id] = point(position, '相机位置');
  }
  for (const [id, direction] of Object.entries(value.cameraDirections ?? {})) {
    if (!knownIds.has(id) || !isObject(direction)) throw fault(400, '相机方向配置含未知设备');
    const result = {};
    for (const [key, min, max] of [['yawDeg', -360, 360], ['pitchDeg', -90, 90], ['horizontalFovDeg', 1, 179], ['verticalFovDeg', 1, 179], ['rangeMeters', 0.01, 100_000]]) {
      if (direction[key] !== undefined) result[key] = number(direction[key], '相机视域', min, max);
    }
    cameraDirections[id] = result;
  }
  return { version: 1, registered: value.registered, origin: point(value.origin, '场景原点'), yawDeg: number(value.yawDeg, '场景朝向', -360, 360), scale: number(value.scale, '场景缩放', 0.000001, 1_000_000), cameraDirections, cameraPositions };
}

function validateDevice(body, old = {}) {
  if (!isObject(body)) throw fault(400, '设备参数无效');
  const next = { ...old, label: requiredText(body.label ?? old.label, '设备名称'), host: requiredText(body.host ?? old.host, '设备地址', 253),
    port: Number(body.port ?? old.port ?? 554), username: body.username ?? old.username ?? '', password: old.password || '',
    channel: Number(body.channel ?? old.channel ?? 1), stream: body.stream ?? old.stream ?? 'main', protocol: body.protocol ?? old.protocol ?? 'rtsp', path: body.path ?? old.path ?? '' };
  if (!/^[a-zA-Z0-9.:-]+$/.test(next.host) || !Number.isInteger(next.port) || next.port < 1 || next.port > 65535 || !Number.isInteger(next.channel) || next.channel < 1 || next.channel > 64 || !['main', 'sub'].includes(next.stream) || !['rtsp', 'rtsps', 'http', 'https'].includes(next.protocol)) throw fault(400, '设备连接参数无效');
  if (typeof next.username !== 'string' || next.username.length > 128 || /[\r\n\x00]/.test(next.username)) throw fault(400, '设备账号格式无效');
  if (typeof next.path !== 'string' || next.path.length > 1024 || next.path && (!next.path.startsWith('/') || /[\r\n\x00]/.test(next.path))) throw fault(400, '码流路径应以 / 开头且不得包含换行');
  if (body.password !== undefined && body.password !== '') {
    if (typeof body.password !== 'string' || body.password.length > 256 || /[\r\n\x00]/.test(body.password)) throw fault(400, '设备密码格式无效');
    next.password = body.password;
  }
  if (body.clearPassword === true) next.password = '';
  if (STEREO_IDS.has(old.id) && next.protocol !== 'rtsp') throw fault(400, '双目固定通道目前使用 RTSP，请选择 RTSP 协议');
  try { cameraSourceUrl(next); } catch { throw fault(400, '设备地址或码流路径无效'); }
  return next;
}

export function createMonitorService(store, config, bridge, options = {}) {
  const router = Router();
  const pool = options.capturePool || createMonitorCapturePool({ project: config.project, ...options.captureOptions });
  const extras = () => store.get('monitorDevices', []);
  const allDevices = () => [...config.devices(), ...extras()];
  const find = id => { const device = allDevices().find(item => item.id === id); if (!device) throw fault(404, '设备不存在'); return device; };
  const scene = () => ({ ...structuredClone(EMPTY_SCENE), ...store.get('monitorSceneConfig', {}) });
  const publicDevice = (device, live) => {
    const { password, ...safe } = device;
    const isStereo = STEREO_IDS.has(device.id);
    const raw = isStereo ? live?.cameras?.[device.id] : pool.state(device.id);
    const runtime = raw ? { state: raw.state, error: raw.error || '', width: raw.width || 0, height: raw.height || 0, frameAvailable: Boolean(raw.frameAvailable),
      sequence: raw.sequence || 0, previewFps: raw.previewFps || 0, lastFrameAt: raw.lastFrameAt || null,
      detectionCount: isStereo ? raw.detectionCount || 0 : 0, objects: isStereo ? raw.objects || [] : [], recordingState: isStereo ? raw.recordingState || 'idle' : 'idle' }
      : { state: 'stopped', error: '', width: 0, height: 0, frameAvailable: false, sequence: 0, previewFps: 0, lastFrameAt: null, detectionCount: 0, objects: [], recordingState: 'idle' };
    return { ...safe, protocol: safe.protocol || 'rtsp', path: safe.path || '', code: 'C' + device.id.replace(/^camera/, ''), passwordConfigured: Boolean(password), isStereo, runtime };
  };
  const list = async () => { const live = await bridge.status(); return allDevices().map(device => publicDevice(device, live)); };
  router.get('/monitor/devices', permission('monitor'), asyncRoute(async (_req, res) => res.json({ devices: await list() })));
  router.post('/monitor/devices', permission('devices'), asyncRoute(async (req, res) => {
    if (allDevices().length >= 64) throw fault(409, '最多配置 64 路设备，请先删除不再使用的设备');
    const validated = validateDevice(req.body);
    const nextIndex = Math.max(2, store.get('monitorNextCamera', 2), ...allDevices().map(device => Number(device.id.replace(/^camera/, '')) || 0)) + 1;
    const device = { ...validated, id: 'camera' + nextIndex };
    store.transaction(() => { store.set('monitorDevices', [...extras(), device]); store.set('monitorNextCamera', nextIndex); store.audit(req.user.username, '新增监控设备', { id: device.id, label: device.label }); });
    res.status(201).json({ device: publicDevice(device), devices: await list(), applied: true });
  }));
  router.patch('/monitor/devices/:id', permission('devices'), asyncRoute(async (req, res) => {
    const old = find(req.params.id), next = validateDevice(req.body, old);
    let applied = { applied: true };
    if (STEREO_IDS.has(old.id)) {
      store.set('devices', config.devices().map(device => device.id === old.id ? next : device));
      applied = await config.apply();
    } else {
      store.set('monitorDevices', extras().map(device => device.id === old.id ? next : device));
      pool.stop(old.id);
    }
    store.audit(req.user.username, '更新监控设备', { id: next.id, label: next.label });
    res.json({ device: publicDevice(next, await bridge.status()), devices: await list(), ...applied });
  }));
  router.delete('/monitor/devices/:id', permission('devices'), asyncRoute(async (req, res) => {
    const device = find(req.params.id);
    if (STEREO_IDS.has(device.id)) throw fault(409, 'C1 和 C2 是双目定位固定通道，不能删除，可编辑其连接配置');
    store.transaction(() => {
      store.set('monitorDevices', extras().filter(item => item.id !== device.id));
      const current = scene(); delete current.cameraDirections?.[device.id]; delete current.cameraPositions?.[device.id]; store.set('monitorSceneConfig', current);
      store.audit(req.user.username, '删除监控设备', { id: device.id, label: device.label });
    });
    pool.stop(device.id);
    res.json({ devices: await list(), deleted: device.id });
  }));
  router.post('/monitor/devices/:id/test', permission('devices'), asyncRoute(async (req, res) => {
    const device = find(req.params.id);
    if (!device.host) throw fault(400, '请先配置设备地址');
    const result = await new Promise(resolve => {
      const start = Date.now(), socket = net.createConnection({ host: device.host, port: device.port }); let settled = false;
      const finish = connected => { if (settled) return; settled = true; socket.destroy(); resolve({ connected, latencyMs: connected ? Date.now() - start : null }); };
      socket.setTimeout(3000, () => finish(false)); socket.once('connect', () => finish(true)); socket.once('error', () => finish(false));
    });
    res.json(result);
  }));
  async function cameraFrame(device, after = 0) {
    if (!STEREO_IDS.has(device.id)) return pool.frame(device, after);
    try {
      const result = await bridge.request(`/v1/cameras/${device.id}/frame.jpg`, { responseType: 'buffer', timeoutMs: 2000,
        requestHeaders: { 'X-After-Frame-Sequence': String(after), 'X-Wait-Milliseconds': '350' } });
      if (result.statusCode === 204 || !result.data) return null;
      const frame = { jpeg: result.data, sequence: Number(result.headers?.['x-frame-sequence']), width: Number(result.headers?.['x-frame-width']), height: Number(result.headers?.['x-frame-height']), receivedAt: Number(result.headers?.['x-frame-received-at']) };
      if (![frame.sequence, frame.width, frame.height, frame.receivedAt].every(value => Number.isSafeInteger(value) && value > 0)) throw fault(503, '摄像机未提供有效画面元数据');
      // The legacy preview endpoint can retain its last packet after capture stops.
      // It must not turn an old cached image into a new live frame in this monitor.
      if (Date.now() - frame.receivedAt > 5000 || frame.receivedAt - Date.now() > 5000) return null;
      return frame;
    } catch { throw fault(503, '摄像机服务未提供画面，请检查视频连接'); }
  }
  function sendFrame(res, frame) {
    res.set({ 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store', 'X-Frame-Sequence': String(frame.sequence), 'X-Frame-Width': String(frame.width), 'X-Frame-Height': String(frame.height), 'X-Frame-Received-At': String(frame.receivedAt) });
    res.send(frame.jpeg);
  }
  router.get('/monitor/cameras/:id/frame.jpg', permission('monitor'), asyncRoute(async (req, res) => {
    const after = Number(req.query.afterSequence ?? 0);
    if (!Number.isSafeInteger(after) || after < 0) throw fault(400, '画面序号无效');
    const frame = await cameraFrame(find(req.params.id), after);
    if (!frame) return res.status(204).end();
    sendFrame(res, frame);
  }));
  router.post('/monitor/cameras/:id/snapshot', permission('export'), asyncRoute(async (req, res) => {
    const device = find(req.params.id), frame = await cameraFrame(device, 0);
    if (!frame || Date.now() - frame.receivedAt > 5000) throw fault(409, '当前设备没有可抓拍的实时画面');
    store.audit(req.user.username, '抓拍监控画面', { id: device.id, sequence: frame.sequence });
    res.set('Content-Disposition', `attachment; filename="${device.id}-${Date.now()}.jpg"`);
    sendFrame(res, frame);
  }));
  router.get('/monitor/scene-config', permission('monitor'), (_req, res) => res.json({ config: scene() }));
  router.put('/monitor/scene-config', permission('devices'), (req, res, next) => {
    try {
      const value = validateSceneConfig(req.body?.config ?? req.body, new Set(allDevices().map(device => device.id)));
      store.set('monitorSceneConfig', value); store.audit(req.user.username, '更新模型空间配准', { registered: value.registered });
      res.json({ config: value });
    } catch (error) { next(error); }
  });
  return { router, close: () => pool.close(), pool, list, scene };
}
