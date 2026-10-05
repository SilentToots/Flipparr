#!/usr/bin/env python3
"""Read-only comic filename metadata proof of concept.

Run with: python3 app.py
Then open: http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import atexit
import collections
import concurrent.futures
import contextlib
import datetime as dt
import difflib
import email.message
import gzip
import hashlib
import hmac
import html
import http.cookies
import base64
import io
import http.client
import ipaddress
import socket
import sqlite3
import json
import logging
import math
import mimetypes
import os
import platform
import posixpath
import re
import secrets
import functools
import shutil
import signal
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

from reading_list_formats import parse_reading_list, write_reading_list
import arc_catalog
import torrent_client
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Iterable

from access_policy import ADMIN, HOUSEHOLD, Viewer, allows, route_access
from catalog_store import ADMIN_USER_ID, SWITCH_LOCKS, CatalogStore, CollectionNameTaken, _issue_release_state, normalized_person
import page_panels as panel_finder
import content_rating
from comic_language import (
    LANGUAGE_NAMES,
    detect_language as detect_release_language,
    language_name,
)
from provider_evidence import (
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
# Covers are kept beside the catalog, like reading pages, so a restart does
# not wipe them: in the container's RAM /tmp every deploy meant re-reading
# every cover from its comic, 46 at once on the first library page (about a
# second each, 2026-10-05). FLIPPARR_COVER_CACHE_MB bounds it.
COVER_CACHE_MAX_BYTES = max(32, int(_env("COVER_CACHE_MB", "512") or 512)) * 1024 * 1024
# The run drawer's header is 606px wide; a page behind it is drawn at twice
# that for high-density screens.
BACKDROP_MAX_DIMENSION = 1200
# How far into an issue an automatic background looks for a double-page spread.
BACKDROP_SPREAD_SCAN_LIMIT = 40
# A page to read rather than to glance at. A phone reads a page height-bound
# (852pt at 3x is ~2556px) and 1536 looks soft; 2400 costs about twice the
# bytes for a difference only visible zoomed in. 1800 is where that curve
# bends: roughly 250-450KB a page at the quality above.
READING_PAGE_MAX_DIMENSION = 1800
# What the rendered pages of comics you have read may occupy before the oldest
# are dropped. They live beside the catalog rather than in /tmp: the container
# mounts a 256MB tmpfs there and shares it with SQLite and the cover cache, and
# one issue at reading size is ~10MB.
READING_CACHE_MAX_BYTES = max(64, int(_env("READING_CACHE_MB", "2048") or 2048)) * 1024 * 1024
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
RECENT_PROBLEMS: collections.deque[dict[str, Any]] = collections.deque(maxlen=200)
_REQUEST_CONTEXT = threading.local()
_LOG_CLOSED = False


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
        if _LOG_CLOSED:
            return
        if level in ("warning", "error"):
            # Kept for Settings -> System, so the last problems can be read
            # without the container's log, which Docker caps and rotates.
            RECENT_PROBLEMS.append(record)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


@atexit.register
def _close_log() -> None:
    """Stop background workers writing to stdout once the process is exiting.

    Workers are daemon threads, and one still logging while the interpreter
    finalizes can hold stdout's buffer lock as the runtime flushes it -- a
    fatal error that turned a passing test run into exit 134 in CI. Taking the
    log lock here waits out any line in flight; after it, nothing writes.
    """
    global _LOG_CLOSED
    with _LOG_LOCK:
        _LOG_CLOSED = True
        try:
            sys.stdout.flush()
        except (OSError, ValueError):
            pass


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


# Entries already reported as unparseable, so a typo is logged once rather
# than on every request.
_BAD_PROXY_ENTRIES_WARNED: set[str] = set()


# Each entry is an address or a network in CIDR form. A network is what makes
# this work under Docker: a bridge network hands out container addresses in
# start order, so a proxy's address changes across restarts while the
# network's subnet does not. An entry that does not parse is skipped, never
# widened, so a typo can only trust less.
def trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for entry in trusted_proxies():
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            if entry not in _BAD_PROXY_ENTRIES_WARNED:
                _BAD_PROXY_ENTRIES_WARNED.add(entry)
                log_event(
                    "invalid_trusted_proxy", level="warning", entry=entry,
                    detail="FLIPPARR_TRUSTED_PROXIES entries must be an address "
                           "(172.18.0.5) or a network (172.18.0.0/16); this one "
                           "is ignored.",
                )
    return tuple(networks)


def _parse_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    # A dual-stack socket reports an IPv4 peer as ::ffff:a.b.c.d.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_trusted_proxy(value: str) -> bool:
    address = _parse_address(value)
    if address is None:
        return False
    return any(address in network for network in trusted_proxy_networks())


def resolve_client_address(peer: str, forwarded_for: str) -> str:
    """The caller's real address, given the socket peer and X-Forwarded-For.

    Every proxy appends the address it received the request from, so the header
    reads client-supplied entries first and trusted hops last. Only the
    right-hand end can be believed: walk it from the right, skipping our own
    proxies, and the first address that is not one of them is the client. The
    left-most entry is whatever the client chose to send -- believing it would
    let anyone claim to be 127.0.0.1 through a proxy that appends (nginx's
    $proxy_add_x_forwarded_for does). This is nginx's real_ip_recursive rule.
    """
    if not is_trusted_proxy(peer):
        return peer
    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    for hop in reversed(hops):
        if _parse_address(hop) is None:
            # Garbage in the chain: nothing to its left can be vouched for. Stop
            # here, and return the unparseable hop rather than the trusted proxy
            # before it -- a proxy's own address is private, so that would make
            # a malformed header count as a local caller.
            return hop
        if not is_trusted_proxy(hop):
            return hop
        peer = hop
    return peer


# Set once, so a misconfigured proxy is reported rather than repeated on every
# request for the life of the process.
_PROXY_HEADER_WARNED = False
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
GCD_API_BASE = "https://www.comics.org/api"
METRON_API_BASE = "https://metron.cloud/api"
ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_API_VERSION = "2023-06-01"
# The vision model panel view asks, when the Claude connector is on. Haiku:
# a page is a few thousand tokens, and this is a question about boxes.
# Measured 2026-09-22 on real pages (see the Reader settings): Sonnet 5 read
# every layout tried; Haiku guesses a grid on hard ones.
VISION_MODEL = os.environ.get("FLIPPARR_VISION_MODEL") or "claude-sonnet-5"
OPENAI_API_BASE = "https://api.openai.com/v1"
# gpt-4o-mini bills a page at ~37k tokens (images at 33x) and guessed a grid
# through an eight-panel page; 4.1-mini is a tenth the tokens, says "no
# panels" when unsure, and merges rather than invents.
VISION_MODEL_OPENAI = os.environ.get("FLIPPARR_VISION_MODEL_OPENAI") or "gpt-4.1-mini"
VISION_TIMEOUT_SECONDS = 60.0
# The vision connectors, in the order a tie is broken.
VISION_PROVIDERS = ("anthropic", "openai")
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
    # Comics added outside Flipparr stay invisible until a library scan, so the
    # library is scanned in the background unless this is turned off. The
    # interval is one of AUTO_SCAN_INTERVALS, in minutes.
    "autoScanEnabled": True,
    "autoScanIntervalMinutes": 60,
    # With a vision connector on, whether it reads every page opened in panel
    # view, or only the pages the local finder could not read. The finder
    # can be wrong while sure -- a patch of sky as a panel -- and nothing
    # questions a reading that exists; a model does, and the page corrects
    # its edges. Off, the model is a fallback only. Each page is still sent
    # once. Off by default (2026-10-03): with it on, every page opened is
    # sent to the model's company, which is the operator's choice to make.
    "visionReadsEveryPage": False,
    # Whether a reader profile's page opens may ask the vision connector too.
    # Off, readers get what the local finder reads and whatever the admin's
    # reading already asked or corrected; the connector spends the admin's key.
    "visionForReaders": False,
    # Whether a run Metron has no rating for has its cover read by the vision
    # connector for the rating printed there (DC's "13+ TEEN", Marvel's
    # "RATED T+"). One cover per run, on the admin's key; off until asked.
    "ratingsFromCovers": False,
    # Where releases are taken from first (owner, 2026-09-30). Among releases
    # good enough to take on their own, the source earlier here wins even over
    # a few more points of match elsewhere; score ranks within one source, and
    # a weak match is never taken for its source. Packs keep their own rule:
    # pulling a run they lead, pulling one issue a single does.
    "sourcePriority": ["usenet", "torrent", "direct_site"],
    # Whether story-arc suggestions include the community's reading lists,
    # whose file list is fetched from GitHub once a day while this is on.
    # Off until asked (2026-10-03): it is a call to a third party the
    # operator did not configure.
    "communityListsEnabled": False,
    # Whether each request's log line names the address it came from. Off,
    # the Docker log holds no visitor addresses; sign-in throttling keeps its
    # own count in memory either way.
    "logClientAddresses": False,
}
RELEASE_SOURCES = ("usenet", "torrent", "direct_site")
_APP_SETTINGS_BOOL_KEYS = frozenset({
    "collectedEditionsEnabled", "setupCompleted", "autoScanEnabled", "visionReadsEveryPage", "visionForReaders",
    "ratingsFromCovers", "communityListsEnabled", "logClientAddresses",
})
# Every quarter hour, hour, six hours, or day.
AUTO_SCAN_INTERVALS = (15, 60, 360, 1440)


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
                elif key == "autoScanIntervalMinutes":
                    if isinstance(value, int) and not isinstance(value, bool) and value in AUTO_SCAN_INTERVALS:
                        settings[key] = value
                elif key == "sourcePriority":
                    legacy = {old: new for new, old in _LEGACY_SOURCE_NAMES.items()}
                    value = [legacy.get(item, item) for item in value] if isinstance(value, list) else value
                    if _is_source_order(value):
                        settings[key] = list(value)
                elif isinstance(value, str):
                    settings[key] = value
        settings["sourcePriority"] = list(settings["sourcePriority"])
        return settings


_SETTING_CACHE: dict[str, Any] = {"stamp": None, "settings": {}}


def cached_setting(key: str) -> Any:
    """One setting, for code that asks on every request: the file is read
    again only when it has changed."""
    path = settings_config_path()
    try:
        stamp: Any = (str(path), path.stat().st_mtime_ns)
    except OSError:
        stamp = (str(path), None)
    if _SETTING_CACHE["stamp"] != stamp:
        _SETTING_CACHE["settings"] = load_app_settings()
        _SETTING_CACHE["stamp"] = stamp
    return _SETTING_CACHE["settings"].get(key, _APP_SETTINGS_DEFAULTS.get(key))


def _is_source_order(value: Any) -> bool:
    return isinstance(value, list) and sorted(map(str, value)) == sorted(RELEASE_SOURCES) \
        and all(isinstance(item, str) for item in value)


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
        if key == "autoScanIntervalMinutes" and (
            not isinstance(value, int) or isinstance(value, bool) or value not in AUTO_SCAN_INTERVALS
        ):
            raise ValueError("Choose one of the scan intervals Flipparr offers")
        if key == "sourcePriority":
            if not _is_source_order(value):
                raise ValueError("List Usenet, torrents and direct downloads once each")
            value = list(value)
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
        _SETTING_CACHE["stamp"] = None
        return current


# Re-entrant: a change holds it across load, change and write, and load and
# write each take it on their own too.
_AUTH_CONFIG_LOCK = threading.RLock()
_AUTH_METHODS = frozenset({"none", "forms"})
_SESSION_COOKIE = "flipparr_session"
_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
# A shared device: one the admin signed in on with a password. It is what
# offers the "Who's reading?" picker there, the way a Plex Home device does.
# A reader signing in on their own phone never gets one.
_DEVICE_COOKIE = "flipparr_device"
_DEVICE_TTL_SECONDS = 365 * 24 * 60 * 60
_PIN_PATTERN = re.compile(r"\d{4,6}")
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
        # Moves on when the admin forgets every shared device: device cookies,
        # and profile sessions opened from the picker, carry the epoch they
        # were issued in and stop working when it changes.
        "householdEpoch": 0,
    }


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


# Checked against when a sign-in name belongs to nobody, so the answer costs
# the same scrypt work either way. Its password is unknowable: a random salt
# and a digest of nothing.
_DUMMY_PASSWORD_HASH = f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${'00' * 16}${'00' * 32}"


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
    with _AUTH_CONFIG_LOCK:
        return _save_auth_config(patch)


def _save_auth_config(patch: dict[str, Any]) -> dict[str, Any]:
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
        # A new password signs every device out: sessions are signed with this
        # secret, so replacing it voids them all. Changing a password because
        # it may have leaked would do little if a session taken with the old
        # one stayed good for its remaining 30 days. The caller of the settings
        # API is issued a fresh session in the same response, so only the
        # other devices have to sign in again.
        current["sessionSecret"] = secrets.token_urlsafe(32)
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
    _write_auth_config(current)
    return current


def _write_auth_config(config: dict[str, Any]) -> None:
    config_path = auth_config_path()
    with _AUTH_CONFIG_LOCK:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.parent / (config_path.name + ".tmp")
        tmp.write_text(json.dumps(config, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(config_path)


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


# ---- Reader profiles: whose request this is ----------------------------------
#
# A profile's session cookie is `v2.<id>.<version>.<expiry>.<signature>`,
# signed with the session secret. It is good while the profile exists, is not
# switched off, and still has the version it was issued at -- a new password,
# a changed role or "sign out everywhere" moves the version on, and every
# cookie that profile had stops working. The device cookie says only "the
# admin signed in here". Both are domain-separated, so neither passes for the
# other. A cookie from before profiles (`<username>:<expiry>:<signature>`) is
# the admin's, until the admin's first revocation, and is upgraded on sight.
#
# A profile opened from the picker on a shared device is `v3.`: the same, plus
# the household epoch it was opened in. Forgetting the shared devices moves
# the epoch on, which ends those devices (`d2.<epoch>.`) and every profile
# still open on them at once -- a lost tablet keeps nobody's sign-in. A
# reader's own sign-in with their password (`v2.`) is not a shared device's
# and is untouched. Device cookies from before the epoch (`d1.`) are good
# until the first time it moves.


def _signed(config: dict[str, Any], message: str) -> str:
    return hmac.new(config["sessionSecret"].encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _household_epoch(config: dict[str, Any]) -> int:
    try:
        return max(0, int(config.get("householdEpoch") or 0))
    except (TypeError, ValueError):
        return 0


def issue_profile_token(
    user: dict[str, Any], config: dict[str, Any], now: float | None = None, *, shared: bool = False,
) -> str:
    """A profile's session; `shared` when it was opened from a shared device's
    picker, so forgetting the shared devices ends it too."""
    expires = int(now if now is not None else time.time()) + _SESSION_TTL_SECONDS
    user_id, version = int(user["id"]), int(user["sessionVersion"])
    if shared:
        epoch = _household_epoch(config)
        signature = _signed(config, f"session|{user_id}|{version}|{epoch}|{expires}")
        return f"v3.{user_id}.{version}.{epoch}.{expires}.{signature}"
    return f"v2.{user_id}.{version}.{expires}.{_signed(config, f'session|{user_id}|{version}|{expires}')}"


def issue_device_token(config: dict[str, Any], now: float | None = None) -> str:
    expires = int(now if now is not None else time.time()) + _DEVICE_TTL_SECONDS
    epoch = _household_epoch(config)
    return f"d2.{epoch}.{expires}.{_signed(config, f'device|{epoch}|{expires}')}"


def device_token_valid(token: str, config: dict[str, Any], now: float | None = None) -> bool:
    parts = str(token or "").split(".")
    if not config.get("sessionSecret"):
        return False
    if len(parts) == 4 and parts[0] == "d2":
        _tag, epoch, expires, signature = parts
        if epoch != str(_household_epoch(config)) or not hmac.compare_digest(
                _signed(config, f"device|{epoch}|{expires}"), signature):
            return False
    elif len(parts) == 3 and parts[0] == "d1" and _household_epoch(config) == 0:
        _tag, expires, signature = parts
        if not hmac.compare_digest(_signed(config, f"device|{expires}"), signature):
            return False
    else:
        return False
    try:
        return int(expires) >= int(now if now is not None else time.time())
    except ValueError:
        return False


def forget_shared_devices() -> dict[str, Any]:
    """End every shared device and every profile opened from one, by moving
    the household epoch on. Returns the new sign-in config."""
    with _AUTH_CONFIG_LOCK:
        current = load_auth_config()
        current["householdEpoch"] = _household_epoch(current) + 1
        if not current["sessionSecret"]:
            current["sessionSecret"] = secrets.token_urlsafe(32)
        _write_auth_config(current)
    return current


def viewer_for(user: dict[str, Any] | None) -> Viewer | None:
    if not user or user.get("disabled"):
        return None
    return Viewer(
        id=int(user["id"]), name=str(user["name"]), role=str(user["role"]), colour=user.get("colour"),
        can_request=bool(user.get("canRequest")), auto_approve=bool(user.get("autoApprove")),
        avatar=user.get("avatar"),
        max_rating=user.get("maxRating"), allow_unrated=bool(user.get("allowUnrated")),
        can_discover=bool(user.get("canDiscover", True)),
    )


def resolve_session_token(
    token: str, config: dict[str, Any], store: "CatalogStore", now: float | None = None,
) -> tuple[Viewer | None, bool]:
    """The profile a session cookie speaks for, and whether it is a pre-profiles cookie to upgrade."""
    token = str(token or "")
    if not config.get("sessionSecret") or not token:
        return None, False
    if token.startswith(("v2.", "v3.")):
        parts = token.split(".")
        if parts[0] == "v3" and len(parts) == 6:
            _tag, user_id, version, epoch, expires, signature = parts
            if epoch != str(_household_epoch(config)) or not hmac.compare_digest(
                    _signed(config, f"session|{user_id}|{version}|{epoch}|{expires}"), signature):
                return None, False
        elif parts[0] == "v2" and len(parts) == 5:
            _tag, user_id, version, expires, signature = parts
            if not hmac.compare_digest(_signed(config, f"session|{user_id}|{version}|{expires}"), signature):
                return None, False
        else:
            return None, False
        try:
            if int(expires) < int(now if now is not None else time.time()):
                return None, False
            user = store.user(int(user_id))
        except ValueError:
            return None, False
        if user is None or str(user["sessionVersion"]) != version:
            return None, False
        return viewer_for(user), False
    # From before profiles: the admin's, while nothing has been revoked since.
    if not session_token_valid(token, config):
        return None, False
    admin = store.user(ADMIN_USER_ID)
    if admin is None or admin["sessionVersion"] != 0 or _household_epoch(config) != 0:
        return None, False
    return viewer_for(admin), True


def _has_password(user: dict[str, Any], config: dict[str, Any]) -> bool:
    """The admin's password is in the sign-in config; everyone else's in their profile."""
    return bool(config.get("passwordHash")) if user["id"] == ADMIN_USER_ID else bool(user["hasPassword"])


def profile_lock(user: dict[str, Any], config: dict[str, Any]) -> str:
    """What switching to a profile asks for: "open", "pin" or "password".

    The profile's choice, when it still has the secret it chose. An admin is
    never open while it has either: anyone can tap a profile on a shared
    device, and the admin's opens the library's settings and every reader.
    """
    available = {"pin": bool(user["hasPin"]), "password": _has_password(user, config)}
    chosen = user.get("switchLock") or "open"
    if chosen == "open" and user["role"] != "admin":
        return "open"
    if available.get(chosen):
        return chosen
    for lock in ("pin", "password"):
        if available[lock]:
            return lock
    return "open"


def admin_is_guarded(config: dict[str, Any], store: "CatalogStore") -> bool:
    """Whether the admin profile needs something only the admin knows. Readers
    are refused until it does: on a shared device anyone can tap a profile."""
    admin = store.user(ADMIN_USER_ID)
    return admin is not None and profile_lock(admin, config) != "open"


def _valid_pin(pin: Any) -> str:
    text = str(pin or "")
    if not _PIN_PATTERN.fullmatch(text):
        raise ValueError("A PIN is four to six digits")
    return text


def _login_taken_by_admin(login_name: Any, config: dict[str, Any]) -> bool:
    name = str(login_name or "").strip()
    return bool(name) and name.casefold() == str(config.get("username") or "").strip().casefold()


class Throttled(Exception):
    def __init__(self, wait: int):
        super().__init__(f"retry in {wait}s")
        self.wait = wait


# What a profile may keep about itself on the server, so it follows them to
# any device. Library views and recent searches stay in the browser.
READER_PREF_KEYS = frozenset({"panelMode", "panelScrim", "panelStartWhole", "panelReveal"})


def public_profile(user: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """A profile as the picker shows it: no sign-in name, nothing secret --
    only whether it opens with a tap, a PIN or a password."""
    lock = profile_lock(user, config)
    return {
        "id": user["id"], "name": user["name"], "colour": user["colour"], "role": user["role"],
        "avatar": user.get("avatar"), "lock": lock,
        # How many digits, so the pad draws that many dots -- what a phone's
        # lock screen shows. Unknown for a PIN set before it was recorded.
        "pinLength": user.get("pinLength") if lock == "pin" else None,
    }


def profile_record(user: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any] | None:
    """A profile as its owner and the admin see it, with what switching to it asks for."""
    return None if user is None else {**user, "lock": profile_lock(user, config)}


def profile_choices(store: "CatalogStore", config: dict[str, Any]) -> list[dict[str, Any]]:
    return [public_profile(user, config) for user in store.list_users() if not user["disabled"] and user["showOnPicker"]]


def _checked(key: str, generation: str, secret: Any, hashed: str, *, max_delay: int) -> bool:
    wait = _throttle_wait(key, generation)
    if wait:
        raise Throttled(wait)
    ok = secret is not None and verify_password(str(secret), hashed)
    _throttle_record(key, generation, ok, max_delay=max_delay)
    return ok


def check_profile_switch(store: "CatalogStore", config: dict[str, Any], user_id: int,
                         pin: Any = None, password: Any = None) -> dict[str, Any]:
    """The profile to switch to, once it has been shown what it asks for:
    nothing, its PIN or its password, as the profile chose (`profile_lock`).
    """
    user = store.user(user_id)
    if user is None or user["disabled"]:
        raise LookupError("That profile is not available")
    lock = profile_lock(user, config)
    secrets_ = store.user_secrets(user_id)
    if lock == "pin":
        if not _checked(f"pin:{user_id}", secrets_["pinHash"], pin, secrets_["pinHash"], max_delay=_PIN_MAX_DELAY_SECONDS):
            raise PermissionError("That PIN is not right")
        if not user.get("pinLength"):
            # A PIN from before lengths were kept, now known.
            user = store.update_user(user_id, pinLength=len(str(pin)))
    elif lock == "password":
        hashed = config.get("passwordHash") if user_id == ADMIN_USER_ID else secrets_["passwordHash"]
        key = "account" if user_id == ADMIN_USER_ID else f"user:{user_id}"
        if not _checked(key, hashed, password, hashed, max_delay=_LOGIN_MAX_DELAY_SECONDS):
            raise PermissionError("That password is not right")
    return user


def login_subject(username: str, config: dict[str, Any], store: "CatalogStore") -> tuple[str, str, dict[str, Any] | None]:
    """Who a sign-in name belongs to: its throttle key, the hash to check, and the profile.

    The admin's password stays in the sign-in config (where the
    reset-password command finds it); a reader's is in their profile.
    """
    configured = str(config.get("username") or "")
    if configured and hmac.compare_digest(username, configured):
        return "account", str(config.get("passwordHash") or ""), store.user(ADMIN_USER_ID)
    user = store.user_by_login(username) if username else None
    if user and not user["disabled"]:
        hashed = store.user_secrets(user["id"])["passwordHash"]
        if hashed:
            return f"user:{user['id']}", hashed, user
    return "other", "", None


def _profile_changes(store: "CatalogStore", config: dict[str, Any], target: dict[str, Any],
                     payload: dict[str, Any], *, by_admin: bool) -> dict[str, Any]:
    """The store fields a profile edit asks for, validated and hashed."""
    changes: dict[str, Any] = {}
    for key in ("name", "colour"):
        if key in payload:
            changes[key] = payload[key]
    if "pin" in payload:
        changes["pinHash"] = None if payload["pin"] in (None, "") else hash_password(_valid_pin(payload["pin"]))
        changes["pinLength"] = None if payload["pin"] in (None, "") else len(str(payload["pin"]))
    is_admin_profile = target["id"] == ADMIN_USER_ID
    if "loginName" in payload or "password" in payload:
        if is_admin_profile:
            raise ValueError("Your sign-in name and password are in Settings, Security")
        if "loginName" in payload:
            if _login_taken_by_admin(payload["loginName"], config):
                raise ValueError("Another profile already signs in with that name")
            changes["loginName"] = payload["loginName"]
        if "password" in payload:
            password = payload["password"]
            if password in (None, ""):
                changes["passwordHash"] = None
            else:
                if not isinstance(password, str) or len(password) < 8:
                    raise ValueError("Password must be at least 8 characters")
                if not by_admin and target["hasPassword"]:
                    current = store.user_secrets(target["id"])["passwordHash"]
                    if not verify_password(str(payload.get("currentPassword") or ""), current or ""):
                        raise PermissionError("Your current password is not right")
                changes["passwordHash"] = hash_password(password)
    if by_admin:
        for key in ("role", "canRequest", "autoApprove", "showOnPicker", "disabled"):
            if key in payload:
                changes[key] = payload[key]
        # What the profile may read. The admin reads everything. A limit set
        # for the first time keeps the profile out of Discover too, unless
        # said otherwise: its shelves and results carry no rating to filter by.
        if any(key in payload for key in ("maxRating", "allowUnrated", "canDiscover")):
            if changes.get("role", target["role"]) == "admin":
                raise ValueError("The admin reads everything")
            for key in ("maxRating", "allowUnrated", "canDiscover"):
                if key in payload:
                    changes[key] = payload[key] or None if key == "maxRating" else bool(payload[key])
            if changes.get("maxRating") and not target.get("maxRating") and "canDiscover" not in payload:
                changes["canDiscover"] = False
    # What switching to the profile asks for, checked against the secrets it
    # will have after this edit.
    after = {
        **target, "role": changes.get("role", target["role"]),
        "hasPin": bool(changes["pinHash"]) if "pinHash" in changes else target["hasPin"],
        "hasPassword": bool(changes["passwordHash"]) if "passwordHash" in changes else target["hasPassword"],
    }
    if "switchLock" in payload:
        lock = payload["switchLock"]
        if lock not in SWITCH_LOCKS:
            raise ValueError("A profile opens with a tap, a PIN or a password")
        if lock == "open" and after["role"] == "admin":
            raise ValueError("The admin opens with a PIN or a password")
        if lock == "pin" and not after["hasPin"]:
            raise ValueError("Choose a PIN to open with")
        if lock == "password" and not _has_password(after, config):
            raise ValueError("Set your password in Security first" if is_admin_profile else "Choose a password to open with")
        changes["switchLock"] = lock
    elif "pinHash" in changes and changes["pinHash"] and not target["hasPin"] and target["switchLock"] == "open":
        # A first PIN, with nothing said about the lock, is a PIN to ask for.
        changes["switchLock"] = "pin"
    after["switchLock"] = changes.get("switchLock", target["switchLock"])
    # A reader whose chosen secret goes asks for the other one it has, or
    # nothing, rather than keep a choice it can no longer be asked for.
    lock_now = profile_lock(after, config)
    if after["role"] != "admin" and lock_now != after["switchLock"]:
        changes["switchLock"] = lock_now
    # An admin with neither a PIN nor a password is anyone's, once readers exist.
    if after["role"] == "admin" and lock_now == "open" and store.reader_count() and (
        target["role"] != "admin" or profile_lock(target, config) != "open"
    ):
        raise ValueError("While there are readers, an admin keeps a PIN or a password")
    return changes


def create_profile(store: "CatalogStore", config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """A profile made by the admin: a reader unless said otherwise."""
    if not admin_is_guarded(config, store):
        raise ValueError("Give your own profile a PIN, or a password in Security, before adding readers")
    login = payload.get("loginName")
    if _login_taken_by_admin(login, config):
        raise ValueError("Another profile already signs in with that name")
    password = payload.get("password")
    password_hash = None
    if password not in (None, ""):
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        password_hash = hash_password(password)
    pin = payload.get("pin")
    lock = payload.get("switchLock") or ("pin" if pin not in (None, "") else "open")
    if lock not in SWITCH_LOCKS:
        raise ValueError("A profile opens with a tap, a PIN or a password")
    if lock == "pin" and pin in (None, ""):
        raise ValueError("Choose a PIN to open with")
    if lock == "password" and not password_hash:
        raise ValueError("Choose a password to open with")
    return store.create_user(
        payload.get("name"), role=str(payload.get("role") or "reader"), login_name=login,
        password_hash=password_hash, pin_hash=None if pin in (None, "") else hash_password(_valid_pin(pin)),
        pin_length=None if pin in (None, "") else len(str(pin)),
        colour=payload.get("colour"), can_request=bool(payload.get("canRequest", True)),
        auto_approve=bool(payload.get("autoApprove", False)), show_on_picker=bool(payload.get("showOnPicker", True)),
        switch_lock=lock,
    )


def own_prefs_patch(store: "CatalogStore", user_id: int, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not set(payload) <= READER_PREF_KEYS:
        raise ValueError("Unknown reader settings")
    if not all(isinstance(value, bool) for value in payload.values()):
        raise ValueError("Reader settings are on or off")
    prefs = {key: value for key, value in store.user_prefs(user_id).items() if key in READER_PREF_KEYS}
    return store.set_user_prefs(user_id, {**prefs, **payload})


# ---- Profile pictures ---------------------------------------------------------
#
# A profile's picture is a square JPEG the app writes beside the catalog, from
# a photo someone uploads or from a page of a comic in the library -- a cover,
# usually, which is the comic reader's version of a Plex profile photo. The
# row records where it came from; the file is what is served.

AVATAR_SIDE = 320


def profile_avatar_dir() -> Path:
    return catalog_database_path().parent / "avatars"


def profile_avatar_path(user_id: int) -> Path:
    return profile_avatar_dir() / f"{int(user_id)}.jpg"


def square_avatar(image_bytes: bytes, *, top_bias: float = 0.5) -> bytes:
    """The largest square in an image, `top_bias` of the way down any slack, at AVATAR_SIDE.

    A cover's faces are usually in its upper half and its title across the
    top, so a library page is cut a third of the way down; a photo, centred.
    """
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = int((height - side) * min(1.0, max(0.0, top_bias)))
    square = image.crop((left, top, left + side, top + side)).resize((AVATAR_SIDE, AVATAR_SIDE), Image.LANCZOS)
    buffer = io.BytesIO()
    square.save(buffer, format="JPEG", quality=86)
    return buffer.getvalue()


def save_profile_avatar(user_id: int, jpeg: bytes, source: str) -> dict[str, Any]:
    folder = profile_avatar_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{int(user_id)}-{threading.get_ident()}.tmp"
    temporary.write_bytes(jpeg)
    os.replace(temporary, profile_avatar_path(user_id))
    return _profiles_store().set_user_avatar(user_id, source)


def profile_avatar_from_upload(user_id: int, raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > COVER_SOURCE_MAX_BYTES:
        raise ValueError("A picture must be between 1 byte and 50 MB")
    return save_profile_avatar(user_id, square_avatar(normalize_image_to_jpeg(raw)), "upload")


def profile_avatar_from_library(user_id: int, file_id: int, page: int = 0) -> dict[str, Any]:
    """A page of a comic in the library -- its cover by default -- as a profile's picture."""
    image = render_file_page(int(file_id), int(page))
    return save_profile_avatar(user_id, square_avatar(image, top_bias=0.3), f"library:{int(file_id)}:{int(page)}")


def clear_profile_avatar(user_id: int) -> dict[str, Any]:
    profile_avatar_path(user_id).unlink(missing_ok=True)
    return _profiles_store().set_user_avatar(user_id, None)


def reader_catalog(payload: dict[str, Any], viewer: "Viewer | None" = None) -> dict[str, Any]:
    """The catalog as a reader sees it: the library, without the admin's workbench,
    and -- for a profile with a rating limit -- without the runs above it.

    The keys stay, emptied, so the client reads one shape for everyone.
    """
    if viewer is not None and viewer.max_rating:
        payload["series"] = [item for item in payload.get("series") or []
                             if viewer_may_see_run(viewer, item.get("ageRating"))]
        visible = {str(item["id"]) for item in payload["series"]}
        # A file the catalog has not tied to a run is unrated, and follows
        # the profile's unrated setting like a run with no rating would.
        unrated_ok = viewer_may_see_run(viewer, None)
        payload["files"] = [
            item for item in payload.get("files") or []
            if ((item.get("canonicalSeriesId") in visible) if item.get("canonicalSeriesId") else unrated_ok)
        ]
        families = []
        for family in payload.get("families") or []:
            runs = [run for run in family.get("runs") or [] if str(run.get("id")) in visible]
            if not runs:
                continue
            # Everything the family says about itself is re-said from the
            # runs left: its cover can be a hidden run's, its arcs carry
            # whole run records, and its counts would give the rest away.
            arcs = []
            for arc in family.get("storyArcs") or []:
                arc_runs = [run for run in arc.get("runs") or [] if str(run.get("id")) in visible]
                if arc_runs:
                    arcs.append({**arc, "runs": arc_runs, "runIds": [run["id"] for run in arc_runs]})
            covers = list(dict.fromkeys(
                cover for run in runs for cover in (run.get("coverCandidates") or []) if cover))
            families.append({
                **family, "runs": runs, "runIds": [run["id"] for run in runs], "runCount": len(runs),
                "owned": sum(int(run.get("owned") or 0) for run in runs),
                "total": sum(int(run.get("total") or 0) for run in runs),
                "releaseSummary": {
                    key: sum(int((run.get("releaseSummary") or {}).get(key) or 0) for run in runs)
                    for key in ("releasedMissing", "upcoming", "unknown", "owned")
                },
                "cover": covers[0] if covers else None, "coverCandidates": covers,
                "storyArcs": arcs,
                "mainArcCount": sum(1 for arc in arcs if arc.get("type") == "main"),
                "specialGroupCount": sum(1 for arc in arcs if arc.get("type") == "specials"),
            })
        payload["families"] = families
        # A collection is re-said from the runs this profile may see: a hidden
        # run's id, or a cover that is one, would give it away, and a
        # collection with nothing left is not there at all.
        collections = []
        for collection in payload.get("runCollections") or []:
            run_ids = [run_id for run_id in collection.get("runIds") or [] if run_id in visible]
            if not run_ids:
                continue
            cover = collection.get("coverSeriesId")
            collections.append({**collection, "runIds": run_ids, "coverSeriesId": cover if cover in visible else None})
        payload["runCollections"] = collections
        stats = payload.setdefault("stats", {})
        stats["series"] = len(payload["series"])
        stats["families"] = len(families)
        stats["files"] = sum(len(item.get("fileDetails") or []) for item in payload["series"])
    for key in ("inbox", "roots", "requests", "replacementRequests"):
        payload[key] = []
    payload["activeScan"] = None
    payload["lastScan"] = None
    payload["enrichment"] = {}
    # The admin's queue in numbers is the admin's too.
    stats = payload.get("stats") or {}
    for key in ("needAttention", "damaged", "openRequests", "wantedIssues", "queuedJobs",
                "upcomingIssues", "unknownReleaseIssues", "pendingRequests"):
        if key in stats:
            stats[key] = 0
    return payload


# ---- Readers' requests ----------------------------------------------------------
#
# A reader asks; the admin decides. A pending request is only a row -- nothing
# is imported, followed, searched or downloaded -- and approving it makes the
# very call the admin's own button makes (following a run, adding one from
# Discover, pulling issues), so the acquisition pipeline has one way in.
# Seerr's model, sized for a household.

class AlreadyHandled(Exception):
    """What was asked for is already on its way: nothing to request."""


def _request_text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _cover_url(value: Any) -> str | None:
    """A Discover cover, only as an https address on a provider's own image
    host -- a reader's request is shown to the admin, and any other address
    would let a reader's browser choose what the admin's fetches."""
    text = str(value or "").strip()[:500]
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    return text if parts.scheme == "https" and (parts.hostname or "") in ART_SWATCH_HOSTS else None


def member_request_spec(payload: Any, catalog: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    """What a reader is asking for, checked: kind, target key, title, the
    arguments approval will use, and what the queue shows."""
    if not isinstance(payload, dict):
        raise ValueError("Say what to request")
    kind = str(payload.get("kind") or "")
    if kind == "run":
        run_id = str(payload.get("seriesId") or "")
        series = next((item for item in catalog.get("series") or [] if str(item.get("id")) == run_id), None)
        if series is None:
            raise LookupError("That run is not in the library")
        if series.get("monitoringStatus") == "monitored":
            raise AlreadyHandled(f"{series.get('title')} is already followed")
        return kind, f"run:{run_id}", str(series.get("title") or "Untitled run"), {"seriesId": int(run_id)}, {
            "year": series.get("year"), "publisher": series.get("publisher"),
            "cover": series.get("cover"),
        }
    if kind == "collection":
        family_id = str(payload.get("collectionId") or "")
        family = next((item for item in catalog.get("families") or [] if str(item.get("id")) == family_id), None)
        if family is None:
            raise LookupError("That collection is not in the library")
        if family.get("monitoringStatus") == "monitored":
            raise AlreadyHandled(f"{family.get('name')} is already followed")
        return kind, f"collection:{family_id}", str(family.get("name") or "Untitled collection"), {
            "collectionId": int(family_id)}, {"publisher": family.get("publisher")}
    if kind == "discover_arc":
        arc_id = str(payload.get("arcId") or "").strip()
        if str(payload.get("provider") or "metron") != "metron" or not re.fullmatch(r"\d+", arc_id):
            raise ValueError("Choose a story arc from Discover")
        title = _request_text(payload.get("title")) or "Untitled arc"
        detail = {"cover": _cover_url(payload.get("cover")), "provider": "metron",
                  "issueCount": payload.get("issueCount") if isinstance(payload.get("issueCount"), int) else None}
        return kind, f"discover:arc:metron:{arc_id}", title, {"provider": "metron", "arcId": arc_id}, detail
    if kind in ("discover_run", "discover_issues"):
        provider = str(payload.get("provider") or "").strip().lower()
        provider_series_id = str(payload.get("providerSeriesId") or "").strip()
        if provider not in ("metron", "comic_vine", "gcd") or not re.fullmatch(r"\d+", provider_series_id):
            raise ValueError("Choose a run from Discover")
        title = _request_text(payload.get("title")) or "Untitled run"
        detail = {"year": payload.get("year") if isinstance(payload.get("year"), int) else None,
                  "publisher": _request_text(payload.get("publisher"), 80) or None, "cover": _cover_url(payload.get("cover")),
                  "provider": provider}
        params: dict[str, Any] = {"provider": provider, "providerSeriesId": provider_series_id,
                                  "query": _request_text(payload.get("query")) or title}
        if kind == "discover_run":
            return kind, f"discover:{provider}:{provider_series_id}", title, params, detail
        released = payload.get("released") is True
        numbers = [] if released else list(dict.fromkeys(
            _request_text(number, 12) for number in (payload.get("numbers") or []) if _request_text(number, 12)
        ))[:200]
        if not released and not numbers:
            raise ValueError("Choose at least one issue")
        params.update({"numbers": numbers, "released": released})
        # The one issue asked for, when the shelf or drawer knew its id: what
        # its rating and synopsis are looked up by.
        issue_id = str(payload.get("providerIssueId") or "").strip()
        if len(numbers) == 1 and re.fullmatch(r"\d+", issue_id):
            params["providerIssueId"] = issue_id
        detail["numbers"] = numbers
        key = "released" if released else ",".join(sorted(numbers))
        return kind, f"discover:{provider}:{provider_series_id}#{key}", title, params, detail
    raise ValueError("Unknown kind of request")


def _carry_out_member_request(request: dict[str, Any]) -> tuple[int | None, int | None]:
    """Approval: the admin's own call for what was asked. Returns the
    acquisition request and run it became."""
    params = request["params"]
    kind = request["kind"]
    if kind == "run":
        made = catalog_store().create_acquisition_request("series", int(params["seriesId"]), None, True)
        _start_automatic_release_grabs(made)
        return int(made["id"]), int(params["seriesId"])
    if kind == "collection":
        made = catalog_store().create_acquisition_request("collection", int(params["collectionId"]), None, None)
        _start_automatic_release_grabs(made)
        return int(made["id"]), None
    if kind == "discover_arc":
        # The arc is saved to read in order first -- that is what was asked
        # for -- then what it is missing is pulled; nothing missing is fine.
        requester = int((request.get("requestedBy") or {}).get("id") or ADMIN_USER_ID)
        saved = save_story_arc(params["arcId"], user_id=requester, created_by=requester)
        result = pull_story_arc(params["arcId"], allow_nothing=True)
        first = result["pulled"][0] if result["pulled"] else {}
        made, run = first.get("request") or {}, first.get("series") or {}
        # One arc, several series, several pulls: the request's own column
        # holds the first, and the rest ride in its detail, so the reader
        # hears of every series' issues arriving.
        pulled_ids = [int(item["request"]["id"]) for item in result["pulled"]
                      if isinstance(item.get("request"), dict) and str(item["request"].get("id") or "").isdigit()]
        facts: dict[str, Any] = {"readingListId": saved["id"]}
        if len(pulled_ids) > 1:
            facts["acquisitionRequestIds"] = pulled_ids
        catalog_store().add_member_request_detail(request["id"], facts)
        return (int(made["id"]) if made.get("id") else None), (int(run["id"]) if run.get("id") else None)
    if kind == "discover_run":
        result = request_discovered_series(params["provider"], params["query"], params["providerSeriesId"], "either")
    else:
        result = pull_discovered_issues(
            params["provider"], params["providerSeriesId"], params.get("numbers") or None,
            released=bool(params.get("released")), query=params["query"],
        )
    made = result.get("request") or {}
    run = result.get("series") or {}
    return (int(made["id"]) if made.get("id") else None), (int(run["id"]) if run.get("id") else None)


def approve_member_request(request_id: int, decided_by: int) -> dict[str, Any]:
    """Approve and carry out. Claimed first, so a second tap cannot run it
    twice; a failure is recorded on the request, which can be approved again."""
    store = catalog_store()
    request = store.claim_member_request(request_id, decided_by, "approved")
    try:
        acquisition_request_id, run_id = _carry_out_member_request(request)
    except Exception as exc:  # noqa: BLE001 -- recorded for the admin, who can retry
        log_event("member_request_failed", level="warning", request_id=request_id, error=str(exc))
        return store.record_member_request_outcome(request_id, failure=str(exc) or type(exc).__name__)
    log_event("member_request_approved", level="info", request_id=request_id, kind=request["kind"],
              requested_by=request["requestedBy"]["id"], decided_by=decided_by)
    outcome = store.record_member_request_outcome(
        request_id, acquisition_request_id=acquisition_request_id, series_run_id=run_id)
    notify_request_decided(outcome)
    return outcome


# ---- Notifications ---------------------------------------------------------------
#
# The bell has two halves. What needs someone -- a download that failed, a
# source that rejected its login, series needing a match, requests waiting --
# is worked out from the library as it stands (the client's
# `notifications.js`), so it goes away when it is dealt with. What happened --
# comics arriving, a request decided -- is news, kept here per profile
# (`notifications`, schema 56) so it is the same on every device that profile
# signs in on, and can be read and cleared.
#
# Arrivals are swept from the downloads' own import times rather than raised
# at each place a comic can be imported (a single issue, a pack's members, a
# manual import, a retry): every path writes `imported_at`, so reading those
# past a high-water mark misses none. The sweep runs when a bell is read. It
# stops a few seconds short of now, so an import whose transaction commits a
# moment after a later one's is not stepped over.

_NOTIFICATION_SWEEP_LOCK = threading.Lock()
_NOTIFICATION_SWEEP_LAG = dt.timedelta(seconds=5)


NOTIFICATION_MARK_MAX_LAG_SECONDS = 3600


def _iso_age_seconds(earlier: str, later: str) -> float:
    try:
        return (dt.datetime.fromisoformat(later) - dt.datetime.fromisoformat(earlier)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def sweep_arrivals(now: dt.datetime | None = None) -> int:
    """Turn comics imported since the last sweep into notifications: the
    admins hear of every one, a reader of those carrying out their request.
    Several issues of one run merge into one line while it is unread.
    Returns how many issues were announced."""
    store = catalog_store()
    until = ((now or dt.datetime.now(dt.timezone.utc)) - _NOTIFICATION_SWEEP_LAG).isoformat()
    with _NOTIFICATION_SWEEP_LOCK:
        after = store.notification_mark("arrivals")
        if after is None:
            # A library from before notifications were kept starts from now,
            # rather than announcing every comic it ever imported.
            store.set_notification_mark("arrivals", until)
            return 0
        if until <= after:
            return 0
        arrivals = store.arrivals_between(after, until)
        if not arrivals and _iso_age_seconds(after, until) < NOTIFICATION_MARK_MAX_LAG_SECONDS:
            # Nothing new: leave the mark where it is. The bell is asked on
            # every page load, and writing the mark each time made the
            # database look changed to the catalog cache, which rebuilt the
            # 14 MB catalog for nothing (2026-10-05). Searching from an older
            # mark finds the same nothing; after an hour it is moved anyway,
            # so the search stays small.
            return 0
        admins = [user["id"] for user in store.list_users() if user["role"] == "admin" and not user["disabled"]]
        # A reader who asked for the run is told whatever its rating is now:
        # a run just added is usually unrated for a while, and the bell hides
        # the line until the rating allows it (`notifications_for`) rather
        # than the sweep leaving them out for good.
        readers = store.readers_waiting_on([arrival["requestId"] for arrival in arrivals])
        for arrival in arrivals:
            item = {"key": str(arrival["issueId"]), "issueId": arrival["issueId"], "number": arrival["number"],
                    "title": arrival["issueTitle"], "fileId": arrival["fileId"]}
            payload = {"runId": arrival["runId"], "runTitle": arrival["runTitle"],
                       "startYear": arrival["startYear"], "requestId": arrival["requestId"], "items": [item]}
            audience = set(admins) | readers.get(arrival["requestId"], set())
            for user_id in sorted(audience):
                store.record_notification(user_id, "arrived", f"arrived:run:{arrival['runId']}", payload,
                                          at=arrival["at"], merge=True)
        store.set_notification_mark("arrivals", until)
    if arrivals:
        # Every import path lands here, packs included: a comic that arrived
        # may be one a saved story arc was waiting for.
        try:
            heal_reading_lists()
        except Exception as exc:  # noqa: BLE001 -- the bell is told either way
            log_event("reading_list_heal_failed", level="warning", error=str(exc)[:200])
    return len(arrivals)


def notify_request_decided(request: dict[str, Any]) -> None:
    """Tell a reader their request was approved or declined -- not when they
    approved it themselves (auto-approve), which they saw happen."""
    requester = int((request.get("requestedBy") or {}).get("id") or 0)
    if request.get("status") not in ("approved", "declined") or not requester \
            or request.get("decidedBy") == requester:
        return
    catalog_store().record_notification(requester, "request_decided", f"request:{request['id']}", {
        "requestId": request["id"], "kind": request.get("kind"), "title": request.get("title"),
        "status": request["status"], "reason": request.get("declineReason"),
        "cover": (request.get("detail") or {}).get("cover"),
    }, at=request.get("decidedAt"))


def _issue_numbers(numbers: list[str]) -> str:
    """"#3", "#3 and #4", "#3, #4 and 2 more"."""
    shown = [f"#{number}" for number in numbers[:3]]
    more = len(numbers) - len(shown)
    if more > 0:
        return f"{', '.join(shown)} and {more} more"
    return shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"


def notification_view(row: dict[str, Any], viewer: Viewer) -> dict[str, Any] | None:
    """One bell line as the client shows it, or None for one this profile may
    no longer see (a run now above its rating limit)."""
    payload = row["payload"]
    base = {"id": row["id"], "at": row["updatedAt"], "read": bool(row["readAt"]), "kind": row["kind"]}
    if row["kind"] == "arrived":
        items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        if not items:
            return None
        run = str(payload.get("runTitle") or "A run")
        latest = items[-1]
        if len(items) == 1:
            title = f"{run} #{latest.get('number')}"
            detail = str(latest.get("title") or "") or "Added to your library"
        else:
            title = f"{run}: {len(items)} new issues"
            detail = _issue_numbers(sorted((str(item.get("number")) for item in items), key=_issue_sort_key))
        # The comic's own first page, as the profile's history shows it:
        # `/cover/image` serves only a cover someone uploaded for the file
        # and answered 404 for every ordinary comic, so the bell showed a
        # broken picture on each line.
        return {**base, "title": title, "detail": detail,
                "runId": payload.get("runId"),
                "cover": f"/api/v1/files/{int(latest['fileId'])}/pages/0" if latest.get("fileId") else None,
                "target": {"view": "series", "seriesId": str(payload.get("runId"))}}
    if row["kind"] == "request_decided":
        approved = payload.get("status") == "approved"
        follow = payload.get("kind") in ("run", "collection", "discover_run")
        title = str(payload.get("title") or "Your request")
        return {**base,
                "title": f"{title} was approved" if approved else f"{title} was declined",
                "detail": ("You'll get its new issues as they come out" if follow else "It will arrive once it's downloaded")
                if approved else (str(payload.get("reason") or "") or "No reason given"),
                "tone": "done" if approved else "warning",
                "cover": payload.get("cover"),
                "target": {"view": "requests", "focus": {"requestId": payload.get("requestId")}}}
    if row["kind"] == "service_trouble":
        if not viewer.is_admin:
            return None
        service = str(payload.get("service"))
        name = SERVICE_NAMES.get(service, "A download service")
        folder = service.endswith("_folder")
        return {**base,
                "title": f"Flipparr can't see {name[0].lower() + name[1:]}" if folder else f"{name} isn't answering",
                "detail": ("Finished downloads wait there until it is mounted where Flipparr reads it."
                           if folder else "Searches wait for it; none is given up or counted against an issue meanwhile."
                           if service == "prowlarr" else "Downloads in it wait for it; none is given up meanwhile."),
                "tone": "warning",
                "target": {"view": "settings", "section": "acquisition"}}
    if row["kind"] == "connector_trouble":
        if not viewer.is_admin:
            return None
        name = CONNECTOR_NAMES.get(str(payload.get("provider")), "The vision model")
        out = payload.get("reason") == "out_of_credit"
        return {**base,
                "title": f"{name} is out of credit" if out else f"{name}'s API key was refused",
                "detail": ("Panel view and cover ratings are using Flipparr's own reading until you add credit to that account."
                           if out else "Panel view and cover ratings are using Flipparr's own reading until you enter a working key."),
                "tone": "warning",
                "target": {"view": "settings", "section": "reader"}}
    return None


def notifications_for(viewer: Viewer) -> dict[str, Any]:
    """A profile's news, newest first, and what it has dismissed of the rest."""
    try:
        sweep_arrivals()
    except Exception as exc:  # noqa: BLE001 -- the bell still shows what it has
        log_event("notification_sweep_failed", level="warning", error=str(exc)[:200])
    store = catalog_store()
    rows = store.notifications_for(viewer.id)
    ratings = store.run_age_ratings() if viewer.max_rating else {}
    items = []
    for row in rows:
        view = notification_view(row, viewer)
        if view is None:
            continue
        if view.get("runId") is not None and not viewer_may_see_run(viewer, ratings.get(int(view["runId"]))):
            continue
        items.append(view)
    return {"items": items, "unread": sum(not item["read"] for item in items),
            "dismissed": store.notification_dismissals(viewer.id)}


# ---- What a request is ---------------------------------------------------------
#
# The admin decides better knowing what was asked for: Metron's age rating
# (Everyone, Teen, Teen Plus, Mature -- about half of issues have one), the
# run's genres and what it is about. Looked up after the request is made, off
# the request thread, through the same paced and cached provider queue as
# everything else; a request is never held up or refused for want of it.

_UNRATED = {"", "unknown"}


def _metron_issue_for(series_id: str, number: str | None, credential: str) -> dict[str, Any] | None:
    """The issue whose rating speaks for a request: the one asked for, or the run's first."""
    query = {"series_id": series_id, **({"number": number} if number else {})}
    listing = fetch_provider_json("metron", f"{METRON_API_BASE}/issue/?" + urllib.parse.urlencode(query), credential)
    results = [row for row in (listing or {}).get("results") or [] if isinstance(row, dict) and row.get("id")]
    if not results:
        return None
    first = results[0] if number else min(results, key=lambda row: str(row.get("cover_date") or "9999"))
    return fetch_provider_json("metron", f"{METRON_API_BASE}/issue/{int(first['id'])}/", credential)


def member_request_facts(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Rating, genres and synopsis for a request, from Metron, or nothing."""
    if kind == "discover_arc":
        credential = _provider_credential("metron")
        arc = fetch_provider_json("metron", f"{METRON_API_BASE}/arc/{params['arcId']}/", credential) or {}
        rows = _arc_issue_rows(str(params["arcId"]), credential)
        if not rows:
            return {"genres": [], "synopsis": _synopsis_text(arc.get("desc")), "rating": None, "ratingFrom": None}
        first = fetch_provider_json("metron", f"{METRON_API_BASE}/issue/{int(rows[0]['id'])}/", credential) or {}
        series_id = str((rows[0].get("series") or {}).get("id") or "")
        series = fetch_provider_json("metron", f"{METRON_API_BASE}/series/{series_id}/", credential) or {} if series_id else {}
        rating = str((first.get("rating") or {}).get("name") or "").strip()
        return {
            "genres": [str(genre.get("name")) for genre in series.get("genres") or [] if isinstance(genre, dict) and genre.get("name")][:6],
            "synopsis": _synopsis_text(arc.get("desc")) or _synopsis_text(series.get("desc")),
            "rating": None if rating.casefold() in _UNRATED else rating,
            "ratingFrom": None if rating.casefold() in _UNRATED else f"Issue {first.get('number') or 1}",
            "issueCount": len(rows),
        }
    if kind in ("discover_run", "discover_issues"):
        if params.get("provider") != "metron":
            return {}
        series_id = str(params.get("providerSeriesId") or "")
    elif kind == "run":
        series_id = str(catalog_store().confirmed_series_provider_ids(int(params["seriesId"])).get("metron") or "")
    else:
        return {}
    if not re.fullmatch(r"\d+", series_id):
        return {}
    credential = _provider_credential("metron")
    series = fetch_provider_json("metron", f"{METRON_API_BASE}/series/{series_id}/", credential) or {}
    facts: dict[str, Any] = {
        "genres": [str(genre.get("name")) for genre in series.get("genres") or []
                   if isinstance(genre, dict) and genre.get("name")][:6],
        "synopsis": _synopsis_text(series.get("desc")),
    }
    numbers = params.get("numbers") or []
    one = str(numbers[0]) if kind == "discover_issues" and len(numbers) == 1 else None
    issue_id = str(params.get("providerIssueId") or "")
    issue = (fetch_provider_json("metron", f"{METRON_API_BASE}/issue/{issue_id}/", credential)
             if re.fullmatch(r"\d+", issue_id) else _metron_issue_for(series_id, one, credential)) or {}
    rating = str((issue.get("rating") or {}).get("name") or "").strip()
    facts["rating"] = None if rating.casefold() in _UNRATED else rating
    # Which issue the rating is from, when it stands in for a whole run.
    facts["ratingFrom"] = None if one or not facts["rating"] else f"Issue {issue.get('number') or 1}"
    if one:
        facts["synopsis"] = _synopsis_text(issue.get("desc")) or facts["synopsis"]
    return facts


def _learn_about_member_request(request_id: int, kind: str, params: dict[str, Any]) -> None:
    try:
        facts = member_request_facts(kind, params)
    except Exception as exc:  # noqa: BLE001 -- a request is still a request without its facts
        log_event("member_request_facts_failed", level="info", request_id=request_id, error=str(exc))
        return
    if facts:
        catalog_store().add_member_request_detail(request_id, facts)


# ---- Age ratings ----------------------------------------------------------------
#
# Every run's rating is looked for in the background: Metron's rating of its
# first and latest issues (the stricter), then -- when the admin has turned it
# on -- the rating printed on its newest cover, read by the vision connector.
# The admin's own mark outranks both and stops the looking. What is found, or
# that nothing was, is kept; a run with none is looked at again after a month,
# or at once when cover reading is switched on. A failed call (Metron cooling
# down, the connector out of credit) records nothing, so the run is tried again.

RATING_POLL_SECONDS = 15 * 60
RATING_STALE_DAYS = 30
RATING_BATCH = 10
RATING_COVER_LONG_EDGE = 1200
_RATINGS_STOP = threading.Event()
_RATINGS_WAKE = threading.Event()
_RATINGS_THREAD: threading.Thread | None = None


def _metron_run_readings(series_id: str, credential: str) -> list[tuple[str, str]]:
    """Metron's rating of a run's first and latest issues: (rating, issue number).

    The issue list comes a page at a time. A long run's latest issue is on its
    last page, so that page is asked for directly -- its number from `count`
    and the first page's size, its address from the `next` link Metron gave --
    rather than walking every page between.
    """
    listing = fetch_provider_json(
        "metron", f"{METRON_API_BASE}/issue/?" + urllib.parse.urlencode({"series_id": series_id}), credential) or {}
    rows = [row for row in listing.get("results") or [] if isinstance(row, dict)]
    last_page = _metron_last_page_url(listing, len(rows))
    if last_page:
        rows += [row for row in (fetch_provider_json("metron", last_page, credential) or {}).get("results") or []
                 if isinstance(row, dict)]
    results = sorted(
        (row for row in rows if row.get("id")),
        key=lambda row: str(row.get("cover_date") or "9999"),
    )
    picks = [results[0], results[-1]] if len(results) > 1 else results
    readings = []
    for row in {int(row["id"]): row for row in picks}.values():
        issue = fetch_provider_json("metron", f"{METRON_API_BASE}/issue/{int(row['id'])}/", credential) or {}
        readings.append((str((issue.get("rating") or {}).get("name") or ""), str(issue.get("number") or row.get("number") or "")))
    return readings


def _metron_last_page_url(listing: dict[str, Any], page_size: int) -> str | None:
    """The address of a list's last page, when there is more than one: the
    `next` link with its page number moved on to the last."""
    next_url = str(listing.get("next") or "")
    try:
        count = int(listing.get("count") or 0)
    except (TypeError, ValueError):
        return None
    if not next_url or page_size <= 0 or count <= page_size:
        return None
    parts = urllib.parse.urlsplit(next_url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    if "page" not in query:
        return None
    query["page"] = str(-(-count // page_size))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def metron_configured() -> bool:
    try:
        _provider_credential("metron")
    except (ValueError, KeyError):
        return False
    return True


def _cover_for_rating(file_id: int) -> bytes:
    """A run's cover, small enough to send and large enough that a rating box reads."""
    from PIL import Image

    image = Image.open(io.BytesIO(render_file_page(file_id, 0, "read"))).convert("RGB")
    image.thumbnail((RATING_COVER_LONG_EDGE, RATING_COVER_LONG_EDGE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def find_run_rating(series_run_id: int) -> tuple[str | None, str | None, str | None]:
    """(rating, source, note) for a run, or Nones when nothing says. Raises
    when a source could not be asked, so nothing is recorded."""
    store = catalog_store()
    metron_id = str(store.confirmed_series_provider_ids(series_run_id).get("metron") or "")
    if re.fullmatch(r"\d+", metron_id) and metron_configured():
        known = [(content_rating.normalize_rating(name), f"{name} (issue {number})")
                 for name, number in _metron_run_readings(metron_id, _provider_credential("metron"))]
        known = [(rating, note) for rating, note in known if rating]
        if known:
            return content_rating.strictest(*(rating for rating, _ in known)), "metron", ", ".join(note for _, note in known)
    if load_app_settings().get("ratingsFromCovers") and vision_provider():
        file_id = store.newest_file_of_run(series_run_id)
        if file_id is not None:
            answer = ask_vision_model(_cover_for_rating(file_id), content_rating.COVER_PROMPT)
            rating, printed = content_rating.rating_from_cover_answer(answer)
            if rating:
                return rating, "cover", f"\u201c{printed}\u201d on the cover"
            return None, "cover", None
    return None, None, None


def rate_runs_pass(limit: int = RATING_BATCH) -> int:
    """Look for the ratings of up to `limit` runs. Returns how many were settled."""
    store = catalog_store()
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RATING_STALE_DAYS)).isoformat()
    settled = 0
    for run_id in store.runs_needing_rating(limit=limit, stale_before=stale):
        if _RATINGS_STOP.is_set():
            break
        try:
            rating, source, note = find_run_rating(run_id)
        except Exception as exc:  # noqa: BLE001 -- noted, and asked again once it is stale
            # Left unrecorded, a run whose lookup always fails (a Metron id
            # gone, a file that will not open) came first in every batch and
            # starved the rest. It is put to the back with a note, and "Look
            # again" or the stale window brings it round.
            log_event("run_rating_skipped", level="info", series_run_id=run_id, error=str(exc)[:200])
            store.note_rating_check_failed(run_id, str(exc)[:200])
            continue
        store.record_run_rating(run_id, rating, source, note)
        settled += 1
    return settled


def ratings_worker(stop_event: threading.Event = _RATINGS_STOP) -> None:
    """Settle ratings a batch at a time: shortly after starting, every quarter
    hour, and at once when woken (cover reading switched on, "Check now")."""
    worker_beat("ratings")
    stop_event.wait(120)
    while not stop_event.is_set():
        worker_beat("ratings")
        try:
            while rate_runs_pass() == RATING_BATCH and not stop_event.is_set():
                worker_beat("ratings")
        except Exception as exc:  # noqa: BLE001
            log_exception("ratings_pass_failed", exc, level="warning")
            worker_problem("ratings", exc)
        _RATINGS_WAKE.wait(RATING_POLL_SECONDS)
        _RATINGS_WAKE.clear()


def start_ratings_worker() -> threading.Thread:
    global _RATINGS_THREAD
    if _RATINGS_THREAD and _RATINGS_THREAD.is_alive():
        return _RATINGS_THREAD
    _RATINGS_STOP.clear()
    _RATINGS_THREAD = threading.Thread(target=ratings_worker, name="flipparr-ratings", daemon=True)
    _RATINGS_THREAD.start()
    return _RATINGS_THREAD


def look_for_ratings_again() -> int:
    """Look again at every run nothing was found for, now. Returns how many."""
    count = catalog_store().forget_unfound_ratings()
    _RATINGS_WAKE.set()
    return count


def _run_id_list(value: Any, what: str = "runs") -> list[int]:
    """A list of run ids from a request body, or a ValueError saying what is wrong."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 5000 or not all(
            isinstance(item, (int, str)) and str(item).isdigit() for item in value):
        raise ValueError(f"Say which {what} by their ids")
    return [int(item) for item in value]


def viewer_may_see_run(viewer: "Viewer | None", rating: str | None) -> bool:
    return viewer is None or viewer.is_admin or content_rating.allows(rating, viewer.max_rating, viewer.allow_unrated)


_FACTS_ASKED: set[int] = set()
_FACTS_ASKED_LOCK = threading.Lock()


def learn_about_waiting_requests(requests: list[dict[str, Any]]) -> None:
    """A waiting request made before its facts were looked up -- or whose
    lookup failed -- is looked up once more, once per start."""
    for request in requests:
        if request["status"] not in ("pending", "failed") or "genres" in (request.get("detail") or {}):
            continue
        with _FACTS_ASKED_LOCK:
            if request["id"] in _FACTS_ASKED:
                continue
            _FACTS_ASKED.add(request["id"])
        threading.Thread(target=_learn_about_member_request, args=(request["id"], request["kind"], request["params"]),
                         name=f"flipparr-request-facts-{request['id']}", daemon=True).start()


def ask_member_request(viewer: Viewer, payload: Any) -> tuple[dict[str, Any], bool]:
    """A reader's request, waiting -- or, for a reader the admin trusts
    (auto-approve) and for the admin, carried out at once, still recorded."""
    if not viewer.is_admin and not viewer.can_request:
        raise PermissionError("Requests are turned off for this profile")
    store = catalog_store()
    if not viewer.is_admin and not viewer.can_discover and isinstance(payload, dict) \
            and str(payload.get("kind") or "").startswith("discover"):
        raise PermissionError("Discover is off for this profile")
    catalog = store.catalog()
    # A run above the profile's limit, or a collection with none of its runs
    # within it, is not in the library as far as this profile is concerned --
    # said before anything about it (its title, whether it is followed) is.
    if not viewer.is_admin and viewer.max_rating and isinstance(payload, dict):
        ratings = store.run_age_ratings()
        asked = str(payload.get("kind") or "")
        if asked == "run":
            run_id = str(payload.get("seriesId") or "")
            if not (run_id.isdigit() and int(run_id) in ratings and viewer_may_see_run(viewer, ratings[int(run_id)])):
                raise LookupError("That run is not in the library")
        elif asked == "collection":
            family_id = str(payload.get("collectionId") or "")
            family = next((item for item in catalog.get("families") or [] if str(item.get("id")) == family_id), None)
            run_ids = [int(value) for value in (family or {}).get("runIds") or [] if str(value).isdigit()]
            if not any(viewer_may_see_run(viewer, ratings.get(run_id)) for run_id in run_ids):
                raise LookupError("That collection is not in the library")
    kind, key, title, params, detail = member_request_spec(payload, catalog)
    request, created = store.create_member_request(viewer.id, kind, key, title, params, detail)
    if created:
        threading.Thread(target=_learn_about_member_request, args=(request["id"], kind, params),
                         name=f"flipparr-request-facts-{request['id']}", daemon=True).start()
    if created and (viewer.is_admin or viewer.auto_approve):
        request = approve_member_request(request["id"], viewer.id)
    return request, created


# Failed sign-ins slow further attempts down rather than locking the account.
# Every request through a reverse proxy arrives from the same address, so a
# lock keyed to the caller or the account would let anyone who can reach the
# sign-in page lock the owner out -- OWASP's reason to prefer a growing delay
# over a hard lockout. The first few mistakes are free; after that each failure
# doubles the wait, capped so the owner never waits long after someone else's
# guessing. While a wait is running, attempts are refused without checking the
# password, so a correct guess cannot land inside it. Signed-in devices are
# never affected: only new sign-ins are throttled.
_LOGIN_FREE_FAILURES = 5
_LOGIN_MAX_DELAY_SECONDS = 60
# A quiet spell this long forgets earlier failures.
_LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_LOGIN_THROTTLE_LOCK = threading.Lock()
_LOGIN_THROTTLE: dict[str, dict[str, Any]] = {}


def _throttle_wait(key: str, generation: str, now: float | None = None) -> int:
    """Seconds until another attempt against this key may be checked."""
    now = time.time() if now is None else now
    with _LOGIN_THROTTLE_LOCK:
        bucket = _throttle_bucket(key, generation, now)
        return max(0, math.ceil(bucket["retryAt"] - now))


def _throttle_record(key: str, generation: str, succeeded: bool, now: float | None = None,
                     max_delay: int = _LOGIN_MAX_DELAY_SECONDS) -> None:
    now = time.time() if now is None else now
    with _LOGIN_THROTTLE_LOCK:
        if succeeded:
            _LOGIN_THROTTLE.pop(key, None)
            return
        bucket = _throttle_bucket(key, generation, now)
        bucket["failures"] += 1
        bucket["lastFailure"] = now
        excess = bucket["failures"] - _LOGIN_FREE_FAILURES
        if excess >= 0:
            bucket["retryAt"] = now + min(2 ** excess, max_delay)


def _throttle_bucket(key: str, generation_source: str, now: float) -> dict[str, Any]:
    generation = hashlib.sha256(str(generation_source or "").encode()).hexdigest()
    bucket = _LOGIN_THROTTLE.get(key)
    if (
        bucket is None
        or bucket["generation"] != generation
        or now - bucket["lastFailure"] > _LOGIN_FAILURE_WINDOW_SECONDS
    ):
        bucket = {"failures": 0, "retryAt": 0.0, "lastFailure": now, "generation": generation}
        _LOGIN_THROTTLE[key] = bucket
    return bucket


# A PIN has ten thousand values, so its delay grows further than a password's:
# a child tapping at the admin's profile meets minutes, not seconds.
_PIN_MAX_DELAY_SECONDS = 300


def _too_many(wait: int) -> tuple[dict[str, Any], int, dict[str, str]]:
    return (
        {"error": f"Too many failed attempts. Try again in {wait} second{'s' if wait != 1 else ''}.",
         "retryAfter": wait},
        429, {"Retry-After": str(wait)},
    )


def _login_throttle_key(username: str, config: dict[str, Any]) -> str:
    # Two buckets, so the table cannot grow with invented usernames: the real
    # account, and everything else (which can never succeed anyway).
    configured = str(config.get("username") or "")
    if configured and hmac.compare_digest(username, configured):
        return "account"
    return "other"


def _login_bucket(key: str, config: dict[str, Any], now: float) -> dict[str, Any]:
    bucket = _LOGIN_THROTTLE.get(key)
    # A password reset (possibly from another process, the reset-password
    # command) starts over: the owner should not wait out an attacker's delay
    # on a password the attacker never had.
    generation = hashlib.sha256(str(config.get("passwordHash") or "").encode()).hexdigest()
    if (
        bucket is None
        or bucket["generation"] != generation
        or now - bucket["lastFailure"] > _LOGIN_FAILURE_WINDOW_SECONDS
    ):
        bucket = {"failures": 0, "retryAt": 0.0, "lastFailure": now, "generation": generation}
        _LOGIN_THROTTLE[key] = bucket
    return bucket


def login_retry_after(username: str, config: dict[str, Any], now: float | None = None) -> int:
    """Seconds until another sign-in attempt is allowed; 0 when it is now."""
    now = time.time() if now is None else now
    with _LOGIN_THROTTLE_LOCK:
        bucket = _login_bucket(_login_throttle_key(username, config), config, now)
        return max(0, math.ceil(bucket["retryAt"] - now))


def record_login_result(username: str, config: dict[str, Any], succeeded: bool,
                        now: float | None = None) -> None:
    now = time.time() if now is None else now
    key = _login_throttle_key(username, config)
    with _LOGIN_THROTTLE_LOCK:
        if succeeded:
            _LOGIN_THROTTLE.pop(key, None)
            return
        bucket = _login_bucket(key, config, now)
        bucket["failures"] += 1
        bucket["lastFailure"] = now
        excess = bucket["failures"] - _LOGIN_FREE_FAILURES
        if excess >= 0:
            bucket["retryAt"] = now + min(2 ** excess, _LOGIN_MAX_DELAY_SECONDS)


def reset_password(username: str | None, password: str) -> dict[str, Any]:
    """Set a new password from the server itself, for an owner who has lost it.

    Keeps sign-in in whatever state it was, keeps the username unless a new one
    is given, and replaces the session secret so every device signed in with
    the old password is signed out.
    """
    current = load_auth_config()
    name = (username if username is not None else current["username"]) or ""
    # Setting the password replaces the session secret, which signs out every
    # device.
    return save_auth_config({"username": name.strip(), "password": password})


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


# Covers uploaded for a story arc ("arcs") or a collection ("collections"):
# where each is served, with the file's time on it so a replacement is a
# new address and no screen keeps showing the last one.
_UPLOADED_COVER_ROUTES = {"arcs": "/api/v1/reading-lists/{id}/cover/image",
                          "collections": "/api/v1/run-collections/{id}/cover/image"}


def uploaded_cover_url(kind: str, entity_id: int | str) -> str | None:
    try:
        stamp = int((user_cover_dir(kind) / f"{int(entity_id)}.jpg").stat().st_mtime)
    except (OSError, ValueError):
        return None
    return _UPLOADED_COVER_ROUTES[kind].format(id=int(entity_id)) + f"?v={stamp}"


def remove_uploaded_cover(kind: str, entity_id: int | str) -> None:
    try:
        (user_cover_dir(kind) / f"{int(entity_id)}.jpg").unlink()
    except (FileNotFoundError, ValueError):
        pass


def reading_cache_dir() -> Path:
    """Where rendered reading pages are kept, beside the catalog.

    For the reason in `user_cover_dir`: the container's root is read-only and
    only the config, comics and download directories are mounted. A function
    rather than a constant so a caller that sets its environment after
    importing this module is still heard.
    """
    return _configured_path("READING_CACHE", catalog_database_path().parent / "reading-cache")


def cover_cache_dir() -> Path:
    return _configured_path("COVER_CACHE", catalog_database_path().parent / "cover-cache")


# A comic a person fetched themselves. Larger than any single issue needs, and
# small enough that a mistyped upload cannot fill the config volume.
MANUAL_IMPORT_MAX_BYTES = 2 * 1024 * 1024 * 1024


def acquisition_staging_dir(source: str = "manual") -> Path:
    """Where a comic waits between arriving and being imported.

    Beside the catalog, for the reason in `user_cover_dir`: the container's
    root is read-only and only the config, comics and download directories are
    mounted, so this is the one place a new file can be written.
    """
    staging = _configured_path("ACQUISITION_STAGING", catalog_database_path().parent / "downloads")
    folder = staging / _safe_path_component(source, "manual")
    # Direct downloads' folder took its neutral name in schema 64; the old one
    # moves across when it is first asked for, which main() does at startup
    # so the records the migration moved point at a folder that exists.
    legacy = staging / _LEGACY_SOURCE_NAMES.get(source, "")
    if source in _LEGACY_SOURCE_NAMES and not folder.exists() and legacy.is_dir():
        try:
            legacy.rename(folder)
        except OSError:
            pass
    return folder


# Names direct downloads went by before they took their neutral one
# (2026-10-03, schema 64), read so an existing install keeps its settings.
_LEGACY_SOURCE_NAMES = {"direct_site": "getcomics"}


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


# Types the platform's table may not know. The slim Python image has no
# /etc/mime.types, so the bundled Inter was served as octet-stream there while
# a development machine labelled it correctly.
# Types the slim image's mimetypes table lacks. The manifest is what lets a
# phone install the app to its home screen with the right icon and name, and
# a browser ignores one served as octet-stream.
WEB_ASSET_TYPES = {
    ".woff2": "font/woff2", ".woff": "font/woff",
    ".webmanifest": "application/manifest+json", ".ico": "image/x-icon",
}


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
# qBittorrent's comics category folder, mounted read-only: a torrent keeps
# seeding from it after its files are copied into the library, and only the
# client itself removes anything there.
TORRENT_COMPLETE_ROOT = Path(
    _env("TORRENT_COMPLETE_ROOT", "/downloads/torrents/complete/comics")
)
# A magnet whose file list never arrives, and a torrent nobody shares.
TORRENT_METADATA_GRACE_SECONDS = 2 * 3600
TORRENT_STALLED_SECONDS = 12 * 3600
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
# Manga's "Blue Lock (2021) v18": a bare v and up to three digits. Read as a
# volume only when no issue number is present -- "Nightwing v3 #023" is the
# third Nightwing series, issue 23 -- and never a v and a year (V2011).
SHORT_VOLUME = re.compile(r"(?<![A-Za-z0-9])v(\d{1,3})(?![\dA-Za-z])", re.I)
# "Batman V2011 #001": the series that began in 2011.
VOLUME_YEAR = re.compile(r"(?<![A-Za-z0-9])v((?:19|20)\d{2})(?![\dA-Za-z])", re.I)
# "No 19" is how scene releases mark the issue: American.Vampire.Vol.1.No.19.
ISSUE = re.compile(r"(?:^|\s)(?:#|issue\s*[-#:]?\s*|no\s*)(\d+(?:\.\w+)?)\b", re.I)
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
# "006 AU (2013)" is Marvel's #6AU: two or three capitals right after the
# number, and right before the year or a bracketed tag, belong to it.
_ISSUE_SUFFIX = r"(?:[ .]?(?-i:[A-Z]{2,3})(?=\s*(?:[\(\[]|(?:19|20)\d{2}\b)))?"
PADDED_ISSUE = re.compile(
    rf"{_NOT_A_COUNT}\b(0{{1,3}}\d{{1,3}}{_ISSUE_SUFFIX})\b(?=\s*{_TAGS}(?:{_YEAR_GROUP}|(?:19|20)\d{{2}}\b|$))",
    re.I,
)
UNMARKED_ISSUE = re.compile(
    rf"(?:^|\s){_NOT_A_COUNT}(\d{{1,3}}(?:\.\w+)?{_ISSUE_SUFFIX})\b(?=\s*{_TAGS}{_YEAR_GROUP})",
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
    # The year the publication run began, where the name states one -- kept
    # apart from `year`, which is the issue's own date. See _run_year_clue.
    run_year: int | None = None


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


# Who is asking is always read from the real store. Routes reach the catalog
# through `catalog_store`, which a test may stand in for; identity must not
# change with it.
_profiles_store = catalog_store


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
    "anthropic": {
        "name": "Claude", "credentialField": "apiKey",
        "capabilities": ["Panel view: where the panels are", "Reading order"],
        "description": "Optional. With an Anthropic API key, the reader's panel view asks Claude where the panels "
                       "are on the pages you read, and the order of a layout it cannot settle. Each page is sent "
                       "to Anthropic once, as an image, and the page itself corrects the answer's edges before "
                       "it is kept.",
        "setupSummary": "Optional. Sends the pages you read in panel view to Anthropic, once each, when you turn it on.",
        "credentialUrl": "https://console.anthropic.com/",
        "credentialHelp": "Create an API key in the Anthropic Console. Usage is billed by Anthropic; a page is a "
                          "few thousand tokens, about half a cent, and each page you read is sent once.",
        "defaultEnabled": False, "defaultPriority": 50,
        "kind": "reading",
    },
    "openai": {
        "name": "ChatGPT", "credentialField": "apiKey",
        "capabilities": ["Panel view: where the panels are", "Reading order"],
        "description": "Optional. With an OpenAI API key, the reader's panel view asks ChatGPT where the panels "
                       "are on the pages you read, and the order of a layout it cannot settle. Each page is sent "
                       "to OpenAI once, as an image, and the page itself corrects the answer's edges before it "
                       "is kept. If Claude is on as well, the one with the lower priority number is asked.",
        "setupSummary": "Optional. Sends the pages you read in panel view to OpenAI, once each, when you turn it on.",
        "credentialUrl": "https://platform.openai.com/api-keys",
        "credentialHelp": "Create an API key on the OpenAI platform. Usage is billed by OpenAI; a page is a few "
                          "thousand tokens, and each page you read is sent once.",
        "defaultEnabled": False, "defaultPriority": 60,
        "kind": "reading",
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
        "capabilities": ["Usenet and torrent indexer search", "Release candidates", "Indexer health"],
        "description": "Search your configured indexers for wanted issues and volumes.",
        "setupSummary": "Searches your sources for issues you are missing.",
        "defaultUrl": "http://localhost:9696", "defaultEnabled": False,
        "group": "search",
        "needsApiKey": True,
    },
    "sabnzbd": {
        "name": "SABnzbd", "kind": "Download client",
        "capabilities": ["NZB downloads", "Queue status", "Completed-download tracking"],
        "description": "Download selected NZBs and report their progress back to Flipparr.",
        "setupSummary": "Downloads what you pick and reports progress back.",
        "defaultUrl": "http://localhost:8080", "defaultEnabled": False,
        "group": "clients",
        "needsApiKey": True,
    },
    # Torrents, for what Usenet does not carry: manga volumes, and whole runs
    # as packs -- which the download site loses to file-host pages and corrupt archives
    # (owner, 2026-09-30). It signs in with a username rather than a key,
    # and neither is required: a client that lets its own network in needs
    # no sign-in at all.
    "qbittorrent": {
        "name": "qBittorrent", "kind": "Download client",
        "capabilities": ["Torrent downloads", "Only the wanted files of a pack", "Imports while it seeds"],
        "description": "Download selected torrents, only the issues you want from a pack, and import them while they seed.",
        "setupSummary": "Downloads torrents, including just the issues you want from a pack.",
        "defaultUrl": "http://localhost:8080", "defaultEnabled": False,
        "group": "clients",
        "needsApiKey": False,
    },
    # Direct downloads are a site the operator chooses and enters (2026-10-03):
    # Flipparr names none and has no default address, as it names no indexer.
    # It reads a site that publishes comics as posts with download links in a
    # WordPress-style feed. Some such sites only answer a full browser; for
    # those the operator may run a page fetcher (FlareSolverr's API).
    "flaresolverr": {
        "name": "Page fetcher", "kind": "Browser page fetcher",
        "capabilities": ["Fetches pages through a browser you run", "FlareSolverr-compatible API"],
        "description": "Optional: fetches the direct download site's pages through a browser you run, for a site that answers only browsers.",
        "setupSummary": "Fetches the site's pages through a browser you run.",
        "defaultUrl": "", "defaultEnabled": False,
        "group": "direct",
        "needsApiKey": False,
    },
    "direct_site": {
        "name": "Direct download site", "kind": "Direct download site",
        "capabilities": ["Search", "Direct download links"],
        "description": "A site you choose that publishes comics as posts with download links. Searched for issues your indexers do not carry.",
        "setupSummary": "Searches the site you entered and lists what it has.",
        "defaultUrl": "", "defaultEnabled": False,
        "group": "direct",
        "needsApiKey": False,
    },
}


def _service_needs_api_key(service_id: str) -> bool:
    return bool(ACQUISITION_SERVICE_DEFINITIONS.get(service_id, {}).get("needsApiKey", True))


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
            # What the provider is for -- a source of metadata, or help for the
            # reader -- so Settings can file it on the right page.
            "kind": definition.get("kind", "metadata"),
        })
    providers.sort(key=lambda item: item["priority"])
    return {"providers": providers, "credentialStorage": "local-file"}


def _provider_headers(provider_id: str, credential: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION} (local metadata client)"}
    if provider_id == "metron":
        headers["Authorization"] = f"Bearer {credential}"
    elif provider_id == "anthropic":
        headers["x-api-key"] = credential
        headers["anthropic-version"] = ANTHROPIC_API_VERSION
    elif provider_id == "openai":
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


# Longer than any pacing gap: a wait this long is a pause the provider asked
# for, not its turn coming round.
PROVIDER_PAUSE_WAIT_LIMIT_SECONDS = 10.0


def _wait_for_provider_slot(provider_id: str) -> None:
    """Serialize one provider without blocking traffic to another provider.

    A pacing gap -- a second or three -- is waited out. A pause the provider
    asked for is not: the Grand Comics Database answered a burst of pulls with
    "try again in 620 seconds", and every catalog search then slept ten
    minutes on its GCD half while Metron and Comic Vine had long since
    answered, the page stuck on its skeleton (owner, 2026-09-30). The
    request fails as rate-limited instead, and its caller answers without it
    -- or with a cached copy, which `fetch_provider_json` falls back to.
    """
    while True:
        with _PROVIDER_REQUEST_LOCK:
            now = time.monotonic()
            wait_seconds = max(0.0, _PROVIDER_NEXT_REQUEST_AT.get(provider_id, 0.0) - now)
            if wait_seconds <= 0:
                _PROVIDER_NEXT_REQUEST_AT[provider_id] = (
                    now + _PROVIDER_MIN_INTERVAL_SECONDS.get(provider_id, 1.0)
                )
                return
        if wait_seconds > PROVIDER_PAUSE_WAIT_LIMIT_SECONDS:
            name = PROVIDER_DEFINITIONS.get(provider_id, {}).get("name", provider_id)
            minutes = max(1, math.ceil(wait_seconds / 60))
            raise MetadataRateLimited(
                provider_id, math.ceil(wait_seconds),
                f"{name} asked Flipparr to pause; it is left out for about {minutes} "
                f"more minute{'' if minutes == 1 else 's'}.",
            )
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


# --- Art swatches -----------------------------------------------------------
#
# A drawer takes its background colour from its art, the way Plex's detail
# page does. The browser can only read the pixels of an image served from
# this origin, and most covers come from the providers' image hosts, which
# send no CORS headers. So the server fetches those and hands back a 24px
# swatch the page can read -- never the image itself, only from the hosts
# covers actually come from, over https, and without following redirects
# (an allowed host must not be able to point the fetch somewhere else).

ART_SWATCH_HOSTS = frozenset({"static.metron.cloud", "comicvine.gamespot.com", "files1.comics.org"})
ART_SWATCH_SIZE = 24
ART_SWATCH_MAX_BYTES = 8 * 1024 * 1024
ART_SWATCH_MAX_PIXELS = 60_000_000
_ART_SWATCH_CACHE_LIMIT = 256
_ART_SWATCH_CACHE: dict[str, bytes] = {}
_ART_SWATCH_LOCK = threading.Lock()


class ArtSwatchUnavailable(Exception):
    """The image host did not give back an image."""


def _art_swatch_source(src: str) -> str:
    parsed = urllib.parse.urlsplit(src or "")
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or host not in ART_SWATCH_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("That image is not from a cover host Flipparr reads")
    return urllib.parse.urlunsplit(("https", host, parsed.path, parsed.query, ""))


def art_swatch(src: str) -> bytes:
    """A small PNG of a remote cover, for reading its colour in the page."""
    url = _art_swatch_source(src)
    with _ART_SWATCH_LOCK:
        cached = _ART_SWATCH_CACHE.get(url)
    if cached is not None:
        return cached
    request = urllib.request.Request(url, headers={
        "Accept": "image/*",
        "User-Agent": "Flipparr/1.0 (cover colour)",
    })
    opener = urllib.request.build_opener(_ReportRedirect)
    try:
        with opener.open(request, timeout=10.0) as response:
            payload = response.read(ART_SWATCH_MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ArtSwatchUnavailable("The cover host did not answer") from exc
    if len(payload) > ART_SWATCH_MAX_BYTES:
        raise ArtSwatchUnavailable("The cover is larger than a cover should be")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.width * image.height > ART_SWATCH_MAX_PIXELS:
                raise ArtSwatchUnavailable("The cover is larger than a cover should be")
            image.draft("RGB", (ART_SWATCH_SIZE * 4, ART_SWATCH_SIZE * 4))
            swatch = image.convert("RGB").resize((ART_SWATCH_SIZE, ART_SWATCH_SIZE))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ArtSwatchUnavailable("The cover host did not return an image") from exc
    out = io.BytesIO()
    swatch.save(out, "PNG")
    body = out.getvalue()
    with _ART_SWATCH_LOCK:
        if len(_ART_SWATCH_CACHE) >= _ART_SWATCH_CACHE_LIMIT:
            _ART_SWATCH_CACHE.pop(next(iter(_ART_SWATCH_CACHE)))
        _ART_SWATCH_CACHE[url] = body
    return body


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
    if provider_id not in {"metron", "comic_vine", "anthropic", "openai"}:
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
    elif provider_id == "anthropic":
        data = fetch_provider_json(provider_id, f"{ANTHROPIC_API_BASE}/models?limit=1", credential, force=True)
        if not isinstance(data.get("data"), list):
            raise ValueError("Anthropic did not recognise the API key")
        detail = "API key verified successfully."
    elif provider_id == "openai":
        data = fetch_provider_json(provider_id, f"{OPENAI_API_BASE}/models", credential, force=True)
        if not isinstance(data.get("data"), list):
            raise ValueError("OpenAI did not recognise the API key")
        detail = "API key verified successfully."
    else:
        url = f"{COMIC_VINE_API_BASE}/issues/?" + urllib.parse.urlencode({
            "api_key": credential, "format": "json", "limit": 1, "field_list": "id",
        })
        data = fetch_provider_json(provider_id, url, credential, force=True)
        if str(data.get("status_code")) != "1":
            raise ValueError(str(data.get("error") or "Comic Vine rejected the API key"))
        detail = "API key verified successfully."
    return {"provider": provider_id, "status": "connected", "detail": detail}


# Download clients file Flipparr's downloads under a category of their own.
_CATEGORY_SERVICES = ("sabnzbd", "qbittorrent")
# Every client that can take a release; automatic search needs one of them.
DOWNLOAD_CLIENTS = ("sabnzbd", "qbittorrent")


def _acquisition_service_defaults() -> dict[str, dict[str, Any]]:
    return {
        service_id: {
            "enabled": bool(definition["defaultEnabled"]),
            "url": str(definition["defaultUrl"]),
            **({"category": "comics"} if service_id in _CATEGORY_SERVICES else {}),
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
            for new_id, old_id in _LEGACY_SOURCE_NAMES.items():
                if old_id in saved and new_id not in saved:
                    saved[new_id] = saved.pop(old_id)
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
        for key, variable in (("url", "QBITTORRENT_URL"), ("username", "QBITTORRENT_USERNAME"),
                              ("password", "QBITTORRENT_PASSWORD"), ("category", "QBITTORRENT_CATEGORY")):
            value = str(os.environ.get(variable) or "").strip()
            if value:
                config["qbittorrent"][key] = value.rstrip("/") if key == "url" else value
        if str(os.environ.get("QBITTORRENT_URL") or "").strip():
            config["qbittorrent"]["enabled"] = True
        return config


def public_acquisition_service_config() -> dict[str, Any]:
    config = load_acquisition_service_config()
    services = []
    for service_id, definition in ACQUISITION_SERVICE_DEFINITIONS.items():
        values = config[service_id]
        configured = bool(values.get("url")) and (
            bool(values.get("apiKey")) or not _service_needs_api_key(service_id)
        )
        services.append({
            "id": service_id, "name": definition["name"], "kind": definition["kind"],
            "description": definition["description"],
            "setupSummary": definition.get("setupSummary") or definition["description"],
            "capabilities": definition["capabilities"],
            # Settings shows the services in what they do: finding releases,
            # downloading them, and the download site with the page fetcher it needs.
            "group": definition.get("group") or "search",
            "configured": configured, "enabled": bool(values.get("enabled")) and configured,
            "url": _redacted_upstream_url(
                str(values.get("url") or definition["defaultUrl"])
            ).split("?", 1)[0],
            "category": str(values.get("category") or "comics") if service_id in _CATEGORY_SERVICES else None,
            # A sign-in name is not a secret -- the settings form shows it back
            # -- but its companion never leaves the server.
            **({"username": str(values.get("username") or "")} if service_id == "qbittorrent" else {}),
            "credentialHint": "Saved locally" if values.get("apiKey") or values.get("password") else None,
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
        if service_id == "qbittorrent":
            if "username" in payload:
                current["username"] = str(payload.get("username") or "").strip()
            # An empty field keeps the saved one, as the API key field does.
            supplied = str(payload.get("password") or "")
            if supplied:
                current["password"] = supplied
        if bool(payload.get("clearCredentials")):
            for credential in ("apiKey", "username", "password"):
                current.pop(credential, None)
        if "enabled" in payload:
            current["enabled"] = bool(payload["enabled"])
        if service_id in _CATEGORY_SERVICES and "category" in payload:
            category = str(payload.get("category") or "").strip()
            if not category:
                raise ValueError(f"Enter a {ACQUISITION_SERVICE_DEFINITIONS[service_id]['name']} category")
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
    if not api_key and _service_needs_api_key(service_id):
        raise ValueError(f"Enter a {ACQUISITION_SERVICE_DEFINITIONS[service_id]['name']} API key first")
    if service_id == "flaresolverr":
        endpoint = _solver_endpoint(url)
        answer = solver_request({"cmd": "sessions.list"}, url=url, timeout=30.0)
        version = str(answer.get("version") or "").strip()
        detail = f"Connected to the page fetcher at {endpoint}"
        detail += f" (version {version})." if version else "."
        if not str(url or "").rstrip("/").endswith("/v1"):
            # Saying so now rather than letting every grab fail in silence,
            # which is the shape of mylar3's #1815.
            detail += " Its API lives at /v1, so that is the address Flipparr will use."
        return {"service": service_id, "status": "connected", "detail": detail}
    if service_id == "qbittorrent":
        username = str(payload.get("username") if payload.get("username") is not None else config.get("username") or "")
        password = str(payload.get("password") or config.get("password") or "")
        category = str(payload.get("category") or config.get("category") or "comics").strip() or "comics"
        client = torrent_client.QBittorrent(url, username, password, user_agent=f"Flipparr/{APP_VERSION}")
        try:
            version = client.version()
            default = client.default_save_path().rstrip("/")
            # Created beside the client's own folder when it is not there yet,
            # so its downloads sit in a folder Flipparr can be given.
            saves_to = client.ensure_category(category, f"{default}/{category}" if default else "")
            selective = client.supports_selection()
        except torrent_client.TorrentClientError as exc:
            raise ValueError(str(exc)) from None
        detail = f"Connected to qBittorrent {version}; downloads will use the “{category}” category"
        detail += f", which saves to {saves_to}." if saves_to else "."
        if saves_to and Path(saves_to.rstrip("/")).name.casefold() != TORRENT_COMPLETE_ROOT.name.casefold():
            detail += (f" Flipparr reads finished torrents from a folder named “{TORRENT_COMPLETE_ROOT.name}”,"
                       " so that category's folder has to end in that name.")
        if not selective:
            detail += (" This version downloads a pack whole, so packs are not taken from it;"
                       " qBittorrent 4.5.5 or later downloads only the issues wanted.")
        if not (TORRENT_COMPLETE_ROOT.is_dir() and os.access(TORRENT_COMPLETE_ROOT, os.R_OK | os.X_OK)):
            # Gate 3 (2026-10-05): checked nowhere before, so a missing mount
            # showed only as finished torrents waiting for good.
            detail += (f" Flipparr cannot read its torrents folder ({TORRENT_COMPLETE_ROOT}) yet: mount"
                       f" qBittorrent's “{category}” folder there, or finished torrents will wait.")
        return {"service": service_id, "status": "connected", "detail": detail}
    if service_id == "direct_site":
        found = direct_site_search("batman", limit=3, base_url=url)
        detail = (
            f"Reached the download site; its search answered with {len(found)} result"
            f"{'' if len(found) == 1 else 's'}."
            if found else
            "Reached the download site, but its search returned nothing to read."
        )
        return {"service": service_id, "status": "connected", "detail": detail}
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


class AcquisitionServiceOff(ValueError):
    """A service turned off or not connected in Settings: an answer, not a
    failure. A download already in it waits for it to come back."""


def _enabled_acquisition_service(service_id: str) -> dict[str, Any]:
    config = load_acquisition_service_config().get(service_id) or {}
    if not config.get("enabled") or (_service_needs_api_key(service_id) and not config.get("apiKey")):
        name = ACQUISITION_SERVICE_DEFINITIONS[service_id]["name"]
        raise AcquisitionServiceOff(f"Connect and enable {name} in Settings first")
    return {**config, "url": _normalize_service_url(config.get("url"))}


def _enabled_download_clients() -> list[str]:
    """The download clients that can take a release right now."""
    ready = []
    for service in DOWNLOAD_CLIENTS:
        try:
            _enabled_acquisition_service(service)
        except Exception:  # noqa: BLE001 -- not configured is an answer
            continue
        ready.append(service)
    return ready


# One session per client configuration: qBittorrent signs in with a cookie,
# and a fresh client per call would sign in on every question.
_QBITTORRENT_CLIENTS: dict[tuple[str, str, str], torrent_client.QBittorrent] = {}
_QBITTORRENT_CLIENTS_LOCK = threading.Lock()


# How long the workers wait after qBittorrent refuses Flipparr's sign-in
# before trying again: four tries an hour stay under its default ban (five
# failures). Saving new credentials makes a new client, which asks at once.
QBITTORRENT_REFUSED_WAIT_SECONDS = 900


def _qbittorrent_client() -> torrent_client.QBittorrent:
    config = _enabled_acquisition_service("qbittorrent")
    key = (str(config["url"]), str(config.get("username") or ""),
           hashlib.sha256(str(config.get("password") or "").encode()).hexdigest())
    with _QBITTORRENT_CLIENTS_LOCK:
        client = _QBITTORRENT_CLIENTS.get(key)
        if client is None:
            _QBITTORRENT_CLIENTS.clear()
            client = torrent_client.QBittorrent(
                key[0], key[1], str(config.get("password") or ""), user_agent=f"Flipparr/{APP_VERSION}",
                refused_wait=QBITTORRENT_REFUSED_WAIT_SECONDS,
            )
            _QBITTORRENT_CLIENTS[key] = client
    return client


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


# A poster's reading order in front of the name: "Flashpoint. 02.03 Flashpoint
# - Abin Sur - The Green Lantern V2011 #001" is the third of the event's second
# week, and "02.03" is neither the issue nor part of the series.
_RELEASE_READING_ORDER = re.compile(r"^\s*[A-Za-z][A-Za-z' ]{1,30}\.\s*\d{1,2}\.\d{1,2}\s+")
# A batch counter glued to the name: "010-Flashpoint -Abin Sur", "25.Flashpoint-Secret.Seven".
# Glued only: "100 Bullets" and "2000 AD" keep their numbers.
_RELEASE_BATCH_COUNTER = re.compile(r"^\s*\d{1,3}[-.](?=[A-Za-z])")


def _release_issue_matches(title: str, issue_number: Any) -> bool:
    number = str(issue_number or "").strip()
    if not number:
        return False
    pattern = _issue_number_pattern(number)
    title = _RELEASE_READING_ORDER.sub("", str(title or ""))
    # Numbers that belong to something other than this issue are taken out
    # first: "02 of 04" counts the run, and "Vol 04" is a collected volume.
    # Both read as issue four, so a wanted issue would have been answered
    # with a different issue, or with a trade paperback of the whole run.
    # A Usenet subject's date stamp and part counter carry numbers that are not
    # the issue: "Week of 2022.02.16 [53/72] ... Woman of Tomorrow 08" matched
    # issue 2 on the 02 in its date. Taken out first, before "of N" can eat
    # the year out of "Week of 2022" and leave the date's 02 behind.
    cleaned = re.sub(r"\b(?:19|20)\d{2}[.\-/_ ]\d{1,2}[.\-/_ ]\d{1,2}\b", " ", str(title or ""))
    cleaned = re.sub(r"[\[(]\s*\d{1,4}\s*/\s*\d{1,4}\s*[\])]", " ", cleaned)
    cleaned = re.sub(r"\b(?:vol|volume|v)\.?\s*\d{1,4}\b", " ", cleaned, flags=re.I)
    # "02 of 04" counts the run; a year is never a count.
    cleaned = re.sub(r"\bof\s*\d{1,3}\b", " ", cleaned, flags=re.I)
    # A stated range is a pack's bounds, not an issue number. "Chew 001-060"
    # answered a job for #60, and the download site writes "#1 - 8" the same way -- in
    # both the wanted number is only there as the end of a range.
    cleaned = _ISSUE_RANGE.sub(" ", cleaned)
    return bool(re.search(rf"{pattern}{_NOT_GLUED_TO_A_TAG}", cleaned, re.I))


# A number run into letters and more digits is a name, not an issue: the
# release group "21A1" is not issue 21. A variant letter alone ("21a") still is.
# Nor is a number with a decimal after it: "023.2" is issue 23.2, not 23 --
# a fraction of one or two digits, that is; "068.2024" is issue 68 and a year.
# Nor one with a short capitalised suffix: "006 AU" is Age of Ultron's #6AU,
# not #6 (capitals only, whatever flags the caller passes: "02 of 04" stays
# issue 2).
_NOT_GLUED_TO_A_TAG = r"(?!\d|[A-Za-z]\d|\.\d{1,2}(?!\d)|[ .]?(?-i:[A-Z]{2,3})\b)"

# "001-060", "#1-60", "1 - 60". Not "2009-2016", which is when the run ran.
_ISSUE_RANGE = re.compile(r"(?<![\d.])#?\s*(\d{1,4})\s*[-–—]\s*#?\s*(\d{1,4})(?![\d.])")
_COMPLETE_RUN_WORDING = re.compile(
    r"\b(?:complete(?:\s+(?:series|collection|run|set))?|full\s+run|entire\s+series)\b", re.I
)
# A collected edition says so. Its numbers count books, not issues.
_COLLECTED_EDITION_WORDING = re.compile(
    r"\bv(?:ol(?:ume)?)?\.?\s*\d{1,3}\b|\btpb\b|\bomnibus\b|\bhardcover\b|\bhc\b|\bdeluxe\b", re.I
)


def _release_issue_range(title: Any, run_issue_count: Any = None) -> tuple[int, int] | None:
    """The issues a release says it holds, or None when it is not a pack.

    A pack is the whole point of asking: one download that answers many wanted
    issues. It is also the easiest thing to misread, so what is *not* a range
    is most of this:

    - `(2009-2016)` is when the run ran. Two years are never issue numbers.
    - `v01-v12` counts collected editions, and editions are not an automatic
      acquisition target. Answering a request for issue 4 with volume 4 is the
      same mistake `_release_issue_matches` strips out.
    - `02 of 04` counts the run's parts, and a Usenet subject's `[53/72]` and
      date stamp carry numbers that are not issues either.

    "Complete Series" names no numbers, so the run's own issue count supplies
    them -- and only when nothing in the title says collected edition.
    """
    cleaned = re.sub(r"\b(?:19|20)\d{2}[.\-/_ ]\d{1,2}[.\-/_ ]\d{1,2}\b", " ", str(title or ""))
    cleaned = re.sub(r"[\[(]\s*\d{1,4}\s*/\s*\d{1,4}\s*[\])]", " ", cleaned)
    cleaned = re.sub(r"\bof\s*\d{1,3}\b", " ", cleaned, flags=re.I)
    volumes = bool(_COLLECTED_EDITION_WORDING.search(cleaned))
    # Take the volume range out before looking for an issue range, or "v01-v12"
    # reads as issues 1 to 12.
    cleaned = re.sub(
        r"\bv(?:ol(?:ume)?)?\.?\s*\d{1,4}\s*[-–—]\s*(?:v(?:ol(?:ume)?)?\.?\s*)?\d{1,4}\b",
        " ", cleaned, flags=re.I,
    )
    for match in _ISSUE_RANGE.finditer(cleaned):
        first, last = int(match.group(1)), int(match.group(2))
        if 1900 <= first <= 2099 and 1900 <= last <= 2099:
            continue
        if last <= first or last - first > 2000:
            continue
        return (first, last)
    if not volumes and _COMPLETE_RUN_WORDING.search(cleaned):
        try:
            count = int(run_issue_count or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 1:
            return (1, count)
    return None


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


def _release_name_forms(title: str) -> list[str]:
    """Where in a release title its name might be, most likely first.

    A yEnc subject carries the filename in quotes, but indexers cut subjects
    short and the quotes stop pairing up. American Vampire #21's only posting
    is 'Gold Line" releases (2013.01.25) - "American Vampire 021 (2012)
    (Digital) (Zone-Empire)', which read quote to quote is ' releases
    (2013.01.25) - ' -- so its series never matched and it was thrown away.
    Every quoted stretch is a candidate, the last first, then the whole title.
    """
    text = _RELEASE_READING_ORDER.sub("", str(title or ""))
    forms = [match.group(1) for match in _RELEASE_QUOTED_NAME.finditer(text)]
    if '"' in text:
        forms += [part for part in reversed(text.split('"')) if len(part.strip()) >= 4]
    forms.append(text)
    # A batch counter in front of the name is tried both ways: stripped for
    # "010-Flashpoint -Abin Sur", kept for a name that starts with a number.
    forms += [_RELEASE_BATCH_COUNTER.sub("", form) for form in list(forms) if _RELEASE_BATCH_COUNTER.match(form)]
    seen: set[str] = set()
    ordered = []
    for form in forms:
        if form.strip() and form.strip() not in seen:
            seen.add(form.strip())
            ordered.append(form)
    return ordered


def _release_series_leads(title: str, issue_number: Any) -> list[str]:
    """Every series name a release could be stating, most likely first."""
    number = str(issue_number or "").strip()
    if not number:
        return []
    leads = []
    for form in _release_name_forms(title):
        lead = _release_series_lead_in(form, number)
        if lead is not None:
            leads.append(lead)
    return leads


def _release_series_lead(title: str, issue_number: Any) -> str | None:
    """The series name a release most likely states, or None when it states none."""
    leads = _release_series_leads(title, issue_number)
    return leads[0] if leads else None


def _release_series_lead_in(text: str, number: str) -> str | None:
    """The series named in one stretch of a release title, before its number.

    A release names its series before the issue number and its provenance
    after, so the series is the text in front of that number -- once the
    wrappers Usenet adds in front of the name are taken off.
    """
    def strip_noise(value: str) -> str:
        previous = None
        while previous != value:
            previous = value
            value = _RELEASE_NOISE_PREFIX.sub("", value)
        return value

    # Once with the separators the poster used, once after they are normalised:
    # a date is "2013.05.01" before and "2013 05 01" after. A decimal issue
    # number keeps its dot, or "023.2" could never be found as 23.2.
    text = strip_noise(text)
    text = strip_noise(_separators_to_spaces(text))
    found = re.search(rf"{_issue_number_pattern(number)}{_NOT_GLUED_TO_A_TAG}", text, re.I)
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
    ).strip(" -.:#")
    # A date stamp between the name and the number is the posting's, not the
    # series': "Secret Files and Origins, 2007-12-28 (01)" names no series
    # ending in a date.
    value = value.strip(" -.:,([#")
    value = re.sub(r"[\s,\-]*\b(?:19|20)\d{2}(?:[.\-/_ ]\d{1,2}[.\-/_ ]\d{1,2})?\s*$", "", value).strip(" -.:,([")
    # "The Green Lantern V2011 #001": the volume's year, tagged onto the name.
    value = re.sub(r"[\s\-]*\bv(?:ol)?\.?\s*(?:19|20)\d{2}\s*$", "", value, flags=re.I).strip(" -.:,([")
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
    # A release names its series before its number -- but a pack's number is a
    # range, and the wanted issue is somewhere inside it. Read before the range
    # as well, or "Supergirl - Woman of Tomorrow #1 - 8" reads as a series
    # called "Supergirl - Woman of Tomorrow #1 -".
    numbers = [issue_number]
    stated = _release_issue_range(title)
    if stated:
        numbers.append(stated[0])
    return any(
        _release_lead_is_series(lead, wanted, series_title, publisher)
        for number in numbers
        for lead in _release_series_leads(title, number)
    )


def _release_lead_is_series(lead: str, wanted: str, series_title: Any, publisher: Any) -> bool:
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
    if bool(bare_wanted) and normalized_title(_without_leading_article(trimmed)) == bare_wanted:
        return True
    # "Convergence - The Titans" is "Convergence: Titans": an article inside
    # the name comes and goes between a catalog and a poster. Still an
    # equality of whole names, so "Thor: The Deviants Saga" is no nearer "Saga".
    plain_wanted = _without_inner_articles(_without_leading_article(series_title))
    if bool(plain_wanted) and _without_inner_articles(_without_leading_article(trimmed)) == plain_wanted:
        return True
    return _lead_extends_series(trimmed, series_title)


def _without_inner_articles(value: Any) -> str:
    """A name normalised with its articles gone, for comparing two spellings of one name."""
    return normalized_title(re.sub(r"\b(?:the|a|an)\b", " ", str(value or ""), flags=re.I))


def _title_words(value: Any) -> list[str]:
    """A title as words, "&" kept as one, for comparing names word by word."""
    return re.findall(r"[a-z0-9]+|&", str(value or "").lower())


def _lead_extends_series(lead: str, series_title: Any) -> bool:
    """A release name that begins with the whole wanted name and goes on with
    "and …": DC's one-shots are "Secret Files and Origins" on the cover and
    "Secret Files" in a catalog, and the release is right. Only for a wanted
    name of three words or more, and at most three words after the "and" --
    "Hulk and Power Pack" is not Hulk, "Batman and Robin" is not Batman, and
    "Green Lantern and the Sinestro Corps War" is not Green Lantern."""
    wanted = _title_words(_without_leading_article(series_title))
    words = _title_words(_without_leading_article(lead))
    if len(wanted) < 3 or len(words) <= len(wanted) or words[:len(wanted)] != wanted:
        return False
    rest = words[len(wanted):]
    return rest[0] in ("and", "&") and 1 <= len(rest) - 1 <= 3


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
    else:
        covered = _release_pack_coverage(title, context)
        if covered:
            # Deliberately below an exact single, so a release that is this
            # issue always outranks a pack that merely contains it. On its own
            # a pack therefore lands under the score an automatic grab needs;
            # whether one may be taken anyway is decided per run, not here.
            score += 22
            reasons.append(
                f"Issues #{covered[0]}-#{covered[1]} include #{context.get('issueNumber')}"
            )
    year = str(context.get("publicationYear") or context.get("seriesYear") or "").strip()
    if year and year in title:
        score += 10
        reasons.append(f"Publication year {year} matches")
    category_ids = {
        str(category.get("id") if isinstance(category, dict) else category)
        for category in (release.get("categories") or [])
    }
    # EBook counts as much as Comics: a graphic novel with no issues is filed
    # there as a matter of course (The Adventure Zone, owner, 2026-09-30).
    # What rules out a novel of the same name is the import's look inside.
    if "7030" in category_ids:
        score += 5
        reasons.append("Listed as a comic")
    elif "7020" in category_ids:
        score += 5
        reasons.append("Listed as an ebook")
    return min(score, 100), reasons


# A pack is worth taking when it answers most of what a run is missing, not to
# fill one gap -- the difference between one download and sixty, against the
# risk of forty gigabytes for a single issue.
PACK_MINIMUM_WANTED = 5
PACK_BYTES_PER_COVERED_ISSUE = 1_500_000_000
# A single issue has the same ceiling. The largest file in a 1,569-file library
# is 239 MB (a manga volume; p99 213 MB), so a release six times that is not
# an issue: the 5.2 GB "Absolute Batman 024 (digital-mobile)" SABnzbd paused
# as password-protected was grabbed without a second thought and sat a day
# at 39%. Held back rather than barred: a person may still choose it.
SINGLE_RELEASE_MAX_BYTES = PACK_BYTES_PER_COVERED_ISSUE


def _issue_number(value: Any) -> int | None:
    match = re.match(r"^\s*#?\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def _pack_worth_taking(
    context: dict[str, Any], candidate: dict[str, Any], wanted: list[int],
) -> tuple[bool, str]:
    """Whether one pack may stand in for many wanted issues, and why not.

    Deliberately separate from scoring: a pack always scores below the issue
    itself, and this decides whether taking one anyway is proportionate for
    *this* run. A person choosing a pack in Find release is never asked.
    """
    pack = candidate.get("pack") or {}
    first, last = _issue_number(pack.get("first")), _issue_number(pack.get("last"))
    if first is None or last is None:
        return False, "not a pack"
    covered = [number for number in wanted if first <= number <= last]
    run_count = _issue_number(context.get("runIssueCount")) or 0
    mostly_missing = (
        len(wanted) >= PACK_MINIMUM_WANTED
        or (run_count > 0 and len(wanted) * 2 >= run_count)
    )
    if not mostly_missing:
        return False, f"only {len(wanted)} issue{'' if len(wanted) == 1 else 's'} wanted"
    if len(covered) < min(PACK_MINIMUM_WANTED, len(wanted)):
        return False, f"covers {len(covered)} of the {len(wanted)} wanted"
    size = int(candidate.get("sizeBytes") or 0)
    cap = PACK_BYTES_PER_COVERED_ISSUE * len(covered)
    # A torrent pack is fetched a file at a time: only the wanted issues are
    # downloaded, and their size is judged when the file list is known.
    if size and size > cap and candidate.get("source") != "torrent":
        return False, f"{size / 1_000_000_000:.1f} GB for {len(covered)} issues"
    return True, f"covers {len(covered)} wanted issues"


def _automatically_takeable(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """The candidates an automatic grab may try, and why a torrent was not.

    A torrent nobody shares never finishes, and one with no client connected
    cannot be sent anywhere; both stay in Find release."""
    takeable, unshared = [], []
    for item in candidates:
        if item.get("grabbable") is False:
            continue
        if item.get("source") == "torrent" and not int(item.get("seeders") or 0):
            unshared.append(item)
            continue
        takeable.append(item)
    note = ""
    if unshared and not takeable:
        note = (f"Found {unshared[0].get('title') or 'a torrent'} but nobody is sharing it right now. "
                "Find release lists it if you want it.")
    return takeable, note


# A feed of search results, not a download: small, and capped so a redirect to
# something enormous cannot be read into memory.
DIRECT_SITE_FEED_MAX_BYTES = 4 * 1024 * 1024
_DIRECT_SITE_SIZE = re.compile(r"size\s*:?\s*([0-9.]+)\s*(kb|mb|gb)", re.I)
_SIZE_SCALE = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}


def _plain_dashes(value: Any) -> str:
    """En and em dashes as hyphens.

    the download site writes "Supergirl – Woman of Tomorrow #8"; every matcher here was
    written against Usenet names, which use a plain hyphen or nothing at all.
    """
    return str(value or "").replace("–", "-").replace("—", "-")


def _direct_site_size_bytes(text: Any) -> int:
    match = _DIRECT_SITE_SIZE.search(str(text or ""))
    if not match:
        return 0
    return int(float(match.group(1)) * _SIZE_SCALE[match.group(2).lower()])


def direct_site_search(query: str, limit: int = 20, base_url: str | None = None) -> list[dict[str, Any]]:
    """What the download site lists for a query, read from its search feed.

    The feed rather than the page: it parses without guessing at markup, and it
    carries the size and year the scoring wants. Measured 2026-09-15, it also
    answers a plain request, while the post pages behind it are browser-only
    challenged -- so finding things needs nothing, and fetching them will.
    """
    # No site is built in: it is the one the operator entered and switched on.
    base = str(base_url or "").strip() or str(_enabled_acquisition_service("direct_site").get("url") or "")
    if not base:
        raise ValueError("Enter the direct download site's address in Settings first")
    url = base.rstrip("/") + "/?" + urllib.parse.urlencode({"s": query, "feed": "rss2"})
    body = fetch_bytes_with_headers(
        url,
        {"User-Agent": f"Flipparr/{APP_VERSION}", "Accept": "application/rss+xml, application/xml"},
        timeout=20.0, max_bytes=DIRECT_SITE_FEED_MAX_BYTES,
    )
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"The download site returned something that is not a feed: {exc}") from exc
    # A challenge page is often well-formed enough to parse, and would then read
    # as a search that found nothing. An empty feed and a blocked one are not
    # the same answer, so the root element has to actually be a feed.
    if root.tag.lower() != "rss" and root.find("channel") is None:
        raise ValueError("The download site answered with a page, not a feed")
    found: list[dict[str, Any]] = []
    for item in root.iter("item"):
        title = html.unescape(str(item.findtext("title") or "")).strip()
        link = str(item.findtext("link") or "").strip()
        if not title or not _on_site(link, base):
            continue  # a result that points off the site is not one of its posts
        description = html.unescape(str(item.findtext("description") or ""))
        found.append({
            "title": title, "url": link,
            "sizeBytes": _direct_site_size_bytes(description),
            "publishDate": str(item.findtext("pubDate") or "").strip() or None,
        })
        if len(found) >= limit:
            break
    return found


SOLVER_TIMEOUT_MS = 60_000
DIRECT_SITE_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024 * 1024
_DIRECT_SITE_LINK = re.compile(r"href=.(https://[a-z0-9.-]+/dls/[^\s\"'>]+)")


def _solver_endpoint(url: Any) -> str:
    """The page fetcher's API address, with the path it actually answers on.

    FlareSolverr serves its API at /v1 and returns 405 for a POST anywhere
    else. Mylar3 logs that exact mistake: a URL without /v1 makes every DDL
    grab fail, and fail silently, because the challenge step never runs.
    """
    address = str(url or "").strip().rstrip("/")
    if not address:
        raise ValueError("Enter the page fetcher's address first")
    return address if address.endswith("/v1") else address + "/v1"


def solver_request(payload: dict[str, Any], *, url: str | None = None, timeout: float = 90.0) -> dict[str, Any]:
    """Ask the page fetcher for something, and fail with what it actually said."""
    endpoint = _solver_endpoint(url or _enabled_acquisition_service("flaresolverr").get("url"))
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"},
    )
    try:
        with _safe_urlopen(request, timeout=timeout) as response:
            answer = json.loads(response.read(4 * 1024 * 1024) or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 405:
            raise ValueError(
                f"{endpoint} refused a POST: the page fetcher's API lives at /v1, so check the address"
            ) from exc
        raise ValueError(f"The page fetcher answered {exc.code}") from exc
    if str(answer.get("status") or "").casefold() != "ok":
        raise ValueError(str(answer.get("message") or "The page fetcher could not fetch that page"))
    return answer


def solver_fetch_html(url: str) -> str:
    """One page, fetched through the page fetcher's browser."""
    answer = solver_request({"cmd": "request.get", "url": url, "maxTimeout": SOLVER_TIMEOUT_MS})
    solution = answer.get("solution") or {}
    html_text = str(solution.get("response") or "")
    if "just a moment" in html_text[:4000].casefold():
        raise ValueError("The page fetcher returned a browser check instead of the page")
    return html_text


_DIRECT_SITE_HOST_WORDS = re.compile(
    r"\b(?:Main Server|Google Drive|YanDisk|Mega|Mediafire|Zippyshare|Read Online|Download Now)\b|\|", re.I,
)
_DIRECT_SITE_RANGE = re.compile(r"#\s*(\d+)(?:\s*[\u2013\u2014-]\s*#?\s*(\d+))?")
# A part says when its issues ran, "(1994-1996)", and how big it is, "(517 MB)".
_DIRECT_SITE_PART_YEARS = re.compile(r"\(\s*((?:19|20)\d{2})(?:\s*[\u2013\u2014-]\s*((?:19|20)\d{2}))?\s*\)")
_DIRECT_SITE_PART_SIZE = re.compile(r"\(\s*([0-9.]+)\s*(kb|mb|gb)\s*\)", re.I)


def _direct_site_part_years(label: Any) -> tuple[int, int] | None:
    """The years a part says its issues ran, or None when it names none."""
    match = _DIRECT_SITE_PART_YEARS.search(str(label or ""))
    if not match:
        return None
    first = int(match.group(1))
    return (first, int(match.group(2)) if match.group(2) else first)


def _direct_site_part_fits_years(label: Any, years: Any) -> bool:
    """Whether a part's stated years can be this run's.

    A collection post holds every run of a name in turn -- "R.E.B.E.L.S.
    Vol. 1 #0 – 17 (1994-1996)" and "Vol. 2 #1 – 28 + Annual (2009-2011)" --
    and both hold a #12. The years tell them apart; a part that names none,
    or a run whose year is unknown, is given the benefit of the doubt.
    """
    stated = _direct_site_part_years(label)
    known = {int(year) for year in (years or ()) if _provider_year(year)}
    if not stated or not known:
        return True
    return any(stated[0] - 1 <= year <= stated[1] + 1 for year in known)


def _on_site(url: Any, site: Any) -> bool:
    """Whether a link is on the direct-download site the operator entered (or
    a subdomain of it). Only the site itself is asked for anything: a page
    cannot send the downloader to another host, least of all one inside the
    network Flipparr runs on."""
    try:
        host = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
        home = (urllib.parse.urlsplit(str(site or "")).hostname or "").lower()
    except ValueError:
        return False
    home = home[4:] if home.startswith("www.") else home
    return bool(host and home) and (host == home or host.endswith("." + home))


def _direct_site_home() -> str:
    try:
        return str(_enabled_acquisition_service("direct_site").get("url") or "")
    except ValueError:
        return ""


def direct_site_download_links(page: str, site: str | None = None) -> list[dict[str, Any]]:
    """Every direct link a post offers, each with the part it belongs to.

    A run's post is in parts -- "The Woods #1 – 12 (474 MB) : :" and its
    buttons, then "#13 – 23", "#24 – 33", "#34 – 36", then the trades -- and
    each part's label sits in the text before its first button, ending in
    " : :". A button with no label of its own belongs to the part before it.
    `first`/`last` are the issues a part says it holds, None for a trade or
    a post with no parts.
    """
    links: list[dict[str, Any]] = []
    last_end = 0
    label = ""
    for match in _DIRECT_SITE_LINK.finditer(page or ""):
        if site is not None and not _on_site(match.group(1), site):
            continue
        raw = page[last_end:match.start()]
        # The match ends inside the previous button's tag; the rest of that
        # tag is not text.
        raw = raw[raw.find(">") + 1:] if ">" in raw else raw
        between = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))).strip()
        last_end = match.end()
        # The text of the previous part's buttons is host names; what is left
        # after them, up to the " : :" the label ends in, is the new label.
        if ":" in between:
            head = between.rsplit(":", 2)[0] if between.rstrip().endswith(":") else between.rsplit(":", 1)[0]
            cleaned = re.sub(r"\s+", " ", _DIRECT_SITE_HOST_WORDS.sub(" ", head)).strip(" :|-")
            if cleaned:
                # The page's own title and facts ("Size : 3.6 GB") come before
                # the first part's label: the part is the last range named,
                # and its label starts after the last colon before it.
                spans = list(_DIRECT_SITE_RANGE.finditer(cleaned))
                if spans:
                    start = cleaned.rfind(":", 0, spans[-1].start()) + 1
                    label = cleaned[start:].strip(" :|-")[-160:]
                else:
                    label = cleaned[-160:]
        # The post's size fact, "Size : 1.5 GB", ends just before the first
        # part's label and would otherwise lead it.
        label = re.sub(r"^\s*[0-9.]+\s*(?:kb|mb|gb)\b\s*", "", label, flags=re.I)
        span = _DIRECT_SITE_RANGE.search(label)
        first = int(span.group(1)) if span else None
        last = int(span.group(2)) if span and span.group(2) else first
        size = _DIRECT_SITE_PART_SIZE.search(label)
        links.append({
            "url": match.group(1), "label": label, "first": first, "last": last,
            "years": _direct_site_part_years(label),
            "sizeBytes": int(float(size.group(1)) * _SIZE_SCALE[size.group(2).lower()]) if size else 0,
        })
    return links


def direct_site_download_link(post_url: str, issue_number: Any = None, years: Any = None) -> str:
    """The direct link a download-site post offers -- for a post in parts, the
    part whose issues include the one wanted, from this run's years.

    The post page is what only a browser can load, so the page fetcher reads it; the link
    it carries is then an ordinary URL that redirects to a file host, which
    plain HTTP can fetch (measured 2026-09-15). Taking the first link of a
    post in parts fetched "#1 – 12" for a wanted #21 (The Woods, 2026-09-28),
    and the first part holding a #12 of R.E.B.E.L.S. was the 1994 run's
    (2026-09-29).
    """
    links = direct_site_download_links(solver_fetch_html(post_url), _direct_site_home())
    if not links:
        raise ValueError("That download-site post offers no direct download link")
    wanted = _issue_number(issue_number)
    parts = [link for link in links if link["first"] is not None]
    # No parts, or one part: the post is the download, and the importer
    # judges what it holds. Only a post in several parts has a choice.
    if wanted is None or len({link["label"] for link in parts}) < 2:
        return links[0]["url"]
    holding = [
        link for link in parts
        if link["first"] <= wanted <= (link["last"] if link["last"] is not None else link["first"])
    ]
    for link in holding:
        if _direct_site_part_fits_years(link["label"], years):
            return link["url"]
    if holding:
        labels = list(dict.fromkeys(link["label"] for link in holding))
        raise ValueError(
            f"The part of that download-site post holding #{issue_number} is from other years than this run: "
            + "; ".join(labels[:3])
        )
    labels = list(dict.fromkeys(link["label"] for link in parts))
    raise ValueError(
        f"That download-site post is in parts, and none says it holds #{issue_number}: " + "; ".join(labels[:6])
    )


# A post that collects a name's runs: "R.E.B.E.L.S. Vol. 1 – 2 (Collection)
# (1994-2011)". Its title names no issue; its parts do.
_DIRECT_SITE_COLLECTION = re.compile(
    r"\bcollection\b|\bv(?:ol(?:ume)?)?\.?\s*\d{1,3}\s*-\s*(?:v(?:ol(?:ume)?)?\.?\s*)?\d{1,3}\b", re.I
)
_DIRECT_SITE_PARTS_TTL_SECONDS = 30 * 60
_DIRECT_SITE_PARTS_LOCK = threading.Lock()
_DIRECT_SITE_PARTS: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _direct_site_names_series(title: str, context: dict[str, Any]) -> bool:
    """Whether a post's title begins with the wanted series' words."""
    wanted = _title_words(_without_leading_article(context.get("seriesTitle")))
    words = _title_words(_without_leading_article(_plain_dashes(title)))
    return bool(wanted) and words[:len(wanted)] == wanted


def _direct_site_post_parts(post_url: str) -> list[dict[str, Any]]:
    """A post's parts, read once per half hour: the page fetcher is slow and a
    person searches the same issue more than once."""
    now = time.time()
    with _DIRECT_SITE_PARTS_LOCK:
        cached = _DIRECT_SITE_PARTS.get(post_url)
        if cached and cached[0] > now:
            return cached[1]
    parts = direct_site_download_links(solver_fetch_html(post_url), _direct_site_home())
    with _DIRECT_SITE_PARTS_LOCK:
        for key in [key for key, (expires, _) in _DIRECT_SITE_PARTS.items() if expires <= now]:
            _DIRECT_SITE_PARTS.pop(key, None)
        _DIRECT_SITE_PARTS[post_url] = (now + _DIRECT_SITE_PARTS_TTL_SECONDS, parts)
    return parts


def _direct_site_collection_part(post_url: str, title: str, context: dict[str, Any]) -> dict[str, Any] | None:
    """The part of a collection post that holds this issue, judged as a
    release in its own right, or None.

    R.E.B.E.L.S. #12 was on the download site only inside "R.E.B.E.L.S. Vol. 1 – 2
    (Collection) (1994-2011)", a title that names no issue and so scored
    nothing -- while its part "R.E.B.E.L.S. Vol. 2 #1 – 28 + Annual
    (2009-2011)" is exactly the release wanted (2026-09-29). Reading the post
    is what a person does by hand; it is done only for a post that says it is
    a collection of the wanted series, since each read is a page fetcher round trip.
    """
    if not _DIRECT_SITE_COLLECTION.search(_plain_dashes(title)) or not _direct_site_names_series(title, context):
        return None
    parts = _direct_site_post_parts(post_url)
    years = (context.get("seriesYear"), context.get("publicationYear"))
    seen: set[str] = set()
    for part in parts:
        label = str(part.get("label") or "")
        if part.get("first") is None or label in seen:
            continue
        seen.add(label)
        plain = _plain_dashes(label)
        score, reasons = _release_candidate_score({"title": plain}, context)
        pack = _release_pack_coverage(plain, context)
        if not pack or score < 70 or not _direct_site_part_fits_years(label, years):
            continue
        return {
            "label": label, "score": score, "pack": pack,
            "reasons": reasons + [f"A part of the post {title}"],
            "sizeBytes": int(part.get("sizeBytes") or 0),
        }
    return None


def _direct_site_queries(context: dict[str, Any], query: str) -> list[str]:
    """What to ask the download site for one wanted issue.

    Its feed matches words literally and lists only its dozen newest matches,
    so the series title alone buries an older single issue under recent posts:
    "Farmhand" listed #18-#26 and a #1-20 pack, never "Farmhand #3 (2018)".
    download-site titles a single issue "Series #N", so that is asked first, and
    the title alone still finds the packs. A query someone typed is theirs.
    """
    series = str(context.get("seriesTitle") or "").strip()
    number = re.match(r"^\s*#?\s*0*(\d+)", str(context.get("issueNumber") or ""))
    queries = [str(query or "").strip()]
    if series and queries[0].casefold() == series.casefold():
        # Its search matches each word as a substring of the post's title, so
        # the series is asked plain, with "World's" cut to "World" -- which
        # the post's straight or curly apostrophe both contain.
        plain = _plain_query_title(series, cut_at_apostrophe=True) or series
        queries = [plain]
        if number:
            queries.insert(0, f"{plain} #{number.group(1)}")
        # A pack's post is titled with the run's years -- "The Woods #1 – 36
        # (2014-2018)" -- and the title alone lists only the dozen newest
        # posts that contain the words, which for "The Woods" were a dozen
        # newer comics with "Wood" in them. The year reaches past them.
        year = _provider_year(context.get("seriesYear"))
        if year:
            queries.append(f"{plain} {year}")
    return [text for text in queries if text]


def _direct_site_candidates(
    job_id: int, context: dict[str, Any], query: str, *, set_aside: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Direct-download results for one wanted issue, judged the way any release is.

    Listed, not grabbable: reading the post page a link lives on needs a
    page fetcher, which is its own piece of work. Knowing the issue is
    there is worth having on its own -- it is what a person checks by hand.

    With `set_aside`, a post below the bar that still names the series or the
    issue is appended there, registered so a person can take it by hand --
    only while the page fetcher is ready, since otherwise nothing could be taken.
    """
    found: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for text in _direct_site_queries(context, query):
        try:
            results = direct_site_search(text)
        except ValueError as exc:
            # Not enabled, or the feed was unreadable. Neither is a search failure.
            log_event("direct_site_search_unavailable", level="info", job_id=job_id, reason=str(exc)[:120])
            return []
        except Exception as exc:  # noqa: BLE001 -- Usenet results still stand
            log_event("direct_site_search_failed", level="warning", job_id=job_id, error=str(exc)[:160])
            continue
        for item in results:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                found.append(item)
    try:
        _enabled_acquisition_service("flaresolverr")
        solver_ready = True
    except ValueError:
        solver_ready = False
    now = time.time()
    candidates: list[dict[str, Any]] = []
    reads = 0
    refused: dict[str, str] = {}
    if set_aside is not None:
        try:
            records = catalog_store().rejected_acquisition_releases(job_id)
            for record in records if isinstance(records, list) else []:
                if isinstance(record, dict):
                    reason = (str(record.get("error") or "").strip().splitlines() or ["no reason recorded"])[0]
                    refused[str(record.get("release_key") or "")] = reason
        except Exception:  # noqa: BLE001 -- refusals are context, not the search
            refused = {}
    for item in found:
        title = _plain_dashes(item["title"])
        score, reasons = _release_candidate_score({"title": title}, context)
        pack = _release_pack_coverage(title, context)
        shown = item["title"]
        size = int(item.get("sizeBytes") or 0)
        if item["url"] in refused:
            score, reasons, pack = None, [f"Refused before: {refused[item['url']]}"], None
        if score is None or (score < 85 and not (pack and score >= 70)):
            part = None
            # A collection of the series names its issues in its parts, not
            # its title. Read at most a few posts a search: each is a page fetcher
            # round trip, and without a page fetcher the post cannot be read at all.
            if score is not None and solver_ready and reads < 3:
                try:
                    reads += 1
                    part = _direct_site_collection_part(item["url"], item["title"], context)
                except Exception as exc:  # noqa: BLE001 -- an unreadable post is not a search failure
                    log_event("direct_site_post_unreadable", level="info", job_id=job_id, url=item["url"], error=str(exc)[:160])
            if not part:
                names_it = score is None or score > 0 or _release_issue_matches(title, context.get("issueNumber"))
                if set_aside is not None and solver_ready and names_it:
                    reason = _set_aside_reason(score, reasons)
                    aside_id = hashlib.sha256(f"gc:{job_id}:aside:{item['url']}:{now}".encode()).hexdigest()[:24]
                    with _RELEASE_CANDIDATE_LOCK:
                        _RELEASE_CANDIDATES[aside_id] = {
                            "jobId": int(job_id), "source": "direct_site", "postUrl": item["url"],
                            "title": item["title"], "releaseKey": item["url"],
                            "expiresAt": now + _RELEASE_CANDIDATE_TTL_SECONDS,
                            "setAside": True, "setAsideReason": reason,
                        }
                    set_aside.append({
                        "id": aside_id, "title": item["title"], "score": score, "reasons": reasons,
                        "copies": 1, "setAside": True, "refused": score is None, "reason": reason,
                        "source": "direct_site", "grabbable": True,
                    })
                continue
            shown, score, reasons, pack, size = part["label"], part["score"], part["reasons"], part["pack"], part["sizeBytes"]
        candidate_id = hashlib.sha256(f"gc:{job_id}:{item['url']}:{now}".encode()).hexdigest()[:24]
        with _RELEASE_CANDIDATE_LOCK:
            _RELEASE_CANDIDATES[candidate_id] = {
                "jobId": int(job_id), "source": "direct_site", "postUrl": item["url"],
                "title": shown, "releaseKey": item["url"],
                "expiresAt": now + _RELEASE_CANDIDATE_TTL_SECONDS,
            }
        candidates.append({
            "id": candidate_id, "title": shown, "postTitle": item["title"],
            "indexer": "The download site", "sizeBytes": size,
            "publishDate": item.get("publishDate"), "protocol": "Direct download",
            "matchScore": score, "matchReasons": reasons,
            "matchStrength": (
                "Strong match" if score >= 85
                else f"Pack, #{pack[0]}-#{pack[1]}" if pack else "Possible match"
            ),
            "formatTags": _release_format_tags(shown),
            "source": "direct_site", "postUrl": item["url"],
            # Without a page fetcher this is a sighting rather than an offer: the page
            # the link lives on cannot be read at all.
            "grabbable": solver_ready,
            **({} if solver_ready else {
                "grabHint": "Downloading from this site needs a page fetcher; add one in Settings.",
            }),
            **({"pack": {"first": pack[0], "last": pack[1]}} if pack else {}),
        })
    return candidates


def search_release_candidates(job_id: int, query: str | None = None) -> dict[str, Any]:
    """Every source a person can choose from for one wanted issue.

    The automatic grab still asks Prowlarr alone: what it may take without
    asking is a narrower question than what is worth showing someone.
    """
    try:
        result = search_prowlarr_releases(job_id, query)
    except ValueError as exc:
        # No indexer configured is not a reason to hide what the download site has.
        context = catalog_store().get_acquisition_job_context(job_id)
        result = {
            "job": context, "query": str(query or context.get("seriesTitle") or ""),
            "candidateCount": 0, "candidates": [], "resultCount": 0, "nearMisses": [],
            "detail": str(exc),
        }
    context = result.get("job") or {}
    wanted = str(query or "").strip() or str(context.get("seriesTitle") or "")
    if not wanted:
        return result
    aside: list[dict[str, Any]] = []
    extra = _direct_site_candidates(job_id, context, wanted, set_aside=aside)
    if aside:
        merged = list(result.get("nearMisses") or []) + aside
        merged.sort(key=lambda item: -(item["score"] if item.get("score") is not None else 101))
        result = {**result, "nearMisses": merged[:8]}
    if not extra:
        return result
    candidates = list(result.get("candidates") or []) + extra
    candidates.sort(key=_release_order_key(by_title=True))
    candidates = candidates[:24]
    # The row said how many Prowlarr found; it is now a different number.
    try:
        catalog_store().update_acquisition_job(
            job_id, "queued",
            f"{len(candidates)} release candidate{'s' if len(candidates) != 1 else ''} found",
        )
    except Exception as exc:  # noqa: BLE001 -- the list is the answer, not the row
        log_exception("release_count_not_recorded", exc, level="warning")
    return {**result, "candidates": candidates, "candidateCount": len(candidates)}


def _release_pack_coverage(title: Any, context: dict[str, Any]) -> tuple[int, int] | None:
    """The pack's range, when it says it holds the issue this job wants."""
    wanted = re.match(r"^\s*#?\s*(\d+)", str(context.get("issueNumber") or ""))
    if not wanted:
        return None
    # A manga volume range is the pack; for a comic it is collected editions,
    # which `_release_issue_range` sets aside.
    if context.get("format") == "manga":
        stated = _manga_pack_range(title)
    else:
        stated = _release_issue_range(title, context.get("runIssueCount"))
    if not stated:
        return None
    number = int(wanted.group(1))
    return stated if stated[0] <= number <= stated[1] else None


def _set_aside_reason(score: int | None, reasons: list[str]) -> str:
    """Why a release was set aside, in the words the release list shows
    (mirrored by setAsideReason in pull-list.js): the grab's refusal and the
    row a person is looking at say the same thing."""
    reasons = list(reasons or [])
    if score is None or (score == 0 and reasons):
        return reasons[0] if reasons else "Set aside"
    if not any(re.match(r"^Issue #.+ matches$", reason) for reason in reasons):
        return "Not this issue"
    if not any(reason.startswith("Series title matches") for reason in reasons):
        return "Not this series"
    return f"Too weak a match ({score} of the 85 needed)"


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


class TorrentSubmissionError(SABSubmissionError):
    """qBittorrent could not take a torrent. A download client refusing, like
    SABnzbd refusing: the next release would meet the same answer."""


def source_priority() -> list[str]:
    """Where releases are taken from first, as the admin ordered them."""
    try:
        order = load_app_settings().get("sourcePriority")
    except Exception:  # noqa: BLE001 -- an unreadable setting is the default order
        order = None
    return list(order) if _is_source_order(order) else list(RELEASE_SOURCES)


def _source_rank(source: Any, order: list[str] | None = None) -> int:
    order = order if order is not None else source_priority()
    value = str(source or "usenet")
    return order.index(value) if value in order else len(order)


def _release_order_key(order: list[str] | None = None, *, by_title: bool = False) -> Callable[[dict[str, Any]], tuple]:
    """How releases are ranked: a strong match before a weak one, then the
    admin's order of sources, then match score, the healthiest swarm and the
    larger file. Without `by_title` a full tie keeps the order it came in,
    so re-sorting a ranked list never reshuffles it."""
    order = order if order is not None else source_priority()

    def key(item: dict[str, Any]) -> tuple:
        score = int(item.get("matchScore") or 0)
        ranked = (0 if score >= 85 else 1, _source_rank(item.get("source"), order), -score,
                  -int(item.get("seeders") or 0), -int(item.get("sizeBytes") or 0))
        return (*ranked, str(item.get("title") or "")) if by_title else ranked
    return key


def _release_seeders(release: dict[str, Any]) -> int:
    try:
        return max(0, int(release.get("seeders") or 0))
    except (TypeError, ValueError):
        return 0


def _pack_label(pack: tuple[int, int], context: dict[str, Any]) -> str:
    """ "Pack, #1-#36", or for manga "Pack, Vol. 1-39"."""
    if context.get("format") == "manga":
        return f"Pack, Vol. {pack[0]}-{pack[1]}"
    return f"Pack, #{pack[0]}-#{pack[1]}"


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


# Comics are asked for in Comics and in EBook: a book publisher's graphic
# novels are filed as ebooks -- The Adventure Zone 04 (First Second) is posted
# only there, and was found in Prowlarr by hand while Flipparr, asking Comics
# alone, found nothing (2026-09-30). A novel of the same name is in EBook too,
# so a download whose EPUB is a book of text is refused at import.
COMIC_CATEGORIES = ("7030", "7020")
# Manga is posted in Comics and in EBook --
# and some of it under TV/Anime: Blue Lock v02 and v24 exist only there, and
# every other volume was found, so those two looked unposted. Some torrent
# indexers file English volumes under Books as a whole (7000), not a child of it.
MANGA_CATEGORIES = ("7030", "7020", "5070", "7000")
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
# The bounds of that range, as numbers: "v01-39" is volumes 1 to 39.
_MANGA_RANGE_BOUNDS = re.compile(
    r"(?<![A-Za-z0-9])(?:v|vol(?:ume)?)\.?\s*0*(\d{1,4})\s*[-–~]\s*(?:v|vol(?:ume)?)?\.?\s*0*(\d{1,4})(?![\dA-Za-z])",
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


def _manga_pack_range(title: Any) -> tuple[int, int] | None:
    """The volumes a manga pack says it holds -- "Blue Lock v01-39" is 1 to
    39 -- or None for one volume, a chapter run, or no range at all."""
    match = _MANGA_RANGE_BOUNDS.search(_manga_release_name(title))
    if not match:
        return None
    first, last = int(match.group(1)), int(match.group(2))
    return (first, last) if first < last else None


def _manga_names_series(lead: str, context: dict[str, Any]) -> bool:
    """Whether the text before a volume marker is this series, by equality:
    "One Piece - Heroines v01" is a spin-off, and a title that merely
    contains the series is how specials got through."""
    bare_wanted = normalized_title(_without_leading_article(context.get("seriesTitle")))
    leads = {lead, _strip_manga_publisher(lead, context.get("publisher"))}
    return bool(bare_wanted) and any(
        normalized_title(_without_leading_article(re.sub(r"[-–—:]+", " ", form))) == bare_wanted
        for form in leads
    )


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


def _epub_is_prose(path: Path) -> bool:
    """Whether an EPUB is a book of text rather than comic pages.

    Only text says so: an EPUB built some other way than one picture a page
    is still a comic, just not one that converts.
    """
    try:
        _epub_page_images(path)
    except EbookNotConvertible as exc:
        return str(exc) == "The EPUB is text, not page images"
    except Exception:  # noqa: BLE001 -- unreadable is the health check's to judge
        return False
    return False


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
    wanted = str(context.get("issueNumber") or "").strip()
    score = 0
    reasons: list[str] = []
    # "Blue Lock v01-39" is thirty-nine volumes. It is never this volume --
    # it scores below one -- but it holds it, and for a volume nobody posts on
    # its own, or a run pulled whole, a pack holding it is the answer
    # (owner, 2026-09-30). The torrent client then fetches only the wanted
    # volumes out of it. A chapter run is still not a volume.
    span = _manga_pack_range(name)
    if span:
        if not wanted.isdigit() or not span[0] <= int(wanted) <= span[1]:
            return 0, [f"A pack of volumes {span[0]}-{span[1]}, without volume {wanted}"]
        range_marker = _MANGA_RANGE.search(name)
        if range_marker and _manga_names_series(name[: range_marker.start()], context):
            score += 50
            reasons.append("Series title matches")
        score += 22
        reasons.append(f"Volumes {span[0]}-{span[1]} include {int(wanted)}")
    else:
        # "Chainsaw Man - 13 (cbz)", 974 MB, is thirteen chapters' worth of
        # something, not volume 13.
        if _MANGA_RANGE.search(name) or _MANGA_CHAPTER.search(name):
            return 0, ["A pack, not one volume"]
        volumes = _manga_volumes(name)
        marker = _MANGA_VOLUME.search(name)
        if len(volumes) != 1 or not wanted.isdigit() or not marker:
            return 0, ["No single volume number"]
        if _manga_names_series(name[: marker.start()], context):
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


# "R.E.B.E.L.S.", "S.H.I.E.L.D.": letters that are one word only with their dots.
_DOTTED_ACRONYM = re.compile(r"\b(?:[A-Za-z]\.){2,}")


def _plain_query_title(title: str, *, cut_at_apostrophe: bool = False) -> str:
    """A title as a search wants it: no punctuation. A release drops an
    apostrophe ("World's" -> "Worlds", the way a scene name must), so that is
    what an indexer is asked; a site that matches words as substrings is
    asked for the stem alone ("World", which "World's" and "World’s" both
    contain). A hyphen inside a name stays ("Spider-Man"), and so does a
    dotted acronym: split at its dots "R.E.B.E.L.S." is six letters no search
    can use -- the download site listed nothing for "R E B E L S" and the run's
    collection at once for the dotted name (2026-09-29). Every other mark is
    a space, one space between words."""
    text = str(title or "")
    text = re.sub(r"['’]\w*", "", text) if cut_at_apostrophe else re.sub(r"['’]", "", text)
    text = _DOTTED_ACRONYM.sub(lambda match: match.group(0).replace(".", "\x00"), text)
    text = re.sub(r"[^-\w\s\x00]", " ", text)
    text = re.sub(r"(?<!\w)-|-(?!\w)", " ", text)
    text = text.replace("\x00", ".")
    return re.sub(r"\s+", " ", text).strip()


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
    # A release writes a title without its punctuation: "Batman / Superman:
    # World's Finest" is posted as "Batman-Superman - Worlds Finest", and an
    # indexer given the slash, the colon and the apostrophe found none of it
    # while the same words alone found it twice. An indexer matches words, so
    # the marks never help; only the plain wording is asked (owner, 2026-09-28).
    titles = [_plain_query_title(series) or series]
    for title in list(titles):
        without_article = _without_leading_article(title)
        if without_article and without_article not in titles:
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
            # A book publisher's graphic novels are numbered in two: "The
            # Adventure Zone 04 - The Crystal Kingdom" answered neither "004"
            # nor "4", and was only found by hand (2026-09-30).
            if len(issue.lstrip("0")) == 1:
                forms.append(f"{title} {issue.zfill(2)}")
        elif _issue_key(issue) in ("0.5", "0.25", "0.75"):
            # A half issue: releases write "0.5", older indexes "1/2"; the
            # "½" the catalog keeps is a word no indexer has.
            decimal = _issue_key(issue)
            forms.append(f"{title} {decimal}")
            forms.append(f"{title} {next(text for text, value in _VULGAR_FRACTIONS.items() if value == decimal and '/' in text)}")
        elif re.fullmatch(r"\d+\.\d+", issue):
            # "Green Lantern 023.1", as a release names DC's Villains Month
            # issues; the unpadded form is a different word to an indexer.
            whole, fraction = issue.split(".", 1)
            forms.append(f"{title} {whole.zfill(3)}.{fraction}")
            forms.append(f"{title} {whole.lstrip('0') or '0'}.{fraction}")
        elif re.fullmatch(r"\d+[ .]?[A-Za-z]{1,3}", issue):
            # "Superior Spider-Man 006 AU": the letters are their own word.
            digits, letters = re.fullmatch(r"(\d+)[ .]?([A-Za-z]{1,3})", issue).groups()
            forms.append(f"{title} {digits.zfill(3)} {letters.upper()}")
            forms.append(f"{title} {digits.lstrip('0') or '0'} {letters.upper()}")
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
    prowlarr: dict[str, Any], query: str, categories: tuple[str, ...] = COMIC_CATEGORIES
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
    # Why each was refused, said back in the release list: "refused before"
    # alone left an incomplete release looking like a search that found nothing.
    refusal_reasons: dict[str, str] = {}
    for item in rejected_records:
        if not isinstance(item, dict):
            continue
        reason = (str(item.get("error") or "").strip().splitlines() or ["no reason recorded"])[0]
        refusal_reasons[str(item.get("release_key") or "")] = reason
        refusal_reasons[re.sub(r"\s+", " ", str(item.get("release_title") or "")).strip().casefold()] = reason
    now = time.time()
    # What came back and was set aside. "No release found yet" could mean the
    # indexers have nothing, or that everything they had was refused -- and
    # telling those apart used to take a trip into the container.
    # By release, not by answer: with nothing found every wording is tried, and
    # each brings back the same releases again.
    results_seen: set[str] = set()
    near_misses: list[dict[str, Any]] = []
    # A torrent can be offered either way, but taken only by a client that
    # speaks it. Usenet results stay takeable as they always were.
    try:
        _enabled_acquisition_service("qbittorrent")
        torrents_ready = True
    except ValueError:
        torrents_ready = False

    def candidates_for(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for release in releases:
            if not isinstance(release, dict):
                continue
            protocol = str(release.get("protocol") or "").casefold()
            title = str(release.get("title") or "").strip()
            # Some indexers give a magnet link only, so a torrent with no
            # download link is still one -- through Prowlarr's magnet proxy.
            download_url = str(release.get("downloadUrl") or "").strip() or (
                str(release.get("magnetUrl") or "").strip() if protocol == "torrent" else ""
            )
            if protocol not in ("usenet", "torrent") or not title or not download_url:
                continue
            try:
                download_path = _prowlarr_download_reference(download_url)
            except ValueError:
                continue
            release_key = _release_candidate_key(release, download_path)
            results_seen.add(release_key)
            source = "torrent" if protocol == "torrent" else "usenet"
            seeders = _release_seeders(release) if source == "torrent" else None
            about = {
                "source": source, "seeders": seeders,
                "infoHash": str(release.get("infoHash") or "").strip().casefold() or None,
            }
            normalized_title = re.sub(r"\s+", " ", title).strip().casefold()
            if release_key in rejected_keys or normalized_title in rejected_titles:
                reason = refusal_reasons.get(release_key) or refusal_reasons.get(normalized_title)
                near_misses.append({"title": title, "score": None, "key": release_key, "downloadPath": download_path,
                                    "reasons": [f"Refused before: {reason}" if reason else "Refused before for this issue"],
                                    **about})
                continue
            score, reasons = _release_candidate_score(release, context)
            # A pack scores below an exact single by design, so it would never
            # be offered at all under one threshold. It is listed as what it is
            # and, for an automatic grab, judged against the run separately.
            pack = _release_pack_coverage(title, context)
            if score < 85 and not (pack and score >= 70):
                near_misses.append({"title": title, "score": score, "reasons": reasons, "key": release_key,
                                    "downloadPath": download_path, **about})
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
                    **about,
                    **({"pack": {"first": pack[0], "last": pack[1]}} if pack else {}),
                }
            takeable = source == "usenet" or torrents_ready
            if pack and source == "torrent":
                # Its size is the whole pack; what is fetched is far less.
                reasons = [*reasons, "Only the issues wanted are downloaded from it"]
            candidates.append({
                "id": candidate_id, "title": title,
                "indexer": str(release.get("indexer") or "Unknown indexer"),
                "sizeBytes": int(release.get("size") or 0),
                "publishDate": release.get("publishDate"),
                "protocol": "Torrent" if source == "torrent" else "Usenet",
                "matchScore": score, "matchReasons": reasons,
                "matchStrength": (
                    "Strong match" if score >= 85
                    else _pack_label(pack, context) if pack else "Possible match"
                ),
                "formatTags": _release_format_tags(title),
                "source": source, "grabbable": takeable, "releaseKey": release_key,
                **({"seeders": seeders} if source == "torrent" else {}),
                **({} if takeable else {"grabHint": "Connect qBittorrent in Settings to download torrents."}),
                **({"pack": {"first": pack[0], "last": pack[1]}} if pack else {}),
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
        if isinstance(exc, SERVICE_HICCUPS):
            # Prowlarr did not answer: that says nothing about the issue, so
            # the try does not count towards its backoff. Counted, an outage
            # pushed every issue's next search out by up to a day.
            store.return_unanswered_search(
                job_id, f"Prowlarr did not answer ({exc}); the search will run again when it does")
            service_trouble("prowlarr", exc)
        else:
            store.update_acquisition_job(
                job_id, "queued", f"Search did not finish ({exc}); it will be tried again"
            )
        raise
    service_answered("prowlarr")
    # Strong matches first, in the admin's order of sources.
    candidates.sort(key=_release_order_key(by_title=True))
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
    # One release is often listed by several indexers; it is one thing set
    # aside, with a copy per listing -- not per wording that found it again.
    distinct: dict[str, dict[str, Any]] = {}
    for miss in near_misses:
        title_key = re.sub(r"\s+", " ", miss["title"]).strip().casefold()
        entry = distinct.setdefault(title_key, {**miss, "listings": set()})
        entry["listings"].add(miss["key"])
    ordered = sorted(
        distinct.values(), key=lambda item: -(item["score"] if item["score"] is not None else 101),
    )[:8]
    # What was set aside can still be taken by hand: a person who is looking
    # at the right file under a name the matcher did not read is not sent to
    # a developer (owner, 2026-09-29). Each row is registered like a
    # candidate, marked set aside, so a grab without "anyway" still refuses it.
    set_aside = []
    for entry in ordered:
        reason = _set_aside_reason(entry["score"], entry["reasons"])
        aside_id = hashlib.sha256(f"{job_id}:aside:{entry['key']}:{now}".encode()).hexdigest()[:24]
        source = str(entry.get("source") or "usenet")
        takeable = source == "usenet" or torrents_ready
        with _RELEASE_CANDIDATE_LOCK:
            _RELEASE_CANDIDATES[aside_id] = {
                "jobId": int(job_id), "downloadPath": entry["downloadPath"], "title": entry["title"],
                "releaseKey": entry["key"], "expiresAt": now + _RELEASE_CANDIDATE_TTL_SECONDS,
                "source": source, "seeders": entry.get("seeders"), "infoHash": entry.get("infoHash"),
                "setAside": True, "setAsideReason": reason,
            }
        set_aside.append({
            "id": aside_id, "title": entry["title"], "score": entry["score"], "reasons": entry["reasons"],
            "copies": len(entry["listings"]), "setAside": True, "refused": entry["score"] is None,
            "reason": reason, "source": source, "grabbable": takeable,
            **({"seeders": entry.get("seeders")} if source == "torrent" else {}),
        })
    return {
        "job": context, "query": query, "candidateCount": len(candidates),
        "candidates": candidates,
        "resultCount": len(results_seen), "nearMisses": set_aside,
    }


def _claim_release_candidate(job_id: int, candidate_id: str, *, anyway: bool = False) -> dict[str, Any]:
    """The registered result a grab names, or why it cannot be grabbed.

    A result set aside by the matcher is registered too, so that a person who
    can see it is the right file may take it -- but only by saying so
    (`anyway`): a client holding a stale list cannot grab it blindly.
    """
    now = time.time()
    with _RELEASE_CANDIDATE_LOCK:
        candidate = _RELEASE_CANDIDATES.get(candidate_id)
        if (not candidate or int(candidate.get("jobId") or 0) != int(job_id)
                or float(candidate.get("expiresAt") or 0) <= now):
            _RELEASE_CANDIDATES.pop(candidate_id, None)
            raise ValueError("This release result expired; search again")
    if candidate.get("setAside") and not anyway:
        raise ValueError(
            f"This release was set aside as {candidate.get('setAsideReason') or 'not this issue'}; "
            "take it anyway to grab it"
        )
    return candidate


def _lift_release_refusal(store: Any, job_id: int, release_key: str, title: str) -> None:
    """The person has overruled a refusal of this release for this issue.

    Its kept evidence goes first (the refusal row is what _discard_kept_downloads
    finds it by), then the row itself, by key or title.
    """
    try:
        wanted = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
        kept = store.kept_refused_downloads(job_id=int(job_id))
        for row in kept if isinstance(kept, list) else []:
            if not isinstance(row, dict):
                continue
            if re.sub(r"\s+", " ", str(row.get("release_title") or "")).strip().casefold() == wanted:
                if str(row.get("sab_nzo_id") or "").startswith("torrent:"):
                    # The same torrent is being taken again: it is kept.
                    continue
                _sab_remove_job({"sab_nzo_id": row.get("sab_nzo_id"), "sab_storage": row.get("sab_storage")})
        store.forget_release_refusal(int(job_id), release_key, title)
    except Exception as exc:  # noqa: BLE001 -- the grab is the point; a refusal left behind only sets it aside again
        log_exception("release_refusal_not_lifted", exc, level="warning")


def grab_release_candidate(job_id: int, candidate_id: str, *, anyway: bool = False) -> dict[str, Any]:
    """Take the release a person chose, by whichever road it came down.

    Usenet goes to SABnzbd as it always has. A direct download is Flipparr's
    own job: there is no download client to hand it to. `anyway` is the
    person taking a set-aside release on their own word.
    """
    with _RELEASE_CANDIDATE_LOCK:
        candidate = _RELEASE_CANDIDATES.get(candidate_id)
        source = str((candidate or {}).get("source") or "usenet")
    # Named only when meant: the automatic grabs call this too, and their
    # calls read exactly as before.
    by_hand = {"anyway": True} if anyway else {}
    if candidate and source == "direct_site":
        return grab_direct_site_release(job_id, candidate_id, **by_hand)
    if candidate and source == "torrent":
        return send_release_to_qbittorrent(job_id, candidate_id, **by_hand)
    return send_release_to_sabnzbd(job_id, candidate_id, **by_hand)


def _fetch_selected_torrent(candidate: dict[str, Any]) -> tuple[bytes | None, str | None]:
    """The torrent Prowlarr's link stands for: a .torrent file, or a magnet
    link -- Prowlarr answers a magnet with a redirect to it, which is read
    rather than followed. A redirect to an ordinary address is fetched once,
    without Prowlarr's key."""
    prowlarr = _enabled_acquisition_service("prowlarr")
    reference = urllib.parse.urlsplit(str(candidate.get("downloadPath") or ""))
    base = urllib.parse.urlsplit(str(prowlarr["url"]))
    endpoint = urllib.parse.urlunsplit((base.scheme, base.netloc, reference.path, reference.query, ""))
    request = urllib.request.Request(endpoint, headers={
        "Accept": "application/x-bittorrent, */*;q=0.1", "X-Api-Key": str(prowlarr["apiKey"]),
        "User-Agent": f"Flipparr/{APP_VERSION}",
    })
    try:
        with urllib.request.build_opener(_ReportRedirect).open(request, timeout=45.0) as response:
            payload = response.read(NZB_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in _UPLOAD_REDIRECT_CODES:
            location = str(exc.headers.get("Location") or "").strip()
            if location.casefold().startswith("magnet:"):
                return None, location
            if urllib.parse.urlsplit(location).scheme in ("http", "https"):
                try:
                    payload = fetch_bytes_with_headers(
                        location, {"Accept": "application/x-bittorrent, */*;q=0.1",
                                   "User-Agent": f"Flipparr/{APP_VERSION}"},
                        timeout=45.0, max_bytes=NZB_MAX_BYTES,
                    )
                except Exception:  # noqa: BLE001 -- said as one failure below
                    raise ReleaseDownloadError("The indexer's torrent link did not answer") from None
            else:
                raise ReleaseDownloadError("Prowlarr's torrent link leads nowhere Flipparr can follow") from None
        else:
            raise ReleaseDownloadError(
                "Prowlarr rejected the torrent request" if exc.code in {401, 403}
                else f"Prowlarr could not fetch the torrent ({exc.code})"
            ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ReleaseDownloadError("Flipparr could not retrieve the torrent from Prowlarr") from None
    if len(payload) > NZB_MAX_BYTES:
        raise ReleaseDownloadError("Prowlarr's answer is too large to be a torrent file")
    text = payload.strip()
    if text[:7].lower() == b"magnet:":
        return None, text.decode("utf-8", "replace")
    if not torrent_client.looks_like_torrent(payload):
        raise ReleaseDownloadError("Prowlarr returned something that is not a torrent")
    return payload, None


def send_release_to_qbittorrent(job_id: int, candidate_id: str, *, anyway: bool = False) -> dict[str, Any]:
    """Hand a torrent to qBittorrent, to wait for its file list.

    It is added to stop as soon as its files are known, so that only the ones
    wanted are fetched: one volume out of "Blue Lock v01-39" is 400 MB, not
    16 GB. The choosing happens when the import worker next looks at it
    (`_choose_torrent_files`). A torrent qBittorrent already has -- a pack
    another issue took -- is joined rather than added again.
    """
    candidate = _claim_release_candidate(job_id, candidate_id, anyway=anyway)
    if str(candidate.get("source") or "") != "torrent":
        raise ValueError("That result is not a torrent, so qBittorrent cannot take it")
    client = _qbittorrent_client()
    title = str(candidate.get("title") or "Torrent")
    release_key = str(candidate.get("releaseKey") or "").strip() or hashlib.sha256(
        f"candidate|{title}|{candidate.get('downloadPath')}".encode()
    ).hexdigest()
    config = _enabled_acquisition_service("qbittorrent")
    tags = ["flipparr", f"job-{int(job_id)}"]
    try:
        info_hash_ = str(candidate.get("infoHash") or "").strip().casefold() or None
        present = client.info(hashes=[info_hash_]) if info_hash_ else []
        torrent = magnet = None
        if not present:
            torrent, magnet = _fetch_selected_torrent(candidate)
            if not info_hash_:
                info_hash_ = torrent_client.info_hash(torrent) if torrent else torrent_client.magnet_hash(magnet)
            if not info_hash_:
                raise ReleaseDownloadError("Prowlarr's link names no torrent Flipparr can follow")
            present = client.info(hashes=[info_hash_])
        if present:
            client.add_tags([info_hash_], tags)
        else:
            selective = client.supports_selection()
            if candidate.get("pack") and not selective:
                raise TorrentSubmissionError(
                    f"qBittorrent {client.version()} would download the whole pack; "
                    "4.5.5 or later downloads only the issues wanted"
                )
            client.add_torrent(
                torrent=torrent, magnet=magnet, category=str(config.get("category") or "comics"),
                tags=tags, name=title, wait_for_files=selective,
            )
    except torrent_client.TorrentClientError as exc:
        raise TorrentSubmissionError(str(exc)) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TorrentSubmissionError(f"Flipparr could not reach qBittorrent: {exc}") from None
    store = catalog_store()
    if anyway:
        _lift_release_refusal(store, job_id, release_key, title)
    store.record_acquisition_download(
        job_id, f"torrent:{info_hash_}:{int(job_id)}", title, release_key, source="qbittorrent",
        **({"taken_by_hand": True} if anyway else {}),
    )
    job = store.update_acquisition_job(
        job_id, "grabbed", f"Taken by hand: {title}" if anyway else f"Sent to qBittorrent: {title}",
    )
    with _RELEASE_CANDIDATE_LOCK:
        _RELEASE_CANDIDATES.pop(candidate_id, None)
    return {"status": "grabbed", "source": "qbittorrent", "job": job, "release": {"title": title},
            "detail": "Release sent to qBittorrent.", "takenByHand": bool(anyway)}


def grab_direct_site_release(job_id: int, candidate_id: str, *, anyway: bool = False) -> dict[str, Any]:
    """Start fetching a download-site post, and let the row show it happening.

    The bytes are fetched on a thread of their own: a comic is tens or hundreds
    of megabytes, and the page that asked for it should not wait for them. The
    import worker picks the download up when it lands, exactly as it does for
    anything SABnzbd finishes.
    """
    candidate = _claim_release_candidate(job_id, candidate_id, anyway=anyway)
    _enabled_acquisition_service("flaresolverr")
    post_url = str(candidate.get("postUrl") or "")
    title = str(candidate.get("title") or "download-site download")
    store = catalog_store()
    if anyway:
        _lift_release_refusal(store, job_id, post_url, title)
    download = store.record_acquisition_download(
        # Per job as well as per post: a post in parts serves one job with
        # "#13 – 23" and another with "#34 – 36", and the download identity
        # is unique across the table.
        job_id, f"direct_site:{hashlib.sha256(post_url.encode()).hexdigest()[:20]}:{int(job_id)}",
        title, release_key=post_url, source="direct_site",
        **({"taken_by_hand": True} if anyway else {}),
    )
    store.update_acquisition_job(
        job_id, "grabbed", f"Taken by hand: {title}" if anyway else f"Downloading from the download site: {title}",
    )
    _start_direct_fetch(int(download["id"]), int(job_id), post_url, title)
    return {"status": "grabbed", "source": "direct_site", "release": {"title": title}, "download": download,
            "takenByHand": bool(anyway)}


class DirectSiteNotAFile(ValueError):
    """The post's link answered with a web page -- a file host's share page -- not a comic."""


class DownloadStopped(Exception):
    """A person stopped this download while it was being fetched."""


# The downloads Flipparr fetches itself, by download id, each with the flag
# that stops it. A row that says "downloading" with nothing here is orphaned
# -- a restart took its thread -- and is started again, once.
_DIRECT_FETCHES: dict[int, threading.Event] = {}
_DIRECT_FETCHES_LOCK = threading.Lock()
_DIRECT_FETCHES_RESTARTED: set[int] = set()


def _start_direct_fetch(download_id: int, job_id: int, post_url: str, title: str) -> None:
    stop = threading.Event()
    with _DIRECT_FETCHES_LOCK:
        _DIRECT_FETCHES[int(download_id)] = stop
    threading.Thread(
        target=_fetch_direct_site_download, args=(int(download_id), int(job_id), post_url, title),
        name=f"flipparr-direct_site-{job_id}", daemon=True,
    ).start()


def recover_interrupted_direct_downloads() -> int:
    """Start again every download Flipparr was fetching itself when it last
    stopped. World's Finest #36 sat at 'Downloading' for good
    after a restart: the thread was gone and the row said nothing else."""
    started = 0
    for row in catalog_store().direct_downloads_in_flight():
        download_id = int(row["id"])
        with _DIRECT_FETCHES_LOCK:
            live = download_id in _DIRECT_FETCHES
            if not live:
                _DIRECT_FETCHES_RESTARTED.add(download_id)
        if live or not row.get("release_key"):
            continue
        _start_direct_fetch(download_id, int(row["job_id"]), str(row["release_key"]), str(row["release_title"] or "download-site download"))
        started += 1
    return started


def stop_acquisition_download(job_id: int, *, research: bool = True, release_torrent: bool = True) -> dict[str, Any]:
    """Stop what is downloading for an issue, whoever is fetching it, and put
    the issue back to wanting a release. The stopped release is set aside for
    a day, and the issue is looked for again at once (`research`)."""
    store = catalog_store()
    download = store.acquisition_download_for_job(job_id)
    if not download or download.get("status") in ("imported", "failed"):
        raise ValueError("Nothing is downloading for this issue")
    result = store.stop_acquisition_download(job_id)
    if str(download.get("source") or "sabnzbd") == "sabnzbd":
        try:
            _sab_remove_job(download)
        except Exception as exc:  # noqa: BLE001 -- SABnzbd keeping the files is not a reason to fail the stop
            log_event("sab_stop_failed", level="info", job_id=job_id, error=str(exc)[:160])
    elif download.get("source") == "qbittorrent":
        if release_torrent:
            _stop_torrent_for_job(store, download)
    else:
        with _DIRECT_FETCHES_LOCK:
            flag = _DIRECT_FETCHES.get(int(download["id"]))
        if flag is not None:
            flag.set()
    try:
        store.record_acquisition_release_failure(
            job_id, str(download.get("release_key") or ""), str(download.get("release_title") or ""),
            "Stopped by you", kind="lost",
        )
    except ValueError:
        pass
    log_event("download_stopped", job_id=job_id, release=str(download.get("release_title") or "")[:200])
    if research:
        _search_again_after_stop([int(job_id)])
    return result


def stop_acquisition_pack(job_id: int) -> dict[str, Any]:
    """Stop a torrent pack as one: every issue still coming in it goes back
    to wanting a release, the pack is set aside for each of them, and the
    torrent goes from qBittorrent with what it had fetched. Then each is
    looked for again at once -- another run pack first, else singles.

    Stopping one issue of a pack only leaves its file out, so a slow pack of
    a long run meant stopping every issue in turn (owner, 2026-10-01).
    """
    store = catalog_store()
    download = store.acquisition_download_for_job(job_id)
    if not download or download.get("status") in ("imported", "failed"):
        raise ValueError("Nothing is downloading for this issue")
    info_hash_ = _torrent_hash(download)
    if download.get("source") != "qbittorrent" or not info_hash_:
        # Usenet and download-site packs are one download already.
        return {**stop_acquisition_download(job_id), "stopped": [str(job_id)]}
    job_ids = store.torrent_jobs(info_hash_)
    stopped: list[str] = []
    for member in job_ids:
        try:
            stop_acquisition_download(member, research=False, release_torrent=False)
            stopped.append(str(member))
        except ValueError:
            continue
    _release_torrent(store, info_hash_)
    log_event("torrent_pack_stopped", info_hash=info_hash_, issues=len(stopped),
              release=str(download.get("release_title") or "")[:200])
    _search_again_after_stop([int(member) for member in stopped])
    return {"id": str(job_id), "status": "queued", "stopped": stopped,
            "detail": f"Stopped {len(stopped)} issue{'' if len(stopped) == 1 else 's'}; looking for another release"}


def _search_again_after_stop(job_ids: list[int]) -> None:
    """What a person stopped is looked for again now, not at the next sweep:
    they stopped it to get it another way. A run's issues are asked about
    together, so another run pack can answer them all; the stopped release
    is set aside, so it is not taken again."""
    by_request: dict[int, list[int]] = {}
    for job_id in job_ids:
        try:
            context = catalog_store().get_acquisition_job_context(int(job_id))
            request_id = int(str(context.get("requestId") or "").strip() or 0)
        except Exception:  # noqa: BLE001 -- the scheduled sweep is still there
            continue
        if request_id:
            by_request.setdefault(request_id, []).append(int(job_id))
    for request_id, members in by_request.items():
        if not _acquisition_services_ready(request_id):
            continue
        threading.Thread(
            target=_automatic_release_grabs, args=(request_id,),
            # One issue is one question; a pack's issues are a run's.
            kwargs={"skip_run_pack": len(members) < 2},
            name=f"flipparr-after-stop-{request_id}", daemon=True,
        ).start()


def _stop_torrent_for_job(store: Any, download: dict[str, Any]) -> None:
    """A stopped issue's torrent goes when nothing else uses it; while other
    issues are still coming in it, only this issue's file is left out."""
    info_hash_ = _torrent_hash(download)
    if not info_hash_ or _release_torrent(store, info_hash_):
        return
    try:
        client = _qbittorrent_client()
        files = _torrent_comics(client.files(info_hash_))
        context = store.get_acquisition_job_context(int(download["job_id"]))
        mine = _torrent_file_matching(files, context)
        if mine is not None and len(files) > 1:
            client.set_file_priority(info_hash_, [int(mine["index"])], 0)
    except Exception as exc:  # noqa: BLE001 -- the stop itself stands
        log_event("torrent_stop_failed", level="info", job_id=int(download["job_id"]), error=str(exc)[:160])


def _looks_like_a_page(content_type: str, first_bytes: bytes) -> bool:
    if content_type.split(";")[0].strip().lower() in {"text/html", "application/xhtml+xml"}:
        return True
    head = first_bytes.lstrip()[:15].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _fetch_direct_site_download(download_id: int, job_id: int, post_url: str, title: str) -> None:
    """Resolve a post's link and stream the file into staging.

    Only the page needs the page fetcher. The link it carries redirects to a plain
    file host that answers an ordinary request, so the transfer itself is
    normal HTTP and its progress is real rather than a spinner.
    """
    store = catalog_store()
    folder = acquisition_staging_dir("direct_site") / str(job_id)
    with _DIRECT_FETCHES_LOCK:
        stop = _DIRECT_FETCHES.setdefault(int(download_id), threading.Event())
    try:
        store.update_acquisition_download(download_id, "downloading")
        # The wanted issue, and the run's years, decide which part of a post
        # in parts is taken.
        try:
            context = store.get_acquisition_job_context(int(job_id))
            wanted = context.get("issueNumber")
            years = (context.get("seriesYear"), context.get("publicationYear"))
        except Exception:  # noqa: BLE001 -- a job with no context still gets the post's first link
            wanted, years = None, None
        try:
            # Taken by hand: the person's word covers the years; the number still picks the part.
            row = store.acquisition_download_for_job(int(job_id))
            if isinstance(row, dict) and row.get("taken_by_hand"):
                years = None
        except Exception:  # noqa: BLE001 -- no row to read is no reason not to fetch
            pass
        link = direct_site_download_link(post_url, wanted, years)
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(link, headers={"User-Agent": f"Flipparr/{APP_VERSION}"})
        with _safe_urlopen(request, timeout=120.0) as response:
            name = _download_filename(response, title)
            total = int(response.headers.get("Content-Length") or 0)
            destination = folder / name
            fetched = 0
            marker = 0
            content_type = str(response.headers.get("Content-Type") or "")
            first = True
            with destination.open("xb") as handle:
                while True:
                    if stop.is_set():
                        raise DownloadStopped()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    # Some posts link to a share page on a file host (WeTransfer,
                    # say) rather than the file. Saved as a .cbz, the page came
                    # back from the importer as a corrupt archive, and the same
                    # page was fetched again on the next try.
                    if first and _looks_like_a_page(content_type, chunk):
                        host = urllib.parse.urlsplit(str(response.geturl() or link)).hostname or "another site"
                        raise DirectSiteNotAFile(
                            f"the download site's link for this release opens a page on {host}, not a file, "
                            "so Flipparr can't fetch it. Download it there and use Upload a file."
                        )
                    first = False
                    fetched += len(chunk)
                    if fetched > DIRECT_SITE_DOWNLOAD_MAX_BYTES:
                        raise ValueError("The download is larger than Flipparr will fetch in one go")
                    handle.write(chunk)
                    # Often enough to watch, seldom enough not to write a row
                    # per megabyte of a two-gigabyte pack.
                    if fetched - marker >= 8 * 1024 * 1024:
                        marker = fetched
                        store.update_download_progress(download_id, fetched, total)
        store.update_download_progress(download_id, fetched, total or fetched)
        store.update_acquisition_download(download_id, "completed", sab_storage=str(folder))
        log_event("direct_site_download_complete", job_id=job_id, file=name, bytes=fetched)
    except DownloadStopped:
        # The row and the job were already put right by whoever stopped it.
        shutil.rmtree(folder, ignore_errors=True)
        return
    except Exception as exc:  # noqa: BLE001 -- every failure belongs on the row
        message = f"download-site download failed: {exc}"
        log_event("direct_site_download_failed", level="warning", job_id=job_id, error=str(exc)[:200])
        shutil.rmtree(folder, ignore_errors=True)
        try:
            store.update_acquisition_download(
                download_id, "failed", error=message, failure_stage="download",
            )
            # The same link will lead to the same page, so a post that is not
            # a file is set aside for good. Anything else -- a timeout, a
            # link gone, a file too large -- is set aside for a day (`lost`),
            # or the run-pack pass would pick the same post straight back up.
            store.record_acquisition_release_failure(
                job_id, post_url, title, str(exc),
                kind="not_a_file" if isinstance(exc, DirectSiteNotAFile) else "lost",
            )
            # Wanted again rather than failed outright: another release, or the
            # same one later, is a normal way out of this.
            store.update_acquisition_job(job_id, "queued", message)
        except Exception as inner:  # noqa: BLE001
            log_exception("direct_site_failure_not_recorded", inner, level="warning")
        _resume_run_after_pack(job_id, title)
    finally:
        with _DIRECT_FETCHES_LOCK:
            _DIRECT_FETCHES.pop(int(download_id), None)


def _download_filename(response: Any, fallback: str) -> str:
    """What to call the file, from the server rather than from us.

    The redirect lands on the real name -- "Supergirl - Woman of Tomorrow 08
    (2022).cbz" -- and that name is what the identity check reads.
    """
    disposition = str(response.headers.get("Content-Disposition") or "")
    stated = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
    name = stated.group(1) if stated else Path(urllib.parse.unquote(
        urllib.parse.urlsplit(response.geturl()).path
    )).name
    name = _safe_path_component(urllib.parse.unquote(name), "")
    if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
        return name
    return _safe_path_component(f"{fallback}.cbz", "download.cbz")


def send_release_to_sabnzbd(job_id: int, candidate_id: str, *, anyway: bool = False) -> dict[str, Any]:
    """Fetch a selected NZB privately, validate it, and upload the file to SABnzbd."""
    candidate = _claim_release_candidate(job_id, candidate_id, anyway=anyway)
    if str(candidate.get("source") or "usenet") != "usenet":
        # SABnzbd takes NZBs. A direct download has its own road, and
        # grab_release_candidate is the fork that chooses between them.
        raise ValueError("That result is not a Usenet release, so SABnzbd cannot take it")
    nzb_payload = _fetch_selected_nzb(candidate)
    sab = _enabled_acquisition_service("sabnzbd")
    payload = _submit_nzb_to_sabnzbd(sab, str(candidate["title"]), nzb_payload)
    queue_ids = [str(value) for value in (payload.get("nzo_ids") or [])]
    if not queue_ids:
        raise SABSubmissionError("SABnzbd accepted the NZB but did not return a queue ID")
    detail = f"Taken by hand: {candidate['title']}" if anyway else f"Sent to SABnzbd: {candidate['title']}"
    store = catalog_store()
    release_key = str(candidate.get("releaseKey") or "").strip()
    if not release_key:
        release_key = hashlib.sha256(
            f"candidate|{candidate.get('title')}|{candidate.get('downloadPath')}".encode()
        ).hexdigest()
    if anyway:
        _lift_release_refusal(store, job_id, release_key, str(candidate["title"]))
    store.record_acquisition_download(
        job_id, queue_ids[0], candidate["title"], release_key,
        **({"taken_by_hand": True} if anyway else {}),
    )
    job = store.update_acquisition_job(job_id, "grabbed", detail)
    with _RELEASE_CANDIDATE_LOCK:
        _RELEASE_CANDIDATES.pop(candidate_id, None)
    return {"status": "grabbed", "job": job, "queueIds": queue_ids,
            "detail": "Release sent to SABnzbd.", "takenByHand": bool(anyway)}


def _safe_path_component(value: str, fallback: str = "Comic") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or fallback)[:180].rstrip(" .")


# Publishers' half issues: Metron writes "½", a release "0.5" or ".5", an
# older index "1/2". One number, four spellings.
_VULGAR_FRACTIONS = {"½": "0.5", "¼": "0.25", "¾": "0.75", "1/2": "0.5", "1/4": "0.25", "3/4": "0.75"}


def _issue_key(value: Any) -> str:
    """Compare issue numbers without arguing about how they are written:
    "01", "001" and "1" are one comic, as are "½", "0.5" and ".5", and
    "023.2" is 23.2. Providers, releases and files each spell them their
    own way, and the shelf, the search and the import all line them up."""
    text = str(value or "").strip().casefold().lstrip("#").strip()
    text = _VULGAR_FRACTIONS.get(text, text)
    if re.fullmatch(r"\.\d+", text):
        text = "0" + text
    # Marvel's lettered tie-ins: "6AU" in the catalog, "006 AU" on a release.
    text = re.sub(r"^(\d+(?:\.\d+)?)[ .]?([a-z]{1,3})$", r"\1\2", text)
    if re.fullmatch(r"\d+(?:\.\d+)?[a-z]{0,3}", text):
        text = re.sub(r"^0+(?=\d)", "", text)
    return text


def _issue_number_pattern(value: Any) -> str:
    """A regex for this issue's number as a release or file might write it,
    without the trailing tag check. "23" is also "#23" and "023"; a half is
    "0.5", ".5", "½" or "1/2"."""
    key = _issue_key(value)
    if key in ("0.5", "0.25", "0.75"):
        fraction = re.escape(key[1:])
        glyph = next(glyph for glyph, decimal in _VULGAR_FRACTIONS.items() if decimal == key and len(glyph) == 1)
        vulgar = next(text for text, decimal in _VULGAR_FRACTIONS.items() if decimal == key and "/" in text)
        return rf"(?:(?:#|\b0*)0{fraction}|(?<![\d.]){fraction}|{glyph}|(?<!\d){re.escape(vulgar)}(?!\d))"
    lettered = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]{1,3})", key)
    if lettered:
        return rf"(?:#|\b0*){re.escape(lettered.group(1))}[ .]?{re.escape(lettered.group(2))}"
    return rf"(?:#|\b0*){re.escape(key)}"


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
        reason = health.get("message")
        missing = _zero_filled_megabytes(path)
        if missing:
            reason = (f"{reason} {missing} MB of it is zero-filled: data missing from the "
                      "download itself, which downloading it again cannot fill")
        return -1000, {"path": path, "result": result, "reason": reason, "damaged": bool(missing)}
    locked = archive_is_locked(path)
    if locked:
        return -1000, {"path": path, "result": result, "reason": locked, "locked": True}
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
    than about this machine.

    `kind` says how much it proves. "contradiction", "language" and "format"
    mean the file is not this issue, and its release is barred. "unidentified"
    and "unreadable" mean only that this machine could not confirm it, so the
    release is set aside for a day rather than for good.
    """

    UNPROVEN = frozenset({"unidentified", "unreadable"})

    def __init__(self, message: str, kind: str = "contradiction") -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def stage(self) -> str:
        """The download's failure_stage: a wrong comic, an incomplete one, or one not confirmed."""
        if self.kind == "damaged":
            return "damaged"
        return "unidentified" if self.kind in self.UNPROVEN else "content"


def _zero_filled_megabytes(path: Path) -> int:
    """How many whole megabytes of a file are zeros: the holes SABnzbd leaves
    where a release's Usenet articles never arrived and there was nothing to
    repair them from. A real comic archive has none."""
    block = 1 << 20
    missing = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(block):
                if len(chunk) == block and not chunk.strip(b"\x00"):
                    missing += 1
    except OSError:
        return 0
    return missing


# ---- Packs ------------------------------------------------------------------
#
# A pack is several comics zipped into one file -- the download site's "Batman Beyond
# 2.0 #1 - 40 + TPBs" arrived as one 479 MB ".cbz" holding twenty .cbr issues
# and not one page, and was refused as unreadable while the issue it was
# grabbed for sat inside it. A pack is opened: only the files whose names say
# they are the wanted issue are copied out, and each is then judged like any
# downloaded comic. The rest of the pack is left where it is and goes with the
# download. A pack without the issue is refused as not holding it -- for this
# issue only; another issue may well be in it.

COMIC_PACK_MEMBER_TYPES = frozenset({".cbr", ".cbz", ".cb7", ".cbt", ".pdf"})
PACK_MEMBER_MAX_BYTES = 2 * 1024 * 1024 * 1024
_PAGE_IMAGE_TYPES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".jxl", ".bmp", ".tif", ".tiff"})


def _comic_pack_members(path: Path) -> list[zipfile.ZipInfo] | None:
    """The comics inside a file that is a pack of them, or None when it is a comic
    (it has pages of its own) or not a zip at all."""
    if path.suffix.lower() not in {".cbz", ".cbr", ".zip"} or archive_kind(path) != "zip":
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    if any(Path(entry.filename).suffix.lower() in _PAGE_IMAGE_TYPES for entry in entries):
        return None
    members = [entry for entry in entries if Path(entry.filename).suffix.lower() in COMIC_PACK_MEMBER_TYPES]
    return members or None


def _pack_member_matches(name: str, context: dict[str, Any], *, vouched: bool = False) -> bool:
    """Whether a pack's file names the wanted issue of the wanted series --
    or, for a pack the person chose, the wanted issue at all."""
    parsed = parse_filename(Path(name))
    number = parsed.issue
    if context.get("format") == "manga" and not number:
        number = _manga_file_volume(name)
    if not number or _issue_key(number) != _issue_key(context.get("issueNumber")):
        return False
    if vouched:
        return True
    wanted = normalized_title(_without_leading_article(context.get("seriesTitle") or ""))
    title = normalized_title(_without_leading_article(parsed.title or ""))
    return bool(wanted) and difflib.SequenceMatcher(None, title, wanted).ratio() >= 0.55


def _pack_holdings(members: list[zipfile.ZipInfo]) -> str:
    """What a pack holds, in a few words: "#1-#20", or its size when unreadable."""
    numbers = sorted(
        number for number in (_issue_number(parse_filename(Path(entry.filename)).issue) for entry in members)
        if number is not None
    )
    if numbers:
        return f"#{numbers[0]}" if numbers[0] == numbers[-1] else f"#{numbers[0]}-#{numbers[-1]}"
    return f"{len(members)} files"


def _unpack_comic_packs(
    candidates: list[Path], context: dict[str, Any], *, vouched: bool = False,
) -> tuple[list[Path], Path | None]:
    """The candidates with every pack replaced by the wanted issue's file from
    inside it, and the folder those files were copied to (None if none were)."""
    kept: list[Path] = []
    unpacked_dir: Path | None = None
    missing: list[str] = []
    for path in candidates:
        members = _comic_pack_members(path)
        if not members:
            kept.append(path)
            continue
        wanted = [entry for entry in members
                  if _pack_member_matches(Path(entry.filename).name, context, vouched=vouched)
                  and entry.file_size <= PACK_MEMBER_MAX_BYTES]
        if not wanted:
            held = f"{path.name} is a pack of {len(members)} comics ({_pack_holdings(members)})"
            if vouched:
                # The person chose the pack; they are told what is in it.
                names = [Path(entry.filename).name for entry in members]
                held += "; it holds: " + ", ".join(names[:12]) + (f", and {len(names) - 12} more" if len(names) > 12 else "")
            missing.append(held)
            continue
        unpacked_dir = unpacked_dir or Path(tempfile.mkdtemp(prefix="flipparr-pack-"))
        with zipfile.ZipFile(path) as archive:
            for entry in wanted:
                # Its own name only: a path inside a zip is never a path here.
                target = unpacked_dir / _safe_path_component(Path(entry.filename).name, "issue.cbz")
                with archive.open(entry) as inside, target.open("wb") as outside:
                    shutil.copyfileobj(inside, outside, 1024 * 1024)
                kept.append(target)
        log_event("comic_pack_unpacked", pack=path.name, members=len(members), taken=len(wanted))
    if not kept and missing:
        wanted_label = f"{context.get('seriesTitle')} {_number_label(context)}"
        raise DownloadContentMismatch(
            f"The download is a pack, and {wanted_label} is not in it: " + "; ".join(missing),
            # A pack the person chose proves nothing against the release.
            kind="unidentified" if vouched else "contradiction",
        )
    return kept, unpacked_dir


def select_downloaded_comic(
    source: Path, context: dict[str, Any], completed_root: Path = SAB_COMPLETE_ROOT,
    *, release_title: str | None = None, vouched: bool = False,
) -> dict[str, Any]:
    """The comic in a download that is the issue wanted.

    `vouched` is a release the person took by hand from the set-aside list:
    what it and its files call themselves is then not judged (see
    _choose_downloaded_comic). It is a keyword rather than part of `context`
    because the same download is offered to a run's other wanted issues
    (_sweep_download_for_other_issues) on the matcher's judgment alone.
    """
    candidates = _comic_files_under(source, completed_root)
    if not candidates:
        raise DownloadContentMismatch(
            "The completed download does not contain a supported comic file", kind="format",
        )
    candidates, unpacked_dir = _unpack_comic_packs(candidates, context, vouched=vouched)
    try:
        selected = _select_downloaded_comic(candidates, source, context, release_title, vouched=vouched)
    except Exception:
        if unpacked_dir is not None:
            shutil.rmtree(unpacked_dir, ignore_errors=True)
        raise
    return {**selected, "unpackedDir": str(unpacked_dir)} if unpacked_dir is not None else selected


def _select_downloaded_comic(
    candidates: list[Path], source: Path, context: dict[str, Any], release_title: str | None,
    *, vouched: bool = False,
) -> dict[str, Any]:
    converted_dir: Path | None = None
    if context.get("format") == "manga":
        archives = [path for path in candidates if path.suffix.lower() in MANGA_FILE_TYPES]
        if not archives:
            ebooks = [path for path in candidates if path.suffix.lower() in MANGA_EBOOK_TYPES]
            if not ebooks:
                raise DownloadContentMismatch(
                    "The download has no CBZ, CBR, PDF or EPUB volume", kind="format",
                )
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
                        f"{exc}, so this {ebook.suffix.lstrip('.').upper()} cannot become a CBZ volume",
                        kind="format",
                    ) from exc
                archives.append(target)
        candidates = archives
    else:
        prose = [path for path in candidates if path.suffix.lower() == ".epub" and _epub_is_prose(path)]
        if prose:
            candidates = [path for path in candidates if path not in prose]
            if not candidates:
                raise DownloadContentMismatch(
                    "The EPUB is a book of text, not comic pages", kind="format",
                )
    try:
        selected = _choose_downloaded_comic(candidates, source, context, release_title, vouched=vouched)
    except Exception:
        if converted_dir is not None:
            shutil.rmtree(converted_dir, ignore_errors=True)
        raise
    if converted_dir is not None:
        selected = {**selected, "converted": True, "convertedDir": str(converted_dir)}
    elif context.get("format") != "manga" and Path(selected["path"]).suffix.lower() == ".pdf":
        selected = _comic_pdf_as_cbz(selected, context)
    return selected


def _comic_pdf_as_cbz(selected: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """A comic that arrived as a PDF, as the CBZ the library keeps.

    Only the release already chosen is converted, not every PDF in the
    download. Unlike manga, a PDF that is not one image per page is filed as
    it came rather than refused: it is still the issue that was asked for.
    """
    source = Path(selected["path"])
    converted_dir = Path(tempfile.mkdtemp(prefix="flipparr-comic-"))
    target = converted_dir / f"{_safe_path_component(source.stem, 'issue')}.cbz"
    try:
        convert_ebook_to_cbz(source, target)
        _score, rescored = _download_candidate_score(target, context)
    except EbookNotConvertible as exc:
        shutil.rmtree(converted_dir, ignore_errors=True)
        log_event("comic_pdf_kept", file=source.name, reason=str(exc))
        return selected
    return {**selected, **rescored, "converted": True, "convertedDir": str(converted_dir)}


def _choose_downloaded_comic(
    candidates: list[Path], source: Path, context: dict[str, Any],
    release_title: str | None = None, *, vouched: bool = False,
) -> dict[str, Any]:
    """The comic in a finished download that is the issue it was grabbed for.

    A file that names the issue is taken on its own word. One that names
    nothing the parser can read is taken on the release's word, when that
    release matched this issue and the download holds no other readable comic
    that could be it. Only a file that says it is something else is refused
    as the wrong comic; one that cannot be read or identified is refused as
    unproven, which does not bar its release.

    A release the person took by hand (`vouched`) is on their word: what it
    or its file calls itself, its year and its language are not judged
    (owner, 2026-09-29: "your word wins on names"). The file still has to
    be a readable comic, and a download holding several comics still has to
    name the issue on one of them -- guessing between files is never done.

    This used to refuse anything the file alone could not confirm, and every
    name it could not read -- "Vol.1.No.19", "No.04", Supergirl: Woman of
    Tomorrow #2's -- was barred as the wrong comic, deleted, and never tried
    again. Every refusal now says what was read from each file.
    """
    wanted_label = f"{context.get('seriesTitle')} {_number_label(context)}"
    ranked = sorted(
        (_download_candidate_score(path, context) for path in candidates),
        key=lambda item: (-item[0], str(item[1]["path"])),
    )
    readings = "\n".join(_download_reading(info, score) for score, info in ranked)
    release_matches = vouched or (bool(release_title) and _release_candidate_score(
        {"title": release_title}, context
    )[0] >= 85)
    release_line = (
        f"Release: {release_title} -- taken by hand as {wanted_label}" if vouched
        else f"Release: {release_title} -- {'matches' if release_matches else 'does not match'} {wanted_label}"
        if release_title else "Release: not recorded"
    )

    def refuse(headline: str, kind: str) -> DownloadContentMismatch:
        return DownloadContentMismatch(f"{headline}\nFiles read:\n{readings}\n{release_line}", kind=kind)

    readable = [info for score, info in ranked if score > -1000]
    if not readable:
        reason = ranked[0][1].get("reason") or "the archive could not be opened"
        # Zero-filled means the posted data is missing -- Supergirl: Woman of
        # Tomorrow #2's only release had 22 of its 140 MB gone, no RAR header,
        # and no repair files. That is proof, so it bars the release; a file
        # that is merely unreadable here may only be beyond this machine.
        if all(info.get("damaged") for _score, info in ranked):
            raise refuse(f"The download is incomplete ({reason})", "damaged")
        # Locked is proof too: no download of this release will open.
        if all(info.get("locked") for _score, info in ranked):
            raise refuse("The download is password-protected", "format")
        raise refuse(f"No comic in the download could be read ({reason})", "unreadable")
    score, selected = ranked[0]
    if vouched:
        names = ", ".join(Path(info["path"]).name for info in readable)
        numbered = [info for info in readable if info.get("issueMatch")]
        if len(numbered) == 1:
            return {**numbered[0], "acceptedOn": "file"}
        if len(readable) == 1:
            return {**readable[0], "acceptedOn": "hand"}
        if numbered:
            raise refuse(f"Could not tell which of {len(numbered)} files is {wanted_label}: {names}", "unidentified")
        raise refuse(f"None of the {len(readable)} files names {wanted_label}: {names}", "unidentified")
    if not (score >= 180 and selected.get("issueMatch") and selected.get("titleRatio", 0) >= 0.55):
        judged = [(info, _download_contradiction(info, context)) for info in readable]
        uncontradicted = [info for info, contradiction in judged if not contradiction]
        # A pack's title never scores as the issue itself, but a pack of this
        # series that holds it vouches for one file that names the issue.
        # Never for a file that names nothing: that is taken on a matching
        # release's word alone.
        pack_vouches = False
        if not release_matches and release_title and len(uncontradicted) == 1 and uncontradicted[0].get("issueMatch"):
            _pack_score, said = _release_candidate_score({"title": release_title}, context)
            pack_vouches = bool(_release_pack_coverage(release_title, context)) and "Series title matches" in said
        if (release_matches or pack_vouches) and len(uncontradicted) == 1:
            selected = {**uncontradicted[0], "acceptedOn": "release"}
        elif not uncontradicted:
            raise refuse(judged[0][1], "contradiction")
        elif not release_matches:
            raise refuse(
                f"Could not tell whether this is {wanted_label}: no file names the issue, "
                "and the release name does not match it", "unidentified",
            )
        else:
            raise refuse(
                f"Could not tell which of {len(uncontradicted)} files is {wanted_label}", "unidentified",
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
        raise refuse(
            f"The download is labelled as {language_name(conflict)}, "
            f"not {language_name(wanted if wanted is not None else preferred_language())}",
            "language",
        )
    return selected


def _download_contradiction(info: dict[str, Any], context: dict[str, Any]) -> str | None:
    """What a file states that makes it not the issue wanted; None if it states nothing against it."""
    name = Path(info["path"]).name
    issue = info.get("candidateIssue")
    if issue and not info.get("issueMatch"):
        return (f"{name} is {_number_label({**context, 'issueNumber': issue})}, "
                f"not {context.get('seriesTitle')} {_number_label(context)}")
    embedded = (info.get("result") or {}).get("embedded_metadata") or {}
    series = str(embedded.get("series") or "").strip()
    wanted = normalized_title(_without_leading_article(context.get("seriesTitle") or ""))
    if series and wanted:
        stated = normalized_title(_without_leading_article(series))
        if difflib.SequenceMatcher(None, stated, wanted).ratio() < 0.55:
            return f"{name} says it is {series}, not {context.get('seriesTitle')}"
    return None


def _download_reading(info: dict[str, Any], score: int) -> str:
    """One line of a refusal: what was read from one file."""
    name = Path(info["path"]).name
    if score <= -1000:
        return f"- {name}: could not be read ({info.get('reason') or 'unknown error'})"
    result = info.get("result") or {}
    lookup = result.get("lookup_identity") or {}
    embedded = result.get("embedded_metadata") or {}
    title = lookup.get("title") or embedded.get("series") or "no series"
    issue = info.get("candidateIssue")
    source = " (from its ComicInfo)" if embedded.get("series") or embedded.get("number") else ""
    return (f"- {name}: read as {title!r}, issue {('#' + str(issue)) if issue else 'not stated'}"
            f"{source}; title likeness {float(info.get('titleRatio') or 0):.2f}")


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
    # The run this issue was requested for. Worked out again from the filename
    # it would be the wrong one whenever a series has been relaunched under the
    # same name, which is the ordinary case for a long-running character.
    requested_run = str(context.get("seriesId") or "").strip()
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
        series_run_id=int(requested_run) if requested_run.isdigit() else None,
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
    source = str(download.get("source") or "sabnzbd")
    if source == "qbittorrent":
        # The file qBittorrent finished, already translated into the mount of
        # its comics folder; read in place, and never removed from there.
        completed_root = TORRENT_COMPLETE_ROOT
        source_container = Path(str(download.get("sab_storage") or ""))
        if not source_container.exists():
            raise CompletedDownloadNotVisible(
                "The finished torrent is not visible in Flipparr's torrents folder yet"
            )
    elif source != "sabnzbd":
        # Flipparr fetched this one into its own staging folder, so there is no
        # SABnzbd path to translate and the containment root is staging's.
        completed_root = acquisition_staging_dir("direct_site")
        source_container = Path(str(download.get("sab_storage") or ""))
    else:
        source_container = _resolve_sab_download_source(
            str(download.get("sab_storage") or ""), str(download.get("release_title") or ""),
            completed_root,
        )
    selected = select_downloaded_comic(
        source_container, context, completed_root,
        release_title=str(download.get("release_title") or "") or None,
        vouched=bool(download.get("taken_by_hand")),
    )
    try:
        return _import_selected_comic(selected, context, download, library_root)
    finally:
        # A manga ebook is converted into a temporary CBZ. By now the library
        # holds its copy, or the import failed and the copy is of no use.
        _discard_converted(selected)


def import_uploaded_comic(job_id: int, stream: Any, length: int, filename: str) -> dict[str, Any]:
    """Take a comic the user fetched themselves and file it against a wanted issue.

    Everything after the bytes land is the path an automatic download already
    takes: the file has to prove it is the issue it is claimed for, the library
    names and places it, and it is cataloged under the run the request named.
    Only the way it arrived is different.

    Until now there was no way to hand a file over. A comic found by hand had
    to be dropped into the library folder and waited for, and nothing connected
    it to the issue that was wanted.
    """
    store = catalog_store()
    context = store.get_acquisition_job_context(job_id)
    name = _safe_path_component(filename or "", "comic")
    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("That file is not a comic Flipparr can read")
    folder = acquisition_staging_dir("manual") / str(job_id)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    staged = folder / name
    selected: dict[str, Any] | None = None
    try:
        remaining = length
        with staged.open("xb") as handle:
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("The upload ended before the whole comic arrived")
                handle.write(chunk)
                remaining -= len(chunk)
        # Refuses a comic that says it is something else, exactly as it would
        # for a download: being handed the file is not evidence about it.
        selected = select_downloaded_comic(folder, context, folder, release_title=name)
        placed = _import_selected_comic(selected, context, {"job_id": job_id}, COMIC_LIBRARY_ROOT)
        placed["fileId"] = _catalog_imported_issue(
            Path(placed["destination"]), context, COMIC_LIBRARY_ROOT,
        )
        download = store.record_acquisition_download(
            job_id, f"manual:{job_id}:{int(time.time())}", name, source="manual",
        )
        store.update_acquisition_download(
            int(download["id"]), "imported", local_source=str(staged),
            destination=placed["destination"], source_size=placed["size"],
            source_sha256=placed["sha256"],
        )
        store.update_acquisition_job(
            job_id, "fulfilled",
            f"Imported from the file you uploaded, as {Path(placed['destination']).name}",
        )
        log_event(
            "manual_import", job_id=job_id, file=name,
            destination=placed["destination"], size=placed["size"],
        )
        return {"status": "imported", **placed}
    finally:
        _discard_converted(selected)
        shutil.rmtree(folder, ignore_errors=True)


def _discard_converted(selected: dict[str, Any] | None) -> None:
    # What was made for the import -- a converted ebook, an issue copied out
    # of a pack -- goes once it is filed or refused.
    for key in ("convertedDir", "unpackedDir"):
        folder = (selected or {}).get(key)
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
    _clear_stale_partials(destination)
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
    if nzo_id:
        try:
            sab = _enabled_acquisition_service("sabnzbd")
            # archive=0 deletes the entry; without it SABnzbd 4 only archives
            # it. del_files covers a failed job's files -- SABnzbd leaves a
            # completed job's alone, which is why they are removed below.
            endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
                "mode": "history", "name": "delete", "value": nzo_id, "del_files": 1,
                "archive": 0, "output": "json", "apikey": sab["apiKey"],
            })
            fetch_json_with_headers(
                endpoint, {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"},
                timeout=30.0,
            )
        except Exception as exc:
            log_exception("sab_cleanup_failed", exc, level="warning")
    # Three copies of American Vampire #19 were still in the completed folder
    # after being "removed": SABnzbd had forgotten the jobs and kept the files.
    folder = _sab_job_folder(str((download or {}).get("sab_storage") or ""), SAB_COMPLETE_ROOT)
    if folder is not None:
        try:
            if folder.is_dir():
                shutil.rmtree(folder)
            else:
                folder.unlink()
        except OSError as exc:
            log_exception("sab_files_cleanup_failed", exc, level="warning")


def _sab_job_folder(storage: str, completed_root: Path) -> Path | None:
    """The folder SABnzbd finished one job into, beneath the comics category.

    Taken only from the storage path SABnzbd reported, never from a folder
    that merely looks like the release, because whatever this returns is
    deleted -- and never the category folder itself.
    """
    try:
        root = completed_root.resolve()
    except OSError:
        return None
    parts = Path(str(storage or "").rstrip("/\\")).parts
    indexes = [index for index, part in enumerate(parts) if part.casefold() == root.name.casefold()]
    if not indexes or len(parts) <= indexes[-1] + 1:
        return None
    folder = root / parts[indexes[-1] + 1]
    try:
        resolved = folder.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if resolved == root or not folder.exists():
        return None
    return folder


class DownloadClientUnanswered(Exception):
    """A download client that answered, but not with what was asked for."""


def _sab_slots(payload: Any, part: str) -> list[dict[str, Any]]:
    """The slots of SABnzbd's `part` ("queue" or "history"), or an error.

    A wrong API key is answered 200, `{"status": false, "error": "API Key
    Incorrect"}`. Read as no slots, every download looked forgotten and, ten
    minutes on, each was given up and the next release grabbed (2026-10-05).
    Only a real queue or history says what SABnzbd has.
    """
    section = payload.get(part) if isinstance(payload, dict) else None
    slots = section.get("slots") if isinstance(section, dict) else None
    if not isinstance(slots, list):
        said = payload.get("error") if isinstance(payload, dict) else None
        raise DownloadClientUnanswered(
            f"SABnzbd did not answer with its {part}" + (f": {str(said)[:120]}" if said else ""))
    return [slot for slot in slots if isinstance(slot, dict)]


def _sab_history_slot(download: dict[str, Any]) -> dict[str, Any] | None:
    sab = _enabled_acquisition_service("sabnzbd")
    endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
        "mode": "history", "start": 0, "limit": 1,
        "nzo_ids": str(download["sab_nzo_id"]), "output": "json", "apikey": sab["apiKey"],
    })
    payload = fetch_json_with_headers(
        endpoint, {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"}, timeout=30.0
    )
    return next(
        (slot for slot in _sab_slots(payload, "history") if str(slot.get("nzo_id")) == str(download["sab_nzo_id"])),
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
    # A download Flipparr is fetching itself keeps its own figures: there is no
    # queue elsewhere to ask, and the row is written as the bytes arrive.
    ours: dict[str, Any] = {}
    torrents = [row for row in active if row.get("source") == "qbittorrent"]
    if torrents:
        ours.update(_torrent_progress(torrents))
    for row in active:
        if str(row.get("source") or "sabnzbd") in ("sabnzbd", "qbittorrent"):
            continue
        fetched, total = int(row.get("bytes_fetched") or 0), int(row.get("bytes_total") or 0)
        ours[str(row["job_id"])] = {
            "percent": round(fetched * 100 / total, 1) if total else 0.0,
            "sizeLeft": f"{max(0, total - fetched) / 1_000_000:.0f} MB" if total else "",
            "timeLeft": "", "state": str(row.get("status") or "downloading"),
        }
    active = [row for row in active if str(row.get("source") or "sabnzbd") == "sabnzbd"]
    if not active:
        return {"downloads": ours, "paused": False}
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
    return {"downloads": {**ours, **downloads}, "paused": bool((queue or {}).get("paused"))}


def _torrent_progress(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How far each torrent row's download is, asked of qBittorrent once.
    With only some files wanted, its progress is of those files."""
    try:
        client = _qbittorrent_client()
        hashes = sorted({info for info in (_torrent_hash(row) for row in rows) if info})
        found = {str(entry.get("hash") or "").casefold(): entry for entry in client.info(hashes=hashes)}
    except Exception:  # noqa: BLE001 -- no progress to show, not a page that fails
        log_event("qbittorrent_progress_unavailable", level="warning")
        return {}
    progress: dict[str, Any] = {}
    for row in rows:
        entry = found.get(_torrent_hash(row) or "")
        if not entry:
            continue
        left = int(entry.get("amount_left") or 0)
        eta = int(entry.get("eta") or 0)
        kind = torrent_client.state_class(entry.get("state"))
        progress[str(row["job_id"])] = {
            "percent": round(max(0.0, min(1.0, float(entry.get("progress") or 0))) * 100, 1),
            "sizeLeft": f"{left / 1_000_000:.0f} MB" if left else "",
            # 8640000 is qBittorrent's "no idea".
            "timeLeft": f"{eta // 3600}:{eta % 3600 // 60:02d}:{eta % 60:02d}" if 0 < eta < 8640000 else "",
            "state": "downloading" if kind == "downloading" else kind,
        }
    return progress


# Runs already asked about in this sweep. A run whose catalogs simply have no
# dates must not be re-synchronized once per issue.
_YEAR_FILL_ATTEMPTED: set[int] = set()
_YEAR_FILL_LOCK = threading.Lock()


def _issue_year_gate(job_id: int) -> str | None:
    """Fill a missing publication year before searching, or say why not to grab.

    `_release_year_conflict` is the only thing keeping a relaunch's release out
    of the original run's request, and it is inert when the issue's own year is
    unknown -- silence reads as agreement. So an unknown year is worth one
    provider refresh before any release is chosen.

    If it is still unknown afterwards, whether to wait depends on what the year
    would have decided. With one run of the title there is nothing to confuse
    and the search goes ahead as before. With two, the year is the only thing
    separating them, so the job waits for a person rather than guessing: the
    wrong choice puts a file in the wrong run and fills a run that is already
    complete.

    Returns None to carry on, or the reason to show on the job's row.
    """
    store = catalog_store()
    try:
        context = store.get_acquisition_job_context(job_id)
    except Exception as exc:  # noqa: BLE001 -- the grab decides, not this check
        log_exception("issue_year_gate_unavailable", exc, level="warning")
        return None

    def stated_year(values: dict[str, Any]) -> int:
        try:
            return int(values.get("publicationYear") or 0)
        except (TypeError, ValueError):
            return 0

    if stated_year(context):
        return None
    try:
        series_run_id = int(str(context.get("seriesId") or "").strip() or 0)
    except (TypeError, ValueError):
        series_run_id = 0
    # Nothing to ask, or nobody to ask: an install with no metadata provider
    # must not stop acquiring.
    if not series_run_id or not _series_enrichment_provider_order():
        return None
    with _YEAR_FILL_LOCK:
        first_attempt = series_run_id not in _YEAR_FILL_ATTEMPTED
        _YEAR_FILL_ATTEMPTED.add(series_run_id)
    if first_attempt:
        try:
            sync_issue_catalog(series_run_id)
        except Exception as exc:  # noqa: BLE001 -- a provider that cannot answer is not a failure here
            log_event(
                "issue_year_fill_failed", level="warning",
                job_id=job_id, series_run_id=series_run_id, error=str(exc),
            )
        else:
            try:
                if stated_year(store.get_acquisition_job_context(job_id)):
                    return None
            except Exception as exc:  # noqa: BLE001
                log_exception("issue_year_recheck_failed", exc, level="warning")
                return None
    try:
        siblings = store.runs_sharing_title(series_run_id)
    except Exception as exc:  # noqa: BLE001
        log_exception("issue_year_siblings_unavailable", exc, level="warning")
        return None
    if not siblings:
        return None
    others = ", ".join(
        f"{item['title']} ({item['year']})" if item.get("year") else str(item["title"])
        for item in siblings[:3]
    )
    return (
        "No release date is known for this issue yet, and this title has another run "
        f"({others}). Without the date a release from the wrong run cannot be ruled out, "
        "so nothing was grabbed. Find release still works."
    )


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
    hold = _issue_year_gate(job_id)
    if hold:
        _say_on_the_row(job_id, hold)
        return None
    try:
        search = search_prowlarr_releases(job_id)
    except Exception as exc:
        log_event(
            "retry_release_search_failed", level="warning",
            job_id=job_id, error=str(exc),
        )
        return None
    candidates = search.get("candidates") if isinstance(search, dict) else []
    context = (search.get("job") if isinstance(search, dict) else None) or {}
    if not isinstance(candidates, list):
        candidates = []
    candidates, held_back = _automatic_grab_order(context, candidates) if candidates else ([], "")
    # A download-site single is asked for when the indexers have no single or
    # pack worth taking -- it comes before settling for a torrent pack -- and
    # when the download site comes before the best single's source in the admin's
    # order. Otherwise it is not asked: each ask is a page fetcher round trip.
    # World's Finest #32 sat at "No release found yet" while Find release
    # showed the issue, confidently, from the download site -- a list a person had to
    # go and act on.
    firsts = [item for item in candidates if not item.get("lastResort")]
    single_ranks = [_source_rank(item.get("source")) for item in firsts if not item.get("pack")]
    if not firsts or (single_ranks and _source_rank("direct_site") < min(single_ranks)):
        fallback = _direct_site_single_to_take(job_id, context)
        if fallback is not None:
            try:
                grabbed = grab_release_candidate(job_id, str(fallback["id"]))
            except Exception as exc:  # noqa: BLE001 -- the row says; the next pass tries again
                log_event("direct_site_single_grab_failed", level="warning", job_id=job_id,
                          release=str(fallback.get("title") or ""), error=str(exc)[:200])
                if not firsts:
                    _say_on_the_row(job_id, f"Found {fallback.get('title') or 'a release'} on the download site but could not fetch it: {exc}")
                    return None
            else:
                return {**grabbed, "release": fallback}
    if not candidates:
        if held_back:
            _say_on_the_row(job_id, held_back)
        return None
    # The same release is usually posted on several indexers, and one of them
    # handing back something that is not an NZB says nothing about the others.
    # Once & Future #30 sat at "4 release candidates found": one indexer's copy
    # was ranked first, its NZB was broken, and the three good copies were never
    # tried. The release itself is not marked unusable -- that goes by title,
    # and would take the good copies down with the broken one.
    problems: list[str] = []
    for candidate in candidates[:MAX_AUTOMATIC_RELEASE_FAILURES]:
        try:
            # SABnzbd for Usenet, qBittorrent for a torrent.
            grabbed = grab_release_candidate(job_id, str(candidate["id"]))
        except ReleaseDownloadError as exc:
            problems.append(f"{candidate.get('indexer') or 'an indexer'}: {exc}")
            log_event(
                "retry_release_fetch_failed", level="warning",
                job_id=job_id, release=str(candidate.get("title") or ""),
                indexer=str(candidate.get("indexer") or ""), error=str(exc),
            )
            continue
        except Exception as exc:
            # The download client itself refusing is not the release's fault,
            # and the next candidate would meet the same refusal.
            log_event(
                "retry_release_send_failed", level="warning",
                job_id=job_id, release=str(candidate.get("title") or ""), error=str(exc),
            )
            _say_on_the_row(job_id, f"Found a release but could not send it to {_client_name(candidate)}: {exc}")
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


def _client_name(candidate: dict[str, Any]) -> str:
    return "qBittorrent" if str((candidate or {}).get("source") or "") == "torrent" else "SABnzbd"


def _direct_site_single_to_take(job_id: int, context: dict[str, Any]) -> dict[str, Any] | None:
    """The one download-site release the automatic search may take for an issue
    Usenet has nothing for: a strong match for that issue alone (a pack is
    the run's question, asked elsewhere), not refused before, and only while
    the download site can be downloaded from."""
    try:
        _enabled_acquisition_service("flaresolverr")
    except ValueError:
        return None
    title = str(context.get("seriesTitle") or "").strip()
    if not title:
        return None
    try:
        found = _direct_site_candidates(job_id, context, title)
        refused = set(catalog_store().rejected_acquisition_release_keys(job_id))
    except Exception as exc:  # noqa: BLE001 -- the download site down is not a reason to stop
        log_event("direct_site_single_search_failed", level="info", job_id=job_id, error=str(exc)[:160])
        return None
    for candidate in sorted(found, key=lambda item: -int(item.get("matchScore") or 0)):
        if candidate.get("pack") or int(candidate.get("matchScore") or 0) < 85:
            continue
        if {str(candidate.get(key) or "") for key in ("postUrl", "releaseKey", "guid")} & refused:
            continue
        return candidate
    return None


def _resume_run_after_pack(job_id: int, release_title: str) -> None:
    """A run pack that did not arrive, or arrived holding less than it said,
    hands what it did not bring straight to single issues.

    Grabbing a pack stops the per-issue searches -- the rest of the run rides
    on it -- so a pack that fails left every other issue waiting for the next
    scheduled sweep. Y: The Last Man's sixty issues did, behind a download site
    pack whose link opened a file host's page. The job the pack was grabbed
    against is left to its own fallback.
    """
    try:
        context = catalog_store().get_acquisition_job_context(job_id)
        request_id = int(str(context.get("requestId") or "").strip() or 0)
        if not request_id or not _release_pack_coverage(release_title, context):
            return
    except Exception:  # noqa: BLE001 -- the scheduled sweep is still there
        return
    if not _acquisition_services_ready(request_id):
        return
    log_event("run_pack_fell_back", request_id=request_id, job_id=job_id, release=release_title[:200])
    threading.Thread(
        target=_automatic_release_grabs, args=(request_id,),
        kwargs={"skip_run_pack": True, "exclude": frozenset({int(job_id)})},
        name=f"flipparr-run-singles-{request_id}", daemon=True,
    ).start()


def _grab_run_pack(store: Any, request_id: int, job_ids: list[int]) -> dict[str, Any] | None:
    """One search for the run, before asking for its issues one at a time.

    Per-issue search cannot find a pack. A job for Batman #40 asks for
    "Batman 040", and a release called "Batman 001-100" is not an answer to
    that question -- it is an answer to the run's question, which nothing was
    asking. So a run that is mostly missing gets one search for its title
    first, and the packs that come back are judged against everything it wants.

    Returns what was grabbed, or None to fan out into singles exactly as
    before. The issues the pack holds are imported when it lands, by the sweep
    in `_sweep_download_for_other_issues`.
    """
    try:
        wanted = [
            item for item in store.wanted_run_issues(request_id)
            if int(item["jobId"]) in set(job_ids)
        ]
        numbers = [number for number in (_issue_number(item["issueNumber"]) for item in wanted)
                   if number is not None]
        if len(numbers) < PACK_MINIMUM_WANTED:
            return None
        # The issue the pack is grabbed against; the rest ride on its download.
        lead = min(wanted, key=lambda item: _issue_number(item["issueNumber"]) or 10_000)
        lead_job = int(lead["jobId"])
        context = store.get_acquisition_job_context(lead_job)
        # One run's issues: a collection's request spans several.
        wanted = _same_run(wanted, context)
        numbers = [number for number in (_issue_number(item["issueNumber"]) for item in wanted)
                   if number is not None]
        if len(numbers) < PACK_MINIMUM_WANTED:
            return None
        title = str(context.get("seriesTitle") or "").strip()
        if not title:
            return None
        search = search_prowlarr_releases(lead_job, title)
    except Exception as exc:  # noqa: BLE001 -- singles are always the fallback
        log_event("run_pack_search_failed", level="warning", request_id=request_id, error=str(exc))
        return None
    candidates = list(search.get("candidates") or [])
    # Direct-download sites post whole runs as packs ("Batman Beyond 2.0 #1-40 + TPBs")
    # that Usenet may not carry. For a run that is mostly missing, a pack
    # holding it is the best answer wherever it is, so the download site's packs are
    # weighed here too -- when it can be downloaded from at all. Its single
    # issues stay a person's choice, as before.
    try:
        _enabled_acquisition_service("flaresolverr")
        candidates += [item for item in _direct_site_candidates(lead_job, context, title) if item.get("pack")]
    except ValueError:
        pass
    except Exception as exc:  # noqa: BLE001 -- Usenet's packs still stand
        log_event("run_pack_direct_site_failed", level="warning", request_id=request_id, error=str(exc)[:160])
    # A pack that already failed for this run -- a download-site link that opens
    # a file host's page, an NZB that would not complete -- is not tried again.
    # Judged against every issue of the run: after a failure the issue the
    # pack was grabbed against falls back to a single and another leads, and
    # the refusal must still count.
    try:
        refused = set(store.rejected_release_keys_for_jobs(job_ids))
    except Exception:  # noqa: BLE001
        refused = set()
    candidates, _unshared = _automatically_takeable(candidates)
    order = source_priority()
    offers: list[tuple[int, int, int, dict[str, Any]]] = []
    for candidate in candidates:
        if not candidate.get("pack"):
            continue
        if {str(candidate.get(key) or "") for key in ("postUrl", "releaseKey", "guid")} & refused:
            log_event("run_pack_declined", level="info", request_id=request_id,
                      release=str(candidate.get("title") or ""), reason="failed before")
            continue
        worth, why = _pack_worth_taking(context, candidate, numbers)
        if not worth:
            log_event(
                "run_pack_declined", level="info", request_id=request_id,
                release=str(candidate.get("title") or ""), reason=why,
            )
            continue
        pack = candidate["pack"]
        covered = sum(1 for number in numbers if pack["first"] <= number <= pack["last"])
        # Most of the run first; between equals the admin's order of sources
        # (Usenet, a torrent, then the download site unless changed), then the smaller
        # download.
        rank = _source_rank(candidate.get("source"), order)
        offers.append((-covered, rank, int(candidate.get("sizeBytes") or 0), candidate))
    if not offers:
        return None
    offers.sort(key=lambda item: (item[0], item[1], item[2]))
    covered, _rank, _size, candidate = offers[0]
    try:
        # SABnzbd for Usenet, qBittorrent for a torrent, Flipparr's own
        # download for the download site.
        grab_release_candidate(lead_job, str(candidate["id"]))
    except Exception as exc:  # noqa: BLE001 -- a pack that cannot be sent is not a reason to stop
        log_event(
            "run_pack_grab_failed", level="warning", request_id=request_id,
            release=str(candidate.get("title") or ""), error=str(exc),
        )
        return None
    parts = 0
    if candidate.get("source") == "direct_site":
        parts = _grab_other_direct_site_parts(store, candidate, lead, wanted, context)
    return {"title": str(candidate.get("title") or ""), "covers": -covered, "jobId": lead_job, "parts": 1 + parts}


def _grab_other_direct_site_parts(
    store: Any, candidate: dict[str, Any], lead: dict[str, Any],
    wanted: list[dict[str, Any]], context: dict[str, Any],
) -> int:
    """The rest of a download site run pack posted in parts, one download a part.

    A grab fetches the part holding its own issue, so "Descender #1 – 32 +
    TPBs" brought "#1 – 8" and nothing else: #9 to #32 were left to singles
    (2026-09-30). Each other part holding wanted issues is fetched against the
    first of them, and the rest of that part rides on its download. Returns
    how many parts were started.
    """
    post_url = str(candidate.get("postUrl") or "")
    lead_number = _issue_number(lead.get("issueNumber"))
    try:
        links = _direct_site_post_parts(post_url)
    except Exception as exc:  # noqa: BLE001 -- the lead's part is on its way regardless
        log_event("direct_site_parts_unread", level="warning", release=str(candidate.get("title") or ""),
                  error=str(exc)[:160])
        return 0
    parts: dict[str, dict[str, Any]] = {}
    for link in links:
        if link.get("first") is not None:
            parts.setdefault(str(link.get("label") or ""), link)
    started = 0
    for label, part in parts.items():
        first = int(part["first"])
        last = int(part["last"] if part.get("last") is not None else first)
        if lead_number is not None and first <= lead_number <= last:
            continue
        inside = sorted(
            (item for item in wanted
             if (number := _issue_number(item.get("issueNumber"))) is not None and first <= number <= last),
            key=lambda item: _issue_number(item.get("issueNumber")) or 0,
        )
        if not inside:
            continue
        job_id = int(inside[0]["jobId"])
        try:
            # Judged by its own issue's years, as its fetch will be: a run's
            # later parts are later years than its #1.
            part_context = store.get_acquisition_job_context(job_id)
            if not _direct_site_part_fits_years(label, (part_context.get("seriesYear"), part_context.get("publicationYear"))):
                continue
        except Exception:  # noqa: BLE001 -- unjudged is not fetched
            continue
        # Named for the part, so the issues it holds are the ones held for it;
        # its size is the post's business, not the row's.
        named = re.sub(r"\s*\(\s*[0-9.]+\s*(?:kb|mb|gb)\s*\)\s*$", "", label, flags=re.I).strip()
        title = named if _direct_site_names_series(named, context) else f"{context.get('seriesTitle')} {named}"
        try:
            download = store.record_acquisition_download(
                job_id, f"direct_site:{hashlib.sha256(post_url.encode()).hexdigest()[:20]}:{job_id}",
                title, release_key=post_url, source="direct_site",
            )
            store.update_acquisition_job(job_id, "grabbed", f"Downloading from the download site: {title}")
            _start_direct_fetch(int(download["id"]), job_id, post_url, title)
            started += 1
        except Exception as exc:  # noqa: BLE001 -- its issues fall to singles
            log_event("direct_site_part_not_started", level="warning", job_id=job_id, part=label[:120],
                      error=str(exc)[:160])
    if started:
        log_event("direct_site_parts_grabbed", release=str(candidate.get("title") or ""), parts=started)
    return started


def _stated_pack_range(title: Any, context: dict[str, Any]) -> tuple[int, int] | None:
    """The issues a release says it holds, whichever issue is asked about."""
    if context.get("format") == "manga":
        return _manga_pack_range(title)
    return _release_issue_range(title, context.get("runIssueCount"))


def _issues_awaiting_a_pack(store: Any) -> set[int]:
    """The jobs a pack still on its way will bring, so no pass searches them.

    Only the request's own pass knew a run pack had been grabbed; the backlog
    sweep every quarter hour saw issues nothing had been sent for and searched
    them. Descender's pack landed a minute before the sweep came round, which
    was luck: a minute the other way and #2 to #8 would have come twice.
    A torrent's issues each have their own download row already.
    """
    try:
        live = store.live_downloads_with_requests()
    except Exception as exc:  # noqa: BLE001 -- unknown means nothing is held
        log_exception("pack_holds_unavailable", exc, level="warning")
        return set()
    held: set[int] = set()
    wanted_by_request: dict[int, list[dict[str, Any]]] = {}
    for row in live if isinstance(live, list) else []:
        if row.get("source") == "qbittorrent":
            continue
        try:
            context = store.get_acquisition_job_context(int(row["job_id"]))
            stated = _stated_pack_range(row.get("release_title"), context)
            if not stated:
                continue
            request_id = int(row["request_id"])
            if request_id not in wanted_by_request:
                wanted_by_request[request_id] = list(store.wanted_run_issues(request_id))
            for item in _same_run(wanted_by_request[request_id], context):
                number = _issue_number(item.get("issueNumber"))
                if number is not None and stated[0] <= number <= stated[1] and int(item["jobId"]) != int(row["job_id"]):
                    held.add(int(item["jobId"]))
        except Exception as exc:  # noqa: BLE001 -- one unreadable row holds nothing
            log_event("pack_hold_unread", level="warning", download_id=row.get("id"), error=str(exc)[:160])
    return held


def _automatic_grab_order(
    context: dict[str, Any], candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """What may be taken without asking, best first.

    Singles behave exactly as they always have. A pack joins them only when it
    answers most of what the run is missing; otherwise it stays in the list a
    person chooses from, and the row says why it was not taken.
    """
    candidates, unshared = _automatically_takeable(candidates)
    if not candidates:
        return [], unshared
    candidates = sorted(candidates, key=_release_order_key())
    singles = [item for item in candidates if not item.get("pack")]
    packs = [item for item in candidates if item.get("pack")]
    oversized = [item for item in singles if int(item.get("sizeBytes") or 0) > SINGLE_RELEASE_MAX_BYTES]
    singles = [item for item in singles if item not in oversized]
    if oversized and not singles and not packs:
        biggest = oversized[0]
        return [], (
            f"Found {biggest.get('title') or 'a release'} but did not take it automatically: "
            f"{int(biggest.get('sizeBytes') or 0) / 1_000_000_000:.1f} GB is too large for one issue. "
            "Find release lists it if you want it."
        )
    if not packs:
        return singles, ""
    try:
        request_id = int(str(context.get("requestId") or "").strip() or 0)
        wanted = [
            number for number in (
                _issue_number(item.get("issueNumber"))
                for item in _same_run(
                    catalog_store().wanted_run_issues(request_id) if request_id else [], context,
                )
            ) if number is not None
        ]
    except Exception as exc:  # noqa: BLE001 -- unknown demand means singles only
        log_exception("pack_demand_unavailable", exc, level="warning")
        return singles, ""
    eligible: list[dict[str, Any]] = []
    refused = ""
    for candidate in packs:
        worth, why = _pack_worth_taking(context, candidate, wanted)
        if worth:
            eligible.append(candidate)
        elif not refused:
            refused = f"{candidate.get('title') or 'A pack'} — {why}"
    # Pulling one issue, a pack is the last resort -- taken only when nothing
    # else has the issue at all -- and then only a torrent, the one pack that
    # can be fetched for that issue alone (owner, 2026-09-30). Marked, so
    # the caller asks the download site for a single before settling for it.
    last_resort = [] if singles or eligible else [
        {**candidate, "lastResort": True} for candidate in packs if candidate.get("source") == "torrent"
    ]
    note = ""
    if not singles and not eligible and not last_resort and refused:
        note = (
            f"Found a pack but did not take it automatically: {refused}. "
            "Find release lists it if you want it."
        )
    return singles + eligible + last_resort, note


def _same_run(wanted: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    """The wanted issues of this job's run only. A collection's request spans
    several runs, and Batman #4's file must never answer Detective #4."""
    series = str(context.get("seriesId") or "").strip()
    if not series:
        return list(wanted)
    return [item for item in wanted if str(item.get("seriesId") or series) == series]


def _say_on_the_row(job_id: int, reason: str) -> None:
    """Put why a grab stopped on the job's row -- a courtesy that must never
    turn "nothing was grabbed" into a crash, whatever happened to the job."""
    try:
        catalog_store().update_acquisition_job(job_id, "queued", reason)
    except Exception as exc:  # noqa: BLE001 -- the grab's answer stands either way
        log_exception("grab_reason_not_recorded", exc, level="warning")


def _automatic_release_grabs(
    request_id: int | None = None, *, backoff: bool = False, skip_run_pack: bool = False,
    exclude: frozenset[int] = frozenset(),
) -> None:
    """Grab the best release for every job still waiting on one.

    `backoff` is for the scheduled sweep, which must not ask the indexer about
    the same unfindable issue every quarter of an hour. A person pressing
    "Manual find" means now, and passes it off.

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
    # One refresh per run per sweep, not per issue.
    with _YEAR_FILL_LOCK:
        _YEAR_FILL_ATTEMPTED.clear()
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
    job_ids = [job_id for job_id in job_ids if job_id not in exclude]
    if job_ids:
        held = _issues_awaiting_a_pack(store)
        job_ids = [job_id for job_id in job_ids if job_id not in held]
    if not job_ids:
        return
    # A run that is mostly missing is one question -- "who has this run?" --
    # and asking it once can answer every job at a stroke. Only for a named
    # request: the whole-backlog sweep spans many runs, and each would need its
    # own search.
    if request_id is not None and not skip_run_pack:
        pack = _grab_run_pack(store, int(request_id), job_ids)
        if pack:
            log_event(
                "run_pack_grabbed", request_id=int(request_id),
                release=pack["title"], covers=pack["covers"], job_id=pack["jobId"],
            )
            # The rest of the run rides on this download; searching for each
            # issue as well would grab the same comics twice over.
            return
    grabbed = 0
    started = time.time()
    for job_id in job_ids:
        if service_failed_since("prowlarr", started):
            # Prowlarr stopped answering during this pass: the rest would each
            # wait out its deadline for the same silence.
            break
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


def _acquisition_services_ready(request_id: int) -> bool:
    """Whether automatic searching can do anything: no indexer or no download
    client means every search would fail in a loop and say nothing useful,
    so the jobs are left for the list."""
    try:
        _enabled_acquisition_service("prowlarr")
    except Exception:
        log_event("automatic_release_grabs_skipped", level="info",
                  request_id=request_id, reason="prowlarr is not configured")
        return False
    if not _enabled_download_clients():
        log_event("automatic_release_grabs_skipped", level="info",
                  request_id=request_id, reason="no download client is configured")
        return False
    return True


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
    if not _acquisition_services_ready(request_id):
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
    try:
        _enabled_acquisition_service("prowlarr")
    except Exception:
        raise ValueError("Prowlarr is not configured, so there is nothing to search with") from None
    if not _enabled_download_clients():
        raise ValueError("No download client is configured, so there is nothing to download with") from None
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
    store: CatalogStore, download: dict[str, Any], message: str,
    *, kind: str = "download", storage: str | None = None,
) -> dict[str, Any]:
    """Record a refused release and try the next unused strong result.

    `kind` is what was proven. A download SABnzbd could not complete, or a
    wrong comic, bars the release; one that was lost, unreadable or
    unidentified only sets it aside for a day (catalog_store's
    SOFT_REFUSAL_KINDS). `storage` is where its files were kept, so they can
    be removed once they are no longer evidence (_discard_kept_downloads).
    """
    job_id = int(download["job_id"])
    release_key = str(download.get("release_key") or "").strip() or _legacy_release_key(download)
    release_title = str(download.get("release_title") or "Unknown release")
    kept_nzo = (str(download.get("sab_nzo_id") or "") or None) if storage else None
    failure = store.record_acquisition_release_failure(
        job_id, release_key, release_title, message,
        kind=kind, sab_nzo_id=kept_nzo, sab_storage=storage or None,
    )
    if storage and str(download.get("source") or "") == "qbittorrent":
        # Kept as evidence, not shared on: stopped until it is let go.
        _pause_torrent_if_alone(store, download)
    # A torrent's other issues have rows of their own and fall back each by
    # itself; handing the run to singles as well would search them twice.
    if not _rides_with_others(store, download):
        _resume_run_after_pack(job_id, release_title)
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

    candidates, _unshared = _automatically_takeable(candidates)
    candidates.sort(key=_release_order_key())
    if not candidates:
        detail = f"{message}. No unused strong Prowlarr match can be taken automatically."
        store.update_acquisition_job(job_id, "failed", detail)
        return {"status": "failed", "error": detail, "automaticFallback": False}
    candidate = candidates[0]
    try:
        grabbed = grab_release_candidate(job_id, str(candidate["id"]))
    except Exception as exc:
        detail = f"{message}. The next release could not be sent to {_client_name(candidate)}: {exc}"
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
    return next(
        (slot for slot in _sab_slots(payload, "queue") if str(slot.get("nzo_id")) == str(download["sab_nzo_id"])),
        None,
    )


# What SABnzbd pauses a download for, by the label it puts on the slot, and
# what that is in plain words. Each is proof the release is wrong, not the
# connection: a password-protected archive, a duplicate, a job past the size
# limit, a file type it refuses. A slot paused with no label is a person's
# pause, and stands.
SAB_REFUSAL_LABELS = {
    "ENCRYPTED": "SABnzbd paused it: the archive is password-protected",
    "TOO LARGE": "SABnzbd paused it as larger than its size limit",
    "DUPLICATE": "SABnzbd paused it as a duplicate of a download it already has",
    "UNWANTED_EXTENSION": "SABnzbd paused it as holding a file type it refuses",
}


def _sab_just_sent(download: dict[str, Any]) -> bool:
    """Whether a download was sent so recently that SABnzbd's queue should be left alone."""
    try:
        sent = dt.datetime.fromisoformat(str(download.get("created_at") or ""))
    except ValueError:
        return True
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - sent).total_seconds() < SAB_MISSING_GRACE_SECONDS


def _sab_queue_refusal(download: dict[str, Any]) -> str | None:
    """Why SABnzbd has paused a queued download, when the reason condemns the release.

    A download in the queue and not in the history read as "downloading"
    however long it sat there: Absolute Batman #24 sat a day at 39%,
    paused with the label ENCRYPTED, and the Pull List said Downloading.
    Asked after the same grace as a lost download: SABnzbd finds an
    encrypted archive minutes in, and a just-sent job is left to settle.
    """
    if _sab_just_sent(download):
        return None
    try:
        slot = _sab_queue_slot(download)
    except Exception:  # noqa: BLE001 -- unreachable is not a refusal
        return None
    if not slot or str(slot.get("status") or "").casefold() != "paused":
        return None
    labels = {str(label).strip().upper() for label in (slot.get("labels") or []) if label}
    for label, reason in SAB_REFUSAL_LABELS.items():
        if label in labels:
            return reason
    return None


def _sab_forget_queued(download: dict[str, Any]) -> None:
    """Take a refused download out of SABnzbd's queue, files and all. A failure costs disk, not the outcome."""
    nzo_id = str((download or {}).get("sab_nzo_id") or "").strip()
    if not nzo_id:
        return
    try:
        sab = _enabled_acquisition_service("sabnzbd")
        endpoint = f"{sab['url']}/api?" + urllib.parse.urlencode({
            "mode": "queue", "name": "delete", "value": nzo_id, "del_files": 1,
            "output": "json", "apikey": sab["apiKey"],
        })
        fetch_json_with_headers(
            endpoint, {"Accept": "application/json", "User-Agent": f"Flipparr/{APP_VERSION}"}, timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001
        log_exception("sab_queue_delete_failed", exc, level="warning")


def _sab_lost_download(download: dict[str, Any]) -> bool:
    """Whether SABnzbd has forgotten a download: in neither its queue nor its history.

    Waiting on one it has forgotten waits forever. American Vampire #19 did:
    its files were deleted as the wrong comic, "Retry import" put it back to
    waiting on SABnzbd, and the Pull List said Downloading from then on.
    """
    if _sab_just_sent(download):
        return False
    return _sab_queue_slot(download) is None


def reconcile_acquisition_download(download: dict[str, Any]) -> dict[str, Any]:
    store = catalog_store()
    source = str(download.get("source") or "sabnzbd")
    if source == "qbittorrent":
        # Never the direct-download road below: that one restarts a "fetch"
        # of the release key and deletes the folder after import, and this
        # folder is seeding.
        outcome = _qbt_download_storage(store, download)
        if isinstance(outcome, dict):
            return outcome
        return _import_completed_download(store, download, outcome)
    if source != "sabnzbd":
        # Nobody to ask: Flipparr is fetching it, and the row says where it got
        # to. Everything from the import onwards is shared with SABnzbd's path.
        status = str(download.get("status") or "")
        if status in {"queued", "downloading"}:
            # Still going only if a thread is fetching it. One that is not --
            # a restart took it -- is started again, once; if that fails, the
            # fetch's own failure path says so on the row.
            download_id = int(download.get("id") or 0)
            with _DIRECT_FETCHES_LOCK:
                orphaned = download_id and download_id not in _DIRECT_FETCHES and download_id not in _DIRECT_FETCHES_RESTARTED
                if orphaned:
                    _DIRECT_FETCHES_RESTARTED.add(download_id)
            if orphaned and download.get("release_key"):
                _start_direct_fetch(download_id, int(download.get("job_id") or 0), str(download["release_key"]),
                                    str(download.get("release_title") or "download-site download"))
            return {"status": "downloading"}
        # "importing" here is an import a restart cut off: only this worker
        # imports, one download at a time, so none is under way. It is done
        # again from the fetched folder; a copy already in place counts as
        # imported (`_import_selected_comic`).
        if status not in {"completed", "importing"} or (status == "importing" and not download.get("sab_storage")):
            return {"status": status or "unknown"}
        storage = str(download.get("sab_storage") or "")
    else:
        outcome = _sab_download_storage(store, download)
        if isinstance(outcome, dict):
            return outcome
        storage = outcome
    return _import_completed_download(store, download, storage)


def _torrent_hash(download: dict[str, Any]) -> str | None:
    """The info hash in a torrent download's identity, `torrent:<hash>:<job>`."""
    parts = str((download or {}).get("sab_nzo_id") or "").split(":")
    return parts[1].casefold() if len(parts) >= 3 and parts[0] == "torrent" and parts[1] else None


def _torrent_is_dead(torrent: dict[str, Any]) -> bool:
    """A magnet whose file list never came, or a torrent nobody has shared
    for half a day: waiting longer only holds the issue back."""
    state = str(torrent.get("state") or "").casefold()
    now = time.time()
    added = int(torrent.get("added_on") or 0)
    if state in ("metadl", "forcedmetadl"):
        return bool(added) and now - added > TORRENT_METADATA_GRACE_SECONDS
    if state == "stalleddl" and not int(torrent.get("num_seeds") or 0):
        since = int(torrent.get("last_activity") or 0) or added
        return bool(since) and now - since > TORRENT_STALLED_SECONDS
    return False


def _torrent_local(torrent: dict[str, Any], name: str) -> Path:
    """Where Flipparr sees one of a torrent's files -- `name` relative to its
    save folder, which is the comics category folder qBittorrent was given."""
    save = posixpath.basename(str(torrent.get("save_path") or "").rstrip("/"))
    if save.casefold() != TORRENT_COMPLETE_ROOT.name.casefold():
        raise CompletedDownloadNotVisible(
            f"qBittorrent saved this torrent in “{save or 'its default folder'}”, not in the "
            f"“{TORRENT_COMPLETE_ROOT.name}” folder Flipparr reads finished torrents from"
        )
    return TORRENT_COMPLETE_ROOT.joinpath(*[part for part in str(name).split("/") if part not in ("", ".", "..")])


def _torrent_comics(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in files
            if Path(str(entry.get("name") or "")).suffix.lower() in SUPPORTED_EXTENSIONS]


def _torrent_file_matching(files: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    """The file in a torrent that is this issue, by its number and the
    series' likeness together -- never the number alone."""
    matches = [entry for entry in files
               if _pack_member_matches(posixpath.basename(str(entry.get("name") or "")), context)]
    return max(matches, key=lambda entry: int(entry.get("size") or 0)) if matches else None


def _torrent_holdings(files: list[dict[str, Any]]) -> str:
    names = [posixpath.basename(str(entry.get("name") or "")) for entry in files]
    shown = ", ".join(names[:8])
    return shown + (f", and {len(names) - 8} more" if len(names) > 8 else "")


def _qbt_download_storage(store: Any, download: dict[str, Any]) -> str | dict[str, Any]:
    """Where qBittorrent finished this issue's file, or the answer for a
    torrent that has not: waiting for its file list, downloading, broken,
    gone, or dead."""
    info_hash_ = _torrent_hash(download)
    status = str(download.get("status") or "")
    try:
        client = _qbittorrent_client()
        found = client.info(hashes=[info_hash_]) if info_hash_ else []
    except ValueError:
        return {"status": status or "downloading", "detail": "qBittorrent is not connected"}
    except torrent_client.TorrentClientError as exc:
        log_event("qbittorrent_unavailable", level="warning", error=str(exc)[:160])
        return {"status": status or "downloading"}
    if not found:
        if _sab_just_sent(download):
            return {"status": "downloading"}
        message = "qBittorrent no longer has this torrent"
        store.update_acquisition_download(int(download["id"]), "failed", error=message, failure_stage="download")
        return _fallback_after_sab_failure(store, download, message, kind="lost")
    torrent = found[0]
    if status == "queued":
        return _choose_torrent_files(store, client, download, torrent)
    state = str(torrent.get("state") or "")
    kind = torrent_client.state_class(state)
    if kind == "error":
        message = f"qBittorrent could not finish the torrent ({state})"
        store.update_acquisition_download(int(download["id"]), "failed", error=message, failure_stage="download")
        return _fallback_after_sab_failure(store, download, message)
    if kind == "downloading":
        if _torrent_is_dead(torrent):
            message = "Nobody is sharing this torrent, so it cannot finish"
            store.update_acquisition_download(int(download["id"]), "failed", error=message, failure_stage="download")
            _release_torrent(store, info_hash_)
            return _fallback_after_sab_failure(store, download, message, kind="lost")
        store.update_acquisition_download(int(download["id"]), "downloading")
        return {"status": "downloading"}
    # Its wanted files are all here: this issue's own, or -- for a release in
    # several files that names none of them -- the torrent's folder, for the
    # import to choose from.
    files = _torrent_comics([entry for entry in client.files(info_hash_) if int(entry.get("priority") or 0) > 0])
    context = store.get_acquisition_job_context(int(download["job_id"]))
    mine = files[0] if len(files) == 1 else _torrent_file_matching(files, context)
    try:
        if mine is not None:
            local = _torrent_local(torrent, str(mine.get("name") or ""))
        else:
            content = str(torrent.get("content_path") or "")
            save = str(torrent.get("save_path") or "").rstrip("/")
            relative = content[len(save):].lstrip("/") if content.startswith(save) else posixpath.basename(content)
            local = _torrent_local(torrent, relative)
    except CompletedDownloadNotVisible as exc:
        message = str(exc)
        store.update_acquisition_download(int(download["id"]), "waiting_for_files", error=message, failure_stage=None)
        store.update_acquisition_job(int(download["job_id"]), "grabbed", message)
        return {"status": "waiting_for_files", "detail": message}
    store.update_acquisition_download(int(download["id"]), "completed", sab_storage=str(local))
    return str(local)


def _choose_torrent_files(store: Any, client: Any, download: dict[str, Any], torrent: dict[str, Any]) -> dict[str, Any]:
    """Pick the wanted files out of a torrent whose file list has arrived.

    Only this issue's file is fetched, and every other wanted issue of the
    same run that the torrent holds rides along on a download row of its own
    -- marked grabbed, so nothing searches for it singly while it comes. A
    pack that does not hold the issue it was taken for is refused, naming
    what it holds; a single release in several files keeps them all, and
    the import chooses.
    """
    info_hash_ = _torrent_hash(download) or ""
    job_id = int(download["job_id"])
    title = str(download.get("release_title") or "the torrent")
    if not client.supports_selection():
        # An older client fetches the whole torrent; packs are not sent to it.
        store.update_acquisition_download(int(download["id"]), "downloading")
        return {"status": "downloading"}
    files = client.files(info_hash_)
    if not files:
        if _torrent_is_dead(torrent):
            message = "The torrent's file list never arrived; nobody is sharing it"
            store.update_acquisition_download(int(download["id"]), "failed", error=message, failure_stage="download")
            _release_torrent(store, info_hash_)
            return _fallback_after_sab_failure(store, download, message, kind="lost")
        return {"status": "downloading", "detail": "Waiting for the torrent's file list"}
    context = store.get_acquisition_job_context(job_id)
    label = f"{context.get('seriesTitle')} {_number_label(context)}"
    comics = _torrent_comics(files)
    if not comics:
        return _refuse_torrent(store, download, f"The torrent holds no comic files: {_torrent_holdings(files)}", "format")
    if "flipparr-selected" in torrent_client.tags_of(torrent):
        # Another issue chose this torrent's files already; this one joins it.
        mine = comics[0] if len(comics) == 1 else _torrent_file_matching(comics, context)
        if mine is None:
            return _refuse_torrent(store, download, f"The pack does not hold {label}: it holds {_torrent_holdings(comics)}",
                                   "contradiction", release=False)
        if not int(mine.get("priority") or 0):
            client.set_file_priority(info_hash_, [int(mine["index"])], 1)
        client.start([info_hash_])
        store.update_acquisition_download(int(download["id"]), "downloading")
        return {"status": "downloading"}
    keep: dict[int, dict[str, Any]] = {}
    riders: list[tuple[int, dict[str, Any]]] = []
    if len(comics) == 1:
        keep[int(comics[0]["index"])] = comics[0]
    else:
        mine = _torrent_file_matching(comics, context)
        if mine is None:
            if _release_pack_coverage(title, context):
                return _refuse_torrent(
                    store, download, f"The pack does not hold {label}: it holds {_torrent_holdings(comics)}",
                    "contradiction",
                )
            # One release in several files: all of it, and the import chooses.
            keep = {int(entry["index"]): entry for entry in comics}
        else:
            keep[int(mine["index"])] = mine
            riders = _torrent_riders(store, context, job_id, comics, keep)
    chosen = sum(int(entry.get("size") or 0) for entry in keep.values())
    cap = (SINGLE_RELEASE_MAX_BYTES if not riders and len(keep) > 1 else PACK_BYTES_PER_COVERED_ISSUE * len(keep))
    if chosen > cap:
        return _refuse_torrent(
            store, download,
            f"The torrent's wanted files come to {chosen / 1_000_000_000:.1f} GB for {len(keep)} "
            f"issue{'' if len(keep) == 1 else 's'}", "format",
        )
    client.set_file_priority(info_hash_, [int(entry["index"]) for entry in files if int(entry["index"]) not in keep], 0)
    client.set_file_priority(info_hash_, sorted(keep), 1)
    client.add_tags([info_hash_], ["flipparr-selected"])
    client.start([info_hash_])
    store.update_acquisition_download(int(download["id"]), "downloading")
    for rider, _entry in riders:
        try:
            row = store.record_acquisition_download(
                rider, f"torrent:{info_hash_}:{rider}", title, str(download.get("release_key") or "") or None,
                source="qbittorrent",
            )
            store.update_acquisition_download(int(row["id"]), "downloading")
            store.update_acquisition_job(rider, "grabbed", f"Coming in the same download as {label}: {title}")
        except Exception as exc:  # noqa: BLE001 -- that issue stays wanted and is searched as usual
            log_exception("torrent_rider_not_recorded", exc, level="warning")
    log_event("torrent_files_chosen", job_id=job_id, release=title[:200], files=len(keep),
              riders=len(riders), of_files=len(files))
    return {"status": "downloading", "selected": len(keep), "riders": len(riders)}


def _torrent_riders(
    store: Any, context: dict[str, Any], lead_job: int, comics: list[dict[str, Any]], keep: dict[int, dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """The run's other wanted issues this torrent holds, each with its file.

    Only issues nothing else is fetching: queued, not yet released, or failed
    -- not one being searched this moment, and never another run's."""
    try:
        request_id = int(str(context.get("requestId") or "").strip() or 0)
        wanted = _same_run(store.wanted_run_issues(request_id), context) if request_id else []
    except Exception as exc:  # noqa: BLE001 -- the lead issue still comes
        log_exception("torrent_riders_unavailable", exc, level="warning")
        return []
    riders = []
    for item in wanted:
        rider = int(item["jobId"])
        if rider == lead_job or str(item.get("status") or "") not in ("queued", "waiting", "failed"):
            continue
        with _JOBS_IN_FLIGHT_LOCK:
            if rider in _JOBS_IN_FLIGHT:
                continue
        entry = _torrent_file_matching(
            [file for file in comics if int(file["index"]) not in keep], {**context, "issueNumber": item["issueNumber"]},
        )
        if entry is not None:
            keep[int(entry["index"])] = entry
            riders.append((rider, entry))
    return riders


def _refuse_torrent(store: Any, download: dict[str, Any], message: str, kind: str, *, release: bool = True) -> dict[str, Any]:
    """A torrent that cannot be this issue: its row fails, the release is
    refused for it, and -- when no other issue is using it -- it goes."""
    store.update_acquisition_download(
        int(download["id"]), "failed", error=message,
        failure_stage="content" if kind in ("contradiction", "format") else "download",
    )
    if release:
        _release_torrent(store, _torrent_hash(download))
    return _fallback_after_sab_failure(store, download, message, kind=kind)


def _release_torrent(store: Any, info_hash_: str | None) -> bool:
    """Remove a torrent and its files once nothing of Flipparr's uses it: no
    live download of any issue, and no refused download kept as evidence."""
    if not info_hash_:
        return False
    try:
        references = store.torrent_references(info_hash_)
        if references.get("live") or references.get("kept"):
            return False
        _qbittorrent_client().delete([info_hash_], delete_files=True)
    except Exception as exc:  # noqa: BLE001 -- the seeding sweep tries again
        log_event("torrent_release_failed", level="warning", info_hash=info_hash_, error=str(exc)[:160])
        return False
    return True


def _pause_torrent_if_alone(store: Any, download: dict[str, Any]) -> None:
    """Stop a refused torrent kept as evidence, unless another issue's file
    is still coming in it."""
    info_hash_ = _torrent_hash(download)
    try:
        if info_hash_ and not store.torrent_references(info_hash_).get("live"):
            _qbittorrent_client().pause([info_hash_])
    except Exception as exc:  # noqa: BLE001 -- evidence seeding on is harmless
        log_event("torrent_pause_failed", level="info", info_hash=info_hash_, error=str(exc)[:160])


def _rides_with_others(store: Any, download: dict[str, Any]) -> bool:
    """Whether other issues came in the same torrent, each with its own row."""
    info_hash_ = _torrent_hash(download)
    if not info_hash_:
        return False
    try:
        return any(int(job) != int(download["job_id"]) for job in store.torrent_jobs(info_hash_))
    except Exception:  # noqa: BLE001
        return False


def _sab_download_storage(store: Any, download: dict[str, Any]) -> str | dict[str, Any]:
    """Where SABnzbd put a finished download, or the answer for one that is not.

    Split out so a download Flipparr fetched itself can reach the import below
    without being asked about in SABnzbd's queue, where it has never been.
    """
    slot = _sab_history_slot(download)
    if not slot:
        refusal = _sab_queue_refusal(download)
        if refusal:
            _sab_forget_queued(download)
            store.update_acquisition_download(
                int(download["id"]), "failed", error=refusal, failure_stage="download",
            )
            return _fallback_after_sab_failure(store, download, refusal)
        if _sab_lost_download(download):
            message = "SABnzbd no longer has this download"
            store.update_acquisition_download(
                int(download["id"]), "failed", error=message, failure_stage="download",
            )
            # Lost is not proof the release is bad, so it is set aside, not barred.
            return _fallback_after_sab_failure(store, download, message, kind="lost")
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
    return storage


def _import_completed_download(
    store: Any, download: dict[str, Any], storage: str,
) -> dict[str, Any]:
    """Put a finished download into the library, whoever fetched it."""
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
            f"{'qBittorrent' if download.get('source') == 'qbittorrent' else 'SABnzbd'} finished; "
            "waiting for the completed file to appear in Flipparr",
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
        # A refused download has a stage of its own -- "content" for a proven
        # wrong comic, "unidentified" for one that could not be confirmed --
        # not an import to retry: the same check would refuse it again.
        store.update_acquisition_download(
            int(download["id"]), "failed", sab_storage=storage, error=message,
            failure_stage=exc.stage if isinstance(exc, DownloadContentMismatch) else "import",
        )
        if isinstance(exc, DownloadContentMismatch) and download.get("taken_by_hand"):
            # The person chose this release; another is not grabbed in its
            # place, and nothing is recorded against it. Its files stay where
            # they are for them to look at (SABnzbd's own retention applies).
            headline = (message.splitlines() or ["it could not be imported"])[0]
            store.update_acquisition_job(int(download["job_id"]), "failed", f"Taken by hand, but {headline}")
            return {"status": "failed", "error": message, "automaticFallback": False, "takenByHand": True}
        if isinstance(exc, DownloadContentMismatch):
            # Its files stay in SABnzbd's folder: they are the evidence for
            # why it was refused. Supergirl: Woman of Tomorrow #2's were
            # deleted the moment it was refused, so what it had downloaded
            # could never be looked at. They go once the issue is imported
            # from another release, or after a week (_discard_kept_downloads).
            #
            # Then record the release and take the next candidate. Without
            # this the issue sat failed until somebody noticed and asked for
            # another search by hand.
            try:
                outcome = _fallback_after_sab_failure(
                    store, download, message, kind=exc.kind, storage=storage,
                )
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
        f"Imported as {Path(imported['destination']).name} on your word" if download.get("taken_by_hand")
        else f"Imported and verified as {Path(imported['destination']).name}",
    )
    # Before the folder goes: a pack holds the run's other wanted issues, and
    # _sab_remove_job is about to delete it.
    swept = _sweep_download_for_other_issues(store, download, storage, context)
    if swept:
        imported["packImports"] = swept
        store.update_acquisition_job(
            int(download["job_id"]), "fulfilled",
            f"Imported and verified as {Path(imported['destination']).name}"
            f", with {len(swept)} more issue{'' if len(swept) == 1 else 's'} from the same download",
        )
    if str(download.get("source") or "sabnzbd") == "sabnzbd":
        _sab_remove_job({**download, "sab_storage": storage})
    elif download.get("source") == "qbittorrent":
        # It seeds on until qBittorrent's own ratio or time limit stops it;
        # `_release_seeded_torrents` removes it then.
        pass
    else:
        # Nothing in SABnzbd to forget; the staging folder is ours to clear.
        shutil.rmtree(Path(storage), ignore_errors=True)
    # A pack held its run's issues back while it came. What it turned out not
    # to hold goes to singles now, not at the next sweep a quarter hour away.
    if download.get("source") != "qbittorrent" and _stated_pack_range(download.get("release_title"), context):
        _resume_run_after_pack(int(download["job_id"]), str(download.get("release_title") or ""))
    # Refused downloads of this issue were kept as evidence; it is here now.
    _discard_kept_downloads(store, job_id=int(download["job_id"]))
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


def _sweep_download_for_other_issues(
    store: Any, download: dict[str, Any], storage: str, context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Import the run's other wanted issues from a download that holds them.

    This is what makes a pack worth grabbing: one download answering many jobs.
    It runs before `_sab_remove_job`, which deletes the folder the moment the
    grabbing job is done.

    Every file goes through the same identity check as any other import --
    `select_downloaded_comic` refuses a comic that says it is something else --
    so a download cannot fulfil an issue it does not actually contain. Nothing
    here may fail the import that already succeeded: the grabbing job keeps its
    comic and anything not found stays wanted for the next pass.
    """
    if download.get("source") == "qbittorrent":
        # A torrent's other issues were chosen with it and each has its own
        # download row; the rest of its files were never fetched.
        return []
    try:
        request_id = int(str(context.get("requestId") or "").strip() or 0)
        grabbing_job = int(download["job_id"])
        if not request_id:
            return []
        outstanding = [
            item for item in store.wanted_run_issues(request_id)
            if int(item["jobId"]) != grabbing_job and str(item["status"]) != "grabbed"
        ]
        if not outstanding:
            return []
        if str(download.get("source") or "sabnzbd") != "sabnzbd":
            completed_root = acquisition_staging_dir("direct_site")
            source = Path(storage)
        else:
            completed_root = SAB_COMPLETE_ROOT
            source = _resolve_sab_download_source(
                storage, str(download.get("release_title") or ""), SAB_COMPLETE_ROOT,
            )
    except Exception as exc:  # noqa: BLE001 -- the grabbed issue is already home
        log_exception("pack_sweep_setup_failed", exc, level="warning")
        return []
    imported: list[dict[str, Any]] = []
    for item in outstanding:
        job_id = int(item["jobId"])
        selected = None
        try:
            job_context = store.get_acquisition_job_context(job_id)
            selected = select_downloaded_comic(
                source, job_context, completed_root,
                release_title=str(download.get("release_title") or "") or None,
            )
            placed = _import_selected_comic(
                selected, job_context, {"job_id": job_id}, COMIC_LIBRARY_ROOT,
            )
            _catalog_imported_issue(
                Path(placed["destination"]), job_context, COMIC_LIBRARY_ROOT,
            )
        except DownloadContentMismatch:
            # This download simply does not hold that issue. Not a failure:
            # the job stays wanted and is searched for as usual.
            continue
        except Exception as exc:  # noqa: BLE001
            log_event(
                "pack_sweep_issue_failed", level="warning", job_id=job_id,
                release=str(download.get("release_title") or ""), error=str(exc),
            )
            continue
        finally:
            _discard_converted(selected)
        try:
            store.record_download_import(
                int(download["id"]), job_id, int(item["issueId"]), placed["destination"],
            )
            store.update_acquisition_job(
                job_id, "fulfilled",
                f"Imported from {download.get('release_title') or 'the same download'}"
                f" as {Path(placed['destination']).name}",
            )
        except Exception as exc:  # noqa: BLE001 -- the comic is in the library either way
            log_exception("pack_sweep_record_failed", exc, level="warning")
        imported.append({"jobId": str(job_id), "destination": placed["destination"]})
    if imported:
        log_event(
            "pack_sweep_imported", release=str(download.get("release_title") or ""),
            download_id=int(download["id"]), issues=len(imported),
        )
    return imported


# How long a refused download is kept as evidence when its issue never arrives.
KEPT_REFUSED_DAYS = 7
_KEPT_SWEPT_AT = [0.0]


def _discard_kept_downloads(
    store: CatalogStore, *, job_id: int | None = None, older_than_days: int | None = None,
) -> int:
    """Remove refused downloads kept as evidence, once they no longer are:
    when their issue is imported from another release, or after a week."""
    kept = store.kept_refused_downloads(job_id=job_id, older_than_days=older_than_days)
    if not isinstance(kept, list):
        return 0
    for row in kept:
        identity = str(row.get("sab_nzo_id") or "")
        if identity.startswith("torrent:"):
            # Forgotten first, so this refusal no longer holds the torrent;
            # it goes when nothing else does.
            store.forget_kept_download(int(row["id"]))
            _release_torrent(store, _torrent_hash({"sab_nzo_id": identity}))
            continue
        _sab_remove_job({"sab_nzo_id": row.get("sab_nzo_id"), "sab_storage": row.get("sab_storage")})
        store.forget_kept_download(int(row["id"]))
    return len(kept)


def _release_seeded_torrents(store: Any) -> int:
    """Remove the torrents Flipparr has finished with, and their files.

    Driven by qBittorrent's own list of what Flipparr added (the `flipparr`
    tag), not by download rows: a row goes when its issue is fulfilled another
    way, a request is cancelled or a retry clears it, and a torrent kept only
    by its row would then seed for ever. A torrent still in use -- a download
    in flight, a refusal kept as evidence -- stays; so does one still seeding,
    until the client stops it at its ratio or time limit. A torrent Flipparr
    did not tag is never touched.
    """
    try:
        client = _qbittorrent_client()
    except ValueError:
        return 0
    in_use = store.torrent_hashes_in_use()
    removed = 0
    now = time.time()
    for torrent in client.info(tag="flipparr"):
        info_hash_ = str(torrent.get("hash") or "").casefold()
        if not info_hash_ or info_hash_ in in_use:
            continue
        # Just added: its download row may be a moment behind.
        if now - int(torrent.get("added_on") or now) < 600:
            continue
        state = torrent.get("state")
        if torrent_client.state_class(state) == "complete" and not torrent_client.stopped_after_seeding(state):
            continue
        try:
            client.delete([info_hash_], delete_files=True)
            removed += 1
        except torrent_client.TorrentClientError as exc:
            log_event("torrent_release_failed", level="warning", info_hash=info_hash_, error=str(exc)[:160])
    if removed:
        log_event("seeded_torrents_released", removed=removed)
    return removed


# Which acquisition service is not answering: since when, and what it last
# said. Kept in memory -- a restart asks again -- and cleared by the first
# answer. Before 2026-10-05 an outage showed only as a warning per download
# every 15 seconds, and nobody was told.
_SERVICE_TROUBLE: dict[str, dict[str, Any]] = {}
_SERVICE_TROUBLE_LOCK = threading.Lock()
# How long a service may stay silent before the admins are told. Downloads
# wait and searches do not count meanwhile, so a blip is nobody's business;
# half an hour is a service that needs looking at.
SERVICE_TROUBLE_NOTICE_SECONDS = 1800


def service_trouble(service: str, problem: BaseException | str) -> None:
    """Note that `service` did not answer, and tell the admins once it has
    been so long enough to need them. Never raises."""
    now = time.time()
    message = support_safe(f"{type(problem).__name__}: {problem}" if isinstance(problem, BaseException) else str(problem))[:300]
    with _SERVICE_TROUBLE_LOCK:
        trouble = _SERVICE_TROUBLE.setdefault(service, {"since": now, "told": False})
        trouble.update(last=now, message=message)
        tell = not trouble["told"] and now - trouble["since"] >= SERVICE_TROUBLE_NOTICE_SECONDS
        if tell:
            trouble["told"] = True
        since = trouble["since"]
    if tell:
        try:
            store = catalog_store()
            for user in store.list_users():
                if user["role"] == "admin" and not user["disabled"]:
                    store.record_notification(int(user["id"]), "service_trouble", f"service:{service}:{int(since)}",
                                              {"service": service, "since": _iso(since)}, merge=True)
        except Exception as exc:  # noqa: BLE001
            log_exception("service_notification_failed", exc, level="warning", service=service)


def service_answered(service: str) -> None:
    with _SERVICE_TROUBLE_LOCK:
        trouble = _SERVICE_TROUBLE.pop(service, None)
    if trouble and trouble.get("told"):
        log_event("service_answering_again", service=service)


def service_failed_since(service: str, moment: float) -> bool:
    with _SERVICE_TROUBLE_LOCK:
        trouble = _SERVICE_TROUBLE.get(service)
        return bool(trouble) and float(trouble.get("last") or 0) >= moment


def service_troubles() -> list[dict[str, Any]]:
    """For Settings -> System: the services not answering right now."""
    with _SERVICE_TROUBLE_LOCK:
        return [{"service": service, "name": SERVICE_NAMES.get(service, service),
                 "since": _iso(trouble["since"]), "lastProblem": trouble.get("message")}
                for service, trouble in sorted(_SERVICE_TROUBLE.items())]


SERVICE_NAMES = {"prowlarr": "Prowlarr", "sabnzbd": "SABnzbd", "qbittorrent": "qBittorrent",
                 "direct_site": "The download site",
                 "sabnzbd_folder": "SABnzbd's finished folder", "qbittorrent_folder": "The torrents folder"}


# What a service's trouble looks like from here -- an indexer or download
# client not answering, or answering half a reply -- as against a download's
# or a search's own failure.
SERVICE_HICCUPS = (
    urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException,
    json.JSONDecodeError, AcquisitionServiceOff, DownloadClientUnanswered,
)


def acquisition_import_worker(stop_event: threading.Event = _IMPORT_STOP) -> None:
    while not stop_event.is_set():
        worker_beat("imports")
        if time.monotonic() - _KEPT_SWEPT_AT[0] >= 3600:
            _KEPT_SWEPT_AT[0] = time.monotonic()
            try:
                _discard_kept_downloads(catalog_store(), older_than_days=KEPT_REFUSED_DAYS)
            except Exception as exc:
                log_exception("kept_download_sweep_failed", exc, level="warning")
            try:
                _release_seeded_torrents(catalog_store())
            except Exception as exc:
                log_exception("seeded_torrent_sweep_failed", exc, level="warning")
        try:
            downloads = catalog_store().pending_acquisition_downloads()
            imported_any = False
            unanswered: list[BaseException] = []
            for download in downloads:
                if stop_event.is_set():
                    break
                service = str(download.get("source") or "sabnzbd")
                try:
                    result = reconcile_acquisition_download(download)
                    imported_any = imported_any or result.get("status") == "imported"
                    service_answered(service)
                    # Finished, but not where Flipparr reads it: a mount that is
                    # missing or points elsewhere. It waits for good otherwise,
                    # saying so only on its own row.
                    folder = f"{service}_folder"
                    if result.get("status") == "waiting_for_files":
                        service_trouble(folder, str(result.get("detail") or "The finished download is not visible to Flipparr"))
                    elif result.get("status") == "imported":
                        service_answered(folder)
                except SERVICE_HICCUPS as exc:
                    service_trouble(service, exc)
                    # A download client that did not answer, or answered half a
                    # reply, says nothing about the download: it waits for the
                    # next pass. Until 2026-10-05 only a timeout counted, and a
                    # dropped connection failed the download for good -- for a
                    # torrent, the hourly sweep then deleted it and its data.
                    unanswered.append(exc)
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
            if unanswered:
                # Once a pass, not once a download every 15 seconds; and on
                # Settings -> System, where it was invisible before.
                log_exception("acquisition_import_transient", unanswered[0], level="warning",
                              downloads=len(unanswered))
                worker_problem("imports", unanswered[0])
            if imported_any:
                start_catalog_scan(str(COMIC_LIBRARY_ROOT), True, "local")
        except Exception as exc:  # noqa: BLE001 -- the next pass tries again
            # Swallowed without a word until 2026-10-05: imports could stop
            # and nothing anywhere said why.
            log_exception("acquisition_import_cycle_failed", exc, level="warning")
            worker_problem("imports", exc)
        stop_event.wait(IMPORT_POLL_SECONDS)


# The first sweep comes soon after start. A restart in the middle of a pass --
# a deploy, a crash -- otherwise left the rest of it for a quarter of an hour.
RESEARCH_FIRST_SWEEP_SECONDS = 60


def release_research_worker(stop_event: threading.Event = _RESEARCH_STOP) -> None:
    """Look again, on a schedule, for issues no release was found for.

    Nothing re-searched. An issue whose search came up empty sat at queued
    until somebody pressed "Manual find" by hand -- and a #1 that ships
    on Wednesday is often not posted until the weekend, so the common case was
    a comic that would have been found by simply asking again.

    The backoff lives in the query, so this is a cheap sweep that mostly
    returns nothing: an issue searched minutes ago is not due, and one nobody
    ever posts costs a request a day rather than one per pass.
    """
    delay = RESEARCH_FIRST_SWEEP_SECONDS
    while not stop_event.is_set():
        worker_beat("searches")
        stop_event.wait(delay)
        delay = RESEARCH_POLL_SECONDS
        if stop_event.is_set():
            break
        try:
            _enabled_acquisition_service("prowlarr")
            if not _enabled_download_clients():
                raise ValueError("no download client")
        except Exception:
            # No indexer or no download client: every search would fail in a
            # loop and say nothing useful.
            continue
        worker_beat("searches")
        try:
            _automatic_release_grabs(None, backoff=True)
        except Exception as exc:
            log_exception("release_research_failed", exc, level="warning")
            worker_problem("searches", exc)


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
            # A run that arrived may be one a saved story arc was waiting for.
            try:
                heal_reading_lists()
            except Exception as exc:  # noqa: BLE001 -- the scan is done either way
                log_event("reading_list_heal_failed", level="warning", error=str(exc)[:200])

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


def _separators_to_spaces(text: str) -> str:
    """Dots and underscores are a scene name's separators -- except the dot
    inside a decimal issue number: DC's Villains Month "Green Lantern 023.2"
    is issue 23.2, and read with the dot as a space it became issue 2 of
    "Green Lantern 023". One or two digits after the dot; "001.2016" is an
    issue and a year, and "2013.05.01" a date, both still split."""
    kept = re.sub(r"(?<=\d)\.(?=\d{1,2}(?!\d))", "\x00", str(text or ""))
    return re.sub(r"[._]+", " ", kept).replace("\x00", ".")


# How far a year in a file's name may sit from its folder's and still be
# that book's own date rather than another run's (the catalog's era slack).
RUN_YEAR_DRIFT = 3


def _run_year_clue(path: Path, raw: str, title: str, number_match: re.Match[str] | None) -> int | None:
    """The year a publication run began, where the file's name says so.

    Three ways a library names it, after the conventions Mylar, ComicTagger
    and Komga write (2026-10-05), strongest first:

    - the folder -- "Captain America (2011)/Captain America #010.cbr" --
      when the folder's title is the file's own. It is the grouping the
      owner made, and outranks a nearby year in the name: a book series
      files "The Adventure Zone (2019) #001" (that book's year) under "The
      Adventure Zone (2018)". A year an era away is another run's;
    - "V2011" -- "Batman V2011 #001";
    - a year before the number -- "Captain America (2011) #010": the
      series' year. A year after the number -- "Batman #015 (2017)" -- is
      the issue's cover date and is not read here.

    It is what tells relaunches apart: without it a library of Batman's
    1940, 2011, 2016 and 2025 runs, none with its #1, was one run of 245
    files. `year` keeps the issue's date either way.
    """
    named = None
    stated = VOLUME_YEAR.search(raw)
    if stated:
        named = int(stated.group(1))
    elif number_match is not None:
        years = re.findall(r"\(\s*((?:19|20)\d{2})\s*\)", raw[:number_match.start()])
        if years:
            named = int(years[-1])
    folder = re.sub(r"\s+", " ", _separators_to_spaces(urllib.parse.unquote(path.parent.name))).strip()
    found = re.fullmatch(r"(.+?)\s*\(\s*((?:19|20)\d{2})\s*\)", folder)
    if found and title and normalized_title(found.group(1)) == normalized_title(title):
        folder_year = int(found.group(2))
        # A name a whole era from its folder names another run, misfiled:
        # "Nightwing (2011) #025" in "Nightwing (2016)" is the 2011 run's
        # #25. Within a few years it is the book's own date.
        if named is not None and abs(named - folder_year) > RUN_YEAR_DRIFT:
            return named
        return folder_year
    return named


def parse_filename(path: Path) -> ParsedFile:
    decoded_filename = urllib.parse.unquote(path.name)
    stem = Path(decoded_filename).stem
    # "Ultimate Spider-Man ½" is issue 0.5, the number a release writes.
    for glyph, decimal in _VULGAR_FRACTIONS.items():
        if len(glyph) == 1:
            stem = stem.replace(glyph, decimal)
    raw = _separators_to_spaces(stem)
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
    if issue_match is None and volume_match is None:
        volume_match = SHORT_VOLUME.search(raw)
        volume = int(volume_match.group(1)) if volume_match else None
    issue = issue_match.group(1) if issue_match else None
    if issue:
        # "006 AU" is written 6AU, as the catalog has it.
        issue = re.sub(r"^(\d+(?:\.\d+)?)[ .]([A-Za-z]{1,3})$", r"\1\2", issue)
    if issue and re.fullmatch(r"\d+(?:\.\d+)?[A-Za-z]{0,3}", issue):
        # Leading zeros are padding: "023" is 23, "023.2" is 23.2.
        issue = re.sub(r"^0+(?=\d)", "", issue)
    # A scene name marks the issue "No 19", after the series' own volume, and
    # puts only tags after the number: "American Vampire Vol 1 No 19 Nov 2011
    # SCAN Comic eBook-iNTENSiTY" is issue 19 of the first series, not
    # collected volume 1. Read the other way, every copy of it was refused as
    # the wrong comic.
    scene_title_end = None
    if issue_match is not None and re.match(r"\s*no\b", issue_match.group(0), re.I):
        scene_title_end = issue_match.start()
        if volume_match and volume_match.start() < issue_match.start():
            volume = None
            scene_title_end = volume_match.start()

    detected_format = None
    for name, pattern in FORMAT_PATTERNS:
        if pattern.search(raw):
            detected_format = name
            break

    title = raw if scene_title_end is None else raw[:scene_title_end]
    if issue_match and scene_title_end is None:
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
    title = VOLUME_YEAR.sub(" ", title)   # before YEAR, which would leave the V
    title = YEAR.sub(" ", title)
    title = VOLUME.sub(" ", title)
    if volume_match is not None and volume_match.re is SHORT_VOLUME:
        title = SHORT_VOLUME.sub(" ", title)
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
        run_year=_run_year_clue(path, raw, title, issue_match or volume_match),
    )


# Comic archives are not all zips. RAR is a quarter of a typical library, and
# its compression cannot be decoded in pure Python, so those go through
# bsdtar (libarchive): BSD-licensed, and unlike unrar or 7-Zip's RAR decoder
# it carries no restriction on redistributing the image that ships it.
class ArchiveToolMissing(RuntimeError):
    """bsdtar is not installed, so non-zip comics cannot be opened here."""


_ARCHIVE_READ_TIMEOUT_SECONDS = 60
COMIC_ARCHIVE_EXTENSIONS = frozenset({".cbz", ".cbr", ".cb7", ".cbt"})
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


_LOCKED_ARCHIVE_WORDS = re.compile(r"encrypt|passphrase|password", re.I)


def archive_is_locked(path: Path) -> str | None:
    """Why a comic cannot be opened without a password, or None.

    For a download, before it is taken: SABnzbd pauses a password-protected
    Usenet post and says so, but a torrent or a direct download arrived with
    nothing to stop it, and the file-health check reads a RAR's header and no
    further (Gate 3, 2026-10-05). A zip says so in each member's flags; RAR
    and 7-Zip are asked to list themselves and hand over their first page,
    which libarchive refuses for an encrypted entry.
    """
    kind = archive_kind(path)
    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                if any(info.flag_bits & 0x1 for info in archive.infolist() if not info.is_dir()):
                    return "The archive is password-protected"
        except (zipfile.BadZipFile, OSError):
            return None
        return None
    if kind not in {"rar", "rar5", "7z"}:
        return None
    try:
        names = archive_member_names(path)
        pages = sorted(name for name in names if Path(name).suffix.lower() in ARCHIVE_IMAGE_EXTENSIONS)
        if pages:
            read_archive_member(path, pages[0])
    except ArchiveToolMissing:
        return None
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        if _LOCKED_ARCHIVE_WORDS.search(str(exc)):
            return "The archive is password-protected"
    return None


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


def archive_page_members(path: Path) -> list[str]:
    """The archive's image pages in reading order.

    2.jpg before 10.jpg, and no macOS resource forks or hidden files. Both the
    cover and a drawer background are chosen from this list, so they agree on
    what page 1 is.
    """
    try:
        names = archive_member_names(path)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError,
            subprocess.SubprocessError):
        # An unreadable archive has no pages; it is reported as a file problem
        # by the health check, not by failing this.
        return []
    images = [
        member for member in names
        if Path(member).suffix.lower() in ARCHIVE_IMAGE_EXTENSIONS
        and "__MACOSX" not in Path(member).parts
        and not any(part.startswith(".") for part in Path(member).parts)
    ]
    return sorted(images, key=_natural_member_key)


def find_archive_cover_member(path: Path, embedded: dict[str, Any] | None = None) -> str | None:
    """Find the file's declared cover or its first plausible comic page."""
    if embedded and embedded.get("cover_member"):
        return str(embedded["cover_member"])
    images = archive_page_members(path)
    if not images:
        return None
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


def _cover_source_path(raw: Any) -> Path | None:
    """The requested comic, when it is one the library actually holds.

    The path arrives in the query string, and anything readable used to be
    served: with the local-network bypass on, a NAS would hand any archive on
    the host to anyone on the LAN. The answer is the same containment rule an
    import already applies -- the file has to be inside a configured library
    root -- and symlinks are resolved first, so a link inside the library
    cannot reach outside it.

    None when the path names nothing servable, whatever the reason. The caller
    says only that; which of a missing file, a folder, or a file elsewhere on
    the host it was is not a client's business.
    """
    requested = str(raw or "").strip()
    if not requested:
        return None
    try:
        resolved = Path(requested).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file():
        return None
    roots: list[Path] = []
    try:
        roots.extend(Path(item) for item in catalog_store().library_root_paths())
    except Exception as exc:  # noqa: BLE001 -- an unreadable catalog denies, never opens
        log_exception("library_roots_unavailable", exc, level="warning")
    roots.append(COMIC_LIBRARY_ROOT)
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        return resolved
    return None


def inspect_file_health(path: Path) -> dict[str, str]:
    """Perform cheap structural checks without reading every comic page."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"status": "error", "code": "unreadable", "message": f"File cannot be read: {exc}"}
    if size == 0:
        return {"status": "error", "code": "empty_file", "message": "File is empty (0 bytes)."}

    extension = path.suffix.lower()
    # A comic archive is judged by what it is, not what it is called: a .cbr
    # that is really a zip is common (The Adventure Zone 05 on Usenet, twice),
    # and the reader already opens it by its contents (`archive_kind`).
    kind = archive_kind(path) if extension in COMIC_ARCHIVE_EXTENSIONS else None
    if extension == ".epub" or kind == "zip" or (extension == ".cbz" and kind is None):
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
        if extension != ".epub" and not any(
            Path(info.filename).suffix.lower() in ARCHIVE_IMAGE_EXTENSIONS for info in members
        ):
            return {
                "status": "error", "code": "no_image_pages",
                "message": f"{extension[1:].upper()} is readable but contains no supported image pages.",
            }
    elif kind == "tar" and not tarfile.is_tarfile(path):
        return {
            "status": "error", "code": "corrupt_archive",
            "message": f"{extension[1:].upper()} is not a readable TAR archive.",
        }
    elif extension in COMIC_ARCHIVE_EXTENSIONS and kind is None:
        expected = {".cbr": "a valid RAR archive header", ".cb7": "a valid 7-Zip archive header",
                    ".cbt": "a readable TAR archive"}[extension]
        return {
            "status": "error", "code": "corrupt_archive",
            "message": f"{extension[1:].upper()} does not have {expected}."
            if extension != ".cbt" else "CBT is not a readable TAR archive.",
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


_CACHE_SWEEP_LOCK = threading.Lock()
_CACHE_SWEPT_AT: dict[str, float] = {}


def sweep_image_cache(cache_dir: Path, budget: int, interval: float = 60.0) -> int:
    """Drop the least recently used files until the cache fits its budget.

    Reading a comic writes a page at a time, so without this the directory
    grows for as long as someone keeps reading. Rate-limited because eight
    preloads arriving together should walk the directory once, not eight
    times, and every filesystem error is swallowed: a cache that cannot be
    swept must degrade to rendering again, never to a failed page.

    Returns the bytes removed, for the test and for nothing else.
    """
    key = str(cache_dir)
    now = time.monotonic()
    with _CACHE_SWEEP_LOCK:
        if now - _CACHE_SWEPT_AT.get(key, 0.0) < interval:
            return 0
        _CACHE_SWEPT_AT[key] = now
    try:
        entries = []
        total = 0
        with os.scandir(cache_dir) as listing:
            for entry in listing:
                if not entry.is_file():
                    continue
                stat = entry.stat()
                entries.append((stat.st_mtime, stat.st_size, entry.path))
                total += stat.st_size
    except OSError:
        return 0
    if total <= budget:
        return 0
    # Down to four fifths, so a sweep is not triggered again by the next page.
    target = int(budget * 0.8)
    removed = 0
    for _, size, candidate in sorted(entries):
        if total - removed <= target:
            break
        try:
            os.unlink(candidate)
        except OSError:
            continue
        removed += size
    return removed


def render_file_cover_thumbnail(
    path: Path, member: str, max_dimension: int = COVER_THUMBNAIL_MAX_DIMENSION,
    cache_dir: Path | None = None, budget: int | None = None,
) -> bytes:
    """Extract and cache a web-sized JPEG without modifying the comic archive."""
    if cache_dir is None:
        cache_dir = cover_cache_dir()
        budget = COVER_CACHE_MAX_BYTES if budget is None else budget
    stat = path.stat()
    fingerprint = "\0".join(
        (
            str(path.resolve()), str(stat.st_mtime_ns), str(stat.st_size), member,
            str(max_dimension), str(COVER_THUMBNAIL_QUALITY),
        )
    )
    cache_key = hashlib.sha256(fingerprint.encode()).hexdigest()
    cached = cache_dir / f"{cache_key}.jpg"
    try:
        if cached.is_file():
            body = cached.read_bytes()
            # Touched on a hit, so the sweep above drops what is truly unused
            # rather than what was rendered longest ago.
            with contextlib.suppress(OSError):
                os.utime(cached)
            return body
    except OSError:
        pass

    source_bytes = read_archive_member(path, member, COVER_SOURCE_MAX_BYTES)
    body = normalize_image_to_jpeg(source_bytes, max_dimension)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_temp = cache_dir / f".{cache_key}-{threading.get_ident()}.tmp"
        cache_temp.write_bytes(body)
        os.replace(cache_temp, cached)
        if budget:
            sweep_image_cache(cache_dir, budget)
    except OSError:
        pass
    return body


def normalize_image_to_jpeg(source_bytes: bytes, max_dimension: int = COVER_THUMBNAIL_MAX_DIMENSION) -> bytes:
    """Create a bounded JPEG using Pillow on macOS, Linux, and Docker."""
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
        with Image.open(io.BytesIO(source_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((max_dimension, max_dimension))
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


# An import's copy in flight, written beside its destination and renamed into
# place: ".<stem>.flipparr-<pid>-<thread>.partial<ext>". Never one of the
# library's comics, though it ends in .cbz.
IMPORT_PARTIAL = re.compile(r"^\..+\.flipparr-\d+-\d+\.partial(?:\.[A-Za-z0-9]+)?$")


def _is_import_partial(name: str) -> bool:
    return bool(IMPORT_PARTIAL.match(name))


def _clear_stale_partials(destination: Path) -> None:
    """Remove copies of this comic an earlier process left half-written.

    A restart between the copy and its rename -- `docker stop` does not wait
    for the import worker -- leaves the copy behind, under a name no later
    import would look for. Nothing this process writes is older than its
    start, so whatever is was left by another (Docker gives every start the
    same process id, so the id in the name cannot tell them apart).
    """
    prefix = f".{destination.stem}.flipparr-"
    try:
        siblings = list(destination.parent.iterdir())
    except OSError:
        return
    for leftover in siblings:
        if not leftover.name.startswith(prefix) or not _is_import_partial(leftover.name):
            continue
        try:
            if leftover.is_file() and leftover.stat().st_mtime < PROCESS_STARTED_AT:
                leftover.unlink()
                log_event("import_partial_removed", path=str(leftover))
        except OSError as exc:
            log_event("import_partial_unremovable", level="warning", path=str(leftover), error=str(exc))


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
        and not _is_import_partial(p.name)
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


def discover_series(query: str, *, extended: bool = False) -> dict[str, Any]:
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
    metron = config.get("metron") or {}
    # Claimed before the library view is built, so a creator lookup that
    # arrived alongside this search cannot take Metron's next turn first.
    turn = _MetronTitleTurn() if metron.get("enabled") and metron.get("token") else None
    try:
        return _discover_series_with(cleaned, title_query, year_hint, config, turn, extended)
    finally:
        if turn:
            turn.release()


class _MetronTitleTurn:
    """A title search's claim on Metron's next request.

    Metron serves one request every 3.2s to everyone. A creator lookup fired
    alongside a title search could put its requests first and hold up the
    titles the reader is waiting on, so it waits for this -- the title
    search's one Metron request -- rather than for the whole search, whose
    Comic Vine and GCD halves have nothing to do with Metron.
    """

    def __init__(self) -> None:
        self._held = True
        with _METRON_TITLE_TURNS:
            _METRON_TITLE_WAITING[0] += 1

    def after(self, work: Callable[[], Any]) -> Any:
        try:
            return work()
        finally:
            self.release()

    def release(self) -> None:
        with _METRON_TITLE_TURNS:
            if self._held:
                self._held = False
                _METRON_TITLE_WAITING[0] -= 1
                _METRON_TITLE_TURNS.notify_all()


_METRON_TITLE_TURNS = threading.Condition()
_METRON_TITLE_WAITING = [0]


def _after_metron_title_searches(timeout: float = 10.0) -> None:
    """Let any title search in flight have Metron first, for up to `timeout`."""
    deadline = time.monotonic() + timeout
    with _METRON_TITLE_TURNS:
        while _METRON_TITLE_WAITING[0]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            _METRON_TITLE_TURNS.wait(remaining)


def _discovery_found_title(
    by_provider: dict[str, list[dict[str, Any]]], title_query: str, year_hint: int | None,
) -> bool:
    """Whether a catalog already answered with the title itself -- and, when
    the search named a year, a run begun within a year of it."""
    wanted = normalized_title(title_query)
    if not wanted:
        return False
    for rows in by_provider.values():
        for row in rows or []:
            if normalized_title(row.get("title")) != wanted:
                continue
            began = _provider_year(row.get("yearBegan"))
            if not year_hint or (began and abs(began - year_hint) <= 1):
                return True
    return False


def _discover_series_with(
    cleaned: str, title_query: str, year_hint: int | None, config: dict[str, Any],
    turn: _MetronTitleTurn | None, extended: bool = False,
) -> dict[str, Any]:
    """`extended` also asks whether the query names a creator or a publisher,
    and ranks those runs in with the titles. Discover wants that; Fix Match,
    which is looking for one run by its title, does not pay for it."""
    library = _discovery_library_view()
    searches: list[tuple[str, str, Any]] = []
    metron = config.get("metron") or {}
    if metron.get("enabled") and metron.get("token"):
        metron_search = functools.partial(
            discover_metron_series, cleaned, str(metron["token"]), library, hydrate=False)
        searches.append(("metron", "Metron", (
            functools.partial(turn.after, metron_search) if turn else metron_search)))
    comic_vine = config.get("comic_vine") or {}
    if comic_vine.get("enabled") and comic_vine.get("apiKey"):
        searches.append(("comic_vine", "Comic Vine", functools.partial(
            discover_comic_vine_series, cleaned, str(comic_vine["apiKey"]), library)))
    # GCD is the fallback, asked only when the catalogs above do not find the
    # title itself (owner, 2026-09-30). It is the one used without an
    # account and throttles hardest: asked on every search, a busy afternoon
    # of pulls had it refusing Flipparr for ten minutes. Asking it whenever
    # the others fail to find the exact title keeps why it was added -- a
    # loose Metron answer ("Saga of the Swamp Thing" for Saga) still reaches
    # GCD, the deepest catalog for older, indie and reprint runs.
    fallback = ("gcd", "Grand Comics Database", functools.partial(discover_gcd_series, cleaned, library))

    # Asked at the same time as the titles, so one answer holds everything;
    # its Metron requests wait for the title search's own (_MetronTitleTurn).
    people_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1) if extended else None
    people_future = people_pool.submit(
        _people_and_publishers, cleaned, title_query, year_hint, config, library,
    ) if people_pool else None

    by_provider: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    if searches:
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
    if not _discovery_found_title(by_provider, title_query, year_hint):
        searches.append(fallback)
        provider_id, name, search = fallback
        try:
            by_provider[provider_id] = (search() or {}).get("results") or []
        except Exception as exc:
            errors.append({"provider": name, "error": str(exc)})

    results = _merge_discovered_runs(by_provider, title_query, year_hint)
    people: dict[str, Any] = {}
    if people_future is not None:
        try:
            people = people_future.result() or {}
        except Exception as exc:
            people = {"errors": [{"lookup": "creators and publishers", "error": str(exc)}]}
        finally:
            people_pool.shutdown(wait=False)
        results = _rank_discovered(results, people, title_query, year_hint)
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
    if extended:
        result["creatorMatches"] = [match["name"] for match in people.get("creatorMatches") or []]
        result["publisherMatches"] = [
            {"name": match["name"], "year": match.get("year")}
            for match in people.get("publisherMatches") or []
        ]
        result["didYouMean"] = people.get("didYouMean") or []
        result["stillLooking"] = bool(people.get("stillLooking"))
    if errors:
        result["fallbacks"] = errors
    return result


def _rank_discovered(
    results: list[dict[str, Any]], people: dict[str, Any], title_query: str, year_hint: int | None,
) -> list[dict[str, Any]]:
    """Titles, a creator's runs and a publisher's, as one list most relevant first.

    An exact title leads; then the creator's runs, most credited first; then
    the publisher's that year; then titles that only resemble the query. A run
    found more than one way appears once, with every provider's id, and a run
    found through a person or a publisher says which, so a card whose title
    looks nothing like the query explains itself.
    """
    wanted = normalized_title(title_query)
    ranked: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[dict[str, Any]] = []

    def identity(run: dict[str, Any]) -> tuple[str, str]:
        return normalized_title(run.get("title")), str(run.get("yearBegan") or "")

    for run in results:
        title = normalized_title(run.get("title"))
        score = 100.0 if title == wanted else 70.0 * difflib.SequenceMatcher(None, title, wanted).ratio()
        # A year typed is part of what was asked: "Image 2012" is not Image+
        # from 2016, however exactly the title matches.
        if year_hint and run.get("yearBegan") and int(run["yearBegan"]) != year_hint:
            score -= 25 + min(abs(int(run["yearBegan"]) - year_hint), 15)
        item = dict(run, relevance=score, matchedBy=None)
        ranked.setdefault(identity(run), item)
        order.append(item)

    def add(runs: list[dict[str, Any]], best: float, floor: float, reason: str) -> None:
        for rank, run in enumerate(runs):
            score = max(floor, best - 2 * rank)
            existing = ranked.get(identity(run))
            if existing is None:
                item = dict(run, relevance=score, matchedBy=reason)
                item["providerIds"] = dict(run.get("providerIds") or {run.get("provider"): run.get("providerSeriesId")})
                ranked[identity(run)] = item
                order.append(item)
                continue
            if score > existing["relevance"]:
                existing["relevance"] = score
                existing["matchedBy"] = existing.get("matchedBy") or reason
            existing["providerIds"] = {**(run.get("providerIds") or {}), **(existing.get("providerIds") or {})}
            existing["inLibrary"] = existing.get("inLibrary") or run.get("inLibrary")
            for field in ("cover", "publisher", "issueCount", "medium"):
                if not existing.get(field) and run.get(field):
                    existing[field] = run[field]

    for match in people.get("creatorMatches") or []:
        add(match.get("runs") or [], 90, 60, f"By {match['name']}")
    for match in people.get("publisherMatches") or []:
        add(match.get("runs") or [], 75, 45, f"{match['name']}, {match['year']}" if match.get("year") else match["name"])
    # Stable, so equally relevant titles keep the order the merge gave them.
    return sorted(order, key=lambda item: -item["relevance"])


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
    _after_metron_title_searches()
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
    _after_metron_title_searches()
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

    Discover asks this inside the title search (discover_series with
    `extended`), so one answer ranks everything together. Metron takes one
    request every 3.2s from everyone, so the Metron lookups here wait for the
    title search's own Metron request (_MetronTitleTurn); Comic Vine's starts
    at once.
    """
    cleaned, title_query, year_hint = _discovery_query_parts(query)
    if len(cleaned) < 2:
        raise ValueError("Enter at least two characters to discover a series")
    return _people_and_publishers(
        cleaned, title_query, year_hint, load_provider_config(), _discovery_library_view(),
    )


def _people_and_publishers(
    cleaned: str, title_query: str, year_hint: int | None,
    config: dict[str, Any], library: dict[str, Any],
) -> dict[str, Any]:
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
            "year": _provider_year(item.get("year")),
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
        # And by the provider's own id for the run, once it is linked there.
        linked = item.get("issueCatalog") or {}
        if linked.get("provider") and linked.get("providerSeriesId"):
            relevance[f"{linked['provider']}:{linked['providerSeriesId']}"] = entry
    return relevance


def _library_run_for(
    relevance: dict[str, dict[str, Any]], title: str, year: Any,
    provider: str | None = None, provider_run_id: Any = None,
) -> dict[str, Any] | None:
    """The library run a provider's run is, or None when it is a different one.

    A run the library has linked at the provider is that run, whatever it is
    called. Otherwise title and year: the same year, or a year either side
    of it, because Metron's `year_began` is a cover-date year and a filename's
    is usually the release year, and cover dates run ahead of the shelf. A
    title alone matches only a library run with no year to disagree, since
    "Green Lantern" (2011) and "Green Lantern" (2023) share a name and nothing
    else, and calling the 2023 issues "In library" because the 2011 run is
    put the wrong era on the shelf (owner, 2026-09-28).
    """
    if provider and provider_run_id:
        entry = relevance.get(f"{provider}:{provider_run_id}")
        if entry:
            return entry
    key = normalized_title(title)
    if not key:
        return None
    year = _provider_year(year)
    if year is None:
        return relevance.get(key)
    for candidate in (year, year - 1, year + 1):
        entry = relevance.get(f"{key}|{candidate}")
        if entry:
            return entry
    entry = relevance.get(key)
    return entry if entry and entry.get("year") is None else None


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
        known = _library_run_for(relevance, entry["seriesTitle"], entry.get("seriesYear"),
                                 "metron", entry.get("providerSeriesId"))
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
    """Three shelves: the week coming, the week that shipped, and the one before.

    The week before last is there because a comic is easy to miss by a few
    days: by the time you look, the shelf it was on has moved up. It costs no
    more than the others after the first look -- a settled week is answered
    from the provider cache, and a fortnight-old week never changes again.
    """
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
    previous_start, previous_end = _ship_week(anchor, weeks_back=2)
    token = str(metron["token"])
    # One projection for both shelves; building it is not free.
    relevance = _library_relevance()
    shelves = {}
    for name, (start, end) in (
        ("latest", (latest_start, latest_end)),
        ("upcoming", (upcoming_start, upcoming_end)),
        ("previous", (previous_start, previous_end)),
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


def _comic_vine_issue_description(provider_issue_id: str) -> str | None:
    """Comic Vine's blurb for one issue, asked for only when Metron has none."""
    credential = _provider_credential("comic_vine")
    url = f"{COMIC_VINE_API_BASE}/issue/4000-{provider_issue_id}/?" + urllib.parse.urlencode({
        "api_key": credential, "format": "json", "field_list": "deck,description",
    })
    # An issue's description is a wiki article like a volume's, so the deck or
    # the opening paragraph is the part that says what the issue is about.
    return _comic_vine_synopsis(
        fetch_provider_json("comic_vine", url, credential).get("results") or {}
    )


_EMPTY_ISSUE_DETAIL: dict[str, Any] = {
    "storyTitles": [], "description": None, "creators": [],
    "pageCount": None, "price": None, "coverDate": None, "storeDate": None,
}


def issue_detail(issue_id: int) -> dict[str, Any]:
    """What a catalog says about one of our own issues.

    Fetched when a screen opens rather than stored, for `series_synopsis`'s
    reason: a blurb is not worth a migration and a re-sync of every issue to
    backfill it. Metron answers with the whole shape; Comic Vine is asked only
    for a description, and only when Metron has not already given one. Between
    them they reach 1,480 of the library's 1,482 issues, where Metron alone
    reaches 1,350 -- which is why this does not stop at one provider.

    `status` is the whole point of the contract. A provider being down,
    rate-limited or unconfigured is not the client's mistake and must never
    arrive as a 4xx: every failure is a 200 saying `unavailable`, on a screen
    that reads perfectly well without a blurb.
    """
    ids = catalog_store().issue_provider_ids(issue_id)
    detail: dict[str, Any] = {
        "issueId": str(issue_id), "provider": None, "providerName": None, **_EMPTY_ISSUE_DETAIL,
    }
    asked = False
    failed = False
    for provider in _SYNOPSIS_ORDER:
        provider_id = str(ids.get(provider) or "")
        if detail["description"] or not re.fullmatch(r"\d+", provider_id):
            continue
        asked = True
        try:
            if provider == "metron":
                detail.update(discovered_issue_detail(provider_id))
                detail.pop("providerIssueId", None)
            else:
                detail["description"] = _comic_vine_issue_description(provider_id)
        except Exception:  # noqa: BLE001 -- see the docstring: a blurb is not an error page
            failed = True
            continue
        if detail["description"]:
            detail["provider"] = provider
            detail["providerName"] = _DISCOVERY_PROVIDERS.get(provider, provider)
    if detail["description"] or (asked and not failed):
        # Everyone we could ask answered, even if the answer was "nothing known".
        detail["status"] = "ok"
    elif failed:
        detail["status"] = "unavailable"
    else:
        detail["status"] = "unmatched"
    return detail


def _file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def _page_is_spread(image: bytes) -> bool:
    """A double-page spread: a page noticeably wider than it is tall."""
    try:
        from PIL import Image, UnidentifiedImageError
        with Image.open(io.BytesIO(image)) as picture:
            width, height = picture.size
    except (OSError, ValueError, UnidentifiedImageError):
        return False
    return bool(height) and width > height * 1.15


_SCANNER_CREDIT_PAGE = re.compile(
    r"(?:^|[_. -])(?:scan(?:ned|ner|s)?|tag|credits?|zzz+)(?:[_. -]|$)", re.I
)


def _is_scanner_credit(member: str) -> bool:
    """A scan group's credit image rather than a page of the comic.

    Groups name it to sort after the last page -- "zKizz.jpg", "z_GD-Pmack.jpg"
    -- and it is often wide, so without this it is the "spread" a run's header
    ends up showing.
    """
    stem = Path(member).stem
    return (stem[:1].casefold() == "z" and not re.search(r"\d", stem)) or bool(
        _SCANNER_CREDIT_PAGE.search(stem)
    )


def automatic_backdrop_page(path: Path) -> str | None:
    """The page an issue lends the drawer's header when nobody has chosen one.

    The first double-page spread after the cover suits a header that is wider
    than it is tall. Without one, a page a third of the way in is past the
    credits and the opening splash. The cover is only used when it is all
    there is -- the drawer already shows it beside the title.
    """
    pages = [member for member in archive_page_members(path) if not _is_scanner_credit(member)]
    if len(pages) < 2:
        return pages[0] if pages else None
    interior = pages[1:]
    for member in interior[:BACKDROP_SPREAD_SCAN_LIMIT]:
        try:
            image = read_archive_member(path, member)
        except Exception:  # noqa: BLE001 -- one unreadable page is not a reason to stop looking
            continue
        if _page_is_spread(image):
            return member
    return interior[min(len(interior) - 1, len(pages) // 3)]


# A page render lists the archive's members to find the page asked for, and a
# reader asks for page after page. Listing a zip is cheap; listing a CBR is a
# `bsdtar` process that scans the whole file, so remembering the answer for as
# long as the file is unchanged is what keeps paging quick.
#
# Measured on the NAS (2026-09-20, 600px, cold cache): 110ms a page from a CBR
# against 90ms from a CBZ. The subprocess costs about 20ms on these archives,
# which is why pages are still read one at a time rather than the whole
# archive being unpacked for a reading session.
_PAGE_MEMBER_CACHE: dict[str, tuple[str, list[str]]] = {}
_PAGE_MEMBER_CACHE_LIMIT = 64
_PAGE_MEMBER_LOCK = threading.Lock()


def cached_page_members(path: Path) -> list[str]:
    """`archive_page_members`, remembered while the file on disk is unchanged."""
    key = str(path)
    try:
        signature = _file_signature(path)
    except OSError:
        return archive_page_members(path)
    with _PAGE_MEMBER_LOCK:
        remembered = _PAGE_MEMBER_CACHE.get(key)
        if remembered and remembered[0] == signature:
            return remembered[1]
    members = archive_page_members(path)
    with _PAGE_MEMBER_LOCK:
        if len(_PAGE_MEMBER_CACHE) >= _PAGE_MEMBER_CACHE_LIMIT:
            _PAGE_MEMBER_CACHE.pop(next(iter(_PAGE_MEMBER_CACHE)), None)
        _PAGE_MEMBER_CACHE[key] = (signature, members)
    return members


# The sizes a page may be asked for. A caller that asks for anything else is
# refused rather than quietly served a thumbnail: each size is a separate set
# of cached files, so an open list of sizes is an open-ended cache.
PAGE_SIZES = {
    "": COVER_THUMBNAIL_MAX_DIMENSION,
    "backdrop": BACKDROP_MAX_DIMENSION,
    "read": READING_PAGE_MAX_DIMENSION,
}


def _page_url(file_id: str, index: int, path: Path, size: str = "") -> str:
    query = {"v": _file_signature(path)}
    if size:
        query["size"] = size
    return f"/api/v1/files/{file_id}/pages/{index}?" + urllib.parse.urlencode(query)


def series_backdrop(series_run_id: int) -> dict[str, Any]:
    """The page behind a run's drawer header: the one chosen, or one found."""
    store = catalog_store()
    files = store.series_backdrop_files(series_run_id)
    by_id = {item["id"]: item for item in files}
    preference = store.series_backdrop_preference(series_run_id)
    if preference and preference["fileId"] in by_id:
        path = Path(by_id[preference["fileId"]]["path"])
        try:
            signature = _file_signature(path)
        except OSError:
            signature = None
        # A chosen page stands until it is changed; an automatic one is looked
        # for again when its file changes underneath it.
        if signature and (preference["source"] == "chosen" or preference["fileSignature"] == signature):
            pages = cached_page_members(path)
            if preference["member"] in pages:
                index = pages.index(preference["member"])
                return {
                    "url": _page_url(preference["fileId"], index, path, "backdrop"),
                    "source": preference["source"], "fileId": preference["fileId"], "page": index,
                }
    for item in files[:3]:
        path = Path(item["path"])
        try:
            if not path.is_file() or archive_kind(path) is None:
                continue
            member = automatic_backdrop_page(path)
            signature = _file_signature(path)
        except OSError:
            continue
        if not member:
            continue
        store.set_series_backdrop(series_run_id, int(item["id"]), member, "auto", signature)
        index = cached_page_members(path).index(member)
        return {
            "url": _page_url(item["id"], index, path, "backdrop"),
            "source": "auto", "fileId": item["id"], "page": index,
        }
    return {"url": None, "source": "none", "fileId": None, "page": None}


def choose_series_backdrop(series_run_id: int, file_id: str, page: int) -> dict[str, Any]:
    store = catalog_store()
    item = next(
        (entry for entry in store.series_backdrop_files(series_run_id) if entry["id"] == str(file_id)),
        None,
    )
    if item is None:
        raise ValueError("That comic is not part of this run")
    path = Path(item["path"])
    pages = archive_page_members(path)
    if not 0 <= page < len(pages):
        raise ValueError("That page is not in this comic")
    store.set_series_backdrop(series_run_id, int(item["id"]), pages[page], "chosen", _file_signature(path))
    return series_backdrop(series_run_id)


def next_unread_file(run_files: list[dict[str, Any]], after_file_id: str, progress: dict[str, Any]) -> dict[str, Any] | None:
    """The next comic in a run nobody has started, after the one just finished.

    Finishing an issue is the moment to offer the next one; offering the issue
    just read instead is what makes a "continue" shelf feel like it is not
    paying attention. A comic already started is skipped, because it is
    offered on its own account.
    """
    seen = False
    for item in run_files:
        if str(item["id"]) == str(after_file_id):
            seen = True
            continue
        if not seen:
            continue
        record = progress.get(str(item["id"]))
        if record is None:
            return item
    return None


READING_STATES = {"unstarted", "continue", "next", "finished", "volume-only", "none"}


def reading_target(
    issues: list[dict[str, Any]], volumes: list[dict[str, Any]], progress: dict[str, Any],
) -> dict[str, Any]:
    """Which comic a run's Read button opens, and what state the run is in.

    Decided here rather than in the browser because the Comics grid needs one
    of these per run and cannot be sent every run's file list to work it out.
    The frontend only words the answer (`src/reading-target.js`).

    Two of the six states are arguments rather than mechanics:

    `finished` resolves to the comic read last, from its first page, instead of
    the newest issue. Mihon's one button always jumps to the first unread, and
    its own users complain that going back to re-read a run is ignored.

    `volume-only` offers the collection and is labelled as one. Nothing records
    where an issue begins inside an omnibus, so an offer of "#7" that opens
    page 1 of 1,100 pages would be a lie.
    """
    def place(item: dict[str, Any]) -> dict[str, Any] | None:
        record = progress.get(str(item["id"]))
        if not record or record.get("stale"):
            # A place kept against a file replaced since it was read points at
            # a page that may not exist. It is not a place, and not evidence
            # that the comic was ever started.
            return None
        if not record["page"] and not record["finishedAt"]:
            # Opened, nothing turned. The reader does not write this -- it
            # saves nothing until a page moves -- but a cleared record and an
            # older client both can, and "Continue Series, page 1 of 47" is
            # not a thing anyone wants to be offered.
            return None
        return record

    def answer(state: str, item: dict[str, Any] | None, record: dict[str, Any] | None = None) -> dict[str, Any]:
        if item is None:
            return {"state": "none", "fileId": None, "issueNumber": None, "page": 0, "pageCount": 0}
        return {
            "state": state, "fileId": str(item["id"]), "issueNumber": item.get("issueNumber"),
            "filename": item.get("filename"), "volumeLabel": item.get("volumeLabel"),
            "page": int(record["page"]) if record else 0,
            "pageCount": int(record["pageCount"]) if record else 0,
        }

    comics = [item for item in issues if item.get("readable", True)]
    if not comics:
        editions = [item for item in volumes if item.get("readable", True)]
        return answer("volume-only", editions[0]) if editions else answer("none", None)

    resume = last = None
    for item in comics:
        record = place(item)
        if record is None:
            continue
        if last is None or str(record.get("updatedAt") or "") > str(last[1].get("updatedAt") or ""):
            last = (item, record)
        if not record.get("finishedAt") and (
            resume is None or str(record.get("updatedAt") or "") > str(resume[1].get("updatedAt") or "")
        ):
            resume = (item, record)

    # A comic left part-read is the answer whatever else is true: it is the one
    # thing that can be resumed rather than begun.
    if resume:
        return answer("continue", resume[0], resume[1])
    if last is None:
        return answer("unstarted", comics[0])

    after = [item for item in comics[comics.index(last[0]) + 1:] if place(item) is None]
    if after:
        return answer("next", after[0])
    # Everything readable has been read, so the offer is the run again from
    # its first issue -- which is what "Restart" means.
    return answer("finished", comics[0])


def series_reading(series_run_id: int, *, user_id: int) -> dict[str, Any]:
    """A run's comics, what is readable, and where each was left.

    `readable` is sniffed here rather than stored: opening a drawer is a
    deliberate act against a few dozen files, and `archive_kind` reads 264
    bytes. The alternative -- a column on every file -- is a schema change
    bought for a flag that only this surface asks about.
    """
    store = catalog_store()
    files = store.run_reading_files(series_run_id)
    progress = store.reading_progress_for_run(series_run_id, user_id=user_id)

    def described(item: dict[str, Any]) -> dict[str, Any]:
        record = progress.get(str(item["id"])) or {}
        path = Path(item["path"])
        return {
            **item,
            "readable": path.is_file() and archive_kind(path) is not None,
            "page": record.get("page", 0), "pageCount": record.get("pageCount", 0),
            "finishedAt": record.get("finishedAt"), "stale": record.get("stale", False),
        }

    issues = [described(item) for item in files["issues"]]
    volumes = [described(item) for item in files["volumes"]]
    return {
        "seriesRunId": str(series_run_id), "medium": files["medium"],
        "readingDirection": files["readingDirection"],
        "resume": reading_target(issues, volumes, progress),
        "issues": [{k: v for k, v in item.items() if k != "path"} for item in issues],
        "volumes": [{k: v for k, v in item.items() if k != "path"} for item in volumes],
    }


def reading_by_run(*, user_id: int) -> dict[str, Any]:
    """Where each run in the library was left, for the Comics grid.

    Only runs with a reading history appear. A card for a run nobody has
    opened wants a plain "Read", and that is resolved when it is clicked --
    working it out here would mean sniffing every archive in the library to
    find which comic is readable, on a request the grid makes on every visit.
    """
    store = catalog_store()
    owned = store.issue_file_counts_by_run()
    runs = {}
    for run_id, progress in store.reading_progress_by_run(user_id=user_id).items():
        live = {
            file_id: record for file_id, record in progress.items() if not record["stale"]
        }
        if not live:
            continue
        started = [record for record in live.values() if not record["finishedAt"]]
        latest = max(
            started or live.values(), key=lambda record: str(record["updatedAt"] or ""),
        )
        file_id = next(key for key, record in live.items() if record is latest)
        # The drawer's three states, so a cover says the same thing the run's
        # button does. "Between issues" used to read as finished, and the cover
        # of a run with one issue read out of twenty-five offered Restart.
        finished_count = len(live) - len(started)
        remaining = max(0, owned.get(run_id, 0) - finished_count)
        if started:
            state = "continue"
        elif remaining:
            state = "next"
        else:
            state = "finished"
        runs[run_id] = {
            "state": state,
            "fileId": file_id, "issueNumber": latest["issueNumber"],
            "page": latest["page"], "pageCount": latest["pageCount"],
            "lastReadAt": max(str(record["updatedAt"] or "") for record in live.values()) or None,
            # For the card's marker: a check once every issue is read, else
            # how many are left of a run that has been started.
            "read": finished_count, "total": int(owned.get(run_id, 0)),
        }
    return {"runs": runs}


def continue_reading(limit: int = 12, *, user_id: int) -> dict[str, Any]:
    """What to carry on with: what is part-read, then what comes next.

    Each run appears once. A part-read comic is the run's entry; a run whose
    last read comic is finished offers the next issue instead.
    """
    store = catalog_store()
    recent = store.recent_reading(limit * 3, user_id=user_id)
    progress_by_file = {item["fileId"]: item for item in recent}
    items: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    for record in recent:
        run_id = record["seriesRunId"]
        key = run_id or f"file:{record['fileId']}"
        if key in seen_runs:
            continue
        if not record["finishedAt"]:
            seen_runs.add(key)
            items.append({**record, "resume": "continue"})
            continue
        if not run_id:
            continue
        try:
            # Issues only, in issue order. The backdrop picker's list sorts
            # every volume to the end, which is how this used to offer an
            # omnibus as the next issue of a run.
            run_files = store.run_reading_files(int(run_id))["issues"]
        except LookupError:
            continue
        following = next_unread_file(run_files, record["fileId"], progress_by_file)
        if following is None:
            continue
        seen_runs.add(key)
        items.append({
            "fileId": str(following["id"]), "filename": following["filename"],
            "issueNumber": following.get("issueNumber"),
            "seriesRunId": run_id, "seriesTitle": record["seriesTitle"], "medium": record["medium"],
            "page": 0, "pageCount": 0, "finishedAt": None,
            "updatedAt": record["updatedAt"], "resume": "next",
        })
    return {"items": items[:limit]}


def file_pages(file_id: int) -> dict[str, Any]:
    path = catalog_store().library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Pages can only be read from comic archives")
    pages = cached_page_members(path)
    return {
        "fileId": str(file_id), "pageCount": len(pages),
        # `url` is the thumbnail the page picker draws; `readUrl` is the same
        # page at reading size. Both are given so the picker is untouched.
        "pages": [
            {
                "index": index,
                "url": _page_url(str(file_id), index, path),
                "readUrl": _page_url(str(file_id), index, path, "read"),
            }
            for index in range(len(pages))
        ],
    }


def vision_provider() -> str | None:
    """Which vision connector is on and keyed: the lowest priority number wins. Never raises."""
    config = load_provider_config()
    ready = [
        provider for provider in VISION_PROVIDERS
        if (config.get(provider) or {}).get("enabled") and str((config.get(provider) or {}).get("apiKey") or "").strip()
    ]
    if not ready:
        return None
    return min(ready, key=lambda provider: (int((config.get(provider) or {}).get("priority") or 99), VISION_PROVIDERS.index(provider)))


class VisionUnavailable(RuntimeError):
    """The connector did not answer: a failed call, a refusal by the service, a cool-down.

    Not the same as an answer nobody could use. A page this was raised for
    has not been looked at, and is asked again once the connector is back.
    """


# How long a connector is left alone after it says the account is out of
# credit. A rate limit passes in seconds; an empty balance does not, and a
# page turn should not cost a doomed request each time.
VISION_QUOTA_COOLDOWN_SECONDS = 15 * 60
_VISION_QUOTA_CODES = {"insufficient_quota", "credit_balance_exhausted", "billing_error", "billing_not_active"}


def _provider_cooling_down(provider_id: str) -> bool:
    """Whether the provider is held off for longer than its ordinary pacing."""
    with _PROVIDER_REQUEST_LOCK:
        held = _PROVIDER_NEXT_REQUEST_AT.get(provider_id, 0.0) - time.monotonic()
    return held > _PROVIDER_MIN_INTERVAL_SECONDS.get(provider_id, 1.0)


def vision_model_ready() -> bool:
    """Whether a vision connector is on, has a key, and is not cooling down. Never raises.

    A connector that has just been refused -- a rate limit, an account out of
    credit -- is not ready: the reader would otherwise wait out the cool-down
    on a page turn. The page is read by the lower tiers and asked once the
    connector is back.
    """
    provider = vision_provider()
    return provider is not None and not _provider_cooling_down(provider)


def _vision_request(provider: str, credential: str, image: bytes, prompt: str,
                    examples: list[tuple[bytes, str]] | None = None) -> tuple[str, bytes]:
    """The endpoint and body for one page and one question, in the provider's own shape.

    Examples, when there are any, come first: each an image and the text
    that describes it, then the page and the question.
    """
    pairs = [*(examples or []), (image, prompt)]
    if provider == "anthropic":
        content: list[dict[str, Any]] = []
        for picture, text in pairs:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": base64.b64encode(picture).decode("ascii")}})
            content.append({"type": "text", "text": text})
        return f"{ANTHROPIC_API_BASE}/messages", json.dumps({
            "model": VISION_MODEL, "max_tokens": 1024, "messages": [{"role": "user", "content": content}],
        }).encode()
    content = []
    for picture, text in pairs:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(picture).decode('ascii')}"}})
        content.append({"type": "text", "text": text})
    return f"{OPENAI_API_BASE}/chat/completions", json.dumps({
        "model": VISION_MODEL_OPENAI, "max_tokens": 1024, "messages": [{"role": "user", "content": content}],
    }).encode()


def _vision_answer(provider: str, payload: dict[str, Any]) -> str:
    """The text of the model's answer, in the provider's own shape."""
    if provider == "anthropic":
        return "".join(
            str(block.get("text") or "") for block in (payload.get("content") or []) if isinstance(block, dict)
        )
    choices = payload.get("choices") or []
    message = (choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


def ask_vision_model(image: bytes, prompt: str, examples: list[tuple[bytes, str]] | None = None) -> str:
    """One question about one page image, answered as text, by whichever connector is on.

    A POST with the page as a base64 JPEG -- Anthropic's Messages API or
    OpenAI's Chat Completions, same question either way. Paced with the
    provider's slot like every other upstream call, and a 429 cools the
    provider down; the caller decides what a failure means, which for panel
    view is always "keep what the lower tiers found".
    """
    provider = vision_provider()
    if provider is None:
        raise VisionUnavailable("No vision model is configured")
    credential = _provider_credential(provider)
    url, body = _vision_request(provider, credential, image, prompt, examples)
    headers = {**_provider_headers(provider, credential), "Content-Type": "application/json"}
    _wait_for_provider_slot(provider)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    # Opened directly rather than through _safe_urlopen: the URL carries no
    # credential (the key is a header), and the error body is what says why
    # the service refused -- a rate limit and an account out of credit are
    # both a 429, and only one of them passes in a minute.
    try:
        with urllib.request.urlopen(request, timeout=VISION_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
        _VISION_TROUBLE_TOLD.pop(provider, None)  # it answers again: the next outage is news
    except urllib.error.HTTPError as exc:
        code, said = _vision_error(exc)
        if code in _VISION_QUOTA_CODES:
            tell_admins_of_connector_trouble(provider, "out_of_credit")
        elif exc.code == 401:
            tell_admins_of_connector_trouble(provider, "key_refused")
        if exc.code == 429 or code in _VISION_QUOTA_CODES:
            headers_seen = dict(exc.headers.items()) if exc.headers else {}
            # OpenAI says "try again in 1s" while its token window has a
            # minute to run; the window's own reset is the honest wait.
            retry_after = max(_provider_retry_after(provider, headers_seen) or 30, _window_reset_seconds(headers_seen))
            if code in _VISION_QUOTA_CODES:
                retry_after = max(retry_after, VISION_QUOTA_COOLDOWN_SECONDS)
            _cool_down_provider_requests(provider, retry_after)
        # What the service said, not only its code: "invalid_request_error"
        # alone could not say why one page in a sitting was refused.
        log_event("vision_request_failed", "warning", provider=provider, status=exc.code, code=code,
                  detail=support_safe(said)[:300] or None)
        raise VisionUnavailable(f"{provider} answered {exc.code}" + (f" ({code})" if code else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log_event("vision_request_failed", "warning", provider=provider, error=type(exc).__name__)
        raise VisionUnavailable(f"{provider} could not be reached") from None
    except ValueError as exc:
        log_event("vision_request_failed", "warning", provider=provider, error="unreadable response")
        raise VisionUnavailable(f"{provider} answered with something other than JSON") from exc
    if credential in json.dumps(payload, ensure_ascii=False):
        raise VisionUnavailable("Provider returned unsafe credential-bearing data")
    return _vision_answer(provider, payload)


def _window_reset_seconds(headers: dict[str, Any]) -> int:
    """How long until an exhausted rate-limit window refills, from OpenAI's own headers, else 0.

    `x-ratelimit-remaining-tokens: 0` beside `x-ratelimit-reset-tokens:
    1m17.49s` (or `51.833s`, or `876ms`); the same pair for requests.
    """
    lowered = {str(key).casefold(): str(value) for key, value in (headers or {}).items()}
    longest = 0
    for kind in ("tokens", "requests"):
        remaining = lowered.get(f"x-ratelimit-remaining-{kind}")
        if remaining is None or remaining.strip() != "0":
            continue
        longest = max(longest, int(_duration_seconds(lowered.get(f"x-ratelimit-reset-{kind}", "")) + 0.999))
    return longest


def _duration_seconds(text: str) -> float:
    """`1m17.49s`, `51.833s`, `876ms`, `3h47m45.167s` as seconds; unreadable is 0."""
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", text or ""):
        total += float(amount) * {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}[unit]
    return total


# Which connectors the admins have been told are in trouble, and why: one
# notification an outage, not one a page. Cleared by the next answer.
_VISION_TROUBLE_TOLD: dict[str, str] = {}
CONNECTOR_NAMES = {"anthropic": "Claude", "openai": "ChatGPT"}


def tell_admins_of_connector_trouble(provider: str, reason: str) -> None:
    """Put it in every admin's bell that an AI connector has stopped for a
    reason only they can fix -- its account is out of credit, or its key was
    refused. Neither service lets a key ask for its balance (checked
    2026-10-05), so "running low" cannot be known; "out" is what the refusal
    says. Never raises: the page being read falls back either way."""
    if _VISION_TROUBLE_TOLD.get(provider) == reason:
        return
    _VISION_TROUBLE_TOLD[provider] = reason
    try:
        store = catalog_store()
        for user in store.list_users():
            if user["role"] == "admin" and not user["disabled"]:
                store.record_notification(int(user["id"]), "connector_trouble", f"connector:{provider}:{reason}",
                                          {"provider": provider, "reason": reason}, merge=True)
    except Exception as exc:  # noqa: BLE001
        log_exception("connector_notification_failed", exc, level="warning", provider=provider)


def _vision_error(exc: urllib.error.HTTPError) -> tuple[str, str]:
    """The service's own name for a refusal and what it said about it, from
    the error body; empty strings when there is none."""
    try:
        raw = exc.read(4096)
    except Exception:  # noqa: BLE001 -- a body that cannot be read is no code
        return "", ""
    try:
        error = json.loads(raw.decode("utf-8", "replace")).get("error") or {}
    except (ValueError, AttributeError):
        return "", ""
    if not isinstance(error, dict):
        return "", ""
    code, said = str(error.get("code") or error.get("type") or ""), str(error.get("message") or "")
    # Anthropic says an empty balance as a 400 "invalid_request_error" whose
    # only tell is its message ("Your credit balance is too low..."). Taken
    # for an ordinary refusal, every page opened asked again and waited on a
    # call that could not succeed (2026-10-05).
    if code == "invalid_request_error" and "credit balance" in said.lower():
        code = "credit_balance_exhausted"
    return code, said


def _vision_error_code(exc: urllib.error.HTTPError) -> str:
    return _vision_error(exc)[0]


def vision_panels(image: bytes, width: int, height: int, direction: str, mask: Any,
                  examples: list[tuple[bytes, str]] | None = None) -> list[dict[str, Any]] | None:
    """The panels a vision model sees on a page, or None.

    "No panels" is an answer: a splash, a cover, a pin-up is one panel the
    size of the page, and the reader shows it whole rather than in quarters.
    Boxes are believed only once the page has corrected them against its
    own gutters (`refine_vision_boxes`, with the `mask` the cut read the
    page with): a model gets the structure right and the edges roughly, and
    a grid it guessed is refused. None is an answer nobody could use, and
    then the quadrants stand. A connector that did not answer at all raises
    VisionUnavailable, and the page is not counted as asked.
    """
    examples = list(examples or [])
    text = ask_vision_model(image, panel_finder.vision_boxes_prompt(width, height, direction, len(examples)), examples)
    boxes = panel_finder.parse_vision_boxes(text, width, height)
    if boxes is None:
        return None
    # Boxes that lie over each other -- a diagonal pair, a panel seen twice
    # -- are one field, read at once.
    boxes = panel_finder.merge_overlapping(boxes)
    if len(boxes) <= 1:
        return [dict(panel_finder.WHOLE_PAGE)]
    if not panel_finder.accept_vision_boxes(boxes):
        return None
    refined = panel_finder.refine_vision_boxes(mask, boxes)
    if refined is None:
        return None
    # A panel the model left out comes back from the ink no box covers; a
    # reading that still leaves a tenth of the ink uncovered is a guess.
    refined = panel_finder.add_leftover_panels(mask, refined)
    if panel_finder.ink_outside(mask, refined) > panel_finder.INK_UNCOVERED_MAX:
        return None
    return refined


VISION_EXAMPLES_MAX = 2


def vision_examples(file_id: int, store: Any) -> list[tuple[bytes, str]]:
    """A reader's own hand-fixed pages from the same comic, as examples for the model.

    The one thing the model can learn from a person's fixes without being
    trained: how this artist cuts a page. Each example is the page with its
    panels outlined and numbered in reading order, with the answer in the
    form the model is asked for. Two at most, the nearest first; a spread
    is not an example of a page. Anything that cannot be rendered is left
    out rather than failing the page being read.
    """
    from PIL import Image, ImageDraw

    examples: list[tuple[bytes, str]] = []
    for row in store.manual_page_panels_near(file_id, VISION_EXAMPLES_MAX):
        try:
            path = store.library_file_path(row["fileId"])
            members = cached_page_members(path)
            index = members.index(row["member"])
            with Image.open(io.BytesIO(render_file_page(int(row["fileId"]), index, "backdrop"))) as image:
                if panel_finder.is_spread(*image.size):
                    continue
                page = image.convert("RGB")
        except (ValueError, LookupError, OSError, KeyError, zipfile.BadZipFile):
            continue
        width, height = page.size
        draw = ImageDraw.Draw(page)
        stroke = max(3, width // 200)
        answer = []
        for number, panel in enumerate(panel_finder.order_panels(row["panels"]), start=1):
            box = (int(panel["x"] * width), int(panel["y"] * height),
                   int(round((panel["x"] + panel["w"]) * width)), int(round((panel["y"] + panel["h"]) * height)))
            draw.rectangle(box, outline=(0, 220, 0), width=stroke)
            draw.rectangle((box[0], box[1], box[0] + 12 * stroke, box[1] + 8 * stroke), fill=(0, 220, 0))
            draw.text((box[0] + 2 * stroke, box[1] + stroke), str(number), fill="black")
            answer.append({"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]})
        buffer = io.BytesIO()
        page.save(buffer, format="JPEG", quality=85)
        examples.append((buffer.getvalue(), (
            f"Example {len(examples) + 1}: a page from the same comic, {width} pixels wide and {height} pixels tall, "
            f"its panels marked by a reader. Its answer is {json.dumps(answer)}"
        )))
    return examples


def vision_order(image: bytes, panels: list[dict[str, Any]], direction: str) -> list[int] | None:
    """The reading order a vision model gives a layout row-major cannot settle, or None.

    Raises VisionUnavailable when the connector did not answer.
    """
    text = ask_vision_model(image, panel_finder.vision_order_prompt(panels, direction))
    return panel_finder.parse_vision_order(text, len(panels))


_PANEL_MODEL: dict[str, Any] = {}


def panel_model_session() -> Any:
    """The optional panel detector, probed once. None is the ordinary case."""
    if "session" not in _PANEL_MODEL:
        _PANEL_MODEL["session"] = panel_finder.load_model_session(os.environ.get("FLIPPARR_PANEL_MODEL"))
        # ONNX Runtime aborts the interpreter if a session is still alive
        # when the modules are torn down (a recursive_mutex failure, seen on
        # macOS). Let it go while the interpreter is still whole.
        atexit.register(_PANEL_MODEL.clear)
    return _PANEL_MODEL["session"]


def file_page_panels(file_id: int, index: int, *, allow_vision: bool = True) -> dict[str, Any]:
    """Where the panels are on one page, in the order its run reads.

    Read from the 600px render the first time a page is asked about and kept,
    so a comic is segmented once, as it is read -- on demand, never as a
    library sweep. An automatic reading is redone when the file changes
    underneath it; a person's, like a chosen backdrop, stands. An unsegmented
    page answers with no panels, and the reader draws its four quadrants.
    """
    from PIL import Image

    store = catalog_store()
    path = store.library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Pages can only be read from comic archives")
    pages = cached_page_members(path)
    if not 0 <= index < len(pages):
        raise LookupError("That page is not in this comic")
    member = pages[index]
    signature = _file_signature(path)
    record = store.page_panels(file_id, member)
    session = panel_model_session()
    # A reader's page opens ask the vision connector only when the admin allows it.
    vision = allow_vision and vision_model_ready()
    # Whether the connector reads every page, or only what the local tiers
    # could not: a local reading can be wrong while sure, and only a model
    # questions it.
    every = vision and bool(load_app_settings().get("visionReadsEveryPage"))
    direction = store.file_reading_direction(file_id)
    # A reading is redone when the file changed underneath it; a reading made
    # without a tier is redone once that tier is there -- the detector for an
    # "auto" row, the vision connector for a row it could still help; a
    # person's stands regardless. Every automatic source counts as a machine's.
    machine = record is not None and record["source"] in ("auto", "model", "vlm")
    stale = machine and record["fileSignature"] != signature
    upgrade = record is not None and (
        (record["source"] == "auto" and session is not None)
        or (record["source"] in ("auto", "model") and vision
            and (every or not record["segmented"] or panel_finder.ambiguous_layout(record["panels"])))
    )
    if record is None or stale or upgrade:
        # A reader's own fixes to this comic go with the question, when a
        # model is asked: the way this artist cuts a page, shown not told.
        examples = vision_examples(file_id, store) if vision else []
        with Image.open(io.BytesIO(render_file_page(file_id, index))) as image:
            spread = panel_finder.is_spread(*image.size)
            if spread:
                found = None
            elif session is None and not vision:
                found = panel_finder.detect_panels(image)
            else:
                # The detector reads the 1200px render: it was exported at
                # 1024px so thin panels survive, and the cut's 600px would
                # throw that away. Rendered once and cached like any page.
                detail_bytes = render_file_page(file_id, index, "backdrop")
                with Image.open(io.BytesIO(detail_bytes)) as detail:
                    found = _read_page_panels(image, session, vision, every, direction, detail, detail_bytes, examples)
        if spread:
            # A spread is two pages, and is read as two: each half through
            # the same tiers, from the renders a size up so that a half is
            # a page's size, then joined across the fold and ordered a
            # page at a time.
            with Image.open(io.BytesIO(render_file_page(file_id, index, "backdrop"))) as cut_image:
                halves = panel_finder.spread_halves(cut_image)
            details: list[Any] = [None, None]
            if session is not None or vision:
                with Image.open(io.BytesIO(render_file_page(file_id, index, "read"))) as read_image:
                    details = panel_finder.spread_halves(read_image)
            readings = [
                _read_page_panels(half, session, vision, every, direction, detail, None, examples)
                for half, detail in zip(halves, details)
            ]
            found = panel_finder.combine_halves(readings, direction)
        store.set_page_panels(file_id, member, signature, found["source"], found["panels"], found["segmented"])
        record = {"source": found["source"], "segmented": found["segmented"], "panels": found["panels"]}
    ordered = panel_finder.order_panels(record["panels"], direction) if record["segmented"] else []
    return {
        "fileId": str(file_id), "page": index, "source": record["source"],
        "segmented": bool(record["segmented"]), "readingDirection": direction,
        "panels": [{"id": f"{index}-{position}", **panel} for position, panel in enumerate(ordered)],
    }


def _read_page_panels(image: Any, session: Any, vision: bool, every: bool, direction: str,
                      detail: Any = None, detail_bytes: bytes | None = None,
                      examples: list[tuple[bytes, str]] | None = None) -> dict[str, Any]:
    """One page (or one half of a spread) through every tier there is.

    `image` is what the cut reads; `detail` the same page a size up, for the
    detector and the vision model, with its bytes when already encoded. The
    vision model is asked last and least: about a page nothing could read,
    or every page when so set, or the order of a layout row-major cannot
    settle. Its "vlm" source is stamped whenever it answered, usable or not,
    so a page is sent once, not every time it is opened. A page it reads as
    one image is kept as one panel, and shown whole. A connector that did
    not answer -- down, refused, out of credit -- stamps nothing: the lower
    tiers' reading is kept under their own name, which is what gets a page
    asked again once the connector is back.
    """
    found = panel_finder.detect_panels(image) if detail is None else panel_finder.detect_panels(image, session, detail)
    if found["segmented"]:
        # Whatever the local tiers left out, read from the ink they left.
        found = {**found, "panels": panel_finder.add_leftover_panels(panel_finder.page_mask(image), found["panels"])}
    if detail is None or not vision:
        return found
    if detail_bytes is None:
        buffer = io.BytesIO()
        detail.convert("RGB").save(buffer, format="JPEG", quality=88)
        detail_bytes = buffer.getvalue()
    # The mask a vision model's boxes are corrected against: the larger
    # render, where a two-pixel gutter is still bare rather than the grey
    # the smaller one blends it to.
    mask = panel_finder.page_mask(detail)
    try:
        if every or not found["segmented"]:
            # An answer the page could correct replaces the local reading;
            # a refused one leaves it standing, asked.
            boxes = vision_panels(detail_bytes, detail.size[0], detail.size[1], direction, mask, examples)
            if boxes:
                found = {"segmented": True, "source": "vlm", "panels": boxes}
            else:
                found = {**found, "source": "vlm"}
        if found["segmented"] and panel_finder.ambiguous_layout(found["panels"]):
            ordered = vision_order(detail_bytes, found["panels"], direction)
            panels = found["panels"]
            if ordered:
                panels = [{**panel, "order": ordered.index(position + 1)} for position, panel in enumerate(panels)]
            found = {**found, "source": "vlm", "panels": panels}
    except VisionUnavailable:
        pass
    return found


PANEL_MIN_SIDE = 0.02
PANELS_MAX = 64


def save_page_panels(file_id: int, index: int, payload: Any) -> dict[str, Any]:
    """Keep a person's panels for one page, in the order given, and answer as the reader asks.

    Rectangles are normalised (0-1) and are checked to be on the page and no
    thinner than a sliver; their order is the order sent, kept as `order` so
    the row rule never rearranges what a person arranged. No rectangles is
    the whole page as one panel -- a splash, said outright. A person's row
    stands until they forget it (`forget_page_panels`), whatever the
    automatic tiers would say.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("panels"), list):
        raise ValueError("Send the page's panels as a list")
    raw = payload["panels"]
    if len(raw) > PANELS_MAX:
        raise ValueError(f"A page has at most {PANELS_MAX} panels")
    panels: list[dict[str, Any]] = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("Each panel is a rectangle")
        try:
            x, y, w, h = (float(item[key]) for key in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each panel needs x, y, w and h between 0 and 1") from exc
        if not all(0.0 <= value <= 1.0 for value in (x, y, w, h)) or x + w > 1.0001 or y + h > 1.0001:
            raise ValueError("A panel has to sit on the page")
        if w < PANEL_MIN_SIDE or h < PANEL_MIN_SIDE:
            raise ValueError("A panel that thin cannot be read")
        panels.append({"x": round(x, 4), "y": round(y, 4), "w": round(min(w, 1.0 - x), 4), "h": round(min(h, 1.0 - y), 4), "order": position})
    if not panels:
        panels = [{**panel_finder.WHOLE_PAGE, "order": 0}]
    store = catalog_store()
    path = store.library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Pages can only be read from comic archives")
    pages = cached_page_members(path)
    if not 0 <= index < len(pages):
        raise LookupError("That page is not in this comic")
    store.set_page_panels(file_id, pages[index], _file_signature(path), "manual", panels, True)
    return file_page_panels(file_id, index)


def forget_file_panels(file_id: int) -> dict[str, Any]:
    """Drop every automatic reading of a comic's pages, so each is read again as it is reached.

    For a comic read before a tier, a model or a way of reading was there --
    spreads read whole, a page read without the connector. What a person
    fixed by hand is kept; a page of theirs is let go one at a time.
    """
    store = catalog_store()
    path = store.library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Pages can only be read from comic archives")
    return {"fileId": str(file_id), **store.forget_automatic_page_panels(file_id)}


def forget_page_panels(file_id: int, index: int) -> dict[str, Any]:
    """Drop whatever is kept for one page, a person's reading included, and read it afresh."""
    store = catalog_store()
    path = store.library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Pages can only be read from comic archives")
    pages = cached_page_members(path)
    if not 0 <= index < len(pages):
        raise LookupError("That page is not in this comic")
    store.delete_page_panels(file_id, pages[index])
    return file_page_panels(file_id, index)


def file_reading_progress(file_id: int, *, user_id: int) -> dict[str, Any]:
    """Where a comic was left, and whether that place still exists.

    The page is kept with the file's signature, so a file replaced since it
    was read starts again rather than opening at a page it may not have.
    """
    path = catalog_store().library_file_path(file_id)
    record = catalog_store().reading_progress(file_id, user_id=user_id)
    try:
        signature = _file_signature(path)
    except OSError:
        signature = None
    if record and signature and record["fileSignature"] == signature:
        return record
    return {
        "fileId": str(file_id), "page": 0, "pageCount": 0, "fileSignature": signature,
        "startedAt": None, "finishedAt": None, "updatedAt": None,
        "stale": bool(record),
    }


def set_file_reading_progress(file_id: int, page: int, panel: int = 0, *, user_id: int) -> dict[str, Any]:
    """Remember the page, and the panel on it. The count and the signature are the server's to know."""
    path = catalog_store().library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Pages can only be read from comic archives")
    pages = cached_page_members(path)
    if not pages:
        raise ValueError("This comic has no pages")
    if not 0 <= page < len(pages):
        raise ValueError("That page is not in this comic")
    if not 0 <= panel <= PANELS_MAX:
        raise ValueError("That panel is not on the page")
    return catalog_store().set_reading_progress(
        file_id, page, len(pages), _file_signature(path), finished=page >= len(pages) - 1, panel=panel,
        user_id=user_id,
    )


def mark_file_read(file_id: int, read: bool, *, user_id: int) -> dict[str, Any]:
    """Mark one comic read -- its place on the last page, finished -- or
    unread, which forgets its place altogether."""
    if not read:
        catalog_store().clear_reading_progress(file_id, user_id=user_id)
        return file_reading_progress(file_id, user_id=user_id)
    path = catalog_store().library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Only a comic archive can be marked read")
    pages = cached_page_members(path)
    if not pages:
        raise ValueError("This comic has no pages")
    return set_file_reading_progress(file_id, len(pages) - 1, 0, user_id=user_id)


def mark_run_reading(series_run_id: int, read: bool, *, user_id: int) -> dict[str, Any]:
    """Mark every comic of a run read or unread for one profile. A file that
    cannot be paged (not an archive, or empty) is passed over rather than
    stopping the rest; the answer is the run's reading as it now stands."""
    files = catalog_store().run_reading_files(series_run_id)
    for item in [*files.get("issues", []), *files.get("volumes", [])]:
        try:
            mark_file_read(int(item["id"]), read, user_id=user_id)
        except (ValueError, LookupError, OSError):
            continue
    return series_reading(series_run_id, user_id=user_id)


def render_file_page(file_id: int, index: int, size: str = "") -> bytes:
    path = catalog_store().library_file_path(file_id)
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("Pages can only be read from comic archives")
    if size not in PAGE_SIZES:
        raise ValueError("That is not a page size this library renders")
    pages = cached_page_members(path)
    if not 0 <= index < len(pages):
        raise LookupError("That page is not in this comic")
    # Reading pages are kept beside the catalog under their own budget, so a
    # long sitting cannot evict the covers of a whole library.
    reading = size == "read"
    return render_file_cover_thumbnail(
        path, pages[index], PAGE_SIZES[size],
        cache_dir=reading_cache_dir() if reading else None,
        budget=READING_CACHE_MAX_BYTES if reading else None,
    )


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
    known = _library_run_for(relevance, title, year, provider, run.get("providerSeriesId"))
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


# ---------------------------------------------------------------------------
# Story arcs
# ---------------------------------------------------------------------------
#
# Metron keeps story arcs as their own records -- "Hush" is Batman #608-619,
# twelve issues -- with the issues in them across every series they run
# through. Searching Discover finds them; an arc's drawer lists its issues in
# reading order with what the library holds; Pull arc asks for the missing
# ones, one pull per series, through the same path as pulling any issues.
# Arcs are volunteer-maintained: an arc still being published is often not
# one yet (Hush 2's chapters carry no arc in September 2026), and the run's
# own issue list is the way to pull it until it is.

ARC_MAX_PAGES = 10


# ---- Story arc names, kept locally (2026-10-03) ------------------------------
# Metron's arc names and the community's reading lists (arc_catalog.py), kept
# in one file beside the catalog and refreshed at most once a day, when a
# search asks -- so suggesting arcs as someone types asks no remote catalog
# anything, and "War of Realms" finds "War of the Realms". Metron's list comes
# whole the first time (2,261 arcs, 23 pages at its 3.2s pace) and after that
# only what changed since (`modified_gt`, one page a day); the community's is
# one GitHub tree request. A failed refresh keeps the last list and waits an
# hour before trying again.

ARC_INDEX_MAX_AGE_SECONDS = 24 * 3600
ARC_INDEX_RETRY_SECONDS = 3600
ARC_INDEX_MAX_PAGES = 60
_ARC_INDEX_LOCK = threading.Lock()
_ARC_INDEX_REFRESHING = threading.Event()


def arc_index_path() -> Path:
    return _configured_path("ARC_INDEX", catalog_database_path().parent / "arc-index.json")


def read_arc_index() -> dict[str, Any]:
    try:
        index = json.loads(arc_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return index if isinstance(index, dict) else {}


def _write_arc_index(index: dict[str, Any]) -> None:
    path = arc_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(index), encoding="utf-8")
    os.replace(temporary, path)


def _arc_part_stale(part: dict[str, Any] | None, now: float) -> bool:
    part = part or {}
    fetched = float(part.get("fetchedAtEpoch") or 0)
    tried = float(part.get("triedAtEpoch") or 0)
    return now - fetched > ARC_INDEX_MAX_AGE_SECONDS and now - tried > ARC_INDEX_RETRY_SECONDS


def _fetch_metron_arc_names(previous: dict[str, Any]) -> dict[str, Any]:
    """Every Metron arc's id and name: all of them the first time, then only
    those changed since the last fetch, merged in."""
    credential = _provider_credential("metron")
    since = previous.get("fetchedAt") if previous.get("arcs") else None
    names = {str(item["id"]): item["name"] for item in previous.get("arcs") or []} if since else {}
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    url = f"{METRON_API_BASE}/arc/" + (("?" + urllib.parse.urlencode({"modified_gt": since})) if since else "")
    for _ in range(ARC_INDEX_MAX_PAGES):
        page = fetch_provider_json("metron", url, credential) or {}
        for row in page.get("results") or []:
            if isinstance(row, dict) and row.get("id") and str(row.get("name") or "").strip():
                names[str(row["id"])] = str(row["name"]).strip()
        url = page.get("next")
        if not url:
            break
    return {"fetchedAt": started, "arcs": [{"id": key, "name": value} for key, value in names.items()]}


def _fetch_community_lists() -> dict[str, Any]:
    """The community repository's reading lists, from one request for its tree."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{arc_catalog.COMMUNITY_REPO}/git/trees/{arc_catalog.COMMUNITY_BRANCH}?recursive=1",
        headers={"User-Agent": f"Flipparr/{APP_VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        tree = json.loads(response.read(16 * 1024 * 1024))
    paths = [item["path"] for item in tree.get("tree") or [] if isinstance(item, dict) and item.get("type") == "blob"]
    return {"fetchedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "lists": arc_catalog.community_lists(paths)}


def refresh_arc_index() -> None:
    """Bring whichever of the two lists is stale up to date. Each is kept or
    replaced on its own; one failing never empties the other."""
    now = time.time()
    with _ARC_INDEX_LOCK:
        index = read_arc_index()
    for part, fetch, wanted in (
        ("metron", lambda previous: _fetch_metron_arc_names(previous), metron_configured()),
        ("community", lambda _previous: _fetch_community_lists(), bool(cached_setting("communityListsEnabled"))),
    ):
        previous = index.get(part) or {}
        if not wanted or not _arc_part_stale(previous, now):
            continue
        try:
            fresh = fetch(previous)
        except Exception as exc:  # a slow or down source is not an error for whoever searched
            log_exception("arc_index_refresh_failed", exc, level="warning", part=part)
            fresh = {**previous, "triedAtEpoch": now}
        else:
            fresh = {**fresh, "fetchedAtEpoch": now, "triedAtEpoch": now}
            log_event("arc_index_refreshed", part=part,
                      count=len(fresh.get("arcs") or fresh.get("lists") or []))
        with _ARC_INDEX_LOCK:
            index = {**read_arc_index(), part: fresh}
            _write_arc_index(index)


def ensure_arc_index() -> dict[str, Any]:
    """The kept lists as they are, starting a refresh behind them when one is
    stale -- once at a time, so a run of searches does not start several."""
    index = read_arc_index()
    now = time.time()
    stale = (cached_setting("communityListsEnabled") and _arc_part_stale(index.get("community"), now)) \
        or (metron_configured() and _arc_part_stale(index.get("metron"), now))
    if stale and not _ARC_INDEX_REFRESHING.is_set():
        _ARC_INDEX_REFRESHING.set()

        def run() -> None:
            try:
                refresh_arc_index()
            finally:
                _ARC_INDEX_REFRESHING.clear()

        threading.Thread(target=run, name="arc-index-refresh", daemon=True).start()
    return index


def _metron_arc(entry: dict[str, Any]) -> dict[str, Any]:
    return {"provider": "metron", "providerArcId": str(entry["id"]), "name": str(entry["name"])}


def arc_suggestions(query: str, *, include_lists: bool) -> dict[str, Any]:
    """Arcs to offer while someone types, from the kept lists only: Metron's
    arcs, and -- for the admin, who imports them -- the community's lists."""
    index = ensure_arc_index()
    arcs = arc_catalog.search((index.get("metron") or {}).get("arcs") or [], query, limit=6) if metron_configured() else []
    lists = arc_catalog.search((index.get("community") or {}).get("lists") or [], query, limit=8) \
        if include_lists and cached_setting("communityListsEnabled") else []
    return {"arcs": [_metron_arc(entry) for entry in arcs], "lists": lists,
            "ready": bool((index.get("metron") or {}).get("arcs") or (index.get("community") or {}).get("lists"))}


def discover_story_arcs(query: str) -> dict[str, Any]:
    """Metron's story arcs whose names match, for Discover's search: Metron's
    own name search, joined by the kept list's forgiving one, and -- when
    neither finds anything -- Metron asked again for the search's most telling
    word, keeping the arcs whose names hold all of its words."""
    cleaned = " ".join(str(query or "").split())
    if len(cleaned) < 2 or not metron_configured():
        return {"arcs": [], "available": metron_configured()}
    credential = _provider_credential("metron")

    def ask(name: str) -> list[dict[str, Any]]:
        listing = fetch_provider_json("metron", f"{METRON_API_BASE}/arc/?" + urllib.parse.urlencode({"name": name}), credential)
        return [{"id": str(row["id"]), "name": str(row.get("name") or "").strip()}
                for row in (listing or {}).get("results") or [] if isinstance(row, dict) and row.get("id") and row.get("name")]

    found = {entry["id"]: entry for entry in ask(cleaned)}
    # The kept list as it is; refreshing it is the suggestions' to start.
    for entry in arc_catalog.search((read_arc_index().get("metron") or {}).get("arcs") or [], cleaned, limit=8):
        found.setdefault(str(entry["id"]), {"id": str(entry["id"]), "name": entry["name"]})
    words = arc_catalog.query_words(cleaned)
    if not found and words:
        for entry in ask(max(words, key=len)):
            if arc_catalog.name_matches(words, entry["name"]):
                found[entry["id"]] = entry
    ranked = arc_catalog.search(found.values(), cleaned, limit=8) if words else list(found.values())[:8]
    # Metron's own match for a phrase of small words ("The End") still counts.
    ranked += [entry for entry in found.values() if entry not in ranked][: max(0, 8 - len(ranked))]
    return {"arcs": [_metron_arc(entry) for entry in ranked], "available": True}


def _arc_issue_rows(arc_id: str, credential: str, *, force: bool = False) -> list[dict[str, Any]]:
    """An arc's issues from Metron, every page, in reading order (by cover date)."""
    url = f"{METRON_API_BASE}/arc/{arc_id}/issue_list/"
    rows: list[dict[str, Any]] = []
    for _ in range(ARC_MAX_PAGES):
        page = fetch_provider_json("metron", url, credential, force=force) or {}
        rows.extend(row for row in page.get("results") or [] if isinstance(row, dict) and row.get("id"))
        url = page.get("next")
        if not url:
            break
    return sorted(rows, key=lambda row: (str(row.get("cover_date") or "9999"), _issue_sort_key(row.get("number"))))


def _issue_sort_key(number: Any) -> tuple[float, str]:
    match = re.match(r"-?\d+(?:\.\d+)?", str(number or ""))
    return (float(match.group()) if match else float("inf"), str(number or ""))


def story_arc_detail(
    arc_id: str, may_see: "Callable[[int], bool] | None" = None, *, force: bool = False,
) -> dict[str, Any]:
    """An arc and its issues, each with what the library holds of it. With
    `may_see`, a run the profile may not see counts as not held at all."""
    arc_id = str(arc_id or "").strip()
    if not re.fullmatch(r"\d+", arc_id):
        raise ValueError("Choose a story arc")
    credential = _provider_credential("metron")
    arc = fetch_provider_json("metron", f"{METRON_API_BASE}/arc/{arc_id}/", credential, force=force) or {}
    rows = _arc_issue_rows(arc_id, credential, force=force)
    # The library's runs: by their confirmed Metron id first, by title and
    # year when a run has none.
    relevance = _library_relevance()
    by_metron = catalog_store().runs_by_provider_series("metron")
    by_run = {entry["runId"]: entry for entry in relevance.values()}
    issues = []
    for row in rows:
        series = row.get("series") or {}
        series_id = str(series.get("id") or "")
        title = str(series.get("name") or "").strip()
        year = series.get("year_began")
        run_id = by_metron.get(series_id)
        entry = by_run.get(str(run_id)) if run_id is not None else None
        entry = entry or _library_run_for(relevance, title, year, "metron", series_id)
        if entry and may_see is not None and str(entry.get("runId") or "").isdigit() and not may_see(int(entry["runId"])):
            entry = None
        key = _issue_key(row.get("number"))
        issues.append({
            "providerIssueId": str(row["id"]), "providerSeriesId": series_id,
            "seriesTitle": title, "seriesYear": year, "number": str(row.get("number") or ""),
            "coverDate": row.get("cover_date"), "cover": row.get("image"),
            "owned": bool(entry and key in entry["owned"]),
            "queued": bool(entry and key in entry["queued"]),
            "runId": entry["runId"] if entry else None,
        })
    series_list: dict[str, dict[str, Any]] = {}
    for issue in issues:
        group = series_list.setdefault(issue["providerSeriesId"], {
            "providerSeriesId": issue["providerSeriesId"], "title": issue["seriesTitle"], "year": issue["seriesYear"],
            "issueCount": 0, "missing": 0,
        })
        group["issueCount"] += 1
        group["missing"] += 0 if issue["owned"] or issue["queued"] else 1
    return {
        "provider": "metron", "providerArcId": arc_id, "name": str(arc.get("name") or ""),
        "description": _synopsis_text(arc.get("desc")),
        "cover": arc.get("image") or (issues[0]["cover"] if issues else None),
        "issues": issues, "series": list(series_list.values()),
        "missing": sum(1 for issue in issues if not (issue["owned"] or issue["queued"])),
        "owned": sum(1 for issue in issues if issue["owned"]),
        # Saved to read across runs already? The drawer then offers Read.
        "readingListId": _reading_list_id_for_arc(arc_id),
    }


def _reading_list_id_for_arc(arc_id: str) -> str | None:
    found = catalog_store().reading_list_by_provider("metron", arc_id)
    return str(found) if isinstance(found, int) else None


def _pull_missing_issues(issues: list[dict[str, Any]], titles: dict[str, str]) -> dict[str, Any]:
    """Ask for every issue here that is neither owned nor on the way, one pull
    per series. What could not be pulled is said, and the rest still is; an
    issue with no catalog id to pull by is said too. Nothing missing is an
    empty answer, for the caller to word."""
    groups: dict[tuple[str, str], list[str]] = {}
    failed: list[dict[str, Any]] = []
    for issue in issues:
        if issue["owned"] or issue["queued"]:
            continue
        provider = str(issue.get("provider") or "metron")
        if not issue.get("providerSeriesId") or provider not in {"metron", "comic_vine", "gcd"}:
            failed.append({"providerSeriesId": issue.get("providerSeriesId"), "title": issue.get("seriesTitle"),
                           "error": "No catalog entry to pull it from"})
            continue
        groups.setdefault((provider, str(issue["providerSeriesId"])), []).append(issue["number"])
    pulled = []
    for (provider, series_id), numbers in groups.items():
        try:
            result = pull_discovered_issues(provider, series_id, numbers, query=titles.get(series_id, ""))
            pulled.append({"providerSeriesId": series_id, "title": titles.get(series_id), "numbers": result["numbers"],
                           "request": result.get("request"), "series": result.get("series")})
        except Exception as exc:  # noqa: BLE001 -- the other series are still pulled
            failed.append({"providerSeriesId": series_id, "title": titles.get(series_id), "error": str(exc)})
    if groups and not pulled:
        raise ValueError(f"The arc could not be pulled: {failed[0]['error']}")
    return {"pulled": pulled, "failed": failed, "count": sum(len(item["numbers"]) for item in pulled)}


def pull_story_arc(arc_id: str, *, allow_nothing: bool = False) -> dict[str, Any]:
    """Ask for every issue of an arc the library has not got and is not getting,
    one pull per series. With `allow_nothing`, an arc already here is an empty
    answer rather than a refusal -- approving a reader's request saves the
    arc first, and that is the success."""
    detail = story_arc_detail(arc_id)
    titles = {group["providerSeriesId"]: group["title"] for group in detail["series"]}
    result = _pull_missing_issues(detail["issues"], titles)
    if not result["pulled"] and not result["failed"] and not allow_nothing:
        raise ValueError("Every issue of this arc is in your library or on the way")
    return {"name": detail["name"], **result}


# ---- Story arcs saved as reading lists ---------------------------------------
# An arc kept as the household's own ordered list (schema 57), so it can be
# read across runs, reordered by hand -- Metron has no reading order, only
# cover dates, so tie-ins within a month come out alphabetical -- and pruned.
# `story_arcs` is a collection's run groups and unrelated. Each profile's
# place is derived from reading_progress, as it is for runs.

def _resolve_reading_list_items(
    items: list[dict[str, Any]], relevance: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int | None]:
    """What each item is in the library, by item id: the issue already linked
    to its provider id, else an issue of the run the provider's series is --
    linked at the provider, or by title and year, which is what keeps Green
    Lantern (2011) out of a 2023 arc -- whose number is the same. An item
    with no provider id keeps what it names."""
    store = catalog_store()
    relevance = _library_relevance() if relevance is None else relevance
    by_provider: dict[str, list[str]] = {}
    for item in items:
        if item.get("provider") and item.get("providerIssueId"):
            by_provider.setdefault(item["provider"], []).append(str(item["providerIssueId"]))
    exact = {provider: store.issue_ids_by_provider(provider, ids) for provider, ids in by_provider.items()}
    linked_runs = {provider: store.runs_by_provider_series(provider) for provider in by_provider}
    resolved: dict[str, int | None] = {}
    run_for: dict[str, int] = {}
    for item in items:
        provider = item.get("provider")
        if not provider:
            resolved[item["id"]] = int(item["issueId"]) if item.get("issueId") else None
            continue
        hit = exact.get(provider, {}).get(str(item.get("providerIssueId") or ""))
        if hit is not None:
            resolved[item["id"]] = int(hit)
            continue
        run_id = linked_runs.get(provider, {}).get(str(item.get("providerSeriesId") or ""))
        if run_id is None:
            entry = _library_run_for(relevance, item["seriesTitle"], item.get("seriesYear"),
                                     provider, item.get("providerSeriesId"))
            run_id = int(entry["runId"]) if entry and str(entry.get("runId") or "").isdigit() else None
        if run_id is not None:
            run_for[item["id"]] = int(run_id)
    numbers = store.issues_by_run(list(set(run_for.values())))
    for item in items:
        if item["id"] in resolved:
            continue
        run_id = run_for.get(item["id"])
        key = _issue_key(item["number"])
        resolved[item["id"]] = next(
            (issue_id for issue_id, number in numbers.get(str(run_id), []) if _issue_key(number) == key), None,
        ) if run_id is not None else None
    return resolved


def _light_relevance() -> dict[str, dict[str, Any]]:
    """What `_library_run_for` needs to place a provider's run -- title, year,
    id -- from one cheap query rather than the whole catalog, which
    `_library_relevance` builds and an arc's drawer must not wait for."""
    relevance: dict[str, dict[str, Any]] = {}
    for run in catalog_store().run_title_index():
        entry = {"runId": run["id"], "year": _provider_year(run["year"]), "following": False,
                 "publisher": None, "owned": set(), "queued": set()}
        title = normalized_title(run["title"])
        relevance.setdefault(title, entry)
        relevance[f"{title}|{run['year'] or ''}"] = entry
    return relevance


def _reading_list_resolved(list_id: int, relevance: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The arc, with any item that has no issue yet resolved again and the
    change written back. Items already placed are left alone: opening an
    arc is not the moment to re-derive what a scan already settled."""
    store = catalog_store()
    data = store.reading_list(list_id)
    if all(item["issueId"] for item in data["items"]):
        return data
    relevance = _light_relevance() if relevance is None else relevance
    resolved = _resolve_reading_list_items(data["items"], relevance)
    changed = {
        int(item["id"]): resolved.get(item["id"]) for item in data["items"]
        if (str(resolved.get(item["id"])) if resolved.get(item["id"]) is not None else None) != item["issueId"]
    }
    if changed:
        store.set_reading_list_item_issues(list_id, changed)
        data = store.reading_list(list_id)
    return data


def _arc_item_visible(run_id: Any, may_see: "Callable[[int], bool] | None", may_see_unrated: bool) -> bool:
    if may_see is None:
        return True
    if run_id in (None, ""):
        return may_see_unrated
    return may_see(int(run_id))


def _arc_years(items: list[dict[str, Any]]) -> dict[str, int | None]:
    """When the arc ran, from its issues' cover dates: the first and last
    year. A run's own start year is not the arc's -- Hush is 2002, not
    Batman's 1940 -- so only the items' dates count."""
    years = sorted({int(str(item.get("coverDate"))[:4]) for item in items
                    if re.match(r"^\d{4}", str(item.get("coverDate") or ""))})
    return {"year": years[0] if years else None, "yearEnd": years[-1] if years else None}


def _arc_series(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, Any], dict[str, Any]] = {}
    for item in items:
        group = groups.setdefault((item["seriesTitle"], item.get("seriesYear")), {
            "providerSeriesId": item.get("providerSeriesId"), "title": item["seriesTitle"],
            "year": item.get("seriesYear"), "issueCount": 0, "missing": 0,
        })
        group["issueCount"] += 1
        group["missing"] += 0 if item.get("owned") or item.get("queued") else 1
    return list(groups.values())


# Whose an arc -- or, since schema 63, a collection -- is. The rules below
# read only `ownerId` and `shared`, so both kinds of read group use them.
# Whose an arc is (schema 62). One with no owner is the household's, as
# every arc saved from Metron or a CBL file is, and the admin keeps it. Any
# profile may make its own by hand; it is that profile's until shared with
# the household, and then the others read it but only its maker changes it
# -- the admin may still unshare or delete it, as the household's keeper.

def arc_visible_to(entry: dict[str, Any], viewer: "Viewer | None") -> bool:
    """The household's arcs, shared ones and a profile's own. Without a viewer
    -- the library's own work: healing, pulling -- every arc."""
    owner = entry.get("ownerId")
    return viewer is None or owner is None or bool(entry.get("shared")) or int(owner) == viewer.id


def arc_editable_by(entry: dict[str, Any], viewer: "Viewer") -> bool:
    owner = entry.get("ownerId")
    return viewer.is_admin if owner is None else int(owner) == viewer.id


def arc_manageable_by(entry: dict[str, Any], viewer: "Viewer") -> bool:
    """Who may unshare or delete it: whoever edits it, and the admin a shared one."""
    return arc_editable_by(entry, viewer) or (viewer.is_admin and bool(entry.get("shared")))


def _arc_ownership(entry: dict[str, Any], viewer: "Viewer | None", names: dict[int, str]) -> dict[str, Any]:
    owner = entry.get("ownerId")
    return {
        "editable": bool(viewer and arc_editable_by(entry, viewer)),
        "manageable": bool(viewer and arc_manageable_by(entry, viewer)),
        "mine": bool(viewer and owner is not None and int(owner) == viewer.id),
        "ownerName": names.get(int(owner)) if owner is not None else None,
    }


def _profile_names() -> dict[int, str]:
    return {int(user["id"]): user["name"] for user in _profiles_store().list_users()}


def reading_list_detail(
    list_id: int, *, user_id: int, may_see: "Callable[[int], bool] | None" = None,
    may_see_unrated: bool = True, viewer: "Viewer | None" = None,
) -> dict[str, Any]:
    """An arc as the drawer draws it: every issue in order with what the
    library holds and where this profile is in it, and where its Read button
    goes. Items of runs the profile may not see are left out, and an arc with
    nothing left to show is not there for it."""
    store = catalog_store()
    if not arc_visible_to(store.reading_list_meta(list_id), viewer):
        raise LookupError("That story arc is not in the library")
    data = _reading_list_resolved(list_id)
    # "On the way" for the issues here that are not: one scoped query, not
    # the catalog's every request.
    queued = store.queued_issue_ids([int(item["issueId"]) for item in data["items"] if item["issueId"] and not item["fileId"]])
    progress = store.reading_progress_by_list(user_id=user_id).get(str(list_id), {})
    items = []
    for item in data["items"]:
        if not _arc_item_visible(item["runId"], may_see, may_see_unrated):
            continue
        path = Path(item["path"]) if item.get("path") else None
        record = progress.get(item["fileId"] or "") or {}
        items.append({
            **{key: value for key, value in item.items() if key != "path"},
            "owned": bool(item["fileId"]),
            "queued": bool(item["issueId"] and not item["fileId"] and int(item["issueId"]) in queued),
            "readable": bool(path and path.is_file() and archive_kind(path) is not None),
            "fileCover": f"/api/v1/files/{item['fileId']}/pages/0" if item["fileId"] else None,
            "page": record.get("page", 0), "pageCount": record.get("pageCount", 0),
            "finishedAt": record.get("finishedAt"), "stale": record.get("stale", False),
        })
    # An arc made by hand starts empty; one emptied by the profile's rating
    # is not there for it.
    if not items and data["items"]:
        raise LookupError("That story arc is not in the library")
    comics = [{"id": item["fileId"], "issueNumber": item["number"], "filename": item["filename"],
               "readable": item["readable"]} for item in items if item["fileId"]]
    meta = {key: value for key, value in data.items() if key != "items"}
    return {
        **meta, "issueCount": len(items),
        "owned": sum(1 for item in items if item["owned"]),
        "queued": sum(1 for item in items if item["queued"]),
        "missing": sum(1 for item in items if not (item["owned"] or item["queued"])),
        "series": _arc_series(items), "resume": reading_target(comics, [], progress), "items": items,
        **_arc_years(items), **_arc_ownership(meta, viewer, _profile_names()),
        "uploadedCover": uploaded_cover_url("arcs", list_id),
    }


def reading_lists(
    *, may_see: "Callable[[int], bool] | None" = None, may_see_unrated: bool = True,
    viewer: "Viewer | None" = None,
) -> dict[str, Any]:
    """Every arc for the Comics grid, without progress (that is `reading_by_list`,
    as `reading_by_run` is for runs) and without resolving anything: the grid
    asks on every visit."""
    lists = []
    names = _profile_names()
    for entry in catalog_store().reading_lists_overview():
        if not arc_visible_to(entry, viewer):
            continue
        items = [item for item in entry["items"] if _arc_item_visible(item["runId"], may_see, may_see_unrated)]
        if not items and entry["items"]:
            continue
        titles = list(dict.fromkeys(item["seriesTitle"] for item in items))
        lists.append({
            **{key: value for key, value in entry.items() if key != "items"},
            "issueCount": len(items), "owned": sum(1 for item in items if item["fileId"]),
            "missing": sum(1 for item in items if not item["fileId"]),
            "seriesTitles": titles, "seriesCount": len(titles), **_arc_years(items),
            # The library's own issues and runs in it, so the Comics grid can
            # fold a run kept only for this arc into the arc's card.
            "issueIds": [item["issueId"] for item in items if item["issueId"]],
            "runIds": list(dict.fromkeys(item["runId"] for item in items if item["runId"])),
            **_arc_ownership(entry, viewer, names),
        })
    return {"lists": lists}


def _reading_place(progress: dict[str, dict[str, Any]], owned_total: int) -> dict[str, Any] | None:
    """Where a run or an arc was left, from where its comics were: the comic
    mid-page, else the last one read; and one of three states for the card --
    "Between issues" used to read as finished, and the cover of a run with
    one issue read out of twenty-five offered Restart."""
    live = {file_id: record for file_id, record in progress.items() if not record["stale"]}
    if not live:
        return None
    started = [record for record in live.values() if not record["finishedAt"]]
    latest = max(started or live.values(), key=lambda record: str(record["updatedAt"] or ""))
    file_id = next(key for key, record in live.items() if record is latest)
    finished_count = len(live) - len(started)
    remaining = max(0, owned_total - finished_count)
    state = "continue" if started else "next" if remaining else "finished"
    return {
        "state": state,
        "fileId": file_id, "issueNumber": latest["issueNumber"],
        "page": latest["page"], "pageCount": latest["pageCount"],
        "lastReadAt": max(str(record["updatedAt"] or "") for record in live.values()) or None,
        # For the card's marker: a check once every issue is read, else
        # that the thing has been started.
        "read": finished_count, "total": int(owned_total),
    }


def reading_by_list(
    *, user_id: int, may_see: "Callable[[int], bool] | None" = None, may_see_unrated: bool = True,
    viewer: "Viewer | None" = None,
) -> dict[str, Any]:
    """Where each arc was left, for the Comics grid: one query, only arcs
    with a reading history, the same record a run's card gets."""
    store = catalog_store()
    overview = {entry["id"]: entry for entry in store.reading_lists_overview()}
    lists = {}
    for list_id, progress in store.reading_progress_by_list(user_id=user_id).items():
        entry = overview.get(list_id)
        if entry is None or not arc_visible_to(entry, viewer):
            continue
        files = {item["fileId"] for item in entry["items"]
                 if item["fileId"] and _arc_item_visible(item["runId"], may_see, may_see_unrated)}
        place = _reading_place({file_id: record for file_id, record in progress.items() if file_id in files}, len(files))
        if place:
            lists[list_id] = place
    return {"lists": lists}


def mark_reading_list_reading(
    list_id: int, read: bool, *, user_id: int, may_see: "Callable[[int], bool] | None" = None,
    may_see_unrated: bool = True, viewer: "Viewer | None" = None,
) -> dict[str, Any]:
    """Mark every comic of an arc this profile may see read or unread, as
    `mark_run_reading` does for a run."""
    if not arc_visible_to(catalog_store().reading_list_meta(list_id), viewer):
        raise LookupError("That story arc is not in the library")
    for item in catalog_store().reading_list_files(list_id):
        if not _arc_item_visible(item["runId"], may_see, may_see_unrated):
            continue
        try:
            mark_file_read(int(item["id"]), read, user_id=user_id)
        except (ValueError, LookupError, OSError):
            continue
    return reading_list_detail(list_id, user_id=user_id, may_see=may_see, may_see_unrated=may_see_unrated, viewer=viewer)


def _arc_items_from_metron(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "provider": "metron", "providerIssueId": issue["providerIssueId"], "providerSeriesId": issue["providerSeriesId"],
        "seriesTitle": issue["seriesTitle"], "seriesYear": issue["seriesYear"], "number": issue["number"],
        "coverDate": issue["coverDate"], "cover": issue["cover"],
    } for issue in detail["issues"]]


def _reading_list_cover_choices(list_id: int) -> set[str]:
    uploaded = uploaded_cover_url("arcs", list_id)
    return _reading_list_cover_choices_found(list_id) | ({uploaded} if uploaded else set())


def _reading_list_cover_choices_found(list_id: int) -> set[str]:
    """The covers an arc may wear: the first page of any of its comics that
    is here, or a picture the provider or the list gave it. Never a link
    someone typed."""
    data = catalog_store().reading_list(list_id)
    choices = {f"/api/v1/files/{item['fileId']}/pages/0" for item in data["items"] if item.get("fileId")}
    choices |= {str(item["cover"]) for item in data["items"] if str(item.get("cover") or "").startswith("https://")}
    if str(data.get("cover") or "").startswith("https://"):
        choices.add(str(data["cover"]))
    return choices


def reading_list_backdrop(list_id: int) -> dict[str, Any]:
    """The page behind an arc's drawer header: the one chosen, or one found
    in the first of its comics that are here -- as a run's is."""
    store = catalog_store()
    return _group_backdrop(store.reading_list_files(list_id), store.reading_list_backdrop_preference(list_id),
                           lambda *found: store.set_reading_list_backdrop(list_id, *found))


def run_collection_backdrop(collection_id: int) -> dict[str, Any]:
    """The page behind a collection's drawer header, as an arc's: chosen, or
    found in the first of its runs' comics."""
    store = catalog_store()
    return _group_backdrop(store.run_collection_files(collection_id),
                           store.run_collection_backdrop_preference(collection_id),
                           lambda *found: store.set_run_collection_backdrop(collection_id, *found))


def choose_run_collection_backdrop(collection_id: int, file_id: str, page: int) -> dict[str, Any]:
    """A page picked from one of the collection's own comics."""
    files = {item["id"]: item for item in catalog_store().run_collection_files(collection_id)}
    if str(file_id) not in files:
        raise ValueError("That comic is not part of this collection")
    path = Path(files[str(file_id)]["path"])
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("That comic cannot be opened")
    pages = cached_page_members(path)
    if not 0 <= int(page) < len(pages):
        raise ValueError("That page is not in this comic")
    catalog_store().set_run_collection_backdrop(collection_id, int(file_id), pages[int(page)], "chosen",
                                                _file_signature(path))
    return run_collection_backdrop(collection_id)


def _group_backdrop(
    files: list[dict[str, Any]], preference: dict[str, Any] | None, remember: "Callable[..., None]",
) -> dict[str, Any]:
    """A read group's header page: the preference while its comic is still
    the same file (a chosen page holds through a rescan), else the automatic
    page of the first of its first three comics that has one, remembered."""
    by_id = {item["id"]: item for item in files}
    if preference and preference["fileId"] in by_id:
        path = Path(by_id[preference["fileId"]]["path"])
        try:
            signature = _file_signature(path)
        except OSError:
            signature = None
        if signature and (preference["source"] == "chosen" or preference["fileSignature"] == signature):
            pages = cached_page_members(path)
            if preference["member"] in pages:
                index = pages.index(preference["member"])
                return {"url": _page_url(preference["fileId"], index, path, "backdrop"),
                        "source": preference["source"], "fileId": preference["fileId"], "page": index}
    for item in files[:3]:
        path = Path(item["path"])
        try:
            if not path.is_file() or archive_kind(path) is None:
                continue
            member = automatic_backdrop_page(path)
            signature = _file_signature(path)
        except OSError:
            continue
        if not member:
            continue
        remember(int(item["id"]), member, "auto", signature)
        index = cached_page_members(path).index(member)
        return {"url": _page_url(item["id"], index, path, "backdrop"), "source": "auto", "fileId": item["id"], "page": index}
    return {"url": None, "source": "none", "fileId": None, "page": None}


def choose_reading_list_backdrop(list_id: int, file_id: str, page: int) -> dict[str, Any]:
    """A page the admin picked from one of the arc's own comics."""
    files = {item["id"]: item for item in catalog_store().reading_list_files(list_id)}
    if str(file_id) not in files:
        raise ValueError("That comic is not part of this story arc")
    path = Path(files[str(file_id)]["path"])
    if not path.is_file() or archive_kind(path) is None:
        raise ValueError("That comic cannot be opened")
    pages = cached_page_members(path)
    if not 0 <= int(page) < len(pages):
        raise ValueError("That page is not in this comic")
    catalog_store().set_reading_list_backdrop(list_id, int(file_id), pages[int(page)], "chosen", _file_signature(path))
    return reading_list_backdrop(list_id)


def heal_reading_lists() -> int:
    """Every saved arc with an issue not yet found in the library, resolved
    again. Called when a run can have appeared -- a scan, an import -- so
    the grid does not say "0 of 5" about comics that are here until someone
    opens the arc. Returns how many arcs gained an issue."""
    store = catalog_store()
    waiting = [entry for entry in store.reading_lists_overview() if any(item["issueId"] is None for item in entry["items"])]
    if not waiting:
        return 0
    relevance = _light_relevance()
    healed = 0
    for entry in waiting:
        try:
            before = sum(1 for item in entry["items"] if item["issueId"])
            after = sum(1 for item in _reading_list_resolved(int(entry["id"]), relevance)["items"] if item["issueId"])
            healed += after > before
        except Exception as exc:  # noqa: BLE001 -- one arc failing leaves the rest healed
            log_event("reading_list_heal_failed", level="warning", listId=entry["id"], error=str(exc)[:200])
    return healed


def save_story_arc(arc_id: str, *, user_id: int, created_by: int | None = None) -> dict[str, Any]:
    """Keep a Metron arc as a story arc of the library's own. Saving one
    already kept is that one, said so."""
    arc_id = str(arc_id or "").strip()
    if not re.fullmatch(r"\d+", arc_id):
        raise ValueError("Choose a story arc")
    store = catalog_store()
    existing = store.reading_list_by_provider("metron", arc_id)
    if existing is not None:
        return {**reading_list_detail(existing, user_id=user_id), "existed": True}
    detail = story_arc_detail(arc_id)
    made = store.create_reading_list(
        detail["name"] or f"Story arc {arc_id}", description=detail["description"], cover=detail["cover"],
        source="metron", provider="metron", provider_arc_id=arc_id,
        created_by=created_by if created_by is not None else user_id, items=_arc_items_from_metron(detail),
    )
    return {**reading_list_detail(int(made["id"]), user_id=user_id), "existed": False}


def refresh_reading_list(list_id: int, *, user_id: int) -> dict[str, Any]:
    """Metron's list again: details taken, new issues slotted in after their
    neighbours, our order and our removals kept."""
    store = catalog_store()
    data = store.reading_list(list_id)
    if data["provider"] == "metron" and data["providerArcId"]:
        detail = story_arc_detail(data["providerArcId"], force=True)
        store.update_reading_list(list_id, name=detail["name"] or None, description=detail["description"], cover=detail["cover"])
        merged = store.merge_reading_list_items(list_id, _arc_items_from_metron(detail))
    elif data["source"] == "cbl" and data["sourceUrl"]:
        raw, _ = _fetch_reading_list(data["sourceUrl"])
        parsed = parse_reading_list(raw)
        store.update_reading_list(list_id, description=parsed["description"], cover=(parsed["coverUrls"] or [None])[0])
        merged = store.merge_reading_list_items(list_id, parsed["items"])
    else:
        raise ValueError("This story arc came from a file, so there is nothing to refresh it from")
    return {**reading_list_detail(list_id, user_id=user_id), **merged}


# ---- Reading lists from files and links (CBL) --------------------------------
# The community keeps reading orders as CBL files -- DieselTech's
# CBL-ReadingLists on GitHub, made from CBRO and CMRO, is the one Kavita and
# Komga import -- and those carry the story order Metron does not. They are
# other people's work under no stated licence, so nothing is bundled: the
# admin imports a file they have, or pastes a link, and the arc says where
# it came from.

READING_LIST_IMPORT_MAX_BYTES = 2 * 1024 * 1024


class ReadingListDuplicate(ValueError):
    def __init__(self, name: str, list_id: str):
        super().__init__(f"You already have a story arc called {name}")
        self.name, self.list_id = name, list_id


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 -- urllib's signature
        raise ValueError("That link redirects somewhere else; paste the link it ends up at")


def _reading_list_link(url: str) -> str:
    """The link as it will be fetched and remembered: http(s) only, a GitHub
    page link turned into the raw file it shows, and the path encoded --
    the community's file names have spaces and brackets, and a link copied
    from a browser bar carries them as typed."""
    url = str(url or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Paste a link that starts with https://")
    if parsed.hostname == "github.com":
        blob = re.fullmatch(r"/([^/]+)/([^/]+)/blob/(.+)", parsed.path)
        if blob:
            parsed = urllib.parse.urlsplit(f"https://raw.githubusercontent.com/{blob.group(1)}/{blob.group(2)}/{blob.group(3)}")
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/:@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _fetch_reading_list(url: str) -> tuple[bytes, str]:
    """A reading list from a link the admin pasted: never a host inside the
    household's own network, no redirects, five seconds, two megabytes."""
    url = _reading_list_link(url)
    parsed = urllib.parse.urlsplit(url)
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except socket.gaierror as exc:
        raise ValueError(f"{parsed.hostname} could not be found") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("That link points inside your own network")
    request = urllib.request.Request(url, headers={
        "User-Agent": f"Flipparr/{APP_VERSION}",
        "Accept": "application/xml, application/json, text/xml, text/plain;q=0.9, */*;q=0.5",
    })
    opener = urllib.request.build_opener(_NoRedirects)
    try:
        with opener.open(request, timeout=5) as response:
            data = response.read(READING_LIST_IMPORT_MAX_BYTES + 1)
    except http.client.HTTPException as exc:
        raise ValueError(f"That link could not be read: {exc}") from exc
    if len(data) > READING_LIST_IMPORT_MAX_BYTES:
        raise ValueError("That reading list is larger than 2 MB")
    return data, url


def import_reading_list(
    *, data: bytes | None = None, url: str | None = None, filename: str = "", user_id: int,
    replace_duplicate: bool = False,
) -> dict[str, Any]:
    """A CBL or JSON reading list, from a file or a link, kept as a story
    arc. The same link twice is the same arc; a file whose name and length
    match an arc already here is refused unless the admin says otherwise."""
    store = catalog_store()
    source_url = None
    if url:
        # The same list from the same place is the same arc, however the
        # link was spelled.
        existing = store.reading_list_by_source_url(_reading_list_link(url))
        if existing is not None:
            return {**reading_list_detail(existing, user_id=user_id), "existed": True}
        data, source_url = _fetch_reading_list(url)
    parsed = parse_reading_list(data or b"")
    name = parsed["name"] or Path(filename).stem or "Reading list"
    if not replace_duplicate:
        for entry in store.reading_lists_overview():
            if entry["source"] == "cbl" and entry["name"].casefold() == name.casefold() and len(entry["items"]) == len(parsed["items"]):
                raise ReadingListDuplicate(name, entry["id"])
    made = store.create_reading_list(
        name, description=parsed["description"], cover=(parsed["coverUrls"] or [None])[0], source="cbl",
        source_url=source_url, source_name=parsed["name"] or None, created_by=user_id, items=parsed["items"],
    )
    detail = reading_list_detail(int(made["id"]), user_id=user_id)
    if not detail["cover"]:
        # No picture of its own: the first comic that is here stands for it.
        first = next((item["fileCover"] for item in detail["items"] if item["fileCover"]), None)
        if first:
            store.update_reading_list(int(made["id"]), cover=first)
            detail["cover"] = first
    return {**detail, "existed": False}


def pull_reading_list(list_id: int, *, user_id: int) -> dict[str, Any]:
    """Ask for what the saved arc is missing -- the saved issues, so an issue
    taken out of the arc is never pulled for it."""
    detail = reading_list_detail(list_id, user_id=user_id)
    titles = {item["providerSeriesId"]: item["seriesTitle"] for item in detail["items"] if item.get("providerSeriesId")}
    result = _pull_missing_issues(detail["items"], titles)
    if not result["pulled"] and not result["failed"]:
        raise ValueError("Every issue of this arc is in your library or on the way")
    return {"name": detail["name"], **result}


def create_manual_reading_list(name: str, *, viewer: "Viewer", issue_ids: list[int] | None = None) -> dict[str, Any]:
    """An arc made by hand: the asker's own, private until shared, and either
    empty or holding the issue it was made from."""
    store = catalog_store()
    made = store.create_reading_list(name, source="manual", created_by=viewer.id, owner_user_id=viewer.id)
    if issue_ids:
        store.add_reading_list_issues(int(made["id"]), issue_ids)
    return reading_list_detail(int(made["id"]), user_id=viewer.id, viewer=viewer)


def export_reading_list(list_id: int, fmt: str, **visibility: Any) -> tuple[bytes, str]:
    """An arc as a CBL file, with only what the asking profile may see in it."""
    if fmt not in {"cbl", "json"}:
        raise ValueError("Export a story arc as cbl or json")
    detail = reading_list_detail(list_id, user_id=visibility["viewer"].id if visibility.get("viewer") else ADMIN_USER_ID,
                                 **visibility)
    body = write_reading_list(detail["name"], detail["items"], fmt=fmt, description=detail.get("description"))
    stem = re.sub(r"[^\w .()-]+", "", detail["name"]).strip() or "Story arc"
    return body, f"{stem}.{fmt}"


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
        worker_beat("metadata")
        try:
            _metadata_enrichment_step(store, stop_event)
        except Exception as exc:  # noqa: BLE001 -- one bad pass must not end the worker
            # Before 2026-10-05 an exception here ended the thread, and
            # metadata stopped until the next restart without a word.
            log_exception("metadata_worker_failed", exc, level="warning")
            worker_problem("metadata", exc)
            stop_event.wait(30)


def _metadata_enrichment_step(store: CatalogStore, stop_event: threading.Event) -> None:
    """One turn of the metadata worker's loop."""
    providers = [provider_id for provider_id, _values in _series_enrichment_provider_order()]
    if providers and not any(store.metadata_provider_available(provider_id) for provider_id in providers):
        stop_event.wait(5)
        return
    job = store.claim_metadata_enrichment_job()
    if job:
        run_metadata_enrichment_job(job)
        stop_event.wait(1)
        return
    monitored = store.claim_monitored_series_refresh()
    if not monitored:
        # Idle: ask about one run whose files name no creator, so search
        # by creator finds it too.
        try:
            synced = sync_next_run_creators(store)
        except Exception:
            synced = False
        stop_event.wait(1 if synced else 5)
        return
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


# ---------------------------------------------------------------------------
# Automatic library scans
#
# Comics added outside Flipparr stay invisible until a scan looks at the
# folder: Supergirl: Woman of Tomorrow #2 sat unseen for two days because
# nothing did. On by default; the interval is a setting.
# ---------------------------------------------------------------------------

AUTO_SCAN_POLL_SECONDS = 60
# A scan still "running" after this long was cut off -- a restart does it --
# and must not hold every automatic scan back for good.
AUTO_SCAN_STALE_SECONDS = 2 * 60 * 60
_AUTO_SCAN_STOP = threading.Event()
_AUTO_SCAN_THREAD: threading.Thread | None = None


def _auto_scan_time(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def auto_scan_due(settings: dict[str, Any], schedule: dict[str, Any], now: dt.datetime) -> bool:
    """Whether the background library scan should start now."""
    if not settings.get("autoScanEnabled") or not schedule.get("roots"):
        return False
    running_since = _auto_scan_time(schedule.get("activeSince"))
    if running_since and (now - running_since).total_seconds() < AUTO_SCAN_STALE_SECONDS:
        return False
    last = _auto_scan_time(schedule.get("lastScanAt"))
    if last is None:
        return True
    interval = int(settings.get("autoScanIntervalMinutes") or 60)
    return (now - last).total_seconds() >= interval * 60


def auto_scan_worker(stop_event: threading.Event = _AUTO_SCAN_STOP) -> None:
    """Scan every library folder, one at a time, whenever a scan is due.

    The same fast local scan the scan buttons start: filenames and the
    metadata inside files, no online lookups, unchanged files reused.
    """
    worker_beat("scans")
    while not stop_event.wait(AUTO_SCAN_POLL_SECONDS):
        worker_beat("scans")
        try:
            store = catalog_store()
            schedule = store.scan_schedule()
            if not auto_scan_due(load_app_settings(), schedule, dt.datetime.now(dt.timezone.utc)):
                continue
            for root in schedule["roots"]:
                scan_id = start_catalog_scan(root["path"], root["recursive"], "local")
                waited = 0
                while waited < AUTO_SCAN_STALE_SECONDS and not stop_event.wait(5):
                    worker_beat("scans")
                    waited += 5
                    if (store.get_scan(scan_id) or {}).get("status") not in {"queued", "scanning"}:
                        break
            log_event("auto_scan_finished", roots=len(schedule["roots"]))
        except Exception as exc:
            log_exception("auto_scan_failed", exc, level="warning")
            worker_problem("scans", exc)


def start_auto_scan_worker() -> threading.Thread:
    global _AUTO_SCAN_THREAD
    if _AUTO_SCAN_THREAD and _AUTO_SCAN_THREAD.is_alive():
        return _AUTO_SCAN_THREAD
    _AUTO_SCAN_STOP.clear()
    _AUTO_SCAN_THREAD = threading.Thread(
        target=auto_scan_worker,
        name="flipparr-auto-scan",
        daemon=True,
    )
    _AUTO_SCAN_THREAD.start()
    return _AUTO_SCAN_THREAD


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


# ---------------------------------------------------------------------------
# Background workers: activity, problems and supervision (Settings -> System)
#
# Five long-running threads do Flipparr's background work. Each says when it
# last did something (worker_beat) and what last went wrong (worker_problem);
# a supervisor restarts one that has died, a few times an hour at most, and
# every death is logged with its traceback. Before 2026-10-05 a worker could
# end on one unexpected exception and its work stop, unseen, until a restart.
# ---------------------------------------------------------------------------

PROCESS_STARTED_AT = time.time()
_WORKER_BEATS: dict[str, float] = {}
_WORKER_PROBLEMS: dict[str, dict[str, Any]] = {}
_WORKER_RESTARTS: dict[str, list[float]] = {}
_WORKERS_GIVEN_UP: set[str] = set()
_WORKERS_STOPPING = threading.Event()
WORKER_RESTARTS_PER_HOUR = 5


@dataclass(frozen=True)
class WorkerSpec:
    id: str
    name: str
    description: str
    thread_name: str
    # How long the worker may go without a beat while still being normal:
    # its own poll interval, with room to spare.
    quiet_seconds: int
    start: Callable[[], threading.Thread]
    thread: Callable[[], threading.Thread | None]


def worker_specs() -> tuple[WorkerSpec, ...]:
    return (
        WorkerSpec("metadata", "Metadata", "Fills in issue titles, dates and covers from your metadata sources.",
                   "flipparr-metadata-coordinator", 180, start_metadata_enrichment_worker, lambda: _ENRICHMENT_THREAD),
        WorkerSpec("imports", "Imports", "Picks up finished downloads and files them into your library.",
                   "flipparr-sab-import-coordinator", IMPORT_POLL_SECONDS * 4 + 120, start_acquisition_import_worker,
                   lambda: _IMPORT_THREAD),
        WorkerSpec("searches", "Release searches", "Searches again for wanted issues nothing was found for yet.",
                   "flipparr-release-research", RESEARCH_POLL_SECONDS * 2 + 300, start_release_research_worker,
                   lambda: _RESEARCH_THREAD),
        WorkerSpec("scans", "Library scans", "Scans your library folders on the schedule you set.",
                   "flipparr-auto-scan", AUTO_SCAN_POLL_SECONDS * 3, start_auto_scan_worker, lambda: _AUTO_SCAN_THREAD),
        WorkerSpec("ratings", "Age ratings", "Settles age ratings for runs that have none yet.",
                   "flipparr-ratings", RATING_POLL_SECONDS * 2 + 300, start_ratings_worker, lambda: _RATINGS_THREAD),
    )


def worker_beat(worker_id: str) -> None:
    _WORKER_BEATS[worker_id] = time.time()


def worker_problem(worker_id: str, problem: BaseException | str) -> None:
    message = f"{type(problem).__name__}: {problem}" if isinstance(problem, BaseException) else str(problem)
    _WORKER_PROBLEMS[worker_id] = {"at": _utc_now_iso(), "message": support_safe(message)[:500]}


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _iso(epoch: float | None) -> str | None:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="seconds") if epoch else None


def _thread_crashed(args: threading.ExceptHookArgs) -> None:
    """Every thread's uncaught exception, as a log line rather than a stray
    traceback on stderr -- and, for a worker, its last problem."""
    name = args.thread.name if args.thread else ""
    worker = next((spec.id for spec in worker_specs() if spec.thread_name == name), None)
    if args.exc_value is not None:
        log_exception("thread_crashed", args.exc_value, thread=name, worker=worker)
        if worker:
            worker_problem(worker, args.exc_value)


def supervise_workers(stop_event: threading.Event = _WORKERS_STOPPING, interval: float = 30.0) -> None:
    while not stop_event.wait(interval):
        restart_dead_workers()


def restart_dead_workers(now: float | None = None) -> list[str]:
    """Start again any worker whose thread has ended, unless it has already
    been restarted WORKER_RESTARTS_PER_HOUR times in the last hour -- then it
    stays stopped, so a worker that fails at once does not spin."""
    now = time.time() if now is None else now
    restarted = []
    for spec in worker_specs():
        thread = spec.thread()
        if thread is not None and thread.is_alive():
            continue
        if thread is None and spec.id not in _WORKER_BEATS:
            continue  # never started in this process (a test, a one-off command)
        recent = [at for at in _WORKER_RESTARTS.get(spec.id, []) if now - at < 3600]
        if len(recent) >= WORKER_RESTARTS_PER_HOUR:
            if spec.id not in _WORKERS_GIVEN_UP:
                _WORKERS_GIVEN_UP.add(spec.id)
                log_event("worker_stopped", level="error", worker=spec.id,
                          detail=f"Stopped after {len(recent)} restarts in an hour; restart Flipparr once the cause is fixed.")
            continue
        _WORKER_RESTARTS[spec.id] = recent + [now]
        log_event("worker_restarted", level="warning", worker=spec.id)
        spec.start()
        restarted.append(spec.id)
    return restarted


def start_worker_supervisor() -> threading.Thread:
    threading.excepthook = _thread_crashed
    _WORKERS_STOPPING.clear()
    thread = threading.Thread(target=supervise_workers, name="flipparr-supervisor", daemon=True)
    thread.start()
    return thread


def worker_states(now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    states = []
    for spec in worker_specs():
        thread = spec.thread()
        alive = thread is not None and thread.is_alive()
        beat = _WORKER_BEATS.get(spec.id)
        if alive:
            state = "running" if beat is not None and now - beat <= spec.quiet_seconds else "quiet"
        elif thread is None and beat is None:
            state = "off"
        else:
            state = "stopped" if spec.id in _WORKERS_GIVEN_UP else "restarting"
        states.append({
            "id": spec.id, "name": spec.name, "description": spec.description, "state": state,
            "lastActivityAt": _iso(beat),
            "lastProblem": _WORKER_PROBLEMS.get(spec.id),
            "restartsLastHour": len([at for at in _WORKER_RESTARTS.get(spec.id, []) if now - at < 3600]),
        })
    return states


# A support file is read by someone else: anything that could let them into
# the instance or a service is taken out of every string in it.
_SECRET_IN_TEXT = re.compile(
    r"(?i)((?:api[_-]?key|apikey|token|password|passwd|secret|session|cookie|authorization)\s*[=:]\s*)([^&\s\"',;]+)")
_URL_CREDENTIALS = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")


def support_safe(value: Any) -> Any:
    if isinstance(value, str):
        return _URL_CREDENTIALS.sub(r"\1<redacted>@", _SECRET_IN_TEXT.sub(r"\1<redacted>", value))
    if isinstance(value, dict):
        return {key: ("<redacted>" if re.search(r"(?i)key|token|password|secret|cookie|authorization", str(key))
                      and key not in {"tokenized"} else support_safe(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [support_safe(item) for item in value]
    return value


def _disk(path: Path | str) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}
    return {"path": str(path), "freeBytes": usage.free, "totalBytes": usage.total}


def recent_problems(limit: int = 50) -> list[dict[str, Any]]:
    with _LOG_LOCK:
        records = list(RECENT_PROBLEMS)[-limit:]
    problems = []
    for record in reversed(records):
        detail = {key: value for key, value in record.items()
                  if key not in {"ts", "level", "event", "error", "detail", "traceback"}}
        problems.append(support_safe({
            "at": record.get("ts"), "level": record.get("level"), "event": record.get("event"),
            "message": str(record.get("error") or record.get("detail") or "")[:500],
            "detail": detail,
            "traceback": record.get("traceback"),
        }))
    return problems


def system_status() -> dict[str, Any]:
    """Settings -> System: is the background work alive, what is waiting, and
    what went wrong lately. Nothing in it is a secret."""
    store = catalog_store()
    try:
        with sqlite3.connect(store.database_path) as connection:
            schema = connection.execute("SELECT version FROM schema_info").fetchone()[0]
    except sqlite3.Error:
        schema = None
    return {
        "version": APP_VERSION,
        "build": APP_BUILD,
        "startedAt": _iso(PROCESS_STARTED_AT),
        "uptimeSeconds": int(time.time() - PROCESS_STARTED_AT),
        "python": platform.python_version(),
        "database": {"sqlite": sqlite3.sqlite_version, "journalMode": getattr(store, "journal_mode", None),
                     "schema": schema},
        "server": {"name": "Waitress", "threads": http_thread_count()},
        "disk": {"config": _disk(store.database_path.parent), "temp": _disk(tempfile.gettempdir())},
        "workers": worker_states(),
        "work": store.background_work_summary(),
        "services": service_troubles(),
        "problems": recent_problems(),
    }


def diagnostics_report() -> dict[str, Any]:
    """The support file: the System page's facts plus what is configured --
    never a key, a password, a token or a service's address."""
    services = [{"id": service["id"], "configured": bool(service.get("configured")),
                 "enabled": bool(service.get("enabled"))}
                for service in public_acquisition_service_config().get("services", [])]
    providers = [{"id": provider.get("id"), "configured": bool(provider.get("configured")),
                  "enabled": bool(provider.get("enabled"))}
                 for provider in (public_provider_config().get("providers") or [])]
    report = {
        "generatedAt": _utc_now_iso(),
        "platform": {"system": platform.system(), "machine": platform.machine(), "release": platform.release()},
        **system_status(),
        "settings": load_app_settings(),
        "services": services,
        "providers": providers,
        "library": catalog_store().library_counts(),
    }
    return support_safe(report)


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


# The catalog is the whole library in one answer: 14 MB of JSON and 1.6 s to
# build for 2,800 files (measured 2026-10-05), and the page asks for it again
# on every visit and every few seconds while work is running. Built once, it
# is reused for as long as nothing it is made from has changed: the database
# (any commit touches the file or its write-ahead log), the settings and the
# provider settings. CATALOG_REUSE_SECONDS caps that, for the parts that
# depend on the clock -- an issue's release date arriving, a provider's
# cooldown ending. One viewer's ratings are not another's, so each has their own.
CATALOG_REUSE_SECONDS = 30
_CATALOG_CACHE: dict[int, tuple[Any, float, dict[str, Any]]] = {}
_CATALOG_BUILD_LOCK = threading.Lock()


def _catalog_stamp() -> tuple[Any, ...]:
    database = catalog_database_path()
    # The store too: a catalog built by one store is never another's answer.
    stamp: list[Any] = [str(database), id(catalog_store())]
    for path in (database, database.with_name(database.name + "-wal"), settings_config_path(), provider_config_path()):
        try:
            stat = path.stat()
            stamp.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamp.append(None)
    return tuple(stamp)


def catalog_api_payload(*, viewer_id: int) -> dict[str, Any]:
    """The catalog for one viewer, rebuilt only when something it is made
    from has changed (see CATALOG_REUSE_SECONDS). The top level and its stats
    are the caller's own to change; everything deeper is shared, read-only."""
    def fresh(entry: tuple[Any, float, dict[str, Any]] | None) -> bool:
        return entry is not None and entry[0] == _catalog_stamp() and time.monotonic() - entry[1] < CATALOG_REUSE_SECONDS

    entry = _CATALOG_CACHE.get(viewer_id)
    if not fresh(entry):
        # One build at a time: several pages asking at once wait for the one
        # answer instead of each spending the 1.6 s.
        with _CATALOG_BUILD_LOCK:
            entry = _CATALOG_CACHE.get(viewer_id)
            if not fresh(entry):
                # Stamped before the build, so a write that lands while it runs
                # is never missed: the next ask sees a newer stamp and builds
                # again. (Building can settle a job's state, itself a write;
                # that costs one more build, after which nothing changes.)
                stamp = _catalog_stamp()
                payload = _build_catalog_api_payload(viewer_id=viewer_id)
                entry = (stamp, time.monotonic(), payload)
                if len(_CATALOG_CACHE) >= 4 and viewer_id not in _CATALOG_CACHE:
                    _CATALOG_CACHE.pop(next(iter(_CATALOG_CACHE)))  # a household is few; memory is not
                _CATALOG_CACHE[viewer_id] = entry
    payload = entry[2]
    return {**payload, "stats": dict(payload.get("stats") or {})}


def _build_catalog_api_payload(*, viewer_id: int) -> dict[str, Any]:
    """Add provider availability to the public catalog without exposing credentials.

    Ratings in it are the viewer's own.
    """
    store = catalog_store()
    payload = store.catalog(preferred_language(), rater_id=viewer_id)
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
        for cookie in self._pending_cookies:
            self.send_header("Set-Cookie", cookie)
        self._pending_cookies = []
        # Returned on every response so a user can quote it and land on the
        # exact server-side record for their request.
        request_id = current_request_id()
        if request_id:
            self.send_header("X-Request-Id", request_id)
        # No page or image request tells another site where it came from: a
        # cover loaded from a provider would otherwise carry this instance's
        # address with it.
        self.send_header("Referrer-Policy", "no-referrer")

    def _client_address(self) -> str:
        """The caller's real address, believing a forwarded header only from a
        peer we were told to trust.

        This is what keeps the local-address bypass safe. A reverse proxy makes
        every request arrive from the proxy, so if X-Forwarded-For were trusted
        unconditionally anyone could send `X-Forwarded-For: 127.0.0.1` and be
        treated as local — which would silently disable authentication.
        """
        peer = self.client_address[0] if self.client_address else ""
        return resolve_client_address(peer, self.headers.get("X-Forwarded-For", ""))

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
        if is_trusted_proxy(peer):
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
        """Whether this request may reach `path` (GET), as the dispatcher decides it."""
        return allows(route_access("GET", path), viewer=self.viewer, household=self._household)

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

    viewer: Viewer | None = None
    _household = False
    _pending_cookies: tuple[str, ...] | list[str] = ()

    def _viewer(self) -> Viewer:
        """The profile this request speaks for. Routes past the gate always have one."""
        if self.viewer is None:
            raise RuntimeError("This route needs a profile, and the request has none")
        return self.viewer

    def _viewer_id(self) -> int:
        return self._viewer().id

    def _resolve_identity(self) -> None:
        """Who is asking, and whether this is a shared device.

        One profile and a shared device -- sign-in off, a device the admin
        signed in on, or the local network when that is allowed -- is the
        admin, exactly as before profiles. With more than one, a shared device
        has to be told who is reading. The local network no longer means
        "admin": on a NAS every device is local.
        """
        config = load_auth_config()
        store = _profiles_store()
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        viewer = None
        session = cookies.get(_SESSION_COOKIE)
        # Opened from a shared device's picker: a session re-issued on this
        # request (a new password, forgetting devices) stays one.
        self._shared_session = bool(session is not None and session.value.startswith("v3."))
        if session is not None and session.value:
            viewer, legacy = resolve_session_token(session.value, config, store)
            if viewer is not None and legacy:
                admin = store.user(ADMIN_USER_ID)
                self._sign_in_as(admin, config, device=True)
        device = cookies.get(_DEVICE_COOKIE)
        self._shared_device_cookie = bool(device is not None and device_token_valid(device.value, config))
        household = (
            config["method"] == "none"
            or self._shared_device_cookie
            or bool(config["localBypass"] and is_local_address(self._client_address()))
        )
        if viewer is None and household:
            enabled = [user for user in store.list_users() if not user["disabled"]]
            if len(enabled) == 1:
                viewer = viewer_for(enabled[0])
                # The device gets a real sign-in now, so it keeps its profile
                # when a second one is added -- rather than falling to the
                # picker mid-page. Adding the first reader ends every admin
                # sign-in but the adder's (`POST /api/v1/users`), so a device
                # that was the admin only because nobody else existed is not
                # left an admin.
                if self.path.startswith("/api/"):
                    self._sign_in_as(enabled[0], config, device=False, shared=True)
        self.viewer = viewer
        self._household = household

    def _set_cookie(self, name: str, value: str, max_age: int) -> None:
        attributes = [
            f"{name}={value}", "Path=/", "HttpOnly",
            # Lax keeps the cookie off cross-site POSTs, which is the CSRF risk
            # a cookie session introduces.
            "SameSite=Lax", f"Max-Age={max_age}",
        ]
        if self._request_is_https():
            attributes.append("Secure")
        if not isinstance(self._pending_cookies, list):
            self._pending_cookies = []
        # The last word on a cookie wins: an upgrade issued on the way in is
        # replaced by a sign-in made by the route itself.
        self._pending_cookies = [
            cookie for cookie in self._pending_cookies if not cookie.startswith(f"{name}=")
        ] + ["; ".join(attributes)]

    def _sign_in_as(self, user: dict[str, Any], config: dict[str, Any], *, device: bool, shared: bool = False) -> None:
        """Issue a profile's session, and with `device` make this a shared
        device. `shared` is a profile opened from a shared device's picker:
        forgetting the shared devices ends it."""
        if not config.get("sessionSecret"):
            # Sign-in off has never needed a secret; a profile cookie does.
            config = save_auth_config({})
        # Signing in makes a device shared, so the sign-in is the device's too.
        self._set_cookie(_SESSION_COOKIE, issue_profile_token(user, config, shared=shared or device), _SESSION_TTL_SECONDS)
        if device:
            self._set_cookie(_DEVICE_COOKIE, issue_device_token(config), _DEVICE_TTL_SECONDS)

    def _only_visible_runs(self, payload: Any) -> Any:
        """A reading list without the runs above this profile's rating: items
        and history by their run, and a by-run map by its keys."""
        viewer = self._viewer()
        if viewer is None or viewer.is_admin or not viewer.max_rating or not isinstance(payload, dict):
            return payload
        ratings = catalog_store().run_age_ratings()
        def keep(run_id: Any) -> bool:
            # No run: unrated, and the profile's unrated setting decides.
            return viewer_may_see_run(viewer, None if run_id in (None, "") else ratings.get(int(run_id)))
        for key in ("items", "history"):
            if isinstance(payload.get(key), list):
                payload[key] = [item for item in payload[key] if keep(item.get("seriesRunId"))]
        if isinstance(payload.get("runs"), dict):
            payload["runs"] = {run_id: value for run_id, value in payload["runs"].items() if keep(run_id)}
        return payload

    def _arc_visibility(self) -> dict[str, Any]:
        """How a story arc is trimmed for this profile. `_run_visible` does not
        know the arc routes -- an arc is many runs -- so their handlers leave
        out the issues of runs above the profile's rating themselves, as the
        Discover arc drawer does."""
        viewer = self._viewer()
        if viewer is None or viewer.is_admin or not viewer.max_rating:
            return {"viewer": viewer}
        ratings = catalog_store().run_age_ratings()
        return {"may_see": lambda run_id: viewer_may_see_run(viewer, ratings.get(int(run_id))),
                "may_see_unrated": viewer_may_see_run(viewer, None), "viewer": viewer}

    def _arc_gate(self, list_id: int, need: str = "see") -> dict[str, Any] | None:
        """The arc a route is about, or None with the answer sent: 404 for one
        this profile can't see -- a private arc is not there for anyone else --
        and 403 for one it sees but may not change (`need` "edit"), or unshare
        and delete ("manage")."""
        try:
            entry = catalog_store().reading_list_meta(list_id)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, 404)
            return None
        viewer = self._viewer()
        if not arc_visible_to(entry, viewer):
            self.send_json({"error": "That story arc is not in the library"}, 404)
            return None
        allowed = {"see": True, "edit": arc_editable_by(entry, viewer), "manage": arc_manageable_by(entry, viewer)}[need]
        if not allowed:
            self.send_json({"error": "Only the admin can change the household's story arcs" if entry["ownerId"] is None
                            else "Only the profile that made this story arc can change it"}, 403)
            return None
        return entry

    def _collection_gate(self, collection_id: int, need: str = "see") -> dict[str, Any] | None:
        """A collection's `_arc_gate`: 404 for one this profile can't see -- a
        private one is not there for anyone else -- and 403 for one it sees
        but may not change ("edit"), or unshare and delete ("manage")."""
        try:
            entry = catalog_store().run_collection(collection_id)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, 404)
            return None
        viewer = self._viewer()
        if not arc_visible_to(entry, viewer):
            self.send_json({"error": "That collection is not in the library"}, 404)
            return None
        allowed = {"see": True, "edit": arc_editable_by(entry, viewer), "manage": arc_manageable_by(entry, viewer)}[need]
        if not allowed:
            self.send_json({"error": "Only the admin can change the household's collections" if entry["ownerId"] is None
                            else "Only the profile that made this collection can change it"}, 403)
            return None
        return entry

    def _collection_payload(self, collection: dict[str, Any]) -> dict[str, Any]:
        """A collection as the asking profile is told it: its uploaded cover,
        and whose it is and what it may do with it."""
        return {**collection, "coverImage": uploaded_cover_url("collections", collection["id"]),
                **_arc_ownership(collection, self._viewer(), _profile_names())}

    def _reading_list_visible(self, kind: str, target_id: int) -> bool:
        """Whether this profile may see what it is adding to its reading list."""
        viewer = self._viewer()
        store = catalog_store()
        if kind == "arc":
            # Someone else's private arc is not there; its issues are trimmed
            # to the profile's rating when it is opened.
            try:
                return arc_visible_to(store.reading_list_meta(int(target_id)), viewer)
            except LookupError:
                return False
        if kind == "collection":
            try:
                if not arc_visible_to(store.run_collection(int(target_id)), viewer):
                    return False
            except LookupError:
                return False
        if viewer is None or viewer.is_admin or not viewer.max_rating:
            return True
        ratings = store.run_age_ratings()
        if kind == "run":
            return int(target_id) in ratings and viewer_may_see_run(viewer, ratings.get(int(target_id)))
        if kind == "collection":
            collection = next((item for item in store.run_collections() if item["id"] == str(int(target_id))), None)
            return bool(collection) and any(
                viewer_may_see_run(viewer, ratings.get(int(run_id))) for run_id in collection["runIds"])
        return True

    def _run_visible(self, path: str) -> bool:
        """Whether the run a reading route is about is one this profile may see."""
        store = catalog_store()
        run_id = None
        for pattern, lookup in (
            (r"/api/v1/files/(\d+)(?:/.*)?", store.run_for_file),
            (r"/api/v1/series/(\d+)(?:/.*)?", int),
            (r"/api/v1/issues/(\d+)(?:/.*)?", store.run_for_issue),
        ):
            match = re.fullmatch(pattern, path)
            if match:
                run_id = lookup(int(match.group(1)))
                break
        else:
            if path == "/api/file-cover":
                # Looked up by the path the handler will actually serve, not
                # the spelling in the query: `/comics/./X.cbz` names the same
                # file, and only the resolved form is in the catalog.
                source = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("path") or [""])[0]
                resolved = _cover_source_path(source)
                if resolved is None:
                    return True  # the handler refuses it
                run_id = store.run_for_path(str(resolved)) or store.run_for_path(source)
            else:
                return True
        # Nothing ties this to a run: it is unrated, and follows the
        # profile's unrated setting rather than being waved through.
        if run_id is None:
            return viewer_may_see_run(self.viewer, None)
        return viewer_may_see_run(self.viewer, store.run_age_ratings().get(int(run_id)))

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
        # A connection carries several requests; nothing about who asked
        # survives from one to the next.
        self.viewer = None
        self._household = False
        self._shared_device_cookie = False
        self._shared_session = False
        self._pending_cookies = []
        started = time.monotonic()
        try:
            access = route_access(self.command, path)
            try:
                if path != "/healthz":
                    self._resolve_identity()
            except AuthConfigUnreadable as exc:
                log_exception("auth_config_unreadable", exc, path=path)
                self.send_json({"error": f"Authentication is misconfigured: {exc}"}, 503)
                return
            if not allows(access, viewer=self.viewer, household=self._household):
                if self.viewer is None:
                    # A shared device that has not said who is reading gets
                    # the picker; anything else, the sign-in form.
                    reason = "profile_required" if self._household else "signin_required"
                    self.send_json({"error": "Authentication required", "reason": reason}, 401)
                elif access == HOUSEHOLD:
                    self.send_json({"error": "Profiles are switched on a shared device",
                                    "reason": "shared_device_only"}, 403)
                else:
                    self.send_json({"error": "Your profile can't do that", "reason": "admin_only"}, 403)
                return
            if self.command in {"POST", "PATCH", "DELETE"} and not self._same_origin():
                self.send_json({"error": "Cross-site request rejected"}, 403)
                return
            if self.viewer is not None and not self.viewer.is_admin:
                # A profile kept out of Discover, and one kept from a run above
                # its rating: the run answers as if it did not exist.
                if path.startswith("/api/v1/discover") and not self.viewer.can_discover:
                    self.send_json({"error": "Discover is off for this profile", "reason": "discover_off"}, 403)
                    return
                if self.viewer.max_rating and not self._run_visible(path):
                    self.send_json({"error": "Not found"}, 404)
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
                **({"client": self._client_address()} if cached_setting("logClientAddresses") else {}),
                user=self.viewer.id if self.viewer is not None else None,
            )
            _REQUEST_CONTEXT.request_id = None

    def do_GET(self) -> None:
        self._dispatch(self._route_get)

    def do_HEAD(self) -> None:
        # The GET route, answered without its body: access_policy already
        # treats HEAD as GET, and Waitress sends no body for HEAD. Uptime
        # monitors check with HEAD; it answered 501 until 2026-10-05.
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
            profile_required = self.viewer is None and self._household and len(profile_choices(_profiles_store(), load_auth_config())) > 1
            self.send_json({
                "method": config["method"],
                "authenticated": self.viewer is not None,
                "viewer": self.viewer.public() if self.viewer is not None else None,
                "household": self._household,
                "profileRequired": profile_required,
            })
            return
        if parsed_url.path == "/api/v1/profiles":
            self.send_json({"profiles": profile_choices(_profiles_store(), load_auth_config())})
            return
        avatar_get = re.fullmatch(r"/api/v1/profiles/(\d+)/avatar", parsed_url.path)
        if avatar_get:
            user = _profiles_store().user(int(avatar_get.group(1)))
            path = profile_avatar_path(int(avatar_get.group(1)))
            if user is None or user["disabled"] or not user["avatarSource"] or not path.is_file():
                self.send_json({"error": "No picture"}, 404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            # The URL carries the picture's version, so it can be kept.
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed_url.path == "/api/v1/notifications":
            self.send_json(notifications_for(self._viewer()))
            return
        if parsed_url.path == "/api/v1/me/reading-list":
            # Only this profile's rows, and only what it may see now: a run
            # rated above its limit since it was added is not listed.
            entries = catalog_store().reading_list_entries(self._viewer_id())
            entries["runs"] = [item for item in entries["runs"] if self._reading_list_visible("run", int(item["id"]))]
            entries["collections"] = [item for item in entries["collections"]
                                      if self._reading_list_visible("collection", int(item["id"]))]
            entries["arcs"] = [item for item in entries["arcs"] if self._reading_list_visible("arc", int(item["id"]))]
            self.send_json(entries)
            return
        if parsed_url.path == "/api/v1/me/activity":
            self.send_json(self._only_visible_runs(_profiles_store().reading_activity(self._viewer_id())))
            return
        if parsed_url.path == "/api/v1/me":
            store = _profiles_store()
            self.send_json({"profile": profile_record(store.user(self._viewer_id()), load_auth_config()),
                            "prefs": store.user_prefs(self._viewer_id())})
            return
        if parsed_url.path == "/api/v1/users":
            config = load_auth_config()
            self.send_json({"users": [profile_record(user, config) for user in _profiles_store().list_users()]})
            return
        if parsed_url.path == "/api/v1/auth":
            self.send_json(public_auth_config())
            return
        if parsed_url.path == "/api/v1/catalog":
            admin = self._viewer().is_admin
            payload = catalog_api_payload(viewer_id=self._viewer_id())
            # Someone else's private collection is not there, for the admin
            # too, as with story arcs; each says whose it is.
            viewer = self._viewer()
            payload = {**payload, "runCollections": [
                self._collection_payload(collection) for collection in payload.get("runCollections") or []
                if arc_visible_to(collection, viewer)]}
            payload = payload if admin else reader_catalog(payload, self._viewer())
            # Readers' requests: everyone's for the admin, a reader's own for them.
            store = catalog_store()
            payload["memberRequests"] = store.member_requests(user_id=None if admin else self._viewer_id(), limit=100)
            if admin:
                payload.setdefault("stats", {})["pendingRequests"] = store.pending_member_request_count()
                learn_about_waiting_requests(payload["memberRequests"])
            self.send_json(payload)
            return
        if parsed_url.path == "/api/v1/ratings":
            settings = load_app_settings()
            self.send_json({**catalog_store().rating_summary(),
                            "fromCovers": bool(settings.get("ratingsFromCovers")),
                            "visionConnected": vision_provider() is not None,
                            "metronConnected": metron_configured()})
            return
        if parsed_url.path == "/api/v1/member-requests":
            admin = self._viewer().is_admin
            self.send_json({"requests": catalog_store().member_requests(user_id=None if admin else self._viewer_id())})
            return
        if parsed_url.path == "/api/v1/settings":
            self.send_json(load_app_settings())
            return
        if parsed_url.path == "/api/v1/system/status":
            self.send_json(system_status())
            return
        if parsed_url.path == "/api/v1/system/diagnostics":
            body = json.dumps(diagnostics_report(), indent=2, ensure_ascii=False, default=str).encode("utf-8")
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="flipparr-diagnostics-{stamp}.json"')
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
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
        if parsed_url.path == "/api/v1/art-swatch":
            src = (urllib.parse.parse_qs(parsed_url.query).get("src") or [""])[0]
            try:
                body = art_swatch(src)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except ArtSwatchUnavailable as exc:
                self.send_json({"error": str(exc)}, 502)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
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
        if parsed_url.path == "/api/v1/discover/arc-suggestions":
            # From the kept lists only: no remote catalog is asked as someone types.
            query = (urllib.parse.parse_qs(parsed_url.query).get("query") or [""])[0]
            self.send_json(arc_suggestions(query, include_lists=self._viewer().is_admin))
            return
        if parsed_url.path == "/api/v1/discover/arcs":
            query = (urllib.parse.parse_qs(parsed_url.query).get("query") or [""])[0]
            try:
                self.send_json(discover_story_arcs(query))
            except Exception as exc:
                self.send_json({"arcs": [], "error": f"Story arcs are unavailable: {exc}"}, 502)
            return
        if parsed_url.path == "/api/v1/discover/arc":
            arc_id = (urllib.parse.parse_qs(parsed_url.query).get("id") or [""])[0]
            viewer = self._viewer()
            may_see = None
            if not viewer.is_admin and viewer.max_rating:
                ratings = catalog_store().run_age_ratings()
                may_see = lambda run_id: viewer_may_see_run(viewer, ratings.get(run_id))  # noqa: E731
            try:
                self.send_json(story_arc_detail(arc_id, may_see))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": f"This arc's issues could not be listed: {exc}"}, 502)
            return
        if parsed_url.path == "/api/v1/discover/releases":
            try:
                self.send_json(release_calendar())
            except Exception as exc:
                self.send_json({"error": f"Release dates are temporarily unavailable: {exc}"}, 502)
            return
        if parsed_url.path == "/api/v1/discover":
            query = urllib.parse.parse_qs(parsed_url.query).get("query", [""])[0]
            try:
                self.send_json(discover_series(query, extended=True))
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
        file_page_panels_match = re.fullmatch(r"/api/v1/files/(\d+)/pages/(\d+)/panels", parsed_url.path)
        if file_page_panels_match:
            try:
                payload = file_page_panels(
                    int(file_page_panels_match.group(1)), int(file_page_panels_match.group(2)),
                    allow_vision=self._viewer().is_admin or bool(load_app_settings().get("visionForReaders")),
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
                self.send_json({"error": str(exc) or "That page could not be read"}, 422)
                return
            self.send_json(payload)
            return
        file_page_match = re.fullmatch(r"/api/v1/files/(\d+)/pages/(\d+)", parsed_url.path)
        if file_page_match:
            try:
                body = render_file_page(
                    int(file_page_match.group(1)), int(file_page_match.group(2)),
                    str(self.query(parsed_url).get("size") or ""),
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
                self.send_json({"error": str(exc) or "That page could not be read"}, 422)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed_url.path == "/api/v1/reading":
            self.send_json(self._only_visible_runs(continue_reading(user_id=self._viewer_id())))
            return
        if parsed_url.path == "/api/v1/reading/runs":
            self.send_json(self._only_visible_runs(reading_by_run(user_id=self._viewer_id())))
            return
        if parsed_url.path == "/api/v1/reading/lists":
            self.send_json(reading_by_list(user_id=self._viewer_id(), **self._arc_visibility()))
            return
        if parsed_url.path == "/api/v1/reading-lists":
            self.send_json(reading_lists(**self._arc_visibility()))
            return
        arc_cover_image = re.fullmatch(r"/api/v1/reading-lists/(\d+)/cover/image", parsed_url.path)
        if arc_cover_image:
            if self._arc_gate(int(arc_cover_image.group(1))) is not None:
                self.handle_uploaded_cover(int(arc_cover_image.group(1)), "arcs")
            return
        collection_cover_image = re.fullmatch(r"/api/v1/run-collections/(\d+)/cover/image", parsed_url.path)
        if collection_cover_image:
            if not self._reading_list_visible("collection", int(collection_cover_image.group(1))):
                self.send_json({"error": "That collection is not in the library"}, 404)
                return
            self.handle_uploaded_cover(int(collection_cover_image.group(1)), "collections")
            return
        collection_backdrop_get = re.fullmatch(r"/api/v1/run-collections/(\d+)/backdrop", parsed_url.path)
        if collection_backdrop_get:
            if self._collection_gate(int(collection_backdrop_get.group(1))) is None:
                return
            try:
                self.send_json(run_collection_backdrop(int(collection_backdrop_get.group(1))))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
            return
        reading_list_backdrop_get = re.fullmatch(r"/api/v1/reading-lists/(\d+)/backdrop", parsed_url.path)
        if reading_list_backdrop_get:
            if self._arc_gate(int(reading_list_backdrop_get.group(1))) is None:
                return
            try:
                self.send_json(reading_list_backdrop(int(reading_list_backdrop_get.group(1))))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
            return
        reading_list_export = re.fullmatch(r"/api/v1/reading-lists/(\d+)/export", parsed_url.path)
        if reading_list_export:
            fmt = (urllib.parse.parse_qs(parsed_url.query).get("format") or ["cbl"])[0]
            try:
                body, filename = export_reading_list(int(reading_list_export.group(1)), fmt, **self._arc_visibility())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8" if fmt == "json" else "application/xml; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=\"{}\"; filename*=UTF-8''{}".format(
                filename.encode("ascii", "ignore").decode().replace('"', "") or f"Story arc.{fmt}",
                urllib.parse.quote(filename)))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Vary", "Cookie")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        reading_list_get = re.fullmatch(r"/api/v1/reading-lists/(\d+)", parsed_url.path)
        if reading_list_get:
            try:
                self.send_json(reading_list_detail(int(reading_list_get.group(1)), user_id=self._viewer_id(),
                                                   **self._arc_visibility()))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
            return
        series_reading_match = re.fullmatch(r"/api/v1/series/(\d+)/reading", parsed_url.path)
        if series_reading_match:
            try:
                payload = series_reading(int(series_reading_match.group(1)), user_id=self._viewer_id())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(payload)
            return
        file_progress_match = re.fullmatch(r"/api/v1/files/(\d+)/progress", parsed_url.path)
        if file_progress_match:
            try:
                payload = file_reading_progress(int(file_progress_match.group(1)), user_id=self._viewer_id())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(payload)
            return
        file_pages_match = re.fullmatch(r"/api/v1/files/(\d+)/pages", parsed_url.path)
        if file_pages_match:
            try:
                payload = file_pages(int(file_pages_match.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 422)
                return
            self.send_json(payload)
            return
        series_backdrop_match = re.fullmatch(r"/api/v1/series/(\d+)/backdrop", parsed_url.path)
        if series_backdrop_match:
            try:
                payload = series_backdrop(int(series_backdrop_match.group(1)))
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
        issue_detail_match = re.fullmatch(r"/api/v1/issues/(\d+)/detail", parsed_url.path)
        if issue_detail_match:
            try:
                payload = issue_detail(int(issue_detail_match.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            # No 502 branch: `issue_detail` answers a provider failure with
            # `status: "unavailable"` rather than raising.
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

    def _may_change_profile(self, user_id: int) -> bool:
        """A profile changes its own picture; the admin changes anyone's."""
        viewer = self._viewer()
        return viewer.id == user_id or viewer.is_admin

    def _route_post(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        avatar_upload = re.fullmatch(r"/api/v1/profiles/(\d+)/avatar/upload", parsed_url.path)
        if avatar_upload:
            user_id = int(avatar_upload.group(1))
            if not self._may_change_profile(user_id):
                self.send_json({"error": "Your profile can't do that", "reason": "admin_only"}, 403)
                return
            if _profiles_store().user(user_id) is None:
                self.send_json({"error": "That profile does not exist"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_json({"error": "Invalid Content-Length"}, 400)
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}:
                self.send_json({"error": "Upload a JPEG, PNG, WebP, GIF, or HEIC image"}, 415)
                return
            if length <= 0 or length > COVER_SOURCE_MAX_BYTES:
                self.send_json({"error": "A picture must be no larger than 50 MB"}, 413)
                return
            try:
                updated = profile_avatar_from_upload(user_id, self.rfile.read(length))
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc) or "That picture could not be read"}, 422)
                return
            self.send_json(updated)
            return
        cover_upload_match = re.fullmatch(r"/api/v1/files/(\d+)/cover/upload", parsed_url.path)
        if cover_upload_match:
            self.handle_cover_upload(int(cover_upload_match.group(1)))
            return
        # A comic the user fetched themselves, sent as a raw body like the
        # covers below and for the same reason: it is not JSON.
        manual_import_match = re.fullmatch(r"/api/v1/acquisition-jobs/(\d+)/import", parsed_url.path)
        if manual_import_match:
            self.handle_manual_import(int(manual_import_match.group(1)))
            return
        # Both upload routes carry a raw image, so they are matched before the
        # body is read as JSON.
        series_upload_match = re.fullmatch(r"/api/v1/series/(\d+)/cover/upload", parsed_url.path)
        if series_upload_match:
            self.handle_cover_upload(int(series_upload_match.group(1)), "series")
            return
        arc_upload_match = re.fullmatch(r"/api/v1/reading-lists/(\d+)/cover/upload", parsed_url.path)
        if arc_upload_match:
            self.handle_cover_upload(int(arc_upload_match.group(1)), "arcs")
            return
        collection_upload_match = re.fullmatch(r"/api/v1/run-collections/(\d+)/cover/upload", parsed_url.path)
        if collection_upload_match:
            self.handle_cover_upload(int(collection_upload_match.group(1)), "collections")
            return
        if parsed_url.path == "/api/v1/reading-lists/import":
            # A reading list file as a raw body, or JSON naming a link.
            self.handle_reading_list_import()
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
            store = _profiles_store()
            key, hashed, user = login_subject(username, config, store)
            wait = _throttle_wait(key, hashed)
            if wait:
                body, status, headers = _too_many(wait)
                body["error"] = f"Too many failed sign-ins. Try again in {wait} second{'s' if wait != 1 else ''}."
                self.send_json(body, status, headers=headers)
                return
            # An unknown name still pays for a hash, so the answer's timing
            # says nothing about which names exist.
            succeeded = verify_password(password, hashed or _DUMMY_PASSWORD_HASH) and bool(hashed) and user is not None
            _throttle_record(key, hashed, succeeded)
            if not succeeded:
                # One message for both cases: a distinct "no such user" reply
                # would let an attacker enumerate valid usernames.
                self.send_json({"error": "Incorrect username or password"}, 401)
                return
            # The admin signing in makes this a shared device; a reader
            # signing in on their own phone does not.
            self._sign_in_as(user, config, device=user["role"] == "admin")
            self.send_json({"status": "authenticated", "viewer": viewer_for(user).public()})
            return
        if parsed_url.path == "/api/v1/auth/logout":
            if payload.get("forgetDevice"):
                self._set_cookie(_DEVICE_COOKIE, "", 0)
            self.send_session_cookie("", expire=True)
            return
        avatar_set = re.fullmatch(r"/api/v1/profiles/(\d+)/avatar", parsed_url.path)
        if avatar_set:
            user_id = int(avatar_set.group(1))
            if not self._may_change_profile(user_id):
                self.send_json({"error": "Your profile can't do that", "reason": "admin_only"}, 403)
                return
            try:
                file_id = int(payload.get("fileId"))
                page = int(payload.get("page") or 0)
                if _profiles_store().user(user_id) is None:
                    raise LookupError("That profile does not exist")
                updated = profile_avatar_from_library(user_id, file_id, page)
            except (TypeError, ValueError) as exc:
                self.send_json({"error": str(exc) or "Choose a comic from the library"}, 400)
                return
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except (zipfile.BadZipFile, KeyError, OSError) as exc:
                self.send_json({"error": str(exc) or "That page could not be read"}, 422)
                return
            self.send_json(updated)
            return
        if parsed_url.path == "/api/v1/profiles/switch":
            config = load_auth_config()
            store = _profiles_store()
            try:
                user = check_profile_switch(
                    store, config, int(payload.get("userId") or 0), payload.get("pin"), payload.get("password"),
                )
            except Throttled as exc:
                body, status, headers = _too_many(exc.wait)
                self.send_json(body, status, headers=headers)
                return
            except (LookupError, ValueError, TypeError):
                self.send_json({"error": "That profile is not available"}, 404)
                return
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
                return
            self._sign_in_as(user, config, device=False, shared=True)
            self.send_json({"status": "switched", "viewer": viewer_for(user).public()})
            return
        if parsed_url.path == "/api/v1/users":
            store = _profiles_store()
            config = load_auth_config()
            first_reader = store.reader_count() == 0
            try:
                created = create_profile(store, config, payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            if first_reader and created["role"] == "reader":
                # Until now every shared device was the admin by default, and
                # may hold the admin's cookie from then. From here the admin
                # is the admin's alone: those sign-ins end, the adder's is
                # issued afresh.
                store.bump_session_version(ADMIN_USER_ID)
                if self._viewer_id() == ADMIN_USER_ID:
                    self._sign_in_as(store.user(ADMIN_USER_ID), config, device=config["method"] == "forms")
            self.send_json(profile_record(created, config), 201)
            return
        if parsed_url.path == "/api/v1/ratings/check":
            self.send_json({"lookingAgain": look_for_ratings_again()})
            return
        if parsed_url.path == "/api/v1/member-requests":
            try:
                request, created = ask_member_request(self._viewer(), payload)
            except AlreadyHandled as exc:
                self.send_json({"status": "already", "message": str(exc)})
                return
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
                return
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(request, 201 if created else 200)
            return
        # One pattern each, so the route census sees three routes and their
        # three access classes rather than one.
        member_request_action = next((
            (int(match.group(1)), action) for action, match in (
                ("cancel", re.fullmatch(r"/api/v1/member-requests/(\d+)/cancel", parsed_url.path)),
                ("approve", re.fullmatch(r"/api/v1/member-requests/(\d+)/approve", parsed_url.path)),
                ("decline", re.fullmatch(r"/api/v1/member-requests/(\d+)/decline", parsed_url.path)),
            ) if match
        ), None)
        if member_request_action:
            request_id, action = member_request_action
            store = catalog_store()
            existing = store.member_request(request_id)
            # Cancelling is the asker's; approving and declining the admin's,
            # which the route's access class already enforces.
            if existing is None or (action == "cancel" and existing["requestedBy"]["id"] != self._viewer_id()):
                self.send_json({"error": "That request does not exist"}, 404)
                return
            try:
                if action == "approve":
                    result = approve_member_request(request_id, self._viewer_id())
                else:
                    result = store.claim_member_request(
                        request_id, self._viewer_id(), "cancelled" if action == "cancel" else "declined",
                        (payload or {}).get("reason") if action == "decline" else None,
                    )
                    notify_request_decided(result)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 409)
                return
            self.send_json(result)
            return
        # A profile's own bell: read, cleared, and what it dismissed of the
        # things needing attention. Each profile reaches only its own rows.
        if parsed_url.path in ("/api/v1/notifications/read", "/api/v1/notifications/clear"):
            body = payload if isinstance(payload, dict) else {}
            ids = body.get("ids")
            if body.get("all") is True:
                ids = None
            elif not isinstance(ids, list) or len(ids) > 500 \
                    or not all(type(value) is int and 0 < value < 2 ** 53 for value in ids):
                self.send_json({"error": "Say which notifications (ids), or all: true"}, 400)
                return
            store = catalog_store()
            if parsed_url.path.endswith("/read"):
                store.mark_notifications_read(self._viewer_id(), ids)
            else:
                store.clear_notifications(self._viewer_id(), ids)
            self.send_json(notifications_for(self._viewer()))
            return
        if parsed_url.path == "/api/v1/notifications/dismissed":
            body = payload if isinstance(payload, dict) else {}
            add, remove = body.get("add") or [], body.get("remove") or []
            if not all(isinstance(value, list) and all(isinstance(key, str) for key in value) for value in (add, remove)):
                self.send_json({"error": "Dismissals are lists of keys"}, 400)
                return
            dismissed = catalog_store().change_notification_dismissals(self._viewer_id(), add[:500], remove[:500])
            self.send_json({"dismissed": dismissed})
            return
        if parsed_url.path == "/api/v1/devices/forget":
            # Every shared device, and every profile opened on one, is
            # forgotten -- except this one: the admin doing it keeps their
            # place, and this device stays shared if it was.
            config = forget_shared_devices()
            admin = _profiles_store().user(self._viewer_id())
            self._sign_in_as(admin, config, device=self._shared_device_cookie,
                             shared=self._shared_session or self._shared_device_cookie)
            self.send_json({"status": "forgotten"})
            return
        users_sign_out = re.fullmatch(r"/api/v1/users/(\d+)/sign-out", parsed_url.path)
        if users_sign_out:
            try:
                _profiles_store().bump_session_version(int(users_sign_out.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json({"status": "signed out everywhere"})
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
                admin = _profiles_store().user(ADMIN_USER_ID)
                self._sign_in_as(admin, updated, device=True)
                self.send_json(public_auth_config())
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

        if parsed_url.path == "/api/v1/discover/pull-arc":
            try:
                # Pulling an arc keeps it: the issues without their order would
                # be the gap the saved arc exists to close.
                saved = save_story_arc(str((payload or {}).get("arcId") or ""), user_id=self._viewer_id())
                result = {**pull_story_arc(str((payload or {}).get("arcId") or "")), "readingListId": saved["id"]}
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"The arc could not be pulled: {exc}"}, 502)
                return
            # Each series' pull started its own searches, as any pull does.
            self.send_json(result)
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
        reading_list_add = re.fullmatch(r"/api/v1/me/reading-list/(run|arc|collection)/(\d+)", parsed_url.path)
        if reading_list_add:
            kind, target = reading_list_add.group(1), int(reading_list_add.group(2))
            try:
                if not self._reading_list_visible(kind, target):
                    raise LookupError("That is not in the library")
                catalog_store().set_reading_list_entry(self._viewer_id(), kind, target, True)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(catalog_store().reading_list_entries(self._viewer_id()))
            return
        if parsed_url.path == "/api/v1/run-collections":
            # Any profile's (schema 63): the maker's own, private until shared,
            # as a hand-made story arc is.
            body = payload if isinstance(payload, dict) else {}
            try:
                run_ids = _run_id_list(body.get("seriesIds"))
                if any(not self._run_visible(f"/api/v1/series/{run_id}") for run_id in run_ids):
                    raise LookupError("That run is not in the library")
                made = self._collection_payload(catalog_store().create_run_collection(
                    body.get("name"), run_ids,
                    body.get("summary") if isinstance(body.get("summary"), str) else None,
                    created_by=self._viewer_id(), owner_user_id=self._viewer_id(),
                ))
            except (LookupError, CollectionNameTaken) as exc:
                self.send_json({"error": str(exc)}, 409)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(made, 201)
            return
        if parsed_url.path == "/api/v1/ratings/runs":
            # Many runs rated at once (Settings > Profiles > Ratings); null
            # sends them back to what was found. The admin's alone: unlisted.
            body = payload if isinstance(payload, dict) else {}
            ids = body.get("seriesIds")
            if not isinstance(ids, list) or len(ids) > 5000 or not all(
                    isinstance(item, (int, str)) and str(item).isdigit() for item in ids):
                self.send_json({"error": "Choose the runs to rate"}, 400)
                return
            try:
                count = catalog_store().set_run_rating_overrides([int(item) for item in ids], body.get("rating") or None)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 409)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "saved", "count": count})
            return
        series_age_rating = re.fullmatch(r"/api/v1/series/(\d+)/age-rating", parsed_url.path)
        if series_age_rating:
            # The admin's own rating for a run; null goes back to what was found.
            try:
                catalog_store().set_run_rating_override(int(series_age_rating.group(1)), (payload or {}).get("rating") or None)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "saved"})
            return
        series_format = re.fullmatch(r"/api/v1/series/(\d+)/format", parsed_url.path)
        if series_format:
            try:
                # Absent leaves the direction alone; an explicit null means
                # "follow the medium" again.
                direction = payload["readingDirection"] if "readingDirection" in payload else ""
                result = catalog_store().set_series_format(
                    int(series_format.group(1)), str(payload.get("format") or ""), direction,
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
                result = search_release_candidates(
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
        acquisition_stop = re.fullmatch(r"/api/v1/acquisition-jobs/(\d+)/stop", parsed_url.path)
        if acquisition_stop:
            try:
                # `pack`: every issue coming in the same torrent, at once.
                stop = stop_acquisition_pack if payload.get("pack") is True else stop_acquisition_download
                self.send_json(stop(int(acquisition_stop.group(1))))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
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
                result = grab_release_candidate(
                    job_id, str(payload.get("candidateId") or "").strip(),
                    anyway=bool(payload.get("anyway")),
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
        # POST with a null clears, the way progress does. The plan said PUT and
        # DELETE; this server has no do_PUT and clears everything else this
        # way, and one idiom beats a tidier verb.
        rating_post = re.fullmatch(r"/api/v1/(issues|series)/(\d+)/rating", parsed_url.path)
        if rating_post:
            kind = "issue" if rating_post.group(1) == "issues" else "series"
            try:
                result = catalog_store().set_rating(
                    kind, int(rating_post.group(2)), payload.get("rating"), user_id=self._viewer_id(),
                )
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        file_progress_post = re.fullmatch(r"/api/v1/files/(\d+)/progress", parsed_url.path)
        if file_progress_post:
            file_id = int(file_progress_post.group(1))
            page = payload.get("page")
            try:
                if "read" in payload:
                    # Marked read or unread outright, without a page: the check
                    # on an issue's cover.
                    if not isinstance(payload["read"], bool):
                        raise ValueError("read is true or false")
                    result = mark_file_read(file_id, payload["read"], user_id=self._viewer_id())
                elif page is None:
                    catalog_store().clear_reading_progress(file_id, user_id=self._viewer_id())
                    result = file_reading_progress(file_id, user_id=self._viewer_id())
                else:
                    if not isinstance(page, int) or isinstance(page, bool):
                        raise ValueError("A page is a whole number")
                    panel = payload.get("panel", 0)
                    if not isinstance(panel, int) or isinstance(panel, bool):
                        raise ValueError("A panel is a whole number")
                    result = set_file_reading_progress(file_id, page, panel, user_id=self._viewer_id())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/reading-lists":
            if str(payload.get("provider") or "metron") != "metron":
                self.send_json({"error": "Story arcs are saved from Metron"}, 400)
                return
            try:
                saved = save_story_arc(str(payload.get("arcId") or ""), user_id=self._viewer_id())
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:  # noqa: BLE001 -- the provider, not the library
                self.send_json({"error": f"This arc's issues could not be listed: {exc}"}, 502)
                return
            self.send_json(saved, 200 if saved.get("existed") else 201)
            return
        if parsed_url.path == "/api/v1/reading-lists/manual":
            issue_ids = payload.get("issueIds") or []
            if not isinstance(issue_ids, list) or not all(isinstance(item, (int, str)) and str(item).isdigit() for item in issue_ids):
                self.send_json({"error": "Say which issues to start the story arc with"}, 400)
                return
            if any(not self._run_visible(f"/api/v1/issues/{int(item)}") for item in issue_ids):
                self.send_json({"error": "That issue is not in the library"}, 404)
                return
            try:
                made = create_manual_reading_list(str(payload.get("name") or ""), viewer=self._viewer(),
                                                  issue_ids=[int(item) for item in issue_ids])
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(made, 201)
            return
        reading_list_items_post = re.fullmatch(r"/api/v1/reading-lists/(\d+)/items", parsed_url.path)
        if reading_list_items_post:
            list_id = int(reading_list_items_post.group(1))
            issue_ids = payload.get("issueIds")
            if not isinstance(issue_ids, list) or not issue_ids or not all(
                    isinstance(item, (int, str)) and str(item).isdigit() for item in issue_ids):
                self.send_json({"error": "Say which issues to add"}, 400)
                return
            if self._arc_gate(list_id, "edit") is None:
                return
            if any(not self._run_visible(f"/api/v1/issues/{int(item)}") for item in issue_ids):
                self.send_json({"error": "That issue is not in the library"}, 404)
                return
            try:
                outcome = catalog_store().add_reading_list_issues(list_id, [int(item) for item in issue_ids])
                result = reading_list_detail(list_id, user_id=self._viewer_id(), **self._arc_visibility())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({**result, **outcome})
            return
        reading_list_reading_post = re.fullmatch(r"/api/v1/reading-lists/(\d+)/reading", parsed_url.path)
        if reading_list_reading_post:
            if not isinstance(payload.get("read"), bool):
                self.send_json({"error": "Say whether the arc is read (true) or unread (false)"}, 400)
                return
            try:
                result = mark_reading_list_reading(int(reading_list_reading_post.group(1)), payload["read"],
                                                   user_id=self._viewer_id(), **self._arc_visibility())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(result)
            return
        collection_backdrop_post = re.fullmatch(r"/api/v1/run-collections/(\d+)/backdrop", parsed_url.path)
        if collection_backdrop_post:
            collection_id = int(collection_backdrop_post.group(1))
            if self._collection_gate(collection_id, "edit") is None:
                return
            try:
                if str(payload.get("source") or "") == "auto":
                    catalog_store().clear_run_collection_backdrop(collection_id)
                    result = run_collection_backdrop(collection_id)
                else:
                    page = payload.get("page")
                    if not isinstance(page, int) or isinstance(page, bool):
                        raise ValueError("Choose a page")
                    result = choose_run_collection_backdrop(collection_id, str(payload.get("fileId") or ""), page)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        reading_list_backdrop_post = re.fullmatch(r"/api/v1/reading-lists/(\d+)/backdrop", parsed_url.path)
        if reading_list_backdrop_post:
            list_id = int(reading_list_backdrop_post.group(1))
            if self._arc_gate(list_id, "edit") is None:
                return
            try:
                if str(payload.get("source") or "") == "auto":
                    catalog_store().clear_reading_list_backdrop(list_id)
                    result = reading_list_backdrop(list_id)
                else:
                    page = payload.get("page")
                    if not isinstance(page, int) or isinstance(page, bool):
                        raise ValueError("Choose a page")
                    result = choose_reading_list_backdrop(list_id, str(payload.get("fileId") or ""), page)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(result)
            return
        reading_list_pull = re.fullmatch(r"/api/v1/reading-lists/(\d+)/pull", parsed_url.path)
        reading_list_refresh = re.fullmatch(r"/api/v1/reading-lists/(\d+)/refresh", parsed_url.path)
        if reading_list_pull or reading_list_refresh:
            if self._arc_gate(int((reading_list_pull or reading_list_refresh).group(1))) is None:
                return
            try:
                if reading_list_pull:
                    result = pull_reading_list(int(reading_list_pull.group(1)), user_id=self._viewer_id())
                else:
                    result = refresh_reading_list(int(reading_list_refresh.group(1)), user_id=self._viewer_id())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:  # noqa: BLE001 -- the provider, not the library
                self.send_json({"error": f"This arc's issues could not be listed: {exc}"}, 502)
                return
            self.send_json(result)
            return
        series_reading_post = re.fullmatch(r"/api/v1/series/(\d+)/reading", parsed_url.path)
        if series_reading_post:
            if not isinstance(payload.get("read"), bool):
                self.send_json({"error": "Say whether the run is read (true) or unread (false)"}, 400)
                return
            try:
                result = mark_run_reading(int(series_reading_post.group(1)), payload["read"], user_id=self._viewer_id())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(result)
            return
        series_backdrop_post = re.fullmatch(r"/api/v1/series/(\d+)/backdrop", parsed_url.path)
        if series_backdrop_post:
            run_id = int(series_backdrop_post.group(1))
            try:
                if str(payload.get("source") or "") == "auto":
                    catalog_store().clear_series_backdrop(run_id)
                    result = series_backdrop(run_id)
                else:
                    page = payload.get("page")
                    if not isinstance(page, int) or isinstance(page, bool):
                        raise ValueError("Choose a page")
                    result = choose_series_backdrop(run_id, str(payload.get("fileId") or ""), page)
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
        collection_patch = re.fullmatch(r"/api/v1/run-collections/(\d+)", parsed_url.path)
        if collection_patch:
            body = payload if isinstance(payload, dict) else {}
            shared = body.get("shared")
            if shared is not None and not isinstance(shared, bool):
                self.send_json({"error": "Say whether the household shares this collection (true or false)"}, 400)
                return
            # Only its maker changes or shares it; the admin may take a shared
            # one back out of the household's view.
            changes_it = any(key in body for key in (
                "name", "summary", "sortMode", "coverSeriesId", "coverImage", "add", "remove", "order"))
            entry = self._collection_gate(int(collection_patch.group(1)), "edit" if changes_it or shared else "manage")
            if entry is None:
                return
            if shared is not None and entry["ownerId"] is None:
                self.send_json({"error": "The household's collections are already everyone's"}, 400)
                return
            try:
                changes: dict[str, Any] = {}
                if shared is not None:
                    changes["shared"] = shared
                for key in ("name", "summary", "sortMode"):
                    if key in body:
                        if body[key] is not None and not isinstance(body[key], str):
                            raise ValueError(f"{key} is text")
                        changes[key] = body[key]
                if "coverSeriesId" in body:
                    cover = body["coverSeriesId"]
                    if cover is not None and not str(cover).isdigit():
                        raise ValueError("Choose a cover from the collection's runs")
                    changes["coverSeriesId"] = cover
                # An uploaded picture is the cover until it is taken away.
                if "coverImage" in body and body["coverImage"] is not None:
                    raise ValueError("Upload a picture to /cover/upload; coverImage only takes null")
                drop_upload = "coverImage" in body
                for key in ("add", "remove", "order"):
                    if key in body:
                        changes[key] = _run_id_list(body[key])
                if any(not self._run_visible(f"/api/v1/series/{run_id}") for run_id in changes.get("add") or []):
                    raise LookupError("That run is not in the library")
                updated = catalog_store().update_run_collection(int(collection_patch.group(1)), changes)
                if drop_upload:
                    remove_uploaded_cover("collections", updated["id"])
                if not arc_visible_to(updated, self._viewer()):
                    # The admin took someone's collection back out of the
                    # household: done, and no longer theirs to see.
                    self.send_json({"id": updated["id"], "shared": False, "visible": False})
                    return
                updated = self._collection_payload(updated)
            except CollectionNameTaken as exc:
                self.send_json({"error": str(exc)}, 409)
                return
            except LookupError as exc:
                missing = "not in the library" in str(exc) and "collection" in str(exc)
                self.send_json({"error": str(exc)}, 404 if missing else 409)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json(updated)
            return
        reading_list_patch = re.fullmatch(r"/api/v1/reading-lists/(\d+)", parsed_url.path)
        if reading_list_patch:
            list_id = int(reading_list_patch.group(1))
            remove = payload.get("remove")
            order = payload.get("order")
            shared = payload.get("shared")
            if (remove is not None and not isinstance(remove, list)) or (order is not None and not isinstance(order, list)):
                self.send_json({"error": "Say which issues to remove, or the order to keep them in"}, 400)
                return
            if shared is not None and not isinstance(shared, bool):
                self.send_json({"error": "Say whether the household shares this story arc (true or false)"}, 400)
                return
            for key in ("description", "sortMode"):
                if key in payload and not isinstance(payload[key], str):
                    self.send_json({"error": f"{key} is text"}, 400)
                    return
            changes_arc = bool(remove or order) or any(
                isinstance(payload.get(key), str) for key in ("name", "cover", "description", "sortMode"))
            # Only the maker shares an arc; the admin may take a shared one
            # back out of the household's view.
            entry = self._arc_gate(list_id, "edit" if changes_arc or shared else "manage")
            if entry is None:
                return
            if shared is not None and entry["ownerId"] is None:
                self.send_json({"error": "The household's story arcs are already everyone's"}, 400)
                return
            try:
                if shared is not None:
                    catalog_store().set_reading_list_shared(list_id, shared)
                if remove:
                    catalog_store().remove_reading_list_items(list_id, [int(item) for item in remove])
                if order:
                    catalog_store().reorder_reading_list(list_id, [int(item) for item in order])
                if isinstance(payload.get("name"), str) or isinstance(payload.get("cover"), str):
                    cover = payload.get("cover") if isinstance(payload.get("cover"), str) else None
                    if cover is not None and cover not in _reading_list_cover_choices(list_id):
                        raise ValueError("Choose a cover from the arc's own comics")
                    catalog_store().update_reading_list(
                        list_id, name=payload.get("name") if isinstance(payload.get("name"), str) else None, cover=cover)
                if isinstance(payload.get("description"), str) or isinstance(payload.get("sortMode"), str):
                    catalog_store().update_reading_list(
                        list_id, description=payload.get("description"), sort_mode=payload.get("sortMode"))
                if not arc_visible_to(catalog_store().reading_list_meta(list_id), self._viewer()):
                    # The admin took someone's arc back out of the household:
                    # done, and no longer theirs to see.
                    self.send_json({"id": str(list_id), "shared": False, "visible": False})
                    return
                result = reading_list_detail(list_id, user_id=self._viewer_id(), **self._arc_visibility())
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except (ValueError, TypeError) as exc:
                self.send_json({"error": str(exc) or "That is not an issue of this arc"}, 400)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/me/prefs":
            try:
                prefs = own_prefs_patch(_profiles_store(), self._viewer_id(), payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"prefs": prefs})
            return
        me = parsed_url.path == "/api/v1/me"
        users_match = re.fullmatch(r"/api/v1/users/(\d+)", parsed_url.path)
        if me or users_match:
            store = _profiles_store()
            config = load_auth_config()
            target_id = self._viewer_id() if me else int(users_match.group(1))
            target = store.user(target_id)
            if target is None:
                self.send_json({"error": "That profile does not exist"}, 404)
                return
            if me:
                # A profile about itself: its name, colour, PIN, and -- for a
                # reader -- a sign-in name and password. Roles and permissions
                # are the admin's to give.
                payload = {key: value for key, value in (payload or {}).items()
                           if key in ("name", "colour", "pin", "loginName", "password", "currentPassword", "switchLock")}
            try:
                changes = _profile_changes(store, config, target, payload or {}, by_admin=not me)
                updated = store.update_user(target_id, **changes)
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, 403)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            if target_id == self._viewer_id() and updated["sessionVersion"] != target["sessionVersion"]:
                # A new password ends every sign-in this profile had -- except
                # the one making the change, which is issued afresh -- and one
                # opened on a shared device stays a shared device's, so
                # forgetting the shared devices still ends it.
                self._sign_in_as(updated, config, device=False, shared=self._shared_session or self._shared_device_cookie)
            self.send_json(profile_record(updated, config))
            return
        page_panels_match = re.fullmatch(r"/api/v1/files/(\d+)/pages/(\d+)/panels", parsed_url.path)
        if page_panels_match:
            try:
                result = save_page_panels(int(page_panels_match.group(1)), int(page_panels_match.group(2)), payload)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
                self.send_json({"error": str(exc) or "That page could not be read"}, 400)
                return
            self.send_json(result)
            return
        if parsed_url.path == "/api/v1/settings":
            try:
                was_reading_covers = bool(load_app_settings().get("ratingsFromCovers"))
                updated = save_app_settings(payload if isinstance(payload, dict) else {})
                if updated.get("ratingsFromCovers") and not was_reading_covers:
                    look_for_ratings_again()
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
        reading_list_remove = re.fullmatch(r"/api/v1/me/reading-list/(run|arc|collection)/(\d+)", parsed_url.path)
        if reading_list_remove:
            catalog_store().set_reading_list_entry(
                self._viewer_id(), reading_list_remove.group(1), int(reading_list_remove.group(2)), False)
            self.send_json(catalog_store().reading_list_entries(self._viewer_id()))
            return
        collection_delete = re.fullmatch(r"/api/v1/run-collections/(\d+)", parsed_url.path)
        if collection_delete:
            if self._collection_gate(int(collection_delete.group(1)), "manage") is None:
                return
            try:
                catalog_store().delete_run_collection(int(collection_delete.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            remove_uploaded_cover("collections", collection_delete.group(1))
            self.send_json({"status": "removed"})
            return
        reading_list_delete = re.fullmatch(r"/api/v1/reading-lists/(\d+)", parsed_url.path)
        if reading_list_delete:
            if self._arc_gate(int(reading_list_delete.group(1)), "manage") is None:
                return
            try:
                catalog_store().delete_reading_list(int(reading_list_delete.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            remove_uploaded_cover("arcs", reading_list_delete.group(1))
            self.send_json({"status": "removed"})
            return
        avatar_clear = re.fullmatch(r"/api/v1/profiles/(\d+)/avatar", parsed_url.path)
        if avatar_clear:
            user_id = int(avatar_clear.group(1))
            if not self._may_change_profile(user_id):
                self.send_json({"error": "Your profile can't do that", "reason": "admin_only"}, 403)
                return
            try:
                updated = clear_profile_avatar(user_id)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json(updated)
            return
        users_match = re.fullmatch(r"/api/v1/users/(\d+)", parsed_url.path)
        if users_match:
            user_id = int(users_match.group(1))
            if user_id == self._viewer_id():
                self.send_json({"error": "Switch to another admin profile to remove this one"}, 400)
                return
            try:
                _profiles_store().delete_user(user_id)
                profile_avatar_path(user_id).unlink(missing_ok=True)
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "removed"})
            return
        page_panels_match = re.fullmatch(r"/api/v1/files/(\d+)/pages/(\d+)/panels", parsed_url.path)
        if page_panels_match:
            try:
                result = forget_page_panels(int(page_panels_match.group(1)), int(page_panels_match.group(2)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
                self.send_json({"error": str(exc) or "That page could not be read"}, 422)
                return
            self.send_json(result)
            return
        file_panels_match = re.fullmatch(r"/api/v1/files/(\d+)/panels", parsed_url.path)
        if file_panels_match:
            try:
                result = forget_file_panels(int(file_panels_match.group(1)))
            except LookupError as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            except (OSError, ValueError) as exc:
                self.send_json({"error": str(exc) or "That comic could not be read"}, 422)
                return
            self.send_json(result)
            return
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
        self._set_cookie(_SESSION_COOKIE, "" if expire else token, 0 if expire else _SESSION_TTL_SECONDS)
        self.send_json(
            payload if payload is not None
            else {"status": "signed out" if expire else "authenticated"}
        )

    def send_json(self, payload: Any, status: int = 200,
                  headers: dict[str, str] | None = None) -> None:
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
        # And by cookie: one profile's answers are not another's.
        self.send_header("Vary", "Accept-Encoding, Cookie")
        headers = {"Cache-Control": "private, no-store", **(headers or {})}
        for name, value in headers.items():
            self.send_header(name, value)
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
        content_type = (
            WEB_ASSET_TYPES.get(target.suffix.lower())
            or mimetypes.guess_type(target.name)[0]
            or "application/octet-stream"
        )
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
        path = _cover_source_path(query.get("path"))
        if path is None:
            self.send_json({"error": "That comic is not in the library"}, 403)
            return
        # Whether a cover can be pulled is a question about the archive, which
        # archive_kind answers by reading it. Gating on the extension here kept
        # refusing every RAR comic after the rest of the app had learned to
        # read them: the cover was found and then could not be served.
        if archive_kind(path) is None:
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

    def handle_manual_import(self, job_id: int) -> None:
        """Take a comic file for one wanted issue, streamed rather than buffered.

        A cover is read whole into memory at 50MB; a comic can be twenty times
        that, so the body goes to disk in chunks as it arrives.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"error": "Invalid Content-Length"}, 400)
            return
        if length <= 0 or length > MANUAL_IMPORT_MAX_BYTES:
            self.send_json({"error": "Upload a comic file no larger than 2 GB"}, 413)
            return
        # Percent-encoded by the page: a header holds only ASCII, and a comic's
        # name often does not -- "Batman – Superman – World's Finest #35" has
        # an en dash, and sending it raw failed in the browser before any
        # request was made. A plain name decodes to itself.
        filename = urllib.parse.unquote(str(self.headers.get("X-Filename") or "")).strip()
        if not filename:
            self.send_json({"error": "Send the comic's filename in an X-Filename header"}, 400)
            return
        try:
            result = import_uploaded_comic(job_id, self.rfile, length, filename)
        except DownloadContentMismatch as exc:
            # The file is readable and is not that issue. Worth its own status:
            # nothing is wrong with the request, the comic is simply not it.
            self.send_json({"error": str(exc)}, 422)
            return
        except (LookupError, ValueError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001
            log_exception("manual_import_failed", exc, level="warning")
            self.send_json({"error": f"The comic could not be imported: {exc}"}, 500)
            return
        self.send_json(result, 201)

    def handle_reading_list_import(self) -> None:
        content_type = str(self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            if content_type == "application/json":
                payload = self.read_json_body()
                result = import_reading_list(url=str(payload.get("url") or ""), user_id=self._viewer_id(),
                                             replace_duplicate=bool(payload.get("force")))
            else:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_json({"error": "Invalid Content-Length"}, 400)
                    return
                if length <= 0 or length > READING_LIST_IMPORT_MAX_BYTES:
                    self.send_json({"error": "Upload a reading list no larger than 2 MB"}, 413)
                    return
                filename = urllib.parse.unquote(str(self.headers.get("X-Filename") or "")).strip()
                result = import_reading_list(data=self.rfile.read(length), filename=filename, user_id=self._viewer_id(),
                                             replace_duplicate=str(self.headers.get("X-Import-Anyway") or "") == "1")
        except ReadingListDuplicate as exc:
            self.send_json({"error": str(exc), "readingListId": exc.list_id}, 409)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        except (urllib.error.URLError, OSError) as exc:
            self.send_json({"error": f"The reading list could not be fetched: {getattr(exc, 'reason', exc)}"}, 502)
            return
        self.send_json(result, 200 if result.get("existed") else 201)

    def handle_cover_upload(self, entity_id: int, kind: str = "files") -> None:
        """Take a raw image body for a comic file, a series run, a story arc or
        a collection.

        Only the existence check and the setter differ between them; the
        type gate, the size limit, the thumbnailing and the atomic write are
        the same job either way. An arc's cover is changed by whoever may
        change the arc (`_arc_gate`).
        """
        if kind == "arcs":
            if self._arc_gate(entity_id, "edit") is None:
                return
        elif kind == "collections":
            if self._collection_gate(entity_id, "edit") is None:
                return
        else:
            try:
                if kind == "series":
                    catalog_store().get_series_cover_workbench(entity_id)
                elif kind == "collections":
                    catalog_store().run_collection(entity_id)
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
            if kind == "arcs":
                catalog_store().update_reading_list(entity_id, cover=uploaded_cover_url("arcs", entity_id))
                result = reading_list_detail(entity_id, user_id=self._viewer_id(), **self._arc_visibility())
            elif kind == "collections":
                result = self._collection_payload(catalog_store().run_collection(entity_id))
            elif kind == "series":
                result = catalog_store().set_series_cover_preference(entity_id, "upload")
            else:
                result = catalog_store().set_file_cover_preference(entity_id, "upload")
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


# ---- Serving -----------------------------------------------------------------
#
# Python documents http.server as not for production: ThreadingHTTPServer
# starts a thread for every connection, without limit, and never times out a
# client that stops halfway through a request. Measured 2026-10-04 with 300
# stalled connections: http.server grew to 300 threads and dropped none;
# Cheroot stayed at 19 threads but real requests timed out behind the stalls;
# Waitress stayed at 18, answered a real request in 0.02 s and dropped all 300
# at its timeout. Flipparr is served by Waitress (Pylons, ZPL 2.1): network I/O
# runs in one asynchronous loop, so a slow or silent client never holds one of
# the worker threads that run the routes. It runs in this process, beside the
# background workers and the state they share.
#
# Waitress reads a whole request before handing it to a worker, spilling a body
# over 512 KB to a temporary file. A comic upload can be 2 GB and the container's
# /tmp is a 256 MB RAM disk, so main() points temporary files at the config
# volume (serving_temp_dir).
#
# The routes stay as they are: _WSGIHandler gives Handler the request a socket
# would have, and takes back the status, headers and body it writes.

_HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                         "te", "trailers", "transfer-encoding", "upgrade"})


class _WSGIHandler(Handler):
    """Handler, fed from a WSGI environ rather than a socket of its own."""

    def __init__(self, environ: dict[str, Any]) -> None:  # noqa: D107 -- no socket to read
        self.command = str(environ.get("REQUEST_METHOD") or "GET").upper()
        raw = environ.get("REQUEST_URI")
        if not raw:
            raw = urllib.parse.quote(str(environ.get("PATH_INFO") or "/").encode("latin-1"), safe="/:@!$&'()*+,;=~-._")
            if environ.get("QUERY_STRING"):
                raw += "?" + str(environ["QUERY_STRING"])
        self.path = str(raw)
        self.request_version = str(environ.get("SERVER_PROTOCOL") or "HTTP/1.1")
        self.requestline = f"{self.command} {self.path} {self.request_version}"
        headers = email.message.Message()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                headers[key[5:].replace("_", "-").title()] = str(value)
        for key, name in (("CONTENT_TYPE", "Content-Type"), ("CONTENT_LENGTH", "Content-Length")):
            if environ.get(key):
                headers[name] = str(environ[key])
        self.headers = headers
        self.rfile = environ["wsgi.input"]
        self.wfile = io.BytesIO()
        self.client_address = (str(environ.get("REMOTE_ADDR") or ""), int(environ.get("REMOTE_PORT") or 0))
        self.close_connection = False
        self._headers_buffer = []
        self.wsgi_status: str | None = None
        self.wsgi_headers: list[tuple[str, str]] = []

    def send_response_only(self, code: int, message: str | None = None) -> None:
        if message is None:
            message = self.responses[code][0] if code in self.responses else ""
        self.wsgi_status = f"{int(code)} {message}".strip()

    def send_header(self, keyword: str, value: str) -> None:
        # Hop-by-hop headers are the server's (PEP 3333). So is Server: the
        # stdlib's names the exact Python version; Waitress says "flipparr".
        if keyword.lower() not in _HOP_BY_HOP and keyword.lower() != "server":
            self.wsgi_headers.append((str(keyword), str(value)))

    def end_headers(self) -> None:
        pass

    def respond(self) -> tuple[str, list[tuple[str, str]], bytes]:
        method = getattr(self, f"do_{self.command}", None)
        if method is None:
            self.send_error(501, f"Unsupported method ({self.command!r})")
        else:
            method()
        if self.wsgi_status is None:
            return "500 Internal Server Error", [("Content-Length", "0")], b""
        return self.wsgi_status, self.wsgi_headers, self.wfile.getvalue()


def wsgi_app(environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
    status, headers, body = _WSGIHandler(environ).respond()
    start_response(status, headers)
    return [body]


def http_thread_count() -> int:
    """Worker threads running routes: FLIPPARR_HTTP_THREADS, else 32. Sixteen
    was measured too few on 2026-10-05: one library page asks for 18 covers at
    once and a panel read can hold a thread for ten seconds, and requests
    queued behind them."""
    try:
        return max(4, min(int(_env("HTTP_THREADS", "32")), 128))
    except ValueError:
        return 32


class _ServerLogHandler(logging.Handler):
    """Waitress's own notices, as lines of the structured log rather than
    bare text on stderr. A queue-depth notice is information -- requests
    waited a moment for a free thread -- and not a problem to list."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            queue_notice = record.name == "waitress.queue"
            level = "info" if queue_notice or record.levelno < logging.WARNING else (
                "error" if record.levelno >= logging.ERROR else "warning")
            log_event("server_queue" if queue_notice else "server_notice", level=level,
                      detail=record.getMessage()[:500], logger=record.name)
        except Exception:  # noqa: BLE001 -- logging must never raise
            pass


def _route_server_logs() -> None:
    logger = logging.getLogger("waitress")
    if not any(isinstance(handler, _ServerLogHandler) for handler in logger.handlers):
        logger.addHandler(_ServerLogHandler())
        logger.setLevel(logging.INFO)
        logger.propagate = False


def make_server(host: str, port: int) -> tuple[Any, int]:
    """The production server, bound and ready to run(). Returns it with the
    port it bound (port 0 picks a free one, for tests)."""
    import waitress

    _route_server_logs()
    server = waitress.create_server(
        wsgi_app, host=host, port=port,
        threads=http_thread_count(),
        ident="flipparr",
        # Idle connections cost a file descriptor, not a thread, so the limit
        # sits well above what a household opens: at Waitress's default of 100,
        # 100 stalled connections lock everyone else out. poll(), not select(),
        # which cannot watch more than 1,024 sockets.
        connection_limit=1000,
        asyncore_use_poll=True,
        # Seconds a connection may sit with nothing happening before it is closed.
        channel_timeout=30,
        max_request_header_size=64 * 1024,
        # The largest body any route takes (a manual comic import), plus room
        # for its framing; each route still checks its own, smaller limit.
        max_request_body_size=MANUAL_IMPORT_MAX_BYTES + 1024 * 1024,
        # Responses are built in memory already (a page image is a few MB);
        # copying them to a temporary file on the way out would only add disk I/O.
        outbuf_overflow=64 * 1024 * 1024,
        # X-Forwarded-For and friends are judged by Flipparr itself, against
        # FLIPPARR_TRUSTED_PROXIES (resolve_client_address); Waitress must pass
        # them through untouched rather than strip them.
        clear_untrusted_proxy_headers=False,
    )
    return server, int(server.effective_port)


def serving_temp_dir() -> Path | None:
    """Temporary files beside the library's data, not in the container's
    256 MB /tmp: uploads Waitress holds until they are complete, and comic packs
    being unpacked. <config>/tmp, or a `flipparr-tmp` folder inside
    FLIPPARR_TEMP_DIR; emptied at start, since anything left there belongs to
    a process that has gone. Only ever a folder of Flipparr's own is emptied:
    the setting names where to put it, so pointing it at /config or the
    library cannot delete either.

    None when the folder cannot be made -- a config folder the app may not
    write, as in a container started without its volume. The server still
    starts and says what is wrong; large uploads then fall back on /tmp."""
    chosen = _env("TEMP_DIR")
    folder = Path(chosen) / "flipparr-tmp" if chosen else catalog_database_path().parent / "tmp"
    try:
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log_event("temp_dir_unavailable", level="warning", path=str(folder), error=str(exc),
                  detail="Uploads larger than the free space in the default temporary folder will fail.")
        return None
    return folder


def reset_password_command(username: str | None, password_stdin: bool) -> int:
    """`app.py reset-password`: the way back in for an owner who has lost the
    password. There is no email to send a reset link to, so the proof of
    ownership is being able to run a command on the server."""
    import getpass
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat it: ") != password:
            print("The passwords did not match; nothing was changed.", file=sys.stderr)
            return 1
    try:
        updated = reset_password(username, password)
    except (ValueError, AuthConfigUnreadable) as exc:
        print(f"{exc}; nothing was changed.", file=sys.stderr)
        return 1
    state = ("Sign-in is on" if updated["method"] == "forms"
             else "Sign-in is still off; switch it on in Settings -> Security")
    print(f"Password for '{updated['username']}' changed and every device signed out. {state}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="serve", choices=("serve", "reset-password"),
                        help="serve (the default) or reset-password")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--username", help="reset-password: also change the username")
    parser.add_argument("--password-stdin", action="store_true",
                        help="reset-password: read the new password from standard input")
    args = parser.parse_args()
    if args.command == "reset-password":
        sys.exit(reset_password_command(args.username, args.password_stdin))
    load_persisted_remote_cache()
    load_persisted_provider_cache()
    try:
        # Which SQLite this is decides WAL or the rollback journal
        # (catalog_store.journal_mode_for); said once, so a log shows it.
        log_event("sqlite_runtime", version=sqlite3.sqlite_version,
                  journal_mode=getattr(catalog_store(), "journal_mode", None))
        recovered = catalog_store().recover_interrupted_searches()
        acquisition_staging_dir("direct_site")
        restarted = recover_interrupted_direct_downloads()
        if restarted:
            log_event("interrupted_direct_downloads_recovered", downloads=restarted)
        if recovered:
            log_event("interrupted_searches_recovered", jobs=recovered)
    except Exception as exc:
        log_exception("interrupted_search_recovery_failed", exc, level="warning")
    start_metadata_enrichment_worker()
    start_acquisition_import_worker()
    start_release_research_worker()
    start_auto_scan_worker()
    start_ratings_worker()
    start_worker_supervisor()
    temp_dir = serving_temp_dir()
    if temp_dir is not None:
        tempfile.tempdir = str(temp_dir)
    server, port = make_server(args.host, args.port)

    def stop(signum: int, _frame: Any) -> None:
        # `docker stop` sends SIGTERM. Waitress's loop answers SystemExit by
        # letting the requests already running finish (up to 5 s) before it
        # returns, instead of the process dying mid-write.
        log_event("server_stopping", signal=signal.Signals(signum).name)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    log_event("server_started", host=args.host, port=port, threads=http_thread_count(),
              tempDir=tempfile.gettempdir(), version=APP_VERSION, build=APP_BUILD)
    try:
        server.run()
    finally:
        _WORKERS_STOPPING.set()  # first, or the supervisor restarts what is stopping
        _ENRICHMENT_STOP.set()
        _IMPORT_STOP.set()
        log_event("server_stopped")


if __name__ == "__main__":
    main()
