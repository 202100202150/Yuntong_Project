import type { CameraDetectedObject, CameraId } from './camera-types';

const cameraIds: readonly CameraId[] = ['camera1', 'camera2'];

export type SpatialTrack = {
  id: string;
  left?: CameraDetectedObject;
  right?: CameraDetectedObject;
};

type IndexedObject = { index: number; object: CameraDetectedObject };

function center(object: CameraDetectedObject): [number, number] {
  return [object.bbox.x + object.bbox.width * 0.5, object.bbox.y + object.bbox.height * 0.5];
}

function geometryCost(left: CameraDetectedObject, right: CameraDetectedObject): number {
  const [leftX, leftY] = center(left);
  const [rightX, rightY] = center(right);
  const heightCost = Math.abs(Math.log((left.bbox.height + 0.02) / (right.bbox.height + 0.02)));
  const widthCost = Math.abs(Math.log((left.bbox.width + 0.02) / (right.bbox.width + 0.02)));
  return Math.abs(leftX - rightX) * 1.4 + Math.abs(leftY - rightY) * 0.35 + heightCost * 0.55 + widthCost * 0.2;
}

/** Pair only the current unmatched detections; no old identity is retained. */
function currentFrameAssignment(left: readonly IndexedObject[], right: readonly IndexedObject[]): Array<[IndexedObject, IndexedObject]> {
  if (!left.length || !right.length) return [];
  const [smaller, larger, swapped] = left.length <= right.length
    ? [left, right, false] as const : [right, left, true] as const;
  if (larger.length > 12) {
    const remaining = [...larger];
    return smaller.map((item) => {
      let best = 0;
      let bestCost = Number.POSITIVE_INFINITY;
      for (let index = 0; index < remaining.length; index += 1) {
        const candidate = swapped ? geometryCost(remaining[index].object, item.object) : geometryCost(item.object, remaining[index].object);
        if (candidate < bestCost) { best = index; bestCost = candidate; }
      }
      const match = remaining.splice(best, 1)[0];
      return swapped ? [match, item] : [item, match];
    });
  }
  const costs = smaller.map((item) => larger.map((candidate) => swapped
    ? geometryCost(candidate.object, item.object)
    : geometryCost(item.object, candidate.object)));
  const memo = new Map<string, { cost: number; picks: number[] }>();
  const solve = (smallIndex: number, usedMask: number): { cost: number; picks: number[] } => {
    if (smallIndex >= smaller.length) return { cost: 0, picks: [] };
    const key = smallIndex + ':' + usedMask;
    const cached = memo.get(key);
    if (cached) return cached;
    let best = { cost: Number.POSITIVE_INFINITY, picks: [] as number[] };
    for (let largeIndex = 0; largeIndex < larger.length; largeIndex += 1) {
      if (usedMask & (1 << largeIndex)) continue;
      const next = solve(smallIndex + 1, usedMask | (1 << largeIndex));
      const candidate = {
        cost: costs[smallIndex][largeIndex] + next.cost,
        picks: [largeIndex, ...next.picks],
      };
      if (candidate.cost < best.cost) best = candidate;
    }
    memo.set(key, best);
    return best;
  };
  return solve(0, 0).picks.map((largeIndex, smallIndex) => swapped
    ? [larger[largeIndex], smaller[smallIndex]]
    : [smaller[smallIndex], larger[largeIndex]]);
}

export function groupSpatialTracks(
  objects: Record<CameraId, CameraDetectedObject[]>,
): SpatialTrack[] {
  const tracks = new Map<string, SpatialTrack>();
  const unmatched: Record<CameraId, IndexedObject[]> = { camera1: [], camera2: [] };
  for (const cameraId of cameraIds) {
    const side = cameraId === 'camera1' ? 'left' : 'right';
    objects[cameraId].forEach((object, index) => {
      const globalId = object.globalId?.trim();
      const sharedIdentity = globalId?.startsWith('stereo:') ? globalId : undefined;
      if (!sharedIdentity) {
        unmatched[cameraId].push({ index, object });
        return;
      }
      let identity = sharedIdentity;
      let collision = 1;
      while (tracks.get(identity)?.[side]) {
        identity = sharedIdentity + ':' + cameraId + ':' + object.id + ':' + collision++;
      }
      const track = tracks.get(identity) ?? { id: object.id };
      track[side] = object;
      tracks.set(identity, track);
    });
  }

  // Reconcile only this snapshot. A failed match must not turn every local
  // detection into a new 3D person or retain a departed target.
  const paired = currentFrameAssignment(unmatched.camera1, unmatched.camera2);
  const pairedLeft = new Set(paired.map(([left]) => left.index));
  const pairedRight = new Set(paired.map(([, right]) => right.index));
  for (const [left, right] of paired) {
    tracks.set('current:pair:' + left.index + ':' + right.index, {
      id: left.object.id,
      left: left.object,
      right: right.object,
    });
  }
  for (const item of unmatched.camera1) {
    if (!pairedLeft.has(item.index)) tracks.set('current:left:' + item.index, { id: item.object.id, left: item.object });
  }
  for (const item of unmatched.camera2) {
    if (!pairedRight.has(item.index)) tracks.set('current:right:' + item.index, { id: item.object.id, right: item.object });
  }
  return [...tracks.values()];
}
