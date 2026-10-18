from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the UAVGuard service")
    parser.add_argument("--config", default="config/demo.yaml")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    app = create_app(config=config)
    uvicorn.run(
        app,
        host=args.host or config.service.host,
        port=args.port or config.service.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
