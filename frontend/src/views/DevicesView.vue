<template><div class="devices-page"><div class="page-heading"><div><h1>设备管理</h1><p class="muted">管理双目摄像机连接与运行状态</p></div><button class="btn" :disabled="loading" @click="load"><RefreshCw :size="16"/>刷新状态</button></div><p v-if="error" role="alert" class="error-banner">{{error}}</p><div class="device-workspace"><aside class="panel device-tree"><strong><Building2 :size="17"/>校园监测</strong><button v-for="item in devices" :key="item.id" :class="{active:selected?.id===item.id}" @click="select(item)"><Video :size="16"/><span>{{item.label?.replace(/^C[12]\s*/,'')||cameraName(item.id)}}</span><i :class="{online:online(item.id)}"/></button></aside><section class="panel device-table-panel"><div class="device-counters"><div><span>已接入</span><strong>{{devices.length}}</strong></div><div><span><i class="online"/>在线</span><strong>{{devices.filter(d=>online(d.id)).length}}</strong></div><div><span><i/>离线</span><strong>{{devices.filter(d=>!online(d.id)).length}}</strong></div></div><div class="table-wrap"><table><thead><tr><th class="device-selection-heading">查看</th><th>设备</th><th>状态</th><th>分辨率</th><th>解码帧率</th><th>网络延迟</th><th>视频源</th></tr></thead><tbody><tr v-for="item in devices" :key="item.id" :class="{selected:selected?.id===item.id}" @click="select(item)" tabindex="0" @keydown.enter="select(item)"><td><input type="radio" name="device-inspector" :aria-label="'查看 '+cameraName(item.id)" :checked="selected?.id===item.id" @click.stop="select(item)"/></td><td><strong>{{cameraName(item.id)}}</strong><span class="device-name">{{item.label?.replace(/^C[12]\s*/,'')||'摄像机'}}</span></td><td><span class="badge" :class="online(item.id)?'success':'neutral'">{{online(item.id)?'在线':'离线'}}</span></td><td>{{resolution(item.id)}}</td><td>{{runtime(item.id).decodeFps.toFixed(1)}} FPS</td><td>{{connectionHistory[item.id]?.at(-1)?.latency??'—'}} ms</td><td>RTSP</td></tr></tbody></table></div><ProductEmpty v-if="!devices.length&&!loading" title="暂无设备配置" description="请检查服务连接后重新读取"/><div class="device-help"><Info :size="17"/><p>双目定位需要 C1 与 C2 同时在线。变更连接参数后，请核对实时画面与定位状态。</p></div></section><aside class="panel inspector"><template v-if="selected"><div class="inspector-title"><h2>{{cameraName(selected.id)}}&nbsp; {{selected.label?.replace(/^C[12]\s*/,'')||'摄像机'}}</h2><span class="badge" :class="online(selected.id)?'success':'neutral'">{{online(selected.id)?'运行正常':'未连接'}}</span></div><DevicePreview :camera-id="selected.id" :label="selected.label"/><div class="inspector-tabs"><button :class="{active:mode==='info'}" @click="mode='info'">运行状态</button><button :class="{active:mode==='edit'}" @click="mode='edit'">编辑配置</button></div><dl v-if="mode==='info'" class="device-facts"><div><dt>设备地址</dt><dd>{{selected.host||'未配置'}}</dd></div><div><dt>视频通道</dt><dd>{{selected.channel}}</dd></div><div><dt>{{selected.stream==='sub'?'子码流':'主码流'}}</dt><dd>{{resolution(selected.id)}} / {{runtime(selected.id).decodeFps.toFixed(1)}} FPS</dd></div><div><dt>预览帧率</dt><dd>{{runtime(selected.id).previewFps.toFixed(1)}} FPS</dd></div><div><dt>检测帧率</dt><dd>{{runtime(selected.id).detectionFps.toFixed(1)}} FPS</dd></div><div><dt>重连次数</dt><dd>{{runtime(selected.id).reconnects}}</dd></div><div><dt>诊断信息</dt><dd>{{runtime(selected.id).error|| (online(selected.id)?'连接稳定':'等待设备接入')}}</dd></div></dl><form v-else @submit.prevent="save"><fieldset :disabled="saving" class="device-edit-fields"><label class="form-field">设备名称<input v-model="form.label" required maxlength="80"/></label><div class="form-grid"><label class="form-field">设备地址<input v-model="form.host" required placeholder="IP 地址或主机名"/></label><label class="form-field">RTSP 端口<input v-model.number="form.port" type="number" min="1" max="65535" required/></label></div><label class="form-field">设备账号<input v-model="form.username" required autocomplete="off"/></label><label class="form-field">设备密码<input v-model="form.password" type="password" autocomplete="new-password" :placeholder="selected.passwordConfigured?'留空保留当前密码':'请输入密码'"/></label><div class="form-grid"><label class="form-field">视频通道<input v-model.number="form.channel" type="number" min="1" max="64" required/></label><label class="form-field">码流<select v-model="form.stream"><option value="main">主码流</option><option value="sub">子码流</option></select></label></div><div class="save-row"><button class="btn" type="button" @click="select(selected!)">取消</button><button class="btn primary" :disabled="saving">{{saving?'正在保存…':'保存配置'}}</button></div></fieldset></form><div v-if="mode==='info'" class="device-buttons"><button class="btn" :disabled="testing||!selected.host" :title="selected.host?'检测设备 RTSP 端口连通性':'请先配置设备地址'" @click="testConnection">{{testing?'检测中…':'测试端口'}}</button><button class="btn" @click="mode='edit'">编辑配置</button></div><section v-if="mode==='info'" class="connection-chart"><header><h3>连接质量</h3><span>最近采样</span></header><span>端口延迟（ms）</span><div class="chart-canvas"><svg viewBox="0 0 460 110" preserveAspectRatio="none"><polyline v-if="connectionHistory[selected.id]?.length" :points="connectionPoints(selected.id)" fill="none" stroke="#1671ff" stroke-width="2"/></svg><div v-if="!connectionHistory[selected.id]?.length" class="chart-empty">尚无连通性采样</div></div><footer>通过“测试端口”获取实际端口延迟</footer></section><section v-if="mode==='info'" class="device-recent"><h3>最近事件</h3><div><span class="dot" :class="{connected:online(selected.id)}"/>{{online(selected.id)?'连接稳定':'设备未连接'}}</div></section></template><ProductEmpty v-else title="选择设备查看详情"/></aside></div></div></template>
<script setup lang="ts">
import {onMounted,ref} from 'vue';import {RefreshCw,Building2,Video,Info} from 'lucide-vue-next';import {http} from '@/api/http';import {useCameraStore} from '@/stores/camera';import type{CameraId}from '@/types/camera-types';import {cameraName,errorMessage} from '@/lib/product-format';import ProductEmpty from '@/components/ProductEmpty.vue';import DevicePreview from '@/components/DevicePreview.vue';
interface Device{id:CameraId;label:string;host:string;port:number;username:string;channel:number;stream:string;passwordConfigured:boolean}
const store=useCameraStore();const connectionHistory=ref<Record<string,{time:number;latency:number}[]>>({});const devices=ref<Device[]>([]);const selected=ref<Device|null>(null);const loading=ref(false);const saving=ref(false);const testing=ref(false);const error=ref('');const mode=ref('info');const form=ref({label:'',host:'',port:554,username:'',password:'',channel:1,stream:'main'});
const runtime=(id:CameraId)=>store.status.cameras[id];const online=(id:CameraId)=>store.status.serviceState==='ready'&&runtime(id).state==='streaming';const resolution=(id:CameraId)=>runtime(id).width?runtime(id).width+' × '+runtime(id).height:'—';
function select(item:Device){selected.value=item;form.value={label:item.label,host:item.host,port:item.port,username:item.username,password:'',channel:item.channel,stream:item.stream};mode.value='info'}
async function load(){loading.value=true;error.value='';try{const{data}=await http.get('/devices');devices.value=data.devices;const item=devices.value.find(d=>d.id===selected.value?.id)||devices.value[1]||devices.value[0];if(item)select(item);await store.refreshStatus()}catch(e){error.value=errorMessage(e)}finally{loading.value=false}}
async function save(){if(!selected.value||saving.value)return;saving.value=true;error.value='';try{const payload={...form.value};if(!payload.password)delete (payload as Partial<typeof payload>).password;const{data}=await http.patch('/devices/'+selected.value.id,payload);store.toast(data.applied===false?(data.message||'设备配置已保存，等待服务恢复后生效'):'设备配置已保存',data.applied===false?'info':'success');await load()}catch(e){error.value=errorMessage(e)}finally{saving.value=false}}
async function testConnection(){if(!selected.value||testing.value)return;if(!selected.value.host){error.value='请先配置设备地址';return;}const id=selected.value.id;testing.value=true;error.value='';try{const{data}=await http.post('/devices/'+id+'/test');if(data.connected){connectionHistory.value[id]=[...(connectionHistory.value[id]||[]),{time:Date.now(),latency:data.latencyMs}].slice(-20)}store.toast(cameraName(id)+' · '+(data.connected?'RTSP 端口可达 · '+data.latencyMs+' ms':'设备端口无法连接，请检查网络与连接参数'),data.connected?'success':'error')}catch(e){error.value=errorMessage(e)}finally{testing.value=false}}
function connectionPoints(id:string){const points=connectionHistory.value[id]||[];const max=Math.max(40,...points.map(p=>p.latency));return points.map((point,index)=>((index/Math.max(1,points.length-1))*450+5)+','+(100-point.latency/max*90)).join(' ')}
onMounted(load);
</script><style scoped>
.device-tree button i {
  margin-left: auto;
}

