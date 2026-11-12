"""AprilGrid physical geometry, shared tag correspondences, and recovery.

The reference-photo test checks tag identity/order only.  The synthetic recovery
test exercises metre units but does not claim on-site positioning accuracy.
"""
from __future__ import annotations

import copy
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import spatial_calibration_boards as boards
from spatial_calibration_boards import AprilGridBoard, load_tag_board


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = PROJECT_ROOT / "work" / "aprilgrid-test"
REFERENCE_PHOTO = PROJECT_ROOT / "outputs" / "aprilgrid-20261021" / "board-reference.jpg"
FULL_OPENCV = all(hasattr(cv2, name) for name in (
    "calibrateCamera", "stereoCalibrate", "projectPoints", "Rodrigues",
))
ARUCO_AVAILABLE = hasattr(cv2, "aruco") and hasattr(cv2.aruco, "getPredefinedDictionary")


def board_config() -> dict:
    return {
        "version": 1,
        "boardType": "aprilgrid",
        "dictionary": "DICT_APRILTAG_36h11",
        "markerBorderBits": 2,
        "tagRows": 6,
        "tagColumns": 6,
        "tagSizeMeters": 0.055,
        "tagSpacingMeters": 0.0165,
        "idsTopToBottom": [list(range(start, start + 6)) for start in (30, 24, 18, 12, 6, 0)],
        "tagRotationDeg": 180,
        "minCommonTags": 8,
    }


def image_observations(tag_ids, offset=0.0) -> dict[int, np.ndarray]:
    """Unique pixels per ID/corner make any accidental ordering mismatch clear."""
    return {
        tag_id: np.array([
            [tag_id * 10 + offset, 100 + offset],
            [tag_id * 10 + 1 + offset, 101 + offset],
            [tag_id * 10 + 2 + offset, 102 + offset],
            [tag_id * 10 + 3 + offset, 103 + offset],
        ], dtype=np.float32)
        for tag_id in tag_ids
    }


