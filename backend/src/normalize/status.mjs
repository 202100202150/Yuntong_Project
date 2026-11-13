import { normalizeSpatial } from './spatial.mjs';

const cameraIds = new Set(['camera1', 'camera2']);

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

function nullableFiniteNumber(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function nullableSignedFiniteNumber(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function cameraTrackId(value) {
  return typeof value === 'string' && /^track-\d{1,12}$/.test(value) ? value : null;
}

function boundedStatusString(value, fallback = 'unreported') {
  return typeof value === 'string' && value.trim() ? value.trim().slice(0, 80) : fallback;
}

export function emptyCameraStatus(id, state = 'starting') {
  return {
    id,
    label: id,
    state,
    error: '',
    width: 0,
    height: 0,
    decodeFps: 0,
    framesDecoded: 0,
    reconnects: 0,
    sequence: 0,
    frameAvailable: false,
    recordingState: 'idle',
    detectionCount: 0,
    detectionAgeMs: null,
    previewFps: 0,
    detectionFps: 0,
    latency: null,
    objects: [],
  };
}

export function normalizeStatus(payload) {
  const rootDetection = payload?.detection && typeof payload.detection === 'object'
    ? payload.detection : {};
  const inference = payload?.inference && typeof payload.inference === 'object'
    ? payload.inference
    : rootDetection.inference && typeof rootDetection.inference === 'object'
      ? rootDetection.inference
      : rootDetection;
  const preview = payload?.preview && typeof payload.preview === 'object' ? payload.preview : {};

  const rawMatching = payload?.matchingDiagnostics && typeof payload.matchingDiagnostics === 'object'
    ? payload.matchingDiagnostics : {};
  const matchingReasons = new Set([
    'waiting_for_frames', 'capture_time_gap', 'no_targets',
    'low_similarity', 'ambiguous', 'confirming', 'partial_match', 'matched',
  ]);
  const matchingReason = boundedStatusString(rawMatching.reason, 'waiting_for_frames');
  const matchingVersion = rawMatching.algorithmVersion;
  const rawReasonCounts = rawMatching.reasonCounts && typeof rawMatching.reasonCounts === 'object'
    && !Array.isArray(rawMatching.reasonCounts) ? rawMatching.reasonCounts : {};
  const reasonCounts = Object.fromEntries([...matchingReasons].map((r) => [
    r, Math.floor(finiteNumber(rawReasonCounts[r])),
  ]));
  const recentFailures = Array.isArray(rawMatching.recentFailures)
    ? rawMatching.recentFailures.slice(0, 5).flatMap((item) => {
      if (!item || typeof item !== 'object' || !matchingReasons.has(item.reason)) return [];
      return [{
        reason: item.reason,
        bestSimilarity: nullableFiniteNumber(item.bestSimilarity),
        secondSimilarity: nullableFiniteNumber(item.secondSimilarity),
        camera1Id: cameraTrackId(item.camera1Id),
        camera2Id: cameraTrackId(item.camera2Id),
        frameSkewMs: nullableFiniteNumber(item.frameSkewMs),
        ageMs: Math.floor(finiteNumber(item.ageMs)),
      }];
    }) : [];

  const matchingDiagnostics = {
    algorithmVersion: matchingVersion === 'drone_stereo_center_v1' ? matchingVersion : 'unreported',
    reason: matchingReasons.has(matchingReason) ? matchingReason : 'waiting_for_frames',
    bestSimilarity: nullableFiniteNumber(rawMatching.bestSimilarity),
    secondSimilarity: nullableFiniteNumber(rawMatching.secondSimilarity),
    camera1Id: cameraTrackId(rawMatching.camera1Id),
    camera2Id: cameraTrackId(rawMatching.camera2Id),
    comparisonCount: Math.floor(finiteNumber(rawMatching.comparisonCount)),
    frameSkewMs: nullableFiniteNumber(rawMatching.frameSkewMs),
    offsetMs: nullableSignedFiniteNumber(rawMatching.offsetMs),
    pendingPairs: finiteNumber(rawMatching.pendingPairs),
    confirmedPairs: finiteNumber(rawMatching.confirmedPairs),
    geometryStatus: rawMatching.geometryStatus === 'ready' ? 'ready' : 'unavailable',
    geometryInliers: Math.floor(finiteNumber(rawMatching.geometryInliers)),
    reasonCounts,
    recentFailures,
  };

  const sourceCameras = payload?.cameras && typeof payload.cameras === 'object' ? payload.cameras : {};
  const cameras = {};
  for (const id of cameraIds) {
    const source = sourceCameras[id] ?? {};
    const stream = source.stream ?? source.status ?? source;
    const recording = source.recording ?? {};
    const detection = source.detection ?? {};
    const state = typeof stream.state === 'string' ? stream.state : 'starting';
    cameras[id] = {
      id,
      label: typeof source.label === 'string' ? source.label : id,
      state: ['starting', 'connecting', 'streaming', 'reconnecting', 'stopped', 'error'].includes(state)
        ? state : 'error',
      error: typeof stream.error === 'string' ? stream.error.slice(0, 240) : '',
      width: finiteNumber(stream.width),
      height: finiteNumber(stream.height),
      decodeFps: finiteNumber(stream.decode_fps ?? stream.decodeFps),
      framesDecoded: finiteNumber(stream.frames_decoded ?? stream.framesDecoded),
      reconnects: finiteNumber(stream.reconnects),
      sequence: finiteNumber(source.frame?.sequence ?? source.sequence ?? stream.sequence),
      frameAvailable: Boolean(
        source.frame?.available ?? source.frame_available ?? source.frameAvailable ?? source.sequence,
      ),
      recordingState: String(recording.state ?? source.recording_state ?? 'idle'),
      detectionCount: finiteNumber(
        detection.count ?? source.objectCount ?? source.objects?.length ?? source.detection_count,
      ),
      detectionAgeMs: nullableFiniteNumber(
        detection.ageMs ?? detection.age_ms ?? source.detectionAgeMs ?? source.detection_age_ms,
      ),
      previewFps: finiteNumber(
        source.previewFps ?? source.preview_fps ?? source.preview?.fps ?? stream.preview_fps,
      ),
      detectionFps: finiteNumber(
        source.detectionFps ?? source.detection_fps ?? detection.fps,
      ),
      latency: nullableFiniteNumber(
        source.inferenceLatencyMs
          ?? source.inference_latency_ms
          ?? source.latency
          ?? detection.latencyMs
          ?? detection.latency_ms
          ?? detection.latency,
      ),
      objects: Array.isArray(source.objects) ? source.objects.flatMap((item) => {
        const bbox = item?.bbox;
        const confidence = Number(item?.confidence);
        if (!item || typeof item.id !== 'string' || item.class !== 'drone' || !bbox) return [];
        const values = [bbox.x, bbox.y, bbox.width, bbox.height].map(Number);
        if (!values.every(Number.isFinite) || values[2] <= 0 || values[3] <= 0) return [];
        return [{
          id: item.id.slice(0, 80),
          ...(typeof item.globalId === 'string' && item.globalId.trim()
            ? { globalId: item.globalId.slice(0, 160) }
            : {}),
          ...(typeof item.displayId === 'string' && /^drone(?:[1-9]\d{0,11})?$/.test(item.displayId)
            ? { displayId: item.displayId }
            : {}),
          class: 'drone',
          confidence: Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : 0,
          bbox: { x: values[0], y: values[1], width: values[2], height: values[3] },
          frameSequence: finiteNumber(item.frameSequence ?? item.frame_sequence),
        }];
      }) : [],
    };
  }

  // Arming and finalizing are intermediate states, not completed recording.
  const recording = Object.values(cameras).some((c) => c.recordingState === 'recording');

  const detectionModelState = boundedStatusString(
    rootDetection.modelState
      ?? rootDetection.model_state
      ?? payload?.modelState
      ?? payload?.model_state,
  ).toLowerCase();
  const normalizedEngineState = boundedStatusString(
    inference.engineState
      ?? inference.engine_state
      ?? payload?.engineState
      ?? payload?.engine_state,
  ).toLowerCase();
  const detectionError = boundedStatusString(rootDetection.error ?? inference.error ?? payload?.error, '');
  const inferenceFailed = ['error', 'failed', 'failure', 'closed'].includes(normalizedEngineState)
    || ['error', 'failed', 'failure'].includes(detectionModelState);

  return {
    serviceState: inferenceFailed ? 'error' : 'ready',
    error: inferenceFailed
      ? (detectionError || `TensorRT 推理状态异常（${normalizedEngineState || detectionModelState}）。`)
      : '',
    recording,
    detecting: Boolean(payload?.detection?.running ?? payload?.detecting ?? payload?.detection_enabled),
    inferenceProvider: boundedStatusString(
      inference.provider
        ?? inference.inferenceProvider
        ?? inference.backend
        ?? payload?.inferenceProvider,
    ),
    precision: boundedStatusString(inference.precision ?? payload?.precision),
    previewProcessor: boundedStatusString(
      preview.processor ?? rootDetection.previewProcessor ?? payload?.previewProcessor,
    ),
    engineState: boundedStatusString(
      inference.engineState ?? inference.engine_state ?? payload?.engineState,
    ),
    matchingDiagnostics,
    spatial: normalizeSpatial(payload?.spatial),
    cameras,
  };
}
