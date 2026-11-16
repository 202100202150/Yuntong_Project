<template>
  <div ref="container" class="campus-scene" :class="{ panning: drag }"
    :data-mode="sceneMode" :data-zoom="view.zoom" :data-offset-x="view.offsetX" :data-offset-y="view.offsetY" :data-transitioning="transitioning"
    @wheel.prevent="onWheel" @pointerdown="startPan" @pointermove="pan" @pointerup="endPan" @pointercancel="endPan" @lostpointercapture="endPan">
    <div ref="surface" class="campus-surface" :style="surfaceStyle" tabindex="0" role="img"
      :aria-label="'校园' + (sceneMode === '2d' ? '平面' : '透视') + '视图。拖动平移，滚轮缩放；键盘方向键平移，加减号缩放，Home 重置。'" @keydown="onKeydown">
      <div class="campus-image-layer" data-layer="perspective" :data-opacity="sceneMode === '3d' ? 1 : 0" :class="{ active: sceneMode === '3d' }" :aria-hidden="sceneMode !== '3d'" @transitionend="finishTransition">
        <img class="campus-image" :src="perspectiveImage" alt="校园透视图" draggable="false" />
      </div>
      <div class="campus-image-layer" data-layer="plan" :data-opacity="sceneMode === '2d' ? 1 : 0" :class="{ active: sceneMode === '2d' }" :aria-hidden="sceneMode !== '2d'" @transitionend="finishTransition">
        <img class="campus-image" :src="planImage" alt="校园平面图" draggable="false" />
      </div>
    </div>
    <div class="campus-map-note" title="校园场景尚未完成测量配准。真实目标请在相对定位视图查看。" @pointerdown.stop><MapPin :size="14" /> 场景定位待配置</div>
    <div class="map-controls" @pointerdown.stop>
      <button aria-label="放大地图" title="放大" :disabled="view.zoom >= MAX_ZOOM" @click="zoomBy(.2)"><Plus :size="19" /></button>
      <button aria-label="缩小地图" title="缩小" :disabled="view.zoom <= MIN_ZOOM" @click="zoomBy(-.2)"><Minus :size="19" /></button>
      <button aria-label="重置地图" title="重置视图" @click="reset"><LocateFixed :size="19" /></button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { Plus, Minus, LocateFixed, MapPin } from 'lucide-vue-next';
import perspectiveImage from '@/assets/campus-map.png';
import planImage from '@/assets/campus-plan-v6.png';

type SceneMode = '2d' | '3d';
const MIN_ZOOM = 1;
const MAX_ZOOM = 2.8;
const sceneMode = ref<SceneMode>('3d');
// Shared image viewport coordinates. Offsets are CSS pixels, not world units.
const view = reactive({ zoom: 1, offsetX: 0, offsetY: 0 });
const viewport = reactive({ width: 1, height: 1 });
const container = ref<HTMLDivElement | null>(null);
const surface = ref<HTMLDivElement | null>(null);
const drag = ref(false);
const transitioning = ref(false);
const surfaceStyle = computed(() => ({ transform: `translate(${view.offsetX}px, ${view.offsetY}px) scale(${view.zoom})` }));
let pointer = -1;
let lastPointer: { x: number; y: number } | null = null;
let resizeObserver: ResizeObserver | null = null;
let transitionTimer: ReturnType<typeof setTimeout> | undefined;

