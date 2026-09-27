import type { MediaItem } from './media-store';

type FileWithPath = File & { path?: string };

type FileSystemWritableFileStreamLike = {
  write(data: Blob): Promise<void>;
  close(): Promise<void>;
  abort?(reason?: unknown): Promise<void>;
};

type FileSystemFileHandleLike = {
  createWritable(): Promise<FileSystemWritableFileStreamLike>;
};

type SaveFilePicker = (options: {
  suggestedName?: string;
  types?: Array<{
    description?: string;
    accept: Record<string, string[]>;
  }>;
}) => Promise<FileSystemFileHandleLike>;

type DesktopFileBridge = {
  getPathForFile?(file: File): string | undefined;
  saveScreenshotAdjacent(input: {
    sourcePath: string;
    fileName: string;
    pngBytes: ArrayBuffer;
    conflictPolicy: 'increment';
  }): Promise<{ path: string }>;
};

export type SaveScreenshotResult = {
  mode: 'file-system-access' | 'download' | 'cancelled' | 'unavailable';
  fileName: string;
  saved: boolean;
  message: string;
};

export type SaveScreenshotOptions = {
  suggestedName?: string;
};

export type PathDescription = {
  value: string;
  isAbsolute: boolean;
};

const reservedWindowsName = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;

const safeBaseName = (value: string): string => {
  const withoutExtension = value.replace(/\.[^.]*$/, '');
  const withoutControls = Array.from(withoutExtension.normalize('NFKC'), (character) =>
    character.charCodeAt(0) < 32 ? '-' : character,
  ).join('');
  let safe = withoutControls
    .replace(/[<>:"/\\|?*]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^[.\s-]+|[.\s-]+$/g, '')
    .slice(0, 80);
  if (!safe || reservedWindowsName.test(safe)) safe = `media-${Date.now()}`;
  return safe;
};

export function describeAbsolutePath(
  source: File | Pick<MediaItem, 'file' | 'name'>,
): PathDescription {
  const file = 'file' in source ? source.file : source;
  const bridge = (globalThis as typeof globalThis & {
    yuntongFileBridge?: DesktopFileBridge;
  }).yuntongFileBridge;
  let bridgePath: string | undefined;
  try {
    bridgePath = bridge?.getPathForFile?.(file)?.trim();
  } catch {
    bridgePath = undefined;
  }
  const nativePath = bridgePath || (file as FileWithPath).path?.trim();
  if (nativePath)
    return {
      value: nativePath,
      isAbsolute: /^(?:[a-z]:[\\/]|\\\\|\/)/i.test(nativePath),
    };
  return {
    value: `当前浏览器无法提供绝对路径（${file.name}）`,
    isAbsolute: false,
  };
}

export function makeScreenshotFileName(
  item: Pick<MediaItem, 'id' | 'name'>,
  now = new Date(),
): string {
  const timestamp = Number.isFinite(now.getTime())
    ? now.toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', '')
    : 'undated';
  const safeId = safeBaseName(item.id).slice(0, 32);
  return `${safeBaseName(item.name)}_${safeId}_${timestamp}.png`;
}

const triggerDownload = (blob: Blob, fileName: string): boolean => {
  if (
    typeof document === 'undefined' ||
    typeof URL.createObjectURL !== 'function'
  )
    return false;
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = fileName;
    anchor.rel = 'noopener';
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    return true;
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
};

export async function saveScreenshot(
  blob: Blob,
  item: Pick<MediaItem, 'id' | 'name' | 'file'>,
  options: SaveScreenshotOptions = {},
): Promise<SaveScreenshotResult> {
  const fileName = options.suggestedName
    ? `${safeBaseName(options.suggestedName)}.png`
    : makeScreenshotFileName(item);
  const globals = globalThis as typeof globalThis & {
    showSaveFilePicker?: SaveFilePicker;
    yuntongFileBridge?: DesktopFileBridge;
  };
  const sourcePath = describeAbsolutePath(item);

  if (sourcePath.isAbsolute && globals.yuntongFileBridge) {
    const result = await globals.yuntongFileBridge.saveScreenshotAdjacent({
      sourcePath: sourcePath.value,
      fileName,
      pngBytes: await blob.arrayBuffer(),
      conflictPolicy: 'increment',
    });
    return {
      mode: 'file-system-access',
      fileName,
      saved: true,
      message: `截图已保存到源媒体同目录：${result.path}`,
    };
  }

  const picker = globals.showSaveFilePicker;

  if (typeof picker === 'function') {
    let writable: FileSystemWritableFileStreamLike | undefined;
    try {
      const handle = await picker({
        suggestedName: fileName,
        types: [
          {
            description: 'PNG image',
            accept: { 'image/png': ['.png'] },
          },
        ],
      });
      writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return {
        mode: 'file-system-access',
        fileName,
        saved: true,
        message: '截图已保存。',
      };
    } catch (error) {
      await writable?.abort?.(error).catch(() => undefined);
      if (error instanceof DOMException && error.name === 'AbortError')
        return {
          mode: 'cancelled',
          fileName,
          saved: false,
          message: '已取消保存截图。',
        };
      // Permission/policy/browser implementation failures fall through to a
      // regular browser download instead of losing the user's screenshot.
    }
  }

  const saved = triggerDownload(blob, fileName);
  return {
    mode: saved ? 'download' : 'unavailable',
    fileName,
    saved,
    message: saved
      ? '浏览器不支持直接写入文件，已改为下载截图。'
      : '当前环境无法保存截图。',
  };
}
