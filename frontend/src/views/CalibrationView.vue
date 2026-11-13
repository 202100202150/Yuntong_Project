<template>
<div class="calibration-page">
<div class="page-heading"><div><h1>设备与标定</h1><p class="muted">双摄像机采集、空间解算与质量验证</p></div><button class="btn" :disabled="loading" @click="refresh"><RefreshCw :size="16"/>重新读取</button></div>
<nav class="device-tabs"><span class="active current-tab" aria-current="page">空间标定</span><router-link to="/devices">设备管理</router-link></nav>
<p v-if="error" class="error-banner" role="alert">{{error}}</p>
<section class="panel calibration-summary"><header><h2>双摄像机空间标定</h2><span class="badge" :class="job?.report?.passed?'success':calibrated?'blue':'warning'">{{job?.report?.passed?'验证通过':calibrated?'已载入标定参数':'等待标定'}}</span></header>
<ol class="calibration-steps"><li v-for="(step,index) in steps" :key="step"><span :class="{complete:completed(index),current:index===currentStep}"><Check v-if="completed(index)" :size="17"/><template v-else>{{index+1}}</template></span><strong>{{step}}</strong></li></ol>
<div class="calibration-workspace"><div class="capture-workspace">
<div class="calibration-previews"><DevicePreview camera-id="camera1" label="标定画面"/><DevicePreview camera-id="camera2" label="标定画面"/></div>
<div class="coordinate-block"><svg class="calibration-geometry" viewBox="0 0 580 190" role="img" aria-label="两相机光心中点坐标关系"><defs><marker id="axis-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="context-stroke"/></marker><pattern id="board-cells" width="20" height="20" patternUnits="userSpaceOnUse"><rect x="2" y="2" width="12" height="12" fill="#425974"/><rect x="5" y="5" width="6" height="6" fill="#f7faff"/></pattern></defs><path d="M110 135L250 24L350 55Z" fill="#dceaff" fill-opacity=".5" stroke="#9fc1f8"/><path d="M470 135L250 24L350 55Z" fill="#dceaff" fill-opacity=".5" stroke="#9fc1f8"/><path d="M250 15L350 34L350 100L250 81Z" fill="#fff" stroke="#b4c4d9" stroke-width="2"/><path d="M260 26L340 41L340 88L260 74Z" fill="url(#board-cells)"/><path d="M110 145L470 145" fill="none" stroke="#a7b6ca" stroke-width="2"/><path d="M290 145L225 145" stroke="#e64c45" stroke-width="2.5" marker-end="url(#axis-arrow)"/><path d="M290 145L290 94" stroke="#25a64e" stroke-width="2.5" marker-end="url(#axis-arrow)"/><path d="M290 145L325 119" stroke="#2473f3" stroke-width="2.5" marker-end="url(#axis-arrow)"/><circle cx="290" cy="145" r="4" fill="white" stroke="#49627e" stroke-width="2"/><text x="208" y="149" fill="#d83f3a" font-size="18" font-weight="600">X</text><text x="300" y="99" fill="#269a48" font-size="18" font-weight="600">Y</text><text x="332" y="121" fill="#2672eb" font-size="18" font-weight="600">Z</text><g v-for="(x,i) in [110,470]" :key="x" :transform="'translate('+x+',130)'"><path d="M-16 -12L3 -18L18 -10L18 5L-2 11L-16 4Z" fill="#fff" stroke="#58708e" stroke-width="2"/><path d="M0 11V24" stroke="#58708e" stroke-width="3"/><rect x="-20" y="26" width="40" height="24" rx="6" fill="white" stroke="#c3cede"/><text x="0" y="43" text-anchor="middle" font-size="15" fill="#263d59">C{{i+1}}</text></g></svg><div class="coordinate-caption"><strong>坐标原点：两光心中点</strong><span>X 向左 · Y 向上 · Z 向前</span></div></div><div v-if="job && !(job.type==='solve' && job.status==='succeeded' && job.report?.passed)" class="job-status" :class="job.status"><LoaderCircle v-if="job.status==='running'" class="spin" :size="18"/><CheckCircle2 v-else-if="job.status==='succeeded'" :size="18"/><CircleAlert v-else :size="18"/><div><strong>{{job.status==='running'?'任务进行中':job.status==='succeeded'?'任务已完成':'任务未完成'}}</strong><p>{{job.message||jobMessage}}</p><span v-if="job.type==='capture'&&job.capturedPairs!==undefined">已采集 {{job.capturedPairs}} / {{job.requestedPairs||pairs}} 组</span></div></div>
</div><aside class="validation-panel"><div class="quality-title" :class="{passed:job?.report?.passed}"><component :is="job?.report?.passed?CheckCircle2:ScanLine" :size="34"/><strong>{{job?.report?.passed?'验证通过':'等待标定验证'}}</strong></div><dl class="quality-values"><div><dt>重投影误差</dt><dd>{{reportNumber(job?.report?.stereoRmsPx)}} px</dd></div><div><dt>有效样本</dt><dd>{{job?.report?.pairCount??'—'}} 组</dd></div><div><dt>相机基线</dt><dd>{{reportNumber(job?.report?.measuredBaselineMeters??config?.rig.baselineMeters,3)}} m</dd></div><div><dt>双路时差</dt><dd>{{skew}} ms</dd></div></dl><p v-for="failure in job?.report?.failures||[]" :key="failure" class="quality-failure">{{failure}}</p><div class="calibration-save-note"><Info :size="22"/><p>保存后将把本次标定参数应用于空间跟踪，用于目标的三维定位、距离与速度计算。</p></div><div class="calibration-state">当前状态：{{job?.status==='running'?'计算任务进行中':job?.report?.passed?'已完成质量验证':calibrated?'已载入参数，等待验证':'尚未采集标定数据'}}</div></aside></div><footer class="calibration-footer"><button class="btn" @click="toggleControls">采集与计算</button><button class="btn" :disabled="loading" @click="refresh">重新读取</button><button class="btn primary" :disabled="busy||job?.type!=='solve'||job?.status!=='succeeded'||!job?.report?.passed" :title="job?.report?.passed?'保存已通过验证的标定参数':'须先完成计算并通过质量验证'" @click="apply">{{applying?'正在应用…':'保存并应用'}}</button></footer></section><details ref="controlsPanel" class="panel calibration-controls" :open="controlsOpen"><summary @click.prevent="toggleControls">采集与计算参数</summary><div class="controls-grid"><div class="capture-controls"><form @submit.prevent="capture"><label class="form-field">采集组数<input v-model.number="pairs" type="number" min="3" max="100" step="1" required/></label><label class="form-field">采集间隔（秒）<input v-model.number="interval" type="number" min="1" max="15" step="1" required/></label><button class="btn" :disabled="busy||!connected" :title="connected?'启动双路标定板采集':'请先连接两路摄像机'"><Camera :size="16"/>{{job?.type==='capture'&&job.status==='running'?'正在采集…':'开始采集'}}</button></form><p class="muted">使用 AprilGrid 标定板，调整板的位置与角度，同时保持两路相机可见。</p></div>
<form @submit.prevent="solve"><label class="form-field">实测光心基线（m）<input v-model.number="baseline" type="number" min=".01" max="1000" step=".001" required placeholder="输入实测距离"/></label><div class="validation-settings"><label class="form-field">最少有效样本<input v-model.number="minPairs" type="number" min="3" max="100" step="1" required/></label><label class="form-field">误差上限（px）<input v-model.number="maxRms" type="number" min=".1" max="10" step=".1" required/></label></div><button class="btn primary calculate-button" :disabled="busy||!baseline" :title="baseline?'根据已采集样本计算标定参数':'请先输入实测光心基线'"><ScanLine :size="16"/>{{job?.type==='solve'&&job.status==='running'?'正在计算…':'计算标定参数'}}</button></form></div></details>
<details class="panel calibration-editor"><summary>导入已有标定参数 / 高级配置</summary><DeviceManage :key="editorKey"/></details></div>
</template>
<script setup lang="ts">
import{computed,onMounted,onBeforeUnmount,ref,nextTick}from'vue';import{Check,RefreshCw,Move3D,Camera,ScanLine,LoaderCircle,CheckCircle2,CircleAlert,Info}from'lucide-vue-next';import DevicePreview from'@/components/DevicePreview.vue';import DeviceManage from'@/views/DeviceManage.vue';import{cameraBridge}from'@/api/camera-bridge';import{http}from'@/api/http';import{useCameraStore}from'@/stores/camera';import type{SpatialConfig}from'@/types/camera-types';import{errorMessage}from'@/lib/product-format';
interface Report{passed:boolean;pairCount?:number;stereoRmsPx?:number;measuredBaselineMeters?:number;rawCalibratedBaselineMeters?:number;failures?:string[]}
interface Job{id:string;type:'capture'|'solve';status:'running'|'succeeded'|'failed';message:string;report?:Report;capturedPairs?:number;requestedPairs?:number}
const store=useCameraStore();const controlsOpen=ref(false);const controlsPanel=ref<HTMLDetailsElement|null>(null);const steps=['连接设备','采集标定板','计算参数','验证与保存'];const config=ref<SpatialConfig|null>(null);const error=ref('');const loading=ref(false);const editorKey=ref(0);const job=ref<Job|null>(null);const pairs=ref(30);const interval=ref(4);const baseline=ref<number|string>('');const minPairs=ref(15);const maxRms=ref(2);const submitting=ref(false);const applying=ref(false);let alive=true;let timer:ReturnType<typeof setTimeout>|undefined;
const connected=computed(()=>Object.values(store.status.cameras).every(c=>c.state==='streaming'));const calibrated=computed(()=>!!config.value&&Object.values(config.value.cameras).every(c=>(c.intrinsics.fx||0)>0&&(c.intrinsics.fy||0)>0));const skew=computed(()=>store.status.matchingDiagnostics.frameSkewMs?.toFixed(1)||'—');const busy=computed(()=>submitting.value||applying.value||job.value?.status==='running');const currentStep=computed(()=>job.value?.type==='solve'?(job.value.status==='succeeded'?3:2):job.value?.type==='capture'?1:0);const completed=(index:number)=>index===0?connected.value:index<currentStep.value;
const reportNumber=(value:unknown,precision=2)=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(precision):'—';const jobMessage=computed(()=>job.value?.type==='capture'?'正在采集双路标定板画面':'正在计算双目参数与质量指标');
async function toggleControls(){controlsOpen.value=!controlsOpen.value;await nextTick();if(controlsOpen.value)controlsPanel.value?.scrollIntoView({behavior:'smooth',block:'start'})}
async function poll(){try{const{data}=await http.get('/calibration/job');if(alive)job.value=data.job||null}catch(e){if(alive)error.value=errorMessage(e)}finally{if(alive)timer=setTimeout(poll,job.value?.status==='running'?1200:6000)}}
async function refresh(){loading.value=true;error.value='';try{config.value=(await cameraBridge.getSpatialConfig()).config;editorKey.value++;await store.refreshStatus()}catch(e){error.value=errorMessage(e)}finally{loading.value=false}}
async function capture(){if(busy.value)return;if(!connected.value){error.value='请先连接两路摄像机';return}submitting.value=true;error.value='';try{const{data}=await http.post('/calibration/capture',{pairs:pairs.value,interval:interval.value,boardType:'aprilgrid'});job.value=data.job;store.toast('标定采集任务已启动','info')}catch(e){error.value=errorMessage(e)}finally{submitting.value=false}}
async function solve(){if(busy.value)return;if(!Number.isFinite(Number(baseline.value))||Number(baseline.value)<=0){error.value='请输入有效的实测光心基线';return;}submitting.value=true;error.value='';try{const{data}=await http.post('/calibration/solve',{baselineMeters:Number(baseline.value),minPairs:minPairs.value,maxRms:maxRms.value});job.value=data.job;store.toast('标定计算任务已启动','info')}catch(e){error.value=errorMessage(e)}finally{submitting.value=false}}
async function apply(){if(busy.value)return;if(job.value?.type!=='solve'||job.value.status!=='succeeded'||!job.value.report?.passed){error.value='请先完成标定计算并通过质量验证';return}applying.value=true;error.value='';try{const{data}=await http.post('/calibration/apply',{}, {timeout:60000});store.toast(data.applied===false?(data.message||'标定参数已保存，等待服务恢复后生效'):'标定参数已保存并应用',data.applied===false?'info':'success');await refresh()}catch(e){error.value=errorMessage(e)}finally{applying.value=false}}
onMounted(()=>{void refresh();void poll()});onBeforeUnmount(()=>{alive=false;clearTimeout(timer)});
</script>
<style scoped>
.device-tabs {
  display: flex;
  gap: 27px;
  border-bottom: 1px solid #dde5ef;
}

