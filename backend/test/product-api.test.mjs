import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { startBackend } from '../src/index.mjs';
import { ingestTargets } from '../src/product/events.mjs';
import { confirmSnapshot } from '../src/routes/snapshots.mjs';
import { normalizeStatus } from '../src/normalize/status.mjs';

async function fixture(t,bridge={}) {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'yuntong-api-'));
 const runtime=await startBackend({port:0,dataDirectory:dir,startBridge:false,bridge:{request:async()=>{throw Error('offline');},configure(){},async restart(){throw Error('offline');},async stop(){},...bridge}});
 let cookie='';
 const call=async(route,method='GET',body,headers={})=>{
  const result=await fetch(runtime.url+'/api'+route,{method,headers:{...(cookie?{cookie}:{}),...(body!==undefined?{'content-type':'application/json'}:{}),...headers},body:body===undefined?undefined:JSON.stringify(body)});
  const value=await result.json();return {status:result.status,value,headers:result.headers};
 };
 const setup=async()=>{const r=await call('/auth/setup','POST',{username:'admin',displayName:'Administrator',password:'strong-password-2026'});assert.equal(r.status,201);cookie=r.headers.get('set-cookie').split(';')[0];return r;};
 t.after(async()=>{await runtime.close();fs.rmSync(dir,{recursive:true,force:true});});
 return {runtime,dir,call,setup,setCookie:value=>{cookie=value},getCookie:()=>cookie};
}
test('public authentication status exposes only the site brand and shares the settings fallback',async t=>{
 const f=await fixture(t);
 assert.deepEqual((await f.call('/auth/status')).value,{setupRequired:true,user:null,siteName:'校园监测'});
 f.runtime.store.set('general',{siteName:'深圳技术大学 · 校园监测',retentionDays:90,notifyEvents:false,privateToken:'brand-private-token'});
 f.runtime.store.set('devices',[{id:'camera1',password:'private-camera-password'}]);
 const branded=await f.call('/auth/status');
 assert.deepEqual(branded.value,{setupRequired:true,user:null,siteName:'深圳技术大学 · 校园监测'});
 assert.equal(branded.headers.get('cache-control'),'no-store');
 await f.setup();
 assert.equal((await f.call('/settings')).value.settings.siteName,branded.value.siteName);
 for(const siteName of ['',null,42]){
  f.runtime.store.set('general',{siteName});
  const status=await f.call('/auth/status');
  assert.equal(status.value.siteName,'校园监测');
  assert.deepEqual(Object.keys(status.value).sort(),['setupRequired','siteName','user']);
  assert.equal((await f.call('/settings')).value.settings.siteName,status.value.siteName);
 }
});

