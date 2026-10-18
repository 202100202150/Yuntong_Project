import { http } from './http';
import type {
  CameraBridgeStatus,
  CameraFrame,
  CameraId,
  SnapshotResult,
  RecordingToggleResult,
} from '@/types/camera-types';

export const cameraBridge = {
  async getStatus(): Promise<CameraBridgeStatus> {
    const { data } = await http.get('/status');
    return data;
  },

  async getFrame(cameraId: CameraId, afterSequence: number): Promise<CameraFrame | null> {
    const base = (import.meta.env.VITE_API_BASE as string) || '/api';
    const res = await fetch(
      `${base}/cameras/${cameraId}/frame.jpg?afterSequence=${afterSequence}`,
      { cache: 'no-store' },
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