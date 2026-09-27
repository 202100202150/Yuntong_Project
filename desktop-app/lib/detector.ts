import type { DetectedObject, DetectionInput } from './annotation-types';

export type DetectorInfo = {
  mode: 'demo' | 'onnx';
  name: string;
  inputWidth: number;
  inputHeight: number;
  classes: readonly string[];
};

export type Detector = {
  readonly mode: DetectorInfo['mode'];
  initialize(): Promise<DetectorInfo>;
  detect(input: DetectionInput): Promise<DetectedObject[]>;
  dispose(): Promise<void> | void;
};

const fnv1a = (value: string): number => {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
};

const unit = (seed: number, salt: number): number => {
  let value = (seed ^ Math.imul(salt, 0x9e3779b1)) >>> 0;
  value ^= value << 13;
  value ^= value >>> 17;
  value ^= value << 5;
  return (value >>> 0) / 0x1_0000_0000;
};

const finitePositive = (value: number, field: string): number => {
  if (!Number.isFinite(value) || value <= 0)
    throw new RangeError(`${field} must be a finite positive number.`);
  return value;
};

const rounded = (value: number): number => Math.round(value * 100) / 100;

export class DemoDetector implements Detector {
  readonly mode = 'demo' as const;
  readonly seed: string;

  constructor(seed = 'yuntong-detection-workbench-demo-v1') {
    this.seed = seed;
  }

  async initialize(): Promise<DetectorInfo> {
    return {
      mode: this.mode,
      name: 'Deterministic demo detector',
      inputWidth: 0,
      inputHeight: 0,
      classes: ['person'],
    };
  }

  async detect(input: DetectionInput): Promise<DetectedObject[]> {
    if (input.signal?.aborted) throw input.signal.reason;

    const width = finitePositive(input.width, 'width');
    const height = finitePositive(input.height, 'height');
    const mediaId = input.mediaId.trim() || 'anonymous-media';
    const time = input.frameTime ?? input.time ?? 0;
    const frame = Number.isFinite(input.frameIndex)
      ? Math.max(0, Math.floor(input.frameIndex ?? 0))
      : Math.max(0, Math.floor(time * 5));
    const seed = fnv1a(`${this.seed}:${mediaId}`);

    const boxWidth = Math.min(0.32, Math.max(24 / width, 0.18 + unit(seed, 1) * 0.08));
    const boxHeight = Math.min(
      0.55,
      Math.max(32 / height, 0.34 + unit(seed, 2) * 0.12),
    );
    const verticalRoom = Math.max(0, 1 - boxHeight);
    const horizontalRoom = Math.max(0, 1 - boxWidth);
    const phase = frame * 0.045;

    const makeDetection = (index: number): DetectedObject => {
      const className = 'person' as const;
      const anchor = index === 0 ? 0.22 : 0.68;
      const driftX = Math.sin(phase + unit(seed, 10 + index) * Math.PI * 2) * 0.04;
      const driftY = Math.cos(phase * 0.7 + unit(seed, 20 + index) * Math.PI * 2) * 0.035;
      const x = Math.min(
        horizontalRoom,
        Math.max(0, horizontalRoom * (anchor + driftX)),
      );
      const y = Math.min(
        verticalRoom,
        Math.max(0, verticalRoom * (0.26 + index * 0.08 + driftY)),
      );
      const confidence = Math.min(
        0.99,
        0.88 + unit(seed, 30 + index) * 0.09,
      );

      return {
        id: `demo-${className}-${fnv1a(`${mediaId}:${index}`).toString(36)}`,
        class: className,
        className,
        label: className,
        confidence: rounded(confidence),
        bbox: {
          x: rounded(x),
          y: rounded(y),
          width: rounded(boxWidth),
          height: rounded(boxHeight),
        },
        source: 'demo',
        visible: true,
        frameTime: time,
      };
    };

    // Images keep a media-stable result. Videos use a deterministic phase so
    // the current-frame list/count visibly changes without pretending to run
    // a real model.
    const count = input.kind === 'video'
      ? 1 + (Math.floor(frame / 8 + unit(seed, 42) * 3) % 3)
      : 1 + (seed % 3);
    const detections = [
      makeDetection(0),
      makeDetection(1),
      makeDetection(2),
    ];
    return detections.slice(0, count);
  }

  dispose(): void {}
}

export function createDemoDetector(seed?: string): DemoDetector {
  return new DemoDetector(seed);
}

export const demoDetector = createDemoDetector();
