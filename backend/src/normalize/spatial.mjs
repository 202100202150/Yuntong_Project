const states = new Set(['uncalibrated', 'ready', 'waiting_for_frames', 'frame_time_gap', 'no_targets', 'no_valid_matches', 'error']);
const numeric = (value) => typeof value === 'number' && Number.isFinite(value);
const point = (value, fallback) => value && [value.x, value.y, value.z].every(numeric)
  ? { x: value.x, y: value.y, z: value.z } : fallback;

export function normalizeSpatial(value) {
  const source = value && typeof value === 'object' ? value : {};
  const state = states.has(source.state) ? source.state : 'uncalibrated';
  return {
    state,
    message: typeof source.message === 'string' ? source.message.slice(0, 300) : '请先完成双摄标定，再显示米制无人机位置。',
    units: 'm',
    cameras: {
      camera1: point(source.cameras?.camera1, { x: -0.305, y: 0, z: 0 }),
      camera2: point(source.cameras?.camera2, { x: 0.305, y: 0, z: 0 }),
    },
    targets: state === 'ready' && Array.isArray(source.targets) ? source.targets.slice(0, 256).flatMap((item) => {
      if (!item || item.class !== 'drone' || ![item.x, item.y, item.z, item.rangeMeters, item.reprojectionErrorPx, item.frameSkewMs].every(numeric)
        || item.rangeMeters < 0 || item.reprojectionErrorPx < 0 || item.frameSkewMs < 0
        || typeof item.id !== 'string' || typeof item.camera1Id !== 'string' || typeof item.camera2Id !== 'string'
        || item.quality !== 'calibrated') return [];
      return [{
        id: item.id.slice(0, 160), camera1Id: item.camera1Id.slice(0, 80), camera2Id: item.camera2Id.slice(0, 80),
        label: typeof item.label === 'string' ? item.label.slice(0, 80) : 'drone',
        class: 'drone',
        keypointName: typeof item.keypointName === 'string' ? item.keypointName.slice(0, 40) : 'drone_box_center',
        x: item.x, y: item.y, z: item.z, rangeMeters: item.rangeMeters,
        azimuthDeg: numeric(item.azimuthDeg) ? item.azimuthDeg : Math.atan2(item.x, item.z) * 180 / Math.PI,
        elevationDeg: numeric(item.elevationDeg) ? item.elevationDeg : Math.atan2(item.y, Math.hypot(item.x, item.z)) * 180 / Math.PI,
        trackingState: item.trackingState === 'held' ? 'held' : 'measured',
        keypointCount: numeric(item.keypointCount) ? Math.max(0, Math.floor(item.keypointCount)) : 0,
        reprojectionErrorPx: item.reprojectionErrorPx, frameSkewMs: item.frameSkewMs, quality: 'calibrated',
      }];
    }) : [],
    diagnostics: source.diagnostics && typeof source.diagnostics === 'object' ? source.diagnostics : {},
  };
}
