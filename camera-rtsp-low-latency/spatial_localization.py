"""Metric drone localization from paired detections and calibrated camera poses."""
from __future__ import annotations

from copy import deepcopy
import math
import time
from typing import Any

import cv2
import numpy as np

CAMERA_KEYS = ("camera1", "camera2")
_MAX_DRONE_FRAME_SKEW_SECONDS = 0.040
_CAMERA_AXIS_REFLECTION = np.diag([1.0, -1.0, 1.0])


def default_config() -> dict[str, Any]:
    camera = {
        "offset": {"x": 0.0, "y": 0.0, "z": 0.0},
        "yawDeg": 0.0, "pitchDeg": 0.0, "rollDeg": 0.0,
        "intrinsics": {
            "imageWidth": 3840, "imageHeight": 2160,
            "fx": None, "fy": None, "cx": 1920.0, "cy": 1080.0,
            "distortion": [0.0] * 5, "source": "unconfigured",
        },
    }
    return {
        "version": 1,
        "units": "m",
        "coordinateFrame": {
            "mode": "stereo_midpoint",
            "xAxis": "left",
            "yAxis": "up",
            "zAxis": "forward",
            "description": "World axes used by the spatial scene.",
        },
        "rig": {
            "midpoint": {"x": 0.0, "y": 0.0, "z": 0.0},
            "baselineMeters": 0.61,
            "baselineYawDeg": 0.0,
            "geometrySource": "manual",
            "cameraPositions": None,
        },
        "cameras": {key: deepcopy(camera) for key in CAMERA_KEYS},
        "extrinsics": {
            "enabled": False,
            "commonFrame": "AprilGrid",
            "camera1": {
                "rotationCameraToBoard": None,
                "translationCameraInBoard": None,
                "source": "",
            },
            "camera2": {
                "rotationCameraToBoard": None,
                "translationCameraInBoard": None,
                "source": "",
            },
        },
        "stereo": {
            "processingScale": 0.32,
            "minDisparity": 0,
            "numDisparities": 128,
            "blockSize": 5,
            "minValidDepthPoints": 3,
            "minDepthMeters": 0.2,
            "maxDepthMeters": 100.0,
        },
        "quality": {
            "maxFrameSkewMs": 40.0,
            "maxReprojectionErrorPx": 50.0,
            "minFeatureMatches": 3,
            "maxRangeMeters": 100.0,
            "minTriangulationAngleDeg": 0.01,
        },
    }


