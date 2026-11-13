<template><section class="dc-camera-panel"><div class="dc-camera-viewport"><canvas ref="canvasRef" :aria-label="`${cameraId} ${!status.frameAvailable?'未接入视频源':paused?'已暂停画面':frameStale?'停止更新的画面':'实时检测画面'}`"/><div v-if="!status.frameAvailable" class="dc-camera-placeholder"><VideoOff :stroke-width="1.3"/><strong>{{bridge.serviceState==='error'?'摄像机未连接':'等待视频流'}}</strong><span>{{status.error?'请检查设备配置及网络连接':'设备接入后自动显示实时画面'}}</span></div><div v-if="status.frameAvailable&&frameStale" class="dc-frame-wait"><VideoOff :size="24"/><strong>正在等待新画面</strong><span>视频流暂时中断，当前画面已停止更新</span></div><header><div><span class="camera-code">{{cameraId==='camera1'?'C1':'C2'}}</span><strong>{{status.label===cameraId?'摄像机 '+(cameraId==='camera1'?'1':'2'):status.label}}</strong></div><span :class="['dc-live-state',paused?'paused':frameStale?'waiting':status.state]"><i/>{{stateText}}</span></header><div v-if="status.recordingState==='recording'" class="dc-recording-indicator"><i/> REC</div><span v-else-if="['armed','starting','stopping','error'].includes(status.recordingState)" class="dc-recording-indicator">{{status.recordingState==='stopping'?'录像正在保存':status.recordingState==='error'?'录像异常':'录像准备中'}}</span><div class="camera-frame-meta" v-if="status.frameAvailable&&!paused&&!frameStale">{{displayFps}} FPS <span v-if="status.width">{{status.width}} × {{status.height}}</span></div></div><footer><button class="video-play-control" :aria-label="paused?'恢复实时画面':'暂停实时画面'" :disabled="!status.frameAvailable||!lastFrameMilliseconds" @click="paused=!paused"><component :is="paused?Play:Pause" :size="22" fill="currentColor"/></button><time :title="paused?'暂停帧的接收时间':'最新画面的接收时间'">{{lastFrameAt}}</time><span v-if="paused" class="camera-footer-status">画面已暂停</span><span v-else-if="frameStale" class="camera-footer-status">画面更新已中断</span><span v-else-if="status.frameAvailable&&status.detectionCount>0" class="detection-count">{{status.detectionCount}} 个目标</span><span v-else class="camera-footer-status">{{status.frameAvailable?'实时画面':'未接入视频源'}}</span></footer></section></template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { Play, Pause, VideoOff } from 'lucide-vue-next';
import type { CameraBridgeStatus, CameraId, CameraRuntimeStatus } from '@/types/camera-types';

const props = defineProps<{
  cameraId: CameraId;
  status: CameraRuntimeStatus;
  displayFps: number;
  bridge: CameraBridgeStatus;
}>();

const lastFrameAt = ref('—');
const paused = ref(false);
const lastFrameMilliseconds = ref(0), now = ref(Date.now());
const frameStale = computed(() => props.status.frameAvailable && !paused.value && now.value - lastFrameMilliseconds.value > 3000);
let freshnessTimer: ReturnType<typeof setInterval>;
const canvasRef = ref<HTMLCanvasElement | null>(null);
let latestBitmap: ImageBitmap | null = null;
let resizeObserver: ResizeObserver | undefined;

