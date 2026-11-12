export type CameraId = 'camera1' | 'camera2';

export interface SpatialPoint { x: number; y: number; z: number }
export interface SpatialCameraPosition extends SpatialPoint {
  side?: 'left' | 'right' | 'center';
  distanceToOriginMeters?: number;
}

export interface SpatialCameraConfig {
  offset: SpatialPoint;
  yawDeg: number;
  pitchDeg: number;
  rollDeg: number;
  intrinsics: {
    imageWidth: number;
    imageHeight: number;
    fx: number | null;
    fy: number | null;
    cx: number;
    cy: number;
    distortion: number[];
    source: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface SpatialConfig {
  version: 1;
  units: 'm';
  coordinateFrame?: { mode: string; xAxis: string; yAxis: string; zAxis: string; description?: string; [key: string]: unknown };
  rig: {
    midpoint: SpatialPoint;
    baselineMeters: number;
    baselineYawDeg: number;
    geometrySource?: 'manual' | 'extrinsics';
    cameraPositions?: Record<CameraId, SpatialCameraPosition> | null;
    [key: string]: unknown;
  };
  cameras: Record<CameraId, SpatialCameraConfig>;
  extrinsics?: {
    enabled: boolean;
    commonFrame: string;
    camera1: { rotationCameraToBoard: number[][] | null; translationCameraInBoard: number[] | null; [key: string]: unknown };
    camera2: { rotationCameraToBoard: number[][] | null; translationCameraInBoard: number[] | null; [key: string]: unknown };
    [key: string]: unknown;
  };
  stereo: {
    processingScale: number;
    minDisparity: number;
    numDisparities: number;
    blockSize: number;
    minValidDepthPoints: number;
    minDepthMeters: number;
    maxDepthMeters: number;
    [key: string]: unknown;
  };
  quality: { maxFrameSkewMs: number; maxReprojectionErrorPx: number; minFeatureMatches: number; maxRangeMeters: number; [key: string]: unknown };
  [key: string]: unknown;
}

export interface SpatialTarget extends SpatialPoint {
  id: string;
  camera1Id: string;
  camera2Id: string;
  label: string;
  class: string;
  keypointName: string;
  rangeMeters: number;
  azimuthDeg: number;
  elevationDeg: number;
  trackingState: 'measured' | 'held';
  keypointCount: number;
  reprojectionErrorPx: number;
  frameSkewMs: number;
  quality: 'calibrated';
}

export interface SpatialStatus {
  state: 'uncalibrated' | 'ready' | 'waiting_for_frames' | 'frame_time_gap' | 'no_targets' | 'no_valid_matches' | 'error';
  message: string;
  units: 'm';
  cameras: Partial<Record<CameraId, SpatialPoint>>;
  targets: SpatialTarget[];
  diagnostics: Record<string, unknown>;
}

export interface CameraBBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CameraDetectedObject {
  id: string;
  globalId?: string;
  displayId?: string;
  class: 'drone';
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
  | 'no_targets'
  | 'low_similarity'
  | 'ambiguous'
  | 'confirming'
  | 'partial_match'
  | 'matched';

export interface CameraMatchingDiagnostics {
  algorithmVersion: 'drone_stereo_center_v1' | 'unreported';
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
  spatial: SpatialStatus;
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

export interface CameraPreviewPair {
  epoch: string;
  camera1: { sequence: number; jpeg: Blob };
  camera2: { sequence: number; jpeg: Blob };
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
