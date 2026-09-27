#!/usr/bin/env python3
"""Low-latency RTSP preview with PyAV and an always-latest frame buffer."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
import getpass
import json
import logging
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
from typing import Any
from urllib.parse import quote

import av
from av.codec.hwaccel import HWAccel, hwdevices_available
import cv2
import numpy as np
from dotenv import load_dotenv


LOG = logging.getLogger("camera-low-latency")
STREAM_INDEX = {"main": 1, "sub": 2, "third": 3}


class StreamBacklogError(RuntimeError):
    """Raised when decoded PTS shows that latency is accumulating."""


@dataclass(frozen=True)
class StreamConfig:
    host: str
    port: int
    username: str
    password: str
    channel: int
    stream_name: str
    transport: str
    path: str | None
    connect_timeout: float
    read_timeout: float
    analyzeduration_us: int
    probesize: int
    buffer_size: int
    decoder_threads: int
    hwaccel: str
    max_backlog_seconds: float
    backlog_frames: int

    @property
    def stream_id(self) -> str:
        return f"{self.channel}{STREAM_INDEX[self.stream_name]:02d}"

    @property
    def stream_path(self) -> str:
        if self.path:
            return self.path if self.path.startswith("/") else f"/{self.path}"
        return f"/Streaming/Channels/{self.stream_id}"

    def rtsp_url(self, *, redact_password: bool = False) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        user = quote(self.username, safe="")
        password = "***" if redact_password else quote(self.password, safe="")
        credentials = ""
        if self.username:
            credentials = f"{user}:{password}@"
        return f"rtsp://{credentials}{host}:{self.port}{self.stream_path}"

    def av_options(self) -> dict[str, str]:
        timeout_us = max(1, int(self.read_timeout * 1_000_000))
        return {
            "rtsp_transport": self.transport,
            "fflags": "nobuffer",
            "flags": "low_delay",
            "avioflags": "direct",
            "flush_packets": "1",
            "max_delay": "0",
            "reorder_queue_size": "0",
            "probesize": str(self.probesize),
            "analyzeduration": str(self.analyzeduration_us),
            "buffer_size": str(self.buffer_size),
            "rw_timeout": str(timeout_us),
            "stimeout": str(timeout_us),
        }


@dataclass(frozen=True)
class CameraSource:
    key: str
    label: str
    stream: StreamConfig


@dataclass(frozen=True)
class OutputConfig:
    record_dir: Path
    snapshot_dir: Path
    snapshot_format: str
    jpeg_quality: int
    png_compression: int
    record_container: str
    record_queue_packets: int
    record_on_start: bool


@dataclass(frozen=True)
class FramePacket:
    image: np.ndarray
    sequence: int
    received_monotonic: float
    pts_seconds: float | None
    pts_backlog_seconds: float | None


class LatestFrameBuffer:
    """A capacity-one frame buffer. Producers overwrite unconsumed frames."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._packet: FramePacket | None = None
        self._sequence = 0
        self._consumed_sequence = 0
        self._overwritten = 0

    def publish(
        self,
        image: np.ndarray,
        received_monotonic: float,
        pts_seconds: float | None,
        pts_backlog_seconds: float | None,
    ) -> None:
        with self._condition:
            if self._packet is not None and self._packet.sequence > self._consumed_sequence:
                self._overwritten += 1
            self._sequence += 1
            self._packet = FramePacket(
                image=image,
                sequence=self._sequence,
                received_monotonic=received_monotonic,
                pts_seconds=pts_seconds,
                pts_backlog_seconds=pts_backlog_seconds,
            )
            self._condition.notify_all()

    def wait_for_new(self, last_sequence: int, timeout: float) -> FramePacket | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._packet is not None and self._packet.sequence > last_sequence,
                timeout=timeout,
            )
            if self._packet is None or self._packet.sequence <= last_sequence:
                return None
            self._consumed_sequence = self._packet.sequence
            return self._packet

    def latest(self) -> FramePacket | None:
        """Return the latest immutable packet reference without consuming it.

        Sidecar preview readers use this method so an HTTP request cannot alter
        the desktop preview's overwrite/consumption accounting.
        """
        with self._condition:
            return self._packet

    def wait_for_new_snapshot(self, last_sequence: int, timeout: float) -> FramePacket | None:
        """Wait for a newer immutable packet without changing consumption stats.

        The Electron bridge uses this for bounded long-poll preview requests.
        Keeping this separate from :meth:`wait_for_new` means UI readers never
        affect the overwrite counters used by the original OpenCV preview CLI.
        """
        if last_sequence < 0:
            raise ValueError("last_sequence must be non-negative")
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        with self._condition:
            self._condition.wait_for(
                lambda: self._packet is not None and self._packet.sequence > last_sequence,
                timeout=timeout,
            )
            if self._packet is None or self._packet.sequence <= last_sequence:
                return None
            return self._packet

    @property
    def overwritten(self) -> int:
        with self._condition:
            return self._overwritten


@dataclass
class StreamStatus:
    state: str = "starting"
    error: str = ""
    codec: str = "unknown"
    profile: str = "unknown"
    width: int = 0
    height: int = 0
    nominal_fps: float = 0.0
    decode_fps: float = 0.0
    frames_decoded: int = 0
    reconnects: int = 0


class StatusStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = StreamStatus()

    def update(self, **changes: Any) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(self._value, name, value)

    def snapshot(self) -> StreamStatus:
        with self._lock:
            return replace(self._value)


@dataclass(frozen=True)
class RecordingStatus:
    state: str = "idle"
    path: str = ""
    error: str = ""
    packets_written: int = 0
    bytes_written: int = 0
    segments_completed: int = 0


@dataclass(frozen=True)
class _RecorderStart:
    input_stream: av.video.stream.VideoStream
    path: Path


@dataclass(frozen=True)
class _RecorderPacket:
    packet: av.Packet


@dataclass(frozen=True)
class _RecorderStop:
    reason: str


class _RecorderShutdown:
    pass


def timestamp_for_filename(*, milliseconds: bool = True) -> str:
    value = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return value[:-3] if milliseconds else value[:15]


def safe_file_component(value: str) -> str:
    """Return a readable filename component without path separators."""
    cleaned = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    return cleaned.strip("_") or "camera"


