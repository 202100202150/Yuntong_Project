<template>
<div class="playback-page"><div class="page-heading"><div><h1>录像回放</h1><p class="muted">检索双路录像，定位关键时刻并导出片段</p></div><button v-if="auth.can('export')" class="btn" :disabled="!selectedMedia.length" :title="selectedMedia.length?'打开导出设置':'请先检索并选择录像'" @click="showExport=true"><Download :size="16"/>导出片段</button></div><p v-if="error" class="error-banner" role="alert">{{error}}</p>
<div class="playback-workspace" :class="{'with-export':showExport}">
<aside class="panel search-panel"><h2>录像检索</h2><form @submit.prevent="search"><div class="calendar-label">日期</div><div class="month-calendar"><header><button type="button" aria-label="上个月" @click="shiftMonth(-1)">‹</button><strong>{{calendarYear}}年{{calendarMonth+1}}月</strong><button type="button" aria-label="下个月" @click="shiftMonth(1)">›</button></header><div class="calendar-grid"><span v-for="day in ['一','二','三','四','五','六','日']" :key="day">{{day}}</span><button v-for="day in calendarDays" :key="day.date" type="button" :class="{outside:!day.current,chosen:date===day.date}" @click="date=day.date">{{day.day}}</button></div></div><h3>选择相机</h3><label v-for="id in ids" :key="id" class="camera-check"><input v-model="included" type="checkbox" :value="id"/><Video :size="16"/>{{cameraName(id)}} {{playbackCameraLabel(id)}}</label><div class="time-range-title">时间范围</div><div class="time-range-inputs"><input v-model="filterStart" type="time" aria-label="检索开始时间"/><span>—</span><input v-model="filterEnd" type="time" aria-label="检索结束时间"/></div><button class="btn primary search-button" :disabled="loading||!included.length"><Search :size="16"/>{{loading?'正在检索…':'搜索录像'}}</button></form><div class="recording-list"><div class="list-heading"><strong>录像文件</strong><span>{{filtered.length}}</span></div><button v-for="item in filtered" :key="item.id" class="recording-item" :class="{selected:selection[item.cameraId]===item.id}" @click="choose(item)"><div><span>{{cameraName(item.cameraId)}}</span><strong>{{timeOnly(item.startedAt)}}</strong><Check v-if="selection[item.cameraId]===item.id" :size="14"/></div><p>{{secondsLabel(item.duration)}} · {{bytesLabel(item.bytes)}}</p></button><ProductEmpty v-if="!filtered.length&&!loading" title="未找到录像" description="尝试其他日期，或先在实时监控中开始录制" icon="search"/></div></aside>
<section class="playback-main"><div class="panel video-grid"><div v-for="id in ids" :key="id" class="playback-video" :ref="el=>setViewport(id,el)"><div class="video-heading"><strong>{{cameraName(id)}} {{playbackCameraLabel(id)}}</strong><span>{{itemFor(id)?timeOnly(itemFor(id)!.startedAt):'未选择录像'}}</span></div><video v-if="itemFor(id)" :ref="el=>setVideo(id,el)" :src="mediaUrl(itemFor(id)!.url)" preload="metadata" :muted="muted[id]" @volumechange="syncMute(id)" @loadedmetadata="metadata(id)" @timeupdate="tick(id)" @ended="ended(id)" @waiting="setBuffering(id,true)" @canplay="setBuffering(id,false)" @error="videoError(id)" :aria-label="cameraName(id)+' 录像回放'"/><ProductEmpty v-else icon="video" title="选择录像文件" description="从左侧检索结果中选择对应相机的录像"/><span v-if="itemFor(id)&&!within(id)" class="outside-range">当前时刻无录像</span><div v-if="itemFor(id)" class="video-control-bar"><button :aria-label="cameraName(id)+(playing?' 暂停联动录像':' 播放联动录像')" :title="playing?'暂停联动录像':'播放联动录像'" @click="togglePlay"><component :is="playing?Pause:Play" :size="22"/></button><span class="video-clock">{{timeOnly(start+position*1000)}} <span>/ {{timeOnly(start+duration*1000)}}</span></span><input class="video-seek" type="range" :aria-label="cameraName(id)+' 联动回放时间'" min="0" :max="duration||1" step=".1" :value="position" :style="{'--played':(duration?position/duration*100:0)+'%'}" @input="seek(Number(($event.target as HTMLInputElement).value))"/><button :aria-label="cameraName(id)+(muted[id]?' 取消静音':' 静音')" :aria-pressed="muted[id]" :title="muted[id]?'取消静音':'静音'" @click="toggleMute(id)"><component :is="muted[id]?VolumeX:Volume2" :size="21"/></button><button :aria-label="cameraName(id)+(fullscreenId===id?' 退出全屏':' 全屏')" :title="fullscreenId===id?'退出全屏':'全屏'" @click="toggleFullscreen(id)"><component :is="fullscreenId===id?Minimize:Maximize" :size="21"/></button></div></div></div>
<div class="panel timeline-panel"><div class="playback-controls"><button class="btn primary play-main" :aria-label="playing?'暂停':'播放'" :title="playing?'暂停':'播放'" @click="togglePlay" :disabled="!selectedMedia.length"><component :is="playing?Pause:Play" :size="23"/></button><button class="btn step-control" aria-label="回到录像开始" title="回到录像开始" @click="seek(0)" :disabled="!selectedMedia.length"><SkipBack :size="19"/></button><button class="btn step-control" aria-label="后退5秒" title="后退 5 秒" @click="seek(position-5)" :disabled="!selectedMedia.length"><ChevronLeft :size="22"/></button><span class="playback-time" aria-label="当前回放时刻">{{selectedMedia.length?timeOnly(start+position*1000):'00:00:00'}}</span><button class="btn step-control" aria-label="前进5秒" title="前进 5 秒" @click="seek(position+5)" :disabled="!selectedMedia.length"><Play :size="19"/></button><button class="btn step-control" aria-label="跳到录像结束" title="跳到录像结束" @click="seek(duration)" :disabled="!selectedMedia.length"><SkipForward :size="19"/></button><label class="speed-label"><span class="control-label">回放倍速</span><select aria-label="回放倍速" v-model.number="speed" @change="updateSpeed"><option :value=".5">0.5×</option><option :value="1">1×</option><option :value="2">2×</option><option :value="4">4×</option></select></label></div>
<input type="range" class="seek-bar" aria-label="回放时间" :value="position" :max="duration||1" step=".1" min="0" :disabled="!selectedMedia.length" @input="seek(Number(($event.target as HTMLInputElement).value))"/>
<div class="time-axis"><strong class="timeline-date">{{date}}</strong><div class="time-ticks"><span>{{selectedMedia.length?timeOnly(start):'00:00:00'}}</span><span>{{selectedMedia.length?timeOnly(start+duration*500):'—'}}</span><span>{{selectedMedia.length?timeOnly(start+duration*1000):'00:00:00'}}</span></div></div>
<div class="recording-tracks"><div v-for="id in ids" :key="id" class="timeline-track"><strong :title="cameraName(id)+' '+playbackCameraLabel(id)">{{cameraName(id)}} {{playbackCameraLabel(id)}}</strong><div class="track-background"><div v-if="itemFor(id)" class="recorded-block" :style="blockStyle(id)"/><span class="playhead" :style="{left:(duration?position/duration*100:0)+'%'}"/></div></div>
<ClipRange v-if="selectedMedia.length&&validClip" :duration="duration" :start="clipStart" :end="clipEnd" :disabled="exporting" @change="setClipRange"/></div><div class="timeline-legend"><span><i/>录像可用</span><span v-if="selectedMedia.length" class="clip-range-caption">选中片段 {{secondsLabel(clipEnd-clipStart)}}</span><span>{{selectedMedia.length}} 路录像</span></div></div>
<div class="playback-footnote muted"><Info :size="15"/>录像采用统一时间定位；没有文件覆盖的时刻会暂停该路画面。</div></section>
<aside v-if="showExport&&auth.can('export')" class="panel export-panel"><div class="export-heading"><h2>导出片段</h2><button class="btn icon-only" aria-label="关闭导出" @click="showExport=false"><X :size="17"/></button></div><div class="export-camera-select"><strong>选择相机</strong><label v-for="id in ids" :key="id" class="camera-check"><input v-model="exportIncluded" type="checkbox" :value="id" :disabled="exporting||!itemFor(id)"/>{{cameraName(id)}} {{playbackCameraLabel(id)}}</label></div><label class="form-field">开始时间<input :value="clipTime(clipStart)" type="datetime-local" step=".1" :disabled="!selectedMedia.length||exporting" @change="setClipTime('start',($event.target as HTMLInputElement).value)"/></label><label class="form-field">结束时间<input :value="clipTime(clipEnd)" type="datetime-local" step=".1" :disabled="!selectedMedia.length||exporting" @change="setClipTime('end',($event.target as HTMLInputElement).value)"/></label><div class="export-format"><span>导出格式</span><output aria-label="导出格式">MP4</output></div><div class="annotation-option">目标标注<span>保留原录像标注</span></div><div class="export-summary"><strong>输出摘要</strong><span>{{exportMedia.length}} 路视频 · {{secondsLabel(Math.max(0,clipEnd-clipStart))}}</span><span>MP4 · 保留原始画面标注</span></div><details v-if="selectedMedia.length" class="original-downloads"><summary>下载完整录像</summary><div class="original-links"><a v-for="item in selectedMedia" :key="item.id" :href="'/api/media/'+encodeURIComponent(item.id)+'/download'" :aria-disabled="!!downloading" @click.prevent="downloadFile('/api/media/'+encodeURIComponent(item.id)+'/download',item.name)" download>下载 {{cameraName(item.cameraId)}} 完整录像</a></div></details><div class="export-actions"><button class="btn primary export-button" :disabled="exporting||!validClip||!exportMedia.length" :title="!exportMedia.length?'请选择需要导出的相机':!validClip?'请选择录像范围内的有效起止时间':'生成所选片段的 MP4 文件'" @click="exportClips">{{exporting?'正在导出…':'导出'}}</button><button class="btn" @click="showExport=false">取消</button></div><div v-if="exports.length" class="export-results"><a v-for="item in exports" :key="item.url" :href="item.url" :aria-disabled="!!downloading" @click.prevent="downloadFile(item.url,item.label+'.mp4')" download>{{item.label}} <Download :size="14"/></a></div></aside>
</div></div>
</template>
<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue';import {useRoute} from 'vue-router';import {Search,Video,Download,Play,Pause,SkipBack,SkipForward,Link2,Info,X,Check,Volume2,VolumeX,Maximize,Minimize,ChevronLeft} from 'lucide-vue-next';
import {http} from '@/api/http';import {downloadFile as startFileDownload} from '@/lib/download-file';import {useAuthStore} from '@/stores/auth';import {useCameraStore} from '@/stores/camera';import ProductEmpty from '@/components/ProductEmpty.vue';import ClipRange from '@/components/ClipRange.vue';import {cameraName,localDate,timeOnly,secondsLabel,bytesLabel,errorMessage,mediaUrl,type ProductMedia} from '@/lib/product-format';
const cameras=useCameraStore();const auth=useAuthStore();const route=useRoute();const ids=['camera1','camera2'];const requestedDate=typeof route.query.date==='string'?route.query.date:'';const validRequestedDate=/^\d{4}-\d{2}-\d{2}$/.test(requestedDate)&&Number.isFinite(Date.parse(requestedDate))&&new Date(requestedDate).toISOString().slice(0,10)===requestedDate;const date=ref(validRequestedDate?requestedDate:localDate());let requestedMediaId=typeof route.query.id==='string'?route.query.id:'';const included=ref([...ids]);const items=ref<ProductMedia[]>([]);const selection=ref<Record<string,string>>({});const loading=ref(false);const error=ref('');const videos:Record<string,HTMLVideoElement>={};const videoViewports:Record<string,HTMLElement>={};const muted=ref<Record<string,boolean>>({camera1:true,camera2:true});const fullscreenId=ref('');const buffering=ref<Record<string,boolean>>({});const playing=ref(false);const speed=ref(1);const position=ref(0);const showExport=ref(auth.can('export'));const clipStart=ref(0);const clipEnd=ref(0);const exporting=ref(false);const exportIncluded=ref([...ids]);const downloading=ref('');const exports=ref<{url:string;label:string}[]>([]);
const filtered=computed(()=>items.value.filter(item=>included.value.includes(item.cameraId)&&item.kind==='video'&&item.ready && item.startedAt+item.duration*1000>=new Date(date.value+'T'+filterStart.value).getTime() && item.startedAt<=new Date(date.value+'T'+filterEnd.value+':59').getTime()));
const selectedMedia=computed(()=>filtered.value.filter(item=>selection.value[item.cameraId]===item.id));
const exportMedia=computed(()=>selectedMedia.value.filter(item=>exportIncluded.value.includes(item.cameraId)));
const itemFor=(id:string)=>selectedMedia.value.find(item=>item.cameraId===id);
const start=computed(()=>selectedMedia.value.length?Math.min(...selectedMedia.value.map(item=>item.startedAt)):0);
const duration=computed(()=>selectedMedia.value.length?Math.max(...selectedMedia.value.map(item=>(item.startedAt-start.value)/1000+item.duration)):0);
const offset=(id:string)=>((itemFor(id)?.startedAt||start.value)-start.value)/1000;
const within=(id:string)=>{const item=itemFor(id);return!!item&&position.value>=offset(id)&&position.value<offset(id)+item.duration};
const validClip=computed(()=>selectedMedia.value.length>0&&Number.isFinite(clipStart.value)&&Number.isFinite(clipEnd.value)&&clipStart.value>=0&&clipEnd.value>clipStart.value&&clipEnd.value<=duration.value);
function setVideo(id:string,el:unknown){if(el instanceof HTMLVideoElement){videos[id]=el;el.muted=muted.value[id]??true}else delete videos[id]}
function setViewport(id:string,el:unknown){if(el instanceof HTMLElement)videoViewports[id]=el;else delete videoViewports[id]}
function toggleMute(id:string){muted.value[id]=!muted.value[id];if(videos[id])videos[id].muted=muted.value[id]}
function syncMute(id:string){if(videos[id])muted.value[id]=videos[id].muted}
function updateFullscreen(){fullscreenId.value=Object.entries(videoViewports).find(([,element])=>document.fullscreenElement===element)?.[0]||''}
async function toggleFullscreen(id:string){const viewport=videoViewports[id];if(!viewport)return;try{if(document.fullscreenElement===viewport)await document.exitFullscreen();else{if(document.fullscreenElement)await document.exitFullscreen();await viewport.requestFullscreen()}updateFullscreen()}catch(e){error.value='无法切换全屏：'+errorMessage(e)}}

