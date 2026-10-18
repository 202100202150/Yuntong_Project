from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np


LOGGER = logging.getLogger(__name__)


class SegmentRecorder:
    """Optional decoded-frame recorder for the MVP.

    Production sites should prefer camera-side/NVR recording or a GStreamer
    remux pipeline so the 4K stream is not decoded and encoded a second time.
    """

    def __init__(
        self,
        root: Path,
        segment_seconds: int,
        retention_days: int,
        target_fps: float = 10.0,
        maximum_width: int = 1280,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.segment_seconds = segment_seconds
        self.retention_days = retention_days
        self.target_fps = target_fps
        self.maximum_width = maximum_width
        self._writers: dict[str, cv2.VideoWriter] = {}
        self._started: dict[str, float] = {}
        self._paths: dict[str, Path] = {}
        self._lock = threading.RLock()
        self._last_write_s: dict[str, float] = {}

    def _open(
        self, camera_id: str, timestamp_s: float, frame: np.ndarray, fps: float
    ) -> cv2.VideoWriter | None:
        camera_root = self.root / camera_id
        camera_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(timestamp_s))
        path = camera_root / f"{camera_id}-{stamp}.mp4"
        height, width = frame.shape[:2]
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            max(min(fps, self.target_fps), 1.0),
            (width, height),
        )
        if not writer.isOpened():
            LOGGER.error("Unable to open recording writer for %s", camera_id)
            writer.release()
            return None
        self._writers[camera_id] = writer
        self._started[camera_id] = timestamp_s
        self._paths[camera_id] = path
        return writer

    def write(
        self, camera_id: str, timestamp_s: float, frame: np.ndarray, fps: float
    ) -> None:
        with self._lock:
            last_write = self._last_write_s.get(camera_id, float("-inf"))
            if timestamp_s - last_write < 1.0 / max(self.target_fps, 1.0):
                return
            self._last_write_s[camera_id] = timestamp_s
            if frame.shape[1] > self.maximum_width:
                scale = self.maximum_width / frame.shape[1]
                frame = cv2.resize(
                    frame,
                    (self.maximum_width, int(round(frame.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            writer = self._writers.get(camera_id)
            started = self._started.get(camera_id, 0.0)
            if writer is None or timestamp_s - started >= self.segment_seconds:
                if writer is not None:
                    writer.release()
                writer = self._open(camera_id, timestamp_s, frame, fps)
            if writer is not None:
                writer.write(frame)

    def current_paths(self) -> dict[str, str]:
        with self._lock:
            return {
                camera_id: str(path.relative_to(self.root)).replace("\\", "/")
                for camera_id, path in self._paths.items()
            }

    def cleanup(self, now_s: float | None = None) -> int:
        cutoff = (now_s or time.time()) - self.retention_days * 86400
        removed = 0
        for path in self.root.rglob("*.mp4"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def close(self) -> None:
        with self._lock:
            for writer in self._writers.values():
                writer.release()
            self._writers.clear()
