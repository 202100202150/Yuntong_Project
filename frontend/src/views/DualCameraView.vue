<template><section class="monitor-page">
<div class="page-heading"><h1>实时监控</h1><div class="actions"><button class="btn" @click="captureBoth" :disabled="!auth.can('export')||!bothFramesReady||snapshotBusy" :title="!auth.can('export')?'当前账号没有抓拍权限':!bothFramesReady?'等待两路摄像机提供画面':'保存两路当前画面并提供下载'"><Camera :size="18"/>截图</button><button class="btn" @click="toggleRecording" :disabled="!auth.can('review')||recordBusy||store.recordingBusy||recordingFinishing||(!recordingActive&&!anyFramesReady)" :title="!auth.can('review')?'当前账号没有录制权限':recordingFinishing?'正在结束录像并写入文件':!recordingActive&&!anyFramesReady?'等待摄像机提供画面':recordingActive?'结束当前录像':'开始保存录像'"><Circle :size="16" :fill="status.recording?'currentColor':'none'"/>{{recordingLabel}}</button><button class="btn primary" @click="toggleDetection" :disabled="!auth.can('review')||store.detectionBusy||(!status.detecting&&!anyFramesReady)" :title="!auth.can('review')?'当前账号没有监测控制权限':!status.detecting&&!anyFramesReady?'等待摄像机提供画面':'切换监测服务状态'"><component :is="status.detecting?Pause:Play" :size="17"/>{{store.detectionBusy?'正在处理…':status.detecting?'暂停监测':'开始监测'}}</button></div></div>
<div v-if="snapshotResults.length" class="snapshot-results" role="status"><span>本次抓拍</span><template v-for="result in snapshotResults" :key="result.cameraId"><button v-if="result.saved&&result.downloadUrl" class="btn" :disabled="snapshotDownloadBusy===result.cameraId" @click="downloadSnapshot(result)"><Download :size="16"/>下载 {{result.cameraId==='camera1'?'C1':'C2'}} 抓拍</button><span v-else class="muted">{{result.cameraId==='camera1'?'C1':'C2'}}：{{result.error||(result.queued?'等待文件写入确认':'未保存')}}</span></template><button class="icon-button" aria-label="收起抓拍结果" @click="snapshotResults=[]"><X :size="16"/></button></div>
<div class="monitor-grid"><div class="panel space-panel" ref="card3dRef"><div class="panel-heading"><h3>空间态势</h3><div class="space-actions"><button class="view-switch" @click="relativeView=!relativeView" :title="relativeView?'查看校园底图':'查看真实米制坐标'"><component :is="relativeView?Map:Crosshair" :size="16"/>{{relativeView?'校园视图':'相对定位'}}</button><div class="mode-toggle"><button :class="{selected:mapMode==='2d'}" :aria-pressed="mapMode==='2d'" @click="changeMode('2d')">{{relativeView?'2D':'平面'}}</button><button :class="{selected:mapMode==='3d'}" :aria-pressed="mapMode==='3d'" @click="changeMode('3d')">{{relativeView?'3D':'透视'}}</button></div><button class="icon-button" title="全屏空间视图" @click="enterNativeFullscreen('3d')"><Maximize :size="17"/></button></div></div>
<div class="target-strip"><strong v-if="primaryTarget" class="badge blue target-id">{{primaryTarget.id}}</strong><span class="badge" :class="status.detecting?'success':'neutral'"><i/>{{detectionLabel}}</span><template v-if="primaryTarget"><span class="target-field">距离 <b>{{primaryTarget.rangeMeters.toFixed(1)}}</b> m</span><span class="target-field">相对高度 <b>{{primaryTarget.y.toFixed(1)}}</b> m</span></template><span v-else class="target-placeholder">暂无定位目标</span><span class="target-count">{{totalTargets}} 个目标</span></div>
<div class="space-canvas"><SpatialScene v-if="relativeView" ref="spatialSceneRef" :detecting="status.detecting" :spatial="status.spatial"/><CampusMap v-else ref="mapRef"/></div></div>
<div class="panel camera-column"><div class="camera-wrapper" v-for="id in cameraIds" :key="id" :ref="el=>setCameraRef(el,id)"><CameraPanel :ref="panel=>setCameraPanelRef(id,panel)" :camera-id="id" :status="status.cameras[id]" :display-fps="getRealFps(id)" :bridge="status"/><button class="camera-fullscreen icon-button" @click="enterNativeFullscreen(id)" :aria-label="(id==='camera1'?'C1':'C2')+' 全屏视频'" title="切换视频全屏"><Maximize :size="17"/></button></div></div></div>
<div class="monitor-bottom"><LiveTimeline :cameras="status.cameras"/><section class="panel pending-monitor"><div class="panel-heading"><h3>待复核事件 <span>{{pendingCount}}</span></h3><RouterLink to="/events" class="pending-more">查看全部 <ChevronRight :size="15"/></RouterLink></div><div v-if="pending" class="monitor-event"><div class="monitor-event-info"><strong><span class="badge blue">{{pending.trackId}}</span>目标检测</strong><time><CalendarDays :size="18"/>{{dateTime(pending.startedAt).replaceAll('/','-')}}</time></div><RouterLink :to="'/events?id='+pending.id" class="btn primary">{{auth.can('review')?'立即复核':'查看事件'}}</RouterLink></div><div v-else class="monitor-no-events"><CircleCheck :size="35" :stroke-width="1.4"/><div><strong>暂无待复核事件</strong><p>新的监测事件将自动同步至此</p></div></div></section></div>
</section></template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, nextTick, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { Camera, Circle, Maximize, Play, Pause, Layers, Map, CircleCheck, ChevronRight, ScanLine, Crosshair, CalendarDays, Download, X } from 'lucide-vue-next';
import { useCameraStore } from '@/stores/camera';
import { useAuthStore } from '@/stores/auth';
import { cameraBridge } from '@/api/camera-bridge';
import SpatialScene from '@/components/SpatialScene.vue';
import CampusMap from '@/components/CampusMap.vue';
import LiveTimeline from '@/components/LiveTimeline.vue';
import { http } from '@/api/http';
import { dateTime, type ProductEvent } from '@/lib/product-format';
import CameraPanel from '@/components/CameraPanel.vue';
import type { CameraId, SnapshotResult } from '@/types/camera-types';
import { downloadFile } from '@/lib/download-file';

