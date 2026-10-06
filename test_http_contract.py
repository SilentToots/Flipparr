"""HTTP contract characterisation for the Flipparr API.

These tests speak plain HTTP against a running server and assert only what a
client can observe: status code, content type, and response shape. Nothing here
references the handler class, so the suite is transport-agnostic and must pass
unchanged before and after the FastAPI port. Only `_start_server` knows which
server is under test.

Written before the port, against the stdlib ThreadingHTTPServer, so the port has
something to be verified against: the routing table is 54 branches and only one
existing test touched the transport layer at all. Since 2026-10-04 it runs
against the production server, Waitress (app.make_server).
"""

import gzip
import http.client
import socket
import contextlib
import io
import datetime as dt
import json
import os
import subprocess
import sys
import re
import sqlite3
import tempfile
import zipfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory()
_ROOT = Path(_TEMP.name)
(_ROOT / "web").mkdir()
(_ROOT / "web" / "index.html").write_text("<!doctype html><title>shell</title>")
(_ROOT / "web" / "assets").mkdir()
(_ROOT / "web" / "assets" / "asset.js").write_text("console.log(1)")
(_ROOT / "web" / "font.woff2").write_bytes(b"wOF2")
(_ROOT / "web" / "manifest.webmanifest").write_text('{"name": "Flipparr"}')
(_ROOT / "web" / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
os.environ["COMICARR_DATABASE"] = str(_ROOT / "contract.db")
os.environ["COMICARR_WEB_ROOT"] = str(_ROOT / "web")
os.environ["COMICARR_PROVIDER_CONFIG"] = str(_ROOT / "providers.json")
os.environ["COMICARR_ACQUISITION_CONFIG"] = str(_ROOT / "services.json")
os.environ["COMICARR_SETTINGS_CONFIG"] = str(_ROOT / "settings.json")
os.environ["COMICARR_AUTH_CONFIG"] = str(_ROOT / "auth.json")

import app  # noqa: E402  (import after the environment is prepared)


def _start_server() -> str:
    """Boot the server under test and return its base URL."""
    server, port = app.make_server("127.0.0.1", 0)
    threading.Thread(target=server.run, daemon=True).start()
    _SERVERS.append(server)
    return f"http://127.0.0.1:{port}"


_SERVERS: list = []


def tearDownModule():
    for server in _SERVERS:
        server.close()
        server.task_dispatcher.shutdown(timeout=1)


class Response:
    def __init__(self, status: int, headers, body: bytes, set_cookies: list[str] | None = None):
        self.status = status
        self.headers = headers
        self.body = body
        # Every Set-Cookie, by name: a dict of headers keeps only one of them.
        self.cookies = {}
        for header in set_cookies or []:
            name, _, rest = header.partition("=")
            self.cookies[name] = rest.split(";")[0]

    def json(self):
        return json.loads(self.body)


def request(
    method: str, url: str, data: bytes | None = None, content_type: str | None = None,
    accept_encoding: str | None = None, headers: dict[str, str] | None = None,
) -> Response:
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    if accept_encoding:
        req.add_header("Accept-Encoding", accept_encoding)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return Response(response.status, dict(response.headers), response.read(),
                            response.headers.get_all("Set-Cookie") or [])
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers or {}), exc.read(),
                        (exc.headers.get_all("Set-Cookie") if exc.headers else None) or [])