test('bootstrap, cookie auth, origin protection and durable users',async t=>{
 const f=await fixture(t);
 assert.equal((await f.call('/events')).status,401);
 assert.equal((await f.call('/auth/status')).value.setupRequired,true);
 assert.equal((await f.call('/auth/setup','POST',{username:'proxy',password:'strong-password-2026'},{'x-forwarded-for':'203.0.113.1'})).status,403);
 const setup=await f.setup();
 assert.match(setup.headers.get('set-cookie'),/HttpOnly/);
 assert.match(setup.headers.get('set-cookie'),/SameSite=Strict/);
 assert.equal((await f.call('/auth/status')).value.user.role,'admin');
 assert.equal((await f.call('/auth/setup','POST',{username:'second',password:'strong-password'})).status,409);
 assert.equal((await f.call('/settings','PUT',{siteName:'Changed'},{origin:'https://untrusted.example'})).status,403);
 const users=(await f.call('/users')).value.users;
 assert.equal(users.length,1);assert.equal(users[0].password_hash,undefined);
 const stored=f.runtime.store.db.prepare('SELECT password_hash FROM users').get().password_hash;
 assert.notEqual(stored,'strong-password-2026');
 assert.equal((await f.call('/users/'+users[0].id,'PATCH',{enabled:false})).status,409);
 assert.equal((await f.call('/auth/logout','POST')).status,200);
 assert.equal((await f.call('/events')).status,401);
 assert.equal((await f.call('/auth/login','POST',{username:'admin',password:'wrong'})).status,401);
 assert.equal((await f.call('/auth/login','POST',{username:'admin',password:'strong-password-2026'})).status,200);
});
test('event ingestion deduplicates active tracks and review is audited and irreversible',async t=>{
 const f=await fixture(t);await f.setup();
 const target={id:'T-017',x:3,y:5,z:10,trackingState:'measured'};
 const live={serviceState:'ready',detecting:true,spatial:{targets:[target]},cameras:{camera1:{objects:[]},camera2:{objects:[]}}};
 const now=Date.now();
 ingestTargets(f.runtime.store,live,now);ingestTargets(f.runtime.store,live,now+1000);
 const list=(await f.call('/events')).value;assert.equal(list.events.length,1);assert.equal(list.pending,1);
 const event=list.events[0];assert.equal(event.payload.trajectory.length,2);
 const review=await f.call('/events/'+event.id+'/review','POST',{conclusion:'drone',note:'核对双路画面'});
 assert.equal(review.status,200);assert.equal(review.value.event.status,'reviewed');
 assert.equal((await f.call('/events/'+event.id+'/review','POST',{conclusion:'other'})).status,409);
 assert.equal((await f.call('/events')).value.pending,0);
 ingestTargets(f.runtime.store,{...live,spatial:{targets:[{...target,id:'old-pending'}]}},now-3600000);
 for(let i=0;i<6;i++){
  const id='reviewed-'+i;f.runtime.store.db.prepare("INSERT INTO events(id,track_id,started_at,last_seen_at,status,payload) VALUES(?,?,?,?,?,?)").run(id,id,now+i+2000,now+i+2000,'reviewed',JSON.stringify({trajectory:[]}));
 }
 const overview=(await f.call('/overview')).value;
 assert.equal(overview.recentEvents.every(item=>item.status==='reviewed'),true);
 assert.equal(overview.pendingEvent.trackId,'old-pending');
 assert.ok((await f.call('/audit')).value.entries.some(e=>e.action==='复核事件'));
 const user=await f.call('/users','POST',{username:'audit01',displayName:'Auditor',password:'strong-password-123',role:'auditor'});
 assert.equal(user.status,201);
 const login=await f.call('/auth/login','POST',{username:'audit01',password:'strong-password-123'});f.setCookie(login.headers.get('set-cookie').split(';')[0]);
 assert.equal((await f.call('/events')).status,200);
 assert.equal((await f.call('/events/'+event.id+'/review','POST',{conclusion:'other'})).status,403);
 assert.equal((await f.call('/users')).status,403);
});
test('recording lookup handles byte ranges, safe paths and export validation',async t=>{
 const f=await fixture(t);await f.setup();
 const dir=path.join(f.dir,'recordings','camera1');fs.mkdirSync(dir,{recursive:true});
 const file=path.join(dir,'camera1_video_20261112_102800_000000.mp4');fs.writeFileSync(file,Buffer.from('0123456789'));
 f.runtime.store.db.prepare('INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?)').run('file1',file,'camera1','video',Date.now(),60,10,1,1);
 const response=await fetch(f.runtime.url+'/api/media/file1/content',{headers:{cookie:f.getCookie(),range:'bytes=2-5'}});
 assert.equal(response.status,206);assert.equal(await response.text(),'2345');
 assert.equal((await f.call('/media/file1/export','POST',{start:10,end:9})).status,400);
 assert.equal((await f.call('/media/file1/export','POST',{start:0,end:100})).status,400);
 const outside=path.join(f.dir,'private.txt');fs.writeFileSync(outside,'private');
 f.runtime.store.db.prepare('INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?)').run('outside',outside,'camera1','video',Date.now(),1,7,1,1);
 assert.equal((await f.call('/media/outside/content')).status,403);
});
test('offline status is truthful; configuration validates and persists independently of GPU',async t=>{
 const f=await fixture(t);await f.setup();
 const status=(await f.call('/status')).value;assert.equal(status.serviceState,'error');assert.equal(status.cameras.camera1.frameAvailable,false);
 assert.deepEqual(status.spatial.cameras,{});
 const overview=(await f.call('/overview')).value;assert.equal(overview.onlineDevices,0);assert.equal(overview.activeTargets,0);
 assert.equal((await f.call('/models/config','PUT',{threshold:1.1})).status,400);
 const saved=await f.call('/models/config','PUT',{threshold:0.42,detectionFps:10,holdSeconds:0.6});
 assert.equal(saved.status,200);assert.equal(saved.value.applied,false);
 assert.equal((await f.call('/models/config')).value.config.threshold,0.42);
 assert.equal((await f.call('/devices/camera1','PATCH',{host:'127.0.0.1;echo secret'})).status,400);
 const configured=await f.call('/devices/camera1','PATCH',{host:'192.0.2.10',password:'device-password'});
 assert.equal(configured.status,200);
 const device=(await f.call('/devices')).value.devices[0];assert.equal(device.password,undefined);assert.equal(device.passwordConfigured,true);
});
test('spatial configuration bootstraps without a GPU and validates with the calibration runtime',async t=>{
 const f=await fixture(t);await f.setup();
 const migrated=path.join(f.dir,'spatial-config.json');if(fs.existsSync(migrated))fs.unlinkSync(migrated);
 const current=await f.call('/spatial/config');
 assert.equal(current.status,200);assert.equal(current.value.config.cameras.camera1.intrinsics.fx,null);
 const config=current.value.config;config.rig.baselineMeters=5.08;
 const saved=await f.call('/spatial/config','POST',{config});
 if(saved.status===503){t.diagnostic('Calibration Python dependencies unavailable: '+saved.value.error);return;}
 assert.equal(saved.status,200);assert.equal(saved.value.applied,false);
 assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir,'spatial-config.json'),'utf8')).rig.baselineMeters,5.08);
 assert.equal((await f.call('/spatial/config')).value.config.rig.baselineMeters,5.08);
 config.cameras.camera1.intrinsics.fx=-1;
 assert.equal((await f.call('/spatial/config','POST',{config})).status,400);
 assert.equal((await f.call('/spatial/config')).value.config.cameras.camera1.intrinsics.fx,null);
 assert.equal((await f.call('/calibration/solve','POST',{baselineMeters:5.08})).status,409);
 assert.equal((await f.call('/calibration/capture','POST',{pairs:1})).status,400);
});



