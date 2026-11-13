<template>
  <div :class="sceneMode === '2d' ? 'spatial-scene spatial-scene--2d' : 'spatial-scene'">
    <canvas ref="canvasRef" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="onPointerUp"
      @pointercancel="onPointerUp" @contextmenu.prevent @wheel.prevent="onWheel" aria-label="米制空间定位场景" />
    <div class="spatial-localization-status" :class="{ 'needs-calibration': spatial.state === 'uncalibrated', 'has-error': spatial.state === 'error' }" role="status">
      <strong>{{ statusLabel }}</strong>
      <span>{{ spatial.message }}</span>
      <span v-if="skewNotice">{{ skewNotice }}</span>
      <span v-if="!detecting">等待启动无人机检测。</span>
    </div>
    <button type="button" class="spatial-reset" @click="reset">重置视角</button>
    <div class="spatial-scene-readout">
      <span v-for="[id, point] in cameraEntries" :key="id">{{ cameraLabel(id, point) }} ({{ point.x.toFixed(3) }}, {{ point.y.toFixed(3) }}, {{ point.z.toFixed(3) }}) m · 原点距 {{ originDistance(point) }} m</span>
      <span>无人机三维坐标 (X, Y, Z) · 方位左为正 · 仰角上为正 · 网格 {{ gridStep.toFixed(gridStep < 1 ? 1 : 0) }} m</span>
    </div>
    <label class="spatial-campus-toggle"><input v-model="showCampus" type="checkbox" /> 建筑参考层（未配准）</label>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import type { SpatialPoint, SpatialStatus, SpatialTarget } from '@/types/camera-types';
import { drawCampusBuildings, drawCampusBuildingsPlan } from '@/lib/campus-buildings';
import { cameraLabel, originDistance } from '@/lib/camera-position';

type ProjectedPoint = { x: number; y: number; depth: number };
type SceneMode = '3d' | '2d';
type Limits = { xMin: number; xMax: number; yMin: number; yMax: number; zMin: number; zMax: number };
type Transition<T> = { from: T; to: T; start: number };
const transitionMs = 160;
const defaultLimits: Limits = { xMin: -2, xMax: 2, yMin: -1, yMax: 2, zMin: -1, zMax: 3 };
const limitKeys = Object.keys(defaultLimits) as (keyof Limits)[];
const props = defineProps<{ detecting: boolean; spatial: SpatialStatus }>();
const cameraEntries = computed(() => ['uncalibrated','error'].includes(props.spatial.state) ? [] : Object.entries(props.spatial.cameras));
const canvasRef = ref<HTMLCanvasElement | null>(null);
const sceneMode = ref<SceneMode>('3d');
const showCampus = ref(false);
const initialView = () => ({ yaw: -0.72, pitch: 0.56, zoom: 1, panX: 0, panY: 0 });
const view = ref(initialView());
const dragRef = ref<{ x: number; y: number; mode: 'rotate' | 'pan'; yaw: number; pitch: number; panX: number; panY: number } | null>(null);

// World points come exclusively from the calibrated backend, never from a display-only estimate.
const targets = computed(() => props.detecting && props.spatial.state === 'ready'
  ? props.spatial.targets.filter(point => [point.x, point.y, point.z].every(Number.isFinite)) : []);
const statusLabel = computed(() => ({
  uncalibrated: '需要相机标定', ready: '米制定位有效', waiting_for_frames: '等待两路检测帧',
  frame_time_gap: '双路帧时差超限', no_targets: '等待双路无人机', no_valid_matches: '暂无可靠定位', error: '定位服务异常',
})[props.spatial.state]);
const skewNotice = computed(() => {
  if (props.spatial.state !== 'frame_time_gap') return '';
  const skew = Number(props.spatial.diagnostics.frameSkewMs);
  const threshold = Number(props.spatial.diagnostics.maxFrameSkewMs);
  if (!Number.isFinite(skew)) return '';
  return Number.isFinite(threshold)
    ? `本次双路帧估计时差 ${skew.toFixed(1)} ms，阈值 ${threshold.toFixed(0)} ms。`
    : `本次双路帧估计时差 ${skew.toFixed(1)} ms。`;
});
const bounds = shallowRef<Limits>(defaultLimits);
let boundMotion: Transition<Limits> | null = null;
let movements = new Map<string, Transition<SpatialPoint> & { target: SpatialTarget }>();
let drawRequest: number | null = null;
const progress = (start: number, now: number) => Math.max(0, Math.min(1, (now - start) / transitionMs));
const lerp = (a: number, b: number, ratio: number) => a + (b - a) * ratio;
const span = (limits: Limits) => Math.max(limits.xMax - limits.xMin, limits.yMax - limits.yMin, limits.zMax - limits.zMin);

