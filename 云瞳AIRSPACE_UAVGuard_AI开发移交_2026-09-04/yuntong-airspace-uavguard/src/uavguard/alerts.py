from __future__ import annotations

import json
import logging
import urllib.request


LOGGER = logging.getLogger(__name__)


def send_webhook(url: str | None, payload: dict, timeout_s: float = 3.0) -> None:
    if not url:
        return
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            if response.status >= 300:
                LOGGER.warning("Webhook returned HTTP %s", response.status)
    except Exception:
        LOGGER.exception("Failed to send alert webhook")

