<template><div class="models-page"><div class="page-heading"><div><h1>系统设置</h1><p class="muted">管理检测模型与运行参数</p></div></div><SettingsTabs active="models"/><p v-if="error" class="error-banner" role="alert">{{error}}</p><div class="models-heading"><h2>检测模型</h2><div class="model-heading-actions"><button class="btn" @click="load" :disabled="busy"><RefreshCw :size="16"/>刷新</button><button class="btn primary" :disabled="busy" @click="uploadInput?.click()"><Upload :size="16"/>{{uploading?'正在上传…':'上传模型'}}</button><input ref="uploadInput" type="file" accept=".onnx" hidden @change="upload" :disabled="busy"/></div></div><div class="model-grid"><section class="panel model-deployment"><h3>模型部署</h3><div class="model-identity"><div class="model-icon"><DroneGlyph width="60" height="38"/></div><div><h2>{{activeVersion?.name||'无人机检测模型'}}</h2><span class="badge" :class="ready?'success':'neutral'">{{ready?(store.status.detecting?'运行中':'模型就绪'):'等待推理服务'}}</span></div></div><dl class="model-facts"><div><dt>模型文件</dt><dd>{{activeVersion?.name||filename(config.modelPath)||'未配置'}}</dd></div><div><dt>推理引擎</dt><dd>{{provider}}</dd></div><div><dt>运行精度</dt><dd>{{store.status.precision==='unreported'?'未上报':store.status.precision}}</dd></div><div><dt>检测类别</dt><dd>无人机</dd></div></dl><h3 class="version-title">版本列表</h3><div class="table-wrap"><table><thead><tr><th>模型文件</th><th>大小</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="version in versions" :key="version.id"><td><strong>{{version.name}}</strong></td><td>{{bytesLabel(version.bytes)}}</td><td><span class="badge" :class="version.active?'blue':version.validatedAt?'success':'neutral'">{{version.active?'当前版本':version.validatedAt?'已验证':'待验证'}}</span></td><td><div class="version-actions"><button class="btn small" @click="validate(version)" :disabled="busy">{{workingId===version.id&&workingAction==='validate'?'验证中…':'验证'}}</button><button v-if="!version.active" class="btn small" :disabled="busy||!version.validatedAt" :title="version.validatedAt?'切换为当前模型':'须先完成模型结构验证'" @click="activate(version)">启用</button></div></td></tr></tbody></table></div><ProductEmpty v-if="!versions.length&&!loading" title="暂无模型文件" description="上传 ONNX 文件并完成验证后可启用"/><div class="model-note"><Info :size="16"/><p>模型启用后将重启推理服务。上传与验证不会切换当前运行模型。</p></div></section><form id="model-parameters-form" ref="modelForm" class="panel model-parameters" @submit.prevent="save"><h3>模型参数</h3><label class="parameter-label" for="confidence">最低模型分数<span>{{thresholdLabel}}</span></label><div class="threshold-control"><input id="confidence" v-model.number="config.threshold" :style="{background:'linear-gradient(to right,#0868ff 0%,#0868ff '+((Number(config.threshold)||0)*100)+'%,#e0e5ee '+((Number(config.threshold)||0)*100)+'%,#e0e5ee 100%)'}" type="range" min=".01" max=".99" step=".01"/><input v-model.number="config.threshold" aria-label="最低模型分数数值" type="number" min=".01" max=".99" step=".01" required/></div><p class="parameter-help">低于此分数的目标不进入检测结果。</p><label class="form-field">每路检测上限<input v-model.number="config.detectionFps" type="number" min=".5" max="30" step=".5" required/><span class="field-unit">FPS</span></label><label class="form-field">短时位置保持<input v-model.number="config.holdSeconds" type="number" min="0" max="3" step=".05" required/><span class="field-unit">秒</span></label><p class="parameter-help">短时漏检期间保留最近一次有效位置，超过设定时间后清除。</p><details class="model-advanced"><summary>参数说明</summary><p>低于最低分数的目标不进入检测结果。短时位置保持用于减少漏检闪烁，更新参数后推理服务将重新载入配置。</p></details><div class="parameter-action"><button class="btn" type="button" :disabled="busy||!loaded||!dirty" :title="dirty?'恢复最近保存的参数':'尚未修改参数'" @click="reset">撤销修改</button><button class="btn primary" :disabled="busy||!loaded">{{saving?'正在保存…':'保存配置'}}</button></div></form></div><section class="panel performance"><h3>运行表现 <span>（实时）</span></h3><div class="performance-grid"><div><div class="performance-icon"><Cpu :size="30"/></div><div><span>单帧推理</span><strong>{{latency}} <small>ms</small></strong></div></div><div><div class="performance-icon"><Activity :size="25"/></div><div><span>实际检测帧率</span><strong>{{store.status.cameras.camera1.detectionFps.toFixed(1)}} <small>FPS</small></strong></div></div><div><div class="performance-icon"><CheckCircle2 :size="25"/></div><div><span>最近验证</span><strong class="validation-value">{{activeVersion?.validatedAt?dateTime(activeVersion.validatedAt):'尚无验证记录'}}</strong></div></div></div></section><div class="page-actions"><button class="btn" :disabled="busy||!loaded||!dirty" :title="dirty?'恢复最近保存的参数':'尚未修改参数'" @click="reset">撤销修改</button><button class="btn primary" type="submit" form="model-parameters-form" :disabled="busy||!loaded">{{saving?'正在保存…':'保存配置'}}</button></div></div></template>
<script setup lang="ts">
import{computed,onMounted,ref}from'vue';import{RefreshCw,Upload,ScanLine,Info,Cpu,Activity,CheckCircle2}from'lucide-vue-next';import{http}from'@/api/http';import{useCameraStore}from'@/stores/camera';import SettingsTabs from'@/components/SettingsTabs.vue';import DroneGlyph from'@/components/DroneGlyph.vue';import ProductEmpty from'@/components/ProductEmpty.vue';import{bytesLabel,errorMessage,dateTime}from'@/lib/product-format';
interface ModelConfig{threshold:number;detectionFps:number;holdSeconds:number;modelPath:string}
type ModelDraft=Omit<ModelConfig,'threshold'|'detectionFps'|'holdSeconds'>&{threshold:number|'';detectionFps:number|'';holdSeconds:number|''};
interface Version{id:string;name:string;active:boolean;bytes:number;validatedAt?:number}
const modelForm=ref<HTMLFormElement|null>(null);const uploadInput=ref<HTMLInputElement|null>(null);const store=useCameraStore();const config=ref<ModelDraft>({threshold:.35,detectionFps:12.5,holdSeconds:.45,modelPath:''});let original:ModelDraft={...config.value};const versions=ref<Version[]>([]);const error=ref('');const loading=ref(false);const loaded=ref(false);const saving=ref(false);const uploading=ref(false);const workingId=ref('');const workingAction=ref('');
const busy=computed(()=>loading.value||saving.value||uploading.value||!!workingId.value);const activeVersion=computed(()=>versions.value.find(v=>v.active));const ready=computed(()=>store.status.engineState==='ready'&&store.status.serviceState==='ready');const latency=computed(()=>store.status.cameras.camera1.latency?.toFixed(1)||'—');const provider=computed(()=>store.status.inferenceProvider==='unreported'?'未上报':store.status.inferenceProvider);const filename=(path:string)=>path.split(/[\\/]/).pop();
async function load(){loading.value=true;error.value='';try{const{data}=await http.get('/models/config');config.value={...config.value,...data.config};original={...config.value};versions.value=data.versions;loaded.value=true;await store.refreshStatus()}catch(e){error.value=errorMessage(e)}finally{loading.value=false}}
const thresholdLabel=computed(()=>typeof config.value.threshold==='number'&&Number.isFinite(config.value.threshold)?config.value.threshold.toFixed(2):'—');
const dirty=computed(()=>JSON.stringify(config.value)!==JSON.stringify(original));
function reset(){if(!loaded.value||busy.value)return;config.value={...original};error.value=''}
async function save(){
 if(busy.value||!loaded.value)return;
 error.value='';
 if(!modelForm.value?.reportValidity())return;
 const{threshold,detectionFps,holdSeconds,modelPath}=config.value;
 if(typeof threshold!=='number'||!Number.isFinite(threshold)||threshold<.01||threshold>.99){error.value='最低模型分数须为 0.01 至 0.99 的有效数值';return}
 if(typeof detectionFps!=='number'||!Number.isFinite(detectionFps)||detectionFps<.5||detectionFps>30){error.value='每路检测上限须为 0.5 至 30 FPS 的有效数值';return}
 if(typeof holdSeconds!=='number'||!Number.isFinite(holdSeconds)||holdSeconds<0||holdSeconds>3){error.value='短时位置保持须为 0 至 3 秒的有效数值';return}
 const payload:ModelConfig={threshold,detectionFps,holdSeconds,modelPath};
 saving.value=true;
 try{const{data}=await http.put('/models/config',payload);store.toast(data.applied===false?(data.message||'配置已保存，等待服务恢复后生效'):'模型参数已保存并应用',data.applied===false?'info':'success');await load()}catch(e){error.value=errorMessage(e)}finally{saving.value=false}}
