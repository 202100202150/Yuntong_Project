<template>
  <div class="workspace-container">
    <!-- 顶部工具栏（录制、截图） -->
    <Teleport defer to="#global-header-actions">
      <div class="toolbar-actions">
        <button class="action-btn" :class="{ 'is-active': status.recording }" @click="toggleRecording" :disabled="!serviceReady || recordBusy">
          <Loader2 v-if="recordBusy" class="spin" :size="16" />
          <Circle v-else :size="16" fill="currentColor" />
          {{ status.recording ? '停止录制' : '开始录制' }}
        </button>
        <button class="action-btn" @click="captureBoth" :disabled="!serviceReady || !bothFramesReady || snapshotBusy">
          <Loader2 v-if="snapshotBusy" class="spin" :size="16" />
          <Camera v-else :size="16" />
          截图保存
        </button>
      </div>
    </Teleport>

    <!-- 工作区网格 -->
    <div class="workspace-grid">
      <!-- 3D 面板 -->
      <div class="panel-card card-left" ref="card3dRef">
        <div class="card-header">
          <div class="card-title">
            <span>空间定位</span>
            <span class="status-pill" :class="status.spatial.state === 'ready' ? 'success' : 'idle'">
              {{ status.spatial.state === 'uncalibrated' ? '需要标定' : status.spatial.state === 'ready' ? '米制定位' : '等待有效定位' }}
            </span>
          </div>
          <div class="header-actions">
            <!-- 👇 新增：3D / 2D 切换按钮 -->
            <div class="mode-switch">
              <button
                :class="{ active: spatialSceneRef?.sceneMode === '3d' }"
                @click="spatialSceneRef?.switchMode('3d')"
              >3D</button>
              <button
                :class="{ active: spatialSceneRef?.sceneMode === '2d' }"
                @click="spatialSceneRef?.switchMode('2d')"
              >2D</button>
            </div>

            <button class="icon-action-btn" @click="enterNativeFullscreen('3d')" title="全屏显示">
              <Maximize :size="16" />
            </button>
          </div>
        </div>
        <div class="card-body spatial-body">
          <SpatialScene
            ref="spatialSceneRef"
            :detecting="status.detecting"
            :spatial="status.spatial"
          />
          <div class="custom-legend">
            <span><i class="dot x"></i> X 左</span>
            <span><i class="dot y"></i> Y 上</span>
            <span><i class="dot z"></i> Z 前</span>
            <span><i class="dot drone"></i> 无人机</span>
            <span class="target-count">有效定位：{{ totalTargets }} · 单位 m</span>
          </div>
        </div>
      </div>

      <!-- 双路监控面板 -->
      <div class="panel-card card-right">
        <div class="card-header">
          <div class="card-title">
            <span>双路监控</span>
            <span v-if="waitingForPair" class="status-pill idle">等待同步画面</span>
          </div>
          <div class="header-actions">
            <button class="start-btn" :class="{ 'is-stop': status.detecting }" @click="toggleDetection" :disabled="!serviceReady || runState === 'starting'">
              <Loader2 v-if="runState === 'starting'" class="spin" :size="14" />
              <component v-else :is="status.detecting ? Pause : Play" :size="14" />
              {{ runState === 'starting' ? '启动中...' : status.detecting ? '暂停检测' : '开始检测' }}
            </button>
          </div>
        </div>

        <div class="card-body cameras-body">
          <div class="camera-wrapper" v-for="id in cameraIds" :key="id" :ref="(el) => setCameraRef(el, id)">
            <CameraPanel
              :ref="(panel) => setCameraPanelRef(id, panel)"
              :camera-id="id" :status="status.cameras[id]" :display-fps="getRealFps(id)" :bridge="status"
            />

            <div class="error-overlay" v-if="!status.cameras[id].frameAvailable && status.serviceState === 'error'">
              <AlertCircle :size="24" class="error-icon" />
              <p class="error-title">服务未连接：无法获取视频流 (500)</p>
              <button @click="retryConnection" class="retry-btn">
                <RefreshCw :size="14" /> 重新连接
              </button>
            </div>

            <div class="camera-btn-group">
              <button class="camera-action-btn" @click="enterNativeFullscreen(id)" title="全屏显示">
                <Maximize :size="14" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, nextTick, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { Camera, Circle, Maximize, Play, Pause, Loader2, AlertCircle, RefreshCw } from 'lucide-vue-next';
