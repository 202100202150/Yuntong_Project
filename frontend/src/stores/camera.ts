import { defineStore } from 'pinia';
import { cameraBridge } from '@/api/camera-bridge';
import type { CameraBridgeStatus, CameraId, SnapshotResult } from '@/types/camera-types';

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
  spatial: {
    state: 'uncalibrated',
    message: '等待定位服务；请在设备管理中完成相机标定。',
    units: 'm',
    cameras: {},
    targets: [],
    diagnostics: {},
  },
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
    detectionBusy: false,
    recordingBusy: false,
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
          spatial: raw.spatial || emptyStatus.spatial,
          inferenceProvider: raw.inferenceProvider || (raw as any).inference_provider || 'unreported',
          engineState: raw.engineState || (raw as any).engine_state || 'unreported',
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
        if (!this.detectionBusy && this.status.detecting && this.runState !== 'running') {
          this.runState = 'running';
        } else if (!this.detectionBusy && !this.status.detecting && this.runState === 'running') {
          this.runState = 'paused';
        }
      } catch (err: any) {
        this.runState = 'idle';
        this.status = { ...this.status, detecting: false, recording: false, serviceState: 'error', error: err.message,
          cameras: { camera1: { ...emptyStatus.cameras.camera1, state: 'error' }, camera2: { ...emptyStatus.cameras.camera2, state: 'error' } },
          spatial: { ...this.status.spatial, state: 'error', message: '无法获取定位状态：' + err.message, targets: [] } };
      }
    },

    async setDetection(enabled: boolean): Promise<void> {
      if (this.detectionBusy) return;
      this.detectionBusy = true;
      this.runState = 'starting';
      try {
        const confirmed = enabled ? await cameraBridge.startDetection() : await cameraBridge.stopDetection();
        this.status = confirmed;
        if (confirmed.detecting !== enabled) throw new Error('服务未确认监测状态改变，请检查设备与推理服务后重试。');
        if (!enabled) this.toast('监测已暂停。', 'info');
        else if (confirmed.engineState === 'ready') this.toast('监测已启动。', 'success');
        else if (confirmed.serviceState === 'error') this.toast(confirmed.error || '监测已启用，但推理服务异常。', 'error');
        else this.toast('监测已启用，推理服务正在准备。', 'info');
      } catch (err: any) {
        this.toast(err.message, 'error');
        await this.refreshStatus();
      } finally {
        this.detectionBusy = false;
        this.runState = this.status.detecting ? 'running' : this.status.serviceState === 'error' ? 'idle' : 'paused';
      }
    },

    async toggleDetection(): Promise<void> {
      await this.setDetection(!this.status.detecting);
    },

    async stopDetection(): Promise<void> {
      await this.setDetection(false);
    },

    async toggleRecording() {
      if (this.recordingBusy) return;
      this.recordingBusy = true;
      try {
        const result = await cameraBridge.toggleRecording();
        this.status = { ...this.status, recording: result.recording, cameras: {
          camera1: { ...this.status.cameras.camera1, recordingState: result.cameras.camera1.state },
          camera2: { ...this.status.cameras.camera2, recordingState: result.cameras.camera2.state },
        } };
        const states = Object.values(result.cameras);
        if (states.some(camera => camera.state === 'error')) this.toast(states.find(camera => camera.state === 'error')?.error || '部分录像失败，请检查设备及录像目录。', 'error');
        else if (states.some(camera => camera.state === 'stopping')) this.toast('正在结束录像并写入文件。', 'info');
        else if (states.some(camera => ['armed','starting'].includes(camera.state))) this.toast('已请求录制，正在等待画面和录像文件就绪。', 'info');
        else if (states.filter(camera => camera.state === 'recording').length === 2) this.toast('两路录像已开始写入。', 'success');
        else if (result.recording) this.toast('当前仅一路正在录像，请检查另一设备状态。', 'info');
        else if (result.requestedRecording) this.toast('录像未开始，请检查设备状态后重试。', 'error');
        else this.toast('录像已停止。', 'info');
      } catch (err: any) {
        this.toast(err.message, 'error');
      } finally {
        this.recordingBusy = false;
      }
    },

    async captureBoth(): Promise<SnapshotResult[]> {
      try {
        const result = await cameraBridge.saveSnapshots();
        const saved = result.results.filter((r) => r.saved);
        if (saved.length === 2) this.toast('两路抓拍已保存。', 'success');
        else if (saved.length === 1) this.toast(`仅 ${saved[0].cameraId} 保存成功。`, 'error');
        else this.toast(result.results.find(r => r.error)?.error || (result.results.some(r => r.queued) ? '抓拍仍在处理，尚未确认文件保存。' : '两路抓拍均失败。'), 'error');
        return result.results;
      } catch (err: any) {
        this.toast(err.message, 'error');
        return [];
      }
    },
  },
});