async function upload(event:Event){if(busy.value)return;const input=event.target as HTMLInputElement;const file=input.files?.[0];if(!file)return;input.value='';if(!file.name.toLowerCase().endsWith('.onnx')||file.size>128*1024*1024){error.value='请选择不超过 128 MB 的 ONNX 文件';return}uploading.value=true;error.value='';try{await http.post('/models/upload',file,{headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},timeout:120000});store.toast('模型已上传，请完成验证后启用','success');await load()}catch(e){error.value=errorMessage(e)}finally{uploading.value=false}}
async function validate(version:Version){if(busy.value)return;workingId.value=version.id;workingAction.value='validate';error.value='';try{await http.post('/models/'+encodeURIComponent(version.id)+'/validate',{}, {timeout:120000});store.toast('模型结构验证通过','success');await load()}catch(e){error.value=errorMessage(e)}finally{workingId.value='';workingAction.value=''}}
async function activate(version:Version){if(busy.value)return;if(!version.validatedAt){error.value='请先完成模型结构验证';return;}if(!window.confirm('启用 '+version.name+' 将重启推理服务，是否继续？'))return;workingId.value=version.id;workingAction.value='activate';error.value='';try{const{data}=await http.post('/models/'+encodeURIComponent(version.id)+'/activate',{}, {timeout:60000});store.toast(data.applied===false?(data.message||'模型已选择，等待服务恢复后生效'):'模型已切换',data.applied===false?'info':'success');await load()}catch(e){error.value=errorMessage(e)}finally{workingId.value='';workingAction.value=''}}
onMounted(load);
</script><style scoped>
.model-heading-actions {
  display: flex;
  gap: 10px;
}

