from __future__ import annotations

import ctypes
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import tensorrt_person_detector as detector_module
from tensorrt_person_detector import (
    EngineFingerprint,
    TensorRTDetectorError,
    TensorRTPersonDetector,
    _AccelerationModules,
    _decode_person_output,
    _load_acceleration_modules,
    engine_cache_paths,
    load_cached_engine,
    make_engine_fingerprint,
    store_cached_engine,
)


CUDA_RUNTIME_VERSION = 12090
WORKSPACE_SIZE_BYTES = 2 * 1024 * 1024 * 1024


def project_work_directory() -> Path:
    directory = Path(__file__).with_name("work")
    directory.mkdir(exist_ok=True)
    return directory


class FingerprintAndCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model_hash = hashlib.sha256(b"model-v1").hexdigest()
        self.fingerprint = make_engine_fingerprint(
            model_sha256=self.model_hash,
            tensorrt_version="11.0.0",
            cuda_runtime_version=CUDA_RUNTIME_VERSION,
            gpu_capability=(8, 9),
            workspace_size_bytes=WORKSPACE_SIZE_BYTES,
        )

    def test_fingerprint_contains_every_engine_compatibility_dimension(self) -> None:
        same = make_engine_fingerprint(
            model_sha256=self.model_hash,
            tensorrt_version="11.0.0",
            cuda_runtime_version=CUDA_RUNTIME_VERSION,
            gpu_capability=(8, 9),
            workspace_size_bytes=WORKSPACE_SIZE_BYTES,
        )
        changes = [
            make_engine_fingerprint(
                model_sha256=hashlib.sha256(b"model-v2").hexdigest(),
                tensorrt_version="11.0.0",
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
            ),
            make_engine_fingerprint(
                model_sha256=self.model_hash,
                tensorrt_version="11.0.1",
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
            ),
            make_engine_fingerprint(
                model_sha256=self.model_hash,
                tensorrt_version="11.0.0",
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(9, 0),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
            ),
            make_engine_fingerprint(
                model_sha256=self.model_hash,
                tensorrt_version="11.0.0",
                cuda_runtime_version=12080,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
            ),
            make_engine_fingerprint(
                model_sha256=self.model_hash,
                tensorrt_version="11.0.0",
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES // 2,
            ),
        ]
        self.assertEqual(self.fingerprint.digest, same.digest)
        self.assertTrue(all(item.digest != self.fingerprint.digest for item in changes))
        self.assertEqual(self.fingerprint.components["inputShape"], [1, 3, 640, 640])
        self.assertEqual(self.fingerprint.components["precision"], "fp32_tf32")
        self.assertEqual(
            self.fingerprint.components["cudaRuntimeVersion"], CUDA_RUNTIME_VERSION
        )
        self.assertEqual(
            self.fingerprint.components["workspaceSizeBytes"], WORKSPACE_SIZE_BYTES
        )

    def test_cache_names_are_generated_and_stay_inside_absolute_cache_root(self) -> None:
        with tempfile.TemporaryDirectory(dir=project_work_directory()) as temporary:
            root = Path(temporary).resolve()
            paths = engine_cache_paths(root, self.fingerprint.digest)
            self.assertEqual(paths.engine.parent, root)
            self.assertEqual(paths.manifest.parent, root)
            self.assertNotIn("model", paths.engine.name)
            self.assertRegex(paths.engine.name, r"^person-fp32-tf32-[0-9a-f]{64}\.plan$")

    def test_relative_or_traversing_cache_paths_are_rejected(self) -> None:
        with self.assertRaises(TensorRTDetectorError) as relative:
            engine_cache_paths(Path("cache"), self.fingerprint.digest)
        self.assertEqual(relative.exception.code, "invalid_cache_directory")
        with tempfile.TemporaryDirectory(dir=project_work_directory()) as temporary:
            traversing = Path(temporary).resolve() / "child" / ".." / "elsewhere"
            with self.assertRaises(TensorRTDetectorError):
                engine_cache_paths(traversing, self.fingerprint.digest)

    def test_atomic_cache_round_trip_has_integrity_manifest_and_no_temp_files(self) -> None:
        engine = b"serialized-tensorrt-plan"
        with tempfile.TemporaryDirectory(dir=project_work_directory()) as temporary:
            root = Path(temporary).resolve()
            paths = store_cached_engine(root, self.fingerprint, engine)
            self.assertEqual(load_cached_engine(root, self.fingerprint), engine)
            manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest["engineSha256"], hashlib.sha256(engine).hexdigest())
            self.assertEqual(manifest["components"], dict(self.fingerprint.components))
            self.assertFalse(any(path.suffix == ".tmp" for path in root.iterdir()))
            manifest_text = paths.manifest.read_text(encoding="utf-8")
            self.assertNotIn(str(root), manifest_text)

    def test_cache_corruption_is_a_safe_miss(self) -> None:
        with tempfile.TemporaryDirectory(dir=project_work_directory()) as temporary:
            root = Path(temporary).resolve()
            paths = store_cached_engine(root, self.fingerprint, b"valid-plan")
            paths.engine.write_bytes(b"tampered-plan")
            self.assertIsNone(load_cached_engine(root, self.fingerprint))
            store_cached_engine(root, self.fingerprint, b"valid-plan")
            paths.manifest.write_text("not json", encoding="utf-8")
            self.assertIsNone(load_cached_engine(root, self.fingerprint))


