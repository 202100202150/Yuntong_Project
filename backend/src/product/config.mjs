import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import { randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import express, { Router } from 'express';
import { parse as parseEnv } from 'dotenv';
import { asyncRoute, fault, requiredText } from './database.mjs';
import { permission, readSiteName } from './auth.mjs';
const run=promisify(execFile);
export function createConfiguration(store,bridge) {
 const project=path.resolve(process.env.YUNTONG_CAMERA_PROJECT_DIR||path.resolve(import.meta.dirname,'../../../camera-rtsp-low-latency'));
 let env={...process.env};
 const envFile=path.join(project,'.env');if(fs.existsSync(envFile))env={...parseEnv(fs.readFileSync(envFile)),...process.env};
 const ids=['camera1','camera2'];
 const deviceDefaults=()=>ids.map((id,i)=>{const prefix='CAMERA'+(i+1);return {id,label:env[prefix+'_LABEL']|| (i?'C2 图书馆楼顶':'C1 教学楼A楼顶'),host:env[prefix+'_IP']||'',port:Number(env[prefix+'_RTSP_PORT']||env.CAMERA_RTSP_PORT||554),username:env[prefix+'_USERNAME']||env.CAMERA_USERNAME||'admin',password:env[prefix+'_PASSWORD']||env.CAMERA_PASSWORD||'',channel:Number(env[prefix+'_CHANNEL']||env.CAMERA_CHANNEL||1),stream:env.CAMERA_STREAM||'main'};});
 const devices=()=>store.get('devices',deviceDefaults());
 const softwareRoot=env.YUNTONG_ROOT||path.resolve(import.meta.dirname,'../../../desktop-app');
 const defaultModel=path.resolve(softwareRoot,env.YUNTONG_DRONE_MODEL||'models/drone.onnx');
 const modelDefaults={threshold:0.15,detectionFps:12.5,holdSeconds:0.45,modelPath:defaultModel};
 const model=()=>store.get('model',modelDefaults);
 const settings=()=>({siteName:readSiteName(store)});
 const modelDir=path.join(store.directory,'models');fs.mkdirSync(modelDir,{recursive:true});
 const spatialFile=path.join(store.directory,'spatial-config.json');
 const legacySpatial=path.join(project,'spatial-config.json');
 if(!fs.existsSync(spatialFile)&&fs.existsSync(legacySpatial))fs.copyFileSync(legacySpatial,spatialFile,fs.constants.COPYFILE_EXCL);
 const roots=()=>ids.map((id,i)=>path.resolve(project,env['CAMERA'+(i+1)+'_OUTPUT_DIR']||env['CAMERA'+(i+1)+'_RECORD_DIR']||env.CAMERA_RECORD_DIR||path.join(store.directory,'recordings',id)));
 function runtimeOptions() {
  const childEnv={...env};devices().forEach((device,i)=>{
   const prefix='CAMERA'+(i+1);Object.assign(childEnv,{[prefix+'_IP']:device.host,[prefix+'_LABEL']:device.label,[prefix+'_RTSP_PORT']:String(device.port),[prefix+'_USERNAME']:device.username,[prefix+'_PASSWORD']:device.password,[prefix+'_CHANNEL']:String(device.channel),[prefix+'_OUTPUT_DIR']:roots()[i],[prefix+'_RTSP_PATH']:'/Streaming/Channels/'+device.channel+(device.stream==='sub'?'02':'01')});
  });
  childEnv.CAMERA_MODEL_THRESHOLD=String(model().threshold);
  childEnv.CAMERA_HOLD_SECONDS=String(model().holdSeconds);
  childEnv.CAMERA_SPATIAL_CONFIG=path.join(store.directory,'spatial-config.json');
  return {env:childEnv,detectionFps:model().detectionFps,modelPath:model().modelPath};
 }
 async function apply() {
  try {
   if(typeof bridge.configure!=='function'||typeof bridge.restart!=='function')throw new Error('Runtime configuration is unavailable');
   bridge.configure(runtimeOptions());await bridge.restart();return {applied:true};
  }catch{return {applied:false,message:'配置已保存；摄像机服务尚未就绪，连接恢复后生效'};}
 }
 bridge.configure?.(runtimeOptions());
 const versions=()=>{
  const list=store.get('models',[]);
  if(fs.existsSync(defaultModel)&&!list.some(item=>item.path===defaultModel))list.unshift({id:'installed',name:path.basename(defaultModel),path:defaultModel,bytes:fs.statSync(defaultModel).size});
  return list.map(({path:filename,...item})=>({...item,active:filename===model().modelPath}));
 };
 const rawVersions=()=>{const list=store.get('models',[]);if(fs.existsSync(defaultModel)&&!list.some(x=>x.id==='installed'))list.unshift({id:'installed',name:path.basename(defaultModel),path:defaultModel,bytes:fs.statSync(defaultModel).size});return list;};
 const publicDevices=()=>devices().map(({password,...item})=>({...item,passwordConfigured:Boolean(password)}));
 const router=Router();
 router.get('/devices',permission('monitor'),(_req,res)=>res.json({devices:publicDevices(),config:{previewWidth:768,previewHeight:432,previewFps:30,detectionFps:model().detectionFps},camera1Ip:devices()[0].host,camera2Ip:devices()[1].host}));
 router.patch('/devices/:id',permission('devices'),asyncRoute(async(req,res)=>{
  if(!ids.includes(req.params.id))throw fault(404,'设备不存在');const list=devices();const i=ids.indexOf(req.params.id),old=list[i],body=req.body||{};
  const next={...old,label:requiredText(body.label??old.label,'设备名称'),host:requiredText(body.host??old.host,'主机地址',253),username:requiredText(body.username??old.username,'用户名',80),port:Number(body.port??old.port),channel:Number(body.channel??old.channel),stream:body.stream??old.stream};
  if(!/^[a-zA-Z0-9.:-]+$/.test(next.host)||!Number.isInteger(next.port)||next.port<1||next.port>65535||!Number.isInteger(next.channel)||next.channel<1||next.channel>64||!['main','sub'].includes(next.stream))throw fault(400,'设备连接参数无效');
  if(body.password!==undefined&&body.password!==''){if(typeof body.password!=='string'||body.password.length>256)throw fault(400,'密码格式无效');next.password=body.password;}
  list[i]=next;store.set('devices',list);store.audit(req.user.username,'更新设备配置',{id:next.id,label:next.label});res.json({devices:publicDevices(),...await apply()});
 }));
 router.post('/devices/:id/test',permission('devices'),asyncRoute(async(req,res)=>{
  const device=devices().find(d=>d.id===req.params.id);if(!device?.host)throw fault(400,'请先配置设备地址');
  const result=await new Promise(resolve=>{const begin=Date.now();const socket=net.createConnection({host:device.host,port:device.port});let settled=false;const finish=(connected)=>{if(settled)return;settled=true;socket.destroy();resolve({connected,latencyMs:connected?Date.now()-begin:null});};socket.setTimeout(3000,()=>finish(false));socket.once('connect',()=>finish(true));socket.once('error',()=>finish(false));});
  res.json(result);
 }));
 router.get('/models/config',permission('models'),asyncRoute(async(_req,res)=>res.json({config:model(),versions:versions(),runtime:await bridge.status()})));
 router.put('/models/config',permission('models'),asyncRoute(async(req,res)=>{
  const body=req.body?.config||req.body||{};const old=model();
  const config={...old,threshold:Number(body.threshold??old.threshold),detectionFps:Number(body.detectionFps??old.detectionFps),holdSeconds:Number(body.holdSeconds??old.holdSeconds)};
  if(!Number.isFinite(config.threshold)||config.threshold<0||config.threshold>1||!Number.isFinite(config.detectionFps)||config.detectionFps<0.5||config.detectionFps>30||!Number.isFinite(config.holdSeconds)||config.holdSeconds<0||config.holdSeconds>3)throw fault(400,'模型参数超出范围');
  store.set('model',config);store.audit(req.user.username,'更新模型参数',{threshold:config.threshold,detectionFps:config.detectionFps,holdSeconds:config.holdSeconds});res.json({config,versions:versions(),...await apply()});
 }));
 router.post('/models/upload',permission('models'),express.raw({type:'application/octet-stream',limit:'128mb'}),asyncRoute(async(req,res)=>{
  let name;try{name=decodeURIComponent(req.get('x-filename')||'');}catch{throw fault(400,'文件名无效');}
  if(!Buffer.isBuffer(req.body)||req.body.length<16||!name.toLowerCase().endsWith('.onnx')||path.basename(name)!==name)throw fault(400,'请上传 ONNX 模型文件');
  const id=randomUUID(),filename=path.join(modelDir,id+'.onnx');fs.writeFileSync(filename,req.body,{flag:'wx'});
  const item={id,name,path:filename,bytes:req.body.length,uploadedAt:Date.now()};store.set('models',[...store.get('models',[]),item]);store.audit(req.user.username,'上传模型',{id,name});res.status(201).json({versions:versions()});
 }));
 router.post('/models/:id/validate',permission('models'),asyncRoute(async(req,res)=>{
  const list=rawVersions(),item=list.find(v=>v.id===req.params.id);if(!item)throw fault(404,'模型不存在');
  const python=process.env.YUNTONG_MODEL_PYTHON||process.env.YUNTONG_CAMERA_PYTHON||'python';
  try{await run(python,['-c','import onnx,sys; m=onnx.load(sys.argv[1]); onnx.checker.check_model(m)',item.path],{timeout:60_000,windowsHide:true,maxBuffer:1024*1024});}catch{throw fault(422,'模型验证未通过；请检查 ONNX 文件及 Python onnx 环境');}
  item.validatedAt=Date.now();store.set('models',list);store.audit(req.user.username,'验证模型',{id:item.id});res.json({valid:true,versions:versions()});
 }));
 router.post('/models/:id/activate',permission('models'),asyncRoute(async(req,res)=>{
  const item=rawVersions().find(v=>v.id===req.params.id);if(!item)throw fault(404,'模型不存在');if(item.id!=='installed'&&!item.validatedAt)throw fault(409,'请先验证模型');
  store.set('model',{...model(),modelPath:item.path});store.audit(req.user.username,'切换模型',{id:item.id});res.json({config:model(),versions:versions(),...await apply()});
 }));
 router.get('/settings',permission('monitor'),(_req,res)=>res.json({settings:settings()}));
 router.put('/settings',permission('settings'),(req,res,next)=>{try{
  const body=req.body?.settings??req.body??{};
  if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).some(key=>key!=='siteName'))throw fault(400,'当前版本仅支持修改站点名称，其他设置尚不可用');
  const siteName=requiredText(body.siteName,'站点名称');
  store.set('general',{...store.get('general',{}),siteName});store.audit(req.user.username,'更新系统设置');res.json({settings:settings()});
 }catch(error){next(error);}});
 return {router,devices,model,settings,roots,apply,versions,project,runtimeOptions};
}
