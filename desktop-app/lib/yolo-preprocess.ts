export type LetterboxMeta = {
  originalWidth: number;
  originalHeight: number;
  inputSize: number;
  resizedWidth: number;
  resizedHeight: number;
  scale: number;
  padX: number;
  padY: number;
};

const finitePositive = (value: number, field: string): number => {
  if (!Number.isFinite(value) || value <= 0)
    throw new RangeError(`${field} must be a finite positive number.`);
  return value;
};

export function calculateLetterbox(
  originalWidth: number,
  originalHeight: number,
  inputSize = 640,
): LetterboxMeta {
  const width = finitePositive(originalWidth, 'originalWidth');
  const height = finitePositive(originalHeight, 'originalHeight');
  const size = Math.round(finitePositive(inputSize, 'inputSize'));
  const scale = Math.min(size / width, size / height);
  const resizedWidth = Math.max(1, Math.round(width * scale));
  const resizedHeight = Math.max(1, Math.round(height * scale));
  return {
    originalWidth: width,
    originalHeight: height,
    inputSize: size,
    resizedWidth,
    resizedHeight,
    scale,
    padX: Math.floor((size - resizedWidth) / 2),
    padY: Math.floor((size - resizedHeight) / 2),
  };
}

export function rgbaToNchw(rgba: Uint8ClampedArray, width: number, height: number) {
  const pixelCount = width * height;
  if (rgba.length !== pixelCount * 4)
    throw new RangeError('RGBA buffer length does not match its dimensions.');
  const chw = new Float32Array(pixelCount * 3);
  for (let pixel = 0; pixel < pixelCount; pixel += 1) {
    const sourceIndex = pixel * 4;
    chw[pixel] = rgba[sourceIndex] / 255;
    chw[pixelCount + pixel] = rgba[sourceIndex + 1] / 255;
    chw[pixelCount * 2 + pixel] = rgba[sourceIndex + 2] / 255;
  }
  return chw;
}

export function preprocessYoloSource(
  source: CanvasImageSource,
  originalWidth: number,
  originalHeight: number,
  inputSize = 640,
) {
  const meta = calculateLetterbox(originalWidth, originalHeight, inputSize);
  const canvas = document.createElement('canvas');
  canvas.width = meta.inputSize;
  canvas.height = meta.inputSize;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('无法创建模型预处理画布。');
  context.fillStyle = 'rgb(114, 114, 114)';
  context.fillRect(0, 0, meta.inputSize, meta.inputSize);
  context.drawImage(
    source,
    0,
    0,
    meta.originalWidth,
    meta.originalHeight,
    meta.padX,
    meta.padY,
    meta.resizedWidth,
    meta.resizedHeight,
  );
  const pixels = context.getImageData(0, 0, meta.inputSize, meta.inputSize).data;
  return {
    data: rgbaToNchw(pixels, meta.inputSize, meta.inputSize),
    dims: [1, 3, meta.inputSize, meta.inputSize] as const,
    meta,
  };
}