def _png_bytes(size: int = 4) -> bytes:
    """A real PNG, so a cover test fails on the route and not on the fixture."""
    import struct, zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes([120, 90, 200]) * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class HttpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = _start_server()

    def get(self, path, **kw):
        return request("GET", self.base + path, **kw)

    def post(self, path, payload=None, raw=None, headers=None):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"{}")
        return request("POST", self.base + path, body, "application/json", headers=headers)

    def patch(self, path, payload):
        return request("PATCH", self.base + path, json.dumps(payload).encode(), "application/json")

    def delete(self, path):
        return request("DELETE", self.base + path)

    # ---- read-only API surface -------------------------------------------

    def test_catalog_returns_json_with_the_documented_keys(self):
        response = self.get("/api/v1/catalog")
        self.assertEqual(response.status, 200)
        self.assertIn("application/json", response.headers["Content-Type"])
        payload = response.json()
        for key in ("series", "stats", "collectedEditionsEnabled"):
            self.assertIn(key, payload)
        # There is no JSX test harness, so the badge's contract is pinned here.
        # It has three states and "no provider has recorded an end year" is a
        # distinct one from "the run is still publishing"; collapsing them is
        # what made a finished run wear an Active Run badge.
        for item in payload["series"]:
            self.assertIn(
                item.get("publicationStatus"), {"completed", "ongoing", "unknown"},
                f"{item.get('title')!r} reported an unexpected publication status",
            )

    def test_settings_round_trips_over_http(self):
        self.assertEqual(self.get("/api/v1/settings").json()["collectedEditionsEnabled"], False)
        updated = self.patch("/api/v1/settings", {"collectedEditionsEnabled": True})
        self.assertEqual(updated.status, 200)
        self.assertTrue(updated.json()["collectedEditionsEnabled"])
        self.assertTrue(self.get("/api/v1/settings").json()["collectedEditionsEnabled"])
        self.patch("/api/v1/settings", {"collectedEditionsEnabled": False})

    def test_settings_rejects_unknown_keys_with_400(self):
        self.assertEqual(self.patch("/api/v1/settings", {"nope": True}).status, 400)

    def test_the_download_order_round_trips_and_refuses_a_bad_list(self):
        self.assertEqual(self.get("/api/v1/settings").json()["sourcePriority"], ["usenet", "torrent", "direct_site"])
        try:
            updated = self.patch("/api/v1/settings", {"sourcePriority": ["torrent", "direct_site", "usenet"]})
            self.assertEqual(updated.status, 200)
            self.assertEqual(self.get("/api/v1/settings").json()["sourcePriority"], ["torrent", "direct_site", "usenet"])
            for bad in (["torrent", "usenet"], ["usenet", "usenet", "direct_site"], "usenet", ["usenet", "torrent", "ftp"]):
                with self.subTest(bad=bad):
                    self.assertEqual(self.patch("/api/v1/settings", {"sourcePriority": bad}).status, 400)
        finally:
            self.patch("/api/v1/settings", {"sourcePriority": ["usenet", "torrent", "direct_site"]})
        self.assertEqual(self.patch("/api/v1/settings", {"collectedEditionsEnabled": "yes"}).status, 400)

    def test_providers_and_services_list_without_credentials(self):
        for path in ("/api/v1/providers", "/api/v1/acquisition-services"):
            response = self.get(path)
            self.assertEqual(response.status, 200, path)
            body = response.body.decode().lower()
            for secret in ("apikey\":", "token\":", "password"):
                self.assertNotIn(secret, body, f"{path} leaked a credential field")

    def test_a_torrent_clients_sign_in_is_saved_and_never_sent_back(self):
        config = Path(os.environ["COMICARR_ACQUISITION_CONFIG"])
        before = config.read_text() if config.exists() else None
        try:
            response = self.post("/api/v1/acquisition-services/qbittorrent", {
                "url": "http://qbittorrent:8080", "username": "admin", "password": "hunter2", "category": "comics",
            })
            self.assertEqual(response.status, 200)
            body = response.body.decode()
            self.assertNotIn("hunter2", body)
            self.assertNotIn("password", body.lower())
            listed = json.loads(self.get("/api/v1/acquisition-services").body)
            qbt = next(service for service in listed["services"] if service["id"] == "qbittorrent")
            self.assertEqual((qbt["username"], qbt["category"], qbt["credentialHint"]), ("admin", "comics", "Saved locally"))
        finally:
            if before is None:
                config.unlink(missing_ok=True)
            else:
                config.write_text(before)

    def test_unknown_scan_id_is_not_found(self):
        self.assertEqual(self.get("/api/v1/scans/999999").status, 404)

    def test_unknown_file_id_is_not_found(self):
        self.assertEqual(self.get("/api/v1/files/999999").status, 404)

    def test_discover_requires_a_query(self):
        self.assertEqual(self.get("/api/v1/discover?query=a").status, 400)

    # ---- request-body handling -------------------------------------------

    def test_malformed_json_body_is_rejected(self):
        self.assertEqual(self.post("/api/v1/scans", raw=b"{not json").status, 400)

    def test_non_object_json_body_is_rejected(self):
        self.assertEqual(self.post("/api/v1/scans", raw=b"[1,2,3]").status, 400)

    def test_oversized_body_is_refused(self):
        """Characterised, not endorsed: the server refuses the body by closing
        the connection, so the client sees a transport error rather than a
        clean 400. A port may improve this, but it must not start *accepting*
        a body over the 1 MB cap."""
        oversized = b'{"folder":"' + b"x" * 1_100_000 + b'"}'
        try:
            status = self.post("/api/v1/scans", raw=oversized).status
        except urllib.error.URLError:
            return  # connection refused/reset — current behaviour
        self.assertEqual(status, 400)

    def test_scan_requires_a_valid_folder(self):
        self.assertEqual(self.post("/api/v1/scans", {"folder": "/nope/missing"}).status, 400)

    # ---- mutation routes reachable and validating ------------------------

    def test_merge_requires_two_distinct_existing_runs(self):
        self.assertEqual(self.post("/api/v1/series/merge", {"sourceId": 1, "targetId": 1}).status, 400)

    def test_library_root_delete_reports_missing_root(self):
        self.assertIn(self.delete("/api/v1/library-roots/999999").status, (400, 404))

    def test_review_resolve_validates_its_payload(self):
        self.assertEqual(self.post("/api/v1/reviews/resolve", {}).status, 400)

    # ---- static assets and the SPA shell ---------------------------------

    def test_named_asset_is_served_with_immutable_caching(self):
        response = self.get("/assets/asset.js")
        self.assertEqual(response.status, 200)
        self.assertIn("immutable", response.headers.get("Cache-Control", ""))
        # Only the build's hashed files: the manifest keeps its name from
        # release to release, and a year of `immutable` kept the old one.
        manifest = self.get("/manifest.webmanifest")
        self.assertEqual(manifest.headers.get("Cache-Control"), "no-cache")
        shell = self.get("/")
        self.assertIn("script-src 'self'", shell.headers.get("Content-Security-Policy", ""))
        self.assertIn("frame-ancestors 'none'", shell.headers.get("Content-Security-Policy", ""))

    def test_unknown_client_route_falls_back_to_the_app_shell(self):
        response = self.get("/library/some/deep/route")
        self.assertEqual(response.status, 200)
        self.assertIn(b"shell", response.body)
        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")

    def test_a_bundled_font_is_served_as_a_font_without_the_platform_table(self):
        # The slim image's mimetypes table has no .woff2; reproduce that.
        with patch.object(app.mimetypes, "guess_type", return_value=(None, None)):
            response = self.get("/font.woff2")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "font/woff2")

    def test_the_manifest_and_favicon_are_served_as_what_they_are(self):
        """A phone installs the app from the manifest; served as octet-stream
        it is ignored, and the home-screen icon is whatever the browser
        guessed. The slim image's mimetypes table knows neither type."""
        with patch.object(app.mimetypes, "guess_type", return_value=(None, None)):
            manifest = self.get("/manifest.webmanifest")
            icon = self.get("/favicon.ico")
        self.assertEqual(manifest.status, 200)
        self.assertEqual(manifest.headers["Content-Type"], "application/manifest+json")
        self.assertEqual(icon.status, 200)
        self.assertEqual(icon.headers["Content-Type"], "image/x-icon")

    def test_art_swatch_is_a_small_png_of_an_allowed_cover(self):
        from PIL import Image
        source = io.BytesIO()
        Image.new("RGB", (400, 600), (180, 40, 30)).save(source, "JPEG")

        class Upstream:
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def read(self, limit):
                return self.body[:limit]

        opener = type("Opener", (), {"open": lambda _self, request, timeout: Upstream(source.getvalue())})()
        app._ART_SWATCH_CACHE.clear()
        with patch.object(app.urllib.request, "build_opener", return_value=opener):
            response = self.get("/api/v1/art-swatch?src=" + urllib.parse.quote("https://static.metron.cloud/media/a.jpg"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        with Image.open(io.BytesIO(response.body)) as swatch:
            self.assertEqual(swatch.size, (app.ART_SWATCH_SIZE, app.ART_SWATCH_SIZE))

    def test_art_swatch_refuses_hosts_it_does_not_read_covers_from(self):
        for src in ("https://example.com/a.jpg", "http://static.metron.cloud/a.jpg",
                    "https://user:pw@static.metron.cloud/a.jpg", "https://static.metron.cloud:8443/a.jpg",
                    "file:///etc/passwd", ""):
            with self.subTest(src=src):
                response = self.get("/api/v1/art-swatch?src=" + urllib.parse.quote(src))
                self.assertEqual(response.status, 400)

    def test_asset_traversal_outside_the_web_root_is_refused(self):
        self.assertEqual(self.get("/../app.py").status, 404)

    def test_unknown_api_get_route_falls_through_to_the_app_shell(self):
        """Characterised, not endorsed. An unmatched GET under /api/v1 is
        handled by the static asset fallback, so a client that typos an API
        path receives the HTML shell with 200 instead of a JSON 404 — while
        the same typo on POST does return a JSON 404 (below). Pinned here so
        the inconsistency is a deliberate decision to keep or change, not an
        accident of the port."""
        response = self.get("/api/v1/definitely-not-a-route")
        self.assertEqual(response.status, 200)
        self.assertIn(b"shell", response.body)

    def test_unknown_api_post_route_is_json_404(self):
        response = self.post("/api/v1/definitely-not-a-route", {})
        self.assertEqual(response.status, 404)
        self.assertIn("application/json", response.headers["Content-Type"])


    # ---- compression --------------------------------------------------------

    def test_json_is_compressed_when_the_client_accepts_it(self):
        plain = self.get("/api/v1/catalog")
        gzipped = request("GET", self.base + "/api/v1/catalog", accept_encoding="gzip")
        self.assertEqual(gzipped.headers.get("Content-Encoding"), "gzip")
        # JSON varies by who asks as well: one profile's answers are not another's.
        self.assertEqual(gzipped.headers.get("Vary"), "Accept-Encoding, Cookie")
        self.assertEqual(gzipped.headers.get("Cache-Control"), "private, no-store")
        # urllib does not auto-decompress, so the body is the compressed bytes.
        self.assertLess(len(gzipped.body), len(plain.body))
        self.assertEqual(gzip.decompress(gzipped.body), plain.body)

    def test_a_client_that_does_not_accept_gzip_still_gets_plain_json(self):
        response = self.get("/api/v1/catalog")
        self.assertIsNone(response.headers.get("Content-Encoding"))
        json.loads(response.body)

    def test_text_assets_are_compressed_and_marked_vary(self):
        gzipped = request("GET", self.base + "/assets/asset.js", accept_encoding="gzip")
        self.assertEqual(gzipped.headers.get("Vary"), "Accept-Encoding")
        self.assertIn("immutable", gzipped.headers.get("Cache-Control", ""))

    def test_small_responses_are_not_compressed(self):
        """Below the threshold gzip costs more than it saves."""
        response = request("GET", self.base + "/api/v1/settings", accept_encoding="gzip")
        self.assertIsNone(response.headers.get("Content-Encoding"))

    # ---- unhandled failures -------------------------------------------------

    def test_an_unexpected_failure_returns_json_500_not_a_dead_socket(self):
        """Before the guard this closed the connection with no response, which a
        client cannot tell apart from the server being down."""
        with patch("app.catalog_api_payload", side_effect=RuntimeError("boom")):
            response = self.get("/api/v1/catalog")
        self.assertEqual(response.status, 500)
        self.assertIn("application/json", response.headers["Content-Type"])
        self.assertIn("error", response.json())

    # ---- authentication -----------------------------------------------------

    def _configure_auth(self, **patch):
        """Set auth directly through the config layer, bypassing the HTTP API."""
        # Failed sign-ins are remembered process-wide; start every test clean
        # so the suite passes in any order.
        app._LOGIN_THROTTLE.clear()
        self.addCleanup(app._LOGIN_THROTTLE.clear)
        app.save_auth_config({"method": "forms", "username": "reader",
                              "password": "correct horse battery", **patch})
        self.addCleanup(lambda: app.save_auth_config({"method": "none"}))

    def _login(self, password, username="reader"):
        return self.post("/api/v1/auth/login", {"username": username, "password": password})

    def test_the_first_failed_sign_ins_are_not_slowed(self):
        self._configure_auth(localBypass=False)
        for _ in range(app._LOGIN_FREE_FAILURES - 1):
            self.assertEqual(self._login("wrong password").status, 401)
        self.assertEqual(self._login("correct horse battery").status, 200)

    def test_repeated_failures_make_sign_in_wait_even_for_the_right_password(self):
        """A correct guess must not land inside the wait, or the wait only
        slows the attacker's bookkeeping."""
        self._configure_auth(localBypass=False)
        self.assertEqual(self._login("wrong password").status, 401)
        # The rest of the failures are recorded directly, so the wait is long
        # enough (8s) that a slow test runner cannot outlast it.
        config = app.load_auth_config()
        for _ in range(app._LOGIN_FREE_FAILURES + 2):
            app.record_login_result("reader", config, False)
        refused = self._login("correct horse battery")
        self.assertEqual(refused.status, 429)
        wait = int(refused.headers.get("Retry-After"))
        self.assertIn(wait, range(1, 9))
        self.assertEqual(refused.json()["retryAfter"], wait)
        self.assertIn(f"Try again in {wait} second", refused.json()["error"])

    def test_the_wait_doubles_and_is_capped(self):
        config = {"username": "reader", "passwordHash": "h"}
        now = 1_000.0
        waits = []
        for _ in range(app._LOGIN_FREE_FAILURES + 9):
            app.record_login_result("reader", config, False, now=now)
            waits.append(app.login_retry_after("reader", config, now=now))
            now += waits[-1]
        app._LOGIN_THROTTLE.clear()
        free = app._LOGIN_FREE_FAILURES - 1
        self.assertEqual(waits[:free], [0] * free)
        self.assertEqual(waits[free:free + 6], [1, 2, 4, 8, 16, 32])
        self.assertEqual(set(waits[free + 6:]), {app._LOGIN_MAX_DELAY_SECONDS})

    def test_a_quiet_spell_forgets_earlier_failures(self):
        config = {"username": "reader", "passwordHash": "h"}
        for _ in range(app._LOGIN_FREE_FAILURES):
            app.record_login_result("reader", config, False, now=1_000.0)
        later = 1_000.0 + app._LOGIN_FAILURE_WINDOW_SECONDS + 1
        self.assertEqual(app.login_retry_after("reader", config, now=later), 0)
        app._LOGIN_THROTTLE.clear()

    def test_a_successful_sign_in_clears_the_failures(self):
        config = {"username": "reader", "passwordHash": "h"}
        for _ in range(app._LOGIN_FREE_FAILURES - 1):
            app.record_login_result("reader", config, False, now=1_000.0)
        app.record_login_result("reader", config, True, now=1_000.0)
        app.record_login_result("reader", config, False, now=1_000.0)
        self.assertEqual(app.login_retry_after("reader", config, now=1_000.0), 0)
        app._LOGIN_THROTTLE.clear()

    def test_invented_usernames_share_one_bucket(self):
        """The table must not grow with every name an attacker makes up."""
        config = {"username": "reader", "passwordHash": "h"}
        for index in range(50):
            app.record_login_result(f"guess-{index}", config, False, now=1_000.0)
        self.assertEqual(set(app._LOGIN_THROTTLE), {"other"})
        self.assertEqual(app.login_retry_after("reader", config, now=1_000.0), 0)
        app._LOGIN_THROTTLE.clear()

    def test_a_password_reset_ends_the_wait(self):
        """The owner should not sit out an attacker's delay on a password the
        attacker never had -- even when the reset ran in another process."""
        self._configure_auth(localBypass=False)
        config = app.load_auth_config()
        for _ in range(app._LOGIN_FREE_FAILURES + 5):
            app.record_login_result("reader", config, False)
        self.assertEqual(self._login("correct horse battery").status, 429)
        app.reset_password(None, "a brand new password")
        self.assertEqual(self._login("a brand new password").status, 200)

    def test_reset_password_signs_every_device_out_and_keeps_sign_in_on(self):
        self._configure_auth(localBypass=False)
        before = app.load_auth_config()
        old_token = app.issue_session_token(before)
        updated = app.reset_password(None, "a brand new password")
        self.assertEqual(updated["method"], "forms")
        self.assertEqual(updated["username"], "reader")
        self.assertFalse(app.session_token_valid(old_token, app.load_auth_config()))
        self.assertTrue(app.verify_password("a brand new password", app.load_auth_config()["passwordHash"]))
        self.assertFalse(app.verify_password("correct horse battery", app.load_auth_config()["passwordHash"]))

    def test_reset_password_refuses_a_short_password_and_changes_nothing(self):
        self._configure_auth(localBypass=False)
        before = app.load_auth_config()
        with self.assertRaises(ValueError):
            app.reset_password(None, "short")
        self.assertEqual(app.load_auth_config(), before)

    def test_reset_password_command_runs_outside_the_server(self):
        """The recovery path is `docker exec ... app.py reset-password`, so it is
        exercised as a real process against a config file, not a function call."""
        config_file = _ROOT / "reset-command-auth.json"
        config_file.unlink(missing_ok=True)
        env = {**os.environ, "COMICARR_AUTH_CONFIG": str(config_file),
               "FLIPPARR_AUTH_CONFIG": str(config_file)}
        with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(config_file),
                                           "FLIPPARR_AUTH_CONFIG": str(config_file)}):
            app.save_auth_config({"method": "forms", "username": "owner",
                                  "password": "the old password"})
            old_secret = app.load_auth_config()["sessionSecret"]
        script = Path(app.__file__).resolve()
        done = subprocess.run(
            [sys.executable, "-B", str(script), "reset-password", "--password-stdin"],
            input="the new password\n", capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("'owner'", done.stdout)
        self.assertIn("Sign-in is on", done.stdout)
        with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(config_file),
                                           "FLIPPARR_AUTH_CONFIG": str(config_file)}):
            stored = app.load_auth_config()
        self.assertTrue(app.verify_password("the new password", stored["passwordHash"]))
        self.assertNotEqual(stored["sessionSecret"], old_secret)
        refused = subprocess.run(
            [sys.executable, "-B", str(script), "reset-password", "--password-stdin"],
            input="short\n", capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(refused.returncode, 1)
        self.assertIn("nothing was changed", refused.stderr)

    def test_local_bypass_is_off_by_default(self):
        """On by default made "require sign-in" appear to do nothing: a tunnel,
        proxy or container bridge all present a private address."""
        fresh = _ROOT / "default-auth.json"
        fresh.unlink(missing_ok=True)
        with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(fresh)}):
            self.assertFalse(app.load_auth_config()["localBypass"])
            app.save_auth_config({"method": "none", "username": "x", "password": "password-here"})
            self.assertFalse(app.load_auth_config()["localBypass"])

    def test_health_endpoint_stays_open_so_the_container_check_keeps_working(self):
        self._configure_auth(localBypass=False)
        response = self.get("/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_api_requires_a_session_once_forms_auth_is_on(self):
        self._configure_auth(localBypass=False)
        self.assertEqual(self.get("/api/v1/catalog").status, 401)

    def test_app_shell_stays_reachable_so_a_login_form_can_render(self):
        self._configure_auth(localBypass=False)
        self.assertEqual(self.get("/library").status, 200)

    def test_login_rejects_a_bad_password_and_accepts_the_right_one(self):
        self._configure_auth(localBypass=False)
        self.assertEqual(self.post("/api/v1/auth/login",
                                   {"username": "reader", "password": "wrong"}).status, 401)
        ok = self.post("/api/v1/auth/login",
                       {"username": "reader", "password": "correct horse battery"})
        self.assertEqual(ok.status, 200)
        cookie = ok.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_a_session_cookie_grants_access_including_to_image_urls(self):
        """Covers render in <img>, which cannot carry an Authorization header,
        so the session has to travel as a cookie."""
        self._configure_auth(localBypass=False)
        login = self.post("/api/v1/auth/login",
                          {"username": "reader", "password": "correct horse battery"})
        token = login.headers["Set-Cookie"].split(";")[0]
        authed = request("GET", self.base + "/api/v1/catalog")
        self.assertEqual(authed.status, 401)
        req = urllib.request.Request(self.base + "/api/v1/catalog")
        req.add_header("Cookie", token)
        with urllib.request.urlopen(req, timeout=20) as response:
            self.assertEqual(response.status, 200)

    def test_password_is_never_returned_by_the_config_endpoints(self):
        self._configure_auth()
        body = self.get("/api/v1/auth").body.decode().lower()
        for leak in ("passwordhash", "sessionsecret", "correct horse"):
            self.assertNotIn(leak, body)

    def test_auth_status_does_not_leak_the_username_before_login(self):
        self._configure_auth(localBypass=False)
        body = self.get("/api/v1/auth/status").body.decode().lower()
        self.assertNotIn("reader", body)

    def test_a_forwarded_header_from_an_untrusted_peer_cannot_fake_a_local_client(self):
        """The bypass is only safe if a spoofed X-Forwarded-For is ignored."""
        self._configure_auth(localBypass=True)
        req = urllib.request.Request(self.base + "/api/v1/catalog")
        req.add_header("X-Forwarded-For", "127.0.0.1")
        # The test client really is local, so assert on the resolution rule
        # itself rather than on the response.
        self.assertEqual(app.trusted_proxies(), ())

    def test_cross_site_state_change_is_refused(self):
        req = urllib.request.Request(
            self.base + "/api/v1/settings", data=json.dumps({"collectedEditionsEnabled": True}).encode(),
            method="PATCH")
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", "https://evil.example")
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 403)

    def test_password_hashing_is_salted_and_verifies(self):
        first, second = app.hash_password("same password"), app.hash_password("same password")
        self.assertNotEqual(first, second, "each hash must use a fresh salt")
        self.assertTrue(app.verify_password("same password", first))
        self.assertFalse(app.verify_password("other password", first))
        self.assertFalse(app.verify_password("same password", "not-a-hash"))

    def _captured_log(self, run):
        """Run `run` with stdout captured, returning the JSON lines it emitted.

        The server writes its line just after it has answered, so the client
        can be back here before the line lands -- and the line for the request
        *before* this one can land inside this window. Both made this suite
        flaky in CI (an empty capture, or a neighbour's line read as ours), so
        wait for the line this request wrote, and read lines by request id
        (`_logged`) rather than by position.
        """
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = run()
            request_id = getattr(result, "headers", {}).get("X-Request-Id")
            if request_id:
                deadline = time.monotonic() + 5
                while request_id not in buffer.getvalue() and time.monotonic() < deadline:
                    time.sleep(0.005)
        lines = []
        for line in buffer.getvalue().splitlines():
            line = line.strip()
            if line.startswith("{"):
                lines.append(json.loads(line))
        return result, lines

    @staticmethod
    def _logged(lines, response, event="http_request"):
        """The lines this response's own request wrote."""
        request_id = response.headers.get("X-Request-Id")
        return [line for line in lines
                if line.get("event") == event and line.get("request_id") == request_id]

    def test_the_system_page_reports_workers_work_and_problems(self):
        status = self.get("/api/v1/system/status")
        self.assertEqual(status.status, 200)
        body = status.json()
        for key in ("version", "build", "uptimeSeconds", "database", "server", "disk", "workers", "work", "problems"):
            self.assertIn(key, body)
        self.assertEqual({worker["id"] for worker in body["workers"]},
                         {"metadata", "imports", "searches", "scans", "ratings"})
        self.assertIn("byStatus", body["work"]["wanted"])

    def test_the_diagnostics_file_is_a_download_without_any_secret(self):
        app.save_acquisition_service_config("prowlarr", {
            "url": "http://indexer.private-host.example:9696", "apiKey": "super-secret-key-123", "enabled": True})
        self.addCleanup(lambda: Path(os.environ["COMICARR_ACQUISITION_CONFIG"]).unlink(missing_ok=True))
        report = self.get("/api/v1/system/diagnostics")
        self.assertEqual(report.status, 200)
        self.assertIn("attachment", report.headers.get("Content-Disposition", ""))
        self.assertNotIn(b"super-secret-key-123", report.body)
        self.assertNotIn(b"private-host", report.body, "no service addresses either")
        services = {service["id"]: service for service in json.loads(report.body)["services"]}
        self.assertTrue(services["prowlarr"]["configured"])

    def test_the_system_routes_are_the_admins(self):
        import access_policy
        for path in ("/api/v1/system/status", "/api/v1/system/diagnostics"):
            self.assertEqual(access_policy.route_access("GET", path), "admin", path)

    def test_every_request_is_logged_as_one_json_object(self):
        response, lines = self._captured_log(lambda: self.get("/healthz"))
        self.assertEqual(response.status, 200)
        requests = self._logged(lines, response)
        self.assertEqual(len(requests), 1)
        entry = requests[0]
        for field in ("ts", "level", "event", "request_id", "method", "path", "status", "duration_ms"):
            self.assertIn(field, entry)
        self.assertEqual(entry["method"], "GET")
        self.assertEqual(entry["path"], "/healthz")
        self.assertEqual(entry["status"], 200)

    def test_visitor_addresses_are_logged_only_when_turned_on(self):
        response, lines = self._captured_log(lambda: self.get("/healthz"))
        self.assertNotIn("client", self._logged(lines, response)[0])
        with patch("app.cached_setting", side_effect=lambda key: key == "logClientAddresses"):
            response, lines = self._captured_log(lambda: self.get("/healthz"))
        self.assertEqual(self._logged(lines, response)[0]["client"], "127.0.0.1")

    def test_no_response_sends_a_referrer_onward(self):
        self.assertEqual(self.get("/healthz").headers.get("Referrer-Policy"), "no-referrer")

    def test_the_logged_request_id_is_returned_to_the_caller(self):
        """A user quoting the header lands on the exact server-side record."""
        response, lines = self._captured_log(lambda: self.get("/healthz"))
        header_id = response.headers.get("X-Request-Id")
        self.assertTrue(header_id)
        logged = self._logged(lines, response)
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["request_id"], header_id)

    def test_request_ids_differ_between_requests(self):
        first = self.get("/healthz").headers.get("X-Request-Id")
        second = self.get("/healthz").headers.get("X-Request-Id")
        self.assertNotEqual(first, second)

    def test_a_query_string_is_never_written_to_the_log(self):
        """This line is meant to be safe to paste into a bug report, so the part
        of the URL a caller controls freely does not go into it."""
        response, lines = self._captured_log(
            lambda: self.get("/api/v1/catalog?token=super-secret-value&q=private")
        )
        rendered = json.dumps(lines)
        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn("private", rendered)
        entry = self._logged(lines, response)[0]
        self.assertEqual(entry["path"], "/api/v1/catalog")

    def test_a_failing_route_logs_the_stack_and_answers_generically(self):
        """The detail stays server-side; the id is what ties the two together."""
        with patch("app.catalog_api_payload", side_effect=RuntimeError("inner detail")):
            response, lines = self._captured_log(lambda: self.get("/api/v1/catalog"))
        self.assertEqual(response.status, 500)
        self.assertNotIn("inner detail", response.body.decode())
        failures = self._logged(lines, response, event="request_failed")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_type"], "RuntimeError")
        self.assertIn("inner detail", failures[0]["traceback"])
        self.assertEqual(failures[0]["request_id"], response.headers.get("X-Request-Id"))

    def test_the_catalog_says_nothing_changed_in_a_few_bytes(self):
        """Gate 4 (2026-10-05): a page polling every 5 s fetched the whole
        catalog -- 18 MB of JSON on the owner's library -- each time."""
        header = lambda response: next((v for k, v in response.headers.items() if k.lower() == "etag"), None)  # noqa: E731
        first = self.get("/api/v1/catalog")
        self.assertEqual(first.status, 200)
        etag = header(first)
        self.assertTrue(etag and etag.startswith('W/"'), etag)
        self.assertNotIn("etag", first.json(), "the tag travels as a header, not in the body")
        again = self.get("/api/v1/catalog", headers={"If-None-Match": etag})
        self.assertEqual(again.status, 304)
        self.assertEqual(again.body, b"")
        self.assertEqual(header(again), etag)
        stale = self.get("/api/v1/catalog", headers={"If-None-Match": 'W/"something-else"'})
        self.assertEqual(stale.status, 200)
        # A change to what it is made of is a new tag.
        with patch("app.APP_BUILD", "another-build"):
            changed = self.get("/api/v1/catalog", headers={"If-None-Match": etag})
        self.assertEqual(changed.status, 200)
        self.assertNotEqual(header(changed), etag)

    def test_health_reports_the_running_version_and_build(self):
        body = self.get("/healthz").json()
        self.assertEqual(body["version"], app.APP_VERSION)
        self.assertTrue(body["build"])
        self.assertEqual(body["catalog"], "ok")

    def test_health_is_unhealthy_when_the_catalog_cannot_be_read(self):
        """Gate 4 (2026-10-05): a container whose catalog never opened -- a
        root-owned /config -- answered 200 here and 500 everywhere else, and
        Docker called it healthy."""
        with tempfile.TemporaryDirectory() as folder, \
                patch("app.catalog_database_path", return_value=Path(folder) / "missing" / "flipparr.db"):
            response = self.get("/healthz")
        self.assertEqual(response.status, 503)
        body = json.loads(response.body)
        self.assertEqual(body["status"], "unhealthy")
        self.assertTrue(body["catalog"])

    def test_providers_needing_an_account_say_where_to_get_one(self):
        """An account is the one prerequisite the setup screen cannot satisfy,
        so the provider that needs it carries the link."""
        providers = {p["id"]: p for p in self.get("/api/v1/providers").json()["providers"]}
        self.assertEqual({providers["anthropic"]["kind"], providers["openai"]["kind"]}, {"reading"},
                         "help for the reader, not a source of metadata")
        self.assertEqual(providers["metron"]["kind"], "metadata")
        for provider_id in ("metron", "comic_vine", "anthropic", "openai"):
            with self.subTest(provider=provider_id):
                self.assertTrue(providers[provider_id]["credentialUrl"].startswith("https://"))
                self.assertTrue(providers[provider_id]["credentialHelp"])
        # built-ins need no account, so they offer no link
        self.assertIsNone(providers["gcd"]["credentialUrl"])
        self.assertIsNone(providers["open_library"]["credentialUrl"])

    def test_setup_completion_is_stored_with_the_instance(self):
        """Browser storage would re-prompt on a second device."""
        self.assertFalse(self.get("/api/v1/settings").json()["setupCompleted"])
        saved = self.patch("/api/v1/settings", {"setupCompleted": True})
        self.assertEqual(saved.status, 200)
        self.assertTrue(self.get("/api/v1/settings").json()["setupCompleted"])
        self.patch("/api/v1/settings", {"setupCompleted": False})

    def test_library_folder_check_reports_what_is_in_a_usable_folder(self):
        library = _ROOT / "check-library" / "nested"
        library.mkdir(parents=True, exist_ok=True)
        (library / "Saga 001.cbz").write_bytes(b"x")
        (library / "Saga 002.cbr").write_bytes(b"x")
        (library / "notes.txt").write_bytes(b"x")

        response = self.post(
            "/api/v1/library-folder-check",
            {"folder": str(_ROOT / "check-library"), "recursive": True},
        )
        self.assertEqual(response.status, 200)
        body = response.json()
        self.assertEqual(body["comicCount"], 2)
        self.assertIsNone(body["countTruncatedAt"])

        shallow = self.post(
            "/api/v1/library-folder-check",
            {"folder": str(_ROOT / "check-library"), "recursive": False},
        )
        self.assertEqual(shallow.json()["comicCount"], 0)

    def test_library_folder_check_counts_what_a_scan_would_actually_import(self):
        """It counted the originals held in quarantine, promising more comics
        than the scan that follows would import."""
        library = _ROOT / "count-library"
        (library / "Series").mkdir(parents=True, exist_ok=True)
        (library / "Series" / "Saga 001.cbz").write_bytes(b"x")
        (library / "Series" / "Saga 002.cbz").write_bytes(b"x")
        for managed in (".flipparr", ".sonicboom"):
            held = library / managed / "quarantine" / "1" / "Series"
            held.mkdir(parents=True, exist_ok=True)
            (held / "Replaced 003.cbz").write_bytes(b"x")

        counted = self.post(
            "/api/v1/library-folder-check", {"folder": str(library), "recursive": True}
        ).json()["comicCount"]
        scanned = len(app.scan_folder(str(library), True))
        self.assertEqual(counted, 2)
        self.assertEqual(counted, scanned)

    def test_library_folder_check_explains_why_a_folder_is_unusable(self):
        """Setup used to accept a typo and only fail at the first scan."""
        missing = self.post("/api/v1/library-folder-check", {"folder": str(_ROOT / "nope")})
        self.assertEqual(missing.status, 400)
        self.assertIn("does not exist", missing.json()["error"])

        relative = self.post("/api/v1/library-folder-check", {"folder": "comics"})
        self.assertEqual(relative.status, 400)
        self.assertIn("absolute path", relative.json()["error"])

        a_file = _ROOT / "not-a-folder.cbz"
        a_file.write_bytes(b"x")
        self.assertEqual(
            self.post("/api/v1/library-folder-check", {"folder": str(a_file)}).status, 400
        )

    def test_session_cookie_is_marked_secure_behind_a_trusted_proxy(self):
        """Behind TLS the cookie must not be sendable over a plain-HTTP hop."""
        self._configure_auth(localBypass=False)
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "127.0.0.1"}):
            response = self.post(
                "/api/v1/auth/login",
                {"username": "reader", "password": "correct horse battery"},
                headers={"X-Forwarded-Proto": "https"},
            )
        self.assertEqual(response.status, 200)
        self.assertIn("Secure", response.headers.get("Set-Cookie", ""))

    def test_an_untrusted_https_claim_is_ignored_but_not_silently(self):
        """The header is a claim anyone can make, so it counts only from a named
        proxy -- and the operator is told, because the cost of the mistake is a
        cookie quietly missing Secure on a site that looks correctly served."""
        app._PROXY_HEADER_WARNED = False
        self._configure_auth(localBypass=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            response = self.post(
                "/api/v1/auth/login",
                {"username": "reader", "password": "correct horse battery"},
                headers={"X-Forwarded-Proto": "https"},
            )
        self.assertEqual(response.status, 200)
        self.assertNotIn("Secure", response.headers.get("Set-Cookie", ""))
        warnings = [
            json.loads(line) for line in buffer.getvalue().splitlines()
            if line.strip().startswith("{") and "untrusted_forwarded_proto" in line
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn("FLIPPARR_TRUSTED_PROXIES", warnings[0]["detail"])

    def test_trusted_proxies_are_read_when_used_not_at_import(self):
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "10.0.0.5, 10.0.0.6"}):
            self.assertEqual(app.trusted_proxies(), ("10.0.0.5", "10.0.0.6"))
        cleared = dict(os.environ)
        cleared.pop("FLIPPARR_TRUSTED_PROXIES", None)
        cleared.pop("COMICARR_TRUSTED_PROXIES", None)
        with patch.dict("app.os.environ", cleared, clear=True):
            self.assertEqual(app.trusted_proxies(), ())

    def test_a_trusted_proxy_may_be_named_by_its_network(self):
        """Docker hands bridge addresses out in start order, so a proxy's address
        changes across restarts while its network's subnet does not."""
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "172.30.0.0/20"}):
            self.assertTrue(app.is_trusted_proxy("172.30.0.2"))
            self.assertTrue(app.is_trusted_proxy("172.30.15.254"))
            self.assertFalse(app.is_trusted_proxy("172.30.16.1"))
            self.assertEqual(app.resolve_client_address("172.30.0.2", "203.0.113.9"), "203.0.113.9")

    def test_a_single_address_is_still_trusted_exactly(self):
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "10.0.0.5"}):
            self.assertTrue(app.is_trusted_proxy("10.0.0.5"))
            self.assertFalse(app.is_trusted_proxy("10.0.0.6"))

    def test_a_client_supplied_forwarded_for_cannot_claim_to_be_local(self):
        """nginx's $proxy_add_x_forwarded_for appends the real client to whatever
        the client sent, so the left end of the header is the client's to write.
        Only the right end, walked past our own proxies, can be believed."""
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "172.30.0.0/20"}):
            # A routable address: Python counts the documentation ranges
            # (203.0.113.0/24 and friends) as private.
            resolved = app.resolve_client_address("172.30.0.2", "127.0.0.1, 8.8.8.8")
        self.assertEqual(resolved, "8.8.8.8")
        self.assertFalse(app.is_local_address(resolved))

    def test_a_chain_of_trusted_proxies_is_walked_to_the_client(self):
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "10.0.0.0/8, 172.30.0.2"}):
            self.assertEqual(
                app.resolve_client_address("172.30.0.2", "203.0.113.9, 10.1.2.3"), "203.0.113.9")

    def test_forwarded_for_from_an_untrusted_peer_is_ignored(self):
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "172.30.0.0/20"}):
            self.assertEqual(app.resolve_client_address("203.0.113.9", "127.0.0.1"), "203.0.113.9")

    def test_a_malformed_forwarded_hop_never_resolves_to_a_local_address(self):
        """Falling back to the proxy's own, private, address would let a garbled
        header pass the local-address bypass."""
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "172.30.0.0/20"}):
            resolved = app.resolve_client_address("172.30.0.2", "not-an-address")
        self.assertFalse(app.is_local_address(resolved))

    def test_a_trusted_proxy_with_no_forwarded_header_is_the_caller(self):
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "172.30.0.0/20"}):
            self.assertEqual(app.resolve_client_address("172.30.0.5", ""), "172.30.0.5")

    def test_an_ipv4_mapped_peer_matches_an_ipv4_network(self):
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "172.30.0.0/20"}):
            self.assertTrue(app.is_trusted_proxy("::ffff:172.30.0.2"))

    def test_an_unparseable_entry_trusts_nothing_and_is_reported_once(self):
        app._BAD_PROXY_ENTRIES_WARNED.clear()
        buffer = io.StringIO()
        with patch.dict("app.os.environ", {"FLIPPARR_TRUSTED_PROXIES": "npm.local, 10.0.0.5"}):
            with contextlib.redirect_stdout(buffer):
                self.assertTrue(app.is_trusted_proxy("10.0.0.5"))
                self.assertFalse(app.is_trusted_proxy("192.168.1.1"))
        warnings = [line for line in buffer.getvalue().splitlines() if "invalid_trusted_proxy" in line]
        self.assertEqual(len(warnings), 1)
        self.assertIn("npm.local", warnings[0])

    def test_unreadable_auth_config_fails_closed_rather_than_disabling_auth(self):
        """A config written as root and read as a normal user must not silently
        turn into "authentication disabled"."""
        # A real file with its permissions removed, rather than /proc/1/mem:
        # that path exists only on Linux, so on any other platform this read
        # raised FileNotFoundError and the test passed without ever reaching
        # the unreadable-file branch it exists to cover.
        unreadable = _ROOT / "unreadable-auth.json"
        unreadable.write_text("{}")
        unreadable.chmod(0o000)

        def restore() -> None:
            unreadable.chmod(0o600)
            unreadable.unlink(missing_ok=True)

        self.addCleanup(restore)
        if os.access(unreadable, os.R_OK):
            self.skipTest("this user bypasses file permissions, so nothing is unreadable")
        with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(unreadable)}):
            with self.assertRaises(app.AuthConfigUnreadable):
                app.load_auth_config()
        response = None
        with patch("app.load_auth_config", side_effect=app.AuthConfigUnreadable("denied")):
            response = self.get("/api/v1/catalog")
            health = self.get("/healthz")
        self.assertEqual(response.status, 503)
        self.assertIn("misconfigured", response.json()["error"])
        # The health check must still answer, so the container reports the
        # problem instead of silently restarting.
        self.assertEqual(health.status, 200)

    def test_malformed_auth_config_is_also_a_hard_failure(self):
        bad = _ROOT / "broken-auth.json"
        bad.write_text("{not json")
        with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(bad)}):
            with self.assertRaises(app.AuthConfigUnreadable):
                app.load_auth_config()

    def test_missing_auth_config_is_fine_and_means_no_auth(self):
        """First run must not be a hard failure."""
        with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(_ROOT / "definitely-absent.json")}):
            self.assertEqual(app.load_auth_config()["method"], "none")

    def test_credentials_can_be_set_while_sign_in_is_still_off(self):
        """The documented order is: set a username and password, then require
        sign-in. The password used to be discarded unless the method was
        already "forms", which made that order impossible."""
        app._write_auth_config(app._auth_defaults())   # a first setup: no password yet
        self.addCleanup(lambda: app.save_auth_config({"method": "none"}))
        saved = self.post("/api/v1/auth", {
            "method": "none", "username": "setup", "password": "first-password-here"})
        self.assertEqual(saved.status, 200)
        self.assertTrue(saved.json()["configured"], "password was dropped")
        # and it is the password that was actually stored
        self.assertTrue(app.verify_password("first-password-here",
                                            app.load_auth_config()["passwordHash"]))

    def test_turning_sign_in_on_does_not_lock_out_the_caller(self):
        """Enabling auth used to 401 the very next request, stranding the user
        on the settings page they would need to undo it."""
        self.addCleanup(lambda: app.save_auth_config({"method": "none"}))
        app.save_auth_config({"method": "none", "username": "owner",
                              "password": "owner-password-1"})
        enabled = self.post("/api/v1/auth", {"method": "forms", "username": "owner", "currentPassword": "owner-password-1"})
        self.assertEqual(enabled.status, 200)
        cookie = enabled.headers.get("Set-Cookie", "")
        self.assertIn(app._SESSION_COOKIE, cookie, "no session issued on enable")
        # that session works immediately
        req = urllib.request.Request(self.base + "/api/v1/catalog")
        req.add_header("Cookie", cookie.split(";")[0])
        with urllib.request.urlopen(req, timeout=20) as response:
            self.assertEqual(response.status, 200)

    def _catalog_status(self, cookie):
        return request("GET", self.base + "/api/v1/catalog", headers={"Cookie": cookie}).status

    def test_changing_the_password_signs_other_devices_out_but_not_the_changer(self):
        """A password changed because it may have leaked must also end the
        sessions taken with it."""
        self._configure_auth(localBypass=False)
        config = app.load_auth_config()
        cookie = lambda token: f"{app._SESSION_COOKIE}={token}"
        this_device = cookie(app.issue_profile_token(app.catalog_store().user(app.ADMIN_USER_ID), config))
        other_device = cookie(app.issue_profile_token(app.catalog_store().user(app.ADMIN_USER_ID), config))
        changed = self.post("/api/v1/auth", {"username": "reader", "password": "a different password",
                                             "currentPassword": "correct horse battery"},
                            headers={"Cookie": this_device})
        self.assertEqual(changed.status, 200)
        fresh = changed.headers.get("Set-Cookie", "").split(";")[0]
        self.assertIn(app._SESSION_COOKIE, fresh, "the changer was not issued a new session")
        self.assertEqual(self._catalog_status(fresh), 200)
        self.assertEqual(self._catalog_status(other_device), 401)
        self.assertEqual(self._catalog_status(this_device), 401)

    def test_settings_that_leave_the_password_alone_keep_everyone_signed_in(self):
        self._configure_auth(localBypass=False)
        config = app.load_auth_config()
        other_device = f"{app._SESSION_COOKIE}={app.issue_profile_token(app.catalog_store().user(app.ADMIN_USER_ID), config)}"
        this_device = f"{app._SESSION_COOKIE}={app.issue_profile_token(app.catalog_store().user(app.ADMIN_USER_ID), config)}"
        toggled = self.post("/api/v1/auth", {"localBypass": False, "currentPassword": "correct horse battery"},
                            headers={"Cookie": this_device})
        self.assertEqual(toggled.status, 200)
        self.assertEqual(self._catalog_status(other_device), 200)

    def test_sign_in_settings_take_the_current_password_not_just_a_session(self):
        """Review (2026-10-06): a session opened on a shared device with a
        four-digit PIN could change the password or turn sign-in off."""
        self._configure_auth(localBypass=False)
        config = app.load_auth_config()
        session = f"{app._SESSION_COOKIE}={app.issue_profile_token(app.catalog_store().user(app.ADMIN_USER_ID), config)}"
        refused = self.post("/api/v1/auth", {"method": "none"}, headers={"Cookie": session})
        self.assertEqual(refused.status, 403)
        self.assertEqual(json.loads(refused.body)["reason"], "current_password")
        wrong = self.post("/api/v1/auth", {"method": "none", "currentPassword": "not it"}, headers={"Cookie": session})
        self.assertEqual(wrong.status, 403)
        self.assertEqual(app.load_auth_config()["method"], "forms", "nothing changed")
        right = self.post("/api/v1/auth", {"method": "none", "currentPassword": "correct horse battery"},
                          headers={"Cookie": session})
        self.assertEqual(right.status, 200)

    def test_turning_sign_in_on_or_the_bypass_off_ends_the_sessions_from_before(self):
        """Review (2026-10-06): with sign-in off, any device that reached the
        port was issued a 30-day admin cookie, and turning sign-in on left it
        good. The caller keeps a fresh one; everyone else signs in."""
        app._LOGIN_THROTTLE.clear()
        self.addCleanup(app._LOGIN_THROTTLE.clear)
        self.addCleanup(lambda: app.save_auth_config({"method": "none"}))
        app.save_auth_config({"method": "none", "username": "owner", "password": "owner-password-1"})
        before = self.get("/api/v1/catalog").headers.get("Set-Cookie", "").split(";")[0]
        self.assertIn(app._SESSION_COOKIE, before, "a household device is handed a session")
        enabled = self.post("/api/v1/auth", {"method": "forms", "username": "owner", "currentPassword": "owner-password-1"},
                            headers={"Cookie": before})
        self.assertEqual(enabled.status, 200)
        fresh = enabled.headers.get("Set-Cookie", "").split(";")[0]
        self.assertEqual(self._catalog_status(before), 401, "the session from before sign-in was on")
        self.assertEqual(self._catalog_status(fresh), 200, "the caller's new one")
        # The bypass going off is a tightening too.
        app.save_auth_config({"localBypass": True})
        config = app.load_auth_config()
        visitor = f"{app._SESSION_COOKIE}={app.issue_profile_token(app.catalog_store().user(app.ADMIN_USER_ID), config)}"
        self.assertEqual(self._catalog_status(visitor), 200)
        off = self.post("/api/v1/auth", {"localBypass": False, "currentPassword": "owner-password-1"}, headers={"Cookie": visitor})
        self.assertEqual(off.status, 200)
        self.assertEqual(self._catalog_status(visitor), 401)

    def test_a_household_device_is_not_one_when_called_by_a_name_this_server_was_not_given(self):
        """Review (2026-10-06): a page on any website can make its own name
        resolve here (DNS rebinding) and then act as the device it runs on."""
        self.addCleanup(lambda: app.save_auth_config({"method": "none"}))
        app.save_auth_config({"method": "none"})
        rebound = self.get("/api/v1/catalog", headers={"Host": "evil.example:8787"})
        self.assertEqual(rebound.status, 421)
        self.assertEqual(json.loads(rebound.body)["reason"], "host_not_allowed")
        self.assertNotIn(app._SESSION_COOKIE, rebound.headers.get("Set-Cookie", ""), "and is handed nothing")
        for host in ("nas:8787", "nas.local", "192.168.1.20:8787", "[fd00::1]:8787", "localhost"):
            self.assertEqual(self.get("/api/v1/catalog", headers={"Host": host}).status, 200, host)
        with patch.dict(app.os.environ, {"FLIPPARR_ALLOWED_HOSTS": "comics.example.org, nas.tailnet.ts.net"}):
            self.assertEqual(self.get("/api/v1/catalog", headers={"Host": "nas.tailnet.ts.net"}).status, 200)
            self.assertEqual(self.get("/api/v1/catalog", headers={"Host": "evil.example"}).status, 421)
        self.assertEqual(self.get("/healthz", headers={"Host": "evil.example"}).status, 200, "the health check needs no identity")

    def test_every_response_refuses_framing_and_sniffing(self):
        for path in ("/healthz", "/api/v1/catalog", "/"):
            headers = {k.lower(): v for k, v in self.get(path).headers.items()}
            self.assertEqual(headers.get("x-frame-options"), "DENY", path)
            self.assertEqual(headers.get("x-content-type-options"), "nosniff", path)
            self.assertIn("frame-ancestors 'none'", headers.get("content-security-policy", ""), path)

    def test_requiring_sign_in_without_a_password_is_refused_clearly(self):
        """Isolated to its own config file: the suite shares one auth.json, and a
        password stored by an earlier test would otherwise satisfy this one."""
        fresh = _ROOT / "no-password-auth.json"
        fresh.unlink(missing_ok=True)
        with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(fresh)}):
            with self.assertRaises(ValueError) as caught:
                app.save_auth_config({"method": "forms", "username": "nopass"})
        self.assertIn("password", str(caught.exception).lower())

    def test_a_password_without_a_username_is_refused(self):
        app._write_auth_config(app._auth_defaults())
        self.addCleanup(lambda: app.save_auth_config({"method": "none"}))
        refused = self.post("/api/v1/auth", {"method": "none", "username": "", "password": "orphan-password"})
        self.assertEqual(refused.status, 400)

    # ---- search for missing --------------------------------------------

    # Only a literal true starts downloads. The button passed its click
    # event as this flag once: JSON could not encode it, but a truthy value
    # that did encode would have meant "download all of them" with nobody
    # asked.
    def test_a_truthy_non_boolean_still_only_asks(self):
        for value in ("yes", 1, {"nested": True}, ["x"]):
            with self.subTest(value=value):
                with patch("app.start_missing_release_search") as search:
                    search.return_value = {"status": "confirm", "searching": 3, "detail": "x"}
                    self.post("/api/v1/requests/search-missing", {"confirmed": value})
                search.assert_called_once_with(False)

    def test_a_literal_true_confirms(self):
        with patch("app.start_missing_release_search") as search:
            search.return_value = {"status": "searching", "searching": 3, "detail": "x"}
            self.post("/api/v1/requests/search-missing", {"confirmed": True})
        search.assert_called_once_with(True)

    # ---- deleting a pull ---------------------------------------------------

    def test_deleting_a_pull_that_is_not_there_says_so(self):
        for path in ("/api/v1/requests/999999", "/api/v1/requests/999999/issues/1"):
            with self.subTest(path=path):
                response = self.delete(path)
                self.assertEqual(response.status, 400)
                self.assertIn("error", json.loads(response.body))

    def test_a_malformed_pull_path_is_not_found(self):
        self.assertEqual(self.delete("/api/v1/requests/abc").status, 404)

    # ---- series cover picker ---------------------------------------------

    # Uploading a cover returned a 422 carrying a raw errno for the life of
    # the feature, because the directory it wrote to was inside a read-only
    # image. Nothing asked these routes anything, which is how that lasted.

    def test_taking_a_set_aside_release_by_hand_is_said_in_the_request(self):
        with patch("app.grab_release_candidate", return_value={"status": "grabbed", "takenByHand": True}) as grab:
            response = self.post("/api/v1/acquisition-jobs/7/grab", {"candidateId": "aside-1", "anyway": True})
        self.assertEqual(response.status, 202)
        grab.assert_called_once_with(7, "aside-1", anyway=True)
        with patch("app.grab_release_candidate", side_effect=ValueError(
            "This release was set aside as Not this series; take it anyway to grab it",
        )) as grab:
            response = self.post("/api/v1/acquisition-jobs/7/grab", {"candidateId": "aside-1"})
        self.assertEqual(response.status, 400)
        grab.assert_called_once_with(7, "aside-1", anyway=False)
        self.assertIn("take it anyway", json.loads(response.body)["error"])

    def test_a_search_by_hand_during_an_outage_does_not_fail_the_issue(self):
        """Gate 3 (2026-10-05): the route marked the issue failed, and a failed
        issue with no release on record was never searched again."""
        import urllib.error
        for problem, said in ((urllib.error.URLError("Connection refused"), "Could not reach Prowlarr"),
                              (ConnectionResetError(54, "reset"), "Could not reach Prowlarr"),
                              (urllib.error.HTTPError("http://p", 401, "no", None, None), "Prowlarr rejected the request")):
            with self.subTest(type(problem).__name__), patch("app.catalog_store") as store, \
                    patch("app.search_release_candidates", side_effect=problem):
                response = self.post("/api/v1/acquisition-jobs/7/search", {})
                self.assertEqual(response.status, 502)
                self.assertIn(said, json.loads(response.body)["error"])
                store.return_value.update_acquisition_job.assert_not_called()

    def test_replacing_a_file_searches_for_its_own_request(self):
        """The replacement's number is not its acquisition request's."""
        with patch("app.catalog_store") as store, patch("app._start_automatic_release_grabs") as grabs:
            store.return_value.request_file_replacement.return_value = {
                "id": "8", "acquisitionRequestId": "34", "status": "wanted",
            }
            response = self.post("/api/v1/files/763/replacement", {"reason": "wrong_release"})
        self.assertEqual(response.status, 201)
        grabs.assert_called_once_with({"id": "34"})

    def test_a_comics_pages_are_listed_with_a_thumbnail_and_a_reading_url(self):
        """The page picker draws thumbnails; a reader wants the same page
        larger. Both URLs are given so neither caller has to build one."""
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "pages.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                archive.writestr("01.jpg", _png_bytes())
                archive.writestr("02.jpg", _png_bytes())
            with patch("app.catalog_store") as store:
                store.return_value.library_file_path.return_value = comic
                response = self.get("/api/v1/files/12/pages")
        self.assertEqual(response.status, 200)
        body = response.json()
        self.assertEqual(body["pageCount"], 2)
        first = body["pages"][0]
        self.assertIn("/api/v1/files/12/pages/0", first["url"])
        self.assertIn("size=read", first["readUrl"])
        self.assertNotIn("size=", first["url"])

    def test_a_page_is_served_as_an_image_and_an_unknown_size_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "pages.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                archive.writestr("01.jpg", _png_bytes())
            with patch("app.catalog_store") as store:
                store.return_value.library_file_path.return_value = comic
                with patch("app.reading_cache_dir", return_value=Path(folder) / "cache"):
                    page = self.get("/api/v1/files/12/pages/0?size=read")
                    past_the_end = self.get("/api/v1/files/12/pages/9?size=read")
                    unknown_size = self.get("/api/v1/files/12/pages/0?size=enormous")
        self.assertEqual(page.status, 200)
        self.assertEqual(page.headers["Content-Type"], "image/jpeg")
        self.assertTrue(page.body.startswith(b"\xff\xd8"), "a JPEG, whatever the page was")
        self.assertEqual(past_the_end.status, 404)
        self.assertEqual(unknown_size.status, 422)

    def _series_run(self, store, title: str) -> int:
        import sqlite3

        with sqlite3.connect(store.database_path) as connection:
            return connection.execute(
                """INSERT INTO series_runs(canonical_title, canonical_key, format, created_at, updated_at)
                   VALUES (?, ?, 'comic', ?, ?)
                   ON CONFLICT(canonical_key) DO UPDATE SET updated_at=excluded.updated_at
                   RETURNING id""",
                (title, title.lower(), "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            ).fetchone()[0]

    def _library_file(self, path: Path, run: int | None = None, issue: str | None = None) -> int:
        """A real row in the contract database, so progress can be stored
        against it: the table keys on a file id and cascades from it."""
        import sqlite3
        import app as app_module

        store = app_module.catalog_store()
        stat = path.stat()
        with sqlite3.connect(store.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            root = connection.execute(
                "INSERT INTO library_roots(path, created_at) VALUES (?, ?)"
                " ON CONFLICT(path) DO UPDATE SET path=excluded.path RETURNING id",
                (str(path.parent), "2026-01-01T00:00:00+00:00"),
            ).fetchone()[0]
            file_id = connection.execute(
                """INSERT INTO files(root_id, path, filename, extension, size_bytes, mtime_ns,
                                     fingerprint, parsed_json, result_json, created_at, updated_at)
                   VALUES (?, ?, ?, '.cbz', ?, ?, 'fingerprint', '{}', '{}', ?, ?) RETURNING id""",
                # The real size and mtime, as a scan would record them: the
                # staleness check compares them against the signature the
                # reader stores, so zeroes would read as "replaced".
                (root, str(path), path.name, stat.st_size, stat.st_mtime_ns,
                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            ).fetchone()[0]
            if run is not None:
                connection.execute(
                    """INSERT INTO file_identities(file_id, series_run_id, raw_title, normalized_title,
                                                   identity_kind, issue_number, match_basis_json, updated_at)
                       VALUES (?, ?, ?, ?, 'issue', ?, '[]', ?)""",
                    (file_id, run, path.stem, path.stem.lower(), issue, "2026-01-01T00:00:00+00:00"),
                )
            return file_id

    def test_a_reading_place_is_kept_and_the_last_page_finishes_the_comic(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "progress.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                for index in range(3):
                    archive.writestr(f"{index}.jpg", _png_bytes())
            file_id = self._library_file(comic)

            fresh = self.get(f"/api/v1/files/{file_id}/progress")
            self.assertEqual(fresh.status, 200)
            self.assertEqual(fresh.json()["page"], 0, "an unread comic opens at the cover")

            saved = self.post(f"/api/v1/files/{file_id}/progress", {"page": 1})
            self.assertEqual(saved.status, 200)
            self.assertEqual(saved.json()["pageCount"], 3, "the count is the server's to know")
            self.assertIsNone(saved.json()["finishedAt"])
            self.assertEqual(self.get(f"/api/v1/files/{file_id}/progress").json()["page"], 1)

            finished = self.post(f"/api/v1/files/{file_id}/progress", {"page": 2})
            self.assertIsNotNone(finished.json()["finishedAt"], "the last page finishes it")

            self.assertEqual(self.post(f"/api/v1/files/{file_id}/progress", {"page": 9}).status, 400)
            self.assertEqual(self.post(f"/api/v1/files/{file_id}/progress", {"page": "two"}).status, 400)

            cleared = self.post(f"/api/v1/files/{file_id}/progress", {"page": None})
            self.assertEqual(cleared.json()["page"], 0)
            self.assertFalse(cleared.json()["stale"], "cleared on purpose is not stale")

    def test_a_comic_replaced_since_it_was_read_opens_at_the_start(self):
        """The page was kept against a file that no longer exists; page 40 of
        a shorter replacement is a blank screen."""
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "replaced.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                for index in range(4):
                    archive.writestr(f"{index}.jpg", _png_bytes())
            file_id = self._library_file(comic)
            self.post(f"/api/v1/files/{file_id}/progress", {"page": 2})

            time.sleep(0.01)
            with zipfile.ZipFile(comic, "w") as archive:
                archive.writestr("0.jpg", _png_bytes())
            resumed = self.get(f"/api/v1/files/{file_id}/progress").json()
            self.assertEqual(resumed["page"], 0)
            self.assertTrue(resumed["stale"], "and it says why it starts again")

    def test_what_to_carry_on_with_is_listed_on_its_own_route(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "continue.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                for index in range(6):
                    archive.writestr(f"{index}.jpg", _png_bytes())
            file_id = self._library_file(comic)
            self.post(f"/api/v1/files/{file_id}/progress", {"page": 2})

            response = self.get("/api/v1/reading")
            self.assertEqual(response.status, 200)
            entry = next(
                (item for item in response.json()["items"] if item["fileId"] == str(file_id)), None)
            self.assertIsNotNone(entry, "a part-read comic is something to continue")
            self.assertEqual((entry["page"], entry["pageCount"]), (2, 6))
            self.assertEqual(entry["resume"], "continue")

    def test_a_run_reports_what_can_be_read_and_where_it_was_left(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "run.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                for index in range(4):
                    archive.writestr(f"{index}.jpg", _png_bytes())
            # A PDF wearing a .cbz suffix: the extension says read me, the
            # bytes say otherwise, and only the bytes are believed.
            impostor = Path(folder) / "not-a-comic.cbz"
            impostor.write_bytes(b"%PDF-1.7\n")

            import app as app_module
            store = app_module.catalog_store()
            run = self._series_run(store, "Reading Contract")
            readable = self._library_file(comic, run=run, issue="1")
            self._library_file(impostor, run=run, issue="2")

            self.post(f"/api/v1/files/{readable}/progress", {"page": 1})
            response = self.get(f"/api/v1/series/{run}/reading")
        self.assertEqual(response.status, 200)
        body = response.json()
        self.assertEqual(body["resume"]["fileId"], str(readable))
        self.assertEqual(body["resume"]["state"], "continue")
        self.assertEqual((body["resume"]["page"], body["resume"]["pageCount"]), (1, 4))
        by_id = {item["id"]: item for item in body["issues"]}
        self.assertTrue(by_id[str(readable)]["readable"])
        self.assertFalse([item for item in body["issues"] if not item["readable"]][0]["readable"],
                         "a PDF named .cbz is not offered")
        self.assertNotIn("path", by_id[str(readable)], "the library's paths stay on the server")

    def test_an_issue_or_a_run_is_marked_read_or_unread_outright(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "one.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                for index in range(3):
                    archive.writestr(f"{index}.jpg", _png_bytes())
            impostor = Path(folder) / "two.cbz"
            impostor.write_bytes(b"%PDF-1.7\n")
            import app as app_module
            store = app_module.catalog_store()
            run = self._series_run(store, "Marks Contract")
            readable = self._library_file(comic, run=run, issue="1")
            self._library_file(impostor, run=run, issue="2")
            # One issue, marked read without turning a page.
            marked = self.post(f"/api/v1/files/{readable}/progress", {"read": True})
            self.assertEqual(marked.status, 200, marked.body)
            self.assertTrue(marked.json()["finishedAt"])
            self.assertEqual((marked.json()["page"], marked.json()["pageCount"]), (2, 3), "its place is the last page")
            runs = self.get("/api/v1/reading/runs").json()["runs"][str(run)]
            self.assertEqual((runs["read"], runs["total"], runs["state"]), (1, 2, "next"),
                             "the card knows one of two is read")
            # And unread again, which forgets it.
            self.assertIsNone(self.post(f"/api/v1/files/{readable}/progress", {"read": False}).json()["finishedAt"])
            self.assertNotIn(str(run), self.get("/api/v1/reading/runs").json()["runs"])
            self.assertEqual(self.post(f"/api/v1/files/{readable}/progress", {"read": "yes"}).status, 400)
            # The whole run: the file that cannot be paged is passed over.
            whole = self.post(f"/api/v1/series/{run}/reading", {"read": True})
            self.assertEqual(whole.status, 200, whole.body)
            by_id = {item["id"]: item for item in whole.json()["issues"]}
            self.assertTrue(by_id[str(readable)]["finishedAt"])
            self.assertEqual(whole.json()["resume"]["state"], "finished")
            cleared = self.post(f"/api/v1/series/{run}/reading", {"read": False})
            self.assertIsNone({item["id"]: item for item in cleared.json()["issues"]}[str(readable)]["finishedAt"])
            self.assertEqual(self.post(f"/api/v1/series/{run}/reading", {}).status, 400)
            self.assertEqual(self.post("/api/v1/series/999999/reading", {"read": True}).status, 404)

    def test_reading_direction_is_set_beside_the_format_and_can_be_handed_back(self):
        import app as app_module

        store = app_module.catalog_store()
        run = self._series_run(store, "Direction Contract")
        self.assertEqual(self.post(f"/api/v1/series/{run}/format", {"format": "manga"}).status, 200)

        set_ltr = self.post(f"/api/v1/series/{run}/format",
                            {"format": "manga", "readingDirection": "ltr"})
        self.assertEqual(set_ltr.status, 200)
        self.assertEqual(set_ltr.json()["readingDirection"], "ltr")
        # An English edition stays flipped while it is still filed as manga.
        self.assertEqual(self.get(f"/api/v1/series/{run}/reading").json()["readingDirection"], "ltr")

        # Absent leaves it alone; an explicit null hands it back to the medium.
        kept = self.post(f"/api/v1/series/{run}/format", {"format": "manga"})
        self.assertEqual(kept.json()["readingDirection"], "ltr")
        cleared = self.post(f"/api/v1/series/{run}/format",
                            {"format": "manga", "readingDirection": None})
        self.assertIsNone(cleared.json()["readingDirection"])

        refused = self.post(f"/api/v1/series/{run}/format",
                            {"format": "manga", "readingDirection": "sideways"})
        self.assertEqual(refused.status, 400)

    def test_a_rating_is_posted_and_taken_back_the_same_way_progress_is(self):
        import app as app_module

        run = self._series_run(app_module.catalog_store(), "Rating Contract")
        given = self.post(f"/api/v1/series/{run}/rating", {"rating": 4})
        self.assertEqual(given.status, 200)
        self.assertEqual(given.json()["yourRating"], 4)

        cleared = self.post(f"/api/v1/series/{run}/rating", {"rating": None})
        self.assertEqual(cleared.status, 200)
        self.assertIsNone(cleared.json()["yourRating"])

        self.assertEqual(self.post(f"/api/v1/series/{run}/rating", {"rating": 6}).status, 400)
        self.assertEqual(self.post(f"/api/v1/series/{run}/rating", {"rating": "five"}).status, 400)
        self.assertEqual(self.post("/api/v1/series/999999/rating", {"rating": 3}).status, 404)
        self.assertEqual(self.post("/api/v1/issues/999999/rating", {"rating": 3}).status, 404)

    def test_an_unknown_run_has_nothing_to_read(self):
        self.assertEqual(self.get("/api/v1/series/999999/reading").status, 404)

    def test_the_library_reports_where_each_run_was_left_in_one_request(self):
        """One request for the whole grid: a request per card would be a
        request per card."""
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "grid.cbz"
            with zipfile.ZipFile(comic, "w") as archive:
                for index in range(8):
                    archive.writestr(f"{index}.jpg", _png_bytes())
            import app as app_module
            run = self._series_run(app_module.catalog_store(), "Grid Contract")
            file_id = self._library_file(comic, run=run, issue="4")
            self.post(f"/api/v1/files/{file_id}/progress", {"page": 3})

            response = self.get("/api/v1/reading/runs")
        self.assertEqual(response.status, 200)
        entry = response.json()["runs"][str(run)]
        self.assertEqual(entry["fileId"], str(file_id))
        self.assertEqual((entry["page"], entry["pageCount"]), (3, 8))
        self.assertEqual(entry["issueNumber"], "4")
        self.assertEqual(entry["state"], "continue")
        self.assertRegex(entry["lastReadAt"], r"^\d{4}-\d{2}-\d{2}T", "Recent sorts on it as a string")
        self.assertEqual(set(entry), {"state", "fileId", "issueNumber", "page", "pageCount", "lastReadAt", "read", "total"})
        self.assertEqual((entry["read"], entry["total"]), (0, 1), "the card's marker: none read yet of one")

    def test_a_person_can_set_a_pages_panels_and_take_them_back(self):
        """PATCH keeps rectangles as the person's, in their order; DELETE forgets them and the page is read afresh."""
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "manual.cbz"
            image = Image.new("RGB", (300, 450), "white")
            draw = ImageDraw.Draw(image)
            for box in ((20, 20, 140, 210), (160, 20, 280, 210), (20, 240, 280, 430)):
                draw.rectangle(box, fill="#444444", outline="black", width=3)
            page = io.BytesIO()
            image.save(page, format="JPEG")
            with zipfile.ZipFile(comic, "w") as archive:
                archive.writestr("0.jpg", page.getvalue())
            import app as app_module
            run = self._series_run(app_module.catalog_store(), "Manual Panel Contract")
            file_id = self._library_file(comic, run=run, issue="1")
            saved = self.patch(f"/api/v1/files/{file_id}/pages/0/panels", {"panels": [
                {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5}, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
            ]})
            kept = self.get(f"/api/v1/files/{file_id}/pages/0/panels")
            refused = self.patch(f"/api/v1/files/{file_id}/pages/0/panels", {"panels": [{"x": 0.9, "y": 0, "w": 0.5, "h": 0.5}]})
            missing = self.patch(f"/api/v1/files/{file_id}/pages/9/panels", {"panels": []})
            forgotten = self.delete(f"/api/v1/files/{file_id}/pages/0/panels")
            self.patch(f"/api/v1/files/{file_id}/pages/0/panels", {"panels": []})
            whole = self.delete(f"/api/v1/files/{file_id}/panels")
            nobody = self.delete("/api/v1/files/999999/panels")
            # The place is kept to the panel: posted with the page, answered with it.
            placed = self.post(f"/api/v1/files/{file_id}/progress", {"page": 0, "panel": 2})
            place = self.get(f"/api/v1/files/{file_id}/progress")
            odd = self.post(f"/api/v1/files/{file_id}/progress", {"page": 0, "panel": "two"})
        self.assertEqual((placed.status, placed.json()["panel"]), (200, 2))
        self.assertEqual(place.json()["panel"], 2)
        self.assertEqual(odd.status, 400)
        self.assertEqual(whole.status, 200)
        self.assertEqual(whole.json(), {"fileId": str(file_id), "forgotten": 0, "kept": 1}, "the comic's automatic readings go; a person's page stays")
        self.assertIn(nobody.status, (404, 422))
        self.assertEqual(saved.status, 200)
        self.assertEqual(saved.json()["source"], "manual")
        self.assertEqual([panel["x"] for panel in saved.json()["panels"]], [0.5, 0.0], "the person's order, not left to right")
        self.assertEqual(kept.json()["source"], "manual", "and it is what the reader gets back")
        self.assertEqual(refused.status, 400)
        self.assertEqual(missing.status, 404)
        self.assertEqual(forgotten.status, 200)
        self.assertEqual(forgotten.json()["source"], "auto", "read afresh by the automatic tiers")
        self.assertEqual(len(forgotten.json()["panels"]), 3)

    def test_a_page_answers_where_its_panels_are(self):
        """Read once from the render and kept, so the second ask is the first answer."""
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "panels.cbz"
            image = Image.new("RGB", (300, 450), "white")
            draw = ImageDraw.Draw(image)
            for box in ((20, 20, 140, 210), (160, 20, 280, 210), (20, 240, 280, 430)):
                draw.rectangle(box, fill="#444444", outline="black", width=3)
            page = io.BytesIO()
            image.save(page, format="JPEG")
            with zipfile.ZipFile(comic, "w") as archive:
                archive.writestr("0.jpg", page.getvalue())
            import app as app_module
            run = self._series_run(app_module.catalog_store(), "Panel Contract")
            file_id = self._library_file(comic, run=run, issue="1")
            response = self.get(f"/api/v1/files/{file_id}/pages/0/panels")
            again = self.get(f"/api/v1/files/{file_id}/pages/0/panels")
            missing_page = self.get(f"/api/v1/files/{file_id}/pages/9/panels")
        self.assertEqual(response.status, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"fileId", "page", "source", "segmented", "readingDirection", "panels"})
        self.assertTrue(payload["segmented"])
        self.assertEqual(len(payload["panels"]), 3)
        for panel in payload["panels"]:
            for key in ("x", "y", "w", "h"):
                self.assertGreaterEqual(panel[key], 0.0)
                self.assertLessEqual(panel[key], 1.0)
        self.assertEqual(payload["panels"][0]["id"], "0-0")
        self.assertEqual(again.json(), payload)
        self.assertEqual(missing_page.status, 404)
        self.assertEqual(self.get("/api/v1/files/999999/pages/0/panels").status, 404)

    def test_progress_can_only_be_kept_for_comics_the_library_holds(self):
        self.assertEqual(self.get("/api/v1/files/999999/progress").status, 404)
        self.assertEqual(self.post("/api/v1/files/999999/progress", {"page": 1}).status, 404)

    def test_pages_can_only_be_read_from_comics_the_library_holds(self):
        """Pages are addressed by file id through the catalog, never by a path
        in the request, so this is the whole boundary."""
        with patch("app.catalog_store") as store:
            store.return_value.library_file_path.side_effect = LookupError("not in the library")
            listing = self.get("/api/v1/files/999999/pages")
            page = self.get("/api/v1/files/999999/pages/0?size=read")
        self.assertEqual(listing.status, 404)
        self.assertEqual(page.status, 404)

    def test_an_unknown_run_cannot_be_removed(self):
        self.assertEqual(self.get("/api/v1/series/999999/removal").status, 404)
        self.assertEqual(self.delete("/api/v1/series/999999").status, 404)

    def test_an_unknown_run_cannot_be_filed_as_manga(self):
        response = self.post("/api/v1/series/999999/format", {"format": "manga"})
        self.assertEqual(response.status, 404)

    def test_an_unknown_run_has_no_synopsis(self):
        response = self.get("/api/v1/series/999999/synopsis")
        self.assertEqual(response.status, 404)
        self.assertIn("error", json.loads(response.body))

    def test_an_unknown_issue_has_no_detail(self):
        response = self.get("/api/v1/issues/999999/detail")
        self.assertEqual(response.status, 404)
        self.assertIn("error", json.loads(response.body))

    def test_a_provider_problem_is_still_a_200_with_a_status(self):
        """The blurb is decoration. An unconfigured or failing catalog must not
        reach the reader as an error on a screen that works without it."""
        with patch("app.catalog_store") as store:
            store.return_value.issue_provider_ids.return_value = {"metron": "901"}
            with patch("app._provider_credential", side_effect=ValueError("not configured")):
                response = self.get("/api/v1/issues/1/detail")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(set(payload), {
            "issueId", "status", "provider", "providerName", "storyTitles",
            "description", "creators", "pageCount", "price", "coverDate", "storeDate",
        })

    def test_an_unknown_run_has_no_cover_workbench(self):
        response = self.get("/api/v1/series/999999/cover")
        self.assertEqual(response.status, 404)
        self.assertIn("error", json.loads(response.body))

    def test_an_unknown_source_is_refused_as_a_bad_request(self):
        response = self.post("/api/v1/series/1/cover", {"source": "nonsense"})
        self.assertIn(response.status, (400, 404))
        self.assertIn("error", json.loads(response.body))

    def test_a_run_with_no_uploaded_cover_says_so_in_json(self):
        response = self.get("/api/v1/series/1/cover/image")
        self.assertEqual(response.status, 404)
        # A stack trace here would mean the handler raised rather than answered.
        self.assertIn("error", json.loads(response.body))

    def test_a_cover_upload_that_is_not_an_image_is_refused(self):
        response = self.post(
            "/api/v1/series/1/cover/upload", raw=b"not an image",
            headers={"Content-Type": "text/plain"},
        )
        self.assertIn(response.status, (404, 415))

    # ---- serving a cover out of a comic ----------------------------------

    def test_a_non_zip_comic_is_not_refused_before_it_is_opened(self):
        """The endpoint gated on the extension after the rest had moved on.

        Covers were found for every RAR comic and then refused on the way
        out, so a whole library of them showed placeholders and the browser
        console filled with 400s. Nothing in the suite asked this route
        about a file that was not a zip.
        """
        import tarfile, io, tempfile as tf
        with tf.TemporaryDirectory() as folder:
            path = Path(folder) / "Example 001.cbt"
            png = _png_bytes()
            with tarfile.open(path, "w") as archive:
                info = tarfile.TarInfo("001.png")
                info.size = len(png)
                archive.addfile(info, io.BytesIO(png))
            # The endpoint serves comics the library holds, so this one is in it.
            app.catalog_store().register_root(str(folder), True)
            response = self.get(
                "/api/file-cover?" + urllib.parse.urlencode({"path": str(path)})
            )
            self.assertNotEqual(
                response.status, 400,
                "a readable archive must not be refused for its extension",
            )
            self.assertEqual(response.status, 200)
            self.assertIn("image/jpeg", response.headers["Content-Type"])

    def test_something_that_is_not_an_archive_is_still_refused(self):
        import tempfile as tf
        with tf.TemporaryDirectory() as folder:
            path = Path(folder) / "Example 001.cbz"
            path.write_bytes(b"not an archive at all")
            app.catalog_store().register_root(str(folder), True)
            response = self.get(
                "/api/file-cover?" + urllib.parse.urlencode({"path": str(path)})
            )
            self.assertEqual(response.status, 400)

    def test_a_comic_outside_the_library_is_refused(self):
        """The path comes from the query string, so it has to be contained.

        Left open, an instance with the local-network bypass on served any
        archive on the host to anyone who could reach it.
        """
        import zipfile, tempfile as tf
        with tf.TemporaryDirectory() as folder:
            library = Path(folder) / "comics"
            library.mkdir()
            app.catalog_store().register_root(str(library), True)
            outside = Path(folder) / "elsewhere.cbz"
            png = _png_bytes()
            with zipfile.ZipFile(outside, "w") as archive:
                archive.writestr("001.png", png)
            response = self.get(
                "/api/file-cover?" + urllib.parse.urlencode({"path": str(outside)})
            )
            self.assertEqual(response.status, 403)
            self.assertNotIn(str(outside), response.body.decode())

    # ---- unfollowing --------------------------------------------------

    # Only a literal false stops a run being followed, so a stray value can
    # never quietly cancel its searches -- the mistake a click event once
    # made when it was passed as a confirm flag.

    def test_only_a_literal_false_unfollows(self):
        for value in ("false", 0, None, "", [], {}):
            with self.subTest(value=value):
                with patch("app.catalog_store") as store:
                    store.return_value.set_series_monitoring.return_value = {"ok": True}
                    self.post("/api/v1/series/1/monitoring", {"monitored": value})
                    store.return_value.stop_series_monitoring.assert_not_called()
                    store.return_value.set_series_monitoring.assert_called_once()

    def test_a_literal_false_stops_following(self):
        with patch("app.catalog_store") as store:
            store.return_value.stop_series_monitoring.return_value = {"ok": True}
            self.post("/api/v1/series/1/monitoring", {"monitored": False})
            store.return_value.stop_series_monitoring.assert_called_once_with(1)
            store.return_value.set_series_monitoring.assert_not_called()

    def test_a_collection_unfollows_the_same_way(self):
        with patch("app.catalog_store") as store:
            store.return_value.stop_collection_monitoring.return_value = {"ok": True}
            self.post("/api/v1/collections/1/monitoring", {"monitored": False})
            store.return_value.stop_collection_monitoring.assert_called_once_with(1)
            store.return_value.set_collection_monitoring.assert_not_called()

    def test_a_collection_needs_a_literal_false_too(self):
        for value in ("false", 0, None, ""):
            with self.subTest(value=value):
                with patch("app.catalog_store") as store:
                    store.return_value.set_collection_monitoring.return_value = {"ok": True}
                    self.post("/api/v1/collections/1/monitoring", {"monitored": value})
                    store.return_value.stop_collection_monitoring.assert_not_called()
                    store.return_value.set_collection_monitoring.assert_called_once()


if __name__ == "__main__":
    unittest.main()



# ---- Reader profiles ----------------------------------------------------------

def _route_matchers() -> dict[str, list[str]]:
    """Every route app.py's four handlers match, as (method -> patterns), read
    from the source so a new route cannot hide from the census."""
    import ast
    tree = ast.parse(Path(app.__file__).read_text())
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in ("_route_get", "_route_post", "_route_patch", "_route_delete")):
            continue
        method = node.name.rsplit("_", 1)[1].upper()
        patterns = found.setdefault(method, [])
        for sub in ast.walk(node):
            if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Attribute) and sub.left.attr == "path":
                comp = sub.comparators[0]
                values = [comp] if isinstance(comp, ast.Constant) else list(getattr(comp, "elts", []))
                patterns += [re.escape(v.value) for v in values if isinstance(v, ast.Constant)]
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in ("fullmatch", "match")
                    and len(sub.args) > 1 and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[1], ast.Attribute) and sub.args[1].attr == "path"):
                patterns.append(sub.args[0].value)
    return found


def _sample(pattern: str) -> str:
    """A concrete path a route pattern matches."""
    path = pattern.replace(r"(\d+)", "1").replace("([a-z_]+)", "metron").replace("([a-z0-9_]+)", "sabnzbd")
    path = re.sub(r"\(\?:[^()]*\)\?", "", path)
    path = path.replace("(issues|series)", "issues").replace("(run|arc|collection)", "run")
    return path.replace("\\", "")


# The routes a profile other than the admin can reach, by class. Everything else
# in app.py must be the admin's; growing this list is a reviewed decision.
NOT_ADMIN = {
    ("GET", "/healthz"): "public",
    ("GET", "/api/v1/auth/status"): "public",
    ("POST", "/api/v1/auth/login"): "public",
    ("POST", "/api/v1/auth/logout"): "public",
    ("GET", "/api/v1/profiles"): "household",
    ("POST", "/api/v1/profiles/switch"): "household",
    ("GET", "/api/v1/me"): "signed_in",
    ("PATCH", "/api/v1/me"): "signed_in",
    ("PATCH", "/api/v1/me/prefs"): "signed_in",
    ("GET", "/api/v1/me/activity"): "signed_in",
    ("GET", "/api/v1/me/reading-list"): "reader",
    ("POST", "/api/v1/me/reading-list/run/1"): "reader",
    ("DELETE", "/api/v1/me/reading-list/run/1"): "reader",
    ("GET", "/api/v1/profiles/1/avatar"): "picture",
    ("POST", "/api/v1/profiles/1/avatar"): "signed_in",
    ("DELETE", "/api/v1/profiles/1/avatar"): "signed_in",
    ("POST", "/api/v1/profiles/1/avatar/upload"): "signed_in",
    ("GET", "/api/v1/catalog"): "reader",
    ("GET", "/api/v1/art-swatch"): "reader",
    ("GET", "/api/file-cover"): "reader",
    ("GET", "/api/v1/files/1/cover/image"): "reader",
    ("GET", "/api/v1/series/1/cover/image"): "reader",
    ("GET", "/api/v1/series/1/backdrop"): "reader",
    ("GET", "/api/v1/series/1/synopsis"): "reader",
    ("GET", "/api/v1/issues/1/detail"): "reader",
    ("GET", "/api/v1/reading"): "reader",
    ("GET", "/api/v1/reading/runs"): "reader",
    ("GET", "/api/v1/series/1/reading"): "reader",
    ("POST", "/api/v1/series/1/reading"): "reader",
    ("GET", "/api/v1/reading-lists"): "reader",
    ("GET", "/api/v1/reading-lists/1"): "reader",
    ("PATCH", "/api/v1/reading-lists/1"): "reader",
    ("DELETE", "/api/v1/reading-lists/1"): "reader",
    ("GET", "/api/v1/reading-lists/1/backdrop"): "reader",
    ("POST", "/api/v1/reading-lists/1/backdrop"): "reader",
    ("POST", "/api/v1/reading-lists/1/reading"): "reader",
    ("POST", "/api/v1/reading-lists/manual"): "reader",
    ("POST", "/api/v1/reading-lists/1/items"): "reader",
    ("GET", "/api/v1/reading-lists/1/export"): "reader",
    ("GET", "/api/v1/reading-lists/1/cover/image"): "reader",
    ("POST", "/api/v1/reading-lists/1/cover/upload"): "reader",
    ("POST", "/api/v1/run-collections"): "reader",
    ("PATCH", "/api/v1/run-collections/1"): "reader",
    ("DELETE", "/api/v1/run-collections/1"): "reader",
    ("GET", "/api/v1/run-collections/1/backdrop"): "reader",
    ("POST", "/api/v1/run-collections/1/backdrop"): "reader",
    ("GET", "/api/v1/run-collections/1/cover/image"): "reader",
    ("POST", "/api/v1/run-collections/1/cover/upload"): "reader",
    ("GET", "/api/v1/reading/lists"): "reader",
    ("GET", "/api/v1/files/1/progress"): "reader",
    ("POST", "/api/v1/files/1/progress"): "reader",
    ("GET", "/api/v1/files/1/pages"): "reader",
    ("GET", "/api/v1/files/1/pages/1"): "reader",
    ("GET", "/api/v1/files/1/pages/1/panels"): "reader",
    ("GET", "/api/v1/panels/fixes"): "admin",
    ("GET", "/api/v1/panels/export"): "admin",
    ("POST", "/api/v1/panels/import"): "admin",
    ("POST", "/api/v1/issues/1/rating"): "reader",
    ("GET", "/api/v1/discover"): "reader",
    ("GET", "/api/v1/discover/run"): "reader",
    ("GET", "/api/v1/discover/issue"): "reader",
    ("GET", "/api/v1/discover/releases"): "reader",
    ("GET", "/api/v1/discover/arcs"): "reader",
    ("GET", "/api/v1/discover/arc-suggestions"): "reader",
    ("GET", "/api/v1/discover/arc"): "reader",
    ("GET", "/api/v1/member-requests"): "reader",
    ("POST", "/api/v1/member-requests"): "reader",
    ("POST", "/api/v1/member-requests/1/cancel"): "reader",
    ("GET", "/api/v1/notifications"): "reader",
    ("POST", "/api/v1/notifications/read"): "reader",
    ("POST", "/api/v1/notifications/clear"): "reader",
    ("POST", "/api/v1/notifications/dismissed"): "reader",
}


class TransportTests(unittest.TestCase):
    """What the production server (Waitress) does that the routes cannot:
    measured against http.server on 2026-10-04, which gave every connection
    its own thread and never dropped one that stalled."""

    @classmethod
    def setUpClass(cls):
        cls.base = _start_server()
        cls.port = int(cls.base.rsplit(":", 1)[1])

    def test_stalled_clients_neither_starve_a_real_request_nor_add_threads(self):
        before = threading.active_count()
        stalled = []
        try:
            for _ in range(60):  # past the 16 workers, within a default 256-file limit
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
                sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")  # never finished
                stalled.append(sock)
            time.sleep(0.3)
            started = time.monotonic()
            self.assertEqual(request("GET", self.base + "/healthz").status, 200)
            self.assertLess(time.monotonic() - started, 2, "a real request is not queued behind stalled ones")
            self.assertLessEqual(threading.active_count(), before + 2, "a stalled client costs no thread")
        finally:
            for sock in stalled:
                sock.close()

    def test_head_answers_like_get_without_the_body(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("HEAD", "/healthz")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            self.assertGreater(int(response.headers["Content-Length"]), 0, "the length GET would send")
        finally:
            connection.close()

    def test_the_server_names_itself_without_a_python_version(self):
        server = request("GET", self.base + "/healthz").headers.get("Server", "")
        self.assertEqual(server, "flipparr")

    def test_one_connection_carries_several_requests(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            for _ in range(3):
                connection.request("GET", "/healthz")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
        finally:
            connection.close()

    def test_a_body_past_the_in_memory_limit_still_reaches_its_route(self):
        """Waitress keeps 512 KB of a body in memory and spills the rest to a
        temporary file; a route reading it sees the whole body either way."""
        padding = b"<!--" + b"x" * (1024 * 1024) + b"-->"
        cbl = (b'<ReadingList>' + padding + b'<Name>Padded</Name><Books><Book Series="Example" Number="1" Volume="2020"/>'
               b'</Books></ReadingList>')
        raw = request("POST", self.base + "/api/v1/reading-lists/import", cbl, "application/octet-stream",
                      headers={"X-Filename": "padded.cbl"})
        self.assertEqual((raw.status, raw.json().get("name")), (201, "Padded"), raw.body[:200])

    def test_temporary_files_live_in_a_folder_of_their_own_emptied_at_start(self):
        """Only Flipparr's own folder is ever emptied: FLIPPARR_TEMP_DIR says
        where to put it, so naming /config or the library deletes nothing."""
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"FLIPPARR_TEMP_DIR": folder}):
            precious = Path(folder) / "flipparr.db"
            precious.write_bytes(b"the library")
            leftover = Path(folder) / "flipparr-tmp" / "upload-from-a-crash"
            leftover.parent.mkdir()
            leftover.write_bytes(b"x")
            made = app.serving_temp_dir()
            self.assertEqual(made, Path(folder) / "flipparr-tmp")
            self.assertEqual(list(made.iterdir()), [], "what a dead process left is cleared")
            self.assertEqual(precious.read_bytes(), b"the library", "nothing beside it is touched")

    def test_a_temporary_folder_that_cannot_be_made_does_not_stop_the_server(self):
        """A container started without its config volume has a /config it may
        not write: the server must still come up and say so (CI, 2026-10-05)."""
        with tempfile.TemporaryDirectory() as folder:
            locked = Path(folder) / "locked"
            locked.mkdir()
            locked.chmod(0o500)
            try:
                with patch.dict(os.environ, {"FLIPPARR_TEMP_DIR": str(locked)}), \
                        patch("app.log_event") as logged:
                    self.assertIsNone(app.serving_temp_dir())
            finally:
                locked.chmod(0o700)
            self.assertEqual(logged.call_args.args[0], "temp_dir_unavailable")


class RouteCensusTests(unittest.TestCase):
    def test_every_route_has_a_class_and_only_the_reviewed_ones_are_open(self):
        import access_policy
        seen = set()
        for method, patterns in _route_matchers().items():
            for pattern in patterns:
                sample = _sample(pattern)
                self.assertTrue(re.fullmatch(pattern, sample), f"{method} {pattern}: sample {sample} does not match")
                expected = NOT_ADMIN.get((method, sample), "admin")
                self.assertEqual(access_policy.route_access(method, sample), expected, f"{method} {sample}")
                seen.add((method, sample))
        # Every open route in the list is a route app.py actually has.
        missing = {key for key in NOT_ADMIN if key not in seen and key != ("POST", "/api/v1/issues/1/rating")}
        self.assertEqual(missing, set(), "listed as open but not in app.py")
        self.assertEqual(access_policy.route_access("POST", "/api/v1/series/1/rating"), "reader")
        # Anything not listed under /api is the admin's, even a route that does not exist yet.
        self.assertEqual(access_policy.route_access("GET", "/api/v1/something-new"), "admin")
        self.assertEqual(access_policy.route_access("GET", "/results"), "admin")
        self.assertEqual(access_policy.route_access("GET", "/some/app/route"), "public")


class ReaderProfileHttpTests(unittest.TestCase):
    """Profiles end to end over HTTP, each test on its own library and sign-in config."""

    @classmethod
    def setUpClass(cls):
        cls.base = _start_server()

    def setUp(self):
        folder = Path(tempfile.mkdtemp(dir=_ROOT))
        env = {"COMICARR_DATABASE": str(folder / "profiles.db"), "COMICARR_AUTH_CONFIG": str(folder / "auth.json")}
        patcher = patch.dict("app.os.environ", env)
        patcher.start()
        self.addCleanup(patcher.stop)
        app._LOGIN_THROTTLE.clear()
        self.addCleanup(app._LOGIN_THROTTLE.clear)

    def call(self, method, path, payload=None, cookies=None):
        body = json.dumps(payload).encode() if payload is not None else (b"{}" if method != "GET" else None)
        header = "; ".join(f"{name}={value}" for name, value in (cookies or {}).items())
        headers = {"Cookie": header} if header else {}
        return request(method, self.base + path, body, "application/json" if body is not None else None, headers=headers)

    def _household_with_a_reader(self, **reader):
        # Sign-in off, one profile: the admin, with no cookie at all.
        self.assertEqual(self.call("PATCH", "/api/v1/me", {"pin": "2468"}).status, 200)
        made = self.call("POST", "/api/v1/users", {"name": "Sam", "colour": "teal", **reader})
        self.assertEqual(made.status, 201, made.body)
        return made.json()["id"]

    def test_the_adder_stays_in_and_devices_that_were_the_admin_by_default_do_not(self):
        # A single-profile device is the admin, and now holds a real sign-in.
        early = self.call("GET", "/api/v1/catalog")
        self.assertEqual(early.status, 200)
        self.assertIn("flipparr_session", early.cookies)
        other_device = {"flipparr_session": early.cookies["flipparr_session"]}
        adder = self.call("PATCH", "/api/v1/me", {"pin": "2468"}, cookies=other_device)
        made = self.call("POST", "/api/v1/users", {"name": "Sam"}, cookies=other_device)
        self.assertEqual((adder.status, made.status), (200, 201))
        self.assertIn("flipparr_session", made.cookies, "the adder is signed in afresh")
        self.assertEqual(self.call("GET", "/api/v1/settings", cookies={"flipparr_session": made.cookies["flipparr_session"]}).status, 200)
        stale = self.call("GET", "/api/v1/settings", cookies=other_device)
        self.assertEqual((stale.status, stale.json()["reason"]), (401, "profile_required"),
                         "a cookie from the one-profile days no longer opens the admin")

    def test_a_reader_sees_story_arcs_and_their_own_place_but_only_the_admin_shapes_the_households(self):
        sam = self._household_with_a_reader()
        cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        shelf = self.call("GET", "/api/v1/reading-lists", cookies=cookies)
        self.assertEqual((shelf.status, shelf.json()), (200, {"lists": []}))
        places = self.call("GET", "/api/v1/reading/lists", cookies=cookies)
        self.assertEqual((places.status, places.json()), (200, {"lists": {}}))
        absent = self.call("GET", "/api/v1/reading-lists/1", cookies=cookies)
        self.assertEqual(absent.status, 404)
        marked = self.call("POST", "/api/v1/reading-lists/1/reading", {"read": True}, cookies=cookies)
        self.assertEqual(marked.status, 404, "the reader's own place, on an arc that is not there")
        self.assertEqual(self.call("POST", "/api/v1/reading-lists/1/reading", {"read": "yes"}, cookies=cookies).status, 400)
        self.assertEqual(self.call("GET", "/api/v1/reading-lists/1/backdrop", cookies=cookies).status, 404, "no arc, no background")
        for method, path in (("POST", "/api/v1/reading-lists"), ("POST", "/api/v1/reading-lists/1/pull"),
                             ("POST", "/api/v1/reading-lists/1/refresh")):
            denied = self.call(method, path, {"arcId": "482"}, cookies=cookies)
            self.assertEqual((denied.status, denied.json().get("reason")), (403, "admin_only"), f"{method} {path}")
        for method, path in (("PATCH", "/api/v1/reading-lists/1"), ("DELETE", "/api/v1/reading-lists/1"),
                             ("POST", "/api/v1/reading-lists/1/backdrop")):
            self.assertEqual(self.call(method, path, {"name": "x"}, cookies=cookies).status, 404, f"{method} {path}: no arc")
        self.assertEqual(self.call("POST", "/api/v1/reading-lists/import", {"url": "https://example.invalid/x.cbl"}, cookies=cookies).json().get("reason"), "admin_only")
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        self.assertEqual(self.call("POST", "/api/v1/reading-lists", {"arcId": "not-an-id"}, cookies=admin).status, 400)
        self.assertEqual(self.call("POST", "/api/v1/reading-lists/import", {"url": "ftp://x/y.cbl"}, cookies=admin).status, 400)
        cbl = (b'<ReadingList><Name>Two</Name><Books><Book Series="Example" Number="1" Volume="2020"/>'
               b'<Book Series="Example" Number="2" Volume="2020"/></Books></ReadingList>')
        raw = request("POST", self.base + "/api/v1/reading-lists/import", cbl, "application/octet-stream",
                      headers={"X-Filename": "two.cbl", "Cookie": "; ".join(f"{k}={v}" for k, v in admin.items())})
        self.assertEqual((raw.status, raw.json()["name"], raw.json()["issueCount"]), (201, "Two", 2), raw.body)
        self.assertEqual(self.call("GET", "/api/v1/reading-lists", cookies=cookies).json()["lists"][0]["name"], "Two", "the reader sees it")
        for method, path in (("PATCH", "/api/v1/reading-lists/1"), ("DELETE", "/api/v1/reading-lists/1"),
                             ("POST", "/api/v1/reading-lists/1/backdrop")):
            refused = self.call(method, path, {"name": "Mine now", "source": "auto"}, cookies=cookies)
            self.assertEqual(refused.status, 403, f"{method} {path}: the household's arc is the admin's")
        self.assertEqual(self.call("PATCH", "/api/v1/reading-lists/1", {"shared": True}, cookies=admin).status, 400,
                         "the household's arcs are everyone's already")
        # A cover is one of the arc's own comics or the picture it came with, never a typed link.
        for bad in ("https://evil.invalid/x.jpg", "javascript:alert(1)", "/api/v1/files/999/pages/0"):
            self.assertEqual(self.call("PATCH", "/api/v1/reading-lists/1", {"cover": bad}, cookies=admin).status, 400, bad)
        background = self.call("GET", "/api/v1/reading-lists/1/backdrop", cookies=cookies)
        self.assertEqual((background.status, background.json()["source"]), (200, "none"), "nothing is here to take a page from")
        self.assertEqual(self.call("POST", "/api/v1/reading-lists/1/backdrop", {"fileId": "9", "page": 0}, cookies=admin).status, 400)
        self.assertEqual(self.call("POST", "/api/v1/reading-lists/1/backdrop", {"fileId": "9", "page": "0"}, cookies=admin).status, 400)
        renamed = self.call("PATCH", "/api/v1/reading-lists/1", {"name": "Two, renamed"}, cookies=admin)
        self.assertEqual((renamed.status, renamed.json()["name"]), (200, "Two, renamed"))
        self.assertEqual(self.call("PATCH", "/api/v1/reading-lists/1", {"order": "1,2"}, cookies=admin).status, 400, "an order is a list")
        self.assertEqual(self.call("DELETE", "/api/v1/reading-lists/1", cookies=admin).status, 200)
        self.assertEqual(self.call("DELETE", "/api/v1/reading-lists/1", cookies=admin).status, 404, "gone")

    def test_a_reader_cannot_be_added_while_the_admin_is_one_tap_away(self):
        refused = self.call("POST", "/api/v1/users", {"name": "Sam"})
        self.assertEqual(refused.status, 400)
        self.assertIn("PIN", refused.json()["error"])

    def test_a_shared_device_asks_who_is_reading_and_the_admin_needs_their_pin(self):
        sam = self._household_with_a_reader()
        anonymous = self.call("GET", "/api/v1/catalog")
        self.assertEqual((anonymous.status, anonymous.json()["reason"]), (401, "profile_required"))
        status = self.call("GET", "/api/v1/auth/status").json()
        self.assertEqual((status["profileRequired"], status["authenticated"], status["household"]), (True, False, True))
        profiles = self.call("GET", "/api/v1/profiles").json()["profiles"]
        self.assertEqual([(p["name"], p["lock"]) for p in profiles], [("Admin", "pin"), ("Sam", "open")])
        self.assertNotIn("loginName", profiles[0], "the picker shows no sign-in names")
        # Sam taps their own profile: no PIN, in.
        switched = self.call("POST", "/api/v1/profiles/switch", {"userId": sam})
        self.assertEqual(switched.status, 200)
        cookies = switched.cookies
        self.assertIn("flipparr_session", cookies)
        catalog = self.call("GET", "/api/v1/catalog", cookies=cookies)
        self.assertEqual(catalog.status, 200)
        self.assertEqual((catalog.json()["inbox"], catalog.json()["roots"]), ([], []), "no admin workbench")
        # The admin's things are the admin's.
        for method, path in (("GET", "/api/v1/settings"), ("GET", "/api/v1/users"),
                             ("POST", "/api/v1/requests"), ("DELETE", "/api/v1/series/1")):
            denied = self.call(method, path, {} if method != "GET" else None, cookies=cookies)
            self.assertEqual((denied.status, denied.json().get("reason")), (403, "admin_only"), f"{method} {path}")
        # Switching to the admin: not without the PIN.
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": 1}, cookies=cookies).status, 403)
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "1111"}).status, 403)
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"})
        self.assertEqual(admin.status, 200)
        self.assertEqual(self.call("GET", "/api/v1/settings", cookies=admin.cookies).status, 200)

    def test_each_profile_chooses_to_open_with_a_tap_a_pin_or_a_password(self):
        sam = self._household_with_a_reader()
        def lock_of(user_id):
            return {p["id"]: p["lock"] for p in self.call("GET", "/api/v1/profiles").json()["profiles"]}[user_id]
        sam_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        # A password to open with needs no sign-in name: it is asked only on the picker.
        chose = self.call("PATCH", "/api/v1/me", {"switchLock": "password", "password": "sams password"}, cookies=sam_cookies)
        self.assertEqual((chose.status, chose.json()["lock"], chose.json()["loginName"]), (200, "password", None), chose.body)
        self.assertEqual(lock_of(sam), "password")
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).status, 403)
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": sam, "pin": "1234"}).status, 403)
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": sam, "password": "sams password"}).status, 200)
        sam_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam, "password": "sams password"}).cookies
        # A PIN, chosen with the PIN it opens with; a lock without its secret is refused.
        self.assertEqual(self.call("PATCH", "/api/v1/me", {"switchLock": "pin"}, cookies=sam_cookies).status, 400)
        pin = self.call("PATCH", "/api/v1/me", {"switchLock": "pin", "pin": "1357"}, cookies=sam_cookies)
        self.assertEqual((pin.status, pin.json()["lock"]), (200, "pin"))
        # A new PIN ends the sessions opened with the old one; the chooser is
        # handed a fresh one in the same answer (review, 2026-10-06).
        self.assertIn("flipparr_session", pin.cookies)
        self.assertEqual(self.call("GET", "/api/v1/me", cookies=sam_cookies).status, 401)
        sam_cookies = {**sam_cookies, **pin.cookies}
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": sam, "password": "sams password"}).status, 403)
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": sam, "pin": "1357"}).status, 200)
        # Open again, keeping both secrets: a tap.
        opened = self.call("PATCH", "/api/v1/me", {"switchLock": "open"}, cookies=sam_cookies)
        self.assertEqual((opened.status, opened.json()["lock"], opened.json()["hasPin"]), (200, "open", True))
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).status, 200)
        # The admin is never one tap away while there are readers.
        admin_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        refused = self.call("PATCH", "/api/v1/me", {"switchLock": "open"}, cookies=admin_cookies)
        self.assertEqual(refused.status, 400)
        self.assertEqual(self.call("PATCH", "/api/v1/me", {"switchLock": "password"}, cookies=admin_cookies).status, 400,
                         "no password to open with until one is set in Security")
        self.assertEqual(self.call("PATCH", "/api/v1/me", {"pin": None}, cookies=admin_cookies).status, 400)
        self.assertEqual(lock_of(1), "pin")
        # The admin sets a reader's lock too.
        by_admin = self.call("PATCH", f"/api/v1/users/{sam}", {"switchLock": "pin"}, cookies=admin_cookies)
        self.assertEqual((by_admin.status, by_admin.json()["lock"]), (200, "pin"))

    def _library_of_one_run(self):
        """A library with one run of three issues, in the database the server uses."""
        from catalog_store import CatalogStore
        root = Path(os.environ["COMICARR_DATABASE"]).parent / "library"
        root.mkdir(exist_ok=True)
        parsed = []
        for number in ("1", "2", "3"):
            (root / f"Example 00{number}.cbz").write_bytes(b"comic")
            parsed.append(app.ParsedFile(str(root / f"Example 00{number}.cbz"), f"Example 00{number}.cbz", ".cbz",
                                         "Example", issue=number))
        store = CatalogStore(Path(os.environ["COMICARR_DATABASE"]))
        store.perform_scan(store.begin_scan(str(root), True), lambda *_: parsed, lambda p: {
            "parsed": p.__dict__, "lookup_identity": p.__dict__, "embedded_metadata": {},
            "file_health": {"status": "ok"},
            "recommendation": {"title": "Example", "issue": p.issue, "record_type": "single_issue",
                               "publisher": "Example Press", "source": "Test"},
        })
        return store, int(store.catalog()["series"][0]["id"])

    def test_a_readers_request_waits_for_the_admin_and_approving_is_the_admins_own_call(self):
        store, run = self._library_of_one_run()
        sam = self._household_with_a_reader()
        sam_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        admin_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        asked = self.call("POST", "/api/v1/member-requests", {"kind": "run", "seriesId": run}, cookies=sam_cookies)
        self.assertEqual((asked.status, asked.json()["status"], asked.json()["title"]), (201, "pending", "Example"), asked.body)
        again = self.call("POST", "/api/v1/member-requests", {"kind": "run", "seriesId": run}, cookies=sam_cookies)
        self.assertEqual((again.status, again.json()["id"]), (200, asked.json()["id"]), "asking twice is one request")
        # Pending is inert.
        self.assertEqual(store.catalog()["requests"], [])
        self.assertNotEqual(store.catalog()["series"][0]["monitoringStatus"], "monitored")
        # The reader sees their own request and none of the admin's queue.
        catalog = self.call("GET", "/api/v1/catalog", cookies=sam_cookies).json()
        self.assertEqual([r["title"] for r in catalog["memberRequests"]], ["Example"])
        self.assertEqual(catalog["stats"].get("pendingRequests", 0), 0)
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=admin_cookies).json()["stats"]["pendingRequests"], 1)
        # Deciding is the admin's.
        refused = self.call("POST", f"/api/v1/member-requests/{asked.json()['id']}/approve", cookies=sam_cookies)
        self.assertEqual((refused.status, refused.json()["reason"]), (403, "admin_only"))
        approved = self.call("POST", f"/api/v1/member-requests/{asked.json()['id']}/approve", cookies=admin_cookies)
        self.assertEqual(approved.status, 200, approved.body)
        self.assertEqual((approved.json()["status"], approved.json()["seriesRunId"]), ("approved", run))
        self.assertEqual(store.catalog()["series"][0]["monitoringStatus"], "monitored", "the admin's own Follow")
        self.assertEqual([str(r["id"]) for r in store.catalog()["requests"]], [str(approved.json()["acquisitionRequestId"])])
        self.assertEqual(self.call("POST", f"/api/v1/member-requests/{asked.json()['id']}/approve",
                                   cookies=admin_cookies).status, 409, "decided once")
        already = self.call("POST", "/api/v1/member-requests", {"kind": "run", "seriesId": run}, cookies=sam_cookies)
        self.assertEqual(already.json()["status"], "already")
        # Declined with a reason; cancelled by the asker, and only by them.
        found = {"kind": "discover_run", "provider": "metron", "providerSeriesId": "4242", "title": "Found",
                 "cover": "javascript:alert(1)"}
        declined = self.call("POST", "/api/v1/member-requests", found, cookies=sam_cookies).json()
        self.assertIsNone(declined["detail"]["cover"], "only a provider's https picture")
        answer = self.call("POST", f"/api/v1/member-requests/{declined['id']}/decline", {"reason": "Not now"},
                           cookies=admin_cookies).json()
        self.assertEqual((answer["status"], answer["declineReason"]), ("declined", "Not now"))
        issues = {**found, "kind": "discover_issues", "numbers": ["2", "1"]}
        waiting = self.call("POST", "/api/v1/member-requests", issues, cookies=sam_cookies).json()
        self.assertEqual(waiting["targetKey"], "discover:metron:4242#1,2")
        self.assertEqual(self.call("POST", f"/api/v1/member-requests/{waiting['id']}/cancel", cookies=admin_cookies).status,
                         404, "not the admin's to cancel")
        self.assertEqual(self.call("POST", f"/api/v1/member-requests/{waiting['id']}/cancel",
                                   cookies=sam_cookies).json()["status"], "cancelled")
        # Requests turned off; and a trusted reader's go straight through.
        self.call("PATCH", f"/api/v1/users/{sam}", {"canRequest": False}, cookies=admin_cookies)
        self.assertEqual(self.call("POST", "/api/v1/member-requests", found, cookies=sam_cookies).status, 403)
        store.stop_series_monitoring(run)
        self.call("PATCH", f"/api/v1/users/{sam}", {"canRequest": True, "autoApprove": True}, cookies=admin_cookies)
        trusted = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        straight = self.call("POST", "/api/v1/member-requests", {"kind": "run", "seriesId": run}, cookies=trusted).json()
        self.assertEqual((straight["status"], straight["decidedBy"]), ("approved", sam))

    def test_each_profile_hears_what_arrived_for_it_and_how_its_requests_went(self):
        store, run = self._library_of_one_run()
        sam = self._household_with_a_reader()
        sam_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        admin_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        bell = lambda cookies: self.call("GET", "/api/v1/notifications", cookies=cookies).json()
        self.assertEqual(bell(sam_cookies)["items"], [], "a new library announces nothing it already had")
        asked = self.call("POST", "/api/v1/member-requests", {"kind": "run", "seriesId": run}, cookies=sam_cookies).json()
        approved = self.call("POST", f"/api/v1/member-requests/{asked['id']}/approve", cookies=admin_cookies).json()
        found = {"kind": "discover_run", "provider": "metron", "providerSeriesId": "4242", "title": "Found"}
        other = self.call("POST", "/api/v1/member-requests", found, cookies=sam_cookies).json()
        self.call("POST", f"/api/v1/member-requests/{other['id']}/decline", {"reason": "Not now"}, cookies=admin_cookies)
        # Two issues of the run Sam asked for arrive, one from a pack.
        long_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
        with sqlite3.connect(os.environ["COMICARR_DATABASE"]) as connection:
            jobs = []
            for number in ("4", "5"):
                issue = connection.execute(
                    "INSERT INTO issues(series_run_id, issue_number, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (run, number, long_ago, long_ago)).lastrowid
                jobs.append((connection.execute(
                    """INSERT INTO acquisition_jobs(request_id, issue_id, status, created_at, updated_at)
                       VALUES (?, ?, 'fulfilled', ?, ?)""",
                    (approved["acquisitionRequestId"], issue, long_ago, long_ago)).lastrowid, issue))
            download = connection.execute(
                """INSERT INTO acquisition_downloads(job_id, sab_nzo_id, release_title, status, created_at,
                                                     updated_at, imported_at)
                   VALUES (?, 'nzo-1', 'Example 004-005', 'imported', ?, ?, ?)""",
                (jobs[0][0], long_ago, long_ago, long_ago)).lastrowid
            connection.execute(
                """INSERT INTO acquisition_download_imports(download_id, job_id, issue_id, destination, imported_at)
                   VALUES (?, ?, ?, '/comics/Example 005.cbz', ?)""", (download, jobs[1][0], jobs[1][1], long_ago))
        store.set_notification_mark("arrivals", "2000-01-01T00:00:00+00:00")
        # Sam is limited before the sweep: the run is unrated, so the line is
        # hidden -- but recorded, and shown once the rating allows it.
        self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "teen"}, cookies=admin_cookies)
        limited = bell(sam_cookies)
        self.assertEqual([item["title"] for item in limited["items"]], ["Found was declined", "Example was approved"])
        store.record_run_rating(run, "teen", "metron", "Teen (issue 1)")
        mine = bell(sam_cookies)
        self.assertEqual([item["title"] for item in mine["items"]],
                         ["Found was declined", "Example was approved", "Example: 2 new issues"],
                         "newest first, and the issues arrived a minute ago")
        self.assertEqual((mine["items"][2]["detail"], mine["items"][2]["target"]),
                         ("#4 and #5", {"view": "series", "seriesId": str(run)}))
        self.assertIsNone(mine["items"][2]["cover"], "no file is linked to these issues yet")
        self.assertEqual(mine["items"][0]["detail"], "Not now")
        self.assertEqual(mine["unread"], 3)
        admins = bell(admin_cookies)
        self.assertEqual([item["title"] for item in admins["items"]], ["Example: 2 new issues"],
                         "the admin hears of arrivals, not of their own decisions")
        self.assertEqual(bell(sam_cookies)["unread"], 3, "sweeping again announces nothing twice")
        # Read and cleared are each profile's own.
        self.call("POST", "/api/v1/notifications/clear", {"ids": [admins["items"][0]["id"]]}, cookies=sam_cookies)
        self.assertEqual(len(bell(admin_cookies)["items"]), 1, "Sam cannot clear the admin's")
        read = self.call("POST", "/api/v1/notifications/read", {"all": True}, cookies=sam_cookies).json()
        self.assertEqual((read["unread"], len(read["items"])), (0, 3))
        cleared = self.call("POST", "/api/v1/notifications/clear", {"ids": [mine["items"][0]["id"]]}, cookies=sam_cookies)
        self.assertEqual([item["title"] for item in cleared.json()["items"]], ["Example was approved", "Example: 2 new issues"])
        self.assertEqual(self.call("POST", "/api/v1/notifications/clear", {"all": True}, cookies=sam_cookies).json()["items"], [])
        self.assertEqual(self.call("POST", "/api/v1/notifications/read", {"ids": "all"}, cookies=sam_cookies).status, 400)
        self.assertEqual(self.call("POST", "/api/v1/notifications/read", {}, cookies=sam_cookies).status, 400,
                         "nothing named is not everything")
        self.assertEqual(self.call("POST", "/api/v1/notifications/clear", {"ids": [True]}, cookies=sam_cookies).status, 400)
        # What was dismissed of the things needing attention follows the profile.
        dismissed = self.call("POST", "/api/v1/notifications/dismissed", {"add": ["job:9", "metadata-match:1:0"]},
                              cookies=admin_cookies).json()["dismissed"]
        self.assertEqual(dismissed, ["job:9", "metadata-match:1:0"])
        self.call("POST", "/api/v1/notifications/dismissed", {"remove": ["job:9"]}, cookies=admin_cookies)
        self.assertEqual(bell(admin_cookies)["dismissed"], ["metadata-match:1:0"])
        self.assertEqual(bell(sam_cookies)["dismissed"], [])

    def test_a_profile_with_a_rating_limit_sees_only_what_is_within_it(self):
        store, run = self._library_of_one_run()
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        # The admin reads everything, and cannot be limited.
        self.assertEqual(self.call("PATCH", "/api/v1/users/1", {"maxRating": "everyone"}, cookies=admin).status, 400)
        limited = self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "everyone"}, cookies=admin).json()
        self.assertEqual((limited["maxRating"], limited["allowUnrated"], limited["canDiscover"]), ("everyone", False, False),
                         "a first limit keeps the profile out of Discover too")
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        file_id = store.catalog()["series"][0]["fileDetails"][0]["id"]
        def sees():
            catalog = self.call("GET", "/api/v1/catalog", cookies=reader).json()
            # A reading route about the run, and one about its file: both answer
            # as if absent when the run is hidden (the file here is no real
            # archive, so a visible one answers 422, not 404).
            reading = self.call("GET", f"/api/v1/series/{run}/reading", cookies=reader).status
            pages = self.call("GET", f"/api/v1/files/{file_id}/pages", cookies=reader).status
            return [item["id"] for item in catalog["series"]], reading if pages == 404 or reading == 404 else 200
        self.assertEqual(sees(), ([], 404), "unrated is hidden, and its pages answer as if absent")
        store.record_run_rating(run, "mature", "metron", "Mature (issue 1)")
        self.assertEqual(sees(), ([], 404))
        self.assertEqual(self.call("GET", f"/api/v1/series/{run}/reading", cookies=reader).status, 404)
        self.assertEqual(self.call("POST", "/api/v1/member-requests", {"kind": "run", "seriesId": run}, cookies=reader).status,
                         404, "nothing to ask for that it cannot see")
        # The admin's word: this run is for everyone.
        self.assertEqual(self.call("POST", f"/api/v1/series/{run}/age-rating", {"rating": "everyone"}, cookies=admin).status, 200)
        visible, pages = sees()
        self.assertEqual((visible, pages), ([str(run)], 200))
        self.assertEqual(self.call("POST", f"/api/v1/series/{run}/age-rating", {"rating": "everyone"}, cookies=reader).status,
                         403, "a reader cannot rate a run")
        # Discover is off for it; on again when the admin says so.
        self.assertEqual(self.call("GET", "/api/v1/discover/releases", cookies=reader).json().get("reason"), "discover_off")
        self.assertEqual(self.call("POST", "/api/v1/member-requests", {"kind": "discover_run", "provider": "metron",
                                   "providerSeriesId": "1", "title": "X"}, cookies=reader).status, 403)
        self.call("PATCH", f"/api/v1/users/{sam}", {"canDiscover": True}, cookies=admin)
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertNotEqual(self.call("GET", "/api/v1/discover/releases", cookies=reader).status, 403)
        # Unrated allowed; and the admin, unlimited, sees everything throughout.
        store.set_run_rating_override(run, None)
        store.record_run_rating(run, None, None, None)
        self.call("PATCH", f"/api/v1/users/{sam}", {"allowUnrated": True}, cookies=admin)
        self.assertEqual(sees()[0], [str(run)])
        self.assertEqual(len(self.call("GET", "/api/v1/catalog", cookies=admin).json()["series"]), 1)
        summary = self.call("GET", "/api/v1/ratings", cookies=admin).json()
        self.assertEqual((summary["runs"], summary["rated"]), (1, 0))

    def _two_runs(self):
        """Two runs of one issue each: Example (unrated) and Other."""
        from catalog_store import CatalogStore
        root = Path(os.environ["COMICARR_DATABASE"]).parent / "two-runs"
        root.mkdir(exist_ok=True)
        parsed = []
        for title in ("Example", "Other"):
            (root / f"{title} 001.cbz").write_bytes(b"comic")
            parsed.append(app.ParsedFile(str(root / f"{title} 001.cbz"), f"{title} 001.cbz", ".cbz", title, issue="1"))
        store = CatalogStore(Path(os.environ["COMICARR_DATABASE"]))
        store.perform_scan(store.begin_scan(str(root), True), lambda *_: parsed, lambda p: {
            "parsed": p.__dict__, "lookup_identity": p.__dict__, "embedded_metadata": {}, "file_health": {"status": "ok"},
            "recommendation": {"title": p.series, "issue": p.issue, "record_type": "single_issue",
                               "publisher": "Example Press", "source": "Test"},
        })
        ids = {item["title"]: int(item["id"]) for item in store.catalog()["series"]}
        return store, ids["Example"], ids["Other"]

    def test_any_profile_makes_its_own_collection_and_a_reader_sees_only_what_they_may(self):
        store, example, other = self._two_runs()
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        made = self.call("POST", "/api/v1/run-collections", {"name": "Essentials", "seriesIds": [example, other]}, cookies=admin)
        self.assertEqual(made.status, 201, made.body)
        collection = made.json()
        self.assertEqual(collection["runIds"], [str(example), str(other)])
        self.assertEqual((collection["mine"], collection["shared"], collection["editable"]), (True, False, True),
                         "a new collection is its maker's, private, as a hand-made arc")
        second = self.call("POST", "/api/v1/run-collections", {"name": "Also", "seriesIds": [other]}, cookies=admin)
        self.assertEqual(second.status, 201, "a run can be in several collections")
        self.assertEqual(self.call("POST", "/api/v1/run-collections", {"name": "essentials!"}, cookies=admin).status,
                         409, "a name is taken however it is spelled")
        self.assertEqual(self.call("POST", "/api/v1/run-collections", {"name": "Ghosts", "seriesIds": [99999]}, cookies=admin).status, 409)
        self.assertEqual(self.call("POST", "/api/v1/run-collections", {"name": "x"}, cookies=admin).status, 400)
        path = f"/api/v1/run-collections/{collection['id']}"
        reordered = self.call("PATCH", path, {"order": [other, example], "coverSeriesId": example, "summary": "Start here"}, cookies=admin)
        self.assertEqual((reordered.status, reordered.json()["runIds"], reordered.json()["coverSeriesId"]),
                         (200, [str(other), str(example)], str(example)))
        self.assertEqual(self.call("PATCH", path, {"order": [example]}, cookies=admin).status, 409, "an order must be every member")
        self.assertEqual(self.call("PATCH", "/api/v1/run-collections/99999", {"name": "Nope"}, cookies=admin).status, 404)
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertEqual(self.call("PATCH", path, {"name": "Mine"}, cookies=reader).status, 404,
                         "someone else's private collection is not there")
        self.assertEqual([item["name"] for item in self.call("GET", "/api/v1/catalog", cookies=reader).json()["runCollections"]], [])
        self.assertEqual(self.call("PATCH", path, {"shared": True}, cookies=admin).json()["shared"], True)
        seen = {item["name"]: item for item in self.call("GET", "/api/v1/catalog", cookies=reader).json()["runCollections"]}
        self.assertEqual((seen["Essentials"]["mine"], seen["Essentials"]["editable"], seen["Essentials"]["ownerName"]),
                         (False, False, "Admin"))
        self.assertEqual(self.call("PATCH", path, {"name": "Mine"}, cookies=reader).status, 403, "shared is not theirs to change")
        self.assertEqual(self.call("DELETE", path, cookies=reader).status, 403)
        # Sam's own: private, a name of their own even if the household has it.
        own = self.call("POST", "/api/v1/run-collections", {"name": "Essentials", "seriesIds": [other]}, cookies=reader)
        self.assertEqual(own.status, 201, own.body)
        own_path = f"/api/v1/run-collections/{own.json()['id']}"
        self.assertNotIn(own.json()["id"], [item["id"] for item in self.call("GET", "/api/v1/catalog", cookies=admin).json()["runCollections"]],
                         "not even the admin sees a profile's private collection")
        self.assertEqual(self.call("PATCH", own_path, {"order": [other]}, cookies=admin).status, 404)
        self.assertEqual(self.call("PATCH", own_path, {"shared": True}, cookies=reader).status, 200)
        self.assertEqual(self.call("PATCH", own_path, {"name": "Taken"}, cookies=admin).status, 403,
                         "the admin reads a shared one but does not change it")
        taken_back = self.call("PATCH", own_path, {"shared": False}, cookies=admin)
        self.assertEqual((taken_back.status, taken_back.json().get("visible")), (200, False), "the admin may unshare it")
        self.assertEqual(self.call("DELETE", own_path, cookies=reader).status, 200)
        # The household's (made before schema 63): the admin's to change.
        household = store.create_run_collection("Shelf", [example])
        self.assertEqual(self.call("PATCH", f"/api/v1/run-collections/{household['id']}", {"name": "Shelf"}, cookies=reader).status, 403)
        self.assertEqual(self.call("PATCH", f"/api/v1/run-collections/{household['id']}", {"shared": True}, cookies=admin).status, 400)
        self.assertEqual(self.call("PATCH", f"/api/v1/run-collections/{household['id']}", {"summary": "x"}, cookies=admin).status, 200)
        store.delete_run_collection(int(household["id"]))
        # A reader limited to Everyone, unrated allowed, with Example rated Mature.
        self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "everyone", "allowUnrated": True}, cookies=admin)
        store.set_run_rating_override(example, "mature")
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        seen = {item["name"]: item for item in self.call("GET", "/api/v1/catalog", cookies=reader).json()["runCollections"]}
        self.assertEqual(seen["Essentials"]["runIds"], [str(other)], "the Mature member is not there for them")
        self.assertIsNone(seen["Essentials"]["coverSeriesId"], "nor is its cover")
        self.assertEqual(self.call("POST", "/api/v1/run-collections", {"name": "Peek", "seriesIds": [example]}, cookies=reader).status,
                         409, "nor can a run above the limit go into theirs")
        store.set_run_rating_override(other, "mature")
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=reader).json()["runCollections"], [],
                         "a collection with nothing they may see is not there at all")
        self.assertEqual(self.call("DELETE", path, cookies=admin).status, 200)
        self.assertEqual([item["name"] for item in store.run_collections()], ["Also"])

    def test_a_collection_takes_a_header_page_from_its_own_comics(self):
        store, example, other = self._two_runs()
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        collection = self.call("POST", "/api/v1/run-collections", {"name": "Both", "seriesIds": [example, other]}, cookies=admin).json()
        path = f"/api/v1/run-collections/{collection['id']}/backdrop"
        found = self.call("GET", path, cookies=admin)
        self.assertEqual(found.status, 200, found.body)
        self.assertIn(found.json()["source"], {"auto", "none"})
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertEqual(self.call("GET", path, cookies=reader).status, 404, "a private collection's page is not there")
        self.assertEqual(self.call("POST", path, {"fileId": "99999", "page": 0}, cookies=admin).status, 400)
        self.call("PATCH", f"/api/v1/run-collections/{collection['id']}", {"shared": True}, cookies=admin)
        self.assertEqual(self.call("GET", path, cookies=reader).status, 200)
        self.assertEqual(self.call("POST", path, {"source": "auto"}, cookies=reader).status, 403)
        self.assertEqual(self.call("POST", path, {"source": "auto"}, cookies=admin).status, 200)

    def test_a_reading_list_is_the_profiles_own_and_never_holds_what_it_may_not_see(self):
        store, example, other = self._two_runs()
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        collection = self.call("POST", "/api/v1/run-collections", {"name": "Both", "seriesIds": [example, other]}, cookies=admin).json()
        self.assertEqual(self.call("POST", f"/api/v1/me/reading-list/run/{example}", cookies=admin).status, 200)
        added = self.call("POST", f"/api/v1/me/reading-list/collection/{collection['id']}", cookies=admin).json()
        self.assertEqual(([item["id"] for item in added["runs"]], [item["id"] for item in added["collections"]]),
                         ([str(example)], [collection["id"]]))
        self.assertEqual(self.call("POST", "/api/v1/me/reading-list/run/99999", cookies=admin).status, 404)
        # Sam's list is Sam's.
        self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "everyone", "allowUnrated": True}, cookies=admin)
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertEqual(self.call("GET", "/api/v1/me/reading-list", cookies=reader).json(), {"runs": [], "arcs": [], "collections": []})
        self.assertEqual(self.call("POST", f"/api/v1/me/reading-list/run/{other}", cookies=reader).status, 200)
        store.set_run_rating_override(example, "mature")
        self.assertEqual(self.call("POST", f"/api/v1/me/reading-list/run/{example}", cookies=reader).status, 404,
                         "a run above the limit cannot be added, as if it were not there")
        store.set_run_rating_override(other, "mature")
        self.assertEqual(self.call("GET", "/api/v1/me/reading-list", cookies=reader).json()["runs"], [],
                         "one rated above the limit since is not listed")
        self.assertEqual(self.call("POST", f"/api/v1/me/reading-list/collection/{collection['id']}", cookies=reader).status, 404)
        self.assertEqual(self.call("DELETE", f"/api/v1/me/reading-list/run/{other}", cookies=reader).status, 200)
        mine = self.call("GET", "/api/v1/me/reading-list", cookies=admin).json()
        self.assertEqual([item["id"] for item in mine["runs"]], [str(example)], "the admin's list was untouched")

    def test_an_arcs_maker_writes_its_description_and_chooses_its_order(self):
        store, example, other = self._two_runs()
        issues = store.issues_by_run([example, other])
        first, second = (int(issues[str(run)][0][0]) for run in (example, other))
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        arc = self.call("POST", "/api/v1/reading-lists/manual", {"name": "Mine", "issueIds": [first, second]}, cookies=reader).json()
        path = f"/api/v1/reading-lists/{arc['id']}"
        changed = self.call("PATCH", path, {"description": "Read this first", "sortMode": "release"}, cookies=reader)
        self.assertEqual(changed.status, 200, changed.body)
        self.assertEqual((changed.json()["description"], changed.json()["sortMode"]), ("Read this first", "release"))
        self.assertEqual(self.call("PATCH", path, {"sortMode": "title"}, cookies=reader).status, 400)
        self.assertEqual(self.call("PATCH", path, {"description": 5}, cookies=reader).status, 400)
        self.call("PATCH", path, {"shared": True}, cookies=reader)
        self.assertEqual(self.call("PATCH", path, {"description": "Mine now"}, cookies=admin).status, 403)

    def test_any_profile_makes_its_own_story_arc_and_shares_it_with_the_household(self):
        store, example, other = self._two_runs()
        issues = store.issues_by_run([example, other])
        first, second = (int(issues[str(run)][0][0]) for run in (example, other))
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        alex = self.call("POST", "/api/v1/users", {"name": "Alex", "colour": "teal"}, cookies=admin).json()["id"]
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertEqual(self.call("POST", "/api/v1/reading-lists/manual", {"name": "  "}, cookies=reader).status, 400)
        made = self.call("POST", "/api/v1/reading-lists/manual", {"name": "My order", "issueIds": [second]}, cookies=reader)
        self.assertEqual(made.status, 201, made.body)
        arc = made.json()
        self.assertEqual((arc["source"], arc["shared"], arc["mine"], arc["editable"], arc["issueCount"]),
                         ("manual", False, True, True, 1))
        path = f"/api/v1/reading-lists/{arc['id']}"
        added = self.call("POST", f"{path}/items", {"issueIds": [first, second]}, cookies=reader).json()
        self.assertEqual((added["added"], added["skipped"]), ([str(first)], [str(second)]), "an issue is in an arc once")
        self.assertEqual([item["issueId"] for item in added["items"]], [str(second), str(first)], "added at the end")
        self.assertEqual(self.call("POST", f"{path}/items", {"issueIds": [99999]}, cookies=reader).status, 404)
        self.assertEqual(self.call("POST", f"{path}/items", {"issueIds": []}, cookies=reader).status, 400)
        items = [item["id"] for item in added["items"]]
        ordered = self.call("PATCH", path, {"order": list(reversed(items))}, cookies=reader).json()
        self.assertEqual([item["issueId"] for item in ordered["items"]], [str(first), str(second)])
        empty = self.call("POST", "/api/v1/reading-lists/manual", {"name": "Later"}, cookies=reader)
        self.assertEqual((empty.status, empty.json()["issueCount"]), (201, 0), "an arc can start empty")
        # Private: neither the admin nor another reader knows it is there.
        alex_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": alex}).cookies
        for cookies in (admin, alex_cookies):
            self.assertNotIn("My order", [item["name"] for item in self.call("GET", "/api/v1/reading-lists", cookies=cookies).json()["lists"]])
            self.assertEqual(self.call("GET", path, cookies=cookies).status, 404)
            self.assertEqual(self.call("GET", f"{path}/export", cookies=cookies).status, 404)
            self.assertEqual(self.call("PATCH", path, {"name": "Taken"}, cookies=cookies).status, 404)
            self.assertEqual(self.call("POST", f"/api/v1/me/reading-list/arc/{arc['id']}", cookies=cookies).status, 404)
        self.assertEqual(self.call("PATCH", path, {"shared": "yes"}, cookies=reader).status, 400)
        self.assertTrue(self.call("PATCH", path, {"shared": True}, cookies=reader).json()["shared"])
        # Shared: everyone reads it, with whose it is; only Sam changes it.
        seen = {item["name"]: item for item in self.call("GET", "/api/v1/reading-lists", cookies=alex_cookies).json()["lists"]}
        self.assertEqual((seen["My order"]["ownerName"], seen["My order"]["editable"], seen["My order"]["mine"]),
                         ("Sam", False, False))
        self.assertNotIn("Later", seen, "sharing one arc shares only that one")
        self.assertEqual(self.call("POST", f"{path}/items", {"issueIds": [first]}, cookies=alex_cookies).status, 403)
        self.assertEqual(self.call("PATCH", path, {"name": "Taken"}, cookies=admin).status, 403, "not even the admin rewrites it")
        self.assertEqual(self.call("DELETE", path, cookies=alex_cookies).status, 403)
        self.assertEqual(self.call("POST", f"/api/v1/me/reading-list/arc/{arc['id']}", cookies=alex_cookies).status, 200)
        exported = self.call("GET", f"{path}/export", cookies=alex_cookies)
        self.assertEqual(exported.status, 200)
        self.assertIn(b'<Book Series="Other" Number="1"', exported.body)
        self.assertEqual(self.call("GET", f"{path}/export?format=pdf", cookies=alex_cookies).status, 400)
        self.assertEqual(json.loads(self.call("GET", f"{path}/export?format=json", cookies=reader).body)["listDetails"]["name"], "My order")
        # The admin keeps the household: a shared arc can be taken back out.
        self.assertFalse(self.call("PATCH", path, {"shared": False}, cookies=admin).json()["shared"])
        self.assertEqual(self.call("GET", path, cookies=alex_cookies).status, 404)
        self.assertEqual(self.call("GET", "/api/v1/me/reading-list", cookies=alex_cookies).json()["arcs"], [],
                         "an arc no longer shared leaves the reading list it was on")
        self.assertEqual(self.call("DELETE", path, cookies=reader).status, 200, "its maker deletes it")
        self.assertEqual(self.call("GET", path, cookies=reader).status, 404)
        # A profile that goes takes its own arcs with it.
        self.assertEqual(self.call("DELETE", f"/api/v1/users/{sam}", cookies=admin).status, 200)
        self.assertEqual([item["name"] for item in store.reading_lists_overview()], [])

    def _png(self):
        import io
        from PIL import Image
        out = io.BytesIO()
        Image.new("RGB", (20, 30), (200, 40, 40)).save(out, format="PNG")
        return out.getvalue()

    def _upload(self, path, cookies, body=None, content_type="image/png"):
        return request("POST", self.base + path, self._png() if body is None else body, content_type,
                       headers={"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())})

    def test_an_arc_takes_an_uploaded_cover_from_whoever_may_change_it(self):
        store, example, other = self._two_runs()
        first = int(store.issues_by_run([example])[str(example)][0][0])
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        arc = self.call("POST", "/api/v1/reading-lists/manual", {"name": "Mine", "issueIds": [first]}, cookies=reader).json()
        path = f"/api/v1/reading-lists/{arc['id']}"
        self.assertEqual(self._upload(f"{path}/cover/upload", reader, b"not an image", "text/plain").status, 415)
        self.assertEqual(self._upload(f"{path}/cover/upload", reader, b"not an image").status, 422)
        self.assertEqual(self._upload(f"{path}/cover/upload", admin).status, 404, "a private arc is not the admin's to see")
        uploaded = self._upload(f"{path}/cover/upload", reader)
        self.assertEqual(uploaded.status, 201, uploaded.body)
        cover = uploaded.json()["cover"]
        self.assertTrue(cover.startswith(f"{path}/cover/image?v="))
        self.assertEqual(uploaded.json()["uploadedCover"], cover)
        image = self.call("GET", cover, cookies=reader)
        self.assertEqual((image.status, image.headers.get("Content-Type")), (200, "image/jpeg"))
        self.assertEqual(self.call("GET", cover, cookies=admin).status, 404)
        # Shared, the household sees it; it is still only Sam's to change.
        self.call("PATCH", path, {"shared": True}, cookies=reader)
        self.assertEqual(self.call("GET", cover, cookies=admin).status, 200)
        self.assertEqual(self._upload(f"{path}/cover/upload", admin).status, 403)
        # Another cover chosen, the upload can be chosen back.
        self.assertEqual(self.call("PATCH", path, {"cover": "https://evil.invalid/x.jpg"}, cookies=reader).status, 400)
        self.assertEqual(self.call("PATCH", path, {"cover": cover}, cookies=reader).status, 200)
        self.assertEqual(self.call("DELETE", path, cookies=reader).status, 200)
        self.assertIsNone(app.uploaded_cover_url("arcs", arc["id"]), "the picture goes with the arc")

    def test_a_collection_takes_an_uploaded_cover_from_the_admin(self):
        store, example, other = self._two_runs()
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        collection = self.call("POST", "/api/v1/run-collections", {"name": "Both", "seriesIds": [example, other]}, cookies=admin).json()
        path = f"/api/v1/run-collections/{collection['id']}"
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertEqual(self._upload(f"{path}/cover/upload", reader).status, 404, "private: not there for them")
        self.call("PATCH", path, {"shared": True}, cookies=admin)
        self.assertEqual(self._upload(f"{path}/cover/upload", reader).status, 403, "shared: theirs to see, not change")
        self.assertEqual(self._upload("/api/v1/run-collections/99999/cover/upload", admin).status, 404)
        uploaded = self._upload(f"{path}/cover/upload", admin)
        self.assertEqual(uploaded.status, 201, uploaded.body)
        image_url = uploaded.json()["coverImage"]
        seen = {item["id"]: item for item in self.call("GET", "/api/v1/catalog", cookies=reader).json()["runCollections"]}
        self.assertEqual(seen[collection["id"]]["coverImage"], image_url, "readers see the picture too")
        self.assertEqual(self.call("GET", image_url, cookies=reader).status, 200)
        # A limited reader who may see none of its runs does not see the collection or its picture.
        self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "everyone", "allowUnrated": False}, cookies=admin)
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertEqual(self.call("GET", image_url, cookies=reader).status, 404)
        # Saving other changes keeps it; asking for coverImage null takes it away.
        kept = self.call("PATCH", path, {"name": "Both runs", "coverSeriesId": None}, cookies=admin).json()
        self.assertEqual(kept["coverImage"], image_url)
        self.assertEqual(self.call("PATCH", path, {"coverImage": "x"}, cookies=admin).status, 400)
        dropped = self.call("PATCH", path, {"coverSeriesId": str(other), "coverImage": None}, cookies=admin).json()
        self.assertEqual((dropped["coverImage"], dropped["coverSeriesId"]), (None, str(other)))
        self._upload(f"{path}/cover/upload", admin)
        self.assertEqual(self.call("DELETE", path, cookies=admin).status, 200)
        self.assertIsNone(app.uploaded_cover_url("collections", collection["id"]), "the picture goes with the collection")

    def test_the_catalog_says_when_each_issue_and_run_first_arrived(self):
        """Comics' Recently Added shelves order by these."""
        store, run = self._library_of_one_run()
        series = self.call("GET", "/api/v1/catalog").json()["series"][0]
        added = [issue["addedAt"] for issue in series["issues"]]
        self.assertTrue(all(added), "every owned issue says when its file arrived")
        self.assertEqual(series["firstAddedAt"], min(added))
        self.assertEqual(series["addedAt"], max(added), "addedAt stays the newest, for the Recent sort")

    def test_the_admin_rates_many_runs_at_once_and_a_reader_cannot(self):
        store, run = self._library_of_one_run()
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "everyone", "allowUnrated": True}, cookies=admin)
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        self.assertEqual(len(self.call("GET", "/api/v1/catalog", cookies=reader).json()["series"]), 1, "unrated, allowed")
        saved = self.call("POST", "/api/v1/ratings/runs", {"seriesIds": [run], "rating": "mature"}, cookies=admin)
        self.assertEqual((saved.status, saved.json()["count"]), (200, 1))
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=reader).json()["series"], [], "now above the limit")
        self.assertEqual(self.call("POST", "/api/v1/ratings/runs", {"seriesIds": [run], "rating": None}, cookies=reader).status,
                         403, "a reader cannot rate runs")
        self.assertEqual(self.call("POST", "/api/v1/ratings/runs", {"seriesIds": [run, 99999], "rating": "everyone"},
                                   cookies=admin).status, 409)
        self.assertEqual(store.run_age_ratings()[run], "mature", "an unknown run refuses the lot")
        for bad in ({"seriesIds": [run], "rating": "adults"}, {"seriesIds": [], "rating": "teen"}, {"seriesIds": "1"},
                    {"seriesIds": ["one"], "rating": "teen"}):
            self.assertEqual(self.call("POST", "/api/v1/ratings/runs", bad, cookies=admin).status, 400, bad)
        self.assertEqual(self.call("POST", "/api/v1/ratings/runs", {"seriesIds": [str(run)], "rating": None},
                                   cookies=admin).status, 200)
        self.assertIsNone(store.run_age_ratings()[run], "back to what was found: nothing")
        self.assertEqual(len(self.call("GET", "/api/v1/catalog", cookies=reader).json()["series"]), 1)

    def test_a_limited_reader_gets_no_trace_of_a_hidden_run(self):
        store, run = self._library_of_one_run()
        sam = self._household_with_a_reader()
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "teen"}, cookies=admin)
        store.record_run_rating(run, "mature", "metron", "Mature (issue 1)")
        # Followed, so asking for it would have said its title.
        followed = self.call("POST", "/api/v1/requests", {"scopeType": "series", "scopeId": run}, cookies=admin)
        self.assertEqual(followed.status, 201, followed.body)
        reader = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        catalog = self.call("GET", "/api/v1/catalog", cookies=reader).json()
        self.assertEqual((catalog["series"], catalog["files"], catalog["families"]), ([], [], []),
                         "the file list gave away every hidden run's title, cover and path")
        asked = self.call("POST", "/api/v1/member-requests", {"kind": "run", "seriesId": run}, cookies=reader)
        self.assertEqual(asked.status, 404)
        self.assertNotIn("Example", asked.body.decode(), "not even whether it is followed")
        # The cover route is checked by the path it serves, not the spelling asked for.
        root = Path(os.environ["COMICARR_DATABASE"]).parent / "library"
        for spelling in (root / "Example 001.cbz", root / "." / "Example 001.cbz"):
            self.assertEqual(self.call("GET", "/api/file-cover?path=" + urllib.parse.quote(str(spelling)), cookies=reader).status,
                             404, spelling)
        self.assertNotEqual(self.call("GET", "/api/file-cover?path=" + urllib.parse.quote(str(root / "Example 001.cbz")),
                                      cookies=admin).status, 404, "the admin still reaches it")

    def test_a_password_changed_on_a_shared_device_is_still_that_devices(self):
        self.call("PATCH", "/api/v1/me", {"pin": "2468"})
        app.save_auth_config({"method": "forms", "username": "owner", "password": "the admin password"})
        login = {"username": "owner", "password": "the admin password"}
        tablet = self.call("POST", "/api/v1/auth/login", login).cookies
        made = self.call("POST", "/api/v1/users", {"name": "Sam", "loginName": "sam", "password": "sams password"}, cookies=tablet)
        tablet = {**tablet, **made.cookies}
        sam_on_tablet = {**tablet, **self.call("POST", "/api/v1/profiles/switch", {"userId": made.json()["id"]}, cookies=tablet).cookies}
        changed = self.call("PATCH", "/api/v1/me", {"password": "a newer password", "currentPassword": "sams password"},
                            cookies=sam_on_tablet)
        self.assertEqual(changed.status, 200, changed.body)
        sam_on_tablet = {**sam_on_tablet, **changed.cookies}
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=sam_on_tablet).status, 200)
        laptop = self.call("POST", "/api/v1/auth/login", login).cookies
        self.assertEqual(self.call("POST", "/api/v1/devices/forget", {}, cookies=laptop).status, 200)
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=sam_on_tablet).status, 401,
                         "a new password on the tablet did not make the tablet Sam's own phone")

    def test_the_picker_knows_how_many_digits_a_pin_has(self):
        sam = self._household_with_a_reader()
        lengths = lambda: {p["name"]: p["pinLength"] for p in self.call("GET", "/api/v1/profiles").json()["profiles"]}
        self.assertEqual(lengths(), {"Admin": 4, "Sam": None}, "a tap needs no digits")
        # A PIN from before lengths were kept is learned when it is entered right.
        with sqlite3.connect(os.environ["COMICARR_DATABASE"]) as connection:
            connection.execute("UPDATE users SET pin_length=NULL WHERE id=1")
        self.assertEqual(lengths()["Admin"], None)
        self.assertEqual(self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "0000"}).status, 403)
        self.assertEqual(lengths()["Admin"], None, "not from a wrong guess")
        admin = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"})
        self.assertEqual(lengths()["Admin"], 4)
        self.call("PATCH", f"/api/v1/users/{sam}", {"switchLock": "pin", "pin": "135790"}, cookies=admin.cookies)
        self.assertEqual(lengths()["Sam"], 6)

    def test_each_profile_reads_and_rates_on_its_own(self):
        sam = self._household_with_a_reader()
        sam_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        admin_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        me = self.call("GET", "/api/v1/me", cookies=sam_cookies).json()
        self.assertEqual((me["profile"]["name"], me["prefs"]), ("Sam", {}))
        saved = self.call("PATCH", "/api/v1/me/prefs", {"panelMode": True}, cookies=sam_cookies)
        self.assertEqual(saved.json()["prefs"], {"panelMode": True})
        self.assertEqual(self.call("GET", "/api/v1/me", cookies=admin_cookies).json()["prefs"], {}, "Sam's, not the admin's")
        self.assertEqual(self.call("PATCH", "/api/v1/me/prefs", {"theme": "dark"}, cookies=sam_cookies).status, 400)
        self.assertEqual(self.call("PATCH", "/api/v1/me", {"role": "admin"}, cookies=sam_cookies).json()["role"], "reader",
                         "a reader cannot give themselves the admin's role")

    def test_the_local_network_is_a_shared_device_not_the_admin(self):
        # On a NAS every device is local. With readers, local means "ask who is reading".
        self._household_with_a_reader()
        app.save_auth_config({"method": "forms", "username": "owner", "password": "the admin password",
                              "localBypass": True})
        anonymous = self.call("GET", "/api/v1/catalog")
        self.assertEqual((anonymous.status, anonymous.json()["reason"]), (401, "profile_required"))
        self.assertEqual(self.call("GET", "/api/v1/settings").status, 401)

    def test_a_reader_signs_in_on_their_own_device_and_a_new_password_ends_the_old_sign_ins(self):
        self.call("PATCH", "/api/v1/me", {"pin": "2468"})
        app.save_auth_config({"method": "forms", "username": "owner", "password": "the admin password"})
        admin = self.call("POST", "/api/v1/auth/login", {"username": "owner", "password": "the admin password"})
        self.assertEqual(admin.status, 200)
        self.assertIn("flipparr_device", admin.cookies, "the admin's device becomes a shared one")
        made = self.call("POST", "/api/v1/users", {"name": "Sam", "loginName": "sam", "password": "sams password"},
                         cookies=admin.cookies)
        self.assertEqual(made.status, 201, made.body)
        # Adding the first reader re-issues the admin's own sign-in.
        admin_cookies = {**admin.cookies, **made.cookies}
        self.assertEqual(self.call("POST", "/api/v1/users", {"name": "Imposter", "loginName": "OWNER"},
                                   cookies=admin_cookies).status, 400, "nobody else signs in as the admin")
        sam = self.call("POST", "/api/v1/auth/login", {"username": "SAM", "password": "sams password"})
        self.assertEqual(sam.status, 200)
        self.assertNotIn("flipparr_device", sam.cookies, "a reader's own phone is not a shared device")
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=sam.cookies).status, 200)
        own_phone = self.call("GET", "/api/v1/profiles", cookies=sam.cookies)
        self.assertEqual((own_phone.status, own_phone.json()["reason"]), (403, "shared_device_only"),
                         "no picker on a reader's own phone")
        wrong = self.call("PATCH", "/api/v1/me", {"password": "a newer password", "currentPassword": "nope"},
                          cookies=sam.cookies)
        self.assertEqual(wrong.status, 403)
        changed = self.call("PATCH", "/api/v1/me", {"password": "a newer password", "currentPassword": "sams password"},
                            cookies=sam.cookies)
        self.assertEqual(changed.status, 200)
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=sam.cookies).status, 401, "the old sign-in ended")
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=changed.cookies).status, 200, "but not the changer's")
        # The admin can end a reader's sign-ins everywhere.
        self.call("POST", f"/api/v1/users/{made.json()['id']}/sign-out", cookies=admin_cookies)
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=changed.cookies).status, 401)

    def test_forgetting_the_shared_devices_ends_them_and_every_profile_opened_on_them(self):
        self.call("PATCH", "/api/v1/me", {"pin": "2468"})
        app.save_auth_config({"method": "forms", "username": "owner", "password": "the admin password"})
        login = {"username": "owner", "password": "the admin password"}
        tablet = self.call("POST", "/api/v1/auth/login", login).cookies
        sam_id = self.call("POST", "/api/v1/users", {"name": "Sam", "loginName": "sam", "password": "sams password"},
                           cookies=tablet)
        tablet = {**tablet, **sam_id.cookies}
        sam_on_tablet = {**tablet, **self.call("POST", "/api/v1/profiles/switch", {"userId": sam_id.json()["id"]},
                                               cookies=tablet).cookies}
        sams_phone = self.call("POST", "/api/v1/auth/login", {"username": "sam", "password": "sams password"}).cookies
        laptop = self.call("POST", "/api/v1/auth/login", login).cookies
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=sam_on_tablet).status, 200)
        forgot = self.call("POST", "/api/v1/devices/forget", {}, cookies=laptop)
        self.assertEqual(forgot.status, 200, forgot.body)
        laptop = {**laptop, **forgot.cookies}
        self.assertEqual(self.call("GET", "/api/v1/profiles", cookies=tablet).status, 401, "the tablet is forgotten")
        self.assertEqual(self.call("GET", "/api/v1/settings", cookies=tablet).status, 401, "and the admin's sign-in on it")
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=sam_on_tablet).status, 401, "and Sam's")
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=sams_phone).status, 200, "Sam's own phone is not shared")
        self.assertEqual(self.call("GET", "/api/v1/settings", cookies=laptop).status, 200, "the forgetter keeps their place")
        self.assertEqual(self.call("GET", "/api/v1/profiles", cookies=laptop).status, 200, "and their device stays shared")
        refused = self.call("POST", "/api/v1/devices/forget", {}, cookies=sams_phone)
        self.assertEqual(refused.status, 403, "the admin's to do")

    def test_an_uploaded_comics_name_arrives_whole_however_it_is_spelled(self):
        # A header holds only ASCII, so the page percent-encodes the name; a
        # plain name decodes to itself. "Batman – Superman – World's Finest
        # #35 (2025).cbz" failed in the browser before any request was made.
        name = "Batman – Superman – World's Finest #35 (2025).cbz"
        seen = []
        def fake_import(job_id, stream, length, filename):
            seen.append((job_id, length, filename))
            stream.read(length)
            return {"status": "imported", "file": filename}
        with patch("app.import_uploaded_comic", side_effect=fake_import):
            for header in (urllib.parse.quote(name), "Saga 001.cbz"):
                response = request("POST", self.base + "/api/v1/acquisition-jobs/7/import", b"PK\x03\x04comic",
                                   "application/octet-stream", headers={"X-Filename": header})
                self.assertEqual(response.status, 201, response.body)
        self.assertEqual([entry[2] for entry in seen], [name, "Saga 001.cbz"])

    def test_a_cookie_from_before_profiles_is_the_admins_and_is_upgraded(self):
        app.save_auth_config({"method": "forms", "username": "owner", "password": "the admin password"})
        # An install from before profiles that already had sign-in on: no
        # tightening has happened to it, so its epoch is still the first.
        app._write_auth_config({**app.load_auth_config(), "householdEpoch": 0})
        legacy = {"flipparr_session": app.issue_session_token(app.load_auth_config())}
        first = self.call("GET", "/api/v1/catalog", cookies=legacy)
        self.assertEqual(first.status, 200)
        self.assertTrue(first.cookies["flipparr_session"].startswith("v3.1."), "upgraded to a profile cookie")
        self.assertIn("flipparr_device", first.cookies)
        self.assertEqual(self.call("GET", "/api/v1/settings", cookies=first.cookies).status, 200)
        # Once the admin's sign-ins have been ended, the old kind no longer counts.
        app.catalog_store().bump_session_version(1)
        self.assertEqual(self.call("GET", "/api/v1/catalog", cookies=legacy).status, 401)

    def test_a_readers_request_never_reaches_an_admin_handler(self):
        sam = self._household_with_a_reader()
        cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        with patch("app.save_app_settings", side_effect=AssertionError("handler ran")) as saving:
            refused = request("PATCH", self.base + "/api/v1/settings", json.dumps({"autoScanEnabled": False}).encode(),
                              "application/json", headers={"Cookie": f"flipparr_session={cookies['flipparr_session']}"})
        self.assertEqual(refused.status, 403)
        saving.assert_not_called()

    def test_a_profile_has_a_picture_from_a_photo_or_the_library_and_sets_only_its_own(self):
        sam = self._household_with_a_reader()
        sam_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        admin_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        header = lambda cookies: {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()), "Content-Type": "image/png"}
        uploaded = request("POST", self.base + f"/api/v1/profiles/{sam}/avatar/upload", _png_bytes(40), headers=header(sam_cookies))
        self.assertEqual(uploaded.status, 200, uploaded.body)
        avatar = uploaded.json()["avatar"]
        self.assertTrue(avatar.startswith(f"/api/v1/profiles/{sam}/avatar?v="))
        picture = self.call("GET", avatar)
        self.assertEqual((picture.status, picture.headers["Content-Type"]), (200, "image/jpeg"),
                         "the picker shows it before anyone is signed in")
        from PIL import Image
        self.assertEqual(Image.open(io.BytesIO(picture.body)).size, (app.AVATAR_SIDE, app.AVATAR_SIDE), "square")
        self.assertEqual(self.call("GET", "/api/v1/profiles", cookies=sam_cookies).status, 200)
        listed = self.call("GET", "/api/v1/profiles").json()["profiles"]
        self.assertEqual(listed[1]["avatar"], avatar)
        # Sam cannot change the admin's picture; the admin can change Sam's.
        refused = request("POST", self.base + "/api/v1/profiles/1/avatar/upload", _png_bytes(8), headers=header(sam_cookies))
        self.assertEqual(refused.status, 403)
        with patch("app.render_file_page", return_value=_png_bytes(60)) as render:
            chosen = self.call("POST", f"/api/v1/profiles/{sam}/avatar", {"fileId": 7, "page": 0}, cookies=admin_cookies)
        self.assertEqual(chosen.status, 200, chosen.body)
        render.assert_called_once_with(7, 0)
        self.assertEqual(chosen.json()["avatarSource"], "library:7:0")
        self.assertNotEqual(chosen.json()["avatar"], avatar, "a new picture, a new address")
        cleared = self.call("DELETE", f"/api/v1/profiles/{sam}/avatar", cookies=sam_cookies)
        self.assertEqual((cleared.status, cleared.json()["avatar"]), (200, None))
        self.assertEqual(self.call("GET", avatar).status, 404)

    def test_a_limited_reader_cannot_make_a_restricted_page_their_picture(self):
        """Review (2026-10-06): the file came in the body, so the rating gate
        on the path never saw it."""
        sam = self._household_with_a_reader()
        admin_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": 1, "pin": "2468"}).cookies
        self.call("PATCH", f"/api/v1/users/{sam}", {"maxRating": "everyone"}, cookies=admin_cookies)
        sam_cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        with patch("app.render_file_page", return_value=_png_bytes(60)) as render, \
                patch.object(app.Handler, "_run_visible", return_value=False):
            refused = self.call("POST", f"/api/v1/profiles/{sam}/avatar", {"fileId": 7, "page": 3}, cookies=sam_cookies)
        self.assertEqual(refused.status, 404)
        render.assert_not_called()

    def test_a_profile_page_counts_what_its_profile_has_read(self):
        sam = self._household_with_a_reader()
        cookies = self.call("POST", "/api/v1/profiles/switch", {"userId": sam}).cookies
        activity = self.call("GET", "/api/v1/me/activity", cookies=cookies).json()
        self.assertEqual(activity["history"], [])
        self.assertEqual({key: activity["stats"][key] for key in ("issuesRead", "inProgress", "runs", "pagesRead", "rated")},
                         {"issuesRead": 0, "inProgress": 0, "runs": 0, "pagesRead": 0, "rated": 0})

