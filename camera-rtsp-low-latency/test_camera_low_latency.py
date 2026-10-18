from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import unittest

import av
import numpy as np

from camera_low_latency import (
    LatestFrameBuffer,
    OutputConfig,
    PtsBacklogTracker,
    SnapshotWriter,
    StreamConfig,
    build_sources_and_outputs,
    clone_packet,
    compose_side_by_side,
    resize_for_display,
    parse_args,
)


def config(**changes: object) -> StreamConfig:
    values = {
        "host": "192.0.2.10",
        "port": 554,
        "username": "user@example",
        "password": "p@ss:/?#%",
        "channel": 1,
        "stream_name": "sub",
        "transport": "tcp",
        "path": None,
        "connect_timeout": 5.0,
        "read_timeout": 3.0,
        "analyzeduration_us": 100_000,
        "probesize": 65_536,
        "buffer_size": 131_072,
        "decoder_threads": 0,
        "hwaccel": "none",
        "max_backlog_seconds": 1.5,
        "backlog_frames": 5,
    }
    values.update(changes)
    return StreamConfig(**values)


class StreamConfigTests(unittest.TestCase):
    def test_hikvision_channel_mapping(self) -> None:
        self.assertEqual(config(stream_name="main").stream_id, "101")
        self.assertEqual(config(stream_name="sub").stream_id, "102")
        self.assertEqual(config(channel=12, stream_name="third").stream_id, "1203")

    def test_credentials_are_url_encoded(self) -> None:
        url = config().rtsp_url()
        self.assertIn("user%40example:p%40ss%3A%2F%3F%23%25@", url)
        self.assertNotIn("p@ss", url)

    def test_password_is_redacted(self) -> None:
        url = config().rtsp_url(redact_password=True)
        self.assertIn("user%40example:***@", url)
        self.assertNotIn("p%40ss", url)

    def test_custom_path(self) -> None:
        self.assertTrue(config(path="custom/live").rtsp_url().endswith("/custom/live"))

    def test_argument_parser_remains_reusable_and_backward_compatible(self) -> None:
        args = parse_args(["--ip", "192.0.2.10", "--port", "8554", "--no-record-on-start"])
        self.assertEqual(args.camera1_ip, "192.0.2.10")
        self.assertEqual(args.camera1_port, 8554)
        self.assertFalse(args.record_on_start)
        sources, outputs = build_sources_and_outputs(args, "first-secret", "second-secret")
        self.assertEqual([source.key for source in sources], ["camera1", "camera2"])
        self.assertEqual(sources[0].stream.password, "first-secret")
        self.assertFalse(outputs["camera1"].record_on_start)


class BufferTests(unittest.TestCase):
    def test_latest_frame_overwrites_old_frame(self) -> None:
        buffer = LatestFrameBuffer()
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        buffer.publish(image, 1.0, 0.0, 0.0)
        buffer.publish(image, 2.0, 0.04, 0.0)
        packet = buffer.wait_for_new(0, 0.0)
        self.assertIsNotNone(packet)
        assert packet is not None
        self.assertEqual(packet.sequence, 2)
        self.assertEqual(buffer.overwritten, 1)

    def test_preview_snapshot_wait_does_not_change_consumption_accounting(self) -> None:
        buffer = LatestFrameBuffer()
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        buffer.publish(image, 1.0, 0.0, 0.0)
        packet = buffer.wait_for_new_snapshot(0, 0.0)
        self.assertIsNotNone(packet)
        assert packet is not None
        self.assertEqual(packet.sequence, 1)
        buffer.publish(image, 2.0, 0.04, 0.0)
        self.assertEqual(buffer.overwritten, 1)
        self.assertIsNone(buffer.wait_for_new_snapshot(2, 0.0))
        with self.assertRaises(ValueError):
            buffer.wait_for_new_snapshot(-1, 0.0)

    def test_pts_tracker_reports_accumulated_drift(self) -> None:
        tracker = PtsBacklogTracker()
        self.assertEqual(tracker.observe(10.0, 100.0), 0.0)
        drift = tracker.observe(10.04, 100.14)
        self.assertIsNotNone(drift)
        assert drift is not None
        self.assertAlmostEqual(drift, 0.10, places=6)


class MediaPathTests(unittest.TestCase):
    def test_display_resize_only_downscales(self) -> None:
        source = np.zeros((2160, 3840, 3), dtype=np.uint8)
        downscaled = resize_for_display(source, 1280, 720)
        self.assertEqual(downscaled.shape[:2], (720, 1280))
        self.assertIs(resize_for_display(source, 0, 0), source)
        small = np.zeros((576, 704, 3), dtype=np.uint8)
        self.assertIs(resize_for_display(small, 1280, 720), small)

    def test_side_by_side_layout_adds_visible_separator(self) -> None:
        left = np.full((3, 4, 3), 10, dtype=np.uint8)
        right = np.full((2, 2, 3), 20, dtype=np.uint8)
        combined = compose_side_by_side([left, right], separator_width=2)
        self.assertEqual(combined.shape, (3, 10, 3))
        self.assertTrue(np.all(combined[:, 4:6] == 48))
        self.assertTrue(np.all(combined[:, :4] == 10))

    def test_compressed_packet_clone_keeps_timestamps(self) -> None:
        packet = av.Packet(b"compressed")
        packet.pts = 51
        packet.dts = 50
        packet.duration = 1
        packet.time_base = Fraction(1, 25)
        packet.is_keyframe = True
        copied = clone_packet(packet)
        self.assertEqual(bytes(copied), b"compressed")
        self.assertEqual((copied.pts, copied.dts, copied.duration), (51, 50, 1))
        self.assertEqual(copied.time_base, Fraction(1, 25))
        self.assertTrue(copied.is_keyframe)

    def test_snapshot_uses_separate_image_directory(self) -> None:
        camera1_output = OutputConfig(
            record_dir=Path("camera1-output"),
            snapshot_dir=Path("camera1-output"),
            snapshot_format="png",
            jpeg_quality=95,
            png_compression=3,
            record_container="mp4",
            record_queue_packets=16,
            record_on_start=False,
        )
        camera2_output = OutputConfig(
            record_dir=Path("camera2-output"),
            snapshot_dir=Path("camera2-output"),
            snapshot_format="png",
            jpeg_quality=95,
            png_compression=3,
            record_container="mp4",
            record_queue_packets=16,
            record_on_start=False,
        )
        camera1_path = SnapshotWriter(camera1_output, "camera1").submit(
            np.zeros((10, 20, 3), dtype=np.uint8)
        )
        camera2_path = SnapshotWriter(camera2_output, "camera2").submit(
            np.zeros((10, 20, 3), dtype=np.uint8)
        )
        self.assertIsNotNone(camera1_path)
        self.assertIsNotNone(camera2_path)
        assert camera1_path is not None and camera2_path is not None
        self.assertEqual(camera1_path.parent, Path("camera1-output"))
        self.assertEqual(camera2_path.parent, Path("camera2-output"))
        self.assertTrue(camera1_path.name.startswith("camera1_snapshot_"))
        self.assertTrue(camera2_path.name.startswith("camera2_snapshot_"))


if __name__ == "__main__":
    unittest.main()