class DependencyAndErrorBoundaryTests(unittest.TestCase):
    def test_missing_tensor_rt_error_does_not_reveal_import_details(self) -> None:
        def missing_import(_name: str) -> object:
            raise ModuleNotFoundError(r"missing from C:\private\gpu-runtime")

        with self.assertRaises(TensorRTDetectorError) as caught:
            _load_acceleration_modules(missing_import)
        self.assertEqual(caught.exception.code, "tensorrt_unavailable")
        self.assertNotIn("private", str(caught.exception).lower())

    def test_loader_prefers_modern_cuda_bindings_and_supports_legacy_fallback(self) -> None:
        trt = object()
        legacy = object()
        requested: list[str] = []

        def importer(name: str) -> object:
            requested.append(name)
            if name == "tensorrt":
                return trt
            if name == "cuda.bindings.runtime":
                raise ModuleNotFoundError
            if name == "cuda.cudart":
                return legacy
            raise AssertionError(name)

        modules = _load_acceleration_modules(importer)
        self.assertIs(modules.trt, trt)
        self.assertIs(modules.cudart, legacy)
        self.assertEqual(requested, ["tensorrt", "cuda.bindings.runtime", "cuda.cudart"])

    def test_invalid_configuration_is_sanitized_before_gpu_import(self) -> None:
        secret = Path(r"C:\users\private\secret-model.onnx")
        detector = TensorRTPersonDetector(secret, Path("relative-cache"))
        with self.assertRaises(TensorRTDetectorError) as caught:
            detector.load()
        self.assertEqual(caught.exception.code, "invalid_model")
        self.assertNotIn("secret-model", str(caught.exception))
        self.assertEqual(detector.diagnostics()["engineState"], "error")

    def test_fingerprint_rejects_non_fixed_profile(self) -> None:
        with self.assertRaises(TensorRTDetectorError) as caught:
            make_engine_fingerprint(
                model_sha256="a" * 64,
                tensorrt_version="11",
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
                input_shape=(1, 3, 320, 320),
            )
        self.assertEqual(caught.exception.code, "invalid_fingerprint")


class DecoderProtocolTests(unittest.TestCase):
    def test_decode_returns_camera_bridge_compatible_boxes_and_scores(self) -> None:
        output = np.zeros((1, 5, 8400), dtype=np.float32)
        output[0, :, 0] = [200, 200, 100, 100, 0.9]
        output[0, :, 1] = [205, 205, 100, 100, 0.8]
        output[0, :, 2] = [500, 400, 60, 80, 0.7]
        decoded = _decode_person_output(
            output,
            frame_width=640,
            frame_height=640,
            scale=1.0,
            pad_x=0.0,
            pad_y=0.0,
            confidence_threshold=0.25,
            nms_threshold=0.45,
        )
        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[0][0], (150.0, 150.0, 250.0, 250.0))
        self.assertAlmostEqual(decoded[0][1], 0.9, places=5)


