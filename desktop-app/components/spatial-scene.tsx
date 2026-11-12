import { useEffect, useRef, useState } from 'react';
import type { SpatialPoint, SpatialStatus, SpatialTarget } from '@/lib/camera-types';
import { drawCampusBuildings, drawCampusBuildingsPlan } from '@/lib/campus-buildings';
import './spatial-scene.css';

type Point3 = SpatialPoint;
type SceneMode = '3d' | '2d';
type SpatialSceneProps = {
  spatial?: SpatialStatus;
  detecting: boolean;
};
type Motion<T> = { from: T; to: T; start: number };
const transitionMs = 160;
const progress = (motion: Motion<unknown>, now: number) => Math.max(0, Math.min(1, (now - motion.start) / transitionMs));
const lerp = (a: number, b: number, ratio: number) => a + (b - a) * ratio;
const emptyTargets: SpatialTarget[] = [];
const emptyCameras: SpatialStatus['cameras'] = {};
const INITIAL_YAW = -0.72;
const INITIAL_PITCH = 0.56;
const MIN_PITCH = -Math.PI / 2;
const MAX_PITCH = Math.PI / 2;

function initialView(mode: SceneMode) {
  return { yaw: mode === '3d' ? INITIAL_YAW : 0, pitch: mode === '3d' ? INITIAL_PITCH : 0, zoom: 1, panX: 0, panY: 0 };
}

function clampPitch(pitch: number): number {
  return Math.max(MIN_PITCH, Math.min(MAX_PITCH, pitch));
}

function pointAt(motion: Motion<Point3>, now: number): Point3 {
  const { from, to } = motion;
  const t = progress(motion, now);
  return { x: lerp(from.x, to.x, t), y: lerp(from.y, to.y, t), z: lerp(from.z, to.z, t) };
}

function project(
  point: Point3,
  width: number,
  height: number,
  yaw: number,
  pitch: number,
  zoom: number,
  extent: number,
  panX = 0,
  panY = 0,
) {
  const cy = Math.cos(yaw); const sy = Math.sin(yaw);
  const x = point.x * cy - point.z * sy; const rotatedZ = point.x * sy + point.z * cy;
  // At yaw/pitch zero this projects along -Y onto X/Z; increasing pitch
  // tilts the view toward the horizon. Keep the existing 3D projection.
  const cp = Math.cos(pitch); const sp = Math.sin(pitch);
  const y = point.y * sp - rotatedZ * cp;
  const depth = point.y * cp + rotatedZ * sp;
  // Keep scale independent of signed depth. Perspective division becomes
  // unstable when the camera is flipped through ±90° and points cross the
  // view plane; orthographic projection preserves the scene geometry.
  const scale = Math.max(10, Math.min(width - 72, height - 136)) * 0.27 * zoom / extent;
  return { x: width / 2 + x * scale + panX, y: height * 0.53 - y * scale + panY, depth };
}

function projectPlan(point: Point3, width: number, height: number, zoom: number, extent: number, panX = 0, panY = 0) {
  const scale = Math.max(10, Math.min(width - 64, height - 180)) * zoom / (2 * extent);
  return { x: width / 2 - point.x * scale + panX, y: height / 2 - 14 - point.z * scale + panY, depth: point.y };
}

function line(context: CanvasRenderingContext2D, a: ReturnType<typeof project>, b: ReturnType<typeof project>, color: string, width = 1) {
  context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.strokeStyle = color; context.lineWidth = width; context.stroke();
}

function arrow(context: CanvasRenderingContext2D, a: ReturnType<typeof project>, b: ReturnType<typeof project>, color: string, label: string) {
  line(context, a, b, color, 2.4);
  const angle = Math.atan2(b.y - a.y, b.x - a.x);
  const size = 8;
  context.fillStyle = color;
  context.beginPath();
  context.moveTo(b.x, b.y);
  context.lineTo(b.x - size * Math.cos(angle - 0.48), b.y - size * Math.sin(angle - 0.48));
  context.lineTo(b.x - size * Math.cos(angle + 0.48), b.y - size * Math.sin(angle + 0.48));
  context.closePath();
  context.fill();
  context.font = '800 11px Inter, sans-serif';
  context.fillText(label, b.x + 7, b.y - 7);
}

