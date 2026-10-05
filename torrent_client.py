"""qBittorrent's Web API, and the little of BitTorrent Flipparr has to read.

Pure on purpose: no catalog, no settings, no app imports -- app.py decides
what to download and what a finished download means; this module only talks
to the client and names a torrent. Sources, read 2026-09-29/30 from the
qBittorrent source (src/webui/api/torrentscontroller.cpp,
src/base/bittorrent/torrentimpl.cpp, WebAPI_Changelog.md) and
https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-5.0):

- Cookie login with a Referer matching the host; "Fails." in the body for bad
  credentials before WebAPI 2.14 and a 401 from it; 403 when the address is
  banned.
- `torrents/add` reads `stopped` from 5.0 and `paused` before it; each
  ignores the other, so both are sent. `stopCondition=MetadataReceived`
  exists from qBittorrent 4.5.5 (WebAPI 2.8.19): older clients ignore it and
  would download a whole pack, so `supports_selection()` gates it.
- `torrents/stop`/`start` from 5.0, `pause`/`resume` before; the 4.x names
  answer 404 on 5.x and the other way round.
- `torrents/files` is `[]` until the metadata is known; `filePrio` works on a
  stopped torrent.
- "Complete" states are this module's reading of the enum -- the docs do not
  define it. With only some files wanted, `progress` reaches 1.0 and the
  state goes to an `*UP` state once those files are done.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


class TorrentClientError(RuntimeError):
    """The client answered, and the answer was no."""


class TorrentAuthError(TorrentClientError):
    """The client would not let Flipparr in."""


# A torrent whose wanted data is all here. `moving` is not: qBittorrent is
# still carrying the files from its incomplete folder, and `content_path`
# points at where they were. `checkingUP` is a recheck of finished data --
# treated as still working until it ends.
_COMPLETE_STATES = frozenset({
    "uploading", "stalledup", "stoppedup", "pausedup", "queuedup", "forcedup",
})
# Stopped by the client with its data complete: what qBittorrent does when a
# torrent reaches its ratio or seeding-time limit and the action is Stop.
_STOPPED_COMPLETE_STATES = frozenset({"stoppedup", "pausedup"})
_ERROR_STATES = frozenset({"error", "missingfiles"})
# Stopped before it finished: added to wait for its file list, or paused by hand.
_STOPPED_INCOMPLETE_STATES = frozenset({"stoppeddl", "pauseddl"})
# The first WebAPI version whose `torrents/add` takes `stopCondition`.
SELECTION_WEBAPI = (2, 8, 19)


def state_class(state: Any) -> str:
    """ "complete", "error" or "downloading" -- everything not proven finished
    or broken is still in progress, including states a newer client adds."""
    value = str(state or "").strip().casefold()
    if value in _COMPLETE_STATES:
        return "complete"
    if value in _ERROR_STATES:
        return "error"
    return "downloading"


def stopped_after_seeding(state: Any) -> bool:
    """Whether the client has finished with a torrent: complete and stopped."""
    return str(state or "").strip().casefold() in _STOPPED_COMPLETE_STATES


def stopped_before_finishing(state: Any) -> bool:
    return str(state or "").strip().casefold() in _STOPPED_INCOMPLETE_STATES


def tags_of(torrent: dict[str, Any]) -> set[str]:
    """A torrent's tags; the API joins them with a comma and a space."""
    return {tag.strip() for tag in str((torrent or {}).get("tags") or "").split(",") if tag.strip()}


def _bdecode_at(data: bytes, index: int) -> tuple[Any, int]:
    """One bencoded value starting at `index`, and where it ends."""
    if index >= len(data):
        raise ValueError("truncated")
    lead = data[index:index + 1]
    if lead == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1:end]), end + 1
    if lead == b"l":
        index += 1
        items = []
        while data[index:index + 1] != b"e":
            value, index = _bdecode_at(data, index)
            items.append(value)
        return items, index + 1
    if lead == b"d":
        index += 1
        mapping: dict[bytes, Any] = {}
        while data[index:index + 1] != b"e":
            key, index = _bdecode_at(data, index)
            if not isinstance(key, bytes):
                raise ValueError("a dictionary key that is not a string")
            mapping[key], index = _bdecode_at(data, index)
        return mapping, index + 1
    if lead.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        end = colon + 1 + length
        if end > len(data):
            raise ValueError("truncated")
        return data[colon + 1:end], end
    raise ValueError("not bencoded")


