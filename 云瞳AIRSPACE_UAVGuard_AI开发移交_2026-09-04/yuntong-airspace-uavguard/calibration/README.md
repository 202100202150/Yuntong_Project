# Calibration files

Runtime calibration uses OpenCV's world-to-camera convention:

`x_camera = R_world_to_camera * X_ENU + t_world_to_camera`

The projection matrix is `K [R | t]`. ENU means x=east, y=north, z=up,
in metres, relative to one surveyed campus control point.

1. Run `tools/calibrate_intrinsics.py` independently for each locked camera.
2. Survey at least 12 control points distributed across the common view.
3. Run `tools/calibrate_extrinsics.py` with a CSV for each camera.
4. Keep at least 20% of points out of the solve and confirm holdout reprojection
   error is at most 2 px.
5. Set `valid: true` only after the report is reviewed and archive the exact
   camera configuration with the calibration version.

`site-template.yaml` is intentionally invalid. The service refuses to start
with it so placeholder poses cannot produce plausible-looking false coordinates.
