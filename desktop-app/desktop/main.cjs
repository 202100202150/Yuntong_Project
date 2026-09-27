const { app, BrowserWindow, ipcMain, net, protocol, shell } = require('electron');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const readline = require('node:readline');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');

const softwareRootPath = path.join(__dirname, '..');
const rendererRootPath = path.join(__dirname, '..', 'desktop-dist');
const rendererEntryUrl = 'app://bundle/index.html';
const allowedRendererExtensions = new Set([
  '.html', '.js', '.css', '.onnx', '.wasm', '.mjs', '.png', '.jpg', '.jpeg',
  '.webp', '.svg', '.ico', '.woff', '.woff2', '.json',
]);
const cameraIds = new Set(['camera1', 'camera2']);
const MAX_PREVIEW_BYTES = 8 * 1024 * 1024;
const MAX_JSON_BYTES = 1024 * 1024;

const cameraBridge = {
  state: 'stopped',
  error: '',
  child: null,
  baseUrl: '',
  token: '',
  startPromise: null,
};
let shutdownComplete = false;
let shutdownPromise = null;

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'app',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
    },
  },
]);

function resolveRendererAsset(requestUrl) {
  let url;
  try {
    url = new URL(requestUrl);
  } catch {
    return null;
  }
  if (url.protocol !== 'app:' || url.host !== 'bundle') return null;
  let pathname;
  try {
    pathname = decodeURIComponent(url.pathname || '/index.html');
  } catch {
    return null;
  }
  if (pathname === '/') pathname = '/index.html';
  if (pathname.includes('\0') || pathname.includes('\\')) return null;
  const relativePath = pathname.replace(/^\/+/, '');
  const candidate = path.resolve(rendererRootPath, relativePath);
  const relative = path.relative(rendererRootPath, candidate);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) return null;
  if (!allowedRendererExtensions.has(path.extname(candidate).toLowerCase())) return null;
  return candidate;
}

function registerRendererProtocol() {
  protocol.handle('app', (request) => {
    if (request.method !== 'GET') return new Response('Method not allowed', { status: 405 });
    const assetPath = resolveRendererAsset(request.url);
    if (!assetPath) return new Response('Not found', { status: 404 });
    return net.fetch(pathToFileURL(assetPath).href);
  });
}

function isTrustedRenderer(event) {
  const frame = event.senderFrame;
  return Boolean(frame && frame === event.sender.mainFrame && frame.url === rendererEntryUrl);
}

function requireTrustedRenderer(event) {
  if (!isTrustedRenderer(event)) throw new Error('拒绝非本地页面的摄像机控制请求。');
}

function resolveCameraProject() {
  const configured = process.env.YUNTONG_CAMERA_PROJECT_DIR?.trim();
  // Public source layout:
  // <repository>/desktop-app
  // <repository>/camera-rtsp-low-latency
  // Keep the environment override for custom deployments and packaged setups.
  const monorepoProjectPath = path.resolve(softwareRootPath, '..', 'camera-rtsp-low-latency');
  const legacyProjectPath = path.join(
    app.getPath('documents'),
    'ChatGPT',
    'yuntong',
    'camera-rtsp-low-latency',
  );
  const projectPath = configured
    ? path.resolve(configured)
    : fs.existsSync(path.join(monorepoProjectPath, 'camera_bridge.py'))
      ? monorepoProjectPath
      : legacyProjectPath;
  const configuredPython = process.env.YUNTONG_CAMERA_PYTHON?.trim();
  const pythonPath = configuredPython
    ? path.resolve(configuredPython)
    : path.join(softwareRootPath, 'work', 'gpu-runtime', '.venv', 'Scripts', 'python.exe');
  const scriptPath = path.join(projectPath, 'camera_bridge.py');
  // The Python sidecar cannot open a file inside Electron's asar archive.
  // electron-builder unpacks the model below; use that real filesystem path
  // for packaged builds while keeping the normal desktop-dist path in dev.
  const bundledModelPath = path.join(rendererRootPath, 'models', 'person-v1.onnx');
  const unpackedModelPath = path.join(
    process.resourcesPath,
    'app.asar.unpacked',
    'desktop-dist',
    'models',
    'person-v1.onnx',
  );
  const modelPath = app.isPackaged ? unpackedModelPath : bundledModelPath;
  const configuredEngineCache = process.env.YUNTONG_TENSORRT_CACHE?.trim();
  const engineCachePath = configuredEngineCache
    ? path.resolve(configuredEngineCache)
    : app.isPackaged
      ? path.join(app.getPath('userData'), 'tensorrt')
      : path.join(softwareRootPath, 'work', 'tensorrt');
  for (const [label, candidate] of [
    ['CUDA/TensorRT Python 环境', pythonPath],
    ['摄像机桥接程序', scriptPath],
    ['person ONNX 模型', modelPath],
  ]) {
    if (!fs.existsSync(candidate)) throw new Error(`${label}不存在，请检查本机部署。`);
  }
  return { projectPath, pythonPath, scriptPath, modelPath, engineCachePath };
}

