import type { BoundingBox } from './annotation-types';

export const cameraIds = ['camera1', 'camera2'] as const;
export type CameraId = (typeof cameraIds)[number];

export type CameraConnectionState =
  | 'starting'
  | 'connecting'
  | 'streaming'
  | 'reconnecting'
  | 'stopped'
  | 'error';

export type CameraRuntimeStatus = {
  id: CameraId;
  label: string;
  state: CameraConnectionState;
  error: string;
  width: number;
  height: number;
  decodeFps: number;
  framesDecoded: number;
  reconnects: number;
  sequence: number;
  frameAvailable: boolean;
  recordingState: string;
  detectionCount: number;
  detectionAgeMs: number | null;
  previewFps: number;
  detectionFps: number;
  latency: number | null;
  objects: CameraDetectedObject[];
};

export type CameraDetectedObject = {
  id: string;
  /** Anonymous cross-camera association; absent IDs remain camera-scoped. */
  globalId?: string;
  class: 'person';
  confidence: number;
  bbox: BoundingBox;
  frameSequence: number;
};

export type CameraMatchingReason =
  | 'waiting_for_frames'
  | 'capture_time_gap'
  | 'no_people'
  | 'low_similarity'
  | 'ambiguous'
  | 'confirming'
  | 'partial_match'
  | 'matched';

export type CameraMatchingFailure = {
  reason: CameraMatchingReason;
  bestSimilarity: number | null;
  secondSimilarity: number | null;
  camera1Id: string | null;
  camera2Id: string | null;
  frameSkewMs: number | null;
  ageMs: number;
};

export type CameraMatchingDiagnostics = {
  algorithmVersion: string;
  reason: CameraMatchingReason;
  bestSimilarity: number | null;
  secondSimilarity: number | null;
  camera1Id: string | null;
  camera2Id: string | null;
  comparisonCount: number;
  frameSkewMs: number | null;
  offsetMs: number | null;
  pendingPairs: number;
  confirmedPairs: number;
  geometryStatus: 'ready' | 'unavailable';
  geometryInliers: number;
  reasonCounts: Partial<Record<CameraMatchingReason, number>>;
  recentFailures: CameraMatchingFailure[];
};

export type CameraBridgeStatus = {
  serviceState: 'starting' | 'ready' | 'error' | 'stopped';
  error: string;
  recording: boolean;
  detecting: boolean;
  inferenceProvider: string;
  precision: string;
  previewProcessor: string;
  engineState: string;
  matchingDiagnostics: CameraMatchingDiagnostics;
  cameras: Record<CameraId, CameraRuntimeStatus>;
};

export type CameraFrame = {
  cameraId: CameraId;
  sequence: number;
  width: number;
  height: number;
  receivedAt: number;
  jpegBytes: Uint8Array;
};

export type CameraSnapshotObject = {
  class: 'person';
  confidence: number;
  bbox: BoundingBox;
};

export type CameraSnapshotResult = {
  cameraId: CameraId;
  saved: boolean;
  error?: string;
};

export type DualCameraSnapshotResult = {
  results: CameraSnapshotResult[];
};

export type RecordingToggleResult = {
  recording: boolean;
  cameras: Record<CameraId, { state: string; error?: string }>;
};

export type YuntongCameraBridge = {
  getStatus(): Promise<CameraBridgeStatus>;
  getFrame(cameraId: CameraId, afterSequence: number): Promise<CameraFrame | null>;
  startDetection(): Promise<CameraBridgeStatus>;
  stopDetection(): Promise<CameraBridgeStatus>;
  toggleRecording(): Promise<RecordingToggleResult>;
  saveSnapshots(): Promise<DualCameraSnapshotResult>;
};

export function isCameraId(value: unknown): value is CameraId {
  return value === 'camera1' || value === 'camera2';
}
