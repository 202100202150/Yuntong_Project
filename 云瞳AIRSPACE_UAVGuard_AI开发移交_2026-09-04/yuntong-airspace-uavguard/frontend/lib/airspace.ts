export type Vec3 = [number, number, number];
export type ObjectClass = 'multirotor' | 'fixed_wing_uav' | 'bird' | 'unknown';
export type Track = {
  trackId: string;
  timestampUtc: string;
  state: 'candidate' | 'confirmed' | 'lost';
  class: ObjectClass;
  classConfidence: number;
  positionEnuM: Vec3;
  velocityEnuMps: Vec3;
  positionStdM: Vec3;
  reprojectionErrorPx: number;
  cameraIds: string[];
  metadata?: Record<string, unknown>;
};
export type DisplayTrack = Track & { history: Vec3[] };
export type CameraHealth = {
  cameraId: string;
  status: string;
  averageFps?: number;
};
export type Health = {
  status: string;
  cameras: CameraHealth[];
  stereo?: { syncResidualP95Ms?: number | null };
  classifierReady?: boolean;
};
export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
export function parseHealth(value: unknown): Health | null {
  if (
    !isRecord(value) ||
    typeof value.status !== 'string' ||
    !Array.isArray(value.cameras)
  )
    return null;
  const cameras: CameraHealth[] = [];
  for (const c of value.cameras) {
    if (
      !isRecord(c) ||
      typeof c.cameraId !== 'string' ||
      typeof c.status !== 'string'
    )
      return null;
    cameras.push({
      cameraId: c.cameraId,
      status: c.status,
      averageFps:
        typeof c.averageFps === 'number' && Number.isFinite(c.averageFps)
          ? c.averageFps
          : undefined,
    });
  }
  const residual = isRecord(value.stereo)
    ? value.stereo.syncResidualP95Ms
    : null;
  return {
    status: value.status,
    cameras,
    classifierReady: value.classifierReady === true,
    stereo: {
      syncResidualP95Ms:
        typeof residual === 'number' &&
        Number.isFinite(residual) &&
        residual >= 0
          ? residual
          : null,
    },
  };
}
export const objectStyles: Record<
  ObjectClass,
  { label: string; color: string; short: string }
> = {
  multirotor: { label: '多旋翼无人机', color: '#3368a0', short: '多旋翼' },
  fixed_wing_uav: { label: '固定翼无人机', color: '#315570', short: '固定翼' },
  bird: { label: '鸟类', color: '#4c97b5', short: '鸟类' },
  unknown: { label: '未知空中目标', color: '#c27b3f', short: '未知目标' },
};
const vector = (v: unknown): v is Vec3 =>
  Array.isArray(v) &&
  v.length === 3 &&
  v.every((n) => typeof n === 'number' && Number.isFinite(n));
