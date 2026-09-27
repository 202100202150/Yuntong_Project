#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import cv2
import numpy as np
import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate one fixed camera")
    parser.add_argument("--images", required=True, help="Glob, e.g. captures/cam01/*.jpg")
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--cols", type=int, default=9, help="Inner corner columns")
    parser.add_argument("--rows", type=int, default=6, help="Inner corner rows")
    parser.add_argument("--square-size-m", type=float, required=True)
    parser.add_argument("--min-images", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    paths = [Path(item) for item in sorted(glob.glob(args.images))]
    if not paths:
        raise SystemExit(f"No images match {args.images}")
    object_template = np.zeros((args.rows * args.cols, 3), np.float32)
    object_template[:, :2] = np.mgrid[0 : args.cols, 0 : args.rows].T.reshape(-1, 2)
    object_template *= args.square_size_m
    object_points: list[np.ndarray] = []
    image_points: list[np.ndarray] = []
    image_size = None
    for path in paths:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        image_size = (image.shape[1], image.shape[0])
        found, corners = cv2.findChessboardCornersSB(
            image, (args.cols, args.rows), cv2.CALIB_CB_EXHAUSTIVE
        )
        if found:
            object_points.append(object_template.copy())
            image_points.append(corners)
            print(f"accepted {path}")
        else:
            print(f"rejected {path}")
    if len(object_points) < args.min_images or image_size is None:
        raise SystemExit(
            f"Only {len(object_points)} usable images; require {args.min_images}"
        )

    rms, camera_matrix, distortion, _, _ = cv2.calibrateCamera(
        object_points, image_points, image_size, None, None
    )
    payload = {
        "camera_id": args.camera_id,
        "version": "intrinsics-only",
        "valid": False,
        "rms_px": float(rms),
        "image_size": list(image_size),
        "camera_matrix": camera_matrix.tolist(),
        "distortion": distortion.reshape(-1).tolist(),
        "rotation_world_to_camera": np.eye(3).tolist(),
        "translation_world_to_camera_m": [0.0, 0.0, 0.0],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    print(f"RMS={rms:.4f}px; wrote unapproved calibration to {args.output}")
    if rms > 0.8:
        print("WARNING: RMS exceeds the 0.8 px acceptance threshold")


if __name__ == "__main__":
    main()