watch(()=>selectedMedia.value.map(m=>m.id).join('|'),()=>{pause();buffering.value={};position.value=0;clipStart.value=0;clipEnd.value=Math.floor(duration.value*10)/10;exports.value=[];seek(0)});
function playbackFailed(){pause();error.value='录像播放失败，请检查文件编码或下载原片'}
function setBuffering(id:string,blocked:boolean){buffering.value[id]=blocked;if(blocked){Object.values(videos).forEach(v=>v.pause())}else if(playing.value&&!selectedMedia.value.some(m=>within(m.cameraId)&&buffering.value[m.cameraId])){for(const[key,video]of Object.entries(videos))if(within(key))void video.play().catch(playbackFailed)}}
function pause(){playing.value=false;Object.values(videos).forEach(video=>video.pause())}
async function search(){if(loading.value)return;pause();if(!included.value.length){error.value='请选择至少一路相机';return}if(!filterStart.value||!filterEnd.value||filterStart.value>filterEnd.value){error.value='请输入有效的起止时间，开始时间不能晚于结束时间';return}loading.value=true;error.value='';try{const{data}=await http.get('/media',{params:{date:date.value}});items.value=data.items;selection.value={};position.value=0;exports.value=[];const linked=filtered.value.find(m=>m.id===requestedMediaId);if(requestedMediaId&&!linked){error.value='指定录像不存在或不在检索范围内，请重新选择录像';requestedMediaId='';return}for(const id of included.value){const item=linked?.cameraId===id?linked:(linked?filtered.value.find(m=>m.cameraId===id&&m.startedAt<=linked.startedAt+linked.duration*1000&&m.startedAt+m.duration*1000>=linked.startedAt):null)||filtered.value.find(m=>m.cameraId===id);if(item)selection.value[id]=item.id}requestedMediaId='';clipStart.value=0;clipEnd.value=Math.floor(duration.value*10)/10}catch(e){error.value=errorMessage(e)}finally{loading.value=false}}
function choose(item:ProductMedia){pause();selection.value={...selection.value,[item.cameraId]:item.id};position.value=0;clipStart.value=0;clipEnd.value=Math.floor(duration.value*10)/10;exports.value=[]}
function metadata(id:string){const video=videos[id];const item=itemFor(id);if(video&&item&&Number.isFinite(video.duration)&&video.duration>0&&Math.abs(item.duration-video.duration)>.2)item.duration=video.duration;clipEnd.value=Math.floor(duration.value*10)/10;seek(position.value)}
function updateSpeed(){Object.values(videos).forEach(video=>video.playbackRate=speed.value)}
function seek(value:number){if(!Number.isFinite(value))return;position.value=Math.max(0,Math.min(duration.value,value));for(const [id,video]of Object.entries(videos)){const item=itemFor(id);if(!item)continue;const target=Math.max(0,Math.min(item.duration,position.value-offset(id)));if(Number.isFinite(target)&&video.readyState>0){try{video.currentTime=target}catch{playbackFailed();return}}if(!within(id))video.pause();else if(playing.value)void video.play().catch(()=>{pause();error.value='浏览器无法播放此录像，请下载原片查看'})}}
async function togglePlay(){if(!selectedMedia.value.length||!duration.value){error.value='请先选择可播放的录像';return}if(playing.value){pause();return}error.value='';if(position.value>=duration.value)seek(0);playing.value=true;for(const [id,video]of Object.entries(videos))if(within(id)){video.playbackRate=speed.value;try{await video.play()}catch{pause();error.value='当前录像编码无法在浏览器播放，可下载原片查看';break}}}
let timer:ReturnType<typeof setInterval>|undefined;let last=performance.now();
function advance(){const now=performance.now();const elapsed=(now-last)/1000;last=now;if(!playing.value)return;if(selectedMedia.value.some(m=>within(m.cameraId)&&(buffering.value[m.cameraId]||((videos[m.cameraId]?.readyState??0)<3))))return;position.value=Math.min(duration.value,position.value+elapsed*speed.value);for(const[id,video]of Object.entries(videos)){if(within(id)){const target=position.value-offset(id);if(Math.abs(video.currentTime-target)>.4)video.currentTime=target;if(video.paused)void video.play().catch(playbackFailed)}else video.pause()}if(position.value>=duration.value)pause()}
function tick(_id:string){/* shared monotonic clock drives both channels */}
function ended(id:string){if(selectedMedia.value.every(m=>position.value>=offset(m.cameraId)+m.duration-.2))pause()}
function videoError(id:string){pause();error.value=cameraName(id)+' 录像读取失败，请检查文件或下载原片'}
const blockStyle=(id:string)=>({left:(offset(id)/(duration.value||1)*100)+'%',width:((itemFor(id)?.duration||0)/(duration.value||1)*100)+'%'});
async function downloadFile(url:string,filename:string){if(!auth.can('export')){error.value='当前角色无导出权限';return}if(downloading.value)return;downloading.value=url;error.value='';try{await startFileDownload(mediaUrl(url),filename)}catch(e){error.value='下载失败：'+errorMessage(e)}finally{downloading.value=''}}
async function exportClips(){if(!auth.can('export')){error.value='当前角色无导出权限';return}if(exporting.value)return;if(!validClip.value||!exportMedia.value.length){error.value='请选择相机和有效的导出时间范围';return}const plan=exportMedia.value.map(item=>({id:item.id,cameraId:item.cameraId,start:Math.max(0,clipStart.value-offset(item.cameraId)),end:Math.min(item.duration,clipEnd.value-offset(item.cameraId))})).filter(item=>item.end>item.start);exporting.value=true;error.value='';exports.value=[];try{for(const item of plan){const{data}=await http.post('/media/'+encodeURIComponent(item.id)+'/export',{start:item.start,end:item.end});exports.value.push({url:mediaUrl(data.url),label:cameraName(item.cameraId)+' 导出完成'})}if(!exports.value.length)error.value='所选时间段没有可导出的录像'}catch(e){error.value=errorMessage(e)}finally{exporting.value=false}}
onMounted(()=>{document.addEventListener('fullscreenchange',updateFullscreen);void search();last=performance.now();timer=setInterval(advance,100)});onBeforeUnmount(()=>{pause();clearInterval(timer);document.removeEventListener('fullscreenchange',updateFullscreen)});

