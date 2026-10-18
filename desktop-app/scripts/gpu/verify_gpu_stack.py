"""Fail-fast validation for the pinned Yuntong Windows GPU runtime.

This script deliberately performs a real OpenCV CUDA upload/resize/download,
not just an import check. TensorRT validation creates a builder and a network;
it does not compile an engine or write a cache file.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from typing import Any


EXPECTED_DISTRIBUTIONS = {
    "cuda-python": "12.9.7",
    "cuda-bindings": "12.9.7",
    "cuda-pathfinder": "1.8.1",
    "tensorrt-cu12": "11.3.0.99",
    "tensorrt-cu12-bindings": "11.3.0.99",
    "tensorrt-cu12-libs": "11.3.0.99",
}


class VerificationError(RuntimeError):
    """A runtime component is missing, mismatched, or non-functional."""


def _status_code(value: Any) -> int:
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"Unable to interpret CUDA status {value!r}.") from exc


def _check_pinned_distributions() -> dict[str, str]:
    found: dict[str, str] = {}
    for distribution, expected in EXPECTED_DISTRIBUTIONS.items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise VerificationError(f"Missing pinned package: {distribution}=={expected}") from exc
        if actual != expected:
            raise VerificationError(
                f"{distribution} must be {expected}; the active environment has {actual}."
            )
        found[distribution] = actual
    return found


def _decode_cuda_name(value: Any) -> str:
    try:
        raw = bytes(value)
    except (TypeError, ValueError):
        return str(value)
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _check_cuda_python() -> dict[str, Any]:
    try:
        from cuda.bindings import runtime as cudart
    except Exception as exc:  # native-loader failures are important here
        raise VerificationError(f"cuda.bindings.runtime could not be loaded: {exc}") from exc

    count_result = cudart.cudaGetDeviceCount()
    if not isinstance(count_result, tuple) or len(count_result) < 2:
        raise VerificationError(f"Unexpected cudaGetDeviceCount result: {count_result!r}")
    if _status_code(count_result[0]) != 0:
        raise VerificationError(f"cudaGetDeviceCount failed: {count_result[0]!r}")
    device_count = int(count_result[1])
    if device_count < 1:
        raise VerificationError("CUDA reported zero usable devices.")

    properties_result = cudart.cudaGetDeviceProperties(0)
    if not isinstance(properties_result, tuple) or len(properties_result) < 2:
        raise VerificationError(f"Unexpected cudaGetDeviceProperties result: {properties_result!r}")
    if _status_code(properties_result[0]) != 0:
        raise VerificationError(f"cudaGetDeviceProperties failed: {properties_result[0]!r}")
    properties = properties_result[1]
    capability = (int(properties.major), int(properties.minor))
    if capability != (8, 9):
        raise VerificationError(
            f"This deployment is compiled only for SM 8.9, but device 0 is SM {capability[0]}.{capability[1]}."
        )

    return {
        "deviceCount": device_count,
        "device0": _decode_cuda_name(properties.name),
        "computeCapability": f"{capability[0]}.{capability[1]}",
    }


def _check_tensorrt() -> dict[str, Any]:
    try:
        import tensorrt as trt
    except Exception as exc:  # native-loader failures are important here
        raise VerificationError(f"TensorRT could not be loaded: {exc}") from exc

    expected = EXPECTED_DISTRIBUTIONS["tensorrt-cu12-bindings"]
    if str(trt.__version__) != expected:
        raise VerificationError(f"TensorRT must be {expected}; imported {trt.__version__}.")

    logger = trt.Logger(trt.Logger.ERROR)
    builder = trt.Builder(logger)
    if builder is None:
        raise VerificationError("TensorRT failed to create a Builder on the CUDA device.")
    network = builder.create_network(0)
    if network is None:
        raise VerificationError("TensorRT failed to create an explicit network definition.")

    return {
        "version": str(trt.__version__),
        "builder": "ok",
        "precisionContract": "FP32 with TensorRT TF32 permitted; FP16 is not enabled",
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path.resolve()), str(root.resolve()))) == str(root.resolve())
    except ValueError:
        return False


def _check_opencv(expected_install_root: Path) -> dict[str, Any]:
    try:
        import cv2
        import numpy as np
    except Exception as exc:
        raise VerificationError(f"CUDA OpenCV could not be imported: {exc}") from exc

    if not str(cv2.__version__).startswith("5.0.0"):
        raise VerificationError(f"OpenCV 5.0.0 is required; imported {cv2.__version__}.")

    cv2_path = Path(cv2.__file__).resolve()
    if not _inside(cv2_path, expected_install_root):
        raise VerificationError(
            "cv2 was imported from the wrong location. "
            f"Expected a build under '{expected_install_root}', got '{cv2_path}'. "
            "Remove any opencv-python/opencv-contrib-python wheel from the GPU venv."
        )

    build_info = cv2.getBuildInformation()
    import re

    cuda_line = re.search(r"^\s*NVIDIA CUDA:\s+YES\b.*$", build_info, re.MULTILINE)
    if cuda_line is None:
        raise VerificationError("cv2 build information does not report 'NVIDIA CUDA: YES'.")
    if "12.9" not in cuda_line.group(0):
        raise VerificationError(f"OpenCV was not built with CUDA 12.9: {cuda_line.group(0).strip()}")

    architecture_line = re.search(r"^\s*NVIDIA GPU arch:\s+(.+)$", build_info, re.MULTILINE)
    if architecture_line is None or not re.search(r"(?:^|\s)89(?:\s|$)", architecture_line.group(1)):
        raise VerificationError("OpenCV build information does not include the required SM 8.9 / arch 89 target.")

    device_count = int(cv2.cuda.getCudaEnabledDeviceCount())
    if device_count < 1:
        raise VerificationError("cv2.cuda.getCudaEnabledDeviceCount() returned zero.")
    cv2.cuda.setDevice(0)

    host = np.arange(96 * 64 * 3, dtype=np.uint8).reshape((64, 96, 3))
    gpu_mat_type = getattr(cv2.cuda, "GpuMat", None) or getattr(cv2, "cuda_GpuMat", None)
    if gpu_mat_type is None:
        raise VerificationError("The OpenCV Python module does not expose cv2.cuda.GpuMat.")
    gpu_source = gpu_mat_type()
    gpu_source.upload(host)
    # The production preview uses INTER_AREA for downscaling.  CUDA's
    # INTER_LINEAR implementation intentionally has different half-pixel
    # sampling from the CPU path in this OpenCV release, so validating with
    # linear would report a false failure even though the GPU operation is
    # healthy.  Keep the smoke test on the exact production interpolation.
    gpu_resized = cv2.cuda.resize(gpu_source, (48, 32), interpolation=cv2.INTER_AREA)
    downloaded = gpu_resized.download()
    if downloaded.shape != (32, 48, 3):
        raise VerificationError(f"CUDA resize returned the wrong shape: {downloaded.shape!r}")

    expected = cv2.resize(host, (48, 32), interpolation=cv2.INTER_AREA)
    mean_error = float(np.abs(downloaded.astype(np.int16) - expected.astype(np.int16)).mean())
    if mean_error > 1.0:
        raise VerificationError(f"CUDA resize output diverged from CPU resize (mean error {mean_error:.4f}).")

    return {
        "version": str(cv2.__version__),
        "module": str(cv2_path),
        "cudaDevices": device_count,
        "cudaBuild": cuda_line.group(0).strip(),
        "cudaArch": architecture_line.group(1).strip(),
        "resizeSmoke": {"shape": list(downloaded.shape), "meanError": round(mean_error, 6)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tensorrt-only", action="store_true")
    parser.add_argument("--opencv-install-root", type=Path)
    args = parser.parse_args()

    try:
        summary: dict[str, Any] = {
            "python": sys.version.split()[0],
            "distributions": _check_pinned_distributions(),
            "cudaPython": _check_cuda_python(),
            "tensorRT": _check_tensorrt(),
        }
        if not args.tensorrt_only:
            if args.opencv_install_root is None:
                raise VerificationError("--opencv-install-root is required for the full check.")
            summary["openCV"] = _check_opencv(args.opencv_install_root)
    except VerificationError as exc:
        print(f"GPU STACK VERIFICATION FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"GPU STACK VERIFICATION FAILED: unexpected {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("GPU STACK VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