.device-tabs a, .device-tabs .current-tab {
  padding: 12px 7px;
  font-size: 14px;
  text-decoration: none;
  color: #63758e;
  border-bottom: 3px solid transparent;
}

.device-tabs .active {
  color: #1265f1;
  border-bottom-color: #1265f1;
}

.calibration-summary header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.calibration-summary h2 {
  font-size: 18px;
  margin: 0;
}

.calibration-steps li:last-child:before {
  display: none;
}

.capture-controls {
  border: 1px solid #e5ebf4;
  border-radius: 9px;
  padding: 18px;
  margin-top: 18px;
}

.capture-controls form {
  display: flex;
  align-items: end;
  gap: 14px;
}

.capture-controls .form-field {
  flex: 1;
  margin: 0;
  min-width: 0;
}

.capture-controls .muted {
  font-size: 11px;
  line-height: 1.8;
  margin: 14px 0 0;
}

.coordinate-block>div {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.coordinate-block strong {
  font-size: 12px;
  color: #5a6f8d;
}

.coordinate-block span {
  font-size: 11px;
  color: #93a4bb;
}

.coordinate-block dl {
  margin: 0 0 0 auto;
  font-size: 11px;
  flex: 1;
  max-width: 190px;
}

.coordinate-block dl>div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;
}

