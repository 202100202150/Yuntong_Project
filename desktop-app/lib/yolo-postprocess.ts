import type { BoundingBox, DetectedObject } from './annotation-types';
import type { LetterboxMeta } from './yolo-preprocess';

export type ModelBox = {
  candidateIndex: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  confidence: number;
};

export function parseYoloChannelMajor(
  data: ArrayLike<number>,
  dims: readonly number[],
  confidenceThreshold = 0.25,
  personClassId = 0,
): ModelBox[] {
  if (dims.length !== 3 || dims[0] !== 1)
    throw new Error(`不支持的模型输出形状：${dims.join('x')}`);
  const channelCount = dims[1];
  const candidateCount = dims[2];
  if (!Number.isInteger(channelCount) || channelCount < 5 || !Number.isInteger(candidateCount))
    throw new Error(`模型输出维度无效：${dims.join('x')}`);
  if (data.length !== channelCount * candidateCount)
    throw new Error('模型输出数据长度与维度不一致。');
  const classCount = channelCount - 4;
  if (personClassId < 0 || personClassId >= classCount)
    throw new Error(`person class ${personClassId} 超出模型类别范围。`);

  const boxes: ModelBox[] = [];
  for (let candidateIndex = 0; candidateIndex < candidateCount; candidateIndex += 1) {
    const centerX = Number(data[candidateIndex]);
    const centerY = Number(data[candidateCount + candidateIndex]);
    const width = Number(data[candidateCount * 2 + candidateIndex]);
    const height = Number(data[candidateCount * 3 + candidateIndex]);
    if (![centerX, centerY, width, height].every(Number.isFinite) || width <= 0 || height <= 0)
      continue;

    let bestClass = -1;
    let bestConfidence = -Infinity;
    for (let classIndex = 0; classIndex < classCount; classIndex += 1) {
      const confidence = Number(data[(4 + classIndex) * candidateCount + candidateIndex]);
      if (Number.isFinite(confidence) && confidence > bestConfidence) {
        bestConfidence = confidence;
        bestClass = classIndex;
      }
    }
    if (bestClass !== personClassId || bestConfidence < confidenceThreshold) continue;
    boxes.push({
      candidateIndex,
      x1: centerX - width / 2,
      y1: centerY - height / 2,
      x2: centerX + width / 2,
      y2: centerY + height / 2,
      confidence: Math.min(1, Math.max(0, bestConfidence)),
    });
  }
  return boxes;
}

export function boxIou(a: Pick<ModelBox, 'x1' | 'y1' | 'x2' | 'y2'>, b: Pick<ModelBox, 'x1' | 'y1' | 'x2' | 'y2'>) {
  const intersectionWidth = Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1));
  const intersectionHeight = Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1));
  const intersection = intersectionWidth * intersectionHeight;
  const areaA = Math.max(0, a.x2 - a.x1) * Math.max(0, a.y2 - a.y1);
  const areaB = Math.max(0, b.x2 - b.x1) * Math.max(0, b.y2 - b.y1);
  const union = areaA + areaB - intersection;
  return union > 0 ? intersection / union : 0;
}

export function nonMaximumSuppression(
  input: readonly ModelBox[],
  iouThreshold = 0.45,
  maximumDetections = 100,
) {
  const pending = [...input].sort(
    (a, b) => b.confidence - a.confidence || a.candidateIndex - b.candidateIndex,
  );
  const selected: ModelBox[] = [];
  while (pending.length && selected.length < maximumDetections) {
    const current = pending.shift();
    if (!current) break;
    selected.push(current);
    for (let index = pending.length - 1; index >= 0; index -= 1) {
      if (boxIou(current, pending[index]) >= iouThreshold) pending.splice(index, 1);
    }
  }
  return selected;
}

const clamp = (value: number, maximum: number) => Math.max(0, Math.min(maximum, value));

export function restoreYoloDetections(
  boxes: readonly ModelBox[],
  meta: LetterboxMeta,
  frameTime = 0,
): DetectedObject[] {
  return boxes.flatMap((box) => {
    const x1 = clamp((box.x1 - meta.padX) / meta.scale, meta.originalWidth);
    const y1 = clamp((box.y1 - meta.padY) / meta.scale, meta.originalHeight);
    const x2 = clamp((box.x2 - meta.padX) / meta.scale, meta.originalWidth);
    const y2 = clamp((box.y2 - meta.padY) / meta.scale, meta.originalHeight);
    if (x2 <= x1 || y2 <= y1) return [];
    const bbox: BoundingBox = {
      x: x1 / meta.originalWidth,
      y: y1 / meta.originalHeight,
      width: (x2 - x1) / meta.originalWidth,
      height: (y2 - y1) / meta.originalHeight,
    };
    return [{
      id: `candidate-${box.candidateIndex}`,
      class: 'person',
      className: 'person',
      label: 'person',
      confidence: box.confidence,
      bbox,
      source: 'model',
      visible: true,
      frameTime,
    } satisfies DetectedObject];
  });
}
