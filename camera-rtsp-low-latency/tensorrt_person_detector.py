"""TensorRT FP32/TF32 backend for the fixed-shape person detector.

The module deliberately imports neither TensorRT nor CUDA at import time.  The
production protocol is intentionally small and matches ``camera_bridge``:

* ``load()`` builds or loads an engine and allocates all buffers once.
* ``detect(bgr_frame)`` returns ``[((x1, y1, x2, y2), confidence), ...]``.
* ``infer(input_tensor)`` exposes named NumPy outputs for integration tests or
  a future model-specific decoder.
* ``close()`` releases the CUDA stream and all host/device allocations.

There is no CPU fallback.  A missing/incompatible GPU stack raises a sanitized
``TensorRTDetectorError`` so production cannot silently become CPU-bound.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Callable, Mapping

import numpy as np


INPUT_SHAPE = (1, 3, 640, 640)
PRECISION = "fp32_tf32"
PROVIDER = "tensorrt-cuda"
CACHE_SCHEMA_VERSION = 2
_CACHE_PREFIX = "person-fp32-tf32"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_ENGINE_BYTES = 2 * 1024 * 1024 * 1024


class TensorRTDetectorError(RuntimeError):
    """Safe, stable error intended to cross the local sidecar boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.public_message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class EngineFingerprint:
    digest: str
    components: Mapping[str, Any]


@dataclass(frozen=True)
class EngineCachePaths:
    engine: Path
    manifest: Path


@dataclass(frozen=True)
class _AccelerationModules:
    trt: Any
    cudart: Any


@dataclass
class _TensorBuffer:
    name: str
    shape: tuple[int, ...]
    dtype: np.dtype[Any]
    is_input: bool
    host_pointer: int
    host_owner: Any
    host: np.ndarray
    device_pointer: int


def compute_model_sha256(model_path: Path | str) -> str:
    """Hash an ONNX file without exposing its configured path on failure."""

    path = Path(model_path)
    try:
        digest = hashlib.sha256()
        with path.open("rb") as model_file:
            while chunk := model_file.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        raise TensorRTDetectorError(
            "model_unreadable", "Unable to read the configured ONNX model."
        ) from None


