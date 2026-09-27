import {
  Camera,
  Circle,
  Focus,
  Hand,
  Home,
  Maximize,
  MousePointer2,
  Pause,
  Play,
  RotateCcw,
  Square,
  Video,
  WifiOff,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  cameraIds,
  type CameraBridgeStatus,
  type CameraFrame,
  type CameraId,
  type CameraRuntimeStatus,
} from '@/lib/camera-types';
import './dual-camera-workspace.css';
import SpatialScene from './spatial-scene';

type RunState = 'idle' | 'starting' | 'running' | 'paused';
type Toast = { id: number; message: string; tone: 'info' | 'success' | 'error' };

const emptyCameraStatus = (id: CameraId): CameraRuntimeStatus => ({
  id,
  label: id,
  state: 'starting',
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
});

const emptyBridgeStatus = (): CameraBridgeStatus => ({
  serviceState: 'starting',
  error: '',
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
    camera1: emptyCameraStatus('camera1'),
    camera2: emptyCameraStatus('camera2'),
  },
});

const delay = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

const cameraStateText = (status: CameraRuntimeStatus) => {
  if (status.state === 'streaming') return 'LIVE';
  if (status.state === 'reconnecting') return 'RECONNECTING';
  if (status.state === 'error') return 'ERROR';
  if (status.state === 'stopped') return 'STOPPED';
  return 'CONNECTING';
};

const inferenceLabel = (status: CameraBridgeStatus) => {
  const provider = status.inferenceProvider.trim();
  const precision = status.precision.trim();
  const processor = status.previewProcessor.trim();
  const engineState = status.engineState.trim().toLowerCase();
  const providerDescription = provider.toLowerCase();
  const processorDescription = processor.toLowerCase();
  const precisionLabel = precision && precision.toLowerCase() !== 'unreported'
    ? ` ${precision.toUpperCase()}`
    : '';

  if (providerDescription.includes('tensorrt')) {
    if (engineState && !['ready', 'running', 'unreported'].includes(engineState)) {
      return `TensorRT ${engineState.toUpperCase()}`;
    }
    return `TensorRT${precisionLabel} / CUDA`;
  }
  if (providerDescription.includes('cuda') || providerDescription.includes('gpu')) {
    return `CUDA${precisionLabel}`;
  }
  if (provider && providerDescription !== 'unreported') {
    return `CPU / ${provider}`;
  }
  if (processorDescription.includes('cuda') || processorDescription.includes('gpu')) {
    return 'GPU 预览 / 推理未上报';
  }
  return 'GPU 状态未上报';
};

function drawFrame(canvas: HTMLCanvasElement, bitmap: ImageBitmap) {
  const rect = canvas.getBoundingClientRect();
  const cssWidth = Math.max(1, Math.round(rect.width));
  const cssHeight = Math.max(1, Math.round(rect.height));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pixelWidth = Math.max(1, Math.round(cssWidth * dpr));
  const pixelHeight = Math.max(1, Math.round(cssHeight * dpr));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  const context = canvas.getContext('2d');
  if (!context) return;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.fillStyle = '#0f1722';
  context.fillRect(0, 0, cssWidth, cssHeight);
  const scale = Math.min(cssWidth / bitmap.width, cssHeight / bitmap.height);
  const width = bitmap.width * scale;
  const height = bitmap.height * scale;
  context.drawImage(bitmap, (cssWidth - width) / 2, (cssHeight - height) / 2, width, height);
}

function clearCanvas(canvas: HTMLCanvasElement | null) {
  if (!canvas) return;
  const context = canvas.getContext('2d');
  if (!context) return;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.fillStyle = '#0f1722';
  context.fillRect(0, 0, canvas.width, canvas.height);
}