class AprilGridGeometryTests(unittest.TestCase):
    def setUp(self):
        self.board = AprilGridBoard(board_config())

    def test_dimensions_include_six_tags_and_five_clear_gaps(self):
        metadata = self.board.metadata()
        self.assertAlmostEqual(metadata["patternWidthMeters"], 0.4125)
        self.assertAlmostEqual(metadata["patternHeightMeters"], 0.4125)
        self.assertAlmostEqual(metadata["tagPitchMeters"], 0.0715)
        self.assertEqual(metadata["idsTopToBottom"], board_config()["idsTopToBottom"])
        self.assertEqual(metadata["dictionary"], "DICT_APRILTAG_36h11")
        self.assertEqual(metadata["markerBorderBits"], 2)

    def test_canonical_corners_follow_printed_180_degree_rotation(self):
        # Physical top-left tag is ID 30.  Its marker bits are rotated 180 deg.
        expected = np.array([
            [0.055, 0.055, 0],  # canonical TL is physical BR
            [0, 0.055, 0],      # canonical TR is physical BL
            [0, 0, 0],          # canonical BR is physical TL
            [0.055, 0, 0],      # canonical BL is physical TR
        ], dtype=np.float32)
        corners = self.board.object_corners(30)
        self.assertEqual(corners.dtype, np.float32)
        self.assertEqual(corners.shape, (4, 3))
        np.testing.assert_allclose(corners, expected, atol=1e-8)

    def test_id_layout_maps_bottom_right_tag_to_correct_physical_location(self):
        # ID 5 is the physical bottom-right tag; 5 pitches = 0.3575 m.
        expected = np.array([
            [0.4125, 0.4125, 0],
            [0.3575, 0.4125, 0],
            [0.3575, 0.3575, 0],
            [0.4125, 0.3575, 0],
        ], dtype=np.float32)
        np.testing.assert_allclose(self.board.object_corners(5), expected, atol=1e-7)

    def test_pairing_uses_common_ids_and_not_dictionary_insertion_order(self):
        common = [0, 1, 2, 3, 4, 6, 7, 8, 9, 10]
        left = image_observations([30] + list(reversed(common)))
        right = image_observations([35] + common[::2] + common[1::2], offset=20)
        objects, first, second, metadata = self.board.pair_observations(left, right)
        self.assertEqual(metadata["commonTagIds"], common)
        self.assertEqual(metadata["cornerCount"], 40)
        self.assertEqual(objects.shape, (40, 3))
        self.assertEqual(first.shape, (40, 1, 2))
        self.assertEqual(second.shape, (40, 1, 2))
        np.testing.assert_allclose(objects, np.concatenate([self.board.object_corners(tag_id) for tag_id in common]))
        np.testing.assert_allclose(first.reshape(-1, 2), np.concatenate([left[tag_id] for tag_id in common]))
        np.testing.assert_allclose(second.reshape(-1, 2), np.concatenate([right[tag_id] for tag_id in common]))

    def test_insufficient_common_tags_rejected(self):
        common = [0, 1, 2, 6, 7, 8, 9]
        with self.assertRaises(ValueError):
            self.board.pair_observations(image_observations(common), image_observations(common))

    def test_disjoint_tags_rejected(self):
        with self.assertRaises(ValueError):
            self.board.pair_observations(image_observations(range(10)), image_observations(range(18, 28)))

    def test_single_tag_row_or_column_rejected_even_with_lower_count_threshold(self):
        for ids in ([0, 1, 2, 3, 4, 5], [0, 6, 12, 18, 24, 30]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                observations = image_observations(ids)
                self.board.pair_observations(observations, observations, min_common_tags=4)

    def test_no_aruco_runtime_reports_clear_value_error(self):
        with patch.object(boards, "cv2", SimpleNamespace()):
            with self.assertRaises(ValueError):
                self.board.detect(np.zeros((100, 100), dtype=np.uint8))

    def test_json_loader_round_trip_uses_project_local_temporary_files(self):
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="config-", dir=WORK_ROOT) as temporary:
            path = Path(temporary) / "board.json"
            path.write_text(json.dumps(board_config()), encoding="utf-8")
            loaded = load_tag_board(path)
            np.testing.assert_array_equal(loaded.object_corners(30), self.board.object_corners(30))

    def test_invalid_config_rejected(self):
        updates = [
            {"version": 2},
            {"boardType": "chessboard"},
            {"dictionary": "NOT_A_DICTIONARY"},
            {"tagRows": 0},
            {"tagColumns": 0},
            {"tagSizeMeters": 0},
            {"tagSizeMeters": float("nan")},
            {"tagSpacingMeters": -0.01},
            {"tagRotationDeg": 45},
            {"minCommonTags": 0},
            {"idsTopToBottom": [[0]]},
        ]
        duplicate = copy.deepcopy(board_config()["idsTopToBottom"])
        duplicate[0][0] = duplicate[0][1]
        updates.append({"idsTopToBottom": duplicate})
        for update in updates:
            with self.subTest(update=update), self.assertRaises(ValueError):
                config = board_config()
                config.update(update)
                AprilGridBoard(config)

    def test_marker_border_width_must_be_explicit_and_not_boolean(self):
        missing = board_config()
        del missing["markerBorderBits"]
        with self.assertRaisesRegex(ValueError, "markerBorderBits"):
            AprilGridBoard(missing)
        for border_bits in (True, False, 0, 3):
            with self.subTest(markerBorderBits=border_bits), self.assertRaisesRegex(ValueError, "markerBorderBits"):
                config = board_config()
                config["markerBorderBits"] = border_bits
                AprilGridBoard(config)
        for border_bits in (1, 2):
            config = board_config()
            config["markerBorderBits"] = border_bits
            self.assertEqual(AprilGridBoard(config).metadata()["markerBorderBits"], border_bits)


@unittest.skipUnless(ARUCO_AVAILABLE, "reference image requires full OpenCV with aruco/AprilTag")
class AprilGridReferencePhotoTests(unittest.TestCase):
    def test_user_board_decodes_all_36_tags_with_expected_printed_layout(self):
        if not REFERENCE_PHOTO.exists():
            self.skipTest("user's reference photograph is not present")
        image = cv2.imread(str(REFERENCE_PHOTO))
        self.assertIsNotNone(image)
        detections = AprilGridBoard(board_config()).detect(image)
        self.assertEqual(set(detections), set(range(36)))
        centers = {tag_id: np.mean(corners.reshape(4, 2), axis=0) for tag_id, corners in detections.items()}
        self.assertLess(centers[30][0], centers[35][0])
        self.assertLess(centers[30][1], centers[0][1])
        # Canonical corner 0 is physical BR in this particular printed board.
        self.assertTrue(np.all(detections[30].reshape(4, 2)[0] > detections[30].reshape(4, 2)[2]))

    def test_reference_layout_homography_checks_outer_corners_not_just_decoded_ids(self):
        if not REFERENCE_PHOTO.exists():
            self.skipTest("user's reference photograph is not present")
        image = cv2.imread(str(REFERENCE_PHOTO))
        self.assertIsNotNone(image)
        board = AprilGridBoard(board_config())
        detections = board.detect(image)
        self.assertEqual(set(detections), set(range(36)))
        identifiers = sorted(detections)
        object_xy = np.concatenate([board.object_corners(tag_id)[:, :2] for tag_id in identifiers])
        pixels = np.concatenate([detections[tag_id] for tag_id in identifiers])
        homography, _mask = cv2.findHomography(object_xy, pixels, 0)
        self.assertIsNotNone(homography)
        projected = cv2.perspectiveTransform(object_xy.reshape(-1, 1, 2), homography).reshape(-1, 2)
        rms_pixels = float(np.sqrt(np.mean(np.sum((projected - pixels) ** 2, axis=1))))
        # BorderBits=1 can decode all IDs while placing corners inside the tags.
        # This image-layout check catches that regression; it is not field accuracy.
        self.assertLess(rms_pixels, 2.0)


