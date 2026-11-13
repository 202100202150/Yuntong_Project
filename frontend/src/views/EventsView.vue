<template><div class="events-page">
<div class="page-heading"><h1>事件中心</h1><div class="actions"><button class="btn" @click="load" :disabled="loading"><RefreshCw :size="16"/>刷新</button><button class="btn export-top" v-if="auth.can('export')" :disabled="!selected||exporting" :title="selected?'下载事件记录 JSON':'请先选择事件'" @click="exportEvidence"><Download :size="18"/>{{exporting?'正在导出…':'导出事件记录'}}</button></div></div>
<p v-if="error" class="error-banner">{{error}}</p>
<div class="event-workspace">
<aside class="panel event-list">
        <div class="filter-switch"><button :class="{active:filter==='pending'}" @click="changeFilter('pending')">待复核 <span>{{pending}}</span></button><button :class="{active:filter==='all'}" @click="changeFilter('all')">全部事件</button></div>
        <form class="search-box" @submit.prevent="page=0;load()"><Search :size="16"/><input v-model="search" aria-label="搜索事件" placeholder="搜索事件或目标编号"/><button aria-label="执行搜索">↵</button></form>
        <div class="event-scroll" :aria-busy="loading">
          <button v-for="item in events" :key="item.id" class="event-row" :class="{selected:selected?.id===item.id}" @click="select(item)">
            <span class="event-drone"><DroneGlyph width="31" height="22"/></span><div class="event-row-body"><div class="event-row-title"><strong>{{item.trackId}}</strong><span class="badge" :class="item.status==='pending'?'warning':'success'">{{item.status==='pending'?'待复核':'已复核'}}</span></div><p>目标检测</p><time>{{dateTime(item.startedAt)}}</time></div><ChevronRight class="event-row-chevron" :size="16"/>
          </button>
          <ProductEmpty v-if="!events.length&&!loading" title="暂无事件" :description="filter==='pending'?'当前没有待复核的事件':'检测到目标后，事件记录会出现在这里'"/>
          <p v-if="loading" class="list-loading muted">正在读取事件…</p>
        </div><footer class="list-footer muted"><span>共 {{total}} 条记录</span><div class="pagination"><button class="btn small" :disabled="page===0||loading" @click="page--;load()" aria-label="上一页">‹</button><span>{{page+1}} / {{Math.max(1,Math.ceil(total/50))}}</span><button class="btn small" :disabled="(page+1)*50>=total||loading" @click="page++;load()" aria-label="下一页">›</button></div></footer>
      </aside>
