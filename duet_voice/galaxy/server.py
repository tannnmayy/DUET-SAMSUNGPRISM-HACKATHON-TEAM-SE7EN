"""The phone app's front door: room tokens, and the web version of the app.

    python -m duet_voice.galaxy.server            # http://0.0.0.0:8787

GET /api/token?mode=assistant&name=Asha&device=SM-S928B&contacts=... returns
{"url", "token", "room"}: a new LiveKit room whose configuration dispatches the
DUET for Galaxy worker with that mode. The LiveKit API secret stays here (read
from .env.livekit); the phone only ever holds a two-hour room token.

GET / serves app/www, the same interface the Android app ships, so a laptop
browser can run the demo too (with the phone's actions simulated).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import socket

from aiohttp import web
from dotenv import load_dotenv
from livekit import api

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WWW = os.path.join(ROOT, "app", "www")
MODES = ("assistant", "care", "drive")


def _clip(value: str, n: int) -> str:
    return " ".join(str(value or "").split())[:n]


def token(query) -> dict:
    url, key, secret = (os.environ.get(k, "") for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if not (url and key and secret):
        raise web.HTTPServiceUnavailable(text="LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are not set")
    mode = query.get("mode", "assistant")
    mode = mode if mode in MODES else "assistant"
    room = "duet-%s-%s" % (mode, secrets.token_hex(3))
    meta = {"mode": mode, "user_name": _clip(query.get("name"), 40),
            "device": _clip(query.get("device"), 80), "contacts": _clip(query.get("contacts"), 300)}
    identity = "galaxy-" + secrets.token_hex(3)
    jwt = (api.AccessToken(key, secret)
           .with_identity(identity)
           .with_name(meta["user_name"] or "Galaxy user")
           .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True,
                                        can_publish_data=True))
           .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(
               agent_name=os.environ.get("DUET_GALAXY_AGENT", "duet-galaxy"), metadata=json.dumps(meta))]))
           .with_ttl(dt.timedelta(hours=2))
           .to_jwt())
    return {"url": url, "token": jwt, "room": room, "identity": identity}


@web.middleware
async def cors(request, handler):
    resp = web.Response(status=204) if request.method == "OPTIONS" else await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


async def handle_token(request):
    return web.json_response(token(request.query))


async def handle_index(request):
    return web.FileResponse(os.path.join(WWW, "index.html"))


def lan_addresses() -> list:
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address used to reach the internet, when there is one
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        found.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def main() -> None:
    load_dotenv(os.path.join(ROOT, ".env.livekit"))
    port = int(os.environ.get("DUET_APP_PORT", "8787"))
    app = web.Application(middlewares=[cors])
    app.router.add_get("/api/token", handle_token)
    app.router.add_get("/", handle_index)
    app.router.add_static("/", WWW)
    print("DUET for Galaxy: token server and web app on port %d" % port, flush=True)
    print("  on this laptop: http://localhost:%d" % port, flush=True)
    for a in lan_addresses():
        print("  in the phone app, server address: %s:%d" % (a, port), flush=True)
    web.run_app(app, host=os.environ.get("DUET_APP_HOST", "0.0.0.0"), port=port, print=None)


if __name__ == "__main__":
    main()
