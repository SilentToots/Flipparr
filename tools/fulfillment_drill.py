"""Gate 3 drill: restarts, outages and bad downloads, against the real server.

Starts Flipparr from this source tree as a separate process, in a scratch
folder, with fake SABnzbd and Prowlarr beside it, and puts it through what a
household's NAS does to it: a download client that drops the line or refuses
its key, a hard kill in the middle of a download and of an import, a
password-protected download, an indexer that goes away while someone
searches by hand. Each scenario says what must hold and whether it did.

Nothing real is touched: no network, no library, no download client. Run it
in a throwaway container from the build tree (`--network none` works; every
service it talks to is its own):

    docker run --rm --network none --read-only --tmpfs /tmp:size=512m \\
      -e HOME=/tmp -v ~/flipparr-build:/src:ro -v "$OUT":/out -w /src \\
      --entrypoint python3 flipparr:candidate -B tools/fulfillment_drill.py --out /out

Writes fulfillment-drill.json to --out and exits non-zero when anything failed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import os
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


# ---- fake services -----------------------------------------------------------

class Fakes:
    """SABnzbd and Prowlarr as Flipparr asks them, with a switch for each way
    they go wrong."""

    def __init__(self) -> None:
        self.sab_mode = "ok"            # ok | down (drops the line) | refuse_key
        self.prowlarr_mode = "ok"       # ok | down
        self.prowlarr_poison = ""       # a query Prowlarr answers 500 to, and no other
        self.queries: list[str] = []
        self.slots: dict[str, dict[str, Any]] = {}   # nzo -> {"phase", "storage"}
        self.asked: list[str] = []

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
                if url.path.startswith("/prowlarr/"):
                    if fakes.prowlarr_mode == "down":
                        return self._drop()
                    fakes.queries.append(str(query.get("query") or ""))
                    if fakes.prowlarr_poison and query.get("query") == fakes.prowlarr_poison:
                        self.send_response(500)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                    return self._json([])
                self.send_response(404)
                self.end_headers()

        return Handler


def start_fakes(fakes: Fakes) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), fakes.handler())
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
        "FLIPPARR_TORRENT_COMPLETE_ROOT": str(WORK / "torrents" / "comics"),
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
    (WORK / "config" / "acquisition-services.json").write_text(json.dumps({
        "sabnzbd": {"url": f"http://127.0.0.1:{FAKE_PORT}/sab", "apiKey": "drill", "enabled": True, "category": "comics"},
        "prowlarr": {"url": f"http://127.0.0.1:{FAKE_PORT}/prowlarr", "apiKey": "drill", "enabled": True},
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
    return {"app": app, "store": store, "jobs": jobs}


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

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    global WORK
    WORK = Path(tempfile.mkdtemp(prefix="flipparr-drill-"))
    fakes = Fakes()
    start_fakes(fakes)
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