class _FakeCudaRuntime:
    class cudaMemcpyKind:
        cudaMemcpyHostToDevice = 1
        cudaMemcpyDeviceToHost = 2

    def __init__(self) -> None:
        self.allocations: dict[int, object] = {}

    def cudaGetDevice(self) -> tuple[int, int]:
        return 0, 0

    def cudaRuntimeGetVersion(self) -> tuple[int, int]:
        return 0, CUDA_RUNTIME_VERSION

    def cudaGetDeviceProperties(self, _device: int) -> tuple[int, object]:
        return 0, type("Properties", (), {"major": 8, "minor": 9})()

    def cudaStreamCreate(self) -> tuple[int, int]:
        return 0, 7

    def _allocate(self, byte_count: int) -> tuple[int, int]:
        allocation = ctypes.create_string_buffer(byte_count)
        pointer = ctypes.addressof(allocation)
        self.allocations[pointer] = allocation
        return 0, pointer

    def cudaMallocHost(self, byte_count: int) -> tuple[int, int]:
        return self._allocate(byte_count)

    def cudaMalloc(self, byte_count: int) -> tuple[int, int]:
        return self._allocate(byte_count)

    def cudaMemcpyAsync(
        self,
        destination: int,
        source: int,
        byte_count: int,
        _kind: int,
        _stream: int,
    ) -> tuple[int]:
        ctypes.memmove(destination, source, byte_count)
        return (0,)

    def cudaStreamSynchronize(self, _stream: int) -> tuple[int]:
        return (0,)

    def cudaFree(self, pointer: int) -> tuple[int]:
        self.allocations.pop(pointer, None)
        return (0,)

    def cudaFreeHost(self, pointer: int) -> tuple[int]:
        self.allocations.pop(pointer, None)
        return (0,)

    def cudaStreamDestroy(self, _stream: int) -> tuple[int]:
        return (0,)


class _FakeContext:
    def __init__(self) -> None:
        self.addresses: dict[str, int] = {}

    def get_tensor_shape(self, name: str) -> tuple[int, ...]:
        return (1, 3, 640, 640) if name == "images" else (1, 5, 8400)

    def set_tensor_address(self, name: str, address: int) -> bool:
        self.addresses[name] = address
        return True

    def execute_async_v3(self, _stream_handle: int) -> bool:
        output = np.zeros((1, 5, 8400), dtype=np.float32)
        output[0, :, 0] = [320, 320, 100, 200, 0.75]
        ctypes.memmove(self.addresses["output0"], output.ctypes.data, output.nbytes)
        return True


class _FakeEngine:
    num_io_tensors = 2

    def __init__(self) -> None:
        self.context = _FakeContext()

    def get_tensor_name(self, index: int) -> str:
        return ("images", "output0")[index]

    def get_tensor_mode(self, name: str) -> str:
        return "input" if name == "images" else "output"

    def get_tensor_dtype(self, _name: str) -> str:
        return "float32"

    def create_execution_context(self) -> _FakeContext:
        return self.context


class _FakeTensorRT:
    __version__ = "11.0.0"

    class Logger:
        ERROR = 1

        def __init__(self, _level: int) -> None:
            pass

    class TensorIOMode:
        INPUT = "input"

    def __init__(self, *, deserialize_failures: int = 0) -> None:
        self.engine = _FakeEngine()
        self.deserialize_failures = deserialize_failures
        self.deserialize_calls = 0
        outer = self

        class Runtime:
            def __init__(self, _logger: object) -> None:
                pass

            def deserialize_cuda_engine(self, _engine_bytes: bytes) -> _FakeEngine | None:
                outer.deserialize_calls += 1
                if outer.deserialize_failures > 0:
                    outer.deserialize_failures -= 1
                    return None
                return outer.engine

        self.Runtime = Runtime

    @staticmethod
    def nptype(_dtype: object) -> type[np.float32]:
        return np.float32

    @staticmethod
    def init_libnvinfer_plugins(_logger: object, _namespace: str) -> bool:
        return True


