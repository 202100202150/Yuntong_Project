from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np


class FrameHub:
    def __init__(self) -> None:
        self._frames: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def put(self, camera_id: str, frame: np.ndarray, quality: int = 75) -> None:
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return
        with self._lock:
            self._frames[camera_id] = encoded.tobytes()

    def get(self, camera_id: str) -> bytes | None:
        with self._lock:
            return self._frames.get(camera_id)


class TrackBroker:
    def __init__(self) -> None:
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.RLock()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.add((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = {
                item for item in self._subscribers if item[1] is not queue
            }

    @staticmethod
    def _offer(queue: asyncio.Queue, payload: dict) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(payload)

    def publish(self, payload: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, payload)
            except RuntimeError:
                self.unsubscribe(queue)


@dataclass(slots=True)
class CameraHealth:
    camera_id: str
    status: str = "starting"
    frames: int = 0
    detections: int = 0
    started_monotonic_s: float = field(default_factory=time.monotonic)
    last_frame_epoch_s: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict:
        elapsed = max(time.monotonic() - self.started_monotonic_s, 1e-6)
        age = (
            time.time() - self.last_frame_epoch_s
            if self.last_frame_epoch_s is not None
            else None
        )
        return {
            "cameraId": self.camera_id,
            "status": self.status,
            "frames": self.frames,
            "averageFps": round(self.frames / elapsed, 2),
            "detections": self.detections,
            "lastFrameAgeS": round(age, 3) if age is not None else None,
            "lastError": self.last_error,
        }

