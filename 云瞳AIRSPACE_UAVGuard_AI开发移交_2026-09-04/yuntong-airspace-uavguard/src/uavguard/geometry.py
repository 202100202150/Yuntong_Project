from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import cv2

from .calibration import CameraCalibration
from .config import StereoConfig
from .models import Detection2D, ObjectClass, StereoMeasurement


def _skew(vector: np.ndarray) -> np.ndarray:
    x, y, z = vector
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def fundamental_matrix(
    first: CameraCalibration, second: CameraCalibration
) -> np.ndarray:
    relative_rotation = (
        second.rotation_world_to_camera @ first.rotation_world_to_camera.T
    )
    relative_translation = (
        second.translation_world_to_camera_m
        - relative_rotation @ first.translation_world_to_camera_m
    )
    essential = _skew(relative_translation) @ relative_rotation
    return (
        np.linalg.inv(second.camera_matrix).T
        @ essential
        @ np.linalg.inv(first.camera_matrix)
    )


def symmetric_epipolar_distance_px(
    first_point: np.ndarray, second_point: np.ndarray, matrix: np.ndarray
) -> float:
    p1 = np.array([first_point[0], first_point[1], 1.0])
    p2 = np.array([second_point[0], second_point[1], 1.0])
    line2 = matrix @ p1
    line1 = matrix.T @ p2
    numerator = abs(float(p2 @ matrix @ p1))
    d1 = numerator / max(np.linalg.norm(line1[:2]), 1e-12)
    d2 = numerator / max(np.linalg.norm(line2[:2]), 1e-12)
    return float((d1 + d2) * 0.5)


def triangulate_dlt(
    first_point: np.ndarray,
    second_point: np.ndarray,
    first_projection: np.ndarray,
    second_projection: np.ndarray,
) -> np.ndarray:
    x1, y1 = first_point
    x2, y2 = second_point
    design = np.vstack(
        [
            x1 * first_projection[2] - first_projection[0],
            y1 * first_projection[2] - first_projection[1],
            x2 * second_projection[2] - second_projection[0],
            y2 * second_projection[2] - second_projection[1],
        ]
    )
    _, _, vh = np.linalg.svd(design)
    homogeneous = vh[-1]
    if abs(homogeneous[3]) < 1e-12:
        raise ValueError("Triangulation produced a point at infinity")
    return homogeneous[:3] / homogeneous[3]


def _residuals(
    point: np.ndarray,
    observations: tuple[np.ndarray, np.ndarray],
    calibrations: tuple[CameraCalibration, CameraCalibration],
) -> np.ndarray:
    return np.concatenate(
        [cal.project(point) - obs for cal, obs in zip(calibrations, observations)]
    )


def refine_point_gauss_newton(
    point: np.ndarray,
    observations: tuple[np.ndarray, np.ndarray],
    calibrations: tuple[CameraCalibration, CameraCalibration],
    iterations: int = 8,
) -> tuple[np.ndarray, np.ndarray, float]:
    estimate = np.asarray(point, dtype=np.float64).copy()
    jacobian = np.zeros((4, 3), dtype=np.float64)
    for _ in range(iterations):
        residual = _residuals(estimate, observations, calibrations)
        epsilon = max(1e-4, np.linalg.norm(estimate) * 1e-7)
        for axis in range(3):
            step = np.zeros(3)
            step[axis] = epsilon
            jacobian[:, axis] = (
                _residuals(estimate + step, observations, calibrations)
                - _residuals(estimate - step, observations, calibrations)
            ) / (2.0 * epsilon)
        delta, *_ = np.linalg.lstsq(jacobian, -residual, rcond=None)
        estimate += delta
        if np.linalg.norm(delta) < 1e-5:
            break
    residual = _residuals(estimate, observations, calibrations)
    rms = float(np.sqrt(np.mean(residual**2)))
    return estimate, jacobian, rms


def crossing_angle_deg(
    point: np.ndarray, first: CameraCalibration, second: CameraCalibration
) -> float:
    first_ray = point - first.camera_center_enu_m
    second_ray = point - second.camera_center_enu_m
    first_ray /= np.linalg.norm(first_ray)
    second_ray /= np.linalg.norm(second_ray)
    cosine = np.clip(float(first_ray @ second_ray), -1.0, 1.0)
    return float(np.degrees(np.arccos(cosine)))


def _classes_compatible(first: ObjectClass, second: ObjectClass) -> bool:
    return (
        first == ObjectClass.UNKNOWN
        or second == ObjectClass.UNKNOWN
        or first == second
    )


