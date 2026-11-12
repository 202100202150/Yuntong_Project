#!/usr/bin/env python3
"""Authenticated loopback bridge for the dual-camera Electron UI.

The bridge owns the RTSP connections and the only disk-writing operations
(explicit recording and explicit snapshot requests). Preview JPEGs are encoded
in memory, cached and rate-limited. Paths are configuration-only: the HTTP API
never accepts a filesystem path from a client.
"""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Collection
from dataclasses import dataclass, replace
from fractions import Fraction
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import os
from pathlib import Path
import re
import struct
import sys
import threading
import time
import queue
from typing import Any, Callable, Protocol
from urllib.parse import quote, urlsplit
from uuid import uuid4
import zlib
import traceback

import av
import cv2
import numpy as np
from dotenv import load_dotenv

from camera_low_latency import (
    CameraSource,
    FramePacket,
    LatestFrameBuffer,
    OutputConfig,
    PacketRecorder,
    RecordingStatus,
    SnapshotWriter,
    StatusStore,
    StreamWorker,
    build_sources_and_outputs,
    parse_args as parse_camera_args,
    safe_file_component,
    timestamp_for_filename,
    validate_args as validate_camera_args,
)
from drone_tracking import DronePositionTracker
from spatial_localization import (
    SpatialLocalizer, default_config, derive_extrinsic_config, spatial_frame_skew_seconds, validate_config,
)
from tensorrt_drone_detector import TensorRTDroneDetector

LOG = logging.getLogger("camera-bridge")
LOOPBACK_HOST = "127.0.0.1"
CAMERA_KEYS = ("camera1", "camera2")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_DIMENSION = 16_384
MAX_PNG_PIXELS = 100_000_000
ACTIVE_RECORDING_STATES = frozenset({"armed", "starting", "recording", "stopping"})
# Software pairing uses receiver time corrected for measured RTSP backlog.
PREVIEW_PAIR_MAX_SKEW = 0.080
PREVIEW_PAIR_MAX_AGE = 0.300
SPATIAL_UPDATE_INTERVAL = 0.08


