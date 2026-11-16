import type { SpatialPoint } from './camera-types';
export interface MonitorRuntime { state: string; error?: string; frameAvailable: boolean; sequence?: number; width?: number; height?: number; previewFps?: number; lastFrameAt?: number }
export interface MonitorDevice { id:string; code:string; label:string; host:string; port:number; username:string; channel:number; stream:string; path?:string; passwordConfigured:boolean; isStereo:boolean; runtime:MonitorRuntime }
export interface CameraDirection { yawDeg:number; pitchDeg:number; horizontalFovDeg:number; verticalFovDeg:number; rangeMeters:number }
export interface SceneConfig { registered:boolean; origin:SpatialPoint; yawDeg:number; scale:number; cameraPositions?:Record<string,SpatialPoint>; cameraDirections?:Record<string,CameraDirection> }
export const emptySceneConfig=():SceneConfig=>({registered:false,origin:{x:0,y:0,z:0},yawDeg:0,scale:1,cameraPositions:{},cameraDirections:{}});
