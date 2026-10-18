import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';

export const snapshotsRouter = Router();

snapshotsRouter.post('/snapshots', async (req, res) => {
  try {
    const r = await requestCameraBridge('/v1/snapshots', { method: 'POST' });
    const raw = Array.isArray(r.data?.results) ? r.data.results : [];
    res.json({
      results: ['camera1', 'camera2'].map((cameraId) => {
        const item = raw.find((x) => x?.camera_id === cameraId || x?.cameraId === cameraId)
          ?? r.data?.snapshots?.[cameraId]
          ?? r.data?.[cameraId]
          ?? {};
        return {
          cameraId,
          saved: Boolean(item.saved ?? item.queued ?? item.path),
          ...(typeof item.error === 'string' ? { error: item.error.slice(0, 240) } : {}),
        };
      }),
    });
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});