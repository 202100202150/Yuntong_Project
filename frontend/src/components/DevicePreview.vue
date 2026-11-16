<template><div class="device-preview"><img v-if="freshFrame" :src="url" @error="previewError=true" :alt="cameraName(cameraId)+' 实时画面'"/><ProductEmpty v-else icon="video" :title="streaming?(unavailable?'预览暂不可用':'正在获取画面'):'设备未连接'" :description="streaming?(unavailable?'未收到最新视频帧，正在重试':'等待最新视频帧'):'设备接入后将在此显示实时画面'"/><span class="preview-label">{{cameraName(cameraId)}} · {{label?.replace(/^C[12]\s*/,'')||'摄像机'}}</span><span v-if="freshFrame" class="preview-live"><i/>实时画面</span></div></template>
<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue';
import {useCameraStore} from '@/stores/camera';
import {cameraBridge} from '@/api/camera-bridge';
import type {CameraId} from '@/types/camera-types';
import {cameraName} from '@/lib/product-format';
import ProductEmpty from './ProductEmpty.vue';
const props=defineProps<{cameraId:CameraId;label?:string}>();
const store=useCameraStore();const url=ref('');const now=ref(Date.now());const waitingSince=ref(now.value);const lastFrameAt=ref(0);const previewError=ref(false);
const frameTimeout=3000;let sequence=0;let timer:ReturnType<typeof setTimeout>|undefined;let freshnessTimer:ReturnType<typeof setInterval>|undefined;let controller:AbortController|undefined;let alive=true;
const streaming=computed(()=>store.status.serviceState==='ready'&&store.status.cameras[props.cameraId].frameAvailable&&store.status.cameras[props.cameraId].state==='streaming');
const unavailable=computed(()=>previewError.value||now.value-(lastFrameAt.value||waitingSince.value)>frameTimeout);
const freshFrame=computed(()=>streaming.value&&!!url.value&&lastFrameAt.value>0&&!unavailable.value);
function clear(){if(url.value)URL.revokeObjectURL(url.value);url.value='';sequence=0;lastFrameAt.value=0;previewError.value=false;now.value=Date.now();waitingSince.value=now.value}
async function update(){
 if(!alive)return;
 const cameraId=props.cameraId;const request=new AbortController();let timedOut=false;
 controller=request;
 const deadline=setTimeout(()=>{timedOut=true;request.abort()},5000);
 try{
  if(streaming.value){
   const frame=await cameraBridge.getFrame(cameraId,sequence,request.signal);
   if(frame&&alive&&!request.signal.aborted&&cameraId===props.cameraId&&streaming.value){
    sequence=frame.sequence;const old=url.value;url.value=URL.createObjectURL(new Blob([new Uint8Array(frame.jpegBytes)],{type:'image/jpeg'}));
    now.value=Date.now();lastFrameAt.value=now.value;previewError.value=false;
    if(old)URL.revokeObjectURL(old);
   }
  }else clear();
 }catch{
  if(alive&&cameraId===props.cameraId&&(!request.signal.aborted||timedOut))previewError.value=true;
 }finally{
  clearTimeout(deadline);if(controller===request)controller=undefined;
  if(alive)timer=setTimeout(update,750);
 }
}
watch(()=>props.cameraId,()=>{controller?.abort();clear()});
watch(streaming,()=>{controller?.abort();clear()});
watch(()=>store.status.cameras[props.cameraId].sequence,(current,previous)=>{if(current<previous){controller?.abort();clear()}});
onMounted(()=>{freshnessTimer=setInterval(()=>now.value=Date.now(),500);void update()});
onUnmounted(()=>{alive=false;clearTimeout(timer);clearInterval(freshnessTimer);controller?.abort();clear()});
</script>
<style scoped>
.device-preview {
  position: relative;
  overflow: hidden;
  min-height: 180px;
  aspect-ratio: 16 / 9;
  border-radius: 9px;
  background: #edf2f9;
}
.device-preview img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.device-preview :deep(.product-empty) { height: 100%; min-height: 180px; }
.preview-label {
  position: absolute;
  top: 10px;
  left: 12px;
  padding: 4px 10px;
  border-radius: 5px;
  background: #39485bdc;
  color: #fff;
  font-size: 17px;
  font-weight: 600;
}
.preview-live {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 14px;
  background: linear-gradient(transparent, #27364bba);
  color: #fff;
  font-size: 15px;
  font-weight: 500;
}
.preview-live i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #37d991;
}
</style>
