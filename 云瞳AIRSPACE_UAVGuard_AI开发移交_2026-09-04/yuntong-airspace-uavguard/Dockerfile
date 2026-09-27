FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UAVGUARD_CONFIG=/app/config/production.yaml

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      gstreamer1.0-libav \
      gstreamer1.0-plugins-bad \
      gstreamer1.0-plugins-base \
      gstreamer1.0-plugins-good \
      gstreamer1.0-tools \
      libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir '.[inference]'

COPY config ./config
COPY calibration ./calibration
COPY docs ./docs

RUN useradd --create-home --uid 10001 uavguard \
    && mkdir -p /app/data /app/records /app/models \
    && chown -R uavguard:uavguard /app
USER uavguard

EXPOSE 8080
CMD ["uavguard", "--config", "/app/config/production.yaml"]

