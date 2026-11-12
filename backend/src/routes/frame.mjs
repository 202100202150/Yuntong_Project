import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';

const cameraIds = new Set(['camera1', 'camera2']);
const pairHeaders = ['X-Camera1-Sequence', 'X-Camera2-Sequence', 'X-Camera1-Length', 'X-Frame-Skew-Ms', 'X-Preview-Epoch'];

export function createFrameRouter(request = requestCameraBridge) {
  const frameRouter = Router();

  frameRouter.get('/cameras/preview-pair', async (req, res) => {
    const after1 = Number(req.query.afterCamera1Sequence ?? 0);
    const after2 = Number(req.query.afterCamera2Sequence ?? 0);
    const waitMs = Number(req.query.waitMs ?? 750);
    const epoch = req.query.epoch ?? '';
    if (![after1, after2, waitMs].every((value) => Number.isSafeInteger(value) && value >= 0)
      || waitMs > 1_000 || typeof epoch !== 'string' || (epoch && !/^[a-f0-9]{32}$/.test(epoch))) {
      return res.status(400).json({ error: '双路预览请求参数无效。' });
    }

    res.set('Cache-Control', 'no-store');
    try {
      const pair = await request('/v1/cameras/preview-pair', {
        responseType: 'buffer',
        timeoutMs: 2_000,
        requestHeaders: {
          'X-After-Camera1-Sequence': String(after1),
          'X-After-Camera2-Sequence': String(after2),
          'X-Wait-Milliseconds': String(waitMs),
          ...(epoch ? { 'X-Preview-Epoch': epoch } : {}),
        },
      });
      if (pair.statusCode === 204 || !pair.data) return res.status(204).end();
      res.set('Content-Type', 'application/octet-stream');
      res.set('Access-Control-Expose-Headers', pairHeaders.join(', '));
      for (const header of pairHeaders) {
        const value = pair.headers[header.toLowerCase()];
        if (value !== undefined) res.set(header, value);
      }
      return res.send(pair.data);
    } catch (err) {
      return res.status(503).json({ error: err.message });
    }
  });

  frameRouter.get('/cameras/:cameraId/frame.jpg', async (req, res) => {
    const { cameraId } = req.params;
    const after = Number(req.query.afterSequence ?? 0);
    if (!cameraIds.has(cameraId) || !Number.isSafeInteger(after) || after < 0)
      return res.status(400).json({ error: '摄像机帧请求参数无效。' });

    try {
      const r = await request(`/v1/cameras/${cameraId}/frame.jpg`, {
        responseType: 'buffer',
        timeoutMs: 2_000,
        requestHeaders: {
          'X-After-Frame-Sequence': String(after),
          'X-Wait-Milliseconds': '750',
        },
      });
      if (r.statusCode === 204 || !r.data) return res.status(204).end();
      res.set({
        'Content-Type': 'image/jpeg',
        'X-Frame-Sequence': r.headers['x-frame-sequence'] ?? after + 1,
        'X-Frame-Width': r.headers['x-frame-width'] ?? '',
        'X-Frame-Height': r.headers['x-frame-height'] ?? '',
        'X-Frame-Received-At': r.headers['x-frame-received-at'] ?? Date.now(),
      });
      res.send(r.data);
    } catch (err) {
      res.status(503).json({ error: err.message });
    }
  });
  return frameRouter;
}

export const frameRouter = createFrameRouter();
