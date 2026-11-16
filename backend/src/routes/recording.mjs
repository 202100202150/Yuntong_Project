import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';

const states=new Set(['idle','armed','starting','recording','stopping','error']);
export function createRecordingRouter(request=requestCameraBridge) {
  const router=Router();
  router.post('/recording/toggle',async(req,res)=>{
    try {
      const command=await request('/v1/recording/toggle',{method:'POST'});
      const {data}=await request('/v1/status');
      const cameras=Object.fromEntries(['camera1','camera2'].map(id=>{
        const raw=data?.cameras?.[id]?.recording;
        const state=states.has(raw?.state)?raw.state:'error';
        // The Python service redacts credentials before reporting recorder errors.
        return[id,{state,...(raw?.error?{error:String(raw.error).slice(0,240)}:{})}];
      }));
      res.json({recording:Object.values(cameras).some(camera=>camera.state==='recording'),requestedRecording:Boolean(command.data?.recording),cameras});
    }catch(error){res.status(503).json({error:error.message});}
  });
  return router;
}
export const recordingRouter=createRecordingRouter();
