#!/usr/bin/env python3
"""Authenticated loopback bridge for the dual-camera Electron UI.

The bridge owns the RTSP connections and the only disk-writing operations
(explicit recording and explicit snapshot requests). Preview JPEGs are encoded
in memory, cached and rate-limited. Paths are configuration-only: the HTTP API
never accepts a filesystem path from a client.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, replace
from fractions import Fraction
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import struct
import sys
import threading
import time
import queue
from typing import Any, Callable, Protocol
from urllib.parse import quote, urlsplit
import zlib

import av
import cv2
import numpy as np
from dotenv import load_dotenv

from camera_low_latency import (
    CameraSource,
    LatestFrameBuffer,
    OutputConfig,
    PacketRecorder,
    RecordingStatus,
    SnapshotWriter,
    StatusStore,
    StreamWorker,
    build_sources_and_outputs,
    parse_args as parse_camera_args,
    safe_file_component,
    timestamp_for_filename,
    validate_args as validate_camera_args,
)
from tensorrt_person_detector import TensorRTPersonDetector


LOG = logging.getLogger("camera-bridge")
LOOPBACK_HOST = "127.0.0.1"
CAMERA_KEYS = ("camera1", "camera2")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_DIMENSION = 16_384
MAX_PNG_PIXELS = 100_000_000
ACTIVE_RECORDING_STATES = frozenset({"armed", "starting", "recording", "stopping"})


class BridgeApiError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class SecretRedactionFilter(logging.Filter):
    """Prevent credentials from appearing in sidecar stderr logs."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        encoded = [quote(secret, safe="") for secret in secrets if secret]
        self._secrets = sorted({*filter(None, secrets), *encoded}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "***")
        record.msg = message
        record.args = ()
        return True


def validate_png(data: bytes) -> tuple[int, int]:
    """Strictly validate a bounded PNG container and return its dimensions."""
    if len(data) < 45 or not data.startswith(PNG_SIGNATURE):
        raise ValueError("body is not a PNG image")
    offset = len(PNG_SIGNATURE)
    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    width = height = 0
    chunk_index = 0
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("PNG contains a truncated chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("PNG chunk length exceeds the request body")
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(chunk_data, actual_crc) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError("PNG chunk checksum is invalid")
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError("PNG must begin with a valid IHDR chunk")
            width, height = struct.unpack(">II", chunk_data[:8])
            if width < 1 or height < 1:
                raise ValueError("PNG dimensions must be positive")
            if width > MAX_PNG_DIMENSION or height > MAX_PNG_DIMENSION:
                raise ValueError("PNG dimensions exceed the safety limit")
            if width * height > MAX_PNG_PIXELS:
                raise ValueError("PNG pixel count exceeds the safety limit")
            if chunk_data[10] != 0 or chunk_data[11] != 0:
                raise ValueError("PNG uses an unsupported compression or filter method")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise ValueError("PNG contains more than one IHDR chunk")
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise ValueError("PNG IEND chunk is malformed or followed by extra data")
            saw_iend = True
        offset = end
        chunk_index += 1
        if saw_iend:
            break
    if not (saw_ihdr and saw_idat and saw_iend):
        raise ValueError("PNG is missing required chunks")
    return width, height


@dataclass(frozen=True)
class PersonObject:
    object_id: str
    confidence: float
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class DetectionResult:
    sequence: int
    received_monotonic: float
    completed_monotonic: float
    frame: np.ndarray
    objects: tuple[PersonObject, ...]


@dataclass(frozen=True)
class PersonAppearance:
    clothing: np.ndarray
    texture: np.ndarray
    head: np.ndarray
    aspect_ratio: float


def _person_appearance_descriptor(
    frame: np.ndarray,
    bbox: tuple[float, float, float, float],
) -> PersonAppearance | None:
    """Describe color, texture, head region and box shape without another model."""

    frame_height, frame_width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(frame_width, int(round(x1))))
    y1 = max(0, min(frame_height, int(round(y1))))
    x2 = max(0, min(frame_width, int(round(x2))))
    y2 = max(0, min(frame_height, int(round(y2))))
    box_width = x2 - x1
    box_height = y2 - y1
    if box_width < 12 or box_height < 24:
        return None

    inset_x = int(box_width * 0.12)
    crop = frame[y1:y2, x1 + inset_x : x2 - inset_x]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    height = hsv.shape[0]
    # Overlapping upper/lower bands reduce sensitivity to small box shifts.
    bands = ((0.12, 0.58), (0.42, 0.92))
    parts: list[np.ndarray] = []
    for top, bottom in bands:
        start = max(0, min(height - 1, int(height * top)))
        end = max(start + 1, min(height, int(height * bottom)))
        band = hsv[start:end]
        histogram = cv2.calcHist([band], [0, 1], None, [12, 4], [0, 180, 0, 256])
        histogram = histogram.astype(np.float32, copy=False).reshape(-1)
        total = float(histogram.sum())
        if total <= 0:
            return None
        histogram /= total
        parts.append(np.sqrt(histogram))
    clothing = np.concatenate(parts).astype(np.float32, copy=False) / np.sqrt(2.0)

    head_band = hsv[: max(1, int(height * 0.25))]
    head_hist = cv2.calcHist([head_band], [0, 1], None, [12, 4], [0, 180, 0, 256])
    head_hist = head_hist.astype(np.float32, copy=False).reshape(-1)
    head_total = float(head_hist.sum())
    if head_total <= 0:
        return None
    head = np.sqrt(head_hist / head_total)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(cv2.resize(gray, (64, 128), interpolation=cv2.INTER_AREA))
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude, angle = cv2.cartToPolar(gradient_x, gradient_y, angleInDegrees=True)
    direction = np.minimum((np.mod(angle, 180.0) / 20.0).astype(np.int32), 8)
    cells: list[np.ndarray] = []
    for top in range(0, 128, 32):
        for left in range(0, 64, 32):
            bins = direction[top : top + 32, left : left + 32]
            weights = magnitude[top : top + 32, left : left + 32]
            cells.append(np.bincount(bins.ravel(), weights=weights.ravel(), minlength=9))
    texture = np.concatenate(cells).astype(np.float32, copy=False)
    texture_norm = float(np.linalg.norm(texture))
    if texture_norm > 0:
        texture /= texture_norm
    return PersonAppearance(clothing, texture, head, box_width / box_height)


def _appearance_similarity(first: PersonAppearance, second: PersonAppearance) -> float:
    color = float(np.dot(first.clothing, second.clothing))
    first_plain = not np.any(first.texture)
    second_plain = not np.any(second.texture)
    texture = (
        1.0 if first_plain and second_plain
        else 0.0 if first_plain or second_plain
        else float(np.dot(first.texture, second.texture))
    )
    head = float(np.dot(first.head, second.head))
    ratio = first.aspect_ratio / second.aspect_ratio
    shape = float(np.exp(-1.5 * abs(np.log(ratio))))
    # Box aspect ratio is only a weak build cue, never a height measurement.
    return 0.60 * color + 0.25 * texture + 0.10 * shape + 0.05 * head


def _median_appearance(samples: list[PersonAppearance]) -> PersonAppearance:
    """Suppress one occluded/noisy frame without retaining its full image."""

    def unit_median(vectors: list[np.ndarray]) -> np.ndarray:
        median = np.median(np.stack(vectors), axis=0).astype(np.float32)
        norm = float(np.linalg.norm(median))
        return median / norm if norm > 0 else median

    return PersonAppearance(
        clothing=unit_median([sample.clothing for sample in samples]),
        texture=unit_median([sample.texture for sample in samples]),
        head=unit_median([sample.head for sample in samples]),
        aspect_ratio=float(np.median([sample.aspect_ratio for sample in samples])),
    )


@dataclass(frozen=True)
class MatchObservation:
    sequence: int
    received_monotonic: float
    completed_monotonic: float
    frame_width: int
    frame_height: int
    objects: tuple[PersonObject, ...]
    appearances: tuple[PersonAppearance | None, ...]


@dataclass(frozen=True)
class MatchEvaluation:
    matches: dict[str, str]
    best_similarity: float | None
    second_similarity: float | None
    best_pair: tuple[str, str] | None
    matched_similarity: float
    reason: str


def _evaluate_observations(
    camera1: MatchObservation,
    camera2: MatchObservation,
    *,
    max_capture_skew_seconds: float,
    minimum_similarity: float,
    minimum_margin: float,
    geometry_matrix: np.ndarray | None = None,
) -> MatchEvaluation:
    # The shared host clock timestamps each decoded frame before inference.
    if abs(camera1.received_monotonic - camera2.received_monotonic) > max_capture_skew_seconds:
        return MatchEvaluation({}, None, None, None, 0.0, "capture_time_gap")
    if not camera1.objects or not camera2.objects:
        return MatchEvaluation({}, None, None, None, 0.0, "no_people")

    scores = np.full((len(camera1.objects), len(camera2.objects)), -1.0, dtype=np.float32)
    for index1, descriptor1 in enumerate(camera1.appearances):
        if descriptor1 is None:
            continue
        for index2, descriptor2 in enumerate(camera2.appearances):
            if descriptor2 is not None:
                scores[index1, index2] = _appearance_similarity(descriptor1, descriptor2)

    ranking_scores = scores.copy()
    if geometry_matrix is not None:
        for index1, person1 in enumerate(camera1.objects):
            for index2, person2 in enumerate(camera2.objects):
                # Position can break an appearance tie, but must never identify
                # a person whose raw appearance is below the agreed threshold.
                if scores[index1, index2] >= minimum_similarity:
                    geometry_score = _upper_body_geometry_similarity(
                        camera1, person1, camera2, person2, geometry_matrix
                    )
                    ranking_scores[index1, index2] = min(
                        1.0, scores[index1, index2] + 0.10 * geometry_score
                    )

    valid_scores = ranking_scores[ranking_scores >= 0]
    best_similarity = float(np.max(valid_scores)) if valid_scores.size else None
    best_pair: tuple[str, str] | None = None
    second_similarity: float | None = None
    if valid_scores.size:
        best_index1, best_index2 = np.unravel_index(
            int(np.argmax(ranking_scores)), ranking_scores.shape
        )
        best_pair = (
            camera1.objects[best_index1].object_id,
            camera2.objects[best_index2].object_id,
        )
        competitors = np.concatenate((
            np.delete(ranking_scores[best_index1], best_index2),
            np.delete(ranking_scores[:, best_index2], best_index1),
        ))
        competitors = competitors[competitors >= 0]
        if competitors.size:
            second_similarity = float(np.max(competitors))
    matches: dict[str, str] = {}
    matched_similarity = 0.0
    for index1, person1 in enumerate(camera1.objects):
        row = ranking_scores[index1]
        index2 = int(np.argmax(row))
        score = float(row[index2])
        if scores[index1, index2] < minimum_similarity:
            continue
        column = ranking_scores[:, index2]
        if int(np.argmax(column)) != index1:
            continue
        row_sorted = np.sort(row)
        column_sorted = np.sort(column)
        second_row = float(row_sorted[-2]) if len(row_sorted) > 1 else -1.0
        second_column = float(column_sorted[-2]) if len(column_sorted) > 1 else -1.0
        if score - second_row < minimum_margin or score - second_column < minimum_margin:
            continue
        matches[person1.object_id] = camera2.objects[index2].object_id
        matched_similarity += score

    reason = (
        "matched" if matches
        else "low_similarity" if best_similarity is None or best_similarity < minimum_similarity
        else "ambiguous"
    )
    return MatchEvaluation(
        matches, best_similarity, second_similarity, best_pair, matched_similarity, reason
    )


