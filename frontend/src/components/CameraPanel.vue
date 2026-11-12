<template>
  <section class="dc-camera-panel">
    <header>
      <div><Video /><strong>{{ cameraId }}</strong></div>
      <div class="dc-camera-meta">
        <span>{{ displayFps }} FPS</span>
        <span>{{ inferenceLabel }}</span>
        <span v-if="bridge.detecting && status.detectionCount > 0">{{ status.detectionCount }} 架无人机</span>
        <span :class="['dc-live-state', status.state]">{{ stateText }}</span>
      </div>
    </header>
    <div class="dc-camera-viewport">
      <canvas ref="canvasRef" :aria-label="`${cameraId} 实时检测画面`" />
      <div v-if="!status.frameAvailable" class="dc-camera-placeholder">
        <Video />
        <strong>正在连接摄像机</strong>
        <span>{{ status.error || bridge.error || '等待实时画面…' }}</span>
      </div>
      <div v-if="bridge.recording" class="dc-recording-indicator"><i /> REC</div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { Video } from 'lucide-vue-next';
import type { CameraBridgeStatus, CameraId, CameraRuntimeStatus } from '@/types/camera-types';

const props = defineProps<{
  cameraId: CameraId;
  status: CameraRuntimeStatus;
  displayFps: number;
  bridge: CameraBridgeStatus;
}>();

const canvasRef = ref<HTMLCanvasElement | null>(null);
let latestBitmap: ImageBitmap | null = null;
let resizeObserver: ResizeObserver | undefined;

const stateText = computed(() => {
  switch (props.status.state) {
    case 'streaming': return 'LIVE';
    case 'reconnecting': return 'RECONNECTING';
    case 'error': return 'ERROR';
    case 'stopped': return 'STOPPED';
    default: return 'CONNECTING';
  }
});

const inferenceLabel = computed(() => {
  const provider = props.bridge.inferenceProvider.trim();
  if (provider.toLowerCase().includes('tensorrt')) return 'TensorRT';
  if (provider.toLowerCase().includes('cuda')) return 'CUDA';
  if (provider && provider !== 'unreported') return `CPU / ${provider}`;
  return 'GPU 状态未上报';
});

function drawFrame() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pixelWidth = Math.max(1, Math.round(rect.width * dpr));
  const pixelHeight = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
  if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#0f1722';
  ctx.fillRect(0, 0, rect.width, rect.height);
  const bitmap = latestBitmap;
  if (!bitmap) return;
  const scale = Math.min(rect.width / bitmap.width, rect.height / bitmap.height);
  const w = bitmap.width * scale;
  const h = bitmap.height * scale;
  ctx.drawImage(bitmap, (rect.width - w) / 2, (rect.height - h) / 2, w, h);
}

function presentFrame(bitmap: ImageBitmap) {
  latestBitmap?.close();
  latestBitmap = bitmap;
  drawFrame();
}

function clearFrame() {
  latestBitmap?.close();
  latestBitmap = null;
  drawFrame();
}

defineExpose({ presentFrame, clearFrame });

onMounted(() => {
  if (canvasRef.value) {
    resizeObserver = new ResizeObserver(drawFrame);
    resizeObserver.observe(canvasRef.value);
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  latestBitmap?.close();
  latestBitmap = null;
});
</script>