const stateText = computed(() => {
  if (paused.value && props.status.frameAvailable) return '画面暂停';
  if (frameStale.value) return '等待画面';
  switch (props.status.state) {
    case 'streaming': return '在线';
    case 'reconnecting': return '重连中';
    case 'error': return '离线';
    case 'stopped': return '已停止';
    default: return '连接中';
  }
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
  ctx.fillStyle = '#f0f4f9';
  ctx.fillRect(0, 0, rect.width, rect.height);
  const bitmap = latestBitmap;
  if (!bitmap) return;
  const scale = Math.min(rect.width / bitmap.width, rect.height / bitmap.height);
  const w = bitmap.width * scale;
  const h = bitmap.height * scale;
  ctx.drawImage(bitmap, (rect.width - w) / 2, (rect.height - h) / 2, w, h);
}

function presentFrame(bitmap: ImageBitmap) {
  if (paused.value) { bitmap.close(); return; }
  lastFrameMilliseconds.value = Date.now();
  now.value = Date.now();
  lastFrameAt.value = new Date().toLocaleTimeString('zh-CN', {hour12:false});
  latestBitmap?.close();
  latestBitmap = bitmap;
  drawFrame();
}

function clearFrame() {
  lastFrameAt.value = '—';
  lastFrameMilliseconds.value = 0;
  paused.value = false;
  latestBitmap?.close();
  latestBitmap = null;
  drawFrame();
}

defineExpose({ presentFrame, clearFrame });

onMounted(() => {
  freshnessTimer = setInterval(() => now.value = Date.now(), 500);
  if (canvasRef.value) {
    resizeObserver = new ResizeObserver(drawFrame);
    resizeObserver.observe(canvasRef.value);
  }
});

onUnmounted(() => {
  clearInterval(freshnessTimer);
  resizeObserver?.disconnect();
  latestBitmap?.close();
  latestBitmap = null;
});
</script>

<style scoped>
.dc-camera-panel{height:100%;position:relative;display:flex;flex-direction:column;background:#edf2fa}.dc-camera-viewport{position:relative;flex:1;min-height:0;isolation:isolate}.dc-camera-viewport canvas{width:100%;height:100%;display:block}.dc-camera-viewport>header{position:absolute;top:9px;left:11px;right:11px;display:flex;justify-content:space-between;align-items:center;gap:10px;z-index:2}.dc-camera-viewport>header>div{display:flex;align-items:center;gap:10px;background:#28384cc7;padding:4px 11px;border-radius:5px;color:#fff;backdrop-filter:blur(8px);height:31px}.camera-code{font-size:18px;font-weight:650}.dc-camera-panel header strong{font-size:17px;font-weight:600;line-height:1.3}.dc-live-state{display:flex;align-items:center;gap:6px;color:#d6deea;background:#29394b9e;border-radius:4px;font-size:12px;padding:4px 7px}.dc-live-state i{width:6px;height:6px;border-radius:50%;background:currentColor}.dc-live-state.streaming{color:#bcf4d2}.dc-live-state.waiting{color:#ffe1a2}.dc-live-state.paused{color:#dce8fc}
.dc-camera-placeholder{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:#a1b1c8;background:radial-gradient(ellipse at 50% 40%,#f5f8fe,#e7effa);text-align:center;padding:40px 15px}.dc-camera-placeholder svg{width:36px;height:36px;color:#9eafc8}.dc-camera-placeholder strong{font-size:17px;font-weight:500;color:#7488a5}.dc-camera-placeholder span{font-size:14px;color:#8e9fb9}.dc-camera-panel>footer{position:absolute;left:0;right:0;bottom:0;z-index:3;display:flex;align-items:center;gap:16px;height:41px;padding:0 18px;background:linear-gradient(#303d4d8a,#232d3bba);color:#fff;font-size:17px;font-weight:500}.dc-camera-panel>footer time{font-variant-numeric:tabular-nums}.video-play-control{display:grid;place-items:center;padding:0;width:25px;height:30px;background:none;border:0;color:#fff}.video-play-control:disabled{opacity:.45}.video-play-control svg{stroke-width:1.2}.camera-footer-status,.detection-count{margin-left:auto;margin-right:32px;font-size:12px;color:#eef3fc}.camera-frame-meta{position:absolute;bottom:47px;right:10px;background:#26384f80;border-radius:4px;padding:3px 7px;font-size:11px;color:#f7faff;display:flex;gap:9px}.dc-recording-indicator{position:absolute;bottom:48px;left:12px;padding:4px 7px;background:#df4b4be6;color:#fff;font-size:11px;border-radius:4px;display:flex;gap:5px;align-items:center}.dc-recording-indicator i{width:5px;height:5px;background:white;border-radius:50%}.dc-frame-wait{position:absolute;inset:0;background:#edf3fbdc;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px;color:#8399b6;backdrop-filter:blur(3px);padding:40px 15px}.dc-frame-wait strong{font-size:17px;font-weight:500}.dc-frame-wait span{font-size:14px}
@media(max-width:760px){.dc-camera-panel header strong{font-size:14px}.camera-code{font-size:16px}.camera-frame-meta{display:none}.dc-camera-panel>footer{padding:0 12px;font-size:15px}}
</style>