def _match_observations(
    camera1: MatchObservation,
    camera2: MatchObservation,
    *,
    max_capture_skew_seconds: float,
    minimum_similarity: float,
    minimum_margin: float,
) -> dict[str, str]:
    return _evaluate_observations(
        camera1,
        camera2,
        max_capture_skew_seconds=max_capture_skew_seconds,
        minimum_similarity=minimum_similarity,
        minimum_margin=minimum_margin,
    ).matches


def _matching_observation(result: DetectionResult) -> MatchObservation:
    height, width = result.frame.shape[:2]
    return MatchObservation(
        sequence=result.sequence,
        received_monotonic=result.received_monotonic,
        completed_monotonic=result.completed_monotonic,
        frame_width=width,
        frame_height=height,
        objects=result.objects,
        appearances=tuple(
            _person_appearance_descriptor(result.frame, person.bbox)
            for person in result.objects
        ),
    )


def estimate_background_homography(
    camera1: DetectionResult,
    camera2: DetectionResult,
) -> tuple[np.ndarray, int] | None:
    """Map normalized camera-1 image points to camera-2 using static background."""

    def background_features(result: DetectionResult) -> tuple[np.ndarray, np.ndarray, int, int]:
        source_height, source_width = result.frame.shape[:2]
        scale = min(1.0, 640.0 / max(source_width, source_height))
        width = max(1, round(source_width * scale))
        height = max(1, round(source_height * scale))
        gray = cv2.cvtColor(
            cv2.resize(result.frame, (width, height), interpolation=cv2.INTER_AREA),
            cv2.COLOR_BGR2GRAY,
        )
        mask = np.full((height, width), 255, dtype=np.uint8)
        mask[: max(1, round(height * 0.08))] = 0  # Ignore camera OSD and timestamps.
        for person in result.objects:
            x1, y1, x2, y2 = person.bbox
            margin_x = (x2 - x1) * 0.10
            margin_y = (y2 - y1) * 0.10
            left = max(0, int((x1 - margin_x) * width / source_width))
            top = max(0, int((y1 - margin_y) * height / source_height))
            right = min(width, int((x2 + margin_x) * width / source_width) + 1)
            bottom = min(height, int((y2 + margin_y) * height / source_height) + 1)
            mask[top:bottom, left:right] = 0
        # This project's CUDA OpenCV build does not include features2d/ORB.
        # Harris corners plus normalized intensity patches use its available
        # imgproc and NumPy operations without replacing that runtime.
        image = gray.astype(np.float32) / 255.0
        response = cv2.cornerHarris(image, 2, 3, 0.04)
        response[mask == 0] = 0
        patch_radius = 10
        response[:patch_radius] = 0
        response[-patch_radius:] = 0
        response[:, :patch_radius] = 0
        response[:, -patch_radius:] = 0
        peak = float(np.max(response))
        if peak <= 0:
            return np.empty((0, 2), np.float32), np.empty((0, 441), np.float32), width, height
        local_max = cv2.dilate(response, np.ones((11, 11), np.uint8))
        y_values, x_values = np.where((response == local_max) & (response > peak * 0.01))
        if len(x_values) == 0:
            return np.empty((0, 2), np.float32), np.empty((0, 441), np.float32), width, height
        selected = np.argsort(response[y_values, x_values])[-500:]
        points = np.float32([
            (x_values[index] / width, y_values[index] / height)
            for index in selected
        ])
        patches = np.stack([
            image[
                y_values[index] - patch_radius : y_values[index] + patch_radius + 1,
                x_values[index] - patch_radius : x_values[index] + patch_radius + 1,
            ].reshape(-1)
            for index in selected
        ]).astype(np.float32)
        patches -= patches.mean(axis=1, keepdims=True)
        norms = np.linalg.norm(patches, axis=1, keepdims=True)
        patches /= np.maximum(norms, 1e-8)
        return points, patches, width, height

    points1, descriptors1, width1, height1 = background_features(camera1)
    points2, descriptors2, width2, height2 = background_features(camera2)
    if len(points1) < 30 or len(points2) < 30:
        return None
    similarities = descriptors1 @ descriptors2.T
    best_two = np.argpartition(similarities, -2, axis=1)[:, -2:]
    best_order = np.argsort(
        np.take_along_axis(similarities, best_two, axis=1), axis=1
    )
    sorted_two = np.take_along_axis(best_two, best_order, axis=1)
    best_indices = sorted_two[:, 1]
    best_scores = similarities[np.arange(len(points1)), best_indices]
    second_scores = similarities[np.arange(len(points1)), sorted_two[:, 0]]
    reverse_best = np.argmax(similarities, axis=0)
    good_indices = np.flatnonzero(
        (best_scores >= 0.78)
        & ((1.0 - best_scores) < 0.75 * (1.0 - second_scores))
        & (reverse_best[best_indices] == np.arange(len(points1)))
    )
    if len(good_indices) < 30:
        return None
    source_points = points1[good_indices]
    target_points = points2[best_indices[good_indices]]
    matrix, inlier_mask = cv2.findHomography(
        source_points, target_points, cv2.RANSAC, 0.008
    )
    if matrix is None or inlier_mask is None or not np.isfinite(matrix).all():
        return None
    inliers = inlier_mask.ravel().astype(bool)
    count = int(np.count_nonzero(inliers))
    if count < 20 or count / len(good_indices) < 0.50:
        return None
    spread = np.ptp(source_points[inliers], axis=0)
    if spread[0] < 0.25 or spread[1] < 0.20:
        return None
    projected = cv2.perspectiveTransform(
        source_points[inliers].reshape(-1, 1, 2), matrix
    ).reshape(-1, 2)
    errors = np.linalg.norm(projected - target_points[inliers], axis=1)
    if not np.isfinite(errors).all() or float(np.median(errors)) * max(width2, height2) > 4.0:
        return None
    corners = np.float32([[(0, 0)], [(1, 0)], [(1, 1)], [(0, 1)]])
    mapped_corners = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
    if not np.isfinite(mapped_corners).all() or np.any(mapped_corners < -0.5) or np.any(mapped_corners > 1.5):
        return None
    return matrix, count


def _upper_body_geometry_similarity(
    camera1: MatchObservation,
    person1: PersonObject,
    camera2: MatchObservation,
    person2: PersonObject,
    matrix: np.ndarray,
) -> float:
    def anchor(observation: MatchObservation, person: PersonObject) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = person.bbox
        return (
            (x1 + x2) * 0.5 / observation.frame_width,
            (y1 + (y2 - y1) * 0.25) / observation.frame_height,
            (x2 - x1) / observation.frame_width,
            (y2 - y1) / observation.frame_height,
        )

    x1, y1, width1, height1 = anchor(camera1, person1)
    x2, y2, width2, height2 = anchor(camera2, person2)
    projected = matrix @ np.array([x1, y1, 1.0])
    if not np.isfinite(projected).all() or abs(projected[2]) < 1e-8:
        return 0.0
    projected_x, projected_y = projected[:2] / projected[2]
    scale_x = max(0.08, min(0.22, (width1 + width2) * 0.75))
    scale_y = max(0.08, min(0.25, (height1 + height2) * 0.40))
    distance_squared = ((projected_x - x2) / scale_x) ** 2 + ((projected_y - y2) / scale_y) ** 2
    return float(np.exp(-0.5 * distance_squared))


def match_cross_camera_tracks(
    camera1: DetectionResult | None,
    camera2: DetectionResult | None,
    *,
    max_capture_skew_seconds: float = 0.4,
    minimum_similarity: float = 0.7,
    minimum_margin: float = 0.08,
) -> dict[str, str]:
    """Compare two decoded frames on the host clock, independent of inference delay.

    The returned mapping is camera1 local track ID -> camera2 local track ID.
    Local IDs are deliberately not compared: each camera owns an independent
    tracker and can assign the same text to different people.
    """

    if camera1 is None or camera2 is None:
        return {}
    return _match_observations(
        _matching_observation(camera1),
        _matching_observation(camera2),
        max_capture_skew_seconds=max_capture_skew_seconds,
        minimum_similarity=minimum_similarity,
        minimum_margin=minimum_margin,
    )


@dataclass
class _PairEvidence:
    first_frame_time: float
    first_sequences: tuple[int, int]
    last_evidence_completed: float
    confirmed: bool = False
    mismatched_updates: int = 0
    last_failed_sequences: tuple[int, int] | None = None


