import base64
import contextlib
import email.message
import hashlib
import io
import json
import time
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

import torrent_client
from torrent_client import QBittorrent, TorrentAuthError, TorrentClientError


def bencode(value):
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, str):
        return bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(key) + bencode(item) for key, item in sorted(value.items())) + b"e"
    raise TypeError(value)


INFO = {b"length": 1234, b"name": b"Blue Lock v34 (2025) (Digital).cbz", b"piece length": 16384, b"pieces": b"x" * 20}
TORRENT = bencode({b"announce": b"http://tracker.example/announce", b"info": INFO, b"comment": b"after the info"})
HASH = hashlib.sha1(bencode(INFO)).hexdigest()  # noqa: S324


class FakeClient:
    """A qBittorrent that answers from a script and remembers what it was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        status, body, cookies = self.answers.pop(0)
        headers = email.message.Message()
        for cookie in cookies or []:
            headers["Set-Cookie"] = cookie
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", headers, io.BytesIO(body))
        response = io.BytesIO(body)
        response.status = status
        response.headers = headers
        return contextlib.closing(response)


def ok(body=b"Ok.", cookies=None, status=200):
    return (status, body, cookies)


def form(request):
    return urllib.parse.parse_qs(request.data.decode())


class BencodeTests(unittest.TestCase):
    def test_the_info_hash_is_of_the_files_own_bytes(self):
        self.assertEqual(torrent_client.info_hash(TORRENT), HASH)
        self.assertTrue(torrent_client.looks_like_torrent(TORRENT))
        self.assertEqual(torrent_client.bdecode(TORRENT)[b"info"][b"length"], 1234)

    def test_a_page_or_a_truncated_file_is_not_a_torrent(self):
        for junk in (b"<!DOCTYPE html><html>", b"", b"d4:infoi1ee", TORRENT[:-40], b"d8:announce3:abce"):
            with self.subTest(junk=junk[:20]):
                self.assertFalse(torrent_client.looks_like_torrent(junk))
        with self.assertRaisesRegex(ValueError, "not a torrent file"):
            torrent_client.info_hash(b"<html>")
        with self.assertRaisesRegex(ValueError, "not a torrent file"):
            torrent_client.bdecode(TORRENT + b"junk")

    def test_a_magnet_names_its_hash_in_hex_or_base32(self):
        hexed = "0123456789ABCDEF0123456789abcdef01234567"
        self.assertEqual(torrent_client.magnet_hash(f"magnet:?xt=urn:btih:{hexed}&dn=Blue+Lock"), hexed.lower())
        base32 = base64.b32encode(bytes.fromhex(hexed)).decode()
        self.assertEqual(torrent_client.magnet_hash(f"magnet:?dn=x&xt=urn:btih:{base32.lower()}"), hexed.lower())
        self.assertIsNone(torrent_client.magnet_hash("magnet:?xt=urn:btmh:1220abcdef"), "a v2-only link names no v1 hash")
        self.assertIsNone(torrent_client.magnet_hash("https://example.test/file.torrent"))
        self.assertIsNone(torrent_client.magnet_hash("magnet:?xt=urn:btih:nothex"))

    def test_versions_compare_as_numbers(self):
        self.assertEqual(torrent_client.parse_version("2.8.19"), (2, 8, 19))
        self.assertEqual(torrent_client.parse_version("v5.0.2"), (5, 0, 2))
        self.assertEqual(torrent_client.parse_version(""), (0,))
        self.assertGreater(torrent_client.parse_version("2.11.2"), torrent_client.SELECTION_WEBAPI)
        self.assertLess(torrent_client.parse_version("2.8.5"), torrent_client.SELECTION_WEBAPI)


class StateTests(unittest.TestCase):
    def test_only_finished_data_is_complete(self):
        for state in ("uploading", "stalledUP", "stoppedUP", "pausedUP", "queuedUP", "forcedUP"):
            self.assertEqual(torrent_client.state_class(state), "complete", state)
        for state in ("downloading", "metaDL", "forcedMetaDL", "stalledDL", "stoppedDL", "pausedDL", "queuedDL",
                      "checkingDL", "forcedDL", "checkingResumeData", "allocating", "moving", "checkingUP",
                      "unknown", "", "aStateFromTheFuture"):
            self.assertEqual(torrent_client.state_class(state), "downloading", state)
        for state in ("error", "missingFiles"):
            self.assertEqual(torrent_client.state_class(state), "error", state)

    def test_stopped_after_seeding_is_the_clients_own_stop(self):
        self.assertTrue(torrent_client.stopped_after_seeding("stoppedUP"))
        self.assertTrue(torrent_client.stopped_after_seeding("pausedUP"))
        for state in ("uploading", "stalledUP", "stoppedDL", "pausedDL", "error"):
            self.assertFalse(torrent_client.stopped_after_seeding(state), state)
        self.assertTrue(torrent_client.stopped_before_finishing("stoppedDL"))
        self.assertFalse(torrent_client.stopped_before_finishing("stoppedUP"))

    def test_tags_come_back_joined_with_a_comma_and_a_space(self):
        self.assertEqual(torrent_client.tags_of({"tags": "flipparr, job-7, flipparr-selected"}),
                         {"flipparr", "job-7", "flipparr-selected"})
        self.assertEqual(torrent_client.tags_of({"tags": ""}), set())


class SessionTests(unittest.TestCase):
    def test_a_request_without_a_session_signs_in_once_and_asks_again(self):
        fake = FakeClient([
            (403, b"Forbidden", None),
            ok(cookies=["SID=abc123; HttpOnly; SameSite=Strict; path=/"]),
            ok(b"v5.0.2"),
        ])
        client = QBittorrent("http://qbt:8080/", "admin", "secret", opener=fake)
        self.assertEqual(client.version(), "v5.0.2")
        first, login, again = fake.requests
        self.assertEqual(login.full_url, "http://qbt:8080/api/v2/auth/login")
        self.assertEqual(form(login), {"username": ["admin"], "password": ["secret"]})
        self.assertEqual(login.get_header("Referer"), "http://qbt:8080", "the Referer must match the host")
        self.assertIsNone(first.get_header("Cookie"))
        self.assertEqual(again.get_header("Cookie"), "SID=abc123")

    def test_bad_credentials_are_said_as_such_in_either_dialect(self):
        for answer in ((200, b"Fails.", None), (401, b"", None)):
            with self.subTest(answer=answer[0]):
                client = QBittorrent("http://qbt:8080", "admin", "wrong", opener=FakeClient([(403, b"", None), answer]))
                with self.assertRaisesRegex(TorrentAuthError, "rejected this username and password"):
                    client.version()
        banned = QBittorrent("http://qbt:8080", "admin", "x", opener=FakeClient([(403, b"", None), (403, b"", None)]))
        with self.assertRaisesRegex(TorrentAuthError, "blocked this address"):
            banned.version()

    def test_a_refused_sign_in_is_not_asked_again_until_the_wait_is_over(self):
        # Gate 3 (2026-10-05): a wrong password asked every 15 seconds walked
        # into qBittorrent's ban on an address after five failed sign-ins.
        qbt = FakeClient([(403, b"", None), (200, b"Fails.", None)])
        client = QBittorrent("http://qbt:8080", "admin", "wrong", opener=qbt, refused_wait=900)
        for _ in range(3):
            with self.assertRaisesRegex(TorrentAuthError, "rejected this username and password"):
                client.version()
        self.assertEqual(len(qbt.requests), 2, "one try and one sign-in, then the refusal is repeated without asking")
        with patch("torrent_client.time.monotonic", return_value=time.monotonic() + 901):
            qbt.answers += [(403, b"", None), ok(cookies=["SID=abc; path=/"]), ok(b"v4.6.7")]
            self.assertEqual(client.version(), "v4.6.7", "asked again once the wait is over")
        # A person's Test (no wait) always asks.
        tester = QBittorrent("http://qbt:8080", "admin", "wrong",
                             opener=FakeClient([(403, b"", None), (200, b"Fails.", None), (403, b"", None), (200, b"Fails.", None)]))
        for _ in range(2):
            with self.assertRaises(TorrentAuthError):
                tester.version()

    def test_a_client_that_lets_its_network_in_needs_no_credentials(self):
        open_client = QBittorrent("http://qbt:8080", opener=FakeClient([ok(b"v4.6.7")]))
        self.assertEqual(open_client.version(), "v4.6.7")
        closed = QBittorrent("http://qbt:8080", opener=FakeClient([(403, b"Forbidden", None)]))
        with self.assertRaisesRegex(TorrentAuthError, "wants a username and password"):
            closed.version()

    def test_selection_needs_qbittorrent_4_5_5_or_later(self):
        self.assertTrue(QBittorrent("http://q", opener=FakeClient([ok(b"2.11.2")])).supports_selection())
        self.assertFalse(QBittorrent("http://q", opener=FakeClient([ok(b"2.8.5")])).supports_selection())
        cached = QBittorrent("http://q", opener=FakeClient([ok(b"2.9.3")]))
        self.assertTrue(cached.supports_selection())
        self.assertTrue(cached.supports_selection(), "asked once")


class TorrentCallTests(unittest.TestCase):
    def client(self, answers):
        fake = FakeClient(answers)
        client = QBittorrent("http://qbt:8080", "admin", "secret", opener=fake)
        client._cookie = "SID=abc"
        return client, fake

    def test_a_torrent_file_is_uploaded_in_a_folder_of_its_own_with_its_category_and_tags(self):
        client, fake = self.client([ok()])
        self.assertEqual(client.add_torrent(torrent=TORRENT, category="comics", tags=["flipparr", "job-7"], name="Blue Lock: v34?"), [])
        request = fake.requests[0]
        self.assertEqual(request.full_url, "http://qbt:8080/api/v2/torrents/add")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        body = request.data
        for expected in (b'name="category"\r\n\r\ncomics', b'name="tags"\r\n\r\nflipparr,job-7',
                         b'name="contentLayout"\r\n\r\nSubfolder',
                         b'name="stopped"\r\n\r\nfalse', b'name="paused"\r\n\r\nfalse',
                         b'name="torrents"; filename="Blue Lock_ v34_.torrent"', TORRENT):
            self.assertIn(expected, body)
        self.assertNotIn(b'name="urls"', body)
        self.assertNotIn(b"stopCondition", body, "started at once unless its files are to be chosen")

    def test_a_torrent_whose_files_are_to_be_chosen_waits_for_its_file_list(self):
        client, fake = self.client([ok(), ok()])
        client.add_torrent(torrent=TORRENT, category="comics", wait_for_files=True)
        body = fake.requests[0].data
        self.assertIn(b'name="stopCondition"\r\n\r\nMetadataReceived', body)
        self.assertIn(b'name="stopped"\r\n\r\ntrue', body, "a .torrent already has its file list")
        self.assertIn(b'name="paused"\r\n\r\ntrue', body)
        client.add_torrent(magnet=f"magnet:?xt=urn:btih:{HASH}", wait_for_files=True)
        body = fake.requests[1].data
        self.assertIn(b'name="stopCondition"\r\n\r\nMetadataReceived', body)
        self.assertIn(b'name="stopped"\r\n\r\nfalse', body, "a stopped magnet never fetches its file list")

    def test_a_magnet_is_sent_as_a_url_and_newer_clients_name_what_they_added(self):
        answer = json.dumps({"success_count": 1, "pending_count": 0, "failure_count": 0,
                             "added_torrent_ids": [HASH.upper()]}).encode()
        client, fake = self.client([ok(answer)])
        magnet = f"magnet:?xt=urn:btih:{HASH}"
        self.assertEqual(client.add_torrent(magnet=magnet, category="comics"), [HASH])
        self.assertIn(f'name="urls"\r\n\r\n{magnet}'.encode(), fake.requests[0].data)

    def test_a_refused_torrent_says_why(self):
        for answer, wording in (((200, b"Fails.", None), "did not accept"), ((415, b"", None), "not a valid torrent"),
                                ((409, b"", None), "may already have it"),
                                (ok(json.dumps({"success_count": 0, "failure_count": 1}).encode()), "did not accept")):
            with self.subTest(wording=wording):
                client, _fake = self.client([answer])
                with self.assertRaisesRegex(TorrentClientError, wording):
                    client.add_torrent(torrent=TORRENT)
        with self.assertRaises(ValueError):
            self.client([])[0].add_torrent()

    def test_torrents_are_listed_by_hash_tag_or_category(self):
        rows = [{"hash": HASH, "state": "uploading", "content_path": "/data/torrents/complete/comics/Blue Lock v34.cbz"}]
        client, fake = self.client([ok(json.dumps(rows).encode()), ok(b"[]")])
        self.assertEqual(client.info(hashes=[HASH, "ff" * 20]), rows)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(fake.requests[0].full_url).query),
                         {"hashes": [f"{HASH}|{'ff' * 20}"]})
        self.assertEqual(client.info(tag="flipparr", category="comics", filter="downloading"), [])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(fake.requests[1].full_url).query),
                         {"tag": ["flipparr"], "category": ["comics"], "filter": ["downloading"]})
        self.assertEqual(client.info(hashes=[]), [], "no hashes asks nothing")
        self.assertEqual(len(fake.requests), 2)

    def test_the_files_of_a_torrent_are_listed_and_chosen(self):
        files = [{"index": 0, "name": "Blue Lock v01-39/Blue Lock v33.cbz", "size": 400, "priority": 1, "progress": 0},
                 {"index": 1, "name": "Blue Lock v01-39/Blue Lock v34.cbz", "size": 410, "priority": 1, "progress": 0}]
        client, fake = self.client([ok(json.dumps(files).encode()), (404, b"", None), ok(b""), ok(b"")])
        self.assertEqual(client.files(HASH), files)
        self.assertEqual(client.files("00" * 20), [], "an unknown torrent has no files")
        client.set_file_priority(HASH, [0, 5], 0)
        self.assertEqual(form(fake.requests[2]), {"hash": [HASH], "id": ["0|5"], "priority": ["0"]})
        client.add_tags([HASH], ["flipparr-selected"])
        self.assertEqual(form(fake.requests[3]), {"hashes": [HASH], "tags": ["flipparr-selected"]})
        client.set_file_priority(HASH, [], 0)
        self.assertEqual(len(fake.requests), 4, "nothing to choose asks nothing")

    def test_starting_and_stopping_use_the_5x_names_and_fall_back_to_4x(self):
        client, fake = self.client([ok(b""), (404, b"", None), ok(b""), (404, b"", None), (404, b"", None)])
        client.start([HASH])
        self.assertTrue(fake.requests[0].full_url.endswith("/api/v2/torrents/start"))
        client.pause([HASH])
        self.assertTrue(fake.requests[1].full_url.endswith("/api/v2/torrents/stop"))
        self.assertTrue(fake.requests[2].full_url.endswith("/api/v2/torrents/pause"), "4.x has no stop")
        with self.assertRaisesRegex(TorrentClientError, "could not start"):
            client.start([HASH])

    def test_removing_names_its_hashes_and_whether_the_files_go(self):
        client, fake = self.client([ok(b"")])
        client.delete([HASH], delete_files=True)
        self.assertEqual(form(fake.requests[0]), {"hashes": [HASH], "deleteFiles": ["true"]})
        client.delete([], delete_files=True)
        self.assertEqual(len(fake.requests), 1)

    def test_a_category_is_created_once_and_an_existing_one_keeps_its_folder(self):
        existing = json.dumps({"comics": {"name": "comics", "savePath": "/data/torrents/complete/comics"}}).encode()
        client, fake = self.client([ok(existing)])
        self.assertEqual(client.ensure_category("comics", "/elsewhere/comics"), "/data/torrents/complete/comics")
        self.assertEqual(len(fake.requests), 1, "an existing category is left as it is")
        client, fake = self.client([ok(b"{}"), ok(b"")])
        self.assertEqual(client.ensure_category("comics", "/data/torrents/complete/comics"), "/data/torrents/complete/comics")
        self.assertEqual(form(fake.requests[1]), {"category": ["comics"], "savePath": ["/data/torrents/complete/comics"]})


if __name__ == "__main__":
    unittest.main()
