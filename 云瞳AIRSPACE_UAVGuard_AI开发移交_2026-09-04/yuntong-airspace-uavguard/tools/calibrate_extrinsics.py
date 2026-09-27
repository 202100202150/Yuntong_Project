#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np
import yaml


def reprojection_rms(
    world: np.ndarray,
    pixels: np.ndarray,
    rotation_vector: np.ndarray,
    translation: np.ndarray,
    camera_matrix: np.ndarray,
    distortion: np.ndarray,
) -> float:
    projected, _ = cv2.projectPoints(
        world, rotation_vector, translation, camera_matrix, distortion
    )
    residual = projected.reshape(-1, 2) - pixels
    return float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Solve a camera pose from surveyed ENU points")
    parser.add_argument("--intrinsics", type=Path, required=True)
    parser.add_argument(
        "--points",
        type=Path,
        required=True,
        help="CSV columns: x_east_m,y_north_m,z_up_m,u_px,v_px,split",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-holdout-rms", type=float, default=2.0)
    args = parser.parse_args()

    calibration = yaml.safe_load(args.intrinsics.read_text(encoding="utf-8"))
    camera_matrix = np.asarray(calibration["camera_matrix"], dtype=np.float64)
    distortion = np.asarray(calibration.get("distortion", []), dtype=np.float64)
    train_world, train_pixels, holdout_world, holdout_pixels = [], [], [], []
    with args.points.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            world = [
                float(row["x_east_m"]),
                float(row["y_north_m"]),
                float(row["z_up_m"]),
            ]
            pixel = [float(row["u_px"]), float(row["v_px"])]
            if row.get("split", "train").strip().lower() == "holdout":
                holdout_world.append(world)
                holdout_pixels.append(pixel)
            else:
                train_world.append(world)
                train_pixels.append(pixel)
    if len(train_world) < 6:
        raise SystemExit("At least 6 training control points are required")
    if len(holdout_world) < 3:
        raise SystemExit("At least 3 holdout points are required for approval")
    train_world_array = np.asarray(train_world, dtype=np.float64)
    train_pixels_array = np.asarray(train_pixels, dtype=np.float64)
    ok, rotation_vector, translation, inliers = cv2.solvePnPRansac(
        train_world_array,
        train_pixels_array,
        camera_matrix,
        distortion,
        flags=cv2.SOLVEPNP_ITERATIVE,
        reprojectionError=3.0,
        iterationsCount=200,
        confidence=0.999,
    )
    if not ok or inliers is None or len(inliers) < 6:
        raise SystemExit("solvePnPRansac did not find a valid camera pose")
    rotation_vector, translation = cv2.solvePnPRefineLM(
        train_world_array[inliers[:, 0]],
        train_pixels_array[inliers[:, 0]],
        camera_matrix,
        distortion,
        rotation_vector,
        translation,
    )
    rotation, _ = cv2.Rodrigues(rotation_vector)
    train_rms = reprojection_rms(
        train_world_array,
        train_pixels_array,
        rotation_vector,
        translation,
        camera_matrix,
        distortion,
    )
    holdout_rms = reprojection_rms(
        np.asarray(holdout_world, dtype=np.float64),
        np.asarray(holdout_pixels, dtype=np.float64),
        rotation_vector,
        translation,
        camera_matrix,
        distortion,
    )
    intrinsic_rms = calibration.get("rms_px")
    approved = (
        intrinsic_rms is not None
        and float(intrinsic_rms) <= 0.8
        and holdout_rms <= args.maximum_holdout_rms
    )
    calibration.update(
        {
            "version": args.output.stem,
            "valid": bool(approved),
            "rotation_world_to_camera": rotation.tolist(),
            "translation_world_to_camera_m": translation.reshape(-1).tolist(),
            "extrinsic_train_rms_px": train_rms,
            "extrinsic_holdout_rms_px": holdout_rms,
            "control_points": {
                "train": len(train_world),
                "holdout": len(holdout_world),
                "ransac_inliers": int(len(inliers)),
            },
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(calibration, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(
        f"train RMS={train_rms:.3f}px, holdout RMS={holdout_rms:.3f}px, valid={approved}"
    )
    if not approved:
        raise SystemExit("Calibration was written but not approved; inspect RMS thresholds")


if __name__ == "__main__":
    main()