function pointAt(motion: Transition<SpatialPoint>, now: number): SpatialPoint {
  const { from, to, start } = motion;
  const t = progress(start, now);
  return { x: lerp(from.x, to.x, t), y: lerp(from.y, to.y, t), z: lerp(from.z, to.z, t) };
}

function boundsAt(now: number): Limits {
  if (!boundMotion) return bounds.value;
  const { from, to, start } = boundMotion;
  const t = progress(start, now);
  return Object.fromEntries(limitKeys.map(key => [key, lerp(from[key], to[key], t)])) as Limits;
}

function updateBounds(now: number, reset = false) {
  const next = { ...(reset ? defaultLimits : bounds.value) };
  for (const point of [...Object.values(props.spatial.cameras), ...targets.value]) {
    for (const axis of ['x', 'y', 'z'] as const) {
      const min = `${axis}Min` as const;
      const max = `${axis}Max` as const;
      if (point[axis] < next[min]) next[min] = point[axis] - Math.max(1, Math.abs(point[axis]) * 0.2);
      if (point[axis] > next[max]) next[max] = point[axis] + Math.max(1, Math.abs(point[axis]) * 0.2);
    }
  }
  if (limitKeys.every(key => next[key] === bounds.value[key])) return;
  boundMotion = { from: boundsAt(now), to: next, start: now };
  bounds.value = next;
}

function updateTargets() {
  const now = performance.now();
  movements = new Map(targets.value.map(target => {
    const old = movements.get(target.id);
    const unchanged = old && old.to.x === target.x && old.to.y === target.y && old.to.z === target.z;
    const movement = unchanged
      ? { ...old, target }
      : { from: old ? pointAt(old, now) : target, to: target, start: now, target };
    return [target.id, movement];
  }));
  updateBounds(now, !props.detecting);
  scheduleDraw();
}

const gridStep = computed(() => {
  const desired = span(bounds.value) / 10;
  const power = 10 ** Math.floor(Math.log10(desired));
  return [1, 2, 5, 10].map(value => value * power).find(value => value >= desired) ?? 1;
});

function line(ctx: CanvasRenderingContext2D, a: ProjectedPoint, b: ProjectedPoint, color: string, lineWidth = 1) {
  ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.strokeStyle = color; ctx.lineWidth = lineWidth; ctx.stroke();
}
function arrow(ctx: CanvasRenderingContext2D, a: ProjectedPoint, b: ProjectedPoint, color: string, label: string) {
  line(ctx, a, b, color, 2);
  const angle = Math.atan2(b.y - a.y, b.x - a.x);
  ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(b.x, b.y);
  ctx.lineTo(b.x - 7 * Math.cos(angle - 0.48), b.y - 7 * Math.sin(angle - 0.48));
  ctx.lineTo(b.x - 7 * Math.cos(angle + 0.48), b.y - 7 * Math.sin(angle + 0.48));
  ctx.closePath(); ctx.fill(); ctx.font = '600 13px "Yuntong Sans", sans-serif'; ctx.fillText(label, b.x + 7, b.y - 7);
}

function scheduleDraw() {
  if (drawRequest !== null) return;
  drawRequest = requestAnimationFrame(now => {
    drawRequest = null;
    draw(now);
    if (boundMotion && progress(boundMotion.start, now) === 1) boundMotion = null;
    if (boundMotion || [...movements.values()].some(motion => motion.from !== motion.to && progress(motion.start, now) < 1)) {
      scheduleDraw();
    }
  });
}