import { useCameraStore } from '@/stores/camera';
import { cameraBridge } from '@/api/camera-bridge';
import SpatialScene from '@/components/SpatialScene.vue';
import CameraPanel from '@/components/CameraPanel.vue';
import type { CameraId } from '@/types/camera-types';

const store = useCameraStore();
const { status, runState } = storeToRefs(store);
const cameraIds: CameraId[] = ['camera1', 'camera2'];
const recordBusy = ref(false);
const snapshotBusy = ref(false);

const card3dRef = ref<HTMLElement | null>(null);
const spatialSceneRef = ref<any>(null); // 👈 新增
const camera1Ref = ref<HTMLElement | null>(null);
const camera2Ref = ref<HTMLElement | null>(null);
const cameraPanels: Partial<Record<CameraId, InstanceType<typeof CameraPanel>>> = {};
const waitingForPair = ref(false);

function setCameraPanelRef(id: CameraId, panel: unknown) {
  if (panel) cameraPanels[id] = panel as InstanceType<typeof CameraPanel>;
  else delete cameraPanels[id];
}

const setCameraRef = (el: any, id: CameraId) => {
  if (id === 'camera1') camera1Ref.value = el;
  if (id === 'camera2') camera2Ref.value = el;
};

const serviceReady = computed(() => status.value.serviceState === 'ready' || status.value.serviceState === 'error');
const bothFramesReady = computed(() => cameraIds.every((id) => status.value.cameras[id].frameAvailable));
const totalTargets = computed(() => status.value.detecting ? status.value.spatial.targets.length : 0);
const previewMode = computed<'pair' | CameraId | null>(() => {
  const live = cameraIds.filter((id) => {
    const camera = status.value.cameras[id];
    return camera.state === 'streaming' && camera.frameAvailable;
  });
  return live.length === 2 ? 'pair' : live[0] ?? null;
});

const getRealFps = (id: CameraId) => {
  const cam = status.value.cameras[id];
  return Math.round(cam.previewFps || cam.decodeFps || 0);
};

const toggleDetection = () => store.toggleDetection();
const retryConnection = () => store.refreshStatus();

async function toggleRecording() {
  if (!serviceReady.value || recordBusy.value) return;
  recordBusy.value = true;
  try { await store.toggleRecording(); } finally { recordBusy.value = false; }
}

async function captureBoth() {
  if (!serviceReady.value || snapshotBusy.value) return;
  snapshotBusy.value = true;
  try { await store.captureBoth(); } finally { snapshotBusy.value = false; }
}

async function enterNativeFullscreen(target: '3d' | CameraId) {
  let el = target === '3d' ? card3dRef.value : target === 'camera1' ? camera1Ref.value : camera2Ref.value;
  if (!el) return;
  try {
    if (!document.fullscreenElement) await el.requestFullscreen();
    else await document.exitFullscreen();
    nextTick(() => setTimeout(() => window.dispatchEvent(new Event('resize')), 100));
  } catch { store.toast('当前环境拒绝全屏。', 'error'); }
}

const handleFullscreenChange = () => nextTick(() => setTimeout(() => window.dispatchEvent(new Event('resize')), 100));

let cancelled = false;
let previewRequest: AbortController | undefined;
const singleCursor: Record<CameraId, number> = { camera1: 0, camera2: 0 };
const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

watch(previewMode, (mode) => {
  previewRequest?.abort();
  waitingForPair.value = false;
  if (mode === 'camera1' || mode === 'camera2') singleCursor[mode] = 0;
  if (mode !== 'pair') {
    for (const id of cameraIds) {
      if (id !== mode) cameraPanels[id]?.clearFrame();
    }
  }
});

// A restarted camera service can reset the old single-camera endpoint's frame sequence.
watch(() => cameraIds.map((id) => status.value.cameras[id].sequence), (current, previous) => {
  cameraIds.forEach((id, index) => {
    if (current[index] < previous[index]) {
      singleCursor[id] = 0;
      if (previewMode.value === id) previewRequest?.abort();
    }
  });
});

