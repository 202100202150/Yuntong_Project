from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2

from .alerts import send_webhook
from .calibration import CameraCalibration, load_calibration
from .classifier import RoiClassifier
from .config import AppConfig, CameraConfig
from .geometry import StereoAssociator
from .models import Detection2D, TrackState
from .motion import MotionDetector, Tracker2D
from .recorder import SegmentRecorder
from .state import CameraHealth, FrameHub, TrackBroker
from .storage import EventStore, should_persist
from .tracking3d import Tracker3D
from .video import FramePacket, create_video_source


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class FrameAnalysis:
    packet: FramePacket
    detections: list[Detection2D]


class CameraWorker(threading.Thread):
    def __init__(
        self,
        camera: CameraConfig,
        calibration: CameraCalibration,
        app_config: AppConfig,
        output: queue.Queue,
        frame_hub: FrameHub,
        health: CameraHealth,
        recorder: SegmentRecorder,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name=f"camera-{camera.camera_id}", daemon=True)
        self.camera = camera
        self.calibration = calibration
        self.app_config = app_config
        self.output = output
        self.frame_hub = frame_hub
        self.health = health
        self.recorder = recorder
        self.stop_event = stop_event
        self.detector = MotionDetector(
            camera.camera_id, app_config.motion, camera.mask_polygons
        )
        self.tracker = Tracker2D(camera.camera_id)
        self.classifier = RoiClassifier(app_config.classifier)

    def _annotate(
        self, frame, raw: list[Detection2D], confirmed: list[Detection2D]
    ):
        output = frame.copy()
        confirmed_ids = {item.track_id for item in confirmed}
        for detection in raw:
            x1, y1, x2, y2 = [int(round(v)) for v in detection.bbox_xyxy]
            is_confirmed = detection.track_id in confirmed_ids
            color = (30, 220, 30) if is_confirmed else (0, 180, 255)
            cv2.rectangle(output, (x1, y1), (x2, y2), color, 1)
            if detection.track_id:
                label = detection.track_id
                if is_confirmed:
                    label += f" {detection.object_class.value}"
                cv2.putText(
                    output,
                    label,
                    (x1, max(12, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    color,
                    1,
                    cv2.LINE_AA,
                )
        return output

    def run(self) -> None:
        while not self.stop_event.is_set():
            source = None
            try:
                self.health.status = "connecting"
                source = create_video_source(self.camera, self.calibration)
                self.health.status = "online"
                self.health.last_error = None
                while not self.stop_event.is_set():
                    packet = source.read()
                    if packet is None:
                        raise RuntimeError("Video source returned no frame")
                    stabilized, raw = self.detector.detect(
                        packet.image, packet.timestamp_s
                    )
                    confirmed = self.tracker.update(raw, packet.timestamp_s)
                    for detection in confirmed:
                        self.classifier.classify(stabilized, detection)
                    annotated = self._annotate(stabilized, raw, confirmed)
                    self.frame_hub.put(self.camera.camera_id, annotated)
                    if self.camera.record:
                        self.recorder.write(
                            self.camera.camera_id,
                            packet.timestamp_s,
                            packet.image,
                            self.camera.fps,
                        )
                    self.health.frames += 1
                    self.health.detections += len(confirmed)
                    self.health.last_frame_epoch_s = time.time()
                    analysis = FrameAnalysis(packet, confirmed)
                    try:
                        self.output.put(analysis, timeout=0.1)
                    except queue.Full:
                        try:
                            self.output.get_nowait()
                        except queue.Empty:
                            pass
                        self.output.put_nowait(analysis)
            except Exception as exc:
                self.health.status = "error"
                self.health.last_error = str(exc)
                LOGGER.exception("Camera worker %s failed", self.camera.camera_id)
                self.stop_event.wait(2.0)
            finally:
                if source is not None:
                    source.close()
        self.health.status = "stopped"


class StereoWorker(threading.Thread):
    def __init__(
        self,
        camera_ids: tuple[str, str],
        input_queue: queue.Queue,
        associator: StereoAssociator,
        tracker: Tracker3D,
        broker: TrackBroker,
        event_store: EventStore,
        recorder: SegmentRecorder,
        webhook_url: str | None,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="stereo-worker", daemon=True)
        self.camera_ids = camera_ids
        self.input_queue = input_queue
        self.associator = associator
        self.tracker = tracker
        self.broker = broker
        self.event_store = event_store
        self.recorder = recorder
        self.webhook_url = webhook_url
        self.stop_event = stop_event
        self.pending: dict[str, deque[FrameAnalysis]] = {
            camera_id: deque(maxlen=8) for camera_id in camera_ids
        }
        self.sync_residual_ms: deque[float] = deque(maxlen=1000)
        self.reprojection_errors_px: deque[float] = deque(maxlen=1000)
        self.stereo_pairs = 0
        self.measurements = 0

    def _pop_pair(self) -> tuple[FrameAnalysis, FrameAnalysis] | None:
        first_queue = self.pending[self.camera_ids[0]]
        second_queue = self.pending[self.camera_ids[1]]
        tolerance_s = self.associator.config.sync_tolerance_ms / 1000.0
        while first_queue and second_queue:
            first = first_queue[0]
            best_index = min(
                range(len(second_queue)),
                key=lambda index: abs(
                    second_queue[index].packet.timestamp_s - first.packet.timestamp_s
                ),
            )
            second = second_queue[best_index]
            difference = second.packet.timestamp_s - first.packet.timestamp_s
            if abs(difference) <= tolerance_s:
                first_queue.popleft()
                del second_queue[best_index]
                self.sync_residual_ms.append(abs(difference) * 1000.0)
                return first, second
            if first.packet.timestamp_s < second_queue[0].packet.timestamp_s:
                first_queue.popleft()
            else:
                second_queue.popleft()
        return None

    def _process(self, first: FrameAnalysis, second: FrameAnalysis) -> None:
        self.stereo_pairs += 1
        measurements = self.associator.associate(
            first.detections, second.detections
        )
        self.measurements += len(measurements)
        self.reprojection_errors_px.extend(
            measurement.reprojection_error_px for measurement in measurements
        )
        timestamp_s = (first.packet.timestamp_s + second.packet.timestamp_s) * 0.5
        for event in self.tracker.update(measurements, timestamp_s):
            payload = event.to_dict()
            self.broker.publish(payload)
            if should_persist(event):
                if event.metadata.get("justConfirmed"):
                    event.metadata["recordingSegments"] = self.recorder.current_paths()
                    payload = event.to_dict()
                self.event_store.add(event)
            if event.state == TrackState.CONFIRMED and event.metadata.get(
                "justConfirmed"
            ):
                threading.Thread(
                    target=send_webhook,
                    args=(self.webhook_url, payload),
                    daemon=True,
                ).start()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                analysis: FrameAnalysis = self.input_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if analysis.packet.camera_id not in self.pending:
                continue
            self.pending[analysis.packet.camera_id].append(analysis)
            while pair := self._pop_pair():
                self._process(*pair)

    def health(self) -> dict:
        residuals = sorted(self.sync_residual_ms)
        if residuals:
            index = min(len(residuals) - 1, int(0.95 * len(residuals)))
            p95 = residuals[index]
        else:
            p95 = None
        reprojections = sorted(self.reprojection_errors_px)
        if reprojections:
            reprojection_index = min(
                len(reprojections) - 1, int(0.95 * len(reprojections))
            )
            reprojection_p95 = reprojections[reprojection_index]
        else:
            reprojection_p95 = None
        return {
            "stereoPairs": self.stereo_pairs,
            "measurements": self.measurements,
            "syncResidualP95Ms": round(p95, 3) if p95 is not None else None,
            "reprojectionErrorP95Px": (
                round(reprojection_p95, 3)
                if reprojection_p95 is not None
                else None
            ),
        }


class MaintenanceWorker(threading.Thread):
    def __init__(
        self,
        event_store: EventStore,
        recorder: SegmentRecorder,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="maintenance-worker", daemon=True)
        self.event_store = event_store
        self.recorder = recorder
        self.stop_event = stop_event

    def run(self) -> None:
        while not self.stop_event.wait(3600.0):
            try:
                self.event_store.cleanup()
                self.recorder.cleanup()
            except Exception:
                LOGGER.exception("Retention maintenance failed")


class PipelineRuntime:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        calibrations = {
            camera.camera_id: load_calibration(camera.calibration)
            for camera in config.cameras
            if camera.enabled
        }
        for camera in config.cameras:
            if camera.enabled and calibrations[camera.camera_id].camera_id != camera.camera_id:
                raise ValueError(
                    f"Calibration camera_id mismatch for {camera.camera_id}"
                )
        camera_ids = config.camera_ids
        self.frame_hub = FrameHub()
        self.broker = TrackBroker()
        self.event_store = EventStore(
            config.storage.database_path, config.storage.retention_days
        )
        self.recorder = SegmentRecorder(
            config.storage.recording_directory,
            config.storage.segment_seconds,
            config.storage.retention_days,
        )
        self.tracker = Tracker3D(config.tracking)
        self.associator = StereoAssociator(
            calibrations[camera_ids[0]],
            calibrations[camera_ids[1]],
            config.stereo,
        )
        self.stop_event = threading.Event()
        self.analysis_queue: queue.Queue = queue.Queue(maxsize=32)
        self.camera_health = {
            camera_id: CameraHealth(camera_id) for camera_id in camera_ids
        }
        self.camera_workers = [
            CameraWorker(
                camera,
                calibrations[camera.camera_id],
                config,
                self.analysis_queue,
                self.frame_hub,
                self.camera_health[camera.camera_id],
                self.recorder,
                self.stop_event,
            )
            for camera in config.cameras
            if camera.enabled
        ]
        self.stereo_worker = StereoWorker(
            (camera_ids[0], camera_ids[1]),
            self.analysis_queue,
            self.associator,
            self.tracker,
            self.broker,
            self.event_store,
            self.recorder,
            config.service.webhook_url,
            self.stop_event,
        )
        self.maintenance_worker = MaintenanceWorker(
            self.event_store, self.recorder, self.stop_event
        )
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.event_store.cleanup()
        self.recorder.cleanup()
        for worker in self.camera_workers:
            worker.start()
        self.stereo_worker.start()
        self.maintenance_worker.start()

    def stop(self) -> None:
        if not self.started:
            self.event_store.close()
            return
        self.stop_event.set()
        for worker in self.camera_workers:
            worker.join(timeout=3.0)
        self.stereo_worker.join(timeout=3.0)
        self.maintenance_worker.join(timeout=3.0)
        self.recorder.close()
        self.event_store.close()
        self.started = False

    def health(self) -> dict:
        camera_health = [health.to_dict() for health in self.camera_health.values()]
        online = all(item["status"] == "online" for item in camera_health)
        return {
            "status": "healthy" if online else "degraded",
            "cameras": camera_health,
            "stereo": self.stereo_worker.health(),
            "classifierConfigured": bool(self.config.classifier.model_path),
            "classifierReady": all(
                worker.classifier.ready for worker in self.camera_workers
            ),
            "calibrationVersions": {
                self.associator.first.camera_id: self.associator.first.version,
                self.associator.second.camera_id: self.associator.second.version,
            },
        }
