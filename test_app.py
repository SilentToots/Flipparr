import contextlib
import datetime as dt
import json
import email.message
import io
import pathlib
import os
import sqlite3
import tempfile
from catalog_store import ADMIN_USER_ID, SCHEMA_VERSION, CatalogStore
import threading
import types
import contextlib
import unittest
import urllib.error
import urllib.parse
import traceback
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import app
from access_policy import Viewer
from app import _run_end_evidence, SABSubmissionError, UploadRedirected, CompletedDownloadNotVisible, Handler, MetadataRateLimited, PAGE, ReleaseDownloadError, _RELEASE_CANDIDATES, _auto_grab_release, _gcd_discovery_search_rows, _comic_vine_issue_entries, _hydrate_gcd_issue_entries_with_status, _metron_collected_edition_candidates, _metron_issue_entries, _metron_reprint_coverage, _resolve_sab_download_source, assess_identity_confidence, batch_enrich, catalog_api_payload, confirm_gcd_series_collection, confirm_gcd_series_run, discover_gcd_series, discover_metron_series, discover_series, embedded_epub_candidate, enrich, enrich_catalog_series, extract_issue_coverage, file_cover_info, find_archive_cover_member, archive_page_members, automatic_backdrop_page, import_downloaded_comic, inspect_file_health, inventory_file, lookup_identity, parse_filename, post_multipart_file_json, public_acquisition_service_config, public_provider_config, rank_gcd_series_runs, read_embedded_metadata, reconcile_acquisition_download, request_discovered_gcd_series, request_discovered_series, render_batch_results, run_metadata_enrichment_job, save_acquisition_service_config, save_provider_config, scan_folder, score_candidate, search_file_match_candidates, search_gcd, search_google_books, search_open_library, search_prowlarr_releases, send_release_to_sabnzbd, sync_gcd_issue_catalog, sync_issue_catalog, test_acquisition_service_connection


class FilenameParserTests(unittest.TestCase):
    def setUp(self):
        app._PROVIDER_JSON_CACHE.clear()
        app._PROVIDER_JSON_INFLIGHT.clear()
        app._PROVIDER_NEXT_REQUEST_AT.clear()
        self.provider_wait = patch("app._wait_for_provider_slot")
        self.provider_persist = patch("app.persist_provider_cache")
        self.provider_wait.start()
        self.provider_persist.start()

    def tearDown(self):
        self.provider_persist.stop()
        self.provider_wait.stop()
        _RELEASE_CANDIDATES.clear()

    def test_authenticated_provider_requests_are_cached_and_coalesced(self):
        payload = {"results": [{"id": 7}]}
        with patch("app.fetch_json_with_headers", return_value=payload) as fetch:
            first = app.fetch_provider_json(
                "metron", "https://metron.cloud/api/series/?q=Batman", "secret-token"
            )
            second = app.fetch_provider_json(
                "metron", "https://metron.cloud/api/series/?q=Batman", "secret-token"
            )

        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        fetch.assert_called_once()

    def test_a_definitive_miss_is_remembered_instead_of_asked_again(self):
        """A provider that has nothing is asked once, not once per scan."""
        missing = urllib.error.HTTPError(
            "https://www.comics.org/api/issue/999999/", 404, "Not Found", {}, None
        )
        with patch("app.fetch_json_with_headers", side_effect=missing) as fetch:
            with self.assertRaises(urllib.error.HTTPError):
                app.fetch_provider_json("gcd", "https://www.comics.org/api/issue/999999/", "")
            with self.assertRaises(urllib.error.HTTPError) as replayed:
                app.fetch_provider_json("gcd", "https://www.comics.org/api/issue/999999/", "")

        fetch.assert_called_once()
        self.assertEqual(replayed.exception.code, 404)

    def test_a_remembered_miss_expires_sooner_than_a_remembered_hit(self):
        self.assertLess(
            app._PROVIDER_NEGATIVE_TTL_SECONDS, app._PROVIDER_IDENTITY_TTL_SECONDS
        )
        missing = urllib.error.HTTPError(
            "https://www.comics.org/api/issue/999999/", 404, "Not Found", {}, None
        )
        url = "https://www.comics.org/api/issue/999999/"
        with patch("app.fetch_json_with_headers", side_effect=missing):
            with self.assertRaises(urllib.error.HTTPError):
                app.fetch_provider_json("gcd", url, "")
        key = app._provider_cache_key("gcd", url, "")
        app._PROVIDER_JSON_CACHE[key]["saved_at"] -= app._PROVIDER_NEGATIVE_TTL_SECONDS + 1

        payload = {"id": 999999}
        with patch("app.fetch_json_with_headers", return_value=payload) as fetch:
            self.assertEqual(app.fetch_provider_json("gcd", url, ""), payload)
        fetch.assert_called_once()

    def test_a_throttled_or_broken_provider_is_never_remembered_as_empty(self):
        """Caching a 429 or a 5xx would turn a passing outage into a lasting one."""
        for status, reason in ((429, "Too Many Requests"), (503, "Service Unavailable")):
            with self.subTest(status=status):
                app._PROVIDER_JSON_CACHE.clear()
                error = urllib.error.HTTPError(
                    "https://www.comics.org/api/issue/5/", status, reason, {}, None
                )
                with patch("app.fetch_json_with_headers", side_effect=error):
                    with self.assertRaises(Exception):
                        app.fetch_provider_json(
                            "gcd", "https://www.comics.org/api/issue/5/", ""
                        )
                self.assertFalse(app._PROVIDER_JSON_CACHE)

    def test_a_rejected_credential_is_never_remembered_as_empty(self):
        """A fixed API key must take effect at once, not after the miss ages out."""
        error = urllib.error.HTTPError(
            "https://metron.cloud/api/series/1/", 401, "Unauthorized", {}, None
        )
        with patch("app.fetch_json_with_headers", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                app.fetch_provider_json("metron", "https://metron.cloud/api/series/1/", "bad")
        self.assertFalse(app._PROVIDER_JSON_CACHE)

    def test_identity_records_are_cached_far_longer_than_issue_lists(self):
        """A published comic's identity does not get revised; a run's issues do."""
        identity = app._provider_cache_ttl("https://www.comics.org/api/issue/123/", "gcd")
        overview = app._provider_cache_ttl(
            "https://www.comics.org/api/series/123/overview/?format=json", "gcd"
        )
        self.assertEqual(identity, app._PROVIDER_IDENTITY_TTL_SECONDS)
        self.assertGreater(identity, overview)

    def test_provider_cache_key_never_contains_api_credentials(self):
        key = app._provider_cache_key(
            "comic_vine",
            "https://comicvine.gamespot.com/api/search/?api_key=very-secret&query=Batman",
            "very-secret",
        )

        self.assertNotIn("very-secret", key)
        self.assertNotIn("api_key", key)

    def test_legacy_transport_redacts_query_credentials_from_http_errors(self):
        secret = "legacy-query-secret-that-must-not-escape"
        request = urllib.request.Request(
            "https://service.invalid/api?api_key=" + secret + "&format=json"
        )
        original = urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, None
        )
        with patch.object(app._HTTP_OPENER, "open", side_effect=original), self.assertRaises(
            urllib.error.HTTPError
        ) as raised:
            app._safe_urlopen(request, timeout=1)
        rendered = "".join(
            traceback.format_exception(
                type(raised.exception), raised.exception, raised.exception.__traceback__
            )
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, raised.exception.geturl())
        self.assertIn("%5BREDACTED%5D", raised.exception.geturl())

    def test_an_anonymous_provider_response_is_not_mistaken_for_a_leaked_key(self):
        """GCD sends no credential. "" is a substring of every response, so an
        unguarded reflection check rejects every successful anonymous lookup."""
        payload = {"id": 42, "name": "Love and Rockets"}
        with patch("app.fetch_json_with_headers", return_value=payload):
            result = app.fetch_provider_json(
                "gcd", "https://www.comics.org/api/series/42/", ""
            )
        self.assertEqual(result, payload)

    def test_provider_reflection_is_not_cached_or_returned(self):
        secret = "provider-reflection-secret"
        reflected = {"error": "credential was " + secret}
        with patch("app.fetch_json_with_headers", return_value=reflected), self.assertRaisesRegex(
            ValueError, "unsafe credential-bearing data"
        ):
            app.fetch_provider_json(
                "comic_vine",
                "https://comicvine.gamespot.com/api/issues/?api_key=" + secret,
                secret,
                force=True,
            )
        self.assertFalse(app._PROVIDER_JSON_CACHE)

    def test_service_base_url_cannot_smuggle_or_return_a_query_secret(self):
        with self.assertRaisesRegex(ValueError, "without query parameters"):
            app._normalize_service_url(
                "http://sab:8080?apikey=must-not-be-a-base-url"
            )

    def test_comic_vine_embedded_rate_limit_enters_cooldown(self):
        payload = {"status_code": 107, "error": "Rate limit exceeded"}
        with patch("app.fetch_json_with_headers", return_value=payload), self.assertRaises(
            MetadataRateLimited
        ) as raised:
            app.fetch_provider_json(
                "comic_vine",
                "https://comicvine.gamespot.com/api/search/?api_key=secret&query=Batman",
                "secret",
            )

        self.assertEqual(raised.exception.provider, "comic_vine")
        self.assertGreater(app._PROVIDER_NEXT_REQUEST_AT["comic_vine"], 0)

    def test_fix_match_issue_search_uses_configured_provider_before_gcd(self):
        store = Mock()
        store.get_file_workbench.return_value = {
            "file": {"id": "7", "path": "/comics/Batman #01.cbz", "filename": "Batman #01.cbz"},
            "current": {
                "seriesTitle": "Batman", "title": "Batman", "recordType": "issue",
                "issueNumber": "1", "publicationYear": 2025, "publisher": "DC Comics",
                "format": "saddle-stitched",
            },
            "override": {},
        }
        store.retain_file_search_candidates.return_value = {"retained": True}
        entries = [{
            "number": "1", "provider_id": "991", "title": "Vast Colors in the Dark",
            "publication_date": "2025-03-26", "publication_year": 2025,
            "cover": "https://metron.example/batman-1.jpg",
            "api_url": "https://metron.example/issue/991/",
        }]
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[("metron", {"token": "saved"}), ("gcd", {})],
        ), patch(
            "app._metron_issue_entries",
            return_value=("2025", "https://metron.example/series/2025/", entries,
                          {"state": "unknown", "year": None}),
        ) as metron, patch("app._gcd_discovery_search_rows") as gcd:
            result = search_file_match_candidates(7, "Batman 2025")

        self.assertEqual(result, {"retained": True})
        metron.assert_called_once()
        gcd.assert_not_called()
        retained = store.retain_file_search_candidates.call_args.args
        self.assertEqual(retained[1], "Batman 2025")
        self.assertEqual(retained[2][0]["source"], "Metron")
        self.assertEqual(retained[2][0]["issue"], "1")
        self.assertEqual(retained[2][0]["subtitle"], "Vast Colors in the Dark")
        self.assertEqual(store.retain_file_search_candidates.call_args.kwargs["providers_checked"], ["Metron"])

    def test_fix_match_issue_search_falls_through_provider_errors(self):
        store = Mock()
        store.get_file_workbench.return_value = {
            "file": {"id": "7", "path": "/comics/Batman #01.cbz", "filename": "Batman #01.cbz"},
            "current": {
                "seriesTitle": "Batman", "title": "Batman", "recordType": "issue",
                "issueNumber": "1", "publicationYear": 2025, "publisher": "DC Comics",
            },
            "override": {},
        }
        store.retain_file_search_candidates.return_value = {"retained": True}
        entries = [{
            "number": "1", "provider_id": "441", "title": "Vast Colors in the Dark",
            "description": "<p>Source description with a contents statement.</p>",
            "provider_evidence": {"version": 1, "provider": "comic_vine", "providerIssueId": "441"},
            "publication_date": "2025-03-26", "publication_year": 2025,
            "cover": "https://comicvine.example/batman-1.jpg",
            "api_url": "https://comicvine.example/issue/441/",
        }]
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[
                ("metron", {"token": "saved"}),
                ("comic_vine", {"apiKey": "saved"}),
                ("gcd", {}),
            ],
        ), patch("app._metron_issue_entries", side_effect=ValueError("rate limited")), patch(
            "app._comic_vine_issue_entries",
            return_value=("88", "https://comicvine.example/volume/88/", entries),
        ), patch("app._gcd_discovery_search_rows") as gcd:
            search_file_match_candidates(7, "Batman 2025")

        gcd.assert_not_called()
        call = store.retain_file_search_candidates.call_args
        self.assertEqual(call.kwargs["providers_checked"], ["Metron", "Comic Vine"])
        self.assertEqual(call.kwargs["errors"], [{"provider": "Metron", "error": "rate limited"}])
        self.assertEqual(call.args[2][0]["source"], "Comic Vine")
        self.assertEqual(call.args[2][0]["description"], entries[0]["description"])
        self.assertEqual(call.args[2][0]["provider_evidence"], entries[0]["provider_evidence"])

    def test_fix_match_volume_stops_after_strong_structured_metron_match(self):
        store = Mock()
        store.get_file_workbench.return_value = {
            "file": {"id": "9", "path": "/comics/Alex and Ada Vol 1.cbz", "filename": "Alex and Ada Vol 1.cbz"},
            "current": {
                "seriesTitle": "Alex + Ada", "title": "Alex + Ada",
                "recordType": "collected_edition", "volumeNumber": 1,
                "publicationYear": 2014, "publisher": "Image Comics",
            },
            "override": {},
        }
        store.retain_file_search_candidates.return_value = {"retained": True}
        candidate = {
            "source": "Metron", "source_id": "900", "title": "Alex + Ada",
            "volume": 1, "match_score": 105, "cover": "https://covers/v1.jpg",
            "matched_edition": {"coverage": [{
                "series": "Alex + Ada", "issues": ["1", "2", "3", "4", "5"],
                "relation_kind": "full_issue",
            }]},
        }
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_provider_config",
            return_value={"metron": {"enabled": True, "token": "saved"}},
        ), patch(
            "app._metron_collected_edition_candidates", return_value=[candidate]
        ) as metron, patch("app.search_open_library") as open_library, patch(
            "app.search_gcd"
        ) as gcd, patch("app.discover_metron_series") as generic_metron:
            result = search_file_match_candidates(9, "Alex + Ada Volume 1")

        self.assertEqual(result, {"retained": True})
        metron.assert_called_once()
        open_library.assert_not_called()
        gcd.assert_not_called()
        generic_metron.assert_not_called()
        call = store.retain_file_search_candidates.call_args
        self.assertEqual(call.args[2], [candidate])
        self.assertEqual(call.kwargs["providers_checked"], ["Metron"])

    def test_unknown_metron_links_do_not_suppress_comic_vine_evidence(self):
        store = Mock()
        store.get_file_workbench.return_value = {
            "file": {"id": "9", "path": "/comics/Synthetic Vol 1.cbz", "filename": "Synthetic Vol 1.cbz"},
            "current": {"title": "Synthetic", "recordType": "collected_edition", "volumeNumber": 1},
            "override": {},
        }
        metron_candidate = {
            "source": "Metron", "source_id": "900", "title": "Synthetic", "match_score": 105,
            "matched_edition": {"coverage": [{"series": "Synthetic (2014)", "issues": ["1"], "relation_kind": "unknown"}]},
        }
        description = "<p>Collected edition source description.</p>"
        with patch("app.catalog_store", return_value=store), patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "saved"}, "comic_vine": {"enabled": True, "apiKey": "saved"},
        }), patch("app._metron_collected_edition_candidates", return_value=[metron_candidate]), patch(
            "app.search_open_library", return_value=[]
        ), patch("app.search_gcd", return_value=[]), patch("app.discover_metron_series", return_value={"results": []}), patch(
            "app.discover_comic_vine_series", return_value={"results": [{
                "providerName": "Comic Vine", "providerSeriesId": "20", "title": "Synthetic", "description": description,
            }]}
        ) as comic_vine:
            search_file_match_candidates(9, "Synthetic")
        comic_vine.assert_called_once()
        retained = store.retain_file_search_candidates.call_args.args[2]
        candidate = next(c for c in retained if c["source"] == "Comic Vine")
        self.assertEqual(candidate["description"], description)
        self.assertEqual(candidate["matched_edition"]["coverage"], [])

    def test_fast_inventory_uses_local_evidence_without_provider_requests(self):
        parsed = parse_filename(Path("Absolute Batman 003 (2025).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman",
            "title": "The Zoo, Part Three", "number": "3", "year": "2024",
            "publisher": "DC Comics", "contributors": {},
        }
        with patch("app.inspect_file_health", return_value={"status": "ok"}), patch(
            "app.read_embedded_metadata", return_value=embedded
        ), patch("app.file_cover_info", return_value={"available": True}), patch(
            "app.search_gcd"
        ) as gcd, patch("app.search_open_library") as open_library:
            result = inventory_file(parsed)

        self.assertEqual(result["recommendation"]["title"], "Absolute Batman")
        self.assertEqual(result["recommendation"]["issue"], "3")
        self.assertEqual(result["source_status"]["external_metadata"], "deferred during fast library inventory")
        gcd.assert_not_called()
        open_library.assert_not_called()

    def test_metadata_job_respects_provider_retry_after(self):
        store = Mock()
        job = {"id": 8, "series_run_id": 4, "attempt_count": 1}
        with patch("app.catalog_store", return_value=store), patch(
            "app.enrich_catalog_series",
            side_effect=MetadataRateLimited("gcd", 180, "Provider asked Flipparr to pause."),
        ):
            result = run_metadata_enrichment_job(job)

        self.assertEqual(result, {"status": "waiting", "provider": "gcd"})
        store.finish_metadata_enrichment_job.assert_called_once_with(
            8, "waiting", "gcd", "Provider asked Flipparr to pause.", 180
        )

    def test_series_enrichment_stops_after_first_provider_supplies_issue_list(self):
        store = Mock()
        store.get_series_sync_context.return_value = {
            "title": "Example", "year": 2024, "publisher": "Example Press",
            "metronSeriesId": None, "comicVineVolumeId": None,
        }
        store.metadata_provider_available.return_value = True
        store.apply_issue_list.return_value = {"issueCount": 2, "provider": "metron"}
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[("metron", {"token": "secret"}), ("gcd", {})],
        ), patch("app._metron_issue_entries", return_value=(
            "42", "https://metron.cloud/series/42/",
            [{"number": "1"}, {"number": "2"}], {"state": "unknown", "year": None},
        )), patch("app._apply_gcd_series_enrichment") as gcd:
            result = enrich_catalog_series(7)

        self.assertEqual((result["status"], result["provider"]), ("complete", "metron"))
        gcd.assert_not_called()
        store.apply_issue_list.assert_called_once()

    def test_catalog_pause_requires_every_enabled_provider_to_have_an_error_cooldown(self):
        store = Mock()
        store.catalog.return_value = {"enrichment": {
            "providerCooldowns": [{"provider": "gcd", "error": "rate limited"}],
        }}
        store.metadata_provider_available.side_effect = lambda provider: provider != "gcd"
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[("metron", {"token": "saved"}), ("comic_vine", {"apiKey": "saved"}), ("gcd", {})],
        ):
            app._CATALOG_CACHE.clear()  # this test's mocks, not an earlier test's catalog
            result = catalog_api_payload(viewer_id=1)

        enrichment = result["enrichment"]
        self.assertFalse(enrichment["allProvidersCooling"])
        self.assertEqual(enrichment["availableProviders"], ["metron", "comic_vine"])

    def test_a_release_refused_before_says_why_and_counts_once(self):
        """Find release said "no results" for Supergirl #2 when fifty-one came back."""
        context = {
            "id": "12", "requestId": "4", "status": "queued",
            "issueId": "8", "issueNumber": "4", "issueTitle": None,
            "publicationYear": 2025, "publicationDate": "2025-01-01",
            "seriesId": "2", "seriesTitle": "Absolute Batman",
            "seriesYear": 2024, "publisher": "DC Comics", "acquisitionPreference": "either",
        }
        store = Mock()
        store.get_acquisition_job_context.return_value = context
        store.rejected_acquisition_releases.return_value = [{
            "release_key": "old", "release_title": "Absolute Batman 004 (2025) (Digital) (ENG)",
            "error": "The download is incomplete: 3 MB of it is zero-filled\nFiles read:\n- x.cbr",
        }]
        refused = {"title": "Absolute Batman 004 (2025) (Digital) (ENG)", "protocol": "usenet",
                   "size": 1, "categories": [{"id": 7030}]}
        releases = [
            {**refused, "guid": "a", "downloadUrl": "http://prowlarr/a", "indexer": "One"},
            {**refused, "guid": "b", "downloadUrl": "http://prowlarr/b", "indexer": "Two"},
            {"guid": "c", "title": 'Week of 2025.04.16 [53/72] - yEnc "Absolute Batman 008 (2025)"',
             "protocol": "usenet", "downloadUrl": "http://prowlarr/c", "indexer": "One",
             "size": 1, "categories": [{"id": 7030}]},
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"}
            }
        ), patch("app.fetch_json_with_headers", return_value=releases):
            result = search_prowlarr_releases(12)
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(result["resultCount"], 3, "three releases, however many wordings were tried")
        first = result["nearMisses"][0]
        self.assertEqual((first["copies"], first["reasons"]),
                         (2, ["Refused before: The download is incomplete: 3 MB of it is zero-filled"]))
        dated = next(miss for miss in result["nearMisses"] if miss["title"].startswith("Week of"))
        self.assertEqual(dated["copies"], 1)
        self.assertNotIn("Issue #4 matches", dated["reasons"], "the 04 in its date is not the issue")
        self.assertFalse(app._release_issue_matches(
            'Week of 2022.02.16 [53/72] - yEnc "Supergirl - Woman of Tomorrow 08 (of 08) (2022)"', "2"))
        self.assertTrue(app._release_issue_matches("Supergirl - Woman of Tomorrow 02 (of 08) (2021)", "2"))
        # "023.2" is issue 23.2: not an answer for #23, the answer for #23.2.
        self.assertFalse(app._release_issue_matches("Green.Lantern.023.2.(2013).(Digital).(Nahga-Empire)", "23"))
        self.assertTrue(app._release_issue_matches("Green.Lantern.023.2.(2013).(Digital).(Nahga-Empire)", "23.2"))
        self.assertTrue(app._release_issue_matches("Green Lantern 023 (2013) (Digital) (Nahga-Empire)", "23"))
        # The series is still read in front of a decimal number, dots or spaces.
        for title in ("Green.Lantern.023.2.(2013).(Digital).(Nahga-Empire)", "Green Lantern 023.2 (2013) (Digital) (Nahga-Empire) (cbr)"):
            self.assertTrue(app._release_series_matches(title, "Green Lantern", "23.2"), title)
            score, reasons = app._release_candidate_score(
                {"title": title, "categories": [{"id": 7030}]},
                {"seriesTitle": "Green Lantern", "issueNumber": "23.2", "publicationYear": 2013})
            self.assertGreaterEqual(score, 85, (title, reasons))
        # A half issue: "½" in the catalog, "0.5" in a release, ".5" in a search box.
        for spelling in ("½", "0.5", ".5", "1/2", "000.5"):
            self.assertEqual(app._issue_key(spelling), "0.5", spelling)
        half = "Ultimate Spider-man 0.5 (2002) (digital - Empire)"
        self.assertTrue(app._release_issue_matches(half, "½"))
        self.assertTrue(app._release_series_matches(half, "Ultimate Spider-Man", "½"))
        self.assertFalse(app._release_issue_matches("Ultimate Spider-Man 054 - 071 (2004-2005) (digital-Empire)", "½"))
        self.assertFalse(app._release_issue_matches("Ultimate Spider-Man 005 (2001)", "½"), "5 is not .5")
        self.assertEqual(app._issue_key("023.2"), "23.2")
        # Marvel's lettered tie-ins: "6AU" wanted, "006 AU" on the release -- and
        # a plain #6 is not answered by it, nor it by a plain 006.
        self.assertEqual(app._issue_key("006 AU"), app._issue_key("6AU"))
        au = "Superior Spider-Man 006 AU (2013) (digital-TheGroup)"
        self.assertTrue(app._release_issue_matches(au, "6AU"))
        self.assertTrue(app._release_series_matches(au, "Superior Spider-Man", "6AU"))
        self.assertTrue(app._release_issue_matches("part.05.Superior.Spider-Man.006.AU.2013.theProletariat-Novus", "6AU"))
        self.assertFalse(app._release_issue_matches(au, "6"))
        self.assertFalse(app._release_issue_matches("Superior Spider-Man 006 (2013) (digital-TheGroup)", "6AU"))
        self.assertTrue(app._release_issue_matches("Lot 02 of 04 2026", "2"), "a count's 'of' is not a suffix")
        self.assertNotIn("key", first)
        self.assertNotIn("secret", str(result))

    def test_prowlarr_search_returns_safe_ranked_usenet_candidates(self):
        context = {
            "id": "12", "requestId": "4", "status": "queued",
            "issueId": "8", "issueNumber": "4", "issueTitle": "The Zoo",
            "publicationYear": 2025, "publicationDate": "2025-01-01",
            "seriesId": "2", "seriesTitle": "Absolute Batman",
            "seriesYear": 2024, "publisher": "DC Comics",
            "acquisitionPreference": "either",
        }
        store = Mock()
        store.get_acquisition_job_context.return_value = context
        releases = [
            {
                "guid": "good", "title": "Absolute Batman 004 (2025) (Digital) (ENG)",
                "protocol": "usenet", "downloadUrl": "http://prowlarr/api/v1/download?apikey=secret",
                "indexer": "Test Indexer", "size": 50_000_000,
                "publishDate": "2025-01-02T00:00:00Z", "categories": [{"id": 7030}],
            },
            {
                "guid": "wrong-issue", "title": "Absolute Batman 005 (2025)",
                "protocol": "usenet", "downloadUrl": "http://prowlarr/wrong",
                "indexer": "Test Indexer", "size": 40_000_000, "categories": [{"id": 7030}],
            },
            {
                "guid": "torrent", "title": "Absolute Batman 004 (2025)",
                "protocol": "torrent", "downloadUrl": "magnet:?xt=secret",
                "indexer": "Torrent Indexer", "size": 40_000_000, "categories": [{"id": 7030}],
            },
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"}
            }
        ), patch("app.fetch_json_with_headers", return_value=releases) as fetch:
            result = search_prowlarr_releases(12)

        self.assertEqual(result["query"], "Absolute Batman 004")
        self.assertEqual(result["candidateCount"], 1)
        # What came back and was set aside, so "nothing found" can say which it was.
        self.assertEqual(result["resultCount"], 2, "the Usenet results the indexers returned")
        self.assertEqual([miss["title"] for miss in result["nearMisses"]], ["Absolute Batman 005 (2025)"])
        self.assertNotIn("Issue #4 matches", result["nearMisses"][0]["reasons"])
        candidate = result["candidates"][0]
        self.assertEqual(candidate["title"], releases[0]["title"])
        self.assertEqual(candidate["formatTags"], ["Digital", "English"])
        self.assertNotIn("downloadUrl", candidate)
        self.assertNotIn("secret", str(result))
        cached = _RELEASE_CANDIDATES[candidate["id"]]
        self.assertEqual(cached["downloadPath"], "/api/v1/download")
        self.assertNotIn("secret", str(cached))
        requested_url = fetch.call_args.args[0]
        self.assertIn("categories=7030", requested_url)
        self.assertEqual(fetch.call_args.args[1]["X-Api-Key"], "secret")
        self.assertEqual(store.update_acquisition_job.call_args_list[0].args[1], "searching")
        self.assertEqual(store.update_acquisition_job.call_args_list[-1].args[1], "queued")

    def test_a_release_set_aside_can_be_taken_by_hand(self):
        """The owner (2026-09-29): "staring right at the file I need and can't
        download it" -- the Flashpoint tie-ins under batch-numbered names."""
        context = {
            "id": "12", "requestId": "4", "status": "queued", "issueId": "8", "issueNumber": "1",
            "publicationYear": 2011, "publicationDate": "2011-06-01", "seriesId": "2",
            "seriesTitle": "Flashpoint: Secret Seven", "seriesYear": 2011, "publisher": "DC Comics",
            "acquisitionPreference": "either",
        }
        store = Mock()
        store.get_acquisition_job_context.return_value = context
        store.rejected_acquisition_releases.return_value = [{
            "release_key": "old", "release_title": "Flashpoint - Secret Seven 001 (2011) (noads)",
            "error": "Aborted, cannot be completed - https://sabnzbd.org/not-complete",
        }]
        releases = [
            {"guid": "a", "title": "Secret Seven 01 2011 noads DangerAngel-CPS", "protocol": "usenet",
             "downloadUrl": "http://prowlarr/a", "indexer": "One", "size": 1, "categories": [{"id": 7030}]},
            {"guid": "b", "title": "Flashpoint - Secret Seven 001 (2011) (noads)", "protocol": "usenet",
             "downloadUrl": "http://prowlarr/b", "indexer": "One", "size": 1, "categories": [{"id": 7030}]},
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"}
            }
        ), patch("app.fetch_json_with_headers", return_value=releases):
            result = search_prowlarr_releases(12)
        self.assertEqual(result["candidateCount"], 0)
        by_title = {miss["title"]: miss for miss in result["nearMisses"]}
        aside = by_title["Secret Seven 01 2011 noads DangerAngel-CPS"]
        self.assertEqual((aside["setAside"], aside["refused"], aside["reason"], aside["source"]),
                         (True, False, "Not this series", "usenet"))
        refused = by_title["Flashpoint - Secret Seven 001 (2011) (noads)"]
        self.assertEqual((refused["refused"], refused["reason"]),
                         (True, "Refused before: Aborted, cannot be completed - https://sabnzbd.org/not-complete"))
        registered = _RELEASE_CANDIDATES[aside["id"]]
        self.assertEqual((registered["setAside"], registered["setAsideReason"], registered["downloadPath"]),
                         (True, "Not this series", "/a"))
        # A stale client cannot grab it blindly.
        with self.assertRaisesRegex(ValueError, "set aside as Not this series; take it anyway to grab it"):
            app._claim_release_candidate(12, aside["id"])
        self.assertIs(app._claim_release_candidate(12, aside["id"], anyway=True), registered)
        # Taking it: the refusal goes, its kept files go first, the row says so.
        store.kept_refused_downloads.return_value = [
            {"id": 3, "release_title": "Flashpoint - Secret Seven 001 (2011) (noads)", "sab_nzo_id": "old-nzo",
             "sab_storage": "/downloads/complete/comics/old"},
        ]
        store.update_acquisition_job.return_value = {"id": "12", "status": "grabbed"}
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"},
                "sabnzbd": {"enabled": True, "url": "http://sab", "apiKey": "sab-secret", "category": "comics"},
            }
        ), patch("app.fetch_bytes_with_headers", return_value=(
            b'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"></nzb>'
        )), patch("app.post_multipart_file_json", return_value={"status": True, "nzo_ids": ["queue-id"]}), \
             patch("app._sab_remove_job") as remove:
            with self.assertRaisesRegex(ValueError, "take it anyway"):
                app.grab_release_candidate(12, refused["id"])
            taken = app.grab_release_candidate(12, refused["id"], anyway=True)
        self.assertEqual((taken["status"], taken["takenByHand"]), ("grabbed", True))
        remove.assert_called_once_with({"sab_nzo_id": "old-nzo", "sab_storage": "/downloads/complete/comics/old"})
        store.forget_release_refusal.assert_called_once_with(
            12, _RELEASE_CANDIDATES.get(refused["id"], registered)["releaseKey"] if refused["id"] in _RELEASE_CANDIDATES
            else store.forget_release_refusal.call_args.args[1], "Flashpoint - Secret Seven 001 (2011) (noads)")
        self.assertEqual(store.record_acquisition_download.call_args.kwargs, {"taken_by_hand": True})
        self.assertEqual(store.update_acquisition_job.call_args.args[1:],
                         ("grabbed", "Taken by hand: Flashpoint - Secret Seven 001 (2011) (noads)"))

    def test_sabnzbd_grab_fetches_nzb_privately_and_uploads_a_clean_file(self):
        _RELEASE_CANDIDATES["candidate"] = {
            "jobId": 12, "downloadPath": "/9/download?id=42",
            "title": "Absolute Batman 004", "expiresAt": 9_999_999_999,
        }
        store = Mock()
        store.update_acquisition_job.return_value = {"id": "12", "status": "grabbed"}
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr:9696", "apiKey": "prowlarr-secret"},
                "sabnzbd": {"enabled": True, "url": "http://sab", "apiKey": "sab-secret", "category": "comics"},
            }
        ), patch("app.fetch_bytes_with_headers", return_value=(
            b'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"></nzb>'
        )) as fetch, patch("app.post_multipart_file_json", return_value={
            "status": True, "nzo_ids": ["queue-id"]
        }) as upload:
            result = send_release_to_sabnzbd(12, "candidate")

        self.assertEqual(fetch.call_args.args[0], "http://prowlarr:9696/9/download?id=42")
        self.assertEqual(fetch.call_args.args[1]["X-Api-Key"], "prowlarr-secret")
        upload_url = upload.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(upload_url).query)
        self.assertEqual(query["mode"], ["addfile"])
        self.assertEqual(query["nzbname"], ["Absolute Batman 004"])
        self.assertEqual(query["cat"], ["comics"])
        self.assertEqual(query["apikey"], ["sab-secret"])
        self.assertEqual(upload.call_args.kwargs["field_name"], "name")
        self.assertEqual(upload.call_args.kwargs["filename"], "Absolute Batman 004.nzb")
        self.assertTrue(upload.call_args.kwargs["content"].startswith(b"<?xml"))
        self.assertNotIn("prowlarr", upload_url)
        self.assertNotIn("prowlarr-secret", str(upload.call_args))
        self.assertEqual(result["status"], "grabbed")
        self.assertEqual(result["queueIds"], ["queue-id"])
        self.assertNotIn("secret", str(result))
        download_call = store.record_acquisition_download.call_args.args
        self.assertEqual(download_call[:3], (12, "queue-id", "Absolute Batman 004"))
        self.assertRegex(download_call[3], r"^[a-f0-9]{64}$")
        store.update_acquisition_job.assert_called_once()
        self.assertNotIn("candidate", _RELEASE_CANDIDATES)

    def test_sabnzbd_grab_rejects_a_non_nzb_before_upload(self):
        _RELEASE_CANDIDATES["candidate"] = {
            "jobId": 12, "downloadPath": "/9/download", "title": "Absolute Batman 004",
            "expiresAt": 9_999_999_999,
        }
        with patch("app.load_acquisition_service_config", return_value={
            "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"},
            "sabnzbd": {"enabled": True, "url": "http://sab", "apiKey": "secret"},
        }), patch("app.fetch_bytes_with_headers", return_value=b"<html>Indexer error</html>"), patch(
            "app.post_multipart_file_json"
        ) as upload:
            with self.assertRaisesRegex(ReleaseDownloadError, "not an NZB document"):
                send_release_to_sabnzbd(12, "candidate")
        upload.assert_not_called()

    def test_multipart_nzb_upload_uses_the_sab_file_field(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"status": true}')
        with patch("app.urllib.request.build_opener", return_value=opener):
            result = post_multipart_file_json(
                "http://sab/api?mode=addfile",
                field_name="name",
                filename="Absolute Batman 004.nzb",
                content=b"<nzb></nzb>",
                content_type="application/x-nzb",
                headers={"Accept": "application/json"},
            )

        request = opener.open.call_args.args[0]
        body = request.data
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertIn(b'Content-Disposition: form-data; name="name"; filename="Absolute Batman 004.nzb"', body)
        self.assertIn(b"Content-Type: application/x-nzb", body)
        self.assertIn(b"<nzb></nzb>", body)
        self.assertEqual(result, {"status": True})

    def test_sabnzbd_grab_rejects_an_unknown_candidate(self):
        with self.assertRaisesRegex(ValueError, "expired"):
            send_release_to_sabnzbd(12, "missing")

    def test_prowlarr_search_excludes_a_release_that_previously_failed(self):
        context = {
            "issueNumber": "95", "publicationYear": 2010,
            "seriesTitle": "Fables", "seriesYear": 2002,
        }
        failed_title = "Fables.095.(2010).(Digital).(NahgaEmpire)"
        store = Mock()
        store.get_acquisition_job_context.return_value = context
        store.rejected_acquisition_releases.return_value = [{
            "release_key": "different-provider-key",
            "release_title": failed_title,
        }]
        releases = [
            {
                "guid": "failed", "title": failed_title,
                "protocol": "usenet", "downloadUrl": "http://prowlarr/failed",
                "indexer": "Indexer A", "size": 40_000_000,
                "categories": [{"id": 7030}],
            },
            {
                "guid": "fallback", "title": "Fables 095 (2010) (Digital) (Empire)",
                "protocol": "usenet", "downloadUrl": "http://prowlarr/fallback",
                "indexer": "Indexer B", "size": 39_000_000,
                "categories": [{"id": 7030}],
            },
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app.load_acquisition_service_config", return_value={
                "prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"}
            },
        ), patch("app.fetch_json_with_headers", return_value=releases):
            result = search_prowlarr_releases(12)

        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual(result["candidates"][0]["title"], releases[1]["title"])

    def test_year_qualified_discovery_ranks_same_name_runs_by_publication_year(self):
        rows = [
            {"id": index, "series": "Wolverine", "year_began": year, "issue_count": 1}
            for index, year in enumerate(range(1980, 2010), start=1)
        ]
        rows.extend([
            {"id": 101, "series": "Wolverine", "year_began": 2024, "issue_count": 12},
            {"id": 102, "series": "Wolverine", "year_began": 2026, "issue_count": 3},
            {"id": 103, "series": "Wolverine", "year_began": 2025, "issue_count": 8},
        ])

        def response(url, _headers):
            if "/series/?" in url:
                self.assertIn("q=Wolverine", url)
                self.assertNotIn("2026", url)
                return {"results": rows}
            if url.endswith("/series/102/"):
                return {"status": "Ongoing"}
            if "/series/102/issue_list/" in url:
                return {"results": []}
            self.fail(f"Unexpected Metron request: {url}")

        store = Mock()
        store.catalog.return_value = {"series": []}
        with patch("app.fetch_json_with_headers", side_effect=response), patch(
            "app.catalog_store", return_value=store
        ):
            result = discover_metron_series("Wolverine 2026", "token")

        self.assertEqual(result["titleQuery"], "Wolverine")
        self.assertEqual(result["yearHint"], 2026)
        self.assertEqual(result["results"][0]["yearBegan"], 2026)
        self.assertEqual(len(result["results"]), 24)

    def test_metron_discovery_adds_a_role_labeled_byline_from_the_first_issue(self):
        def response(url, _headers):
            if "/series/?" in url:
                return {"results": [{
                    "id": 9, "series": "Saga (2012)", "year_began": 2012,
                    "publisher": {"name": "Image Comics"}, "issue_count": 72,
                }]}
            if url.endswith("/series/9/"):
                return {"publisher": {"name": "Image Comics"}, "status": "Ongoing"}
            if "/series/9/issue_list/" in url:
                return {"results": [{"id": 101, "number": "1", "image": "https://covers.test/saga.jpg"}]}
            if url.endswith("/issue/101/"):
                return {"credits": [
                    {"creator": "Brian K. Vaughan", "role": [{"name": "Writer"}]},
                    {"creator": "Fiona Staples", "role": [
                        {"name": "Artist"}, {"name": "Colorist"}, {"name": "Cover"},
                    ]},
                ]}
            self.fail(f"Unexpected Metron request: {url}")

        store = Mock()
        store.catalog.return_value = {"series": []}
        with patch("app.fetch_json_with_headers", side_effect=response), patch(
            "app.catalog_store", return_value=store
        ):
            result = discover_metron_series("Saga", "token")

        saga = result["results"][0]
        self.assertEqual(saga["cover"], "https://covers.test/saga.jpg")
        self.assertEqual(saga["creatorCreditsSource"], "Issue #1")
        self.assertEqual(saga["creators"], [
            {"name": "Brian K. Vaughan", "roles": ["Writer"]},
            {"name": "Fiona Staples", "roles": ["Artist", "Colorist"]},
        ])

    def test_discovery_prefers_configured_metron_and_leaves_gcd_out_when_it_has_the_title(self):
        metron_result = {
            "query": "Saga", "provider": "Metron", "providerId": "metron",
            "results": [{
                "provider": "metron", "providerName": "Metron",
                "providerSeriesId": "9", "title": "Saga",
                "yearBegan": 2012, "publisher": "Image Comics", "cover": None,
            }],
        }
        comic_result = {
            "query": "Saga", "provider": "Comic Vine", "providerId": "comic_vine",
            "results": [{
                "provider": "comic_vine", "providerName": "Comic Vine",
                "providerSeriesId": "8", "title": "Saga",
                "yearBegan": 2012, "publisher": "Image", "cover": "https://covers.test/saga.jpg",
            }],
        }
        gcd_result = {
            "query": "Saga", "provider": "Grand Comics Database", "providerId": "gcd",
            "results": [{
                "provider": "gcd", "providerName": "Grand Comics Database",
                "providerSeriesId": "7", "title": "Saga", "yearBegan": 2012,
                "issueCount": 72,
            }],
        }
        empty = {"keys": set(), "providerIds": set()}
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
            "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app._discovery_library_view", return_value=empty), patch(
            "app.discover_metron_series", return_value=metron_result
        ) as metron, patch(
            "app.discover_comic_vine_series", return_value=comic_result
        ) as comic_vine, patch("app.discover_gcd_series", return_value=gcd_result) as gcd:
            result = discover_series("Saga")
        # One run, not three rows: the providers describe the same publication.
        self.assertEqual(len(result["results"]), 1)
        run = result["results"][0]
        self.assertEqual(result["providerId"], "metron", "Metron's record is the one shown")
        self.assertEqual(run["cover"], "https://covers.test/saga.jpg")
        self.assertEqual(run["coverProvider"], "Comic Vine")
        self.assertEqual(run["providerIds"], {"metron": "9", "comic_vine": "8"},
                         "every id is kept, because importing goes back to the source")
        metron.assert_called_once_with("Saga", "token", empty, hydrate=False)
        comic_vine.assert_called_once_with("Saga", "key", empty)
        gcd.assert_not_called()
        self.assertEqual(result["providersChecked"], ["Metron", "Comic Vine"],
                         "GCD is the fallback, asked only when the others lack the title (owner, 2026-09-30)")

    def test_a_provider_that_fails_does_not_take_the_search_with_it(self):
        comic_result = {
            "query": "Saga", "provider": "Comic Vine", "providerId": "comic_vine",
            "results": [{"provider": "comic_vine", "providerName": "Comic Vine",
                         "providerSeriesId": "8", "title": "Saga", "yearBegan": 2012}],
        }
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
            "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app._discovery_library_view",
                  return_value={"keys": set(), "providerIds": set()}), patch(
            "app.discover_metron_series", side_effect=RuntimeError("rate limited")
        ), patch(
            "app.discover_comic_vine_series", return_value=comic_result
        ), patch("app.discover_gcd_series", return_value={"results": []}):
            result = discover_series("Saga")
        self.assertEqual(result["providerId"], "comic_vine")
        self.assertEqual(result["fallbacks"][0]["provider"], "Metron")
        self.assertEqual(result["providersAnswered"], ["Comic Vine"], "Comic Vine had the title; GCD was not needed")
        self.assertEqual(len(result["results"]), 1)

    def test_a_name_counts_only_when_the_catalog_knows_it_outright(self):
        people = [
            {"id": 1, "name": "Brian K. Vaughan"}, {"id": 2, "name": "Brian Vaughan"},
            {"id": 3, "name": "Robert Vaughan"}, {"id": 4, "name": "McNally Sagal"},
        ]
        self.assertEqual(app._named_match(people, "brian k vaughan")[0]["id"], 1)
        match, suggestions = app._named_match(people, "Vaughan")
        self.assertIsNone(match, "a surname four people share is not a guess worth making")
        self.assertEqual(suggestions, ["Brian K. Vaughan", "Brian Vaughan", "Robert Vaughan"])
        self.assertEqual(app._named_match(people, "Saga"), (None, []), "Saga is not McNally Sagal")
        self.assertEqual(app._named_match([{"id": 7, "name": "James Tynion IV"}], "Tynion")[0]["id"], 7)
        publishers = [{"id": 1, "name": "Image Comics"}, {"id": 2, "name": "Active Images"}]
        self.assertEqual(app._named_match(publishers, "Image", company=True)[0]["id"], 1)

    def test_a_creators_runs_are_grouped_from_the_issues_credited_to_them(self):
        import time
        issues = [
            {"id": 12, "number": "2", "image": "saga2.jpg", "series": {"id": 5, "name": "Saga", "year_began": 2012}},
            {"id": 11, "number": "1", "image": "saga1.jpg", "series": {"id": 5, "name": "Saga", "year_began": 2012}},
            {"id": 21, "number": "1", "image": "pg1.jpg", "series": {"id": 6, "name": "Paper Girls", "year_began": 2015}},
        ]
        library = {"keys": {(app.normalized_title("Paper Girls"), "2015")}, "providerIds": set()}
        with patch("app.fetch_provider_json", return_value={"results": [{"id": 3, "name": "Brian K. Vaughan"}]}), \
                patch("app._fetch_provider_pages", return_value=issues) as pages:
            result = app._metron_creator_runs("Brian K. Vaughan", None, "token", library, time.monotonic() + 10)
            only_2015 = app._metron_creator_runs("Brian K. Vaughan", 2015, "token", library, time.monotonic() + 10)
        self.assertIn("creator_id=3", pages.call_args[0][1])
        self.assertEqual(result["creator"]["name"], "Brian K. Vaughan")
        self.assertEqual([run["title"] for run in result["runs"]], ["Saga", "Paper Girls"])
        saga, paper_girls = result["runs"]
        self.assertEqual((saga["creditedIssues"], saga["cover"], saga["providerIds"]), (2, "saga1.jpg", {"metron": "5"}))
        self.assertTrue(paper_girls["inLibrary"])
        self.assertEqual([run["title"] for run in only_2015["runs"]], ["Paper Girls"])

    def test_people_search_merges_a_person_both_catalogs_know(self):
        metron = {"creator": {"name": "Tatsuki Fujimoto", "provider": "Metron"}, "runs": [
            {"title": "Chainsaw Man", "yearBegan": 2020, "provider": "metron", "providerSeriesId": "1",
             "providerIds": {"metron": "1"}, "cover": None},
        ]}
        vine = {"creator": {"name": "Tatsuki Fujimoto", "provider": "Comic Vine"}, "runs": [
            {"title": "Chainsaw Man", "yearBegan": 2020, "provider": "comic_vine", "providerSeriesId": "9",
             "providerIds": {"comic_vine": "9"}, "cover": "csm.jpg", "publisher": "VIZ Media"},
            {"title": "Fire Punch", "yearBegan": 2018, "provider": "comic_vine", "providerSeriesId": "8",
             "providerIds": {"comic_vine": "8"}},
        ]}
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"}, "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app._discovery_library_view", return_value={"keys": set(), "providerIds": set()}), \
                patch("app._metron_creator_runs", return_value=metron), \
                patch("app._comic_vine_creator_runs", return_value=vine), \
                patch("app._metron_publisher_runs", return_value={"didYouMean": []}):
            result = app.discover_people_and_publishers("Tatsuki Fujimoto")
        self.assertEqual(len(result["creatorMatches"]), 1, "one person, not one per catalog")
        match = result["creatorMatches"][0]
        self.assertEqual(match["providers"], ["Metron", "Comic Vine"])
        self.assertEqual([run["title"] for run in match["runs"]], ["Chainsaw Man", "Fire Punch"])
        self.assertEqual(match["runs"][0]["providerIds"], {"metron": "1", "comic_vine": "9"})
        self.assertEqual((match["runs"][0]["cover"], match["runs"][0]["publisher"]), ("csm.jpg", "VIZ Media"))
        self.assertEqual((result["publisherMatches"], result["didYouMean"], result["stillLooking"]), ([], [], False))

    def test_people_search_offers_the_names_an_ambiguous_one_might_mean(self):
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"}, "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app._discovery_library_view", return_value={"keys": set(), "providerIds": set()}), \
                patch("app._metron_creator_runs", return_value={"didYouMean": ["Brian K. Vaughan", "Robert Vaughan"]}), \
                patch("app._comic_vine_creator_runs", return_value={"didYouMean": ["Brian K Vaughan"]}), \
                patch("app._metron_publisher_runs", return_value={"didYouMean": []}):
            result = app.discover_people_and_publishers("Vaughan")
        self.assertEqual(result["creatorMatches"], [])
        self.assertEqual(result["didYouMean"], ["Brian K. Vaughan", "Robert Vaughan"])

    def test_people_search_answers_with_what_it_has_at_its_deadline(self):
        import time

        def slow(*_args, **_kwargs):
            time.sleep(0.5)
            return {}
        with patch.object(app, "PEOPLE_LOOKUP_DEADLINE_SECONDS", 0.05), patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
        }), patch("app._discovery_library_view", return_value={"keys": set(), "providerIds": set()}), \
                patch("app._metron_creator_runs", side_effect=slow), \
                patch("app._metron_publisher_runs", return_value={
                    "publisher": {"name": "Image Comics", "provider": "Metron"}, "year": 2012, "runs": [],
                }):
            started = time.monotonic()
            result = app.discover_people_and_publishers("Image 2012")
        self.assertLess(time.monotonic() - started, 0.4)
        self.assertTrue(result["stillLooking"])
        self.assertEqual((result["publisherMatches"][0]["name"], result["publisherMatches"][0]["year"]), ("Image Comics", 2012))

    def test_one_search_ranks_titles_creators_and_publishers_together(self):
        title = {"results": [{"provider": "metron", "providerName": "Metron", "providerSeriesId": "40",
                              "title": "Brian's Song", "yearBegan": 1999}]}
        people = {"creatorMatches": [{"name": "Brian K. Vaughan", "runs": [
            {"provider": "metron", "providerSeriesId": "5", "providerIds": {"metron": "5"},
             "title": "Saga", "yearBegan": 2012, "inLibrary": True},
            {"provider": "metron", "providerSeriesId": "7", "providerIds": {"metron": "7"},
             "title": "Y: The Last Man", "yearBegan": 2002},
        ]}], "publisherMatches": [], "didYouMean": [], "stillLooking": False}
        with patch("app.load_provider_config", return_value={"metron": {"enabled": True, "token": "token"}}), \
                patch("app._discovery_library_view", return_value={"keys": set(), "providerIds": set()}), \
                patch("app.discover_metron_series", return_value=title), \
                patch("app.discover_gcd_series", return_value={"results": []}), \
                patch("app._people_and_publishers", return_value=people) as lookup:
            result = app.discover_series("Brian K. Vaughan", extended=True)
            lookup.assert_called_once()
            plain = app.discover_series("Brian K. Vaughan")
        self.assertEqual([run["title"] for run in result["results"]], ["Saga", "Y: The Last Man", "Brian's Song"])
        self.assertEqual(result["results"][1]["matchedBy"], "By Brian K. Vaughan")
        self.assertEqual(result["creatorMatches"], ["Brian K. Vaughan"])
        self.assertEqual(lookup.call_count, 1, "Fix Match's plain title search does not ask about people")
        self.assertNotIn("creatorMatches", plain)

    def test_an_exact_title_outranks_a_run_found_another_way(self):
        ranked = app._rank_discovered(
            [{"title": "Saga", "yearBegan": 2012, "provider": "metron", "providerSeriesId": "1",
              "providerIds": {"metron": "1"}}],
            {"publisherMatches": [{"name": "Image Comics", "year": 2012, "runs": [
                {"title": "Revival", "yearBegan": 2012, "provider": "metron", "providerSeriesId": "2"},
                {"title": "Saga", "yearBegan": 2012, "provider": "comic_vine", "providerSeriesId": "9",
                 "providerIds": {"comic_vine": "9"}, "cover": "saga.jpg"},
            ]}]},
            "Saga", None,
        )
        self.assertEqual([run["title"] for run in ranked], ["Saga", "Revival"])
        self.assertIsNone(ranked[0]["matchedBy"], "a title match speaks for itself")
        self.assertEqual(ranked[0]["providerIds"], {"metron": "1", "comic_vine": "9"})
        self.assertEqual(ranked[0]["cover"], "saga.jpg")
        self.assertEqual(ranked[1]["matchedBy"], "Image Comics, 2012")

    def test_a_title_from_another_year_ranks_below_what_the_year_asked_for(self):
        ranked = app._rank_discovered(
            [{"title": "Image+", "yearBegan": 2016, "provider": "metron", "providerSeriesId": "3"}],
            {"publisherMatches": [{"name": "Image Comics", "year": 2012, "runs": [
                {"title": "Revival", "yearBegan": 2012, "provider": "metron", "providerSeriesId": "2"},
            ]}]},
            "Image", 2012,
        )
        self.assertEqual([run["title"] for run in ranked], ["Revival", "Image+"])

    def test_a_creator_lookup_lets_the_title_search_have_metron_first(self):
        import threading
        import time
        turn = app._MetronTitleTurn()
        waited = threading.Event()
        worker = threading.Thread(target=lambda: (app._after_metron_title_searches(5), waited.set()))
        worker.start()
        time.sleep(0.1)
        self.assertFalse(waited.is_set(), "held while the title search's Metron request is out")
        self.assertEqual(turn.after(lambda: "titles"), "titles")
        worker.join(2)
        self.assertTrue(waited.is_set(), "and let go the moment it is answered")
        turn.release()  # releasing twice is harmless
        self.assertEqual(app._METRON_TITLE_WAITING[0], 0)
        started = time.monotonic()
        app._after_metron_title_searches(5)
        self.assertLess(time.monotonic() - started, 0.1, "with no title search out, no wait at all")

    def test_idle_worker_fills_a_runs_creators_from_one_metron_issue(self):
        from unittest.mock import Mock
        store = Mock()
        store.metadata_provider_available.return_value = True
        store.claim_run_creator_sync.return_value = {
            "seriesRunId": 4, "provider": "metron", "providerId": "901", "format": "comic",
        }
        issue = {"credits": [
            {"creator": "Brian K. Vaughan", "role": [{"name": "Writer"}]},
            {"creator": "Fiona Staples", "role": [{"name": "Artist"}, {"name": "Cover"}]},
        ]}
        config = {"metron": {"enabled": True, "token": "token"}}
        with patch("app.load_provider_config", return_value=config), \
                patch("app._provider_credential", return_value="token"), \
                patch("app.fetch_provider_json", return_value=issue) as fetch:
            self.assertTrue(app.sync_next_run_creators(store))
        self.assertIn("/issue/901/", fetch.call_args[0][1])
        store.set_run_catalog_creators.assert_called_once_with(4, "metron", [
            {"name": "Brian K. Vaughan", "roles": ["writer"]}, {"name": "Fiona Staples", "roles": ["artist"]},
        ])

        store.reset_mock()
        with patch("app.load_provider_config", return_value=config), \
                patch("app._provider_credential", return_value="token"), \
                patch("app.fetch_provider_json", side_effect=app.MetadataRateLimited("metron", 60, "slow down")):
            self.assertTrue(app.sync_next_run_creators(store))
        store.set_run_catalog_creators.assert_not_called()
        store.release_run_creator_sync.assert_called_once_with(4)

    def test_discover_does_not_pay_for_a_byline_it_never_shows(self):
        """Metron paces callers at one request every 3.2s.

        A cold search made four of them -- search, series, issue list, issue --
        and the last three improved one row out of two dozen. Discover shows a
        title, publisher, year, run status and cover, all of which are on the
        search row already.
        """
        captured = {}

        def metron(query, token, library, *, hydrate=True):
            captured["hydrate"] = hydrate
            return {"results": []}

        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
        }), patch("app._discovery_library_view",
                  return_value={"keys": set(), "providerIds": set()}), patch(
            "app.discover_metron_series", side_effect=metron
        ), patch("app.discover_gcd_series", return_value={"results": []}):
            discover_series("Saga")
        self.assertIs(captured["hydrate"], False)

    def test_the_match_workbench_still_asks_for_the_byline(self):
        """It renders creator credits, so it keeps paying for them."""
        import inspect
        signature = inspect.signature(app.discover_metron_series)
        self.assertIs(signature.parameters["hydrate"].default, True)

    def test_a_run_still_going_is_not_finished_by_another_provider(self):
        """Absence of an end year is evidence when the provider models one.

        Metron reporting year_end as null says the run is still shipping. GCD
        naming a year for its own record of the same run must not overwrite
        that, or a comic out this week reads Run Complete.
        """
        metron_result = {"results": [{
            "provider": "metron", "providerName": "Metron", "providerSeriesId": "9",
            "title": "Batman", "yearBegan": 2016, "yearEnded": None,
            "yearLabel": "2016", "status": "Ongoing",
        }]}
        gcd_result = {"results": [{
            "provider": "gcd", "providerName": "Grand Comics Database",
            "providerSeriesId": "7", "title": "Batman", "yearBegan": 2016,
            "yearEnded": 2026, "yearLabel": "2016–2026",
        }]}
        results = app._merge_discovered_runs(
            {"metron": metron_result["results"], "gcd": gcd_result["results"]}, "Batman", None)
        run = results[0]
        self.assertIsNone(run["yearEnded"])
        self.assertEqual(run["yearLabel"], "2016")
        self.assertEqual(run["status"], "Ongoing")

    def test_every_provider_failing_still_raises(self):
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
        }), patch("app._discovery_library_view",
                  return_value={"keys": set(), "providerIds": set()}), patch(
            "app.discover_metron_series", side_effect=RuntimeError("rate limited")
        ), patch("app.discover_gcd_series", side_effect=RuntimeError("throttled")):
            with self.assertRaises(RuntimeError):
                discover_series("Saga")

    def test_gcd_is_asked_when_the_others_lack_the_title_or_its_year(self):
        config = {"metron": {"enabled": True, "token": "token"}}
        saga_2012 = {"results": [{"provider": "metron", "providerName": "Metron", "providerSeriesId": "9",
                                  "title": "Saga", "yearBegan": 2012}]}
        with patch("app.load_provider_config", return_value=config), \
             patch("app._discovery_library_view", return_value={"keys": set(), "providerIds": set()}), \
             patch("app.discover_metron_series", return_value=saga_2012), \
             patch("app.discover_gcd_series", return_value={"results": []}) as gcd:
            discover_series("Saga")
            gcd.assert_not_called()
            discover_series("Saga 1985")
            gcd.assert_called_once()
        with patch("app.load_provider_config", return_value={}), \
             patch("app._discovery_library_view", return_value={"keys": set(), "providerIds": set()}), \
             patch("app.discover_gcd_series", return_value={"results": []}) as gcd:
            result = discover_series("Saga")
        gcd.assert_called_once()
        self.assertEqual(result["providersChecked"], ["Grand Comics Database"], "with nothing else set up, GCD is the search")

    def test_gcd_is_reached_even_when_metron_answers_weakly(self):
        """The bug this replaced: two loose matches counted as success.

        GCD is the deepest catalog of the three for older, indie and reprint
        material, and while Metron was configured it was never asked at all.
        """
        metron_result = {"results": [{
            "provider": "metron", "providerName": "Metron", "providerSeriesId": "9",
            "title": "Saga of the Swamp Thing", "yearBegan": 1982,
        }]}
        gcd_result = {"results": [{
            "provider": "gcd", "providerName": "Grand Comics Database",
            "providerSeriesId": "7", "title": "Saga", "yearBegan": 2012,
        }]}
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
        }), patch("app._discovery_library_view",
                  return_value={"keys": set(), "providerIds": set()}), patch(
            "app.discover_metron_series", return_value=metron_result
        ), patch("app.discover_gcd_series", return_value=gcd_result):
            result = discover_series("Saga")
        self.assertEqual([item["title"] for item in result["results"]],
                         ["Saga", "Saga of the Swamp Thing"],
                         "the exact match leads, whichever provider found it")

    def test_adding_a_discovered_run_returns_rather_than_raising_after_it_worked(self):
        """Add & request did all of its work and then handed back a NameError.

        The issue list was applied, the request created and the grab sent to
        SABnzbd -- and the return statement read a `status` that had stopped
        existing when the coverage claim moved inline. So the comic really was
        on its way and the user was told it had failed, which is the worst
        shape a bug can take: they retry work that already happened.
        """
        store = Mock()
        store.ensure_provider_series_run.return_value = {"id": "7", "title": "Paper Girls"}
        store.apply_issue_list.return_value = {"issues": 30}
        store.create_acquisition_request.return_value = {"id": "3", "status": "open"}
        with patch("app.catalog_store", return_value=store), patch(
            "app._provider_credential", return_value="token"
        ), patch("app._provider_series_run_details", return_value={
            "providerId": "70", "sourceUrl": "https://metron.cloud/series/70/",
            "entries": [{"number": "1"}, {"number": "2"}],
            "title": "Paper Girls", "year": "2015", "publisher": "Image Comics",
            "endEvidence": {"state": "ended", "year": 2016},
        }), patch("app._start_automatic_release_grabs") as grabs:
            result = request_discovered_series("metron", "Paper Girls", "70", "either")

        grabs.assert_called_once()
        self.assertEqual(result["series"]["id"], "7")
        self.assertEqual(result["issueCount"], 2)
        self.assertEqual(result["metadata"], {"provider": "metron", "status": "complete"})

    def test_discovered_request_routes_to_the_selected_provider(self):
        with patch("app.request_discovered_gcd_series", return_value={"series": {"id": "7"}}) as gcd:
            result = request_discovered_series("gcd", "Saga", "42", "either")
        self.assertEqual(result["series"]["id"], "7")
        gcd.assert_called_once_with("Saga", "42", "either")

    def test_discovery_checks_an_adjacent_page_for_an_exact_title(self):
        def response(url):
            if "page=2" in url:
                return {"results": [{"name": "Die Saga"}, {"name": "The Saga"}]}
            if "page=3" in url:
                return {"results": [{"name": "Saga"}]}
            return {"count": 150, "results": [{"name": "A Clone Saga"}]}

        with patch("app.fetch_gcd_json", side_effect=response) as fetch:
            rows = _gcd_discovery_search_rows("Saga")
        self.assertIn("Saga", [row["name"] for row in rows])
        self.assertEqual(fetch.call_count, 3)

    def test_discovery_search_marks_a_run_already_in_the_library(self):
        series = {
            "api_url": "https://www.comics.org/api/series/42/",
            "name": "Example Comics", "year_began": 2024, "country": "us", "language": "en",
            "active_issues": ["https://www.comics.org/api/issue/101/"],
            "issue_descriptors": ["1"],
        }
        store = Mock()
        store.catalog.return_value = {
            "series": [{"title": "Example Comics", "year": "2024", "issueCatalog": {"providerSeriesId": "42"}}]
        }
        with patch("app.fetch_gcd_json", return_value={"results": [series]}), patch(
            "app.catalog_store", return_value=store
        ):
            result = discover_gcd_series("Example Comics")
        self.assertEqual(result["results"][0]["providerSeriesId"], "42")
        self.assertTrue(result["results"][0]["inLibrary"])

    def test_discovered_run_is_imported_and_requested_in_one_action(self):
        series = {
            "api_url": "https://www.comics.org/api/series/42/",
            "name": "Example Comics", "year_began": 2024, "country": "us", "language": "en",
            "active_issues": ["https://www.comics.org/api/issue/101/"],
            "issue_descriptors": ["1"],
        }
        store = Mock()
        store.ensure_provider_series_run.return_value = {"id": "7", "title": "Example Comics", "created": True}
        store.create_acquisition_request.return_value = {"id": "9", "title": "Example Comics"}
        with patch("app.fetch_gcd_json", return_value={"results": [series]}), patch(
            "app._hydrate_gcd_issue_entries_with_status",
            return_value=([{"number": "1", "provider_id": "101", "publication_year": 2024}], {"status": "complete"}),
        ), patch("app.catalog_store", return_value=store):
            result = request_discovered_gcd_series("Example Comics", "42", "issues")
        self.assertEqual(result["request"]["id"], "9")
        store.apply_issue_list.assert_called_once()
        store.create_acquisition_request.assert_called_once_with("series", 7, "issues", True)

    def test_reveal_file_uses_finder_for_a_catalog_validated_path(self):
        handler = Handler.__new__(Handler)
        handler.send_json = Mock()
        store = Mock()
        store.cataloged_file_path.return_value = Path("/tmp/Example 001.cbz")
        completed = Mock(returncode=0, stdout="", stderr="")
        with patch("app.sys.platform", "darwin"), patch(
            "app.catalog_store", return_value=store
        ), patch("app.subprocess.run", return_value=completed) as run:
            handler.handle_reveal_file("/tmp/Example 001.cbz")

        store.cataloged_file_path.assert_called_once_with("/tmp/Example 001.cbz")
        run.assert_called_once_with(
            ["open", "-R", "/tmp/Example 001.cbz"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        handler.send_json.assert_called_once_with({
            "status": "revealed", "message": "Shown in Finder: Example 001.cbz",
        })

    def test_provider_broker_reports_repaired_and_remaining_fields(self):
        store = Mock()
        store.issue_metadata_summary.side_effect = [
            {"issueCount": 2, "missingTitleCount": 2, "missingDateCount": 1, "missingTitleIssues": ["1", "2"], "missingDateIssues": ["2"]},
            {"issueCount": 2, "missingTitleCount": 1, "missingDateCount": 0, "missingTitleIssues": ["2"], "missingDateIssues": []},
        ]
        store.get_series_sync_context.return_value = {
            "title": "Example", "year": 2026, "knownGcdIssueIds": ["101"],
        }
        with patch("app.catalog_store", return_value=store), patch(
            "app.sync_gcd_issue_catalog",
            return_value={"provider": "gcd", "issueCount": 2, "metadata": {"status": "partial"}},
        ), patch("app.load_provider_config", return_value={}):
            result = sync_issue_catalog(7)
        self.assertEqual(result["metadata"]["repairedTitleCount"], 1)
        self.assertEqual(result["metadata"]["repairedDateCount"], 1)
        self.assertEqual(result["metadata"]["missingTitleIssues"], ["2"])
        self.assertEqual(result["metadata"]["status"], "partial")
        detail = store.update_issue_catalog_detail.call_args.args[1]
        self.assertIn("Still unavailable", detail)
        self.assertIn("#2", detail)

    def test_provider_broker_checks_metron_and_comic_vine_before_gcd(self):
        store = Mock()
        store.metadata_provider_available.return_value = True
        store.issue_metadata_summary.side_effect = [
            {"issueCount": 2, "missingTitleCount": 2, "missingDateCount": 2,
             "missingTitleIssues": ["1", "2"], "missingDateIssues": ["1", "2"]},
            {"issueCount": 2, "missingTitleCount": 0, "missingDateCount": 0,
             "missingTitleIssues": [], "missingDateIssues": []},
        ]
        store.get_series_sync_context.return_value = {
            "title": "Example", "year": 2026, "knownGcdIssueIds": ["101"],
        }
        store.apply_issue_list.side_effect = [
            {"provider": "metron", "issueCount": 2, "titleCount": 2},
            {"provider": "comic_vine", "issueCount": 2, "titleCount": 2},
        ]
        calls = []

        def metron(*_args):
            calls.append("metron")
            return ("20", "https://metron/20", [{"number": "1"}, {"number": "2"}],
                    {"state": "unknown", "year": None})

        def comic_vine(*_args):
            calls.append("comic_vine")
            return "30", "https://comicvine/30", [{"number": "1"}, {"number": "2"}]

        def gcd(*_args):
            calls.append("gcd")
            return {"provider": "gcd", "issueCount": 2, "titleCount": 2,
                    "metadata": {"status": "complete"}}

        with patch("app.catalog_store", return_value=store), patch(
            "app.load_provider_config", return_value={
                "metron": {"enabled": True, "priority": 20, "token": "token"},
                "comic_vine": {"enabled": True, "priority": 30, "apiKey": "key"},
                "gcd": {"enabled": True, "priority": 10},
            },
        ), patch("app._metron_issue_entries", side_effect=metron), patch(
            "app._comic_vine_issue_entries", side_effect=comic_vine
        ), patch("app.sync_gcd_issue_catalog", side_effect=gcd):
            result = sync_issue_catalog(7)

        self.assertEqual(calls, ["metron", "comic_vine", "gcd"])
        self.assertEqual(
            [provider["provider"] for provider in result["providers"]],
            ["metron", "comic_vine", "gcd"],
        )

    def test_a_scan_still_skips_originals_quarantined_before_the_rename(self):
        """The exclusion is why a replaced original stays replaced. Renaming it
        without keeping the old spelling would re-import every one of them."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Saga 001.cbz").write_bytes(b"x")
            for managed in (".flipparr", ".sonicboom"):
                held = root / managed / "quarantine" / "1"
                held.mkdir(parents=True)
                (held / "Replaced 002.cbz").write_bytes(b"x")

            found = sorted(Path(item.path).name for item in app.scan_folder(str(root), True))
            self.assertEqual(found, ["Saga 001.cbz"])

    def test_an_original_quarantined_before_the_rename_is_still_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "Saga 003.cbz"
            legacy = root / ".sonicboom" / "quarantine" / "7"
            legacy.mkdir(parents=True)
            (legacy / "Saga 003.cbz").write_bytes(b"x")

            found = app._replacement_quarantine_path(7, original, root.resolve())
            self.assertEqual(found, (legacy / "Saga 003.cbz").resolve())

            # with nothing held anywhere, a new quarantine uses the new name
            fresh = app._replacement_quarantine_path(8, original, root.resolve())
            self.assertEqual(fresh, root.resolve() / ".flipparr" / "quarantine" / "8" / "Saga 003.cbz")

    def test_the_old_env_prefix_still_configures_a_renamed_app(self):
        """Comicarr -> SonicBoom -> Flipparr. A compose file written against the
        old prefix must keep working, or a rename looks like lost config."""
        with tempfile.TemporaryDirectory() as temp_dir:
            moved = Path(temp_dir)
            with patch.dict("app.os.environ", {"COMICARR_AUTH_CONFIG": str(moved / "legacy.json")}, clear=False):
                self.assertEqual(app.auth_config_path(), moved / "legacy.json")
            # and the new spelling wins when both are set
            with patch.dict("app.os.environ", {
                "COMICARR_AUTH_CONFIG": str(moved / "legacy.json"),
                "FLIPPARR_AUTH_CONFIG": str(moved / "current.json"),
            }, clear=False):
                self.assertEqual(app.auth_config_path(), moved / "current.json")

    def test_a_database_written_before_the_rename_is_still_the_one_used(self):
        """Starting an empty flipparr.db beside a full comicarr.db would look
        exactly like losing the library."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir)
            legacy = config / "comicarr.db"
            with patch.dict("app.os.environ", {"FLIPPARR_DATABASE": str(config / "flipparr.db")}, clear=False):
                # nothing on disk yet: a new install gets the new name
                self.assertEqual(app.catalog_database_path(), config / "flipparr.db")
                legacy.write_bytes(b"")
                self.assertEqual(app.catalog_database_path(), legacy)
                # once the new one exists it wins, so a migrated install moves on
                (config / "flipparr.db").write_bytes(b"")
                self.assertEqual(app.catalog_database_path(), config / "flipparr.db")

    def test_configured_paths_follow_the_environment_after_import(self):
        """Reading these at import time made the suites order-dependent.

        Whichever module imported `app` first fixed every path for the whole
        process; a module that prepared its own environment and imported `app`
        second had that environment silently ignored, because `import app` hit
        sys.modules and re-ran nothing.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            moved = Path(temp_dir)
            with patch.dict("app.os.environ", {
                "COMICARR_WEB_ROOT": str(moved / "web"),
                "COMICARR_DATABASE": str(moved / "catalog.db"),
                "COMICARR_AUTH_CONFIG": str(moved / "auth.json"),
                "COMICARR_PROVIDER_CONFIG": str(moved / "providers.json"),
                "COMICARR_ACQUISITION_CONFIG": str(moved / "services.json"),
                "COMICARR_SETTINGS_CONFIG": str(moved / "settings.json"),
            }):
                self.assertEqual(app.web_root(), (moved / "web").resolve())
                self.assertEqual(app.catalog_database_path(), moved / "catalog.db")
                self.assertEqual(app.auth_config_path(), moved / "auth.json")
                self.assertEqual(app.provider_config_path(), moved / "providers.json")
                self.assertEqual(app.acquisition_config_path(), moved / "services.json")
                self.assertEqual(app.settings_config_path(), moved / "settings.json")
                # The cache files sit beside whichever database is configured.
                self.assertEqual(app._provider_cache_file().parent, moved)
                self.assertEqual(app._remote_cache_file().parent, moved)

    def test_an_unset_web_root_is_absent_rather_than_the_working_directory(self):
        """Handler.do_GET treats a falsy web root as "no React build installed"."""
        environment = dict(os.environ)
        environment.pop("COMICARR_WEB_ROOT", None)
        with patch.dict("app.os.environ", environment, clear=True):
            self.assertIsNone(app.web_root())

    def test_the_catalog_store_reopens_when_the_database_moves(self):
        """Caching the first store would restore the import-time capture a layer up."""
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.db"
            second = Path(temp_dir) / "second.db"
            with patch.dict("app.os.environ", {"COMICARR_DATABASE": str(first)}):
                store_one = app.catalog_store()
                self.assertEqual(store_one.database_path, first)
            with patch.dict("app.os.environ", {"COMICARR_DATABASE": str(second)}):
                store_two = app.catalog_store()
                self.assertEqual(store_two.database_path, second)
            self.assertIsNot(store_one, store_two)

    def test_optional_provider_configuration_is_local_and_masked(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {
                "COMICARR_PROVIDER_CONFIG": str(Path(temp_dir) / "providers.json"),
                "METRON_API_TOKEN": "",
                "COMIC_VINE_API_KEY": "",
            },
        ):
            save_provider_config("metron", {"token": "secret-token", "enabled": True, "priority": 20})
            public = public_provider_config()
            metron = next(item for item in public["providers"] if item["id"] == "metron")
            self.assertTrue(metron["configured"])
            self.assertTrue(metron["enabled"])
            self.assertNotIn("token", metron)
            self.assertNotIn("secret-token", str(public))
            self.assertEqual((Path(temp_dir) / "providers.json").stat().st_mode & 0o777, 0o600)

    def test_acquisition_service_configuration_is_local_and_masked(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_ACQUISITION_CONFIG": str(Path(temp_dir) / "acquisition-services.json")},
        ):
            config_path = Path(temp_dir) / "acquisition-services.json"
            public = save_acquisition_service_config("prowlarr", {
                "url": "http://comic-nas.local:9696/",
                "apiKey": "prowlarr-secret",
                "enabled": True,
            })

            prowlarr = next(service for service in public["services"] if service["id"] == "prowlarr")
            self.assertEqual(prowlarr["url"], "http://comic-nas.local:9696")
            self.assertTrue(prowlarr["configured"])
            self.assertTrue(prowlarr["enabled"])
            self.assertEqual(prowlarr["credentialHint"], "Saved locally")
            self.assertNotIn("apiKey", prowlarr)
            self.assertNotIn("prowlarr-secret", str(public))
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_direct_download_settings_saved_under_the_old_name_are_kept(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_ACQUISITION_CONFIG": str(Path(temp_dir) / "acquisition-services.json")},
        ):
            (Path(temp_dir) / "acquisition-services.json").write_text(json.dumps(
                {"getcomics": {"enabled": True, "url": "https://comics.example"}}))
            config = app.load_acquisition_service_config()
            self.assertEqual((config["direct_site"]["enabled"], config["direct_site"]["url"]),
                             (True, "https://comics.example"))
            self.assertNotIn("getcomics", config)

    def test_the_old_direct_download_folder_moves_to_the_new_name_once(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ", {"COMICARR_ACQUISITION_STAGING": temp_dir},
        ):
            old = Path(temp_dir) / "getcomics" / "356"
            old.mkdir(parents=True)
            (old / "Example 002.cbz").write_bytes(b"PK")
            folder = app.acquisition_staging_dir("direct_site")
            self.assertEqual(folder, Path(temp_dir) / "direct_site")
            self.assertTrue((folder / "356" / "Example 002.cbz").exists(), "the records now point here")
            self.assertFalse((Path(temp_dir) / "getcomics").exists())
            (Path(temp_dir) / "getcomics").mkdir()
            app.acquisition_staging_dir("direct_site")
            self.assertTrue((folder / "356").exists(), "an existing new folder is never replaced")

    def test_collected_editions_default_off_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_SETTINGS_CONFIG": str(Path(temp_dir) / "settings.json")},
        ):
            settings_path = Path(temp_dir) / "settings.json"
            # Assert the setting this test is about, not the whole document:
            # comparing the entire dict broke the moment another setting was
            # added, on a test that has nothing to do with it.
            self.assertFalse(app.load_app_settings()["collectedEditionsEnabled"])
            self.assertFalse(settings_path.exists())

            updated = app.save_app_settings({"collectedEditionsEnabled": True})
            self.assertEqual(updated["collectedEditionsEnabled"], True)
            self.assertTrue(settings_path.exists())
            self.assertTrue(app.load_app_settings()["collectedEditionsEnabled"])
            self.assertTrue(app.collected_editions_enabled())

            app.save_app_settings({"collectedEditionsEnabled": False})
            self.assertFalse(app.collected_editions_enabled())

    def test_auto_scan_defaults_on_and_saves_only_an_offered_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_SETTINGS_CONFIG": str(Path(temp_dir) / "settings.json")},
        ):
            settings = app.load_app_settings()
            self.assertIs(settings["autoScanEnabled"], True, "on unless someone turns it off")
            self.assertEqual(settings["autoScanIntervalMinutes"], 60)
            self.assertEqual(app.save_app_settings({"autoScanIntervalMinutes": 360})["autoScanIntervalMinutes"], 360)
            self.assertEqual(app.load_app_settings()["autoScanIntervalMinutes"], 360)
            for offered_nowhere in (7, "60", True):
                with self.subTest(interval=offered_nowhere), self.assertRaises(ValueError):
                    app.save_app_settings({"autoScanIntervalMinutes": offered_nowhere})
            app.save_app_settings({"autoScanEnabled": False})
            self.assertIs(app.load_app_settings()["autoScanEnabled"], False)

    def test_an_automatic_scan_is_due_when_its_interval_has_passed_and_none_is_running(self):
        now = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)

        def ago(minutes):
            return (now - dt.timedelta(minutes=minutes)).isoformat()
        on = {"autoScanEnabled": True, "autoScanIntervalMinutes": 60}
        roots = [{"path": "/comics", "recursive": True}]

        def due(settings=on, roots=roots, last=None, running=None):
            return app.auto_scan_due(settings, {"roots": roots, "lastScanAt": last, "activeSince": running}, now)
        self.assertTrue(due(last=ago(61)))
        self.assertFalse(due(last=ago(30)))
        self.assertTrue(due(last=None), "a library never scanned")
        self.assertFalse(due(settings={**on, "autoScanEnabled": False}, last=ago(600)))
        self.assertFalse(due(roots=[], last=None), "no library folder yet")
        self.assertFalse(due(last=ago(600), running=ago(10)), "a scan is already running")
        self.assertTrue(due(last=ago(600), running=ago(180)),
                        "a scan cut off by a restart must not hold every later one back")

    def test_a_saved_language_preference_is_read_back(self):
        """The loader used to copy only the boolean settings.

        A preferred language was validated, written to disk, and then
        silently ignored on every read, so the whole feature ran on the
        default. It looked like it worked only because the default is en.
        """
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_SETTINGS_CONFIG": str(Path(temp_dir) / "settings.json")},
        ):
            self.assertEqual(app.preferred_language(), "en")
            app.save_app_settings({"preferredLanguage": "fr"})
            self.assertEqual(app.load_app_settings()["preferredLanguage"], "fr")
            self.assertEqual(app.preferred_language(), "fr")

            app.save_app_settings({"preferredLanguage": ""})
            self.assertEqual(app.preferred_language(), "")

    def test_a_malformed_saved_setting_falls_back_to_the_default(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_SETTINGS_CONFIG": str(Path(temp_dir) / "settings.json")},
        ):
            (Path(temp_dir) / "settings.json").write_text(json.dumps({
                "preferredLanguage": 7, "collectedEditionsEnabled": "yes",
            }))
            settings = app.load_app_settings()
            self.assertEqual(settings["preferredLanguage"], "en")
            self.assertFalse(settings["collectedEditionsEnabled"])

    def test_app_settings_rejects_unknown_keys_and_non_bool(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_SETTINGS_CONFIG": str(Path(temp_dir) / "settings.json")},
        ):
            with self.assertRaises(ValueError):
                app.save_app_settings({"somethingElse": True})
            with self.assertRaises(ValueError):
                app.save_app_settings({"collectedEditionsEnabled": "yes"})
            with self.assertRaises(ValueError):
                app.save_app_settings({})

    def test_catalog_payload_exposes_the_collected_editions_flag(self):
        store = Mock()
        store.catalog.return_value = {"series": [], "enrichment": {}}
        store.metadata_provider_available.return_value = True
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order", return_value=[]
        ), patch("app.collected_editions_enabled", return_value=True):
            app._CATALOG_CACHE.clear()  # this test's mocks, not an earlier test's catalog
            payload = catalog_api_payload(viewer_id=1)
        self.assertTrue(payload["collectedEditionsEnabled"])

    def test_prowlarr_connection_uses_api_key_header(self):
        with patch("app.load_acquisition_service_config", return_value={
            "prowlarr": {"url": "http://comic-nas.local:9696", "apiKey": "saved-key"}
        }), patch("app.fetch_json_with_headers", return_value={"version": "1.36.3"}) as fetch:
            result = test_acquisition_service_connection("prowlarr")

        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["detail"], "Connected to Prowlarr 1.36.3.")
        url, headers = fetch.call_args.args
        self.assertEqual(url, "http://comic-nas.local:9696/api/v1/system/status")
        self.assertEqual(headers["X-Api-Key"], "saved-key")

    def test_sabnzbd_connection_uses_version_endpoint_and_category(self):
        with patch("app.load_acquisition_service_config", return_value={
            "sabnzbd": {
                "url": "http://comic-nas.local:8080", "apiKey": "saved-key", "category": "graphic-novels"
            }
        }), patch("app.fetch_json_with_headers", return_value={"version": "4.5.3"}) as fetch:
            result = test_acquisition_service_connection("sabnzbd")

        self.assertEqual(result["status"], "connected")
        self.assertIn("SABnzbd 4.5.3", result["detail"])
        self.assertIn("graphic-novels", result["detail"])
        url, headers = fetch.call_args.args
        self.assertTrue(url.startswith("http://comic-nas.local:8080/api?"))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["mode"], ["version"])
        self.assertEqual(query["apikey"], ["saved-key"])
        self.assertNotIn("X-Api-Key", headers)

    def test_acquisition_service_url_rejects_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ",
            {"COMICARR_ACQUISITION_CONFIG": str(Path(temp_dir) / "acquisition-services.json")},
        ):
            with self.assertRaisesRegex(ValueError, "API key field"):
                save_acquisition_service_config("sabnzbd", {
                    "url": "http://admin:secret@comic-nas.local:8080",
                    "apiKey": "key",
                })

    def test_metron_matches_by_gcd_id_and_returns_issue_enrichment(self):
        context = {"title": "Absolute Batman", "year": 2024, "gcdSeriesId": "216143"}
        responses = [
            {"results": [{"id": 77, "series": "Absolute Batman (2024)", "year_began": 2024}]},
            {"id": 77, "resource_url": "https://metron.cloud/series/absolute-batman/"},
            {"results": [{"id": 701, "number": "1", "store_date": "2024-10-09", "image": "https://covers/1.jpg"}], "next": None},
        ]
        with patch("app.fetch_json_with_headers", side_effect=responses) as fetch:
            series_id, _, entries, _ended = _metron_issue_entries(context, "token")
        self.assertEqual(series_id, "77")
        self.assertEqual(entries[0]["publication_date"], "2024-10-09")
        self.assertEqual(entries[0]["cover"], "https://covers/1.jpg")
        self.assertIn("gcd_id=216143", fetch.call_args_list[0].args[0])

    def test_metron_reprints_preserve_unknown_and_partial_relationships(self):
        coverage = _metron_reprint_coverage([
            {"issue": {"series": {"name": "Alex + Ada"}, "number": "1"}},
            {"issue": {"series": {"name": "Alex + Ada"}, "number": "2"}},
            {
                "issue": {"series": {"name": "Image Firsts: Alex + Ada"}, "number": "1"},
                "relation_type": "partial story",
            },
        ])

        full = next(item for item in coverage if item["series"] == "Alex + Ada")
        partial = next(item for item in coverage if item["series"].startswith("Image Firsts"))
        self.assertEqual(full["issues"], ["1", "2"])
        self.assertEqual(full["relation_kind"], "unknown")
        self.assertEqual(partial["relation_kind"], "partial_story")

    def test_metron_collected_edition_candidate_requires_structured_reprints(self):
        parsed = app.ParsedFile(
            "/comics/Alex and Ada Vol 1.cbz", "Alex and Ada Vol 1.cbz", ".cbz",
            "Alex + Ada", volume=1, isbn="9781632150066",
        )
        responses = [
            {
                "results": [{
                    "id": 900, "series": {"name": "Alex + Ada"}, "number": "1",
                }],
                "next": None,
            },
            {
                "id": 900, "series": {"id": 90, "name": "Alex + Ada",
                    "series_type": {"id": 10, "name": "Trade Paperback"}}, "number": "1",
                "title": "Volume One", "isbn": "978-1-63215-006-6", "page": 128,
                "desc": "Collects ALEX + ADA #1-5.",
                "publisher": {"name": "Image Comics"},
                "image": "https://metron.example/alex-ada-v1.jpg",
                "resource_url": "https://metron.example/issue/900/",
                "reprints": [
                    {"id": 100 + number, "issue": f"Alex + Ada (2013) #{number}"}
                    for number in range(1, 6)
                ],
            },
        ]
        with patch("app.fetch_provider_json", side_effect=responses) as fetch:
            candidates = _metron_collected_edition_candidates(parsed, "token")

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["record_type"], "collected_edition")
        self.assertEqual(candidate["cover"], "https://metron.example/alex-ada-v1.jpg")
        self.assertEqual(candidate["matched_edition"]["isbn_13"], ["9781632150066"])
        self.assertEqual(candidate["matched_edition"]["number_of_pages"], 128)
        self.assertEqual(candidate["provider_evidence"]["providerPublicationId"], "90")
        self.assertEqual(candidate["description"], "Collects ALEX + ADA #1-5.")
        self.assertEqual(candidate["format"], "Trade Paperback")
        self.assertEqual(candidate["matched_edition"]["coverage"][0]["relation_kind"], "unknown")
        self.assertEqual(candidate["matched_edition"]["coverage"][0]["series"], "Alex + Ada (2013)")
        self.assertEqual(
            candidate["matched_edition"]["coverage"][0]["issues"],
            ["1", "2", "3", "4", "5"],
        )
        self.assertIn("series_q=Alex+%2B+Ada", fetch.call_args_list[0].args[1])

    def test_metron_collected_edition_without_reprints_is_not_a_coverage_match(self):
        parsed = app.ParsedFile(
            "/comics/Example Vol 1.cbz", "Example Vol 1.cbz", ".cbz", "Example", volume=1,
        )
        responses = [
            {"results": [{"id": 12, "series": {"name": "Example"}, "number": "1"}], "next": None},
            {"id": 12, "series": {"name": "Example", "series_type": {"name": "Trade Paperback"}}, "number": "1", "reprints": []},
        ]
        with patch("app.fetch_provider_json", side_effect=responses):
            self.assertEqual(_metron_collected_edition_candidates(parsed, "token"), [])

    def test_metron_single_issue_reprint_is_not_mislabeled_a_collection(self):
        parsed = app.ParsedFile("/comics/Example Vol 1.cbz", "Example Vol 1.cbz", ".cbz", "Example", volume=1)
        responses = [
            {"results": [{"id": 12, "series": {"name": "Example"}, "number": "1"}], "next": None},
            {"id": 12, "series": {"name": "Example", "series_type": {"name": "Single Issue"}},
             "number": "1", "reprints": [{"id": 1, "issue": "Example (2001) #1"}]},
        ]
        with patch("app.fetch_provider_json", side_effect=responses):
            self.assertEqual(_metron_collected_edition_candidates(parsed, "token"), [])

    def test_metron_unparsed_reference_is_retained_on_collected_candidate(self):
        parsed = app.ParsedFile("/comics/Example Vol 1.cbz", "Example Vol 1.cbz", ".cbz", "Example", volume=1)
        reprints = [{"id": 91, "issue": "Unnumbered supplement"}]
        responses = [
            {"results": [{"id": 12, "series": {"name": "Example"}, "number": "1"}], "next": None},
            {"id": 12, "series": {"name": "Example", "series_type": {"name": "Trade Paperback"}},
             "number": "1", "reprints": reprints},
        ]
        with patch("app.fetch_provider_json", side_effect=responses):
            candidate = _metron_collected_edition_candidates(parsed, "token")[0]
        self.assertEqual(candidate["matched_edition"]["coverage"], [])
        self.assertEqual(candidate["provider_evidence"]["reprints"][0]["raw"], reprints[0])

    def test_comic_vine_fetches_and_retains_descriptions_on_every_page(self):
        description = '<p>Collects <a href="/run/4050-9/">Synthetic</a> #6-10.</p>'
        volume = {"id": 88, "name": "Synthetic: Book Two", "start_year": "2020",
                  "publisher": {"id": 2, "name": "Example Press"},
                  "description": "<p>A collected edition.</p>"}
        responses = [
            {"status_code": 1, "results": volume},
            {"status_code": 1, "results": [{"id": 801, "issue_number": "1", "volume": {"id": 88},
                 "description": description}], "number_of_total_results": 2},
            {"status_code": 1, "results": [{"id": 802, "issue_number": "2", "volume": {"id": 88},
                 "description": None}], "number_of_total_results": 2},
        ]
        with patch("app.fetch_provider_json", side_effect=responses) as fetch:
            _, _, entries = _comic_vine_issue_entries({"title": volume["name"], "comicVineVolumeId": "88"}, "key")
        self.assertEqual(entries[0]["description"], description)
        self.assertEqual(entries[0]["provider_evidence"]["description"], description)
        self.assertEqual(entries[0]["provider_evidence"]["publicationContext"]["description"], volume["description"])
        self.assertEqual(entries[0]["provider_evidence"]["providerPublicationId"], "88")
        self.assertIsNone(entries[1]["description"])
        self.assertTrue(entries[1]["provider_evidence"]["descriptionSupplied"])
        for call in fetch.call_args_list:
            fields = urllib.parse.parse_qs(urllib.parse.urlsplit(call.args[1]).query)["field_list"][0].split(",")
            self.assertIn("description", fields)
        self.assertIn("offset=1", fetch.call_args_list[-1].args[1])

    def test_comic_vine_rejects_issue_from_another_publication(self):
        responses = [
            {"status_code": 1, "results": {"id": 88, "name": "Synthetic"}},
            {"status_code": 1, "results": [{"id": 801, "issue_number": "1", "volume": {"id": 99}}],
             "number_of_total_results": 1},
        ]
        with patch("app.fetch_provider_json", side_effect=responses), self.assertRaisesRegex(ValueError, "different publication"):
            _comic_vine_issue_entries({"title": "Synthetic", "comicVineVolumeId": "88"}, "key")

    def test_comic_vine_discovery_keeps_description_for_match_workbench(self):
        row = {"id": 88, "name": "Synthetic", "start_year": "2020", "description": "<p>A collected edition.</p>"}
        with patch("app.fetch_provider_json", return_value={"status_code": 1, "results": [row]}) as fetch, patch(
            "app._discovery_library_view",
            return_value={"keys": set(), "providerIds": set()}
        ):
            found = app.discover_comic_vine_series("Synthetic", "key")["results"]
        self.assertEqual(found[0]["description"], row["description"])
        fields = urllib.parse.parse_qs(urllib.parse.urlsplit(fetch.call_args.args[1]).query)["field_list"][0].split(",")
        self.assertIn("description", fields)

    def test_comic_vine_returns_named_issue_metadata(self):
        context = {"title": "Absolute Batman", "year": 2024}
        responses = [
            {"status_code": 1, "results": [{"id": 88, "name": "Absolute Batman", "start_year": 2024, "site_detail_url": "https://comicvine/88"}]},
            {"status_code": 1, "results": [{"id": 801, "name": "The Zoo", "issue_number": "1", "store_date": "2024-10-09", "image": {"medium_url": "https://covers/cv1.jpg"}, "site_detail_url": "https://comicvine/801"}], "number_of_total_results": 1},
        ]
        with patch("app.fetch_json_with_headers", side_effect=responses):
            volume_id, _, entries = _comic_vine_issue_entries(context, "key")
        self.assertEqual(volume_id, "88")
        self.assertEqual(entries[0]["title"], "The Zoo")
        self.assertEqual(entries[0]["publication_year"], 2024)

    def test_fix_match_strict_year_rejects_wrong_comic_vine_run(self):
        context = {"title": "Batman", "year": 2025, "strictYear": True}
        response = {
            "status_code": 1,
            "results": [{
                "id": 88, "name": "Batman", "start_year": 1940,
                "publisher": {"name": "DC Comics"},
            }],
        }
        with patch("app.fetch_json_with_headers", return_value=response) as fetch:
            with self.assertRaisesRegex(ValueError, "requested publication year"):
                _comic_vine_issue_entries(context, "key")
        fetch.assert_called_once()

    def test_comic_vine_strict_year_selects_current_same_name_run(self):
        context = {"title": "Batman", "year": 2025, "strictYear": True}
        responses = [
            {"status_code": 1, "results": [
                {"id": 796, "name": "Batman", "start_year": 1940,
                 "publisher": {"name": "DC Comics"}},
                {"id": 160672, "name": "Batman", "start_year": 2025,
                 "publisher": {"name": "DC Comics"},
                 "site_detail_url": "https://comicvine/160672"},
            ]},
            {"status_code": 1, "results": [
                {"id": 9001, "name": "Vast Colors in the Dark",
                 "issue_number": "1", "store_date": "2025-09-03"},
            ], "number_of_total_results": 1},
        ]
        with patch("app.fetch_json_with_headers", side_effect=responses) as fetch:
            volume_id, _, entries = _comic_vine_issue_entries(context, "key")
        self.assertEqual(volume_id, "160672")
        self.assertEqual(entries[0]["publication_year"], 2025)
        search_url = fetch.call_args_list[0].args[0]
        self.assertIn("/search/?", search_url)
        self.assertIn("resources=volume", search_url)

    def test_fix_match_strict_year_rejects_wrong_metron_run(self):
        context = {"title": "Batman", "year": 2025, "strictYear": True}
        response = {
            "results": [{"id": 77, "series": "Batman (1940)", "year_began": 1940}],
        }
        with patch("app.fetch_json_with_headers", return_value=response) as fetch:
            with self.assertRaisesRegex(ValueError, "requested publication year"):
                _metron_issue_entries(context, "token")
        fetch.assert_called_once()

    def test_comic_vine_prefers_canonical_issue_count_over_incorrect_local_year(self):
        context = {
            "title": "Birthright", "year": 2015, "publisher": "Image Comics Inc.",
            "gcdIssueEntries": [{"number": str(number)} for number in range(1, 51)],
        }
        responses = [
            {"status_code": 1, "results": [
                {"id": 11, "name": "Birthright", "start_year": "2015", "count_of_issues": 8,
                 "publisher": {"name": "Delcourt"}, "site_detail_url": "https://comicvine/11"},
                {"id": 88, "name": "Birthright", "start_year": "2014", "count_of_issues": 50,
                 "publisher": {"name": "Image"}, "site_detail_url": "https://comicvine/88"},
            ]},
            {"status_code": 1, "results": [
                {"id": 801, "name": None, "issue_number": "1", "store_date": "2014-10-08"},
            ], "number_of_total_results": 1},
        ]
        with patch("app.fetch_json_with_headers", side_effect=responses):
            volume_id, _, entries = _comic_vine_issue_entries(context, "key")
        self.assertEqual(volume_id, "88")
        self.assertEqual(entries[0]["publication_year"], 2014)

    def test_provider_broker_classifies_a_numbered_only_run(self):
        store = Mock()
        store.issue_metadata_summary.side_effect = [
            {"issueCount": 2, "missingTitleCount": 2, "rawMissingTitleCount": 2,
             "missingDateCount": 0, "missingTitleIssues": ["1", "2"], "missingDateIssues": []},
            {"issueCount": 2, "missingTitleCount": 2, "rawMissingTitleCount": 2,
             "missingDateCount": 0, "missingTitleIssues": ["1", "2"], "missingDateIssues": []},
        ]
        store.get_series_sync_context.return_value = {
            "title": "Example", "year": 2020, "knownGcdIssueIds": ["101"],
        }
        store.apply_issue_list.return_value = {
            "provider": "metron", "issueCount": 2, "titleCount": 0,
        }
        with patch("app.catalog_store", return_value=store), patch(
            "app.sync_gcd_issue_catalog",
            return_value={"provider": "gcd", "status": "complete", "issueCount": 2, "titleCount": 0,
                          "metadata": {"status": "complete", "titleCount": 0}},
        ), patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "priority": 20, "token": "token"},
        }), patch("app._metron_issue_entries", return_value=(
            "9", "https://metron/9", [{"number": "1"}, {"number": "2"}],
            {"state": "unknown", "year": None},
        )):
            result = sync_issue_catalog(7)
        self.assertEqual(result["metadata"]["titlePolicy"], "numbered_only")
        self.assertEqual(result["metadata"]["missingTitleCount"], 0)
        self.assertEqual(store.apply_issue_list.call_args.kwargs["status"], "complete_to_date")
        detail_call = store.update_issue_catalog_detail.call_args
        self.assertEqual(detail_call.kwargs["title_policy"], "numbered_only")

    def test_issue_metadata_bulk_refresh_defers_without_losing_retained_data(self):
        entries = [
            {"number": "1", "provider_id": "101", "title": "Retained Title", "publication_year": 2024},
            {"number": "2", "provider_id": "102"},
        ]
        rate_limit = urllib.error.HTTPError(
            "https://www.comics.org/api/series/55/overview/", 429,
            "Too Many Requests", {"Retry-After": "120"}, None,
        )
        with patch("app.fetch_gcd_json", side_effect=rate_limit) as fetch:
            hydrated, metadata = _hydrate_gcd_issue_entries_with_status(entries, "55")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(metadata["status"], "deferred")
        self.assertEqual(metadata["retryAfterSeconds"], 120)
        self.assertEqual(hydrated[0]["title"], "Retained Title")
        self.assertNotIn("title", hydrated[1])

    def test_confirmed_collection_imports_separate_numbering_runs(self):
        def candidate(provider_id, title, count):
            return {
                "providerSeriesId": provider_id, "title": title, "yearBegan": 2010,
                "yearEnded": 2011, "publisher": "Example Press", "issueCount": count,
                "_series": {
                    "api_url": f"https://www.comics.org/api/series/{provider_id}/",
                    "active_issues": [f"https://www.comics.org/api/issue/{provider_id}{n}/" for n in range(1, count + 1)],
                    "issue_descriptors": [str(n) for n in range(1, count + 1)],
                },
            }

        candidates = [candidate("10", "Example", 3), candidate("11", "Example: Second Arc", 2)]
        store = Mock()
        store.ensure_provider_series_run.side_effect = [
            {"id": "7", "title": "Example", "created": False},
            {"id": "11", "title": "Example: Second Arc", "created": True},
        ]
        store.catalog.return_value = {"series": [{"id": "7", "family": None}]}
        store.reassign_matching_editions.return_value = {"moved": [], "count": 0}
        store.create_series_family.return_value = {"id": "5", "name": "Example"}
        store.set_story_structure.return_value = {
            "collection": {"id": "5", "name": "Example"}, "arcCount": 2,
        }
        with patch("app._find_gcd_series_runs", return_value=({
            "title": "Example", "publisher": "Example Press",
        }, candidates)), patch("app._hydrate_gcd_issue_entries", side_effect=lambda entries, *_: entries), patch("app.catalog_store", return_value=store):
            result = confirm_gcd_series_collection(7, ["10", "11"], "Example")
        self.assertEqual((result["runCount"], result["issueCount"]), (2, 5))
        store.create_series_family.assert_called_once_with("Example", [7, 11])
        arcs = store.set_story_structure.call_args.args[1]
        self.assertEqual([arc["name"] for arc in arcs], ["Original series", "Second Arc"])

    def test_confirmed_series_run_import_records_manual_confirmation_source(self):
        raw_series = {
            "name": "Example", "year_began": 2024, "year_ended": 2025,
            "api_url": "https://www.comics.org/api/series/55/",
            "active_issues": ["https://www.comics.org/api/issue/101/"],
            "issue_descriptors": ["1"],
        }
        store = Mock()
        store.apply_issue_list.return_value = {
            "seriesId": "7", "status": "complete", "issueCount": 1, "provider": "gcd",
        }
        with patch("app._find_gcd_series_runs", return_value=({}, [{
            "providerSeriesId": "55", "_series": raw_series,
        }])), patch("app._hydrate_gcd_issue_entries", side_effect=lambda entries, *_: entries), patch("app.catalog_store", return_value=store):
            result = confirm_gcd_series_run(7, "55")
        self.assertEqual(result["issueCount"], 1)
        self.assertEqual(store.apply_issue_list.call_args.kwargs["source"], "user-confirmed series run")

    def test_series_run_ranking_prefers_title_year_publisher_and_owned_overlap(self):
        def series(provider_id, name, year, issue_count, publishing_format="limited series", publisher=None):
            return {
                "name": name, "country": "us", "language": "en", "year_began": year,
                "year_ended": year + 1, "publishing_format": publishing_format,
                "publisher": publisher, "api_url": f"https://www.comics.org/api/series/{provider_id}/",
                "active_issues": [f"https://www.comics.org/api/issue/{provider_id}{number}/" for number in range(1, issue_count + 1)],
                "issue_descriptors": [str(number) for number in range(1, issue_count + 1)],
            }

        context = {
            "title": "Southern Bastards", "aliases": [], "year": 2014,
            "publisher": "Image Comics", "ownedIssueNumbers": [str(number) for number in range(1, 9)],
        }
        candidates = rank_gcd_series_runs(context, [
            series("10", "Southern Bastards", 2014, 20, publisher="Image Comics"),
            series("11", "Southern Bastards", 2025, 3, publisher="Other Press"),
            series("12", "Southern Bastards", 2014, 4, "collected edition", "Image Comics"),
        ])
        self.assertEqual([candidate["providerSeriesId"] for candidate in candidates], ["10", "11"])
        self.assertEqual(candidates[0]["ownedOverlap"], 8)
        self.assertEqual(candidates[0]["issueCount"], 20)
        self.assertEqual(candidates[1]["ownedOverlap"], 3)

    def test_series_run_ranking_collapses_cover_variants(self):
        candidate = rank_gcd_series_runs(
            {"title": "Example", "year": 2024, "publisher": None, "ownedIssueNumbers": ["1"]},
            [{
                "name": "Example", "country": "us", "language": "en", "year_began": 2024,
                "api_url": "https://www.comics.org/api/series/55/",
                "active_issues": [
                    "https://www.comics.org/api/issue/101/",
                    "https://www.comics.org/api/issue/102/",
                    "https://www.comics.org/api/issue/103/",
                ],
                "issue_descriptors": ["1", "1 [Variant Cover]", "2"],
            }],
        )[0]
        self.assertEqual(candidate["issueNumbers"], ["1", "2"])
        self.assertEqual(candidate["issueCount"], 2)

    def test_issue_catalog_sync_uses_verified_run_and_collapses_variants(self):
        store = Mock()
        store.get_series_sync_context.return_value = {
            "id": 7, "title": "Example", "year": 2024, "publisher": "Example Press",
            "aliases": [], "knownGcdIssueIds": ["101"], "gcdSeriesId": "55",
        }
        store.apply_issue_list.return_value = {
            "seriesId": "7", "status": "complete_to_date", "issueCount": 2, "provider": "gcd",
        }
        response = {
            "name": "Example", "country": "us", "language": "en", "year_began": 2024,
            "publishing_format": "ongoing series",
            "api_url": "https://www.comics.org/api/series/55/",
            "active_issues": [
                "https://www.comics.org/api/issue/101/",
                "https://www.comics.org/api/issue/102/",
                "https://www.comics.org/api/issue/103/",
            ],
            "issue_descriptors": ["1", "1 [Variant Cover]", "2"],
        }
        overview = [
            {"issue_id": 101, "number": "1", "key_date": "2024-10-01", "longest_story": {"title": "Chapter One"}},
            {"issue_id": 103, "number": "2", "key_date": "2024-11-01", "longest_story": {"title": "Chapter Two"}},
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app.fetch_gcd_json", side_effect=[response, overview]
        ) as fetch:
            result = sync_gcd_issue_catalog(7)
        self.assertEqual(result["issueCount"], 2)
        self.assertEqual(result["metadata"]["status"], "complete")
        self.assertEqual(fetch.call_count, 2)
        args = store.apply_issue_list.call_args.args
        self.assertEqual(args[:4], (7, "gcd", "55", "https://www.comics.org/api/series/55/"))
        self.assertEqual([(entry["number"], entry["provider_id"]) for entry in args[4]], [("1", "101"), ("2", "103")])
        self.assertEqual([entry["title"] for entry in args[4]], ["Chapter One", "Chapter Two"])
        self.assertEqual([entry["publication_date"] for entry in args[4]], ["2024-10-01", "2024-11-01"])

    def test_trade_filename(self):
        item = parse_filename(Path("Batman - I Am Gotham Vol 1 (2017) TPB.cbz"))
        self.assertEqual(item.title, "Batman I Am Gotham")
        self.assertEqual(item.volume, 1)
        self.assertEqual(item.year, 2017)
        self.assertEqual(item.format, "trade paperback")

    def test_upload_identifier_does_not_split_a_run(self):
        """Saga_vol2_1398374447.cbz used to become its own run titled "Saga 1398374447"."""
        stamped = parse_filename(Path("Saga_vol2_1398374447.cbz"))
        clean = parse_filename(Path("saga_vol8.cbz"))
        self.assertEqual(stamped.title, "Saga")
        self.assertEqual(stamped.volume, 2)
        self.assertEqual(stamped.title.lower(), clean.title.lower())

    def test_short_numbers_in_titles_survive(self):
        """The identifier guard is limited to five or more digits.

        Years, issue numbers and volume numbers are at most four digits and are
        handled by their own rules, so the guard must leave them alone.
        """
        self.assertEqual(parse_filename(Path("Saga vol 2 (2013).cbz")).year, 2013)
        self.assertEqual(parse_filename(Path("Saga vol 2 (2013).cbz")).volume, 2)
        self.assertEqual(parse_filename(Path("Batman 100 (2020).cbz")).title, "Batman")
        # A four-digit run is left in place by the guard; only the pre-existing
        # year rule may remove one.
        self.assertEqual(parse_filename(Path("Judge Dredd 1234.cbz")).title, "Judge Dredd 1234")

    def test_a_decimal_issue_number_survives_the_dots(self):
        # DC's Villains Month (2013): issue 23.2, not issue 2 of "Green Lantern 023".
        for name in ("Green Lantern 023.2 (2013) (Digital) (Nahga-Empire).cbr",
                     "Green.Lantern.023.2.(2013).(Digital).(Nahga-Empire).cbr"):
            parsed = parse_filename(Path(name))
            self.assertEqual((parsed.title, parsed.issue, parsed.year), ("Green Lantern", "23.2", 2013), name)
        # A dot before a year is still a separator.
        dotted = parse_filename(Path("Batman.001.2016.Digital.cbz"))
        self.assertEqual((dotted.title, dotted.issue, dotted.year), ("Batman", "1", 2016))
        # A half issue, however it is written, is 0.5.
        for name in ("Ultimate Spider-man 0.5 (2002) (digital - Empire).cbr", "Ultimate Spider-Man ½ (2002).cbz"):
            parsed = parse_filename(Path(name))
            self.assertEqual((parsed.issue, parsed.year), ("0.5", 2002), name)
            self.assertEqual(app._issue_key(parsed.issue), app._issue_key("½"))
        # A lettered tie-in keeps its letters; a plain number beside a word does not grow them.
        au = parse_filename(Path("Superior Spider-Man 006 AU (2013) (digital-TheGroup).cbr"))
        self.assertEqual((au.title, au.issue, au.year), ("Superior Spider-Man", "6AU", 2013))
        self.assertEqual(parse_filename(Path("Superior Spider-Man 006 (2013) (digital-TheGroup).cbr")).issue, "6")
        self.assertEqual(parse_filename(Path("Batman 100 (2020).cbz")).issue, "100")

    def test_omnibus_with_isbn(self):
        item = parse_filename(Path("Wolverine Omnibus Vol 1 978-1-302-95008-8.cbz"))
        self.assertEqual(item.title, "Wolverine")
        self.assertEqual(item.volume, 1)
        self.assertEqual(item.format, "omnibus")
        self.assertEqual(item.isbn, "9781302950088")

    def test_decodes_release_filename_and_recognizes_padded_issue(self):
        item = parse_filename(Path("Absolute%20Batman%20001%20(2024)%20(c2c)%20(GD-Pmack).cbz"))
        self.assertEqual(item.filename, "Absolute Batman 001 (2024) (c2c) (GD-Pmack).cbz")
        self.assertEqual(item.title, "Absolute Batman")
        self.assertEqual(item.issue, "1")
        self.assertEqual(item.year, 2024)
        self.assertIsNone(item.volume)

    def test_four_digit_padded_issue_is_not_a_volume(self):
        item = parse_filename(Path("Birthright%200006%20(2015)%20(Digital)%20(Pym-Empire).cbz"))
        self.assertEqual(item.title, "Birthright")
        self.assertEqual(item.issue, "6")
        self.assertIsNone(item.volume)

    def test_unmarked_three_digit_issue_is_not_split_into_a_false_series(self):
        item = parse_filename(Path("Fables.105.(2011).(Digital).(NahgaEmpire).cbz"))
        self.assertEqual(item.title, "Fables")
        self.assertEqual(item.issue, "105")
        self.assertEqual(item.year, 2011)

    def test_image_resolution_token_is_not_mistaken_for_publication_year(self):
        item = parse_filename(Path("Fables.141.(2014).(2048px..c2c).cbz"))
        self.assertEqual((item.title, item.issue, item.year), ("Fables", "141", 2014))

    def test_verified_sab_import_uses_series_folder_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "library"
            completed = root / "completed"
            source_folder = completed / "Saga.001.2012"
            library.mkdir()
            source_folder.mkdir(parents=True)
            source = source_folder / "Saga 001 (2012).cbz"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("001.jpg", b"comic-page")
            store = Mock()
            store.get_acquisition_job_context.return_value = {
                "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
                "issueNumber": "1", "issueTitle": "Chapter One", "existingDirectory": None,
            }
            with patch("app.catalog_store", return_value=store):
                result = import_downloaded_comic(
                    {"job_id": 7, "sab_storage": str(source_folder), "release_title": "Saga.001.2012"},
                    library_root=library, completed_root=completed,
                )

            destination = library / "Image Comics" / "Saga (2012)" / "Saga (2012) #001 - Chapter One.cbz"
            self.assertEqual(Path(result["destination"]).resolve(), destination.resolve())
            self.assertTrue(source.is_file())
            self.assertTrue(destination.is_file())
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertFalse(result["alreadyPresent"])

    def test_verified_replacement_swaps_original_into_hidden_quarantine(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "library"
            completed = root / "completed"
            source_folder = completed / "Saga.001.2012"
            destination = library / "Image Comics" / "Saga (2012)" / "Saga (2012) #001.cbz"
            destination.parent.mkdir(parents=True)
            source_folder.mkdir(parents=True)
            destination.write_bytes(b"damaged-original")
            source = source_folder / "Saga 001 (2012).cbz"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("001.jpg", b"replacement-page")
            store = Mock()
            store.get_acquisition_job_context.return_value = {
                "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
                "issueNumber": "1", "issueTitle": None, "existingDirectory": str(destination.parent),
                "replacementId": "9", "replacementFileId": "4",
                "replacementFilePath": str(destination),
            }
            store.replacement_for_job.return_value = {
                "id": 9, "original_path": str(destination), "library_root": str(library),
            }
            with patch("app.catalog_store", return_value=store):
                result = import_downloaded_comic(
                    {"job_id": 7, "sab_storage": str(source_folder), "release_title": "Saga.001.2012"},
                    library_root=library, completed_root=completed,
                )

            quarantine = library / ".flipparr" / "quarantine" / "9" / "Image Comics" / "Saga (2012)" / destination.name
            self.assertEqual(quarantine.read_bytes(), b"damaged-original")
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(Path(result["quarantineOriginal"]).resolve(), quarantine.resolve())
            store.record_replacement_quarantine.assert_called_once()
            replacement_call = store.record_replacement_quarantine.call_args.args
            self.assertEqual(replacement_call[0], 9)
            self.assertEqual(Path(replacement_call[1]).resolve(), destination.resolve())
            self.assertEqual(Path(replacement_call[2]).resolve(), quarantine.resolve())

    def test_invalid_replacement_never_moves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "library"
            completed = root / "completed"
            source_folder = completed / "Saga.001.2012"
            destination = library / "Image Comics" / "Saga (2012)" / "Saga (2012) #001.cbz"
            destination.parent.mkdir(parents=True)
            source_folder.mkdir(parents=True)
            destination.write_bytes(b"damaged-original")
            source = source_folder / "Saga 001 (2012).cbz"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("notes.txt", b"no comic pages")
            store = Mock()
            store.get_acquisition_job_context.return_value = {
                "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
                "issueNumber": "1", "issueTitle": None, "existingDirectory": str(destination.parent),
                "replacementId": "9", "replacementFileId": "4",
                "replacementFilePath": str(destination),
            }
            with (
                patch("app.catalog_store", return_value=store),
                patch("app.select_downloaded_comic", return_value={"path": source}),
            ):
                with self.assertRaisesRegex(ValueError, "image pages"):
                    import_downloaded_comic(
                        {"job_id": 7, "sab_storage": str(source_folder), "release_title": "Saga.001.2012"},
                        library_root=library, completed_root=completed,
                    )
            self.assertEqual(destination.read_bytes(), b"damaged-original")
            self.assertFalse((library / ".flipparr").exists())

    def test_organized_issue_filename_keeps_story_title_out_of_series_title(self):
        item = parse_filename(Path("Absolute Flash (2025) #018 - Now You See Me.cbz"))
        self.assertEqual((item.title, item.issue, item.year), ("Absolute Flash", "18", 2025))

    def test_issue_filename_ignores_truncated_release_group(self):
        item = parse_filename(Path("Chew 015 (2010) (D) (Kingpin-Empire.cbr"))
        self.assertEqual((item.title, item.issue, item.year), ("Chew", "15", 2010))

    def test_sab_import_accepts_exact_issue_with_truncated_release_group(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "library"
            completed = root / "completed"
            source_folder = completed / "Chew 015 (2010) (D) (Kingpin-Empire"
            library.mkdir()
            source_folder.mkdir(parents=True)
            source = source_folder / "Chew 015 (2010) (D) (Kingpin-Empire.cbz"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("001.jpg", b"comic-page")
            store = Mock()
            store.get_acquisition_job_context.return_value = {
                "seriesTitle": "Chew", "seriesYear": 2009, "publisher": "Image",
                "issueNumber": "15", "issueTitle": "Just Desserts, 5 of 5",
                "existingDirectory": None,
            }
            with patch("app.catalog_store", return_value=store):
                result = import_downloaded_comic(
                    {"job_id": 7, "sab_storage": str(source_folder),
                     "release_title": "Chew 015 (2010) (D) (Kingpin-Empire"},
                    library_root=library, completed_root=completed,
                )

            destination = (
                library / "Image" / "Chew (2009)"
                / "Chew (2009) #015 - Just Desserts, 5 of 5.cbz"
            )
            self.assertEqual(Path(result["destination"]).resolve(), destination.resolve())
            self.assertTrue(destination.is_file())

    def test_sab_import_rejects_a_different_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "library"
            completed = root / "completed"
            source_folder = completed / "Saga.002.2012"
            library.mkdir()
            source_folder.mkdir(parents=True)
            with zipfile.ZipFile(source_folder / "Saga 002 (2012).cbz", "w") as archive:
                archive.writestr("001.jpg", b"comic-page")
            store = Mock()
            store.get_acquisition_job_context.return_value = {
                "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
                "issueNumber": "1", "issueTitle": None, "existingDirectory": None,
            }
            with patch("app.catalog_store", return_value=store):
                with self.assertRaisesRegex(ValueError, r"Saga 002 \(2012\)\.cbz is #2, not Saga #1"):
                    import_downloaded_comic(
                        {"job_id": 7, "sab_storage": str(source_folder), "release_title": "Saga.002.2012"},
                        library_root=library, completed_root=completed,
                    )
            self.assertEqual(list(library.rglob("*.cbz")), [])

    def test_completed_sab_job_is_only_fulfilled_after_verified_library_import(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Saga.001.2012", "sab_storage": None,
        }
        store = Mock()
        imported = {
            "source": "/downloads/complete/comics/Saga.001.2012/Saga.001.2012.cbz",
            "destination": "/comics/Image Comics/Saga (2012)/Saga (2012) #001 - Chapter One.cbz",
            "size": 1234, "sha256": "abc123", "alreadyPresent": False,
        }
        store.get_acquisition_job_context.return_value = {
            "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
            "issueNumber": "1", "issueTitle": "Chapter One", "publicationYear": 2012,
        }
        store.replacement_for_job.return_value = None
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/Saga.001.2012",
            },
        ), patch("app.import_downloaded_comic", return_value=imported) as importer:
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "imported")
        importer.assert_called_once()
        states = [call.args[1] for call in store.update_acquisition_download.call_args_list]
        self.assertEqual(states, ["completed", "importing", "imported"])
        store.ingest_acquisition_import.assert_called_once()
        store.update_acquisition_job.assert_called_once_with(
            7, "fulfilled", "Imported and verified as Saga (2012) #001 - Chapter One.cbz"
        )

    def test_a_download_sabnzbd_paused_as_encrypted_is_refused_and_the_next_release_tried(self):
        # In the queue, not the history, paused with a label: not "downloading".
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "paused-queue-id",
            "release_title": "Absolute Batman 024 (2025) (digital-mobile)", "release_key": "junk-key",
            "created_at": "2026-09-23T04:09:26+00:00",
        }
        store = Mock()
        store.record_acquisition_release_failure.return_value = {"failureCount": 1}
        fallback = {"id": "fallback-candidate", "title": "Absolute Batman 024 (2025) (Digital)"}
        with patch("app.catalog_store", return_value=store), \
             patch("app._sab_history_slot", return_value=None), \
             patch("app._sab_queue_slot", return_value={"nzo_id": "paused-queue-id", "status": "Paused", "labels": ["ENCRYPTED"], "percentage": "39"}), \
             patch("app._sab_forget_queued") as forget, \
             patch("app.search_prowlarr_releases", return_value={"candidateCount": 1, "candidates": [fallback], "job": {}}), \
             patch("app.send_release_to_sabnzbd", return_value={"status": "grabbed"}) as send:
            result = reconcile_acquisition_download(download)
        forget.assert_called_once_with(download)
        store.update_acquisition_download.assert_any_call(31, "failed", error="SABnzbd paused it: the archive is password-protected", failure_stage="download")
        self.assertEqual(store.record_acquisition_release_failure.call_args.args[:3], (7, "junk-key", "Absolute Batman 024 (2025) (digital-mobile)"))
        send.assert_called_once_with(7, "fallback-candidate")
        self.assertNotEqual(result.get("status"), "downloading")

    def test_a_download_a_person_paused_is_still_downloading(self):
        download = {"id": 31, "job_id": 7, "sab_nzo_id": "queue-id", "release_title": "x", "release_key": "k",
                    "created_at": "2026-09-23T04:09:26+00:00"}
        store = Mock()
        with patch("app.catalog_store", return_value=store), \
             patch("app._sab_history_slot", return_value=None), \
             patch("app._sab_queue_slot", return_value={"nzo_id": "queue-id", "status": "Paused", "labels": []}):
            result = reconcile_acquisition_download(download)
        self.assertEqual(result["status"], "downloading")
        store.record_acquisition_release_failure.assert_not_called()

    def test_failed_sab_download_automatically_uses_the_next_strong_release(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "failed-queue-id",
            "release_title": "Fables.095.failed", "release_key": "failed-key",
        }
        store = Mock()
        store.record_acquisition_release_failure.return_value = {"failureCount": 1}
        fallback = {"id": "fallback-candidate", "title": "Fables.095.fallback"}
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "failed-queue-id", "status": "Failed", "storage": "",
                "fail_message": "Repair failed, not enough repair blocks",
            },
        ), patch("app.search_prowlarr_releases", return_value={
            "candidateCount": 1, "candidates": [fallback],
        }) as search, patch("app.send_release_to_sabnzbd", return_value={
            "status": "grabbed", "queueIds": ["fallback-queue-id"],
        }) as send:
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "fallback_queued")
        self.assertTrue(result["automaticFallback"])
        store.update_acquisition_download.assert_called_once_with(
            31, "failed", sab_storage="",
            error="Repair failed, not enough repair blocks", failure_stage="download",
        )
        store.record_acquisition_release_failure.assert_called_once_with(
            7, "failed-key", "Fables.095.failed",
            "Repair failed, not enough repair blocks",
            # SABnzbd could not finish it: proof, so the release is barred,
            # and there are no finished files to keep.
            kind="download", sab_nzo_id=None, sab_storage=None,
        )
        search.assert_called_once_with(7)
        send.assert_called_once_with(7, "fallback-candidate")

    def test_failed_sab_download_stops_after_three_failed_releases(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "failed-queue-id",
            "release_title": "Fables.095.failed", "release_key": "failed-key",
        }
        store = Mock()
        store.record_acquisition_release_failure.return_value = {"failureCount": 3}
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "failed-queue-id", "status": "Failed", "storage": "",
                "fail_message": "Aborted, cannot be completed",
            },
        ), patch("app.search_prowlarr_releases") as search, patch(
            "app.send_release_to_sabnzbd"
        ) as send:
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["automaticFallback"])
        self.assertIn("stopped after 3 failed releases", result["error"])
        search.assert_not_called()
        send.assert_not_called()
        self.assertEqual(store.update_acquisition_job.call_args.args[1], "failed")

    def test_collected_volume_stays_active_until_every_replacement_issue_is_imported(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Saga.001.2012", "sab_storage": None,
        }
        imported = {
            "source": "/downloads/complete/comics/Saga.001.2012/Saga.001.2012.cbz",
            "destination": "/comics/Image Comics/Saga (2012)/Saga (2012) #001.cbz",
            "size": 1234, "sha256": "abc123", "alreadyPresent": False,
        }
        store = Mock()
        store.get_acquisition_job_context.return_value = {
            "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
            "issueNumber": "1", "issueTitle": None, "publicationYear": 2012,
        }
        store.replacement_for_job.return_value = {
            "id": 9, "unfinished_jobs": 5,
            "original_path": "/comics/Image Comics/Saga (2012)/Saga Vol. 1.cbz",
        }
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/Saga.001.2012",
            },
        ), patch("app.import_downloaded_comic", return_value=imported), patch(
            "app._move_original_to_quarantine"
        ) as quarantine_original:
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "imported")
        quarantine_original.assert_not_called()
        store.record_replacement_quarantine.assert_not_called()
        store.complete_file_replacement.assert_not_called()

    def test_last_replacement_issue_completes_the_recoverable_swap(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Saga.006.2012", "sab_storage": None,
        }
        quarantine = "/comics/.sonicboom/quarantine/9/Image Comics/Saga (2012)/Saga Vol. 1.cbz"
        imported = {
            "source": "/downloads/complete/comics/Saga.006.2012/Saga.006.2012.cbz",
            "destination": "/comics/Image Comics/Saga (2012)/Saga (2012) #006.cbz",
            "size": 1234, "sha256": "abc123", "alreadyPresent": False,
        }
        store = Mock()
        store.get_acquisition_job_context.return_value = {
            "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
            "issueNumber": "6", "issueTitle": None, "publicationYear": 2012,
        }
        store.replacement_for_job.return_value = {
            "id": 9, "unfinished_jobs": 0, "quarantine_path": quarantine,
            "original_path": "/comics/Image Comics/Saga (2012)/Saga Vol. 1.cbz",
        }
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/Saga.006.2012",
            },
        ), patch("app.import_downloaded_comic", return_value=imported), patch(
            "app._move_original_to_quarantine"
        ) as quarantine_original:
            result = reconcile_acquisition_download(download)

        self.assertTrue(result["replacementCompleted"])
        self.assertEqual(result["quarantineOriginal"], quarantine)
        quarantine_original.assert_not_called()
        store.complete_file_replacement.assert_called_once_with(9, quarantine)

    def test_completed_sab_job_stays_failed_when_post_processing_fails(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Saga.001.2012", "sab_storage": None,
        }
        store = Mock()
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/Saga.001.2012",
            },
        ), patch(
            "app.import_downloaded_comic",
            side_effect=ValueError("Downloaded archive did not match Saga #1"),
        ):
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "failed")
        self.assertIn("did not match", result["error"])
        self.assertEqual(
            store.update_acquisition_download.call_args_list[-1].args[1], "failed"
        )
        store.update_acquisition_job.assert_called_once_with(
            7,
            "failed",
            "Import needs attention: Downloaded archive did not match Saga #1",
        )

    def test_completed_sab_job_waits_when_finished_file_is_not_mounted_yet(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Saga.001.2012", "sab_storage": None,
        }
        store = Mock()
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/Saga.001.2012",
            },
        ), patch(
            "app.import_downloaded_comic",
            side_effect=CompletedDownloadNotVisible(
                "The completed SABnzbd download is not visible in the mounted comics folder"
            ),
        ):
            result = reconcile_acquisition_download(download)

        self.assertEqual(result["status"], "waiting_for_files")
        self.assertEqual(
            store.update_acquisition_download.call_args_list[-1].args[1],
            "waiting_for_files",
        )
        store.update_acquisition_job.assert_called_once_with(
            7,
            "grabbed",
            "SABnzbd finished; waiting for the completed file to appear in Flipparr",
        )

    def test_resolves_sab_host_file_path_inside_category_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "comics"
            release = root / "Fables.072.(2008).(Digital).(NahgaEmpire)"
            release.mkdir(parents=True)
            comic = release / "Fables.072.(2008).(Digital).(NahgaEmpire).cbz"
            comic.write_bytes(b"comic")

            resolved = _resolve_sab_download_source(
                "/data/downloads/usenet/complete/comics/"
                "Fables.072.(2008).(Digital).(NahgaEmpire)/"
                "Fables.072.(2008).(Digital).(NahgaEmpire).cbz",
                "Fables.072.(2008).(Digital).(NahgaEmpire)",
                root,
            )

        self.assertEqual(resolved, comic.resolve())

    def test_segments_a_title_without_spaces(self):
        item = parse_filename(Path("strangetalentoflutherstrode_vol2.cbz"))
        self.assertEqual(item.title, "strange talent of luther strode")
        self.assertEqual(item.volume, 2)
        self.assertTrue(any("word boundaries" in warning for warning in item.warnings))

    def test_segments_joiners_and_plural_words_without_false_boundaries(self):
        item = parse_filename(Path("lockeandkey_heavenandearth_headgames.epub"))
        self.assertEqual(item.title, "locke and key heaven and earth head games")

    def test_compound_segmentation_does_not_read_a_host_dictionary(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("host dictionary read")):
            birthright = parse_filename(Path("Birthright 0006 (2015).cbz"))
            head_games = parse_filename(Path("lockeandkey_headgames.epub"))

        self.assertEqual(birthright.title, "Birthright")
        self.assertEqual(head_games.title, "locke and key head games")

    def test_batch_enrichment_uses_bounded_parallel_workers_and_preserves_order(self):
        files = [parse_filename(Path(f"Book Vol {number}.cbz")) for number in range(1, 5)]
        barrier = threading.Barrier(4)
        thread_ids = set()
        thread_lock = threading.Lock()

        def fake_enrich(item):
            with thread_lock:
                thread_ids.add(threading.get_ident())
            barrier.wait(timeout=2)
            return {"filename": item.filename}

        with patch("app.scan_folder", return_value=files), patch("app.enrich", side_effect=fake_enrich):
            results = batch_enrich("/unused")
        self.assertEqual([result["filename"] for result in results], [item.filename for item in files])
        self.assertEqual(len(thread_ids), 4)

    def test_batch_failure_is_isolated_to_one_file(self):
        files = [parse_filename(Path("Good Vol 1.cbz")), parse_filename(Path("Bad Vol 2.cbz"))]

        def fake_enrich(item):
            if item.filename.startswith("Bad"):
                raise RuntimeError("adapter failed")
            return {"parsed": {"filename": item.filename}, "recommendation": {"title": "Good"}}

        with patch("app.scan_folder", return_value=files), patch("app.enrich", side_effect=fake_enrich):
            results = batch_enrich("/unused")
        self.assertEqual(results[0]["recommendation"]["title"], "Good")
        self.assertIsNone(results[1]["recommendation"])
        self.assertIn("adapter failed", results[1]["errors"]["analysis"])

    def test_scan_ignores_unsupported_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Book Vol 1 TPB.cbz").touch()
            (root / "Book Vol 2.epub").touch()
            (root / "notes.txt").touch()
            files = scan_folder(folder)
            self.assertEqual(len(files), 2)
            self.assertEqual({item.extension for item in files}, {".cbz", ".epub"})

    def test_scan_ignores_sonicboom_quarantine(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Current 001.cbz").touch()
            hidden = root / ".flipparr" / "quarantine" / "4"
            hidden.mkdir(parents=True)
            (hidden / "Old 001.cbz").touch()
            files = scan_folder(folder)
            self.assertEqual([item.filename for item in files], ["Current 001.cbz"])

    def test_page_template_does_not_interpret_css_braces(self):
        rendered = PAGE.replace("__FOLDER__", "/tmp/comics").replace("__CHECKED__", "checked").replace("__CONTENT__", "ok")
        self.assertIn("color-scheme: dark", rendered)
        self.assertIn('/tmp/comics', rendered)
        self.assertIn("/api/pick-folder", rendered)

    def test_reads_epub_package_metadata(self):
        container = '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'''
        package = '''<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><meta name="cover" content="cover-image"/><dc:title>Locke and Key: Heaven and Earth</dc:title><dc:creator>Joe Hill</dc:creator><dc:publisher>IDW Publishing</dc:publisher><dc:identifier>9781684064366</dc:identifier><dc:description>A 72-page hardcover featuring “Open the Moon”.</dc:description></metadata><manifest><item id="cover-image" href="images/cover.jpg" media-type="image/jpeg"/></manifest></package>'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lockeandkey.epub"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("META-INF/container.xml", container)
                archive.writestr("OEBPS/content.opf", package)
                archive.writestr("OEBPS/images/cover.jpg", b"fake-jpeg")
            embedded = read_embedded_metadata(path)
            self.assertEqual(embedded["title"], "Locke and Key: Heaven and Earth")
            self.assertEqual(embedded["isbns"], ["9781684064366"])
            self.assertEqual(embedded["cover_member"], "OEBPS/images/cover.jpg")
            self.assertEqual(embedded["page_count"], 72)
            self.assertEqual(embedded["format"], "hardcover")
            self.assertEqual(embedded["named_contents"], ["Open the Moon"])
            lookup = lookup_identity(parse_filename(path), embedded)
            self.assertEqual(lookup.title, "Locke and Key: Heaven and Earth")
            self.assertEqual(lookup.isbn, "9781684064366")

    def test_archive_cover_prefers_named_cover_then_first_natural_page(self):
        with tempfile.TemporaryDirectory() as folder:
            named = Path(folder) / "named.cbz"
            with zipfile.ZipFile(named, "w") as archive:
                archive.writestr("10.jpg", b"ten")
                archive.writestr("2.jpg", b"two")
                archive.writestr("Front Cover.jpg", b"cover")
                archive.writestr("Back Cover.jpg", b"back")
            self.assertEqual(find_archive_cover_member(named), "Front Cover.jpg")

            pages = Path(folder) / "pages.cbz"
            with zipfile.ZipFile(pages, "w") as archive:
                archive.writestr("10.jpg", b"ten")
                archive.writestr("2.jpg", b"two")
            self.assertEqual(find_archive_cover_member(pages), "2.jpg")
            self.assertEqual(file_cover_info(pages)["source"], "comic file")

    def test_archive_pages_are_image_members_in_reading_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pages.cbz"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("10.jpg", b"ten")
                archive.writestr("2.jpg", b"two")
                archive.writestr("ComicInfo.xml", "<ComicInfo/>")
                archive.writestr("__MACOSX/._2.jpg", b"fork")
                archive.writestr(".hidden.jpg", b"hidden")
            self.assertEqual(archive_page_members(path), ["2.jpg", "10.jpg"])

    def test_a_page_is_rendered_at_the_size_asked_for_and_no_other(self):
        """Reading wants a bigger page than a backdrop, and an unknown size is
        refused: each size is its own set of cached files, so an open list of
        sizes is an open-ended cache."""
        import io
        from PIL import Image
        from app import PAGE_SIZES, READING_PAGE_MAX_DIMENSION, render_file_page

        def png(width, height):
            buffer = io.BytesIO()
            Image.new("RGB", (width, height), "blue").save(buffer, format="PNG")
            return buffer.getvalue()

        self.assertEqual(PAGE_SIZES["read"], READING_PAGE_MAX_DIMENSION)
        self.assertGreater(PAGE_SIZES["read"], PAGE_SIZES["backdrop"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "read.cbz"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("01.jpg", png(2400, 3600))
            with patch("app.catalog_store") as store:
                store.return_value.library_file_path.return_value = path
                with patch("app.reading_cache_dir", return_value=Path(folder) / "cache"):
                    with Image.open(io.BytesIO(render_file_page(1, 0, "read"))) as page:
                        self.assertEqual(max(page.size), READING_PAGE_MAX_DIMENSION)
                    with Image.open(io.BytesIO(render_file_page(1, 0, "backdrop"))) as page:
                        self.assertEqual(max(page.size), PAGE_SIZES["backdrop"])
                    with Image.open(io.BytesIO(render_file_page(1, 0))) as page:
                        self.assertEqual(max(page.size), PAGE_SIZES[""])
                    with self.assertRaises(ValueError):
                        render_file_page(1, 0, "enormous")
                    with self.assertRaises(LookupError):
                        render_file_page(1, 9, "read")

    def test_reading_pages_are_cached_beside_the_catalog_under_a_budget(self):
        """The container's /tmp is a 256MB tmpfs shared with SQLite and the
        cover cache, and one issue at reading size is ~10MB -- so reading pages
        live beside the catalog and the oldest are dropped."""
        import io
        from PIL import Image
        from app import render_file_page, sweep_image_cache

        def png(width, height):
            buffer = io.BytesIO()
            Image.new("RGB", (width, height), "green").save(buffer, format="PNG")
            return buffer.getvalue()

        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "reading-cache"
            path = Path(folder) / "read.cbz"
            with zipfile.ZipFile(path, "w") as archive:
                for index in range(3):
                    archive.writestr(f"{index:02d}.jpg", png(1200, 1800))
            with patch("app.catalog_store") as store:
                store.return_value.library_file_path.return_value = path
                with patch("app.reading_cache_dir", return_value=cache):
                    for index in range(3):
                        render_file_page(1, index, "read")
            rendered = sorted(cache.glob("*.jpg"))
            self.assertEqual(len(rendered), 3, "every page read is kept")

            # Oldest first, and a sweep leaves the cache under its budget.
            for age, cached in enumerate(rendered):
                os.utime(cached, (1_700_000_000 + age, 1_700_000_000 + age))
            total = sum(item.stat().st_size for item in rendered)
            removed = sweep_image_cache(cache, total - 1, interval=0)
            self.assertGreater(removed, 0)
            left = sorted(item.name for item in cache.glob("*.jpg"))
            self.assertLessEqual(sum(item.stat().st_size for item in cache.glob("*.jpg")), total * 0.8)
            self.assertEqual(left[-1], rendered[-1].name, "the newest page survives")
            # A cache already inside its budget is left alone, and so is a
            # directory that is not there at all.
            self.assertEqual(sweep_image_cache(cache, 10 ** 9, interval=0), 0)
            self.assertEqual(sweep_image_cache(Path(folder) / "missing", 1, interval=0), 0)

    def test_a_pages_listing_is_remembered_until_the_file_changes(self):
        """Listing a CBR is a process that scans the whole archive, and a
        reader asks page after page."""
        import app as app_module
        from app import cached_page_members

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pages.cbz"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("1.jpg", b"one")
            app_module._PAGE_MEMBER_CACHE.clear()
            calls = []
            real = app_module.archive_page_members

            def counted(target):
                calls.append(target)
                return real(target)

            with patch("app.archive_page_members", side_effect=counted):
                self.assertEqual(cached_page_members(path), ["1.jpg"])
                self.assertEqual(cached_page_members(path), ["1.jpg"])
                self.assertEqual(len(calls), 1, "the second read is remembered")
                # A replaced file has a new signature, so the answer is looked
                # up again rather than resumed from a stale listing.
                time.sleep(0.01)
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("1.jpg", b"one")
                    archive.writestr("2.jpg", b"two")
                self.assertEqual(cached_page_members(path), ["1.jpg", "2.jpg"])
                self.assertEqual(len(calls), 2)

    def test_a_finished_comic_offers_the_next_one_in_the_run(self):
        """Offering the issue just finished is what makes a continue shelf
        look like it is not paying attention."""
        from app import next_unread_file

        run = [{"id": "1", "filename": "#1"}, {"id": "2", "filename": "#2"},
               {"id": "3", "filename": "#3"}, {"id": "4", "filename": "#4"}]
        self.assertEqual(next_unread_file(run, "1", {})["id"], "2")
        # One already started is offered on its own account, not here.
        started = {"2": {"fileId": "2", "page": 4, "finishedAt": None}}
        self.assertEqual(next_unread_file(run, "1", started)["id"], "3")
        self.assertIsNone(next_unread_file(run, "4", {}), "the end of a run offers nothing")
        self.assertIsNone(next_unread_file(run, "99", {}), "a comic no longer in the run")
        self.assertIsNone(next_unread_file([], "1", {}))

    def test_read_opens_the_first_issue_of_a_run_nobody_has_opened(self):
        from app import reading_target

        run = [{"id": "1", "issueNumber": "1"}, {"id": "2", "issueNumber": "2"}]
        target = reading_target(run, [], {})
        self.assertEqual((target["state"], target["fileId"]), ("unstarted", "1"))
        self.assertEqual(target["page"], 0)

    def test_a_part_read_comic_is_what_read_resumes(self):
        from app import reading_target

        run = [{"id": "1", "issueNumber": "1"}, {"id": "2", "issueNumber": "2"}, {"id": "3", "issueNumber": "3"}]
        progress = {
            "1": {"page": 3, "pageCount": 24, "updatedAt": "2026-09-01T00:00:00+00:00", "finishedAt": None},
            "3": {"page": 9, "pageCount": 24, "updatedAt": "2026-09-19T00:00:00+00:00", "finishedAt": None},
        }
        target = reading_target(run, [], progress)
        self.assertEqual(target["state"], "continue")
        self.assertEqual(target["fileId"], "3", "the one read most recently")
        self.assertEqual((target["page"], target["pageCount"]), (9, 24))

    def test_a_run_read_to_the_end_offers_it_again_from_its_first_issue(self):
        """The button says "Restart Series", so it restarts the series. Mihon's
        one button jumps to the first *unread* instead, which is why
        re-reading a finished run there is a standing complaint."""
        from app import reading_target

        run = [{"id": "1", "issueNumber": "1"}, {"id": "2", "issueNumber": "2"}]
        done = lambda when: {"page": 23, "pageCount": 24, "updatedAt": when, "finishedAt": when}
        target = reading_target(run, [], {
            "1": done("2026-09-01T00:00:00+00:00"), "2": done("2026-09-19T00:00:00+00:00")})
        self.assertEqual(target["state"], "finished")
        self.assertEqual(target["fileId"], "1", "the first issue, not the one read last")
        self.assertEqual(target["page"], 0)

    def test_a_place_kept_against_a_replaced_file_is_not_a_place(self):
        from app import reading_target

        run = [{"id": "1", "issueNumber": "1"}, {"id": "2", "issueNumber": "2"}]
        target = reading_target(run, [], {
            "1": {"page": 11, "pageCount": 24, "updatedAt": "2026-09-19T00:00:00+00:00",
                  "finishedAt": None, "stale": True}})
        self.assertEqual(target["state"], "unstarted", "it starts again rather than resuming a lost page")
        self.assertEqual(target["page"], 0)

    def test_a_run_owned_only_as_a_collection_offers_the_collection(self):
        """There is no record of where an issue starts inside an omnibus, so
        the offer is the volume, and it is named as one."""
        from app import reading_target

        target = reading_target([], [{"id": "9", "issueNumber": None, "volumeLabel": "Vol. 2"}], {})
        self.assertEqual(target["state"], "volume-only")
        self.assertEqual((target["fileId"], target["volumeLabel"]), ("9", "Vol. 2"))

    def test_nothing_readable_offers_nothing(self):
        """Not a disabled button: that would claim the library holds something
        it does not."""
        from app import reading_target

        self.assertEqual(reading_target([], [], {})["state"], "none")
        self.assertIsNone(reading_target([], [], {})["fileId"])
        unreadable = [{"id": "1", "issueNumber": "1", "readable": False}]
        self.assertEqual(reading_target(unreadable, [], {})["state"], "none")

    def test_an_unreadable_file_is_skipped_rather_than_offered(self):
        from app import reading_target

        run = [{"id": "1", "issueNumber": "1", "readable": False}, {"id": "2", "issueNumber": "2"}]
        self.assertEqual(reading_target(run, [], {})["fileId"], "2")

    def test_the_grid_is_told_the_part_read_comic_before_the_finished_one(self):
        """A card should say "Continue #2" while #1 sits finished beside it."""
        from app import reading_by_run

        class Store:
            def issue_file_counts_by_run(self):
                return {"9": 2, "12": 1}

            def reading_progress_by_run(self, *, user_id):
                return {"9": {
                    "1": {"page": 23, "pageCount": 24, "issueNumber": "1", "stale": False,
                          "finishedAt": "2026-09-19T00:00:00+00:00", "updatedAt": "2026-09-19T00:00:00+00:00"},
                    "2": {"page": 5, "pageCount": 24, "issueNumber": "2", "stale": False,
                          "finishedAt": None, "updatedAt": "2026-09-18T00:00:00+00:00"},
                }, "12": {
                    "7": {"page": 3, "pageCount": 24, "issueNumber": "7", "stale": True,
                          "finishedAt": None, "updatedAt": "2026-09-19T00:00:00+00:00"},
                }}

        with patch("app.catalog_store", return_value=Store()):
            runs = reading_by_run(user_id=1)["runs"]
        self.assertEqual(runs["9"]["fileId"], "2", "even though #1 was read more recently")
        self.assertEqual(runs["9"]["state"], "continue")
        self.assertEqual((runs["9"]["page"], runs["9"]["pageCount"]), (5, 24))
        self.assertNotIn("12", runs, "a run whose only record is against a replaced file")

    def _finished_first_issue(self, owned):
        from app import reading_by_run

        class Store:
            def issue_file_counts_by_run(self):
                return {"9": owned}

            def reading_progress_by_run(self, *, user_id):
                return {"9": {"1": {"page": 23, "pageCount": 24, "issueNumber": "1", "stale": False,
                                    "finishedAt": "2026-09-19T00:00:00+00:00",
                                    "updatedAt": "2026-09-19T00:00:00+00:00"}}}

        with patch("app.catalog_store", return_value=Store()):
            return reading_by_run(user_id=1)["runs"]["9"]

    def test_a_run_read_to_the_end_reports_itself_finished_to_the_grid(self):
        run = self._finished_first_issue(owned=1)
        self.assertEqual(run["state"], "finished", "so the cover offers Restart, not Continue")
        self.assertNotIn("finished", run, "the state says it; a second flag would be a second rule")

    def test_finishing_one_issue_of_many_is_between_issues_not_finished(self):
        """The cover of a run with one issue read out of twenty-five used to
        offer Restart. It is the drawer's "next" state: begin the next one."""
        run = self._finished_first_issue(owned=25)
        self.assertEqual(run["state"], "next")

    def test_the_grid_is_told_when_a_run_was_last_read(self):
        """Recent sorts on it, so it is the newest record's time, whichever comic."""
        from app import reading_by_run

        class Store:
            def issue_file_counts_by_run(self):
                return {"9": 3}

            def reading_progress_by_run(self, *, user_id):
                return {"9": {
                    "1": {"page": 23, "pageCount": 24, "issueNumber": "1", "stale": False,
                          "finishedAt": "2026-09-19T00:00:00+00:00", "updatedAt": "2026-09-20T10:00:00+00:00"},
                    "2": {"page": 5, "pageCount": 24, "issueNumber": "2", "stale": False,
                          "finishedAt": None, "updatedAt": "2026-09-18T00:00:00+00:00"},
                }}

        with patch("app.catalog_store", return_value=Store()):
            run = reading_by_run(user_id=1)["runs"]["9"]
        self.assertEqual(run["lastReadAt"], "2026-09-20T10:00:00+00:00")
        self.assertEqual(run["fileId"], "2", "but the place offered is still the part-read comic")

    def test_finishing_an_issue_never_offers_the_omnibus_as_the_next_one(self):
        """The backdrop picker's list sorts volumes last, so walking it for
        "the next issue" hands back a collected edition once the singles run
        out. Reading asks for the issues on their own."""
        from app import continue_reading

        class Store:
            def recent_reading(self, limit, *, user_id):
                return [{
                    "fileId": "1", "filename": "#1", "issueNumber": "1",
                    "seriesRunId": "9", "seriesTitle": "Example", "medium": "comic",
                    "page": 23, "pageCount": 24,
                    "finishedAt": "2026-09-20T00:00:00+00:00", "updatedAt": "2026-09-20T00:00:00+00:00",
                }]

            def run_reading_files(self, run_id):
                return {
                    "issues": [{"id": "1", "filename": "#1", "issueNumber": "1"},
                               {"id": "2", "filename": "#2", "issueNumber": "2"}],
                    "volumes": [{"id": "9", "filename": "Omnibus.cbz", "issueNumber": None}],
                }

        with patch("app.catalog_store", return_value=Store()):
            items = continue_reading(user_id=1)["items"]
        self.assertEqual([item["fileId"] for item in items], ["2"])
        self.assertEqual(items[0]["resume"], "next")
        self.assertEqual(items[0]["issueNumber"], "2", "and it can say which issue it is")

    def test_automatic_backdrop_prefers_the_first_spread_after_the_cover(self):
        import io
        from PIL import Image

        def png(width, height):
            buffer = io.BytesIO()
            Image.new("RGB", (width, height), "red").save(buffer, format="PNG")
            return buffer.getvalue()

        with tempfile.TemporaryDirectory() as folder:
            spread = Path(folder) / "spread.cbz"
            with zipfile.ZipFile(spread, "w") as archive:
                # A wraparound cover is wide too, but it is the cover.
                archive.writestr("00.png", png(120, 80))
                for page in range(1, 6):
                    archive.writestr(f"{page:02d}.png", png(60, 90))
                archive.writestr("06.png", png(180, 120))
            self.assertEqual(automatic_backdrop_page(spread), "06.png")

            portrait = Path(folder) / "portrait.cbz"
            with zipfile.ZipFile(portrait, "w") as archive:
                for page in range(12):
                    archive.writestr(f"{page:02d}.png", png(60, 90))
            # No spread: a page a third of the way in.
            self.assertEqual(automatic_backdrop_page(portrait), "05.png")

            # A scan group's credit image sorts last and is often wide; it is
            # not the comic, so it is never the background.
            credited = Path(folder) / "credited.cbz"
            with zipfile.ZipFile(credited, "w") as archive:
                for page in range(12):
                    archive.writestr(f"American Vampire 001-{page:03d}.jpg", png(60, 90))
                archive.writestr("zKizz.jpg", png(160, 120))
                archive.writestr("z_GD-Pmack.jpg", png(160, 120))
            self.assertEqual(automatic_backdrop_page(credited), "American Vampire 001-005.jpg")

    def test_flags_empty_corrupt_and_pageless_cbz_archives(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder) / "empty.cbz"
            with zipfile.ZipFile(empty, "w"):
                pass
            self.assertEqual(inspect_file_health(empty)["code"], "empty_archive")

            corrupt = Path(folder) / "corrupt.cbz"
            corrupt.write_bytes(b"not a zip archive")
            self.assertEqual(inspect_file_health(corrupt)["code"], "corrupt_archive")

            pageless = Path(folder) / "pageless.cbz"
            with zipfile.ZipFile(pageless, "w") as archive:
                archive.writestr("ComicInfo.xml", "<ComicInfo/>")
            self.assertEqual(inspect_file_health(pageless)["code"], "no_image_pages")

    def test_a_comic_archive_is_judged_by_its_contents_not_its_name(self):
        with tempfile.TemporaryDirectory() as folder:
            # The Adventure Zone 05 came from Usenet twice as a zip named .cbr.
            zipped = Path(folder) / "The Adventure Zone 05 - The Eleventh Hour.cbr"
            with zipfile.ZipFile(zipped, "w") as archive:
                archive.writestr("001.jpg", b"page")
            self.assertEqual(inspect_file_health(zipped)["code"], "readable")

            pageless = Path(folder) / "pageless.cbr"
            with zipfile.ZipFile(pageless, "w") as archive:
                archive.writestr("ComicInfo.xml", "<ComicInfo/>")
            self.assertEqual(inspect_file_health(pageless)["code"], "no_image_pages")

            rar_named_cbz = Path(folder) / "rar.cbz"
            rar_named_cbz.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 64)
            self.assertEqual(inspect_file_health(rar_named_cbz)["code"], "readable")

            neither = Path(folder) / "neither.cbr"
            neither.write_bytes(b"not an archive at all")
            health = inspect_file_health(neither)
            self.assertEqual(health["code"], "corrupt_archive")
            self.assertEqual(health["message"], "CBR does not have a valid RAR archive header.")

    def test_comicinfo_year_does_not_override_collection_filename(self):
        parsed = parse_filename(Path("southernbastards_vol2.cbz"))
        lookup = lookup_identity(parsed, {"source": "ComicInfo.xml", "series": "Southern Bastards", "year": "2026"})
        self.assertEqual(lookup.title, "Southern Bastards")
        self.assertIsNone(lookup.year)

    def test_exact_isbn_dominates_candidate_score(self):
        parsed = parse_filename(Path("Book 978-1-681-23456-7.epub"))
        parsed.isbn = "9781684064366"
        score, reasons = score_candidate(parsed, {"title": "Different", "isbns": ["9781684064366"]}, 4)
        self.assertGreaterEqual(score, 100)
        self.assertIn("exact ISBN", reasons)

    def test_unrelated_first_result_is_rejected(self):
        parsed = parse_filename(Path("southernbastards_vol2.cbz"))
        parsed.title = "Southern Bastards"
        score, reasons = score_candidate(parsed, {"title": "SelectEditions--Volume 3 2000", "isbns": []}, 0)
        self.assertLess(score, 0)
        self.assertTrue(any(reason.startswith("title mismatch") for reason in reasons))

    def test_extracts_issue_range_from_edition_notes(self):
        claims = extract_issue_coverage(
            "Originally published in single magazine form as Southern bastards #5-8. Rated M."
        )
        self.assertEqual(claims[0]["series"], "Southern bastards")
        self.assertEqual(claims[0]["issues"], ["5", "6", "7", "8"])

    def test_embedded_epub_coverage_has_file_provenance(self):
        lookup = parse_filename(Path("/tmp/example.epub"))
        candidate = embedded_epub_candidate(lookup, {
            "source": "EPUB package metadata", "title": "Example Book One",
            "description": "Collects Example #1-4.", "isbns": [],
        })
        claim = candidate["matched_edition"]["coverage"][0]
        self.assertEqual(claim["source"], "EPUB package description")
        self.assertEqual(claim["confidence"], "file confirmed")

    def test_generic_issue_word_does_not_become_a_series_name(self):
        claims = extract_issue_coverage("Collects issues #1-4.")
        self.assertEqual(claims[0]["series"], "")
        self.assertEqual(claims[0]["issues"], ["1", "2", "3", "4"])

    def test_batch_page_renders_coverage(self):
        recommendation = {
            "title": "Southern Bastards",
            "subtitle": "Gridiron",
            "publisher": "Image Comics",
            "publication_year": 2015,
            "isbns": ["9781632152695"],
            "url": "https://openlibrary.org/works/example",
            "cover": None,
            "match_score": 120,
            "match_reasons": ["exact normalized title"],
            "matched_edition": {
                "isbn_13": ["9781632152695"],
                "number_of_pages": 128,
                "coverage": extract_issue_coverage("Collects Southern Bastards #5-8."),
            },
        }
        rendered = render_batch_results([{
            "parsed": {"path": "/tmp/book.cbz", "filename": "book.cbz", "title": "book"},
            "embedded_metadata": {}, "lookup_identity": {"title": "Southern Bastards"},
            "candidates": {"open_library": [recommendation]}, "recommendation": recommendation,
            "gcd_search_url": "https://www.comics.org/searchNew/?q=test",
        }])
        self.assertIn("Southern Bastards: Gridiron", rendered)
        self.assertIn("#5–8", rendered)
        self.assertIn("4 issues", rendered)

    def test_batch_page_prefers_file_cover_over_catalog_cover(self):
        recommendation = {
            "title": "Absolute Batman", "issue": "1", "record_type": "single_issue",
            "cover": "https://catalog.example/cover.jpg", "match_score": 100,
            "match_reasons": [], "matched_edition": {},
        }
        rendered = render_batch_results([{
            "parsed": {"path": "/tmp/issue.cbz", "filename": "issue.cbz", "title": "Absolute Batman"},
            "embedded_metadata": {}, "lookup_identity": {"title": "Absolute Batman"},
            "file_cover": {"source": "comic file", "url": "/api/file-cover?path=%2Ftmp%2Fissue.cbz"},
            "candidates": {}, "recommendation": recommendation,
            "gcd_search_url": "https://www.comics.org/searchNew/?q=test",
        }])
        self.assertIn("/api/file-cover?path=%2Ftmp%2Fissue.cbz", rendered)
        self.assertNotIn("https://catalog.example/cover.jpg", rendered)
        self.assertIn("Cover from comic file", rendered)

    def test_batch_page_prominently_flags_an_empty_archive(self):
        rendered = render_batch_results([{
            "parsed": {"path": "/tmp/empty.cbz", "filename": "empty.cbz", "title": "Empty"},
            "file_health": {
                "status": "error", "code": "empty_archive",
                "message": "Archive is empty (22 bytes) and contains no files or comic pages.",
            },
            "embedded_metadata": {}, "lookup_identity": {"title": "Empty"},
            "candidates": {}, "recommendation": None,
            "gcd_search_url": "https://www.comics.org/searchNew/?q=empty",
        }])
        self.assertIn("Empty Archive", rendered)
        self.assertIn("File problem:", rendered)
        self.assertIn("1 file</strong> failed structural checks", rendered)

    def test_open_library_falls_back_from_isbn_to_title(self):
        parsed = parse_filename(Path("lockeandkey.epub"))
        parsed.title = "Locke and Key: Heaven and Earth"
        parsed.isbn = "9781684064366"
        with patch("app.fetch_json", side_effect=[{"docs": []}, {"docs": [{"key": "/works/OL1W", "title": parsed.title}]}]) as fetch:
            results = search_open_library(parsed)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["search_query"], 'title:"Locke and Key: Heaven and Earth"')
        self.assertEqual(fetch.call_count, 2)

    def test_embedded_epub_can_be_an_unverified_fallback(self):
        parsed = parse_filename(Path("lockeandkey.epub"))
        embedded = {
            "source": "EPUB package metadata", "title": "Locke and Key: Heaven and Earth",
            "creators": ["Joe Hill"], "publisher": "IDW Publishing",
            "isbns": ["9781684064366"], "description": "Three collected stories.",
        }
        candidate = embedded_epub_candidate(parsed, embedded)
        self.assertEqual(candidate["source"], "EPUB package metadata")
        self.assertIn("not confirmed", candidate["verification_status"])
        self.assertGreaterEqual(candidate["match_score"], 70)
        self.assertIn("external collection source", candidate["coverage_status"])

    def test_google_books_is_disabled_without_api_key(self):
        parsed = parse_filename(Path("book.epub"))
        with patch("app.GOOGLE_BOOKS_API_KEY", ""), patch("app.fetch_json") as fetch:
            self.assertEqual(search_google_books(parsed), [])
            fetch.assert_not_called()

    def test_gcd_matches_collected_volume_and_labels_inferred_coverage(self):
        parsed = parse_filename(Path("lockeandkey_vol2_headgames.epub"))
        parsed.title = "Locke and Key Vol. 2: Head Games"
        series_response = {
            "results": [
                {
                    "name": "Locke & Key", "country": "us", "language": "en",
                    "publishing_format": "limited series", "binding": "saddle-stitched",
                    "active_issues": [f"https://www.comics.org/api/issue/{number}/" for number in range(1, 7)],
                    "issue_descriptors": [str(number) for number in range(1, 7)],
                },
                {
                    "name": "Locke & Key", "country": "us", "language": "en",
                    "publishing_format": "collected edition", "binding": "trade paperback",
                    "active_issues": ["https://www.comics.org/api/issue/100/", "https://www.comics.org/api/issue/200/"],
                    "issue_descriptors": ["1 - Welcome to Lovecraft", "2 - Head Games"],
                },
                {
                    "name": "Locke & Key: Head Games", "country": "us", "language": "en",
                    "publishing_format": "limited series", "binding": "saddle-stitched",
                    "active_issues": [f"https://www.comics.org/api/issue/{300 + number}/" for number in range(1, 7)],
                    "issue_descriptors": [str(number) for number in range(1, 7)],
                },
            ]
        }
        issue_response = {
            "api_url": "https://www.comics.org/api/issue/200/",
            "series_name": "Locke & Key (2008 series)", "descriptor": "2 - Head Games",
            "number": "2", "volume": "2", "title": "Head Games",
            "key_date": "2010-10-06", "page_count": "164.000",
            "indicia_publisher": "IDW Publishing", "isbn": "978-1-60010-761-0",
            "cover": "https://files1.comics.org/cover.jpg",
            "story_set": [
                {"type": "comic story", "title": f"Chapter {number}", "script": "Joe Hill", "pencils": "Gabriel Rodriguez"}
                for number in range(1, 7)
            ],
        }

        def response(url):
            return issue_response if url.endswith("/issue/200/") else series_response

        with patch("app.fetch_gcd_json", side_effect=response):
            candidates = search_gcd(parsed)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["subtitle"], "Head Games")
        self.assertEqual(candidate["cover"], "https://files1.comics.org/cover.jpg")
        self.assertEqual(candidate["matched_edition"]["number_of_pages"], 164)
        coverage = candidate["matched_edition"]["coverage"][0]
        self.assertEqual(coverage["issues"], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(coverage["confidence"], "inferred")
        self.assertIn("not an explicit GCD", candidate["coverage_status"])

    def test_gcd_single_issue_path_rejects_collected_editions(self):
        parsed = parse_filename(Path("Absolute%20Batman%20001%20(2024)%20(c2c).cbz"))
        series_response = {
            "results": [
                {
                    "name": "Absolute Batman", "country": "us", "language": "en",
                    "publishing_format": "ongoing series", "binding": "saddle-stitched",
                    "active_issues": ["https://www.comics.org/api/issue/501/"],
                    "issue_descriptors": ["1"],
                },
                {
                    "name": "Absolute Batman", "country": "us", "language": "en",
                    "publishing_format": "collected edition", "binding": "hardcover",
                    "active_issues": ["https://www.comics.org/api/issue/999/"],
                    "issue_descriptors": ["1"],
                },
            ]
        }
        issue_response = {
            "api_url": "https://www.comics.org/api/issue/501/", "series_name": "Absolute Batman",
            "descriptor": "1", "number": "1", "title": "The Zoo",
            "key_date": "2024-10-00", "indicia_publisher": "DC Comics",
            "story_set": [
                {"type": "cover", "pencils": "Variant Artist (credited)"},
                {"type": "promo", "script": "? (promo copy)", "pencils": "various"},
                {
                    "type": "comic story", "title": "The Zoo, Part One of Five",
                    "script": "Scott Snyder (credited)",
                    "pencils": "Nick Dragotta (credited) (signed as DRAGoTTA .)",
                    "inks": "Nick Dragotta (credited)", "colors": "Frank Martin (credited)",
                    "letters": "Clayton Cowles (credited)",
                },
            ], "cover": "https://files1.comics.org/absolute-batman-1.jpg",
        }

        def response(url):
            return issue_response if url.endswith("/issue/501/") else series_response

        with patch("app.fetch_gcd_json", side_effect=response):
            candidates = search_gcd(parsed)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_id"], "501")
        self.assertEqual(candidates[0]["record_type"], "single_issue")
        self.assertEqual(candidates[0]["issue"], "1")
        self.assertIsNone(candidates[0]["volume"])
        self.assertEqual(
            candidates[0]["creators"],
            ["Scott Snyder", "Nick Dragotta", "Frank Martin", "Clayton Cowles"],
        )
        self.assertEqual(candidates[0]["cover_contributors"], ["Variant Artist"])
        self.assertEqual(candidates[0]["coverage_status"], "single issue; volume coverage does not apply")
        self.assertIn("single issue matched", candidates[0]["verification_status"])

    def test_single_issue_confidence_explains_cross_source_year_difference(self):
        parsed = parse_filename(Path("Absolute%20Batman%20003%20(2025).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman", "number": "3",
            "year": "2024", "publisher": "DC Comics", "title": "The Zoo, Part Three",
        }
        candidate = {
            "record_type": "single_issue", "title": "Absolute Batman", "issue": "3",
            "publication_year": 2024, "publication_date": "2024-12-18", "publisher": "DC Comics",
            "descriptor": "3 [Nick Dragotta Cover]", "named_contents": ["The Zoo, Part Three of Five"],
        }
        confidence = assess_identity_confidence(parsed, embedded, candidate)
        self.assertEqual(confidence["level"], "high")
        self.assertGreaterEqual(confidence["score"], 90)
        self.assertTrue(any("filename says 2025" in conflict for conflict in confidence["conflicts"]))
        self.assertTrue(any("ComicInfo.xml year agrees" in item for item in confidence["corroborated_fields"]))

    def test_comicinfo_keeps_single_issue_match_when_gcd_is_unavailable(self):
        parsed = parse_filename(Path("Absolute%20Batman%20003%20(2025).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman", "title": "The Zoo, Part Three",
            "number": "3", "year": "2024", "publisher": "DC Comics",
            "web": "https://metron.cloud/issue/132445/", "metron_id": "8477",
            "contributors": {
                "writer": "Scott Snyder", "penciller": "Nick Dragotta",
                "colorist": "Frank Martin", "letterer": "Clayton Cowles",
            },
        }
        with patch("app.read_embedded_metadata", return_value=embedded), patch("app.search_gcd", return_value=[]):
            result = enrich(parsed)
        recommendation = result["recommendation"]
        self.assertEqual(recommendation["source"], "ComicInfo.xml")
        self.assertEqual(recommendation["record_type"], "single_issue")
        self.assertEqual(recommendation["issue"], "3")
        self.assertEqual(recommendation["identity_confidence"]["level"], "medium")
        self.assertIn("not confirmed by an external catalog", recommendation["verification_status"])

    def test_comicinfo_fallback_suppresses_a_redundant_issue_title(self):
        parsed = parse_filename(Path("Absolute%20Batman%20001%20(2024).cbz"))
        embedded = {
            "source": "ComicInfo.xml", "series": "Absolute Batman",
            "title": "Absolute Batman (2024) #1", "number": "1", "year": "2024",
            "publisher": "DC Comics", "contributors": {},
        }
        with patch("app.read_embedded_metadata", return_value=embedded), patch("app.search_gcd", return_value=[]):
            result = enrich(parsed)
        self.assertIsNone(result["recommendation"]["subtitle"])
        self.assertEqual(result["recommendation"]["named_contents"], [])



class ArtSwatchTests(unittest.TestCase):
    def setUp(self):
        app._ART_SWATCH_CACHE.clear()

    def test_a_redirect_is_not_followed(self):
        # The opener reports redirects rather than following them, so an
        # allowed host cannot send the fetch somewhere else.
        redirect = urllib.error.HTTPError("https://static.metron.cloud/a.jpg", 302, "Found", {"Location": "http://127.0.0.1/"}, None)
        opener = Mock()
        opener.open.side_effect = redirect
        with patch.object(app.urllib.request, "build_opener", return_value=opener) as build:
            with self.assertRaises(app.ArtSwatchUnavailable):
                app.art_swatch("https://static.metron.cloud/a.jpg")
        self.assertIs(build.call_args.args[0], app._ReportRedirect)

    def test_something_that_is_not_an_image_is_refused(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<html>not a cover</html>"
        opener = Mock()
        opener.open.return_value = response
        with patch.object(app.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(app.ArtSwatchUnavailable):
                app.art_swatch("https://files1.comics.org/a.jpg")

    def test_an_oversized_download_is_refused_before_decoding(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"x" * (app.ART_SWATCH_MAX_BYTES + 1)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(app.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(app.ArtSwatchUnavailable):
                app.art_swatch("https://comicvine.gamespot.com/a.jpg")

if __name__ == "__main__":
    unittest.main()


class RetryChoosesItsOwnReleaseTests(unittest.TestCase):
    """Fixing a request should pick the best available release, not ask for one."""

    def test_retry_grabs_the_strongest_unused_release(self):
        best = {"id": "best-candidate", "title": "Fables.095.repack"}
        with patch("app.search_prowlarr_releases", return_value={
            "candidateCount": 2,
            "candidates": [best, {"id": "second", "title": "Fables.095.other"}],
        }) as search, patch("app.send_release_to_sabnzbd", return_value={
            "status": "grabbed", "queueIds": ["queue-1"],
            "detail": "Release sent to SABnzbd.",
        }) as send:
            result = _auto_grab_release(7)

        search.assert_called_once_with(7)
        # The list is already ranked, so the first entry is the best available.
        send.assert_called_once_with(7, "best-candidate")
        self.assertEqual(result["status"], "grabbed")
        self.assertEqual(result["release"], best)

    def test_retry_defers_to_a_person_when_nothing_scores_well_enough(self):
        with patch("app.search_prowlarr_releases", return_value={
            "candidateCount": 0, "candidates": [],
        }), patch("app.send_release_to_sabnzbd") as send:
            self.assertIsNone(_auto_grab_release(7))
        send.assert_not_called()

    def test_retry_defers_to_a_person_when_the_indexer_cannot_answer(self):
        with patch(
            "app.search_prowlarr_releases", side_effect=RuntimeError("Prowlarr is down")
        ), patch("app.send_release_to_sabnzbd") as send:
            self.assertIsNone(_auto_grab_release(7))
        send.assert_not_called()

    def test_retry_defers_to_a_person_when_the_chosen_release_cannot_be_sent(self):
        """A failed hand-off must not look like a queued download."""
        with patch("app.search_prowlarr_releases", return_value={
            "candidateCount": 1, "candidates": [{"id": "best", "title": "Fables.095"}],
        }), patch(
            "app.send_release_to_sabnzbd", side_effect=ReleaseDownloadError("nzb gone")
        ):
            self.assertIsNone(_auto_grab_release(7))


class ReleaseIssueRangeTests(unittest.TestCase):
    """What a release says it holds, when it holds more than one issue."""

    def test_a_stated_range_is_read(self):
        for title, expected in (
            ("Chew 001-060 (2009-2016) (Digital) (Zone-Empire)", (1, 60)),
            ("Nightwing #1-30 (2011-2014)", (1, 30)),
            ("Saga 001 - 072 (Digital)", (1, 72)),
            ("Chew Complete Series (001-060) (2016) (Digital)", (1, 60)),
        ):
            with self.subTest(title=title):
                self.assertEqual(app._release_issue_range(title), expected)

    def test_the_years_a_run_ran_are_not_issue_numbers(self):
        self.assertIsNone(app._release_issue_range("Chew (2009-2016) (Digital)"))

    def test_a_volume_range_is_not_an_issue_range(self):
        """Editions are not an automatic acquisition target, and v04 is not #4."""
        for title in (
            "Chew v01-v12 Complete (Digital) (Zone-Empire)",
            "Saga Vol 1-9 (TPB) (Digital)",
            "Chew Complete Collection v01-v12 (Digital)",
        ):
            with self.subTest(title=title):
                self.assertIsNone(app._release_issue_range(title, run_issue_count=60))

    def test_a_single_issue_is_not_a_pack(self):
        for title in (
            "Chew 004 (2009) (D) (Kingpin-Empire)",
            "Saga 002 of 004 (2012)",
            "Week of 2022.02.16 [53/72] - Supergirl Woman of Tomorrow 08 (2022)",
        ):
            with self.subTest(title=title):
                self.assertIsNone(app._release_issue_range(title))

    def test_complete_run_wording_uses_the_runs_own_count(self):
        self.assertEqual(
            app._release_issue_range("Chew Complete Series (Digital)", run_issue_count=60), (1, 60),
        )
        # Nothing names the bounds, so it claims nothing.
        self.assertIsNone(app._release_issue_range("Chew Complete Series (Digital)"))
        self.assertIsNone(app._release_issue_range("Chew Full Run", run_issue_count=1))


class PackScoringTests(unittest.TestCase):
    """A pack is offered for an issue, but never ahead of that issue itself."""

    CONTEXT = {
        "seriesTitle": "Chew", "issueNumber": "4",
        "publicationYear": 2009, "runIssueCount": 60,
    }

    def judge(self, title, **overrides):
        return app._release_candidate_score({"title": title}, {**self.CONTEXT, **overrides})

    def test_a_single_issue_outranks_a_pack_that_contains_it(self):
        single, _ = self.judge("Chew 004 (2009) (D) (Kingpin-Empire)")
        pack, reasons = self.judge("Chew 001-060 (2009-2016) (Digital) (Zone-Empire)")
        self.assertGreater(single, pack)
        self.assertGreaterEqual(single, 85, "an exact single is still grabbable on its own")
        self.assertLess(pack, 85, "a pack alone must not reach the automatic grab score")
        self.assertGreater(pack, 0, "but it is a real candidate, not discarded")
        self.assertIn("Issues #1-#60 include #4", reasons)

    def test_a_range_end_is_not_the_issue_it_looks_like(self):
        """"Chew 001-060" is not issue #60, though it ends in the number.

        It scored as an exact single and would have been grabbed as one: a
        whole-run download taken for one issue, with none of the pack rules
        applied because nothing knew it was a pack.
        """
        # #60 shipped in 2016, the year both releases state; a mismatch there is
        # a different refusal (_release_year_conflict) and would mask this one.
        context = {**self.CONTEXT, "issueNumber": "60", "publicationYear": 2016, "runIssueCount": 60}
        pack, reasons = app._release_candidate_score(
            {"title": "Chew 001-060 (2009-2016) (Digital) (Zone-Empire)"}, context,
        )
        single, _ = app._release_candidate_score(
            {"title": "Chew 060 (2016) (Digital) (Zone-Empire)"}, context,
        )
        self.assertIn("Series title matches", reasons, "the series is still read, before the range")
        self.assertIn("Issues #1-#60 include #60", reasons)
        self.assertLess(pack, single)
        self.assertLess(pack, 85, "and it cannot be grabbed as though it were the issue")

    def test_a_pack_that_stops_short_of_the_issue_gets_nothing_for_it(self):
        covered, _ = self.judge("Chew 001-060 (2009-2016) (Digital)")
        missed, reasons = self.judge("Chew 010-060 (2009-2016) (Digital)")
        self.assertGreater(covered, missed)
        self.assertFalse([reason for reason in reasons if "include" in reason])

    def test_a_volume_pack_is_never_offered_for_an_issue(self):
        _score, reasons = self.judge("Chew v01-v12 Complete (Digital) (Zone-Empire)")
        self.assertFalse([reason for reason in reasons if "include" in reason])

    def test_a_pack_in_another_language_is_still_refused(self):
        score, _ = self.judge(
            "Chew 001-060 (2009-2016) (Spanish) (Digital)", preferredLanguage="en",
        )
        self.assertEqual(score, 0)


class PackEligibilityTests(unittest.TestCase):
    """One download instead of sixty, but never forty gigabytes for one issue."""

    CONTEXT = {"requestId": "7", "runIssueCount": 60, "seriesTitle": "Chew"}

    @staticmethod
    def pack(size_bytes=20_000_000_000, first=1, last=60, title="Chew 001-060 (Digital)"):
        return {
            "id": "pack-1", "title": title, "sizeBytes": size_bytes,
            "matchScore": 82, "pack": {"first": first, "last": last},
        }

    @staticmethod
    def single(title="Chew 004 (2009)"):
        return {"id": "single-1", "title": title, "sizeBytes": 40_000_000, "matchScore": 95}

    @staticmethod
    def _store(*numbers):
        store = Mock()
        store.wanted_run_issues.return_value = [
            {"jobId": index, "issueId": index, "issueNumber": str(number), "status": "queued"}
            for index, number in enumerate(numbers, 1)
        ]
        return store

    def order(self, candidates, *wanted):
        with patch("app.catalog_store", return_value=self._store(*wanted)):
            return app._automatic_grab_order(self.CONTEXT, candidates)

    def test_a_single_release_too_large_for_one_issue_is_held_back(self):
        # 5.2 GB for a digital issue: password-protected junk, as it turned out.
        junk = {**self.single("Absolute Batman 024 (2025) (digital-mobile)"), "id": "junk", "sizeBytes": 5_200_000_000}
        grabbable, note = self.order([junk, self.single()], 4)
        self.assertEqual([item["id"] for item in grabbable], ["single-1"], "the plausible one is taken")
        self.assertEqual(note, "")
        grabbable, note = self.order([junk], 4)
        self.assertEqual(grabbable, [])
        self.assertIn("5.2 GB is too large for one issue", note)
        self.assertIn("Find release lists it", note)

    def test_a_pack_is_taken_when_a_run_is_mostly_missing(self):
        grabbable, note = self.order([self.pack()], *range(1, 41))
        self.assertEqual([item["id"] for item in grabbable], ["pack-1"])
        self.assertEqual(note, "")

    def test_a_pack_is_not_taken_to_fill_one_gap(self):
        grabbable, note = self.order([self.pack()], 4)
        self.assertEqual(grabbable, [])
        self.assertIn("did not take it automatically", note)
        self.assertIn("only 1 issue wanted", note)

    def test_a_pack_too_large_for_what_it_covers_is_refused(self):
        grabbable, note = self.order([self.pack(size_bytes=40_000_000_000)], *range(1, 7))
        self.assertEqual(grabbable, [])
        self.assertIn("40.0 GB for 6 issues", note)

    def test_a_pack_that_misses_most_of_what_is_wanted_is_refused(self):
        grabbable, _ = self.order([self.pack(first=1, last=3)], *range(1, 41))
        self.assertEqual(grabbable, [])

    def test_a_single_is_always_tried_before_a_pack(self):
        grabbable, note = self.order([self.single(), self.pack()], *range(1, 41))
        self.assertEqual([item["id"] for item in grabbable], ["single-1", "pack-1"])
        self.assertEqual(note, "", "nothing to explain while a single is available")

    def test_half_a_run_counts_as_mostly_missing(self):
        worth, why = app._pack_worth_taking(
            {"runIssueCount": 8}, self.pack(size_bytes=1_000_000_000, last=8), [1, 2, 3, 4],
        )
        self.assertTrue(worth, why)

    def test_an_unknown_demand_leaves_packs_alone(self):
        with patch("app.catalog_store", side_effect=RuntimeError("database is locked")):
            grabbable, note = app._automatic_grab_order(self.CONTEXT, [self.pack()])
        self.assertEqual(grabbable, [])
        self.assertEqual(note, "")


FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>You searched for Supergirl &#8211; the download site</title>
  <item>
    <title>Supergirl &#8211; Woman of Tomorrow #8 (2022)</title>
    <link>https://comics.example/dc/supergirl-woman-of-tomorrow-8-2022/</link>
    <pubDate>Wed, 16 Feb 2022 11:04:21 +0000</pubDate>
    <description>Year : 2022 | Size : 55 MB | Language: English</description>
  </item>
  <item>
    <title>Supergirl &#8211; Woman of Tomorrow #1 &#8211; 8 (2022)</title>
    <link>https://comics.example/dc/supergirl-woman-of-tomorrow-1-8-2022/</link>
    <pubDate>Wed, 16 Feb 2022 11:05:00 +0000</pubDate>
    <description>Year : 2022 | Size : 1.2 GB</description>
  </item>
  <item>
    <title>Batman &#8211; The Long Halloween #3 (1997)</title>
    <link>https://comics.example/dc/batman-long-halloween-3/</link>
    <description>Year : 1997 | Size : 40 MB</description>
  </item>
</channel></rss>"""


class DirectSiteSearchTests(unittest.TestCase):
    """Finding things on the download site is open; fetching them is not.

    The feed answers a plain request (measured 2026-09-15) while the post pages
    behind it are answered only to a full browser, so results are listed and labelled but
    cannot be grabbed until the page fetcher work lands.
    """

    CONTEXT = {
        "seriesTitle": "Supergirl Woman of Tomorrow", "issueNumber": "8",
        "publicationYear": 2022, "runIssueCount": 8, "requestId": "9",
    }

    def setUp(self):
        # No site is built in: these tests run against one the operator entered.
        site = patch("app.load_acquisition_service_config",
                     return_value={"direct_site": {"enabled": True, "url": "https://comics.example"}})
        site.start()
        self.addCleanup(site.stop)

    def test_no_site_is_searched_until_one_is_entered_and_on(self):
        for saved in ({}, {"direct_site": {"enabled": False, "url": "https://comics.example"}},
                      {"direct_site": {"enabled": True, "url": ""}}):
            with patch("app.load_acquisition_service_config", return_value=saved), \
                 patch("app.fetch_bytes_with_headers", return_value=FEED) as fetch:
                self.assertEqual(app._direct_site_candidates(7, self.CONTEXT, "Supergirl"), [])
            fetch.assert_not_called()

    def test_the_feed_is_read_into_results(self):
        with patch("app.fetch_bytes_with_headers", return_value=FEED) as fetch:
            found = app.direct_site_search("Supergirl")
        url = fetch.call_args.args[0]
        self.assertIn("feed=rss2", url)
        self.assertIn("s=Supergirl", url)
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0]["title"], "Supergirl – Woman of Tomorrow #8 (2022)")
        self.assertEqual(found[0]["sizeBytes"], 55 * 1024 * 1024)
        self.assertEqual(found[1]["sizeBytes"], int(1.2 * 1024 ** 3))
        self.assertTrue(found[0]["url"].startswith("https://comics.example/"))

    def test_a_feed_that_is_not_a_feed_is_refused_clearly(self):
        with patch("app.fetch_bytes_with_headers", return_value=b"<html>Just a moment...</html>"):
            with self.assertRaisesRegex(ValueError, "not a feed"):
                app.direct_site_search("Supergirl")

    def test_results_are_scored_despite_the_en_dash(self):
        """The download site writes "Supergirl – Woman of Tomorrow"; matchers expect a hyphen."""
        with patch("app.fetch_bytes_with_headers", return_value=FEED):
            candidates = app._direct_site_candidates(7, self.CONTEXT, "Supergirl")
        titles = [item["title"] for item in candidates]
        self.assertIn("Supergirl – Woman of Tomorrow #8 (2022)", titles)
        self.assertNotIn("Batman – The Long Halloween #3 (1997)", titles, "another series")
        first = candidates[0]
        self.assertEqual(first["source"], "direct_site")
        self.assertEqual(first["indexer"], "The download site")
        self.assertEqual(first["protocol"], "Direct download")
        self.assertGreaterEqual(first["matchScore"], 85)

    def test_a_result_is_a_sighting_not_an_offer(self):
        with patch("app.fetch_bytes_with_headers", return_value=FEED):
            candidates = app._direct_site_candidates(7, self.CONTEXT, "Supergirl")
        self.assertTrue(all(item["grabbable"] is False for item in candidates))
        self.assertTrue(all("page fetcher" in item["grabHint"] for item in candidates))

    def test_grabbing_one_without_a_solver_says_which_is_missing(self):
        with patch("app.fetch_bytes_with_headers", return_value=FEED), \
             patch("app.load_acquisition_service_config", return_value={"direct_site": {"enabled": True, "url": "https://comics.example"}}):
            candidates = app._direct_site_candidates(7, self.CONTEXT, "Supergirl")
            with self.assertRaisesRegex(ValueError, "Page fetcher"):
                app.grab_release_candidate(7, candidates[0]["id"])

    def test_sabnzbd_is_never_handed_a_direct_download(self):
        """It takes NZBs; a direct download has its own road."""
        with patch("app.fetch_bytes_with_headers", return_value=FEED):
            candidates = app._direct_site_candidates(7, self.CONTEXT, "Supergirl")
        with self.assertRaisesRegex(ValueError, "not a Usenet release"):
            app.send_release_to_sabnzbd(7, candidates[0]["id"])

    def test_a_pack_from_direct_site_is_labelled_as_one(self):
        with patch("app.fetch_bytes_with_headers", return_value=FEED):
            candidates = app._direct_site_candidates(7, self.CONTEXT, "Supergirl")
        packs = [item for item in candidates if item.get("pack")]
        self.assertTrue(packs, "#1 - 8 covers the wanted issue")
        self.assertEqual(packs[0]["pack"], {"first": 1, "last": 8})

    def test_an_older_single_issue_is_found_even_when_newer_posts_bury_it(self):
        # the download site lists only its newest matches. "Farmhand" returned #18-#26
        # and a #1-20 pack; only "Farmhand #3" found the single issue.
        context = {"seriesTitle": "Farmhand", "issueNumber": "003", "publicationYear": 2018,
                   "runIssueCount": 20, "requestId": "57"}
        def feed(url, *_args, **_kwargs):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["s"][0]
            items = {
                "Farmhand #3": [("Farmhand #3 (2018)", "https://comics.example/image/farmhand-3-2018/")],
                "Farmhand": [("Farmhand #26 (2025)", "https://comics.example/image/farmhand-26/"),
                             ("Farmhand #1 – 20 (2018-2022)", "https://comics.example/other-comics/farmhand-1-20/")],
            }[query]
            body = "".join(f"<item><title>{title}</title><link>{link}</link><description>Size : 40 MB</description></item>"
                           for title, link in items)
            return f"<rss><channel>{body}</channel></rss>".encode()
        with patch("app.fetch_bytes_with_headers", side_effect=feed) as fetch:
            candidates = app._direct_site_candidates(57, context, "Farmhand")
        asked = [urllib.parse.parse_qs(urllib.parse.urlsplit(c.args[0]).query)["s"][0] for c in fetch.call_args_list]
        self.assertEqual(asked, ["Farmhand #3", "Farmhand"], "the issue first, then the title for packs")
        titles = [item["title"] for item in candidates]
        self.assertIn("Farmhand #3 (2018)", titles)
        self.assertIn("Farmhand #1 – 20 (2018-2022)", titles, "the pack is still offered")
        self.assertNotIn("Farmhand #26 (2025)", titles)

    def test_a_typed_query_is_asked_as_typed(self):
        self.assertEqual(app._direct_site_queries({"seriesTitle": "Farmhand", "issueNumber": "3"}, "farmhand deluxe"),
                         ["farmhand deluxe"])

    def test_a_direct_site_post_below_the_bar_is_set_aside_not_dropped(self):
        feed = ("<rss><channel>"
                "<item><title>Supergirl &#8211; Woman of Tomorrow #3 (2022)</title>"
                "<link>https://comics.example/dc/swot-3/</link><description>Size : 50 MB</description></item>"
                "<item><title>Batman &#8211; The Long Halloween #3 (1997)</title>"
                "<link>https://comics.example/dc/tlh-3/</link><description>Size : 40 MB</description></item>"
                "</channel></rss>").encode()
        aside = []
        with patch("app.fetch_bytes_with_headers", return_value=feed):
            self.assertEqual(app._direct_site_candidates(7, self.CONTEXT, "Supergirl", set_aside=aside), [])
        self.assertEqual(aside, [], "without a page fetcher nothing could be taken, so nothing is offered")
        with patch("app.fetch_bytes_with_headers", return_value=feed), \
             patch("app._enabled_acquisition_service", return_value={"url": "https://comics.example"}):
            self.assertEqual(app._direct_site_candidates(7, self.CONTEXT, "Supergirl", set_aside=aside), [])
        self.assertEqual([(m["title"], m["reason"], m["source"], m["setAside"]) for m in aside],
                         [("Supergirl – Woman of Tomorrow #3 (2022)", "Not this issue", "direct_site", True)],
                         "the post naming the series is offered; the unrelated one is not")
        self.assertEqual(_RELEASE_CANDIDATES[aside[0]["id"]]["postUrl"], "https://comics.example/dc/swot-3/")
        with patch("app.fetch_bytes_with_headers", return_value=feed), \
             patch("app._enabled_acquisition_service", return_value={"url": "https://comics.example"}), \
             patch("app.search_prowlarr_releases", return_value={
                 "job": self.CONTEXT, "query": "Supergirl", "candidateCount": 0, "candidates": [],
                 "resultCount": 0, "nearMisses": [{"id": "u1", "title": "Other 008", "score": 35, "reasons": [],
                                                  "copies": 1, "setAside": True, "refused": False,
                                                  "reason": "Not this series", "source": "usenet"}]}):
            merged = app.search_release_candidates(7)
        self.assertEqual([m["title"] for m in merged["nearMisses"]],
                         ["Other 008", "Supergirl – Woman of Tomorrow #3 (2022)"], "both lists, best first")

    def test_a_collection_posts_part_stands_in_for_the_post(self):
        # R.E.B.E.L.S. #12 was on the download site only inside "R.E.B.E.L.S. Vol. 1 – 2
        # (Collection) (1994-2011)", whose title names no issue (2026-09-29).
        context = {"seriesTitle": "R.E.B.E.L.S.", "seriesYear": 2009, "issueNumber": "12", "publicationYear": 2010,
                   "publisher": "DC Comics", "runIssueCount": 28, "requestId": "3"}
        feed = ("<rss><channel><item><title>R.E.B.E.L.S. Vol. 1 &#8211; 2 (Collection) (1994-2011)</title>"
                "<link>https://comics.example/dc/r-e-b-e-l-s-vol-1-2-collection/</link><description>Size : 1.5 GB</description></item>"
                "<item><title>Legion of Super-Heroes Vol. 1 &#8211; 7 (Collection)</title>"
                "<link>https://comics.example/dc/legion-collection/</link><description>Size : 9 GB</description></item>"
                "</channel></rss>").encode()
        app._DIRECT_SITE_PARTS.clear()
        self.addCleanup(app._DIRECT_SITE_PARTS.clear)
        with patch("app.fetch_bytes_with_headers", return_value=feed) as fetch, \
             patch("app._enabled_acquisition_service", return_value={"url": "https://comics.example"}), \
             patch("app.solver_fetch_html", return_value=DirectSitePartsTests.COLLECTION) as solver:
            candidates = app._direct_site_candidates(3, context, "R.E.B.E.L.S.")
            again = app._direct_site_candidates(3, context, "R.E.B.E.L.S.")
        asked = [urllib.parse.parse_qs(urllib.parse.urlsplit(c.args[0]).query)["s"][0] for c in fetch.call_args_list[:3]]
        self.assertEqual(asked, ["R.E.B.E.L.S. #12", "R.E.B.E.L.S.", "R.E.B.E.L.S. 2009"], "the dotted name is asked as written")
        self.assertEqual(len(candidates), 1, candidates)
        part = candidates[0]
        self.assertEqual(part["title"], "R.E.B.E.L.S. Vol. 2 #1 – 28 + Annual (2009-2011) (943 MB)", "the 1994 run's part is not offered")
        self.assertEqual(part["postTitle"], "R.E.B.E.L.S. Vol. 1 – 2 (Collection) (1994-2011)")
        self.assertEqual(part["pack"], {"first": 1, "last": 28})
        self.assertEqual(part["matchStrength"], "Pack, #1-#28")
        self.assertEqual(part["sizeBytes"], 943 * 1024 ** 2, "the part's size, not the post's")
        self.assertTrue(part["grabbable"])
        self.assertEqual(solver.call_count, 1, "only the post naming the series is read, and once")
        self.assertEqual([item["title"] for item in again], [part["title"]])
        with patch("app.fetch_bytes_with_headers", return_value=feed), \
             patch("app.solver_fetch_html", return_value=DirectSitePartsTests.COLLECTION) as solver:
            self.assertEqual(app._direct_site_candidates(3, context, "R.E.B.E.L.S."), [],
                             "without a page fetcher the post cannot be read, so it is not offered")
            solver.assert_not_called()

    def test_a_search_that_fails_leaves_usenet_results_alone(self):
        with patch("app.fetch_bytes_with_headers", side_effect=TimeoutError("slow")):
            self.assertEqual(app._direct_site_candidates(7, self.CONTEXT, "Supergirl"), [])

    def test_the_two_sources_are_merged_best_first(self):
        usenet = {
            "job": self.CONTEXT, "query": "Supergirl", "candidateCount": 1,
            "candidates": [{
                "id": "usenet-1", "title": "Supergirl Woman of Tomorrow 008 (2022)",
                "matchScore": 95, "sizeBytes": 45_000_000, "source": "usenet", "grabbable": True,
            }],
            "resultCount": 9, "nearMisses": [],
        }
        with patch("app.search_prowlarr_releases", return_value=usenet), \
             patch("app.fetch_bytes_with_headers", return_value=FEED), \
             patch("app.catalog_store"):
            merged = app.search_release_candidates(7, "Supergirl")
        sources = {item["source"] for item in merged["candidates"]}
        self.assertEqual(sources, {"usenet", "direct_site"}, "both sources are offered")
        scores = [item["matchScore"] for item in merged["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True), "best match first, whatever its source")
        self.assertEqual(merged["candidateCount"], len(merged["candidates"]))

    def test_no_indexer_still_shows_what_direct_site_has(self):
        store = Mock()
        store.get_acquisition_job_context.return_value = dict(self.CONTEXT)
        with patch("app.search_prowlarr_releases", side_effect=ValueError("Connect and enable Prowlarr in Settings first")), \
             patch("app.fetch_bytes_with_headers", return_value=FEED), \
             patch("app.catalog_store", return_value=store):
            merged = app.search_release_candidates(7, "Supergirl")
        self.assertTrue(merged["candidates"])
        self.assertTrue(all(item["source"] == "direct_site" for item in merged["candidates"]))


POST_PAGE = """<html><body>
  <a href="https://comics.example/how-to-download/">How To Download</a>
  <a class="aio-red" href="https://comics.example/dls/eB91xJSotuf6mP6lNsh">Download Now</a>
  <a class="aio-red" href="https://readcomicsonline.ru/comic/supergirl/8">Read Online</a>
</body></html>"""


class SolverTests(unittest.TestCase):
    """Reading the post page needs a browser; fetching the file does not."""

    def setUp(self):
        # Links are taken only from the site the operator entered.
        home = patch("app._direct_site_home", return_value="https://comics.example")
        home.start()
        self.addCleanup(home.stop)

    def test_a_link_off_the_site_is_never_followed(self):
        """A post page cannot send the downloader to another host -- least of
        all one inside the network Flipparr runs on (2026-10-05)."""
        page = ('<a href="https://192.168.1.10/dls/internal">Main Server</a>'
                '<a href="https://evil.example/dls/elsewhere">Mirror</a>'
                '<a href="https://comics.example.evil.example/dls/lookalike">Mirror</a>'
                '<a href="https://files.comics.example/dls/good">Main Server</a>')
        self.assertEqual([link["url"] for link in app.direct_site_download_links(page, "https://www.comics.example")],
                         ["https://files.comics.example/dls/good"])
        self.assertEqual(app.direct_site_download_links(page, ""), [], "no site entered: nothing is taken")
        feed = (b"<rss><channel><item><title>Example #1 (2020)</title><link>https://evil.example/example-1/</link></item>"
                b"<item><title>Example #2 (2020)</title><link>https://comics.example/example-2/</link></item></channel></rss>")
        with patch("app.fetch_bytes_with_headers", return_value=feed):
            found = app.direct_site_search("Example", base_url="https://comics.example")
        self.assertEqual([item["title"] for item in found], ["Example #2 (2020)"])

    def test_the_api_path_is_added_rather_than_failing_silently(self):
        """mylar3 #1815: without /v1 the page fetcher 405s and every grab dies quietly."""
        self.assertEqual(app._solver_endpoint("http://flaresolverr:8191"), "http://flaresolverr:8191/v1")
        self.assertEqual(app._solver_endpoint("http://flaresolverr:8191/v1/"), "http://flaresolverr:8191/v1")
        with self.assertRaises(ValueError):
            app._solver_endpoint("")

    def test_a_405_says_what_to_fix(self):
        error = urllib.error.HTTPError("http://solver/v1", 405, "Method Not Allowed", None, None)
        with patch("app._safe_urlopen", side_effect=error):
            with self.assertRaisesRegex(ValueError, "/v1"):
                app.solver_request({"cmd": "sessions.list"}, url="http://solver")

    def test_a_solver_that_refuses_is_reported_not_swallowed(self):
        answer = io.BytesIO(json.dumps({"status": "error", "message": "Challenge not solved!"}).encode())
        answer.__enter__ = lambda: answer
        with patch("app._safe_urlopen", return_value=contextlib.nullcontext(answer)):
            with self.assertRaisesRegex(ValueError, "Challenge not solved"):
                app.solver_request({"cmd": "request.get"}, url="http://solver/v1")

    def test_the_download_link_is_read_from_the_post(self):
        with patch("app.solver_fetch_html", return_value=POST_PAGE):
            link = app.direct_site_download_link("https://comics.example/dc/supergirl-8/")
        self.assertEqual(link, "https://comics.example/dls/eB91xJSotuf6mP6lNsh")

    def test_a_post_with_no_link_is_refused(self):
        with patch("app.solver_fetch_html", return_value="<html><body>nothing here</body></html>"):
            with self.assertRaisesRegex(ValueError, "no direct download link"):
                app.direct_site_download_link("https://comics.example/dc/supergirl-8/")

    def test_a_challenge_page_is_not_mistaken_for_the_post(self):
        with patch("app.solver_request", return_value={"solution": {"response": "<html><title>Just a moment...</title>"}}):
            with self.assertRaisesRegex(ValueError, "browser check"):
                app.solver_fetch_html("https://comics.example/dc/supergirl-8/")


class DirectSiteDownloadTests(unittest.TestCase):
    """The file itself is ordinary HTTP, so it streams, resumes and reports."""

    @staticmethod
    def _response(body, *, name="Supergirl - Woman of Tomorrow 08 (2022).cbz"):
        response = Mock()
        response.headers = {"Content-Length": str(len(body)), "Content-Disposition": ""}
        response.geturl.return_value = f"https://fs1.comicfiles.ru/2022.02.16/{urllib.parse.quote(name)}"
        chunks = [body, b""]
        response.read.side_effect = lambda *_: chunks.pop(0)
        return contextlib.nullcontext(response)

    def test_the_file_lands_in_staging_and_the_row_completes(self):
        body = b"comic-bytes" * 1000
        store = Mock()
        with tempfile.TemporaryDirectory() as folder:
            staging = Path(folder) / "direct_site"
            with patch("app.catalog_store", return_value=store), \
                 patch("app.acquisition_staging_dir", return_value=staging), \
                 patch("app.direct_site_download_link", return_value="https://comics.example/dls/token"), \
                 patch("app._safe_urlopen", return_value=self._response(body)):
                app._fetch_direct_site_download(5, 11, "https://comics.example/dc/supergirl-8/", "Supergirl #8")
            landed = list(staging.rglob("*.cbz"))
            self.assertEqual(len(landed), 1)
            self.assertEqual(landed[0].name, "Supergirl - Woman of Tomorrow 08 (2022).cbz",
                             "named by the server, which is what the identity check reads")
            self.assertEqual(landed[0].read_bytes(), body)
        final = [c for c in store.update_acquisition_download.call_args_list if c.args[1] == "completed"]
        self.assertEqual(len(final), 1)
        self.assertTrue(str(final[0].kwargs["sab_storage"]).endswith("/11"))

    def test_a_post_taken_by_hand_is_marked_and_its_part_chosen_by_number_alone(self):
        store = Mock()
        store.record_acquisition_download.return_value = {"id": 9}
        far = time.time() + 600
        with patch("app.catalog_store", return_value=store), patch("app._enabled_acquisition_service"), \
             patch("app._start_direct_fetch"), patch.dict(app._RELEASE_CANDIDATES, {
                 "gc": {"jobId": 5, "source": "direct_site", "postUrl": "https://comics.example/x/", "title": "X Vol. 1",
                        "expiresAt": far, "setAside": True, "setAsideReason": "Not this issue"}}):
            with self.assertRaisesRegex(ValueError, "set aside as Not this issue"):
                app.grab_release_candidate(5, "gc")
            result = app.grab_release_candidate(5, "gc", anyway=True)
        self.assertTrue(result["takenByHand"])
        self.assertEqual(store.record_acquisition_download.call_args.kwargs["taken_by_hand"], True)
        store.forget_release_refusal.assert_called_once_with(5, "https://comics.example/x/", "X Vol. 1")
        self.assertEqual(store.update_acquisition_job.call_args.args[1:], ("grabbed", "Taken by hand: X Vol. 1"))
        store = Mock()
        store.get_acquisition_job_context.return_value = {"issueNumber": "12", "seriesYear": 2009, "publicationYear": 2010}
        store.acquisition_download_for_job.return_value = {"id": 9, "taken_by_hand": 1}
        with tempfile.TemporaryDirectory() as folder:
            with patch("app.catalog_store", return_value=store), \
                 patch("app.acquisition_staging_dir", return_value=Path(folder) / "direct_site"), \
                 patch("app.direct_site_download_link", return_value="https://comics.example/dls/token") as choose, \
                 patch("app._safe_urlopen", return_value=self._response(b"comic-bytes" * 1000)):
                app._fetch_direct_site_download(9, 5, "https://comics.example/x/", "X Vol. 1")
        choose.assert_called_once_with("https://comics.example/x/", "12", None)

    def test_the_part_is_chosen_by_the_jobs_issue_and_years(self):
        store = Mock()
        store.get_acquisition_job_context.return_value = {"issueNumber": "12", "seriesYear": 2009, "publicationYear": 2010}
        with tempfile.TemporaryDirectory() as folder:
            with patch("app.catalog_store", return_value=store), \
                 patch("app.acquisition_staging_dir", return_value=Path(folder) / "direct_site"), \
                 patch("app.direct_site_download_link", return_value="https://comics.example/dls/token") as choose, \
                 patch("app._safe_urlopen", return_value=self._response(b"comic-bytes" * 1000)):
                app._fetch_direct_site_download(5, 11, "https://comics.example/dc/rebels/", "R.E.B.E.L.S. Vol. 2 #1 – 28")
        choose.assert_called_once_with("https://comics.example/dc/rebels/", "12", (2009, 2010))

    def test_a_failed_fetch_leaves_the_issue_wanted_and_says_why(self):
        store = Mock()
        with tempfile.TemporaryDirectory() as folder:
            with patch("app.catalog_store", return_value=store), \
                 patch("app.acquisition_staging_dir", return_value=Path(folder) / "direct_site"), \
                 patch("app.direct_site_download_link", side_effect=ValueError("no direct download link")):
                app._fetch_direct_site_download(5, 11, "https://comics.example/dc/supergirl-8/", "Supergirl #8")
            self.assertFalse(list((Path(folder) / "direct_site").rglob("*")), "staging is cleared")
        failed = [c for c in store.update_acquisition_download.call_args_list if c.args[1] == "failed"]
        self.assertEqual(len(failed), 1)
        job_id, status, reason = store.update_acquisition_job.call_args.args
        self.assertEqual((job_id, status), (11, "queued"), "wanted again, not failed outright")
        self.assertIn("no direct download link", reason)

    def test_a_share_page_is_refused_rather_than_saved_as_a_comic(self):
        # A post for a whole run linked to a WeTransfer page; the page was saved
        # as "Farmhand #1 - 20.cbz", reported as a corrupt archive, and fetched
        # again on the next try.
        page = b"<!DOCTYPE html><html lang=\"en\"><head><title>WeTransfer</title>"
        response = Mock()
        response.headers = {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(page))}
        response.geturl.return_value = "https://wetransfer.com/downloads/abc123"
        chunks = [page, b""]
        response.read.side_effect = lambda *_: chunks.pop(0)
        store = Mock()
        with tempfile.TemporaryDirectory() as folder:
            with patch("app.catalog_store", return_value=store), \
                 patch("app.acquisition_staging_dir", return_value=Path(folder) / "direct_site"), \
                 patch("app.direct_site_download_link", return_value="https://comics.example/dls/token"), \
                 patch("app._safe_urlopen", return_value=contextlib.nullcontext(response)):
                app._fetch_direct_site_download(5, 11, "https://comics.example/other-comics/farmhand-1-20/", "Farmhand #1 - 20")
            self.assertFalse(list((Path(folder) / "direct_site").rglob("*.cbz")), "no page saved as a comic")
        failed = [c for c in store.update_acquisition_download.call_args_list if c.args[1] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("wetransfer.com", failed[0].kwargs["error"])
        store.record_acquisition_release_failure.assert_called_once()
        args, kwargs = store.record_acquisition_release_failure.call_args
        self.assertEqual(args[:2], (11, "https://comics.example/other-comics/farmhand-1-20/"))
        self.assertEqual(kwargs["kind"], "not_a_file", "a hard refusal: the same link gives the same page")

    def test_a_page_without_an_html_type_is_still_recognised(self):
        self.assertTrue(app._looks_like_a_page("application/octet-stream", b"  <!doctype html><html>"))
        self.assertFalse(app._looks_like_a_page("application/zip", b"PK\x03\x04rest"))

    def test_reconcile_waits_for_our_own_fetcher_rather_than_asking_sabnzbd(self):
        store = Mock()
        with patch("app.catalog_store", return_value=store), \
             patch("app._sab_history_slot") as sab:
            result = app.reconcile_acquisition_download(
                {"id": 5, "job_id": 11, "source": "direct_site", "status": "downloading"},
            )
        self.assertEqual(result["status"], "downloading")
        sab.assert_not_called()

    def test_a_completed_direct_download_goes_through_the_same_import(self):
        store = Mock()
        with patch("app.catalog_store", return_value=store), \
             patch("app._sab_history_slot") as sab, \
             patch("app._import_completed_download", return_value={"status": "imported"}) as importer:
            result = app.reconcile_acquisition_download(
                {"id": 5, "job_id": 11, "source": "direct_site", "status": "completed",
                 "sab_storage": "/config/downloads/direct_site/11"},
            )
        self.assertEqual(result["status"], "imported")
        sab.assert_not_called()
        self.assertEqual(importer.call_args.args[2], "/config/downloads/direct_site/11")


class KeylessServiceTests(unittest.TestCase):
    """A public site has nothing to authenticate to."""

    def test_a_keyless_service_can_be_enabled_without_a_key(self):
        with patch("app.load_acquisition_service_config", return_value={
            "direct_site": {"enabled": True, "url": "https://comics.example"},
        }):
            service = app._enabled_acquisition_service("direct_site")
        self.assertEqual(service["url"], "https://comics.example")

    def test_a_service_that_needs_a_key_still_does(self):
        with patch("app.load_acquisition_service_config", return_value={
            "prowlarr": {"enabled": True, "url": "http://localhost:9696"},
        }):
            with self.assertRaisesRegex(ValueError, "Prowlarr"):
                app._enabled_acquisition_service("prowlarr")


class ManualImportTests(unittest.TestCase):
    """A comic fetched by hand, handed to the issue that wanted it.

    There was no way to do this: a comic found by hand had to be dropped in the
    library folder and waited for, with nothing connecting it to the wanted
    issue. Being handed the file is still not evidence about it -- it goes
    through the same identity check as any download.
    """

    CONTEXT = {
        "seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
        "issueNumber": "2", "issueTitle": None, "publicationYear": 2012,
        "requestId": "9", "existingDirectory": None, "format": "comic",
    }

    @staticmethod
    def _comic_bytes():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("001.png", b"\x89PNG\r\n\x1a\n" + b"0" * 128)
        return buffer.getvalue()

    @contextlib.contextmanager
    def _library(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "comics"
            library.mkdir()
            staging = root / "staging"
            store = Mock()
            store.get_acquisition_job_context.return_value = dict(self.CONTEXT)
            store.record_acquisition_download.return_value = {"id": "5"}
            with patch("app.catalog_store", return_value=store), \
                 patch("app.COMIC_LIBRARY_ROOT", library), \
                 patch("app.acquisition_staging_dir", return_value=staging), \
                 patch("app._catalog_imported_issue", return_value=7):
                yield store, library, staging

    def test_a_comic_is_filed_under_the_run_and_the_job_closes(self):
        data = self._comic_bytes()
        with self._library() as (store, library, staging):
            result = app.import_uploaded_comic(
                11, io.BytesIO(data), len(data), "Saga 002 (2012).cbz",
            )
            # Inside the fixture: the library lives in a temporary directory.
            landed = Path(result["destination"])
            self.assertTrue(landed.is_file())
            self.assertEqual(landed.name, "Saga (2012) #002.cbz", "named by the library, not the upload")
            # resolve(): the library path places it through a resolved root, and
            # macOS temp directories are symlinks (/var -> /private/var).
            self.assertEqual(landed.parent, (library / "Image Comics" / "Saga (2012)").resolve())
            self.assertFalse(any(staging.rglob("*.cbz")), "nothing left in staging")
        self.assertEqual(result["status"], "imported")
        store.record_acquisition_download.assert_called_once()
        self.assertEqual(store.record_acquisition_download.call_args.kwargs["source"], "manual")
        store.update_acquisition_job.assert_called_once()
        job_id, status, reason = store.update_acquisition_job.call_args.args
        self.assertEqual((job_id, status), (11, "fulfilled"))
        self.assertIn("uploaded", reason)

    def test_a_comic_that_is_another_issue_is_refused(self):
        data = self._comic_bytes()
        with self._library() as (store, library, _staging):
            with self.assertRaises(app.DownloadContentMismatch):
                app.import_uploaded_comic(
                    11, io.BytesIO(data), len(data), "Saga 004 (2012).cbz",
                )
        store.update_acquisition_job.assert_not_called()
        self.assertEqual(list(library.rglob("*.cbz")), [])

    def test_something_that_is_not_a_comic_is_refused_before_it_is_read(self):
        with self._library() as (store, _library, _staging):
            with self.assertRaisesRegex(ValueError, "not a comic"):
                app.import_uploaded_comic(11, io.BytesIO(b"x"), 1, "notes.txt")
        store.get_acquisition_job_context.assert_called_once()

    def test_a_truncated_upload_does_not_reach_the_library(self):
        data = self._comic_bytes()
        with self._library() as (store, library, staging):
            with self.assertRaisesRegex(ValueError, "ended before"):
                app.import_uploaded_comic(
                    11, io.BytesIO(data[:20]), len(data), "Saga 002 (2012).cbz",
                )
        self.assertEqual(list(library.rglob("*.cbz")), [])
        self.assertFalse(staging.exists() and any(staging.rglob("*")))


class ComicPackDownloadTests(unittest.TestCase):
    """A pack -- comics zipped into one file -- gives up the issue it was grabbed
    for. the download site's "Batman Beyond 2.0 #1 - 40 + TPBs" came as one .cbz of
    twenty .cbr issues, and was refused as unreadable with #2 inside it."""

    JOB = {"seriesTitle": "Batman Beyond 2.0", "seriesYear": 2013, "issueNumber": "2",
           "publisher": "DC Comics", "format": "comic", "preferredLanguage": "en"}

    @staticmethod
    def _inventory(parsed):
        return {"file_health": {"status": "ok"}, "embedded_metadata": {},
                "lookup_identity": {"title": parsed.title, "issue": parsed.issue}}

    @staticmethod
    def _pack(root, extra=None):
        source = root / "Batman Beyond 2.0 #1 - 40 + TPBs (2013-2014)"
        source.mkdir()
        with zipfile.ZipFile(source / "Batman Beyond 2.0 #1 \u2013 40 + TPBs (2013-2014).cbz", "w") as pack:
            for number in (1, 2, 3):
                comic = io.BytesIO()
                with zipfile.ZipFile(comic, "w") as inner:
                    inner.writestr("001.jpg", b"page")
                pack.writestr(f"Batman Beyond 2.0 001-020 (2013-2014)/Batman Beyond 2.0 00{number} (2013) "
                              "(digital) (Son of Ultron-Empire).cbz", comic.getvalue())
            for name, body in (extra or {}).items():
                pack.writestr(name, body)
        return source

    def test_the_wanted_issue_is_taken_out_of_the_pack_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = self._pack(root)
            with patch("app.inventory_file", side_effect=self._inventory):
                selected = app.select_downloaded_comic(source, self.JOB, root)
            try:
                self.assertEqual(Path(selected["path"]).name,
                                 "Batman Beyond 2.0 002 (2013) (digital) (Son of Ultron-Empire).cbz")
                self.assertTrue(selected["issueMatch"])
                self.assertEqual(len(list(Path(selected["unpackedDir"]).iterdir())), 1, "only #2 is copied out")
            finally:
                app._discard_converted(selected)
            self.assertFalse(Path(selected["path"]).exists(), "the copy goes once filed")

    def test_a_pack_without_the_issue_is_refused_for_that_issue_and_says_what_it_holds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = self._pack(root)
            with patch("app.inventory_file", side_effect=self._inventory), \
                    self.assertRaises(app.DownloadContentMismatch) as caught:
                app.select_downloaded_comic(source, {**self.JOB, "issueNumber": "25"}, root)
        self.assertEqual(caught.exception.kind, "contradiction")
        self.assertIn("#1-#3", str(caught.exception))

    def test_a_pack_taken_by_hand_gives_up_the_issue_by_number_alone(self):
        job = {**self.JOB, "seriesTitle": "Justice League Unlimited"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = self._pack(root)
            with patch("app.inventory_file", side_effect=self._inventory):
                with self.assertRaises(app.DownloadContentMismatch):
                    app.select_downloaded_comic(source, job, root)
                selected = app.select_downloaded_comic(source, job, root, vouched=True)
            try:
                self.assertEqual(Path(selected["path"]).name,
                                 "Batman Beyond 2.0 002 (2013) (digital) (Son of Ultron-Empire).cbz")
            finally:
                app._discard_converted(selected)
            with patch("app.inventory_file", side_effect=self._inventory), \
                    self.assertRaises(app.DownloadContentMismatch) as caught:
                app.select_downloaded_comic(source, {**job, "issueNumber": "25"}, root, vouched=True)
        self.assertEqual(caught.exception.kind, "unidentified", "their pack proves nothing against the release")
        self.assertIn("it holds: Batman Beyond 2.0 001 (2013)", str(caught.exception))

    def test_a_path_inside_a_pack_never_leaves_the_folder_it_is_copied_to(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            comic = io.BytesIO()
            with zipfile.ZipFile(comic, "w") as inner:
                inner.writestr("001.jpg", b"page")
            source = self._pack(root, {"../../Batman Beyond 2.0 002 (2013) escaped.cbz": comic.getvalue()})
            with patch("app.inventory_file", side_effect=self._inventory):
                selected = app.select_downloaded_comic(source, self.JOB, root)
            try:
                unpacked = Path(selected["unpackedDir"])
                self.assertTrue(all(path.parent == unpacked for path in unpacked.iterdir()))
                self.assertFalse((root.parent / "Batman Beyond 2.0 002 (2013) escaped.cbz").exists())
            finally:
                app._discard_converted(selected)


class RunPackPassTests(unittest.TestCase):
    """One search for the run, before asking for its issues one at a time.

    A job for Batman #40 asks for "Batman 040", and "Batman 001-100" is not an
    answer to that question. It is an answer to the run's, which per-issue
    search never asks -- so following a 163-issue run took 163 singles.
    """

    @staticmethod
    def _store(wanted=40):
        store = Mock()
        store.wanted_run_issues.return_value = [
            {"jobId": 100 + n, "issueId": 200 + n, "issueNumber": str(n), "status": "queued"}
            for n in range(1, wanted + 1)
        ]
        store.get_acquisition_job_context.return_value = {
            "requestId": "48", "seriesTitle": "Batman", "issueNumber": "1", "runIssueCount": 163,
        }
        store.rejected_release_keys_for_jobs.return_value = set()
        return store

    @staticmethod
    def _pack(title="Batman 001-100 (Digital)", size=20_000_000_000, first=1, last=100, cid="pack-1"):
        return {"id": cid, "title": title, "sizeBytes": size, "matchScore": 82,
                "pack": {"first": first, "last": last}}

    def test_a_run_mostly_missing_is_searched_once_and_the_pack_taken(self):
        store = self._store()
        jobs = [100 + n for n in range(1, 41)]
        with patch("app.search_prowlarr_releases", return_value={"candidates": [self._pack()]}) as search, \
             patch("app.send_release_to_sabnzbd", return_value={"status": "grabbed"}) as send:
            grabbed = app._grab_run_pack(store, 48, jobs)
        self.assertEqual(grabbed["covers"], 40)
        search.assert_called_once_with(101, "Batman")
        send.assert_called_once_with(101, "pack-1")

    def test_the_pack_covering_most_of_the_run_wins_then_the_smaller_one(self):
        store = self._store()
        jobs = [100 + n for n in range(1, 41)]
        offers = [
            self._pack(cid="small", first=1, last=20, size=5_000_000_000),
            self._pack(cid="big-fat", first=1, last=100, size=30_000_000_000),
            self._pack(cid="big-lean", first=1, last=100, size=18_000_000_000),
        ]
        with patch("app.search_prowlarr_releases", return_value={"candidates": offers}), \
             patch("app.send_release_to_sabnzbd", return_value={"status": "grabbed"}) as send:
            app._grab_run_pack(store, 48, jobs)
        send.assert_called_once_with(101, "big-lean")

    def test_a_direct_site_pack_holding_the_run_is_taken_when_usenet_has_none(self):
        """Batman Beyond 2.0's forty issues were on the download site as one pack, and
        the run went out as forty singles because only Usenet was asked."""
        store = self._store()
        jobs = [100 + n for n in range(1, 41)]
        direct_site = [self._pack(cid="gc-pack", title="Batman #1 - 40 + TPBs", first=1, last=40, size=480_000_000),
                     {"id": "gc-single", "title": "Batman #1", "matchScore": 95, "sizeBytes": 30_000_000}]
        with patch("app.search_prowlarr_releases", return_value={"candidates": []}), \
             patch("app._enabled_acquisition_service", return_value={}), \
             patch("app._direct_site_candidates", return_value=direct_site), \
             patch("app.grab_release_candidate", return_value={"status": "downloading"}) as grab:
            grabbed = app._grab_run_pack(store, 48, jobs)
        self.assertEqual(grabbed["covers"], 40)
        grab.assert_called_once_with(101, "gc-pack")

    def test_a_pack_that_failed_before_is_not_taken_again(self):
        store = self._store()
        dead = {**self._pack(cid="gc-dead", first=1, last=60), "postUrl": "https://comics.example/y-1-60"}
        store.rejected_release_keys_for_jobs.return_value = {"https://comics.example/y-1-60"}
        with patch("app.search_prowlarr_releases", return_value={"candidates": [dead]}), \
             patch("app.grab_release_candidate") as grab:
            self.assertIsNone(app._grab_run_pack(store, 48, [100 + n for n in range(1, 41)]))
        grab.assert_not_called()
        # Judged against every issue of the run, not the one the pack happens
        # to be grabbed against: after a failure that issue falls back to a
        # single and another leads, and the refusal must still count.
        self.assertEqual(sorted(store.rejected_release_keys_for_jobs.call_args.args[0]), [100 + n for n in range(1, 41)])

    def test_a_run_pack_that_fails_hands_the_run_to_its_single_issues_at_once(self):
        """Y: The Last Man's sixty issues waited behind a pack that never came."""
        store = self._store()
        started = []
        class Thread:
            def __init__(self, target, args, kwargs, **_):
                started.append((target, args, kwargs))
            def start(self):
                pass
        with patch("app.catalog_store", return_value=store), patch("app.threading.Thread", Thread), \
             patch("app._acquisition_services_ready", return_value=False):
            app._resume_run_after_pack(101, "Batman 001-100 (Digital)")
        self.assertEqual(started, [], "no indexer or downloader: the same check every automatic search makes")
        with patch("app.catalog_store", return_value=store), patch("app.threading.Thread", Thread), \
             patch("app._acquisition_services_ready", return_value=True):
            app._resume_run_after_pack(101, "Batman 001-100 (Digital)")
            app._resume_run_after_pack(101, "Batman 001 (2016) (Digital)")
        self.assertEqual(len(started), 1, "a single issue failing is its own fallback's business")
        target, args, kwargs = started[0]
        self.assertEqual((target, args, kwargs), (app._automatic_release_grabs, (48,),
                                                  {"skip_run_pack": True, "exclude": frozenset({101})}))

    def test_no_pack_means_the_run_falls_back_to_singles(self):
        store = self._store()
        with patch("app.search_prowlarr_releases", return_value={"candidates": [
            {"id": "single", "title": "Batman 001 (2016)", "matchScore": 95, "sizeBytes": 40_000_000},
        ]}), patch("app.send_release_to_sabnzbd") as send:
            self.assertIsNone(app._grab_run_pack(store, 48, [100 + n for n in range(1, 41)]))
        send.assert_not_called()

    def test_a_run_missing_only_a_few_issues_is_not_searched_at_all(self):
        store = self._store(wanted=3)
        with patch("app.search_prowlarr_releases") as search:
            self.assertIsNone(app._grab_run_pack(store, 48, [101, 102, 103]))
        search.assert_not_called()

    def test_a_pack_that_cannot_be_sent_falls_back_rather_than_stopping(self):
        store = self._store()
        with patch("app.search_prowlarr_releases", return_value={"candidates": [self._pack()]}), \
             patch("app.send_release_to_sabnzbd", side_effect=ReleaseDownloadError("nzb gone")):
            self.assertIsNone(app._grab_run_pack(store, 48, [100 + n for n in range(1, 41)]))

    def test_a_grabbed_pack_stops_the_per_issue_fan_out(self):
        """Otherwise the run is taken twice: once as a pack, once as singles."""
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [101, 102, 103]
        with patch("app.catalog_store", return_value=store), \
             patch("app._grab_run_pack", return_value={"title": "Batman 001-100", "covers": 40, "jobId": 101}), \
             patch("app._auto_grab_release") as single:
            app._automatic_release_grabs(48)
        single.assert_not_called()

    def test_without_a_pack_every_issue_is_searched_as_before(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [101, 102]
        with patch("app.catalog_store", return_value=store), \
             patch("app._grab_run_pack", return_value=None), \
             patch("app._auto_grab_release", return_value=None) as single:
            app._automatic_release_grabs(48)
        self.assertEqual([call.args[0] for call in single.call_args_list], [101, 102])

    def test_the_whole_backlog_sweep_does_not_run_a_pack_search(self):
        """It spans many runs; each would need its own search."""
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [101]
        with patch("app.catalog_store", return_value=store), \
             patch("app._grab_run_pack") as pack, \
             patch("app._auto_grab_release", return_value=None):
            app._automatic_release_grabs(None)
        pack.assert_not_called()


class PackSweepTests(unittest.TestCase):
    """One download, many issues -- and the folder goes as soon as it is done."""

    DOWNLOAD = {"id": 31, "job_id": 7, "release_title": "Chew 001-060 (Digital)"}
    CONTEXT = {"requestId": "9", "seriesTitle": "Chew", "issueNumber": "4"}

    def _store(self, *outstanding):
        store = Mock()
        store.wanted_run_issues.return_value = list(outstanding)
        store.get_acquisition_job_context.side_effect = lambda job_id: {
            "requestId": "9", "seriesTitle": "Chew", "issueNumber": str(job_id),
        }
        return store

    @staticmethod
    def _job(job_id, status="queued"):
        return {"jobId": job_id, "issueId": 100 + job_id, "issueNumber": str(job_id), "status": status}

    @contextlib.contextmanager
    def _pipeline(self, select=None, place=None):
        with patch("app._resolve_sab_download_source", return_value=pathlib.Path("/downloads/chew")), \
             patch("app.select_downloaded_comic", side_effect=select or (lambda *a, **k: {"path": "/downloads/chew/x.cbz"})), \
             patch("app._import_selected_comic", side_effect=place or (lambda selected, context, download, root: {
                 "destination": f"/comics/Chew/Chew #{context['issueNumber']}.cbz",
             })), \
             patch("app._catalog_imported_issue", return_value=1), \
             patch("app._discard_converted"):
            yield

    def test_a_pack_fulfils_the_runs_other_wanted_issues(self):
        store = self._store(self._job(7, "grabbed"), self._job(8), self._job(9))
        with self._pipeline():
            swept = app._sweep_download_for_other_issues(store, self.DOWNLOAD, "/downloads/chew", self.CONTEXT)
        self.assertEqual([item["jobId"] for item in swept], ["8", "9"])
        self.assertEqual(store.record_download_import.call_count, 2)
        fulfilled = [call.args for call in store.update_acquisition_job.call_args_list]
        self.assertEqual([args[0] for args in fulfilled], [8, 9])
        self.assertTrue(all(args[1] == "fulfilled" for args in fulfilled))
        self.assertIn("Chew 001-060", fulfilled[0][2])

    def test_the_job_that_grabbed_it_is_not_imported_twice(self):
        store = self._store(self._job(7, "grabbed"))
        with self._pipeline():
            self.assertEqual(app._sweep_download_for_other_issues(store, self.DOWNLOAD, "/d", self.CONTEXT), [])
        store.update_acquisition_job.assert_not_called()

    def test_an_issue_the_download_does_not_hold_stays_wanted(self):
        store = self._store(self._job(8), self._job(9))

        def select(source, context, *args, **kwargs):
            if context["issueNumber"] == "9":
                raise app.DownloadContentMismatch("no comic for #9", kind="content")
            return {"path": "/downloads/chew/8.cbz"}

        with self._pipeline(select=select):
            swept = app._sweep_download_for_other_issues(store, self.DOWNLOAD, "/d", self.CONTEXT)
        self.assertEqual([item["jobId"] for item in swept], ["8"])
        self.assertEqual([call.args[0] for call in store.update_acquisition_job.call_args_list], [8])

    def test_one_failure_does_not_stop_the_rest(self):
        store = self._store(self._job(8), self._job(9))

        def place(selected, context, download, root):
            if context["issueNumber"] == "8":
                raise OSError("library is not mounted")
            return {"destination": "/comics/Chew/Chew #9.cbz"}

        with self._pipeline(place=place):
            swept = app._sweep_download_for_other_issues(store, self.DOWNLOAD, "/d", self.CONTEXT)
        self.assertEqual([item["jobId"] for item in swept], ["9"])

    def test_a_download_with_no_request_sweeps_nothing(self):
        store = self._store(self._job(8))
        with self._pipeline():
            self.assertEqual(app._sweep_download_for_other_issues(store, self.DOWNLOAD, "/d", {}), [])
        store.wanted_run_issues.assert_not_called()


class CoverPathContainmentTests(unittest.TestCase):
    """The cover endpoint may only reach comics the library holds.

    It took a filesystem path from the query string and served any readable
    archive on the host. Behind the session check, but `localBypass` exempts
    local addresses, so on a NAS the whole LAN could read any comic on the box.
    """

    @staticmethod
    def _store(*roots):
        store = Mock()
        store.library_root_paths.return_value = [str(root) for root in roots]
        return store

    def test_a_comic_inside_a_library_root_is_served(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "comics"
            (root / "DC Comics").mkdir(parents=True)
            comic = root / "DC Comics" / "Nightwing 001.cbz"
            comic.write_bytes(b"comic")
            with patch("app.catalog_store", return_value=self._store(root)):
                self.assertEqual(app._cover_source_path(str(comic)), comic.resolve())

    def test_a_comic_outside_every_root_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "comics"
            root.mkdir()
            elsewhere = Path(folder) / "private" / "diary.cbz"
            elsewhere.parent.mkdir()
            elsewhere.write_bytes(b"not yours")
            with patch("app.catalog_store", return_value=self._store(root)):
                self.assertIsNone(app._cover_source_path(str(elsewhere)))

    def test_a_symlink_that_escapes_the_library_is_refused(self):
        """Resolved first, or a link inside the library reaches outside it."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "comics"
            root.mkdir()
            outside = Path(folder) / "secret.cbz"
            outside.write_bytes(b"not yours")
            link = root / "Nightwing 001.cbz"
            link.symlink_to(outside)
            with patch("app.catalog_store", return_value=self._store(root)):
                self.assertIsNone(app._cover_source_path(str(link)))

    def test_a_folder_a_missing_file_and_an_empty_path_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "comics"
            (root / "DC Comics").mkdir(parents=True)
            with patch("app.catalog_store", return_value=self._store(root)):
                self.assertIsNone(app._cover_source_path(str(root / "DC Comics")))
                self.assertIsNone(app._cover_source_path(str(root / "gone.cbz")))
                self.assertIsNone(app._cover_source_path(""))
                self.assertIsNone(app._cover_source_path(None))

    def test_an_unreadable_catalog_denies_rather_than_opens(self):
        with tempfile.TemporaryDirectory() as folder:
            comic = Path(folder) / "Nightwing 001.cbz"
            comic.write_bytes(b"comic")
            with patch("app.catalog_store", side_effect=RuntimeError("database is locked")):
                self.assertIsNone(app._cover_source_path(str(comic)))


class IssueYearBeforeGrabTests(unittest.TestCase):
    """An unknown year silently disables the wrong-era check, so fill it first.

    `_release_year_conflict` is what keeps a relaunch's release out of the
    original run's request, and it only speaks when the issue's year is known.
    """

    def setUp(self):
        app._YEAR_FILL_ATTEMPTED.clear()

    @staticmethod
    def _store(year=None, siblings=()):
        store = Mock()
        store.get_acquisition_job_context.return_value = {
            "seriesId": "37", "seriesTitle": "Nightwing", "issueNumber": "23",
            "publicationYear": year,
        }
        store.runs_sharing_title.return_value = list(siblings)
        return store

    def test_a_known_year_asks_the_providers_nothing(self):
        with patch("app.catalog_store", return_value=self._store(year=2016)), patch(
            "app.sync_issue_catalog"
        ) as sync:
            self.assertIsNone(app._issue_year_gate(7))
        sync.assert_not_called()

    def test_a_missing_year_is_filled_before_the_search(self):
        store = Mock()
        store.get_acquisition_job_context.side_effect = [
            {"seriesId": "37", "publicationYear": None},
            {"seriesId": "37", "publicationYear": 2016},
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order", return_value=[("metron", {})]
        ), patch("app.sync_issue_catalog") as sync:
            self.assertIsNone(app._issue_year_gate(7))
        sync.assert_called_once_with(37)

    def test_a_year_nobody_knows_holds_a_title_that_has_two_runs(self):
        store = self._store(year=None, siblings=[{"id": "36", "title": "Nightwing", "year": 2011}])
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order", return_value=[("metron", {})]
        ), patch("app.sync_issue_catalog"):
            reason = app._issue_year_gate(7)
        self.assertIn("Nightwing (2011)", reason)
        self.assertIn("wrong run", reason)

    def test_a_title_with_one_run_is_searched_as_before(self):
        """Nothing to confuse it with, so an unknown date is no reason to wait."""
        with patch("app.catalog_store", return_value=self._store(year=None)), patch(
            "app._series_enrichment_provider_order", return_value=[("metron", {})]
        ), patch("app.sync_issue_catalog"):
            self.assertIsNone(app._issue_year_gate(7))

    def test_one_refresh_per_run_however_many_issues_it_has(self):
        store = self._store(year=None, siblings=[{"id": "36", "title": "Nightwing", "year": 2011}])
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order", return_value=[("metron", {})]
        ), patch("app.sync_issue_catalog") as sync:
            for job_id in (7, 8, 9):
                app._issue_year_gate(job_id)
        sync.assert_called_once_with(37)

    def test_an_install_with_no_metadata_provider_still_acquires(self):
        with patch("app.catalog_store", return_value=self._store(year=None)), patch(
            "app._series_enrichment_provider_order", return_value=[]
        ), patch("app.sync_issue_catalog") as sync:
            self.assertIsNone(app._issue_year_gate(7))
        sync.assert_not_called()

    def test_a_held_job_is_not_searched_and_says_why(self):
        with patch("app._issue_year_gate", return_value="No release date is known yet."), patch(
            "app.search_prowlarr_releases"
        ) as search, patch("app._say_on_the_row") as say:
            self.assertIsNone(_auto_grab_release(7))
        search.assert_not_called()
        say.assert_called_once_with(7, "No release date is known yet.")


def _redirect(location, code=301):
    headers = email.message.Message()
    headers["Location"] = location
    return urllib.error.HTTPError("http://sab/api", code, "Moved", headers, None)


class RedirectedUploadTests(unittest.TestCase):
    """A redirected POST arrives empty, and must not read as a rejection.

    urllib answers a redirect by re-sending the request as a GET with no body,
    so the file never leaves. SABnzbd then reports that it was sent nothing,
    which looked exactly like SABnzbd refusing the file.
    """

    def test_an_https_upgrade_on_the_same_host_is_followed_with_the_body(self):
        opener = Mock()
        opener.open.side_effect = [
            _redirect("https://sab/api?mode=addfile"),
            io.BytesIO(b'{"status": true, "nzo_ids": ["a"]}'),
        ]
        with patch("app.urllib.request.build_opener", return_value=opener):
            result = post_multipart_file_json(
                "http://sab/api?mode=addfile", field_name="name",
                filename="x.nzb", content=b"<nzb></nzb>",
                content_type="application/x-nzb", headers={},
            )

        self.assertEqual(result, {"status": True, "nzo_ids": ["a"]})
        retried = opener.open.call_args.args[0]
        self.assertEqual(retried.full_url, "https://sab/api?mode=addfile")
        # The point of the retry: the file goes with it.
        self.assertIn(b"<nzb></nzb>", retried.data)
        self.assertEqual(retried.get_method(), "POST")

    def test_a_redirect_to_another_host_is_refused_rather_than_replayed(self):
        """The request carries an API key, so it is not replayed anywhere."""
        opener = Mock()
        opener.open.side_effect = [_redirect("https://elsewhere.example/api")]
        with patch("app.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(UploadRedirected, "elsewhere.example"):
                post_multipart_file_json(
                    "http://sab/api", field_name="name", filename="x.nzb",
                    content=b"<nzb></nzb>", content_type="application/x-nzb",
                    headers={},
                )
        self.assertEqual(opener.open.call_count, 1)

    def test_a_redirect_loop_stops_instead_of_uploading_forever(self):
        opener = Mock()
        opener.open.side_effect = [
            _redirect("https://sab/api"), _redirect("https://sab/api"),
        ]
        with patch("app.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(UploadRedirected):
                post_multipart_file_json(
                    "http://sab/api", field_name="name", filename="x.nzb",
                    content=b"<nzb></nzb>", content_type="application/x-nzb",
                    headers={},
                )
        self.assertEqual(opener.open.call_count, 2)


class SabRejectionReasonTests(unittest.TestCase):
    def test_the_reason_sabnzbd_gives_is_reported(self):
        """SABnzbd names the problem; repeating only "did not accept" lost it."""
        with patch("app.load_acquisition_service_config", return_value={
            "sabnzbd": {"enabled": True, "url": "http://sab", "apiKey": "k",
                        "category": "comics"},
        }), patch("app.post_multipart_file_json", return_value={
            "status": False, "error": "expects one parameter",
        }):
            with self.assertRaisesRegex(SABSubmissionError, "expects one parameter"):
                app._submit_nzb_to_sabnzbd(
                    {"url": "http://sab", "apiKey": "k", "category": "comics"},
                    "Some Comic 001", b"<nzb></nzb>",
                )


class FinishedRunsAreMarkedFinishedTests(unittest.TestCase):
    """Automatic enrichment must not report every run as still publishing.

    The issue list was fetched automatically all along, but the Metron branch
    passed a hard-coded "complete_to_date", so a finished run stayed Ongoing
    until someone refreshed it by hand -- for every run in the library.
    """

    def _enrich_with_evidence(self, evidence):
        store = Mock()
        store.get_series_enrichment_context.return_value = {
            "title": "Chew", "year": 2009, "publisher": "Image",
        }
        store.metadata_provider_available.return_value = True
        store.apply_issue_list.return_value = {"issues": 60}
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[("metron", {"token": "t"})],
        ), patch("app._metron_issue_entries", return_value=(
            "12", "https://metron.cloud/series/12/",
            [{"number": "1"}, {"number": "2"}], evidence,
        )):
            enrich_catalog_series(7)
        return store.apply_issue_list.call_args.kwargs

    def _enrich_with_end_year(self, ended_year):
        return self._enrich_with_evidence(
            _run_end_evidence(ended_year, modelled=True)
        )["status"]

    def test_a_run_that_ended_is_complete(self):
        self.assertEqual(self._enrich_with_end_year(2016), "complete")

    def test_a_run_with_no_end_year_is_only_complete_to_date(self):
        self.assertEqual(self._enrich_with_end_year(None), "complete_to_date")

    def test_a_run_ending_this_year_counts_as_finished(self):
        import time as _time
        self.assertEqual(
            self._enrich_with_end_year(_time.gmtime().tm_year), "complete"
        )

    def test_a_run_ending_in_a_future_year_is_still_publishing(self):
        import time as _time
        self.assertEqual(
            self._enrich_with_end_year(_time.gmtime().tm_year + 1), "complete_to_date"
        )

    def test_metron_reporting_no_end_year_is_positively_ongoing(self):
        # Metron's series record has the field, so a null in it is a statement
        # that the run continues -- not the silence Comic Vine gives.
        self.assertEqual(
            _run_end_evidence(None, modelled=True), {"state": "ongoing", "year": None}
        )

    def test_a_provider_without_the_field_states_no_opinion(self):
        self.assertEqual(
            _run_end_evidence(None, modelled=False), {"state": "unknown", "year": None}
        )

    def test_a_year_given_as_a_string_still_finishes_the_run(self):
        # sync_gcd_issue_catalog used isinstance(ended, int) where every sibling
        # coerced, so a GCD year that arrived as text pinned the run to ongoing.
        self.assertEqual(
            _run_end_evidence("2016", modelled=True), {"state": "ended", "year": 2016}
        )

    def test_comic_vine_states_no_opinion_rather_than_still_publishing(self):
        store = Mock()
        store.get_series_enrichment_context.return_value = {
            "title": "Chew", "year": 2009, "publisher": "Image",
        }
        store.metadata_provider_available.return_value = True
        store.apply_issue_list.return_value = {"issues": 60}
        with patch("app.catalog_store", return_value=store), patch(
            "app._series_enrichment_provider_order",
            return_value=[("comic_vine", {"apiKey": "k"})],
        ), patch("app._comic_vine_issue_entries", return_value=(
            "12", "https://comicvine.example/volume/12/", [{"number": "1"}],
        )):
            enrich_catalog_series(7)
        self.assertEqual(
            store.apply_issue_list.call_args.kwargs["end_evidence"]["state"], "unknown",
            "Comic Vine has no end-year field; its silence must not claim the run continues",
        )


class EveryProviderWriteRecordsWhatItKnewTests(unittest.TestCase):
    """A provider write that forgets its end-year evidence is invisible.

    Two of eleven call sites were converted by grep and missed, because they
    computed the coverage status inline in a third shape. Both were GCD paths,
    so a run GCD had positively determined was finished still landed with no
    evidence and showed no badge -- the reported symptom, with a new cause.
    A structural check costs nothing and cannot be skipped by a future writer.
    """

    def test_every_apply_issue_list_call_passes_end_evidence(self):
        source = (Path(__file__).parent / "app.py").read_text()
        lines = source.split("\n")
        missing = []
        for index, line in enumerate(lines):
            if "apply_issue_list(" not in line or line.lstrip().startswith("def "):
                continue
            block, depth = [], 0
            for offset in range(index, min(index + 20, len(lines))):
                block.append(lines[offset])
                depth += lines[offset].count("(") - lines[offset].count(")")
                if depth <= 0 and offset > index:
                    break
            if "end_evidence" not in "\n".join(block):
                missing.append(f"app.py:{index + 1}")
        self.assertEqual(
            missing, [],
            "these apply_issue_list calls record a coverage claim without the "
            "end-year evidence behind it, so the run's badge silently vanishes",
        )


class AutomaticSearchOnCreationTests(unittest.TestCase):
    """A new request searches and grabs by itself, as the -arr tools do.

    Adding something monitored there triggers a search that takes the best
    release clearing the bar; the candidate list is a deliberate interactive
    step, not the first one. Flipparr had that backwards.
    """

    def test_every_waiting_job_is_grabbed(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [11, 12, 13]
        with patch("app.catalog_store", return_value=store), patch(
            "app._auto_grab_release", return_value={"status": "grabbed"}
        ) as grab:
            app._automatic_release_grabs(5)

        first = store.acquisition_jobs_awaiting_release.call_args_list[0]
        self.assertEqual((first.args, first.kwargs), ((5,), {"backoff": False}))
        self.assertEqual([call.args[0] for call in grab.call_args_list], [11, 12, 13])

    def test_one_issue_without_a_release_does_not_stop_the_others(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [11, 12, 13]
        with patch("app.catalog_store", return_value=store), patch(
            "app._auto_grab_release",
            side_effect=[{"status": "grabbed"}, None, {"status": "grabbed"}],
        ) as grab:
            app._automatic_release_grabs(5)
        self.assertEqual(grab.call_count, 3)

    def test_a_raising_job_does_not_stop_the_others(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [11, 12]
        with patch("app.catalog_store", return_value=store), patch(
            "app._auto_grab_release",
            side_effect=[RuntimeError("indexer exploded"), {"status": "grabbed"}],
        ) as grab:
            app._automatic_release_grabs(5)
        self.assertEqual(grab.call_count, 2)

    def test_nothing_is_searched_without_an_indexer_or_download_client(self):
        """Otherwise every issue runs a search that cannot succeed."""
        with patch("app._enabled_acquisition_service", side_effect=ValueError("off")), \
             patch("app.threading.Thread") as thread:
            app._start_automatic_release_grabs({"id": "5"})
        thread.assert_not_called()

    def test_a_request_without_an_id_starts_nothing(self):
        with patch("app._enabled_acquisition_service", return_value={}), \
             patch("app.threading.Thread") as thread:
            app._start_automatic_release_grabs({})
        thread.assert_not_called()

    def test_the_search_runs_off_the_request_thread(self):
        """Twenty issues is twenty searches; the response must not wait."""
        with patch("app._enabled_acquisition_service", return_value={}), \
             patch("app.threading.Thread") as thread:
            app._start_automatic_release_grabs({"id": "5"})
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        self.assertEqual(thread.call_args.kwargs["args"], (5,))
        thread.return_value.start.assert_called_once()

    def test_an_issue_another_pass_finished_is_left_alone(self):
        """Each pass works from the list it read at the start; a second
        search of an issue already sent to SABnzbd sends it twice."""
        store = Mock()
        store.acquisition_jobs_awaiting_release.side_effect = [[11, 12], [12], [12]]
        with patch("app.catalog_store", return_value=store), patch(
            "app._auto_grab_release", return_value={"status": "grabbed"}
        ) as grab:
            app._automatic_release_grabs(5)
        self.assertEqual([call.args[0] for call in grab.call_args_list], [12])

    def test_an_issue_another_pass_is_searching_is_not_searched_twice(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [11, 12]
        app._JOBS_IN_FLIGHT.add(11)
        try:
            with patch("app.catalog_store", return_value=store), patch(
                "app._auto_grab_release", return_value={"status": "grabbed"}
            ) as grab:
                app._automatic_release_grabs(5)
        finally:
            app._JOBS_IN_FLIGHT.discard(11)
        self.assertEqual([call.args[0] for call in grab.call_args_list], [12])
        self.assertNotIn(12, app._JOBS_IN_FLIGHT, "a finished job is released")


class SearchForMissingTests(unittest.TestCase):
    """The Wanted list's own action: everything still missing, on demand."""

    def test_it_reports_how_many_issues_it_will_work_through(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [1, 2, 3]
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", return_value={}
        ), patch("app.threading.Thread") as thread:
            result = app.start_missing_release_search(confirmed=True)

        self.assertEqual((result["status"], result["searching"]), ("searching", 3))
        self.assertIn("3 missing issues", result["detail"])
        # The whole backlog, not one request.
        store.acquisition_jobs_awaiting_release.assert_called_once_with()
        thread.return_value.start.assert_called_once()

    def test_one_issue_is_not_described_in_the_plural(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [1]
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", return_value={}
        ), patch("app.threading.Thread"):
            self.assertIn(
                "1 missing issue.",
                app.start_missing_release_search(confirmed=True)["detail"],
            )

    def test_an_empty_backlog_starts_nothing(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = []
        with patch("app.catalog_store", return_value=store), patch(
            "app.threading.Thread"
        ) as thread:
            result = app.start_missing_release_search()
        self.assertEqual(result["status"], "idle")
        thread.assert_not_called()

    def test_it_says_which_service_is_missing_rather_than_failing_quietly(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [1]
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", side_effect=ValueError("off")
        ), patch("app.threading.Thread") as thread:
            with self.assertRaisesRegex(ValueError, "Prowlarr is not configured"):
                app.start_missing_release_search()
        thread.assert_not_called()


class StaleJobsAreNotRedownloadedTests(unittest.TestCase):
    """Job status records what was true when it was written.

    A followed run's jobs are created for every missing issue. Issues acquired
    afterwards leave their job sitting at "queued" until reconciliation runs,
    and searching from that list re-downloads comics already in the library --
    160 of 161 Fables issues, in the case that found this.
    """

    def test_the_backlog_is_reconciled_before_it_is_searched(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [1]
        calls = []
        store.reconcile_acquisition_jobs.side_effect = lambda *a: calls.append("reconcile")
        store.acquisition_jobs_awaiting_release.side_effect = (
            lambda *a, **kw: calls.append("select") or [1]
        )
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", return_value={}
        ), patch("app.threading.Thread"):
            app.start_missing_release_search(confirmed=True)
        self.assertEqual(calls, ["reconcile", "select"])

    def test_one_request_is_reconciled_before_its_jobs_are_grabbed(self):
        store = Mock()
        calls = []
        store.reconcile_acquisition_jobs.side_effect = lambda *a: calls.append("reconcile")
        store.acquisition_jobs_awaiting_release.side_effect = (
            lambda *a, **kw: calls.append("select") or []
        )
        with patch("app.catalog_store", return_value=store):
            app._automatic_release_grabs(7)
        self.assertEqual(calls, ["reconcile", "select"])
        store.reconcile_acquisition_jobs.assert_called_once_with(7)


class SearchMissingAsksFirstTests(unittest.TestCase):
    def test_nothing_starts_until_the_count_is_accepted(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = list(range(161))
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", return_value={}
        ), patch("app.threading.Thread") as thread:
            result = app.start_missing_release_search()

        self.assertEqual(result["status"], "confirm")
        self.assertEqual(result["searching"], 161)
        self.assertIn("161 missing issues", result["detail"])
        thread.assert_not_called()


class ReleaseSeriesMatchTests(unittest.TestCase):
    """A release has to be the series, not merely contain its name.

    Scoring asked whether the wanted title's words appeared anywhere in the
    release name, so a one-word title matched everything containing that word.
    Searching for Saga's missing issues grabbed Thor: The Deviants Saga, the
    French DC Saga anthology, Conan Saga, The Saga of Swamp Thing, and two
    Dragon Ball Z .mp4 files.

    Every release below is one this actually grabbed. The split is by what the
    file turned out to be, not by how its name looks.
    """

    # Different comics -- and, twice, not comics at all.
    WRONG_SERIES = [
        ("Thor-The.Deviants.Saga.001.2012.digital.Marika-Empire", "Saga", "1"),
        ("Comics.FR.-.DC.Saga.(Urban.Comics).-.002 (July, 2012) (cbz)", "Saga", "2"),
        ("Comics.FR.-.DC.Saga.(Urban.Comics).-.003 (July, 2012) (cbz)", "Saga", "3"),
        ("Thor Deviants Saga 004 (2012) (Digital) (Shadowcat-Empire)", "Saga", "4"),
        ("Thor Deviants Saga 005 (2012) (Digital) (Shadowcat-Empire)", "Saga", "5"),
        ("Dragon Ball Z Revised Manga Cut Saga 1 - The Saiyan Invasion 16 - 008 - "
         "Come Forth Shen Long The Saiyans Finally Arrive On Earth.mp4", "Saga", "8"),
        ("Conan Saga v1 017 (1988)", "Saga", "17"),
        ("The Saga of Swamp Thing 041 [1985] [digital] [Marika-Empire]", "Saga", "41"),
        # Not from this incident: a prefix test would let these through.
        ("Batman Beyond 001 (2016) (Digital)", "Batman", "1"),
        ("Hulk and Power Pack 001 (2007) (Digital)", "Hulk", "1"),
        # A wanted name of one or two words is never extended by "and …".
        ("Batman and Robin 001 (2011) (Digital)", "Batman", "1"),
        # A name that starts with a number keeps it: it is not a batch counter.
        ("100 Bullets 012 (2000) (Digital)", "Bullets", "12"),
        ("2000 AD 045 (1978)", "AD", "45"),
        ("Green Lantern and the Sinestro Corps War 001 (2008)", "Green Lantern", "1"),
        ("Green Lantern-Sinestro Corps - Secret Files and Origins and Other Stories Collected 01", "Green Lantern / Sinestro Corps: Secret Files", "1"),
    ]

    # The right comic, behind whatever wrapper the poster used.
    RIGHT_SERIES = [
        ("Saga 006 (2012) (Digital) (Zone-Empire)", "Saga", "6"),
        # A one-shot the cover calls "Secret Files and Origins" and Metron
        # "Secret Files", with the posting's date between name and number
        # (Green Lantern / Sinestro Corps, 2026-09-28).
        ("Green Lantern-Sinestro Corps - Secret Files and Origins, 2007-12-28 (01) (digital) (OkC.O.M.P.U.T.O.-Novus-HD)",
         "Green Lantern / Sinestro Corps: Secret Files", "1"),
        ("Green Lantern-Sinestro Corps - Secret Files and Origins 2007-12-28 01 digital OkC.O.M.P.U.T.O.-Novus-HD",
         "Green Lantern / Sinestro Corps: Secret Files", "1"),
        ("JLA - Secret Files & Origins 001 (1997) (Digital)", "JLA: Secret Files", "1"),
        ("Saga.068.2024.Digital.Zone-Empire", "Saga", "68"),
        # No brackets, so the scene group trails the issue number.
        ("Saga 015 2013 digital Minutemen-Spaztastic (cbr)", "Saga", "15"),
        # A yEnc subject: the real name is the quoted part.
        ('2022.04.27 [46/80] - yEnc "Saga 058 (2022) (Digital) (Zone-Empire).cbr"',
         "Saga", "58"),
        ('2022.05.25 [50/76] - yEnc "Saga 059 (2022) (Digital) (Zone-Empire).cbr"',
         "Saga", "59"),
        # A posting tag and a date in front of the name.
        ("Grab Bag 2013.05.01 Fables 031 (2005) (Digital) (Nahga-Empire)", "Fables", "31"),
        ("Grab Bag 2013.05.01 Fables 091 (2010) (Digital) (Nahga-Empire)", "Fables", "91"),
        # The number is written out as "Vol 1 No 129".
        ("Fables.Vol.1.No.129.Jul.2013.SCAN.Comic.eBook-iNTENSiTY", "Fables", "129"),
        ("Fables.095.(2010).(Digital).(NahgaEmpire)", "Fables", "95"),
        ("Absolute Flash 002 (2025) (Digital) (Shan-Empire)", "Absolute Flash", "2"),
        # Flashpoint tie-ins under a poster's batch counter or reading order
        # (13 open jobs, 2026-09-29), and an article that comes and goes.
        ("010-Flashpoint -Abin Sur -The Green Lantern 01 (2011) (c2c) (DangerAngel-CPS)",
         "Flashpoint: Abin Sur - The Green Lantern", "1"),
        ("Flashpoint. 02.03 Flashpoint - Abin Sur - The Green Lantern V2011 #001 (cbz)",
         "Flashpoint: Abin Sur - The Green Lantern", "1"),
        ("25.Flashpoint-Secret.Seven.01", "Flashpoint: Secret Seven", "1"),
        ("009-Flashpoint -Secret Seven 01 2011 noads DangerAngel-CPS", "Flashpoint: Secret Seven", "1"),
        ("Convergence - The Titans 002 (2015) (Digital) (ThatGuy-Empire)", "Convergence Titans", "2"),
        ("100 Bullets 012 (2000) (Digital)", "100 Bullets", "12"),
        ("2000 AD 045 (1978)", "2000 AD", "45"),
        ("The Department of Truth 012 (2021) (Digital) (Zone-Empire)",
         "The Department of Truth", "12"),
    ]

    def score(self, title, series, issue):
        return app._release_candidate_score(
            {"title": title, "categories": [{"id": "7030"}]},
            {"seriesTitle": series, "issueNumber": issue},
        )[0]

    def test_a_reading_order_label_is_not_an_issue_number(self):
        title = "Flashpoint. 02.03 Flashpoint - Abin Sur - The Green Lantern V2011 #001 (cbz)"
        self.assertFalse(app._release_issue_matches(title, "3"), "the 03 is the poster's order, not #3")
        self.assertFalse(app._release_issue_matches(title, "2"))
        self.assertTrue(app._release_issue_matches(title, "1"))
        self.assertLess(self.score(title, "Flashpoint: Abin Sur - The Green Lantern", "3"), 85)

    def test_another_series_carrying_the_name_never_reaches_a_strong_match(self):
        for title, series, issue in self.WRONG_SERIES:
            with self.subTest(title=title):
                self.assertLess(
                    self.score(title, series, issue), 85,
                    f"{title!r} is not {series}",
                )

    def test_the_series_itself_still_reaches_a_strong_match(self):
        for title, series, issue in self.RIGHT_SERIES:
            with self.subTest(title=title):
                self.assertGreaterEqual(
                    self.score(title, series, issue), 85,
                    f"{title!r} is {series}",
                )


class ProwlarrQueryFormsTests(unittest.TestCase):
    """An indexer matches the words it is given.

    A padded number is a different word from an unpadded one: searching
    "If Destruction Be Our Lot 002" returned nothing, while
    "If Destruction Be Our Lot 2" returned the issue and the series title alone
    returned the run. Padding by itself made those series unfindable.
    """

    def test_it_widens_from_padded_to_bare_to_the_series_alone(self):
        self.assertEqual(
            app._prowlarr_query_forms(
                {"seriesTitle": "If Destruction Be Our Lot", "issueNumber": "2"}
            ),
            [
                "If Destruction Be Our Lot 002",
                "If Destruction Be Our Lot 2",
                "If Destruction Be Our Lot 02",
                "If Destruction Be Our Lot",
            ],
        )

    def test_a_single_digit_issue_is_also_asked_for_in_two_digits(self):
        # "The Adventure Zone 04 - The Crystal Kingdom" answered neither
        # "004" nor "4" (2026-09-30).
        self.assertIn("The Adventure Zone 04",
                      app._prowlarr_query_forms({"seriesTitle": "The Adventure Zone", "issueNumber": "4"}))
        self.assertEqual(app._prowlarr_query_forms({"seriesTitle": "Fables", "issueNumber": "29"}),
                         ["Fables 029", "Fables 29", "Fables"], "two digits already is the two-digit form")

    def test_an_ebook_listing_counts_as_much_as_a_comic_listing(self):
        context = {"seriesTitle": "The Adventure Zone", "issueNumber": "4", "publicationYear": 2021}
        title = "The Adventure Zone 04 - The Crystal Kingdom (2021)"
        as_comic = app._release_candidate_score({"title": title, "categories": [{"id": 7030}]}, context)
        as_ebook = app._release_candidate_score({"title": title, "categories": [{"id": 7000}, {"id": 7020}]}, context)
        self.assertEqual(as_comic[0], as_ebook[0])
        self.assertIn("Listed as an ebook", as_ebook[1])

    def test_comics_are_searched_in_comics_and_ebooks(self):
        # A book publisher's graphic novels are filed as ebooks.
        with patch("app.fetch_json_with_headers", return_value=[]) as fetch:
            app._prowlarr_search({"url": "http://p", "apiKey": "k"}, "The Adventure Zone 04")
        self.assertIn("categories=7030&categories=7020", fetch.call_args.args[0])

    def test_a_three_digit_issue_has_no_bare_form_to_add(self):
        self.assertEqual(
            app._prowlarr_query_forms({"seriesTitle": "Fables", "issueNumber": "129"}),
            ["Fables 129", "Fables"],
        )

    def test_a_decimal_issue_is_asked_for_padded_as_releases_name_it(self):
        self.assertEqual(
            app._prowlarr_query_forms({"seriesTitle": "Green Lantern", "issueNumber": "23.1"}),
            ["Green Lantern 023.1", "Green Lantern 23.1", "Green Lantern"],
        )

    def test_a_lettered_issue_is_asked_for_with_its_letters_as_a_word(self):
        self.assertEqual(
            app._prowlarr_query_forms({"seriesTitle": "Superior Spider-Man", "issueNumber": "6AU"}),
            ["Superior Spider-Man 006 AU", "Superior Spider-Man 6 AU", "Superior Spider-Man"],
        )

    def test_punctuation_in_a_title_is_also_asked_for_plain(self):
        forms = app._prowlarr_query_forms({"seriesTitle": "Batman / Superman: World's Finest", "issueNumber": "32"})
        self.assertEqual(forms, ["Batman Superman Worlds Finest 032", "Batman Superman Worlds Finest 32", "Batman Superman Worlds Finest"],
                         "an indexer matches words: the marks are never sent")
        self.assertEqual(app._plain_query_title("Ultimate Spider-Man"), "Ultimate Spider-Man", "a hyphen inside a name stays")
        self.assertEqual(app._plain_query_title("Once & Future"), "Once Future")
        self.assertEqual(app._plain_query_title("Batman / Superman: World's Finest", cut_at_apostrophe=True), "Batman Superman World Finest")
        self.assertEqual(app._direct_site_queries({"seriesTitle": "Batman / Superman: World's Finest", "issueNumber": "32"},
                                                "Batman / Superman: World's Finest"),
                         ["Batman Superman World Finest #32", "Batman Superman World Finest"])
        self.assertEqual(app._direct_site_queries({"seriesTitle": "Saga", "issueNumber": "3"}, "saga vol 1"), ["saga vol 1"],
                         "a query someone typed is theirs")
        self.assertEqual(app._direct_site_queries({"seriesTitle": "The Woods", "issueNumber": "21", "seriesYear": 2014}, "The Woods"),
                         ["The Woods #21", "The Woods", "The Woods 2014"],
                         "the run's year reaches a pack the title alone buries under newer posts")

    def test_a_dotted_acronym_is_one_word(self):
        # "R E B E L S" found nothing anywhere; the dotted name found the run (2026-09-29).
        self.assertEqual(app._plain_query_title("R.E.B.E.L.S."), "R.E.B.E.L.S.")
        self.assertEqual(app._plain_query_title("S.H.I.E.L.D.: Agents", cut_at_apostrophe=True), "S.H.I.E.L.D. Agents")
        self.assertEqual(app._plain_query_title("Mr. Miracle"), "Mr Miracle", "an abbreviation is not an acronym")
        self.assertEqual(app._prowlarr_query_forms({"seriesTitle": "R.E.B.E.L.S.", "issueNumber": "12"}),
                         ["R.E.B.E.L.S. 012", "R.E.B.E.L.S. 12", "R.E.B.E.L.S."])
        self.assertEqual(app._direct_site_queries({"seriesTitle": "R.E.B.E.L.S.", "issueNumber": "12", "seriesYear": 2009}, "R.E.B.E.L.S."),
                         ["R.E.B.E.L.S. #12", "R.E.B.E.L.S.", "R.E.B.E.L.S. 2009"])

    def test_a_half_issue_is_asked_for_as_releases_write_it(self):
        self.assertEqual(
            app._prowlarr_query_forms({"seriesTitle": "Ultimate Spider-Man", "issueNumber": "½"}),
            ["Ultimate Spider-Man 0.5", "Ultimate Spider-Man 1/2", "Ultimate Spider-Man"],
        )

    def test_a_run_with_no_issue_number_searches_the_series(self):
        self.assertEqual(
            app._prowlarr_query_forms({"seriesTitle": "Saga", "issueNumber": ""}),
            ["Saga"],
        )

    def test_nothing_is_searched_without_a_series_title(self):
        self.assertEqual(
            app._prowlarr_query_forms({"seriesTitle": "", "issueNumber": "2"}), []
        )

    def test_the_first_form_that_returns_anything_wins(self):
        """The wider forms exist for when the narrow one finds nothing."""
        store = Mock()
        store.get_acquisition_job_context.return_value = {
            "seriesTitle": "If Destruction Be Our Lot", "issueNumber": "2",
        }
        store.rejected_acquisition_releases.return_value = []
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", return_value={"url": "http://p", "apiKey": "k"}
        ), patch("app._prowlarr_search", side_effect=[[], [], [], []]) as search:
            app.search_prowlarr_releases(7)
        self.assertEqual(
            [call.args[1] for call in search.call_args_list],
            [
                "If Destruction Be Our Lot 002",
                "If Destruction Be Our Lot 2",
                "If Destruction Be Our Lot 02",
                "If Destruction Be Our Lot",
            ],
        )

    def test_a_query_typed_by_a_person_is_used_as_given(self):
        store = Mock()
        store.get_acquisition_job_context.return_value = {
            "seriesTitle": "If Destruction Be Our Lot", "issueNumber": "2",
        }
        store.rejected_acquisition_releases.return_value = []
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", return_value={"url": "http://p", "apiKey": "k"}
        ), patch("app._prowlarr_search", return_value=[]) as search:
            app.search_prowlarr_releases(7, "destruction lot 02")
        search.assert_called_once()
        self.assertEqual(search.call_args.args[1], "destruction lot 02")


class ImportFailureHealsItselfTests(unittest.TestCase):
    """A download of the wrong comic takes the next release on its own.

    Recording the bad release made a later search skip it, but nothing ran
    that search: the issue sat failed until someone noticed and asked. SABnzbd
    proving a release unusable has always fallen through to the next
    candidate; a download that finishes and turns out to be the wrong comic
    is the same situation one step later.
    """

    DOWNLOAD = {
        "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
        "release_title": "Thor-The.Deviants.Saga.001", "release_key": "deviants-key",
        "sab_storage": "/downloads/complete/comics/x",
    }

    def _reconcile(self, error, store):
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/x",
            },
        ), patch("app.import_downloaded_comic", side_effect=error):
            return app.reconcile_acquisition_download(dict(self.DOWNLOAD))

    def test_the_next_release_is_grabbed_without_being_asked(self):
        store = Mock()
        store.record_acquisition_release_failure.return_value = {"failureCount": 1}
        with patch("app.search_prowlarr_releases", return_value={
            "candidates": [{"id": "next-candidate", "title": "Saga 001 (2012)"}],
        }) as search, patch("app.send_release_to_sabnzbd", return_value={
            "status": "grabbed", "queueIds": ["new-queue-id"],
        }) as send:
            result = self._reconcile(
                app.DownloadContentMismatch("No downloaded comic confidently matched Saga #1"),
                store,
            )
        store.record_acquisition_release_failure.assert_called_once()
        search.assert_called_once_with(7)
        send.assert_called_once_with(7, "next-candidate")
        self.assertEqual(result["status"], "fallback_queued")

    def test_it_stops_rather_than_working_through_every_release(self):
        """The same cap that bounds a download failure bounds this one."""
        store = Mock()
        store.record_acquisition_release_failure.return_value = {"failureCount": 3}
        with patch("app.search_prowlarr_releases") as search, patch(
            "app.send_release_to_sabnzbd"
        ) as send:
            result = self._reconcile(
                app.DownloadContentMismatch("wrong comic again"), store
            )
        search.assert_not_called()
        send.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("stopped after 3 failed releases", result["error"])

    def test_a_local_failure_still_just_stops(self):
        """A read-only disk is not fixed by downloading something else."""
        store = Mock()
        with patch("app.search_prowlarr_releases") as search:
            result = self._reconcile(OSError("Read-only file system"), store)
        search.assert_not_called()
        store.record_acquisition_release_failure.assert_not_called()
        self.assertEqual(result["status"], "failed")


class WrongDownloadIsNotOfferedAgainTests(unittest.TestCase):
    """A release whose download held the wrong comic must not come back.

    Retrying the import of a download that is the wrong comic can only fail
    the same way, and searching again would rank the same release first. The
    failure says something about the release, not about this machine.
    """

    def _reconcile_with_import_failure(self, error):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id",
            "release_title": "Thor-The.Deviants.Saga.001", "release_key": "deviants-key",
            "sab_storage": "/downloads/complete/comics/x",
        }
        store = Mock()
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed",
                "storage": "/downloads/complete/comics/x",
            },
        ), patch("app.import_downloaded_comic", side_effect=error):
            app.reconcile_acquisition_download(download)
        return store

    def test_a_download_of_the_wrong_comic_is_recorded_against_the_release(self):
        store = self._reconcile_with_import_failure(
            app.DownloadContentMismatch("No downloaded comic confidently matched Saga #1")
        )
        store.record_acquisition_release_failure.assert_called_once()
        self.assertEqual(store.record_acquisition_release_failure.call_args.args[0], 7)
        self.assertEqual(
            store.record_acquisition_release_failure.call_args.args[1], "deviants-key"
        )
        # Its files are deleted, so it must not be offered as an import to retry.
        failed = next(
            call for call in store.update_acquisition_download.call_args_list
            if call.args[1] == "failed"
        )
        self.assertEqual(failed.kwargs["failure_stage"], "content")

    def _reconcile_missing_from_history(self, sent_seconds_ago, queue_slot):
        sent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=sent_seconds_ago)
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "gone-id", "release_title": "American.Vampire.019",
            "release_key": "av-19", "created_at": sent.isoformat(),
        }
        store = Mock()
        with patch("app.catalog_store", return_value=store), \
                patch("app._sab_history_slot", return_value=None), \
                patch("app._sab_queue_slot", return_value=queue_slot) as queue, \
                patch("app._fallback_after_sab_failure", return_value={"status": "fallback_queued"}) as fallback:
            result = app.reconcile_acquisition_download(download)
        return result, store, queue, fallback

    def test_a_usenet_subject_is_read_whichever_quotes_survived(self):
        """American Vampire #21's only posting was thrown away at 40 points."""
        context = {
            "seriesTitle": "American Vampire", "seriesYear": 2010, "publisher": "DC Comics",
            "issueNumber": "21", "publicationYear": 2011, "publicationDate": "2011-12-14",
            "format": "comic", "preferredLanguage": "en",
        }
        score, reasons = app._release_candidate_score({"title": (
            'Gold Line" releases (2013.01.25) - "American Vampire 021 (2012) (Digital) (Zone-Empire)'
        )}, context)
        self.assertGreaterEqual(score, 85, reasons)
        self.assertIn("Series title matches", reasons)
        # The release group "21A1" is a name, not issue 21.
        _score, reasons = app._release_candidate_score(
            {"title": "DC.Comics.American.Vampire.Book.One.2024.HYBRID.COMIC.eBook-21A1"}, context)
        self.assertNotIn("Issue #21 matches", reasons)
        # A part counter after a well-quoted name must not become the series.
        self.assertTrue(app._release_series_matches(
            '2011.12.14 [3/9] - "American Vampire 021 (2012) (Digital) (Zone-Empire).cbr" yEnc (1/21)',
            "American Vampire", "21"))
        for title in ("American Vampire 021 (2012) (Digital) (Zone-Empire)",
                      "American.Vampire.Vol.1.No.21.Jan.2012.SCAN.Comic.eBook-iNTENSiTY",
                      "American Vampire #21a (2012)"):
            self.assertTrue(app._release_series_matches(title, "American Vampire", "21"), title)
            self.assertTrue(app._release_issue_matches(title, "21") or "21a" in title, title)

    def test_a_scene_name_marks_its_issue_with_no(self):
        """Every copy of American Vampire #19 was refused as the wrong comic."""
        cases = {
            "American.Vampire.Vol.1.No.19.Nov.2011.SCAN.Comic.eBook-iNTENSiTY.pdf":
                ("American Vampire", "19", None, 2011),
            "Image.Comics.If.Destruction.Be.Our.Lot.No.04.2026.HYBRID.COMIC.eBook-21A1.cbz":
                ("Image Comics If Destruction Be Our Lot", "4", None, 2026),
            "Fables.Vol.1.No.129.Jul.2013.SCAN.Comic.eBook-iNTENSiTY.cbr":
                ("Fables", "129", None, 2013),
            # Not every "No" is an issue marker.
            "Batman - No Man's Land 001 (1999).cbz": ("Batman No Man's Land", "1", None, 1999),
            "Saga Vol 2 (2013).cbz": ("Saga", None, 2, 2013),
        }
        for name, (title, issue, volume, year) in cases.items():
            parsed = app.parse_filename(Path("/downloads") / name)
            self.assertEqual((parsed.title, parsed.issue, parsed.volume, parsed.year),
                             (title, issue, volume, year), name)

    def test_removing_a_sab_job_deletes_its_files_but_never_the_category(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "comics"
            job = root / "American.Vampire.Vol.1.No.19"
            job.mkdir(parents=True)
            (job / "issue.pdf").write_bytes(b"pdf")
            neighbour = root / "Saga.001"
            neighbour.mkdir()
            with patch("app.SAB_COMPLETE_ROOT", root), \
                    patch("app._enabled_acquisition_service", return_value={"url": "http://sab", "apiKey": "k"}), \
                    patch("app.fetch_json_with_headers", return_value={"status": True}) as sab:
                app._sab_remove_job({"sab_nzo_id": "SAB_1",
                                     "sab_storage": "/host/data/complete/comics/American.Vampire.Vol.1.No.19"})
                self.assertIn("archive=0", sab.call_args.args[0], "deleted from SABnzbd, not archived")
                # A storage path naming the category itself, or nothing, removes nothing.
                app._sab_remove_job({"sab_nzo_id": "SAB_2", "sab_storage": "/host/data/complete/comics"})
                app._sab_remove_job({"sab_nzo_id": "SAB_3", "sab_storage": ""})
            self.assertFalse(job.exists())
            self.assertTrue(neighbour.exists())
            self.assertTrue(root.exists())

    def test_a_download_sabnzbd_has_forgotten_moves_on_to_the_next_release(self):
        """American Vampire #19 sat on Downloading for good after its files were deleted."""
        result, store, _queue, fallback = self._reconcile_missing_from_history(3600, None)
        self.assertEqual(result["status"], "fallback_queued")
        store.update_acquisition_download.assert_called_once_with(
            31, "failed", error="SABnzbd no longer has this download", failure_stage="download",
        )
        fallback.assert_called_once()

    def test_sabnzbd_refusing_the_api_key_is_not_every_download_lost(self):
        # Gate 3 (2026-10-05): a 200 with {"status": false, "error": ...} read
        # as an empty queue and history, so every download looked forgotten.
        download = {"id": 31, "job_id": 7, "sab_nzo_id": "gone-id", "release_title": "Saga.001.2012",
                    "created_at": "2026-09-01T00:00:00+00:00"}
        store = Mock()
        refused = {"status": False, "error": "API Key Incorrect"}
        with patch("app.catalog_store", return_value=store), \
                patch("app._enabled_acquisition_service", return_value={"url": "http://sab", "apiKey": "wrong"}), \
                patch("app.fetch_json_with_headers", return_value=refused), \
                patch("app._fallback_after_sab_failure") as fallback:
            with self.assertRaisesRegex(app.DownloadClientUnanswered, "API Key Incorrect"):
                reconcile_acquisition_download(download)
        store.update_acquisition_download.assert_not_called()
        fallback.assert_not_called()
        self.assertTrue(issubclass(app.DownloadClientUnanswered, app.SERVICE_HICCUPS), "the import worker waits it out")

    def test_a_download_still_in_the_queue_or_just_sent_keeps_waiting(self):
        result, store, _queue, fallback = self._reconcile_missing_from_history(3600, {"nzo_id": "gone-id"})
        self.assertEqual(result["status"], "downloading")
        fallback.assert_not_called()
        result, store, queue, fallback = self._reconcile_missing_from_history(30, None)
        self.assertEqual(result["status"], "downloading")
        queue.assert_not_called()
        fallback.assert_not_called()

    def test_a_refused_download_is_kept_as_evidence_and_only_proof_bars_it(self):
        """Supergirl: Woman of Tomorrow #2's files were deleted the moment it was refused."""
        with patch("app._sab_remove_job") as remove, patch(
            "app._fallback_after_sab_failure", return_value={"status": "fallback_queued"}
        ) as fallback:
            store = self._reconcile_with_import_failure(
                app.DownloadContentMismatch("Could not tell whether this is Saga #1", kind="unidentified")
            )
        remove.assert_not_called()
        self.assertEqual(fallback.call_args.kwargs,
                         {"kind": "unidentified", "storage": "/downloads/complete/comics/x"})
        failed = next(call for call in store.update_acquisition_download.call_args_list
                      if call.args[1] == "failed")
        self.assertEqual(failed.kwargs["failure_stage"], "unidentified")

    def test_a_hand_taken_download_that_fails_is_not_replaced_or_recorded(self):
        download = {
            "id": 31, "job_id": 7, "sab_nzo_id": "queue-id", "taken_by_hand": 1,
            "release_title": "009-Flashpoint -Secret Seven 01", "release_key": "by-hand-key",
            "sab_storage": "/downloads/complete/comics/x",
        }
        store = Mock()
        with patch("app.catalog_store", return_value=store), patch(
            "app._sab_history_slot", return_value={
                "nzo_id": "queue-id", "status": "Completed", "storage": "/downloads/complete/comics/x",
            },
        ), patch("app.import_downloaded_comic", side_effect=app.DownloadContentMismatch(
            "None of the 2 files names Flashpoint: Secret Seven #1: a.cbr, b.cbr\nFiles read:\n- a.cbr", kind="unidentified",
        )), patch("app._fallback_after_sab_failure") as fallback:
            result = app.reconcile_acquisition_download(download)
        fallback.assert_not_called()
        store.record_acquisition_release_failure.assert_not_called()
        self.assertEqual((result["status"], result["automaticFallback"], result["takenByHand"]), ("failed", False, True))
        self.assertEqual(store.update_acquisition_job.call_args.args,
                         (7, "failed", "Taken by hand, but None of the 2 files names Flashpoint: Secret Seven #1: a.cbr, b.cbr"))
        failed = next(call for call in store.update_acquisition_download.call_args_list if call.args[1] == "failed")
        self.assertEqual(failed.kwargs["failure_stage"], "unidentified")

    def test_a_local_failure_is_not_blamed_on_the_release(self):
        """A disk or quarantine problem says nothing about the download."""
        store = self._reconcile_with_import_failure(
            OSError("Read-only file system")
        )
        store.record_acquisition_release_failure.assert_not_called()


class ReleaseLanguageTests(unittest.TestCase):
    """A foreign edition must be refused, not filed under the issue it claims.

    The French "DC Saga" anthology imported as Saga #2 and #3 and sat in the
    library looking correct. Nothing about the catalogue said otherwise; the
    only way to find it was to open the file.
    """

    STATED = [
        ("Comics.FR.-.DC.Saga.(Urban.Comics).-.002 (July, 2012) (cbz)", "fr"),
        ("Batman 001 (2016) (Digital) (English)", "en"),
        ("Batman 001 (2016) (Spanish) (Digital)", "es"),
        ("Batman 001 ITA (2016)", "it"),
    ]
    UNSTATED = [
        "Saga 006 (2012) (Digital) (Zone-Empire)",
        "Saga.068.2024.Digital.Zone-Empire",
        "Fables.095.(2010).(Digital).(NahgaEmpire)",
        "Fables.Vol.1.No.129.Jul.2013.SCAN.Comic.eBook-iNTENSiTY",
        "Grab Bag 2013.05.01 Fables 031 (2005) (Digital) (Nahga-Empire)",
        '2022.04.27 [46/80] - yEnc "Saga 058 (2022) (Digital) (Zone-Empire).cbr"',
        # A dual-language edition is not a wrong one.
        "Batman 001 (English) (French) (2016)",
        # "It" is a word long before it is Italian.
        "Let It Bleed 003 (2019) (Digital)",
    ]

    def test_a_stated_language_is_read(self):
        for title, expected in self.STATED:
            with self.subTest(title=title):
                self.assertEqual(app.detect_release_language(title), expected)

    def test_silence_is_not_a_guess(self):
        """Most releases say nothing, and inferring would refuse far too much."""
        for title in self.UNSTATED:
            with self.subTest(title=title):
                self.assertIsNone(app.detect_release_language(title))

    def test_a_foreign_release_cannot_reach_a_strong_match(self):
        score, reasons = app._release_candidate_score(
            {"title": "Comics.FR.-.DC.Saga.(Urban.Comics).-.002 (July, 2012) (cbz)",
             "categories": [{"id": "7030"}]},
            {"seriesTitle": "DC Saga", "issueNumber": "2", "preferredLanguage": "en"},
        )
        self.assertEqual(score, 0)
        self.assertIn("French", reasons[0])

    def test_the_wanted_language_is_not_penalised(self):
        self.assertIsNone(
            app.release_language_conflicts("Batman 001 (2016) (English)", "en")
        )

    def test_no_preference_accepts_any_language(self):
        self.assertIsNone(
            app.release_language_conflicts("Comics.FR.-.DC.Saga.-.002", "")
        )

    def test_a_reader_of_french_gets_french(self):
        self.assertIsNone(
            app.release_language_conflicts("Comics.FR.-.DC.Saga.-.002", "fr")
        )
        self.assertEqual(
            app.release_language_conflicts("Batman 001 (2016) (English)", "fr"), "en"
        )


class DownloadProgressTests(unittest.TestCase):
    """Progress is asked of SABnzbd while someone is watching, not stored."""

    QUEUE = {"queue": {"paused": False, "slots": [
        {"nzo_id": "SAB-1", "percentage": "42", "sizeleft": "58 MB",
         "timeleft": "0:01:12", "status": "Downloading"},
        {"nzo_id": "SAB-other", "percentage": "9"},
    ]}}

    def _progress(self, pending, queue=None, sab=True):
        store = Mock()
        store.pending_acquisition_downloads.return_value = pending
        service = (lambda *_a, **_k: {"url": "http://sab", "apiKey": "k"}) if sab else Mock(
            side_effect=ValueError("not configured"))
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", side_effect=service if sab else service
        ), patch("app.fetch_json_with_headers", return_value=queue or self.QUEUE):
            return app.acquisition_download_progress()

    def test_a_downloading_job_reports_how_far_along_it_is(self):
        result = self._progress([
            {"job_id": 7, "sab_nzo_id": "SAB-1", "status": "downloading"},
        ])
        self.assertEqual(result["downloads"]["7"]["percent"], 42)
        self.assertEqual(result["downloads"]["7"]["timeLeft"], "0:01:12")
        self.assertEqual(result["downloads"]["7"]["sizeLeft"], "58 MB")

    def test_a_job_past_downloading_is_not_asked_about(self):
        """Importing and imported are not queue states; SABnzbd is done with them."""
        result = self._progress([
            {"job_id": 7, "sab_nzo_id": "SAB-1", "status": "importing"},
        ])
        self.assertEqual(result["downloads"], {})

    def test_nothing_downloading_asks_sabnzbd_nothing(self):
        store = Mock()
        store.pending_acquisition_downloads.return_value = []
        with patch("app.catalog_store", return_value=store), patch(
            "app.fetch_json_with_headers"
        ) as fetch:
            self.assertEqual(app.acquisition_download_progress()["downloads"], {})
        fetch.assert_not_called()

    def test_a_download_client_that_cannot_answer_shows_no_progress(self):
        """The page must load whether or not SABnzbd is reachable."""
        store = Mock()
        store.pending_acquisition_downloads.return_value = [
            {"job_id": 7, "sab_nzo_id": "SAB-1", "status": "downloading"},
        ]
        with patch("app.catalog_store", return_value=store), patch(
            "app._enabled_acquisition_service", return_value={"url": "http://sab", "apiKey": "k"}
        ), patch("app.fetch_json_with_headers", side_effect=urllib.error.URLError("down")):
            self.assertEqual(app.acquisition_download_progress()["downloads"], {})

    def test_a_percentage_outside_the_range_is_clamped(self):
        result = self._progress(
            [{"job_id": 7, "sab_nzo_id": "SAB-1", "status": "downloading"}],
            queue={"queue": {"slots": [{"nzo_id": "SAB-1", "percentage": "137"}]}},
        )
        self.assertEqual(result["downloads"]["7"]["percent"], 100)

    def test_an_unreadable_percentage_is_zero_rather_than_an_error(self):
        result = self._progress(
            [{"job_id": 7, "sab_nzo_id": "SAB-1", "status": "downloading"}],
            queue={"queue": {"slots": [{"nzo_id": "SAB-1", "percentage": "n/a"}]}},
        )
        self.assertEqual(result["downloads"]["7"]["percent"], 0)


class EveryWayOfFollowingSearchesTests(unittest.TestCase):
    """Following a run must look for its issues however it was followed.

    Discover creates its request through a different path than following a run
    already in the library, and only the latter started the search. A series
    added from Discover sat with fifty queued issues and never looked for one
    of them.

    This reads the source rather than the behaviour on purpose: what went
    wrong was a call site nobody had connected, and a new one is exactly what
    this has to catch.
    """

    def test_every_request_creation_starts_a_search(self):
        lines = pathlib.Path("app.py").read_text().split("\n")
        unhooked = []
        for index, line in enumerate(lines):
            if "create_acquisition_request(" not in line:
                continue
            if line.lstrip().startswith(("def ", "#")):
                continue
            window = "\n".join(lines[index:index + 10])
            if "_start_automatic_release_grabs(" not in window:
                unhooked.append(f"app.py:{index + 1}: {line.strip()[:70]}")
        self.assertEqual(
            unhooked, [],
            "these create a request without starting the search for its issues",
        )


class UserCoverLocationTests(unittest.TestCase):
    """Uploaded covers have to land somewhere the container can write.

    They were kept next to app.py, inside the image. The container runs
    with a read-only root and mounts only config, comics and downloads, so
    the mkdir failed and every upload came back 422. The feature had never
    worked once in a deployed container, and no test covered it.
    """

    def test_user_covers_follow_the_configured_database(self):
        # Sets its own environment, so it does not depend on whether some
        # other test module imported app first.
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ", {"FLIPPARR_DATABASE": str(Path(temp_dir) / "flipparr.db")},
        ):
            self.assertEqual(
                app.user_cover_dir(), Path(temp_dir) / "user-covers" / "files",
            )

    def test_a_file_and_a_run_with_the_same_id_do_not_collide(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ", {"FLIPPARR_DATABASE": str(Path(temp_dir) / "flipparr.db")},
        ):
            self.assertNotEqual(app.user_cover_dir("files"), app.user_cover_dir("series"))

    def test_covers_from_the_old_location_are_carried_over(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ", {"FLIPPARR_DATABASE": str(Path(temp_dir) / "flipparr.db")},
        ):
            legacy = Path(temp_dir) / "legacy"
            legacy.mkdir()
            (legacy / "7.jpg").write_bytes(b"cover")
            with patch("app._LEGACY_USER_COVER_DIR", legacy):
                covers = app.user_cover_dir()
            self.assertEqual((covers / "7.jpg").read_bytes(), b"cover")
            # The originals are never removed.
            self.assertTrue((legacy / "7.jpg").exists())

    def test_carrying_over_never_overwrites_what_is_already_there(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ", {"FLIPPARR_DATABASE": str(Path(temp_dir) / "flipparr.db")},
        ):
            legacy = Path(temp_dir) / "legacy"
            legacy.mkdir()
            (legacy / "7.jpg").write_bytes(b"old")
            current = Path(temp_dir) / "user-covers" / "files"
            current.mkdir(parents=True)
            (current / "7.jpg").write_bytes(b"new")
            with patch("app._LEGACY_USER_COVER_DIR", legacy):
                app.user_cover_dir()
            self.assertEqual((current / "7.jpg").read_bytes(), b"new")

    def test_an_unreadable_old_location_is_not_an_error(self):
        """A read-only root makes the old directory unreadable, not absent."""
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ", {"FLIPPARR_DATABASE": str(Path(temp_dir) / "flipparr.db")},
        ):
            with patch("app._LEGACY_USER_COVER_DIR") as legacy:
                legacy.is_dir.side_effect = OSError("Read-only file system")
                self.assertEqual(
                    app.user_cover_dir(), Path(temp_dir) / "user-covers" / "files",
                )


class ArchiveReadingTests(unittest.TestCase):
    """Comic archives are not all zips.

    RAR is about a quarter of a real library. Reading only zips meant those
    files had no cover and, worse, no ComicInfo at all -- so nothing about
    them was known beyond their filename, and a comic in the wrong language
    could never be recognised as one.
    """

    COMIC_INFO = (
        b"<?xml version='1.0'?><ComicInfo>"
        b"<Series>Example</Series><Number>3</Number>"
        b"<LanguageISO>fr</LanguageISO><Publisher>Example Press</Publisher>"
        b"</ComicInfo>"
    )

    def _png(self):
        import struct, zlib
        def chunk(tag, data):
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
        raw = b"".join(b"\x00" + bytes([90, 140, 200]) * 4 for _ in range(4))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    def _tar_comic(self, folder, name="Example 003.cbt"):
        """A tar-backed comic, read through the same external tool RAR uses."""
        import tarfile, io
        path = Path(folder) / name
        with tarfile.open(path, "w") as archive:
            for member, payload in (
                ("ComicInfo.xml", self.COMIC_INFO),
                ("pages/001.png", self._png()),
                ("pages/002.png", self._png()),
            ):
                info = tarfile.TarInfo(member)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        return path

    def _zip_comic(self, folder, name="Example 003.cbz"):
        path = Path(folder) / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("ComicInfo.xml", self.COMIC_INFO)
            archive.writestr("pages/001.png", self._png())
        return path

    # ---- what the file actually is ---------------------------------------

    def test_the_container_is_read_from_the_file_not_the_name(self):
        cases = {
            b"PK\x03\x04rest": "zip",
            b"Rar!\x1a\x07\x00rest": "rar",
            b"Rar!\x1a\x07\x01\x00rest": "rar5",
            b"7z\xbc\xaf\x27\x1crest": "7z",
            b"not an archive at all": None,
        }
        with tempfile.TemporaryDirectory() as folder:
            for index, (head, expected) in enumerate(cases.items()):
                path = Path(folder) / f"probe{index}.cbz"
                path.write_bytes(head + b"\0" * 300)
                self.assertEqual(app.archive_kind(path), expected, head[:6])

    def test_a_cbr_that_is_really_a_zip_is_read_as_one(self):
        """Mislabelled extensions are common, and cost nothing to handle."""
        with tempfile.TemporaryDirectory() as folder:
            path = self._zip_comic(folder, name="Example 003.cbr")
            self.assertEqual(app.archive_kind(path), "zip")
            self.assertEqual(app.read_embedded_metadata(path)["language"], "fr")

    # ---- the external path, which RAR also takes --------------------------

    def test_a_non_zip_comic_yields_its_embedded_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._tar_comic(folder)
            self.assertEqual(app.archive_kind(path), "tar")
            embedded = app.read_embedded_metadata(path)
            self.assertEqual(embedded["series"], "Example")
            self.assertEqual(embedded["number"], "3")
            # The field the wrong-language check reads.
            self.assertEqual(embedded["language"], "fr")

    def test_a_non_zip_comic_yields_a_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._tar_comic(folder)
            member = app.find_archive_cover_member(path)
            self.assertEqual(member, "pages/001.png")
            self.assertTrue(app.render_file_cover_thumbnail(path, member).startswith(b"\xff\xd8"))

    def test_members_are_listed_without_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            names = app.archive_member_names(self._tar_comic(folder))
            self.assertIn("pages/001.png", names)
            self.assertFalse([n for n in names if n.endswith("/")])

    # ---- refusals and degradation ----------------------------------------

    def test_a_member_named_like_an_option_is_refused(self):
        """It would reach the archive tool as a flag rather than a name."""
        with tempfile.TemporaryDirectory() as folder:
            path = self._tar_comic(folder)
            with self.assertRaises(ValueError):
                app.read_archive_member(path, "--one-file-system")

    def test_without_the_tool_a_non_zip_comic_is_skipped_not_fatal(self):
        """A developer checkout has no bsdtar; that must not break scanning."""
        with tempfile.TemporaryDirectory() as folder:
            path = self._tar_comic(folder)
            with patch("app._external_archive_tool", return_value=None):
                self.assertEqual(app.read_embedded_metadata(path), {})
                self.assertIsNone(app.find_archive_cover_member(path))

    def test_a_zip_comic_never_needs_the_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._zip_comic(folder)
            with patch("app._external_archive_tool", return_value=None):
                self.assertEqual(app.read_embedded_metadata(path)["series"], "Example")
                self.assertEqual(app.find_archive_cover_member(path), "pages/001.png")

    def test_a_file_that_is_not_an_archive_reports_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Example 003.cbr"
            path.write_bytes(b"this is not an archive")
            self.assertEqual(app.read_embedded_metadata(path), {})
            self.assertIsNone(app.find_archive_cover_member(path))

    def test_a_partly_damaged_archive_still_gives_up_what_it_can(self):
        """Comic archives in the wild are often slightly malformed.

        bsdtar lists their pages and extracts a good first page, then exits
        non-zero over something later in the file. Judging that on the exit
        code alone left fifteen comics in a real library with no cover.
        """
        partial = (b"pages/001.png\npages/002.png\n", b"bsdtar: Bad RAR file\n", 1)
        with tempfile.TemporaryDirectory() as folder:
            path = self._tar_comic(folder)
            with patch("app._run_tool_bounded", return_value=partial):
                self.assertEqual(
                    app.archive_member_names(path), ["pages/001.png", "pages/002.png"],
                )

    def test_an_archive_that_gives_nothing_back_is_an_error(self):
        empty = (b"", b"bsdtar: Unrecognized archive format\n", 1)
        with tempfile.TemporaryDirectory() as folder:
            path = self._tar_comic(folder)
            with patch("app._run_tool_bounded", return_value=empty):
                with self.assertRaises(ValueError):
                    app.read_archive_member(path, "pages/001.png")

    @unittest.skipUnless(app.shutil.which("bsdtar"), "needs bsdtar, as the image has")
    def test_an_entry_that_inflates_past_the_limit_is_cut_off_not_read_whole(self):
        """Review (2026-10-06): the tool's whole output was in memory before
        the limit was looked at; a page that decompressed to gigabytes took
        it all."""
        with tempfile.TemporaryDirectory() as folder:
            big = Path(folder) / "big.bin"
            with big.open("wb") as handle:
                for _ in range(24):
                    handle.write(b"\0" * (1024 * 1024))
            archive = Path(folder) / "bomb.cbt"
            import subprocess
            subprocess.run(["bsdtar", "-cf", str(archive), "-C", folder, "big.bin"], check=True)
            with self.assertRaisesRegex(ValueError, "larger than the limit"):
                app.read_archive_member(archive, "big.bin", limit=4 * 1024 * 1024)
            self.assertEqual(len(app.read_archive_member(archive, "big.bin", limit=32 * 1024 * 1024)), 24 * 1024 * 1024)


class VariantTaggedReleaseTests(unittest.TestCase):
    """A variant tag between the issue number and the year hid the number.

    Terminal 7 and 10 downloaded correctly, twice each, and were refused at
    import nine times: with no issue number parsed the file could not be
    matched to the issue it was grabbed for. The releases were right; the
    parser could not read them.
    """

    def parse(self, name):
        return parse_filename(Path("/library") / name)

    def test_a_variant_tag_before_the_year_does_not_hide_the_issue(self):
        for name, expected in (
            ("Terminal 007 (Blind Bag) (2026) (Image) (c2c) (Ignatz-DCP).cbz", "7"),
            ("Terminal 10 (Ultra-Rare Advance Copy) ( 2026).cbz", "10"),
            ("Example 012 (Foil) (Second Print) (2021).cbz", "12"),
            ("Example 4 [Director's Cut] (2019).cbz", "4"),
        ):
            with self.subTest(name=name):
                parsed = self.parse(name)
                self.assertEqual(parsed.issue, expected)
                self.assertEqual(parsed.title, "Terminal" if "Terminal" in name else "Example")

    def test_padding_inside_the_year_brackets_is_tolerated(self):
        self.assertEqual(self.parse("Example 003 ( 2012 ).cbz").issue, "3")

    def test_an_unbracketed_word_still_breaks_the_anchor(self):
        """Only bracketed groups may sit between the number and the year.

        Without that the anchor would stop doing its job, and a number
        inside a title would start reading as an issue number.
        """
        self.assertIsNone(self.parse("Example 12 of 30 published 2019.cbz").issue)

    def test_a_number_with_no_year_after_it_is_not_an_issue(self):
        self.assertIsNone(self.parse("Fantastic Four Omnibus (2020).cbz").issue)
        self.assertIsNone(self.parse("Saga (2012).cbz").issue)

    def test_a_volume_is_still_not_an_issue(self):
        self.assertIsNone(self.parse("Saga vol 2 (2013).cbz").issue)
        self.assertEqual(self.parse("Saga vol 2 (2013).cbz").volume, 2)

    def test_the_releases_this_library_already_holds_still_parse(self):
        for name, title, issue in (
            ("Saga 003 (2012) (Digital) (Zone-Empire).cbz", "Saga", "3"),
            ("Fables.095.(2010).(Digital).(NahgaEmpire).cbz", "Fables", "95"),
            ("Chew 015 (2010) (D) (Kingpin-Empire.cbz", "Chew", "15"),
            ("If Destruction Be Our Lot 002 (2026).cbz", "If Destruction Be Our Lot", "2"),
            ("Absolute Batman 003 (2025).cbz", "Absolute Batman", "3"),
        ):
            with self.subTest(name=name):
                parsed = self.parse(name)
                self.assertEqual((parsed.title, parsed.issue), (title, issue))


class ReleaseSearchFormTests(unittest.TestCase):
    """Two wanted issues sat unfound while the release was on the indexer.

    The catalog keeps "The Department of Truth"; releases are named without
    the article, so the first search returned nothing. The second returned
    forty-seven of the series' other issues, and the loop stopped there --
    it took any results as an answer -- so the form that had issue 4 was
    never tried.
    """

    def forms(self, title, issue):
        return app._prowlarr_query_forms({"seriesTitle": title, "issueNumber": issue})

    def test_a_leading_article_is_also_tried_without_it(self):
        forms = self.forms("The Department of Truth", "4")
        self.assertEqual(forms[0], "The Department of Truth 004", "catalog title first")
        self.assertIn("Department of Truth 004", forms)
        self.assertIn("Department of Truth", forms)

    def test_a_title_with_no_article_is_unchanged(self):
        self.assertEqual(self.forms("Saga", "3"), ["Saga 003", "Saga 3", "Saga 03", "Saga"])

    def test_a_title_that_merely_starts_with_the_letters_is_left_alone(self):
        forms = self.forms("Thanos", "1")
        self.assertEqual(forms, ["Thanos 001", "Thanos 1", "Thanos 01", "Thanos"])

    def _release(self, title):
        """Shaped like a real Prowlarr row, so the filter cannot drop it."""
        return {
            "protocol": "usenet", "title": title,
            "downloadUrl": "https://indexer.example/nzb/1", "guid": title,
            "size": 1000, "indexer": "Test", "publishDate": "2026-01-01",
        }

    def _search_for(self, useful_form):
        searched = []

        def fake_search(_service, form):
            searched.append(form)
            return [self._release("Example 004 (2026)")] if form == useful_form else []

        store = Mock()
        store.get_acquisition_job_context.return_value = {
            "seriesTitle": "Example", "issueNumber": "4",
        }
        store.rejected_acquisition_releases.return_value = []
        with patch("app.catalog_store", return_value=store), \
             patch("app._enabled_acquisition_service", return_value={"url": "x", "apiKey": "y"}), \
             patch("app._prowlarr_search", side_effect=fake_search), \
             patch("app._release_candidate_score", return_value=(100, ["match"])), \
             patch("app._prowlarr_download_reference", return_value="ref"):
            result = app.search_prowlarr_releases(1)
        return searched, result

    def test_searching_continues_until_a_form_yields_a_candidate(self):
        """Results alone are not an answer; a usable candidate is."""
        searched, result = self._search_for("Example")
        self.assertEqual(
            searched, ["Example 004", "Example 4", "Example 04", "Example"],
            "every form must be tried until one produces a candidate",
        )
        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual(result["query"], "Example")

    def test_searching_stops_at_the_form_that_works(self):
        searched, result = self._search_for("Example 004")
        self.assertEqual(searched, ["Example 004"], "no need to look further")
        self.assertEqual(result["candidateCount"], 1)

    def test_a_form_with_results_but_no_candidate_is_not_the_answer(self):
        """The exact shape that stranded Department of Truth #4.

        The middle form returned forty-seven releases and not one of them
        was the issue, and that was taken as the search having succeeded.
        """
        searched = []

        def fake_search(_service, form):
            searched.append(form)
            if form == "Example 4":
                return [self._release(f"Example {n:03d} (2026)") for n in (1, 2, 3)]
            if form == "Example":
                return [self._release("Example 004 (2026)")]
            return []

        def score(release, _context):
            # Only the wanted issue scores; siblings are below the bar.
            return (100, ["match"]) if "004" in release["title"] else (10, [])

        store = Mock()
        store.get_acquisition_job_context.return_value = {
            "seriesTitle": "Example", "issueNumber": "4",
        }
        store.rejected_acquisition_releases.return_value = []
        with patch("app.catalog_store", return_value=store), \
             patch("app._enabled_acquisition_service", return_value={"url": "x", "apiKey": "y"}), \
             patch("app._prowlarr_search", side_effect=fake_search), \
             patch("app._release_candidate_score", side_effect=score), \
             patch("app._prowlarr_download_reference", return_value="ref"):
            result = app.search_prowlarr_releases(1)
        self.assertEqual(searched, ["Example 004", "Example 4", "Example 04", "Example"])
        self.assertEqual(result["candidateCount"], 1)
        self.assertIn("004", result["candidates"][0]["title"])


class IssueCountIsNotAnIssueNumberTests(unittest.TestCase):
    """"of 04" says how long the run is, not which issue this is.

    A release named with underscores has no brackets left by the time the
    name is read, so "Lot 02 of 04 2026" put a bare 04 next to the year
    looking exactly like an issue number -- and the search then believed
    two copies of issue 2 were issue 4.
    """

    def issue(self, name):
        return parse_filename(Path("/library") / f"{name}.cbz").issue

    def test_the_issue_is_read_and_the_count_is_not(self):
        self.assertEqual(self.issue("If_Destruction_Be_Our_Lot_02__of_04___2026_"), "2")
        self.assertEqual(self.issue("If Destruction Be Our Lot 02 (of 04) (2026)"), "2")
        self.assertEqual(self.issue("Some Comic 03 of 12 2024"), "3")

    def test_a_count_with_no_issue_before_it_is_not_taken_as_one(self):
        """Nothing here says which issue it is, so nothing should be claimed."""
        self.assertIsNone(self.issue("Some Comic of 04 2024"))
        self.assertIsNone(self.issue("Collected Edition of 06 (2024)"))


class ReleaseScoringTests(unittest.TestCase):
    """The right release could not score high enough to be grabbed.

    "Department of Truth 004 (2020) (Digital) (Zone-Empire)" is exactly the
    wanted issue and scored 50 against a bar of 85, because the catalog
    keeps the article the release drops and the series match is an equality.
    Nothing that release could have said would have got it downloaded.
    """

    def score(self, title, series, issue, year="2026", publisher="Image"):
        return app._release_candidate_score(
            {"title": title, "categories": [{"id": "7030"}]},
            {"seriesTitle": series, "issueNumber": issue, "publicationYear": year,
             "publisher": publisher, "preferredLanguage": "en"},
        )[0]

    GRABBED = 85

    def test_the_release_that_could_never_be_grabbed_now_can(self):
        self.assertGreaterEqual(
            self.score("Department of Truth 004 (2020) (Digital) (Zone-Empire)",
                       "The Department of Truth", "4", "2020"),
            self.GRABBED,
        )

    def test_a_publisher_prefix_and_a_number_marker_do_not_hide_the_series(self):
        self.assertGreaterEqual(
            self.score("Image.Comics.If.Destruction.Be.Our.Lot.No.04.2026.HYBRID",
                       "If Destruction Be Our Lot", "4"),
            self.GRABBED,
        )

    def test_a_run_count_is_not_the_issue(self):
        """Two copies of issue 2 were being offered as issue 4."""
        for title in (
            "If Destruction Be Our Lot 02 [of 04] [2026] [Limited Series]",
            "If_Destruction_Be_Our_Lot_02__of_04___2026___Limited_Series",
        ):
            with self.subTest(title=title):
                self.assertLess(
                    self.score(title, "If Destruction Be Our Lot", "4"), self.GRABBED,
                )

    def test_the_issue_itself_is_still_matched_when_a_count_is_present(self):
        self.assertGreaterEqual(
            self.score("If Destruction Be Our Lot 02 (of 04) (2026)",
                       "If Destruction Be Our Lot", "2"),
            self.GRABBED,
        )

    def test_a_collected_volume_is_not_the_issue_of_the_same_number(self):
        """Wanting issue 4 must not fetch the trade of the whole run."""
        self.assertLess(
            self.score("Image Comics The Department Of Truth Vol 04 2022 Hybrid",
                       "The Department of Truth", "4", "2020"),
            self.GRABBED,
        )

    def test_the_series_that_started_all_this_is_still_refused(self):
        """The contamination the equality rule exists to prevent."""
        for title in (
            "Thor The Deviants Saga 004 (2012)",
            "DC Saga 004 (2012)",
            "Conan Saga 004 (2012)",
            "The Saga of Swamp Thing 004 (2012)",
        ):
            with self.subTest(title=title):
                self.assertLess(self.score(title, "Saga", "4", "2012"), self.GRABBED)

    def test_the_series_that_started_all_this_is_still_found(self):
        self.assertGreaterEqual(
            self.score("Saga 004 (2012) (Digital) (Zone-Empire)", "Saga", "4", "2012"),
            self.GRABBED,
        )

    def test_a_release_in_another_language_still_scores_nothing(self):
        self.assertEqual(
            self.score("The.Department.of.Truth.T04.Le.ministere.du.mensonge.2023.FR",
                       "The Department of Truth", "4", "2020"),
            0,
        )


class DownloadedFileMatchTests(unittest.TestCase):
    """A file grabbed as the right issue was refused once it landed.

    The release scorer had learned that scene names lead with the
    publisher and mark the number with "No"; the import matcher had not,
    so it downloaded the right comic and then could not recognise it.
    """

    def test_a_scene_named_file_is_recognised_as_its_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / (
                "Image.Comics.If.Destruction.Be.Our.Lot.No.04.2026."
                "HYBRID.COMIC.eBook-21A1-FTP.pdf"
            )
            path.write_bytes(b"%PDF-1.4\n%stub\n")
            score, detail = app._download_candidate_score(path, {
                "seriesTitle": "If Destruction Be Our Lot", "issueNumber": "4",
                "publisher": "Image",
            })
            self.assertTrue(detail["issueMatch"], "the issue number is in the name")
            self.assertGreaterEqual(
                detail["titleRatio"], 0.55,
                "the publisher prefix must not make the series unrecognisable",
            )

    def test_a_different_series_is_still_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Thor The Deviants Saga 004 (2012).cbz"
            # A real archive, so the score comes from the name and not from
            # the file failing its structural check before it is compared.
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("001.png", ArchiveReadingTests()._png())
            _, detail = app._download_candidate_score(path, {
                "seriesTitle": "Saga", "issueNumber": "4", "publisher": "Image",
            })
            self.assertLess(
                detail["titleRatio"], 0.55,
                "a longer name that merely ends in the wanted one is not it",
            )


class FixSeriesMatchTests(unittest.TestCase):
    """A run matched to the wrong publication run had no way back.

    The only picker was GCD-only and reachable only from a collection with
    no single issues, so on an ordinary series -- the case that actually
    goes wrong -- it could not be opened at all.
    """

    def _series(self, title="Saga", run_id="13"):
        return {"id": run_id, "title": title, "year": "2012", "publisher": "Image Comics"}

    def test_candidates_come_from_every_configured_provider(self):
        store = Mock()
        store.catalog.return_value = {"series": [self._series()]}
        with patch("app.catalog_store", return_value=store), \
             patch("app.discover_series", return_value={
                 "providerId": "metron", "provider": "Metron",
                 "providersChecked": ["Metron", "Comic Vine"],
                 "results": [{"title": "Saga", "providerSeriesId": "916"}],
             }) as discover:
            payload = app.series_match_candidates(13)
        discover.assert_called_once_with("Saga")
        self.assertEqual(payload["provider"], "metron")
        self.assertEqual(payload["candidates"][0]["providerSeriesId"], "916")
        self.assertEqual(payload["providersChecked"], ["Metron", "Comic Vine"])

    def test_the_search_can_be_worded_by_hand(self):
        store = Mock()
        store.catalog.return_value = {"series": [self._series()]}
        with patch("app.catalog_store", return_value=store), \
             patch("app.discover_series", return_value={"results": []}) as discover:
            app.series_match_candidates(13, "  Saga   Image  ")
        discover.assert_called_once_with("Saga Image")

    def test_an_unknown_run_is_not_found(self):
        store = Mock()
        store.catalog.return_value = {"series": []}
        with patch("app.catalog_store", return_value=store):
            with self.assertRaises(LookupError):
                app.series_match_candidates(999)

    def test_confirming_clears_the_old_match_before_applying_the_new(self):
        """Order matters: apply_issue_list only fills blanks.

        A title the wrong match wrote would otherwise survive the
        correction that exists to remove it.
        """
        calls = []
        store = Mock()
        store.catalog.return_value = {"series": [self._series()]}
        store.rebuild_series_run.side_effect = lambda *a: calls.append("rebuild") or {"cleared": 1}
        store.apply_issue_list.side_effect = lambda *a, **k: calls.append("apply") or {"status": "ok"}
        with patch("app.catalog_store", return_value=store), \
             patch("app._provider_credential", return_value="key"), \
             patch("app._provider_series_run_details", return_value={
                 "providerId": "916", "sourceUrl": "https://metron/916",
                 "entries": [{"number": "1"}], "title": "Saga",
                 "publisher": "Image Comics", "endEvidence": {"state": "unknown", "year": None},
             }):
            result = app.confirm_series_match(13, "metron", "916")
        self.assertEqual(calls, ["rebuild", "apply"])
        self.assertEqual(result["provider"], "metron")
        self.assertEqual(result["matchedPublisher"], "Image Comics")

    def test_a_run_the_provider_knows_nothing_about_is_refused(self):
        store = Mock()
        store.catalog.return_value = {"series": [self._series()]}
        with patch("app.catalog_store", return_value=store), \
             patch("app._provider_credential", return_value="key"), \
             patch("app._provider_series_run_details", return_value={
                 "providerId": "916", "sourceUrl": "", "entries": [],
                 "title": "Saga", "publisher": None, "endEvidence": {"state": "unknown", "year": None},
             }):
            with self.assertRaises(ValueError):
                app.confirm_series_match(13, "metron", "916")
        store.rebuild_series_run.assert_not_called()

    def test_gcd_still_goes_through_its_own_confirmation(self):
        with patch("app.confirm_gcd_series_run", return_value={"status": "ok"}) as gcd:
            app.confirm_series_match(13, "gcd", "55")
        gcd.assert_called_once_with(13, "55")

    def test_a_nonsense_provider_is_refused(self):
        for provider, series_id in (("nonsense", "1"), ("metron", "abc"), ("metron", "")):
            with self.subTest(provider=provider, series_id=series_id):
                with self.assertRaises(ValueError):
                    app.confirm_series_match(13, provider, series_id)


class ReleaseCalendarTests(unittest.TestCase):
    """Discover's two shelves: what shipped, and what is coming.

    Comics ship on Wednesday, so the shelves are labelled by that date and the
    window runs to the following Tuesday -- a title with an off-Wednesday store
    date still belongs to its week.
    """

    def week(self, day, weeks_back=0):
        return app._ship_week(dt.date.fromisoformat(day), weeks_back)

    def test_the_week_is_named_by_its_wednesday(self):
        self.assertEqual(self.week("2026-09-09")[0], dt.date(2026, 9, 9))
        self.assertEqual(self.week("2026-09-09")[1], dt.date(2026, 9, 15))
        self.assertEqual(self.week("2026-09-09", 1)[0], dt.date(2026, 9, 2))

    def test_midweek_looks_forward_to_the_next_wednesday(self):
        # Tuesday still belongs to the week that is landing tomorrow.
        self.assertEqual(self.week("2026-09-08")[0], dt.date(2026, 9, 9))
        # Thursday has had its books; the next shelf is the following week.
        self.assertEqual(self.week("2026-09-10")[0], dt.date(2026, 9, 16))
        self.assertEqual(self.week("2026-09-10", 1)[0], dt.date(2026, 9, 9))

    def test_an_issue_number_sorts_numerically(self):
        # "#2 before #10" is not what string order gives, and an annual has no
        # number to sort on at all.
        self.assertEqual(
            sorted(["10", "2", "1", "Annual 1", "2.1"], key=app._issue_sort_key),
            ["1", "2", "2.1", "10", "Annual 1"],
        )

    def row(self, name, number, series_id=1, year=2026):
        return {
            "id": f"{series_id}{number}", "number": number,
            "series": {"id": series_id, "name": name, "year_began": year},
            "store_date": "2026-09-02", "cover_date": "2026-11-01",
            "image": "https://example.invalid/cover.jpg",
        }

    def test_a_row_without_a_series_or_number_is_dropped(self):
        self.assertIsNone(app._release_entry({"id": 1, "series": {}, "number": "1"}))
        self.assertIsNone(app._release_entry({"id": 1, "series": {"name": "X"}, "number": ""}))
        entry = app._release_entry(self.row("Wolverine", "27"))
        self.assertEqual(entry["title"], "Wolverine #27")
        self.assertEqual(entry["seriesYear"], 2026)

    def ranked(self, rows, series=()):
        catalog = types.SimpleNamespace(catalog=lambda: {"series": list(series)})
        with patch("app.catalog_store", return_value=catalog):
            entries = [app._release_entry(row) for row in rows]
            return app._rank_releases([entry for entry in entries if entry])

    def test_runs_you_follow_lead_then_runs_you_own(self):
        rows = [
            self.row("Zzz Unknown", "4", series_id=3),
            self.row("Owned Run", "12", series_id=2),
            self.row("Followed Run", "7", series_id=1),
        ]
        order = self.ranked(rows, series=[
            {"id": "10", "title": "Followed Run", "year": "2026",
             "monitoringStatus": "monitored", "publisher": "Image"},
            {"id": "11", "title": "Owned Run", "year": "2026",
             "monitoringStatus": "cataloged", "publisher": "Boom!"},
        ])
        self.assertEqual([item["title"] for item in order],
                         ["Followed Run #7", "Owned Run #12", "Zzz Unknown #4"])
        self.assertTrue(order[0]["following"])
        self.assertTrue(order[1]["inLibrary"])
        self.assertFalse(order[1]["following"])
        self.assertEqual(order[0]["runId"], "10", "the shelf can link to the run it knows")

    def test_a_first_issue_leads_the_rest(self):
        """Metron's rows carry no publisher, so a #1 carries the weighting.

        It is also the better signal for Discover: a first issue is the thing
        someone browsing can actually start.
        """
        rows = [self.row("Aaa Ongoing", "45", series_id=1),
                self.row("Zzz Brand New", "1", series_id=2)]
        self.assertEqual([item["title"] for item in self.ranked(rows)],
                         ["Zzz Brand New #1", "Aaa Ongoing #45"])

    def test_a_year_mismatch_still_matches_on_title(self):
        # Providers and the library do not always agree on a start year, and a
        # title match is worth more than treating a followed run as unknown.
        order = self.ranked([self.row("Followed Run", "7", year=2025)], series=[
            {"id": "10", "title": "Followed Run", "year": "2026",
             "monitoringStatus": "monitored", "publisher": "Image"},
        ])
        self.assertTrue(order[0]["following"])

    def test_a_run_of_the_same_name_from_another_era_is_not_in_the_library(self):
        """"Green Lantern" (2011) on disk said the 2023 run's #39 was in the
        library, because a title alone was accepted whenever the year did
        not match. A year either side is a cover-date-versus-release drift;
        twelve years is another comic (owner, 2026-09-28)."""
        owned = [{"number": "39", "ownership": "direct"}]
        library = [{"id": "10", "title": "Green Lantern", "year": "2011", "publisher": "DC",
                    "monitoringStatus": "monitored", "issues": owned}]
        item = self.ranked([self.row("Green Lantern", "39", year=2023)], series=library)[0]
        self.assertEqual((item["inLibrary"], item["owned"], item["following"], item["runId"]),
                         (False, False, False, None))
        drift = self.ranked([self.row("Green Lantern", "39", year=2012)], series=library)[0]
        self.assertEqual((drift["inLibrary"], drift["owned"], drift["runId"]), (True, True, "10"))
        # A run the library never dated is still found by its name.
        undated = self.ranked([self.row("Green Lantern", "39", year=2023)],
                              series=[{**library[0], "year": None}])[0]
        self.assertTrue(undated["inLibrary"])
        # And a run linked at Metron is that run whatever its year says.
        linked = self.ranked([self.row("Green Lantern", "39", series_id=555, year=2023)], series=[
            {**library[0], "issueCatalog": {"provider": "metron", "providerSeriesId": "555"}}])[0]
        self.assertEqual((linked["inLibrary"], linked["runId"]), (True, "10"))

    def library(self, issues, requests=(), title="Wolverine", year="2026",
                monitoring="cataloged"):
        return types.SimpleNamespace(catalog=lambda: {
            "series": [{
                "id": "7", "title": title, "year": year, "publisher": "Marvel",
                "monitoringStatus": monitoring, "issues": issues,
            }],
            "requests": list(requests),
        })

    def shelf(self, rows, catalog):
        with patch("app.catalog_store", return_value=catalog):
            entries = [app._release_entry(row) for row in rows]
            return app._rank_releases([e for e in entries if e])

    def test_a_comic_you_already_own_is_not_offered_again(self):
        """Pulling it a second time is a second request for a file on disk.

        The card knows how to say "In library" -- pullState reads issue.owned
        -- but nothing set it. Only `following` reached the button, so a run in
        the library but not followed still read "Pull Issue".
        """
        catalog = self.library([
            {"number": "1", "ownership": "direct"},
            {"number": "2", "ownership": "unowned"},
        ])
        order = self.shelf([self.row("Wolverine", "1"), self.row("Wolverine", "2")], catalog)
        by_number = {item["number"]: item for item in order}
        self.assertTrue(by_number["1"]["owned"])
        self.assertFalse(by_number["2"]["owned"],
                         "owning #1 says nothing about the issue after it")

    def test_a_collected_copy_counts_as_owned(self):
        catalog = self.library([{"number": "1", "ownership": "collection"}])
        self.assertTrue(self.shelf([self.row("Wolverine", "1")], catalog)[0]["owned"])

    def test_an_issue_already_asked_for_reads_as_queued(self):
        catalog = self.library(
            [{"number": "3", "ownership": "unowned"}],
            requests=[{"jobs": [{"seriesId": "7", "issueNumber": "3", "status": "searching"}]}],
        )
        item = self.shelf([self.row("Wolverine", "3")], catalog)[0]
        self.assertTrue(item["queued"])
        self.assertFalse(item["owned"])

    def test_an_issue_pulled_before_it_ships_reads_as_queued(self):
        """No job exists until release, so jobs alone could not see this.

        Pulling Batman of Two Worlds a week early created the request but no
        job, and the card went on reading "Pull Issue".
        """
        catalog = self.library(
            [{"number": "1", "ownership": "unowned"}],
            requests=[{"status": "open", "jobs": [],
                       "issues": [{"seriesId": "7", "number": "1"}]}],
        )
        self.assertTrue(self.shelf([self.row("Wolverine", "1")], catalog)[0]["queued"])

    def test_a_closed_request_does_not_keep_its_issues_queued(self):
        catalog = self.library(
            [{"number": "1", "ownership": "unowned"}],
            requests=[{"status": "cancelled", "jobs": [],
                       "issues": [{"seriesId": "7", "number": "1"}]}],
        )
        self.assertFalse(self.shelf([self.row("Wolverine", "1")], catalog)[0]["queued"])

    def test_a_finished_job_does_not_keep_an_issue_queued(self):
        # Retiring a job is how a request ends. If fulfilled still counted, an
        # issue whose file was later removed could never be asked for again.
        catalog = self.library(
            [{"number": "3", "ownership": "unowned"}],
            requests=[{"jobs": [
                {"seriesId": "7", "issueNumber": "3", "status": "fulfilled"},
                {"seriesId": "7", "issueNumber": "4", "status": "cancelled"},
            ]}],
        )
        order = self.shelf([self.row("Wolverine", "3"), self.row("Wolverine", "4")], catalog)
        self.assertEqual([item["queued"] for item in order], [False, False])

    def test_leading_zeros_are_the_same_issue(self):
        """Providers write "1", "01" and "001" for the same comic."""
        catalog = self.library([{"number": "001", "ownership": "direct"}])
        self.assertTrue(self.shelf([self.row("Wolverine", "1")], catalog)[0]["owned"])

    def test_a_run_you_do_not_have_is_still_offered(self):
        catalog = self.library([{"number": "1", "ownership": "direct"}], title="Something Else")
        item = self.shelf([self.row("Wolverine", "1")], catalog)[0]
        self.assertFalse(item["owned"])
        self.assertFalse(item["queued"])
        self.assertFalse(item["inLibrary"])

    def test_metron_unconfigured_says_so_rather_than_showing_nothing(self):
        for config in ({}, {"metron": {"enabled": False, "token": "x"}},
                       {"metron": {"enabled": True}}):
            with patch("app.load_provider_config", return_value=config):
                result = app.release_calendar(dt.date(2026, 9, 9))
            self.assertFalse(result["available"])
            self.assertIn("Metron", result["reason"])

    def test_one_shelf_failing_does_not_take_the_other_with_it(self):
        calls = []

        def flaky(start, end, token):
            calls.append(start)
            if len(calls) == 1:
                raise RuntimeError("Metron is busy")
            return [app._release_entry(self.row("Wolverine", "27"))]

        catalog = types.SimpleNamespace(catalog=lambda: {"series": []})
        with patch("app.load_provider_config",
                   return_value={"metron": {"enabled": True, "token": "t"}}), \
             patch("app.fetch_release_calendar", side_effect=flaky), \
             patch("app.catalog_store", return_value=catalog):
            result = app.release_calendar(dt.date(2026, 9, 9))
        self.assertTrue(result["available"])
        self.assertIn("busy", result["latest"]["error"])
        self.assertEqual(result["latest"]["issues"], [])
        self.assertEqual(len(result["upcoming"]["issues"]), 1)
        self.assertEqual(result["upcoming"]["date"], "2026-09-09")

    def test_the_week_before_last_is_offered_too(self):
        """A comic is easy to miss by days; its shelf has already moved up."""
        weeks = []

        def fetch(start, end, token):
            weeks.append(start)
            return [app._release_entry(self.row("Wolverine", str(len(weeks))))]

        catalog = types.SimpleNamespace(catalog=lambda: {"series": []})
        with patch("app.load_provider_config",
                   return_value={"metron": {"enabled": True, "token": "t"}}), \
             patch("app.fetch_release_calendar", side_effect=fetch), \
             patch("app.catalog_store", return_value=catalog):
            result = app.release_calendar(dt.date(2026, 9, 9))
        self.assertEqual(result["upcoming"]["date"], "2026-09-09")
        self.assertEqual(result["latest"]["date"], "2026-09-02")
        self.assertEqual(result["previous"]["date"], "2026-08-26", "the week before the latest")
        self.assertEqual(len(result["previous"]["issues"]), 1)
        # Wednesday to Wednesday, so no day belongs to two shelves.
        self.assertEqual(sorted(weeks), [dt.date(2026, 8, 26), dt.date(2026, 9, 2), dt.date(2026, 9, 9)])

    def test_the_older_shelf_failing_leaves_the_others_standing(self):
        def flaky(start, end, token):
            if start == dt.date(2026, 8, 26):
                raise RuntimeError("Metron is busy")
            return [app._release_entry(self.row("Wolverine", "27"))]

        catalog = types.SimpleNamespace(catalog=lambda: {"series": []})
        with patch("app.load_provider_config",
                   return_value={"metron": {"enabled": True, "token": "t"}}), \
             patch("app.fetch_release_calendar", side_effect=flaky), \
             patch("app.catalog_store", return_value=catalog):
            result = app.release_calendar(dt.date(2026, 9, 9))
        self.assertIn("busy", result["previous"]["error"])
        self.assertEqual(len(result["latest"]["issues"]), 1)
        self.assertEqual(len(result["upcoming"]["issues"]), 1)

    def test_every_page_is_followed_and_a_loop_cannot_hang_it(self):
        pages = [
            {"results": [self.row("A", "1")], "next": "https://metron.invalid/2"},
            {"results": [self.row("B", "2")], "next": None},
        ]
        with patch("app.fetch_provider_json", side_effect=pages):
            entries = app.fetch_release_calendar(dt.date(2026, 9, 2), dt.date(2026, 9, 8), "t")
        self.assertEqual([entry["number"] for entry in entries], ["1", "2"])

        looping = {"results": [self.row("A", "1")], "next": "same"}
        with patch("app.fetch_provider_json", return_value=looping) as fetch:
            app.fetch_release_calendar(dt.date(2026, 9, 2), dt.date(2026, 9, 8), "t")
        self.assertLessEqual(fetch.call_count, 20, "a self-referential next must not spin")


class DiscoverDrawerTests(unittest.TestCase):
    """Tap a cover, see the run, choose how much of it to take.

    The preview must not import anything -- only a pull does -- and a pull of
    chosen issues must cover exactly those, without following the run.
    """

    def library(self, issues=(), requests=(), monitoring="cataloged"):
        return {
            "series": [{
                "id": "7", "title": "Wolverine", "year": "2026", "publisher": "Marvel",
                "monitoringStatus": monitoring, "issues": list(issues),
            }],
            "requests": list(requests),
        }

    def fetched(self):
        return {
            "providerId": "9", "sourceUrl": "https://metron.invalid/series/9/",
            "title": "Wolverine", "year": 2026, "publisher": "Marvel",
            "endEvidence": {"state": "ongoing"},
            "entries": [
                {"number": "1", "title": "Hunt", "publication_date": "2020-01-01",
                 "publication_year": 2020, "cover": "https://c.invalid/1.jpg"},
                {"number": "2", "publication_date": "2020-02-01", "publication_year": 2020},
                {"number": "3", "publication_date": "2099-01-01", "publication_year": 2099},
            ],
        }

    def preview(self, catalog, **patches):
        store = Mock()
        store.catalog.return_value = catalog
        with patch("app.catalog_store", return_value=store), \
             patch("app._provider_credential", return_value="t"), \
             patch("app._provider_series_run_details", return_value=self.fetched()):
            return app.preview_discovered_run({"metron": "9"}, "Wolverine"), store

    def test_the_preview_lists_issues_with_what_the_library_knows(self):
        catalog = self.library(
            issues=[{"number": "1", "ownership": "direct"}],
            requests=[{"status": "open", "jobs": [],
                       "issues": [{"seriesId": "7", "number": "2"}]}],
        )
        run, _ = self.preview(catalog)
        self.assertEqual(run["provider"], "metron")
        self.assertEqual(run["providerSeriesId"], "9")
        self.assertEqual(run["publicationStatus"], "ongoing")
        self.assertEqual(run["cover"], "https://c.invalid/1.jpg")
        by_number = {issue["number"]: issue for issue in run["issues"]}
        self.assertTrue(by_number["1"]["owned"])
        self.assertTrue(by_number["2"]["queued"])
        self.assertEqual(by_number["3"]["releaseState"], "upcoming")
        self.assertEqual(by_number["1"]["releaseState"], "released")

    def test_the_preview_writes_nothing(self):
        """Looking is not pulling. Importing waits for a pull."""
        _, store = self.preview(self.library())
        store.ensure_provider_series_run.assert_not_called()
        store.apply_issue_list.assert_not_called()
        store.create_acquisition_request.assert_not_called()

    def test_a_followed_run_reads_as_already_on_its_way(self):
        run, _ = self.preview(self.library(monitoring="monitored"))
        self.assertTrue(all(issue["queued"] for issue in run["issues"]))
        self.assertTrue(run["following"])

    def test_the_preview_falls_through_a_provider_that_is_not_there(self):
        def credential(provider):
            if provider == "metron":
                raise ValueError("Metron is not configured")
            return "k"
        store = Mock()
        store.catalog.return_value = self.library()
        with patch("app.catalog_store", return_value=store), \
             patch("app._provider_credential", side_effect=credential), \
             patch("app._provider_series_run_details", return_value=self.fetched()):
            run = app.preview_discovered_run({"metron": "9", "comic_vine": "88"}, "Wolverine")
        self.assertEqual(run["provider"], "comic_vine")
        self.assertEqual(run["fallbacks"][0]["provider"], "Metron")

    def test_a_gcd_preview_is_one_request_and_numbers_only(self):
        """Filling each issue in is a request per issue against GCD's limit."""
        series = {
            "name": "Wolverine", "year_began": 2026, "year_ended": None, "publisher": "Marvel",
            "active_issues": ["https://www.comics.org/api/issue/101/",
                              "https://www.comics.org/api/issue/102/"],
            "issue_descriptors": ["1", "2"],
        }
        store = Mock()
        store.catalog.return_value = self.library()
        with patch("app.catalog_store", return_value=store), \
             patch("app.fetch_gcd_json", return_value=series) as fetch:
            run = app.preview_discovered_run({"gcd": "55"}, "Wolverine")
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(run["detailsLimited"])
        self.assertEqual(len(run["issues"]), 2)
        self.assertIsNone(run["issues"][0]["title"])

    def pull(self, catalog=None, **kwargs):
        store = Mock()
        store.catalog.return_value = catalog or {"series": []}
        store.create_acquisition_request.return_value = {"id": "r1"}
        with patch("app.catalog_store", return_value=store), \
             patch("app._import_provider_run",
                   return_value=({"id": 5}, {"publisher": "P", "year": 2026})) as imported, \
             patch("app._import_gcd_run",
                   return_value=({"id": 6}, {"publisher": "P", "year": 2026})) as imported_gcd, \
             patch("app._start_automatic_release_grabs"):
            result = app.pull_discovered_issues(**kwargs)
        return result, store, imported, imported_gcd

    def test_chosen_issues_are_the_only_ones_requested(self):
        result, store, _, _ = self.pull(provider="metron", provider_series_id="9",
                                        numbers=["3", "1", "3"])
        args, kwargs = store.create_acquisition_request.call_args
        self.assertEqual(kwargs["issue_numbers"], ["3", "1"])
        self.assertEqual(args[2], "issues")
        self.assertEqual(result["numbers"], ["3", "1"])

    def test_all_released_takes_released_issues_you_do_not_have(self):
        catalog = {"series": [{"id": "5", "issues": [
            {"number": "1", "releaseState": "released", "ownership": "direct"},
            {"number": "2", "releaseState": "released", "ownership": "unowned"},
            {"number": "3", "releaseState": "upcoming", "ownership": "unowned"},
            {"number": "4", "releaseState": "unknown", "ownership": "unowned"},
        ]}]}
        _, store, _, _ = self.pull(catalog, provider="metron", provider_series_id="9",
                                   released=True)
        self.assertEqual(store.create_acquisition_request.call_args.kwargs["issue_numbers"], ["2"])

    def test_all_released_with_nothing_left_says_so(self):
        catalog = {"series": [{"id": "5", "issues": [
            {"number": "1", "releaseState": "released", "ownership": "direct"},
        ]}]}
        with self.assertRaises(ValueError):
            self.pull(catalog, provider="metron", provider_series_id="9", released=True)

    def test_choosing_nothing_is_refused_before_anything_is_imported(self):
        store = Mock()
        with patch("app.catalog_store", return_value=store), \
             patch("app._import_provider_run") as imported:
            with self.assertRaises(ValueError):
                app.pull_discovered_issues("metron", "9", [])
        imported.assert_not_called()

    def test_a_gcd_run_can_be_pulled_in_part(self):
        """GCD could only be followed before; importing lived inside following."""
        _, _, imported, imported_gcd = self.pull(provider="gcd", provider_series_id="55",
                                                 numbers=["2"], query="Wolverine")
        imported_gcd.assert_called_once_with("Wolverine", "55")
        imported.assert_not_called()

    def test_the_shelf_pull_is_still_one_issue(self):
        store = Mock()
        store.create_acquisition_request.return_value = {"id": "r1"}
        with patch("app.catalog_store", return_value=store), \
             patch("app._import_provider_run", return_value=({"id": 5}, {})), \
             patch("app._start_automatic_release_grabs"):
            result = app.pull_discovered_issue("metron", "9", "27")
        self.assertEqual(result["number"], "27")
        self.assertEqual(store.create_acquisition_request.call_args.kwargs["issue_numbers"], ["27"])

    def test_issue_details_come_back_readable(self):
        row = {
            "desc": "<p>Logan &amp; the <b>hunt</b></p>", "page": 32, "price": "4.99",
            "name": ["The Hunt"], "cover_date": "2026-11-01", "store_date": "2026-09-16",
            "credits": [{"creator": "Saladin Ahmed", "role": [{"name": "Writer"}]},
                        {"creator": "Somebody", "role": [{"name": "Editor"}]}],
        }
        with patch("app._provider_credential", return_value="t"), \
             patch("app.fetch_provider_json", return_value=row):
            detail = app.discovered_issue_detail("123")
        self.assertEqual(detail["description"], "Logan & the hunt")
        self.assertEqual(detail["creators"], [{"name": "Saladin Ahmed", "roles": ["Writer"]}])
        self.assertEqual(detail["pageCount"], 32)
        self.assertEqual(detail["storyTitles"], ["The Hunt"])
        self.assertEqual(detail["storeDate"], "2026-09-16")

    def test_a_bad_issue_id_is_refused(self):
        with self.assertRaises(ValueError):
            app.discovered_issue_detail("../etc")


class RunSynopsisTests(unittest.TestCase):
    """What a run is about, in its catalog's own words."""

    def test_metron_desc_arrives_as_plain_text(self):
        detail = {"name": "Absolute Batman", "year_began": 2024, "publisher": {"name": "DC"},
                  "desc": "<p>Without the mansion&hellip; <b>the Dark Knight</b>!</p>"}
        with patch("app._metron_issue_entries", return_value=("9", "https://m.invalid/9/", [], None)), \
             patch("app.fetch_provider_json", return_value=detail):
            run = app._provider_series_run_details("metron", "9", "Absolute Batman", "t")
        self.assertEqual(run["synopsis"], "Without the mansion… the Dark Knight!")

    def test_comic_vine_prefers_its_deck(self):
        self.assertEqual(
            app._comic_vine_synopsis({"deck": "A short one.", "description": "<p>Long</p>"}),
            "A short one.")

    def test_comic_vine_falls_back_to_the_lead_paragraph(self):
        """The description is a wiki page; only its opening says what the story is."""
        volume = {"deck": None, "description":
                  "<h2>Overview</h2><p>Bruce <i>Wayne</i> returns.</p><p>Issue list</p>"}
        self.assertEqual(app._comic_vine_synopsis(volume), "Bruce Wayne returns.")
        self.assertIsNone(app._comic_vine_synopsis({}))

    def test_the_run_drawer_carries_it(self):
        with patch("app._library_relevance", return_value={}):
            run = app._shape_run_preview(
                "metron", {"title": "X", "entries": [], "synopsis": "A story."}, [])
        self.assertEqual(run["synopsis"], "A story.")

    def synopsis(self, ids, responses):
        store = Mock()
        store.confirmed_series_provider_ids.return_value = ids

        def fetch(provider, url, credential):
            value = responses[provider]
            if isinstance(value, Exception):
                raise value
            return value
        with patch("app.catalog_store", return_value=store), \
             patch("app._provider_credential", return_value="t"), \
             patch("app.fetch_provider_json", side_effect=fetch):
            return app.series_synopsis(7)

    def test_a_library_run_reads_metron_first(self):
        result = self.synopsis({"metron": "9", "comic_vine": "5"}, {
            "metron": {"desc": "From Metron."}, "comic_vine": {"results": {"deck": "From CV."}}})
        self.assertEqual((result["synopsis"], result["provider"]), ("From Metron.", "metron"))

    def test_it_falls_through_to_comic_vine(self):
        for metron in ({"desc": ""}, RuntimeError("Metron is down")):
            with self.subTest(metron=metron):
                result = self.synopsis({"metron": "9", "comic_vine": "5"}, {
                    "metron": metron, "comic_vine": {"results": {"deck": "From CV."}}})
                self.assertEqual((result["synopsis"], result["provider"]), ("From CV.", "comic_vine"))

    def test_no_source_is_no_synopsis_not_an_error(self):
        """GCD has publication notes, not a story, so it is not asked."""
        self.assertIsNone(self.synopsis({"gcd": "3"}, {})["synopsis"])

    def test_a_format_label_is_not_a_story(self):
        """All of these came back from the live library, under "Story"."""
        for text in ("Ongoing series.", "A one-shot.", "A 7 issue mini-series.", "Digital Exclusive.",
                     "<p>Seven issue mini-series.</p>", " ",
                     'Note: Indicia list title as "Geiger (2024)". We use "Geiger".'):
            with self.subTest(text=text):
                self.assertIsNone(app._synopsis_text(text))
        story = "A one-shot where Batman meets another Batman."
        self.assertEqual(app._synopsis_text(story), story)

    def test_a_label_falls_through_to_the_next_source(self):
        result = self.synopsis({"metron": "9", "comic_vine": "5"}, {
            "metron": {"desc": "A 7 issue mini-series."},
            "comic_vine": {"results": {"deck": " ", "description":
                                       "<p>Ongoing series.</p><h4>Collected Editions</h4>"}}})
        self.assertIsNone(result["synopsis"])


class IssueDetailTests(unittest.TestCase):
    """What one of our own issues is about, for the screen you see when you
    finish the one before it. The blurb is decoration: nothing here may turn a
    provider's bad day into an error the reader has to read."""

    def detail(self, ids, responses, credential="t"):
        store = Mock()
        store.issue_provider_ids.return_value = ids

        def fetch(provider, url, credential):
            value = responses[provider]
            if isinstance(value, Exception):
                raise value
            return value
        with patch("app.catalog_store", return_value=store), \
             patch("app._provider_credential", side_effect=(
                 credential if isinstance(credential, Exception) else lambda _p: credential)), \
             patch("app.fetch_provider_json", side_effect=fetch):
            return app.issue_detail(41)

    def test_metron_answers_with_the_whole_issue(self):
        result = self.detail({"metron": "9", "comic_vine": "5"}, {
            "metron": {"name": ["The Gauntlet"], "desc": "<p>Batman <b>runs</b>.</p>",
                       "page": 22, "price": "3.99", "cover_date": "2024-01-01"}})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["description"], "Batman runs.")
        self.assertEqual(result["storyTitles"], ["The Gauntlet"])
        self.assertEqual((result["provider"], result["pageCount"]), ("metron", 22))
        self.assertNotIn("providerIssueId", result, "the shape is the same whoever answered")

    def test_comic_vine_covers_the_issues_metron_does_not(self):
        """130 of the 132 issues with no Metron id have a Comic Vine one."""
        for metron in ({}, {"metron": "9"}):
            with self.subTest(metron=metron):
                result = self.detail({**metron, "comic_vine": "5"}, {
                    "metron": {"desc": ""}, "comic_vine": {"results": {"deck": "From CV."}}})
                self.assertEqual(
                    (result["status"], result["description"], result["provider"]),
                    ("ok", "From CV.", "comic_vine"))

    def test_an_issue_no_catalog_knows_is_not_asked_about(self):
        with patch("app.catalog_store") as store, \
             patch("app.discovered_issue_detail") as metron, \
             patch("app.fetch_provider_json") as fetch:
            store.return_value.issue_provider_ids.return_value = {"gcd": "3"}
            result = app.issue_detail(41)
        self.assertEqual(result["status"], "unmatched")
        self.assertIsNone(result["description"])
        metron.assert_not_called()
        fetch.assert_not_called()

    def test_a_catalog_with_nothing_to_say_is_not_a_failure(self):
        result = self.detail({"metron": "9"}, {"metron": {"desc": ""}})
        self.assertEqual((result["status"], result["description"]), ("ok", None))

    def test_a_provider_that_breaks_says_so_without_raising(self):
        for responses in ({"metron": RuntimeError("Metron is down")},
                          {"metron": {"desc": ""}, "comic_vine": TimeoutError("slow")}):
            with self.subTest(responses=responses):
                ids = {"metron": "9", "comic_vine": "5"} if len(responses) > 1 else {"metron": "9"}
                result = self.detail(ids, responses)
                self.assertEqual((result["status"], result["description"]), ("unavailable", None))

    def test_an_unconfigured_provider_is_the_same_as_a_broken_one(self):
        """`_provider_credential` raises ValueError, which a route would turn
        into a 400 about a request the reader never made."""
        result = self.detail({"metron": "9"}, {},
                             credential=ValueError("Metron is not configured"))
        self.assertEqual(result["status"], "unavailable")


class PagePanelTests(unittest.TestCase):
    """Where a page's panels are: read once from the render, kept, ordered the way the run reads."""

    def _page_png(self, gridded=True, bridged=False):
        """A readable page, a solid one, or -- `bridged` -- two panels whose
        gutter a caption runs across, which the cut cannot read and a model can."""
        import io
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (300, 450), "white")
        draw = ImageDraw.Draw(image)
        if bridged:
            for box in ((20, 20, 280, 210), (20, 240, 280, 430)):
                draw.rectangle(box, fill="#444444", outline="black", width=3)
            draw.rectangle((100, 190, 200, 260), fill="#444444")
        elif gridded:
            for box in ((20, 20, 140, 210), (160, 20, 280, 210), (20, 240, 280, 430)):
                draw.rectangle(box, fill="#444444", outline="black", width=3)
        else:
            draw.rectangle((0, 0, 299, 449), fill="#444444")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _spread_png(self):
        """Two readable pages side by side: a spread, as the renders give it."""
        import io
        from PIL import Image
        with Image.open(io.BytesIO(self._page_png())) as page:
            spread = Image.new("RGB", (page.width * 2, page.height), "white")
            spread.paste(page, (0, 0))
            spread.paste(page, (page.width, 0))
        buffer = io.BytesIO()
        spread.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_a_spread_is_read_as_its_two_pages_in_order(self):
        result, store, render = self._ask(png=self._spread_png())
        self.assertEqual([call.args for call in render.call_args_list], [(7, 1), (7, 1, "backdrop")])
        self.assertTrue(result["segmented"])
        self.assertEqual(len(result["panels"]), 6)
        self.assertTrue(all(panel["x"] + panel["w"] <= 0.5 for panel in result["panels"][:3]), "the left page first")
        self.assertTrue(all(panel["x"] >= 0.5 for panel in result["panels"][3:]), "then the right")
        self.assertTrue(store.set_page_panels.call_args[0][5])
        manga, _, _ = self._ask(png=self._spread_png(), direction="rtl")
        self.assertTrue(all(panel["x"] >= 0.5 for panel in manga["panels"][:3]), "a manga spread starts on the right page")

    def _ask(self, record=None, direction="ltr", png=None, index=1):
        from pathlib import Path
        store = Mock()
        store.library_file_path.return_value = Path("/library/x.cbz")
        store.page_panels.return_value = record
        store.file_reading_direction.return_value = direction
        with patch("app.catalog_store", return_value=store), \
             patch("app.archive_kind", return_value="zip"), \
             patch("app.cached_page_members", return_value=["a.jpg", "b.jpg"]), \
             patch("app._file_signature", return_value="sig"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("app.render_file_page", return_value=png or self._page_png()) as render:
            store.manual_page_panels_near.return_value = []
            return app.file_page_panels(7, index), store, render

    def test_the_first_look_reads_the_render_and_keeps_what_it_found(self):
        result, store, render = self._ask()
        render.assert_called_once_with(7, 1)
        (file_id, member, signature, source, panels, segmented), _ = store.set_page_panels.call_args
        self.assertEqual((file_id, member, signature, source, segmented, len(panels)), (7, "b.jpg", "sig", "auto", True, 3))
        self.assertEqual([panel["id"] for panel in result["panels"]], ["1-0", "1-1", "1-2"])
        self.assertLess(result["panels"][0]["x"], result["panels"][1]["x"], "left before right")
        self.assertEqual((result["page"], result["source"], result["readingDirection"]), (1, "auto", "ltr"))

    def test_a_kept_reading_is_not_read_again(self):
        kept = {"fileSignature": "sig", "source": "auto", "segmented": True,
                "panels": [{"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}]}
        result, store, render = self._ask(record=kept)
        render.assert_not_called()
        store.set_page_panels.assert_not_called()
        self.assertEqual([panel["x"] for panel in result["panels"]], [0.0, 0.5], "ordered on the way out")

    def test_an_automatic_reading_is_redone_when_the_file_changed(self):
        stale = {"fileSignature": "old", "source": "auto", "segmented": False, "panels": []}
        _, _, render = self._ask(record=stale)
        render.assert_called_once()

    def test_a_persons_reading_stands_whatever_happened_to_the_file(self):
        corrected = {"fileSignature": "old", "source": "manual", "segmented": True,
                     "panels": [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5}]}
        result, _, render = self._ask(record=corrected)
        render.assert_not_called()
        self.assertEqual(result["source"], "manual")

    def test_a_page_that_could_not_be_read_says_so(self):
        result, store, _ = self._ask(png=self._page_png(gridded=False))
        self.assertEqual((result["segmented"], result["panels"]), (False, []))
        self.assertFalse(store.set_page_panels.call_args[0][5], "and that is what is kept")

    def test_a_manga_page_orders_its_panels_right_to_left(self):
        result, _, _ = self._ask(direction="rtl")
        self.assertGreater(result["panels"][0]["x"], result["panels"][1]["x"])
        self.assertEqual(result["readingDirection"], "rtl")

    def test_a_page_the_comic_does_not_have_is_not_found(self):
        with self.assertRaises(LookupError):
            self._ask(index=5)


class ManualPanelTests(unittest.TestCase):
    """A person's panels for a page: kept in their order, standing until forgotten."""

    def _saving(self, payload, index=1, record=None):
        from pathlib import Path
        store = Mock()
        store.library_file_path.return_value = Path("/library/x.cbz")
        store.page_panels.return_value = record
        store.file_reading_direction.return_value = "ltr"
        with patch("app.catalog_store", return_value=store), \
             patch("app.archive_kind", return_value="zip"), \
             patch("app.cached_page_members", return_value=["a.jpg", "b.jpg"]), \
             patch("app._file_signature", return_value="sig"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("app.file_page_panels", return_value={"ok": True}) as answer:
            result = app.save_page_panels(7, index, payload)
        return result, store, answer

    def test_rectangles_are_kept_as_manual_in_the_order_sent(self):
        result, store, answer = self._saving({"panels": [
            {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5}, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
        ]})
        self.assertEqual(result, {"ok": True})
        answer.assert_called_once_with(7, 1)
        args = store.set_page_panels.call_args[0]
        self.assertEqual(args[:4], (7, "b.jpg", "sig", "manual"))
        self.assertEqual([panel["order"] for panel in args[4]], [0, 1], "the order sent, not the row rule's")
        self.assertEqual(args[4][0]["x"], 0.5)
        self.assertIs(args[5], True)

    def test_no_rectangles_is_the_whole_page(self):
        _, store, _ = self._saving({"panels": []})
        self.assertEqual(store.set_page_panels.call_args[0][4], [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "order": 0}])

    def test_a_rectangle_off_the_page_or_too_thin_is_refused(self):
        for bad in ([{"x": 0.8, "y": 0, "w": 0.5, "h": 0.5}], [{"x": 0, "y": 0, "w": 0.01, "h": 0.5}], [{"x": "a", "y": 0, "w": 1, "h": 1}], "nope", {"panels": 3}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self._saving(bad if isinstance(bad, (str, dict)) else {"panels": bad})
        with self.assertRaises(LookupError):
            self._saving({"panels": []}, index=9)

    def test_a_persons_reading_stands_when_the_file_changes_and_whatever_the_tiers_say(self):
        from pathlib import Path
        kept = {"fileSignature": "old", "source": "manual", "segmented": True,
                "panels": [{"x": 0, "y": 0, "w": 1, "h": 0.5, "order": 1}, {"x": 0, "y": 0.5, "w": 1, "h": 0.5, "order": 0}]}
        store = Mock()
        store.library_file_path.return_value = Path("/library/x.cbz")
        store.page_panels.return_value = kept
        store.file_reading_direction.return_value = "ltr"
        with patch("app.catalog_store", return_value=store), \
             patch("app.archive_kind", return_value="zip"), \
             patch("app.cached_page_members", return_value=["a.jpg", "b.jpg"]), \
             patch("app._file_signature", return_value="new"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("app.panel_model_session", return_value=object()), \
             patch("app.vision_model_ready", return_value=True), \
             patch("app.load_app_settings", return_value={"visionReadsEveryPage": True}), \
             patch("app.render_file_page") as render:
            result = app.file_page_panels(7, 1)
        render.assert_not_called()
        self.assertEqual(result["source"], "manual")
        self.assertEqual([panel["y"] for panel in result["panels"]], [0.5, 0], "in the person's order")

    def test_forgetting_drops_the_row_and_reads_afresh(self):
        from pathlib import Path
        store = Mock()
        store.library_file_path.return_value = Path("/library/x.cbz")
        with patch("app.catalog_store", return_value=store), \
             patch("app.archive_kind", return_value="zip"), \
             patch("app.cached_page_members", return_value=["a.jpg", "b.jpg"]), \
             patch.object(Path, "is_file", return_value=True), \
             patch("app.file_page_panels", return_value={"fresh": True}) as answer:
            self.assertEqual(app.forget_page_panels(7, 0), {"fresh": True})
        store.delete_page_panels.assert_called_once_with(7, "a.jpg")
        answer.assert_called_once_with(7, 0)


class VisionConnectorTests(unittest.TestCase):
    """Claude as an optional provider: a key, a switch, and only the hard pages sent."""

    def test_claude_is_a_provider_with_a_key_and_somewhere_to_get_one(self):
        definition = app.PROVIDER_DEFINITIONS["anthropic"]
        self.assertEqual(definition["credentialField"], "apiKey")
        self.assertTrue(definition["credentialUrl"].startswith("https://"))
        self.assertFalse(definition["defaultEnabled"], "off until a person turns it on")
        headers = app._provider_headers("anthropic", "k")
        self.assertEqual(headers["x-api-key"], "k")
        self.assertIn("anthropic-version", headers)

    def test_the_connection_test_lists_models_with_the_key(self):
        with patch("app.load_provider_config", return_value={"anthropic": {"enabled": True, "apiKey": "k"}}), \
             patch("app.fetch_provider_json", return_value={"data": [{"id": "claude"}]}) as fetch:
            result = app.test_provider_connection("anthropic", {})
        self.assertEqual(result["status"], "connected")
        self.assertIn("/models", fetch.call_args[0][1])

    def test_chatgpt_is_the_second_connector_behind_the_same_seam(self):
        definition = app.PROVIDER_DEFINITIONS["openai"]
        self.assertEqual(definition["credentialField"], "apiKey")
        self.assertFalse(definition["defaultEnabled"])
        self.assertEqual(app._provider_headers("openai", "k")["Authorization"], "Bearer k")
        with patch("app.load_provider_config", return_value={"openai": {"enabled": True, "apiKey": "k"}}), \
             patch("app.fetch_provider_json", return_value={"data": [{"id": "gpt"}]}) as fetch:
            self.assertEqual(app.test_provider_connection("openai", {})["status"], "connected")
        self.assertIn("/models", fetch.call_args[0][1])

    def test_the_lower_priority_number_is_asked_when_both_are_on(self):
        both = {"anthropic": {"enabled": True, "apiKey": "a", "priority": 50},
                "openai": {"enabled": True, "apiKey": "o", "priority": 20}}
        with patch("app.load_provider_config", return_value=both):
            self.assertEqual(app.vision_provider(), "openai")
        both["anthropic"]["priority"] = 10
        with patch("app.load_provider_config", return_value=both):
            self.assertEqual(app.vision_provider(), "anthropic")
        with patch("app.load_provider_config", return_value={"openai": {"enabled": True, "apiKey": "o"}}):
            self.assertEqual(app.vision_provider(), "openai", "the only one on")

    def test_each_connector_is_spoken_to_in_its_own_shape(self):
        import io

        def opened(request, *, timeout):
            body = json.loads(request.data)
            opened.calls.append((request.full_url, dict(request.header_items()), body))
            if "anthropic" in request.full_url:
                return io.BytesIO(json.dumps({"content": [{"type": "text", "text": "[1, 2]"}]}).encode())
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "[2, 1]"}}]}).encode())
        opened.calls = []
        with patch("urllib.request.urlopen", side_effect=opened), patch("app._wait_for_provider_slot"), \
             patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {}, clear=True):
            with patch("app.load_provider_config", return_value={"anthropic": {"enabled": True, "apiKey": "a-key"}}):
                self.assertEqual(app.ask_vision_model(b"jpeg", "order?"), "[1, 2]")
            with patch("app.load_provider_config", return_value={"openai": {"enabled": True, "apiKey": "o-key"}}):
                self.assertEqual(app.ask_vision_model(b"jpeg", "order?"), "[2, 1]")
        (a_url, a_headers, a_body), (o_url, o_headers, o_body) = opened.calls
        self.assertTrue(a_url.endswith("/messages"))
        self.assertEqual(a_headers.get("X-api-key"), "a-key")
        self.assertEqual(a_body["messages"][0]["content"][0]["source"]["media_type"], "image/jpeg")
        self.assertTrue(o_url.endswith("/chat/completions"))
        self.assertEqual(o_headers.get("Authorization"), "Bearer o-key")
        self.assertTrue(o_body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(o_body["messages"][0]["content"][1]["text"], "order?")

    def test_ready_means_on_and_keyed_and_never_raises(self):
        with patch("app.load_provider_config", return_value={"anthropic": {"enabled": True, "apiKey": "k"}}):
            self.assertTrue(app.vision_model_ready())
        with patch("app.load_provider_config", return_value={"anthropic": {"enabled": False, "apiKey": "k"}}):
            self.assertFalse(app.vision_model_ready())
        with patch("app.load_provider_config", return_value={}):
            self.assertFalse(app.vision_model_ready())

    def _mask(self, **kind):
        import io
        from PIL import Image
        import page_panels
        with Image.open(io.BytesIO(PagePanelTests._page_png(self, **kind))) as image:
            return page_panels.page_mask(image)

    def test_boxes_are_believed_once_the_page_has_corrected_them(self):
        mask = self._mask(bridged=True)
        # Roughly the two panels, drawn a little off; the page puts them right.
        answer = '[{"x1": 14, "y1": 26, "x2": 288, "y2": 200}, {"x1": 26, "y1": 250, "x2": 270, "y2": 440}]'
        with patch("app.ask_vision_model", return_value=answer):
            boxes = app.vision_panels(b"jpeg", 300, 450, "ltr", mask)
        self.assertEqual(len(boxes), 2)
        top, bottom = sorted(boxes, key=lambda b: b["y"])
        # The frame's ink runs 20..430 inclusive; a box's end is exclusive.
        self.assertEqual((round(top["y"] * 450), round((bottom["y"] + bottom["h"]) * 450)), (20, 431), "on the ink, not where the model said")
        self.assertTrue(200 <= round(bottom["y"] * 450) <= 260, "parted at the bridged gutter")
        # A guess: a line through the middle of the top panel.
        guess = '[{"x1": 20, "y1": 20, "x2": 280, "y2": 110}, {"x1": 20, "y1": 110, "x2": 280, "y2": 430}]'
        with patch("app.ask_vision_model", return_value=guess):
            self.assertIsNone(app.vision_panels(b"jpeg", 300, 450, "ltr", mask), "an edge on ink is an edge the model made up")
        with patch("app.ask_vision_model", return_value="[]"):
            self.assertEqual(app.vision_panels(b"jpeg", 300, 450, "ltr", mask), [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
                             "no panels is an answer: the page is one panel, shown whole")
        # The gridded page: two panels above one wide. The model lists the two
        # and forgets the wide one; the ink it left gives the panel back.
        grid = self._mask()
        short = '[{"x1": 22, "y1": 22, "x2": 138, "y2": 208}, {"x1": 162, "y1": 22, "x2": 278, "y2": 208}]'
        with patch("app.ask_vision_model", return_value=short):
            boxes = app.vision_panels(b"jpeg", 300, 450, "ltr", grid)
        self.assertEqual(len(boxes), 3, "the panel the model left out is read from the ink it left")
        self.assertGreater(boxes[2]["y"], 0.5)
        with patch("app.ask_vision_model", return_value="I cannot make out this page."):
            self.assertIsNone(app.vision_panels(b"jpeg", 300, 450, "ltr", mask), "no list is no answer")
        with patch("app.ask_vision_model", side_effect=app.VisionUnavailable("down")):
            with self.assertRaises(app.VisionUnavailable):
                app.vision_panels(b"jpeg", 300, 450, "ltr", mask)

    def test_a_rate_limits_window_is_waited_out_as_a_whole(self):
        self.assertEqual(app._duration_seconds("1m17.49s"), 77.49)
        self.assertEqual(app._duration_seconds("876ms"), 0.876)
        self.assertEqual(app._duration_seconds("3h47m45.167s"), 13665.167)
        self.assertEqual(app._duration_seconds("soon"), 0)
        self.assertEqual(app._window_reset_seconds({"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "51.833s"}), 52)
        self.assertEqual(app._window_reset_seconds({"x-ratelimit-remaining-tokens": "8166", "x-ratelimit-reset-tokens": "51.833s"}), 0, "tokens left: no wait")
        self.assertEqual(app._window_reset_seconds({}), 0)

    def _refusal(self, status, body=b"", headers=None):
        import io
        from email.message import Message
        message = Message()
        for key, value in (headers or {}).items():
            message[key] = value
        return urllib.error.HTTPError("https://api.openai.com/v1/chat/completions", status, "no", message, io.BytesIO(body))

    def test_a_refusal_is_no_answer_and_cools_the_connector_down(self):
        # Absolute Flash #1, 2026-09-22: OpenAI answered 429 "credit balance
        # exhausted" to every page, the 429 branch crashed on a missing
        # argument, the crash was swallowed as "no answer", and three pages
        # were stamped as looked at by a model that never saw them.
        keyed = {"openai": {"enabled": True, "apiKey": "o-key"}}
        quota = b'{"error": {"message": "You have no credits remaining.", "type": "insufficient_quota", "code": "credit_balance_exhausted"}}'
        with patch("app.load_provider_config", return_value=keyed), patch("app._wait_for_provider_slot"), \
             patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {}, clear=True):
            with patch("urllib.request.urlopen", side_effect=self._refusal(429, quota)), patch("app.log_event") as log:
                with self.assertRaises(app.VisionUnavailable) as raised:
                    app.ask_vision_model(b"jpeg", "boxes?")
            self.assertIn("credit_balance_exhausted", str(raised.exception))
            self.assertEqual(log.call_args.kwargs.get("code"), "credit_balance_exhausted")
            held = app._PROVIDER_NEXT_REQUEST_AT["openai"] - time.monotonic()
            self.assertGreater(held, app.VISION_QUOTA_COOLDOWN_SECONDS - 5, "an empty balance is left alone for a while")
            self.assertFalse(app.vision_model_ready(), "not ready while cooling down: the reader must not wait it out")
            with patch("app.log_event"):
                self.assertFalse(app.vision_model_ready())
        # Anthropic says the same thing as a 400 with a generic type; only the
        # message tells (the owner's account at zero, 2026-10-05).
        claude = {"anthropic": {"enabled": True, "apiKey": "a-key"}}
        empty = (b'{"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low '
                 b'to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."}}')
        with patch("app.load_provider_config", return_value=claude), patch("app._wait_for_provider_slot"), \
             patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {}, clear=True):
            with patch("urllib.request.urlopen", side_effect=self._refusal(400, empty)), patch("app.log_event") as log:
                with self.assertRaises(app.VisionUnavailable):
                    app.ask_vision_model(b"jpeg", "boxes?")
            self.assertEqual(log.call_args.kwargs.get("code"), "credit_balance_exhausted")
            self.assertIn("credit balance is too low", log.call_args.kwargs.get("detail"))
            held = app._PROVIDER_NEXT_REQUEST_AT["anthropic"] - time.monotonic()
            self.assertGreater(held, app.VISION_QUOTA_COOLDOWN_SECONDS - 5, "left alone, not asked again on every page")
            with patch("urllib.request.urlopen", side_effect=self._refusal(400, b'{"error": {"type": "invalid_request_error", "message": "image too large"}}')), \
                    patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {}, clear=True), patch("app.log_event") as log:
                with self.assertRaises(app.VisionUnavailable):
                    app.ask_vision_model(b"jpeg", "boxes?")
                self.assertEqual(log.call_args.kwargs.get("code"), "invalid_request_error", "another refusal stays what it is")
                self.assertNotIn("anthropic", app._PROVIDER_NEXT_REQUEST_AT)
        with patch("app.load_provider_config", return_value=keyed), patch("app._wait_for_provider_slot"), \
             patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {}, clear=True):
            with patch("urllib.request.urlopen", side_effect=self._refusal(429, b"{}", {"Retry-After": "7"})), patch("app.log_event"):
                with self.assertRaises(app.VisionUnavailable):
                    app.ask_vision_model(b"jpeg", "boxes?")
            held = app._PROVIDER_NEXT_REQUEST_AT["openai"] - time.monotonic()
            self.assertTrue(6 < held <= 7, f"a rate limit is waited out as told: {held}")
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")), patch("app.log_event"):
                app._PROVIDER_NEXT_REQUEST_AT.clear()
                with self.assertRaises(app.VisionUnavailable):
                    app.ask_vision_model(b"jpeg", "boxes?")
            self.assertTrue(app.vision_model_ready(), "an unreachable service is tried again next time")


class VisionInPanelPipelineTests(PagePanelTests):
    """Where the vision model sits in the pipeline: last, and only for the hard pages."""

    def _ask_with_vision(self, record=None, png=None, answer="[]", ready=True, every=False, fixed=()):
        from pathlib import Path
        store = Mock()
        store.library_file_path.return_value = Path("/library/x.cbz")
        store.page_panels.return_value = record
        store.file_reading_direction.return_value = "ltr"
        store.manual_page_panels_near.return_value = list(fixed)
        with patch("app.catalog_store", return_value=store), \
             patch("app.archive_kind", return_value="zip"), \
             patch("app.cached_page_members", return_value=["a.jpg", "b.jpg"]), \
             patch("app._file_signature", return_value="sig"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("app.panel_model_session", return_value=None), \
             patch("app.vision_model_ready", return_value=ready), \
             patch("app.load_app_settings", return_value={"visionReadsEveryPage": every}), \
             patch("app.render_file_page", return_value=png or self._page_png()), \
             patch("app.ask_vision_model", return_value=answer) as ask:
            return app.file_page_panels(7, 1), store, ask

    def test_a_readers_page_never_asks_the_connector_unless_the_admin_allows_it(self):
        from pathlib import Path
        store = Mock()
        store.library_file_path.return_value = Path("/library/x.cbz")
        store.page_panels.return_value = None
        store.file_reading_direction.return_value = "ltr"
        store.manual_page_panels_near.return_value = []
        with patch("app.catalog_store", return_value=store), \
             patch("app.archive_kind", return_value="zip"), \
             patch("app.cached_page_members", return_value=["a.jpg", "b.jpg"]), \
             patch("app._file_signature", return_value="sig"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("app.panel_model_session", return_value=None), \
             patch("app.vision_model_ready", return_value=True), \
             patch("app.load_app_settings", return_value={"visionReadsEveryPage": True}), \
             patch("app.render_file_page", return_value=self._page_png()), \
             patch("app.ask_vision_model", side_effect=AssertionError("the connector was asked")) as ask:
            result = app.file_page_panels(7, 1, allow_vision=False)
        ask.assert_not_called()
        self.assertEqual(result["source"], "auto", "the local finder's reading, kept for the admin to improve")

    def test_a_spread_is_asked_about_a_half_at_a_time(self):
        # Asked about the whole, a model loses a third of a spread's panels;
        # asked about each page it reads as well as on any page. Here it sees
        # no panels on either half: the two halves meet at the fold with the
        # same top and bottom, and are one image across the spread, shown whole.
        result, _, ask = self._ask_with_vision(png=self._spread_png(), every=True)
        self.assertEqual(ask.call_count, 2)
        for call in ask.call_args_list:
            self.assertIn("300 pixels wide and 450 pixels tall", call.args[1], "each half at its own size")
        self.assertEqual((result["source"], result["segmented"], len(result["panels"])), ("vlm", True, 1))
        self.assertEqual((result["panels"][0]["x"], result["panels"][0]["w"]), (0.0, 1.0))

    def test_a_readers_own_fixes_go_with_the_question(self):
        # A page of this comic a reader fixed by hand: it goes first, drawn
        # and described, and the question says to cut the page its way.
        fixed = [{"fileId": 7, "member": "a.jpg", "panels": [
            {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5, "order": 1}, {"x": 0.0, "y": 0.5, "w": 1.0, "h": 0.5, "order": 0}]}]
        _, _, ask = self._ask_with_vision(every=True, fixed=fixed)
        ask.assert_called_once()
        image, prompt, examples = ask.call_args.args
        self.assertIn("1 example page from the same comic", prompt)
        self.assertEqual(len(examples), 1)
        picture, text = examples[0]
        self.assertEqual(picture[:2], b"\xff\xd8", "a JPEG of the page")
        self.assertIn("Example 1", text)
        self.assertIn('[{"x1": 0, "y1": 225, "x2": 300, "y2": 450}, {"x1": 0, "y1": 0, "x2": 300, "y2": 225}]', text,
                      "the person's order, in the answer's own form")
        # Without any, the question stands alone, as before.
        _, _, alone = self._ask_with_vision(every=True)
        self.assertEqual(alone.call_args.args[2], [])
        self.assertNotIn("example", alone.call_args.args[1])

    def test_examples_come_first_in_either_providers_request(self):
        examples = [(b"\xff\xd8one", "Example 1")]
        _, a_body = app._vision_request("anthropic", "k", b"\xff\xd8page", "read it", examples)
        _, o_body = app._vision_request("openai", "k", b"\xff\xd8page", "read it", examples)
        a_content = json.loads(a_body)["messages"][0]["content"]
        o_content = json.loads(o_body)["messages"][0]["content"]
        self.assertEqual([block["type"] for block in a_content], ["image", "text", "image", "text"])
        self.assertEqual([block.get("text") for block in a_content if block["type"] == "text"], ["Example 1", "read it"])
        self.assertEqual([block["type"] for block in o_content], ["image_url", "text", "image_url", "text"])

    def test_nothing_beyond_the_operators_services_is_shared_until_turned_on(self):
        """Every page to a vision model, the community lists' GitHub fetch and
        visitor addresses in the log are each off until the operator says so
        (2026-10-03)."""
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "app.os.environ", {"COMICARR_SETTINGS_CONFIG": str(Path(temp_dir) / "settings.json")},
        ):
            settings = app.load_app_settings()
            self.assertEqual((settings["visionReadsEveryPage"], settings["communityListsEnabled"], settings["logClientAddresses"]),
                             (False, False, False))
            self.assertIs(app.save_app_settings({"visionReadsEveryPage": True})["visionReadsEveryPage"], True)
            self.assertIs(app.cached_setting("logClientAddresses"), False)
            app.save_app_settings({"logClientAddresses": True})
            self.assertIs(app.cached_setting("logClientAddresses"), True, "a saved change is read at once")
            with self.assertRaises(ValueError):
                app.save_app_settings({"visionReadsEveryPage": "yes"})

    def test_reading_every_page_questions_a_local_reading_and_the_page_corrects_the_answer(self):
        # The cloud page: the local finder took a patch of sky between clouds
        # for a panel and was sure. Asked anyway, the model's two rows stand,
        # exact to the ink.
        answer = '[{"x1": 14, "y1": 26, "x2": 288, "y2": 200}, {"x1": 26, "y1": 250, "x2": 270, "y2": 440}]'
        kept = {"fileSignature": "sig", "source": "model", "segmented": True,
                "panels": [{"x": 0.3, "y": 0.0, "w": 0.3, "h": 0.2}, {"x": 0.0, "y": 0.5, "w": 1.0, "h": 0.5}]}
        result, store, ask = self._ask_with_vision(record=kept, png=self._page_png(bridged=True), answer=answer, every=True)
        ask.assert_called_once()
        self.assertEqual((result["source"], result["segmented"], len(result["panels"])), ("vlm", True, 2))
        self.assertEqual(round(result["panels"][0]["y"] * 450), 20)
        # Asked, the page is not asked again, whatever the model said.
        asked = {"fileSignature": "sig", "source": "vlm", "segmented": True, "panels": result["panels"]}
        _, _, again = self._ask_with_vision(record=asked, png=self._page_png(bridged=True), answer=answer, every=True)
        again.assert_not_called()
        # And a refused answer leaves the local reading -- made afresh, not
        # copied from the row -- standing, as asked.
        result, _, _ = self._ask_with_vision(record=kept, answer="I cannot make out this page.", every=True)
        self.assertEqual((result["source"], result["segmented"], len(result["panels"])), ("vlm", True, 3))

    def test_the_fallback_policy_leaves_a_local_reading_alone(self):
        kept = {"fileSignature": "sig", "source": "model", "segmented": True,
                "panels": [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5}, {"x": 0.0, "y": 0.5, "w": 1.0, "h": 0.5}]}
        result, _, ask = self._ask_with_vision(record=kept, every=False)
        ask.assert_not_called()
        self.assertEqual(result["source"], "model")

    def test_a_page_nothing_could_read_is_sent_and_its_boxes_kept(self):
        # Two panels with a caption across their gutter: the cut cannot read
        # it, the model can, and the page puts the model's edges right.
        answer = '[{"x1": 14, "y1": 26, "x2": 288, "y2": 200}, {"x1": 26, "y1": 250, "x2": 270, "y2": 440}]'
        result, store, ask = self._ask_with_vision(png=self._page_png(bridged=True), answer=answer)
        ask.assert_called_once()
        self.assertEqual((result["source"], result["segmented"], len(result["panels"])), ("vlm", True, 2))
        self.assertEqual(store.set_page_panels.call_args[0][3], "vlm")
        self.assertEqual(round(result["panels"][0]["y"] * 450), 20, "on the ink, not where the model said")

    def test_a_page_the_model_could_not_read_either_is_not_sent_again(self):
        result, store, ask = self._ask_with_vision(png=self._page_png(gridded=False), answer="I cannot make out this page.")
        ask.assert_called_once()
        self.assertEqual((result["source"], result["segmented"]), ("vlm", False), "stamped as looked at, so it is asked once")
        kept = {"fileSignature": "sig", "source": "vlm", "segmented": False, "panels": []}
        _, _, ask_again = self._ask_with_vision(record=kept, png=self._page_png(gridded=False))
        ask_again.assert_not_called()

    def test_a_connector_that_did_not_answer_stamps_nothing(self):
        from pathlib import Path
        store = Mock()
        store.library_file_path.return_value = Path("/library/x.cbz")
        store.page_panels.return_value = None
        store.file_reading_direction.return_value = "ltr"
        store.manual_page_panels_near.return_value = []
        with patch("app.catalog_store", return_value=store), \
             patch("app.archive_kind", return_value="zip"), \
             patch("app.cached_page_members", return_value=["a.jpg", "b.jpg"]), \
             patch("app._file_signature", return_value="sig"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("app.panel_model_session", return_value=None), \
             patch("app.vision_model_ready", return_value=True), \
             patch("app.render_file_page", return_value=self._page_png(gridded=False)), \
             patch("app.ask_vision_model", side_effect=app.VisionUnavailable("out of credit")) as ask:
            result = app.file_page_panels(7, 1)
        ask.assert_called_once()
        self.assertEqual((result["source"], result["segmented"]), ("auto", False), "the lower tier's reading, under its own name")
        self.assertEqual(store.set_page_panels.call_args[0][3], "auto")
        # Kept as "auto" and unsegmented, the row is exactly what the pipeline
        # sends once the connector is ready again.
        kept = {"fileSignature": "sig", "source": "auto", "segmented": False, "panels": []}
        _, _, ask_again = self._ask_with_vision(record=kept, png=self._page_png(gridded=False), answer="[]")
        ask_again.assert_called_once()

    def test_a_page_the_model_reads_as_one_image_is_shown_whole(self):
        # Absolute Flash #1: ChatGPT rightly answered "no panels" for the
        # house ad and two splashes, and the reader quartered them anyway.
        whole = {"id": "1-0", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        for answer in ("[]", '[{"x1": 10, "y1": 10, "x2": 290, "y2": 440}]'):
            with self.subTest(answer=answer):
                result, store, ask = self._ask_with_vision(png=self._page_png(gridded=False), answer=answer)
                ask.assert_called_once()
                self.assertEqual((result["source"], result["segmented"], result["panels"]), ("vlm", True, [whole]))
                self.assertEqual(store.set_page_panels.call_args[0][3:], ("vlm", [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}], True))

    def test_a_readable_page_with_clean_rows_is_not_sent(self):
        result, _, ask = self._ask_with_vision()
        ask.assert_not_called()
        self.assertEqual(result["source"], "auto")

    def test_the_connector_off_means_nothing_is_sent(self):
        _, _, ask = self._ask_with_vision(png=self._page_png(gridded=False), ready=False)
        ask.assert_not_called()

    def test_an_unreadable_page_kept_before_the_connector_is_sent_once_it_is_on(self):
        earlier = {"fileSignature": "sig", "source": "model", "segmented": False, "panels": []}
        answer = '[{"x1": 0, "y1": 0, "x2": 300, "y2": 225}, {"x1": 0, "y1": 225, "x2": 300, "y2": 450}]'
        result, _, ask = self._ask_with_vision(record=earlier, png=self._page_png(gridded=False), answer=answer)
        ask.assert_called_once()
        self.assertEqual(result["source"], "vlm")


class ASearchCannotStallThePassTests(unittest.TestCase):
    """Ultimate Spider-Man, 134 issues: the automatic pass asked Prowlarr for
    #050, Prowlarr logged the request, and the answer never came. The pass sat
    there for seven minutes, the 45 issues after it were never searched, and
    #050 was left at "searching" -- which no pass picks up again."""

    def test_a_call_that_never_answers_is_given_up_on(self):
        release = threading.Event()
        try:
            with self.assertRaisesRegex(TimeoutError, "Prowlarr did not answer"):
                app._with_deadline(lambda: release.wait(5), 0.05, "Prowlarr")
        finally:
            release.set()

    def test_an_answer_or_an_error_comes_through_unchanged(self):
        self.assertEqual(app._with_deadline(lambda: [1], 1, "Prowlarr"), [1])
        with self.assertRaisesRegex(ValueError, "bad"):
            app._with_deadline(lambda: (_ for _ in ()).throw(ValueError("bad")), 1, "Prowlarr")

    def test_a_search_that_fails_goes_back_on_the_queue(self):
        store = Mock()
        store.get_acquisition_job_context.return_value = {"seriesTitle": "Example", "issueNumber": "50"}
        store.rejected_acquisition_releases.return_value = []
        with patch("app.catalog_store", return_value=store), \
             patch("app._enabled_acquisition_service", return_value={"url": "x", "apiKey": "y"}), \
             patch("app._prowlarr_search", side_effect=TimeoutError("Prowlarr did not answer")):
            with self.assertRaises(TimeoutError):
                app.search_prowlarr_releases(7)
        # Prowlarr's silence goes back on the queue without counting against
        # the issue (Gate 3, 2026-10-05) ...
        store.return_unanswered_search.assert_called_once()
        self.assertEqual(store.return_unanswered_search.call_args.args[0], 7)
        self.assertIn("prowlarr", app._SERVICE_TROUBLE)
        app._SERVICE_TROUBLE.clear()
        # ... while a search that failed on its own account is put back as before.
        store.reset_mock()
        with patch("app.catalog_store", return_value=store), \
             patch("app._enabled_acquisition_service", return_value={"url": "x", "apiKey": "y"}), \
             patch("app._prowlarr_search", side_effect=KeyError("title")):
            with self.assertRaises(KeyError):
                app.search_prowlarr_releases(7)
        last = store.update_acquisition_job.call_args_list[-1]
        self.assertEqual(last.args[:2], (7, "queued"))
        self.assertIn("tried again", last.args[2])
        store.return_unanswered_search.assert_not_called()

    def test_the_first_sweep_comes_soon_after_start(self):
        """A restart mid-pass otherwise left the rest for a quarter of an hour."""
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        with patch("app._enabled_acquisition_service", return_value={}), \
             patch("app._automatic_release_grabs") as sweep:
            app.release_research_worker(stop)
        self.assertEqual(stop.wait.call_args_list[0].args[0], app.RESEARCH_FIRST_SWEEP_SECONDS)
        self.assertLess(app.RESEARCH_FIRST_SWEEP_SECONDS, app.RESEARCH_POLL_SECONDS)
        sweep.assert_called_once_with(None, backoff=True)


class MangaVolumeTests(unittest.TestCase):
    """English manga comes in volumes, and the volume is the book.

    Every real title below came off the indexers. Before this, each scored
    5-40 of the 85 needed -- the volume number was erased as a collected
    edition -- while a 974 MB chapter pack scored 90 and would have won.
    """

    def context(self, title="Chainsaw Man", number="18", year=2025, publisher="Viz"):
        return {"seriesTitle": title, "issueNumber": number, "publicationYear": year,
                "publisher": publisher, "format": "manga", "preferredLanguage": "en"}

    def score(self, release_title, **kw):
        release = {"title": release_title, "categories": [{"id": 7030}]}
        return app._release_candidate_score(release, self.context(**kw))[0]

    def test_real_volume_names_score_for_their_own_volume(self):
        for title, kw in [
            ("Chainsaw Man v18 (2025) (Digital) (LuCaZ) (cbz)", {}),
            ("Chainsaw Man v18 [2025] [Digital] [LuCaZ]", {}),
            ("VIZ.Media.Chainsaw.Man.Vol.18.2025.HYBRiD.MANGA.eBook-PNLS", {}),
            ("VIZ.Media.-.Spy.X.Family.Vol.03.2020.HYBRID.MANGA.eBook-21A1",
             dict(title="Spy x Family", number="3", year=2020)),
            ("Kodansha-Blue.Lock.Vol.03.2021.HYBRID.MANGA",
             dict(title="Blue Lock", number="3", year=2021, publisher="Kodansha Comics USA")),
            ("One Piece v098 (2021) (Digital) (1r0n) (cbz)",
             dict(title="One Piece", number="98", year=2021)),
            ("One Piece v100 (2022) (Digital) (1r0n) (cbz)",
             dict(title="One Piece", number="100", year=2022)),
            ("Frieren - Beyond Journey's End v01 (2021) (Digital) (1r0n) (f2)",
             dict(title="Frieren: Beyond Journey's End", number="1", year=2021)),
        ]:
            with self.subTest(title=title):
                self.assertGreaterEqual(self.score(title, **kw), 85)

    def test_a_neighbouring_volume_does_not(self):
        self.assertLess(self.score("Chainsaw Man v17 (2025) (Digital) (LuCaZ) (cbz)"), 85)
        # The release group 21A1 is not volume 21.
        self.assertLess(
            self.score("VIZ.Media.Chainsaw.Man.Vol.18.2025.HYBRID.MANGA.eBook-21A1",
                       number="21", year=2026), 85)

    def test_packs_specials_and_other_formats_are_refused(self):
        for title, kw in [
            ("Chainsaw Man - 13 (cbz)", dict(number="13")),
            ("Chainsaw Man v01-v11 (2020-2023) (Digital)", dict(number="1")),
            ("Chainsaw Man v18 c150 (2025)", {}),
            ("One Piece - Heroines v01 (2025) (Digital) (LuCaZ) (cbz)",
             dict(title="One Piece", number="1")),
            ("Hajime Tanaka - Oshi No Ko 01 (epub)", dict(title="Oshi no Ko", number="1")),
            ("-Porn Comics- A Chance With Nami (One Piece) v01", dict(title="One Piece", number="1")),
            ("Chainsaw Man v18 (2025) (German)", {}),
            ("Chainsaw Man v18 (2025) raw", {}),
            ("Chainsaw Man v18 (2025) Jpn", {}),
        ]:
            with self.subTest(title=title):
                self.assertLess(self.score(title, **kw), 85)

    def test_a_comic_volume_is_still_not_the_issue(self):
        """Comics keep the rule manga breaks: Saga v01 collects six issues."""
        context = {"seriesTitle": "Saga", "issueNumber": "1", "publicationYear": 2012,
                   "preferredLanguage": "en"}
        release = {"title": "Saga v01 (2012) (Digital) (cbz)", "categories": [{"id": 7030}]}
        self.assertLess(app._release_candidate_score(release, context)[0], 85)

    def test_manga_is_searched_by_volume_in_both_categories(self):
        forms = app._prowlarr_query_forms(
            {"seriesTitle": "Chainsaw Man", "issueNumber": "18", "format": "manga"})
        self.assertEqual(forms[:3], ["Chainsaw Man v18", "Chainsaw Man Vol 18", "Chainsaw Man v018"])
        self.assertEqual(forms[-1], "Chainsaw Man")
        self.assertEqual(app._prowlarr_query_forms({"seriesTitle": "Saga", "issueNumber": "3"}),
                         ["Saga 003", "Saga 3", "Saga 03", "Saga"], "comics are asked for by issue")
        with patch("app.fetch_json_with_headers", return_value=[]) as fetch:
            app._prowlarr_search({"url": "http://p", "apiKey": "k"}, "Chainsaw Man v18",
                                 categories=app.MANGA_CATEGORIES)
        self.assertIn("categories=7030&categories=7020", fetch.call_args.args[0])

    def test_the_english_edition_is_told_from_the_others(self):
        for publisher in ("Viz", "VIZ Media", "Kodansha Comics USA", "Yen Press", "Seven Seas"):
            self.assertEqual(app.manga_edition(publisher), "manga", publisher)
        for publisher in ("Shueisha", "Kodansha", "Egmont Ehapa Verlag", "Norma Editorial",
                          "Pika Édition", "NXB Trẻ", "Crunchyroll SA"):
            self.assertEqual(app.manga_edition(publisher), "foreign", publisher)
        for publisher in ("Marvel", "Image", "DC Comics", None):
            self.assertIsNone(app.manga_edition(publisher), publisher)

    def test_discover_shows_only_the_english_edition(self):
        payload = {"status_code": 1, "results": [
            {"id": 117318, "name": "Chainsaw Man", "start_year": "2019", "publisher": {"name": "Shueisha"}},
            {"id": 130799, "name": "Chainsaw Man", "start_year": "2020", "publisher": {"name": "Viz"}},
            {"id": 137488, "name": "Chainsaw Man", "start_year": "2020",
             "publisher": {"name": "Egmont Ehapa Verlag"}},
        ]}
        with patch("app.fetch_provider_json", return_value=payload), \
             patch("app.preferred_language", return_value="en"):
            result = app.discover_comic_vine_series("Chainsaw Man", "k", {"keys": set()})
        self.assertEqual([(row["publisher"], row["medium"]) for row in result["results"]],
                         [("Viz", "manga")])

    def manga_job(self, **kw):
        return {"seriesTitle": "Chainsaw Man", "seriesYear": 2020, "issueNumber": "18",
                "publisher": "Viz", "format": "manga", **kw}

    def test_a_downloaded_volume_verifies_as_that_volume(self):
        clean = {"file_health": {"status": "ok"}, "lookup_identity": {}, "embedded_metadata": {}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Chainsaw Man v18 (2025) (Digital) (LuCaZ).cbz"
            path.write_bytes(b"x")
            with patch("app.inventory_file", return_value=clean):
                score, detail = app._download_candidate_score(path, self.manga_job())
                self.assertTrue(detail["issueMatch"])
                self.assertGreaterEqual(score, 180)
                self.assertLess(app._download_candidate_score(path, self.manga_job(issueNumber="17"))[0], 180)
                self.assertLess(app._download_candidate_score(path, self.manga_job(format="comic"))[0], 180,
                                "a comic still does not read v18 as issue 18")

    def test_a_viz_release_name_verifies_despite_its_tags(self):
        """The library reads the title from the name, tags and all."""
        read = {"file_health": {"status": "ok"}, "embedded_metadata": {},
                "lookup_identity": {"title": "VIZ Media Chainsaw Man HYBRiD MANGA eBook-PNLS"}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "VIZ.Media.Chainsaw.Man.Vol.01.2020.HYBRiD.MANGA.eBook-PNLS.cbz"
            path.write_bytes(b"x")
            with patch("app.inventory_file", return_value=read):
                score, detail = app._download_candidate_score(path, self.manga_job(issueNumber="1"))
                self.assertTrue(detail["issueMatch"])
                self.assertGreaterEqual(detail["titleRatio"], 0.55)
                self.assertGreaterEqual(score, 180)
                self.assertLess(app._download_candidate_score(path, self.manga_job(issueNumber="2"))[0], 180)

    def test_an_ebook_that_is_not_page_images_is_refused_so_the_next_release_is_tried(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / "job"
            source.mkdir()
            (source / "Chainsaw Man v18 (2025).epub").write_bytes(b"x")
            with self.assertRaisesRegex(app.DownloadContentMismatch, "EPUB"):
                app.select_downloaded_comic(source, self.manga_job(), root)

    def test_a_volume_is_filed_in_the_manga_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            dest = app._issue_destination(Path("x.cbz"), self.manga_job(issueTitle="All Pets"), root)
            self.assertEqual(dest.relative_to(root).as_posix(),
                             "Manga/Viz/Chainsaw Man (2020)/Chainsaw Man (2020) v18 - All Pets.cbz")
            generic = app._issue_destination(Path("x.cbz"), self.manga_job(issueTitle="Volume 18"), root)
            self.assertEqual(generic.name, "Chainsaw Man (2020) v18.cbz")
            comic = app._issue_destination(Path("x.cbz"), self.manga_job(format="comic"), root)
            self.assertEqual(comic.relative_to(root).as_posix(),
                             "Viz/Chainsaw Man (2020)/Chainsaw Man (2020) #018.cbz")

    def test_a_manga_run_never_asks_metron_by_title(self):
        """Metron answers "Berserk" with the comic, not the manga."""
        store = Mock()
        store.get_series_sync_context.return_value = {"title": "Berserk", "format": "manga"}
        store.metadata_provider_available.return_value = True
        with patch("app.catalog_store", return_value=store), \
             patch("app._series_enrichment_provider_order",
                   return_value=[("metron", {"token": "t"}), ("comic_vine", {"apiKey": "k"})]), \
             patch("app._metron_issue_entries") as metron, \
             patch("app._comic_vine_issue_entries", return_value=("9", "u", [])):
            result = app.enrich_catalog_series(7)
        metron.assert_not_called()
        self.assertEqual(result["provider"], "comic_vine")

    def test_a_comic_archive_goes_before_an_ebook_post(self):
        """The first live pull grabbed Viz's HYBRID eBook posts over LuCaZ's CBZ."""
        cbz = self.score("Chainsaw Man v01 (2020) (Digital) (LuCaZ) (cbz)", number="1", year=2020)
        ebook = self.score("VIZ.Media.Chainsaw.Man.Vol.01.2020.HYBRiD.MANGA.eBook-PNLS",
                           number="1", year=2020)
        self.assertGreater(cbz, ebook)
        self.assertGreaterEqual(ebook, 85, "still a fallback when nothing else is posted")
        epub = self.score("Chainsaw Man v18 (2025) (epub)")
        self.assertGreaterEqual(epub, 85, "an EPUB is converted now, not refused")
        self.assertLess(epub, self.score("Chainsaw Man v18 (2025) (Digital) (LuCaZ) (cbz)"))



JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"


def _image_pdf(pages, *, text=False, two_images=False, shared=False):
    """A real PDF 1.3: each page one DCT (JPEG) image XObject, classic xref.

    `shared` lays it out as Viz does: every image listed once in the page
    tree's resources, and each page's (deflated) content stream drawing one.
    """
    import zlib
    objects = {}
    kids = []
    shared_names = []
    number = 3
    for page_index, image in enumerate(pages):
        page_no, image_no, content_no = number, number + 1, number + 2
        number += 3
        drawn = f"/Im{page_index} Do" + (f" /Im{(page_index + 1) % len(pages)} Do" if two_images else "")
        content = zlib.compress(f"q 10 0 0 10 0 0 cm {drawn} Q".encode())
        objects[content_no] = (f"<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n").encode() + content + b"\nendstream"
        shared_names.append(f"/Im{page_index} {image_no} 0 R")
        font = " /Font << /F1 99 0 R >>" if text else ""
        own = "" if shared else (
            f"/Resources << /XObject << /Im{page_index} {image_no} 0 R"
            + (f" /Im{(page_index + 1) % len(pages)} {image_no} 0 R" if two_images else "") + f" >>{font} >> "
        )
        objects[page_no] = (f"<< /Type /Page /Parent 2 0 R {own}/Contents {content_no} 0 R "
                            f"/MediaBox [0 0 10 10] >>").encode()
        objects[image_no] = (f"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /Filter /DCTDecode "
                             f"/Length {len(image)} >>\nstream\n").encode() + image + b"\nendstream"
        kids.append(f"{page_no} 0 R")
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    tree_resources = f" /Resources << /XObject << {' '.join(shared_names)} >> >>" if shared else ""
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)}{tree_resources} >>".encode()
    out = bytearray(b"%PDF-1.3\n")
    offsets = {}
    for key in sorted(objects):
        offsets[key] = len(out)
        out += f"{key} 0 obj\n".encode() + objects[key] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for key in range(1, size):
        out += (f"{offsets[key]:010d} 00000 n \n" if key in offsets else "0000000000 65535 f \n").encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _image_epub(path, pages, *, text_pages=0):
    import zipfile
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml",
                      '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        items, spine = [], []
        for index, image in enumerate(pages):
            book.writestr(f"OEBPS/images/p{index}.jpg", image)
            book.writestr(f"OEBPS/p{index}.xhtml",
                          f'<html><head><title>x</title></head><body><img src="images/p{index}.jpg"/></body></html>')
            items.append(f'<item id="p{index}" href="p{index}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="p{index}"/>')
        for index in range(text_pages):
            book.writestr(f"OEBPS/t{index}.xhtml", "<html><body><p>" + "words " * 40 + "</p></body></html>")
            items.append(f'<item id="t{index}" href="t{index}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="t{index}"/>')
        book.writestr("OEBPS/content.opf",
                      f"<package><manifest>{''.join(items)}</manifest><spine>{''.join(spine)}</spine></package>")


class RunPackInPartsTests(unittest.TestCase):
    """Descender (2026-09-30): a download site run pack in five parts brought only
    its first, and the backlog sweep searched the rest one by one without
    knowing a pack was on its way."""

    LABELS = [
        ("Descender #1 – 8 (2015-2016) (490 MB)", 1, 8), ("Descender #9 – 16 (2016) (460 MB)", 9, 16),
        ("Descender #17 – 23 (2016-2017) (481 MB)", 17, 23), ("Descender #24 – 28 (2017-2018) (457 MB)", 24, 28),
        ("Descender #29 – 32 (2018) (250 MB)", 29, 32),
        ("Descender Vol. 2 – Machine Moon (2016) (372 MB)", None, None),
    ]
    YEARS = {1: 2015, 9: 2016, 17: 2016, 24: 2017, 29: 2018}

    def wanted(self, numbers):
        return [{"jobId": 100 + n, "issueNumber": str(n), "issueId": 500 + n, "status": "queued", "seriesId": "7"}
                for n in numbers]

    def context(self, job_id=101):
        number = job_id - 100
        year = max(year for first, year in self.YEARS.items() if first <= number)
        return {"seriesTitle": "Descender", "seriesId": "7", "issueNumber": str(number), "seriesYear": 2015,
                "publicationYear": year, "requestId": "181"}

    def test_each_part_holding_wanted_issues_is_fetched_against_its_first(self):
        store = Mock()
        store.get_acquisition_job_context.side_effect = self.context
        store.record_acquisition_download.side_effect = lambda job, *a, **k: {"id": 9000 + job}
        links = [{"url": f"https://files.example/{i}", "label": label, "first": first, "last": last}
                 for i, (label, first, last) in enumerate(self.LABELS)]
        wanted = self.wanted([n for n in range(1, 33) if n not in (9, 10)])  # #9 and #10 already owned
        candidate = {"postUrl": "https://comics.example/other-comics/descender-1-9-tpb/", "source": "direct_site",
                     "title": "Descender #1 – 32 + TPBs (2015-2018)"}
        with patch("app._direct_site_post_parts", return_value=links), \
             patch("app._start_direct_fetch") as fetch:
            started = app._grab_other_direct_site_parts(store, candidate, wanted[0], wanted, self.context())
        self.assertEqual(started, 4, "every part but the lead's")
        self.assertEqual([call.args[1] for call in fetch.call_args_list], [111, 117, 124, 129],
                         "each against the first issue wanted in it; #9 is owned, so #11 leads its part")
        self.assertEqual([call.args[2] for call in store.record_acquisition_download.call_args_list],
                         ["Descender #9 – 16 (2016)", "Descender #17 – 23 (2016-2017)",
                          "Descender #24 – 28 (2017-2018)", "Descender #29 – 32 (2018)"])
        self.assertTrue(all(call.kwargs["release_key"] == candidate["postUrl"]
                            for call in store.record_acquisition_download.call_args_list))

    def test_issues_a_pack_in_flight_will_bring_are_held(self):
        store = Mock()
        store.get_acquisition_job_context.side_effect = self.context
        store.wanted_run_issues.return_value = self.wanted(range(1, 33))
        store.live_downloads_with_requests.return_value = [
            {"id": 1, "job_id": 101, "request_id": 181, "release_title": "Descender #1 – 32 + TPBs (2015-2018)",
             "source": "direct_site"},
        ]
        self.assertEqual(app._issues_awaiting_a_pack(store), set(range(102, 133)))
        store.live_downloads_with_requests.return_value = [
            {"id": 2, "job_id": 117, "request_id": 181, "release_title": "Descender #17 – 23 (2016-2017)",
             "source": "direct_site"},
            {"id": 3, "job_id": 109, "request_id": 181, "release_title": "Descender 009 (2016) (Digital) (Zone-Empire)",
             "source": "sabnzbd"},
        ]
        self.assertEqual(app._issues_awaiting_a_pack(store), set(range(118, 124)),
                         "a part holds its own issues; a single holds nothing")

    def test_no_pass_searches_an_issue_a_pack_will_bring(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [102, 140]
        with patch("app.catalog_store", return_value=store), \
             patch("app._issues_awaiting_a_pack", return_value={102}), \
             patch("app._auto_grab_release", return_value=True) as grab:
            app._automatic_release_grabs(None, backoff=True)
        self.assertEqual([call.args[0] for call in grab.call_args_list], [140])


class ComicProseEbookTests(unittest.TestCase):
    """Comics are searched in EBook too, where a novel of the same name lives."""

    def test_a_book_of_text_is_not_taken_as_a_comic(self):
        with tempfile.TemporaryDirectory() as folder:
            novel = Path(folder) / "The Adventure Zone 04.epub"
            _image_epub(novel, [JPEG + b"cover"], text_pages=30)
            comic = Path(folder) / "comic.epub"
            _image_epub(comic, [JPEG + b"1", JPEG + b"2", JPEG + b"3"])
            self.assertTrue(app._epub_is_prose(novel))
            self.assertFalse(app._epub_is_prose(comic), "a comic EPUB is still a comic")
            with self.assertRaises(app.DownloadContentMismatch) as caught:
                app._select_downloaded_comic(
                    [novel], Path(folder),
                    {"format": "comic", "seriesTitle": "The Adventure Zone", "issueNumber": "4"},
                    "The Adventure Zone 04 - The Crystal Kingdom (2021)",
                )
            self.assertIn("book of text", str(caught.exception))


class MangaEbookConversionTests(unittest.TestCase):
    """The publisher's image-only ebook, copied into a CBZ page for page.

    Chainsaw Man v1 and v2 exist on the indexers only as Viz's PDF: 193 pages,
    192 of them one JPEG, no text. CBZ-only refused them and nothing else came.
    """

    pages = [JPEG + b"1", JPEG + b"2", JPEG + b"3"]

    def cbz_pages(self, cbz):
        import zipfile
        with zipfile.ZipFile(cbz) as archive:
            return [archive.read(name) for name in sorted(archive.namelist())]

    def test_an_image_pdf_becomes_a_cbz_page_for_page(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "v01.pdf", Path(folder) / "v01.cbz"
            source.write_bytes(_image_pdf(self.pages))
            self.assertEqual(app.convert_ebook_to_cbz(source, target), 3)
            self.assertEqual(self.cbz_pages(target), self.pages, "the JPEGs are copied, not re-encoded")

    def test_a_pdf_that_shares_its_images_across_pages_still_converts(self):
        """Viz lists every image once on the page tree; each page draws one."""
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "v01.pdf", Path(folder) / "v01.cbz"
            source.write_bytes(_image_pdf(self.pages, shared=True))
            self.assertEqual(app.convert_ebook_to_cbz(source, target), 3)
            self.assertEqual(self.cbz_pages(target), self.pages)
            layered = Path(folder) / "layered.pdf"
            layered.write_bytes(_image_pdf(self.pages, shared=True, two_images=True))
            with self.assertRaises(app.EbookNotConvertible, msg="a page drawing two images is still refused"):
                app.convert_ebook_to_cbz(layered, Path(folder) / "out.cbz")

    def page(self, colour):
        """A real JPEG page: grey for an interior, orange for a cover."""
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (30, 45), colour).save(buffer, "JPEG")
        return buffer.getvalue()

    def test_a_pdf_saved_back_to_front_is_put_the_right_way_round(self):
        """Viz's PDF opens on the story's last page and ends on the colour cover."""
        grey, cover = self.page((200, 200, 200)), self.page((230, 90, 30))
        backwards = [grey, grey, grey, grey, cover]
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "v01.pdf", Path(folder) / "v01.cbz"
            source.write_bytes(_image_pdf(backwards))
            app.convert_ebook_to_cbz(source, target)
            self.assertEqual(self.cbz_pages(target), list(reversed(backwards)), "the cover comes first")

    def test_a_pdf_already_in_order_is_left_alone(self):
        grey, cover = self.page((200, 200, 200)), self.page((230, 90, 30))
        for pages in ([cover, grey, grey, grey, grey],   # cover first: already right
                      [grey, grey, grey, grey, grey],    # no colour anywhere: no evidence
                      [cover, grey, grey, grey, cover]):  # colour both ends: not certain
            with self.subTest(first=pages[0] == cover, last=pages[-1] == cover):
                with tempfile.TemporaryDirectory() as folder:
                    source, target = Path(folder) / "v01.pdf", Path(folder) / "v01.cbz"
                    source.write_bytes(_image_pdf(pages))
                    app.convert_ebook_to_cbz(source, target)
                    self.assertEqual(self.cbz_pages(target), pages)

    def test_an_image_epub_becomes_a_cbz_in_reading_order(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "v01.epub", Path(folder) / "v01.cbz"
            _image_epub(source, self.pages)
            self.assertEqual(app.convert_ebook_to_cbz(source, target), 3)
            self.assertEqual(self.cbz_pages(target), self.pages)

    def test_an_ebook_that_is_not_page_images_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            layered = folder / "layered.pdf"
            layered.write_bytes(_image_pdf(self.pages, two_images=True))
            prose = folder / "prose.epub"
            _image_epub(prose, self.pages[:1], text_pages=5)
            junk = folder / "junk.pdf"
            junk.write_bytes(b"not a pdf")
            for source in (layered, prose, junk):
                with self.subTest(source=source.name):
                    with self.assertRaises(app.EbookNotConvertible):
                        app.convert_ebook_to_cbz(source, folder / "out.cbz")

    def test_a_downloaded_pdf_volume_imports_as_a_cbz(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / "VIZ.Media.Chainsaw.Man.Vol.01.2020.HYBRiD.MANGA.eBook-PNLS"
            source.mkdir()
            (source / "Chainsaw Man v01 (2020) (Digital).pdf").write_bytes(_image_pdf(self.pages))
            (source / "release.nfo").write_bytes(b"info")
            job = {"seriesTitle": "Chainsaw Man", "seriesYear": 2020, "issueNumber": "1",
                   "publisher": "Viz", "format": "manga", "preferredLanguage": "en"}
            clean = {"file_health": {"status": "ok"}, "lookup_identity": {}, "embedded_metadata": {}}
            with patch("app.inventory_file", return_value=clean):
                selected = app.select_downloaded_comic(source, job, root)
            try:
                self.assertEqual(Path(selected["path"]).suffix, ".cbz")
                self.assertEqual(self.cbz_pages(selected["path"]), self.pages)
                self.assertTrue(selected.get("converted"))
            finally:
                app._discard_converted(selected)
            self.assertFalse(Path(selected["path"]).exists(), "the converted copy is cleaned up")

    def test_a_comic_that_arrives_as_a_pdf_is_filed_as_a_cbz(self):
        """American Vampire #19 exists only as iNTENSiTY's scanned PDF."""
        name = "American.Vampire.Vol.1.No.19.Nov.2011.SCAN.Comic.eBook-iNTENSiTY"
        job = {"seriesTitle": "American Vampire", "seriesYear": 2010, "issueNumber": "19",
               "publisher": "DC Comics", "format": "comic", "preferredLanguage": "en"}

        def inventory(parsed):
            return {"file_health": {"status": "ok"}, "embedded_metadata": {},
                    "lookup_identity": {"title": parsed.title, "issue": parsed.issue}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / name
            source.mkdir()
            (source / f"{name}.pdf").write_bytes(_image_pdf(self.pages))
            with patch("app.inventory_file", side_effect=inventory):
                selected = app.select_downloaded_comic(source, job, root)
            try:
                self.assertEqual(Path(selected["path"]).suffix, ".cbz")
                self.assertEqual(self.cbz_pages(selected["path"]), self.pages)
                self.assertTrue(selected.get("issueMatch"))
            finally:
                app._discard_converted(selected)
            # One that is not a page per image is still the issue: kept as it came.
            (source / f"{name}.pdf").write_bytes(_image_pdf(self.pages, two_images=True))
            with patch("app.inventory_file", side_effect=inventory):
                kept = app.select_downloaded_comic(source, job, root)
            self.assertEqual(Path(kept["path"]).suffix, ".pdf")
            self.assertFalse(kept.get("converted"))


class ImportTrustsAMatchingReleaseTests(unittest.TestCase):
    """A download is refused as the wrong comic only when a file says it is.

    Every false refusal so far was a file whose name the check could not read
    -- American Vampire #19's "Vol.1.No.19", If Destruction Be Our Lot's
    "No.04", Supergirl: Woman of Tomorrow #2's -- each barred as the wrong
    comic, deleted, and never tried again. These are the names that did it.
    """

    supergirl = {"seriesTitle": "Supergirl: Woman of Tomorrow", "seriesYear": 2021, "issueNumber": "2",
                 "publisher": "DC Comics", "format": "comic", "preferredLanguage": "en",
                 "publicationYear": 2021, "publicationDate": "2021-11-23"}
    supergirl_release = "Supergirl - Woman of Tomorrow 02 (of 08) (2021) (digital) (Son of Ultron-Empire)"

    @staticmethod
    def inventory(parsed):
        if parsed.filename.startswith("broken"):
            return {"file_health": {"status": "error", "message": "the archive is truncated"},
                    "lookup_identity": {}, "embedded_metadata": {}}
        return {"file_health": {"status": "ok"}, "embedded_metadata": {},
                "lookup_identity": {"title": parsed.title, "issue": parsed.issue}}

    def choose(self, names, job=None, release=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / "download"
            source.mkdir()
            for name in names:
                (source / name).write_bytes(b"comic")
            with patch("app.inventory_file", side_effect=self.inventory):
                return app.select_downloaded_comic(
                    source, job or self.supergirl, root,
                    release_title=self.supergirl_release if release is None else release,
                )

    def test_names_that_were_refused_before_now_import(self):
        vampire = {"seriesTitle": "American Vampire", "seriesYear": 2010, "issueNumber": "19",
                   "publisher": "DC Comics", "format": "comic", "preferredLanguage": "en",
                   "publicationYear": 2011, "publicationDate": "2011-09-28"}
        destruction = {"seriesTitle": "If Destruction Be Our Lot", "seriesYear": 2026, "issueNumber": "4",
                       "publisher": "Image", "format": "comic", "preferredLanguage": "en",
                       "publicationYear": 2026, "publicationDate": "2026-06-10"}
        cases = [
            (vampire, "American.Vampire.Vol.1.No.19.Nov.2011.SCAN.Comic.eBook-iNTENSiTY",
             "American.Vampire.Vol.1.No.19.Nov.2011.SCAN.Comic.eBook-iNTENSiTY.cbr"),
            (destruction, "Image.Comics.If.Destruction.Be.Our.Lot.No.04.2026.HYBRID.COMIC.eBook-21A1",
             "Image.Comics.If.Destruction.Be.Our.Lot.No.04.2026.HYBRID.COMIC.eBook-21A1.cbz"),
            (self.supergirl, self.supergirl_release, f"{self.supergirl_release}.cbr"),
        ]
        for job, release, name in cases:
            with self.subTest(name=name):
                self.assertEqual(Path(self.choose([name], job, release)["path"]).name, name)

    def test_a_file_that_names_nothing_is_taken_on_a_matching_releases_word(self):
        selected = self.choose(["swot.cbr"])
        self.assertEqual(Path(selected["path"]).name, "swot.cbr")
        self.assertEqual(selected["acceptedOn"], "release")

    def test_without_a_matching_release_a_silent_file_is_unproven_not_wrong(self):
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.choose(["swot.cbr"], release="misc comics upload 2021")
        self.assertEqual((refused.exception.kind, refused.exception.stage), ("unidentified", "unidentified"))
        self.assertIn("swot.cbr: read as 'swot', issue not stated", str(refused.exception))
        self.assertIn("does not match Supergirl: Woman of Tomorrow #2", str(refused.exception))

    def test_a_file_that_names_another_issue_is_the_wrong_comic(self):
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.choose(["Supergirl - Woman of Tomorrow 03 (of 08) (2021) (digital).cbr"])
        self.assertEqual((refused.exception.kind, refused.exception.stage), ("contradiction", "content"))
        self.assertIn("is #3, not Supergirl: Woman of Tomorrow #2", str(refused.exception))

    def test_an_unreadable_file_is_unproven(self):
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.choose(["broken.cbr"])
        self.assertEqual(refused.exception.kind, "unreadable")
        self.assertIn("the archive is truncated", str(refused.exception))

    def test_a_zero_filled_download_is_incomplete_and_bars_its_release(self):
        """Supergirl #2's only release: 22 of 140 MB zeros, no RAR header, no repair files."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / "download"
            source.mkdir()
            holed = source / "broken - Supergirl - Woman of Tomorrow 02 (of 08).cbr"
            holed.write_bytes(b"\x00" * (2 << 20) + b"page data")
            with patch("app.inventory_file", side_effect=self.inventory):
                with self.assertRaises(app.DownloadContentMismatch) as refused:
                    app.select_downloaded_comic(source, self.supergirl, root,
                                                release_title=self.supergirl_release)
        self.assertEqual((refused.exception.kind, refused.exception.stage), ("damaged", "damaged"))
        self.assertNotIn(refused.exception.kind, app.DownloadContentMismatch.UNPROVEN,
                         "missing data is proof: the release is not tried again tomorrow")
        self.assertIn("2 MB of it is zero-filled", str(refused.exception))
        # Merely unreadable, with no holes, is still only unproven.
        with self.assertRaises(app.DownloadContentMismatch) as unproven:
            self.choose(["broken.cbr"])
        self.assertEqual(unproven.exception.kind, "unreadable")

    def test_a_password_protected_download_is_refused_and_bars_its_release(self):
        # Gate 3 (2026-10-05): only SABnzbd's ENCRYPTED pause caught these; a
        # torrent or direct download passed the header check and was taken.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / "download"
            source.mkdir()
            locked = source / "Supergirl - Woman of Tomorrow 02 (of 08) (2021).cbz"
            with zipfile.ZipFile(locked, "w") as archive:
                archive.writestr("001.jpg", b"ciphertext")
            # Set the "encrypted" bit a password-protected zip carries, in the
            # local and the central headers (zipfile writes no encryption).
            data = bytearray(locked.read_bytes())
            data[data.find(b"PK\x03\x04") + 6] |= 0x1
            data[data.find(b"PK\x01\x02") + 8] |= 0x1
            locked.write_bytes(bytes(data))
            self.assertEqual(app.archive_is_locked(locked), "The archive is password-protected")
            with patch("app.inventory_file", side_effect=self.inventory):
                with self.assertRaises(app.DownloadContentMismatch) as refused:
                    app.select_downloaded_comic(source, self.supergirl, root, release_title=self.supergirl_release)
        self.assertEqual(refused.exception.kind, "format")
        self.assertNotIn("format", app.DownloadContentMismatch.UNPROVEN, "no copy of this release will open")
        self.assertIn("password-protected", str(refused.exception))

    @unittest.skipUnless(app.shutil.which("bsdtar"), "needs bsdtar, as the image has")
    def test_the_archive_tool_is_believed_on_everything_it_says_not_its_last_line(self):
        # Review of Gate 3 (2026-10-05): the first version read the tool's last
        # line, which for an encrypted entry is "Error exit delayed from
        # previous errors", and its test made the message up. This asks the
        # real tool about a really encrypted archive. bsdtar cannot write an
        # encrypted RAR or 7-Zip (nothing free can write RAR), so the archive
        # is an encrypted zip; reading it goes through the same libarchive
        # passphrase path RAR and 7-Zip do.
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "001.jpg"
            page.write_bytes(b"\xff\xd8\xff\xe0 a page")
            locked, plain = Path(folder) / "locked.cbr", Path(folder) / "plain.cbr"
            made = subprocess.run(["bsdtar", "-cf", str(locked), "--format", "zip", "--options", "zip:encryption=zipcrypt",
                                   "--passphrase", "secret", "-C", folder, "001.jpg"], capture_output=True)
            if made.returncode != 0:
                self.skipTest("this bsdtar cannot write an encrypted zip")
            subprocess.run(["bsdtar", "-cf", str(plain), "--format", "zip", "-C", folder, "001.jpg"], check=True)
            self.assertEqual(app._archive_tool_says_locked(locked), "The archive is password-protected")
            self.assertIsNone(app._archive_tool_says_locked(plain))
            # And the reader's own call refuses it as unreadable, whatever it says.
            with self.assertRaises(ValueError):
                app._run_archive_tool(["-xOf", str(locked), "001.jpg"], 1 << 20)

    def test_a_rar_or_7zip_is_asked_of_the_archive_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            rar = Path(folder) / "Saga 001.cbr"
            rar.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 64)
            with patch("app._archive_tool_says_locked", return_value="The archive is password-protected") as asked:
                self.assertEqual(app.archive_is_locked(rar), "The archive is password-protected")
            asked.assert_called_once_with(rar)

    def test_a_download_labelled_in_another_language_is_refused_at_import(self):
        # Gate 3 (2026-10-05): scored away at search time and tested there; the
        # import's own refusal, for a release that slipped through, was not.
        job = {**self.supergirl, "preferredLanguage": "en"}
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.choose(["Supergirl - Woman of Tomorrow 02 (of 08) (2021) (French).cbz"], job=job)
        self.assertEqual(refused.exception.kind, "language")
        self.assertIn("French", str(refused.exception))

    def test_a_download_with_no_comic_in_it_is_refused_as_such(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / "download"
            source.mkdir()
            (source / "release.nfo").write_text("posted by")
            (source / "cover.jpg").write_bytes(b"jpeg")
            with self.assertRaises(app.DownloadContentMismatch) as refused:
                app.select_downloaded_comic(source, self.supergirl, root, release_title=self.supergirl_release)
        self.assertEqual(refused.exception.kind, "format")
        self.assertNotIn("format", app.DownloadContentMismatch.UNPROVEN)

    def test_two_files_that_name_nothing_are_not_guessed_between(self):
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.choose(["a.cbr", "b.cbr"])
        self.assertEqual(refused.exception.kind, "unidentified")
        self.assertIn("which of 2 files", str(refused.exception))

    def vouch(self, names, release="misc comics upload 2021"):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / "download"
            source.mkdir()
            for name in names:
                (source / name).write_bytes(b"\x00" * (2 << 20) if "holed" in name else b"comic")
            with patch("app.inventory_file", side_effect=self.inventory):
                return app.select_downloaded_comic(source, self.supergirl, root, release_title=release, vouched=True)

    def test_a_release_taken_by_hand_is_on_the_persons_word(self):
        """The owner (2026-09-29): "your word wins on names" -- the release
        name, the file's name, its year and its language are not judged."""
        self.assertEqual(self.vouch(["swot.cbr"])["acceptedOn"], "hand", "a silent file under an unmatched release")
        self.assertEqual(self.vouch(["Batman 002 (2021).cbr"])["acceptedOn"], "file", "another series' name, the right number")
        self.assertEqual(Path(self.vouch(["Supergirl - Woman of Tomorrow 03 (of 08) (2021).cbr"])["path"]).name,
                         "Supergirl - Woman of Tomorrow 03 (of 08) (2021).cbr", "one file stating another number is still theirs to take")
        self.assertEqual(Path(self.vouch(["Supergirl 02 (French).cbr"], release="Supergirl 02 (French)")["path"]).name,
                         "Supergirl 02 (French).cbr", "a language label is not judged")
        self.assertEqual(Path(self.vouch(["x 003.cbr", "y 002.cbr"])["path"]).name, "y 002.cbr",
                         "among several files the one naming the issue is taken")

    def test_a_release_taken_by_hand_still_has_to_hold_one_readable_comic(self):
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.vouch(["broken.cbr"])
        self.assertEqual(refused.exception.kind, "unreadable")
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.vouch(["broken holed - swot 02.cbr"])
        self.assertEqual(refused.exception.kind, "damaged")
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.vouch(["a.cbr", "b.cbr"])
        self.assertEqual(refused.exception.kind, "unidentified")
        self.assertIn("None of the 2 files names Supergirl: Woman of Tomorrow #2: a.cbr, b.cbr", str(refused.exception))
        with self.assertRaises(app.DownloadContentMismatch) as refused:
            self.vouch(["x 002.cbr", "y 002.cbr"])
        self.assertIn("Could not tell which of 2 files is Supergirl: Woman of Tomorrow #2", str(refused.exception))
        self.assertIn("Release: misc comics upload 2021 -- taken by hand as", str(refused.exception))


class RemoveFromLibraryTests(unittest.TestCase):
    """Removing a run deletes its files -- only ever inside the library."""

    def plan(self, root, files, active=0):
        return {"id": "7", "title": "Blue Lock", "fileCount": len(files), "sizeBytes": 0,
                "requestCount": 1, "activeDownloads": active,
                "files": [{"id": index, "path": str(path), "root": str(root), "size": 1}
                          for index, path in enumerate(files)]}

    def test_the_files_go_and_so_do_the_folders_they_leave_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            series = root / "Manga" / "Kodansha" / "Blue Lock (2018)"
            series.mkdir(parents=True)
            volume = series / "Blue Lock (2018) v01.cbz"
            volume.write_bytes(b"x" * 10)
            (root / "Manga" / "keep.txt").write_bytes(b"k")
            store = Mock()
            store.series_removal_plan.return_value = self.plan(root, [volume])
            with patch("app.catalog_store", return_value=store):
                result = app.remove_series_from_library(7)
            self.assertEqual((result["filesDeleted"], result["bytesFreed"]), (1, 10))
            self.assertFalse(volume.exists())
            self.assertFalse((root / "Manga" / "Kodansha").exists(), "emptied folders go")
            self.assertTrue((root / "Manga").exists(), "a folder with something else in it stays")
            store.remove_series_run.assert_called_once_with(7)

    def test_a_path_outside_the_library_stops_the_whole_removal(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            root = Path(folder).resolve()
            inside = root / "a.cbz"
            inside.write_bytes(b"x")
            outside = Path(elsewhere) / "b.cbz"
            outside.write_bytes(b"x")
            store = Mock()
            store.series_removal_plan.return_value = self.plan(root, [inside, outside])
            with patch("app.catalog_store", return_value=store):
                with self.assertRaisesRegex(ValueError, "outside the library"):
                    app.remove_series_from_library(7)
            self.assertTrue(inside.exists() and outside.exists(), "nothing is deleted")
            store.remove_series_run.assert_not_called()

    def test_nothing_is_removed_while_it_is_downloading(self):
        store = Mock()
        store.series_removal_plan.return_value = self.plan(Path("/tmp"), [], active=1)
        with patch("app.catalog_store", return_value=store):
            with self.assertRaisesRegex(ValueError, "downloading"):
                app.remove_series_from_library(7)
        store.remove_series_run.assert_not_called()


class SabCleanupTests(unittest.TestCase):
    """A finished download leaves SABnzbd's folder once the library has it."""

    def test_the_job_and_its_files_are_deleted_through_sabnzbd(self):
        with patch("app._enabled_acquisition_service", return_value={"url": "http://sab", "apiKey": "k"}), \
             patch("app.fetch_json_with_headers", return_value={"status": True}) as fetch:
            app._sab_remove_job({"sab_nzo_id": "SABnzbd_nzo_1"})
        url = fetch.call_args.args[0]
        for part in ("mode=history", "name=delete", "value=SABnzbd_nzo_1", "del_files=1"):
            self.assertIn(part, url)

    def test_a_failed_cleanup_never_fails_the_import(self):
        with patch("app._enabled_acquisition_service", side_effect=ValueError("SABnzbd is off")):
            app._sab_remove_job({"sab_nzo_id": "SABnzbd_nzo_1"})
        with patch("app.fetch_json_with_headers") as fetch:
            app._sab_remove_job({})
        fetch.assert_not_called()


class OneBrokenIndexerTests(unittest.TestCase):
    """Once & Future #30: four copies of one release, and the first one's NZB
    was broken. The automatic grab tried only that one, and the row went on
    saying "4 release candidates found"."""

    candidates = [
        {"id": "a", "title": "Once  Future 030 (2022)", "indexer": "Indexer A"},
        {"id": "b", "title": "Once & Future 030 (2022)", "indexer": "Indexer B"},
        {"id": "c", "title": "Once & Future 030 (2022)", "indexer": "Indexer C"},
    ]

    def grab(self, send):
        store = Mock()
        with patch("app.catalog_store", return_value=store), \
             patch("app.search_prowlarr_releases", return_value={"candidates": self.candidates}), \
             patch("app.send_release_to_sabnzbd", side_effect=send) as sent:
            result = app._auto_grab_release(621)
        return result, sent, store

    def test_a_broken_nzb_moves_on_to_the_next_indexer(self):
        def send(job_id, candidate_id):
            if candidate_id == "a":
                raise app.ReleaseDownloadError("Prowlarr returned a response that is not a valid NZB")
            return {"status": "grabbed"}
        result, sent, _ = self.grab(send)
        self.assertEqual([call.args[1] for call in sent.call_args_list], ["a", "b"])
        self.assertEqual(result["release"]["indexer"], "Indexer B")

    def test_when_none_can_be_fetched_the_row_says_so(self):
        def send(job_id, candidate_id):
            raise app.ReleaseDownloadError("Prowlarr returned a response that is not a valid NZB")
        result, sent, store = self.grab(send)
        self.assertIsNone(result)
        self.assertEqual(sent.call_count, 3)
        reason = store.update_acquisition_job.call_args.args[2]
        self.assertIn("none could be fetched", reason)
        self.assertIn("Indexer A", reason)

    def test_sabnzbd_refusing_stops_rather_than_trying_every_copy(self):
        def send(job_id, candidate_id):
            raise app.SABSubmissionError("SABnzbd is not accepting jobs")
        result, sent, store = self.grab(send)
        self.assertIsNone(result)
        self.assertEqual(sent.call_count, 1)
        self.assertIn("could not send it to SABnzbd", store.update_acquisition_job.call_args.args[2])


class MangaFiledUnderAnimeTests(unittest.TestCase):
    """Blue Lock v02 and v24 are posted only under TV/Anime; the other 35
    volumes were found, so those two looked as though nobody had posted them."""

    def score(self, title, number, year, categories=(5000, 105000, 5070)):
        context = {"seriesTitle": "Blue Lock", "issueNumber": number, "publicationYear": year,
                   "publisher": "Kodansha Comics USA", "format": "manga", "preferredLanguage": "en"}
        release = {"title": title, "categories": [{"id": c} for c in categories]}
        return app._release_candidate_score(release, context)[0]

    def test_a_volume_filed_under_anime_is_found_and_taken(self):
        self.assertGreaterEqual(self.score("Blue Lock v02 (2021) (Digital) (F) (danke-Empire)", "2", 2021), 85)
        self.assertGreaterEqual(self.score("Blue Lock v24 (2024) (Digital) (Stick)", "24", 2024), 85)
        self.assertIn("5070", app.MANGA_CATEGORIES)

    def test_the_anime_itself_is_never_taken_for_the_book(self):
        for title in ("Blue.Lock.Vol.2.2022.ANiME.DUAL.COMPLETE.BLURAY-iFPD",
                      "Blue Lock v02 1080p WEB-DL x265",
                      "Blue Lock Vol 2 (2022) [BD][1080p Multi Opus AV1][Multi-subs]",
                      "Blue Lock S01E02 1080p"):
            with self.subTest(title=title):
                self.assertLess(self.score(title, "2", 2021), 85)


class RelaunchIsAnotherComicTests(unittest.TestCase):
    """Ultimate Spider-Man (2000) was filled with Hickman's 2024 relaunch: the
    same name and numbers, and the year only ever added points."""

    def score(self, title, number="3", year=2001, series_year=2000):
        context = {"seriesTitle": "Ultimate Spider-Man", "issueNumber": number,
                   "publicationYear": year, "seriesYear": series_year, "preferredLanguage": "en"}
        return app._release_candidate_score({"title": title, "categories": [{"id": 7030}]}, context)[0]

    def test_the_relaunch_is_refused_for_the_original(self):
        self.assertEqual(self.score("Ultimate Spider-Man 003 (2024) (Digital) (Shan-Empire)"), 0)
        self.assertEqual(self.score("Ultimate Spider-Man 016 (2025) (Digital) (Shan-Empire)",
                                    number="16", year=2002), 0)

    def test_the_original_is_still_taken(self):
        self.assertGreaterEqual(self.score("Ultimate Spider-Man 003 (2001) (Digital)"), 85)
        self.assertGreaterEqual(self.score("Ultimate Spider-Man 003 (2000) (Digital)"), 85,
                                "the run's start year is a fair thing for a release to say")
        self.assertGreaterEqual(self.score("Ultimate Spider-Man 003 (Digital) (cbz)"), 85,
                                "a release that states no year is not refused for it")

    def test_a_year_in_the_title_is_not_the_release_date(self):
        context = {"seriesTitle": "2000 AD", "issueNumber": "45", "publicationYear": 1977,
                   "preferredLanguage": "en"}
        self.assertGreaterEqual(app._release_candidate_score(
            {"title": "2000 AD 045 (1977)", "categories": [{"id": 7030}]}, context)[0], 85)

    def test_without_the_issues_own_year_nothing_is_refused(self):
        """A start year alone would refuse Batman #500 for saying 1993."""
        context = {"seriesTitle": "Batman", "issueNumber": "500", "seriesYear": 1940,
                   "preferredLanguage": "en"}
        self.assertGreaterEqual(app._release_candidate_score(
            {"title": "Batman 500 (1993) (Digital)", "categories": [{"id": 7030}]}, context)[0], 85)



class MemberRequestFactsTests(unittest.TestCase):
    """What the admin is told about a request comes from Metron."""

    def _metron(self, responses):
        def fetch(provider, url, credential, **_):
            for fragment, body in responses.items():
                if fragment in url:
                    return body
            raise AssertionError(f"unexpected {url}")
        return patch.multiple(app, fetch_provider_json=fetch, _provider_credential=lambda provider: "token")

    def test_one_issue_is_told_by_its_own_rating_and_story(self):
        series = {"genres": [{"id": 1, "name": "Humor"}, {"id": 2, "name": "Fantasy"}], "desc": "A land of Ooo, in long form, full of candy people and wizards and a dog who stretches."}
        issue = {"number": "1", "rating": {"id": 2, "name": "Everyone"}, "desc": "It's Halloween in Ooo, and every ghost in the land has a costume party planned."}
        with self._metron({"/series/16702/": series, "/issue/99/": issue}):
            facts = app.member_request_facts("discover_issues", {
                "provider": "metron", "providerSeriesId": "16702", "numbers": ["1"], "providerIssueId": "99"})
        self.assertEqual((facts["rating"], facts["ratingFrom"], facts["genres"]), ("Everyone", None, ["Humor", "Fantasy"]))
        self.assertTrue(facts["synopsis"].startswith("It's Halloween"), "the issue's own story, not the run's")

    def test_a_run_is_told_by_its_first_issue_and_unknown_is_no_rating(self):
        listing = {"results": [{"id": 12, "number": "2", "cover_date": "2020-02-01"}, {"id": 11, "number": "1", "cover_date": "2020-01-01"}]}
        with self._metron({"/series/5/": {"genres": []}, "/issue/?series_id=5": listing,
                           "/issue/11/": {"number": "1", "rating": {"name": "Mature"}}}):
            facts = app.member_request_facts("discover_run", {"provider": "metron", "providerSeriesId": "5"})
        self.assertEqual((facts["rating"], facts["ratingFrom"]), ("Mature", "Issue 1"))
        with self._metron({"/series/5/": {}, "/issue/?series_id=5": listing, "/issue/11/": {"rating": {"name": "Unknown"}}}):
            facts = app.member_request_facts("discover_run", {"provider": "metron", "providerSeriesId": "5"})
        self.assertEqual((facts["rating"], facts["ratingFrom"]), (None, None))
        self.assertEqual(app.member_request_facts("discover_run", {"provider": "gcd", "providerSeriesId": "5"}), {},
                         "only Metron has ratings")


class StoryArcTests(unittest.TestCase):
    """Story arcs from Metron: found by name, listed in reading order with what
    the library holds, pulled a series at a time."""

    ARC_ROWS = [
        {"id": 3, "number": "610", "cover_date": "2003-02-01", "series": {"id": 796, "name": "Batman", "year_began": 1940}},
        {"id": 1, "number": "608", "cover_date": "2002-12-01", "series": {"id": 796, "name": "Batman", "year_began": 1940}},
        {"id": 2, "number": "609", "cover_date": "2003-01-01", "series": {"id": 796, "name": "Batman", "year_began": 1940}},
        {"id": 9, "number": "1", "cover_date": "2003-06-01", "series": {"id": 5000, "name": "Batman: Gotham Knights", "year_began": 2000}},
    ]

    def _patches(self, relevance=None, by_metron=None):
        def fetch(provider, url, credential, **_):
            if "/arc/?" in url:
                return {"results": [{"id": 482, "name": "Hush"}, {"id": 1595, "name": "Hush Beyond"}]}
            if url.endswith("/arc/482/issue_list/"):
                return {"results": self.ARC_ROWS, "next": None}
            if url.endswith("/arc/482/"):
                return {"name": "Hush", "desc": "A mystery villain dismantles Batman's life piece by piece, and the whole of his rogues' gallery is in on it."}
            raise AssertionError(url)
        store = Mock()
        store.runs_by_provider_series.return_value = by_metron or {}
        store.reading_list_by_provider.return_value = None
        return [patch.object(app, "fetch_provider_json", side_effect=fetch), patch.object(app, "_provider_credential", return_value="t"),
                patch.object(app, "metron_configured", return_value=True), patch.object(app, "catalog_store", return_value=store),
                patch.object(app, "_library_relevance", return_value=relevance or {})]

    def test_an_arc_is_found_by_name_and_listed_in_reading_order(self):
        library = {"runId": "7", "owned": {app._issue_key("608")}, "queued": {app._issue_key("609")}, "following": False}
        with contextlib.ExitStack() as stack:
            for item in self._patches(relevance={"batman|1940": library}):
                stack.enter_context(item)
            self.assertEqual([arc["name"] for arc in app.discover_story_arcs("hush")["arcs"]], ["Hush", "Hush Beyond"])
            detail = app.story_arc_detail("482")
        self.assertEqual([issue["number"] for issue in detail["issues"]], ["608", "609", "610", "1"], "by cover date")
        self.assertEqual([(issue["owned"], issue["queued"]) for issue in detail["issues"][:3]], [(True, False), (False, True), (False, False)])
        self.assertEqual((detail["missing"], detail["owned"]), (2, 1))
        self.assertEqual([(group["title"], group["missing"]) for group in detail["series"]],
                         [("Batman", 1), ("Batman: Gotham Knights", 1)])

    def test_an_arc_search_forgives_missing_small_words(self):
        asked = []

        def fetch(provider, url, credential, **_):
            asked.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("name", [""])[0])
            if asked[-1] == "realms":
                return {"results": [{"id": 70, "name": "War of the Realms"}, {"id": 900, "name": "Realms Fall"}]}
            return {"results": []}
        with patch.object(app, "fetch_provider_json", side_effect=fetch), patch.object(app, "_provider_credential", return_value="t"), \
                patch.object(app, "metron_configured", return_value=True), patch.object(app, "read_arc_index", return_value={}):
            found = app.discover_story_arcs("War of Realms")["arcs"]
        self.assertEqual(asked, ["War of Realms", "realms"], "Metron asked again for the telling word")
        self.assertEqual([arc["name"] for arc in found], ["War of the Realms"], "only arcs holding every word")
        # With the kept list, the match is found there, without asking again.
        asked.clear()
        index = {"metron": {"arcs": [{"id": "70", "name": "War of the Realms"}, {"id": "5", "name": "Secret Wars"}]}}
        with patch.object(app, "fetch_provider_json", side_effect=fetch), patch.object(app, "_provider_credential", return_value="t"), \
                patch.object(app, "metron_configured", return_value=True), patch.object(app, "read_arc_index", return_value=index):
            found = app.discover_story_arcs("realms war 2019")["arcs"]
        self.assertEqual(([arc["providerArcId"] for arc in found], asked), (["70"], ["realms war 2019"]))

    def test_arc_suggestions_come_from_the_kept_lists_and_lists_only_for_the_admin(self):
        index = {"metron": {"arcs": [{"id": "70", "name": "War of the Realms"}]},
                 "community": {"lists": [{"id": "Marvel/x.cbl", "name": "War of the Realms", "publisher": "Marvel"}]}}
        with patch.object(app, "ensure_arc_index", return_value=index), patch.object(app, "metron_configured", return_value=True), \
                patch.object(app, "fetch_provider_json", side_effect=AssertionError("no remote catalog while typing")):
            with patch.object(app, "cached_setting", return_value=True):
                admin = app.arc_suggestions("war of realms", include_lists=True)
                reader = app.arc_suggestions("war of realms", include_lists=False)
            with patch.object(app, "cached_setting", return_value=False):
                self.assertEqual(app.arc_suggestions("war of realms", include_lists=True)["lists"], [],
                                 "community lists are offered only once they are turned on")
        self.assertEqual((admin["arcs"][0]["providerArcId"], [item["name"] for item in admin["lists"]], admin["ready"]),
                         ("70", ["War of the Realms"], True))
        self.assertEqual(reader["lists"], [])

    def test_the_kept_arc_list_refreshes_each_part_on_its_own_and_keeps_the_last_on_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"FLIPPARR_ARC_INDEX": str(Path(folder) / "arc-index.json")}), \
                patch.object(app, "metron_configured", return_value=True), \
                patch.object(app, "_fetch_metron_arc_names", return_value={"fetchedAt": "2026-10-03T00:00:00+00:00", "arcs": [{"id": "70", "name": "War of the Realms"}]}), \
                patch.object(app, "_fetch_community_lists", side_effect=OSError("GitHub is down")) as community:
            with patch.object(app, "cached_setting", return_value=False):
                app.refresh_arc_index()
            community.assert_not_called()
            with patch.object(app, "cached_setting", return_value=True):
                app.refresh_arc_index()
            index = app.read_arc_index()
            self.assertEqual(index["metron"]["arcs"][0]["name"], "War of the Realms")
            self.assertTrue(index["community"]["triedAtEpoch"] and not index["community"].get("lists"))
            self.assertFalse(app._arc_part_stale(index["community"], time.time()), "a failure waits an hour before trying again")

    def test_pulling_an_arc_asks_for_what_is_missing_one_series_at_a_time(self):
        library = {"runId": "7", "owned": {app._issue_key("608")}, "queued": set(), "following": False}
        pulls = []
        def pull(provider, series_id, numbers, *, released=False, query=""):
            pulls.append((series_id, numbers, query))
            if series_id == "5000":
                raise RuntimeError("Metron is cooling down")
            return {"numbers": numbers, "request": {"id": 11}, "series": {"id": 7}}
        with contextlib.ExitStack() as stack:
            for item in self._patches(relevance={"batman|1940": library}):
                stack.enter_context(item)
            stack.enter_context(patch.object(app, "pull_discovered_issues", side_effect=pull))
            result = app.pull_story_arc("482")
        self.assertEqual(pulls, [("796", ["609", "610"], "Batman"), ("5000", ["1"], "Batman: Gotham Knights")])
        self.assertEqual((result["count"], [item["title"] for item in result["failed"]]), (2, ["Batman: Gotham Knights"]),
                         "one series failing does not stop the others")

    def test_an_arc_already_here_has_nothing_to_pull(self):
        everything = {"runId": "7", "owned": {app._issue_key(n) for n in ("608", "609", "610")}, "queued": set(), "following": False}
        gotham = {"runId": "8", "owned": {app._issue_key("1")}, "queued": set(), "following": False}
        with contextlib.ExitStack() as stack:
            for item in self._patches(relevance={"batman|1940": everything, "x": gotham}, by_metron={"5000": 8}):
                stack.enter_context(item)
            with self.assertRaisesRegex(ValueError, "in your library or on the way"):
                app.pull_story_arc("482")


class ReadingListTests(unittest.TestCase):
    """Story arcs saved to read across runs: Hush through Batman and Gotham
    Knights, with #609 missing from the library."""

    def _library(self, root):
        files = [
            ("Batman 608.cbz", "Batman", "608", None), ("Batman 610.cbz", "Batman", "610", None),
            ("Batman - Gotham Knights 001.cbz", "Batman: Gotham Knights", "1", None),
            ("Green Lantern 001 (2011).cbz", "Green Lantern", "1", 2011),
        ]
        parsed = []
        for name, title, issue, year in files:
            (root / name).write_bytes(b"comic")
            parsed.append(app.ParsedFile(str(root / name), name, ".cbz", title, issue=issue, year=year))
        by_path = {item.path: (item, year) for item, (_, _, _, year) in zip(parsed, files)}
        store = CatalogStore(root / "catalog.db")
        store.perform_scan(store.begin_scan(str(root), True), lambda *_: list(parsed), lambda p: {
            "parsed": p.__dict__, "lookup_identity": p.__dict__, "embedded_metadata": {},
            "file_health": {"status": "ok"},
            "recommendation": {"title": by_path[p.path][0].title, "issue": p.issue, "record_type": "single_issue",
                               "publisher": "DC", "source": "Test", "publication_year": by_path[p.path][1]},
            "file_cover": None,
        })
        return store

    def _ids(self, store):
        """By title: (run id, {number: file id}) and a file's live signature."""
        out = {}
        with sqlite3.connect(store.database_path) as connection:
            for run_id, title in connection.execute("SELECT id, canonical_title FROM series_runs"):
                files = {
                    str(number): (int(file_id), f"{mtime:x}-{size:x}") for number, file_id, mtime, size in connection.execute(
                        """SELECT issues.issue_number, files.id, files.mtime_ns, files.size_bytes FROM files
                           JOIN file_issue_links ON file_issue_links.file_id=files.id
                           JOIN issues ON issues.id=file_issue_links.issue_id WHERE issues.series_run_id=?""", (run_id,))
                }
                out[title] = (int(run_id), files)
        return out

    def _open(self, stack, store):
        seed = StoryArcTests("test_an_arc_already_here_has_nothing_to_pull")._patches()[:3]
        for item in seed:
            stack.enter_context(item)
        stack.enter_context(patch.object(app, "catalog_store", return_value=store))
        stack.enter_context(patch.object(app, "_start_automatic_release_grabs"))
        # The files are not archives; what matters here is the order, not the pages.
        stack.enter_context(patch.object(app, "archive_kind", return_value="zip"))

    def test_saving_an_arc_keeps_metrons_order_and_finds_the_librarys_own_issues(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            saved = app.save_story_arc("482", user_id=ADMIN_USER_ID)
            ids = self._ids(store)
            batman, gotham = ids["Batman"], ids["Batman: Gotham Knights"]
            self.assertEqual((saved["name"], saved["existed"], saved["provider"], saved["providerArcId"]), ("Hush", False, "metron", "482"))
            self.assertEqual([(i["seriesTitle"], i["number"]) for i in saved["items"]],
                             [("Batman", "608"), ("Batman", "609"), ("Batman", "610"), ("Batman: Gotham Knights", "1")], "as Metron listed them")
            self.assertEqual([i["fileId"] for i in saved["items"]],
                             [str(batman[1]["608"][0]), None, str(batman[1]["610"][0]), str(gotham[1]["1"][0])])
            self.assertEqual([i["runId"] for i in saved["items"]], [str(batman[0]), None, str(batman[0]), str(gotham[0])])
            self.assertEqual([(i["owned"], i["queued"]) for i in saved["items"]], [(True, False), (False, False), (True, False), (True, False)])
            self.assertEqual((saved["issueCount"], saved["owned"], saved["missing"]), (4, 3, 1))
            self.assertEqual([(g["title"], g["missing"]) for g in saved["series"]], [("Batman", 1), ("Batman: Gotham Knights", 0)])
            self.assertEqual(saved["resume"]["state"], "unstarted")
            self.assertEqual(saved["resume"]["fileId"], str(batman[1]["608"][0]))
            # Opening an arc never builds the catalog: the run drawer does not, and the arc's must not lag it.
            with patch.object(app, "_library_relevance", side_effect=AssertionError("the catalog was built")):
                again_detail = app.reading_list_detail(int(saved["id"]), user_id=ADMIN_USER_ID)
            self.assertEqual([i["owned"] for i in again_detail["items"]], [True, False, True, True])
            # "On the way" comes from the requests themselves.
            missing_issue = next(int(i["issueId"]) for i in again_detail["items"] if i["number"] == "609" and i["issueId"]) if any(i["number"] == "609" and i["issueId"] for i in again_detail["items"]) else None
            self.assertIsNone(missing_issue, "#609 is no issue the library knows, so nothing can be queued for it yet")
            self.assertEqual(saved["items"][0]["fileCover"], f"/api/v1/files/{batman[1]['608'][0]}/pages/0")
            again = app.save_story_arc("482", user_id=ADMIN_USER_ID)
            self.assertEqual((again["id"], again["existed"]), (saved["id"], True))
            self.assertEqual(app.story_arc_detail("482")["readingListId"], saved["id"], "Discover knows it is kept")
            listed = app.reading_lists()["lists"]
            self.assertEqual([(l["name"], l["issueCount"], l["owned"], l["missing"], l["seriesTitles"]) for l in listed],
                             [("Hush", 4, 3, 1, ["Batman", "Batman: Gotham Knights"])])
            self.assertEqual((listed[0]["year"], listed[0]["yearEnd"]), (2002, 2003), "the arc's years are its issues' cover dates, not Batman's 1940")
            self.assertEqual((again_detail["year"], again_detail["yearEnd"]), (2002, 2003))
            with self.assertRaises(ValueError):
                app.save_story_arc("hush", user_id=ADMIN_USER_ID)

    def test_reading_an_arc_goes_on_across_runs_and_past_the_gap(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            list_id = int(app.save_story_arc("482", user_id=ADMIN_USER_ID)["id"])
            ids = self._ids(store)
            f608, f610, gk1 = ids["Batman"][1]["608"], ids["Batman"][1]["610"], ids["Batman: Gotham Knights"][1]["1"]
            self.assertEqual(app.reading_by_list(user_id=ADMIN_USER_ID), {"lists": {}}, "nothing opened yet")
            store.set_reading_progress(f608[0], 23, 24, f608[1], finished=True, user_id=ADMIN_USER_ID)
            detail = app.reading_list_detail(list_id, user_id=ADMIN_USER_ID)
            self.assertEqual((detail["resume"]["state"], detail["resume"]["fileId"]), ("next", str(f610[0])), "past the missing #609")
            self.assertEqual([i["finishedAt"] is not None for i in detail["items"]], [True, False, False, False])
            place = app.reading_by_list(user_id=ADMIN_USER_ID)["lists"][str(list_id)]
            self.assertEqual((place["state"], place["read"], place["total"], place["issueNumber"]), ("next", 1, 3, "608"))
            store.set_reading_progress(gk1[0], 3, 24, gk1[1], user_id=ADMIN_USER_ID)
            place = app.reading_by_list(user_id=ADMIN_USER_ID)["lists"][str(list_id)]
            self.assertEqual((place["state"], place["fileId"], place["page"]), ("continue", str(gk1[0]), 3))
            self.assertEqual(app.reading_list_detail(list_id, user_id=ADMIN_USER_ID)["resume"]["state"], "continue")
            store.set_reading_progress(gk1[0], 23, 24, gk1[1], finished=True, user_id=ADMIN_USER_ID)
            store.set_reading_progress(f610[0], 23, 24, f610[1], finished=True, user_id=ADMIN_USER_ID)
            place = app.reading_by_list(user_id=ADMIN_USER_ID)["lists"][str(list_id)]
            self.assertEqual((place["state"], place["read"], place["total"]), ("finished", 3, 3))
            self.assertEqual(app.reading_by_list(user_id=99), {"lists": {}}, "another profile's shelf is untouched")
            # Files that are not archives are passed over, as a run's are.
            after = app.mark_reading_list_reading(list_id, False, user_id=ADMIN_USER_ID)
            self.assertEqual(after["id"], str(list_id))

    def test_an_arcs_header_background_is_chosen_from_its_own_comics_or_found(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            list_id = int(app.save_story_arc("482", user_id=ADMIN_USER_ID)["id"])
            ids = self._ids(store)
            f608, f610 = ids["Batman"][1]["608"][0], ids["Batman"][1]["610"][0]
            stack.enter_context(patch.object(app, "archive_page_members", return_value=["p01.jpg", "p02.jpg", "p03.jpg"]))
            stack.enter_context(patch.object(app, "_file_signature", return_value="sig-1"))
            stack.enter_context(patch.object(app, "automatic_backdrop_page", return_value="p02.jpg"))
            found = app.reading_list_backdrop(list_id)
            self.assertEqual((found["source"], found["fileId"], found["page"]), ("auto", str(f608), 1), "the arc's first comic lends a page")
            self.assertTrue(found["url"].startswith(f"/api/v1/files/{f608}/pages/1?"))
            chosen = app.choose_reading_list_backdrop(list_id, str(f610), 2)
            self.assertEqual((chosen["source"], chosen["fileId"], chosen["page"]), ("chosen", str(f610), 2))
            self.assertEqual(app.reading_list_backdrop(list_id)["source"], "chosen", "a chosen page stands")
            with self.assertRaisesRegex(ValueError, "not part of this story arc"):
                app.choose_reading_list_backdrop(list_id, str(ids["Green Lantern"][1]["1"][0]), 0)
            with self.assertRaisesRegex(ValueError, "not in this comic"):
                app.choose_reading_list_backdrop(list_id, str(f610), 7)
            store.clear_reading_list_backdrop(list_id)
            self.assertEqual(app.reading_list_backdrop(list_id)["source"], "auto", "cleared, a page is found again")

    def test_our_order_and_removals_survive_a_refresh(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            list_id = int(app.save_story_arc("482", user_id=ADMIN_USER_ID)["id"])
            items = [int(i["id"]) for i in store.reading_list(list_id)["items"]]
            store.reorder_reading_list(list_id, [items[3], items[0], items[1], items[2]])
            store.remove_reading_list_items(list_id, [items[1]])
            refreshed = app.refresh_reading_list(list_id, user_id=ADMIN_USER_ID)
            self.assertEqual((refreshed["added"], refreshed["updated"]), (0, 4))
            self.assertEqual([i["number"] for i in refreshed["items"]], ["1", "608", "610"], "our order, #609 still out")
            self.assertIsNotNone(refreshed["refreshedAt"])
            pulls = []
            stack.enter_context(patch.object(app, "pull_discovered_issues", side_effect=lambda provider, series_id, numbers, **kw: (
                pulls.append((series_id, numbers)) or {"numbers": numbers, "request": {"id": 3}, "series": {"id": 1}})))
            with self.assertRaisesRegex(ValueError, "in your library or on the way"):
                app.pull_reading_list(list_id, user_id=ADMIN_USER_ID)
            self.assertEqual(pulls, [], "a removed issue is never pulled for the arc")
            store.delete_reading_list(list_id)
            with self.assertRaises(LookupError):
                app.reading_list_detail(list_id, user_id=ADMIN_USER_ID)

    def test_a_run_of_the_same_name_from_another_era_is_not_the_arcs(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            made = store.create_reading_list("Absolute Power", provider="metron", provider_arc_id="1606", items=[
                {"provider": "metron", "providerIssueId": "g13", "providerSeriesId": "9001", "seriesTitle": "Green Lantern",
                 "seriesYear": 2023, "number": "1"},
                {"provider": "metron", "providerIssueId": "g1", "providerSeriesId": "9002", "seriesTitle": "Green Lantern",
                 "seriesYear": 2012, "number": "1"},
            ])
            detail = app.reading_list_detail(int(made["id"]), user_id=ADMIN_USER_ID)
            self.assertEqual([i["runId"] for i in detail["items"]],
                             [None, str(self._ids(store)["Green Lantern"][0])], "2023 is another comic; 2012 is a cover-date drift")

    def test_a_reader_sees_an_arc_without_the_runs_above_their_rating(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            list_id = int(app.save_story_arc("482", user_id=ADMIN_USER_ID)["id"])
            ids = self._ids(store)
            gotham = ids["Batman: Gotham Knights"]
            hidden = {"may_see": lambda run_id: run_id != gotham[0], "may_see_unrated": True}
            store.set_reading_progress(gotham[1]["1"][0], 3, 24, gotham[1]["1"][1], user_id=ADMIN_USER_ID)
            detail = app.reading_list_detail(list_id, user_id=ADMIN_USER_ID, **hidden)
            self.assertEqual([i["number"] for i in detail["items"]], ["608", "609", "610"])
            self.assertEqual(detail["resume"]["state"], "unstarted", "the hidden comic's place does not count")
            self.assertEqual(app.reading_lists(**hidden)["lists"][0]["seriesTitles"], ["Batman"])
            self.assertEqual(app.reading_by_list(user_id=ADMIN_USER_ID, **hidden), {"lists": {}})
            nothing = {"may_see": lambda run_id: False, "may_see_unrated": False}
            self.assertEqual(app.reading_lists(**nothing), {"lists": []})
            with self.assertRaises(LookupError):
                app.reading_list_detail(list_id, user_id=ADMIN_USER_ID, **nothing)
            unrated_hidden = {"may_see": lambda run_id: True, "may_see_unrated": False}
            self.assertEqual([i["number"] for i in app.reading_list_detail(list_id, user_id=ADMIN_USER_ID, **unrated_hidden)["items"]],
                             ["608", "610", "1"], "an issue no run claims follows the unrated rule")

    def test_approving_a_readers_arc_request_saves_the_arc_then_pulls_what_is_missing(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            sam = store.create_user("Sam")["id"]
            request, _ = store.create_member_request(sam, "discover_arc", "discover:arc:metron:482", "Hush", {"arcId": "482"})
            pulls = []
            stack.enter_context(patch.object(app, "pull_discovered_issues", side_effect=lambda provider, series_id, numbers, **kw: (
                pulls.append((series_id, numbers)) or {"numbers": numbers, "request": {"id": 31}, "series": {"id": 7}})))
            made, run = app._carry_out_member_request({**request, "params": {"arcId": "482"}})
            self.assertEqual((made, run, pulls), (31, 7, [("796", ["609"])]))
            saved = store.reading_lists_overview()
            self.assertEqual([(l["name"], l["createdBy"]) for l in saved], [("Hush", sam)])
            self.assertEqual(store.member_requests()[0]["detail"]["readingListId"], saved[0]["id"])
            # Asked for again once everything is here: saved already, nothing to pull, still approved.
            pulls.clear()
            stack.enter_context(patch.object(app, "_pull_missing_issues", return_value={"pulled": [], "failed": [], "count": 0}))
            self.assertEqual(app._carry_out_member_request({**request, "params": {"arcId": "482"}}), (None, None))


class ReadingListImportTests(ReadingListTests):
    """A reading list from a file or a link, kept as a story arc."""

    CBL = (b'<?xml version="1.0"?><ReadingList><Name>Hush (Batman)</Name><NumIssues>3</NumIssues><Books>'
           b'<Book Series="Batman" Number="608" Volume="1940" Year="2002"><Database Name="cv" Series="796" Issue="1"/></Book>'
           b'<Book Series="Batman" Number="609" Volume="1940" Year="2003"><Database Name="cv" Series="796" Issue="2"/></Book>'
           b'<Book Series="Green Lantern" Number="1" Volume="2023" Year="2023"><Database Name="cv" Series="9001" Issue="3"/></Book>'
           b'</Books></ReadingList>')

    def test_a_file_becomes_an_arc_matched_to_the_library_without_the_wrong_era(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            made = app.import_reading_list(data=self.CBL, filename="hush.cbl", user_id=ADMIN_USER_ID)
            ids = self._ids(store)
            self.assertEqual((made["name"], made["source"], made["sourceName"], made["existed"]), ("Hush (Batman)", "cbl", "Hush (Batman)", False))
            self.assertEqual([(i["seriesTitle"], i["number"], i["provider"], i["providerIssueId"]) for i in made["items"]],
                             [("Batman", "608", "comic_vine", "1"), ("Batman", "609", "comic_vine", "2"), ("Green Lantern", "1", "comic_vine", "3")])
            self.assertEqual([i["runId"] for i in made["items"]], [str(ids["Batman"][0]), None, None],
                             "Batman #608 by title; #609 is no issue the library knows; Green Lantern (2023) is not the 2011 run")
            self.assertEqual([i["owned"] for i in made["items"]], [True, False, False])
            self.assertEqual(made["cover"], f"/api/v1/files/{ids['Batman'][1]['608'][0]}/pages/0", "the first comic here stands in for a cover")
            with self.assertRaises(app.ReadingListDuplicate):
                app.import_reading_list(data=self.CBL, filename="hush.cbl", user_id=ADMIN_USER_ID)
            again = app.import_reading_list(data=self.CBL, filename="hush.cbl", user_id=ADMIN_USER_ID, replace_duplicate=True)
            self.assertNotEqual(again["id"], made["id"])
            with self.assertRaisesRegex(ValueError, "not a CBL"):
                app.import_reading_list(data=b"hello", filename="x.cbl", user_id=ADMIN_USER_ID)
            with self.assertRaisesRegex(ValueError, "nothing to refresh"):
                app.refresh_reading_list(int(made["id"]), user_id=ADMIN_USER_ID)
            self.assertEqual(app._reading_list_cover_choices(int(made["id"])), {f"/api/v1/files/{ids['Batman'][1]['608'][0]}/pages/0"},
                             "its own comics' first pages, and nothing typed")

    def test_a_linked_run_is_the_arcs_whatever_the_title_says(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            ids = self._ids(store)
            with sqlite3.connect(store.database_path) as connection:
                connection.execute(
                    "INSERT INTO series_provider_ids(series_run_id, provider, provider_id, confirmed, source, updated_at) VALUES (?, 'comic_vine', '9001', 1, 'test', 'now')",
                    (ids["Green Lantern"][0],))
            made = app.import_reading_list(data=self.CBL, filename="hush.cbl", user_id=ADMIN_USER_ID)
            self.assertEqual(made["items"][2]["runId"], str(ids["Green Lantern"][0]), "linked at Comic Vine, so the title's era does not decide")

    def test_a_link_is_fetched_once_kept_and_refreshed_but_never_from_inside_the_house(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            store = self._library(Path(folder))
            self._open(stack, store)
            fetched = []
            def fetch(url):
                fetched.append(url)
                return self.CBL, url
            stack.enter_context(patch.object(app, "_fetch_reading_list", side_effect=fetch))
            made = app.import_reading_list(url="https://example.invalid/hush.cbl", user_id=ADMIN_USER_ID)
            self.assertEqual((made["sourceUrl"], made["existed"], fetched), ("https://example.invalid/hush.cbl", False, ["https://example.invalid/hush.cbl"]))
            same = app.import_reading_list(url="https://example.invalid/hush.cbl", user_id=ADMIN_USER_ID)
            self.assertEqual((same["id"], same["existed"], len(fetched)), (made["id"], True, 1), "the same link is the same arc")
            refreshed = app.refresh_reading_list(int(made["id"]), user_id=ADMIN_USER_ID)
            self.assertEqual((refreshed["added"], refreshed["updated"], len(fetched)), (0, 3, 2))
        for bad in ("file:///etc/hosts", "ftp://x/y", "", "http://127.0.0.1/list.cbl", "http://localhost/list.cbl", "http://10.0.0.5/a.cbl"):
            with self.assertRaises(ValueError, msg=bad):
                app._fetch_reading_list(bad)
        with patch.object(app.socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            with patch.object(app.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = b"x" * (app.READING_LIST_IMPORT_MAX_BYTES + 1)
                with self.assertRaisesRegex(ValueError, "larger than 2 MB"):
                    app._fetch_reading_list("https://example.invalid/big.cbl")
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = self.CBL
                data, url = app._fetch_reading_list("https://github.com/DieselTech/CBL-ReadingLists/blob/main/DC/x.cbl")
                self.assertEqual((data, url), (self.CBL, "https://raw.githubusercontent.com/DieselTech/CBL-ReadingLists/main/DC/x.cbl"),
                                 "a GitHub page link is read as the raw file")
        self.assertEqual(app._reading_list_link("https://github.com/D/L/blob/main/DC/2023 - Dawn/[DC Comics] Absolute Power (WEB-CBRO).cbl"),
                         "https://raw.githubusercontent.com/D/L/main/DC/2023%20-%20Dawn/%5BDC%20Comics%5D%20Absolute%20Power%20(WEB-CBRO).cbl",
                         "spaces and brackets as a browser bar shows them")
        self.assertEqual(app._reading_list_link("https://x.invalid/a%20b.cbl"), "https://x.invalid/a%20b.cbl", "already encoded stays so")


class DirectSitePartsTests(unittest.TestCase):
    """A download-site post in parts: the part that holds the wanted issue is the
    one taken, not the first button on the page."""

    def setUp(self):
        # Links are taken only from the site the operator entered.
        home = patch("app._direct_site_home", return_value="https://comics.example")
        home.start()
        self.addCleanup(home.stop)

    PAGE = (
        '<p><strong>The Woods #1 &#8211; 36 + TPB Vol. 1 &#8211; 9</strong></p><p>Language : English | Year : 2014-2018 | Size : 3.6 GB</p>'
        '<p><a href="https://comics.example/read/1">Read Online</a></p>'
        '<p><strong>The Woods #1 &#8211; 12 (2014-2015) (474 MB) : :</strong> <a href="https://comics.example/dls/a1">Main Server</a> | '
        '<a href="https://comics.example/dls/a2">Google Drive</a> | <a href="https://mega.nz/x">Mega</a></p>'
        '<p><strong>The Woods #13 &#8211; 23 (2015-2016) (470 MB) : :</strong> <a href="https://comics.example/dls/b1">Main Server</a> | '
        '<a href="https://comics.example/dls/b2">Google Drive</a> | <a href="https://mega.nz/y">Mega</a> | <span>Mediafire</span> | <span>Zippyshare</span></p>'
        '<p><strong>The Woods #34 &#8211; 36 (2017) (262 MB) : :</strong> <a href="https://comics.example/dls/d1">Main Server</a></p>'
        '<p><strong>TPBs</strong></p><p><strong>The Woods Vol. 1 &#8211; The Arrow (2014) (181 MB) : :</strong> <a href="https://comics.example/dls/t1">Main Server</a></p>'
    )

    def test_each_link_knows_its_part(self):
        links = app.direct_site_download_links(self.PAGE)
        self.assertEqual([(l["url"][-2:], l["first"], l["last"]) for l in links],
                         [("a1", 1, 12), ("a2", 1, 12), ("b1", 13, 23), ("b2", 13, 23), ("d1", 34, 36), ("t1", None, None)])
        self.assertIn("#13 – 23", links[2]["label"])
        self.assertTrue(links[2]["label"].startswith("The Woods #13"), links[2]["label"])
        self.assertNotIn("Google Drive", links[3]["label"], "the buttons' host names are not the label")

    def test_the_part_holding_the_wanted_issue_is_taken(self):
        with patch.object(app, "solver_fetch_html", return_value=self.PAGE):
            self.assertEqual(app.direct_site_download_link("https://comics.example/other-comics/the-woods-1-12/", "21"), "https://comics.example/dls/b1")
            self.assertEqual(app.direct_site_download_link("https://comics.example/other-comics/the-woods-1-12/", "034"), "https://comics.example/dls/d1")
            self.assertEqual(app.direct_site_download_link("https://comics.example/other-comics/the-woods-1-12/", "5"), "https://comics.example/dls/a1")
            self.assertEqual(app.direct_site_download_link("https://comics.example/other-comics/the-woods-1-12/", None), "https://comics.example/dls/a1", "no issue asked: the first")
            with self.assertRaisesRegex(ValueError, "in parts, and none says it holds #30"):
                app.direct_site_download_link("https://comics.example/other-comics/the-woods-1-12/", "30")
        single = '<p><strong>Saga #61 (2022) : :</strong> <a href="https://comics.example/dls/s1">Main Server</a></p>'
        with patch.object(app, "solver_fetch_html", return_value=single):
            self.assertEqual(app.direct_site_download_link("https://comics.example/saga-61/", "61"), "https://comics.example/dls/s1")
            self.assertEqual(app.direct_site_download_link("https://comics.example/saga-61/", "3"), "https://comics.example/dls/s1",
                             "one part is the post; the importer judges what it holds")
        with patch.object(app, "solver_fetch_html", return_value="<p>nothing here</p>"):
            with self.assertRaisesRegex(ValueError, "no direct download link"):
                app.direct_site_download_link("https://comics.example/x/", "1")

    # A name's runs collected in one post: both parts hold a #12.
    COLLECTION = (
        '<p><strong>R.E.B.E.L.S. Vol. 1 &#8211; 2 (Collection) (1994-2011)</strong></p>'
        '<p>Language : English | Year : 1994-2011 | Size : 1.5 GB</p>'
        '<p><strong>R.E.B.E.L.S. Vol. 1 #0 &#8211; 17 (1994-1996) (517 MB) : :</strong> <a href="https://comics.example/dls/v1a">Main Server</a> | '
        '<a href="https://comics.example/dls/v1b">Mega</a></p>'
        '<p><strong>R.E.B.E.L.S. Vol. 2 #1 &#8211; 28 + Annual (2009-2011) (943 MB) : :</strong> <a href="https://comics.example/dls/v2a">Main Server</a> | '
        '<a href="https://comics.example/dls/v2b">Mega</a></p>'
    )

    def test_a_part_knows_its_years_and_size_and_not_the_posts(self):
        links = app.direct_site_download_links(self.COLLECTION)
        self.assertEqual(links[0]["label"], "R.E.B.E.L.S. Vol. 1 #0 – 17 (1994-1996) (517 MB)",
                         "the post's size fact does not lead the first label")
        self.assertEqual([(l["first"], l["last"], l["years"]) for l in links],
                         [(0, 17, (1994, 1996)), (0, 17, (1994, 1996)), (1, 28, (2009, 2011)), (1, 28, (2009, 2011))])
        self.assertEqual(links[2]["sizeBytes"], 943 * 1024 ** 2)

    def test_the_part_from_this_runs_years_is_taken(self):
        # The first part holding #12 was the 1994 run's (R.E.B.E.L.S., 2026-09-29).
        with patch.object(app, "solver_fetch_html", return_value=self.COLLECTION):
            self.assertEqual(app.direct_site_download_link("https://comics.example/dc/rebels/", "12", (2009, 2010)), "https://comics.example/dls/v2a")
            self.assertEqual(app.direct_site_download_link("https://comics.example/dc/rebels/", "12", (1995, None)), "https://comics.example/dls/v1a")
            self.assertEqual(app.direct_site_download_link("https://comics.example/dc/rebels/", "12", None), "https://comics.example/dls/v1a",
                             "no year known: the first part holding it")
            with self.assertRaisesRegex(ValueError, "holding #0 is from other years"):
                app.direct_site_download_link("https://comics.example/dc/rebels/", "0", (2009,))
            with self.assertRaisesRegex(ValueError, "from other years than this run: R.E.B.E.L.S. Vol. 1"):
                app.direct_site_download_link("https://comics.example/dc/rebels/", "12", (2020,))
        self.assertTrue(app._direct_site_part_fits_years("The Woods #13 – 23 (470 MB)", (2014,)), "a part naming no years is not doubted")


class DirectSiteGrabIdentityTests(unittest.TestCase):
    def test_two_jobs_may_take_parts_of_one_post(self):
        """The download identity was the post alone, so the second job taking
        another part of the same post hit the table's UNIQUE (The Woods #34
        after #21, 2026-09-28)."""
        store = Mock()
        store.record_acquisition_download.side_effect = lambda job_id, key, *a, **k: {"id": job_id, "sab_nzo_id": key}
        post = "https://comics.example/other-comics/the-woods-1-12/"
        far = time.time() + 600
        with patch.object(app, "catalog_store", return_value=store), patch.object(app, "_enabled_acquisition_service"), \
             patch.object(app, "_start_direct_fetch"), patch.dict(app._RELEASE_CANDIDATES, {
                 "c21": {"jobId": 1714, "postUrl": post, "title": "The Woods pack", "expiresAt": far},
                 "c34": {"jobId": 1715, "postUrl": post, "title": "The Woods pack", "expiresAt": far}}):
            app.grab_direct_site_release(1714, "c21")
            app.grab_direct_site_release(1715, "c34")
        keys = [call.args[1] for call in store.record_acquisition_download.call_args_list]
        self.assertEqual(len(set(keys)), 2, keys)
        self.assertTrue(keys[0].startswith("direct_site:") and keys[0].endswith(":1714") and keys[1].endswith(":1715"), keys)


class NotificationViewTests(unittest.TestCase):
    def test_an_arrival_shows_the_comics_own_first_page(self):
        viewer = Viewer(id=1, name="Admin", role="admin")
        row = {"id": 4, "kind": "arrived", "updatedAt": "2026-09-28T10:00:00+00:00", "readAt": None,
               "payload": {"runId": 9, "runTitle": "Batman", "items": [{"key": "14", "number": "14", "title": "The Zoo", "fileId": 1847}]}}
        view = app.notification_view(row, viewer)
        # `/cover/image` is a cover someone uploaded, 404 for every ordinary comic.
        self.assertEqual(view["cover"], "/api/v1/files/1847/pages/0")
        self.assertEqual((view["title"], view["detail"]), ("Batman #14", "The Zoo"))
        many = app.notification_view({**row, "payload": {**row["payload"], "items": [
            {"key": str(n), "number": str(n), "fileId": None} for n in (2, 3, 4)]}}, viewer)
        self.assertEqual((many["title"], many["detail"], many["cover"]), ("Batman: 3 new issues", "#2, #3 and #4", None))


class DirectDownloadRecoveryTests(unittest.TestCase):
    """A download Flipparr fetches itself survives a restart and can be stopped."""

    def setUp(self):
        app._DIRECT_FETCHES.clear()
        app._DIRECT_FETCHES_RESTARTED.clear()
        self.addCleanup(app._DIRECT_FETCHES.clear)
        self.addCleanup(app._DIRECT_FETCHES_RESTARTED.clear)

    def test_a_restart_starts_the_fetch_again_from_the_row(self):
        store = Mock()
        store.direct_downloads_in_flight.return_value = [
            {"id": 1438, "job_id": 1620, "source": "direct_site", "status": "queued",
             "release_key": "https://comics.example/dc/wf-36/", "release_title": "World's Finest #36"},
        ]
        started = []
        class Thread:
            def __init__(self, target, args, **_):
                started.append((target, args))
            def start(self):
                pass
        with patch("app.catalog_store", return_value=store), patch("app.threading.Thread", Thread):
            self.assertEqual(app.recover_interrupted_direct_downloads(), 1)
            self.assertEqual(app.recover_interrupted_direct_downloads(), 0, "not twice while the thread is live")
        self.assertEqual(started, [(app._fetch_direct_site_download, (1438, 1620, "https://comics.example/dc/wf-36/", "World's Finest #36"))])
        # The reconciler starts an orphan again too, once.
        app._DIRECT_FETCHES.clear()
        app._DIRECT_FETCHES_RESTARTED.clear()
        row = {"id": 1438, "job_id": 1620, "source": "direct_site", "status": "downloading",
               "release_key": "https://comics.example/dc/wf-36/", "release_title": "World's Finest #36"}
        with patch("app.catalog_store", return_value=store), patch("app.threading.Thread", Thread):
            self.assertEqual(app.reconcile_acquisition_download(row)["status"], "downloading")
            app.reconcile_acquisition_download(row)
        self.assertEqual(len(started), 2, "the reconciler restarted it once, not on every pass")

    def test_an_import_a_restart_cut_off_is_done_again(self):
        # Gate 3 (2026-10-05): the row said "importing" when the process
        # went; nothing picked it up again and the issue stayed grabbed.
        store = Mock()
        row = {"id": 1438, "job_id": 1620, "source": "direct_site", "status": "importing",
               "sab_storage": "/config/tmp/direct_site/1438", "release_key": "k", "release_title": "World's Finest #36"}
        with patch("app.catalog_store", return_value=store), \
                patch("app._import_completed_download", return_value={"status": "imported"}) as importer:
            self.assertEqual(app.reconcile_acquisition_download(row)["status"], "imported")
        importer.assert_called_once_with(store, row, "/config/tmp/direct_site/1438")
        # Without the fetched folder there is nothing to import from.
        with patch("app.catalog_store", return_value=store), patch("app._import_completed_download") as importer:
            app.reconcile_acquisition_download({**row, "sab_storage": None})
        importer.assert_not_called()

    def test_stopping_a_download_frees_the_issue_and_sets_the_release_aside(self):
        store = Mock()
        store.acquisition_download_for_job.return_value = {
            "id": 1438, "job_id": 1620, "source": "direct_site", "status": "downloading",
            "release_key": "https://comics.example/dc/wf-36/", "release_title": "World's Finest #36",
        }
        store.stop_acquisition_download.return_value = {"id": "1620", "status": "queued", "action": "research"}
        flag = threading.Event()
        app._DIRECT_FETCHES[1438] = flag
        with patch("app.catalog_store", return_value=store), patch("app._sab_remove_job") as sab, \
                patch("app._search_again_after_stop") as again:
            result = app.stop_acquisition_download(1620)
        self.assertEqual(result["status"], "queued")
        again.assert_called_once_with([1620])
        self.assertTrue(flag.is_set(), "the fetch thread is told")
        sab.assert_not_called()
        store.record_acquisition_release_failure.assert_called_once_with(
            1620, "https://comics.example/dc/wf-36/", "World's Finest #36", "Stopped by you", kind="lost")
        # A SABnzbd download is removed from SABnzbd instead.
        store.acquisition_download_for_job.return_value = {"id": 9, "job_id": 7, "source": "sabnzbd", "status": "downloading",
                                                           "release_key": "k", "release_title": "Saga 001"}
        with patch("app.catalog_store", return_value=store), patch("app._sab_remove_job") as sab, \
                patch("app._search_again_after_stop"):
            app.stop_acquisition_download(7)
        sab.assert_called_once()
        store.acquisition_download_for_job.return_value = None
        with patch("app.catalog_store", return_value=store), self.assertRaises(ValueError):
            app.stop_acquisition_download(8)


    def test_stopping_a_torrent_pack_stops_every_issue_in_it_and_looks_again_once(self):
        info_hash = "ab" * 20
        rows = {job: {"id": 100 + job, "job_id": job, "source": "qbittorrent", "status": "downloading",
                      "sab_nzo_id": f"torrent:{info_hash}:{job}", "release_key": "pack",
                      "release_title": "Saga #1-54"} for job in (5, 6, 7)}
        store = Mock()
        store.acquisition_download_for_job.side_effect = lambda job: rows.get(int(job))
        store.torrent_jobs.return_value = [5, 6, 7]
        store.stop_acquisition_download.side_effect = lambda job: {"id": str(job), "status": "queued"}
        with patch("app.catalog_store", return_value=store), patch("app._release_torrent") as release, \
                patch("app._qbittorrent_client") as client, patch("app._search_again_after_stop") as again:
            result = app.stop_acquisition_pack(6)
        self.assertEqual(result["stopped"], ["5", "6", "7"])
        self.assertEqual([call.args[0] for call in store.stop_acquisition_download.call_args_list], [5, 6, 7])
        self.assertEqual([call.args[0] for call in store.record_acquisition_release_failure.call_args_list], [5, 6, 7],
                         "the pack is set aside for every issue, so no lead takes it again")
        release.assert_called_once_with(store, info_hash)
        client.assert_not_called()
        again.assert_called_once_with([5, 6, 7])
        # Not a torrent: the one download is the pack, stopped as before.
        store.acquisition_download_for_job.side_effect = None
        store.acquisition_download_for_job.return_value = {"id": 9, "job_id": 4, "source": "sabnzbd", "status": "downloading",
                                                           "release_key": "k", "release_title": "Saga #1-54"}
        with patch("app.catalog_store", return_value=store), patch("app._sab_remove_job"), \
                patch("app._search_again_after_stop") as again:
            self.assertEqual(app.stop_acquisition_pack(4)["stopped"], ["4"])
        again.assert_called_once_with([4])

    def test_what_was_stopped_is_looked_for_again_at_once_a_run_together(self):
        store = Mock()
        store.get_acquisition_job_context.side_effect = lambda job: {"requestId": "3" if job < 10 else "4"}
        started = []
        class Thread:
            def __init__(self, target, args=(), kwargs=None, **_):
                started.append((args, kwargs))
            def start(self):
                pass
        with patch("app.catalog_store", return_value=store), patch("app._acquisition_services_ready", return_value=True), \
                patch("app.threading.Thread", Thread):
            app._search_again_after_stop([5, 6, 11])
        self.assertEqual(started, [((3,), {"skip_run_pack": False}), ((4,), {"skip_run_pack": True})],
                         "a pack's issues may find another pack; one issue looks for itself")


class DirectSiteSingleFallbackTests(unittest.TestCase):
    """Usenet has nothing; the download site has the very issue, strongly. Taken --
    as a run's pack from there already is -- rather than left on a list."""

    def _run(self, found, flaresolverr=True):
        store = Mock()
        store.rejected_acquisition_release_keys.return_value = {"https://comics.example/refused"}
        context = {"requestId": "78", "seriesTitle": "Batman / Superman: World's Finest", "issueNumber": "32"}
        services = (lambda name: {}) if flaresolverr else Mock(side_effect=ValueError("off"))
        with patch("app.catalog_store", return_value=store), patch("app._issue_year_gate", return_value=None), \
             patch("app.search_prowlarr_releases", return_value={"candidates": [], "job": context}), \
             patch("app._enabled_acquisition_service", services), \
             patch("app._direct_site_candidates", return_value=found) as searched, \
             patch("app.grab_release_candidate", return_value={"status": "grabbed"}) as grab:
            result = app._auto_grab_release(1616)
        return result, grab, searched

    def test_a_strong_single_is_taken_when_usenet_has_nothing(self):
        strong = {"id": "gc-32", "title": "Batman Superman World's Finest #32", "matchScore": 92, "postUrl": "https://comics.example/wf-32"}
        result, grab, _ = self._run([strong])
        grab.assert_called_once_with(1616, "gc-32")
        self.assertEqual(result["release"], strong)

    def test_weak_refused_and_pack_results_are_left_for_a_person(self):
        weak = {"id": "gc-weak", "title": "Batman Superman World's Finest #23", "matchScore": 60, "postUrl": "https://comics.example/wf-23"}
        refused = {"id": "gc-refused", "title": "Batman Superman World's Finest #32", "matchScore": 95, "postUrl": "https://comics.example/refused"}
        pack = {"id": "gc-pack", "title": "World's Finest #1-40", "matchScore": 88, "postUrl": "https://comics.example/wf-pack",
                "pack": {"first": 1, "last": 40}}
        result, grab, _ = self._run([weak, refused, pack])
        self.assertIsNone(result)
        grab.assert_not_called()

    def test_nothing_is_asked_of_direct_site_without_a_way_to_download_from_it(self):
        strong = {"id": "gc-32", "title": "x", "matchScore": 92, "postUrl": "https://comics.example/wf-32"}
        result, grab, searched = self._run([strong], flaresolverr=False)
        self.assertIsNone(result)
        searched.assert_not_called()
        grab.assert_not_called()


class RunRatingTests(unittest.TestCase):
    """A run's rating: Metron's stricter reading, else its cover, else none."""

    def _store(self, metron_id="5"):
        store = Mock()
        store.confirmed_series_provider_ids.return_value = {"metron": metron_id} if metron_id else {}
        store.newest_file_of_run.return_value = 42
        return store

    def test_metron_says_first_and_the_stricter_issue_wins(self):
        store = self._store()
        with patch.object(app, "catalog_store", return_value=store), patch.object(app, "metron_configured", return_value=True), \
                patch.object(app, "_provider_credential", return_value="t"), \
                patch.object(app, "_metron_run_readings", return_value=[("Teen", "1"), ("Teen Plus", "24")]), \
                patch.object(app, "ask_vision_model") as vision:
            self.assertEqual(app.find_run_rating(7), ("teen_plus", "metron", "Teen (issue 1), Teen Plus (issue 24)"))
        vision.assert_not_called()

    def test_a_long_runs_latest_issue_comes_from_its_last_page(self):
        # As Metron answered for Batman (2016), 2026-09-27: 163 issues, 100 a page.
        base = f"{app.METRON_API_BASE}/issue/"
        first = {"count": 163, "next": f"{base}?page=2&series_id=93",
                 "results": [{"id": n, "number": str(n), "cover_date": f"2016-{n:04d}"} for n in range(1, 101)]}
        last = {"count": 163, "next": None,
                "results": [{"id": n, "number": str(n), "cover_date": f"2020-{n:04d}"} for n in range(101, 164)]}
        pages = {f"{base}?series_id=93": first, f"{base}?page=2&series_id=93": last}
        issues = {f"{base}1/": {"number": "1", "rating": {"name": "Teen"}},
                  f"{base}163/": {"number": "163", "rating": {"name": "Teen Plus"}}}
        with patch.object(app, "fetch_provider_json", side_effect=lambda _p, url, _c: pages.get(url) or issues[url]) as fetch:
            self.assertEqual(app._metron_run_readings("93", "t"), [("Teen", "1"), ("Teen Plus", "163")])
        self.assertEqual(fetch.call_count, 4, "the first page, the last, and the two issues -- no pages between")
        self.assertIsNone(app._metron_last_page_url({"count": 15, "next": None}, 15), "one page is all there is")

    def test_a_cover_is_read_only_when_asked_and_metron_says_nothing(self):
        store = self._store()
        unknown = patch.object(app, "_metron_run_readings", return_value=[("Unknown", "1")])
        with patch.object(app, "catalog_store", return_value=store), patch.object(app, "metron_configured", return_value=True), \
                patch.object(app, "_provider_credential", return_value="t"), unknown, \
                patch.object(app, "vision_provider", return_value="anthropic"), \
                patch.object(app, "_cover_for_rating", return_value=b"jpeg"), \
                patch.object(app, "ask_vision_model", return_value="13+ TEEN") as vision:
            with patch.object(app, "load_app_settings", return_value={"ratingsFromCovers": False}):
                self.assertEqual(app.find_run_rating(7), (None, None, None))
            vision.assert_not_called()
            with patch.object(app, "load_app_settings", return_value={"ratingsFromCovers": True}):
                self.assertEqual(app.find_run_rating(7), ("teen", "cover", "\u201c13+ TEEN\u201d on the cover"))
        store.newest_file_of_run.assert_called_with(7)

    def test_a_failed_lookup_records_no_rating_but_goes_to_the_back_of_the_queue(self):
        store = self._store()
        store.runs_needing_rating.return_value = [7, 8]
        def find(run_id):
            if run_id == 7:
                raise app.VisionUnavailable("out of credit")
            return ("mature", "metron", "Mature (issue 1)")
        with patch.object(app, "catalog_store", return_value=store), patch.object(app, "find_run_rating", side_effect=find):
            self.assertEqual(app.rate_runs_pass(), 1)
        store.record_run_rating.assert_called_once_with(8, "mature", "metron", "Mature (issue 1)")
        # Left unrecorded it came first in every batch and starved the rest.
        store.note_rating_check_failed.assert_called_once_with(7, "out of credit")

    def test_a_requests_cover_comes_only_from_a_providers_own_host(self):
        self.assertEqual(app._cover_url("https://static.metron.cloud/media/issue/2024/x.jpg"),
                         "https://static.metron.cloud/media/issue/2024/x.jpg")
        self.assertIsNone(app._cover_url("https://evil.example/pixel.gif"), "a reader must not choose what the admin's browser fetches")
        self.assertIsNone(app._cover_url("http://static.metron.cloud/x.jpg"))
        self.assertIsNone(app._cover_url("javascript:alert(1)"))


class MemberRequestApprovalTests(unittest.TestCase):
    """Approving a reader's request is the admin's own call for it."""

    def _store(self, kind, params):
        store = Mock()
        request = {"id": 7, "kind": kind, "params": params, "requestedBy": {"id": 2}}
        store.claim_member_request.return_value = request
        store.record_member_request_outcome.side_effect = lambda request_id, **outcome: {"id": request_id, **outcome}
        return store

    def test_a_discover_request_approved_is_discovers_own_add_and_pull(self):
        store = self._store("discover_run", {"provider": "metron", "providerSeriesId": "42", "query": "Found"})
        with patch.object(app, "catalog_store", return_value=store), \
                patch.object(app, "request_discovered_series",
                             return_value={"request": {"id": 11}, "series": {"id": 5}}) as add:
            outcome = app.approve_member_request(7, 1)
        add.assert_called_once_with("metron", "Found", "42", "either")
        self.assertEqual((outcome["acquisition_request_id"], outcome["series_run_id"]), (11, 5))
        store = self._store("discover_issues", {"provider": "metron", "providerSeriesId": "42", "query": "Found",
                                                "numbers": ["1", "2"], "released": False})
        with patch.object(app, "catalog_store", return_value=store), \
                patch.object(app, "pull_discovered_issues",
                             return_value={"request": {"id": 12}, "series": {"id": 5}}) as pull:
            app.approve_member_request(7, 1)
        pull.assert_called_once_with("metron", "42", ["1", "2"], released=False, query="Found")

    def test_an_approval_that_fails_is_kept_to_try_again(self):
        store = self._store("discover_run", {"provider": "metron", "providerSeriesId": "42", "query": "Found"})
        with patch.object(app, "catalog_store", return_value=store), \
                patch.object(app, "request_discovered_series", side_effect=RuntimeError("Metron is down")):
            outcome = app.approve_member_request(7, 1)
        self.assertEqual(outcome["failure"], "Metron is down")
        store.claim_member_request.assert_called_once_with(7, 1, "approved")


class ProfileTokenTests(unittest.TestCase):
    """The cookies that say which profile a request speaks for."""

    config = {"sessionSecret": "a secret", "username": "owner", "passwordHash": ""}

    def _store(self, **user):
        store = Mock()
        record = {"id": 2, "name": "Sam", "role": "reader", "colour": None, "canRequest": True,
                  "autoApprove": False, "disabled": False, "sessionVersion": 3, **user}
        store.user.side_effect = lambda user_id: dict(record) if user_id == record["id"] else None
        return store, record

    def test_a_profile_cookie_speaks_for_its_profile_until_its_version_moves_on(self):
        store, record = self._store()
        token = app.issue_profile_token(record, self.config, now=1000)
        viewer, legacy = app.resolve_session_token(token, self.config, store, now=2000)
        self.assertEqual((viewer.id, viewer.role, legacy), (2, "reader", False))
        moved, _ = self._store(sessionVersion=4)
        self.assertIsNone(app.resolve_session_token(token, self.config, moved, now=2000)[0], "revoked")
        off, _ = self._store(disabled=True)
        self.assertIsNone(app.resolve_session_token(token, self.config, off, now=2000)[0], "switched off")
        self.assertIsNone(app.resolve_session_token(token, self.config, store, now=1000 + app._SESSION_TTL_SECONDS + 1)[0],
                          "expired")
        forged = token.replace("v2.2.", "v2.1.")
        self.assertIsNone(app.resolve_session_token(forged, self.config, store, now=2000)[0], "another profile's id")
        self.assertIsNone(app.resolve_session_token(token, {**self.config, "sessionSecret": "other"}, store, now=2000)[0])

    def test_a_device_cookie_is_not_a_session_and_a_session_is_not_a_device(self):
        store, record = self._store()
        device = app.issue_device_token(self.config, now=1000)
        self.assertTrue(app.device_token_valid(device, self.config, now=2000))
        self.assertIsNone(app.resolve_session_token(device, self.config, store, now=2000)[0])
        session = app.issue_profile_token(record, self.config, now=1000)
        self.assertFalse(app.device_token_valid(session, self.config, now=2000))
        self.assertFalse(app.device_token_valid(device, self.config, now=1000 + app._DEVICE_TTL_SECONDS + 1))

    def test_a_new_household_epoch_ends_shared_devices_and_the_profiles_opened_on_them(self):
        store, record = self._store()
        device = app.issue_device_token(self.config, now=1000)
        shared = app.issue_profile_token(record, self.config, now=1000, shared=True)
        own = app.issue_profile_token(record, self.config, now=1000)
        self.assertTrue(shared.startswith("v3.2.3.0."))
        self.assertEqual(app.resolve_session_token(shared, self.config, store, now=2000)[0].id, 2)
        moved = {**self.config, "householdEpoch": 1}
        self.assertFalse(app.device_token_valid(device, moved, now=2000))
        self.assertIsNone(app.resolve_session_token(shared, moved, store, now=2000)[0])
        self.assertEqual(app.resolve_session_token(own, moved, store, now=2000)[0].id, 2, "a sign-in of one's own stays")
        forged = shared.replace("v3.2.3.0.", "v3.2.3.1.")
        self.assertIsNone(app.resolve_session_token(forged, moved, store, now=2000)[0], "the epoch is signed")
        # A device cookie from before epochs counts until the first forgetting.
        old = f"d1.5000.{app._signed(self.config, 'device|5000')}"
        self.assertTrue(app.device_token_valid(old, self.config, now=2000))
        self.assertFalse(app.device_token_valid(old, moved, now=2000))
        # So does the admin's cookie from before profiles.
        admin_store = Mock()
        admin_store.user.return_value = {"id": 1, "name": "Admin", "role": "admin", "disabled": False, "sessionVersion": 0}
        legacy = app.issue_session_token(self.config)
        self.assertEqual(app.resolve_session_token(legacy, self.config, admin_store)[0].id, 1)
        self.assertIsNone(app.resolve_session_token(legacy, moved, admin_store)[0], "not once the shared devices were forgotten")

    def test_a_pin_slows_down_after_a_few_wrong_guesses(self):
        app._LOGIN_THROTTLE.clear()
        self.addCleanup(app._LOGIN_THROTTLE.clear)
        store = Mock()
        pin_hash = app.hash_password("2468")
        store.user.return_value = {"id": 2, "name": "Admin two", "role": "admin", "disabled": False,
                                   "hasPin": True, "hasPassword": False, "switchLock": "pin", "pinLength": 4}
        store.user_secrets.return_value = {"pinHash": pin_hash, "passwordHash": None}
        self.assertEqual(app.check_profile_switch(store, self.config, 2, pin="2468")["id"], 2, "the right PIN opens it")
        for _ in range(app._LOGIN_FREE_FAILURES):
            with self.assertRaises(PermissionError):
                app.check_profile_switch(store, self.config, 2, pin="0000")
        with self.assertRaises(app.Throttled, msg="after that, even the right PIN waits its turn"):
            app.check_profile_switch(store, self.config, 2, pin="2468")


class FakeQbittorrent:
    """An in-memory qBittorrent: torrents by hash, each with state, tags and files."""

    def __init__(self, selective=True):
        self.torrents = {}
        self.selective = selective
        self.added = []
        self.deleted = []
        self.paused = []
        self.started = []

    def put(self, info_hash, *, state="stoppedDL", tags=(), files=(), save_path="/data/torrents/complete/comics",
            content_path=None, added_on=None, **extra):
        self.torrents[info_hash] = {
            "hash": info_hash, "state": state, "tags": ", ".join(tags), "save_path": save_path,
            "content_path": content_path or f"{save_path}/Pack", "added_on": added_on or int(time.time()) - 3600,
            "num_seeds": 5, "progress": 0.0, "amount_left": 0, "eta": 8640000, **extra,
        }
        self.torrents[info_hash]["files"] = [
            {"index": index, "name": name, "size": size, "priority": 1, "progress": 0}
            for index, (name, size) in enumerate(files)
        ]
        return self.torrents[info_hash]

    def supports_selection(self):
        return self.selective

    def version(self):
        return "v5.0.2" if self.selective else "v4.4.5"

    def info(self, *, hashes=None, tag=None, category=None, filter=None):  # noqa: A002
        found = [dict(t) for t in self.torrents.values()]
        if hashes is not None:
            found = [t for t in found if t["hash"] in hashes]
        if tag is not None:
            found = [t for t in found if tag in app.torrent_client.tags_of(t)]
        return found

    def files(self, info_hash):
        return [dict(f) for f in self.torrents.get(info_hash, {}).get("files", [])]

    def set_file_priority(self, info_hash, indexes, priority):
        for entry in self.torrents[info_hash]["files"]:
            if entry["index"] in indexes:
                entry["priority"] = priority

    def add_tags(self, hashes, tags):
        for info_hash in hashes:
            current = app.torrent_client.tags_of(self.torrents[info_hash])
            self.torrents[info_hash]["tags"] = ", ".join(sorted(current | set(tags)))

    def start(self, hashes):
        self.started.extend(hashes)

    def pause(self, hashes):
        self.paused.extend(hashes)

    def delete(self, hashes, *, delete_files):
        for info_hash in hashes:
            self.deleted.append((info_hash, delete_files))
            self.torrents.pop(info_hash, None)

    def add_torrent(self, **kwargs):
        self.added.append(kwargs)
        return []


class TorrentAcquisitionTests(unittest.TestCase):
    """qBittorrent beside SABnzbd: torrents for manga and for whole runs as
    packs, which the download site kept losing (owner, 2026-09-30). Only the wanted
    files of a pack are downloaded, the run's other issues ride along on rows
    of their own, and a torrent seeds on after import until the client stops
    it."""

    HASH = "ab" * 20
    CONTEXT = {"requestId": "48", "seriesId": "7", "seriesTitle": "The Woods", "issueNumber": "21",
               "runIssueCount": 36, "format": "comic", "publicationYear": 2016, "seriesYear": 2014}

    def setUp(self):
        _RELEASE_CANDIDATES.clear()
        self.addCleanup(_RELEASE_CANDIDATES.clear)

    # ---- settings -----------------------------------------------------------

    def test_qbittorrent_is_a_download_client_whose_secret_never_leaves(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            "app.os.environ", {"COMICARR_ACQUISITION_CONFIG": str(Path(folder) / "services.json")},
        ):
            public = save_acquisition_service_config("qbittorrent", {
                "url": "http://qbittorrent:8080/", "username": "admin", "password": "hunter2",
                "category": "comics", "enabled": True,
            })
            qbt = next(service for service in public["services"] if service["id"] == "qbittorrent")
            self.assertEqual((qbt["url"], qbt["username"], qbt["category"], qbt["credentialHint"]),
                             ("http://qbittorrent:8080", "admin", "comics", "Saved locally"))
            self.assertTrue(qbt["configured"] and qbt["enabled"], "credentials are optional")
            self.assertNotIn("hunter2", str(public))
            self.assertNotIn("password", json.dumps(public).lower(), "the word itself never reaches the page")
            save_acquisition_service_config("qbittorrent", {"password": ""})
            self.assertEqual(app.load_acquisition_service_config()["qbittorrent"]["password"], "hunter2",
                             "an empty field keeps the saved one")
            with self.assertRaisesRegex(ValueError, "Enter a qBittorrent category"):
                save_acquisition_service_config("qbittorrent", {"category": " "})
            save_acquisition_service_config("qbittorrent", {"clearCredentials": True})
            saved = app.load_acquisition_service_config()["qbittorrent"]
            self.assertNotIn("password", saved)
            self.assertNotIn("username", saved)
            self.assertEqual(app._enabled_download_clients(), ["qbittorrent"])

    def test_a_saved_secret_goes_only_to_the_address_it_was_saved_for(self):
        """Review (2026-10-06): the test sent the stored key to whatever
        address the request named, so an admin session could read any key
        the settings page hides."""
        saved = {"sabnzbd": {"url": "http://sab:8080", "apiKey": "the-saved-key", "enabled": True},
                 "prowlarr": {"url": "http://prowlarr:9696", "apiKey": "prowlarr-key", "enabled": True},
                 "qbittorrent": {"url": "http://qbt:8080", "username": "admin", "password": "qbt-pass", "enabled": True}}
        with patch("app.load_acquisition_service_config", return_value=saved):
            with self.assertRaisesRegex(ValueError, "API key for the new address"):
                test_acquisition_service_connection("sabnzbd", {"url": "http://attacker.example"})
            with self.assertRaisesRegex(ValueError, "API key for the new address"):
                test_acquisition_service_connection("prowlarr", {"url": "http://attacker.example"})
            with self.assertRaisesRegex(ValueError, "password for the new address"):
                test_acquisition_service_connection("qbittorrent", {"url": "http://attacker.example"})
            # The saved address, and a new address with its own key, are asked.
            with patch("app.fetch_json_with_headers", return_value={"version": "4.3"}) as fetch:
                test_acquisition_service_connection("sabnzbd", {})
                self.assertIn("apikey=the-saved-key", fetch.call_args.args[0])
                test_acquisition_service_connection("sabnzbd", {"url": "http://new-sab:8080", "apiKey": "new-key"})
                self.assertIn("new-sab", fetch.call_args.args[0])
                self.assertNotIn("the-saved-key", fetch.call_args.args[0])

    def test_saving_a_new_address_without_its_secret_drops_the_old_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "acq.json"
            path.write_text(json.dumps({"sabnzbd": {"url": "http://sab:8080", "apiKey": "old-key", "enabled": True}}))
            with patch("app.acquisition_config_path", return_value=path):
                save_acquisition_service_config("sabnzbd", {"url": "http://other:8080"})
                self.assertNotIn("apiKey", json.loads(path.read_text())["sabnzbd"], "the key was for the old address")
                save_acquisition_service_config("sabnzbd", {"url": "http://other:8080", "apiKey": "other-key"})
                save_acquisition_service_config("sabnzbd", {"enabled": False})
                self.assertEqual(json.loads(path.read_text())["sabnzbd"]["apiKey"], "other-key", "the same address keeps it")

    def test_a_redirect_to_another_host_does_not_carry_our_credentials(self):
        """Review (2026-10-06): a Prowlarr indexer in redirect mode answered
        with the indexer's address, and urllib took Prowlarr's key along."""
        handler = app._KeepCredentialsHome()
        original = urllib.request.Request("https://prowlarr.local/api/v1/download?x=1",
                                          headers={"X-Api-Key": "prowlarr-key", "Authorization": "Bearer t", "Accept": "*/*"})
        import email.message
        elsewhere = handler.redirect_request(original, None, 302, "Found", email.message.Message(), "https://indexer.example/get/1")
        names = {name.lower() for name in list(elsewhere.headers) + list(elsewhere.unredirected_hdrs)}
        self.assertNotIn("x-api-key", names)
        self.assertNotIn("authorization", names)
        self.assertIn("accept", names, "ordinary headers stay")
        same = handler.redirect_request(original, None, 302, "Found", email.message.Message(), "https://prowlarr.local/api/v1/download/2")
        self.assertIn("x-api-key", {name.lower() for name in list(same.headers) + list(same.unredirected_hdrs)})
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(original, None, 302, "Found", email.message.Message(), "file:///config/auth.json")
        with self.assertRaisesRegex(urllib.error.URLError, "http and https"):
            app._safe_urlopen(urllib.request.Request("file:///etc/hostname"), timeout=1)

    def test_a_providers_next_page_must_be_the_providers_own(self):
        with self.assertRaisesRegex(ValueError, "not its own"):
            app.fetch_provider_json("metron", "https://attacker.example/api/arc/?page=2", "token")
        with self.assertRaisesRegex(ValueError, "not its own"):
            app.fetch_provider_json("metron", "file:///config/auth.json", "token")
        with self.assertRaisesRegex(ValueError, "not its own"):
            app.fetch_provider_json("comic_vine", "http://comicvine.gamespot.com/api/issues/", "key")

    def test_the_connection_test_names_the_category_and_its_folder(self):
        client = Mock()
        client.version.return_value = "v5.0.2"
        client.default_save_path.return_value = "/data/torrents/complete"
        client.ensure_category.return_value = "/data/torrents/complete/comics"
        client.supports_selection.return_value = True
        with patch("app.load_acquisition_service_config", return_value={"qbittorrent": {"url": "http://qbittorrent:8080", "category": "comics"}}), \
             patch("app.torrent_client.QBittorrent", return_value=client):
            result = test_acquisition_service_connection("qbittorrent", {"username": "admin", "password": "x"})
        client.ensure_category.assert_called_once_with("comics", "/data/torrents/complete/comics")
        self.assertIn("Connected to qBittorrent v5.0.2", result["detail"])
        self.assertIn("saves to /data/torrents/complete/comics", result["detail"])
        client.ensure_category.return_value = "/data/torrents/complete/manga"
        client.supports_selection.return_value = False
        with patch("app.load_acquisition_service_config", return_value={"qbittorrent": {"url": "http://qbittorrent:8080"}}), \
             patch("app.torrent_client.QBittorrent", return_value=client):
            detail = test_acquisition_service_connection("qbittorrent")["detail"]
        self.assertIn("has to end in that name", detail)
        self.assertIn("downloads a pack whole", detail)
        client.version.side_effect = app.torrent_client.TorrentAuthError("qBittorrent rejected this username and password")
        with patch("app.load_acquisition_service_config", return_value={"qbittorrent": {"url": "http://qbittorrent:8080"}}), \
             patch("app.torrent_client.QBittorrent", return_value=client):
            with self.assertRaisesRegex(ValueError, "rejected this username"):
                test_acquisition_service_connection("qbittorrent")

    def test_a_test_that_connects_lets_the_workers_sign_in_again(self):
        # The workers' client holds refused credentials until told otherwise.
        client = Mock()
        client.version.return_value = "v5.0.2"
        client.default_save_path.return_value = "/data/torrents/complete"
        client.ensure_category.return_value = "/data/torrents/complete/comics"
        client.supports_selection.return_value = True
        app._QBITTORRENT_CLIENTS[("http://qbittorrent:8080", "admin", "hash")] = Mock()
        self.addCleanup(app._QBITTORRENT_CLIENTS.clear)
        with patch("app.load_acquisition_service_config", return_value={"qbittorrent": {"url": "http://qbittorrent:8080"}}), \
                patch("app.torrent_client.QBittorrent", return_value=client):
            test_acquisition_service_connection("qbittorrent")
        self.assertEqual(app._QBITTORRENT_CLIENTS, {})

    def test_the_connection_test_says_when_flipparr_cannot_read_the_torrents_folder(self):
        client = Mock()
        client.version.return_value = "v5.0.2"
        client.default_save_path.return_value = "/data/torrents/complete"
        client.ensure_category.return_value = "/data/torrents/complete/comics"
        client.supports_selection.return_value = True
        config = {"qbittorrent": {"url": "http://qbittorrent:8080", "category": "comics"}}
        with tempfile.TemporaryDirectory() as folder:
            mounted = Path(folder) / "comics"
            mounted.mkdir()
            for root, said in ((Path(folder) / "missing" / "comics", True), (mounted, False)):
                with self.subTest(mounted=not said), patch("app.TORRENT_COMPLETE_ROOT", root), \
                        patch("app.load_acquisition_service_config", return_value=config), \
                        patch("app.torrent_client.QBittorrent", return_value=client):
                    detail = test_acquisition_service_connection("qbittorrent")["detail"]
                self.assertEqual("cannot read its torrents folder" in detail, said)

    def test_automatic_search_runs_with_either_download_client(self):
        def enabled(service):
            if service in ("prowlarr", "qbittorrent"):
                return {"url": "http://x"}
            raise ValueError("not configured")
        with patch("app._enabled_acquisition_service", side_effect=enabled):
            self.assertTrue(app._acquisition_services_ready(1))
        with patch("app._enabled_acquisition_service", side_effect=lambda s: {"url": "x"} if s == "prowlarr" else (_ for _ in ()).throw(ValueError())):
            self.assertFalse(app._acquisition_services_ready(1))

    # ---- search ---------------------------------------------------------------

    def search(self, releases, *, qbittorrent=True, context=None):
        store = Mock()
        store.get_acquisition_job_context.return_value = dict(context or {
            "id": "12", "requestId": "4", "issueNumber": "34", "seriesTitle": "Blue Lock", "seriesYear": 2021,
            "publicationYear": 2025, "publisher": "Kodansha Comics USA", "format": "manga", "runIssueCount": 40,
        })
        store.rejected_acquisition_releases.return_value = []
        services = {"prowlarr": {"enabled": True, "url": "http://prowlarr", "apiKey": "secret"}}
        if qbittorrent:
            services["qbittorrent"] = {"enabled": True, "url": "http://qbittorrent:8080"}
        with patch("app.catalog_store", return_value=store), \
             patch("app.load_acquisition_service_config", return_value=services), \
             patch("app.fetch_json_with_headers", return_value=releases) as fetch:
            return search_prowlarr_releases(12), fetch

    def test_a_magnet_only_torrent_is_a_candidate_with_its_swarm(self):
        releases = [
            {"guid": "torrent-1", "title": "Blue Lock v34 (2025) (Digital) (Stick)", "protocol": "torrent",
             "magnetUrl": "http://prowlarr/16/download?apikey=secret&link=abc&file=Blue+Lock", "infoHash": "AB" * 20,
             "seeders": 12, "leechers": 3, "indexer": "Public tracker", "size": 400_000_000, "categories": [{"id": 7000}]},
            {"guid": "usenet-1", "title": "Blue Lock v34 (2025) (Digital) (1r0n)", "protocol": "usenet",
             "downloadUrl": "http://prowlarr/9/download?id=1", "indexer": "One", "size": 400_000_000,
             "categories": [{"id": 7030}]},
        ]
        result, fetch = self.search(releases)
        self.assertIn(("categories", "7000"), urllib.parse.parse_qsl(urllib.parse.urlsplit(fetch.call_args.args[0]).query),
                      "manga is asked of Books too, where some indexers file it")
        usenet, torrent = result["candidates"]
        self.assertEqual((usenet["source"], usenet["protocol"]), ("usenet", "Usenet"), "Usenet first at an equal score")
        self.assertEqual((torrent["source"], torrent["protocol"], torrent["seeders"], torrent["grabbable"]),
                         ("torrent", "Torrent", 12, True))
        cached = _RELEASE_CANDIDATES[torrent["id"]]
        self.assertEqual((cached["source"], cached["infoHash"], cached["seeders"]), ("torrent", "ab" * 20, 12))
        self.assertNotIn("apikey", cached["downloadPath"])
        result, _fetch = self.search(releases, qbittorrent=False)
        torrent = next(item for item in result["candidates"] if item["source"] == "torrent")
        self.assertFalse(torrent["grabbable"])
        self.assertIn("Connect qBittorrent", torrent["grabHint"])

    def test_a_manga_pack_holding_the_volume_is_listed_as_a_pack(self):
        context = {"seriesTitle": "Blue Lock", "issueNumber": "34", "format": "manga", "publicationYear": 2025,
                   "publisher": "Kodansha Comics USA"}
        score, reasons = app._release_candidate_score(
            {"title": "Blue Lock v01-39 (2021-2026) (Digital) (Stick)", "categories": [{"id": 7000}]}, context)
        self.assertEqual((score, reasons), (77, ["Series title matches", "Volumes 1-39 include 34", "Listed as a comic or book"]))
        self.assertEqual(app._release_pack_coverage("Blue Lock v01-39 (2021-2026) (Digital) (Stick)", context), (1, 39))
        self.assertEqual(app._release_candidate_score({"title": "Blue Lock v01-28 (2021-2024)"}, context),
                         (0, ["A pack of volumes 1-28, without volume 34"]))
        self.assertLess(app._release_candidate_score({"title": "Blue Lock - Episode Nagi v01-07 (2024-2026)"}, context)[0], 70,
                        "a spin-off's pack is not this series")
        self.assertEqual(app._release_candidate_score({"title": "Chainsaw Man Ch. 150 (2024) (Digital)"},
                                                      {**context, "seriesTitle": "Chainsaw Man", "issueNumber": "13"})[1],
                         ["A pack, not one volume"], "a chapter is still not a volume")
        self.assertIsNone(app._manga_file_volume("Blue Lock v01-39.cbz"), "a file still names one volume or none")
        result, _fetch = self.search([
            {"guid": "pack", "title": "Blue Lock v01-39 (2021-2026) (Digital) (Stick)", "protocol": "torrent",
             "magnetUrl": "http://prowlarr/16/download?link=x", "seeders": 30, "size": 16_000_000_000,
             "categories": [{"id": 7000}]},
        ])
        pack = result["candidates"][0]
        self.assertEqual((pack["pack"], pack["matchStrength"]), ({"first": 1, "last": 39}, "Pack, Vol. 1-39"))
        self.assertIn("Only the issues wanted are downloaded from it", pack["matchReasons"],
                      "its size is the whole pack's; the row says what is actually fetched")

    # ---- what the automatic grabs may take ------------------------------------

    @staticmethod
    def candidate(cid, *, source="usenet", score=95, pack=None, seeders=None, size=50_000_000, grabbable=True):
        item = {"id": cid, "title": cid, "source": source, "matchScore": score, "sizeBytes": size, "grabbable": grabbable}
        if pack:
            item["pack"] = {"first": pack[0], "last": pack[1]}
        if source == "torrent":
            item["seeders"] = seeders if seeders is not None else 4
        return item

    def order(self, candidates, wanted=(21,)):
        store = Mock()
        store.wanted_run_issues.return_value = [
            {"jobId": 100 + n, "issueId": 200 + n, "issueNumber": str(n), "status": "queued", "seriesId": "7"}
            for n in wanted
        ]
        with patch("app.catalog_store", return_value=store):
            return app._automatic_grab_order(self.CONTEXT, candidates)

    def test_a_torrent_nobody_shares_is_left_for_a_person(self):
        taken, note = self.order([self.candidate("dead", source="torrent", seeders=0)])
        self.assertEqual(taken, [])
        self.assertIn("nobody is sharing it right now", note)
        taken, _note = self.order([self.candidate("dead", source="torrent", seeders=0), self.candidate("usenet")])
        self.assertEqual([item["id"] for item in taken], ["usenet"])
        taken, _note = self.order([self.candidate("unconnected", source="torrent", grabbable=False)])
        self.assertEqual(taken, [], "a torrent with no client connected cannot be sent anywhere")

    def test_pulling_one_issue_a_torrent_pack_is_the_last_resort(self):
        pack = self.candidate("pack", source="torrent", score=72, pack=(1, 36), size=3_000_000_000)
        taken, _note = self.order([pack, self.candidate("single", source="torrent")])
        self.assertEqual([item["id"] for item in taken], ["single"], "any single first")
        taken, _note = self.order([pack])
        self.assertEqual([(item["id"], item.get("lastResort")) for item in taken], [("pack", True)])
        usenet_pack = self.candidate("usenet-pack", score=72, pack=(1, 36), size=3_000_000_000)
        taken, note = self.order([usenet_pack])
        self.assertEqual(taken, [], "a Usenet pack cannot be fetched for one issue alone")
        self.assertIn("only 1 issue wanted", note)

    def test_a_run_mostly_missing_takes_a_torrent_pack_whatever_its_whole_size(self):
        pack = self.candidate("pack", source="torrent", score=72, pack=(1, 36), size=16_000_000_000)
        taken, _note = self.order([pack], wanted=range(1, 9))
        self.assertEqual([(item["id"], item.get("lastResort")) for item in taken], [("pack", None)],
                         "only the wanted issues are fetched; the whole-size rule is for packs that cannot be split")
        usenet_pack = self.candidate("usenet-pack", score=72, pack=(1, 36), size=16_000_000_000)
        taken, _note = self.order([usenet_pack], wanted=range(1, 9))
        self.assertEqual(taken, [], "16 GB for 8 issues from Usenet is still too much")

    def test_a_last_resort_pack_waits_for_a_direct_site_single(self):
        pack = self.candidate("pack", source="torrent", score=72, pack=(1, 36))
        store = Mock()
        store.wanted_run_issues.return_value = [{"jobId": 121, "issueId": 221, "issueNumber": "21", "status": "queued"}]
        with patch("app._issue_year_gate", return_value=None), patch("app.catalog_store", return_value=store), \
             patch("app.search_prowlarr_releases", return_value={"candidates": [pack], "job": self.CONTEXT}), \
             patch("app._direct_site_single_to_take", return_value={"id": "gc", "title": "The Woods #21 (2016)"}), \
             patch("app.grab_release_candidate", return_value={"status": "grabbed"}) as grab:
            app._auto_grab_release(121)
        grab.assert_called_once_with(121, "gc")
        with patch("app._issue_year_gate", return_value=None), patch("app.catalog_store", return_value=store), \
             patch("app.search_prowlarr_releases", return_value={"candidates": [pack], "job": self.CONTEXT}), \
             patch("app._direct_site_single_to_take", return_value=None), \
             patch("app.grab_release_candidate", return_value={"status": "grabbed"}) as grab:
            app._auto_grab_release(121)
        grab.assert_called_once_with(121, "pack")

    def test_between_packs_of_equal_reach_usenet_then_torrent_then_direct_site(self):
        store = Mock()
        store.wanted_run_issues.return_value = [
            {"jobId": 100 + n, "issueId": 200 + n, "issueNumber": str(n), "status": "queued", "seriesId": "7"}
            for n in range(1, 37)
        ] + [{"jobId": 999, "issueId": 999, "issueNumber": "4", "status": "queued", "seriesId": "8"}]
        store.get_acquisition_job_context.return_value = self.CONTEXT
        store.rejected_release_keys_for_jobs.return_value = set()
        offers = [
            {**self.candidate("gc", source="direct_site", score=72, pack=(1, 36), size=1_000_000_000)},
            self.candidate("tor", source="torrent", score=72, pack=(1, 36), size=9_000_000_000),
            self.candidate("dead", source="torrent", score=72, pack=(1, 36), seeders=0),
        ]
        jobs = [100 + n for n in range(1, 37)] + [999]
        with patch("app.search_prowlarr_releases", return_value={"candidates": offers[1:]}), \
             patch("app._direct_site_candidates", return_value=offers[:1]), \
             patch("app._enabled_acquisition_service", return_value={}), \
             patch("app.grab_release_candidate", return_value={"status": "grabbed"}) as grab:
            grabbed = app._grab_run_pack(store, 48, jobs)
        grab.assert_called_once_with(101, "tor")
        self.assertEqual(grabbed["covers"], 36, "the other run's #4 is not counted")

    # ---- the grab -----------------------------------------------------------------

    def register(self, **extra):
        _RELEASE_CANDIDATES["c"] = {"jobId": 12, "title": "The Woods #1 - 36 (2014-2018)", "source": "torrent",
                                    "downloadPath": "/16/download?link=x", "releaseKey": "key-1",
                                    "expiresAt": time.time() + 600, **extra}

    def grab(self, fake, fetched=(b"", None)):
        store = Mock()
        with patch("app._qbittorrent_client", return_value=fake), patch("app.catalog_store", return_value=store), \
             patch("app._enabled_acquisition_service", return_value={"category": "comics"}), \
             patch("app._fetch_selected_torrent", return_value=fetched) as fetch:
            result = app.grab_release_candidate(12, "c")
        return result, store, fetch

    def test_a_torrent_is_added_to_wait_for_its_file_list_and_recorded_by_hash(self):
        from test_torrent_client import TORRENT, HASH
        fake = FakeQbittorrent()
        self.register(pack={"first": 1, "last": 36})
        result, store, fetch = self.grab(fake, (TORRENT, None))
        self.assertEqual((result["source"], result["detail"]), ("qbittorrent", "Release sent to qBittorrent."))
        added = fake.added[0]
        self.assertEqual((added["category"], added["tags"], added["wait_for_files"], added["torrent"]),
                         ("comics", ["flipparr", "job-12"], True, TORRENT))
        store.record_acquisition_download.assert_called_once_with(
            12, f"torrent:{HASH}:12", "The Woods #1 - 36 (2014-2018)", "key-1", source="qbittorrent")
        self.assertEqual(store.update_acquisition_job.call_args.args, (12, "grabbed", "Sent to qBittorrent: The Woods #1 - 36 (2014-2018)"))
        fetch.assert_called_once()

    def test_a_torrent_already_in_the_client_is_joined_not_added_again(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, tags=["flipparr", "job-7", "flipparr-selected"])
        self.register(infoHash=self.HASH)
        _result, store, fetch = self.grab(fake)
        fetch.assert_not_called()
        self.assertEqual(fake.added, [])
        self.assertIn("job-12", app.torrent_client.tags_of(fake.torrents[self.HASH]))
        self.assertEqual(store.record_acquisition_download.call_args.args[1], f"torrent:{self.HASH}:12")

    def test_an_old_client_is_not_sent_a_pack_it_would_fetch_whole(self):
        fake = FakeQbittorrent(selective=False)
        self.register(pack={"first": 1, "last": 36}, infoHash=self.HASH)
        with self.assertRaisesRegex(app.TorrentSubmissionError, "would download the whole pack"):
            self.grab(fake, (None, f"magnet:?xt=urn:btih:{self.HASH}"))
        self.assertTrue(issubclass(app.TorrentSubmissionError, SABSubmissionError),
                        "a client refusing stops the automatic loop, as SABnzbd refusing does")

    def test_prowlarrs_link_is_read_as_a_torrent_or_a_magnet_never_a_page(self):
        from test_torrent_client import TORRENT
        prowlarr = {"url": "http://prowlarr:9696", "apiKey": "secret"}
        opener = MagicMock()
        with patch("app._enabled_acquisition_service", return_value=prowlarr), \
             patch("app.urllib.request.build_opener", return_value=opener):
            opener.open.return_value.__enter__.return_value.read.return_value = TORRENT
            self.assertEqual(app._fetch_selected_torrent({"downloadPath": "/16/download?link=x"}), (TORRENT, None))
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://prowlarr:9696/16/download?link=x")
            headers = email.message.Message()
            headers["Location"] = "magnet:?xt=urn:btih:" + "cd" * 20
            opener.open.side_effect = urllib.error.HTTPError("x", 301, "Moved", headers, None)
            self.assertEqual(app._fetch_selected_torrent({"downloadPath": "/16/download?link=x"}),
                             (None, "magnet:?xt=urn:btih:" + "cd" * 20))
            opener.open.side_effect = None
            opener.open.return_value.__enter__.return_value.read.return_value = b"<!DOCTYPE html><html>"
            with self.assertRaisesRegex(ReleaseDownloadError, "not a torrent"):
                app._fetch_selected_torrent({"downloadPath": "/16/download?link=x"})

    # ---- choosing the files --------------------------------------------------

    WOODS = [(f"The Woods #1 - 36/The Woods {n:03d} (2016) (Digital) (Zone-Empire).cbr", 60_000_000) for n in range(1, 37)]

    def choose(self, fake, *, wanted=(21, 22, 30), statuses=None, release="The Woods #1 - 36 (2014-2018)"):
        store = Mock()
        store.get_acquisition_job_context.return_value = self.CONTEXT
        store.wanted_run_issues.return_value = [
            {"jobId": 100 + n, "issueId": 200 + n, "issueNumber": str(n),
             "status": (statuses or {}).get(n, "queued"), "seriesId": "7"} for n in wanted
        ]
        store.record_acquisition_download.side_effect = lambda job, *a, **k: {"id": 5000 + job}
        store.torrent_references.return_value = {"live": 0, "kept": 0}
        store.torrent_jobs.return_value = []
        download = {"id": 1, "job_id": 121, "sab_nzo_id": f"torrent:{self.HASH}:121", "status": "queued",
                    "source": "qbittorrent", "release_title": release, "release_key": "key-1",
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        with patch("app._qbittorrent_client", return_value=fake), patch("app.catalog_store", return_value=store), \
             patch("app._fallback_after_sab_failure", return_value={"status": "fallback_queued"}) as fallback:
            result = app.reconcile_acquisition_download(download)
        return result, store, fallback

    def test_only_the_wanted_issues_are_fetched_and_the_others_ride_along(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, tags=["flipparr", "job-121"], files=self.WOODS)
        result, store, _fallback = self.choose(fake, statuses={30: "searching"})
        self.assertEqual((result["selected"], result["riders"]), (2, 1))
        chosen = sorted(f["name"].rsplit("/", 1)[1][:13] for f in fake.torrents[self.HASH]["files"] if f["priority"])
        self.assertEqual(chosen, ["The Woods 021", "The Woods 022"], "#30 is being searched already")
        self.assertIn("flipparr-selected", app.torrent_client.tags_of(fake.torrents[self.HASH]))
        self.assertEqual(fake.started, [self.HASH])
        store.record_acquisition_download.assert_called_once_with(
            122, f"torrent:{self.HASH}:122", "The Woods #1 - 36 (2014-2018)", "key-1", source="qbittorrent")
        self.assertIn(call(122, "grabbed", "Coming in the same download as The Woods #21: The Woods #1 - 36 (2014-2018)"),
                      store.update_acquisition_job.call_args_list)
        self.assertIn(call(1, "downloading"), store.update_acquisition_download.call_args_list)

    def test_a_pack_without_the_issue_is_refused_saying_what_it_holds(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, tags=["flipparr"], files=self.WOODS[:12])
        _result, store, fallback = self.choose(fake)
        message = fallback.call_args.args[2]
        self.assertTrue(message.startswith("The pack does not hold The Woods #21: it holds The Woods 001"), message)
        self.assertEqual(fallback.call_args.kwargs, {"kind": "contradiction"})
        self.assertEqual(fake.deleted, [(self.HASH, True)], "nothing else uses it, so it goes")

    def test_a_torrent_with_no_comic_in_it_is_refused_and_removed(self):
        # Gate 3 (2026-10-05): the empty-torrent refusal had no test.
        fake = FakeQbittorrent()
        fake.put(self.HASH, tags=["flipparr"], files=[("Woods/readme.txt", 100), ("Woods/sample.mkv", 9_000_000)])
        _result, _store, fallback = self.choose(fake)
        self.assertEqual(fallback.call_args.kwargs, {"kind": "format"})
        self.assertEqual(fake.deleted, [(self.HASH, True)])

    def test_a_single_release_in_several_files_keeps_them_all(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, files=[("Woods/swot.cbr", 50_000_000), ("Woods/extra.cbz", 20_000_000), ("Woods/info.nfo", 1)])
        result, _store, _fallback = self.choose(fake, release="The Woods 021 (2016) (Digital)")
        self.assertEqual(result["selected"], 2)
        self.assertEqual([f["priority"] for f in fake.torrents[self.HASH]["files"]], [1, 1, 0])

    def test_an_issue_joining_a_chosen_torrent_turns_its_own_file_on(self):
        fake = FakeQbittorrent()
        torrent = fake.put(self.HASH, tags=["flipparr", "flipparr-selected"], files=self.WOODS)
        for entry in torrent["files"]:
            entry["priority"] = 0
        result, _store, _fallback = self.choose(fake)
        self.assertEqual(result["status"], "downloading")
        self.assertEqual([f["index"] for f in fake.torrents[self.HASH]["files"] if f["priority"]], [20])
        self.assertEqual(fake.started, [self.HASH])

    # ---- following it --------------------------------------------------------

    def follow(self, fake, status="downloading", created=None):
        store = Mock()
        store.get_acquisition_job_context.return_value = self.CONTEXT
        store.torrent_references.return_value = {"live": 0, "kept": 0}
        store.torrent_jobs.return_value = []
        download = {"id": 1, "job_id": 121, "sab_nzo_id": f"torrent:{self.HASH}:121", "status": status,
                    "source": "qbittorrent", "release_title": "The Woods #1 - 36", "release_key": "key-1",
                    "created_at": created or "2026-09-01T00:00:00+00:00"}
        with patch("app._qbittorrent_client", return_value=fake), patch("app.catalog_store", return_value=store), \
             patch("app._fallback_after_sab_failure", return_value={"status": "fallback_queued"}) as fallback, \
             patch("app._import_completed_download", return_value={"status": "imported"}) as imported:
            result = app.reconcile_acquisition_download(download)
        return result, store, fallback, imported

    def test_a_finished_file_is_imported_from_the_torrents_folder(self):
        fake = FakeQbittorrent()
        torrent = fake.put(self.HASH, state="uploading", files=self.WOODS)
        for entry in torrent["files"]:
            entry["priority"] = 1 if entry["index"] in (20, 21) else 0
        result, store, _fallback, imported = self.follow(fake)
        self.assertEqual(result["status"], "imported")
        storage = imported.call_args.args[2]
        self.assertEqual(storage, str(app.TORRENT_COMPLETE_ROOT / "The Woods #1 - 36" / "The Woods 021 (2016) (Digital) (Zone-Empire).cbr"))
        self.assertIn(call(1, "completed", sab_storage=storage), store.update_acquisition_download.call_args_list)

    def test_a_torrent_saved_outside_the_comics_folder_waits_and_says_where(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, state="stalledUP", files=self.WOODS[20:21], save_path="/data/torrents/complete/prowlarr")
        result, _store, _fallback, imported = self.follow(fake)
        self.assertEqual(result["status"], "waiting_for_files")
        self.assertIn("“prowlarr”, not in the “comics” folder", result["detail"])
        imported.assert_not_called()

    def test_a_broken_gone_or_unshared_torrent_hands_the_issue_on(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, state="missingFiles")
        _result, _store, fallback, _imported = self.follow(fake)
        self.assertEqual(fallback.call_args.args[2], "qBittorrent could not finish the torrent (missingFiles)")
        _result, _store, fallback, _imported = self.follow(FakeQbittorrent())
        self.assertEqual((fallback.call_args.args[2], fallback.call_args.kwargs), ("qBittorrent no longer has this torrent", {"kind": "lost"}))
        result, _store, fallback, _imported = self.follow(FakeQbittorrent(), created=dt.datetime.now(dt.timezone.utc).isoformat())
        self.assertEqual(result["status"], "downloading", "just sent: the client may not list it yet")
        fallback.assert_not_called()
        fake = FakeQbittorrent()
        fake.put(self.HASH, state="stalledDL", num_seeds=0, last_activity=int(time.time()) - 13 * 3600)
        _result, _store, fallback, _imported = self.follow(fake)
        self.assertEqual(fallback.call_args.kwargs, {"kind": "lost"})
        self.assertEqual(fake.deleted, [(self.HASH, True)])
        fake = FakeQbittorrent()
        fake.put(self.HASH, state="downloading")
        result, _store, fallback, _imported = self.follow(fake)
        self.assertEqual(result["status"], "downloading")
        fallback.assert_not_called()

    def test_a_torrent_seeds_on_after_import_and_is_not_swept_for_other_issues(self):
        store = Mock()
        store.get_acquisition_job_context.return_value = self.CONTEXT
        store.replacement_for_job.return_value = None
        store.kept_refused_downloads.return_value = []
        download = {"id": 1, "job_id": 121, "source": "qbittorrent", "release_title": "The Woods #1 - 36"}
        with patch("app.import_downloaded_comic", return_value={"destination": "/comics/The Woods 021.cbr", "source": "x",
                                                                 "size": 1, "sha256": "y"}), \
             patch("app._catalog_imported_issue", return_value=9), \
             patch("app._sab_remove_job") as sab_remove, patch("app.shutil.rmtree") as rmtree:
            result = app._import_completed_download(store, download, "/downloads/torrents/complete/comics/x.cbr")
        self.assertEqual(result["status"], "imported")
        sab_remove.assert_not_called()
        rmtree.assert_not_called()
        store.wanted_run_issues.assert_not_called()

    # ---- letting go -----------------------------------------------------------

    def test_the_client_stops_a_torrent_at_its_limit_and_then_it_goes(self):
        fake = FakeQbittorrent()
        old = int(time.time()) - 7200
        fake.put("11" * 20, state="stoppedUP", tags=["flipparr"], added_on=old)
        fake.put("22" * 20, state="uploading", tags=["flipparr"], added_on=old)
        fake.put("33" * 20, state="stoppedUP", tags=["flipparr"], added_on=old)
        fake.put("44" * 20, state="stoppedUP", tags=["someone-else"], added_on=old)
        fake.put("55" * 20, state="metaDL", tags=["flipparr"], added_on=int(time.time()))
        fake.put("66" * 20, state="downloading", tags=["flipparr"], added_on=old)
        store = Mock()
        store.torrent_hashes_in_use.return_value = {"33" * 20}
        with patch("app._qbittorrent_client", return_value=fake):
            removed = app._release_seeded_torrents(store)
        self.assertEqual(removed, 2)
        self.assertEqual(sorted(fake.deleted), [("11" * 20, True), ("66" * 20, True)],
                         "stopped by the client, or wanted by nothing: not seeding, not in use, not someone else's, not just added")

    def test_a_stopped_issue_leaves_the_rest_of_its_pack_coming(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, files=self.WOODS)
        store = Mock()
        store.get_acquisition_job_context.return_value = self.CONTEXT
        store.torrent_references.return_value = {"live": 1, "kept": 0}
        with patch("app._qbittorrent_client", return_value=fake):
            app._stop_torrent_for_job(store, {"job_id": 121, "sab_nzo_id": f"torrent:{self.HASH}:121"})
        self.assertEqual([f["index"] for f in fake.torrents[self.HASH]["files"] if not f["priority"]], [20])
        self.assertEqual(fake.deleted, [])
        store.torrent_references.return_value = {"live": 0, "kept": 0}
        with patch("app._qbittorrent_client", return_value=fake):
            app._stop_torrent_for_job(store, {"job_id": 121, "sab_nzo_id": f"torrent:{self.HASH}:121"})
        self.assertEqual(fake.deleted, [(self.HASH, True)])

    def test_kept_evidence_of_a_torrent_goes_through_the_client(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH)
        store = Mock()
        store.kept_refused_downloads.return_value = [{"id": 3, "sab_nzo_id": f"torrent:{self.HASH}:121", "sab_storage": "/x"}]
        store.torrent_references.return_value = {"live": 0, "kept": 0}
        with patch("app._qbittorrent_client", return_value=fake), patch("app._sab_remove_job") as sab_remove:
            self.assertEqual(app._discard_kept_downloads(store, job_id=121), 1)
        sab_remove.assert_not_called()
        store.forget_kept_download.assert_called_once_with(3)
        self.assertEqual(fake.deleted, [(self.HASH, True)])

    def test_progress_is_of_the_wanted_files(self):
        fake = FakeQbittorrent()
        fake.put(self.HASH, state="downloading", progress=0.5, amount_left=60_000_000, eta=754)
        rows = [{"job_id": 121, "sab_nzo_id": f"torrent:{self.HASH}:121"}, {"job_id": 122, "sab_nzo_id": f"torrent:{self.HASH}:122"}]
        with patch("app._qbittorrent_client", return_value=fake):
            progress = app._torrent_progress(rows)
        self.assertEqual(progress["121"], {"percent": 50.0, "sizeLeft": "60 MB", "timeLeft": "0:12:34", "state": "downloading"})
        self.assertEqual(set(progress), {"121", "122"})


class ProviderPauseTests(unittest.TestCase):
    """A provider's pause is not waited out. GCD answered a burst of pulls
    with "try again in 620 seconds", and every catalog search then slept ten
    minutes on its GCD half, the page stuck on its skeleton (2026-09-30)."""

    def setUp(self):
        self.addCleanup(app._PROVIDER_JSON_CACHE.clear)
        app._PROVIDER_JSON_CACHE.clear()

    def test_a_pacing_gap_is_waited_out(self):
        with patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {"gcd": time.monotonic() + 0.05}, clear=True), \
             patch("app.time.sleep") as sleep:
            sleep.side_effect = lambda seconds: app._PROVIDER_NEXT_REQUEST_AT.__setitem__("gcd", 0.0)
            app._wait_for_provider_slot("gcd")
        sleep.assert_called_once()

    def test_a_pause_fails_at_once_as_rate_limited(self):
        with patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {"gcd": time.monotonic() + 620}, clear=True), \
             patch("app.time.sleep") as sleep:
            with self.assertRaises(app.MetadataRateLimited) as raised:
                app._wait_for_provider_slot("gcd")
        sleep.assert_not_called()
        self.assertEqual(raised.exception.provider, "gcd")
        self.assertGreaterEqual(raised.exception.retry_after_seconds, 619)
        self.assertIn("left out for about 11 more minutes", str(raised.exception))

    def test_a_paused_provider_answers_from_its_cache_or_not_at_all(self):
        url = "https://www.comics.org/api/series/name/saga/?format=json"
        key = app._provider_cache_key("gcd", url, "")
        with patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {"gcd": time.monotonic() + 620}, clear=True), \
             patch("app.fetch_json_with_headers") as fetch:
            with self.assertRaises(app.MetadataRateLimited):
                app.fetch_provider_json("gcd", url, "")
            app._PROVIDER_JSON_CACHE[key] = {"saved_at": time.time() - 10 * 24 * 3600, "status": 200, "data": {"results": ["cached"]}}
            self.assertEqual(app.fetch_provider_json("gcd", url, ""), {"results": ["cached"]},
                             "a stale copy is better than a ten-minute wait")
        fetch.assert_not_called()

    def test_a_search_answers_without_the_paused_provider(self):
        config = {"metron": {"enabled": False}, "comic_vine": {"enabled": False}, "gcd": {"enabled": True}}
        with patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {"gcd": time.monotonic() + 620}, clear=True), \
             patch("app.load_provider_config", return_value=config), \
             patch("app._discovery_library_view", return_value={}), \
             patch("app.fetch_json_with_headers") as fetch:
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "asked Flipparr to pause"):
                app.discover_series("saga")
        self.assertLess(time.monotonic() - started, 5, "no ten-minute wait")
        fetch.assert_not_called()


class SourcePriorityTests(unittest.TestCase):
    """Where releases are taken from first is the admin's to say (owner,
    2026-09-30), and among releases good enough to take the order decides."""

    def test_the_order_is_a_setting_with_the_old_order_as_its_default(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            "app.os.environ", {"COMICARR_SETTINGS_CONFIG": str(Path(folder) / "settings.json")},
        ):
            self.assertEqual(app.source_priority(), ["usenet", "torrent", "direct_site"])
            app.save_app_settings({"sourcePriority": ["direct_site", "usenet", "torrent"]})
            self.assertEqual(app.source_priority(), ["direct_site", "usenet", "torrent"])
            with self.assertRaisesRegex(ValueError, "once each"):
                app.save_app_settings({"sourcePriority": ["usenet", "usenet", "torrent"]})
            (Path(folder) / "settings.json").write_text(json.dumps({"sourcePriority": ["usenet"]}))
            self.assertEqual(app.source_priority(), ["usenet", "torrent", "direct_site"], "a broken list is the default")
            (Path(folder) / "settings.json").write_text(json.dumps({"sourcePriority": ["getcomics", "usenet", "torrent"]}))
            self.assertEqual(app.source_priority(), ["direct_site", "usenet", "torrent"],
                             "an order saved under direct downloads' old name is kept")

    def test_the_order_decides_among_strong_matches_and_never_lifts_a_weak_one(self):
        key = app._release_order_key(["torrent", "usenet", "direct_site"])
        items = [
            {"id": "usenet-strong", "source": "usenet", "matchScore": 100},
            {"id": "torrent-strong", "source": "torrent", "matchScore": 88, "seeders": 3},
            {"id": "torrent-weak", "source": "torrent", "matchScore": 72},
            {"id": "gc-strong", "source": "direct_site", "matchScore": 95},
        ]
        self.assertEqual([item["id"] for item in sorted(items, key=key)],
                         ["torrent-strong", "usenet-strong", "gc-strong", "torrent-weak"])

    def test_a_direct_site_single_is_asked_first_only_when_it_leads(self):
        usenet = {"id": "usenet-1", "title": "Saga 061", "source": "usenet", "matchScore": 100, "sizeBytes": 1}
        gc_single = {"id": "gc-1", "title": "Saga #61", "source": "direct_site", "matchScore": 95}
        context = {"seriesTitle": "Saga", "issueNumber": "61", "requestId": "3"}

        def run(order):
            with patch("app.source_priority", return_value=order), patch("app._issue_year_gate", return_value=None), \
                 patch("app.search_prowlarr_releases", return_value={"candidates": [usenet], "job": context}), \
                 patch("app._direct_site_single_to_take", return_value=gc_single) as asked, \
                 patch("app.grab_release_candidate", return_value={"status": "grabbed"}) as grab:
                app._auto_grab_release(9)
            return asked, grab

        asked, grab = run(["direct_site", "usenet", "torrent"])
        asked.assert_called_once()
        grab.assert_called_once_with(9, "gc-1")
        asked, grab = run(["usenet", "torrent", "direct_site"])
        asked.assert_not_called()
        grab.assert_called_once_with(9, "usenet-1")

    def test_a_leading_direct_site_single_that_fails_hands_on_to_the_next_source(self):
        usenet = {"id": "usenet-1", "title": "Saga 061", "source": "usenet", "matchScore": 100, "sizeBytes": 1}
        with patch("app.source_priority", return_value=["direct_site", "usenet", "torrent"]), \
             patch("app._issue_year_gate", return_value=None), \
             patch("app.search_prowlarr_releases", return_value={"candidates": [usenet], "job": {"requestId": "3"}}), \
             patch("app._direct_site_single_to_take", return_value={"id": "gc-1", "title": "Saga #61"}), \
             patch("app.grab_release_candidate", side_effect=[ValueError("the link opened a page"), {"status": "grabbed"}]) as grab:
            result = app._auto_grab_release(9)
        self.assertEqual([c.args for c in grab.call_args_list], [(9, "gc-1"), (9, "usenet-1")])
        self.assertEqual(result["release"]["id"], "usenet-1")

    def test_between_packs_of_equal_reach_the_order_decides(self):
        store = Mock()
        store.wanted_run_issues.return_value = [
            {"jobId": 100 + n, "issueId": 200 + n, "issueNumber": str(n), "status": "queued"} for n in range(1, 21)]
        store.get_acquisition_job_context.return_value = {"requestId": "48", "seriesTitle": "Farmhand", "issueNumber": "1",
                                                          "runIssueCount": 20}
        store.rejected_release_keys_for_jobs.return_value = set()
        usenet = {"id": "usenet-pack", "title": "Farmhand 001-020", "source": "usenet", "matchScore": 72,
                  "sizeBytes": 900_000_000, "pack": {"first": 1, "last": 20}, "grabbable": True}
        gc = {"id": "gc-pack", "title": "Farmhand #1 - 20", "source": "direct_site", "matchScore": 72,
              "sizeBytes": 900_000_000, "pack": {"first": 1, "last": 20}, "grabbable": True}
        with patch("app.source_priority", return_value=["direct_site", "torrent", "usenet"]), \
             patch("app.search_prowlarr_releases", return_value={"candidates": [usenet]}), \
             patch("app._direct_site_candidates", return_value=[gc]), \
             patch("app._enabled_acquisition_service", return_value={}), \
             patch("app.grab_release_candidate", return_value={"status": "grabbed"}) as grab:
            app._grab_run_pack(store, 48, [100 + n for n in range(1, 21)])
        grab.assert_called_once_with(101, "gc-pack")

    def test_services_say_which_group_they_belong_to(self):
        groups = {service["id"]: service["group"] for service in app.public_acquisition_service_config()["services"]}
        self.assertEqual(groups, {"prowlarr": "search", "sabnzbd": "clients", "qbittorrent": "clients",
                                  "flaresolverr": "direct", "direct_site": "direct"})


class CatalogReuseTests(unittest.TestCase):
    """The catalog takes 1.6 s to build for a real library and is asked for
    constantly; it is rebuilt only when what it is made from has changed
    (2026-10-05)."""

    def setUp(self):
        app._CATALOG_CACHE.clear()
        self.addCleanup(app._CATALOG_CACHE.clear)
        self.store = Mock()
        self.store.catalog.side_effect = lambda *args, **kwargs: {
            "series": [{"id": "1"}], "enrichment": {}, "stats": {"series": 1}}
        self.store.metadata_provider_available.return_value = True
        for target, value in (("app.catalog_store", self.store), ("app._series_enrichment_provider_order", []),
                              ("app.collected_editions_enabled", False)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_unchanged_library_is_built_once_and_a_change_rebuilds_it(self):
        with patch("app._catalog_stamp", return_value=("a",)):
            first = catalog_api_payload(viewer_id=1)
            second = catalog_api_payload(viewer_id=1)
        self.assertEqual(self.store.catalog.call_count, 1)
        self.assertIs(first["series"], second["series"], "the same build, not a second one")
        with patch("app._catalog_stamp", return_value=("b",)):
            catalog_api_payload(viewer_id=1)
        self.assertEqual(self.store.catalog.call_count, 2, "a commit, a settings change: built again")

    def test_each_viewer_has_their_own_and_none_outlives_the_cap(self):
        with patch("app._catalog_stamp", return_value=("a",)):
            catalog_api_payload(viewer_id=1)
            catalog_api_payload(viewer_id=2)
            self.assertEqual(self.store.catalog.call_count, 2, "one viewer's ratings are not another's")
            with patch("app.time.monotonic", return_value=time.monotonic() + app.CATALOG_REUSE_SECONDS + 1):
                catalog_api_payload(viewer_id=1)
        self.assertEqual(self.store.catalog.call_count, 3, "the clock moves release dates and cooldowns")

    def test_what_a_caller_changes_at_the_top_never_reaches_the_next_caller(self):
        with patch("app._catalog_stamp", return_value=("a",)):
            first = catalog_api_payload(viewer_id=1)
            first["series"] = []
            first["stats"]["pendingRequests"] = 9
            first["memberRequests"] = ["x"]
            second = catalog_api_payload(viewer_id=1)
        self.assertEqual(second["series"], [{"id": "1"}])
        self.assertNotIn("pendingRequests", second["stats"])
        self.assertNotIn("memberRequests", second)

    def test_the_stamp_moves_when_the_database_or_a_settings_file_does(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = {name: Path(folder) / name for name in ("flipparr.db", "settings.json", "providers.json")}
            for path in paths.values():
                path.write_text("1")
            with patch("app.catalog_database_path", return_value=paths["flipparr.db"]), \
                    patch("app.settings_config_path", return_value=paths["settings.json"]), \
                    patch("app.provider_config_path", return_value=paths["providers.json"]):
                before = app._catalog_stamp()
                self.assertEqual(app._catalog_stamp(), before)
                Path(str(paths["flipparr.db"]) + "-wal").write_text("a commit")
                after_commit = app._catalog_stamp()
                paths["settings.json"].write_text("changed!")
                self.assertEqual(len({before, after_commit, app._catalog_stamp()}), 3)



class ConnectorTroubleNotificationTests(unittest.TestCase):
    """An AI connector that stops for a reason only the admin can fix -- out
    of credit, key refused -- goes in the admin's bell, once an outage
    (owner, 2026-10-05). Neither service says how much credit is left, so
    there is no "running low" to tell."""

    EMPTY = (b'{"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low '
             b'to access the Anthropic API."}}')

    def setUp(self):
        app._VISION_TROUBLE_TOLD.clear()
        self.addCleanup(app._VISION_TROUBLE_TOLD.clear)
        self.store = Mock()
        self.store.list_users.return_value = [
            {"id": 1, "role": "admin", "disabled": False}, {"id": 2, "role": "reader", "disabled": False},
            {"id": 3, "role": "admin", "disabled": True}]
        for patcher in (patch("app.catalog_store", return_value=self.store), patch("app._wait_for_provider_slot"),
                        patch("app.log_event"), patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {}, clear=True),
                        patch("app.load_provider_config", return_value={"anthropic": {"enabled": True, "apiKey": "a-key"}})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _refuse(self, status, body):
        import email.message, io
        error = urllib.error.HTTPError("https://api.anthropic.com/v1/messages", status, "no", email.message.Message(), io.BytesIO(body))
        with patch("urllib.request.urlopen", side_effect=error), patch.dict(app._PROVIDER_NEXT_REQUEST_AT, {}, clear=True):
            with self.assertRaises(app.VisionUnavailable):
                app.ask_vision_model(b"jpeg", "boxes?")

    def test_an_empty_balance_tells_each_admin_once_until_it_answers_again(self):
        self._refuse(400, self.EMPTY)
        self._refuse(400, self.EMPTY)
        calls = self.store.record_notification.call_args_list
        self.assertEqual(len(calls), 1, "once an outage, to the one working admin")
        self.assertEqual(calls[0].args[:3], (1, "connector_trouble", "connector:anthropic:out_of_credit"))
        answer = Mock()
        answer.__enter__ = lambda s: io.BytesIO(json.dumps({"content": [{"type": "text", "text": "[]"}]}).encode())
        answer.__exit__ = lambda *a: False
        with patch("urllib.request.urlopen", return_value=answer):
            app.ask_vision_model(b"jpeg", "boxes?")
        self._refuse(400, self.EMPTY)
        self.assertEqual(self.store.record_notification.call_count, 2, "it worked in between: the next outage is news")

    def test_a_refused_key_is_told_and_an_ordinary_refusal_is_not(self):
        self._refuse(400, b'{"error": {"type": "invalid_request_error", "message": "image too large"}}')
        self._refuse(529, b'{"error": {"type": "overloaded_error"}}')
        self.store.record_notification.assert_not_called()
        self._refuse(401, b'{"error": {"type": "authentication_error", "message": "invalid x-api-key"}}')
        self.assertEqual(self.store.record_notification.call_args.args[2], "connector:anthropic:key_refused")

    def test_the_bell_line_says_what_stopped_and_opens_where_to_fix_it(self):
        row = {"id": 5, "updatedAt": "2026-10-05T12:00:00+00:00", "readAt": None, "kind": "connector_trouble",
               "payload": {"provider": "anthropic", "reason": "out_of_credit"}}
        admin = Mock(is_admin=True, max_rating=None)
        view = app.notification_view(row, admin)
        self.assertEqual((view["title"], view["tone"], view["target"]),
                         ("Claude is out of credit", "warning", {"view": "settings", "section": "reader"}))
        refused = app.notification_view({**row, "payload": {"provider": "openai", "reason": "key_refused"}}, admin)
        self.assertEqual(refused["title"], "ChatGPT's API key was refused")
        self.assertIsNone(app.notification_view(row, Mock(is_admin=False, max_rating=None)), "a reader is not told")



class QuietSweepAndCoverCacheTests(unittest.TestCase):
    """Two causes of a slow library page after a deploy (2026-10-05)."""

    def test_an_empty_arrivals_sweep_writes_nothing(self):
        """The bell is asked on every page load; writing its mark each time
        made the database look changed and the catalog was rebuilt for it."""
        with tempfile.TemporaryDirectory() as folder:
            store = CatalogStore(Path(folder) / "catalog.db")
            now = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc)
            recent = (now - dt.timedelta(minutes=10)).isoformat()
            store.set_notification_mark("arrivals", recent)
            with patch("app.catalog_store", return_value=store):
                self.assertEqual(app.sweep_arrivals(now), 0)
                self.assertEqual(store.notification_mark("arrivals"), recent, "nothing new: the mark stays")
                stale = (now - dt.timedelta(hours=2)).isoformat()
                store.set_notification_mark("arrivals", stale)
                app.sweep_arrivals(now)
                self.assertGreater(store.notification_mark("arrivals"), stale, "an hour behind: moved, to keep the search small")

    def test_covers_are_kept_beside_the_catalog_under_a_budget(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict("app.os.environ", {"FLIPPARR_COVER_CACHE": str(Path(folder) / "cover-cache")}):
            path = Path(folder) / "Example 001.cbz"
            image = io.BytesIO()
            from PIL import Image
            Image.new("RGB", (40, 60), "red").save(image, format="PNG")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("001.png", image.getvalue())
            with patch("app.sweep_image_cache") as sweep:
                body = app.render_file_cover_thumbnail(path, "001.png")
            self.assertTrue(body.startswith(b"\xff\xd8"))
            self.assertEqual(len(list((Path(folder) / "cover-cache").glob("*.jpg"))), 1, "kept on the config volume")
            self.assertEqual(sweep.call_args.args[1], app.COVER_CACHE_MAX_BYTES, "and swept to its budget")



class IntakeEraCompatibilityTests(unittest.TestCase):
    """Intake against the cases AGENTS.md names, through the real scan. The
    Gate 2 checkpoint (2026-10-05) found a clean install merging Batman's
    1940, 2011, 2016 and 2025 runs into one: every file stated its run's
    year, before the number or in its folder, and none was an opening issue,
    the only kind once allowed to set an era."""

    def _comic(self, folder, relative, info=None):
        path = Path(folder) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        page = io.BytesIO()
        from PIL import Image
        Image.new("RGB", (8, 12), "blue").save(page, format="PNG")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("001.png", page.getvalue())
            if info:
                fields = "".join(f"<{key}>{value}</{key}>" for key, value in info.items())
                archive.writestr("ComicInfo.xml", f"<ComicInfo>{fields}</ComicInfo>")
        return path

    def _intake(self, folder):
        store = CatalogStore(Path(folder) / "catalog.db")
        scan_id = store.begin_scan(str(Path(folder) / "comics"), True)
        store.perform_scan(scan_id, app.scan_folder, app.inventory_file)
        with sqlite3.connect(store.database_path) as connection:
            rows = connection.execute(
                """SELECT files.filename, series_runs.canonical_title, series_runs.start_year,
                          file_identities.identity_kind, file_identities.issue_number, file_identities.volume_number
                   FROM files JOIN file_identities ON file_identities.file_id=files.id
                   JOIN series_runs ON series_runs.id=file_identities.series_run_id""").fetchall()
        runs = {}
        for filename, title, year, kind, issue, volume in rows:
            runs.setdefault((title, year), set()).add((kind, issue or volume))
        return runs, {filename: (title, year, kind, issue, volume) for filename, title, year, kind, issue, volume in rows}

    def test_eras_stated_in_the_name_stay_apart_without_an_opening_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Captain America (2005) #010.cbz", "Captain America (2011) #010 - Powerless, Part 5.cbz",
                         "Captain America (2012) #003.cbz", "Captain America (2011) #011.cbz"):
                self._comic(folder, f"comics/{name}")
            runs, _ = self._intake(folder)
        self.assertEqual(runs, {("Captain America", 2005): {("issue", "10")},
                                ("Captain America", 2011): {("issue", "10"), ("issue", "11")},
                                ("Captain America", 2012): {("issue", "3")}},
                         "2011 and 2012 are two series: a stated year has no slack")

    def test_eras_stated_by_folder_stay_apart_and_one_era_stays_together(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Batman (1940)/Batman #04.cbz", "Batman (2016)/Batman #04.cbz", "Batman (2016)/Batman #05.cbz",
                         "Nightwing (2016)/Nightwing #001.cbz", "Nightwing (2016)/Nightwing #050.cbz",
                         "Nightwing (2016)/Nightwing #100.cbz", "Publisher (2020)/Ironwood #02.cbz"):
                self._comic(folder, f"comics/{name}")
            runs, files = self._intake(folder)
        self.assertEqual(runs[("Batman", 1940)], {("issue", "4")})
        self.assertEqual(runs[("Batman", 2016)], {("issue", "4"), ("issue", "5")})
        self.assertEqual(runs[("Nightwing", 2016)], {("issue", "1"), ("issue", "50"), ("issue", "100")})
        self.assertIsNone(files["Ironwood #02.cbz"][1], "a folder that is not the run's own says nothing of its year")

    def test_the_folder_outranks_a_book_year_in_the_name(self):
        # Gate 2 re-run (2026-10-05): a graphic-novel series names each book
        # by its own year, and the folder holds the series' year.
        with tempfile.TemporaryDirectory() as folder:
            for name in ("The Adventure Zone (2019) #001 - Here There Be Gerblins.cbz",
                         "The Adventure Zone (2018) #002 - Murder on the Rockport Limited!.cbz",
                         "The Adventure Zone (2019) #006 - The Suffering Game.cbz"):
                self._comic(folder, f"comics/The Adventure Zone (2018)/{name}")
            runs, _ = self._intake(folder)
        self.assertEqual(runs, {("The Adventure Zone", 2018): {("issue", "1"), ("issue", "2"), ("issue", "6")}})

    def test_a_name_an_era_from_its_folder_is_another_runs_misfiled_issue(self):
        # Gate 2 (2026-10-05): two of the 2011 run's issues in the 2016 folder;
        # production's provider-backed catalog files them under 2011.
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Nightwing (2011)/Nightwing (2011) #024.cbz", "Nightwing (2016)/Nightwing (2011) #025.cbz",
                         "Nightwing (2016)/Nightwing (2016) #025.cbz"):
                self._comic(folder, f"comics/{name}")
            runs, _ = self._intake(folder)
        self.assertEqual(runs, {("Nightwing", 2011): {("issue", "24"), ("issue", "25")},
                                ("Nightwing", 2016): {("issue", "25")}})

    def test_a_year_after_the_number_is_a_cover_date_and_splits_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Saga 015 (2013).cbz", "Saga 030 (2015).cbz", "Saga 054 (2018).cbz"):
                self._comic(folder, f"comics/{name}")
            runs, _ = self._intake(folder)
        self.assertEqual(len(runs), 1, "later issues' dates do not invent eras")

    def test_embedded_only_and_conflicting_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            self._comic(folder, "comics/scan0001.cbz", {"Series": "Hawkeye", "Number": "3", "Volume": "2012"})
            self._comic(folder, "comics/scan0002.cbz", {"Series": "Hawkeye", "Number": "3", "Volume": "2016"})
            # The name says 2012, ComicInfo 2016: the name is the run's own clue
            # and wins; the disagreement does not create a third run.
            self._comic(folder, "comics/Hawkeye (2012) #004.cbz", {"Series": "Hawkeye", "Number": "4", "Volume": "2016"})
            runs, files = self._intake(folder)
        self.assertEqual({key for key in runs if key[0] == "Hawkeye"}, {("Hawkeye", 2012), ("Hawkeye", 2016)})
        self.assertEqual(files["Hawkeye (2012) #004.cbz"][1], 2012)

    def test_a_comicinfo_volume_picks_the_nearest_existing_run(self):
        # Production (2026-10-05): Justice League #056, ComicInfo Volume 2018,
        # with runs from 2016 and 2018 both within the slack.
        with tempfile.TemporaryDirectory() as folder:
            # The 2016 run comes first, so the title's alias points at it.
            self._comic(folder, "comics/Justice League (2016)/Justice League #001.cbz")
            self._intake(folder)
            self._comic(folder, "comics/Justice League (2018)/Justice League #002.cbz")
            self._intake(folder)
            self._comic(folder, "comics/Justice League #056.cbz", {"Series": "Justice League", "Number": "56", "Volume": "2018"})
            runs, files = self._intake(folder)
        self.assertEqual(set(runs), {("Justice League", 2016), ("Justice League", 2018)})
        self.assertEqual(files["Justice League #056.cbz"][:2], ("Justice League", 2018))

    def test_a_comicinfo_volume_that_is_a_cover_year_splits_nothing(self):
        # Production (2026-10-05): DIE: Loaded #9, a 2025 series, carries
        # ComicInfo Volume 2026 -- its own year, not the run's.
        with tempfile.TemporaryDirectory() as folder:
            self._comic(folder, "comics/Die Loaded #001.cbz", {"Series": "Die Loaded", "Number": "1", "Volume": "2025"})
            self._comic(folder, "comics/Die Loaded #009.cbz", {"Series": "Die Loaded", "Number": "9", "Volume": "2026"})
            runs, _ = self._intake(folder)
        self.assertEqual(runs, {("Die Loaded", 2025): {("issue", "1"), ("issue", "9")}})

    def test_manga_volumes_keep_their_numbers_and_issues_and_volumes_share_a_run(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Blue Lock (2021) v18.cbz", "Blue Lock (2021) v19.cbz",
                         "Saga (2012) #001.cbz", "Saga (2012) Vol. 1.cbz"):
                self._comic(folder, f"comics/{name}")
            runs, files = self._intake(folder)
        self.assertEqual(runs[("Blue Lock", 2021)], {("edition", 18), ("edition", 19)})
        self.assertEqual(runs[("Saga", 2012)], {("issue", "1"), ("edition", 1)},
                         "both formats under one run, each owned on its own")


class ImportRestartSafetyTests(unittest.TestCase):
    """Gate 3 (2026-10-05): an import cut off by a restart. `docker stop`
    does not wait for the import worker, so a copy can be left half-written
    beside its destination under a name no later import looked for -- and,
    ending in .cbz, the next scan would have catalogued it."""

    def _comic(self, path, page=b"page"):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("001.jpg", page)
        return path

    def _context(self, **extra):
        return {"seriesTitle": "Saga", "seriesYear": 2012, "publisher": "Image Comics",
                "issueNumber": "1", "issueTitle": "Chapter One", "existingDirectory": None, **extra}

    def test_a_copy_left_by_a_killed_import_is_never_catalogued(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "Saga (2012)"
            self._comic(run / "Saga (2012) #001.cbz")
            (run / ".Saga (2012) #002.flipparr-1-140235.partial.cbz").write_bytes(b"PK\x03\x04 half")
            names = [parsed.filename for parsed in scan_folder(folder)]
        self.assertEqual(names, ["Saga (2012) #001.cbz"])

    def test_a_retried_import_clears_the_copy_an_earlier_process_left(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library, completed = root / "library", root / "completed"
            source = self._comic(completed / "Saga.001.2012" / "Saga 001 (2012).cbz")
            run = library / "Image Comics" / "Saga (2012)"
            run.mkdir(parents=True)
            stale = run / ".Saga (2012) #001 - Chapter One.flipparr-1-140235.partial.cbz"
            stale.write_bytes(b"PK\x03\x04 half")
            before = app.PROCESS_STARTED_AT - 60
            os.utime(stale, (before, before))
            store = Mock()
            store.get_acquisition_job_context.return_value = self._context()
            with patch("app.catalog_store", return_value=store):
                result = import_downloaded_comic(
                    {"job_id": 7, "sab_storage": str(source.parent), "release_title": "Saga.001.2012"},
                    library_root=library, completed_root=completed)
            self.assertFalse(stale.exists(), "the earlier process's copy is gone")
            self.assertEqual(Path(result["destination"]).read_bytes(), source.read_bytes())
            self.assertEqual(sorted(path.name for path in run.iterdir()), ["Saga (2012) #001 - Chapter One.cbz"])

    def test_copies_nobody_will_import_again_are_cleared_at_start(self):
        # Review of Gate 3: only a re-import of the same comic cleared one.
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder)
            stale = library / "Image Comics" / "Saga (2012)" / ".Saga (2012) #009.flipparr-1-77.partial.cbz"
            live = library / "Image Comics" / "Saga (2012)" / ".Saga (2012) #010.flipparr-1-78.partial.cbz"
            kept = self._comic(library / "Image Comics" / "Saga (2012)" / "Saga (2012) #001.cbz")
            held = library / ".flipparr" / "quarantine" / "9" / ".Saga (2012) #002.flipparr-1-79.partial.cbz"
            for path in (stale, live, held):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"half")
            before = app.PROCESS_STARTED_AT - 60
            for path in (stale, held):
                os.utime(path, (before, before))
            self.assertEqual(app.clear_abandoned_import_partials(library), 1)
            self.assertFalse(stale.exists())
            self.assertTrue(live.exists(), "one being written now is left")
            self.assertTrue(held.exists(), "Flipparr's own folders are not walked")
            self.assertTrue(kept.exists())

    def test_a_copy_this_process_is_writing_is_left_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "Saga (2012) #001.cbz"
            live = Path(folder) / ".Saga (2012) #001.flipparr-1-999.partial.cbz"
            live.write_bytes(b"PK\x03\x04 being written")
            app._clear_stale_partials(destination)
            self.assertTrue(live.exists())

    def test_an_import_that_would_fill_the_disk_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library, completed = root / "library", root / "completed"
            source = self._comic(completed / "Saga.001.2012" / "Saga 001 (2012).cbz")
            library.mkdir()
            store = Mock()
            store.get_acquisition_job_context.return_value = self._context()
            nearly_full = app.shutil._ntuple_diskusage(total=10 << 30, used=10 << 30, free=1 << 20)
            with patch("app.catalog_store", return_value=store), patch("app.shutil.disk_usage", return_value=nearly_full):
                with self.assertRaisesRegex(ValueError, "minimum free disk space"):
                    import_downloaded_comic(
                        {"job_id": 7, "sab_storage": str(source.parent), "release_title": "Saga.001.2012"},
                        library_root=library, completed_root=completed)
            self.assertEqual([path for path in library.rglob("*") if path.is_file()], [])

    def test_a_replacement_that_fails_after_the_swap_puts_the_original_back(self):
        # Gate 3: the rollback had no test. The rejected new copy is kept for
        # a look, beside the quarantined original, never deleted.
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / "library"
            original = library / "Image Comics" / "Saga (2012)" / "Saga (2012) #001.cbz"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"new-but-bad")
            quarantine = library / ".flipparr" / "quarantine" / "9" / "Image Comics" / "Saga (2012)" / original.name
            quarantine.parent.mkdir(parents=True)
            quarantine.write_bytes(b"the-original")
            store = Mock()
            app._rollback_replacement_swap(store, 9, original, quarantine, original)
            self.assertEqual(original.read_bytes(), b"the-original")
            kept = [path for path in quarantine.parent.iterdir() if ".failed-new-" in path.name]
            self.assertEqual([path.read_bytes() for path in kept], [b"new-but-bad"])
            store.restore_replacement_original.assert_called_once_with(9, str(quarantine), str(original), str(kept[0]))

    def test_a_replacement_retried_after_its_swap_finished_is_already_done(self):
        # Killed after the rename, before the row said imported: the catalog
        # already points the original at its quarantine copy, so the retry
        # finds the new comic in place rather than refusing it as identical.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library, completed = root / "library", root / "completed"
            source = self._comic(completed / "Saga.001.2012" / "Saga 001 (2012).cbz", b"replacement")
            destination = library / "Image Comics" / "Saga (2012)" / "Saga (2012) #001.cbz"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(source.read_bytes())
            quarantine = library / ".flipparr" / "quarantine" / "9" / "Image Comics" / "Saga (2012)" / destination.name
            quarantine.parent.mkdir(parents=True)
            quarantine.write_bytes(b"damaged-original")
            store = Mock()
            store.get_acquisition_job_context.return_value = self._context(
                issueTitle=None, existingDirectory=str(destination.parent),
                replacementId="9", replacementFileId="4", replacementFilePath=str(quarantine))
            with patch("app.catalog_store", return_value=store):
                result = import_downloaded_comic(
                    {"job_id": 7, "sab_storage": str(source.parent), "release_title": "Saga.001.2012"},
                    library_root=library, completed_root=completed)
            self.assertTrue(result["alreadyPresent"])
            self.assertEqual(quarantine.read_bytes(), b"damaged-original", "the original stays recoverable")


class ServiceOutageTests(unittest.TestCase):
    """Gate 3 (2026-10-05): a service that stops answering is waited for,
    counted against nothing, and named to the admins once it has been long
    enough to need them."""

    def setUp(self):
        app._SERVICE_TROUBLE.clear()
        self.addCleanup(app._SERVICE_TROUBLE.clear)

    def _store(self):
        store = Mock()
        store.list_users.return_value = [{"id": 1, "role": "admin", "disabled": False},
                                         {"id": 2, "role": "reader", "disabled": False}]
        return store

    def test_the_admins_hear_of_an_outage_once_it_has_lasted(self):
        store = self._store()
        with patch("app.catalog_store", return_value=store), patch("app.time.time", return_value=1_000_000.0):
            app.service_trouble("sabnzbd", ConnectionRefusedError(61, "Connection refused"))
        store.record_notification.assert_not_called()
        later = 1_000_000.0 + app.SERVICE_TROUBLE_NOTICE_SECONDS
        with patch("app.catalog_store", return_value=store), patch("app.time.time", return_value=later):
            app.service_trouble("sabnzbd", ConnectionRefusedError(61, "Connection refused"))
            app.service_trouble("sabnzbd", ConnectionRefusedError(61, "Connection refused"))
        store.record_notification.assert_called_once()
        self.assertEqual(store.record_notification.call_args.args[:2], (1, "service_trouble"), "the admin, once")
        self.assertEqual(app.service_troubles()[0]["name"], "SABnzbd")
        app.service_answered("sabnzbd")
        self.assertEqual(app.service_troubles(), [])

    def test_the_bell_says_what_waits(self):
        admin = Mock(is_admin=True, max_rating=None)
        reader = Mock(is_admin=False, max_rating=None)
        row = {"id": 5, "updatedAt": "2026-10-05T12:00:00+00:00", "readAt": None, "kind": "service_trouble",
               "payload": {"service": "prowlarr"}}
        view = app.notification_view(row, admin)
        self.assertEqual(view["title"], "Prowlarr isn't answering")
        self.assertIn("Searches wait", view["detail"])
        self.assertEqual(view["target"], {"view": "settings", "section": "acquisition"})
        self.assertIsNone(app.notification_view(row, reader), "a reader is not told")

    def test_a_finished_download_flipparr_cannot_see_is_a_folder_in_trouble(self):
        stop = _QuickStop()
        store = Mock()
        store.pending_acquisition_downloads.return_value = [{"id": 1, "job_id": 11, "source": "qbittorrent"}]

        def unseen(download):
            stop.set()
            return {"status": "waiting_for_files", "detail": "qBittorrent saved this torrent in “Downloads”"}

        with patch("app.catalog_store", return_value=store), patch("app.log_event"), \
                patch("app.reconcile_acquisition_download", side_effect=unseen), \
                patch.object(app, "_KEPT_SWEPT_AT", [time.monotonic()]):
            app.acquisition_import_worker(stop)
        self.assertEqual([entry["name"] for entry in app.service_troubles()], ["The torrents folder"])
        view = app.notification_view({"id": 6, "updatedAt": "2026-10-05T12:00:00+00:00", "readAt": None,
                                      "kind": "service_trouble", "payload": {"service": "qbittorrent_folder"}},
                                     Mock(is_admin=True, max_rating=None))
        self.assertEqual(view["title"], "Flipparr can't see the torrents folder")
        self.assertIn("until it is mounted", view["detail"])

    def test_a_pass_stops_at_the_first_silence(self):
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [1, 2, 3]
        asked = []

        def search(job_id):
            asked.append(job_id)
            app.service_trouble("prowlarr", TimeoutError("Prowlarr did not answer"))
            return None

        with patch("app.catalog_store", return_value=store), patch("app._auto_grab_release", side_effect=search), \
                patch("app._issues_awaiting_a_pack", return_value=set()), patch("app.log_event"):
            app._automatic_release_grabs(None, backoff=True)
        self.assertEqual(asked, [1, 2], "a second issue confirms it; then no more deadlines are waited out")
        store.count_unanswered_search.assert_not_called()

    def test_one_issue_prowlarr_cannot_search_does_not_hold_up_the_rest(self):
        # Review of Gate 3 (2026-10-05): put back uncounted and due at once,
        # an issue whose own query Prowlarr errors on was asked first on every
        # pass, the pass stopped there, and nothing behind it was searched.
        store = Mock()
        store.acquisition_jobs_awaiting_release.return_value = [1, 2, 3]
        asked = []

        def search(job_id):
            asked.append(job_id)
            if job_id == 1:
                app.service_trouble("prowlarr", urllib.error.HTTPError("http://p", 500, "Internal Server Error", None, None))
            else:
                app.service_answered("prowlarr")
            return None

        with patch("app.catalog_store", return_value=store), patch("app._auto_grab_release", side_effect=search), \
                patch("app._issues_awaiting_a_pack", return_value=set()), patch("app.log_event"):
            app._automatic_release_grabs(None, backoff=True)
        self.assertEqual(asked, [1, 2, 3])
        store.count_unanswered_search.assert_called_once()
        self.assertEqual(store.count_unanswered_search.call_args.args[0], 1, "its failure is its own, and its backoff applies")
        self.assertEqual(app.service_troubles(), [], "Prowlarr answered: it is not in trouble")

    def _one_pass(self, downloads, reconcile):
        """The import worker, for one whole pass."""
        stop = _StopAfterOnePass()
        store = self._store()
        store.pending_acquisition_downloads.return_value = downloads
        with patch("app.catalog_store", return_value=store), patch("app.log_event"), \
                patch("app.clear_abandoned_import_partials"), \
                patch("app.reconcile_acquisition_download", side_effect=reconcile), \
                patch.object(app, "_KEPT_SWEPT_AT", [time.monotonic()]):
            app.acquisition_import_worker(stop)
        return store

    def test_the_import_worker_notes_which_client_is_silent_and_when_it_answers(self):
        def reconcile(download):
            if download["source"] == "qbittorrent":
                raise ConnectionResetError(54, "Connection reset by peer")
            return {"status": "downloading"}

        app.service_trouble("sabnzbd", "earlier")
        self._one_pass([{"id": 1, "job_id": 11, "source": "qbittorrent"}, {"id": 2, "job_id": 12, "source": "sabnzbd"}],
                       reconcile)
        self.assertEqual([entry["service"] for entry in app.service_troubles()], ["qbittorrent"])

    def test_trouble_goes_when_the_download_that_was_waiting_is_gone(self):
        # Review of Gate 3: a folder Flipparr could not see stayed listed after
        # its download was stopped, until the next import or a restart.
        app.service_trouble("qbittorrent_folder", "not visible")
        app.service_trouble("sabnzbd", ConnectionRefusedError(61, "refused"))
        self._one_pass([], lambda download: {"status": "downloading"})
        self.assertEqual(app.service_troubles(), [])

    def test_a_refused_qbittorrent_sign_in_is_told_at_once_and_says_what_to_do(self):
        # Review of Gate 3: this was swallowed, qBittorrent marked as
        # answering, and nobody told.
        refusal = app.torrent_client.TorrentCredentialsRefused("qBittorrent rejected this username and password")

        def refused(download):
            raise refusal

        store = self._one_pass([{"id": 1, "job_id": 11, "source": "qbittorrent"}], refused)
        store.update_acquisition_download.assert_not_called()
        store.record_notification.assert_called_once()
        payload = store.record_notification.call_args.args[3]
        self.assertEqual((payload["service"], payload["reason"]), ("qbittorrent", "refused"))
        entry = app.service_troubles()[0]
        self.assertEqual(entry["title"], "qBittorrent refused Flipparr's sign-in")
        self.assertIn("will not try again until you save", entry["detail"])
        view = app.notification_view({"id": 7, "updatedAt": "2026-10-05T12:00:00+00:00", "readAt": None,
                                      "kind": "service_trouble", "payload": payload}, Mock(is_admin=True, max_rating=None))
        self.assertEqual(view["title"], "qBittorrent refused Flipparr's sign-in")

    def test_a_client_turned_off_is_said_as_turned_off(self):
        def off(download):
            raise app.AcquisitionServiceOff("Connect and enable SABnzbd in Settings first")

        self._one_pass([{"id": 1, "job_id": 11, "source": "sabnzbd"}], off)
        entry = app.service_troubles()[0]
        self.assertEqual((entry["reason"], entry["title"]), ("off", "SABnzbd is turned off"))
        self.assertEqual(app.service_trouble_words("qbittorrent_folder", "folder")[0], "Flipparr can't see the torrents folder")

    def test_qbittorrent_off_or_refusing_is_left_to_the_worker_not_swallowed(self):
        store = Mock()
        download = {"id": 1, "job_id": 11, "source": "qbittorrent", "status": "downloading", "sab_nzo_id": "torrent:abc:11"}
        client = Mock()
        client.info.side_effect = app.torrent_client.TorrentCredentialsRefused("rejected")
        with patch("app._qbittorrent_client", return_value=client):
            with self.assertRaises(app.torrent_client.TorrentCredentialsRefused):
                app._qbt_download_storage(store, download)
        with patch("app._qbittorrent_client", side_effect=app.AcquisitionServiceOff("off")):
            with self.assertRaises(app.AcquisitionServiceOff):
                app._qbt_download_storage(store, download)
        self.assertTrue(issubclass(app.torrent_client.TorrentClientError, app.SERVICE_HICCUPS))

    def test_a_json_error_from_inside_an_import_is_the_downloads_own(self):
        # Review of Gate 3: by type alone it read as SABnzbd being silent, and
        # the download would have been retried for ever.
        self.assertFalse(issubclass(json.JSONDecodeError, app.SERVICE_HICCUPS))

        def broken(download):
            raise json.JSONDecodeError("Expecting value", "{", 0)

        store = self._one_pass([{"id": 1, "job_id": 11, "source": "sabnzbd"}], broken)
        self.assertEqual(store.update_acquisition_download.call_args.args[1], "failed")
        self.assertEqual(app.service_troubles(), [])
        # ... while a reply from SABnzbd that is not JSON is SABnzbd's.
        with patch("app._enabled_acquisition_service", return_value={"url": "http://sab", "apiKey": "k"}), \
                patch("app.fetch_json_with_headers", side_effect=json.JSONDecodeError("Expecting value", "<html>", 0)):
            with self.assertRaisesRegex(app.DownloadClientUnanswered, "not JSON"):
                app._sab_history_slot({"sab_nzo_id": "x"})


class StartupPreconditionTests(unittest.TestCase):
    """Gate 4 (2026-10-05): a container that cannot serve says so in one
    line and stops, rather than coming up "healthy" with every page a 500."""

    def test_a_config_folder_flipparr_cannot_write_refuses_to_start(self):
        with tempfile.TemporaryDirectory() as folder:
            locked = Path(folder) / "config"
            locked.mkdir()
            locked.chmod(0o500)
            try:
                with patch("app.catalog_database_path", return_value=locked / "flipparr.db"):
                    with self.assertRaises(app.StartupRefused) as refused:
                        app.check_startup_preconditions()
            finally:
                locked.chmod(0o700)
        self.assertIn("not writable", refused.exception.reason)
        self.assertIn("PUID:PGID", refused.exception.reason)
        self.assertNotEqual(refused.exception.code, 0, "the process exits non-zero")

    def test_a_catalog_from_a_newer_build_refuses_to_start_and_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "flipparr.db"
            with patch("app.catalog_database_path", return_value=database), patch.object(app, "_CATALOG_STORE", None):
                app.check_startup_preconditions()
                with sqlite3.connect(database) as connection:
                    connection.execute("UPDATE schema_info SET version=?", (SCHEMA_VERSION + 1,))
                with patch.object(app, "_CATALOG_STORE", None):
                    with self.assertRaises(app.StartupRefused) as refused:
                        app.check_startup_preconditions()
        self.assertIn("newer Flipparr", refused.exception.reason)
        self.assertIn("restore the backup", refused.exception.reason)

    def test_a_good_start_reports_where_and_what_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "config" / "flipparr.db"
            with patch("app.catalog_database_path", return_value=database), patch.object(app, "_CATALOG_STORE", None):
                found = app.check_startup_preconditions()
                self.assertTrue(database.is_file(), "a missing config folder it may create is created, and the catalog in it")
        self.assertEqual(found["schema"], SCHEMA_VERSION)


class VisionBudgetTests(unittest.TestCase):
    """Review (2026-10-06): a reader allowed vision could script every page
    of the library through a paid model."""

    def setUp(self):
        app._VISION_SPEND.clear()
        self.addCleanup(app._VISION_SPEND.clear)

    def test_a_reader_has_a_days_worth_of_readings_and_no_more(self):
        for _ in range(app.VISION_PAGES_PER_READER_PER_DAY):
            self.assertTrue(app.vision_budget_allows(7))
        self.assertFalse(app.vision_budget_allows(7))
        self.assertTrue(app.vision_budget_allows(8), "another profile's own")

    def test_one_page_is_read_once_however_many_ask_at_once(self):
        calls = []

        def slow(file_id, index, *, allow_vision):
            calls.append((file_id, index))
            time.sleep(0.05)
            return {"segmented": True, "panels": []}

        with patch("app._file_page_panels", side_effect=slow):
            threads = [threading.Thread(target=lambda: app.file_page_panels(3, 4)) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(calls), 6, "each waits its turn (the second finds the stamp the first wrote)")
        self.assertEqual(app._PANELS_INFLIGHT, {})


class _StopAfterOnePass(threading.Event):
    """A stop event that lets a worker loop finish one whole pass."""

    def wait(self, timeout=None):
        self.set()
        return True


class _QuickStop(threading.Event):
    """A stop event whose waits return at once, so a worker loop runs a turn."""

    def wait(self, timeout=None):
        return self.is_set()


class SystemStatusTests(unittest.TestCase):
    """Settings -> System: workers say they are alive, a dead one is
    restarted (a few times an hour at most), and the support file carries no
    secret (2026-10-05)."""

    def setUp(self):
        for table in (app._WORKER_BEATS, app._WORKER_PROBLEMS, app._WORKER_RESTARTS):
            table.clear()
        app._WORKERS_GIVEN_UP.clear()

    def _specs(self, threads):
        def starter(worker_id):
            def start():
                thread = threading.Thread(target=lambda: None, name=f"t-{worker_id}")
                thread.start()
                thread.join()
                threads[worker_id] = thread
                return thread
            return start
        return tuple(app.WorkerSpec(worker_id, worker_id.title(), "", f"t-{worker_id}", 60,
                                    starter(worker_id), lambda worker_id=worker_id: threads.get(worker_id))
                     for worker_id in ("alpha", "beta"))

    def test_a_dead_worker_is_restarted_and_one_that_keeps_dying_is_left_stopped(self):
        threads = {}
        specs = self._specs(threads)
        with patch("app.worker_specs", return_value=specs), patch("app.log_event") as logged:
            specs[0].start()                     # alpha ran and ended; beta never started
            app.worker_beat("alpha")
            self.assertEqual(app.restart_dead_workers(now=1000.0), ["alpha"], "beta was never started here")
            for minute in range(1, 6):
                app.restart_dead_workers(now=1000.0 + minute * 60)
            states = {state["id"]: state for state in app.worker_states(now=1400.0)}
        self.assertEqual(states["alpha"]["state"], "stopped")
        self.assertEqual(states["alpha"]["restartsLastHour"], app.WORKER_RESTARTS_PER_HOUR)
        self.assertEqual(states["beta"]["state"], "off")
        events = [call.args[0] for call in logged.call_args_list]
        self.assertEqual(events.count("worker_restarted"), app.WORKER_RESTARTS_PER_HOUR)
        self.assertEqual(events.count("worker_stopped"), 1, "said once, not every half minute")

    def test_a_running_worker_is_running_until_it_has_been_quiet_too_long(self):
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, name="t-alpha", daemon=True)
        thread.start()
        self.addCleanup(stop.set)
        spec = app.WorkerSpec("alpha", "Alpha", "", "t-alpha", 60, lambda: thread, lambda: thread)
        with patch("app.worker_specs", return_value=(spec,)):
            app._WORKER_BEATS["alpha"] = 1000.0
            self.assertEqual(app.worker_states(now=1030.0)[0]["state"], "running")
            self.assertEqual(app.worker_states(now=1100.0)[0]["state"], "quiet")

    def test_a_thread_that_crashes_is_logged_and_its_worker_remembers_why(self):
        spec = app.WorkerSpec("alpha", "Alpha", "", "t-alpha", 60, lambda: None, lambda: None)

        def crash():
            raise RuntimeError("disk on fire")

        thread = threading.Thread(target=crash, name="t-alpha")
        with patch("app.worker_specs", return_value=(spec,)), patch("app.log_event") as logged, \
                patch("threading.excepthook", app._thread_crashed):
            thread.start()
            thread.join()
        self.assertEqual(logged.call_args.args[0], "thread_crashed")
        self.assertEqual(logged.call_args.kwargs["worker"], "alpha")
        self.assertIn("disk on fire", app._WORKER_PROBLEMS["alpha"]["message"])

    def test_the_metadata_worker_outlives_an_exception_in_one_turn(self):
        stop = _QuickStop()
        turns = []

        def step(store, event):
            turns.append(1)
            if len(turns) == 1:
                raise RuntimeError("provider returned nonsense")
            event.set()

        with patch("app.catalog_store"), patch("app._metadata_enrichment_step", side_effect=step), \
                patch("app.log_event"):
            app.metadata_enrichment_worker(stop)
        self.assertEqual(len(turns), 2, "the loop went on after the failure")
        self.assertIn("provider returned nonsense", app._WORKER_PROBLEMS["metadata"]["message"])

    def test_an_import_pass_that_fails_is_logged_not_swallowed(self):
        stop = _QuickStop()
        store = Mock()

        def fail():
            stop.set()
            raise RuntimeError("database is locked")

        store.pending_acquisition_downloads.side_effect = fail
        with patch("app.catalog_store", return_value=store), patch("app.log_event") as logged, \
                patch.object(app, "_KEPT_SWEPT_AT", [time.monotonic()]):
            app.acquisition_import_worker(stop)
        self.assertIn("acquisition_import_cycle_failed", [call.args[0] for call in logged.call_args_list])
        self.assertIn("database is locked", app._WORKER_PROBLEMS["imports"]["message"])

    def test_a_download_client_that_drops_the_line_fails_no_download(self):
        # Gate 3 (2026-10-05): a reset connection or half a reply was "Import
        # coordinator failed" -- the download and its issue failed for good.
        import http.client
        for hiccup in (ConnectionResetError(54, "Connection reset by peer"),
                       http.client.RemoteDisconnected("Remote end closed connection without response"),
                       http.client.IncompleteRead(b"{\"queue\""),
                       app.DownloadClientUnanswered("SABnzbd answered with something that is not JSON"),
                       app.AcquisitionServiceOff("Connect and enable SABnzbd in Settings first")):
            with self.subTest(type(hiccup).__name__):
                app._WORKER_PROBLEMS.clear()
                stop = _QuickStop()
                store = Mock()
                store.pending_acquisition_downloads.return_value = [
                    {"id": 1, "job_id": 11, "source": "sabnzbd"}, {"id": 2, "job_id": 12, "source": "qbittorrent"}]

                def unanswered(download):
                    if download["id"] == 2:
                        stop.set()
                    raise hiccup

                with patch("app.catalog_store", return_value=store), patch("app.log_event") as logged, \
                        patch("app.reconcile_acquisition_download", side_effect=unanswered), \
                        patch.object(app, "_KEPT_SWEPT_AT", [time.monotonic()]):
                    app.acquisition_import_worker(stop)
                store.update_acquisition_download.assert_not_called()
                store.update_acquisition_job.assert_not_called()
                events = [call.args[0] for call in logged.call_args_list]
                self.assertEqual(events.count("acquisition_import_transient"), 1, "once a pass, not once a download")
                self.assertIn(type(hiccup).__name__, app._WORKER_PROBLEMS["imports"]["message"])

    def test_a_download_that_breaks_the_importer_still_fails_and_says_so(self):
        stop = _QuickStop()
        store = Mock()
        store.pending_acquisition_downloads.return_value = [{"id": 1, "job_id": 11, "source": "sabnzbd"}]

        def broken(download):
            stop.set()
            raise KeyError("sab_storage")

        with patch("app.catalog_store", return_value=store), patch("app.log_event"), \
                patch("app.reconcile_acquisition_download", side_effect=broken), \
                patch.object(app, "_KEPT_SWEPT_AT", [time.monotonic()]):
            app.acquisition_import_worker(stop)
        store.update_acquisition_download.assert_called_once()
        self.assertEqual(store.update_acquisition_download.call_args.args[1], "failed")

    def test_the_servers_own_notices_join_the_structured_log(self):
        """Waitress wrote "Task queue depth is 4" as bare text on stderr."""
        import logging
        app._route_server_logs()
        app._route_server_logs()   # asked twice, attached once
        handlers = [handler for handler in logging.getLogger("waitress").handlers
                    if isinstance(handler, app._ServerLogHandler)]
        self.assertEqual(len(handlers), 1)
        with patch("app.log_event") as logged:
            logging.getLogger("waitress.queue").warning("Task queue depth is %d", 4)
            logging.getLogger("waitress").error("something broke")
        self.assertEqual(logged.call_args_list[0].args[0], "server_queue")
        self.assertEqual(logged.call_args_list[0].kwargs["level"], "info", "waiting a moment is not a problem to list")
        self.assertEqual((logged.call_args_list[1].args[0], logged.call_args_list[1].kwargs["level"]), ("server_notice", "error"))
        self.assertEqual(app.http_thread_count(), 32)

    def test_a_refused_vision_request_says_what_the_service_said(self):
        import email.message, io
        body = json.dumps({"error": {"type": "invalid_request_error", "message": "image exceeds 5 MB maximum"}}).encode()
        refusal = urllib.error.HTTPError("https://api.example/v1", 400, "Bad Request", email.message.Message(), io.BytesIO(body))
        self.assertEqual(app._vision_error(refusal), ("invalid_request_error", "image exceeds 5 MB maximum"))

    def test_the_support_file_carries_no_secret(self):
        scrubbed = app.support_safe({
            "detail": "GET http://sab:8080/api?apikey=abc123&mode=queue failed",
            "url": "https://user:hunter2@indexer.example/rss",
            "apiKey": "abc123", "nested": [{"password": "hunter2", "note": "token=xyz"}],
            "count": 3,
        })
        text = json.dumps(scrubbed)
        for secret in ("abc123", "hunter2", "xyz"):
            self.assertNotIn(secret, text)
        self.assertEqual(scrubbed["count"], 3)
        self.assertIn("mode=queue", scrubbed["detail"], "only the secret goes")

    def test_recent_problems_keep_warnings_newest_first_and_leave_info_out(self):
        app.RECENT_PROBLEMS.clear()
        app.log_event("all_fine", level="info")
        app.log_event("first_trouble", level="warning", detail="sab said apikey=abc123 was wrong")
        app.log_event("second_trouble", level="error", error="boom")
        problems = app.recent_problems()
        self.assertEqual([problem["event"] for problem in problems], ["second_trouble", "first_trouble"])
        self.assertNotIn("abc123", json.dumps(problems))