@unittest.skipUnless(FULL_OPENCV and ARUCO_AVAILABLE, "AprilGrid integration requires full OpenCV/aruco")
class AprilGridIntegrationTests(unittest.TestCase):
    def setUp(self):
        if not REFERENCE_PHOTO.exists():
            self.skipTest("user's reference photograph is not present")
        self.image = cv2.imread(str(REFERENCE_PHOTO))
        self.assertIsNotNone(self.image)
        self.board = AprilGridBoard(board_config())
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="integration-", dir=WORK_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write_pair(self, name, left, right):
        for key, image in (("camera1", left), ("camera2", right)):
            folder = self.root / "pairs" / key
            folder.mkdir(parents=True, exist_ok=True)
            self.assertTrue(cv2.imwrite(str(folder / name), image))

    @staticmethod
    def marker_sheet(ids):
        # Synthetic marker images exercise actual detection before count rejection.
        image = np.full((520, 1000), 255, dtype=np.uint8)
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        for index, tag_id in enumerate(ids):
            x, y = 30 + (index % 5) * 190, 30 + (index // 5) * 240
            marker = cv2.aruco.generateImageMarker(dictionary, tag_id, 160, borderBits=2)
            marker = np.rot90(marker, 2)
            image[y:y + 160, x:x + 160] = marker
        return image

    def test_load_raw_photo_pair_retains_144_common_corners_and_raw_pixel_size(self):
        from spatial_calibration import load_aprilgrid_pairs

        self.write_pair("0001.png", self.image, self.image)
        objects, first, second, size, accepted, rejected, details = load_aprilgrid_pairs(self.root / "pairs", self.board)
        self.assertEqual(size, (self.image.shape[1], self.image.shape[0]))
        self.assertEqual(accepted, ["0001.png"])
        self.assertEqual(rejected, [])
        self.assertEqual(objects[0].shape, (144, 3))
        self.assertEqual(first[0].shape, (144, 1, 2))
        np.testing.assert_array_equal(first[0], second[0])
        self.assertEqual(details[0]["commonTagIds"], list(range(36)))
        self.assertEqual(details[0]["commonTagCount"], 36)
        self.assertEqual(details[0]["cornerCount"], 144)

    def test_blank_and_insufficient_common_tags_write_failure_diagnostics_without_config(self):
        from spatial_calibration import calibrate, load_aprilgrid_pairs

        blank = np.full((520, 1000), 255, dtype=np.uint8)
        self.write_pair("0001.png", blank, blank)
        self.write_pair("0002.png", self.marker_sheet(range(10)), self.marker_sheet(range(3, 10)))
        objects, _first, _second, _size, accepted, rejected, _details = load_aprilgrid_pairs(self.root / "pairs", self.board)
        self.assertEqual(objects, [])
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 2)
        self.assertIn("only 0 common board tags", rejected[0])
        self.assertIn("only 7 common board tags", rejected[1])
        profile = self.root / "board.json"
        profile.write_text(json.dumps(board_config()), encoding="utf-8")
        args = argparse.Namespace(board_type="aprilgrid", board_config=profile, input=self.root / "pairs",
                                  base_config=self.root / "not-existing-base.json", output=self.root / "calibrated.json",
                                  diagnostics=self.root / "diagnostics.json", min_pairs=15,
                                  baseline_tolerance=0.1, max_rms=2)
        with self.assertRaisesRegex(ValueError, "only 0 valid paired boards"):
            calibrate(args)
        self.assertFalse(args.output.exists())
        report = json.loads(args.diagnostics.read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertFalse(report["configWritten"])
        self.assertEqual(report["pairCount"], 0)
        self.assertEqual(report["minimumPairs"], 15)
        self.assertEqual(len(report["rejectedFiles"]), 2)
        self.assertEqual(report["board"]["tagSizeMeters"], 0.055)

    def test_mocked_capture_checks_common_ids_and_records_board_geometry(self):
        import spatial_calibration as calibration

        fixture_image = self.image

        class FakeReader:
            def __init__(self, _config, frames, _status, _recorder, _stop, worker_name):
                self.frames = frames

            def start(self):
                self.frames.publish(fixture_image.copy(), time.monotonic(), 0.0, 0.0)

            def join(self, timeout):
                pass

        profile = self.root / "board.json"
        profile.write_text(json.dumps(board_config()), encoding="utf-8")
        args = argparse.Namespace(board_type="aprilgrid", board_config=profile, output=self.root / "capture",
                                  columns=9, rows=6, manual=False, display=False, pairs=1, interval=0.01,
                                  duration=2.0, max_skew_ms=100, skip_board_check=False)
        sources = [SimpleNamespace(key=key, stream=None) for key in ("camera1", "camera2")]
        with patch("camera_low_latency.StreamWorker", FakeReader), \
             patch("camera_low_latency.parse_args", return_value=SimpleNamespace()), \
             patch("camera_low_latency.build_sources_and_outputs", return_value=(sources, {})), \
             patch("dotenv.load_dotenv", return_value=False), \
             patch.dict("os.environ", {"CAMERA1_PASSWORD": "test-only", "CAMERA2_PASSWORD": "test-only"}), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(calibration.capture(args), 0)
        manifest = json.loads((args.output / "capture-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["boardType"], "aprilgrid")
        self.assertEqual(manifest["board"]["tagSizeMeters"], 0.055)
        self.assertEqual(manifest["board"]["tagSpacingMeters"], 0.0165)
        self.assertTrue(manifest["boardCheckedAtCapture"])
        self.assertEqual(len(manifest["pairs"]), 1)
        self.assertEqual(manifest["pairs"][0]["commonTagIds"], list(range(36)))
        self.assertEqual(manifest["pairs"][0]["commonTagCount"], 36)
        self.assertEqual(manifest["pairs"][0]["cornerCount"], 144)
        self.assertNotIn("test-only", json.dumps(manifest))
        for key in ("camera1", "camera2"):
            np.testing.assert_array_equal(cv2.imread(str(args.output / key / "0001.png")), self.image)


@unittest.skipUnless(FULL_OPENCV, "synthetic calibration recovery requires full OpenCV; run E:\\python\\python.exe")
class AprilGridCalibrationRecoveryTests(unittest.TestCase):
    def test_variable_partial_tag_sets_recover_intrinsics_stereo_pose_and_metre_scale(self):
        from spatial_calibration import calibration_from_observations

        board = AprilGridBoard(board_config())
        generator = np.random.default_rng(20261021)
        k1 = np.array([[1450., 0., 960.], [0., 1440., 540.], [0., 0., 1.]])
        k2 = np.array([[1445., 0., 958.], [0., 1455., 542.], [0., 0., 1.]])
        rotation = cv2.Rodrigues(np.array([0.015, 0.025, -0.006]))[0]
        translation = np.array([-0.1798, 0.001, 0.003])
        translation *= 0.18 / np.linalg.norm(translation)
        objects, first_points, second_points = [], [], []
        corner_counts = set()
        for index in range(24):
            # Eight fixed tags span two rows/four columns; additional tags vary.
            common_ids = [0, 1, 2, 3, 6, 7, 8, 9]
            remaining = [value for value in range(36) if value not in common_ids]
            common_ids += generator.choice(remaining, size=index % 20, replace=False).tolist()
            rvec = generator.uniform(-0.4, 0.4, 3)
            tvec = np.array([generator.uniform(-0.4, 0.05), generator.uniform(-0.25, 0.05), generator.uniform(1.4, 2.8)])
            right_rvec = cv2.Rodrigues(rotation @ cv2.Rodrigues(rvec)[0])[0]
            right_tvec = rotation @ tvec + translation
            left, right = {}, {}
            for tag_id in reversed(common_ids):
                template = board.object_corners(tag_id)
                left[tag_id] = cv2.projectPoints(template, rvec, tvec, k1, np.zeros(5))[0].reshape(4, 2)
                right[tag_id] = cv2.projectPoints(template, right_rvec, right_tvec, k2, np.zeros(5))[0].reshape(4, 2)
                left[tag_id] += generator.normal(0, 0.01, (4, 2)).astype(np.float32)
                right[tag_id] += generator.normal(0, 0.01, (4, 2)).astype(np.float32)
            obj, pixels1, pixels2, metadata = board.pair_observations(left, right)
            objects.append(obj)
            first_points.append(pixels1)
            second_points.append(pixels2)
            corner_counts.add(metadata["cornerCount"])
        self.assertGreater(len(corner_counts), 10)
        result = calibration_from_observations(objects, first_points, second_points, (1920, 1080))
        self.assertLess(abs(result["camera1"]["matrix"][0, 0] - k1[0, 0]), 2)
        self.assertLess(abs(result["camera2"]["matrix"][1, 1] - k2[1, 1]), 2)
        np.testing.assert_allclose(result["rotation"], rotation, atol=0.001)
        np.testing.assert_allclose(result["translationMeters"], translation, atol=0.001)
        self.assertAlmostEqual(float(np.linalg.norm(result["translationMeters"])), 0.18, delta=0.001)
        self.assertLess(result["stereoRmsPx"], 0.1)


if __name__ == "__main__":
    unittest.main()