function draw(now: number) {
  const canvas = canvasRef.value; if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width)); const height = Math.max(1, Math.round(rect.height));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pixelsWide = Math.round(width * dpr);
  const pixelsHigh = Math.round(height * dpr);
  if (canvas.width !== pixelsWide) canvas.width = pixelsWide;
  if (canvas.height !== pixelsHigh) canvas.height = pixelsHigh;
  const ctx = canvas.getContext('2d'); if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const bg = ctx.createLinearGradient(0, 0, 0, height); bg.addColorStop(0, '#f5f8fc'); bg.addColorStop(1, '#edf3f9');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, width, height);
  const b = boundsAt(now);
  const rawProject = (pt: SpatialPoint): ProjectedPoint => {
    if (sceneMode.value === '2d') return { x: -pt.x, y: -pt.z, depth: pt.y };
    const cy = Math.cos(view.value.yaw); const sy = Math.sin(view.value.yaw);
    const x = pt.x * cy - pt.z * sy; const z = pt.x * sy + pt.z * cy;
    return { x, y: z * Math.sin(view.value.pitch) - pt.y * Math.cos(view.value.pitch), depth: z * Math.cos(view.value.pitch) + pt.y * Math.sin(view.value.pitch) };
  };
  const fittingPoints = [b.xMin, b.xMax].flatMap(x =>
    [b.yMin, b.yMax].flatMap(y => [b.zMin, b.zMax].map(z => rawProject({ x, y, z }))));
  const xs = fittingPoints.map(point => point.x); const ys = fittingPoints.map(point => point.y);
  const minX = Math.min(...xs); const maxX = Math.max(...xs); const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const scale = Math.min(Math.max(80, width - 150) / Math.max(1, maxX - minX), Math.max(80, height - 220) / Math.max(1, maxY - minY)) * view.value.zoom;
  const p = (pt: SpatialPoint): ProjectedPoint => {
    const raw = rawProject(pt);
    return { x: width / 2 + (raw.x - (minX + maxX) / 2) * scale + view.value.panX,
      y: height / 2 + 10 + (raw.y - (minY + maxY) / 2) * scale + view.value.panY, depth: raw.depth };
  };
  const step = gridStep.value;
  for (let x = Math.ceil(b.xMin / step) * step; x <= b.xMax; x += step) {
    line(ctx, p({ x, y: 0, z: b.zMin }), p({ x, y: 0, z: b.zMax }), Math.abs(x) < step / 2 ? '#a6b8c8' : '#d0dbe5');
    if (Math.abs(x) > step / 2) { const tick = p({ x, y: 0, z: 0 }); ctx.fillStyle = '#6d8195'; ctx.font = '500 12px "Yuntong Sans", sans-serif'; ctx.fillText(x.toFixed(step < 1 ? 1 : 0), tick.x + 3, tick.y + 12); }
  }
  for (let z = Math.ceil(b.zMin / step) * step; z <= b.zMax; z += step) {
    line(ctx, p({ x: b.xMin, y: 0, z }), p({ x: b.xMax, y: 0, z }), Math.abs(z) < step / 2 ? '#a6b8c8' : '#d0dbe5');
    if (Math.abs(z) > step / 2) { const tick = p({ x: 0, y: 0, z }); ctx.fillStyle = '#6d8195'; ctx.font = '500 12px "Yuntong Sans", sans-serif'; ctx.fillText(z.toFixed(step < 1 ? 1 : 0), tick.x + 4, tick.y - 3); }
  }
  if (showCampus.value) {
    if (sceneMode.value === '2d') drawCampusBuildingsPlan(ctx, p); else drawCampusBuildings(ctx, p);
  }
  const origin = p({ x: 0, y: 0, z: 0 }); const axisLength = Math.max(0.5, span(b) / 5);
  ctx.fillStyle = '#40566d'; ctx.font = '600 13px "Yuntong Sans", sans-serif'; ctx.fillText('O (0,0,0)', origin.x + 7, origin.y + 16);
  arrow(ctx, origin, p({ x: Math.min(axisLength, b.xMax), y: 0, z: 0 }), '#d24f55', 'X 左 / m');
  arrow(ctx, origin, p({ x: 0, y: 0, z: Math.min(axisLength, b.zMax) }), '#3979b8', 'Z 前 / m');
  if (sceneMode.value === '3d') arrow(ctx, origin, p({ x: 0, y: b.yMax, z: 0 }), '#3b9a69', 'Y 上 / m');
  cameraEntries.value.forEach(([id, point]) => {
    const projected = p(point); const left = point.x > 0; const color = id === 'camera2' ? '#c27a36' : '#3979b8';
    const labelX = projected.x + (left ? -110 : 18); const labelY = projected.y - (left ? 28 : 12);
    line(ctx, projected, { x: labelX + (left ? 85 : 0), y: labelY + 2, depth: 0 }, color);
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(projected.x, projected.y, 4, 0, Math.PI * 2); ctx.fill();
    ctx.font = '600 13px "Yuntong Sans", sans-serif'; ctx.fillText(cameraLabel(id, point), labelX, labelY);
    ctx.font = '500 12px "Yuntong Sans", sans-serif'; ctx.fillText(`原点距 ${originDistance(point)} m`, labelX, labelY + 18);
  });
  movements.forEach(motion => {
    const { x, y, z } = pointAt(motion, now);
    const projected = p({ x, y, z });
    const held = motion.target.trackingState === 'held';
    const color = held ? '#718398' : '#1d9b72';
    line(ctx, origin, projected, color + '66');
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(projected.x, projected.y, 6, 0, Math.PI * 2); ctx.fill();
    ctx.font = '600 13px "Yuntong Sans", sans-serif'; ctx.fillStyle = '#24364c';
    const distance = Math.hypot(x, y, z);
    const azimuth = Math.atan2(x, z) * 180 / Math.PI;
    const elevation = Math.atan2(y, Math.hypot(x, z)) * 180 / Math.PI;
    const label = `${motion.target.label || 'drone'}${held ? '（短时保持）' : ''}  (${x.toFixed(1)}, ${y.toFixed(1)}, ${z.toFixed(1)}) m`;
    const detail = `距离 ≈ ${distance.toFixed(1)} m · 方位 ${azimuth.toFixed(1)}° · 仰角 ${elevation.toFixed(1)}°`;
    const textWidth = Math.max(ctx.measureText(label).width, ctx.measureText(detail).width);
    const textX = Math.max(8, Math.min(projected.x + 10, width - textWidth - 8));
    const textY = Math.max(105, projected.y - 10);
    ctx.fillText(label, textX, textY);
    ctx.fillText(detail, textX, textY + 18);
  });
}

