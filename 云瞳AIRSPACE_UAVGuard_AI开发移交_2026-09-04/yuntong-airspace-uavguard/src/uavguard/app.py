from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from .config import AppConfig, load_config
from .auth import BasicAuthGuard
from .pipeline import PipelineRuntime


def create_app(
    config_path: str | Path | None = None,
    config: AppConfig | None = None,
) -> FastAPI:
    resolved_config = config or load_config(
        config_path or os.environ.get("UAVGUARD_CONFIG", "config/demo.yaml")
    )
    runtime = PipelineRuntime(resolved_config)
    auth = BasicAuthGuard(
        resolved_config.service.auth_username,
        resolved_config.service.auth_password,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    app = FastAPI(
        title="UAVGuard",
        version="0.1.0",
        description="Dual-camera campus UAV detection and 3D tracking service",
        lifespan=lifespan,
        dependencies=[Depends(auth.require_http)],
    )
    app.state.runtime = runtime

    @app.middleware("http")
    async def access_audit(request: Request, call_next):
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            runtime.event_store.log_access(
                request.client.host if request.client else None,
                request.method,
                request.url.path,
                status_code,
            )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        return files("uavguard").joinpath("static").joinpath("dashboard.html").read_text(
            encoding="utf-8"
        )

    @app.get("/api/v1/config")
    async def get_public_config() -> dict:
        return resolved_config.sanitized()

    @app.get("/api/v1/health")
    async def get_health() -> dict:
        return runtime.health()

    @app.get("/api/v1/tracks/active")
    async def get_active_tracks(confirmed_only: bool = False) -> dict:
        return {"tracks": runtime.tracker.snapshot(confirmed_only)}

    @app.get("/api/v1/events")
    async def get_events(limit: int = Query(100, ge=1, le=1000)) -> dict:
        return {"events": runtime.event_store.list(limit)}

    @app.get("/api/v1/cameras/{camera_id}/frame.jpg", include_in_schema=False)
    async def camera_frame(camera_id: str) -> Response:
        if camera_id not in resolved_config.camera_ids:
            raise HTTPException(404, "Unknown camera")
        frame = runtime.frame_hub.get(camera_id)
        if frame is None:
            raise HTTPException(503, "Camera frame is not available yet")
        return Response(content=frame, media_type="image/jpeg")

    @app.get("/api/v1/cameras/{camera_id}/mjpeg", include_in_schema=False)
    async def camera_mjpeg(camera_id: str) -> StreamingResponse:
        if camera_id not in resolved_config.camera_ids:
            raise HTTPException(404, "Unknown camera")

        async def generate():
            previous = None
            while True:
                frame = runtime.frame_hub.get(camera_id)
                if frame is not None and frame is not previous:
                    previous = frame
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                await asyncio.sleep(0.08)

        return StreamingResponse(
            generate(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/api/v1/recordings/{recording_path:path}", include_in_schema=False)
    async def get_recording(recording_path: str) -> FileResponse:
        root = resolved_config.storage.recording_directory.resolve()
        candidate = (root / recording_path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise HTTPException(404, "Recording not found")
        return FileResponse(candidate, media_type="video/mp4")

    @app.websocket("/ws/tracks")
    async def tracks_websocket(websocket: WebSocket) -> None:
        if not await auth.require_websocket(websocket):
            await websocket.close(code=1008, reason="Authentication required")
            return
        await websocket.accept()
        subscriber = runtime.broker.subscribe()
        try:
            while True:
                payload = await subscriber.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            runtime.broker.unsubscribe(subscriber)

    return app
