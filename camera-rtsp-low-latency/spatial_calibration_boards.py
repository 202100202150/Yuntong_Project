"""Metric AprilGrid observations, separate from transport and camera runtime.

Board X is right, Y is down, Z=0; origin is the upper-left corner of the
upper-left tag. This temporary board frame is independent of the scene frame.
OpenCV AprilTag dictionaries rotate the original AprilTag patterns by 180
degrees; the attached physical board has been checked explicitly for this.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def aprilgrid_detection_available() -> bool:
    aruco = getattr(cv2, "aruco", None)
    return aruco is not None and hasattr(aruco, "ArucoDetector")


class AprilGridBoard:
    def __init__(self, config: dict[str, Any]):
        if not isinstance(config, dict) or config.get("version") != 1 or config.get("boardType") != "aprilgrid":
            raise ValueError("AprilGrid board config requires version=1 and boardType=aprilgrid")
        self.config = deepcopy(config)
        if config.get("dictionary") != "DICT_APRILTAG_36h11":
            raise ValueError("this verified board requires dictionary DICT_APRILTAG_36h11")
        if isinstance(config.get("markerBorderBits"), bool) or config.get("markerBorderBits") not in (1, 2):
            raise ValueError("markerBorderBits must explicitly be 1 or 2; the verified physical board uses 2")
        for field in ("tagRows", "tagColumns", "minCommonTags"):
            value = config.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{field} must be a positive integer >=2")
        for field in ("tagSizeMeters", "tagSpacingMeters"):
            value = config.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be a positive finite number in metres")
        if config.get("tagRotationDeg") not in (0, 90, 180, 270):
            raise ValueError("tagRotationDeg must be 0, 90, 180 or 270")
        rows, columns = config["tagRows"], config["tagColumns"]
        layout = config.get("idsTopToBottom")
        if not isinstance(layout, list) or len(layout) != rows or any(not isinstance(row, list) or len(row) != columns for row in layout):
            raise ValueError("idsTopToBottom must match tagRows x tagColumns")
        ids = [value for row in layout for value in row]
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 587 for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("tag IDs must be unique integers in the tag36h11 dictionary (0..586)")
        if not 4 <= config["minCommonTags"] <= len(ids):
            raise ValueError("minCommonTags must be between 4 and the board tag count")
        self._cells = {marker: (row, column) for row, line in enumerate(layout) for column, marker in enumerate(line)}
        self._detector = None

    def metadata(self) -> dict[str, Any]:
        result = deepcopy(self.config)
        size, gap = result["tagSizeMeters"], result["tagSpacingMeters"]
        result.update(tagPitchMeters=size + gap,
                      patternWidthMeters=result["tagColumns"] * size + (result["tagColumns"] - 1) * gap,
                      patternHeightMeters=result["tagRows"] * size + (result["tagRows"] - 1) * gap,
                      boardFrame="X right, Y down, Z=0; upper-left tag outer upper-left corner is (0,0,0)",
                      cornerConvention="OpenCV dictionary-canonical order; tagRotationDeg maps it onto the physical board")
        return result

    def object_corners(self, marker: int) -> np.ndarray:
        if marker not in self._cells:
            raise ValueError(f"tag ID {marker} is not on the configured board")
        row, column = self._cells[marker]
        size = self.config["tagSizeMeters"]
        pitch = size + self.config["tagSpacingMeters"]
        # Physical TL, TR, BR, BL, then map to OpenCV's canonical marker order.
        offsets = np.array([[0, 0, 0], [size, 0, 0], [size, size, 0], [0, size, 0]], np.float32)
        offsets = np.roll(offsets, -self.config["tagRotationDeg"] // 90, axis=0)
        return offsets + np.array([column * pitch, row * pitch, 0], np.float32)

    def detect(self, image: np.ndarray) -> dict[int, np.ndarray]:
        if not aprilgrid_detection_available():
            raise ValueError("this OpenCV lacks aruco/AprilTag detection; use E:\\python\\python.exe without replacing the CUDA environment")
        if self._detector is None:
            parameters = cv2.aruco.DetectorParameters()
            parameters.markerBorderBits = self.config["markerBorderBits"]
            parameters.adaptiveThreshWinSizeMax = 73
            parameters.adaptiveThreshWinSizeStep = 4
            parameters.minMarkerPerimeterRate = 0.01
            parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            parameters.cornerRefinementWinSize = 3
            dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
            self._detector = cv2.aruco.ArucoDetector(dictionary, parameters)
        # Work at native image resolution: exported corners index raw pixels.
        corners, identifiers, _rejected = self._detector.detectMarkers(image)
        result: dict[int, np.ndarray] = {}
        if identifiers is None:
            return result
        for marker, points in zip(identifiers.reshape(-1), corners):
            marker = int(marker)
            if marker not in self._cells:
                continue
            if marker in result:
                raise ValueError(f"duplicate board tag ID {marker} in one image; use one board")
            result[marker] = np.asarray(points, np.float32).reshape(4, 2).copy()
        return result

    def pair_observations(self, left: dict[int, np.ndarray], right: dict[int, np.ndarray],
                          min_common_tags: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        minimum = self.config["minCommonTags"] if min_common_tags is None else min_common_tags
        if isinstance(minimum, bool) or not isinstance(minimum, int) or not 4 <= minimum <= len(self._cells):
            raise ValueError("minimum common tag count must be between 4 and the board tag count")
        common = sorted(set(left) & set(right) & set(self._cells))
        if len(common) < minimum:
            raise ValueError(f"only {len(common)} common board tags; at least {minimum} required")
        if len({self._cells[i][0] for i in common}) < 2 or len({self._cells[i][1] for i in common}) < 2:
            raise ValueError("common tags must cover at least two board rows and two columns")
        pixels = []
        for detected in (left, right):
            parts = [np.asarray(detected[i], np.float32).reshape(4, 2) for i in common]
            values = np.concatenate(parts).reshape(-1, 1, 2)
            if not np.isfinite(values).all():
                raise ValueError("detected tag corners must be finite pixels")
            pixels.append(values)
        objects = np.concatenate([self.object_corners(i) for i in common])
        return objects, pixels[0], pixels[1], {"commonTagIds": common, "commonTagCount": len(common),
                                               "cornerCount": len(objects), "camera1TagCount": len(left), "camera2TagCount": len(right)}


def load_tag_board(path: Path | str) -> AprilGridBoard:
    return AprilGridBoard(json.loads(Path(path).read_text(encoding="utf-8-sig")))