function pointerDown(event: PointerEvent) {
  event.preventDefault(); (event.currentTarget as HTMLCanvasElement).setPointerCapture(event.pointerId);
  dragRef.value = { x: event.clientX, y: event.clientY,
    mode: sceneMode.value === '2d' || event.button === 2 || (event.buttons & 3) === 3 ? 'pan' : 'rotate', ...view.value };
}
function pointerMove(event: PointerEvent) {
  const drag = dragRef.value; if (!drag || !event.buttons) return;
  if (drag.mode === 'pan') { view.value.panX = drag.panX + event.clientX - drag.x; view.value.panY = drag.panY + event.clientY - drag.y; }
  else { view.value.yaw = drag.yaw + (event.clientX - drag.x) * 0.012; view.value.pitch = Math.max(-1.5, Math.min(1.5, drag.pitch + (event.clientY - drag.y) * 0.009)); }
}
function onPointerUp() { dragRef.value = null; }
function onWheel(event: WheelEvent) { view.value.zoom = Math.max(0.2, Math.min(12, view.value.zoom * (event.deltaY < 0 ? 1.08 : 0.92))); }
function reset() { dragRef.value = null; view.value = initialView(); updateBounds(performance.now(), true); }
function switchMode(mode: SceneMode) { sceneMode.value = mode; reset(); }
defineExpose({ sceneMode, switchMode });
let observer: ResizeObserver | null = null;
onMounted(() => {
  if (canvasRef.value) {
    observer = new ResizeObserver(scheduleDraw);
    observer.observe(canvasRef.value);
  }
  updateTargets();
  void document.fonts.ready.then(() => { if (canvasRef.value) scheduleDraw(); });
});
onUnmounted(() => {
  observer?.disconnect();
  if (drawRequest !== null) cancelAnimationFrame(drawRequest);
});
watch(() => [targets.value, props.spatial.cameras, props.detecting], updateTargets);
watch(() => [view.value, sceneMode.value, showCampus.value], scheduleDraw, { deep: true });
</script>

<style scoped src="@/styles/spatial-scene.css"></style>
