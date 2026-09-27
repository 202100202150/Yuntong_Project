from pathlib import Path

import pytest

from uavguard.calibration import load_calibration
from uavguard.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_demo_config_resolves_two_cameras() -> None:
    config = load_config(ROOT / "config/demo.yaml")
    assert config.camera_ids == ("cam01", "cam02")
    assert all(camera.calibration.is_absolute() for camera in config.cameras)
    assert config.service.demo_mode is True


def test_runtime_rejects_unapproved_calibration() -> None:
    with pytest.raises(ValueError, match="not approved"):
        load_calibration(ROOT / "calibration/site-template.yaml")


def test_production_config_requires_rtsp_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAM01_RTSP_URL", raising=False)
    monkeypatch.delenv("CAM02_RTSP_URL", raising=False)
    with pytest.raises(ValueError, match="CAM01_RTSP_URL"):
        load_config(ROOT / "config/production.example.yaml")

