import { http } from './http';
import type {
  CameraBridgeStatus,
  CameraFrame,
  CameraId,
  CameraPreviewPair,
  SnapshotResult,
  RecordingToggleResult,
  SpatialConfig,
} from '@/types/camera-types';

const previewBase = (import.meta.env.VITE_API_BASE as string) || '/api';

export const cameraBridge = {
  async getSpatialConfig(): Promise<{ config: SpatialConfig }> {
    const { data } = await http.get('/spatial/config');
    return data;
  },

  async deriveSpatialConfig(config: SpatialConfig): Promise<{ config: SpatialConfig }> {
    const { data } = await http.post('/spatial/config/derive', { config });
    return data;
  },

  async saveSpatialConfig(config: SpatialConfig): Promise<{ config: SpatialConfig }> {
    const { data } = await http.post('/spatial/config', { config });
    return data;
  },

  async getStatus(): Promise<CameraBridgeStatus> {
    const { data } = await http.get('/status');
    return data;
  },

  async getFrame(cameraId: CameraId, afterSequence: number, signal?: AbortSignal): Promise<CameraFrame | null> {
    const res = await fetch(
      `${previewBase}/cameras/${cameraId}/frame.jpg?afterSequence=${afterSequence}`,
      { cache: 'no-store', signal },
    );
    if (res.status === 204) return null;
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = await res.arrayBuffer();
    return {
      cameraId,
      sequence: Number(res.headers.get('X-Frame-Sequence') ?? 0),
      width: Number(res.headers.get('X-Frame-Width') ?? 0),
      height: Number(res.headers.get('X-Frame-Height') ?? 0),
      receivedAt: Number(res.headers.get('X-Frame-Received-At') ?? Date.now()),
      jpegBytes: new Uint8Array(buf),
    };
  },

  async getPreviewPair(
    after: Record<CameraId, number>,
    epoch: string,
    signal: AbortSignal,
  ): Promise<CameraPreviewPair | null> {
    const query = new URLSearchParams({
      afterCamera1Sequence: String(after.camera1),
      afterCamera2Sequence: String(after.camera2),
      waitMs: '350',
    });
    if (epoch) query.set('epoch', epoch);
    const res = await fetch(`${previewBase}/cameras/preview-pair?${query}`, { cache: 'no-store', signal });
    if (res.status === 204) return null;
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const camera1Sequence = Number(res.headers.get('X-Camera1-Sequence'));
    const camera2Sequence = Number(res.headers.get('X-Camera2-Sequence'));
    const split = Number(res.headers.get('X-Camera1-Length'));
    const responseEpoch = res.headers.get('X-Preview-Epoch') ?? '';
    const payload = await res.blob();
    if (!Number.isSafeInteger(split) || split <= 0 || split >= payload.size
      || !Number.isSafeInteger(camera1Sequence) || camera1Sequence <= 0
      || !Number.isSafeInteger(camera2Sequence) || camera2Sequence <= 0
      || !/^[0-9a-f]{32}$/.test(responseEpoch)) {
      throw new Error('双路预览帧格式无效。');
    }
    return {
      epoch: responseEpoch,
      camera1: { sequence: camera1Sequence, jpeg: payload.slice(0, split, 'image/jpeg') },
      camera2: { sequence: camera2Sequence, jpeg: payload.slice(split, payload.size, 'image/jpeg') },
    };
  },

  async startDetection(): Promise<CameraBridgeStatus> {
    const { data } = await http.post('/detection/start');
    return data;
  },

  async stopDetection(): Promise<CameraBridgeStatus> {
    const { data } = await http.post('/detection/stop');
    return data;
  },

  async toggleRecording(): Promise<RecordingToggleResult> {
    const { data } = await http.post('/recording/toggle');
    return data;
  },

  async saveSnapshots(): Promise<{ results: SnapshotResult[] }> {
    const { data } = await http.post('/snapshots');
    return data;
  },
};