<div class="evidence-column">
<section class="panel evidence">
<header class="evidence-title"><h2>事件复核</h2><div class="evidence-kind"><button :class="{active:evidenceKind==='image'}" @click="evidenceKind='image'">抓拍</button><button :class="{active:evidenceKind==='video'}" @click="evidenceKind='video'">录像</button></div></header>
<div class="evidence-tabs"><button v-for="tab in ['视频证据','空间轨迹','处置记录']" :key="tab" :class="{active:activeTab===tab}" @click="activeTab=tab">{{tab}}</button></div>
<template v-if="activeTab==='视频证据'">
<div class="evidence-videos"><div v-for="id in cameraIds" :key="id" class="evidence-video"><span class="video-label">{{cameraName(id)}} {{evidenceCameraLabel(id)}}</span><img v-if="evidenceKind==='image'&&imageFor(id)&&!evidenceFailures[imageFor(id)!.id]" :src="mediaUrl(imageFor(id)!.url)" :alt="cameraName(id)+' 抓拍资料'" class="evidence-image" @error="evidenceFailed(id,'image')"/><video v-else-if="evidenceKind==='video'&&videoFor(id)&&!evidenceFailures[videoFor(id)!.id]" :src="mediaUrl(videoFor(id)!.url)" controls preload="metadata" @error="evidenceFailed(id,'video')" :aria-label="cameraName(id)+' 事件录像'"/><div v-else class="video-unavailable"><VideoOff :size="32" :stroke-width="1.4"/><span>{{activeEvidenceFailed(id)?'资料读取失败，请刷新重试':selected?'暂无关联证据':'等待选择事件'}}</span></div><span v-if="evidenceKind==='video'&&videoFor(id)" class="evidence-origin">{{isExplicitEvidence(videoFor(id))?'事件关联录像':'时间邻近资料'}}</span><div v-if="evidenceKind==='image'" class="video-footer"><span>{{imageFor(id)?(isExplicitEvidence(imageFor(id))?'事件关联抓拍':'时间邻近资料'):'抓拍时间'}}</span><span>{{timeOnly(imageFor(id)?.startedAt)}}</span></div></div></div>
<div class="event-timeline"><div class="timeline-toolbar"><strong>证据时间范围</strong><button class="btn primary evidence-playback-link" :disabled="!hasEventVideo||!auth.can('playback')" :title="!auth.can('playback')?'当前角色无回放权限':!hasEventVideo?'此事件没有可播放的录像':'打开录像回放'" @click="openPlayback"><Play :size="17"/>联动回放</button><span>{{timeOnly(evidenceRange.start)}} / {{timeOnly(evidenceRange.end)}}</span></div><div class="event-axis"><span v-for="i in 5" :key="i">{{eventTick(i-1)}}</span></div><div v-for="id in cameraIds" :key="id" class="event-track"><label>{{cameraName(id)}} {{evidenceCameraLabel(id)}}</label><div><i v-if="videoFor(id)" class="evidence-recorded-block" :style="evidenceBlockStyle(id)"/></div></div></div>
</template>
<div v-else-if="activeTab==='空间轨迹'" class="large-trajectory"><svg viewBox="0 0 620 300"><polyline :points="trackPoints" fill="none" stroke="#ed9a26" stroke-width="3"/></svg><p>{{trajectory.length?trajectory.length+' 个有效定位点':'暂无有效定位点'}}</p></div>
<div v-else class="audit-timeline"><template v-if="selected"><div><i/><time>{{dateTime(selected.startedAt)}}</time><strong>事件生成</strong><p>系统发现目标 {{selected.trackId}}</p></div><div v-if="selected.reviewedAt"><i/><time>{{dateTime(selected.reviewedAt)}}</time><strong>复核完成 · {{conclusions[selected.conclusion||'']}}</strong><p>{{selected.reviewedBy}} · {{selected.note||'未填写备注'}}</p></div></template><p v-else class="muted">选择事件后查看处置记录</p></div>
</section>
<section class="panel event-facts"><div class="mini-track"><h3>空间轨迹</h3><div class="mini-track-view"><svg viewBox="0 0 620 300"><defs><pattern id="track-grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#dae4ef"/></pattern></defs><rect width="620" height="300" fill="url(#track-grid)"/><polyline :points="trackPoints" fill="none" stroke="#f29b22" stroke-width="3"/></svg><span v-if="!trajectory.length">等待有效定位轨迹</span></div></div><div class="facts-main"><h3>事件事实</h3><dl class="facts"><div><dt>目标编号</dt><dd><span :class="{'facts-target':selected}">{{selected?.trackId||'—'}}</span></dd></div><div><dt>事件类型</dt><dd>{{selected?'目标检测':'—'}}</dd></div><div><dt>发生时间</dt><dd>{{dateTime(selected?.startedAt)}}</dd></div><div><dt>相对高度</dt><dd>{{metric('y')}} m</dd></div><div><dt>空间距离</dt><dd>{{metric('rangeMeters')}} m</dd></div></dl></div></section>
</div>
<div class="review-column"><aside class="panel review-panel"><h2>人工复核</h2><div class="model-suggestion" :title="selected?.status==='reviewed'?'已由 '+selected.reviewedBy+' 完成复核':'模型建议：'+modelSuggestion"><template v-if="selected?.status==='reviewed'"><CheckCircle2 :size="20"/>已由 {{selected.reviewedBy}} 完成复核</template><template v-else><ScanLine :size="20"/>模型建议：{{modelSuggestion}}</template></div>
<form @submit.prevent="review"><fieldset :disabled="!auth.can('review')||!selected||selected.status==='reviewed'||saving"><legend>复核结论 <span>*</span></legend><label v-for="(label,value) in conclusions" :key="value" class="radio-option"><input v-model="conclusion" type="radio" :value="value" name="conclusion" required/>{{label}}</label><label class="notes-label" for="review-notes">处置备注</label><textarea id="review-notes" v-model="note" rows="6" maxlength="2000" placeholder="记录现场情况、判断依据或后续处理"/></fieldset><div class="review-actions"><button type="button" class="btn" :disabled="!selected||!hasNextPending||nextLoading" :title="hasNextPending?'打开下一条待复核事件':'没有其他待复核事件'" @click="nextPending">{{nextLoading?'正在读取…':'下一待复核'}}</button><button class="btn primary" :title="!auth.can('review')?'当前角色无复核权限':!selected?'请先选择事件':selected.status==='reviewed'?'此事件已完成复核':!conclusion?'请选择复核结论':'保存本次复核结论'" :disabled="!auth.can('review')||!selected||!conclusion||saving||selected.status==='reviewed'">{{saving?'正在提交…':'提交复核'}}</button></div></form>
</aside><section class="panel review-history"><h2>审核记录</h2><div v-if="selected" class="audit-timeline"><div><i/><time>{{dateTime(selected.startedAt)}}</time><strong>事件生成</strong><p>系统自动检测到目标，等待人工复核</p></div><div v-if="selected.reviewedAt"><i/><time>{{dateTime(selected.reviewedAt)}}</time><strong>完成复核</strong><p>{{selected.reviewedBy}} · {{conclusions[selected.conclusion||'']}}</p></div></div><p v-else class="history-empty">选择事件后查看审核记录</p></section></div>
</div></div></template>
<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue';
import {useRoute,useRouter} from 'vue-router';
import {RefreshCw,Search,Download,ChevronRight,ScanLine,CheckCircle2,Play,VideoOff} from 'lucide-vue-next';
import {http} from '@/api/http';import {useAuthStore} from '@/stores/auth';import {useCameraStore} from '@/stores/camera';
import ProductEmpty from '@/components/ProductEmpty.vue';import DroneGlyph from '@/components/DroneGlyph.vue';
import {dateTime,timeOnly,cameraName,mediaUrl,errorMessage,conclusions,type ProductEvent,type ProductMedia} from '@/lib/product-format';
const route=useRoute();const router=useRouter();const auth=useAuthStore();const store=useCameraStore();const events=ref<ProductEvent[]>([]);const selected=ref<ProductEvent|null>(null);const evidenceFailures=ref<Record<string,boolean>>({});const filter=ref('pending');const search=ref('');const total=ref(0);const pending=ref(0);const loading=ref(false);const saving=ref(false);const exporting=ref(false);const nextLoading=ref(false);const error=ref('');const conclusion=ref('');const note=ref('');const activeTab=ref('视频证据');const evidenceKind=ref('image');const page=ref(0);const cameraIds=['camera1','camera2'];
let selectionRequest=0;let listRequest=0;let querySelectionId=typeof route.query.id==='string'?route.query.id:'';
async function selectById(id:string){const request=++selectionRequest;evidenceFailures.value={};selected.value=null;conclusion.value='';note.value='';try{const {data}=await http.get('/events/'+encodeURIComponent(id));if(request!==selectionRequest)return;if(!data.event||data.event.id!==id)throw new Error('未找到指定事件');selected.value=data.event;conclusion.value=data.event.conclusion||'';note.value=data.event.note||''}catch(e){if(request===selectionRequest){selected.value=null;error.value='无法读取事件 '+id+'：'+errorMessage(e)}}}
async function select(item:ProductEvent){querySelectionId='';error.value='';await selectById(item.id)}
async function load(){const request=++listRequest;loading.value=true;error.value='';try{const {data}=await http.get('/events',{params:{status:filter.value==='all'?undefined:filter.value,q:search.value,limit:50,offset:page.value*50}});if(request!==listRequest)return;events.value=data.events;total.value=data.total;pending.value=data.pending;const id=querySelectionId||events.value.find(e=>e.id===selected.value?.id)?.id||events.value[0]?.id;if(id)await selectById(id);else{selectionRequest++;selected.value=null}}catch(e){if(request===listRequest)error.value=errorMessage(e)}finally{if(request===listRequest)loading.value=false}}
function changeFilter(value:string){querySelectionId='';filter.value=value;page.value=0;selected.value=null;void load()}
async function review(){if(!auth.can('review')||saving.value||!selected.value||selected.value.status==='reviewed'||!conclusion.value)return;saving.value=true;error.value='';try{await http.post('/events/'+encodeURIComponent(selected.value.id)+'/review',{conclusion:conclusion.value,note:note.value});store.toast('复核结论已保存','success');await load()}catch(e){error.value=errorMessage(e)}finally{saving.value=false}}
const hasNextPending=computed(()=>pending.value>(selected.value?.status==='pending'?1:0));
async function nextPending(){if(nextLoading.value||!selected.value||!hasNextPending.value)return;nextLoading.value=true;error.value='';try{const current=selected.value.id;const index=events.value.findIndex(e=>e.id===current);let next=events.value.slice(index+1).find(e=>e.status==='pending')||events.value.find(e=>e.status==='pending'&&e.id!==current);if(!next){const{data}=await http.get('/events',{params:{status:'pending',limit:2,offset:0}});next=data.events.find((e:ProductEvent)=>e.id!==current);pending.value=data.pending}if(next)await select(next);else store.toast('没有其他待复核事件','info')}catch(e){error.value=errorMessage(e)}finally{nextLoading.value=false}}
async function exportEvidence(){if(!auth.can('export')||!selected.value||exporting.value)return;const id=selected.value.id;exporting.value=true;error.value='';try{const response=await http.get('/events/'+encodeURIComponent(id)+'/export',{responseType:'blob'});const url=URL.createObjectURL(response.data);const a=document.createElement('a');a.href=url;a.download=id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){error.value=errorMessage(e)}finally{exporting.value=false}}
const isExplicitEvidence=(item?:ProductMedia)=>!!item&&!!selected.value?.payload.evidence?.includes(item.id);
function mediaFor(id:string,kind:'image'|'video'){const event=selected.value;if(!event)return;const candidates=(event.media||[]).filter(m=>m.cameraId===id&&m.kind===kind&&m.ready);const overlaps=(item:ProductMedia)=>item.startedAt<=event.lastSeenAt&&item.startedAt+Math.max(0,item.duration)*1000>=event.startedAt;return candidates.sort((a,b)=>Number(isExplicitEvidence(b))-Number(isExplicitEvidence(a))||(kind==='video'?Number(overlaps(b))-Number(overlaps(a)):0)||Math.abs(a.startedAt-event.startedAt)-Math.abs(b.startedAt-event.startedAt))[0]}
function evidenceFailed(id:string,kind:'image'|'video'){const item=mediaFor(id,kind);if(item)evidenceFailures.value[item.id]=true;error.value=cameraName(id)+' 资料读取失败，请刷新或下载事件记录后检查原文件'}
function activeEvidenceFailed(id:string){const item=mediaFor(id,evidenceKind.value==='image'?'image':'video');return !!item&&evidenceFailures.value[item.id]}
const imageFor=(id:string)=>mediaFor(id,'image');
const videoFor=(id:string)=>mediaFor(id,'video');
const metric=(key:string)=>{const value=selected.value?.payload.target?.[key];return typeof value==='number'&&Number.isFinite(value)?value.toFixed(1):'—'};
const trajectory=computed(()=>selected.value?.payload.trajectory?.filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.z))||[]);
const trackCoordinates=computed(()=>{const points=trajectory.value;if(!points.length)return[];const xs=points.map(p=>p.x);const zs=points.map(p=>p.z);const minX=Math.min(...xs),maxX=Math.max(...xs),minZ=Math.min(...zs),maxZ=Math.max(...zs);return points.map(p=>({x:30+(p.x-minX)/Math.max(maxX-minX,1)*560,y:270-(p.z-minZ)/Math.max(maxZ-minZ,1)*240}))});
const trackPoints=computed(()=>trackCoordinates.value.map(p=>p.x+','+p.y).join(' '));
onMounted(()=>{if(querySelectionId)filter.value='all';void load()});
watch(()=>route.query.id,id=>{querySelectionId=typeof id==='string'?id:'';filter.value='all';page.value=0;selectionRequest++;selected.value=null;void load()});
const modelSuggestion=computed(()=>{if(!selected.value)return'等待事件';const target=selected.value.payload.target;const detectedClass=target?.class||target?.className||target?.label;return detectedClass==='drone'?'疑似无人机':typeof detectedClass==='string'&&detectedClass?detectedClass:'等待分类结果'});
const eventVideos=computed(()=>cameraIds.map(id=>videoFor(id)).filter((item):item is ProductMedia=>!!item));
const hasEventVideo=computed(()=>eventVideos.value.length>0);
const evidenceRange=computed(()=>{if(!selected.value)return{start:0,end:0};const start=Math.min(selected.value.startedAt,...eventVideos.value.map(m=>m.startedAt));const end=Math.max(selected.value.lastSeenAt,...eventVideos.value.map(m=>m.startedAt+Math.max(0,m.duration)*1000));return{start,end}});
function eventTick(index:number){if(!selected.value)return'—';return timeOnly(evidenceRange.value.start+(evidenceRange.value.end-evidenceRange.value.start)*index/4)}
function evidenceBlockStyle(id:string){const item=videoFor(id);if(!item)return{};const range=Math.max(1,evidenceRange.value.end-evidenceRange.value.start);return{left:((item.startedAt-evidenceRange.value.start)/range*100)+'%',width:(Math.max(0,item.duration)*1000/range*100)+'%'}}
function openPlayback(){const item=eventVideos.value[0];if(!item||!auth.can('playback'))return;const date=new Date(item.startedAt);const value=[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-');void router.push({path:'/playback',query:{date:value,id:item.id}})}

function evidenceCameraLabel(id:string){const label=store.status.cameras[id as 'camera1'|'camera2'].label;return label&&label!==id?label.replace(/^C[12]\s*/,''):'摄像机 '+(id==='camera1'?'1':'2')}
</script>
<style scoped>
.filter-switch button.active {
  background: #1265f1;
  color: white;
}

.filter-switch span {
  margin-left: 5px;
}

.search-box button {
  border: 0;
  background: none;
  cursor: pointer;
  color: #8796aa;
}

.event-scroll {
  flex: 1;
  overflow: auto;
}

.event-row:hover {
  background: #f5f8fc;
}

.event-row-title {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
}

.list-loading {
  padding: 25px;
  font-size: 12px;
}

.evidence-title h2 span {
  font-size: 13px;
  color: #91a0b3;
  font-weight: 400;
  margin-left: 8px;
}

.pagination {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  justify-content: space-between;
}

.pagination button {
  min-height: 25px;
  padding: 3px 8px;
}

.evidence-video :deep(.product-empty) {
  min-height: 210px;
  padding: 35px 10px 15px;
}

.evidence-note {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 15px 0;
  color: #7c8ca0;
  font-size: 12px;
  border-bottom: 1px solid #e6ebf1;
}

h3 {
  font-size: 14px;
  margin: 24px 0 12px;
}

fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}

fieldset:disabled {
  opacity: .65;
}

legend {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 14px;
}

legend span {
  color: #dd685f;
}

textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid #dbe3ee;
  border-radius: 7px;
  background: #fbfcfe;
  padding: 11px;
  font: inherit;
  font-size: 12px;
  line-height: 1.6;
  box-sizing: border-box;
}