async function runPreview() {
  const pairedCursor = { camera1: 0, camera2: 0 };
  let epoch = '';
  while (!cancelled) {
    const mode = previewMode.value;
    const { camera1, camera2 } = cameraPanels;
    if (!mode || !camera1 || !camera2 || document.hidden) {
      await delay(100);
      continue;
    }
    const request = new AbortController();
    previewRequest = request;
    try {
      if (mode === 'pair') {
        const pair = await cameraBridge.getPreviewPair(pairedCursor, epoch, request.signal);
        if (request.signal.aborted) continue;
        if (!pair) {
          waitingForPair.value = true;
          await delay(20);
          continue;
        }
        pairedCursor.camera1 = pair.camera1.sequence;
        pairedCursor.camera2 = pair.camera2.sequence;
        epoch = pair.epoch;
        const [first, second] = await Promise.allSettled([
          createImageBitmap(pair.camera1.jpeg),
          createImageBitmap(pair.camera2.jpeg),
        ]);
        if (request.signal.aborted || first.status !== 'fulfilled' || second.status !== 'fulfilled') {
          if (first.status === 'fulfilled') first.value.close();
          if (second.status === 'fulfilled') second.value.close();
          continue;
        }
        // Present both decoded images in the same browser task. A slow JPEG
        // never advances one canvas while the other is still on the old pair.
        camera1.presentFrame(first.value);
        camera2.presentFrame(second.value);
        waitingForPair.value = false;
      } else {
        const frame = await cameraBridge.getFrame(mode, singleCursor[mode], request.signal);
        if (request.signal.aborted) continue;
        if (!frame) {
          await delay(20);
          continue;
        }
        singleCursor[mode] = frame.sequence;
        const bytes = new Uint8Array(frame.jpegBytes);
        const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }));
        if (request.signal.aborted) bitmap.close();
        else {
          pairedCursor[mode] = frame.sequence;
          (mode === 'camera1' ? camera1 : camera2).presentFrame(bitmap);
        }
      }
    } catch {
      if (!request.signal.aborted) await delay(150);
    } finally {
      previewRequest = undefined;
    }
  }
}

onMounted(() => {
  void runPreview();
  (async () => {
    while (!cancelled) {
      try {
        await store.refreshStatus();
      } catch (e) {
        // 静默处理
      }
      // Localization runs in the sidecar independently of this serial poll.
      await delay(status.value.detecting && !document.hidden ? 250 : 1000);
    }
  })();
  document.addEventListener('fullscreenchange', handleFullscreenChange);
});

onUnmounted(() => {
  cancelled = true;
  previewRequest?.abort();
  document.removeEventListener('fullscreenchange', handleFullscreenChange);
});
</script>