test('new user enabled state and unsupported settings cannot silently succeed',async t=>{
 const f=await fixture(t);await f.setup();
 const created=await f.call('/users','POST',{username:'disabled01',displayName:'Disabled',password:'strong-password-2026',role:'operator',enabled:false});
 assert.equal(created.status,201);assert.equal(created.value.user.enabled,false);
 assert.equal((await f.call('/auth/login','POST',{username:'disabled01',password:'strong-password-2026'})).status,401);
 assert.equal((await f.call('/users','POST',{username:'invalid01',displayName:'Invalid',password:'strong-password-2026',role:'operator',enabled:'false'})).status,400);
 f.runtime.store.set('general',{siteName:'Before',retentionDays:30,notifyEvents:true,privateToken:'private-value'});
 assert.deepEqual((await f.call('/settings')).value.settings,{siteName:'Before'});
 assert.equal((await f.call('/settings','PUT',{siteName:'Must not save',retentionDays:60})).status,400);
 assert.equal(f.runtime.store.get('general').siteName,'Before');
 assert.equal((await f.call('/settings','PUT',{siteName:'After'})).status,200);
 assert.deepEqual(f.runtime.store.get('general'),{siteName:'After',retentionDays:30,notifyEvents:true,privateToken:'private-value'});
 assert.equal((await f.call('/auth/status')).value.siteName,'After');
});