export function parseTrack(value: unknown): Track | null {
  if (!value || typeof value !== 'object') return null;
  const t = value as Record<string, unknown>;
  if (
    typeof t.trackId !== 'string' ||
    !t.trackId ||
    t.trackId.length > 128 ||
    typeof t.timestampUtc !== 'string' ||
    !Number.isFinite(Date.parse(t.timestampUtc)) ||
    !['candidate', 'confirmed', 'lost'].includes(t.state as string) ||
    !Object.keys(objectStyles).includes(t.class as string) ||
    typeof t.classConfidence !== 'number' ||
    !Number.isFinite(t.classConfidence) ||
    t.classConfidence < 0 ||
    t.classConfidence > 1 ||
    !vector(t.positionEnuM) ||
    !vector(t.velocityEnuMps) ||
    !vector(t.positionStdM) ||
    t.positionStdM.some((n) => n < 0) ||
    typeof t.reprojectionErrorPx !== 'number' ||
    !Number.isFinite(t.reprojectionErrorPx) ||
    t.reprojectionErrorPx < 0 ||
    !Array.isArray(t.cameraIds) ||
    !t.cameraIds.every((id) => typeof id === 'string')
  )
    return null;
  return {
    trackId: t.trackId,
    timestampUtc: t.timestampUtc,
    state: t.state as Track['state'],
    class: t.class as ObjectClass,
    classConfidence: t.classConfidence,
    positionEnuM: t.positionEnuM,
    velocityEnuMps: t.velocityEnuMps,
    positionStdM: t.positionStdM,
    reprojectionErrorPx: t.reprojectionErrorPx,
    cameraIds: t.cameraIds,
    metadata:
      t.metadata && typeof t.metadata === 'object' && !Array.isArray(t.metadata)
        ? (t.metadata as Record<string, unknown>)
        : {},
  };
}
export function mergeTrack(
  current: DisplayTrack | undefined,
  incoming: Track,
): DisplayTrack {
  if (
    current &&
    Date.parse(incoming.timestampUtc) <= Date.parse(current.timestampUtc)
  )
    return current;
  return {
    ...incoming,
    history: [...(current?.history ?? []).slice(-99), incoming.positionEnuM],
  };
}
export function normalizeBackend(
  input: string,
  pageProtocol = 'http:',
): string {
  const value = input.trim().replace(/\/+$/, '');
  if (!value) return '';
  const url = new URL(value);
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error('请使用不含账户、密码、查询参数的 HTTP(S) 网关地址。');
  if (pageProtocol === 'https:' && url.protocol === 'http:')
    throw new Error(
      'HTTPS 页面不能连接 HTTP 网关；请使用 HTTPS 网关，或在校园本地部署网页。',
    );
  return url.toString().replace(/\/+$/, '');
}
export function projectPoint(
  point: Vec3,
  yaw: number,
  pitch: number,
  scale: number,
  center: Vec3 = [0, 0, 0],
): Vec3 {
  const [e, n, u] = point.map((v, i) => v - center[i]);
  const x = e * Math.cos(yaw) - n * Math.sin(yaw);
  const depth = e * Math.sin(yaw) + n * Math.cos(yaw);
  return [
    Number((410 + x * scale).toFixed(3)),
    Number(
      (
        256 +
        depth * Math.sin(pitch) * scale -
        u * Math.cos(pitch) * scale
      ).toFixed(3),
    ),
    Number((depth * Math.cos(pitch) + u * Math.sin(pitch)).toFixed(3)),
  ];
}
export function recordingPaths(track: Track): string[] {
  const segments = track.metadata?.recordingSegments;
  const values = Array.isArray(segments)
    ? segments
    : segments && typeof segments === 'object'
      ? Object.values(segments)
      : [];
  return values.filter(
    (p): p is string =>
      typeof p === 'string' &&
      p.length > 0 &&
      !p.startsWith('/') &&
      !p.includes('..') &&
      !p.includes(':') &&
      !p.includes('\\'),
  );
}
const demoSeeds: {
  id: string;
  class: ObjectClass;
  center: Vec3;
  radius: number;
  speed: number;
  phase: number;
  confidence: number;
}[] = [
  {
    id: 'UAV-017',
    class: 'multirotor',
    center: [70, 120, 125],
    radius: 220,
    speed: 0.041,
    phase: 0.55,
    confidence: 0.948,
  },
  {
    id: 'UAV-022',
    class: 'fixed_wing_uav',
    center: [-160, -170, 230],
    radius: 300,
    speed: 0.078,
    phase: 3.6,
    confidence: 0.902,
  },
  {
    id: 'AIR-024',
    class: 'bird',
    center: [-280, 90, 86],
    radius: 130,
    speed: 0.085,
    phase: 2.1,
    confidence: 0.872,
  },
  {
    id: 'AIR-031',
    class: 'unknown',
    center: [210, -210, 180],
    radius: 165,
    speed: 0.072,
    phase: 5.5,
    confidence: 0.613,
  },
];
export function demoPosition(index: number, seconds: number): Vec3 {
  const s = demoSeeds[index],
    a = s.phase + seconds * s.speed;
  return [
    s.center[0] + Math.cos(a) * s.radius,
    s.center[1] + Math.sin(a) * s.radius * 0.68,
    s.center[2] + Math.sin(a * 1.6) * 18,
  ];
}
export function makeDemoTracks(
  seconds = 0,
  timestamp = '2026-08-31T06:32:08.000Z',
): DisplayTrack[] {
  return demoSeeds.map((s, i) => {
    const a = s.phase + seconds * s.speed;
    return {
      trackId: s.id,
      class: s.class,
      state: i === 3 ? 'candidate' : 'confirmed',
      timestampUtc: timestamp,
      classConfidence: s.confidence,
      positionEnuM: demoPosition(i, seconds),
      velocityEnuMps: [
        -Math.sin(a) * s.radius * s.speed,
        Math.cos(a) * s.radius * 0.68 * s.speed,
        Math.cos(a * 1.6) * 28.8 * s.speed,
      ],
      positionStdM: i === 3 ? [4.2, 5.1, 3.7] : [1.4, 2.1, 1.2],
      reprojectionErrorPx: i === 3 ? 2.1 : 0.84,
      cameraIds: ['cam01', 'cam02'],
      metadata: { demo: true },
      history: Array.from({ length: 70 }, (_, j) =>
        demoPosition(i, seconds - (69 - j) * 0.5),
      ),
    };
  });
}
export function speedOf(t: Track) {
  return Math.hypot(...t.velocityEnuMps);
}
export function eventKey(t: Track) {
  return `${t.trackId}:${t.timestampUtc}:${t.state}`;
}
