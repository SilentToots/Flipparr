"""Gate 3 drill: restarts, outages and bad downloads, against the real server.

Starts Flipparr from this source tree as a separate process, in a scratch
folder, with fake SABnzbd, Prowlarr and qBittorrent beside it, and puts it
through what a household's NAS does to it: a download client that drops the
line or refuses its key, a hard kill in the middle of a download and of an
import, a password-protected download, an indexer that goes away while
someone searches by hand, a torrent pack of which only the wanted issues may
be fetched, a torrent client that refuses the password. Each scenario says
what must hold and whether it did.

Nothing real is touched: no network, no library, no download client. Run it
in a throwaway container from the build tree (`--network none` works; every
service it talks to is its own):

    docker run --rm --network none --read-only --tmpfs /tmp:size=512m \\
      -e HOME=/tmp -v ~/flipparr-build:/src:ro -v "$OUT":/out -w /src \\
      --entrypoint python3 flipparr:candidate -B tools/fulfillment_drill.py --out /out

The torrent scenarios can be run against a real qBittorrent instead of the
fake, which is how the client was proven for the release (Gate 4,
docs/FULFILLMENT_PROOFS.md): `--qbittorrent http://host:8080` with
`--qbittorrent-user`/`--qbittorrent-password` (or none, for a client that lets
its own network in), its `comics` category saving into the folder given as
`--torrents` -- which must be the same folder, seen from here, as from the
client -- and a web seed the client can reach for the drill's pack given as
`--web-seed http://this-host:18080/seed/`. The drill serves the pack's files
from that address itself.

Writes fulfillment-drill.json to --out and exits non-zero when anything failed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.server
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

WORK = Path(tempfile.gettempdir())   # a fresh folder of its own, made in main()
FAKE_PORT = 18080
APP_PORT = 18787
SOURCE = Path(__file__).resolve().parent.parent
# The torrent client the torrent scenarios use: the fake under /qbt on
# FAKE_PORT, or a real one named on the command line (main()).
QBT: dict[str, Any] = {"url": f"http://127.0.0.1:{FAKE_PORT}/qbt", "username": "drill", "password": "drill",
                       "real": False, "torrents": None, "web_seed": ""}
ONLY_TORRENTS = False
PACK_TITLE = "Drill Torrent 001-008 (2026) (Digital)"
PACK_ISSUES = range(1, 9)


# ---- a torrent, made here ------------------------------------------------------

def bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, str):
        return bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(key) + bencode(value[key]) for key in sorted(value, key=lambda k: k.encode() if isinstance(k, str) else k)) + b"e"
    raise TypeError(type(value))


def posixpath_name(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def pack_files() -> list[tuple[str, bytes]]:
    """The pack's comics, one an issue, each a real little zip -- a fixed
    content so the torrent's hash is the same on every run."""
    files = []
    for number in PACK_ISSUES:
        buffer = Path(WORK / "tmp" / f"pack-{number}.cbz")
        buffer.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            info = zipfile.ZipInfo("001.jpg", date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, b"\xff\xd8\xff\xe0 drill torrent page " + str(number).encode())
            info = zipfile.ZipInfo("002.jpg", date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, b"\xff\xd8\xff\xe0 drill torrent page two " + str(number).encode())
        files.append((f"Drill Torrent (2026) #{number:03d}.cbz", buffer.read_bytes()))
    return files


def make_torrent(files: list[tuple[str, bytes]], web_seed: str) -> tuple[bytes, str]:
    """A multi-file torrent of the pack, in a folder named after it, with a
    web seed (BEP 19) and no tracker. Returns its bytes and info hash."""
    piece_length = 16384
    data = b"".join(content for _name, content in files)
    pieces = b"".join(hashlib.sha1(data[i:i + piece_length]).digest() for i in range(0, len(data), piece_length))  # noqa: S324 -- the protocol's hash
    info = {
        "name": PACK_TITLE, "piece length": piece_length, "pieces": pieces,
        "files": [{"length": len(content), "path": [name]} for name, content in files],
    }
    torrent: dict[str, Any] = {"info": info, "created by": "flipparr drill"}
    if web_seed:
        torrent["url-list"] = [web_seed]
    return bencode(torrent), hashlib.sha1(bencode(info)).hexdigest()  # noqa: S324


# ---- fake services -----------------------------------------------------------