class BridgeApiError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class SecretRedactionFilter(logging.Filter):
    """Prevent credentials from appearing in sidecar stderr logs."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        encoded = [quote(secret, safe="") for secret in secrets if secret]
        self._secrets = sorted({*filter(None, secrets), *encoded}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "***")
        record.msg = message
        record.args = ()
        return True


def validate_png(data: bytes) -> tuple[int, int]:
    """Strictly validate a bounded PNG container and return its dimensions."""
    if len(data) < 45 or not data.startswith(PNG_SIGNATURE):
        raise ValueError("body is not a PNG image")
    offset = len(PNG_SIGNATURE)
    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    width = height = 0
    chunk_index = 0
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("PNG contains a truncated chunk")
        length = struct.unpack(">I", data[offset: offset + 4])[0]
        chunk_type = data[offset + 4: offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("PNG chunk length exceeds the request body")
        chunk_data = data[offset + 8: offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length: end])[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(chunk_data, actual_crc) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError("PNG chunk checksum is invalid")
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError("PNG must begin with a valid IHDR chunk")
            width, height = struct.unpack(">II", chunk_data[:8])
            if width < 1 or height < 1:
                raise ValueError("PNG dimensions must be positive")
            if width > MAX_PNG_DIMENSION or height > MAX_PNG_DIMENSION:
                raise ValueError("PNG dimensions exceed the safety limit")
            if width * height > MAX_PNG_PIXELS:
                raise ValueError("PNG pixel count exceeds the safety limit")
            if chunk_data[10] != 0 or chunk_data[11] != 0:
                raise ValueError("PNG uses an unsupported compression or filter method")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise ValueError("PNG contains more than one IHDR chunk")
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise ValueError("PNG IEND chunk is malformed or followed by extra data")
            saw_iend = True
        offset = end
        chunk_index += 1
        if saw_iend:
            break
    if not (saw_ihdr and saw_idat and saw_iend):
        raise ValueError("PNG is missing required chunks")
    return width, height


@dataclass(frozen=True)
class DroneObject:
    object_id: str
    confidence: float
    bbox: tuple[float, float, float, float]
    display_id: str | None = None


@dataclass(frozen=True)
class DetectionResult:
    sequence: int
    received_monotonic: float
    completed_monotonic: float
    frame: np.ndarray
    objects: tuple[DroneObject, ...]
    frame_monotonic: float | None = None
    pair_id: tuple[int, int] | None = None


class DroneDetector(Protocol):
    """Runtime-neutral detector contract used by the fair scheduler."""

    def load(self) -> None: ...

    def detect(
            self, image: np.ndarray
    ) -> list[tuple[tuple[float, float, float, float], float]]: ...

    def diagnostics(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


def bbox_iou(
        first: tuple[float, float, float, float],
        second: tuple[float, float, float, float],
) -> float:
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    union = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(
        0.0, by2 - by1
    ) - intersection
    return intersection / union if union > 0 else 0.0


@dataclass
class _Track:
    track_id: int
    bbox: tuple[float, float, float, float]
    missed: int = 0


class DroneImageTracker:
    """Associate nearby drone boxes even when small boxes do not overlap."""

    def __init__(self, max_missed: int = 3) -> None:
        self.max_missed = max_missed
        self._next_id = 1
        self._tracks: dict[int, _Track] = {}

    def reset(self, *, reset_ids: bool = True) -> None:
        if reset_ids:
            self._next_id = 1
        self._tracks.clear()

    def update(
            self,
            detections: list[tuple[tuple[float, float, float, float], float]],
            image_width: int,
    ) -> tuple[DroneObject, ...]:
        for track in self._tracks.values():
            track.missed += 1
        available_tracks = set(self._tracks)
        assignments: dict[int, int] = {}
        pairs: list[tuple[float, int, int]] = []
        for detection_index, (bbox, _confidence) in enumerate(detections):
            for track_id in available_tracks:
                track = self._tracks[track_id]
                x1, y1, x2, y2 = bbox
                a1, b1, a2, b2 = track.bbox
                distance = math.hypot(x1 + x2 - a1 - a2, y1 + y2 - b1 - b2) / 2
                limit = max(x2 - x1, y2 - y1, a2 - a1, b2 - b1, image_width * 0.015)
                if distance <= limit * track.missed:
                    score = bbox_iou(bbox, track.bbox) - distance / (limit * track.missed)
                    pairs.append((score, detection_index, track_id))
        used_detections: set[int] = set()
        used_tracks: set[int] = set()
        for _overlap, detection_index, track_id in sorted(pairs, reverse=True):
            if detection_index in used_detections or track_id in used_tracks:
                continue
            assignments[detection_index] = track_id
            used_detections.add(detection_index)
            used_tracks.add(track_id)
        objects: list[DroneObject] = []
        for detection_index, (bbox, confidence) in enumerate(detections):
            track_id = assignments.get(detection_index)
            if track_id is None:
                track_id = self._next_id
                self._next_id += 1
                self._tracks[track_id] = _Track(track_id=track_id, bbox=bbox)
            track = self._tracks[track_id]
            track.bbox = bbox
            track.missed = 0
            objects.append(DroneObject(f"track-{track_id}", confidence, bbox))
        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if track.missed <= self.max_missed
        }
        return tuple(objects)


class DetectionCoordinator(threading.Thread):
    """One model/session, capacity-one inputs and fair round-robin scheduling."""

    def __init__(
            self,
            frames: dict[str, LatestFrameBuffer],
            detector: DroneDetector,
            *,
            per_camera_fps: float = 4.0,
            result_ttl: float = 0.6,
            max_frame_skew: float = 0.040,
    ) -> None:
        super().__init__(name="drone-detection", daemon=True)
        self.frames = frames
        self.detector = detector
        self.minimum_interval = 1.0 / per_camera_fps
        self.result_ttl = result_ttl
        self.max_frame_skew = max_frame_skew
        self._condition = threading.Condition()
        self._running = False
        self._shutdown_requested = False
        self._generation = 0
        self._model_state = "idle"
        self._error = ""
        self._results: dict[str, DetectionResult | None] = {key: None for key in CAMERA_KEYS}
        # Retain recent completed results for exact-frame preview and stereo.
        self._result_history: dict[str, deque[DetectionResult]] = {
            key: deque(maxlen=2) for key in CAMERA_KEYS
        }
        self._last_sequences = {key: 0 for key in CAMERA_KEYS}
        self._last_started = {key: 0.0 for key in CAMERA_KEYS}
        self._completed_times: dict[str, deque[float]] = {
            key: deque() for key in CAMERA_KEYS
        }
        self._last_inference_ms: dict[str, float | None] = {
            key: None for key in CAMERA_KEYS
        }
        self._trackers = {key: DroneImageTracker() for key in CAMERA_KEYS}
        self._labels: dict[tuple[str, str], str] = {}
        self._pending_view: tuple[str, FramePacket, tuple[int, int]] | None = None
        self._next_camera = 0

    def request_start(self) -> dict[str, Any]:
        with self._condition:
            if self._running:
                return self._state_locked()
            self._generation += 1
            self._running = True
            self._error = ""
            self._results = {key: None for key in CAMERA_KEYS}
            self._result_history = {key: deque(maxlen=2) for key in CAMERA_KEYS}
            self._labels.clear()
            self._pending_view = None
            self._last_sequences = {key: 0 for key in CAMERA_KEYS}
            self._last_started = {key: 0.0 for key in CAMERA_KEYS}
            self._completed_times = {key: deque() for key in CAMERA_KEYS}
            self._last_inference_ms = {key: None for key in CAMERA_KEYS}
            for tracker in self._trackers.values():
                tracker.reset()
            if self._model_state == "error":
                self._model_state = "idle"
            self._condition.notify_all()
            return self._state_locked()

    def request_stop(self) -> dict[str, Any]:
        with self._condition:
            self._generation += 1
            self._running = False
            self._results = {key: None for key in CAMERA_KEYS}
            self._result_history = {key: deque(maxlen=2) for key in CAMERA_KEYS}
            self._labels.clear()
            self._pending_view = None
            self._condition.notify_all()
            return self._state_locked()

    def state(self) -> dict[str, Any]:
        with self._condition:
            return self._state_locked()

    def generation(self) -> int:
        with self._condition:
            return self._generation

    def is_running(self) -> bool:
        with self._condition:
            return self._running

    def _state_locked(self) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {}
        get_diagnostics = getattr(self.detector, "diagnostics", None)
        if callable(get_diagnostics):
            try:
                value = get_diagnostics()
                if isinstance(value, dict):
                    diagnostics = value
            except Exception:
                diagnostics = {"provider": "tensorrt-cuda", "engineState": "error"}
        return {
            "running": self._running,
            "modelState": self._model_state,
            "error": self._error,
            "targetFpsPerCamera": round(1.0 / self.minimum_interval, 2),
            "resultTtlMs": round(self.result_ttl * 1000),
            **diagnostics,
        }

    def camera_metrics(self, camera_key: str) -> dict[str, float | None]:
        with self._condition:
            completed = self._completed_times[camera_key]
            now = time.monotonic()
            while completed and completed[0] < now - 1.0:
                completed.popleft()
            return {
                "fps": round(float(len(completed)), 2),
                "latencyMs": (
                    round(self._last_inference_ms[camera_key], 3)
                    if self._last_inference_ms[camera_key] is not None
                    else None
                ),
            }

    def latest(self, camera_key: str) -> DetectionResult | None:
        with self._condition:
            if not self._running:
                return None
            result = self._results[camera_key]
            if result is None or time.monotonic() - result.completed_monotonic > self.result_ttl:
                return None
            return self._label_result_locked(camera_key, result)

    def preview_detections(self) -> dict[str, dict[int, DetectionResult]]:
        """Return completed results indexed by their original frame sequences."""
        with self._condition:
            return {
                key: {
                    result.sequence: self._label_result_locked(key, result)
                    for result in self._result_history[key]
                } if self._running else {}
                for key in CAMERA_KEYS
            }

    def _label_result_locked(self, camera_key: str, result: DetectionResult) -> DetectionResult:
        if not result.objects:
            return result
        objects = tuple(
            replace(drone, display_id=self._labels.get((camera_key, drone.object_id), "drone"))
            for drone in result.objects
        )
        return replace(result, objects=objects)

    def detection_snapshot(self) -> dict[str, DetectionResult | None]:
        with self._condition:
            return {key: self.latest(key) for key in CAMERA_KEYS}

    def set_spatial_frame_skew(self, maximum: float) -> None:
        with self._condition:
            self.max_frame_skew = maximum
            self._pending_view = None
            self._labels.clear()

    def publish_spatial_labels(self, generation: int, targets: list[dict[str, Any]]) -> None:
        with self._condition:
            if self._running and generation == self._generation:
                self._labels = {
                    (key, target[key + "Id"]): target["label"]
                    for target in targets
                    for key in CAMERA_KEYS
                }

    def spatial_snapshot(self) -> dict[str, DetectionResult | None]:
        """Return only results inferred from the same selected frame pair."""
        with self._condition:
            now = time.monotonic()
            if self._running:
                for left in reversed(self._result_history["camera1"]):
                    if left.pair_id is None or now - left.received_monotonic > self.result_ttl:
                        continue
                    for right in reversed(self._result_history["camera2"]):
                        if right.pair_id == left.pair_id and now - right.received_monotonic <= self.result_ttl:
                            return {"camera1": left, "camera2": right}
            return dict.fromkeys(CAMERA_KEYS)

    def wait_latest(self, camera_key: str, timeout: float = 0.6) -> DetectionResult | None:
        """Wait briefly for a fresh result before a detection-mode snapshot."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._running:
                result = self._results[camera_key]
                if result is not None and time.monotonic() - result.completed_monotonic <= self.result_ttl:
                    return self._label_result_locked(camera_key, result)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
        return None

    def shutdown(self, timeout: float = 10.0) -> None:
        with self._condition:
            self._shutdown_requested = True
            self._running = False
            self._condition.notify_all()
        self.join(timeout=timeout)
        close_detector = getattr(self.detector, "close", None)
        if callable(close_detector):
            close_detector()

    def _select_work(self) -> tuple[str, FramePacket, int, tuple[int, int] | None] | None:
        if self._pending_view is not None:
            key, packet, pair_id = self._pending_view
            self._pending_view = None
            self._last_sequences[key] = packet.sequence
            return key, packet, self._generation, pair_id

        now = time.monotonic()
        if all(now - self._last_started[key] >= self.minimum_interval for key in CAMERA_KEYS):
            left, right = (
                self.frames[key].recent_snapshots(self._last_sequences[key]) for key in CAMERA_KEYS
            )
            pair = _pick_preview_pair(left, right, now, max_skew=self.max_frame_skew)
            if pair is not None:
                first, second, _, _ = pair
                pair_id = (first.sequence, second.sequence)
                self._pending_view = ("camera2", second, pair_id)
                # Both views share one sampling interval, even though their
                # inference calls run consecutively on the detector.
                self._last_started.update(camera1=now, camera2=now)
                self._last_sequences["camera1"] = first.sequence
                return "camera1", first, self._generation, pair_id

        for offset in range(len(CAMERA_KEYS)):
            index = (self._next_camera + offset) % len(CAMERA_KEYS)
            key = CAMERA_KEYS[index]
            packet = self.frames[key].latest()
            if packet is None or packet.sequence <= self._last_sequences[key]:
                continue
            if now - self._last_started[key] < self.minimum_interval:
                continue
            other = self.frames[CAMERA_KEYS[1 - index]].latest()
            if other is not None and now - other.received_monotonic <= PREVIEW_PAIR_MAX_AGE:
                # A live companion can still supply a compatible packet.
                if now - self._last_started[key] < self.minimum_interval + self.max_frame_skew:
                    continue
            self._next_camera = (index + 1) % len(CAMERA_KEYS)
            self._last_started[key] = now
            self._last_sequences[key] = packet.sequence
            return key, packet, self._generation, None
        return None

    def run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._running or self._shutdown_requested)
                if self._shutdown_requested:
                    return
                if self._model_state != "ready":
                    self._model_state = "loading"
            try:
                self.detector.load()
            except Exception as exc:
                import traceback
                traceback.print_exc()  # 👈 强行打印
                with self._condition:
                    self._model_state = "error"
                    self._error = f"ONNX model could not be loaded ({type(exc).__name__})"
                    self._running = False
                continue
            with self._condition:
                self._model_state = "ready"
                if not self._running:
                    continue
                work = self._select_work()
                if work is None:
                    self._condition.wait(timeout=0.015)
                    continue
            camera_key, packet, generation, pair_id = work
            # The frame buffer publishes immutable images. Snapshot writers
            # copy this exact frame before drawing its boxes.
            frame = packet.image
            inference_started = time.perf_counter()
            try:
                detections = self.detector.detect(frame)
            except Exception as exc:
                import traceback
                traceback.print_exc()  # 👈 强行打印
                with self._condition:
                    self._error = f"drone inference failed ({type(exc).__name__})"
                    self._model_state = "error"
                    self._running = False
                continue
            completed_at = time.monotonic()
            inference_ms = (time.perf_counter() - inference_started) * 1000.0
            with self._condition:
                if self._running and generation == self._generation:
                    previous = self._results[camera_key]
                    if (
                            previous is not None
                            and time.monotonic() - previous.completed_monotonic > self.result_ttl * 2.0
                    ):
                        self._trackers[camera_key].reset(reset_ids=False)
                    objects = self._trackers[camera_key].update(detections, frame.shape[1])
                    result = DetectionResult(
                        sequence=packet.sequence,
                        received_monotonic=packet.received_monotonic,
                        completed_monotonic=completed_at,
                        frame=frame,
                        objects=objects,
                        frame_monotonic=_preview_frame_time(packet),
                        pair_id=pair_id,
                    )
                    self._results[camera_key] = result
                    self._result_history[camera_key].append(result)
                    self._last_inference_ms[camera_key] = inference_ms
                    completed = self._completed_times[camera_key]
                    completed.append(completed_at)
                    while completed and completed[0] < completed_at - 1.0:
                        completed.popleft()
                    self._condition.notify_all()


