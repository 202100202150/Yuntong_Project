import { Router } from 'express';
import { requestCameraBridge } from '../sidecar/client.mjs';

const cameraIds = new Set(['camera1', 'camera2']);
export const frameRouter = Router();

frameRouter.get('/cameras/:cameraId/frame.jpg', async (req, res) => {
  const { cameraId } = req.params;
  const after = Number(req.query.afterSequence ?? 0);
  if (!cameraIds.has(cameraId) || !Number.isSafeInteger(after) || after < 0)
    return res.status(400).json({ error: '摄像机帧请求参数无效。' });

  try {
    const r = await requestCameraBridge(`/v1/cameras/${cameraId}/frame.jpg`, {
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