i {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #bdc6d2;
}

.online {
  background: #11a66d;
}

tbody tr {
  cursor: pointer;
}

tbody tr:hover {
  background: #f8faff;
}

tbody tr.selected {
  background: #edf3ff;
}

.device-help svg {
  flex-shrink: 0;
}

.device-help p {
  margin: 0;
}

.inspector-tabs button {
  font: inherit;
  font-size: 13px;
  padding: 10px 0;
  color: #72839c;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  cursor: pointer;
}

.inspector-tabs button.active {
  color: #1265f1;
  border-bottom-color: #1265f1;
}

.form-grid {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: 12px;
}

.save-row {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.devices-page>.page-heading {
  height: 76px;
  min-height: 76px;
  margin: 0;
  padding: 0 16px;
}

.devices-page>.page-heading p {
  display: none;
}

.device-workspace {
  display: grid;
  flex: 1;
  grid-template-columns: 220px minmax(0,1fr) 506px;
  gap: 8px;
  min-height: 0;
}

.device-tree {
  padding: 12px 8px;
}

.device-table-panel {
  display: flex;
  flex-direction: column;
  padding: 16px 8px;
}

.device-name {
  margin-top: 6px;
  display: inline;
  font-size: 14px;
  color: #28364c;
  margin: 0;
}

.device-table-panel input[type=radio] {
  width: 17px;
  height: 17px;
  padding: 0;
}

.device-help {
  margin-top: auto;
  padding-top: 40px;
  gap: 10px;
  font-size: 12px;
  line-height: 1.8;
  color: #8c9bb0;
  display: none;
}

.inspector {
  padding: 12px 16px;
  overflow: auto;
  grid-column: auto;
}

.inspector-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  margin: 0 0 8px;
  min-height: 26px;
}

