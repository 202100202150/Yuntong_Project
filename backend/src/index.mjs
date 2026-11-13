import express from 'express';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import 'dotenv/config';
import { frameRouter } from './routes/frame.mjs';
import { createDetectionRouter } from './routes/detection.mjs';
import { createRecordingRouter } from './routes/recording.mjs';
import { createSnapshotsRouter } from './routes/snapshots.mjs';
import { spatialRouter } from './routes/spatial.mjs';
import { attachFrameStream } from './ws/frame-stream.mjs';
import { startCameraBridge, stopCameraBridge, requestCameraBridge, configureCameraBridge, restartCameraBridge } from './sidecar/client.mjs';
import { normalizeStatus } from './normalize/status.mjs';
import { openProductDatabase, fault, asyncRoute } from './product/database.mjs';
import { createAuth, createUsersRouter, permission } from './product/auth.mjs';
import { createEventsRouter, ingestTargets, eventRecord } from './product/events.mjs';
import { createMediaService } from './product/media.mjs';
import { createConfiguration } from './product/config.mjs';
import { createCalibrationService } from './product/calibration.mjs';
import { createLocalSpatialRouter } from './product/spatial.mjs';

export async function startBackend(options={}) {
 const shouldStart=options.startBridge??options.startCamera??true;
 const dataDirectory=path.resolve(options.dataDirectory||process.env.YUNTONG_DATA_DIR||path.join(import.meta.dirname,'../data'));
 const staticDirectory=path.resolve(options.staticDirectory||process.env.YUNTONG_STATIC_DIR||path.join(import.meta.dirname,'../../frontend/dist'));
 const store=openProductDatabase(dataDirectory);
 const app=express(); app.disable('x-powered-by');
 app.use((req,res,next)=>{
  res.set('X-Content-Type-Options','nosniff');
  if(req.path.startsWith('/api'))res.set('Cache-Control','no-store');
  if(!['GET','HEAD','OPTIONS'].includes(req.method)&&req.headers.origin) {
    let origin;try{origin=new URL(req.headers.origin);}catch{return next(fault(403,'无效请求来源'));}
    const same=origin.host===req.headers.host;
    const dev=process.env.NODE_ENV!=='production'&&['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress)&&
      ['localhost','127.0.0.1'].includes(origin.hostname)&&['5173','5174'].includes(origin.port);
    if(!same&&!dev)return next(fault(403,'不允许跨站写入请求'));
  }
  next();
 });
 app.use(express.json({limit:'2mb'}));
 const auth=createAuth(store);
 app.use('/api/auth',auth.router);
 app.use('/api',auth.requireSession);
 const runtimeBridge=options.bridge||{request:(url,args={})=>requestCameraBridge(url,{...args,ensureStarted:args.ensureStarted??shouldStart}),start:startCameraBridge,stop:stopCameraBridge,configure:configureCameraBridge,restart:restartCameraBridge};
 let cachedStatus=null,lastStatus=0,inflight=null;
 const status=async()=>{
  if(cachedStatus&&Date.now()-lastStatus<800)return cachedStatus;
  if(inflight)return inflight;
  inflight=(async()=>{
    try {const result=await runtimeBridge.request('/v1/status');cachedStatus=normalizeStatus(result.data);}
    catch {cachedStatus=normalizeStatus({});cachedStatus.serviceState='error';cachedStatus.error='摄像机服务未连接，请检查设备配置与本地运行环境';cachedStatus.spatial={...cachedStatus.spatial,cameras:{},state:'error',message:cachedStatus.error};for(const camera of Object.values(cachedStatus.cameras)){camera.state='error';camera.frameAvailable=false;}}
    lastStatus=Date.now();return cachedStatus;
  })().finally(()=>{inflight=null;});
  return inflight;
 };
 const config=createConfiguration(store,{...runtimeBridge,status});
 const media=createMediaService(store,config.roots);
 const calibration=createCalibrationService(store,config,runtimeBridge);
 const localSpatial=createLocalSpatialRouter(store,config,runtimeBridge);
 app.get('/api/status',permission('monitor'),asyncRoute(async(_req,res)=>res.json(await status())));
 app.use('/api',config.router);
 app.use('/api/users',createUsersRouter(store));
 app.use('/api',createEventsRouter(store));
 app.use('/api',media.router);
 app.use('/api',calibration.router);
 app.use('/api',localSpatial.router);
 app.get('/api/overview',permission('monitor'),asyncRoute(async(_req,res)=>{
  const live=await status(),today=new Date();today.setHours(0,0,0,0);
  const rows=store.db.prepare('SELECT * FROM events ORDER BY started_at DESC LIMIT 5').all();
  const pendingRow=store.db.prepare("SELECT * FROM events WHERE status='pending' ORDER BY started_at DESC LIMIT 1").get();
  const hourly=Array.from({length:24},(_,hour)=>({hour,count:store.db.prepare('SELECT COUNT(*) AS n FROM events WHERE started_at>=? AND started_at<?').get(today.getTime()+hour*3600000,today.getTime()+(hour+1)*3600000).n}));
  res.json({onlineDevices:Object.values(live.cameras).filter(c=>c.state==='streaming').length,activeTargets:live.spatial.targets.length,pendingEvents:store.db.prepare("SELECT COUNT(*) AS n FROM events WHERE status='pending'").get().n,pendingEvent:pendingRow?eventRecord(pendingRow):null,todayEvents:store.db.prepare('SELECT COUNT(*) AS n FROM events WHERE started_at>=?').get(today.getTime()).n,recentEvents:rows.map(eventRecord),hourly,health:{video:live.serviceState==='ready',inference:live.engineState==='ready',calibration:live.spatial.state!=='uncalibrated'&&live.serviceState==='ready',storage:true}});
 }));
 app.use('/api/cameras',permission('monitor'));
 app.use('/api/detection',permission('review'));
 app.use('/api/recording',permission('review'));
 app.use('/api/snapshots',permission('export'));
 app.use('/api/spatial',(req,res,next)=>req.method==='GET'?permission('monitor')(req,res,next):permission('devices')(req,res,next));
 app.use('/api',frameRouter,createDetectionRouter(runtimeBridge.request),createRecordingRouter(runtimeBridge.request),createSnapshotsRouter({request:runtimeBridge.request,roots:config.roots,indexSnapshot:media.indexSnapshot,audit:store.audit}),spatialRouter);
 app.use('/api',(_req,_res,next)=>next(fault(404,'接口不存在')));
 if(fs.existsSync(path.join(staticDirectory,'index.html'))) {
   app.use(express.static(staticDirectory,{index:false}));
   app.get('*',(req,res,next)=>{if(req.path.includes('.'))return next();res.sendFile(path.join(staticDirectory,'index.html'));});
 }
 app.use((error,req,res,_next)=>{
  if(res.headersSent)return;
  const statusCode=Number(error.status)||500;
  res.status(statusCode>=400&&statusCode<=599?statusCode:500).json({error:statusCode===500?'服务处理失败，请稍后重试':error.message});
 });
 const server=http.createServer(app);
 const wss=attachFrameStream(server,(req)=>auth.sessionUser(req)?.permissions.includes('monitor')??false);
 const port=options.port??Number(process.env.PORT||8000),host=options.host||process.env.HOST||'127.0.0.1';
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,host,resolve);});
 if(shouldStart)runtimeBridge.start?.().catch(()=>{});
 let busy=false,closed=false,tickPromise=Promise.resolve();
 const tick=()=>{
  if(busy||closed)return;busy=true;
  tickPromise=(async()=>{try{const live=await status();if(!closed){const created=ingestTargets(store,live);await media.captureEventEvidence(runtimeBridge.request,created);}await media.scan();}catch{}finally{busy=false;}})();
 };
 const timer=setInterval(tick,2000);timer.unref();
 if(shouldStart)tick();
 let closePromise;
 const close=()=>closePromise??=(async()=>{
  closed=true;clearInterval(timer);
  calibration.close();
  for(const client of wss.clients)client.terminate();wss.close();
  await runtimeBridge.stop?.();
  await tickPromise;
  await new Promise(resolve=>server.close(resolve));store.db.close();
 })();
 return {app,server,url:'http://'+(host==='0.0.0.0'?'127.0.0.1':host)+':'+server.address().port,close,store,status,config,media};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
 startBackend().then(runtime=>{
  console.log('[backend] '+runtime.url);
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>runtime.close().then(()=>process.exit(0)));
 }).catch(error=>{console.error('[backend] 启动失败: '+error.message);process.exitCode=1;});
}
