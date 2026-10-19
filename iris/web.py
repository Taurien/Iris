"""Local HTTP API for Iris's clients (desktop widget, Telegram mini app,
future Android app). Binds to 127.0.0.1 only — anything that needs to reach
it from outside this machine goes through a tunnel (e.g. cloudflared), never
by binding this server itself to a public interface.

API contract:
  GET  /status                          -> {"state", "channel", "since", "detail"}  (no auth)
  POST /message  {"text": "..."}        -> {"reply": "..."}                         (auth required)
  GET  /app/*                           -> static files for the web mini app

Auth: POST /message requires `Authorization: Bearer <IRIS_API_KEY>`
(from .env). GET /status carries no sensitive content, so it's left open on
localhost — the tunnel in front of it is the thing that decides who can
reach it at all.
"""
import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web

from . import brain, status

log = logging.getLogger("iris.web")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = PROJECT_ROOT / "webapp"

HOST = "127.0.0.1"
PORT = 8787


def _check_auth(request: web.Request) -> bool:
    api_key = os.environ.get("IRIS_API_KEY", "").strip()
    if not api_key:
        log.warning("IRIS_API_KEY not set — rejecting all /message requests")
        return False
    header = request.headers.get("Authorization", "")
    return header == f"Bearer {api_key}"


async def handle_status(_request: web.Request) -> web.Response:
    return web.json_response(status.get_state())


async def handle_webapp_index(_request: web.Request) -> web.Response:
    # aiohttp's static handler doesn't serve index.html for a directory
    # request on its own (show_index=False just turns off listing, it
    # doesn't fall back to an index file) — without this, /app/ 403s and
    # the Telegram bot's menu button (which points exactly there) breaks.
    return web.FileResponse(WEBAPP_DIR / "index.html")


async def handle_webapp_config(_request: web.Request) -> web.Response:
    api_key = os.environ.get("IRIS_API_KEY", "")
    body = f"window.IRIS_CONFIG = {{apiKey: {api_key!r}}};\n"
    return web.Response(text=body, content_type="application/javascript")


async def _revert_to_idle_after_error() -> None:
    # Don't make the HTTP caller wait on this — the error state just needs
    # to stay on the wire long enough for the widget's poll to see it.
    await asyncio.sleep(status.ERROR_MIN_VISIBLE_SEC)
    status.set_state("idle")


async def handle_message(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json body"}, status=400)
    text = (body or {}).get("text", "").strip()
    if not text:
        return web.json_response({"error": "empty text"}, status=400)

    reply, backend = await brain.ask(text, "App")
    if backend is None:
        asyncio.create_task(_revert_to_idle_after_error())
    else:
        status.set_state("idle")
    return web.json_response({"reply": reply})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/status", handle_status)
    app.router.add_post("/message", handle_message)
    app.router.add_get("/app/config.js", handle_webapp_config)
    if WEBAPP_DIR.exists():
        app.router.add_get("/app/", handle_webapp_index)
        app.router.add_static("/app/", path=str(WEBAPP_DIR), show_index=False)
    return app


async def start_web_server() -> web.AppRunner:
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    log.info("web API listening at http://%s:%d", HOST, PORT)
    return runner
