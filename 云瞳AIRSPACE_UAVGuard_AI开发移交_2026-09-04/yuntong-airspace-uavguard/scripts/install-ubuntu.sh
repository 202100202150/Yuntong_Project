#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as the deployment user, not root." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-libav

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[inference]'

echo "Installation complete. Copy config/production.example.yaml to config/production.yaml."