class Fakes:
    """SABnzbd, Prowlarr and qBittorrent as Flipparr asks them, with a switch
    for each way they go wrong."""

    def __init__(self) -> None:
        self.sab_mode = "ok"            # ok | down (drops the line) | refuse_key
        self.prowlarr_mode = "ok"       # ok | down
        self.prowlarr_poison = ""       # a query Prowlarr answers 500 to, and no other
        self.queries: list[str] = []
        self.slots: dict[str, dict[str, Any]] = {}   # nzo -> {"phase", "storage"}
        self.asked: list[str] = []
        # The torrent pack: offered by Prowlarr only while `torrent_offer` is
        # on, fetched from Prowlarr's download link, and -- for a real client
        # -- its files served as the web seed under /seed/.
        self.torrent_offer = False
        self.pack: tuple[bytes, str] = (b"", "")
        self.pack_files: list[tuple[str, bytes]] = []
        # The fake qBittorrent: its torrents by hash, and whether it signs in.
        self.qbt_mode = "ok"            # ok | refuse (wrong password)
        self.torrents: dict[str, dict[str, Any]] = {}
        self.qbt_asked: list[str] = []

    # -- what the drill does to the fake client between the client's own moves

    def complete_torrent(self, info_hash: str) -> None:
        """The wanted files arrive (the ones left at priority 1), and the
        torrent goes on to seed."""
        torrent = self.torrents[info_hash]
        folder = Path(torrent["content_path"])
        for entry in torrent["files"]:
            if int(entry["priority"]) > 0:
                target = folder / entry["name"].split("/", 1)[1]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(dict(self.pack_files)[target.name])
                entry["progress"] = 1
        torrent["progress"] = 1
        torrent["state"] = "uploading"

    def seeding_done(self, info_hash: str) -> None:
        """The client reached its share limit and stopped the torrent."""
        self.torrents[info_hash]["state"] = "stoppedUP"

    def add_fake_torrent(self, data: bytes, *, category: str, tags: list[str], stopped: bool, auto_tmm: bool) -> str:
        sys.path.insert(0, str(SOURCE))
        import torrent_client  # noqa: PLC0415 -- the module under test reads the torrent for us

        decoded = torrent_client.bdecode(data)
        info = decoded[b"info"]
        info_hash = torrent_client.info_hash(data)
        name = info[b"name"].decode()
        # As the real client does: a torrent follows its category's folder only
        # under automatic management; in manual mode (qBittorrent's default)
        # it lands in the default save folder, category or not.
        save_path = str(WORK / "torrents" / category) if category and auto_tmm else str(WORK / "torrents")
        files = [{"index": index, "name": f"{name}/{b'/'.join(entry[b'path']).decode()}",
                  "size": int(entry[b"length"]), "priority": 1, "progress": 0}
                 for index, entry in enumerate(info.get(b"files") or [])]
        self.torrents[info_hash] = {
            "hash": info_hash, "name": name, "state": "stoppedDL" if stopped else "downloading",
            "save_path": save_path, "content_path": f"{save_path}/{name}", "category": category,
            "tags": list(tags), "added_on": int(time.time()), "progress": 0, "num_seeds": 12,
            "last_activity": int(time.time()), "files": files,
        }
        return info_hash

    def handler(fakes) -> type[http.server.BaseHTTPRequestHandler]:  # noqa: N805
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def _json(self, payload: Any) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _drop(self) -> None:
                # Closed without a word: what a client restarting, or a proxy
                # in front of one, does to a request.
                self.close_connection = True

            def do_GET(self) -> None:  # noqa: N802
                url = urllib.parse.urlparse(self.path)
                query = dict(urllib.parse.parse_qsl(url.query))
                fakes.asked.append(url.path)
                if url.path.startswith("/sab/api"):
                    if fakes.sab_mode == "down":
                        return self._drop()
                    if fakes.sab_mode == "refuse_key":
                        return self._json({"status": False, "error": "API Key Incorrect"})
                    mode = query.get("mode")
                    wanted = set(filter(None, str(query.get("nzo_ids") or "").split(",")))
                    if mode == "queue":
                        return self._json({"queue": {"paused": False, "slots": [
                            {"nzo_id": nzo, "status": "Downloading", "percentage": "40", "labels": [],
                             "mb": "50", "mbleft": "30", "timeleft": "0:01:00"}
                            for nzo, slot in fakes.slots.items()
                            if slot["phase"] == "downloading" and (not wanted or nzo in wanted)]}})
                    if mode == "history":
                        if query.get("name") == "delete":
                            return self._json({"status": True})
                        return self._json({"history": {"slots": [
                            {"nzo_id": nzo, "status": "Completed", "storage": slot["storage"], "fail_message": ""}
                            for nzo, slot in fakes.slots.items()
                            if slot["phase"] == "completed" and (not wanted or nzo in wanted)]}})
                    return self._json({"status": True})
                if url.path == "/prowlarr/1/download":
                    return self._bytes(fakes.pack[0], "application/x-bittorrent")
                if url.path.startswith("/prowlarr/"):
                    if fakes.prowlarr_mode == "down":
                        return self._drop()
                    asked_for = str(query.get("query") or "")
                    fakes.queries.append(asked_for)
                    if fakes.prowlarr_poison and asked_for == fakes.prowlarr_poison:
                        self.send_response(500)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                    if fakes.torrent_offer and asked_for.startswith("Drill Torrent"):
                        return self._json([{
                            "guid": "drill-pack", "title": PACK_TITLE, "protocol": "torrent",
                            "indexer": "Drill Indexer", "size": sum(len(content) for _n, content in fakes.pack_files),
                            "seeders": 12, "leechers": 1,
                            # Posted today, like the issues it holds: a fixed date
                            # would fall a season behind them and be refused.
                            "publishDate": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                            "infoHash": fakes.pack[1],
                            "downloadUrl": f"http://127.0.0.1:{FAKE_PORT}/prowlarr/1/download?link=pack&file=pack.torrent",
                        }])
                    return self._json([])
                if url.path.startswith("/seed/"):
                    # BEP 19: the client asks for <url-list>/<torrent name>/<file path>.
                    wanted = urllib.parse.unquote(url.path[len("/seed/"):]).split("/", 1)
                    content = dict(fakes.pack_files).get(wanted[1]) if len(wanted) == 2 and wanted[0] == PACK_TITLE else None
                    if content is None:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                    return self._bytes(content, "application/octet-stream", ranges=self.headers.get("Range"))
                if url.path.startswith("/qbt/api/v2/"):
                    return self._qbt_get(url.path[len("/qbt/api/v2/"):], query)
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                url = urllib.parse.urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if url.path.startswith("/qbt/api/v2/"):
                    return self._qbt_post(url.path[len("/qbt/api/v2/"):], body)
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _bytes(self, payload: bytes, content_type: str, *, ranges: str | None = None) -> None:
                status, extra = 200, {}
                if ranges:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", ranges.strip())
                    if match:
                        start = int(match.group(1) or 0)
                        end = int(match.group(2)) if match.group(2) else len(payload) - 1
                        extra["Content-Range"] = f"bytes {start}-{end}/{len(payload)}"
                        payload, status = payload[start:end + 1], 206
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Accept-Ranges", "bytes")
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def _text(self, text: str, status: int = 200, **headers: str) -> None:
                payload = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=UTF-8")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in headers.items():
                    self.send_header(key.replace("_", "-"), value)
                self.end_headers()
                self.wfile.write(payload)

            # -- the fake qBittorrent: the Web API as torrent_client.py speaks it

            def _signed_in(self) -> bool:
                return "SID=drill" in str(self.headers.get("Cookie") or "")

            def _qbt_get(self, route: str, query: dict[str, str]) -> None:
                fakes.qbt_asked.append(route)
                if not self._signed_in():
                    return self._text("Forbidden", 403)
                if route == "app/version":
                    return self._text("v5.1.0")
                if route == "app/webapiVersion":
                    return self._text("2.11.4")
                if route == "app/defaultSavePath":
                    return self._text(str(WORK / "torrents"))
                if route == "torrents/categories":
                    return self._json({"comics": {"name": "comics", "savePath": str(WORK / "torrents" / "comics")}})
                if route == "torrents/info":
                    hashes = set(filter(None, str(query.get("hashes") or "").split("|")))
                    found = [self._public(t) for t in fakes.torrents.values()
                             if (not hashes or t["hash"] in hashes)
                             and (not query.get("tag") or query["tag"] in t["tags"])
                             and (not query.get("category") or query["category"] == t["category"])]
                    return self._json(found)
                if route == "torrents/files":
                    torrent = fakes.torrents.get(str(query.get("hash") or ""))
                    if not torrent:
                        return self._text("Not Found", 404)
                    return self._json([dict(entry) for entry in torrent["files"]])
                return self._text("Not Found", 404)

            @staticmethod
            def _public(torrent: dict[str, Any]) -> dict[str, Any]:
                return {**{key: value for key, value in torrent.items() if key != "files"}, "tags": ", ".join(torrent["tags"])}

            def _qbt_post(self, route: str, body: bytes) -> None:
                fakes.qbt_asked.append(route)
                content_type = str(self.headers.get("Content-Type") or "")
                if route == "auth/login":
                    form = dict(urllib.parse.parse_qsl(body.decode()))
                    if fakes.qbt_mode == "refuse" or form.get("username") != QBT["username"] or form.get("password") != QBT["password"]:
                        return self._text("Fails.")
                    return self._text("Ok.", Set_Cookie="SID=drill; HttpOnly; path=/")
                if not self._signed_in():
                    return self._text("Forbidden", 403)
                if route == "torrents/add":
                    boundary = content_type.split("boundary=", 1)[1].encode()
                    fields: dict[str, str] = {}
                    torrent_bytes = b""
                    for part in body.split(b"--" + boundary)[1:-1]:
                        head, _sep, value = part.partition(b"\r\n\r\n")
                        name = re.search(rb'name="([^"]+)"', head)
                        if not name:
                            continue
                        if name.group(1) == b"torrents":
                            torrent_bytes = value[:-2]
                        else:
                            fields[name.group(1).decode()] = value[:-2].decode()
                    if not torrent_bytes:
                        return self._text("Fails.", 415)
                    stopped = fields.get("stopped") == "true" or fields.get("paused") == "true"
                    fakes.add_fake_torrent(torrent_bytes, category=fields.get("category", ""),
                                           tags=[t for t in fields.get("tags", "").split(",") if t], stopped=stopped,
                                           auto_tmm=fields.get("autoTMM") == "true")
                    return self._text("Ok.")
                form = dict(urllib.parse.parse_qsl(body.decode()))
                hashes = [h for h in str(form.get("hashes") or form.get("hash") or "").split("|") if h]
                if route == "torrents/createCategory":
                    return self._text("Ok.")
                if route == "torrents/filePrio":
                    torrent = fakes.torrents.get(str(form.get("hash") or ""))
                    if not torrent:
                        return self._text("Not Found", 404)
                    indexes = {int(i) for i in str(form.get("id") or "").split("|") if i}
                    for entry in torrent["files"]:
                        if int(entry["index"]) in indexes:
                            entry["priority"] = int(form.get("priority") or 0)
                    return self._text("Ok.")
                if route == "torrents/addTags":
                    for info_hash in hashes:
                        if info_hash in fakes.torrents:
                            fakes.torrents[info_hash]["tags"] += [t for t in str(form.get("tags") or "").split(",") if t]
                    return self._text("Ok.")
                if route in ("torrents/start", "torrents/resume"):
                    for info_hash in hashes:
                        if info_hash in fakes.torrents:
                            torrent = fakes.torrents[info_hash]
                            torrent["state"] = "uploading" if torrent["progress"] >= 1 else "downloading"
                    return self._text("Ok.")
                if route in ("torrents/stop", "torrents/pause"):
                    for info_hash in hashes:
                        if info_hash in fakes.torrents:
                            torrent = fakes.torrents[info_hash]
                            torrent["state"] = "stoppedUP" if torrent["progress"] >= 1 else "stoppedDL"
                    return self._text("Ok.")
                if route == "torrents/delete":
                    for info_hash in hashes:
                        torrent = fakes.torrents.pop(info_hash, None)
                        if torrent and form.get("deleteFiles") == "true":
                            shutil.rmtree(torrent["content_path"], ignore_errors=True)
                    return self._text("Ok.")
                return self._text("Not Found", 404)

        return Handler