def make_engine_fingerprint(
    *,
    model_sha256: str,
    tensorrt_version: str,
    cuda_runtime_version: int,
    gpu_capability: tuple[int, int],
    workspace_size_bytes: int,
    input_shape: tuple[int, ...] = INPUT_SHAPE,
    precision: str = PRECISION,
) -> EngineFingerprint:
    """Create the complete, deterministic TensorRT plan-cache identity."""

    if not _HEX_64.fullmatch(model_sha256):
        raise TensorRTDetectorError("invalid_fingerprint", "The model fingerprint is invalid.")
    if not tensorrt_version or len(tensorrt_version) > 128:
        raise TensorRTDetectorError("invalid_fingerprint", "The TensorRT version is invalid.")
    if (
        isinstance(cuda_runtime_version, bool)
        or not isinstance(cuda_runtime_version, int)
        or cuda_runtime_version < 1
    ):
        raise TensorRTDetectorError("invalid_fingerprint", "The CUDA runtime version is invalid.")
    if (
        len(gpu_capability) != 2
        or any(not isinstance(part, int) or part < 0 or part > 99 for part in gpu_capability)
    ):
        raise TensorRTDetectorError("invalid_fingerprint", "The GPU capability is invalid.")
    if tuple(input_shape) != INPUT_SHAPE or precision != PRECISION:
        raise TensorRTDetectorError(
            "invalid_fingerprint",
            "Only the fixed 1x3x640x640 FP32/TF32 profile is supported.",
        )
    if (
        isinstance(workspace_size_bytes, bool)
        or not isinstance(workspace_size_bytes, int)
        or workspace_size_bytes < 64 * 1024 * 1024
    ):
        raise TensorRTDetectorError("invalid_fingerprint", "The TensorRT workspace is invalid.")
    components: dict[str, Any] = {
        "schema": CACHE_SCHEMA_VERSION,
        "modelSha256": model_sha256,
        "tensorrtVersion": str(tensorrt_version),
        "cudaRuntimeVersion": cuda_runtime_version,
        "gpuCapability": [gpu_capability[0], gpu_capability[1]],
        "workspaceSizeBytes": workspace_size_bytes,
        "inputShape": list(INPUT_SHAPE),
        "precision": PRECISION,
    }
    canonical = json.dumps(components, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return EngineFingerprint(hashlib.sha256(canonical).hexdigest(), components)


def engine_cache_paths(cache_dir: Path | str, fingerprint: str) -> EngineCachePaths:
    """Return generated cache paths that cannot be influenced by model names."""

    directory = Path(cache_dir)
    if not directory.is_absolute() or ".." in directory.parts:
        raise TensorRTDetectorError(
            "invalid_cache_directory", "The TensorRT cache directory must be an absolute path."
        )
    if not _HEX_64.fullmatch(fingerprint):
        raise TensorRTDetectorError("invalid_fingerprint", "The engine fingerprint is invalid.")
    try:
        root = directory.resolve(strict=False)
    except OSError:
        raise TensorRTDetectorError(
            "invalid_cache_directory", "The TensorRT cache directory is unavailable."
        ) from None
    basename = f"{_CACHE_PREFIX}-{fingerprint}"
    return EngineCachePaths(root / f"{basename}.plan", root / f"{basename}.manifest.json")


def _write_temporary_file(directory: Path, prefix: str, payload: bytes) -> Path:
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
        with os.fdopen(descriptor, "wb") as temporary:
            descriptor = -1
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        return Path(temporary_name)
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise TensorRTDetectorError(
            "cache_write_failed", "Unable to write the TensorRT engine cache."
        ) from None


def store_cached_engine(
    cache_dir: Path | str,
    fingerprint: EngineFingerprint,
    engine_bytes: bytes,
) -> EngineCachePaths:
    """Atomically publish a plan and its path-free integrity manifest."""

    if not engine_bytes or len(engine_bytes) > _MAX_ENGINE_BYTES:
        raise TensorRTDetectorError("invalid_engine", "The generated TensorRT engine is invalid.")
    paths = engine_cache_paths(cache_dir, fingerprint.digest)
    try:
        paths.engine.parent.mkdir(parents=True, exist_ok=True)
        if not paths.engine.parent.is_dir():
            raise OSError
    except OSError:
        raise TensorRTDetectorError(
            "cache_write_failed", "Unable to prepare the TensorRT engine cache."
        ) from None

    engine_digest = hashlib.sha256(engine_bytes).hexdigest()
    manifest = {
        "schema": CACHE_SCHEMA_VERSION,
        "fingerprint": fingerprint.digest,
        "components": dict(fingerprint.components),
        "engineBytes": len(engine_bytes),
        "engineSha256": engine_digest,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    engine_temp: Path | None = None
    manifest_temp: Path | None = None
    try:
        engine_temp = _write_temporary_file(paths.engine.parent, ".engine-", engine_bytes)
        manifest_temp = _write_temporary_file(paths.engine.parent, ".manifest-", manifest_bytes)
        os.replace(engine_temp, paths.engine)
        engine_temp = None
        os.replace(manifest_temp, paths.manifest)
        manifest_temp = None
    except TensorRTDetectorError:
        raise
    except OSError:
        raise TensorRTDetectorError(
            "cache_write_failed", "Unable to publish the TensorRT engine cache."
        ) from None
    finally:
        for temporary in (engine_temp, manifest_temp):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return paths


def load_cached_engine(
    cache_dir: Path | str,
    fingerprint: EngineFingerprint,
) -> bytes | None:
    """Return a verified cached plan, treating any corruption as a cache miss."""

    paths = engine_cache_paths(cache_dir, fingerprint.digest)
    try:
        if not paths.engine.is_file() or not paths.manifest.is_file():
            return None
        engine_size = paths.engine.stat().st_size
        if engine_size < 1 or engine_size > _MAX_ENGINE_BYTES:
            return None
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return None
        if manifest.get("schema") != CACHE_SCHEMA_VERSION:
            return None
        if manifest.get("fingerprint") != fingerprint.digest:
            return None
        if manifest.get("components") != dict(fingerprint.components):
            return None
        if manifest.get("engineBytes") != engine_size:
            return None
        expected_digest = manifest.get("engineSha256")
        if not isinstance(expected_digest, str) or not _HEX_64.fullmatch(expected_digest):
            return None
        engine_bytes = paths.engine.read_bytes()
        if hashlib.sha256(engine_bytes).hexdigest() != expected_digest:
            return None
        return engine_bytes
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _load_acceleration_modules(
    importer: Callable[[str], Any] = importlib.import_module,
) -> _AccelerationModules:
    try:
        trt = importer("tensorrt")
    except (ImportError, ModuleNotFoundError, OSError):
        raise TensorRTDetectorError(
            "tensorrt_unavailable", "TensorRT is not available in the GPU runtime."
        ) from None
    cudart: Any | None = None
    for module_name in ("cuda.bindings.runtime", "cuda.cudart"):
        try:
            cudart = importer(module_name)
            break
        except (ImportError, ModuleNotFoundError, OSError):
            continue
    if cudart is None:
        raise TensorRTDetectorError(
            "cuda_runtime_unavailable", "CUDA Python runtime bindings are not available."
        ) from None
    return _AccelerationModules(trt=trt, cudart=cudart)


def _status_code(status: Any) -> int:
    try:
        return int(status)
    except (TypeError, ValueError):
        value = getattr(status, "value", None)
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1


def _cuda_call(cudart: Any, function_name: str, *arguments: Any) -> tuple[Any, ...]:
    function = getattr(cudart, function_name, None)
    if not callable(function):
        raise TensorRTDetectorError(
            "cuda_api_incompatible", "The installed CUDA Python runtime is incompatible."
        )
    try:
        result = function(*arguments)
    except Exception:
        raise TensorRTDetectorError(
            "cuda_operation_failed", "A CUDA runtime operation failed."
        ) from None
    if not isinstance(result, tuple):
        result = (result,)
    if not result or _status_code(result[0]) != 0:
        raise TensorRTDetectorError(
            "cuda_operation_failed", "A CUDA runtime operation failed."
        )
    return tuple(result[1:])


def _pointer_value(pointer: Any) -> int:
    try:
        value = int(pointer)
    except (TypeError, ValueError):
        value = int(getattr(pointer, "value", 0) or 0)
    if value <= 0:
        raise TensorRTDetectorError(
            "cuda_operation_failed", "CUDA returned an invalid memory or stream handle."
        )
    return value


def _gpu_compute_capability(cudart: Any) -> tuple[int, int]:
    device_result = _cuda_call(cudart, "cudaGetDevice")
    if len(device_result) != 1:
        raise TensorRTDetectorError("cuda_api_incompatible", "CUDA returned invalid device data.")
    device = int(device_result[0])
    properties_result = _cuda_call(cudart, "cudaGetDeviceProperties", device)
    if len(properties_result) == 1:
        properties = properties_result[0]
        major = getattr(properties, "major", None)
        minor = getattr(properties, "minor", None)
        if isinstance(major, int) and isinstance(minor, int):
            return major, minor

    attribute_enum = getattr(cudart, "cudaDeviceAttr", None)
    if attribute_enum is None:
        raise TensorRTDetectorError(
            "cuda_api_incompatible", "CUDA did not expose GPU capability information."
        )
    try:
        major_attribute = attribute_enum.cudaDevAttrComputeCapabilityMajor
        minor_attribute = attribute_enum.cudaDevAttrComputeCapabilityMinor
    except AttributeError:
        raise TensorRTDetectorError(
            "cuda_api_incompatible", "CUDA did not expose GPU capability information."
        ) from None
    major_result = _cuda_call(cudart, "cudaDeviceGetAttribute", major_attribute, device)
    minor_result = _cuda_call(cudart, "cudaDeviceGetAttribute", minor_attribute, device)
    if len(major_result) != 1 or len(minor_result) != 1:
        raise TensorRTDetectorError(
            "cuda_api_incompatible", "CUDA returned invalid GPU capability information."
        )
    return int(major_result[0]), int(minor_result[0])


def _cuda_runtime_version(cudart: Any) -> int:
    version_result = _cuda_call(cudart, "cudaRuntimeGetVersion")
    if len(version_result) != 1:
        raise TensorRTDetectorError(
            "cuda_api_incompatible", "CUDA returned invalid runtime version data."
        )
    try:
        version = int(version_result[0])
    except (TypeError, ValueError):
        version = 0
    if version < 1:
        raise TensorRTDetectorError(
            "cuda_api_incompatible", "CUDA returned invalid runtime version data."
        )
    return version


def _cuda_memcpy_kind(cudart: Any, name: str) -> Any:
    kinds = getattr(cudart, "cudaMemcpyKind", None)
    value = getattr(kinds, name, None) if kinds is not None else None
    if value is None:
        raise TensorRTDetectorError(
            "cuda_api_incompatible", "The installed CUDA Python runtime is incompatible."
        )
    return value


def _build_serialized_engine(
    trt: Any,
    onnx_bytes: bytes,
    *,
    workspace_size_bytes: int,
) -> bytes:
    """Build one fixed-shape FP32 plan, allowing TensorRT's default TF32 tactics."""

    try:
        logger = trt.Logger(trt.Logger.ERROR)
        builder = trt.Builder(logger)
        explicit_batch = getattr(getattr(trt, "NetworkDefinitionCreationFlag", None), "EXPLICIT_BATCH", None)
        flags = 0 if explicit_batch is None else 1 << int(explicit_batch)
        network = builder.create_network(flags)
        parser = trt.OnnxParser(network, logger)
        if not parser.parse(onnx_bytes):
            raise TensorRTDetectorError(
                "onnx_parse_failed", "TensorRT could not parse the configured ONNX model."
            )
        if int(network.num_inputs) != 1 or int(network.num_outputs) != 1:
            raise TensorRTDetectorError(
                "unsupported_model", "The TensorRT backend requires one input and one output."
            )
        input_tensor = network.get_input(0)
        if tuple(int(dimension) for dimension in input_tensor.shape) != INPUT_SHAPE:
            raise TensorRTDetectorError(
                "unsupported_model", "The ONNX model must use a fixed 1x3x640x640 input."
            )
        config = builder.create_builder_config()
        set_pool_limit = getattr(config, "set_memory_pool_limit", None)
        if not callable(set_pool_limit):
            raise TensorRTDetectorError(
                "tensorrt_api_incompatible", "TensorRT does not expose the required builder API."
            )
        set_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_size_bytes)
        serialized = builder.build_serialized_network(network, config)
        if serialized is None:
            raise TensorRTDetectorError(
                "engine_build_failed", "TensorRT failed to build the FP32/TF32 engine."
            )
        engine_bytes = bytes(serialized)
        if not engine_bytes:
            raise TensorRTDetectorError(
                "engine_build_failed", "TensorRT produced an empty FP32/TF32 engine."
            )
        return engine_bytes
    except TensorRTDetectorError:
        raise
    except Exception:
        raise TensorRTDetectorError(
            "engine_build_failed", "TensorRT failed to build the FP32/TF32 engine."
        ) from None


def _bbox_iou(
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


def _decode_person_output(
    output: np.ndarray,
    *,
    frame_width: int,
    frame_height: int,
    scale: float,
    pad_x: float,
    pad_y: float,
    confidence_threshold: float,
    nms_threshold: float,
) -> list[tuple[tuple[float, float, float, float], float]]:
    tensor = np.asarray(output)
    if tensor.ndim == 3 and tensor.shape[0] == 1:
        tensor = tensor[0]
    if tensor.ndim != 2:
        raise TensorRTDetectorError(
            "unsupported_output", "The TensorRT model returned an unsupported output tensor."
        )
    if tensor.shape[0] == 5:
        rows = tensor.T
    elif tensor.shape[1] == 5:
        rows = tensor
    else:
        raise TensorRTDetectorError(
            "unsupported_output", "The TensorRT model returned an unsupported person tensor."
        )
    candidates = rows[rows[:, 4] >= confidence_threshold]
    boxes: list[tuple[float, float, float, float]] = []
    scores: list[float] = []
    for cx, cy, width, height, confidence in candidates:
        x1 = max(0.0, min(float(frame_width), (float(cx) - float(width) / 2 - pad_x) / scale))
        y1 = max(0.0, min(float(frame_height), (float(cy) - float(height) / 2 - pad_y) / scale))
        x2 = max(0.0, min(float(frame_width), (float(cx) + float(width) / 2 - pad_x) / scale))
        y2 = max(0.0, min(float(frame_height), (float(cy) + float(height) / 2 - pad_y) / scale))
        if x2 - x1 >= 1.0 and y2 - y1 >= 1.0:
            boxes.append((x1, y1, x2, y2))
            scores.append(float(confidence))
    order = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
    kept: list[int] = []
    while order:
        selected = order.pop(0)
        kept.append(selected)
        order = [
            candidate
            for candidate in order
            if _bbox_iou(boxes[selected], boxes[candidate]) <= nms_threshold
        ]
    return [(boxes[index], scores[index]) for index in kept]


class TensorRTPersonDetector:
    """Fixed 640x640 TensorRT FP32/TF32 detector with one reusable CUDA stream."""

    def __init__(
        self,
        model_path: Path | str,
        cache_dir: Path | str,
        *,
        confidence_threshold: float = 0.25,
        nms_threshold: float = 0.45,
        workspace_size_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        self.model_path = Path(model_path)
        self.cache_dir = Path(cache_dir)
        self.confidence_threshold = float(confidence_threshold)
        self.nms_threshold = float(nms_threshold)
        self.workspace_size_bytes = int(workspace_size_bytes)
        self._lock = threading.RLock()
        self._state = "unloaded"
        self._error_code: str | None = None
        self._cache_hit: bool | None = None
        self._last_inference_ms: float | None = None
        self._trt_version: str | None = None
        self._cuda_runtime_version_value: int | None = None
        self._gpu_capability_value: tuple[int, int] | None = None
        self._modules: _AccelerationModules | None = None
        self._runtime: Any | None = None
        self._engine: Any | None = None
        self._context: Any | None = None
        self._stream: Any | None = None
        self._stream_handle: int | None = None
        self._buffers: dict[str, _TensorBuffer] = {}
        self._input_name: str | None = None
        self._output_names: tuple[str, ...] = ()

    def _validate_configuration(self) -> None:
        if (
            not self.model_path.is_absolute()
            or self.model_path.suffix.lower() != ".onnx"
            or not self.model_path.is_file()
        ):
            raise TensorRTDetectorError(
                "invalid_model", "A readable absolute ONNX model path is required."
            )
        engine_cache_paths(self.cache_dir, "0" * 64)
        if not 0.0 <= self.confidence_threshold <= 1.0 or not 0.0 <= self.nms_threshold <= 1.0:
            raise TensorRTDetectorError(
                "invalid_threshold", "Detection thresholds must be between zero and one."
            )
        if self.workspace_size_bytes < 64 * 1024 * 1024:
            raise TensorRTDetectorError(
                "invalid_workspace", "TensorRT workspace must be at least 64 MiB."
            )

    def load(self) -> None:
        with self._lock:
            if self._state == "ready":
                return
            if self._state == "closed":
                raise TensorRTDetectorError("detector_closed", "The TensorRT detector is closed.")
            self._state = "loading"
            self._error_code = None
            try:
                self._validate_configuration()
                model_sha256 = compute_model_sha256(self.model_path)
                modules = _load_acceleration_modules()
                trt_version = str(getattr(modules.trt, "__version__", ""))
                if not trt_version:
                    raise TensorRTDetectorError(
                        "tensorrt_api_incompatible", "TensorRT did not report its runtime version."
                    )
                cuda_runtime_version = _cuda_runtime_version(modules.cudart)
                gpu_capability = _gpu_compute_capability(modules.cudart)
                fingerprint = make_engine_fingerprint(
                    model_sha256=model_sha256,
                    tensorrt_version=trt_version,
                    cuda_runtime_version=cuda_runtime_version,
                    gpu_capability=gpu_capability,
                    workspace_size_bytes=self.workspace_size_bytes,
                )
                engine_bytes = load_cached_engine(self.cache_dir, fingerprint)
                self._cache_hit = engine_bytes is not None
                if engine_bytes is None:
                    self._state = "building"
                    engine_bytes = self._build_engine(modules.trt)
                    store_cached_engine(self.cache_dir, fingerprint, engine_bytes)
                # Keep the CUDA module reference before allocating any runtime
                # resources.  ``_initialize_runtime`` may fail part way through
                # deserialisation or buffer allocation; publishing the module
                # reference up front lets the common error path release every
                # stream/device/host allocation instead of leaking them.
                self._modules = modules
                try:
                    self._initialize_runtime(modules, engine_bytes)
                except TensorRTDetectorError as error:
                    if not self._cache_hit or error.code != "engine_deserialize_failed":
                        raise
                    # A plan can be intact on disk yet incompatible with the
                    # active runtime. Rebuild exactly once, then fail closed if
                    # the freshly generated plan also cannot be loaded.
                    self._release_resources()
                    self._state = "building"
                    engine_bytes = self._build_engine(modules.trt)
                    store_cached_engine(self.cache_dir, fingerprint, engine_bytes)
                    self._cache_hit = False
                    self._initialize_runtime(modules, engine_bytes)
                self._trt_version = trt_version
                self._cuda_runtime_version_value = cuda_runtime_version
                self._gpu_capability_value = gpu_capability
                self._state = "ready"
            except TensorRTDetectorError as error:
                self._release_resources()
                self._state = "error"
                self._error_code = error.code
                raise
            except Exception:
                self._release_resources()
                self._state = "error"
                self._error_code = "tensorrt_initialization_failed"
                raise TensorRTDetectorError(
                    "tensorrt_initialization_failed", "TensorRT initialization failed."
                ) from None

    def _build_engine(self, trt: Any) -> bytes:
        try:
            onnx_bytes = self.model_path.read_bytes()
        except OSError:
            raise TensorRTDetectorError(
                "model_unreadable", "Unable to read the configured ONNX model."
            ) from None
        return _build_serialized_engine(
            trt,
            onnx_bytes,
            workspace_size_bytes=self.workspace_size_bytes,
        )

    def _initialize_runtime(self, modules: _AccelerationModules, engine_bytes: bytes) -> None:
        try:
            logger = modules.trt.Logger(modules.trt.Logger.ERROR)
            initialize_plugins = getattr(modules.trt, "init_libnvinfer_plugins", None)
            if callable(initialize_plugins):
                initialize_plugins(logger, "")
            runtime = modules.trt.Runtime(logger)
            engine = runtime.deserialize_cuda_engine(engine_bytes)
            if engine is None:
                raise TensorRTDetectorError(
                    "engine_deserialize_failed",
                    "TensorRT could not deserialize the cached or generated engine.",
                )
            context = engine.create_execution_context()
            if context is None:
                raise TensorRTDetectorError(
                    "engine_load_failed", "TensorRT could not create an execution context."
                )
            self._runtime = runtime
            self._engine = engine
            self._context = context
            self._allocate_buffers(modules, engine, context)
        except TensorRTDetectorError:
            raise
        except Exception:
            raise TensorRTDetectorError(
                "engine_load_failed", "TensorRT could not initialize the FP32/TF32 engine."
            ) from None

    def _allocate_buffers(self, modules: _AccelerationModules, engine: Any, context: Any) -> None:
        trt = modules.trt
        cudart = modules.cudart
        if not hasattr(engine, "num_io_tensors") or not hasattr(context, "execute_async_v3"):
            raise TensorRTDetectorError(
                "tensorrt_api_incompatible", "TensorRT 10 or newer tensor APIs are required."
            )
        stream_result = _cuda_call(cudart, "cudaStreamCreate")
        if len(stream_result) != 1:
            raise TensorRTDetectorError("cuda_api_incompatible", "CUDA returned invalid stream data.")
        stream = stream_result[0]
        stream_handle = _pointer_value(stream)
        allocated: dict[str, _TensorBuffer] = {}
        try:
            tensor_specs: list[tuple[str, bool, tuple[int, ...], np.dtype[Any]]] = []
            input_names: list[str] = []
            output_names: list[str] = []
            for index in range(int(engine.num_io_tensors)):
                name = str(engine.get_tensor_name(index))
                mode = engine.get_tensor_mode(name)
                is_input = mode == trt.TensorIOMode.INPUT
                shape = tuple(int(dimension) for dimension in context.get_tensor_shape(name))
                if any(dimension < 1 for dimension in shape):
                    raise TensorRTDetectorError(
                        "unsupported_engine", "The TensorRT engine contains unresolved tensor shapes."
                    )
                try:
                    dtype = np.dtype(trt.nptype(engine.get_tensor_dtype(name)))
                except Exception:
                    raise TensorRTDetectorError(
                        "unsupported_engine", "The TensorRT engine uses an unsupported tensor type."
                    ) from None
                tensor_specs.append((name, is_input, shape, dtype))
                (input_names if is_input else output_names).append(name)
            if len(input_names) != 1 or len(output_names) != 1:
                raise TensorRTDetectorError(
                    "unsupported_engine", "The TensorRT engine must have one input and one output."
                )
            input_spec = next(spec for spec in tensor_specs if spec[0] == input_names[0])
            if input_spec[2] != INPUT_SHAPE:
                raise TensorRTDetectorError(
                    "unsupported_engine", "The TensorRT engine input shape is not 1x3x640x640."
                )
            for name, is_input, shape, dtype in tensor_specs:
                element_count = int(np.prod(shape, dtype=np.int64))
                byte_count = element_count * dtype.itemsize
                if byte_count < 1:
                    raise TensorRTDetectorError(
                        "unsupported_engine", "The TensorRT engine contains an empty tensor."
                    )
                host_result = _cuda_call(cudart, "cudaMallocHost", byte_count)
                if len(host_result) != 1:
                    raise TensorRTDetectorError(
                        "cuda_api_incompatible", "CUDA returned invalid host allocation data."
                    )
                host_pointer = _pointer_value(host_result[0])
                try:
                    host_owner = (ctypes.c_ubyte * byte_count).from_address(host_pointer)
                    host = np.ctypeslib.as_array(host_owner).view(dtype).reshape(shape)
                    device_result = _cuda_call(cudart, "cudaMalloc", byte_count)
                    if len(device_result) != 1:
                        raise TensorRTDetectorError(
                            "cuda_api_incompatible", "CUDA returned invalid device allocation data."
                        )
                    device_pointer = _pointer_value(device_result[0])
                except Exception:
                    try:
                        _cuda_call(cudart, "cudaFreeHost", host_pointer)
                    except TensorRTDetectorError:
                        pass
                    raise
                buffer = _TensorBuffer(
                    name=name,
                    shape=shape,
                    dtype=dtype,
                    is_input=is_input,
                    host_pointer=host_pointer,
                    host_owner=host_owner,
                    host=host,
                    device_pointer=device_pointer,
                )
                allocated[name] = buffer
                if context.set_tensor_address(name, device_pointer) is False:
                    raise TensorRTDetectorError(
                        "engine_load_failed", "TensorRT rejected a preallocated tensor buffer."
                    )
            self._stream = stream
            self._stream_handle = stream_handle
            self._buffers = allocated
            self._input_name = input_names[0]
            self._output_names = tuple(output_names)
        except Exception:
            for buffer in allocated.values():
                try:
                    _cuda_call(cudart, "cudaFree", buffer.device_pointer)
                except TensorRTDetectorError:
                    pass
                try:
                    _cuda_call(cudart, "cudaFreeHost", buffer.host_pointer)
                except TensorRTDetectorError:
                    pass
            try:
                _cuda_call(cudart, "cudaStreamDestroy", stream)
            except TensorRTDetectorError:
                pass
            raise

    def infer(self, input_tensor: np.ndarray) -> dict[str, np.ndarray]:
        """Run one NCHW tensor and return copies of all named outputs."""

        with self._lock:
            self.load()
            if (
                self._modules is None
                or self._context is None
                or self._stream_handle is None
                or self._input_name is None
            ):
                raise TensorRTDetectorError(
                    "detector_not_ready", "The TensorRT detector is not ready."
                )
            input_buffer = self._buffers[self._input_name]
            source = np.asarray(input_tensor)
            if tuple(source.shape) != input_buffer.shape:
                raise TensorRTDetectorError(
                    "invalid_input", "The detector input must have shape 1x3x640x640."
                )
            started = time.perf_counter()
            try:
                np.copyto(input_buffer.host, source, casting="unsafe")
                cudart = self._modules.cudart
                host_to_device = _cuda_memcpy_kind(cudart, "cudaMemcpyHostToDevice")
                device_to_host = _cuda_memcpy_kind(cudart, "cudaMemcpyDeviceToHost")
                _cuda_call(
                    cudart,
                    "cudaMemcpyAsync",
                    input_buffer.device_pointer,
                    input_buffer.host_pointer,
                    input_buffer.host.nbytes,
                    host_to_device,
                    self._stream,
                )
                if not bool(self._context.execute_async_v3(self._stream_handle)):
                    raise TensorRTDetectorError(
                        "inference_failed", "TensorRT rejected an inference request."
                    )
                for output_name in self._output_names:
                    output_buffer = self._buffers[output_name]
                    _cuda_call(
                        cudart,
                        "cudaMemcpyAsync",
                        output_buffer.host_pointer,
                        output_buffer.device_pointer,
                        output_buffer.host.nbytes,
                        device_to_host,
                        self._stream,
                    )
                _cuda_call(cudart, "cudaStreamSynchronize", self._stream)
                outputs = {
                    name: np.array(self._buffers[name].host, copy=True) for name in self._output_names
                }
                self._last_inference_ms = (time.perf_counter() - started) * 1000.0
                return outputs
            except TensorRTDetectorError:
                raise
            except Exception:
                raise TensorRTDetectorError(
                    "inference_failed", "TensorRT inference failed."
                ) from None

    def infer_raw(self, input_tensor: np.ndarray) -> np.ndarray:
        """Return the sole model output in the shape needed by the YOLO decoder."""

        outputs = self.infer(input_tensor)
        if len(outputs) != 1:
            raise TensorRTDetectorError(
                "unsupported_output", "The TensorRT model returned multiple output tensors."
            )
        return next(iter(outputs.values()))

    def detect(
        self, image: np.ndarray
    ) -> list[tuple[tuple[float, float, float, float], float]]:
        """Detect people in a BGR uint8 frame using the bridge-compatible result format."""

        frame = np.asarray(image)
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.shape[0] < 1 or frame.shape[1] < 1:
            raise TensorRTDetectorError("invalid_frame", "A non-empty three-channel frame is required.")
        try:
            cv2 = importlib.import_module("cv2")
            height, width = frame.shape[:2]
            scale = min(640 / width, 640 / height)
            resized_width = max(1, round(width * scale))
            resized_height = max(1, round(height * scale))
            resized = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
            pad_x = round((640 - resized_width) / 2.0 - 0.1)
            pad_y = round((640 - resized_height) / 2.0 - 0.1)
            letterboxed = np.full((640, 640, 3), 114, dtype=np.uint8)
            letterboxed[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width] = resized
            tensor = np.ascontiguousarray(
                letterboxed[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32
            )
            tensor *= 1.0 / 255.0
            output = self.infer_raw(tensor)
            return _decode_person_output(
                output,
                frame_width=width,
                frame_height=height,
                scale=scale,
                pad_x=float(pad_x),
                pad_y=float(pad_y),
                confidence_threshold=self.confidence_threshold,
                nms_threshold=self.nms_threshold,
            )
        except TensorRTDetectorError:
            raise
        except (ImportError, ModuleNotFoundError):
            raise TensorRTDetectorError(
                "opencv_unavailable", "OpenCV preprocessing is unavailable."
            ) from None
        except Exception:
            raise TensorRTDetectorError(
                "preprocessing_failed", "Frame preprocessing failed."
            ) from None

    def diagnostics(self) -> dict[str, Any]:
        """Return path-free status suitable for the authenticated local API."""

        with self._lock:
            output_shapes = {
                name: list(self._buffers[name].shape)
                for name in self._output_names
                if name in self._buffers
            }
            return {
                "provider": PROVIDER,
                "precision": PRECISION,
                "engineState": self._state,
                "cacheHit": self._cache_hit,
                "inputShape": list(INPUT_SHAPE),
                "outputShapes": output_shapes,
                "tensorrtVersion": self._trt_version,
                "cudaRuntimeVersion": self._cuda_runtime_version_value,
                "gpuCapability": (
                    list(self._gpu_capability_value)
                    if self._gpu_capability_value is not None
                    else None
                ),
                "lastInferenceMs": (
                    round(self._last_inference_ms, 3)
                    if self._last_inference_ms is not None
                    else None
                ),
                "errorCode": self._error_code,
            }

    def _release_resources(self) -> None:
        modules = self._modules
        if modules is not None:
            if self._context is not None:
                for tensor_name in self._buffers:
                    try:
                        self._context.set_tensor_address(tensor_name, 0)
                    except Exception:
                        pass
            for buffer in self._buffers.values():
                try:
                    _cuda_call(modules.cudart, "cudaFree", buffer.device_pointer)
                except TensorRTDetectorError:
                    pass
                try:
                    _cuda_call(modules.cudart, "cudaFreeHost", buffer.host_pointer)
                except TensorRTDetectorError:
                    pass
            if self._stream is not None:
                try:
                    _cuda_call(modules.cudart, "cudaStreamDestroy", self._stream)
                except TensorRTDetectorError:
                    pass
        self._buffers.clear()
        self._stream = None
        self._stream_handle = None
        self._input_name = None
        self._output_names = ()
        self._context = None
        self._engine = None
        self._runtime = None
        self._modules = None

    def close(self) -> None:
        with self._lock:
            if self._state == "closed":
                return
            self._release_resources()
            self._state = "closed"

    def __enter__(self) -> "TensorRTPersonDetector":
        self.load()
        return self

    def __exit__(self, _exception_type: Any, _exception: Any, _traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
