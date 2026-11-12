#!/usr/bin/env python3
"""Capture chessboards/AprilGrids, calibrate, and evaluate measured X/Z samples.

No camera/device settings are written. Capture is opt-in and reuses the existing
latest-frame RTSP reader; calibration and evaluation work offline.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np

from spatial_calibration_boards import aprilgrid_detection_available, load_tag_board

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "work" / "spatial-calibration"
DEFAULT_CONFIG = Path(__file__).with_name("spatial-config.json")
FULL_OPENCV_PYTHON = Path("E:/python/python.exe")
DEFAULT_TAG_BOARD = Path(__file__).with_name("aprilgrid-6x6.json")


def board_detection_available(board_type: str = "chessboard") -> bool:
    if board_type == "aprilgrid":
        return aprilgrid_detection_available()
    return hasattr(cv2, "findChessboardCornersSB") or hasattr(cv2, "findChessboardCorners")


def ensure_calibration_runtime(board_type: str = "chessboard") -> None:
    required = ("calibrateCamera", "stereoCalibrate", "projectPoints", "Rodrigues")
    missing = [name for name in required if not hasattr(cv2, name)]
    if missing or not board_detection_available(board_type):
        raise ValueError(
            "this OpenCV runtime lacks offline calibration/board-detection modules. "
            f"Keep the CUDA environment unchanged; run calibrate with the verified full OpenCV interpreter: {FULL_OPENCV_PYTHON}"
        )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def board_points(columns: int, rows: int, square_size_m: float) -> np.ndarray:
    if columns < 3 or rows < 3 or not math.isfinite(square_size_m) or square_size_m <= 0:
        raise ValueError("board requires >=3 internal corners per direction and a positive square size in metres")
    result = np.zeros((columns * rows, 3), np.float32)
    result[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2) * square_size_m
    return result


def find_board(image: np.ndarray, pattern: tuple[int, int]) -> np.ndarray | None:
    """Find on a reduced copy, then refine against the original raw pixels."""
    if not board_detection_available():
        raise ValueError(f"board detector unavailable; use {FULL_OPENCV_PYTHON} for offline calibration")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    scale = min(1.0, 1600.0 / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    if hasattr(cv2, "findChessboardCornersSB"):
        found, corners = cv2.findChessboardCornersSB(small, pattern, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    else:
        found, corners = cv2.findChessboardCorners(small, pattern, flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        return None
    corners = np.asarray(corners / scale, dtype=np.float32)
    return cv2.cornerSubPix(gray, corners, (7, 7), (-1, -1), (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001))


def pixel_rms(delta: np.ndarray) -> float:
    """Return a finite Euclidean pixel RMS even for a degenerate projection."""
    delta = np.asarray(delta, dtype=np.float64).reshape(-1, 2)
    if not np.isfinite(delta).all():
        return 1e12
    distances = np.hypot(delta[:, 0], delta[:, 1])
    if not np.isfinite(distances).all():
        return 1e12
    # A projection this far outside an image is already an unambiguous bad
    # view; clipping keeps its diagnostic JSON finite and avoids square overflow.
    distances = np.minimum(distances, 1e12)
    return float(np.sqrt(np.mean(distances * distances)))


def calibration_from_observations(
    object_points: list[np.ndarray],
    points1: list[np.ndarray],
    points2: list[np.ndarray],
    image_size: tuple[int, int],
    max_view_rms_px: float | None = None,
    min_pairs: int = 3,
) -> dict[str, Any]:
    """Return OpenCV convention: P2=R*P1+T; T is in metres.

    When enabled, iteratively drop the single worst paired observation whose
    monocular reprojection RMS exceeds ``max_view_rms_px``. Indices in the
    returned filter report refer to the caller's original observation arrays.
    """
    ensure_calibration_runtime()
    if len(object_points) < 3 or len(points1) != len(object_points) or len(points2) != len(object_points):
        raise ValueError("at least three complete paired observations are required")
    if min_pairs < 3 or min_pairs > len(object_points):
        raise ValueError("min_pairs must be between 3 and the number of observations")
    if max_view_rms_px is not None and (not math.isfinite(max_view_rms_px) or max_view_rms_px <= 0):
        raise ValueError("max_view_rms_px must be a finite positive number, or None to disable filtering")
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-8)
    retained = list(range(len(object_points)))
    rejected_views = []

    while True:
        current_objects = [object_points[index] for index in retained]
        current_points1 = [points1[index] for index in retained]
        current_points2 = [points2[index] for index in retained]
        camera_results = [cv2.calibrateCamera(current_objects, points, image_size, None, None, criteria=criteria) for points in (current_points1, current_points2)]
        rms1, k1, d1, rvecs1, tvecs1 = camera_results[0]
        rms2, k2, d2, rvecs2, tvecs2 = camera_results[1]
        per_view: dict[str, list[float]] = {}
        for key, points, intrinsic, distortion, rvecs, tvecs in (
            ("camera1", current_points1, k1, d1, rvecs1, tvecs1),
            ("camera2", current_points2, k2, d2, rvecs2, tvecs2),
        ):
            errors = []
            for objects, pixels, rvec, tvec in zip(current_objects, points, rvecs, tvecs):
                projected, _ = cv2.projectPoints(objects, rvec, tvec, intrinsic, distortion)
                errors.append(pixel_rms(projected.reshape(-1, 2) - pixels.reshape(-1, 2)))
            per_view[key] = errors
        rms_stereo, k1, d1, k2, d2, rotation, translation, _essential, _fundamental = cv2.stereoCalibrate(
            current_objects, current_points1, current_points2, k1, d1, k2, d2, image_size,
            criteria=criteria, flags=cv2.CALIB_FIX_INTRINSIC,
        )
        # A moving board can fit each camera separately while violating the
        # paired-frame assumption. Measure symmetric cross-camera reprojection
        # error for every observation so those pairs can be filtered too.
        stereo_pair_errors = []
        rotation_inverse = rotation.T
        translation_vector = translation.reshape(3)
        for objects, pixels1, pixels2, rvec1, tvec1, rvec2, tvec2 in zip(
            current_objects, current_points1, current_points2, rvecs1, tvecs1, rvecs2, tvecs2,
        ):
            rotation_board1 = cv2.Rodrigues(rvec1)[0]
            rotation_board2 = cv2.Rodrigues(rvec2)[0]
            projected2, _ = cv2.projectPoints(
                objects,
                cv2.Rodrigues(rotation @ rotation_board1)[0],
                rotation @ tvec1.reshape(3) + translation_vector,
                k2, d2,
            )
            projected1, _ = cv2.projectPoints(
                objects,
                cv2.Rodrigues(rotation_inverse @ rotation_board2)[0],
                rotation_inverse @ (tvec2.reshape(3) - translation_vector),
                k1, d1,
            )
            delta1 = projected1.reshape(-1, 2) - pixels1.reshape(-1, 2)
            delta2 = projected2.reshape(-1, 2) - pixels2.reshape(-1, 2)
            stereo_pair_errors.append(pixel_rms(np.concatenate((delta1, delta2), axis=0)))
        per_view["stereoPair"] = stereo_pair_errors
        if max_view_rms_px is None or len(retained) <= min_pairs:
            break
        worst_local = max(range(len(retained)), key=lambda index: max(
            per_view["camera1"][index], per_view["camera2"][index], per_view["stereoPair"][index],
        ))
        worst_rms1 = per_view["camera1"][worst_local]
        worst_rms2 = per_view["camera2"][worst_local]
        worst_stereo = per_view["stereoPair"][worst_local]
        if max(worst_rms1, worst_rms2, worst_stereo) <= max_view_rms_px:
            break
        rejected_views.append({
            "index": retained[worst_local],
            "camera1RmsPx": worst_rms1,
            "camera2RmsPx": worst_rms2,
            "stereoPairRmsPx": worst_stereo,
            "reason": f"per-view reprojection RMS exceeds {max_view_rms_px:g} px",
        })
        del retained[worst_local]

    return {
        "camera1": {"matrix": k1, "distortion": d1, "rmsPx": float(rms1)},
        "camera2": {"matrix": k2, "distortion": d2, "rmsPx": float(rms2)},
        "rotation": rotation, "translationMeters": translation.reshape(3),
        "stereoRmsPx": float(rms_stereo), "perViewRmsPx": per_view,
        "imageSize": image_size, "pairCount": len(retained),
        "inputPairCount": len(object_points), "retainedObservationIndices": retained,
        "viewFilter": {"enabled": max_view_rms_px is not None, "maxViewRmsPx": max_view_rms_px,
                       "minimumRetainedPairs": min_pairs, "rejectedViews": rejected_views},
    }


def quality_diagnostics(result: dict[str, Any], expected_baseline: float, tolerance: float, max_rms: float) -> dict[str, Any]:
    if expected_baseline <= 0 or not 0 < tolerance < 1 or max_rms <= 0:
        raise ValueError("invalid baseline, tolerance or RMS threshold")
    raw_baseline = float(np.linalg.norm(result["translationMeters"]))
    relative_error = abs(raw_baseline - expected_baseline) / expected_baseline
    rms_values = [result["camera1"]["rmsPx"], result["camera2"]["rmsPx"], result["stereoRmsPx"]]
    failures = []
    if not all(math.isfinite(value) and value <= max_rms for value in rms_values):
        failures.append("reprojection RMS exceeds threshold or is non-finite")
    if not math.isfinite(raw_baseline) or raw_baseline <= 1e-9 or relative_error > tolerance:
        failures.append("calibrated optical baseline disagrees with the measured baseline")
    return {
        "passed": not failures, "failures": failures,
        "pairCount": result["pairCount"], "imageSize": list(result["imageSize"]),
        "inputPairCount": result.get("inputPairCount", result["pairCount"]),
        "camera1RmsPx": rms_values[0], "camera2RmsPx": rms_values[1], "stereoRmsPx": rms_values[2],
        "perViewRmsPx": result["perViewRmsPx"],
        "viewFilter": result.get("viewFilter", {"enabled": False, "rejectedViews": []}),
        "rawCalibratedBaselineMeters": raw_baseline,
        "measuredBaselineMeters": expected_baseline, "baselineRelativeDifference": relative_error,
        "baselineToleranceFraction": tolerance, "maxRmsPx": max_rms,
        "appliedTranslationScale": expected_baseline / raw_baseline if raw_baseline > 1e-9 else None,
        "rawOpenCvRotationCamera1To2": result["rotation"].tolist(),
        "rawOpenCvTranslationCamera1To2Meters": result["translationMeters"].tolist(),
        "worldAnchor": "Configured camera positions define scale and baseline direction. Remaining common rotation about the baseline follows the input camera1 pose; independently verify common pitch using surveyed controls or an inclinometer.",
        "accuracyStatus": "Calibration residuals are not independent field position accuracy; held-out measured positions are still required.",
    }


def rotation_aligning_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = np.asarray(source, dtype=float) / np.linalg.norm(source)
    target = np.asarray(target, dtype=float) / np.linalg.norm(target)
    cross = np.cross(source, target)
    cosine = float(np.clip(np.dot(source, target), -1, 1))
    if cosine > 1 - 1e-12:
        return np.eye(3)
    if cosine < -1 + 1e-12:
        axis = np.cross(source, np.eye(3)[int(np.argmin(abs(source)))])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    skew = np.array([[0, -cross[2], cross[1]], [cross[2], 0, -cross[0]], [-cross[1], cross[0], 0]])
    return np.eye(3) + skew + skew @ skew / (1 + cosine)


def export_config(result: dict[str, Any], base_config: dict[str, Any]) -> dict[str, Any]:
    """Preserve measured C1/C2; align the calibrated pair to that baseline.

    The length of T is anchored explicitly to the measured optical baseline.
    A common rotation aligns its direction; the relative stereo rotation is
    preserved exactly. Common rotation about the baseline is not observable
    from the board alone and follows the input camera1 pose.
    """
    from spatial_localization import camera_pose, pose_angles_from_camera_to_world, validate_config

    config = validate_config(copy.deepcopy(base_config))
    center1, world1 = camera_pose(config, "camera1")
    center2, _world2 = camera_pose(config, "camera2")
    delta = center2 - center1
    estimated_delta = -world1 @ result["rotation"].T @ result["translationMeters"]
    world1 = rotation_aligning_vectors(estimated_delta, delta) @ world1
    world2 = world1 @ result["rotation"].T
    width, height = result["imageSize"]
    for key, world in (("camera1", world1), ("camera2", world2)):
        camera = config["cameras"][key]
        camera.update(pose_angles_from_camera_to_world(world))
        k = result[key]["matrix"]
        camera["intrinsics"] = {
            "imageWidth": int(width), "imageHeight": int(height),
            "fx": float(k[0, 0]), "fy": float(k[1, 1]), "cx": float(k[0, 2]), "cy": float(k[1, 2]),
            "distortion": result[key]["distortion"].reshape(-1).tolist(), "source": "calibrated",
        }
    return validate_config(config)


def load_pairs(input_dir: Path, columns: int, rows: int, square_size: float) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray], tuple[int, int], list[str], list[str]]:
    objects, points1, points2, accepted, rejected = [], [], [], [], []
    image_size = None
    object_template = board_points(columns, rows, square_size)
    for left_path in sorted((input_dir / "camera1").glob("*.png")):
        right_path = input_dir / "camera2" / left_path.name
        left, right = cv2.imread(str(left_path)), cv2.imread(str(right_path))
        if left is None or right is None:
            rejected.append(f"{left_path.name}: unreadable/missing paired image")
            continue
        size = (left.shape[1], left.shape[0])
        if right.shape[:2] != left.shape[:2] or (image_size is not None and size != image_size):
            raise ValueError(f"{left_path.name}: mixed image sizes; capture both cameras at the same fixed resolution")
        image_size = size
        corners = [find_board(image, (columns, rows)) for image in (left, right)]
        if any(item is None for item in corners):
            rejected.append(f"{left_path.name}: board not found in both cameras")
            continue
        objects.append(object_template.copy())
        points1.append(corners[0])
        points2.append(corners[1])
        accepted.append(left_path.name)
    if image_size is None:
        raise ValueError("no paired PNGs found in input/camera1 and input/camera2")
    return objects, points1, points2, image_size, accepted, rejected


def load_aprilgrid_pairs(input_dir: Path, board) -> tuple:
    objects, points1, points2, accepted, rejected, details = [], [], [], [], [], []
    image_size = None
    manifest_path = input_dir / "capture-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        recorded = manifest.get("board")
        if manifest.get("boardType") not in (None, "aprilgrid"):
            raise ValueError("capture manifest is for another board type; use a new AprilGrid capture session")
        if recorded and any(recorded.get(key) != value for key, value in board.config.items()):
            raise ValueError("AprilGrid profile differs from the capture manifest; verify board dimensions and IDs")
    for left_path in sorted((input_dir / "camera1").glob("*.png")):
        right_path = input_dir / "camera2" / left_path.name
        images = [cv2.imread(str(path)) for path in (left_path, right_path)]
        if any(image is None for image in images):
            rejected.append(f"{left_path.name}: unreadable/missing paired image")
            continue
        left, right = images
        size = (left.shape[1], left.shape[0])
        if right.shape[:2] != left.shape[:2] or (image_size is not None and size != image_size):
            raise ValueError(f"{left_path.name}: mixed image sizes; capture both cameras at the same fixed resolution")
        image_size = size
        try:
            detected = [board.detect(image) for image in images]
            obj, first, second, metadata = board.pair_observations(*detected)
        except ValueError as error:
            rejected.append(f"{left_path.name}: {error}")
            continue
        objects.append(obj)
        points1.append(first)
        points2.append(second)
        accepted.append(left_path.name)
        details.append({"file": left_path.name, **metadata})
    return objects, points1, points2, image_size, accepted, rejected, details


def calibrate(args: argparse.Namespace) -> int:
    board_type = getattr(args, "board_type", "chessboard")
    ensure_calibration_runtime(board_type)
    from spatial_localization import camera_pose, default_config, validate_config

    base = validate_config(json.loads(args.base_config.read_text(encoding="utf-8-sig"))) if args.base_config.exists() else default_config()
    center1, _ = camera_pose(base, "camera1")
    center2, _ = camera_pose(base, "camera2")
    expected_baseline = float(np.linalg.norm(center2 - center1))
    pair_details = []
    if board_type == "aprilgrid":
        board = load_tag_board(args.board_config)
        observations = load_aprilgrid_pairs(args.input, board)
        objects, points1, points2, size, accepted, rejected, pair_details = observations
        board_metadata = board.metadata()
    else:
        observations = load_pairs(args.input, args.columns, args.rows, args.square_size_m)
        objects, points1, points2, size, accepted, rejected = observations
        board_metadata = {"boardType": "chessboard", "columnsInternalCorners": args.columns, "rowsInternalCorners": args.rows, "squareSizeMeters": args.square_size_m}
    diagnostics_path = args.diagnostics or args.output.with_name(args.output.stem + "-diagnostics.json")
    if len(objects) < args.min_pairs:
        write_json(diagnostics_path, {"passed": False, "failures": ["insufficient valid paired boards"],
                   "pairCount": len(objects), "minimumPairs": args.min_pairs, "imageSize": list(size) if size else None,
                   "acceptedFiles": accepted, "rejectedFiles": rejected, "pairDetails": pair_details,
                   "board": board_metadata, "configWritten": False})
        raise ValueError(f"only {len(objects)} valid paired boards; at least {args.min_pairs} required; {len(rejected)} rejected")
    result = calibration_from_observations(
        objects, points1, points2, size,
        max_view_rms_px=args.max_view_rms,
        min_pairs=args.min_pairs,
    )
    retained_indices = result["retainedObservationIndices"]
    retained_set = set(retained_indices)
    view_filter_rejected = []
    for item in result["viewFilter"]["rejectedViews"]:
        entry = {"file": accepted[item["index"]], **item}
        view_filter_rejected.append(entry)
        rejected.append(
            f"{entry['file']}: {entry['reason']} "
            f"(camera1={entry['camera1RmsPx']:.3f} px, camera2={entry['camera2RmsPx']:.3f} px, "
            f"stereoPair={entry['stereoPairRmsPx']:.3f} px)"
        )
    accepted = [name for index, name in enumerate(accepted) if index in retained_set]
    if pair_details:
        pair_details = [detail for index, detail in enumerate(pair_details) if index in retained_set]
    report = quality_diagnostics(result, expected_baseline, args.baseline_tolerance, args.max_rms)
    report.update({"createdUtc": datetime.now(timezone.utc).isoformat(), "acceptedFiles": accepted, "rejectedFiles": rejected,
                   "viewFilterRejectedFiles": view_filter_rejected,
                   "board": board_metadata, "pairDetails": pair_details,
                   "configWritten": False})
    if not report["passed"]:
        write_json(diagnostics_path, report)
        print(f"Calibration rejected; config unchanged. See {diagnostics_path}")
        return 2
    config = export_config(result, base)
    if args.output.exists():
        backup = args.output.with_name(args.output.name + ".before-calibration.json")
        if backup.exists():
            raise ValueError(f"backup already exists: {backup}; choose another output or archive it before replacing configuration")
        backup.write_bytes(args.output.read_bytes())
    write_json(args.output, config)
    report["configWritten"] = True
    write_json(diagnostics_path, report)
    print(f"Calibration config: {args.output}")
    print(f"Diagnostics: {diagnostics_path}")
    print(f"Stereo RMS {report['stereoRmsPx']:.4f} px; raw baseline {report['rawCalibratedBaselineMeters']:.6f} m; anchored baseline {expected_baseline:.6f} m; explicit scale {report['appliedTranslationScale']:.6f}.")
    print("Restart/reload the bridge after offline config replacement. Validate common pitch and independent field distances before claiming accuracy.")
    return 0


def inspect_board(args: argparse.Namespace) -> int:
    """Inspect a single reference image; never infer camera calibration from it."""
    board = load_tag_board(args.board_config)
    image = cv2.imread(str(args.image))
    if image is None:
        raise ValueError("could not read the reference board image")
    detected = board.detect(image)
    report = {"board": board.metadata(), "detectedTagIds": sorted(detected),
              "detectedTagCount": len(detected), "expectedTagCount": len(board._cells),
              "imageSize": [image.shape[1], image.shape[0]],
              "scope": "Single-image dictionary/layout inspection only; not stereo calibration or metric field validation."}
    if len(detected) >= 4:
        identifiers = sorted(detected)
        objects = np.concatenate([board.object_corners(i)[:, :2] for i in identifiers])
        pixels = np.concatenate([detected[i] for i in identifiers])
        homography, _ = cv2.findHomography(objects, pixels, 0)
        if homography is not None:
            projected = cv2.perspectiveTransform(objects.reshape(-1, 1, 2), homography).reshape(-1, 2)
            report["layoutHomographyRmsPx"] = float(np.sqrt(np.mean(np.sum((projected - pixels) ** 2, axis=1))))
    write_json(args.output, report)
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        overlay = image.copy()
        for marker, corners in detected.items():
            vertices = np.round(corners).astype(int)
            cv2.polylines(overlay, [vertices], True, (0, 180, 0), 2)
            center = vertices.mean(axis=0).astype(int)
            cv2.putText(overlay, str(marker), tuple(center), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 255), 2)
            cv2.circle(overlay, tuple(vertices[0]), 4, (0, 0, 255), -1)
        if not cv2.imwrite(str(args.preview), overlay):
            raise ValueError("failed to save the inspected board preview")
    print(f"Detected {len(detected)}/{len(board._cells)} configured tag36h11 tags; report: {args.output}")
    return 0 if len(detected) >= board.config["minCommonTags"] else 2


class _NoRecorder:
    def stream_disconnected(self, _message: str) -> None:
        pass


def capture(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv
    from camera_low_latency import LatestFrameBuffer, StatusStore, StreamWorker, build_sources_and_outputs, parse_args

    if args.manual and not args.display:
        raise ValueError("--manual requires --display; press Space to save and Esc to finish")
    board_type = getattr(args, "board_type", "chessboard")
    board = load_tag_board(getattr(args, "board_config", DEFAULT_TAG_BOARD)) if board_type == "aprilgrid" else None
    if board is None:
        board_points(args.columns, args.rows, 1.0)
    board_metadata = board.metadata() if board else {"boardType": "chessboard", "columnsInternalCorners": args.columns, "rowsInternalCorners": args.rows}
    load_dotenv(Path(__file__).with_name(".env"))
    camera_args = parse_args([])
    password1 = os.getenv("CAMERA1_PASSWORD") or os.getenv("CAMERA_PASSWORD", "")
    password2 = os.getenv("CAMERA2_PASSWORD") or os.getenv("CAMERA_PASSWORD", "")
    if not password1 or not password2:
        raise ValueError("camera passwords are not configured in the existing .env")
    sources, _outputs = build_sources_and_outputs(camera_args, password1, password2)
    logging.getLogger("camera-low-latency").setLevel(logging.CRITICAL)
    buffers = [LatestFrameBuffer(), LatestFrameBuffer()]
    statuses = [StatusStore(), StatusStore()]
    stop = threading.Event()
    readers = [StreamWorker(source.stream, buffer, status, _NoRecorder(), stop, worker_name=source.key) for source, buffer, status in zip(sources, buffers, statuses)]
    for key in ("camera1", "camera2"):
        (args.output / key).mkdir(parents=True, exist_ok=True)
    if any((args.output / "camera1").glob("*.png")) or any((args.output / "camera2").glob("*.png")):
        raise ValueError("capture output already contains images; choose a fresh directory to avoid mixing sessions")
    records = []
    last_sequences = (-1, -1)
    started = time.monotonic()
    last_saved = started - args.interval
    print(f"Board type: {board_type}. Hold still for each capture, then vary position, tilt and coverage.")
    print(f"Keep at least {board.config['minCommonTags']} common AprilGrid tags over multiple rows/columns visible in BOTH cameras." if board else "Keep the entire chessboard visible in BOTH cameras.")
    print("Paired receive times limit network arrival skew only; they do not prove synchronized exposure.")
    check_board = board_detection_available(board_type) and not args.skip_board_check
    if not check_board:
        print("Board detection unavailable or explicitly skipped: RAW PAIRS will be saved without board validation. Visually confirm the full stationary board in both previews; offline calibration will reject missing boards.")
    if args.manual:
        print("Space: save a valid pair. Esc: stop.")
    for reader in readers:
        reader.start()
    try:
        while len(records) < args.pairs and time.monotonic() - started < args.duration:
            packets = [buffer.latest() for buffer in buffers]
            if any(packet is None for packet in packets):
                stop.wait(0.05)
                continue
            packet1, packet2 = packets
            now = time.monotonic()
            sequences = (packet1.sequence, packet2.sequence)
            if sequences == last_sequences:
                stop.wait(0.02)
                continue
            last_sequences = sequences
            key = -1
            if args.display:
                previews = []
                for packet in packets:
                    scale = min(1.0, 800 / packet.image.shape[1])
                    preview = cv2.resize(packet.image, None, fx=scale, fy=scale)
                    previews.append(preview)
                cv2.imshow("Stereo calibration capture - raw saves", np.hstack(previews))
                key = cv2.waitKey(1) & 0xFF
                if key == 27:
                    break
            should_save = key == 32 if args.manual else now - last_saved >= args.interval
            if not should_save:
                stop.wait(0.02)
                continue
            skew = abs(packet1.received_monotonic - packet2.received_monotonic) * 1000
            age = max(now - packet1.received_monotonic, now - packet2.received_monotonic)
            if skew > args.max_skew_ms or age > 0.5:
                stop.wait(0.05)
                continue
            if packet1.image.shape != packet2.image.shape:
                raise ValueError("camera resolutions differ; use identical capture resolution")
            pair_metadata = {}
            if check_board:
                if board:
                    try:
                        detections = [board.detect(packet.image) for packet in packets]
                        _obj, _first, _second, pair_metadata = board.pair_observations(*detections)
                    except ValueError as error:
                        last_saved = now
                        print(f"AprilGrid pair skipped: {error}")
                        continue
                else:
                    corners = [find_board(packet.image, (args.columns, args.rows)) for packet in packets]
                    if any(corner is None for corner in corners):
                        last_saved = now
                        print("Board not detected in both views; pair skipped.")
                        continue
            name = f"{len(records) + 1:04d}.png"
            for index, packet in enumerate(packets, 1):
                if not cv2.imwrite(str(args.output / f"camera{index}" / name), packet.image):
                    raise ValueError("failed to save paired raw image")
            records.append({"file": name, "receivedSkewMs": skew, "sequences": list(sequences),
                            "imageWidth": packet1.image.shape[1], "imageHeight": packet1.image.shape[0],
                            "ptsSeconds": [packet1.pts_seconds, packet2.pts_seconds], "boardCheckedAtCapture": check_board,
                            **pair_metadata})
            last_saved = time.monotonic()
            tag_text = f"; {pair_metadata['commonTagCount']} common tags / {pair_metadata['cornerCount']} corners" if pair_metadata else ""
            print(f"Saved pair {len(records)}/{args.pairs}; receive skew {skew:.1f} ms{tag_text}")
    finally:
        stop.set()
        for reader in readers:
            reader.join(timeout=2)
        if args.display:
            cv2.destroyAllWindows()
        write_json(args.output / "capture-manifest.json", {
            "createdUtc": datetime.now(timezone.utc).isoformat(), "pairs": records,
            "timeMeaning": "Independent RTSP local decode/receive monotonic times; not synchronized exposure times.",
            "boardType": board_type, "board": board_metadata,
            "boardInternalCorners": [args.columns, args.rows] if not board else None, "maxReceiveSkewMs": args.max_skew_ms,
            "boardCheckedAtCapture": check_board,
        })
    print(f"Saved {len(records)} raw board pairs under {args.output}")
    return 0 if len(records) >= args.pairs else 2


def evaluate_samples(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("no measured validation samples")
    values = np.array([[float(row[key]) for key in ("xTruth", "zTruth", "xEstimated", "zEstimated")] for row in rows])
    if not np.isfinite(values).all():
        raise ValueError("all sample coordinates must be finite numbers in metres")
    errors = values[:, 2:] - values[:, :2]
    planar = np.linalg.norm(errors, axis=1)
    return {
        "units": "m", "sampleCount": len(rows),
        "xMaeMeters": float(np.mean(abs(errors[:, 0]))), "zMaeMeters": float(np.mean(abs(errors[:, 1]))),
        "xRmseMeters": float(np.sqrt(np.mean(errors[:, 0] ** 2))), "zRmseMeters": float(np.sqrt(np.mean(errors[:, 1] ** 2))),
        "planarRmseMeters": float(np.sqrt(np.mean(planar ** 2))), "planarMaxErrorMeters": float(planar.max()),
        "perSample": [{"index": index + 1, "xErrorMeters": float(error[0]), "zErrorMeters": float(error[1]), "planarErrorMeters": float(length)} for index, (error, length) in enumerate(zip(errors, planar))],
        "scope": "Independent measured samples only; values are not extrapolated to untested ranges or all people.",
    }


def evaluate(args: argparse.Namespace) -> int:
    with args.samples.open("r", encoding="utf-8-sig", newline="") as handle:
        report = evaluate_samples(list(csv.DictReader(handle)))
    write_json(args.output, report)
    print(f"Field sample report: {args.output}")
    print(f"X RMSE {report['xRmseMeters']:.4f} m; Z RMSE {report['zRmseMeters']:.4f} m; planar RMSE {report['planarRmseMeters']:.4f} m")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture", help="explicitly capture full-resolution paired raw chessboards using existing .env")
    capture_parser.add_argument("--output", type=Path, default=DEFAULT_INPUT)
    capture_parser.add_argument("--board-type", choices=("chessboard", "aprilgrid"), default="chessboard")
    capture_parser.add_argument("--board-config", type=Path, default=DEFAULT_TAG_BOARD)
    capture_parser.add_argument("--columns", type=int, default=9, help="internal corner columns, not square count")
    capture_parser.add_argument("--rows", type=int, default=6, help="internal corner rows, not square count")
    capture_parser.add_argument("--pairs", type=int, default=30)
    capture_parser.add_argument("--interval", type=float, default=4, help="automatic capture interval in seconds (default: 4)")
    capture_parser.add_argument("--duration", type=float, default=240)
    capture_parser.add_argument("--max-skew-ms", type=float, default=100)
    capture_parser.add_argument("--display", action="store_true")
    capture_parser.add_argument("--manual", action="store_true")
    capture_parser.add_argument("--skip-board-check", action="store_true", help="save raw pairs for later full OpenCV board validation; automatic when CUDA OpenCV lacks board detectors")
    capture_parser.set_defaults(run=capture)
    calibration_parser = commands.add_parser("calibrate", help="offline intrinsics/stereo solve; quality gate and measured baseline anchoring")
    calibration_parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    calibration_parser.add_argument("--board-type", choices=("chessboard", "aprilgrid"), default="chessboard")
    calibration_parser.add_argument("--board-config", type=Path, default=DEFAULT_TAG_BOARD)
    calibration_parser.add_argument("--columns", type=int, default=9)
    calibration_parser.add_argument("--rows", type=int, default=6)
    calibration_parser.add_argument("--square-size-m", type=float)
    calibration_parser.add_argument("--min-pairs", type=int, default=15)
    calibration_parser.add_argument("--max-rms", type=float, default=2)
    calibration_parser.add_argument("--max-view-rms", type=float, default=2.0,
                                    help="iteratively exclude the worst paired view above this mono/stereo RMS (px); default: 2.0")
    calibration_parser.add_argument("--baseline-tolerance", type=float, default=0.10)
    calibration_parser.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG)
    calibration_parser.add_argument("--output", type=Path, required=True)
    calibration_parser.add_argument("--diagnostics", type=Path)
    calibration_parser.set_defaults(run=calibrate)
    inspect_parser = commands.add_parser("inspect-board", help="verify AprilGrid IDs/layout in one reference image without calibrating cameras")
    inspect_parser.add_argument("--image", type=Path, required=True)
    inspect_parser.add_argument("--board-config", type=Path, default=DEFAULT_TAG_BOARD)
    inspect_parser.add_argument("--output", type=Path, required=True)
    inspect_parser.add_argument("--preview", type=Path)
    inspect_parser.set_defaults(run=inspect_board)
    evaluation_parser = commands.add_parser("evaluate", help="compute X/Z field error from held-out measured CSV samples")
    evaluation_parser.add_argument("--samples", type=Path, required=True, help="CSV columns: xTruth,zTruth,xEstimated,zEstimated, all metres")
    evaluation_parser.add_argument("--output", type=Path, required=True)
    evaluation_parser.set_defaults(run=evaluate)
    return result


def main(argv: list[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if args.command == "capture" and (args.pairs <= 0 or args.interval <= 0 or args.duration <= 0 or args.max_skew_ms <= 0):
        command_parser.error("pairs, interval, duration and max-skew-ms must be positive")
    if args.command == "calibrate" and args.min_pairs < 3:
        command_parser.error("min-pairs must be >=3")
    if args.command == "calibrate" and args.max_view_rms <= 0:
        command_parser.error("max-view-rms must be positive")
    if args.command == "calibrate" and args.board_type == "chessboard" and args.square_size_m is None:
        command_parser.error("chessboard calibration requires --square-size-m")
    if args.command == "calibrate" and args.board_type == "aprilgrid" and args.square_size_m is not None:
        command_parser.error("AprilGrid uses tagSizeMeters/tagSpacingMeters in --board-config; omit --square-size-m")
    try:
        return args.run(args)
    except (ValueError, OSError, cv2.error) as error:
        # Stream reader exceptions are intentionally not printed with endpoints.
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped by operator.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
