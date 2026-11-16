import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np


class MonitorCaptureTests(unittest.TestCase):
    def test_worker_emits_decodable_original_video_frame(self):
        with tempfile.TemporaryDirectory(prefix="yuntong-capture-") as directory:
            source = pathlib.Path(directory) / "source.avi"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 15, (64, 48))
            self.assertTrue(writer.isOpened())
            for index in range(15):
                frame = np.zeros((48, 64, 3), dtype=np.uint8)
                frame[:, :32] = (0, 0, 220)
                frame[:, 32:] = (0, 180, 0)
                writer.write(frame)
            writer.release()
            result = subprocess.run(
                [sys.executable, str(pathlib.Path(__file__).with_name("monitor_capture.py"))],
                input=(json.dumps({"url": str(source), "maxWidth": 960, "maxFps": 15}) + "\n").encode(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            )
            self.assertGreaterEqual(len(result.stdout), 24)
            magic, length, width, height, timestamp = struct.unpack(">4sIIIQ", result.stdout[:24])
            self.assertEqual((magic, width, height), (b"YTF1", 64, 48))
            self.assertGreater(timestamp, 0)
            decoded = cv2.imdecode(np.frombuffer(result.stdout[24:24 + length], dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.shape, (48, 64, 3))
            self.assertGreater(decoded[24, 12, 2], 180)
            self.assertGreater(decoded[24, 50, 1], 150)
            self.assertIn(result.returncode, (0, 4))


if __name__ == "__main__":
    unittest.main()