function startCameraBridge() {
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
          // The camera source is currently 25 FPS. 768x432 keeps the 16:9
          // preview sharp at the right-hand panel's CSS size while reducing
          // CUDA download, JPEG encode, IPC payload and renderer decode cost.
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

      const fail = (message) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        cameraBridge.state = 'error';
        cameraBridge.error = message;
        cameraBridge.startPromise = null;
        reject(new Error(message));
      };

      const lines = readline.createInterface({ input: child.stdout });
      lines.on('line', (line) => {
        if (settled || !line.trim().startsWith('{')) return;
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          return;
        }
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

      // Do not forward sidecar stderr: RTSP libraries may include local network
      // details. The renderer receives only a bounded, generic process state.
      child.stderr.resume();
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
        if (!shutdownComplete) {
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

function requestCameraBridge(
  pathname,
  {
    method = 'GET',
    body = null,
    responseType = 'json',
    timeoutMs = 6_000,
    ensureStarted = true,
    requestHeaders = null,
  } = {},
) {
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
      const maximum = responseType === 'buffer' ? MAX_PREVIEW_BYTES : MAX_JSON_BYTES;
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
          let bridgeCode = '';
          try {
            const parsed = JSON.parse(data.toString('utf8'));
            const detail = typeof parsed.error === 'string' ? parsed.error : parsed.error?.message;
            bridgeCode = typeof parsed.error?.code === 'string' ? parsed.error.code : '';
            if (typeof detail === 'string' && detail.length <= 240) message = detail;
          } catch {
            // Keep the generic message; never relay arbitrary sidecar output.
          }
          const error = new Error(message);
          error.statusCode = response.statusCode;
          error.bridgeCode = bridgeCode;
          reject(error);
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

function numberHeader(headers, name, fallback = 0) {
  const raw = headers[name];
  const value = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isFinite(value) ? value : fallback;
}

function emptyCameraStatus(id, state = 'starting') {
  return {
    id,
    label: id,
    state,
    error: '',
    width: 0,
    height: 0,
    decodeFps: 0,
    framesDecoded: 0,
    reconnects: 0,
    sequence: 0,
    frameAvailable: false,
    recordingState: 'idle',
    detectionCount: 0,
    detectionAgeMs: null,
    previewFps: 0,
    detectionFps: 0,
    latency: null,
    objects: [],
  };
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

function nullableFiniteNumber(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function nullableSignedFiniteNumber(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function cameraPersonId(value) {
  return typeof value === 'string' && /^person-\d{1,12}$/.test(value) ? value : null;
}

function boundedStatusString(value, fallback = 'unreported') {
  return typeof value === 'string' && value.trim()
    ? value.trim().slice(0, 80)
    : fallback;
}

function normalizeStatus(payload) {
  const rootDetection = payload?.detection && typeof payload.detection === 'object'
    ? payload.detection
    : {};
  const inference = payload?.inference && typeof payload.inference === 'object'
    ? payload.inference
    : rootDetection.inference && typeof rootDetection.inference === 'object'
      ? rootDetection.inference
      : rootDetection;
  const preview = payload?.preview && typeof payload.preview === 'object'
    ? payload.preview
    : {};
  const rawMatching = payload?.matchingDiagnostics && typeof payload.matchingDiagnostics === 'object'
    ? payload.matchingDiagnostics
    : {};
  const matchingReasons = new Set([
    'waiting_for_frames', 'capture_time_gap', 'no_people',
    'low_similarity', 'ambiguous', 'confirming', 'partial_match', 'matched',
  ]);
  const matchingReason = boundedStatusString(rawMatching.reason, 'waiting_for_frames');
  const matchingVersion = rawMatching.algorithmVersion;
  const rawReasonCounts = rawMatching.reasonCounts && typeof rawMatching.reasonCounts === 'object'
    && !Array.isArray(rawMatching.reasonCounts) ? rawMatching.reasonCounts : {};
  const reasonCounts = Object.fromEntries([...matchingReasons].map((reason) => [
    reason, Math.floor(finiteNumber(rawReasonCounts[reason])),
  ]));
  const recentFailures = Array.isArray(rawMatching.recentFailures)
    ? rawMatching.recentFailures.slice(0, 5).flatMap((item) => {
      if (!item || typeof item !== 'object' || !matchingReasons.has(item.reason)) return [];
      return [{
        reason: item.reason,
        bestSimilarity: nullableFiniteNumber(item.bestSimilarity),
        secondSimilarity: nullableFiniteNumber(item.secondSimilarity),
        camera1Id: cameraPersonId(item.camera1Id),
        camera2Id: cameraPersonId(item.camera2Id),
        frameSkewMs: nullableFiniteNumber(item.frameSkewMs),
        ageMs: Math.floor(finiteNumber(item.ageMs)),
      }];
    }) : [];
  const matchingDiagnostics = {
    algorithmVersion: matchingVersion === 'temporal-window-v2' || matchingVersion === 'temporal-geometry-v3'
      ? matchingVersion : 'unreported',
    reason: matchingReasons.has(matchingReason) ? matchingReason : 'waiting_for_frames',
    bestSimilarity: nullableFiniteNumber(rawMatching.bestSimilarity),
    secondSimilarity: nullableFiniteNumber(rawMatching.secondSimilarity),
    camera1Id: cameraPersonId(rawMatching.camera1Id),
    camera2Id: cameraPersonId(rawMatching.camera2Id),
    comparisonCount: Math.floor(finiteNumber(rawMatching.comparisonCount)),
    frameSkewMs: nullableFiniteNumber(rawMatching.frameSkewMs),
    offsetMs: nullableSignedFiniteNumber(rawMatching.offsetMs),
    pendingPairs: finiteNumber(rawMatching.pendingPairs),
    confirmedPairs: finiteNumber(rawMatching.confirmedPairs),
    geometryStatus: rawMatching.geometryStatus === 'ready' ? 'ready' : 'unavailable',
    geometryInliers: Math.floor(finiteNumber(rawMatching.geometryInliers)),
    reasonCounts,
    recentFailures,
  };
  const sourceCameras = payload?.cameras && typeof payload.cameras === 'object'
    ? payload.cameras
    : {};
  const cameras = {};
  for (const id of cameraIds) {
    const source = sourceCameras[id] ?? {};
    const stream = source.stream ?? source.status ?? source;
    const recording = source.recording ?? {};
    const detection = source.detection ?? {};
    const state = typeof stream.state === 'string' ? stream.state : 'starting';
    cameras[id] = {
      id,
      label: typeof source.label === 'string' ? source.label : id,
      state: ['starting', 'connecting', 'streaming', 'reconnecting', 'stopped', 'error'].includes(state)
        ? state
        : 'error',
      error: typeof stream.error === 'string' ? stream.error.slice(0, 240) : '',
      width: finiteNumber(stream.width),
      height: finiteNumber(stream.height),
      decodeFps: finiteNumber(stream.decode_fps ?? stream.decodeFps),
      framesDecoded: finiteNumber(stream.frames_decoded ?? stream.framesDecoded),
      reconnects: finiteNumber(stream.reconnects),
      sequence: finiteNumber(source.frame?.sequence ?? source.sequence ?? stream.sequence),
      frameAvailable: Boolean(
        source.frame?.available ?? source.frame_available ?? source.frameAvailable ?? source.sequence,
      ),
      recordingState: String(recording.state ?? source.recording_state ?? 'idle'),
      detectionCount: finiteNumber(
        detection.count ?? source.objectCount ?? source.objects?.length ?? source.detection_count,
      ),
      // The Python bridge currently emits camelCase, while older bridge
      // builds used snake_case.  Accept both spellings so stale clients do
      // not silently lose the freshness metric.
      detectionAgeMs: nullableFiniteNumber(
        detection.ageMs
          ?? detection.age_ms
          ?? source.detectionAgeMs
          ?? source.detection_age_ms,
      ),
      previewFps: finiteNumber(
        source.previewFps ?? source.preview_fps ?? source.preview?.fps ?? stream.preview_fps,
      ),
      detectionFps: finiteNumber(
        source.detectionFps ?? source.detection_fps ?? detection.fps,
      ),
      latency: nullableFiniteNumber(
        source.inferenceLatencyMs
          ?? source.inference_latency_ms
          ?? source.latency
          ?? detection.latencyMs
          ?? detection.latency_ms
          ?? detection.latency,
      ),
      objects: Array.isArray(source.objects) ? source.objects.flatMap((item) => {
        const bbox = item?.bbox;
        const confidence = Number(item?.confidence);
        if (!item || typeof item.id !== 'string' || item.class !== 'person' || !bbox) return [];
        const values = [bbox.x, bbox.y, bbox.width, bbox.height].map(Number);
        if (!values.every(Number.isFinite) || values[2] <= 0 || values[3] <= 0) return [];
        return [{
          id: item.id.slice(0, 80),
          ...(typeof item.globalId === 'string' && item.globalId.trim()
            ? { globalId: item.globalId.slice(0, 160) }
            : {}),
          class: 'person',
          confidence: Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : 0,
          bbox: { x: values[0], y: values[1], width: values[2], height: values[3] },
          frameSequence: finiteNumber(item.frameSequence ?? item.frame_sequence),
        }];
      }) : [],
    };
  }
  const recording = typeof payload?.recording === 'boolean'
    ? payload.recording
    : Object.values(cameras).some((camera) =>
      ['armed', 'starting', 'recording', 'stopping'].includes(camera.recordingState));
  const detectionModelState = boundedStatusString(
    rootDetection.modelState
      ?? rootDetection.model_state
      ?? payload?.modelState
      ?? payload?.model_state,
  ).toLowerCase();
  const normalizedEngineState = boundedStatusString(
    inference.engineState
      ?? inference.engine_state
      ?? payload?.engineState
      ?? payload?.engine_state,
  ).toLowerCase();
  const detectionError = boundedStatusString(
    rootDetection.error
      ?? inference.error
      ?? payload?.error,
    '',
  );
  const inferenceFailed = ['error', 'failed', 'failure', 'closed'].includes(normalizedEngineState)
    || ['error', 'failed', 'failure'].includes(detectionModelState);

  return {
    // The bridge can still be reachable while TensorRT has failed to load or
    // infer.  Surface that as an error instead of claiming the workbench is
    // ready; otherwise Start would appear successful and the UI would keep
    // showing stale/empty detection data.
    serviceState: inferenceFailed ? 'error' : 'ready',
    error: inferenceFailed
      ? (detectionError || `TensorRT 推理状态异常（${normalizedEngineState || detectionModelState}）。`)
      : '',
    recording,
    detecting: Boolean(
      payload?.detection?.running ?? payload?.detecting ?? payload?.detection_enabled,
    ),
    inferenceProvider: boundedStatusString(
      inference.provider
        ?? inference.inferenceProvider
        ?? inference.backend
        ?? payload?.inferenceProvider,
    ),
    precision: boundedStatusString(inference.precision ?? payload?.precision),
    previewProcessor: boundedStatusString(
      preview.processor
        ?? rootDetection.previewProcessor
        ?? payload?.previewProcessor,
    ),
    engineState: boundedStatusString(
      inference.engineState
        ?? inference.engine_state
        ?? payload?.engineState,
    ),
    matchingDiagnostics,
    cameras,
  };
}

async function getNormalizedStatus() {
  if (cameraBridge.state !== 'ready') {
    try {
      await startCameraBridge();
    } catch {
      return {
        serviceState: 'error',
        error: cameraBridge.error || '摄像机服务不可用。',
        recording: false,
        detecting: false,
        inferenceProvider: 'unreported',
        precision: 'unreported',
        previewProcessor: 'unreported',
        engineState: 'unreported',
        matchingDiagnostics: {
          algorithmVersion: 'unreported',
          reason: 'waiting_for_frames',
          bestSimilarity: null,
          secondSimilarity: null,
          camera1Id: null,
          camera2Id: null,
          comparisonCount: 0,
          frameSkewMs: null,
          offsetMs: null,
          pendingPairs: 0,
          confirmedPairs: 0,
          geometryStatus: 'unavailable',
          geometryInliers: 0,
          reasonCounts: {},
          recentFailures: [],
        },
        cameras: {
          camera1: emptyCameraStatus('camera1', 'error'),
          camera2: emptyCameraStatus('camera2', 'error'),
        },
      };
    }
  }
  const response = await requestCameraBridge('/v1/status');
  return normalizeStatus(response.data);
}

ipcMain.handle('camera:get-status', async (event) => {
  requireTrustedRenderer(event);
  return getNormalizedStatus();
});

ipcMain.handle('camera:get-frame', async (event, input) => {
  requireTrustedRenderer(event);
  const cameraId = input?.cameraId;
  const afterSequence = Number(input?.afterSequence ?? 0);
  if (!cameraIds.has(cameraId) || !Number.isSafeInteger(afterSequence) || afterSequence < 0)
    throw new Error('摄像机帧请求参数无效。');
  let response;
  try {
    response = await requestCameraBridge(
      `/v1/cameras/${cameraId}/frame.jpg`,
      {
        responseType: 'buffer',
        timeoutMs: 2_000,
        requestHeaders: {
          'X-After-Frame-Sequence': String(afterSequence),
          'X-Wait-Milliseconds': '750',
        },
      },
    );
  } catch (error) {
    if (error?.bridgeCode === 'frame_unavailable') return null;
    throw error;
  }
  if (response.statusCode === 204 || !response.data) return null;
  const sequence = numberHeader(response.headers, 'x-frame-sequence', afterSequence + 1);
  if (sequence <= afterSequence) return null;
  return {
    cameraId,
    sequence,
    width: numberHeader(response.headers, 'x-frame-width'),
    height: numberHeader(response.headers, 'x-frame-height'),
    receivedAt: numberHeader(response.headers, 'x-frame-received-at', Date.now()),
    jpegBytes: new Uint8Array(response.data),
  };
});

ipcMain.handle('camera:start-detection', async (event) => {
  requireTrustedRenderer(event);
  await requestCameraBridge('/v1/detection/start', { method: 'POST' });
  return getNormalizedStatus();
});

ipcMain.handle('camera:stop-detection', async (event) => {
  requireTrustedRenderer(event);
  await requestCameraBridge('/v1/detection/stop', { method: 'POST' });
  return getNormalizedStatus();
});

ipcMain.handle('camera:toggle-recording', async (event) => {
  requireTrustedRenderer(event);
  const response = await requestCameraBridge('/v1/recording/toggle', {
    method: 'POST',
  });
  const status = await getNormalizedStatus();
  return {
    recording: Boolean(response.data?.recording ?? status.recording),
    cameras: {
      camera1: { state: status.cameras.camera1.recordingState },
      camera2: { state: status.cameras.camera2.recordingState },
    },
  };
});

ipcMain.handle('camera:save-snapshots', async (event) => {
  requireTrustedRenderer(event);
  const response = await requestCameraBridge('/v1/snapshots', { method: 'POST' });
  const rawResults = Array.isArray(response.data?.results) ? response.data.results : [];
  return {
    results: ['camera1', 'camera2'].map((cameraId) => {
      const item = rawResults.find((candidate) => candidate?.camera_id === cameraId || candidate?.cameraId === cameraId)
        ?? response.data?.snapshots?.[cameraId]
        ?? response.data?.[cameraId]
        ?? {};
      return {
        cameraId,
        saved: Boolean(item.saved ?? item.queued ?? item.path),
        ...(typeof item.error === 'string' ? { error: item.error.slice(0, 240) } : {}),
      };
    }),
  };
});

async function stopCameraBridge() {
  if (shutdownPromise) return shutdownPromise;
  shutdownPromise = (async () => {
    const child = cameraBridge.child;
    if (!child) return;
    try {
      await requestCameraBridge('/v1/shutdown', {
        method: 'POST',
        timeoutMs: 8_000,
        ensureStarted: false,
      });
    } catch {
      // The process may close its socket while finalizing recording files.
    }
    if (child.exitCode === null) {
      await Promise.race([
        new Promise((resolve) => child.once('exit', resolve)),
        new Promise((resolve) => setTimeout(resolve, 10_000)),
      ]);
    }
    if (child.exitCode === null) child.kill();
    cameraBridge.child = null;
    cameraBridge.baseUrl = '';
    cameraBridge.state = 'stopped';
  })();
  return shutdownPromise;
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#e8edf2',
    icon: path.join(__dirname, 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  window.removeMenu();
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (url !== rendererEntryUrl) event.preventDefault();
  });
  window.webContents.on('will-redirect', (event, url) => {
    if (url !== rendererEntryUrl) event.preventDefault();
  });
  window.once('ready-to-show', () => window.show());
  void window.loadURL(rendererEntryUrl);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  void app.whenReady().then(() => {
    registerRendererProtocol();
    createWindow();
    void startCameraBridge().catch(() => undefined);
  });
  app.on('second-instance', () => {
    const window = BrowserWindow.getAllWindows()[0];
    if (window) {
      if (window.isMinimized()) window.restore();
      window.focus();
    }
  });
  app.on('before-quit', (event) => {
    if (shutdownComplete || !cameraBridge.child) return;
    event.preventDefault();
    void stopCameraBridge().finally(() => {
      shutdownComplete = true;
      app.quit();
    });
  });
  app.on('window-all-closed', () => app.quit());
}