.model-heading-actions label {
  cursor: pointer;
}

.model-heading-actions label.disabled {
  opacity: .5;
  cursor: not-allowed;
}

.version-actions {
  display: flex;
  gap: 5px;
}

.model-note svg {
  flex-shrink: 0;
}

.model-note p {
  margin: 0;
}

.parameter-label span {
  color: #1265f1;
  font-variant-numeric: tabular-nums;
}

.form-field {
  position: relative;
}

.form-field input {
  padding-right: 55px !important;
}

.performance-grid>div:first-child {
  padding-left: 0;
}

.performance-grid>div:last-child {
  border: 0;
}

.models-page>.page-heading {
  height: 76px;
  min-height: 76px;
  margin: 0;
  padding: 0 16px;
}

.models-page>.page-heading p {
  display: none;
}

.models-page :deep(.settings-tabs) {
  height: 41px;
  min-height: 41px;
  margin: 0 0 16px;
  padding: 0 16px;
}

.models-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  height: 58px;
  min-height: 58px;
  margin: 0 14px 16px;
}

.model-grid {
  display: grid;
  grid-template-columns: 1.18fr 1fr;
  gap: 18px;
  height: 428px;
  min-height: 428px;
  padding: 0 16px;
}

.model-deployment {
  min-width: 0;
  padding: 16px 20px;

  min-height: 0;
}

.model-identity {
  display: flex;
  align-items: center;
  margin-bottom: 30px;
  margin: 0 0 24px;
  gap: 20px;
}

.model-facts>div:first-child {
  padding-left: 0;
}

.model-facts>div:last-child {
  border: 0;
}

.model-deployment table {
  font-size: 15px;
}

.model-deployment .badge {
  font-size: 13px;
}

.model-deployment :deep(.product-empty) {
  padding: 8px;
  min-height: 70px;
  gap: 5px;
}

.model-deployment :deep(.product-empty svg) {
  display: none;
}

.model-deployment :deep(.product-empty strong) {
  font-size: 15px;
}

.model-deployment :deep(.product-empty p) {
  font-size: 13px;
}

.model-note {
  gap: 8px;
  align-items: start;
  font-size: 12px;
  color: #8a9ab0;
  margin-top: 22px;
  line-height: 1.7;
  display: none;
}

.model-parameters {
  min-width: 0;
  flex-direction: column;
  padding: 16px 20px;
  display: grid;
  grid-template-columns: 185px minmax(0,1fr);
  grid-template-rows: 39px 70px 70px 70px minmax(0,1fr);
  align-items: center;
  gap: 0 12px;

  min-height: 0;
}

