import {
  Camera,
  Clipboard,
  Eye,
  EyeOff,
  FileImage,
  Film,
  Focus,
  Hand,
  Home,
  Maximize,
  MousePointer2,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Search,
  Square,
  Trash2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react';
import type { Detector } from '@/lib/detector';
import { createOnnxPersonDetector } from '@/lib/onnx-person-detector';
import {
  createMediaItem,
  releaseMediaItem,
  selectMediaAfterRemoval,
  type MediaItem,
} from '@/lib/media-store';
import type { DetectedObject } from '@/lib/annotation-types';
import {
  describeAbsolutePath,
  saveScreenshot,
} from '@/lib/file-access-adapter';
import './annotation-workspace.css';

type ToolMode = 'pointer' | 'pan';
type RunState = 'idle' | 'running' | 'paused';
type ModelStatus = 'unloaded' | 'loading' | 'ready' | 'error';
type Toast = { id: number; message: string; tone: 'info' | 'success' | 'error' };
type Transform = { scale: number; x: number; y: number };

const ACCEPTED_EXTENSIONS = new Set([
  'jpg',
  'jpeg',
  'png',
  'webp',
  'avif',
  'gif',
  'bmp',
  'heic',
  'heif',
  'mp4',
  'webm',
  'mov',
  'm4v',
  'ogv',
  'avi',
  'mkv',
  'mpeg',
  'mpg',
]);

const timestamp = () =>
  new Intl.DateTimeFormat('sv-SE', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
    .format(new Date())
    .replaceAll('-', '')
    .replaceAll(':', '')
    .replace(' ', '_');

// Use a high-contrast blue for the single person class.
const objectColor = (_className: DetectedObject['class']) => '#087fdb';

function mediaKey(file: File) {
  return `${file.name.toLowerCase()}:${file.size}:${file.lastModified}`;
}

function isSupportedFile(file: File) {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  return (
    ACCEPTED_EXTENSIONS.has(extension) &&
    (!file.type || file.type.startsWith('image/') || file.type.startsWith('video/'))
  );
}

function baseName(name: string) {
  return name.replace(/\.[^.]+$/, '') || 'media';
}

export default function AnnotationWorkspace() {
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tool, setTool] = useState<ToolMode>('pointer');
  const [transform, setTransform] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  const [runState, setRunState] = useState<RunState>('idle');
  const [modelStatus, setModelStatus] = useState<ModelStatus>('unloaded');
  const [query, setQuery] = useState('');
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef({ x: 0, y: 0, originX: 0, originY: 0 });
  const frameCallbackRef = useRef<number | null>(null);
  const fallbackFrameRef = useRef<number | null>(null);
  const lastDetectionMsRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRef = useRef<MediaItem[]>([]);
  const activeIdRef = useRef<string | null>(null);
  const detectorRef = useRef<Detector | null>(null);
  const detectorPromiseRef = useRef<Promise<Detector> | null>(null);
  const detectionAbortRef = useRef<AbortController | null>(null);
  const detectionGenerationRef = useRef(0);
  const inferenceInFlightRef = useRef(false);
  const mountedRef = useRef(true);
  const drawCanvasRef = useRef<() => void>(() => undefined);
  const detectCurrentRef = useRef<(timeSeconds?: number) => Promise<boolean>>(
    async () => false,
  );

  const activeMedia = useMemo(
    () => media.find((item) => item.id === activeId) ?? null,
    [activeId, media],
  );
  mediaRef.current = media;
  activeIdRef.current = activeId;
  const visibleObjects = useMemo(() => activeMedia?.objects ?? [], [activeMedia]);
  const filteredObjects = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return visibleObjects;
    return visibleObjects.filter((item) => item.class.includes(term));
  }, [query, visibleObjects]);

  const toast = useCallback((message: string, tone: Toast['tone'] = 'info') => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current.slice(-2), { id, message, tone }]);
    window.setTimeout(
      () => setToasts((current) => current.filter((item) => item.id !== id)),
      4200,
    );
  }, []);

  const ensureDetector = useCallback(async (): Promise<Detector> => {
    if (detectorRef.current) return detectorRef.current;
    if (detectorPromiseRef.current) return detectorPromiseRef.current;
    setModelStatus('loading');
    const promise = (async () => {
      const detector = createOnnxPersonDetector();
      try {
        await detector.initialize();
        if (!mountedRef.current) {
          await detector.dispose();
          throw new DOMException('Detector initialization was cancelled', 'AbortError');
        }
        detectorRef.current = detector;
        setModelStatus('ready');
        return detector;
      } catch (error) {
        if (detectorRef.current !== detector) await detector.dispose();
        throw error;
      }
    })();
    detectorPromiseRef.current = promise;
    try {
      return await promise;
    } catch (error) {
      if (
        mountedRef.current &&
        !(error instanceof DOMException && error.name === 'AbortError')
      ) setModelStatus('error');
      throw error;
    } finally {
      detectorPromiseRef.current = null;
    }
  }, []);

  const patchActive = useCallback(
    (patch: Partial<MediaItem> | ((item: MediaItem) => Partial<MediaItem>)) => {
      if (!activeId) return;
      setMedia((current) =>
        current.map((item) =>
          item.id === activeId
            ? { ...item, ...(typeof patch === 'function' ? patch(item) : patch) }
            : item,
        ),
      );
    },
    [activeId],
  );

  const patchMediaItem = useCallback((id: string, patch: Partial<MediaItem>) => {
    setMedia((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  }, []);

  const setObjects = useCallback((mediaId: string, objects: DetectedObject[], frameTime = 0) => {
    setMedia((current) =>
      current.map((item) => {
        if (item.id !== mediaId) return item;
        const visibility = new Map(item.objects.map((object) => [object.id, object.visible]));
        const next = objects.map((object) => ({
          ...object,
          visible: visibility.get(object.id) ?? true,
        }));
        return {
          ...item,
          objects: next,
          detected: true,
          status: 'detected',
          currentFrame: frameTime,
          peakObjectCount: Math.max(item.peakObjectCount ?? 0, next.length),
        };
      }),
    );
  }, []);

  const stopDetectionLoop = useCallback(() => {
    detectionGenerationRef.current += 1;
    detectionAbortRef.current?.abort();
    detectionAbortRef.current = null;
    const video = videoRef.current as
      | (HTMLVideoElement & { cancelVideoFrameCallback?: (id: number) => void })
      | null;
    if (frameCallbackRef.current !== null && video?.cancelVideoFrameCallback) {
      video.cancelVideoFrameCallback(frameCallbackRef.current);
    }
    if (fallbackFrameRef.current !== null) cancelAnimationFrame(fallbackFrameRef.current);
    frameCallbackRef.current = null;
    fallbackFrameRef.current = null;
  }, []);

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    if (!canvas || !stage) return;
    const rect = stage.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    }
    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    context.fillStyle = '#f3f5f8';
    context.fillRect(0, 0, width, height);
    if (!activeMedia) return;

    const source = activeMedia.kind === 'video' ? videoRef.current : imageRef.current;
    if (!source) return;
    const sourceWidth =
      activeMedia.kind === 'video'
        ? (source as HTMLVideoElement).videoWidth
        : (source as HTMLImageElement).naturalWidth;
    const sourceHeight =
      activeMedia.kind === 'video'
        ? (source as HTMLVideoElement).videoHeight
        : (source as HTMLImageElement).naturalHeight;
    if (!sourceWidth || !sourceHeight) return;

    const fitScale = Math.min((width - 48) / sourceWidth, (height - 48) / sourceHeight);
    const renderScale = fitScale * transform.scale;
    const drawWidth = sourceWidth * renderScale;
    const drawHeight = sourceHeight * renderScale;
    const originX = (width - drawWidth) / 2 + transform.x;
    const originY = (height - drawHeight) / 2 + transform.y;
    context.save();
    context.shadowColor = 'rgba(25, 45, 72, .14)';
    context.shadowBlur = 24;
    context.shadowOffsetY = 8;
    context.drawImage(source, originX, originY, drawWidth, drawHeight);
    context.restore();

    for (const object of activeMedia.objects) {
      if (!object.visible) continue;
      const x = originX + object.bbox.x * drawWidth;
      const y = originY + object.bbox.y * drawHeight;
      const boxWidth = object.bbox.width * drawWidth;
      const boxHeight = object.bbox.height * drawHeight;
      const color = objectColor(object.class);
      const selected = object.id === selectedObjectId;
      context.lineWidth = selected ? 3 : 2;
      context.strokeStyle = color;
      context.strokeRect(x, y, boxWidth, boxHeight);
      const label = `${object.class} ${(object.confidence * 100).toFixed(0)}%`;
      context.font = '600 12px system-ui, sans-serif';
      const labelWidth = Math.ceil(context.measureText(label).width + 14);
      const labelHeight = 22;
      const labelX = Math.max(2, Math.min(width - labelWidth - 2, x));
      const labelY = Math.max(2, y - labelHeight);
      context.fillStyle = color;
      context.fillRect(labelX, labelY, labelWidth, labelHeight);
      context.fillStyle = '#fff';
      context.fillText(label, labelX + 7, labelY + 15);
    }
  }, [activeMedia, selectedObjectId, transform]);
  drawCanvasRef.current = drawCanvas;

  useEffect(() => {
    const resize = new ResizeObserver(drawCanvas);
    if (stageRef.current) resize.observe(stageRef.current);
    drawCanvas();
    return () => resize.disconnect();
  }, [drawCanvas]);

  useEffect(() => {
    stopDetectionLoop();
    videoRef.current?.pause();
    setRunState('idle');
    setSelectedObjectId(null);
    setQuery('');
    setTransform({ scale: 1, x: 0, y: 0 });
    imageRef.current = null;
    let disposed = false;
    let loadingImage: HTMLImageElement | null = null;
    if (!activeMedia) {
      drawCanvasRef.current();
    } else if (activeMedia.kind === 'image') {
      const image = new Image();
      loadingImage = image;
      const mediaId = activeMedia.id;
      image.decoding = 'async';
      image.onload = () => {
        if (disposed || activeIdRef.current !== mediaId) return;
        imageRef.current = image;
        drawCanvasRef.current();
      };
      image.onerror = () => {
        if (disposed || activeIdRef.current !== mediaId) return;
        patchMediaItem(mediaId, { status: 'error' });
        toast(`无法读取图片：${activeMedia.name}`, 'error');
      };
      image.src = activeMedia.objectUrl;
    }
    return () => {
      disposed = true;
      if (loadingImage) {
        loadingImage.onload = null;
        loadingImage.onerror = null;
      }
      stopDetectionLoop();
    };
  }, [activeId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopDetectionLoop();
      mediaRef.current.forEach((item) => URL.revokeObjectURL(item.objectUrl));
      const detector = detectorRef.current;
      detectorRef.current = null;
      void detector?.dispose();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const addFiles = useCallback(
    async (files: FileList | File[]) => {
      const incoming = Array.from(files);
      const currentKeys = new Set(media.map((item) => item.sourceKey));
      const accepted: MediaItem[] = [];
      const rejected: string[] = [];
      const duplicates: string[] = [];
      for (const file of incoming) {
        if (!isSupportedFile(file)) {
          rejected.push(file.name);
          continue;
        }
        const key = mediaKey(file);
        if (currentKeys.has(key)) {
          duplicates.push(file.name);
          continue;
        }
        currentKeys.add(key);
        try {
          accepted.push(createMediaItem(file, { sourceKey: key }));
        } catch {
          rejected.push(file.name);
        }
      }
      if (accepted.length) {
        setMedia((current) => [...current, ...accepted]);
        setActiveId((current) => current ?? accepted[0].id);
        toast(`已导入 ${accepted.length} 个媒体文件。`, 'success');
      }
      if (duplicates.length) toast(`已跳过重复文件：${duplicates.join('、')}`, 'info');
      if (rejected.length) toast(`不支持或读取失败：${rejected.join('、')}`, 'error');
    },
    [media, toast],
  );

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) void addFiles(event.target.files);
    event.target.value = '';
  };

  const removeMedia = useCallback(
    (id: string) => {
      const item = media.find((candidate) => candidate.id === id);
      if (!item) return;

      const isActive = id === activeId;
      const nextActiveId = selectMediaAfterRemoval(media, id, activeId);
      if (isActive) {
        stopDetectionLoop();
        const video = videoRef.current;
        if (item.kind === 'video' && video) {
          video.pause();
          video.removeAttribute('src');
          video.load();
        }
        imageRef.current = null;
        lastDetectionMsRef.current = 0;
        setRunState('idle');
        setSelectedObjectId(null);
        setQuery('');
        setIsDragging(false);
        setTransform({ scale: 1, x: 0, y: 0 });
      }

      const nextMedia = media.filter((candidate) => candidate.id !== id);
      mediaRef.current = nextMedia;
      setMedia(nextMedia);
      setActiveId(nextActiveId);
      releaseMediaItem(item);
      toast(`已从工作台移除 ${item.name}；本地文件未删除。`, 'success');
    },
    [activeId, media, stopDetectionLoop, toast],
  );

  const detectCurrent = useCallback(
    async (timeSeconds = 0) => {
      if (!activeMedia || inferenceInFlightRef.current) return false;
      const mediaSnapshot = activeMedia;
      const source = mediaSnapshot.kind === 'video' ? videoRef.current : imageRef.current;
      const width =
        mediaSnapshot.kind === 'video'
          ? (source as HTMLVideoElement | null)?.videoWidth
          : (source as HTMLImageElement | null)?.naturalWidth;
      const height =
        mediaSnapshot.kind === 'video'
          ? (source as HTMLVideoElement | null)?.videoHeight
          : (source as HTMLImageElement | null)?.naturalHeight;
      if (!width || !height) throw new Error('媒体尚未完成加载，请稍后重试。');
      if (!source) throw new Error('无法取得当前图片或视频帧。');
      inferenceInFlightRef.current = true;
      const generation = detectionGenerationRef.current;
      const abortController = new AbortController();
      detectionAbortRef.current = abortController;
      patchMediaItem(mediaSnapshot.id, { status: 'detecting' });
      try {
        const detector = await ensureDetector();
        if (
          abortController.signal.aborted ||
          generation !== detectionGenerationRef.current ||
          activeIdRef.current !== mediaSnapshot.id
        ) return false;
        const objects = await detector.detect({
          mediaId: mediaSnapshot.id,
          source,
          width,
          height,
          frameTime: timeSeconds,
          kind: mediaSnapshot.kind,
          signal: abortController.signal,
        });
        if (
          abortController.signal.aborted ||
          generation !== detectionGenerationRef.current ||
          activeIdRef.current !== mediaSnapshot.id
        ) return false;
        setObjects(mediaSnapshot.id, objects, timeSeconds);
        return true;
      } finally {
        if (detectionAbortRef.current === abortController) detectionAbortRef.current = null;
        inferenceInFlightRef.current = false;
      }
    },
    [activeMedia, ensureDetector, patchMediaItem, setObjects],
  );
  detectCurrentRef.current = detectCurrent;

  const startVideoLoop = useCallback(() => {
    const video = videoRef.current as
      | (HTMLVideoElement & {
          requestVideoFrameCallback?: (
            callback: (now: number, metadata: { mediaTime: number }) => void,
          ) => number;
        })
      | null;
    if (!video) return;
    stopDetectionLoop();
    const loopGeneration = detectionGenerationRef.current;
    const mediaId = activeIdRef.current;
    const isCurrentLoop = () =>
      loopGeneration === detectionGenerationRef.current &&
      mediaId !== null &&
      activeIdRef.current === mediaId &&
      videoRef.current === video;
    if (video.requestVideoFrameCallback) {
      const callback = (now: number, metadata: { mediaTime: number }) => {
        if (!isCurrentLoop()) return;
        drawCanvasRef.current();
        if (now - lastDetectionMsRef.current >= 140) {
          lastDetectionMsRef.current = now;
          void detectCurrentRef.current(metadata.mediaTime).catch((error: unknown) => {
            if (error instanceof DOMException && error.name === 'AbortError') return;
            if (!isCurrentLoop()) return;
            video.pause();
            stopDetectionLoop();
            setRunState('idle');
            toast(error instanceof Error ? error.message : '逐帧检测失败。', 'error');
          });
        }
        if (isCurrentLoop()) {
          frameCallbackRef.current = video.requestVideoFrameCallback?.(callback) ?? null;
        }
      };
      frameCallbackRef.current = video.requestVideoFrameCallback(callback);
      return;
    }
    const callback = (now: number) => {
      if (!isCurrentLoop()) return;
      drawCanvasRef.current();
      if (now - lastDetectionMsRef.current >= 180) {
        lastDetectionMsRef.current = now;
        void detectCurrentRef.current(video.currentTime).catch((error: unknown) => {
          if (error instanceof DOMException && error.name === 'AbortError') return;
          if (!isCurrentLoop()) return;
          video.pause();
          stopDetectionLoop();
          setRunState('idle');
          toast(error instanceof Error ? error.message : '逐帧检测失败。', 'error');
        });
      }
      if (isCurrentLoop()) fallbackFrameRef.current = requestAnimationFrame(callback);
    };
    fallbackFrameRef.current = requestAnimationFrame(callback);
  }, [stopDetectionLoop, toast]);

  const startOrPause = async () => {
    if (!activeMedia) {
      toast('请先使用 Add 导入图片或视频。', 'error');
      return;
    }
    if (activeMedia.kind === 'image') {
      const mediaId = activeMedia.id;
      const operationGeneration = detectionGenerationRef.current;
      try {
        setRunState('running');
        const detected = await detectCurrent(0);
        if (
          activeIdRef.current !== mediaId ||
          detectionGenerationRef.current !== operationGeneration
        ) return;
        setRunState('idle');
        if (detected) toast('真实 ONNX 人体检测完成。', 'success');
        else toast('上一帧推理尚未结束，请稍后再次点击 Start。', 'info');
      } catch (error) {
        if (
          activeIdRef.current !== mediaId ||
          detectionGenerationRef.current !== operationGeneration ||
          (error instanceof DOMException && error.name === 'AbortError')
        ) return;
        setRunState('idle');
        toast(error instanceof Error ? error.message : '图片检测失败。', 'error');
      }
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    if (runState === 'running') {
      video.pause();
      stopDetectionLoop();
      setRunState('paused');
      drawCanvas();
      return;
    }
    const mediaId = activeMedia.id;
    const operationGeneration = detectionGenerationRef.current;
    try {
      const playPromise = video.play();
      await ensureDetector();
      await playPromise;
      if (
        activeIdRef.current !== mediaId ||
        detectionGenerationRef.current !== operationGeneration ||
        videoRef.current !== video
      ) {
        video.pause();
        return;
      }
      setRunState('running');
      lastDetectionMsRef.current = 0;
      startVideoLoop();
      void detectCurrentRef.current(video.currentTime).catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (activeIdRef.current !== mediaId || videoRef.current !== video) return;
        video.pause();
        stopDetectionLoop();
        setRunState('idle');
        toast(error instanceof Error ? error.message : '视频检测失败。', 'error');
      });
    } catch (error) {
      video.pause();
      if (
        activeIdRef.current !== mediaId ||
        detectionGenerationRef.current !== operationGeneration ||
        (error instanceof DOMException && error.name === 'AbortError')
      ) return;
      setRunState('idle');
      toast(
        `视频播放失败：${error instanceof Error ? error.message : '浏览器拒绝播放'}`,
        'error',
      );
    }
  };

  const stopVideo = () => {
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.currentTime = 0;
    stopDetectionLoop();
    setRunState('idle');
    patchActive({ objects: [], detected: false, status: 'ready', currentFrame: 0 });
    setSelectedObjectId(null);
    drawCanvas();
  };

  const fitCanvas = () => setTransform({ scale: 1, x: 0, y: 0 });
  const zoomBy = (factor: number) =>
    setTransform((current) => ({
      ...current,
      scale: Math.max(0.25, Math.min(4, current.scale * factor)),
    }));

  const onWheel = (event: ReactWheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.1 : 0.9);
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (tool !== 'pan') return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsDragging(true);
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      originX: transform.x,
      originY: transform.y,
    };
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!isDragging || tool !== 'pan') return;
    setTransform((current) => ({
      ...current,
      x: dragRef.current.originX + event.clientX - dragRef.current.x,
      y: dragRef.current.originY + event.clientY - dragRef.current.y,
    }));
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setIsDragging(false);
  };

  const selectObjectAtPoint = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    if (tool !== 'pointer' || !activeMedia || !stageRef.current) return;
    const rect = stageRef.current.getBoundingClientRect();
    const source = activeMedia.kind === 'video' ? videoRef.current : imageRef.current;
    const sourceWidth =
      activeMedia.kind === 'video'
        ? (source as HTMLVideoElement | null)?.videoWidth
        : (source as HTMLImageElement | null)?.naturalWidth;
    const sourceHeight =
      activeMedia.kind === 'video'
        ? (source as HTMLVideoElement | null)?.videoHeight
        : (source as HTMLImageElement | null)?.naturalHeight;
    if (!sourceWidth || !sourceHeight) return;
    const fitScale = Math.min((rect.width - 48) / sourceWidth, (rect.height - 48) / sourceHeight);
    const renderScale = fitScale * transform.scale;
    const drawWidth = sourceWidth * renderScale;
    const drawHeight = sourceHeight * renderScale;
    const originX = (rect.width - drawWidth) / 2 + transform.x;
    const originY = (rect.height - drawHeight) / 2 + transform.y;
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const hit = [...activeMedia.objects].reverse().find((object) => {
      if (!object.visible) return false;
      const x = originX + object.bbox.x * drawWidth;
      const y = originY + object.bbox.y * drawHeight;
      return (
        px >= x &&
        px <= x + object.bbox.width * drawWidth &&
        py >= y &&
        py <= y + object.bbox.height * drawHeight
      );
    });
    setSelectedObjectId(hit?.id ?? null);
  };

  const toggleObject = (id: string) => {
    patchActive((current) => ({
      objects: current.objects.map((item) =>
        item.id === id ? { ...item, visible: !item.visible } : item,
      ),
    }));
  };

  const copyPath = async () => {
    if (!activeMedia) return;
    const description = describeAbsolutePath(activeMedia);
    try {
      await navigator.clipboard.writeText(description.value);
      toast('路径信息已复制。', 'success');
    } catch {
      toast('无法访问剪贴板，请手动复制路径提示。', 'error');
    }
  };

  const captureScreenshot = async () => {
    const canvas = canvasRef.current;
    if (!canvas || !activeMedia) {
      toast('请先选择媒体。', 'error');
      return;
    }
    drawCanvas();
    try {
      const dataUrl = canvas.toDataURL('image/png');
      const blob = await fetch(dataUrl).then((response) => response.blob());
      if (!blob.size) throw new Error('截图生成失败。');
      const result = await saveScreenshot(blob, activeMedia, {
        suggestedName: `${baseName(activeMedia.name)}_screenshot_${timestamp()}.png`,
      });
      toast(result.message, result.mode === 'download' ? 'info' : 'success');
    } catch (error) {
      toast(error instanceof Error ? error.message : '截图保存失败。', 'error');
    }
  };

  const toggleFullscreen = async () => {
    const element = document.querySelector('.aw-shell') as HTMLElement | null;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await element?.requestFullscreen();
    } catch {
      toast('当前浏览器不允许进入全屏。', 'error');
    }
  };

  const pathDescription = activeMedia
    ? describeAbsolutePath(activeMedia)
    : { value: '未选择媒体', isAbsolute: false };

  return (
    <main className="aw-page">
      <section className="aw-shell">
        <header className="aw-header">
          <div className="aw-brand" aria-label="云瞳">
            云瞳
          </div>
          <button
            type="button"
            className="aw-path"
            title={pathDescription.value}
            onClick={copyPath}
            disabled={!activeMedia}
          >
            <span>{pathDescription.value}</span>
            {activeMedia && <Clipboard aria-hidden="true" />}
          </button>
          <div className="aw-current-media">
            <strong>{activeMedia?.name ?? '等待导入媒体'}</strong>
            <small>
              {activeMedia
                ? `${media.findIndex((item) => item.id === activeMedia.id) + 1} / ${media.length} media`
                : '图片 / 视频'}
            </small>
          </div>
          <div className="aw-header-actions">
            <span className="aw-model-badge">
              {modelStatus === 'loading'
                ? 'LOADING ONNX'
                : modelStatus === 'error'
                  ? 'MODEL ERROR'
                  : 'ONNX PERSON'}
            </span>
            <button type="button" onClick={captureScreenshot} disabled={!activeMedia}>
              <Camera /> Screenshot
            </button>
            <button type="button" onClick={toggleFullscreen}>
              <Maximize /> Fullscreen
            </button>
          </div>
        </header>

        <aside className="aw-toolbar" aria-label="画布工具">
          <button type="button" disabled title="Home 暂未开放" aria-label="Home 暂未开放">
            <Home />
          </button>
          <button
            type="button"
            className={tool === 'pan' ? 'active' : ''}
            title="Pan / Zoom"
            aria-label="Pan / Zoom"
            onClick={() => setTool('pan')}
          >
            <Hand />
          </button>
          <button
            type="button"
            className={tool === 'pointer' ? 'active' : ''}
            title="Normal Pointer"
            aria-label="Normal Pointer"
            onClick={() => setTool('pointer')}
          >
            <MousePointer2 />
          </button>
        </aside>

        <section
          ref={stageRef}
          className={`aw-stage ${tool === 'pan' ? 'is-pan' : 'is-pointer'} ${isDragging ? 'is-dragging' : ''}`}
        >
          <canvas
            ref={canvasRef}
            tabIndex={0}
            aria-label="媒体检测画布；Pan 模式可拖动和滚轮缩放"
            onWheel={onWheel}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onClick={selectObjectAtPoint}
            onKeyDown={(event) => {
              if (event.key === 'Escape') setSelectedObjectId(null);
            }}
          />
          {activeMedia?.kind === 'video' && (
            <video
              ref={videoRef}
              className="aw-hidden-video"
              src={activeMedia.objectUrl}
              preload="metadata"
              playsInline
              onLoadedData={drawCanvas}
              onSeeked={drawCanvas}
              onEnded={() => {
                stopDetectionLoop();
                setRunState('idle');
                drawCanvas();
              }}
              onError={() => {
                patchMediaItem(activeMedia.id, { status: 'error' });
                toast(`无法读取视频：${activeMedia.name}`, 'error');
              }}
            >
              <track
                kind="captions"
                srcLang="zh-CN"
                label="本地检测视频无字幕"
                src="data:text/vtt;charset=utf-8,WEBVTT"
              />
            </video>
          )}
          {!activeMedia && (
            <div className="aw-empty-stage">
              <div><FileImage /><Film /></div>
              <strong>添加图片或视频开始检测</strong>
              <span>媒体只在本机浏览器中处理，不会自动上传</span>
              <button type="button" onClick={() => fileInputRef.current?.click()}>
                <Plus /> Add media
              </button>
            </div>
          )}
          <div className="aw-stage-status">
            <span>{tool === 'pan' ? 'Pan / Zoom' : 'Normal Pointer'}</span>
            <span>{activeMedia?.detected ? `${activeMedia.objects.length} objects` : '未检测'}</span>
          </div>
          <div className="aw-stage-controls">
            <button type="button" aria-label="缩小" onClick={() => zoomBy(0.9)}><ZoomOut /></button>
            <span>{Math.round(transform.scale * 100)}%</span>
            <button type="button" aria-label="放大" onClick={() => zoomBy(1.1)}><ZoomIn /></button>
            <button type="button" onClick={fitCanvas} title="Fit"><Focus /> Fit</button>
            <button type="button" onClick={() => setTransform({ scale: 1, x: 0, y: 0 })} title="Reset"><RotateCcw /></button>
            <button
              type="button"
              className="aw-start"
              onClick={startOrPause}
              disabled={
                !activeMedia ||
                modelStatus === 'loading' ||
                (activeMedia.kind === 'image' && runState === 'running')
              }
            >
              {runState === 'running' && activeMedia?.kind === 'video' ? <Pause /> : <Play />}
              {modelStatus === 'loading'
                ? 'Loading...'
                : runState === 'running' && activeMedia?.kind === 'video'
                  ? 'Pause'
                  : 'Start'}
            </button>
            {activeMedia?.kind === 'video' && runState !== 'idle' && (
              <button type="button" className="aw-stop" onClick={stopVideo}><Square /> Stop</button>
            )}
          </div>
        </section>

        <aside className="aw-inspector">
          <section className="aw-panel aw-media-panel">
            <div className="aw-panel-heading">
              <div><FileImage /><strong>Images / Media</strong><span>{media.length}</span></div>
              <button type="button" className="aw-add" onClick={() => fileInputRef.current?.click()}><Plus /> Add</button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*,video/*,.mov,.m4v,.ogv,.avi,.mkv,.mpeg,.mpg,.heic,.heif,.avif"
                multiple
                hidden
                onChange={onFileChange}
              />
            </div>
            <div className="aw-media-list">
              {media.map((item) => (
                <div
                  key={item.id}
                  className={`aw-media-row ${item.id === activeId ? 'active' : ''}`}
                >
                  <button
                    type="button"
                    className="aw-media-select"
                    aria-pressed={item.id === activeId}
                    onClick={() => setActiveId(item.id)}
                  >
                    <span className="aw-thumb">
                      {item.kind === 'image' ? (
                        <img
                          src={item.objectUrl}
                          alt=""
                          onError={() => patchMediaItem(item.id, { status: 'error' })}
                        />
                      ) : (
                        <video
                          src={item.objectUrl}
                          muted
                          preload="metadata"
                          onError={() => patchMediaItem(item.id, { status: 'error' })}
                        >
                          <track
                            kind="captions"
                            srcLang="zh-CN"
                            label="视频缩略图无字幕"
                            src="data:text/vtt;charset=utf-8,WEBVTT"
                          />
                        </video>
                      )}
                      <i>{item.kind === 'video' ? <Film /> : <FileImage />}</i>
                    </span>
                    <span className="aw-media-copy">
                      <strong title={item.name}>{item.name}</strong>
                      <small>
                        {item.kind === 'video' ? 'Video' : 'Image'} ·{' '}
                        {item.status === 'error'
                          ? '读取失败'
                          : item.detected
                            ? `${item.objects.length} objects`
                            : '未检测'}
                      </small>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="aw-media-delete"
                    aria-label={`从工作台移除 ${item.name}，不删除本地文件`}
                    title="从工作台移除（不会删除本地文件）"
                    onClick={() => removeMedia(item.id)}
                  >
                    <Trash2 />
                  </button>
                </div>
              ))}
              {!media.length && <p className="aw-empty-list">尚未导入媒体</p>}
            </div>
          </section>

          <section className="aw-panel aw-object-panel">
            <div className="aw-panel-heading aw-object-heading">
              <div><Square /><strong>Objects</strong><span>{visibleObjects.length}</span></div>
              <label className="aw-search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search class" /></label>
            </div>
            <div className="aw-object-columns"><span>ID</span><span>Class / confidence</span><span>Visible</span></div>
            <div className="aw-object-list">
              {filteredObjects.map((object) => (
                <div key={object.id} className={`aw-object-row ${selectedObjectId === object.id ? 'selected' : ''}`}>
                  <button
                    type="button"
                    className="aw-object-main"
                    onClick={() => setSelectedObjectId(object.id)}
                  >
                    <span className="aw-object-id">{object.id}</span>
                    <span className="aw-object-class">
                      <i style={{ background: objectColor(object.class) }} />
                      <span><strong>{object.class}</strong><small>{(object.confidence * 100).toFixed(1)}%</small></span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="aw-eye"
                    title={object.visible ? '隐藏检测框' : '显示检测框'}
                    onClick={(event) => { event.stopPropagation(); toggleObject(object.id); }}
                  >
                    {object.visible ? <Eye /> : <EyeOff />}
                  </button>
                </div>
              ))}
              {activeMedia && activeMedia.detected && !filteredObjects.length && (
                <div className="aw-empty-objects">没有匹配的 class</div>
              )}
              {activeMedia && !activeMedia.detected && (
                <div className="aw-empty-objects">点击 Start 运行真实 ONNX 人体检测</div>
              )}
              {!activeMedia && <div className="aw-empty-objects">请选择媒体</div>}
            </div>
          </section>
          <footer className="aw-capability-note">
            <strong>本地能力</strong>
            <span>{pathDescription.isAbsolute ? '已取得真实路径' : '浏览器无法提供绝对路径'}</span>
            <small>本机离线运行 person ONNX 模型；不会上传本地媒体。</small>
          </footer>
        </aside>

        <div className="aw-toasts" aria-live="polite">
          {toasts.map((item) => <div key={item.id} className={`aw-toast ${item.tone}`}>{item.message}</div>)}
        </div>
      </section>
    </main>
  );
}