function stopTransition() {
  if (transitionTimer !== undefined) clearTimeout(transitionTimer);
  transitionTimer = undefined;
  transitioning.value = false;
}
function finishTransition(event: TransitionEvent) {
  if (event.propertyName === 'opacity' && event.target === event.currentTarget) stopTransition();
}
function switchMode(mode: SceneMode) {
  if (sceneMode.value === mode) return;
  stopTransition();
  endPan();
  sceneMode.value = mode;
  if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    transitioning.value = true;
    // transitionend normally settles this state; the timer also covers hidden tabs.
    transitionTimer = setTimeout(stopTransition, 250);
  }
}
function clampOffsets() {
  const limitX = viewport.width * (view.zoom - 1) / 2;
  const limitY = viewport.height * (view.zoom - 1) / 2;
  view.offsetX = Math.max(-limitX, Math.min(limitX, view.offsetX));
  view.offsetY = Math.max(-limitY, Math.min(limitY, view.offsetY));
}
function resize() {
  if (!container.value) return;
  const width = Math.max(1, container.value.clientWidth);
  const height = Math.max(1, container.value.clientHeight);
  // Preserve the center in the common image viewport when its layout resizes.
  view.offsetX *= width / viewport.width;
  view.offsetY *= height / viewport.height;
  viewport.width = width;
  viewport.height = height;
  clampOffsets();
}
function zoomBy(delta: number, anchor?: { x: number; y: number }) {
  const nextZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Math.round((view.zoom + delta) * 100) / 100));
  if (nextZoom === view.zoom) return;
  const ratio = nextZoom / view.zoom;
  const x = (anchor?.x ?? viewport.width / 2) - viewport.width / 2;
  const y = (anchor?.y ?? viewport.height / 2) - viewport.height / 2;
  view.offsetX = x - (x - view.offsetX) * ratio;
  view.offsetY = y - (y - view.offsetY) * ratio;
  view.zoom = nextZoom;
  clampOffsets();
}
function onWheel(event: WheelEvent) {
  if (!event.deltaY || !container.value) return;
  const rect = container.value.getBoundingClientRect();
  zoomBy(event.deltaY < 0 ? .12 : -.12, { x: event.clientX - rect.left, y: event.clientY - rect.top });
}
function reset() {
  endPan();
  view.zoom = 1;
  view.offsetX = 0;
  view.offsetY = 0;
}
function startPan(event: PointerEvent) {
  if (event.button !== 0 || pointer !== -1 || !container.value) return;
  pointer = event.pointerId;
  lastPointer = { x: event.clientX, y: event.clientY };
  drag.value = true;
  surface.value?.focus({ preventScroll: true });
  container.value.setPointerCapture(pointer);
}
function pan(event: PointerEvent) {
  if (!drag.value || event.pointerId !== pointer || !lastPointer) return;
  view.offsetX += event.clientX - lastPointer.x;
  view.offsetY += event.clientY - lastPointer.y;
  lastPointer = { x: event.clientX, y: event.clientY };
  clampOffsets();
}
function endPan(event?: PointerEvent) {
  if (event && event.pointerId !== pointer) return;
  const previousPointer = pointer;
  pointer = -1;
  drag.value = false;
  lastPointer = null;
  if (previousPointer !== -1 && container.value?.hasPointerCapture(previousPointer)) container.value.releasePointerCapture(previousPointer);
}
function onKeydown(event: KeyboardEvent) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(.2); }
  else if (event.key === '-') { event.preventDefault(); zoomBy(-.2); }
  else if (event.key === 'Home') { event.preventDefault(); reset(); }
  else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
    event.preventDefault();
    view.offsetX += event.key === 'ArrowLeft' ? 32 : event.key === 'ArrowRight' ? -32 : 0;
    view.offsetY += event.key === 'ArrowUp' ? 32 : event.key === 'ArrowDown' ? -32 : 0;
    clampOffsets();
  }
}
onMounted(() => {
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container.value!);
  window.addEventListener('resize', resize);
  resize();
});
onUnmounted(() => {
  endPan();
  stopTransition();
  resizeObserver?.disconnect();
  window.removeEventListener('resize', resize);
});
defineExpose({
  sceneMode, switchMode,
  getViewState: () => ({
    mode: sceneMode.value,
    zoom: view.zoom,
    offsetXPx: view.offsetX,
    offsetYPx: view.offsetY,
    viewportPx: { ...viewport },
    // Normalized within the shared viewport, before object-fit cropping.
    viewportCenterNormalized: {
      u: .5 - view.offsetX / (viewport.width * view.zoom),
      v: .5 - view.offsetY / (viewport.height * view.zoom),
    },
    transitioning: transitioning.value,
  }),
});
</script>

<style scoped>
.campus-scene{position:relative;width:100%;height:100%;overflow:hidden;background:#edf3f3;border-radius:7px;cursor:grab;touch-action:none;isolation:isolate}
.campus-scene.panning{cursor:grabbing}
.campus-surface{position:absolute;inset:0;width:100%;height:100%;outline:none;transform-origin:center center}
.campus-surface:focus-visible{outline:2px solid var(--blue);outline-offset:-3px;border-radius:7px}
.campus-image-layer{position:absolute;inset:0;width:100%;height:100%;opacity:0;transition:opacity 200ms ease;pointer-events:none}
.campus-image-layer.active{opacity:1}
.campus-image{display:block;width:100%;height:100%;object-fit:cover;filter:saturate(.58) brightness(1.07);opacity:.88;user-select:none;pointer-events:none}
.campus-map-note{position:absolute;bottom:13px;left:13px;display:flex;align-items:center;gap:6px;background:#ffffffed;border:1px solid #dee6ee;box-shadow:0 2px 8px #1e3b5f12;border-radius:5px;padding:6px 9px;font-size:12px;color:#64758a;cursor:default}
.map-controls{position:absolute;bottom:12px;right:12px;display:flex;flex-direction:column;border:0;border-radius:6px;background:transparent;filter:drop-shadow(0 2px 5px #23436c18)}
.map-controls button{border:1px solid #d4deea;border-bottom:0;width:39px;height:39px;background:#fff;color:#182234;display:grid;place-items:center}
.map-controls button svg{width:22px;height:22px;stroke-width:2.1}
.map-controls button:first-child{border-radius:6px 6px 0 0}
.map-controls button:nth-child(2){border-bottom:1px solid #d4deea;border-radius:0 0 6px 6px}
.map-controls button:last-child{margin-top:8px;border:1px solid #d4deea;border-radius:6px}
.map-controls button:hover:not(:disabled){background:#eef5ff;color:var(--blue)}
.map-controls button:disabled{color:#b1bdca;cursor:not-allowed}
@media(prefers-reduced-motion:reduce){.campus-image-layer{transition:none}}
</style>
