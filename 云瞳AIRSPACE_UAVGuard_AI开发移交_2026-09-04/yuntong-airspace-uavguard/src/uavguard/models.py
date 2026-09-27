from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import numpy as np


class ObjectClass(StrEnum):
    MULTIROTOR = "multirotor"
    FIXED_WING_UAV = "fixed_wing_uav"
    BIRD = "bird"
    UNKNOWN = "unknown"


class TrackState(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    LOST = "lost"


@dataclass(slots=True)
class Detection2D:
    camera_id: str
    timestamp_s: float
    bbox_xyxy: tuple[float, float, float, float]
    score: float = 1.0
    object_class: ObjectClass = ObjectClass.UNKNOWN
    class_confidence: float = 0.0
    track_id: str | None = None
    velocity_px_s: tuple[float, float] = (0.0, 0.0)

    @property
    def center(self) -> np.ndarray:
        x1, y1, x2, y2 = self.bbox_xyxy
        return np.array([(x1 + x2) * 0.5, (y1 + y2) * 0.5], dtype=np.float64)

    @property
    def size(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox_xyxy
        return x2 - x1, y2 - y1


@dataclass(slots=True)
class StereoMeasurement:
    timestamp_s: float
    position_enu_m: np.ndarray
    covariance_m2: np.ndarray
    reprojection_error_px: float
    crossing_angle_deg: float
    camera_ids: tuple[str, str]
    object_class: ObjectClass = ObjectClass.UNKNOWN
    class_confidence: float = 0.0
    source_track_ids: tuple[str | None, str | None] = (None, None)


@dataclass(slots=True)
class TrackEvent:
    track_id: str
    timestamp_s: float
    state: TrackState
    object_class: ObjectClass
    class_confidence: float
    position_enu_m: np.ndarray
    velocity_enu_mps: np.ndarray
    covariance_m2: np.ndarray
    reprojection_error_px: float
    camera_ids: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        std = np.sqrt(np.maximum(np.diag(self.covariance_m2), 0.0))
        return {
            "trackId": self.track_id,
            "timestampUtc": datetime.fromtimestamp(
                self.timestamp_s, tz=timezone.utc
            ).isoformat(),
            "state": self.state.value,
            "class": self.object_class.value,
            "classConfidence": round(float(self.class_confidence), 4),
            "positionEnuM": [round(float(v), 3) for v in self.position_enu_m],
            "velocityEnuMps": [round(float(v), 3) for v in self.velocity_enu_mps],
            "positionStdM": [round(float(v), 3) for v in std],
            "reprojectionErrorPx": round(float(self.reprojection_error_px), 3),
            "cameraIds": list(self.camera_ids),
            "metadata": self.metadata,
        }