const viewedMonth=ref(Number(date.value.slice(5,7))-1);const viewedYear=ref(Number(date.value.slice(0,4)));const calendarMonth=computed(()=>viewedMonth.value);const calendarYear=computed(()=>viewedYear.value);const filterStart=ref('00:00');const filterEnd=ref('23:59');
const calendarDays=computed(()=>{const first=new Date(viewedYear.value,viewedMonth.value,1);const offset=(first.getDay()+6)%7;return Array.from({length:42},(_,index)=>{const day=new Date(viewedYear.value,viewedMonth.value,index-offset+1);const value=[day.getFullYear(),String(day.getMonth()+1).padStart(2,'0'),String(day.getDate()).padStart(2,'0')].join('-');return {date:value,day:day.getDate(),current:day.getMonth()===viewedMonth.value}})});
function shiftMonth(delta:number){const value=new Date(viewedYear.value,viewedMonth.value+delta,1);viewedMonth.value=value.getMonth();viewedYear.value=value.getFullYear()}

function playbackCameraLabel(id:string){const label=cameras.status.cameras[id as 'camera1'|'camera2'].label;return label&&label!==id?label.replace(/^C[12]\s*/,''):'摄像机 '+(id==='camera1'?'1':'2')}
function setClipRange(range:{start:number;end:number}){if(exporting.value)return;clipStart.value=range.start;clipEnd.value=range.end;exports.value=[]}
function clipTime(seconds:number){if(!selectedMedia.value.length)return'';const date=new Date(start.value+seconds*1000);const pad=(n:number)=>String(n).padStart(2,'0');return date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate())+'T'+pad(date.getHours())+':'+pad(date.getMinutes())+':'+pad(date.getSeconds())+'.'+Math.floor(date.getMilliseconds()/100)}
function setClipTime(edge:'start'|'end',value:string){if(exporting.value)return;const time=new Date(value).getTime();if(!Number.isFinite(time)){error.value='请输入有效的导出时间';return;}const seconds=Math.round((time-start.value)/100)/10;if(edge==='start')clipStart.value=seconds;else clipEnd.value=seconds;exports.value=[];error.value=validClip.value?'':'导出时间必须在已选录像范围内，且结束时间晚于开始时间'}

