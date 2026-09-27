#!/usr/bin/env python3
"""Read-only Hikvision ISAPI inspection using HTTP Digest authentication."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import sys
import xml.etree.ElementTree as ET

from dotenv import load_dotenv
import requests
from requests.auth import HTTPDigestAuth


def element_text(element: ET.Element, name: str, default: str = "") -> str:
    node = element.find(f".//{{*}}{name}")
    return node.text.strip() if node is not None and node.text else default


def child_text(element: ET.Element, parent_name: str, child_name: str, default: str = "") -> str:
    parent = element.find(f".//{{*}}{parent_name}")
    if parent is None:
        return default
    child = parent.find(f".//{{*}}{child_name}")
    return child.text.strip() if child is not None and child.text else default


def rate_value(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value / 100.0 if value > 100.0 else value


def get_xml(session: requests.Session, base_url: str, path: str, timeout: float) -> ET.Element:
    response = session.get(f"{base_url}{path}", timeout=timeout)
    if response.status_code == 401:
        raise PermissionError(f"401 Unauthorized for {path}; verify the camera credentials")
    response.raise_for_status()
    try:
        return ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise RuntimeError(f"{path} did not return valid XML: {exc}") from exc


def inspect(args: argparse.Namespace, password: str) -> dict[str, object]:
    try:
        with socket.create_connection((args.ip, args.http_port), timeout=min(args.timeout, 3.0)):
            pass
    except OSError as exc:
        raise ConnectionError(f"TCP {args.ip}:{args.http_port} is unreachable: {exc}") from exc

    base_url = f"{args.scheme}://{args.ip}:{args.http_port}"
    session = requests.Session()
    session.auth = HTTPDigestAuth(args.username, password)
    session.headers["User-Agent"] = "camera-readonly-inspector/1.0"
    session.verify = not args.insecure

    device = get_xml(session, base_url, "/ISAPI/System/deviceInfo", args.timeout)
    channels_root = get_xml(session, base_url, "/ISAPI/Streaming/channels", args.timeout)

    image_response = session.get(f"{base_url}/ISAPI/Image/channels/1", timeout=args.timeout)
    image_settings: dict[str, object] = {"http_status": image_response.status_code}
    if image_response.ok:
        image_root = ET.fromstring(image_response.content)
        image_settings.update(
            {
                "exposure_type": child_text(image_root, "Exposure", "ExposureType"),
                "shutter": child_text(image_root, "Shutter", "ShutterLevel"),
                "gain": child_text(image_root, "Gain", "GainLevel"),
                "sharpness": child_text(image_root, "Sharpness", "SharpnessLevel"),
                "noise_reduction_mode": child_text(image_root, "NoiseReduce", "mode"),
                "noise_reduction_level": child_text(image_root, "NoiseReduce", "generalLevel"),
                "wdr_mode": child_text(image_root, "WDR", "mode"),
                "hlc_enabled": child_text(image_root, "HLC", "enabled"),
                "hlc_mode": child_text(image_root, "HLC", "HLCMode"),
            }
        )

    focus_response = session.get(f"{base_url}/ISAPI/Image/channels/1/focus", timeout=args.timeout)
    image_settings["focus_api_http_status"] = focus_response.status_code
    image_settings["focus_api_available"] = focus_response.ok

    channels: list[dict[str, object]] = []
    for channel in channels_root.findall(".//{*}StreamingChannel"):
        channel_id = element_text(channel, "id")
        codec = element_text(channel, "videoCodecType", "unknown")
        channels.append(
            {
                "id": channel_id,
                "name": element_text(channel, "channelName"),
                "enabled": element_text(channel, "enabled"),
                "codec": codec,
                "resolution": {
                    "width": element_text(channel, "videoResolutionWidth"),
                    "height": element_text(channel, "videoResolutionHeight"),
                },
                "fps": rate_value(element_text(channel, "maxFrameRate")),
                "bitrate_control": element_text(channel, "videoQualityControlType"),
                "bitrate_kbps": element_text(channel, "constantBitRate")
                or element_text(channel, "vbrUpperCap"),
                "gop_frames": element_text(channel, "GovLength"),
                "rtsp_url": f"rtsp://{args.ip}:{args.rtsp_port}/Streaming/Channels/{channel_id}",
            }
        )

    onvif_response = session.get(f"{base_url}/onvif/device_service", timeout=args.timeout)
    onvif_body = onvif_response.text[:300].replace("\r", " ").replace("\n", " ")
    return {
        "device": {
            "name": element_text(device, "deviceName"),
            "model": element_text(device, "model"),
            "serial": element_text(device, "serialNumber"),
            "firmware": element_text(device, "firmwareVersion"),
            "mac": element_text(device, "macAddress"),
        },
        "http": {"base_url": base_url, "digest_auth_succeeded": True},
        "image_settings": image_settings,
        "onvif": {"http_status": onvif_response.status_code, "response_excerpt": onvif_body},
        "streaming_channels": channels,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Hikvision ISAPI camera inspection")
    parser.add_argument("--ip", default=os.getenv("CAMERA_IP", "192.0.2.10"))
    parser.add_argument("--http-port", type=int, default=int(os.getenv("CAMERA_HTTP_PORT", "80")))
    parser.add_argument("--rtsp-port", type=int, default=int(os.getenv("CAMERA_RTSP_PORT", "554")))
    parser.add_argument("--username", default=os.getenv("CAMERA_USERNAME", "your_camera_username"))
    parser.add_argument("--scheme", choices=("http", "https"), default=os.getenv("CAMERA_HTTP_SCHEME", "http"))
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="disable HTTPS certificate verification; use only for a known self-signed camera certificate",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    args = parse_args()
    password_from_env = os.getenv("CAMERA_PASSWORD")
    password = (
        password_from_env
        if password_from_env not in (None, "")
        else getpass.getpass("Camera password: ")
    )
    try:
        result = inspect(args, password)
    except (requests.RequestException, OSError, RuntimeError, ValueError) as exc:
        print(f"Inspection failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