@dataclass(slots=True)
class _CandidatePair:
    cost: float
    first_index: int
    second_index: int
    measurement: StereoMeasurement


class StereoAssociator:
    def __init__(
        self,
        first: CameraCalibration,
        second: CameraCalibration,
        config: StereoConfig,
    ) -> None:
        self.first = first
        self.second = second
        self.config = config
        self.fundamental = fundamental_matrix(first, second)

    @staticmethod
    def _undistort(point: np.ndarray, calibration: CameraCalibration) -> np.ndarray:
        if calibration.distortion.size == 0 or np.allclose(
            calibration.distortion, 0.0
        ):
            return point
        corrected = cv2.undistortPoints(
            point.reshape(1, 1, 2),
            calibration.camera_matrix,
            calibration.distortion,
            P=calibration.camera_matrix,
        )
        return corrected.reshape(2)

    def _measurement(
        self, first_detection: Detection2D, second_detection: Detection2D
    ) -> StereoMeasurement | None:
        if not _classes_compatible(
            first_detection.object_class, second_detection.object_class
        ):
            return None
        time_delta_ms = abs(
            first_detection.timestamp_s - second_detection.timestamp_s
        ) * 1000.0
        if time_delta_ms > self.config.sync_tolerance_ms:
            return None

        first_point = self._undistort(first_detection.center, self.first)
        second_point = self._undistort(second_detection.center, self.second)
        epipolar = symmetric_epipolar_distance_px(
            first_point, second_point, self.fundamental
        )
        if epipolar > self.config.epipolar_threshold_px:
            return None
        try:
            point = triangulate_dlt(
                first_point,
                second_point,
                self.first.projection_matrix,
                self.second.projection_matrix,
            )
            point, jacobian, reprojection = refine_point_gauss_newton(
                point,
                (first_point, second_point),
                (self.first, self.second),
            )
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            return None

        if self.first.depth(point) <= 0 or self.second.depth(point) <= 0:
            return None
        first_range = np.linalg.norm(point - self.first.camera_center_enu_m)
        second_range = np.linalg.norm(point - self.second.camera_center_enu_m)
        if not (
            self.config.minimum_range_m <= first_range <= self.config.maximum_range_m
            and self.config.minimum_range_m
            <= second_range
            <= self.config.maximum_range_m
        ):
            return None
        angle = crossing_angle_deg(point, self.first, self.second)
        if (
            angle < self.config.minimum_crossing_angle_deg
            or reprojection > self.config.reprojection_threshold_px
        ):
            return None

        information = jacobian.T @ jacobian / (self.config.pixel_sigma**2)
        covariance = np.linalg.pinv(information)
        if first_detection.class_confidence >= second_detection.class_confidence:
            object_class = first_detection.object_class
            confidence = first_detection.class_confidence
        else:
            object_class = second_detection.object_class
            confidence = second_detection.class_confidence
        return StereoMeasurement(
            timestamp_s=(
                first_detection.timestamp_s + second_detection.timestamp_s
            )
            * 0.5,
            position_enu_m=point,
            covariance_m2=covariance,
            reprojection_error_px=reprojection,
            crossing_angle_deg=angle,
            camera_ids=(self.first.camera_id, self.second.camera_id),
            object_class=object_class,
            class_confidence=confidence,
            source_track_ids=(first_detection.track_id, second_detection.track_id),
        )

    def associate(
        self,
        first_detections: list[Detection2D],
        second_detections: list[Detection2D],
    ) -> list[StereoMeasurement]:
        candidates: list[_CandidatePair] = []
        for (first_index, first_detection), (second_index, second_detection) in product(
            enumerate(first_detections), enumerate(second_detections)
        ):
            measurement = self._measurement(first_detection, second_detection)
            if measurement is None:
                continue
            cost = measurement.reprojection_error_px + 0.02 * abs(
                first_detection.timestamp_s - second_detection.timestamp_s
            ) * 1000.0
            candidates.append(
                _CandidatePair(cost, first_index, second_index, measurement)
            )
        used_first: set[int] = set()
        used_second: set[int] = set()
        output: list[StereoMeasurement] = []
        for candidate in sorted(candidates, key=lambda item: item.cost):
            if (
                candidate.first_index in used_first
                or candidate.second_index in used_second
            ):
                continue
            used_first.add(candidate.first_index)
            used_second.add(candidate.second_index)
            output.append(candidate.measurement)
        return output