</script>
<style scoped>
h3 {
  font-size: 13px;
  margin: 24px 0 17px;
}

.recording-item.selected {
  background: #edf3ff;
  color: #1769ed;
}

.recording-item:hover {
  background: #f3f6fc;
}

.recording-item p {
  font-size: 11px;
  margin: 8px 0 0;
  color: #8b98ab;
}

.recording-item svg {
  margin-left: auto;
}

.outside-range {
  position: absolute;
  bottom: 50px;
  left: 15px;
  font-size: 12px;
  background: #ffffffdf;
  color: #738199;
  border-radius: 4px;
  padding: 5px 8px;
}

.timeline-legend i {
  display: inline-block;
  background: #a8ccff;
  width: 18px;
  height: 10px;
  border-radius: 2px;
  margin-right: 6px;
}

.export-results a {
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: #1265f1;
  text-decoration: none;
}

.playback-page>.page-heading {
  height: 76px;
  margin: 0;
  padding: 0 16px;
}

.playback-page>.page-heading p {
  display: none;
}

.playback-workspace {
  display: grid;
  grid-template-columns: 240px minmax(0,1fr);
  gap: 10px;
  min-height: 0;
  flex: 1;
}

.playback-workspace.with-export {
  grid-template-columns: 240px minmax(0,1fr) 288px;
  gap: 10px;
  min-height: 0;
  flex: 1;
}