.review-actions button {
  flex: 1;
  padding: 10px 6px;
}

.review-guidance {
  margin-top: auto;
  padding-top: 30px;
  display: flex;
  gap: 8px;
  color: #8a99ae;
  font-size: 12px;
  line-height: 1.8;
}

.review-guidance svg {
  flex-shrink: 0;
}

.review-guidance p {
  margin: 0;
}

.audit-timeline {
  padding: 15px 15px 15px 25px;
}

.audit-timeline>div {
  padding: 0 0 30px 25px;
  border-left: 1px solid #dbe5f3;
  position: relative;
  font-size: 13px;
}

.audit-timeline i {
  position: absolute;
  left: -5px;
  top: 3px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #176afa;
}

.audit-timeline .pending-dot {
  background: #f0a23b;
}

.audit-timeline time {
  display: block;
  color: #8e9aad;
  margin-bottom: 10px;
  font-size: 12px;
}

.audit-timeline p {
  color: #7a899e;
  line-height: 1.7;
}

.track-chart {
  background: #f7f9fc;
  border: 1px solid #e0e7f0;
  border-radius: 8px;
}

.track-chart svg {
  width: 100%;
  display: block;
}

.trajectory>p {
  font-size: 12px;
}

.event-list :deep(.product-empty) {
  min-height: 170px;
}

