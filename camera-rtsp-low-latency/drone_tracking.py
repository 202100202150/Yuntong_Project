"""Keep a short, stable track for each triangulated drone."""

from dataclasses import dataclass
import math
from typing import Any

import numpy as np


@dataclass
class _Track:
    target: dict[str, Any]
    position: np.ndarray
    observed: np.ndarray
    observed_at: float
    hits: int = 1


class DronePositionTracker:
    """Reject isolated jumps, then smooth the accepted XYZ measurements."""

    HOLD_SECONDS = 0.45
    FRESH_SECONDS = 0.18
    SMOOTH_SECONDS = 0.16
    MAX_SPEED_MPS = 30.0

    def __init__(self) -> None:
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1
        self._last_sample_at = -math.inf

    @staticmethod
    def _view_ids(target: dict[str, Any]) -> tuple[str, str]:
        return target["camera1Id"], target["camera2Id"]

    def update(
        self, targets: list[dict[str, Any]], sample_at: float, now: float,
    ) -> list[dict[str, Any]]:
        self._tracks = {
            key: track for key, track in self._tracks.items()
            if now - track.observed_at <= self.HOLD_SECONDS
        }
        if sample_at > self._last_sample_at and 0 <= now - sample_at <= self.HOLD_SECONDS:
            self._last_sample_at = sample_at
            self._associate(targets, sample_at)
        return [
            self._display(key, track, sample_at, now)
            for key, track in self._tracks.items() if track.hits >= 2
        ]

    def _associate(self, targets: list[dict[str, Any]], sample_at: float) -> None:
        observations = [
            np.array([target[axis] for axis in "xyz"], dtype=float)
            for target in targets
        ]
        candidates = []
        claimed = set()
        for key, track in self._tracks.items():
            old_ids = self._view_ids(track.target)
            dt = max(0.0, sample_at - track.observed_at)
            # Depth jitter grows with range. Allow that noise and plausible
            # flight between observations, but do not move to a distant outlier.
            noise = max(0.6, 0.025 * float(np.linalg.norm(track.observed)))
            limit = noise + self.MAX_SPEED_MPS * dt
            for index, (target, point) in enumerate(zip(targets, observations)):
                shared = sum(a == b for a, b in zip(old_ids, self._view_ids(target)))
                if shared:
                    claimed.add(index)
                distance = float(np.linalg.norm(point - track.observed))
                if distance <= limit:
                    candidates.append((-shared, distance, key, index))

        updated = set()
        used = set()
        for negative_shared, _, key, index in sorted(candidates):
            if key in updated or index in used or (negative_shared == 0 and index in claimed):
                continue
            track = self._tracks[key]
            dt = sample_at - track.observed_at
            alpha = 1 - math.exp(-dt / self.SMOOTH_SECONDS)
            track.position += alpha * (observations[index] - track.position)
            track.target = targets[index]
            track.observed = observations[index]
            track.observed_at = sample_at
            track.hits += 1
            updated.add(key)
            used.add(index)

        for index, (target, point) in enumerate(zip(targets, observations)):
            if index in used or index in claimed:
                continue
            self._tracks[self._next_id] = _Track(target, point.copy(), point, sample_at)
            self._next_id += 1

    def _display(self, key: int, track: _Track, sample_at: float, now: float) -> dict[str, Any]:
        x, y, z = track.position
        name = f"drone{key}"
        fresh = track.observed_at == sample_at and now - track.observed_at <= self.FRESH_SECONDS
        return {
            **track.target,
            "id": name,
            "label": name,
            "x": round(float(x), 4),
            "y": round(float(y), 4),
            "z": round(float(z), 4),
            "rangeMeters": round(math.hypot(x, y, z), 4),
            "azimuthDeg": round(math.degrees(math.atan2(x, z)), 3),
            "elevationDeg": round(math.degrees(math.atan2(y, math.hypot(x, z))), 3),
            "trackingState": "measured" if fresh else "held",
        }
