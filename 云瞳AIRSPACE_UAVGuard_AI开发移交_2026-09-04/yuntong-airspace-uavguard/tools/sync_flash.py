#!/usr/bin/env python3
from __future__ import annotations

import argparse

import cv2
import numpy as np


def trace(path: str, roi: tuple[int, int, int, int]) -> tuple[np.ndarray, float]:
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise SystemExit(f"Cannot open {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    values = []
    x, y, width, height = roi
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        values.append(float(np.mean(gray[y : y + height, x : x + width])))
    capture.release()
    signal = np.asarray(values, dtype=np.float64)
    signal -= np.mean(signal)
    signal /= max(np.std(signal), 1e-9)
    return signal, fps


def parse_roi(value: str) -> tuple[int, int, int, int]:
    parts = tuple(int(item) for item in value.split(","))
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("ROI must be x,y,width,height")
    return parts


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate camera offset from a shared flashing target")
    parser.add_argument("--first", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument("--first-roi", type=parse_roi, required=True)
    parser.add_argument("--second-roi", type=parse_roi, required=True)
    parser.add_argument("--max-lag-frames", type=int, default=50)
    args = parser.parse_args()
    first, first_fps = trace(args.first, args.first_roi)
    second, second_fps = trace(args.second, args.second_roi)
    fps = (first_fps + second_fps) * 0.5
    correlation = np.correlate(first, second, mode="full")
    lags = np.arange(-len(second) + 1, len(first))
    valid = np.abs(lags) <= args.max_lag_frames
    lag = int(lags[valid][np.argmax(correlation[valid])])
    offset_ms = lag * 1000.0 / fps
    print(f"camera2_offset_to_add_ms={offset_ms:.3f}")
    print("Positive means camera 2 timestamps should be shifted later.")


if __name__ == "__main__":
    main()