test('saved camera and model settings configure the runtime and TCP test checks a real port',async t=>{
 const configured=[];let restarts=0;
 const f=await fixture(t,{configure:options=>configured.push(options),restart:async()=>{restarts++}});await f.setup();
 const listener=net.createServer(socket=>socket.end());await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>listener.close(resolve)));
 const changed=await f.call('/devices/camera1','PATCH',{host:'127.0.0.1',port:listener.address().port,channel:3,stream:'sub',password:'private-device-password'});
 assert.equal(changed.status,200);assert.equal(changed.value.applied,true);assert.equal(restarts,1);
 assert.equal(configured.at(-1).env.CAMERA1_RTSP_PATH,'/Streaming/Channels/302');
 assert.equal(configured.at(-1).env.CAMERA1_PASSWORD,'private-device-password');
 assert.equal((await f.call('/devices/camera1/test','POST')).value.connected,true);
 const changedModel=await f.call('/models/config','PUT',{threshold:0.37,detectionFps:7.5,holdSeconds:0.25});
 assert.equal(changedModel.value.applied,true);assert.equal(restarts,2);
 assert.equal(configured.at(-1).env.CAMERA_MODEL_THRESHOLD,'0.37');
 assert.equal(configured.at(-1).env.CAMERA_HOLD_SECONDS,'0.25');
 assert.equal(configured.at(-1).detectionFps,7.5);
 assert.equal(f.runtime.store.get('model').threshold,0.37);
 assert.equal(JSON.stringify(changed.value).includes('private-device-password'),false);
});

test('missing runtime apply capability cannot report an applied configuration',async t=>{
 const f=await fixture(t,{restart:undefined});await f.setup();
 const result=await f.call('/models/config','PUT',{threshold:0.33});
 assert.equal(result.status,200);assert.equal(result.value.applied,false);assert.equal(f.runtime.store.get('model').threshold,0.33);
});

test('recording reports actual writer states and detection returns confirmed runtime state',async t=>{
 let phase='armed',running=false;const calls=[];
 const raw=()=>({recording:true,detection:{running,modelState:running?'loading':'idle'},cameras:{camera1:{state:'streaming',recording:{state:phase}},camera2:{state:'streaming',recording:{state:phase}}}});
 const f=await fixture(t,{request:async(url,options)=>{calls.push([url,options]);if(url==='/v1/recording/toggle')return{data:{recording:phase!=='stopping'}};if(url==='/v1/detection/start'||url==='/v1/detection/stop')return{data:{accepted:true}};return{data:raw()}}});await f.setup();
 let result=await f.call('/recording/toggle','POST');assert.equal(result.value.recording,false);assert.equal(result.value.requestedRecording,true);assert.equal(result.value.cameras.camera1.state,'armed');
 phase='recording';result=await f.call('/recording/toggle','POST');assert.equal(result.value.recording,true);
 phase='stopping';result=await f.call('/recording/toggle','POST');assert.equal(result.value.recording,false);assert.equal(result.value.requestedRecording,false);assert.equal(result.value.cameras.camera2.state,'stopping');
 assert.equal(normalizeStatus(raw()).recording,false);
 assert.equal((await f.call('/detection/start','POST')).value.detecting,false);
 running=true;assert.equal((await f.call('/detection/stop','POST')).value.detecting,true);
 assert.ok(calls.some(([url,options])=>url==='/v1/detection/start'&&options.method==='POST'));
});

