from pathlib import Path

import numpy as np

from uavguard.calibration import load_calibration
from uavguard.config import StereoConfig
from uavguard.geometry import StereoAssociator
from uavguard.models import Detection2D


ROOT = Path(__file__).resolve().parents[1]


def detection(camera_id: str, timestamp: float, point: np.ndarray) -> Detection2D:
    x, y = point
    return Detection2D(camera_id, timestamp, (x - 3, y - 3, x + 3, y + 3))


def test_synthetic_triangulation_recovers_world_point() -> None:
    first = load_calibration(ROOT / "calibration/demo-cam01.yaml")
    second = load_calibration(ROOT / "calibration/demo-cam02.yaml")
    associator = StereoAssociator(first, second, StereoConfig(sync_tolerance_ms=20))
    expected = np.array([4.0, 175.0, 72.0])
    measurements = associator.associate(
        [detection("cam01", 1000.0, first.project(expected))],
        [detection("cam02", 1000.01, second.project(expected))],
    )
    assert len(measurements) == 1
    assert np.linalg.norm(measurements[0].position_enu_m - expected) < 0.05
    assert measurements[0].reprojection_error_px < 0.01
    assert measurements[0].crossing_angle_deg >= 3.0


def test_stereo_rejects_unsynchronized_pair() -> None:
    first = load_calibration(ROOT / "calibration/demo-cam01.yaml")
    second = load_calibration(ROOT / "calibration/demo-cam02.yaml")
    associator = StereoAssociator(first, second, StereoConfig(sync_tolerance_ms=20))
    point = np.array([0.0, 160.0, 65.0])
    measurements = associator.associate(
        [detection("cam01", 1000.0, first.project(point))],
        [detection("cam02", 1000.05, second.project(point))],
    )
    assert measurements == []