def draw_drone_objects(image: np.ndarray, objects: tuple[DroneObject, ...]) -> None:
    """Draw high-contrast drone boxes and labels in-place."""
    height, width = image.shape[:2]
    thickness = max(2, round(min(width, height) / 360))
    font_scale = max(0.48, min(1.1, min(width, height) / 850))
    for detected in objects:
        x1, y1, x2, y2 = detected.bbox
        left = max(0, min(width - 1, round(x1)))
        top = max(0, min(height - 1, round(y1)))
        right = max(left + 1, min(width - 1, round(x2)))
        bottom = max(top + 1, min(height - 1, round(y2)))
        color = (45, 220, 85)
        cv2.rectangle(image, (left, top), (right, bottom), color, thickness, cv2.LINE_AA)
        label = f"{detected.display_id or 'drone'} {detected.confidence:.0%}"
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        label_height = text_height + baseline + 6
        label_left = min(left, max(0, width - text_width - 8))
        label_bottom = top if top >= label_height else min(height - 1, top + label_height)
        label_top = max(0, label_bottom - label_height)
        label_right = min(width - 1, label_left + text_width + 8)
        cv2.rectangle(image, (label_left, label_top), (label_right, label_bottom), color, -1)
        cv2.putText(
            image,
            label,
            (label_left + 4, max(0, label_bottom - baseline - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (8, 20, 12),
            thickness,
            cv2.LINE_AA,
        )


@dataclass(frozen=True)
class _OverlayFrameTask:
    image: np.ndarray
    pts_seconds: float | None
    frame_rate: float


@dataclass(frozen=True)
class _OverlayStop:
    reason: str


class _OverlayShutdown:
    pass


class OverlayVideoRecorder(threading.Thread):
    """Encode decoded frames with the latest detection boxes burned in.

    The original :class:`PacketRecorder` intentionally remuxes compressed
    packets without decoding them. That is ideal for a clean archival stream,
    but a compressed packet cannot contain the UI's detection overlay. The
    bridge therefore uses this recorder, which keeps the same trusted output
    directory and explicit Record toggle while encoding an annotated video.
    NVENC is preferred on the configured GPU; libx264 is a transparent
    compatibility fallback when the FFmpeg build does not expose NVENC.
    """

    _CODECS = ("h264_nvenc", "libx264")

    def __init__(
            self,
            output: OutputConfig,
            camera_key: str,
            annotate: Callable[[np.ndarray], None],
            *,
            queue_size: int = 12,
    ) -> None:
        self.camera_key = safe_file_component(camera_key)
        super().__init__(name=f"overlay-recorder-{self.camera_key}", daemon=True)
        self.output = output
        self.annotate = annotate
        self._queue: queue.Queue[_OverlayFrameTask | _OverlayStop | _OverlayShutdown] = queue.Queue(
            maxsize=max(1, min(int(queue_size), 4))
        )
        self._lock = threading.Lock()
        self._desired = False
        self._segment_active = False
        self._status = RecordingStatus()

    def status(self) -> RecordingStatus:
        with self._lock:
            return self._status

    def _set_status(self, **changes: Any) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)

    def _enqueue_control(self, item: _OverlayStop | _OverlayShutdown) -> None:
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    continue

    def _discard_pending_frames(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def request_start(self) -> None:
        with self._lock:
            if self._desired:
                return
            self._desired = True
            self._status = RecordingStatus(state="armed")
        LOG.info("Annotated recording armed; waiting for decoded frames")

    def request_stop(self) -> None:
        with self._lock:
            was_active = self._segment_active
            was_desired = self._desired
            self._desired = False
            if not was_active and was_desired:
                self._status = RecordingStatus(state="idle")
        if was_active:
            self._set_status(state="stopping")
            self._enqueue_control(_OverlayStop("user requested stop"))
        elif was_desired:
            self._discard_pending_frames()
            self._enqueue_control(_OverlayStop("user requested stop"))
            LOG.info("Annotated recording request cancelled before the first frame")

    def submit_frame(
            self,
            image: np.ndarray,
            *,
            pts_seconds: float | None,
            frame_rate: float,
    ) -> None:
        with self._lock:
            if not self._desired:
                return
        if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
            return
        try:
            task = _OverlayFrameTask(
                image=np.ascontiguousarray(image.copy()),
                pts_seconds=pts_seconds,
                frame_rate=max(1.0, min(float(frame_rate), 120.0)),
            )
            while True:
                try:
                    self._queue.put_nowait(task)
                    return
                except queue.Full:
                    try:
                        stale = self._queue.get_nowait()
                    except queue.Empty:
                        continue
                    if not isinstance(stale, _OverlayFrameTask):
                        try:
                            self._queue.put_nowait(stale)
                        except queue.Full:
                            LOG.debug("Annotated recording control marker already pending")
                        return
                    LOG.debug("Annotated recording queue full; dropped oldest frame")
        except (TypeError, ValueError):
            return

    def stream_disconnected(self, reason: str) -> None:
        with self._lock:
            active = self._segment_active
        if active:
            self._enqueue_control(_OverlayStop(f"stream disconnected: {reason}"))

    def shutdown(self, timeout: float = 10.0) -> None:
        with self._lock:
            self._desired = False
        self._enqueue_control(_OverlayShutdown())
        self.join(timeout=timeout)
        if self.is_alive():
            LOG.error("Annotated recorder did not exit before the shutdown timeout")

    def _open_encoder(
            self,
            image: np.ndarray,
            frame_rate: float,
            codec_names: tuple[str, ...] | None = None,
    ) -> tuple[av.container.OutputContainer, av.video.stream.VideoStream, Path, str, Fraction]:
        suffix = "mp4" if self.output.record_container == "mp4" else "mkv"
        stem = f"{self.camera_key}_video_{timestamp_for_filename()}"
        path = self.output.record_dir / f"{stem}.{suffix}"
        suffix_index = 1
        while path.exists():
            path = self.output.record_dir / f"{stem}_{suffix_index}.{suffix}"
            suffix_index += 1
        rate = max(1, min(round(frame_rate), 120))
        codec_time_base = Fraction(1, rate)
        format_name = "mp4" if suffix == "mp4" else "matroska"
        errors: list[str] = []
        candidates = codec_names or self._CODECS
        if image.shape[1] < 144 or image.shape[0] < 144:
            candidates = tuple(codec for codec in candidates if codec != "h264_nvenc")
        for codec_name in candidates:
            container: av.container.OutputContainer | None = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                container_options = {"movflags": "+faststart"} if format_name == "mp4" else None
                container = av.open(str(path), mode="w", format=format_name, options=container_options)
                stream = container.add_stream(codec_name, rate=rate)
                stream.width = int(image.shape[1])
                stream.height = int(image.shape[0])
                stream.pix_fmt = "yuv420p"
                if codec_name == "h264_nvenc":
                    stream.options = {"preset": "p4", "tune": "ll"}
                else:
                    stream.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": "23"}
                stream.codec_context.time_base = codec_time_base
                stream.codec_context.open()
                return container, stream, path, codec_name, codec_time_base
            except BaseException as exc:
                errors.append(f"{codec_name}:{type(exc).__name__}")
                if container is not None:
                    try:
                        container.close()
                    except BaseException:
                        pass
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise RuntimeError(f"no annotated video encoder is available ({', '.join(errors)})")

    @staticmethod
    def _frame_timestamp(
            pts_seconds: float | None,
            first_pts: float | None,
            frame_index: int,
            frame_rate: float,
    ) -> int:
        if pts_seconds is None or first_pts is None:
            return frame_index
        candidate = round(max(0.0, pts_seconds - first_pts) * frame_rate)
        return max(frame_index, candidate)

    def _close_segment(
            self,
            container: av.container.OutputContainer | None,
            stream: av.video.stream.VideoStream | None,
            path: Path | None,
            reason: str,
            *,
            flush: bool = True,
    ) -> None:
        if container is None or stream is None:
            return
        try:
            if flush:
                for packet in stream.encode(None):
                    container.mux(packet)
            container.close()
            size = path.stat().st_size if path is not None and path.exists() else 0
            self._set_status(
                state="armed" if self._desired else "idle",
                path=str(path.resolve()) if path is not None else self.status().path,
                bytes_written=size,
                segments_completed=self.status().segments_completed + 1,
            )
            LOG.info("Annotated recording finalized (%s): %s (%d bytes)", reason, path, size)
        except BaseException as exc:
            self._set_status(state="error", error=f"annotated recording finalize failed: {type(exc).__name__}")
            LOG.error("Annotated recording finalize failed (%s)", type(exc).__name__)

    def run(self) -> None:
        container: av.container.OutputContainer | None = None
        stream: av.video.stream.VideoStream | None = None
        path: Path | None = None
        codec_name = ""
        first_pts: float | None = None
        frame_index = 0
        packets_written = 0
        bytes_written = 0
        frame_rate = 25.0
        codec_time_base = Fraction(1, 25)
        while True:
            item = self._queue.get()
            if isinstance(item, _OverlayShutdown):
                self._close_segment(container, stream, path, "application shutdown")
                return
            if isinstance(item, _OverlayStop):
                self._close_segment(container, stream, path, item.reason)
                container = None
                stream = None
                path = None
                codec_name = ""
                first_pts = None
                frame_index = 0
                packets_written = 0
                bytes_written = 0
                with self._lock:
                    self._segment_active = False
                    if self._desired:
                        self._status = replace(self._status, state="armed")
                    elif self._status.state != "error":
                        self._status = replace(self._status, state="idle")
                continue
            if not isinstance(item, _OverlayFrameTask):
                continue
            with self._lock:
                if not self._desired and not self._segment_active:
                    continue
            try:
                if container is None or stream is None:
                    container, stream, path, codec_name, codec_time_base = self._open_encoder(
                        item.image,
                        item.frame_rate,
                    )
                    frame_rate = item.frame_rate
                    first_pts = item.pts_seconds
                    frame_index = 0
                    packets_written = 0
                    bytes_written = 0
                    with self._lock:
                        self._segment_active = True
                        self._status = RecordingStatus(state="recording", path=str(path.resolve()))
                    LOG.info("Annotated recording started (%s): %s", codec_name, path.resolve())
                self.annotate(item.image)
                video_frame = av.VideoFrame.from_ndarray(item.image, format="bgr24")
                video_frame.pts = self._frame_timestamp(
                    item.pts_seconds,
                    first_pts,
                    frame_index,
                    frame_rate,
                )
                video_frame.time_base = codec_time_base
                for packet in stream.encode(video_frame):
                    container.mux(packet)
                    packets_written += 1
                    bytes_written += packet.size
                frame_index += 1
                self._set_status(packets_written=packets_written, bytes_written=bytes_written)
            except BaseException as exc:
                self._set_status(state="error", error=f"annotated recording failed: {type(exc).__name__}")
                LOG.error("Annotated recording failed (%s)", type(exc).__name__)
                self._close_segment(container, stream, path, "encode error", flush=False)
                container = None
                stream = None
                path = None
                with self._lock:
                    self._desired = False
                    self._segment_active = False


@dataclass
class BridgeCameraRuntime:
    source: CameraSource
    output: OutputConfig
    frames: LatestFrameBuffer
    status: StatusStore
    recorder: PacketRecorder
    snapshots: SnapshotWriter
    worker: StreamWorker


@dataclass(frozen=True)
class EncodedPreview:
    jpeg: bytes
    sequence: int
    width: int
    height: int
    received_at_ms: int
    age_ms: float
    detection_sequence: int = 0


@dataclass(frozen=True)
class EncodedPreviewPair:
    camera1: EncodedPreview
    camera2: EncodedPreview
    skew_seconds: float
    oldest_frame_at: float
    epoch: str


def _preview_frame_time(packet: FramePacket) -> float:
    return packet.received_monotonic - max(0.0, packet.pts_backlog_seconds or 0.0)


def _pick_preview_pair(
        frames1: tuple[FramePacket, ...],
        frames2: tuple[FramePacket, ...],
        now: float,
        *,
        max_skew: float = PREVIEW_PAIR_MAX_SKEW,
        analyzed_sequences: tuple[Collection[int], Collection[int]] = ((), ()),
        after_sequences: tuple[int, int] = (0, 0),
) -> tuple[FramePacket, FramePacket, float, float] | None:
    best = None
    best_key = None
    most_analyzed = 0
    for first in frames1:
        first_at = _preview_frame_time(first)
        if now - first_at > PREVIEW_PAIR_MAX_AGE:
            continue
        for second in frames2:
            second_at = _preview_frame_time(second)
            skew = abs(first_at - second_at)
            oldest = min(first_at, second_at)
            if now - oldest > PREVIEW_PAIR_MAX_AGE or skew > max_skew:
                continue
            analyzed = int(first.sequence in analyzed_sequences[0]) + int(second.sequence in analyzed_sequences[1])
            most_analyzed = max(most_analyzed, analyzed)
            if first.sequence <= after_sequences[0] or second.sequence <= after_sequences[1]:
                continue
            key = (analyzed, oldest, -skew, first.sequence, second.sequence)
            if best_key is None or key > best_key:
                best_key = key
                best = first, second, skew, oldest
    # While a fresh analyzed view is available, wait for another with the
    # same coverage. Sending raw frames now would pass its sequence cursor.
    return best if best_key is not None and best_key[0] == most_analyzed else None


class OpenCvCudaPreviewProcessor:
    """Use the CUDA-enabled OpenCV build for the 4K-to-preview resize."""

    name = "opencv-cuda"

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._gpu_mat_factory = None  # 👈 强行禁用 GPU 缩放
        return

    @property
    def stream_enabled(self) -> bool:
        """Whether preview transfers currently use a non-default CUDA stream."""
        return False

    def resize(self, image: np.ndarray) -> np.ndarray:
        # 直接用 CPU 缩放，放弃 OpenCV CUDA
        return cv2.resize(image, (self.width, self.height), interpolation=cv2.INTER_AREA)


class PreviewEncoder:
    """Long-poll, latest-only preview encoder with CUDA resize telemetry."""

    def __init__(self, width: int, height: int, jpeg_quality: int, max_fps: float) -> None:
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.target_fps = max_fps
        self.minimum_interval = 1.0 / max_fps
        self.processor = OpenCvCudaPreviewProcessor(width, height)
        self._lock = threading.Lock()
        self._sequence = 0
        self._encoded_at = 0.0
        self._encoded_times: deque[float] = deque()
        self._preview: EncodedPreview | None = None

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            while self._encoded_times and self._encoded_times[0] < now - 1.0:
                self._encoded_times.popleft()
            return {
                "processor": self.processor.name,
                "cudaStream": self.processor.stream_enabled,
                "targetFps": round(self.target_fps, 2),
                "fps": round(float(len(self._encoded_times)), 2),
                "sequence": self._sequence,
            }

    def encode(
            self,
            frames: LatestFrameBuffer,
            detection_provider: Callable[[], dict[int, DetectionResult]],
            *,
            after_sequence: int,
            wait_timeout: float,
    ) -> EncodedPreview | None:
        deadline = time.monotonic() + wait_timeout
        while True:
            now = time.monotonic()
            results = detection_provider()
            packets = frames.recent_snapshots()
            if packets:
                analyzed = next((
                    packet for packet in reversed(packets)
                    if packet.sequence in results and now - _preview_frame_time(packet) <= PREVIEW_PAIR_MAX_AGE
                ), None)
                packet = analyzed if analyzed is not None else packets[-1]
                if packet.sequence > after_sequence:
                    detection = results.get(packet.sequence) if analyzed is not None else None
                    with self._lock:
                        cached = self._preview
                        if (
                                cached is not None and cached.sequence == packet.sequence
                                and cached.detection_sequence == (detection.sequence if detection else 0)
                        ):
                            return cached
                        rate_ready = now - self._encoded_at >= self.minimum_interval
                    if rate_ready:
                        return self.encode_packet(packet, detection)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(remaining, 0.010))

    def encode_packet(
            self, packet: FramePacket, detection: DetectionResult | None,
    ) -> EncodedPreview:
        """Only burn boxes produced by inference on this exact frame."""
        if detection is not None and detection.sequence != packet.sequence:
            detection = None
        with self._lock:
            detection_sequence = detection.sequence if detection is not None else 0
            if (
                    self._preview is not None
                    and self._preview.sequence == packet.sequence
                    and self._preview.detection_sequence == detection_sequence
            ):
                return self._preview
            preview = self.processor.resize(packet.image)
            if detection is not None and detection.objects:
                source_height, source_width = detection.frame.shape[:2]
                preview_height, preview_width = preview.shape[:2]
                x_scale = preview_width / source_width
                y_scale = preview_height / source_height
                scaled = tuple(
                    replace(
                        detected,
                        bbox=(
                            detected.bbox[0] * x_scale,
                            detected.bbox[1] * y_scale,
                            detected.bbox[2] * x_scale,
                            detected.bbox[3] * y_scale,
                        ),
                    )
                    for detected in detection.objects
                )
                draw_drone_objects(preview, scaled)
            ok, encoded = cv2.imencode(
                ".jpg",
                preview,
                [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
            )
            if not ok:
                raise BridgeApiError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "preview_encode_failed",
                    "camera preview encoding failed",
                )
            completed = time.monotonic()
            age_ms = max(0.0, completed - packet.received_monotonic) * 1000.0
            received_at_ms = round(time.time() * 1000.0 - age_ms)
            result = EncodedPreview(
                jpeg=encoded.tobytes(),
                sequence=packet.sequence,
                width=preview.shape[1],
                height=preview.shape[0],
                received_at_ms=received_at_ms,
                age_ms=age_ms,
                detection_sequence=detection_sequence,
            )
            if self._preview is None or packet.sequence >= self._preview.sequence:
                self._preview = result
                self._sequence = packet.sequence
            self._encoded_at = completed
            self._encoded_times.append(completed)
            while self._encoded_times and self._encoded_times[0] < completed - 1.0:
                self._encoded_times.popleft()
            return result


@dataclass(frozen=True)
class SpatialUpdate:
    generation: int
    payload: dict[str, Any]


class CameraBridgeService:
    def __init__(
            self,
            sources: list[CameraSource],
            outputs: dict[str, OutputConfig],
            *,
            model_path: Path,
            preview_width: int,
            preview_height: int,
            preview_jpeg_quality: int,
            preview_max_fps: float,
            engine_cache_dir: Path | None = None,
            detection_fps: float = 12.5,
            detector: DroneDetector | None = None,
            secrets: list[str] | None = None,
            spatial_config_path: Path | None = None,
    ) -> None:
        if {source.key for source in sources} != set(CAMERA_KEYS):
            raise ValueError("the bridge requires exactly camera1 and camera2")
        if any(outputs[key].record_on_start for key in CAMERA_KEYS):
            raise ValueError("record_on_start is forbidden in camera bridge mode")
        if detector is None and engine_cache_dir is None:
            raise ValueError("a trusted TensorRT engine cache directory is required")
        self._stop_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._recording_lock = threading.Lock()
        self._preview_pair_lock = threading.Lock()
        self._preview_epoch = uuid4().hex
        self._preview_pair: EncodedPreviewPair | None = None
        self._preview_pair_started_at = 0.0
        self._stopped = False
        self._secrets = tuple(filter(None, secrets or []))
        self._spatial_lock = threading.RLock()
        self._spatial_config_path = spatial_config_path or Path(__file__).with_name('spatial-config.json')
        self._spatial_config = validate_config(
            json.loads(self._spatial_config_path.read_text(encoding='utf-8-sig'))
            if self._spatial_config_path.exists() else default_config()
        )
        self._spatial_localizer = SpatialLocalizer(self._spatial_config)
        self._spatial_idle = self._spatial_localizer.localize(None, None)
        self._spatial_update: SpatialUpdate | None = None
        self._spatial_cache_key: tuple[Any, ...] | None = None
        self._spatial_cache: dict[str, Any] | None = None
        self._spatial_thread = threading.Thread(
            target=self._update_spatial, name="spatial-localization", daemon=True,
        )
        self.cameras: dict[str, BridgeCameraRuntime] = {}
        self.previews: dict[str, PreviewEncoder] = {}
        for source in sources:
            output = outputs[source.key]
            frames = LatestFrameBuffer(history_size=4)
            status = StatusStore()
            recorder = OverlayVideoRecorder(
                output,
                source.key,
                lambda image, camera_key=source.key: self._annotate_camera_frame(camera_key, image),
                queue_size=output.record_queue_packets,
            )
            snapshots = SnapshotWriter(output, source.key)
            worker = StreamWorker(source.stream, frames, status, recorder, self._stop_event, source.key)
            self.cameras[source.key] = BridgeCameraRuntime(
                source=source,
                output=output,
                frames=frames,
                status=status,
                recorder=recorder,
                snapshots=snapshots,
                worker=worker,
            )
            self.previews[source.key] = PreviewEncoder(
                preview_width,
                preview_height,
                preview_jpeg_quality,
                preview_max_fps,
            )
        if detector is None:
            assert engine_cache_dir is not None
            detector = TensorRTDroneDetector(model_path, engine_cache_dir)

        self.detection = DetectionCoordinator(
            {key: runtime.frames for key, runtime in self.cameras.items()},
            detector,
            per_camera_fps=detection_fps,
            result_ttl=0.6,
            max_frame_skew=spatial_frame_skew_seconds(self._spatial_config),
        )

    def _annotate_camera_frame(self, camera_key: str, image: np.ndarray) -> None:
        """Burn the freshest valid detection boxes into a recording frame."""
        detection = self.detection.latest(camera_key)
        if detection is None or not detection.objects:
            return
        source_height, source_width = detection.frame.shape[:2]
        frame_height, frame_width = image.shape[:2]
        if source_width == frame_width and source_height == frame_height:
            draw_drone_objects(image, detection.objects)
            return
        x_scale = frame_width / max(1, source_width)
        y_scale = frame_height / max(1, source_height)
        scaled = tuple(
            replace(
                detected,
                bbox=(
                    detected.bbox[0] * x_scale,
                    detected.bbox[1] * y_scale,
                    detected.bbox[2] * x_scale,
                    detected.bbox[3] * y_scale,
                ),
            )
            for detected in detection.objects
        )
        draw_drone_objects(image, scaled)

    def start(self) -> None:
        # Directory creation is intentionally deferred until an explicit
        # snapshot or recording request actually writes a file.
        self.detection.start()
        self._spatial_thread.start()
        for runtime in self.cameras.values():
            runtime.recorder.start()
            runtime.snapshots.start()
            runtime.worker.start()

    def _safe_error(self, value: str) -> str:
        result = value
        for secret in self._secrets:
            result = result.replace(secret, "***").replace(quote(secret, safe=""), "***")
        return result

    def status_payload(self) -> dict[str, Any]:
        cameras: dict[str, Any] = {}
        stream_views = {
            key: (runtime.status.snapshot(), runtime.frames.latest())
            for key, runtime in self.cameras.items()
        }
        generation = self.detection.generation()
        detection_state = self.detection.state()
        detections = self.detection.detection_snapshot()
        with self._spatial_lock:
            update = self._spatial_update
            idle = self._spatial_idle
        spatial = update.payload if update is not None and update.generation == generation else idle
        identities = {
            (key, target[key + "Id"]): target["id"]
            for target in spatial["targets"]
            for key in CAMERA_KEYS
        }

        for key, runtime in self.cameras.items():
            status, packet = stream_views[key]
            recording = runtime.recorder.status()
            detection = detections[key]
            if detection is not None:
                detected_height, detected_width = detection.frame.shape[:2]
                objects = [
                    {
                        "id": detected.object_id,
                        "globalId": identities.get((key, detected.object_id), f"{key}:{detected.object_id}"),
                        "displayId": detected.display_id,
                        "class": "drone",
                        "confidence": round(detected.confidence, 6),
                        "bbox": {
                            "x": round(detected.bbox[0] / detected_width, 7),
                            "y": round(detected.bbox[1] / detected_height, 7),
                            "width": round((detected.bbox[2] - detected.bbox[0]) / detected_width, 7),
                            "height": round((detected.bbox[3] - detected.bbox[1]) / detected_height, 7),
                        },
                        "frameSequence": detection.sequence,
                    }
                    for detected in detection.objects
                ]
            else:
                objects = []
            detection_metrics = self.detection.camera_metrics(key)
            preview_metrics = self.previews[key].metrics()
            cameras[key] = {
                "label": runtime.source.label,
                "state": status.state,
                "error": self._safe_error(status.error),
                "codec": status.codec,
                "width": status.width,
                "height": status.height,
                "nominalFps": round(status.nominal_fps, 3),
                "decodeFps": round(status.decode_fps, 3),
                "framesDecoded": status.frames_decoded,
                "reconnects": status.reconnects,
                "frame": {
                    "available": packet is not None,
                    "sequence": packet.sequence if packet is not None else 0,
                    "ageMs": round(max(0.0, time.monotonic() - packet.received_monotonic) * 1000)
                    if packet is not None
                    else None,
                },
                "objects": objects,
                "objectCount": len(objects),
                "preview": preview_metrics,
                "previewFps": preview_metrics["fps"],
                "detection": {
                    "count": len(objects),
                    "fps": detection_metrics["fps"],
                    "latencyMs": detection_metrics["latencyMs"],
                    "ageMs": (
                        round(max(0.0, time.monotonic() - detection.completed_monotonic) * 1000)
                        if detection is not None
                        else None
                    ),
                },
                "detectionFps": detection_metrics["fps"],
                "inferenceLatencyMs": detection_metrics["latencyMs"],
                "recording": {
                    "state": recording.state,
                    "path": recording.path,
                    "error": self._safe_error(recording.error),
                    "packetsWritten": recording.packets_written,
                    "bytesWritten": recording.bytes_written,
                },
            }
        preview_processor = next(iter(self.previews.values())).processor.name
        match_reason = {
            "ready": "matched",
            "frame_time_gap": "capture_time_gap",
            "no_valid_matches": "confirming",
        }.get(spatial["state"], spatial["state"])
        return {
            "version": 1,
            "detection": detection_state,
            "preview": {"processor": preview_processor},
            "matchingDiagnostics": {
                "algorithmVersion": "drone_stereo_center_v1",
                "reason": match_reason,
                "frameSkewMs": spatial["diagnostics"].get("frameSkewMs"),
                "confirmedPairs": sum(target["trackingState"] == "measured" for target in spatial["targets"]),
            },
            "spatial": spatial,
            "cameras": cameras,
        }

    def spatial_config_payload(self) -> dict[str, Any]:
        with self._spatial_lock:
            return {"config": json.loads(json.dumps(self._spatial_config))}

    def derive_spatial_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            config = derive_extrinsic_config(payload.get('config', payload))
        except (ValueError, TypeError, KeyError) as exc:
            raise BridgeApiError(HTTPStatus.BAD_REQUEST, 'invalid_spatial_config', str(exc)) from exc
        return {"config": config}

    def configure_spatial(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            config = validate_config(payload.get('config', payload))
        except (ValueError, TypeError, KeyError) as exc:
            raise BridgeApiError(HTTPStatus.BAD_REQUEST, 'invalid_spatial_config', str(exc)) from exc
        localizer = SpatialLocalizer(config)
        with self._spatial_lock:
            temporary = self._spatial_config_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(self._spatial_config_path)
            self._spatial_config = config
            self._spatial_localizer = localizer
            self._spatial_idle = localizer.localize(None, None)
            self._spatial_update = None
            self._spatial_cache_key = None
            self._spatial_cache = None
            if hasattr(self, "detection"):
                self.detection.set_spatial_frame_skew(spatial_frame_skew_seconds(config))
            return self.spatial_config_payload()

    def _update_spatial(self) -> None:
        """Consume the newest eligible detection pair without queueing old work."""
        tracking_session = None
        tracker = DronePositionTracker()
        while not self._stop_event.is_set():
            started = time.monotonic()
            generation = self.detection.generation()
            with self._spatial_lock:
                localizer = self._spatial_localizer
            if localizer.stream_order is None and not self.detection.is_running():
                first, second = (self.cameras[key].frames.latest() for key in CAMERA_KEYS)
                if first is not None and second is not None:
                    localizer.observe_stream_order(first.image, second.image)
            session = (localizer, localizer.stream_order, generation)
            if session != tracking_session:
                tracker = DronePositionTracker()
                tracking_session = session
            detections = self.detection.spatial_snapshot()
            payload = self.spatial_payload(detections)
            frame_times = [
                result.frame_monotonic if result.frame_monotonic is not None else result.received_monotonic
                for result in detections.values() if result is not None
            ]
            sample_at = min(frame_times) if len(frame_times) == 2 else -math.inf
            targets = tracker.update(payload["targets"], sample_at, time.monotonic())
            payload = {**payload, "targets": targets}
            if targets:
                if all(target["trackingState"] == "held" for target in targets):
                    payload.update(state="ready", message="短时保留最近一次有效位置，等待双路重新确认。")
                else:
                    payload.update(state="ready", message="双目三角测量已更新无人机位置。")
            elif payload["state"] == "ready":
                payload.update(state="no_valid_matches", message="正在确认无人机的连续双路位置。")
            with self._spatial_lock:
                if localizer is self._spatial_localizer and generation == self.detection.generation():
                    self.detection.publish_spatial_labels(generation, targets)
                    self._spatial_update = SpatialUpdate(generation, payload)
            self._stop_event.wait(max(0.02, SPATIAL_UPDATE_INTERVAL - (time.monotonic() - started)))

    def spatial_payload(
            self,
            detections: dict[str, DetectionResult | None],
    ) -> dict[str, Any]:
        # Existing lightweight test doubles need no configuration or file writes.
        if not hasattr(self, '_spatial_localizer'):
            return SpatialLocalizer(default_config()).localize(None, None)
        left, right = detections.get('camera1'), detections.get('camera2')
        frame_key = tuple((result.sequence, result.received_monotonic) if result else None for result in (left, right))
        with self._spatial_lock:
            localizer = self._spatial_localizer
            cache_key = (frame_key, localizer.stream_order)
            if cache_key == self._spatial_cache_key and self._spatial_cache is not None:
                return self._spatial_cache
            idle = getattr(self, '_spatial_idle', None)
        started = time.perf_counter()
        try:
            result = localizer.localize(left, right)
        except Exception as exc:
            LOG.exception('Spatial localization failed (%s): %s', type(exc).__name__, exc)
            result = {
                **(idle or localizer.localize(None, None)),
                'state': 'error', 'message': '定位计算失败，请检查标定参数。', 'targets': [],
            }
        result['diagnostics'] = {
            **result.get('diagnostics', {}),
            'computeMs': round((time.perf_counter() - started) * 1000.0, 2),
        }
        with self._spatial_lock:
            if localizer is self._spatial_localizer:
                self._spatial_cache = result
                self._spatial_cache_key = cache_key
        return result

    def preview_jpeg(
            self,
            camera_key: str,
            *,
            after_sequence: int = 0,
            wait_timeout: float = 0.0,
    ) -> EncodedPreview | None:
        self._camera(camera_key)
        return self.previews[camera_key].encode(
            self.cameras[camera_key].frames,
            lambda: self.detection.preview_detections()[camera_key],
            after_sequence=after_sequence,
            wait_timeout=wait_timeout,
        )

    def preview_pair(
            self,
            *,
            after_camera1: int,
            after_camera2: int,
            wait_timeout: float,
            epoch: str,
    ) -> EncodedPreviewPair | None:
        if epoch and epoch != self._preview_epoch:
            after_camera1 = after_camera2 = 0
        deadline = time.monotonic() + wait_timeout
        first_frames, second_frames = (self.cameras[key].frames for key in CAMERA_KEYS)
        first_encoder, second_encoder = (self.previews[key] for key in CAMERA_KEYS)
        interval = max(first_encoder.minimum_interval, second_encoder.minimum_interval)
        while not self._stop_event.is_set():
            with self._preview_pair_lock:
                now = time.monotonic()
                first = first_frames.recent_snapshots()
                second = second_frames.recent_snapshots()
                results = self.detection.preview_detections()
                selection = _pick_preview_pair(
                    first, second, now,
                    analyzed_sequences=(results["camera1"], results["camera2"]),
                    after_sequences=(after_camera1, after_camera2),
                )
                if selection is not None:
                    left, right, skew, oldest = selection
                    detection1 = results["camera1"].get(left.sequence)
                    detection2 = results["camera2"].get(right.sequence)
                    cached = self._preview_pair
                    if (
                            cached is not None
                            and cached.camera1.sequence == left.sequence
                            and cached.camera2.sequence == right.sequence
                            and cached.camera1.detection_sequence == (detection1.sequence if detection1 else 0)
                            and cached.camera2.detection_sequence == (detection2.sequence if detection2 else 0)
                    ):
                        return cached
                    if now - self._preview_pair_started_at >= interval:
                        # Rate-limit starts. Encoding time is already part of
                        # the frame interval and must not add a second delay.
                        self._preview_pair_started_at = now
                        preview1 = first_encoder.encode_packet(left, detection1)
                        preview2 = second_encoder.encode_packet(right, detection2)
                        if time.monotonic() - oldest <= PREVIEW_PAIR_MAX_AGE:
                            self._preview_pair = EncodedPreviewPair(
                                preview1, preview2, skew, oldest, self._preview_epoch,
                            )
                            return self._preview_pair
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._stop_event.wait(min(remaining, 0.010))
        return None

    def start_detection(self) -> dict[str, Any]:
        return {"detection": self.detection.request_start()}

    def stop_detection(self) -> dict[str, Any]:
        return {"detection": self.detection.request_stop()}

    def toggle_recording(self) -> dict[str, Any]:
        with self._recording_lock:
            currently_active = any(
                runtime.recorder.status().state in ACTIVE_RECORDING_STATES
                for runtime in self.cameras.values()
            )
            for runtime in self.cameras.values():
                if currently_active:
                    runtime.recorder.request_stop()
                else:
                    runtime.recorder.request_start()
        return {
            "recording": not currently_active,
            "cameras": {
                key: {"state": runtime.recorder.status().state}
                for key, runtime in self.cameras.items()
            },
        }

    def queue_rendered_snapshot(self, camera_key: str, png: bytes) -> dict[str, Any]:
        runtime = self._camera(camera_key)
        width, height = validate_png(png)
        path = runtime.snapshots.submit_png(png)
        if path is None:
            raise BridgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "snapshot_queue_full",
                "snapshot queue is busy; no file was written",
            )
        return {
            "camera": camera_key,
            "queued": True,
            "path": str(path.resolve()),
            "width": width,
            "height": height,
        }

    def capture_snapshots(self) -> dict[str, Any]:
        """Capture both cameras from detection-matched full-resolution frames."""
        queued: dict[str, Any] = {}
        state_reader = getattr(self.detection, "state", None)
        detection_state = state_reader() if callable(state_reader) else {"running": False}
        detection_running = bool(detection_state.get("running"))
        for camera_key, runtime in self.cameras.items():
            detection = self.detection.latest(camera_key)
            wait_reader = getattr(self.detection, "wait_latest", None)
            if detection is None and detection_running and callable(wait_reader):
                detection = wait_reader(camera_key, timeout=0.6)
            if detection is not None:
                image = detection.frame.copy()
                draw_drone_objects(image, detection.objects)
                sequence = detection.sequence
                detected = True
            else:
                if detection_running:
                    queued[camera_key] = {
                        "queued": False,
                        "error": "detection_not_ready",
                    }
                    continue
                packet = runtime.frames.latest()
                if packet is None:
                    queued[camera_key] = {
                        "queued": False,
                        "error": "no decoded frame is available",
                    }
                    continue
                image = packet.image.copy()
                sequence = packet.sequence
                detected = False
            path = runtime.snapshots.submit(image)
            if path is None:
                queued[camera_key] = {"queued": False, "error": "snapshot queue is busy"}
            else:
                queued[camera_key] = {
                    "queued": True,
                    "path": str(path.resolve()),
                    "frameSequence": sequence,
                    "detectionOverlay": detected,
                }
        if not any(item["queued"] for item in queued.values()):
            if detection_running:
                raise BridgeApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "detection_not_ready",
                    "a fresh drone detection frame is not ready; try the screenshot again",
                )
            raise BridgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "frames_unavailable",
                "neither camera has a frame available for capture",
            )
        return {"snapshots": queued}

    def _camera(self, camera_key: str) -> BridgeCameraRuntime:
        try:
            return self.cameras[camera_key]
        except KeyError as exc:
            raise BridgeApiError(HTTPStatus.NOT_FOUND, "camera_not_found", "unknown camera") from exc

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._stopped:
                return
            self._stopped = True
            self._stop_event.set()
        self.detection.shutdown()
        if self._spatial_thread.is_alive():
            self._spatial_thread.join(timeout=2.0)
        for runtime in self.cameras.values():
            runtime.worker.join(timeout=runtime.source.stream.read_timeout + 2.0)
        for runtime in self.cameras.values():
            runtime.recorder.shutdown()
            runtime.snapshots.shutdown()