def bdecode(data: bytes) -> Any:
    """A bencoded document as Python values; strings stay bytes."""
    try:
        value, end = _bdecode_at(bytes(data), 0)
    except (ValueError, IndexError, RecursionError) as exc:
        raise ValueError(f"This is not a torrent file: {exc}") from exc
    if end != len(data):
        raise ValueError("This is not a torrent file: data after its end")
    return value


def info_hash(torrent: bytes) -> str:
    """A .torrent file's v1 info hash: SHA-1 of its `info` dictionary exactly
    as the file writes it, which is why the bytes are found rather than
    re-encoded."""
    data = bytes(torrent)
    if data[:1] != b"d":
        raise ValueError("This is not a torrent file")
    try:
        index = 1
        while data[index:index + 1] != b"e":
            key, index = _bdecode_at(data, index)
            start = index
            _value, index = _bdecode_at(data, index)
            if key == b"info":
                if data[start:start + 1] != b"d":
                    break
                return hashlib.sha1(data[start:index]).hexdigest()  # noqa: S324 -- the protocol's identifier
    except (ValueError, IndexError, RecursionError) as exc:
        raise ValueError(f"This is not a torrent file: {exc}") from exc
    raise ValueError("This is not a torrent file: it has no info dictionary")


def looks_like_torrent(data: bytes) -> bool:
    try:
        info_hash(data)
    except ValueError:
        return False
    return True


def magnet_hash(link: Any) -> str | None:
    """The v1 info hash a magnet link names, as lowercase hex, or None.

    `xt=urn:btih:` carries it as forty hex digits or thirty-two base32
    characters; a v2-only link (`urn:btmh:`) names nothing qBittorrent's v1
    hash list can be asked for.
    """
    text = str(link or "").strip()
    if not text.casefold().startswith("magnet:"):
        return None
    for value in urllib.parse.parse_qs(urllib.parse.urlsplit(text).query).get("xt", []):
        if not value.casefold().startswith("urn:btih:"):
            continue
        digest = value[len("urn:btih:"):].strip()
        if len(digest) == 40:
            try:
                int(digest, 16)
            except ValueError:
                continue
            return digest.casefold()
        if len(digest) == 32:
            try:
                return base64.b32decode(digest.upper()).hex()
            except (binascii.Error, ValueError):
                continue
    return None


def parse_version(text: Any) -> tuple[int, ...]:
    """ "2.8.19" -> (2, 8, 19); anything unreadable is (0,)."""
    parts = []
    for piece in str(text or "").strip().lstrip("v").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