.search-panel form {
  display: flex;
  flex-direction: column;
  flex: 1;
}

.month-calendar header {
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e1e7f0;
  margin-bottom: 7px;
}

.time-range-inputs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 18px;
}

.recording-list {
  border-top: 1px solid #e0e7ef;
  max-height: 130px;
  overflow: auto;
  border: 0;
  flex: 0;
}

.recording-list :deep(.product-empty) {
  padding: 10px;
  min-height: 60px;
  gap: 4px;
}

.recording-list :deep(.product-empty svg) {
  display: none;
}

.recording-list :deep(.product-empty p) {
  display: none;
}

.list-heading {
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  padding: 18px 0;
  color: #70819a;
  display: none;
}

.recording-list :deep(.product-empty strong) {
  font-size: 13px;
  font-weight: 400;
}

.recording-item {
  border: 0;
  background: none;
  border-radius: 6px;
  width: 100%;
  text-align: left;
  cursor: pointer;
  color: #4c5e76;
  margin-bottom: 4px;
  padding: 8px;
}

.playback-main {
  min-width: 0;
  flex-direction: column;
  display: grid;
  grid-template-rows: minmax(330px,1.2fr) minmax(300px,1fr);
  gap: 10px;
}

.video-grid {
  flex: 1;
  padding: 11px;
  gap: 12px;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
}

