import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { Router } from 'express';
import { asyncRoute, fault } from './database.mjs';
import { permission } from './auth.mjs';
export function defaultSpatialConfig() {
 const camera={offset:{x:0,y:0,z:0},yawDeg:0,pitchDeg:0,rollDeg:0,intrinsics:{imageWidth:3840,imageHeight:2160,fx:null,fy:null,cx:1920,cy:1080,distortion:[0,0,0,0,0],source:'unconfigured'}};
 return {version:1,units:'m',coordinateFrame:{mode:'stereo_midpoint',xAxis:'left',yAxis:'up',zAxis:'forward',description:'World axes used by the spatial scene.'},rig:{midpoint:{x:0,y:0,z:0},baselineMeters:0.61,baselineYawDeg:0,geometrySource:'manual',cameraPositions:null},cameras:{camera1:structuredClone(camera),camera2:structuredClone(camera)},extrinsics:{enabled:false,commonFrame:'AprilGrid',camera1:{rotationCameraToBoard:null,translationCameraInBoard:null,source:''},camera2:{rotationCameraToBoard:null,translationCameraInBoard:null,source:''}},stereo:{processingScale:0.32,minDisparity:0,numDisparities:128,blockSize:5,minValidDepthPoints:3,minDepthMeters:0.2,maxDepthMeters:100},quality:{maxFrameSkewMs:40,maxReprojectionErrorPx:50,minFeatureMatches:3,maxRangeMeters:100,minTriangulationAngleDeg:0.01}};
}
export function validateSpatialOffline(project,config,derive=false) {
 const code="import sys,json\nfrom spatial_localization import validate_config,derive_extrinsic_config\ntry:\n value=json.load(sys.stdin)\n result="+(derive?'derive_extrinsic_config':'validate_config')+"(value)\n print(json.dumps({'config':result},allow_nan=False))\nexcept (ValueError,TypeError,KeyError) as error:\n print(json.dumps({'error':str(error)}))\n sys.exit(2)";
 const python=process.env.YUNTONG_CALIBRATION_PYTHON||process.env.YUNTONG_CAMERA_PYTHON||'python';
 return new Promise((resolve,reject)=>{
  const child=spawn(python,['-c',code],{cwd:project,windowsHide:true,shell:false,stdio:['pipe','pipe','pipe']});
  let output='',errors='',settled=false;
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value);};
  const timer=setTimeout(()=>{child.kill();finish(fault(503,'标定配置校验超时'));},20000);
  child.stdout.on('data',chunk=>{output+=chunk.toString();if(output.length>2*1024*1024){child.kill();finish(fault(503,'配置响应过大'));}});
  child.stderr.on('data',chunk=>{errors=(errors+chunk.toString()).slice(-4000);});
  child.on('error',()=>finish(fault(503,'未找到标定 Python，请配置 YUNTONG_CALIBRATION_PYTHON')));
  child.on('close',code=>{
   let result;try{result=JSON.parse(output);}catch{}
   if(result?.error)return finish(fault(400,result.error.slice(0,240)));
   if(code!==0||!result?.config) {const missing=errors.match(/No module named ['"]([A-Za-z0-9_.-]+)['"]/);return finish(fault(503,missing?'标定环境缺少 '+missing[1]+' 模块':'标定配置校验未完成，请检查 Python 环境'));}
   finish(null,result.config);
  });
  child.stdin.on('error',()=>{});
  child.stdin.end(JSON.stringify(config));
 });
}
export function createLocalSpatialRouter(store,configuration,bridge) {
 const router=Router(),filename=path.join(store.directory,'spatial-config.json');
 const load=()=>fs.existsSync(filename)?JSON.parse(fs.readFileSync(filename,'utf8')):defaultSpatialConfig();
 router.get('/spatial/config',permission('monitor'),asyncRoute(async(_req,res)=>res.json({config:load()})));
 router.post('/spatial/config/derive',permission('devices'),asyncRoute(async(req,res)=>{
  const config=await validateSpatialOffline(configuration.project,req.body?.config??req.body,true);
  res.json({config});
 }));
 router.post('/spatial/config',permission('devices'),asyncRoute(async(req,res)=>{
  const config=await validateSpatialOffline(configuration.project,req.body?.config??req.body);
  const temporary=filename+'.tmp';fs.writeFileSync(temporary,JSON.stringify(config,null,2));fs.renameSync(temporary,filename);
  let applied=false;
  try {await bridge.request('/v1/spatial/config',{method:'POST',body:{config},ensureStarted:false});applied=true;}catch{}
  store.audit(req.user.username,'保存空间标定配置',{applied});
  res.json({config,applied,...(!applied?{message:'配置已保存，摄像机服务启动后生效'}:{})});
 }));
 return {router,load};
}
