import { defineStore } from 'pinia';
import { cameraBridge } from '@/api/camera-bridge';
import type { CameraBridgeStatus, CameraId } from '@/types/camera-types';

type RunState = 'idle' | 'starting' | 'running' | 'paused';
type Toast = { id: number; message: string; tone: 'info' | 'success' | 'error' };

const emptyStatus: CameraBridgeStatus = {
  serviceState: 'starting',
  error: '',
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
    camera1: {
      id: 'camera1', label: 'camera1', state: 'starting', error: '',
      width: 0, height: 0, decodeFps: 0, framesDecoded: 0, reconnects: 0,
      sequence: 0, frameAvailable: false, recordingState: 'idle',
      detectionCount: 0, detectionAgeMs: null,
      previewFps: 0, detectionFps: 0, latency: null, objects: [],
    },
    camera2: {
      id: 'camera2', label: 'camera2', state: 'starting', error: '',
      width: 0, height: 0, decodeFps: 0, framesDecoded: 0, reconnects: 0,
      sequence: 0, frameAvailable: false, recordingState: 'idle',
      detectionCount: 0, detectionAgeMs: null,
      previewFps: 0, detectionFps: 0, latency: null, objects: [],
    },
  },
};

export const useCameraStore = defineStore('camera', {
  state: () => ({
    status: emptyStatus as CameraBridgeStatus,
    runState: 'idle' as RunState,
    displayFps: { camera1: 0, camera2: 0 } as Record<CameraId, number>,
    toasts: [] as Toast[],
  }),

  actions: {
    toast(message: string, tone: Toast['tone'] = 'info') {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, message, tone });
      setTimeout(() => {
        this.toasts = this.toasts.filter((t) => t.id !== id);
      }, 4600);
    },

    async refreshStatus() {
      try {
        const raw = await cameraBridge.getStatus();

        // 强行映射兼容各种后端字段名
        const mappedStatus = {
          ...raw,
          inferenceProvider: raw.inferenceProvider || (raw as any).inference_provider || 'tensorrt',
          engineState: raw.engineState || (raw as any).engine_state || 'ready',
          cameras: {
            camera1: {
              ...raw.cameras.camera1,
              decodeFps: raw.cameras.camera1.decodeFps || (raw.cameras.camera1 as any).fps || (raw.cameras.camera1 as any).decode_fps || 0,
              previewFps: raw.cameras.camera1.previewFps || (raw.cameras.camera1 as any).preview_fps || 0,
            },
            camera2: {
              ...raw.cameras.camera2,
              decodeFps: raw.cameras.camera2.decodeFps || (raw.cameras.camera2 as any).fps || (raw.cameras.camera2 as any).decode_fps || 0,
              previewFps: raw.cameras.camera2.previewFps || (raw.cameras.camera2 as any).preview_fps || 0,
            }
          }
        };

        this.status = mappedStatus as unknown as CameraBridgeStatus;

        // 同步 runState 状态
        if (this.status.detecting && this.runState !== 'running') {
          this.runState = 'running';
        } else if (!this.status.detecting && this.runState === 'running') {
          this.runState = 'paused';
        }
      } catch (err: any) {
        this.status = { ...this.status, serviceState: 'error', error: err.message };
      }
    },

    async toggleDetection() {
      if (this.runState === 'running') {
        try {
          this.runState = 'starting';
          await cameraBridge.stopDetection();
          this.status.detecting = false;
          this.runState = 'paused';
          this.toast('两路人物检测已暂停。', 'info');
        } catch (err: any) {
          this.runState = 'running';
          this.toast(err.message, 'error');
        }
        return;
      }
      try {
        this.runState = 'starting';
        await cameraBridge.startDetection();
        this.status.detecting = true;
        this.runState = 'running';
        this.toast('两路摄像机人物检测与跟踪已启动。', 'success');
      } catch (err: any) {
        this.runState = 'idle';
        this.toast(err.message, 'error');
      }
    },

    async stopDetection() {
      try {
        await cameraBridge.stopDetection();
        this.status.detecting = false;
        this.runState = 'idle';
        this.toast('人物检测已停止并清除跟踪框。', 'info');
      } catch (err: any) {
        this.toast(err.message, 'error');
      }
    },

    async toggleRecording() {
      try {
        const result = await cameraBridge.toggleRecording();
        this.status = { ...this.status, recording: result.recording };
        this.toast(result.recording ? '已开始带框录像。' : '已停止录像。', result.recording ? 'success' : 'info');
      } catch (err: any) {
        this.toast(err.message, 'error');
      }
    },

    async captureBoth() {
      try {
        const result = await cameraBridge.saveSnapshots();
        const saved = result.results.filter((r) => r.saved);
        if (saved.length === 2) this.toast('两路抓拍已保存。', 'success');
        else if (saved.length === 1) this.toast(`仅 ${saved[0].cameraId} 保存成功。`, 'error');
        else this.toast('两路抓拍均失败。', 'error');
      } catch (err: any) {
        this.toast(err.message, 'error');
      }
    },
  },
});