.coordinate-block dt {
  color: #8fa1b9;
}

.coordinate-block dd {
  margin: 0;
  color: #4f6889;
}

.validation-panel h3 {
  font-size: 16px;
  margin: 0 0 24px;
}

.validation-settings {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.calculate-button {
  width: 100%;
}

.quality-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 15px;
  color: #8ea2bf;
  padding: 38px 20px;
  text-align: center;
  font-size: 12px;
  flex: 1;
}

.quality-placeholder strong {
  font-size: 13px;
  color: #67809f;
}

.quality-placeholder span {
  line-height: 1.8;
}

.quality-report {
  margin: 23px 0;
}

.quality-report dl>div {
  display: flex;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid #e8eef6;
  font-size: 12px;
}

.quality-report dt {
  color: #8b9aae;
}

.quality-report dd {
  color: #4c617c;
  margin: 0;
}

.quality-failure {
  font-size: 12px;
  color: #b34c45;
}

.apply-button {
  width: 100%;
  margin-top: auto;
}

.apply-note {
  font-size: 11px;
  line-height: 1.8;
  text-align: center;
  margin: 10px 0 0;
}

.job-status.failed {
  background: #fff5f3;
  border-color: #f4dcd7;
  color: #bb6252;
}

.job-status.succeeded {
  background: #f1faf5;
  border-color: #dcefe2;
  color: #438961;
}

