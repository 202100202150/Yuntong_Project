import { Router } from 'express';
import { requestCameraBridge, startCameraBridge } from '../sidecar/client.mjs';
import { normalizeStatus, emptyCameraStatus } from '../normalize/status.mjs';

export const statusRouter = Router();

statusRouter.get('/status', async (req, res) => {
  try {
    await startCameraBridge().catch(() => {});
    const r = await requestCameraBridge('/v1/status');
    res.json(normalizeStatus(r.data));
  } catch (err) {
    res.status(200).json({
      serviceState: 'error',
      error: err.message,
      recording: false,
      detecting: false,
      inferenceProvider: 'unreported',
      precision: 'unreported',
      previewProcessor: 'unreported',
      engineState: 'unreported',
      matchingDiagnostics: {
        algorithmVersion: 'unreported',
        reason: 'waiting_for_frames',
        bestSimilarity: null,
        secondSimilarity: null,
        camera1Id: null,
        camera2Id: null,
        comparisonCount: 0,
        frameSkewMs: null,
        offsetMs: null,
        pendingPairs: 0,
        confirmedPairs: 0,
        geometryStatus: 'unavailable',
        geometryInliers: 0,
        reasonCounts: {},
        recentFailures: [],
      },
      cameras: {
        camera1: emptyCameraStatus('camera1', 'error'),
        camera2: emptyCameraStatus('camera2', 'error'),
      },
    });
  }
});