import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Router } from 'express';
import { asyncRoute, fault } from './database.mjs';
import { permission } from './auth.mjs';
const run=promisify(execFile);
export function mediaItem(row) {
 return {id:row.id,name:path.basename(row.path),cameraId:row.camera_id,kind:row.kind,startedAt:row.started_at,duration:row.duration,bytes:row.bytes,annotated:Boolean(row.annotated),ready:Boolean(row.ready),url:'/api/media/'+row.id+'/content'};
}
function fileTime(name,fallback) {
 const m=name.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
 return m?new Date(+m[1],+m[2]-1,+m[3],+m[4],+m[5],+m[6]).getTime():fallback;
}
export function createMediaService(store,roots) {
 const exportsPath=path.join(store.directory,'exports'); fs.mkdirSync(exportsPath,{recursive:true});
 const canonicalRoots=()=>roots().filter(p=>fs.existsSync(p)).map(p=>fs.realpathSync(p));
 function safeFile(filename,exported=false) {
   let resolved; try {resolved=fs.realpathSync(filename);}catch{throw fault(404,'录像文件不存在');}
   const dirs=exported?[fs.realpathSync(exportsPath)]:canonicalRoots();
   if(!dirs.some(dir=>{const rel=path.relative(dir,resolved);return rel && !rel.startsWith('..')&&!path.isAbsolute(rel);}))throw fault(403,'文件不在录像目录');
   return resolved;
 }
 let scanning=false;
 function indexSnapshot(filename,cameraId,annotated=false) {
  if(!['camera1','camera2'].includes(cameraId))throw fault(400,'摄像机不存在');
  const source=safeFile(filename),stat=fs.statSync(source);
  if(!stat.isFile()||stat.size===0||!/\.(png|jpe?g)$/i.test(source))throw fault(400,'抓拍文件无效');
  const id=createHash('sha256').update(source).digest('hex').slice(0,32);
  store.db.prepare('INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET bytes=excluded.bytes,annotated=excluded.annotated,ready=1')
   .run(id,source,cameraId,'image',fileTime(path.basename(source),stat.birthtimeMs||Date.now()),0,stat.size,Number(annotated),1);
  return {mediaId:id,name:path.basename(source),downloadUrl:'/api/media/'+id+'/download'};
 }
 async function captureEventEvidence(request, eventIds) {
  if (!eventIds.length) return;
  try {
   const pair=await request('/v1/cameras/preview-pair',{responseType:'buffer',timeoutMs:2000,requestHeaders:{'X-After-Camera1-Sequence':'0','X-After-Camera2-Sequence':'0','X-Wait-Milliseconds':'750'}});
   if(!pair.data||pair.statusCode===204)return;
   const split=Number(pair.headers['x-camera1-length']);
   if(!Number.isSafeInteger(split)||split<1||split>=pair.data.length)return;
   const now=Date.now(),date=new Date(now),pad=n=>String(n).padStart(2,'0');
   const stamp=date.getFullYear()+pad(date.getMonth()+1)+pad(date.getDate())+'_'+pad(date.getHours())+pad(date.getMinutes())+pad(date.getSeconds())+'_'+String(date.getMilliseconds()).padStart(3,'0');
   const evidence=[];
   for(const [i,bytes] of [pair.data.subarray(0,split),pair.data.subarray(split)].entries()) {
    const cameraId='camera'+(i+1),directory=roots()[i];fs.mkdirSync(directory,{recursive:true});
    const file=path.join(directory,cameraId+'_event_'+stamp+'.jpg');fs.writeFileSync(file,bytes,{flag:'wx'});
    const id=createHash('sha256').update(file).digest('hex').slice(0,32);
    store.db.prepare('INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?)').run(id,file,cameraId,'image',now,0,bytes.length,0,1);
    evidence.push(id);
   }
   for(const eventId of eventIds) {
    const event=store.db.prepare('SELECT payload FROM events WHERE id=?').get(eventId);if(!event)continue;
    store.db.prepare('UPDATE events SET payload=? WHERE id=?').run(JSON.stringify({...JSON.parse(event.payload),evidence}),eventId);
   }
  } catch { /* Camera loss must not prevent an event from being retained. */ }
 }
 async function scan() {
  if(scanning)return;scanning=true;
  try {
   for(const directory of canonicalRoots()) {
    for(const entry of fs.readdirSync(directory,{withFileTypes:true})) {
     if(!entry.isFile()||!/^camera[12]_.*\.(mp4|mkv|png|jpe?g)$/i.test(entry.name))continue;
     const full=path.join(directory,entry.name); const stat=fs.statSync(full);
     if(stat.size===0||Date.now()-stat.mtimeMs<4000)continue;
     const old=store.db.prepare('SELECT * FROM media WHERE path=?').get(full);
     if(old?.bytes===stat.size&&old.ready)continue;
     const kind=/\.(mp4|mkv)$/i.test(entry.name)?'video':'image';
     let duration=0;
     if(kind==='video') {
      try {const result=await run(process.env.YUNTONG_FFPROBE||'ffprobe',['-v','error','-show_entries','format=duration','-of','json',full],{timeout:10_000,windowsHide:true,maxBuffer:1024*1024});duration=Number(JSON.parse(result.stdout).format?.duration)||0;} catch {}
     }
     const id=createHash('sha256').update(full).digest('hex').slice(0,32);
     store.db.prepare('INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET duration=excluded.duration,bytes=excluded.bytes,ready=excluded.ready')
      .run(id,full,entry.name.startsWith('camera1')?'camera1':'camera2',kind,fileTime(entry.name,stat.birthtimeMs||stat.mtimeMs),duration,stat.size,1,1);
    }
   }
  }finally{scanning=false;}
 }
 const router=Router();
 router.get('/media',permission('playback'),asyncRoute(async(req,res)=>{
  await scan();const where=['ready=1'];const args=[];
  if(req.query.date) {
   if(!/^\d{4}-\d{2}-\d{2}$/.test(req.query.date))throw fault(400,'日期格式无效');
   const from=new Date(req.query.date+'T00:00:00').getTime();if(!Number.isFinite(from))throw fault(400,'日期无效');
   where.push('started_at>=? AND started_at<?');args.push(from,from+86400000);
  }
  if(['camera1','camera2'].includes(req.query.cameraId)){where.push('camera_id=?');args.push(req.query.cameraId);}
  const rows=store.db.prepare('SELECT * FROM media WHERE '+where.join(' AND ')+' ORDER BY started_at DESC LIMIT 500').all(...args);
  res.json({items:rows.map(mediaItem)});
 }));
 const rowFor=id=>{const row=store.db.prepare('SELECT * FROM media WHERE id=? AND ready=1').get(id);if(!row)throw fault(404,'录像不存在');return row;};
 router.get('/media/:id/content',permission('playback'),(req,res,next)=>{
  try{const row=rowFor(req.params.id);res.sendFile(safeFile(row.path),{headers:{'Cache-Control':'private, no-store'}},err=>err&&next(err));}catch(err){next(err);}
 });
 router.get('/media/:id/download',permission('export'),(req,res,next)=>{
  try{const row=rowFor(req.params.id),filename=safeFile(row.path);if(req.method==='GET')store.audit(req.user.username,row.kind==='image'?'下载抓拍':'下载录像',{id:row.id});res.download(filename,path.basename(row.path),err=>err&&next(err));}catch(err){next(err);}
 });
 let exporting=false;
 router.post('/media/:id/export',permission('export'),asyncRoute(async(req,res)=>{
  if(exporting)throw fault(409,'已有片段正在导出，请稍后重试');
  const row=rowFor(req.params.id);
  if(row.kind!=='video')throw fault(400,'请选择录像');
  const start=Number(req.body?.start),end=Number(req.body?.end);
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end-start>3600||(row.duration&&end>row.duration+0.1))throw fault(400,'请选择有效的片段起止秒数，单次最多一小时');
  const source=safeFile(row.path),id=randomUUID(),output=path.join(exportsPath,id+'.mp4');
  exporting=true;
  try {
   await run(process.env.YUNTONG_FFMPEG||'ffmpeg',['-nostdin','-v','error','-ss',String(start),'-i',source,'-t',String(end-start),'-map','0:v:0','-an','-c:v','libx264','-preset','fast','-movflags','+faststart',output],{timeout:180_000,windowsHide:true,maxBuffer:1024*1024});
   store.audit(req.user.username,'导出录像片段',{mediaId:row.id,start,end});
   res.json({url:'/api/media-exports/'+id,filename:path.basename(output)});
  }catch(err){if(fs.existsSync(output))fs.unlinkSync(output);throw fault(503,err.code==='ENOENT'?'未找到 FFmpeg，请配置 YUNTONG_FFMPEG 后重试':'录像导出失败，请检查格式或缩短片段');}
  finally{exporting=false;}
 }));
 router.get('/media-exports/:id',permission('export'),(req,res,next)=>{
  try{if(!/^[a-f0-9-]{36}$/.test(req.params.id))throw fault(404,'导出文件不存在');res.download(safeFile(path.join(exportsPath,req.params.id+'.mp4'),true),'airspace-clip.mp4',err=>err&&next(err));}catch(err){next(err);}
 });
 return {router,scan,roots,captureEventEvidence,indexSnapshot};
}