.job-status svg {
  flex-shrink: 0;
}

.calibration-editor summary {
  cursor: pointer;
  font-weight: 600;
  font-size: 14px;
  color: #607895;
}

.calibration-editor[open] summary {
  margin-bottom: 20px;
}

.calibration-editor :deep(.page-content) {
  max-width: none;
  padding: 8px;
  height: auto;
  overflow: visible;
}

.calibration-editor :deep(.camera-summary) {
  display: none;
}

.calibration-editor :deep(h2) {
  font-size: 17px;
}

.calibration-editor :deep(.config-card) {
  box-shadow: none;
  border: 1px solid #e3eaf3;
}

.calibration-editor :deep(.subtitle) {
  font-size: 12px;
}

.calibration-editor :deep(.calibration-notice) {
  background: #f6f9fe;
  border-color: #e0e9f7;
  color: #536c8b;
}

.calibration-editor :deep(.calibration-json) {
  margin-bottom: 20px;
}

.calibration-page>.page-heading {
  display: none;
}

.calibration-summary {
  padding: 0 12px 12px;
  min-height: 787px;
}

.calibration-summary>header .badge {
  display: none;
}

.calibration-steps {
  display: flex;
  list-style: none;
  margin: 0;
  height: 106px;
  padding: 18px 30px 20px;
}

.calibration-steps li:before {
  content: '';
  height: 1px;
  background: #dee7f2;
  position: absolute;
  top: 16px;
  left: 56%;
  right: -44%;
}

.calibration-workspace {
  display: grid;
  grid-template-columns: 2.18fr 1fr;
  gap: 14px;
}

.calibration-previews {
  display: grid;
  gap: 14px;
  grid-template-columns: 1fr 1fr;
}

