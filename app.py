#!/usr/bin/env python3
"""Read-only comic filename metadata proof of concept.

Run with: python3 app.py
Then open: http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import difflib
import email.message
import gzip
import hashlib
import hmac
import html
import http.cookies
import io
import ipaddress
import json
import mimetypes
import os
import posixpath
import re
import secrets
import functools
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

from catalog_store import CatalogStore, _issue_release_state, normalized_person
from catalog_core_v2.language import (
    LANGUAGE_NAMES,
    detect_language as detect_release_language,
    language_name,
)
from catalog_core_v2.provider_evidence import (
    COMIC_VINE_ISSUE_FIELDS,
    COMIC_VINE_VOLUME_FIELDS,
    metron_reprint_evidence,
    native_issue_evidence,
)


def _env(name: str, default: str = "") -> str:
    """Read FLIPPARR_<name>, falling back to the older COMICARR_ spelling.

    The app was renamed. A compose file written against the old prefix keeps
    working rather than silently dropping back to defaults, which would look
    like the configuration was ignored rather than renamed.
    """
    value = os.environ.get(f"FLIPPARR_{name}")
    if value is None:
        value = os.environ.get(f"COMICARR_{name}")
    return default if value is None else value


# Bumped by hand at release. COMICARR_BUILD is stamped by the image build (a
# commit sha), so a running container can be traced to the source that made it
# even between releases.
APP_VERSION = "0.1.0"
APP_BUILD = _env("BUILD", "").strip() or "source"

# Flipparr's own directory inside a library root, holding originals set aside by
# a replacement. ".sonicboom" is the pre-rename spelling: still read, and still
# excluded from scans, because a scan that stopped excluding it would re-import
# every original the user had already replaced.
MANAGED_LIBRARY_DIR = ".flipparr"
LEGACY_MANAGED_LIBRARY_DIRS = (".sonicboom",)
MANAGED_LIBRARY_DIRS = frozenset({MANAGED_LIBRARY_DIR, *LEGACY_MANAGED_LIBRARY_DIRS})

SUPPORTED_EXTENSIONS = {".cbz", ".cbr", ".pdf", ".epub", ".cb7", ".cbt"}
ARCHIVE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}
COVER_THUMBNAIL_MAX_DIMENSION = 600
COVER_THUMBNAIL_QUALITY = 82
COVER_SOURCE_MAX_BYTES = 50_000_000
COVER_CACHE_DIR = Path(tempfile.gettempdir()) / "comic-metadata-poc-cover-cache-v1"
_LEGACY_USER_COVER_DIR = Path(__file__).parent / ".data" / "user-covers"
_DEFAULT_CONFIG_DIR = Path(__file__).parent / ".data"


# --- Structured logging ------------------------------------------------------
#
# One JSON object per line on stdout, which is where a container collects logs.
# Every line carries the id of the request that produced it, and that same id
# goes back on the response as X-Request-Id, so a user reporting a failure can
# quote a string that finds the exact server-side record.
#
# Support-safe by construction: the logger writes the fields it is given and
# never a request body, a header, or a query string. Query strings are dropped
# rather than filtered -- an allowlist only holds until someone adds a
# parameter nobody thought to list.

_LOG_LOCK = threading.Lock()
_REQUEST_CONTEXT = threading.local()


def current_request_id() -> str | None:
    return getattr(_REQUEST_CONTEXT, "request_id", None)


def log_event(event: str, level: str = "info", **fields: Any) -> None:
    record: dict[str, Any] = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        "level": level,
        "event": event,
    }
    request_id = current_request_id()
    if request_id:
        record["request_id"] = request_id
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    line = json.dumps(record, ensure_ascii=False, default=str)
    # One write under a lock: interleaved partial lines are unparseable, and
    # every worker thread logs to the same stream.
    with _LOG_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def log_exception(event: str, exc: BaseException, level: str = "error", **fields: Any) -> None:
    """Record a failure with its stack, keeping the detail server-side.

    Callers answer the client separately and generically; the request id is
    what ties that answer back to this record.
    """
    log_event(
        event, level=level,
        error_type=type(exc).__name__,
        error=str(exc),
        traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip(),
        **fields,
    )


def _configured_path(variable: str, default: Path) -> Path:
    """Resolve a configured path when it is used, not when this module loads.

    Reading these at import time let whichever module imported `app` first fix
    them for the whole process. A caller that prepared its environment and then
    imported `app` got those settings only when it happened to be the first
    importer; arriving second, its `import app` hit sys.modules and its
    environment was silently ignored.
    """
    override = _env(variable)
    return Path(override) if override else default


def provider_config_path() -> Path:
    return _configured_path("PROVIDER_CONFIG", _DEFAULT_CONFIG_DIR / "metadata-providers.json"
    )


def acquisition_config_path() -> Path:
    return _configured_path("ACQUISITION_CONFIG", _DEFAULT_CONFIG_DIR / "acquisition-services.json"
    )


def settings_config_path() -> Path:
    return _configured_path("SETTINGS_CONFIG", _DEFAULT_CONFIG_DIR / "settings.json"
    )


def auth_config_path() -> Path:
    return _configured_path("AUTH_CONFIG", _DEFAULT_CONFIG_DIR / "auth.json")
# Addresses whose X-Forwarded-For may be believed. A reverse proxy makes every
# request look like it came from the proxy, so without this the "local
# addresses" bypass would treat the whole internet as local. Empty by default:
# an unconfigured deployment trusts nothing and falls back to the real peer.
def trusted_proxies() -> tuple[str, ...]:
    return tuple(
        entry.strip()
        for entry in _env("TRUSTED_PROXIES", "").split(",")
        if entry.strip()
    )


# Set once, so a misconfigured proxy is reported rather than repeated on every
# request for the life of the process.
_PROXY_HEADER_WARNED = False
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
GCD_API_BASE = "https://www.comics.org/api"
METRON_API_BASE = "https://metron.cloud/api"
COMIC_VINE_API_BASE = "https://comicvine.gamespot.com/api"
_PROVIDER_CONFIG_LOCK = threading.Lock()
_ACQUISITION_CONFIG_LOCK = threading.Lock()
_SETTINGS_CONFIG_LOCK = threading.Lock()

# Application-level preferences (not credentials). Persisted as a small JSON file
# in /config so it survives restarts.
_APP_SETTINGS_DEFAULTS: dict[str, Any] = {
    # Collected editions (trades, hardcovers, omnibuses) are an opt-in feature.
    # Files are always catalogued and grouped; this only controls whether the
    # collected-edition browsing and management surfaces are shown. Issue↔volume
    # fulfillment never happens regardless.
    "collectedEditionsEnabled": False,
    # First-run setup. Stored with the instance rather than in the browser so
    # finishing setup on a laptop does not leave a phone still being prompted.
    "setupCompleted": False,
    # The language wanted for acquisitions. A release that says it is another
    # language is not grabbed, and one that gets as far as import is refused
    # rather than filed silently under the issue it claims to be. Empty means
    # no preference, which accepts anything.
    "preferredLanguage": "en",
}
_APP_SETTINGS_BOOL_KEYS = frozenset({"collectedEditionsEnabled", "setupCompleted"})


def load_app_settings() -> dict[str, Any]:
    with _SETTINGS_CONFIG_LOCK:
        settings = dict(_APP_SETTINGS_DEFAULTS)
        try:
            saved = json.loads(settings_config_path().read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key not in _APP_SETTINGS_DEFAULTS:
                    continue
                # A malformed value falls back to the default rather than
                # reaching the rest of the app as the wrong type.
                if key in _APP_SETTINGS_BOOL_KEYS:
                    if isinstance(value, bool):
                        settings[key] = value
                elif isinstance(value, str):
                    settings[key] = value
        return settings


def save_app_settings(patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise ValueError("Provide at least one setting to change")
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in _APP_SETTINGS_DEFAULTS:
            raise ValueError(f"Unknown setting: {key}")
        if key in _APP_SETTINGS_BOOL_KEYS and not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        if key == "preferredLanguage":
            value = str(value or "").strip().casefold()
            if value and value not in LANGUAGE_NAMES:
                raise ValueError("Choose a language Flipparr can recognise in a release")
        clean[key] = value
    with _SETTINGS_CONFIG_LOCK:
        current = dict(_APP_SETTINGS_DEFAULTS)
        try:
            saved = json.loads(settings_config_path().read_text())
            if isinstance(saved, dict):
                current.update({k: v for k, v in saved.items() if k in _APP_SETTINGS_DEFAULTS})
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        current.update(clean)
        config_path = settings_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.parent / (config_path.name + ".tmp")
        tmp.write_text(json.dumps(current, indent=2, sort_keys=True))
        tmp.replace(config_path)
        return current


_AUTH_CONFIG_LOCK = threading.Lock()
_AUTH_METHODS = frozenset({"none", "forms"})
_SESSION_COOKIE = "flipparr_session"
_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_AUTH_EXEMPT_PATHS = frozenset({"/healthz", "/api/v1/auth/login", "/api/v1/auth/status"})
# Legacy server-rendered view; it prints catalog data, so it is not public.
_PROTECTED_PATHS = frozenset({"/results"})


def _auth_defaults() -> dict[str, Any]:
    return {
        "method": "none",
        # Sonarr/Radarr's "disabled for local addresses". Off by default: any
        # access through a tunnel, a reverse proxy, or a container bridge
        # arrives from a private address, so defaulting this on makes
        # "require sign-in" appear to do nothing on exactly the setups people
        # actually run. Opt in once you know the traffic really is local --
        # and set FLIPPARR_TRUSTED_PROXIES if a proxy is in front, or the
        # real client address cannot be seen at all.
        "localBypass": False,
        "username": "",
        "passwordHash": "",
        "sessionSecret": "",
    }


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, pp, salt_hex, digest_hex = str(encoded or "").split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(pp), dklen=len(bytes.fromhex(digest_hex)),
        )
    except (ValueError, TypeError):
        return False
    # Constant time: a timing difference here leaks the password one byte at a time.
    return hmac.compare_digest(derived, bytes.fromhex(digest_hex))


class AuthConfigUnreadable(RuntimeError):
    """The auth config exists but could not be read or parsed.

    Treated as a hard failure rather than falling back to defaults. The defaults
    disable authentication, so silently using them would turn a file-permission
    problem into a silently unprotected instance -- which is exactly what
    happens when the file is written as root and the service then runs as a
    non-root user.
    """


def load_auth_config() -> dict[str, Any]:
    defaults = _auth_defaults()
    config_path = auth_config_path()
    with _AUTH_CONFIG_LOCK:
        try:
            stored = json.loads(config_path.read_text())
        except FileNotFoundError:
            stored = {}
        except OSError as exc:
            raise AuthConfigUnreadable(
                f"{config_path} exists but cannot be read ({exc.strerror}). "
                "Check that it is owned by the user the service runs as."
            ) from exc
        except ValueError as exc:
            raise AuthConfigUnreadable(
                f"{config_path} is not valid JSON ({exc})."
            ) from exc
    if not isinstance(stored, dict):
        raise AuthConfigUnreadable(f"{config_path} must contain a JSON object.")
    merged = {**defaults, **{k: v for k, v in stored.items() if k in defaults}}
    if str(merged.get("method")) not in _AUTH_METHODS:
        merged["method"] = "none"
    return merged


def save_auth_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_auth_config()
    method = str(patch.get("method", current["method"]))
    if method not in _AUTH_METHODS:
        raise ValueError("Authentication method must be 'none' or 'forms'")
    username = str(patch.get("username", current["username"]) or "").strip()
    password = patch.get("password")
    # Credentials are accepted whatever the method is. They used to be handled
    # only while enabling forms auth, which made the intended order -- set a
    # username and password, then switch sign-in on -- impossible: the password
    # was silently discarded and enabling then failed for want of one.
    if password is not None:
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not username:
            raise ValueError("A username is required to set a password")
        current["passwordHash"] = hash_password(password)
    if method == "forms":
        if not username:
            raise ValueError("A username is required to enable authentication")
        if not current["passwordHash"]:
            raise ValueError("Set a username and password before requiring sign-in")
    current["method"] = method
    current["username"] = username
    if "localBypass" in patch:
        if not isinstance(patch["localBypass"], bool):
            raise ValueError("localBypass must be true or false")
        current["localBypass"] = patch["localBypass"]
    if not current["sessionSecret"]:
        current["sessionSecret"] = secrets.token_urlsafe(32)
    config_path = auth_config_path()
    with _AUTH_CONFIG_LOCK:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.parent / (config_path.name + ".tmp")
        tmp.write_text(json.dumps(current, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(config_path)
    return current


def public_auth_config() -> dict[str, Any]:
    """Never expose the password hash or the session secret."""
    config = load_auth_config()
    return {
        "method": config["method"],
        "localBypass": bool(config["localBypass"]),
        "username": config["username"],
        "configured": bool(config["passwordHash"]),
    }


def issue_session_token(config: dict[str, Any]) -> str:
    expires = int(time.time()) + _SESSION_TTL_SECONDS
    payload = f"{config['username']}:{expires}"
    signature = hmac.new(
        config["sessionSecret"].encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def session_token_valid(token: str, config: dict[str, Any]) -> bool:
    try:
        username, expires, signature = str(token or "").rsplit(":", 2)
    except ValueError:
        return False
    if not config.get("sessionSecret"):
        return False
    expected = hmac.new(
        config["sessionSecret"].encode("utf-8"),
        f"{username}:{expires}".encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    try:
        if int(expires) < int(time.time()):
            return False
    except ValueError:
        return False
    # Renaming the account or rotating the secret invalidates issued sessions.
    return hmac.compare_digest(username, str(config.get("username") or ""))


def is_local_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def collected_editions_enabled() -> bool:
    return bool(load_app_settings().get("collectedEditionsEnabled"))
_RELEASE_CANDIDATE_LOCK = threading.Lock()
# Jobs some pass is working on right now. The sweep and a new request's own
# pass can overlap, and each works from the list it read when it started.
_JOBS_IN_FLIGHT: set[int] = set()
_JOBS_IN_FLIGHT_LOCK = threading.Lock()
_RELEASE_CANDIDATES: dict[str, dict[str, Any]] = {}
_RELEASE_CANDIDATE_TTL_SECONDS = 30 * 60
MAX_AUTOMATIC_RELEASE_FAILURES = 3
NZB_MAX_BYTES = 25 * 1024 * 1024
_REMOTE_JSON_CACHE: dict[str, Any] = {}
_REMOTE_JSON_INFLIGHT: dict[str, threading.Event] = {}
_REMOTE_JSON_LOCK = threading.Lock()
_REMOTE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_PERSIST_REMOTE_CACHE = False
_PROVIDER_JSON_CACHE: dict[str, dict[str, Any]] = {}
_PROVIDER_JSON_INFLIGHT: dict[str, threading.Event] = {}
_PROVIDER_JSON_LOCK = threading.Lock()
_PROVIDER_REQUEST_LOCK = threading.Lock()
_PROVIDER_NEXT_REQUEST_AT: dict[str, float] = {}
_PROVIDER_RESPONSE_HEADERS = threading.local()
_PROVIDER_CACHE_STALE_SECONDS = 30 * 24 * 60 * 60
# A published comic's identity record does not get revised, so re-asking for it
# every week spends request budget to re-learn the same answer.
_PROVIDER_IDENTITY_TTL_SECONDS = max(
    24 * 60 * 60,
    int(_env("PROVIDER_IDENTITY_TTL_SECONDS", str(90 * 24 * 60 * 60))),
)
# Remembering a definitive "we have nothing" stops every later scan from asking
# the same question again. Kept well short of a hit's lifetime because a
# provider can gain a record it did not have; a hit cannot become less true.
_PROVIDER_NEGATIVE_TTL_SECONDS = max(
    60 * 60,
    int(_env("PROVIDER_NEGATIVE_TTL_SECONDS", str(7 * 24 * 60 * 60))),
)
# Only answers that mean "this does not exist". A 429 or a 5xx is the provider
# failing to answer, and a 401/403 is our credential -- caching either would
# turn a passing problem into a lasting one.
_PROVIDER_CACHEABLE_MISS_CODES = frozenset({404, 410})
_PROVIDER_MIN_INTERVAL_SECONDS = {
    # Metron documents a sustained limit of 20 requests/minute.  A small
    # cushion prevents clock/network jitter from putting the 20th request over.
    "metron": max(3.2, float(_env("METRON_MIN_INTERVAL_SECONDS", "3.2"))),
    # Comic Vine requires burst control in addition to its hourly resource
    # quota.  Cached series identifiers keep sustained traffic under that cap.
    "comic_vine": max(1.1, float(_env("COMIC_VINE_MIN_INTERVAL_SECONDS", "1.1"))),
    # GCD publishes no anonymous rate, but its behaviour has been measured
    # (2026-09-05): exhausting the anonymous quota returns 429 with a JSON body
    # of {"detail": "Request was throttled. Expected available in N seconds."}
    # and a matching Retry-After. The largest seen was 3189s, which puts the
    # window at an hour rather than a minute -- so a burst is not repaid in a
    # few seconds, and the cushion below is the wrong lever for it. What keeps
    # us under the cap is the per-series cooldown in enrich_catalog_series plus
    # the response cache; this interval only stops two lookups colliding.
    "gcd": max(1.5, float(_env("GCD_MIN_INTERVAL_SECONDS", "1.5"))),
}
def user_cover_dir(kind: str = "files") -> Path:
    """Where a cover the user supplied is kept, beside the catalog.

    It used to live next to this file, inside the image. The container runs
    with a read-only root and mounts only the config, comics and download
    directories, so every upload failed on mkdir and came back as a 422 --
    the feature has never worked once in a deployed container.

    A function rather than a constant for the reason in `_configured_path`:
    a module-level Path is fixed by whichever module imports `app` first.
    """
    configured = _configured_path("USER_COVERS", catalog_database_path().parent / "user-covers")
    _adopt_legacy_user_covers(configured)
    return configured / kind


def _adopt_legacy_user_covers(destination: Path) -> None:
    """Carry covers over from the old in-image location, once.

    In a container the old directory is always empty, because writing to it
    could never succeed. A developer's checkout may hold real files, and
    starting empty beside them would look like losing them.
    """
    marker = destination / ".adopted-legacy"
    try:
        if marker.exists() or not _LEGACY_USER_COVER_DIR.is_dir():
            return
        target = destination / "files"
        target.mkdir(parents=True, exist_ok=True)
        for cover in _LEGACY_USER_COVER_DIR.glob("*.jpg"):
            copy = target / cover.name
            if not copy.exists():
                shutil.copy2(cover, copy)
        marker.write_text("")
    except OSError:
        # Nothing here is worth failing a request over; the originals are
        # never removed, so a failed copy can be retried by hand.
        return


def catalog_database_path() -> Path:
    """Where the catalog lives, honouring a database written before the rename.

    The app was Comicarr, then SonicBoom, now Flipparr. A new install gets
    flipparr.db, but an existing comicarr.db sitting where the database is
    configured to be keeps being used -- starting an empty database beside a
    full one would look exactly like losing the library.
    """
    configured = _configured_path("DATABASE", _DEFAULT_CONFIG_DIR / "flipparr.db")
    legacy = configured.with_name("comicarr.db")
    if not configured.exists() and legacy.exists():
        return legacy
    return configured


def _remote_cache_file() -> Path:
    return _configured_path("REMOTE_CACHE",
        catalog_database_path().parent / "remote-metadata-cache.json",
    )


def _provider_cache_file() -> Path:
    return _configured_path("PROVIDER_CACHE",
        catalog_database_path().parent / "provider-metadata-cache-v1.json",
    )


def web_root() -> Path | None:
    configured = _env("WEB_ROOT") or None
    return Path(configured).resolve() if configured else None
_CATALOG_STORE: CatalogStore | None = None
_CATALOG_STORE_LOCK = threading.Lock()
_ENRICHMENT_STOP = threading.Event()
_ENRICHMENT_THREAD: threading.Thread | None = None
_IMPORT_STOP = threading.Event()
_RESEARCH_STOP = threading.Event()
_RESEARCH_THREAD: threading.Thread | None = None
_IMPORT_THREAD: threading.Thread | None = None
COMIC_LIBRARY_ROOT = Path(_env("LIBRARY_ROOT", "/comics"))
SAB_COMPLETE_ROOT = Path(
    _env("SAB_COMPLETE_ROOT", "/downloads/complete/comics")
)
IMPORT_POLL_SECONDS = max(5, int(_env("IMPORT_POLL_SECONDS", "15")))
# The sweep itself is cheap; the backoff decides how often any given issue is
# actually searched, so this only sets how promptly a due one is picked up.
RESEARCH_POLL_SECONDS = max(60, int(_env("RESEARCH_POLL_SECONDS", "900")))
IMPORT_MIN_FREE_BYTES = max(
    0, int(_env("MIN_FREE_SPACE_MB", "100")) * 1024 * 1024
)
FORMAT_PATTERNS = (
    ("omnibus", re.compile(r"\bomnibus\b", re.I)),
    ("compendium", re.compile(r"\bcompendium\b", re.I)),
    ("deluxe edition", re.compile(r"\bdeluxe(?: edition)?\b", re.I)),
    ("hardcover", re.compile(r"\b(?:hardcover|hard cover|hc)\b", re.I)),
    ("trade paperback", re.compile(r"\b(?:trade paperback|tpb)\b", re.I)),
    ("graphic novel", re.compile(r"\bgraphic novel\b", re.I)),
)
NOISE = re.compile(
    r"\b(?:digital|webrip|web-dl|retail|scan|fixed|empire|zone-empire|"
    r"noads|no ads|english|eng|v\d{2}|\d{3,4}p)\b",
    re.I,
)
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?![\dA-Za-z])")
ISBN_13 = re.compile(r"(?<!\d)(97[89](?:[ -]?\d){10})(?!\d)")
ISBN_10 = re.compile(r"(?<!\d)(\d(?:[ -]?\d){8}[ -]?[\dXx])(?!\d)")
VOLUME = re.compile(r"\b(?:vol(?:ume)?\.?|book)\s*[-#:]?\s*(\d+)\b", re.I)
ISSUE = re.compile(r"(?:^|\s)(?:#|issue\s*[-#:]?\s*)(\d+(?:\.\w+)?)\b", re.I)
# An unmarked issue number is only believable when a year follows it, which is
# what keeps a number inside a title from being read as an issue. Releases
# often put a variant tag in between -- "Terminal 007 (Blind Bag) (2026)" --
# so a bounded run of bracketed groups is allowed to sit there. Anything that
# is not a bracketed group still breaks the anchor, and the year itself is
# still required, so this stays far narrower than matching a bare number.
# "Lot 02 of 04 2026" counts the run, not this issue. Brackets normally keep
# that apart, but an underscored release name has none left by the time it is
# read, and the 04 sat next to the year looking exactly like an issue number.
_NOT_A_COUNT = r"(?<![Oo]f )"
_NON_YEAR_GROUP = r"[\(\[](?!\s*(?:19|20)\d{2}\s*[\)\]])[^\)\]]*[\)\]]"
# Some releases pad inside the brackets: "Terminal 10 (Ultra-Rare) ( 2026)".
_YEAR_GROUP = r"\(\s*(?:19|20)\d{2}\s*\)"
# "02 of 04" says which issue and how many there are. Once underscores have
# become spaces there are no brackets left to strip, so the count is allowed
# to sit between the issue number and the year like any other tag.
_COUNT_OF = r"of\s+\d{1,3}"
_TAGS = rf"(?:(?:{_NON_YEAR_GROUP}|{_COUNT_OF})\s*){{0,4}}"
PADDED_ISSUE = re.compile(
    rf"{_NOT_A_COUNT}\b(0{{1,3}}\d{{1,3}})\b(?=\s*{_TAGS}(?:{_YEAR_GROUP}|(?:19|20)\d{{2}}\b|$))",
    re.I,
)
UNMARKED_ISSUE = re.compile(
    rf"(?:^|\s){_NOT_A_COUNT}(\d{{1,3}}(?:\.\w+)?)\b(?=\s*{_TAGS}{_YEAR_GROUP})",
    re.I,
)


@dataclass
class ParsedFile:
    path: str
    filename: str
    extension: str
    title: str
    volume: int | None = None
    issue: str | None = None
    year: int | None = None
    format: str | None = None
    isbn: str | None = None
    warnings: list[str] = field(default_factory=list)


def catalog_store() -> CatalogStore:
    global _CATALOG_STORE
    database_path = catalog_database_path()
    with _CATALOG_STORE_LOCK:
        # Re-open when the configured database moves. Holding the first store
        # forever would reintroduce, one layer up, the import-time capture that
        # `_configured_path` exists to avoid.
        if _CATALOG_STORE is None or _CATALOG_STORE.database_path != database_path:
            _CATALOG_STORE = CatalogStore(database_path)
        # The store derives issue titles and covers from the files themselves,
        # so it needs to know which language the library asked for. Read it
        # here rather than at construction: the setting can change while the
        # server runs, and the store outlives any one request.
        _CATALOG_STORE.preferred_language = preferred_language()
        return _CATALOG_STORE


PROVIDER_DEFINITIONS = {
    "gcd": {
        "name": "Grand Comics Database", "credentialField": None,
        "capabilities": ["Series structure", "Issue records", "Volume records"],
        "description": "Built-in anonymous catalog source. No account or API key is required. Responses are cached to disk and reused until they age out, so a re-scan of series you've already matched does not repeat the same lookups.",
        "setupSummary": "Works straight away. No account needed.",
        "defaultEnabled": True, "defaultPriority": 10,
    },
    "metron": {
        "name": "Metron", "credentialField": "token",
        "capabilities": ["Issue titles", "Release dates", "Covers", "Creators", "Collected-edition contents", "Cross-provider IDs"],
        "description": "Optional authenticated source for detailed issue metadata, structured reprints, and cross-provider matching.",
        "setupSummary": "Free account. The fastest way to fill in issue details.",
        "credentialUrl": "https://metron.cloud/",
        "credentialHelp": "Create a free account, then copy the token from your Metron profile.",
        "defaultEnabled": False, "defaultPriority": 20,
    },
    "comic_vine": {
        "name": "Comic Vine", "credentialField": "apiKey",
        "capabilities": ["Issue titles", "Release dates", "Covers", "Volumes"],
        "description": "Optional API-key source for issue and volume enrichment. Comic Vine restricts its API to non-commercial use.",
        "setupSummary": "Free API key. Good cover art and issue details.",
        "credentialUrl": "https://comicvine.gamespot.com/api/",
        "credentialHelp": "Sign in and the API page shows your key. Comic Vine restricts API access to personal, non-commercial use.",
        "defaultEnabled": False, "defaultPriority": 30,
    },
    "open_library": {
        "name": "Open Library", "credentialField": None,
        "capabilities": ["ISBN editions", "Volumes", "Book covers"],
        "description": "Built-in source for ISBN-based trades, hardcovers, omnibuses, and ebooks.",
        "setupSummary": "Works straight away. Covers trades and graphic novels.",
        "defaultEnabled": True, "defaultPriority": 40,
    },
}

ACQUISITION_SERVICE_DEFINITIONS = {
    "prowlarr": {
        "name": "Prowlarr", "kind": "Indexer manager",
        "capabilities": ["Usenet indexer search", "Release candidates", "Indexer health"],
        "description": "Search your configured indexers for wanted issues and volumes.",
        "setupSummary": "Searches your sources for issues you are missing.",
        "defaultUrl": "http://localhost:9696", "defaultEnabled": False,
    },
    "sabnzbd": {
        "name": "SABnzbd", "kind": "Download client",
        "capabilities": ["NZB downloads", "Queue status", "Completed-download tracking"],
        "description": "Download selected NZBs and report their progress back to Flipparr.",
        "setupSummary": "Downloads what you pick and reports progress back.",
        "defaultUrl": "http://localhost:8080", "defaultEnabled": False,
    },
}


def _provider_defaults() -> dict[str, dict[str, Any]]:
    return {
        provider_id: {
            "enabled": bool(definition["defaultEnabled"]),
            "priority": int(definition["defaultPriority"]),
        }
        for provider_id, definition in PROVIDER_DEFINITIONS.items()
    }


def load_provider_config() -> dict[str, dict[str, Any]]:
    """Load local provider credentials without ever returning them to the browser."""
    with _PROVIDER_CONFIG_LOCK:
        config = _provider_defaults()
        try:
            saved = json.loads(provider_config_path().read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict):
            for provider_id, values in saved.items():
                if provider_id in config and isinstance(values, dict):
                    config[provider_id].update(values)
        metron_env = os.environ.get("METRON_API_TOKEN", "").strip()
        comic_vine_env = os.environ.get("COMIC_VINE_API_KEY", "").strip()
        if metron_env:
            config["metron"]["token"] = metron_env
            config["metron"]["enabled"] = True
        if comic_vine_env:
            config["comic_vine"]["apiKey"] = comic_vine_env
            config["comic_vine"]["enabled"] = True
        return config


def save_provider_config(provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if provider_id not in PROVIDER_DEFINITIONS:
        raise ValueError("Unknown metadata provider")
    definition = PROVIDER_DEFINITIONS[provider_id]
    if definition["credentialField"] is None:
        raise ValueError("Built-in providers do not require configuration")
    with _PROVIDER_CONFIG_LOCK:
        config = _provider_defaults()
        try:
            saved = json.loads(provider_config_path().read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict):
            for saved_id, values in saved.items():
                if saved_id in config and isinstance(values, dict):
                    config[saved_id].update(values)
        current = config[provider_id]
        credential_field = str(definition["credentialField"])
        supplied_credential = str(payload.get(credential_field) or "").strip()
        if supplied_credential:
            current[credential_field] = supplied_credential
        if bool(payload.get("clearCredentials")):
            current.pop(credential_field, None)
        if "enabled" in payload:
            current["enabled"] = bool(payload["enabled"])
        if "priority" in payload:
            try:
                current["priority"] = max(1, min(99, int(payload["priority"])))
            except (TypeError, ValueError) as exc:
                raise ValueError("Provider priority must be a number") from exc
        config_path = provider_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        os.chmod(temporary, 0o600)
        os.replace(temporary, config_path)
    return public_provider_config()


def public_provider_config() -> dict[str, Any]:
    config = load_provider_config()
    providers = []
    for provider_id, definition in PROVIDER_DEFINITIONS.items():
        values = config[provider_id]
        credential_field = definition["credentialField"]
        configured = credential_field is None or bool(values.get(str(credential_field)))
        providers.append({
            "id": provider_id, "name": definition["name"],
            "description": definition["description"],
            "setupSummary": definition.get("setupSummary") or definition["description"],
            "credentialUrl": definition.get("credentialUrl"),
            "credentialHelp": definition.get("credentialHelp"),
            "capabilities": definition["capabilities"],
            "builtIn": credential_field is None, "configured": configured,
            "enabled": bool(values.get("enabled")) and configured,
            "priority": int(values.get("priority") or definition["defaultPriority"]),
            "credentialField": credential_field,
            "credentialHint": "Saved locally" if configured and credential_field else None,
        })
    providers.sort(key=lambda item: item["priority"])
    return {"providers": providers, "credentialStorage": "local-file"}


def _provider_headers(provider_id: str, credential: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION} (local metadata client)"}
    if provider_id == "metron":
        headers["Authorization"] = f"Bearer {credential}"
    return headers


def _redacted_upstream_url(value: str) -> str:
    """Remove credentials before an upstream URL can enter an exception."""
    parsed = urllib.parse.urlsplit(str(value or ""))
    query = urllib.parse.urlencode(
        [
            (key, "[REDACTED]" if key.casefold() in {
                "api_key", "apikey", "token", "access_token"
            } else item)
            for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query, "")
    )


def _safe_urlopen(request: urllib.request.Request, *, timeout: float):
    """Open one request without retaining credential-bearing URLs on failure."""
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise urllib.error.HTTPError(
            _redacted_upstream_url(exc.geturl() or request.full_url),
            exc.code,
            exc.msg,
            exc.headers,
            None,
        ) from None
    except (urllib.error.URLError, TimeoutError):
        raise urllib.error.URLError("upstream service unavailable") from None


def fetch_json_with_headers(url: str, headers: dict[str, str], timeout: float = 12.0) -> Any:
    _PROVIDER_RESPONSE_HEADERS.value = {}
    request = urllib.request.Request(url, headers=headers)
    with _safe_urlopen(request, timeout=timeout) as response:
        _PROVIDER_RESPONSE_HEADERS.value = dict(response.headers.items())
        return json.load(response)


def _provider_cache_key(provider_id: str, url: str, credential: str) -> str:
    """Create an opaque cache key without writing API credentials to disk."""
    parsed = urllib.parse.urlsplit(url)
    safe_query = urllib.parse.urlencode([
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in {"api_key", "apikey", "token", "access_token"}
    ])
    safe_url = urllib.parse.urlunsplit((
        parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, safe_query, "",
    ))
    credential_scope = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:12]
    return f"{provider_id}:{credential_scope}:{safe_url}"


def _is_remembered_miss(entry: dict[str, Any]) -> bool:
    """A cache row with no payload records that the provider had nothing."""
    return entry.get("data") is None and int(entry.get("status") or 200) >= 400


def _remembered_miss_error(url: str, status: int) -> urllib.error.HTTPError:
    """Replay a remembered miss in the shape callers already handle.

    Callers branch on exc.code (``if exc.code != 404: raise``), so a remembered
    miss has to arrive as the same exception a live one would, not as a special
    return value every call site would need to learn about.
    """
    return urllib.error.HTTPError(
        _redacted_upstream_url(url), status, "Not found (remembered)",
        email.message.Message(), None,
    )


def _provider_cache_ttl(url: str, provider_id: str | None = None) -> int:
    """Keep stable identifiers longer than issue lists for ongoing runs."""
    path = urllib.parse.urlsplit(url).path.casefold()
    if "/search/" in path or path.rstrip("/").endswith("/series"):
        return 7 * 24 * 60 * 60
    if "issue_list" in path or path.rstrip("/").endswith("/issues"):
        return 6 * 60 * 60
    if provider_id == "gcd":
        # GCD's URL shapes don't match the generic patterns above:
        # /series/name/{query}/ is a search, /series/{id}/ and /issue/{id}/
        # are stable identity for published, rarely-revised comics, and
        # /series/{id}/overview/ enumerates a run's issues (can grow).
        if "/series/name/" in path:
            return 7 * 24 * 60 * 60
        if "/overview" in path:
            return 6 * 60 * 60
        if re.search(r"/(?:series|issue)/\d+/?$", path):
            return _PROVIDER_IDENTITY_TTL_SECONDS
    return 24 * 60 * 60


def _provider_retry_after(provider_id: str, headers: dict[str, Any]) -> int | None:
    lowered = {str(key).casefold(): value for key, value in (headers or {}).items()}
    raw_retry = lowered.get("retry-after")
    if raw_retry is not None:
        try:
            return max(1, int(float(str(raw_retry))))
        except (TypeError, ValueError):
            pass
    reset_values = []
    for key in (
        "x-ratelimit-burst-reset", "x-ratelimit-sustained-reset", "x-ratelimit-reset",
    ):
        try:
            reset_values.append(float(str(lowered.get(key))))
        except (TypeError, ValueError):
            continue
    if reset_values:
        return max(1, int(max(reset_values) - time.time()) + 1)
    return 300 if provider_id == "comic_vine" else 60


def _provider_headers_exhausted(headers: dict[str, Any]) -> bool:
    lowered = {str(key).casefold(): value for key, value in (headers or {}).items()}
    remaining = []
    for key in (
        "x-ratelimit-burst-remaining", "x-ratelimit-sustained-remaining", "x-ratelimit-remaining",
    ):
        try:
            remaining.append(int(float(str(lowered.get(key)))))
        except (TypeError, ValueError):
            continue
    return bool(remaining) and min(remaining) <= 0


def _wait_for_provider_slot(provider_id: str) -> None:
    """Serialize one provider without blocking traffic to another provider."""
    while True:
        with _PROVIDER_REQUEST_LOCK:
            now = time.monotonic()
            wait_seconds = max(0.0, _PROVIDER_NEXT_REQUEST_AT.get(provider_id, 0.0) - now)
            if wait_seconds <= 0:
                _PROVIDER_NEXT_REQUEST_AT[provider_id] = (
                    now + _PROVIDER_MIN_INTERVAL_SECONDS.get(provider_id, 1.0)
                )
                return
        time.sleep(wait_seconds)


def _cool_down_provider_requests(provider_id: str, retry_after_seconds: int) -> None:
    with _PROVIDER_REQUEST_LOCK:
        _PROVIDER_NEXT_REQUEST_AT[provider_id] = max(
            _PROVIDER_NEXT_REQUEST_AT.get(provider_id, 0.0),
            time.monotonic() + max(1, retry_after_seconds),
        )


def fetch_provider_json(
    provider_id: str,
    url: str,
    credential: str,
    *,
    timeout: float = 12.0,
    force: bool = False,
) -> Any:
    """Cache, coalesce, and globally pace authenticated metadata requests."""
    key = _provider_cache_key(provider_id, url, credential)
    ttl_seconds = _provider_cache_ttl(url, provider_id)
    stale_entry: dict[str, Any] | None = None
    used_stale_cache = False
    while True:
        with _PROVIDER_JSON_LOCK:
            entry = _PROVIDER_JSON_CACHE.get(key)
            if entry and _is_remembered_miss(entry):
                age = time.time() - float(entry.get("saved_at") or 0)
                if not force and age < _PROVIDER_NEGATIVE_TTL_SECONDS:
                    raise _remembered_miss_error(url, int(entry.get("status") or 404))
            elif entry and isinstance(entry.get("data"), (dict, list)):
                stale_entry = entry
                if not force and time.time() - float(entry.get("saved_at") or 0) < ttl_seconds:
                    return entry["data"]
            event = _PROVIDER_JSON_INFLIGHT.get(key)
            if event is None:
                event = threading.Event()
                _PROVIDER_JSON_INFLIGHT[key] = event
                leader = True
            else:
                leader = False
        if leader:
            break
        event.wait(timeout + max(_PROVIDER_MIN_INTERVAL_SECONDS.get(provider_id, 1.0), 5.0))

    try:
        _wait_for_provider_slot(provider_id)
        _PROVIDER_RESPONSE_HEADERS.value = {}
        if timeout == 12.0:
            payload = fetch_json_with_headers(url, _provider_headers(provider_id, credential))
        else:
            payload = fetch_json_with_headers(
                url, _provider_headers(provider_id, credential), timeout=timeout
            )
        # Guard against a provider echoing our key back. `credential` must be
        # checked for emptiness first: anonymous providers pass "", and "" is a
        # substring of every string, so an unguarded test rejects every one of
        # their successful responses.
        if credential and credential in json.dumps(payload, ensure_ascii=False):
            raise ValueError("Provider returned unsafe credential-bearing data")
        response_headers = getattr(_PROVIDER_RESPONSE_HEADERS, "value", {}) or {}
        if provider_id == "comic_vine" and str((payload or {}).get("status_code")) == "107":
            retry_after = _provider_retry_after(provider_id, response_headers)
            _cool_down_provider_requests(provider_id, retry_after)
            raise MetadataRateLimited(
                provider_id, retry_after,
                "Comic Vine asked Flipparr to pause after reaching its request limit.",
            )
        if _provider_headers_exhausted(response_headers):
            _cool_down_provider_requests(
                provider_id, _provider_retry_after(provider_id, response_headers)
            )
    except Exception as exc:
        limited = _rate_limit_from_error(provider_id, exc)
        if limited:
            _cool_down_provider_requests(provider_id, limited.retry_after_seconds)
            if stale_entry and time.time() - float(stale_entry.get("saved_at") or 0) < _PROVIDER_CACHE_STALE_SECONDS:
                payload = stale_entry["data"]
                used_stale_cache = True
            else:
                with _PROVIDER_JSON_LOCK:
                    _PROVIDER_JSON_INFLIGHT.pop(key, None)
                    event.set()
                raise limited
        else:
            miss_code = (
                exc.code
                if isinstance(exc, urllib.error.HTTPError)
                and exc.code in _PROVIDER_CACHEABLE_MISS_CODES
                else None
            )
            saved_at = time.time()
            with _PROVIDER_JSON_LOCK:
                if miss_code is not None:
                    _PROVIDER_JSON_CACHE[key] = {
                        "saved_at": saved_at, "status": miss_code, "data": None,
                    }
                _PROVIDER_JSON_INFLIGHT.pop(key, None)
                event.set()
            if miss_code is not None:
                persist_provider_cache(key, provider_id, saved_at, miss_code, None)
            raise

    saved_at = time.time()
    with _PROVIDER_JSON_LOCK:
        if not used_stale_cache:
            _PROVIDER_JSON_CACHE[key] = {
                "saved_at": saved_at, "status": 200, "data": payload,
            }
        _PROVIDER_JSON_INFLIGHT.pop(key, None)
        event.set()
    if not used_stale_cache:
        # Deliberately outside the lock: this commits and fsyncs, and holding
        # the cache lock across that would serialise every provider fetch.
        persist_provider_cache(key, provider_id, saved_at, 200, payload)
    return payload


def fetch_bytes_with_headers(
    url: str,
    headers: dict[str, str],
    *,
    timeout: float = 30.0,
    max_bytes: int = NZB_MAX_BYTES,
) -> bytes:
    """Fetch a bounded binary response so an upstream cannot exhaust local memory."""
    request = urllib.request.Request(url, headers=headers)
    with _safe_urlopen(request, timeout=timeout) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"Downloaded NZB exceeds the {max_bytes // (1024 * 1024)} MB safety limit")
    return payload


class UploadRedirected(Exception):
    """An upload was redirected somewhere it must not be replayed."""


_UPLOAD_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class _ReportRedirect(urllib.request.HTTPRedirectHandler):
    """Report redirects instead of following them.

    urllib answers a redirected POST by re-sending it as a GET with no body.
    An upload that is redirected therefore arrives empty, and the service
    replies as though nothing was sent -- which reads as "the service rejected
    your file" when the truth is that the file never left. A proxy with
    "force SSL" in front of an http:// address does exactly this.
    """

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _upload_redirect_target(current: str, location: str) -> str | None:
    """Where an upload may be retried, or None when it must not follow.

    Only the same host, and only with the scheme unchanged or http upgrading
    to https. A redirect can name any host, and this request carries an API
    key in its query string, so anything else is refused rather than replayed.
    """
    if not location:
        return None
    target = urllib.parse.urljoin(current, location)
    here = urllib.parse.urlsplit(current)
    there = urllib.parse.urlsplit(target)
    if not there.hostname or here.hostname != there.hostname:
        return None
    if there.scheme == here.scheme:
        return target
    if here.scheme == "http" and there.scheme == "https":
        return target
    return None


def post_multipart_file_json(
    url: str,
    *,
    field_name: str,
    filename: str,
    content: bytes,
    content_type: str,
    headers: dict[str, str],
    timeout: float = 30.0,
) -> Any:
    """Upload one in-memory file and decode the JSON response."""
    boundary = f"----Flipparr{os.urandom(12).hex()}"
    safe_field = re.sub(r"[^A-Za-z0-9_-]", "", field_name) or "name"
    source_name = Path(filename)
    safe_stem = _safe_path_component(source_name.stem, "comic")[:170]
    safe_suffix = source_name.suffix.casefold() if source_name.suffix else ".bin"
    safe_filename = f"{safe_stem}{safe_suffix}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{safe_field}"; filename="{safe_filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    request_headers = {
        **headers,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    opener = urllib.request.build_opener(_ReportRedirect)
    target = url
    # The original request, then at most one redirect. Following the upgrade
    # keeps an http:// address working behind a proxy that forces SSL, rather
    # than reporting the empty upload it would otherwise produce.
    for _ in range(2):
        request = urllib.request.Request(
            target, data=body, headers=request_headers, method="POST"
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in _UPLOAD_REDIRECT_CODES:
                raise urllib.error.HTTPError(
                    _redacted_upstream_url(exc.geturl() or target),
                    exc.code, exc.msg, exc.headers, None,
                ) from None
            following = _upload_redirect_target(
                target, exc.headers.get("Location") or ""
            )
            if not following:
                raise UploadRedirected(
                    "the upload was redirected to "
                    f"{_redacted_upstream_url(exc.headers.get('Location') or 'an unnamed location')}"
                ) from None
            target = following
        except (urllib.error.URLError, TimeoutError):
            raise urllib.error.URLError("upstream service unavailable") from None
    raise UploadRedirected("the upload was redirected more than once")


def test_provider_connection(provider_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    if provider_id not in {"metron", "comic_vine"}:
        raise ValueError("This provider does not require a connection test")
    config = load_provider_config()[provider_id]
    credential_field = str(PROVIDER_DEFINITIONS[provider_id]["credentialField"])
    credential = str(payload.get(credential_field) or config.get(credential_field) or "").strip()
    if not credential:
        raise ValueError(f"Enter a {PROVIDER_DEFINITIONS[provider_id]['name']} credential first")
    if provider_id == "metron":
        url = f"{METRON_API_BASE}/series/?page=1"
        data = fetch_provider_json(provider_id, url, credential, force=True)
        detail = f"Authenticated successfully; {int(data.get('count') or 0):,} series are available."
    else:
        url = f"{COMIC_VINE_API_BASE}/issues/?" + urllib.parse.urlencode({
            "api_key": credential, "format": "json", "limit": 1, "field_list": "id",
        })
        data = fetch_provider_json(provider_id, url, credential, force=True)
        if str(data.get("status_code")) != "1":
            raise ValueError(str(data.get("error") or "Comic Vine rejected the API key"))
        detail = "API key verified successfully."
    return {"provider": provider_id, "status": "connected", "detail": detail}


def _acquisition_service_defaults() -> dict[str, dict[str, Any]]:
    return {
        service_id: {
            "enabled": bool(definition["defaultEnabled"]),
            "url": str(definition["defaultUrl"]),
            **({"category": "comics"} if service_id == "sabnzbd" else {}),
        }
        for service_id, definition in ACQUISITION_SERVICE_DEFINITIONS.items()
    }


def _normalize_service_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a complete HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Enter credentials in the API key field, not in the URL")
    if parsed.query or parsed.fragment or parsed.params:
        raise ValueError("Enter the service base URL without query parameters or fragments")
    return url


def load_acquisition_service_config() -> dict[str, dict[str, Any]]:
    """Load local acquisition credentials without returning secrets to the browser."""
    with _ACQUISITION_CONFIG_LOCK:
        config = _acquisition_service_defaults()
        try:
            saved = json.loads(acquisition_config_path().read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict):
            for service_id, values in saved.items():
                if service_id in config and isinstance(values, dict):
                    config[service_id].update(values)
        environment = {
            "prowlarr": (os.environ.get("PROWLARR_URL"), os.environ.get("PROWLARR_API_KEY")),
            "sabnzbd": (os.environ.get("SABNZBD_URL"), os.environ.get("SABNZBD_API_KEY")),
        }
        for service_id, (url, api_key) in environment.items():
            if str(url or "").strip():
                config[service_id]["url"] = str(url).strip().rstrip("/")
            if str(api_key or "").strip():
                config[service_id]["apiKey"] = str(api_key).strip()
                config[service_id]["enabled"] = True
        sab_category = str(os.environ.get("SABNZBD_CATEGORY") or "").strip()
        if sab_category:
            config["sabnzbd"]["category"] = sab_category
        return config


def public_acquisition_service_config() -> dict[str, Any]:
    config = load_acquisition_service_config()
    services = []
    for service_id, definition in ACQUISITION_SERVICE_DEFINITIONS.items():
        values = config[service_id]
        configured = bool(values.get("url") and values.get("apiKey"))
        services.append({
            "id": service_id, "name": definition["name"], "kind": definition["kind"],
            "description": definition["description"],
            "setupSummary": definition.get("setupSummary") or definition["description"],
            "capabilities": definition["capabilities"],
            "configured": configured, "enabled": bool(values.get("enabled")) and configured,
            "url": _redacted_upstream_url(
                str(values.get("url") or definition["defaultUrl"])
            ).split("?", 1)[0],
            "category": str(values.get("category") or "comics") if service_id == "sabnzbd" else None,
            "credentialHint": "Saved locally" if values.get("apiKey") else None,
        })
    return {"services": services, "credentialStorage": "local-file"}


def save_acquisition_service_config(service_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if service_id not in ACQUISITION_SERVICE_DEFINITIONS:
        raise ValueError("Unknown acquisition service")
    with _ACQUISITION_CONFIG_LOCK:
        config = _acquisition_service_defaults()
        try:
            saved = json.loads(acquisition_config_path().read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict):
            for saved_id, values in saved.items():
                if saved_id in config and isinstance(values, dict):
                    config[saved_id].update(values)
        current = config[service_id]
        if "url" in payload and str(payload.get("url") or "").strip():
            current["url"] = _normalize_service_url(payload["url"])
        supplied_key = str(payload.get("apiKey") or "").strip()
        if supplied_key:
            current["apiKey"] = supplied_key
        if bool(payload.get("clearCredentials")):
            current.pop("apiKey", None)
        if "enabled" in payload:
            current["enabled"] = bool(payload["enabled"])
        if service_id == "sabnzbd" and "category" in payload:
            category = str(payload.get("category") or "").strip()
            if not category:
                raise ValueError("Enter a SABnzbd category")
            current["category"] = category
        config_path = acquisition_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        os.chmod(temporary, 0o600)
        os.replace(temporary, config_path)
    return public_acquisition_service_config()


def test_acquisition_service_connection(
    service_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    if service_id not in ACQUISITION_SERVICE_DEFINITIONS:
        raise ValueError("Unknown acquisition service")
    payload = payload or {}
    config = load_acquisition_service_config()[service_id]
    url = _normalize_service_url(payload.get("url") or config.get("url"))
    api_key = str(payload.get("apiKey") or config.get("apiKey") or "").strip()
    if not api_key:
        raise ValueError(f"Enter a {ACQUISITION_SERVICE_DEFINITIONS[service_id]['name']} API key first")
    if service_id == "prowlarr":
        data = fetch_json_with_headers(
            f"{url}/api/v1/system/status",
            {"Accept": "application/json", "X-Api-Key": api_key, "User-Agent": f"Flipparr/{APP_VERSION}"},
        )
        version = str(data.get("version") or "unknown")
        detail = f"Connected to Prowlarr {version}."
    else:
        endpoint = f"{url}/api?" + urllib.parse.urlencode({
            "mode": "version", "output": "json", "apikey": api_key,
        })
        data = fetch_json_with_headers(endpoint, {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"})
        version = str((data.get("version") if isinstance(data, dict) else None) or "unknown")
        detail = f"Connected to SABnzbd {version}; downloads will use the “{str(payload.get('category') or config.get('category') or 'comics')}” category."
    return {"service": service_id, "status": "connected", "detail": detail}


def _enabled_acquisition_service(service_id: str) -> dict[str, Any]:
    config = load_acquisition_service_config().get(service_id) or {}
    if not config.get("enabled") or not config.get("apiKey"):
        name = ACQUISITION_SERVICE_DEFINITIONS[service_id]["name"]
        raise ValueError(f"Connect and enable {name} in Settings first")
    return {**config, "url": _normalize_service_url(config.get("url"))}


def preferred_language() -> str:
    return str(load_app_settings().get("preferredLanguage") or "").strip().casefold()


def release_language_conflicts(title: Any, wanted: str | None = None) -> str | None:
    """The language a release states, when that is not the one wanted."""
    want = (wanted if wanted is not None else preferred_language()).strip().casefold()
    if not want:
        return None
    stated = detect_release_language(title)
    return stated if stated and stated != want else None


def _release_tokens(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").casefold())


def _without_leading_article(value: Any) -> str:
    """A catalog keeps the article a release drops, and they are one series."""
    return re.sub(r"^(?:the|a|an)\s+", "", str(value or "").strip(), flags=re.I).strip()


def _release_issue_matches(title: str, issue_number: Any) -> bool:
    number = str(issue_number or "").strip()
    if not number:
        return False
    escaped = re.escape(number.lstrip("0") or "0")
    # Numbers that belong to something other than this issue are taken out
    # first: "02 of 04" counts the run, and "Vol 04" is a collected volume.
    # Both read as issue four, so a wanted issue would have been answered
    # with a different issue, or with a trade paperback of the whole run.
    cleaned = re.sub(r"\b(?:vol|volume|v)\.?\s*\d{1,4}\b", " ", str(title or ""), flags=re.I)
    cleaned = re.sub(r"\bof\s*\d{1,4}\b", " ", cleaned, flags=re.I)
    return bool(re.search(rf"(?:#|\b0*){escaped}(?!\d)", cleaned, re.I))


# Usenet subjects wrap the real name in quotes; posts are often prefixed with a
# date stamp, a part counter, or a poster's tag. None of that is the series, and
# the date in particular also looks like an issue number.
_RELEASE_QUOTED_NAME = re.compile(r'"([^"]{4,})"')
_RELEASE_NOISE_PREFIX = re.compile(
    r"""^\s*(?:
          \[[^\]]*\]                      # [46/80]
        | \((?:\d{1,4}/\d{1,4})\)         # (1/12)
        | \d{4}[.\-/\s]\d{2}[.\-/\s]\d{2}  # 2013.05.01, before or after dots go
        | \d{1,3}/\d{1,3}                  # 46/80
        | grab\s*bag | 0-?day | yenc | req(?:uest)?
        | [-–—:]
    )\s*""",
    re.I | re.X,
)
# A trailing "Vol 1 No" or "#" belongs to the number, not to the series name.
_RELEASE_NUMBER_LEAD = re.compile(
    r"\b(?:v(?:ol(?:ume)?)?\.?\s*\d*|n(?:o|um(?:ber)?)?\.?|issue|#)\s*$", re.I
)


def _release_series_lead(title: str, issue_number: Any) -> str | None:
    """The series name a release states, or None when it states none.

    A release names its series before the issue number and its provenance
    after, so the series is the text in front of that number -- once the
    wrappers Usenet adds in front of the name are taken off.
    """
    number = str(issue_number or "").strip()
    if not number:
        return None
    text = str(title or "")
    quoted = _RELEASE_QUOTED_NAME.search(text)
    if quoted:
        # A yEnc subject carries the real filename in quotes.
        text = quoted.group(1)
    def strip_noise(value: str) -> str:
        previous = None
        while previous != value:
            previous = value
            value = _RELEASE_NOISE_PREFIX.sub("", value)
        return value

    # Once with the separators the poster used, once after they are normalised:
    # a date is "2013.05.01" before and "2013 05 01" after.
    text = strip_noise(text)
    text = strip_noise(re.sub(r"[._]+", " ", text))
    escaped = re.escape(number.lstrip("0") or "0")
    found = re.search(rf"(?:#|\b0*){escaped}(?!\d)", text, re.I)
    if not found:
        return None
    lead = text[: found.start()]
    # "Fables Vol 1 No 129" leaves "Fables Vol 1 No", which takes two passes.
    previous = None
    while previous != lead:
        previous = lead
        lead = _RELEASE_NUMBER_LEAD.sub("", lead)
    return lead


def _release_series_trim(lead: str, publisher: Any) -> str:
    """Take off what a release puts around the series name but is not it.

    Scene names often lead with the publisher and mark the number with "No",
    so the text before the number reads "Image Comics If Destruction Be Our
    Lot No" -- the right series, unrecognisable by equality. Only this
    series' own publisher is removed, never an arbitrary leading word, and
    the wanted name is never matched at the end of a longer one: that is what
    once let "Thor: The Deviants Saga" answer a request for "Saga".
    """
    value = str(lead or "").strip()
    value = re.sub(
        r"[\s.]*\b(?:no|nos|number|issue)\.?\s*$", "", value, flags=re.I
    ).strip(" -.:")
    known = str(publisher or "").strip()
    if known:
        value = re.sub(
            rf"^{re.escape(known)}(?:\s+comics?)?\b[\s\-.:]*", "", value, flags=re.I
        ).strip(" -.:")
    return value


def _release_series_matches(
    title: str, series_title: Any, issue_number: Any, publisher: Any = None
) -> bool:
    """Whether a release is this series, not just a name containing its words.

    Asking only that the wanted words appear somewhere made every release
    containing them score as a title match. Wanting "Saga" took "Thor: The
    Deviants Saga", the French "DC Saga" anthology, "Conan Saga", "The Saga of
    Swamp Thing", and two Dragon Ball Z .mp4 files -- because the one word they
    share is the whole of the wanted title. Any short name is a substring of
    something longer: Chew, Hulk, Batman.

    The series a release names has to be the series wanted. Comparing that
    rather than the whole string also stops "Batman Beyond" answering a request
    for "Batman", which testing only the end of the name would have allowed.
    """
    wanted = normalized_title(str(series_title or ""))
    if not wanted:
        return False
    lead = _release_series_lead(title, issue_number)
    if lead is None:
        return False
    if normalized_title(lead) == wanted:
        return True
    # "The Department of Truth" and "Department of Truth" are one series, and
    # the correct release could not otherwise score high enough to be grabbed.
    # This stays an equality, so it does not reopen the far worse mistake of
    # letting "Thor: The Deviants Saga" answer a request for "Saga".
    bare_wanted = normalized_title(_without_leading_article(series_title))
    bare_lead = normalized_title(_without_leading_article(lead))
    if bool(bare_wanted) and bare_lead == bare_wanted:
        return True
    trimmed = _release_series_trim(lead, publisher)
    return bool(bare_wanted) and normalized_title(
        _without_leading_article(trimmed)
    ) == bare_wanted


def _release_year_conflict(title: str, context: dict[str, Any]) -> str | None:
    """Why a release dated years away from this issue is another comic, or None.

    A relaunch reuses the name and the numbers: "Ultimate Spider-Man 003
    (2024)" is Hickman's #3, not the 2001 issue of the 2000 run, and it scored
    as a match -- the year only ever added points -- so the 2000 run was filled
    with the relaunch and replacing those issues found the same files again.

    Only years after the issue number count, so "2000 AD 045" keeps its title,
    and only when the issue's own year is known: a run's start year alone would
    refuse Batman #500 from 1993 for saying 1993.
    """
    number = str(context.get("issueNumber") or "").strip()
    try:
        wanted = int(context.get("publicationYear") or 0)
    except (TypeError, ValueError):
        wanted = 0
    if not number or not wanted:
        return None
    text = re.sub(r"[._]+", " ", str(title or ""))
    found = re.search(rf"(?:#|\b0*){re.escape(number.lstrip('0') or '0')}(?!\d)", text, re.I)
    if not found:
        return None
    stated = [int(year) for year in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text[found.end():])]
    try:
        series_year = int(context.get("seriesYear") or 0)
    except (TypeError, ValueError):
        series_year = 0
    # A release may name the run's start year instead of the issue's, and a
    # cover year is often a year after the date it went on sale.
    plausible = {wanted, series_year} - {0}
    if stated and not any(abs(year - known) <= 1 for year in stated for known in plausible):
        return f"Dated {stated[0]}, but this issue is from {wanted}"
    return None


def _release_candidate_score(release: dict[str, Any], context: dict[str, Any]) -> tuple[int, list[str]]:
    title = str(release.get("title") or "")
    # A release that says it is another language is not this comic in any
    # useful sense, so it never reaches a score that could be grabbed.
    conflict = release_language_conflicts(title, context.get("preferredLanguage"))
    if conflict:
        return 0, [f"Labelled as {language_name(conflict)}"]
    if context.get("format") == "manga":
        return _manga_release_score(release, context)
    conflict_year = _release_year_conflict(title, context)
    if conflict_year:
        return 0, [conflict_year]
    score = 0
    reasons: list[str] = []
    if _release_series_matches(
        title, context.get("seriesTitle"), context.get("issueNumber"),
        context.get("publisher"),
    ):
        score += 50
        reasons.append("Series title matches")
    if _release_issue_matches(title, context.get("issueNumber")):
        score += 35
        reasons.append(f"Issue #{context.get('issueNumber')} matches")
    year = str(context.get("publicationYear") or context.get("seriesYear") or "").strip()
    if year and year in title:
        score += 10
        reasons.append(f"Publication year {year} matches")
    category_ids = {
        str(category.get("id") if isinstance(category, dict) else category)
        for category in (release.get("categories") or [])
    }
    if "7030" in category_ids:
        score += 5
        reasons.append("Listed as a comic")
    return min(score, 100), reasons


def _release_format_tags(title: Any) -> list[str]:
    value = str(title or "")
    tags = []
    for label, pattern in (
        ("CBZ", r"\bcbz\b"), ("CBR", r"\bcbr\b"), ("Digital", r"\bdigital\b"),
        ("Retail", r"\bretail\b"), ("English", r"\b(?:english|eng)\b"),
        ("Manga", r"\bmanga\b"), ("Hybrid", r"\bhybrid\b"), ("EPUB", r"\bepub\b"),
    ):
        if re.search(pattern, value, re.I):
            tags.append(label)
    return tags


class ReleaseDownloadError(RuntimeError):
    """Prowlarr could not provide a safe, valid NZB for a selected release."""


class SABSubmissionError(RuntimeError):
    """SABnzbd could not accept an NZB that Flipparr already retrieved."""


def _prowlarr_download_reference(download_url: Any) -> str:
    """Retain only the Prowlarr-local path and non-credential query parameters."""
    parsed = urllib.parse.urlsplit(str(download_url or "").strip())
    if parsed.scheme and parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("Prowlarr returned an unsupported release download URL")
    if not parsed.path.startswith("/"):
        raise ValueError("Prowlarr returned an invalid release download URL")
    query = urllib.parse.urlencode([
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in {"apikey", "api_key"}
    ])
    return urllib.parse.urlunsplit(("", "", parsed.path, query, ""))


def _release_candidate_key(release: dict[str, Any], download_path: str) -> str:
    """Build a stable, credential-free identity for one Prowlarr search result."""
    identity = json.dumps(
        {
            "guid": str(release.get("guid") or ""),
            "indexer": str(release.get("indexer") or ""),
            "title": str(release.get("title") or ""),
            "size": int(release.get("size") or 0),
            "downloadPath": download_path,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _legacy_release_key(download: dict[str, Any]) -> str:
    """Give pre-migration downloads a durable failure identity."""
    identity = "|".join(
        (
            "legacy",
            str(download.get("release_title") or ""),
            str(download.get("sab_nzo_id") or ""),
        )
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _fetch_selected_nzb(candidate: dict[str, Any]) -> bytes:
    """Retrieve and validate the selected NZB through the configured Prowlarr service."""
    prowlarr = _enabled_acquisition_service("prowlarr")
    reference = urllib.parse.urlsplit(str(candidate.get("downloadPath") or ""))
    base = urllib.parse.urlsplit(str(prowlarr["url"]))
    endpoint = urllib.parse.urlunsplit((
        base.scheme, base.netloc, reference.path, reference.query, "",
    ))
    try:
        payload = fetch_bytes_with_headers(
            endpoint,
            {
                "Accept": "application/x-nzb, application/xml;q=0.9, */*;q=0.1",
                "X-Api-Key": str(prowlarr["apiKey"]),
                "User-Agent": "Flipparr/1.0",
            },
            timeout=45.0,
            max_bytes=NZB_MAX_BYTES,
        )
    except urllib.error.HTTPError as exc:
        detail = (
            "Prowlarr rejected the NZB request"
            if exc.code in {401, 403}
            else f"Prowlarr could not fetch the NZB ({exc.code})"
        )
        raise ReleaseDownloadError(detail) from None
    except (urllib.error.URLError, TimeoutError):
        raise ReleaseDownloadError("Flipparr could not retrieve the NZB from Prowlarr") from None
    except ValueError as exc:
        raise ReleaseDownloadError(str(exc)) from exc
    if not payload.strip():
        raise ReleaseDownloadError("Prowlarr returned an empty NZB")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ReleaseDownloadError("Prowlarr returned a response that is not a valid NZB") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "nzb":
        raise ReleaseDownloadError("Prowlarr returned a response that is not an NZB document")
    return payload


def _submit_nzb_to_sabnzbd(
    sab: dict[str, Any], title: str, payload: bytes
) -> dict[str, Any]:
    endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
        "mode": "addfile", "nzbname": title,
        "cat": str(sab.get("category") or "comics"),
        "priority": -100, "pp": -1, "output": "json", "apikey": sab["apiKey"],
    })
    filename = f"{_safe_path_component(title, 'comic')}.nzb"
    try:
        response = post_multipart_file_json(
            endpoint,
            field_name="name",
            filename=filename,
            content=payload,
            content_type="application/x-nzb",
            headers={"Accept": "application/json", "User-Agent": "Flipparr/1.0"},
            timeout=30.0,
        )
    except urllib.error.HTTPError as exc:
        detail = (
            "SABnzbd rejected the upload"
            if exc.code in {401, 403}
            else f"SABnzbd upload failed ({exc.code})"
        )
        raise SABSubmissionError(detail) from None
    except (urllib.error.URLError, TimeoutError):
        raise SABSubmissionError("Flipparr could not reach SABnzbd") from None
    except UploadRedirected as exc:
        raise SABSubmissionError(
            f"SABnzbd's address redirects and {exc}. A redirected upload arrives "
            "empty. Point the SABnzbd URL at the address it redirects to, or at "
            "SABnzbd directly."
        ) from None
    if not isinstance(response, dict) or not response.get("status"):
        # SABnzbd says why in the same object. Reporting only that it "did not
        # accept" the file threw that away and left nothing to act on.
        reason = ""
        if isinstance(response, dict):
            reason = str(response.get("error") or "").strip()
        raise SABSubmissionError(
            f"SABnzbd did not accept the uploaded NZB: {reason}" if reason
            else "SABnzbd did not accept the uploaded NZB"
        )
    return response


# ---------------------------------------------------------------------------
# Manga
# ---------------------------------------------------------------------------

def _publisher_key(value: Any) -> str:
    """A publisher name compared without case, accents or punctuation."""
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


# Who publishes manga in English. Comic Vine has no language field and lists
# each edition of a manga as its own run -- Chainsaw Man is Shueisha (the
# Japanese original), Viz (English), Egmont (German), Norma (Spanish) -- so
# the publisher is the only thing that says which one an English reader wants.
_ENGLISH_MANGA_PUBLISHERS = frozenset(_publisher_key(name) for name in (
    "Viz", "Viz Media", "Viz Media LLC", "Kodansha Comics", "Kodansha Comics USA",
    "Kodansha USA", "Kodansha USA Publishing", "Yen Press", "Yen On",
    "Seven Seas", "Seven Seas Entertainment", "Vertical", "Vertical Comics",
    "Vertical Inc", "Tokyopop", "Dark Horse Manga", "Square Enix Manga & Books",
    "Square Enix Manga", "Ghost Ship", "Denpa", "Udon Entertainment",
    "J-Novel Club", "Airship", "Titan Manga", "Digital Manga Publishing",
    "Del Rey Manga", "One Peace Books", "Kaiten Books", "Irodori Comics",
))
# Japanese originals and other languages' editions of the same books.
_OTHER_MANGA_EDITIONS = frozenset(_publisher_key(name) for name in (
    "Shueisha", "Kodansha", "Shogakukan", "Hakusensha", "Kadokawa",
    "Kadokawa Shoten", "Akita Shoten", "Square Enix", "Futabasha", "Houbunsha",
    "Ichijinsha", "Mag Garden", "Shonen Gahosha", "Tokuma Shoten", "Alpha Polis",
    "Pika Édition", "Ki-oon", "Kurokawa", "Glénat", "Kana", "Delcourt", "Kazé",
    "Crunchyroll SA", "Crunchyroll SAS", "Norma Editorial", "Editorial Ivrea",
    "Ivrea", "Panini", "Panini Comics", "Panini Verlag", "Panini Manga",
    "Panini Brasil", "Planet Manga", "Egmont", "Egmont Ehapa Verlag",
    "Egmont Manga", "Carlsen", "Carlsen Manga", "Tokyopop GmbH", "Haksan",
    "Daewon C.I.", "Tong Li Publishing Co.", "NXB Trẻ", "Edizioni BD", "J-Pop",
    "Star Comics", "JBC", "NewPOP", "Nasha idea", "Planeta Cómic",
    "Planeta DeAgostini", "Milky Way Ediciones", "ECC Ediciones", "Altraverse",
    "Manga Cult",
))


def manga_edition(publisher: Any) -> str | None:
    """'manga' for an English manga publisher, 'foreign' for a Japanese
    original or another language's edition, None for everyone else."""
    key = _publisher_key(publisher)
    if not key:
        return None
    if key in _ENGLISH_MANGA_PUBLISHERS:
        return "manga"
    if key in _OTHER_MANGA_EDITIONS:
        return "foreign"
    return None


# Manga is posted in Comics and in EBook, where comics are only in Comics --
# and some of it under TV/Anime: Blue Lock v02 and v24 exist only there, and
# every other volume was found, so those two looked unposted.
MANGA_CATEGORIES = ("7030", "7020", "5070")
# CBZ and CBR only: the library reads comic archives, and an EPUB would sit
# there looking like the volume without being one it can show.
MANGA_FILE_TYPES = frozenset({".cbz", ".cbr"})
# "v18", "v018", "Vol.18", "Vol 18", "Volume 18" -- never a number glued to a
# letter, which is how the release groups 21A1 and 1r0n read as volumes.
_MANGA_VOLUME = re.compile(
    r"(?<![A-Za-z0-9])(?:v|vol(?:ume)?)\.?\s*0*(\d{1,4})(?![\dA-Za-z])", re.I
)
_MANGA_RANGE = re.compile(
    r"(?<![A-Za-z0-9])(?:v|vol(?:ume)?)\.?\s*\d{1,4}\s*[-–~]\s*(?:v|vol(?:ume)?)?\.?\s*\d{1,4}(?![\dA-Za-z])",
    re.I,
)
_MANGA_CHAPTER = re.compile(r"(?<![A-Za-z0-9])(?:c|ch|chapter)\.?\s*\d{1,4}(?![\dA-Za-z])", re.I)
# Not a comic-archive volume of this book: another format, an adult parody
# named after the series, or an untranslated scan.
_MANGA_REFUSED = re.compile(
    r"\b(?:mobi|azw3?|hentai|doujin(?:shi)?|porn|xxx|nsfw|erotic|parody|raw)\b", re.I
)
_MANGA_VIDEO = re.compile(
    r"\b(?:blu-?ray|bd(?:rip|remux)?|web-?(?:dl|rip)|hdtv|remux|x26[45]|h\s?26[45]|hevc|avc|av1"
    r"|\d{3,4}p|dual|dub(?:bed)?|subs?|multi-?subs?|anime|episodes?|season|s\d{1,2}e\d{1,3}"
    r"|mkv|mp4|avi|aac|ddp?\d?|flac|opus)\b",
    re.I,
)
# Publishers that lead a scene name: "VIZ.Media.-.Spy.X.Family.Vol.03",
# "Kodansha-Blue.Lock.Vol.03". Longest first, so "Viz Media" goes whole.
_MANGA_PUBLISHER_PREFIXES = (
    "viz media", "viz", "kodansha comics", "kodansha usa", "kodansha", "yen press",
    "seven seas entertainment", "seven seas", "vertical comics", "vertical",
    "tokyopop", "dark horse manga", "dark horse", "square enix manga", "square enix",
    "ghost ship", "j novel club", "denpa", "udon",
)


def _number_label(context: dict[str, Any]) -> str:
    number = context.get("issueNumber")
    return f"Vol. {number}" if context.get("format") == "manga" else f"#{number}"


def _manga_release_name(title: Any) -> str:
    """The release name, unwrapped from Usenet's subject line and its dots."""
    text = str(title or "")
    quoted = _RELEASE_QUOTED_NAME.search(text)
    if quoted:
        text = quoted.group(1)
    text = re.sub(r"[._]+", " ", text)
    previous = None
    while previous != text:
        previous = text
        text = _RELEASE_NOISE_PREFIX.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_manga_publisher(value: Any, publisher: Any = None) -> str:
    """Take a leading publisher off a release or file name."""
    text = re.sub(r"[-–—:]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    prefixes = sorted(
        {_publisher_key(publisher)} | set(_MANGA_PUBLISHER_PREFIXES) - {""},
        key=len, reverse=True,
    )
    for prefix in prefixes:
        stripped = re.sub(rf"^{re.escape(prefix)}\b\s*", "", text, flags=re.I)
        if stripped != text:
            return stripped.strip()
    return text


def _manga_volumes(name: Any) -> set[int]:
    return {int(found.group(1)) for found in _MANGA_VOLUME.finditer(str(name or ""))}


def _manga_file_volume(name: Any) -> str | None:
    """The one volume a file name states, or None for none, a range or a pack."""
    text = _manga_release_name(Path(str(name or "")).stem)
    if _MANGA_RANGE.search(text) or _MANGA_CHAPTER.search(text):
        return None
    volumes = _manga_volumes(text)
    return str(volumes.pop()) if len(volumes) == 1 else None


# ---------------------------------------------------------------------------
# Manga ebooks, made into comic archives
# ---------------------------------------------------------------------------
#
# The publisher's own digital edition is often the only English copy posted --
# Chainsaw Man volumes 1 and 2 exist on the indexers only as Viz's
# "HYBRID.MANGA.eBook", a PDF of 193 pages, 192 of them a single JPEG and none
# carrying text. A page that is one JPEG can be copied into a CBZ byte for byte,
# so the library stays comic archives without re-rendering anything. An ebook
# that is not built that way -- text, layered pages, encryption -- is refused,
# and the next release is tried.

MANGA_EBOOK_TYPES = frozenset({".pdf", ".epub"})


class EbookNotConvertible(ValueError):
    """An ebook that is not one image per page, so it cannot become a CBZ."""


_PDF_SUBSECTION = re.compile(rb"\s*(\d+)\s+(\d+)[ \t]*\r?\n")
_PDF_XREF_ENTRY = re.compile(rb"(\d{10})\s(\d{5})\s([nf])")
_PDF_OBJECT_HEAD = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj\s*")
_PDF_REFERENCE = re.compile(rb"(\d+)\s+\d+\s+R")


def _pdf_cross_reference(data: bytes) -> tuple[dict[int, int], int]:
    """Object number -> byte offset, and the catalog's object number."""
    marker = data.rfind(b"startxref")
    found = re.search(rb"startxref\s+(\d+)", data[marker:]) if marker >= 0 else None
    if not found:
        raise EbookNotConvertible("The PDF has no cross-reference table")
    offset = int(found.group(1))
    offsets: dict[int, int] = {}
    root: int | None = None
    visited: set[int] = set()
    while offset and offset not in visited:
        visited.add(offset)
        if not data.startswith(b"xref", offset):
            # PDF 1.5 packs the table into a compressed stream. Publisher image
            # PDFs are the classic kind; anything else is refused, not guessed at.
            raise EbookNotConvertible("The PDF uses a compressed cross-reference table")
        position = offset + 4
        while True:
            section = _PDF_SUBSECTION.match(data, position)
            if not section:
                break
            first, count = int(section.group(1)), int(section.group(2))
            position = section.end()
            for index in range(count):
                entry = _PDF_XREF_ENTRY.match(data, position)
                if not entry:
                    raise EbookNotConvertible("The PDF cross-reference table is damaged")
                if entry.group(3) == b"n":
                    # The newest section is read first, so it wins.
                    offsets.setdefault(first + index, int(entry.group(1)))
                position = entry.end()
                while data[position:position + 1] in (b" ", b"\r", b"\n"):
                    position += 1
        trailer = re.compile(rb"\s*trailer\s*(<<.*?>>)", re.S).match(data, position)
        if not trailer:
            raise EbookNotConvertible("The PDF has no trailer")
        if b"/Encrypt" in trailer.group(1):
            raise EbookNotConvertible("The PDF is encrypted")
        if root is None:
            catalog = re.search(rb"/Root\s+(\d+)\s+\d+\s+R", trailer.group(1))
            root = int(catalog.group(1)) if catalog else None
        previous = re.search(rb"/Prev\s+(\d+)", trailer.group(1))
        offset = int(previous.group(1)) if previous else 0
    if root is None:
        raise EbookNotConvertible("The PDF names no catalog")
    return offsets, root


def _pdf_object(data: bytes, offsets: dict[int, int], number: int) -> tuple[bytes, bytes | None]:
    """An object's dictionary text and, when it is a stream, its raw bytes."""
    start = offsets.get(number)
    head = _PDF_OBJECT_HEAD.match(data, start) if start is not None else None
    if not head or int(head.group(1)) != number:
        raise EbookNotConvertible("The PDF points at an object that is not there")
    body = head.end()
    stream_at = data.find(b"stream", body)
    end_at = data.find(b"endobj", body)
    if stream_at < 0 or 0 <= end_at < stream_at:
        return data[body:end_at if end_at >= 0 else None], None
    dictionary = data[body:stream_at]
    begin = stream_at + len(b"stream")
    if data[begin:begin + 2] == b"\r\n":
        begin += 2
    elif data[begin:begin + 1] in (b"\n", b"\r"):
        begin += 1
    length = re.search(rb"/Length\s+(\d+)(?:\s+(\d+)\s+R)?", dictionary)
    if not length:
        raise EbookNotConvertible("A PDF stream does not say how long it is")
    if length.group(2) is not None:
        size = int(_pdf_object(data, offsets, int(length.group(1)))[0].strip() or 0)
    else:
        size = int(length.group(1))
    return dictionary, data[begin:begin + size]


def _pdf_entry(data: bytes, offsets: dict[int, int], text: bytes, key: bytes) -> bytes | None:
    """The value of `key` in a dictionary: an inline << >>, or the object it refers to."""
    found = re.search(re.escape(key) + rb"(?![A-Za-z])\s*", text)
    if not found:
        return None
    position = found.end()
    if text.startswith(b"<<", position):
        depth, index = 0, position
        while index < len(text):
            if text.startswith(b"<<", index):
                depth, index = depth + 1, index + 2
            elif text.startswith(b">>", index):
                depth, index = depth - 1, index + 2
                if depth == 0:
                    return text[position:index]
            else:
                index += 1
        return None
    reference = _PDF_REFERENCE.match(text, position)
    return _pdf_object(data, offsets, int(reference.group(1)))[0] if reference else None


def _pdf_drawn_names(data: bytes, offsets: dict[int, int], page: bytes) -> set[bytes]:
    """The XObject names a page's content stream actually draws ("/Im7 Do")."""
    import zlib

    contents = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", page)
    drawn: set[bytes] = set()
    for ref in _PDF_REFERENCE.findall(contents.group(1) if contents else b""):
        dictionary, stream = _pdf_object(data, offsets, int(ref))
        body = stream or b""
        if re.search(rb"/FlateDecode", dictionary):
            try:
                body = zlib.decompress(body)
            except zlib.error as exc:
                raise EbookNotConvertible("A PDF page's drawing could not be read") from exc
        drawn.update(re.findall(rb"/([^\s/<>\[\]()]+)\s+Do\b", body))
    return drawn


def _pdf_page_images(path: Path) -> list[bytes]:
    """Each page's one JPEG, in reading order."""
    data = path.read_bytes()
    offsets, root = _pdf_cross_reference(data)
    catalog = _pdf_object(data, offsets, root)[0]
    pages_root = re.search(rb"/Pages\s+(\d+)\s+\d+\s+R", catalog)
    if not pages_root:
        raise EbookNotConvertible("The PDF has no pages")
    images: list[bytes] = []
    page_count = 0

    def walk(number: int, inherited: bytes | None, depth: int) -> None:
        nonlocal page_count
        if depth > 32:
            raise EbookNotConvertible("The PDF page tree does not end")
        node = _pdf_object(data, offsets, number)[0]
        resources = _pdf_entry(data, offsets, node, b"/Resources") or inherited
        if re.search(rb"/Type\s*/Pages(?![A-Za-z])", node):
            kids = re.search(rb"/Kids\s*\[([^\]]*)\]", node)
            for kid in _PDF_REFERENCE.findall(kids.group(1) if kids else b""):
                walk(int(kid), resources, depth + 1)
            return
        page_count += 1
        xobjects = _pdf_entry(data, offsets, resources or b"", b"/XObject") or b""
        named = {
            name: int(ref)
            for name, ref in re.findall(rb"/([^\s/<>\[\]()]+)\s+(\d+)\s+\d+\s+R", xobjects)
        }
        if not named:
            return  # a blank page
        if len(named) > 1:
            # Resources are often shared: Viz's PDF lists all 192 images once,
            # at the top of the page tree, and each page draws one of them.
            # What a page draws is what counts.
            drawn = _pdf_drawn_names(data, offsets, node)
            named = {name: ref for name, ref in named.items() if name in drawn}
        refs = list(named.values())
        if len(refs) != 1:
            raise EbookNotConvertible("A PDF page is built from several pieces")
        dictionary, stream = _pdf_object(data, offsets, refs[0])
        filters = re.search(rb"/Filter\s*(\[[^\]]*\]|/\w+)", dictionary)
        if (not re.search(rb"/Subtype\s*/Image", dictionary) or not filters
                or re.findall(rb"/(\w+)", filters.group(1)) != [b"DCTDecode"]
                or not stream or not stream.startswith(b"\xff\xd8")):
            raise EbookNotConvertible("A PDF page is not a single JPEG")
        images.append(stream)

    walk(int(pages_root.group(1)), None, 0)
    return _whole_book(images, page_count)


def _epub_page_images(path: Path) -> list[bytes]:
    """Each page's one image, in the order the book is read."""
    import posixpath
    import zipfile

    with zipfile.ZipFile(path) as book:
        container = book.read("META-INF/container.xml").decode("utf-8", "ignore")
        package = re.search(r'full-path\s*=\s*"([^"]+)"', container)
        if not package:
            raise EbookNotConvertible("The EPUB names no package")
        opf_path = package.group(1)
        opf = book.read(opf_path).decode("utf-8", "ignore")
        base = posixpath.dirname(opf_path)
        manifest: dict[str, tuple[str, str]] = {}
        for item in re.finditer(r"<item\b[^>]*>", opf):
            attributes = dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', item.group(0)))
            if attributes.get("id") and attributes.get("href"):
                manifest[attributes["id"]] = (attributes["href"], attributes.get("media-type", ""))
        images: list[bytes] = []
        page_count = text_pages = 0
        for idref in re.findall(r'<itemref\b[^>]*\bidref\s*=\s*"([^"]+)"', opf):
            href, media = manifest.get(idref, ("", ""))
            if not href:
                continue
            member = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(href)))
            page_count += 1
            if media.startswith("image/"):
                images.append(book.read(member))
                continue
            page = book.read(member).decode("utf-8", "ignore")
            sources = re.findall(
                r'<(?:img\b[^>]*?\bsrc|image\b[^>]*?\b(?:xlink:)?href)\s*=\s*["\']([^"\']+)', page
            )
            words = re.sub(r"\s+", "", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<head>.*?</head>", " ", page)))
            if not sources:
                text_pages += len(words) > 40
                continue
            if len(sources) != 1:
                raise EbookNotConvertible("An EPUB page is built from several pictures")
            image = posixpath.normpath(posixpath.join(
                posixpath.dirname(member), urllib.parse.unquote(sources[0])
            ))
            images.append(book.read(image))
    # A title page or a credits page of text is ordinary; a book of text is not
    # manga pages at all.
    if text_pages > 2:
        raise EbookNotConvertible("The EPUB is text, not page images")
    return _whole_book(images, page_count)


def _whole_book(images: list[bytes], page_count: int) -> list[bytes]:
    """The images, if they are the book -- not a cover and some text."""
    if not images or len(images) < 0.9 * page_count:
        raise EbookNotConvertible("The ebook is not one image per page")
    for image in images:
        if not (image.startswith(b"\xff\xd8") or image.startswith(b"\x89PNG")):
            raise EbookNotConvertible("An ebook page is not a JPEG or PNG")
    return images


# How colourful a page has to be to read as a cover, and how grey to read as
# an interior page, in mean HSV saturation (0-255).
_COVER_SATURATION = 40
_INTERIOR_SATURATION = 12


def _page_saturation(image: bytes) -> float:
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(image)) as picture:
        picture.draft("RGB", (80, 120))
        small = picture.convert("RGB").resize((48, 72))
        return ImageStat.Stat(small.convert("HSV")).mean[1]


def _pages_run_backwards(pages: list[bytes]) -> bool:
    """Whether a publisher PDF stores a right-to-left book back to front.

    Viz's Chainsaw Man PDF opens on the last page of the story and ends on the
    colour front cover, and declares nothing -- no reading direction, no page
    labels. So the pages say it: a manga's interior is black and white and its
    cover is not. Only a colour last page after a grey first one reverses the
    book; anything less certain keeps the order it came in.
    """
    if len(pages) < 4:
        return False
    try:
        first, last = _page_saturation(pages[0]), _page_saturation(pages[-1])
    except Exception:  # noqa: BLE001 -- an unreadable page is no evidence either way
        return False
    return last >= _COVER_SATURATION and first <= _INTERIOR_SATURATION


def convert_ebook_to_cbz(source: Path, destination: Path) -> int:
    """Copy an image-per-page PDF or EPUB into a CBZ, page for page. Returns pages."""
    import zipfile

    kind = source.suffix.lower()
    try:
        pages = _pdf_page_images(source) if kind == ".pdf" else _epub_page_images(source)
    except EbookNotConvertible:
        raise
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise EbookNotConvertible(f"The {kind.lstrip('.').upper()} could not be read") from exc
    # An EPUB's spine is its reading order by definition; a PDF's page order
    # is only the order the pages were saved in.
    if kind == ".pdf" and _pages_run_backwards(pages):
        pages = list(reversed(pages))
    width = max(3, len(str(len(pages))))
    # Stored, not deflated: JPEG does not compress, and a reader opens it faster.
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as archive:
        for index, image in enumerate(pages, 1):
            suffix = ".png" if image.startswith(b"\x89PNG") else ".jpg"
            archive.writestr(f"{index:0{width}d}{suffix}", image)
    return len(pages)


def _manga_release_score(release: dict[str, Any], context: dict[str, Any]) -> tuple[int, list[str]]:
    """How well a release is one English volume of this manga.

    The comic rules do not transfer. There a volume is a collected edition, so
    "Saga v01" must never answer Saga #1; here the volume is the book. What a
    manga release has to prove instead: one volume, the right one, of this
    series and not a special beside it, in a format the library can read.
    """
    name = _manga_release_name(release.get("title"))
    if _MANGA_REFUSED.search(name):
        return 0, ["Not a comic-archive volume of this book"]
    # Searching TV/Anime brings the anime back too, numbered the same way:
    # "Blue.Lock.Vol.2.2022.ANiME.DUAL.COMPLETE.BLURAY" scored as volume 2.
    if _MANGA_VIDEO.search(name):
        return 0, ["A video release, not a book"]
    # "Chainsaw Man - 13 (cbz)", 974 MB, is thirteen chapters' worth of
    # something, not volume 13; "v01-v11" is eleven volumes in one file.
    if _MANGA_RANGE.search(name) or _MANGA_CHAPTER.search(name):
        return 0, ["A pack, not one volume"]
    volumes = _manga_volumes(name)
    wanted = str(context.get("issueNumber") or "").strip()
    marker = _MANGA_VOLUME.search(name)
    if len(volumes) != 1 or not wanted.isdigit() or not marker:
        return 0, ["No single volume number"]
    score = 0
    reasons: list[str] = []
    lead = name[: marker.start()]
    bare_wanted = normalized_title(_without_leading_article(context.get("seriesTitle")))
    # Equality, as for comics: "One Piece - Heroines v01" is a spin-off, and
    # a title that merely contains the series is how specials got through.
    leads = {lead, _strip_manga_publisher(lead, context.get("publisher"))}
    if bare_wanted and any(
        normalized_title(_without_leading_article(re.sub(r"[-–—:]+", " ", form))) == bare_wanted
        for form in leads
    ):
        score += 50
        reasons.append("Series title matches")
    if volumes == {int(wanted)}:
        score += 35
        reasons.append(f"Volume {int(wanted)} matches")
    year = str(context.get("publicationYear") or context.get("seriesYear") or "").strip()
    if year and year in name:
        score += 10
        reasons.append(f"Publication year {year} matches")
    category_ids = {
        str(category.get("id") if isinstance(category, dict) else category)
        for category in (release.get("categories") or [])
    }
    if category_ids & set(MANGA_CATEGORIES):
        score += 5
        reasons.append("Listed as a comic or book")
    score = min(score, 100)
    # Publisher ebook posts -- Viz's "HYBRID.MANGA.eBook" is an image-only
    # PDF -- are converted to CBZ on import. A release that is already a comic
    # archive needs no conversion and cannot fail one, so it goes first; the
    # ebook stays eligible for when nothing else is posted, which for early
    # volumes is often the case.
    if re.search(r"\b(?:ebook|hybrid|epub|pdf)\b", name, re.I) and not re.search(r"\bcb[zr]\b", name, re.I):
        score -= 10
        reasons.append("Posted as an ebook, which may not be a comic archive")
    return score, reasons


def _prowlarr_query_forms(context: dict[str, Any]) -> list[str]:
    """The queries to try for one wanted issue, widest match last.

    An indexer matches the words it is given, so a padded number is a
    different word from an unpadded one: "If Destruction Be Our Lot 002"
    returned nothing while "If Destruction Be Our Lot 2" returned the issue
    and "If Destruction Be Our Lot" returned the run. Padding alone therefore
    made some series permanently unfindable. Scoring still decides which
    result is the right issue, so a wider query costs recall nothing.
    """
    series = str(context.get("seriesTitle") or "").strip()
    if not series:
        return []
    # A release is usually named without the article the catalog keeps:
    # "The Department of Truth 004" found nothing, "Department of Truth 004"
    # found the issue twice over. The catalog title is still tried first.
    titles = [series]
    without_article = _without_leading_article(series)
    if without_article and without_article != series:
        titles.append(without_article)
    issue = str(context.get("issueNumber") or "").strip()
    forms = []
    for title in titles:
        if context.get("format") == "manga" and issue.isdigit():
            # Manga is posted by volume -- "Chainsaw Man v18", "Vol.18",
            # "One Piece v098" -- and an indexer matches whole words, so
            # "Chainsaw Man 018" finds none of them.
            number = int(issue)
            forms.append(f"{title} v{number:02d}")
            forms.append(f"{title} Vol {number:02d}")
            if number < 100:
                forms.append(f"{title} v{number:03d}")
        elif issue.isdigit():
            forms.append(f"{title} {issue.zfill(3)}")
            if issue.lstrip("0") != issue.zfill(3):
                forms.append(f"{title} {issue.lstrip('0') or '0'}")
        elif issue:
            forms.append(f"{title} {issue}")
    forms.extend(titles)
    seen: set[str] = set()
    return [form for form in forms if not (form in seen or seen.add(form))]


# A search the indexer never finishes answering must not hold up the issues
# after it. The socket timeout bounds each read, not the whole exchange: the
# first automatic pass over Ultimate Spider-Man sat on #050 for seven minutes,
# Prowlarr having logged the request at once, and every issue after it waited.
PROWLARR_SEARCH_DEADLINE_SECONDS = 90.0


def _with_deadline(work: Any, seconds: float, what: str) -> Any:
    """Run `work`, giving up on it after `seconds` of wall-clock time.

    A thread rather than a socket option, because the stall is not always in a
    read the socket can time out. An abandoned call is left to finish or fail
    on its own, and whatever it returns is discarded.
    """
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = work()
        except BaseException as exc:  # noqa: BLE001 -- re-raised by the caller
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="flipparr-deadline", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError(f"{what} did not answer within {int(seconds)} seconds")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _prowlarr_search(
    prowlarr: dict[str, Any], query: str, categories: tuple[str, ...] = ("7030",)
) -> list[dict[str, Any]]:
    endpoint = f"{prowlarr['url']}/api/v1/search?" + urllib.parse.urlencode(
        [("query", query), ("type", "search"),
         *(("categories", category) for category in categories), ("limit", 100)]
    )
    payload = _with_deadline(
        lambda: fetch_json_with_headers(
            endpoint,
            {"Accept": "application/json", "X-Api-Key": str(prowlarr["apiKey"]),
             "User-Agent": f"Flipparr/{APP_VERSION}"},
            timeout=45.0,
        ),
        PROWLARR_SEARCH_DEADLINE_SECONDS, "Prowlarr",
    )
    return payload if isinstance(payload, list) else []


def search_prowlarr_releases(job_id: int, query: str | None = None) -> dict[str, Any]:
    """Search one wanted issue and return credential-free release summaries.

    `query` replaces the generated one, so a person who can see that nothing
    was found can word the search themselves.
    """
    store = catalog_store()
    context = store.get_acquisition_job_context(job_id)
    prowlarr = _enabled_acquisition_service("prowlarr")
    asked = str(query or "").strip()
    forms = [asked] if asked else _prowlarr_query_forms(context)
    if not forms:
        raise ValueError("This issue has no series title to search for")
    query = forms[0]
    context.setdefault("preferredLanguage", preferred_language())
    store.update_acquisition_job(job_id, "searching", f"Searching Prowlarr for {query}")
    rejected_records = store.rejected_acquisition_releases(job_id)
    if not isinstance(rejected_records, (list, tuple)):
        rejected_records = []
    rejected_keys = {
        str(item.get("release_key") or "")
        for item in rejected_records if isinstance(item, dict)
    }
    rejected_titles = {
        re.sub(r"\s+", " ", str(item.get("release_title") or "")).strip().casefold()
        for item in rejected_records if isinstance(item, dict)
    }
    now = time.time()

    def candidates_for(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for release in releases:
            if not isinstance(release, dict):
                continue
            protocol = str(release.get("protocol") or "").casefold()
            title = str(release.get("title") or "").strip()
            download_url = str(release.get("downloadUrl") or "").strip()
            if protocol != "usenet" or not title or not download_url:
                continue
            try:
                download_path = _prowlarr_download_reference(download_url)
            except ValueError:
                continue
            release_key = _release_candidate_key(release, download_path)
            normalized_title = re.sub(r"\s+", " ", title).strip().casefold()
            if release_key in rejected_keys or normalized_title in rejected_titles:
                continue
            score, reasons = _release_candidate_score(release, context)
            if score < 85:
                continue
            seed = f"{job_id}:{release.get('guid')}:{title}:{now}"
            candidate_id = hashlib.sha256(seed.encode()).hexdigest()[:24]
            with _RELEASE_CANDIDATE_LOCK:
                _RELEASE_CANDIDATES[candidate_id] = {
                    "jobId": int(job_id),
                    "downloadPath": download_path,
                    "title": title,
                    "releaseKey": release_key,
                    "expiresAt": now + _RELEASE_CANDIDATE_TTL_SECONDS,
                }
            candidates.append({
                "id": candidate_id, "title": title,
                "indexer": str(release.get("indexer") or "Unknown indexer"),
                "sizeBytes": int(release.get("size") or 0),
                "publishDate": release.get("publishDate"), "protocol": "Usenet",
                "matchScore": score, "matchReasons": reasons,
                "matchStrength": "Strong match" if score >= 85 else "Possible match",
                "formatTags": _release_format_tags(title),
            })
        return candidates

    # The lock guards the candidate table, not the indexer. Held across the
    # searches, one slow answer stalled every other search in the process --
    # "Find release" included -- for as long as it took.
    with _RELEASE_CANDIDATE_LOCK:
        for candidate_id, cached in list(_RELEASE_CANDIDATES.items()):
            if float(cached.get("expiresAt") or 0) <= now:
                _RELEASE_CANDIDATES.pop(candidate_id, None)
    # A form that returns results but none of this issue is not an answer.
    # Stopping at the first non-empty search left one issue looking for a
    # release among forty-seven of its siblings, when the next form down
    # had the one it wanted.
    candidates = []
    try:
        for form in forms:
            query = form
            candidates = candidates_for(
                _prowlarr_search(prowlarr, form, categories=MANGA_CATEGORIES)
                if context.get("format") == "manga" else _prowlarr_search(prowlarr, form)
            )
            if candidates:
                break
    except Exception as exc:
        # A job left at "searching" is invisible to every later pass, which
        # only picks up queued work, so a search that failed once was never
        # tried again. Put it back where the sweep will find it.
        store.update_acquisition_job(
            job_id, "queued", f"Search did not finish ({exc}); it will be tried again"
        )
        raise
    candidates.sort(key=lambda item: (-item["matchScore"], -item["sizeBytes"], item["title"]))
    candidates = candidates[:24]
    # Nothing found is a finding. Reporting it as "0 release candidates found"
    # and then letting reconcile stamp the boilerplate back over it left an
    # issue reading "available to search" after five searches that came up
    # empty -- with nothing on the row to say it had ever looked.
    detail = (
        f"{len(candidates)} release candidate{'s' if len(candidates) != 1 else ''} found"
        if candidates else "No release found yet"
    )
    store.update_acquisition_job(job_id, "queued", detail)
    return {
        "job": context, "query": query, "candidateCount": len(candidates),
        "candidates": candidates,
    }


def send_release_to_sabnzbd(job_id: int, candidate_id: str) -> dict[str, Any]:
    """Fetch a selected NZB privately, validate it, and upload the file to SABnzbd."""
    now = time.time()
    with _RELEASE_CANDIDATE_LOCK:
        candidate = _RELEASE_CANDIDATES.get(candidate_id)
        if (not candidate or int(candidate.get("jobId") or 0) != int(job_id)
                or float(candidate.get("expiresAt") or 0) <= now):
            _RELEASE_CANDIDATES.pop(candidate_id, None)
            raise ValueError("This release result expired; search again")
    nzb_payload = _fetch_selected_nzb(candidate)
    sab = _enabled_acquisition_service("sabnzbd")
    payload = _submit_nzb_to_sabnzbd(sab, str(candidate["title"]), nzb_payload)
    queue_ids = [str(value) for value in (payload.get("nzo_ids") or [])]
    if not queue_ids:
        raise SABSubmissionError("SABnzbd accepted the NZB but did not return a queue ID")
    detail = f"Sent to SABnzbd: {candidate['title']}"
    store = catalog_store()
    release_key = str(candidate.get("releaseKey") or "").strip()
    if not release_key:
        release_key = hashlib.sha256(
            f"candidate|{candidate.get('title')}|{candidate.get('downloadPath')}".encode()
        ).hexdigest()
    store.record_acquisition_download(
        job_id, queue_ids[0], candidate["title"], release_key
    )
    job = store.update_acquisition_job(job_id, "grabbed", detail)
    with _RELEASE_CANDIDATE_LOCK:
        _RELEASE_CANDIDATES.pop(candidate_id, None)
    return {"status": "grabbed", "job": job, "queueIds": queue_ids,
            "detail": "Release sent to SABnzbd."}


def _safe_path_component(value: str, fallback: str = "Comic") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or fallback)[:180].rstrip(" .")


def _issue_key(value: Any) -> str:
    text = str(value or "").strip().casefold().lstrip("#")
    if text.isdigit():
        return str(int(text))
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _comic_files_under(source: Path, completed_root: Path) -> list[Path]:
    resolved_root = completed_root.resolve()
    resolved_source = source.resolve()
    try:
        resolved_source.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("SABnzbd reported a download outside the mounted comics category") from exc
    paths = [resolved_source] if resolved_source.is_file() else list(resolved_source.rglob("*"))
    return sorted(
        path for path in paths
        if path.is_file() and not path.is_symlink()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not path.name.startswith("._")
    )


class CompletedDownloadNotVisible(ValueError):
    """SAB is complete, but its finished files have not appeared in our mount yet."""


def _resolve_sab_download_source(
    storage: str, release_title: str, completed_root: Path = SAB_COMPLETE_ROOT
) -> Path:
    root = completed_root.resolve()
    if not root.is_dir():
        raise ValueError("SABnzbd's completed comics folder is not mounted")
    storage_path = Path(str(storage or "").rstrip("/\\"))
    storage_name = storage_path.name
    candidates: list[Path] = []
    # SAB commonly reports its own host path. Rebuild the portion beneath the
    # configured category directory inside Flipparr's completed-download mount.
    storage_parts = storage_path.parts
    category_indexes = [
        index for index, part in enumerate(storage_parts)
        if part.casefold() == root.name.casefold()
    ]
    if category_indexes:
        relative_parts = storage_parts[category_indexes[-1] + 1:]
        if relative_parts:
            candidates.append(root.joinpath(*relative_parts))
    if storage_name:
        candidates.append(root / storage_name)
    release_value = str(release_title or "").strip()
    release_name = release_value
    for extension in SUPPORTED_EXTENSIONS:
        if release_name.casefold().endswith(extension):
            release_name = release_name[:-len(extension)]
            break
    release_name = _safe_path_component(release_name, "")
    if release_name:
        candidates.append(root / release_name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    wanted = {normalized_title(storage_name), normalized_title(release_name)} - {""}
    for candidate in root.iterdir():
        if normalized_title(candidate.name) in wanted:
            return candidate
    raise CompletedDownloadNotVisible(
        "The completed SABnzbd download is not visible in the mounted comics folder"
    )


def _download_candidate_score(path: Path, context: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    parsed = parse_filename(path)
    result = inventory_file(parsed)
    health = result.get("file_health") or {}
    if health.get("status") == "error":
        return -1000, {"path": path, "result": result, "reason": health.get("message")}
    lookup = result.get("lookup_identity") or {}
    embedded = result.get("embedded_metadata") or {}
    candidate_issue = lookup.get("issue") or embedded.get("number")
    manga = context.get("format") == "manga"
    if manga and not candidate_issue:
        # A manga file names its volume, "Chainsaw Man v18", and the parser
        # rightly reads no issue there -- for a comic that is a collected
        # edition. For a manga run the volume is the book it was grabbed for.
        candidate_issue = (
            _manga_file_volume(path.name) or embedded.get("volume")
            or _manga_file_volume(path.parent.name)
        )
    expected_issue = context.get("issueNumber")
    issue_match = bool(candidate_issue) and _issue_key(candidate_issue) == _issue_key(expected_issue)
    candidate_title = lookup.get("title") or embedded.get("series") or parsed.title
    if manga:
        # The series is what comes before the volume, as in the release name:
        # "VIZ Media Chainsaw Man Vol 01 2020 HYBRiD MANGA eBook-PNLS" is
        # Chainsaw Man, and the tags after it only drag the likeness down.
        # Preferred even over the title the library read from the name, which
        # keeps those tags: "VIZ Media Chainsaw Man HYBRiD MANGA eBook-PNLS"
        # scored 0.52 against "Chainsaw Man" and refused the right volume.
        name = _manga_release_name(path.stem)
        marker = _MANGA_VOLUME.search(name)
        if marker and not embedded.get("series"):
            candidate_title = name[: marker.start()]
        else:
            candidate_title = _MANGA_VOLUME.sub(" ", str(candidate_title or ""))
    expected_title = context.get("seriesTitle") or ""
    # A scene name leads with the publisher and marks the number with "No",
    # so the same trimming the release scorer does is needed again here --
    # otherwise a file that was grabbed as the right issue is refused as
    # unrecognisable the moment it finishes downloading.
    wanted = normalized_title(expected_title)
    bare_wanted = normalized_title(_without_leading_article(expected_title))
    title_ratio = max(
        difflib.SequenceMatcher(None, normalized_title(form), compare).ratio()
        for form, compare in (
            (candidate_title, wanted),
            (_without_leading_article(candidate_title), bare_wanted),
            (_release_series_trim(candidate_title, context.get("publisher")), bare_wanted),
            (_strip_manga_publisher(candidate_title, context.get("publisher")), bare_wanted),
        )
    )
    score = (140 if issue_match else 0) + round(title_ratio * 70)
    return score, {
        "path": path, "result": result, "issueMatch": issue_match,
        "titleRatio": title_ratio, "candidateIssue": candidate_issue,
    }


class DownloadContentMismatch(ValueError):
    """A finished download did not contain the comic it was grabbed for.

    Distinct from the other ways an import fails -- a disk error, a rollback
    that needs attention -- because it says something about the release rather
    than about this machine: it is the wrong comic, and asking for it again
    will fail the same way.
    """


def select_downloaded_comic(
    source: Path, context: dict[str, Any], completed_root: Path = SAB_COMPLETE_ROOT
) -> dict[str, Any]:
    candidates = _comic_files_under(source, completed_root)
    if not candidates:
        raise DownloadContentMismatch(
            "The completed download does not contain a supported comic file"
        )
    converted_dir: Path | None = None
    if context.get("format") == "manga":
        archives = [path for path in candidates if path.suffix.lower() in MANGA_FILE_TYPES]
        if not archives:
            ebooks = [path for path in candidates if path.suffix.lower() in MANGA_EBOOK_TYPES]
            if not ebooks:
                raise DownloadContentMismatch("The download has no CBZ, CBR, PDF or EPUB volume")
            converted_dir = Path(tempfile.mkdtemp(prefix="flipparr-manga-"))
            for ebook in ebooks:
                # Named for the volume it states; a publisher's file is often
                # named for nothing, and the release folder says which it is.
                label = ebook.stem if _manga_file_volume(ebook.name) else Path(source).name
                target = converted_dir / f"{_safe_path_component(label, 'volume')}.cbz"
                try:
                    convert_ebook_to_cbz(ebook, target)
                except EbookNotConvertible as exc:
                    shutil.rmtree(converted_dir, ignore_errors=True)
                    # Refused as the wrong release, so the next one is tried.
                    raise DownloadContentMismatch(
                        f"{exc}, so this {ebook.suffix.lstrip('.').upper()} cannot become a CBZ volume"
                    ) from exc
                archives.append(target)
        candidates = archives
    try:
        selected = _choose_downloaded_comic(candidates, source, context)
    except Exception:
        if converted_dir is not None:
            shutil.rmtree(converted_dir, ignore_errors=True)
        raise
    if converted_dir is not None:
        selected = {**selected, "converted": True, "convertedDir": str(converted_dir)}
    return selected


def _choose_downloaded_comic(
    candidates: list[Path], source: Path, context: dict[str, Any]
) -> dict[str, Any]:
    ranked = sorted(
        (_download_candidate_score(path, context) for path in candidates),
        key=lambda item: (-item[0], str(item[1]["path"])),
    )
    score, selected = ranked[0]
    if score < 180 or not selected.get("issueMatch") or selected.get("titleRatio", 0) < 0.55:
        raise DownloadContentMismatch(
            f"No downloaded comic confidently matched {context.get('seriesTitle')} "
            f"{_number_label(context)}"
        )
    # Said in the file or in the folder it arrived in. Refusing here is what
    # makes a foreign edition visible: filed silently, it looks like the issue
    # it claims to be and is only found by opening it.
    wanted = context.get("preferredLanguage")
    conflict = (
        release_language_conflicts(Path(selected["path"]).name, wanted)
        or release_language_conflicts(Path(source).name, wanted)
    )
    if conflict:
        raise DownloadContentMismatch(
            f"The download is labelled as {language_name(conflict)}, "
            f"not {language_name(wanted if wanted is not None else preferred_language())}"
        )
    return selected


def _series_destination_directory(context: dict[str, Any], library_root: Path) -> Path:
    root = library_root.resolve()
    existing = Path(str(context.get("existingDirectory") or ""))
    if str(existing):
        try:
            resolved = existing.resolve()
            resolved.relative_to(root)
            if resolved != root:
                return resolved
        except (OSError, ValueError):
            pass
    title = _safe_path_component(str(context.get("seriesTitle") or "Unknown series"))
    year = context.get("seriesYear")
    series_folder = title if not year or re.search(rf"\({re.escape(str(year))}\)$", title) else f"{title} ({year})"
    publisher = _safe_path_component(str(context.get("publisher") or ""), "")
    # Manga has a folder of its own, so comics and manga browse apart.
    base = root / "Manga" if context.get("format") == "manga" else root
    return base / publisher / series_folder if publisher else base / series_folder


def _issue_destination(path: Path, context: dict[str, Any], library_root: Path) -> Path:
    title = _safe_path_component(str(context.get("seriesTitle") or "Unknown series"))
    year = context.get("seriesYear")
    series_label = title if not year or re.search(rf"\({re.escape(str(year))}\)$", title) else f"{title} ({year})"
    issue = str(context.get("issueNumber") or "").strip()
    issue_label = issue.zfill(3) if issue.isdigit() else _safe_path_component(issue, "Special")
    issue_title = _safe_path_component(str(context.get("issueTitle") or ""), "")
    generic_title = normalized_title(issue_title) in {
        "", normalized_title(f"Issue {issue}"), normalized_title(f"Volume {issue}"),
        normalized_title(f"Vol. {issue}"),
    }
    # A manga volume is named the way manga is: "Chainsaw Man (2020) v18".
    manga = context.get("format") == "manga" and issue.isdigit()
    filename = f"{series_label} v{int(issue):02d}" if manga else f"{series_label} #{issue_label}"
    if issue_title and not generic_title:
        filename += f" - {issue_title}"
    return _series_destination_directory(context, library_root) / f"{filename}{path.suffix.lower()}"


def _catalog_imported_issue(destination: Path, context: dict[str, Any], library_root: Path) -> int:
    """Catalog an imported issue using the request's trusted canonical identity."""
    parsed = parse_filename(destination)
    result = inventory_file(parsed)
    return catalog_store().ingest_acquisition_import(
        str(library_root.resolve()),
        parsed,
        result,
        {
            "seriesTitle": context.get("seriesTitle"),
            "recordType": "issue",
            "issueNumber": context.get("issueNumber"),
            "publisher": context.get("publisher"),
            "publicationYear": context.get("publicationYear"),
        },
    )


def _replacement_quarantine_path(
    replacement_id: int, original_path: Path, library_root: Path
) -> Path:
    """Build a hidden, recoverable path without allowing the source to escape its root."""
    root = library_root.resolve()
    if original_path.is_symlink():
        raise ValueError("Flipparr will not quarantine a symbolic link")
    resolved_original = original_path.resolve()
    try:
        relative = resolved_original.relative_to(root)
    except ValueError as exc:
        raise ValueError("The replacement original is outside the configured library root") from exc
    target = root / MANAGED_LIBRARY_DIR / "quarantine" / str(replacement_id) / relative
    try:
        target.resolve(strict=False).relative_to(root / MANAGED_LIBRARY_DIR / "quarantine")
    except ValueError as exc:
        raise ValueError("The quarantine destination escaped its managed directory") from exc
    if target.exists():
        return target
    # An original set aside before the rename stays where it was put; moving it
    # would be this app relocating a file in the library, which it does not do.
    for legacy_dir in LEGACY_MANAGED_LIBRARY_DIRS:
        legacy = root / legacy_dir / "quarantine" / str(replacement_id) / relative
        try:
            legacy.resolve(strict=False).relative_to(root / legacy_dir / "quarantine")
        except ValueError:
            continue
        if legacy.exists():
            return legacy
    return target


def _move_original_to_quarantine(
    replacement: dict[str, Any], library_root: Path
) -> Path:
    original = Path(str(replacement["original_path"]))
    target = _replacement_quarantine_path(int(replacement["id"]), original, library_root)
    if not original.exists():
        if target.is_file():
            return target
        raise ValueError("The original comic is no longer available to quarantine")
    if target.exists():
        raise ValueError("A quarantined original already exists and needs review")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(original, target)
    return target


def _rollback_replacement_swap(
    store: CatalogStore, replacement_id: int, original_path: Path,
    quarantine_path: Path, destination: Path,
) -> None:
    """Restore the original while retaining the rejected new copy for inspection."""
    failed_new_path: Path | None = None
    if destination.is_file():
        failed_new_path = quarantine_path.with_name(
            f"{quarantine_path.stem}.failed-new-{int(time.time())}-{os.getpid()}"
            f"{destination.suffix.lower()}"
        )
        if failed_new_path.exists():
            raise ValueError("A failed replacement copy already exists and needs review")
        os.replace(destination, failed_new_path)
    if quarantine_path.is_file() and not original_path.exists():
        original_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(quarantine_path, original_path)
    store.restore_replacement_original(
        replacement_id, str(quarantine_path), str(original_path),
        str(failed_new_path) if failed_new_path else None,
    )


def import_downloaded_comic(
    download: dict[str, Any],
    *,
    library_root: Path = COMIC_LIBRARY_ROOT,
    completed_root: Path = SAB_COMPLETE_ROOT,
) -> dict[str, Any]:
    store = catalog_store()
    context = store.get_acquisition_job_context(int(download["job_id"]))
    source_container = _resolve_sab_download_source(
        str(download.get("sab_storage") or ""), str(download.get("release_title") or ""),
        completed_root,
    )
    selected = select_downloaded_comic(source_container, context, completed_root)
    try:
        return _import_selected_comic(selected, context, download, library_root)
    finally:
        # A manga ebook is converted into a temporary CBZ. By now the library
        # holds its copy, or the import failed and the copy is of no use.
        _discard_converted(selected)


def _discard_converted(selected: dict[str, Any] | None) -> None:
    folder = (selected or {}).get("convertedDir")
    if folder:
        shutil.rmtree(folder, ignore_errors=True)


def _import_selected_comic(
    selected: dict[str, Any], context: dict[str, Any], download: dict[str, Any],
    library_root: Path,
) -> dict[str, Any]:
    store = catalog_store()
    source = Path(selected["path"])
    root = library_root.resolve()
    if not root.is_dir():
        raise ValueError("The comics library is not mounted")
    destination = _issue_destination(source, context, root)
    try:
        destination.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("The generated comic destination escaped the library root") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_size = source.stat().st_size
    if shutil.disk_usage(destination.parent).free - source_size < IMPORT_MIN_FREE_BYTES:
        raise ValueError("Import would leave less than the configured minimum free disk space")
    source_hash = _sha256_file(source)
    replacement_id = int(context["replacementId"]) if context.get("replacementId") else None
    replacement_original = (
        Path(str(context["replacementFilePath"])) if context.get("replacementFilePath") else None
    )
    replacing_destination = bool(
        replacement_id and replacement_original
        and destination.resolve() == replacement_original.resolve()
    )
    if destination.exists():
        if replacing_destination:
            if destination.stat().st_size == source_size and _sha256_file(destination) == source_hash:
                raise ValueError("The selected release is identical to the comic being replaced")
        elif destination.is_file() and destination.stat().st_size == source_size and _sha256_file(destination) == source_hash:
            return {
                "source": str(source), "destination": str(destination),
                "size": source_size, "sha256": source_hash, "alreadyPresent": True,
            }
        else:
            raise ValueError("A different comic already exists at the organized destination")
    partial = destination.with_name(
        f".{destination.stem}.flipparr-{os.getpid()}-{threading.get_ident()}.partial"
        f"{destination.suffix.lower()}"
    )
    if partial.exists():
        raise ValueError("A previous partial import needs review before this comic can be copied")
    quarantine_path: Path | None = None
    try:
        with source.open("rb") as input_file, partial.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        if partial.stat().st_size != source_size or _sha256_file(partial) != source_hash:
            raise ValueError("The copied comic did not match the SABnzbd source")
        copied_health = inspect_file_health(partial)
        if copied_health.get("status") == "error":
            raise ValueError(copied_health.get("message") or "The copied comic failed validation")
        if replacing_destination and replacement_id and replacement_original:
            replacement = store.replacement_for_job(int(download["job_id"]))
            if not replacement:
                raise ValueError("The replacement request is no longer available")
            quarantine_path = _move_original_to_quarantine(replacement, root)
            store.record_replacement_quarantine(
                replacement_id, str(replacement_original), str(quarantine_path)
            )
        os.replace(partial, destination)
    except Exception:
        if partial.exists() and partial.is_file() and partial.parent == destination.parent:
            partial.unlink()
        if quarantine_path and replacement_id and replacement_original:
            _rollback_replacement_swap(
                store, replacement_id, replacement_original, quarantine_path, destination
            )
        raise
    return {
        "source": str(source), "destination": str(destination),
        "size": source_size, "sha256": source_hash, "alreadyPresent": False,
        "quarantineOriginal": str(quarantine_path) if quarantine_path else None,
        "replacementId": str(replacement_id) if replacement_id else None,
    }


def _sab_remove_job(download: dict[str, Any]) -> None:
    """Ask SABnzbd to forget a finished job and delete its files.

    Called once the library holds a verified copy, and when a release is
    refused as the wrong content. Left alone, every download stays in
    SABnzbd's completed folder: one manga run's failed attempts left nine
    copies of two PDFs there, 930 MB. Sonarr and Radarr remove completed
    downloads the same way. A failure costs disk space, not the import, so it
    is only logged.
    """
    nzo_id = str((download or {}).get("sab_nzo_id") or "").strip()
    if not nzo_id:
        return
    try:
        sab = _enabled_acquisition_service("sabnzbd")
        endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
            "mode": "history", "name": "delete", "value": nzo_id, "del_files": 1,
            "output": "json", "apikey": sab["apiKey"],
        })
        fetch_json_with_headers(
            endpoint, {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"},
            timeout=30.0,
        )
    except Exception as exc:
        log_exception("sab_cleanup_failed", exc, level="warning")


def _sab_history_slot(download: dict[str, Any]) -> dict[str, Any] | None:
    sab = _enabled_acquisition_service("sabnzbd")
    endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
        "mode": "history", "start": 0, "limit": 1,
        "nzo_ids": str(download["sab_nzo_id"]), "output": "json", "apikey": sab["apiKey"],
    })
    payload = fetch_json_with_headers(
        endpoint, {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"}, timeout=30.0
    )
    history = payload.get("history") if isinstance(payload, dict) else None
    slots = history.get("slots") if isinstance(history, dict) else []
    return next(
        (slot for slot in slots if str(slot.get("nzo_id")) == str(download["sab_nzo_id"])),
        None,
    )


def acquisition_download_progress() -> dict[str, Any]:
    """How far along whatever is downloading right now is.

    Asked of SABnzbd when someone is looking rather than stored on the
    download: the figure is only wanted while it is moving, it is stale the
    moment the poll that wrote it ends, and keeping it would put a column and
    a migration behind a number that SABnzbd already has.

    Never raises. A download client that cannot be reached means no progress
    to show, not a page that fails to load.
    """
    empty: dict[str, Any] = {"downloads": {}, "paused": False}
    try:
        store = catalog_store()
        active = [
            row for row in store.pending_acquisition_downloads()
            if str(row.get("status") or "") in {"queued", "downloading"}
        ]
    except Exception:
        return empty
    if not active:
        return empty
    try:
        sab = _enabled_acquisition_service("sabnzbd")
        endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
            "mode": "queue", "limit": 200, "output": "json", "apikey": sab["apiKey"],
        })
        payload = fetch_json_with_headers(
            endpoint,
            {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"},
            timeout=15.0,
        )
    except Exception:
        log_event("sab_queue_unavailable", level="warning")
        return empty
    queue = payload.get("queue") if isinstance(payload, dict) else None
    slots = {
        str(slot.get("nzo_id")): slot
        for slot in ((queue or {}).get("slots") or []) if isinstance(slot, dict)
    }
    downloads: dict[str, Any] = {}
    for row in active:
        slot = slots.get(str(row.get("sab_nzo_id") or ""))
        if not slot:
            continue
        try:
            percent = int(float(slot.get("percentage") or 0))
        except (TypeError, ValueError):
            percent = 0
        downloads[str(row["job_id"])] = {
            "percent": max(0, min(100, percent)),
            "sizeLeft": str(slot.get("sizeleft") or "").strip(),
            "timeLeft": str(slot.get("timeleft") or "").strip(),
            "state": str(slot.get("status") or "").strip().casefold(),
        }
    return {"downloads": downloads, "paused": bool((queue or {}).get("paused"))}


def _auto_grab_release(job_id: int) -> dict[str, Any] | None:
    """Send the strongest unused release for one job to SABnzbd.

    This is the automatic search, in the sense Radarr and Sonarr use the term:
    the scoring that ranks the candidate list picks from it, and the list
    itself is a separate deliberate action rather than the first step. Only
    releases already filtered to a strong match are eligible, so "nothing good
    enough" is a real outcome rather than a reason to grab something weak.

    Returns None when the choice genuinely needs a person -- nothing scored
    high enough, or a service could not answer -- and the job is left for the
    candidate list to handle.
    """
    try:
        search = search_prowlarr_releases(job_id)
    except Exception as exc:
        log_event(
            "retry_release_search_failed", level="warning",
            job_id=job_id, error=str(exc),
        )
        return None
    candidates = search.get("candidates") if isinstance(search, dict) else []
    if not isinstance(candidates, list) or not candidates:
        return None
    # The same release is usually posted on several indexers, and one of them
    # handing back something that is not an NZB says nothing about the others.
    # Once & Future #30 sat at "4 release candidates found": Indexer A's copy
    # was ranked first, its NZB was broken, and the three good copies were never
    # tried. The release itself is not marked unusable -- that goes by title,
    # and would take the good copies down with the broken one.
    problems: list[str] = []
    for candidate in candidates[:MAX_AUTOMATIC_RELEASE_FAILURES]:
        try:
            grabbed = send_release_to_sabnzbd(job_id, str(candidate["id"]))
        except ReleaseDownloadError as exc:
            problems.append(f"{candidate.get('indexer') or 'an indexer'}: {exc}")
            log_event(
                "retry_release_fetch_failed", level="warning",
                job_id=job_id, release=str(candidate.get("title") or ""),
                indexer=str(candidate.get("indexer") or ""), error=str(exc),
            )
            continue
        except Exception as exc:
            # SABnzbd itself refusing is not the release's fault, and the next
            # candidate would meet the same refusal.
            log_event(
                "retry_release_send_failed", level="warning",
                job_id=job_id, release=str(candidate.get("title") or ""), error=str(exc),
            )
            _say_on_the_row(job_id, f"Found a release but could not send it to SABnzbd: {exc}")
            return None
        return {**grabbed, "release": candidate}
    # Said on the row, so "4 release candidates found" no longer stands for a
    # grab that failed without a word.
    _say_on_the_row(
        job_id,
        f"Found {len(candidates)} release{'s' if len(candidates) != 1 else ''} but none "
        f"could be fetched ({problems[0]}); it will be tried again",
    )
    return None


def _say_on_the_row(job_id: int, reason: str) -> None:
    """Put why a grab stopped on the job's row -- a courtesy that must never
    turn "nothing was grabbed" into a crash, whatever happened to the job."""
    try:
        catalog_store().update_acquisition_job(job_id, "queued", reason)
    except Exception as exc:  # noqa: BLE001 -- the grab's answer stands either way
        log_exception("grab_reason_not_recorded", exc, level="warning")


def _automatic_release_grabs(request_id: int | None = None, *, backoff: bool = False) -> None:
    """Grab the best release for every job still waiting on one.

    `backoff` is for the scheduled sweep, which must not ask the indexer about
    the same unfindable issue every quarter of an hour. A person pressing
    "Search for missing" means now, and passes it off.

    With a request id this covers one newly created request. Without one it
    covers the whole backlog, which is the "search for missing" pass Radarr
    and Sonarr put behind a button on their Wanted list.

    Adding something monitored in Radarr or Sonarr triggers a search that grabs
    the best release clearing the quality bar; you only meet a list of releases
    when you deliberately open an interactive search. Flipparr had the opposite
    default -- every issue of every new request waited on a person to pick,
    including a collected edition mapping to twenty of them.

    Failures are per job. One issue with no usable release must not stop the
    rest, and it simply stays on the list where "Find release" still works.
    """
    try:
        store = catalog_store()
        # Align the jobs with what is actually on disk before acting on them.
        # A job's status records what was true when it was written; issues
        # acquired since are still marked queued until this runs, and grabbing
        # from that list re-downloads comics already in the library.
        store.reconcile_acquisition_jobs(
            None if request_id is None else int(request_id)
        )
        job_ids = store.acquisition_jobs_awaiting_release(
            None if request_id is None else int(request_id), backoff=backoff,
        )
    except Exception as exc:
        log_exception("automatic_grab_lookup_failed", exc, level="warning")
        return
    if not job_ids:
        return
    grabbed = 0
    for job_id in job_ids:
        # Another pass may be on this issue, or have finished it since this one
        # read its list. Searching it again sends a second copy to SABnzbd.
        with _JOBS_IN_FLIGHT_LOCK:
            if job_id in _JOBS_IN_FLIGHT:
                continue
            _JOBS_IN_FLIGHT.add(job_id)
        try:
            if job_id not in store.acquisition_jobs_awaiting_release(
                None if request_id is None else int(request_id)
            ):
                continue
            if _auto_grab_release(job_id):
                grabbed += 1
        except Exception as exc:
            log_exception("automatic_grab_failed", exc, level="warning")
        finally:
            with _JOBS_IN_FLIGHT_LOCK:
                _JOBS_IN_FLIGHT.discard(job_id)
    log_event(
        "automatic_release_grabs",
        request_id=None if request_id is None else int(request_id),
        jobs=len(job_ids), grabbed=grabbed, left_to_choose=len(job_ids) - grabbed,
    )


def _start_automatic_release_grabs(request: Any) -> None:
    """Run the automatic search off the request thread.

    Called beside every `create_acquisition_request`, not on one route.
    Following a run from Discover creates its request through a different path
    than following one already in the library, and hooking only the latter
    meant a series added from Discover sat with fifty queued issues and never
    looked for any of them.

    Twenty issues means twenty indexer searches and twenty uploads, which must
    not hold up the response that tells the page its request exists. Nothing
    downstream waits on the result: the jobs update themselves as they go and
    the page is already polling them.
    """
    try:
        request_id = int(str((request or {}).get("id") or "").strip() or 0)
    except (TypeError, ValueError):
        request_id = 0
    if not request_id:
        return
    # No indexer or no download client means every search would fail in a loop
    # and say nothing useful. Leave the jobs for the list.
    for service in ("prowlarr", "sabnzbd"):
        try:
            _enabled_acquisition_service(service)
        except Exception:
            log_event(
                "automatic_release_grabs_skipped", level="info",
                request_id=request_id, reason=f"{service} is not configured",
            )
            return
    threading.Thread(
        target=_automatic_release_grabs, args=(request_id,),
        name=f"flipparr-auto-grab-{request_id}", daemon=True,
    ).start()


def start_missing_release_search(confirmed: bool = False) -> dict[str, Any]:
    """Search for every wanted issue that still has no release.

    The equivalent of Radarr's and Sonarr's Wanted list: everything monitored
    and still missing, searched on demand. Requests created before automatic
    search existed, and issues nothing good enough was found for last time,
    both land here.

    Returns immediately with what it is about to work through -- the jobs
    report their own progress, and the page is already polling them.
    """
    store = catalog_store()
    # Same reason as the per-request pass: the count reported here has to be of
    # issues genuinely still missing, not of jobs left over from before they
    # were acquired.
    store.reconcile_acquisition_jobs()
    job_ids = store.acquisition_jobs_awaiting_release()
    if not job_ids:
        return {"status": "idle", "searching": 0, "detail": "Nothing is waiting on a release."}
    for service in ("prowlarr", "sabnzbd"):
        try:
            _enabled_acquisition_service(service)
        except Exception:
            name = ACQUISITION_SERVICE_DEFINITIONS[service]["name"]
            raise ValueError(
                f"{name} is not configured, so there is nothing to search with"
            ) from None
    plural = "" if len(job_ids) == 1 else "s"
    if not confirmed:
        # This spends real bandwidth on every issue at once, so the count is
        # reported and nothing starts until it is accepted.
        return {
            "status": "confirm", "searching": len(job_ids),
            "detail": f"This will search for {len(job_ids)} missing issue{plural} "
                      f"and download {'it' if len(job_ids) == 1 else 'them'}.",
        }
    threading.Thread(
        target=_automatic_release_grabs, name="flipparr-search-missing", daemon=True,
    ).start()
    return {
        "status": "searching", "searching": len(job_ids),
        "detail": f"Searching for {len(job_ids)} missing issue{plural}.",
    }


def _fallback_after_sab_failure(
    store: CatalogStore, download: dict[str, Any], message: str
) -> dict[str, Any]:
    """Try the next unused strong result after SAB proves a release unusable."""
    job_id = int(download["job_id"])
    release_key = str(download.get("release_key") or "").strip() or _legacy_release_key(download)
    release_title = str(download.get("release_title") or "Unknown release")
    failure = store.record_acquisition_release_failure(
        job_id, release_key, release_title, message
    )
    failure_count = int(failure.get("failureCount") or 0)
    if failure_count >= MAX_AUTOMATIC_RELEASE_FAILURES:
        detail = (
            f"{message}. Automatic fallback stopped after {failure_count} failed releases; "
            "choose another release or review the indexer results."
        )
        store.update_acquisition_job(job_id, "failed", detail)
        return {"status": "failed", "error": detail, "automaticFallback": False}

    try:
        search = search_prowlarr_releases(job_id)
    except Exception as exc:
        detail = f"{message}. Flipparr could not search for a fallback release: {exc}"
        store.update_acquisition_job(job_id, "failed", detail)
        return {"status": "failed", "error": detail, "automaticFallback": False}
    candidates = search.get("candidates") if isinstance(search, dict) else []
    if not isinstance(candidates, list) or not candidates:
        detail = f"{message}. No unused strong Prowlarr match remains."
        store.update_acquisition_job(job_id, "failed", detail)
        return {"status": "failed", "error": detail, "automaticFallback": False}

    candidate = candidates[0]
    try:
        grabbed = send_release_to_sabnzbd(job_id, str(candidate["id"]))
    except Exception as exc:
        detail = f"{message}. The next release could not be sent to SABnzbd: {exc}"
        store.update_acquisition_job(job_id, "failed", detail)
        return {"status": "failed", "error": detail, "automaticFallback": False}
    return {
        "status": "fallback_queued",
        "automaticFallback": True,
        "failedRelease": release_title,
        "fallbackRelease": candidate.get("title"),
        "failureCount": failure_count,
        "grab": grabbed,
    }


# How long a download may be missing from SABnzbd's queue and history before it
# counts as lost. A job just sent can take a moment to appear in either.
SAB_MISSING_GRACE_SECONDS = 600


def _sab_queue_slot(download: dict[str, Any]) -> dict[str, Any] | None:
    sab = _enabled_acquisition_service("sabnzbd")
    endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
        "mode": "queue", "nzo_ids": str(download["sab_nzo_id"]), "output": "json",
        "apikey": sab["apiKey"],
    })
    payload = fetch_json_with_headers(
        endpoint, {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"}, timeout=30.0
    )
    queue = payload.get("queue") if isinstance(payload, dict) else None
    slots = queue.get("slots") if isinstance(queue, dict) else []
    return next(
        (slot for slot in slots or [] if str(slot.get("nzo_id")) == str(download["sab_nzo_id"])),
        None,
    )


def _sab_lost_download(download: dict[str, Any]) -> bool:
    """Whether SABnzbd has forgotten a download: in neither its queue nor its history.

    Waiting on one it has forgotten waits forever. American Vampire #19 did:
    its files were deleted as the wrong comic, "Retry import" put it back to
    waiting on SABnzbd, and the Pull List said Downloading from then on.
    """
    try:
        sent = dt.datetime.fromisoformat(str(download.get("created_at") or ""))
    except ValueError:
        return False
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=dt.timezone.utc)
    if (dt.datetime.now(dt.timezone.utc) - sent).total_seconds() < SAB_MISSING_GRACE_SECONDS:
        return False
    return _sab_queue_slot(download) is None


def reconcile_acquisition_download(download: dict[str, Any]) -> dict[str, Any]:
    store = catalog_store()
    slot = _sab_history_slot(download)
    if not slot:
        if _sab_lost_download(download):
            message = "SABnzbd no longer has this download"
            store.update_acquisition_download(
                int(download["id"]), "failed", error=message, failure_stage="download",
            )
            return _fallback_after_sab_failure(store, download, message)
        store.update_acquisition_download(int(download["id"]), "downloading")
        return {"status": "downloading"}
    sab_status = str(slot.get("status") or "").casefold()
    storage = str(slot.get("storage") or "").strip()
    if sab_status == "failed":
        message = str(slot.get("fail_message") or "SABnzbd reported that the download failed")
        store.update_acquisition_download(
            int(download["id"]), "failed", sab_storage=storage,
            error=message, failure_stage="download",
        )
        return _fallback_after_sab_failure(store, download, message)
    if sab_status != "completed":
        store.update_acquisition_download(int(download["id"]), "downloading", sab_storage=storage or None)
        return {"status": "downloading", "sabStatus": slot.get("status")}
    store.update_acquisition_download(int(download["id"]), "completed", sab_storage=storage)
    imported: dict[str, Any] | None = None
    try:
        store.update_acquisition_download(int(download["id"]), "importing", sab_storage=storage)
        imported = import_downloaded_comic({**download, "sab_storage": storage})
        context = store.get_acquisition_job_context(int(download["job_id"]))
        imported["fileId"] = _catalog_imported_issue(
            Path(imported["destination"]), context, COMIC_LIBRARY_ROOT
        )
    except CompletedDownloadNotVisible as exc:
        message = str(exc)
        store.update_acquisition_download(
            int(download["id"]), "waiting_for_files", sab_storage=storage,
            error=message, failure_stage=None,
        )
        store.update_acquisition_job(
            int(download["job_id"]), "grabbed",
            "SABnzbd finished; waiting for the completed file to appear in Flipparr",
        )
        return {"status": "waiting_for_files", "detail": message}
    except Exception as exc:
        if imported and imported.get("quarantineOriginal") and imported.get("replacementId"):
            try:
                _rollback_replacement_swap(
                    store,
                    int(imported["replacementId"]),
                    Path(imported["destination"]),
                    Path(imported["quarantineOriginal"]),
                    Path(imported["destination"]),
                )
            except Exception as rollback_error:
                exc = ValueError(f"{exc}; original restore also needs attention: {rollback_error}")
        message = str(exc)
        # A wrong comic is its own stage, not an import to retry: its files
        # are deleted from SABnzbd below, so "Retry import" had nothing left to
        # import and waited on a SABnzbd job that no longer existed.
        store.update_acquisition_download(
            int(download["id"]), "failed", sab_storage=storage, error=message,
            failure_stage="content" if isinstance(exc, DownloadContentMismatch) else "import",
        )
        if isinstance(exc, DownloadContentMismatch):
            # The wrong comic is of no use to keep, and it is recorded as
            # unusable, so it will not be grabbed again.
            _sab_remove_job(download)
            # The release is the problem, not the machine, so the same thing
            # happens as when SABnzbd proves one unusable: record it and take
            # the next candidate. Without this the issue sat failed until
            # somebody noticed and asked for another search by hand.
            try:
                outcome = _fallback_after_sab_failure(store, download, message)
            except Exception as fallback_error:
                log_exception("import_fallback_failed", fallback_error, level="warning")
            else:
                return {"error": message, **outcome}
        store.update_acquisition_job(int(download["job_id"]), "failed", f"Import needs attention: {message}")
        return {"status": "failed", "error": message}
    store.update_acquisition_download(
        int(download["id"]), "imported", sab_storage=storage,
        local_source=imported["source"], destination=imported["destination"],
        source_size=imported["size"], source_sha256=imported["sha256"],
    )
    store.update_acquisition_job(
        int(download["job_id"]), "fulfilled",
        f"Imported and verified as {Path(imported['destination']).name}",
    )
    _sab_remove_job(download)
    replacement = store.replacement_for_job(int(download["job_id"]))
    if replacement and int(replacement.get("unfinished_jobs") or 0) == 0:
        try:
            quarantine_path = imported.get("quarantineOriginal") or replacement.get("quarantine_path")
            if not quarantine_path:
                quarantine = _move_original_to_quarantine(replacement, COMIC_LIBRARY_ROOT)
                quarantine_path = str(quarantine)
                store.record_replacement_quarantine(
                    int(replacement["id"]), replacement["original_path"], quarantine_path
                )
            store.complete_file_replacement(int(replacement["id"]), str(quarantine_path))
            imported["replacementCompleted"] = True
            imported["quarantineOriginal"] = str(quarantine_path)
        except Exception as exc:
            message = f"Replacement was imported, but the original could not be quarantined: {exc}"
            store.update_acquisition_download(
                int(download["id"]), "failed", sab_storage=storage,
                local_source=imported["source"], destination=imported["destination"],
                source_size=imported["size"], source_sha256=imported["sha256"],
                error=message, failure_stage="import",
            )
            store.update_acquisition_job(int(download["job_id"]), "failed", message)
            store.update_file_replacement_status(int(replacement["id"]), "failed", message)
            return {"status": "failed", "error": message, **imported}
    return {"status": "imported", **imported}


def acquisition_import_worker(stop_event: threading.Event = _IMPORT_STOP) -> None:
    while not stop_event.is_set():
        try:
            downloads = catalog_store().pending_acquisition_downloads()
            imported_any = False
            for download in downloads:
                if stop_event.is_set():
                    break
                try:
                    result = reconcile_acquisition_download(download)
                    imported_any = imported_any or result.get("status") == "imported"
                except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                    # Transient service outages are retried without changing the durable request state.
                    log_exception("acquisition_import_transient", exc, level="warning")
                    continue
                except Exception as exc:
                    # Never leave a request looking active when the coordinator itself failed
                    # outside reconcile_acquisition_download's normal validation path.
                    log_exception("acquisition_import_failed", exc)
                    message = f"Import coordinator failed: {exc}"
                    try:
                        store = catalog_store()
                        store.update_acquisition_download(
                            int(download["id"]), "failed", error=message,
                            failure_stage="import",
                        )
                        store.update_acquisition_job(
                            int(download["job_id"]), "failed", message,
                        )
                    except Exception as nested:
                        log_exception("acquisition_failure_record_failed", nested)
            if imported_any:
                start_catalog_scan(str(COMIC_LIBRARY_ROOT), True, "local")
        except Exception:
            pass
        stop_event.wait(IMPORT_POLL_SECONDS)


# The first sweep comes soon after start. A restart in the middle of a pass --
# a deploy, a crash -- otherwise left the rest of it for a quarter of an hour.
RESEARCH_FIRST_SWEEP_SECONDS = 60


def release_research_worker(stop_event: threading.Event = _RESEARCH_STOP) -> None:
    """Look again, on a schedule, for issues no release was found for.

    Nothing re-searched. An issue whose search came up empty sat at queued
    until somebody pressed "Search for missing" by hand -- and a #1 that ships
    on Wednesday is often not posted until the weekend, so the common case was
    a comic that would have been found by simply asking again.

    The backoff lives in the query, so this is a cheap sweep that mostly
    returns nothing: an issue searched minutes ago is not due, and one nobody
    ever posts costs a request a day rather than one per pass.
    """
    delay = RESEARCH_FIRST_SWEEP_SECONDS
    while not stop_event.is_set():
        stop_event.wait(delay)
        delay = RESEARCH_POLL_SECONDS
        if stop_event.is_set():
            break
        try:
            for service in ("prowlarr", "sabnzbd"):
                _enabled_acquisition_service(service)
        except Exception:
            # No indexer or no download client: every search would fail in a
            # loop and say nothing useful.
            continue
        try:
            _automatic_release_grabs(None, backoff=True)
        except Exception as exc:
            log_exception("release_research_failed", exc, level="warning")


def start_release_research_worker() -> threading.Thread:
    global _RESEARCH_THREAD
    if _RESEARCH_THREAD and _RESEARCH_THREAD.is_alive():
        return _RESEARCH_THREAD
    _RESEARCH_STOP.clear()
    _RESEARCH_THREAD = threading.Thread(
        target=release_research_worker,
        name="flipparr-release-research",
        daemon=True,
    )
    _RESEARCH_THREAD.start()
    return _RESEARCH_THREAD


def start_acquisition_import_worker() -> threading.Thread:
    global _IMPORT_THREAD
    if _IMPORT_THREAD and _IMPORT_THREAD.is_alive():
        return _IMPORT_THREAD
    _IMPORT_STOP.clear()
    _IMPORT_THREAD = threading.Thread(
        target=acquisition_import_worker,
        name="flipparr-sab-import-coordinator",
        daemon=True,
    )
    _IMPORT_THREAD.start()
    return _IMPORT_THREAD


def inspect_library_folder(folder: str, recursive: bool = True) -> dict[str, Any]:
    """Report whether a folder can be used as a library root, and what is in it.

    Setup used to accept any string and only discover a typo when the first
    scan failed, several steps later. Counting is capped because the answer
    only has to be good enough to say "this is the right folder".
    """
    path = Path(str(folder or "").strip())
    if not path.is_absolute():
        raise ValueError("Enter an absolute path, starting with /")
    if not path.exists():
        raise ValueError(f"{path} does not exist inside the container")
    if not path.is_dir():
        raise ValueError(f"{path} is not a folder")
    if not os.access(path, os.R_OK | os.X_OK):
        raise ValueError(f"{path} cannot be read by the user this service runs as")

    limit = 5000
    comics = 0
    truncated = False
    walker = os.walk(path) if recursive else [(str(path), [], os.listdir(path))]
    for _, dirnames, names in walker:
        # Skip the same directories a scan skips. Counting the originals held
        # in quarantine promised more comics than the scan would then import,
        # which is the one number this screen exists to get right.
        dirnames[:] = [name for name in dirnames if name not in MANAGED_LIBRARY_DIRS]
        for name in names:
            if os.path.splitext(name)[1].casefold() in SUPPORTED_EXTENSIONS:
                comics += 1
                if comics >= limit:
                    truncated = True
                    break
        if truncated:
            break
    return {
        "path": str(path),
        "recursive": bool(recursive),
        "comicCount": comics,
        "countTruncatedAt": limit if truncated else None,
    }


def start_catalog_scan(folder: str, recursive: bool = True, metadata_mode: str = "full") -> int:
    """Queue an incremental scan and return its durable scan-run id."""
    if metadata_mode not in {"local", "full"}:
        raise ValueError("metadataMode must be 'local' or 'full'")
    store = catalog_store()
    scan_id = store.begin_scan(folder, recursive)
    enrich_file = inventory_file if metadata_mode == "local" else enrich

    def perform() -> None:
        store.perform_scan(scan_id, scan_folder, enrich_file)
        completed = store.get_scan(scan_id)
        if completed and completed.get("status") == "complete":
            store.enqueue_metadata_enrichment()

    worker = threading.Thread(
        target=perform,
        name=f"flipparr-scan-{scan_id}",
        daemon=True,
    )
    worker.start()
    return scan_id


def normalize_isbn(value: str) -> str:
    return re.sub(r"[^0-9Xx]", "", value).upper()


def valid_isbn(value: str) -> bool:
    normalized = normalize_isbn(value)
    if len(normalized) == 13 and normalized.isdigit():
        total = sum((1 if index % 2 == 0 else 3) * int(char) for index, char in enumerate(normalized[:12]))
        return (10 - total % 10) % 10 == int(normalized[-1])
    if len(normalized) == 10:
        values = [10 if char == "X" else int(char) for char in normalized]
        return sum((10 - index) * value for index, value in enumerate(values)) % 11 == 0
    return False


def find_isbn(value: str) -> re.Match[str] | None:
    match = ISBN_13.search(value)
    if match and valid_isbn(match.group(1)):
        return match
    for candidate in ISBN_10.finditer(value):
        if valid_isbn(candidate.group(1)):
            return candidate
    return None


SEGMENTATION_LEXICON_VERSION = 1
_SEGMENT_WORDS = frozenset({
    "a", "and", "bastards", "batman", "birth", "birthright", "earth", "game",
    "head", "heaven", "key", "locke", "luther", "of", "right", "southern",
    "strange", "strode", "talent", "the",
})


def segmentation_words() -> frozenset[str]:
    """Return the versioned, platform-independent filename segmentation lexicon."""
    return _SEGMENT_WORDS


def split_compound_token(token: str) -> str:
    """Insert likely word boundaries into a long, unspaced filename token."""
    if len(token) < 8 or not token.isalpha():
        return token
    lowered = token.lower()
    words = segmentation_words()
    if lowered in words:
        return token

    def word_score(word: str) -> int | None:
        derived = False
        if word not in words:
            stems = []
            if word.endswith("s") and len(word) > 3:
                stems.append(word[:-1])
            if word.endswith("es") and len(word) > 4:
                stems.append(word[:-2])
            if word.endswith("ed") and len(word) > 4:
                stems.append(word[:-2])
            if word.endswith("ing") and len(word) > 5:
                stems.append(word[:-3])
            if not any(stem in words for stem in stems):
                return None
            derived = True
        if len(word) == 1 and word not in {"a", "i"}:
            return None
        connector_bonus = 15 if word in {"and", "of", "the"} else 0
        return len(word) * len(word) - 8 + connector_bonus - (2 if derived else 0)

    best: list[tuple[int, list[str]] | None] = [None] * (len(lowered) + 1)
    best[0] = (0, [])
    for start in range(len(lowered)):
        if best[start] is None:
            continue
        for end in range(start + 1, min(len(lowered), start + 24) + 1):
            word = lowered[start:end]
            score_delta = word_score(word)
            if score_delta is None:
                continue
            # Favor meaningful longer words while still allowing short joiners.
            score = best[start][0] + score_delta
            if best[end] is None or score > best[end][0]:
                best[end] = (score, best[start][1] + [word])
    if best[-1] is None or len(best[-1][1]) < 2:
        return token
    return " ".join(best[-1][1])


def split_compound_title(title: str) -> tuple[str, bool]:
    tokens = title.split()
    segmented = [split_compound_token(token) for token in tokens]
    result = " ".join(segmented)
    return result, result != title


def parse_filename(path: Path) -> ParsedFile:
    decoded_filename = urllib.parse.unquote(path.name)
    raw = Path(decoded_filename).stem.replace("_", " ").replace(".", " ")
    raw = re.sub(r"\s+", " ", raw).strip()

    isbn_match = find_isbn(raw)
    isbn = normalize_isbn(isbn_match.group(1)) if isbn_match else None
    year_matches = YEAR.findall(raw)
    year = int(year_matches[-1]) if year_matches else None
    volume_match = VOLUME.search(raw)
    volume = int(volume_match.group(1)) if volume_match else None
    issue_match = ISSUE.search(raw) or PADDED_ISSUE.search(raw)
    if issue_match is None and volume_match is None:
        issue_match = UNMARKED_ISSUE.search(raw)
    issue = issue_match.group(1) if issue_match else None
    if issue and issue.isdigit():
        issue = str(int(issue))

    detected_format = None
    for name, pattern in FORMAT_PATTERNS:
        if pattern.search(raw):
            detected_format = name
            break

    title = raw
    if issue_match:
        # Organized issue filenames may include a human-readable story title
        # after the issue token. It describes the issue, not the publication run.
        trailing = raw[issue_match.end():]
        if re.match(r"\s+[-–—]\s+\S", trailing):
            title = raw[:issue_match.end()]
    if isbn_match:
        title = title.replace(isbn_match.group(0), " ")
    if issue_match:
        title = title.replace(issue_match.group(0), " ", 1)
    # Parenthesized/bracketed filename groups are normally release metadata
    # such as year, scan type, or release group—not part of the comic title.
    title = re.sub(r"\([^)]*\)|\[[^]]*\]|\{[^}]*\}", " ", title)
    # Some indexer releases contain a truncated final release-group marker
    # (for example ``Chew 015 (2010) (D) (Kingpin-Empire``). Treat an
    # unmatched trailing bracket group as release metadata too. Requiring
    # whitespace before the opener and the group to reach the end avoids
    # stripping legitimate punctuation inside a comic title.
    title = re.sub(r"\s+[\(\[\{][^\)\]\}]*$", " ", title)
    title = NOISE.sub(" ", title)
    title = YEAR.sub(" ", title)
    title = VOLUME.sub(" ", title)
    # Scanner and upload pipelines leave bare identifiers in filenames
    # (Saga_vol2_1398374447.cbz). Left in the title they split one run into
    # several. Real years are already removed above and issue/volume numbers are
    # at most four digits, so a standalone run of five or more digits is never
    # part of a comic's title.
    title = re.sub(r"(?<!\d)\d{5,}(?!\d)", " ", title)
    for _, pattern in FORMAT_PATTERNS:
        title = pattern.sub(" ", title)
    title = re.sub(r"\s+-\s+|[-–—]+$", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    title, inserted_boundaries = split_compound_title(title)

    warnings = []
    if not title:
        warnings.append("Could not infer a title")
    if not isbn:
        warnings.append("No ISBN found in filename; title matching may be ambiguous")
    if issue and detected_format:
        warnings.append("Filename resembles both an issue and a collected volume")
    if inserted_boundaries:
        warnings.append("Inserted probable word boundaries in a concatenated title; verify the parsed title")

    return ParsedFile(
        path=str(path),
        filename=decoded_filename,
        extension=path.suffix.lower(),
        title=title,
        volume=volume,
        issue=issue,
        year=year,
        format=detected_format,
        isbn=isbn,
        warnings=warnings,
    )


# Comic archives are not all zips. RAR is a quarter of a typical library, and
# its compression cannot be decoded in pure Python, so those go through
# bsdtar (libarchive): BSD-licensed, and unlike unrar or 7-Zip's RAR decoder
# it carries no restriction on redistributing the image that ships it.
class ArchiveToolMissing(RuntimeError):
    """bsdtar is not installed, so non-zip comics cannot be opened here."""


_ARCHIVE_READ_TIMEOUT_SECONDS = 60
_EXTERNAL_ARCHIVE_SIGNATURES = (
    (b"Rar!\x1a\x07\x00", "rar"),        # RAR 4.x
    (b"Rar!\x1a\x07\x01\x00", "rar5"),  # RAR 5.x
    (b"7z\xbc\xaf\x27\x1c", "7z"),
)


@functools.lru_cache(maxsize=1)
def _external_archive_tool() -> str | None:
    return shutil.which("bsdtar")


def archive_kind(path: Path) -> str | None:
    """What this archive really is, read from the file rather than its name.

    A .cbr that is secretly a zip is common enough that trusting the
    extension would keep failing on files Python can already open.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(264)
    except OSError:
        return None
    if head[:2] == b"PK":
        return "zip"
    for signature, name in _EXTERNAL_ARCHIVE_SIGNATURES:
        if head.startswith(signature):
            return name
    if head[257:262] == b"ustar":
        return "tar"
    return None


def _run_archive_tool(arguments: list[str], limit: int) -> bytes:
    tool = _external_archive_tool()
    if not tool:
        raise ArchiveToolMissing(
            "This comic is a RAR archive, which needs bsdtar (libarchive-tools) to read."
        )
    completed = subprocess.run(  # noqa: S603 - argv list, never a shell string
        [tool, *arguments], capture_output=True, timeout=_ARCHIVE_READ_TIMEOUT_SECONDS,
    )
    if len(completed.stdout) > limit:
        raise ValueError("Archive entry is larger than the limit")
    # A partly damaged archive still answers for the members it can reach, and
    # the cover is usually the first of them. Fifteen comics in a real library
    # list their pages and hand over a perfectly good first page while the tool
    # exits non-zero over something later in the file; refusing that output on
    # the exit code alone left those comics with no cover at all.
    if not completed.stdout and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ValueError(detail[-1] if detail else "The archive could not be read")
    return completed.stdout


def archive_member_names(path: Path) -> list[str]:
    """Every member name, from whichever reader suits the container."""
    kind = archive_kind(path)
    if kind == "zip":
        with zipfile.ZipFile(path) as archive:
            return [info.filename for info in archive.infolist() if not info.is_dir()]
    if kind is None:
        return []
    listing = _run_archive_tool(["-tf", str(path)], 8_000_000)
    return [
        name for name in listing.decode("utf-8", "replace").splitlines()
        if name and not name.endswith("/")
    ]


def read_archive_member(path: Path, member: str, limit: int = COVER_SOURCE_MAX_BYTES) -> bytes:
    """One member's bytes, without unpacking the rest of the archive."""
    kind = archive_kind(path)
    if kind == "zip":
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(member)
            if info.file_size <= 0 or info.file_size > limit:
                raise ValueError("Archive entry is larger than the limit")
            return archive.read(info)
    if kind is None:
        raise ValueError("This file is not an archive Flipparr can read")
    if member.startswith("-"):
        # It would arrive at the tool as an option rather than a name.
        raise ValueError("Flipparr will not read a member whose name begins with a dash")
    return _run_archive_tool(["-xOf", str(path), member], limit)


def _zip_xml(archive: zipfile.ZipFile, member_name: str) -> ET.Element | None:
    try:
        info = archive.getinfo(member_name)
    except KeyError:
        return None
    if info.file_size > 2_000_000:
        return None
    try:
        return ET.fromstring(archive.read(info))
    except (ET.ParseError, OSError):
        return None


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def read_epub_metadata(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        container = _zip_xml(archive, "META-INF/container.xml")
        if container is None:
            return {}
        rootfile = container.find(".//{*}rootfile")
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        if not opf_path:
            return {}
        package = _zip_xml(archive, opf_path)
        if package is None:
            return {}
        metadata = package.find(".//{*}metadata")
        if metadata is None:
            return {}
        identifiers = [value for value in (_text(x) for x in metadata.findall("{*}identifier")) if value]
        normalized_isbns = [normalize_isbn(value) for value in identifiers if valid_isbn(value)]
        creators = []
        for creator in metadata.findall("{*}creator"):
            name = _text(creator)
            if not name:
                continue
            role = creator.get("{http://www.idpf.org/2007/opf}role") or creator.get("role")
            creators.append({"name": name, "role": role})
        description = _text(metadata.find("{*}description"))
        page_match = re.search(r"\b(\d{1,4})[- ]page\b", description or "", re.I)
        detected_format = next(
            (name for name, pattern in FORMAT_PATTERNS if pattern.search(description or "")),
            "ebook",
        )
        quoted_contents = []
        for quoted in re.findall(r"[“\"]([^”\"]+)[”\"]", description or ""):
            value = quoted.strip()
            if value and value not in quoted_contents and len(value) <= 120:
                quoted_contents.append(value)
        manifest_items = list(package.findall(".//{*}manifest/{*}item"))
        cover_meta = next(
            (item.get("content") for item in metadata.findall("{*}meta") if item.get("name") == "cover"),
            None,
        )
        cover_item = next(
            (item for item in manifest_items if "cover-image" in (item.get("properties") or "").split()),
            None,
        )
        if cover_item is None and cover_meta:
            cover_item = next((item for item in manifest_items if item.get("id") == cover_meta), None)
        if cover_item is None:
            cover_item = next(
                (
                    item for item in manifest_items
                    if (item.get("media-type") or "").startswith("image/")
                    and "cover" in f"{item.get('id') or ''} {item.get('href') or ''}".lower()
                ),
                None,
            )
        cover_member = None
        cover_media_type = None
        if cover_item is not None and cover_item.get("href"):
            cover_member = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), cover_item.get("href")))
            cover_media_type = cover_item.get("media-type")
        return {
            "source": "EPUB package metadata",
            "title": _text(metadata.find("{*}title")),
            "creators": [creator["name"] for creator in creators],
            "creator_details": creators,
            "publisher": _text(metadata.find("{*}publisher")),
            "date": _text(metadata.find("{*}date")),
            "language": _text(metadata.find("{*}language")),
            "description": description,
            "rights": _text(metadata.find("{*}rights")),
            "subjects": [value for value in (_text(x) for x in metadata.findall("{*}subject")) if value],
            "identifiers": identifiers,
            "isbns": normalized_isbns,
            "format": detected_format,
            "page_count": int(page_match.group(1)) if page_match else None,
            "named_contents": quoted_contents,
            "cover_member": cover_member,
            "cover_media_type": cover_media_type,
        }


def read_comicinfo_metadata(path: Path) -> dict[str, Any]:
    member = next(
        (name for name in archive_member_names(path)
         if name.lower().endswith("comicinfo.xml")),
        None,
    )
    if not member:
        return {}
    try:
        body = read_archive_member(path, member, 2_000_000)
    except (ValueError, OSError, subprocess.SubprocessError):
        return {}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return {}
    def value(name: str) -> str | None:
        return _text(root.find(name))
    contributors = {
        role: value(tag)
        for role, tag in (
            ("writer", "Writer"), ("penciller", "Penciller"), ("inker", "Inker"),
            ("colorist", "Colorist"), ("letterer", "Letterer"), ("cover_artist", "CoverArtist"),
        )
        if value(tag)
    }
    return {
        "source": "ComicInfo.xml",
        "series": value("Series"),
        "title": value("Title"),
        "number": value("Number"),
        "volume": value("Volume"),
        "year": value("Year"),
        "publisher": value("Publisher"),
        "imprint": value("Imprint"),
        "format": value("Format"),
        "summary": value("Summary"),
        "language": value("LanguageISO"),
        "web": value("Web"),
        "comicvine_volume_id": value("ComicVineVolumeId"),
        "comicvine_issue_id": value("ComicVineIssueId"),
        "metron_id": value("MetronId") or value("MetronIssueId"),
        "contributors": contributors,
    }


def read_embedded_metadata(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".epub":
            return read_epub_metadata(path)
        # Every comic container can hold a ComicInfo.xml. Listing .cbz here and
        # not .cbr meant a quarter of a typical library had no embedded series,
        # issue, publisher or language at all -- and so a comic in the wrong
        # language could never be recognised as one.
        if archive_kind(path) is not None:
            return read_comicinfo_metadata(path)
    except (zipfile.BadZipFile, OSError, RuntimeError, subprocess.SubprocessError):
        return {}
    return {}


def _natural_member_key(member: str) -> list[tuple[int, Any]]:
    """Sort archive page names naturally, so 2.jpg appears before 10.jpg."""
    return [
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", member)
    ]


def find_archive_cover_member(path: Path, embedded: dict[str, Any] | None = None) -> str | None:
    """Find the file's declared cover or its first plausible comic page."""
    if embedded and embedded.get("cover_member"):
        return str(embedded["cover_member"])
    try:
        images = []
        for member in archive_member_names(path):
            parts = Path(member).parts
            if (
                Path(member).suffix.lower() not in ARCHIVE_IMAGE_EXTENSIONS
                or "__MACOSX" in parts
                or any(part.startswith(".") for part in parts)
            ):
                continue
            images.append(member)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError,
            subprocess.SubprocessError):
        # An unreadable archive has no cover; it is reported as a file problem
        # by the health check, not by failing this.
        return None
    if not images:
        return None
    images.sort(key=_natural_member_key)
    named_covers = [
        member for member in images
        if re.search(r"(?:^|[_. -])(?:front[_. -]*cover|cover)(?:[_. -]|$)", Path(member).stem, re.I)
        and not re.search(r"(?:^|[_. -])back(?:[_. -]|$)", Path(member).stem, re.I)
    ]
    return named_covers[0] if named_covers else images[0]


def file_cover_info(path: Path, embedded: dict[str, Any] | None = None) -> dict[str, Any] | None:
    member = find_archive_cover_member(path, embedded)
    if not member:
        return None
    # The cover is cached by the browser for a day, so a file that changes --
    # a manga volume reordered cover-first -- needs a new address, or the old
    # picture stays for that day. The version is the file's own mtime and size.
    try:
        stat = path.stat()
        version = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
    except OSError:
        version = ""
    query = {"path": str(path), **({"v": version} if version else {})}
    return {
        "source": "comic file",
        "member": member,
        "url": "/api/file-cover?" + urllib.parse.urlencode(query),
        "max_dimension": COVER_THUMBNAIL_MAX_DIMENSION,
    }


def inspect_file_health(path: Path) -> dict[str, str]:
    """Perform cheap structural checks without reading every comic page."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"status": "error", "code": "unreadable", "message": f"File cannot be read: {exc}"}
    if size == 0:
        return {"status": "error", "code": "empty_file", "message": "File is empty (0 bytes)."}

    extension = path.suffix.lower()
    if extension in {".cbz", ".epub"}:
        try:
            with zipfile.ZipFile(path) as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
        except (zipfile.BadZipFile, OSError, RuntimeError):
            return {
                "status": "error", "code": "corrupt_archive",
                "message": "Archive is corrupt, truncated, or not a readable ZIP file.",
            }
        if not members:
            return {
                "status": "error", "code": "empty_archive",
                "message": f"Archive is empty ({size} bytes) and contains no files or comic pages.",
            }
        if extension == ".cbz" and not any(
            Path(info.filename).suffix.lower() in ARCHIVE_IMAGE_EXTENSIONS for info in members
        ):
            return {
                "status": "error", "code": "no_image_pages",
                "message": "CBZ is readable but contains no supported image pages.",
            }
    elif extension == ".cbr":
        try:
            with path.open("rb") as source:
                header = source.read(8)
        except OSError as exc:
            return {"status": "error", "code": "unreadable", "message": f"File cannot be read: {exc}"}
        if not header.startswith(b"Rar!\x1a\x07"):
            return {
                "status": "error", "code": "corrupt_archive",
                "message": "CBR does not have a valid RAR archive header.",
            }
    elif extension == ".cb7":
        try:
            with path.open("rb") as source:
                header = source.read(6)
        except OSError as exc:
            return {"status": "error", "code": "unreadable", "message": f"File cannot be read: {exc}"}
        if header != b"7z\xbc\xaf'\x1c":
            return {
                "status": "error", "code": "corrupt_archive",
                "message": "CB7 does not have a valid 7-Zip archive header.",
            }
    elif extension == ".cbt" and not tarfile.is_tarfile(path):
        return {
            "status": "error", "code": "corrupt_archive",
            "message": "CBT is not a readable TAR archive.",
        }
    elif extension == ".pdf":
        try:
            with path.open("rb") as source:
                header = source.read(5)
        except OSError as exc:
            return {"status": "error", "code": "unreadable", "message": f"File cannot be read: {exc}"}
        if header != b"%PDF-":
            return {
                "status": "error", "code": "invalid_pdf",
                "message": "PDF signature is missing; the file may be corrupt or mislabeled.",
            }
    return {"status": "ok", "code": "readable", "message": "File passed basic structural checks."}


def render_file_cover_thumbnail(path: Path, member: str) -> bytes:
    """Extract and cache a web-sized JPEG without modifying the comic archive."""
    stat = path.stat()
    fingerprint = "\0".join(
        (
            str(path.resolve()), str(stat.st_mtime_ns), str(stat.st_size), member,
            str(COVER_THUMBNAIL_MAX_DIMENSION), str(COVER_THUMBNAIL_QUALITY),
        )
    )
    cache_key = hashlib.sha256(fingerprint.encode()).hexdigest()
    cached = COVER_CACHE_DIR / f"{cache_key}.jpg"
    try:
        if cached.is_file():
            return cached.read_bytes()
    except OSError:
        pass

    source_bytes = read_archive_member(path, member, COVER_SOURCE_MAX_BYTES)
    body = normalize_image_to_jpeg(source_bytes)
    try:
        COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_temp = COVER_CACHE_DIR / f".{cache_key}-{threading.get_ident()}.tmp"
        cache_temp.write_bytes(body)
        os.replace(cache_temp, cached)
    except OSError:
        pass
    return body


def normalize_image_to_jpeg(source_bytes: bytes) -> bytes:
    """Create a bounded JPEG using Pillow on macOS, Linux, and Docker."""
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
        with Image.open(io.BytesIO(source_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((COVER_THUMBNAIL_MAX_DIMENSION, COVER_THUMBNAIL_MAX_DIMENSION))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=COVER_THUMBNAIL_QUALITY, optimize=True)
            body = output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ValueError("Cover image is not a readable supported image") from exc
    if not body:
        raise ValueError("Cover thumbnail is empty")
    return body


def render_uploaded_cover_thumbnail(source_bytes: bytes, suffix: str = ".img") -> bytes:
    """Validate and normalize an uploaded image into a bounded local JPEG."""
    if not source_bytes or len(source_bytes) > COVER_SOURCE_MAX_BYTES:
        raise ValueError("Uploaded cover must be between 1 byte and 50 MB")
    return normalize_image_to_jpeg(source_bytes)


def lookup_identity(parsed: ParsedFile, embedded: dict[str, Any]) -> ParsedFile:
    title = embedded.get("title") if embedded.get("source") == "EPUB package metadata" else embedded.get("series")
    isbns = embedded.get("isbns") or []
    # EPUB package metadata describes the book itself. ComicInfo.xml may describe
    # an issue scan incorrectly embedded in a collected file, so its date/year is
    # retained for review but not allowed to steer collection matching.
    year_value = embedded.get("date") if embedded.get("source") == "EPUB package metadata" else None
    year_match = YEAR.search(str(year_value)) if year_value else None
    warnings = [warning for warning in parsed.warnings if not (isbns and warning.startswith("No ISBN found"))]
    return ParsedFile(
        path=parsed.path,
        filename=parsed.filename,
        extension=parsed.extension,
        title=title or parsed.title,
        volume=parsed.volume,
        issue=parsed.issue,
        year=int(year_match.group(1)) if year_match else parsed.year,
        format=embedded.get("format") or parsed.format,
        isbn=isbns[0] if isbns else parsed.isbn,
        warnings=warnings,
    )


def scan_folder(folder: str, recursive: bool = True) -> list[ParsedFile]:
    root = Path(folder).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Folder does not exist or is not a directory")
    iterator = root.rglob("*") if recursive else root.glob("*")
    return [
        parse_filename(p)
        for p in iterator
        if not MANAGED_LIBRARY_DIRS.intersection(p.relative_to(root).parts)
        and p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]


def fetch_json(url: str, timeout: float = 8.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ComicMetadataPOC/0.1 (local read-only experiment)",
        },
    )
    with _safe_urlopen(request, timeout=timeout) as response:
        return json.load(response)


def load_persisted_remote_cache() -> None:
    global _PERSIST_REMOTE_CACHE
    _PERSIST_REMOTE_CACHE = True
    try:
        payload = json.loads(_remote_cache_file().read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return
    cutoff = time.time() - _REMOTE_CACHE_TTL_SECONDS
    with _REMOTE_JSON_LOCK:
        for url, entry in payload.items():
            if (
                isinstance(entry, dict)
                and entry.get("saved_at", 0) >= cutoff
                and isinstance(entry.get("data"), (dict, list))
            ):
                _REMOTE_JSON_CACHE[url] = entry["data"]


def _import_retired_provider_cache_file() -> None:
    """Carry the retired single-file cache into SQLite once, then retire it.

    The file held every response in one JSON object that was rewritten in full
    on each miss. Existing rows win the import: anything already in SQLite was
    written by the current path and is at least as trustworthy.
    """
    cache_file = _provider_cache_file()
    try:
        payload = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    cutoff = time.time() - _PROVIDER_CACHE_STALE_SECONDS
    entries: list[tuple[str, str, float, int, Any]] = []
    for key, entry in payload.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("data"), (dict, list)):
            continue
        try:
            saved_at = float(entry.get("saved_at") or 0)
        except (TypeError, ValueError):
            continue
        if saved_at < cutoff:
            continue
        # Keys are built as "{provider}:{credential_scope}:{url}".
        entries.append((key, key.split(":", 1)[0], saved_at, 200, entry["data"]))
    try:
        catalog_store().provider_cache_import(entries)
    except Exception:
        return
    try:
        cache_file.replace(cache_file.with_suffix(".migrated"))
    except OSError:
        pass


def load_persisted_provider_cache() -> None:
    _import_retired_provider_cache_file()
    try:
        warmed = catalog_store().provider_cache_load(
            time.time() - _PROVIDER_CACHE_STALE_SECONDS
        )
        catalog_store().provider_cache_prune(
            time.time() - _PROVIDER_CACHE_STALE_SECONDS
        )
    except Exception:
        # An unreadable cache costs speed, never correctness: fall back to
        # fetching everything rather than refusing to start.
        return
    with _PROVIDER_JSON_LOCK:
        _PROVIDER_JSON_CACHE.update(warmed)


def persist_provider_cache(
    key: str, provider_id: str, saved_at: float, status: int, data: Any
) -> None:
    """Write one cache row. A cache write must never fail its own lookup."""
    try:
        catalog_store().provider_cache_put(
            key, provider_id, saved_at=saved_at, status=status, data=data
        )
    except Exception:
        pass


def persist_remote_cache(snapshot: dict[str, Any]) -> None:
    if not _PERSIST_REMOTE_CACHE:
        return
    payload = {
        url: {"saved_at": time.time(), "data": data}
        for url, data in snapshot.items()
    }
    cache_file = _remote_cache_file()
    temporary = cache_file.with_suffix(".tmp")
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(temporary, cache_file)
    except OSError:
        pass


def fetch_cached_json(url: str, timeout: float = 8.0) -> Any:
    """Share remote responses and coalesce concurrent requests for the same URL."""
    while True:
        with _REMOTE_JSON_LOCK:
            cached = _REMOTE_JSON_CACHE.get(url)
            if cached is not None:
                return cached
            event = _REMOTE_JSON_INFLIGHT.get(url)
            if event is None:
                event = threading.Event()
                _REMOTE_JSON_INFLIGHT[url] = event
                leader = True
            else:
                leader = False
        if leader:
            break
        event.wait(timeout + 5.0)

    try:
        result = fetch_json(url, timeout=timeout)
    except Exception:
        with _REMOTE_JSON_LOCK:
            _REMOTE_JSON_INFLIGHT.pop(url, None)
            event.set()
        raise
    with _REMOTE_JSON_LOCK:
        _REMOTE_JSON_CACHE[url] = result
        _REMOTE_JSON_INFLIGHT.pop(url, None)
        event.set()
        cache_snapshot = dict(_REMOTE_JSON_CACHE)
    persist_remote_cache(cache_snapshot)
    return result


def search_open_library(parsed: ParsedFile) -> list[dict[str, Any]]:
    if parsed.isbn:
        query = f"isbn:{parsed.isbn}"
    else:
        query = f'title:"{parsed.title}"'
    url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(
        {"q": query, "limit": 5, "fields": "key,title,subtitle,author_name,publisher,first_publish_year,publish_year,isbn,cover_i,edition_key"}
    )
    data = fetch_cached_json(url)
    if not data.get("docs") and parsed.isbn and parsed.title:
        query = f'title:"{parsed.title}"'
        url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(
            {"q": query, "limit": 5, "fields": "key,title,subtitle,author_name,publisher,first_publish_year,publish_year,isbn,cover_i,edition_key"}
        )
        data = fetch_cached_json(url)
    results = []
    for item in data.get("docs", [])[:5]:
        results.append(
            {
                "source": "Open Library",
                "source_id": item.get("key"),
                "title": item.get("title"),
                "subtitle": item.get("subtitle"),
                "creators": item.get("author_name", []),
                "publisher": (item.get("publisher") or [None])[0],
                "publication_year": item.get("first_publish_year"),
                "isbns": item.get("isbn", [])[:10],
                "cover": f"https://covers.openlibrary.org/b/id/{item['cover_i']}-M.jpg" if item.get("cover_i") else None,
                "url": f"https://openlibrary.org{item.get('key')}" if item.get("key") else None,
                "search_query": query,
            }
        )
    return results


def metadata_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(metadata_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(metadata_text(item) for item in value)
    return ""


def human_metadata_text(value: Any) -> str:
    if isinstance(value, dict) and "value" in value:
        return human_metadata_text(value["value"])
    return metadata_text(value)


def extract_issue_coverage(
    value: str | None,
    source: str = "Open Library edition notes",
    confidence: str | None = None,
) -> list[dict[str, Any]]:
    if not value:
        return []
    text = re.sub(r"\s+", " ", value.replace("–", "-").replace("—", "-")).strip()
    clauses = []
    marker = re.compile(
        r"(?:originally\s+published(?:\s+in\s+single\s+magazine\s+form)?\s+as|"
        r"collects?|collecting|includes?)\s+(.+?)(?:\.|$)",
        re.I,
    )
    clauses.extend(match.group(1) for match in marker.finditer(text))
    if not clauses:
        clauses = [text]
    claim_pattern = re.compile(
        r"(?P<series>[A-Za-z0-9][A-Za-z0-9 &'’:.!/-]*?)\s+#(?P<start>\d+[A-Za-z]?)"
        r"(?:\s*-\s*#?(?P<end>\d+[A-Za-z]?))?",
        re.I,
    )
    claims = []
    seen = set()
    for clause in clauses:
        for match in claim_pattern.finditer(clause):
            series = re.split(r"[,;]", match.group("series"))[-1]
            series = re.sub(r"^(?:and|plus)\s+", "", series, flags=re.I).strip(" -")
            if normalized_title(series) in {"issue", "issues"}:
                series = ""
            start = match.group("start")
            end = match.group("end") or start
            issues = [start]
            if start.isdigit() and end.isdigit():
                first, last = int(start), int(end)
                if first <= last and last - first <= 200:
                    issues = [str(number) for number in range(first, last + 1)]
            key = (series.lower(), start.lower(), end.lower())
            if key in seen:
                continue
            seen.add(key)
            claim = {
                    "series": series,
                    "start_issue": start,
                    "end_issue": end,
                    "issues": issues,
                    "source_text": match.group(0).strip(),
                    "source": source,
                }
            if confidence:
                claim["confidence"] = confidence
            claims.append(claim)
    return claims


def find_open_library_volume_edition(candidate: dict[str, Any], volume: int | None) -> dict[str, Any] | None:
    source_id = candidate.get("source_id")
    if not volume or not isinstance(source_id, str) or not source_id.startswith("/works/"):
        return None
    url = f"https://openlibrary.org{source_id}/editions.json?limit=25"
    data = fetch_cached_json(url)
    pattern = re.compile(rf"(?:\b(?:vol(?:ume)?|book)\.?\s*[-#:]?\s*{volume}\b|\b{volume}\s*:\s*)", re.I)
    for entry in data.get("entries", []):
        notes_text = human_metadata_text(entry.get("notes"))
        description_text = human_metadata_text(entry.get("description"))
        searchable = metadata_text(
            {
                "title": entry.get("title"),
                "subtitle": entry.get("subtitle"),
                "other_titles": entry.get("other_titles"),
                "series": entry.get("series"),
                "notes": entry.get("notes"),
                "description": entry.get("description"),
            }
        )
        if pattern.search(searchable):
            return {
                "edition_id": entry.get("key"),
                "title": entry.get("title"),
                "subtitle": entry.get("subtitle"),
                "other_titles": entry.get("other_titles", []),
                "publish_date": entry.get("publish_date"),
                "publishers": entry.get("publishers", []),
                "isbn_10": entry.get("isbn_10", []),
                "isbn_13": entry.get("isbn_13", []),
                "number_of_pages": entry.get("number_of_pages"),
                "volume_evidence": searchable,
                "notes": notes_text or None,
                "description": description_text or None,
                "coverage": extract_issue_coverage(" ".join(x for x in (notes_text, description_text) if x)),
            }
    return None


def normalized_title(value: str | None) -> str:
    value = re.sub(r"\band\b", "&", (value or "").lower())
    return re.sub(r"[^a-z0-9]", "", value)


def fetch_gcd_json(url: str) -> Any:
    """Cache GCD responses on disk under /config so lookups persist across
    restarts and scans, not just within one process. Reuses the same
    persistent, rate-limited, stale-on-error-fallback cache already built for
    Metron and Comic Vine (fetch_provider_json) rather than the weaker
    in-memory-first remote cache. GCD is anonymous, so credential is ""."""
    return fetch_provider_json("gcd", url, "", timeout=12.0)


def gcd_title_parts(parsed: ParsedFile) -> tuple[str, str]:
    title = VOLUME.sub(" ", parsed.title)
    title = re.sub(r"\s+", " ", title).strip(" :-")
    base, separator, subtitle = title.partition(":")
    if not separator:
        subtitle = ""
    base = re.sub(r"\band\b", "&", base, flags=re.I).strip()
    return base, subtitle.strip()


def _descriptor_number(value: str | None) -> str | None:
    match = re.match(r"\s*(\d+(?:\.\w+)?)\b", value or "", re.I)
    return match.group(1) if match else None


def _gcd_issue_id(api_url: str | None) -> str | None:
    match = re.search(r"/issue/(\d+)/?", api_url or "")
    return match.group(1) if match else None


def infer_gcd_coverage(
    base_title: str,
    issue: dict[str, Any],
    series_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Infer a range only when a same-named limited series and story count agree."""
    subtitle = (issue.get("title") or "").strip()
    if not subtitle:
        return []
    target = normalized_title(f"{base_title}: {subtitle}")
    source_series = next(
        (
            series for series in series_results
            if normalized_title(series.get("name")) == target
            and series.get("country") == "us"
            and series.get("language") == "en"
            and "limited series" in (series.get("publishing_format") or "").lower()
        ),
        None,
    )
    if not source_series:
        return []
    issue_numbers = []
    for descriptor in source_series.get("issue_descriptors") or []:
        number = _descriptor_number(descriptor)
        if number and number not in issue_numbers:
            issue_numbers.append(number)
    comic_story_count = sum(
        1 for story in issue.get("story_set") or []
        if (story.get("type") or "").lower() == "comic story"
    )
    if len(issue_numbers) < 2 or comic_story_count != len(issue_numbers):
        return []
    return [{
        "series": source_series.get("name"),
        "start_issue": issue_numbers[0],
        "end_issue": issue_numbers[-1],
        "issues": issue_numbers,
        "source_text": (
            f"Inferred from {comic_story_count} comic stories in the volume "
            f"and {len(issue_numbers)} distinct issues in the matching GCD limited series."
        ),
        "source": "Grand Comics Database inference",
        "confidence": "inferred",
    }]


def clean_gcd_credit(value: str | None) -> list[str]:
    names = []
    for raw_name in re.split(r"\s*;\s*", value or ""):
        name = re.sub(r"\s*\((?:credited|signed(?:\s+as)?)[^)]*\).*", "", raw_name, flags=re.I).strip()
        if not name or name.startswith("?") or name.lower() in {"none", "various", "typeset"}:
            continue
        if name not in names:
            names.append(name)
    return names


def gcd_candidate_from_issue(
    parsed: ParsedFile,
    base_title: str,
    series: dict[str, Any],
    descriptor: str,
    issue: dict[str, Any],
    series_results: list[dict[str, Any]],
    preliminary_score: int,
    preliminary_reasons: list[str],
    record_kind: str = "collection",
) -> dict[str, Any]:
    isbns = [normalize_isbn(issue["isbn"])] if issue.get("isbn") and valid_isbn(issue["isbn"]) else []
    reasons = list(preliminary_reasons)
    score = preliminary_score
    if parsed.isbn and parsed.isbn in isbns:
        score += 100
        reasons.append("exact ISBN")
    elif parsed.isbn and isbns:
        score -= 15
        reasons.append("ISBN differs from the file metadata; GCD may describe a print edition of the same collection")
    stories = issue.get("story_set") or []
    comic_stories = [
        story for story in stories if (story.get("type") or "").lower() == "comic story"
    ]
    credit_stories = comic_stories or stories
    creators = []
    contributor_roles: dict[str, list[str]] = {}
    for role in ("script", "pencils", "inks", "colors", "letters", "editing"):
        names = []
        for story in credit_stories:
            for name in clean_gcd_credit(story.get(role)):
                if name not in names:
                    names.append(name)
                if name not in creators:
                    creators.append(name)
        if names:
            contributor_roles[role] = names
    cover_contributors = []
    for story in stories:
        if (story.get("type") or "").lower() != "cover":
            continue
        for role in ("pencils", "inks", "colors"):
            for name in clean_gcd_credit(story.get(role)):
                if name not in cover_contributors:
                    cover_contributors.append(name)
    coverage = infer_gcd_coverage(base_title, issue, series_results) if record_kind == "collection" else []
    issue_id = _gcd_issue_id(issue.get("api_url"))
    publication_date = issue.get("on_sale_date") or issue.get("key_date") or issue.get("publication_date")
    year_match = YEAR.search(str(publication_date)) if publication_date else None
    page_count = issue.get("page_count")
    try:
        page_count = int(float(page_count)) if page_count else None
    except (TypeError, ValueError):
        page_count = None
    named_comic_stories = [
        story.get("title") for story in comic_stories if story.get("title")
    ]
    return {
        "source": "Grand Comics Database",
        "source_id": issue_id,
        "record_type": "single_issue" if record_kind == "single_issue" else "collected_edition",
        "title": series.get("name") or re.sub(r"\s*\(\d{4} series\)$", "", issue.get("series_name") or ""),
        "subtitle": issue.get("title") or None,
        "issue": issue.get("number") if record_kind == "single_issue" else None,
        "volume": (issue.get("volume") or issue.get("number")) if record_kind == "collection" else None,
        "descriptor": issue.get("descriptor") or descriptor,
        "creators": creators,
        "contributors": contributor_roles,
        "cover_contributors": cover_contributors,
        "publisher": issue.get("indicia_publisher"),
        "publication_year": int(year_match.group(1)) if year_match else None,
        "publication_date": publication_date,
        "isbns": isbns,
        "cover": issue.get("cover"),
        "url": f"https://www.comics.org/issue/{issue_id}/" if issue_id else None,
        "format": series.get("binding") or series.get("publishing_format"),
        "language": series.get("language"),
        "named_contents": named_comic_stories,
        "matched_edition": {
            "isbn_10": [value for value in isbns if len(value) == 10],
            "isbn_13": [value for value in isbns if len(value) == 13],
            "number_of_pages": page_count,
            "coverage": coverage,
        },
        "coverage_status": (
            "single issue; volume coverage does not apply"
            if record_kind == "single_issue" else
            "inferred from matching GCD series and story counts; not an explicit GCD reprint relationship"
            if coverage else
            "GCD identifies this volume, but its API does not expose a defensible issue range for it"
        ),
        "match_score": score,
        "match_score_note": "internal candidate ranking value; not a confidence percentage",
        "match_reasons": reasons,
        "verification_status": (
            "single issue matched through the GCD API by series and issue number"
            if record_kind == "single_issue" else
            "exact edition ISBN verified through the GCD API; inferred coverage is labeled separately"
            if parsed.isbn and parsed.isbn in isbns else
            "collection identity matched through the GCD API; the exact file edition is not confirmed"
        ),
    }


def search_gcd(parsed: ParsedFile) -> list[dict[str, Any]]:
    base_title, subtitle = gcd_title_parts(parsed)
    if not base_title:
        return []
    query_path = urllib.parse.quote(base_title, safe="")
    search_url = f"{GCD_API_BASE}/series/name/{query_path}/"
    data = fetch_gcd_json(search_url)
    series_results = data.get("results") or []
    full_title = normalized_title(parsed.title)
    base_normalized = normalized_title(base_title)
    if parsed.issue:
        ranked_refs = []
        for series in series_results:
            publishing_format = (series.get("publishing_format") or "").lower()
            if "collected edition" in publishing_format:
                continue
            if series.get("country") not in {None, "", "us"} or series.get("language") not in {None, "", "en"}:
                continue
            similarity = difflib.SequenceMatcher(
                None, normalized_title(series.get("name")), base_normalized
            ).ratio()
            if similarity < 0.82:
                continue
            issues = series.get("active_issues") or []
            descriptors = series.get("issue_descriptors") or []
            for index, api_url in enumerate(issues):
                descriptor = descriptors[index] if index < len(descriptors) else ""
                if _descriptor_number(descriptor) != parsed.issue:
                    continue
                score = 55 + int(40 * similarity)
                reasons = [
                    f"GCD descriptor matches issue {parsed.issue}",
                    f"series title similarity {similarity:.0%}",
                ]
                if series.get("country") == "us" and series.get("language") == "en":
                    score += 10
                    reasons.append("US English issue")
                if "[" in descriptor:
                    score -= 5
                ranked_refs.append((score, series, descriptor, api_url, reasons))
        ranked_refs.sort(key=lambda item: item[0], reverse=True)
        candidates = []
        seen_urls = set()
        for score, series, descriptor, api_url, reasons in ranked_refs:
            if api_url in seen_urls:
                continue
            seen_urls.add(api_url)
            issue = fetch_gcd_json(api_url)
            candidates.append(
                gcd_candidate_from_issue(
                    parsed, base_title, series, descriptor, issue, series_results,
                    score, reasons, record_kind="single_issue",
                )
            )
            # A filename normally cannot identify a cover variant, so use the
            # best regular issue instead of spending API calls on variants.
            if len(candidates) == 1:
                break
        candidates.sort(key=lambda item: item["match_score"], reverse=True)
        return candidates
    ranked_refs = []
    for series in series_results:
        publishing_format = (series.get("publishing_format") or "").lower()
        if "collected edition" not in publishing_format:
            continue
        if series.get("country") not in {None, "", "us"} or series.get("language") not in {None, "", "en"}:
            continue
        series_name = normalized_title(series.get("name"))
        base_similarity = difflib.SequenceMatcher(None, series_name, base_normalized).ratio()
        full_similarity = difflib.SequenceMatcher(None, series_name, full_title).ratio()
        if max(base_similarity, full_similarity) < 0.72:
            continue
        issues = series.get("active_issues") or []
        descriptors = series.get("issue_descriptors") or []
        for index, api_url in enumerate(issues):
            descriptor = descriptors[index] if index < len(descriptors) else ""
            number = _descriptor_number(descriptor)
            if parsed.volume is not None and number != str(parsed.volume):
                continue
            subtitle_match = bool(
                subtitle and normalized_title(subtitle) in normalized_title(descriptor)
            )
            full_series_match = full_similarity >= 0.88
            if parsed.volume is None and subtitle and not (subtitle_match or full_series_match):
                continue
            if parsed.volume is None and not subtitle and len(issues) > 1:
                continue
            score = 30
            reasons = ["GCD identifies the series as a collected volume"]
            if base_similarity == 1:
                score += 45
                reasons.append("exact normalized series title")
            elif full_similarity >= 0.88:
                score += 35
                reasons.append("strong collected-volume title match")
            else:
                score += int(25 * max(base_similarity, full_similarity))
            if parsed.volume is not None and number == str(parsed.volume):
                score += 35
                reasons.append(f"GCD descriptor matches volume {parsed.volume}")
            if subtitle_match:
                score += 25
                reasons.append("GCD descriptor matches the embedded subtitle")
            if series.get("country") == "us" and series.get("language") == "en":
                score += 10
                reasons.append("US English edition")
            # Prefer the base printing over later-printing duplicates.
            if "[" in descriptor:
                score -= 5
            ranked_refs.append((score, series, descriptor, api_url, reasons))
    ranked_refs.sort(key=lambda item: item[0], reverse=True)
    candidates = []
    seen_urls = set()
    for score, series, descriptor, api_url, reasons in ranked_refs:
        if api_url in seen_urls:
            continue
        seen_urls.add(api_url)
        issue = fetch_gcd_json(api_url)
        candidates.append(
            gcd_candidate_from_issue(
                parsed, base_title, series, descriptor, issue, series_results, score, reasons
            )
        )
        # Return the best edition first; additional printings multiply network
        # time without improving the recommendation in this POC.
        if len(candidates) == 1:
            break
    candidates.sort(key=lambda item: item["match_score"], reverse=True)
    return candidates


def _gcd_series_id(series: dict[str, Any]) -> str | None:
    match = re.search(r"/series/(\d+)/?", series.get("api_url") or "")
    return match.group(1) if match else None


def _gcd_series_publisher(series: dict[str, Any]) -> str | None:
    value = series.get("publisher") or series.get("publisher_name") or series.get("indicia_publisher")
    if isinstance(value, dict):
        value = value.get("name") or value.get("label")
    if isinstance(value, str) and re.match(r"https?://", value):
        return None
    return str(value).strip() if value else None


def _gcd_canonical_issue_entries(series: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one base record for each issue number, collapsing cover variants."""
    canonical_entries: dict[str, dict[str, Any]] = {}
    issue_urls = series.get("active_issues") or []
    descriptors = series.get("issue_descriptors") or []
    for index, api_url in enumerate(issue_urls):
        descriptor = descriptors[index] if index < len(descriptors) else ""
        number = _descriptor_number(descriptor)
        provider_match = re.search(r"/issue/(\d+)/?", api_url or "")
        if not number or not provider_match:
            continue
        entry = {
            "number": number,
            "provider_id": provider_match.group(1),
            "api_url": api_url,
            "descriptor": descriptor,
        }
        existing = canonical_entries.get(number)
        # Prefer the undecorated/base issue to variant covers and later printings.
        if existing is None or ("[" in existing["descriptor"] and "[" not in descriptor):
            canonical_entries[number] = entry
    return sorted(canonical_entries.values(), key=lambda item: _natural_member_key(item["number"]))


def _issue_year(issue: dict[str, Any]) -> int | None:
    publication_date = issue.get("on_sale_date") or issue.get("key_date") or issue.get("publication_date")
    year_match = YEAR.search(str(publication_date)) if publication_date else None
    return int(year_match.group(1)) if year_match else None


def _issue_date(issue: dict[str, Any]) -> str | None:
    publication_date = issue.get("on_sale_date") or issue.get("key_date") or issue.get("publication_date")
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", str(publication_date or ""))
    return match.group(1) if match else None


def _hydrate_gcd_issue_entries_with_status(
    entries: list[dict[str, Any]], provider_series_id: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach all issue titles/dates with one overview call when GCD supports it.

    GCD explicitly throttles anonymous API traffic. Its series-overview endpoint
    returns every non-variant issue and longest comic-story title at once, so a
    normal refresh should never fan out into dozens of concurrent requests.
    """
    if not entries:
        return [], {
            "status": "complete", "source": "none", "titleCount": 0,
            "dateCount": 0, "missingTitleCount": 0, "missingDateCount": 0,
        }
    if provider_series_id:
        overview_url = f"{GCD_API_BASE}/series/{provider_series_id}/overview/?format=json"
        try:
            overview_payload = fetch_gcd_json(overview_url)
            overview_rows = (
                overview_payload
                if isinstance(overview_payload, list)
                else (overview_payload.get("results") or []) if isinstance(overview_payload, dict) else []
            )
            by_provider_id = {
                str(row.get("issue_id")): row for row in overview_rows if row.get("issue_id") is not None
            }
            by_number = {
                str(row.get("number")): row for row in overview_rows if row.get("number") is not None
            }
            hydrated_entries = []
            for entry in entries:
                hydrated = dict(entry)
                row = by_provider_id.get(str(entry.get("provider_id"))) or by_number.get(str(entry.get("number")))
                if row:
                    story = row.get("longest_story") or {}
                    hydrated["title"] = row.get("title") or story.get("title") or hydrated.get("title")
                    hydrated["publication_year"] = _issue_year(row) or hydrated.get("publication_year")
                    hydrated["publication_date"] = _issue_date(row) or hydrated.get("publication_date")
                    if row.get("cover_url"):
                        hydrated["cover"] = row["cover_url"]
                hydrated_entries.append(hydrated)
            title_count = sum(bool(entry.get("title")) for entry in hydrated_entries)
            date_count = sum(bool(entry.get("publication_year")) for entry in hydrated_entries)
            return hydrated_entries, {
                "status": "complete" if title_count == len(entries) else "partial",
                "source": "gcd_series_overview", "titleCount": title_count,
                "dateCount": date_count, "missingTitleCount": len(entries) - title_count,
                "missingDateCount": len(entries) - date_count,
            }
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    retry_seconds = int(retry_after) if retry_after else None
                except ValueError:
                    retry_seconds = None
                return [dict(entry) for entry in entries], {
                    "status": "deferred", "source": "gcd_series_overview",
                    "titleCount": sum(bool(entry.get("title")) for entry in entries),
                    "dateCount": sum(bool(entry.get("publication_year")) for entry in entries),
                    "missingTitleCount": sum(not entry.get("title") for entry in entries),
                    "missingDateCount": sum(not entry.get("publication_year") for entry in entries),
                    "retryAfterSeconds": retry_seconds,
                }
            if exc.code != 404:
                raise
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            pass

    # Compatibility fallback for providers that have not deployed the overview
    # endpoint. Keep it sequential to avoid triggering anti-bot throttling.
    hydrated_entries = []
    for entry in entries:
        hydrated = dict(entry)
        api_url = entry.get("api_url")
        if not api_url:
            hydrated_entries.append(hydrated)
            continue
        try:
            issue = fetch_gcd_json(api_url)
        except urllib.error.HTTPError as exc:
            hydrated_entries.append(hydrated)
            if exc.code == 429:
                hydrated_entries.extend(dict(item) for item in entries[len(hydrated_entries):])
                break
            continue
        except Exception:
            hydrated_entries.append(hydrated)
            continue
        comic_stories = [
            story for story in (issue.get("story_set") or [])
            if str(story.get("type") or "").casefold() == "comic story"
        ]
        hydrated["title"] = issue.get("title") or next(
            (story.get("title") for story in comic_stories if story.get("title")), None
        )
        hydrated["publication_year"] = _issue_year(issue)
        hydrated["publication_date"] = _issue_date(issue)
        hydrated_entries.append(hydrated)
    title_count = sum(bool(entry.get("title")) for entry in hydrated_entries)
    date_count = sum(bool(entry.get("publication_year")) for entry in hydrated_entries)
    return hydrated_entries, {
        "status": "complete" if title_count == len(entries) else "partial",
        "source": "individual_issue_fallback", "titleCount": title_count,
        "dateCount": date_count, "missingTitleCount": len(entries) - title_count,
        "missingDateCount": len(entries) - date_count,
    }


def _hydrate_gcd_issue_entries(
    entries: list[dict[str, Any]], provider_series_id: str | None = None
) -> list[dict[str, Any]]:
    return _hydrate_gcd_issue_entries_with_status(entries, provider_series_id)[0]


def rank_gcd_series_runs(context: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank possible GCD publication runs without treating the score as certainty."""
    target_titles = [context.get("title") or "", *(context.get("aliases") or [])]
    normalized_targets = [normalized_title(value) for value in target_titles if normalized_title(value)]
    owned_numbers = {str(value).strip().lower() for value in context.get("ownedIssueNumbers") or []}
    target_publisher = normalized_title(context.get("publisher"))
    target_year = context.get("year")
    try:
        target_year = int(target_year) if target_year else None
    except (TypeError, ValueError):
        target_year = None
    candidates = []
    seen_ids: set[str] = set()
    for series in results:
        provider_id = _gcd_series_id(series)
        if not provider_id or provider_id in seen_ids:
            continue
        if series.get("country") not in {None, "", "us"} or series.get("language") not in {None, "", "en"}:
            continue
        publishing_format = str(series.get("publishing_format") or "")
        if "collected edition" in publishing_format.lower():
            continue
        candidate_title = normalized_title(series.get("name"))
        title_similarity = max(
            (difflib.SequenceMatcher(None, candidate_title, target).ratio() for target in normalized_targets),
            default=0,
        )
        if title_similarity < 0.5:
            continue
        entries = _gcd_canonical_issue_entries(series)
        if not entries:
            continue
        issue_numbers = [str(entry["number"]) for entry in entries]
        issue_number_set = {number.strip().lower() for number in issue_numbers}
        overlap_numbers = sorted(owned_numbers & issue_number_set, key=_natural_member_key)
        score = int(title_similarity * 60)
        reasons = []
        if title_similarity == 1:
            score += 20
            reasons.append("Exact series-title match")
        elif title_similarity >= 0.85:
            score += 10
            reasons.append("Strong series-title match")
        else:
            reasons.append("Possible series-title match")
        try:
            candidate_year = int(series.get("year_began")) if series.get("year_began") else None
        except (TypeError, ValueError):
            candidate_year = None
        if target_year and candidate_year:
            year_difference = abs(target_year - candidate_year)
            if year_difference == 0:
                score += 25
                reasons.append(f"Series began in {target_year}")
            elif year_difference == 1:
                score += 8
                reasons.append("Start year is within one year")
            elif year_difference >= 5:
                score -= 20
                reasons.append(f"Start year differs by {year_difference} years")
        publisher = _gcd_series_publisher(series)
        publisher_similarity = difflib.SequenceMatcher(
            None, normalized_title(publisher), target_publisher
        ).ratio() if publisher and target_publisher else 0
        if publisher_similarity >= 0.85:
            score += 15
            reasons.append("Publisher matches library metadata")
        if owned_numbers:
            overlap_ratio = len(overlap_numbers) / len(owned_numbers)
            score += int(overlap_ratio * 30)
            if overlap_numbers:
                reasons.append(
                    f"Contains {len(overlap_numbers)} of {len(owned_numbers)} issue numbers already owned"
                )
            else:
                score -= 15
                reasons.append("Does not contain the issue numbers currently associated with your editions")
        if "ongoing" in publishing_format.lower() or "limited" in publishing_format.lower():
            score += 5
        year_ended = series.get("year_ended")
        year_label = str(candidate_year or "Unknown")
        if year_ended and str(year_ended) != str(candidate_year):
            year_label += f"–{year_ended}"
        candidates.append({
            "provider": "gcd", "providerSeriesId": provider_id,
            "apiUrl": series.get("api_url") or "", "title": series.get("name") or "Untitled series",
            "yearBegan": candidate_year, "yearEnded": year_ended, "yearLabel": year_label,
            "publisher": publisher, "publishingFormat": publishing_format or None,
            "status": series.get("publication_status") or None,
            "issueCount": len(entries), "issueNumbers": issue_numbers,
            "sampleIssues": issue_numbers[:12], "ownedOverlap": len(overlap_numbers),
            "ownedIssueCount": len(owned_numbers), "overlapIssueNumbers": overlap_numbers,
            "matchScore": score, "matchReasons": reasons,
            "fit": "strong" if score >= 110 else "possible" if score >= 75 else "review",
            "_series": series,
        })
        seen_ids.add(provider_id)
    candidates.sort(key=lambda item: (-item["matchScore"], item["title"], item["yearBegan"] or 0))
    return candidates[:12]


def _gcd_discovery_search_rows(query: str) -> list[dict[str, Any]]:
    """Find the alphabetic page containing an exact title without crawling every result page."""
    base_url = f"{GCD_API_BASE}/series/name/{urllib.parse.quote(query, safe='')}/"
    first_page = fetch_gcd_json(base_url)
    results = list(first_page.get("results") or [])
    target = str(query).casefold()
    if any(str(item.get("name") or "").casefold() == target for item in results):
        return results
    try:
        page_count = max(1, (int(first_page.get("count") or len(results)) + 49) // 50)
    except (TypeError, ValueError):
        page_count = 1
    low, high = 2, min(page_count, 40)
    while low <= high:
        page = (low + high) // 2
        separator = "&" if "?" in base_url else "?"
        payload = fetch_gcd_json(f"{base_url}{separator}page={page}")
        page_results = list(payload.get("results") or [])
        if not page_results:
            break
        results.extend(page_results)
        names = [str(item.get("name") or "").casefold() for item in page_results]
        if target in names:
            break
        if target < names[0]:
            high = page - 1
        elif target > names[-1]:
            low = page + 1
        else:
            # GCD's ordering normalizes articles and localized titles, so an
            # exact title can fall on an adjacent page even when it appears
            # lexically inside this page's range.
            for neighbor in (page - 1, page + 1):
                if neighbor < 2 or neighbor > page_count:
                    continue
                adjacent = fetch_gcd_json(f"{base_url}{separator}page={neighbor}")
                adjacent_results = list(adjacent.get("results") or [])
                results.extend(adjacent_results)
                if any(str(item.get("name") or "").casefold() == target for item in adjacent_results):
                    return results
            break
    return results


def _discovery_query_parts(query: str) -> tuple[str, str, int | None]:
    """Split an optional trailing publication year from a discovery query."""
    cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
    match = re.fullmatch(r"(.+?)(?:\s+|\s*\()((?:19|20)\d{2})\)?", cleaned)
    if match and match.group(1).strip():
        return cleaned, match.group(1).strip(), int(match.group(2))
    return cleaned, cleaned, None


def discover_gcd_series(query: str, library: dict[str, Any] | None = None) -> dict[str, Any]:
    """Search publication runs that are not limited to the local library."""
    cleaned, title_query, year_hint = _discovery_query_parts(query)
    if len(cleaned) < 2:
        raise ValueError("Enter at least two characters to discover a series")
    ranked = rank_gcd_series_runs(
        {"title": title_query, "aliases": [], "ownedIssueNumbers": []},
        _gcd_discovery_search_rows(title_query),
    )
    if year_hint:
        ranked.sort(key=lambda item: (
            0 if normalized_title(item.get("title")) == normalized_title(title_query) else 1,
            abs((item.get("yearBegan") or 0) - year_hint) if item.get("yearBegan") else 9999,
            -item.get("matchScore", 0),
        ))
    view = library or _discovery_library_view()
    provider_ids = view["providerIds"]
    library_keys = view["keys"]
    results = []
    for candidate in ranked[:24]:
        public = {key: value for key, value in candidate.items() if key != "_series"}
        key = (normalized_title(candidate.get("title")), str(candidate.get("yearBegan") or ""))
        public["inLibrary"] = (
            str(candidate["providerSeriesId"]) in provider_ids or key in library_keys
        )
        results.append(public)
    return {
        "query": cleaned,
        "titleQuery": title_query,
        "yearHint": year_hint,
        "provider": "Grand Comics Database",
        "providerId": "gcd",
        "results": results,
    }


def _discovery_library_view() -> dict[str, Any]:
    """What the library already has, as discovery needs to ask it.

    Built once and handed to every provider: the catalog projection is not
    cheap, and searching three providers at once used to mean building it three
    times in parallel.
    """
    items = catalog_store().catalog().get("series") or []
    return {
        "keys": {
            (normalized_title(item.get("title")), str(item.get("year") or ""))
            for item in items
        },
        "providerIds": {
            str((item.get("issueCatalog") or {}).get("providerSeriesId"))
            for item in items if (item.get("issueCatalog") or {}).get("providerSeriesId")
        },
    }


def _metron_discovery_creators(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return concise, structured credits suitable for a discovery byline."""
    useful_roles = {"writer", "artist", "penciller", "inker", "colorist", "letterer"}
    creators = []
    for credit in issue.get("credits") or []:
        name = str(credit.get("creator") or "").strip()
        roles = [
            str(role.get("name") or "").strip()
            for role in (credit.get("role") or [])
            if str(role.get("name") or "").strip().casefold() in useful_roles
        ]
        if name and roles:
            creators.append({"name": name, "roles": roles})
    return creators


def _metron_series_row(
    row: dict[str, Any], library_keys: set[tuple[str, str]], search_rank: int = 0,
) -> dict[str, Any] | None:
    """One Metron series-list row as a discovery card."""
    raw_title = str(row.get("series") or row.get("name") or "").strip()
    title = re.sub(r"\s+\((?:19|20)\d{2}\)$", "", raw_title).strip()
    if not title or not row.get("id"):
        return None
    year = _provider_year(row.get("year_began"), raw_title)
    year_ended = _provider_year(row.get("year_end"))
    publisher_value = row.get("publisher") or {}
    publisher = publisher_value.get("name") if isinstance(publisher_value, dict) else publisher_value
    return {
        "provider": "metron", "providerName": "Metron",
        "providerSeriesId": str(row["id"]), "title": title,
        "yearBegan": year, "yearEnded": year_ended,
        "yearLabel": f"{year or 'Unknown'}{f'–{year_ended}' if year_ended and year_ended != year else ''}",
        "publisher": publisher, "issueCount": int(row.get("issue_count") or 0),
        "cover": row.get("image"), "status": row.get("status"),
        "inLibrary": (normalized_title(title), str(year or "")) in library_keys,
        "_searchRank": search_rank,
    }


def discover_metron_series(
    query: str, token: str, library: dict[str, Any] | None = None, *, hydrate: bool = True,
) -> dict[str, Any]:
    """Use Metron's purpose-built series list endpoint for primary discovery.

    `hydrate` fills in the best exact match from three further requests: the
    series record, then an issue listed from it, then that issue, for a
    contributor byline. Metron paces callers at one request every 3.2s, so
    those three are almost all of a cold search's latency -- and they improve
    exactly one row out of the two dozen returned.

    The match workbench shows the byline and asks for them. Discover does not:
    it shows a title, a publisher, a year, a run status and a cover, and the
    search row already carries every one of those. Where a row is missing one,
    merging Comic Vine and GCD fills it -- in parallel, for every row, at no
    additional wait.
    """
    cleaned, title_query, year_hint = _discovery_query_parts(query)
    if len(cleaned) < 2:
        raise ValueError("Enter at least two characters to discover a series")
    url = f"{METRON_API_BASE}/series/?" + urllib.parse.urlencode({"q": title_query})
    payload = fetch_provider_json("metron", url, token)
    library_keys = (library or _discovery_library_view())["keys"]
    results = [
        item for search_rank, row in enumerate((payload.get("results") or [])[:100])
        if (item := _metron_series_row(row, library_keys, search_rank))
    ]
    results.sort(key=lambda item: (
        0 if normalized_title(item["title"]) == normalized_title(title_query) else 1,
        abs((item.get("yearBegan") or 0) - year_hint) if year_hint and item.get("yearBegan") else (9999 if year_hint else 0),
        -difflib.SequenceMatcher(None, normalized_title(item["title"]), normalized_title(title_query)).ratio(),
        -(item.get("yearBegan") or 0), item["_searchRank"],
    ))
    # The list endpoint is intentionally lightweight. Hydrate only the best
    # exact match so the primary result gets publisher/status/cover context
    # without turning one search into an N+1 request burst.
    exact = next(
        (item for item in results if normalized_title(item["title"]) == normalized_title(title_query)),
        None,
    )
    if exact is not None and hydrate:
        try:
            detail = fetch_provider_json(
                "metron", f"{METRON_API_BASE}/series/{exact['providerSeriesId']}/", token,
            )
            publisher_value = detail.get("publisher") or {}
            exact["publisher"] = (
                publisher_value.get("name") if isinstance(publisher_value, dict) else publisher_value
            ) or exact.get("publisher")
            exact["cover"] = detail.get("image") or exact.get("cover")
            exact["status"] = detail.get("status") or exact.get("status")
            detail_end = _provider_year(detail.get("year_end"))
            if detail_end:
                exact["yearEnded"] = detail_end
                exact["yearLabel"] = f"{exact.get('yearBegan') or 'Unknown'}–{detail_end}"
        except Exception:
            pass
        # Series records do not contain contributor credits. Sample the first
        # issue so an exact discovery match can show an honest comic byline
        # without hydrating every search result and overwhelming the provider.
        try:
            issue_page = fetch_provider_json(
                "metron",
                f"{METRON_API_BASE}/series/{exact['providerSeriesId']}/issue_list/?page=1",
                token,
            )
            representative = next((
                issue for issue in (issue_page.get("results") or []) if issue.get("id")
            ), None)
            if representative:
                exact["cover"] = exact.get("cover") or representative.get("image")
                issue_detail = fetch_provider_json(
                    "metron", f"{METRON_API_BASE}/issue/{representative['id']}/", token,
                )
                creators = _metron_discovery_creators(issue_detail)
                if creators:
                    exact["creators"] = creators
                    exact["creatorCreditsSource"] = (
                        f"Issue #{representative.get('number')}"
                        if representative.get("number") else "Representative issue"
                    )
        except Exception:
            pass
    for item in results:
        item.pop("_searchRank", None)
    return {
        "query": cleaned, "titleQuery": title_query, "yearHint": year_hint,
        "provider": "Metron", "providerId": "metron", "results": results[:24],
    }


def _comic_vine_volume_row(
    row: dict[str, Any], library_keys: set[tuple[str, str]], english_only: bool,
) -> dict[str, Any] | None:
    """One Comic Vine volume as a discovery card, or None for an edition to skip."""
    title = str(row.get("name") or "").strip()
    if not title or not row.get("id"):
        return None
    year = _provider_year(row.get("start_year"))
    publisher = (row.get("publisher") or {}).get("name")
    edition = manga_edition(publisher)
    if edition == "foreign" and english_only:
        return None
    image = row.get("image") or {}
    return {
        "provider": "comic_vine", "providerName": "Comic Vine",
        "providerSeriesId": str(row["id"]), "title": title,
        "yearBegan": year, "yearEnded": None, "yearLabel": str(year or "Unknown"),
        "publisher": publisher, "issueCount": int(row.get("count_of_issues") or 0),
        "medium": "manga" if edition == "manga" else "comic",
        "cover": image.get("medium_url") or image.get("small_url"),
        "url": row.get("site_detail_url"),
        "description": row.get("description"),
        "inLibrary": (normalized_title(title), str(year or "")) in library_keys,
    }


def discover_comic_vine_series(
    query: str, api_key: str, library: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Use Comic Vine volume search as a secondary discovery catalog."""
    cleaned, title_query, year_hint = _discovery_query_parts(query)
    if len(cleaned) < 2:
        raise ValueError("Enter at least two characters to discover a series")
    url = f"{COMIC_VINE_API_BASE}/search/?" + urllib.parse.urlencode({
        "api_key": api_key, "format": "json", "resources": "volume", "query": title_query,
        "limit": 50, "field_list": ",".join(COMIC_VINE_VOLUME_FIELDS),
    })
    payload = fetch_provider_json("comic_vine", url, api_key)
    if str(payload.get("status_code")) != "1":
        raise ValueError(str(payload.get("error") or "Comic Vine series search failed"))
    library_keys = (library or _discovery_library_view())["keys"]
    # Comic Vine lists every edition of a manga as its own run. For a reader
    # in English the Japanese original and the German, Spanish and Korean
    # editions are the same book they cannot read, so they are left out --
    # before the cut below, or the English one can be the row that is cut.
    english_only = preferred_language() == "en"
    results = [
        item for row in (payload.get("results") or [])
        if (item := _comic_vine_volume_row(row, library_keys, english_only))
    ]
    results.sort(key=lambda item: (
        0 if normalized_title(item["title"]) == normalized_title(title_query) else 1,
        abs((item.get("yearBegan") or 0) - year_hint) if year_hint and item.get("yearBegan") else (9999 if year_hint else 0),
        -difflib.SequenceMatcher(None, normalized_title(item["title"]), normalized_title(title_query)).ratio(),
        -(item.get("yearBegan") or 0), item["title"],
    ))
    return {
        "query": cleaned, "titleQuery": title_query, "yearHint": year_hint,
        "provider": "Comic Vine", "providerId": "comic_vine", "results": results[:24],
    }


# Which provider's record to show when several describe the same run. Metron
# carries publisher, status, cover and credits; Comic Vine has art and
# publisher; GCD is the deepest catalog but has no cover. This is the order the
# search chain already preferred, kept now that everyone is asked.
_DISCOVERY_PROVIDERS = {
    "metron": "Metron",
    "comic_vine": "Comic Vine",
    "gcd": "Grand Comics Database",
}

# Fields worth taking from a lesser record when the preferred one lacks them.
#
# `yearEnded` is deliberately not among them. A provider that models an end
# year and reports none is saying the run is still going -- that is the whole
# distinction issue_catalog_status.run_end_status exists to keep -- so filling
# it from a provider that happens to name a year turns "still running" into
# "finished", and the card then reads Run Complete for a comic shipping this
# week. `yearLabel` is derived from it and goes with it.
_DISCOVERY_FILLABLE = (
    "cover", "publisher", "status", "issueCount",
    "creators", "creatorCreditsSource", "coverProvider", "medium",
)


def _merge_discovered_runs(
    by_provider: dict[str, list[dict[str, Any]]], title_query: str, year_hint: int | None
) -> list[dict[str, Any]]:
    """One row per publication run, however many providers know about it.

    Providers disagree about which runs exist and about how to spell them, so a
    run is identified by title and start year. Every provider's id for it is
    kept: importing has to go back to the source that actually has the run, and
    the source with the best record for the card is not always that one.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for provider_id, provider_name in _DISCOVERY_PROVIDERS.items():
        for row in by_provider.get(provider_id) or []:
            key = (normalized_title(row.get("title")), str(row.get("yearBegan") or ""))
            existing = merged.get(key)
            if existing is None:
                item = dict(row)
                item["providerIds"] = {provider_id: row.get("providerSeriesId")}
                item["providersKnowing"] = [provider_name]
                merged[key] = item
                continue
            existing["providerIds"][provider_id] = row.get("providerSeriesId")
            existing["providersKnowing"].append(provider_name)
            existing["inLibrary"] = existing.get("inLibrary") or row.get("inLibrary")
            for field in _DISCOVERY_FILLABLE:
                if not existing.get(field) and row.get(field):
                    existing[field] = row[field]
                    # Say where a borrowed cover came from: GCD has none, and
                    # Metron's series records often do not either, so the art on
                    # a card is frequently not from the provider naming it.
                    if field == "cover":
                        existing["coverProvider"] = provider_name

    wanted = normalized_title(title_query)
    results = list(merged.values())
    results.sort(key=lambda item: (
        0 if normalized_title(item.get("title")) == wanted else 1,
        abs((item.get("yearBegan") or 0) - year_hint)
        if year_hint and item.get("yearBegan") else (9999 if year_hint else 0),
        -difflib.SequenceMatcher(None, normalized_title(item.get("title")), wanted).ratio(),
        # A run several providers agree on is more likely to be the real one.
        -len(item["providerIds"]),
        -(item.get("yearBegan") or 0),
    ))
    return results


def discover_series(query: str) -> dict[str, Any]:
    """Search every configured catalog and merge what they know.

    This used to stop at the first provider that answered. With Metron
    configured that meant Comic Vine and GCD were never asked for runs at all,
    and a weak Metron answer -- two loose matches -- counted as success and
    ended the search. GCD is the deepest catalog of the three for older, indie
    and reprint material, and it was never reached.

    The rate limits in _PROVIDER_MIN_INTERVAL_SECONDS are per provider, so
    asking all of them costs the slowest one rather than the sum.
    """
    cleaned, title_query, year_hint = _discovery_query_parts(query)
    if len(cleaned) < 2:
        raise ValueError("Enter at least two characters to discover a series")
    config = load_provider_config()
    library = _discovery_library_view()
    searches: list[tuple[str, str, Any]] = []
    metron = config.get("metron") or {}
    if metron.get("enabled") and metron.get("token"):
        searches.append(("metron", "Metron", functools.partial(
            discover_metron_series, cleaned, str(metron["token"]), library, hydrate=False)))
    comic_vine = config.get("comic_vine") or {}
    if comic_vine.get("enabled") and comic_vine.get("apiKey"):
        searches.append(("comic_vine", "Comic Vine", functools.partial(
            discover_comic_vine_series, cleaned, str(comic_vine["apiKey"]), library)))
    searches.append(("gcd", "Grand Comics Database", functools.partial(
        discover_gcd_series, cleaned, library)))

    by_provider: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(searches)) as pool:
        futures = {
            pool.submit(search): (provider_id, name) for provider_id, name, search in searches
        }
        for future in concurrent.futures.as_completed(futures):
            provider_id, name = futures[future]
            try:
                by_provider[provider_id] = (future.result() or {}).get("results") or []
            except Exception as exc:
                errors.append({"provider": name, "error": str(exc)})

    results = _merge_discovered_runs(by_provider, title_query, year_hint)
    if not results and len(errors) == len(searches):
        raise RuntimeError(
            errors[0]["error"] if len(errors) == 1
            else "No comic discovery provider is currently available"
        )
    # Answered means it replied, rows or none. Counting only providers with
    # rows left one that found nothing reading "searching" for good.
    answered = [name for provider_id, name, _ in searches if provider_id in by_provider]
    primary = next(
        (provider_id for provider_id in _DISCOVERY_PROVIDERS if by_provider.get(provider_id)),
        None,
    )
    result: dict[str, Any] = {
        "query": cleaned, "titleQuery": title_query, "yearHint": year_hint,
        "provider": next((name for pid, name, _ in searches if pid == primary), None),
        "providerId": primary,
        "results": results[:48],
        "providersChecked": [name for _, name, _ in searches],
        "providersAnswered": answered,
    }
    if errors:
        result["fallbacks"] = errors
    return result


# ---------------------------------------------------------------------------
# Search by creator and by publisher
#
# The same box as a title search, asked a second way: is this a person, or a
# publisher? Only a name the catalog knows outright counts -- "Saga" is not
# McNally Sagal -- and an ambiguous surname is offered back as a choice rather
# than guessed at.
# ---------------------------------------------------------------------------

PEOPLE_LOOKUP_DEADLINE_SECONDS = 25
# "Image" is Image Comics; "Boom" is BOOM! Studios.
_COMPANY_SUFFIXES = ("comics", "publishing", "entertainment", "press", "books", "studios", "media", "group")
# Comic Vine credits a manga artist with the magazine that serialised them.
_MAGAZINE_TITLE = re.compile(r"\b(?:magazine|weekly|monthly|jump|anthology)\b", re.I)


def _named_match(
    rows: Iterable[dict[str, Any]], query: str, *, company: bool = False,
) -> tuple[dict[str, Any] | None, list[str]]:
    """The one row a query names outright, else the names it might have meant."""
    wanted = normalized_person(query)
    tokens = wanted.split()
    if len(wanted) < 3:
        return None, []
    named = [
        (row, normalized_person(row.get("name")))
        for row in rows if isinstance(row, dict) and row.get("name") and row.get("id")
    ]
    exact = {wanted} | ({f"{wanted} {suffix}" for suffix in _COMPANY_SUFFIXES} if company else set())
    for row, name in named:
        if name in exact:
            return row, []
    # Whole words only: a person by any of their names, a company by the
    # start of its name.
    close = [
        row for row, name in named
        if (name.split()[:len(tokens)] == tokens if company else set(tokens) <= set(name.split()))
    ]
    if len(close) == 1:
        return close[0], []
    return None, [str(row["name"]) for row in close[:6]]


def _metron_creator_runs(
    query: str, year_hint: int | None, token: str, library: dict[str, Any], deadline: float,
) -> dict[str, Any]:
    """A creator's runs, from the issues Metron credits them on."""
    url = f"{METRON_API_BASE}/creator/?" + urllib.parse.urlencode({"name": query})
    creator, suggestions = _named_match(fetch_provider_json("metron", url, token).get("results") or [], query)
    if not creator:
        return {"didYouMean": suggestions}
    issues = _fetch_provider_pages(
        "metron", f"{METRON_API_BASE}/issue/?creator_id={creator['id']}", token,
        max_pages=5, deadline=deadline,
    )
    runs: dict[str, dict[str, Any]] = {}
    for row in issues:
        entry = _release_entry(row)
        if not entry or not entry["providerSeriesId"]:
            continue
        series_id = entry["providerSeriesId"]
        run = runs.get(series_id)
        if run is None:
            title = re.sub(r"\s+\((?:19|20)\d{2}\)$", "", entry["seriesTitle"]).strip()
            year = entry["seriesYear"]
            run = runs[series_id] = {
                "provider": "metron", "providerName": "Metron",
                "providerSeriesId": series_id, "providerIds": {"metron": series_id},
                "title": title, "yearBegan": year, "yearEnded": None,
                "yearLabel": str(year or "Unknown"), "publisher": None,
                "cover": entry["cover"], "medium": "comic", "creditedIssues": 0,
                "inLibrary": (normalized_title(title), str(year or "")) in library["keys"],
            }
        run["creditedIssues"] += 1
        if entry["number"] == "1" and entry["cover"]:
            run["cover"] = entry["cover"]
    found = [run for run in runs.values() if not year_hint or run["yearBegan"] == year_hint]
    found.sort(key=lambda run: (-run["creditedIssues"], -(run["yearBegan"] or 0), run["title"]))
    return {"creator": {"name": creator["name"], "provider": "Metron"}, "runs": found[:24]}


def _comic_vine_creator_runs(
    query: str, year_hint: int | None, api_key: str, library: dict[str, Any],
) -> dict[str, Any]:
    """A creator's runs from Comic Vine, which is the one that knows manga."""
    url = f"{COMIC_VINE_API_BASE}/search/?" + urllib.parse.urlencode({
        "api_key": api_key, "format": "json", "resources": "person", "query": query,
        "limit": 10, "field_list": "id,name",
    })
    payload = fetch_provider_json("comic_vine", url, api_key)
    if str(payload.get("status_code")) != "1":
        raise ValueError(str(payload.get("error") or "Comic Vine creator search failed"))
    person, suggestions = _named_match(payload.get("results") or [], query)
    if not person:
        return {"didYouMean": suggestions}
    detail_url = f"{COMIC_VINE_API_BASE}/person/4040-{person['id']}/?" + urllib.parse.urlencode({
        "api_key": api_key, "format": "json", "field_list": "id,name,volume_credits",
    })
    detail = fetch_provider_json("comic_vine", detail_url, api_key).get("results") or {}
    volume_ids = [str(item["id"]) for item in (detail.get("volume_credits") or []) if item.get("id")][:100]
    found: list[dict[str, Any]] = []
    if volume_ids:
        volumes_url = f"{COMIC_VINE_API_BASE}/volumes/?" + urllib.parse.urlencode({
            "api_key": api_key, "format": "json", "filter": f"id:{'|'.join(volume_ids)}",
            "limit": 100, "field_list": ",".join(COMIC_VINE_VOLUME_FIELDS),
        })
        english_only = preferred_language() == "en"
        for row in fetch_provider_json("comic_vine", volumes_url, api_key).get("results") or []:
            item = _comic_vine_volume_row(row, library["keys"], english_only)
            if not item or _MAGAZINE_TITLE.search(item["title"]):
                continue
            if year_hint and item["yearBegan"] != year_hint:
                continue
            item.pop("description", None)
            item["providerIds"] = {"comic_vine": item["providerSeriesId"]}
            found.append(item)
    found.sort(key=lambda run: (-(run["issueCount"] or 0), -(run["yearBegan"] or 0), run["title"]))
    return {"creator": {"name": person["name"], "provider": "Comic Vine"}, "runs": found[:24]}


def _metron_publisher_runs(
    query: str, year_hint: int | None, token: str, library: dict[str, Any],
) -> dict[str, Any]:
    """Runs a publisher began in one year: the one asked for, else this one.

    A publisher's whole list is thousands of runs in alphabetical order, which
    answers nothing. A year makes it a question worth asking.
    """
    url = f"{METRON_API_BASE}/publisher/?" + urllib.parse.urlencode({"name": query})
    publisher, suggestions = _named_match(
        fetch_provider_json("metron", url, token).get("results") or [], query, company=True,
    )
    if not publisher:
        return {"didYouMean": suggestions}
    year = year_hint or dt.date.today().year
    series_url = f"{METRON_API_BASE}/series/?" + urllib.parse.urlencode({
        "publisher_id": publisher["id"], "year_began": year,
    })
    found = []
    for rank, row in enumerate(fetch_provider_json("metron", series_url, token).get("results") or []):
        item = _metron_series_row(row, library["keys"], rank)
        if not item or (item["yearBegan"] and item["yearBegan"] != year):
            continue
        item.pop("_searchRank", None)
        item["publisher"] = item.get("publisher") or publisher["name"]
        item["providerIds"] = {"metron": item["providerSeriesId"]}
        found.append(item)
    found.sort(key=lambda run: (-(run["issueCount"] or 0), run["title"]))
    return {"publisher": {"name": publisher["name"], "provider": "Metron"}, "year": year, "runs": found[:24]}


def _merge_creator_runs(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Metron's runs for a person, filled out by what Comic Vine has on them."""
    merged = [dict(run, providerIds=dict(run.get("providerIds") or {})) for run in primary]
    index = {(normalized_title(run["title"]), str(run.get("yearBegan") or "")): run for run in merged}
    for run in extra:
        existing = index.get((normalized_title(run["title"]), str(run.get("yearBegan") or "")))
        if existing is None:
            merged.append(run)
            continue
        existing["providerIds"].update(run.get("providerIds") or {})
        for field in ("cover", "publisher", "issueCount", "medium"):
            if not existing.get(field) and run.get(field):
                existing[field] = run[field]
    return merged


def discover_people_and_publishers(query: str) -> dict[str, Any]:
    """The runs a query names as a creator or a publisher rather than a title.

    Its own endpoint, asked after the title search settles: Metron takes one
    request every 3.2s from everyone, so asking in parallel would put these
    lookups in front of the title results the reader is waiting on.
    """
    cleaned, title_query, year_hint = _discovery_query_parts(query)
    if len(cleaned) < 2:
        raise ValueError("Enter at least two characters to discover a series")
    config = load_provider_config()
    library = _discovery_library_view()
    started = time.monotonic()
    lookups: list[tuple[str, Any]] = []
    metron = config.get("metron") or {}
    if metron.get("enabled") and metron.get("token"):
        token = str(metron["token"])
        lookups.append(("metronCreator", functools.partial(
            _metron_creator_runs, title_query, year_hint, token, library,
            started + PEOPLE_LOOKUP_DEADLINE_SECONDS - 6)))
        lookups.append(("publisher", functools.partial(
            _metron_publisher_runs, title_query, year_hint, token, library)))
    comic_vine = config.get("comic_vine") or {}
    if comic_vine.get("enabled") and comic_vine.get("apiKey"):
        lookups.append(("comicVineCreator", functools.partial(
            _comic_vine_creator_runs, title_query, year_hint, str(comic_vine["apiKey"]), library)))

    answers: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    pending: list[str] = []
    if lookups:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(lookups))
        futures = {pool.submit(work): name for name, work in lookups}
        done, not_done = concurrent.futures.wait(futures, timeout=PEOPLE_LOOKUP_DEADLINE_SECONDS)
        # A lookup still going is left to finish; it warms the cache, so
        # asking again a moment later answers at once.
        pool.shutdown(wait=False)
        for future in done:
            try:
                answers[futures[future]] = future.result() or {}
            except Exception as exc:
                errors.append({"lookup": futures[future], "error": str(exc)})
        pending = [futures[future] for future in not_done]

    creator_matches: list[dict[str, Any]] = []
    metron_creator = answers.get("metronCreator") or {}
    vine_creator = answers.get("comicVineCreator") or {}
    if metron_creator.get("creator"):
        runs = metron_creator.get("runs") or []
        providers = ["Metron"]
        if vine_creator.get("creator") and normalized_person(vine_creator["creator"]["name"]) == normalized_person(metron_creator["creator"]["name"]):
            runs = _merge_creator_runs(runs, vine_creator.get("runs") or [])
            providers.append("Comic Vine")
            vine_creator = {}
        creator_matches.append({"name": metron_creator["creator"]["name"], "providers": providers, "runs": runs})
    if vine_creator.get("creator"):
        creator_matches.append({
            "name": vine_creator["creator"]["name"], "providers": ["Comic Vine"],
            "runs": vine_creator.get("runs") or [],
        })
    publisher = answers.get("publisher") or {}
    publisher_matches = [{
        "name": publisher["publisher"]["name"], "year": publisher.get("year"),
        "runs": publisher.get("runs") or [],
    }] if publisher.get("publisher") else []

    suggestions: list[str] = []
    if not creator_matches and not publisher_matches:
        seen: set[str] = set()
        for name in [*metron_creator.get("didYouMean", []), *vine_creator.get("didYouMean", []),
                     *publisher.get("didYouMean", [])]:
            if normalized_person(name) not in seen:
                seen.add(normalized_person(name))
                suggestions.append(name)
    return {
        "query": cleaned, "titleQuery": title_query, "yearHint": year_hint,
        "creatorMatches": creator_matches, "publisherMatches": publisher_matches,
        "didYouMean": suggestions[:6], "stillLooking": bool(pending),
        "errors": errors,
    }


def sync_next_run_creators(store: CatalogStore | None = None) -> bool:
    """Ask a catalog who made one run whose files do not say. False when none is due."""
    store = store or catalog_store()
    config = load_provider_config()
    available = [
        provider for provider, field in (("metron", "token"), ("comic_vine", "apiKey"))
        if (config.get(provider) or {}).get("enabled") and (config.get(provider) or {}).get(field)
        and store.metadata_provider_available(provider)
    ]
    claim = store.claim_run_creator_sync(available) if available else None
    if not claim:
        return False
    provider = claim["provider"]
    try:
        credential = _provider_credential(provider)
        if provider == "metron":
            issue = fetch_provider_json(
                "metron", f"{METRON_API_BASE}/issue/{claim['providerId']}/", credential,
            )
            creators = [
                {"name": item["name"], "roles": [role.casefold() for role in item["roles"]]}
                for item in _metron_discovery_creators(issue)
            ]
        else:
            url = f"{COMIC_VINE_API_BASE}/volume/4050-{claim['providerId']}/?" + urllib.parse.urlencode({
                "api_key": credential, "format": "json", "field_list": "people",
            })
            people = (fetch_provider_json("comic_vine", url, credential).get("results") or {}).get("people") or []
            # A volume lists everyone credited anywhere in it; the few named
            # most often are who made it.
            people = sorted(
                (person for person in people if person.get("name")),
                key=lambda person: -int(person.get("count") or 0),
            )[:6]
            creators = [
                {"name": person["name"], "roles": ["creator"], "count": int(person.get("count") or 0) or None}
                for person in people
            ]
    except Exception as exc:
        limited = _rate_limit_from_error(provider, exc)
        if limited:
            store.record_metadata_provider_outcome(provider, str(limited), limited.retry_after_seconds)
            store.release_run_creator_sync(claim["seriesRunId"])
        return True
    store.set_run_catalog_creators(claim["seriesRunId"], provider, creators)
    store.record_metadata_provider_outcome(provider, minimum_delay_seconds=3)
    return True


# ---------------------------------------------------------------------------
# The release calendar
#
# Discover's two shelves answer "what came out, and what is coming". Only
# Metron can: comics.org's API is fetch-by-id and search, and Comic Vine has no
# ship-date browse, so with Metron unconfigured the shelves say so rather than
# pretending to be empty.
# ---------------------------------------------------------------------------

# Comics ship on Wednesday. The shelves are labelled by that date, and the
# window runs to the following Tuesday so a title with an off-Wednesday store
# date still lands on the week it belongs to.
_SHIP_WEEKDAY = 2


def _ship_week(anchor: dt.date, weeks_back: int = 0) -> tuple[dt.date, dt.date]:
    """The Wednesday-to-Tuesday shipping week containing or preceding `anchor`."""
    wednesday = anchor + dt.timedelta(days=(_SHIP_WEEKDAY - anchor.weekday()) % 7)
    wednesday -= dt.timedelta(weeks=weeks_back)
    return wednesday, wednesday + dt.timedelta(days=6)


def _release_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    """One issue from Metron's issue list, as the shelf needs it.

    The list rows carry no publisher -- that lives on the series record, and
    asking for it per row would be an N+1 burst against a 20-per-minute limit.
    The card does not show one, so nothing is lost on screen; it is only the
    ranking that has to do without.
    """
    series = row.get("series") or {}
    title = str(series.get("name") or "").strip()
    number = str(row.get("number") or "").strip()
    if not title or not number or not row.get("id"):
        return None
    return {
        "providerIssueId": str(row["id"]),
        "providerSeriesId": str(series.get("id") or "") or None,
        "seriesTitle": title,
        "seriesYear": _provider_year(series.get("year_began")),
        "number": number,
        # Metron pre-formats "Series (Year) #N"; the card wants the short form.
        "title": f"{title} #{number}",
        "cover": row.get("image"),
        "storeDate": _provider_date(row.get("store_date")),
        "coverDate": _provider_date(row.get("cover_date")),
    }


def fetch_release_calendar(start: dt.date, end: dt.date, token: str) -> list[dict[str, Any]]:
    """Every issue shipping between two dates, in provider order.

    A week is one request today -- 57 issues came back whole for 2026-09-02 --
    but the endpoint is paginated and a heavy week could split, so this follows
    `next` rather than assuming. Responses go through the provider cache, which
    is what keeps a settled week from ever being fetched twice.
    """
    url = f"{METRON_API_BASE}/issue/?" + urllib.parse.urlencode({
        "store_date_range_after": start.isoformat(),
        "store_date_range_before": end.isoformat(),
    })
    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    while url and url not in seen_urls and len(seen_urls) < 20:
        seen_urls.add(url)
        payload = fetch_provider_json("metron", url, token)
        for row in payload.get("results") or []:
            entry = _release_entry(row)
            if entry:
                entries.append(entry)
        url = str(payload.get("next") or "")
    return entries


def _issue_key(number: Any) -> str:
    """Compare issue numbers without arguing about leading zeros.

    Providers write "1", "01" and "001" for the same comic, and the shelf has
    to line a release up against the library's own record of it.
    """
    cleaned = str(number or "").strip()
    return cleaned.lstrip("0") or cleaned


def _library_relevance() -> dict[str, dict[str, Any]]:
    """What the library already says about a run, keyed for provider titles.

    Keyed by title-and-year and by title alone, because a provider's start year
    and the library's do not always agree and a title match is still worth more
    than nothing.

    Per-issue as well as per-run: a shelf card is one comic, and owning Archie
    #1 says nothing about #2. Run-level facts alone meant the shelf offered a
    comic already on disk, and pulling it again is a second acquisition request
    for a file you have.
    """
    catalog = catalog_store().catalog()
    queued_by_run: dict[str, set[str]] = {}
    for request in catalog.get("requests") or []:
        for job in request.get("jobs") or []:
            if job.get("status") in {"fulfilled", "cancelled"}:
                continue
            queued_by_run.setdefault(str(job.get("seriesId")), set()).add(
                _issue_key(job.get("issueNumber"))
            )
        # An issue asked for before it ships has no job yet -- reconcile makes
        # jobs only for released issues -- so pulling next week's comic left
        # nothing to report and the card went on offering it. The open
        # request's own issue list is the record that it was asked for.
        if request.get("status") == "open":
            for issue in request.get("issues") or []:
                queued_by_run.setdefault(str(issue.get("seriesId")), set()).add(
                    _issue_key(issue.get("number"))
                )
    relevance: dict[str, dict[str, Any]] = {}
    for item in catalog.get("series") or []:
        run_id = str(item.get("id"))
        entry = {
            "runId": run_id,
            "following": item.get("monitoringStatus") == "monitored",
            "publisher": item.get("publisher"),
            "owned": {
                _issue_key(issue.get("number"))
                for issue in item.get("issues") or []
                if issue.get("ownership") not in {None, "", "unowned"}
            },
            "queued": queued_by_run.get(run_id, set()),
        }
        title = normalized_title(item.get("title"))
        relevance.setdefault(title, entry)
        relevance[f"{title}|{item.get('year') or ''}"] = entry
    return relevance


def _rank_releases(
    entries: list[dict[str, Any]], relevance: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Order a week so the issues worth seeing first are first.

    Runs already followed, then runs owned, then first issues, then everything
    else by title. A #1 stands in for the publisher weighting the shelves were
    meant to have: Metron's list rows carry no publisher, and fetching one per
    series would be dozens of throttled requests for a sort key. It is also the
    better signal for a screen called Discover -- a first issue is the thing
    someone browsing can actually start.
    """
    relevance = _library_relevance() if relevance is None else relevance
    ranked = []
    for entry in entries:
        title = normalized_title(entry["seriesTitle"])
        known = relevance.get(f"{title}|{entry.get('seriesYear') or ''}") or relevance.get(title)
        item = dict(entry)
        number = _issue_key(entry["number"])
        item["inLibrary"] = bool(known)
        item["following"] = bool(known and known["following"])
        # Per issue, not per run: the card offers one comic, and the button has
        # to say whether that one is already here or already asked for.
        item["owned"] = bool(known and number in known["owned"])
        item["queued"] = bool(known and number in known["queued"])
        item["runId"] = known["runId"] if known else None
        item["publisher"] = known["publisher"] if known else None
        first_issue = entry["number"] in {"1", "01", "001"}
        item["_rank"] = (
            0 if item["following"] else 1 if item["inLibrary"] else 2 if first_issue else 3,
            normalized_title(entry["seriesTitle"]),
            _issue_sort_key(entry["number"]),
        )
        ranked.append(item)
    ranked.sort(key=lambda item: item["_rank"])
    for item in ranked:
        item.pop("_rank", None)
    return ranked


def _issue_sort_key(number: str) -> tuple[int, float, str]:
    """Numeric where it can be, so #2 precedes #10 and #Annual sorts last."""
    match = re.match(r"^(\d+(?:\.\d+)?)", str(number or "").strip())
    return (0, float(match.group(1)), "") if match else (1, 0.0, str(number or ""))


def release_calendar(today: dt.date | None = None) -> dict[str, Any]:
    """Both shelves: the week that shipped, and the week that is coming."""
    config = load_provider_config()
    metron = config.get("metron") or {}
    if not (metron.get("enabled") and metron.get("token")):
        return {
            "available": False,
            "reason": "Connect Metron in Settings to see what is shipping. "
                      "It is the only source with comic shop release dates.",
        }
    anchor = today or dt.datetime.now().astimezone().date()
    upcoming_start, upcoming_end = _ship_week(anchor)
    latest_start, latest_end = _ship_week(anchor, weeks_back=1)
    token = str(metron["token"])
    # One projection for both shelves; building it is not free.
    relevance = _library_relevance()
    shelves = {}
    for name, (start, end) in (
        ("latest", (latest_start, latest_end)),
        ("upcoming", (upcoming_start, upcoming_end)),
    ):
        try:
            issues = _rank_releases(fetch_release_calendar(start, end, token), relevance)
            shelves[name] = {"date": start.isoformat(), "issues": issues}
        except Exception as exc:
            # One shelf failing must not take the other with it.
            shelves[name] = {"date": start.isoformat(), "issues": [], "error": str(exc)}
    return {"available": True, **shelves}


def _import_gcd_run(query: str, provider_series_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bring one GCD run into the library, with its issue list.

    GCD's half of _import_provider_run. It lived only inside following a run,
    so a GCD run could be followed or nothing -- never pulled in part.
    """
    cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
    provider_series_id = str(provider_series_id or "").strip()
    if len(cleaned) < 2 or not re.fullmatch(r"\d+", provider_series_id):
        raise ValueError("Choose a valid discovered publication run")
    candidates = rank_gcd_series_runs(
        {"title": cleaned, "aliases": [], "ownedIssueNumbers": []},
        _gcd_discovery_search_rows(cleaned),
    )
    candidate = next(
        (item for item in candidates if item["providerSeriesId"] == provider_series_id), None
    )
    if not candidate:
        raise ValueError("That publication run is no longer available in the search results")
    series = candidate["_series"]
    entries, metadata = _hydrate_gcd_issue_entries_with_status(
        _gcd_canonical_issue_entries(series), provider_series_id
    )
    if not entries:
        raise ValueError("The selected publication run has no usable issue list")
    store = catalog_store()
    run = store.ensure_provider_series_run(
        "gcd", provider_series_id, candidate["title"],
        candidate.get("yearBegan"), candidate.get("publisher"),
        run_format="manga" if manga_edition(candidate.get("publisher")) == "manga" else None,
    )
    evidence = _run_end_evidence(candidate.get("yearEnded"), modelled="yearEnded" in candidate)
    store.apply_issue_list(
        int(run["id"]), "gcd", provider_series_id, candidate.get("apiUrl") or "", entries,
        status=_run_catalog_status(evidence), end_evidence=evidence,
        detail=f"Added from Discover with {len(entries)} known issues.",
        source="added from library discovery search",
    )
    return run, {
        "entries": entries, "metadata": metadata,
        "publisher": candidate.get("publisher"), "year": candidate.get("yearBegan"),
    }


def request_discovered_gcd_series(
    query: str, provider_series_id: str, acquisition_preference: str = "either"
) -> dict[str, Any]:
    """Import one discovered run and immediately place its missing issues on the wanted list."""
    run, fetched = _import_gcd_run(query, provider_series_id)
    request = catalog_store().create_acquisition_request(
        "series", int(run["id"]), acquisition_preference, True
    )
    _start_automatic_release_grabs(request)
    return {
        "series": {**run, "publisher": fetched["publisher"], "year": fetched["year"]},
        "request": request,
        "issueCount": len(fetched["entries"]),
        "metadata": fetched["metadata"],
    }


def _find_gcd_series_runs(series_run_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    context = catalog_store().get_series_sync_context(series_run_id)
    search_names = []
    for value in [context["title"], *(context.get("aliases") or [])]:
        if value and normalized_title(value) not in {normalized_title(item) for item in search_names}:
            search_names.append(value)
        if len(search_names) == 3:
            break
    urls = [f"{GCD_API_BASE}/series/name/{urllib.parse.quote(name, safe='')}/" for name in search_names]
    results: list[dict[str, Any]] = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as executor:
        futures = [executor.submit(fetch_gcd_json, url) for url in urls]
        for future in futures:
            try:
                results.extend(future.result().get("results") or [])
            except Exception as exc:
                errors.append(str(exc))
    if not results and errors:
        raise RuntimeError(errors[0])
    return context, rank_gcd_series_runs(context, results)


def find_gcd_series_runs(series_run_id: int) -> dict[str, Any]:
    context, candidates = _find_gcd_series_runs(series_run_id)
    public_candidates = [{key: value for key, value in candidate.items() if key != "_series"} for candidate in candidates]
    return {
        "series": {
            "id": str(context["id"]), "title": context["title"], "year": context.get("year"),
            "publisher": context.get("publisher"), "ownedIssueNumbers": context.get("ownedIssueNumbers") or [],
        },
        "candidates": public_candidates,
        "provider": "Grand Comics Database",
        "guidance": "Choose the publication run, not a collected volume. Nothing is linked until you confirm.",
    }


def confirm_gcd_series_run(series_run_id: int, provider_series_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d+", provider_series_id):
        raise ValueError("A valid GCD series run is required")
    _, candidates = _find_gcd_series_runs(series_run_id)
    candidate = next((item for item in candidates if item["providerSeriesId"] == provider_series_id), None)
    if not candidate:
        raise ValueError("That GCD series run is no longer available in the current search results")
    series = candidate["_series"]
    entries = _hydrate_gcd_issue_entries(
        _gcd_canonical_issue_entries(series), provider_series_id
    )
    if not entries:
        raise ValueError("The selected GCD series has no usable issue descriptors")
    evidence = _run_end_evidence(series.get("year_ended"), modelled="year_ended" in series)
    status = _run_catalog_status(evidence)
    detail = (
        f"You confirmed GCD series {provider_series_id}; {len(entries)} canonical issues were imported "
        "and cover variants were collapsed by issue number."
    )
    store = catalog_store()
    # Confirming a match has to undo what the old one left behind. Otherwise
    # apply_issue_list's COALESCE only fills blanks, and a title written by a
    # comic that turned out to be something else -- "Numéro 2", from a French
    # edition filed as Saga #2 -- survives the correction that was meant to
    # remove it. Manual corrections are layered at read time and are untouched.
    rebuilt = store.rebuild_series_run(series_run_id)
    applied = store.apply_issue_list(
        series_run_id, "gcd", provider_series_id, series.get("api_url") or "", entries,
        status=status, detail=detail, source="user-confirmed series run",
        end_evidence=evidence,
    )
    return {**applied, "rebuilt": rebuilt}


def confirm_gcd_series_collection(
    series_run_id: int, provider_series_ids: list[str], collection_name: str,
    acquisition_preference: str = "either", include_specials: bool = True,
) -> dict[str, Any]:
    """Import several related GCD runs as one monitorable Collection."""
    selected_ids = list(dict.fromkeys(str(value) for value in provider_series_ids))
    if not selected_ids or any(not re.fullmatch(r"\d+", value) for value in selected_ids):
        raise ValueError("Select at least one valid GCD publication run")
    context, candidates = _find_gcd_series_runs(series_run_id)
    collection_name = re.sub(r"\s+", " ", collection_name).strip() or context["title"]
    selected = [candidate for candidate in candidates if candidate["providerSeriesId"] in selected_ids]
    if {candidate["providerSeriesId"] for candidate in selected} != set(selected_ids):
        raise ValueError("One or more selected publication runs are no longer available")
    store = catalog_store()
    imported = []
    for candidate in selected:
        series = candidate["_series"]
        entries = _hydrate_gcd_issue_entries(
            _gcd_canonical_issue_entries(series), candidate["providerSeriesId"]
        )
        if not entries:
            continue
        run = store.ensure_provider_series_run(
            "gcd", candidate["providerSeriesId"], candidate["title"],
            candidate.get("yearBegan"), candidate.get("publisher") or context.get("publisher"),
        )
        evidence = _run_end_evidence(
            candidate.get("yearEnded"), modelled="yearEnded" in candidate
        )
        store.apply_issue_list(
            int(run["id"]), "gcd", candidate["providerSeriesId"], series.get("api_url") or "", entries,
            status=_run_catalog_status(evidence), end_evidence=evidence,
            detail=f"Imported as part of the reviewed {collection_name} Collection structure.",
            source="user-confirmed collection structure",
        )
        imported.append({**candidate, "seriesId": run["id"]})
    if not imported:
        raise ValueError("The selected runs did not contain usable issue lists")

    matched_volumes = store.reassign_matching_editions(
        series_run_id,
        [{"id": item["seriesId"], "title": item["title"]} for item in imported],
    )

    current_catalog = store.catalog()
    current_series = next(
        (item for item in current_catalog["series"] if item["id"] == str(series_run_id)), None
    )
    family_id = (current_series.get("family") or {}).get("id") if current_series else None
    all_run_ids = list(dict.fromkeys([series_run_id, *(int(item["seriesId"]) for item in imported)]))
    if family_id:
        for run_id in all_run_ids:
            store.set_series_run_family(run_id, int(family_id))
    else:
        family = store.create_series_family(collection_name, all_run_ids)
        family_id = family["id"]

    arcs = []
    used_names: set[str] = set()
    normalized_collection = normalized_title(collection_name)
    for item in imported:
        title = item["title"]
        if ":" in title:
            arc_name = title.split(":", 1)[1].strip()
        elif normalized_title(title) == normalized_collection:
            arc_name = "Original series"
        else:
            arc_name = title
        base_name = arc_name
        suffix = 2
        while normalized_title(arc_name) in used_names:
            arc_name = f"{base_name} ({suffix})"
            suffix += 1
        used_names.add(normalized_title(arc_name))
        arc_type = "specials" if "one-shot" in str(item.get("publishingFormat") or "").lower() else "main"
        arcs.append({"name": arc_name, "type": arc_type, "runIds": [item["seriesId"]]})
    structured = store.set_story_structure(int(family_id), arcs)
    monitoring = store.set_collection_monitoring(
        int(family_id), acquisition_preference, include_specials
    )
    return {
        "collection": structured["collection"], "arcCount": structured["arcCount"],
        "runCount": len(imported),
        "issueCount": sum(item["issueCount"] for item in imported),
        "matchedVolumeCount": matched_volumes["count"],
        "monitoring": monitoring,
    }


def sync_gcd_issue_catalog(series_run_id: int) -> dict[str, Any]:
    """Import one canonical issue number per GCD issue, excluding cover variants."""
    store = catalog_store()
    context = store.get_series_sync_context(series_run_id)
    known_issue_ids = set(context["knownGcdIssueIds"])
    if not known_issue_ids:
        raise ValueError("A verified GCD single-issue match is required before importing a complete issue list")
    confirmed_series_id = context.get("gcdSeriesId")
    if confirmed_series_id:
        direct_url = f"{GCD_API_BASE}/series/{confirmed_series_id}/?format=json"
        try:
            direct_series = fetch_gcd_json(direct_url)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or not context.get("gcdIssueEntries"):
                raise
            retained_entries = context["gcdIssueEntries"]
            direct_series = {
                "name": context["title"], "country": "us", "language": "en",
                "year_began": context.get("year"), "publisher": context.get("publisher"),
                "publishing_format": "series",
                "api_url": context.get("gcdSeriesApiUrl") or f"{GCD_API_BASE}/series/{confirmed_series_id}/",
                "active_issues": [entry.get("api_url") for entry in retained_entries],
                "issue_descriptors": [str(entry["number"]) for entry in retained_entries],
            }
        results = [direct_series] if isinstance(direct_series, dict) else []
    else:
        query_url = f"{GCD_API_BASE}/series/name/{urllib.parse.quote(context['title'], safe='')}/"
        results = (fetch_gcd_json(query_url).get("results") or [])
    ranked = []
    target_title = normalized_title(context["title"])
    for series in results:
        if series.get("country") not in {None, "", "us"} or series.get("language") not in {None, "", "en"}:
            continue
        publishing_format = (series.get("publishing_format") or "").lower()
        if "collected edition" in publishing_format:
            continue
        issue_urls = series.get("active_issues") or []
        provider_ids = {
            match.group(1)
            for url in issue_urls
            if (match := re.search(r"/issue/(\d+)/?", url or ""))
        }
        overlap = len(known_issue_ids & provider_ids)
        if overlap == 0:
            continue
        similarity = difflib.SequenceMatcher(None, normalized_title(series.get("name")), target_title).ratio()
        score = overlap * 100 + int(similarity * 40)
        if context.get("year") and series.get("year_began") == context["year"]:
            score += 25
        if "ongoing" in publishing_format or "limited" in publishing_format:
            score += 10
        ranked.append((score, series))
    if not ranked:
        raise ValueError("GCD did not return a series containing the verified local issue IDs")
    ranked.sort(key=lambda item: item[0], reverse=True)
    series = ranked[0][1]
    series_id = _gcd_series_id(series)
    if not series_id:
        raise ValueError("The matched GCD series is missing a provider ID")
    base_entries = _gcd_canonical_issue_entries(series)
    retained_by_provider = {
        str(entry.get("provider_id")): entry for entry in context.get("gcdIssueEntries") or []
    }
    for entry in base_entries:
        retained = retained_by_provider.get(str(entry.get("provider_id"))) or {}
        entry["title"] = retained.get("title")
        entry["publication_year"] = retained.get("publication_year")
    entries, metadata = _hydrate_gcd_issue_entries_with_status(base_entries, series_id)
    if not entries:
        raise ValueError("The matched GCD series has no usable issue descriptors")
    evidence = _run_end_evidence(series.get("year_ended"), modelled="year_ended" in series)
    status = _run_catalog_status(evidence)
    detail = (
        f"Matched GCD series {series_id} using {len(known_issue_ids)} verified local issue ID"
        f"{'s' if len(known_issue_ids) != 1 else ''}; cover variants were collapsed by issue number."
    )
    if metadata["status"] == "deferred":
        detail += " Existing issue details were retained; missing titles and dates will repair automatically after the provider limit resets."
    else:
        detail += (
            f" Loaded {metadata['titleCount']} issue title"
            f"{'s' if metadata['titleCount'] != 1 else ''} and {metadata['dateCount']} publication date"
            f"{'s' if metadata['dateCount'] != 1 else ''} in one series request."
        )
    result = store.apply_issue_list(
        series_run_id, "gcd", series_id, series.get("api_url") or "",
        entries, status=status, detail=detail, end_evidence=evidence,
    )
    result["metadata"] = metadata
    return result


def _provider_date(value: Any) -> str | None:
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", str(value or ""))
    if not match or match.group(2) == "00" or match.group(3) == "00":
        return None
    return match.group(0)


def _provider_year(*values: Any) -> int | None:
    for value in values:
        match = re.search(r"\b((?:19|20)\d{2})\b", str(value or ""))
        if match:
            return int(match.group(1))
    return None


def _fetch_provider_pages(
    provider_id: str,
    url: str,
    credential: str,
    result_key: str = "results",
    max_pages: int = 20,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Every page of a list, or as many as arrive before `deadline` (monotonic)."""
    rows: list[dict[str, Any]] = []
    next_url: str | None = url
    for _ in range(max_pages):
        if not next_url or (deadline is not None and rows and time.monotonic() > deadline):
            break
        payload = fetch_provider_json(provider_id, next_url, credential)
        page_rows = payload.get(result_key) or []
        rows.extend(row for row in page_rows if isinstance(row, dict))
        next_url = payload.get("next")
    return rows


def _run_end_evidence(ended_year: Any, *, modelled: bool) -> dict[str, Any]:
    """What a provider actually said about the year a run ended.

    `modelled` is whether the provider publishes an end year at all. Comic Vine
    does not, and treating its silence as "still publishing" is how a run that
    finished years ago kept its Active Run badge until someone opened its
    drawer and a provider that does model it happened to run last. Silence is
    recorded as silence, and silence never overwrites another provider's answer.
    """
    year = _provider_year(ended_year)
    if not modelled:
        return {"state": "unknown", "year": None}
    if year and year <= time.gmtime().tm_year:
        return {"state": "ended", "year": year}
    return {"state": "ongoing", "year": year}


def _run_catalog_status(evidence: dict[str, Any]) -> str:
    """The issue-list coverage claim that follows from the lifecycle evidence."""
    return "complete" if evidence.get("state") == "ended" else "complete_to_date"


def _metron_issue_entries(
    context: dict[str, Any], token: str
) -> tuple[str, str, list[dict[str, Any]], int | None]:
    if context.get("metronSeriesId"):
        series_id = str(context["metronSeriesId"])
    else:
        filters: dict[str, Any] = {"q": context["title"]}
        if context.get("gcdSeriesId"):
            filters = {"gcd_id": context["gcdSeriesId"]}
        elif context.get("year"):
            filters["year_began"] = context["year"]
        search_url = f"{METRON_API_BASE}/series/?" + urllib.parse.urlencode(filters)
        payload = fetch_provider_json("metron", search_url, token)
        candidates = payload.get("results") or []
        exact = [
            candidate for candidate in candidates
            if normalized_title(str(candidate.get("series") or candidate.get("name") or "").split("(")[0])
            == normalized_title(context["title"])
        ]
        if context.get("year"):
            year_matches = [
                candidate for candidate in exact
                if _provider_year(candidate.get("year_began")) == _provider_year(context["year"])
            ]
            if context.get("strictYear") and not year_matches:
                raise ValueError("Metron did not return this title for the requested publication year")
            exact = year_matches or exact
        if len(exact) != 1:
            raise ValueError("Metron could not identify one unambiguous matching series")
        series_id = str(exact[0]["id"])
    detail = fetch_provider_json("metron", f"{METRON_API_BASE}/series/{series_id}/", token)
    issue_rows = _fetch_provider_pages(
        "metron", f"{METRON_API_BASE}/series/{series_id}/issue_list/", token
    )
    entries = []
    for issue in issue_rows:
        number = str(issue.get("number") or "").strip()
        if not number:
            continue
        publication_date = _provider_date(issue.get("store_date")) or _provider_date(issue.get("cover_date"))
        title = str(issue.get("title") or issue.get("name") or "").strip() or None
        entries.append({
            "number": number, "provider_id": str(issue.get("id") or "") or None,
            "api_url": f"{METRON_API_BASE}/issue/{issue['id']}/" if issue.get("id") else None,
            "title": title, "publication_date": publication_date,
            "publication_year": _provider_year(publication_date, issue.get("cover_date")),
            "cover": issue.get("image"),
        })
    if not entries:
        raise ValueError("Metron returned no issues for the matched series")
    source_url = str(detail.get("resource_url") or f"{METRON_API_BASE}/series/{series_id}/")
    # Metron records the year a run ended. Returning it is what lets a finished
    # run be marked finished; discarded, every Metron match looked ongoing.
    return series_id, source_url, entries, _run_end_evidence(
        detail.get("year_end"), modelled="year_end" in detail
    )


def _metron_display_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("name") or value.get("series") or value.get("title") or ""
        ).strip()
    return str(value or "").strip()


def _metron_reprint_coverage(reprints: Any) -> list[dict[str, Any]]:
    """Compatibility projection of preserved, ownership-neutral V2 evidence."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in metron_reprint_evidence(reprints):
        series, number = item["seriesLabel"], item["issueNumber"]
        if not series or not number:
            # Still retained in candidate.provider_evidence, not fabricated as
            # a numbered issue merely to fit this legacy display projection.
            continue
        relation_kind = item["relationKind"]
        key = (series, relation_kind)
        group = grouped.setdefault(key, {
            "series": series,
            "issues": [],
            "source": "Metron reprints",
            "confidence": "completeness unverified",
            "relation_kind": relation_kind,
            "provider_links": [],
        })
        if number not in group["issues"]:
            group["issues"].append(number)
        group["provider_links"].append(item)

    def issue_sort(value: str) -> tuple[int, float, str]:
        match = re.match(r"^(\d+(?:\.\d+)?)", value)
        return (0, float(match.group(1)), value.casefold()) if match else (1, 0, value.casefold())

    for group in grouped.values():
        group["issues"].sort(key=issue_sort)
        group["source_text"] = json.dumps({
            "source": "Metron reprints", "completeness": "unverified",
            "relationships": group["provider_links"],
        }, ensure_ascii=False)
    return list(grouped.values())


def _metron_collected_edition_candidates(
    parsed: ParsedFile, token: str, max_details: int = 4
) -> list[dict[str, Any]]:
    """Return exact collected-edition records with structured reprint evidence.

    This is deliberately used only by explicit Fix Match today. Bulk intake
    must not fan out into one detail request per file.
    """
    filters: dict[str, Any] = {"series_q": parsed.title}
    if parsed.volume not in {None, ""}:
        filters["number"] = parsed.volume
    rows = _fetch_provider_pages(
        "metron",
        f"{METRON_API_BASE}/issue/?" + urllib.parse.urlencode(filters),
        token,
        max_pages=2,
    )

    ranked: list[tuple[float, dict[str, Any]]] = []
    for rank, row in enumerate(rows):
        series_name = _metron_display_name(row.get("series"))
        similarity = difflib.SequenceMatcher(
            None, normalized_title(series_name), normalized_title(parsed.title)
        ).ratio()
        number = str(row.get("number") or "").strip().lstrip("0") or "0"
        wanted_number = str(parsed.volume or "").strip().lstrip("0") or "0"
        volume_match = parsed.volume in {None, ""} or number == wanted_number
        if similarity < 0.72 or not volume_match or not row.get("id"):
            continue
        ranked.append((similarity * 100 - rank, row))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    candidates: list[dict[str, Any]] = []
    for _, row in ranked[:max(1, max_details)]:
        detail = fetch_provider_json(
            "metron", f"{METRON_API_BASE}/issue/{row['id']}/", token
        )
        evidence = native_issue_evidence("metron", detail)
        series_type = _metron_display_name(evidence["publication"].get("series_type"))
        if series_type.casefold() not in {"trade paperback", "hardcover", "omnibus", "collected edition"}:
            # A single issue can itself be a reprint; that does not make it a
            # collection. The provider's native publication type is required.
            continue
        coverage = _metron_reprint_coverage(detail.get("reprints"))
        if not evidence["reprints"]:
            continue
        series_name = _metron_display_name(detail.get("series") or row.get("series"))
        similarity = difflib.SequenceMatcher(
            None, normalized_title(series_name), normalized_title(parsed.title)
        ).ratio()
        raw_isbn = str(detail.get("isbn") or "")
        isbns = []
        for match in re.finditer(r"[0-9Xx][0-9Xx\s-]{8,20}[0-9Xx]", raw_isbn):
            value = normalize_isbn(match.group(0))
            if valid_isbn(value) and value not in isbns:
                isbns.append(value)
        exact_isbn = bool(parsed.isbn and parsed.isbn in isbns)
        if not exact_isbn and similarity < 0.8:
            continue
        number = str(detail.get("number") or row.get("number") or "").strip()
        publication_date = _provider_date(detail.get("store_date")) or _provider_date(detail.get("cover_date"))
        score = round(similarity * 70) + 20
        reasons = [
            f"Metron series title similarity {round(similarity * 100)}%",
            f"Metron supplies {sum(len(group['issues']) for group in coverage)} structured reprint relationships",
        ]
        if parsed.volume not in {None, ""} and number.lstrip("0") == str(parsed.volume).lstrip("0"):
            score += 15
            reasons.append(f"Volume number {parsed.volume} matches")
        if exact_isbn:
            score += 100
            reasons.append("Exact ISBN matches")
        publisher = _metron_display_name(detail.get("publisher")) or None
        candidates.append({
            "source": "Metron",
            "source_id": str(detail.get("id") or row.get("id")),
            "record_type": "collected_edition",
            "title": series_name or parsed.title,
            "subtitle": str(detail.get("title") or "").strip() or None,
            "volume": int(number) if number.isdigit() else parsed.volume,
            "creators": [],
            "publisher": publisher,
            "description": evidence["description"],
            "provider_evidence": evidence,
            "publication_year": _provider_year(publication_date, detail.get("cover_date")),
            "publication_date": publication_date,
            "isbns": isbns,
            "cover": detail.get("image"),
            "url": detail.get("resource_url") or f"{METRON_API_BASE}/issue/{row['id']}/",
            "format": series_type,
            "search_query": parsed.title,
            "match_score": score,
            "match_reasons": reasons,
            "verification_status": "Collected edition found; Metron reprint links are retained but complete issue contents still need verification",
            "coverage_status": "reprint completeness unverified",
            "matched_edition": {
                "isbn_10": [value for value in isbns if len(value) == 10],
                "isbn_13": [value for value in isbns if len(value) == 13],
                "number_of_pages": detail.get("page") or detail.get("page_count"),
                "coverage": coverage,
            },
        })
    return candidates


def _comic_vine_issue_entries(
    context: dict[str, Any], api_key: str
) -> tuple[str, str, list[dict[str, Any]]]:
    if context.get("comicVineVolumeId"):
        detail_url = f"{COMIC_VINE_API_BASE}/volume/4050-{context['comicVineVolumeId']}/?" + urllib.parse.urlencode({
            "api_key": api_key, "format": "json",
            "field_list": ",".join(COMIC_VINE_VOLUME_FIELDS),
        })
        payload = fetch_provider_json("comic_vine", detail_url, api_key)
        if str(payload.get("status_code")) != "1" or not isinstance(payload.get("results"), dict):
            raise ValueError(str(payload.get("error") or "Comic Vine volume lookup failed"))
        volume = payload["results"]
    else:
        # Comic Vine's filtered volume listing is not relevance ordered and
        # can omit a recent same-name run from its first page (for example,
        # Batman 2025 behind Batman 1940).  Its search endpoint returns a
        # broader relevance-ranked set and is also what discovery uses.
        search_url = f"{COMIC_VINE_API_BASE}/search/?" + urllib.parse.urlencode({
            "api_key": api_key, "format": "json", "resources": "volume",
            "query": context["title"], "limit": 50,
            "field_list": ",".join(COMIC_VINE_VOLUME_FIELDS),
        })
        payload = fetch_provider_json("comic_vine", search_url, api_key)
        if str(payload.get("status_code")) != "1":
            raise ValueError(str(payload.get("error") or "Comic Vine series search failed"))
        candidates = [
            candidate for candidate in (payload.get("results") or [])
            if normalized_title(candidate.get("name")) == normalized_title(context["title"])
        ]
        expected_count = len(context.get("gcdIssueEntries") or []) or len(context.get("knownGcdIssueIds") or [])
        context_year = _provider_year(context.get("year"))
        context_publisher = normalized_title(context.get("publisher"))
        if context.get("strictYear") and context_year:
            year_matches = [
                candidate for candidate in candidates
                if _provider_year(candidate.get("start_year")) == context_year
            ]
            if not year_matches:
                raise ValueError("Comic Vine did not return this title for the requested publication year")
            candidates = year_matches

        def candidate_rank(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
            candidate_count = int(candidate.get("count_of_issues") or 0)
            candidate_year = _provider_year(candidate.get("start_year"))
            publisher = normalized_title((candidate.get("publisher") or {}).get("name"))
            publisher_match = bool(
                publisher and context_publisher
                and (publisher in context_publisher or context_publisher in publisher)
            )
            year_distance = abs(candidate_year - context_year) if candidate_year and context_year else 9999
            return (
                1 if expected_count and candidate_count == expected_count else 0,
                1 if publisher_match else 0,
                1 if year_distance == 0 else 0,
                -year_distance,
            )

        ranked = sorted(candidates, key=candidate_rank, reverse=True)
        if not ranked or (len(ranked) > 1 and candidate_rank(ranked[0]) == candidate_rank(ranked[1])):
            raise ValueError("Comic Vine could not identify one unambiguous matching volume")
        volume = ranked[0]
    volume_id = str(volume["id"])
    issue_url = f"{COMIC_VINE_API_BASE}/issues/?" + urllib.parse.urlencode({
        "api_key": api_key, "format": "json", "filter": f"volume:{volume_id}", "limit": 100,
        "field_list": ",".join(COMIC_VINE_ISSUE_FIELDS),
        "sort": "issue_number:asc",
    })
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(20):
        page_url = issue_url + f"&offset={offset}"
        page = fetch_provider_json("comic_vine", page_url, api_key)
        if str(page.get("status_code")) != "1":
            raise ValueError(str(page.get("error") or "Comic Vine issue lookup failed"))
        page_rows = page.get("results") or []
        rows.extend(row for row in page_rows if isinstance(row, dict))
        offset += len(page_rows)
        if not page_rows or offset >= int(page.get("number_of_total_results") or offset):
            break
    entries = []
    for issue in rows:
        number = str(issue.get("issue_number") or "").strip()
        if not number:
            continue
        image = issue.get("image") or {}
        evidence = native_issue_evidence("comic_vine", issue)
        if evidence["providerPublicationId"] not in {None, volume_id}:
            raise ValueError("Comic Vine returned an issue from a different publication container")
        evidence["publicationContext"] = {
            "id": volume_id, "name": volume.get("name"),
            "start_year": volume.get("start_year"),
            "publisher": volume.get("publisher"),
            "description": volume.get("description"),
        }
        publication_date = _provider_date(issue.get("store_date")) or _provider_date(issue.get("cover_date"))
        entries.append({
            "number": number, "provider_id": str(issue.get("id") or "") or None,
            "api_url": issue.get("site_detail_url"),
            "title": str(issue.get("name") or "").strip() or None,
            "description": evidence["description"],
            "provider_evidence": evidence,
            "publication_date": publication_date,
            "publication_year": _provider_year(publication_date, issue.get("cover_date")),
            "cover": image.get("medium_url") or image.get("small_url") or image.get("original_url"),
        })
    if not entries:
        raise ValueError("Comic Vine returned no issues for the matched volume")
    return volume_id, str(volume.get("site_detail_url") or ""), entries


def _provider_credential(provider: str) -> str:
    """The stored key for a provider, or a refusal naming it."""
    config = load_provider_config().get(provider) or {}
    field = "token" if provider == "metron" else "apiKey"
    credential = str(config.get(field) or "").strip()
    if not config.get("enabled") or not credential:
        raise ValueError(f"{PROVIDER_DEFINITIONS[provider]['name']} is not configured")
    return credential


def _provider_series_run_details(
    provider: str, provider_series_id: str, query: Any, credential: str
) -> dict[str, Any]:
    """One provider's account of a publication run: what it is and what is in it.

    Shared by adding a run from discovery and by correcting the run a
    library already has, because both need the same answer and had no
    business asking for it in two different ways.
    """
    cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
    context: dict[str, Any] = {"title": cleaned}
    if provider == "metron":
        context["metronSeriesId"] = str(provider_series_id)
        provider_id, source_url, entries, _ended = _metron_issue_entries(context, credential)
        detail = fetch_provider_json(
            provider, f"{METRON_API_BASE}/series/{provider_id}/", credential
        )
        raw_title = str(detail.get("name") or detail.get("series") or cleaned)
        publisher_value = detail.get("publisher") or {}
        return {
            "providerId": provider_id, "sourceUrl": source_url, "entries": entries,
            "title": re.sub(r"\s+\((?:19|20)\d{2}\)$", "", raw_title).strip(),
            "year": _provider_year(detail.get("year_began"), raw_title),
            "publisher": (
                publisher_value.get("name")
                if isinstance(publisher_value, dict) else publisher_value
            ),
            "endEvidence": _run_end_evidence(detail.get("year_end"), modelled="year_end" in detail),
            "synopsis": _synopsis_text(detail.get("desc")),
            # Metron catalogs no manga.
            "format": "comic",
        }
    context["comicVineVolumeId"] = str(provider_series_id)
    provider_id, source_url, entries = _comic_vine_issue_entries(context, credential)
    detail_url = f"{COMIC_VINE_API_BASE}/volume/4050-{provider_id}/?" + urllib.parse.urlencode({
        "api_key": credential, "format": "json",
        "field_list": "name,start_year,publisher,deck,description",
    })
    detail = (fetch_provider_json(provider, detail_url, credential).get("results")) or {}
    return {
        "providerId": provider_id, "sourceUrl": source_url, "entries": entries,
        "title": str(detail.get("name") or cleaned).strip(),
        "year": _provider_year(detail.get("start_year")),
        "publisher": (detail.get("publisher") or {}).get("name"),
        # Comic Vine volumes carry a start year and an issue count but no end
        # year at all, so this is silence rather than "still publishing".
        "endEvidence": _run_end_evidence(None, modelled=False),
        "synopsis": _comic_vine_synopsis(detail),
        "format": (
            "manga" if manga_edition((detail.get("publisher") or {}).get("name")) == "manga"
            else "comic"
        ),
    }


def _import_provider_run(
    provider: str, provider_series_id: str, query: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bring one provider run into the library, with its issue list.

    Shared by following a run and by pulling a single issue from it: both need
    the run and its issues to exist before anything can be asked for, and
    neither should decide on its own what the issue list looks like.
    """
    credential = _provider_credential(provider)
    fetched = _provider_series_run_details(provider, provider_series_id, query, credential)
    provider_id = fetched["providerId"]
    run = catalog_store().ensure_provider_series_run(
        provider, provider_id, fetched["title"], fetched["year"], fetched["publisher"],
        run_format=fetched.get("format"),
    )
    evidence = fetched["endEvidence"]
    catalog_store().apply_issue_list(
        int(run["id"]), provider, provider_id, fetched["sourceUrl"], fetched["entries"],
        status=_run_catalog_status(evidence), end_evidence=evidence,
        detail=f"Added from {PROVIDER_DEFINITIONS[provider]['name']} with "
               f"{len(fetched['entries'])} known issues.",
        source="added from library discovery search",
    )
    return run, fetched


def pull_discovered_issues(
    provider: str, provider_series_id: str, numbers: list[Any] | None = None,
    *, released: bool = False, query: str = "",
) -> dict[str, Any]:
    """Acquire chosen issues of a run -- or every released one -- without following it.

    The run has to exist in the library before any issue of it can be asked
    for, but importing is not following: the request covers exactly these
    issues, so the run is not monitored, not checked daily, and picks up no
    back catalogue. `released` is resolved after import, against the library's
    own record of which issues are out and which are already here.
    """
    provider = str(provider or "").strip().lower()
    provider_series_id = str(provider_series_id or "").strip()
    if provider not in {"metron", "comic_vine", "gcd"} or not re.fullmatch(r"\d+", provider_series_id):
        raise ValueError("Choose a valid publication run")
    wanted = list(dict.fromkeys(
        str(number).strip() for number in (numbers or []) if str(number).strip()
    ))
    if not released and not wanted:
        raise ValueError("Choose at least one issue to pull")
    if provider == "gcd":
        run, fetched = _import_gcd_run(query, provider_series_id)
    else:
        run, fetched = _import_provider_run(provider, provider_series_id, query)
    if released:
        series = next((
            item for item in catalog_store().catalog().get("series") or []
            if str(item.get("id")) == str(run["id"])
        ), None) or {}
        wanted = [
            str(issue.get("number")) for issue in series.get("issues") or []
            if issue.get("releaseState") == "released"
            and issue.get("ownership") in {None, "", "unowned"}
        ]
        if not wanted:
            raise ValueError("Every released issue of this run is already in your library")
    request = catalog_store().create_acquisition_request(
        "series", int(run["id"]), "issues", True, issue_numbers=wanted,
    )
    _start_automatic_release_grabs(request)
    return {
        "series": {**run, "publisher": fetched.get("publisher"), "year": fetched.get("year")},
        "request": request, "numbers": wanted,
    }


def pull_discovered_issue(
    provider: str, provider_series_id: str, number: str, query: str = "",
) -> dict[str, Any]:
    """One issue: the shelf's Pull Issue, kept as its own entry point."""
    number = str(number or "").strip()
    if not number:
        raise ValueError("Choose an issue to pull")
    result = pull_discovered_issues(provider, provider_series_id, [number], query=query)
    return {**result, "number": number}


# ---------------------------------------------------------------------------
# Discover's drawers
# ---------------------------------------------------------------------------

_PREVIEW_ORDER = ("metron", "comic_vine", "gcd")


def _plain_text(value: Any) -> str | None:
    """Provider prose without its markup; Metron and Comic Vine both send HTML."""
    # Block ends become spaces; inline tags vanish, so "<b>Knight</b>!" does
    # not come out as "Knight !".
    text = re.sub(r"<(?:br|/p|/div|/li|/h\d)\b[^>]*>", " ", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text or None


# Words that say what shape a run is, not what happens in it.
_FORMAT_WORDS = frozenset("""
    a an the ongoing one shot oneshot mini miniseries series limited maxi maxiseries
    issue issues part parts monthly bimonthly bi comic book special annual
    digital exclusive print only edition release
    two three four five six seven eight nine ten eleven twelve
""".split())


def _synopsis_text(value: Any) -> str | None:
    """Provider prose, if it says what the story is.

    Catalogs fill the field with whatever they have: "Ongoing series.", "A 7
    issue mini-series.", an editor's note about the indicia. Under "Story",
    those read as a blurb that says nothing, so they count as none.
    """
    text = _plain_text(value)
    if not text or text.casefold().startswith("note:"):
        return None
    words = re.findall(r"[a-z0-9]+", text.casefold())
    if len(text) < 80 and all(word.isdigit() or word in _FORMAT_WORDS for word in words):
        return None
    return text


def _comic_vine_synopsis(volume: dict[str, Any]) -> str | None:
    """Comic Vine's one-line deck, else the opening paragraph of its article.

    A volume's description is a wiki page -- headings, issue lists, credits --
    and a drawer wants what the story is, which is the deck or the lead.
    """
    deck = _synopsis_text(volume.get("deck"))
    if deck:
        return deck
    lead = re.search(r"<p[^>]*>(.*?)</p>", str(volume.get("description") or ""), re.S | re.I)
    return _synopsis_text(lead.group(1)) if lead else None


def series_removal_preview(series_run_id: int) -> dict[str, Any]:
    """What "Remove from library" would delete, for the confirmation."""
    plan = catalog_store().series_removal_plan(series_run_id)
    return {key: value for key, value in plan.items() if key != "files"}


def _remove_empty_folders(folder: Path, root: Path) -> None:
    """Take away folders a removal emptied, up to but never including the root."""
    current = folder
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def remove_series_from_library(series_run_id: int) -> dict[str, Any]:
    """Remove a run from Flipparr and delete its comic files from disk.

    Only ever asked for by a person, after being told how many files and how
    much space it deletes. Every path is checked to lie inside a library
    folder before anything is deleted, so one that does not stops the whole
    removal rather than half of it.
    """
    store = catalog_store()
    plan = store.series_removal_plan(series_run_id)
    if plan["activeDownloads"]:
        raise ValueError(
            "Something for this run is downloading or being searched. "
            "Remove it once that has finished."
        )
    targets: list[tuple[Path, Path]] = []
    for item in plan["files"]:
        root = Path(item["root"]).resolve()
        path = Path(item["path"])
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            raise ValueError(f"{path.name} is outside the library folder, so nothing was removed")
        if resolved == root:
            raise ValueError("A library folder itself cannot be removed this way")
        targets.append((resolved, root))
    deleted = freed = 0
    for path, root in targets:
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        freed += size
        _remove_empty_folders(path.parent, root)
    store.remove_series_run(series_run_id)
    log_event("series_removed", series_run_id=series_run_id, files_deleted=deleted)
    return {"removed": True, "title": plan["title"], "filesDeleted": deleted, "bytesFreed": freed}


_SYNOPSIS_ORDER = ("metron", "comic_vine")


def series_synopsis(series_run_id: int) -> dict[str, Any]:
    """What a library run is about, from the first catalog that says.

    Fetched when the drawer opens rather than stored: the series record is
    cached for a day, and a blurb is not worth a migration and a re-sync of
    every run to backfill. Only confirmed provider ids are asked -- a blurb for
    the wrong comic is worse than none. GCD has publication notes, not a story.
    """
    ids = catalog_store().confirmed_series_provider_ids(series_run_id)
    for provider in _SYNOPSIS_ORDER:
        provider_id = str(ids.get(provider) or "")
        if not re.fullmatch(r"\d+", provider_id):
            continue
        try:
            credential = _provider_credential(provider)
            if provider == "metron":
                detail = fetch_provider_json(
                    provider, f"{METRON_API_BASE}/series/{provider_id}/", credential
                )
                text = _synopsis_text(detail.get("desc"))
            else:
                url = f"{COMIC_VINE_API_BASE}/volume/4050-{provider_id}/?" + urllib.parse.urlencode({
                    "api_key": credential, "format": "json", "field_list": "deck,description",
                })
                text = _comic_vine_synopsis(
                    fetch_provider_json(provider, url, credential).get("results") or {}
                )
        except Exception:  # noqa: BLE001 -- the blurb is extra; no source means no blurb
            continue
        if text:
            return {"synopsis": text, "provider": provider,
                    "providerName": _DISCOVERY_PROVIDERS.get(provider, provider)}
    return {"synopsis": None, "provider": None, "providerName": None}


def _gcd_run_preview(provider_series_id: str) -> dict[str, Any]:
    """GCD's account of a run from one request: issue numbers, not details.

    Filling each issue in is a request per issue against the harshest limit of
    the three providers, so the preview stops at the series record and says so
    rather than spending an hour of GCD's quota on a drawer.
    """
    series = fetch_gcd_json(f"{GCD_API_BASE}/series/{provider_series_id}/?format=json")
    return {
        "providerSeriesId": provider_series_id,
        "title": str(series.get("name") or "").strip(),
        "year": _provider_year(series.get("year_began")),
        "publisher": _gcd_series_publisher(series),
        "endEvidence": _run_end_evidence(series.get("year_ended"), modelled="year_ended" in series),
        "entries": _gcd_canonical_issue_entries(series),
    }


def _shape_run_preview(provider: str, run: dict[str, Any], errors: list[dict[str, Any]]) -> dict[str, Any]:
    """A run as the drawer draws it, each issue saying what the library knows of it."""
    relevance = _library_relevance()
    title = str(run.get("title") or "").strip()
    year = run.get("year")
    known = relevance.get(f"{normalized_title(title)}|{year or ''}") or relevance.get(normalized_title(title))
    issues = []
    for entry in run.get("entries") or []:
        number = str(entry.get("number") or "").strip()
        if not number:
            continue
        key = _issue_key(number)
        issues.append({
            "number": number,
            "title": entry.get("title"),
            "publicationDate": entry.get("publication_date"),
            "publicationYear": entry.get("publication_year"),
            "cover": entry.get("cover"),
            "releaseState": _issue_release_state(
                entry.get("publication_date"), entry.get("publication_year")
            ),
            "owned": bool(known and key in known["owned"]),
            # A followed run already has every issue on its way.
            "queued": bool(known and (known["following"] or key in known["queued"])),
        })
    state = (run.get("endEvidence") or {}).get("state")
    return {
        "provider": provider, "providerName": _DISCOVERY_PROVIDERS.get(provider, provider),
        "providerSeriesId": str(run.get("providerSeriesId") or ""),
        "title": title, "year": year, "publisher": run.get("publisher"),
        "synopsis": run.get("synopsis"),
        "medium": run.get("format") or "comic",
        "publicationStatus": (
            "completed" if state == "ended" else "ongoing" if state == "ongoing" else "unknown"
        ),
        "cover": next((issue["cover"] for issue in issues if issue.get("cover")), None),
        "inLibrary": bool(known), "following": bool(known and known["following"]),
        "runId": known["runId"] if known else None,
        "detailsLimited": provider == "gcd",
        "issues": issues, "fallbacks": errors,
    }


def preview_discovered_run(provider_ids: dict[str, Any], query: str = "") -> dict[str, Any]:
    """A run's issues, before anything is imported.

    Tried in the order Discover already prefers for display -- Metron, then
    Comic Vine, then GCD -- falling through a provider that is not configured
    or does not answer. The provider actually used is returned, so a pull goes
    back to the same source and its issue numbers line up. Nothing is written:
    importing waits until something is pulled.
    """
    errors: list[dict[str, Any]] = []
    for provider in _PREVIEW_ORDER:
        provider_series_id = str((provider_ids or {}).get(provider) or "").strip()
        if not re.fullmatch(r"\d+", provider_series_id):
            continue
        try:
            if provider == "gcd":
                run = _gcd_run_preview(provider_series_id)
            else:
                fetched = _provider_series_run_details(
                    provider, provider_series_id, query, _provider_credential(provider)
                )
                run = {**fetched, "providerSeriesId": fetched.get("providerId") or provider_series_id}
        except Exception as exc:
            errors.append({"provider": _DISCOVERY_PROVIDERS.get(provider, provider), "error": str(exc)})
            continue
        return _shape_run_preview(provider, run, errors)
    if errors:
        raise RuntimeError(errors[0]["error"])
    raise ValueError("Choose a valid publication run")


def discovered_issue_detail(provider_issue_id: str) -> dict[str, Any]:
    """What Metron knows about one issue, for Discover's issue drawer.

    One request, made after the drawer opens: the shelf already has the cover,
    title and dates, so those show at once and this fills in the rest.
    """
    provider_issue_id = str(provider_issue_id or "").strip()
    if not re.fullmatch(r"\d+", provider_issue_id):
        raise ValueError("Choose a valid issue")
    row = fetch_provider_json(
        "metron", f"{METRON_API_BASE}/issue/{provider_issue_id}/", _provider_credential("metron"),
    )
    names = row.get("name") or []
    return {
        "providerIssueId": provider_issue_id,
        "storyTitles": [
            str(name).strip() for name in (names if isinstance(names, list) else [names])
            if str(name).strip()
        ],
        "description": _plain_text(row.get("desc")),
        "creators": _metron_discovery_creators(row),
        "pageCount": row.get("page"),
        "price": row.get("price"),
        "coverDate": _provider_date(row.get("cover_date")),
        "storeDate": _provider_date(row.get("store_date")),
    }


def request_discovered_series(
    provider: str, query: str, provider_series_id: str,
    acquisition_preference: str = "either",
) -> dict[str, Any]:
    """Import the exact discovered provider run and begin following its missing issues."""
    provider = str(provider or "gcd").strip().lower()
    if provider == "gcd":
        return request_discovered_gcd_series(query, provider_series_id, acquisition_preference)
    if provider not in {"metron", "comic_vine"} or not re.fullmatch(r"\d+", str(provider_series_id or "")):
        raise ValueError("Choose a valid discovered publication run")
    run, fetched = _import_provider_run(provider, provider_series_id, query)
    entries, publisher, year = fetched["entries"], fetched["publisher"], fetched["year"]
    evidence = fetched["endEvidence"]
    request = catalog_store().create_acquisition_request(
        "series", int(run["id"]), acquisition_preference, True
    )
    _start_automatic_release_grabs(request)
    return {
        "series": {**run, "publisher": publisher, "year": year},
        "request": request, "issueCount": len(entries),
        "metadata": {"provider": provider, "status": _run_catalog_status(evidence)},
    }


def series_match_candidates(series_run_id: int, query: Any = None) -> dict[str, Any]:
    """Publication runs this series could be, from every configured provider.

    The picker was GCD-only and reachable only from a collection with no
    single issues, so a run matched to the wrong comic by any other
    provider had no way to be corrected.
    """
    store = catalog_store()
    series = next(
        (item for item in store.catalog()["series"] if str(item["id"]) == str(series_run_id)),
        None,
    )
    if not series:
        raise LookupError("That series run is not in the catalog")
    asked = re.sub(r"\s+", " ", str(query or "")).strip() or str(series.get("title") or "")
    discovered = discover_series(asked)
    return {
        "series": {
            "id": str(series_run_id), "title": series.get("title"),
            "year": series.get("year"), "publisher": series.get("publisher"),
        },
        "query": asked,
        "provider": discovered.get("providerId"),
        "providerName": discovered.get("provider"),
        "providersChecked": discovered.get("providersChecked") or [],
        "candidates": discovered.get("results") or [],
    }


def confirm_series_match(
    series_run_id: int, provider: str, provider_series_id: str, query: Any = None
) -> dict[str, Any]:
    """Point an existing run at the publication run it actually is."""
    provider = str(provider or "").strip().lower()
    if provider == "gcd":
        return confirm_gcd_series_run(series_run_id, str(provider_series_id))
    if provider not in {"metron", "comic_vine"} or not re.fullmatch(
        r"\d+", str(provider_series_id or "")
    ):
        raise ValueError("Choose a valid publication run to match")
    store = catalog_store()
    series = next(
        (item for item in store.catalog()["series"] if str(item["id"]) == str(series_run_id)),
        None,
    )
    if not series:
        raise LookupError("That series run is not in the catalog")
    credential = _provider_credential(provider)
    fetched = _provider_series_run_details(
        provider, provider_series_id, query or series.get("title"), credential
    )
    entries = fetched["entries"]
    if not entries:
        raise ValueError("That publication run lists no issues to import")
    name = PROVIDER_DEFINITIONS[provider]["name"]
    # Correcting a match has to undo what the wrong one left behind:
    # apply_issue_list only fills blanks, so a title a mismatched run wrote
    # would otherwise survive the correction meant to remove it.
    rebuilt = store.rebuild_series_run(series_run_id)
    applied = store.apply_issue_list(
        series_run_id, provider, fetched["providerId"], fetched["sourceUrl"], entries,
        status=_run_catalog_status(fetched["endEvidence"]),
        end_evidence=fetched["endEvidence"],
        detail=(
            f"You matched this run to {name} series {fetched['providerId']}; "
            f"{len(entries)} issues were imported."
        ),
        source="user-confirmed series match",
    )
    return {**applied, "rebuilt": rebuilt, "provider": provider,
            "matchedTitle": fetched["title"], "matchedPublisher": fetched["publisher"]}


def rebuild_series_run(series_run_id: int) -> dict[str, Any]:
    """Clear a run's derived identity, then work it out again.

    Its own action beside fixing a match, because they repair different faults:
    a wrong match links the run to the wrong comic, while this undoes what a
    file that turned out to be something else left on a run whose match was
    right all along.
    """
    outcome = catalog_store().rebuild_series_run(series_run_id)
    try:
        outcome["refresh"] = sync_issue_catalog(series_run_id)
    except Exception as exc:
        # The clearing is the part worth keeping. A provider that cannot answer
        # now is a reason to refresh again later, not to put the wrong titles
        # back.
        log_exception("rebuild_refresh_failed", exc, level="warning")
        outcome["refresh"] = {"status": "unavailable", "detail": str(exc)}
    return outcome


def sync_issue_catalog(series_run_id: int) -> dict[str, Any]:
    """Refresh issue structure and details through configured providers in priority order."""
    store = catalog_store()
    store.repair_provider_run_mismatch(series_run_id)
    before = store.issue_metadata_summary(series_run_id)
    provider_results: list[dict[str, Any]] = []
    provider_errors: list[dict[str, str]] = []
    retry_after_seconds: int | None = None
    context = store.get_series_sync_context(series_run_id)
    for provider_id, values in _series_enrichment_provider_order():
        if provider_id == "metron" and context.get("format") == "manga":
            continue
        if not store.metadata_provider_available(provider_id):
            provider_errors.append({
                "provider": provider_id,
                "error": "Provider is cooling down after a recent rate limit or service error",
            })
            continue
        try:
            if provider_id == "metron":
                credential = str(values.get("token") or "").strip()
                if not credential:
                    continue
                provider_series_id, api_url, entries, evidence = _metron_issue_entries(
                    context, credential
                )
                result = store.apply_issue_list(
                    series_run_id, provider_id, provider_series_id, api_url, entries,
                    status=_run_catalog_status(evidence), end_evidence=evidence,
                    detail="Issue structure and details synchronized through Metron.",
                    source="matched automatically by canonical title, year, and cross-provider identifiers",
                )
            elif provider_id == "comic_vine":
                credential = str(values.get("apiKey") or "").strip()
                if not credential:
                    continue
                provider_series_id, api_url, entries = _comic_vine_issue_entries(context, credential)
                result = store.apply_issue_list(
                    series_run_id, provider_id, provider_series_id, api_url, entries,
                    # Comic Vine volumes carry a start year and an issue count
                    # but no end year at all. That is silence, not a statement
                    # that the run continues, so it must not overwrite what a
                    # provider that does model an end year already recorded.
                    status="complete_to_date",
                    end_evidence=_run_end_evidence(None, modelled=False),
                    detail="Issue structure and details synchronized through Comic Vine.",
                    source="matched automatically by canonical title, year, and cross-provider identifiers",
                )
            else:
                if context.get("knownGcdIssueIds") or context.get("gcdSeriesId"):
                    result = sync_gcd_issue_catalog(series_run_id)
                else:
                    outcome = _apply_gcd_series_enrichment(series_run_id, context)
                    if outcome.get("status") == "review":
                        provider_errors.append({
                            "provider": "gcd",
                            "error": str(outcome.get("detail") or "No unambiguous matching publication run"),
                        })
                        continue
                    result = outcome.get("result") or outcome
                gcd_metadata = result.get("metadata") or {}
                if gcd_metadata.get("status") == "deferred":
                    retry_after_seconds = gcd_metadata.get("retryAfterSeconds")
            provider_results.append(result)
            store.record_metadata_provider_outcome(
                provider_id, minimum_delay_seconds=15 if provider_id == "gcd" else 3
            )
            context = store.get_series_sync_context(series_run_id)
        except Exception as exc:
            limited = _rate_limit_from_error(provider_id, exc)
            if limited:
                store.record_metadata_provider_outcome(
                    provider_id, str(limited), limited.retry_after_seconds
                )
                retry_after_seconds = max(retry_after_seconds or 0, limited.retry_after_seconds)
            else:
                store.record_metadata_provider_outcome(provider_id, str(exc))
            provider_errors.append({"provider": provider_id, "error": str(exc)})

    if not provider_results:
        detail = "; ".join(
            f"{PROVIDER_DEFINITIONS.get(item['provider'], {}).get('name', item['provider'])}: {item['error']}"
            for item in provider_errors
        )
        raise ValueError(detail or "No metadata provider could refresh this series")
    after = store.issue_metadata_summary(series_run_id)
    before_raw_titles = before.get("rawMissingTitleCount", before["missingTitleCount"])
    after_raw_titles = after.get("rawMissingTitleCount", after["missingTitleCount"])
    repaired_titles = max(0, before_raw_titles - after_raw_titles)
    repaired_dates = max(0, before["missingDateCount"] - after["missingDateCount"])
    checked_names = [
        PROVIDER_DEFINITIONS.get(result.get("provider"), {}).get("name", result.get("provider"))
        for result in provider_results
    ]
    checked_names = list(dict.fromkeys(str(name) for name in checked_names if name))
    complete_titleless_sources = [
        result for result in provider_results
        if result.get("issueCount") == after["issueCount"] and int(result.get("titleCount") or 0) == 0
    ]
    title_policy = "numbered_only" if (
        after["issueCount"] > 0
        and after_raw_titles == after["issueCount"]
        and len(complete_titleless_sources) >= 2
    ) else "named"
    if title_policy == "numbered_only":
        after["titlePolicy"] = title_policy
        after["missingTitleCount"] = 0
        after["missingTitleIssues"] = []
    title_available = after["issueCount"] - after_raw_titles
    date_available = after["issueCount"] - after["missingDateCount"]
    if title_policy == "numbered_only":
        detail = (
            f"Checked {', '.join(checked_names)}. All {after['issueCount']} issues are matched and "
            f"{date_available} of {after['issueCount']} release dates are available. The matched "
            "catalogs describe this run by issue number and do not publish separate issue titles."
        )
    else:
        detail = (
            f"Checked {', '.join(checked_names)}. {title_available} of {after['issueCount']} issue titles "
            f"and {date_available} of {after['issueCount']} release dates are available."
        )
    missing_parts = []
    if after["missingTitleIssues"]:
        missing_parts.append("title: " + ", ".join(f"#{number}" for number in after["missingTitleIssues"][:12]))
    if after["missingDateIssues"]:
        missing_parts.append("release date: " + ", ".join(f"#{number}" for number in after["missingDateIssues"][:12]))
    if missing_parts:
        detail += " Still unavailable from enabled providers — " + "; ".join(missing_parts) + "."
    store.update_issue_catalog_detail(series_run_id, detail, title_policy=title_policy)
    remaining = after["missingTitleCount"] + after["missingDateCount"]
    return {
        "seriesId": str(series_run_id), "status": "complete", "issueCount": after["issueCount"],
        "providers": provider_results, "providerErrors": provider_errors,
        "metadata": {
            "status": "complete" if remaining == 0 else "partial",
            "repairedTitleCount": repaired_titles, "repairedDateCount": repaired_dates,
            **after,
            **({"retryAfterSeconds": retry_after_seconds} if retry_after_seconds else {}),
        },
    }


class MetadataRateLimited(RuntimeError):
    def __init__(self, provider: str, retry_after_seconds: int, message: str):
        super().__init__(message)
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds


def _rate_limit_from_error(provider: str, exc: Exception) -> MetadataRateLimited | None:
    if isinstance(exc, MetadataRateLimited):
        return exc
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 429:
        return None
    retry_after = _provider_retry_after(provider, dict(exc.headers.items()) if exc.headers else {})
    return MetadataRateLimited(
        provider, retry_after,
        f"{PROVIDER_DEFINITIONS.get(provider, {}).get('name', provider)} asked Flipparr to pause.",
    )


def _series_enrichment_provider_order() -> list[tuple[str, dict[str, Any]]]:
    """Prefer configured authenticated catalogs, retaining anonymous GCD as fallback."""
    config = load_provider_config()
    optional = [
        (provider_id, values)
        for provider_id, values in config.items()
        if provider_id in {"metron", "comic_vine"} and values.get("enabled")
    ]
    optional.sort(key=lambda item: int(item[1].get("priority") or 99))
    if (config.get("gcd") or {}).get("enabled", True):
        optional.append(("gcd", config.get("gcd") or {}))
    return optional


def _apply_gcd_series_enrichment(series_run_id: int, context: dict[str, Any]) -> dict[str, Any]:
    if context.get("gcdSeriesId"):
        return sync_gcd_issue_catalog(series_run_id)
    candidates = rank_gcd_series_runs(
        context, _gcd_discovery_search_rows(str(context["title"])),
    )
    exact = [
        candidate for candidate in candidates
        if normalized_title(candidate.get("title")) == normalized_title(context.get("title"))
        and candidate.get("fit") == "strong"
    ]
    if context.get("year"):
        same_year = [candidate for candidate in exact if candidate.get("yearBegan") == context.get("year")]
        exact = same_year or exact
    if len(exact) != 1:
        return {
            "status": "review", "provider": "gcd", "candidateCount": len(exact),
            "detail": "Flipparr found no single high-confidence publication run and left the local grouping unchanged.",
        }
    candidate = exact[0]
    provider_series_id = str(candidate["providerSeriesId"])
    series = candidate["_series"]
    entries, metadata = _hydrate_gcd_issue_entries_with_status(
        _gcd_canonical_issue_entries(series), provider_series_id
    )
    if not entries:
        return {
            "status": "review", "provider": "gcd", "candidateCount": 1,
            "detail": "The matching publication run did not contain a usable issue list.",
        }
    evidence = _run_end_evidence(candidate.get("yearEnded"), modelled="yearEnded" in candidate)
    applied = catalog_store().apply_issue_list(
        series_run_id, "gcd", provider_series_id, candidate.get("apiUrl") or "", entries,
        status=_run_catalog_status(evidence), end_evidence=evidence,
        detail=f"Matched automatically during initial library enrichment with {len(entries)} known issues.",
        source="initial import: exact title, year, publisher, and owned issue evidence",
    )
    return {"status": "complete", "provider": "gcd", "result": applied, "metadata": metadata}


def enrich_catalog_series(series_run_id: int) -> dict[str, Any]:
    """Enrich one canonical run, stopping after the first provider supplies a safe match."""
    store = catalog_store()
    store.repair_provider_run_mismatch(series_run_id)
    context = store.get_series_sync_context(series_run_id)
    errors: list[str] = []
    deferred_limit: MetadataRateLimited | None = None
    available_provider = False
    considered: list[str] = []
    for provider_id, values in _series_enrichment_provider_order():
        # Metron has no manga, and asked by title it answers with whatever
        # comic shares the name -- Berserk, Monster, Akira.
        if provider_id == "metron" and context.get("format") == "manga":
            continue
        considered.append(provider_id)
        if not store.metadata_provider_available(provider_id):
            continue
        available_provider = True
        try:
            if provider_id == "metron":
                token = str(values.get("token") or "").strip()
                if not token:
                    continue
                provider_series_id, api_url, entries, evidence = _metron_issue_entries(
                    context, token
                )
                result = store.apply_issue_list(
                    series_run_id, provider_id, provider_series_id, api_url, entries,
                    status=_run_catalog_status(evidence), end_evidence=evidence,
                    detail=f"Matched automatically through Metron during initial library enrichment.",
                    source="initial import: unambiguous title, year, and provider identifiers",
                )
            elif provider_id == "comic_vine":
                api_key = str(values.get("apiKey") or "").strip()
                if not api_key:
                    continue
                provider_series_id, api_url, entries = _comic_vine_issue_entries(context, api_key)
                result = store.apply_issue_list(
                    series_run_id, provider_id, provider_series_id, api_url, entries,
                    # Comic Vine volumes carry a start year and an issue count
                    # but no end year at all. That is silence, not a statement
                    # that the run continues, so it must not overwrite what a
                    # provider that does model an end year already recorded.
                    status="complete_to_date",
                    end_evidence=_run_end_evidence(None, modelled=False),
                    detail=f"Matched automatically through Comic Vine during initial library enrichment.",
                    source="initial import: unambiguous title, year, and provider identifiers",
                )
            else:
                outcome = _apply_gcd_series_enrichment(series_run_id, context)
                if outcome["status"] == "review":
                    store.record_metadata_provider_outcome("gcd", minimum_delay_seconds=15)
                    if deferred_limit:
                        break
                    return outcome
                result = outcome.get("result") or outcome
            store.record_metadata_provider_outcome(
                provider_id, minimum_delay_seconds=15 if provider_id == "gcd" else 3
            )
            return {"status": "complete", "provider": provider_id, "result": result}
        except ValueError as exc:
            # A provider can be healthy yet unable to disambiguate this one title.
            # Keep trying fallbacks without globally cooling down that provider.
            errors.append(f"{PROVIDER_DEFINITIONS[provider_id]['name']}: {exc}")
        except Exception as exc:
            limited = _rate_limit_from_error(provider_id, exc)
            if limited:
                store.record_metadata_provider_outcome(
                    provider_id, str(limited), limited.retry_after_seconds
                )
                deferred_limit = limited
                continue
            message = str(exc)
            store.record_metadata_provider_outcome(provider_id, message)
            errors.append(f"{PROVIDER_DEFINITIONS[provider_id]['name']}: {message}")
    if not available_provider:
        # Report the wait that is actually outstanding. A flat minute here made
        # every series in a run wait 60s for a 15s cooldown, so a 75-series
        # library took four times longer than its own pacing requires.
        raise MetadataRateLimited(
            "all",
            store.metadata_provider_retry_seconds(considered),
            "Metadata providers are cooling down.",
        )
    if deferred_limit:
        raise deferred_limit
    if errors:
        raise RuntimeError("; ".join(errors))
    return {
        "status": "review", "provider": None,
        "detail": "No provider returned one unambiguous series match.",
    }


def run_metadata_enrichment_job(job: dict[str, Any]) -> dict[str, Any]:
    store = catalog_store()
    job_id = int(job["id"])
    try:
        outcome = enrich_catalog_series(int(job["series_run_id"]))
    except MetadataRateLimited as exc:
        store.finish_metadata_enrichment_job(
            job_id, "waiting", exc.provider, str(exc), exc.retry_after_seconds
        )
        return {"status": "waiting", "provider": exc.provider}
    except Exception as exc:
        attempts = int(job.get("attempt_count") or 1)
        status = "waiting" if attempts < 3 else "failed"
        store.finish_metadata_enrichment_job(
            job_id, status, error=str(exc), retry_after_seconds=min(3600, 60 * (2 ** attempts))
        )
        return {"status": status, "error": str(exc)}
    status = str(outcome.get("status") or "review")
    store.finish_metadata_enrichment_job(
        job_id, "complete" if status == "complete" else "review",
        outcome.get("provider"), outcome.get("detail"),
    )
    return outcome


def metadata_enrichment_worker(stop_event: threading.Event = _ENRICHMENT_STOP) -> None:
    """Process initial enrichment and low-frequency refreshes for followed ongoing runs."""
    store = catalog_store()
    store.resume_metadata_enrichment()
    store.enqueue_metadata_enrichment()
    while not stop_event.is_set():
        providers = [provider_id for provider_id, _values in _series_enrichment_provider_order()]
        if providers and not any(store.metadata_provider_available(provider_id) for provider_id in providers):
            stop_event.wait(5)
            continue
        job = store.claim_metadata_enrichment_job()
        if job:
            run_metadata_enrichment_job(job)
            stop_event.wait(1)
            continue
        monitored = store.claim_monitored_series_refresh()
        if not monitored:
            # Idle: ask about one run whose files name no creator, so search
            # by creator finds it too.
            try:
                synced = sync_next_run_creators(store)
            except Exception:
                synced = False
            stop_event.wait(1 if synced else 5)
            continue
        series_run_id = int(monitored["series_run_id"])
        try:
            outcome = enrich_catalog_series(series_run_id)
        except MetadataRateLimited as exc:
            store.finish_monitored_series_refresh(
                series_run_id, exc.provider, str(exc), exc.retry_after_seconds
            )
        except Exception as exc:
            store.finish_monitored_series_refresh(series_run_id, error=str(exc))
        else:
            store.finish_monitored_series_refresh(
                series_run_id, str(outcome.get("provider") or "") or None
            )
            store.reconcile_acquisition_jobs()
        stop_event.wait(1)


def start_metadata_enrichment_worker() -> threading.Thread:
    global _ENRICHMENT_THREAD
    if _ENRICHMENT_THREAD and _ENRICHMENT_THREAD.is_alive():
        return _ENRICHMENT_THREAD
    _ENRICHMENT_STOP.clear()
    _ENRICHMENT_THREAD = threading.Thread(
        target=metadata_enrichment_worker,
        name="flipparr-metadata-coordinator",
        daemon=True,
    )
    _ENRICHMENT_THREAD.start()
    return _ENRICHMENT_THREAD


def assess_identity_confidence(
    parsed: ParsedFile,
    embedded: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    """Explain confidence in identity separately from metadata completeness."""
    if candidate.get("record_type") != "single_issue":
        return None
    score = 10
    corroborated = ["GCD returned a specific issue record"]
    conflicts = []
    limitations = []

    series_similarity = difflib.SequenceMatcher(
        None, normalized_title(parsed.title), normalized_title(candidate.get("title"))
    ).ratio()
    if series_similarity == 1:
        score += 25
        corroborated.append("filename series exactly matches GCD series")
    elif series_similarity >= 0.85:
        score += 15
        corroborated.append(f"filename series strongly matches GCD series ({series_similarity:.0%})")
    else:
        conflicts.append(f"filename and GCD series differ ({series_similarity:.0%} similarity)")

    if parsed.issue and parsed.issue == str(candidate.get("issue") or ""):
        score += 30
        corroborated.append(f"filename and GCD both identify issue {parsed.issue}")
    else:
        conflicts.append("filename and GCD issue numbers do not agree")

    candidate_year = candidate.get("publication_year")
    if parsed.year and candidate_year:
        difference = abs(int(parsed.year) - int(candidate_year))
        if difference == 0:
            score += 5
            corroborated.append("filename year agrees with GCD publication year")
        elif difference == 1:
            score -= 3
            conflicts.append(
                f"filename says {parsed.year}; GCD publication date is {candidate.get('publication_date') or candidate_year}"
            )
        else:
            score -= 12
            conflicts.append(f"filename year {parsed.year} conflicts with GCD year {candidate_year}")

    if embedded.get("source") == "ComicInfo.xml":
        if normalized_title(embedded.get("series")) == normalized_title(candidate.get("title")):
            score += 10
            corroborated.append("ComicInfo.xml series agrees with GCD")
        else:
            conflicts.append("ComicInfo.xml series does not agree with GCD")
        if str(embedded.get("number") or "").lstrip("0") == str(candidate.get("issue") or "").lstrip("0"):
            score += 10
            corroborated.append("ComicInfo.xml issue number agrees with GCD")
        else:
            conflicts.append("ComicInfo.xml issue number does not agree with GCD")
        embedded_publisher = normalized_title(re.sub(r"\binc\.?\b", "", embedded.get("publisher") or "", flags=re.I))
        candidate_publisher = normalized_title(re.sub(r"\binc\.?\b", "", candidate.get("publisher") or "", flags=re.I))
        if embedded_publisher and embedded_publisher == candidate_publisher:
            score += 5
            corroborated.append("ComicInfo.xml publisher agrees with GCD")
        embedded_year_match = YEAR.search(str(embedded.get("year") or ""))
        if embedded_year_match and candidate_year:
            if int(embedded_year_match.group(1)) == int(candidate_year):
                score += 5
                corroborated.append("ComicInfo.xml year agrees with GCD publication year")
            else:
                conflicts.append("ComicInfo.xml year does not agree with GCD")
        embedded_story = normalized_title(embedded.get("title"))
        if embedded_story and any(
            difflib.SequenceMatcher(None, embedded_story, normalized_title(title)).ratio() >= 0.8
            for title in candidate.get("named_contents") or []
        ):
            score += 5
            corroborated.append("ComicInfo.xml story title agrees with the GCD story record")

    if "[" in (candidate.get("descriptor") or ""):
        limitations.append("series and issue are verified; the filename does not independently verify the cover variant")
    limitations.append("identity confidence does not guarantee every credit or descriptive field is complete")
    score = max(0, min(97, score))
    level = "high" if score >= 85 else "medium" if score >= 65 else "low"
    return {
        "scope": "series and issue identity",
        "level": level,
        "score": score,
        "corroborated_fields": corroborated,
        "conflicts": conflicts,
        "limitations": limitations,
    }


def score_candidate(parsed: ParsedFile, candidate: dict[str, Any], rank: int) -> tuple[int, list[str]]:
    title_similarity = difflib.SequenceMatcher(
        None, normalized_title(candidate.get("title")), normalized_title(parsed.title)
    ).ratio()
    score = max(0, 20 - rank * 3)
    reasons = [f"Open Library search rank {rank + 1}"]
    candidate_isbns = {normalize_isbn(value) for value in candidate.get("isbns", [])}
    exact_isbn = bool(parsed.isbn and parsed.isbn in candidate_isbns)
    if title_similarity == 1:
        score += 50
        reasons.append("exact normalized title")
    elif title_similarity >= 0.8:
        score += 30
        reasons.append(f"strong title similarity ({title_similarity:.0%})")
    elif not exact_isbn:
        score -= 60
        reasons.append(f"title mismatch ({title_similarity:.0%})")
    else:
        reasons.append(f"title differs, but exact ISBN overrides it ({title_similarity:.0%})")
    if exact_isbn:
        score += 100
        reasons.append("exact ISBN")
    if candidate.get("matched_edition"):
        score += 50
        reasons.append(f"Open Library edition explicitly matches volume {parsed.volume}")
    if parsed.year and candidate.get("publication_year"):
        difference = abs(int(candidate["publication_year"]) - parsed.year)
        if difference <= 1:
            score += 15
            reasons.append("publication year agrees")
        elif difference >= 5:
            score -= 10
            reasons.append("publication year conflicts")
    return score, reasons


def search_google_books(parsed: ParsedFile) -> list[dict[str, Any]]:
    if not GOOGLE_BOOKS_API_KEY:
        return []
    query = f"isbn:{parsed.isbn}" if parsed.isbn else f'intitle:"{parsed.title}"'
    url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
        {"q": query, "maxResults": 5, "key": GOOGLE_BOOKS_API_KEY}
    )
    data = fetch_json(url)
    if not data.get("items") and parsed.isbn and parsed.title:
        query = f'intitle:"{parsed.title}"'
        url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
            {"q": query, "maxResults": 5, "key": GOOGLE_BOOKS_API_KEY}
        )
        data = fetch_json(url)
    results = []
    for item in data.get("items", [])[:5]:
        info = item.get("volumeInfo", {})
        results.append(
            {
                "source": "Google Books",
                "source_id": item.get("id"),
                "title": info.get("title"),
                "subtitle": info.get("subtitle"),
                "creators": info.get("authors", []),
                "publisher": info.get("publisher"),
                "published_date": info.get("publishedDate"),
                "description": info.get("description"),
                "page_count": info.get("pageCount"),
                "format": info.get("printType"),
                "isbns": [x.get("identifier") for x in info.get("industryIdentifiers", []) if x.get("identifier")],
                "cover": (info.get("imageLinks") or {}).get("thumbnail"),
                "url": info.get("infoLink"),
            }
        )
    return results


def embedded_epub_candidate(lookup: ParsedFile, embedded: dict[str, Any]) -> dict[str, Any] | None:
    if embedded.get("source") != "EPUB package metadata" or not embedded.get("title"):
        return None
    isbns = embedded.get("isbns") or []
    score = 55
    reasons = ["title read directly from EPUB package metadata"]
    if embedded.get("creators"):
        score += 5
        reasons.append("creator metadata present")
    if embedded.get("publisher"):
        score += 5
        reasons.append("publisher metadata present")
    if isbns:
        score += 10
        reasons.append("valid-checksum ISBN present in EPUB")
    if embedded.get("cover_member"):
        reasons.append("cover image found inside EPUB")
    cover_url = None
    if embedded.get("cover_member"):
        cover_url = "/api/file-cover?" + urllib.parse.urlencode({"path": lookup.path})
    embedded_coverage = extract_issue_coverage(
        embedded.get("description"), "EPUB package description", "file confirmed"
    )
    return {
        "source": "EPUB package metadata",
        "source_id": None,
        "title": embedded.get("title"),
        "subtitle": None,
        "creators": embedded.get("creators", []),
        "publisher": embedded.get("publisher"),
        "publication_year": lookup.year,
        "isbns": isbns,
        "cover": cover_url,
        "url": None,
        "description": embedded.get("description"),
        "format": embedded.get("format"),
        "language": embedded.get("language"),
        "rights": embedded.get("rights"),
        "subjects": embedded.get("subjects", []),
        "creator_details": embedded.get("creator_details", []),
        "named_contents": embedded.get("named_contents", []),
        "coverage_status": (
            "coverage found in embedded description"
            if embedded_coverage
            else "not available in EPUB metadata; an external collection source is required"
        ),
        "matched_edition": {
            "isbn_10": [value for value in isbns if len(value) == 10],
            "isbn_13": [value for value in isbns if len(value) == 13],
            "number_of_pages": embedded.get("page_count"),
            "coverage": embedded_coverage,
        },
        "match_score": score,
        "match_reasons": reasons,
        "verification_status": "embedded-only; not confirmed by an external catalog",
    }


def embedded_comicinfo_candidate(
    parsed: ParsedFile,
    embedded: dict[str, Any],
) -> dict[str, Any] | None:
    if embedded.get("source") != "ComicInfo.xml" or not parsed.issue or not embedded.get("series"):
        return None
    embedded_issue = str(embedded.get("number") or "").lstrip("0") or None
    if embedded_issue and embedded_issue != parsed.issue:
        return None
    embedded_title = embedded.get("title")
    if (
        embedded_title
        and re.search(rf"#\s*0*{re.escape(parsed.issue)}\b", embedded_title, re.I)
        and normalized_title(embedded.get("series")) in normalized_title(embedded_title)
    ):
        embedded_title = None
    contributors: dict[str, list[str]] = {}
    creators = []
    for role, value in (embedded.get("contributors") or {}).items():
        names = [name.strip() for name in str(value).split(",") if name.strip()]
        if not names:
            continue
        contributors[role] = names
        if role != "cover_artist":
            for name in names:
                if name not in creators:
                    creators.append(name)
    year_match = YEAR.search(str(embedded.get("year") or ""))
    corroborated = []
    conflicts = []
    confidence_score = 50
    if normalized_title(parsed.title) == normalized_title(embedded.get("series")):
        confidence_score += 15
        corroborated.append("filename series agrees with ComicInfo.xml")
    if parsed.issue == (embedded_issue or parsed.issue):
        confidence_score += 20
        corroborated.append(f"filename and ComicInfo.xml both identify issue {parsed.issue}")
    if parsed.year and year_match:
        embedded_year = int(year_match.group(1))
        if parsed.year == embedded_year:
            confidence_score += 5
            corroborated.append("filename year agrees with ComicInfo.xml")
        else:
            confidence_score -= 5
            conflicts.append(f"filename says {parsed.year}; ComicInfo.xml says {embedded_year}")
    confidence_score = min(84, max(0, confidence_score))
    source_id = embedded.get("metron_id") or embedded.get("comicvine_issue_id")
    return {
        "source": "ComicInfo.xml",
        "source_id": source_id,
        "record_type": "single_issue",
        "title": embedded.get("series"),
        "subtitle": embedded_title,
        "issue": embedded_issue or parsed.issue,
        "volume": None,
        "creators": creators,
        "contributors": contributors,
        "cover_contributors": contributors.get("cover_artist", []),
        "publisher": embedded.get("publisher"),
        "publication_year": int(year_match.group(1)) if year_match else parsed.year,
        "publication_date": None,
        "isbns": [],
        "cover": None,
        "url": embedded.get("web"),
        "format": embedded.get("format"),
        "language": embedded.get("language"),
        "description": embedded.get("summary"),
        "named_contents": [embedded_title] if embedded_title else [],
        "matched_edition": {
            "isbn_10": [], "isbn_13": [], "number_of_pages": None, "coverage": [],
        },
        "coverage_status": "single issue; volume coverage does not apply",
        "match_score": 82,
        "match_score_note": "internal candidate ranking value; not a confidence percentage",
        "match_reasons": [
            "filename and embedded series agree",
            f"filename and embedded metadata agree on issue {parsed.issue}",
            "used as a resilient fallback while an external catalog is unavailable",
        ],
        "verification_status": "matched from filename plus embedded ComicInfo.xml; not confirmed by an external catalog",
        "identity_confidence": {
            "scope": "series and issue identity",
            "level": "medium",
            "score": confidence_score,
            "corroborated_fields": corroborated,
            "conflicts": conflicts,
            "limitations": [
                "filename and ComicInfo.xml may have originated from the same release source",
                "external catalog confirmation is unavailable",
            ],
        },
    }


def inventory_file(parsed: ParsedFile) -> dict[str, Any]:
    """Build a useful catalog record without calling external metadata services."""
    file_health = inspect_file_health(Path(parsed.path))
    embedded = read_embedded_metadata(Path(parsed.path))
    local_cover = file_cover_info(Path(parsed.path), embedded)
    lookup = lookup_identity(parsed, embedded)
    embedded_candidate = embedded_epub_candidate(lookup, embedded) or embedded_comicinfo_candidate(parsed, embedded)
    return {
        "parsed": asdict(parsed),
        "file_health": file_health,
        "embedded_metadata": embedded,
        "lookup_identity": asdict(lookup),
        "candidates": {"embedded": [embedded_candidate] if embedded_candidate else []},
        "recommendation": embedded_candidate,
        "file_cover": local_cover,
        "errors": {},
        "source_status": {"external_metadata": "deferred during fast library inventory"},
        "gcd_search_url": "https://www.comics.org/searchNew/?q=" + urllib.parse.quote_plus(lookup.title),
        "note": "This inventory pass used filenames and metadata embedded in the comic only. External matching can be refreshed after the library is visible.",
    }


def enrich(parsed: ParsedFile) -> dict[str, Any]:
    file_health = inspect_file_health(Path(parsed.path))
    embedded = read_embedded_metadata(Path(parsed.path))
    local_cover = file_cover_info(Path(parsed.path), embedded)
    lookup = lookup_identity(parsed, embedded)
    sources: dict[str, Any] = {}
    errors: dict[str, str] = {}
    source_status: dict[str, str] = {}
    if lookup.issue:
        sources["open_library"] = []
        source_status["open_library"] = "skipped for a filename classified as a single issue"
    else:
        try:
            sources["open_library"] = search_open_library(lookup)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors["open_library"] = str(exc)
            sources["open_library"] = []
    if lookup.issue:
        sources["google_books"] = []
        source_status["google_books"] = "skipped for a filename classified as a single issue"
    elif not sources["open_library"] and GOOGLE_BOOKS_API_KEY:
        try:
            sources["google_books"] = search_google_books(lookup)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                errors["google_books"] = "Rate limited by Google Books; try again later."
            else:
                errors["google_books"] = str(exc)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors["google_books"] = str(exc)
    else:
        sources["google_books"] = []
        if not GOOGLE_BOOKS_API_KEY:
            source_status["google_books"] = "disabled; set GOOGLE_BOOKS_API_KEY to enable it"
    try:
        sources["gcd"] = search_gcd(lookup)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            errors["gcd"] = "GCD anonymous API rate limit reached; cached results remain available."
        else:
            errors["gcd"] = str(exc)
        sources["gcd"] = []
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        errors["gcd"] = str(exc)
        sources["gcd"] = []
    for candidate in sources["gcd"]:
        confidence = assess_identity_confidence(parsed, embedded, candidate)
        if confidence:
            candidate["identity_confidence"] = confidence
    ranked_candidates = []
    for rank, candidate in enumerate(sources.get("open_library", [])):
        candidate = dict(candidate)
        similarity = difflib.SequenceMatcher(
            None, normalized_title(candidate.get("title")), normalized_title(lookup.title)
        ).ratio()
        # Edition endpoints are relatively slow. Inspect only the two strongest
        # search results; GCD independently validates the collection volume.
        if lookup.volume and similarity >= 0.8 and rank < 2:
            try:
                candidate["matched_edition"] = find_open_library_volume_edition(candidate, lookup.volume)
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                candidate["matched_edition"] = None
        candidate["match_score"], candidate["match_reasons"] = score_candidate(lookup, candidate, rank)
        ranked_candidates.append(candidate)
    ranked_candidates.sort(key=lambda item: item["match_score"], reverse=True)
    sources["open_library"] = ranked_candidates
    recommendation = next(
        (
            candidate for candidate in ranked_candidates
            if candidate["match_score"] >= 50
            and (
                (lookup.isbn and lookup.isbn in {normalize_isbn(value) for value in candidate.get("isbns", [])})
                or difflib.SequenceMatcher(
                    None, normalized_title(candidate.get("title")), normalized_title(lookup.title)
                ).ratio() >= 0.8
            )
        ),
        None,
    )
    gcd_recommendation = next(
        (candidate for candidate in sources["gcd"] if candidate["match_score"] >= 80),
        None,
    )
    if gcd_recommendation and (
        recommendation is None
        or gcd_recommendation["match_score"] > recommendation["match_score"]
    ):
        recommendation = gcd_recommendation
    embedded_candidate = embedded_epub_candidate(lookup, embedded) or embedded_comicinfo_candidate(parsed, embedded)
    sources["embedded"] = [embedded_candidate] if embedded_candidate else []
    if recommendation is None and embedded_candidate:
        recommendation = embedded_candidate
    gcd_query = " ".join(str(x) for x in (lookup.title, lookup.volume or "", lookup.year or "") if x)
    return {
        "parsed": asdict(parsed),
        "file_health": file_health,
        "embedded_metadata": embedded,
        "lookup_identity": asdict(lookup),
        "candidates": sources,
        "recommendation": recommendation,
        "file_cover": local_cover,
        "errors": errors,
        "source_status": source_status,
        "gcd_search_url": "https://www.comics.org/searchNew/?q=" + urllib.parse.quote_plus(gcd_query),
        "note": "match_score is an internal ranking value, not a percentage. For single issues, identity_confidence explains cross-source agreement and conflicts. GCD edition data is ingested through its public API; inferred collection coverage is labeled separately.",
    }


def search_file_match_candidates(file_id: int, query: str) -> dict[str, Any]:
    """Search enabled catalogs from Fix Match without changing the selected identity."""
    cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
    if len(cleaned) < 2:
        raise ValueError("Enter at least two characters to search for a match")
    store = catalog_store()
    workbench = store.get_file_workbench(file_id)
    effective = {**(workbench.get("current") or {}), **(workbench.get("override") or {})}
    _cleaned_query, title_query, year_hint = _discovery_query_parts(cleaned)
    isbn_match = find_isbn(cleaned)
    parsed = ParsedFile(
        path=str((workbench.get("file") or {}).get("path") or ""),
        filename=str((workbench.get("file") or {}).get("filename") or ""),
        extension=Path(str((workbench.get("file") or {}).get("filename") or "")).suffix.lower(),
        title=title_query or cleaned,
        volume=effective.get("volumeNumber"),
        issue=str(effective.get("issueNumber")) if effective.get("recordType") == "issue" and effective.get("issueNumber") not in {None, ""} else None,
        year=year_hint or effective.get("publicationYear"),
        format=effective.get("format"),
        isbn=normalize_isbn(isbn_match.group(1)) if isbn_match else None,
        warnings=[],
    )
    candidates: list[dict[str, Any]] = []
    providers_checked: list[str] = []
    errors: list[dict[str, str]] = []

    def attempt(name: str, search: Any) -> Any:
        providers_checked.append(name)
        try:
            return search()
        except Exception as exc:
            errors.append({"provider": name, "error": str(exc)})
            return None

    def issue_candidate(
        provider_name: str,
        provider_series_id: str,
        source_url: str,
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert one provider issue-list hit into a selectable Fix Match candidate."""
        wanted_number = str(parsed.issue or "").strip().lstrip("0") or "0"
        matched = next((
            entry for entry in entries
            if (str(entry.get("number") or "").strip().lstrip("0") or "0") == wanted_number
        ), None)
        if not matched:
            return []
        publication_year = matched.get("publication_year") or _provider_year(
            matched.get("publication_date")
        )
        score = 90
        reasons = [
            f"{provider_name} issue number matches #{parsed.issue}",
            f"Publication run matched from the Fix Match search for {cleaned}",
        ]
        if parsed.year and publication_year == parsed.year:
            score += 10
            reasons.append(f"Issue was published in {parsed.year}")
        return [{
            "source": provider_name,
            "source_id": matched.get("provider_id"),
            "provider_series_id": provider_series_id,
            "record_type": "single_issue",
            "title": parsed.title,
            "subtitle": matched.get("title"),
            "description": matched.get("description"),
            "provider_evidence": matched.get("provider_evidence"),
            "issue": str(matched.get("number") or parsed.issue),
            "volume": None,
            "creators": [],
            "publisher": effective.get("publisher"),
            "publication_year": publication_year or parsed.year,
            "publication_date": matched.get("publication_date"),
            "isbns": [],
            "cover": matched.get("cover"),
            "url": matched.get("api_url") or source_url,
            "format": effective.get("format"),
            "search_query": cleaned,
            "match_score": score,
            "match_reasons": reasons,
            "verification_status": (
                f"Single issue matched through {provider_name} by publication run and issue number; "
                "review before selecting"
            ),
            "matched_edition": {
                "isbn_10": [], "isbn_13": [], "number_of_pages": None, "coverage": [],
            },
        }]

    def gcd_issue_candidates() -> list[dict[str, Any]]:
        """Use GCD's paged, year-aware run search instead of its narrow first result page."""
        context = {
            "title": parsed.title,
            "year": parsed.year,
            "publisher": effective.get("publisher"),
            "aliases": [],
            "ownedIssueNumbers": [parsed.issue] if parsed.issue else [],
        }
        ranked_runs = rank_gcd_series_runs(
            context, _gcd_discovery_search_rows(parsed.title)
        )
        results: list[dict[str, Any]] = []
        wanted_number = str(parsed.issue or "").strip().lstrip("0") or "0"
        for run in ranked_runs:
            run_issue_numbers = {
                str(value).strip().lstrip("0") or "0"
                for value in run.get("issueNumbers") or []
            }
            if wanted_number not in run_issue_numbers:
                continue
            series = run.get("_series") or {}
            entries = _gcd_canonical_issue_entries(series)
            entry = next((
                item for item in entries
                if (str(item.get("number") or "").strip().lstrip("0") or "0") == wanted_number
            ), None)
            if not entry or not entry.get("api_url"):
                continue
            issue = fetch_gcd_json(str(entry["api_url"]))
            candidate = gcd_candidate_from_issue(
                parsed,
                parsed.title,
                series,
                str(entry.get("descriptor") or parsed.issue),
                issue,
                [ranked.get("_series") or {} for ranked in ranked_runs],
                int(run.get("matchScore") or 0),
                [*(run.get("matchReasons") or []), "Explicit Fix Match search"],
                record_kind="single_issue",
            )
            candidate["provider_series_id"] = str(run.get("providerSeriesId") or "") or None
            candidate["verification_status"] = (
                "Single issue returned by the GCD year-aware publication-run search; review before selecting"
            )
            results.append(candidate)
            if len(results) == 3:
                break
        return results

    if effective.get("recordType") == "issue":
        # Fix Match follows the same provider priority as library enrichment:
        # authenticated catalogs first, anonymous GCD only as a fallback.
        for provider_id, values in _series_enrichment_provider_order():
            if provider_id == "metron":
                token = str(values.get("token") or "").strip()
                if not token:
                    continue
                result = attempt("Metron", lambda: _metron_issue_entries({
                    "title": parsed.title,
                    "year": parsed.year,
                    "publisher": effective.get("publisher"),
                    "strictYear": True,
                }, token))
                found = issue_candidate("Metron", *result[:3]) if result else []
            elif provider_id == "comic_vine":
                api_key = str(values.get("apiKey") or "").strip()
                if not api_key:
                    continue
                result = attempt("Comic Vine", lambda: _comic_vine_issue_entries({
                    "title": parsed.title,
                    "year": parsed.year,
                    "publisher": effective.get("publisher"),
                    "strictYear": True,
                }, api_key))
                found = issue_candidate("Comic Vine", *result) if result else []
            else:
                found = attempt("Grand Comics Database", gcd_issue_candidates) or []
            if found:
                candidates.extend(found)
                break

    if effective.get("recordType") != "issue":
        config = load_provider_config()
        metron = config.get("metron") or {}
        strong_collected_match = False
        if metron.get("enabled") and metron.get("token"):
            collected = attempt(
                "Metron",
                lambda: _metron_collected_edition_candidates(
                    parsed, str(metron["token"])
                ),
            ) or []
            candidates.extend(collected)
            strong_collected_match = any(
                int(candidate.get("match_score") or 0) >= 85
                and any(
                    claim.get("relation_kind") == "full_issue"
                    for claim in (candidate.get("matched_edition") or {}).get("coverage") or []
                )
                for candidate in collected
            )

        # A structured edition match is stronger than a generic publication-run
        # result and avoids spending more provider quota. Fall back to broad
        # catalogs only when Metron cannot identify an exact collected edition.
        if not strong_collected_match:
            open_library = attempt("Open Library", lambda: search_open_library(parsed)) or []
            for rank, item in enumerate(open_library):
                candidate = dict(item)
                candidate["match_score"], candidate["match_reasons"] = score_candidate(parsed, candidate, rank)
                candidate["verification_status"] = "Returned by an explicit Fix Match search; review before selecting"
                candidates.append(candidate)

            optional_searches: list[tuple[str, Any]] = []
            if metron.get("enabled") and metron.get("token"):
                optional_searches.append(("Metron", lambda: discover_metron_series(cleaned, str(metron["token"]))))
            comic_vine = config.get("comic_vine") or {}
            if comic_vine.get("enabled") and comic_vine.get("apiKey"):
                optional_searches.append(("Comic Vine", lambda: discover_comic_vine_series(cleaned, str(comic_vine["apiKey"]))))
            for name, search in optional_searches:
                discovery = attempt(name, search) or {}
                for rank, item in enumerate(discovery.get("results") or []):
                    creators = [
                        creator.get("name") if isinstance(creator, dict) else str(creator)
                        for creator in (item.get("creators") or [])
                        if (creator.get("name") if isinstance(creator, dict) else str(creator)).strip()
                    ]
                    similarity = difflib.SequenceMatcher(
                        None, normalized_title(item.get("title")), normalized_title(parsed.title)
                    ).ratio()
                    candidate = {
                        "source": item.get("providerName") or name,
                        "source_id": item.get("providerSeriesId"),
                        "title": item.get("title"),
                        "subtitle": None,
                        "description": item.get("description"),
                        "creators": creators,
                        "publisher": item.get("publisher"),
                        "publication_year": item.get("yearBegan"),
                        "isbns": [],
                        "cover": item.get("cover"),
                        "url": item.get("url"),
                        "search_query": cleaned,
                        "match_score": max(20, round(similarity * 100) - rank),
                        "match_reasons": [
                            f"Explicit Fix Match search for {cleaned}",
                            f"Series title similarity {round(similarity * 100)}%",
                        ],
                        "verification_status": "Publication run returned by an enabled metadata service; review before selecting",
                        "matched_edition": {
                            "isbn_10": [], "isbn_13": [], "number_of_pages": None, "coverage": [],
                        },
                    }
                    candidates.append(candidate)

            gcd = attempt("Grand Comics Database", lambda: search_gcd(parsed)) or []
            for item in gcd:
                candidate = dict(item)
                candidate["verification_status"] = "Returned by an explicit Fix Match search; review before selecting"
                candidates.append(candidate)

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in sorted(
        candidates,
        key=lambda item: (int(item.get("match_score") or 0), bool(item.get("cover"))),
        reverse=True,
    ):
        key = (
            normalized_title(candidate.get("source")),
            str(candidate.get("source_id") or ""),
            normalized_title(candidate.get("title")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) == 24:
            break
    return store.retain_file_search_candidates(
        file_id, cleaned, unique, providers_checked=providers_checked, errors=errors
    )


def batch_enrich(folder: str, recursive: bool = True) -> list[dict[str, Any]]:
    files = scan_folder(folder, recursive)
    if len(files) < 2:
        return [enrich(item) for item in files]
    # External lookups dominate runtime. A small pool keeps the UI responsive
    # without flooding anonymous catalog APIs.
    worker_count = min(4, len(files))

    def safe_enrich(item: ParsedFile) -> dict[str, Any]:
        try:
            return enrich(item)
        except Exception as exc:
            gcd_query = " ".join(str(x) for x in (item.title, item.volume or "", item.year or "") if x)
            return {
                "parsed": asdict(item),
                "file_health": inspect_file_health(Path(item.path)),
                "embedded_metadata": {},
                "lookup_identity": asdict(item),
                "candidates": {},
                "recommendation": None,
                "file_cover": file_cover_info(Path(item.path)),
                "errors": {"analysis": f"Unexpected per-file error: {exc}"},
                "source_status": {},
                "gcd_search_url": "https://www.comics.org/searchNew/?q=" + urllib.parse.quote_plus(gcd_query),
                "note": "This file failed independently; analysis continued for the remaining files.",
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(safe_enrich, files))


def render_batch_results(results: list[dict[str, Any]]) -> str:
    cards = []
    matched = sum(1 for result in results if result.get("recommendation"))
    file_problems = sum(
        1 for result in results if (result.get("file_health") or {}).get("status") == "error"
    )
    for result in results:
        parsed = result["parsed"]
        recommendation = result.get("recommendation")
        raw_params = urllib.parse.urlencode({"path": parsed["path"]})
        if recommendation:
            edition = recommendation.get("matched_edition") or {}
            title = recommendation.get("title") or parsed["title"]
            subtitle = recommendation.get("subtitle")
            if recommendation.get("record_type") == "single_issue" and recommendation.get("issue"):
                display_title = f"{title} #{recommendation['issue']}"
                if subtitle:
                    display_title += f": {subtitle}"
            else:
                display_title = f"{title}: {subtitle}" if subtitle else title
            isbns = edition.get("isbn_13") or edition.get("isbn_10") or recommendation.get("isbns", [])
            coverage = edition.get("coverage", [])
            coverage_html = ""
            if coverage:
                rows = []
                for claim in coverage:
                    issue_label = claim["start_issue"] if claim["start_issue"] == claim["end_issue"] else f"{claim['start_issue']}–{claim['end_issue']}"
                    confidence = f" · {claim['confidence']}" if claim.get("confidence") else ""
                    rows.append(
                        f"<li><strong>{html.escape(claim['series'])}</strong> #{html.escape(issue_label)} "
                        f"<span class='muted'>({len(claim['issues'])} issues{html.escape(confidence)})</span></li>"
                    )
                coverage_html = f"<div><h3>Collected issue coverage</h3><ul>{''.join(rows)}</ul></div>"
            else:
                coverage_status = recommendation.get("coverage_status") or "No structured issue coverage found in this edition record."
                coverage_html = f"<p class='muted'>{html.escape(coverage_status)}</p>"
            local_cover = result.get("file_cover") or {}
            cover = local_cover.get("url") or recommendation.get("cover")
            cover_html = f"<img class='cover' src='{html.escape(cover, quote=True)}' alt='Cover for {html.escape(display_title, quote=True)}'>" if cover else ""
            cover_source_html = (
                "<div class='cover-source'>Cover from comic file</div>"
                if local_cover.get("url") else
                (f"<div class='cover-source'>Cover from {html.escape(recommendation.get('source') or 'metadata source')}</div>" if cover else "")
            )
            reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in recommendation.get("match_reasons", []))
            facts = [
                recommendation.get("publisher"),
                str(recommendation.get("publication_year") or ""),
                recommendation.get("format"),
                recommendation.get("language"),
                f"{edition.get('number_of_pages')} pages" if edition.get("number_of_pages") else None,
            ]
            facts_html = " · ".join(html.escape(value) for value in facts if value)
            isbn_html = ", ".join(html.escape(value) for value in isbns)
            verification = recommendation.get("verification_status")
            verification_html = f"<p class='warn'><strong>Verification:</strong> {html.escape(verification)}</p>" if verification else ""
            identity = recommendation.get("identity_confidence") or {}
            if identity:
                score_label = f"{identity.get('level', 'unknown').title()} identity confidence · {identity.get('score', 0)}%"
                evidence_items = "".join(
                    f"<li>{html.escape(value)}</li>" for value in identity.get("corroborated_fields", [])
                )
                conflict_items = "".join(
                    f"<li class='warn'>{html.escape(value)}</li>" for value in identity.get("conflicts", [])
                )
                limitation_items = "".join(
                    f"<li class='muted'>{html.escape(value)}</li>" for value in identity.get("limitations", [])
                )
                confidence_html = (
                    "<details><summary>Identity confidence evidence</summary><ul>"
                    f"{evidence_items}{conflict_items}{limitation_items}</ul></details>"
                )
            else:
                score_label = f"Candidate score {recommendation['match_score']}"
                confidence_html = ""
            source_link = (
                f"<a target='_blank' href='{html.escape(recommendation['url'], quote=True)}'>{html.escape(recommendation.get('source') or 'Source record')}</a> · "
                if recommendation.get("url") else ""
            )
            named_contents = recommendation.get("named_contents", [])
            named_contents_html = ""
            if named_contents:
                named_contents_html = (
                    "<div><h3>Named contents</h3><ul>"
                    + "".join(f"<li>{html.escape(value)}</li>" for value in named_contents)
                    + "</ul></div>"
                )
            description_html = ""
            if recommendation.get("description"):
                description_html = f"<details><summary>Description</summary><p>{html.escape(recommendation['description'])}</p></details>"
            match_body = (
                f"<div class='match-grid'><div>{cover_html}{cover_source_html}</div><div><div class='score'>{html.escape(score_label)}</div>"
                f"<h2>{html.escape(display_title)}</h2><p>{facts_html}</p>"
                f"<p><strong>ISBN:</strong> {isbn_html or 'Not available'}</p>"
                f"{verification_html}{confidence_html}{coverage_html}{named_contents_html}{description_html}"
                f"<details><summary>Why this matched</summary><ul>{reasons}</ul></details>"
                f"<p class='links'>{source_link}"
                f"<a target='_blank' href='{html.escape(result['gcd_search_url'], quote=True)}'>Search GCD</a> · "
                f"<a target='_blank' href='/api/enrich?{raw_params}'>Raw JSON</a></p></div></div>"
            )
        else:
            local_cover = result.get("file_cover") or {}
            local_cover_html = (
                f"<div><img class='cover' src='{html.escape(local_cover['url'], quote=True)}' alt='Cover from comic file'>"
                "<div class='cover-source'>Cover from comic file</div></div>"
                if local_cover.get("url") else ""
            )
            alternatives = (
                result.get("candidates", {}).get("gcd", [])
                + result.get("candidates", {}).get("open_library", [])
            )[:3]
            alternatives_html = "".join(
                f"<li>{html.escape(candidate.get('title') or 'Untitled')}"
                f"{': ' + html.escape(candidate.get('subtitle')) if candidate.get('subtitle') else ''}"
                f" — score {candidate.get('match_score')}</li>" for candidate in alternatives
            )
            match_body = (
                f"<div class='match-grid'>{local_cover_html}<div class='no-match'><h2>No confident match</h2>"
                f"<p>The parsed lookup title was <code>{html.escape(result['lookup_identity']['title'])}</code>.</p>"
                f"<ul>{alternatives_html}</ul><p><a target='_blank' href='/api/enrich?{raw_params}'>Inspect raw JSON</a></p></div></div>"
            )
        embedded = result.get("embedded_metadata") or {}
        embedded_label = embedded.get("source") or "None found"
        health = result.get("file_health") or {}
        has_file_problem = health.get("status") == "error"
        health_badge = (
            f"<span class='health-pill'>{html.escape(health.get('code', 'file_error').replace('_', ' ').title())}</span>"
            if has_file_problem else ""
        )
        health_alert = (
            "<div class='archive-alert' role='alert'><strong>File problem:</strong> "
            f"{html.escape(health.get('message') or 'The comic file failed its structural check.')}</div>"
            if has_file_problem else ""
        )
        cards.append(
            f"<article class='result-card'><header><code>{html.escape(parsed['filename'])}</code>"
            f"<span class='status-pills'>{health_badge}<span class='source-pill'>Embedded: {html.escape(embedded_label)}</span></span>"
            f"</header>{health_alert}{match_body}</article>"
        )
    problem_summary = (
        f" <strong class='problem-count'>{file_problems} file{'s' if file_problems != 1 else ''}</strong> "
        "failed structural checks and are flagged below."
        if file_problems else ""
    )
    return (
        f"<div class='batch-summary'><strong>{matched} of {len(results)}</strong> files have a confident recommendation. "
        f"Results are read-only and source-attributed.{problem_summary}</div>"
        f"<div class='results'>{''.join(cards)}</div>"
    )


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flipparr</title>
<style>
:root { color-scheme: dark; font-family: ui-sans-serif,system-ui,sans-serif; background:#11151c; color:#eef2f7 }
body { max-width:1180px; margin:0 auto; padding:32px 20px 80px }
h1 { margin-bottom:4px } .sub { color:#aab6c5; margin-top:0 }
form { display:grid; grid-template-columns:1fr auto auto auto; gap:10px; background:#1a202b; padding:16px; border-radius:12px; align-items:center }
input[type=text] { min-width:0; padding:11px; border:1px solid #3a4556; border-radius:8px; background:#10141b; color:white }
button { padding:10px 16px; border:0; border-radius:8px; background:#6d5dfc; color:white; font-weight:700; cursor:pointer }
.secondary { display:inline-block; padding:10px 16px; border-radius:8px; background:#263247; color:#e8eeff; font-weight:700; text-decoration:none }
table { width:100%; border-collapse:collapse; margin-top:20px; background:#171d27 }
th,td { padding:10px; text-align:left; border-bottom:1px solid #2b3544; vertical-align:top }
th { color:#aebbd0 } code { color:#b7c9ff } .warn { color:#ffca72 } a { color:#9db8ff }
.empty,.error { padding:18px; background:#1a202b; border-radius:10px; margin-top:18px }.error { color:#ff9b9b }
.batch-actions { margin:18px 0 }.batch-summary { margin:20px 0; padding:14px 16px; background:#1a202b; border-radius:10px }
.results { display:grid; gap:18px }.result-card { background:#171d27; border:1px solid #2b3544; border-radius:14px; overflow:hidden }
.result-card header { display:flex; justify-content:space-between; gap:12px; padding:12px 16px; background:#1d2532; align-items:center }
.source-pill,.score { display:inline-block; padding:4px 8px; border-radius:999px; background:#263247; color:#c8d5e8; font-size:.82rem }
.status-pills { display:flex; gap:7px; align-items:center; flex-wrap:wrap; justify-content:flex-end }.health-pill { display:inline-block; padding:4px 8px; border-radius:999px; background:#762f35; color:#ffd9dc; font-size:.82rem; font-weight:700 }.archive-alert { padding:12px 16px; background:#4b2228; border-top:1px solid #8f3f47; border-bottom:1px solid #8f3f47; color:#ffd9dc }.problem-count { color:#ff9b9b }
.score { background:#244936; color:#adf0c8 }.match-grid { display:grid; grid-template-columns:140px 1fr; gap:20px; padding:20px }
.cover { width:140px; max-height:210px; object-fit:contain; border-radius:7px; background:#202938 }.cover-source { width:140px; margin-top:6px; color:#99a8bb; font-size:.75rem; text-align:center }.match-grid h2 { margin:8px 0 4px }
.match-grid h3 { margin-bottom:4px }.muted { color:#99a8bb }.links { margin-top:16px }.no-match { padding:20px }
details { margin-top:12px } summary { cursor:pointer; color:#c8d5e8 }
@media (max-width:720px) { form { grid-template-columns:1fr 1fr } input[type=text] { grid-column:1/-1 } .match-grid { grid-template-columns:1fr } .cover { width:110px } }
</style></head><body>
<h1>Flipparr</h1><p class="sub">Read-only filename parsing and metadata candidate lookup.</p>
<form id="scan-form" method="get" action="/"><input id="folder" name="folder" type="text" value="__FOLDER__" placeholder="/path/to/comics" required>
<button id="choose-folder" type="button">Choose Folder…</button>
<label><input type="checkbox" name="recursive" value="1" __CHECKED__> Recursive</label><button type="submit">Scan folder</button></form>
<p id="picker-status" class="sub" aria-live="polite"></p>
__CONTENT__
<script>
const chooseButton = document.getElementById('choose-folder');
const folderInput = document.getElementById('folder');
const pickerStatus = document.getElementById('picker-status');
chooseButton.addEventListener('click', async () => {
  chooseButton.disabled = true;
  pickerStatus.textContent = 'Opening Finder…';
  try {
    const response = await fetch('/api/pick-folder');
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Folder selection failed');
    if (result.folder) {
      folderInput.value = result.folder;
      pickerStatus.textContent = 'Folder selected. Choose Scan folder to continue.';
    } else {
      pickerStatus.textContent = 'Folder selection canceled.';
    }
  } catch (error) {
    pickerStatus.textContent = error.message;
  } finally {
    chooseButton.disabled = false;
  }
});
</script>
</body></html>"""


def catalog_api_payload() -> dict[str, Any]:
    """Add provider availability to the public catalog without exposing credentials."""
    store = catalog_store()
    payload = store.catalog(preferred_language())
    enrichment = payload.get("enrichment") or {}
    provider_ids = [provider_id for provider_id, _values in _series_enrichment_provider_order()]
    cooldown_ids = {
        str(item.get("provider"))
        for item in (enrichment.get("providerCooldowns") or [])
        if item.get("provider")
    }
    enrichment["enabledProviders"] = provider_ids
    enrichment["availableProviders"] = [
        provider_id for provider_id in provider_ids
        if store.metadata_provider_available(provider_id)
    ]
    # Short success pacing delays are intentionally excluded from providerCooldowns.
    # Only show a global pause when every enabled issue catalog has an active error.
    enrichment["allProvidersCooling"] = bool(provider_ids) and all(
        provider_id in cooldown_ids for provider_id in provider_ids
    )
    payload["enrichment"] = enrichment
    payload["collectedEditionsEnabled"] = collected_editions_enabled()
    return payload


COMPRESSIBLE_TYPES = (
    "application/json", "application/javascript", "image/svg+xml",
    "application/manifest+json", "text/",
)
# Below roughly one packet, compression costs more than it saves.
GZIP_MIN_BYTES = 1024


def _gzip_if_worthwhile(
    body: bytes, content_type: str, accept_encoding: str
) -> tuple[bytes, str | None]:
    """Compress a response when the client asked and the type benefits.

    The catalog payload for a real library is megabytes of highly repetitive
    JSON, so this is the difference between a multi-megabyte and a
    hundred-kilobyte response on every load.
    """
    if "gzip" not in (accept_encoding or "").lower():
        return body, None
    if len(body) < GZIP_MIN_BYTES:
        return body, None
    if not content_type.startswith(COMPRESSIBLE_TYPES):
        return body, None
    compressed = gzip.compress(body, compresslevel=6)
    if len(compressed) >= len(body):
        return body, None
    return compressed, "gzip"


class Handler(BaseHTTPRequestHandler):
    # Set once a status line has gone out, so the guard below knows whether it
    # is still safe to write an error response.
    _response_started = False

    _status: int | None = None

    def send_response(self, code, message=None):  # type: ignore[override]
        self._response_started = True
        self._status = code
        super().send_response(code, message)
        # Returned on every response so a user can quote it and land on the
        # exact server-side record for their request.
        request_id = current_request_id()
        if request_id:
            self.send_header("X-Request-Id", request_id)

    def _client_address(self) -> str:
        """The caller's real address, believing a forwarded header only from a
        peer we were told to trust.

        This is what keeps the local-address bypass safe. A reverse proxy makes
        every request arrive from the proxy, so if X-Forwarded-For were trusted
        unconditionally anyone could send `X-Forwarded-For: 127.0.0.1` and be
        treated as local — which would silently disable authentication.
        """
        peer = self.client_address[0] if self.client_address else ""
        if peer in trusted_proxies():
            forwarded = self.headers.get("X-Forwarded-For", "")
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        return peer

    def _request_is_https(self) -> bool:
        """Whether the browser reached us over TLS, which only a proxy can say.

        The app speaks plain HTTP, so HTTPS always means something in front
        terminated it. That claim arrives in a header, and a header from an
        untrusted peer is a claim anyone can make -- so it counts only from an
        address the operator named.
        """
        global _PROXY_HEADER_WARNED
        peer = self.client_address[0] if self.client_address else ""
        claimed = self.headers.get("X-Forwarded-Proto", "").strip().lower() == "https"
        if peer in trusted_proxies():
            return claimed
        if claimed and not _PROXY_HEADER_WARNED:
            # Otherwise this fails silently: the session cookie quietly loses
            # its Secure flag on a deployment that looks correctly served.
            _PROXY_HEADER_WARNED = True
            log_event(
                "untrusted_forwarded_proto", level="warning", peer=peer,
                detail="A request claimed HTTPS via X-Forwarded-Proto but came from "
                       "an address not in FLIPPARR_TRUSTED_PROXIES, so it is ignored "
                       "and session cookies are not marked Secure. Set "
                       "FLIPPARR_TRUSTED_PROXIES to your proxy's address.",
            )
        return False

    def _authorized(self, path: str) -> bool:
        # An unreadable config denies everything except the health check, rather
        # than quietly serving an unprotected instance.
        config = load_auth_config()
        if config["method"] == "none":
            return True
        if path in _AUTH_EXEMPT_PATHS:
            return True
        protected = path.startswith("/api/") or path in _PROTECTED_PATHS
        if not protected:
            # The app shell and its assets must load unauthenticated, or there
            # is no page on which to present the login form.
            return True
        if config["localBypass"] and is_local_address(self._client_address()):
            return True
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(_SESSION_COOKIE)
        return bool(morsel and session_token_valid(morsel.value, config))

    def _same_origin(self) -> bool:
        """Reject cross-site state changes.

        SameSite=Lax already stops the browser sending the session cookie on a
        cross-site POST; this is the second lock, and also covers clients that
        ignore SameSite.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        try:
            return urllib.parse.urlsplit(origin).netloc == host
        except ValueError:
            return False

    def _dispatch(self, route: Callable[[], None]) -> None:
        """Turn an unhandled failure into a JSON 500 rather than a dead socket.

        Routes keep their own specific error mappings; this catches only what
        none of them did. Without it an unexpected exception propagates out of
        the handler and the client sees the connection close with no response
        at all, which is indistinguishable from the server being down.

        This is also where a request gets its id: assigned before anything can
        fail, so even a rejection is traceable.
        """
        path = urllib.parse.urlparse(self.path).path
        _REQUEST_CONTEXT.request_id = secrets.token_hex(6)
        self._status = None
        started = time.monotonic()
        try:
            try:
                authorized = path == "/healthz" or self._authorized(path)
            except AuthConfigUnreadable as exc:
                log_exception("auth_config_unreadable", exc, path=path)
                self.send_json({"error": f"Authentication is misconfigured: {exc}"}, 503)
                return
            if not authorized:
                self.send_json({"error": "Authentication required"}, 401)
                return
            if self.command in {"POST", "PATCH", "DELETE"} and not self._same_origin():
                self.send_json({"error": "Cross-site request rejected"}, 403)
                return
            try:
                route()
            except Exception as exc:
                log_exception("request_failed", exc, method=self.command, path=path)
                if self._response_started:
                    return
                self.send_json(
                    {"error": "The server could not complete this request."}, 500
                )
        finally:
            # Path only: a query string can carry anything a caller put there,
            # and this line is meant to be safe to paste into a bug report.
            log_event(
                "http_request",
                method=self.command,
                path=path,
                status=self._status,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                client=self._client_address(),
            )
            _REQUEST_CONTEXT.request_id = None

    def do_GET(self) -> None:
        self._dispatch(self._route_get)

    def do_POST(self) -> None:
        self._dispatch(self._route_post)

    def do_PATCH(self) -> None:
        self._dispatch(self._route_patch)

    def do_DELETE(self) -> None:
        self._dispatch(self._route_delete)

    def _route_get(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path == "/healthz":
            # Separate from "/" so the container healthcheck keeps working once
            # authentication is switched on.
            self.send_json({"status": "ok", "version": APP_VERSION, "build": APP_BUILD})
            return
        if parsed_url.path == "/api/v1/auth/status":
            # Reachable unauthenticated: the app needs to know whether to show a
            # login form. Deliberately does not reveal the username.
            config = load_auth_config()
            self.send_json({
                "method": config["method"],
                "authenticated": self._authorized("/api/v1/catalog"),
            })
            return
        if parsed_url.path == "/api/v1/auth":
            self.send_json(public_auth_config())
            return
        if parsed_url.path == "/api/v1/catalog":
            self.send_json(catalog_api_payload())
            return
        if parsed_url.path == "/api/v1/settings":
            self.send_json(load_app_settings())
            return
        if parsed_url.path == "/api/v1/acquisition/progress":
            self.send_json(acquisition_download_progress())
            return
        if parsed_url.path == "/api/v1/providers":
            self.send_json(public_provider_config())
            return
        if parsed_url.path == "/api/v1/acquisition-services":
            self.send_json(public_acquisition_service_config())
            return
        if parsed_url.path == "/api/v1/discover/run":
            params = urllib.parse.parse_qs(parsed_url.query)
            ids = {key: (params.get(key) or [""])[0] for key in _PREVIEW_ORDER}
            try:
                self.send_json(preview_discovered_run(ids, (params.get("query") or [""])[0]))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": f"This run's issues could not be listed: {exc}"}, 502)
            return
        if parsed_url.path == "/api/v1/discover/issue":
            issue_id = (urllib.parse.parse_qs(parsed_url.query).get("id") or [""])[0]
            try:
                self.send_json(discovered_issue_detail(issue_id))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": f"This issue's details are unavailable: {exc}"}, 502)
            return
        if parsed_url.path == "/api/v1/discover/releases":
            try:
                self.send_json(release_calendar())
            except Exception as exc:
                self.send_json({"error": f"Release dates are temporarily unavailable: {exc}"}, 502)
            return
        if parsed_url.path == "/api/v1/discover/people":
            query = urllib.parse.parse_qs(parsed_url.query).get("query", [""])[0]
            try:
                self.send_json(discover_people_and_publishers(query))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": f"Creator and publisher search is temporarily unavailable: {exc}"}, 502)
            return
        if parsed_url.path == "/api/v1/discover":
            query = urllib.parse.parse_qs(parsed_url.query).get("query", [""])[0]
            try:
                self.send_json(discover_series(query))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": f"Series discovery is temporarily unavailable: {exc}"}, 502)
            return
        collection_structure = re.fullmatch(r"/api/v1/collections/(\d+)/structure", parsed_url.path)
        if collection_structure:
            try:
                result = catalog_store().story_structure_proposal(int(collection_structure.group(1)))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(result)
            return
        scan_match = re.fullmatch(r"/api/v1/scans/(\d+)", parsed_url.path)
        if scan_match:
            scan = catalog_store().get_scan(int(scan_match.group(1)))
            self.send_json(scan or {"error": "Scan not found"}, 200 if scan else 404)
            return
        series_run_search = re.fullmatch(r"/api/v1/series/(\d+)/issue-runs", parsed_url.path)
        if series_run_search:
            try:
                result = find_gcd_series_runs(int(series_run_search.group(1)))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"Series-run search failed: {exc}"}, 502)
                return
            self.send_json(result)
            return
        file_match = re.fullmatch(r"/api/v1/files/(\d+)", parsed_url.path)
        if file_match:
            try:
                payload = catalog_store().get_file_workbench(int(file_match.group(1)))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(payload)
            return
        uploaded_cover_match = re.fullmatch(r"/api/v1/files/(\d+)/cover/image", parsed_url.path)
        if uploaded_cover_match:
            self.handle_uploaded_cover(int(uploaded_cover_match.group(1)))
            return
        series_cover_image_match = re.fullmatch(r"/api/v1/series/(\d+)/cover/image", parsed_url.path)
        if series_cover_image_match:
            self.handle_uploaded_cover(int(series_cover_image_match.group(1)), "series")
            return
        match_candidates = re.fullmatch(
            r"/api/v1/series/(\d+)/match-candidates", parsed_url.path
        )
        if match_candidates:
            try:
                payload = series_match_candidates(
                    int(match_candidates.group(1)),
                    self.query(parsed_url).get("query"),
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(payload)
            return
        series_removal = re.fullmatch(r"/api/v1/series/(\d+)/removal", parsed_url.path)
        if series_removal:
            try:
                payload = series_removal_preview(int(series_removal.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(payload)
            return
        series_synopsis_match = re.fullmatch(r"/api/v1/series/(\d+)/synopsis", parsed_url.path)
        if series_synopsis_match:
            try:
                payload = series_synopsis(int(series_synopsis_match.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(payload)
            return
        series_cover_match = re.fullmatch(r"/api/v1/series/(\d+)/cover", parsed_url.path)
        if series_cover_match:
            try:
                payload = catalog_store().get_series_cover_workbench(
                    int(series_cover_match.group(1))
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(payload)
            return
        if parsed_url.path == "/api/enrich":
            self.handle_enrich(parsed_url)
            return
        if parsed_url.path == "/api/scan":
            self.handle_scan(parsed_url)
            return
        if parsed_url.path == "/api/batch":
            self.handle_batch(parsed_url)
            return
        if parsed_url.path == "/api/embedded-cover":
            self.handle_file_cover(parsed_url)
            return
        if parsed_url.path == "/api/file-cover":
            self.handle_file_cover(parsed_url)
            return
        if parsed_url.path == "/api/pick-folder":
            self.handle_pick_folder()
            return
        if parsed_url.path == "/results":
            self.handle_results(parsed_url)
            return
        configured_web_root = web_root()
        if configured_web_root and configured_web_root.is_dir():
            self.handle_web_asset(parsed_url.path)
            return
        self.handle_page(parsed_url)

    def _route_post(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        cover_upload_match = re.fullmatch(r"/api/v1/files/(\d+)/cover/upload", parsed_url.path)
        if cover_upload_match:
            self.handle_cover_upload(int(cover_upload_match.group(1)))
            return
        # Both upload routes carry a raw image, so they are matched before the
        # body is read as JSON.
        series_upload_match = re.fullmatch(r"/api/v1/series/(\d+)/cover/upload", parsed_url.path)
        if series_upload_match:
            self.handle_cover_upload(int(series_upload_match.group(1)), "series")
            return
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        if parsed_url.path == "/api/v1/auth/login":
            config = load_auth_config()
            if config["method"] == "none":
                self.send_json({"status": "authenticated"})
                return
            username = str(payload.get("username") or "")
            password = str(payload.get("password") or "")
            if not (
                hmac.compare_digest(username, str(config["username"] or ""))
                and verify_password(password, config["passwordHash"])
            ):
                # One message for both cases: a distinct "no such user" reply
                # would let an attacker enumerate valid usernames.
                self.send_json({"error": "Incorrect username or password"}, 401)
                return
            self.send_session_cookie(issue_session_token(config))
            return
        if parsed_url.path == "/api/v1/auth/logout":
            self.send_session_cookie("", expire=True)
            return
        if parsed_url.path == "/api/v1/auth":
            try:
                updated = save_auth_config(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            if updated["method"] == "forms":
                # Whoever just turned sign-in on (or changed the password) is
                # already authorised by definition -- they were allowed to make
                # this call. Issue them a session in the same response, or the
                # next request 401s and they are locked out of the very page
                # they would use to fix it.
                self.send_session_cookie(
                    issue_session_token(updated), payload=public_auth_config()
                )
                return
            self.send_json(public_auth_config())
            return
        provider_test_match = re.fullmatch(r"/api/v1/providers/([a-z_]+)/test", parsed_url.path)
        if provider_test_match:
            try:
                result = test_provider_connection(provider_test_match.group(1), payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except urllib.error.HTTPError as exc:
                message = "The provider rejected these credentials" if exc.code in {401, 403} else f"Provider request failed ({exc.code})"
                self.send_json({"error": message}, 400 if exc.code in {401, 403} else 502)
                return
            except (urllib.error.URLError, TimeoutError) as exc:
                self.send_json({"error": f"Could not reach the provider: {exc}"}, 502)
                return
            self.send_json(result)
            return
        provider_match = re.fullmatch(r"/api/v1/providers/([a-z_]+)", parsed_url.path)
        if provider_match:
            try:
                result = save_provider_config(provider_match.group(1), payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        service_test_match = re.fullmatch(r"/api/v1/acquisition-services/([a-z0-9_]+)/test", parsed_url.path)
        if service_test_match:
            try:
                result = test_acquisition_service_connection(service_test_match.group(1), payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except urllib.error.HTTPError as exc:
                message = "The service rejected these credentials" if exc.code in {401, 403} else f"Service request failed ({exc.code})"
                self.send_json({"error": message}, 400 if exc.code in {401, 403} else 502)
                return
            except (urllib.error.URLError, TimeoutError) as exc:
                self.send_json({"error": f"Could not reach the service: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}"}, 502)
                return
            self.send_json(result)
            return
        service_match = re.fullmatch(r"/api/v1/acquisition-services/([a-z0-9_]+)", parsed_url.path)
        if service_match:
            try:
                result = save_acquisition_service_config(service_match.group(1), payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/library-folder-check":
            try:
                self.send_json(inspect_library_folder(
                    str(payload.get("folder") or ""),
                    bool(payload.get("recursive", True)),
                ))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except OSError as exc:
                self.send_json({"error": f"Could not read that folder: {exc.strerror}"}, 400)
            return
        if parsed_url.path == "/api/v1/scans":
            folder = str(payload.get("folder") or "")
            recursive = bool(payload.get("recursive", True))
            metadata_mode = str(payload.get("metadataMode") or "full")
            try:
                scan_id = start_catalog_scan(folder, recursive, metadata_mode)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"id": scan_id, "status": "queued"}, 202)
            return
        if parsed_url.path == "/api/v1/reveal-file":
            self.handle_reveal_file(str(payload.get("path") or ""))
            return
        if parsed_url.path == "/api/v1/requests/search-missing":
            try:
                # Literal true only. Anything else -- a stray object, a
                # string, a number -- means the caller has not been asked yet,
                # and this starts a download for every issue it finds.
                result = start_missing_release_search(
                    payload.get("confirmed") is True
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result, 202 if result["status"] == "searching" else 200)
            return
        if parsed_url.path == "/api/v1/requests":
            try:
                request = catalog_store().create_acquisition_request(
                    str(payload.get("scopeType") or ""), int(payload.get("scopeId")),
                    str(payload.get("acquisitionPreference") or "").strip() or None,
                    payload.get("includeSpecials"),
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            _start_automatic_release_grabs(request)
            self.send_json(request, 201)
            return

        if parsed_url.path == "/api/v1/discover/pull-issue":
            try:
                numbers = payload.get("numbers")
                if isinstance(numbers, list) or payload.get("released"):
                    result = pull_discovered_issues(
                        str(payload.get("provider") or "metron"),
                        str(payload.get("providerSeriesId") or ""),
                        numbers if isinstance(numbers, list) else None,
                        released=bool(payload.get("released")),
                        query=str(payload.get("seriesTitle") or payload.get("query") or ""),
                    )
                else:
                    result = pull_discovered_issue(
                        str(payload.get("provider") or "metron"),
                        str(payload.get("providerSeriesId") or ""),
                        str(payload.get("number") or ""),
                        str(payload.get("seriesTitle") or ""),
                    )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"That issue could not be pulled: {exc}"}, 502)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/discover":
            try:
                result = request_discovered_series(
                    str(payload.get("provider") or "gcd"),
                    str(payload.get("query") or ""),
                    str(payload.get("providerSeriesId") or ""),
                    str(payload.get("acquisitionPreference") or "either"),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"Series could not be added: {exc}"}, 502)
                return
            self.send_json(result, 201)
            return
        file_replacement = re.fullmatch(r"/api/v1/files/(\d+)/replacement", parsed_url.path)
        if file_replacement:
            try:
                request = catalog_store().request_file_replacement(
                    int(file_replacement.group(1)),
                    str(payload.get("reason") or ""),
                    str(payload.get("desiredLanguage") or "").strip() or None,
                    str(payload.get("acquisitionPreference") or "either"),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            # This is the replacement's record, and its "id" is the
            # replacement's own number. Passed as it was, the search ran for
            # the acquisition request that happened to share that number --
            # Ultimate Spider-Man #3 and #16 searched requests 8 and 9 and sat
            # untouched until the next sweep.
            _start_automatic_release_grabs({"id": request.get("acquisitionRequestId")})
            self.send_json(request, 201)
            return
        series_format = re.fullmatch(r"/api/v1/series/(\d+)/format", parsed_url.path)
        if series_format:
            try:
                result = catalog_store().set_series_format(
                    int(series_format.group(1)), str(payload.get("format") or "")
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        replacement_status = re.fullmatch(r"/api/v1/replacements/(\d+)/status", parsed_url.path)
        if replacement_status:
            try:
                request = catalog_store().update_file_replacement_status(
                    int(replacement_status.group(1)),
                    str(payload.get("status") or ""),
                    str(payload.get("error") or "").strip() or None,
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(request)
            return
        acquisition_job = re.fullmatch(r"/api/v1/acquisition-jobs/(\d+)/status", parsed_url.path)
        if acquisition_job:
            try:
                job = catalog_store().update_acquisition_job(
                    int(acquisition_job.group(1)),
                    str(payload.get("status") or ""),
                    str(payload.get("detail") or "").strip() or None,
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(job)
            return
        acquisition_search = re.fullmatch(r"/api/v1/acquisition-jobs/(\d+)/search", parsed_url.path)
        if acquisition_search:
            job_id = int(acquisition_search.group(1))
            try:
                result = search_prowlarr_releases(
                    job_id, str(payload.get("query") or "").strip() or None
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except urllib.error.HTTPError as exc:
                message = "Prowlarr rejected the request" if exc.code in {401, 403} else f"Prowlarr search failed ({exc.code})"
                catalog_store().update_acquisition_job(job_id, "failed", message)
                self.send_json({"error": message}, 502)
                return
            except (urllib.error.URLError, TimeoutError) as exc:
                message = "Could not reach Prowlarr"
                catalog_store().update_acquisition_job(job_id, "failed", message)
                self.send_json({"error": message}, 502)
                return
            self.send_json(result)
            return
        acquisition_retry = re.fullmatch(r"/api/v1/acquisition-jobs/(\d+)/retry", parsed_url.path)
        if acquisition_retry:
            retry_job_id = int(acquisition_retry.group(1))
            try:
                result = catalog_store().retry_acquisition_job(retry_job_id)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            # Fixing a request should choose the best release available rather
            # than stopping to ask which one. The list is the fallback, not the
            # first step.
            if result["action"] == "research":
                grabbed = _auto_grab_release(retry_job_id)
                if grabbed:
                    release_title = str((grabbed.get("release") or {}).get("title") or "")
                    result = {
                        "id": str(retry_job_id), "status": "grabbed", "action": "grabbed",
                        "detail": grabbed.get("detail") or "Release sent to SABnzbd.",
                        "releaseTitle": release_title,
                    }
            self.send_json(result, 202 if result["action"] == "reprocess" else 200)
            return
        acquisition_grab = re.fullmatch(r"/api/v1/acquisition-jobs/(\d+)/grab", parsed_url.path)
        if acquisition_grab:
            job_id = int(acquisition_grab.group(1))
            try:
                result = send_release_to_sabnzbd(
                    job_id, str(payload.get("candidateId") or "").strip()
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except ReleaseDownloadError as exc:
                message = str(exc)
                catalog_store().update_acquisition_job(job_id, "failed", message)
                self.send_json({"error": message}, 502)
                return
            except SABSubmissionError as exc:
                message = str(exc)
                catalog_store().update_acquisition_job(job_id, "failed", message)
                self.send_json({"error": message}, 502)
                return
            self.send_json(result, 202)
            return
        collection_monitoring = re.fullmatch(
            r"/api/v1/collections/(\d+)/monitoring", parsed_url.path
        )
        if collection_monitoring:
            try:
                # As with a run, only a literal false stops following, so a
                # stray value cannot quietly cancel a collection's searches.
                if payload.get("monitored") is False:
                    result = catalog_store().stop_collection_monitoring(
                        int(collection_monitoring.group(1))
                    )
                else:
                    result = catalog_store().set_collection_monitoring(
                        int(collection_monitoring.group(1)),
                        str(payload.get("acquisitionPreference") or "either"),
                        bool(payload.get("includeSpecials", True)),
                    )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        series_rebuild = re.fullmatch(r"/api/v1/series/(\d+)/rebuild", parsed_url.path)
        if series_rebuild:
            try:
                self.send_json(rebuild_series_run(int(series_rebuild.group(1))))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        series_monitoring = re.fullmatch(r"/api/v1/series/(\d+)/monitoring", parsed_url.path)
        if series_monitoring:
            try:
                # Only a literal false stops following, so a missing or
                # malformed flag can never quietly cancel a run's searches.
                if payload.get("monitored") is False:
                    result = catalog_store().stop_series_monitoring(
                        int(series_monitoring.group(1))
                    )
                else:
                    result = catalog_store().set_series_monitoring(
                        int(series_monitoring.group(1)),
                        str(payload.get("acquisitionPreference") or "either"),
                    )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/reviews/resolve":
            file_path = str(payload.get("path") or "")
            code = str(payload.get("code") or "")
            fingerprint = str(payload.get("fingerprint") or "")
            if not all((file_path, code, fingerprint)):
                self.send_json({"error": "path, code, and fingerprint are required"}, 400)
                return
            catalog_store().resolve_review(file_path, code, fingerprint)
            self.send_json({"status": "resolved"})
            return
        file_metadata_match = re.fullmatch(r"/api/v1/files/(\d+)/metadata", parsed_url.path)
        if file_metadata_match:
            try:
                result = catalog_store().update_file_metadata(
                    int(file_metadata_match.group(1)), payload.get("fields") or {},
                    payload.get("lockedFields"), action="edit",
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        file_candidate_search = re.fullmatch(
            r"/api/v1/files/(\d+)/candidates/search", parsed_url.path
        )
        if file_candidate_search:
            try:
                result = search_file_match_candidates(
                    int(file_candidate_search.group(1)), str(payload.get("query") or "")
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"Metadata search is temporarily unavailable: {exc}"}, 502)
                return
            self.send_json(result)
            return
        file_fix_match = re.fullmatch(r"/api/v1/files/(\d+)/match", parsed_url.path)
        if file_fix_match:
            try:
                result = catalog_store().apply_file_match(
                    int(file_fix_match.group(1)), str(payload.get("candidateKey") or "")
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        file_reset_match = re.fullmatch(r"/api/v1/files/(\d+)/metadata/reset", parsed_url.path)
        if file_reset_match:
            try:
                result = catalog_store().reset_file_metadata(int(file_reset_match.group(1)))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        file_run_match = re.fullmatch(r"/api/v1/files/(\d+)/series-run", parsed_url.path)
        if file_run_match:
            try:
                run_id = payload.get("seriesId")
                result = catalog_store().move_file_to_series_run(
                    int(file_run_match.group(1)),
                    int(run_id) if run_id not in {None, ""} else None,
                    str(payload.get("title") or "").strip() or None,
                    payload.get("year"),
                    str(payload.get("publisher") or "").strip() or None,
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        file_contents_reset = re.fullmatch(r"/api/v1/files/(\d+)/contents/reset", parsed_url.path)
        if file_contents_reset:
            try:
                result = catalog_store().reset_file_collection_contents(int(file_contents_reset.group(1)))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        file_contents_match = re.fullmatch(r"/api/v1/files/(\d+)/contents", parsed_url.path)
        if file_contents_match:
            try:
                result = catalog_store().set_file_collection_contents(
                    int(file_contents_match.group(1)), int(payload.get("seriesId")),
                    payload.get("issueNumbers") or [], bool(payload.get("included", True)),
                    str(payload.get("note") or "").strip() or None,
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        series_match_post = re.fullmatch(r"/api/v1/series/(\d+)/match", parsed_url.path)
        if series_match_post:
            try:
                result = confirm_series_match(
                    int(series_match_post.group(1)),
                    str(payload.get("provider") or ""),
                    str(payload.get("providerSeriesId") or ""),
                    payload.get("query"),
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        series_cover_post = re.fullmatch(r"/api/v1/series/(\d+)/cover", parsed_url.path)
        if series_cover_post:
            file_id = str(payload.get("fileId") or "").strip()
            try:
                result = catalog_store().set_series_cover_preference(
                    int(series_cover_post.group(1)), str(payload.get("source") or ""),
                    str(payload.get("url") or "") or None,
                    int(file_id) if file_id.isdigit() else None,
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        file_cover_match = re.fullmatch(r"/api/v1/files/(\d+)/cover", parsed_url.path)
        if file_cover_match:
            try:
                result = catalog_store().set_file_cover_preference(
                    int(file_cover_match.group(1)), str(payload.get("source") or ""),
                    str(payload.get("url") or "") or None,
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        issue_metadata_reset = re.fullmatch(r"/api/v1/issues/(\d+)/metadata/reset", parsed_url.path)
        if issue_metadata_reset:
            try:
                result = catalog_store().reset_issue_metadata(int(issue_metadata_reset.group(1)))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        issue_metadata_match = re.fullmatch(r"/api/v1/issues/(\d+)/metadata", parsed_url.path)
        if issue_metadata_match:
            try:
                result = catalog_store().update_issue_metadata(
                    int(issue_metadata_match.group(1)), payload.get("title"),
                    payload.get("publicationYear"),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        alias_match = re.fullmatch(r"/api/v1/series/(\d+)/aliases", parsed_url.path)
        if alias_match:
            try:
                alias = catalog_store().add_series_alias(int(alias_match.group(1)), str(payload.get("alias") or ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(alias, 201)
            return
        if parsed_url.path == "/api/v1/families":
            try:
                family = catalog_store().create_series_family(
                    str(payload.get("name") or ""),
                    [int(value) for value in (payload.get("runIds") or [])],
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(family, 201)
            return
        collection_structure = re.fullmatch(r"/api/v1/collections/(\d+)/structure", parsed_url.path)
        if collection_structure:
            try:
                result = catalog_store().set_story_structure(
                    int(collection_structure.group(1)), payload.get("arcs") or []
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        family_assignment = re.fullmatch(r"/api/v1/series/(\d+)/family", parsed_url.path)
        if family_assignment:
            try:
                family_id = payload.get("familyId")
                result = catalog_store().set_series_run_family(
                    int(family_assignment.group(1)), int(family_id) if family_id not in {None, ""} else None,
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        issue_run_match = re.fullmatch(r"/api/v1/series/(\d+)/issue-run", parsed_url.path)
        if issue_run_match:
            try:
                result = confirm_gcd_series_run(
                    int(issue_run_match.group(1)), str(payload.get("providerSeriesId") or "")
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"Series-run confirmation failed: {exc}"}, 502)
                return
            self.send_json(result)
            return
        collection_run_match = re.fullmatch(
            r"/api/v1/series/(\d+)/collection-structure", parsed_url.path
        )
        if collection_run_match:
            try:
                result = confirm_gcd_series_collection(
                    int(collection_run_match.group(1)),
                    payload.get("providerSeriesIds") or [],
                    str(payload.get("name") or "").strip(),
                    str(payload.get("acquisitionPreference") or "either"),
                    bool(payload.get("includeSpecials", True)),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"Collection structure import failed: {exc}"}, 502)
                return
            self.send_json(result)
            return
        issue_sync_match = re.fullmatch(r"/api/v1/series/(\d+)/issues/sync", parsed_url.path)
        if issue_sync_match:
            series_id = int(issue_sync_match.group(1))
            try:
                result = sync_issue_catalog(series_id)
            except ValueError as exc:
                catalog_store().record_issue_sync_error(series_id, "provider_broker", str(exc))
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                catalog_store().record_issue_sync_error(series_id, "provider_broker", str(exc))
                self.send_json({"error": f"Metadata refresh failed: {exc}"}, 502)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/series/merge/preview":
            try:
                result = catalog_store().series_merge_preview(
                    int(payload.get("sourceId")), int(payload.get("targetId"))
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/series/merge":
            try:
                result = catalog_store().merge_series(
                    int(payload.get("sourceId")),
                    int(payload.get("targetId")),
                    bool(payload.get("allowProviderConflicts", False)),
                )
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        self.send_json({"error": "Endpoint not found"}, 404)

    def _route_patch(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        try:
            payload = self.read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        if parsed_url.path == "/api/v1/settings":
            try:
                updated = save_app_settings(payload if isinstance(payload, dict) else {})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:  # pragma: no cover - surfaced for diagnosis
                log_exception("settings_save_failed", exc)
                self.send_json({"error": f"Could not save settings: {exc}"}, 500)
                return
            self.send_json(updated)
            return
        root_match = re.fullmatch(r"/api/v1/library-roots/(\d+)", parsed_url.path)
        if root_match:
            try:
                recursive = payload.get("recursive")
                if not isinstance(recursive, bool):
                    raise ValueError("recursive must be true or false")
                root = catalog_store().update_root(
                    int(root_match.group(1)), recursive
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(root)
            return
        self.send_json({"error": "Endpoint not found"}, 404)

    def _route_delete(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        root_match = re.fullmatch(r"/api/v1/library-roots/(\d+)", parsed_url.path)
        if root_match:
            try:
                result = catalog_store().remove_root(int(root_match.group(1)))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        series_match = re.fullmatch(r"/api/v1/series/(\d+)", parsed_url.path)
        if series_match:
            try:
                result = remove_series_from_library(int(series_match.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        pulled = re.fullmatch(r"/api/v1/requests/(\d+)(?:/issues/(\d+))?", parsed_url.path)
        if pulled:
            try:
                result = catalog_store().delete_pulled_issues(
                    int(pulled.group(1)),
                    [int(pulled.group(2))] if pulled.group(2) else None,
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        self.send_json({"error": "Endpoint not found"}, 404)

    def query(self, parsed_url: urllib.parse.ParseResult) -> dict[str, str]:
        return {k: values[0] for k, values in urllib.parse.parse_qs(parsed_url.query).items()}

    def send_session_cookie(
        self, token: str, expire: bool = False, payload: Any = None
    ) -> None:
        body = json.dumps(
            payload if payload is not None
            else {"status": "signed out" if expire else "authenticated"}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        attributes = [
            f"{_SESSION_COOKIE}={token}", "Path=/", "HttpOnly",
            # Lax keeps the cookie off cross-site POSTs, which is the CSRF risk
            # a cookie session introduces.
            "SameSite=Lax",
        ]
        if expire:
            attributes.append("Max-Age=0")
        else:
            attributes.append(f"Max-Age={_SESSION_TTL_SECONDS}")
        if self._request_is_https():
            attributes.append("Secure")
        self.send_header("Set-Cookie", "; ".join(attributes))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode()
        body, encoding = _gzip_if_worthwhile(
            body, "application/json", self.headers.get("Accept-Encoding", "")
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        # Required whenever a response varies by request header, and doubly so
        # for the immutably cached assets below: without it a shared cache can
        # hand a compressed body to a client that never asked for one.
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_web_asset(self, request_path: str) -> None:
        """Serve the production React build and fall back to its app shell."""
        root = web_root()
        assert root is not None
        relative = urllib.parse.unquote(request_path).lstrip("/") or "index.html"
        requested = (root / relative).resolve()
        try:
            requested.relative_to(root)
        except ValueError:
            self.send_json({"error": "Asset not found"}, 404)
            return
        target = requested if requested.is_file() else root / "index.html"
        if not target.is_file():
            self.send_json({"error": "Web interface is not installed"}, 404)
            return
        try:
            body = target.read_bytes()
        except OSError:
            self.send_json({"error": "Web interface could not be read"}, 500)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        body, encoding = _gzip_if_worthwhile(
            body, content_type, self.headers.get("Accept-Encoding", "")
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if target.name != "index.html" else "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0 or length > 1_000_000:
            raise ValueError("Request body must be JSON and no larger than 1 MB")
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def handle_scan(self, parsed_url: urllib.parse.ParseResult) -> None:
        query = self.query(parsed_url)
        try:
            files = scan_folder(query.get("folder", ""), query.get("recursive") == "1")
            self.send_json({"count": len(files), "files": [asdict(x) for x in files]})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_enrich(self, parsed_url: urllib.parse.ParseResult) -> None:
        query = self.query(parsed_url)
        path = Path(query.get("path", ""))
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            self.send_json({"error": "Invalid or unsupported comic or ebook file"}, 400)
            return
        self.send_json(enrich(parse_filename(path)))

    def handle_batch(self, parsed_url: urllib.parse.ParseResult) -> None:
        query = self.query(parsed_url)
        try:
            results = batch_enrich(query.get("folder", ""), query.get("recursive", "1") == "1")
            self.send_json({"count": len(results), "results": results})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_file_cover(self, parsed_url: urllib.parse.ParseResult) -> None:
        query = self.query(parsed_url)
        path = Path(query.get("path", ""))
        # Whether a cover can be pulled is a question about the archive, which
        # archive_kind answers by reading it. Gating on the extension here kept
        # refusing every RAR comic after the rest of the app had learned to
        # read them: the cover was found and then could not be served.
        if not path.is_file() or archive_kind(path) is None:
            self.send_json({"error": "This comic format does not support local cover extraction yet"}, 400)
            return
        metadata = read_embedded_metadata(path)
        member = find_archive_cover_member(path, metadata)
        if not member:
            self.send_json({"error": "No image page found inside comic file"}, 404)
            return
        try:
            body = render_file_cover_thumbnail(path, member)
        except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
            self.send_json({"error": str(exc) or "Embedded cover could not be read"}, 422)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("X-Cover-Source", "comic-file")
        self.end_headers()
        self.wfile.write(body)

    def handle_uploaded_cover(self, file_id: int, kind: str = "files") -> None:
        path = user_cover_dir(kind) / f"{file_id}.jpg"
        if not path.is_file():
            self.send_json({"error": "Uploaded cover was not found"}, 404)
            return
        try:
            body = path.read_bytes()
        except OSError as exc:
            self.send_json({"error": f"Uploaded cover could not be read: {exc}"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        # A replacement uses the same stable URL, so force clients to revalidate
        # instead of showing the previous upload for several minutes.
        self.send_header("Cache-Control", "private, no-cache")
        self.end_headers()
        self.wfile.write(body)

    def handle_cover_upload(self, entity_id: int, kind: str = "files") -> None:
        """Take a raw image body for a comic file or for a series run.

        Only the existence check and the setter differ between the two; the
        type gate, the size limit, the thumbnailing and the atomic write are
        the same job either way.
        """
        try:
            if kind == "series":
                catalog_store().get_series_cover_workbench(entity_id)
            else:
                catalog_store().get_file_workbench(entity_id)
        except (LookupError, ValueError) as exc:
            self.send_json({"error": str(exc)}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"error": "Invalid Content-Length"}, 400)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        allowed_types = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "image/heic": ".heic", "image/heif": ".heic",
        }
        if content_type not in allowed_types:
            self.send_json({"error": "Upload a JPEG, PNG, WebP, GIF, or HEIC image"}, 415)
            return
        if length <= 0 or length > COVER_SOURCE_MAX_BYTES:
            self.send_json({"error": "Uploaded cover must be no larger than 50 MB"}, 413)
            return
        try:
            body = render_uploaded_cover_thumbnail(self.rfile.read(length), allowed_types[content_type])
            covers = user_cover_dir(kind)
            covers.mkdir(parents=True, exist_ok=True)
            destination = covers / f"{entity_id}.jpg"
            temporary = covers / f".{entity_id}-{threading.get_ident()}.tmp"
            temporary.write_bytes(body)
            os.replace(temporary, destination)
            result = (
                catalog_store().set_series_cover_preference(entity_id, "upload")
                if kind == "series"
                else catalog_store().set_file_cover_preference(entity_id, "upload")
            )
        except (OSError, ValueError) as exc:
            self.send_json({"error": str(exc)}, 422)
            return
        self.send_json(result, 201)

    def render_page(self, folder: str, checked: str, content: str) -> None:
        body = (
            PAGE.replace("__FOLDER__", html.escape(folder, quote=True))
            .replace("__CHECKED__", checked)
            .replace("__CONTENT__", content)
            .encode()
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_results(self, parsed_url: urllib.parse.ParseResult) -> None:
        query = self.query(parsed_url)
        folder = query.get("folder", "")
        recursive = query.get("recursive", "1") == "1"
        checked = "checked" if recursive else ""
        try:
            results = batch_enrich(folder, recursive)
            content = render_batch_results(results)
        except ValueError as exc:
            content = f"<div class='error'>{html.escape(str(exc))}</div>"
        self.render_page(folder, checked, content)

    def handle_pick_folder(self) -> None:
        if sys.platform != "darwin":
            self.send_json({"error": "The system folder picker is currently supported on macOS only."}, 501)
            return
        script = 'POSIX path of (choose folder with prompt "Choose a comics folder")'
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.send_json({"error": f"Could not open Finder: {exc}"}, 500)
            return
        if result.returncode == 0:
            self.send_json({"folder": result.stdout.strip().rstrip("/")})
            return
        if "User canceled" in result.stderr or "(-128)" in result.stderr:
            self.send_json({"folder": None, "canceled": True})
            return
        self.send_json({"error": result.stderr.strip() or "Finder folder selection failed"}, 500)

    def handle_reveal_file(self, file_path: str) -> None:
        if sys.platform != "darwin":
            self.send_json({"error": "Showing comic files is currently supported on macOS only."}, 501)
            return
        try:
            target = catalog_store().cataloged_file_path(file_path)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 404)
            return
        try:
            result = subprocess.run(
                ["open", "-R", str(target)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.send_json({"error": f"Could not open Finder: {exc}"}, 500)
            return
        if result.returncode != 0:
            self.send_json({"error": result.stderr.strip() or "Finder could not show the comic file"}, 500)
            return
        self.send_json({"status": "revealed", "message": f"Shown in Finder: {target.name}"})

    def handle_page(self, parsed_url: urllib.parse.ParseResult) -> None:
        query = self.query(parsed_url)
        folder = query.get("folder", "")
        checked = "checked" if query.get("recursive", "1") == "1" else ""
        content = "<div class='empty'>Choose a folder containing CBR, CBZ, PDF, EPUB, CB7, or CBT files.</div>"
        if folder:
            try:
                files = scan_folder(folder, checked == "checked")
                rows = []
                for item in files:
                    params = urllib.parse.urlencode({"path": item.path})
                    health = inspect_file_health(Path(item.path))
                    warnings = list(item.warnings)
                    if health.get("status") == "error":
                        warnings.insert(0, f"FILE PROBLEM: {health.get('message')}")
                    warning = "; ".join(warnings)
                    rows.append(
                        "<tr>"
                        f"<td><code>{html.escape(item.filename)}</code></td>"
                        f"<td>{html.escape(item.title)}</td>"
                        f"<td>{item.volume or ''}</td><td>{html.escape(item.issue or '')}</td>"
                        f"<td>{item.year or ''}</td><td>{html.escape(item.format or '')}</td>"
                        f"<td>{html.escape(item.isbn or '')}</td>"
                        f"<td class='warn'>{html.escape(warning)}</td>"
                        f"<td><a target='_blank' href='/api/enrich?{params}'>Lookup JSON</a></td></tr>"
                    )
                content = (
                    f"<p>Found <strong>{len(files)}</strong> supported files. No files were modified.</p>"
                    f"<div class='batch-actions'><a class='secondary' href='/results?{urllib.parse.urlencode({'folder': folder, 'recursive': '1' if checked else '0'})}'>Analyze all files</a></div>"
                    "<table><thead><tr><th>File</th><th>Parsed title</th><th>Vol.</th><th>Issue</th>"
                    "<th>Year</th><th>Format</th><th>ISBN</th><th>Warnings</th><th>Metadata</th></tr></thead>"
                    f"<tbody>{''.join(rows)}</tbody></table>"
                )
            except ValueError as exc:
                content = f"<div class='error'>{html.escape(str(exc))}</div>"
        self.render_page(folder, checked, content)

    def log_message(self, format: str, *args: Any) -> None:
        """Superseded by the structured http_request line in _dispatch."""

    def log_error(self, format: str, *args: Any) -> None:
        log_event("http_error", level="warning", detail=format % args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    load_persisted_remote_cache()
    load_persisted_provider_cache()
    try:
        recovered = catalog_store().recover_interrupted_searches()
        if recovered:
            log_event("interrupted_searches_recovered", jobs=recovered)
    except Exception as exc:
        log_exception("interrupted_search_recovery_failed", exc, level="warning")
    start_metadata_enrichment_worker()
    start_acquisition_import_worker()
    start_release_research_worker()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log_event("server_started", host=args.host, port=args.port,
              version=APP_VERSION, build=APP_BUILD)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _ENRICHMENT_STOP.set()
        _IMPORT_STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