def _merge(base: dict, update: dict, path: str = "config") -> dict:
    if not isinstance(update, dict):
        raise ValueError(f"{path} must be an object")
    unknown = sorted(set(update) - set(base))
    if unknown:
        raise ValueError(f"{path} contains unknown field(s): {', '.join(map(str, unknown))}")
    for key, value in update.items():
        if isinstance(base.get(key), dict):
            base[key] = _merge(base[key], value, f"{path}.{key}")
        else:
            base[key] = deepcopy(value)
    return base


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    result = _merge(default_config(), raw)
    if result["version"] != 1 or result["units"] != "m":
        raise ValueError("only version 1 and metre units ('m') are supported")
    frame = result["coordinateFrame"]
    for field in ("mode", "xAxis", "yAxis", "zAxis", "description"):
        if not isinstance(frame[field], str) or not frame[field]:
            raise ValueError(f"coordinateFrame.{field} must be a non-empty string")
    rig = result["rig"]
    if rig["geometrySource"] not in ("manual", "extrinsics"):
        raise ValueError("rig.geometrySource must be 'manual' or 'extrinsics'")
    derive_geometry = rig["geometrySource"] == "extrinsics"
    if not derive_geometry:
        for axis in "xyz":
            rig["midpoint"][axis] = _number(rig["midpoint"][axis], f"midpoint.{axis}", -1e6, 1e6)
        rig["baselineMeters"] = _number(rig["baselineMeters"], "baselineMeters", 0.001, 100.0)
        rig["baselineYawDeg"] = _number(rig["baselineYawDeg"], "baselineYawDeg", -360.0, 360.0)
    for key in CAMERA_KEYS:
        camera = result["cameras"][key]
        for axis in "xyz":
            camera["offset"][axis] = _number(
                camera["offset"][axis], f"{key}.offset.{axis}", -100.0, 100.0
            )
        for angle in ("yawDeg", "pitchDeg", "rollDeg"):
            camera[angle] = _number(camera[angle], f"{key}.{angle}", -360.0, 360.0)
        intrinsics = camera["intrinsics"]
        for dimension in ("imageWidth", "imageHeight"):
            numeric = _number(intrinsics[dimension], f"{key}.{dimension}", 1, 32768)
            if int(numeric) != numeric:
                raise ValueError(f"{key}.{dimension} must be an integer")
            intrinsics[dimension] = int(numeric)
        for focal in ("fx", "fy"):
            if intrinsics[focal] is not None:
                intrinsics[focal] = _number(intrinsics[focal], f"{key}.{focal}", 0.01, 1e7)
        for principal in ("cx", "cy"):
            intrinsics[principal] = _number(
                intrinsics[principal], f"{key}.{principal}", -1e6, 1e6
            )
        coefficients = intrinsics["distortion"]
        if not isinstance(coefficients, list) or len(coefficients) not in (4, 5, 8, 12, 14):
            raise ValueError(
                f"{key}.distortion must contain 4, 5, 8, 12 or 14 OpenCV coefficients"
            )
        intrinsics["distortion"] = [
            _number(value, f"{key}.distortion", -1e4, 1e4) for value in coefficients
        ]
        if not isinstance(intrinsics["source"], str):
            raise ValueError(f"{key}.intrinsics.source must be a string")

    stereo = result["stereo"]
    stereo["processingScale"] = _number(
        stereo["processingScale"], "stereo.processingScale", 0.1, 1.0
    )
    stereo["minDisparity"] = _number(
        stereo["minDisparity"], "stereo.minDisparity", -256.0, 256.0
    )
    stereo["numDisparities"] = _number(
        stereo["numDisparities"], "stereo.numDisparities", 16.0, 512.0
    )
    if int(stereo["numDisparities"]) != stereo["numDisparities"] or int(stereo["numDisparities"]) % 16:
        raise ValueError("stereo.numDisparities must be a multiple of 16")
    stereo["numDisparities"] = int(stereo["numDisparities"])
    stereo["blockSize"] = _number(stereo["blockSize"], "stereo.blockSize", 3.0, 21.0)
    if int(stereo["blockSize"]) != stereo["blockSize"] or int(stereo["blockSize"]) % 2 == 0:
        raise ValueError("stereo.blockSize must be an odd integer")
    stereo["blockSize"] = int(stereo["blockSize"])
    stereo["minValidDepthPoints"] = _number(
        stereo["minValidDepthPoints"], "stereo.minValidDepthPoints", 1.0, 100000.0
    )
    if int(stereo["minValidDepthPoints"]) != stereo["minValidDepthPoints"]:
        raise ValueError("stereo.minValidDepthPoints must be an integer")
    stereo["minValidDepthPoints"] = int(stereo["minValidDepthPoints"])
    stereo["minDepthMeters"] = _number(
        stereo["minDepthMeters"], "stereo.minDepthMeters", 0.01, 1000.0
    )
    stereo["maxDepthMeters"] = _number(
        stereo["maxDepthMeters"], "stereo.maxDepthMeters",
        stereo["minDepthMeters"], 10000.0,
    )

    extrinsics = result["extrinsics"]
    if not isinstance(extrinsics["enabled"], bool):
        raise ValueError("extrinsics.enabled must be boolean")
    if not isinstance(extrinsics["commonFrame"], str) or not extrinsics["commonFrame"]:
        raise ValueError("extrinsics.commonFrame must be a non-empty string")
    if derive_geometry and not extrinsics["enabled"]:
        raise ValueError("deriving rig geometry requires both enabled camera extrinsics")
    if extrinsics["enabled"]:
        for key in CAMERA_KEYS:
            item = extrinsics[key]
            rotation = item["rotationCameraToBoard"]
            translation = item["translationCameraInBoard"]
            if (
                not isinstance(rotation, list)
                or len(rotation) != 3
                or any(not isinstance(row, list) or len(row) != 3 for row in rotation)
            ):
                raise ValueError(f"extrinsics.{key}.rotationCameraToBoard must be a 3x3 matrix")
            if not isinstance(translation, list) or len(translation) != 3:
                raise ValueError(f"extrinsics.{key}.translationCameraInBoard must contain 3 values")
            matrix = np.asarray(rotation, dtype=np.float64)
            vector = np.asarray(translation, dtype=np.float64)
            if not np.isfinite(matrix).all() or not np.isfinite(vector).all():
                raise ValueError(f"extrinsics.{key} contains non-finite values")
            determinant = float(np.linalg.det(matrix))
            if abs(determinant) < 1e-6:
                raise ValueError(f"extrinsics.{key}.rotationCameraToBoard is singular")
            if derive_geometry and (
                abs(determinant - 1.0) > 1e-3
                or not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-3)
            ):
                raise ValueError(f"extrinsics.{key}.rotationCameraToBoard must be a proper rotation")

    quality = result["quality"]
    limits = {
        "maxFrameSkewMs": (1.0, 5000.0),
        "maxReprojectionErrorPx": (0.05, 100.0),
        "maxRangeMeters": (0.1, 10000.0),
        "minFeatureMatches": (1, 1000),
        "minTriangulationAngleDeg": (0.001, 45.0),
    }
    for key, (low, high) in limits.items():
        quality[key] = _number(quality[key], f"quality.{key}", low, high)
    if int(quality["minFeatureMatches"]) != quality["minFeatureMatches"]:
        raise ValueError("quality.minFeatureMatches must be an integer")
    quality["minFeatureMatches"] = int(quality["minFeatureMatches"])

    positions = camera_positions(result)
    separation = float(np.linalg.norm(_xyz(positions["camera1"]) - _xyz(positions["camera2"])))
    if separation < 1e-5:
        raise ValueError("camera optical centres must not coincide")
    rig["cameraPositions"] = None
    if derive_geometry:
        rig["baselineMeters"] = separation
        rig["baselineYawDeg"] = 0.0
        rig["midpoint"] = dict.fromkeys("xyz", 0.0)
        rig["cameraPositions"] = {
            key: {
                **position,
                "side": "left" if position["x"] > 0.0 else "right",
                "distanceToOriginMeters": separation * 0.5,
            }
            for key, position in positions.items()
        }
        frame.update(mode="stereo_midpoint", xAxis="left", yAxis="up", zAxis="forward")
    return result


