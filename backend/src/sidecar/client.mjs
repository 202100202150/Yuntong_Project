import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { randomBytes } from 'node:crypto';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const backendRoot = path.resolve(__dirname, '..', '..');
const defaultRoot = path.resolve(backendRoot, '..', 'desktop-app');
const softwareRootPath = path.resolve(process.env.YUNTONG_ROOT || defaultRoot);

const cameraBridge = {
  state: 'stopped',
  error: '',
  child: null,
  baseUrl: '',
  token: '',
  startPromise: null,
  shuttingDown: false,
};

export function resolveCameraProject() {
  const configured = process.env.YUNTONG_CAMERA_PROJECT_DIR?.trim();
  const monorepoProjectPath = path.resolve(softwareRootPath, '..', 'camera-rtsp-low-latency');
  const projectPath = configured
    ? path.resolve(softwareRootPath, configured)
    : monorepoProjectPath;
  const pythonPath = process.env.YUNTONG_CAMERA_PYTHON
    ? path.resolve(softwareRootPath, process.env.YUNTONG_CAMERA_PYTHON)
    : path.join(softwareRootPath, 'work', 'gpu-runtime', '.venv', 'Scripts', 'python.exe');
  const scriptPath = path.join(projectPath, 'camera_bridge.py');
  const modelPath = process.env.YUNTONG_PERSON_MODEL
    ? path.resolve(softwareRootPath, process.env.YUNTONG_PERSON_MODEL)
    : path.join(softwareRootPath, 'models', 'person-v1.onnx');
  const engineCachePath = process.env.YUNTONG_TENSORRT_CACHE
    ? path.resolve(softwareRootPath, process.env.YUNTONG_TENSORRT_CACHE)
    : path.join(softwareRootPath, 'work', 'tensorrt');

  for (const [label, p] of [
    ['Python 环境', pythonPath],
    ['摄像机桥接', scriptPath],
    ['person ONNX', modelPath],
  ]) {
    if (!fs.existsSync(p)) throw new Error(`${label}不存在：${p}`);
  }
  return { projectPath, pythonPath, scriptPath, modelPath, engineCachePath };
}

export function startCameraBridge() {
  if (cameraBridge.state === 'ready') return Promise.resolve();
  if (cameraBridge.startPromise) return cameraBridge.startPromise;
  cameraBridge.state = 'starting';
  cameraBridge.error = '';

  cameraBridge.startPromise = new Promise((resolve, reject) => {
    let settled = false;
    let timeout;
    try {
      const paths = resolveCameraProject();
      const token = randomBytes(32).toString('hex');
      const child = spawn(
        paths.pythonPath,
        [
          paths.scriptPath,
          '--preview-width', '768',
          '--preview-height', '432',
          '--preview-jpeg-quality', '78',
          '--preview-max-fps', '30',
          '--detection-fps', '12.5',
        ],
        {
          cwd: paths.projectPath,
          env: {
            ...process.env,
            CAMERA_BRIDGE_TOKEN: token,
            CAMERA_PERSON_MODEL: paths.modelPath,
            CAMERA_TENSORRT_CACHE: paths.engineCachePath,
            PYTHONUNBUFFERED: '1',
          },
          windowsHide: true,
          shell: false,
          stdio: ['ignore', 'pipe', 'pipe'],
        },
      );
      cameraBridge.child = child;
      cameraBridge.token = token;

      const fail = (msg) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        cameraBridge.state = 'error';
        cameraBridge.error = msg;
        cameraBridge.startPromise = null;
        reject(new Error(msg));
      };

      const lines = readline.createInterface({ input: child.stdout });
      lines.on('line', (line) => {
        if (settled || !line.trim().startsWith('{')) return;
        let message;
        try { message = JSON.parse(line); } catch { return; }
        if (message?.type !== 'camera-bridge-ready' || message.host !== '127.0.0.1') return;
        const port = Number(message.port);
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
          fail('摄像机桥接返回了无效端口。');
          return;
        }
        settled = true;
        clearTimeout(timeout);
        cameraBridge.baseUrl = `http://127.0.0.1:${port}`;
        cameraBridge.state = 'ready';
        cameraBridge.error = '';
        cameraBridge.startPromise = null;
        resolve();
      });

      // 👇 核心修改：把 Python 的 stderr 打印出来，不再默默吞掉
      child.stderr.on('data', (data) => {
        console.error(`[Python 报错] ${data.toString().trim()}`);
      });

      child.once('error', () => fail('无法启动本地摄像机服务。'));
      child.once('exit', (code) => {
        lines.close();
        cameraBridge.child = null;
        cameraBridge.baseUrl = '';
        cameraBridge.startPromise = null;
        if (!settled) {
          fail(`摄像机服务启动失败（退出码 ${code ?? 'unknown'}）。`);
          return;
        }
        if (!cameraBridge.shuttingDown) {
          cameraBridge.state = 'error';
          cameraBridge.error = '摄像机服务意外停止。';
        }
      });
      timeout = setTimeout(() => {
        fail('摄像机服务启动超时。');
        child.kill();
      }, 25_000);
    } catch (error) {
      const message = error instanceof Error ? error.message : '摄像机服务启动失败。';
      cameraBridge.state = 'error';
      cameraBridge.error = message;
      cameraBridge.startPromise = null;
      reject(new Error(message));
    }
  });
  return cameraBridge.startPromise;
}

