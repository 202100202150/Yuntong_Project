export const annotationClasses = ['person'] as const;

export type AnnotationClass = (typeof annotationClasses)[number];
export type AnnotationClassInput = AnnotationClass | 'male' | 'female' | 'famale';

export type BoundingBox = {
  /** Left edge as a normalized 0..1 media coordinate. */
  x: number;
  /** Top edge as a normalized 0..1 media coordinate. */
  y: number;
  /** Width as a normalized 0..1 media coordinate. */
  width: number;
  /** Height as a normalized 0..1 media coordinate. */
  height: number;
};

export type DetectionInput = {
  mediaId: string;
  /** Original image or current video frame. Required by real detectors. */
  source?: CanvasImageSource;
  width: number;
  height: number;
  /** Seconds from the start of a video. Images should leave this at zero. */
  time?: number;
  /** Preferred time field used by the annotation workspace. */
  frameTime?: number;
  /** Optional explicit frame number for callers that already sample frames. */
  frameIndex?: number;
  kind?: 'image' | 'video';
  signal?: AbortSignal;
};

/**
 * Annotation detector output for the local two-dimensional media workspace.
 */
export type DetectedObject = {
  id: string;
  class: AnnotationClass;
  className: AnnotationClass;
  /** Alias kept convenient for renderers that display a short label. */
  label: AnnotationClass;
  confidence: number;
  bbox: BoundingBox;
  source: 'demo' | 'model';
  visible: boolean;
  frameTime?: number;
};

export function normalizeDetectedClass(
  value: string | null | undefined,
): AnnotationClass | null {
  const normalized = value?.trim().toLowerCase();
  if (
    normalized === 'person' ||
    normalized === 'male' ||
    normalized === 'female' ||
    normalized === 'famale'
  ) return 'person';
  return null;
}

export function isDetectedObject(value: unknown): value is DetectedObject {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<DetectedObject>;
  const bbox = item.bbox as Partial<BoundingBox> | undefined;
  return (
    typeof item.id === 'string' &&
    normalizeDetectedClass(item.class ?? item.className) !== null &&
    typeof item.confidence === 'number' &&
    Number.isFinite(item.confidence) &&
    item.confidence >= 0 &&
    item.confidence <= 1 &&
    !!bbox &&
    [bbox.x, bbox.y, bbox.width, bbox.height].every(
      (number) => typeof number === 'number' && Number.isFinite(number),
    ) &&
    (bbox.width ?? 0) > 0 &&
    (bbox.height ?? 0) > 0 &&
    (bbox.x ?? -1) >= 0 &&
    (bbox.y ?? -1) >= 0 &&
    (bbox.x ?? 1) + (bbox.width ?? 1) <= 1 &&
    (bbox.y ?? 1) + (bbox.height ?? 1) <= 1
  );
}