<style scoped>
.workspace-container { width: 100%; height: 100%; padding: 24px; display: flex; flex-direction: column; overflow: hidden; box-sizing: border-box; }
.toolbar-actions { display: flex; align-items: center; gap: 12px; }
.toolbar-actions .action-btn { display: flex; align-items: center; gap: 6px; padding: 6px 14px; background: transparent; border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-main); font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.2s; }
.toolbar-actions .action-btn:hover:not(:disabled) { background: rgba(148, 163, 184, 0.1); border-color: #94a3b8; }
.toolbar-actions .action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.toolbar-actions .action-btn.is-active { background: rgba(239, 68, 68, 0.1); border-color: #ef4444; color: #ef4444; }
.workspace-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 24px; flex: 1; min-height: 0; }
.panel-card { background: var(--header-bg); border-radius: 16px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03); display: flex; flex-direction: column; overflow: hidden; border: 1px solid var(--border-color); }
.card-header { padding: 16px 20px; background: var(--header-bg); border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; flex-shrink: 0; height: 64px; box-sizing: border-box; }
.card-title { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 12px; color: var(--text-main); }
.card-body { flex: 1; position: relative; min-height: 0; }
.status-pill { font-size: 12px; padding: 3px 8px; border-radius: 12px; font-weight: normal; }
.status-pill.idle { background: #e2e8f0; color: #475569; }
.status-pill.success { background: #dcfce7; color: #166534; }
.spatial-body { padding: 0; display: flex; flex-direction: column; background-color: var(--header-bg); min-height: 0; }
:deep(.spatial-scene-title), :deep(.spatial-mode-switch), :deep(.spatial-scene-hint), :deep(.spatial-scene-legend) { display: none !important; }
:deep(.spatial-scene) { position: absolute; top: 0; left: 0; right: 0; bottom: 40px; background: transparent; overflow: hidden; }
:deep(.spatial-scene canvas) { position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: block; }
.custom-legend { position: absolute; bottom: 0; left: 0; right: 0; height: 40px; background: var(--header-bg); display: flex; align-items: center; padding: 0 20px; gap: 16px; font-size: 12px; color: var(--text-sub); border-top: 1px solid var(--border-color); z-index: 10; }
.custom-legend .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
.dot.x { background: #ef4444; } .dot.y { background: #10b981; } .dot.z { background: #3b82f6; } .dot.drone { background: #14b8a6; }
.target-count { margin-left: auto; font-weight: 600; color: var(--text-main); }
.cameras-body { padding: 16px; display: flex; flex-direction: column; gap: 16px; overflow: hidden; background: transparent; }
.camera-wrapper { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; border-radius: 12px; }
.camera-btn-group { position: absolute; top: 8px; right: 8px; z-index: 99; display: flex; gap: 6px; }
.camera-action-btn { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; background: rgba(255, 255, 255, 0.9); border: 1px solid #cbd5e1; border-radius: 6px; color: #475569; cursor: pointer; }
.error-overlay { position: absolute; top: 44px; left: 0; right: 0; bottom: 0; background: rgba(30, 41, 59, 0.95); display: flex; flex-direction: column; align-items: center; justify-content: center; color: #cbd5e1; z-index: 50; border-radius: 0 0 12px 12px; padding: 16px; box-sizing: border-box; }
.error-icon { color: #f87171; margin-bottom: 8px; flex-shrink: 0; }
.error-title { margin: 0 0 20px 0; font-size: 14px; font-weight: 500; color: #f8fafc; text-align: center; flex-shrink: 0; }
.retry-btn { display: flex; align-items: center; gap: 6px; background: #3b82f6; color: white; border: 0; padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: background 0.2s; flex-shrink: 0; }
.retry-btn:hover { background: #2563eb; }
:deep(.dc-camera-panel .dc-camera-placeholder), :deep(.dc-camera-panel .error-message), :deep(.dc-camera-panel .dc-camera-error), :deep(.dc-camera-panel .dc-camera-overlay), :deep(.dc-camera-panel .dc-camera-status-text) { display: none !important; }
:deep(.dc-camera-panel) { flex: 1; min-height: 0; height: 100%; display: flex; flex-direction: column; border: 1px solid var(--border-color); border-radius: 12px; overflow: hidden; background: transparent; }
:deep(.dc-camera-panel > header) { height: 44px; flex-shrink: 0; background: var(--header-bg); border-bottom: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; padding: 0 16px; padding-right: 76px; }
:deep(.dc-camera-panel > header svg) { display: none; }
:deep(.dc-camera-panel > header strong) { color: var(--text-main); }
:deep(.dc-camera-meta) { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--text-sub); }
:deep(.dc-camera-viewport) { flex: 1; min-height: 0; height: 100%; background: #cbd5e1; position: relative; overflow: hidden; }
:deep(.dc-camera-viewport canvas) { position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: contain; }
.start-btn { display: flex; align-items: center; gap: 6px; background: #059669; color: #ffffff; border: 0; padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; flex-shrink: 0; transition: background 0.2s; }
.start-btn:hover:not(:disabled) { background: #047857; }
.start-btn.is-stop { background: #ef4444; }
.start-btn.is-stop:hover:not(:disabled) { background: #dc2626; }
.start-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.header-actions { display: flex; align-items: center; gap: 8px; }
.icon-action-btn { display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; background: transparent; border: 1px solid transparent; border-radius: 6px; color: var(--text-sub); cursor: pointer; transition: all 0.2s; }
.icon-action-btn:hover { background: #f1f5f9; color: var(--text-main); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { 100% { transform: rotate(360deg); } }

/* 👇 新增 3D/2D 切换按钮样式 */
.mode-switch {
  display: flex;
  background: rgba(148, 163, 184, 0.1);
  border-radius: 6px;
  padding: 2px;
  margin-right: 8px;
}
.mode-switch button {
  padding: 4px 12px;
  border: none;
  background: transparent;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-sub);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.mode-switch button.active {
  background: var(--header-bg);
  color: #3b82f6;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.panel-card:fullscreen, .camera-wrapper:fullscreen, .panel-card:fullscreen :deep(.dc-camera-panel), .camera-wrapper:fullscreen :deep(.dc-camera-panel) { border-radius: 0 !important; border: none !important; width: 100vw !important; height: 100vh !important; display: flex !important; flex-direction: column !important; background: var(--header-bg) !important; }
.panel-card:fullscreen :deep(.dc-camera-viewport), .camera-wrapper:fullscreen :deep(.dc-camera-viewport) { flex: 1 !important; height: 100% !important; }
.panel-card:fullscreen .header-actions, .camera-wrapper:fullscreen .camera-btn-group { display: none !important; }
</style>
