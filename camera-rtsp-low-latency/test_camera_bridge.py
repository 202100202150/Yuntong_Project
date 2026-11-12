from __future__ import annotations

from http import HTTPStatus
from http.server import ThreadingHTTPServer
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import av
import cv2
import numpy as np

from camera_bridge import (
    CameraBridgeService,
    DetectionCoordinator,
    DetectionResult,
    DroneImageTracker,
    DroneObject,
    EncodedPreview,
    PreviewEncoder,
    OverlayVideoRecorder,
    draw_drone_objects,
    make_handler,
    validate_png,
)
from drone_tracking import DronePositionTracker
from camera_low_latency import (
    CameraSource,
    LatestFrameBuffer,
    OutputConfig,
    PacketRecorder,
    SnapshotWriter,
    StreamConfig,
)


TOKEN = "unit-test-token-with-at-least-32-characters"


def png_bytes(width: int = 16, height: int = 12) -> bytes:
    ok, encoded = cv2.imencode(".png", np.zeros((height, width, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


class AlgorithmTests(unittest.TestCase):
    def test_small_fast_drone_keeps_id_without_box_overlap(self) -> None:
        tracker = DroneImageTracker()
        first, = tracker.update([((20.0, 24.0, 28.0, 30.0), 0.9)], 1920)
        second, = tracker.update([((42.0, 26.0, 50.0, 32.0), 0.88)], 1920)
        self.assertEqual(first.object_id, second.object_id)

    def test_3d_track_moves_in_all_axes_and_ignores_single_bad_measurement(self) -> None:
        tracker = DronePositionTracker()

        def observation(x, y, z, left="track-1", right="track-1"):
            return {"camera1Id": left, "camera2Id": right, "x": x, "y": y, "z": z, "class": "drone"}

        self.assertEqual(tracker.update([observation(0, 0, 20)], 1, 1), [])
        first, = tracker.update([observation(0.5, 0.6, 20.8)], 1.1, 1.11)
        second, = tracker.update([observation(1.0, 1.2, 21.6, left="track-2")], 1.2, 1.21)
        self.assertEqual(first["id"], second["id"])
        for axis in "xyz":
            self.assertGreater(second[axis], first[axis])
        self.assertGreater(second["azimuthDeg"], first["azimuthDeg"])
        self.assertGreater(second["elevationDeg"], first["elevationDeg"])
        outlier, = tracker.update([observation(1.1, 1.2, 85, left="track-2")], 1.3, 1.31)
        self.assertEqual([outlier[a] for a in "xyz"], [second[a] for a in "xyz"])
        held, = tracker.update([], float("-inf"), 1.41)
        self.assertEqual(held["trackingState"], "held")
        self.assertEqual(tracker.update([], float("-inf"), 1.70), [])

    def test_draw_drone_objects_burns_box_and_drone_label_into_frame(self) -> None:
        """The frame handed to a recorder/snapshot must contain the annotation pixels."""
        image = np.zeros((80, 120, 3), dtype=np.uint8)
        detected = DroneObject("track-1", 0.875, (20.0, 24.0, 90.0, 68.0))

        draw_drone_objects(image, (detected,))

        # OpenCV stores the selected green color as BGR.  Checking a small
        # neighborhood along each edge avoids coupling this test to the exact
        # anti-aliased line thickness while still proving that the box was
        # burned into the pixels (rather than merely returned as metadata).
        green = np.logical_and.reduce(
            (
                image[:, :, 1] > 150,
                image[:, :, 1] > image[:, :, 0] * 2,
                image[:, :, 1] > image[:, :, 2] * 1.5,
            )
        )
        self.assertTrue(bool(green[24:29, 20:91].any()))
        self.assertTrue(bool(green[64:69, 20:91].any()))
        self.assertTrue(bool(green[24:69, 20:25].any()))
        self.assertTrue(bool(green[24:69, 85:91].any()))
        # The label is rendered above the box when there is room.  Its dark
        # text and filled green background must leave non-black pixels there.
        self.assertGreater(int(image[0:24, 20:90].sum()), 0)


class _FakeDetector:
    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def detect(self, image: np.ndarray) -> list[tuple[tuple[float, float, float, float], float]]:
        return [((1.0, 1.0, float(image.shape[1] - 1), float(image.shape[0] - 1)), 0.9)]


class SchedulingTests(unittest.TestCase):
    def _preview_service(self, width=32, height=24):
        service = CameraBridgeService.__new__(CameraBridgeService)
        service._stop_event = threading.Event()
        service._preview_pair_lock = threading.Lock()
        service._preview_epoch = "b" * 32
        service._preview_pair = None
        service._preview_pair_started_at = 0.0
        frames = {key: LatestFrameBuffer(history_size=4) for key in ("camera1", "camera2")}
        service.cameras = {key: SimpleNamespace(frames=buffer) for key, buffer in frames.items()}
        service.previews = {key: PreviewEncoder(width, height, 90, 30) for key in frames}
        service.detection = DetectionCoordinator(frames, _FakeDetector())
        return service, frames

    def test_paired_preview_uses_newest_matching_packets_and_drops_backlogged_frames(self) -> None:
        service, frames = self._preview_service()
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        # At t=10, camera2's newest receiver timestamp is close, but its
        # content is ~1 second older after accounting for RTSP backlog.
        frames["camera1"].publish(image, 9.90, None, None)
        frames["camera1"].publish(image, 9.98, None, None)
        frames["camera2"].publish(image, 9.93, None, 0.01)
        frames["camera2"].publish(image, 9.99, None, 1.0)
        def fetch(after1=0, after2=0, epoch=""):
            return service.preview_pair(
                after_camera1=after1, after_camera2=after2, wait_timeout=0, epoch=epoch,
            )
        with patch("camera_bridge.time.monotonic", return_value=10.0):
            pair = fetch()
            assert pair is not None
            self.assertEqual((pair.camera1.sequence, pair.camera2.sequence), (2, 1))
            self.assertAlmostEqual(pair.skew_seconds, 0.06)
            self.assertIsNone(fetch(2, 1))
            # A viewer whose sidecar restarted can recover even with old
            # cursors much higher than either new sequence.
            self.assertEqual(fetch(100, 101, "a" * 32), pair)
        frames["camera2"].clear()
        with patch("camera_bridge.time.monotonic", return_value=10.04):
            self.assertIsNone(fetch())  # Cached pair cannot survive a disconnect.
        frames["camera1"].publish(image, 10.05, None, None)
        frames["camera2"].publish(image, 10.07, None, None)
        with patch("camera_bridge.time.monotonic", return_value=10.08):
            recovered = fetch(2, 1)
            assert recovered is not None
            self.assertEqual((recovered.camera1.sequence, recovered.camera2.sequence), (3, 3))
            self.assertAlmostEqual(recovered.skew_seconds, 0.02)

    def test_paired_preview_keeps_boxes_with_their_original_frames(self) -> None:
        service, frames = self._preview_service(160, 100)
        service.detection.request_start()
        for sequence, x in enumerate((30, 110), start=1):
            image = np.zeros((100, 160, 3), dtype=np.uint8)
            image[57:63, x - 3:x + 3] = 255
            for key, buffer in frames.items():
                buffer.publish(image, 9.85 + 0.06 * sequence, None, None)

        def complete(camera, index, objects):
            packet = frames[camera].recent_snapshots()[index]
            service.detection._result_history[camera].append(DetectionResult(
                packet.sequence, packet.received_monotonic, 10.0, packet.image, objects,
            ))

        def fetch(after=0):
            return service.preview_pair(
                after_camera1=after, after_camera2=after, wait_timeout=0, epoch="",
            )

        first_box = (DroneObject("track-1", 0.9, (20, 48, 40, 72)),)
        complete("camera1", 0, first_box)
        complete("camera2", 0, ())  # Inference can complete without a detection.
        with patch("camera_bridge.time.monotonic", return_value=10.0):
            first = fetch()
            assert first is not None
            self.assertEqual((first.camera1.sequence, first.camera2.sequence), (1, 1))
            image = cv2.imdecode(np.frombuffer(first.camera1.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertGreater(int(image[60, 30].min()), 200)
            self.assertLess(int(image[60, 110].max()), 15)
            blue, green, red = map(int, image[72, 30])
            self.assertGreater(green, max(blue, red) + 50)
            self.assertIsNone(fetch(1))

        second_box = (DroneObject("track-1", 0.9, (100, 48, 120, 72)),)
        complete("camera1", 1, second_box)
        with patch("camera_bridge.time.monotonic", return_value=10.04):
            self.assertIsNone(fetch(1))  # Wait while the companion is in inference.
        complete("camera2", 1, second_box)
        with patch("camera_bridge.time.monotonic", return_value=10.05):
            second = fetch(1)
            assert second is not None
            self.assertEqual((second.camera1.sequence, second.camera2.sequence), (2, 2))
            self.assertEqual((second.camera1.detection_sequence, second.camera2.detection_sequence), (2, 2))

        service.detection.request_stop()
        with patch("camera_bridge.time.monotonic", return_value=10.10):
            raw = fetch()
            assert raw is not None
            self.assertEqual((raw.camera1.detection_sequence, raw.camera2.detection_sequence), (0, 0))

    def test_single_preview_never_overlays_a_stale_detection_on_new_video(self) -> None:
        service, frames = self._preview_service()
        service.detection.request_start()
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        buffer = frames["camera1"]
        buffer.publish(image, 9.90, None, None)
        buffer.publish(image, 9.99, None, None)
        drone = DroneObject("track-1", 0.9, (4, 6, 12, 18))
        service.detection._result_history["camera1"].append(DetectionResult(1, 9.90, 9.95, image, (drone,)))
        with patch("camera_bridge.time.monotonic", return_value=10.0):
            first = service.preview_jpeg("camera1")
            assert first is not None
            self.assertEqual((first.sequence, first.detection_sequence), (1, 1))
            self.assertIsNone(service.preview_jpeg("camera1", after_sequence=1))

        buffer.publish(image, 10.31, None, None)
        with patch("camera_bridge.time.monotonic", return_value=10.32):
            raw = service.preview_jpeg("camera1", after_sequence=1)
            assert raw is not None
            self.assertEqual((raw.sequence, raw.detection_sequence), (3, 0))

    def test_detection_uses_a_near_time_pair_before_inference(self) -> None:
        frames = {key: LatestFrameBuffer(history_size=4) for key in ("camera1", "camera2")}
        coordinator = DetectionCoordinator(frames, _FakeDetector(), per_camera_fps=12.5)
        coordinator.request_start()
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        frames["camera1"].publish(image, 9.92, None, None)
        frames["camera1"].publish(image, 9.98, None, None)
        frames["camera2"].publish(image, 9.93, None, None)
        frames["camera2"].publish(image, 9.99, None, 0.5)
        with patch("camera_bridge.time.monotonic", return_value=10.0):
            first = coordinator._select_work()
        with patch("camera_bridge.time.monotonic", return_value=10.03):
            second = coordinator._select_work()
        assert first is not None and second is not None
        self.assertEqual((first[0], first[1].sequence, first[3]), ("camera1", 1, (1, 1)))
        self.assertEqual((second[0], second[1].sequence, second[3]), ("camera2", 1, (1, 1)))
        frames["camera1"].publish(image, 10.07, None, None)
        frames["camera2"].publish(image, 10.071, None, None)
        with patch("camera_bridge.time.monotonic", return_value=10.079):
            self.assertIsNone(coordinator._select_work())
        with patch("camera_bridge.time.monotonic", return_value=10.081):
            next_pair = coordinator._select_work()
        assert next_pair is not None
        self.assertEqual(next_pair[3], (3, 3))

    def test_spatial_snapshot_never_combines_different_detection_pairs(self) -> None:
        frames = {key: LatestFrameBuffer() for key in ("camera1", "camera2")}
        coordinator = DetectionCoordinator(frames, _FakeDetector())
        coordinator.request_start()
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        now = time.monotonic()
        drone = DroneObject("track-1", .9, (3, 5, 8, 11))
        first = DetectionResult(1, now, now, image, (drone,), pair_id=(1, 2))
        second = DetectionResult(2, now, now, image, (drone,), pair_id=(3, 2))
        coordinator._result_history["camera1"].append(first)
        coordinator._result_history["camera2"].append(second)
        self.assertEqual(coordinator.spatial_snapshot(), {"camera1": None, "camera2": None})
        coordinator._result_history["camera2"].append(replace(second, pair_id=(1, 2)))
        self.assertEqual(coordinator.spatial_snapshot()["camera1"], first)

    def test_detection_result_expires_after_six_tenths_of_a_second(self) -> None:
        frames = {"camera1": LatestFrameBuffer(), "camera2": LatestFrameBuffer()}
        frames["camera1"].publish(
            np.zeros((30, 40, 3), dtype=np.uint8), time.monotonic(), None, None
        )
        coordinator = DetectionCoordinator(
            frames,
            _FakeDetector(),  # type: ignore[arg-type]
            per_camera_fps=30.0,
            result_ttl=0.6,
        )
        coordinator.start()
        try:
            self.assertEqual(coordinator.request_start()["resultTtlMs"], 600)
            deadline = time.monotonic() + 2.0
            while coordinator.latest("camera1") is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(coordinator.latest("camera1"))

            deadline = time.monotonic() + 1.5
            while coordinator.latest("camera1") is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(coordinator.latest("camera1"))
        finally:
            coordinator.request_stop()
            coordinator.shutdown()

    def test_single_worker_round_robins_latest_frame_for_both_cameras(self) -> None:
        frames = {"camera1": LatestFrameBuffer(), "camera2": LatestFrameBuffer()}
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        for sequence in range(3):
            frames["camera1"].publish(image + sequence, time.monotonic(), None, None)
        frames["camera2"].publish(image, time.monotonic(), None, None)
        coordinator = DetectionCoordinator(
            frames,
            _FakeDetector(),  # type: ignore[arg-type]
            per_camera_fps=30.0,
            result_ttl=2.0,
        )
        coordinator.start()
        coordinator.request_start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if coordinator.latest("camera1") and coordinator.latest("camera2"):
                break
            time.sleep(0.01)
        camera1 = coordinator.latest("camera1")
        camera2 = coordinator.latest("camera2")
        self.assertIsNotNone(camera1)
        self.assertIsNotNone(camera2)
        assert camera1 is not None and camera2 is not None
        self.assertEqual(camera1.sequence, 3)
        self.assertEqual(camera2.sequence, 1)
        self.assertEqual(camera1.objects[0].object_id, "track-1")
        self.assertEqual(camera2.objects[0].object_id, "track-1")
        coordinator.request_stop()
        self.assertIsNone(coordinator.latest("camera1"))
        coordinator.shutdown()


class SnapshotTests(unittest.TestCase):
    def test_idle_writers_do_not_create_output_directories(self) -> None:
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            root = Path(directory)
            output = OutputConfig(
                record_dir=root / "recordings-not-created",
                snapshot_dir=root / "snapshots-not-created",
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            recorder = PacketRecorder(output, "camera1")
            snapshots = SnapshotWriter(output, "camera1")
            recorder.start()
            snapshots.start()
            recorder.shutdown()
            snapshots.shutdown()
            self.assertFalse(output.record_dir.exists())
            self.assertFalse(output.snapshot_dir.exists())

    def test_png_validation_and_encoded_writer_use_fixed_output_directory(self) -> None:
        payload = png_bytes(19, 11)
        self.assertEqual(validate_png(payload), (19, 11))
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            output = OutputConfig(
                record_dir=Path(directory),
                snapshot_dir=Path(directory),
                snapshot_format="jpg",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            writer = SnapshotWriter(output, "camera1")
            writer.start()
            path = writer.submit_png(payload)
            self.assertIsNotNone(path)
            assert path is not None
            deadline = time.monotonic() + 2.0
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            writer.shutdown()
            self.assertEqual(path.parent, Path(directory))
            self.assertTrue(path.name.startswith("camera1_snapshot_"))
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(path.read_bytes(), payload)

    def test_png_rejects_bad_crc_and_trailing_data(self) -> None:
        payload = bytearray(png_bytes())
        payload[-5] ^= 1
        with self.assertRaises(ValueError):
            validate_png(bytes(payload))
        with self.assertRaises(ValueError):
            validate_png(png_bytes() + b"trailing")


class OverlayRecordingTests(unittest.TestCase):
    def test_overlay_recorder_drops_oldest_frame_when_queue_is_full(self) -> None:
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            output = OutputConfig(
                record_dir=Path(directory) / "recordings",
                snapshot_dir=Path(directory) / "snapshots",
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            recorder = OverlayVideoRecorder(
                output,
                "camera1",
                lambda image: None,
                queue_size=2,
            )
            recorder.request_start()
            for value in range(3):
                recorder.submit_frame(
                    np.full((8, 8, 3), value, dtype=np.uint8),
                    pts_seconds=value / 5.0,
                    frame_rate=5.0,
            )

            queued = list(recorder._queue.queue)
            self.assertEqual(len(queued), 2)
            self.assertEqual([int(item.image[0, 0, 0]) for item in queued], [1, 2])
            self.assertEqual(recorder.status().state, "armed")
            self.assertFalse(recorder.status().error)

    def test_overlay_recorder_writes_decodable_video_with_detection_boxes(self) -> None:
        """Record output must contain burned-in boxes, not only detection metadata."""
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            root = Path(directory)
            output = OutputConfig(
                record_dir=root / "recordings",
                snapshot_dir=root / "snapshots",
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            # 320x240 is large enough for both NVENC and libx264's H.264
            # profiles; tiny synthetic frames can be rejected by hardware
            # encoders before the overlay code is exercised.
            objects = (DroneObject("track-1", 0.9, (40.0, 56.0, 260.0, 200.0)),)
            recorder = OverlayVideoRecorder(
                output,
                "camera1",
                lambda image: draw_drone_objects(image, objects),
                queue_size=output.record_queue_packets,
            )
            recorder.start()
            try:
                recorder.request_start()
                for index in range(5):
                    recorder.submit_frame(
                        np.zeros((240, 320, 3), dtype=np.uint8),
                        pts_seconds=index / 5.0,
                        frame_rate=5.0,
                    )

                # Wait until the first frame opened the encoder before asking
                # it to stop.  The stop marker is queued after all frames, so
                # this also verifies that pending frames are drained/finalized.
                recording_deadline = time.monotonic() + 5.0
                while recorder.status().state not in {"recording", "error"}:
                    if time.monotonic() >= recording_deadline:
                        self.fail(f"overlay recorder did not start: {recorder.status()}")
                    time.sleep(0.01)
                if recorder.status().state == "error":
                    self.fail(
                        "no usable annotated video encoder is available: "
                        f"{recorder.status().error or recorder.status()}"
                    )
                recorder.request_stop()

                finalized_deadline = time.monotonic() + 10.0
                while recorder.status().segments_completed < 1:
                    if recorder.status().state == "error":
                        self.fail(f"overlay recorder failed while finalizing: {recorder.status()}")
                    if recorder.status().state == "idle" and not recorder.status().path:
                        self.fail(f"overlay recorder produced no segment: {recorder.status()}")
                    if time.monotonic() >= finalized_deadline:
                        self.fail(f"overlay recorder did not finalize: {recorder.status()}")
                    time.sleep(0.01)
            finally:
                recorder.shutdown()

            status = recorder.status()
            self.assertEqual(status.state, "idle", status.error)
            self.assertTrue(status.path, status)
            path = Path(status.path)
            self.assertEqual(path.parent.resolve(), output.record_dir.resolve())
            self.assertTrue(path.exists(), status)
            self.assertGreater(path.stat().st_size, 0)

            container = av.open(str(path), mode="r")
            try:
                stream = next(iter(container.streams.video))
                frame = next(container.decode(stream))
                decoded = frame.to_ndarray(format="bgr24")
            finally:
                container.close()

            self.assertEqual(decoded.shape[:2], (240, 320))
            # H.264 introduces small quantization changes, so use a tolerant
            # green-dominance mask instead of requiring exact BGR=(45,220,85).
            green = np.logical_and.reduce(
                (
                    decoded[:, :, 1] > 100,
                    decoded[:, :, 1] > decoded[:, :, 0] * 1.25,
                    decoded[:, :, 1] > decoded[:, :, 2] * 1.15,
                )
            )
            self.assertGreater(int(green[56:64, 40:261].sum()), 10)
            self.assertGreater(int(green[194:201, 40:261].sum()), 10)
            self.assertGreater(int(green[56:201, 40:48].sum()), 10)
            self.assertGreater(int(green[56:201, 253:261].sum()), 10)


class BridgeSafetyTests(unittest.TestCase):
    @staticmethod
    def _source(key: str) -> CameraSource:
        return CameraSource(
            key=key,
            label=key,
            stream=StreamConfig(
                host="192.0.2.1",
                port=554,
                username="test-user",
                password="test-password",
                channel=1,
                stream_name="main",
                transport="tcp",
                path=None,
                connect_timeout=1.0,
                read_timeout=1.0,
                analyzeduration_us=100_000,
                probesize=65_536,
                buffer_size=131_072,
                decoder_threads=0,
                hwaccel="none",
                max_backlog_seconds=1.5,
                backlog_frames=5,
            ),
        )

    def test_bridge_rejects_record_on_start_before_any_runtime_starts(self) -> None:
        def output(key: str) -> OutputConfig:
            return OutputConfig(
                record_dir=Path("unused") / key,
                snapshot_dir=Path("unused") / key,
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=True,
            )

        with self.assertRaisesRegex(ValueError, "record_on_start is forbidden"):
            CameraBridgeService(
                [self._source("camera1"), self._source("camera2")],
                {"camera1": output("camera1"), "camera2": output("camera2")},
                model_path=Path("unused.onnx"),
                preview_width=960,
                preview_height=540,
                preview_jpeg_quality=82,
                preview_max_fps=10.0,
            )

    def test_bridge_uses_configured_per_camera_detection_rate(self) -> None:
        class FakeDetector:
            def load(self) -> None:
                pass

            def detect(self, image: np.ndarray) -> list[tuple[tuple[float, float, float, float], float]]:
                return []

            def diagnostics(self) -> dict[str, object]:
                return {}

            def close(self) -> None:
                pass

        def output(key: str) -> OutputConfig:
            return OutputConfig(
                record_dir=Path("unused") / key,
                snapshot_dir=Path("unused") / key,
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )

        with patch("camera_bridge.SpatialLocalizer"):
            service = CameraBridgeService(
                [self._source("camera1"), self._source("camera2")],
                {"camera1": output("camera1"), "camera2": output("camera2")},
                model_path=Path("unused.onnx"),
                preview_width=768,
                preview_height=432,
                preview_jpeg_quality=78,
                preview_max_fps=30.0,
                detection_fps=7.0,
                detector=FakeDetector(),
            )

        self.assertAlmostEqual(service.detection.minimum_interval, 1.0 / 7.0)
        self.assertEqual(service.detection.state()["targetFpsPerCamera"], 7.0)

    def test_capture_request_submits_detection_matched_frames_with_overlays(self) -> None:
        class FakeDetection:
            def __init__(self, results: dict[str, DetectionResult]) -> None:
                self.results = results

            def latest(self, camera_key: str) -> DetectionResult:
                return self.results[camera_key]

        class MemorySnapshotWriter:
            def __init__(self, camera_key: str) -> None:
                self.camera_key = camera_key
                self.images: list[np.ndarray] = []

            def submit(self, image: np.ndarray) -> Path:
                self.images.append(image.copy())
                return Path("trusted-output") / f"{self.camera_key}.png"

        class Runtime:
            def __init__(self, camera_key: str) -> None:
                self.frames = LatestFrameBuffer()
                self.snapshots = MemorySnapshotWriter(camera_key)

        now = time.monotonic()
        results = {
            key: DetectionResult(
                sequence=index,
                received_monotonic=now,
                completed_monotonic=now,
                frame=np.zeros((80, 120, 3), dtype=np.uint8),
                objects=(DroneObject(f"track-{index}", 0.9, (10.0, 12.0, 60.0, 70.0)),),
            )
            for index, key in enumerate(("camera1", "camera2"), start=1)
        }
        service = object.__new__(CameraBridgeService)
        service.cameras = {key: Runtime(key) for key in ("camera1", "camera2")}  # type: ignore[assignment]
        service.detection = FakeDetection(results)  # type: ignore[assignment]

        payload = service.capture_snapshots()

        for key, sequence in (("camera1", 1), ("camera2", 2)):
            item = payload["snapshots"][key]
            self.assertTrue(item["queued"])
            self.assertTrue(item["detectionOverlay"])
            self.assertEqual(item["frameSequence"], sequence)
            writer = service.cameras[key].snapshots
            self.assertEqual(len(writer.images), 1)  # type: ignore[attr-defined]
            self.assertGreater(int(writer.images[0].sum()), 0)  # type: ignore[attr-defined]


class _FakeService:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, bytes]] = []
        self.calls: list[str] = []

    def status_payload(self) -> dict[str, object]:
        return {"version": 1, "detection": {"running": False}, "cameras": {}}

    def preview_jpeg(
        self,
        camera_key: str,
        *,
        after_sequence: int = 0,
        wait_timeout: float = 0.0,
    ) -> EncodedPreview | None:
        self.calls.append(f"preview:{camera_key}:{after_sequence}:{wait_timeout:.3f}")
        if after_sequence >= 7:
            return None
        return EncodedPreview(
            jpeg=b"jpeg",
            sequence=7,
            width=960,
            height=540,
            received_at_ms=1_700_000_000_000,
            age_ms=12.5,
        )

    def toggle_recording(self) -> dict[str, object]:
        self.calls.append("record")
        return {"recording": True}

    def start_detection(self) -> dict[str, object]:
        self.calls.append("start")
        return {"detection": {"running": True}}

    def stop_detection(self) -> dict[str, object]:
        self.calls.append("stop")
        return {"detection": {"running": False}}

    def capture_snapshots(self) -> dict[str, object]:
        self.calls.append("snapshots")
        return {"snapshots": {"camera1": {"queued": True}, "camera2": {"queued": True}}}

    def queue_rendered_snapshot(self, camera_key: str, payload: bytes) -> dict[str, object]:
        width, height = validate_png(payload)
        self.snapshots.append((camera_key, payload))
        return {"camera": camera_key, "queued": True, "width": width, "height": height}


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _FakeService()
        self.shutdown_called = threading.Event()
        handler = make_handler(
            self.service,
            TOKEN,
            max_snapshot_bytes=1024 * 1024,
            shutdown_callback=self.shutdown_called.set,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str | None = None,
        token: str | None = TOKEN,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        headers: dict[str, str] = dict(extra_headers or {})
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, dict(exc.headers.items()), exc.read()
            finally:
                exc.close()

    def test_requires_bearer_authentication(self) -> None:
        status, _headers, body = self.request("/v1/status", token=None)
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    def test_status_preview_and_controls(self) -> None:
        status, _headers, body = self.request("/v1/status")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(json.loads(body)["version"], 1)
        status, headers, body = self.request("/v1/cameras/camera1/frame.jpg")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(headers["X-Frame-Sequence"], "7")
        self.assertEqual(headers["X-Frame-Width"], "960")
        self.assertEqual(headers["X-Frame-Height"], "540")
        self.assertEqual(body, b"jpeg")
        self.assertEqual(self.request("/v1/detection/start", method="POST", body=b"")[0], HTTPStatus.OK)
        self.assertEqual(self.request("/v1/detection/stop", method="POST", body=b"")[0], HTTPStatus.OK)
        self.assertEqual(self.request("/v1/recording/toggle", method="POST", body=b"")[0], HTTPStatus.OK)
        self.assertEqual(self.request("/v1/snapshots", method="POST", body=b"")[0], HTTPStatus.ACCEPTED)

    def test_preview_long_poll_headers_are_bounded_and_return_204_without_a_new_frame(self) -> None:
        status, _headers, body = self.request(
            "/v1/cameras/camera1/frame.jpg",
            extra_headers={
                "X-After-Frame-Sequence": "7",
                "X-Wait-Milliseconds": "750",
            },
        )
        self.assertEqual(status, HTTPStatus.NO_CONTENT)
        self.assertEqual(body, b"")
        self.assertIn("preview:camera1:7:0.750", self.service.calls)

        status, _headers, body = self.request(
            "/v1/cameras/camera1/frame.jpg",
            extra_headers={"X-Wait-Milliseconds": "1001"},
        )
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_preview_header")

    def test_png_endpoint_validates_content_and_never_accepts_a_path(self) -> None:
        payload = png_bytes()
        status, _headers, body = self.request(
            "/v1/cameras/camera2/snapshot.png",
            method="POST",
            body=payload,
            content_type="image/png",
        )
        self.assertEqual(status, HTTPStatus.ACCEPTED)
        self.assertEqual(json.loads(body)["camera"], "camera2")
        self.assertEqual(self.service.snapshots, [("camera2", payload)])
        status, _headers, body = self.request(
            "/v1/cameras/camera1/snapshot.png",
            method="POST",
            body=b'{"path":"C:/untrusted.png"}',
            content_type="application/json",
        )
        self.assertEqual(status, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(json.loads(body)["error"]["code"], "png_required")

    def test_query_strings_are_rejected(self) -> None:
        status, _headers, body = self.request("/v1/status?path=C%3A%2Fanything")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_url")


if __name__ == "__main__":
    unittest.main()
