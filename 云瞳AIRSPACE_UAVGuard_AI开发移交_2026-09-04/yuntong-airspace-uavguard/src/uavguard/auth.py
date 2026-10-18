from __future__ import annotations

import base64
import secrets

from fastapi import HTTPException, Request, WebSocket, status
from fastapi.security.utils import get_authorization_scheme_param


class BasicAuthGuard:
    def __init__(self, username: str | None, password: str | None) -> None:
        if bool(username) != bool(password):
            raise ValueError("Both auth_username and auth_password must be configured")
        self.username = username
        self.password = password

    @property
    def enabled(self) -> bool:
        return bool(self.username and self.password)

    def _valid_header(self, header: str | None) -> bool:
        if not self.enabled:
            return True
        scheme, token = get_authorization_scheme_param(header or "")
        if scheme.lower() != "basic" or not token:
            return False
        try:
            decoded = base64.b64decode(token).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return secrets.compare_digest(username, self.username or "") and secrets.compare_digest(
            password, self.password or ""
        )

    async def require_http(self, request: Request) -> None:
        if not self._valid_header(request.headers.get("Authorization")):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": 'Basic realm="UAVGuard"'},
            )

    async def require_websocket(self, websocket: WebSocket) -> bool:
        return self._valid_header(websocket.headers.get("Authorization"))

