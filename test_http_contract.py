"""HTTP contract characterisation for the Flipparr API.

These tests speak plain HTTP against a running server and assert only what a
client can observe: status code, content type, and response shape. Nothing here
references the handler class, so the suite is transport-agnostic and must pass
unchanged before and after the FastAPI port. Only `_start_server` knows which
server is under test.

Written before the port, against the stdlib ThreadingHTTPServer, so the port has
something to be verified against: the routing table is 54 branches and only one
existing test touched the transport layer at all.
"""

import gzip
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory()
_ROOT = Path(_TEMP.name)
(_ROOT / "web").mkdir()
(_ROOT / "web" / "index.html").write_text("<!doctype html><title>shell</title>")
(_ROOT / "web" / "asset.js").write_text("console.log(1)")
os.environ["COMICARR_DATABASE"] = str(_ROOT / "contract.db")
os.environ["COMICARR_WEB_ROOT"] = str(_ROOT / "web")
os.environ["COMICARR_PROVIDER_CONFIG"] = str(_ROOT / "providers.json")
os.environ["COMICARR_ACQUISITION_CONFIG"] = str(_ROOT / "services.json")
os.environ["COMICARR_SETTINGS_CONFIG"] = str(_ROOT / "settings.json")
os.environ["COMICARR_AUTH_CONFIG"] = str(_ROOT / "auth.json")

import app  # noqa: E402  (import after the environment is prepared)