const store = useCameraStore();
const auth = useAuthStore();
const { status } = storeToRefs(store);
const cameraIds: CameraId[] = ['camera1', 'camera2'];
const recordBusy = ref(false);
const mapRef = ref<InstanceType<typeof CampusMap> | null>(null);
const relativeView = ref(false);
const mapMode = ref<'2d'|'3d'>('3d');
const pending = ref<ProductEvent | null>(null);
const pendingCount = ref(0);
let pendingTimer: ReturnType<typeof setInterval>;
const primaryTarget = computed(() => status.value.detecting ? status.value.spatial.targets[0] : undefined);
function changeMode(mode:'2d'|'3d'){mapMode.value=mode;mapRef.value?.switchMode(mode);spatialSceneRef.value?.switchMode(mode)}
async function loadPending(){try{const{data}=await http.get('/overview');if(!cancelled){pending.value=data.pendingEvent||null;pendingCount.value=data.pendingEvents||0}}catch{ /* Camera monitoring remains available when the event index reconnects. */ }}
const snapshotBusy = ref(false);
const snapshotResults = ref<SnapshotResult[]>([]),snapshotDownloadBusy=ref('');
async function downloadSnapshot(result:SnapshotResult){if(!result.downloadUrl)return;snapshotDownloadBusy.value=result.cameraId;try{await downloadFile(result.downloadUrl,result.name)}catch(e){store.toast((e as Error).message,'error')}finally{snapshotDownloadBusy.value=''}}

const card3dRef = ref<HTMLElement | null>(null);
const spatialSceneRef = ref<InstanceType<typeof SpatialScene> | null>(null);
const camera1Ref = ref<HTMLElement | null>(null);
const camera2Ref = ref<HTMLElement | null>(null);
const cameraPanels: Partial<Record<CameraId, InstanceType<typeof CameraPanel>>> = {};
const waitingForPair = ref(false);

function setCameraPanelRef(id: CameraId, panel: unknown) {
  if (panel) cameraPanels[id] = panel as InstanceType<typeof CameraPanel>;
  else delete cameraPanels[id];
}