.evidence {
  min-width: 0;
  padding: 0 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.evidence-kind {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
  margin: 0;
}

.evidence-videos {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  min-height: 260px;
  flex: 1;
}

.evidence-image {
  display: block;
  height: 100%;
  max-height: none;
  width: 100%;
  object-fit: contain;
}

.evidence-video video {
  height: 100%;
  max-height: none;
  width: 100%;
  object-fit: contain;
}

.video-unavailable {
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #8596ae;
  font-size: 15px;
}

.video-footer>svg:last-child {
  margin-left: auto;
}

.timeline-toolbar .btn {
  min-height: 34px;
  padding: 7px;
}

.timeline-toolbar select {
  margin-left: auto;
  padding: 5px 9px;
  font-size: 13px;
}

.event-track>div.recorded {
  background: #9bc2fc;
  border-color: #8ab7fa;
}

.mini-track-view {
  height: 175px;
  border: 1px solid #e0e8f2;
  border-radius: 8px;
  background: #f3f7fc;
  position: relative;
  overflow: hidden;
}

.mini-track-view svg {
  width: 100%;
  height: 100%;
  display: block;
}

.mini-track-view>span {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  color: #8b9bb0;
}

.review-column {
  display: grid;
  grid-template-rows: 492px minmax(240px,1fr);
  gap: 10px;

  min-height: 0;
}

.review-panel {
  flex-direction: column;
  padding: 18px 20px;
  display: block;
}

.review-actions {
  display: flex;
  margin-top: 17px;
  gap: 15px;
}

.review-complete {
  font-size: 12px;
  color: #159067;
  display: flex;
  gap: 5px;
  align-items: center;
  margin: 8px 0;
}

.review-history .audit-timeline {
  padding: 18px 0 0 12px;
}

.large-trajectory {
  flex: 1;
  background: #f4f7fc;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}

.large-trajectory svg {
  width: 100%;
}

.large-trajectory p {
  font-size: 16px;
  color: #8092ac;
}

.events-page .badge {
  font-size: 13px;
}

.timeline-toolbar>span {
  margin-left: auto;
}

.evidence-origin {
  position: absolute;
  top: 48px;
  left: 10px;
  padding: 4px 8px;
  border-radius: 4px;
  background: #ffffffdf;
  color: #697d98;
  font-size: 12px;
}

.events-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: #111827;
}

.events-page>.page-heading {
  height: 76px;
  min-height: 76px;
  margin: 0;
  padding: 0 16px;
}

.event-workspace {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(240px,.75fr) minmax(530px,2.24fr) minmax(330px,1fr);
  gap: 10px;
  min-height: 0;

  grid-template-rows: minmax(0,1fr);
}

.event-list {
  display: flex;
  flex-direction: column;
  padding: 12px 10px;

  min-height: 0;
}

.filter-switch {
  display: flex;
  border: 1px solid #e0e7f0;
  padding: 0;
  height: 40px;
  background: #f8faff;
  border-color: #ced7e6;
  border-radius: 8px;
}

.filter-switch button {
  flex: 1;
  border: 0;
  background: transparent;
  padding: 10px 4px;
  cursor: pointer;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
  border-radius: 7px;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 7px;
  border: 1px solid #dce4ef;
  margin: 14px 3px;
  height: 39px;
  padding: 7px 10px;
  border-color: #ccd6e5;
  border-radius: 8px;
  color: #5f6b82;
}

.search-box input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  font: inherit;
  font-size: 15px;
  font-weight: 500;
  color: #111827;
}

