<template>
  <div :class="sceneMode === '2d' ? 'spatial-scene spatial-scene--2d' : 'spatial-scene'">
    <canvas
      ref="canvasRef"
      @pointerdown="pointerDown"
      @pointermove="pointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @contextmenu.prevent
      @wheel.prevent="onWheel"
      aria-label="空间定位场景"
    />
    <div class="spatial-scene-title">
      <strong>{{ sceneMode === '2d' ? '2D 平面定位' : '3D 空间定位' }}</strong>
      <span>{{ detecting ? '实时更新' : '等待检测启动' }}</span>
    </div>
    <!-- 组件内部的切换器依然保留，但在外部容器里被隐藏了 -->
    <fieldset class="spatial-mode-switch" aria-label="定位视图切换">
      <button type="button" :aria-pressed="sceneMode === '3d'" @click="switchMode('3d')">3D</button>
      <button type="button" :aria-pressed="sceneMode === '2d'" @click="switchMode('2d')">2D</button>
    </fieldset>
    <div class="spatial-scene-hint">
      {{ sceneMode === '2d' ? '拖动平移 · 滚轮缩放' : '左键旋转 · 左右键拖动平移 · 滚轮缩放' }}
    </div>
    <button type="button" class="spatial-reset" @click="reset">重置视角</button>
    <div class="spatial-scene-legend">
      <span><i class="axis-x" /> X 右</span>
      <span v-if="sceneMode === '3d'"><i class="axis-y" /> Y 上</span>
      <span><i class="axis-z" /> {{ sceneMode === '2d' ? 'Z 下' : 'Z 深' }}</span>
      <span><i class="target" /> person</span>
    </div>
    <div class="spatial-scene-readout" :title="matchingTitle">
      <span v-if="tracks.length">
        {{ tracks.length }} 个目标 · 双目匹配 {{ tracks.filter((track) => track.left && track.right).length }}
      </span>
      <span v-else>等待双摄检测目标…</span>
      <span v-if="matchingDetail" class="spatial-matching-detail">{{ matchingDetail }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import type { CameraDetectedObject, CameraId, CameraMatchingDiagnostics, CameraMatchingReason } from '@/types/camera-types';
import { drawCampusBuildings, drawCampusBuildingsPlan } from '@/lib/campus-buildings';
import { groupSpatialTracks } from '@/lib/spatial-tracks';

type Point3 = { x: number; y: number; z: number };
type ProjectedPoint = { x: number; y: number; depth: number };
type SceneMode = '3d' | '2d';

const props = defineProps<{
  objects: Record<CameraId, CameraDetectedObject[]>;
  detecting: boolean;
  matchingDiagnostics: CameraMatchingDiagnostics;
}>();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const sceneMode = ref<SceneMode>('3d');
const view = ref({ yaw: -0.72, pitch: 0.56, zoom: 1, panX: 0, panY: 0 });

const dragRef = ref<{
  x: number; y: number; mode: 'rotate' | 'pan'; yaw: number; pitch: number; panX: number; panY: number;
} | null>(null);

const baseline = 3;
const INITIAL_YAW = -0.72;
const INITIAL_PITCH = 0.56;
const MIN_PITCH = -Math.PI / 2;
const MAX_PITCH = Math.PI / 2;

const initialView = (mode: SceneMode) => ({
  yaw: mode === '3d' ? INITIAL_YAW : 0,
  pitch: mode === '3d' ? INITIAL_PITCH : 0,
  zoom: 1,
  panX: 0,
  panY: 0,
});

const clampPitch = (pitch: number) => Math.max(MIN_PITCH, Math.min(MAX_PITCH, pitch));

const matchingReasonText: Record<CameraMatchingDiagnostics['reason'], string> = {
  waiting_for_frames: '等待双路识别',
  capture_time_gap: '两路帧时差过大',
  no_people: '有一路未识别到人',
  low_similarity: '匹配分数不足',
  ambiguous: '候选不唯一',
  confirming: '等待跨镜确认',
  partial_match: '仅部分目标已配对',
  matched: '已配对',
};

// 原版三维坐标计算
function triangulate(left?: CameraDetectedObject, right?: CameraDetectedObject): Point3 {
  const leftU = left ? left.bbox.x + left.bbox.width / 2 : 0.5;
  const rightU = right ? right.bbox.x + right.bbox.width / 2 : leftU;
  const disparity = right && left ? rightU - leftU : 0;
  const depth = disparity < -0.004 || disparity > 0.004 ? Math.min(16, Math.max(1.2, Math.abs(baseline / disparity) * 0.42)) : 5;
  const x = ((leftU + rightU) / 2 - 0.5) * depth * 1.8;
  const top = left?.bbox.y ?? right?.bbox.y ?? 0.5;
  const height = left?.bbox.height ?? right?.bbox.height ?? 0.3;
  return { x, y: Math.max(0.2, 2.6 - (top + height) * 3.1), z: depth };
}

// 原版 3D 正交投影
function project(point: Point3, width: number, height: number, yaw: number, pitch: number, zoom: number, panX = 0, panY = 0): ProjectedPoint {
  const cy = Math.cos(yaw); const sy = Math.sin(yaw);
  const x = point.x * cy - point.z * sy; const rotatedZ = point.x * sy + point.z * cy;
  const cp = Math.cos(pitch); const sp = Math.sin(pitch);
  const y = point.y * sp - rotatedZ * cp;
  const depth = point.y * cp + rotatedZ * sp;
  const scale = Math.max(10, Math.min(width, height) * 0.075 * zoom);
  return { x: width / 2 + x * scale + panX, y: height * 0.62 - y * scale + panY, depth };
}

// 原版 2D 投影
function projectPlan(point: Point3, width: number, height: number, zoom: number, panX = 0, panY = 0): ProjectedPoint {
  const scale = Math.max(10, Math.min(width - 64, height - 180) / 12 * zoom);
  return { x: width / 2 + point.x * scale + panX, y: height / 2 - 14 + point.z * scale + panY, depth: point.y };
}

function line(ctx: CanvasRenderingContext2D, a: ProjectedPoint, b: ProjectedPoint, color: string, lineWidth = 1) {
  ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.strokeStyle = color; ctx.lineWidth = lineWidth; ctx.stroke();
}

function arrow(ctx: CanvasRenderingContext2D, a: ProjectedPoint, b: ProjectedPoint, color: string, label: string) {
  line(ctx, a, b, color, 2.4);
  const angle = Math.atan2(b.y - a.y, b.x - a.x);
  const size = 8;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(b.x, b.y);
  ctx.lineTo(b.x - size * Math.cos(angle - 0.48), b.y - size * Math.sin(angle - 0.48));
  ctx.lineTo(b.x - size * Math.cos(angle + 0.48), b.y - size * Math.sin(angle + 0.48));
  ctx.closePath(); ctx.fill();
  ctx.font = '800 11px Inter, sans-serif';
  ctx.fillText(label, b.x + 7, b.y - 7);
}

// 使用原版的 groupSpatialTracks 算法
const tracks = computed(() => groupSpatialTracks(props.objects).map(track => ({
  ...track,
  point: triangulate(track.left, track.right),
})));

const matchingVersion = computed(() => props.matchingDiagnostics.algorithmVersion === 'temporal-geometry-v3' ? 'v3' : props.matchingDiagnostics.algorithmVersion === 'temporal-window-v2' ? 'v2' : '版本未上报');
const similarityDetail = computed(() => props.matchingDiagnostics.bestSimilarity === null ? '' : ` · 最高综合分 ${props.matchingDiagnostics.bestSimilarity.toFixed(2)}` + (props.matchingDiagnostics.secondSimilarity === null ? '' : ` / 次高 ${props.matchingDiagnostics.secondSimilarity.toFixed(2)}`));
const candidateDetail = computed(() => props.matchingDiagnostics.camera1Id && props.matchingDiagnostics.camera2Id ? ` · 候选 ${props.matchingDiagnostics.camera1Id} ↔ ${props.matchingDiagnostics.camera2Id}` : '');
const geometryDetail = computed(() => matchingVersion.value === 'v3' ? ` · 位置辅助${props.matchingDiagnostics.geometryStatus === 'ready' ? '已启用' : '未就绪'}` : '');

const blockingReasons: CameraMatchingReason[] = ['capture_time_gap', 'low_similarity', 'ambiguous', 'no_people', 'partial_match'];
const dominantBlocker = computed(() => blockingReasons.map((reason) => ({ reason, count: props.matchingDiagnostics.reasonCounts[reason] ?? 0 })).sort((a, b) => b.count - a.count)[0]);
const blockerDetail = computed(() => dominantBlocker.value.count > 0 ? ` · 近30秒主要阻碍 ${matchingReasonText[dominantBlocker.value.reason]} ${dominantBlocker.value.count}次` : '');

const matchingDetail = computed(() => props.detecting ? `跨镜 ${matchingVersion.value}：${matchingReasonText[props.matchingDiagnostics.reason]}` + similarityDetail.value + candidateDetail.value + (props.matchingDiagnostics.frameSkewMs === null ? '' : ` · 帧时差 ${props.matchingDiagnostics.frameSkewMs.toFixed(0)}ms`) + geometryDetail.value + blockerDetail.value : '');

const matchingTitle = computed(() => [
  `算法 ${props.matchingDiagnostics.algorithmVersion}；${matchingReasonText[props.matchingDiagnostics.reason]}`,
  `最高综合分：${props.matchingDiagnostics.bestSimilarity?.toFixed(3) ?? '无'}；次高综合分：${props.matchingDiagnostics.secondSimilarity?.toFixed(3) ?? '无'}`,
  `候选：camera1/${props.matchingDiagnostics.camera1Id ?? '无'} ↔ camera2/${props.matchingDiagnostics.camera2Id ?? '无'}；比较次数：${props.matchingDiagnostics.comparisonCount}`,
  `帧时差：${props.matchingDiagnostics.frameSkewMs?.toFixed(0) ?? '无'}ms；待确认：${props.matchingDiagnostics.pendingPairs}；已确认：${props.matchingDiagnostics.confirmedPairs}`,
  `位置辅助：${props.matchingDiagnostics.geometryStatus === 'ready' ? '已启用' : '未就绪'}；可靠匹配点：${props.matchingDiagnostics.geometryInliers}`,
  '匹配不明确时两路目标分别显示；未做人脸识别或真实身高测量。'
].join('\n'));

const draw = () => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  canvas.width = width * dpr; canvas.height = height * dpr;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);

  // 👇 核心修改：改为米黄色渐变背景
  const bg = ctx.createLinearGradient(0, 0, 0, height);
  bg.addColorStop(0, '#fffdfa'); // 几乎纯白，带一点点暖意
  bg.addColorStop(1, '#f9f6ef'); // 极淡的奶油米色
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);

  const p = (pt: Point3) => sceneMode.value === '2d'
    ? projectPlan(pt, width, height, view.value.zoom, view.value.panX, view.value.panY)
    : project(pt, width, height, view.value.yaw, view.value.pitch, view.value.zoom, view.value.panX, view.value.panY);

  const origin = p({ x: 0, y: 0, z: 0 });
  const extent = 6;

  // 1. 地面网格
  for (let i = -extent; i <= extent; i += 1) {
    line(ctx, p({ x: i, y: 0, z: -extent }), p({ x: i, y: 0, z: extent }), i === 0 ? '#9eb4c8' : '#d0dbe5');
    line(ctx, p({ x: -extent, y: 0, z: i }), p({ x: extent, y: 0, z: i }), i === 0 ? '#9eb4c8' : '#d0dbe5');
  }

  // 2. 3D 竖直参考面
  if (sceneMode.value === '3d') {
    for (let level = 0; level <= extent; level += 1) {
      line(ctx, p({ x: 0, y: level, z: -extent }), p({ x: 0, y: level, z: extent }), level === 0 ? '#a9becf' : '#d6e0e8');
      line(ctx, p({ x: -extent, y: level, z: 0 }), p({ x: extent, y: level, z: 0 }), level === 0 ? '#b0c3d2' : '#dce5ec');
    }
    for (let coordinate = -extent; coordinate <= extent; coordinate += 1) {
      line(ctx, p({ x: 0, y: 0, z: coordinate }), p({ x: 0, y: extent, z: coordinate }), coordinate === 0 ? '#a9becf' : '#d6e0e8');
      line(ctx, p({ x: coordinate, y: 0, z: 0 }), p({ x: coordinate, y: extent, z: 0 }), coordinate === 0 ? '#b0c3d2' : '#dce5ec');
    }
  }

  // 3. 空间边界框
  const room = [{ x: -extent, y: 0, z: -extent }, { x: extent, y: 0, z: -extent }, { x: extent, y: 0, z: extent }, { x: -extent, y: 0, z: extent }];
  for (let i = 0; i < room.length; i += 1) line(ctx, p(room[i]), p(room[(i + 1) % room.length]), '#829bb2', 1.5);

  // 4. 原版校园建筑群
  if (sceneMode.value === '2d') {
    drawCampusBuildingsPlan(ctx, p);
  } else {
    drawCampusBuildings(ctx, p);
  }

  // 5. 坐标轴
  ctx.fillStyle = '#40566d'; ctx.font = '800 11px Inter, sans-serif';
  ctx.fillText('O', origin.x + 7, origin.y + 14);
  arrow(ctx, origin, p({ x: 3, y: 0, z: 0 }), '#d24f55', 'X');
  if (sceneMode.value === '3d') arrow(ctx, origin, p({ x: 0, y: 3, z: 0 }), '#3b9a69', 'Y');
  arrow(ctx, origin, p({ x: 0, y: 0, z: 4 }), '#3979b8', 'Z');

  // 6. 刻度
  ctx.fillStyle = '#6d8195'; ctx.font = '600 9px Inter, sans-serif';
  for (let x = 1; x <= 5; x += 1) { const tick = p({ x, y: 0, z: 0 }); ctx.fillText(String(x), tick.x + 3, tick.y + 13); }
  for (let z = 1; z <= (sceneMode.value === '2d' ? extent : 10); z += 1) { const tick = p({ x: 0, y: 0, z }); ctx.fillText(String(z), tick.x + 4, tick.y + 3); }
  if (sceneMode.value === '3d') {
    for (let y = 1; y <= extent; y += 1) { const tick = p({ x: 0, y, z: 0 }); ctx.fillText(String(y), tick.x + 5, tick.y - 3); }
  }

  // 7. 摄像头位置
  const cameras = [{ point: { x: -1.5, y: 1.8, z: 0 }, label: 'C1', color: '#3979b8' }, { point: { x: 1.5, y: 1.8, z: 0 }, label: 'C2', color: '#c27a36' }];
  cameras.forEach(camera => {
    const projected = p(camera.point); ctx.fillStyle = camera.color;
    ctx.beginPath(); ctx.arc(projected.x, projected.y, 5, 0, Math.PI * 2); ctx.fill();
    ctx.font = '700 10px Inter, sans-serif'; ctx.fillText(camera.label, projected.x + 8, projected.y - 6);
  });

  // 8. 目标人物
  tracks.value.forEach(track => {
    const projected = p(track.point);
    const color = track.left && track.right ? '#1d9b72' : '#8b98a7';
    line(ctx, origin, projected, color + '66');
    ctx.fillStyle = color;
    ctx.shadowColor = color + '99'; ctx.shadowBlur = 12;
    ctx.beginPath(); ctx.arc(projected.x, projected.y, 7, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = '#24364c'; ctx.font = '700 10px Inter, sans-serif';
    const coordinates = sceneMode.value === '2d'
      ? track.point.x.toFixed(1) + ', ' + track.point.z.toFixed(1)
      : track.point.x.toFixed(1) + ', ' + track.point.y.toFixed(1) + ', ' + track.point.z.toFixed(1);
    ctx.fillText(track.id + '  (' + coordinates + 'm)', projected.x + 11, projected.y - 7);
  });
};

