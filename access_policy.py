"""Who may call what: the access class of every route, admin unless listed.

Reader profiles (schema 49) put an authorization boundary in front of every
route. It is enforced in one place -- the handler's dispatcher asks
`route_access` before any route runs -- rather than in each handler, so a
route nobody thought about is closed, not open: anything not matched below is
**admin**. The table is the reviewed list of what a reader can reach, and
`test_http_contract`'s route census fails when a route appears in app.py
without a class here, or when the reader list grows without the test being
changed with it.

Classes, from most to least open:

- ``public``: no sign-in -- the health check, the sign-in routes, the app
  shell and its assets.
- ``household``: a shared device (sign-in off, a device the admin signed in
  on, or the local network when that is allowed) -- the profile picker.
- ``signed_in``: any profile, about itself -- its settings, signing out.
- ``reader``: any profile -- reading the library.
- ``admin``: the admin only. The default.

Pure: no I/O, importable without the app, like `page_panels`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PUBLIC = "public"
HOUSEHOLD = "household"
SIGNED_IN = "signed_in"
READER = "reader"
ADMIN = "admin"
ACCESS_CLASSES = (PUBLIC, HOUSEHOLD, SIGNED_IN, READER, ADMIN)


@dataclass(frozen=True)
class Viewer:
    """The profile a request speaks for."""

    id: int
    name: str
    role: str
    colour: str | None = None
    can_request: bool = False
    auto_approve: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, object]:
        return {
            "id": self.id, "name": self.name, "role": self.role, "colour": self.colour,
            "canRequest": self.can_request, "autoApprove": self.auto_approve,
        }


# (methods, pattern, class). Patterns are the same strings the handlers match,
# anchored as `re.fullmatch` anchors them.
ROUTE_ACCESS: tuple[tuple[frozenset[str], str, str], ...] = (
    # Sign-in itself.
    (frozenset({"GET"}), r"/healthz", PUBLIC),
    (frozenset({"GET"}), r"/api/v1/auth/status", PUBLIC),
    (frozenset({"POST"}), r"/api/v1/auth/login", PUBLIC),
    # Signing out clears cookies; it needs no proof of who is leaving.
    (frozenset({"POST"}), r"/api/v1/auth/logout", PUBLIC),
    # The picker on a shared device.
    (frozenset({"GET"}), r"/api/v1/profiles", HOUSEHOLD),
    (frozenset({"POST"}), r"/api/v1/profiles/switch", HOUSEHOLD),
    # A profile about itself.
    (frozenset({"GET", "PATCH"}), r"/api/v1/me", SIGNED_IN),
    (frozenset({"PATCH"}), r"/api/v1/me/prefs", SIGNED_IN),
    # Reading the library. Covers are served by path, but only a path inside a
    # library folder (`_cover_source_path`), which a page read already exposes.
    (frozenset({"GET"}), r"/api/v1/catalog", READER),
    (frozenset({"GET"}), r"/api/v1/art-swatch", READER),
    (frozenset({"GET"}), r"/api/file-cover", READER),
    (frozenset({"GET"}), r"/api/v1/files/(\d+)/cover/image", READER),
    (frozenset({"GET"}), r"/api/v1/series/(\d+)/cover/image", READER),
    (frozenset({"GET"}), r"/api/v1/series/(\d+)/backdrop", READER),
    (frozenset({"GET"}), r"/api/v1/series/(\d+)/synopsis", READER),
    (frozenset({"GET"}), r"/api/v1/issues/(\d+)/detail", READER),
    (frozenset({"GET"}), r"/api/v1/reading", READER),
    (frozenset({"GET"}), r"/api/v1/reading/runs", READER),
    (frozenset({"GET"}), r"/api/v1/series/(\d+)/reading", READER),
    (frozenset({"GET", "POST"}), r"/api/v1/files/(\d+)/progress", READER),
    (frozenset({"GET"}), r"/api/v1/files/(\d+)/pages", READER),
    (frozenset({"GET"}), r"/api/v1/files/(\d+)/pages/(\d+)", READER),
    # Reading a page's panels. A reader never spends the vision connector on
    # it unless the admin allows that (`visionForReaders`); correcting panels
    # (PATCH, DELETE) stays the admin's.
    (frozenset({"GET"}), r"/api/v1/files/(\d+)/pages/(\d+)/panels", READER),
    (frozenset({"POST"}), r"/api/v1/(issues|series)/(\d+)/rating", READER),
)

_COMPILED = tuple((methods, re.compile(pattern), access) for methods, pattern, access in ROUTE_ACCESS)


def route_access(method: str, path: str) -> str:
    """The access class for a request. Anything outside `/api/` that is not the
    legacy results page is the app shell or an asset, and public; anything
    under `/api/` not listed is the admin's."""
    method = "GET" if method == "HEAD" else method
    for methods, pattern, access in _COMPILED:
        if method in methods and pattern.fullmatch(path):
            return access
    if not path.startswith("/api/") and path != "/results":
        return PUBLIC
    return ADMIN


def allows(access: str, *, viewer: Viewer | None, household: bool) -> bool:
    """Whether a request with this viewer (or none) on this kind of device may pass."""
    if access == PUBLIC:
        return True
    if access == HOUSEHOLD:
        return household
    if viewer is None:
        return False
    if access in (SIGNED_IN, READER):
        return True
    return viewer.is_admin
