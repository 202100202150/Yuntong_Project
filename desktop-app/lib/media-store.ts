export const mediaKinds = ['image', 'video'] as const;
export type MediaKind = (typeof mediaKinds)[number];

export type MediaItem = {
  id: string;
  sourceKey: string;
  file: File;
  name: string;
  kind: MediaKind;
  objectUrl: string;
  size: number;
  lastModified: number;
  objects: import('./annotation-types').DetectedObject[];
  detected: boolean;
  status: 'ready' | 'detecting' | 'detected' | 'error';
  currentFrame: number;
  peakObjectCount: number;
};

export type CreateMediaItemOptions = {
  sourceKey?: string;
};

const imageExtensions = new Set([
  'avif',
  'bmp',
  'gif',
  'heic',
  'heif',
  'jpeg',
  'jpg',
  'png',
  'webp',
]);
const videoExtensions = new Set([
  'avi',
  'm4v',
  'mkv',
  'mov',
  'mp4',
  'mpeg',
  'mpg',
  'webm',
]);

const extensionOf = (name: string): string =>
  name.split('.').pop()?.toLowerCase() ?? '';

export function getMediaKind(file: Pick<File, 'name' | 'type'>): MediaKind | null {
  const mime = file.type.toLowerCase();
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('video/')) return 'video';
  const extension = extensionOf(file.name);
  if (imageExtensions.has(extension)) return 'image';
  if (videoExtensions.has(extension)) return 'video';
  return null;
}

const stableFileId = (file: Pick<File, 'name' | 'size' | 'lastModified'>): string => {
  const value = `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `media-${(hash >>> 0).toString(36)}`;
};

export function createMediaItem(
  file: File,
  options: CreateMediaItemOptions = {},
): MediaItem {
  const kind = getMediaKind(file);
  if (!kind) throw new TypeError(`Unsupported media file: ${file.name}`);
  if (typeof URL.createObjectURL !== 'function')
    throw new Error('Object URLs are unavailable in this environment.');
  return {
    id: stableFileId(file),
    sourceKey: options.sourceKey ?? `${file.name.toLowerCase()}:${file.size}:${file.lastModified}`,
    file,
    name: file.name,
    kind,
    objectUrl: URL.createObjectURL(file),
    size: file.size,
    lastModified: file.lastModified,
    objects: [],
    detected: false,
    status: 'ready',
    currentFrame: 0,
    peakObjectCount: 0,
  };
}

export function releaseMediaItem(item: Pick<MediaItem, 'objectUrl'>): void {
  if (item.objectUrl && typeof URL.revokeObjectURL === 'function')
    URL.revokeObjectURL(item.objectUrl);
}

export function selectMediaAfterRemoval(
  items: readonly Pick<MediaItem, 'id'>[],
  removedId: string,
  activeId: string | null,
): string | null {
  if (activeId !== removedId) return activeId;
  const removedIndex = items.findIndex((item) => item.id === removedId);
  if (removedIndex < 0) return null;
  return items[removedIndex + 1]?.id ?? items[removedIndex - 1]?.id ?? null;
}

export class MediaStore {
  #items = new Map<string, MediaItem>();

  get size(): number {
    return this.#items.size;
  }

  list(): MediaItem[] {
    return [...this.#items.values()];
  }

  get(id: string): MediaItem | undefined {
    return this.#items.get(id);
  }

  add(file: File): MediaItem {
    const item = createMediaItem(file);
    const existing = this.#items.get(item.id);
    if (existing) {
      releaseMediaItem(item);
      return existing;
    }
    this.#items.set(item.id, item);
    return item;
  }

  addMany(files: Iterable<File>): MediaItem[] {
    const items: MediaItem[] = [];
    for (const file of files) {
      if (getMediaKind(file)) items.push(this.add(file));
    }
    return items;
  }

  remove(id: string): boolean {
    const item = this.#items.get(id);
    if (!item) return false;
    releaseMediaItem(item);
    return this.#items.delete(id);
  }

  clear(): void {
    for (const item of this.#items.values()) releaseMediaItem(item);
    this.#items.clear();
  }
}