.event-row {
  width: 100%;
  text-align: left;
  border: 0;
  border-bottom: 1px solid #e6ecf3;
  background: transparent;
  cursor: pointer;
  color: #20324a;
  padding: 11px 5px;
  min-height: 96px;
  border-radius: 7px;
  display: grid;
  grid-template-columns: 33px minmax(0,1fr) 14px;
  gap: 9px;
  align-items: center;

  line-height: 1.35;
}

.event-row.selected {
  background: linear-gradient(110deg,#dce9ff,#e5edfd);
}

.event-row-title strong {
  font-size: 18px;
  font-weight: 700;
  color: #111827;
}

.event-row.selected .event-row-title strong {
  color: #0868ff;
}

.event-row p {
  font-size: 15px;
  font-weight: 500;
  margin: 5px 0;
  color: #111827;
}

.event-row time {
  font-size: 14px;
  font-weight: 500;
  color: #37455d;
  white-space: nowrap;
}

.event-row .badge {
  font-size: 14px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 6px;

  line-height: 1.25;
}

.event-drone {
  width: 33px;
  height: 37px;
  border-radius: 50%;
  background: #f0f4f9;
  color: #19283c;
  display: grid;
  place-items: center;
}

.event-row-chevron {
  color: #43516b;
}

.event-row.selected .event-row-chevron {
  color: #0868ff;
}

.evidence-column {
  display: grid;
  gap: 8px;
  min-width: 0;
  grid-template-rows: minmax(510px,1fr) 228px;

  min-height: 0;
}

.evidence-title h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
}

