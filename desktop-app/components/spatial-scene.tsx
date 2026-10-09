import { useEffect, useMemo, useRef, useState } from 'react';
import type { CameraDetectedObject, CameraId, CameraMatchingDiagnostics, CameraMatchingReason } from '@/lib/camera-types';
import { groupSpatialTracks } from '@/lib/spatial-tracks';
import { drawCampusBuildings, drawCampusBuildingsPlan } from '@/lib/campus-buildings';
import './spatial-scene.css';

type Point3 = { x: number; y: number; z: number };
type SceneMode = '3d' | '2d';
type SpatialSceneProps = {
  objects: Record<CameraId, CameraDetectedObject[]>;
  detecting: boolean;
  matchingDiagnostics: CameraMatchingDiagnostics;
};
const baseline = 3;
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

function project(
  point: Point3,
  width: number,
  height: number,
  yaw: number,
  pitch: number,
  zoom: number,
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
  const scale = Math.max(10, Math.min(width, height) * 0.075 * zoom);
  return { x: width / 2 + x * scale + panX, y: height * 0.78 - y * scale + panY, depth };
}

function projectPlan(point: Point3, width: number, height: number, zoom: number, panX = 0, panY = 0) {
  // Look from +Y toward -Y, with +X right and +Z down. Height does not
  // affect the screen position. Leave room for the header and readout.
  const scale = Math.max(10, Math.min(width - 64, height - 180) / 12 * zoom);
  return { x: width / 2 + point.x * scale + panX, y: height / 2 - 14 + point.z * scale + panY, depth: point.y };
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

export default function SpatialScene({ objects, detecting, matchingDiagnostics }: SpatialSceneProps) {
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
  const tracks = useMemo(
    () => groupSpatialTracks(objects).map((track) => ({
      ...track,
      point: triangulate(track.left, track.right),
    })),
    [objects],
  );
  const matchingVersion = matchingDiagnostics.algorithmVersion === 'temporal-geometry-v3'
    ? 'v3' : matchingDiagnostics.algorithmVersion === 'temporal-window-v2'
      ? 'v2' : '版本未上报';
  const similarityDetail = matchingDiagnostics.bestSimilarity === null
    ? '' : ` · 最高综合分 ${matchingDiagnostics.bestSimilarity.toFixed(2)}`
      + (matchingDiagnostics.secondSimilarity === null
        ? '' : ` / 次高 ${matchingDiagnostics.secondSimilarity.toFixed(2)}`);
  const candidateDetail = matchingDiagnostics.camera1Id && matchingDiagnostics.camera2Id
    ? ` · 候选 ${matchingDiagnostics.camera1Id} ↔ ${matchingDiagnostics.camera2Id}` : '';
  const geometryDetail = matchingVersion === 'v3'
    ? ` · 位置辅助${matchingDiagnostics.geometryStatus === 'ready' ? '已启用' : '未就绪'}` : '';
  const blockingReasons: CameraMatchingReason[] = [
    'capture_time_gap', 'low_similarity', 'ambiguous', 'no_people', 'partial_match',
  ];
  const dominantBlocker = blockingReasons
    .map((reason) => ({ reason, count: matchingDiagnostics.reasonCounts[reason] ?? 0 }))
    .sort((a, b) => b.count - a.count)[0];
  const blockerDetail = dominantBlocker.count > 0
    ? ` · 近30秒主要阻碍 ${matchingReasonText[dominantBlocker.reason]} ${dominantBlocker.count}次` : '';
  const matchingDetail = detecting
    ? `跨镜 ${matchingVersion}：${matchingReasonText[matchingDiagnostics.reason]}`
      + similarityDetail + candidateDetail
      + (matchingDiagnostics.frameSkewMs === null
        ? '' : ` · 帧时差 ${matchingDiagnostics.frameSkewMs.toFixed(0)}ms`)
      + geometryDetail + blockerDetail
    : '';
  const reasonCountsTitle = blockingReasons
    .filter((reason) => (matchingDiagnostics.reasonCounts[reason] ?? 0) > 0)
    .map((reason) => `${matchingReasonText[reason]} ${matchingDiagnostics.reasonCounts[reason]}次`)
    .join('、') || '无';
  const recentFailureTitle = matchingDiagnostics.recentFailures.length
    ? matchingDiagnostics.recentFailures.map((failure, index) =>
      `${index + 1}. ${matchingReasonText[failure.reason]}；综合分 ${failure.bestSimilarity?.toFixed(3) ?? '无'} / ${failure.secondSimilarity?.toFixed(3) ?? '无'}`
      + `；候选 ${failure.camera1Id ?? '无'} ↔ ${failure.camera2Id ?? '无'}`
      + `；帧时差 ${failure.frameSkewMs?.toFixed(0) ?? '无'}ms；${(failure.ageMs / 1000).toFixed(1)}秒前`,
    ) : ['无'];
  const matchingTitle = [
    `算法 ${matchingDiagnostics.algorithmVersion}；${matchingReasonText[matchingDiagnostics.reason]}`,
    `最高综合分：${matchingDiagnostics.bestSimilarity?.toFixed(3) ?? '无'}；次高综合分：${matchingDiagnostics.secondSimilarity?.toFixed(3) ?? '无'}`,
    `候选：camera1/${matchingDiagnostics.camera1Id ?? '无'} ↔ camera2/${matchingDiagnostics.camera2Id ?? '无'}；比较次数：${matchingDiagnostics.comparisonCount}`,
    `帧时差：${matchingDiagnostics.frameSkewMs?.toFixed(0) ?? '无'}ms；待确认：${matchingDiagnostics.pendingPairs}；已确认：${matchingDiagnostics.confirmedPairs}`,
    `位置辅助：${matchingDiagnostics.geometryStatus === 'ready' ? '已启用' : '未就绪'}；可靠匹配点：${matchingDiagnostics.geometryInliers}`,
    `近30秒各阻碍：${reasonCountsTitle}`,
    '最近失败：',
    ...recentFailureTitle,
    '匹配不明确时两路目标分别显示；未做人脸识别或真实身高测量。',
  ].join('\n');

  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return undefined;
    const draw = () => {
      const rect = canvas.getBoundingClientRect(); const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(rect.width)); const height = Math.max(1, Math.round(rect.height));
      canvas.width = width * dpr; canvas.height = height * dpr;
      const context = canvas.getContext('2d'); if (!context) return;
      context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, width, height);
      const bg = context.createLinearGradient(0, 0, 0, height); bg.addColorStop(0, '#f5f8fb'); bg.addColorStop(1, '#e6edf4'); context.fillStyle = bg; context.fillRect(0, 0, width, height);
      const p = (point: Point3) => sceneMode === '2d'
        ? projectPlan(point, width, height, view.zoom, view.panX, view.panY)
        : project(point, width, height, view.yaw, view.pitch, view.zoom, view.panX, view.panY);
      const origin = p({ x: 0, y: 0, z: 0 });
      // Ground grid keeps the full signed X/Z range.
      const extent = 6;
      for (let i = -extent; i <= extent; i += 1) {
        line(context, p({ x: i, y: 0, z: -extent }), p({ x: i, y: 0, z: extent }), i === 0 ? '#9eb4c8' : '#d0dbe5');
        line(context, p({ x: -extent, y: 0, z: i }), p({ x: extent, y: 0, z: i }), i === 0 ? '#9eb4c8' : '#d0dbe5');
      }
      if (sceneMode === '3d') {
        // Vertical reference planes extend only above the ground (Y >= 0).
        for (let level = 0; level <= extent; level += 1) {
          line(context, p({ x: 0, y: level, z: -extent }), p({ x: 0, y: level, z: extent }), level === 0 ? '#a9becf' : '#d6e0e8');
          line(context, p({ x: -extent, y: level, z: 0 }), p({ x: extent, y: level, z: 0 }), level === 0 ? '#b0c3d2' : '#dce5ec');
        }
        for (let coordinate = -extent; coordinate <= extent; coordinate += 1) {
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
      arrow(context, origin, p({ x: 3, y: 0, z: 0 }), '#d24f55', 'X');
      if (sceneMode === '3d') arrow(context, origin, p({ x: 0, y: 3, z: 0 }), '#3b9a69', 'Y');
      arrow(context, origin, p({ x: 0, y: 0, z: 4 }), '#3979b8', 'Z');
      context.fillStyle = '#6d8195'; context.font = '600 9px Inter, sans-serif';
      for (let x = 1; x <= 5; x += 1) { const tick = p({ x, y: 0, z: 0 }); context.fillText(String(x), tick.x + 3, tick.y + 13); }
      for (let z = 1; z <= (sceneMode === '2d' ? extent : 10); z += 1) { const tick = p({ x: 0, y: 0, z }); context.fillText(String(z), tick.x + 4, tick.y + 3); }
      if (sceneMode === '3d') {
        for (let y = 1; y <= extent; y += 1) {
          const tick = p({ x: 0, y, z: 0 });
          context.fillText(String(y), tick.x + 5, tick.y - 3);
        }
      }
      for (const camera of [{ point: { x: -1.5, y: 1.8, z: 0 }, label: 'C1', color: '#3979b8' }, { point: { x: 1.5, y: 1.8, z: 0 }, label: 'C2', color: '#c27a36' }]) { const projected = p(camera.point); context.fillStyle = camera.color; context.beginPath(); context.arc(projected.x, projected.y, 5, 0, Math.PI * 2); context.fill(); context.font = '700 10px Inter, sans-serif'; context.fillText(camera.label, projected.x + 8, projected.y - 6); }
      for (const track of tracks) { const projected = p(track.point); const color = track.left && track.right ? '#1d9b72' : '#8b98a7'; line(context, origin, projected, color + '66'); context.fillStyle = color; context.shadowColor = color + '99'; context.shadowBlur = 12; context.beginPath(); context.arc(projected.x, projected.y, 7, 0, Math.PI * 2); context.fill(); context.shadowBlur = 0; context.fillStyle = '#24364c'; context.font = '700 10px Inter, sans-serif'; const coordinates = sceneMode === '2d' ? track.point.x.toFixed(1) + ', ' + track.point.z.toFixed(1) : track.point.x.toFixed(1) + ', ' + track.point.y.toFixed(1) + ', ' + track.point.z.toFixed(1); context.fillText(track.id + '  (' + coordinates + 'm)', projected.x + 11, projected.y - 7); }
    };
    draw(); const observer = new ResizeObserver(draw); observer.observe(canvas); return () => observer.disconnect();
  }, [tracks, view, sceneMode]);

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
    <div className="spatial-scene-legend"><span><i className="axis-x" /> X 右</span>{sceneMode === '3d' && <span><i className="axis-y" /> Y 上</span>}<span><i className="axis-z" /> {sceneMode === '2d' ? 'Z 下' : 'Z 深'}</span><span><i className="target" /> person</span></div>
    <div
      className="spatial-scene-readout"
      title={matchingTitle}
    >
      {tracks.length
        ? tracks.length + ' 个目标 · 双目匹配 ' + tracks.filter((track) => track.left && track.right).length
        : '等待双摄检测目标…'}
      {matchingDetail && <span className="spatial-matching-detail">{matchingDetail}</span>}
    </div>
  </div>;
}
