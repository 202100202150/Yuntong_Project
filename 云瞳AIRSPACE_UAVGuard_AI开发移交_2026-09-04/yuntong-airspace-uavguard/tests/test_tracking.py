import numpy as np

from uavguard.config import TrackingConfig
from uavguard.models import Detection2D, ObjectClass, StereoMeasurement, TrackState
from uavguard.motion import Tracker2D
from uavguard.tracking3d import Tracker3D


def test_2d_track_requires_three_hits() -> None:
    tracker = Tracker2D("cam01")
    confirmed = []
    for index in range(3):
        detection = Detection2D(
            "cam01",
            index * 0.04,
            (100 + index, 100, 106 + index, 106),
        )
        confirmed = tracker.update([detection], index * 0.04)
    assert len(confirmed) == 1
    assert confirmed[0].track_id.startswith("cam01-")


def measurement(timestamp: float, x: float) -> StereoMeasurement:
    return StereoMeasurement(
        timestamp_s=timestamp,
        position_enu_m=np.array([x, 160.0, 65.0]),
        covariance_m2=np.eye(3),
        reprojection_error_px=0.5,
        crossing_angle_deg=10.0,
        camera_ids=("cam01", "cam02"),
        object_class=ObjectClass.UNKNOWN,
    )


def test_3d_track_requires_five_of_eight_hits() -> None:
    tracker = Tracker3D(TrackingConfig(confirm_hits=5, confirm_window=8))
    events = []
    for index in range(5):
        timestamp = 1000.0 + index * 0.04
        events = tracker.update([measurement(timestamp, index * 0.2)], timestamp)
    assert len(events) == 1
    assert events[0].state == TrackState.CONFIRMED
    assert events[0].metadata["justConfirmed"] is True
    assert events[0].class_confidence == 0.0

