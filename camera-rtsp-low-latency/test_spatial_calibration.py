"""Known-camera calibration recovery and independent field-error accounting."""
import copy
import math
import unittest

import cv2
import numpy as np

from spatial_calibration import (
    board_points, calibration_from_observations, evaluate_samples, export_config,
    quality_diagnostics, rotation_aligning_vectors,
)
from spatial_localization import camera_pose, default_config


@unittest.skipUnless(
    hasattr(cv2, "calibrateCamera") and hasattr(cv2, "stereoCalibrate"),
    "offline calibration suite requires full OpenCV; run with E:\\python\\python.exe (keep CUDA environment unchanged)",
)
class SpatialCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        generator = np.random.default_rng(20261020)
        cls.k1 = np.array([[1450., 0., 960.], [0., 1440., 540.], [0., 0., 1.]])
        cls.k2 = np.array([[1445., 0., 958.], [0., 1455., 542.], [0., 0., 1.]])
        cls.rotation = cv2.Rodrigues(np.array([0.015, 0.025, -0.006]))[0]
        cls.translation = np.array([-0.1798, 0.001, 0.003])
        cls.translation *= 0.18 / np.linalg.norm(cls.translation)
        cls.objects, cls.points1, cls.points2 = [], [], []
        template = board_points(9, 6, 0.06)
        for _ in range(24):
            rvec = generator.uniform(-0.4, 0.4, 3)
            tvec = np.array([generator.uniform(-0.45, 0.05), generator.uniform(-0.25, 0.05), generator.uniform(1.4, 2.8)])
            board_rotation = cv2.Rodrigues(rvec)[0]
            right_rvec = cv2.Rodrigues(cls.rotation @ board_rotation)[0]
            right_tvec = cls.rotation @ tvec + cls.translation
            first = cv2.projectPoints(template, rvec, tvec, cls.k1, np.zeros(5))[0]
            second = cv2.projectPoints(template, right_rvec, right_tvec, cls.k2, np.zeros(5))[0]
            first += generator.normal(0, 0.015, first.shape).astype(np.float32)
            second += generator.normal(0, 0.015, second.shape).astype(np.float32)
            cls.objects.append(template.copy())
            cls.points1.append(first)
            cls.points2.append(second)
        cls.result = calibration_from_observations(cls.objects, cls.points1, cls.points2, (1920, 1080))

    def test_recovers_known_intrinsics_and_nonparallel_stereo(self):
        self.assertLess(abs(self.result["camera1"]["matrix"][0, 0] - self.k1[0, 0]), 2)
        self.assertLess(abs(self.result["camera2"]["matrix"][1, 1] - self.k2[1, 1]), 2)
        np.testing.assert_allclose(self.result["rotation"], self.rotation, atol=0.001)
        np.testing.assert_allclose(self.result["translationMeters"], self.translation, atol=0.001)
        self.assertLess(self.result["stereoRmsPx"], 0.1)

    def test_quality_gate_rejects_incorrect_board_scale(self):
        self.assertTrue(quality_diagnostics(self.result, 0.18, 0.1, 2)["passed"])
        wrong_scale = copy.deepcopy(self.result)
        wrong_scale["translationMeters"] *= 1.5
        diagnostics = quality_diagnostics(wrong_scale, 0.18, 0.1, 2)
        self.assertFalse(diagnostics["passed"])
        self.assertIn("baseline", diagnostics["failures"][0])

    def test_quality_gate_rejects_bad_residuals(self):
        wrong = copy.deepcopy(self.result)
        wrong["stereoRmsPx"] = 5
        self.assertFalse(quality_diagnostics(wrong, 0.18, 0.1, 2)["passed"])

    def test_per_view_filter_drops_the_worst_paired_observation(self):
        first = [points.copy() for points in self.points1]
        second = [points.copy() for points in self.points2]
        generator = np.random.default_rng(42)
        first[7] += generator.normal(0, 12, first[7].shape).astype(np.float32)
        result = calibration_from_observations(
            self.objects, first, second, (1920, 1080), max_view_rms_px=1.0, min_pairs=15,
        )
        self.assertEqual(result["pairCount"], len(self.objects) - 1)
        self.assertEqual(result["viewFilter"]["rejectedViews"][0]["index"], 7)
        self.assertNotIn(7, result["retainedObservationIndices"])
        self.assertEqual(len(result["perViewRmsPx"]["camera1"]), result["pairCount"])

    def test_export_preserves_measured_positions_and_stereo_rotation(self):
        base = default_config()
        config = export_config(self.result, base)
        c1, w1 = camera_pose(config, "camera1")
        c2, w2 = camera_pose(config, "camera2")
        np.testing.assert_allclose(c1, [-0.305, 0.0, 0], atol=1e-12)
        np.testing.assert_allclose(c2, [0.305, 0.0, 0], atol=1e-12)
        np.testing.assert_allclose(w2.T @ w1, self.result["rotation"], atol=1e-10)
        scaled_t = self.result["translationMeters"] * 0.61 / np.linalg.norm(self.result["translationMeters"])
        np.testing.assert_allclose(w2.T @ (c1 - c2), scaled_t, atol=1e-10)
        self.assertEqual(config["cameras"]["camera1"]["intrinsics"]["source"], "calibrated")
        self.assertIsNone(base["cameras"]["camera1"]["intrinsics"]["fx"])

    def test_alignment_handles_opposite_and_general_vectors(self):
        for source, target in (([1, 0, 0], [-1, 0, 0]), ([0.2, 0.3, 0.4], [1, 0, 0]), ([1, 0, 0], [1, 0, 0])):
            rotation = rotation_aligning_vectors(np.array(source), np.array(target))
            normalized_source = np.array(source) / np.linalg.norm(source)
            normalized_target = np.array(target) / np.linalg.norm(target)
            np.testing.assert_allclose(rotation @ normalized_source, normalized_target, atol=1e-12)
            self.assertAlmostEqual(float(np.linalg.det(rotation)), 1.0)

    def test_field_errors_use_independent_truth_and_signed_axes(self):
        report = evaluate_samples([
            {"xTruth": 1, "zTruth": 4, "xEstimated": 1.3, "zEstimated": 4.4},
            {"xTruth": -1, "zTruth": 6, "xEstimated": -1.3, "zEstimated": 5.6},
        ])
        self.assertAlmostEqual(report["xRmseMeters"], 0.3)
        self.assertAlmostEqual(report["zRmseMeters"], 0.4)
        self.assertAlmostEqual(report["planarRmseMeters"], 0.5)
        self.assertEqual(report["sampleCount"], 2)

    def test_rejects_missing_or_nonfinite_field_samples(self):
        with self.assertRaises(ValueError):
            evaluate_samples([])
        with self.assertRaises(ValueError):
            evaluate_samples([{"xTruth": 0, "zTruth": 1, "xEstimated": math.nan, "zEstimated": 1}])


if __name__ == "__main__":
    unittest.main()
