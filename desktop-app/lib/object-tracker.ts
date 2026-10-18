import type { BoundingBox, DetectedObject } from './annotation-types';

type Track = { id: string; bbox: BoundingBox; missed: number };

function normalizedIou(a: BoundingBox, b: BoundingBox) {
  const ax2 = a.x + a.width;
  const ay2 = a.y + a.height;
  const bx2 = b.x + b.width;
  const by2 = b.y + b.height;
  const intersection =
    Math.max(0, Math.min(ax2, bx2) - Math.max(a.x, b.x)) *
    Math.max(0, Math.min(ay2, by2) - Math.max(a.y, b.y));
  const union = a.width * a.height + b.width * b.height - intersection;
  return union > 0 ? intersection / union : 0;
}

export class PersonTracker {
  private tracks: Track[] = [];
  private nextId = 1;
  private mediaId: string | null = null;

  reset() {
    this.tracks = [];
    this.nextId = 1;
    this.mediaId = null;
  }

  update(mediaId: string, kind: 'image' | 'video', detections: readonly DetectedObject[]) {
    if (this.mediaId !== mediaId) {
      this.reset();
      this.mediaId = mediaId;
    }
    if (kind === 'image') {
      return [...detections]
        .sort((a, b) => a.bbox.x - b.bbox.x || a.bbox.y - b.bbox.y)
        .map((detection, index) => ({ ...detection, id: `person-${index + 1}` }));
    }

    const unmatchedTracks = new Set(this.tracks.map((_, index) => index));
    const nextTracks: Track[] = [];
    const tracked = detections.map((detection) => {
      let matchedIndex = -1;
      let matchedIou = 0.3;
      for (const trackIndex of unmatchedTracks) {
        const overlap = normalizedIou(this.tracks[trackIndex].bbox, detection.bbox);
        if (overlap > matchedIou) {
          matchedIou = overlap;
          matchedIndex = trackIndex;
        }
      }
      const id = matchedIndex >= 0 ? this.tracks[matchedIndex].id : `person-${this.nextId++}`;
      if (matchedIndex >= 0) unmatchedTracks.delete(matchedIndex);
      nextTracks.push({ id, bbox: detection.bbox, missed: 0 });
      return { ...detection, id };
    });
    for (const trackIndex of unmatchedTracks) {
      const track = this.tracks[trackIndex];
      if (track.missed < 5) nextTracks.push({ ...track, missed: track.missed + 1 });
    }
    this.tracks = nextTracks;
    return tracked;
  }
}