export default function SpatialScene({ spatial, detecting }: SpatialSceneProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragRef = useRef<{
    x: number;
    y: number;
    mode: 'rotate' | 'pan';
    yaw: number;
    pitch: number;
    panX: number;
    panY: number;
  } | null>(null);
  const [sceneMode, setSceneMode] = useState<SceneMode>('3d');
  const [view, setView] = useState(initialView('3d'));
  const targets = detecting && spatial?.state === 'ready' ? spatial.targets : emptyTargets;
  const cameras = spatial?.cameras ?? emptyCameras;
  const motions = useRef(new Map<string, Motion<Point3>>());
  const fit = useRef<Motion<number>>({ from: 6, to: 6, start: 0 });

  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return undefined;
    const started = performance.now();
    motions.current = new Map(targets.map(target => {
      const old = motions.current.get(target.id);
      const unchanged = old && old.to.x === target.x && old.to.y === target.y && old.to.z === target.z;
      return [target.id, unchanged ? old : {
        from: old ? pointAt(old, started) : target, to: target, start: started,
      }];
    }));
    const farthest = Math.max(6, ...[...Object.values(cameras), ...targets].map(point =>
      Math.max(Math.abs(point.x), Math.abs(point.y), Math.abs(point.z))));
    const desiredExtent = Math.ceil(farthest / 6) * 6;
    if (desiredExtent > fit.current.to || (!detecting && desiredExtent !== fit.current.to)) {
      const current = lerp(fit.current.from, fit.current.to, progress(fit.current, started));
      fit.current = { from: current, to: desiredExtent, start: started };
    }
    const draw = (now: number) => {
      const rect = canvas.getBoundingClientRect(); const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(rect.width)); const height = Math.max(1, Math.round(rect.height));
      const pixelsWide = Math.round(width * dpr);
      const pixelsHigh = Math.round(height * dpr);
      if (canvas.width !== pixelsWide) canvas.width = pixelsWide;
      if (canvas.height !== pixelsHigh) canvas.height = pixelsHigh;
      const context = canvas.getContext('2d'); if (!context) return;
      context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, width, height);
      const bg = context.createLinearGradient(0, 0, 0, height); bg.addColorStop(0, '#f5f8fb'); bg.addColorStop(1, '#e6edf4'); context.fillStyle = bg; context.fillRect(0, 0, width, height);
      const extent = lerp(fit.current.from, fit.current.to, progress(fit.current, now));
      const p = (point: Point3) => sceneMode === '2d'
        ? projectPlan(point, width, height, view.zoom, extent, view.panX, view.panY)
        : project(point, width, height, view.yaw, view.pitch, view.zoom, extent, view.panX, view.panY);
      const origin = p({ x: 0, y: 0, z: 0 });
      // Ground grid keeps the full signed X/Z range.
      const step = Math.max(1, Math.round(extent / 6));
      for (let i = -6 * step; i <= 6 * step; i += step) {
        line(context, p({ x: i, y: 0, z: -extent }), p({ x: i, y: 0, z: extent }), i === 0 ? '#9eb4c8' : '#d0dbe5');
        line(context, p({ x: -extent, y: 0, z: i }), p({ x: extent, y: 0, z: i }), i === 0 ? '#9eb4c8' : '#d0dbe5');
      }
      if (sceneMode === '3d') {
        // Vertical reference planes extend only above the ground (Y >= 0).
        for (let level = 0; level <= extent; level += step) {
          line(context, p({ x: 0, y: level, z: -extent }), p({ x: 0, y: level, z: extent }), level === 0 ? '#a9becf' : '#d6e0e8');
          line(context, p({ x: -extent, y: level, z: 0 }), p({ x: extent, y: level, z: 0 }), level === 0 ? '#b0c3d2' : '#dce5ec');
        }
        for (let coordinate = -6 * step; coordinate <= 6 * step; coordinate += step) {
          line(context, p({ x: 0, y: 0, z: coordinate }), p({ x: 0, y: extent, z: coordinate }), coordinate === 0 ? '#a9becf' : '#d6e0e8');
          line(context, p({ x: coordinate, y: 0, z: 0 }), p({ x: coordinate, y: extent, z: 0 }), coordinate === 0 ? '#b0c3d2' : '#dce5ec');
        }
      }
      const room = [{ x: -extent, y: 0, z: -extent }, { x: extent, y: 0, z: -extent }, { x: extent, y: 0, z: extent }, { x: -extent, y: 0, z: extent }];
      for (let i = 0; i < room.length; i += 1) line(context, p(room[i]), p(room[(i + 1) % room.length]), '#829bb2', 1.5);
      if (sceneMode === '2d') drawCampusBuildingsPlan(context, p);
      else drawCampusBuildings(context, p);
      // Fixed world axes. They remain tied to the scene origin while the camera view rotates.
      context.fillStyle = '#40566d'; context.font = '800 11px Inter, sans-serif';
      context.fillText('O', origin.x + 7, origin.y + 14);
      arrow(context, origin, p({ x: extent / 2, y: 0, z: 0 }), '#d24f55', 'X');
      if (sceneMode === '3d') arrow(context, origin, p({ x: 0, y: extent / 2, z: 0 }), '#3b9a69', 'Y');
      arrow(context, origin, p({ x: 0, y: 0, z: extent * 2 / 3 }), '#3979b8', 'Z');
      context.fillStyle = '#6d8195'; context.font = '600 9px Inter, sans-serif';
      for (let x = step; x <= extent; x += step) { const tick = p({ x, y: 0, z: 0 }); context.fillText(String(x), tick.x + 3, tick.y + 13); }
      for (let z = step; z <= extent; z += step) { const tick = p({ x: 0, y: 0, z }); context.fillText(String(z), tick.x + 4, tick.y + 3); }
      if (sceneMode === '3d') {
        for (let y = step; y <= extent; y += step) {
          const tick = p({ x: 0, y, z: 0 });
          context.fillText(String(y), tick.x + 5, tick.y - 3);
        }
      }
      for (const [id, point] of Object.entries(cameras)) {
        const projected = p(point);
        context.fillStyle = id === 'camera1' ? '#3979b8' : '#c27a36';
        context.beginPath();
        context.arc(projected.x, projected.y, 5, 0, Math.PI * 2);
        context.fill();
        context.font = '700 10px Inter, sans-serif';
        context.fillText(id === 'camera1' ? 'C1' : 'C2', projected.x + 8, projected.y - 6);
      }
      for (const target of targets) {
        const motion = motions.current.get(target.id);
        const { x, y, z } = motion ? pointAt(motion, now) : target;
        const projected = p({ x, y, z });
        const held = target.trackingState === 'held';
        const color = held ? '#8b98a7' : '#1d9b72';
        line(context, origin, projected, color + '66');
        context.fillStyle = color;
        context.shadowColor = color + '99';
        context.shadowBlur = 12;
        context.beginPath();
        context.arc(projected.x, projected.y, 7, 0, Math.PI * 2);
        context.fill();
        context.shadowBlur = 0;
        context.fillStyle = '#24364c';
        context.font = '700 10px Inter, sans-serif';
        const azimuth = Math.atan2(x, z) * 180 / Math.PI;
        const elevation = Math.atan2(y, Math.hypot(x, z)) * 180 / Math.PI;
        const name = `${target.label}${held ? '（短时保持）' : ''}  (${x.toFixed(1)}, ${y.toFixed(1)}, ${z.toFixed(1)}) m`;
        const detail = `距离 ≈ ${Math.hypot(x, y, z).toFixed(1)} m · 方位 ${azimuth.toFixed(1)}° · 仰角 ${elevation.toFixed(1)}°`;
        const textWidth = Math.max(context.measureText(name).width, context.measureText(detail).width);
        const textX = Math.max(8, Math.min(projected.x + 10, width - textWidth - 8));
        context.fillText(name, textX, projected.y - 8);
        context.fillText(detail, textX, projected.y + 5);
      }
    };
    let animation = 0;
    const render = (now: number) => {
      draw(now);
      if (progress(fit.current, now) < 1 || [...motions.current.values()].some(motion => progress(motion, now) < 1)) {
        animation = requestAnimationFrame(render);
      }
    };
    animation = requestAnimationFrame(render);
    const observer = new ResizeObserver(() => draw(performance.now()));
    observer.observe(canvas);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(animation);
    };
  }, [cameras, detecting, targets, view, sceneMode]);

  const pointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const pan = sceneMode === '2d' || (event.buttons & 3) === 3;
    dragRef.current = {
      x: event.clientX, y: event.clientY, mode: pan ? 'pan' : 'rotate',
      yaw: view.yaw, pitch: view.pitch, panX: view.panX, panY: view.panY,
    };
  };
  const pointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const bothButtons = (event.buttons & 3) === 3;
    let drag = dragRef.current;
    if (sceneMode === '2d') {
      if (!drag) return;
      if (!event.buttons) { dragRef.current = null; return; }
      setView((previous) => ({ ...previous, panX: drag!.panX + event.clientX - drag!.x, panY: drag!.panY + event.clientY - drag!.y }));
      return;
    }
    if (bothButtons && (!drag || drag.mode !== 'pan')) {
      // If the second mouse button is pressed after a rotation starts, switch
      // modes at the current cursor position to avoid a visible jump.
      drag = {
        x: event.clientX, y: event.clientY, mode: 'pan',
        yaw: view.yaw, pitch: view.pitch, panX: view.panX, panY: view.panY,
      };
      dragRef.current = drag;
    }
    if (!drag) return;
    if (bothButtons && drag.mode === 'pan') {
      setView((previous) => ({
        ...previous,
        panX: drag!.panX + (event.clientX - drag!.x),
        panY: drag!.panY + (event.clientY - drag!.y),
      }));
      return;
    }
    if (drag.mode === 'pan') {
      // One button was released. Stop the pan gesture without rotating.
      dragRef.current = null;
      return;
    }
    setView((previous) => ({
      ...previous,
      yaw: drag!.yaw + (event.clientX - drag!.x) * 0.012,
      pitch: clampPitch(drag!.pitch + (event.clientY - drag!.y) * 0.009),
    }));
  };
  const reset = () => { dragRef.current = null; setView(initialView(sceneMode)); };
  const switchMode = (mode: SceneMode) => {
    if (mode === sceneMode) return;
    dragRef.current = null;
    setSceneMode(mode);
    setView(initialView(mode));
  };
  return <div className={sceneMode === '2d' ? 'spatial-scene spatial-scene--2d' : 'spatial-scene'}>
    <canvas ref={canvasRef} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={() => { dragRef.current = null; }} onPointerCancel={() => { dragRef.current = null; }} onContextMenu={(event) => event.preventDefault()} onWheel={(event) => { event.preventDefault(); setView((previous) => ({ ...previous, zoom: Math.max(.65, Math.min(1.8, previous.zoom * (event.deltaY < 0 ? 1.08 : .92))) })); }} aria-label={sceneMode === '2d' ? '可平移的二维平面' : '可旋转、可平移的三维空间'} />
    <div className="spatial-scene-title"><strong>{sceneMode === '2d' ? '2D 平面定位' : '3D 空间定位'}</strong><span>{detecting ? '实时更新' : '等待检测启动'}</span></div>
    <fieldset className="spatial-mode-switch" aria-label="定位视图切换">
      <button type="button" aria-pressed={sceneMode === '3d'} onClick={() => switchMode('3d')}>3D</button>
      <button type="button" aria-pressed={sceneMode === '2d'} onClick={() => switchMode('2d')}>2D</button>
    </fieldset>
    <div className="spatial-scene-hint">{sceneMode === '2d' ? '拖动平移 · 滚轮缩放' : '左键旋转 · 左右键拖动平移 · 滚轮缩放'}</div><button type="button" className="spatial-reset" onClick={reset}>重置视角</button>
    <div className="spatial-scene-legend"><span><i className="axis-x" /> X 左</span>{sceneMode === '3d' && <span><i className="axis-y" /> Y 上</span>}<span><i className="axis-z" /> Z 前</span><span><i className="target" /> 无人机</span></div>
    <div className="spatial-scene-readout">
      {detecting ? spatial?.message || '等待双路无人机检测…' : '等待启动无人机检测。'}
    </div>
  </div>;
}
