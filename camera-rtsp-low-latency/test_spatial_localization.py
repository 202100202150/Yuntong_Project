"""Tests for metric localization from paired drone detections."""
from __future__ import annotations

from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from spatial_localization import (
    SpatialLocalizer,
    calibration_ready,
    camera_pose,
    camera_positions,
    default_config,
    derive_extrinsic_config,
    pose_angles_from_camera_to_world,
    spatial_frame_skew_seconds,
    triangulate_correspondences,
    validate_config,
)


def calibrated_config() -> dict:
    config = default_config()
    config["rig"]["baselineMeters"] = 0.61
    for camera in config["cameras"].values():
        camera["intrinsics"].update(
            imageWidth=1280,
            imageHeight=720,
            fx=1000.0,
            fy=1000.0,
            cx=640.0,
            cy=360.0,
            source="calibrated",
        )
    return config


def common_board_config(camera1_left: bool) -> dict:
    config = calibrated_config()
    config["extrinsics"]["enabled"] = True
    rotation, _ = cv2.Rodrigues(np.array([0.24, -0.4, 0.3]))
    midpoint = np.array([2.0, -1.0, 3.2])
    for key, is_left in (("camera1", camera1_left), ("camera2", not camera1_left)):
        centre = midpoint + rotation[:, 0] * (-0.375 if is_left else 0.375)
        config["extrinsics"][key].update(
            rotationCameraToBoard=rotation.tolist(),
            translationCameraInBoard=centre.tolist(),
            source="test",
        )
    return config