const setCameraRef = (el: unknown, id: CameraId) => {
  if (id === 'camera1') camera1Ref.value = el as HTMLElement | null;
  if (id === 'camera2') camera2Ref.value = el as HTMLElement | null;
};

watch(relativeView, async () => { await nextTick(); changeMode(mapMode.value); });

const bothFramesReady = computed(() => cameraIds.every((id) => status.value.cameras[id].frameAvailable&&status.value.cameras[id].state==='streaming'));
const anyFramesReady = computed(() => cameraIds.some(id=>status.value.cameras[id].frameAvailable&&status.value.cameras[id].state==='streaming'));
const recordingStates=computed(()=>cameraIds.map(id=>status.value.cameras[id].recordingState));
const recordingActive=computed(()=>recordingStates.value.some(state=>['armed','starting','recording','stopping'].includes(state)));
const recordingFinishing=computed(()=>recordingStates.value.includes('stopping'));
const recordingLabel=computed(()=>recordBusy.value||store.recordingBusy?'正在处理…':recordingFinishing.value?'正在保存…':status.value.recording?'停止录制':recordingActive.value?'取消录制':'录制');
const detectionLabel=computed(()=>!status.value.detecting?'监测未启动':status.value.engineState==='ready'?'监测中':status.value.serviceState==='error'?'推理异常':'监测准备中');
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

async function toggleRecording() {
  if (!auth.can('review') || recordBusy.value || recordingFinishing.value || (!recordingActive.value&&!anyFramesReady.value)) return;
  recordBusy.value = true;
  try { await store.toggleRecording(); } finally { recordBusy.value = false; }
}

async function captureBoth() {
  if (!auth.can('export') || !bothFramesReady.value || snapshotBusy.value) return;
  snapshotBusy.value = true;
  try { snapshotResults.value = await store.captureBoth(); } finally { snapshotBusy.value = false; }
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
  void loadPending();
  pendingTimer = setInterval(loadPending, 5000);
  document.addEventListener('fullscreenchange', handleFullscreenChange);
});

