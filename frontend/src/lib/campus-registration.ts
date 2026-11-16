import type { SpatialPoint } from '@/types/camera-types';
import type { SceneConfig } from '@/types/monitor';

export const finitePoint=(point:SpatialPoint|undefined|null):point is SpatialPoint=>!!point&&[point.x,point.y,point.z].every(Number.isFinite);
export function validRegistration(config:SceneConfig):boolean {
  return config.registered&&finitePoint(config.origin)&&Number.isFinite(config.yawDeg)&&Number.isFinite(config.scale)&&config.scale>0;
}
/** The same metric-to-model transform applies to positions, trails and frusta. */
export function metricToCampus(point:SpatialPoint,config:SceneConfig):SpatialPoint {
  const yaw=config.yawDeg*Math.PI/180,c=Math.cos(yaw),s=Math.sin(yaw);
  return{x:config.origin.x+(c*point.x+s*point.z)*config.scale,y:config.origin.y+point.y*config.scale,z:config.origin.z+(-s*point.x+c*point.z)*config.scale};
}
export interface CampusObservation extends SpatialPoint { receivedAt:number }
/** Receipt timestamps describe actual measured observations, never synthetic motion. */
export function appendObservation(history:CampusObservation[],point:SpatialPoint,receivedAt:number,windowMs=60_000):CampusObservation[] {
  const current=history.filter(item=>receivedAt-item.receivedAt<=windowMs);
  const last=current.at(-1);
  if(!finitePoint(point)||!Number.isFinite(receivedAt))return current;
  if(last&&last.x===point.x&&last.y===point.y&&last.z===point.z)return current;
  return [...current,{x:point.x,y:point.y,z:point.z,receivedAt}].slice(-300);
}