function pointerDown(event: PointerEvent) {
  event.preventDefault();
  (event.currentTarget as HTMLCanvasElement).setPointerCapture(event.pointerId);
  const pan = sceneMode.value === '2d' || (event.buttons & 3) === 3;
  dragRef.value = {
    x: event.clientX, y: event.clientY, mode: pan ? 'pan' : 'rotate',
    yaw: view.value.yaw, pitch: view.value.pitch, panX: view.value.panX, panY: view.value.panY,
  };
}

function pointerMove(event: PointerEvent) {
  const bothButtons = (event.buttons & 3) === 3;
  let drag = dragRef.value;

  if (sceneMode.value === '2d') {
    if (!drag) return;
    if (!event.buttons) { dragRef.value = null; return; }
    view.value.panX = drag.panX + event.clientX - drag.x;
    view.value.panY = drag.panY + event.clientY - drag.y;
    return;
  }

  if (bothButtons && (!drag || drag.mode !== 'pan')) {
    drag = {
      x: event.clientX, y: event.clientY, mode: 'pan',
      yaw: view.value.yaw, pitch: view.value.pitch, panX: view.value.panX, panY: view.value.panY,
    };
    dragRef.value = drag;
  }

  if (!drag) return;

  if (bothButtons && drag.mode === 'pan') {
    view.value.panX = drag.panX + (event.clientX - drag.x);
    view.value.panY = drag.panY + (event.clientY - drag.y);
    return;
  }

  if (drag.mode === 'pan') {
    dragRef.value = null;
    return;
  }

  view.value.yaw = drag.yaw + (event.clientX - drag.x) * 0.012;
  view.value.pitch = clampPitch(drag.pitch + (event.clientY - drag.y) * 0.009);
}

function onPointerUp() { dragRef.value = null; }

function onWheel(event: WheelEvent) {
  event.preventDefault();
  view.value.zoom = Math.max(0.65, Math.min(1.8, view.value.zoom * (event.deltaY < 0 ? 1.08 : 0.92)));
}

function reset() {
  dragRef.value = null;
  view.value = initialView(sceneMode.value);
}

function switchMode(mode: SceneMode) {
  if (mode === sceneMode.value) return;
  dragRef.value = null;
  sceneMode.value = mode;
  view.value = initialView(mode);
}

// 👇 暴露给父组件调用
defineExpose({ sceneMode, switchMode });

let observer: ResizeObserver | null = null;
onMounted(() => {
  if (canvasRef.value) {
    observer = new ResizeObserver(draw);
    observer.observe(canvasRef.value);
  }
  draw();
});
onUnmounted(() => observer?.disconnect());

watch(() => [props.objects, view.value, sceneMode.value, props.detecting], draw, { deep: true });
</script>

<style scoped src="@/styles/spatial-scene.css"></style>