class FakeRuntimeIntegrationTests(unittest.TestCase):
    def test_cached_engine_preallocates_once_infers_and_releases_without_real_cuda(self) -> None:
        with tempfile.TemporaryDirectory(dir=project_work_directory()) as temporary:
            root = Path(temporary).resolve()
            model = root / "person.onnx"
            model.write_bytes(b"fake-onnx-for-cache-identity")
            cache = root / "engine-cache"
            fake_trt = _FakeTensorRT()
            fake_cuda = _FakeCudaRuntime()
            fingerprint = make_engine_fingerprint(
                model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                tensorrt_version=fake_trt.__version__,
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
            )
            store_cached_engine(cache, fingerprint, b"fake-plan")
            modules = _AccelerationModules(trt=fake_trt, cudart=fake_cuda)
            with patch.object(detector_module, "_load_acceleration_modules", return_value=modules):
                detector = TensorRTPersonDetector(model, cache)
                tensor = np.zeros((1, 3, 640, 640), dtype=np.float32)
                first = detector.infer_raw(tensor)
                allocation_count = len(fake_cuda.allocations)
                second = detector.infer_raw(tensor)
                self.assertEqual(first.shape, (1, 5, 8400))
                self.assertAlmostEqual(float(first[0, 4, 0]), 0.75)
                self.assertTrue(np.array_equal(first, second))
                self.assertEqual(len(fake_cuda.allocations), allocation_count)
                status = detector.diagnostics()
                self.assertEqual(status["provider"], "tensorrt-cuda")
                self.assertEqual(status["precision"], "fp32_tf32")
                self.assertEqual(status["engineState"], "ready")
                self.assertTrue(status["cacheHit"])
                self.assertEqual(status["cudaRuntimeVersion"], CUDA_RUNTIME_VERSION)
                self.assertIsNotNone(status["lastInferenceMs"])
                detector.close()
                self.assertFalse(fake_cuda.allocations)
                self.assertEqual(detector.diagnostics()["engineState"], "closed")

    def test_cached_engine_deserialize_failure_rebuilds_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=project_work_directory()) as temporary:
            root = Path(temporary).resolve()
            model = root / "person.onnx"
            model.write_bytes(b"fake-onnx-for-stale-cache")
            cache = root / "engine-cache"
            fake_trt = _FakeTensorRT(deserialize_failures=1)
            fake_cuda = _FakeCudaRuntime()
            fingerprint = make_engine_fingerprint(
                model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                tensorrt_version=fake_trt.__version__,
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
            )
            store_cached_engine(cache, fingerprint, b"stale-plan")
            modules = _AccelerationModules(trt=fake_trt, cudart=fake_cuda)
            with (
                patch.object(detector_module, "_load_acceleration_modules", return_value=modules),
                patch.object(
                    detector_module,
                    "_build_serialized_engine",
                    return_value=b"rebuilt-plan",
                ) as build_engine,
            ):
                detector = TensorRTPersonDetector(model, cache)
                detector.load()
                self.assertEqual(fake_trt.deserialize_calls, 2)
                build_engine.assert_called_once()
                self.assertFalse(detector.diagnostics()["cacheHit"])
                self.assertEqual(load_cached_engine(cache, fingerprint), b"rebuilt-plan")
                detector.close()

    def test_inference_rejects_wrong_shape_without_running_context(self) -> None:
        with tempfile.TemporaryDirectory(dir=project_work_directory()) as temporary:
            root = Path(temporary).resolve()
            model = root / "person.onnx"
            model.write_bytes(b"fake-onnx")
            cache = root / "engine-cache"
            fake_trt = _FakeTensorRT()
            fake_cuda = _FakeCudaRuntime()
            fingerprint = make_engine_fingerprint(
                model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                tensorrt_version=fake_trt.__version__,
                cuda_runtime_version=CUDA_RUNTIME_VERSION,
                gpu_capability=(8, 9),
                workspace_size_bytes=WORKSPACE_SIZE_BYTES,
            )
            store_cached_engine(cache, fingerprint, b"fake-plan")
            modules = _AccelerationModules(trt=fake_trt, cudart=fake_cuda)
            with patch.object(detector_module, "_load_acceleration_modules", return_value=modules):
                detector = TensorRTPersonDetector(model, cache)
                with self.assertRaises(TensorRTDetectorError) as caught:
                    detector.infer(np.zeros((1, 3, 320, 320), dtype=np.float32))
                self.assertEqual(caught.exception.code, "invalid_input")
                detector.close()


if __name__ == "__main__":
    unittest.main()
