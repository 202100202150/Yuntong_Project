import { WebSocketServer } from 'ws';
import { requestCameraBridge } from '../sidecar/client.mjs';

export function attachFrameStream(server) {
  const wss = new WebSocketServer({ server, path: '/ws/frames' });
  wss.on('connection', (ws, req) => {
    const url = new URL(req.url, 'http://localhost');
    const cameraId = url.searchParams.get('cameraId');
    if (!['camera1', 'camera2'].includes(cameraId)) {
      ws.close(1008, 'invalid cameraId');
      return;
    }
    let after = 0;
    let stopped = false;
    (async () => {
      while (!stopped && ws.readyState === ws.OPEN) {
        try {
          const r = await requestCameraBridge(`/v1/cameras/${cameraId}/frame.jpg`, {
            responseType: 'buffer',
            timeoutMs: 2_000,
            requestHeaders: {
              'X-After-Frame-Sequence': String(after),
              'X-Wait-Milliseconds': '750',
            },
          });
          if (r.statusCode === 204 || !r.data) continue;
          after = Number(r.headers['x-frame-sequence'] ?? after + 1);
          ws.send(JSON.stringify({
            type: 'frame',
            sequence: after,
            width: Number(r.headers['x-frame-width'] ?? 0),
            height: Number(r.headers['x-frame-height'] ?? 0),
            receivedAt: Number(r.headers['x-frame-received-at'] ?? Date.now()),
          }));
          ws.send(r.data);
        } catch (err) {
          try { ws.send(JSON.stringify({ type: 'error', message: err.message })); } catch {}
          await new Promise((r) => setTimeout(r, 500));
        }
      }
    })();
    ws.on('close', () => { stopped = true; });
  });
}