onUnmounted(() => {
  cancelled = true;
  clearInterval(pendingTimer);
  previewRequest?.abort();
  document.removeEventListener('fullscreenchange', handleFullscreenChange);
});
</script>
<style scoped>
.snapshot-results{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 14px;margin-bottom:10px;border:1px solid #cddced;border-radius:8px;background:#f7faff;font-size:14px}.snapshot-results>.icon-button{margin-left:auto}.snapshot-results .btn{font-size:14px;min-height:34px}
.monitor-page>.page-heading .actions{gap:15px}.monitor-page>.page-heading .actions .btn{font-size:18px;min-width:104px;min-height:40px}.monitor-page>.page-heading .actions .btn:last-child{min-width:138px}.monitor-grid{display:grid;grid-template-columns:minmax(0,1.435fr) minmax(360px,1fr);gap:8px;height:574px;min-height:440px}.space-panel{position:relative;display:flex;flex-direction:column;overflow:hidden;padding:0 12px 12px}.space-panel .panel-heading{padding:10px 4px 9px;min-height:53px;height:53px}.space-panel h3{margin:0;font-size:24px;font-weight:700}.space-actions{display:flex;align-items:center;gap:9px}.view-switch{display:flex;align-items:center;gap:5px;background:none;border:0;color:#8192ad;font-size:12px;padding:5px}.view-switch:hover{color:var(--blue)}.mode-toggle{position:absolute;right:24px;top:123px;z-index:3;display:flex;border:3px solid #fff;border-radius:8px;height:35px;overflow:hidden;box-shadow:0 2px 6px #253a5210}.mode-toggle button{border:0;background:#fff;color:#536783;padding:3px 13px;font-size:16px;font-weight:500}.mode-toggle button.selected{background:var(--blue);color:#fff;border-radius:5px}.space-actions .icon-button{width:28px;height:28px}.target-strip{display:flex;align-items:center;gap:17px;height:46px;flex-shrink:0;border:1px solid #e4eaf4;background:#f7f9fd;border-radius:7px;margin-bottom:10px;padding:0 12px;font-size:17px;color:#18253c}.target-strip>.badge{font-size:16px;padding:4px 10px}.target-strip>.target-id{min-width:80px;justify-content:center;font-size:18px}.target-strip strong{font-size:18px;font-weight:600}.target-strip b{color:#172033;font-weight:600}.target-field{border-left:1px solid #b9c8df;padding-left:20px;white-space:nowrap}.target-count{font-size:13px;margin-left:auto;color:#9aa8bb;white-space:nowrap}.target-placeholder{font-size:16px;color:#74839b}.space-canvas{flex:1;position:relative;min-height:0;border-radius:7px;overflow:hidden}.camera-column{display:grid;grid-template-rows:1fr 1fr;gap:14px;min-height:0;padding:11px 12px}.camera-wrapper{position:relative;min-height:0;overflow:hidden;border-radius:10px;border:1px solid #d6deeb}.camera-fullscreen{position:absolute;z-index:5;bottom:6px;right:10px;color:white;width:30px;height:30px}.camera-fullscreen svg{width:23px;height:23px}.camera-fullscreen:hover{background:#ffffff25;color:#fff}.monitor-bottom{display:grid;grid-template-columns:minmax(0,2.18fr) minmax(310px,1fr);gap:10px;margin-top:10px;height:206px}.pending-monitor{overflow:hidden}.pending-monitor .panel-heading{height:65px;padding:15px 20px}.pending-monitor h3{font-size:19px;font-weight:700}.pending-monitor h3>span{font-size:17px;color:#59667a;margin-left:6px;font-weight:600}.pending-more{font-size:13px;color:#6d7b92;display:flex;align-items:center;white-space:nowrap}.monitor-event{display:flex;align-items:center;gap:14px;margin:0 16px;padding:15px 13px;background:#fbfcff;border:1px solid #d4deee;border-radius:9px;min-height:108px}.monitor-event-info{min-width:0;flex:1}.monitor-event-info strong{font-size:17px;font-weight:600;white-space:nowrap;display:flex;align-items:center;gap:13px}.monitor-event-info .badge{font-size:17px;padding:3px 9px}.monitor-event-info time{display:flex;gap:12px;align-items:center;font-size:15px;color:#35445e;margin-top:15px;white-space:nowrap}.monitor-event>.btn{margin-left:auto;font-size:16px;min-height:36px;padding:5px 13px}.monitor-no-events{display:flex;align-items:center;justify-content:center;gap:17px;padding:35px 20px;color:#a2b2c6}.monitor-no-events strong{font-size:17px;color:#72859e;font-weight:500}.monitor-no-events p{margin:10px 0 0;font-size:14px;color:#9aa9bd}.space-panel:fullscreen,.camera-wrapper:fullscreen{width:100vw;height:100vh;background:#fff}.space-panel:fullscreen .space-canvas{height:100%}
@media(min-height:1000px) and (min-width:1300px){.monitor-grid{height:calc(100dvh - 368px)}}@media(max-width:1350px){.target-placeholder,.target-count{display:none}.monitor-event{gap:10px}.monitor-event-info strong{font-size:15px}.monitor-event-info time{font-size:12px}.monitor-event>.btn{font-size:14px;padding:5px 9px}.monitor-event-info p{font-size:12px}.space-actions{gap:4px}.monitor-grid{grid-template-columns:minmax(0,1.3fr) minmax(330px,1fr)}}@media(max-width:1100px){.monitor-grid{grid-template-columns:1fr;height:auto;min-height:0}.space-panel{height:520px}.camera-column{grid-template-columns:1fr 1fr;grid-template-rows:260px}.monitor-bottom{grid-template-columns:1fr;height:auto}.pending-monitor{min-height:200px}.monitor-bottom>.live-timeline{height:206px}.monitor-event>.btn{position:static}.monitor-event{min-height:120px}}@media(max-width:700px){.camera-column{grid-template-columns:1fr;grid-template-rows:240px 240px}.space-panel{height:430px;padding:0 8px 8px}.target-strip{gap:10px;padding:0 8px;font-size:12px}.target-strip strong{font-size:14px}.target-strip>.badge{font-size:11px}.view-switch{font-size:0}.space-panel .panel-heading h3{font-size:20px}.monitor-page .actions{width:100%;justify-content:flex-end}.mode-toggle button{padding:3px 8px}}
</style>
