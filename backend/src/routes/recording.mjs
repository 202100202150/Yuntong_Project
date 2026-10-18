import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';
import { normalizeStatus } from '../normalize/status.mjs';

export const recordingRouter = Router();

recordingRouter.post('/recording/toggle', async (req, res) => {
  try {
    const r = await requestCameraBridge('/v1/recording/toggle', { method: 'POST' });
    const status = normalizeStatus((await requestCameraBridge('/v1/status')).data);
    res.json({
      recording: Boolean(r.data?.recording ?? status.recording),
      cameras: {
        camera1: { state: status.cameras.camera1.recordingState },
        camera2: { state: status.cameras.camera2.recordingState },
      },
    });
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});