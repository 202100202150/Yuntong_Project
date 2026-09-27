from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from itertools import count

import cv2
import numpy as np

from .config import MotionConfig
from .models import Detection2D


class FrameStabilizer:
    """Compensate small mount vibration with phase-correlation translation."""

    def __init__(self, enabled: bool = True, scale: float = 0.25) -> None:
        self.enabled = enabled
        self.scale = scale
        self.reference: np.ndarray | None = None

    def apply(self, frame: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, None, fx=self.scale, fy=self.scale)
        small = small.astype(np.float32)
        if self.reference is None:
            self.reference = small
            return frame
        shift, response = cv2.phaseCorrelate(self.reference, small)
        dx, dy = shift[0] / self.scale, shift[1] / self.scale
        if response < 0.05 or abs(dx) > 20 or abs(dy) > 20:
            return frame
        transform = np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], dtype=np.float32)
        return cv2.warpAffine(
            frame,
            transform,
            (frame.shape[1], frame.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )


class MotionDetector:
    def __init__(
        self,
        camera_id: str,
        config: MotionConfig,
        mask_polygons: list[list[list[float]]] | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.config = config
        self.mask_polygons = mask_polygons or []
        self.background = cv2.createBackgroundSubtractorMOG2(
            history=config.history,
            varThreshold=config.var_threshold,
            detectShadows=False,
        )
        self.previous: deque[np.ndarray] = deque(maxlen=2)
        self.stabilizer = FrameStabilizer(enabled=config.stabilize)
        self._mask_cache: tuple[tuple[int, int], np.ndarray] | None = None

    def _mask(self, shape: tuple[int, int]) -> np.ndarray:
        if self._mask_cache is not None and self._mask_cache[0] == shape:
            return self._mask_cache[1]
        height, width = shape
        if not self.mask_polygons:
            mask = np.full((height, width), 255, dtype=np.uint8)
        else:
            mask = np.zeros((height, width), dtype=np.uint8)
            for polygon in self.mask_polygons:
                points = np.array(
                    [
                        [
                            int(np.clip(point[0], 0.0, 1.0) * (width - 1)),
                            int(np.clip(point[1], 0.0, 1.0) * (height - 1)),
                        ]
                        for point in polygon
                    ],
                    dtype=np.int32,
                )
                cv2.fillPoly(mask, [points], 255)
        self._mask_cache = (shape, mask)
        return mask

    @staticmethod
    def _merge_detections(
        detections: list[Detection2D], gap_px: float = 12.0
    ) -> list[Detection2D]:
        """Merge leading/trailing edges created by temporal differencing."""
        pending = detections[:]
        merged: list[Detection2D] = []
        while pending:
            seed = pending.pop()
            x1, y1, x2, y2 = seed.bbox_xyxy
            score = seed.score
            changed = True
            while changed:
                changed = False
                keep: list[Detection2D] = []
                for item in pending:
                    ix1, iy1, ix2, iy2 = item.bbox_xyxy
                    horizontal_gap = max(ix1 - x2, x1 - ix2, 0.0)
                    vertical_gap = max(iy1 - y2, y1 - iy2, 0.0)
                    if horizontal_gap <= gap_px and vertical_gap <= gap_px:
                        x1, y1 = min(x1, ix1), min(y1, iy1)
                        x2, y2 = max(x2, ix2), max(y2, iy2)
                        score = max(score, item.score)
                        changed = True
                    else:
                        keep.append(item)
                pending = keep
            merged.append(
                Detection2D(
                    camera_id=seed.camera_id,
                    timestamp_s=seed.timestamp_s,
                    bbox_xyxy=(x1, y1, x2, y2),
                    score=score,
                )
            )
        return merged

    def detect(self, frame: np.ndarray, timestamp_s: float) -> tuple[np.ndarray, list[Detection2D]]:
        stabilized = self.stabilizer.apply(frame)
        gray = cv2.cvtColor(stabilized, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        foreground = self.background.apply(gray, learningRate=self.config.learning_rate)

        temporal = np.zeros_like(gray)
        if len(self.previous) == 2:
            first = cv2.absdiff(self.previous[0], self.previous[1])
            second = cv2.absdiff(self.previous[1], gray)
            _, first = cv2.threshold(
                first, self.config.diff_threshold, 255, cv2.THRESH_BINARY
            )
            _, second = cv2.threshold(
                second, self.config.diff_threshold, 255, cv2.THRESH_BINARY
            )
            temporal = cv2.bitwise_and(first, second)
        self.previous.append(gray)

        _, foreground = cv2.threshold(foreground, 200, 255, cv2.THRESH_BINARY)
        temporal_neighborhood = cv2.dilate(
            temporal, np.ones((3, 3), dtype=np.uint8), iterations=1
        )
        combined = cv2.bitwise_and(foreground, temporal_neighborhood)
        if not np.any(combined) and len(self.previous) < 2:
            combined = foreground
        combined = cv2.bitwise_and(combined, self._mask(gray.shape))
        kernel = np.ones((3, 3), dtype=np.uint8)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel)
        combined = cv2.dilate(combined, kernel, iterations=1)

        count_labels, _, stats, _ = cv2.connectedComponentsWithStats(combined)
        detections: list[Detection2D] = []
        for index in range(1, count_labels):
            x, y, width, height, area = stats[index]
            if area < self.config.min_area_px:
                continue
            if max(width, height) > self.config.max_dimension_px:
                continue
            padding = 2
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = min(gray.shape[1] - 1, x + width + padding)
            y2 = min(gray.shape[0] - 1, y + height + padding)
            roi = gray[y1 : y2 + 1, x1 : x2 + 1]
            contrast = float(np.std(roi)) / 32.0 if roi.size else 0.0
            score = float(np.clip(0.3 + contrast, 0.0, 1.0))
            detections.append(
                Detection2D(
                    camera_id=self.camera_id,
                    timestamp_s=timestamp_s,
                    bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                    score=score,
                )
            )
        return stabilized, self._merge_detections(detections)


@dataclass(slots=True)
class _Track2D:
    track_id: str
    state: np.ndarray
    covariance: np.ndarray
    last_timestamp_s: float
    hit_history: deque[bool] = field(default_factory=lambda: deque(maxlen=5))
    missed_s: float = 0.0

    def predict(self, timestamp_s: float) -> None:
        dt = max(0.0, min(timestamp_s - self.last_timestamp_s, 0.5))
        transition = np.array(
            [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]],
            dtype=np.float64,
        )
        process = np.diag([2.0, 2.0, 20.0, 20.0]) * max(dt, 0.04)
        self.state = transition @ self.state
        self.covariance = transition @ self.covariance @ transition.T + process
        self.missed_s += dt
        self.last_timestamp_s = timestamp_s

    def update(self, center: np.ndarray) -> None:
        observation = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
        noise = np.eye(2) * 4.0
        innovation = center - observation @ self.state
        innovation_covariance = observation @ self.covariance @ observation.T + noise
        gain = (
            self.covariance
            @ observation.T
            @ np.linalg.inv(innovation_covariance)
        )
        self.state += gain @ innovation
        self.covariance = (np.eye(4) - gain @ observation) @ self.covariance
        self.missed_s = 0.0


class Tracker2D:
    def __init__(
        self,
        camera_id: str,
        confirm_hits: int = 3,
        confirm_window: int = 5,
        maximum_distance_px: float = 100.0,
        maximum_missed_s: float = 0.8,
    ) -> None:
        self.camera_id = camera_id
        self.confirm_hits = confirm_hits
        self.confirm_window = confirm_window
        self.maximum_distance_px = maximum_distance_px
        self.maximum_missed_s = maximum_missed_s
        self._tracks: dict[str, _Track2D] = {}
        self._ids = count(1)

    def update(
        self, detections: list[Detection2D], timestamp_s: float
    ) -> list[Detection2D]:
        for track in self._tracks.values():
            track.hit_history.append(False)
            track.predict(timestamp_s)

        candidates: list[tuple[float, str, int]] = []
        for track_id, track in self._tracks.items():
            predicted = track.state[:2]
            for detection_index, detection in enumerate(detections):
                distance = float(np.linalg.norm(predicted - detection.center))
                if distance <= self.maximum_distance_px:
                    candidates.append((distance, track_id, detection_index))

        used_tracks: set[str] = set()
        used_detections: set[int] = set()
        assigned: dict[int, _Track2D] = {}
        for _, track_id, detection_index in sorted(candidates):
            if track_id in used_tracks or detection_index in used_detections:
                continue
            track = self._tracks[track_id]
            track.update(detections[detection_index].center)
            track.hit_history[-1] = True
            used_tracks.add(track_id)
            used_detections.add(detection_index)
            assigned[detection_index] = track

        for detection_index, detection in enumerate(detections):
            if detection_index in used_detections:
                continue
            track_id = f"{self.camera_id}-{next(self._ids):06d}"
            track = _Track2D(
                track_id=track_id,
                state=np.array([*detection.center, 0.0, 0.0], dtype=np.float64),
                covariance=np.diag([9.0, 9.0, 100.0, 100.0]),
                last_timestamp_s=timestamp_s,
            )
            track.hit_history = deque([True], maxlen=self.confirm_window)
            self._tracks[track_id] = track
            assigned[detection_index] = track

        expired = [
            track_id
            for track_id, track in self._tracks.items()
            if track.missed_s > self.maximum_missed_s
        ]
        for track_id in expired:
            del self._tracks[track_id]

        confirmed: list[Detection2D] = []
        for detection_index, track in assigned.items():
            detection = detections[detection_index]
            detection.track_id = track.track_id
            detection.velocity_px_s = (float(track.state[2]), float(track.state[3]))
            if sum(track.hit_history) >= self.confirm_hits:
                confirmed.append(detection)
        return confirmed
