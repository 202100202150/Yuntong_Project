import { defineStore } from 'pinia';
import { http } from '@/api/http';
import { emptySceneConfig, type MonitorDevice, type SceneConfig } from '@/types/monitor';
export const useMonitorStore=defineStore('monitor',{
 state:()=>({devices:[] as MonitorDevice[],sceneConfig:emptySceneConfig(),sceneLoaded:false,loaded:false,error:'',selectedCameraId:null as string|null,selectedTargetId:null as string|null}),
 actions:{
  async refresh(){try{const{data}=await http.get('/monitor/devices');this.devices=data.devices;this.loaded=true;this.error=''}catch(error){this.error=(error as Error).message;this.devices=this.devices.map(device=>({...device,runtime:{...device.runtime,state:'error',frameAvailable:false,error:'连接中断'}}))}},
  async loadSceneConfig(){const{data}=await http.get('/monitor/scene-config');this.sceneConfig=data.config;this.sceneLoaded=true},
  async saveSceneConfig(config:SceneConfig){const{data}=await http.put('/monitor/scene-config',{config});this.sceneConfig=data.config},
 }
});
