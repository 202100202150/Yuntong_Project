import { Router } from 'express';

export const devicesRouter = Router();

devicesRouter.get('/devices', (_req, res) => {
  res.json({
    // 👇 只返回纯 IP 地址
    camera1Ip: '10.108.16.9',
    camera2Ip: '10.108.16.8',
    previewWidth: '768',
    previewHeight: '432',
    previewFps: '30',
    detectionFps: '2'
  });
});