def start_fakes(fakes: Fakes) -> http.server.ThreadingHTTPServer:
    # Loopback only, unless a real client has to reach the web seed.
    server = http.server.ThreadingHTTPServer(("0.0.0.0" if QBT["real"] else "127.0.0.1", FAKE_PORT), fakes.handler())  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---- the scratch install -----------------------------------------------------

def environment() -> dict[str, str]:
    config = WORK / "config"
    return {
        **{key: value for key, value in os.environ.items() if not key.startswith(("FLIPPARR_", "COMICARR_"))},
        "FLIPPARR_DATABASE": str(config / "flipparr.db"),
        "FLIPPARR_ACQUISITION_CONFIG": str(config / "acquisition-services.json"),
        "FLIPPARR_SETTINGS_CONFIG": str(config / "settings.json"),
        "FLIPPARR_AUTH_CONFIG": str(config / "auth.json"),
        "FLIPPARR_PROVIDER_CONFIG": str(config / "metadata-providers.json"),
        "FLIPPARR_LIBRARY_ROOT": str(WORK / "comics"),
        "FLIPPARR_SAB_COMPLETE_ROOT": str(WORK / "sab"),
        "FLIPPARR_TORRENT_COMPLETE_ROOT": str((QBT["torrents"] or WORK / "torrents") / "comics"),
        "FLIPPARR_TEMP_DIR": str(WORK / "tmp"),
        "FLIPPARR_IMPORT_POLL_SECONDS": "5",
        "FLIPPARR_RESEARCH_POLL_SECONDS": "3600",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def comic(path: Path, *, locked: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("001.jpg", b"\xff\xd8\xff\xe0 drill page")
        archive.writestr("002.jpg", b"\xff\xd8\xff\xe0 drill page two")
    if locked:
        data = bytearray(path.read_bytes())
        data[data.find(b"PK\x03\x04") + 6] |= 0x1
        data[data.find(b"PK\x01\x02") + 8] |= 0x1
        path.write_bytes(bytes(data))
    return path


def seed() -> dict[str, Any]:
    """A library holding Drill Comic #1, wanting #2-#8, each its own job."""
    for folder in ("config", "comics", "sab", "torrents/comics", "tmp"):
        (WORK / folder).mkdir(parents=True, exist_ok=True)
    if QBT["torrents"]:
        (Path(QBT["torrents"]) / "comics").mkdir(parents=True, exist_ok=True)
    (WORK / "config" / "acquisition-services.json").write_text(json.dumps({
        "sabnzbd": {"url": f"http://127.0.0.1:{FAKE_PORT}/sab", "apiKey": "drill", "enabled": True, "category": "comics"},
        "prowlarr": {"url": f"http://127.0.0.1:{FAKE_PORT}/prowlarr", "apiKey": "drill", "enabled": True},
        "qbittorrent": {"url": QBT["url"], "username": QBT["username"], "password": QBT["password"],
                        "enabled": True, "category": "comics"},
    }))
    os.environ.update(environment())
    sys.path.insert(0, str(SOURCE))
    import app  # noqa: PLC0415 -- after the environment points it here
    from catalog_store import CatalogStore  # noqa: PLC0415

    comic(WORK / "comics" / "Drill Press" / "Drill Comic (2026)" / "Drill Comic (2026) #001.cbz")
    store = CatalogStore(Path(os.environ["FLIPPARR_DATABASE"]))
    scan = store.begin_scan(str(WORK / "comics"), True)
    store.perform_scan(scan, app.scan_folder, app.inventory_file)
    series_id = int(store.catalog()["series"][0]["id"])
    released = dt.date.today() - dt.timedelta(days=60)
    store.apply_issue_list(series_id, "gcd", "990", "https://www.comics.org/series/990/", [
        {"number": str(number), "provider_id": str(9900 + number),
         "publication_date": str(released + dt.timedelta(days=7 * number)), "publication_year": released.year}
        for number in range(1, 9)
    ])
    request = store.create_acquisition_request("series", series_id, "issues")
    jobs = {str(job["issueNumber"]): int(job["id"]) for job in request["jobs"]}
    # A second run for the torrent scenarios: Drill Torrent #1 owned, #2-#8
    # wanted, its request made through the API when its scenario runs so the
    # automatic search -- the run's pack search first -- goes with it.
    comic(WORK / "comics" / "Drill Press" / "Drill Torrent (2026)" / "Drill Torrent (2026) #001.cbz")
    scan = store.begin_scan(str(WORK / "comics"), True)
    store.perform_scan(scan, app.scan_folder, app.inventory_file)
    torrent_series = next(int(item["id"]) for item in store.catalog()["series"] if "Torrent" in str(item.get("title")))
    store.apply_issue_list(torrent_series, "gcd", "991", "https://www.comics.org/series/991/", [
        {"number": str(number), "provider_id": str(9910 + number),
         "publication_date": str(released + dt.timedelta(days=7 * number)), "publication_year": released.year}
        for number in range(1, 9)
    ])
    return {"app": app, "store": store, "jobs": jobs, "torrent_series": torrent_series}


# ---- the server under test --------------------------------------------------

class Server:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.log = open(WORK / "server.log", "ab")  # noqa: SIM115

    def start(self) -> None:
        self.process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-B", str(SOURCE / "app.py"), "serve", "--host", "127.0.0.1", "--port", str(APP_PORT)],
            cwd=str(SOURCE), env=environment(), stdout=self.log, stderr=self.log,
        )
        wait_for(lambda: get("/healthz") is not None, 60, "the server to answer")

    def kill(self) -> None:
        # SIGKILL: what a container stop that runs out of grace, an OOM kill
        # or a power cut does. Nothing gets to tidy up.
        if self.process:
            self.process.send_signal(signal.SIGKILL)
            self.process.wait(10)
            self.process = None

    def stop(self) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(15)
            except subprocess.TimeoutExpired:
                self.kill()
            self.process = None


def get(path: str) -> Any:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}{path}", timeout=5) as response:  # noqa: S310
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def post(path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(  # noqa: S310
        f"http://127.0.0.1:{APP_PORT}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{APP_PORT}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def wait_for(check: Callable[[], bool], seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.5)
    raise TimeoutError(f"waited {seconds:.0f}s for {what}")


def settle(check: Callable[[], bool], seconds: float) -> bool:
    """Whether `check` came true within `seconds`; never raises."""
    try:
        wait_for(check, seconds, "")
        return True
    except TimeoutError:
        return False


def row(sql: str, *args: Any) -> dict[str, Any] | None:
    with sqlite3.connect(f"file:{os.environ['FLIPPARR_DATABASE']}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        found = connection.execute(sql, args).fetchone()
        return dict(found) if found else None


def download(job_id: int) -> dict[str, Any]:
    return row("SELECT * FROM acquisition_downloads WHERE job_id=?", job_id) or {}


def job(job_id: int) -> dict[str, Any]:
    return row("SELECT * FROM acquisition_jobs WHERE id=?", job_id) or {}


def silent_services() -> list[str]:
    status = get("/api/v1/system/status") or {}
    return [entry.get("service") for entry in status.get("services") or []]


def grabbed(seeded: dict[str, Any], fakes: Fakes, issue: str, phase: str, *, aged: bool = False) -> int:
    """Issue `issue` sent to the fake SABnzbd, as a grab would leave it."""
    store, job_id = seeded["store"], seeded["jobs"][issue]
    nzo = f"drill-nzo-{issue}"
    storage = WORK / "sab" / f"Drill.Comic.{int(issue):03d}.2026"
    fakes.slots[nzo] = {"phase": phase, "storage": str(storage)}
    store.record_acquisition_download(job_id, nzo, f"Drill Comic {int(issue):03d} (2026) (Digital)", f"drill-release-{issue}")
    store.update_acquisition_job(job_id, "grabbed", "Sent to SABnzbd")
    if aged:
        # Older than the grace a just-sent download gets, so a SABnzbd that
        # answers "nothing" would make it look forgotten.
        with sqlite3.connect(os.environ["FLIPPARR_DATABASE"]) as connection:
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
            connection.execute("UPDATE acquisition_downloads SET created_at=?, updated_at=? WHERE job_id=?", (old, old, job_id))
    return job_id


def finish(fakes: Fakes, issue: str, *, locked: bool = False) -> None:
    storage = Path(fakes.slots[f"drill-nzo-{issue}"]["storage"])
    comic(storage / f"Drill Comic {int(issue):03d} (2026) (Digital).cbz", locked=locked)
    fakes.slots[f"drill-nzo-{issue}"]["phase"] = "completed"


def imported(job_id: int) -> bool:
    return download(job_id).get("status") == "imported" and job(job_id).get("status") == "fulfilled"


# ---- scenarios -------------------------------------------------------------

def run(seeded: dict[str, Any], fakes: Fakes, server: Server) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def scenario(name: str, promise: str) -> Callable[[Callable[[], list[str]]], None]:
        def runner(body: Callable[[], list[str]]) -> None:
            if ONLY_TORRENTS and not name.startswith("torrent"):
                return
            started = time.monotonic()
            try:
                broken = body()
            except Exception as exc:  # noqa: BLE001 -- a scenario that cannot finish has failed
                broken = [f"{type(exc).__name__}: {exc}"]
            results.append({"scenario": name, "promise": promise, "passed": not broken,
                            "broken": broken, "seconds": round(time.monotonic() - started, 1)})
            print(f"{'PASS' if not broken else 'FAIL'}  {name}" + "".join(f"\n      {item}" for item in broken), flush=True)
        return runner

    # The import worker asks every 5 seconds here; an outage is held for four
    # of its passes before anything is judged.
    outage = 22

    @scenario("baseline", "a finished SABnzbd download is imported and its issue fulfilled")
    def _() -> list[str]:
        job_id = grabbed(seeded, fakes, "2", "downloading")
        finish(fakes, "2")
        return [] if settle(lambda: imported(job_id), 60) else [f"#2 is {download(job_id).get('status')}, not imported"]

    @scenario("sabnzbd drops the line", "downloads wait, nothing fails, the System page names SABnzbd; it carries on when SABnzbd is back")
    def _() -> list[str]:
        job_id = grabbed(seeded, fakes, "3", "downloading")
        fakes.sab_mode = "down"
        time.sleep(outage)
        broken = []
        if download(job_id).get("status") == "failed" or job(job_id).get("status") == "failed":
            broken.append(f"#3 failed during the outage: {download(job_id).get('error')}")
        if "sabnzbd" not in silent_services():
            broken.append("the System page does not name SABnzbd")
        fakes.sab_mode = "ok"
        finish(fakes, "3")
        if not settle(lambda: imported(job_id), 60):
            broken.append(f"#3 did not import once SABnzbd answered: {download(job_id).get('status')}")
        if "sabnzbd" in silent_services():
            broken.append("SABnzbd still named as silent after it answered")
        return broken

    @scenario("sabnzbd refuses the API key", "a 200 carrying an error is not every download lost; nothing falls back")
    def _() -> list[str]:
        job_id = grabbed(seeded, fakes, "4", "absent", aged=True)
        fakes.sab_mode = "refuse_key"
        time.sleep(outage)
        broken = []
        if download(job_id).get("status") == "failed":
            broken.append(f"#4 given up: {download(job_id).get('error')}")
        if row("SELECT 1 FROM acquisition_release_failures WHERE job_id=?", job_id):
            broken.append("#4's release was set aside over a refused key")
        if "sabnzbd" not in silent_services():
            broken.append("the System page does not name SABnzbd")
        fakes.sab_mode = "ok"
        finish(fakes, "4")
        if not settle(lambda: imported(job_id), 60):
            broken.append(f"#4 did not import once the key was accepted: {download(job_id).get('status')}")
        return broken

    @scenario("hard kill mid-download and mid-search", "after a SIGKILL the download carries on, a cut-off search is due again, and a half-written copy is cleared, never catalogued")
    def _() -> list[str]:
        store, app = seeded["store"], seeded["app"]
        job_id = grabbed(seeded, fakes, "5", "downloading")
        searching = seeded["jobs"]["6"]
        store.update_acquisition_job(searching, "searching", "Searching Prowlarr")
        server.kill()
        # What an import killed between its copy and its rename leaves.
        destination = app._issue_destination(Path("Drill Comic 005.cbz"), store.get_acquisition_job_context(job_id),
                                              WORK / "comics")
        partial = destination.with_name(f".{destination.stem}.flipparr-1-99999.partial.cbz")
        comic(partial)
        os.utime(partial, (time.time() - 3600, time.time() - 3600))
        finish(fakes, "5")
        server.start()
        broken = []
        if not settle(lambda: imported(job_id), 60):
            broken.append(f"#5 did not import after the restart: download {download(job_id).get('status')}, "
                          f"issue {job(job_id).get('status')}")
        if partial.exists():
            broken.append("the half-written copy is still there")
        if row("SELECT 1 FROM files WHERE path LIKE ?", "%.partial%"):
            broken.append("the half-written copy was catalogued")
        if job(searching).get("status") != "queued":
            broken.append(f"the cut-off search is {job(searching).get('status')}, not queued")
        return broken

    @scenario("password-protected download", "refused, its release barred, nothing put in the library")
    def _() -> list[str]:
        job_id = grabbed(seeded, fakes, "7", "downloading")
        finish(fakes, "7", locked=True)
        broken = []
        if not settle(lambda: download(job_id).get("status") in {"failed", "imported"}, 60):
            broken.append(f"#7 was neither refused nor imported: {download(job_id).get('status')}")
        failure = row("SELECT kind, error FROM acquisition_release_failures WHERE job_id=?", job_id) or {}
        if failure.get("kind") != "format":
            broken.append(f"release recorded as {failure.get('kind')!r}, not barred as 'format'")
        if "password-protected" not in str(download(job_id).get("error") or ""):
            broken.append(f"the row does not say why: {download(job_id).get('error')}")
        if any(path.name.startswith("Drill Comic (2026) #007") for path in (WORK / "comics").rglob("*.cbz")):
            broken.append("the locked file reached the library")
        return broken

    @scenario("one issue prowlarr cannot search", "its failure is counted as its own, and the issues behind it are still searched")
    def _() -> list[str]:
        poisoned, behind = seeded["jobs"]["6"], seeded["jobs"]["8"]
        if not settle(lambda: job(poisoned).get("status") == "queued" and job(behind).get("status") == "queued", 30):
            return [f"#6 is {job(poisoned).get('status')} and #8 is {job(behind).get('status')} before the search"]
        before = int(job(poisoned).get("attempt_count") or 0)
        fakes.prowlarr_poison = "Drill Comic 006"
        fakes.queries.clear()
        status, body = post("/api/v1/requests/search-missing", {"confirmed": True})
        searched_behind = settle(lambda: any(query.startswith("Drill Comic 008") for query in fakes.queries), 60)
        counted = settle(lambda: int(job(poisoned).get("attempt_count") or 0) == before + 1, 20)
        fakes.prowlarr_poison = ""
        broken = []
        if status >= 400:
            broken.append(f"the search for missing issues answered {status}: {body}")
        if "Drill Comic 006" not in fakes.queries:
            broken.append("#6 was never asked about")
        if not searched_behind:
            broken.append("#8, behind it, was not searched")
        if not counted:
            broken.append(f"#6's failed search did not count: {before} -> {job(poisoned).get('attempt_count')}")
        if "prowlarr" in silent_services():
            broken.append("Prowlarr is named as silent though it answered for the others")
        return broken

    @scenario("prowlarr down while searching by hand", "the person is told, the issue stays queued, and the try does not count")
    def _() -> list[str]:
        job_id = seeded["jobs"]["8"]
        before = int(job(job_id).get("attempt_count") or 0)
        fakes.prowlarr_mode = "down"
        status, body = post(f"/api/v1/acquisition-jobs/{job_id}/search", {})
        fakes.prowlarr_mode = "ok"
        broken = []
        if status != 502 or "Prowlarr" not in str(body.get("error")):
            broken.append(f"answered {status}: {body}")
        if job(job_id).get("status") != "queued":
            broken.append(f"the issue is {job(job_id).get('status')}, not queued")
        if int(job(job_id).get("attempt_count") or 0) != before:
            broken.append(f"the try counted: {before} -> {job(job_id).get('attempt_count')}")
        if "prowlarr" not in silent_services():
            broken.append("Prowlarr is not named as silent")
        return broken

    # ---- torrents: a pack through qBittorrent, fake or real --------------------

    def torrent_rows() -> list[dict[str, Any]]:
        with sqlite3.connect(f"file:{os.environ['FLIPPARR_DATABASE']}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(found) for found in connection.execute(
                "SELECT d.*, i.issue_number FROM acquisition_downloads d JOIN acquisition_jobs j ON j.id=d.job_id "
                "JOIN issues i ON i.id=j.issue_id WHERE d.source='qbittorrent' ORDER BY CAST(i.issue_number AS INTEGER)")]

    def client_torrent() -> dict[str, Any] | None:
        """The pack as the client reports it, fake or real."""
        if not QBT["real"]:
            return fakes.torrents.get(fakes.pack[1])
        sys.path.insert(0, str(SOURCE))
        import torrent_client  # noqa: PLC0415

        client = torrent_client.QBittorrent(QBT["url"], QBT["username"], QBT["password"])
        found = client.info(hashes=[fakes.pack[1]])
        if not found:
            return None
        torrent = found[0]
        torrent["files"] = client.files(fakes.pack[1])
        return torrent

    torrent_state: dict[str, Any] = {}

    def torrent_client_state(torrent: dict[str, Any]) -> str:
        state = str(torrent.get("state") or "").casefold()
        if state in ("uploading", "stalledup", "stoppedup", "pausedup", "queuedup", "forcedup"):
            return "complete"
        if state in ("stoppeddl", "pauseddl"):
            return "stopped"
        return "downloading"


    @scenario("torrent pack: only the wanted issues are fetched",
              "a run mostly missing takes the pack through qBittorrent, chooses its issues' files and no other, and the rest of the run rides along")
    def _() -> list[str]:
        fakes.torrent_offer = True
        status, body = post("/api/v1/requests", {"scopeType": "series", "scopeId": seeded["torrent_series"],
                                                 "acquisitionPreference": "issues"})
        if status != 201:
            return [f"the request answered {status}: {body}"]
        torrent_state["request"] = body
        torrent_state["jobs"] = {str(job["issueNumber"]): int(job["id"]) for job in body["jobs"]}
        # The pack search, the grab, and the import worker's next pass choosing
        # the files: priorities set, then the "chosen" tag, then started -- a
        # real client is read between those, so the last of them is waited for.
        def tags_of(torrent: dict[str, Any] | None) -> set[str]:
            raw = (torrent or {}).get("tags")
            return {tag.strip() for tag in str(raw or "").split(",") if tag.strip()} if isinstance(raw, str) else set(raw or [])

        chosen = settle(lambda: "flipparr-selected" in tags_of(client_torrent())
                        and torrent_client_state(client_torrent() or {}) != "stopped", 90)
        broken = []
        torrent = client_torrent()
        if torrent is None:
            return ["the pack never reached qBittorrent: " + json.dumps(torrent_rows()[:2])]
        if not all(int(entry.get("priority") or 0) == (0 if entry["name"].endswith("#001.cbz") else 1)
                   for entry in torrent.get("files") or []) or not torrent.get("files"):
            broken.append("the files were not chosen as wanted: " + json.dumps(
                [(entry["name"][-12:], entry.get("priority")) for entry in torrent.get("files") or []]))
        tags = tags_of(torrent)
        if "flipparr" not in tags or "flipparr-selected" not in tags:
            broken.append(f"the torrent is not tagged as Flipparr's and chosen: {sorted(tags)}")
        if not chosen:
            broken.append(f"the torrent was not started after choosing: {torrent.get('state')}")
        saved_in = posixpath_name(str(torrent.get("save_path") or ""))
        if saved_in != "comics":
            broken.append(f"qBittorrent saved the pack in “{saved_in}”, not the comics category's folder")
        # The riders' rows are written one by one after the torrent is
        # started, so they may be a moment behind the tag (CI, 2026-10-06).
        settle(lambda: len(torrent_rows()) >= 7, 20)
        rows = torrent_rows()
        if sorted(str(r["issue_number"]) for r in rows) != [str(n) for n in range(2, 9)]:
            broken.append("not every wanted issue has a download row on the pack: " + str([r["issue_number"] for r in rows]))
        if not all(str(r.get("sab_nzo_id") or "").startswith(f"torrent:{fakes.pack[1]}:") for r in rows):
            broken.append("a row names another torrent: " + str([r.get("sab_nzo_id") for r in rows][:3]))
        return broken

    @scenario("torrent: killed while it downloads",
              "after a hard kill the same torrent is kept -- not added again -- and its issues are still coming")
    def _() -> list[str]:
        if not torrent_state.get("jobs"):
            return ["no pack in flight"]
        before = torrent_rows()
        server.kill()
        time.sleep(2)
        server.start()
        time.sleep(outage)
        broken = []
        if QBT["real"]:
            sys.path.insert(0, str(SOURCE))
            import torrent_client  # noqa: PLC0415

            tagged = torrent_client.QBittorrent(QBT["url"], QBT["username"], QBT["password"]).info(tag="flipparr")
        else:
            tagged = [t for t in fakes.torrents.values() if "flipparr" in t["tags"]]
        if len(tagged) != 1:
            broken.append(f"{len(tagged)} torrents carry Flipparr's tag; one pack was taken")
        after = torrent_rows()
        if [r["id"] for r in after] != [r["id"] for r in before]:
            broken.append("the download rows changed across the restart")
        if any(r["status"] not in ("downloading", "queued", "completed", "imported") for r in after):
            broken.append("a row fell out of the download: " + str([(r["issue_number"], r["status"], r.get("error")) for r in after]))
        return broken

    @scenario("torrent: the pack lands and every issue is imported",
              "when the chosen files are complete, each wanted issue is imported from the torrent's folder and fulfilled, and nothing is deleted while it seeds")
    def _() -> list[str]:
        jobs = torrent_state.get("jobs") or {}
        if not jobs:
            return ["no pack in flight"]
        if not QBT["real"]:
            fakes.complete_torrent(fakes.pack[1])
        # A real client fetches from the web seed on its own; either way the
        # import worker sees the files on its next passes.
        done = settle(lambda: all(imported(jobs[str(n)]) for n in range(2, 9)), 240 if QBT["real"] else 60)
        broken = []
        if not done:
            broken.append("not every issue was imported: " + str([(n, job(jobs[str(n)]).get("status"), download(jobs[str(n)]).get("status"), download(jobs[str(n)]).get("error")) for n in range(2, 9)]))
        library = sorted(path.name for path in (WORK / "comics").rglob("Drill Torrent*#00[2-8].cbz"))
        if len(library) != 7:
            broken.append(f"{len(library)} of 7 files reached the library: {library}")
        torrent = client_torrent()
        if torrent is None:
            broken.append("the torrent was removed from qBittorrent while it should seed")
        elif torrent_client_state(torrent) != "complete":
            broken.append(f"the torrent is {torrent.get('state')}, not seeding")
        if any(name.endswith("#001.cbz") for name in library):
            broken.append("the owned issue was imported again")
        return broken

    @scenario("torrent: seeding done, the torrent is released",
              "once the client has stopped the torrent at its share limit, Flipparr removes it and its files; while it still seeds, it stays")
    def _() -> list[str]:
        if not torrent_state.get("jobs"):
            return ["no pack in flight"]
        info_hash = fakes.pack[1]
        # The sweep skips a torrent added less than ten minutes ago, so the
        # client's clock is moved back rather than the drill waiting.
        with sqlite3.connect(os.environ["FLIPPARR_DATABASE"]) as connection:
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
            connection.execute("UPDATE acquisition_downloads SET created_at=?, updated_at=? WHERE sab_nzo_id LIKE ?",
                               (old, old, f"torrent:{info_hash}:%"))
        if QBT["real"]:
            sys.path.insert(0, str(SOURCE))
            import torrent_client  # noqa: PLC0415

            client = torrent_client.QBittorrent(QBT["url"], QBT["username"], QBT["password"])
            client.pause([info_hash])
            # The sweep leaves a torrent added in the last ten minutes alone
            # (its row may be a moment behind); a real client's clock cannot
            # be moved, so the drill waits that out.
            added = int((client_torrent() or {}).get("added_on") or time.time())
            time.sleep(max(0.0, added + 605 - time.time()))
        else:
            fakes.torrents[info_hash]["added_on"] -= 3600
            fakes.seeding_done(info_hash)
        # The sweep runs when the import worker starts and hourly after; a
        # restart is how an hour passes here.
        server.stop()
        server.start()
        released = settle(lambda: client_torrent() is None, 90)
        broken = []
        if not released:
            broken.append(f"the stopped torrent is still in qBittorrent: {(client_torrent() or {}).get('state')}")
        folder = Path(os.environ["FLIPPARR_TORRENT_COMPLETE_ROOT"]) / PACK_TITLE
        if folder.exists():
            broken.append("the torrent's files were left in the category folder")
        if sorted(path.name for path in (WORK / "comics").rglob("Drill Torrent*#00[2-8].cbz")) != [
                f"Drill Torrent (2026) #{n:03d}.cbz" for n in range(2, 9)]:
            broken.append("the library's copies went with the torrent")
        return broken

    @scenario("torrent: qBittorrent refuses the password",
              "a refused sign-in is asked once, named on the System page, and not hammered -- qBittorrent bans an address after a few failures")
    def _() -> list[str]:
        if QBT["real"]:
            return []  # not against a real client: its ban would outlive the drill
        fakes.torrent_offer = False
        fakes.qbt_mode = "refuse"
        fakes.qbt_asked.clear()
        # A fresh client is made when the saved credentials change; the import
        # worker's next pass asks with the held ones.
        status, body = post("/api/v1/acquisition-services/qbittorrent", {
            "url": QBT["url"], "username": "drill", "password": "wrong", "enabled": True, "category": "comics"})
        broken = []
        if status >= 400:
            broken.append(f"saving the credentials answered {status}: {body}")
        # Something for the worker to ask about: a torrent row.
        store, job_id = seeded["store"], seeded["jobs"]["8"]
        store.record_acquisition_download(job_id, f"torrent:{'ab' * 20}:{job_id}", "Drill Comic 008 torrent", "drill-t8", source="qbittorrent")
        store.update_acquisition_job(job_id, "grabbed", "Sent to qBittorrent")
        named = settle(lambda: "qbittorrent" in silent_services(), outage + 10)
        logins = fakes.qbt_asked.count("auth/login")
        time.sleep(12)
        fakes.qbt_mode = "ok"
        if not named:
            broken.append("qBittorrent is not named on the System page")
        if fakes.qbt_asked.count("auth/login") > logins:
            broken.append(f"the refused password was tried again: {fakes.qbt_asked.count('auth/login')} sign-ins")
        if logins > 2:
            broken.append(f"{logins} sign-ins before the refusal was held")
        if job(job_id).get("status") != "grabbed":
            broken.append(f"the issue fell to {job(job_id).get('status')} because the client refused the sign-in")
        return broken

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--qbittorrent", help="a real qBittorrent's Web UI address, instead of the fake")
    parser.add_argument("--qbittorrent-user", default="")
    parser.add_argument("--qbittorrent-password", default="")
    parser.add_argument("--torrents", type=Path, help="the folder the real client's comics category saves into, as seen from here")
    parser.add_argument("--web-seed", default="", help="this drill's /seed/ address as the real client reaches it")
    parser.add_argument("--only-torrents", action="store_true", help="run the torrent scenarios alone")
    args = parser.parse_args()
    global WORK, ONLY_TORRENTS
    WORK = Path(tempfile.mkdtemp(prefix="flipparr-drill-"))
    ONLY_TORRENTS = bool(args.only_torrents)
    if args.qbittorrent:
        if not args.torrents or not args.web_seed:
            parser.error("--qbittorrent needs --torrents and --web-seed")
        QBT.update({"url": args.qbittorrent.rstrip("/"), "username": args.qbittorrent_user,
                    "password": args.qbittorrent_password, "real": True, "torrents": args.torrents,
                    "web_seed": args.web_seed})
    fakes = Fakes()
    start_fakes(fakes)
    fakes.pack_files = pack_files()
    fakes.pack = make_torrent(fakes.pack_files, QBT["web_seed"])
    seeded = seed()
    server = Server()
    server.start()
    try:
        results = run(seeded, fakes, server)
    finally:
        server.stop()
    report = {"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "build": os.environ.get("FLIPPARR_BUILD", ""), "scenarios": results,
              "passed": sum(1 for item in results if item["passed"]), "total": len(results)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "fulfillment-drill.json").write_text(json.dumps(report, indent=2) + "\n")
    tail = (WORK / "server.log").read_text(errors="replace").splitlines()[-400:]
    (args.out / "fulfillment-drill-server.log").write_text("\n".join(tail) + "\n")
    print(f"{report['passed']} of {report['total']} scenarios held")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
