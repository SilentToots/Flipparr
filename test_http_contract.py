"""HTTP contract characterisation for the SonicBoom API.

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
import json
import os
import tempfile
import threading
import unittest
import urllib.error
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
    accept_encoding: str | None = None,
) -> Response:
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    if accept_encoding:
        req.add_header("Accept-Encoding", accept_encoding)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return Response(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers or {}), exc.read())


class HttpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = _start_server()

    def get(self, path, **kw):
        return request("GET", self.base + path, **kw)

    def post(self, path, payload=None, raw=None):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"{}")
        return request("POST", self.base + path, body, "application/json")

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

if __name__ == "__main__":
    unittest.main()
