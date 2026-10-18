import time
from pathlib import Path

from fastapi.testclient import TestClient

from uavguard.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_demo_api_starts_and_serves_dashboard() -> None:
    app = create_app(ROOT / "config/demo.yaml")
    with TestClient(app) as client:
        time.sleep(0.4)
        config = client.get("/api/v1/config")
        assert config.status_code == 200
        assert config.json()["cameraIds"] == ["cam01", "cam02"]
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert len(health.json()["cameras"]) == 2
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "UAVGuard" in dashboard.text


def test_basic_auth_can_protect_dashboard() -> None:
    from uavguard.config import load_config

    config = load_config(ROOT / "config/demo.yaml")
    config.service.auth_username = "operator"
    config.service.auth_password = "correct-horse-battery-staple"
    app = create_app(config=config)
    with TestClient(app) as client:
        assert client.get("/").status_code == 401
        assert client.get("/", auth=("operator", "wrong")).status_code == 401
        assert client.get(
            "/", auth=("operator", "correct-horse-battery-staple")
        ).status_code == 200
