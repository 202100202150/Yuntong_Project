import type { SpatialCameraPosition } from '@/types/camera-types';

export function cameraLabel(id: string, point?: SpatialCameraPosition): string {
  const name = id === 'camera1' ? 'C1' : 'C2';
  if (!point) return name;
  const side = point.side ?? (Math.abs(point.x) < 1e-8 ? 'center' : point.x > 0 ? 'left' : 'right');
  return `${name}（${{ left: '左', right: '右', center: '中线' }[side]}）`;
}

export function originDistance(point: SpatialCameraPosition): string {
  return (point.distanceToOriginMeters ?? Math.hypot(point.x, point.y, point.z)).toFixed(3);
}
