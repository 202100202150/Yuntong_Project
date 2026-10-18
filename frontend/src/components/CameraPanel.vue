<template>
  <section class="dc-camera-panel">
    <header>
      <div><Video /><strong>{{ cameraId }}</strong></div>
      <div class="dc-camera-meta">
        <span>{{ displayFps }} FPS</span>
        <span>{{ inferenceLabel }}</span>
        <span v-if="bridge.detecting && status.detectionCount > 0">{{ status.detectionCount }} person</span>
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
import { cameraBridge } from '@/api/camera-bridge';
import type { CameraBridgeStatus, CameraId, CameraRuntimeStatus } from '@/types/camera-types';

const props = defineProps<{
  cameraId: CameraId;
  status: CameraRuntimeStatus;
  displayFps: number;
  bridge: CameraBridgeStatus;
}>();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const latestBitmap = ref<ImageBitmap | null>(null);

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

function drawFrame(bitmap: ImageBitmap) {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#0f1722';
  ctx.fillRect(0, 0, rect.width, rect.height);
  const scale = Math.min(rect.width / bitmap.width, rect.height / bitmap.height);
  const w = bitmap.width * scale;
  const h = bitmap.height * scale;
  ctx.drawImage(bitmap, (rect.width - w) / 2, (rect.height - h) / 2, w, h);
}

let cancelled = false;
onMounted(() => {
  let after = 0;
  (async () => {
    while (!cancelled) {
      try {
        const frame = await cameraBridge.getFrame(props.cameraId, after);
        if (frame) {
          after = frame.sequence;
          const bitmap = await createImageBitmap(new Blob([frame.jpegBytes], { type: 'image/jpeg' }));
          if (cancelled) { bitmap.close(); return; }
          latestBitmap.value?.close();
          latestBitmap.value = bitmap;
          drawFrame(bitmap);
        }
      } catch {
        await new Promise((r) => setTimeout(r, 150));
      }
    }
  })();
});

onUnmounted(() => {
  cancelled = true;
  latestBitmap.value?.close();
  latestBitmap.value = null;
});
</script>