.calibration-previews :deep(.device-preview) {
  height: 300px;
  min-height: 300px;
  aspect-ratio: auto;
  border-radius: 9px;
  background: linear-gradient(145deg,#eaf2fa,#dce8f6);
}

.calibration-previews :deep(.product-empty strong) {
  font-size: 17px;
}

.calibration-previews :deep(.product-empty p) {
  font-size: 14px;
}

.calibration-geometry {
  height: 180px;
  width: 67%;
  display: block;
  flex-shrink: 0;
}

.coordinate-caption {
  flex: 1;
  border-left: 1px solid #dce3ef;
  padding-left: 18px;
  min-width: 0;
}

.calibration-save-note svg {
  flex-shrink: 0;
  color: #0868ff;
}

.calibration-controls {
  margin-top: 14px;
  padding: 20px;
}

.controls-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-top: 20px;
}

.controls-grid .capture-controls {
  margin: 0;
}

.controls-grid>form {
  border: 1px solid #dce3ef;
  padding: 20px;
  border-radius: 9px;
}

.calibration-editor {
  padding: 20px;
  margin: 14px 0;
}

.job-status {
  display: flex;
  gap: 12px;
  padding: 17px;
  margin-top: 18px;
  border: 1px solid #dbe8fc;
  border-radius: 8px;
  background: #f3f7ff;
  color: #4a79b9;
  font-size: 14px;
  margin: 14px 0 0;
}

.calibration-page {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 100%;
  color: #111827;
}

.calibration-page>.device-tabs {
  min-height: 76px;
  align-items: center;
  margin: 0;
  border: 0;
  padding: 0 22px;
  height: 76px;
  gap: 0;
}

.calibration-page>.device-tabs a, .device-tabs .current-tab {
  border: 1px solid #e1e7f2;
  height: 45px;
  min-width: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 20px;
  border-color: #dce3ef;
  font-size: 19px;
  font-weight: 650;
  color: #273149;
}

.calibration-page>.device-tabs a:first-child {
  border-radius: 8px 0 0 8px;
}

.calibration-page>.device-tabs a:last-child {
  border-left: 0;
  border-radius: 0 8px 8px 0;
}

.calibration-page>.device-tabs .active {
  border-color: #d3e4ff;
  background: #dbe8ff;
  color: #0868ff;
  border-radius: 8px;
  border-bottom: 2px solid #0868ff;
}

.calibration-summary>header h2 {
  font-size: 28px;
  font-weight: 700;
  color: #111827;
}

.calibration-summary>header {
  height: 60px;
  margin: 0 10px;
  border-bottom: 1px solid #dce3ef;
  border-color: #d9e0eb;
}

.calibration-steps li {
  flex: 1;
  position: relative;
  display: flex;
  align-items: center;
  flex-direction: column;
  gap: 11px;
  font-size: 20px;
  color: #111827;
}

.calibration-steps strong {
  font-weight: 650;
}

.calibration-steps li>span {
  border-radius: 50%;
  display: flex;
  justify-content: center;
  align-items: center;
  color: #7a91af;
  border: 1px solid #dce6f5;
  position: relative;
  z-index: 1;
  width: 32px;
  height: 32px;
  font-size: 18px;
  background: #edf3ff;
  border-color: #d4e0f3;
}

.calibration-steps li>span.complete {
  color: white;
  background: #0868ff;
  border-color: #0868ff;
}

.calibration-steps li>span.current {
  color: white;
  background: #0868ff;
  border-color: #0868ff;
}

.calibration-steps li:has(.complete):before {
  background: #0868ff;
  height: 2px;
}

.calibration-steps li:has(.current)>strong {
  color: #0868ff;
}

.capture-workspace {
  border: 1px solid #dce3ef;
  border-radius: 12px;
  padding: 18px 14px 14px;
  min-height: 540px;
  border-color: #dce3ee;
}

.validation-panel {
  border: 1px solid #e3eaf4;
  flex-direction: column;
  padding: 19px 18px;
  border-radius: 12px;
  display: flex;
  min-height: 540px;
  border-color: #dce3ee;
}

.calibration-previews :deep(.preview-label) {
  border: 0;
  font-size: 18px;
  font-weight: 600;
  color: #fff;
  background: #39485bdc;
  padding: 5px 11px;
}

