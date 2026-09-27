'use client';
import { useEffect, useRef, useState } from 'react';
import { LocateFixed, Minus, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  objectStyles,
  projectPoint,
  type DisplayTrack,
  type Vec3,
} from '@/lib/airspace';

type Props = {
  tracks: DisplayTrack[];
  selected: string;
  onSelect: (id: string) => void;
  topDown: boolean;
  trails: boolean;
  uncertainty: boolean;
  campus: boolean;
  focus: string | null;
  focusRevision: number;
  demo: boolean;
};
const footprints = [
  [-200, -180, 70, 120, 35],
  [-60, -170, 140, 60, 50],
  [100, -180, 80, 120, 35],
  [-190, 20, 90, 160, 38],
  [30, 50, 150, 60, 65],
  [210, 20, 60, 180, 30],
  [-40, 200, 160, 70, 25],
];
export default function AirspaceView({
  tracks,
  selected,
  onSelect,
  topDown,
  trails,
  uncertainty,
  campus,
  focus,
  focusRevision,
  demo,
}: Props) {
  const [yaw, setYaw] = useState(0.74),
    [pitch, setPitch] = useState(0.61),
    [zoom, setZoom] = useState(1);
  const [center, setCenter] = useState<Vec3>([0, 0, 0]);
  const svg = useRef<SVGSVGElement>(null);
  const [viewWidth, setViewWidth] = useState(820);
  const drag = useRef<{ x: number; y: number; id: number } | null>(null);
  const scale = 0.55 * zoom * Math.min(viewWidth / 820, 1.2);
  const viewPitch = topDown ? Math.PI / 2 : pitch;
  const project = (v: Vec3): Vec3 => {
    const p = projectPoint(
      topDown ? [v[0], v[1], 0] : v,
      yaw,
      viewPitch,
      scale,
      topDown ? [center[0], center[1], 0] : center,
    );
    return [p[0] + (viewWidth - 820) / 2, p[1], p[2]];
  };
  const path = (points: Vec3[]) =>
    points.map((p) => project(p).slice(0, 2).join(',')).join(' ');
  const reset = () => {
    setYaw(0.74);
    setPitch(0.61);
    setZoom(1);
    setCenter([0, 0, 0]);
  };
  useEffect(() => {
    const target = tracks.find((t) => t.trackId === focus);
    if (target) {
      setCenter([...target.positionEnuM]);
      setZoom(1.5);
    }
    // A focus command captures this position; live telemetry must not reset manual orbit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, focusRevision]);
  useEffect(() => {
    const node = svg.current;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width) setViewWidth(Math.max(320, Math.round(width)));
    });
    if (node) observer.observe(node);
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setZoom((z) =>
        Math.max(0.5, Math.min(3, z * Math.exp(-event.deltaY * 0.001))),
      );
    };
    node?.addEventListener('wheel', wheel, { passive: false });
    return () => {
      node?.removeEventListener('wheel', wheel);
      observer.disconnect();
    };
  }, []);
  const faces: { points: Vec3[]; fill: string; depth: number }[] = [];
  if (campus)
    footprints.forEach(([e, n, w, d, h]) => {
      const box: Vec3[][] = [
        [
          [e, n, 0],
          [e + w, n, 0],
          [e + w, n, h],
          [e, n, h],
        ],
        [
          [e + w, n, 0],
          [e + w, n + d, 0],
          [e + w, n + d, h],
          [e + w, n, h],
        ],
        [
          [e, n + d, 0],
          [e + w, n + d, 0],
          [e + w, n + d, h],
          [e, n + d, h],
        ],
        [
          [e, n, 0],
          [e, n + d, 0],
          [e, n + d, h],
          [e, n, h],
        ],
        [
          [e, n, h],
          [e + w, n, h],
          [e + w, n + d, h],
          [e, n + d, h],
        ],
      ];
      box.forEach((points, i) =>
        faces.push({
          points,
          fill: ['#a9c9cc', '#7faab6', '#91b9bd', '#6f9fac', '#c8dfdb'][i],
          depth: points.reduce((sum, p) => sum + project(p)[2], 0) / 4,
        }),
      );
    });
  const circle = (r: number, z = 0): Vec3[] =>
    Array.from({ length: 81 }, (_, i) => [
      Math.cos((i / 80) * Math.PI * 2) * r,
      Math.sin((i / 80) * Math.PI * 2) * r,
      z,
    ]);
  const north = project([0, 520, 0]);
  return (
    <div className="space-view">
      <svg
        ref={svg}
        viewBox={`0 0 ${viewWidth} 430`}
        role="application"
        aria-label="交互式三维空域"
        aria-describedby="map-help"
        tabIndex={0}
        onKeyDown={(e) => {
          if (
            [
              'ArrowLeft',
              'ArrowRight',
              'ArrowUp',
              'ArrowDown',
              '+',
              '-',
              '0',
            ].includes(e.key)
          )
            e.preventDefault();
          if (e.key === 'ArrowLeft') setYaw((y) => y - 0.1);
          if (e.key === 'ArrowRight') setYaw((y) => y + 0.1);
          if (e.key === 'ArrowUp') setPitch((p) => Math.min(1.3, p + 0.08));
          if (e.key === 'ArrowDown') setPitch((p) => Math.max(0.15, p - 0.08));
          if (e.key === '+') setZoom((z) => Math.min(3, z + 0.15));
          if (e.key === '-') setZoom((z) => Math.max(0.5, z - 0.15));
          if (e.key === '0') reset();
        }}
        onPointerDown={(e) => {
          if ((e.target as Element).closest('[data-target]')) return;
          drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drag.current || drag.current.id !== e.pointerId) return;
          const deltaX = e.clientX - drag.current.x;
          const deltaY = e.clientY - drag.current.y;
          setYaw((y) => y + deltaX * 0.008);
          setPitch((p) => Math.max(0.15, Math.min(1.3, p + deltaY * 0.005)));
          drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <polygon
          points={path([
            [-1000, -1000, 0],
            [1000, -1000, 0],
            [1000, 1000, 0],
            [-1000, 1000, 0],
          ])}
          fill="#d8e6e3"
          stroke="#79a9b9"
        />
        {Array.from({ length: 41 }, (_, i) => (i - 20) * 50).map((i) => (
          <g
            key={i}
            stroke={i % 250 === 0 ? '#78a8b8' : '#b5d0cf'}
            strokeWidth={i % 250 === 0 ? 0.7 : 0.4}
          >
            <polyline
              points={path([
                [i, -1000, 0],
                [i, 1000, 0],
              ])}
            />
            <polyline
              points={path([
                [-1000, i, 0],
                [1000, i, 0],
              ])}
            />
          </g>
        ))}
        {[250, 500, 1000].map((r) => (
          <g key={r}>
            <polyline
              points={path(circle(r))}
              fill="none"
              stroke="#4b7ca3"
              strokeDasharray="4 7"
              strokeWidth=".8"
              opacity=".65"
            />
            <text
              x={project([r, 0, 0])[0]}
              y={project([r, 0, 0])[1] + 13}
              fill="#52778b"
              fontSize="8"
            >
              {r} m
            </text>
          </g>
        ))}
        {faces
          .sort((a, b) => a.depth - b.depth)
          .map((f, i) => (
            <polygon
              key={i}
              points={path(f.points)}
              fill={f.fill}
              stroke="#5d8da0"
              strokeWidth=".65"
            />
          ))}
        {campus && (
          <text
            x={project([0, 100, 0])[0]}
            y={project([0, 100, 0])[1] + 30}
            fill="#3f6b82"
            fontSize="10"
            letterSpacing="3"
          >
            校园中心 / 示意
          </text>
        )}
        <polyline
          points={path([
            [0, 0, 0],
            [0, 0, 350],
          ])}
          stroke="#4e7f9c"
          strokeDasharray="3 5"
          strokeWidth=".6"
        />
        {!topDown && (
          <text
            x={project([0, 0, 350])[0] + 8}
            y={project([0, 0, 350])[1]}
            fontSize="9"
            fill="#50768a"
          >
            U ↑ 350 m
          </text>
        )}
        <text x={north[0]} y={north[1]} fill="#3368a0" fontSize="11">
          N
        </text>
        <circle
          cx={project([0, 0, 0])[0]}
          cy={project([0, 0, 0])[1]}
          r="3"
          fill="#3368a0"
        />
        {demo &&
          [
            [-20, 0, 8],
            [20, 0, 8],
          ].map((p, i) => (
            <g key={i}>
              <circle
                cx={project(p as Vec3)[0]}
                cy={project(p as Vec3)[1]}
                r="4"
                fill="#3f88a7"
              />
              <text
                x={project(p as Vec3)[0] + (i === 0 ? -64 : 8)}
                y={project(p as Vec3)[1] + 15}
                fill="#426e82"
                fontSize="8"
              >
                CAM 0{i + 1}
              </text>
            </g>
          ))}
        {[...tracks]
          .sort(
            (a, b) => project(a.positionEnuM)[2] - project(b.positionEnuM)[2],
          )
          .map((t) => {
            const [x, y] = project(t.positionEnuM),
              color = objectStyles[t.class].color,
              isSelected = selected === t.trackId;
            const lx = Math.min(viewWidth - 144, Math.max(12, x + 19)),
              ly = Math.min(363, Math.max(50, y - 25));
            return (
              <g
                key={t.trackId}
                data-target={t.trackId}
                role="button"
                tabIndex={0}
                aria-label={`选择 ${t.trackId} ${objectStyles[t.class].label}`}
                aria-pressed={isSelected}
                onClick={() => onSelect(t.trackId)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                    onSelect(t.trackId);
                  }
                }}
                className="map-target"
              >
                {trails && (
                  <polyline
                    points={path(t.history)}
                    fill="none"
                    stroke={color}
                    strokeWidth={isSelected ? 2 : 1.3}
                    opacity=".7"
                  />
                )}
                <polyline
                  points={path([
                    t.positionEnuM,
                    [t.positionEnuM[0], t.positionEnuM[1], 0],
                  ])}
                  stroke={color}
                  strokeWidth=".7"
                  strokeDasharray="3 5"
                  opacity=".5"
                />
                {uncertainty && (
                  <ellipse
                    cx={x}
                    cy={y}
                    rx={Math.max(2, t.positionStdM[0] * scale)}
                    ry={Math.max(2, t.positionStdM[2] * scale)}
                    stroke={color}
                    fill={color}
                    fillOpacity=".16"
                    strokeWidth=".8"
                  />
                )}
                <circle
                  cx={x}
                  cy={y}
                  r={isSelected ? 14 : 9}
                  fill={color}
                  fillOpacity=".1"
                  stroke={color}
                  strokeOpacity={isSelected ? 0.8 : 0.4}
                />
                <circle cx={x} cy={y} r="3" fill={color} />
                <path
                  d={`M${x - 5},${y}h10 M${x},${y - 5}v10`}
                  stroke={color}
                  strokeWidth="1.2"
                />
                <rect
                  x={lx}
                  y={ly}
                  width="129"
                  height="40"
                  rx="4"
                  fill="#f2efe7"
                  stroke={isSelected ? color : '#91aba9'}
                  strokeWidth=".7"
                />
                <text
                  x={lx + 9}
                  y={ly + 15}
                  fontSize="10"
                  fill={color}
                  fontFamily="monospace"
                >
                  {t.trackId}
                </text>
                <text x={lx + 9} y={ly + 30} fontSize="8.5" fill="#4d6d7a">
                  H {t.positionEnuM[2].toFixed(0)}m ·{' '}
                  {objectStyles[t.class].short}
                </text>
              </g>
            );
          })}
      </svg>
      <div className="space-note">
        <strong>校园中心 · 局部 ENU 坐标系</strong>
        <br />
        {campus ? '校园模型与机位仅为示意' : '空域原点以现场标定为准'}
        <br />
        <span className="mono">
          {topDown ? 'TOP VIEW' : 'PERSPECTIVE'} / {zoom.toFixed(1)}×
        </span>
      </div>
      <div className="space-controls">
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="放大空域"
          onClick={() => setZoom((z) => Math.min(3, z + 0.2))}
        >
          <Plus />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="缩小空域"
          onClick={() => setZoom((z) => Math.max(0.5, z - 0.2))}
        >
          <Minus />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="重置空域视角"
          onClick={reset}
        >
          <LocateFixed />
        </Button>
      </div>
      <div className="map-legend">
        {Object.entries(objectStyles).map(([key, t]) => (
          <span key={key}>
            <i className="legend-dot" style={{ background: t.color }} />
            {t.short}
          </span>
        ))}
      </div>
      <span id="map-help" className="sr-only">
        拖动旋转，滚轮缩放。键盘方向键旋转，加减号缩放，0
        重置。目标支持回车选择。
      </span>
      {tracks.length === 0 && (
        <div className="map-empty">
          <span>当前暂无有效轨迹</span>
          <small>等待经过校验的空中目标坐标</small>
        </div>
      )}
    </div>
  );
}