class CrossCameraAssociator:
    """Confirm synchronized matches over time using only lightweight features."""

    def __init__(
        self,
        *,
        max_capture_skew_seconds: float = 0.4,
        minimum_similarity: float = 0.7,
        minimum_margin: float = 0.08,
        confirmation_seconds: float = 0.15,
        result_ttl: float = 0.6,
    ) -> None:
        self.max_capture_skew_seconds = max_capture_skew_seconds
        self.minimum_similarity = minimum_similarity
        self.minimum_margin = minimum_margin
        self.confirmation_seconds = confirmation_seconds
        self.result_ttl = result_ttl
        self._history: dict[str, deque[MatchObservation]] = {
            key: deque(maxlen=24) for key in CAMERA_KEYS
        }
        self._pairs: dict[tuple[str, str], _PairEvidence] = {}
        self._last_reason = "waiting_for_frames"
        self._last_best_similarity: float | None = None
        self._last_second_similarity: float | None = None
        self._last_best_pair: tuple[str, str] | None = None
        self._last_frame_skew_ms: float | None = None
        self._last_comparison_count = 0
        self._geometry_matrix: np.ndarray | None = None
        self._geometry_inliers = 0
        self._last_geometry_attempt = float("-inf")
        self._decision_history: deque[dict[str, Any]] = deque(maxlen=1000)

    def reset(self) -> None:
        for history in self._history.values():
            history.clear()
        self._pairs.clear()
        self._last_reason = "waiting_for_frames"
        self._last_best_similarity = None
        self._last_second_similarity = None
        self._last_best_pair = None
        self._last_frame_skew_ms = None
        self._last_comparison_count = 0
        self._geometry_matrix = None
        self._geometry_inliers = 0
        self._last_geometry_attempt = float("-inf")
        self._decision_history.clear()

    def reset_camera(self, camera_key: str) -> None:
        self._history[camera_key].clear()
        self._pairs.clear()
        self._last_reason = "waiting_for_frames"
        self._last_best_similarity = None
        self._last_second_similarity = None
        self._last_best_pair = None
        self._last_frame_skew_ms = None
        self._last_comparison_count = 0
        self._geometry_matrix = None
        self._geometry_inliers = 0
        self._last_geometry_attempt = float("-inf")
        self._decision_history.clear()

    def _prune(self, now: float) -> None:
        for history in self._history.values():
            while history and now - history[0].completed_monotonic > self.result_ttl:
                history.popleft()
        # A confirmed pair can live as long as its two detection results do.
        for pair, evidence in list(self._pairs.items()):
            expiry = self.result_ttl if evidence.confirmed else self.max_capture_skew_seconds
            if now - evidence.last_evidence_completed > expiry:
                del self._pairs[pair]
        while self._decision_history and now - self._decision_history[0]["at"] > 30.0:
            self._decision_history.popleft()

    def _record_decision(self, now: float) -> None:
        self._decision_history.append({
            "at": now,
            "reason": self._last_reason,
            "bestSimilarity": self._last_best_similarity,
            "secondSimilarity": self._last_second_similarity,
            "camera1Id": self._last_best_pair[0] if self._last_best_pair else None,
            "camera2Id": self._last_best_pair[1] if self._last_best_pair else None,
            "frameSkewMs": self._last_frame_skew_ms,
        })

    def _smoothed_observation(
        self, camera_key: str, observation: MatchObservation
    ) -> MatchObservation:
        appearances: list[PersonAppearance | None] = []
        for person, current in zip(observation.objects, observation.appearances):
            if current is None:
                appearances.append(None)
                continue
            samples = [
                descriptor
                for past in self._history[camera_key]
                if abs(past.received_monotonic - observation.received_monotonic) <= self.result_ttl
                for past_person, descriptor in zip(past.objects, past.appearances)
                if past_person.object_id == person.object_id and descriptor is not None
            ]
            # A two-frame median is just an average and is easily pulled by a
            # single bad crop; wait for three independent frames.
            appearances.append(_median_appearance(samples[-5:]) if len(samples) >= 3 else current)
        return replace(observation, appearances=tuple(appearances))

    def observe(
        self,
        camera_key: str,
        result: DetectionResult,
        *,
        other_result: DetectionResult | None = None,
    ) -> None:
        observation = _matching_observation(result)
        self._history[camera_key].append(observation)
        now = result.completed_monotonic
        self._prune(now)
        other_key = "camera2" if camera_key == "camera1" else "camera1"
        if (
            other_result is not None
            and self._geometry_matrix is None
            and now - self._last_geometry_attempt >= 5.0
        ):
            self._last_geometry_attempt = now
            left_result, right_result = (
                (result, other_result) if camera_key == "camera1"
                else (other_result, result)
            )
            estimate = estimate_background_homography(left_result, right_result)
            if estimate is not None:
                self._geometry_matrix, self._geometry_inliers = estimate
        eligible = [
            other for other in self._history[other_key]
            if abs(other.received_monotonic - observation.received_monotonic)
            <= self.max_capture_skew_seconds
        ]
        if not eligible:
            if self._history[other_key]:
                closest = min(
                    self._history[other_key],
                    key=lambda item: abs(item.received_monotonic - observation.received_monotonic),
                )
                self._last_reason = "capture_time_gap"
                self._last_frame_skew_ms = round(
                    abs(closest.received_monotonic - observation.received_monotonic) * 1000, 1
                )
            else:
                self._last_reason = "waiting_for_frames"
                self._last_frame_skew_ms = None
            self._last_best_similarity = None
            self._last_second_similarity = None
            self._last_best_pair = None
            self._last_comparison_count = 0
            self._record_decision(now)
            return
        current_smoothed = self._smoothed_observation(camera_key, observation)
        evaluations: list[tuple[MatchObservation, MatchEvaluation]] = []
        for other in eligible:
            other_smoothed = self._smoothed_observation(other_key, other)
            left, right = (
                (current_smoothed, other_smoothed)
                if camera_key == "camera1" else (other_smoothed, current_smoothed)
            )
            evaluation = _evaluate_observations(
                left,
                right,
                max_capture_skew_seconds=self.max_capture_skew_seconds,
                minimum_similarity=self.minimum_similarity,
                minimum_margin=self.minimum_margin,
                geometry_matrix=self._geometry_matrix,
            )
            evaluations.append((other, evaluation))
        other, evaluation = max(
            evaluations,
            key=lambda candidate: (
                len(candidate[1].matches),
                -abs(candidate[0].received_monotonic - observation.received_monotonic),
                candidate[1].matched_similarity,
            ),
        )
        nearest_other, _ = min(
            evaluations,
            key=lambda candidate: abs(candidate[0].received_monotonic - observation.received_monotonic),
        )
        left, right = (
            (current_smoothed, self._smoothed_observation(other_key, other))
            if camera_key == "camera1"
            else (self._smoothed_observation(other_key, other), current_smoothed)
        )
        nearest_left, nearest_right = (
            (observation, nearest_other)
            if camera_key == "camera1"
            else (nearest_other, observation)
        )
        # Do not let an older appearance template conceal two fresh, genuinely
        # conflicting frames after a pair has already been confirmed.
        nearest_evaluation = _evaluate_observations(
            nearest_left,
            nearest_right,
            max_capture_skew_seconds=self.max_capture_skew_seconds,
            minimum_similarity=self.minimum_similarity,
            minimum_margin=self.minimum_margin,
            geometry_matrix=self._geometry_matrix,
        )
        matched_pairs = set(evaluation.matches.items())
        nearest_pairs = set(nearest_evaluation.matches.items())
        self._last_reason = "confirming" if matched_pairs else evaluation.reason
        self._last_best_similarity = evaluation.best_similarity
        self._last_second_similarity = evaluation.second_similarity
        self._last_best_pair = evaluation.best_pair
        self._last_comparison_count = len(evaluations)
        self._last_frame_skew_ms = round(
            abs(other.received_monotonic - observation.received_monotonic) * 1000, 1
        )
        invalidated: set[tuple[str, str]] = set()
        for pair, evidence in list(self._pairs.items()):
            if pair in nearest_pairs:
                evidence.mismatched_updates = 0
                evidence.last_failed_sequences = None
                continue
            if any(left_id == pair[0] or right_id == pair[1] for left_id, right_id in nearest_pairs):
                del self._pairs[pair]
                invalidated.add(pair)
                continue
            if (
                any(person.object_id == pair[0] for person in nearest_left.objects)
                and any(person.object_id == pair[1] for person in nearest_right.objects)
            ):
                sequences = (nearest_left.sequence, nearest_right.sequence)
                if evidence.confirmed or evidence.last_failed_sequences is None or all(
                    current != previous
                    for current, previous in zip(sequences, evidence.last_failed_sequences)
                ):
                    evidence.mismatched_updates += 1
                    evidence.last_failed_sequences = sequences
                if evidence.mismatched_updates >= 2:
                    del self._pairs[pair]
                    invalidated.add(pair)

        frame_time = max(left.received_monotonic, right.received_monotonic)
        for pair in matched_pairs:
            if pair in invalidated:
                continue
            evidence = self._pairs.get(pair)
            if evidence is None:
                self._pairs[pair] = _PairEvidence(
                    first_frame_time=frame_time,
                    first_sequences=(left.sequence, right.sequence),
                    last_evidence_completed=now,
                )
                continue
            if pair in nearest_pairs:
                evidence.last_evidence_completed = now
            if evidence.confirmed:
                continue
            if (
                frame_time - evidence.first_frame_time >= self.confirmation_seconds
                and left.sequence != evidence.first_sequences[0]
                and right.sequence != evidence.first_sequences[1]
            ):
                evidence.confirmed = True
                for other_pair in list(self._pairs):
                    if other_pair != pair and (
                        other_pair[0] == pair[0] or other_pair[1] == pair[1]
                    ):
                        del self._pairs[other_pair]
        if any(
            pair in self._pairs and self._pairs[pair].confirmed
            for pair in matched_pairs
        ):
            self._last_reason = "matched"
        self._record_decision(now)

    def diagnostics(self, now: float) -> dict[str, Any]:
        self._prune(now)
        confirmed = sum(evidence.confirmed for evidence in self._pairs.values())
        pending = len(self._pairs) - confirmed
        reason_counts: dict[str, int] = {}
        for item in self._decision_history:
            reason_counts[item["reason"]] = reason_counts.get(item["reason"], 0) + 1
        recent_failures = [
            {
                **{key: value for key, value in item.items() if key != "at"},
                "ageMs": round(max(0.0, now - item["at"]) * 1000),
            }
            for item in reversed(self._decision_history)
            if item["reason"] not in ("matched", "confirming")
        ][:5]
        return {
            "algorithmVersion": "temporal-geometry-v3",
            "reason": "matched" if confirmed else self._last_reason,
            "bestSimilarity": (
                round(self._last_best_similarity, 3)
                if self._last_best_similarity is not None else None
            ),
            "secondSimilarity": (
                round(self._last_second_similarity, 3)
                if self._last_second_similarity is not None else None
            ),
            "camera1Id": self._last_best_pair[0] if self._last_best_pair else None,
            "camera2Id": self._last_best_pair[1] if self._last_best_pair else None,
            "comparisonCount": self._last_comparison_count,
            "offsetMs": None,
            "frameSkewMs": self._last_frame_skew_ms,
            "geometryStatus": "ready" if self._geometry_matrix is not None else "unavailable",
            "geometryInliers": self._geometry_inliers,
            "reasonCounts": reason_counts,
            "recentFailures": recent_failures,
            "pendingPairs": pending,
            "confirmedPairs": confirmed,
        }

    def confirmed_matches(self, now: float) -> dict[str, str]:
        self._prune(now)
        return {
            left_id: right_id
            for (left_id, right_id), evidence in self._pairs.items()
            if evidence.confirmed
        }


class PersonDetector(Protocol):
    """Runtime-neutral detector contract used by the fair scheduler."""

    def load(self) -> None: ...

    def detect(
        self, image: np.ndarray
    ) -> list[tuple[tuple[float, float, float, float], float]]: ...

    def diagnostics(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


def letterbox_image(image: np.ndarray, size: int = 640) -> tuple[np.ndarray, float, float, float]:
    """Ultralytics-compatible square letterbox without changing aspect ratio."""
    height, width = image.shape[:2]
    if width < 1 or height < 1:
        raise ValueError("source frame is empty")
    scale = min(size / width, size / height)
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    pad_x = (size - resized_width) / 2.0
    pad_y = (size - resized_height) / 2.0
    left = round(pad_x - 0.1)
    top = round(pad_y - 0.1)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[top : top + resized_height, left : left + resized_width] = resized
    return canvas, scale, float(left), float(top)


def bbox_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    union = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(
        0.0, by2 - by1
    ) - intersection
    return intersection / union if union > 0 else 0.0


def non_max_suppression(
    boxes: list[tuple[float, float, float, float]],
    scores: list[float],
    iou_threshold: float,
) -> list[int]:
    order = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    kept: list[int] = []
    while order:
        selected = order.pop(0)
        kept.append(selected)
        order = [
            candidate
            for candidate in order
            if bbox_iou(boxes[selected], boxes[candidate]) <= iou_threshold
        ]
    return kept


def decode_yolo_person_output(
    output: np.ndarray,
    *,
    frame_width: int,
    frame_height: int,
    scale: float,
    pad_x: float,
    pad_y: float,
    confidence_threshold: float = 0.25,
    nms_threshold: float = 0.45,
) -> list[tuple[tuple[float, float, float, float], float]]:
    """Decode the project's raw [1, 5, 8400] single-class YOLO tensor."""
    tensor = np.asarray(output)
    if tensor.ndim == 3 and tensor.shape[0] == 1:
        tensor = tensor[0]
    if tensor.ndim != 2:
        raise ValueError(f"unexpected ONNX output rank: {tensor.shape}")
    if tensor.shape[0] == 5:
        rows = tensor.T
    elif tensor.shape[1] == 5:
        rows = tensor
    else:
        raise ValueError(f"unexpected ONNX person output shape: {tensor.shape}")
    candidates = rows[rows[:, 4] >= confidence_threshold]
    boxes: list[tuple[float, float, float, float]] = []
    scores: list[float] = []
    for cx, cy, width, height, confidence in candidates:
        x1 = max(0.0, min(float(frame_width), (float(cx) - float(width) / 2.0 - pad_x) / scale))
        y1 = max(0.0, min(float(frame_height), (float(cy) - float(height) / 2.0 - pad_y) / scale))
        x2 = max(0.0, min(float(frame_width), (float(cx) + float(width) / 2.0 - pad_x) / scale))
        y2 = max(0.0, min(float(frame_height), (float(cy) + float(height) / 2.0 - pad_y) / scale))
        if x2 - x1 < 1.0 or y2 - y1 < 1.0:
            continue
        boxes.append((x1, y1, x2, y2))
        scores.append(float(confidence))
    return [(boxes[index], scores[index]) for index in non_max_suppression(boxes, scores, nms_threshold)]


@dataclass
class _Track:
    track_id: int
    bbox: tuple[float, float, float, float]
    missed: int = 0


class IouPersonTracker:
    """Small independent per-camera tracker that stabilizes person IDs."""

    def __init__(self, iou_threshold: float = 0.25, max_missed: int = 6) -> None:
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self._next_id = 1
        self._tracks: dict[int, _Track] = {}

    def reset(self) -> None:
        self._next_id = 1
        self._tracks.clear()

    def update(
        self,
        detections: list[tuple[tuple[float, float, float, float], float]],
    ) -> tuple[PersonObject, ...]:
        for track in self._tracks.values():
            track.missed += 1
        available_tracks = set(self._tracks)
        assignments: dict[int, int] = {}
        pairs: list[tuple[float, int, int]] = []
        for detection_index, (bbox, _confidence) in enumerate(detections):
            for track_id in available_tracks:
                overlap = bbox_iou(bbox, self._tracks[track_id].bbox)
                if overlap >= self.iou_threshold:
                    pairs.append((overlap, detection_index, track_id))
        used_detections: set[int] = set()
        used_tracks: set[int] = set()
        for _overlap, detection_index, track_id in sorted(pairs, reverse=True):
            if detection_index in used_detections or track_id in used_tracks:
                continue
            assignments[detection_index] = track_id
            used_detections.add(detection_index)
            used_tracks.add(track_id)
        objects: list[PersonObject] = []
        for detection_index, (bbox, confidence) in enumerate(detections):
            track_id = assignments.get(detection_index)
            if track_id is None:
                track_id = self._next_id
                self._next_id += 1
                self._tracks[track_id] = _Track(track_id=track_id, bbox=bbox)
            track = self._tracks[track_id]
            track.bbox = bbox
            track.missed = 0
            objects.append(PersonObject(f"person-{track_id}", confidence, bbox))
        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if track.missed <= self.max_missed
        }
        return tuple(objects)


