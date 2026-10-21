"""The token server: only the phone app's WebView may read its answers from a browser.

21 Oct 2026, the failure hunt: with "Access-Control-Allow-Origin: *" any web page open on a machine that can reach
the laptop could read a room token, join, and type requests, including to the SmartThings home, which runs on the
laptop. The app's WebView is https://localhost (measured on the emulator); pages served by the server itself are
same-origin.
"""

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from duet_voice.galaxy import server


def answers(origin, method="GET"):
    async def go():
        app = web.Application(middlewares=[server.cors])
        app.router.add_get("/api/x", lambda request: web.json_response({"ok": True}))
        async with TestClient(TestServer(app)) as client:
            headers = {"Origin": origin} if origin else {}
            resp = await client.request(method, "/api/x", headers=headers)
            return resp.status, resp.headers.get("Access-Control-Allow-Origin")
    return asyncio.run(go())


def test_the_phone_apps_webview_may_read_the_answers():
    assert answers("https://localhost") == (200, "https://localhost")
    assert answers("capacitor://localhost")[1] == "capacitor://localhost"
    assert answers("https://localhost", "OPTIONS") == (204, "https://localhost")


def test_another_web_page_may_not():
    assert answers("https://evil.example") == (200, None)
    assert answers("https://evil.example", "OPTIONS")[1] is None


def test_requests_from_outside_a_browser_are_unchanged():
    assert answers("") == (200, None)            # the suite, the phone scripts, curl: no Origin, no CORS


def test_more_origins_can_be_allowed(monkeypatch):
    monkeypatch.setenv("DUET_APP_ORIGINS", "http://192.168.0.3:8080")
    assert answers("http://192.168.0.3:8080")[1] == "http://192.168.0.3:8080"
