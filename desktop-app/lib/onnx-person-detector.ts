import * as ort from 'onnxruntime-web/wasm';
import type { DetectedObject, DetectionInput } from './annotation-types';
import type { Detector, DetectorInfo } from './detector';
import { PersonTracker } from './object-tracker';
import { preprocessYoloSource } from './yolo-preprocess';
import {
  nonMaximumSuppression,
  parseYoloChannelMajor,
  restoreYoloDetections,
} from './yolo-postprocess';

const INPUT_SIZE = 640;

export class OnnxPersonDetector implements Detector {
  readonly mode = 'onnx' as const;
  private session: ort.InferenceSession | null = null;
  private initializePromise: Promise<DetectorInfo> | null = null;
  private readonly trackers = new Map<string, PersonTracker>();

  initialize(): Promise<DetectorInfo> {
    if (this.initializePromise) return this.initializePromise;
    this.initializePromise = this.initializeSession();
    return this.initializePromise;
  }

  private async initializeSession(): Promise<DetectorInfo> {
    ort.env.wasm.numThreads = 1;
    ort.env.wasm.proxy = false;
    ort.env.wasm.wasmPaths = new URL('./ort/', document.baseURI).href;
    const modelUrl = new URL('./models/person-v1.onnx', document.baseURI).href;
    try {
      const session = await ort.InferenceSession.create(modelUrl, {
        executionProviders: ['wasm'],
        graphOptimizationLevel: 'all',
        executionMode: 'sequential',
      });
      if (!session.inputNames.length || !session.outputNames.length) {
        await session.release();
        throw new Error('ONNX 模型没有有效的输入或输出节点。');
      }
      this.session = session;
    } catch (error) {
      this.session = null;
      this.initializePromise = null;
      throw new Error(`真实 ONNX 模型加载失败：${error instanceof Error ? error.message : String(error)}`);
    }
    return {
      mode: this.mode,
      name: 'YOLO26n person ONNX',
      inputWidth: INPUT_SIZE,
      inputHeight: INPUT_SIZE,
      classes: ['person'],
    };
  }

  async detect(input: DetectionInput): Promise<DetectedObject[]> {
    const info = await this.initialize();
    if (input.signal?.aborted) throw new DOMException('Detection aborted', 'AbortError');
    if (!input.source) throw new Error('真实检测缺少图片或视频帧。');
    const session = this.session;
    if (!session) throw new Error('ONNX Session 尚未初始化。');
    const prepared = preprocessYoloSource(input.source, input.width, input.height, info.inputWidth);
    const tensor = new ort.Tensor('float32', prepared.data, prepared.dims);
    const outputMap = await session.run({ [session.inputNames[0]]: tensor });
    if (input.signal?.aborted) throw new DOMException('Detection aborted', 'AbortError');
    const output = outputMap[session.outputNames[0]];
    if (!(output instanceof ort.Tensor)) throw new Error('ONNX 模型没有返回有效 Tensor。');
    const candidates = parseYoloChannelMajor(output.data as Float32Array, output.dims, 0.25, 0);
    const selected = nonMaximumSuppression(candidates, 0.45, 100);
    const detections = restoreYoloDetections(selected, prepared.meta, input.frameTime ?? 0);
    let tracker = this.trackers.get(input.mediaId);
    if (!tracker) {
      tracker = new PersonTracker();
      this.trackers.set(input.mediaId, tracker);
    }
    return tracker.update(input.mediaId, input.kind ?? 'image', detections);
  }

  async dispose() {
    for (const tracker of this.trackers.values()) tracker.reset();
    this.trackers.clear();
    await this.session?.release();
    this.session = null;
    this.initializePromise = null;
  }
}

export function createOnnxPersonDetector() {
  return new OnnxPersonDetector();
}