export default function DualCameraWorkspace() {
  const [bridgeStatus, setBridgeStatus] = useState<CameraBridgeStatus>(emptyBridgeStatus);
  const [runState, setRunState] = useState<RunState>('idle');
  const [recordBusy, setRecordBusy] = useState(false);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [displayFps, setDisplayFps] = useState<Record<CameraId, number>>({
    camera1: 0,
    camera2: 0,
  });
  const [toasts, setToasts] = useState<Toast[]>([]);
  const canvasRefs = useRef<Record<CameraId, HTMLCanvasElement | null>>({
    camera1: null,
    camera2: null,
  });
  const latestSequenceRef = useRef<Record<CameraId, number>>({ camera1: 0, camera2: 0 });
  const latestBitmapRef = useRef<Record<CameraId, ImageBitmap | null>>({
    camera1: null,
    camera2: null,
  });
  const mountedRef = useRef(true);
  const displayedFramesRef = useRef<Record<CameraId, Array<{ sequence: number; at: number }>>>(
    { camera1: [], camera2: [] },
  );

  const toast = useCallback((message: string, tone: Toast['tone'] = 'info') => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current.slice(-2), { id, message, tone }]);
    window.setTimeout(
      () => setToasts((current) => current.filter((item) => item.id !== id)),
      4600,
    );
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const bridge = window.yuntongCameraBridge;
    if (!bridge) {
      setBridgeStatus((current) => ({
        ...current,
        serviceState: 'error',
        error: '摄像机桥接仅在桌面模式下可用。',
      }));
      return () => {
        mountedRef.current = false;
      };
    }

    let cancelled = false;
    const pollStatus = async () => {
      while (!cancelled) {
        try {
          const status = await bridge.getStatus();
          if (!cancelled) {
            setBridgeStatus(status);
            setRunState((current) => {
              if (current === 'starting' && status.detecting) return 'running';
              if (current === 'running' && !status.detecting) return 'paused';
              return current;
            });
          }
        } catch (error) {
          if (!cancelled) {
            setBridgeStatus((current) => ({
              ...current,
              serviceState: 'error',
              error: error instanceof Error ? error.message : '摄像机服务不可用。',
            }));
          }
        }
        await delay(900);
      }
    };
    void pollStatus();

    return () => {
      cancelled = true;
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const bridge = window.yuntongCameraBridge;
    if (!bridge) return undefined;
    let cancelled = false;
    const bitmaps = latestBitmapRef.current;
    const displayedFrames = displayedFramesRef.current;

    const decodeAndDraw = async (frame: CameraFrame) => {
      const bytes = new Uint8Array(frame.jpegBytes);
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }));
      if (cancelled) {
        bitmap.close();
        return;
      }
      const previous = bitmaps[frame.cameraId];
      bitmaps[frame.cameraId] = bitmap;
      previous?.close();
      const canvas = canvasRefs.current[frame.cameraId];
      if (canvas) drawFrame(canvas, bitmap);
      const frames = displayedFrames[frame.cameraId];
      const now = performance.now();
      if (!frames.length || frames[frames.length - 1].sequence !== frame.sequence) {
        frames.push({ sequence: frame.sequence, at: now });
      }
      while (frames.length && frames[0].at < now - 1_000) frames.shift();
    };

    const pollFrames = async (cameraId: CameraId) => {
      while (!cancelled) {
        try {
          const frame = await bridge.getFrame(cameraId, latestSequenceRef.current[cameraId]);
          if (frame) {
            latestSequenceRef.current[cameraId] = frame.sequence;
            await decodeAndDraw(frame);
          }
        } catch {
          // The status poll owns user-facing errors; frame polling retries without
          // producing a toast storm during normal RTSP reconnects.
          await delay(120);
        }
      }
    };

    const fpsTimer = window.setInterval(() => {
      const now = performance.now();
      const next = {} as Record<CameraId, number>;
      for (const cameraId of cameraIds) {
        const frames = displayedFrames[cameraId];
        while (frames.length && frames[0].at < now - 1_000) frames.shift();
        next[cameraId] = frames.length;
      }
      setDisplayFps(next);
    }, 250);

    void pollFrames('camera1');
    void pollFrames('camera2');
    return () => {
      cancelled = true;
      window.clearInterval(fpsTimer);
      for (const cameraId of cameraIds) {
        bitmaps[cameraId]?.close();
        bitmaps[cameraId] = null;
      }
    };
  }, []);

  useEffect(() => {
    const observers = cameraIds.map((cameraId) => {
      const canvas = canvasRefs.current[cameraId];
      if (!canvas) return null;
      const observer = new ResizeObserver(() => {
        const bitmap = latestBitmapRef.current[cameraId];
        if (bitmap) drawFrame(canvas, bitmap);
        else clearCanvas(canvas);
      });
      observer.observe(canvas);
      return observer;
    });
    return () => observers.forEach((observer) => observer?.disconnect());
  }, []);

  useEffect(() => () => {
    const bridge = window.yuntongCameraBridge;
    if (bridge && bridgeStatus.detecting) void bridge.stopDetection().catch(() => undefined);
  }, [bridgeStatus.detecting]);

  const toggleDetection = async () => {
    const bridge = window.yuntongCameraBridge;
    if (!bridge) return;
    if (runState === 'running') {
      try {
        const status = await bridge.stopDetection();
        setBridgeStatus(status);
        setRunState('paused');
        toast('两路人物检测已暂停，实时预览继续。', 'info');
      } catch (error) {
        toast(error instanceof Error ? error.message : '暂停检测失败。', 'error');
      }
      return;
    }
    try {
      setRunState('starting');
      const status = await bridge.startDetection();
      setBridgeStatus(status);
      setRunState('running');
      toast('两路摄像机人物检测与跟踪已启动。', 'success');
    } catch (error) {
      setRunState('idle');
      toast(error instanceof Error ? error.message : '启动检测失败。', 'error');
    }
  };

  const stopDetection = async () => {
    const bridge = window.yuntongCameraBridge;
    if (!bridge) return;
    try {
      const status = await bridge.stopDetection();
      setBridgeStatus(status);
      setRunState('idle');
      toast('人物检测已停止并清除跟踪框。', 'info');
    } catch (error) {
      toast(error instanceof Error ? error.message : '停止检测失败。', 'error');
    }
  };

  const toggleRecording = async () => {
    const bridge = window.yuntongCameraBridge;
    if (!bridge || recordBusy) return;
    setRecordBusy(true);
    try {
      const result = await bridge.toggleRecording();
      setBridgeStatus((current) => ({ ...current, recording: result.recording }));
      toast(
        result.recording
          ? '两路摄像机已请求开始带 person 检测框录像。'
          : '两路带框录像已停止并正在完成文件索引。',
        result.recording ? 'success' : 'info',
      );
    } catch (error) {
      toast(error instanceof Error ? error.message : '录像操作失败。', 'error');
    } finally {
      if (mountedRef.current) setRecordBusy(false);
    }
  };

  const captureBoth = async () => {
    const bridge = window.yuntongCameraBridge;
    if (!bridge || snapshotBusy) return;
    setSnapshotBusy(true);
    try {
      const result = await bridge.saveSnapshots();
      const saved = result.results.filter((item) => item.saved);
      const failed = result.results.filter((item) => !item.saved);
      if (saved.length === 2) {
        toast('camera1 与 camera2 检测结果抓拍已保存到各自原目录。', 'success');
      } else if (saved.length) {
        toast(`已保存 ${saved[0].cameraId}；另一台抓拍失败。`, 'error');
      } else {
        toast(failed[0]?.error ?? '两路抓拍均失败。', 'error');
      }
    } catch (error) {
      toast(error instanceof Error ? error.message : '双路抓拍失败。', 'error');
    } finally {
      if (mountedRef.current) setSnapshotBusy(false);
    }
  };

  const toggleFullscreen = async () => {
    const element = document.querySelector('.dc-shell') as HTMLElement | null;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await element?.requestFullscreen();
    } catch {
      toast('当前环境不允许进入全屏。', 'error');
    }
  };

  const bothFramesReady = useMemo(
    () => cameraIds.every((cameraId) => bridgeStatus.cameras[cameraId].frameAvailable),
    [bridgeStatus.cameras],
  );
  const serviceReady = bridgeStatus.serviceState === 'ready';

  return (
    <main className="dc-page">
      <section className="dc-shell">
        <header className="dc-header">
          <div className="dc-brand" aria-label="云瞳">云瞳</div>
          <div className="dc-header-blank" aria-hidden="true" />
          <div className="dc-header-actions">
            <button
              type="button"
              className={`dc-record ${bridgeStatus.recording ? 'active' : ''}`}
              onClick={toggleRecording}
              disabled={!serviceReady || recordBusy}
              aria-pressed={bridgeStatus.recording}
            >
              <Circle aria-hidden="true" />
              {bridgeStatus.recording ? 'Stop Recording' : 'Record'}
            </button>
            <button
              type="button"
              onClick={captureBoth}
              disabled={!serviceReady || !bothFramesReady || snapshotBusy}
            >
              <Camera aria-hidden="true" />
              {snapshotBusy ? 'Saving…' : 'Screenshot'}
            </button>
            <button type="button" onClick={toggleFullscreen}>
              <Maximize aria-hidden="true" /> Fullscreen
            </button>
          </div>
        </header>

        <aside className="dc-toolbar" aria-label="暂未启用的画布工具">
          <button type="button" disabled title="Home 暂未启用"><Home /></button>
          <button type="button" disabled title="Pan / Zoom 暂未启用"><Hand /></button>
          <button type="button" disabled title="Normal Pointer 暂未启用"><MousePointer2 /></button>
        </aside>

        <section className="dc-stage" aria-label="实时三维空间建模视图">
          <SpatialScene
            objects={{
              camera1: bridgeStatus.cameras.camera1.objects,
              camera2: bridgeStatus.cameras.camera2.objects,
            }}
            detecting={bridgeStatus.detecting}
            matchingDiagnostics={bridgeStatus.matchingDiagnostics}
          />
          <div className="dc-stage-controls">
            <button type="button" disabled aria-label="缩小暂未启用"><ZoomOut /></button>
            <span>100%</span>
            <button type="button" disabled aria-label="放大暂未启用"><ZoomIn /></button>
            <button type="button" disabled title="Fit 暂未启用"><Focus /> Fit</button>
            <button type="button" disabled title="Reset 暂未启用"><RotateCcw /></button>
            <button
              type="button"
              className="dc-start"
              onClick={toggleDetection}
              disabled={!serviceReady || runState === 'starting'}
            >
              {runState === 'running' ? <Pause /> : <Play />}
              {runState === 'starting'
                ? 'Starting…'
                : runState === 'running'
                  ? 'Pause'
                  : 'Start'}
            </button>
            {(runState === 'running' || runState === 'paused') && (
              <button type="button" className="dc-stop" onClick={stopDetection}>
                <Square /> Stop
              </button>
            )}
          </div>
        </section>

        <aside className="dc-cameras" aria-label="双摄像机实时画面">
          {cameraIds.map((cameraId) => {
            const status = bridgeStatus.cameras[cameraId];
            const stateText = cameraStateText(status);
            const measuredFps = displayFps[cameraId];
            return (
              <section className="dc-camera-panel" key={cameraId}>
                <header>
                  <div><Video /><strong>{cameraId}</strong></div>
                  <div className="dc-camera-meta">
                    <span>{measuredFps} FPS</span>
                    <span title={`推理：${bridgeStatus.inferenceProvider}；预览：${bridgeStatus.previewProcessor}`}>
                      {inferenceLabel(bridgeStatus)}
                    </span>
                    {bridgeStatus.detecting && status.detectionCount > 0 && (
                      <span>{status.detectionCount} person</span>
                    )}
                    <span className={`dc-live-state ${status.state}`}>{stateText}</span>
                  </div>
                </header>
                <div className="dc-camera-viewport">
                  <canvas
                    ref={(element) => { canvasRefs.current[cameraId] = element; }}
                    aria-label={`${cameraId} 实时检测画面`}
                  />
                  {!status.frameAvailable && (
                    <div className="dc-camera-placeholder">
                      {status.state === 'error' || bridgeStatus.serviceState === 'error'
                        ? <WifiOff />
                        : <Video />}
                      <strong>{bridgeStatus.serviceState === 'error' ? '摄像机服务不可用' : '正在连接摄像机'}</strong>
                      <span>{status.error || bridgeStatus.error || '等待实时画面…'}</span>
                    </div>
                  )}
                  {bridgeStatus.recording && (
                    <div className="dc-recording-indicator"><i /> REC</div>
                  )}
                </div>
              </section>
            );
          })}
        </aside>

        <div className="dc-toasts" aria-live="polite">
          {toasts.map((item) => (
            <div key={item.id} className={`dc-toast ${item.tone}`}>{item.message}</div>
          ))}
        </div>
      </section>
    </main>
  );
}