.coordinate-block {
  border: 1px solid #e3ebf7;
  color: #4b79b9;
  height: 192px;
  margin-top: 16px;
  padding: 10px 18px;
  gap: 10px;
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  border-radius: 10px;
  background: linear-gradient(135deg,#fbfcff,#f4f7fd);
  border-color: #e0e6f1;
}

.coordinate-caption strong {
  white-space: nowrap;
  font-size: 18px;
  font-weight: 650;
  color: #111827;
}

.coordinate-caption span {
  white-space: nowrap;
  border-radius: 6px;
  padding: 9px 12px;
  margin-top: 6px;
  display: block;
  font-size: 17px;
  font-weight: 600;
  color: #526079;
  background: #e7edf7;
}

.quality-title {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  min-height: 77px;
  background: #edf3fa;
  color: #69829f;
  border-radius: 10px;

  height: 77px;
}

.quality-title strong {
  font-size: 28px;
  font-weight: 700;

  line-height: 1.3;
}

.quality-title.passed {
  color: #116e1b;
  background: linear-gradient(110deg,#e3f7e5,#e6f9e8);
}

.quality-title.passed svg {
  width: 40px;
  height: 40px;
  padding: 6px;
  border-radius: 50%;
  background: #13a523;
  color: #fff;
  stroke-width: 3;
}

.quality-values {
  margin: 16px 8px 0;
  font-size: 20px;
}

.quality-values>div {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #dce3ef;
  gap: 12px;
  height: 52px;
  align-items: center;
  padding: 0;
  border-color: #d2dae8;
}

.quality-values dt {
  font-size: 19px;
  font-weight: 550;
  color: #111827;
}

.quality-values dd {
  margin: 0;
  font-size: 20px;
  font-weight: 500;
  color: #111827;
}

.calibration-save-note {
  display: flex;
  gap: 12px;
  margin: 32px 0 20px;
  color: #34619c;
  align-items: flex-start;
  padding: 14px 18px;
  background: #edf3ff;
  border-radius: 10px;
}

.calibration-save-note p {
  margin: 0;
  font-size: 16px;
  font-weight: 500;
  line-height: 1.65;
  color: #35415a;
}

.calibration-state {
  padding: 5px 10px;
  font-size: 17px;
  font-weight: 500;
  color: #48556d;
}

.calibration-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 18px;
  margin-top: 16px;
  padding: 8px;
  border: 1px solid #dce3ef;
  border-radius: 11px;
  height: 62px;
  border-color: #dce3ee;
}

.calibration-footer .btn {
  height: 43px;
  min-width: 120px;
  font-size: 18px;
  font-weight: 600;
  border-radius: 8px;
}

.calibration-controls>summary {
  cursor: pointer;
  font-size: 17px;
  font-weight: 600;
  color: #253046;
}

.calibration-editor>summary {
  cursor: pointer;
  font-size: 17px;
  font-weight: 600;
  color: #253046;
}

.controls-grid .form-field {
  font-size: 16px;
  color: #111827;
}

.job-status strong {
  font-size: 16px;
  font-weight: 600;
}

.job-status p {
  line-height: 1.8;
  margin: 7px 0 0;
  overflow-wrap: anywhere;
  font-size: 15px;
}

.job-status span {
  display: block;
  margin-top: 10px;
  font-size: 15px;
}

@media (max-width:1350px) {
  .calibration-workspace {
    grid-template-columns: 2fr 1fr;
  }

  .calibration-geometry {
    width: 58%;
  }

  .coordinate-caption {
    padding-left: 10px;
  }

  .coordinate-caption strong {
    font-size: 14px;
  }

  .coordinate-caption span {
    font-size: 13px;
    padding: 8px;
  }

  .quality-values {
    font-size: 17px;
  }

  .quality-title strong {
    font-size: 22px;
  }

  .calibration-save-note p {
    font-size: 14px;
  }

  .calibration-previews :deep(.device-preview) {
    height: 280px;
    min-height: 280px;
  }

  .capture-workspace {
    min-height: 520px;
  }

  .validation-panel {
    min-height: 520px;
  }
}

@media (max-width:1050px) {
  .calibration-workspace {
    grid-template-columns: 1fr;
  }

  .validation-panel {
    min-height: 400px;
  }

  .quality-values {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0 25px;
  }

  .controls-grid {
    grid-template-columns: 1fr;
  }

  .coordinate-caption strong {
    font-size: 17px;
  }

  .coordinate-caption span {
    font-size: 16px;
  }
}

.calibration-page>.device-tabs .current-tab{cursor:default;border:1px solid #dce3ef;text-decoration:none}.calibration-page>.device-tabs .current-tab.active{border-bottom:2px solid #0868ff}
</style>
