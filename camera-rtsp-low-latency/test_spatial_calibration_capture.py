"""Capture smoke tests also runnable in the reduced CUDA OpenCV runtime."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import spatial_calibration as calibration


class SpatialCaptureRuntimeTests(unittest.TestCase):
    def test_current_runtime_reports_calibration_capability(self):
        available = all(hasattr(cv2, method) for method in ("calibrateCamera", "stereoCalibrate", "projectPoints", "Rodrigues")) and calibration.board_detection_available()
        if available:
            calibration.ensure_calibration_runtime()
        else:
            with self.assertRaises(ValueError) as raised:
                calibration.ensure_calibration_runtime()
            self.assertIn(str(calibration.FULL_OPENCV_PYTHON), str(raised.exception))

    def test_missing_board_detector_saves_explicitly_unchecked_raw_pairs(self):
        class FakeReader:
            def __init__(self, _config, frames, _status, _recorder, _stop, worker_name):
                self.frames = frames

            def start(self):
                image = np.full((48, 64, 3), 127, np.uint8)
                self.frames.publish(image, time.monotonic(), 0.0, 0.0)

            def join(self, timeout):
                pass

        work = calibration.PROJECT_ROOT / "work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="capture-runtime-test-", dir=work) as temporary:
            output = Path(temporary) / "pairs"
            args = argparse.Namespace(output=output, columns=9, rows=6, manual=False, display=False,
                                      pairs=1, interval=0.01, duration=1.0, max_skew_ms=100, skip_board_check=False)
            sources = [SimpleNamespace(key=key, stream=None) for key in ("camera1", "camera2")]
            with patch("camera_low_latency.StreamWorker", FakeReader), patch("camera_low_latency.parse_args", return_value=SimpleNamespace()), patch("camera_low_latency.build_sources_and_outputs", return_value=(sources, {})), patch("spatial_calibration.board_detection_available", return_value=False), patch.dict("os.environ", {"CAMERA1_PASSWORD": "test-only", "CAMERA2_PASSWORD": "test-only"}), redirect_stdout(io.StringIO()) as messages:
                self.assertEqual(calibration.capture(args), 0)
            self.assertIn("RAW PAIRS", messages.getvalue())
            for camera in ("camera1", "camera2"):
                image = cv2.imread(str(output / camera / "0001.png"))
                self.assertEqual(image.shape, (48, 64, 3))
                self.assertTrue(np.all(image == 127))
            manifest = json.loads((output / "capture-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["boardCheckedAtCapture"])
            self.assertFalse(manifest["pairs"][0]["boardCheckedAtCapture"])
            self.assertNotIn("test-only", json.dumps(manifest))


if __name__ == "__main__":
    unittest.main()