def clone_packet(packet: av.Packet) -> av.Packet:
    """Copy a compressed packet so disk muxing cannot race with decoding."""
    copied = av.Packet(bytes(packet))
    copied.pts = packet.pts
    copied.dts = packet.dts
    copied.duration = packet.duration
    copied.time_base = packet.time_base
    copied.is_keyframe = packet.is_keyframe
    return copied


class PacketRecorder(threading.Thread):
    """Asynchronously remux compressed camera packets without re-encoding."""

    def __init__(self, output: OutputConfig, camera_key: str = "camera") -> None:
        self.camera_key = safe_file_component(camera_key)
        super().__init__(name=f"packet-recorder-{self.camera_key}", daemon=True)
        self.output = output
        self._queue: queue.Queue[object] = queue.Queue(maxsize=output.record_queue_packets)
        self._lock = threading.Lock()
        self._desired = False
        self._segment_active = False
        self._awaiting_keyframe = False
        self._abort = threading.Event()
        self._status = RecordingStatus()

    def status(self) -> RecordingStatus:
        with self._lock:
            return replace(self._status)

    def _set_status(self, **changes: Any) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)

    def request_start(self) -> None:
        with self._lock:
            if self._desired:
                return
            self._desired = True
            self._awaiting_keyframe = True
            self._status = replace(self._status, state="armed", path="", error="")
        LOG.info("Recording armed; waiting for the next keyframe")

    def request_stop(self) -> None:
        with self._lock:
            if not self._desired and not self._segment_active:
                return
            self._desired = False
            self._awaiting_keyframe = False
            active = self._segment_active
            self._segment_active = False
            self._status = replace(self._status, state="stopping" if active else "idle")
            if active:
                self._enqueue_locked(_RecorderStop("user requested stop"))
        if active:
            LOG.info("Recording stop requested; finalizing the container")
        else:
            LOG.info("Recording request cancelled before the first keyframe")

    def toggle(self) -> None:
        with self._lock:
            desired = self._desired
        if desired:
            self.request_stop()
        else:
            self.request_start()

    def submit_packet(self, packet: av.Packet, input_stream: av.video.stream.VideoStream) -> None:
        if packet.size <= 0 or packet.dts is None:
            return
        with self._lock:
            if not self._desired:
                return
            commands: list[object] = []
            if self._awaiting_keyframe:
                if not packet.is_keyframe:
                    return
                suffix = "mp4" if self.output.record_container == "mp4" else "mkv"
                path = self.output.record_dir / f"{self.camera_key}_video_{timestamp_for_filename()}.{suffix}"
                commands.append(_RecorderStart(input_stream=input_stream, path=path))
                self._awaiting_keyframe = False
                self._segment_active = True
                self._status = replace(
                    self._status,
                    state="starting",
                    path=str(path.resolve()),
                    error="",
                    packets_written=0,
                    bytes_written=0,
                )
            if not self._segment_active:
                return
            commands.append(_RecorderPacket(clone_packet(packet)))
            for command in commands:
                if not self._enqueue_locked(command):
                    break

    def stream_disconnected(self, reason: str) -> None:
        with self._lock:
            if not self._segment_active:
                return
            self._segment_active = False
            self._awaiting_keyframe = self._desired
            self._status = replace(self._status, state="armed" if self._desired else "stopping")
            self._enqueue_locked(_RecorderStop(f"stream disconnected: {reason}"))

    def _enqueue_locked(self, item: object) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            message = (
                f"recording queue exceeded {self.output.record_queue_packets} packets; "
                "recording was stopped to protect live preview latency"
            )
            self._desired = False
            self._segment_active = False
            self._awaiting_keyframe = False
            self._status = replace(self._status, state="error", error=message)
            self._abort.set()
            LOG.error(message)
            return False

    def shutdown(self, timeout: float = 10.0) -> None:
        self.request_stop()
        try:
            self._queue.put(_RecorderShutdown(), timeout=timeout)
        except queue.Full:
            self._abort.set()
            self._queue.put(_RecorderShutdown())
        self.join(timeout=timeout)
        if self.is_alive():
            LOG.error("Recorder thread did not exit before the shutdown timeout")

    def _close_container(
        self,
        container: av.container.OutputContainer | None,
        path: Path | None,
        reason: str,
    ) -> None:
        if container is None:
            return
        try:
            container.close()
        except BaseException as exc:
            message = f"failed to finalize recording {path}: {explain_exception(exc)}"
            self._set_status(state="error", error=message)
            LOG.error(message)
            return
        size = path.stat().st_size if path is not None and path.exists() else 0
        with self._lock:
            self._status = replace(
                self._status,
                state="armed" if self._desired else "idle",
                bytes_written=max(self._status.bytes_written, size),
                segments_completed=self._status.segments_completed + 1,
            )
        LOG.info("Recording finalized (%s): %s (%d bytes)", reason, path, size)

    def run(self) -> None:
        container: av.container.OutputContainer | None = None
        output_stream: av.video.stream.VideoStream | None = None
        path: Path | None = None
        first_dts: int | None = None
        packets_written = 0
        bytes_written = 0
        while True:
            if self._abort.is_set():
                self._abort.clear()
                self._close_container(container, path, "queue overflow")
                container = None
                output_stream = None
                path = None
                first_dts = None
                while True:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break
            item = self._queue.get()
            if isinstance(item, _RecorderShutdown):
                self._close_container(container, path, "application shutdown")
                break
            if isinstance(item, _RecorderStart):
                self._close_container(container, path, "new segment")
                container = None
                try:
                    path = item.path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    format_name = "mp4" if self.output.record_container == "mp4" else "matroska"
                    container_options = {"movflags": "+faststart"} if format_name == "mp4" else None
                    container = av.open(str(path), mode="w", format=format_name, options=container_options)
                    output_stream = container.add_stream_from_template(item.input_stream)
                    first_dts = None
                    packets_written = 0
                    bytes_written = 0
                    self._set_status(state="recording", path=str(path.resolve()), error="")
                    LOG.info("Recording original compressed stream to %s", path.resolve())
                except BaseException as exc:
                    message = f"unable to start recording: {explain_exception(exc)}"
                    self._set_status(state="error", error=message)
                    LOG.error(message)
                    if container is not None:
                        container.close()
                    container = None
                    output_stream = None
                    with self._lock:
                        self._desired = False
                        self._segment_active = False
                        self._awaiting_keyframe = False
                continue
            if isinstance(item, _RecorderStop):
                self._close_container(container, path, item.reason)
                container = None
                output_stream = None
                path = None
                first_dts = None
                continue
            if not isinstance(item, _RecorderPacket) or container is None or output_stream is None:
                continue
            try:
                packet = item.packet
                if first_dts is None:
                    first_dts = packet.dts if packet.dts is not None else packet.pts or 0
                if packet.dts is not None:
                    packet.dts -= first_dts
                if packet.pts is not None:
                    packet.pts -= first_dts
                packet.stream = output_stream
                container.mux(packet)
                packets_written += 1
                bytes_written += packet.size
                self._set_status(packets_written=packets_written, bytes_written=bytes_written)
            except BaseException as exc:
                message = f"recording mux failed: {explain_exception(exc)}"
                self._set_status(state="error", error=message)
                LOG.error(message)
                self._close_container(container, path, "mux error")
                container = None
                output_stream = None
                path = None
                with self._lock:
                    self._desired = False
                    self._segment_active = False
                    self._awaiting_keyframe = False


