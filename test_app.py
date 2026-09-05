import io
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import traceback
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import app
from app import CompletedDownloadNotVisible, Handler, MetadataRateLimited, PAGE, ReleaseDownloadError, _RELEASE_CANDIDATES, _gcd_discovery_search_rows, _comic_vine_issue_entries, _hydrate_gcd_issue_entries_with_status, _metron_collected_edition_candidates, _metron_issue_entries, _metron_reprint_coverage, _resolve_sab_download_source, assess_identity_confidence, batch_enrich, catalog_api_payload, confirm_gcd_series_collection, confirm_gcd_series_run, discover_gcd_series, discover_metron_series, discover_series, embedded_epub_candidate, enrich, enrich_catalog_series, extract_issue_coverage, file_cover_info, find_archive_cover_member, import_downloaded_comic, inspect_file_health, inventory_file, lookup_identity, parse_filename, post_multipart_file_json, public_acquisition_service_config, public_provider_config, rank_gcd_series_runs, read_embedded_metadata, reconcile_acquisition_download, request_discovered_gcd_series, request_discovered_series, render_batch_results, run_metadata_enrichment_job, save_acquisition_service_config, save_provider_config, scan_folder, score_candidate, search_file_match_candidates, search_gcd, search_google_books, search_open_library, search_prowlarr_releases, send_release_to_sabnzbd, sync_gcd_issue_catalog, sync_issue_catalog, test_acquisition_service_connection


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
        with patch("app.urllib.request.urlopen", side_effect=original), self.assertRaises(
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
            return_value=("2025", "https://metron.example/series/2025/", entries),
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
            [{"number": "1"}, {"number": "2"}],
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
            result = catalog_api_payload()

        enrichment = result["enrichment"]
        self.assertFalse(enrichment["allProvidersCooling"])
        self.assertEqual(enrichment["availableProviders"], ["metron", "comic_vine"])

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
        with patch("app.urllib.request.urlopen", return_value=io.BytesIO(b'{"status": true}')) as open_url:
            result = post_multipart_file_json(
                "http://sab/api?mode=addfile",
                field_name="name",
                filename="Absolute Batman 004.nzb",
                content=b"<nzb></nzb>",
                content_type="application/x-nzb",
                headers={"Accept": "application/json"},
            )

        request = open_url.call_args.args[0]
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

    def test_discovery_prefers_configured_metron_over_gcd(self):
        metron_result = {
            "query": "Saga", "provider": "Metron", "providerId": "metron",
            "results": [{
                "provider": "metron", "providerSeriesId": "9", "title": "Saga",
                "yearBegan": 2012, "publisher": "Image Comics", "cover": None,
            }],
        }
        comic_result = {
            "query": "Saga", "provider": "Comic Vine", "providerId": "comic_vine",
            "results": [{
                "provider": "comic_vine", "providerSeriesId": "8", "title": "Saga",
                "yearBegan": 2012, "publisher": "Image", "cover": "https://covers.test/saga.jpg",
            }],
        }
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
            "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app.discover_metron_series", return_value=metron_result) as metron, patch(
            "app.discover_comic_vine_series", return_value=comic_result
        ) as comic_vine, patch("app.discover_gcd_series") as gcd:
            result = discover_series("Saga")
        self.assertEqual(result["providerId"], "metron")
        self.assertEqual(result["results"][0]["cover"], "https://covers.test/saga.jpg")
        self.assertEqual(result["results"][0]["coverProvider"], "Comic Vine")
        metron.assert_called_once_with("Saga", "token")
        comic_vine.assert_called_once_with("Saga", "key")
        gcd.assert_not_called()

    def test_discovery_falls_back_to_comic_vine_when_metron_is_busy(self):
        comic_result = {
            "query": "Saga", "provider": "Comic Vine", "providerId": "comic_vine",
            "results": [{"provider": "comic_vine", "providerSeriesId": "8", "title": "Saga"}],
        }
        with patch("app.load_provider_config", return_value={
            "metron": {"enabled": True, "token": "token"},
            "comic_vine": {"enabled": True, "apiKey": "key"},
        }), patch("app.discover_metron_series", side_effect=RuntimeError("rate limited")), patch(
            "app.discover_comic_vine_series", return_value=comic_result
        ), patch("app.discover_gcd_series") as gcd:
            result = discover_series("Saga")
        self.assertEqual(result["providerId"], "comic_vine")
        self.assertEqual(result["fallbacks"][0]["provider"], "Metron")
        gcd.assert_not_called()

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
            return "20", "https://metron/20", [{"number": "1"}, {"number": "2"}]

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
            payload = catalog_api_payload()
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
            series_id, _, entries = _metron_issue_entries(context, "token")
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
            "app._discovery_library_keys", return_value=set()
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
                with self.assertRaisesRegex(ValueError, "No downloaded comic confidently matched Saga #1"):
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


if __name__ == "__main__":
    unittest.main()