.inspector :deep(.device-preview) {
  max-width: none;
  width: 100%;
  margin: 0;
  float: none;
  aspect-ratio: 16/7;
  min-height: 205px;
}

.inspector-tabs {
  border-bottom: 1px solid #e5eaf2;
  gap: 28px;
  margin: 16px 0;
  display: none;
}

.device-facts {
  margin: 7px 0;
}

.device-facts>div:nth-child(4) {
  display: none;
}

.device-facts>div:nth-child(5) {
  display: none;
}

.inspector .badge {
  font-size: 13px;
}

.device-buttons {
  display: flex;
  gap: 9px;
  margin: 6px 0 10px;
}

.connection-chart header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.connection-chart .chart-canvas {
  position: relative;
}

.device-recent .dot {
  width: 7px;
  height: 7px;
  background: #aab8cc;
  border-radius: 50%;
}

.device-recent .dot.connected {
  background: #1ca668;
}

.inspector form {
  margin-top: 18px;
}

.connection-chart svg {
  width: 100%;
  display: block;
  background: repeating-linear-gradient(0deg,transparent,transparent 24px,#e6ebf3 25px);
  height: 76px;
}

.devices-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: #111827;
}

.device-tree>strong {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  border-radius: 6px;
  margin: 0 0 6px;
  padding: 11px 12px;
  background: linear-gradient(110deg,#deebff,#d8e7ff);
  color: #111827;
  font-size: 17px;
  font-weight: 650;
}

.device-tree button {
  display: flex;
  align-items: center;
  font: inherit;
  background: none;
  border: 0;
  width: 100%;
  cursor: pointer;
  border-radius: 6px;
  text-align: left;
  padding: 12px 26px;
  gap: 10px;
  padding-left: 16px;
  padding-right: 10px;
  color: #111827;
  font-size: 16px;
  font-weight: 600;
}

.device-tree button span {
  flex: 1;
  overflow-wrap: anywhere;
  white-space: nowrap;
  font-size: 16px;
}

.device-tree button.active {
  color: #0868ff;
  background: #e8f0ff;
}

.device-tree button svg {
  color: #485a76;
}

.device-tree button.active svg {
  color: #0868ff;
}

.device-counters {
  display: flex;
  gap: 14px;
  margin: 0 8px 28px;
}

.device-counters>div {
  border: 1px solid #e4eaf3;
  flex: 0 0 126px;
  padding: 11px 17px;
  border-radius: 10px;
  height: 73px;
  border-color: #e0e5ef;
  background: linear-gradient(135deg,#fff,#f9fbff);
}

.device-counters span {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 16px;
  font-weight: 550;
  color: #111827;
}

.device-counters strong {
  display: block;
  margin-top: 10px;
  margin: 5px 0 0 19px;
  line-height: 1;
  font-size: 28px;
  font-weight: 700;
  color: #111827;
}

.device-table-panel table {
  table-layout: auto;
}

.device-table-panel th {
  white-space: nowrap;
  font-size: 15px;
  font-weight: 650;
  padding: 13px 8px;
  background: linear-gradient(#f0f3fa,#eaf0f9);
  color: #111827;
  border: 0;
}

.device-table-panel td {
  white-space: nowrap;
  font-size: 15px;
  font-weight: 500;
  padding: 13px 8px;
  color: #111827;
  border-color: #dce3ed;
}

.device-table-panel td>strong {
  margin-right: 10px;
  font-weight: 650;
}

.device-table-panel td .device-name {
  font-size: 15px;
  font-weight: 500;
  color: #111827;
}

.device-table-panel tr.selected {
  background: linear-gradient(110deg,#dbe7ff,#e0eaff);
}

.device-table-panel td .badge {
  font-size: 14px;
  font-weight: 600;
}

.inspector-title h2 {
  margin: 0;
  line-height: 1.2;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
}

.inspector-title .badge {
  font-size: 14px;
  font-weight: 600;
  border: 1px solid #cef0dc;
  border-radius: 14px;
}

.device-facts>div {
  display: grid;
  gap: 10px;
  border-bottom: 1px solid #edf1f6;
  grid-template-columns: 145px minmax(0,1fr);
  font-size: 16px;
  padding: 3px 0;
  min-height: 28px;
  border-color: #dce3ed;
}

.device-facts dt {
  font-size: 16px;
  font-weight: 500;
  color: #111827;
}

.device-facts dd {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 16px;
  font-weight: 500;
  color: #111827;
}

.device-buttons .btn {
  flex: 1;
  margin: 0;
  padding: 7px 10px;
  font-size: 15px;
  font-weight: 600;
  border-color: #cdd6e5;
  border-radius: 8px;
}

.device-buttons .btn:first-child {
  color: #0868ff;
}

.connection-chart {
  border-top: 1px solid #dce3ef;
  padding-top: 10px;
  border-color: #d9e0eb;
}

.connection-chart h3 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #111827;
}

.connection-chart header span {
  border: 1px solid #d8e0ec;
  border-radius: 6px;
  padding: 5px 9px;
  font-size: 14px;
  font-weight: 500;
  color: #111827;
}

.connection-chart>span {
  display: block;
  margin: 9px 0;
  font-size: 13px;
  font-weight: 500;
  color: #35435c;
}

.connection-chart .chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 500;
  color: #5f6b82;
}

.connection-chart footer {
  margin-top: 3px;
  font-size: 12px;
  color: #5f6b82;

  line-height: 1.25;
}

.device-recent {
  border-top: 1px solid #dce3ef;
  margin-top: 9px;
  padding-top: 9px;
  border-color: #d9e0eb;
}

.device-recent h3 {
  margin: 0 0 11px;
  font-size: 20px;
  font-weight: 700;
  color: #111827;
  margin-bottom: 6px;
}

.device-recent>div {
  border: 1px solid #e0e7f1;
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 15px;
  font-weight: 500;
  color: #111827;
  padding: 7px 9px;
  border-color: #dce3ed;
  background: #f9fbff;
  border-radius: 7px;
}

.inspector .form-field {
  font-size: 16px;
  color: #111827;
}

@media (max-width:1400px) {
  .device-workspace {
    grid-template-columns: 180px minmax(440px,1fr) 420px;
    overflow: auto;
  }

  .device-counters>div {
    flex-basis: 110px;
  }

  .inspector {
    grid-column: auto;
  }

  .device-tree button {
    padding: 12px;
  }

  .inspector :deep(.device-preview) {
    width: 100%;
    float: none;
    min-height: 200px;
  }

  .device-table-panel td {
    font-size: 13px;
    padding: 13px 7px;
  }

  .device-table-panel th {
    font-size: 13px;
    padding: 13px 7px;
  }
}

@media (max-width:1000px) {
  .devices-page {
    height: auto;
  }

  .device-workspace {
    grid-template-columns: 160px 1fr;
    overflow: visible;
  }

  .inspector {
    grid-column: 1/-1;
  }

  .device-tree {
    display: block;
  }

  .device-table-panel {
    min-height: 350px;
  }
}

.device-edit-fields{border:0;margin:0;padding:0;min-width:0}.device-selection-heading{font-size:13px!important}
</style>