@dataclass(frozen=True)
class _SnapshotTask:
    image: np.ndarray
    path: Path


@dataclass(frozen=True)
class _EncodedSnapshotTask:
    data: bytes
    path: Path


class SnapshotWriter(threading.Thread):
    """Asynchronously encode and save full-resolution snapshots."""

    def __init__(self, output: OutputConfig, camera_key: str = "camera", queue_size: int = 4) -> None:
        self.camera_key = safe_file_component(camera_key)
        super().__init__(name=f"snapshot-writer-{self.camera_key}", daemon=True)
        self.output = output
        self._queue: queue.Queue[_SnapshotTask | _EncodedSnapshotTask | None] = queue.Queue(maxsize=queue_size)
        self._path_lock = threading.Lock()
        self._reserved_paths: set[Path] = set()

    def _allocate_path(self, extension: str) -> Path:
        stem = f"{self.camera_key}_snapshot_{timestamp_for_filename()}"
        with self._path_lock:
            suffix = 0
            while True:
                name = f"{stem}.{extension}" if suffix == 0 else f"{stem}_{suffix}.{extension}"
                path = self.output.snapshot_dir / name
                if path not in self._reserved_paths and not path.exists():
                    self._reserved_paths.add(path)
                    return path
                suffix += 1

    def _release_path(self, path: Path) -> None:
        with self._path_lock:
            self._reserved_paths.discard(path)

    def submit(self, image: np.ndarray) -> Path | None:
        extension = "jpg" if self.output.snapshot_format in ("jpg", "jpeg") else "png"
        path = self._allocate_path(extension)
        try:
            self._queue.put_nowait(_SnapshotTask(image=image, path=path))
            LOG.info("Snapshot queued: %s", path.resolve())
            return path
        except queue.Full:
            self._release_path(path)
            LOG.error("Snapshot queue is full; request dropped to protect preview latency")
            return None

    def submit_png(self, data: bytes) -> Path | None:
        """Queue an already encoded PNG using the configured, trusted directory.

        The local camera bridge uses this for an Electron canvas capture that
        already includes the exact person boxes shown to the operator. Callers
        must validate the PNG before submitting it; no client path is accepted.
        """
        path = self._allocate_path("png")
        try:
            self._queue.put_nowait(_EncodedSnapshotTask(data=bytes(data), path=path))
            LOG.info("Rendered PNG snapshot queued: %s", path.resolve())
            return path
        except queue.Full:
            self._release_path(path)
            LOG.error("Snapshot queue is full; request dropped to protect preview latency")
            return None

    def shutdown(self, timeout: float = 10.0) -> None:
        self._queue.put(None)
        self.join(timeout=timeout)
        if self.is_alive():
            LOG.error("Snapshot writer did not exit before the shutdown timeout")

    def run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                break
            try:
                task.path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(task, _EncodedSnapshotTask):
                    task.path.write_bytes(task.data)
                    LOG.info("Rendered PNG snapshot saved: %s (%d bytes)", task.path.resolve(), len(task.data))
                    continue
                if task.path.suffix.lower() == ".jpg":
                    parameters = [cv2.IMWRITE_JPEG_QUALITY, self.output.jpeg_quality]
                else:
                    parameters = [cv2.IMWRITE_PNG_COMPRESSION, self.output.png_compression]
                if not cv2.imwrite(str(task.path), task.image, parameters):
                    raise OSError("cv2.imwrite returned false")
                height, width = task.image.shape[:2]
                LOG.info("Snapshot saved: %s (%dx%d)", task.path.resolve(), width, height)
            except BaseException as exc:
                LOG.error("Snapshot failed for %s: %s", task.path, explain_exception(exc))
            finally:
                self._release_path(task.path)


class PtsBacklogTracker:
    """Estimate growth in receiver backlog relative to the first decoded PTS."""

    def __init__(self) -> None:
        self._base_pts: float | None = None
        self._base_arrival: float | None = None
        self._last_pts: float | None = None

    def observe(self, pts_seconds: float | None, arrival_monotonic: float) -> float | None:
        if pts_seconds is None:
            return None
        if (
            self._base_pts is None
            or self._base_arrival is None
            or self._last_pts is None
            or pts_seconds < self._last_pts - 0.25
            or pts_seconds - self._last_pts > 30.0
        ):
            self._base_pts = pts_seconds
            self._base_arrival = arrival_monotonic
            self._last_pts = pts_seconds
            return 0.0
        self._last_pts = pts_seconds
        stream_elapsed = pts_seconds - self._base_pts
        wall_elapsed = arrival_monotonic - self._base_arrival
        drift = wall_elapsed - stream_elapsed
        if drift < -0.5:
            self._base_pts = pts_seconds
            self._base_arrival = arrival_monotonic
            return 0.0
        return max(0.0, drift)


