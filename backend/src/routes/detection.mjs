import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';
import { normalizeStatus } from '../normalize/status.mjs';

export function createDetectionRouter(request = requestCameraBridge) {
const detectionRouter = Router();

detectionRouter.post('/detection/start', async (req, res) => {
  try {
    await request('/v1/detection/start', { method: 'POST' });
    const r = await request('/v1/status');
    res.json(normalizeStatus(r.data));
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});

detectionRouter.post('/detection/stop', async (req, res) => {
  try {
    await request('/v1/detection/stop', { method: 'POST' });
    const r = await request('/v1/status');
    res.json(normalizeStatus(r.data));
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});
return detectionRouter;
}
export const detectionRouter = createDetectionRouter();
