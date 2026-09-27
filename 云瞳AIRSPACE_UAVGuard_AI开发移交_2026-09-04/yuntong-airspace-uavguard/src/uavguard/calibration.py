from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml


@dataclass(slots=True)
class CameraCalibration:
    camera_id: str
    image_size: tuple[int, int]
    camera_matrix: np.ndarray
    distortion: np.ndarray
    rotation_world_to_camera: np.ndarray
    translation_world_to_camera_m: np.ndarray
    valid: bool
    rms_px: float | None = None
    version: str = "unknown"

    @property
    def projection_matrix(self) -> np.ndarray:
        extrinsic = np.hstack(
            [self.rotation_world_to_camera, self.translation_world_to_camera_m.reshape(3, 1)]
        )
        return self.camera_matrix @ extrinsic

    @property
    def camera_center_enu_m(self) -> np.ndarray:
        return -self.rotation_world_to_camera.T @ self.translation_world_to_camera_m

    def depth(self, point_enu_m: np.ndarray) -> float:
        camera_point = (
            self.rotation_world_to_camera @ point_enu_m
            + self.translation_world_to_camera_m
        )
        return float(camera_point[2])

    def project(self, point_enu_m: np.ndarray) -> np.ndarray:
        homogeneous = self.projection_matrix @ np.append(point_enu_m, 1.0)
        if abs(homogeneous[2]) < 1e-12:
            raise ValueError("Point projects to zero depth")
        return homogeneous[:2] / homogeneous[2]


def load_calibration(path: str | Path, require_valid: bool = True) -> CameraCalibration:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    calibration = CameraCalibration(
        camera_id=str(raw["camera_id"]),
        image_size=tuple(int(v) for v in raw["image_size"]),
        camera_matrix=np.asarray(raw["camera_matrix"], dtype=np.float64).reshape(3, 3),
        distortion=np.asarray(raw.get("distortion", []), dtype=np.float64),
        rotation_world_to_camera=np.asarray(
            raw["rotation_world_to_camera"], dtype=np.float64
        ).reshape(3, 3),
        translation_world_to_camera_m=np.asarray(
            raw["translation_world_to_camera_m"], dtype=np.float64
        ).reshape(3),
        valid=bool(raw.get("valid", False)),
        rms_px=float(raw["rms_px"]) if raw.get("rms_px") is not None else None,
        version=str(raw.get("version", "unknown")),
    )
    if require_valid and not calibration.valid:
        raise ValueError(f"Calibration is not approved for runtime use: {path}")
    if not np.allclose(
        calibration.rotation_world_to_camera
        @ calibration.rotation_world_to_camera.T,
        np.eye(3),
        atol=1e-3,
    ):
        raise ValueError(f"Rotation matrix is not orthonormal: {path}")
    return calibration