class OpenCvOnnxPersonDetector:
    def __init__(
        self,
        model_path: Path,
        confidence_threshold: float = 0.25,
        nms_threshold: float = 0.45,
    ) -> None:
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self._network: Any | None = None

    def load(self) -> None:
        if self._network is None:
            self._network = cv2.dnn.readNetFromONNX(str(self.model_path))

    def detect(self, image: np.ndarray) -> list[tuple[tuple[float, float, float, float], float]]:
        self.load()
        assert self._network is not None
        letterboxed, scale, pad_x, pad_y = letterbox_image(image, 640)
        blob = cv2.dnn.blobFromImage(
            letterboxed,
            scalefactor=1.0 / 255.0,
            size=(640, 640),
            mean=(0, 0, 0),
            swapRB=True,
            crop=False,
        )
        self._network.setInput(blob)
        output = self._network.forward()
        height, width = image.shape[:2]
        return decode_yolo_person_output(
            output,
            frame_width=width,
            frame_height=height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
            confidence_threshold=self.confidence_threshold,
            nms_threshold=self.nms_threshold,
        )


class DetectionCoordinator(threading.Thread):
    """One model/session, capacity-one inputs and fair round-robin scheduling."""

    def __init__(
        self,
        frames: dict[str, LatestFrameBuffer],
        detector: PersonDetector,
        *,
        per_camera_fps: float = 4.0,
        result_ttl: float = 0.6,
    ) -> None:
        super().__init__(name="person-detection", daemon=True)
        self.frames = frames
        self.detector = detector
        self.minimum_interval = 1.0 / per_camera_fps
        self.result_ttl = result_ttl
        self._condition = threading.Condition()
        self._running = False
        self._shutdown_requested = False
        self._generation = 0
        self._model_state = "idle"
        self._error = ""
        self._results: dict[str, DetectionResult | None] = {key: None for key in CAMERA_KEYS}
        self._last_sequences = {key: 0 for key in CAMERA_KEYS}
        self._last_started = {key: 0.0 for key in CAMERA_KEYS}
        self._completed_times: dict[str, deque[float]] = {
            key: deque() for key in CAMERA_KEYS
        }
        self._last_inference_ms: dict[str, float | None] = {
            key: None for key in CAMERA_KEYS
        }
        self._trackers = {key: IouPersonTracker() for key in CAMERA_KEYS}
        self._cross_camera = CrossCameraAssociator(result_ttl=result_ttl)
        self._next_camera = 0

    def request_start(self) -> dict[str, Any]:
        with self._condition:
            was_running = self._running
            self._generation += 1
            self._running = True
            self._error = ""
            if not was_running:
                self._results = {key: None for key in CAMERA_KEYS}
                self._cross_camera.reset()
                self._last_sequences = {key: 0 for key in CAMERA_KEYS}
                self._completed_times = {key: deque() for key in CAMERA_KEYS}
                self._last_inference_ms = {key: None for key in CAMERA_KEYS}
                for tracker in self._trackers.values():
                    tracker.reset()
            if self._model_state == "error":
                self._model_state = "idle"
            self._condition.notify_all()
            return self._state_locked()

    def request_stop(self) -> dict[str, Any]:
        with self._condition:
            self._generation += 1
            self._running = False
            self._results = {key: None for key in CAMERA_KEYS}
            self._cross_camera.reset()
            self._condition.notify_all()
            return self._state_locked()

    def state(self) -> dict[str, Any]:
        with self._condition:
            return self._state_locked()

    def _state_locked(self) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {}
        get_diagnostics = getattr(self.detector, "diagnostics", None)
        if callable(get_diagnostics):
            try:
                value = get_diagnostics()
                if isinstance(value, dict):
                    diagnostics = value
            except Exception:
                diagnostics = {"provider": "tensorrt-cuda", "engineState": "error"}
        return {
            "running": self._running,
            "modelState": self._model_state,
            "error": self._error,
            "targetFpsPerCamera": round(1.0 / self.minimum_interval, 2),
            "resultTtlMs": round(self.result_ttl * 1000),
            **diagnostics,
        }

    def camera_metrics(self, camera_key: str) -> dict[str, float | None]:
        with self._condition:
            completed = self._completed_times[camera_key]
            now = time.monotonic()
            while completed and completed[0] < now - 1.0:
                completed.popleft()
            return {
                "fps": round(float(len(completed)), 2),
                "latencyMs": (
                    round(self._last_inference_ms[camera_key], 3)
                    if self._last_inference_ms[camera_key] is not None
                    else None
                ),
            }

    def latest(self, camera_key: str) -> DetectionResult | None:
        with self._condition:
            if not self._running:
                return None
            result = self._results[camera_key]
            if result is None or time.monotonic() - result.completed_monotonic > self.result_ttl:
                return None
            return result

    def matching_snapshot(
        self,
    ) -> tuple[dict[str, DetectionResult | None], dict[str, str], dict[str, Any]]:
        """Return current detections, cross-camera IDs and diagnostics atomically."""
        with self._condition:
            now = time.monotonic()
            results = {
                key: (
                    result
                    if self._running
                    and result is not None
                    and now - result.completed_monotonic <= self.result_ttl
                    else None
                )
                for key, result in self._results.items()
            }
            confirmed = self._cross_camera.confirmed_matches(now)
            left_ids = (
                {person.object_id for person in results["camera1"].objects}
                if results["camera1"] else set()
            )
            right_ids = (
                {person.object_id for person in results["camera2"].objects}
                if results["camera2"] else set()
            )
            matches = {
                left: right for left, right in confirmed.items()
                if left in left_ids and right in right_ids
            }
            diagnostics = self._cross_camera.diagnostics(now)
            diagnostics["confirmedPairs"] = len(matches)
            if not self._running or any(result is None for result in results.values()):
                diagnostics["reason"] = "waiting_for_frames"
            elif not left_ids or not right_ids:
                diagnostics["reason"] = "no_people"
            elif matches:
                diagnostics["reason"] = (
                    "matched" if len(matches) == min(len(left_ids), len(right_ids))
                    else "partial_match"
                )
            elif diagnostics["reason"] == "matched":
                diagnostics["reason"] = "confirming"
            return results, matches, diagnostics

    def wait_latest(self, camera_key: str, timeout: float = 0.6) -> DetectionResult | None:
        """Wait briefly for a fresh result before a detection-mode snapshot."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._running:
                result = self._results[camera_key]
                if result is not None and time.monotonic() - result.completed_monotonic <= self.result_ttl:
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
        return None

    def shutdown(self, timeout: float = 10.0) -> None:
        with self._condition:
            self._shutdown_requested = True
            self._running = False
            self._condition.notify_all()
        self.join(timeout=timeout)
        close_detector = getattr(self.detector, "close", None)
        if callable(close_detector):
            close_detector()

    def _select_work(self) -> tuple[str, Any, int] | None:
        now = time.monotonic()
        for offset in range(len(CAMERA_KEYS)):
            index = (self._next_camera + offset) % len(CAMERA_KEYS)
            key = CAMERA_KEYS[index]
            packet = self.frames[key].latest()
            if packet is None or packet.sequence == self._last_sequences[key]:
                continue
            if now - self._last_started[key] < self.minimum_interval:
                continue
            self._next_camera = (index + 1) % len(CAMERA_KEYS)
            self._last_started[key] = now
            self._last_sequences[key] = packet.sequence
            return key, packet, self._generation
        return None

    def run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._running or self._shutdown_requested)
                if self._shutdown_requested:
                    return
                if self._model_state != "ready":
                    self._model_state = "loading"
            try:
                self.detector.load()
            except Exception as exc:
                with self._condition:
                    self._model_state = "error"
                    self._error = f"ONNX model could not be loaded ({type(exc).__name__})"
                    self._running = False
                continue
            with self._condition:
                self._model_state = "ready"
                if not self._running:
                    continue
                work = self._select_work()
                if work is None:
                    self._condition.wait(timeout=0.015)
                    continue
            camera_key, packet, generation = work
            # Freeze the exact full-resolution frame used for inference. This
            # also gives Screenshot a frame whose boxes have the same sequence.
            frame = packet.image.copy()
            inference_started = time.perf_counter()
            try:
                detections = self.detector.detect(frame)
            except Exception as exc:
                with self._condition:
                    self._error = f"person inference failed ({type(exc).__name__})"
                    self._model_state = "error"
                    self._running = False
                continue
            completed_at = time.monotonic()
            inference_ms = (time.perf_counter() - inference_started) * 1000.0
            with self._condition:
                if self._running and generation == self._generation:
                    previous = self._results[camera_key]
                    if (
                        previous is not None
                        and time.monotonic() - previous.completed_monotonic > self.result_ttl * 2.0
                    ):
                        self._trackers[camera_key].reset()
                        self._cross_camera.reset_camera(camera_key)
                    objects = self._trackers[camera_key].update(detections)
                    result = DetectionResult(
                        sequence=packet.sequence,
                        received_monotonic=packet.received_monotonic,
                        completed_monotonic=completed_at,
                        frame=frame,
                        objects=objects,
                    )
                    self._results[camera_key] = result
                    other_key = "camera2" if camera_key == "camera1" else "camera1"
                    self._cross_camera.observe(
                        camera_key,
                        result,
                        other_result=self._results[other_key],
                    )
                    self._last_inference_ms[camera_key] = inference_ms
                    completed = self._completed_times[camera_key]
                    completed.append(completed_at)
                    while completed and completed[0] < completed_at - 1.0:
                        completed.popleft()
                    self._condition.notify_all()


def draw_person_objects(image: np.ndarray, objects: tuple[PersonObject, ...]) -> None:
    """Draw high-contrast person boxes and labels in-place."""
    height, width = image.shape[:2]
    thickness = max(2, round(min(width, height) / 360))
    font_scale = max(0.48, min(1.1, min(width, height) / 850))
    for detected in objects:
        x1, y1, x2, y2 = detected.bbox
        left = max(0, min(width - 1, round(x1)))
        top = max(0, min(height - 1, round(y1)))
        right = max(left + 1, min(width - 1, round(x2)))
        bottom = max(top + 1, min(height - 1, round(y2)))
        color = (45, 220, 85)
        cv2.rectangle(image, (left, top), (right, bottom), color, thickness, cv2.LINE_AA)
        label = f"person {detected.confidence:.0%}"
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        label_top = max(0, top - text_height - baseline - 6)
        label_right = min(width - 1, left + text_width + 8)
        cv2.rectangle(image, (left, label_top), (label_right, top), color, -1)
        cv2.putText(
            image,
            label,
            (left + 4, max(text_height + 2, top - baseline - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (8, 20, 12),
            thickness,
            cv2.LINE_AA,
        )


@dataclass(frozen=True)
class _OverlayFrameTask:
    image: np.ndarray
    pts_seconds: float | None
    frame_rate: float


@dataclass(frozen=True)
class _OverlayStop:
    reason: str


class _OverlayShutdown:
    pass


class OverlayVideoRecorder(threading.Thread):
    """Encode decoded frames with the latest detection boxes burned in.

    The original :class:`PacketRecorder` intentionally remuxes compressed
    packets without decoding them. That is ideal for a clean archival stream,
    but a compressed packet cannot contain the UI's detection overlay. The
    bridge therefore uses this recorder, which keeps the same trusted output
    directory and explicit Record toggle while encoding an annotated video.
    NVENC is preferred on the configured GPU; libx264 is a transparent
    compatibility fallback when the FFmpeg build does not expose NVENC.
    """

    _CODECS = ("h264_nvenc", "libx264")

    def __init__(
        self,
        output: OutputConfig,
        camera_key: str,
        annotate: Callable[[np.ndarray], None],
        *,
        queue_size: int = 12,
    ) -> None:
        self.camera_key = safe_file_component(camera_key)
        super().__init__(name=f"overlay-recorder-{self.camera_key}", daemon=True)
        self.output = output
        self.annotate = annotate
        self._queue: queue.Queue[_OverlayFrameTask | _OverlayStop | _OverlayShutdown] = queue.Queue(
            # A decoded 4K BGR frame is roughly 25 MiB. Keep this bounded to
            # four frames so a stalled encoder cannot consume gigabytes while
            # the RTSP reader remains low-latency.
            # Honor a caller's small bounded queue as well (useful for
            # low-latency deployments/tests), while keeping a sane upper
            # bound so a misconfigured value cannot retain unbounded 4K
            # frames.  A single-slot queue still preserves the newest frame
            # because submit_frame evicts the stale frame on saturation.
            maxsize=max(1, min(int(queue_size), 4))
        )
        self._lock = threading.Lock()
        self._desired = False
        self._segment_active = False
        self._status = RecordingStatus()

    def status(self) -> RecordingStatus:
        with self._lock:
            return self._status

    def _set_status(self, **changes: Any) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)

    def _enqueue_control(self, item: _OverlayStop | _OverlayShutdown) -> None:
        # A stop/shutdown command must not be stranded behind stale frames.
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    continue

    def _discard_pending_frames(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def request_start(self) -> None:
        with self._lock:
            if self._desired:
                return
            self._desired = True
            self._status = RecordingStatus(state="armed")
        LOG.info("Annotated recording armed; waiting for decoded frames")

    def request_stop(self) -> None:
        with self._lock:
            was_active = self._segment_active
            was_desired = self._desired
            self._desired = False
            if not was_active and was_desired:
                self._status = RecordingStatus(state="idle")
        if was_active:
            self._set_status(state="stopping")
            self._enqueue_control(_OverlayStop("user requested stop"))
        elif was_desired:
            # No encoder has opened yet; discard queued frames so a fast
            # Start→Stop click cannot create a late unannotated segment.
            self._discard_pending_frames()
            self._enqueue_control(_OverlayStop("user requested stop"))
            LOG.info("Annotated recording request cancelled before the first frame")

    def submit_frame(
        self,
        image: np.ndarray,
        *,
        pts_seconds: float | None,
        frame_rate: float,
    ) -> None:
        with self._lock:
            if not self._desired:
                return
        if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
            return
        try:
            task = _OverlayFrameTask(
                image=np.ascontiguousarray(image.copy()),
                pts_seconds=pts_seconds,
                frame_rate=max(1.0, min(float(frame_rate), 120.0)),
            )
            # Recording must not back-pressure the live RTSP/detection path.
            # When the encoder falls behind, keep the newest frame and drop
            # the oldest queued frame instead of turning an otherwise healthy
            # recording into an error state.
            while True:
                try:
                    self._queue.put_nowait(task)
                    return
                except queue.Full:
                    try:
                        stale = self._queue.get_nowait()
                    except queue.Empty:
                        continue
                    if not isinstance(stale, _OverlayFrameTask):
                        # A stop/shutdown marker should retain its ordering
                        # semantics. Put it back and let the recorder thread
                        # consume it; do not enqueue frames after it.
                        try:
                            self._queue.put_nowait(stale)
                        except queue.Full:
                            LOG.debug("Annotated recording control marker already pending")
                        return
                    LOG.debug("Annotated recording queue full; dropped oldest frame")
        except (TypeError, ValueError):
            # Invalid frame construction is a caller/data error, not a queue
            # saturation condition. Preserve the existing recording state.
            return

    def stream_disconnected(self, reason: str) -> None:
        with self._lock:
            active = self._segment_active
        if active:
            self._enqueue_control(_OverlayStop(f"stream disconnected: {reason}"))

    def shutdown(self, timeout: float = 10.0) -> None:
        with self._lock:
            self._desired = False
        self._enqueue_control(_OverlayShutdown())
        self.join(timeout=timeout)
        if self.is_alive():
            LOG.error("Annotated recorder did not exit before the shutdown timeout")

    def _open_encoder(
        self,
        image: np.ndarray,
        frame_rate: float,
        codec_names: tuple[str, ...] | None = None,
    ) -> tuple[av.container.OutputContainer, av.video.stream.VideoStream, Path, str, Fraction]:
        suffix = "mp4" if self.output.record_container == "mp4" else "mkv"
        stem = f"{self.camera_key}_video_{timestamp_for_filename()}"
        path = self.output.record_dir / f"{stem}.{suffix}"
        suffix_index = 1
        while path.exists():
            path = self.output.record_dir / f"{stem}_{suffix_index}.{suffix}"
            suffix_index += 1
        rate = max(1, min(round(frame_rate), 120))
        codec_time_base = Fraction(1, rate)
        format_name = "mp4" if suffix == "mp4" else "matroska"
        errors: list[str] = []
        candidates = codec_names or self._CODECS
        # NVENC rejects very small synthetic/test frames on some drivers. The
        # CPU fallback is still lossless with respect to the overlay semantics,
        # and real 4K camera frames always take the GPU path.
        if image.shape[1] < 144 or image.shape[0] < 144:
            candidates = tuple(codec for codec in candidates if codec != "h264_nvenc")
        for codec_name in candidates:
            container: av.container.OutputContainer | None = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                container_options = {"movflags": "+faststart"} if format_name == "mp4" else None
                container = av.open(str(path), mode="w", format=format_name, options=container_options)
                stream = container.add_stream(codec_name, rate=rate)
                stream.width = int(image.shape[1])
                stream.height = int(image.shape[0])
                stream.pix_fmt = "yuv420p"
                if codec_name == "h264_nvenc":
                    # A small option set is portable across FFmpeg builds;
                    # some reject rc/cq when the encoder opens on frame one.
                    stream.options = {"preset": "p4", "tune": "ll"}
                else:
                    stream.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": "23"}
                stream.codec_context.time_base = codec_time_base
                # PyAV opens encoders lazily at the first encode call. Open now
                # so unavailable NVENC falls through to libx264 immediately.
                stream.codec_context.open()
                return container, stream, path, codec_name, codec_time_base
            except BaseException as exc:
                errors.append(f"{codec_name}:{type(exc).__name__}")
                if container is not None:
                    try:
                        container.close()
                    except BaseException:
                        pass
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise RuntimeError(f"no annotated video encoder is available ({', '.join(errors)})")

    @staticmethod
    def _frame_timestamp(
        pts_seconds: float | None,
        first_pts: float | None,
        frame_index: int,
        frame_rate: float,
    ) -> int:
        if pts_seconds is None or first_pts is None:
            return frame_index
        candidate = round(max(0.0, pts_seconds - first_pts) * frame_rate)
        return max(frame_index, candidate)

    def _close_segment(
        self,
        container: av.container.OutputContainer | None,
        stream: av.video.stream.VideoStream | None,
        path: Path | None,
        reason: str,
        *,
        flush: bool = True,
    ) -> None:
        if container is None or stream is None:
            return
        try:
            if flush:
                for packet in stream.encode(None):
                    container.mux(packet)
            container.close()
            size = path.stat().st_size if path is not None and path.exists() else 0
            self._set_status(
                state="armed" if self._desired else "idle",
                path=str(path.resolve()) if path is not None else self.status().path,
                bytes_written=size,
                segments_completed=self.status().segments_completed + 1,
            )
            LOG.info("Annotated recording finalized (%s): %s (%d bytes)", reason, path, size)
        except BaseException as exc:
            self._set_status(state="error", error=f"annotated recording finalize failed: {type(exc).__name__}")
            LOG.error("Annotated recording finalize failed (%s)", type(exc).__name__)

    def run(self) -> None:
        container: av.container.OutputContainer | None = None
        stream: av.video.stream.VideoStream | None = None
        path: Path | None = None
        codec_name = ""
        first_pts: float | None = None
        frame_index = 0
        packets_written = 0
        bytes_written = 0
        frame_rate = 25.0
        codec_time_base = Fraction(1, 25)
        while True:
            item = self._queue.get()
            if isinstance(item, _OverlayShutdown):
                self._close_segment(container, stream, path, "application shutdown")
                return
            if isinstance(item, _OverlayStop):
                self._close_segment(container, stream, path, item.reason)
                container = None
                stream = None
                path = None
                codec_name = ""
                first_pts = None
                frame_index = 0
                packets_written = 0
                bytes_written = 0
                with self._lock:
                    self._segment_active = False
                    if self._desired:
                        self._status = replace(self._status, state="armed")
                    elif self._status.state != "error":
                        self._status = replace(self._status, state="idle")
                continue
            if not isinstance(item, _OverlayFrameTask):
                continue
            with self._lock:
                if not self._desired and not self._segment_active:
                    continue
            try:
                if container is None or stream is None:
                    container, stream, path, codec_name, codec_time_base = self._open_encoder(
                        item.image,
                        item.frame_rate,
                    )
                    frame_rate = item.frame_rate
                    first_pts = item.pts_seconds
                    frame_index = 0
                    packets_written = 0
                    bytes_written = 0
                    with self._lock:
                        self._segment_active = True
                        self._status = RecordingStatus(state="recording", path=str(path.resolve()))
                    LOG.info("Annotated recording started (%s): %s", codec_name, path.resolve())
                self.annotate(item.image)
                video_frame = av.VideoFrame.from_ndarray(item.image, format="bgr24")
                video_frame.pts = self._frame_timestamp(
                    item.pts_seconds,
                    first_pts,
                    frame_index,
                    frame_rate,
                )
                # The muxer may rewrite stream.time_base after the first
                # packet. Keep one stable encoder time base for all frames.
                video_frame.time_base = codec_time_base
                for packet in stream.encode(video_frame):
                    container.mux(packet)
                    packets_written += 1
                    bytes_written += packet.size
                frame_index += 1
                self._set_status(packets_written=packets_written, bytes_written=bytes_written)
            except BaseException as exc:
                self._set_status(state="error", error=f"annotated recording failed: {type(exc).__name__}")
                LOG.error("Annotated recording failed (%s)", type(exc).__name__)
                self._close_segment(container, stream, path, "encode error", flush=False)
                container = None
                stream = None
                path = None
                with self._lock:
                    self._desired = False
                    self._segment_active = False


@dataclass
class BridgeCameraRuntime:
    source: CameraSource
    output: OutputConfig
    frames: LatestFrameBuffer
    status: StatusStore
    recorder: PacketRecorder
    snapshots: SnapshotWriter
    worker: StreamWorker


@dataclass(frozen=True)
class EncodedPreview:
    jpeg: bytes
    sequence: int
    width: int
    height: int
    received_at_ms: int
    age_ms: float


class OpenCvCudaPreviewProcessor:
    """Use the CUDA-enabled OpenCV build for the 4K-to-preview resize."""

    name = "opencv-cuda"

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._gpu_mat_factory = getattr(cv2, "cuda_GpuMat", None)
        if self._gpu_mat_factory is None:
            self._gpu_mat_factory = getattr(getattr(cv2, "cuda", None), "GpuMat", None)
        # Keep one upload buffer, one resize destination and one CUDA stream
        # for the life of the processor.  Creating a GpuMat for every preview
        # request forces the CUDA allocator to repeatedly search/allocate
        # device memory, which is particularly expensive with two 4K feeds.
        # Buffers are recreated only when a camera changes resolution or pixel
        # format.  The processor is owned by one PreviewEncoder and all calls
        # are serialized by that encoder's lock.
        self._gpu_source: Any | None = None
        self._gpu_preview: Any | None = None
        self._source_signature: tuple[tuple[int, ...], str] | None = None
        self._preview_signature: tuple[tuple[int, int], str, int] | None = None
        self._host_preview: np.ndarray | None = None
        self._stream: Any | None = None
        try:
            device_count = int(cv2.cuda.getCudaEnabledDeviceCount())
        except Exception:
            device_count = 0
        build_information = cv2.getBuildInformation()
        if (
            device_count < 1
            or not callable(self._gpu_mat_factory)
            or not callable(getattr(cv2.cuda, "resize", None))
            or "NVIDIA CUDA" not in build_information
        ):
            raise RuntimeError("the configured OpenCV build does not provide CUDA support")
        try:
            smoke_source = np.full((8, 8, 3), 17, dtype=np.uint8)
            smoke_gpu = self._gpu_mat_factory()
            smoke_gpu.upload(smoke_source)
            smoke_result = cv2.cuda.resize(
                smoke_gpu,
                (4, 4),
                interpolation=cv2.INTER_LINEAR,
            ).download()
            if smoke_result.shape != (4, 4, 3) or not np.all(smoke_result == 17):
                raise RuntimeError("unexpected CUDA resize result")
        except Exception as exc:
            raise RuntimeError("OpenCV CUDA resize smoke test failed") from exc

        # A non-default stream lets upload, resize and download use one
        # explicitly-owned CUDA queue.  Python bindings have differed between
        # OpenCV releases, so probe the complete in/out signature once and
        # transparently fall back to the blocking CUDA API if a build lacks
        # stream support.  This remains CUDA-only; there is deliberately no
        # CPU resize fallback.
        stream_factory = getattr(getattr(cv2, "cuda", None), "Stream", None)
        if stream_factory is None:
            stream_factory = getattr(cv2, "cuda_Stream", None)
        if callable(stream_factory):
            try:
                stream = stream_factory()
                stream_source = self._gpu_mat_factory()
                stream_destination = self._gpu_mat_factory()
                stream_host = np.empty((4, 4, 3), dtype=np.uint8)
                stream_destination.create(4, 4, cv2.CV_8UC3)
                stream_source.upload(smoke_source, stream)
                cv2.cuda.resize(
                    stream_source,
                    (4, 4),
                    stream_destination,
                    interpolation=cv2.INTER_LINEAR,
                    stream=stream,
                )
                stream_destination.download(stream, stream_host)
                stream.waitForCompletion()
                if stream_host.shape != (4, 4, 3) or not np.all(stream_host == 17):
                    raise RuntimeError("unexpected CUDA stream resize result")
                self._stream = stream
            except Exception:
                # The synchronous path below is still GPU accelerated and is
                # supported by older cv2 Python bindings.
                self._stream = None

    @property
    def stream_enabled(self) -> bool:
        """Whether preview transfers currently use a non-default CUDA stream."""
        return self._stream is not None

    def resize(self, image: np.ndarray) -> np.ndarray:
        if image.ndim not in (2, 3) or image.size == 0:
            raise ValueError("source frame must be a non-empty 2D/3D image")
        source_height, source_width = image.shape[:2]
        width_scale = self.width / source_width
        height_scale = self.height / source_height
        scale = min(1.0, width_scale, height_scale)
        target = (
            max(1, round(source_width * scale)),
            max(1, round(source_height * scale)),
        )
        channels = image.shape[2] if image.ndim == 3 else 1
        source_signature = (tuple(image.shape), image.dtype.str)
        preview_signature = (target, image.dtype.str, channels)

        if self._gpu_source is None or self._source_signature != source_signature:
            self._gpu_source = self._gpu_mat_factory()
            self._source_signature = source_signature
        if self._gpu_preview is None or self._preview_signature != preview_signature:
            self._gpu_preview = self._gpu_mat_factory()
            self._preview_signature = preview_signature
            # Python's cv.cuda.resize(src, dsize, dst, ...) does not allocate
            # an empty destination on all OpenCV builds (some return a 0x0
            # GpuMat and leave the caller's buffer untouched).  Allocate it
            # explicitly once so subsequent frames can reuse the same device
            # storage safely.
            depth_types = {
                np.dtype(np.uint8): cv2.CV_8U,
                np.dtype(np.int8): cv2.CV_8S,
                np.dtype(np.uint16): cv2.CV_16U,
                np.dtype(np.int16): cv2.CV_16S,
                np.dtype(np.int32): cv2.CV_32S,
                np.dtype(np.float32): cv2.CV_32F,
                np.dtype(np.float64): cv2.CV_64F,
            }
            try:
                depth = depth_types[image.dtype]
            except KeyError as exc:
                raise ValueError(f"unsupported preview image dtype: {image.dtype}") from exc
            cv_type = cv2.CV_MAKETYPE(depth, channels)
            self._gpu_preview.create(target[1], target[0], cv_type)
            self._host_preview = np.empty(
                (target[1], target[0], channels) if channels > 1 else (target[1], target[0]),
                dtype=image.dtype,
            )
        elif self._host_preview is None:
            # Defensive recovery for bindings that release the OutputArray
            # when a GpuMat is reconfigured internally.
            self._host_preview = np.empty(
                (target[1], target[0], channels) if channels > 1 else (target[1], target[0]),
                dtype=image.dtype,
            )

        assert self._gpu_source is not None
        assert self._gpu_preview is not None
        assert self._host_preview is not None
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        if self._stream is not None:
            self._gpu_source.upload(image, self._stream)
            cv2.cuda.resize(
                self._gpu_source,
                target,
                self._gpu_preview,
                interpolation=interpolation,
                stream=self._stream,
            )
            # GpuMat.download(stream, dst) fills the reusable host array.  A
            # wait is required before returning because the caller draws
            # overlays and JPEG-encodes it immediately after this method.
            self._gpu_preview.download(self._stream, self._host_preview)
            self._stream.waitForCompletion()
        else:
            self._gpu_source.upload(image)
            cv2.cuda.resize(
                self._gpu_source,
                target,
                self._gpu_preview,
                interpolation=interpolation,
            )
            self._gpu_preview.download(self._host_preview)
        # Ownership stays with the processor and the caller must consume the
        # result before the next resize call. PreviewEncoder does so while
        # holding its lock, avoiding a per-frame host allocation/copy.
        return self._host_preview


class PreviewEncoder:
    """Long-poll, latest-only preview encoder with CUDA resize telemetry."""

    def __init__(self, width: int, height: int, jpeg_quality: int, max_fps: float) -> None:
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.target_fps = max_fps
        self.minimum_interval = 1.0 / max_fps
        self.processor = OpenCvCudaPreviewProcessor(width, height)
        self._lock = threading.Lock()
        self._sequence = 0
        self._detection_sequence = 0
        self._encoded_at = 0.0
        self._encoded_times: deque[float] = deque()
        self._preview: EncodedPreview | None = None

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            while self._encoded_times and self._encoded_times[0] < now - 1.0:
                self._encoded_times.popleft()
            return {
                "processor": self.processor.name,
                "cudaStream": self.processor.stream_enabled,
                "targetFps": round(self.target_fps, 2),
                "fps": round(float(len(self._encoded_times)), 2),
                "sequence": self._sequence,
            }

    def encode(
        self,
        frames: LatestFrameBuffer,
        detection_provider: Callable[[], DetectionResult | None],
        *,
        after_sequence: int,
        wait_timeout: float,
    ) -> EncodedPreview | None:
        deadline = time.monotonic() + wait_timeout
        with self._lock:
            cached = self._preview
            encoded_at = self._encoded_at
        if cached is not None and cached.sequence > after_sequence:
            return cached

        remaining = max(0.0, deadline - time.monotonic())
        packet = frames.wait_for_new_snapshot(after_sequence, remaining)
        if packet is None:
            return None

        rate_delay = self.minimum_interval - (time.monotonic() - encoded_at)
        if rate_delay > 0:
            if wait_timeout <= 0 or time.monotonic() + rate_delay > deadline:
                return None
            time.sleep(rate_delay)
            newest = frames.latest()
            if newest is not None and newest.sequence > packet.sequence:
                packet = newest

        with self._lock:
            if self._preview is not None and self._preview.sequence >= packet.sequence:
                return self._preview if self._preview.sequence > after_sequence else None
            detection = detection_provider()
            detection_sequence = detection.sequence if detection is not None else 0
            preview = self.processor.resize(packet.image)
            if detection is not None and detection.objects:
                source_height, source_width = detection.frame.shape[:2]
                preview_height, preview_width = preview.shape[:2]
                x_scale = preview_width / source_width
                y_scale = preview_height / source_height
                scaled = tuple(
                    PersonObject(
                        detected.object_id,
                        detected.confidence,
                        (
                            detected.bbox[0] * x_scale,
                            detected.bbox[1] * y_scale,
                            detected.bbox[2] * x_scale,
                            detected.bbox[3] * y_scale,
                        ),
                    )
                    for detected in detection.objects
                )
                draw_person_objects(preview, scaled)
            ok, encoded = cv2.imencode(
                ".jpg",
                preview,
                [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
            )
            if not ok:
                raise BridgeApiError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "preview_encode_failed",
                    "camera preview encoding failed",
                )
            completed = time.monotonic()
            age_ms = max(0.0, completed - packet.received_monotonic) * 1000.0
            received_at_ms = round(time.time() * 1000.0 - age_ms)
            self._preview = EncodedPreview(
                jpeg=encoded.tobytes(),
                sequence=packet.sequence,
                width=preview.shape[1],
                height=preview.shape[0],
                received_at_ms=received_at_ms,
                age_ms=age_ms,
            )
            self._sequence = packet.sequence
            self._detection_sequence = detection_sequence
            self._encoded_at = completed
            self._encoded_times.append(completed)
            while self._encoded_times and self._encoded_times[0] < completed - 1.0:
                self._encoded_times.popleft()
            return self._preview


class CameraBridgeService:
    def __init__(
        self,
        sources: list[CameraSource],
        outputs: dict[str, OutputConfig],
        *,
        model_path: Path,
        preview_width: int,
        preview_height: int,
        preview_jpeg_quality: int,
        preview_max_fps: float,
        engine_cache_dir: Path | None = None,
        detection_fps: float = 12.5,
        detector: PersonDetector | None = None,
        secrets: list[str] | None = None,
    ) -> None:
        if {source.key for source in sources} != set(CAMERA_KEYS):
            raise ValueError("the bridge requires exactly camera1 and camera2")
        if any(outputs[key].record_on_start for key in CAMERA_KEYS):
            raise ValueError("record_on_start is forbidden in camera bridge mode")
        if detector is None and engine_cache_dir is None:
            raise ValueError("a trusted TensorRT engine cache directory is required")
        self._stop_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._recording_lock = threading.Lock()
        self._stopped = False
        self._secrets = tuple(filter(None, secrets or []))
        self.cameras: dict[str, BridgeCameraRuntime] = {}
        self.previews: dict[str, PreviewEncoder] = {}
        for source in sources:
            output = outputs[source.key]
            frames = LatestFrameBuffer()
            status = StatusStore()
            recorder = OverlayVideoRecorder(
                output,
                source.key,
                lambda image, camera_key=source.key: self._annotate_camera_frame(camera_key, image),
                queue_size=output.record_queue_packets,
            )
            snapshots = SnapshotWriter(output, source.key)
            worker = StreamWorker(source.stream, frames, status, recorder, self._stop_event, source.key)
            self.cameras[source.key] = BridgeCameraRuntime(
                source=source,
                output=output,
                frames=frames,
                status=status,
                recorder=recorder,
                snapshots=snapshots,
                worker=worker,
            )
            self.previews[source.key] = PreviewEncoder(
                preview_width,
                preview_height,
                preview_jpeg_quality,
                preview_max_fps,
            )
        if detector is None:
            assert engine_cache_dir is not None
            detector = TensorRTPersonDetector(model_path, engine_cache_dir)
        self.detection = DetectionCoordinator(
            {key: runtime.frames for key, runtime in self.cameras.items()},
            detector,
            per_camera_fps=detection_fps,
            result_ttl=0.6,
        )

    def _annotate_camera_frame(self, camera_key: str, image: np.ndarray) -> None:
        """Burn the freshest valid detection boxes into a recording frame."""
        detection = self.detection.latest(camera_key)
        if detection is None or not detection.objects:
            return
        source_height, source_width = detection.frame.shape[:2]
        frame_height, frame_width = image.shape[:2]
        if source_width == frame_width and source_height == frame_height:
            draw_person_objects(image, detection.objects)
            return
        x_scale = frame_width / max(1, source_width)
        y_scale = frame_height / max(1, source_height)
        scaled = tuple(
            PersonObject(
                detected.object_id,
                detected.confidence,
                (
                    detected.bbox[0] * x_scale,
                    detected.bbox[1] * y_scale,
                    detected.bbox[2] * x_scale,
                    detected.bbox[3] * y_scale,
                ),
            )
            for detected in detection.objects
        )
        draw_person_objects(image, scaled)

    def start(self) -> None:
        # Directory creation is intentionally deferred until an explicit
        # snapshot or recording request actually writes a file.
        self.detection.start()
        for runtime in self.cameras.values():
            runtime.recorder.start()
            runtime.snapshots.start()
            runtime.worker.start()

    def _safe_error(self, value: str) -> str:
        result = value
        for secret in self._secrets:
            result = result.replace(secret, "***").replace(quote(secret, safe=""), "***")
        return result

    def status_payload(self) -> dict[str, Any]:
        cameras: dict[str, Any] = {}
        detection_state = self.detection.state()
        detections, cross_camera_matches, matching_diagnostics = self.detection.matching_snapshot()
        reverse_matches = {
            camera2_id: camera1_id
            for camera1_id, camera2_id in cross_camera_matches.items()
        }

        def global_identity(camera_key: str, object_id: str) -> str:
            if camera_key == "camera1" and object_id in cross_camera_matches:
                return f"stereo:camera1/{object_id}|camera2/{cross_camera_matches[object_id]}"
            if camera_key == "camera2" and object_id in reverse_matches:
                return f"stereo:camera1/{reverse_matches[object_id]}|camera2/{object_id}"
            return f"{camera_key}:{object_id}"

        for key, runtime in self.cameras.items():
            status = runtime.status.snapshot()
            recording = runtime.recorder.status()
            packet = runtime.frames.latest()
            detection = detections[key]
            if detection is not None:
                detected_height, detected_width = detection.frame.shape[:2]
                objects = [
                    {
                        "id": detected.object_id,
                        "globalId": global_identity(key, detected.object_id),
                        "class": "person",
                        "confidence": round(detected.confidence, 6),
                        "bbox": {
                            "x": round(detected.bbox[0] / detected_width, 7),
                            "y": round(detected.bbox[1] / detected_height, 7),
                            "width": round((detected.bbox[2] - detected.bbox[0]) / detected_width, 7),
                            "height": round((detected.bbox[3] - detected.bbox[1]) / detected_height, 7),
                        },
                        "frameSequence": detection.sequence,
                    }
                    for detected in detection.objects
                ]
            else:
                objects = []
            detection_metrics = self.detection.camera_metrics(key)
            preview_metrics = self.previews[key].metrics()
            cameras[key] = {
                "label": runtime.source.label,
                "state": status.state,
                "error": self._safe_error(status.error),
                "codec": status.codec,
                "width": status.width,
                "height": status.height,
                "nominalFps": round(status.nominal_fps, 3),
                "decodeFps": round(status.decode_fps, 3),
                "framesDecoded": status.frames_decoded,
                "reconnects": status.reconnects,
                "frame": {
                    "available": packet is not None,
                    "sequence": packet.sequence if packet is not None else 0,
                    "ageMs": round(max(0.0, time.monotonic() - packet.received_monotonic) * 1000)
                    if packet is not None
                    else None,
                },
                "objects": objects,
                "objectCount": len(objects),
                "preview": preview_metrics,
                "previewFps": preview_metrics["fps"],
                "detection": {
                    "count": len(objects),
                    "fps": detection_metrics["fps"],
                    "latencyMs": detection_metrics["latencyMs"],
                    "ageMs": (
                        round(max(0.0, time.monotonic() - detection.completed_monotonic) * 1000)
                        if detection is not None
                        else None
                    ),
                },
                "detectionFps": detection_metrics["fps"],
                "inferenceLatencyMs": detection_metrics["latencyMs"],
                "recording": {
                    "state": recording.state,
                    "path": recording.path,
                    "error": self._safe_error(recording.error),
                    "packetsWritten": recording.packets_written,
                    "bytesWritten": recording.bytes_written,
                },
            }
        preview_processor = next(iter(self.previews.values())).processor.name
        return {
            "version": 1,
            "detection": detection_state,
            "preview": {"processor": preview_processor},
            "matchingDiagnostics": matching_diagnostics,
            "cameras": cameras,
        }

    def preview_jpeg(
        self,
        camera_key: str,
        *,
        after_sequence: int = 0,
        wait_timeout: float = 0.0,
    ) -> EncodedPreview | None:
        self._camera(camera_key)
        return self.previews[camera_key].encode(
            self.cameras[camera_key].frames,
            lambda: self.detection.latest(camera_key),
            after_sequence=after_sequence,
            wait_timeout=wait_timeout,
        )

    def start_detection(self) -> dict[str, Any]:
        return {"detection": self.detection.request_start()}

    def stop_detection(self) -> dict[str, Any]:
        return {"detection": self.detection.request_stop()}

    def toggle_recording(self) -> dict[str, Any]:
        with self._recording_lock:
            currently_active = any(
                runtime.recorder.status().state in ACTIVE_RECORDING_STATES
                for runtime in self.cameras.values()
            )
            for runtime in self.cameras.values():
                if currently_active:
                    runtime.recorder.request_stop()
                else:
                    runtime.recorder.request_start()
        return {
            "recording": not currently_active,
            "cameras": {
                key: {"state": runtime.recorder.status().state}
                for key, runtime in self.cameras.items()
            },
        }

    def queue_rendered_snapshot(self, camera_key: str, png: bytes) -> dict[str, Any]:
        runtime = self._camera(camera_key)
        width, height = validate_png(png)
        path = runtime.snapshots.submit_png(png)
        if path is None:
            raise BridgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "snapshot_queue_full",
                "snapshot queue is busy; no file was written",
            )
        return {
            "camera": camera_key,
            "queued": True,
            "path": str(path.resolve()),
            "width": width,
            "height": height,
        }

    def capture_snapshots(self) -> dict[str, Any]:
        """Capture both cameras from detection-matched full-resolution frames."""
        queued: dict[str, Any] = {}
        state_reader = getattr(self.detection, "state", None)
        detection_state = state_reader() if callable(state_reader) else {"running": False}
        detection_running = bool(detection_state.get("running"))
        for camera_key, runtime in self.cameras.items():
            detection = self.detection.latest(camera_key)
            wait_reader = getattr(self.detection, "wait_latest", None)
            if detection is None and detection_running and callable(wait_reader):
                detection = wait_reader(camera_key, timeout=0.6)
            if detection is not None:
                image = detection.frame.copy()
                draw_person_objects(image, detection.objects)
                sequence = detection.sequence
                detected = True
            else:
                if detection_running:
                    queued[camera_key] = {
                        "queued": False,
                        "error": "detection_not_ready",
                    }
                    continue
                packet = runtime.frames.latest()
                if packet is None:
                    queued[camera_key] = {
                        "queued": False,
                        "error": "no decoded frame is available",
                    }
                    continue
                image = packet.image.copy()
                sequence = packet.sequence
                detected = False
            path = runtime.snapshots.submit(image)
            if path is None:
                queued[camera_key] = {"queued": False, "error": "snapshot queue is busy"}
            else:
                queued[camera_key] = {
                    "queued": True,
                    "path": str(path.resolve()),
                    "frameSequence": sequence,
                    "detectionOverlay": detected,
                }
        if not any(item["queued"] for item in queued.values()):
            if detection_running:
                raise BridgeApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "detection_not_ready",
                    "a fresh person detection frame is not ready; try the screenshot again",
                )
            raise BridgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "frames_unavailable",
                "neither camera has a frame available for capture",
            )
        return {"snapshots": queued}

    def _camera(self, camera_key: str) -> BridgeCameraRuntime:
        try:
            return self.cameras[camera_key]
        except KeyError as exc:
            raise BridgeApiError(HTTPStatus.NOT_FOUND, "camera_not_found", "unknown camera") from exc

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._stopped:
                return
            self._stopped = True
            self._stop_event.set()
        self.detection.shutdown()
        for runtime in self.cameras.values():
            runtime.worker.join(timeout=runtime.source.stream.read_timeout + 2.0)
        for runtime in self.cameras.values():
            runtime.recorder.shutdown()
            runtime.snapshots.shutdown()


def make_handler(
    service: Any,
    token: str,
    *,
    max_snapshot_bytes: int,
    shutdown_callback: Callable[[], None],
) -> type[BaseHTTPRequestHandler]:
    snapshot_pattern = re.compile(r"^/v1/cameras/(camera1|camera2)/snapshot\.png$")
    preview_pattern = re.compile(r"^/v1/cameras/(camera1|camera2)/(?:frame|preview)\.jpg$")

    class BridgeRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, error: BridgeApiError) -> None:
            self._json(error.status, {"error": {"code": error.code, "message": error.message}})

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if hmac.compare_digest(supplied, expected):
                return True
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"error": {"code": "unauthorized", "message": "valid bearer token required"}},
            )
            return False

        def _path(self) -> str:
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment:
                raise BridgeApiError(HTTPStatus.BAD_REQUEST, "invalid_url", "query strings are not accepted")
            return parsed.path

        def _content_length(self, *, required: bool) -> int:
            if self.headers.get("Transfer-Encoding"):
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "chunked_body_forbidden",
                    "Transfer-Encoding is not accepted",
                )
            raw = self.headers.get("Content-Length")
            if raw is None:
                if required:
                    raise BridgeApiError(
                        HTTPStatus.LENGTH_REQUIRED,
                        "content_length_required",
                        "Content-Length is required",
                    )
                return 0
            try:
                length = int(raw, 10)
            except ValueError as exc:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_content_length",
                    "Content-Length must be a non-negative integer",
                ) from exc
            if length < 0:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_content_length",
                    "Content-Length must be a non-negative integer",
                )
            return length

        def _empty_body(self) -> None:
            if self._content_length(required=False) != 0:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "body_not_allowed",
                    "this endpoint does not accept a request body",
                )

        def _bounded_integer_header(
            self,
            name: str,
            *,
            default: int,
            minimum: int,
            maximum: int,
        ) -> int:
            raw = self.headers.get(name)
            if raw is None:
                return default
            if not raw.isascii() or not raw.isdecimal():
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_preview_header",
                    f"{name} must be a decimal integer",
                )
            value = int(raw, 10)
            if value < minimum or value > maximum:
                raise BridgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_preview_header",
                    f"{name} is outside the accepted range",
                )
            return value

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return
            try:
                path = self._path()
                if path == "/v1/status":
                    self._json(HTTPStatus.OK, service.status_payload())
                    return
                match = preview_pattern.fullmatch(path)
                if match:
                    after_sequence = self._bounded_integer_header(
                        "X-After-Frame-Sequence",
                        default=0,
                        minimum=0,
                        maximum=9_007_199_254_740_991,
                    )
                    wait_milliseconds = self._bounded_integer_header(
                        "X-Wait-Milliseconds",
                        default=0,
                        minimum=0,
                        maximum=1_000,
                    )
                    preview = service.preview_jpeg(
                        match.group(1),
                        after_sequence=after_sequence,
                        wait_timeout=wait_milliseconds / 1000.0,
                    )
                    if preview is None:
                        self.send_response(HTTPStatus.NO_CONTENT.value)
                        self.send_header("Content-Length", "0")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        return
                    self.send_response(HTTPStatus.OK.value)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(preview.jpeg)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Frame-Sequence", str(preview.sequence))
                    self.send_header("X-Frame-Width", str(preview.width))
                    self.send_header("X-Frame-Height", str(preview.height))
                    self.send_header("X-Frame-Received-At", str(preview.received_at_ms))
                    self.send_header("X-Frame-Age-Milliseconds", f"{preview.age_ms:.3f}")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(preview.jpeg)
                    return
                raise BridgeApiError(HTTPStatus.NOT_FOUND, "not_found", "endpoint not found")
            except BridgeApiError as exc:
                self._error(exc)
            except Exception as exc:  # keep implementation details out of the API
                LOG.error("GET request failed (%s)", type(exc).__name__)
                self._error(
                    BridgeApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "request failed")
                )

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return
            try:
                path = self._path()
                if path == "/v1/recording/toggle":
                    self._empty_body()
                    self._json(HTTPStatus.OK, service.toggle_recording())
                    return
                if path == "/v1/detection/start":
                    self._empty_body()
                    self._json(HTTPStatus.OK, service.start_detection())
                    return
                if path == "/v1/detection/stop":
                    self._empty_body()
                    self._json(HTTPStatus.OK, service.stop_detection())
                    return
                if path == "/v1/snapshots":
                    self._empty_body()
                    self._json(HTTPStatus.ACCEPTED, service.capture_snapshots())
                    return
                if path == "/v1/shutdown":
                    self._empty_body()
                    self._json(HTTPStatus.ACCEPTED, {"shuttingDown": True})
                    threading.Thread(target=shutdown_callback, name="bridge-http-shutdown", daemon=True).start()
                    return
                match = snapshot_pattern.fullmatch(path)
                if match:
                    media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if media_type != "image/png":
                        raise BridgeApiError(
                            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                            "png_required",
                            "Content-Type must be image/png",
                        )
                    length = self._content_length(required=True)
                    if length < len(PNG_SIGNATURE):
                        raise BridgeApiError(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_png",
                            "request body is not a valid PNG",
                        )
                    if length > max_snapshot_bytes:
                        raise BridgeApiError(
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            "snapshot_too_large",
                            "PNG exceeds the configured request limit",
                        )
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise BridgeApiError(
                            HTTPStatus.BAD_REQUEST,
                            "truncated_body",
                            "request body ended before Content-Length bytes were received",
                        )
                    try:
                        payload = service.queue_rendered_snapshot(match.group(1), body)
                    except ValueError as exc:
                        raise BridgeApiError(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_png",
                            str(exc),
                        ) from exc
                    self._json(HTTPStatus.ACCEPTED, payload)
                    return
                raise BridgeApiError(HTTPStatus.NOT_FOUND, "not_found", "endpoint not found")
            except BridgeApiError as exc:
                self._error(exc)
            except Exception as exc:
                LOG.error("POST request failed (%s)", type(exc).__name__)
                self._error(
                    BridgeApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "request failed")
                )

    return BridgeRequestHandler


def parse_bridge_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loopback-only dual-camera bridge")
    parser.add_argument("--preview-width", type=int, default=960)
    parser.add_argument("--preview-height", type=int, default=540)
    parser.add_argument("--preview-jpeg-quality", type=int, default=82)
    parser.add_argument("--preview-max-fps", type=float, default=30.0)
    parser.add_argument("--detection-fps", type=float, default=12.5)
    parser.add_argument("--max-snapshot-mib", type=int, default=32)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    if args.preview_width < 1 or args.preview_height < 1:
        parser.error("preview dimensions must be positive")
    if not 1 <= args.preview_jpeg_quality <= 100:
        parser.error("preview JPEG quality must be between 1 and 100")
    if not 0.5 <= args.preview_max_fps <= 30.0:
        parser.error("preview max FPS must be between 0.5 and 30")
    if not 0.5 <= args.detection_fps <= 30.0:
        parser.error("detection FPS must be between 0.5 and 30")
    if not 1 <= args.max_snapshot_mib <= 128:
        parser.error("maximum snapshot size must be between 1 and 128 MiB")
    return args


def valid_token(value: str | None) -> bool:
    return bool(value and 32 <= len(value) <= 512 and not any(character.isspace() for character in value))


def main(argv: list[str] | None = None) -> int:
    bridge_args = parse_bridge_args(argv)
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.getenv("CAMERA_BRIDGE_TOKEN")
    if not valid_token(token):
        print("Camera bridge configuration error: CAMERA_BRIDGE_TOKEN is missing or invalid", file=sys.stderr)
        return 2
    assert token is not None
    shared_password = os.getenv("CAMERA_PASSWORD")
    camera1_password = os.getenv("CAMERA1_PASSWORD") or shared_password
    camera2_password = os.getenv("CAMERA2_PASSWORD") or shared_password
    if not camera1_password or not camera2_password:
        print("Camera bridge configuration error: camera credentials are incomplete", file=sys.stderr)
        return 2
    model_value = os.getenv("CAMERA_PERSON_MODEL")
    if not model_value:
        print("Camera bridge configuration error: CAMERA_PERSON_MODEL is missing", file=sys.stderr)
        return 2
    model_path = Path(model_value)
    if not model_path.is_absolute() or model_path.suffix.lower() != ".onnx" or not model_path.is_file():
        print("Camera bridge configuration error: CAMERA_PERSON_MODEL is not a readable ONNX file", file=sys.stderr)
        return 2
    model_path = model_path.resolve()
    engine_cache_value = os.getenv("CAMERA_TENSORRT_CACHE")
    if not engine_cache_value:
        print("Camera bridge configuration error: CAMERA_TENSORRT_CACHE is missing", file=sys.stderr)
        return 2
    engine_cache_dir = Path(engine_cache_value)
    if not engine_cache_dir.is_absolute() or ".." in engine_cache_dir.parts:
        print("Camera bridge configuration error: CAMERA_TENSORRT_CACHE is invalid", file=sys.stderr)
        return 2
    engine_cache_dir = engine_cache_dir.resolve(strict=False)

    logging.basicConfig(
        level=logging.DEBUG if bridge_args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s: %(message)s",
        stream=sys.stderr,
    )
    redaction = SecretRedactionFilter([token, camera1_password, camera2_password])
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction)
    av.logging.set_level(av.logging.DEBUG if bridge_args.debug else av.logging.ERROR)

    try:
        camera_args = parse_camera_args([])
        # Bridge mode never performs implicit disk writes at startup, even if a
        # user's normal preview CLI is configured to record immediately.
        camera_args.record_on_start = False
        validate_camera_args(camera_args)
        identity_redaction = SecretRedactionFilter(
            [
                camera_args.camera1_username,
                camera_args.camera2_username,
                camera_args.camera1_ip,
                camera_args.camera2_ip,
            ]
        )
        for log_handler in logging.getLogger().handlers:
            log_handler.addFilter(identity_redaction)
        sources, outputs = build_sources_and_outputs(
            camera_args,
            camera1_password,
            camera2_password,
        )
        service = CameraBridgeService(
            sources,
            outputs,
            model_path=model_path,
            preview_width=bridge_args.preview_width,
            preview_height=bridge_args.preview_height,
            preview_jpeg_quality=bridge_args.preview_jpeg_quality,
            preview_max_fps=bridge_args.preview_max_fps,
            engine_cache_dir=engine_cache_dir,
            detection_fps=bridge_args.detection_fps,
            secrets=[
                camera1_password,
                camera2_password,
                camera_args.camera1_username,
                camera_args.camera2_username,
                camera_args.camera1_ip,
                camera_args.camera2_ip,
            ],
        )
        server_ref: list[ThreadingHTTPServer] = []

        def stop_server() -> None:
            if server_ref:
                server_ref[0].shutdown()

        handler = make_handler(
            service,
            token,
            max_snapshot_bytes=bridge_args.max_snapshot_mib * 1024 * 1024,
            shutdown_callback=stop_server,
        )
        server = ThreadingHTTPServer((LOOPBACK_HOST, 0), handler)
        server.daemon_threads = True
        server_ref.append(server)
        service.start()
    except (OSError, RuntimeError, ValueError) as exc:
        LOG.error("Camera bridge startup failed (%s)", type(exc).__name__)
        return 2

    ready = {
        "type": "camera-bridge-ready",
        "version": 1,
        "host": LOOPBACK_HOST,
        "port": server.server_address[1],
        "pid": os.getpid(),
        "cameras": list(CAMERA_KEYS),
    }
    print(json.dumps(ready, separators=(",", ":")), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