.evidence-title {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  height: 49px;
  border-bottom: 1px solid #dce3ef;
  border-color: #d9e0eb;
}

.evidence-kind button {
  background: #f4f7fc;
  border: 0;
  border-radius: 5px;
  cursor: pointer;
  padding: 5px 7px;
  font-size: 13px;
  font-weight: 500;
  color: #5f6b82;
}

.evidence-kind button.active {
  background: #edf3ff;
  font-weight: 600;
  color: #0868ff;
}

.evidence-tabs {
  display: flex;
  border-bottom: 1px solid #e1e8f1;
  height: 53px;
  margin: 0 0 20px;
  gap: 0;
  flex-shrink: 0;
  border-color: #d9e0eb;
  margin-bottom: 20px;
}

.evidence-tabs button {
  border: 0;
  background: none;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  position: relative;
  font-size: 17px;
  font-weight: 600;
  color: #111827;
  padding: 12px 22px;
}

.evidence-tabs button+button:before {
  content: '';
  position: absolute;
  left: 0;
  top: 17px;
  height: 18px;
  border-left: 1px solid #dfe5ef;
}

.evidence-tabs button.active {
  border-bottom-color: #1265f1;
  color: #0868ff;
  border-bottom: 3px solid #0868ff;
}

.evidence-video {
  position: relative;
  overflow: hidden;
  height: 260px;
  min-height: 0;
  background: linear-gradient(150deg,#edf3fa,#e2ebf6);
  border: 0;
  border-radius: 9px;
}

.video-label {
  position: absolute;
  z-index: 1;
  top: 9px;
  left: 10px;
  color: white;
  font-size: 17px;
  font-weight: 600;
  background: #3f4b5fdb;
  padding: 4px 10px;
  border-radius: 5px;
}

.video-footer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  color: white;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 17px;
  font-size: 15px;
  font-weight: 500;
  background: linear-gradient(transparent,#243140cc);
  height: 43px;
}

.event-timeline {
  margin-top: 14px;
  border-top: 1px solid #dce3ef;
  padding-bottom: 8px;
  border-color: #d9e0eb;
}

.timeline-toolbar {
  height: 55px;
  display: flex;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid #dce3ef;
  font-size: 14px;
  color: #111827;
  border-color: #d9e0eb;
}

.timeline-toolbar strong {
  font-size: 16px;
  font-weight: 700;
}

.timeline-toolbar .evidence-playback-link {
  padding: 6px 10px;
  gap: 5px;
  font-size: 14px;
  font-weight: 600;
}

.event-axis {
  display: flex;
  justify-content: space-between;
  margin: 12px 6px 8px 130px;
  font-size: 13px;
  font-weight: 500;
  color: #44516a;
}

.event-track {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 8px 0;
}

.event-track label {
  padding: 2px 6px;
  border-radius: 4px;
  min-width: 118px;
  font-size: 13px;
  font-weight: 600;
  background: #e9edf4;
  color: #111827;

  line-height: 1.2;
}

.event-track>div {
  flex: 1;
  border-radius: 2px;
  position: relative;
  overflow: hidden;
  height: 13px;
  border: 0;
  background: #edf2f9;
}

.evidence-recorded-block {
  position: absolute;
  top: 0;
  bottom: 0;
  background: linear-gradient(#b7d3ff,#94bfff);
}

.event-facts {
  display: grid;
  grid-template-columns: 1.25fr 1fr;
  overflow: hidden;
  gap: 18px;
  padding: 10px 16px;
}

.event-facts h3 {
  font-size: 18px;
  font-weight: 700;
  margin: 0 0 9px;
  color: #111827;

  line-height: 1.2;
}

.facts-main {
  border-left: 1px solid #dce3ef;
  padding-left: 17px;
  border-color: #d9e0eb;
}

.facts {
  margin: 0;
  font-size: 16px;
}

.facts>div {
  display: grid;
  border-bottom: 1px solid #edf1f6;
  grid-template-columns: 86px minmax(0,1fr);
  gap: 10px;
  padding: 5px 0;
  min-height: 32px;
  border-color: #dce3ed;

  line-height: 1.3;
}

.facts dt {
  color: #253046;
  font-size: 15px;
  font-weight: 600;
}

.facts dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: #111827;
  font-size: 15px;
  font-weight: 500;
}

.facts-target {
  color: #0868ff;
  background: #dfebff;
  border-radius: 4px;
  padding: 0 5px;
  font-weight: 600;
}

.review-panel h2 {
  margin: 0;
  padding-bottom: 11px;
  border-bottom: 1px solid #dce3ef;
  font-size: 24px;
  font-weight: 700;
  color: #111827;
  border-color: #d9e0eb;

  line-height: 1.25;
}

.model-suggestion {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 24px;
  margin: 12px 0 18px;
  padding: 10px 12px;
  white-space: nowrap;
  background: #dfebff;
  color: #0868ff;
  font-size: 16px;
  font-weight: 600;
  border-radius: 8px;

  line-height: 1.3;
}

.review-panel legend {
  margin-bottom: 12px;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.notes-label {
  display: block;
  margin: 17px 0 10px;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.radio-option {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
  gap: 12px;
  font-size: 18px;
  font-weight: 500;
  color: #111827;
}

.radio-option input {
  accent-color: #1265f1;
  width: 21px;
  height: 21px;
  margin: 0;
}

.review-panel textarea {
  height: 101px;
  min-height: 101px;
  padding: 10px 12px;
  resize: vertical;
  font-size: 16px;
  font-weight: 500;
  border: 1px solid #c9d3e3;
  background: #fbfcff;
  border-radius: 8px;
  line-height: 1.55;
}

.review-panel fieldset:disabled {
  opacity: .7;
}

.review-actions .btn {
  height: 40px;
  font-size: 17px;
  font-weight: 600;
}

.review-actions .primary:disabled {
  opacity: 1;
  background: #d2d7e0;
  border-color: #d2d7e0;
  color: #939aa6;
}

.review-history {
  overflow: auto;
  padding: 14px 20px;
}

.review-history h2 {
  margin: 0;
  padding-bottom: 14px;
  border-bottom: 1px solid #dce3ef;
  font-weight: 700;
  color: #111827;
  border-color: #d9e0eb;
  font-size: 23px;

  line-height: 1.3;
}

.review-history .audit-timeline time {
  font-size: 15px;
  color: #43516a;
  font-weight: 500;
}

.review-history .audit-timeline strong {
  font-size: 17px;
  color: #111827;
  font-weight: 650;
}

.review-history .audit-timeline p {
  font-size: 15px;
  color: #5f6b82;
  line-height: 1.65;
}

.list-footer {
  padding: 15px 8px 0;
  font-size: 13px;
  color: #5f6b82;
}

.list-footer .btn {
  border-color: #d5deea;
}

.history-empty {
  font-size: 15px;
  margin: 24px 0;
  color: #5f6b82;
}

@media (max-width:1300px) {
  .event-workspace {
    grid-template-columns: 220px minmax(420px,1fr) 310px;
    overflow: auto;
  }

  .review-column {
    grid-template-rows: 492px 260px;
  }

  .evidence-column {
    min-width: 420px;
  }

  .timeline-toolbar {
    gap: 8px;
  }

  .timeline-toolbar strong {
    font-size: 14px;
  }

  .facts>div {
    grid-template-columns: 68px 1fr;
  }

  .model-suggestion {
    font-size: 14px;
  }

  .review-panel {
    grid-column: auto;
    padding: 16px;
  }

  .evidence-tabs button {
    padding: 12px 15px;
  }
}

@media (max-width:1000px) {
  .events-page {
    height: auto;
  }

  .event-workspace {
    grid-template-columns: 220px 1fr;
    overflow: visible;
  }

  .review-column {
    grid-column: 1/-1;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto;
  }

  .event-list {
    max-height: none;
  }

  .evidence-videos {
    grid-template-columns: 1fr 1fr;
  }

  .review-panel {
    grid-column: auto;
  }

  .event-facts {
    grid-template-columns: 1fr;
  }

  .mini-track {
    display: none;
  }

  .facts-main {
    border: 0;
    padding: 0;
  }
}
</style>