def explain_exception(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if "401" in lowered or "unauthorized" in lowered or "permission denied" in lowered:
        return f"authentication failed (401): verify camera/RTSP username and password; {text}"
    if "timed out" in lowered or "timeout" in lowered:
        return f"connection/read timeout: verify IP, port, VLAN and transport; {text}"
    if "refused" in lowered:
        return f"connection refused: RTSP may be disabled or the port is wrong; {text}"
    if "invalid data" in lowered or "not found" in lowered:
        return f"stream path, codec probing or RTSP response was invalid; {text}"
    return text


def tcp_preflight(host: str, port: int, timeout: float) -> None:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as exc:
        raise ConnectionError(f"TCP {host}:{port} is unreachable: {exc}") from exc


def build_hwaccel(name: str) -> HWAccel | None:
    if name == "none":
        return None
    available = set(hwdevices_available())
    if name not in available:
        raise RuntimeError(
            f"hardware decoder '{name}' is unavailable; PyAV reports: {', '.join(sorted(available)) or 'none'}"
        )
    return HWAccel(name, allow_software_fallback=True)


def open_rtsp(config: StreamConfig) -> av.container.InputContainer:
    tcp_preflight(config.host, config.port, min(config.connect_timeout, 3.0))
    hwaccel = build_hwaccel(config.hwaccel)
    return av.open(
        config.rtsp_url(),
        mode="r",
        format="rtsp",
        options=config.av_options(),
        timeout=(config.connect_timeout, config.read_timeout),
        hwaccel=hwaccel,
    )


def configure_decoder(stream: av.video.stream.VideoStream, decoder_threads: int) -> None:
    if decoder_threads > 0:
        stream.codec_context.thread_count = decoder_threads
        try:
            stream.codec_context.thread_type = "SLICE"
        except (AttributeError, ValueError, RuntimeError):
            LOG.debug("Slice threading is unavailable for this decoder", exc_info=True)


def stream_metadata(stream: av.video.stream.VideoStream) -> dict[str, Any]:
    # RTSP's SDP may omit avg_frame_rate. Do not substitute guessed_rate here:
    # FFmpeg commonly reports 50 tbr for this camera's real 25 fps stream.
    rate = float(stream.average_rate) if stream.average_rate else 0.0
    context = stream.codec_context
    return {
        "codec": context.name or "unknown",
        "profile": str(context.profile or "unknown"),
        "width": int(context.width or 0),
        "height": int(context.height or 0),
        "nominal_fps": rate,
        "pixel_format": context.pix_fmt or "unknown",
    }


def first_video_stream(container: av.container.InputContainer) -> av.video.stream.VideoStream:
    if not container.streams.video:
        raise RuntimeError("RTSP session opened, but no video stream was advertised")
    return container.streams.video[0]


def probe_stream(config: StreamConfig) -> int:
    container: av.container.InputContainer | None = None
    try:
        container = open_rtsp(config)
        stream = first_video_stream(container)
        configure_decoder(stream, config.decoder_threads)
        metadata = stream_metadata(stream)
        deadline = time.monotonic() + max(config.read_timeout, 3.0)
        first_frame: av.VideoFrame | None = None
        pts_samples: list[float] = []
        for packet in container.demux(stream):
            for frame in packet.decode():
                if first_frame is None:
                    first_frame = frame
                if frame.pts is not None and frame.time_base is not None:
                    pts_samples.append(float(frame.pts * frame.time_base))
                if len(pts_samples) >= 8:
                    break
            if len(pts_samples) >= 8 or time.monotonic() >= deadline:
                break
        if first_frame is None:
            raise TimeoutError("stream opened but no decodable video frame arrived")
        measured_fps = 0.0
        if len(pts_samples) >= 2 and pts_samples[-1] > pts_samples[0]:
            measured_fps = (len(pts_samples) - 1) / (pts_samples[-1] - pts_samples[0])
        if not metadata["nominal_fps"] and measured_fps:
            metadata["nominal_fps"] = measured_fps
        metadata.update(
            {
                "decoded_width": first_frame.width,
                "decoded_height": first_frame.height,
                "decoded_pixel_format": first_frame.format.name,
                "measured_fps_from_pts": measured_fps,
                "transport": config.transport,
                "stream_id": config.stream_id,
                "url": config.rtsp_url(redact_password=True),
                "authenticated_and_decoded": True,
            }
        )
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    except BaseException as exc:
        print(f"Probe failed: {explain_exception(exc)}", file=sys.stderr)
        return 2
    finally:
        if container is not None:
            container.close()


class StreamWorker(threading.Thread):
    def __init__(
        self,
        config: StreamConfig,
        frames: LatestFrameBuffer,
        status: StatusStore,
        recorder: PacketRecorder,
        stop_event: threading.Event,
        worker_name: str = "camera",
    ) -> None:
        super().__init__(name=f"rtsp-reader-{safe_file_component(worker_name)}", daemon=True)
        self.config = config
        self.frames = frames
        self.status = status
        self.recorder = recorder
        self.stop_event = stop_event

    def run(self) -> None:
        backoff = 1.0
        reconnects = 0
        while not self.stop_event.is_set():
            container: av.container.InputContainer | None = None
            decoded = 0
            arrivals: deque[float] = deque()
            tracker = PtsBacklogTracker()
            excessive_backlog_frames = 0
            try:
                self.status.update(state="connecting", error="", reconnects=reconnects)
                LOG.info(
                    "Opening rtsp://%s:%d%s via RTSP/%s",
                    self.config.host,
                    self.config.port,
                    self.config.stream_path,
                    self.config.transport.upper(),
                )
                container = open_rtsp(self.config)
                stream = first_video_stream(container)
                configure_decoder(stream, self.config.decoder_threads)
                frame_recorder = getattr(self.recorder, "submit_frame", None)
                packet_recorder = getattr(self.recorder, "submit_packet", None)
                try:
                    frame_rate = float(stream.average_rate) if stream.average_rate else 25.0
                except (TypeError, ValueError, ZeroDivisionError):
                    frame_rate = 25.0
                frame_rate = max(1.0, min(frame_rate, 120.0))
                metadata = stream_metadata(stream)
                self.status.update(state="streaming", error="", **metadata)
                LOG.info(
                    "Connected: codec=%s profile=%s %sx%s nominal_fps=%.3f",
                    metadata["codec"],
                    metadata["profile"],
                    metadata["width"],
                    metadata["height"],
                    metadata["nominal_fps"],
                )
                backoff = 1.0

                for packet in container.demux(stream):
                    if self.stop_event.is_set():
                        break
                    # The normal low-latency CLI uses compressed packet remuxing.
                    # The Electron bridge supplies a frame recorder instead so it
                    # can burn the current person boxes into the saved video.
                    if not callable(frame_recorder) and callable(packet_recorder):
                        packet_recorder(packet, stream)
                    for frame in packet.decode():
                        if self.stop_event.is_set():
                            break
                        now = time.monotonic()
                        pts_seconds = (
                            float(frame.pts * frame.time_base)
                            if frame.pts is not None and frame.time_base is not None
                            else None
                        )
                        backlog = tracker.observe(pts_seconds, now)
                        if backlog is not None and backlog > self.config.max_backlog_seconds:
                            excessive_backlog_frames += 1
                        else:
                            excessive_backlog_frames = 0
                        if excessive_backlog_frames >= self.config.backlog_frames:
                            raise StreamBacklogError(
                                f"PTS backlog grew to {backlog:.3f}s; reconnecting to discard stale buffered media"
                            )

                        image = frame.to_ndarray(format="bgr24")
                        decoded += 1
                        arrivals.append(now)
                        cutoff = now - 2.0
                        while arrivals and arrivals[0] < cutoff:
                            arrivals.popleft()
                        decode_fps = (len(arrivals) - 1) / max(arrivals[-1] - arrivals[0], 1e-6) if len(arrivals) > 1 else 0.0
                        self.frames.publish(image, now, pts_seconds, backlog)
                        if callable(frame_recorder):
                            # The frame recorder copies before queuing. This keeps
                            # its annotation work isolated from the latest-frame
                            # preview buffer and the TensorRT worker.
                            frame_recorder(image, pts_seconds=pts_seconds, frame_rate=frame_rate)
                        self.status.update(
                            state="streaming",
                            error="",
                            decode_fps=decode_fps,
                            frames_decoded=decoded,
                            width=frame.width,
                            height=frame.height,
                        )

                if not self.stop_event.is_set():
                    raise EOFError("RTSP stream ended")
            except BaseException as exc:
                if self.stop_event.is_set():
                    break
                message = explain_exception(exc)
                self.recorder.stream_disconnected(message)
                reconnects += 1
                self.status.update(state="reconnecting", error=message, reconnects=reconnects)
                LOG.error("Stream error: %s", message)
            finally:
                if container is not None:
                    container.close()

            if self.stop_event.wait(backoff):
                break
            backoff = min(backoff * 2.0, 10.0)
        self.status.update(state="stopped")


def resize_for_display(image: np.ndarray, display_width: int, display_height: int) -> np.ndarray:
    """Downscale only the preview copy; never upscale or touch saved media."""
    source_height, source_width = image.shape[:2]
    width_scale = display_width / source_width if display_width > 0 else 1.0
    height_scale = display_height / source_height if display_height > 0 else 1.0
    scale = min(1.0, width_scale, height_scale)
    if scale >= 1.0:
        return image
    target = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
    return cv2.resize(image, target, interpolation=cv2.INTER_AREA)


def draw_overlay(
    image: np.ndarray,
    camera_label: str,
    status: StreamStatus,
    packet: FramePacket,
    overwritten: int,
    transport: str,
    recording: RecordingStatus,
) -> None:
    now = time.monotonic()
    display_age_ms = max(0.0, (now - packet.received_monotonic) * 1000.0)
    backlog_text = "n/a" if packet.pts_backlog_seconds is None else f"{packet.pts_backlog_seconds * 1000.0:.0f}ms"
    lines = [
        f"[{camera_label}] state={status.state}  {status.codec}/{status.profile} "
        f"{status.width}x{status.height}  decode {status.decode_fps:.1f} fps",
        f"transport={transport.upper()}  receiver->display={display_age_ms:.1f}ms  PTS backlog growth={backlog_text}",
        f"latest-frame overwrites={overwritten}  reconnects={status.reconnects}  record={recording.state}",
        "r start/stop recording  s full-resolution snapshot  q quit",
    ]
    for index, line in enumerate(lines):
        y = 28 + index * 27
        cv2.putText(image, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.64, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(image, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.64, (80, 255, 80), 1, cv2.LINE_AA)


def waiting_canvas(
    status: StreamStatus,
    camera_label: str,
    width: int = 960,
    height: int = 540,
) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    lines = [
        f"[{camera_label}] state: {status.state}",
        status.error or "Waiting for the first decoded frame...",
        "Press q to quit",
    ]
    for index, line in enumerate(lines):
        clipped = line[:130]
        color = (80, 255, 80) if index != 1 or not status.error else (80, 80, 255)
        cv2.putText(image, clipped, (24, 70 + index * 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    return image


def compose_side_by_side(
    panels: list[np.ndarray],
    separator_width: int,
    separator_color: tuple[int, int, int] = (48, 48, 48),
) -> np.ndarray:
    """Pad equally sized camera panels and join them with a visible divider."""
    if not panels:
        raise ValueError("at least one panel is required")
    if separator_width < 1:
        raise ValueError("separator_width must be >= 1")
    panel_height = max(panel.shape[0] for panel in panels)
    panel_width = max(panel.shape[1] for panel in panels)
    padded: list[np.ndarray] = []
    for panel in panels:
        height, width = panel.shape[:2]
        canvas = np.zeros((panel_height, panel_width, 3), dtype=np.uint8)
        x = (panel_width - width) // 2
        y = (panel_height - height) // 2
        canvas[y : y + height, x : x + width] = panel
        padded.append(canvas)
    separator = np.full((panel_height, separator_width, 3), separator_color, dtype=np.uint8)
    pieces: list[np.ndarray] = []
    for index, panel in enumerate(padded):
        if index:
            pieces.append(separator)
        pieces.append(panel)
    return np.hstack(pieces)


@dataclass
class CameraRuntime:
    source: CameraSource
    frames: LatestFrameBuffer
    status: StatusStore
    recorder: PacketRecorder
    snapshots: SnapshotWriter
    worker: StreamWorker
    last_sequence: int = 0
    current: FramePacket | None = None
    display_panel: np.ndarray | None = None
    last_display_sequence: int = 0
    last_display_state: str = ""
    last_display_error: str = ""


def run_preview(
    sources: list[CameraSource],
    outputs: dict[str, OutputConfig],
    display_width: int,
    display_height: int,
    window_name: str,
    no_display: bool,
    show_stats: bool,
    separator_width: int,
) -> int:
    stop_event = threading.Event()
    runtimes: list[CameraRuntime] = []
    for source in sources:
        output = outputs[source.key]
        output.record_dir.mkdir(parents=True, exist_ok=True)
        output.snapshot_dir.mkdir(parents=True, exist_ok=True)
        frames = LatestFrameBuffer()
        status = StatusStore()
        recorder = PacketRecorder(output, source.key)
        snapshots = SnapshotWriter(output, source.key)
        worker = StreamWorker(source.stream, frames, status, recorder, stop_event, source.key)
        runtimes.append(CameraRuntime(source, frames, status, recorder, snapshots, worker))
        recorder.start()
        snapshots.start()
        if output.record_on_start:
            recorder.request_start()
        worker.start()
    last_console_update = 0.0

    try:
        if not no_display:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        while not stop_event.is_set():
            received_frame = False
            for runtime in runtimes:
                packet = runtime.frames.wait_for_new(runtime.last_sequence, timeout=0.0)
                if packet is not None:
                    runtime.current = packet
                    runtime.last_sequence = packet.sequence
                    received_frame = True

            if no_display:
                now = time.monotonic()
                if now - last_console_update >= 1.0:
                    for runtime in runtimes:
                        snapshot = runtime.status.snapshot()
                        print(
                            f"camera={runtime.source.label!r} state={snapshot.state} codec={snapshot.codec} "
                            f"size={snapshot.width}x{snapshot.height} decode_fps={snapshot.decode_fps:.1f} "
                            f"overwrites={runtime.frames.overwritten} reconnects={snapshot.reconnects} "
                            f"record={runtime.recorder.status().state} error={snapshot.error or '-'}"
                        )
                    last_console_update = now
                if not received_frame:
                    stop_event.wait(0.01)
                continue

            if not received_frame:
                stop_event.wait(0.005)

            panels: list[np.ndarray] = []
            for runtime in runtimes:
                snapshot = runtime.status.snapshot()
                current = runtime.current
                if current is None:
                    if (
                        runtime.display_panel is None
                        or snapshot.state != runtime.last_display_state
                        or snapshot.error != runtime.last_display_error
                    ):
                        runtime.display_panel = waiting_canvas(
                            snapshot,
                            runtime.source.label,
                            display_width or 960,
                            display_height or 540,
                        )
                        runtime.last_display_state = snapshot.state
                        runtime.last_display_error = snapshot.error
                    panel = runtime.display_panel
                else:
                    if (
                        runtime.display_panel is None
                        or current.sequence != runtime.last_display_sequence
                        or snapshot.state != runtime.last_display_state
                    ):
                        panel = resize_for_display(current.image, display_width, display_height)
                        if show_stats:
                            # Overlay text mutates its image. Copy only when no display
                            # resize already produced an independent buffer.
                            if panel is current.image:
                                panel = panel.copy()
                            draw_overlay(
                                panel,
                                runtime.source.label,
                                snapshot,
                                current,
                                runtime.frames.overwritten,
                                runtime.source.stream.transport,
                                runtime.recorder.status(),
                            )
                        runtime.display_panel = panel
                        runtime.last_display_sequence = current.sequence
                        runtime.last_display_state = snapshot.state
                        runtime.last_display_error = snapshot.error
                    panel = runtime.display_panel
                panels.append(panel)
            display = compose_side_by_side(panels, separator_width)
            cv2.imshow(window_name, display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                stop_event.set()
            elif key == ord("r"):
                recording_active = any(
                    runtime.recorder.status().state in {"armed", "starting", "recording", "stopping"}
                    for runtime in runtimes
                )
                for runtime in runtimes:
                    if recording_active:
                        runtime.recorder.request_stop()
                    else:
                        runtime.recorder.request_start()
            elif key == ord("s"):
                for runtime in runtimes:
                    if runtime.current is None:
                        LOG.warning("%s snapshot ignored: no decoded frame is available yet", runtime.source.label)
                    else:
                        runtime.snapshots.submit(runtime.current.image)
    except KeyboardInterrupt:
        stop_event.set()
    except cv2.error as exc:
        LOG.error("OpenCV display failed: %s. Use --no-display for a headless test.", exc)
        stop_event.set()
        return 3
    finally:
        stop_event.set()
        for runtime in runtimes:
            runtime.worker.join(timeout=runtime.source.stream.read_timeout + 2.0)
        for runtime in runtimes:
            runtime.recorder.shutdown()
            runtime.snapshots.shutdown()
        if not no_display:
            cv2.destroyAllWindows()
    return 0


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value not in (None, "") else default


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, yes/no, on/off or 1/0")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual-camera low-latency RTSP preview using PyAV/FFmpeg and OpenCV")
    shared_port = env_int("CAMERA_RTSP_PORT", 554)
    shared_username = os.getenv("CAMERA_USERNAME", "admin")
    shared_channel = env_int("CAMERA_CHANNEL", 1)
    parser.add_argument(
        "--camera1-ip",
        "--ip",
        dest="camera1_ip",
        default=os.getenv("CAMERA1_IP") or os.getenv("CAMERA_IP") or "192.0.2.10",
        help="first camera IP or host; --ip remains as a backward-compatible alias",
    )
    parser.add_argument(
        "--camera2-ip",
        default=os.getenv("CAMERA2_IP", "192.0.2.11"),
        help="second camera IP or host",
    )
    parser.add_argument(
        "--camera1-port",
        "--port",
        dest="camera1_port",
        type=int,
        default=env_int("CAMERA1_RTSP_PORT", shared_port),
        help="first camera RTSP port; --port remains as a backward-compatible alias",
    )
    parser.add_argument("--camera2-port", type=int, default=env_int("CAMERA2_RTSP_PORT", shared_port))
    parser.add_argument(
        "--camera1-username",
        "--username",
        dest="camera1_username",
        default=os.getenv("CAMERA1_USERNAME") or shared_username,
        help="first camera RTSP username; --username remains as a backward-compatible alias",
    )
    parser.add_argument(
        "--camera2-username",
        default=os.getenv("CAMERA2_USERNAME") or shared_username,
        help="second camera RTSP username",
    )
    parser.add_argument(
        "--camera1-channel",
        "--channel",
        dest="camera1_channel",
        type=int,
        default=env_int("CAMERA1_CHANNEL", shared_channel),
        help="first physical channel number; --channel remains as a backward-compatible alias",
    )
    parser.add_argument("--camera2-channel", type=int, default=env_int("CAMERA2_CHANNEL", shared_channel))
    parser.add_argument("--camera1-label", default=os.getenv("CAMERA1_LABEL", "Camera 1"))
    parser.add_argument("--camera2-label", default=os.getenv("CAMERA2_LABEL", "Camera 2"))
    parser.add_argument(
        "--stream",
        choices=tuple(STREAM_INDEX),
        default=os.getenv("CAMERA_STREAM", "main"),
        help="shared stream selection for both cameras; main=101 is the default",
    )
    parser.add_argument(
        "--camera1-path",
        "--path",
        dest="camera1_path",
        default=os.getenv("CAMERA1_RTSP_PATH") or os.getenv("CAMERA_RTSP_PATH") or None,
        help="override first camera RTSP path; --path remains as a backward-compatible alias",
    )
    parser.add_argument("--camera2-path", default=os.getenv("CAMERA2_RTSP_PATH") or None)
    parser.add_argument(
        "--transport",
        choices=("tcp", "udp"),
        default=os.getenv("CAMERA_TRANSPORT", "tcp").lower(),
        help="RTP transport; TCP is the stable default",
    )
    parser.add_argument("--connect-timeout", type=float, default=env_float("CAMERA_CONNECT_TIMEOUT", 5.0))
    parser.add_argument("--read-timeout", type=float, default=env_float("CAMERA_READ_TIMEOUT", 3.0))
    parser.add_argument("--analyzeduration-us", type=int, default=env_int("CAMERA_ANALYZEDURATION_US", 100_000))
    parser.add_argument("--probesize", type=int, default=env_int("CAMERA_PROBESIZE", 65_536))
    parser.add_argument("--buffer-size", type=int, default=env_int("CAMERA_BUFFER_SIZE", 131_072))
    parser.add_argument("--decoder-threads", type=int, default=env_int("CAMERA_DECODER_THREADS", 0))
    parser.add_argument(
        "--hwaccel",
        choices=("none", "cuda", "d3d11va", "dxva2", "qsv"),
        default=os.getenv("CAMERA_HWACCEL", "none").lower(),
        help="optional FFmpeg hardware decoder; software fallback remains enabled",
    )
    parser.add_argument("--max-backlog", type=float, default=env_float("CAMERA_MAX_BACKLOG", 1.5))
    parser.add_argument("--backlog-frames", type=int, default=env_int("CAMERA_BACKLOG_FRAMES", 5))
    parser.add_argument(
        "--display-width",
        type=int,
        default=env_int("CAMERA_DISPLAY_WIDTH", 0),
        help="preview-only maximum width; 0 keeps native width",
    )
    parser.add_argument(
        "--display-height",
        type=int,
        default=env_int("CAMERA_DISPLAY_HEIGHT", 0),
        help="preview-only maximum height; 0 keeps native height",
    )
    parser.add_argument(
        "--separator-width",
        type=int,
        default=env_int("CAMERA_SEPARATOR_WIDTH", 16),
        help="pixel width of the divider between camera panels",
    )
    parser.add_argument(
        "--camera1-output-dir",
        type=Path,
        default=Path(
            os.getenv("CAMERA1_OUTPUT_DIR")
            or os.getenv("CAMERA1_RECORD_DIR")
            or os.getenv("CAMERA1_SNAPSHOT_DIR")
            or os.getenv("CAMERA_RECORD_DIR")
            or os.getenv("CAMERA_OUTPUT_DIR")
            or Path(__file__).resolve().parent / "outputs" / "camera1"
        ),
        help="first camera recording and snapshot directory",
    )
    parser.add_argument(
        "--camera2-output-dir",
        type=Path,
        default=Path(
            os.getenv("CAMERA2_OUTPUT_DIR")
            or os.getenv("CAMERA2_RECORD_DIR")
            or os.getenv("CAMERA2_SNAPSHOT_DIR")
            or os.getenv("CAMERA_RECORD_DIR")
            or os.getenv("CAMERA_OUTPUT_DIR")
            or Path(__file__).resolve().parent / "outputs" / "camera2"
        ),
        help="second camera recording and snapshot directory",
    )
    parser.add_argument(
        "--output-dir",
        "--record-dir",
        dest="shared_output_dir",
        type=Path,
        default=None,
        help="backward-compatible override: use one recording directory for both cameras",
    )
    parser.add_argument(
        "--snapshot-dir",
        dest="shared_snapshot_dir",
        type=Path,
        default=None,
        help="backward-compatible override: use one snapshot directory for both cameras",
    )
    parser.add_argument(
        "--snapshot-format",
        choices=("jpg", "png"),
        default=os.getenv("CAMERA_SNAPSHOT_FORMAT", "png").lower(),
    )
    parser.add_argument("--jpeg-quality", type=int, default=env_int("CAMERA_JPEG_QUALITY", 95))
    parser.add_argument("--png-compression", type=int, default=env_int("CAMERA_PNG_COMPRESSION", 3))
    parser.add_argument(
        "--record-container",
        choices=("mp4", "mkv"),
        default=os.getenv("CAMERA_RECORD_CONTAINER", "mp4").lower(),
        help="MP4 for H.264 compatibility; MKV is the safer H.265 choice",
    )
    parser.add_argument(
        "--record-queue-packets",
        type=int,
        default=env_int("CAMERA_RECORD_QUEUE_PACKETS", 1024),
    )
    parser.add_argument(
        "--record-on-start",
        action=argparse.BooleanOptionalAction,
        default=env_bool("CAMERA_RECORD_ON_START", False),
        help="start recording as soon as the first keyframe arrives",
    )
    parser.add_argument(
        "--show-stats",
        action=argparse.BooleanOptionalAction,
        default=env_bool("CAMERA_SHOW_STATS", True),
    )
    parser.add_argument(
        "--window-name",
        default=os.getenv("CAMERA_WINDOW_NAME", "Dual-camera low-latency preview"),
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="authenticate and decode both streams sequentially, print metadata and exit",
    )
    parser.add_argument("--no-display", action="store_true", help="decode without GUI; stop with Ctrl+C")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    for option, host in (("--camera1-ip", args.camera1_ip), ("--camera2-ip", args.camera2_ip)):
        if not host or "://" in host:
            raise ValueError(f"{option} must be a host/IP only, not a URL")
    for option, port in (("--camera1-port", args.camera1_port), ("--camera2-port", args.camera2_port)):
        if not 1 <= port <= 65535:
            raise ValueError(f"{option} must be between 1 and 65535")
    for option, channel in (
        ("--camera1-channel", args.camera1_channel),
        ("--camera2-channel", args.camera2_channel),
    ):
        if channel < 1:
            raise ValueError(f"{option} must be >= 1")
    if args.read_timeout <= 0 or args.connect_timeout <= 0:
        raise ValueError("timeouts must be positive")
    if args.probesize < 32:
        raise ValueError("--probesize is too small")
    if args.backlog_frames < 1:
        raise ValueError("--backlog-frames must be >= 1")
    if args.display_width < 0 or args.display_height < 0:
        raise ValueError("display dimensions must be >= 0")
    if args.separator_width < 1:
        raise ValueError("--separator-width must be >= 1")
    if not 1 <= args.jpeg_quality <= 100:
        raise ValueError("--jpeg-quality must be between 1 and 100")
    if not 0 <= args.png_compression <= 9:
        raise ValueError("--png-compression must be between 0 and 9")
    if args.record_queue_packets < 16:
        raise ValueError("--record-queue-packets must be >= 16")


def build_sources_and_outputs(
    args: argparse.Namespace,
    camera1_password: str,
    camera2_password: str,
) -> tuple[list[CameraSource], dict[str, OutputConfig]]:
    """Build runtime configuration without performing I/O or prompting.

    Keeping this deterministic lets the authenticated local bridge reuse the
    exact CLI/env configuration while retaining the existing command-line UI.
    """
    common_stream_options = {
        "stream_name": args.stream,
        "transport": args.transport,
        "connect_timeout": args.connect_timeout,
        "read_timeout": args.read_timeout,
        "analyzeduration_us": args.analyzeduration_us,
        "probesize": args.probesize,
        "buffer_size": args.buffer_size,
        "decoder_threads": args.decoder_threads,
        "hwaccel": args.hwaccel,
        "max_backlog_seconds": args.max_backlog,
        "backlog_frames": args.backlog_frames,
    }
    sources = [
        CameraSource(
            key="camera1",
            label=args.camera1_label,
            stream=StreamConfig(
                host=args.camera1_ip,
                port=args.camera1_port,
                username=args.camera1_username,
                password=camera1_password,
                channel=args.camera1_channel,
                path=args.camera1_path,
                **common_stream_options,
            ),
        ),
        CameraSource(
            key="camera2",
            label=args.camera2_label,
            stream=StreamConfig(
                host=args.camera2_ip,
                port=args.camera2_port,
                username=args.camera2_username,
                password=camera2_password,
                channel=args.camera2_channel,
                path=args.camera2_path,
                **common_stream_options,
            ),
        ),
    ]
    output_options = {
        "snapshot_format": args.snapshot_format,
        "jpeg_quality": args.jpeg_quality,
        "png_compression": args.png_compression,
        "record_container": args.record_container,
        "record_queue_packets": args.record_queue_packets,
        "record_on_start": args.record_on_start,
    }
    camera1_output_dir = (args.shared_output_dir or args.camera1_output_dir).expanduser()
    camera2_output_dir = (args.shared_output_dir or args.camera2_output_dir).expanduser()
    camera1_snapshot_dir = (args.shared_snapshot_dir or camera1_output_dir).expanduser()
    camera2_snapshot_dir = (args.shared_snapshot_dir or camera2_output_dir).expanduser()
    outputs = {
        "camera1": OutputConfig(
            record_dir=camera1_output_dir,
            snapshot_dir=camera1_snapshot_dir,
            **output_options,
        ),
        "camera2": OutputConfig(
            record_dir=camera2_output_dir,
            snapshot_dir=camera2_snapshot_dir,
            **output_options,
        ),
    }
    return sources, outputs


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s: %(message)s",
    )
    av.logging.set_level(av.logging.DEBUG if args.debug else av.logging.ERROR)
    try:
        validate_args(args)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    shared_password = os.getenv("CAMERA_PASSWORD")
    camera1_password = os.getenv("CAMERA1_PASSWORD") or shared_password
    camera2_password = os.getenv("CAMERA2_PASSWORD") or shared_password
    if not camera1_password:
        camera1_password = getpass.getpass("Camera 1 password: ")
    if not camera2_password:
        camera2_password = getpass.getpass("Camera 2 password: ")

    sources, outputs = build_sources_and_outputs(args, camera1_password, camera2_password)
    for source in sources:
        LOG.info(
            "%s target: rtsp://%s:%d%s",
            source.label,
            source.stream.host,
            source.stream.port,
            source.stream.stream_path,
        )
        source_output = outputs[source.key]
        LOG.info("%s recording directory: %s", source.label, source_output.record_dir.resolve())
        LOG.info("%s snapshot-only directory: %s", source.label, source_output.snapshot_dir.resolve())
    if args.probe_only:
        results = []
        for source in sources:
            print(f"--- {source.label} ---")
            results.append(probe_stream(source.stream))
        return max(results)
    return run_preview(
        sources,
        outputs,
        args.display_width,
        args.display_height,
        args.window_name,
        args.no_display,
        args.show_stats,
        args.separator_width,
    )


if __name__ == "__main__":
    raise SystemExit(main())
