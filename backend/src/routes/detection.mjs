import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';
import { normalizeStatus } from '../normalize/status.mjs';

export const detectionRouter = Router();

detectionRouter.post('/detection/start', async (req, res) => {
  try {
    await requestCameraBridge('/v1/detection/start', { method: 'POST' });
    const r = await requestCameraBridge('/v1/status');
    res.json(normalizeStatus(r.data));
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});

detectionRouter.post('/detection/stop', async (req, res) => {
  try {
    await requestCameraBridge('/v1/detection/stop', { method: 'POST' });
    const r = await requestCameraBridge('/v1/status');
    res.json(normalizeStatus(r.data));
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});