import express from 'express';
import cors from 'cors';
import http from 'node:http';
import 'dotenv/config';
import { statusRouter } from './routes/status.mjs';
import { frameRouter } from './routes/frame.mjs';
import { detectionRouter } from './routes/detection.mjs';
import { recordingRouter } from './routes/recording.mjs';
import { snapshotsRouter } from './routes/snapshots.mjs';
import { devicesRouter } from './routes/devices.mjs'; // 👈 引入设备路由
import { attachFrameStream } from './ws/frame-stream.mjs';
import { startCameraBridge, stopCameraBridge } from './sidecar/client.mjs';

const app = express();
app.use(cors());
app.use(express.json());

// 注册所有 API 路由
app.use('/api', statusRouter);
app.use('/api', frameRouter);
app.use('/api', detectionRouter);
app.use('/api', recordingRouter);
app.use('/api', snapshotsRouter);
app.use('/api', devicesRouter); // 👈 注册设备路由

const server = http.createServer(app);
attachFrameStream(server);

const PORT = Number(process.env.PORT ?? 8000);

startCameraBridge().catch((err) => {
  console.error('[backend] sidecar 启动失败:', err.message);
});

server.listen(PORT, () => {
  console.log(`[backend] http://localhost:${PORT}`);
});

const shutdown = async () => {
  await stopCameraBridge();
  server.close(() => process.exit(0));
};
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);