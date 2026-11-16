"""On-demand single-camera JPEG transport for the multi-camera monitor.

No inference or synthetic footage is generated here. The parent supplies a
validated camera URL over stdin; binary frames are the only stdout content.
"""
import json
import os
import struct
import sys
import time

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def main():
    try:
        import cv2
        config = json.loads(sys.stdin.buffer.readline(65536))
        url = config["url"]
        max_width = max(160, min(1920, int(config.get("maxWidth", 960))))
        max_fps = max(1, min(30, float(config.get("maxFps", 15))))
    except Exception:
        return 2
    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 6000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 6000]
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
    if not capture.isOpened():
        capture.release()
        return 3
    last_sent = 0.0
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                return 4
            now = time.monotonic()
            if now - last_sent < 1.0 / max_fps:
                continue
            height, width = frame.shape[:2]
            if width > max_width:
                height = max(1, round(height * max_width / width))
                width = max_width
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if not ok:
                continue
            jpeg = encoded.tobytes()
            header = struct.pack(">4sIIIQ", b"YTF1", len(jpeg), width, height, int(time.time() * 1000))
            sys.stdout.buffer.write(header)
            sys.stdout.buffer.write(jpeg)
            sys.stdout.buffer.flush()
            last_sent = now
    except BrokenPipeError:
        return 0
    finally:
        capture.release()


if __name__ == "__main__":
    sys.exit(main())