.parameter-label>span {
  display: none;
}

.threshold-control {
  display: flex;
  align-items: center;
  grid-column: 2;
  grid-row: 2;
  gap: 27px;
}

.parameter-help {
  font-size: 12px;
  color: #8b9bb0;
  line-height: 1.9;
  margin: 12px 0 30px;
  display: none;
}

.parameter-action {
  justify-content: flex-end;
  gap: 10px;
  margin-top: auto;
  padding-top: 35px;
  display: none;
}

.performance {
  margin-top: 20px;
  height: 144px;
  min-height: 144px;
  padding: 12px 20px;
  margin: 16px 10px 0;
}

.performance-icon {
  border-radius: 10px;
  background: #eaf2ff;
  color: #2a73e5;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 54px;
  height: 54px;
}

.page-actions {
  display: flex;
  justify-content: flex-end;
  gap: 18px;
  margin-top: auto;
  padding: 22px 16px 10px;
  min-height: 64px;
}

.models-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  color: #111827;
}

.models-heading h2 {
  margin: 0;
  font-size: 27px;
  font-weight: 700;
  color: #111827;
}

.model-deployment h3 {
  border-bottom: 1px solid #e4eaf3;
  margin: 0 0 14px;
  padding-bottom: 9px;
  line-height: 1.25;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
  border-color: #d9e0eb;
}

.model-parameters h3 {
  border-bottom: 1px solid #e4eaf3;
  margin: 0 0 14px;
  padding-bottom: 12px;
  line-height: 1.25;
  grid-column: 1/-1;
  align-self: start;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
  border-color: #d9e0eb;
}

.performance h3 {
  border-bottom: 1px solid #e4eaf3;
  padding-bottom: 11px;
  margin: 0 0 15px;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
  border-color: #d9e0eb;
}

.model-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 83px;
  height: 70px;
  border-radius: 9px;
  background: linear-gradient(135deg,#d4e6ff,#dceaff);
  color: #163458;
}

.model-identity h2 {
  overflow-wrap: anywhere;
  margin: 0 0 8px;
  font-size: 24px;
  font-weight: 700;
  color: #111827;

  line-height: 1.25;
}

.model-identity .badge {
  border-radius: 20px;
  font-size: 16px;
  font-weight: 600;
  padding: 4px 12px;
  border: 1px solid #bce9cf;

  line-height: 1.25;
}

.model-facts {
  padding-bottom: 14px;
  display: grid;
  grid-template-columns: repeat(4,minmax(0,1fr));
  gap: 0;
  margin: 0 0 13px;
  padding: 0 0 17px;
  border-bottom: 1px solid #dce3ef;
  border-color: #d9e0eb;
}

.model-facts>div {
  padding-left: 22px;
  border-right: 1px solid #dce3ef;
  border-color: #d3dce9;
}

.model-facts dt {
  font-size: 16px;
  font-weight: 500;
  color: #4c566e;

  line-height: 1.3;
}

.model-facts dd {
  overflow-wrap: anywhere;
  margin: 8px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 18px;
  font-weight: 550;
  color: #111827;

  line-height: 1.3;
}

.model-deployment .version-title {
  border: 0;
  padding: 0;
  margin: 0 0 10px;
  font-size: 22px;
  font-weight: 700;
}

.model-deployment .table-wrap {
  overflow: auto;
  max-height: 132px;
  border-radius: 8px;
}