def derive_extrinsic_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a validated config whose rig geometry is based on both poses."""
    config = _merge(default_config(), raw)
    config["rig"]["geometrySource"] = "extrinsics"
    return validate_config(config)


def calibration_ready(config: dict[str, Any]) -> bool:
    return all(
        str(config["cameras"][key]["intrinsics"].get("source", "")).startswith("calibrated")
        and all(config["cameras"][key]["intrinsics"][focal] is not None for focal in ("fx", "fy"))
        for key in CAMERA_KEYS
    )


def _xyz(value: dict) -> np.ndarray:
    return np.array([value[axis] for axis in "xyz"], dtype=np.float64)


def _extrinsic_geometry(config: dict[str, Any]) -> dict[str, Any]:
    """Build the midpoint frame, preserving manual scale for legacy configs."""
    extrinsics = config["extrinsics"]
    rotations = {
        key: np.asarray(extrinsics[key]["rotationCameraToBoard"], dtype=np.float64)
        for key in CAMERA_KEYS
    }
    board_centres = {
        key: np.asarray(extrinsics[key]["translationCameraInBoard"], dtype=np.float64)
        for key in CAMERA_KEYS
    }
    raw_delta = board_centres["camera2"] - board_centres["camera1"]
    raw_norm = float(np.linalg.norm(raw_delta))
    if raw_norm <= 1e-9:
        raise ValueError("extrinsic camera centres must be distinct")
    x_axis = raw_delta / raw_norm
    optical = np.mean([rotation[:, 2] for rotation in rotations.values()], axis=0)
    derive_geometry = config["rig"].get("geometrySource") == "extrinsics"
    if derive_geometry:
        baseline = _number(raw_norm, "extrinsic camera centre separation (metres)", 0.001, 100.0)
        # OpenCV camera X points to image right and Y to image down. Their
        # common view gives side/up independently of the board orientation.
        left_reference = -np.mean([rotation[:, 0] for rotation in rotations.values()], axis=0)
        y_reference = -np.mean([rotation[:, 1] for rotation in rotations.values()], axis=0)
        left_norm = float(np.linalg.norm(left_reference))
        lateral = float(x_axis @ left_reference)
        if left_norm <= 1e-9 or abs(lateral) <= left_norm * 1e-3:
            raise ValueError("extrinsic baseline must run left to right across the common camera view")
        camera2_side = math.copysign(1.0, lateral)
        x_axis *= camera2_side
    else:
        baseline = float(config["rig"]["baselineMeters"])
        y_reference = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        camera2_side = 1.0
    y_reference -= x_axis * float(np.dot(y_reference, x_axis))
    if np.linalg.norm(y_reference) <= 1e-9:
        raise ValueError("extrinsic baseline is parallel to the up reference")
    y_axis = y_reference / np.linalg.norm(y_reference)
    if derive_geometry:
        z_axis = np.cross(x_axis, y_axis)
        if float(z_axis @ optical) <= 1e-9:
            raise ValueError("extrinsic rotations must share an upright forward view")
    else:
        z_axis = np.cross(y_axis, x_axis)
        optical -= x_axis * float(np.dot(optical, x_axis))
        if np.linalg.norm(optical) > 1e-9 and float(z_axis @ optical) < 0.0:
            z_axis = -z_axis
    z_axis /= np.linalg.norm(z_axis)
    axes = np.column_stack((x_axis, y_axis, z_axis))
    midpoint_board = (board_centres["camera1"] + board_centres["camera2"]) * 0.5
    camera2_x = camera2_side * baseline * 0.5
    centres = {
        "camera1": np.array([-camera2_x, 0.0, 0.0], dtype=np.float64),
        "camera2": np.array([camera2_x, 0.0, 0.0], dtype=np.float64),
    }
    world_to_camera = {key: axes.T @ rotations[key] for key in CAMERA_KEYS}
    return {
        "centres": centres,
        "worldToCamera": world_to_camera,
        "axesBoard": axes,
        "midpointBoard": midpoint_board,
        "baselineMeters": baseline,
    }


def camera_positions(config: dict[str, Any]) -> dict[str, dict[str, float]]:
    if config.get("extrinsics", {}).get("enabled"):
        geometry = _extrinsic_geometry(config)
        return {
            key: dict(zip("xyz", geometry["centres"][key].tolist()))
            for key in CAMERA_KEYS
        }
    rig = config["rig"]
    angle = math.radians(rig["baselineYawDeg"])
    half_baseline = rig["baselineMeters"] * 0.5 * np.array(
        [math.cos(angle), 0.0, math.sin(angle)]
    )
    midpoint = _xyz(rig["midpoint"])
    return {
        key: dict(
            zip(
                "xyz",
                (midpoint + sign * half_baseline + _xyz(config["cameras"][key]["offset"])).tolist(),
            )
        )
        for key, sign in (("camera1", -1), ("camera2", 1))
    }


def camera_pose(config: dict[str, Any], key: str) -> tuple[np.ndarray, np.ndarray]:
    """Return optical centre and row-vector world-to-camera transform."""
    if config.get("extrinsics", {}).get("enabled"):
        geometry = _extrinsic_geometry(config)
        return geometry["centres"][key], geometry["worldToCamera"][key]
    camera = config["cameras"][key]
    yaw, pitch, roll = (
        math.radians(camera[name]) for name in ("yawDeg", "pitchDeg", "rollDeg")
    )
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(-pitch), math.sin(-pitch)
    cr, sr = math.cos(-roll), math.sin(-roll)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], dtype=np.float64)
    rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]], dtype=np.float64)
    return _xyz(camera_positions(config)[key]), ry @ rx @ rz @ _CAMERA_AXIS_REFLECTION


def pose_angles_from_camera_to_world(matrix: np.ndarray) -> dict[str, float]:
    proper = np.asarray(matrix, dtype=np.float64) @ _CAMERA_AXIS_REFLECTION
    pitch = math.asin(float(np.clip(proper[1, 2], -1.0, 1.0)))
    if abs(math.cos(pitch)) > 1e-7:
        yaw = math.atan2(float(proper[0, 2]), float(proper[2, 2]))
        roll = -math.atan2(float(proper[1, 0]), float(proper[1, 1]))
    else:
        yaw = math.atan2(-float(proper[2, 0]), float(proper[0, 0]))
        roll = 0.0
    return dict(zip(("yawDeg", "pitchDeg", "rollDeg"), map(math.degrees, (yaw, pitch, roll))))


def _intrinsics(
    config: dict, key: str, image_size: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    source = config["cameras"][key]["intrinsics"]
    sx, sy = image_size[0] / source["imageWidth"], image_size[1] / source["imageHeight"]
    matrix = np.array(
        [
            [source["fx"] * sx, 0.0, source["cx"] * sx],
            [0.0, source["fy"] * sy, source["cy"] * sy],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return matrix, np.asarray(source["distortion"], dtype=np.float64)


def _project_world(
    points: np.ndarray,
    centre: np.ndarray,
    world_to_camera: np.ndarray,
    matrix: np.ndarray,
    distortion: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    camera_points = (points - centre) @ world_to_camera
    pixels, _ = cv2.projectPoints(
        camera_points, np.zeros(3), np.zeros(3), matrix, distortion
    )
    return pixels.reshape(-1, 2), camera_points[:, 2]


def triangulate_correspondences(
    points1: Any,
    points2: Any,
    config: dict[str, Any],
    image_sizes: dict[str, tuple[int, int]],
) -> tuple[np.ndarray, dict]:
    """Triangulate matched native-image pixels in the calibrated midpoint frame."""
    config = validate_config(config)
    if not calibration_ready(config):
        raise ValueError("both cameras require calibrated fx and fy")
    first = np.asarray(points1, dtype=np.float64).reshape(-1, 2)
    second = np.asarray(points2, dtype=np.float64).reshape(-1, 2)
    if len(first) != len(second):
        raise ValueError("correspondence arrays must have equal length")
    stats: dict[str, Any] = {
        "inputCount": len(first), "validCount": 0, "indices": [], "reprojectionErrors": []
    }
    if not len(first):
        return np.empty((0, 3), dtype=np.float64), stats
    if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
        raise ValueError("correspondence pixels must be finite")
    poses = [camera_pose(config, key) for key in CAMERA_KEYS]
    lenses = [
        _intrinsics(config, key, image_sizes[key]) for key in CAMERA_KEYS
    ]
    normalized = [
        cv2.undistortPoints(pixels.reshape(-1, 1, 2), *lens).reshape(-1, 2)
        for pixels, lens in zip((first, second), lenses)
    ]
    projections = [
        np.column_stack((world_to_camera.T, -world_to_camera.T @ centre))
        for centre, world_to_camera in poses
    ]
    homogeneous = cv2.triangulatePoints(
        projections[0], projections[1], normalized[0].T, normalized[1].T
    ).T
    finite = np.abs(homogeneous[:, 3]) > 1e-12
    world_points = np.full((len(first), 3), np.nan)
    world_points[finite] = homogeneous[finite, :3] / homogeneous[finite, 3:4]
    projected = np.nan_to_num(world_points, nan=1e10, posinf=1e10, neginf=-1e10)
    errors, depths, rays = [], [], []
    for pixels, (centre, rotation), (matrix, distortion), norm in zip(
        (first, second), poses, lenses, normalized
    ):
        reprojection, depth = _project_world(
            projected, centre, rotation, matrix, distortion
        )
        errors.append(np.linalg.norm(reprojection - pixels, axis=1))
        depths.append(depth)
        direction = np.column_stack((norm, np.ones(len(norm)))) @ rotation.T
        rays.append(direction / np.linalg.norm(direction, axis=1)[:, None])
    reprojection_error = np.maximum(*errors)
    angles = np.degrees(
        np.arccos(np.clip(np.sum(rays[0] * rays[1], axis=1), -1, 1))
    )
    quality = config["quality"]
    valid = (
        finite
        & np.all(np.isfinite(world_points), axis=1)
        & (depths[0] > 0.01)
        & (depths[1] > 0.01)
        & (reprojection_error <= quality["maxReprojectionErrorPx"])
        & (np.linalg.norm(world_points, axis=1) <= quality["maxRangeMeters"])
        & (angles >= quality["minTriangulationAngleDeg"])
    )
    indices = np.flatnonzero(valid)
    stats.update(
        {
            "validCount": len(indices),
            "rejectedCount": int(len(first) - len(indices)),
            "indices": indices.tolist(),
            "reprojectionErrors": reprojection_error[valid].tolist(),
            "triangulationAnglesDeg": angles[valid].tolist(),
        }
    )
    return world_points[valid], stats


def spatial_frame_skew_seconds(config: dict[str, Any]) -> float:
    # Older configurations accepted up to a second for slow-moving targets.
    return min(_MAX_DRONE_FRAME_SKEW_SECONDS, config["quality"]["maxFrameSkewMs"] / 1000.0)


def _drone_observations(
    objects: tuple[Any, ...], size: tuple[int, int],
) -> list[tuple[str, tuple[float, float], float]]:
    width, height = size
    observations = []
    for item in objects:
        x1, y1, x2, y2 = item.bbox
        # At a frame edge, the visible fragment's centre no longer represents the drone.
        if not (0 < x1 < x2 < width - 1 and 0 < y1 < y2 < height - 1):
            continue
        centre = ((x1 + x2) * 0.5, (y1 + y2) * 0.5)
        observations.append((item.object_id, centre, math.hypot(x2 - x1, y2 - y1)))
    return observations


def _unambiguous_matches(
    candidates: list[tuple[int, int]], indices: list[int], valid: list[int],
    errors: list[float], margin: float,
) -> list[int]:
    """Return indices into the validated geometry results, unique in both cameras."""
    by_camera: tuple[dict[int, list[int]], dict[int, list[int]]] = ({}, {})
    for match in valid:
        for groups, camera_index in zip(by_camera, candidates[indices[match]]):
            groups.setdefault(camera_index, []).append(match)

    def choices(groups: dict[int, list[int]]) -> set[int]:
        unique = set()
        for matches in groups.values():
            ranked = sorted(matches, key=errors.__getitem__)
            if len(ranked) == 1 or errors[ranked[1]] - errors[ranked[0]] > margin:
                unique.add(ranked[0])
        return unique

    return sorted(choices(by_camera[0]) & choices(by_camera[1]))


def _stream_order_is_reversed(
    frames: dict[str, np.ndarray], config: dict[str, Any], image_sizes: dict[str, tuple[int, int]],
) -> bool | None:
    """Return the verified order, or None when the shared scene is inconclusive."""
    detector = cv2.SIFT_create(nfeatures=600)
    pixels, descriptors = [], []
    for key in CAMERA_KEYS:
        frame = frames[key]
        height, width = frame.shape[:2]
        thumb_size = min(560, width), max(1, round(height * min(560, width) / width))
        thumb = cv2.resize(frame, thumb_size, interpolation=cv2.INTER_AREA)
        features, description = detector.detectAndCompute(cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY), None)
        if description is None or len(features) < 12:
            return None
        xy = np.float32([feature.pt for feature in features])
        xy *= (width / thumb_size[0], height / thumb_size[1])
        pixels.append(xy)
        descriptors.append(description)

    matcher = cv2.BFMatcher(cv2.NORM_L2)

    def distinctive(first: np.ndarray, second: np.ndarray) -> dict[int, int]:
        return {
            hits[0].queryIdx: hits[0].trainIdx for hits in matcher.knnMatch(first, second, k=2)
            if len(hits) == 2 and hits[0].distance < 0.72 * hits[1].distance
        }

    forward = distinctive(descriptors[0], descriptors[1])
    backward = distinctive(descriptors[1], descriptors[0])
    matches = [(i, j) for i, j in forward.items() if backward.get(j) == i]
    if len(matches) < 12:
        return None
    first, second = np.asarray(matches).T
    raw1, raw2 = pixels[0][first], pixels[1][second]

    scene_config = {
        **config,
        "quality": {
            **config["quality"],
            "maxRangeMeters": 10000.0,
            "maxReprojectionErrorPx": min(100.0, max(4.0, max(width for width, _ in image_sizes.values()) / 128.0)),
            "minTriangulationAngleDeg": 0.05,
        },
    }
    _, normal = triangulate_correspondences(raw1, raw2, scene_config, image_sizes)
    _, reversed_ = triangulate_correspondences(
        raw2, raw1, scene_config,
        {"camera1": image_sizes["camera2"], "camera2": image_sizes["camera1"]},
    )
    normal_inliers = np.asarray(normal["indices"], dtype=int)
    reverse_inliers = np.asarray(reversed_["indices"], dtype=int)
    height, width = frames["camera1"].shape[:2]
    for is_reversed, selected, competing in (
            (True, reverse_inliers, normal_inliers), (False, normal_inliers, reverse_inliers)):
        if len(selected) < max(12, len(matches) * 0.25, 2.5 * len(competing)):
            continue
        cells = np.floor(raw1[selected] / (width / 3, height / 3)).astype(int)
        if len(np.unique(cells, axis=0)) >= 3:
            return is_reversed
    return None



class SpatialLocalizer:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = validate_config(config)
        self._verified_reversed_streams: bool | None = None
        self._last_stream_order_check = -math.inf

    @property
    def stream_order(self) -> bool | None:
        """True for reversed, False for configured, None while unverified."""
        return self._verified_reversed_streams

    def _frame_sizes(self, frames: dict[str, np.ndarray]) -> dict[str, tuple[int, int]]:
        sizes = {key: (frame.shape[1], frame.shape[0]) for key, frame in frames.items()}
        for key, (width, height) in sizes.items():
            intrinsics = self.config["cameras"][key]["intrinsics"]
            aspect = intrinsics["imageWidth"] / intrinsics["imageHeight"]
            if abs(width / height / aspect - 1.0) > 0.02:
                raise ValueError("检测帧宽高比与标定不一致，请检查裁剪、旋转或标定分辨率。")
        return sizes

    def observe_stream_order(self, camera1_frame: np.ndarray, camera2_frame: np.ndarray) -> None:
        now = time.monotonic()
        if (self._verified_reversed_streams is not None
                or now - self._last_stream_order_check < 3.0
                or not calibration_ready(self.config)):
            return
        self._last_stream_order_check = now
        frames = dict(zip(CAMERA_KEYS, (camera1_frame, camera2_frame)))
        try:
            self._verified_reversed_streams = _stream_order_is_reversed(
                frames, self.config, self._frame_sizes(frames),
            )
        except (cv2.error, ValueError, TypeError):
            # Keep the configured order until the common scene provides strong evidence.
            return

    def localize(self, left: Any | None, right: Any | None) -> dict[str, Any]:
        config = self.config
        max_skew_ms = spatial_frame_skew_seconds(config) * 1000.0
        payload: dict[str, Any] = {
            "state": "uncalibrated",
            "message": "请先完成两台相机的内参与外参配置。",
            "units": "m",
            "cameras": camera_positions(config),
            "targets": [],
            "diagnostics": {
                "hardwareSynchronized": False,
                "pointMethod": "drone_box_center_triangulation",
                "pairMethod": "time_and_reprojection",
                "calibrationReady": calibration_ready(config),
                "baselineMeters": config["rig"]["baselineMeters"],
                "maxFrameSkewMs": max_skew_ms,
            },
        }
        if not payload["diagnostics"]["calibrationReady"]:
            return payload
        if left is None or right is None:
            payload.update(
                state="waiting_for_frames",
                message="等待两路接近同一时刻的检测帧。",
            )
            return payload

        left_time = getattr(left, "frame_monotonic", None)
        right_time = getattr(right, "frame_monotonic", None)
        estimated = left_time is not None and right_time is not None
        if not estimated:
            left_time, right_time = left.received_monotonic, right.received_monotonic
        skew_ms = abs(left_time - right_time) * 1000.0
        payload["diagnostics"].update(
            timestampSource="estimated_frame_time" if estimated else "host_decoded_frame_arrival",
            frameSkewMs=round(skew_ms, 3),
            frameSequences={"camera1": left.sequence, "camera2": right.sequence},
        )
        if skew_ms > max_skew_ms + 1e-6:
            payload.update(
                state="frame_time_gap",
                message="两路估计帧时刻相差过大，等待下一组画面。",
            )
            return payload
        if not left.objects or not right.objects:
            payload.update(state="no_targets", message="等待两路画面同时看到无人机。")
            return payload

        try:
            sizes = self._frame_sizes({"camera1": left.frame, "camera2": right.frame})
        except ValueError as exc:
            payload.update(state="error", message=str(exc))
            return payload

        observations1 = _drone_observations(left.objects, sizes["camera1"])
        observations2 = _drone_observations(right.objects, sizes["camera2"])
        if not observations1 or not observations2:
            payload.update(
                state="no_valid_matches",
                message="等待两路画面均完整看到无人机机体。",
            )
            return payload

        candidates = [(i, j) for i in range(len(observations1)) for j in range(len(observations2))]
        pixels1 = [observations1[i][1] for i, _ in candidates]
        pixels2 = [observations2[j][1] for _, j in candidates]
        width_scale = max(width for width, _ in sizes.values()) / 3840.0
        reprojection_cap = min(config["quality"]["maxReprojectionErrorPx"], max(2.0, 20.0 * width_scale))
        # Centre jitter can be larger for a nearby drone whose silhouette changes with angle.
        pair_limits = [
            min(reprojection_cap, max(1.0, 4.0 * width_scale) + 0.08 * min(observations1[i][2], observations2[j][2]))
            for i, j in candidates
        ]
        max_reprojection = max(pair_limits)
        drone_config = {
            **config,
            "quality": {**config["quality"], "maxReprojectionErrorPx": max_reprojection},
        }
        if self.stream_order is True:
            pixels1, pixels2 = pixels2, pixels1
            sizes = {"camera1": sizes["camera2"], "camera2": sizes["camera1"]}

        points, stats = triangulate_correspondences(pixels1, pixels2, drone_config, sizes)
        indices = stats["indices"]
        errors = stats["reprojectionErrors"]
        valid = [match for match, index in enumerate(indices) if errors[match] <= pair_limits[index]]
        matches = _unambiguous_matches(candidates, indices, valid, errors, max(0.5, width_scale))
        payload["diagnostics"].update(
            candidatePairs=len(candidates),
            geometryPairs=len(valid),
            acceptedPairs=len(matches),
            maxReprojectionErrorPx=max_reprojection,
            streamOrder=(
                "reversed" if self.stream_order is True else
                "configured" if self.stream_order is False else "unverified"
            ),
        )
        if not matches:
            payload.update(
                state="no_valid_matches",
                message="当前两路画面未得到可靠的无人机配对，等待下一组画面。",
            )
            return payload

        for index in matches:
            i, j = candidates[indices[index]]
            id1, id2 = observations1[i][0], observations2[j][0]
            x, y, z = map(float, points[index])
            payload["targets"].append({
                "id": f"stereo:{id1}:{id2}",
                "label": "drone",
                "class": "drone",
                "camera1Id": id1,
                "camera2Id": id2,
                "x": round(x, 4),
                "y": round(y, 4),
                "z": round(z, 4),
                "rangeMeters": round(math.hypot(x, y, z), 4),
                "azimuthDeg": round(math.degrees(math.atan2(x, z)), 3),
                "elevationDeg": round(math.degrees(math.atan2(y, math.hypot(x, z))), 3),
                "keypointName": "drone_box_center",
                "keypointCount": 1,
                "reprojectionErrorPx": round(errors[index], 4),
                "frameSkewMs": round(skew_ms, 3),
                "quality": "calibrated",
            })

        payload.update(state="ready", message="已根据双路无人机机体中心估算三维位置。")
        return payload
