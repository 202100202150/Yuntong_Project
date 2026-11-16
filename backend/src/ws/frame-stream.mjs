import { WebSocketServer } from 'ws';
import { requestCameraBridge } from '../sidecar/client.mjs';

export function attachFrameStream(server, authorize = () => false) {
  const wss = new WebSocketServer({ noServer: true });
  server.on('upgrade', (req, socket, head) => {
    let validOrigin = true;
    try { if (req.headers.origin) validOrigin = new URL(req.headers.origin).host === req.headers.host; } catch { validOrigin = false; }
    if (!req.url?.startsWith('/ws/frames?') || !validOrigin || !authorize(req)) {
      socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n'); socket.destroy(); return;
    }
    wss.handleUpgrade(req, socket, head, ws => wss.emit('connection', ws, req));
  });
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
        if (!authorize(req)) { ws.close(1008, 'session expired'); break; }
        if (ws.bufferedAmount > 4 * 1024 * 1024) {
          await new Promise(resolve => setTimeout(resolve, 100)); continue;
        }
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
  return wss;
}