def make_handler(
        service: Any,
        token: str,
        *,
        max_snapshot_bytes: int,
        shutdown_callback: Callable[[], None],
) -> type[BaseHTTPRequestHandler]:
    snapshot_pattern = re.compile(r"^/v1/cameras/(camera1|camera2)/snapshot\.png$")
    preview_pattern = re.compile(r"^/v1/cameras/(camera1|camera2)/(?:frame|preview)\.jpg$")

    class BridgeRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, error: BridgeApiError) -> None:
            self._json(error.status, {"error": {"code": error.code, "message": error.message}})

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if hmac.compare_digest(supplied, expected):
                return True
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"error": {"code": "unauthorized", "message": "valid bearer token required"}},
            )
            return False

        def _path(self) -> str:
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment:
                raise BridgeApiError(HTTPStatus.BAD_REQUEST, "invalid_url", "query strings are not accepted")
            return parsed.path

        def _content_length(self, *, required: bool) -> int:
            if self.headers.get("Transfer-Encoding"):
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "chunked_body_forbidden",
                    "Transfer-Encoding is not accepted",
                )
            raw = self.headers.get("Content-Length")
            if raw is None:
                if required:
                    raise BridgeApiError(
                        HTTPStatus.LENGTH_REQUIRED,
                        "content_length_required",
                        "Content-Length is required",
                    )
                return 0
            try:
                length = int(raw, 10)
            except ValueError as exc:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_content_length",
                    "Content-Length must be a non-negative integer",
                ) from exc
            if length < 0:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_content_length",
                    "Content-Length must be a non-negative integer",
                )
            return length

        def _empty_body(self) -> None:
            if self._content_length(required=False) != 0:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "body_not_allowed",
                    "this endpoint does not accept a request body",
                )

        def _bounded_integer_header(
                self,
                name: str,
                *,
                default: int,
                minimum: int,
                maximum: int,
        ) -> int:
            raw = self.headers.get(name)
            if raw is None:
                return default
            if not raw.isascii() or not raw.isdecimal():
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_preview_header",
                    f"{name} must be a decimal integer",
                )
            value = int(raw, 10)
            if value < minimum or value > maximum:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_preview_header",
                    f"{name} is outside the accepted range",
                )
            return value

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return
            try:
                path = self._path()
                if path == "/v1/status":
                    self._json(HTTPStatus.OK, service.status_payload())
                    return
                if path == "/v1/spatial/config":
                    self._json(HTTPStatus.OK, service.spatial_config_payload())
                    return
                if path == "/v1/cameras/preview-pair":
                    epoch = self.headers.get("X-Preview-Epoch", "")
                    if epoch and re.fullmatch(r"[0-9a-f]{32}", epoch) is None:
                        raise BridgeApiError(HTTPStatus.BAD_REQUEST, "invalid_preview_header", "invalid preview epoch")
                    pair = service.preview_pair(
                        after_camera1=self._bounded_integer_header(
                            "X-After-Camera1-Sequence", default=0, minimum=0, maximum=9_007_199_254_740_991,
                        ),
                        after_camera2=self._bounded_integer_header(
                            "X-After-Camera2-Sequence", default=0, minimum=0, maximum=9_007_199_254_740_991,
                        ),
                        wait_timeout=self._bounded_integer_header(
                            "X-Wait-Milliseconds", default=0, minimum=0, maximum=1_000,
                        ) / 1000.0,
                        epoch=epoch,
                    )
                    self.send_response((HTTPStatus.OK if pair else HTTPStatus.NO_CONTENT).value)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Length", str(len(pair.camera1.jpeg) + len(pair.camera2.jpeg) if pair else 0))
                    if pair is not None:
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("X-Camera1-Length", str(len(pair.camera1.jpeg)))
                        self.send_header("X-Camera1-Sequence", str(pair.camera1.sequence))
                        self.send_header("X-Camera2-Sequence", str(pair.camera2.sequence))
                        self.send_header("X-Frame-Skew-Ms", f"{pair.skew_seconds * 1000:.3f}")
                        self.send_header("X-Preview-Epoch", pair.epoch)
                    self.end_headers()
                    if pair is not None:
                        self.wfile.write(pair.camera1.jpeg)
                        self.wfile.write(pair.camera2.jpeg)
                    return
                match = preview_pattern.fullmatch(path)
                if match:
                    after_sequence = self._bounded_integer_header(
                        "X-After-Frame-Sequence",
                        default=0,
                        minimum=0,
                        maximum=9_007_199_254_740_991,
                    )
                    wait_milliseconds = self._bounded_integer_header(
                        "X-Wait-Milliseconds",
                        default=0,
                        minimum=0,
                        maximum=1_000,
                    )
                    preview = service.preview_jpeg(
                        match.group(1),
                        after_sequence=after_sequence,
                        wait_timeout=wait_milliseconds / 1000.0,
                    )
                    if preview is None:
                        self.send_response(HTTPStatus.NO_CONTENT.value)
                        self.send_header("Content-Length", "0")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        return
                    self.send_response(HTTPStatus.OK.value)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(preview.jpeg)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Frame-Sequence", str(preview.sequence))
                    self.send_header("X-Frame-Width", str(preview.width))
                    self.send_header("X-Frame-Height", str(preview.height))
                    self.send_header("X-Frame-Received-At", str(preview.received_at_ms))
                    self.send_header("X-Frame-Age-Milliseconds", f"{preview.age_ms:.3f}")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(preview.jpeg)
                    return
                raise BridgeApiError(HTTPStatus.NOT_FOUND, "not_found", "endpoint not found")
            except BridgeApiError as exc:
                self._error(exc)
            except Exception as exc:  # keep implementation details out of the API
                LOG.error("GET request failed (%s)", type(exc).__name__)
                self._error(
                    BridgeApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "request failed")
                )

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return
            try:
                path = self._path()
                if path in ("/v1/spatial/config", "/v1/spatial/config/derive"):
                    length = self._content_length(required=True)
                    if length > 128 * 1024:
                        raise BridgeApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'config_too_large', '定位配置过大。')
                    try:
                        payload = json.loads(self.rfile.read(length))
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise BridgeApiError(HTTPStatus.BAD_REQUEST, 'invalid_json', '定位配置不是有效 JSON。') from exc
                    if not isinstance(payload, dict):
                        raise BridgeApiError(HTTPStatus.BAD_REQUEST, 'invalid_spatial_config', '定位配置必须是对象。')
                    configure = service.derive_spatial_config if path.endswith("/derive") else service.configure_spatial
                    self._json(HTTPStatus.OK, configure(payload))
                    return
                if path == "/v1/recording/toggle":
                    self._empty_body()
                    self._json(HTTPStatus.OK, service.toggle_recording())
                    return
                if path == "/v1/detection/start":
                    self._empty_body()
                    self._json(HTTPStatus.OK, service.start_detection())
                    return
                if path == "/v1/detection/stop":
                    self._empty_body()
                    self._json(HTTPStatus.OK, service.stop_detection())
                    return
                if path == "/v1/snapshots":
                    self._empty_body()
                    self._json(HTTPStatus.ACCEPTED, service.capture_snapshots())
                    return
                if path == "/v1/shutdown":
                    self._empty_body()
                    self._json(HTTPStatus.ACCEPTED, {"shuttingDown": True})
                    threading.Thread(target=shutdown_callback, name="bridge-http-shutdown", daemon=True).start()
                    return
                match = snapshot_pattern.fullmatch(path)
                if match:
                    media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if media_type != "image/png":
                        raise BridgeApiError(
                            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                            "png_required",
                            "Content-Type must be image/png",
                        )
                    length = self._content_length(required=True)
                    if length < len(PNG_SIGNATURE):
                        raise BridgeApiError(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_png",
                            "request body is not a valid PNG",
                        )
                    if length > max_snapshot_bytes:
                        raise BridgeApiError(
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            "snapshot_too_large",
                            "PNG exceeds the configured request limit",
                        )
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise BridgeApiError(
                            HTTPStatus.BAD_REQUEST,
                            "truncated_body",
                            "request body ended before Content-Length bytes were received",
                        )
                    try:
                        payload = service.queue_rendered_snapshot(match.group(1), body)
                    except ValueError as exc:
                        raise BridgeApiError(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_png",
                            str(exc),
                        ) from exc
                    self._json(HTTPStatus.ACCEPTED, payload)
                    return
                raise BridgeApiError(HTTPStatus.NOT_FOUND, "not_found", "endpoint not found")
            except BridgeApiError as exc:
                self._error(exc)
            except Exception as exc:
                LOG.error("POST request failed (%s)", type(exc).__name__)
                self._error(
                    BridgeApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "request failed")
                )

    return BridgeRequestHandler


