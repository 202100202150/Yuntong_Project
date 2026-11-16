import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

const MAGIC = Buffer.from('YTF1');
const MAX_FRAME_BYTES = 8 * 1024 * 1024;

export function cameraSourceUrl(device) {
  const host = device.host.includes(':') && !device.host.startsWith('[') ? '[' + device.host + ']' : device.host;
  const url = new URL(`${device.protocol || 'rtsp'}://${host}:${device.port}`);
  const sourcePath = device.path || `/Streaming/Channels/${device.channel}${device.stream === 'sub' ? '02' : '01'}`;
  const split = sourcePath.indexOf('?');
  url.pathname = split < 0 ? sourcePath : sourcePath.slice(0, split);
  if (split >= 0) url.search = sourcePath.slice(split + 1);
  if (device.username) url.username = device.username;
  if (device.password) url.password = device.password;
  return url.toString();
}

export function resolveMonitorPython() {
  const root = path.resolve(process.env.YUNTONG_ROOT || path.resolve(import.meta.dirname, '../../../desktop-app'));
  if (process.env.YUNTONG_CAMERA_PYTHON) return path.resolve(root, process.env.YUNTONG_CAMERA_PYTHON);
  const bundled = path.join(root, 'work', 'gpu-runtime', '.venv', 'Scripts', 'python.exe');
  return fs.existsSync(bundled) ? bundled : 'python';
}

/** One real decoder per requested non-stereo camera; polling extends its lease. */
export function createMonitorCapturePool({ project, spawnCapture = spawn, python = resolveMonitorPython(), idleMs = 20_000, freshMs = 5000, now = Date.now } = {}) {
  const entries = new Map();
  let closed = false;
  function state(id) {
    const entry = entries.get(id);
    if (!entry) return { state: 'stopped', error: '', width: 0, height: 0, frameAvailable: false, sequence: 0, previewFps: 0, lastFrameAt: null };
    const fresh = Boolean(entry.frame && now() - entry.frame.receivedAt <= freshMs && entry.child);
    return { state: fresh ? 'streaming' : entry.child ? 'connecting' : entry.error ? 'error' : 'stopped', error: fresh ? '' : entry.error,
      width: entry.frame?.width || 0, height: entry.frame?.height || 0, frameAvailable: fresh, sequence: entry.sequence,
      previewFps: fresh ? entry.fps : 0, lastFrameAt: entry.frame?.receivedAt || null };
  }
  function stop(id) {
    const entry = entries.get(id);
    if (!entry) return;
    entry.stopping = true;
    const child = entry.child;
    entry.child = null;
    entry.frame = null;
    entry.buffer = Buffer.alloc(0);
    child?.kill();
    entries.delete(id);
  }
  function start(device) {
    const prior = entries.get(device.id);
    if (closed || prior?.child || prior && now() - prior.lastAttempt < 3000) return prior;
    const entry = prior || { sequence: 0 };
    Object.assign(entry, { child: null, buffer: Buffer.alloc(0), error: '', stopping: false, lastAttempt: now(), lastDemand: now(), frame: null, fps: 0, fpsSince: now(), fpsCount: 0 });
    entries.set(device.id, entry);
    let child;
    try {
      const source = cameraSourceUrl(device);
      child = spawnCapture(python, [path.join(project, 'monitor_capture.py')], { cwd: project, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONUNBUFFERED: '1' } });
      entry.child = child;
      // Credentials go over stdin, never command-line arguments or diagnostic logs.
      child.stdin.on('error', () => {});
      child.stdin.end(JSON.stringify({ url: source, maxWidth: 960, maxFps: 15 }) + '\n');
    } catch {
      child?.kill(); entry.child = null;
      entry.error = '无法启动视频采集，请检查 Python 与 OpenCV 运行环境';
      return entry;
    }
    const fail = (message) => {
      if (entry.stopping || entries.get(device.id) !== entry || entry.child !== child) return;
      entry.error = message;
      entry.child = null;
      entry.frame = null;
      entry.buffer = Buffer.alloc(0);
      child.kill();
    };
    child.stdout.on('data', chunk => {
      if (entry.child !== child) return;
      entry.buffer = Buffer.concat([entry.buffer, chunk]);
      while (entry.buffer.length >= 24) {
        if (!entry.buffer.subarray(0, 4).equals(MAGIC)) return fail('视频采集进程返回了无效数据');
        const length = entry.buffer.readUInt32BE(4), width = entry.buffer.readUInt32BE(8), height = entry.buffer.readUInt32BE(12);
        const receivedAt = Number(entry.buffer.readBigUInt64BE(16));
        if (!length || length > MAX_FRAME_BYTES || !width || !height || width > 16384 || height > 16384 || !Number.isSafeInteger(receivedAt)) return fail('视频帧超出允许范围');
        if (entry.buffer.length < 24 + length) break;
        const jpeg = Buffer.from(entry.buffer.subarray(24, 24 + length));
        entry.buffer = entry.buffer.subarray(24 + length);
        if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8 || jpeg.at(-2) !== 0xff || jpeg.at(-1) !== 0xd9) return fail('视频采集未返回有效 JPEG');
        entry.sequence++;
        entry.frame = { jpeg, width, height, receivedAt, sequence: entry.sequence };
        entry.error = '';
        entry.fpsCount++;
        const elapsed = now() - entry.fpsSince;
        if (elapsed >= 1000) { entry.fps = Math.round(entry.fpsCount * 1000 / elapsed); entry.fpsCount = 0; entry.fpsSince = now(); }
      }
      if (entry.buffer.length > MAX_FRAME_BYTES + 24) fail('视频采集缓冲区超出限制');
    });
    // Decoder diagnostics may contain credentials. Never forward them to the API/logs.
    child.stderr.on('data', () => {});
    child.once('error', () => fail('无法启动视频采集，请检查 Python 与 OpenCV 运行环境'));
    child.once('exit', code => {
      if (entry.stopping || entries.get(device.id) !== entry || entry.child !== child) return;
      entry.child = null; entry.frame = null; entry.buffer = Buffer.alloc(0);
      entry.error = code === 3 ? '无法打开视频流，请检查地址、账号和网络' : code === 4 ? '视频流已中断，正在等待重新连接' : '视频采集已停止，请检查采集运行环境';
    });
    return entry;
  }
  function frame(device, afterSequence = 0) {
    const entry = entries.get(device.id) || start(device);
    if (!entry) return null;
    entry.lastDemand = now();
    if (!entry.child) start(device);
    if (entry.frame && state(device.id).frameAvailable && entry.frame.sequence > afterSequence) return entry.frame;
    // A restarted decoder does not reuse a client's previous sequence.
    if (entry.frame && state(device.id).frameAvailable && afterSequence > entry.sequence) return entry.frame;
    return null;
  }
  const timer = setInterval(() => {
    for (const [id, entry] of entries) {
      if (now() - entry.lastDemand > idleMs) stop(id);
      else if (entry.child && now() - (entry.frame?.receivedAt || entry.lastAttempt) > 12_000) {
        entry.error = '视频连接超时，正在等待重新连接';
        const child = entry.child; entry.child = null; entry.frame = null; child.kill();
      }
    }
  }, Math.min(1000, idleMs));
  timer.unref();
  function close() { closed = true; clearInterval(timer); for (const id of [...entries.keys()]) stop(id); }
  return { state, frame, stop, close };
}
