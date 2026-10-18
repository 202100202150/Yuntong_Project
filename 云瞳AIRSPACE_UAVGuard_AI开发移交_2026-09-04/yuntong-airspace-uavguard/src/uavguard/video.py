from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .calibration import CameraCalibration
from .config import CameraConfig


@dataclass(slots=True)
class FramePacket:
    camera_id: str
    frame_id: int
    timestamp_s: float
    image: np.ndarray


class VideoSource:
    def read(self) -> FramePacket | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class OpenCvVideoSource(VideoSource):
    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self.frame_id = 0
        self.started_epoch_s = time.time()
        if config.gstreamer_pipeline:
            self.capture = cv2.VideoCapture(config.gstreamer_pipeline, cv2.CAP_GSTREAMER)
        else:
            source: str | int = config.source
            if config.source_type == "device" or config.source.isdigit():
                source = int(config.source)
            self.capture = cv2.VideoCapture(source)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        if not self.capture.isOpened():
            raise RuntimeError(f"Unable to open video source for {config.camera_id}")

    def read(self) -> FramePacket | None:
        ok, image = self.capture.read()
        if not ok:
            return None
        self.frame_id += 1
        if self.config.source_type == "file":
            position_ms = self.capture.get(cv2.CAP_PROP_POS_MSEC)
            timestamp_s = self.started_epoch_s + position_ms / 1000.0
        else:
            # Production deployments should provide a GStreamer pipeline that maps
            # RTCP sender reports to PTS. Arrival time is an explicit fallback.
            timestamp_s = time.time()
        timestamp_s += self.config.timestamp_offset_ms / 1000.0
        return FramePacket(self.config.camera_id, self.frame_id, timestamp_s, image)

    def close(self) -> None:
        self.capture.release()


class SyntheticVideoSource(VideoSource):
    """Deterministic sky scene used for installation and CI smoke tests."""

    def __init__(
        self,
        config: CameraConfig,
        calibration: CameraCalibration,
        image_size: tuple[int, int] = (640, 360),
    ) -> None:
        self.config = config
        self.calibration = calibration
        self.width, self.height = image_size
        self.frame_id = 0
        self.last_bucket = -1

    def _world_position(self, timestamp_s: float) -> np.ndarray:
        phase = timestamp_s % 20.0
        return np.array(
            [
                -25.0 + 2.5 * phase,
                150.0 + 12.0 * np.sin(phase * 0.4),
                65.0 + 8.0 * np.sin(phase * 0.7),
            ],
            dtype=np.float64,
        )

    def read(self) -> FramePacket | None:
        fps = max(self.config.fps, 1.0)
        while True:
            now = time.time()
            bucket = int(now * fps)
            if bucket != self.last_bucket:
                break
            time.sleep(min(0.002, 0.5 / fps))
        self.last_bucket = bucket
        timestamp_s = bucket / fps + self.config.timestamp_offset_ms / 1000.0
        self.frame_id += 1

        y_gradient = np.linspace(0, 35, self.height, dtype=np.uint8)[:, None]
        image = np.empty((self.height, self.width, 3), dtype=np.uint8)
        image[:, :, 0] = 210 - y_gradient
        image[:, :, 1] = 170 - y_gradient // 2
        image[:, :, 2] = 105
        cv2.ellipse(image, (130, 90), (70, 18), 0, 0, 360, (225, 220, 205), -1)
        cv2.ellipse(image, (500, 160), (85, 22), 0, 0, 360, (220, 215, 200), -1)
        point = self.calibration.project(self._world_position(timestamp_s))
        x, y = int(round(point[0])), int(round(point[1]))
        if 8 <= x < self.width - 8 and 8 <= y < self.height - 8:
            cv2.line(image, (x - 6, y), (x + 6, y), (20, 20, 20), 3)
            cv2.line(image, (x, y - 4), (x, y + 4), (20, 20, 20), 2)
        return FramePacket(self.config.camera_id, self.frame_id, timestamp_s, image)


def create_video_source(
    config: CameraConfig, calibration: CameraCalibration
) -> VideoSource:
    if config.source_type == "synthetic" or config.source == "synthetic":
        return SyntheticVideoSource(config, calibration, calibration.image_size)
    return OpenCvVideoSource(config)