def project(points: np.ndarray, config: dict, key: str) -> np.ndarray:
    centre, world_to_camera = camera_pose(config, key)
    intrinsics = config["cameras"][key]["intrinsics"]
    matrix = np.array(
        [
            [intrinsics["fx"], 0, intrinsics["cx"]],
            [0, intrinsics["fy"], intrinsics["cy"]],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )
    camera_points = (points - centre) @ world_to_camera
    pixels, _ = cv2.projectPoints(
        camera_points, np.zeros(3), np.zeros(3), matrix,
        np.asarray(intrinsics["distortion"]),
    )
    return pixels.reshape(-1, 2)


IMAGE_SIZE = (1280, 720)
EMPTY_FRAME = np.zeros((IMAGE_SIZE[1], IMAGE_SIZE[0], 3), dtype=np.uint8)


def drone_frame(config, camera, points, *, time=10.0, frame=EMPTY_FRAME, sequence=1):
    objects = []
    for index, (u, v) in enumerate(project(np.asarray(points), config, camera)):
        objects.append(SimpleNamespace(
            bbox=(u - 10, v - 6, u + 10, v + 6),
            object_id=f"{camera}-{index}",
        ))
    return SimpleNamespace(frame=frame, objects=tuple(objects), sequence=sequence, received_monotonic=time)


class SpatialGeometryTests(unittest.TestCase):
    def test_default_baseline_is_current_parameter(self):
        config = validate_config(default_config())
        self.assertAlmostEqual(config["rig"]["baselineMeters"], 0.61)
        self.assertEqual(
            camera_positions(config),
            {
                "camera1": {"x": -0.305, "y": 0.0, "z": 0.0},
                "camera2": {"x": 0.305, "y": 0.0, "z": 0.0},
            },
        )
        self.assertFalse(calibration_ready(config))

    def test_extrinsic_geometry_uses_configured_baseline(self):
        config = calibrated_config()
        config["extrinsics"]["enabled"] = True
        for key in ("camera1", "camera2"):
            config["extrinsics"][key] = {
                "rotationCameraToBoard": np.eye(3).tolist(),
                "translationCameraInBoard": (
                    [-0.31, 0.0, 0.0] if key == "camera1" else [0.31, 0.0, 0.0]
                ),
                "source": "test",
            }
        positions = camera_positions(validate_config(config))
        self.assertAlmostEqual(positions["camera1"]["x"], -0.305)
        self.assertAlmostEqual(positions["camera2"]["x"], 0.305)

    def test_import_derives_metric_geometry_for_either_camera_order(self):
        for camera1_left in (True, False):
            with self.subTest(camera1_left=camera1_left):
                raw = common_board_config(camera1_left)
                raw["rig"].update(midpoint={"x": 12.0, "y": 3.0, "z": -2.0}, baselineYawDeg=18.0)
                config = derive_extrinsic_config(raw)
                rig = config["rig"]
                self.assertEqual(raw["rig"]["geometrySource"], "manual")
                self.assertEqual(rig["geometrySource"], "extrinsics")
                self.assertAlmostEqual(rig["baselineMeters"], 0.75)
                self.assertEqual(rig["midpoint"], {"x": 0.0, "y": 0.0, "z": 0.0})
                self.assertEqual(rig["baselineYawDeg"], 0.0)
                self.assertEqual(config["cameras"], raw["cameras"])
                for key, is_left in (("camera1", camera1_left), ("camera2", not camera1_left)):
                    position = rig["cameraPositions"][key]
                    self.assertEqual(position["side"], "left" if is_left else "right")
                    self.assertAlmostEqual(position["distanceToOriginMeters"], 0.375)
                    centre, transform = camera_pose(config, key)
                    np.testing.assert_allclose(centre, [0.375 if is_left else -0.375, 0.0, 0.0])
                    np.testing.assert_allclose(transform, np.diag([-1.0, -1.0, 1.0]), atol=1e-8)
                    self.assertEqual(camera_positions(config)[key], {axis: position[axis] for axis in "xyz"})
                edited = {**config, "rig": {**rig, "baselineMeters": 9.0, "cameraPositions": None}}
                self.assertEqual(validate_config(edited)["rig"], rig)

    def test_import_rejects_missing_coincident_or_ambiguous_calibration(self):
        with self.assertRaisesRegex(ValueError, "requires both enabled"):
            derive_extrinsic_config(calibrated_config())
        config = common_board_config(True)
        pose1, pose2 = (config["extrinsics"][key] for key in ("camera1", "camera2"))
        pose2["translationCameraInBoard"] = pose1["translationCameraInBoard"]
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            derive_extrinsic_config(config)
        rotation = np.array(pose1["rotationCameraToBoard"])
        centre = np.array(pose1["translationCameraInBoard"])
        pose2["translationCameraInBoard"] = (centre + 0.75 * rotation[:, 2]).tolist()
        with self.assertRaisesRegex(ValueError, "left to right"):
            derive_extrinsic_config(config)
        pose2["translationCameraInBoard"] = (centre + 0.6 * rotation[:, 0] + 0.45 * rotation[:, 1]).tolist()
        self.assertAlmostEqual(derive_extrinsic_config(config)["rig"]["baselineMeters"], 0.75)
        pose2["rotationCameraToBoard"] = (-rotation).tolist()
        with self.assertRaisesRegex(ValueError, "proper rotation"):
            derive_extrinsic_config(config)

    def test_triangulation_utility_still_recovers_metric_points(self):
        config = calibrated_config()
        points = np.array([[0.4, 1.2, 6.0], [-0.8, 1.8, 10.0]], dtype=np.float64)
        actual, stats = triangulate_correspondences(
            project(points, config, "camera1"),
            project(points, config, "camera2"),
            config,
            {"camera1": (1280, 720), "camera2": (1280, 720)},
        )
        np.testing.assert_allclose(actual, points, atol=1e-8)
        self.assertEqual(stats["validCount"], 2)

    def test_pose_inverse_remains_available_for_calibration_tools(self):
        config = calibrated_config()
        _, matrix = camera_pose(config, "camera1")
        angles = pose_angles_from_camera_to_world(matrix)
        self.assertAlmostEqual(angles["yawDeg"], 0.0, places=8)
        self.assertAlmostEqual(angles["pitchDeg"], 0.0, places=8)
        self.assertAlmostEqual(angles["rollDeg"], 0.0, places=8)


class DroneStereoTests(unittest.TestCase):
    def test_imported_poses_recover_motion_in_xyz_in_either_physical_order(self):
        path = [(0.0, 0.2, 8.0), (0.6, 0.8, 9.5), (1.1, 1.5, 12.0)]
        for camera1_left in (True, False):
            with self.subTest(camera1_left=camera1_left):
                config = derive_extrinsic_config(common_board_config(camera1_left))
                localizer = SpatialLocalizer(config)
                for sequence, point in enumerate(path, 1):
                    result = localizer.localize(*(
                        drone_frame(config, key, [point], sequence=sequence)
                        for key in ("camera1", "camera2")
                    ))
                    self.assertEqual(result["state"], "ready")
                    target, = result["targets"]
                    self.assertEqual(target["class"], "drone")
                    self.assertEqual(target["keypointCount"], 1)
                    np.testing.assert_allclose([target[axis] for axis in "xyz"], point, atol=0.001)
                    self.assertAlmostEqual(target["rangeMeters"], np.linalg.norm(point), places=3)
                    self.assertGreater(target["elevationDeg"], 0.0)

    def test_missing_or_edge_clipped_view_cannot_create_a_position(self):
        config = calibrated_config()
        point = [(0.0, 0.4, 10.0)]
        left = drone_frame(config, "camera1", point)
        right = drone_frame(config, "camera2", point)
        right.objects = ()
        localizer = SpatialLocalizer(config)
        result = localizer.localize(left, right)
        self.assertEqual(result["state"], "no_targets")
        self.assertEqual(result["targets"], [])
        self.assertEqual(localizer.localize(left, None)["targets"], [])
        right.objects = (SimpleNamespace(bbox=(0.0, 306.0, 12.0, 330.0), object_id="edge"),)
        self.assertEqual(localizer.localize(left, right)["targets"], [])

    def test_effective_skew_uses_estimated_frame_time_with_legacy_config(self):
        config = calibrated_config()
        config["quality"]["maxFrameSkewMs"] = 1000
        self.assertAlmostEqual(spatial_frame_skew_seconds(config), 0.040)
        point = [(0.0, 0.4, 10.0)]
        left = drone_frame(config, "camera1", point, time=10.0)
        right = drone_frame(config, "camera2", point, time=10.2)
        localizer = SpatialLocalizer(config)
        self.assertEqual(localizer.localize(left, right)["state"], "frame_time_gap")
        left.frame_monotonic, right.frame_monotonic = 9.95, 9.99
        result = localizer.localize(left, right)
        self.assertEqual(result["state"], "ready")
        self.assertAlmostEqual(result["diagnostics"]["frameSkewMs"], 40.0)
        config["quality"]["maxFrameSkewMs"] = 20
        self.assertEqual(SpatialLocalizer(config).localize(left, right)["state"], "frame_time_gap")

    def test_inconsistent_geometry_does_not_generate_a_false_location(self):
        config = calibrated_config()
        point = [(0.0, 0.4, 10.0)]
        left = drone_frame(config, "camera1", point)
        right = drone_frame(config, "camera2", point)
        x1, y1, x2, y2 = right.objects[0].bbox
        right.objects[0].bbox = (x1, y1 + 25.0, x2, y2 + 25.0)
        result = SpatialLocalizer(config).localize(left, right)
        self.assertEqual(result["state"], "no_valid_matches")
        self.assertEqual(result["targets"], [])

    def test_larger_boxes_allow_modest_viewpoint_shift_in_the_box_centres(self):
        config = calibrated_config()
        point = [(0.0, 0.4, 10.0)]
        left = drone_frame(config, "camera1", point)
        right = drone_frame(config, "camera2", point)
        x1, y1, x2, y2 = right.objects[0].bbox
        right.objects[0].bbox = (x1, y1 + 10.0, x2, y2 + 10.0)
        localizer = SpatialLocalizer(config)
        self.assertEqual(localizer.localize(left, right)["targets"], [])
        for result in (left, right):
            x1, y1, x2, y2 = result.objects[0].bbox
            result.objects[0].bbox = (x1 - 30.0, y1 - 20.0, x2 + 30.0, y2 + 20.0)
        target, = localizer.localize(left, right)["targets"]
        self.assertAlmostEqual(target["z"], 10.0, delta=0.1)

    def test_multiple_targets_pair_one_to_one_and_ambiguous_matches_are_rejected(self):
        config = calibrated_config()
        points = [(0.2, 0.25, 12.0), (-0.2, 1.4, 12.0)]
        localizer = SpatialLocalizer(config)
        result = localizer.localize(
            drone_frame(config, "camera1", points),
            drone_frame(config, "camera2", points[::-1]),
        )
        pairs = [(target["camera1Id"], target["camera2Id"]) for target in result["targets"]]
        self.assertEqual(pairs, [("camera1-0", "camera2-1"), ("camera1-1", "camera2-0")])

        same_epipolar_line = [(0.2, 0.0, 12.0), (-0.2, 0.0, 12.0)]
        ambiguous = localizer.localize(
            drone_frame(config, "camera1", same_epipolar_line),
            drone_frame(config, "camera2", same_epipolar_line),
        )
        self.assertEqual(ambiguous["diagnostics"]["geometryPairs"], 4)
        self.assertEqual(ambiguous["targets"], [])

    def test_verified_reversed_streams_preserve_logical_ids(self):
        config = calibrated_config()
        rng = np.random.default_rng(22)
        background = cv2.resize(
            rng.integers(30, 170, (180, 320, 3), dtype=np.uint8),
            IMAGE_SIZE, interpolation=cv2.INTER_LINEAR,
        )
        first = np.zeros_like(background)
        first[:, 32:] = background[:, :-32]
        localizer = SpatialLocalizer(config)
        localizer.observe_stream_order(background, first)
        self.assertIs(localizer.stream_order, True)

        point = [(0.5, 0.6, 10.0)]
        from_second = drone_frame(config, "camera2", point, frame=background)
        from_first = drone_frame(config, "camera1", point, frame=first)
        target, = localizer.localize(from_second, from_first)["targets"]
        self.assertEqual(target["camera1Id"], "camera2-0")
        self.assertEqual(target["camera2Id"], "camera1-0")
        np.testing.assert_allclose([target[axis] for axis in "xyz"], point[0], atol=0.001)


if __name__ == "__main__":
    unittest.main()
