import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';

const inside = (root,filename) => {const relative=path.relative(root,filename);return relative&&!relative.startsWith('..')&&!path.isAbsolute(relative);};
const completeImage = filename => {
  let fd;
  try {
    const stat=fs.statSync(filename);if(!stat.isFile()||stat.size<16)return false;
    fd=fs.openSync(filename,'r');const first=Buffer.alloc(8),last=Buffer.alloc(12);
    fs.readSync(fd,first,0,8,0);fs.readSync(fd,last,0,12,stat.size-12);
    if(/\.png$/i.test(filename))return first.equals(Buffer.from('89504e470d0a1a0a','hex'))&&last.equals(Buffer.from('0000000049454e44ae426082','hex'));
    return /\.jpe?g$/i.test(filename)&&first[0]===0xff&&first[1]===0xd8&&last[10]===0xff&&last[11]===0xd9;
  }catch{return false;}finally{if(fd!==undefined)fs.closeSync(fd);}
};

export async function confirmSnapshot(filename,roots,timeoutMs=5000) {
  if(typeof filename!=='string'||!filename||!roots.some(root=>inside(path.resolve(root),path.resolve(filename))))return null;
  const end=Date.now()+timeoutMs;
  do {
    try {
      const resolved=fs.realpathSync(filename);
      if(!roots.some(root=>{try{return inside(fs.realpathSync(root),resolved);}catch{return false;}}))return null;
      if(completeImage(resolved))return resolved;
    }catch{}
    if(Date.now()>=end)break;
    await new Promise(resolve=>setTimeout(resolve,50));
  }while(true);
  return null;
}

export function createSnapshotsRouter({request=requestCameraBridge,roots=()=>[],indexSnapshot,timeoutMs=5000,audit=()=>{}}={}) {
  const router=Router();
  router.post('/snapshots',async(req,res)=>{
    try {
      const r=await request('/v1/snapshots',{method:'POST'});
      const raw=Array.isArray(r.data?.results)?r.data.results:[];
      const results=await Promise.all(['camera1','camera2'].map(async cameraId=>{
        const item=raw.find(x=>x?.camera_id===cameraId||x?.cameraId===cameraId)??r.data?.snapshots?.[cameraId]??r.data?.[cameraId]??{};
        const queued=Boolean(item.queued||item.saved||item.path);
        if(!queued)return{cameraId,saved:false,queued:false,error:typeof item.error==='string'?item.error.slice(0,240):'未获得可保存的画面'};
        const filename=await confirmSnapshot(item.path,roots(),timeoutMs);
        if(!filename)return{cameraId,saved:false,queued:true,error:'抓拍请求已提交，但尚未确认完整文件写入'};
        try {
          if(typeof indexSnapshot!=='function')throw new Error('Media index unavailable');
          const media=indexSnapshot(filename,cameraId,Boolean(item.detectionOverlay));
          audit(req.user?.username,'保存抓拍',{cameraId,mediaId:media.mediaId});
          return{cameraId,saved:true,queued:false,...media};
        }catch{return{cameraId,saved:false,queued:false,error:'抓拍文件已写入，但媒体索引登记失败，请检查存储服务'};}
      }));
      const status=results.some(item=>item.saved)?200:results.some(item=>item.queued)?202:503;
      const error=status===503?results.map(item=>(item.cameraId==='camera1'?'C1':'C2')+'：'+item.error).join('；'):undefined;
      res.status(status).json({results,...(error?{error}:{})});
    }catch(error){res.status(503).json({error:error.message});}
  });
  return router;
}
export const snapshotsRouter=createSnapshotsRouter();
