from __future__ import annotations

from http import HTTPStatus
from http.server import ThreadingHTTPServer
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import av
import cv2
import numpy as np

from camera_bridge import (
    CameraBridgeService,
    CrossCameraAssociator,
    DetectionCoordinator,
    DetectionResult,
    EncodedPreview,
    IouPersonTracker,
    MatchObservation,
    OverlayVideoRecorder,
    PersonAppearance,
    PersonObject,
    _appearance_similarity,
    _evaluate_observations,
    _matching_observation,
    _upper_body_geometry_similarity,
    bbox_iou,
    decode_yolo_person_output,
    draw_person_objects,
    estimate_background_homography,
    letterbox_image,
    match_cross_camera_tracks,
    make_handler,
    non_max_suppression,
    validate_png,
)
from camera_low_latency import (
    CameraSource,
    LatestFrameBuffer,
    OutputConfig,
    PacketRecorder,
    SnapshotWriter,
    StreamConfig,
)


TOKEN = "unit-test-token-with-at-least-32-characters"


def png_bytes(width: int = 16, height: int = 12) -> bytes:
    ok, encoded = cv2.imencode(".png", np.zeros((height, width, 3), dtype=np.uint8))
    assert ok
    return encoded.tobytes()


class AlgorithmTests(unittest.TestCase):
    def test_letterbox_preserves_aspect_ratio(self) -> None:
        source = np.zeros((360, 640, 3), dtype=np.uint8)
        image, scale, pad_x, pad_y = letterbox_image(source)
        self.assertEqual(image.shape, (640, 640, 3))
        self.assertAlmostEqual(scale, 1.0)
        self.assertEqual(pad_x, 0.0)
        self.assertEqual(pad_y, 140.0)

    def test_yolo_decode_confidence_and_nms(self) -> None:
        output = np.zeros((1, 5, 8400), dtype=np.float32)
        output[0, :, 0] = [200, 200, 100, 100, 0.90]
        output[0, :, 1] = [205, 205, 100, 100, 0.80]
        output[0, :, 2] = [500, 400, 60, 80, 0.70]
        output[0, :, 3] = [100, 100, 40, 40, 0.10]
        decoded = decode_yolo_person_output(
            output,
            frame_width=640,
            frame_height=640,
            scale=1.0,
            pad_x=0.0,
            pad_y=0.0,
        )
        self.assertEqual(len(decoded), 2)
        self.assertAlmostEqual(decoded[0][1], 0.90, places=5)
        self.assertEqual(decoded[0][0], (150.0, 150.0, 250.0, 250.0))

    def test_iou_tracker_keeps_stable_ids(self) -> None:
        tracker = IouPersonTracker(iou_threshold=0.2)
        first = tracker.update([((10, 10, 50, 70), 0.8), ((200, 10, 250, 80), 0.7)])
        second = tracker.update([((13, 11, 53, 71), 0.9), ((198, 12, 248, 82), 0.8)])
        self.assertEqual([item.object_id for item in first], ["person-1", "person-2"])
        self.assertEqual([item.object_id for item in second], ["person-1", "person-2"])
        self.assertGreater(bbox_iou(first[0].bbox, second[0].bbox), 0.7)
        self.assertEqual(non_max_suppression([first[0].bbox, second[0].bbox], [0.8, 0.9], 0.5), [1])

    def test_cross_camera_match_uses_appearance_not_local_tracker_ids(self) -> None:
        def frame_with_person(width: int, colors: tuple[tuple[int, int, int], tuple[int, int, int]]) -> np.ndarray:
            frame = np.zeros((120, width, 3), dtype=np.uint8)
            x1, x2 = 10, width - 10
            frame[5:70, x1:x2] = colors[0]
            frame[55:115, x1:x2] = colors[1]
            return frame

        person_box = (10.0, 5.0, 70.0, 115.0)
        camera1 = DetectionResult(
            1,
            1.0,
            10.0,
            frame_with_person(80, ((20, 40, 220), (30, 180, 30))),
            (PersonObject("person-1", 0.9, person_box),),
        )
        camera2 = DetectionResult(
            1,
            1.0,
            10.1,
            frame_with_person(80, ((20, 40, 220), (30, 180, 30))),
            (PersonObject("person-1", 0.88, person_box),),
        )
        self.assertEqual(match_cross_camera_tracks(camera1, camera2), {"person-1": "person-1"})

        # Identical local IDs alone must never fuse visually different people.
        different_person = DetectionResult(
            1,
            1.0,
            10.1,
            frame_with_person(80, ((220, 30, 30), (30, 30, 220))),
            (PersonObject("person-1", 0.88, person_box),),
        )
        self.assertEqual(match_cross_camera_tracks(camera1, different_person), {})

    def test_cross_camera_match_rejects_ambiguous_or_stale_candidates(self) -> None:
        frame = np.zeros((120, 200, 3), dtype=np.uint8)
        frame[5:115, 5:95] = (20, 40, 220)
        frame[5:115, 105:195] = (20, 40, 220)
        camera1 = DetectionResult(
            1,
            1.0,
            10.0,
            frame[:, :100],
            (PersonObject("person-1", 0.9, (5.0, 5.0, 95.0, 115.0)),),
        )
        camera2 = DetectionResult(
            1,
            1.0,
            10.1,
            frame,
            (
                PersonObject("person-1", 0.9, (5.0, 5.0, 95.0, 115.0)),
                PersonObject("person-2", 0.9, (105.0, 5.0, 195.0, 115.0)),
            ),
        )
        self.assertEqual(match_cross_camera_tracks(camera1, camera2), {})

        stale_camera2 = replace(camera2, received_monotonic=1.5)
        self.assertEqual(match_cross_camera_tracks(camera1, stale_camera2), {})

    def test_cross_camera_match_uses_frame_arrival_not_inference_completion(self) -> None:
        frame = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        camera1 = DetectionResult(1, 10.0, 10.1, frame, person)
        camera2 = DetectionResult(1, 10.39, 11.0, frame, person)

        self.assertEqual(match_cross_camera_tracks(camera1, camera2), {"person-1": "person-1"})
        self.assertEqual(
            match_cross_camera_tracks(camera1, replace(camera2, received_monotonic=10.41)),
            {},
        )

    def test_texture_separates_people_with_the_same_clothing_hue(self) -> None:
        boxes = ((10.0, 5.0, 80.0, 115.0), (110.0, 5.0, 180.0, 115.0))
        plain = np.full((110, 70, 3), (20, 40, 220), dtype=np.uint8)
        striped = plain.copy()
        striped[::8, :] = (10, 20, 110)  # Same hue/saturation; different texture.

        def scene(first: np.ndarray, second: np.ndarray) -> np.ndarray:
            image = np.zeros((120, 200, 3), dtype=np.uint8)
            image[5:115, 10:80] = first
            image[5:115, 110:180] = second
            return image

        people = (
            PersonObject("person-1", 0.9, boxes[0]),
            PersonObject("person-2", 0.9, boxes[1]),
        )
        left = DetectionResult(1, 10.0, 10.01, scene(plain, striped), people)
        right = DetectionResult(1, 10.02, 10.03, scene(striped, plain), people)
        self.assertEqual(
            match_cross_camera_tracks(left, right),
            {"person-1": "person-2", "person-2": "person-1"},
        )

    def test_background_homography_recovers_fixed_scene_behind_people(self) -> None:
        width, height = 640, 480
        random = np.random.default_rng(1027)
        background = random.integers(0, 256, (height, width, 3), dtype=np.uint8)
        background = cv2.GaussianBlur(background, (3, 3), 0)
        pixel_homography = np.array(
            [[1.015, 0.025, 18.0], [-0.015, 1.0, 14.0], [0.00004, -0.00002, 1.0]],
            dtype=np.float64,
        )
        first_frame = background.copy()
        second_frame = cv2.warpPerspective(background, pixel_homography, (width, height))
        first_box = (190.0, 110.0, 290.0, 435.0)
        first_corners = np.float32([
            [[first_box[0], first_box[1]]], [[first_box[2], first_box[1]]],
            [[first_box[2], first_box[3]]], [[first_box[0], first_box[3]]],
        ])
        mapped = cv2.perspectiveTransform(first_corners, pixel_homography).reshape(-1, 2)
        second_box = (
            float(mapped[:, 0].min()), float(mapped[:, 1].min()),
            float(mapped[:, 0].max()), float(mapped[:, 1].max()),
        )
        cv2.rectangle(first_frame, (190, 110), (290, 435), (20, 50, 220), -1)
        cv2.rectangle(
            second_frame,
            (int(second_box[0]), int(second_box[1])),
            (int(second_box[2]), int(second_box[3])),
            (190, 40, 30),
            -1,
        )
        first = DetectionResult(
            1, 10.0, 10.01, first_frame,
            (PersonObject("person-1", 0.9, first_box),),
        )
        second = DetectionResult(
            1, 10.02, 10.03, second_frame,
            (PersonObject("person-7", 0.9, second_box),),
        )

        estimate = estimate_background_homography(first, second)
        self.assertIsNotNone(estimate)
        assert estimate is not None
        normalized_homography, inliers = estimate
        self.assertGreaterEqual(inliers, 30)
        for x, y in ((0.12, 0.18), (0.72, 0.30), (0.82, 0.83)):
            source = np.float32([[[x, y]]])
            projected = cv2.perspectiveTransform(source, normalized_homography)[0, 0]
            expected_pixels = cv2.perspectiveTransform(
                np.float32([[[x * width, y * height]]]), pixel_homography
            )[0, 0]
            expected = expected_pixels / np.float32([width, height])
            np.testing.assert_allclose(projected, expected, atol=0.012, rtol=0)

    def test_background_homography_rejects_unrelated_or_untextured_views(self) -> None:
        def result(sequence: int, frame: np.ndarray) -> DetectionResult:
            return DetectionResult(sequence, 10.0, 10.01, frame, ())

        random = np.random.default_rng(2205)
        unrelated = random.integers(0, 256, (480, 640, 3), dtype=np.uint8)
        other = random.integers(0, 256, (480, 640, 3), dtype=np.uint8)
        self.assertIsNone(estimate_background_homography(result(1, unrelated), result(1, other)))
        plain = np.full((480, 640, 3), 127, dtype=np.uint8)
        self.assertIsNone(estimate_background_homography(result(1, plain), result(1, plain)))

    def test_upper_body_geometry_tolerates_hidden_lower_body(self) -> None:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        first = PersonObject("person-1", 0.9, (190.0, 90.0, 290.0, 430.0))
        same_person_clipped = PersonObject("person-8", 0.9, (190.0, 90.0, 290.0, 250.0))
        distant_person = PersonObject("person-9", 0.9, (390.0, 90.0, 490.0, 250.0))
        left = _matching_observation(DetectionResult(1, 10.0, 10.01, frame, (first,)))
        right = _matching_observation(
            DetectionResult(1, 10.01, 10.02, frame, (same_person_clipped, distant_person))
        )
        matrix = np.eye(3, dtype=np.float64)
        near_score = _upper_body_geometry_similarity(left, first, right, same_person_clipped, matrix)
        far_score = _upper_body_geometry_similarity(left, first, right, distant_person, matrix)
        self.assertGreater(near_score, 0.85)
        self.assertGreater(near_score - far_score, 0.4)

    def test_three_frame_appearance_median_ignores_one_occluded_crop(self) -> None:
        normal = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        occluded = np.full((120, 80, 3), (220, 40, 20), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        associator = CrossCameraAssociator()
        results = [
            DetectionResult(1, 10.00, 10.01, normal, person),
            DetectionResult(2, 10.10, 10.11, normal, person),
            DetectionResult(3, 10.20, 10.21, occluded, person),
        ]
        for result in results:
            associator.observe("camera1", result)

        smoothed = associator._smoothed_observation(
            "camera1", _matching_observation(results[-1])
        ).appearances[0]
        normal_descriptor = _matching_observation(results[0]).appearances[0]
        occluded_descriptor = _matching_observation(results[-1]).appearances[0]
        self.assertIsNotNone(smoothed)
        self.assertIsNotNone(normal_descriptor)
        self.assertIsNotNone(occluded_descriptor)
        assert smoothed is not None and normal_descriptor is not None and occluded_descriptor is not None
        self.assertGreater(_appearance_similarity(smoothed, normal_descriptor), 0.98)
        self.assertGreater(
            _appearance_similarity(smoothed, normal_descriptor),
            _appearance_similarity(smoothed, occluded_descriptor) + 0.25,
        )

    def test_geometry_breaks_appearance_tie_without_overriding_low_similarity(self) -> None:
        shared_texture = np.zeros(2, dtype=np.float32)
        shared_head = np.array([1.0, 0.0], dtype=np.float32)
        left_appearance = PersonAppearance(
            np.array([1.0, 0.0], dtype=np.float32), shared_texture, shared_head, 0.25
        )
        right_appearance = PersonAppearance(
            np.array([0.8, 0.6], dtype=np.float32), shared_texture, shared_head, 0.25
        )
        different_appearance = PersonAppearance(
            np.array([0.0, 1.0], dtype=np.float32), shared_texture,
            np.array([0.0, 1.0], dtype=np.float32), 0.25,
        )
        left = MatchObservation(
            1, 10.0, 10.01, 640, 480,
            (
                PersonObject("person-1", 0.9, (80.0, 80.0, 160.0, 400.0)),
                PersonObject("person-2", 0.9, (480.0, 80.0, 560.0, 400.0)),
            ),
            (left_appearance, left_appearance),
        )
        right = MatchObservation(
            1, 10.02, 10.03, 640, 480,
            (
                PersonObject("person-7", 0.9, (100.0, 80.0, 180.0, 400.0)),
                PersonObject("person-8", 0.9, (500.0, 80.0, 580.0, 400.0)),
            ),
            (right_appearance, right_appearance),
        )
        kwargs = {
            "max_capture_skew_seconds": 0.4,
            "minimum_similarity": 0.7,
            "minimum_margin": 0.08,
        }
        self.assertEqual(_evaluate_observations(left, right, **kwargs).matches, {})

        # The cameras see the same background 20 pixels apart horizontally.
        geometry = np.array([[1.0, 0.0, 20.0 / 640], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        self.assertEqual(
            _evaluate_observations(left, right, geometry_matrix=geometry, **kwargs).matches,
            {"person-1": "person-7", "person-2": "person-8"},
        )
        # A position bonus must not rescue a pair below the raw 0.7 appearance threshold.
        unrelated = replace(right, appearances=(different_appearance, different_appearance))
        self.assertLess(_appearance_similarity(left_appearance, different_appearance), 0.7)
        self.assertEqual(
            _evaluate_observations(left, unrelated, geometry_matrix=geometry, **kwargs).matches,
            {},
        )

    def test_cross_camera_confirmation_requires_new_frames_from_both_cameras(self) -> None:
        frame = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        result = lambda sequence, received, completed: DetectionResult(
            sequence, received, completed, frame, person
        )
        associator = CrossCameraAssociator()

        associator.observe("camera1", result(1, 10.00, 10.01))
        associator.observe("camera2", result(1, 10.02, 10.03))
        self.assertEqual(associator.confirmed_matches(10.03), {})

        associator.observe("camera1", result(2, 10.20, 10.21))
        self.assertEqual(associator.confirmed_matches(10.21), {})

        associator.observe("camera2", result(2, 10.22, 10.23))
        self.assertEqual(associator.confirmed_matches(10.23), {"person-1": "person-1"})

    def test_cross_camera_history_uses_matching_frame_not_just_nearest_frame(self) -> None:
        red = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        blue = np.full((120, 80, 3), (220, 40, 20), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        def result(sequence: int, received: float, frame: np.ndarray) -> DetectionResult:
            return DetectionResult(sequence, received, received + 0.01, frame, person)

        associator = CrossCameraAssociator()
        associator.observe("camera1", result(1, 10.00, red))
        associator.observe("camera2", result(1, 10.02, red))
        associator.observe("camera2", result(2, 10.20, red))
        # A slightly newer but noisy camera-2 frame is the closest by time.
        associator.observe("camera2", result(3, 10.21, blue))
        associator.observe("camera1", result(2, 10.22, red))

        # The camera-2 frame at 10.20 is still a valid synchronized appearance
        # sample; the single noisy frame at 10.21 must not mask it.
        self.assertEqual(associator.confirmed_matches(10.23), {"person-1": "person-1"})

    def test_cross_camera_one_bad_frame_does_not_restart_confirmation(self) -> None:
        red = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        blue = np.full((120, 80, 3), (220, 40, 20), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        def result(sequence: int, received: float, frame: np.ndarray) -> DetectionResult:
            return DetectionResult(sequence, received, received + 0.01, frame, person)

        associator = CrossCameraAssociator(confirmation_seconds=0.15)
        associator.observe("camera1", result(1, 10.00, red))
        associator.observe("camera2", result(1, 10.02, red))
        associator.observe("camera1", result(2, 10.10, red))
        associator.observe("camera2", result(2, 10.12, blue))
        self.assertEqual(associator.confirmed_matches(10.13), {})

        associator.observe("camera1", result(3, 10.20, red))
        associator.observe("camera2", result(3, 10.22, red))
        # The recovered pair consists of new frames on both sides and spans
        # the original 0.15-second confirmation period.
        self.assertEqual(associator.confirmed_matches(10.23), {"person-1": "person-1"})

    def test_cross_camera_two_independent_bad_frame_pairs_reset_candidate(self) -> None:
        red = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        blue = np.full((120, 80, 3), (220, 40, 20), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        def result(sequence: int, received: float, frame: np.ndarray) -> DetectionResult:
            return DetectionResult(sequence, received, received + 0.01, frame, person)

        associator = CrossCameraAssociator(confirmation_seconds=0.15)
        associator.observe("camera1", result(1, 10.00, red))
        associator.observe("camera2", result(1, 10.02, red))
        for sequence, received in ((2, 10.20), (3, 10.30)):
            associator.observe("camera1", result(sequence, received, red))
            associator.observe("camera2", result(sequence, received + 0.02, blue))
        self.assertEqual(associator.confirmed_matches(10.33), {})
        self.assertEqual(associator.diagnostics(10.33)["pendingPairs"], 0)

        # Red appears in both views again, but after two *distinct* failed
        # frame pairs this must begin a new candidate rather than immediately
        # confirming the first candidate from 10.02.
        associator.observe("camera1", result(4, 10.36, red))
        associator.observe("camera2", result(4, 10.38, red))
        self.assertEqual(associator.confirmed_matches(10.39), {})

    def test_cross_camera_history_still_rejects_actual_capture_skew(self) -> None:
        frame = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        associator = CrossCameraAssociator(max_capture_skew_seconds=0.4)
        for sequence, received in ((1, 10.00), (2, 10.10)):
            associator.observe("camera1", DetectionResult(sequence, received, received + 0.01, frame, person))
        for sequence, received in ((1, 10.51), (2, 10.61)):
            associator.observe("camera2", DetectionResult(sequence, received, received + 0.01, frame, person))

        # Every pair differs by more than 0.4 seconds, even after searching
        # the buffered history. Appearance alone cannot merge them.
        self.assertEqual(associator.confirmed_matches(10.62), {})

    def test_confirmed_match_survives_gap_until_detection_ttl(self) -> None:
        frame = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        associator = CrossCameraAssociator(result_ttl=0.6)
        for sequence, frame_time in ((1, 10.0), (2, 10.2)):
            associator.observe(
                "camera1",
                DetectionResult(sequence, frame_time, frame_time + 0.01, frame, person),
            )
            associator.observe(
                "camera2",
                DetectionResult(sequence, frame_time + 0.02, frame_time + 0.03, frame, person),
            )

        self.assertEqual(associator.confirmed_matches(10.23), {"person-1": "person-1"})
        # No new result arrives for 0.49 s; the last detections are still valid.
        self.assertEqual(associator.confirmed_matches(10.72), {"person-1": "person-1"})
        self.assertEqual(associator.confirmed_matches(10.84), {})

    def test_confirmed_match_is_withdrawn_after_fresh_conflicting_appearances(self) -> None:
        red = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        blue = np.full((120, 80, 3), (220, 40, 20), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        associator = CrossCameraAssociator()
        for sequence, frame_time in ((1, 10.0), (2, 10.2)):
            associator.observe(
                "camera1",
                DetectionResult(sequence, frame_time, frame_time + 0.01, red, person),
            )
            associator.observe(
                "camera2",
                DetectionResult(sequence, frame_time + 0.02, frame_time + 0.03, red, person),
            )
        self.assertEqual(associator.confirmed_matches(10.23), {"person-1": "person-1"})

        associator.observe("camera1", DetectionResult(3, 10.40, 10.41, red, person))
        associator.observe("camera2", DetectionResult(3, 10.42, 10.43, blue, person))
        self.assertEqual(associator.confirmed_matches(10.43), {"person-1": "person-1"})
        associator.observe("camera1", DetectionResult(4, 10.58, 10.59, red, person))
        self.assertEqual(associator.confirmed_matches(10.59), {})

    def test_cross_camera_failed_confirmation_keeps_people_separate(self) -> None:
        red = np.full((120, 80, 3), (20, 40, 220), dtype=np.uint8)
        blue = np.full((120, 80, 3), (220, 40, 20), dtype=np.uint8)
        person = (PersonObject("person-1", 0.9, (10.0, 5.0, 70.0, 115.0)),)
        associator = CrossCameraAssociator()
        associator.observe("camera1", DetectionResult(1, 10.00, 10.01, red, person))
        associator.observe("camera2", DetectionResult(1, 10.02, 10.03, red, person))
        self.assertEqual(associator.confirmed_matches(10.03), {})

        associator.observe("camera1", DetectionResult(2, 10.20, 10.21, red, person))
        associator.observe("camera2", DetectionResult(2, 10.22, 10.23, blue, person))
        self.assertEqual(associator.confirmed_matches(10.23), {})
        diagnostics = associator.diagnostics(10.23)
        self.assertGreater(diagnostics["reasonCounts"].get("low_similarity", 0), 0)
        self.assertEqual(diagnostics["recentFailures"][0]["reason"], "low_similarity")
        self.assertEqual(diagnostics["recentFailures"][0]["camera1Id"], "person-1")
        self.assertEqual(diagnostics["recentFailures"][0]["camera2Id"], "person-1")
        self.assertEqual(associator.diagnostics(41.0)["reasonCounts"], {})

    def test_cross_camera_two_people_confirm_one_to_one_when_order_swaps(self) -> None:
        red, blue = (20, 40, 220), (220, 40, 20)
        boxes = ((10.0, 5.0, 80.0, 115.0), (110.0, 5.0, 180.0, 115.0))
        def frame(first, second):
            image = np.zeros((120, 200, 3), dtype=np.uint8)
            image[5:115, 10:80] = first
            image[5:115, 110:180] = second
            return image

        camera1_frame = frame(red, blue)
        camera2_frame = frame(blue, red)
        camera1_people = (PersonObject("person-1", 0.9, boxes[0]), PersonObject("person-2", 0.9, boxes[1]))
        camera2_people = (PersonObject("person-1", 0.9, boxes[0]), PersonObject("person-2", 0.9, boxes[1]))
        associator = CrossCameraAssociator()
        for sequence, frame_time in ((1, 10.0), (2, 10.2)):
            associator.observe(
                "camera1",
                DetectionResult(sequence, frame_time, frame_time + 0.01, camera1_frame, camera1_people),
            )
            associator.observe(
                "camera2",
                DetectionResult(
                    sequence, frame_time + 0.02, frame_time + 0.03,
                    camera2_frame, camera2_people,
                ),
            )

        self.assertEqual(
            associator.confirmed_matches(10.23),
            {"person-1": "person-2", "person-2": "person-1"},
        )

    def test_draw_person_objects_burns_box_and_person_label_into_frame(self) -> None:
        """The frame handed to a recorder/snapshot must contain the annotation pixels."""
        image = np.zeros((80, 120, 3), dtype=np.uint8)
        detected = PersonObject("person-1", 0.875, (20.0, 24.0, 90.0, 68.0))

        draw_person_objects(image, (detected,))

        # OpenCV stores the selected green color as BGR.  Checking a small
        # neighborhood along each edge avoids coupling this test to the exact
        # anti-aliased line thickness while still proving that the box was
        # burned into the pixels (rather than merely returned as metadata).
        green = np.logical_and.reduce(
            (
                image[:, :, 1] > 150,
                image[:, :, 1] > image[:, :, 0] * 2,
                image[:, :, 1] > image[:, :, 2] * 1.5,
            )
        )
        self.assertTrue(bool(green[24:29, 20:91].any()))
        self.assertTrue(bool(green[64:69, 20:91].any()))
        self.assertTrue(bool(green[24:69, 20:25].any()))
        self.assertTrue(bool(green[24:69, 85:91].any()))
        # The label is rendered above the box when there is room.  Its dark
        # text and filled green background must leave non-black pixels there.
        self.assertGreater(int(image[0:24, 20:90].sum()), 0)


class _FakeDetector:
    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def detect(self, image: np.ndarray) -> list[tuple[tuple[float, float, float, float], float]]:
        return [((1.0, 1.0, float(image.shape[1] - 1), float(image.shape[0] - 1)), 0.9)]


class SchedulingTests(unittest.TestCase):
    def test_detection_result_expires_after_six_tenths_of_a_second(self) -> None:
        frames = {"camera1": LatestFrameBuffer(), "camera2": LatestFrameBuffer()}
        frames["camera1"].publish(
            np.zeros((30, 40, 3), dtype=np.uint8), time.monotonic(), None, None
        )
        coordinator = DetectionCoordinator(
            frames,
            _FakeDetector(),  # type: ignore[arg-type]
            per_camera_fps=30.0,
            result_ttl=0.6,
        )
        coordinator.start()
        try:
            self.assertEqual(coordinator.request_start()["resultTtlMs"], 600)
            deadline = time.monotonic() + 2.0
            while coordinator.latest("camera1") is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(coordinator.latest("camera1"))

            deadline = time.monotonic() + 1.5
            while coordinator.latest("camera1") is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(coordinator.latest("camera1"))
        finally:
            coordinator.request_stop()
            coordinator.shutdown()

    def test_single_worker_round_robins_latest_frame_for_both_cameras(self) -> None:
        frames = {"camera1": LatestFrameBuffer(), "camera2": LatestFrameBuffer()}
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        for sequence in range(3):
            frames["camera1"].publish(image + sequence, time.monotonic(), None, None)
        frames["camera2"].publish(image, time.monotonic(), None, None)
        coordinator = DetectionCoordinator(
            frames,
            _FakeDetector(),  # type: ignore[arg-type]
            per_camera_fps=30.0,
            result_ttl=2.0,
        )
        coordinator.start()
        coordinator.request_start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if coordinator.latest("camera1") and coordinator.latest("camera2"):
                break
            time.sleep(0.01)
        camera1 = coordinator.latest("camera1")
        camera2 = coordinator.latest("camera2")
        self.assertIsNotNone(camera1)
        self.assertIsNotNone(camera2)
        assert camera1 is not None and camera2 is not None
        self.assertEqual(camera1.sequence, 3)
        self.assertEqual(camera2.sequence, 1)
        self.assertEqual(camera1.objects[0].object_id, "person-1")
        self.assertEqual(camera2.objects[0].object_id, "person-1")
        coordinator.request_stop()
        self.assertIsNone(coordinator.latest("camera1"))
        coordinator.shutdown()


class SnapshotTests(unittest.TestCase):
    def test_idle_writers_do_not_create_output_directories(self) -> None:
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            root = Path(directory)
            output = OutputConfig(
                record_dir=root / "recordings-not-created",
                snapshot_dir=root / "snapshots-not-created",
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            recorder = PacketRecorder(output, "camera1")
            snapshots = SnapshotWriter(output, "camera1")
            recorder.start()
            snapshots.start()
            recorder.shutdown()
            snapshots.shutdown()
            self.assertFalse(output.record_dir.exists())
            self.assertFalse(output.snapshot_dir.exists())

    def test_png_validation_and_encoded_writer_use_fixed_output_directory(self) -> None:
        payload = png_bytes(19, 11)
        self.assertEqual(validate_png(payload), (19, 11))
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            output = OutputConfig(
                record_dir=Path(directory),
                snapshot_dir=Path(directory),
                snapshot_format="jpg",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            writer = SnapshotWriter(output, "camera1")
            writer.start()
            path = writer.submit_png(payload)
            self.assertIsNotNone(path)
            assert path is not None
            deadline = time.monotonic() + 2.0
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            writer.shutdown()
            self.assertEqual(path.parent, Path(directory))
            self.assertTrue(path.name.startswith("camera1_snapshot_"))
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(path.read_bytes(), payload)

    def test_png_rejects_bad_crc_and_trailing_data(self) -> None:
        payload = bytearray(png_bytes())
        payload[-5] ^= 1
        with self.assertRaises(ValueError):
            validate_png(bytes(payload))
        with self.assertRaises(ValueError):
            validate_png(png_bytes() + b"trailing")


class OverlayRecordingTests(unittest.TestCase):
    def test_overlay_recorder_drops_oldest_frame_when_queue_is_full(self) -> None:
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            output = OutputConfig(
                record_dir=Path(directory) / "recordings",
                snapshot_dir=Path(directory) / "snapshots",
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            recorder = OverlayVideoRecorder(
                output,
                "camera1",
                lambda image: None,
                queue_size=2,
            )
            recorder.request_start()
            for value in range(3):
                recorder.submit_frame(
                    np.full((8, 8, 3), value, dtype=np.uint8),
                    pts_seconds=value / 5.0,
                    frame_rate=5.0,
            )

            queued = list(recorder._queue.queue)
            self.assertEqual(len(queued), 2)
            self.assertEqual([int(item.image[0, 0, 0]) for item in queued], [1, 2])
            self.assertEqual(recorder.status().state, "armed")
            self.assertFalse(recorder.status().error)

    def test_overlay_recorder_writes_decodable_video_with_detection_boxes(self) -> None:
        """Record output must contain burned-in boxes, not only detection metadata."""
        project_work = Path(__file__).with_name("work")
        project_work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_work) as directory:
            root = Path(directory)
            output = OutputConfig(
                record_dir=root / "recordings",
                snapshot_dir=root / "snapshots",
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=False,
            )
            # 320x240 is large enough for both NVENC and libx264's H.264
            # profiles; tiny synthetic frames can be rejected by hardware
            # encoders before the overlay code is exercised.
            objects = (PersonObject("person-1", 0.9, (40.0, 56.0, 260.0, 200.0)),)
            recorder = OverlayVideoRecorder(
                output,
                "camera1",
                lambda image: draw_person_objects(image, objects),
                queue_size=output.record_queue_packets,
            )
            recorder.start()
            try:
                recorder.request_start()
                for index in range(5):
                    recorder.submit_frame(
                        np.zeros((240, 320, 3), dtype=np.uint8),
                        pts_seconds=index / 5.0,
                        frame_rate=5.0,
                    )

                # Wait until the first frame opened the encoder before asking
                # it to stop.  The stop marker is queued after all frames, so
                # this also verifies that pending frames are drained/finalized.
                recording_deadline = time.monotonic() + 5.0
                while recorder.status().state not in {"recording", "error"}:
                    if time.monotonic() >= recording_deadline:
                        self.fail(f"overlay recorder did not start: {recorder.status()}")
                    time.sleep(0.01)
                if recorder.status().state == "error":
                    self.fail(
                        "no usable annotated video encoder is available: "
                        f"{recorder.status().error or recorder.status()}"
                    )
                recorder.request_stop()

                finalized_deadline = time.monotonic() + 10.0
                while recorder.status().segments_completed < 1:
                    if recorder.status().state == "error":
                        self.fail(f"overlay recorder failed while finalizing: {recorder.status()}")
                    if recorder.status().state == "idle" and not recorder.status().path:
                        self.fail(f"overlay recorder produced no segment: {recorder.status()}")
                    if time.monotonic() >= finalized_deadline:
                        self.fail(f"overlay recorder did not finalize: {recorder.status()}")
                    time.sleep(0.01)
            finally:
                recorder.shutdown()

            status = recorder.status()
            self.assertEqual(status.state, "idle", status.error)
            self.assertTrue(status.path, status)
            path = Path(status.path)
            self.assertEqual(path.parent.resolve(), output.record_dir.resolve())
            self.assertTrue(path.exists(), status)
            self.assertGreater(path.stat().st_size, 0)

            container = av.open(str(path), mode="r")
            try:
                stream = next(iter(container.streams.video))
                frame = next(container.decode(stream))
                decoded = frame.to_ndarray(format="bgr24")
            finally:
                container.close()

            self.assertEqual(decoded.shape[:2], (240, 320))
            # H.264 introduces small quantization changes, so use a tolerant
            # green-dominance mask instead of requiring exact BGR=(45,220,85).
            green = np.logical_and.reduce(
                (
                    decoded[:, :, 1] > 100,
                    decoded[:, :, 1] > decoded[:, :, 0] * 1.25,
                    decoded[:, :, 1] > decoded[:, :, 2] * 1.15,
                )
            )
            self.assertGreater(int(green[56:64, 40:261].sum()), 10)
            self.assertGreater(int(green[194:201, 40:261].sum()), 10)
            self.assertGreater(int(green[56:201, 40:48].sum()), 10)
            self.assertGreater(int(green[56:201, 253:261].sum()), 10)


class BridgeSafetyTests(unittest.TestCase):
    @staticmethod
    def _source(key: str) -> CameraSource:
        return CameraSource(
            key=key,
            label=key,
            stream=StreamConfig(
                host="192.0.2.1",
                port=554,
                username="test-user",
                password="test-password",
                channel=1,
                stream_name="main",
                transport="tcp",
                path=None,
                connect_timeout=1.0,
                read_timeout=1.0,
                analyzeduration_us=100_000,
                probesize=65_536,
                buffer_size=131_072,
                decoder_threads=0,
                hwaccel="none",
                max_backlog_seconds=1.5,
                backlog_frames=5,
            ),
        )

    def test_bridge_rejects_record_on_start_before_any_runtime_starts(self) -> None:
        def output(key: str) -> OutputConfig:
            return OutputConfig(
                record_dir=Path("unused") / key,
                snapshot_dir=Path("unused") / key,
                snapshot_format="png",
                jpeg_quality=90,
                png_compression=3,
                record_container="mp4",
                record_queue_packets=16,
                record_on_start=True,
            )

        with self.assertRaisesRegex(ValueError, "record_on_start is forbidden"):
            CameraBridgeService(
                [self._source("camera1"), self._source("camera2")],
                {"camera1": output("camera1"), "camera2": output("camera2")},
                model_path=Path("unused.onnx"),
                preview_width=960,
                preview_height=540,
                preview_jpeg_quality=82,
                preview_max_fps=10.0,
            )

    def test_capture_request_submits_detection_matched_frames_with_overlays(self) -> None:
        class FakeDetection:
            def __init__(self, results: dict[str, DetectionResult]) -> None:
                self.results = results

            def latest(self, camera_key: str) -> DetectionResult:
                return self.results[camera_key]

        class MemorySnapshotWriter:
            def __init__(self, camera_key: str) -> None:
                self.camera_key = camera_key
                self.images: list[np.ndarray] = []

            def submit(self, image: np.ndarray) -> Path:
                self.images.append(image.copy())
                return Path("trusted-output") / f"{self.camera_key}.png"

        class Runtime:
            def __init__(self, camera_key: str) -> None:
                self.frames = LatestFrameBuffer()
                self.snapshots = MemorySnapshotWriter(camera_key)

        now = time.monotonic()
        results = {
            key: DetectionResult(
                sequence=index,
                received_monotonic=now,
                completed_monotonic=now,
                frame=np.zeros((80, 120, 3), dtype=np.uint8),
                objects=(PersonObject(f"person-{index}", 0.9, (10.0, 12.0, 60.0, 70.0)),),
            )
            for index, key in enumerate(("camera1", "camera2"), start=1)
        }
        service = object.__new__(CameraBridgeService)
        service.cameras = {key: Runtime(key) for key in ("camera1", "camera2")}  # type: ignore[assignment]
        service.detection = FakeDetection(results)  # type: ignore[assignment]

        payload = service.capture_snapshots()

        for key, sequence in (("camera1", 1), ("camera2", 2)):
            item = payload["snapshots"][key]
            self.assertTrue(item["queued"])
            self.assertTrue(item["detectionOverlay"])
            self.assertEqual(item["frameSequence"], sequence)
            writer = service.cameras[key].snapshots
            self.assertEqual(len(writer.images), 1)  # type: ignore[attr-defined]
            self.assertGreater(int(writer.images[0].sum()), 0)  # type: ignore[attr-defined]


class _FakeService:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, bytes]] = []
        self.calls: list[str] = []

    def status_payload(self) -> dict[str, object]:
        return {"version": 1, "detection": {"running": False}, "cameras": {}}

    def preview_jpeg(
        self,
        camera_key: str,
        *,
        after_sequence: int = 0,
        wait_timeout: float = 0.0,
    ) -> EncodedPreview | None:
        self.calls.append(f"preview:{camera_key}:{after_sequence}:{wait_timeout:.3f}")
        if after_sequence >= 7:
            return None
        return EncodedPreview(
            jpeg=b"jpeg",
            sequence=7,
            width=960,
            height=540,
            received_at_ms=1_700_000_000_000,
            age_ms=12.5,
        )

    def toggle_recording(self) -> dict[str, object]:
        self.calls.append("record")
        return {"recording": True}

    def start_detection(self) -> dict[str, object]:
        self.calls.append("start")
        return {"detection": {"running": True}}

    def stop_detection(self) -> dict[str, object]:
        self.calls.append("stop")
        return {"detection": {"running": False}}

    def capture_snapshots(self) -> dict[str, object]:
        self.calls.append("snapshots")
        return {"snapshots": {"camera1": {"queued": True}, "camera2": {"queued": True}}}

    def queue_rendered_snapshot(self, camera_key: str, payload: bytes) -> dict[str, object]:
        width, height = validate_png(payload)
        self.snapshots.append((camera_key, payload))
        return {"camera": camera_key, "queued": True, "width": width, "height": height}


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _FakeService()
        self.shutdown_called = threading.Event()
        handler = make_handler(
            self.service,
            TOKEN,
            max_snapshot_bytes=1024 * 1024,
            shutdown_callback=self.shutdown_called.set,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str | None = None,
        token: str | None = TOKEN,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        headers: dict[str, str] = dict(extra_headers or {})
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, dict(exc.headers.items()), exc.read()
            finally:
                exc.close()

    def test_requires_bearer_authentication(self) -> None:
        status, _headers, body = self.request("/v1/status", token=None)
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    def test_status_preview_and_controls(self) -> None:
        status, _headers, body = self.request("/v1/status")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(json.loads(body)["version"], 1)
        status, headers, body = self.request("/v1/cameras/camera1/frame.jpg")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(headers["X-Frame-Sequence"], "7")
        self.assertEqual(headers["X-Frame-Width"], "960")
        self.assertEqual(headers["X-Frame-Height"], "540")
        self.assertEqual(body, b"jpeg")
        self.assertEqual(self.request("/v1/detection/start", method="POST", body=b"")[0], HTTPStatus.OK)
        self.assertEqual(self.request("/v1/detection/stop", method="POST", body=b"")[0], HTTPStatus.OK)
        self.assertEqual(self.request("/v1/recording/toggle", method="POST", body=b"")[0], HTTPStatus.OK)
        self.assertEqual(self.request("/v1/snapshots", method="POST", body=b"")[0], HTTPStatus.ACCEPTED)

    def test_preview_long_poll_headers_are_bounded_and_return_204_without_a_new_frame(self) -> None:
        status, _headers, body = self.request(
            "/v1/cameras/camera1/frame.jpg",
            extra_headers={
                "X-After-Frame-Sequence": "7",
                "X-Wait-Milliseconds": "750",
            },
        )
        self.assertEqual(status, HTTPStatus.NO_CONTENT)
        self.assertEqual(body, b"")
        self.assertIn("preview:camera1:7:0.750", self.service.calls)

        status, _headers, body = self.request(
            "/v1/cameras/camera1/frame.jpg",
            extra_headers={"X-Wait-Milliseconds": "1001"},
        )
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_preview_header")

    def test_png_endpoint_validates_content_and_never_accepts_a_path(self) -> None:
        payload = png_bytes()
        status, _headers, body = self.request(
            "/v1/cameras/camera2/snapshot.png",
            method="POST",
            body=payload,
            content_type="image/png",
        )
        self.assertEqual(status, HTTPStatus.ACCEPTED)
        self.assertEqual(json.loads(body)["camera"], "camera2")
        self.assertEqual(self.service.snapshots, [("camera2", payload)])
        status, _headers, body = self.request(
            "/v1/cameras/camera1/snapshot.png",
            method="POST",
            body=b'{"path":"C:/untrusted.png"}',
            content_type="application/json",
        )
        self.assertEqual(status, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(json.loads(body)["error"]["code"], "png_required")

    def test_query_strings_are_rejected(self) -> None:
        status, _headers, body = self.request("/v1/status?path=C%3A%2Fanything")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_url")


if __name__ == "__main__":
    unittest.main()