export function requestCameraBridge(pathname, options = {}) {
  const {
    method = 'GET',
    body = null,
    responseType = 'json',
    timeoutMs = 6_000,
    ensureStarted = true,
    requestHeaders = null,
  } = options;

  const begin = ensureStarted ? startCameraBridge() : Promise.resolve();
  return begin.then(() => new Promise((resolve, reject) => {
    if (!cameraBridge.baseUrl || !cameraBridge.token) {
      reject(new Error(cameraBridge.error || '摄像机服务尚未就绪。'));
      return;
    }
    const url = new URL(pathname, cameraBridge.baseUrl);
    const payload = body === null
      ? null
      : Buffer.isBuffer(body)
        ? body
        : Buffer.from(JSON.stringify(body), 'utf8');
    const request = http.request(url, {
      method,
      headers: {
        ...requestHeaders,
        Authorization: `Bearer ${cameraBridge.token}`,
        ...(payload ? {
          'Content-Type': Buffer.isBuffer(body) ? 'application/octet-stream' : 'application/json',
          'Content-Length': String(payload.length),
        } : {}),
      },
    });
    request.setTimeout(timeoutMs, () => request.destroy(new Error('摄像机服务响应超时。')));
    request.once('error', () => reject(new Error('无法连接本地摄像机服务。')));
    request.once('response', (response) => {
      const chunks = [];
      let length = 0;
      const maximum = responseType === 'buffer' ? 8 * 1024 * 1024 : 1024 * 1024;
      response.on('data', (chunk) => {
        length += chunk.length;
        if (length > maximum) {
          response.destroy(new Error('摄像机服务响应超过大小限制。'));
          return;
        }
        chunks.push(chunk);
      });
      response.once('error', () => reject(new Error('读取摄像机服务响应失败。')));
      response.once('end', () => {
        const data = Buffer.concat(chunks);
        if (response.statusCode === 204) {
          resolve({ statusCode: 204, headers: response.headers, data: null });
          return;
        }
        if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
          let message = `摄像机服务请求失败（${response.statusCode ?? 'unknown'}）。`;
          try {
            const parsed = JSON.parse(data.toString('utf8'));
            const detail = typeof parsed.error === 'string' ? parsed.error : parsed.error?.message;
            if (typeof detail === 'string' && detail.length <= 240) message = detail;
          } catch {}
          reject(new Error(message));
          return;
        }
        if (responseType === 'buffer') {
          resolve({ statusCode: response.statusCode, headers: response.headers, data });
          return;
        }
        try {
          resolve({
            statusCode: response.statusCode,
            headers: response.headers,
            data: data.length ? JSON.parse(data.toString('utf8')) : {},
          });
        } catch {
          reject(new Error('摄像机服务返回了无效数据。'));
        }
      });
    });
    if (payload) request.write(payload);
    request.end();
  }));
}

export async function stopCameraBridge() {
  cameraBridge.shuttingDown = true;
  const child = cameraBridge.child;
  if (!child) return;
  try {
    await requestCameraBridge('/v1/shutdown', {
      method: 'POST',
      timeoutMs: 8_000,
      ensureStarted: false,
    });
  } catch {}
  await new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    child.once('exit', resolve);
    setTimeout(resolve, 10_000);
  });
  if (child.exitCode === null) child.kill();
  cameraBridge.child = null;
  cameraBridge.baseUrl = '';
  cameraBridge.state = 'stopped';
}

export function getCameraBridgeInfo() {
  return {
    state: cameraBridge.state,
    error: cameraBridge.error,
    baseUrl: cameraBridge.baseUrl,
  };
}