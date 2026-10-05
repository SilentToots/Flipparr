"""Gate 4 drill: install, upgrade, rollback and restore, with real containers.

Runs the published or freshly built image the way a household would, from
the host's Docker, against a made-up library (no network, no real comics):

- a clean install into a config folder the container's user owns: it comes
  up, the catalog answers, a scan files the library;
- a config folder the container's user cannot write (what Docker makes when
  the folder did not exist yet): it says so in one line and stops, rather
  than coming up "healthy" with every page a 500;
- an upgrade from the previous image on the same config folder: the library
  is kept, the schema moves, a copy of the catalog as it was is left beside
  it (`--previous`; said as not tried when there is none);
- a rollback: the previous image on the config folder restored from the
  backup taken before the upgrade, as docs/OPERATING.md says to;
- a catalog from a newer build: this image refuses it before touching it.

    python3 -B tools/install_drill.py --image flipparr:ci \\
        [--previous ghcr.io/silenttoots/flipparr:0.1.0] --out ./install-drill

Writes install-drill.json and the containers' logs to --out; exits non-zero
when a promise did not hold. Needs `docker` and a free local port.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

PORT = int(os.environ.get("INSTALL_DRILL_PORT", "18797"))
NAME = "flipparr-install-drill"
# A one-pixel PNG, so a page is an image the scan can read a cover from.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc000000301"
    "0100c9fe92ef0000000049454e44ae426082")


def sh(*args: str, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), text=True, capture_output=True, check=check, **kwargs)  # noqa: S603


def library(root: Path, titles: int = 3, issues: int = 4) -> int:
    """`titles` runs of `issues` numbered comics each, as a scanner sees them."""
    count = 0
    for run in range(1, titles + 1):
        folder = root / "Drill Press" / f"Drill Run {run} (2020)"
        folder.mkdir(parents=True, exist_ok=True)
        for number in range(1, issues + 1):
            with zipfile.ZipFile(folder / f"Drill Run {run} (2020) #{number:03d}.cbz", "w") as archive:
                archive.writestr("001.png", PNG)
                archive.writestr("002.png", PNG)
            count += 1
    return count


class Container:
    def __init__(self, image: str, config: Path, comics: Path, user: str) -> None:
        self.image, self.config, self.comics, self.user = image, config, comics, user

    def start(self) -> None:
        self.stop()
        # On Docker's default network, for the published port; nothing in the
        # drill reaches out (no provider or download client is configured).
        sh("docker", "run", "-d", "--name", NAME, "--user", self.user, "-p", f"127.0.0.1:{PORT}:8787",
           "-v", f"{self.config}:/config", "-v", f"{self.comics}:/comics:ro", self.image)

    def stop(self) -> None:
        sh("docker", "rm", "-f", NAME, check=False)

    def logs(self) -> str:
        return sh("docker", "logs", NAME, check=False).stdout + sh("docker", "logs", NAME, check=False).stderr

    def state(self) -> dict[str, Any]:
        out = sh("docker", "inspect", "-f", "{{json .State}}", NAME, check=False).stdout.strip()
        return json.loads(out) if out else {}


def get(path: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def post(path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(  # noqa: S310
        f"http://127.0.0.1:{PORT}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def settle(check: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return True
        except Exception:  # noqa: BLE001 -- not yet
            pass
        time.sleep(1)
    return False


def serving() -> bool:
    status, body = get("/healthz")
    return status == 200 and isinstance(body, dict) and body.get("status") == "ok"


def catalog() -> dict[str, Any] | None:
    status, body = get("/api/v1/catalog")
    return body if status == 200 and isinstance(body, dict) else None


def scan(expected: int) -> bool:
    status, body = post("/api/v1/scans", {"folder": "/comics", "recursive": True, "metadataMode": "local"})
    if status != 202:
        return False
    return settle(lambda: (catalog() or {}).get("stats", {}).get("files") == expected, 180)


def filed(config: Path) -> tuple[int | None, list[str]]:
    """The catalog's schema and its files, read from the database itself."""
    with sqlite3.connect(f"file:{config / 'flipparr.db'}?mode=ro", uri=True) as connection:
        try:
            version = connection.execute("SELECT version FROM schema_info").fetchone()[0]
        except sqlite3.OperationalError:
            version = None
        paths = sorted(row[0] for row in connection.execute("SELECT path FROM files WHERE present=1"))
    return version, paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--image", required=True, help="the image under test")
    parser.add_argument("--previous", help="the previous release's image, for the upgrade and rollback")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    user = f"{os.getuid()}:{os.getgid()}"
    work = Path(tempfile.mkdtemp(prefix="flipparr-install-"))
    comics = work / "comics"
    expected = library(comics)
    results: list[dict[str, Any]] = []
    logs: dict[str, str] = {}

    def scenario(name: str, promise: str) -> Callable[[Callable[[], list[str]]], None]:
        def runner(body: Callable[[], list[str]]) -> None:
            started = time.monotonic()
            try:
                broken = body()
            except Exception as exc:  # noqa: BLE001
                broken = [f"{type(exc).__name__}: {exc}"]
            tried = broken != ["not tried"]
            results.append({"scenario": name, "promise": promise, "passed": tried and not broken,
                            "tried": tried, "broken": broken if tried else [], "seconds": round(time.monotonic() - started, 1)})
            mark = "PASS" if tried and not broken else "SKIP" if not tried else "FAIL"
            print(f"{mark}  {name}" + "".join(f"\n      {item}" for item in (broken if tried else [])), flush=True)
        return runner

    @scenario("clean install", "the image comes up on an empty config folder it may write, the catalog answers, and a scan files the library")
    def _() -> list[str]:
        config = work / "clean"
        config.mkdir()
        box = Container(args.image, config, comics, user)
        box.start()
        try:
            broken = []
            if not settle(serving, 60):
                return [f"never served: {box.logs()[-600:]}"]
            if (get("/healthz")[1] or {}).get("catalog") != "ok":
                broken.append(f"/healthz does not say the catalog is ok: {get('/healthz')[1]}")
            if catalog() is None:
                broken.append("the catalog does not answer")
            if not scan(expected):
                broken.append(f"a scan did not file the {expected} comics: {(catalog() or {}).get('stats')}")
            owner = f"{config.joinpath('flipparr.db').stat().st_uid}:{config.joinpath('flipparr.db').stat().st_gid}"
            if owner != user:
                broken.append(f"the catalog is owned by {owner}, not the container's user {user}")
            if box.state().get("Health", {}).get("Status") not in (None, "healthy", "starting"):
                broken.append(f"Docker says {box.state().get('Health', {}).get('Status')}")
            return broken
        finally:
            logs["clean install"] = box.logs()
            box.stop()

    @scenario("config folder the container cannot write", "it says so in one line and stops; Docker never calls it healthy")
    def _() -> list[str]:
        config = work / "locked"
        config.mkdir()
        config.chmod(0o555)
        box = Container(args.image, config, comics, user)
        box.start()
        try:
            exited = settle(lambda: box.state().get("Status") == "exited", 60)
            state = box.state()
            said = box.logs()
            broken = []
            if not exited:
                broken.append(f"still running after a minute: healthz {get('/healthz')}, state {state.get('Status')}")
            elif state.get("ExitCode") == 0:
                broken.append("exited 0, as if nothing were wrong")
            if "Flipparr cannot start" not in said or "not writable" not in said:
                broken.append(f"the log does not say why: {said[-400:]}")
            if "PUID:PGID" not in said:
                broken.append("the log does not say what to do")
            return broken
        finally:
            logs["locked config"] = box.logs()
            box.stop()
            config.chmod(0o755)

    upgraded = work / "upgrade"
    backup = work / "before-upgrade.tgz"

    @scenario("upgrade from the previous image", "the library and its catalog are kept, the schema moves forward, and the catalog as it was is left beside it")
    def _() -> list[str]:
        if not args.previous:
            return ["not tried"]
        upgraded.mkdir()
        old = Container(args.previous, upgraded, comics, user)
        old.start()
        try:
            if not settle(serving, 60):
                return [f"the previous image never served: {old.logs()[-600:]}"]
            if not scan(expected):
                return [f"the previous image did not file the library: {(catalog() or {}).get('stats')}"]
        finally:
            logs["previous image"] = old.logs()
            old.stop()
        before_version, before_files = filed(upgraded)
        # The backup the docs say to take first.
        with tarfile.open(backup, "w:gz") as archive:
            archive.add(upgraded, arcname=".")
        new = Container(args.image, upgraded, comics, user)
        new.start()
        try:
            broken = []
            if not settle(serving, 90):
                return [f"the upgraded install never served: {new.logs()[-600:]}"]
            if catalog() is None:
                broken.append("the catalog does not answer after the upgrade")
            after_version, after_files = filed(upgraded)
            if after_files != before_files:
                broken.append(f"the files changed: {len(before_files)} before, {len(after_files)} after")
            if after_version is None or (before_version is not None and after_version < before_version):
                broken.append(f"schema {before_version} -> {after_version}")
            if before_version is not None and after_version != before_version:
                copies = sorted(p.name for p in upgraded.glob("flipparr.db.pre-v*"))
                if f"flipparr.db.pre-v{after_version}" not in copies:
                    broken.append(f"no copy of the catalog as it was before schema {after_version}: {copies}")
            if (catalog() or {}).get("stats", {}).get("files") != expected:
                broken.append(f"the catalog shows {(catalog() or {}).get('stats', {}).get('files')} files, not {expected}")
            return broken
        finally:
            logs["upgrade"] = new.logs()
            new.stop()

    @scenario("rollback by restoring the backup", "the previous image serves the restored config folder with the same library")
    def _() -> list[str]:
        if not args.previous or not backup.exists():
            return ["not tried"]
        restored = work / "restored"
        restored.mkdir()
        with tarfile.open(backup) as archive:
            # The safe extraction filter, where this Python has it (3.12+).
            if hasattr(tarfile, "data_filter"):
                archive.extractall(restored, filter="data")
            else:
                archive.extractall(restored)  # noqa: S202 -- our own archive, made a moment ago
        old = Container(args.previous, restored, comics, user)
        old.start()
        try:
            if not settle(serving, 60):
                return [f"the previous image does not serve the restored folder: {old.logs()[-600:]}"]
            broken = []
            if (catalog() or {}).get("stats", {}).get("files") != expected:
                broken.append(f"the restored catalog shows {(catalog() or {}).get('stats', {}).get('files')} files, not {expected}")
            _version, files = filed(restored)
            if files != filed(upgraded)[1]:
                broken.append("the restored library is not the one that was backed up")
            return broken
        finally:
            logs["rollback"] = old.logs()
            old.stop()

    @scenario("catalog from a newer build", "this image refuses it before touching it, and says what to do")
    def _() -> list[str]:
        config = work / "newer"
        shutil.copytree(work / "clean", config)
        for stray in config.glob("flipparr.db-*"):
            stray.unlink()
        with sqlite3.connect(config / "flipparr.db") as connection:
            connection.execute("UPDATE schema_info SET version=version+100")
        before = (config / "flipparr.db").read_bytes()
        box = Container(args.image, config, comics, user)
        box.start()
        try:
            exited = settle(lambda: box.state().get("Status") == "exited", 60)
            said = box.logs()
            broken = []
            if not exited:
                broken.append(f"still running after a minute: healthz {get('/healthz')}")
            elif box.state().get("ExitCode") == 0:
                broken.append("exited 0, as if nothing were wrong")
            if "newer Flipparr" not in said:
                broken.append(f"the log does not say the catalog is from a newer build: {said[-400:]}")
            if (config / "flipparr.db").read_bytes() != before:
                broken.append("the catalog file was changed")
            return broken
        finally:
            logs["newer catalog"] = box.logs()
            box.stop()

    args.out.mkdir(parents=True, exist_ok=True)
    report = {"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "image": args.image, "previous": args.previous, "scenarios": results,
              "passed": sum(1 for item in results if item["passed"]),
              "tried": sum(1 for item in results if item["tried"]), "total": len(results)}
    (args.out / "install-drill.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, text in logs.items():
        (args.out / f"{name.replace(' ', '-')}.log").write_text(text)
    shutil.rmtree(work, ignore_errors=True)
    print(f"{report['passed']} of {report['tried']} tried scenarios held ({report['total'] - report['tried']} not tried)")
    return 0 if report["passed"] == report["tried"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
