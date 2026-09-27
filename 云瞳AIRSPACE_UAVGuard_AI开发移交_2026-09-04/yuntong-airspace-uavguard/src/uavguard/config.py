from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-(.*?))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"Required environment variable is not set: {name}")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


@dataclass(slots=True)
class CameraConfig:
    camera_id: str
    source: str
    calibration: Path
    enabled: bool = True
    source_type: str = "auto"
    fps: float = 25.0
    gstreamer_pipeline: str | None = None
    mask_polygons: list[list[list[float]]] = field(default_factory=list)
    record: bool = False
    timestamp_offset_ms: float = 0.0


@dataclass(slots=True)
class MotionConfig:
    history: int = 200
    var_threshold: float = 12.0
    diff_threshold: int = 8
    min_area_px: int = 3
    max_dimension_px: int = 80
    learning_rate: float = 0.005
    stabilize: bool = True


@dataclass(slots=True)
class StereoConfig:
    sync_tolerance_ms: float = 20.0
    epipolar_threshold_px: float = 2.5
    reprojection_threshold_px: float = 2.5
    minimum_crossing_angle_deg: float = 3.0
    minimum_range_m: float = 50.0
    maximum_range_m: float = 350.0
    pixel_sigma: float = 1.5


@dataclass(slots=True)
class TrackingConfig:
    confirm_hits: int = 5
    confirm_window: int = 8
    max_missed_s: float = 2.0
    association_distance_m: float = 30.0


@dataclass(slots=True)
class ClassifierConfig:
    model_path: Path | None = None
    input_size: int = 128
    minimum_confidence: float = 0.65
    providers: list[str] = field(
        default_factory=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
    )


@dataclass(slots=True)
class StorageConfig:
    database_path: Path = Path("data/uavguard.db")
    recording_directory: Path = Path("records")
    retention_days: int = 30
    segment_seconds: int = 300


@dataclass(slots=True)
class ServiceConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    webhook_url: str | None = None
    demo_mode: bool = False
    auth_username: str | None = None
    auth_password: str | None = None


@dataclass(slots=True)
class AppConfig:
    cameras: list[CameraConfig]
    motion: MotionConfig = field(default_factory=MotionConfig)
    stereo: StereoConfig = field(default_factory=StereoConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    config_path: Path = Path(".")

    @property
    def camera_ids(self) -> tuple[str, ...]:
        return tuple(camera.camera_id for camera in self.cameras if camera.enabled)

    def sanitized(self) -> dict[str, Any]:
        return {
            "cameraIds": list(self.camera_ids),
            "demoMode": self.service.demo_mode,
            "syncToleranceMs": self.stereo.sync_tolerance_ms,
            "retentionDays": self.storage.retention_days,
            "classifierConfigured": bool(self.classifier.model_path),
        }


def _resolve_path(base: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = _expand_env(yaml.safe_load(handle) or {})
    base = config_path.parent

    cameras = []
    for item in raw.get("cameras", []):
        item = dict(item)
        item["calibration"] = _resolve_path(base, item["calibration"])
        cameras.append(CameraConfig(**item))
    if len([camera for camera in cameras if camera.enabled]) != 2:
        raise ValueError("Exactly two enabled cameras are required for stereo operation")

    classifier_raw = dict(raw.get("classifier", {}))
    if "model_path" in classifier_raw:
        classifier_raw["model_path"] = _resolve_path(base, classifier_raw["model_path"])

    storage_raw = dict(raw.get("storage", {}))
    for key in ("database_path", "recording_directory"):
        if key in storage_raw:
            storage_raw[key] = _resolve_path(base, storage_raw[key])

    return AppConfig(
        cameras=cameras,
        motion=MotionConfig(**raw.get("motion", {})),
        stereo=StereoConfig(**raw.get("stereo", {})),
        tracking=TrackingConfig(**raw.get("tracking", {})),
        classifier=ClassifierConfig(**classifier_raw),
        storage=StorageConfig(**storage_raw),
        service=ServiceConfig(**raw.get("service", {})),
        config_path=config_path,
    )