def _start_server() -> str:
    """Boot the server under test and return its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _SERVERS.append(server)
    return f"http://127.0.0.1:{server.server_address[1]}"


_SERVERS: list = []


class Response:
    def __init__(self, status: int, headers, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

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
            return Response(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers or {}), exc.read())


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
        self.assertEqual(self.patch("/api/v1/settings", {"collectedEditionsEnabled": "yes"}).status, 400)

    def test_providers_and_services_list_without_credentials(self):
        for path in ("/api/v1/providers", "/api/v1/acquisition-services"):
            response = self.get(path)
            self.assertEqual(response.status, 200, path)
            body = response.body.decode().lower()
            for secret in ("apikey\":", "token\":", "password"):
                self.assertNotIn(secret, body, f"{path} leaked a credential field")

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
        response = self.get("/asset.js")
        self.assertEqual(response.status, 200)
        self.assertIn("immutable", response.headers.get("Cache-Control", ""))

    def test_unknown_client_route_falls_back_to_the_app_shell(self):
        response = self.get("/library/some/deep/route")
        self.assertEqual(response.status, 200)
        self.assertIn(b"shell", response.body)
        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")

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
        self.assertEqual(gzipped.headers.get("Vary"), "Accept-Encoding")
        # urllib does not auto-decompress, so the body is the compressed bytes.
        self.assertLess(len(gzipped.body), len(plain.body))
        self.assertEqual(gzip.decompress(gzipped.body), plain.body)

    def test_a_client_that_does_not_accept_gzip_still_gets_plain_json(self):
        response = self.get("/api/v1/catalog")
        self.assertIsNone(response.headers.get("Content-Encoding"))
        json.loads(response.body)

    def test_text_assets_are_compressed_and_marked_vary(self):
        gzipped = request("GET", self.base + "/asset.js", accept_encoding="gzip")
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
        app.save_auth_config({"method": "forms", "username": "reader",
                              "password": "correct horse battery", **patch})
        self.addCleanup(lambda: app.save_auth_config({"method": "none"}))

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
        """Run `run` with stdout captured, returning the JSON lines it emitted."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = run()
        lines = []
        for line in buffer.getvalue().splitlines():
            line = line.strip()
            if line.startswith("{"):
                lines.append(json.loads(line))
        return result, lines

    def test_every_request_is_logged_as_one_json_object(self):
        response, lines = self._captured_log(lambda: self.get("/healthz"))
        self.assertEqual(response.status, 200)
        requests = [line for line in lines if line.get("event") == "http_request"]
        self.assertEqual(len(requests), 1)
        entry = requests[0]
        for field in ("ts", "level", "event", "request_id", "method", "path", "status", "duration_ms"):
            self.assertIn(field, entry)
        self.assertEqual(entry["method"], "GET")
        self.assertEqual(entry["path"], "/healthz")
        self.assertEqual(entry["status"], 200)

    def test_the_logged_request_id_is_returned_to_the_caller(self):
        """A user quoting the header lands on the exact server-side record."""
        response, lines = self._captured_log(lambda: self.get("/healthz"))
        header_id = response.headers.get("X-Request-Id")
        self.assertTrue(header_id)
        logged = [line for line in lines if line.get("event") == "http_request"][0]
        self.assertEqual(logged["request_id"], header_id)

    def test_request_ids_differ_between_requests(self):
        first = self.get("/healthz").headers.get("X-Request-Id")
        second = self.get("/healthz").headers.get("X-Request-Id")
        self.assertNotEqual(first, second)

    def test_a_query_string_is_never_written_to_the_log(self):
        """This line is meant to be safe to paste into a bug report, so the part
        of the URL a caller controls freely does not go into it."""
        _, lines = self._captured_log(
            lambda: self.get("/api/v1/catalog?token=super-secret-value&q=private")
        )
        rendered = json.dumps(lines)
        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn("private", rendered)
        entry = [line for line in lines if line.get("event") == "http_request"][0]
        self.assertEqual(entry["path"], "/api/v1/catalog")

    def test_a_failing_route_logs_the_stack_and_answers_generically(self):
        """The detail stays server-side; the id is what ties the two together."""
        with patch("app.catalog_api_payload", side_effect=RuntimeError("inner detail")):
            response, lines = self._captured_log(lambda: self.get("/api/v1/catalog"))
        self.assertEqual(response.status, 500)
        self.assertNotIn("inner detail", response.body.decode())
        failures = [line for line in lines if line.get("event") == "request_failed"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error_type"], "RuntimeError")
        self.assertIn("inner detail", failures[0]["traceback"])
        self.assertEqual(failures[0]["request_id"], response.headers.get("X-Request-Id"))

    def test_health_reports_the_running_version_and_build(self):
        body = self.get("/healthz").json()
        self.assertEqual(body["version"], app.APP_VERSION)
        self.assertTrue(body["build"])

    def test_providers_needing_an_account_say_where_to_get_one(self):
        """An account is the one prerequisite the setup screen cannot satisfy,
        so the provider that needs it carries the link."""
        providers = {p["id"]: p for p in self.get("/api/v1/providers").json()["providers"]}
        for provider_id in ("metron", "comic_vine"):
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
        enabled = self.post("/api/v1/auth", {"method": "forms", "username": "owner"})
        self.assertEqual(enabled.status, 200)
        cookie = enabled.headers.get("Set-Cookie", "")
        self.assertIn(app._SESSION_COOKIE, cookie, "no session issued on enable")
        # that session works immediately
        req = urllib.request.Request(self.base + "/api/v1/catalog")
        req.add_header("Cookie", cookie.split(";")[0])
        with urllib.request.urlopen(req, timeout=20) as response:
            self.assertEqual(response.status, 200)

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

    def test_replacing_a_file_searches_for_its_own_request(self):
        """The replacement's number is not its acquisition request's."""
        with patch("app.catalog_store") as store, patch("app._start_automatic_release_grabs") as grabs:
            store.return_value.request_file_replacement.return_value = {
                "id": "8", "acquisitionRequestId": "34", "status": "wanted",
            }
            response = self.post("/api/v1/files/763/replacement", {"reason": "wrong_release"})
        self.assertEqual(response.status, 201)
        grabs.assert_called_once_with({"id": "34"})

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
            response = self.get(
                "/api/file-cover?" + urllib.parse.urlencode({"path": str(path)})
            )
            self.assertEqual(response.status, 400)

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

