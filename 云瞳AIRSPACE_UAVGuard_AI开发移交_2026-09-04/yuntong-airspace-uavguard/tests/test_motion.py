import cv2
import numpy as np

from uavguard.config import MotionConfig
from uavguard.motion import MotionDetector


def test_three_frame_motion_detector_finds_small_target() -> None:
    detector = MotionDetector(
        "cam01",
        MotionConfig(
            history=20,
            diff_threshold=3,
            min_area_px=2,
            max_dimension_px=30,
            learning_rate=0.01,
            stabilize=False,
        ),
    )
    found = []
    for index in range(8):
        frame = np.full((180, 320, 3), (210, 170, 100), dtype=np.uint8)
        cv2.circle(frame, (80 + index * 3, 90), 3, (10, 10, 10), -1)
        _, detections = detector.detect(frame, index * 0.04)
        found.extend(detections)
    assert found
    assert all(max(item.size) <= 30 for item in found)