class QBittorrent:
    """One qBittorrent instance, spoken to over its Web API.

    Credentials are optional: a client that lets its own network in without
    a login (subnet whitelisting, common behind a VPN container) answers
    without a cookie, and then none is asked for.
    """

    def __init__(
        self, url: str, username: str = "", password: str = "", *,
        timeout: float = 20.0, user_agent: str = "Flipparr",
        opener: Callable[..., Any] | None = None, refused_wait: float = 0.0,
    ) -> None:
        self.url = str(url or "").rstrip("/")
        self.username = str(username or "")
        self.password = str(password or "")
        self.timeout = timeout
        self.user_agent = user_agent
        self._open = opener or urllib.request.urlopen
        self._cookie = ""
        self._webapi: tuple[int, ...] | None = None
        # After a refused sign-in, how long before trying again; meanwhile the
        # refusal is repeated without asking. qBittorrent bans an address
        # after a few failed sign-ins (five, by default, for an hour), and a
        # wrong password asked every 15 seconds -- once per torrent a pass --
        # walked straight into it. Zero, as for a person's Test, always asks.
        self.refused_wait = refused_wait
        self._refused: tuple[float, str] | None = None

    # ---- transport --------------------------------------------------------

    def _send(self, method: str, path: str, *, query: dict[str, Any] | None = None,
              body: bytes | None = None, content_type: str | None = None) -> tuple[int, bytes, Any]:
        address = f"{self.url}{path}"
        if query:
            address += "?" + urllib.parse.urlencode(query)
        headers = {"Referer": self.url, "User-Agent": self.user_agent, "Accept": "*/*"}
        if self._cookie:
            headers["Cookie"] = self._cookie
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(address, data=body, headers=headers, method=method)
        try:
            with self._open(request, timeout=self.timeout) as response:
                return int(getattr(response, "status", 200) or 200), response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read() or b"", exc.headers

    def _call(self, method: str, path: str, *, query: dict[str, Any] | None = None,
              form: dict[str, Any] | None = None, multipart: tuple[bytes, str] | None = None) -> tuple[int, bytes]:
        def once() -> tuple[int, bytes]:
            if multipart is not None:
                status, body, _headers = self._send(method, path, query=query, body=multipart[0], content_type=multipart[1])
            elif form is not None:
                status, body, _headers = self._send(
                    method, path, query=query, body=urllib.parse.urlencode(form).encode(),
                    content_type="application/x-www-form-urlencoded",
                )
            else:
                status, body, _headers = self._send(method, path, query=query)
            return status, body

        self._still_refused()
        status, body = once()
        if status in (401, 403):
            # No session, or one that has expired. Signed in once and asked again.
            self.login()
            status, body = once()
            if status in (401, 403):
                raise TorrentAuthError("qBittorrent would not accept Flipparr's sign-in")
        return status, body

    def login(self) -> None:
        """Sign in and keep the session cookie. Without a username there is
        nothing to sign in with, and the caller's 403 stands."""
        if not self.username:
            raise TorrentAuthError("qBittorrent wants a username and password")
        self._still_refused()
        try:
            self._sign_in()
        except TorrentAuthError as exc:
            self._refused = (time.monotonic(), str(exc))
            raise
        self._refused = None

    def _still_refused(self) -> None:
        if self._refused and time.monotonic() - self._refused[0] < self.refused_wait:
            raise TorrentAuthError(self._refused[1])

    def _sign_in(self) -> None:
        self._cookie = ""
        status, body, headers = self._send(
            "POST", "/api/v2/auth/login",
            body=urllib.parse.urlencode({"username": self.username, "password": self.password}).encode(),
            content_type="application/x-www-form-urlencoded",
        )
        text = body.decode("utf-8", "replace").strip()
        if status == 403:
            raise TorrentAuthError("qBittorrent has blocked this address after too many failed sign-ins")
        if status == 401 or text.casefold().startswith("fails"):
            raise TorrentAuthError("qBittorrent rejected this username and password")
        if status >= 400:
            raise TorrentClientError(f"qBittorrent could not sign Flipparr in ({status})")
        cookies = []
        for value in (headers.get_all("Set-Cookie") if headers is not None and hasattr(headers, "get_all") else None) or []:
            pair = str(value).split(";", 1)[0].strip()
            if "=" in pair:
                cookies.append(pair)
        if not cookies:
            raise TorrentAuthError("qBittorrent signed Flipparr in without a session to use")
        self._cookie = "; ".join(cookies)

    @staticmethod
    def _text(body: bytes) -> str:
        return body.decode("utf-8", "replace").strip()

    def _json(self, status: int, body: bytes, what: str) -> Any:
        if status >= 400:
            raise TorrentClientError(f"qBittorrent could not {what} ({status}): {self._text(body)[:160]}")
        try:
            return json.loads(body.decode("utf-8", "replace") or "null")
        except ValueError as exc:
            raise TorrentClientError(f"qBittorrent answered {what} with something unreadable") from exc

    def _post_either(self, new: str, old: str, form: dict[str, Any], what: str) -> None:
        """The 5.x endpoint, or the 4.x one where it answers 404."""
        status, body = self._call("POST", new, form=form)
        if status == 404:
            status, body = self._call("POST", old, form=form)
        if status >= 400:
            raise TorrentClientError(f"qBittorrent could not {what} ({status}): {self._text(body)[:120]}")

    # ---- about the client -------------------------------------------------

    def version(self) -> str:
        status, body = self._call("GET", "/api/v2/app/version")
        if status >= 400:
            raise TorrentClientError(f"qBittorrent did not say its version ({status})")
        return self._text(body) or "unknown"

    def webapi_version(self) -> tuple[int, ...]:
        if self._webapi is None:
            status, body = self._call("GET", "/api/v2/app/webapiVersion")
            self._webapi = parse_version(self._text(body)) if status < 400 else (0,)
        return self._webapi

    def supports_selection(self) -> bool:
        """Whether a torrent can be added to wait for its file list, so only
        the wanted files of a pack are downloaded (qBittorrent 4.5.5+)."""
        return self.webapi_version() >= SELECTION_WEBAPI

    def default_save_path(self) -> str:
        status, body = self._call("GET", "/api/v2/app/defaultSavePath")
        return self._text(body) if status < 400 else ""

    def categories(self) -> dict[str, str]:
        """Each category and the folder it saves into ("" when it follows the default)."""
        status, body = self._call("GET", "/api/v2/torrents/categories")
        found = self._json(status, body, "list its categories")
        if not isinstance(found, dict):
            return {}
        return {
            str(name): str((entry or {}).get("savePath") or (entry or {}).get("save_path") or "")
            for name, entry in found.items() if isinstance(entry, dict) or entry is None
        }

    def ensure_category(self, name: str, save_path: str = "") -> str:
        """The folder a category saves into, creating the category if it is
        not there. An existing category keeps the path it has -- changing it
        would move torrents the person put there."""
        existing = self.categories()
        if name in existing:
            return existing[name]
        status, body = self._call("POST", "/api/v2/torrents/createCategory",
                                  form={"category": name, "savePath": save_path})
        if status >= 400:
            raise TorrentClientError(f"qBittorrent could not create the “{name}” category ({status}): {self._text(body)[:120]}")
        return save_path

    # ---- torrents ---------------------------------------------------------

    def add_torrent(
        self, *, torrent: bytes | None = None, magnet: str | None = None,
        category: str = "", tags: list[str] | tuple[str, ...] = (), name: str = "release",
        wait_for_files: bool = False,
    ) -> list[str]:
        """Hand the client a torrent. With `wait_for_files` it stops as soon as
        its file list is known, so the wanted files can be chosen before any
        data is fetched; otherwise it starts at once. Always in a folder of
        its own (`Subfolder`): a rootless torrent would otherwise land in the
        shared category folder among everyone else's files.

        Returns the ids the client says it added (WebAPI 2.14 and later), or
        an empty list when it only says Ok."""
        if not torrent and not magnet:
            raise ValueError("A torrent file or a magnet link is needed")
        boundary = f"----flipparr{secrets.token_hex(12)}"
        parts: list[bytes] = []

        def field(key: str, value: str) -> None:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            )

        if magnet:
            field("urls", magnet)
        if category:
            field("category", category)
        if tags:
            field("tags", ",".join(tags))
        field("contentLayout", "Subfolder")
        if wait_for_files:
            field("stopCondition", "MetadataReceived")
        # A magnet added stopped never fetches its file list, so it runs until
        # the stop condition stops it. A .torrent already has its file list;
        # 4.6.7+ turns the condition into "stopped", 4.5.5 does not, so it is
        # also added stopped. 5.x reads `stopped`, 4.x `paused`.
        stop = "true" if (wait_for_files and torrent) else "false"
        field("stopped", stop)
        field("paused", stop)
        if torrent:
            filename = "".join(ch if ch.isalnum() or ch in " ._-" else "_" for ch in name)[:120] or "release"
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="{filename}.torrent"\r\n'
                "Content-Type: application/x-bittorrent\r\n\r\n".encode() + bytes(torrent) + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        status, body = self._call(
            "POST", "/api/v2/torrents/add",
            multipart=(b"".join(parts), f"multipart/form-data; boundary={boundary}"),
        )
        text = self._text(body)
        if status == 415:
            raise TorrentClientError("qBittorrent says this is not a valid torrent")
        if status == 409:
            raise TorrentClientError("qBittorrent did not add the torrent; it may already have it")
        if status >= 400 or text.casefold().startswith("fails"):
            raise TorrentClientError(f"qBittorrent did not accept the torrent ({status}): {text[:160]}")
        if text.startswith("{"):
            try:
                answer = json.loads(text)
            except ValueError:
                return []
            if int(answer.get("failure_count") or 0) and not int(answer.get("success_count") or 0) \
                    and not int(answer.get("pending_count") or 0):
                raise TorrentClientError("qBittorrent did not accept the torrent")
            return [str(value).casefold() for value in (answer.get("added_torrent_ids") or [])]
        return []

    def info(self, *, hashes: list[str] | tuple[str, ...] | None = None, tag: str | None = None,
             category: str | None = None, filter: str | None = None) -> list[dict[str, Any]]:  # noqa: A002 -- the API's word
        query: dict[str, Any] = {}
        if hashes is not None:
            if not hashes:
                return []
            query["hashes"] = "|".join(hashes)
        if tag is not None:
            query["tag"] = tag
        if category is not None:
            query["category"] = category
        if filter is not None:
            query["filter"] = filter
        status, body = self._call("GET", "/api/v2/torrents/info", query=query)
        found = self._json(status, body, "list its torrents")
        return [entry for entry in found if isinstance(entry, dict)] if isinstance(found, list) else []

    def files(self, info_hash_: str) -> list[dict[str, Any]]:
        """A torrent's files (`index`, `name` relative to its save path,
        `size`, `priority`, `progress`); empty until its metadata is known."""
        status, body = self._call("GET", "/api/v2/torrents/files", query={"hash": info_hash_})
        if status == 404:
            return []
        found = self._json(status, body, "list the torrent's files")
        return [entry for entry in found if isinstance(entry, dict)] if isinstance(found, list) else []

    def set_file_priority(self, info_hash_: str, indexes: list[int] | tuple[int, ...], priority: int) -> None:
        """0 is "do not download", 1 normal."""
        if not indexes:
            return
        status, body = self._call("POST", "/api/v2/torrents/filePrio", form={
            "hash": info_hash_, "id": "|".join(str(int(index)) for index in indexes), "priority": str(int(priority)),
        })
        if status >= 400:
            raise TorrentClientError(f"qBittorrent could not choose the torrent's files ({status}): {self._text(body)[:120]}")

    def add_tags(self, hashes: list[str] | tuple[str, ...], tags: list[str] | tuple[str, ...]) -> None:
        if not hashes or not tags:
            return
        status, body = self._call("POST", "/api/v2/torrents/addTags",
                                  form={"hashes": "|".join(hashes), "tags": ",".join(tags)})
        if status >= 400:
            raise TorrentClientError(f"qBittorrent could not tag the torrent ({status}): {self._text(body)[:120]}")

    def start(self, hashes: list[str] | tuple[str, ...]) -> None:
        if hashes:
            self._post_either("/api/v2/torrents/start", "/api/v2/torrents/resume",
                              {"hashes": "|".join(hashes)}, "start the torrent")

    def pause(self, hashes: list[str] | tuple[str, ...]) -> None:
        """Stop a torrent without removing it."""
        if hashes:
            self._post_either("/api/v2/torrents/stop", "/api/v2/torrents/pause",
                              {"hashes": "|".join(hashes)}, "stop the torrent")

    def delete(self, hashes: list[str] | tuple[str, ...], *, delete_files: bool) -> None:
        if not hashes:
            return
        status, body = self._call("POST", "/api/v2/torrents/delete", form={
            "hashes": "|".join(hashes), "deleteFiles": "true" if delete_files else "false",
        })
        if status >= 400:
            raise TorrentClientError(f"qBittorrent could not remove the torrent ({status}): {self._text(body)[:120]}")