test('snapshots confirm complete files, register downloadable media and protect private paths',async t=>{
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR0kAAAAASUVORK5CYII=','base64');
 let filename='',noFrames=false;const f=await fixture(t,{request:async url=>{
  if(url==='/v1/snapshots'){if(noFrames)return{data:{snapshots:{camera1:{queued:false,error:'no frame'},camera2:{queued:false,error:'no frame'}}}};fs.mkdirSync(path.dirname(filename),{recursive:true});fs.writeFileSync(filename,png.subarray(0,16));setTimeout(()=>fs.writeFileSync(filename,png),75);return{data:{snapshots:{camera1:{queued:true,path:filename,detectionOverlay:true},camera2:{queued:false,error:'no frame'}}}}}
  return{data:{cameras:{}}};
 }});await f.setup();
 filename=path.join(f.dir,'recordings','camera1','camera1_snapshot_20261112_120000.png');
 const result=await f.call('/snapshots','POST');assert.equal(result.status,200);
 const saved=result.value.results[0];assert.equal(saved.saved,true);assert.equal(saved.queued,false);assert.ok(saved.mediaId);assert.equal(saved.downloadUrl,'/api/media/'+saved.mediaId+'/download');
 assert.equal(JSON.stringify(result.value).includes(f.dir),false);
 const row=f.runtime.store.db.prepare('SELECT * FROM media WHERE id=?').get(saved.mediaId);assert.equal(row.ready,1);assert.equal(row.annotated,1);
 const head=await fetch(f.runtime.url+saved.downloadUrl,{method:'HEAD',headers:{cookie:f.getCookie()}});assert.equal(head.status,200);
 assert.equal(f.runtime.store.db.prepare("SELECT COUNT(*) AS n FROM audit WHERE action='下载抓拍'").get().n,0);
 const download=await fetch(f.runtime.url+saved.downloadUrl,{headers:{cookie:f.getCookie()}});assert.equal(download.status,200);assert.deepEqual(Buffer.from(await download.arrayBuffer()),png);
 assert.equal(f.runtime.store.db.prepare("SELECT COUNT(*) AS n FROM audit WHERE action='下载抓拍'").get().n,1);
 assert.equal(f.runtime.store.db.prepare("SELECT COUNT(*) AS n FROM audit WHERE action='下载录像'").get().n,0);
 assert.equal((await fetch(f.runtime.url+saved.downloadUrl)).status,401);
 const partial=path.join(path.dirname(filename),'camera1_snapshot_partial.png');fs.writeFileSync(partial,png.subarray(0,16));
 assert.equal(await confirmSnapshot(partial,[path.dirname(filename)],0),null);
 const outside=path.join(f.dir,'outside.png');fs.writeFileSync(outside,png);assert.equal(await confirmSnapshot(outside,[path.dirname(filename)],0),null);
 noFrames=true;const failed=await f.call('/snapshots','POST');assert.equal(failed.status,503);assert.equal(failed.value.results.length,2);assert.match(failed.value.error,/C1：no frame；C2：no frame/);
});

test('model validation and clip export fail truthfully when required tools are unavailable',async t=>{
 const f=await fixture(t);await f.setup();
 const upload=await fetch(f.runtime.url+'/api/models/upload',{method:'POST',headers:{cookie:f.getCookie(),'content-type':'application/octet-stream','x-filename':'test.onnx'},body:Buffer.alloc(32,1)});
 assert.equal(upload.status,201);const version=(await upload.json()).versions.find(item=>item.name==='test.onnx');assert.ok(version);
 assert.equal((await f.call('/models/'+version.id+'/activate','POST')).status,409);
 const priorPython=process.env.YUNTONG_MODEL_PYTHON,priorFfmpeg=process.env.YUNTONG_FFMPEG;
 try{
  process.env.YUNTONG_MODEL_PYTHON=path.join(f.dir,'missing-python.exe');
  assert.equal((await f.call('/models/'+version.id+'/validate','POST')).status,422);
  assert.equal(f.runtime.store.get('models').find(item=>item.id===version.id).validatedAt,undefined);
  const directory=path.join(f.dir,'recordings','camera1');fs.mkdirSync(directory,{recursive:true});const file=path.join(directory,'camera1_video_clip.mp4');fs.writeFileSync(file,'not-a-real-video');
  f.runtime.store.db.prepare('INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?)').run('export-test',file,'camera1','video',Date.now(),60,16,1,1);
  process.env.YUNTONG_FFMPEG=path.join(f.dir,'missing-ffmpeg.exe');
  const exported=await f.call('/media/export-test/export','POST',{start:0,end:10});assert.equal(exported.status,503);assert.equal(exported.value.url,undefined);
 }finally{
  if(priorPython===undefined)delete process.env.YUNTONG_MODEL_PYTHON;else process.env.YUNTONG_MODEL_PYTHON=priorPython;
  if(priorFfmpeg===undefined)delete process.env.YUNTONG_FFMPEG;else process.env.YUNTONG_FFMPEG=priorFfmpeg;
 }
});