.playback-video {
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  border-radius: 10px;
  min-height: 0;
  background: linear-gradient(145deg,#eaf2fb,#dce8f5);
}

.video-heading span {
  font-size: 11px;
  color: #8e9aac;
  display: none;
}

.playback-video :deep(.product-empty) {
  flex: 1;
  min-height: 0;
  height: 100%;
  padding: 35px 15px;
}

.playback-video :deep(.product-empty strong) {
  font-size: 17px;
}

.playback-video :deep(.product-empty p) {
  font-size: 14px;
}

.playback-video video {
  display: block;
  width: 100%;
  flex: 1;
  object-fit: contain;
  background: #e9eff7;
  min-height: 0;
}

.playback-video:after {
  content: '';
  position: absolute;
  bottom: 0;
  height: 42px;
  left: 0;
  right: 0;
  background: linear-gradient(transparent,#61708744);
  pointer-events: none;
}

.timeline-panel {
  padding: 16px 20px;
  min-height: 0;
}

.playback-controls {
  display: flex;
  align-items: center;
  border-bottom: 1px solid #e6ebf3;
  flex-wrap: nowrap;
  padding-bottom: 20px;
  margin-bottom: 10px;
  gap: 12px;
}

.sync-label {
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #7d8fa6;
  display: none;
}

.timeline-track {
  display: flex;
  align-items: center;
  margin: 22px 0;
  gap: 12px;
}

.track-background {
  background: #eef3f9;
  border-radius: 4px;
  position: relative;
  flex: 1;
  overflow: visible;
  height: 25px;
}

.playhead {
  position: absolute;
  width: 2px;
  background: #1265f1;
  height: 39px;
  top: -7px;
}

.playback-footnote {
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 0 8px 10px;
  display: none;
}

.export-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  margin: 0;
}

.export-heading h2 {
  flex: 1;
  margin: 0;
}

.export-heading .icon-only {
  border: 0;
  padding: 0;
  min-width: 25px;
  margin-left: 10px;
}

.export-panel>.muted {
  font-size: 12px;
  line-height: 1.8;
  margin-bottom: 25px;
  display: none;
}

.export-results {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 15px;
}

.original-links {
  border-top: 1px solid #e7ecf4;
  display: flex;
  flex-direction: column;
  margin-top: 18px;
  padding-top: 15px;
  gap: 10px;
}

.original-links a {
  color: #6d8099;
  text-decoration: none;
  font-size: 13px;
}

.playback-page .badge {
  font-size: 14px;
}

.export-panel input[type=checkbox] {
  height: 20px;
  width: 20px;
  flex: none;
  padding: 0;
}

.export-camera-select {
  border-bottom: 1px solid #dce3ef;
  margin: 18px 0 22px;
  padding-bottom: 18px;
}

.export-panel .original-links:empty {
  display: none;
}

.recording-tracks {
  position: relative;
  margin: 18px 0 0;
}

.recording-tracks .timeline-track {
  margin: 0 0 20px;
  min-height: 31px;
}

.recording-tracks .timeline-track:last-of-type {
  margin-bottom: 0;
}

.recording-tracks :deep(.clip-range) {
  left: 144px;
  right: 0;
  top: 0;
  bottom: 0;
}

.seek-bar {
  width: 100%;
  accent-color: #1265f1;
  cursor: pointer;
  padding: 0;
  -webkit-appearance: none;
  appearance: none;
  height: 5px;
  background: #e3eaf5;
  border: 0;
  border-radius: 3px;
  margin: 9px 0;
}

.seek-bar::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  background: var(--blue);
  border-radius: 50%;
  box-shadow: 0 0 0 2px #fff;
}

.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7,1fr);
  gap: 2px 1px;
}

.search-panel .camera-check {
  margin: 7px 0;
}

.original-downloads summary {
  cursor: pointer;
  padding: 4px 0;
}

.original-downloads .original-links {
  margin: 8px 0 0;
  padding: 8px 0;
  max-height: 90px;
  overflow: auto;
}

.playback-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: #111827;
}

.playback-page>.page-heading h1 {
  color: #0868ff;
  border-bottom: 3px solid #0868ff;
  padding-bottom: 12px;
  transform: translateY(7px);
}

.search-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 18px;
}

.export-panel {
  display: flex;
  flex-direction: column;
  grid-column: auto;
  min-height: 0;
  overflow: auto;
  padding: 18px;
}

.search-panel h2 {
  margin: 0 0 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid #dce3ef;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
  border-color: #d9e0eb;
}

.export-panel h2 {
  margin: 0 0 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid #dce3ef;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
  border-color: #d9e0eb;
}

