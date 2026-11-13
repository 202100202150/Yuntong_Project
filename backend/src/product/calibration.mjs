import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { Router } from 'express';
import { asyncRoute, fault } from './database.mjs';
import { permission } from './auth.mjs';
import { defaultSpatialConfig } from './spatial.mjs';

export function createCalibrationService(store,configuration,bridge) {
 const router=Router();router.use('/calibration',permission('devices'));
 const root=path.join(store.directory,'calibration');fs.mkdirSync(root,{recursive:true});
 const project=configuration.project;
 const script=path.join(project,'spatial_calibration.py');
 const python=process.env.YUNTONG_CALIBRATION_PYTHON||process.env.YUNTONG_CAMERA_PYTHON||'python';
 let child=null,job=null,closed=false;
 const publicJob=()=>job?{...job,capturedPairs:job.captureDirectory&&fs.existsSync(path.join(job.captureDirectory,'camera1'))?fs.readdirSync(path.join(job.captureDirectory,'camera1')).filter(name=>name.endsWith('.png')).length:job.capturedPairs,captureDirectory:undefined,resultDirectory:undefined}:null;
 const number=(value,fallback,min,max)=>{const n=Number(value??fallback);if(!Number.isFinite(n)||n<min||n>max)throw fault(400,'标定参数超出范围');return n;};
 function start(type,args,{captureDirectory,resultDirectory,...metadata}) {
  if(child)throw fault(409,'标定任务正在运行');
  if(closed)throw fault(503,'服务正在关闭');
  const id=randomUUID();
  job={id,type,status:'running',message:type==='capture'?'正在采集双路标定图像，请缓慢改变标定板位置与角度':'正在计算内外参与重投影误差',startedAt:Date.now(),captureDirectory,resultDirectory,...metadata};
  const active=job;
  const environment={...process.env,...configuration.runtimeOptions().env,PYTHONUNBUFFERED:'1'};
  child=spawn(python,args,{cwd:project,env:environment,shell:false,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let output='',settled=false;
  const append=data=>{output=(output+data.toString()).slice(-8000);};
  child.stdout.on('data',append);child.stderr.on('data',append);
  const finish=(code,error)=>{
   if(settled)return;settled=true;child=null;active.finishedAt=Date.now();
   const readJson=name=>{try{return JSON.parse(fs.readFileSync(path.join(resultDirectory||captureDirectory,name),'utf8'));}catch{return null;}};
   const report=type==='solve'?readJson('report.json'):readJson('capture-manifest.json');
   const succeeded=!error&&code===0&&(type==='capture'||report?.passed===true);
   active.status=succeeded?'succeeded':'failed';active.report=report;
   if(succeeded) {
    active.message=type==='capture'?'采集完成，可开始计算参数':'计算完成，质量检查通过；请核对后保存应用';
    if(type==='capture')store.set('calibrationCapture',{directory:captureDirectory,boardType:active.boardType,time:Date.now()});
    else {active.config=readJson('result.json');store.set('calibrationResult',{directory:resultDirectory,time:Date.now()});}
   }else{
    const missing=output.match(/No module named ['"]([A-Za-z0-9_.-]+)['"]/);
    active.message=missing?'标定运行环境缺少 '+missing[1]+' 模块':error?.code==='ENOENT'?'未找到标定 Python，请配置 YUNTONG_CALIBRATION_PYTHON':type==='capture'?'未获得足够标定图像，请检查相机连接、标定板可见范围和时差':'标定未通过，请检查采集数量、标定板尺寸与实测基线';
   }
   if(!closed)store.audit('system','标定任务结束',{id,type,status:active.status});
  };
  child.once('error',error=>finish(null,error));child.once('exit',code=>finish(code));
  const timeout=setTimeout(()=>{if(!settled)child?.kill();},type==='capture'?650_000:300_000);timeout.unref();
  child.once('close',()=>clearTimeout(timeout));
  return publicJob();
 }
 router.get('/calibration/job',(_req,res)=>res.json({job:publicJob()}));
 router.post('/calibration/capture',asyncRoute(async(req,res)=>{
  const pairs=number(req.body?.pairs,30,3,100),interval=number(req.body?.interval,4,1,15);
  if(!Number.isInteger(pairs))throw fault(400,'采集组数必须为整数');
  const duration=Math.min(600,pairs*interval+60),directory=path.join(root,randomUUID());
  fs.mkdirSync(directory,{recursive:true});
  if(req.body?.boardType&&req.body.boardType!=='aprilgrid')throw fault(400,'当前使用 AprilGrid 标定板');
  const value=start('capture',[script,'capture','--board-type','aprilgrid','--output',directory,'--pairs',String(pairs),'--interval',String(interval),'--duration',String(duration),'--max-skew-ms','40'],{captureDirectory:directory,boardType:'aprilgrid',requestedPairs:pairs});
  store.audit(req.user.username,'开始采集标定板',{jobId:value.id,pairs});res.status(202).json({job:value});
 }));
 router.post('/calibration/solve',asyncRoute(async(req,res)=>{
  const capture=store.get('calibrationCapture',null);if(!capture)throw fault(409,'请先完成采集');
  const minPairs=number(req.body?.minPairs,15,3,100),maxRms=number(req.body?.maxRms,2,0.1,10);
  if(!Number.isInteger(minPairs))throw fault(400,'最少样本数必须为整数');
  const baseline=number(req.body?.baselineMeters,undefined,0.01,1000);
  const directory=path.join(root,randomUUID());fs.mkdirSync(directory,{recursive:true});
  // Build a manual baseline anchor from the existing calibrated rig, retaining orientation.
  const current=path.join(store.directory,'spatial-config.json');
  const base=path.join(directory,'base.json');
  let config;
  try{config=JSON.parse(fs.readFileSync(current,'utf8'));}catch{}
  if(!config)config=defaultSpatialConfig();
  config.rig={...config.rig,baselineMeters:baseline,geometrySource:'manual',cameraPositions:null};
  config.extrinsics={...config.extrinsics,enabled:false};
  for(const id of ['camera1','camera2'])config.cameras[id].offset={x:0,y:0,z:0};
  fs.writeFileSync(base,JSON.stringify(config));
  const value=start('solve',[script,'calibrate','--board-type','aprilgrid','--input',capture.directory,'--base-config',base,'--output',path.join(directory,'result.json'),'--diagnostics',path.join(directory,'report.json'),'--min-pairs',String(minPairs),'--max-rms',String(maxRms)],{captureDirectory:capture.directory,resultDirectory:directory,boardType:'aprilgrid'});
  store.audit(req.user.username,'开始计算标定',{jobId:value.id,minPairs,maxRms,baselineMeters:baseline});res.status(202).json({job:value});
 }));
 router.post('/calibration/apply',asyncRoute(async(req,res)=>{
  if(child)throw fault(409,'请等待标定任务结束');
  if(job?.type!=='solve'||job.status!=='succeeded'||!job.report?.passed||!job.config)throw fault(409,'没有可应用的标定结果');
  const filename=path.join(store.directory,'spatial-config.json'),temporary=filename+'.tmp';
  fs.writeFileSync(temporary,JSON.stringify(job.config,null,2));fs.renameSync(temporary,filename);
  let applied=false;
  try{await bridge.request('/v1/spatial/config',{method:'POST',body:{config:job.config},ensureStarted:false});applied=true;}catch{}
  store.audit(req.user.username,'应用标定结果',{jobId:job.id});
  res.json({config:job.config,applied,...(!applied?{message:'标定参数已保存，摄像机服务启动后生效'}:{})});
 }));
 return {router,close:()=>{closed=true;if(child)child.kill();}};
}
