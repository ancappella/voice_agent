"""
Chapter07 HTTP 入口：自定义收音页 + Pipecat WebRTC bot。

  python server.py
  → http://localhost:7860/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.routing import Mount, Route

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


def _build_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chapter07 Pipecat voice server")
    parser.add_argument("-t", "--transport", default="webrtc", choices=["webrtc", "daily"])
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-f", "--folder", type=str, default=None)
    parser.add_argument("--esp32", action="store_true", default=False)
    parser.add_argument("--whatsapp", action="store_true", default=False)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--direct", action="store_true", default=False)
    parser.add_argument("--dialin", action="store_true", default=False)
    return parser.parse_args(argv)


def _attach_listen_ui(app) -> None:
    """Serve our page at / and /assets; keep /api/offer from the runner."""
    # Drop any existing GET / (runner redirects to /client)
    app.router.routes = [
        r for r in app.router.routes if not (isinstance(r, Route) and r.path == "/")
    ]

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.get("/", include_in_schema=False)
    async def listen_index():
        return FileResponse(STATIC / "index.html")

    logger.info("Listen UI mounted at /  (prebuilt still at /client)")


def main(argv: list[str] | None = None) -> None:
    args = _build_args(argv)
    args.transport = "webrtc"

    logger.remove()
    logger.add(sys.stderr, level="TRACE" if args.verbose else "DEBUG")

    # Ensure bot module discovery resolves to this package's bot.py
    sys.path.insert(0, str(ROOT))

    from pipecat.runner.run import _create_server_app

    app = _create_server_app(args)
    _attach_listen_ui(app)

    print()
    print("🚀 Chapter07 语音管道就绪")
    print(f"   → 收音页 http://{args.host}:{args.port}/")
    print(f"   → 官方客户端 http://{args.host}:{args.port}/client/")
    print()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