.calendar-label {
  margin-bottom: 8px;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.search-panel h3 {
  border-top: 1px solid #dce3ef;
  padding-top: 18px;
  margin: 20px 0 10px;
  margin-top: 14px;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.time-range-title {
  border-top: 1px solid #dce3ef;
  padding-top: 18px;
  margin-top: 14px;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.month-calendar {
  border: 1px solid #dce3ef;
  padding: 0 8px 8px;
  min-height: 248px;
  border-color: #d9e0eb;
  border-radius: 9px;
  background: #fbfcff;
}

.month-calendar header strong {
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.month-calendar header button {
  border: 0;
  background: none;
  padding: 0 7px;
  font-size: 25px;
  font-weight: 500;
  color: #111827;
  cursor: pointer;
}

.calendar-grid button {
  display: flex;
  justify-content: center;
  align-items: center;
  border: 0;
  background: transparent;
  border-radius: 5px;
  height: 25px;
  font-size: 14px;
  font-weight: 500;
  color: #111827;
}

.calendar-grid span {
  display: flex;
  justify-content: center;
  align-items: center;
  border: 0;
  background: transparent;
  border-radius: 5px;
  height: 25px;
  font-size: 15px;
  font-weight: 600;
  color: #5f6b82;
}

.calendar-grid button.outside {
  color: #929daf;
}

.calendar-grid button.chosen {
  border-radius: 7px;
  background: #0868ff;
  color: #fff;
}

.camera-check {
  display: flex;
  align-items: center;
  margin: 9px 0;
  gap: 10px;
  white-space: nowrap;
  font-size: 16px;
  font-weight: 600;
  color: #111827;
}

.search-panel .camera-check>svg {
  display: none;
}

.camera-check input {
  width: 20px;
  height: 20px;
  padding: 0;
  margin: 0;
  accent-color: #0868ff;
}

.search-button {
  height: 46px;
  margin: auto 0 12px;
  width: 100%;
  order: 10;
  font-size: 17px;
  font-weight: 600;
  border-radius: 8px;
}

.time-range-inputs input {
  width: 92px;
  min-width: 0;
  padding: 8px 5px;
  font-size: 15px;
  font-weight: 500;
  border-color: #cdd6e4;
  background: #fbfcff;
  border-radius: 8px;
}

.video-heading {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  position: absolute;
  top: 10px;
  left: 12px;
  z-index: 2;
  padding: 5px 10px;
  background: #39485bdc;
  color: #fff;
  font-size: 18px;
  border-radius: 6px;
}

.playback-page .video-heading strong {
  font-weight: 600;
}

.playback-controls .btn {
  height: 38px;
  padding: 7px 17px;
  border-radius: 8px;
  color: #29364d;
}

.playback-controls .primary {
  color: #fff;
}

.playback-time {
  font-variant-numeric: tabular-nums;
  margin: 0 8px;
  font-size: 18px;
  font-weight: 600;
  color: #111827;

  border: 1px solid #d7dfeb;

  border-radius: 7px;

  height: 38px;

  min-width: 110px;

  display: grid;

  place-items: center;

  background: #fbfcff;
}

.playback-time span {
  font-size: 15px;
  color: #5f6b82;
}

.speed-label {
  display: flex;
  gap: 7px;
  align-items: center;
  margin-left: 24px;
  font-size: 15px;
  font-weight: 500;
  color: #5f6b82;

  padding-left: 34px;

  border-left: 1px solid #cdd5e4;
}

.speed-label select {
  border: 1px solid #dce4ef;
  padding: 7px;
  border-radius: 5px;
  background: white;
  min-width: 82px;
  font-size: 17px;
  color: #111827;
  border-color: #cdd6e4;
}

.time-axis {
  display: grid;
  justify-content: space-between;
  padding: 7px 0 18px;
  font-size: 14px;
  font-weight: 500;
  color: #37435b;

  grid-template-columns: 132px minmax(0,1fr);

  gap: 12px;

  align-items: center;
}

.timeline-track strong {
  padding: 4px 7px;
  font-size: 15px;
  font-weight: 650;
  color: #111827;
  background: #e4e9f2;
  border-radius: 5px;

  flex: 0 0 132px;

  width: 132px;

  min-width: 132px;

  white-space: nowrap;

  overflow: hidden;

  text-overflow: ellipsis;
}

.recorded-block {
  position: absolute;
  top: 0;
  border-radius: 4px;
  height: 25px;
  background: linear-gradient(#a4caff,#bed9ff);
}

.timeline-legend {
  border-top: 1px solid #edf1f6;
  display: flex;
  justify-content: space-between;
  padding-top: 22px;
  margin-top: 28px;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 15px;
  font-weight: 500;
  color: #253046;
}

.clip-range-caption {
  color: #155bd2;
}

.export-camera-select strong {
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.export-panel .form-field {
  margin-bottom: 22px;
  gap: 10px;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.export-format {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 4px;
  margin-bottom: 22px;
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}

.export-camera-select .camera-check {
  margin: 16px 0 0;
  font-size: 16px;
  font-weight: 600;
  color: #111827;
}

.export-panel input {
  height: 39px;
  font-size: 16px;
  font-weight: 500;
  border-color: #c9d3e2;
  border-radius: 8px;
  background: #fbfcff;
  color: #111827;
}

.export-panel input[type=datetime-local] {
  padding: 8px;
  width: 100%;
  font-size: 14px;
  font-weight: 500;
}

.export-format output {
  height: 39px;
  font-weight: 500;
  border-color: #c9d3e2;
  border-radius: 8px;
  background: #fbfcff;
  color: #111827;
  font-size: 17px;
}

.annotation-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 15px 0;
  border-top: 1px solid #dce3ef;
  border-bottom: 1px solid #dce3ef;
  margin-bottom: 12px;
  font-size: 16px;
  font-weight: 600;
  color: #111827;
  border-color: #d9e0eb;
}

.annotation-option span {
  font-size: 13px;
  font-weight: 500;
  color: #5f6b82;
}

.export-summary {
  display: flex;
  flex-direction: column;
  font-size: 16px;
  margin: 8px 0 20px;
  padding: 12px;
  gap: 6px;
  margin-bottom: 12px;
  background: linear-gradient(135deg,#eff2f8,#e9eef6);
  border-radius: 8px;
  color: #111827;
}

.export-summary strong {
  font-size: 16px;
  font-weight: 600;
  color: #253046;
}

.export-summary span {
  font-size: 16px;
  font-weight: 600;
}

.export-summary span:last-child {
  font-size: 13px;
  font-weight: 500;
  color: #5f6b82;
}

.export-button {
  width: 100%;
  height: 46px;
  margin-top: 0;
  font-size: 18px;
  font-weight: 600;
  border-radius: 8px;
  min-height: 46px;
  flex-shrink: 0;
}

.original-downloads {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 500;
  color: #5f6b82;
}

.recording-list:has(.recording-item) {
  flex: 0 0 105px;
  min-height: 105px;
  max-height: 105px;
  border-top: 1px solid #d9e0eb;
}

.recording-list .recording-item {
  margin: 0;
  padding: 5px 8px;
  border-radius: 6px;
  color: #26354b;
}

.recording-list .recording-item.selected {
  color: #0868ff;
  background: #e4edff;
}

.recording-item>div {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  font-weight: 600;
}

.recording-list .recording-item p {
  margin: 4px 0;
  font-size: 12px;
  color: #5f6b82;
}


.video-control-bar {
  position: absolute;
  z-index: 3;
  inset: auto 0 0;
  display: grid;
  grid-template-columns: 24px auto minmax(45px, 1fr) 24px 24px;
  align-items: center;
  gap: 10px;
  height: 41px;
  padding: 0 12px;
  background: linear-gradient(#344152b5, #1f2732e6);
  color: #fff;
}
.video-control-bar button {
  display: grid;
  place-items: center;
  width: 24px;
  height: 30px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #fff;
}
.video-control-bar button:hover { background: #ffffff22; }
.video-clock { white-space: nowrap; font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.video-seek {
  appearance: none;
  -webkit-appearance: none;
  width: 100%;
  height: 5px;
  min-width: 0;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: linear-gradient(to right, #2083ff var(--played), #c9d0da var(--played));
  cursor: pointer;
}
.video-seek::-webkit-slider-thumb {
  appearance: none;
  -webkit-appearance: none;
  width: 12px;
  height: 12px;
  border: 1px solid #2182ff;
  border-radius: 50%;
  background: #fff;
}
.video-seek::-moz-range-thumb { width: 12px; height: 12px; border: 1px solid #2182ff; border-radius: 50%; background: #fff; }
.playback-video:fullscreen { width: 100%; height: 100%; border-radius: 0; background: #17202b; }
.playback-video:fullscreen video { background: #17202b; }
.playback-video:fullscreen .video-control-bar { grid-template-columns: 30px auto minmax(80px,1fr) 30px 30px; gap: 20px; padding: 0 24px; height: 50px; }
.playback-video:fullscreen .video-clock { font-size: 16px; }
.playback-controls .play-main { width: 59px; padding: 0; flex-shrink: 0; }
.playback-controls .step-control { width: 57px; padding: 0; flex-shrink: 0; }
.playback-controls .play-main svg { width: 23px; height: 23px; }
.control-label { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.timeline-date { font-size: 15px; font-weight: 600; color: #111827; }
.time-ticks { display: flex; justify-content: space-between; min-width: 0; }
.export-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: auto; flex-shrink: 0; }
.export-actions .btn { height: 46px; font-size: 17px; border-radius: 8px; }
@media (max-width:1400px) {
  .video-control-bar { gap: 6px; padding: 0 9px; grid-template-columns: 22px auto minmax(30px,1fr) 22px 22px; }
  .video-clock { font-size: 12px; }
  .video-clock>span { display: none; }
  .playback-controls .play-main { width: 45px; }
  .playback-controls .step-control { width: 38px; }
  .speed-label { margin-left: 7px; padding-left: 15px; }
}

@media (max-width:1300px) {
  .playback-workspace {
    grid-template-columns: 220px minmax(420px,1fr);
    overflow: auto;
  }

  .playback-workspace.with-export {
    grid-template-columns: 220px minmax(420px,1fr) 260px;
    overflow: auto;
  }

  .export-panel {
    grid-column: auto;
    display: flex;
  }

  .video-grid {
    min-height: 0;
  }

  .playback-controls {
    gap: 8px;
  }

  .playback-controls .btn {
    padding: 7px 10px;
  }

  .playback-time {
    margin: 0;
  }
}

@media (max-width:1000px) {
  .playback-page {
    height: auto;
  }

  .playback-workspace {
    grid-template-columns: 220px 1fr;
    overflow: visible;
  }

  .playback-workspace.with-export {
    grid-template-columns: 220px 1fr;
    overflow: visible;
  }

  .export-panel {
    grid-column: 1/-1;
  }

  .playback-main {
    min-height: 700px;
  }

  .search-panel {
    min-height: 700px;
  }

  .export-camera-select {
    display: none;
  }
}

.export-format output{display:flex;align-items:center;border:1px solid #c9d3e2;padding:8px 12px;line-height:1.3}.original-links a[aria-disabled=true],.export-results a[aria-disabled=true]{pointer-events:none;opacity:.55}
</style>