.model-deployment th {
  font-size: 16px;
  font-weight: 600;
  color: #253046;
  background: linear-gradient(#f0f3fa,#eaf0f9);
  padding: 8px 16px;

  line-height: 1.25;
}

.model-deployment td {
  font-size: 16px;
  font-weight: 500;
  color: #111827;
  padding: 7px 16px;
  border-color: #dce3ed;

  line-height: 1.3;
}

.model-deployment td strong {
  font-weight: 600;
}

.model-deployment td .badge {
  font-size: 14px;
  font-weight: 600;
  padding: 4px 9px;
  border-radius: 6px;

  line-height: 1.2;
}

.btn.small {
  min-height: 29px;
  font-size: 15px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 7px;
  height: 32px;
}

.parameter-label {
  display: flex;
  justify-content: space-between;
  grid-column: 1;
  grid-row: 2;
  margin: 0;
  font-size: 19px;
  font-weight: 550;
  color: #111827;
}

.model-parameters>.form-field {
  grid-column: 1/-1;
  display: grid;
  grid-template-columns: 185px minmax(0,1fr);
  align-items: center;
  gap: 12px;
  margin: 0;
  font-size: 19px;
  font-weight: 550;
  color: #111827;
}

.threshold-control input[type=range] {
  flex: 1;
  min-width: 0;
  appearance: none;
  -webkit-appearance: none;
  height: 7px;
  background: #e0e5ee;
  border: 0;
  border-radius: 6px;
  padding: 0;
  accent-color: #0868ff;
}

.threshold-control input[type=range]::-webkit-slider-thumb {
  appearance: none;
  -webkit-appearance: none;
  width: 19px;
  height: 19px;
  border: 2px solid #0868ff;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 0 2px #fff;
}

.threshold-control input[type=number] {
  border: 1px solid #dae3ef;
  font: inherit;
  width: 106px;
  padding: 7px 12px;
  font-size: 18px;
  font-weight: 500;
  color: #111827;
  border-color: #c8d1e1;
  background: #fbfcff;
  border-radius: 8px;
}

.model-parameters>.form-field input {
  height: 39px;
  font-size: 18px;
  font-weight: 500;
  color: #111827;
  border-color: #c8d1e1;
  background: #fbfcff;
  border-radius: 8px;
}

.field-unit {
  position: absolute;
  right: 14px;
  bottom: auto;
  top: 11px;
  font-size: 15px;
  font-weight: 500;
  color: #5f6b82;
}

.model-advanced {
  grid-column: 1/-1;
  align-self: start;
  border-top: 1px solid #dce3ef;
  margin-top: 15px;
  padding-top: 22px;
  font-size: 16px;
  border-color: #d9e0eb;
}

.model-advanced summary {
  padding: 16px;
  cursor: pointer;
  background: linear-gradient(110deg,#f0f4fb,#eaf0fa);
  font-size: 18px;
  font-weight: 600;
  color: #111827;
  border-radius: 9px;
}

.model-advanced p {
  line-height: 1.7;
  padding: 12px;
  background: white;
  position: relative;
  z-index: 2;
  border: 1px solid #e0e7f1;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 500;
  color: #5f6b82;
}

.performance h3 span {
  margin-left: 8px;
  font-size: 21px;
  font-weight: 700;
  color: #111827;
}

.performance-grid {
  display: grid;
  grid-template-columns: 1fr 1.55fr 1.1fr;
}

.performance-grid>div {
  display: flex;
  align-items: center;
  border-right: 1px solid #e5ebf3;
  padding: 0 30px;
  gap: 20px;
  min-height: 60px;
  border-color: #d3dce9;
}

.performance-grid span {
  font-size: 16px;
  font-weight: 550;
  color: #253046;
}

.performance-grid strong {
  display: block;
  margin-top: 5px;
  line-height: 1;
  font-size: 28px;
  font-weight: 700;
  color: #111827;
}

.performance-grid small {
  font-size: 17px;
  font-weight: 550;
  color: #111827;
}

.performance-grid .validation-value {
  line-height: 1.6;
  font-size: 17px;
  font-weight: 550;
  color: #111827;
}

.performance-grid>div:last-child .performance-icon {
  background: #e3f5e9;
  color: #1f8a39;
}

.page-actions .btn {
  min-width: 137px;
  height: 43px;
  font-size: 18px;
  font-weight: 600;
  border-radius: 8px;
}

@media (max-width:1250px) {
  .model-grid {
    padding: 0;
    grid-template-columns: 1.15fr 1fr;
    gap: 12px;
  }

  .model-parameters {
    grid-template-columns: 145px minmax(0,1fr);
  }

  .parameter-label {
    font-size: 16px;
  }

  .model-parameters>.form-field {
    grid-template-columns: 145px minmax(0,1fr);
    font-size: 16px;
  }

  .model-facts>div {
    padding-left: 12px;
  }

  .model-facts dt {
    font-size: 14px;
  }

  .model-facts dd {
    font-size: 15px;
  }

  .model-deployment th {
    font-size: 13px;
    padding: 9px;
  }

  .model-deployment td {
    font-size: 13px;
    padding: 9px;
  }

  .performance-grid>div {
    gap: 12px;
    padding: 0 20px;
  }
}

@media (max-width:1000px) {
  .models-page {
    height: auto;
  }

  .model-grid {
    height: auto;
    grid-template-columns: 1fr;
  }

  .model-deployment {
    min-height: 428px;
  }

  .model-parameters {
    min-height: 428px;
  }

  .performance {
    height: auto;
  }

  .page-actions {
    margin: 0;
  }
}
</style>
