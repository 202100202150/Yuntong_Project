export type CameraId = 'camera1' | 'camera2';

export interface CameraBBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CameraDetectedObject {
  id: string;
  globalId?: string;
  class: 'person';
  confidence: number;
  bbox: CameraBBox;
  frameSequence: number;
}

export interface CameraRuntimeStatus {
  id: CameraId;
  label: string;
  state: 'starting' | 'connecting' | 'streaming' | 'reconnecting' | 'stopped' | 'error';
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
}

export type CameraMatchingReason =
  | 'waiting_for_frames'
  | 'capture_time_gap'
  | 'no_people'
  | 'low_similarity'
  | 'ambiguous'
  | 'confirming'
  | 'partial_match'
  | 'matched';

export interface CameraMatchingDiagnostics {
  algorithmVersion: 'temporal-window-v2' | 'temporal-geometry-v3' | 'unreported';
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
  recentFailures: {
    reason: CameraMatchingReason;
    bestSimilarity: number | null;
    secondSimilarity: number | null;
    camera1Id: string | null;
    camera2Id: string | null;
    frameSkewMs: number | null;
    ageMs: number;
  }[];
}

export interface CameraBridgeStatus {
  serviceState: 'starting' | 'ready' | 'error';
  error: string;
  recording: boolean;
  detecting: boolean;
  inferenceProvider: string;
  precision: string;
  previewProcessor: string;
  engineState: string;
  matchingDiagnostics: CameraMatchingDiagnostics;
  cameras: Record<CameraId, CameraRuntimeStatus>;
}

export interface CameraFrame {
  cameraId: CameraId;
  sequence: number;
  width: number;
  height: number;
  receivedAt: number;
  jpegBytes: Uint8Array;
}

export interface SnapshotResult {
  cameraId: CameraId;
  saved: boolean;
  error?: string;
}

export interface RecordingToggleResult {
  recording: boolean;
  cameras: {
    camera1: { state: string };
    camera2: { state: string };
  };
}

export interface MediaItem {
  id: string;
  name: string;
  kind: 'image' | 'video';
  objectUrl: string;
  status: 'ready' | 'detecting' | 'detected' | 'error';
  detected: boolean;
  objects: CameraDetectedObject[];
}