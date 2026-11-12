import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';

const bridgeErrorStatus = (error) => [400, 422].includes(error?.statusCode) ? 400 : 503;

export function createSpatialRouter(requestBridge = requestCameraBridge) {
  const router = Router();

  router.get('/spatial/config', async (_req, res) => {
    try {
      const result = await requestBridge('/v1/spatial/config');
      res.json(result.data);
    } catch (err) {
      res.status(bridgeErrorStatus(err)).json({ error: err.message });
    }
  });

  for (const path of ['/spatial/config', '/spatial/config/derive']) {
    router.post(path, async (req, res) => {
      try {
        const config = req.body?.config ?? req.body;
        if (!config || typeof config !== 'object' || Array.isArray(config)) {
          return res.status(400).json({ error: '请提交完整的定位配置对象。' });
        }
        const result = await requestBridge(`/v1${path}`, { method: 'POST', body: { config } });
        res.json(result.data);
      } catch (err) {
        res.status(bridgeErrorStatus(err)).json({ error: err.message });
      }
    });
  }

  return router;
}

export const spatialRouter = createSpatialRouter();