def parse_bridge_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loopback-only dual-camera bridge")
    parser.add_argument("--preview-width", type=int, default=960)
    parser.add_argument("--preview-height", type=int, default=540)
    parser.add_argument("--preview-jpeg-quality", type=int, default=82)
    parser.add_argument("--preview-max-fps", type=float, default=30.0)
    parser.add_argument("--detection-fps", type=float, default=12.5)
    parser.add_argument("--max-snapshot-mib", type=int, default=32)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    if args.preview_width < 1 or args.preview_height < 1:
        parser.error("preview dimensions must be positive")
    if not 1 <= args.preview_jpeg_quality <= 100:
        parser.error("preview JPEG quality must be between 1 and 100")
    if not 0.5 <= args.preview_max_fps <= 30.0:
        parser.error("preview max FPS must be between 0.5 and 30")
    if not 0.5 <= args.detection_fps <= 30.0:
        parser.error("detection FPS must be between 0.5 and 30")
    if not 1 <= args.max_snapshot_mib <= 128:
        parser.error("maximum snapshot size must be between 1 and 128 MiB")
    return args


def valid_token(value: str | None) -> bool:
    return bool(value and 32 <= len(value) <= 512 and not any(character.isspace() for character in value))


def main(argv: list[str] | None = None) -> int:
    bridge_args = parse_bridge_args(argv)
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.getenv("CAMERA_BRIDGE_TOKEN")
    if not valid_token(token):
        print("Camera bridge configuration error: CAMERA_BRIDGE_TOKEN is missing or invalid", file=sys.stderr)
        return 2
    assert token is not None
    shared_password = os.getenv("CAMERA_PASSWORD")
    camera1_password = os.getenv("CAMERA1_PASSWORD") or shared_password
    camera2_password = os.getenv("CAMERA2_PASSWORD") or shared_password
    if not camera1_password or not camera2_password:
        print("Camera bridge configuration error: camera credentials are incomplete", file=sys.stderr)
        return 2
    model_value = os.getenv("CAMERA_DRONE_MODEL")
    if not model_value:
        print("Camera bridge configuration error: CAMERA_DRONE_MODEL is missing", file=sys.stderr)
        return 2
    model_path = Path(model_value)
    if not model_path.is_absolute() or model_path.suffix.lower() != ".onnx" or not model_path.is_file():
        print("Camera bridge configuration error: CAMERA_DRONE_MODEL is not a readable ONNX file", file=sys.stderr)
        return 2
    model_path = model_path.resolve()
    engine_cache_value = os.getenv("CAMERA_TENSORRT_CACHE")
    if not engine_cache_value:
        print("Camera bridge configuration error: CAMERA_TENSORRT_CACHE is missing", file=sys.stderr)
        return 2
    engine_cache_dir = Path(engine_cache_value)
    if not engine_cache_dir.is_absolute() or ".." in engine_cache_dir.parts:
        print("Camera bridge configuration error: CAMERA_TENSORRT_CACHE is invalid", file=sys.stderr)
        return 2
    engine_cache_dir = engine_cache_dir.resolve(strict=False)

    logging.basicConfig(
        level=logging.DEBUG if bridge_args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s: %(message)s",
        stream=sys.stderr,
    )
    redaction = SecretRedactionFilter([token, camera1_password, camera2_password])
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction)
    av.logging.set_level(av.logging.DEBUG if bridge_args.debug else av.logging.ERROR)

    try:
        camera_args = parse_camera_args([])
        camera_args.record_on_start = False
        validate_camera_args(camera_args)
        identity_redaction = SecretRedactionFilter(
            [
                camera_args.camera1_username,
                camera_args.camera2_username,
                camera_args.camera1_ip,
                camera_args.camera2_ip,
            ]
        )
        for log_handler in logging.getLogger().handlers:
            log_handler.addFilter(identity_redaction)
        sources, outputs = build_sources_and_outputs(
            camera_args,
            camera1_password,
            camera2_password,
        )
        service = CameraBridgeService(
            sources,
            outputs,
            model_path=model_path,
            preview_width=bridge_args.preview_width,
            preview_height=bridge_args.preview_height,
            preview_jpeg_quality=bridge_args.preview_jpeg_quality,
            preview_max_fps=bridge_args.preview_max_fps,
            engine_cache_dir=engine_cache_dir,
            detection_fps=bridge_args.detection_fps,
            secrets=[
                camera1_password,
                camera2_password,
                camera_args.camera1_username,
                camera_args.camera2_username,
                camera_args.camera1_ip,
                camera_args.camera2_ip,
            ],
        )
        server_ref: list[ThreadingHTTPServer] = []

        def stop_server() -> None:
            if server_ref:
                server_ref[0].shutdown()

        handler = make_handler(
            service,
            token,
            max_snapshot_bytes=bridge_args.max_snapshot_mib * 1024 * 1024,
            shutdown_callback=stop_server,
        )
        server = ThreadingHTTPServer((LOOPBACK_HOST, 0), handler)
        server.daemon_threads = True
        server_ref.append(server)
        service.start()
    except (OSError, RuntimeError, ValueError) as exc:
        import traceback
        traceback.print_exc()  # 👈 核心修改：打印完整的错误堆栈
        LOG.error("Camera bridge startup failed (%s)", type(exc).__name__)
        return 2

    ready = {
        "type": "camera-bridge-ready",
        "version": 1,
        "host": LOOPBACK_HOST,
        "port": server.server_address[1],
        "pid": os.getpid(),
        "cameras": list(CAMERA_KEYS),
    }
    print(json.dumps(ready, separators=(",", ":")), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
