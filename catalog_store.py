"""Persistent catalog storage and incremental library scans for Flipparr."""

from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import datetime as dt
import difflib
import hashlib
import json

import content_rating
import math
import re
import sqlite3
import threading
import urllib.parse
import unicodedata
from comic_language import detect_language, language_name, normalize_language
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


SCHEMA_VERSION = 64


class CatalogNewerThanBuild(RuntimeError):
    """The database was written by a newer Flipparr than this one. Opening
    it would mean changing it; the way back is the previous build's own
    backup (docs/OPERATING.md, "Upgrading and rolling back")."""

    def __init__(self, stored: int) -> None:
        super().__init__(
            f"This library's catalog is at schema {stored}, newer than this build's {SCHEMA_VERSION}: "
            f"it was last opened by a newer Flipparr. Run that version, or restore the backup taken before it.")
        self.stored = stored


def journal_mode_for(version_info: Sequence[int]) -> str:
    """WAL where this SQLite is safe in it, the rollback journal elsewhere.

    SQLite 3.7.0 through 3.51.2 can corrupt a database in WAL mode when two
    connections write or checkpoint at the same instant, which several
    threads here can do (https://sqlite.org/wal.html#walresetbug). The fix is
    in 3.51.3, backported to 3.44.6 and 3.50.7. The image builds a fixed
    SQLite (Dockerfile); anything older -- Debian 12's 3.40.1, an old Python
    run from source -- gets the rollback journal: readers then wait for a
    write instead of reading beside it, which is slower and cannot corrupt.
    """
    version = tuple(int(part) for part in version_info[:3])
    fixed = version >= (3, 51, 3) or (3, 50, 7) <= version < (3, 51, 0) or (3, 44, 6) <= version < (3, 45, 0)
    return "WAL" if fixed else "DELETE"
# Who found a page's panels: the gutter finder, a local model, a vision model
# over the wire, a person, or a file that carried them.
PANEL_SOURCES = {"auto", "model", "vlm", "manual", "acbf"}

# Reader profiles (schema 49). One library, several people reading it, the way
# a Plex Home shares one server: the admin, who has always existed and is the
# only profile a single-person install ever sees, and readers the admin adds.
# What a person reads, where they left it and what they thought of it is
# theirs; everything else -- the files, the catalog, what is downloaded -- is
# the library's.
ADMIN_USER_ID = 1
USER_ROLES = ("admin", "reader")
# What a profile asks for when someone switches to it on a shared device.
SWITCH_LOCKS = ("open", "pin", "password")
# What a reader can ask the admin for: a run or collection in the library to
# be followed, a run found in Discover to be added and followed, or chosen
# issues of a Discover run. Each becomes, on approval, exactly the call the
# admin's own button makes.
MEMBER_REQUEST_KINDS = ("run", "collection", "discover_run", "discover_issues", "discover_arc")
MEMBER_REQUEST_STATES = ("pending", "approved", "declined", "cancelled", "failed")
# A profile's colour on the picker and in the nav: the design system's own
# accent names, never a free value, so a colour is always one the UI can draw.
PROFILE_COLOURS = ("violet", "blue", "teal", "green", "amber", "orange", "red", "pink")
PROFILE_NAME_MAX = 40
USER_PREFS_MAX_BYTES = 8192

# The tables whose rows belong to a reader, in their per-reader shape. They are
# created from here rather than in the migration's script so that a library
# made before profiles is rebuilt into this shape (`_key_personal_tables_by_reader`)
# and a new one simply starts in it; both end with the same columns.
PERSONAL_TABLES: tuple[tuple[str, str, str], ...] = (
    (
        "reading_progress",
        # Where you are in a comic (schema 40), per reader since 49. Keyed by
        # file, because a file is the thing you read; an issue may have
        # several. The file's signature is kept with the page: this app
        # replaces files, and page 40 of a 24-page replacement is a blank
        # screen, so a page from a file that has changed is not offered back.
        """CREATE TABLE IF NOT EXISTS {name} (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            page INTEGER NOT NULL,
            panel INTEGER NOT NULL DEFAULT 0,
            page_count INTEGER NOT NULL,
            file_signature TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, file_id)
        )""",
        "file_id, page, panel, page_count, file_signature, started_at, finished_at, updated_at",
    ),
    (
        "issue_ratings",
        # A reader's own rating, and nobody else's. No provider Flipparr talks
        # to carries a quality score -- Comic Vine has none, Metron's is an age
        # rating, GCD is bibliographic -- so this is the only rating there is,
        # and it never arrives from a scan or a sync. Whole stars, one to five.
        """CREATE TABLE IF NOT EXISTS {name} (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, issue_id)
        )""",
        "issue_id, rating, updated_at",
    ),
    (
        "series_run_ratings",
        """CREATE TABLE IF NOT EXISTS {name} (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, series_run_id)
        )""",
        "series_run_id, rating, updated_at",
    ),
)


def _panels_cover(panels_json: str) -> float:
    """How much of a page its stored panels account for, from their normalised sizes."""
    try:
        panels = json.loads(panels_json or "[]")
    except ValueError:
        return 0.0
    return sum(
        float(panel.get("w") or 0) * float(panel.get("h") or 0)
        for panel in panels if isinstance(panel, dict)
    )
# Refusals that prove nothing about the release -- a file this machine could
# not read or identify, a download SABnzbd lost -- set it aside for a day
# rather than barring it. See app.DownloadContentMismatch.
SOFT_REFUSAL_KINDS = ("unidentified", "unreadable", "lost")
SOFT_REFUSAL_HOURS = 24
# A scene release name marking its issue "No.19"; see _forget_misread_scene_releases.
_SCENE_ISSUE_NAME = re.compile(r"(?:^|[.\s_])No[.\s_]?\d{1,4}(?=[.\s_]|$)", re.I)
# What a run is. Manga comes in volumes that *are* the unit, where a western
# comic's volume is a collected edition of issues -- every step from search to
# import has to know which rule applies.
SERIES_FORMATS = ("comic", "manga")
# Which way the pages turn. Null on a run means "follow the medium".
READING_DIRECTIONS = frozenset({"ltr", "rtl"})
# Bumped when local analysis starts producing something it did not before, so
# the next scan re-reads files it would otherwise reuse on their fingerprint.
# 3: comics that are not zip archives yield covers and embedded metadata.
LOCAL_ANALYSIS_VERSION = 3
MONITORED_RUN_REFRESH_HOURS = 24
# Credits that are kept but not searched. ComicInfo names every variant
# cover's artist, so "by" a cover artist would claim runs they drew one cover
# for; a translator or editor is not who a reader is looking for.
_UNSEARCHED_ROLES = frozenset({"cover_artist", "translator", "editor"})
_ROLE_ORDER = {"writer": 0, "artist": 1, "penciller": 1, "inker": 2, "colorist": 3, "letterer": 4}
_EPUB_ROLES = {"aut": "writer", "ill": "artist", "art": "artist", "trl": "translator", "edt": "editor"}


def normalized_person(value: Any) -> str:
    """A name as a search compares it: accents, case and punctuation folded.

    So "Rodríguez" finds "Rodriguez" and "Brian K. Vaughan" finds "brian k vaughan".
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    return " ".join(re.sub(r"[^\w\s]|_", " ", text).split())


def _file_credits(result: dict[str, Any]) -> list[tuple[str, str]]:
    """The (name, role) credits a scanned file carries in its own metadata."""
    embedded = (result or {}).get("embedded_metadata") or {}
    credits: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: Any, role: str) -> None:
        cleaned = re.sub(r"\s+", " ", str(name or "")).strip()
        key = (normalized_person(cleaned), role)
        if key[0] and key not in seen:
            seen.add(key)
            credits.append((cleaned, role))

    for role, value in (embedded.get("contributors") or {}).items():
        # ComicInfo puts several people in one field, comma separated.
        for name in value if isinstance(value, list) else str(value or "").split(","):
            add(name, str(role))
    for creator in embedded.get("creator_details") or []:
        name = str((creator or {}).get("name") or "")
        # EPUB often files a name surname first: "Fujimoto, Tatsuki".
        parts = [part.strip() for part in name.split(",")]
        if len(parts) == 2 and all(parts):
            name = f"{parts[1]} {parts[0]}"
        add(name, _EPUB_ROLES.get(str((creator or {}).get("role") or "").casefold(), "writer"))
    return credits


def _trusted_recommendation(
    result: dict[str, Any] | None, override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The lookup's match for a file, unless the file's own metadata names another series.

    A file whose ComicInfo names its series is not overruled by a lookup that
    matched it to a different one. "Supergirl Woman of Tomorrow #02.cbz", its
    ComicInfo saying Supergirl: Woman of Tomorrow #2 (2021), was matched by
    lookup to DC's 1972 "Supergirl": filed as a run of its own, beside the
    right run in the same folder, and given that run's issue ids. A lookup that
    agrees with the file is kept, for the provider ids it carries.

    What the file is known to be is its confirmed identity when it has one --
    #5 of the same run, imported by Flipparr and confirmed, was matched to
    1973's "Supergirl" #5 all the same -- and otherwise what its ComicInfo says.
    """
    result = result or {}
    override = override or {}
    recommendation = result.get("recommendation") or {}
    series = str(
        override.get("seriesTitle") or override.get("title")
        or (result.get("embedded_metadata") or {}).get("series") or ""
    ).strip()
    if not recommendation or not series:
        return recommendation
    matched = _normalized(_canonical_series_title(str(recommendation.get("title") or ""), True))
    stated = _normalized(_canonical_series_title(series, True))
    if not matched or not stated:
        return recommendation
    # "The Department of Truth" against "Department of Truth" still agrees.
    return recommendation if difflib.SequenceMatcher(None, stated, matched).ratio() >= 0.8 else {}


def _summarize_run_creators(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """[{name, roles}] for one run: writers first, then by how often credited."""
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        role = str(row["role"] or "")
        if role in _UNSEARCHED_ROLES:
            continue
        credits = int(row["credits"] or 1)
        person = people.setdefault(
            normalized_person(row["name"]), {"name": row["name"], "roles": set(), "credits": 0}
        )
        person["roles"].add(role)
        person["credits"] += credits
    ordered = sorted(people.values(), key=lambda person: (
        min(_ROLE_ORDER.get(role, 5) for role in person["roles"]),
        -person["credits"], person["name"].casefold(),
    ))
    return [
        {"name": person["name"], "roles": sorted(person["roles"], key=lambda role: (_ROLE_ORDER.get(role, 5), role))}
        for person in ordered[:12]
    ]


class _ClosingConnection(sqlite3.Connection):
    """A connection kept by its thread: `with` ends the transaction (commit,
    or roll back on an exception) and leaves the connection open for the
    thread's next operation. Nested `with` blocks on the same thread share
    one transaction, ended by the outermost.

    It used to close on exit, one connection per operation. On a NAS volume
    (btrfs over bcache, Synology) a fresh connection paid about 17 ms to
    commit and 16 ms to close, where one kept open commits in 0.2 ms, so a
    scan of 290 comics -- six operations a file -- took 55 s instead of 8
    (Gate 4, 2026-10-05). A thread's connection goes when the thread does.
    """

    _depth = 0
    _kept = False

    def __enter__(self):
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._depth -= 1
        if self._depth > 0:
            return False
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            if not self._kept:
                self.close()


def _issue_rating_summary(issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What a run's rated issues average to, and how many said so.

    Kept apart from the run's own rating: a run nobody has rated, whose issues
    average four, has not been given four stars by anyone, and a card that
    said so would be inventing an opinion.
    """
    given = [issue["yourRating"] for issue in issues if issue.get("yourRating")]
    if not given:
        return None
    return {"average": round(sum(given) / len(given), 2), "count": len(given)}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class CollectionNameTaken(ValueError):
    """Another collection already has that name."""


def _normalized(value: Any) -> str:
    # Comic titles routinely use ``+`` and ``&`` interchangeably (for example
    # ``Alex + Ada``). Treat both as the word "and" so a display-title cleanup
    # does not split one local run into two canonical identities.
    normalized = str(value or "").casefold().replace("&", " and ").replace("+", " and ")
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _normalized_publisher(value: Any) -> str:
    words = re.findall(r"[a-z0-9]+", str(value or "").casefold().replace("&", " and "))
    corporate_suffixes = {"comic", "comics", "inc", "incorporated", "llc", "ltd", "publishing", "publisher", "press"}
    meaningful = [word for word in words if word not in corporate_suffixes]
    return "".join(meaningful or words)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return cleaned or hashlib.sha1(value.encode()).hexdigest()[:12]


def _run_id(value: Any) -> int | None:
    """The series run's row id, or None when the group has no run yet.

    A group of files that has not been matched to a series run is keyed by a
    slug of its title rather than a row id -- the state every file is in while a
    scan is still running. Families are keyed by run id, so such a group simply
    has no membership; calling int() on the slug took the whole catalog
    response down with a 500 instead.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _canonical_series_title(title: Any, is_issue: bool) -> str:
    """Return a conservative series hint while retaining the raw title as an alias."""
    value = re.sub(r"\s+", " ", str(title or "")).strip(" -_:;")
    if not is_issue and ":" in value:
        prefix, _ = value.split(":", 1)
        if len(prefix.strip()) >= 3:
            value = prefix.strip()
    if not is_issue:
        value = re.sub(r"\s+(?:vol(?:ume)?\.?|book)\s*#?\s*\d+.*$", "", value, flags=re.I).strip()
    return value or str(title or "Unknown series")


def _display_title_rank(value: str) -> tuple[int, int, int]:
    """Prefer readable publisher styling over compact filename-derived text."""
    return (
        1 if any(separator in value for separator in ("&", "+")) else 0,
        1 if re.search(r"\s", value) else 0,
        -len(value),
    )


def _suggest_arc_name(collection_name: str, run_title: str) -> str:
    """Derive a reviewable arc label without treating it as authoritative metadata."""
    value = re.sub(r"\s+", " ", str(run_title or "")).strip()
    if ":" in value:
        candidate = value.rsplit(":", 1)[-1].strip()
        if candidate:
            return candidate
    if value.casefold().startswith(str(collection_name or "").casefold()):
        value = value[len(collection_name):].strip(" -_:;")
    value = re.sub(r"^(?:vol(?:ume)?\.?|book)\s*#?\s*\d+\s*[-_:;]?\s*", "", value, flags=re.I)
    return value or str(run_title or "Untitled arc")


def _natural_issue_key(value: Any) -> tuple[Any, ...]:
    parts = re.split(r"(\d+(?:\.\d+)?)", str(value or ""))
    return tuple(
        (0, float(part)) if re.fullmatch(r"\d+(?:\.\d+)?", part) else (1, part.casefold())
        for part in parts if part
    )


def _valid_year(value: Any) -> int | None:
    """Return a plausible publication year without accepting booleans or noise."""
    if isinstance(value, bool):
        return None
    try:
        year = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2200 else None


def _years_compatible(first: Any, second: Any, tolerance: int = 3) -> bool:
    """Treat nearby dates as one run while separating reboots with the same title."""
    first_year = _valid_year(first)
    second_year = _valid_year(second)
    return first_year is None or second_year is None or abs(first_year - second_year) <= tolerance


def _issue_number_value(value: Any) -> float | None:
    match = re.match(r"^\s*#?\s*(\d+(?:\.\d+)?)", str(value or ""))
    return float(match.group(1)) if match else None


def _named_run_year(parsed: dict[str, Any], identity: dict[str, Any]) -> int | None:
    """The run year the file's name or folder states (parse_filename's
    `run_year`): the owner's own grouping, exact to the year."""
    for value in (parsed.get("run_year"), identity.get("run_year")):
        year = _valid_year(value)
        if year is not None:
            return year
    return None


def _stated_run_year(parsed: dict[str, Any], identity: dict[str, Any], embedded: dict[str, Any]) -> int | None:
    """The run year a file states: its name or folder, else ComicInfo's
    Volume when that is a year -- the field ComicRack and Mylar fill with
    the series' start year. Other taggers write the issue's own year there
    (DIE: Loaded #9, a 2025 series, says 2026), so only the name's year is
    exact; see _resolve_series_run."""
    named = _named_run_year(parsed, identity)
    if named is not None:
        return named
    volume = str(embedded.get("volume") or "").strip()
    return _valid_year(volume) if re.fullmatch(r"(?:19|20)\d{2}", volume) else None


def _claim_run_anchor_year(claim: dict[str, Any]) -> int | None:
    """Return a year that can identify a publication run's starting era.

    A year the file states as its run's -- "Captain America (2011) #010", a
    "Batman (2016)" folder, ComicInfo's Volume -- does, for any issue or
    volume (`run_year`, 2026-10-05). Otherwise: a later issue's publication
    year is useful for choosing between already known runs, but it is not
    the run's start year. Collected-edition years are even less useful
    because trades are routinely published years after their underlying
    series began. Only an opening numbered issue may create or move a run's
    era boundary.
    """
    stated = _valid_year(claim.get("run_year"))
    if stated is not None:
        return stated
    if claim.get("kind") != "issue":
        return None
    issue_number = _issue_number_value(claim.get("issue"))
    if issue_number is None or not 1 <= issue_number <= 3:
        return None
    return _valid_year(claim.get("year"))


def _issue_list_start_year(entries: list[dict[str, Any]]) -> int | None:
    """Infer a provider run's era from its opening numbered issues."""
    opening_years = []
    all_years = []
    for entry in entries:
        year = _valid_year(entry.get("publication_year") or entry.get("publication_date"))
        if year is None:
            continue
        all_years.append(year)
        issue_number = _issue_number_value(entry.get("number"))
        if issue_number is not None and 1 <= issue_number <= 3:
            opening_years.append(year)
    return min(opening_years or all_years) if (opening_years or all_years) else None


def _coverage_groups(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, bool, str], list[dict[str, Any]]] = {}
    for claim in claims:
        key = (
            claim["seriesLabel"], claim["source"], claim["confidence"], claim["resolved"],
            str(claim.get("relationKind") or "full_issue"),
        )
        grouped.setdefault(key, []).append(claim)
    summaries = []
    for (series_label, source, confidence, resolved, relation_kind), members in grouped.items():
        numbers = sorted({member["issueNumber"] for member in members}, key=_natural_issue_key)
        issue_label = numbers[0] if len(numbers) == 1 else f"{numbers[0]}–{numbers[-1]}"
        summaries.append(
            {
                "seriesLabel": series_label, "issues": numbers, "issueLabel": issue_label,
                "source": source, "confidence": confidence, "resolved": resolved,
                "relationKind": relation_kind,
                "evidence": next((member["evidence"] for member in members if member.get("evidence")), None),
            }
        )
    return summaries


def _coverage_relation_kind(claim: dict[str, Any]) -> str:
    """Normalize provider coverage semantics before they can affect ownership."""
    value = str(
        claim.get("relation_kind") or claim.get("relationKind") or "full_issue"
    ).strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "full": "full_issue", "issue": "full_issue", "reprint": "full_issue",
        "partial": "partial_story", "story": "partial_story",
        "extract": "excerpt",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in {"full_issue", "partial_story", "excerpt", "unknown"} else "unknown"


def _logical_volume_key(edition: dict[str, Any]) -> str:
    """Return the collected-work slot shared by equivalent edition records.

    ``editions`` intentionally retains ISBNs, formats, providers, and individual
    files. Those are publication/copy facts, not the user's logical volume
    count. This helper is applied within one canonical publication run, so a
    numbered volume and kind are enough to share a slot even when providers use
    different display titles. Unnumbered works stay conservative and use ISBN
    or title.
    """
    title = _normalized(edition.get("title")) or "untitled"
    kind = str(edition.get("editionKind") or "edition")
    volume = edition.get("volume")
    if volume not in {None, ""}:
        return f"numbered:{volume}:{kind}"
    isbns = sorted(str(value) for value in (edition.get("isbns") or []) if value)
    if isbns:
        return f"isbn:{isbns[0]}"
    subtitle = _normalized(edition.get("subtitle"))
    return f"title:{title}:{subtitle}:{kind}"


def _group_logical_volumes(editions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse publication editions/copies into user-facing collected works."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edition in editions:
        grouped.setdefault(_logical_volume_key(edition), []).append(edition)

    logical_volumes: list[dict[str, Any]] = []
    for logical_key, members in grouped.items():
        def quality(item: dict[str, Any]) -> tuple[int, ...]:
            return (
                int(any(group.get("resolved") for group in item.get("coverageGroups") or [])),
                int(str(item.get("source") or "").casefold() not in {"", "local metadata"}),
                int(bool(item.get("cover"))),
                int(bool(item.get("isbns"))),
                int(bool(item.get("subtitle"))),
                int(bool(item.get("publisher"))),
                int(bool(item.get("publicationYear"))),
            )

        representative = max(members, key=quality)
        claims: list[dict[str, Any]] = []
        seen_claims: set[tuple[Any, ...]] = set()
        for member in members:
            for claim in member.get("coverage") or []:
                claim_key = (
                    claim.get("seriesLabel"), claim.get("issueNumber"),
                    bool(claim.get("resolved")), claim.get("source"),
                )
                if claim_key not in seen_claims:
                    claims.append(claim)
                    seen_claims.add(claim_key)

        merged = dict(representative)
        resolved_issue_keys = {
            (str(claim.get("seriesLabel") or "").casefold(), str(claim.get("issueNumber") or ""))
            for claim in claims
            if claim.get("resolved")
            and claim.get("relationKind", "full_issue") == "full_issue"
            and claim.get("issueNumber") not in {None, ""}
        }
        has_unresolved_claims = any(not claim.get("resolved") for claim in claims)
        has_partial_claims = any(
            claim.get("relationKind", "full_issue") != "full_issue"
            for claim in claims
        )
        if resolved_issue_keys and (has_unresolved_claims or has_partial_claims):
            contents_status = "partial"
        elif resolved_issue_keys:
            contents_status = "verified"
        elif has_partial_claims:
            contents_status = "partial"
        elif claims:
            contents_status = "unresolved"
        else:
            contents_status = "unknown"
        merged.update(
            {
                "logicalVolumeKey": logical_key,
                "editionIds": [member["id"] for member in members],
                "editionRecordCount": len(members),
                "copyCount": len({file_id for member in members for file_id in member.get("fileIds") or []}),
                "fileIds": list(dict.fromkeys(
                    file_id for member in members for file_id in member.get("fileIds") or []
                )),
                "files": list(dict.fromkeys(
                    filename for member in members for filename in member.get("files") or []
                )),
                "isbns": list(dict.fromkeys(
                    isbn for member in members for isbn in member.get("isbns") or []
                )),
                "cover": next((
                    member.get("cover")
                    for member in sorted(members, key=quality, reverse=True)
                    if member.get("cover")
                ), None),
                "coverage": claims,
                "coverageGroups": _coverage_groups(claims),
                "contentsStatus": contents_status,
                "contentsIssueCount": len(resolved_issue_keys),
                "seriesPlacementStatus": "matched",
                "coverageOverrideCount": sum(
                    int(member.get("coverageOverrideCount") or 0) for member in members
                ),
            }
        )
        logical_volumes.append(merged)

    logical_volumes.sort(
        key=lambda item: (
            item.get("volume") is None,
            _natural_issue_key(
                item.get("volume") if item.get("volume") is not None else item.get("title")
            ),
        )
    )
    return logical_volumes


def _parse_timestamp(value: Any) -> dt.datetime | None:
    """An aware datetime from a stored timestamp, or None if it will not read."""
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _display_time(timestamp: str | None) -> tuple[str, str]:
    if not timestamp:
        return "Not scanned", ""
    try:
        parsed = dt.datetime.fromisoformat(timestamp)
        local = parsed.astimezone()
        return local.strftime("%b %-d, %Y"), local.strftime("%-I:%M %p")
    except (ValueError, OSError):
        return timestamp, ""


def _issue_release_state(
    publication_date: str | None, publication_year: Any, today: dt.date | None = None
) -> str:
    """Classify release availability without calling an unknown date "missing."""
    today = today or dt.datetime.now().astimezone().date()
    if publication_date:
        try:
            released_on = dt.date.fromisoformat(str(publication_date)[:10])
            return "upcoming" if released_on > today else "released"
        except ValueError:
            pass
    try:
        year = int(publication_year) if publication_year not in {None, ""} else None
    except (TypeError, ValueError):
        year = None
    if year is None or year == today.year:
        return "unknown"
    return "upcoming" if year > today.year else "released"


def _health_label(code: str) -> str:
    return {
        "empty_file": "Empty file",
        "empty_archive": "Empty archive",
        "corrupt_archive": "Corrupt archive",
        "mislabeled_archive": "Mislabeled archive",
        "no_image_pages": "No image pages",
        "invalid_pdf": "Invalid PDF",
    }.get(code, code.replace("_", " ").title())


def _edition_kind(
    parsed: dict[str, Any], result: dict[str, Any], volume_number: Any, manual_kind: str | None = None
) -> str:
    """Classify a physical/digital edition without conflating it with issue identity."""
    if manual_kind:
        return manual_kind
    recommendation = result.get("recommendation") or {}
    embedded = result.get("embedded_metadata") or {}
    evidence = " ".join(
        str(value or "") for value in (
            parsed.get("filename"), parsed.get("format"), recommendation.get("format"),
            recommendation.get("title"), recommendation.get("subtitle"), embedded.get("format"),
        )
    ).casefold()
    if "omnibus" in evidence:
        return "omnibus"
    if "compendium" in evidence:
        return "compendium"
    if "deluxe" in evidence:
        return "deluxe_edition"
    if "graphic novel" in evidence:
        return "graphic_novel"
    if "hardcover" in evidence or "hard cover" in evidence:
        return "hardcover"
    if volume_number is not None or any(
        marker in evidence for marker in ("trade paperback", "softcover", "collected edition", " tpb")
    ):
        return "collected_volume"
    if "collection" in evidence:
        return "collection"
    return "edition"


def _candidate_issue_provider_identity(
    candidate: dict[str, Any], *, allow_embedded_url: bool = False
) -> dict[str, str | None] | None:
    """Return durable provider identity carried by a selected issue candidate.

    Provider candidates name their source directly. Embedded ComicInfo metadata
    is intentionally weaker, so its URL is accepted only after an explicit Fix
    Match action confirms that the user chose it.
    """
    source = str(candidate.get("source") or "").strip().casefold()
    provider = {
        "grand comics database": "gcd",
        "gcd": "gcd",
        "metron": "metron",
        "comic vine": "comic_vine",
        "comic_vine": "comic_vine",
    }.get(source)
    provider_id = str(candidate.get("source_id") or "").strip() or None
    url = str(candidate.get("url") or candidate.get("web") or "").strip() or None

    if not provider and allow_embedded_url and url:
        patterns = (
            ("gcd", r"(?:comics\.org)/(?:api/)?issue/(\d+)/?"),
            ("metron", r"(?:metron\.cloud)/(?:api/)?issue/(\d+)/?"),
            ("comic_vine", r"(?:comicvine\.gamespot\.com)/issue/4000-(\d+)/?"),
        )
        for candidate_provider, pattern in patterns:
            match = re.search(pattern, url, flags=re.IGNORECASE)
            if match:
                provider = candidate_provider
                provider_id = match.group(1)
                break

    if not provider or not provider_id:
        return None
    series_id_keys = {
        "gcd": ("provider_series_id", "gcd_series_id"),
        "metron": ("provider_series_id", "metron_series_id"),
        "comic_vine": ("provider_series_id", "comicvine_volume_id"),
    }[provider]
    provider_series_id = next(
        (
            str(candidate.get(key)).strip()
            for key in series_id_keys
            if candidate.get(key) not in {None, ""}
        ),
        None,
    )
    return {
        "provider": provider,
        "providerId": provider_id,
        "apiUrl": url,
        "providerSeriesId": provider_series_id,
    }


# What a download a person stopped says. It is not a failure: the issue goes
# back to wanting a release and another is looked for.
DOWNLOAD_STOPPED_ERROR = "Stopped by you"


def _torrent_identity_hash(identity: Any) -> str | None:
    """The info hash in a torrent download's identity, `torrent:<hash>:<job>`."""
    parts = str(identity or "").split(":")
    return parts[1].casefold() if len(parts) >= 3 and parts[0] == "torrent" and parts[1] else None


class CatalogStore:
    """SQLite-backed catalog with one connection per operation/thread."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        # The language the library asked for, set by the caller that knows the
        # application settings. Empty means no preference, which trusts every
        # file. A file that states another language still counts as owned and
        # still appears in listings; it just stops writing its own title, year
        # and cover onto records the whole run shares.
        self.preferred_language = ""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        # Each thread's own connection (see _ClosingConnection), and all of
        # them, for close().
        self._thread = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self._migrate()
        self._reconcile_all_identities()
        self._backfill_legacy_replacement_requests()

    def close(self) -> None:
        """Let go of every thread's connection: for a store being replaced
        (the database moved), or a test's folder being removed."""
        with self._connections_lock:
            connections, self._connections = self._connections, []
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self._thread = threading.local()

    def _connect(self) -> sqlite3.Connection:
        kept = getattr(self._thread, "connection", None)
        if kept is not None:
            try:
                kept.execute("SELECT 1")   # still open, and the file still there
                return kept
            except sqlite3.ProgrammingError:
                self._thread.connection = None
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            factory=_ClosingConnection,
            check_same_thread=False,   # closed by close(), from any thread
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        if getattr(self, "journal_mode", None) is not None:
            # Kept by its thread from here on; the migration's own connection,
            # opened before the journal mode is known, is let go.
            connection._kept = True
            self._thread.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        if getattr(self, "journal_mode", None) == "WAL":
            # In WAL mode, NORMAL syncs the log at each checkpoint rather than
            # at each commit, and SQLite documents it as safe from corruption
            # ("a transaction committed in WAL mode with synchronous=NORMAL
            # might roll back following a power loss or system crash",
            # https://sqlite.org/pragma.html#pragma_synchronous); the catalog
            # is rebuilt by a rescan in any case. FULL cost a sync of the log
            # per commit, several a file: a scan of 525 comics took 110 s
            # with the database on a NAS volume, 8 s with NORMAL (Gate 4,
            # 2026-10-05). The rollback journal keeps FULL: there NORMAL can
            # lose the journal's protection.
            connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextlib.contextmanager
    def _borrowed_write(self, connection: sqlite3.Connection | None):
        """Join the caller's transaction when given one, else own a short one.

        Committing per row costs an fsync each. That is fine for a single edit
        and ruinous for a whole-library pass, so bulk callers pass a connection
        and pay one commit for the batch. The work done is identical either way.
        """
        if connection is not None:
            yield connection
            return
        with self._write_lock, self._connect() as owned:
            yield owned

    @contextlib.contextmanager
    def _borrowed_read(self, connection: sqlite3.Connection | None):
        if connection is not None:
            yield connection
            return
        with self._connect() as owned:
            yield owned

    def stored_schema_version(self) -> int | None:
        """The schema the file is at, read without changing anything: None
        for a new or pre-schema file."""
        if not self.database_path.is_file():
            return None
        try:
            with contextlib.closing(sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)) as connection:
                row = connection.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            return None
        return int(row[0]) if row else None

    # How many pre-upgrade copies stay beside the database. Each is the whole
    # library as it was; three cover the last few upgrades without the
    # folder growing by a copy every release for ever.
    PRE_UPGRADE_COPIES_KEPT = 3

    def _keep_a_copy_before(self, version: int, stored: int | None) -> Path | None:
        """A copy of the library as it was, before this build's schema is
        applied to it: `<db>.pre-v<version>`, beside the database, through
        SQLite's backup API (safe with WAL). The way back if an upgrade is
        ever doubted, since v1 has no backup of its own. Taken once per
        target version; the oldest copies go once there are more than
        PRE_UPGRADE_COPIES_KEPT (Gate 4, 2026-10-05: before, only the move to
        49 kept one).
        """
        if stored is None or stored >= version or not self.database_path.is_file():
            return None
        copy = self.database_path.with_name(f"{self.database_path.name}.pre-v{version}")
        if copy.exists():
            return None
        with self._connect() as connection, contextlib.closing(sqlite3.connect(copy)) as target:
            connection.backup(target)
        copies = sorted(
            (path for path in self.database_path.parent.glob(f"{self.database_path.name}.pre-v*")
             if re.fullmatch(r"\d+", path.name.rsplit("pre-v", 1)[-1])),
            key=lambda path: int(path.name.rsplit("pre-v", 1)[-1]))
        for old in copies[:-self.PRE_UPGRADE_COPIES_KEPT]:
            try:
                old.unlink()
            except OSError:
                pass
        return copy

    def _migrate(self) -> None:
        # First, before a table is touched: a file from a newer build is not
        # this build's to change. Checked only after the tables were created
        # and rows cleaned up, an older image put back what a newer schema had
        # dropped before it refused (Gate 4, 2026-10-05).
        stored = self.stored_schema_version()
        if stored is not None and stored > SCHEMA_VERSION:
            raise CatalogNewerThanBuild(stored)
        self._keep_a_copy_before(SCHEMA_VERSION, stored)
        with self._connect() as connection:
            # Switching mode needs the file to itself, which it has here: the
            # store migrates before anything else opens it.
            self.journal_mode = str(connection.execute(
                f"PRAGMA journal_mode = {journal_mode_for(sqlite3.sqlite_version_info)}").fetchone()[0]).upper()
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_roots (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    recursive INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_scan_at TEXT
                );
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY,
                    root_id INTEGER NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    changed_files INTEGER NOT NULL DEFAULT 0,
                    reused_files INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    root_id INTEGER NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
                    path TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    present INTEGER NOT NULL DEFAULT 1,
                    parsed_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS files_root_present ON files(root_id, present);
                CREATE TABLE IF NOT EXISTS review_resolutions (
                    id INTEGER PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    UNIQUE(file_path, reason_code, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS series_runs (
                    id INTEGER PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    canonical_key TEXT NOT NULL UNIQUE,
                    start_year INTEGER,
                    publisher TEXT,
                    acquisition_preference TEXT NOT NULL DEFAULT 'either',
                    monitoring_status TEXT NOT NULL DEFAULT 'cataloged',
                    format TEXT NOT NULL DEFAULT 'comic',
                    creators_synced_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS series_families (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    acquisition_preference TEXT NOT NULL DEFAULT 'either',
                    include_specials INTEGER NOT NULL DEFAULT 1,
                    monitoring_status TEXT NOT NULL DEFAULT 'monitored',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS series_family_memberships (
                    series_family_id INTEGER NOT NULL REFERENCES series_families(id) ON DELETE CASCADE,
                    series_run_id INTEGER NOT NULL UNIQUE REFERENCES series_runs(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(series_family_id, series_run_id)
                );
                CREATE INDEX IF NOT EXISTS series_family_memberships_family
                    ON series_family_memberships(series_family_id, position, series_run_id);
                CREATE TABLE IF NOT EXISTS story_arcs (
                    id INTEGER PRIMARY KEY,
                    series_family_id INTEGER NOT NULL REFERENCES series_families(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    arc_type TEXT NOT NULL CHECK(arc_type IN ('main', 'specials')),
                    position INTEGER NOT NULL DEFAULT 0,
                    start_year INTEGER,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(series_family_id, normalized_name)
                );
                CREATE INDEX IF NOT EXISTS story_arcs_family
                    ON story_arcs(series_family_id, arc_type, position, id);
                CREATE TABLE IF NOT EXISTS story_arc_run_memberships (
                    story_arc_id INTEGER NOT NULL REFERENCES story_arcs(id) ON DELETE CASCADE,
                    series_run_id INTEGER NOT NULL UNIQUE REFERENCES series_runs(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(story_arc_id, series_run_id)
                );
                CREATE INDEX IF NOT EXISTS story_arc_run_memberships_arc
                    ON story_arc_run_memberships(story_arc_id, position, series_run_id);
                CREATE TABLE IF NOT EXISTS series_aliases (
                    id INTEGER PRIMARY KEY,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS series_aliases_run ON series_aliases(series_run_id);
                CREATE TABLE IF NOT EXISTS file_identities (
                    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE RESTRICT,
                    raw_title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    identity_kind TEXT NOT NULL,
                    issue_number TEXT,
                    volume_number INTEGER,
                    match_confidence INTEGER,
                    match_basis_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS file_identities_series ON file_identities(series_run_id);
                CREATE TABLE IF NOT EXISTS creators (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_creators (
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    PRIMARY KEY(file_id, creator_id, role)
                );
                CREATE INDEX IF NOT EXISTS file_creators_creator ON file_creators(creator_id);
                CREATE TABLE IF NOT EXISTS series_run_creators (
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    source TEXT NOT NULL,
                    issue_count INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(series_run_id, creator_id, role, source)
                );
                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    issue_number TEXT NOT NULL,
                    title TEXT,
                    publication_year INTEGER,
                    publication_date TEXT,
                    cover TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(series_run_id, issue_number)
                );
                CREATE INDEX IF NOT EXISTS issues_series ON issues(series_run_id, issue_number);
                CREATE TABLE IF NOT EXISTS editions (
                    id INTEGER PRIMARY KEY,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    edition_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    subtitle TEXT,
                    volume_number INTEGER,
                    publication_year INTEGER,
                    publisher TEXT,
                    format TEXT,
                    edition_kind TEXT NOT NULL DEFAULT 'edition',
                    isbns_json TEXT NOT NULL,
                    cover TEXT,
                    source TEXT,
                    source_id TEXT,
                    verification_status TEXT,
                    coverage_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS editions_series ON editions(series_run_id);
                CREATE TABLE IF NOT EXISTS file_issue_links (
                    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS file_edition_links (
                    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS file_editions_edition ON file_edition_links(edition_id);
                -- Looked up by issue on every arc, request and ownership
                -- question; without it each was a scan of every link (Gate 4,
                -- 2026-10-05: the story-arcs grid took 0.7-2 s on 420 items).
                CREATE INDEX IF NOT EXISTS file_issue_links_issue ON file_issue_links(issue_id);
                CREATE TABLE IF NOT EXISTS edition_coverage_claims (
                    id INTEGER PRIMARY KEY,
                    edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
                    issue_id INTEGER REFERENCES issues(id) ON DELETE SET NULL,
                    series_label TEXT NOT NULL,
                    issue_number TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    resolution_status TEXT NOT NULL,
                    relation_kind TEXT NOT NULL DEFAULT 'full_issue'
                        CHECK(relation_kind IN ('full_issue', 'partial_story', 'excerpt', 'unknown')),
                    evidence TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(edition_id, series_label, issue_number, source)
                );
                CREATE INDEX IF NOT EXISTS edition_coverage_edition ON edition_coverage_claims(edition_id);
                CREATE INDEX IF NOT EXISTS edition_coverage_issue ON edition_coverage_claims(issue_id);
                CREATE TABLE IF NOT EXISTS edition_coverage_overrides (
                    id INTEGER PRIMARY KEY,
                    edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    issue_number TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('add', 'remove')),
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(edition_id, series_run_id, issue_number)
                );
                CREATE INDEX IF NOT EXISTS edition_coverage_overrides_edition
                    ON edition_coverage_overrides(edition_id);
                CREATE INDEX IF NOT EXISTS edition_coverage_overrides_issue
                    ON edition_coverage_overrides(series_run_id, issue_number);
                CREATE TABLE IF NOT EXISTS series_provider_ids (
                    id INTEGER PRIMARY KEY,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    api_url TEXT,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_id),
                    UNIQUE(series_run_id, provider)
                );
                CREATE TABLE IF NOT EXISTS issue_provider_ids (
                    id INTEGER PRIMARY KEY,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    api_url TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_id),
                    UNIQUE(issue_id, provider)
                );
                CREATE TABLE IF NOT EXISTS issue_metadata_overrides (
                    issue_id INTEGER PRIMARY KEY REFERENCES issues(id) ON DELETE CASCADE,
                    title TEXT,
                    publication_year INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS issue_metadata_override_history (
                    id INTEGER PRIMARY KEY,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS issue_metadata_override_history_issue
                    ON issue_metadata_override_history(issue_id, created_at);
                CREATE TABLE IF NOT EXISTS issue_catalog_status (
                    series_run_id INTEGER PRIMARY KEY REFERENCES series_runs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    provider TEXT,
                    provider_series_id TEXT,
                    issue_count INTEGER NOT NULL DEFAULT 0,
                    last_synced_at TEXT,
                    title_policy TEXT NOT NULL DEFAULT 'unknown',
                    -- What a provider actually said about the year the run
                    -- ended, kept apart from `status` because that column also
                    -- decides whether the issue list is trustworthy enough to
                    -- count ownership against. 'unknown' means no provider has
                    -- an opinion, which is not the same as "still publishing".
                    run_end_status TEXT NOT NULL DEFAULT 'unknown',
                    end_year INTEGER,
                    end_year_provider TEXT,
                    detail TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS acquisition_requests (
                    id INTEGER PRIMARY KEY,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('series', 'collection')),
                    series_run_id INTEGER REFERENCES series_runs(id) ON DELETE CASCADE,
                    series_family_id INTEGER REFERENCES series_families(id) ON DELETE CASCADE,
                    -- How much of the scope this request is for. 'run' follows
                    -- everything the scope covers and grows as the run does;
                    -- 'issues' covers exactly the issues enrolled in
                    -- acquisition_request_issues and never grows. Asking for one
                    -- new comic is not the same as following its run, and the
                    -- difference has to be declared: inferred narrowness --
                    -- "this request stands behind a replacement" -- is what let
                    -- one damaged issue enrol a whole run once already.
                    -- Validated in Python; no CHECK, so the alter path and the
                    -- create path cannot drift.
                    coverage TEXT NOT NULL DEFAULT 'run',
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'fulfilled', 'cancelled')),
                    acquisition_preference TEXT NOT NULL DEFAULT 'either',
                    include_specials INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(
                        (scope_type='series' AND series_run_id IS NOT NULL AND series_family_id IS NULL)
                        OR (scope_type='collection' AND series_family_id IS NOT NULL AND series_run_id IS NULL)
                    )
                );
                CREATE INDEX IF NOT EXISTS acquisition_requests_status
                    ON acquisition_requests(status, updated_at);
                CREATE TABLE IF NOT EXISTS acquisition_request_issues (
                    request_id INTEGER NOT NULL REFERENCES acquisition_requests(id) ON DELETE CASCADE,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(request_id, issue_id)
                );
                CREATE INDEX IF NOT EXISTS acquisition_request_issues_issue
                    ON acquisition_request_issues(issue_id);
                CREATE TABLE IF NOT EXISTS acquisition_jobs (
                    id INTEGER PRIMARY KEY,
                    request_id INTEGER NOT NULL REFERENCES acquisition_requests(id) ON DELETE CASCADE,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'waiting', 'searching', 'grabbed', 'failed', 'fulfilled', 'cancelled')),
                    queue_reason TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(request_id, issue_id)
                );
                CREATE INDEX IF NOT EXISTS acquisition_jobs_status
                    ON acquisition_jobs(status, updated_at);
                CREATE INDEX IF NOT EXISTS acquisition_jobs_issue ON acquisition_jobs(issue_id);
                CREATE TABLE IF NOT EXISTS acquisition_job_events (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES acquisition_jobs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS acquisition_job_events_job
                    ON acquisition_job_events(job_id, created_at);
                CREATE TABLE IF NOT EXISTS acquisition_downloads (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL UNIQUE REFERENCES acquisition_jobs(id) ON DELETE CASCADE,
                    sab_nzo_id TEXT NOT NULL UNIQUE,
                    -- Where the comic came from. SABnzbd was the only answer
                    -- once, and the column exists because it no longer is: a
                    -- file handed over by the user does not pass through a
                    -- downloader at all, and reconciliation has to know which
                    -- of them to ask about a row.
                    source TEXT NOT NULL DEFAULT 'sabnzbd',
                    release_title TEXT NOT NULL,
                    release_key TEXT,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'downloading', 'completed', 'importing', 'waiting_for_files', 'imported', 'failed')),
                    sab_storage TEXT,
                    local_source TEXT,
                    destination TEXT,
                    source_size INTEGER,
                    source_sha256 TEXT,
                    -- SABnzbd is asked how far along its own downloads are, so
                    -- nothing is stored for them. A direct download has no one
                    -- to ask: Flipparr is the one fetching it.
                    bytes_fetched INTEGER NOT NULL DEFAULT 0,
                    bytes_total INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    failure_stage TEXT,
                    -- A release a person took from the set-aside list. The
                    -- import then takes their word on its name (app.py,
                    -- _choose_downloaded_comic) and never grabs another
                    -- release in its place.
                    taken_by_hand INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    imported_at TEXT
                );
                CREATE INDEX IF NOT EXISTS acquisition_downloads_status
                    ON acquisition_downloads(status, updated_at);
                -- One download belongs to the job that grabbed it, and a pack
                -- answers many. Each extra issue it delivered is recorded here,
                -- so a row can say "12 issues from one download" rather than
                -- twelve rows showing no download at all.
                CREATE TABLE IF NOT EXISTS acquisition_download_imports (
                    id INTEGER PRIMARY KEY,
                    download_id INTEGER NOT NULL
                        REFERENCES acquisition_downloads(id) ON DELETE CASCADE,
                    job_id INTEGER NOT NULL REFERENCES acquisition_jobs(id) ON DELETE CASCADE,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
                    destination TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(download_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS acquisition_release_failures (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES acquisition_jobs(id) ON DELETE CASCADE,
                    release_key TEXT NOT NULL,
                    release_title TEXT NOT NULL,
                    error TEXT,
                    kind TEXT,
                    sab_nzo_id TEXT,
                    sab_storage TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, release_key)
                );
                CREATE INDEX IF NOT EXISTS acquisition_release_failures_job
                    ON acquisition_release_failures(job_id, updated_at);
                CREATE TABLE IF NOT EXISTS file_replacement_requests (
                    id INTEGER PRIMARY KEY,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    reason_code TEXT NOT NULL,
                    desired_language TEXT,
                    acquisition_preference TEXT NOT NULL DEFAULT 'either',
                    acquisition_request_id INTEGER REFERENCES acquisition_requests(id) ON DELETE SET NULL,
                    quarantine_path TEXT,
                    status TEXT NOT NULL DEFAULT 'wanted'
                        CHECK(status IN ('wanted', 'searching', 'grabbed', 'failed', 'fulfilled', 'cancelled')),
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(file_id)
                );
                CREATE INDEX IF NOT EXISTS file_replacement_requests_status
                    ON file_replacement_requests(status, updated_at);
                CREATE TABLE IF NOT EXISTS file_metadata_overrides (
                    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    fields_json TEXT NOT NULL,
                    locked_fields_json TEXT NOT NULL,
                    match_source TEXT,
                    match_source_id TEXT,
                    match_candidate_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata_override_history (
                    id INTEGER PRIMARY KEY,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS metadata_override_history_file
                    ON metadata_override_history(file_id, created_at);
                CREATE TABLE IF NOT EXISTS file_cover_preferences (
                    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    cover_url TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS series_cover_preferences (
                    series_run_id INTEGER PRIMARY KEY REFERENCES series_runs(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    cover_url TEXT,
                    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
                    updated_at TEXT NOT NULL
                );
                -- The page behind a run's drawer header (schema 36). "chosen" is
                -- the reader's pick and stands until changed; "auto" is the one
                -- found for them, kept with its file's signature so a changed
                -- file is looked at again.
                CREATE TABLE IF NOT EXISTS series_backdrop_preferences (
                    series_run_id INTEGER PRIMARY KEY REFERENCES series_runs(id) ON DELETE CASCADE,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    page_member TEXT NOT NULL,
                    source TEXT NOT NULL,
                    file_signature TEXT,
                    updated_at TEXT NOT NULL
                );
                -- Where the panels are on a page (schema 43). Keyed by the
                -- archive member like a backdrop, and stamped with the file's
                -- signature so a re-scanned file is read again. Rectangles are
                -- normalised and unordered: the order depends on how the run
                -- reads, and is worked out when they are asked for.
                CREATE TABLE IF NOT EXISTS page_panels (
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    page_member TEXT NOT NULL,
                    file_signature TEXT NOT NULL,
                    source TEXT NOT NULL,
                    panels_json TEXT NOT NULL,
                    segmented INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (file_id, page_member)
                );
                /* The people reading this library (schema 49). Profile 1 is
                   the admin and always exists; a household with one person
                   never sees another. A name to sign in with is optional --
                   a profile picked on a shared device needs none -- and so
                   are the password and the PIN, both scrypt hashes written by
                   the app. `session_version` is what revokes a profile's
                   sign-ins: a cookie carries the version it was issued at. */
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    login_name TEXT UNIQUE COLLATE NOCASE,
                    role TEXT NOT NULL DEFAULT 'reader' CHECK(role IN ('admin', 'reader')),
                    colour TEXT,
                    password_hash TEXT,
                    pin_hash TEXT,
                    can_request INTEGER NOT NULL DEFAULT 1,
                    auto_approve INTEGER NOT NULL DEFAULT 0,
                    show_on_picker INTEGER NOT NULL DEFAULT 1,
                    session_version INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    avatar_source TEXT,
                    avatar_updated_at TEXT,
                    switch_lock TEXT NOT NULL DEFAULT 'open' CHECK(switch_lock IN ('open', 'pin', 'password')),
                    pin_length INTEGER
                );
                /* A reader's own settings that should follow them to any
                   device -- how panel view steps, for one -- as a small JSON
                   object the app validates key by key. */
                CREATE TABLE IF NOT EXISTS user_prefs (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    prefs_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                /* A reader's request, waiting for the admin (schema 52). Pending
                   is inert: no acquisition request, no monitoring, no job and
                   no provider import exist until the admin approves, which
                   makes the same call the admin's own button would and records
                   what it became. `params_json` is that call's arguments;
                   `detail_json` what the queue shows (title, year, cover,
                   issue numbers). `target_key` names what is asked for, so
                   one person asking twice is one request. */
                CREATE TABLE IF NOT EXISTS member_requests (
                    id INTEGER PRIMARY KEY,
                    requested_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    -- Validated in Python (MEMBER_REQUEST_KINDS); no CHECK, so a
                    -- new kind is not a table rebuild.
                    kind TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    params_json TEXT NOT NULL DEFAULT '{}',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'approved', 'declined', 'cancelled', 'failed')),
                    decided_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    decided_at TEXT,
                    decline_reason TEXT,
                    failure TEXT,
                    acquisition_request_id INTEGER REFERENCES acquisition_requests(id) ON DELETE SET NULL,
                    series_run_id INTEGER REFERENCES series_runs(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS member_requests_by_status ON member_requests(status, created_at);
                CREATE INDEX IF NOT EXISTS member_requests_by_requester ON member_requests(requested_by, created_at DESC);
                /* What a profile has been told (schema 56), so the bell is the
                   same on every device it signs in on. One row is one line in
                   the bell: comics arriving (several issues of one run merge
                   into one row while it is unread) or a request decided.
                   `payload_json` is what happened; the wording is the app's,
                   made when the bell is read. Read and cleared are the
                   profile's own. What needs someone's attention is not here:
                   it is worked out from the library as it stands, so it goes
                   away when it is dealt with; only its dismissals are kept. */
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    group_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    read_at TEXT,
                    cleared_at TEXT
                );
                CREATE INDEX IF NOT EXISTS notifications_by_user ON notifications(user_id, cleared_at, updated_at DESC);
                CREATE TABLE IF NOT EXISTS notification_dismissals (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    key TEXT NOT NULL,
                    dismissed_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, key)
                );
                /* How far the arrivals sweep has read: one high-water mark. */
                CREATE TABLE IF NOT EXISTS notification_marks (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                /* A story arc saved to read in order across runs (schema 57):
                   Absolute Power through Batman, Superman and its own
                   miniseries as one sequence. Not `story_arcs`, which are a
                   collection's run groups. Saved from Metron first; a CBL
                   reading list or a hand-built arc has no provider of its
                   own and its items may name only a local issue. Positions
                   are the household's: a refresh from the provider never
                   reorders them. An item keeps its title and number so it
                   outlives its run, and `issue_id` is set again each time the
                   arc is opened, so a run imported later heals the gap.
                   63: sort_mode is the order it is read in -- as arranged, or
                   by cover date, so an issue added later falls into place. */
                CREATE TABLE IF NOT EXISTS reading_lists (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    cover TEXT,
                    source TEXT NOT NULL DEFAULT 'metron' CHECK(source IN ('metron', 'manual', 'cbl')),
                    provider TEXT,
                    provider_arc_id TEXT,
                    source_url TEXT,
                    source_name TEXT,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    refreshed_at TEXT,
                    -- The page behind the drawer's header (schema 58): one of
                    -- the arc's own comics, chosen or found, as a run's is.
                    -- 62: whose arc it is (NULL: the household's) and whether its
                    -- maker shared it. A profile's own arc goes with the profile.
                    owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    shared INTEGER NOT NULL DEFAULT 0,
                    backdrop_file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
                    backdrop_member TEXT,
                    backdrop_source TEXT,
                    backdrop_signature TEXT,
                    sort_mode TEXT NOT NULL DEFAULT 'custom' CHECK(sort_mode IN ('custom', 'release')),
                    UNIQUE(provider, provider_arc_id)
                );
                CREATE TABLE IF NOT EXISTS reading_list_items (
                    id INTEGER PRIMARY KEY,
                    reading_list_id INTEGER NOT NULL REFERENCES reading_lists(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    provider TEXT,
                    provider_issue_id TEXT,
                    provider_series_id TEXT,
                    series_title TEXT NOT NULL,
                    series_year INTEGER,
                    issue_number TEXT NOT NULL,
                    cover_date TEXT,
                    cover TEXT,
                    issue_type TEXT,
                    issue_id INTEGER REFERENCES issues(id) ON DELETE SET NULL,
                    removed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(reading_list_id, provider, provider_issue_id)
                );
                CREATE INDEX IF NOT EXISTS reading_list_items_list
                    ON reading_list_items(reading_list_id, removed_at, position, id);
                CREATE INDEX IF NOT EXISTS reading_list_items_issue ON reading_list_items(issue_id);
                -- Collections (schema 60): the household's own groups of runs,
                -- after Plex's. A run may be in any number of them, which is
                -- what sets them apart from a series family (one per run, and
                -- tied to monitoring). Made by the admin; a profile sees the
                -- members its rating allows.
                -- 63: a collection's owner and its header page, as a story
                -- arc's (schema 58, 62): NULL owner is the household's, which
                -- every collection before this stays. normalized_name is
                -- unique per owner ("u<id>:" before a profile's own). These
                -- notes stay outside the column list: an older SQLite's DROP
                -- COLUMN cannot rewrite a definition with comments inside it.
                CREATE TABLE IF NOT EXISTS run_collections (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    summary TEXT,
                    sort_mode TEXT NOT NULL DEFAULT 'custom' CHECK(sort_mode IN ('custom', 'title', 'year')),
                    cover_series_run_id INTEGER REFERENCES series_runs(id) ON DELETE SET NULL,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    shared INTEGER NOT NULL DEFAULT 0,
                    backdrop_file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
                    backdrop_member TEXT,
                    backdrop_source TEXT,
                    backdrop_signature TEXT
                );
                CREATE TABLE IF NOT EXISTS run_collection_items (
                    collection_id INTEGER NOT NULL REFERENCES run_collections(id) ON DELETE CASCADE,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (collection_id, series_run_id)
                );
                CREATE INDEX IF NOT EXISTS run_collection_items_run ON run_collection_items(series_run_id);
                -- A profile's reading list (schema 61): the runs, story arcs and
                -- collections it added, to find again when choosing what to
                -- read next. Its own rows, like its place in a comic.
                CREATE TABLE IF NOT EXISTS series_run_bookmarks (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    series_run_id INTEGER NOT NULL REFERENCES series_runs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, series_run_id)
                );
                CREATE TABLE IF NOT EXISTS reading_list_bookmarks (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    reading_list_id INTEGER NOT NULL REFERENCES reading_lists(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, reading_list_id)
                );
                CREATE TABLE IF NOT EXISTS run_collection_bookmarks (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    collection_id INTEGER NOT NULL REFERENCES run_collections(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, collection_id)
                );
                CREATE TABLE IF NOT EXISTS metadata_enrichment_jobs (
                    id INTEGER PRIMARY KEY,
                    series_run_id INTEGER NOT NULL UNIQUE
                        REFERENCES series_runs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'running', 'waiting', 'complete', 'review', 'failed')),
                    priority INTEGER NOT NULL DEFAULT 100,
                    provider TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS metadata_enrichment_jobs_status
                    ON metadata_enrichment_jobs(status, next_attempt_at, priority, id);
                CREATE TABLE IF NOT EXISTS metadata_provider_state (
                    provider TEXT PRIMARY KEY,
                    next_allowed_at TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS series_monitor_refreshes (
                    series_run_id INTEGER PRIMARY KEY
                        REFERENCES series_runs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'scheduled'
                        CHECK(status IN ('scheduled', 'running', 'waiting', 'complete')),
                    last_checked_at TEXT,
                    next_check_at TEXT NOT NULL,
                    last_provider TEXT,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS series_monitor_refreshes_due
                    ON series_monitor_refreshes(status, next_check_at);
                CREATE TABLE IF NOT EXISTS provider_cache (
                    key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    saved_at REAL NOT NULL,
                    status INTEGER NOT NULL DEFAULT 200,
                    payload TEXT
                );
                CREATE INDEX IF NOT EXISTS provider_cache_saved_at
                    ON provider_cache(saved_at);
                """
            )
            edition_columns = {row["name"] for row in connection.execute("PRAGMA table_info(editions)")}
            if "edition_kind" not in edition_columns:
                connection.execute(
                    "ALTER TABLE editions ADD COLUMN edition_kind TEXT NOT NULL DEFAULT 'edition'"
                )
            override_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(file_metadata_overrides)")
            }
            if "match_candidate_json" not in override_columns:
                connection.execute("ALTER TABLE file_metadata_overrides ADD COLUMN match_candidate_json TEXT")
            family_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(series_families)")
            }
            if "acquisition_preference" not in family_columns:
                connection.execute(
                    "ALTER TABLE series_families ADD COLUMN acquisition_preference TEXT NOT NULL DEFAULT 'either'"
                )
            if "include_specials" not in family_columns:
                connection.execute(
                    "ALTER TABLE series_families ADD COLUMN include_specials INTEGER NOT NULL DEFAULT 1"
                )
            if "monitoring_status" not in family_columns:
                connection.execute(
                    "ALTER TABLE series_families ADD COLUMN monitoring_status TEXT NOT NULL DEFAULT 'monitored'"
                )
            run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(series_runs)")
            }
            if "acquisition_preference" not in run_columns:
                connection.execute(
                    "ALTER TABLE series_runs ADD COLUMN acquisition_preference TEXT NOT NULL DEFAULT 'either'"
                )
            if "monitoring_status" not in run_columns:
                connection.execute(
                    "ALTER TABLE series_runs ADD COLUMN monitoring_status TEXT NOT NULL DEFAULT 'cataloged'"
                )
            if "format" not in run_columns:
                connection.execute(
                    "ALTER TABLE series_runs ADD COLUMN format TEXT NOT NULL DEFAULT 'comic'"
                )
            if "creators_synced_at" not in run_columns:
                connection.execute("ALTER TABLE series_runs ADD COLUMN creators_synced_at TEXT")
            download_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(acquisition_downloads)")
            }
            if "source" not in download_columns:
                connection.execute(
                    "ALTER TABLE acquisition_downloads ADD COLUMN source TEXT NOT NULL DEFAULT 'sabnzbd'"
                )
            if "bytes_fetched" not in download_columns:
                connection.execute(
                    "ALTER TABLE acquisition_downloads ADD COLUMN bytes_fetched INTEGER NOT NULL DEFAULT 0"
                )
            if "bytes_total" not in download_columns:
                connection.execute(
                    "ALTER TABLE acquisition_downloads ADD COLUMN bytes_total INTEGER NOT NULL DEFAULT 0"
                )
            issue_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(issues)")
            }
            if "publication_date" not in issue_columns:
                connection.execute("ALTER TABLE issues ADD COLUMN publication_date TEXT")
            if "cover" not in issue_columns:
                connection.execute("ALTER TABLE issues ADD COLUMN cover TEXT")
            issue_catalog_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(issue_catalog_status)")
            }
            if "title_policy" not in issue_catalog_columns:
                connection.execute(
                    "ALTER TABLE issue_catalog_status ADD COLUMN title_policy TEXT NOT NULL DEFAULT 'unknown'"
                )
            if "run_end_status" not in issue_catalog_columns:
                connection.execute(
                    "ALTER TABLE issue_catalog_status ADD COLUMN run_end_status TEXT NOT NULL DEFAULT 'unknown'"
                )
            if "end_year" not in issue_catalog_columns:
                connection.execute("ALTER TABLE issue_catalog_status ADD COLUMN end_year INTEGER")
            if "end_year_provider" not in issue_catalog_columns:
                connection.execute("ALTER TABLE issue_catalog_status ADD COLUMN end_year_provider TEXT")
            # A download marked failed whose job has since moved on is dead
            # state by definition: the latch that used to leave it there is
            # fixed, but rows written before that fix are still on disk, and
            # the bell, the rail badge and the Failed tab all read them as
            # work outstanding. Idempotent, so it runs on every start rather
            # than needing a version gate.
            connection.execute(
                """DELETE FROM acquisition_downloads
                     WHERE status='failed'
                       AND job_id IN (SELECT id FROM acquisition_jobs WHERE status <> 'failed')"""
            )
            # Every 'complete' on record was written because some provider named
            # a year the run had ended. The year itself was never stored and
            # cannot be invented, but the conclusion is evidence enough to keep
            # the badge. 'complete_to_date' is not recoverable -- it is the union
            # of "still running", "that provider has no end-year field", and
            # writes that discarded the year -- so those become 'unknown'.
            connection.execute(
                """UPDATE issue_catalog_status
                      SET run_end_status='ended', end_year_provider=provider
                    WHERE status='complete' AND run_end_status='unknown'"""
            )
            acquisition_request_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(acquisition_requests)")
            }
            if "coverage" not in acquisition_request_columns:
                connection.execute(
                    "ALTER TABLE acquisition_requests ADD COLUMN coverage TEXT NOT NULL DEFAULT 'run'"
                )
            series_run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(series_runs)")
            }
            if "reading_direction" not in series_run_columns:
                # Null means "decide from the medium". Manga reads right to
                # left, but an English edition of one may be flipped, and no
                # provider records which -- so it is the reader's to say.
                connection.execute("ALTER TABLE series_runs ADD COLUMN reading_direction TEXT")
            coverage_claim_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(edition_coverage_claims)"
                )
            }
            if "relation_kind" not in coverage_claim_columns:
                connection.execute(
                    "ALTER TABLE edition_coverage_claims "
                    "ADD COLUMN relation_kind TEXT NOT NULL DEFAULT 'full_issue'"
                )
            replacement_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(file_replacement_requests)")
            }
            if "acquisition_preference" not in replacement_columns:
                connection.execute(
                    "ALTER TABLE file_replacement_requests ADD COLUMN acquisition_preference TEXT NOT NULL DEFAULT 'either'"
                )
            if "acquisition_request_id" not in replacement_columns:
                connection.execute(
                    "ALTER TABLE file_replacement_requests ADD COLUMN acquisition_request_id INTEGER REFERENCES acquisition_requests(id) ON DELETE SET NULL"
                )
            if "quarantine_path" not in replacement_columns:
                connection.execute(
                    "ALTER TABLE file_replacement_requests ADD COLUMN quarantine_path TEXT"
                )
            # Requests written before `coverage` existed carry its default, and
            # a replacement's request must not read as a followed run now that
            # the inferred guard it relied on is gone. After the ALTER above,
            # because it reads a column that may itself have just been added.
            # Idempotent, so it runs on every start rather than needing a gate.
            connection.execute(
                """UPDATE acquisition_requests SET coverage='issues'
                    WHERE coverage='run'
                      AND EXISTS(
                          SELECT 1 FROM file_replacement_requests
                          WHERE file_replacement_requests.acquisition_request_id=
                                acquisition_requests.id
                      )"""
            )
            acquisition_download_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(acquisition_downloads)")
            }
            if "source_size" not in acquisition_download_columns:
                connection.execute("ALTER TABLE acquisition_downloads ADD COLUMN source_size INTEGER")
            if "source_sha256" not in acquisition_download_columns:
                connection.execute("ALTER TABLE acquisition_downloads ADD COLUMN source_sha256 TEXT")
            if "imported_at" not in acquisition_download_columns:
                connection.execute("ALTER TABLE acquisition_downloads ADD COLUMN imported_at TEXT")
            if "failure_stage" not in acquisition_download_columns:
                connection.execute("ALTER TABLE acquisition_downloads ADD COLUMN failure_stage TEXT")
            release_failure_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(acquisition_release_failures)")
            }
            for column in ("kind", "sab_nzo_id", "sab_storage"):
                if column not in release_failure_columns:
                    connection.execute(f"ALTER TABLE acquisition_release_failures ADD COLUMN {column} TEXT")
            if "release_key" not in acquisition_download_columns:
                connection.execute("ALTER TABLE acquisition_downloads ADD COLUMN release_key TEXT")

            # Preserve failures recorded before release-level fallback existed.
            # The legacy key is internal; title matching also prevents the same
            # failed Usenet post from being selected through another indexer.
            connection.execute(
                """
                INSERT OR IGNORE INTO acquisition_release_failures (
                    job_id,
                    release_key,
                    release_title,
                    error,
                    created_at,
                    updated_at
                )
                SELECT
                    job_id,
                    COALESCE(NULLIF(release_key, ''), 'legacy-sab:' || sab_nzo_id),
                    release_title,
                    error,
                    updated_at,
                    updated_at
                FROM acquisition_downloads
                WHERE status = 'failed'
                  AND failure_stage = 'download'
                """
            )
            acquisition_download_sql = str(connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='acquisition_downloads'"
            ).fetchone()["sql"] or "")
            if "waiting_for_files" not in acquisition_download_sql:
                # SQLite cannot alter a CHECK constraint in place. Preserve every
                # durable SAB/import field while widening the existing state machine.
                connection.executescript(
                    """
                    CREATE TABLE acquisition_downloads_next (
                        id INTEGER PRIMARY KEY,
                        job_id INTEGER NOT NULL UNIQUE REFERENCES acquisition_jobs(id) ON DELETE CASCADE,
                        sab_nzo_id TEXT NOT NULL UNIQUE,
                        release_title TEXT NOT NULL,
                        release_key TEXT,
                        status TEXT NOT NULL DEFAULT 'queued'
                            CHECK(status IN ('queued', 'downloading', 'completed', 'importing', 'waiting_for_files', 'imported', 'failed')),
                        sab_storage TEXT,
                        local_source TEXT,
                        destination TEXT,
                        source_size INTEGER,
                        source_sha256 TEXT,
                        error TEXT,
                        failure_stage TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        imported_at TEXT
                    );
                    INSERT INTO acquisition_downloads_next(
                        id, job_id, sab_nzo_id, release_title, release_key, status, sab_storage,
                        local_source, destination, source_size, source_sha256, error,
                        failure_stage, created_at, updated_at, imported_at
                    )
                    SELECT id, job_id, sab_nzo_id, release_title, release_key, status, sab_storage,
                           local_source, destination, source_size, source_sha256, error,
                           failure_stage, created_at, updated_at, imported_at
                    FROM acquisition_downloads;
                    DROP TABLE acquisition_downloads;
                    ALTER TABLE acquisition_downloads_next RENAME TO acquisition_downloads;
                    CREATE INDEX acquisition_downloads_status
                        ON acquisition_downloads(status, updated_at);
                    """
                )
            # After the rebuild above: its explicit column list would drop a
            # column added before it.
            if "taken_by_hand" not in {
                row["name"] for row in connection.execute("PRAGMA table_info(acquisition_downloads)")
            }:
                connection.execute(
                    "ALTER TABLE acquisition_downloads ADD COLUMN taken_by_hand INTEGER NOT NULL DEFAULT 0"
                )
            monitor_now = _utc_now()
            monitor_next = (
                dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(hours=MONITORED_RUN_REFRESH_HOURS)
            ).isoformat()
            connection.execute(
                """INSERT OR IGNORE INTO series_monitor_refreshes(
                       series_run_id, status, next_check_at, updated_at
                   )
                   SELECT series_run_id, 'scheduled', ?, ?
                   FROM acquisition_requests
                   WHERE scope_type='series' AND status='open' AND coverage='run'""",
                (monitor_next, monitor_now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO series_monitor_refreshes(
                       series_run_id, status, next_check_at, updated_at
                   )
                   SELECT series_family_memberships.series_run_id, 'scheduled', ?, ?
                   FROM acquisition_requests
                   JOIN series_family_memberships
                     ON series_family_memberships.series_family_id=
                        acquisition_requests.series_family_id
                   WHERE acquisition_requests.scope_type='collection'
                     AND acquisition_requests.status='open'
                     AND acquisition_requests.coverage='run'""",
                (monitor_next, monitor_now),
            )
            # The admin exists in every library, from the first start; the
            # personal tables are made in their per-reader shape (a library
            # older than 49 already has them, and is rebuilt below).
            now = _utc_now()
            connection.execute(
                """INSERT OR IGNORE INTO users(id, name, role, created_at, updated_at)
                   VALUES (?, 'Admin', 'admin', ?, ?)""",
                (ADMIN_USER_ID, now, now),
            )
            for name, create, _columns in PERSONAL_TABLES:
                connection.execute(create.format(name=name))
            row = connection.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
            stored_version = int(row["version"]) if row else None
            if row is None:
                connection.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] > SCHEMA_VERSION:
                raise CatalogNewerThanBuild(int(row["version"]))
            elif row["version"] < SCHEMA_VERSION:
                connection.execute("UPDATE schema_info SET version=?", (SCHEMA_VERSION,))
            if stored_version is not None and stored_version < 30:
                # One pass per version, then never again. End-year evidence was
                # not stored before 29, so the runs whose lifecycle could not be
                # recovered from what is on record are asked once more rather
                # than left showing nothing forever.
                #
                # 30 repeats it because two GCD write paths in the 29 build
                # computed the coverage status but dropped the evidence behind
                # it, so runs GCD had positively determined were finished still
                # landed with none. Their answers are recoverable; asking again
                # is cheaper than reasoning about which ones.
                #
                # attempt_count is reset or the three-attempt rule fails the job
                # the moment it is picked up. This is the only version-gated
                # step here -- everything above it is idempotent by construction.
                unknown_now = _utc_now()
                connection.execute(
                    """INSERT OR IGNORE INTO metadata_enrichment_jobs(
                           series_run_id, status, priority, created_at, updated_at)
                       SELECT series_run_id, 'queued', 100, ?, ?
                         FROM issue_catalog_status WHERE run_end_status='unknown'""",
                    (unknown_now, unknown_now),
                )
                connection.execute(
                    """UPDATE metadata_enrichment_jobs
                          SET status='queued', attempt_count=0, next_attempt_at=NULL,
                              last_error=NULL, completed_at=NULL, updated_at=?
                        WHERE series_run_id IN (SELECT series_run_id
                                                  FROM issue_catalog_status
                                                 WHERE run_end_status='unknown')""",
                    (unknown_now,),
                )
            if stored_version is not None and stored_version < 34:
                self._forget_misread_scene_releases(connection)
            if stored_version is not None and stored_version < 35:
                self._forget_vague_refusals(connection)
            if stored_version is not None and stored_version < 46:
                # Before 44 a vision model's "no panels" was thrown away with
                # a failed call, and the page quartered. Now it is one panel
                # shown whole -- but the rows cannot say which they were, so
                # the pages the model was asked about and left unsegmented
                # are asked once more. A handful per library, on demand.
                #
                # 45 repeated it: the 44 build still counted a connector that
                # did not answer as one that had, and stamped the same rows
                # again while the account behind it was out of credit.
                #
                # 46 lets every vision reading go: until now a model's boxes
                # were believed on coverage alone, and a grid one guessed
                # through an eight-panel page was kept as its layout. Boxes
                # are corrected against the page's gutters now, and the few
                # pages already asked about are asked under that rule.
                connection.execute("DELETE FROM page_panels WHERE source='vlm'")
            if stored_version is not None and stored_version < 47:
                # 47: a corrected answer now has to account for three quarters
                # of the page (page_panels.VISION_READ_MIN); a reading kept
                # under the old floor that falls short is asked again. Only
                # those -- a spread in one library -- not every reading.
                short = [
                    (row["file_id"], row["page_member"]) for row in connection.execute(
                        "SELECT file_id, page_member, panels_json FROM page_panels WHERE source='vlm' AND segmented=1"
                    ).fetchall()
                    if _panels_cover(row["panels_json"]) < 0.75
                ]
                connection.executemany("DELETE FROM page_panels WHERE file_id=? AND page_member=?", short)
            # 48: the place is kept to the panel as well as the page, so panel
            # view resumes on the panel it was left on, as Guided View does.
            progress_columns = {row["name"] for row in connection.execute("PRAGMA table_info(reading_progress)")}
            if "panel" not in progress_columns:
                connection.execute("ALTER TABLE reading_progress ADD COLUMN panel INTEGER NOT NULL DEFAULT 0")
            # 49: reading history and ratings belong to a reader. Everything
            # recorded before profiles existed was the admin's, and stays so.
            self._key_personal_tables_by_reader(connection)
            # Indexes on the reader's column come after the rebuild: made in
            # the script above, they would name a column an older library's
            # table did not have yet, and the library would not open.
            connection.execute(
                "CREATE INDEX IF NOT EXISTS reading_progress_by_reader ON reading_progress(user_id, updated_at DESC)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS reading_progress_file ON reading_progress(file_id)")
            # 50: a profile's picture -- a photo, or a cover from the library.
            # The image is a file the app writes; the row says where it came
            # from and when, which is what makes its URL change with it.
            user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
            for column in ("avatar_source", "avatar_updated_at"):
                if column not in user_columns:
                    connection.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")
            # 51: what a profile asks for when someone switches to it -- nothing,
            # its PIN or its password -- chosen by the profile, not implied by
            # which secrets it happens to have. Existing profiles keep what
            # they asked for before: a PIN if they had one; the admin, without
            # one, their password.
            # 53: how many digits the PIN has, so the picker's number pad
            # shows that many dots and opens on the last one, as a phone's
            # lock screen does. A PIN set before this is learned the first
            # time it is entered right.
            if "pin_length" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN pin_length INTEGER")
            # 58: a story arc's header background, chosen from its own comics.
            list_columns = {row["name"] for row in connection.execute("PRAGMA table_info(reading_lists)")}
            for column, kind in (("backdrop_file_id", "INTEGER REFERENCES files(id) ON DELETE SET NULL"),
                                 ("backdrop_member", "TEXT"), ("backdrop_source", "TEXT"), ("backdrop_signature", "TEXT")):
                if list_columns and column not in list_columns:
                    connection.execute(f"ALTER TABLE reading_lists ADD COLUMN {column} {kind}")
            # 62: hand-built arcs belong to the profile that made them until it
            # shares them; every arc before this stays the household's.
            for column, kind in (("owner_user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
                                 ("shared", "INTEGER NOT NULL DEFAULT 0")):
                if list_columns and column not in list_columns:
                    connection.execute(f"ALTER TABLE reading_lists ADD COLUMN {column} {kind}")
            # 63: an arc's saved order; a collection's maker, sharing and header
            # page, as an arc's. Every collection before this is the household's.
            if list_columns and "sort_mode" not in list_columns:
                connection.execute("ALTER TABLE reading_lists ADD COLUMN sort_mode TEXT NOT NULL DEFAULT 'custom' "
                                   "CHECK(sort_mode IN ('custom', 'release'))")
            collection_columns = {row["name"] for row in connection.execute("PRAGMA table_info(run_collections)")}
            for column, kind in (("owner_user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
                                 ("shared", "INTEGER NOT NULL DEFAULT 0"),
                                 ("backdrop_file_id", "INTEGER REFERENCES files(id) ON DELETE SET NULL"),
                                 ("backdrop_member", "TEXT"), ("backdrop_source", "TEXT"), ("backdrop_signature", "TEXT")):
                if collection_columns and column not in collection_columns:
                    connection.execute(f"ALTER TABLE run_collections ADD COLUMN {column} {kind}")
            # 64: direct downloads take their neutral name, "direct_site", in
            # the records that carry it: the source, the download identity and
            # the staging paths (the folder moves when it is next used).
            for table in ("acquisition_downloads", "acquisition_release_failures"):
                columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                if "source" in columns:
                    connection.execute(f"UPDATE {table} SET source='direct_site' WHERE source='getcomics'")
                if "sab_nzo_id" in columns:
                    connection.execute(
                        f"UPDATE {table} SET sab_nzo_id='direct_site:' || substr(sab_nzo_id, 11) "
                        "WHERE sab_nzo_id LIKE 'getcomics:%'")
                for column in ("sab_storage", "local_source"):
                    if column in columns:
                        connection.execute(
                            f"UPDATE {table} SET {column}=replace({column}, '/downloads/getcomics/', '/downloads/direct_site/') "
                            f"WHERE {column} LIKE '%/downloads/getcomics/%'")
            # 55: member_requests loses its CHECK on kind (story arcs are a new
            # kind; kinds are validated in Python). SQLite cannot drop a
            # constraint in place, so the table is copied, counted and swapped.
            request_sql = (connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='member_requests'").fetchone() or [""])[0] or ""
            if "kind IN (" in request_sql:
                before = connection.execute("SELECT COUNT(*) FROM member_requests").fetchone()[0]
                connection.execute("SAVEPOINT member_requests_kinds")
                connection.execute(
                    re.sub(r"CREATE TABLE (?:IF NOT EXISTS )?member_requests",
                           "CREATE TABLE member_requests_rebuilt",
                           re.sub(r"\s*CHECK\s*\(\s*kind IN \([^)]*\)\s*\)", "", request_sql, count=1), count=1)
                )
                connection.execute("INSERT INTO member_requests_rebuilt SELECT * FROM member_requests")
                after = connection.execute("SELECT COUNT(*) FROM member_requests_rebuilt").fetchone()[0]
                if after != before:
                    connection.execute("ROLLBACK TO member_requests_kinds")
                    raise RuntimeError("Rebuilding member_requests would lose requests; nothing was changed")
                connection.execute("DROP TABLE member_requests")
                connection.execute("ALTER TABLE member_requests_rebuilt RENAME TO member_requests")
                connection.execute("CREATE INDEX IF NOT EXISTS member_requests_by_status ON member_requests(status, created_at)")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS member_requests_by_requester ON member_requests(requested_by, created_at DESC)")
                connection.execute("RELEASE member_requests_kinds")
            # 54: age ratings. A run's is found (Metron, then its cover) or
            # set by the admin, who always has the last word; a profile may
            # have a highest rating it reads, whether it sees unrated comics,
            # and whether it may browse Discover (which cannot be filtered:
            # provider results carry no rating).
            run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(series_runs)")}
            for column in ("age_rating", "age_rating_source", "age_rating_note", "age_rating_checked_at",
                           "age_rating_override"):
                if column not in run_columns:
                    connection.execute(f"ALTER TABLE series_runs ADD COLUMN {column} TEXT")
            if "max_rating" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN max_rating TEXT")
            if "allow_unrated" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN allow_unrated INTEGER NOT NULL DEFAULT 0")
            if "can_discover" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN can_discover INTEGER NOT NULL DEFAULT 1")
            if "switch_lock" not in user_columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN switch_lock TEXT NOT NULL DEFAULT 'open' "
                    "CHECK(switch_lock IN ('open', 'pin', 'password'))"
                )
                connection.execute(
                    "UPDATE users SET switch_lock = CASE WHEN pin_hash IS NOT NULL THEN 'pin' "
                    "WHEN role = 'admin' THEN 'password' ELSE 'open' END"
                )

    @staticmethod
    def _key_personal_tables_by_reader(connection: sqlite3.Connection) -> None:
        """Rebuild a pre-profiles personal table with a reader in its key, every row the admin's.

        SQLite cannot change a primary key in place, so each table is copied
        into its new shape and swapped in, inside a savepoint: the row count is
        compared before the old table goes, and on any difference nothing is
        changed and the library refuses to open rather than lose a place or a
        rating. A table already keyed by reader is left alone, so this runs
        once and is a no-op on every later start.
        """
        for name, create, columns in PERSONAL_TABLES:
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({name})")}
            if "user_id" in existing:
                continue
            rebuilt = f"{name}_by_reader"
            connection.execute("SAVEPOINT personal_by_reader")
            try:
                connection.execute(f"DROP TABLE IF EXISTS {rebuilt}")
                connection.execute(create.format(name=rebuilt))
                connection.execute(
                    f"INSERT INTO {rebuilt}(user_id, {columns}) SELECT ?, {columns} FROM {name}",
                    (ADMIN_USER_ID,),
                )
                before = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                after = connection.execute(f"SELECT COUNT(*) FROM {rebuilt}").fetchone()[0]
                if before != after:
                    raise RuntimeError(f"{name}: {before} rows before and {after} after keying by reader")
                connection.execute(f"DROP TABLE {name}")
                connection.execute(f"ALTER TABLE {rebuilt} RENAME TO {name}")
            except Exception:
                connection.execute("ROLLBACK TO personal_by_reader")
                connection.execute("RELEASE personal_by_reader")
                raise
            connection.execute("RELEASE personal_by_reader")

    @staticmethod
    def _forget_misread_scene_releases(connection: sqlite3.Connection) -> int:
        """Take back refusals made by a filename reading since corrected.

        Scene names mark the issue "No.19" -- American.Vampire.Vol.1.No.19 --
        and until schema 34 the import check did not read that as an issue.
        Every such download was refused as the wrong comic and its release
        barred from the job, so the right issue could never be taken again.
        Those refusals are forgotten and their jobs put back to search.
        """
        misread = [
            row for row in connection.execute(
                """SELECT id, job_id, release_title FROM acquisition_release_failures
                   WHERE error LIKE 'No downloaded comic confidently matched%'"""
            )
            if _SCENE_ISSUE_NAME.search(str(row["release_title"] or ""))
        ]
        return CatalogStore._forget_refusals(
            connection, misread,
            "Ready to search again: its releases were refused by a filename reading since fixed",
        )

    @staticmethod
    def _forget_vague_refusals(connection: sqlite3.Connection) -> int:
        """Take back every refusal the old all-or-nothing import check made.

        Until schema 35 a download was refused as the wrong comic whenever the
        file alone could not confirm the issue, and its release was barred for
        good -- Supergirl: Woman of Tomorrow #2's only release was. None of
        those refusals said why, and none proved anything, so they are
        forgotten and their jobs put back to search, for the check that
        trusts a matching release to decide again.
        """
        vague = connection.execute(
            """SELECT id, job_id FROM acquisition_release_failures
               WHERE error LIKE 'No downloaded comic confidently matched%'"""
        ).fetchall()
        return CatalogStore._forget_refusals(
            connection, vague,
            "Ready to search again: its release was refused by an import check since made fairer",
        )

    @staticmethod
    def _forget_refusals(connection: sqlite3.Connection, rows: Sequence[Any], detail: str) -> int:
        """Delete these release refusals and put their unfinished jobs back to search."""
        if not rows:
            return 0
        now = _utc_now()
        connection.executemany(
            "DELETE FROM acquisition_release_failures WHERE id=?", [(row["id"],) for row in rows]
        )
        for job_id in sorted({int(row["job_id"]) for row in rows}):
            changed = connection.execute(
                """UPDATE acquisition_jobs
                   SET status='queued', queue_reason=?, error=NULL, attempt_count=0,
                       last_attempt_at=NULL, updated_at=?
                   WHERE id=? AND status IN ('queued', 'waiting', 'failed')""",
                (detail, now, job_id),
            ).rowcount
            if changed:
                connection.execute(
                    """INSERT INTO acquisition_job_events(job_id, status, detail, created_at)
                       VALUES (?, 'queued', ?, ?)""",
                    (job_id, detail, now),
                )
        return len(rows)

    def _reconcile_all_identities(self) -> None:
        with self._connect() as connection:
            rows = list(connection.execute("SELECT id, parsed_json, result_json FROM files"))
        if not rows:
            return
        # This runs on every start-up. Reconciling each file in its own
        # transaction costs one fsync per file (~12ms on a NAS disk), so a
        # 963-file library spent ~90s here before the server accepted its first
        # request. One transaction for the whole pass does identical work.
        with self._write_lock, self._connect() as connection:
            for row in rows:
                self._reconcile_file_identity(
                    int(row["id"]),
                    _load_json(row["parsed_json"], {}),
                    _load_json(row["result_json"], {}),
                    connection=connection,
                )

    def _backfill_legacy_replacement_requests(self) -> None:
        """Attach pre-V25 replacement rows to durable acquisition requests once."""
        with self._connect() as connection:
            legacy = list(connection.execute(
                """SELECT id, file_id, reason_code, desired_language, acquisition_preference
                   FROM file_replacement_requests
                   WHERE acquisition_request_id IS NULL
                     AND status NOT IN ('fulfilled', 'cancelled')
                   ORDER BY id"""
            ))
        for row in legacy:
            try:
                self.request_file_replacement(
                    int(row["file_id"]),
                    str(row["reason_code"]),
                    row["desired_language"],
                    str(row["acquisition_preference"] or "either"),
                )
            except ValueError as exc:
                now = _utc_now()
                with self._write_lock, self._connect() as connection:
                    connection.execute(
                        """UPDATE file_replacement_requests
                           SET status='failed', error=?, updated_at=?
                           WHERE id=? AND acquisition_request_id IS NULL""",
                        (f"Replacement setup needs review: {exc}", now, int(row["id"])),
                    )

    def _identity_claim(
        self, parsed: dict[str, Any], result: dict[str, Any], override: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        override = override or {}
        identity = result.get("lookup_identity") or parsed
        embedded = result.get("embedded_metadata") or {}
        recommendation = _trusted_recommendation(result, override)
        # Overruled: the file's own ComicInfo names the series, issue and year.
        overruled = bool(result.get("recommendation")) and not recommendation
        if overruled and str(embedded.get("series") or "").strip():
            stated_year = _valid_year(embedded.get("year"))
            identity = {
                **identity, "title": str(embedded.get("series")).strip(),
                "issue": identity.get("issue") or embedded.get("number"),
                "year": identity.get("year") or stated_year,
            }
        issue = override.get("issueNumber") if "issueNumber" in override else recommendation.get("issue") or identity.get("issue")
        if override.get("recordType"):
            is_issue = override["recordType"] == "issue"
        else:
            is_issue = bool(issue or recommendation.get("record_type") == "single_issue")
        raw_title = override.get("title") or recommendation.get("title") or identity.get("title") or parsed.get("title") or "Unknown series"
        series_title = override.get("seriesTitle") or _canonical_series_title(raw_title, is_issue)
        assessed_confidence = (recommendation.get("identity_confidence") or {}).get("score")
        source = str(recommendation.get("source") or "")
        reasons = recommendation.get("match_reasons") or []
        if assessed_confidence is not None:
            confidence = assessed_confidence
        elif not recommendation:
            confidence = 65 if overruled else 25
        elif any("exact ISBN" in str(reason) for reason in reasons):
            confidence = 90
        elif source in {"Grand Comics Database", "GCD"}:
            confidence = 85
        elif source == "Open Library":
            confidence = 80
        elif source in {"EPUB package metadata", "ComicInfo.xml"}:
            confidence = 65
        else:
            confidence = 70
        try:
            confidence = max(0, min(100, int(confidence)))
        except (TypeError, ValueError):
            confidence = 25
        basis = ["filename"]
        if embedded.get("source"):
            basis.append(embedded["source"])
        if recommendation.get("source"):
            basis.append(recommendation["source"])
        if overruled:
            basis.append("ComicInfo series over a lookup that disagreed")
        if override:
            basis.append("Manual correction")
        provider_identity = (
            _candidate_issue_provider_identity(recommendation) if is_issue else None
        )
        return {
            "raw_title": str(raw_title),
            "series_title": series_title,
            "normalized_title": _normalized(raw_title),
            "kind": "issue" if is_issue else "edition",
            "issue": str(issue) if issue is not None else None,
            "run_year": _stated_run_year(parsed, identity, embedded),
            "run_year_named": _named_run_year(parsed, identity),
            "volume": override.get("volumeNumber") if "volumeNumber" in override else identity.get("volume") or parsed.get("volume"),
            "year": override.get("publicationYear") if "publicationYear" in override else recommendation.get("publication_year") or identity.get("year"),
            "publisher": override.get("publisher") if "publisher" in override else recommendation.get("publisher") or embedded.get("publisher"),
            "provider": (provider_identity or {}).get("provider"),
            "provider_series_id": (provider_identity or {}).get("providerSeriesId"),
            "confidence": 100 if override else confidence,
            "basis": list(dict.fromkeys(basis)),
        }

    def _local_run_start_year(
        self, connection: sqlite3.Connection, series_run_id: int
    ) -> int | None:
        """Infer a run's start only from locally owned opening issues.

        A random later issue cannot identify when a long-running series began.
        Issues #1-#3 can, and manual corrections take precedence over embedded
        or parsed values.
        """
        years: list[int] = []
        rows = connection.execute(
            """SELECT file_identities.issue_number, files.parsed_json, files.result_json,
                      file_metadata_overrides.fields_json,
                      file_metadata_overrides.match_candidate_json
               FROM file_identities
               JOIN files ON files.id=file_identities.file_id
               LEFT JOIN file_metadata_overrides
                 ON file_metadata_overrides.file_id=files.id
               WHERE file_identities.series_run_id=?
                 AND file_identities.identity_kind='issue'
                 AND files.present=1""",
            (series_run_id,),
        ).fetchall()
        stated = [year for year in (
            _stated_run_year(_load_json(row["parsed_json"], {}), {},
                             _load_json(row["result_json"], {}).get("embedded_metadata") or {})
            for row in rows) if year is not None]
        if stated:
            # What the files say their run is outranks any issue's cover date.
            return collections.Counter(stated).most_common(1)[0][0]
        for row in rows:
            issue_number = _issue_number_value(row["issue_number"])
            if issue_number is None or not 1 <= issue_number <= 3:
                continue
            parsed = _load_json(row["parsed_json"], {})
            result = _load_json(row["result_json"], {})
            override = _load_json(row["fields_json"], {})
            selected = _load_json(row["match_candidate_json"], None)
            recommendation = selected or result.get("recommendation") or {}
            embedded = result.get("embedded_metadata") or {}
            identity = result.get("lookup_identity") or {}
            year = next((candidate for candidate in (
                _valid_year(override.get("publicationYear")),
                _valid_year(recommendation.get("publication_year")),
                _valid_year(embedded.get("year") or embedded.get("date")),
                _valid_year(identity.get("year")),
                _valid_year(parsed.get("year")),
            ) if candidate is not None), None)
            if year is not None:
                years.append(year)
        return min(years) if years else None

    def _states_another_language(
        self, filename: Any, embedded: dict[str, Any], wanted: str | None = None,
    ) -> bool:
        """True when a file says it is a language the library did not ask for.

        A French edition filed as Saga #2 is right about the series and the
        issue number, so its embedded title and cover land on the shared issue
        record and the whole run reads as French. Silence is not a conflict:
        most comics state no language at all.
        """
        wanted = str(
            self.preferred_language if wanted is None else wanted or ""
        ).strip().casefold()
        if not wanted:
            return False
        stated = (
            normalize_language((embedded or {}).get("language"))
            or detect_language(filename)
        )
        return bool(stated and stated != wanted)

    def _local_issue_values(
        self, connection: sqlite3.Connection, issue_id: int
    ) -> dict[str, Any]:
        """Return the strongest file-owned values for an existing issue."""
        row = connection.execute(
            """SELECT files.filename, files.parsed_json, files.result_json,
                      file_metadata_overrides.fields_json,
                      file_metadata_overrides.match_candidate_json
               FROM file_issue_links
               JOIN files ON files.id=file_issue_links.file_id
               LEFT JOIN file_metadata_overrides
                 ON file_metadata_overrides.file_id=files.id
               WHERE file_issue_links.issue_id=? AND files.present=1
               LIMIT 1""",
            (issue_id,),
        ).fetchone()
        if row is None:
            return {}
        parsed = _load_json(row["parsed_json"], {})
        result = _load_json(row["result_json"], {})
        override = _load_json(row["fields_json"], {})
        selected = _load_json(row["match_candidate_json"], None)
        recommendation = selected or result.get("recommendation") or {}
        embedded = result.get("embedded_metadata") or {}
        # A correction the user typed is theirs and stands either way; what a
        # wrong-language file says about itself does not.
        if self._states_another_language(row["filename"], embedded):
            embedded = {}
        title = next((str(value).strip() for value in (
            override.get("subtitle"), recommendation.get("subtitle"),
            recommendation.get("story_title"), embedded.get("title"),
        ) if str(value or "").strip()), None)
        year = next((candidate for candidate in (
            _valid_year(override.get("publicationYear")),
            _valid_year(recommendation.get("publication_year")),
            _valid_year(embedded.get("year") or embedded.get("date")),
            _valid_year(parsed.get("year")),
        ) if candidate is not None), None)
        return {
            "title": title, "publication_year": year,
            "publication_date": recommendation.get("publication_date"),
            "cover": recommendation.get("cover"),
        }

    def _resolve_series_run(
        self, connection: sqlite3.Connection, claim: dict[str, Any],
        pinned_run_id: int | None = None,
    ) -> int:
        raw_key = _normalized(claim["raw_title"])
        canonical_key = _normalized(claim["series_title"])
        observed_year = _valid_year(claim.get("year"))
        anchor_year = _claim_run_anchor_year(claim)
        # A run year in the name or folder is the run's own: 2011 and 2012
        # are two Captain America series, not one with a year's slack.
        # ComicInfo's Volume, and a year inferred from an opening issue's
        # cover date, keep the slack they need.
        era_slack = 0 if anchor_year is not None and anchor_year == _valid_year(claim.get("run_year_named")) else 3
        normalized_publisher = _normalized_publisher(claim.get("publisher"))
        claim_provider = str(claim.get("provider") or "").strip()
        claim_provider_series_id = str(claim.get("provider_series_id") or "").strip()

        def matching_runs() -> list[sqlite3.Row]:
            candidates = [
                candidate for candidate in connection.execute(
                    "SELECT * FROM series_runs ORDER BY id"
                ).fetchall()
                if _normalized(candidate["canonical_title"]) == canonical_key
            ]
            if anchor_year is not None:
                candidates = [
                    candidate for candidate in candidates
                    if _years_compatible(candidate["start_year"], anchor_year, era_slack)
                ]
            return candidates

        def catalog_year_gap(candidate: sqlite3.Row) -> int | None:
            """How far this run's catalog puts the claimed issue from the claim's year.

            None when the run's catalog cannot place the issue in that era at
            all. Both runs of a relaunched title can list the same number --
            Nightwing ran to #30 in 2014 and the 2016 relaunch reached #30 in
            2017 -- so nearness decides between them, not a yes or no.

            A year may not invent a run -- that is `_claim_run_anchor_year`'s
            rule and it stands -- but when two runs of one title already exist
            it is exactly what tells them apart: Nightwing #23 dated 2016 is the
            2016 relaunch's, not the 2011 run's, whose catalog ends at #30.

            Only provider-backed rows count, or a row a misfiled file created
            would vouch for itself; and the provider's date outranks
            `publication_year`, which such a file may already have overwritten.
            """
            if observed_year is None or claim.get("kind") != "issue" or not claim.get("issue"):
                return None
            numbers = {str(claim["issue"]).strip()}
            value = _issue_number_value(claim["issue"])
            if value is not None:
                numbers.add(str(int(value)) if float(value).is_integer() else str(value))
            numbers = {number for number in numbers if number}
            if not numbers:
                return None
            placeholders = ",".join("?" for _ in numbers)
            catalogued = connection.execute(
                f"""SELECT issues.publication_year, issues.publication_date
                    FROM issues
                    JOIN issue_provider_ids ON issue_provider_ids.issue_id=issues.id
                    WHERE issues.series_run_id=? AND issues.issue_number IN ({placeholders})
                    LIMIT 1""",
                (int(candidate["id"]), *sorted(numbers)),
            ).fetchone()
            if catalogued is None:
                return None
            year = (
                _valid_year(catalogued["publication_date"])
                or _valid_year(catalogued["publication_year"])
            )
            if year is None or not _years_compatible(year, observed_year):
                return None
            return abs(year - observed_year)

        def candidate_rank(candidate: sqlite3.Row) -> tuple[int, int, int, int, int, int]:
            start_year = _valid_year(candidate["start_year"])
            publisher_match = bool(
                normalized_publisher
                and _normalized_publisher(candidate["publisher"]) == normalized_publisher
            )
            # Nearest catalog first; a run whose catalog cannot place the issue
            # at all ranks below every run that can.
            gap = catalog_year_gap(candidate)
            placed = -gap if gap is not None else -10_000
            # An issue published in 2025 should prefer a known 2025 relaunch
            # over a 1940 run. It must not, however, create a new 2025 run merely
            # because the only known run began earlier.
            plausible = observed_year is None or start_year is None or start_year <= observed_year + 1
            distance = abs(observed_year - start_year) if observed_year is not None and start_year is not None else 9999
            if anchor_year is None:
                # Later issues and collected editions describe publication
                # dates, not run boundaries. Prefer the durable base identity
                # over legacy ``::year`` splits, then the earliest plausible
                # run. Choosing the closest year here previously repopulated
                # bad bulk-intake splits on every metadata refresh.
                base_identity = candidate["canonical_key"] == canonical_key
                earliest = -(start_year if start_year is not None else 9999)
                return (
                    placed,
                    1 if publisher_match else 0,
                    1 if base_identity else 0,
                    1 if plausible else 0,
                    earliest,
                    -int(candidate["id"]),
                )
            # The year the claim gives its run picks the run nearest it: a
            # ComicInfo Volume of 2018 fits 2016 too, within the slack, but
            # is Justice League (2018)'s.
            anchor_distance = abs(anchor_year - start_year) if start_year is not None else 9999
            return (
                placed,
                1 if publisher_match else 0,
                1,
                1 if plausible else 0,
                -anchor_distance,
                -distance,
                -int(candidate["id"]),
            )

        # A provider's series/volume identity is stronger than a title and
        # publication year. It also lets a later issue select a genuine reboot
        # without teaching the resolver that every issue year is a new run.
        row = None
        provider_anchored = False
        # An acquisition import already knows which run was asked for. Two runs
        # of a series share every word of the title, so nothing derived from the
        # title may overrule the request that fetched the comic.
        if pinned_run_id is not None and connection.execute(
            "SELECT 1 FROM series_runs WHERE id=?", (int(pinned_run_id),)
        ).fetchone():
            row = {"series_run_id": int(pinned_run_id)}
            provider_anchored = True
        if row is None and claim_provider and claim_provider_series_id:
            provider_row = connection.execute(
                """SELECT series_provider_ids.series_run_id
                   FROM series_provider_ids
                   JOIN series_runs ON series_runs.id=series_provider_ids.series_run_id
                   WHERE series_provider_ids.provider=?
                     AND series_provider_ids.provider_id=?""",
                (claim_provider, claim_provider_series_id),
            ).fetchone()
            if provider_row:
                provider_series = connection.execute(
                    "SELECT canonical_title FROM series_runs WHERE id=?",
                    (provider_row["series_run_id"],),
                ).fetchone()
                if provider_series and _normalized(provider_series["canonical_title"]) == canonical_key:
                    row = provider_row
                    provider_anchored = True
        if row is None:
            row = connection.execute(
                "SELECT series_run_id FROM series_aliases WHERE normalized_alias=?",
                (canonical_key,),
            ).fetchone()
        if row is None and raw_key != canonical_key:
            row = connection.execute(
                "SELECT series_run_id FROM series_aliases WHERE normalized_alias=?",
                (raw_key,),
            ).fetchone()
        now = _utc_now()
        if row:
            series_run_id = int(row["series_run_id"])
            series = connection.execute("SELECT * FROM series_runs WHERE id=?", (series_run_id,)).fetchone()
            candidates = matching_runs()
            if provider_anchored:
                pass
            elif anchor_year is not None and (
                not _years_compatible(series["start_year"], anchor_year, era_slack)
                or (series["start_year"] is not None and any(
                    candidate["start_year"] is not None
                    and abs(candidate["start_year"] - anchor_year) < abs(series["start_year"] - anchor_year)
                    for candidate in candidates))
            ):
                # Identical titles are routinely relaunched decades apart. A
                # global title alias is useful for discovery but must never be
                # sufficient to merge two publication eras, nor to pass over
                # a run of the title nearer the claim's year.
                compatible = max(candidates, key=candidate_rank) if candidates else None
                row = {"series_run_id": int(compatible["id"])} if compatible else None
            elif anchor_year is None and len(candidates) > 1:
                # A later issue cannot establish a new era. In the absence of
                # a provider series ID, prefer the durable base run rather than
                # reinforcing a synthetic year split.
                compatible = max(candidates, key=candidate_rank)
                row = {"series_run_id": int(compatible["id"])}
        elif matching_runs():
            compatible = max(matching_runs(), key=candidate_rank)
            row = {"series_run_id": int(compatible["id"])}
        if row:
            series_run_id = int(row["series_run_id"])
            series = connection.execute("SELECT * FROM series_runs WHERE id=?", (series_run_id,)).fetchone()
            title = series["canonical_title"]
            if _normalized(title) == canonical_key and _display_title_rank(claim["series_title"]) > _display_title_rank(title):
                title = claim["series_title"]
            years = [value for value in (series["start_year"], anchor_year) if isinstance(value, int) and value > 0]
            start_year = min(years) if years else series["start_year"]
            publisher = series["publisher"] or claim.get("publisher")
            connection.execute(
                "UPDATE series_runs SET canonical_title=?, start_year=?, publisher=?, updated_at=? WHERE id=?",
                (title, start_year, publisher, now, series_run_id),
            )
        else:
            run_key = canonical_key
            if connection.execute(
                "SELECT 1 FROM series_runs WHERE canonical_key=?", (run_key,)
            ).fetchone():
                year_key = anchor_year or "unknown"
                base_key = f"{canonical_key}::year:{year_key}"
                run_key = base_key
                suffix = 2
                while connection.execute(
                    "SELECT 1 FROM series_runs WHERE canonical_key=?", (run_key,)
                ).fetchone():
                    run_key = f"{base_key}:{suffix}"
                    suffix += 1
            cursor = connection.execute(
                """INSERT INTO series_runs(canonical_title, canonical_key, start_year, publisher, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (claim["series_title"], run_key, anchor_year, claim.get("publisher"), now, now),
            )
            series_run_id = int(cursor.lastrowid)
        for alias, source in ((claim["series_title"], "canonical hint"), (claim["raw_title"], "catalog claim")):
            normalized_alias = _normalized(alias)
            if not normalized_alias:
                continue
            existing_alias = connection.execute(
                "SELECT id, series_run_id, alias, confirmed FROM series_aliases WHERE normalized_alias=?",
                (normalized_alias,),
            ).fetchone()
            if existing_alias is None:
                connection.execute(
                    """INSERT INTO series_aliases(series_run_id, alias, normalized_alias, source, confirmed, created_at)
                       VALUES (?, ?, ?, ?, 0, ?)""",
                    (series_run_id, alias, normalized_alias, source, now),
                )
            elif (
                int(existing_alias["series_run_id"]) == series_run_id
                and not existing_alias["confirmed"]
                and _display_title_rank(alias) > _display_title_rank(existing_alias["alias"])
            ):
                connection.execute(
                    "UPDATE series_aliases SET alias=?, source=? WHERE id=?",
                    (alias, source, existing_alias["id"]),
                )
        return series_run_id

    def _reconcile_file_identity(
        self, file_id: int, parsed: dict[str, Any], result: dict[str, Any], override: dict[str, Any] | None = None,
        *, connection: sqlite3.Connection | None = None, pinned_run_id: int | None = None,
    ) -> None:
        if override is None:
            with self._borrowed_read(connection) as read_connection:
                row = read_connection.execute(
                    "SELECT fields_json, match_candidate_json FROM file_metadata_overrides WHERE file_id=?", (file_id,)
                ).fetchone()
            override = _load_json(row["fields_json"], {}) if row else {}
            selected_candidate = _load_json(row["match_candidate_json"], None) if row else None
            if selected_candidate:
                result = {**result, "recommendation": selected_candidate}
        claim = self._identity_claim(parsed, result, override)
        with self._borrowed_write(connection) as write_connection:
            series_run_id = self._resolve_series_run(write_connection, claim, pinned_run_id)
            write_connection.execute(
                """INSERT INTO file_identities(
                       file_id, series_run_id, raw_title, normalized_title, identity_kind,
                       issue_number, volume_number, match_confidence, match_basis_json, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(file_id) DO UPDATE SET
                       series_run_id=excluded.series_run_id, raw_title=excluded.raw_title,
                       normalized_title=excluded.normalized_title, identity_kind=excluded.identity_kind,
                       issue_number=excluded.issue_number, volume_number=excluded.volume_number,
                       match_confidence=excluded.match_confidence,
                       match_basis_json=excluded.match_basis_json, updated_at=excluded.updated_at""",
                (
                    file_id, series_run_id, claim["raw_title"], claim["normalized_title"], claim["kind"],
                    claim["issue"], claim["volume"], claim["confidence"], _json(claim["basis"]), _utc_now(),
                ),
            )
        self._sync_file_catalog_entity(file_id, parsed, result, override, connection=connection)
        # Here rather than in the scan, so the start-up pass over every file
        # fills the index for a library scanned before it existed.
        self._sync_file_creators(file_id, result, connection=connection)

    def _heal_connected_placeholder_run(
        self,
        file_id: int,
        source_run_id: int | None,
        requested_series_title: str | None,
    ) -> dict[str, Any] | None:
        """Carry a manual run correction across safely connected local files.

        A filename-derived placeholder is one local identity, even when it owns
        several volume files. Correcting one member should therefore correct the
        run, not strand that file in a second run. Confirmed/provider-backed,
        followed, collected, or independently locked runs remain separate and
        require an explicit merge because those relationships may be meaningful.
        """
        requested_title = re.sub(r"\s+", " ", str(requested_series_title or "")).strip()
        if source_run_id is None or not requested_title:
            return None
        with self._write_lock, self._connect() as connection:
            target_identity = connection.execute(
                "SELECT series_run_id FROM file_identities WHERE file_id=?", (file_id,)
            ).fetchone()
            source = connection.execute(
                "SELECT * FROM series_runs WHERE id=?", (source_run_id,)
            ).fetchone()
            if not target_identity or not source:
                return None
            target_run_id = int(target_identity["series_run_id"])
            file_rows = list(connection.execute(
                """SELECT file_identities.file_id
                   FROM file_identities
                   JOIN files ON files.id=file_identities.file_id
                   WHERE file_identities.series_run_id=? AND files.present=1
                   ORDER BY file_identities.file_id""",
                (source_run_id,),
            ))
            connected_count = len(file_rows)
            if target_run_id == source_run_id:
                return {
                    "status": "healed",
                    "healedFileCount": connected_count,
                    "seriesId": str(target_run_id),
                    "title": requested_title,
                    "detail": "The corrected title applies to the connected run.",
                }
            original_connected_count = connected_count + 1
            if original_connected_count == 1:
                # Preserve the previous alias/run so Restore provider metadata can
                # put a single-file correction back exactly where it came from.
                return None

            blockers: list[str] = []
            if source["monitoring_status"] != "cataloged":
                blockers.append("the original run is being followed")
            if connection.execute(
                "SELECT 1 FROM series_provider_ids WHERE series_run_id=? AND confirmed=1 LIMIT 1",
                (source_run_id,),
            ).fetchone():
                blockers.append("the original run has a confirmed catalog identity")
            if connection.execute(
                "SELECT 1 FROM series_family_memberships WHERE series_run_id=? LIMIT 1",
                (source_run_id,),
            ).fetchone():
                blockers.append("the original run belongs to a collection")
            if connection.execute(
                "SELECT 1 FROM acquisition_requests WHERE series_run_id=? AND status='open' LIMIT 1",
                (source_run_id,),
            ).fetchone():
                blockers.append("the original run has an active request")
            if connection.execute(
                """SELECT 1
                   FROM issue_metadata_overrides
                   JOIN issues ON issues.id=issue_metadata_overrides.issue_id
                   WHERE issues.series_run_id=? LIMIT 1""",
                (source_run_id,),
            ).fetchone():
                blockers.append("the original run contains manually edited issue details")

            for row in connection.execute(
                """SELECT file_metadata_overrides.fields_json,
                          file_metadata_overrides.locked_fields_json
                   FROM file_metadata_overrides
                   JOIN file_identities
                     ON file_identities.file_id=file_metadata_overrides.file_id
                   WHERE file_identities.series_run_id=?
                     AND file_metadata_overrides.file_id<>?""",
                (source_run_id, file_id),
            ):
                locked = _load_json(row["locked_fields_json"], [])
                fields = _load_json(row["fields_json"], {})
                if (
                    "seriesTitle" in locked
                    and _normalized(fields.get("seriesTitle")) != _normalized(requested_title)
                ):
                    blockers.append("another connected file has its own locked series title")
                    break

            if blockers:
                return {
                    "status": "review_required",
                    "healedFileCount": 1,
                    "connectedFileCount": original_connected_count,
                    "seriesId": str(target_run_id),
                    "title": requested_title,
                    "detail": "; ".join(dict.fromkeys(blockers)),
                }

            sibling_ids = [
                int(row["file_id"]) for row in file_rows if int(row["file_id"]) != file_id
            ]
            now = _utc_now()
            for sibling_id in sibling_ids:
                connection.execute(
                    """INSERT INTO metadata_override_history(file_id, action, fields_json, created_at)
                       VALUES (?, 'series_heal', ?, ?)""",
                    (
                        sibling_id,
                        _json({
                            "seriesTitle": requested_title,
                            "sourceSeriesId": str(source_run_id),
                            "targetSeriesId": str(target_run_id),
                        }),
                        now,
                    ),
                )

        # merge_series owns its own transaction/lock. The guarded source run is
        # local-only, so moving its aliases, issues, editions, and file identities
        # is the durable equivalent of healing every connected file individually.
        self.merge_series(source_run_id, target_run_id)
        return {
            "status": "healed",
            "healedFileCount": original_connected_count,
            "seriesId": str(target_run_id),
            "title": requested_title,
            "detail": "Connected files were moved to the corrected run.",
        }

    def _sync_file_catalog_entity(
        self, file_id: int, parsed: dict[str, Any], result: dict[str, Any], override: dict[str, Any] | None = None,
        *, connection: sqlite3.Connection | None = None,
    ) -> None:
        override = override or {}
        # The same match the file's run was chosen by: a lookup the file's own
        # ComicInfo contradicts must not hand the right run another run's
        # issue title, dates and provider ids.
        recommendation = dict(_trusted_recommendation(result, override))
        embedded = result.get("embedded_metadata") or {}
        # The edition cover below prefers the cover inside the comic over the
        # provider's. A French edition would otherwise become the face of an
        # English run on the strength of being filed first.
        wrong_language = self._states_another_language(
            parsed.get("filename") or parsed.get("path"), embedded
        )
        if wrong_language:
            embedded = {}
        override_mapping = {
            "title": "title", "subtitle": "subtitle", "publisher": "publisher",
            "publicationYear": "publication_year", "format": "format",
            "issueNumber": "issue",
        }
        for override_key, recommendation_key in override_mapping.items():
            if override_key in override:
                recommendation[recommendation_key] = override[override_key]
        if "isbn" in override:
            recommendation["isbns"] = [override["isbn"]] if override["isbn"] else []
        if override.get("recordType"):
            recommendation["record_type"] = "single_issue" if override["recordType"] == "issue" else "collected_edition"
        # Rebind so the body below is unchanged whether the transaction is ours
        # or the caller's.
        with self._borrowed_write(connection) as connection:
            identity = connection.execute("SELECT * FROM file_identities WHERE file_id=?", (file_id,)).fetchone()
            if not identity:
                return
            series_run_id = int(identity["series_run_id"])
            now = _utc_now()
            if identity["identity_kind"] == "issue" and identity["issue_number"]:
                issue_number = str(identity["issue_number"])
                connection.execute(
                    """INSERT INTO issues(
                           series_run_id, issue_number, title, publication_year,
                           publication_date, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(series_run_id, issue_number) DO UPDATE SET
                           title=COALESCE(excluded.title, issues.title),
                           publication_year=COALESCE(excluded.publication_year, issues.publication_year),
                           publication_date=COALESCE(excluded.publication_date, issues.publication_date),
                           updated_at=excluded.updated_at""",
                    (
                        series_run_id, issue_number,
                        recommendation.get("subtitle") or recommendation.get("story_title")
                        or next(iter(recommendation.get("named_contents") or []), None),
                        recommendation.get("publication_year") or parsed.get("year"),
                        recommendation.get("publication_date"), now, now,
                    ),
                )
                issue_id = int(connection.execute(
                    "SELECT id FROM issues WHERE series_run_id=? AND issue_number=?", (series_run_id, issue_number)
                ).fetchone()["id"])
                explicit_match = bool(connection.execute(
                    """SELECT 1 FROM metadata_override_history
                       WHERE file_id=? AND action='fix_match' LIMIT 1""",
                    (file_id,),
                ).fetchone())
                provider_identity = _candidate_issue_provider_identity(
                    recommendation, allow_embedded_url=explicit_match
                )
                if provider_identity:
                    provider = str(provider_identity["provider"])
                    provider_id = str(provider_identity["providerId"])
                    self._upsert_issue_provider_id(
                        connection, issue_id, provider, provider_id,
                        provider_identity.get("apiUrl"), now,
                    )
                    provider_series_id = provider_identity.get("providerSeriesId")
                    if provider_series_id:
                        owner = connection.execute(
                            """SELECT series_run_id FROM series_provider_ids
                               WHERE provider=? AND provider_id=?""",
                            (provider, provider_series_id),
                        ).fetchone()
                        if owner is None or int(owner["series_run_id"]) == series_run_id:
                            connection.execute(
                                """INSERT INTO series_provider_ids(
                                       series_run_id, provider, provider_id, api_url,
                                       confirmed, source, updated_at
                                   ) VALUES (?, ?, ?, ?, 1, ?, ?)
                                   ON CONFLICT(series_run_id, provider) DO UPDATE SET
                                       provider_id=excluded.provider_id,
                                       api_url=COALESCE(excluded.api_url, series_provider_ids.api_url),
                                       confirmed=1, source=excluded.source,
                                       updated_at=excluded.updated_at""",
                                (
                                    series_run_id, provider, provider_series_id,
                                    provider_identity.get("apiUrl"),
                                    "confirmed through explicit issue match", now,
                                ),
                            )
                previous_edition = connection.execute(
                    "SELECT edition_id FROM file_edition_links WHERE file_id=?", (file_id,)
                ).fetchone()
                connection.execute("DELETE FROM file_edition_links WHERE file_id=?", (file_id,))
                if previous_edition:
                    connection.execute(
                        """DELETE FROM editions WHERE id=? AND NOT EXISTS(
                               SELECT 1 FROM file_edition_links WHERE edition_id=?
                           )""",
                        (previous_edition["edition_id"], previous_edition["edition_id"]),
                    )
                connection.execute(
                    """INSERT INTO file_issue_links(file_id, issue_id) VALUES (?, ?)
                       ON CONFLICT(file_id) DO UPDATE SET issue_id=excluded.issue_id""",
                    (file_id, issue_id),
                )
                return

            isbns = [re.sub(r"[^0-9Xx]", "", str(value)).upper() for value in recommendation.get("isbns", []) if value]
            source = str(recommendation.get("source") or embedded.get("source") or "Local metadata")
            source_id = recommendation.get("source_id")
            if isbns:
                edition_key = "isbn:" + sorted(isbns, key=lambda value: (-len(value), value))[0]
            elif source_id:
                edition_key = f"source:{_normalized(source)}:{source_id}"
            else:
                path = connection.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()["path"]
                edition_key = "file:" + hashlib.sha1(path.encode()).hexdigest()
            title = recommendation.get("title") or embedded.get("title") or identity["raw_title"]
            subtitle = recommendation.get("subtitle")
            edition_kind = _edition_kind(parsed, result, identity["volume_number"], override.get("editionKind"))
            connection.execute(
                """INSERT INTO editions(
                       series_run_id, edition_key, title, subtitle, volume_number, publication_year,
                       publisher, format, edition_kind, isbns_json, cover, source, source_id,
                       verification_status, coverage_status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(edition_key) DO UPDATE SET
                       series_run_id=excluded.series_run_id, title=excluded.title,
                       subtitle=COALESCE(excluded.subtitle, editions.subtitle),
                       volume_number=COALESCE(excluded.volume_number, editions.volume_number),
                       publication_year=COALESCE(excluded.publication_year, editions.publication_year),
                       publisher=COALESCE(excluded.publisher, editions.publisher),
                       format=COALESCE(excluded.format, editions.format),
                       edition_kind=excluded.edition_kind,
                       isbns_json=excluded.isbns_json,
                       cover=COALESCE(excluded.cover, editions.cover), source=excluded.source,
                       source_id=COALESCE(excluded.source_id, editions.source_id),
                       verification_status=COALESCE(excluded.verification_status, editions.verification_status),
                       coverage_status=COALESCE(excluded.coverage_status, editions.coverage_status),
                       updated_at=excluded.updated_at""",
                (
                    series_run_id, edition_key, title, subtitle, identity["volume_number"],
                    recommendation.get("publication_year"),
                    recommendation.get("publisher") or embedded.get("publisher"),
                    recommendation.get("format") or embedded.get("format") or parsed.get("extension", "").lstrip(".").upper(),
                    edition_kind, _json(isbns),
                    recommendation.get("cover") if wrong_language
                    else (result.get("file_cover") or {}).get("url") or recommendation.get("cover"),
                    source, str(source_id) if source_id else None, recommendation.get("verification_status"),
                    recommendation.get("coverage_status"), now, now,
                ),
            )
            edition_id = int(connection.execute(
                "SELECT id FROM editions WHERE edition_key=?", (edition_key,)
            ).fetchone()["id"])
            previous_edition = connection.execute(
                "SELECT edition_id FROM file_edition_links WHERE file_id=?", (file_id,)
            ).fetchone()
            connection.execute("DELETE FROM file_issue_links WHERE file_id=?", (file_id,))
            connection.execute(
                """INSERT INTO file_edition_links(file_id, edition_id) VALUES (?, ?)
                   ON CONFLICT(file_id) DO UPDATE SET edition_id=excluded.edition_id""",
                (file_id, edition_id),
            )
            if previous_edition and int(previous_edition["edition_id"]) != edition_id:
                connection.execute(
                    """DELETE FROM editions WHERE id=? AND NOT EXISTS(
                           SELECT 1 FROM file_edition_links WHERE edition_id=?
                       )""",
                    (previous_edition["edition_id"], previous_edition["edition_id"]),
                )
            connection.execute("DELETE FROM edition_coverage_claims WHERE edition_id=?", (edition_id,))
            coverage = (recommendation.get("matched_edition") or {}).get("coverage") or recommendation.get("coverage") or []
            aliases = {
                row["normalized_alias"]
                for row in connection.execute("SELECT normalized_alias FROM series_aliases WHERE series_run_id=?", (series_run_id,))
            }
            for claim in coverage:
                claimed_series = str(claim.get("series") or "").strip()
                if _normalized(claimed_series) in {"issue", "issues"}:
                    claimed_series = ""
                series_label = claimed_series or title
                resolved = _normalized(series_label) in aliases
                confidence = str(claim.get("confidence") or ("explicit" if claim.get("source") else "unverified"))
                claim_source = str(claim.get("source") or source)
                relation_kind = _coverage_relation_kind(claim)
                for issue_number in claim.get("issues") or []:
                    issue_id = None
                    if resolved:
                        connection.execute(
                            """INSERT INTO issues(series_run_id, issue_number, created_at, updated_at)
                               VALUES (?, ?, ?, ?)
                               ON CONFLICT(series_run_id, issue_number) DO UPDATE SET updated_at=excluded.updated_at""",
                            (series_run_id, str(issue_number), now, now),
                        )
                        issue_id = int(connection.execute(
                            "SELECT id FROM issues WHERE series_run_id=? AND issue_number=?",
                            (series_run_id, str(issue_number)),
                        ).fetchone()["id"])
                    connection.execute(
                        """INSERT INTO edition_coverage_claims(
                               edition_id, issue_id, series_label, issue_number, source,
                               confidence, resolution_status, relation_kind, evidence, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            edition_id, issue_id, series_label, str(issue_number), claim_source,
                            confidence, "resolved" if resolved else "unresolved",
                            relation_kind, claim.get("source_text"), now,
                        ),
                    )

    @staticmethod
    def _candidate_cover_urls(result: dict[str, Any]) -> list[str]:
        """Provider cover urls, in the order the candidates were ranked.

        _candidate_payloads sha1s every candidate to key it. Reading covers
        does not need those keys, and the whole-library pass was paying that
        hash for every candidate of every file to throw it away.
        """
        urls: list[str] = []
        recommendation = result.get("recommendation")
        pools = ([recommendation] if recommendation else []) + [
            candidate
            for source_candidates in (result.get("candidates") or {}).values()
            for candidate in (source_candidates or [])
        ]
        for candidate in pools:
            if not isinstance(candidate, dict):
                continue
            cover = candidate.get("cover")
            if cover and cover not in urls:
                urls.append(cover)
        return urls

    @staticmethod
    def _resolved_file_cover(
        file_id: Any, result: dict[str, Any],
        cover_source: str | None, preferred_cover_url: str | None,
    ) -> str | None:
        """The cover this file should show, honouring what the user chose.

        One rule for the library, the issue rows and the run picker, so a
        cover chosen in one place cannot fail to appear in another.
        """
        embedded = (result.get("file_cover") or {}).get("url")
        if cover_source == "upload":
            return f"/api/v1/files/{file_id}/cover/image"
        if cover_source == "provider" and preferred_cover_url:
            return preferred_cover_url
        if cover_source == "file" and embedded:
            return embedded
        recommendation = result.get("recommendation") or {}
        return (
            embedded or recommendation.get("cover")
            or next(iter(CatalogStore._candidate_cover_urls(result)), None)
        )

    def _candidate_payloads(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        recommendation = result.get("recommendation")
        pools = ([recommendation] if recommendation else []) + [
            candidate
            for source_candidates in (result.get("candidates") or {}).values()
            for candidate in (source_candidates or [])
        ]
        for candidate in pools:
            if not isinstance(candidate, dict):
                continue
            key = hashlib.sha1(_json(candidate).encode()).hexdigest()[:16]
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"key": key, **candidate})
        return candidates

    def cataloged_file_path(self, file_path: str) -> Path:
        """Return a present library file after validating it against the catalog."""
        requested = str(file_path or "").strip()
        if not requested:
            raise ValueError("A comic file path is required")
        try:
            resolved = Path(requested).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError("Comic file could not be found") from exc
        if not resolved.is_file():
            raise ValueError("Comic file could not be found")
        with self._connect() as connection:
            rows = connection.execute("SELECT path FROM files WHERE present=1").fetchall()
        for row in rows:
            try:
                if Path(row["path"]).expanduser().resolve(strict=True) == resolved:
                    return resolved
            except OSError:
                continue
        raise ValueError("Comic file is not part of the current library")

    def get_file_workbench(self, file_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT files.*, file_identities.*, series_runs.canonical_title,
                          editions.title AS edition_title, editions.subtitle AS edition_subtitle,
                          editions.publisher AS edition_publisher,
                          editions.publication_year AS edition_publication_year,
                          editions.format AS edition_format, editions.edition_kind,
                          editions.id AS edition_id, editions.isbns_json,
                          file_metadata_overrides.fields_json,
                          file_metadata_overrides.locked_fields_json,
                          file_metadata_overrides.match_source,
                          file_metadata_overrides.match_source_id,
                          file_metadata_overrides.match_candidate_json,
                          file_cover_preferences.source AS cover_source,
                          file_cover_preferences.cover_url AS preferred_cover_url
                   FROM files
                   LEFT JOIN file_identities ON file_identities.file_id=files.id
                   LEFT JOIN series_runs ON series_runs.id=file_identities.series_run_id
                   LEFT JOIN file_edition_links ON file_edition_links.file_id=files.id
                   LEFT JOIN editions ON editions.id=file_edition_links.edition_id
                   LEFT JOIN file_metadata_overrides ON file_metadata_overrides.file_id=files.id
                   LEFT JOIN file_cover_preferences ON file_cover_preferences.file_id=files.id
                   WHERE files.id=? AND files.present=1""",
                (file_id,),
            ).fetchone()
            if not row:
                raise ValueError("Library file was not found")
            collection_contents = self._collection_contents_payload(
                connection, int(row["edition_id"])
            ) if row["edition_id"] is not None else None
            series_options = [
                {
                    "id": str(series["id"]), "title": series["canonical_title"],
                    "year": series["start_year"], "publisher": series["publisher"],
                }
                for series in connection.execute(
                    "SELECT id, canonical_title, start_year, publisher FROM series_runs ORDER BY canonical_title COLLATE NOCASE"
                )
            ]
        result = _load_json(row["result_json"], {})
        recommendation = result.get("recommendation") or {}
        isbns = _load_json(row["isbns_json"], [])
        current = {
            "seriesTitle": row["canonical_title"],
            "title": row["edition_title"] or recommendation.get("title") or row["raw_title"],
            "subtitle": row["edition_subtitle"] or recommendation.get("subtitle"),
            "recordType": row["identity_kind"],
            "issueNumber": row["issue_number"],
            "volumeNumber": row["volume_number"],
            "editionKind": row["edition_kind"],
            "publisher": row["edition_publisher"] or recommendation.get("publisher"),
            "publicationYear": row["edition_publication_year"] or recommendation.get("publication_year"),
            "isbn": isbns[0] if isbns else next(iter(recommendation.get("isbns") or []), None),
            "format": row["edition_format"] or recommendation.get("format"),
        }
        candidates = self._candidate_payloads(result)
        selected_candidate = _load_json(row["match_candidate_json"], None)
        if selected_candidate:
            selected_key = selected_candidate.get("key") or hashlib.sha1(
                _json({key: value for key, value in selected_candidate.items() if key != "key"}).encode()
            ).hexdigest()[:16]
            selected_candidate = {"key": selected_key, **selected_candidate}
            if not any(candidate["key"] == selected_key for candidate in candidates):
                candidates.insert(0, selected_candidate)
        else:
            selected_key = None
        candidates = [
            {**candidate, "selectionPreview": self._candidate_selection_preview(candidate, current)}
            for candidate in candidates
        ]
        cover_options: list[dict[str, Any]] = []
        local_cover = result.get("file_cover") or {}
        if local_cover.get("url"):
            cover_options.append({
                "source": "file", "url": local_cover["url"],
                "label": "Embedded comic cover", "detail": local_cover.get("source") or "Comic file",
            })
        seen_covers = {option["url"] for option in cover_options}
        for candidate in candidates:
            cover_url = candidate.get("cover")
            if not cover_url or cover_url in seen_covers:
                continue
            seen_covers.add(cover_url)
            cover_options.append({
                "source": "provider", "url": cover_url,
                "label": candidate.get("subtitle") or candidate.get("title") or "Catalog cover",
                "detail": candidate.get("source") or "Metadata provider",
            })
        if row["cover_source"] == "upload":
            cover_options.insert(0, {
                "source": "upload", "url": f"/api/v1/files/{file_id}/cover/image",
                "label": "Uploaded cover", "detail": "Local preference",
            })
        return {
            "file": {"id": str(file_id), "filename": row["filename"], "path": row["path"]},
            "current": current,
            "override": _load_json(row["fields_json"], {}),
            "lockedFields": _load_json(row["locked_fields_json"], []),
            "matchSource": row["match_source"], "matchSourceId": row["match_source_id"],
            "selectedCandidateKey": selected_key,
            "parsed": _load_json(row["parsed_json"], {}),
            "embedded": result.get("embedded_metadata") or {},
            "candidates": candidates,
            "suggestedSearchQuery": self._suggested_file_search_query(
                current, _load_json(row["fields_json"], {})
            ),
            "candidateSearch": result.get("manual_candidate_search") or {},
            "covers": {
                "selectedSource": row["cover_source"] or "auto",
                "selectedUrl": row["preferred_cover_url"],
                "options": cover_options,
            },
            "collectionContents": collection_contents,
            "seriesOptions": series_options,
        }

    @staticmethod
    def _suggested_file_search_query(
        current: dict[str, Any], overrides: dict[str, Any]
    ) -> str:
        """Prefer the collector's latest correction when proposing a new match search."""
        title = (
            overrides.get("seriesTitle") or overrides.get("title")
            or current.get("seriesTitle") or current.get("title") or ""
        )
        title = re.sub(r"\s+", " ", str(title)).strip()
        year = overrides.get("publicationYear") or current.get("publicationYear")
        if year and str(year) not in title:
            return f"{title} {year}".strip()
        return title

    def retain_file_search_candidates(
        self,
        file_id: int,
        query: str,
        candidates: list[dict[str, Any]],
        providers_checked: list[str] | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Retain explicit Fix Match results so selection remains server-authoritative."""
        cleaned_query = re.sub(r"\s+", " ", str(query or "")).strip()
        if len(cleaned_query) < 2:
            raise ValueError("Enter at least two characters to search for a match")
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM files WHERE id=? AND present=1", (file_id,)
            ).fetchone()
            if not row:
                raise ValueError("Library file was not found")
            result = _load_json(row["result_json"], {})
            pools = dict(result.get("candidates") or {})
            pools["manual_search"] = [
                candidate for candidate in candidates if isinstance(candidate, dict)
            ]
            result["candidates"] = pools
            result["manual_candidate_search"] = {
                "query": cleaned_query,
                "resultCount": len(pools["manual_search"]),
                "providersChecked": providers_checked or [],
                "errors": errors or [],
                "searchedAt": _utc_now(),
            }
            connection.execute(
                "UPDATE files SET result_json=? WHERE id=?", (_json(result), file_id)
            )
        return self.get_file_workbench(file_id)

    def _candidate_selection_fields(
        self, candidate: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        is_issue = candidate.get("record_type") == "single_issue" or bool(candidate.get("issue"))
        return {
            "seriesTitle": candidate.get("title") or current.get("seriesTitle"),
            "title": candidate.get("title") or current.get("title"),
            "subtitle": candidate.get("subtitle"),
            "recordType": "issue" if is_issue else "edition",
            "issueNumber": str(candidate.get("issue")) if candidate.get("issue") is not None else None,
            "volumeNumber": candidate.get("volume") if candidate.get("volume") is not None else current.get("volumeNumber"),
            "publisher": candidate.get("publisher") or current.get("publisher"),
            "publicationYear": candidate.get("publication_year") or current.get("publicationYear"),
            "isbn": next(iter(candidate.get("isbns") or []), None) or current.get("isbn"),
            "format": candidate.get("format") or current.get("format"),
            "editionKind": current.get("editionKind"),
        }

    def _candidate_selection_preview(
        self, candidate: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        fields = self._candidate_selection_fields(candidate, current)
        labels = {
            "seriesTitle": "Series", "title": "Title", "subtitle": "Subtitle",
            "recordType": "Record type", "issueNumber": "Issue", "volumeNumber": "Volume",
            "publisher": "Publisher", "publicationYear": "Year", "isbn": "ISBN",
            "format": "Format", "editionKind": "Edition type",
        }
        changes = [
            {"field": key, "label": labels[key], "from": current.get(key), "to": value}
            for key, value in fields.items()
            if (
                str(current.get(key)) != str(value)
                if key in {"volumeNumber", "publicationYear"} and current.get(key) is not None and value is not None
                else current.get(key) != value
            )
        ]
        coverage = (candidate.get("matched_edition") or {}).get("coverage") or candidate.get("coverage") or []
        associations = []
        if fields["recordType"] == "issue" and fields["issueNumber"]:
            associations.append({
                "series": fields["seriesTitle"], "issues": [fields["issueNumber"]],
                "issueLabel": fields["issueNumber"], "source": candidate.get("source"),
                "confidence": "direct issue match",
            })
        for claim in coverage:
            issues = [str(value) for value in claim.get("issues") or []]
            if not issues:
                continue
            association_series = str(claim.get("series") or "").strip()
            if _normalized(association_series) in {"issue", "issues"}:
                association_series = ""
            associations.append({
                "series": association_series or fields["seriesTitle"], "issues": issues,
                "issueLabel": issues[0] if len(issues) == 1 else f"{issues[0]}–{issues[-1]}",
                "source": claim.get("source") or candidate.get("source"),
                "confidence": claim.get("confidence") or "explicit",
            })
        return {
            "fields": fields, "changes": changes, "associations": associations,
            "hasCover": bool(candidate.get("cover")),
            "coverEffect": "Available in the Cover picker" if candidate.get("cover") else "No provider cover supplied",
        }

    def _collection_contents_payload(
        self, connection: sqlite3.Connection, edition_id: int
    ) -> dict[str, Any]:
        edition = connection.execute(
            "SELECT id, title, subtitle, series_run_id FROM editions WHERE id=?", (edition_id,)
        ).fetchone()
        if not edition:
            raise ValueError("Volume was not found")
        overrides = {
            (int(row["series_run_id"]), str(row["issue_number"])): row
            for row in connection.execute(
                "SELECT * FROM edition_coverage_overrides WHERE edition_id=?", (edition_id,)
            )
        }
        contents: list[dict[str, Any]] = []
        provider_keys: set[tuple[int, str]] = set()
        for claim in connection.execute(
            """SELECT edition_coverage_claims.*, issues.series_run_id,
                      series_runs.canonical_title
               FROM edition_coverage_claims
               LEFT JOIN issues ON issues.id=edition_coverage_claims.issue_id
               LEFT JOIN series_runs ON series_runs.id=issues.series_run_id
               WHERE edition_coverage_claims.edition_id=?
               ORDER BY series_label COLLATE NOCASE, issue_number""",
            (edition_id,),
        ):
            series_run_id = int(claim["series_run_id"]) if claim["series_run_id"] is not None else None
            key = (series_run_id, str(claim["issue_number"])) if series_run_id is not None else None
            override = overrides.get(key) if key else None
            if key:
                provider_keys.add(key)
            contents.append({
                "seriesId": str(series_run_id) if series_run_id is not None else None,
                "seriesLabel": claim["canonical_title"] or claim["series_label"],
                "issueNumber": str(claim["issue_number"]),
                "source": claim["source"], "confidence": claim["confidence"],
                "resolved": claim["resolution_status"] == "resolved",
                "relationKind": claim["relation_kind"],
                "evidence": claim["evidence"],
                "included": not override or override["action"] != "remove",
                "manual": False,
                "overrideNote": override["note"] if override else None,
            })
        for key, override in overrides.items():
            if key in provider_keys:
                continue
            series = connection.execute(
                "SELECT canonical_title FROM series_runs WHERE id=?", (key[0],)
            ).fetchone()
            contents.append({
                "seriesId": str(key[0]),
                "seriesLabel": series["canonical_title"] if series else "Unknown series",
                "issueNumber": key[1], "source": "Manual correction",
                "confidence": "manual", "resolved": True,
                "relationKind": "full_issue",
                "evidence": override["note"], "included": override["action"] == "add",
                "manual": True, "overrideNote": override["note"],
            })
        contents.sort(key=lambda item: (_normalized(item["seriesLabel"]), _natural_issue_key(item["issueNumber"])))
        return {
            "editionId": str(edition_id),
            "title": edition["subtitle"] or edition["title"],
            "seriesId": str(edition["series_run_id"]),
            "items": contents,
            "hasOverrides": bool(overrides),
            "includedCount": sum(
                1 for item in contents
                if item["included"] and item["resolved"]
                and item.get("relationKind") == "full_issue"
            ),
        }

    def set_file_collection_contents(
        self,
        file_id: int,
        series_run_id: int,
        issue_numbers: list[Any],
        included: bool,
        note: str | None = None,
    ) -> dict[str, Any]:
        cleaned_numbers = list(dict.fromkeys(
            str(value).strip() for value in issue_numbers if str(value).strip()
        ))
        if not cleaned_numbers:
            raise ValueError("At least one issue number is required")
        if len(cleaned_numbers) > 500:
            raise ValueError("No more than 500 issues can be changed at once")
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                """SELECT file_edition_links.edition_id
                   FROM files JOIN file_edition_links ON file_edition_links.file_id=files.id
                   WHERE files.id=? AND files.present=1""",
                (file_id,),
            ).fetchone()
            if not row:
                raise ValueError("This file is not linked to a volume")
            edition_id = int(row["edition_id"])
            series = connection.execute(
                "SELECT id FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone()
            if not series:
                raise ValueError("Canonical series was not found")
            now = _utc_now()
            for issue_number in cleaned_numbers:
                provider_claim = connection.execute(
                    """SELECT 1 FROM edition_coverage_claims
                       JOIN issues ON issues.id=edition_coverage_claims.issue_id
                       WHERE edition_coverage_claims.edition_id=?
                         AND edition_coverage_claims.relation_kind='full_issue'
                         AND issues.series_run_id=? AND issues.issue_number=?""",
                    (edition_id, series_run_id, issue_number),
                ).fetchone()
                if included and provider_claim:
                    connection.execute(
                        "DELETE FROM edition_coverage_overrides WHERE edition_id=? AND series_run_id=? AND issue_number=?",
                        (edition_id, series_run_id, issue_number),
                    )
                    continue
                action = "add" if included else "remove"
                connection.execute(
                    """INSERT INTO edition_coverage_overrides(
                           edition_id, series_run_id, issue_number, action, note, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(edition_id, series_run_id, issue_number) DO UPDATE SET
                           action=excluded.action, note=excluded.note, updated_at=excluded.updated_at""",
                    (edition_id, series_run_id, issue_number, action, note, now, now),
                )
                if included:
                    connection.execute(
                        """INSERT INTO issues(series_run_id, issue_number, created_at, updated_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(series_run_id, issue_number) DO UPDATE SET updated_at=excluded.updated_at""",
                        (series_run_id, issue_number, now, now),
                    )
        return self.get_file_workbench(file_id)

    def reset_file_collection_contents(self, file_id: int) -> dict[str, Any]:
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT edition_id FROM file_edition_links WHERE file_id=?", (file_id,)
            ).fetchone()
            if not row:
                raise ValueError("This file is not linked to a volume")
            connection.execute(
                "DELETE FROM edition_coverage_overrides WHERE edition_id=?", (row["edition_id"],)
            )
        return self.get_file_workbench(file_id)

    def get_series_cover_workbench(self, series_run_id: int) -> dict[str, Any]:
        """Every cover this run could wear, and which one it wears now.

        Deliberately not get_file_workbench in a loop: that does a wide
        multi-join, hashes every candidate and resolves collection contents
        per call, which for a 161-issue run is seconds of work to produce a
        list of image urls.
        """
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone()
            if run is None:
                raise LookupError("That series run is not in the catalog")
            preference = connection.execute(
                "SELECT * FROM series_cover_preferences WHERE series_run_id=?",
                (series_run_id,),
            ).fetchone()
            file_rows = list(connection.execute(
                """SELECT files.id, files.filename, files.result_json,
                          file_identities.identity_kind, file_identities.issue_number,
                          file_cover_preferences.source AS cover_source,
                          file_cover_preferences.cover_url AS preferred_cover_url
                   FROM files
                   JOIN file_identities ON file_identities.file_id=files.id
                   LEFT JOIN file_cover_preferences ON file_cover_preferences.file_id=files.id
                   WHERE file_identities.series_run_id=? AND files.present=1
                   ORDER BY files.filename COLLATE NOCASE""",
                (series_run_id,),
            ))
            issue_rows = list(connection.execute(
                """SELECT issue_number, cover FROM issues
                   WHERE series_run_id=? AND cover IS NOT NULL
                   ORDER BY id""",
                (series_run_id,),
            ))

        options: list[dict[str, Any]] = []
        selected_source = (preference["source"] if preference else None) or "auto"
        if selected_source == "upload":
            options.append({
                "optionId": "upload", "source": "upload",
                "url": f"/api/v1/series/{series_run_id}/cover/image",
                "label": "Uploaded cover", "detail": "Local preference",
            })
        seen: set[str] = {option["url"] for option in options}
        provider_urls: list[tuple[str, str]] = []
        for row in file_rows:
            result = _load_json(row["result_json"], {})
            url = self._resolved_file_cover(
                row["id"], result, row["cover_source"], row["preferred_cover_url"],
            )
            embedded = (result.get("file_cover") or {}).get("url")
            language = normalize_language(
                (result.get("embedded_metadata") or {}).get("language")
            ) or detect_language(row["filename"])
            detail = row["filename"]
            # A wrong-language cover is offered rather than hidden, but it says
            # so: the choice is the user's to make knowingly.
            if language and language != str(self.preferred_language or "").strip().casefold():
                detail = f"{detail} · states {language_name(language)}"
            if url and url not in seen:
                seen.add(url)
                label = (
                    f"Issue #{row['issue_number']}"
                    if row["identity_kind"] == "issue" and row["issue_number"]
                    else Path(row["filename"]).stem
                )
                options.append({
                    "optionId": f"file:{row['id']}", "source": "file", "url": url,
                    "label": label, "detail": detail, "fileId": str(row["id"]),
                })
            if embedded and url != embedded:
                seen.add(embedded)
            for candidate in self._candidate_cover_urls(result):
                provider_urls.append((candidate, row["issue_number"] or ""))
        for url, issue_number in provider_urls:
            if url in seen:
                continue
            seen.add(url)
            options.append({
                "optionId": f"provider:{url}", "source": "provider", "url": url,
                "label": f"Issue #{issue_number} cover" if issue_number else "Catalog cover",
                "detail": "Metadata provider",
            })
        for row in issue_rows:
            if row["cover"] in seen:
                continue
            seen.add(row["cover"])
            options.append({
                "optionId": f"provider:{row['cover']}", "source": "provider",
                "url": row["cover"],
                "label": f"Issue #{row['issue_number']} cover",
                "detail": "Metadata provider",
            })
        selected_option_id = None
        if selected_source == "upload":
            selected_option_id = "upload"
        elif selected_source == "file" and preference["file_id"] is not None:
            selected_option_id = f"file:{preference['file_id']}"
        elif selected_source == "provider" and preference["cover_url"]:
            selected_option_id = f"provider:{preference['cover_url']}"
        return {
            "series": {
                "id": str(series_run_id), "title": run["canonical_title"],
                "year": str(run["start_year"] or "Unknown"),
                "publisher": run["publisher"] or "Publisher unknown",
            },
            "covers": {
                "selectedSource": selected_source,
                "selectedUrl": preference["cover_url"] if preference else None,
                "selectedOptionId": selected_option_id,
                "options": options,
            },
        }

    def set_series_cover_preference(
        self, series_run_id: int, source: str,
        cover_url: str | None = None, file_id: int | None = None,
    ) -> dict[str, Any]:
        if source not in {"auto", "file", "provider", "upload"}:
            raise ValueError("Unsupported cover source")
        workbench = self.get_series_cover_workbench(series_run_id)
        options = workbench["covers"]["options"]
        if source == "file":
            # Only a file in this run, so a chosen id cannot reach across runs.
            if not any(
                option["source"] == "file" and option.get("fileId") == str(file_id)
                for option in options
            ):
                raise ValueError("That comic is not part of this run")
            cover_url = None
        elif source == "provider":
            if not any(
                option["source"] == "provider" and option["url"] == cover_url
                for option in options
            ):
                raise ValueError("The selected cover is not part of this run's metadata evidence")
            file_id = None
        elif source == "upload":
            cover_url = f"/api/v1/series/{series_run_id}/cover/image"
            file_id = None
        else:
            cover_url, file_id = None, None
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO series_cover_preferences(
                       series_run_id, source, cover_url, file_id, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(series_run_id) DO UPDATE SET
                       source=excluded.source, cover_url=excluded.cover_url,
                       file_id=excluded.file_id, updated_at=excluded.updated_at""",
                (series_run_id, source, cover_url, file_id, _utc_now()),
            )
        return self.get_series_cover_workbench(series_run_id)

    def library_file_path(self, file_id: int) -> Path:
        """A present catalog file's path.

        Page images are served by file id through this rather than by a path
        in the request, so a request can only reach comics the library holds.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path FROM files WHERE id=? AND present=1", (file_id,)
            ).fetchone()
        if row is None:
            raise LookupError("That comic is not in the library")
        return Path(row["path"])

    def series_backdrop_files(self, series_run_id: int) -> list[dict[str, Any]]:
        """A run's present comics, lowest issue first.

        That is the order an automatic header background looks through, so
        issue #1 lends its page before anything later in the run.
        """
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone() is None:
                raise LookupError("That series run is not in the catalog")
            rows = list(connection.execute(
                """SELECT files.id, files.path, files.filename, file_identities.issue_number
                   FROM files
                   JOIN file_identities ON file_identities.file_id=files.id
                   WHERE file_identities.series_run_id=? AND files.present=1""",
                (series_run_id,),
            ))

        def order(row: sqlite3.Row) -> tuple[float, str]:
            try:
                number = float(str(row["issue_number"]))
            except (TypeError, ValueError):
                number = float("inf")
            return (number, str(row["filename"]).casefold())

        return [
            {"id": str(row["id"]), "path": row["path"], "filename": row["filename"],
             "issueNumber": row["issue_number"]}
            for row in sorted(rows, key=order)
        ]

    def series_backdrop_preference(self, series_run_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM series_backdrop_preferences WHERE series_run_id=?",
                (series_run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "fileId": str(row["file_id"]), "member": row["page_member"],
            "source": row["source"], "fileSignature": row["file_signature"],
        }

    def set_series_backdrop(
        self, series_run_id: int, file_id: int, member: str, source: str,
        file_signature: str | None = None,
    ) -> None:
        if source not in {"chosen", "auto"}:
            raise ValueError("Unsupported background source")
        if not member:
            raise ValueError("Choose a page")
        # Only a comic in this run, so a chosen id cannot reach across runs.
        if not any(item["id"] == str(file_id) for item in self.series_backdrop_files(series_run_id)):
            raise ValueError("That comic is not part of this run")
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO series_backdrop_preferences(
                       series_run_id, file_id, page_member, source, file_signature, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(series_run_id) DO UPDATE SET
                       file_id=excluded.file_id, page_member=excluded.page_member,
                       source=excluded.source, file_signature=excluded.file_signature,
                       updated_at=excluded.updated_at""",
                (series_run_id, int(file_id), member, source, file_signature, _utc_now()),
            )

    def reading_progress(self, file_id: int, *, user_id: int) -> dict[str, Any] | None:
        """Where this reader left this comic, if it is still the comic it was."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reading_progress WHERE user_id=? AND file_id=?", (int(user_id), int(file_id)),
            ).fetchone()
        if row is None:
            return None
        return {
            "fileId": str(row["file_id"]), "page": int(row["page"]), "panel": int(row["panel"] or 0),
            "pageCount": int(row["page_count"]), "fileSignature": row["file_signature"],
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "updatedAt": row["updated_at"],
        }

    def set_reading_progress(
        self, file_id: int, page: int, page_count: int, file_signature: str,
        finished: bool = False, panel: int = 0, *, user_id: int,
    ) -> dict[str, Any]:
        """Remember the page (and the panel on it), and when a comic was finished.

        Two things write this -- turning a page, and closing the reader -- and
        the last write wins. That is correct here: both carry the page the
        reader was on, and a page number is not a value two writers can
        disagree about the way a counter would be.
        """
        if page < 0 or page_count < 0 or page > max(0, page_count - 1):
            raise ValueError("That page is not in this comic")
        if panel < 0:
            raise ValueError("A panel is counted from the first")
        now = _utc_now()
        finished_at = now if finished else None
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO reading_progress(
                       user_id, file_id, page, panel, page_count, file_signature, started_at, finished_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, file_id) DO UPDATE SET
                       page=excluded.page, panel=excluded.panel, page_count=excluded.page_count,
                       file_signature=excluded.file_signature,
                       -- Going back into a comic you finished makes it unfinished again.
                       finished_at=excluded.finished_at,
                       updated_at=excluded.updated_at""",
                (int(user_id), int(file_id), int(page), int(panel), int(page_count), file_signature,
                 now, finished_at, now),
            )
        return self.reading_progress(file_id, user_id=user_id)

    def clear_reading_progress(self, file_id: int, *, user_id: int) -> None:
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM reading_progress WHERE user_id=? AND file_id=?", (int(user_id), int(file_id)),
            )

    def recent_reading(self, limit: int = 24, *, user_id: int) -> list[dict[str, Any]]:
        """What was read lately, newest first, with what it belongs to.

        Only comics the library still holds: a file that has gone is not
        something to offer to continue.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT reading_progress.*, files.filename, files.path,
                          file_identities.issue_number AS issue_number,
                          series_runs.id AS series_run_id, series_runs.canonical_title AS series_title,
                          series_runs.format AS medium
                   FROM reading_progress
                   JOIN files ON files.id=reading_progress.file_id AND files.present=1
                   LEFT JOIN file_identities ON file_identities.file_id=files.id
                   LEFT JOIN series_runs ON series_runs.id=file_identities.series_run_id
                   WHERE reading_progress.user_id=?
                   ORDER BY reading_progress.updated_at DESC
                   LIMIT ?""",
                (int(user_id), int(limit)),
            ).fetchall()
        return [
            {
                "fileId": str(row["file_id"]), "filename": row["filename"],
                "issueNumber": row["issue_number"],
                "seriesRunId": str(row["series_run_id"]) if row["series_run_id"] else None,
                "seriesTitle": row["series_title"], "medium": row["medium"],
                "page": int(row["page"]), "pageCount": int(row["page_count"]),
                "finishedAt": row["finished_at"], "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def run_reading_files(self, series_run_id: int) -> dict[str, list[dict[str, Any]]]:
        """A run's comics in reading order, issues apart from volumes.

        Deliberately not `series_backdrop_files`, which looks like the same
        list and is not. That one sorts by `float(issue_number)`, so every
        volume -- whose issue number is null -- becomes infinity and lands
        after the last issue, and "1AU" and "Annual 1" land there with them.
        A reader walking that list offers an omnibus as the next issue.

        Here the order is `_natural_issue_key`, the key the issue list itself
        is sorted by, so the reader and the Issues tab agree; and volumes are
        returned separately rather than sorted to the end, because a volume is
        a different offer ("read Vol. 2"), not a later issue.
        """
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone() is None:
                raise LookupError("That series run is not in the catalog")
            run = connection.execute(
                "SELECT format, reading_direction FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone()
            rows = list(connection.execute(
                """SELECT files.id, files.path, files.filename, files.extension,
                          file_identities.issue_number, file_identities.identity_kind,
                          editions.title AS edition_title, editions.volume_number
                   FROM files
                   JOIN file_identities ON file_identities.file_id=files.id
                   LEFT JOIN file_edition_links ON file_edition_links.file_id=files.id
                   LEFT JOIN editions ON editions.id=file_edition_links.edition_id
                   WHERE file_identities.series_run_id=? AND files.present=1""",
                (series_run_id,),
            ))
        issues, volumes = [], []
        for row in rows:
            item = {
                "id": str(row["id"]), "path": row["path"], "filename": row["filename"],
                "extension": row["extension"], "issueNumber": row["issue_number"],
                "identityKind": row["identity_kind"],
            }
            if row["issue_number"]:
                issues.append(item)
                continue
            # What to call a collection on a button. Its volume number if the
            # edition has one, else its title; never the issue it might
            # contain, because nothing records where that issue begins.
            item["volumeLabel"] = (
                f"Vol. {row['volume_number']}" if row["volume_number"] is not None
                else row["edition_title"] or "volume"
            )
            volumes.append(item)
        issues.sort(key=lambda item: (_natural_issue_key(item["issueNumber"]), item["filename"].casefold()))
        volumes.sort(key=lambda item: item["volumeLabel"].casefold())
        return {
            "medium": run["format"], "readingDirection": run["reading_direction"],
            "issues": issues, "volumes": volumes,
        }

    def reading_progress_for_run(self, series_run_id: int, *, user_id: int) -> dict[str, dict[str, Any]]:
        """Where every comic in one run was left, keyed by file id.

        Staleness is decided in SQL rather than by opening the files: the
        signature `app._file_signature` writes is the file's mtime and size in
        hex, and both are columns here, so `printf('%x-%x', ...)` rebuilds it
        exactly.

        Those columns are only as fresh as the last scan, which makes this
        good enough to decide whether to draw a progress bar in a list and not
        good enough to decide where to open a comic. The reader's own check in
        `file_reading_progress` stats the real file and stays authoritative.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT reading_progress.*,
                          printf('%x-%x', files.mtime_ns, files.size_bytes) AS scanned_signature
                   FROM reading_progress
                   JOIN files ON files.id=reading_progress.file_id AND files.present=1
                   JOIN file_identities ON file_identities.file_id=files.id
                   WHERE file_identities.series_run_id=? AND reading_progress.user_id=?""",
                (series_run_id, int(user_id)),
            ).fetchall()
        return {
            str(row["file_id"]): {
                "page": int(row["page"]), "pageCount": int(row["page_count"]),
                "finishedAt": row["finished_at"], "updatedAt": row["updated_at"],
                "stale": row["file_signature"] != row["scanned_signature"],
            }
            for row in rows
        }

    def reading_progress_by_run(self, *, user_id: int) -> dict[str, dict[str, dict[str, Any]]]:
        """Every run that has been read in, and where each of its comics sits.

        One query for the whole library, because the Comics grid asks about
        every run at once and a request per card would be a request per card.
        Runs nobody has opened are absent rather than empty: the grid draws
        nothing for them, so there is nothing to say.

        Staleness is decided the same way as `reading_progress_for_run`, and
        carries the same caveat -- the columns are as fresh as the last scan.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT reading_progress.*, file_identities.series_run_id,
                          file_identities.issue_number,
                          printf('%x-%x', files.mtime_ns, files.size_bytes) AS scanned_signature
                   FROM reading_progress
                   JOIN files ON files.id=reading_progress.file_id AND files.present=1
                   JOIN file_identities ON file_identities.file_id=files.id
                   WHERE file_identities.series_run_id IS NOT NULL AND reading_progress.user_id=?""",
                (int(user_id),),
            ).fetchall()
        by_run: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            by_run.setdefault(str(row["series_run_id"]), {})[str(row["file_id"])] = {
                "page": int(row["page"]), "pageCount": int(row["page_count"]),
                "finishedAt": row["finished_at"], "updatedAt": row["updated_at"],
                "issueNumber": row["issue_number"],
                "stale": row["file_signature"] != row["scanned_signature"],
            }
        return by_run

    # ----- Story arcs saved as reading lists (schema 57) -----

    _READING_LIST_ITEM_KEYS = (
        ("provider", "provider"), ("providerIssueId", "provider_issue_id"),
        ("providerSeriesId", "provider_series_id"), ("seriesTitle", "series_title"),
        ("seriesYear", "series_year"), ("number", "issue_number"), ("coverDate", "cover_date"),
        ("cover", "cover"), ("issueType", "issue_type"), ("issueId", "issue_id"),
    )

    @staticmethod
    def _reading_list_meta(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]), "name": row["name"], "description": row["description"],
            "cover": row["cover"], "source": row["source"], "provider": row["provider"],
            "providerArcId": row["provider_arc_id"], "sourceUrl": row["source_url"],
            "sourceName": row["source_name"],
            "createdBy": int(row["created_by"]) if row["created_by"] is not None else None,
            "createdAt": row["created_at"], "updatedAt": row["updated_at"], "refreshedAt": row["refreshed_at"],
            "ownerId": int(row["owner_user_id"]) if row["owner_user_id"] is not None else None,
            "shared": bool(row["shared"]),
            "sortMode": row["sort_mode"] or "custom",
        }

    # An arc read by cover date: dated issues first, oldest first, the
    # arranged order breaking ties and keeping the undated where they were
    # relative to each other (as the client's one-off sort did).
    @staticmethod
    def _release_ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def dated(item: dict[str, Any]) -> str | None:
            value = str(item.get("coverDate") or "")
            return value if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", value) else None
        return [item for _index, item in sorted(
            enumerate(items), key=lambda pair: (dated(pair[1]) is None, dated(pair[1]) or "", pair[0]))]

    @staticmethod
    def _reading_list_item(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]), "position": int(row["position"]), "provider": row["provider"],
            "providerIssueId": row["provider_issue_id"], "providerSeriesId": row["provider_series_id"],
            "seriesTitle": row["series_title"], "seriesYear": row["series_year"],
            "number": row["issue_number"], "coverDate": row["cover_date"], "cover": row["cover"],
            "issueType": row["issue_type"],
            "issueId": str(row["issue_id"]) if row["issue_id"] is not None else None,
            "runId": str(row["run_id"]) if row["run_id"] is not None else None,
            "fileId": str(row["file_id"]) if row["file_id"] is not None else None,
        }

    def _insert_reading_list_items(
        self, connection: sqlite3.Connection, list_id: int, items: list[dict[str, Any]], *, now: str,
        start: int = 0,
    ) -> None:
        for offset, item in enumerate(items):
            values = {column: item.get(key) for key, column in self._READING_LIST_ITEM_KEYS}
            if not str(values["series_title"] or "").strip() or not str(values["issue_number"] or "").strip():
                raise ValueError("Every issue of a story arc needs a series and a number")
            values["series_year"] = int(values["series_year"]) if str(values["series_year"] or "").isdigit() else None
            values["issue_id"] = int(values["issue_id"]) if values["issue_id"] is not None else None
            connection.execute(
                """INSERT INTO reading_list_items(reading_list_id, position, provider, provider_issue_id,
                       provider_series_id, series_title, series_year, issue_number, cover_date, cover,
                       issue_type, issue_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(list_id), start + offset, values["provider"],
                 str(values["provider_issue_id"]) if values["provider_issue_id"] is not None else None,
                 str(values["provider_series_id"]) if values["provider_series_id"] is not None else None,
                 str(values["series_title"]).strip(), values["series_year"], str(values["issue_number"]).strip(),
                 values["cover_date"], values["cover"], values["issue_type"], values["issue_id"], now, now),
            )

    @staticmethod
    def _renumber_reading_list(connection: sqlite3.Connection, list_id: int, order: list[int] | None = None) -> None:
        """Positions 0..n-1 over the live items, in `order` or in their current order."""
        if order is None:
            order = [int(row["id"]) for row in connection.execute(
                """SELECT id FROM reading_list_items WHERE reading_list_id=? AND removed_at IS NULL
                   ORDER BY position, id""", (int(list_id),))]
        connection.executemany(
            "UPDATE reading_list_items SET position=? WHERE id=? AND reading_list_id=?",
            [(position, item_id, int(list_id)) for position, item_id in enumerate(order)],
        )

    def _require_reading_list(self, connection: sqlite3.Connection, list_id: int) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM reading_lists WHERE id=?", (int(list_id),)).fetchone()
        if row is None:
            raise LookupError("That story arc is not in the library")
        return row

    def create_reading_list(
        self, name: str, *, description: str | None = None, cover: str | None = None,
        source: str = "metron", provider: str | None = None, provider_arc_id: str | None = None,
        source_url: str | None = None, source_name: str | None = None,
        created_by: int | None = None, items: list[dict[str, Any]] | None = None,
        owner_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Save an arc with its issues in the order given. Items are dicts with
        `seriesTitle`, `number` and, when known, `provider`, `providerIssueId`,
        `providerSeriesId`, `seriesYear`, `coverDate`, `cover`, `issueType`,
        `issueId`."""
        name = " ".join(str(name or "").split())
        if not name:
            raise ValueError("A story arc needs a name")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO reading_lists(name, description, cover, source, provider, provider_arc_id,
                       source_url, source_name, created_by, owner_user_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, description, cover, source, provider,
                 str(provider_arc_id) if provider_arc_id is not None else None, source_url, source_name,
                 int(created_by) if created_by is not None else None,
                 int(owner_user_id) if owner_user_id is not None else None, now, now),
            )
            list_id = int(cursor.lastrowid)
            self._insert_reading_list_items(connection, list_id, list(items or []), now=now)
        return self.reading_list(list_id)

    # Which catalog's ids an added issue carries, best first: Metron is what
    # Pull and CBL export know best.
    _ISSUE_PROVIDER_ORDER = ("metron", "comic_vine", "gcd")

    def add_reading_list_issues(self, list_id: int, issue_ids: list[int]) -> dict[str, list[str]]:
        """Library issues appended to an arc by hand, in the order given.

        Each item is the issue as the catalog knows it -- its run's title and
        year, its number and date -- tied to it by `issue_id`, and carries the
        issue's catalog ids so an issue not owned yet can be pulled and the arc
        exported. One already in the arc is skipped, not added twice.
        """
        wanted = list(dict.fromkeys(int(value) for value in issue_ids))
        if not wanted:
            raise ValueError("Choose an issue to add")
        now = _utc_now()
        added: list[str] = []
        skipped: list[str] = []
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            marks = ",".join("?" * len(wanted))
            rows = {int(row["id"]): row for row in connection.execute(
                f"""SELECT issues.id, issues.issue_number, issues.publication_date, issues.cover, issues.series_run_id,
                           series_runs.canonical_title, series_runs.start_year
                      FROM issues JOIN series_runs ON series_runs.id=issues.series_run_id
                     WHERE issues.id IN ({marks})""", wanted)}
            if len(rows) != len(wanted):
                raise LookupError("Some of those issues are no longer in the library")
            present = {int(row["issue_id"]) for row in connection.execute(
                "SELECT issue_id FROM reading_list_items WHERE reading_list_id=? AND removed_at IS NULL AND issue_id IS NOT NULL",
                (int(list_id),))}
            issue_ids_by_provider: dict[int, dict[str, str]] = {}
            for row in connection.execute(
                    f"SELECT issue_id, provider, provider_id FROM issue_provider_ids WHERE issue_id IN ({marks})", wanted):
                issue_ids_by_provider.setdefault(int(row["issue_id"]), {})[row["provider"]] = str(row["provider_id"])
            run_ids = sorted({int(row["series_run_id"]) for row in rows.values()})
            series_ids_by_provider: dict[int, dict[str, str]] = {}
            for row in connection.execute(
                    f"SELECT series_run_id, provider, provider_id FROM series_provider_ids WHERE series_run_id IN ({','.join('?' * len(run_ids))})",
                    run_ids):
                series_ids_by_provider.setdefault(int(row["series_run_id"]), {})[row["provider"]] = str(row["provider_id"])
            position = int(connection.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM reading_list_items WHERE reading_list_id=? AND removed_at IS NULL",
                (int(list_id),)).fetchone()[0])
            for issue_id in wanted:
                if issue_id in present:
                    skipped.append(str(issue_id))
                    continue
                present.add(issue_id)
                row = rows[issue_id]
                ids = issue_ids_by_provider.get(issue_id, {})
                provider = next((name for name in self._ISSUE_PROVIDER_ORDER if name in ids), None)
                series_ids = series_ids_by_provider.get(int(row["series_run_id"]), {})
                # The arc may already hold this catalog issue: taken out
                # before, or never matched to a file. It is that item again --
                # brought back at the end and tied to the issue -- since an
                # arc holds a catalog issue once.
                earlier = connection.execute(
                    """SELECT id, removed_at FROM reading_list_items
                        WHERE reading_list_id=? AND provider=? AND provider_issue_id=?""",
                    (int(list_id), provider, ids.get(provider))).fetchone() if provider else None
                if earlier is not None and earlier["removed_at"] is None:
                    connection.execute("UPDATE reading_list_items SET issue_id=?, updated_at=? WHERE id=?",
                                       (issue_id, now, int(earlier["id"])))
                    skipped.append(str(issue_id))
                    continue
                if earlier is not None:
                    connection.execute(
                        "UPDATE reading_list_items SET removed_at=NULL, issue_id=?, position=?, updated_at=? WHERE id=?",
                        (issue_id, position, now, int(earlier["id"])))
                else:
                    self._insert_reading_list_items(connection, list_id, [{
                        "seriesTitle": row["canonical_title"], "seriesYear": row["start_year"],
                        "number": row["issue_number"], "coverDate": row["publication_date"], "cover": row["cover"],
                        "issueId": issue_id, "provider": provider,
                        "providerIssueId": ids.get(provider) if provider else None,
                        "providerSeriesId": series_ids.get(provider) if provider else None,
                    }], now=now, start=position)
                position += 1
                added.append(str(issue_id))
            connection.execute("UPDATE reading_lists SET updated_at=? WHERE id=?", (now, int(list_id)))
        return {"added": added, "skipped": skipped}

    def set_reading_list_shared(self, list_id: int, shared: bool) -> None:
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            connection.execute("UPDATE reading_lists SET shared=?, updated_at=? WHERE id=?",
                               (1 if shared else 0, _utc_now(), int(list_id)))

    def reading_list_meta(self, list_id: int) -> dict[str, Any]:
        """An arc's name, source, owner and sharing, without its items."""
        with self._connect() as connection:
            return self._reading_list_meta(self._require_reading_list(connection, list_id))

    def reading_list_by_provider(self, provider: str, provider_arc_id: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM reading_lists WHERE provider=? AND provider_arc_id=?",
                (provider, str(provider_arc_id)),
            ).fetchone()
        return int(row["id"]) if row else None

    def reading_list_by_source_url(self, url: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute("SELECT id FROM reading_lists WHERE source_url=?", (url,)).fetchone()
        return int(row["id"]) if row else None

    _READING_LIST_ITEMS_SQL = """
        SELECT items.*, issues.series_run_id AS run_id,
               (SELECT MIN(files.id) FROM file_issue_links
                  JOIN files ON files.id=file_issue_links.file_id AND files.present=1
                 WHERE file_issue_links.issue_id=items.issue_id) AS file_id
          FROM reading_list_items AS items
          LEFT JOIN issues ON issues.id=items.issue_id
         WHERE items.removed_at IS NULL"""

    def reading_list(self, list_id: int) -> dict[str, Any]:
        """An arc and its live items in order, each with the local issue, run
        and file it resolves to (the run's first present file for the issue)."""
        with self._connect() as connection:
            meta = self._reading_list_meta(self._require_reading_list(connection, list_id))
            rows = connection.execute(
                self._READING_LIST_ITEMS_SQL + " AND items.reading_list_id=? ORDER BY items.position, items.id",
                (int(list_id),),
            ).fetchall()
            items = [self._reading_list_item(row) for row in rows]
            file_ids = [int(item["fileId"]) for item in items if item["fileId"]]
            files = {}
            if file_ids:
                marks = ",".join("?" * len(file_ids))
                files = {
                    int(row["id"]): row for row in connection.execute(
                        f"SELECT id, path, filename FROM files WHERE id IN ({marks})", file_ids)
                }
        for item in items:
            row = files.get(int(item["fileId"])) if item["fileId"] else None
            item["filename"] = row["filename"] if row else None
            item["path"] = row["path"] if row else None
        if meta["sortMode"] == "release":
            items = self._release_ordered(items)
        return {**meta, "items": items}

    def reading_lists_overview(self) -> list[dict[str, Any]]:
        """Every arc with its items in brief (issue, run, file), for the grid."""
        with self._connect() as connection:
            lists = [self._reading_list_meta(row) for row in connection.execute(
                "SELECT * FROM reading_lists ORDER BY created_at DESC, id DESC")]
            rows = connection.execute(self._READING_LIST_ITEMS_SQL + " ORDER BY items.position, items.id").fetchall()
        by_list: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = self._reading_list_item(row)
            by_list.setdefault(str(row["reading_list_id"]), []).append({
                key: item[key] for key in ("id", "seriesTitle", "seriesYear", "number", "coverDate", "issueId", "runId", "fileId")
            })
        for entry in lists:
            entry["items"] = by_list.get(entry["id"], [])
            if entry["sortMode"] == "release":
                entry["items"] = self._release_ordered(entry["items"])
        return lists

    READING_LIST_SORTS = ("custom", "release")

    def update_reading_list(
        self, list_id: int, *, name: str | None = None, description: str | None = None,
        cover: str | None = None, refreshed_at: str | None = None, sort_mode: str | None = None,
    ) -> None:
        """Change what is given; a description of "" clears it."""
        changes = {"name": " ".join(str(name).split()) if name is not None else None,
                   "description": description, "cover": cover, "refreshed_at": refreshed_at,
                   "sort_mode": sort_mode}
        changes = {column: value for column, value in changes.items() if value is not None}
        if changes.get("name") == "":
            raise ValueError("A story arc needs a name")
        if "description" in changes:
            changes["description"] = str(changes["description"]).strip() or None
            if changes["description"] and len(changes["description"]) > 2000:
                raise ValueError("Keep the description under 2,000 characters")
        if "sort_mode" in changes and changes["sort_mode"] not in self.READING_LIST_SORTS:
            raise ValueError("Read a story arc in your order or by release date")
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            changes["updated_at"] = _utc_now()
            assignments = ", ".join(f"{column}=?" for column in changes)
            connection.execute(f"UPDATE reading_lists SET {assignments} WHERE id=?", (*changes.values(), int(list_id)))

    def set_reading_list_item_issues(self, list_id: int, issue_by_item: dict[int, int | None]) -> None:
        """Write back what the items resolved to."""
        if not issue_by_item:
            return
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            connection.executemany(
                "UPDATE reading_list_items SET issue_id=?, updated_at=? WHERE id=? AND reading_list_id=?",
                [(int(issue_id) if issue_id is not None else None, now, int(item_id), int(list_id))
                 for item_id, issue_id in issue_by_item.items()],
            )

    def reorder_reading_list(self, list_id: int, item_ids: list[int]) -> None:
        """The new order, which has to place every live item exactly once."""
        wanted = [int(item_id) for item_id in item_ids]
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            live = {int(row["id"]) for row in connection.execute(
                "SELECT id FROM reading_list_items WHERE reading_list_id=? AND removed_at IS NULL", (int(list_id),))}
            if len(wanted) != len(set(wanted)) or set(wanted) != live:
                raise ValueError("Every issue of the arc must be placed, once")
            self._renumber_reading_list(connection, list_id, wanted)
            connection.execute("UPDATE reading_lists SET updated_at=? WHERE id=?", (_utc_now(), int(list_id)))

    def remove_reading_list_items(self, list_id: int, item_ids: list[int]) -> int:
        """Take issues out of the arc. They are kept, marked removed, so a
        refresh from the provider does not put them back."""
        ids = [int(item_id) for item_id in item_ids]
        if not ids:
            return 0
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            marks = ",".join("?" * len(ids))
            cursor = connection.execute(
                f"""UPDATE reading_list_items SET removed_at=?, updated_at=?
                     WHERE reading_list_id=? AND removed_at IS NULL AND id IN ({marks})""",
                (now, now, int(list_id), *ids),
            )
            self._renumber_reading_list(connection, list_id)
            connection.execute("UPDATE reading_lists SET updated_at=? WHERE id=?", (now, int(list_id)))
            return cursor.rowcount

    def merge_reading_list_items(self, list_id: int, entries: list[dict[str, Any]]) -> dict[str, int]:
        """The provider's list again. An issue already here (removed or not)
        takes the new details and keeps its place; a new one goes in after
        the nearest issue before it in the provider's order that is in the
        arc, or first. Nothing is reordered and nothing removed comes back."""
        now = _utc_now()
        added = updated = 0
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            known = {
                (row["provider"], row["provider_issue_id"]): (int(row["id"]), row["removed_at"])
                for row in connection.execute(
                    """SELECT id, provider, provider_issue_id, removed_at FROM reading_list_items
                       WHERE reading_list_id=? AND provider_issue_id IS NOT NULL""", (int(list_id),))
            }
            order = [int(row["id"]) for row in connection.execute(
                """SELECT id FROM reading_list_items WHERE reading_list_id=? AND removed_at IS NULL
                   ORDER BY position, id""", (int(list_id),))]
            placed: list[tuple[str, str] | None] = []
            for entry in entries:
                key = (entry.get("provider"), str(entry.get("providerIssueId") or "") or None)
                if key[1] is None:
                    placed.append(None)
                    continue
                if key in known:
                    item_id, _removed = known[key]
                    connection.execute(
                        """UPDATE reading_list_items SET provider_series_id=?, series_title=?, series_year=?,
                               issue_number=?, cover_date=?, cover=?, issue_type=?, updated_at=?
                           WHERE id=?""",
                        (str(entry.get("providerSeriesId") or "") or None, str(entry.get("seriesTitle") or "").strip(),
                         int(entry["seriesYear"]) if str(entry.get("seriesYear") or "").isdigit() else None,
                         str(entry.get("number") or "").strip(), entry.get("coverDate"), entry.get("cover"),
                         entry.get("issueType"), now, item_id),
                    )
                    updated += 1
                    placed.append(key)
                    continue
                self._insert_reading_list_items(connection, list_id, [entry], now=now, start=len(order))
                new_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                known[key] = (new_id, None)
                after = 0
                for earlier in reversed(placed):
                    if earlier is None:
                        continue
                    earlier_id, earlier_removed = known[earlier]
                    if earlier_removed is None and earlier_id in order:
                        after = order.index(earlier_id) + 1
                        break
                order.insert(after, new_id)
                placed.append(key)
                added += 1
            self._renumber_reading_list(connection, list_id, order)
            connection.execute("UPDATE reading_lists SET updated_at=?, refreshed_at=? WHERE id=?",
                               (now, now, int(list_id)))
        return {"added": added, "updated": updated}

    def delete_reading_list(self, list_id: int) -> None:
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            connection.execute("DELETE FROM reading_lists WHERE id=?", (int(list_id),))

    def reading_list_files(self, list_id: int) -> list[dict[str, Any]]:
        """The arc's comics that are here, in the arc's order."""
        return [
            {"itemId": item["id"], "id": item["fileId"], "path": item["path"], "filename": item["filename"],
             "issueNumber": item["number"], "runId": item["runId"], "seriesTitle": item["seriesTitle"]}
            for item in self.reading_list(list_id)["items"] if item["fileId"]
        ]

    def reading_progress_by_list(self, *, user_id: int) -> dict[str, dict[str, dict[str, Any]]]:
        """Where each arc's comics were left, keyed by arc then file -- one
        query for the grid, as `reading_progress_by_run` is."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT items.reading_list_id, items.issue_number, issues.series_run_id, reading_progress.*,
                          printf('%x-%x', files.mtime_ns, files.size_bytes) AS scanned_signature
                     FROM reading_list_items AS items
                     JOIN issues ON issues.id=items.issue_id
                     JOIN file_issue_links ON file_issue_links.issue_id=issues.id
                     JOIN files ON files.id=file_issue_links.file_id AND files.present=1
                     JOIN reading_progress ON reading_progress.file_id=files.id AND reading_progress.user_id=?
                    WHERE items.removed_at IS NULL""",
                (int(user_id),),
            ).fetchall()
        by_list: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            by_list.setdefault(str(row["reading_list_id"]), {})[str(row["file_id"])] = {
                "page": int(row["page"]), "pageCount": int(row["page_count"]),
                "finishedAt": row["finished_at"], "updatedAt": row["updated_at"],
                "issueNumber": row["issue_number"], "runId": str(row["series_run_id"]),
                "stale": row["file_signature"] != row["scanned_signature"],
            }
        return by_list

    def reading_list_backdrop_preference(self, list_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = self._require_reading_list(connection, list_id)
        if row["backdrop_file_id"] is None or not row["backdrop_member"]:
            return None
        return {"fileId": str(row["backdrop_file_id"]), "member": row["backdrop_member"],
                "source": row["backdrop_source"] or "chosen", "fileSignature": row["backdrop_signature"]}

    def set_reading_list_backdrop(
        self, list_id: int, file_id: int, member: str, source: str, file_signature: str | None = None,
    ) -> None:
        """The page behind the arc's header: one of its own comics only."""
        if source not in {"chosen", "auto"}:
            raise ValueError("Unsupported background source")
        if not member:
            raise ValueError("Choose a page")
        if not any(item["id"] == str(file_id) for item in self.reading_list_files(list_id)):
            raise ValueError("That comic is not part of this story arc")
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE reading_lists SET backdrop_file_id=?, backdrop_member=?, backdrop_source=?,
                       backdrop_signature=?, updated_at=? WHERE id=?""",
                (int(file_id), member, source, file_signature, _utc_now(), int(list_id)),
            )

    def clear_reading_list_backdrop(self, list_id: int) -> None:
        with self._write_lock, self._connect() as connection:
            self._require_reading_list(connection, list_id)
            connection.execute(
                """UPDATE reading_lists SET backdrop_file_id=NULL, backdrop_member=NULL, backdrop_source=NULL,
                       backdrop_signature=NULL, updated_at=? WHERE id=?""", (_utc_now(), int(list_id)))

    def queued_issue_ids(self, issue_ids: list[int]) -> set[int]:
        """Which of these issues are on their way: a job still open for
        them, or an open request that names them before it has a job."""
        ids = sorted({int(value) for value in issue_ids if str(value).isdigit()})
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT issue_id FROM acquisition_jobs
                     WHERE issue_id IN ({marks}) AND status NOT IN ('fulfilled', 'cancelled')
                    UNION
                    SELECT ari.issue_id FROM acquisition_request_issues AS ari
                      JOIN acquisition_requests AS r ON r.id=ari.request_id
                     WHERE r.status='open' AND ari.issue_id IN ({marks})""",
                (*ids, *ids),
            ).fetchall()
        return {int(row[0]) for row in rows}

    def run_title_index(self) -> list[dict[str, Any]]:
        """Every run's id, title and year -- enough to tell which run a
        provider's series is, without building the catalog."""
        with self._connect() as connection:
            return [
                {"id": str(row["id"]), "title": row["canonical_title"], "year": row["start_year"]}
                for row in connection.execute("SELECT id, canonical_title, start_year FROM series_runs")
            ]

    def issue_ids_by_provider(self, provider: str, provider_ids: list[str]) -> dict[str, int]:
        """Local issues already linked to these provider issue ids."""
        ids = [str(value) for value in provider_ids if value]
        if not ids:
            return {}
        with self._connect() as connection:
            marks = ",".join("?" * len(ids))
            return {
                str(row["provider_id"]): int(row["issue_id"]) for row in connection.execute(
                    f"SELECT provider_id, issue_id FROM issue_provider_ids WHERE provider=? AND provider_id IN ({marks})",
                    (provider, *ids))
            }

    def issues_by_run(self, run_ids: list[int]) -> dict[str, list[tuple[int, str]]]:
        """Each run's issues as (issue id, number)."""
        ids = sorted({int(value) for value in run_ids if str(value).isdigit()})
        if not ids:
            return {}
        with self._connect() as connection:
            marks = ",".join("?" * len(ids))
            rows = connection.execute(
                f"SELECT id, series_run_id, issue_number FROM issues WHERE series_run_id IN ({marks}) ORDER BY id",
                ids).fetchall()
        by_run: dict[str, list[tuple[int, str]]] = {}
        for row in rows:
            by_run.setdefault(str(row["series_run_id"]), []).append((int(row["id"]), row["issue_number"]))
        return by_run

    def delete_page_panels(self, file_id: int, member: str) -> bool:
        """Forget one page's panels, a person's included, so the automatic tiers read it again."""
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM page_panels WHERE file_id=? AND page_member=?", (int(file_id), member),
            )
            return cursor.rowcount > 0

    def forget_automatic_page_panels(self, file_id: int) -> dict[str, int]:
        """Forget every automatic reading of a file's pages, keeping a person's, so they are read again as reached."""
        with self._write_lock, self._connect() as connection:
            kept = connection.execute(
                "SELECT COUNT(*) FROM page_panels WHERE file_id=? AND source='manual'", (int(file_id),),
            ).fetchone()[0]
            cursor = connection.execute(
                "DELETE FROM page_panels WHERE file_id=? AND source<>'manual'", (int(file_id),),
            )
            return {"forgotten": cursor.rowcount, "kept": int(kept)}

    def manual_page_panels_near(self, file_id: int, limit: int = 2) -> list[dict[str, Any]]:
        """A person's hand-fixed pages nearest this comic: its own first, then its run's, newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT p.file_id, p.page_member, p.panels_json
                   FROM page_panels p
                   LEFT JOIN file_identities own ON own.file_id=?
                   LEFT JOIN file_identities theirs ON theirs.file_id=p.file_id
                   WHERE p.source='manual' AND p.segmented=1
                     AND (p.file_id=? OR (own.series_run_id IS NOT NULL AND theirs.series_run_id=own.series_run_id))
                   ORDER BY (p.file_id=?) DESC, p.updated_at DESC
                   LIMIT ?""",
                (int(file_id), int(file_id), int(file_id), int(limit)),
            ).fetchall()
        return [
            {"fileId": int(row["file_id"]), "member": row["page_member"], "panels": _load_json(row["panels_json"], [])}
            for row in rows
        ]

    def page_panels(self, file_id: int, member: str) -> dict[str, Any] | None:
        """What was read off one page, or None when nobody has looked yet."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM page_panels WHERE file_id=? AND page_member=?", (int(file_id), member),
            ).fetchone()
        if row is None:
            return None
        return {
            "fileSignature": row["file_signature"], "source": row["source"],
            "segmented": bool(row["segmented"]), "panels": _load_json(row["panels_json"], []),
        }

    def set_page_panels(
        self, file_id: int, member: str, file_signature: str, source: str,
        panels: list[dict[str, Any]], segmented: bool,
    ) -> None:
        """Keep one page's panels. `source` says who found them, so an automatic
        reading can be redone when the file changes and a person's is left alone."""
        if source not in PANEL_SOURCES:
            raise ValueError("Unsupported panel source")
        if not member:
            raise ValueError("Choose a page")
        with self._write_lock, self._connect() as connection:
            if not connection.execute("SELECT 1 FROM files WHERE id=?", (int(file_id),)).fetchone():
                raise LookupError("Library file was not found")
            connection.execute(
                """INSERT INTO page_panels(
                       file_id, page_member, file_signature, source, panels_json, segmented, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(file_id, page_member) DO UPDATE SET
                       file_signature=excluded.file_signature, source=excluded.source,
                       panels_json=excluded.panels_json, segmented=excluded.segmented,
                       updated_at=excluded.updated_at""",
                (int(file_id), member, file_signature, source, json.dumps(list(panels)),
                 1 if segmented else 0, _utc_now()),
            )

    def file_reading_direction(self, file_id: int) -> str:
        """Which way a comic reads: its run's override, else right to left for manga.

        The same rule as `readingDirection` in the reader's reader.js, so a
        page's panels are ordered the way its pages are turned. A file with
        no run reads as a comic.
        """
        with self._connect() as connection:
            row = connection.execute(
                """SELECT series_runs.format, series_runs.reading_direction
                   FROM files
                   JOIN file_identities ON file_identities.file_id=files.id
                   JOIN series_runs ON series_runs.id=file_identities.series_run_id
                   WHERE files.id=?""",
                (int(file_id),),
            ).fetchone()
        if row is None:
            return "ltr"
        if row["reading_direction"] in ("ltr", "rtl"):
            return row["reading_direction"]
        return "rtl" if row["format"] == "manga" else "ltr"

    def issue_file_counts_by_run(self) -> dict[str, int]:
        """How many single-issue comics each run owns, for the Comics grid.

        The run map needs to know whether anything is left unread after the
        comics that have been finished, and this is the cheapest honest answer:
        one query, present files only, and only files that are an issue --
        volumes are read separately, as `run_reading_files` partitions them,
        and an omnibus must not keep a run "in progress" forever. It is a file
        count, not a readability sniff: a PDF counts as unread. That is the
        same proxy the grid already accepts rather than opening every archive
        on every visit.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT file_identities.series_run_id AS run_id, COUNT(*) AS files
                   FROM files
                   JOIN file_identities ON file_identities.file_id=files.id
                   WHERE files.present=1
                     AND file_identities.series_run_id IS NOT NULL
                     AND file_identities.issue_number IS NOT NULL
                   GROUP BY file_identities.series_run_id"""
            ).fetchall()
        return {str(row["run_id"]): int(row["files"]) for row in rows}

    # ---- Reader profiles -------------------------------------------------
    #
    # The store keeps who exists and what they may do; the app hashes secrets,
    # signs cookies and decides who is asking. Hashes never leave the store
    # except through `user_secrets`, which the app calls only to verify one.

    _USER_PUBLIC = (
        "id, name, login_name, role, colour, can_request, auto_approve, show_on_picker, "
        "session_version, disabled, created_at, updated_at, last_seen_at, avatar_source, avatar_updated_at, switch_lock, pin_length, "
        "max_rating, allow_unrated, can_discover, "
        "password_hash IS NOT NULL AS has_password, pin_hash IS NOT NULL AS has_pin"
    )

    @staticmethod
    def _user_record(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]), "name": row["name"], "loginName": row["login_name"],
            "role": row["role"], "colour": row["colour"],
            "canRequest": bool(row["can_request"]), "autoApprove": bool(row["auto_approve"]),
            "showOnPicker": bool(row["show_on_picker"]), "sessionVersion": int(row["session_version"]),
            "disabled": bool(row["disabled"]), "hasPassword": bool(row["has_password"]),
            "hasPin": bool(row["has_pin"]), "createdAt": row["created_at"],
            "updatedAt": row["updated_at"], "lastSeenAt": row["last_seen_at"],
            # The picture's address changes whenever the picture does, so a
            # browser that kept the old one asks again.
            "avatar": (
                f"/api/v1/profiles/{int(row['id'])}/avatar?v={urllib.parse.quote(str(row['avatar_updated_at']))}"
                if row["avatar_source"] else None
            ),
            "avatarSource": row["avatar_source"],
            "switchLock": row["switch_lock"],
            "pinLength": row["pin_length"],
            "maxRating": row["max_rating"], "allowUnrated": bool(row["allow_unrated"]),
            "canDiscover": bool(row["can_discover"]),
        }

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(f"SELECT {self._USER_PUBLIC} FROM users ORDER BY id").fetchall()
        return [self._user_record(row) for row in rows]

    def user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(f"SELECT {self._USER_PUBLIC} FROM users WHERE id=?", (int(user_id),)).fetchone()
        return self._user_record(row)

    def user_by_login(self, login_name: str) -> dict[str, Any] | None:
        """The profile that signs in with this name, ignoring case."""
        if not login_name:
            return None
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {self._USER_PUBLIC} FROM users WHERE login_name=? COLLATE NOCASE", (login_name.strip(),),
            ).fetchone()
        return self._user_record(row)

    def user_secrets(self, user_id: int) -> dict[str, str | None]:
        """A profile's password and PIN hashes, for the app to verify against. Never sent anywhere."""
        with self._connect() as connection:
            row = connection.execute("SELECT password_hash, pin_hash FROM users WHERE id=?", (int(user_id),)).fetchone()
        if row is None:
            raise LookupError("That profile does not exist")
        return {"passwordHash": row["password_hash"], "pinHash": row["pin_hash"]}

    @staticmethod
    def _profile_name(value: Any) -> str:
        name = " ".join(str(value or "").split())
        if not name:
            raise ValueError("A profile needs a name")
        if len(name) > PROFILE_NAME_MAX:
            raise ValueError(f"A profile's name is at most {PROFILE_NAME_MAX} characters")
        return name

    @staticmethod
    def _login_name(value: Any) -> str | None:
        if value is None:
            return None
        name = str(value).strip()
        if not name:
            return None
        if len(name) > PROFILE_NAME_MAX or any(char.isspace() for char in name):
            raise ValueError("A sign-in name is one word, at most 40 characters")
        return name

    def create_user(
        self, name: str, *, role: str = "reader", login_name: str | None = None,
        password_hash: str | None = None, pin_hash: str | None = None, colour: str | None = None,
        can_request: bool = True, auto_approve: bool = False, show_on_picker: bool = True,
        switch_lock: str = "open", pin_length: int | None = None,
    ) -> dict[str, Any]:
        """A new profile.

        The app, not the store, refuses a reader while the admin can be picked
        by anyone: the admin's password lives in the sign-in config, which the
        store never reads.
        """
        if role not in USER_ROLES:
            raise ValueError("A profile is an admin or a reader")
        if colour is not None and colour not in PROFILE_COLOURS:
            raise ValueError("Choose one of the profile colours")
        if switch_lock not in SWITCH_LOCKS:
            raise ValueError("A profile opens with nothing, a PIN or a password")
        name = self._profile_name(name)
        login = self._login_name(login_name)
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            if colour is None:
                colour = self._next_profile_colour(connection)
            try:
                cursor = connection.execute(
                    """INSERT INTO users(name, login_name, role, colour, password_hash, pin_hash,
                                         can_request, auto_approve, show_on_picker, switch_lock, pin_length,
                                         created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, login, role, colour, password_hash, pin_hash, int(bool(can_request)),
                     int(bool(auto_approve)), int(bool(show_on_picker)), switch_lock,
                     pin_length if pin_hash else None, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Another profile already signs in with that name") from exc
            user_id = int(cursor.lastrowid)
        return self.user(user_id)

    @staticmethod
    def _next_profile_colour(connection: sqlite3.Connection) -> str:
        """A new profile's colour, chosen for it rather than by anyone: the
        first in the palette nobody has, so profiles side by side on the
        picker differ. A profile with none is drawn violet, so counts as it."""
        taken = [row[0] or PROFILE_COLOURS[0] for row in connection.execute("SELECT colour FROM users ORDER BY id")]
        for colour in PROFILE_COLOURS:
            if colour not in taken:
                return colour
        return PROFILE_COLOURS[len(taken) % len(PROFILE_COLOURS)]

    _USER_FIELDS = {
        "name": "name", "loginName": "login_name", "role": "role", "colour": "colour",
        "passwordHash": "password_hash", "pinHash": "pin_hash", "canRequest": "can_request",
        "autoApprove": "auto_approve", "showOnPicker": "show_on_picker", "disabled": "disabled",
        "switchLock": "switch_lock", "pinLength": "pin_length",
        "maxRating": "max_rating", "allowUnrated": "allow_unrated", "canDiscover": "can_discover",
    }
    # What ends a profile's sign-ins everywhere when it changes: a new
    # password, a changed role, being switched off. A PIN is not a sign-in.
    # A change to any of these ends the profile's sessions: what it signs in
    # with, what it may do, and (review, 2026-10-06) its PIN and lock -- a
    # PIN changed because it leaked would otherwise leave the sessions it
    # opened good for thirty days.
    _USER_REVOKING = {"password_hash", "role", "disabled", "pin_hash", "switch_lock"}

    def update_user(self, user_id: int, **changes: Any) -> dict[str, Any]:
        """Change a profile. Keys are the public names (`name`, `passwordHash`, ...)."""
        unknown = set(changes) - set(self._USER_FIELDS)
        if unknown:
            raise ValueError(f"Unknown profile fields: {', '.join(sorted(unknown))}")
        values: dict[str, Any] = {}
        for key, value in changes.items():
            column = self._USER_FIELDS[key]
            if column == "name":
                value = self._profile_name(value)
            elif column == "login_name":
                value = self._login_name(value)
            elif column == "role" and value not in USER_ROLES:
                raise ValueError("A profile is an admin or a reader")
            elif column == "colour" and value is not None and value not in PROFILE_COLOURS:
                raise ValueError("Choose one of the profile colours")
            elif column == "switch_lock" and value not in SWITCH_LOCKS:
                raise ValueError("A profile opens with nothing, a PIN or a password")
            elif column == "max_rating" and value is not None and value not in content_rating.RATINGS:
                raise ValueError("Choose one of the ratings")
            elif column in ("allow_unrated", "can_discover"):
                value = int(bool(value))
            elif column in ("can_request", "auto_approve", "show_on_picker", "disabled"):
                value = int(bool(value))
            values[column] = value
        with self._write_lock, self._connect() as connection:
            current = connection.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
            if current is None:
                raise LookupError("That profile does not exist")
            losing_admin = current["role"] == "admin" and not current["disabled"] and (
                values.get("role", "admin") != "admin" or values.get("disabled", 0)
            )
            if losing_admin and self._enabled_admins(connection) <= 1:
                raise ValueError("The library needs an admin: this is the last one")
            if not values:
                return self.user(user_id)
            revoke = any(
                column in values and values[column] != current[column] for column in self._USER_REVOKING
            )
            assignments = ", ".join(f"{column}=?" for column in values)
            params = [*values.values(), _utc_now()]
            if revoke:
                assignments += ", session_version=session_version+1"
            try:
                connection.execute(
                    f"UPDATE users SET {assignments}, updated_at=? WHERE id=?", (*params, int(user_id)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Another profile already signs in with that name") from exc
        return self.user(user_id)

    @staticmethod
    def _enabled_admins(connection: sqlite3.Connection) -> int:
        return int(connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0])

    def reader_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM users WHERE role='reader'").fetchone()[0])

    def delete_user(self, user_id: int) -> None:
        """Remove a profile and everything that was only theirs: history, ratings, settings."""
        with self._write_lock, self._connect() as connection:
            current = connection.execute("SELECT role, disabled FROM users WHERE id=?", (int(user_id),)).fetchone()
            if current is None:
                raise LookupError("That profile does not exist")
            if current["role"] == "admin" and not current["disabled"] and self._enabled_admins(connection) <= 1:
                raise ValueError("The library needs an admin: this is the last one")
            connection.execute("DELETE FROM users WHERE id=?", (int(user_id),))

    def bump_session_version(self, user_id: int) -> int:
        """End every sign-in this profile has, everywhere. Returns the new version."""
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "UPDATE users SET session_version=session_version+1, updated_at=? WHERE id=?",
                (_utc_now(), int(user_id)),
            )
            row = connection.execute("SELECT session_version FROM users WHERE id=?", (int(user_id),)).fetchone()
        if row is None:
            raise LookupError("That profile does not exist")
        return int(row["session_version"])

    def set_user_avatar(self, user_id: int, source: str | None) -> dict[str, Any]:
        """Record where a profile's picture came from (`upload`, `library:<file>:<page>`), or that it has none."""
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET avatar_source=?, avatar_updated_at=?, updated_at=? WHERE id=?",
                (source, _utc_now() if source else None, _utc_now(), int(user_id)),
            )
            if cursor.rowcount == 0:
                raise LookupError("That profile does not exist")
        return self.user(user_id)

    # ---- Readers' requests -------------------------------------------------
    #
    # The store keeps who asked for what and what became of it; the app
    # validates a request, and on approval makes the call and reports back.

    @staticmethod
    def _member_request_record(row: sqlite3.Row) -> dict[str, Any]:
        state = row["status"]
        # Approved and then fulfilled: what was asked for is here.
        if state == "approved" and row["acquisition_status"] == "fulfilled":
            state = "available"
        return {
            "id": int(row["id"]), "kind": row["kind"], "title": row["title"], "status": row["status"],
            "state": state, "targetKey": row["target_key"],
            "params": json.loads(row["params_json"] or "{}"), "detail": json.loads(row["detail_json"] or "{}"),
            "requestedBy": {
                "id": int(row["requested_by"]), "name": row["requester_name"], "colour": row["requester_colour"],
                "avatar": (
                    f"/api/v1/profiles/{int(row['requested_by'])}/avatar?v="
                    f"{urllib.parse.quote(str(row['requester_avatar_at']))}" if row["requester_avatar"] else None
                ),
            },
            "decidedBy": int(row["decided_by"]) if row["decided_by"] is not None else None,
            "decidedAt": row["decided_at"], "declineReason": row["decline_reason"], "failure": row["failure"],
            "acquisitionRequestId": row["acquisition_request_id"], "seriesRunId": row["series_run_id"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    _MEMBER_REQUEST_SELECT = """
        SELECT member_requests.*, users.name AS requester_name, users.colour AS requester_colour,
               users.avatar_source AS requester_avatar, users.avatar_updated_at AS requester_avatar_at,
               acquisition_requests.status AS acquisition_status
        FROM member_requests
        JOIN users ON users.id = member_requests.requested_by
        LEFT JOIN acquisition_requests ON acquisition_requests.id = member_requests.acquisition_request_id
    """

    def create_member_request(
        self, user_id: int, kind: str, target_key: str, title: str,
        params: dict[str, Any], detail: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """A reader's request, pending. Asking again for what they already
        wait on returns that request rather than a second one. Returns the
        request and whether it is new."""
        if kind not in MEMBER_REQUEST_KINDS:
            raise ValueError("Unknown kind of request")
        title = " ".join(str(title or "").split())[:200]
        if not title or not target_key:
            raise ValueError("A request needs something to ask for")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM member_requests WHERE requested_by=? AND target_key=? AND status='pending'",
                (int(user_id), target_key),
            ).fetchone()
            if existing:
                request_id, created = int(existing["id"]), False
            else:
                cursor = connection.execute(
                    """INSERT INTO member_requests(requested_by, kind, target_key, title, params_json,
                                                   detail_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (int(user_id), kind, target_key, title, json.dumps(params, sort_keys=True),
                     json.dumps(detail or {}, sort_keys=True), now, now),
                )
                request_id, created = int(cursor.lastrowid), True
        return self.member_request(request_id), created

    def member_request(self, request_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                f"{self._MEMBER_REQUEST_SELECT} WHERE member_requests.id=?", (int(request_id),)
            ).fetchone()
        return self._member_request_record(row) if row else None

    def member_requests(
        self, *, user_id: int | None = None, statuses: Sequence[str] | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Newest first: one reader's, or everyone's for the admin."""
        clauses, params = [], []
        if user_id is not None:
            clauses.append("member_requests.requested_by=?")
            params.append(int(user_id))
        if statuses:
            clauses.append(f"member_requests.status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"{self._MEMBER_REQUEST_SELECT} {where} ORDER BY member_requests.id DESC LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
        return [self._member_request_record(row) for row in rows]

    def pending_member_request_count(self) -> int:
        with self._connect() as connection:
            # Waiting on the admin: new, or approved and not carried out.
            return int(connection.execute(
                "SELECT COUNT(*) FROM member_requests WHERE status IN ('pending', 'failed')"
            ).fetchone()[0])

    def claim_member_request(self, request_id: int, decided_by: int, status: str,
                             reason: str | None = None) -> dict[str, Any]:
        """Decide a pending request: approved, declined, or (by the one who
        asked) cancelled. Only a waiting request can be decided -- pending,
        or failed, which can be approved again once whatever broke is fixed
        -- and only once: two taps of Approve, or Approve racing Cancel,
        leave one winner, and the other is told it was already decided."""
        if status not in ("approved", "declined", "cancelled"):
            raise ValueError("A request is approved, declined or cancelled")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE member_requests SET status=?, decided_by=?, decided_at=?, decline_reason=?,
                          failure=NULL, updated_at=?
                   WHERE id=? AND status IN ('pending', 'failed')""",
                (status, int(decided_by), now, (str(reason).strip()[:300] or None) if reason else None, now,
                 int(request_id)),
            )
            if cursor.rowcount == 0:
                exists = connection.execute("SELECT status FROM member_requests WHERE id=?", (int(request_id),)).fetchone()
                if exists is None:
                    raise LookupError("That request does not exist")
                raise ValueError(f"That request was already {exists['status']}")
        return self.member_request(request_id)

    # ---- Notifications (schema 56) -------------------------------------------

    # Cleared rows are kept a while (a Clear all is not a delete the moment it
    # is tapped) and nothing is kept for ever: a profile's bell is a few
    # hundred rows at most.
    NOTIFICATIONS_KEEP_CLEARED_DAYS = 30
    NOTIFICATIONS_KEEP_DAYS = 90
    NOTIFICATIONS_KEEP_ROWS = 200
    NOTIFICATION_DISMISSALS_KEEP = 500

    def notification_mark(self, name: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM notification_marks WHERE name=?", (name,)).fetchone()
        return str(row["value"]) if row else None

    def set_notification_mark(self, name: str, value: str) -> None:
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO notification_marks(name, value) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (name, str(value)),
            )

    def arrivals_between(self, after: str, until: str) -> list[dict[str, Any]]:
        """Every issue a download delivered after one moment and up to
        another, oldest first: the download's own issue, and each further
        issue a pack delivered."""
        with self._connect() as connection:
            rows = connection.execute(
                """WITH arrived(job_id, imported_at) AS (
                       SELECT job_id, imported_at FROM acquisition_downloads
                        WHERE status='imported' AND imported_at > ? AND imported_at <= ?
                       UNION
                       SELECT job_id, imported_at FROM acquisition_download_imports
                        WHERE imported_at > ? AND imported_at <= ?
                   )
                   SELECT arrived.imported_at, acquisition_jobs.id AS job_id, acquisition_jobs.request_id,
                          issues.id AS issue_id, issues.issue_number, issues.title AS issue_title,
                          series_runs.id AS series_run_id, series_runs.canonical_title, series_runs.start_year,
                          (SELECT MAX(file_id) FROM file_issue_links WHERE issue_id=issues.id) AS file_id
                     FROM arrived
                     JOIN acquisition_jobs ON acquisition_jobs.id = arrived.job_id
                     JOIN issues ON issues.id = acquisition_jobs.issue_id
                     JOIN series_runs ON series_runs.id = issues.series_run_id
                    ORDER BY arrived.imported_at, acquisition_jobs.id""",
                (after, until, after, until),
            ).fetchall()
        return [{
            "at": row["imported_at"], "jobId": int(row["job_id"]), "requestId": int(row["request_id"]),
            "issueId": int(row["issue_id"]), "number": str(row["issue_number"]), "issueTitle": row["issue_title"],
            "runId": int(row["series_run_id"]), "runTitle": str(row["canonical_title"]),
            "startYear": row["start_year"], "fileId": int(row["file_id"]) if row["file_id"] is not None else None,
        } for row in rows]

    def readers_waiting_on(self, acquisition_request_ids: Sequence[int]) -> dict[int, set[int]]:
        """For each acquisition request, the readers whose approved request
        it carries out -- the ones to tell when its comics arrive."""
        ids = sorted({int(value) for value in acquisition_request_ids})
        if not ids:
            return {}
        wanted = set(ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT member_requests.acquisition_request_id, member_requests.requested_by,
                           json_extract(member_requests.detail_json, '$.acquisitionRequestIds') AS more
                      FROM member_requests JOIN users ON users.id = member_requests.requested_by
                     WHERE member_requests.status='approved' AND users.disabled=0
                       AND (member_requests.acquisition_request_id IN ({', '.join('?' for _ in ids)})
                            OR more IS NOT NULL)""",
                ids,
            ).fetchall()
        audience: dict[int, set[int]] = {}
        for row in rows:
            # The column holds one; an arc's several pulls ride in the detail.
            linked = {int(row["acquisition_request_id"])} if row["acquisition_request_id"] is not None else set()
            linked |= {int(value) for value in (_load_json(row["more"], []) or []) if str(value).isdigit()}
            for request_id in linked & wanted:
                audience.setdefault(request_id, set()).add(int(row["requested_by"]))
        return audience

    def record_notification(
        self, user_id: int, kind: str, group_key: str, payload: dict[str, Any], *,
        at: str | None = None, merge: bool = False,
    ) -> int:
        """Tell a profile something. With `merge`, a row about the same thing
        that is still unread takes the news instead of a second row: its
        `items` gain the new ones (once each, by `key`) and it moves to the
        top. Returns the row's id."""
        at = at or _utc_now()
        with self._write_lock, self._connect() as connection:
            existing = connection.execute(
                """SELECT id, payload_json, updated_at FROM notifications
                    WHERE user_id=? AND group_key=? AND read_at IS NULL AND cleared_at IS NULL
                    ORDER BY id DESC LIMIT 1""",
                (int(user_id), group_key),
            ).fetchone() if merge else None
            if existing:
                merged = {**_load_json(existing["payload_json"], {}), **payload}
                items = list(_load_json(existing["payload_json"], {}).get("items") or [])
                seen = {item.get("key") for item in items}
                items += [item for item in payload.get("items") or [] if item.get("key") not in seen]
                merged["items"] = items
                connection.execute(
                    "UPDATE notifications SET payload_json=?, updated_at=? WHERE id=?",
                    (json.dumps(merged, sort_keys=True), max(str(existing["updated_at"]), at), int(existing["id"])),
                )
                notification_id = int(existing["id"])
            else:
                notification_id = int(connection.execute(
                    """INSERT INTO notifications(user_id, kind, group_key, payload_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (int(user_id), kind, group_key, json.dumps(payload, sort_keys=True), at, at),
                ).lastrowid)
            self._prune_notifications(connection, int(user_id))
        return notification_id

    def _prune_notifications(self, connection: sqlite3.Connection, user_id: int) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        cleared_before = (now - dt.timedelta(days=self.NOTIFICATIONS_KEEP_CLEARED_DAYS)).isoformat()
        older_than = (now - dt.timedelta(days=self.NOTIFICATIONS_KEEP_DAYS)).isoformat()
        connection.execute(
            "DELETE FROM notifications WHERE user_id=? AND (cleared_at < ? OR updated_at < ?)",
            (user_id, cleared_before, older_than),
        )
        connection.execute(
            """DELETE FROM notifications WHERE user_id=? AND id NOT IN (
                   SELECT id FROM notifications WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT ?)""",
            (user_id, user_id, self.NOTIFICATIONS_KEEP_ROWS),
        )

    def notifications_for(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """A profile's bell, newest first, without what it cleared."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM notifications WHERE user_id=? AND cleared_at IS NULL
                    ORDER BY updated_at DESC, id DESC LIMIT ?""",
                (int(user_id), int(limit)),
            ).fetchall()
        return [{
            "id": int(row["id"]), "kind": row["kind"], "groupKey": row["group_key"],
            "payload": _load_json(row["payload_json"], {}), "createdAt": row["created_at"],
            "updatedAt": row["updated_at"], "readAt": row["read_at"],
        } for row in rows]

    def _own_notifications(self, user_id: int, ids: Sequence[int] | None, *, clear: bool) -> int:
        """Mark a profile's rows read, or clear them: these, or all of them.
        Cleared is read too, so nothing counts it as new again."""
        now = _utc_now()
        column = "cleared_at" if clear else "read_at"
        sets, params = ("cleared_at=?, read_at=COALESCE(read_at, ?)", [now, now]) if clear else ("read_at=?", [now])
        sql = f"UPDATE notifications SET {sets} WHERE user_id=? AND {column} IS NULL"
        params.append(int(user_id))
        if ids is not None:
            ids = [int(value) for value in ids]
            if not ids:
                return 0
            sql += f" AND id IN ({', '.join('?' for _ in ids)})"
            params += ids
        with self._write_lock, self._connect() as connection:
            return connection.execute(sql, params).rowcount

    def mark_notifications_read(self, user_id: int, ids: Sequence[int] | None = None) -> int:
        return self._own_notifications(user_id, ids, clear=False)

    def clear_notifications(self, user_id: int, ids: Sequence[int] | None = None) -> int:
        return self._own_notifications(user_id, ids, clear=True)

    def notification_dismissals(self, user_id: int) -> list[str]:
        with self._connect() as connection:
            return [str(row["key"]) for row in connection.execute(
                "SELECT key FROM notification_dismissals WHERE user_id=? ORDER BY dismissed_at", (int(user_id),))]

    def change_notification_dismissals(
        self, user_id: int, add: Sequence[str] = (), remove: Sequence[str] = (),
    ) -> list[str]:
        """Dismiss things that need attention, or forget dismissals of things
        that are gone (their keys are reused, and a stale dismissal would
        hide a real future failure)."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            connection.executemany(
                """INSERT INTO notification_dismissals(user_id, key, dismissed_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id, key) DO NOTHING""",
                [(int(user_id), str(key)[:200], now) for key in add if str(key or "").strip()],
            )
            connection.executemany(
                "DELETE FROM notification_dismissals WHERE user_id=? AND key=?",
                [(int(user_id), str(key)[:200]) for key in remove],
            )
            # A profile keeps its newest few hundred; the rest were about
            # things long gone.
            connection.execute(
                """DELETE FROM notification_dismissals WHERE user_id=? AND key NOT IN (
                       SELECT key FROM notification_dismissals WHERE user_id=?
                        ORDER BY dismissed_at DESC, rowid DESC LIMIT ?)""",
                (int(user_id), int(user_id), self.NOTIFICATION_DISMISSALS_KEEP),
            )
        return self.notification_dismissals(user_id)

    def add_member_request_detail(self, request_id: int, facts: dict[str, Any]) -> dict[str, Any] | None:
        """Merge what was learned about a request after it was made -- its
        rating, genres, synopsis -- into what the queue shows."""
        with self._write_lock, self._connect() as connection:
            row = connection.execute("SELECT detail_json FROM member_requests WHERE id=?", (int(request_id),)).fetchone()
            if row is None:
                return None
            detail = {**_load_json(row["detail_json"], {}), **facts}
            connection.execute(
                "UPDATE member_requests SET detail_json=?, updated_at=? WHERE id=?",
                (json.dumps(detail, sort_keys=True), _utc_now(), int(request_id)),
            )
        return self.member_request(request_id)

    def record_member_request_outcome(
        self, request_id: int, *, acquisition_request_id: int | None = None,
        series_run_id: int | None = None, failure: str | None = None,
    ) -> dict[str, Any]:
        """What an approved request became -- or why it could not be done."""
        with self._write_lock, self._connect() as connection:
            if failure:
                connection.execute(
                    "UPDATE member_requests SET status='failed', failure=?, updated_at=? WHERE id=?",
                    (str(failure)[:500], _utc_now(), int(request_id)),
                )
            else:
                connection.execute(
                    """UPDATE member_requests SET acquisition_request_id=?, series_run_id=?, updated_at=?
                       WHERE id=?""",
                    (acquisition_request_id, series_run_id, _utc_now(), int(request_id)),
                )
        return self.member_request(request_id)

    def reading_activity(self, user_id: int, *, history_limit: int = 24) -> dict[str, Any]:
        """What a profile has read: the numbers for its page, and its history, newest first.

        Pages read counts a finished comic whole and a started one up to the
        page it is on -- the place is all that is kept, not a log of turns.
        """
        year = _utc_now()[:4]
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS started,
                          SUM(CASE WHEN reading_progress.finished_at IS NOT NULL THEN 1 ELSE 0 END) AS finished,
                          SUM(CASE WHEN reading_progress.finished_at IS NOT NULL
                                   THEN reading_progress.page_count ELSE reading_progress.page + 1 END) AS pages,
                          SUM(CASE WHEN substr(reading_progress.finished_at, 1, 4)=? THEN 1 ELSE 0 END) AS this_year,
                          COUNT(DISTINCT file_identities.series_run_id) AS runs
                   FROM reading_progress
                   JOIN files ON files.id=reading_progress.file_id AND files.present=1
                   LEFT JOIN file_identities ON file_identities.file_id=files.id
                   WHERE reading_progress.user_id=?""",
                (year, int(user_id)),
            ).fetchone()
            rated = connection.execute(
                "SELECT (SELECT COUNT(*) FROM issue_ratings WHERE user_id=?) + (SELECT COUNT(*) FROM series_run_ratings WHERE user_id=?)",
                (int(user_id), int(user_id)),
            ).fetchone()[0]
        started = int(row["started"] or 0)
        finished = int(row["finished"] or 0)
        return {
            "stats": {
                "issuesRead": finished, "inProgress": started - finished, "runs": int(row["runs"] or 0),
                "pagesRead": int(row["pages"] or 0), "finishedThisYear": int(row["this_year"] or 0),
                "rated": int(rated or 0), "year": int(year),
            },
            "history": self.recent_reading(history_limit, user_id=user_id),
        }

    def touch_user(self, user_id: int) -> None:
        with self._write_lock, self._connect() as connection:
            connection.execute("UPDATE users SET last_seen_at=? WHERE id=?", (_utc_now(), int(user_id)))

    def user_prefs(self, user_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT prefs_json FROM user_prefs WHERE user_id=?", (int(user_id),)).fetchone()
        prefs = _load_json(row["prefs_json"], {}) if row else {}
        return prefs if isinstance(prefs, dict) else {}

    def set_user_prefs(self, user_id: int, prefs: dict[str, Any]) -> dict[str, Any]:
        """Replace a profile's settings. The app decides which keys exist; the store bounds the size."""
        if not isinstance(prefs, dict):
            raise ValueError("Settings are a set of named values")
        text = json.dumps(prefs, separators=(",", ":"), sort_keys=True)
        if len(text.encode()) > USER_PREFS_MAX_BYTES:
            raise ValueError("Those settings are too large to keep")
        with self._write_lock, self._connect() as connection:
            if connection.execute("SELECT 1 FROM users WHERE id=?", (int(user_id),)).fetchone() is None:
                raise LookupError("That profile does not exist")
            connection.execute(
                """INSERT INTO user_prefs(user_id, prefs_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET prefs_json=excluded.prefs_json, updated_at=excluded.updated_at""",
                (int(user_id), text, _utc_now()),
            )
        return self.user_prefs(user_id)

    def set_rating(self, kind: str, target_id: int, rating: int | None, *, user_id: int) -> dict[str, Any]:
        """Rate an issue or a run, or take the rating back.

        `None` clears it, which is how a star control undoes itself: pressing
        the star you already gave means "actually, no", not "three again".
        """
        if kind not in ("issue", "series"):
            raise ValueError("Only an issue or a run can be rated")
        table, column = (
            ("issue_ratings", "issue_id") if kind == "issue" else ("series_run_ratings", "series_run_id")
        )
        if rating is not None and (not isinstance(rating, int) or isinstance(rating, bool)
                                   or not 1 <= rating <= 5):
            raise ValueError("A rating is one to five stars")
        with self._write_lock, self._connect() as connection:
            exists = connection.execute(
                f"SELECT 1 FROM {'issues' if kind == 'issue' else 'series_runs'} WHERE id=?", (target_id,)
            ).fetchone()
            if exists is None:
                raise LookupError("That is not in the catalog")
            if rating is None:
                connection.execute(f"DELETE FROM {table} WHERE user_id=? AND {column}=?", (int(user_id), target_id))
            else:
                connection.execute(
                    f"""INSERT INTO {table}(user_id, {column}, rating, updated_at) VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, {column}) DO UPDATE SET
                            rating=excluded.rating, updated_at=excluded.updated_at""",
                    (int(user_id), target_id, rating, _utc_now()),
                )
        return {"id": str(target_id), "yourRating": rating}

    def clear_series_backdrop(self, series_run_id: int) -> None:
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM series_backdrop_preferences WHERE series_run_id=?", (series_run_id,)
            )

    def set_file_cover_preference(
        self, file_id: int, source: str, cover_url: str | None = None
    ) -> dict[str, Any]:
        if source not in {"auto", "file", "provider", "upload"}:
            raise ValueError("Unsupported cover source")
        workbench = self.get_file_workbench(file_id)
        if source == "file" and not any(option["source"] == "file" for option in workbench["covers"]["options"]):
            raise ValueError("This file does not contain a readable embedded cover")
        if source == "provider" and not any(
            option["source"] == "provider" and option["url"] == cover_url
            for option in workbench["covers"]["options"]
        ):
            raise ValueError("The selected provider cover is not part of this file's metadata evidence")
        if source == "upload":
            cover_url = f"/api/v1/files/{file_id}/cover/image"
        elif source in {"auto", "file"}:
            cover_url = None
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO file_cover_preferences(file_id, source, cover_url, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(file_id) DO UPDATE SET
                       source=excluded.source, cover_url=excluded.cover_url, updated_at=excluded.updated_at""",
                (file_id, source, cover_url, _utc_now()),
            )
        return self.get_file_workbench(file_id)

    def update_file_metadata(
        self,
        file_id: int,
        fields: dict[str, Any],
        locked_fields: list[str] | None = None,
        match_source: str | None = None,
        match_source_id: str | None = None,
        match_candidate: dict[str, Any] | None = None,
        action: str = "edit",
    ) -> dict[str, Any]:
        allowed = {
            "seriesTitle", "title", "subtitle", "recordType", "issueNumber", "volumeNumber",
            "editionKind", "publisher", "publicationYear", "isbn", "format",
        }
        cleaned = {key: value for key, value in fields.items() if key in allowed}
        if not cleaned:
            raise ValueError("At least one editable metadata field is required")
        if cleaned.get("recordType") not in {None, "issue", "edition"}:
            raise ValueError("recordType must be issue or edition")
        for key in ("volumeNumber", "publicationYear"):
            if cleaned.get(key) not in {None, ""}:
                try:
                    cleaned[key] = int(cleaned[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be a number") from exc
            elif key in cleaned:
                cleaned[key] = None
        locked = [field for field in (locked_fields or list(cleaned)) if field in cleaned]
        now = _utc_now()
        source_run_id: int | None = None
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                """SELECT files.parsed_json, files.result_json,
                          file_metadata_overrides.match_candidate_json
                   FROM files LEFT JOIN file_metadata_overrides ON file_metadata_overrides.file_id=files.id
                   WHERE files.id=?""",
                (file_id,),
            ).fetchone()
            if not row:
                raise ValueError("Library file was not found")
            source_identity = connection.execute(
                "SELECT series_run_id FROM file_identities WHERE file_id=?", (file_id,)
            ).fetchone()
            source_run_id = int(source_identity["series_run_id"]) if source_identity else None
            connection.execute(
                """INSERT INTO file_metadata_overrides(
                       file_id, fields_json, locked_fields_json, match_source, match_source_id,
                       match_candidate_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(file_id) DO UPDATE SET
                       fields_json=excluded.fields_json, locked_fields_json=excluded.locked_fields_json,
                       match_source=excluded.match_source, match_source_id=excluded.match_source_id,
                       match_candidate_json=COALESCE(excluded.match_candidate_json, file_metadata_overrides.match_candidate_json),
                       updated_at=excluded.updated_at""",
                (
                    file_id, _json(cleaned), _json(locked), match_source, match_source_id,
                    _json(match_candidate) if match_candidate else None, now, now,
                ),
            )
            connection.execute(
                "INSERT INTO metadata_override_history(file_id, action, fields_json, created_at) VALUES (?, ?, ?, ?)",
                (file_id, action, _json(cleaned), now),
            )
        effective_candidate = match_candidate or _load_json(row["match_candidate_json"], None)
        effective_result = _load_json(row["result_json"], {})
        if effective_candidate:
            effective_result = {**effective_result, "recommendation": effective_candidate}
        self._reconcile_file_identity(file_id, _load_json(row["parsed_json"], {}), effective_result, cleaned)
        series_healing = self._heal_connected_placeholder_run(
            file_id, source_run_id, cleaned.get("seriesTitle")
        )
        workbench = self.get_file_workbench(file_id)
        if series_healing:
            workbench["seriesHealing"] = series_healing
        return workbench

    def apply_file_match(self, file_id: int, candidate_key: str) -> dict[str, Any]:
        workbench = self.get_file_workbench(file_id)
        candidate = next((item for item in workbench["candidates"] if item["key"] == candidate_key), None)
        if not candidate:
            raise ValueError("Metadata candidate was not found")
        fields = self._candidate_selection_fields(candidate, workbench["current"])
        return self.update_file_metadata(
            file_id, fields, list(fields), str(candidate.get("source") or "Catalog candidate"),
            str(candidate.get("source_id") or "") or None, candidate, action="fix_match",
        )

    def reset_file_metadata(self, file_id: int) -> dict[str, Any]:
        with self._write_lock, self._connect() as connection:
            row = connection.execute("SELECT parsed_json, result_json FROM files WHERE id=?", (file_id,)).fetchone()
            if not row:
                raise ValueError("Library file was not found")
            connection.execute("DELETE FROM file_metadata_overrides WHERE file_id=?", (file_id,))
            connection.execute(
                "INSERT INTO metadata_override_history(file_id, action, fields_json, created_at) VALUES (?, 'reset', '{}', ?)",
                (file_id, _utc_now()),
            )
        self._reconcile_file_identity(
            file_id, _load_json(row["parsed_json"], {}), _load_json(row["result_json"], {}), {}
        )
        return self.get_file_workbench(file_id)

    # ---- Age ratings -------------------------------------------------------

    def runs_needing_rating(self, *, limit: int = 20, stale_before: str | None = None) -> list[int]:
        """Runs whose rating has not been looked for, or not since `stale_before`
        -- never one the admin has rated themselves."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id FROM series_runs
                   WHERE age_rating_override IS NULL
                     AND (age_rating_checked_at IS NULL OR (? IS NOT NULL AND age_rating_checked_at < ?))
                   ORDER BY age_rating_checked_at IS NOT NULL, id LIMIT ?""",
                (stale_before, stale_before, int(limit)),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def record_run_rating(self, series_run_id: int, rating: str | None, source: str | None, note: str | None) -> None:
        """What looking for a run's rating found -- or that nothing was, which
        is recorded too, so the run is not asked about again straight away."""
        if rating is not None and rating not in content_rating.RATINGS:
            raise ValueError("Unknown rating")
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE series_runs SET age_rating=?, age_rating_source=?, age_rating_note=?,
                          age_rating_checked_at=? WHERE id=?""",
                (rating, source if rating else None, (note or None) if rating else None, _utc_now(), int(series_run_id)),
            )

    def note_rating_check_failed(self, series_run_id: int, error: str) -> None:
        """A lookup that could not be made: the run keeps whatever rating it
        has, is marked checked so it goes to the back of the queue, and -- if
        it has no rating -- says why in its note."""
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE series_runs
                      SET age_rating_checked_at=?,
                          age_rating_note=CASE WHEN age_rating IS NULL THEN ? ELSE age_rating_note END
                    WHERE id=?""",
                (_utc_now(), f"Couldn't check: {str(error or '').strip()[:160]}", int(series_run_id)),
            )

    def set_run_rating_override(self, series_run_id: int, rating: str | None) -> None:
        """The admin's own rating for a run, or None to go back to what was found."""
        if rating is not None and rating not in content_rating.RATINGS:
            raise ValueError("Choose one of the ratings")
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE series_runs SET age_rating_override=?, updated_at=? WHERE id=?",
                (rating, _utc_now(), int(series_run_id)),
            )
            if cursor.rowcount == 0:
                raise LookupError("That run is not in the catalog")

    def set_run_rating_overrides(self, series_run_ids: list[int], rating: str | None) -> int:
        """The admin's rating for many runs at once, or None to send them all
        back to what was found. All or nothing: one unknown run refuses the lot,
        so a stale list never rates half of what was chosen. Returns how many."""
        if rating is not None and rating not in content_rating.RATINGS:
            raise ValueError("Choose one of the ratings")
        ids = sorted({int(run_id) for run_id in series_run_ids})
        if not ids:
            raise ValueError("Choose at least one run")
        with self._write_lock, self._connect() as connection:
            marks = ",".join("?" * len(ids))
            found = {int(row["id"]) for row in connection.execute(
                f"SELECT id FROM series_runs WHERE id IN ({marks})", ids)}
            if len(found) != len(ids):
                raise LookupError("Some of those runs are no longer in the catalog")
            connection.execute(
                f"UPDATE series_runs SET age_rating_override=?, updated_at=? WHERE id IN ({marks})",
                (rating, _utc_now(), *ids),
            )
        return len(ids)

    def forget_unfound_ratings(self) -> int:
        """Look again at every run nothing was found for -- when a new way of
        finding ratings is turned on. Returns how many."""
        with self._write_lock, self._connect() as connection:
            return connection.execute(
                "UPDATE series_runs SET age_rating_checked_at=NULL WHERE age_rating IS NULL AND age_rating_override IS NULL"
            ).rowcount

    def run_age_ratings(self) -> dict[int, str | None]:
        """Every run's rating as it applies: the admin's, else what was found."""
        with self._connect() as connection:
            return {int(row["id"]): row["rating"] for row in connection.execute(
                "SELECT id, COALESCE(age_rating_override, age_rating) AS rating FROM series_runs")}

    def rating_summary(self) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS runs,
                          SUM(COALESCE(age_rating_override, age_rating) IS NOT NULL) AS rated,
                          SUM(age_rating_override IS NOT NULL) AS by_admin,
                          SUM(age_rating_override IS NULL AND age_rating_source='metron') AS by_metron,
                          SUM(age_rating_override IS NULL AND age_rating_source='cover') AS by_cover,
                          SUM(age_rating_checked_at IS NULL AND age_rating_override IS NULL) AS waiting
                   FROM series_runs"""
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("runs", "rated", "by_admin", "by_metron", "by_cover", "waiting")}

    def run_for_file(self, file_id: int) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT series_run_id FROM file_identities WHERE file_id=?", (int(file_id),)).fetchone()
        return int(row["series_run_id"]) if row and row["series_run_id"] is not None else None

    def run_for_issue(self, issue_id: int) -> int | None:
        with self._connect() as connection:
            row = connection.execute("SELECT series_run_id FROM issues WHERE id=?", (int(issue_id),)).fetchone()
        return int(row["series_run_id"]) if row and row["series_run_id"] is not None else None

    def run_for_path(self, path: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT file_identities.series_run_id FROM files
                   JOIN file_identities ON file_identities.file_id = files.id WHERE files.path=?""", (str(path),)).fetchone()
        return int(row["series_run_id"]) if row and row["series_run_id"] is not None else None

    def newest_file_of_run(self, series_run_id: int) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT files.id FROM files JOIN file_identities ON file_identities.file_id = files.id
                   WHERE file_identities.series_run_id=? ORDER BY files.id DESC LIMIT 1""", (int(series_run_id),)).fetchone()
        return int(row["id"]) if row else None

    def runs_by_provider_series(self, provider: str) -> dict[str, int]:
        """Every run with a confirmed id at a provider: provider series id -> run."""
        with self._connect() as connection:
            return {
                str(row["provider_id"]): int(row["series_run_id"])
                for row in connection.execute(
                    "SELECT provider_id, series_run_id FROM series_provider_ids WHERE provider=? AND confirmed=1",
                    (provider,),
                )
            }

    def confirmed_series_provider_ids(self, series_run_id: int) -> dict[str, str]:
        """A run's confirmed provider ids, by provider."""
        with self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone():
                raise LookupError("Series run was not found")
            return {
                row["provider"]: str(row["provider_id"])
                for row in connection.execute(
                    """SELECT provider, provider_id FROM series_provider_ids
                       WHERE series_run_id=? AND confirmed=1""",
                    (series_run_id,),
                )
            }

    def issue_provider_ids(self, issue_id: int) -> dict[str, str]:
        """An issue's provider ids, by provider.

        Unlike a run's, these have no `confirmed` column: an issue id is written
        by a scan or an issue sync that already knows which run it belongs to,
        so there is nothing here for a human to confirm and nothing to filter.
        """
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM issues WHERE id=?", (issue_id,)).fetchone():
                raise LookupError("Issue was not found")
            return {
                row["provider"]: str(row["provider_id"])
                for row in connection.execute(
                    "SELECT provider, provider_id FROM issue_provider_ids WHERE issue_id=?",
                    (issue_id,),
                )
            }

    def get_series_sync_context(self, series_run_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            series = connection.execute("SELECT * FROM series_runs WHERE id=?", (series_run_id,)).fetchone()
            if not series:
                raise ValueError("Canonical series was not found")
            gcd_series = connection.execute(
                """SELECT provider_id, api_url FROM series_provider_ids
                   WHERE series_run_id=? AND provider='gcd' AND confirmed=1""",
                (series_run_id,),
            ).fetchone()
            provider_series_ids = {
                row["provider"]: {"id": row["provider_id"], "apiUrl": row["api_url"]}
                for row in connection.execute(
                    """SELECT provider, provider_id, api_url FROM series_provider_ids
                       WHERE series_run_id=? AND confirmed=1""",
                    (series_run_id,),
                )
            }
            known_issue_ids = [
                row["provider_id"]
                for row in connection.execute(
                    """SELECT issue_provider_ids.provider_id
                       FROM issue_provider_ids
                       JOIN issues ON issues.id=issue_provider_ids.issue_id
                       WHERE issues.series_run_id=? AND issue_provider_ids.provider='gcd'""",
                    (series_run_id,),
                )
            ]
            gcd_issue_entries = [
                {
                    "number": row["issue_number"], "provider_id": row["provider_id"],
                    "api_url": row["api_url"], "title": row["title"],
                    "publication_year": row["publication_year"],
                    "publication_date": row["publication_date"],
                }
                for row in connection.execute(
                    """SELECT issues.issue_number, issues.title, issues.publication_year,
                              issues.publication_date,
                              issue_provider_ids.provider_id, issue_provider_ids.api_url
                       FROM issue_provider_ids
                       JOIN issues ON issues.id=issue_provider_ids.issue_id
                       WHERE issues.series_run_id=? AND issue_provider_ids.provider='gcd'
                       ORDER BY issues.id""",
                    (series_run_id,),
                )
            ]
            aliases = [
                row["alias"] for row in connection.execute(
                    "SELECT alias FROM series_aliases WHERE series_run_id=? ORDER BY confirmed DESC, id",
                    (series_run_id,),
                )
            ]
            owned_issue_numbers = [
                row["issue_number"]
                for row in connection.execute(
                    """SELECT issues.issue_number
                       FROM issues
                       WHERE issues.series_run_id=? AND (
                           EXISTS(
                               SELECT 1 FROM file_issue_links
                               JOIN files ON files.id=file_issue_links.file_id
                               WHERE file_issue_links.issue_id=issues.id AND files.present=1
                           ) OR EXISTS(
                               SELECT 1 FROM edition_coverage_claims
                               JOIN file_edition_links ON file_edition_links.edition_id=edition_coverage_claims.edition_id
                               JOIN files ON files.id=file_edition_links.file_id
                               WHERE edition_coverage_claims.issue_id=issues.id
                                 AND edition_coverage_claims.resolution_status='resolved'
                                 AND edition_coverage_claims.relation_kind='full_issue'
                                 AND files.present=1
                                 AND NOT EXISTS(
                                     SELECT 1 FROM edition_coverage_overrides
                                     WHERE edition_coverage_overrides.edition_id=edition_coverage_claims.edition_id
                                       AND edition_coverage_overrides.series_run_id=issues.series_run_id
                                       AND edition_coverage_overrides.issue_number=issues.issue_number
                                       AND edition_coverage_overrides.action='remove'
                                 )
                           ) OR EXISTS(
                               SELECT 1 FROM edition_coverage_overrides
                               JOIN file_edition_links ON file_edition_links.edition_id=edition_coverage_overrides.edition_id
                               JOIN files ON files.id=file_edition_links.file_id
                               WHERE edition_coverage_overrides.series_run_id=issues.series_run_id
                                 AND edition_coverage_overrides.issue_number=issues.issue_number
                                 AND edition_coverage_overrides.action='add'
                                 AND files.present=1
                           )
                       )""",
                    (series_run_id,),
                )
            ]
        return {
            "id": int(series["id"]), "title": series["canonical_title"],
            "year": series["start_year"], "publisher": series["publisher"],
            "format": series["format"] or "comic",
            # A year-qualified canonical key exists specifically because the
            # normalized title belongs to more than one publication era.  In
            # that case provider matching must not fall back to another era
            # merely because it has the same title and publisher.
            "strictYear": "::year:" in str(series["canonical_key"] or ""),
            "aliases": aliases, "knownGcdIssueIds": known_issue_ids,
            "gcdSeriesId": gcd_series["provider_id"] if gcd_series else None,
            "gcdSeriesApiUrl": gcd_series["api_url"] if gcd_series else None,
            "metronSeriesId": (provider_series_ids.get("metron") or {}).get("id"),
            "comicVineVolumeId": (provider_series_ids.get("comic_vine") or {}).get("id"),
            "providerSeriesIds": provider_series_ids,
            "gcdIssueEntries": gcd_issue_entries,
            "ownedIssueNumbers": owned_issue_numbers,
        }

    def apply_issue_list(
        self,
        series_run_id: int,
        provider: str,
        provider_series_id: str,
        api_url: str,
        entries: list[dict[str, Any]],
        status: str = "complete_to_date",
        detail: str | None = None,
        source: str = "verified through owned issue ids",
        end_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"partial", "complete", "complete_to_date"}:
            raise ValueError("Unsupported issue catalog status")
        evidence = end_evidence or {"state": "unknown", "year": None}
        if evidence.get("state") not in {"ongoing", "ended", "unknown"}:
            raise ValueError("Unsupported publication run end evidence")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            series = connection.execute(
                "SELECT id, canonical_title, start_year FROM series_runs WHERE id=?",
                (series_run_id,),
            ).fetchone()
            if not series:
                raise ValueError("Canonical series was not found")
            provider_start_year = _issue_list_start_year(entries)
            local_start_year = self._local_run_start_year(connection, series_run_id)
            expected_start_year = local_start_year or _valid_year(series["start_year"])
            if not _years_compatible(expected_start_year, provider_start_year):
                raise ValueError(
                    f"{provider.replace('_', ' ').title()} returned a {provider_start_year} "
                    f"publication run, but local opening issues identify "
                    f"{series['canonical_title']} as {expected_start_year}. "
                    "Flipparr left the catalog unchanged."
                )
            connection.execute(
                """INSERT INTO series_provider_ids(
                       series_run_id, provider, provider_id, api_url, confirmed, source, updated_at
                   ) VALUES (?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(series_run_id, provider) DO UPDATE SET
                       provider_id=excluded.provider_id, api_url=excluded.api_url,
                       confirmed=1, source=excluded.source, updated_at=excluded.updated_at""",
                (series_run_id, provider, provider_series_id, api_url, source, now),
            )
            for entry in entries:
                issue_number = str(entry["number"])
                connection.execute(
                    """INSERT INTO issues(
                           series_run_id, issue_number, title, publication_year,
                           publication_date, cover, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(series_run_id, issue_number) DO UPDATE SET
                           title=COALESCE(excluded.title, issues.title),
                           publication_year=COALESCE(excluded.publication_year, issues.publication_year),
                           publication_date=COALESCE(excluded.publication_date, issues.publication_date),
                           cover=COALESCE(excluded.cover, issues.cover),
                           updated_at=excluded.updated_at""",
                    (
                        series_run_id, issue_number, entry.get("title"),
                        entry.get("publication_year"), entry.get("publication_date"),
                        entry.get("cover"), now, now,
                    ),
                )
                issue_id = int(connection.execute(
                    "SELECT id FROM issues WHERE series_run_id=? AND issue_number=?",
                    (series_run_id, issue_number),
                ).fetchone()["id"])
                if entry.get("provider_id"):
                    self._upsert_issue_provider_id(
                        connection, issue_id, provider, str(entry["provider_id"]),
                        entry.get("api_url"), now,
                    )
                active_requests = connection.execute(
                    """SELECT acquisition_requests.id
                       FROM acquisition_requests
                       WHERE acquisition_requests.status='open'
                         -- A request that covers named issues -- one behind a
                         -- replacement, or one comic pulled from Discover --
                         -- covers those issues, not the run they belong to.
                         -- Enrolled like a followed run, replacing one issue
                         -- pulled every missing issue in the run, twice over
                         -- when two replacements were open, and left the
                         -- replacement unable to finish.
                         AND acquisition_requests.coverage='run'
                         AND (
                           (acquisition_requests.scope_type='series'
                            AND acquisition_requests.series_run_id=?)
                           OR
                           (acquisition_requests.scope_type='collection'
                            AND EXISTS(
                                SELECT 1 FROM series_family_memberships
                                WHERE series_family_memberships.series_family_id=
                                      acquisition_requests.series_family_id
                                  AND series_family_memberships.series_run_id=?
                            )
                            AND (
                                acquisition_requests.include_specials=1
                                OR NOT EXISTS(
                                    SELECT 1 FROM story_arc_run_memberships
                                    JOIN story_arcs ON story_arcs.id=
                                         story_arc_run_memberships.story_arc_id
                                    WHERE story_arc_run_memberships.series_run_id=?
                                      AND story_arcs.series_family_id=
                                          acquisition_requests.series_family_id
                                      AND story_arcs.arc_type='specials'
                                )
                            ))
                       )""",
                    (series_run_id, series_run_id, series_run_id),
                ).fetchall()
                connection.executemany(
                    """INSERT OR IGNORE INTO acquisition_request_issues(
                           request_id, issue_id, created_at
                       ) VALUES (?, ?, ?)""",
                    [(int(request["id"]), issue_id, now) for request in active_requests],
                )
            connection.execute(
                """INSERT INTO issue_catalog_status(
                       series_run_id, status, provider, provider_series_id, issue_count,
                       last_synced_at, detail, error,
                       run_end_status, end_year, end_year_provider
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                   ON CONFLICT(series_run_id) DO UPDATE SET
                       -- Silence preserves. A provider that does not model an
                       -- end year must not walk a finished run back to
                       -- "complete up to today", which is what made a run's
                       -- badge depend on which provider answered last.
                       status=CASE WHEN excluded.run_end_status='unknown'
                                    AND issue_catalog_status.run_end_status='ended'
                                   THEN issue_catalog_status.status
                                   ELSE excluded.status END,
                       provider=excluded.provider,
                       provider_series_id=excluded.provider_series_id,
                       issue_count=excluded.issue_count, last_synced_at=excluded.last_synced_at,
                       detail=excluded.detail, error=NULL,
                       run_end_status=CASE WHEN excluded.run_end_status='unknown'
                                           THEN issue_catalog_status.run_end_status
                                           ELSE excluded.run_end_status END,
                       end_year=CASE WHEN excluded.run_end_status='unknown'
                                     THEN issue_catalog_status.end_year
                                     ELSE excluded.end_year END,
                       end_year_provider=CASE WHEN excluded.run_end_status='unknown'
                                              THEN issue_catalog_status.end_year_provider
                                              ELSE excluded.end_year_provider END""",
                (series_run_id, status, provider, provider_series_id, len(entries), now, detail,
                 evidence.get("state") or "unknown", evidence.get("year"),
                 provider if evidence.get("state") != "unknown" else None),
            )
            publication_years = [
                int(entry["publication_year"])
                for entry in entries
                if entry.get("publication_year") not in {None, ""}
            ]
            if publication_years:
                resolved_start_year = local_start_year or min(publication_years)
                connection.execute(
                    "UPDATE series_runs SET start_year=?, updated_at=? WHERE id=?",
                    (resolved_start_year, now, series_run_id),
                )
        return {
            "seriesId": str(series_run_id), "status": status,
            "issueCount": len(entries), "provider": provider,
            "titleCount": sum(bool(str(entry.get("title") or "").strip()) for entry in entries),
        }

    def rebuild_series_run(self, series_run_id: int) -> dict[str, Any]:
        """Discard what was derived for a run and work it out again.

        Every other repair only adds. A provider refresh fills a blank and
        never corrects a value -- `COALESCE(excluded.title, issues.title)` --
        and a file-level fix touches that file's own record. So a wrong value
        written onto a shared issue survives all of them: a French edition
        filed as Saga #2 left "Numéro 2" as the issue's title, and no refresh
        could take it back off.

        Derived identity is cleared and re-derived from the files present now.
        Manual corrections are not touched -- they live in
        issue_metadata_overrides, which is layered over this at read time --
        and the provider link is kept, so a refresh afterwards refills what
        the provider knows.
        """
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            if not connection.execute(
                "SELECT id FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone():
                raise ValueError("Canonical series was not found")
            # Issues a file invented by landing on the wrong run. Nothing points
            # at them once it leaves: no catalog lists them, no file is theirs,
            # no collection covers them and nobody has corrected them. Left in
            # place they read as issues the run is missing for ever -- the 2011
            # Nightwing grew from 31 to 141 that way. Jobs queued against them
            # go with them through the cascade, which is the point: they want
            # issues this run never had.
            pruned = int(connection.execute(
                """DELETE FROM issues
                    WHERE series_run_id=?
                      AND NOT EXISTS(
                          SELECT 1 FROM issue_provider_ids
                          WHERE issue_provider_ids.issue_id=issues.id
                      )
                      AND NOT EXISTS(
                          SELECT 1 FROM file_issue_links
                          JOIN files ON files.id=file_issue_links.file_id
                          WHERE file_issue_links.issue_id=issues.id AND files.present=1
                      )
                      AND NOT EXISTS(
                          SELECT 1 FROM edition_coverage_claims
                          WHERE edition_coverage_claims.issue_id=issues.id
                      )
                      AND NOT EXISTS(
                          SELECT 1 FROM edition_coverage_overrides
                          WHERE edition_coverage_overrides.series_run_id=issues.series_run_id
                            AND edition_coverage_overrides.issue_number=issues.issue_number
                      )
                      AND NOT EXISTS(
                          SELECT 1 FROM issue_metadata_overrides
                          WHERE issue_metadata_overrides.issue_id=issues.id
                      )""",
                (series_run_id,),
            ).rowcount or 0)
            cleared = int(connection.execute(
                """UPDATE issues SET title=NULL, publication_date=NULL, cover=NULL,
                          updated_at=?
                    WHERE series_run_id=?""",
                (now, series_run_id),
            ).rowcount or 0)
            retained = connection.execute(
                """SELECT DISTINCT issues.id
                     FROM issues
                     JOIN file_issue_links ON file_issue_links.issue_id=issues.id
                     JOIN files ON files.id=file_issue_links.file_id
                    WHERE issues.series_run_id=? AND files.present=1""",
                (series_run_id,),
            ).fetchall()
            rederived = 0
            for issue in retained:
                issue_id = int(issue["id"])
                local = self._local_issue_values(connection, issue_id)
                if not local:
                    continue
                connection.execute(
                    """UPDATE issues SET
                           title=COALESCE(?, title),
                           publication_year=COALESCE(?, publication_year),
                           publication_date=COALESCE(?, publication_date),
                           cover=COALESCE(?, cover), updated_at=?
                       WHERE id=?""",
                    (
                        local.get("title"), local.get("publication_year"),
                        local.get("publication_date"), local.get("cover"),
                        now, issue_id,
                    ),
                )
                rederived += 1
        return {
            "seriesRunId": str(series_run_id),
            "clearedIssues": cleared,
            "prunedIssues": pruned,
            "rederivedFromFiles": rederived,
        }

    def repair_provider_run_mismatch(self, series_run_id: int) -> dict[str, Any]:
        """Remove a provider catalog whose era conflicts with owned opening issues.

        The operation is intentionally conservative: it only runs when files
        for issue #1-#3 provide a start year and the active provider catalog is
        separated from it by more than three years. Owned issues and manual
        corrections are retained; provider-only issues and their queued jobs
        are removed through foreign-key cascades.
        """
        now = _utc_now()
        repaired = False
        removed_issues = 0
        removed_jobs = 0
        provider = None
        local_start_year = None
        provider_start_year = None
        with self._write_lock, self._connect() as connection:
            series = connection.execute(
                "SELECT id, canonical_title, start_year FROM series_runs WHERE id=?",
                (series_run_id,),
            ).fetchone()
            if not series:
                raise ValueError("Canonical series was not found")
            status = connection.execute(
                "SELECT provider, provider_series_id FROM issue_catalog_status WHERE series_run_id=?",
                (series_run_id,),
            ).fetchone()
            local_start_year = self._local_run_start_year(connection, series_run_id)
            if status is None or local_start_year is None or not status["provider"]:
                return {"status": "unchanged", "seriesId": str(series_run_id)}
            provider = str(status["provider"])
            provider_years = [
                # Exact provider dates survive some older hybrid matches even
                # when a local file year has already overlaid publication_year.
                # Prefer that date when determining which era the provider
                # catalog actually describes.
                _valid_year(row["publication_date"]) or _valid_year(row["publication_year"])
                for row in connection.execute(
                    """SELECT issues.issue_number, issues.publication_year, issues.publication_date
                       FROM issues
                       JOIN issue_provider_ids ON issue_provider_ids.issue_id=issues.id
                       WHERE issues.series_run_id=? AND issue_provider_ids.provider=?""",
                    (series_run_id, provider),
                ).fetchall()
                if (_issue_number_value(row["issue_number"]) is not None
                    and 1 <= _issue_number_value(row["issue_number"]) <= 3)
            ]
            provider_years = [year for year in provider_years if year is not None]
            provider_start_year = min(provider_years) if provider_years else None
            if _years_compatible(local_start_year, provider_start_year):
                return {
                    "status": "unchanged", "seriesId": str(series_run_id),
                    "localStartYear": local_start_year,
                    "providerStartYear": provider_start_year,
                }

            removable = connection.execute(
                """SELECT issues.id
                   FROM issues
                   WHERE issues.series_run_id=?
                     AND EXISTS(
                         SELECT 1 FROM issue_provider_ids
                         WHERE issue_provider_ids.issue_id=issues.id
                           AND issue_provider_ids.provider=?
                     )
                     AND NOT EXISTS(
                         SELECT 1 FROM file_issue_links
                         JOIN files ON files.id=file_issue_links.file_id
                         WHERE file_issue_links.issue_id=issues.id AND files.present=1
                     )
                     AND NOT EXISTS(
                         SELECT 1 FROM edition_coverage_claims
                         WHERE edition_coverage_claims.issue_id=issues.id
                     )
                     AND NOT EXISTS(
                         SELECT 1 FROM edition_coverage_overrides
                         WHERE edition_coverage_overrides.series_run_id=issues.series_run_id
                           AND edition_coverage_overrides.issue_number=issues.issue_number
                     )
                     AND NOT EXISTS(
                         SELECT 1 FROM issue_metadata_overrides
                         WHERE issue_metadata_overrides.issue_id=issues.id
                     )
                     AND NOT EXISTS(
                         SELECT 1 FROM issue_provider_ids AS other_provider
                         WHERE other_provider.issue_id=issues.id
                           AND other_provider.provider!=?
                     )""",
                (series_run_id, provider, provider),
            ).fetchall()
            removable_ids = [int(row["id"]) for row in removable]
            if removable_ids:
                placeholders = ",".join("?" for _ in removable_ids)
                removed_jobs = int(connection.execute(
                    f"SELECT COUNT(*) AS count FROM acquisition_jobs WHERE issue_id IN ({placeholders})",
                    removable_ids,
                ).fetchone()["count"])
                connection.execute(
                    f"DELETE FROM issues WHERE id IN ({placeholders})", removable_ids
                )
                removed_issues = len(removable_ids)

            retained = connection.execute(
                """SELECT DISTINCT issues.id
                   FROM issues
                   JOIN file_issue_links ON file_issue_links.issue_id=issues.id
                   JOIN files ON files.id=file_issue_links.file_id
                   WHERE issues.series_run_id=? AND files.present=1""",
                (series_run_id,),
            ).fetchall()
            for issue in retained:
                issue_id = int(issue["id"])
                local = self._local_issue_values(connection, issue_id)
                connection.execute(
                    """UPDATE issues SET
                           title=COALESCE(?, title), publication_year=COALESCE(?, publication_year),
                           publication_date=?, cover=?, updated_at=?
                       WHERE id=?""",
                    (
                        local.get("title"), local.get("publication_year"),
                        local.get("publication_date"), local.get("cover"), now, issue_id,
                    ),
                )
            connection.execute(
                """DELETE FROM issue_provider_ids
                   WHERE provider=? AND issue_id IN(
                       SELECT id FROM issues WHERE series_run_id=?
                   )""",
                (provider, series_run_id),
            )
            connection.execute(
                "DELETE FROM series_provider_ids WHERE series_run_id=? AND provider=?",
                (series_run_id, provider),
            )
            connection.execute(
                "DELETE FROM issue_catalog_status WHERE series_run_id=?", (series_run_id,)
            )
            connection.execute(
                "UPDATE series_runs SET start_year=?, updated_at=? WHERE id=?",
                (local_start_year, now, series_run_id),
            )
            repaired = True
        if repaired:
            self.reconcile_acquisition_jobs()
        return {
            "status": "repaired" if repaired else "unchanged",
            "seriesId": str(series_run_id), "provider": provider,
            "localStartYear": local_start_year,
            "providerStartYear": provider_start_year,
            "removedProviderOnlyIssues": removed_issues,
            "removedQueuedJobs": removed_jobs,
        }

    def retire_stale_provider_run(
        self, source_run_id: int, target_run_id: int
    ) -> dict[str, Any]:
        """Retire a provider-only run after local files were split into the right era.

        This is deliberately stricter than a normal merge. A normal merge would
        copy the source catalog into the target and recreate the exact mismatch
        we are trying to repair. Here the source must be fileless, the target
        must contain owned files, the display titles must agree, and the source
        provider era must conflict with the target's locally observed era.
        Monitoring and one open request are retained, but stale provider issues
        and their jobs are discarded so the target can be enriched afresh.
        """
        if source_run_id == target_run_id:
            raise ValueError("Source and target series must be different")
        now = _utc_now()
        transferred_request_id: int | None = None
        removed_issue_count = 0
        removed_job_count = 0
        with self._write_lock, self._connect() as connection:
            source = connection.execute(
                "SELECT * FROM series_runs WHERE id=?", (source_run_id,)
            ).fetchone()
            target = connection.execute(
                "SELECT * FROM series_runs WHERE id=?", (target_run_id,)
            ).fetchone()
            if not source or not target:
                raise ValueError("One of the canonical series was not found")
            if _normalized(source["canonical_title"]) != _normalized(target["canonical_title"]):
                raise ValueError("Only duplicate titles from different publication eras can be repaired")

            source_owned = int(connection.execute(
                """SELECT
                       (SELECT COUNT(*) FROM file_identities WHERE series_run_id=?) +
                       (SELECT COUNT(*) FROM editions WHERE series_run_id=?) +
                       (SELECT COUNT(*) FROM file_issue_links
                          JOIN issues ON issues.id=file_issue_links.issue_id
                         WHERE issues.series_run_id=?) AS count""",
                (source_run_id, source_run_id, source_run_id),
            ).fetchone()["count"])
            target_owned = int(connection.execute(
                "SELECT COUNT(*) AS count FROM file_identities WHERE series_run_id=?",
                (target_run_id,),
            ).fetchone()["count"])
            if source_owned or not target_owned:
                raise ValueError(
                    "The stale run must contain no local comics and the corrected run must contain local comics"
                )

            status = connection.execute(
                "SELECT provider FROM issue_catalog_status WHERE series_run_id=?",
                (source_run_id,),
            ).fetchone()
            if not status or not status["provider"]:
                raise ValueError("The stale run does not have a provider catalog to retire")
            provider = str(status["provider"])
            provider_years = [
                _valid_year(row["publication_date"]) or _valid_year(row["publication_year"])
                for row in connection.execute(
                    """SELECT issues.issue_number, issues.publication_year, issues.publication_date
                       FROM issues
                       JOIN issue_provider_ids ON issue_provider_ids.issue_id=issues.id
                       WHERE issues.series_run_id=? AND issue_provider_ids.provider=?""",
                    (source_run_id, provider),
                )
                if (_issue_number_value(row["issue_number"]) is not None
                    and 1 <= _issue_number_value(row["issue_number"]) <= 3)
            ]
            provider_years = [year for year in provider_years if year is not None]
            provider_start_year = min(provider_years) if provider_years else _valid_year(source["start_year"])
            target_start_year = self._local_run_start_year(connection, target_run_id)
            if target_start_year is None:
                target_start_year = _valid_year(target["start_year"])
            if _years_compatible(target_start_year, provider_start_year):
                raise ValueError("The provider catalog does not conflict with the corrected local run")

            source_requests = connection.execute(
                """SELECT * FROM acquisition_requests
                   WHERE series_run_id=? AND status='open' ORDER BY id""",
                (source_run_id,),
            ).fetchall()
            target_request = connection.execute(
                """SELECT id FROM acquisition_requests
                   WHERE series_run_id=? AND status='open' ORDER BY id LIMIT 1""",
                (target_run_id,),
            ).fetchone()
            if source_requests:
                keeper = source_requests[0]
                removed_job_count = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM acquisition_jobs WHERE request_id=?",
                    (int(keeper["id"]),),
                ).fetchone()["count"])
                connection.execute(
                    "DELETE FROM acquisition_jobs WHERE request_id=?", (int(keeper["id"]),)
                )
                connection.execute(
                    "DELETE FROM acquisition_request_issues WHERE request_id=?",
                    (int(keeper["id"]),),
                )
                if target_request:
                    connection.execute(
                        "DELETE FROM acquisition_requests WHERE id=?", (int(keeper["id"]),)
                    )
                    transferred_request_id = int(target_request["id"])
                else:
                    connection.execute(
                        """UPDATE acquisition_requests
                           SET series_run_id=?, updated_at=? WHERE id=?""",
                        (target_run_id, now, int(keeper["id"])),
                    )
                    transferred_request_id = int(keeper["id"])
                for duplicate in source_requests[1:]:
                    connection.execute(
                        "DELETE FROM acquisition_requests WHERE id=?", (int(duplicate["id"]),)
                    )

            removed_issue_count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM issues WHERE series_run_id=?",
                (source_run_id,),
            ).fetchone()["count"])
            connection.execute(
                """UPDATE series_runs
                   SET monitoring_status=CASE
                         WHEN ?='monitored' THEN 'monitored' ELSE monitoring_status END,
                       acquisition_preference=CASE
                         WHEN ?='monitored' THEN ? ELSE acquisition_preference END,
                       start_year=COALESCE(?, start_year), updated_at=?
                   WHERE id=?""",
                (
                    source["monitoring_status"], source["monitoring_status"],
                    source["acquisition_preference"], target_start_year, now, target_run_id,
                ),
            )
            connection.execute(
                """UPDATE series_aliases
                   SET series_run_id=?, source='stale provider run repair', confirmed=1
                   WHERE series_run_id=?""",
                (target_run_id, source_run_id),
            )
            connection.execute("DELETE FROM series_runs WHERE id=?", (source_run_id,))

        self.reconcile_acquisition_jobs()
        return {
            "status": "repaired", "sourceSeriesId": str(source_run_id),
            "targetSeriesId": str(target_run_id), "provider": provider,
            "targetStartYear": target_start_year,
            "retiredProviderStartYear": provider_start_year,
            "removedProviderIssues": removed_issue_count,
            "removedQueuedJobs": removed_job_count,
            "requestId": str(transferred_request_id) if transferred_request_id else None,
        }

    def update_issue_metadata(
        self, issue_id: int, title: str | None, publication_year: Any = None
    ) -> dict[str, Any]:
        cleaned_title = re.sub(r"\s+", " ", str(title or "")).strip() or None
        if publication_year in {None, ""}:
            cleaned_year = None
        else:
            try:
                cleaned_year = int(publication_year)
            except (TypeError, ValueError) as exc:
                raise ValueError("Publication year must be a number") from exc
            if cleaned_year < 1800 or cleaned_year > 2200:
                raise ValueError("Publication year must be between 1800 and 2200")
        if cleaned_title is None and cleaned_year is None:
            raise ValueError("Enter an issue title or publication year")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            issue = connection.execute(
                "SELECT id, series_run_id, issue_number FROM issues WHERE id=?", (issue_id,)
            ).fetchone()
            if not issue:
                raise ValueError("Canonical issue was not found")
            connection.execute(
                """INSERT INTO issue_metadata_overrides(
                       issue_id, title, publication_year, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(issue_id) DO UPDATE SET
                       title=excluded.title, publication_year=excluded.publication_year,
                       updated_at=excluded.updated_at""",
                (issue_id, cleaned_title, cleaned_year, now, now),
            )
            fields = {"title": cleaned_title, "publicationYear": cleaned_year}
            connection.execute(
                """INSERT INTO issue_metadata_override_history(issue_id, action, fields_json, created_at)
                   VALUES (?, 'edit', ?, ?)""",
                (issue_id, _json(fields), now),
            )
        return {
            "issueId": str(issue_id), "seriesId": str(issue["series_run_id"]),
            "number": issue["issue_number"], **fields, "metadataLocked": True,
        }

    def reset_issue_metadata(self, issue_id: int) -> dict[str, Any]:
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            issue = connection.execute(
                "SELECT id, series_run_id, issue_number, title, publication_year FROM issues WHERE id=?",
                (issue_id,),
            ).fetchone()
            if not issue:
                raise ValueError("Canonical issue was not found")
            connection.execute("DELETE FROM issue_metadata_overrides WHERE issue_id=?", (issue_id,))
            connection.execute(
                """INSERT INTO issue_metadata_override_history(issue_id, action, fields_json, created_at)
                   VALUES (?, 'reset', '{}', ?)""",
                (issue_id, now),
            )
        return {
            "issueId": str(issue_id), "seriesId": str(issue["series_run_id"]),
            "number": issue["issue_number"], "title": issue["title"],
            "publicationYear": issue["publication_year"], "metadataLocked": False,
        }

    def ensure_provider_series_run(
        self,
        provider: str,
        provider_series_id: str,
        title: str,
        year: int | None = None,
        publisher: str | None = None,
        *,
        run_format: str | None = None,
    ) -> dict[str, Any]:
        """Create a catalog-only run when a provider run is not represented by a local file.

        `run_format` says the run is manga or a comic when the provider row
        knows; None leaves an existing run as it is.
        """
        wanted_format = run_format if run_format in SERIES_FORMATS else None
        title = re.sub(r"\s+", " ", str(title or "")).strip()
        canonical_key = _normalized(title)
        if not canonical_key:
            raise ValueError("A publication run title is required")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            existing = connection.execute(
                """SELECT series_runs.* FROM series_provider_ids
                   JOIN series_runs ON series_runs.id=series_provider_ids.series_run_id
                   WHERE series_provider_ids.provider=? AND series_provider_ids.provider_id=?""",
                (provider, str(provider_series_id)),
            ).fetchone()
            if existing is None:
                title_match = connection.execute(
                    "SELECT * FROM series_runs WHERE canonical_key=?", (canonical_key,)
                ).fetchone()
                # The first provider run may safely anchor an existing local
                # filename-derived run. A second provider run with the same
                # display title is a distinct publication run and must not be
                # collapsed into that identity (Farmhand is one real example).
                conflicting_provider_identity = None
                if title_match is not None:
                    conflicting_provider_identity = connection.execute(
                        """SELECT provider_id FROM series_provider_ids
                           WHERE series_run_id=? AND provider=? AND provider_id!=?""",
                        (int(title_match["id"]), provider, str(provider_series_id)),
                    ).fetchone()
                if (
                    conflicting_provider_identity is None
                    and title_match is not None
                    and _years_compatible(title_match["start_year"], year)
                    # Berserk the manga is not Berserk the comic, whatever the
                    # year: joining them merges two issue lists into one run.
                    and (wanted_format is None
                         or (title_match["format"] or "comic") == wanted_format)
                ):
                    existing = title_match
            created = existing is None
            if created:
                run_key = canonical_key
                if connection.execute(
                    "SELECT 1 FROM series_runs WHERE canonical_key=?", (run_key,)
                ).fetchone():
                    # canonical_key is an internal identity, not display copy.
                    # Include the provider identity only when two real runs
                    # normalize to the same human-facing title.
                    provider_key = _normalized(provider) or "provider"
                    provider_id_key = _normalized(provider_series_id) or "run"
                    run_key = f"{canonical_key}::{provider_key}:{provider_id_key}"
                    suffix = 2
                    candidate_key = run_key
                    while connection.execute(
                        "SELECT 1 FROM series_runs WHERE canonical_key=?", (candidate_key,)
                    ).fetchone():
                        candidate_key = f"{run_key}:{suffix}"
                        suffix += 1
                    run_key = candidate_key
                cursor = connection.execute(
                    """INSERT INTO series_runs(
                           canonical_title, canonical_key, start_year, publisher, format,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (title, run_key, year, publisher, wanted_format or "comic", now, now),
                )
                run_id = int(cursor.lastrowid)
            else:
                run_id = int(existing["id"])
                connection.execute(
                    """UPDATE series_runs SET
                           start_year=COALESCE(start_year, ?),
                           publisher=COALESCE(publisher, ?),
                           format=COALESCE(?, format), updated_at=?
                       WHERE id=?""",
                    (year, publisher, wanted_format, now, run_id),
                )
            alias_owner = connection.execute(
                "SELECT series_run_id FROM series_aliases WHERE normalized_alias=?", (canonical_key,)
            ).fetchone()
            if alias_owner is None:
                connection.execute(
                    """INSERT INTO series_aliases(
                           series_run_id, alias, normalized_alias, source, confirmed, created_at
                       ) VALUES (?, ?, ?, ?, 1, ?)""",
                    (run_id, title, canonical_key, f"{provider} catalog run", now),
                )
        return {"id": str(run_id), "title": title, "created": created}

    def reassign_matching_editions(
        self, source_series_run_id: int, target_runs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Move clearly matching local volumes under newly imported catalog runs."""
        def match_key(value: str) -> str:
            value = re.sub(r"\b(?:vol(?:ume)?\.?|book)\s*#?\s*\d+\b", " ", value, flags=re.I)
            return _normalized(value)

        targets = [
            {"id": int(item["id"]), "title": str(item["title"]), "key": match_key(str(item["title"]))}
            for item in target_runs if item.get("id") and item.get("title")
        ]
        moved = []
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            for target in targets:
                alias_owner = connection.execute(
                    "SELECT series_run_id FROM series_aliases WHERE normalized_alias=?",
                    (target["key"],),
                ).fetchone()
                if alias_owner is None:
                    connection.execute(
                        """INSERT INTO series_aliases(
                               series_run_id, alias, normalized_alias, source, confirmed, created_at
                           ) VALUES (?, ?, ?, 'confirmed collection run', 1, ?)""",
                        (target["id"], target["title"], target["key"], now),
                    )
            candidate_run_ids = list(dict.fromkeys([source_series_run_id, *(target["id"] for target in targets)]))
            editions = list(connection.execute(
                f"SELECT * FROM editions WHERE series_run_id IN ({','.join('?' for _ in candidate_run_ids)})",
                candidate_run_ids,
            ))
            for edition in editions:
                label = " ".join(
                    value for value in [edition["title"], edition["subtitle"]] if value
                )
                edition_key = match_key(label)
                ranked = sorted(
                    (
                        (difflib.SequenceMatcher(None, edition_key, target["key"]).ratio(), target)
                        for target in targets if target["id"] != source_series_run_id
                    ),
                    key=lambda item: item[0], reverse=True,
                )
                if not ranked or ranked[0][0] < 0.84:
                    continue
                score, target = ranked[0]
                if len(ranked) > 1 and score - ranked[1][0] < 0.08:
                    continue
                connection.execute(
                    "UPDATE editions SET series_run_id=?, updated_at=? WHERE id=?",
                    (target["id"], now, edition["id"]),
                )
                connection.execute(
                    """UPDATE file_identities SET series_run_id=?, updated_at=?
                       WHERE file_id IN(
                           SELECT file_id FROM file_edition_links WHERE edition_id=?
                       )""",
                    (target["id"], now, edition["id"]),
                )
                file_ids = [
                    int(row["file_id"])
                    for row in connection.execute(
                        "SELECT file_id FROM file_edition_links WHERE edition_id=?", (edition["id"],)
                    )
                ]
                for file_id in file_ids:
                    override_row = connection.execute(
                        """SELECT fields_json, locked_fields_json
                           FROM file_metadata_overrides WHERE file_id=?""",
                        (file_id,),
                    ).fetchone()
                    fields = _load_json(override_row["fields_json"], {}) if override_row else {}
                    locked_fields = _load_json(override_row["locked_fields_json"], []) if override_row else []
                    fields["seriesTitle"] = target["title"]
                    locked_fields = list(dict.fromkeys([*locked_fields, "seriesTitle"]))
                    if override_row:
                        connection.execute(
                            """UPDATE file_metadata_overrides
                               SET fields_json=?, locked_fields_json=?, updated_at=? WHERE file_id=?""",
                            (_json(fields), _json(locked_fields), now, file_id),
                        )
                    else:
                        connection.execute(
                            """INSERT INTO file_metadata_overrides(
                                   file_id, fields_json, locked_fields_json, created_at, updated_at
                               ) VALUES (?, ?, ?, ?, ?)""",
                            (file_id, _json(fields), _json(locked_fields), now, now),
                        )
                    connection.execute(
                        """INSERT INTO metadata_override_history(
                               file_id, action, fields_json, created_at
                           ) VALUES (?, 'collection_run_match', ?, ?)""",
                        (file_id, _json({"seriesTitle": target["title"]}), now),
                    )
                claim_rows = list(connection.execute(
                    "SELECT id, issue_number FROM edition_coverage_claims WHERE edition_id=?",
                    (edition["id"],),
                ))
                for claim in claim_rows:
                    issue = connection.execute(
                        "SELECT id FROM issues WHERE series_run_id=? AND issue_number=?",
                        (target["id"], claim["issue_number"]),
                    ).fetchone()
                    if issue:
                        connection.execute(
                            """UPDATE edition_coverage_claims
                               SET issue_id=?, resolution_status='resolved' WHERE id=?""",
                            (issue["id"], claim["id"]),
                        )
                if not claim_rows and score >= 0.98:
                    for issue in connection.execute(
                        "SELECT issue_number FROM issues WHERE series_run_id=?", (target["id"],)
                    ):
                        connection.execute(
                            """INSERT INTO edition_coverage_overrides(
                                   edition_id, series_run_id, issue_number, action, note,
                                   created_at, updated_at
                               ) VALUES (?, ?, ?, 'add', ?, ?, ?)
                               ON CONFLICT(edition_id, series_run_id, issue_number) DO UPDATE SET
                                   action='add', note=excluded.note, updated_at=excluded.updated_at""",
                            (
                                edition["id"], target["id"], issue["issue_number"],
                                "Exact volume title matched during confirmed Collection import",
                                now, now,
                            ),
                        )
                moved.append({
                    "editionId": str(edition["id"]), "title": label,
                    "seriesId": str(target["id"]), "seriesTitle": target["title"],
                    "matchScore": round(score, 3),
                })
        return {"moved": moved, "count": len(moved)}

    def record_issue_sync_error(self, series_run_id: int, provider: str, error: str) -> None:
        with self._connect() as connection:
            if not connection.execute("SELECT id FROM series_runs WHERE id=?", (series_run_id,)).fetchone():
                return
            connection.execute(
                """INSERT INTO issue_catalog_status(series_run_id, status, provider, error)
                   VALUES (?, 'unknown', ?, ?)
                   ON CONFLICT(series_run_id) DO UPDATE SET
                       provider=COALESCE(issue_catalog_status.provider, excluded.provider),
                       error=excluded.error""",
                (series_run_id, provider, error),
            )

    def issue_metadata_summary(self, series_run_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            catalog = connection.execute(
                "SELECT title_policy FROM issue_catalog_status WHERE series_run_id=?", (series_run_id,)
            ).fetchone()
            rows = list(connection.execute(
                """SELECT issues.issue_number,
                          COALESCE(issue_metadata_overrides.title, issues.title) AS effective_title,
                          COALESCE(issue_metadata_overrides.publication_year, issues.publication_year) AS effective_year,
                          issues.publication_date
                   FROM issues
                   LEFT JOIN issue_metadata_overrides ON issue_metadata_overrides.issue_id=issues.id
                   WHERE issues.series_run_id=?
                   ORDER BY issues.id""",
                (series_run_id,),
            ))
        title_policy = str(catalog["title_policy"] if catalog else "unknown")
        raw_missing_titles = [
            row["issue_number"] for row in rows if not str(row["effective_title"] or "").strip()
        ]
        missing_titles = [] if title_policy == "numbered_only" else raw_missing_titles
        missing_dates = [row["issue_number"] for row in rows if row["effective_year"] is None]
        return {
            "issueCount": len(rows), "missingTitleCount": len(missing_titles),
            "missingDateCount": len(missing_dates), "missingTitleIssues": missing_titles,
            "missingDateIssues": missing_dates, "titlePolicy": title_policy,
            "rawMissingTitleCount": len(raw_missing_titles),
        }

    def runs_sharing_title(self, series_run_id: int) -> list[dict[str, Any]]:
        """Other publication runs whose title is this one's.

        Two runs of a title are the case where an unknown publication year
        stops being a cosmetic gap: it is the only thing that separates a
        relaunch's issues from the original's.
        """
        with self._connect() as connection:
            series = connection.execute(
                "SELECT canonical_title FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone()
            if not series:
                return []
            wanted = _normalized(series["canonical_title"])
            return [
                {"id": str(row["id"]), "title": row["canonical_title"], "year": row["start_year"]}
                for row in connection.execute(
                    "SELECT id, canonical_title, start_year FROM series_runs WHERE id<>? ORDER BY id",
                    (series_run_id,),
                )
                if _normalized(row["canonical_title"]) == wanted
            ]

    def update_issue_catalog_detail(
        self, series_run_id: int, detail: str, title_policy: str | None = None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE issue_catalog_status
                   SET detail=?, title_policy=COALESCE(?, title_policy), error=NULL
                   WHERE series_run_id=?""",
                (detail, title_policy, series_run_id),
            )

    def add_series_alias(self, series_run_id: int, alias: str) -> dict[str, Any]:
        alias = re.sub(r"\s+", " ", alias).strip()
        normalized_alias = _normalized(alias)
        if len(normalized_alias) < 2:
            raise ValueError("Alias must contain at least two letters or numbers")
        with self._connect() as connection:
            series = connection.execute("SELECT id FROM series_runs WHERE id=?", (series_run_id,)).fetchone()
            if not series:
                raise ValueError("Canonical series was not found")
            owner = connection.execute(
                "SELECT series_run_id FROM series_aliases WHERE normalized_alias=?", (normalized_alias,)
            ).fetchone()
            if owner and int(owner["series_run_id"]) != series_run_id:
                raise ValueError("That alias already belongs to another canonical series; merge the series instead")
            connection.execute(
                """INSERT INTO series_aliases(series_run_id, alias, normalized_alias, source, confirmed, created_at)
                   VALUES (?, ?, ?, 'manual', 1, ?)
                   ON CONFLICT(normalized_alias) DO UPDATE SET
                       alias=excluded.alias, source='manual', confirmed=1""",
                (series_run_id, alias, normalized_alias, _utc_now()),
            )
        return {"seriesId": str(series_run_id), "alias": alias, "normalizedAlias": normalized_alias, "confirmed": True}

    def move_file_to_series_run(
        self,
        file_id: int,
        series_run_id: int | None = None,
        title: str | None = None,
        start_year: int | None = None,
        publisher: str | None = None,
    ) -> dict[str, Any]:
        """Move one cataloged file to an existing or newly created publication run."""
        requested_title = str(title or "").strip()
        if series_run_id is None and not requested_title:
            raise ValueError("Choose an existing run or provide a new run title")
        if start_year not in {None, ""}:
            try:
                start_year = int(start_year)
            except (TypeError, ValueError) as exc:
                raise ValueError("Publication year must be a number") from exc
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            file_row = connection.execute(
                """SELECT files.id, files.filename, file_identities.series_run_id,
                          series_runs.canonical_title
                   FROM files
                   JOIN file_identities ON file_identities.file_id=files.id
                   JOIN series_runs ON series_runs.id=file_identities.series_run_id
                   WHERE files.id=? AND files.present=1""",
                (file_id,),
            ).fetchone()
            if not file_row:
                raise ValueError("Library file was not found")
            source_run_id = int(file_row["series_run_id"])

            if series_run_id is not None:
                target = connection.execute("SELECT * FROM series_runs WHERE id=?", (series_run_id,)).fetchone()
                if not target:
                    raise ValueError("Publication run was not found")
                target_run_id = int(target["id"])
                target_title = str(target["canonical_title"])
            else:
                canonical_key = _normalized(requested_title)
                if not canonical_key:
                    raise ValueError("A publication run title is required")
                target = connection.execute(
                    "SELECT * FROM series_runs WHERE canonical_key=?", (canonical_key,)
                ).fetchone()
                if target:
                    target_run_id = int(target["id"])
                    target_title = str(target["canonical_title"])
                else:
                    cursor = connection.execute(
                        """INSERT INTO series_runs(
                               canonical_title, canonical_key, start_year, publisher, created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (requested_title, canonical_key, start_year, str(publisher or "").strip() or None, now, now),
                    )
                    target_run_id = int(cursor.lastrowid)
                    target_title = requested_title

            if target_run_id == source_run_id:
                return {"fileId": str(file_id), "seriesId": str(target_run_id), "title": target_title}

            normalized_target = _normalized(target_title)
            alias = connection.execute(
                "SELECT id FROM series_aliases WHERE normalized_alias=?", (normalized_target,)
            ).fetchone()
            if alias:
                connection.execute(
                    """UPDATE series_aliases
                       SET series_run_id=?, alias=?, source='manual run assignment', confirmed=1
                       WHERE id=?""",
                    (target_run_id, target_title, alias["id"]),
                )
            else:
                connection.execute(
                    """INSERT INTO series_aliases(
                           series_run_id, alias, normalized_alias, source, confirmed, created_at
                       ) VALUES (?, ?, ?, 'manual run assignment', 1, ?)""",
                    (target_run_id, target_title, normalized_target, now),
                )

            connection.execute(
                "UPDATE file_identities SET series_run_id=?, updated_at=? WHERE file_id=?",
                (target_run_id, now, file_id),
            )

            issue = connection.execute(
                """SELECT issues.* FROM file_issue_links
                   JOIN issues ON issues.id=file_issue_links.issue_id
                   WHERE file_issue_links.file_id=?""",
                (file_id,),
            ).fetchone()
            if issue:
                target_issue = connection.execute(
                    "SELECT id FROM issues WHERE series_run_id=? AND issue_number=?",
                    (target_run_id, issue["issue_number"]),
                ).fetchone()
                if target_issue:
                    target_issue_id = int(target_issue["id"])
                else:
                    cursor = connection.execute(
                        """INSERT INTO issues(
                               series_run_id, issue_number, title, publication_year,
                               publication_date, created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            target_run_id, issue["issue_number"], issue["title"],
                            issue["publication_year"], issue["publication_date"], now, now,
                        ),
                    )
                    target_issue_id = int(cursor.lastrowid)
                connection.execute(
                    "UPDATE file_issue_links SET issue_id=? WHERE file_id=?",
                    (target_issue_id, file_id),
                )

            edition = connection.execute(
                """SELECT editions.* FROM file_edition_links
                   JOIN editions ON editions.id=file_edition_links.edition_id
                   WHERE file_edition_links.file_id=?""",
                (file_id,),
            ).fetchone()
            if edition:
                link_count = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM file_edition_links WHERE edition_id=?",
                    (edition["id"],),
                ).fetchone()["count"])
                if link_count == 1:
                    connection.execute(
                        "UPDATE editions SET series_run_id=?, updated_at=? WHERE id=?",
                        (target_run_id, now, edition["id"]),
                    )
                else:
                    edition_key = f"{edition['edition_key']}:file:{file_id}"
                    cursor = connection.execute(
                        """INSERT INTO editions(
                               series_run_id, edition_key, title, subtitle, volume_number,
                               publication_year, publisher, format, isbns_json, cover, source,
                               source_id, verification_status, coverage_status, created_at,
                               updated_at, edition_kind
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            target_run_id, edition_key, edition["title"], edition["subtitle"],
                            edition["volume_number"], edition["publication_year"], edition["publisher"],
                            edition["format"], edition["isbns_json"], edition["cover"], edition["source"],
                            edition["source_id"], edition["verification_status"], edition["coverage_status"],
                            now, now, edition["edition_kind"],
                        ),
                    )
                    new_edition_id = int(cursor.lastrowid)
                    for claim in connection.execute(
                        "SELECT * FROM edition_coverage_claims WHERE edition_id=?", (edition["id"],)
                    ):
                        connection.execute(
                            """INSERT INTO edition_coverage_claims(
                                   edition_id, issue_id, series_label, issue_number, source,
                                   confidence, resolution_status, relation_kind, evidence, created_at
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                new_edition_id, claim["issue_id"], claim["series_label"],
                                claim["issue_number"], claim["source"], claim["confidence"],
                                claim["resolution_status"], claim["relation_kind"],
                                claim["evidence"], now,
                            ),
                        )
                    connection.execute(
                        "UPDATE file_edition_links SET edition_id=? WHERE file_id=?",
                        (new_edition_id, file_id),
                    )

            override_row = connection.execute(
                "SELECT fields_json, locked_fields_json FROM file_metadata_overrides WHERE file_id=?",
                (file_id,),
            ).fetchone()
            fields = _load_json(override_row["fields_json"], {}) if override_row else {}
            locked_fields = _load_json(override_row["locked_fields_json"], []) if override_row else []
            fields["seriesTitle"] = target_title
            locked_fields = list(dict.fromkeys([*locked_fields, "seriesTitle"]))
            if override_row:
                connection.execute(
                    """UPDATE file_metadata_overrides
                       SET fields_json=?, locked_fields_json=?, updated_at=? WHERE file_id=?""",
                    (_json(fields), _json(locked_fields), now, file_id),
                )
            else:
                connection.execute(
                    """INSERT INTO file_metadata_overrides(
                           file_id, fields_json, locked_fields_json, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (file_id, _json(fields), _json(locked_fields), now, now),
                )
            connection.execute(
                """INSERT INTO metadata_override_history(file_id, action, fields_json, created_at)
                   VALUES (?, 'move_run', ?, ?)""",
                (file_id, _json({"seriesTitle": target_title}), now),
            )
        return {"fileId": str(file_id), "seriesId": str(target_run_id), "title": target_title}

    def create_series_family(self, name: str, series_run_ids: list[int] | None = None) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        normalized_name = _normalized(name)
        if len(normalized_name) < 2:
            raise ValueError("Family name must contain at least two letters or numbers")
        run_ids = list(dict.fromkeys(int(value) for value in (series_run_ids or [])))
        if not run_ids:
            raise ValueError("A collection must start with at least one publication run")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM series_families WHERE normalized_name=?", (normalized_name,)
            ).fetchone()
            if existing:
                raise ValueError("A collection with that name already exists")
            if run_ids:
                found = {
                    int(row["id"])
                    for row in connection.execute(
                        f"SELECT id FROM series_runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids
                    )
                }
                if found != set(run_ids):
                    raise ValueError("One or more canonical runs were not found")
            cursor = connection.execute(
                """INSERT INTO series_families(name, normalized_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (name, normalized_name, now, now),
            )
            family_id = int(cursor.lastrowid)
            previous_family_ids: set[int] = set()
            for position, run_id in enumerate(run_ids):
                previous = connection.execute(
                    "SELECT series_family_id FROM series_family_memberships WHERE series_run_id=?", (run_id,)
                ).fetchone()
                if previous:
                    previous_family_ids.add(int(previous["series_family_id"]))
                connection.execute(
                    "DELETE FROM story_arc_run_memberships WHERE series_run_id=?", (run_id,)
                )
                connection.execute(
                    """INSERT INTO series_family_memberships(
                           series_family_id, series_run_id, position, created_at
                       ) VALUES (?, ?, ?, ?)
                       ON CONFLICT(series_run_id) DO UPDATE SET
                           series_family_id=excluded.series_family_id,
                           position=excluded.position, created_at=excluded.created_at""",
                    (family_id, run_id, position, now),
                )
            self._delete_empty_series_families(connection, previous_family_ids - {family_id})
        return {"id": str(family_id), "name": name, "runIds": [str(value) for value in run_ids]}

    # ---- Collections (schema 60) --------------------------------------------

    COLLECTION_SORTS = ("custom", "title", "year")

    @staticmethod
    def _run_collections(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        members: dict[int, list[str]] = {}
        for row in connection.execute(
            "SELECT collection_id, series_run_id FROM run_collection_items ORDER BY collection_id, position, added_at"
        ):
            members.setdefault(int(row["collection_id"]), []).append(str(row["series_run_id"]))
        return [{
            "id": str(row["id"]), "name": row["name"], "summary": row["summary"], "sortMode": row["sort_mode"],
            "coverSeriesId": str(row["cover_series_run_id"]) if row["cover_series_run_id"] is not None else None,
            "runIds": members.get(int(row["id"]), []),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "createdBy": int(row["created_by"]) if row["created_by"] is not None else None,
            "ownerId": int(row["owner_user_id"]) if row["owner_user_id"] is not None else None,
            "shared": bool(row["shared"]),
        } for row in connection.execute("SELECT * FROM run_collections ORDER BY name COLLATE NOCASE, id")]

    def run_collections(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return self._run_collections(connection)

    def run_collection(self, collection_id: int) -> dict[str, Any]:
        found = next((item for item in self.run_collections() if item["id"] == str(int(collection_id))), None)
        if found is None:
            raise LookupError("That collection is not in the library")
        return found

    @staticmethod
    def _collection_name(
        connection: sqlite3.Connection, name: Any, collection_id: int | None = None, owner_user_id: int | None = None,
    ) -> tuple[str, str]:
        """The name as kept, and its key: unique among the household's, and
        among each profile's own -- so a private name never collides with,
        or gives away, anyone else's."""
        cleaned = re.sub(r"\s+", " ", str(name or "")).strip()
        normalized = _normalized(cleaned)
        if len(normalized) < 2 or len(cleaned) > 80:
            raise ValueError("Give the collection a name of 2 to 80 characters")
        if owner_user_id is not None:
            normalized = f"u{int(owner_user_id)}:{normalized}"
        taken = connection.execute(
            "SELECT id FROM run_collections WHERE normalized_name=? AND id IS NOT ?", (normalized, collection_id)
        ).fetchone()
        if taken:
            raise CollectionNameTaken("A collection with that name already exists")
        return cleaned, normalized

    @staticmethod
    def _existing_runs(connection: sqlite3.Connection, run_ids: list[int]) -> None:
        if not run_ids:
            return
        found = {int(row["id"]) for row in connection.execute(
            f"SELECT id FROM series_runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids)}
        if found != set(run_ids):
            raise LookupError("Some of those runs are no longer in the library")

    def create_run_collection(
        self, name: str, series_run_ids: list[int] | None = None, summary: str | None = None,
        created_by: int | None = None, owner_user_id: int | None = None,
    ) -> dict[str, Any]:
        """A collection; with an owner it is that profile's, private until
        shared (as a hand-made story arc), without one the household's."""
        run_ids = list(dict.fromkeys(int(value) for value in (series_run_ids or [])))
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            cleaned, normalized = self._collection_name(connection, name, owner_user_id=owner_user_id)
            self._existing_runs(connection, run_ids)
            cursor = connection.execute(
                """INSERT INTO run_collections(name, normalized_name, summary, created_by, owner_user_id,
                       created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cleaned, normalized, (str(summary).strip() or None) if summary else None, created_by,
                 int(owner_user_id) if owner_user_id is not None else None, now, now),
            )
            collection_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO run_collection_items(collection_id, series_run_id, position, added_at) VALUES (?, ?, ?, ?)",
                [(collection_id, run_id, position, now) for position, run_id in enumerate(run_ids)],
            )
        return self.run_collection(collection_id)

    def update_run_collection(self, collection_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        """Change a collection in one go: its name, summary, order mode, cover,
        members added or removed, and the members' order. All or nothing."""
        collection_id = int(collection_id)
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM run_collections WHERE id=?", (collection_id,)).fetchone()
            if not row:
                raise LookupError("That collection is not in the library")
            if "shared" in changes:
                connection.execute("UPDATE run_collections SET shared=? WHERE id=?",
                                   (1 if changes["shared"] else 0, collection_id))
            if "name" in changes:
                cleaned, normalized = self._collection_name(
                    connection, changes["name"], collection_id, owner_user_id=row["owner_user_id"])
                connection.execute("UPDATE run_collections SET name=?, normalized_name=? WHERE id=?",
                                   (cleaned, normalized, collection_id))
            if "summary" in changes:
                summary = str(changes["summary"] or "").strip()
                if len(summary) > 2000:
                    raise ValueError("Keep the summary under 2,000 characters")
                connection.execute("UPDATE run_collections SET summary=? WHERE id=?", (summary or None, collection_id))
            if "sortMode" in changes:
                if changes["sortMode"] not in self.COLLECTION_SORTS:
                    raise ValueError("Order a collection by hand, by title or by year")
                connection.execute("UPDATE run_collections SET sort_mode=? WHERE id=?", (changes["sortMode"], collection_id))
            members = [int(item["series_run_id"]) for item in connection.execute(
                "SELECT series_run_id FROM run_collection_items WHERE collection_id=? ORDER BY position, added_at",
                (collection_id,))]
            remove = {int(value) for value in changes.get("remove") or []}
            if remove:
                connection.execute(
                    f"DELETE FROM run_collection_items WHERE collection_id=? AND series_run_id IN ({','.join('?' for _ in remove)})",
                    (collection_id, *remove),
                )
                members = [run_id for run_id in members if run_id not in remove]
            add = [int(value) for value in dict.fromkeys(changes.get("add") or []) if int(value) not in members]
            if add:
                self._existing_runs(connection, add)
                connection.executemany(
                    "INSERT INTO run_collection_items(collection_id, series_run_id, position, added_at) VALUES (?, ?, ?, ?)",
                    [(collection_id, run_id, len(members) + index, now) for index, run_id in enumerate(add)],
                )
                members += add
            if "order" in changes:
                order = [int(value) for value in changes["order"] or []]
                if sorted(order) != sorted(members) or len(set(order)) != len(order):
                    raise LookupError("The collection changed while you were ordering it; try again")
                connection.executemany(
                    "UPDATE run_collection_items SET position=? WHERE collection_id=? AND series_run_id=?",
                    [(position, collection_id, run_id) for position, run_id in enumerate(order)],
                )
            if "coverSeriesId" in changes:
                cover = changes["coverSeriesId"]
                cover = int(cover) if cover not in (None, "") else None
                if cover is not None and cover not in members:
                    raise ValueError("A collection's cover is one of its own runs")
                connection.execute("UPDATE run_collections SET cover_series_run_id=? WHERE id=?", (cover, collection_id))
            elif remove and row["cover_series_run_id"] in remove:
                connection.execute("UPDATE run_collections SET cover_series_run_id=NULL WHERE id=?", (collection_id,))
            connection.execute("UPDATE run_collections SET updated_at=? WHERE id=?", (now, collection_id))
        return self.run_collection(collection_id)

    def run_collection_files(self, collection_id: int) -> list[dict[str, Any]]:
        """The collection's comics that are here: each run's, lowest issue
        first, run by run in the collection's order -- what its header page is
        found among and chosen from, as an arc's comics are."""
        files: list[dict[str, Any]] = []
        for run_id in self.run_collection(collection_id)["runIds"]:
            try:
                files += [{**item, "runId": run_id} for item in self.series_backdrop_files(int(run_id))]
            except LookupError:
                continue
        return files

    def run_collection_backdrop_preference(self, collection_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM run_collections WHERE id=?", (int(collection_id),)).fetchone()
        if not row:
            raise LookupError("That collection is not in the library")
        if row["backdrop_file_id"] is None or not row["backdrop_member"]:
            return None
        return {"fileId": str(row["backdrop_file_id"]), "member": row["backdrop_member"],
                "source": row["backdrop_source"] or "chosen", "fileSignature": row["backdrop_signature"]}

    def set_run_collection_backdrop(
        self, collection_id: int, file_id: int, member: str, source: str, file_signature: str | None = None,
    ) -> None:
        """The page behind the collection's header: one of its own comics only."""
        if source not in {"chosen", "auto"}:
            raise ValueError("Unsupported background source")
        if not member:
            raise ValueError("Choose a page")
        if not any(item["id"] == str(file_id) for item in self.run_collection_files(collection_id)):
            raise ValueError("That comic is not part of this collection")
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE run_collections SET backdrop_file_id=?, backdrop_member=?, backdrop_source=?,
                       backdrop_signature=?, updated_at=? WHERE id=?""",
                (int(file_id), member, source, file_signature, _utc_now(), int(collection_id)),
            )

    def clear_run_collection_backdrop(self, collection_id: int) -> None:
        with self._write_lock, self._connect() as connection:
            if connection.execute(
                """UPDATE run_collections SET backdrop_file_id=NULL, backdrop_member=NULL, backdrop_source=NULL,
                       backdrop_signature=NULL, updated_at=? WHERE id=?""", (_utc_now(), int(collection_id)),
            ).rowcount == 0:
                raise LookupError("That collection is not in the library")

    def delete_run_collection(self, collection_id: int) -> None:
        with self._write_lock, self._connect() as connection:
            if connection.execute("DELETE FROM run_collections WHERE id=?", (int(collection_id),)).rowcount == 0:
                raise LookupError("That collection is not in the library")

    # ---- A profile's reading list (schema 61) ---------------------------------

    _READING_LIST_KINDS = {
        "run": ("series_run_bookmarks", "series_run_id", "series_runs"),
        "arc": ("reading_list_bookmarks", "reading_list_id", "reading_lists"),
        "collection": ("run_collection_bookmarks", "collection_id", "run_collections"),
    }

    def reading_list_entries(self, user_id: int) -> dict[str, list[dict[str, str]]]:
        """What this profile added to its reading list, newest first, by kind."""
        entries: dict[str, list[dict[str, str]]] = {}
        with self._connect() as connection:
            for kind, (table, column, _target) in self._READING_LIST_KINDS.items():
                entries[f"{kind}s"] = [{"id": str(row[column]), "at": row["created_at"]} for row in connection.execute(
                    f"SELECT {column}, created_at FROM {table} WHERE user_id=? ORDER BY created_at DESC, {column} DESC",
                    (int(user_id),))]
        return entries

    def set_reading_list_entry(self, user_id: int, kind: str, target_id: int, on: bool) -> None:
        """Add to, or take off, a profile's reading list. Adding twice keeps the first date."""
        if kind not in self._READING_LIST_KINDS:
            raise ValueError("A reading list holds runs, story arcs and collections")
        table, column, target = self._READING_LIST_KINDS[kind]
        with self._write_lock, self._connect() as connection:
            if on:
                if not connection.execute(f"SELECT 1 FROM {target} WHERE id=?", (int(target_id),)).fetchone():
                    raise LookupError("That is not in the library")
                connection.execute(
                    f"INSERT OR IGNORE INTO {table}(user_id, {column}, created_at) VALUES (?, ?, ?)",
                    (int(user_id), int(target_id), _utc_now()),
                )
            else:
                connection.execute(f"DELETE FROM {table} WHERE user_id=? AND {column}=?", (int(user_id), int(target_id)))

    def set_series_run_family(self, series_run_id: int, series_family_id: int | None) -> dict[str, Any]:
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            run = connection.execute("SELECT id FROM series_runs WHERE id=?", (series_run_id,)).fetchone()
            if not run:
                raise ValueError("Canonical series run was not found")
            previous = connection.execute(
                "SELECT series_family_id FROM series_family_memberships WHERE series_run_id=?", (series_run_id,)
            ).fetchone()
            previous_family_id = int(previous["series_family_id"]) if previous else None
            if series_family_id is None:
                connection.execute(
                    "DELETE FROM story_arc_run_memberships WHERE series_run_id=?", (series_run_id,)
                )
                connection.execute(
                    "DELETE FROM series_family_memberships WHERE series_run_id=?", (series_run_id,)
                )
                self._delete_empty_series_families(
                    connection, {previous_family_id} if previous_family_id is not None else set()
                )
                return {"seriesId": str(series_run_id), "family": None}
            family = connection.execute(
                "SELECT id, name FROM series_families WHERE id=?", (series_family_id,)
            ).fetchone()
            if not family:
                raise ValueError("Collection was not found")
            if previous_family_id != series_family_id:
                connection.execute(
                    "DELETE FROM story_arc_run_memberships WHERE series_run_id=?", (series_run_id,)
                )
            next_position = int(connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM series_family_memberships WHERE series_family_id=?",
                (series_family_id,),
            ).fetchone()["position"])
            connection.execute(
                """INSERT INTO series_family_memberships(
                       series_family_id, series_run_id, position, created_at
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(series_run_id) DO UPDATE SET
                       series_family_id=excluded.series_family_id,
                       position=excluded.position, created_at=excluded.created_at""",
                (series_family_id, series_run_id, next_position, now),
            )
            connection.execute("UPDATE series_families SET updated_at=? WHERE id=?", (now, series_family_id))
            if previous_family_id is not None and previous_family_id != series_family_id:
                self._delete_empty_series_families(connection, {previous_family_id})
        return {
            "seriesId": str(series_run_id),
            "family": {"id": str(series_family_id), "name": family["name"]},
        }

    def stop_collection_monitoring(self, series_family_id: int) -> dict[str, Any]:
        """Stop following a collection, and stop looking for its missing issues."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            family = connection.execute(
                "SELECT id, name, acquisition_preference, include_specials"
                " FROM series_families WHERE id=?", (series_family_id,)
            ).fetchone()
            if not family:
                raise ValueError("Collection was not found")
            connection.execute(
                """UPDATE series_families SET monitoring_status='cataloged', updated_at=?
                   WHERE id=?""",
                (now, series_family_id),
            )
            cancelled = connection.execute(
                """UPDATE acquisition_requests SET status='cancelled', updated_at=?
                   WHERE scope_type='collection' AND series_family_id=? AND status='open'
                     AND coverage='run'""",
                (now, series_family_id),
            ).rowcount
            # A member run may be followed in its own right. Only the runs this
            # collection was checking on its behalf stop being checked.
            connection.execute(
                """DELETE FROM series_monitor_refreshes
                   WHERE series_run_id IN (
                       SELECT series_family_memberships.series_run_id
                       FROM series_family_memberships
                       JOIN series_runs
                         ON series_runs.id=series_family_memberships.series_run_id
                       WHERE series_family_memberships.series_family_id=?
                         AND series_runs.monitoring_status!='monitored'
                   )""",
                (series_family_id,),
            )
        self.reconcile_acquisition_jobs()
        return {
            "id": str(series_family_id), "name": family["name"],
            "acquisitionPreference": family["acquisition_preference"],
            "includeSpecials": bool(family["include_specials"]),
            "monitoringStatus": "cataloged", "cancelledRequests": int(cancelled),
        }

    def set_collection_monitoring(
        self, series_family_id: int, acquisition_preference: str, include_specials: bool
    ) -> dict[str, Any]:
        preference = str(acquisition_preference or "either").strip().casefold()
        if preference not in {"volumes", "issues", "either"}:
            raise ValueError("Acquisition preference must be volumes, issues, or either")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            family = connection.execute(
                "SELECT id, name FROM series_families WHERE id=?", (series_family_id,)
            ).fetchone()
            if not family:
                raise ValueError("Collection was not found")
            connection.execute(
                """UPDATE series_families
                   SET acquisition_preference=?, include_specials=?, monitoring_status='monitored', updated_at=?
                   WHERE id=?""",
                (preference, int(bool(include_specials)), now, series_family_id),
            )
            next_check = (
                dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(hours=MONITORED_RUN_REFRESH_HOURS)
            ).isoformat()
            run_ids = [
                int(row["series_run_id"])
                for row in connection.execute(
                    "SELECT series_run_id FROM series_family_memberships WHERE series_family_id=?",
                    (series_family_id,),
                )
            ]
            connection.executemany(
                """INSERT INTO series_monitor_refreshes(
                       series_run_id, status, next_check_at, updated_at
                   ) VALUES (?, 'scheduled', ?, ?)
                   ON CONFLICT(series_run_id) DO NOTHING""",
                [(run_id, next_check, now) for run_id in run_ids],
            )
        return {
            "id": str(series_family_id), "name": family["name"],
            "acquisitionPreference": preference, "includeSpecials": bool(include_specials),
            "monitoringStatus": "monitored",
        }

    def set_series_monitoring(
        self, series_run_id: int, acquisition_preference: str = "either"
    ) -> dict[str, Any]:
        preference = str(acquisition_preference or "either").strip().casefold()
        if preference not in {"volumes", "issues", "either"}:
            raise ValueError("Acquisition preference must be volumes, issues, or either")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            run = connection.execute(
                "SELECT id, canonical_title FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone()
            if not run:
                raise ValueError("Canonical series run was not found")
            connection.execute(
                """UPDATE series_runs
                   SET acquisition_preference=?, monitoring_status='monitored', updated_at=?
                   WHERE id=?""",
                (preference, now, series_run_id),
            )
            next_check = (
                dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(hours=MONITORED_RUN_REFRESH_HOURS)
            ).isoformat()
            connection.execute(
                """INSERT INTO series_monitor_refreshes(
                       series_run_id, status, next_check_at, updated_at
                   ) VALUES (?, 'scheduled', ?, ?)
                   ON CONFLICT(series_run_id) DO NOTHING""",
                (series_run_id, next_check, now),
            )
        return {
            "id": str(series_run_id), "name": run["canonical_title"],
            "acquisitionPreference": preference, "includeSpecials": True,
            "monitoringStatus": "monitored",
        }

    def stop_series_monitoring(self, series_run_id: int) -> dict[str, Any]:
        """Stop following a run, and stop looking for its missing issues.

        Following was one-way: set_series_monitoring writes 'monitored' and
        nothing wrote anything else, so a run followed by accident stayed
        followed and kept searching.
        """
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            run = connection.execute(
                "SELECT id, canonical_title, acquisition_preference FROM series_runs WHERE id=?",
                (series_run_id,),
            ).fetchone()
            if not run:
                raise ValueError("Canonical series run was not found")
            connection.execute(
                """UPDATE series_runs SET monitoring_status='cataloged', updated_at=?
                   WHERE id=?""",
                (now, series_run_id),
            )
            # The run stays in the library with everything already owned; what
            # stops is the looking. Cancelling the request cancels its jobs,
            # which is what reconcile does with a cancelled request anyway.
            cancelled = connection.execute(
                """UPDATE acquisition_requests SET status='cancelled', updated_at=?
                   WHERE scope_type='series' AND series_run_id=? AND status='open'
                     AND coverage='run'""",
                (now, series_run_id),
            ).rowcount
            connection.execute(
                "DELETE FROM series_monitor_refreshes WHERE series_run_id=?",
                (series_run_id,),
            )
        self.reconcile_acquisition_jobs()
        return {
            "id": str(series_run_id), "name": run["canonical_title"],
            "acquisitionPreference": run["acquisition_preference"],
            "monitoringStatus": "cataloged", "cancelledRequests": int(cancelled),
        }

    def claim_monitored_series_refresh(self) -> dict[str, Any] | None:
        """Claim one due ongoing run without reopening the initial-import progress UI."""
        now_dt = dt.datetime.now(dt.timezone.utc)
        now = now_dt.isoformat()
        safety_retry = (now_dt + dt.timedelta(minutes=30)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT series_monitor_refreshes.*, series_runs.canonical_title
                   FROM series_monitor_refreshes
                   JOIN series_runs ON series_runs.id=series_monitor_refreshes.series_run_id
                   JOIN issue_catalog_status
                     ON issue_catalog_status.series_run_id=series_monitor_refreshes.series_run_id
                   WHERE series_monitor_refreshes.status IN ('scheduled', 'waiting', 'complete')
                     AND series_monitor_refreshes.next_check_at<=?
                     AND issue_catalog_status.status='complete_to_date'
                     AND (
                         series_runs.monitoring_status='monitored'
                         OR EXISTS(
                             SELECT 1 FROM series_family_memberships
                             JOIN series_families
                               ON series_families.id=series_family_memberships.series_family_id
                             WHERE series_family_memberships.series_run_id=series_runs.id
                               AND series_families.monitoring_status='monitored'
                         )
                     )
                     AND EXISTS(
                         SELECT 1 FROM acquisition_requests
                         WHERE acquisition_requests.status='open' AND (
                             (acquisition_requests.scope_type='series'
                              AND acquisition_requests.series_run_id=series_runs.id)
                             OR
                             (acquisition_requests.scope_type='collection'
                              AND EXISTS(
                                  SELECT 1 FROM series_family_memberships
                                  WHERE series_family_memberships.series_run_id=series_runs.id
                                    AND series_family_memberships.series_family_id=
                                        acquisition_requests.series_family_id
                              ))
                         )
                     )
                   ORDER BY series_monitor_refreshes.next_check_at,
                            series_monitor_refreshes.series_run_id
                   LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            connection.execute(
                """UPDATE series_monitor_refreshes
                   SET status='running', next_check_at=?, last_error=NULL, updated_at=?
                   WHERE series_run_id=?""",
                (safety_retry, now, row["series_run_id"]),
            )
            connection.commit()
        result = dict(row)
        result["status"] = "running"
        return result

    def queue_monitored_series_refresh(self, series_run_id: int) -> None:
        """Make a followed run eligible for an immediate background catalog check."""
        now = _utc_now()
        with self._connect() as connection:
            if not connection.execute(
                "SELECT id FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone():
                raise ValueError("Canonical series run was not found")
            connection.execute(
                """INSERT INTO series_monitor_refreshes(
                       series_run_id, status, next_check_at, updated_at
                   ) VALUES (?, 'scheduled', ?, ?)
                   ON CONFLICT(series_run_id) DO UPDATE SET
                       status='scheduled', next_check_at=excluded.next_check_at,
                       last_error=NULL, updated_at=excluded.updated_at""",
                (series_run_id, now, now),
            )

    def finish_monitored_series_refresh(
        self,
        series_run_id: int,
        provider: str | None = None,
        error: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Schedule the next provider check for an ongoing followed run."""
        now_dt = dt.datetime.now(dt.timezone.utc)
        now = now_dt.isoformat()
        if error:
            delay = max(300, int(retry_after_seconds or 3600))
            status = "waiting"
        else:
            delay = MONITORED_RUN_REFRESH_HOURS * 60 * 60
            status = "complete"
        next_check = (now_dt + dt.timedelta(seconds=delay)).isoformat()
        with self._connect() as connection:
            connection.execute(
                """UPDATE series_monitor_refreshes
                   SET status=?, last_checked_at=?, next_check_at=?, last_provider=?,
                       last_error=?, updated_at=?
                   WHERE series_run_id=?""",
                (status, now, next_check, provider, error, now, series_run_id),
            )

    def reconcile_acquisition_jobs(self, request_id: int | None = None) -> dict[str, int]:
        """Keep durable jobs aligned with release, ownership, and request state."""
        now = _utc_now()
        created = updated = 0
        request_filter = "AND acquisition_requests.id=?" if request_id is not None else ""
        parameters: tuple[Any, ...] = (request_id,) if request_id is not None else ()
        with self._write_lock, self._connect() as connection:
            rows = connection.execute(
                f"""SELECT acquisition_request_issues.request_id,
                           acquisition_requests.status AS request_status,
                           file_replacement_requests.id AS replacement_request_id,
                           file_replacement_requests.status AS replacement_status,
                           issues.id AS issue_id, issues.publication_date,
                           issues.publication_year,
                           EXISTS(
                               SELECT 1 FROM file_issue_links
                               JOIN files ON files.id=file_issue_links.file_id
                               WHERE file_issue_links.issue_id=issues.id AND files.present=1
                           ) AS direct_owned,
                           EXISTS(
                               SELECT 1 FROM edition_coverage_claims
                               JOIN file_edition_links
                                 ON file_edition_links.edition_id=edition_coverage_claims.edition_id
                               JOIN files ON files.id=file_edition_links.file_id
                               WHERE edition_coverage_claims.issue_id=issues.id
                                 AND edition_coverage_claims.resolution_status='resolved'
                                 AND edition_coverage_claims.relation_kind='full_issue'
                                 AND files.present=1
                                 AND NOT EXISTS(
                                     SELECT 1 FROM edition_coverage_overrides
                                     WHERE edition_coverage_overrides.edition_id=
                                           edition_coverage_claims.edition_id
                                       AND edition_coverage_overrides.series_run_id=issues.series_run_id
                                       AND edition_coverage_overrides.issue_number=issues.issue_number
                                       AND edition_coverage_overrides.action='remove'
                                 )
                           ) OR EXISTS(
                               SELECT 1 FROM edition_coverage_overrides
                               JOIN file_edition_links
                                 ON file_edition_links.edition_id=edition_coverage_overrides.edition_id
                               JOIN files ON files.id=file_edition_links.file_id
                               WHERE edition_coverage_overrides.series_run_id=issues.series_run_id
                                 AND edition_coverage_overrides.issue_number=issues.issue_number
                                 AND edition_coverage_overrides.action='add'
                                 AND files.present=1
                           ) AS collection_owned
                    FROM acquisition_request_issues
                    JOIN acquisition_requests
                      ON acquisition_requests.id=acquisition_request_issues.request_id
                    JOIN issues ON issues.id=acquisition_request_issues.issue_id
                    -- Scoped to the issues the replaced file actually covers.
                    -- Joined on the request alone, one damaged issue turned
                    -- every issue on a followed run into a replacement target,
                    -- and the branch below then kept all of them queued no
                    -- matter what was already owned.
                    LEFT JOIN file_replacement_requests
                      ON file_replacement_requests.acquisition_request_id=acquisition_requests.id
                     AND (
                       EXISTS(
                           SELECT 1 FROM file_issue_links
                            WHERE file_issue_links.file_id=file_replacement_requests.file_id
                              AND file_issue_links.issue_id=issues.id
                       )
                       OR EXISTS(
                           SELECT 1 FROM file_edition_links
                             JOIN edition_coverage_claims
                               ON edition_coverage_claims.edition_id=file_edition_links.edition_id
                            WHERE file_edition_links.file_id=file_replacement_requests.file_id
                              AND edition_coverage_claims.issue_id=issues.id
                              AND edition_coverage_claims.resolution_status='resolved'
                       )
                     )
                    WHERE 1=1 {request_filter}""",
                parameters,
            ).fetchall()
            existing = {
                (int(row["request_id"]), int(row["issue_id"])): row
                for row in connection.execute(
                    f"""SELECT acquisition_jobs.*,
                               EXISTS(
                                   SELECT 1 FROM acquisition_downloads
                                   WHERE acquisition_downloads.job_id=acquisition_jobs.id
                                     AND acquisition_downloads.status='imported'
                               ) AS download_imported
                        FROM acquisition_jobs
                        JOIN acquisition_requests
                          ON acquisition_requests.id=acquisition_jobs.request_id
                        WHERE 1=1 {request_filter}""",
                    parameters,
                )
            }
            for row in rows:
                key = (int(row["request_id"]), int(row["issue_id"]))
                job = existing.get(key)
                replacement = row["replacement_request_id"] is not None
                replacement_status = str(row["replacement_status"] or "")
                owned = bool(row["direct_owned"] or row["collection_owned"])
                release_state = _issue_release_state(row["publication_date"], row["publication_year"])
                if row["request_status"] == "cancelled" or replacement_status == "cancelled":
                    desired, reason = "cancelled", "Request cancelled"
                elif row["request_status"] == "fulfilled" or replacement_status == "fulfilled":
                    desired, reason = "fulfilled", "Replacement completed"
                elif replacement and job and job["download_imported"]:
                    # The new comic is here. Retiring the original is a separate
                    # step; until this said so, reconcile put the job back in the
                    # queue seconds after every import, forever.
                    desired, reason = "fulfilled", "Replacement downloaded and imported"
                elif replacement:
                    # A replacement target is intentionally searched even though the damaged
                    # file currently satisfies ownership. Its presence is also sufficient
                    # evidence that the issue has been released.
                    desired, reason = "queued", "Replacement needed for the existing library copy"
                elif owned:
                    desired, reason = "fulfilled", "Issue is covered by the library"
                elif release_state == "released":
                    desired, reason = "queued", "Missing from the library and available to search"
                elif release_state == "upcoming":
                    desired, reason = "waiting", "Waiting for publication date"
                else:
                    desired, reason = "waiting", "Release date must be repaired before searching"

                # Only released gaps become jobs. Other targets remain represented by monitoring.
                if not job and desired != "queued":
                    continue
                if not job:
                    cursor = connection.execute(
                        """INSERT INTO acquisition_jobs(
                               request_id, issue_id, status, queue_reason, created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (*key, desired, reason, now, now),
                    )
                    job_id = int(cursor.lastrowid)
                    connection.execute(
                        """INSERT INTO acquisition_job_events(job_id, status, detail, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (job_id, desired, reason, now),
                    )
                    created += 1
                    continue
                current = str(job["status"])
                if current == "cancelled" and row["request_status"] != "cancelled":
                    continue
                if desired == "queued" and current in {"searching", "grabbed", "failed"}:
                    continue
                # Reconcile's job is to move a job when ownership or the release
                # date changes. When the status is not changing it has nothing
                # newer to say than whoever wrote the reason last -- and a job
                # that has been searched knows something reconcile does not:
                # that it looked and found nothing. Overwriting that put
                # "available to search" back on an issue searched five times.
                # A job never attempted has no such finding, so it stays in
                # step with reconcile's own reading.
                searched = int(job["attempt_count"] or 0) > 0
                if current == desired and (job["queue_reason"] == reason or searched):
                    continue
                # The file arrived by another route, or the request was
                # cancelled. Either way the failure that is still on the
                # download row is no longer the story.
                if current == "failed":
                    self._clear_download_failure(connection, int(job["id"]))
                connection.execute(
                    """UPDATE acquisition_jobs
                       SET status=?, queue_reason=?, error=NULL, updated_at=? WHERE id=?""",
                    (desired, reason, now, int(job["id"])),
                )
                connection.execute(
                    """INSERT INTO acquisition_job_events(job_id, status, detail, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (int(job["id"]), desired, reason, now),
                )
                updated += 1
        return {"created": created, "updated": updated}

    def update_acquisition_job(
        self, job_id: int, status: str, detail: str | None = None
    ) -> dict[str, Any]:
        """Persist a worker-facing job transition and its audit event."""
        desired = str(status or "").strip().casefold()
        allowed = {"queued", "waiting", "searching", "grabbed", "failed", "fulfilled", "cancelled"}
        if desired not in allowed:
            raise ValueError("Unsupported acquisition job status")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            job = connection.execute(
                "SELECT * FROM acquisition_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not job:
                raise ValueError("Acquisition job was not found")
            attempt_increment = 1 if desired == "searching" and job["status"] != "searching" else 0
            error = detail if desired == "failed" else None
            connection.execute(
                """UPDATE acquisition_jobs
                   SET status=?, queue_reason=COALESCE(?, queue_reason),
                       attempt_count=attempt_count+?,
                       last_attempt_at=CASE WHEN ?=1 THEN ? ELSE last_attempt_at END,
                       error=?, updated_at=? WHERE id=?""",
                (desired, detail, attempt_increment, attempt_increment, now, error, now, job_id),
            )
            connection.execute(
                """INSERT INTO acquisition_job_events(job_id, status, detail, created_at)
                   VALUES (?, ?, ?, ?)""",
                (job_id, desired, detail, now),
            )
            updated = connection.execute(
                "SELECT * FROM acquisition_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return {
            "id": str(updated["id"]), "requestId": str(updated["request_id"]),
            "issueId": str(updated["issue_id"]), "status": updated["status"],
            "attemptCount": int(updated["attempt_count"]), "error": updated["error"],
            "updatedAt": updated["updated_at"],
        }

    def series_removal_plan(self, series_run_id: int) -> dict[str, Any]:
        """What removing a run takes with it, so it can be said before it is done."""
        with self._connect() as connection:
            run = connection.execute(
                "SELECT id, canonical_title FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone()
            if not run:
                raise LookupError("Series run was not found")
            files = [
                dict(row) for row in connection.execute(
                    """SELECT files.id, files.path, files.size_bytes, library_roots.path AS root
                       FROM files
                       JOIN file_identities ON file_identities.file_id=files.id
                       JOIN library_roots ON library_roots.id=files.root_id
                       WHERE file_identities.series_run_id=? AND files.present=1
                       ORDER BY files.path""",
                    (series_run_id,),
                )
            ]
            active = connection.execute(
                """SELECT COUNT(*) FROM acquisition_jobs
                   JOIN acquisition_requests
                     ON acquisition_requests.id=acquisition_jobs.request_id
                   LEFT JOIN acquisition_downloads
                     ON acquisition_downloads.job_id=acquisition_jobs.id
                   WHERE acquisition_requests.series_run_id=?
                     AND (acquisition_jobs.status='searching'
                          OR acquisition_downloads.status IN
                             ('queued', 'downloading', 'completed', 'importing', 'waiting_for_files'))""",
                (series_run_id,),
            ).fetchone()[0]
            requests = connection.execute(
                "SELECT COUNT(*) FROM acquisition_requests WHERE series_run_id=?", (series_run_id,)
            ).fetchone()[0]
        return {
            "id": str(run["id"]), "title": run["canonical_title"],
            "files": [
                {"id": item["id"], "path": item["path"], "root": item["root"],
                 "size": int(item["size_bytes"] or 0)}
                for item in files
            ],
            "fileCount": len(files),
            "sizeBytes": sum(int(item["size_bytes"] or 0) for item in files),
            "requestCount": int(requests), "activeDownloads": int(active),
        }

    def remove_series_run(self, series_run_id: int) -> None:
        """Forget a run and everything recorded about it.

        The caller has already dealt with the files on disk. Issues, requests,
        jobs, provider ids, aliases and editions go with the run; its files'
        records go first, because a file identity holds its run with RESTRICT.
        """
        with self._write_lock, self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone():
                raise LookupError("Series run was not found")
            connection.execute(
                """DELETE FROM files WHERE id IN (
                       SELECT file_id FROM file_identities WHERE series_run_id=?
                   )""",
                (series_run_id,),
            )
            connection.execute("DELETE FROM series_runs WHERE id=?", (series_run_id,))

    def _creator_id(self, connection: sqlite3.Connection, name: str) -> int:
        normalized = normalized_person(name)
        row = connection.execute(
            "SELECT id FROM creators WHERE normalized_name=?", (normalized,)
        ).fetchone()
        if row:
            return int(row["id"])
        return int(connection.execute(
            "INSERT INTO creators(name, normalized_name, created_at) VALUES (?, ?, ?)",
            (name, normalized, _utc_now()),
        ).lastrowid)

    def _sync_file_creators(
        self, file_id: int, result: dict[str, Any], *, connection: sqlite3.Connection | None = None,
    ) -> None:
        """Record who a file credits, from metadata the scan already read.

        Kept per file rather than per run: a file moved to another run takes
        its credits with it through file_identities, and a deleted one drops
        them by cascade, so nothing ever has to be rebuilt.
        """
        wanted = {(normalized_person(name), role): name for name, role in _file_credits(result)}
        with self._borrowed_write(connection) as write_connection:
            existing = {
                (row["normalized_name"], row["role"])
                for row in write_connection.execute(
                    """SELECT creators.normalized_name, file_creators.role FROM file_creators
                       JOIN creators ON creators.id=file_creators.creator_id
                       WHERE file_creators.file_id=?""",
                    (file_id,),
                )
            }
            if existing == set(wanted):
                return
            write_connection.execute("DELETE FROM file_creators WHERE file_id=?", (file_id,))
            for (_normalized_name, role), name in wanted.items():
                write_connection.execute(
                    "INSERT OR IGNORE INTO file_creators(file_id, creator_id, role) VALUES (?, ?, ?)",
                    (file_id, self._creator_id(write_connection, name), role),
                )

    def claim_run_creator_sync(self, available: Iterable[str]) -> dict[str, Any] | None:
        """One run whose files credit nobody, to ask a catalog about instead.

        Metron's first issue carries role-labelled credits; Comic Vine's volume
        record lists its people, and is the only source for manga. A run is
        stamped when claimed, so a failure is not retried in a loop.
        """
        usable = set(available)
        with self._write_lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT series_runs.id, series_runs.format,
                          (SELECT issue_provider_ids.provider_id FROM issue_provider_ids
                             JOIN issues ON issues.id=issue_provider_ids.issue_id
                            WHERE issues.series_run_id=series_runs.id
                              AND issue_provider_ids.provider='metron'
                            ORDER BY CAST(issues.issue_number AS REAL)=0,
                                     CAST(issues.issue_number AS REAL), issues.id
                            LIMIT 1) AS metron_issue_id,
                          (SELECT provider_id FROM series_provider_ids
                            WHERE series_provider_ids.series_run_id=series_runs.id
                              AND series_provider_ids.provider='comic_vine'
                            LIMIT 1) AS comic_vine_id
                   FROM series_runs
                   WHERE series_runs.creators_synced_at IS NULL
                     AND NOT EXISTS(
                         SELECT 1 FROM file_creators
                         JOIN file_identities ON file_identities.file_id=file_creators.file_id
                         WHERE file_identities.series_run_id=series_runs.id
                           AND file_creators.role NOT IN ('cover_artist', 'translator', 'editor')
                     )
                   ORDER BY series_runs.id"""
            ).fetchall()
            for row in rows:
                if row["metron_issue_id"] and "metron" in usable:
                    provider, provider_id = "metron", row["metron_issue_id"]
                elif row["comic_vine_id"] and "comic_vine" in usable:
                    provider, provider_id = "comic_vine", row["comic_vine_id"]
                else:
                    continue
                connection.execute(
                    "UPDATE series_runs SET creators_synced_at=? WHERE id=?", (_utc_now(), row["id"])
                )
                return {
                    "seriesRunId": int(row["id"]), "provider": provider,
                    "providerId": str(provider_id), "format": row["format"] or "comic",
                }
        return None

    def set_run_catalog_creators(
        self, series_run_id: int, source: str, creators: Sequence[dict[str, Any]],
    ) -> None:
        """Replace what one catalog said about who made a run."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM series_run_creators WHERE series_run_id=? AND source=?",
                (series_run_id, source),
            )
            for creator in creators:
                name = re.sub(r"\s+", " ", str(creator.get("name") or "")).strip()
                if not normalized_person(name):
                    continue
                creator_id = self._creator_id(connection, name)
                for role in creator.get("roles") or ["creator"]:
                    connection.execute(
                        """INSERT OR IGNORE INTO series_run_creators(
                               series_run_id, creator_id, role, source, issue_count, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (series_run_id, creator_id, str(role).strip().casefold(), source,
                         creator.get("count"), now),
                    )
            connection.execute(
                "UPDATE series_runs SET creators_synced_at=? WHERE id=?", (now, series_run_id)
            )

    def release_run_creator_sync(self, series_run_id: int) -> None:
        """Let a run be asked about again, after the catalog said to wait."""
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "UPDATE series_runs SET creators_synced_at=NULL WHERE id=?", (series_run_id,)
            )

    def set_series_format(
        self, series_run_id: int, run_format: str, reading_direction: str | None = "",
    ) -> dict[str, Any]:
        """Say a run is manga or a comic, for the publishers that print both.

        `reading_direction` is separate because the two are not the same
        question: the medium decides how a run is searched and filed, and the
        direction decides how its pages turn. They agree by default and an
        English edition of a manga is why they have to be able to disagree.
        Passing "" leaves the direction alone; None restores "follow the
        medium".
        """
        desired = str(run_format or "").strip().casefold()
        if desired not in SERIES_FORMATS:
            raise ValueError("A run is either a comic or manga")
        direction = reading_direction
        if direction not in ("", None):
            direction = str(direction).strip().casefold()
            if direction not in READING_DIRECTIONS:
                raise ValueError("A run reads left to right or right to left")
        with self._write_lock, self._connect() as connection:
            if direction == "":
                changed = connection.execute(
                    "UPDATE series_runs SET format=?, updated_at=? WHERE id=?",
                    (desired, _utc_now(), series_run_id),
                ).rowcount
            else:
                changed = connection.execute(
                    "UPDATE series_runs SET format=?, reading_direction=?, updated_at=? WHERE id=?",
                    (desired, direction, _utc_now(), series_run_id),
                ).rowcount
        if not changed:
            raise LookupError("Series run was not found")
        return self.series_reading_format(series_run_id)

    def series_reading_format(self, series_run_id: int) -> dict[str, Any]:
        """How a run is filed, and which way its pages turn."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT format, reading_direction FROM series_runs WHERE id=?", (series_run_id,)
            ).fetchone()
        if row is None:
            raise LookupError("Series run was not found")
        return {
            "id": str(series_run_id), "format": row["format"],
            "readingDirection": row["reading_direction"],
        }

    def get_acquisition_job_context(self, job_id: int) -> dict[str, Any]:
        """Return the trusted comic identity needed to search for one wanted issue."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT acquisition_jobs.id, acquisition_jobs.request_id,
                          acquisition_jobs.status,
                          acquisition_requests.acquisition_preference,
                          issues.id AS issue_id, issues.issue_number,
                          issues.title AS issue_title,
                          issues.publication_year, issues.publication_date,
                          series_runs.id AS series_id,
                          series_runs.canonical_title AS series_title,
                          series_runs.start_year AS series_year,
                          series_runs.publisher,
                          series_runs.format AS series_format,
                          file_replacement_requests.id AS replacement_id,
                          file_replacement_requests.file_id AS replacement_file_id,
                          replacement_files.path AS replacement_file_path
                   FROM acquisition_jobs
                   JOIN acquisition_requests
                     ON acquisition_requests.id=acquisition_jobs.request_id
                   JOIN issues ON issues.id=acquisition_jobs.issue_id
                   JOIN series_runs ON series_runs.id=issues.series_run_id
                   LEFT JOIN file_replacement_requests
                     ON file_replacement_requests.acquisition_request_id=acquisition_requests.id
                   LEFT JOIN files AS replacement_files
                     ON replacement_files.id=file_replacement_requests.file_id
                   WHERE acquisition_jobs.id=?""",
                (job_id,),
            ).fetchone()
        if not row:
            raise ValueError("Acquisition job was not found")
        with self._connect() as connection:
            existing_file = connection.execute(
                """SELECT files.path FROM files
                   JOIN file_identities ON file_identities.file_id=files.id
                   WHERE file_identities.series_run_id=? AND files.present=1
                   ORDER BY files.updated_at DESC, files.id DESC LIMIT 1""",
                (row["series_id"],),
            ).fetchone()
        # What a pack would be judged against: how long the run is, and how much
        # of it is still wanted. A pack is only worth taking for a run that is
        # mostly missing.
        with self._connect() as connection:
            run_issue_count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM issues WHERE series_run_id=?", (row["series_id"],)
            ).fetchone()["count"])
            wanted_issue_count = int(connection.execute(
                """SELECT COUNT(*) AS count FROM acquisition_jobs
                   WHERE request_id=? AND status IN ('queued', 'waiting', 'searching', 'failed')""",
                (row["request_id"],),
            ).fetchone()["count"])
        return {
            "id": str(row["id"]), "requestId": str(row["request_id"]),
            "status": row["status"], "issueId": str(row["issue_id"]),
            "runIssueCount": run_issue_count, "wantedIssueCount": wanted_issue_count,
            "issueNumber": row["issue_number"], "issueTitle": row["issue_title"],
            "publicationYear": row["publication_year"],
            "publicationDate": row["publication_date"],
            "seriesId": str(row["series_id"]), "seriesTitle": row["series_title"],
            "seriesYear": row["series_year"], "publisher": row["publisher"],
            "format": row["series_format"] or "comic",
            "acquisitionPreference": row["acquisition_preference"],
            "replacementId": str(row["replacement_id"]) if row["replacement_id"] is not None else None,
            "replacementFileId": (
                str(row["replacement_file_id"]) if row["replacement_file_id"] is not None else None
            ),
            "replacementFilePath": row["replacement_file_path"],
            "existingDirectory": str(Path(existing_file["path"]).parent) if existing_file else None,
        }

    def update_download_progress(self, download_id: int, fetched: int, total: int) -> None:
        """How far a download Flipparr is fetching itself has got.

        Written as it goes rather than asked for on demand, because unlike
        SABnzbd's queue there is nobody else holding the number.
        """
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE acquisition_downloads
                   SET bytes_fetched=?, bytes_total=?, updated_at=?
                   WHERE id=?""",
                (max(0, int(fetched)), max(0, int(total)), _utc_now(), download_id),
            )

    def record_download_import(
        self, download_id: int, job_id: int, issue_id: int, destination: str,
    ) -> None:
        """Note an issue that one download delivered beyond the job that grabbed it."""
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO acquisition_download_imports(
                       download_id, job_id, issue_id, destination, imported_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(download_id, job_id) DO UPDATE SET
                       destination=excluded.destination, imported_at=excluded.imported_at""",
                (download_id, job_id, issue_id, str(destination), _utc_now()),
            )

    def download_import_counts(self) -> dict[int, int]:
        """How many extra issues each download delivered, by download."""
        with self._connect() as connection:
            return {
                int(row["download_id"]): int(row["count"])
                for row in connection.execute(
                    """SELECT download_id, COUNT(*) AS count
                       FROM acquisition_download_imports GROUP BY download_id"""
                )
            }

    def wanted_run_issues(self, request_id: int) -> list[dict[str, Any]]:
        """The issues a request still wants, with the job waiting for each.

        A pack answers many at once, so this is both how its worth is judged
        before it is grabbed and what its download is swept for afterwards.
        """
        with self._connect() as connection:
            return [
                {
                    "jobId": int(row["id"]), "issueId": int(row["issue_id"]),
                    "issueNumber": row["issue_number"], "status": str(row["status"]),
                    # A collection's request spans several runs; a pack is one.
                    "seriesId": str(row["series_run_id"]) if row["series_run_id"] is not None else None,
                }
                for row in connection.execute(
                    """SELECT acquisition_jobs.id, acquisition_jobs.issue_id,
                              acquisition_jobs.status, issues.issue_number, issues.series_run_id
                       FROM acquisition_jobs
                       JOIN issues ON issues.id=acquisition_jobs.issue_id
                       WHERE acquisition_jobs.request_id=?
                         AND acquisition_jobs.status IN
                             ('queued', 'waiting', 'searching', 'grabbed', 'failed')
                       ORDER BY acquisition_jobs.id""",
                    (request_id,),
                )
            ]

    def record_acquisition_download(
        self,
        job_id: int,
        sab_nzo_id: str,
        release_title: str,
        release_key: str | None = None,
        source: str = "sabnzbd",
        *,
        taken_by_hand: bool = False,
    ) -> dict[str, Any]:
        """Persist the identity needed to reconcile a download and its import.

        `sab_nzo_id` is SABnzbd's queue id for a download it is carrying, and
        the source's own identity for anything else -- a file the user handed
        over has no queue to be in. The column keeps its name because that is
        what every existing row means. `taken_by_hand` is set by a grab the
        person made from the set-aside list, and cleared by any later grab.
        """
        queue_id = str(sab_nzo_id or "").strip()
        title = str(release_title or "").strip()
        if not queue_id or not title:
            raise ValueError("The download identity is incomplete")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            if not connection.execute("SELECT id FROM acquisition_jobs WHERE id=?", (job_id,)).fetchone():
                raise ValueError("Acquisition job was not found")
            connection.execute(
                """INSERT INTO acquisition_downloads(
                       job_id, sab_nzo_id, source, release_title, release_key,
                       taken_by_hand, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       sab_nzo_id=excluded.sab_nzo_id,
                       source=excluded.source,
                       release_title=excluded.release_title,
                       release_key=excluded.release_key,
                       taken_by_hand=excluded.taken_by_hand,
                       status='queued', error=NULL, failure_stage=NULL,
                       sab_storage=NULL, local_source=NULL, destination=NULL,
                       source_size=NULL, source_sha256=NULL, imported_at=NULL,
                       updated_at=excluded.updated_at""",
                (
                    job_id, queue_id, str(source or "sabnzbd").strip() or "sabnzbd", title,
                    str(release_key or "").strip() or None, 1 if taken_by_hand else 0, now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM acquisition_downloads WHERE job_id=?", (job_id,)
            ).fetchone()
        return {
            "id": str(row["id"]), "jobId": str(row["job_id"]),
            "queueId": row["sab_nzo_id"], "releaseTitle": row["release_title"],
            "status": row["status"], "updatedAt": row["updated_at"],
            "takenByHand": bool(row["taken_by_hand"]),
        }

    def forget_release_refusal(
        self, job_id: int, release_key: str, release_title: str | None = None,
    ) -> int:
        """Lift a refusal the person has overruled by taking the release by hand.

        By key or by title: the search sets a release aside under either
        (rejected_acquisition_releases), so both have to go. Other jobs'
        refusals of the same release stand -- they were judged for their own
        issue. Returns how many rows went.
        """
        key = str(release_key or "").strip()
        title = re.sub(r"\s+", " ", str(release_title or "")).strip().casefold()
        if not key and not title:
            return 0
        with self._write_lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, release_key, release_title FROM acquisition_release_failures WHERE job_id=?",
                (int(job_id),),
            ).fetchall()
            doomed = [
                int(row["id"]) for row in rows
                if (key and str(row["release_key"] or "") == key)
                or (title and re.sub(r"\s+", " ", str(row["release_title"] or "")).strip().casefold() == title)
            ]
            if doomed:
                connection.execute(
                    f"DELETE FROM acquisition_release_failures WHERE id IN ({', '.join('?' for _ in doomed)})",
                    doomed,
                )
        return len(doomed)

    def record_acquisition_release_failure(
        self,
        job_id: int,
        release_key: str,
        release_title: str,
        error: str | None = None,
        *,
        kind: str | None = None,
        sab_nzo_id: str | None = None,
        sab_storage: str | None = None,
    ) -> dict[str, Any]:
        """Persist one refused release so automatic retries do not select it again.

        For good when the refusal proved something; for SOFT_REFUSAL_HOURS
        when it did not. `sab_storage` is where its files were kept.
        """
        key = str(release_key or "").strip()
        title = str(release_title or "").strip()
        if not key or not title:
            raise ValueError("A failed release needs a stable identity and title")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            if not connection.execute(
                "SELECT id FROM acquisition_jobs WHERE id=?", (job_id,)
            ).fetchone():
                raise ValueError("Acquisition job was not found")
            connection.execute(
                """INSERT INTO acquisition_release_failures(
                       job_id, release_key, release_title, error, kind, sab_nzo_id, sab_storage,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id, release_key) DO UPDATE SET
                       release_title=excluded.release_title,
                       error=excluded.error,
                       kind=excluded.kind,
                       sab_nzo_id=COALESCE(excluded.sab_nzo_id, sab_nzo_id),
                       sab_storage=COALESCE(excluded.sab_storage, sab_storage),
                       updated_at=excluded.updated_at""",
                (job_id, key, title, error, kind, sab_nzo_id, sab_storage, now, now),
            )
            count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM acquisition_release_failures WHERE job_id=?",
                (job_id,),
            ).fetchone()["count"])
        return {"jobId": str(job_id), "releaseKey": key, "failureCount": count}

    # A refusal still in force: proven, or unproven and less than a day old.
    _REFUSAL_IN_FORCE = (
        f"NOT (COALESCE(kind, '') IN ({', '.join(repr(kind) for kind in SOFT_REFUSAL_KINDS)})"
        " AND updated_at < ?)"
    )

    @staticmethod
    def _soft_refusal_cutoff() -> str:
        return (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=SOFT_REFUSAL_HOURS)
        ).isoformat()

    def rejected_acquisition_release_keys(self, job_id: int) -> set[str]:
        """Release identities refused for this wanted issue and still set aside."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT release_key FROM acquisition_release_failures
                    WHERE job_id=? AND {self._REFUSAL_IN_FORCE}""",
                (job_id, self._soft_refusal_cutoff()),
            ).fetchall()
        return {str(row["release_key"]) for row in rows}

    def rejected_release_keys_for_jobs(self, job_ids: Sequence[int]) -> set[str]:
        """Release identities refused for any of these wanted issues and still
        set aside -- a run's pack is judged against the whole run."""
        ids = sorted({int(value) for value in job_ids})
        if not ids:
            return set()
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT release_key FROM acquisition_release_failures
                    WHERE job_id IN ({', '.join('?' for _ in ids)}) AND {self._REFUSAL_IN_FORCE}""",
                (*ids, self._soft_refusal_cutoff()),
            ).fetchall()
        return {str(row["release_key"]) for row in rows}

    def rejected_acquisition_releases(self, job_id: int) -> list[dict[str, Any]]:
        """Refused release identities and titles still set aside, for search de-duplication."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT release_key, release_title, error, updated_at
                    FROM acquisition_release_failures
                    WHERE job_id=? AND {self._REFUSAL_IN_FORCE} ORDER BY updated_at, id""",
                (job_id, self._soft_refusal_cutoff()),
            ).fetchall()
        return [dict(row) for row in rows]

    def kept_refused_downloads(
        self, *, job_id: int | None = None, older_than_days: int | None = None,
    ) -> list[dict[str, Any]]:
        """Refused downloads whose files were kept as evidence, for one job or past an age."""
        clauses, parameters = ["sab_storage IS NOT NULL"], []
        if job_id is not None:
            clauses.append("job_id=?")
            parameters.append(int(job_id))
        if older_than_days is not None:
            clauses.append("created_at < ?")
            parameters.append(
                (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)).isoformat()
            )
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT id, job_id, release_title, sab_nzo_id, sab_storage, created_at
                    FROM acquisition_release_failures WHERE {" AND ".join(clauses)} ORDER BY id""",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def forget_kept_download(self, failure_id: int) -> None:
        """The refused download's files are gone; the refusal itself stays on record."""
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE acquisition_release_failures SET sab_nzo_id=NULL, sab_storage=NULL
                   WHERE id=?""",
                (int(failure_id),),
            )

    def pending_acquisition_downloads(self) -> list[dict[str, Any]]:
        """Return SAB downloads that still need completion tracking or a verified import."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT acquisition_downloads.*, acquisition_jobs.status AS job_status
                   FROM acquisition_downloads
                   JOIN acquisition_jobs ON acquisition_jobs.id=acquisition_downloads.job_id
                   WHERE (
                       acquisition_downloads.status IN (
                           'queued', 'downloading', 'completed', 'importing', 'waiting_for_files'
                       )
                       OR (
                           acquisition_downloads.status='failed'
                           AND acquisition_downloads.failure_stage='import'
                           AND acquisition_downloads.error=(
                               'The completed SABnzbd download is not visible in the mounted comics folder'
                           )
                       )
                   )
                     AND acquisition_jobs.status NOT IN ('fulfilled', 'cancelled')
                   ORDER BY acquisition_downloads.created_at, acquisition_downloads.id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def update_acquisition_download(
        self,
        download_id: int,
        status: str,
        *,
        sab_storage: str | None = None,
        local_source: str | None = None,
        destination: str | None = None,
        source_size: int | None = None,
        source_sha256: str | None = None,
        error: str | None = None,
        failure_stage: str | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "queued", "downloading", "completed", "importing", "waiting_for_files",
            "imported", "failed",
        }
        desired = str(status or "").strip().casefold()
        if desired not in allowed:
            raise ValueError("Unsupported acquisition download status")
        with self._write_lock, self._connect() as connection:
            # Taken once the lock is held: a time stamped before a long wait
            # on it can fall behind the notifications' arrivals mark.
            now = _utc_now()
            imported_at = now if desired == "imported" else None
            if not connection.execute(
                "SELECT id FROM acquisition_downloads WHERE id=?", (download_id,)
            ).fetchone():
                raise ValueError("Acquisition download was not found")
            connection.execute(
                """UPDATE acquisition_downloads SET
                       status=?, sab_storage=COALESCE(?, sab_storage),
                       local_source=COALESCE(?, local_source),
                       destination=COALESCE(?, destination),
                       source_size=COALESCE(?, source_size),
                       source_sha256=COALESCE(?, source_sha256),
                       error=?, failure_stage=?,
                       imported_at=COALESCE(?, imported_at), updated_at=?
                   WHERE id=?""",
                (
                    desired, sab_storage, local_source, destination, source_size,
                    source_sha256, error, failure_stage, imported_at, now, download_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM acquisition_downloads WHERE id=?", (download_id,)
            ).fetchone()
        return dict(row)

    def direct_downloads_in_flight(self) -> list[dict[str, Any]]:
        """Downloads Flipparr fetches itself that say they are still going.
        Nothing outside the process knows about them, so after a restart the
        rows are all that is left of them. A download client's rows are its
        own to answer for: a torrent's "fetch" restarted here would treat its
        release key as a download-site post and refuse it."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, job_id, source, status, release_key, release_title FROM acquisition_downloads
                    WHERE source NOT IN ('sabnzbd', 'qbittorrent') AND status IN ('queued', 'downloading')
                    ORDER BY id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def live_downloads_with_requests(self) -> list[dict[str, Any]]:
        """Every download still under way, with the request its issue is for:
        a pack's issues wait on it rather than being searched for one by one."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT acquisition_downloads.id AS id, acquisition_downloads.job_id AS job_id,
                          acquisition_jobs.request_id AS request_id,
                          acquisition_downloads.release_title AS release_title,
                          acquisition_downloads.source AS source
                     FROM acquisition_downloads
                     JOIN acquisition_jobs ON acquisition_jobs.id=acquisition_downloads.job_id
                    WHERE {self._LIVE_DOWNLOAD}
                    ORDER BY acquisition_downloads.id"""
            ).fetchall()
        return [dict(row) for row in rows]

    # A torrent download's identity is `torrent:<hash>:<job>`; one torrent
    # serves every issue of a pack, each with its own row.
    _LIVE_DOWNLOAD_STATUSES = ("queued", "downloading", "completed", "importing", "waiting_for_files")

    _LIVE_DOWNLOAD = (
        "acquisition_downloads.status IN ('queued', 'downloading', 'completed', 'importing', 'waiting_for_files')"
        " AND acquisition_jobs.status NOT IN ('fulfilled', 'cancelled')"
    )

    def torrent_references(self, info_hash: str) -> dict[str, int]:
        """What still uses one torrent: downloads in flight, and refused
        downloads kept as evidence."""
        pattern = f"torrent:{str(info_hash).casefold()}:%"
        with self._connect() as connection:
            live = connection.execute(
                f"""SELECT COUNT(*) FROM acquisition_downloads
                     JOIN acquisition_jobs ON acquisition_jobs.id=acquisition_downloads.job_id
                    WHERE acquisition_downloads.sab_nzo_id LIKE ? AND {self._LIVE_DOWNLOAD}""",
                (pattern,),
            ).fetchone()[0]
            kept = connection.execute(
                """SELECT COUNT(*) FROM acquisition_release_failures
                    WHERE sab_nzo_id LIKE ? AND sab_storage IS NOT NULL""",
                (pattern,),
            ).fetchone()[0]
        return {"live": int(live), "kept": int(kept)}

    def torrent_hashes_in_use(self) -> set[str]:
        """Every torrent something still uses, for the seeding sweep."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT acquisition_downloads.sab_nzo_id FROM acquisition_downloads
                     JOIN acquisition_jobs ON acquisition_jobs.id=acquisition_downloads.job_id
                    WHERE acquisition_downloads.sab_nzo_id LIKE 'torrent:%' AND {self._LIVE_DOWNLOAD}
                    UNION
                    SELECT sab_nzo_id FROM acquisition_release_failures
                    WHERE sab_nzo_id LIKE 'torrent:%' AND sab_storage IS NOT NULL"""
            ).fetchall()
        return {str(row[0]).split(":")[1].casefold() for row in rows if str(row[0]).count(":") >= 2}

    def torrent_jobs(self, info_hash: str) -> list[int]:
        """The issues downloading from one torrent right now."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT acquisition_downloads.job_id FROM acquisition_downloads
                     JOIN acquisition_jobs ON acquisition_jobs.id=acquisition_downloads.job_id
                    WHERE acquisition_downloads.sab_nzo_id LIKE ? AND {self._LIVE_DOWNLOAD}""",
                (f"torrent:{str(info_hash).casefold()}:%",),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def acquisition_download_for_job(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM acquisition_downloads WHERE job_id=?", (int(job_id),)).fetchone()
        return dict(row) if row else None

    def stop_acquisition_download(self, job_id: int) -> dict[str, Any]:
        """A person stops a download: its row fails as stopped, and the issue
        goes back to wanting a release -- another one, since this one is set
        aside for a day by the caller."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            download = connection.execute(
                "SELECT * FROM acquisition_downloads WHERE job_id=?", (int(job_id),)).fetchone()
            if not download or download["status"] in ("imported", "failed"):
                raise ValueError("Nothing is downloading for this issue")
            connection.execute(
                """UPDATE acquisition_downloads SET status='failed', error=?, failure_stage='download',
                          updated_at=? WHERE id=?""",
                (DOWNLOAD_STOPPED_ERROR, now, int(download["id"])),
            )
            connection.execute(
                """UPDATE acquisition_jobs SET status='queued', queue_reason=?, error=NULL, updated_at=?
                    WHERE id=? AND status NOT IN ('fulfilled', 'cancelled')""",
                ("Stopped; ready to search for another release", now, int(job_id)),
            )
            connection.execute(
                "INSERT INTO acquisition_job_events(job_id, status, detail, created_at) VALUES (?, 'queued', ?, ?)",
                (int(job_id), "Download stopped by you", now),
            )
        return {"id": str(job_id), "status": "queued", "action": "research",
                "detail": "Stopped; ready to search for another release", "download": dict(download)}

    def return_unanswered_search(self, job_id: int, detail: str) -> None:
        """Put back a search the indexer never answered, without the try
        counting against the issue: the backoff is for issues nobody has
        posted, and an outage says nothing about that (Gate 3, 2026-10-05).
        Due again at once; the sweep stops at the first silence, so an outage
        costs one question a pass, not one per issue."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            changed = connection.execute(
                """UPDATE acquisition_jobs
                   SET status='queued', queue_reason=?,
                       attempt_count=MAX(attempt_count-1, 0),
                       last_attempt_at=NULL, updated_at=?
                   WHERE id=? AND status='searching'""",
                (detail, now, int(job_id)),
            ).rowcount
            if changed:
                connection.execute(
                    """INSERT INTO acquisition_job_events(job_id, status, detail, created_at)
                       VALUES (?, 'queued', ?, ?)""",
                    (int(job_id), detail, now),
                )

    def count_unanswered_search(self, job_id: int, detail: str) -> None:
        """Count, after all, a search that went unanswered and was put back
        uncounted: the indexer answered for the next issue, so the failure
        was this issue's own, and its backoff applies. Left uncounted and due
        at once, it was first in every sweep and held up all behind it."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE acquisition_jobs
                   SET attempt_count=attempt_count+1, last_attempt_at=?, queue_reason=?, updated_at=?
                   WHERE id=? AND status='queued'""",
                (now, detail, now, int(job_id)),
            )

    def recover_interrupted_searches(self) -> int:
        """Put back every search a restart cut short.

        A job is marked searching before the indexer is asked, and moved on
        when it answers. A process that stops in between leaves it at
        searching, which no pass picks up -- so the issue sat until someone
        searched it by hand. A fresh process has no search in flight, so any it
        finds are orphans: they go back to the queue, due at once, without the
        attempt that never finished counting against them.
        """
        now = _utc_now()
        detail = "Search was interrupted by a restart; it will run again shortly"
        with self._write_lock, self._connect() as connection:
            ids = [
                int(row["id"]) for row in connection.execute(
                    "SELECT id FROM acquisition_jobs WHERE status='searching'"
                )
            ]
            for job_id in ids:
                connection.execute(
                    """UPDATE acquisition_jobs
                       SET status='queued', queue_reason=?,
                           attempt_count=MAX(attempt_count-1, 0),
                           last_attempt_at=NULL, updated_at=?
                       WHERE id=?""",
                    (detail, now, job_id),
                )
                connection.execute(
                    """INSERT INTO acquisition_job_events(job_id, status, detail, created_at)
                       VALUES (?, 'queued', ?, ?)""",
                    (job_id, detail, now),
                )
        return len(ids)

    def acquisition_jobs_awaiting_release(
        self, request_id: int | None = None, *, backoff: bool = False
    ) -> list[int]:
        """Jobs with nothing sent to the download client yet.

        `backoff` holds back issues searched recently, for the scheduled sweep.
        A person pressing "Manual find" means now, so that path leaves
        it off.

        A job qualifies when nothing it has downloaded is still standing and
        neither it nor its request has been cancelled or fulfilled.

        That includes a job whose download failed because the release was
        wrong. The release is recorded as unusable when that happens, so
        searching again picks a different one; leaving such a job out meant a
        bad release could only be replaced by hand.

        A job that failed without any release being blamed is left alone. Its
        problem is local -- a disk, a quarantine that needs attention -- and
        another release would meet the same wall.

        Without a request id this is every such job in the library, which is
        what a "search for missing" pass over the whole backlog needs.
        """
        clauses = [
            # Nothing queued, downloading or already imported for this issue.
            """NOT EXISTS(
                   SELECT 1 FROM acquisition_downloads
                    WHERE acquisition_downloads.job_id=acquisition_jobs.id
                      AND acquisition_downloads.status <> 'failed'
               )""",
            """(
                   acquisition_jobs.status IN ('queued', 'waiting')
                   OR (
                       acquisition_jobs.status='failed'
                       AND EXISTS(
                           SELECT 1 FROM acquisition_release_failures
                            WHERE acquisition_release_failures.job_id=acquisition_jobs.id
                       )
                   )
               )""",
            "acquisition_requests.status='open'",
        ]
        parameters: list[Any] = []
        if request_id is not None:
            clauses.append("acquisition_jobs.request_id=?")
            parameters.append(int(request_id))
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT acquisition_jobs.id AS id,
                          acquisition_jobs.attempt_count AS attempt_count,
                          acquisition_jobs.last_attempt_at AS last_attempt_at
                   FROM acquisition_jobs
                   JOIN acquisition_requests
                     ON acquisition_requests.id=acquisition_jobs.request_id
                   WHERE {" AND ".join(clauses)}
                   ORDER BY acquisition_jobs.id""",
                parameters,
            ).fetchall()
        if not backoff:
            return [int(row["id"]) for row in rows]
        now = _parse_timestamp(_utc_now())
        return [
            int(row["id"]) for row in rows
            if self._search_is_due(row["attempt_count"], row["last_attempt_at"], now)
        ]

    # How long to leave an issue alone between searches that found nothing.
    # A comic posted three days after it shipped is found within a day of that;
    # one nobody ever posts costs a request a day rather than one per sweep.
    SEARCH_BACKOFF_HOURS = (0, 1, 3, 6, 12, 24)

    @classmethod
    def _search_is_due(cls, attempts: Any, last_attempt_at: Any, now: dt.datetime | None) -> bool:
        """Whether enough time has passed to look for this issue again."""
        attempted = int(attempts or 0)
        if attempted <= 0 or not last_attempt_at:
            return True
        last = _parse_timestamp(last_attempt_at)
        if last is None or now is None:
            # An unreadable timestamp must not park an issue forever; the
            # sweep is idempotent and one extra search is the cheaper mistake.
            return True
        index = min(attempted, len(cls.SEARCH_BACKOFF_HOURS) - 1)
        return (now - last).total_seconds() >= cls.SEARCH_BACKOFF_HOURS[index] * 3600

    @staticmethod
    def _clear_download_failure(connection: sqlite3.Connection, job_id: int) -> None:
        """Forget the download that failed, once its job has moved on.

        `acquisition_downloads.status='failed'` was a one-way latch: retrying
        into a fresh release search left it, reconcile marking the job
        fulfilled left it, and unfollow cancelling the request left it. So the
        bell and the rail badge -- which read `downloadStatus` -- went on
        reporting a failure for work that had already succeeded or been
        abandoned, and tapping it landed on a Pull List tab that no longer held
        the row.

        The row is deleted rather than rewritten: the attempt is preserved in
        `acquisition_release_failures`, a new grab re-creates the row through
        its own upsert, and a stale row also holds its UNIQUE sab_nzo_id.
        """
        connection.execute("DELETE FROM acquisition_downloads WHERE job_id=?", (job_id,))

    def retry_acquisition_job(self, job_id: int) -> dict[str, Any]:
        """Retry a failed import in place, or return a failed download to release search."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            job = connection.execute(
                "SELECT * FROM acquisition_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not job:
                raise ValueError("Acquisition job was not found")
            if job["status"] != "failed":
                raise ValueError("Only a failed acquisition job can be retried")
            download = connection.execute(
                "SELECT * FROM acquisition_downloads WHERE job_id=?", (job_id,)
            ).fetchone()
            retry_import = bool(
                download
                and download["status"] == "failed"
                and download["failure_stage"] == "import"
            )
            if retry_import:
                connection.execute(
                    """UPDATE acquisition_downloads
                       SET status='completed', error=NULL, failure_stage=NULL, updated_at=?
                       WHERE id=?""",
                    (now, int(download["id"])),
                )
                desired = "grabbed"
                detail = "Retrying the verified SABnzbd download import"
                action = "reprocess"
            else:
                desired = "queued"
                detail = "Ready to search for another release"
                action = "research"
                # The release is being abandoned, so its download is history.
                self._clear_download_failure(connection, job_id)
            connection.execute(
                """UPDATE acquisition_jobs
                   SET status=?, queue_reason=?, error=NULL, updated_at=? WHERE id=?""",
                (desired, detail, now, job_id),
            )
            connection.execute(
                """INSERT INTO acquisition_job_events(job_id, status, detail, created_at)
                   VALUES (?, ?, ?, ?)""",
                (job_id, desired, detail, now),
            )
        return {"id": str(job_id), "status": desired, "action": action, "detail": detail}

    def create_acquisition_request(
        self,
        scope_type: str,
        scope_id: int,
        acquisition_preference: str | None = None,
        include_specials: bool | None = None,
        *,
        issue_numbers: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Persist a monitored target and its current canonical issue set.

        With `issue_numbers`, the request covers those issues and nothing else:
        it does not begin following the run, does not grow when the run does,
        and is not withdrawn when the run is unfollowed. Asking for one new
        comic is not the same as taking on its back catalogue.
        """
        scope = str(scope_type or "").strip().casefold()
        if scope not in {"series", "collection"}:
            raise ValueError("Request scope must be a series or collection")
        narrow = issue_numbers is not None
        if narrow and scope != "series":
            raise ValueError("Only a series run can be asked for by issue")
        snapshot = self.catalog()
        if scope == "collection":
            target = next(
                (item for item in snapshot["families"] if item["id"] == str(scope_id)), None
            )
            if not target:
                raise ValueError("Collection was not found")
            preference = acquisition_preference or target.get("acquisitionPreference") or "either"
            include = target.get("includeSpecials", True) if include_specials is None else bool(include_specials)
            excluded_run_ids = {
                str(run_id)
                for arc in target.get("storyArcs") or [] if arc.get("type") == "specials"
                for run_id in arc.get("runIds") or []
            } if not include else set()
            issues = [
                issue for run in target.get("runs") or []
                if str(run["id"]) not in excluded_run_ids
                for issue in run.get("issues") or []
            ]
        else:
            target = next(
                (item for item in snapshot["series"] if item["id"] == str(scope_id)), None
            )
            if not target:
                raise ValueError("Canonical series run was not found")
            preference = acquisition_preference or target.get("acquisitionPreference") or "either"
            include = True
            issues = list(target.get("issues") or [])
        preference = str(preference or "either").strip().casefold()
        if preference not in {"volumes", "issues", "either"}:
            raise ValueError("Acquisition preference must be volumes, issues, or either")
        if not issues:
            raise ValueError("Find and confirm the canonical issue list before acquiring missing comics")
        if narrow:
            wanted = {str(number).strip() for number in issue_numbers if str(number).strip()}
            if not wanted:
                raise ValueError("Name at least one issue to acquire")
            issues = [issue for issue in issues if str(issue.get("number") or "").strip() in wanted]
            if not issues:
                raise ValueError("Those issues are not in this run's issue list")

        # Following is what makes a run keep being checked. A request for named
        # issues asks for those comics once, so it does not turn monitoring on.
        if not narrow:
            if scope == "collection":
                self.set_collection_monitoring(scope_id, preference, include)
            else:
                self.set_series_monitoring(scope_id, preference)
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            # Which open request this joins, if any. The two coverages are not
            # interchangeable: following a run must never adopt a request for
            # named issues -- it would look like following while still covering
            # one comic -- and asking for an issue of a run already followed is
            # already answered by that run's request.
            if scope == "collection":
                existing = connection.execute(
                    """SELECT id, coverage FROM acquisition_requests
                       WHERE scope_type='collection' AND series_family_id=? AND status='open'
                         AND coverage='run'
                       ORDER BY id DESC LIMIT 1""",
                    (scope_id,),
                ).fetchone()
            elif narrow:
                existing = connection.execute(
                    """SELECT id, coverage FROM acquisition_requests
                       WHERE scope_type='series' AND series_run_id=? AND status='open'
                       ORDER BY coverage='run' DESC, id DESC LIMIT 1""",
                    (scope_id,),
                ).fetchone()
            else:
                existing = connection.execute(
                    """SELECT id, coverage FROM acquisition_requests
                       WHERE scope_type='series' AND series_run_id=? AND status='open'
                         AND coverage='run'
                       ORDER BY id DESC LIMIT 1""",
                    (scope_id,),
                ).fetchone()
            if existing:
                request_id = int(existing["id"])
                # Joining a followed run restates nothing about how it is followed.
                if not (narrow and existing["coverage"] == "run"):
                    connection.execute(
                        """UPDATE acquisition_requests
                           SET acquisition_preference=?, include_specials=?, updated_at=? WHERE id=?""",
                        (preference, int(include), now, request_id),
                    )
            else:
                cursor = connection.execute(
                    """INSERT INTO acquisition_requests(
                           scope_type, series_run_id, series_family_id, status,
                           acquisition_preference, include_specials, coverage,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?)""",
                    (
                        scope, scope_id if scope == "series" else None,
                        scope_id if scope == "collection" else None,
                        preference, int(include), "issues" if narrow else "run", now, now,
                    ),
                )
                request_id = int(cursor.lastrowid)
            connection.executemany(
                """INSERT OR IGNORE INTO acquisition_request_issues(request_id, issue_id, created_at)
                   VALUES (?, ?, ?)""",
                [(request_id, int(issue["id"]), now) for issue in issues],
            )
        refreshed = self.catalog()
        return next(item for item in refreshed["requests"] if item["id"] == str(request_id))

    def request_file_replacement(
        self, file_id: int, reason_code: str, desired_language: str | None = None,
        acquisition_preference: str = "either",
    ) -> dict[str, Any]:
        """Queue a replacement without treating the damaged file as missing ownership."""
        reason = str(reason_code or "").strip().casefold()
        allowed = {"corrupt", "no_pages", "empty", "wrong_language", "wrong_release", "poor_quality"}
        if reason not in allowed:
            raise ValueError("Choose a supported replacement reason")
        language = str(desired_language or "").strip() or None
        if reason == "wrong_language" and not language:
            raise ValueError("Choose the language wanted for the replacement")
        preference = str(acquisition_preference or "either").strip().casefold()
        if preference not in {"either", "issues", "volumes"}:
            raise ValueError("Replacement preference must be issues, volumes, or either")
        if preference == "volumes":
            raise ValueError(
                "Volume acquisition is not available yet. Choose issues or either so Flipparr can replace the mapped issues safely."
            )
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            file_row = connection.execute(
                "SELECT id FROM files WHERE id=? AND present=1", (file_id,)
            ).fetchone()
            if not file_row:
                raise ValueError("Library file was not found")
            targets = list(connection.execute(
                """SELECT DISTINCT issues.id, issues.series_run_id
                   FROM file_issue_links
                   JOIN issues ON issues.id=file_issue_links.issue_id
                   WHERE file_issue_links.file_id=?
                   UNION
                   SELECT DISTINCT issues.id, issues.series_run_id
                   FROM file_edition_links
                   JOIN edition_coverage_claims
                     ON edition_coverage_claims.edition_id=file_edition_links.edition_id
                   JOIN issues ON issues.id=edition_coverage_claims.issue_id
                   WHERE file_edition_links.file_id=?
                     AND edition_coverage_claims.resolution_status='resolved'
                     AND edition_coverage_claims.relation_kind='full_issue'
                     AND NOT EXISTS(
                         SELECT 1 FROM edition_coverage_overrides
                         WHERE edition_coverage_overrides.edition_id=file_edition_links.edition_id
                           AND edition_coverage_overrides.series_run_id=issues.series_run_id
                           AND edition_coverage_overrides.issue_number=issues.issue_number
                           AND edition_coverage_overrides.action='remove'
                     )
                   UNION
                   SELECT DISTINCT issues.id, issues.series_run_id
                   FROM file_edition_links
                   JOIN edition_coverage_overrides
                     ON edition_coverage_overrides.edition_id=file_edition_links.edition_id
                    AND edition_coverage_overrides.action='add'
                   JOIN issues
                     ON issues.series_run_id=edition_coverage_overrides.series_run_id
                    AND issues.issue_number=edition_coverage_overrides.issue_number
                   WHERE file_edition_links.file_id=?""",
                (file_id, file_id, file_id),
            ))
            if not targets:
                raise ValueError(
                    "Flipparr cannot replace this comic safely until its issue contents are mapped. Open the volume and confirm its issues first."
                )
            existing = connection.execute(
                "SELECT * FROM file_replacement_requests WHERE file_id=?", (file_id,)
            ).fetchone()
            acquisition_request_id = None
            if (
                existing
                and existing["acquisition_request_id"] is not None
                and existing["status"] not in {"cancelled", "fulfilled"}
            ):
                acquisition_request_id = int(existing["acquisition_request_id"])
                connection.execute(
                    """UPDATE acquisition_requests
                       SET status='open', acquisition_preference=?, updated_at=? WHERE id=?""",
                    (preference, now, acquisition_request_id),
                )
            else:
                cursor = connection.execute(
                    """INSERT INTO acquisition_requests(
                           scope_type, series_run_id, series_family_id, status,
                           acquisition_preference, include_specials, coverage,
                           created_at, updated_at
                       ) VALUES ('series', ?, NULL, 'open', ?, 0, 'issues', ?, ?)""",
                    (int(targets[0]["series_run_id"]), preference, now, now),
                )
                acquisition_request_id = int(cursor.lastrowid)
            connection.executemany(
                """INSERT OR IGNORE INTO acquisition_request_issues(request_id, issue_id, created_at)
                   VALUES (?, ?, ?)""",
                [(acquisition_request_id, int(target["id"]), now) for target in targets],
            )
            if existing:
                request_id = int(existing["id"])
                connection.execute(
                    """UPDATE file_replacement_requests
                       SET reason_code=?, desired_language=?, acquisition_preference=?,
                           acquisition_request_id=?, quarantine_path=NULL,
                           status='wanted', error=NULL, updated_at=?
                       WHERE id=?""",
                    (reason, language, preference, acquisition_request_id, now, request_id),
                )
            else:
                cursor = connection.execute(
                    """INSERT INTO file_replacement_requests(
                           file_id, reason_code, desired_language, acquisition_preference,
                           acquisition_request_id, status, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'wanted', ?, ?)""",
                    (file_id, reason, language, preference, acquisition_request_id, now, now),
                )
                request_id = int(cursor.lastrowid)
        self.reconcile_acquisition_jobs(acquisition_request_id)
        refreshed = self.catalog()
        return next(
            item for item in refreshed["replacementRequests"] if item["id"] == str(request_id)
        )

    def update_file_replacement_status(
        self, request_id: int, status: str, error: str | None = None
    ) -> dict[str, Any]:
        desired = str(status or "").strip().casefold()
        allowed = {"wanted", "searching", "grabbed", "failed", "fulfilled", "cancelled"}
        if desired not in allowed:
            raise ValueError("Unsupported replacement status")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id, acquisition_request_id FROM file_replacement_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                raise ValueError("Replacement request was not found")
            connection.execute(
                """UPDATE file_replacement_requests
                   SET status=?, error=?, updated_at=? WHERE id=?""",
                (desired, str(error or "").strip() or None if desired == "failed" else None, now, request_id),
            )
            if row["acquisition_request_id"] is not None and desired in {"cancelled", "fulfilled"}:
                connection.execute(
                    "UPDATE acquisition_requests SET status=?, updated_at=? WHERE id=?",
                    (desired, now, int(row["acquisition_request_id"])),
                )
                if desired == "cancelled":
                    connection.execute(
                        """UPDATE acquisition_jobs SET status='cancelled', queue_reason='Request cancelled',
                                  error=NULL, updated_at=?
                           WHERE request_id=? AND status NOT IN ('fulfilled', 'cancelled')""",
                        (now, int(row["acquisition_request_id"])),
                    )
        refreshed = self.catalog()
        return next(
            item for item in refreshed["replacementRequests"] if item["id"] == str(request_id)
        )

    def delete_pulled_issues(
        self, request_id: int, issue_ids: Sequence[int] | None = None
    ) -> dict[str, Any]:
        """Take issues pulled by name off the Pull List, as if never asked for.

        Deleted rather than cancelled: a pull is one click to make again, and a
        cancelled record would sit on the Pull List as a comic nobody wants.
        Only a request for named issues can be deleted -- a followed run is
        stopped by unfollowing it. Without `issue_ids` the whole pull goes, and
        so does a request whose last issue is deleted.

        An issue SABnzbd is still working on stays. The job is what imports the
        finished download, so deleting it would leave the file to land with
        nothing to claim it; the same holds for a search that is out right now.
        """
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id, coverage FROM acquisition_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                raise ValueError("That pull is no longer on the Pull List")
            if row["coverage"] != "issues":
                raise ValueError("A followed run is stopped by unfollowing it, not deleted")
            enrolled = {
                int(item["issue_id"]) for item in connection.execute(
                    "SELECT issue_id FROM acquisition_request_issues WHERE request_id=?",
                    (request_id,),
                )
            }
            targets = set(enrolled) if issue_ids is None else {int(value) for value in issue_ids}
            if not targets or not targets <= enrolled:
                raise ValueError("That issue is not part of this pull")
            marks = ",".join("?" for _ in targets)
            busy = connection.execute(
                f"""SELECT COUNT(*) FROM acquisition_jobs
                    LEFT JOIN acquisition_downloads
                      ON acquisition_downloads.job_id=acquisition_jobs.id
                    WHERE acquisition_jobs.request_id=? AND acquisition_jobs.issue_id IN ({marks})
                      AND (acquisition_jobs.status='searching'
                           OR acquisition_downloads.status IN
                              ('queued', 'downloading', 'completed', 'importing', 'waiting_for_files'))""",
                (request_id, *targets),
            ).fetchone()[0]
            if busy:
                raise ValueError(
                    "That issue is downloading. Delete it once it has finished or failed."
                )
            # Events, downloads and failed releases cascade from the job.
            connection.execute(
                f"DELETE FROM acquisition_jobs WHERE request_id=? AND issue_id IN ({marks})",
                (request_id, *targets),
            )
            connection.execute(
                f"DELETE FROM acquisition_request_issues WHERE request_id=? AND issue_id IN ({marks})",
                (request_id, *targets),
            )
            emptied = not (enrolled - targets)
            if emptied:
                connection.execute("DELETE FROM acquisition_requests WHERE id=?", (request_id,))
        return {"deleted": len(targets), "requestDeleted": emptied}

    # Whether one comic file stands for one issue: directly, or as a collected
    # edition whose contents have been resolved. Written once because the
    # replacement flow has been wrong twice by joining on the request alone --
    # a request is shared by a whole run, a file is not.
    _FILE_COVERS_ISSUE = """(
        EXISTS(
            SELECT 1 FROM file_issue_links
            WHERE file_issue_links.file_id={file_id}
              AND file_issue_links.issue_id={issue_id}
        )
        OR EXISTS(
            SELECT 1 FROM file_edition_links
            JOIN edition_coverage_claims
              ON edition_coverage_claims.edition_id=file_edition_links.edition_id
            WHERE file_edition_links.file_id={file_id}
              AND edition_coverage_claims.issue_id={issue_id}
              AND edition_coverage_claims.resolution_status='resolved'
        )
    )"""

    def replacement_for_job(self, job_id: int) -> dict[str, Any] | None:
        """Return replacement/quarantine context for a durable acquisition job."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT file_replacement_requests.id,
                          file_replacement_requests.file_id,
                          file_replacement_requests.acquisition_request_id,
                          file_replacement_requests.status,
                          file_replacement_requests.quarantine_path,
                          files.path AS original_path,
                          library_roots.path AS library_root,
                          (SELECT COUNT(*) FROM acquisition_jobs AS replacement_jobs
                           WHERE replacement_jobs.request_id=
                                 file_replacement_requests.acquisition_request_id
                             AND replacement_jobs.status!='fulfilled'
                             -- Counted across the whole request, an unrelated
                             -- issue kept the replacement unfinished forever.
                             AND {covers_replacement_job}) AS unfinished_jobs
                   FROM acquisition_jobs
                   JOIN file_replacement_requests
                     ON file_replacement_requests.acquisition_request_id=acquisition_jobs.request_id
                    -- Matched on the request alone, importing any issue in the
                    -- run looked like this comic's replacement arriving, and
                    -- would have retired the original on the strength of it.
                    AND {covers_this_job}
                   JOIN files ON files.id=file_replacement_requests.file_id
                   JOIN library_roots ON library_roots.id=files.root_id
                   WHERE acquisition_jobs.id=?
                   """.format(
                    covers_replacement_job=self._FILE_COVERS_ISSUE.format(
                        file_id="file_replacement_requests.file_id",
                        issue_id="replacement_jobs.issue_id",
                    ),
                    covers_this_job=self._FILE_COVERS_ISSUE.format(
                        file_id="file_replacement_requests.file_id",
                        issue_id="acquisition_jobs.issue_id",
                    ),
                ),
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def record_replacement_quarantine(
        self, replacement_id: int, original_path: str, quarantine_path: str
    ) -> None:
        """Move the original catalog record out of the active library namespace."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT file_id FROM file_replacement_requests WHERE id=?", (replacement_id,)
            ).fetchone()
            if not row:
                raise ValueError("Replacement request was not found")
            connection.execute(
                """UPDATE files SET path=?, filename=?, present=0, updated_at=?
                   WHERE id=? AND path=?""",
                (quarantine_path, Path(quarantine_path).name, now, int(row["file_id"]), original_path),
            )
            connection.execute(
                "UPDATE file_replacement_requests SET quarantine_path=?, updated_at=? WHERE id=?",
                (quarantine_path, now, replacement_id),
            )

    def restore_replacement_original(
        self, replacement_id: int, quarantine_path: str, original_path: str,
        failed_new_path: str | None = None,
    ) -> None:
        """Restore catalog state when a recoverable filesystem swap fails."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT file_id FROM file_replacement_requests WHERE id=?", (replacement_id,)
            ).fetchone()
            if row:
                if failed_new_path:
                    connection.execute(
                        """UPDATE files SET path=?, filename=?, present=0, updated_at=?
                           WHERE path=? AND id!=?""",
                        (
                            failed_new_path, Path(failed_new_path).name, now,
                            original_path, int(row["file_id"]),
                        ),
                    )
                connection.execute(
                    """UPDATE files SET path=?, filename=?, present=1, updated_at=?
                       WHERE id=? AND path=?""",
                    (original_path, Path(original_path).name, now, int(row["file_id"]), quarantine_path),
                )
                connection.execute(
                    "UPDATE file_replacement_requests SET quarantine_path=NULL, updated_at=? WHERE id=?",
                    (now, replacement_id),
                )

    def complete_file_replacement(self, replacement_id: int, quarantine_path: str) -> None:
        """Mark a fully verified replacement and its hidden acquisition request complete."""
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT acquisition_request_id FROM file_replacement_requests WHERE id=?",
                (replacement_id,),
            ).fetchone()
            if not row:
                raise ValueError("Replacement request was not found")
            connection.execute(
                """UPDATE file_replacement_requests
                   SET status='fulfilled', quarantine_path=?, error=NULL, updated_at=? WHERE id=?""",
                (quarantine_path, now, replacement_id),
            )
            if row["acquisition_request_id"] is not None:
                connection.execute(
                    "UPDATE acquisition_requests SET status='fulfilled', updated_at=? WHERE id=?",
                    (now, int(row["acquisition_request_id"])),
                )

    def story_structure_proposal(self, series_family_id: int) -> dict[str, Any]:
        catalog = self.catalog()
        collection = next(
            (item for item in catalog["families"] if item["id"] == str(series_family_id)), None
        )
        if not collection:
            raise ValueError("Collection was not found")
        proposals: list[dict[str, Any]] = []
        assigned_run_ids: set[str] = set()
        for arc in collection.get("storyArcs") or []:
            run_ids = [run["id"] for run in arc.get("runs") or []]
            assigned_run_ids.update(run_ids)
            proposals.append({
                "id": arc["id"], "name": arc["name"], "type": arc["type"],
                "position": arc["position"], "runIds": run_ids,
                "runLabels": [run["title"] for run in arc.get("runs") or []],
                "issueCount": arc["issueCount"], "ownedIssueCount": arc["ownedIssueCount"],
                "volumeCount": arc["volumeCount"], "confidence": arc["confidence"],
                "reason": "Saved collection structure",
            })
        next_position = len(proposals)
        for run in collection.get("runs") or []:
            if run["id"] in assigned_run_ids:
                continue
            issue_count = len(run.get("issues") or [])
            volume_count = len(run.get("editions") or [])
            proposals.append({
                "id": None,
                "name": _suggest_arc_name(collection["name"], run["title"]),
                "type": "main" if issue_count else "specials",
                "position": next_position,
                "runIds": [run["id"]], "runLabels": [run["title"]],
                "issueCount": issue_count,
                "ownedIssueCount": sum(
                    1 for issue in (run.get("issues") or []) if issue["ownership"] != "unowned"
                ),
                "volumeCount": volume_count,
                "confidence": "medium" if issue_count else "low",
                "reason": (
                    f"Numbered issue sequence found ({issue_count} issues)."
                    if issue_count else
                    "No numbered issue sequence is linked; review as a possible special or one-shot group."
                ),
            })
            next_position += 1
        return {
            "collection": {
                "id": collection["id"], "name": collection["name"],
                "publisher": collection["publisher"], "year": collection["year"],
            },
            "arcs": proposals,
            "guidance": (
                "Review the proposed story arcs and specials. Saving changes only the catalog structure; "
                "files, issue identities, and volume metadata remain unchanged."
            ),
        }

    def set_story_structure(self, series_family_id: int, arcs: list[dict[str, Any]]) -> dict[str, Any]:
        now = _utc_now()
        cleaned: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        seen_runs: set[int] = set()
        for position, arc in enumerate(arcs or []):
            name = str(arc.get("name") or "").strip()
            normalized_name = _normalized(name)
            arc_type = str(arc.get("type") or "main")
            if not name or not normalized_name:
                raise ValueError("Every story group needs a name")
            if normalized_name in seen_names:
                raise ValueError(f"Story group names must be unique: {name}")
            if arc_type not in {"main", "specials"}:
                raise ValueError("Story group type must be main or specials")
            run_ids = list(dict.fromkeys(int(value) for value in (arc.get("runIds") or [])))
            if not run_ids:
                raise ValueError(f"{name} must include at least one publication run")
            duplicate_run = next((run_id for run_id in run_ids if run_id in seen_runs), None)
            if duplicate_run is not None:
                raise ValueError("A publication run can belong to only one story group")
            seen_names.add(normalized_name)
            seen_runs.update(run_ids)
            cleaned.append({
                "name": name, "normalizedName": normalized_name, "type": arc_type,
                "position": position, "runIds": run_ids,
                "startYear": arc.get("startYear"),
            })
        with self._write_lock, self._connect() as connection:
            family = connection.execute(
                "SELECT id FROM series_families WHERE id=?", (series_family_id,)
            ).fetchone()
            if not family:
                raise ValueError("Collection was not found")
            family_run_ids = {
                int(row["series_run_id"])
                for row in connection.execute(
                    "SELECT series_run_id FROM series_family_memberships WHERE series_family_id=?",
                    (series_family_id,),
                )
            }
            invalid = sorted(seen_runs - family_run_ids)
            if invalid:
                raise ValueError("Every story-group run must already belong to this collection")
            connection.execute("DELETE FROM story_arcs WHERE series_family_id=?", (series_family_id,))
            for arc in cleaned:
                cursor = connection.execute(
                    """INSERT INTO story_arcs(
                           series_family_id, name, normalized_name, arc_type, position,
                           start_year, source, confidence, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'manual review', 'confirmed', ?, ?)""",
                    (
                        series_family_id, arc["name"], arc["normalizedName"], arc["type"],
                        arc["position"], arc["startYear"], now, now,
                    ),
                )
                arc_id = int(cursor.lastrowid)
                for run_position, run_id in enumerate(arc["runIds"]):
                    connection.execute(
                        """INSERT INTO story_arc_run_memberships(
                               story_arc_id, series_run_id, position, created_at
                           ) VALUES (?, ?, ?, ?)""",
                        (arc_id, run_id, run_position, now),
                    )
            connection.execute(
                "UPDATE series_families SET updated_at=? WHERE id=?", (now, series_family_id)
            )
        collection = next(
            item for item in self.catalog()["families"] if item["id"] == str(series_family_id)
        )
        return {"collection": collection, "arcCount": len(collection.get("storyArcs") or [])}

    @staticmethod
    def _delete_empty_series_families(connection: sqlite3.Connection, family_ids: set[int]) -> None:
        for family_id in family_ids:
            connection.execute(
                """DELETE FROM story_arcs
                   WHERE series_family_id=? AND NOT EXISTS(
                       SELECT 1 FROM story_arc_run_memberships
                       WHERE story_arc_id=story_arcs.id
                   )""",
                (family_id,),
            )
            connection.execute(
                """DELETE FROM series_families
                   WHERE id=? AND NOT EXISTS(
                       SELECT 1 FROM series_family_memberships WHERE series_family_id=?
                   )""",
                (family_id, family_id),
            )

    def _series_merge_preview(
        self, connection: sqlite3.Connection, source_id: int, target_id: int
    ) -> dict[str, Any]:
        if source_id == target_id:
            raise ValueError("Source and target series must be different")
        source = connection.execute("SELECT * FROM series_runs WHERE id=?", (source_id,)).fetchone()
        target = connection.execute("SELECT * FROM series_runs WHERE id=?", (target_id,)).fetchone()
        if not source or not target:
            raise ValueError("One of the series runs was not found")

        source_providers = {
            row["provider"]: row for row in connection.execute(
                "SELECT * FROM series_provider_ids WHERE series_run_id=?", (source_id,)
            )
        }
        target_providers = {
            row["provider"]: row for row in connection.execute(
                "SELECT * FROM series_provider_ids WHERE series_run_id=?", (target_id,)
            )
        }
        provider_conflicts = [
            {
                "provider": provider,
                "sourceId": str(source_row["provider_id"]),
                "targetId": str(target_providers[provider]["provider_id"]),
            }
            for provider, source_row in source_providers.items()
            if provider in target_providers
            and str(source_row["provider_id"]) != str(target_providers[provider]["provider_id"])
        ]
        source_family = connection.execute(
            "SELECT series_family_id FROM series_family_memberships WHERE series_run_id=?", (source_id,)
        ).fetchone()
        target_family = connection.execute(
            "SELECT series_family_id FROM series_family_memberships WHERE series_run_id=?", (target_id,)
        ).fetchone()
        source_arc = connection.execute(
            "SELECT story_arc_id FROM story_arc_run_memberships WHERE series_run_id=?", (source_id,)
        ).fetchone()
        target_arc = connection.execute(
            "SELECT story_arc_id FROM story_arc_run_memberships WHERE series_run_id=?", (target_id,)
        ).fetchone()
        blockers = []
        if source_family and target_family and source_family["series_family_id"] != target_family["series_family_id"]:
            blockers.append("The runs belong to different collections")
        if source_arc and target_arc and source_arc["story_arc_id"] != target_arc["story_arc_id"]:
            blockers.append("The runs belong to different story groups")
        source_open_request = connection.execute(
            "SELECT id FROM acquisition_requests WHERE series_run_id=? AND status='open' LIMIT 1", (source_id,)
        ).fetchone()
        target_open_request = connection.execute(
            "SELECT id FROM acquisition_requests WHERE series_run_id=? AND status='open' LIMIT 1", (target_id,)
        ).fetchone()
        if source_open_request and target_open_request:
            blockers.append("Both runs have active wanted lists")
        overlapping_job_count = int(connection.execute(
            """SELECT COUNT(*) AS count
               FROM issues AS source_issue
               JOIN issues AS target_issue
                 ON target_issue.series_run_id=?
                AND target_issue.issue_number=source_issue.issue_number
               JOIN acquisition_jobs AS source_job ON source_job.issue_id=source_issue.id
               JOIN acquisition_jobs AS target_job
                 ON target_job.issue_id=target_issue.id
                AND target_job.request_id=source_job.request_id
               WHERE source_issue.series_run_id=?""",
            (target_id, source_id),
        ).fetchone()["count"])
        if overlapping_job_count:
            blockers.append(
                "The same issue has download history on both runs; finish or cancel one copy first"
            )

        def counts(run_id: int) -> dict[str, int]:
            return {
                "files": int(connection.execute(
                    "SELECT COUNT(*) AS count FROM file_identities WHERE series_run_id=?", (run_id,)
                ).fetchone()["count"]),
                "issues": int(connection.execute(
                    "SELECT COUNT(*) AS count FROM issues WHERE series_run_id=?", (run_id,)
                ).fetchone()["count"]),
                "volumes": int(connection.execute(
                    "SELECT COUNT(*) AS count FROM editions WHERE series_run_id=?", (run_id,)
                ).fetchone()["count"]),
            }

        def summary(row: sqlite3.Row) -> dict[str, Any]:
            return {
                "id": str(row["id"]), "title": row["canonical_title"],
                "year": row["start_year"], "publisher": row["publisher"],
                "counts": counts(int(row["id"])),
            }

        return {
            "source": summary(source),
            "target": summary(target),
            "providerConflicts": provider_conflicts,
            "blockers": blockers,
            "canMerge": not blockers and not provider_conflicts,
            "requiresProviderConfirmation": bool(provider_conflicts) and not blockers,
        }

    def series_merge_preview(self, source_id: int, target_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            return self._series_merge_preview(connection, source_id, target_id)

    def merge_series(
        self, source_id: int, target_id: int, allow_provider_conflicts: bool = False
    ) -> dict[str, Any]:
        if source_id == target_id:
            raise ValueError("Source and target series must be different")
        with self._write_lock, self._connect() as connection:
            preview = self._series_merge_preview(connection, source_id, target_id)
            if preview["blockers"]:
                raise ValueError("Cannot combine these runs: " + "; ".join(preview["blockers"]))
            if preview["providerConflicts"] and not allow_provider_conflicts:
                providers = ", ".join(item["provider"] for item in preview["providerConflicts"])
                raise ValueError(
                    "The runs have different provider identities for " + providers
                    + ". Review the warning and explicitly confirm the merge."
                )
            source = connection.execute("SELECT * FROM series_runs WHERE id=?", (source_id,)).fetchone()
            target = connection.execute("SELECT * FROM series_runs WHERE id=?", (target_id,)).fetchone()
            if not source or not target:
                raise ValueError("One of the canonical series was not found")
            # So do the reading lists it was on.
            connection.execute(
                """INSERT OR IGNORE INTO series_run_bookmarks(user_id, series_run_id, created_at)
                   SELECT user_id, ?, created_at FROM series_run_bookmarks WHERE series_run_id=?""",
                (target_id, source_id),
            )
            connection.execute("DELETE FROM series_run_bookmarks WHERE series_run_id=?", (source_id,))
            # Its collections follow it; a collection that held both keeps one.
            connection.execute(
                """INSERT OR IGNORE INTO run_collection_items(collection_id, series_run_id, position, added_at)
                   SELECT collection_id, ?, position, added_at FROM run_collection_items WHERE series_run_id=?""",
                (target_id, source_id),
            )
            connection.execute("DELETE FROM run_collection_items WHERE series_run_id=?", (source_id,))
            connection.execute(
                "UPDATE run_collections SET cover_series_run_id=? WHERE cover_series_run_id=?", (target_id, source_id)
            )
            # Requests belong to the logical run. Move their complete history,
            # not only the currently open wanted list, before re-homing issues.
            connection.execute(
                "UPDATE acquisition_requests SET series_run_id=?, updated_at=? WHERE series_run_id=?",
                (target_id, _utc_now(), source_id),
            )
            connection.execute("UPDATE file_identities SET series_run_id=?, updated_at=? WHERE series_run_id=?", (target_id, _utc_now(), source_id))
            for issue in connection.execute("SELECT * FROM issues WHERE series_run_id=?", (source_id,)):
                target_issue = connection.execute(
                    "SELECT id FROM issues WHERE series_run_id=? AND issue_number=?",
                    (target_id, issue["issue_number"]),
                ).fetchone()
                if target_issue:
                    # Preserve request membership before removing the duplicate
                    # issue record. INSERT OR IGNORE makes repeated repair safe.
                    for request_issue in connection.execute(
                        "SELECT request_id, created_at FROM acquisition_request_issues WHERE issue_id=?",
                        (issue["id"],),
                    ).fetchall():
                        connection.execute(
                            "INSERT OR IGNORE INTO acquisition_request_issues(request_id, issue_id, created_at) VALUES (?, ?, ?)",
                            (request_issue["request_id"], target_issue["id"], request_issue["created_at"]),
                        )
                    connection.execute("DELETE FROM acquisition_request_issues WHERE issue_id=?", (issue["id"],))
                    # The preview rejects the only unsafe collision: two jobs
                    # for the same request/issue. Preserve remaining jobs and
                    # their download/event history by changing the issue key.
                    connection.execute(
                        "UPDATE acquisition_jobs SET issue_id=?, updated_at=? WHERE issue_id=?",
                        (target_issue["id"], _utc_now(), issue["id"]),
                    )
                    source_override = connection.execute(
                        "SELECT * FROM issue_metadata_overrides WHERE issue_id=?", (issue["id"],)
                    ).fetchone()
                    target_override = connection.execute(
                        "SELECT 1 FROM issue_metadata_overrides WHERE issue_id=?", (target_issue["id"],)
                    ).fetchone()
                    if source_override and not target_override:
                        connection.execute(
                            "UPDATE issue_metadata_overrides SET issue_id=? WHERE issue_id=?",
                            (target_issue["id"], issue["id"]),
                        )
                    elif source_override:
                        connection.execute("DELETE FROM issue_metadata_overrides WHERE issue_id=?", (issue["id"],))
                    connection.execute(
                        "UPDATE issue_metadata_override_history SET issue_id=? WHERE issue_id=?",
                        (target_issue["id"], issue["id"]),
                    )
                    connection.execute("UPDATE file_issue_links SET issue_id=? WHERE issue_id=?", (target_issue["id"], issue["id"]))
                    connection.execute("UPDATE edition_coverage_claims SET issue_id=? WHERE issue_id=?", (target_issue["id"], issue["id"]))
                    for provider_id in connection.execute(
                        "SELECT * FROM issue_provider_ids WHERE issue_id=?", (issue["id"],)
                    ):
                        existing_provider = connection.execute(
                            "SELECT id FROM issue_provider_ids WHERE issue_id=? AND provider=?",
                            (target_issue["id"], provider_id["provider"]),
                        ).fetchone()
                        if existing_provider:
                            connection.execute("DELETE FROM issue_provider_ids WHERE id=?", (provider_id["id"],))
                        else:
                            connection.execute(
                                "UPDATE issue_provider_ids SET issue_id=?, updated_at=? WHERE id=?",
                                (target_issue["id"], _utc_now(), provider_id["id"]),
                            )
                    connection.execute("DELETE FROM issues WHERE id=?", (issue["id"],))
                else:
                    connection.execute("UPDATE issues SET series_run_id=?, updated_at=? WHERE id=?", (target_id, _utc_now(), issue["id"]))
            connection.execute("UPDATE editions SET series_run_id=?, updated_at=? WHERE series_run_id=?", (target_id, _utc_now(), source_id))
            # Coverage corrections describe the edition, not the accidental run
            # split. Re-home them and collapse exact duplicates.
            for override in connection.execute(
                "SELECT * FROM edition_coverage_overrides WHERE series_run_id=?", (source_id,)
            ).fetchall():
                connection.execute(
                    """INSERT OR IGNORE INTO edition_coverage_overrides(
                           edition_id, series_run_id, issue_number, action, note, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        override["edition_id"], target_id, override["issue_number"], override["action"],
                        override["note"], override["created_at"], override["updated_at"],
                    ),
                )
            connection.execute("DELETE FROM edition_coverage_overrides WHERE series_run_id=?", (source_id,))
            target_provider_names = {
                row["provider"] for row in connection.execute(
                    "SELECT provider FROM series_provider_ids WHERE series_run_id=?", (target_id,)
                )
            }
            for provider in connection.execute(
                "SELECT * FROM series_provider_ids WHERE series_run_id=?", (source_id,)
            ):
                if provider["provider"] in target_provider_names:
                    connection.execute("DELETE FROM series_provider_ids WHERE id=?", (provider["id"],))
                else:
                    connection.execute(
                        "UPDATE series_provider_ids SET series_run_id=?, updated_at=? WHERE id=?",
                        (target_id, _utc_now(), provider["id"]),
                    )
            source_catalog = connection.execute(
                "SELECT * FROM issue_catalog_status WHERE series_run_id=?", (source_id,)
            ).fetchone()
            target_catalog = connection.execute(
                "SELECT * FROM issue_catalog_status WHERE series_run_id=?", (target_id,)
            ).fetchone()
            if source_catalog and not target_catalog:
                connection.execute(
                    "UPDATE issue_catalog_status SET series_run_id=? WHERE series_run_id=?",
                    (target_id, source_id),
                )
            # Aliases are globally unique. Inserting the source aliases here only
            # conflicts with their existing rows and then loses them when the
            # source run is deleted. Reassign the durable rows instead so future
            # filename scans keep resolving to the healed canonical run.
            connection.execute(
                """UPDATE series_aliases
                   SET series_run_id=?, source='manual merge', confirmed=1
                   WHERE series_run_id=?""",
                (target_id, source_id),
            )
            source_family = connection.execute(
                "SELECT series_family_id FROM series_family_memberships WHERE series_run_id=?", (source_id,)
            ).fetchone()
            target_family = connection.execute(
                "SELECT 1 FROM series_family_memberships WHERE series_run_id=?", (target_id,)
            ).fetchone()
            if source_family and not target_family:
                connection.execute(
                    "UPDATE series_family_memberships SET series_run_id=? WHERE series_run_id=?",
                    (target_id, source_id),
                )
            else:
                connection.execute("DELETE FROM series_family_memberships WHERE series_run_id=?", (source_id,))
            source_arc = connection.execute(
                "SELECT story_arc_id FROM story_arc_run_memberships WHERE series_run_id=?", (source_id,)
            ).fetchone()
            target_arc = connection.execute(
                "SELECT 1 FROM story_arc_run_memberships WHERE series_run_id=?", (target_id,)
            ).fetchone()
            if source_arc and not target_arc:
                connection.execute(
                    "UPDATE story_arc_run_memberships SET series_run_id=? WHERE series_run_id=?",
                    (target_id, source_id),
                )
            else:
                connection.execute("DELETE FROM story_arc_run_memberships WHERE series_run_id=?", (source_id,))
            connection.execute(
                "DELETE FROM metadata_enrichment_jobs WHERE series_run_id=?", (source_id,)
            )
            connection.execute(
                "DELETE FROM series_monitor_refreshes WHERE series_run_id=?", (source_id,)
            )
            if source["monitoring_status"] == "monitored":
                connection.execute(
                    "UPDATE series_runs SET monitoring_status='monitored', updated_at=? WHERE id=?",
                    (_utc_now(), target_id),
                )
            # Keep the strongest human-facing identity after the merge. A
            # filename-derived placeholder such as "birth right" should not
            # win over a provider-confirmed "Birthright", while the earliest
            # known start remains the run boundary.
            def title_quality(value: str | None) -> tuple[int, int, int, int]:
                text = str(value or "").strip()
                # A long digit run is an upload id or timestamp carried in from a
                # filename, never part of a comic's title. Rank a title carrying
                # one below a clean title, ahead of every other signal: otherwise
                # capitalisation alone lets "Saga 1398374447" beat "saga" and the
                # junk name survives the merge the user asked for.
                return (
                    int(not re.search(r"(?<!\d)\d{5,}(?!\d)", text)),
                    int(bool(text) and not text.islower()),
                    int(bool(text) and " " not in text),
                    len(text),
                )

            merged_title = target["canonical_title"]
            if title_quality(source["canonical_title"]) > title_quality(merged_title):
                merged_title = source["canonical_title"]
            known_start_years = [
                int(value) for value in (target["start_year"], source["start_year"])
                if value is not None
            ]
            merged_start_year = min(known_start_years) if known_start_years else None
            merged_publisher = target["publisher"] or source["publisher"]
            connection.execute(
                """UPDATE series_runs
                   SET canonical_title=?, start_year=?, publisher=?, updated_at=?
                   WHERE id=?""",
                (merged_title, merged_start_year, merged_publisher, _utc_now(), target_id),
            )
            connection.execute("DELETE FROM series_runs WHERE id=?", (source_id,))
            return {
                "status": "merged", "seriesId": str(target_id),
                "title": merged_title, "preview": preview,
            }

    def register_root(self, folder: str, recursive: bool) -> int:
        resolved = str(Path(folder).expanduser().resolve())
        if not Path(resolved).is_dir():
            raise ValueError("Library folder does not exist or is not a directory")
        now = _utc_now()
        with self._connect() as connection:
            for row in connection.execute("SELECT id, path FROM library_roots"):
                existing = Path(row["path"])
                candidate = Path(resolved)
                if candidate == existing:
                    continue
                if candidate.is_relative_to(existing) or existing.is_relative_to(candidate):
                    raise ValueError(
                        f"Library folders cannot overlap. {resolved} overlaps the existing source {existing}."
                    )
            connection.execute(
                """INSERT INTO library_roots(path, recursive, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET recursive=excluded.recursive""",
                (resolved, int(recursive), now),
            )
            return int(connection.execute("SELECT id FROM library_roots WHERE path=?", (resolved,)).fetchone()["id"])

    def update_root(self, root_id: int, recursive: bool) -> dict[str, Any]:
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM library_roots WHERE id=?", (root_id,)
            ).fetchone()
            if not row:
                raise ValueError("Library folder was not found")
            connection.execute(
                "UPDATE library_roots SET recursive=? WHERE id=?",
                (int(recursive), root_id),
            )
            result = dict(row)
            result["recursive"] = int(recursive)
            return result

    def remove_root(self, root_id: int) -> dict[str, Any]:
        """Stop tracking one library root without changing anything on disk."""
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM library_roots WHERE id=?", (root_id,)
            ).fetchone()
            if not row:
                raise ValueError("Library folder was not found")
            active_scan = connection.execute(
                "SELECT id FROM scan_runs WHERE root_id=? AND status IN ('queued', 'scanning') LIMIT 1",
                (root_id,),
            ).fetchone()
            if active_scan:
                raise ValueError("Wait for this folder's scan to finish before removing it")
            file_count = int(connection.execute(
                "SELECT COUNT(*) FROM files WHERE root_id=?", (root_id,)
            ).fetchone()[0])
            connection.execute("DELETE FROM library_roots WHERE id=?", (root_id,))
            return {
                "id": int(root_id),
                "path": row["path"],
                "removedFileRecords": file_count,
                "filesChanged": False,
            }

    def enqueue_metadata_enrichment(self, series_run_ids: Iterable[int] | None = None) -> dict[str, int]:
        """Queue one resumable metadata job per local publication run."""
        requested_ids = {int(value) for value in series_run_ids or []}
        now = _utc_now()
        queued = 0
        retained = 0
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """DELETE FROM metadata_enrichment_jobs
                   WHERE NOT EXISTS(
                       SELECT 1 FROM file_identities
                       JOIN files ON files.id=file_identities.file_id
                       WHERE file_identities.series_run_id=metadata_enrichment_jobs.series_run_id
                         AND files.present=1
                   )"""
            )
            sql = """
                SELECT series_runs.id,
                       COALESCE(issue_catalog_status.status, 'unknown') AS catalog_status,
                       COALESCE(issue_catalog_status.run_end_status, 'unknown') AS run_end_status
                FROM series_runs
                LEFT JOIN issue_catalog_status
                  ON issue_catalog_status.series_run_id=series_runs.id
                WHERE EXISTS(
                    SELECT 1 FROM file_identities
                    JOIN files ON files.id=file_identities.file_id
                    WHERE file_identities.series_run_id=series_runs.id AND files.present=1
                )
            """
            params: tuple[Any, ...] = ()
            if requested_ids:
                placeholders = ",".join("?" for _ in requested_ids)
                sql += f" AND series_runs.id IN ({placeholders})"
                params = tuple(sorted(requested_ids))
            for row in connection.execute(sql, params):
                series_run_id = int(row["id"])
                # A run whose end year no provider has recorded is not done
                # being enriched, whatever its coverage claim says. Without
                # this the startup pass force-completes exactly the runs the
                # version-29 migration queued, one second after queueing them.
                catalog_complete = (
                    row["catalog_status"] in {"complete", "complete_to_date"}
                    and row["run_end_status"] != "unknown"
                )
                existing = connection.execute(
                    "SELECT status FROM metadata_enrichment_jobs WHERE series_run_id=?",
                    (series_run_id,),
                ).fetchone()
                if catalog_complete:
                    if existing and existing["status"] != "complete":
                        connection.execute(
                            """UPDATE metadata_enrichment_jobs
                               SET status='complete', last_error=NULL, next_attempt_at=NULL,
                                   completed_at=?, updated_at=? WHERE series_run_id=?""",
                            (now, now, series_run_id),
                        )
                    retained += 1
                    continue
                if existing:
                    retained += 1
                    continue
                connection.execute(
                    """INSERT INTO metadata_enrichment_jobs(
                           series_run_id, status, priority, created_at, updated_at
                       ) VALUES (?, 'queued', 100, ?, ?)""",
                    (series_run_id, now, now),
                )
                queued += 1
        return {"queued": queued, "retained": retained}

    def resume_metadata_enrichment(self) -> None:
        """Return work interrupted by a process restart to the durable queue."""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE metadata_enrichment_jobs
                   SET status='waiting', next_attempt_at=?,
                       last_error=COALESCE(last_error, 'Interrupted by restart; retrying safely.'),
                       updated_at=?
                   WHERE status='running'""",
                (now, now),
            )

    def claim_metadata_enrichment_job(self) -> dict[str, Any] | None:
        """Atomically claim the next due series job for a single coordinator worker."""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT metadata_enrichment_jobs.*, series_runs.canonical_title,
                          series_runs.start_year, series_runs.publisher
                   FROM metadata_enrichment_jobs
                   JOIN series_runs ON series_runs.id=metadata_enrichment_jobs.series_run_id
                   WHERE metadata_enrichment_jobs.status IN ('queued', 'waiting')
                     AND (metadata_enrichment_jobs.next_attempt_at IS NULL
                          OR metadata_enrichment_jobs.next_attempt_at<=?)
                   ORDER BY metadata_enrichment_jobs.priority, metadata_enrichment_jobs.id
                   LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            connection.execute(
                """UPDATE metadata_enrichment_jobs
                   SET status='running', attempt_count=attempt_count+1,
                       updated_at=?, last_error=NULL WHERE id=?""",
                (now, row["id"]),
            )
            connection.commit()
        result = dict(row)
        result["status"] = "running"
        result["attempt_count"] = int(result["attempt_count"]) + 1
        return result

    def finish_metadata_enrichment_job(
        self,
        job_id: int,
        status: str,
        provider: str | None = None,
        error: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        if status not in {"queued", "waiting", "complete", "review", "failed"}:
            raise ValueError("Unsupported metadata enrichment job status")
        now_dt = dt.datetime.now(dt.timezone.utc)
        now = now_dt.isoformat()
        next_attempt = None
        completed_at = now if status in {"complete", "review", "failed"} else None
        if status == "waiting":
            delay = max(30, int(retry_after_seconds or 300))
            next_attempt = (now_dt + dt.timedelta(seconds=delay)).isoformat()
        with self._connect() as connection:
            connection.execute(
                """UPDATE metadata_enrichment_jobs
                   SET status=?, provider=?, next_attempt_at=?, last_error=?,
                       completed_at=?, updated_at=? WHERE id=?""",
                (status, provider, next_attempt, error, completed_at, now, job_id),
            )

    def metadata_provider_available(self, provider: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT next_allowed_at FROM metadata_provider_state WHERE provider=?",
                (provider,),
            ).fetchone()
        return not row or not row["next_allowed_at"] or row["next_allowed_at"] <= now

    def metadata_provider_retry_seconds(
        self, providers: Iterable[str], default: int = 60
    ) -> int:
        """Seconds until the earliest of these providers may be called again.

        Callers used to report a flat minute regardless of the real wait, so a
        15-second per-series cooldown stalled enrichment for 60 -- four times
        longer than the pacing actually asks for, on every series in the run.
        """
        names = [str(provider) for provider in providers]
        if not names:
            return default
        placeholders = ",".join("?" for _ in names)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT next_allowed_at FROM metadata_provider_state
                    WHERE provider IN ({placeholders})
                      AND next_allowed_at IS NOT NULL""",
                names,
            ).fetchall()
        now = dt.datetime.now(dt.timezone.utc)
        waits = []
        for row in rows:
            try:
                allowed_at = dt.datetime.fromisoformat(str(row["next_allowed_at"]))
            except ValueError:
                continue
            if allowed_at.tzinfo is None:
                allowed_at = allowed_at.replace(tzinfo=dt.timezone.utc)
            waits.append((allowed_at - now).total_seconds())
        if not waits:
            return default
        # A provider with no recorded wait is already free; one already past its
        # window rounds up to a second rather than to zero, so a caller that
        # sleeps on this value always makes progress.
        return max(1, min(default, math.ceil(min(waits))))

    def record_metadata_provider_outcome(
        self,
        provider: str,
        error: str | None = None,
        retry_after_seconds: int | None = None,
        minimum_delay_seconds: int = 0,
    ) -> None:
        now_dt = dt.datetime.now(dt.timezone.utc)
        now = now_dt.isoformat()
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT consecutive_failures FROM metadata_provider_state WHERE provider=?",
                (provider,),
            ).fetchone()
            failures = 0 if not error else int(previous["consecutive_failures"] if previous else 0) + 1
            next_allowed = (
                (now_dt + dt.timedelta(seconds=max(0, int(minimum_delay_seconds)))).isoformat()
                if minimum_delay_seconds else None
            )
            if error:
                delay = max(30, int(retry_after_seconds or min(3600, 30 * (2 ** min(failures, 6)))))
                next_allowed = (now_dt + dt.timedelta(seconds=delay)).isoformat()
            connection.execute(
                """INSERT INTO metadata_provider_state(
                       provider, next_allowed_at, consecutive_failures, last_error, updated_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(provider) DO UPDATE SET
                       next_allowed_at=excluded.next_allowed_at,
                       consecutive_failures=excluded.consecutive_failures,
                       last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (provider, next_allowed, failures, error, now),
            )

    def metadata_enrichment_summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            counts = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM metadata_enrichment_jobs GROUP BY status"
                )
            }
            total = sum(counts.values())
            next_jobs = [
                {
                    "id": str(row["id"]), "seriesId": str(row["series_run_id"]),
                    "title": row["canonical_title"], "status": row["status"],
                    "provider": row["provider"], "attemptCount": int(row["attempt_count"]),
                    "nextAttemptAt": row["next_attempt_at"], "error": row["last_error"],
                }
                for row in connection.execute(
                    """SELECT metadata_enrichment_jobs.*, series_runs.canonical_title
                       FROM metadata_enrichment_jobs
                       JOIN series_runs ON series_runs.id=metadata_enrichment_jobs.series_run_id
                       WHERE metadata_enrichment_jobs.status!='complete'
                       ORDER BY CASE metadata_enrichment_jobs.status
                                    WHEN 'running' THEN 0
                                    WHEN 'queued' THEN 1
                                    WHEN 'waiting' THEN 2
                                    WHEN 'review' THEN 3
                                    ELSE 4
                                END,
                                metadata_enrichment_jobs.priority, metadata_enrichment_jobs.id
                       LIMIT 8"""
                )
            ]
            provider_cooldowns = [
                {
                    "provider": row["provider"], "nextRetryAt": row["next_allowed_at"],
                    "error": row["last_error"],
                }
                for row in connection.execute(
                    """SELECT provider, next_allowed_at, last_error
                       FROM metadata_provider_state
                       WHERE next_allowed_at IS NOT NULL AND next_allowed_at>?
                         AND last_error IS NOT NULL
                       ORDER BY next_allowed_at""",
                    (_utc_now(),),
                )
            ]
        active = counts.get("queued", 0) + counts.get("running", 0) + counts.get("waiting", 0)
        return {
            "total": total, "complete": counts.get("complete", 0),
            "queued": counts.get("queued", 0), "running": counts.get("running", 0),
            "waiting": counts.get("waiting", 0), "review": counts.get("review", 0),
            "failed": counts.get("failed", 0), "active": active,
            "nextJobs": next_jobs, "providerCooldowns": provider_cooldowns,
        }

    @staticmethod
    def _upsert_issue_provider_id(
        connection: sqlite3.Connection,
        issue_id: int,
        provider: str,
        provider_id: str,
        api_url: str | None,
        updated_at: str,
    ) -> None:
        """Attach one provider identity without tripping either uniqueness rule.

        Provider refreshes can legitimately replace the external ID previously
        attached to an issue. Likewise, identity reconciliation can move a known
        external ID to a newly canonicalized issue. SQLite UPSERT can target only
        one of those constraints at a time, so reconcile both rows explicitly.
        """
        issue_row = connection.execute(
            "SELECT id FROM issue_provider_ids WHERE issue_id=? AND provider=?",
            (issue_id, provider),
        ).fetchone()
        provider_row = connection.execute(
            "SELECT id FROM issue_provider_ids WHERE provider=? AND provider_id=?",
            (provider, provider_id),
        ).fetchone()
        if issue_row and provider_row and int(issue_row["id"]) != int(provider_row["id"]):
            connection.execute("DELETE FROM issue_provider_ids WHERE id=?", (issue_row["id"],))
            issue_row = None
        row = provider_row or issue_row
        if row:
            connection.execute(
                """UPDATE issue_provider_ids
                   SET issue_id=?, provider_id=?, api_url=?, updated_at=? WHERE id=?""",
                (issue_id, provider_id, api_url, updated_at, row["id"]),
            )
        else:
            connection.execute(
                """INSERT INTO issue_provider_ids(
                       issue_id, provider, provider_id, api_url, updated_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (issue_id, provider, provider_id, api_url, updated_at),
            )

    def library_root_paths(self) -> list[str]:
        """The folders the library is configured to hold comics in.

        Used to decide whether a path in a request names a comic this library
        actually holds, so a request cannot reach a file elsewhere on the host.
        """
        with self._connect() as connection:
            return [
                str(row["path"])
                for row in connection.execute("SELECT path FROM library_roots ORDER BY id")
            ]

    def scan_schedule(self) -> dict[str, Any]:
        """What the background scan needs to decide: the folders, when the
        least recently scanned was last scanned, and any scan still running."""
        with self._connect() as connection:
            roots = connection.execute(
                "SELECT path, recursive, last_scan_at FROM library_roots ORDER BY id"
            ).fetchall()
            running = connection.execute(
                """SELECT started_at FROM scan_runs WHERE status IN ('queued', 'scanning')
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        stamps = [row["last_scan_at"] for row in roots if row["last_scan_at"]]
        return {
            "roots": [{"path": row["path"], "recursive": bool(row["recursive"])} for row in roots],
            # A folder never scanned makes the whole library due.
            "lastScanAt": min(stamps) if stamps and len(stamps) == len(roots) else None,
            "activeSince": running["started_at"] if running else None,
        }

    def begin_scan(self, folder: str, recursive: bool) -> int:
        root_id = self.register_root(folder, recursive)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO scan_runs(root_id, status, started_at) VALUES (?, 'queued', ?)",
                (root_id, _utc_now()),
            )
            return int(cursor.lastrowid)

    def get_scan(self, scan_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT scan_runs.*, library_roots.path AS root_path
                   FROM scan_runs JOIN library_roots ON library_roots.id=scan_runs.root_id
                   WHERE scan_runs.id=?""",
                (scan_id,),
            ).fetchone()
        return dict(row) if row else None

    def background_work_summary(self) -> dict[str, Any]:
        """What the background work has in hand, for Settings -> System:
        counts by state, what is next and when, read in one short connection."""
        with self._connect() as connection:
            def counts(sql: str) -> dict[str, int]:
                return {str(row[0]): int(row[1]) for row in connection.execute(sql)}

            wanted = counts("SELECT status, COUNT(*) FROM acquisition_jobs GROUP BY status")
            oldest_waiting = connection.execute(
                "SELECT MIN(created_at) FROM acquisition_jobs WHERE status IN ('queued', 'waiting')").fetchone()[0]
            # In progress the way the importer sees it (pending_acquisition_downloads):
            # a download whose request was cancelled or fulfilled is history, whatever
            # its own row last said -- six such rows from a cancelled request read as
            # "6 downloads in progress" with SABnzbd's queue empty (2026-10-05).
            downloads = [{"source": row[0], "status": row[1], "count": int(row[2])} for row in connection.execute(
                """SELECT acquisition_downloads.source, acquisition_downloads.status, COUNT(*)
                   FROM acquisition_downloads
                   JOIN acquisition_jobs ON acquisition_jobs.id=acquisition_downloads.job_id
                   WHERE acquisition_downloads.status IN ('queued', 'downloading', 'completed', 'importing', 'waiting_for_files')
                     AND acquisition_jobs.status NOT IN ('fulfilled', 'cancelled')
                   GROUP BY acquisition_downloads.source, acquisition_downloads.status
                   ORDER BY acquisition_downloads.source, acquisition_downloads.status""")]
            metadata = counts("SELECT status, COUNT(*) FROM metadata_enrichment_jobs GROUP BY status")
            next_metadata = connection.execute(
                "SELECT MIN(next_attempt_at) FROM metadata_enrichment_jobs WHERE status='waiting'").fetchone()[0]
            providers = [{"provider": row["provider"], "waitUntil": row["next_allowed_at"],
                          "consecutiveFailures": int(row["consecutive_failures"] or 0), "lastError": row["last_error"]}
                         for row in connection.execute(
                             "SELECT provider, next_allowed_at, consecutive_failures, last_error FROM metadata_provider_state "
                             "ORDER BY provider")]
            scan = connection.execute(
                """SELECT status, started_at, completed_at, total_files, processed_files, changed_files, error
                   FROM scan_runs ORDER BY id DESC LIMIT 1""").fetchone()
        return {
            "wanted": {"byStatus": wanted, "oldestWaitingSince": oldest_waiting},
            "downloads": downloads,
            "metadata": {"byStatus": metadata, "nextRetryAt": next_metadata},
            "providers": providers,
            "lastScan": dict(scan) if scan else None,
        }

    def library_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                "files": int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
                "runs": int(connection.execute("SELECT COUNT(*) FROM series_runs").fetchone()[0]),
                "roots": int(connection.execute("SELECT COUNT(*) FROM library_roots").fetchone()[0]),
            }

    def perform_scan(
        self,
        scan_id: int,
        scan_folder: Callable[[str, bool], Iterable[Any]],
        enrich_file: Callable[[Any], dict[str, Any]],
    ) -> None:
        scan = self.get_scan(scan_id)
        if not scan:
            return

        root_id = int(scan["root_id"])
        folder = scan["root_path"]
        with self._connect() as connection:
            recursive = bool(connection.execute("SELECT recursive FROM library_roots WHERE id=?", (root_id,)).fetchone()[0])
            connection.execute("UPDATE scan_runs SET status='scanning' WHERE id=?", (scan_id,))
        try:
            parsed_files = list(scan_folder(folder, recursive))
            with self._connect() as connection:
                existing = {
                    row["path"]: row
                    for row in connection.execute("SELECT * FROM files WHERE root_id=?", (root_id,))
                }
                connection.execute(
                    "UPDATE scan_runs SET total_files=? WHERE id=?", (len(parsed_files), scan_id)
                )

            changed: list[tuple[Any, int, int, str]] = []
            reused = 0
            seen_paths: set[str] = set()
            for parsed in parsed_files:
                path = Path(parsed.path)
                stat = path.stat()
                fingerprint = f"{LOCAL_ANALYSIS_VERSION}:{stat.st_size}:{stat.st_mtime_ns}"
                seen_paths.add(str(path))
                prior = existing.get(str(path))
                if prior and prior["fingerprint"] == fingerprint:
                    reused += 1
                    continue
                changed.append((parsed, stat.st_size, stat.st_mtime_ns, fingerprint))

            with self._connect() as connection:
                connection.execute(
                    "UPDATE scan_runs SET changed_files=?, reused_files=?, processed_files=? WHERE id=?",
                    (len(changed), reused, reused, scan_id),
                )

            def safe_enrich(item: tuple[Any, int, int, str]) -> tuple[Any, int, int, str, dict[str, Any]]:
                parsed, size, mtime_ns, fingerprint = item
                try:
                    result = enrich_file(parsed)
                except Exception as exc:  # A single adapter failure must not abort inventory.
                    result = {
                        "parsed": asdict(parsed),
                        "lookup_identity": asdict(parsed),
                        "file_health": {"status": "unknown", "code": "analysis_failed", "message": str(exc)},
                        "recommendation": None,
                        "file_cover": None,
                        "errors": {"analysis": f"Unexpected per-file error: {exc}"},
                    }
                return parsed, size, mtime_ns, fingerprint, result

            completed = reused
            if changed:
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(changed))) as executor:
                    for parsed, size, mtime_ns, fingerprint, result in executor.map(safe_enrich, changed):
                        self._upsert_file(root_id, parsed, size, mtime_ns, fingerprint, result)
                        completed += 1
                        with self._connect() as connection:
                            connection.execute(
                                "UPDATE scan_runs SET processed_files=? WHERE id=?", (completed, scan_id)
                            )

            now = _utc_now()
            with self._connect() as connection:
                if seen_paths:
                    placeholders = ",".join("?" for _ in seen_paths)
                    connection.execute(
                        f"UPDATE files SET present=0, updated_at=? WHERE root_id=? AND path NOT IN ({placeholders})",
                        (now, root_id, *sorted(seen_paths)),
                    )
                    connection.execute(
                        f"UPDATE files SET present=1 WHERE root_id=? AND path IN ({placeholders})",
                        (root_id, *sorted(seen_paths)),
                    )
                else:
                    connection.execute("UPDATE files SET present=0, updated_at=? WHERE root_id=?", (now, root_id))
                connection.execute("UPDATE library_roots SET last_scan_at=? WHERE id=?", (now, root_id))
                connection.execute(
                    "UPDATE scan_runs SET status='complete', completed_at=?, processed_files=? WHERE id=?",
                    (now, len(parsed_files), scan_id),
                )
        except Exception as exc:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE scan_runs SET status='failed', completed_at=?, error=? WHERE id=?",
                    (_utc_now(), str(exc), scan_id),
                )

    def _upsert_file(
        self,
        root_id: int,
        parsed: Any,
        size: int,
        mtime_ns: int,
        fingerprint: str,
        result: dict[str, Any],
        identity_override: dict[str, Any] | None = None,
        series_run_id: int | None = None,
    ) -> int:
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO files(
                       root_id, path, filename, extension, size_bytes, mtime_ns, fingerprint,
                       present, parsed_json, result_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       root_id=excluded.root_id, filename=excluded.filename,
                       extension=excluded.extension, size_bytes=excluded.size_bytes,
                       mtime_ns=excluded.mtime_ns, fingerprint=excluded.fingerprint,
                       present=1, parsed_json=excluded.parsed_json,
                       result_json=excluded.result_json, updated_at=excluded.updated_at""",
                (
                    root_id, parsed.path, parsed.filename, parsed.extension, size, mtime_ns,
                    fingerprint, _json(asdict(parsed)), _json(result), now, now,
                ),
            )
            file_id = int(connection.execute("SELECT id FROM files WHERE path=?", (parsed.path,)).fetchone()["id"])
            if identity_override:
                locked = list(identity_override)
                connection.execute(
                    """INSERT INTO file_metadata_overrides(
                           file_id, fields_json, locked_fields_json, match_source,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, 'verified acquisition request', ?, ?)
                       ON CONFLICT(file_id) DO UPDATE SET
                           fields_json=excluded.fields_json,
                           locked_fields_json=excluded.locked_fields_json,
                           match_source=excluded.match_source,
                           updated_at=excluded.updated_at""",
                    (file_id, _json(identity_override), _json(locked), now, now),
                )
                connection.execute(
                    """INSERT INTO metadata_override_history(
                           file_id, action, fields_json, created_at
                       ) VALUES (?, 'verified_acquisition_import', ?, ?)""",
                    (file_id, _json(identity_override), now),
                )
        self._reconcile_file_identity(
            file_id, asdict(parsed), result, identity_override, pinned_run_id=series_run_id,
        )
        return file_id

    def ingest_acquisition_import(
        self,
        library_root: str,
        parsed: Any,
        result: dict[str, Any],
        identity_override: dict[str, Any],
        series_run_id: int | None = None,
    ) -> int:
        """Synchronously add a verified import without re-guessing its requested run.

        `series_run_id` is the run the request named. Without it the run was
        worked out again from the title, and a title is exactly what a series
        and its relaunch have in common: every Nightwing (2016) issue past #3
        was filed under the 2011 run that way.
        """
        root = Path(library_root).resolve()
        path = Path(parsed.path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("The imported comic is outside the configured library root") from exc
        if not path.is_file():
            raise ValueError("The imported comic is no longer present in the library")
        required = {"seriesTitle", "recordType", "issueNumber"}
        if not required.issubset(identity_override) or not all(identity_override.get(key) for key in required):
            raise ValueError("The acquisition request did not provide a complete issue identity")
        root_id = self.register_root(str(root), True)
        stat = path.stat()
        fingerprint = f"{LOCAL_ANALYSIS_VERSION}:{stat.st_size}:{stat.st_mtime_ns}"
        return self._upsert_file(
            root_id,
            parsed,
            stat.st_size,
            stat.st_mtime_ns,
            fingerprint,
            result,
            identity_override,
            series_run_id=int(series_run_id) if series_run_id else None,
        )

    def resolve_review(self, file_path: str, code: str, fingerprint: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO review_resolutions(file_path, reason_code, fingerprint, status, resolved_at)
                   VALUES (?, ?, ?, 'resolved', ?)
                   ON CONFLICT(file_path, reason_code, fingerprint)
                   DO UPDATE SET status='resolved', resolved_at=excluded.resolved_at""",
                (file_path, code, fingerprint, _utc_now()),
            )

    def catalog(self, preferred_language: str | None = None, *, rater_id: int | None = ADMIN_USER_ID) -> dict[str, Any]:
        self.reconcile_acquisition_jobs()
        with self._connect() as connection:
            roots = [dict(row) for row in connection.execute("SELECT * FROM library_roots ORDER BY id")]
            family_rows = list(connection.execute("SELECT * FROM series_families ORDER BY name COLLATE NOCASE"))
            family_membership_rows = list(connection.execute(
                """SELECT series_family_memberships.*, series_families.name AS family_name
                   FROM series_family_memberships
                   JOIN series_families ON series_families.id=series_family_memberships.series_family_id
                   ORDER BY series_family_memberships.position, series_family_memberships.series_run_id"""
            ))
            family_membership_map = {
                int(row["series_run_id"]): {
                    "id": str(row["series_family_id"]), "name": row["family_name"],
                    "position": int(row["position"]),
                }
                for row in family_membership_rows
            }
            family_members_by_family: dict[int, list[int]] = {}
            for row in family_membership_rows:
                family_members_by_family.setdefault(int(row["series_family_id"]), []).append(
                    int(row["series_run_id"])
                )
            story_arc_rows = list(connection.execute(
                "SELECT * FROM story_arcs ORDER BY series_family_id, arc_type, position, id"
            ))
            story_arcs_by_family: dict[int, list[sqlite3.Row]] = {}
            for row in story_arc_rows:
                story_arcs_by_family.setdefault(int(row["series_family_id"]), []).append(row)
            story_arc_runs: dict[int, list[int]] = {}
            for row in connection.execute(
                """SELECT * FROM story_arc_run_memberships
                   ORDER BY story_arc_id, position, series_run_id"""
            ):
                story_arc_runs.setdefault(int(row["story_arc_id"]), []).append(
                    int(row["series_run_id"])
                )
            series_run_rows = list(connection.execute(
                """SELECT * FROM series_runs
                   WHERE EXISTS(
                       SELECT 1 FROM file_identities
                       JOIN files ON files.id=file_identities.file_id
                       WHERE file_identities.series_run_id=series_runs.id AND files.present=1
                   ) OR EXISTS(
                       SELECT 1 FROM series_provider_ids
                       WHERE series_provider_ids.series_run_id=series_runs.id
                         AND series_provider_ids.confirmed=1
                   ) OR EXISTS(
                       SELECT 1 FROM series_family_memberships
                       WHERE series_family_memberships.series_run_id=series_runs.id
                   )
                   ORDER BY canonical_title COLLATE NOCASE"""
            ))
            monitor_refresh_by_series = {
                int(row["series_run_id"]): dict(row)
                for row in connection.execute("SELECT * FROM series_monitor_refreshes")
            }
            cover_preference_by_run = {
                int(row["series_run_id"]): dict(row)
                for row in connection.execute("SELECT * FROM series_cover_preferences")
            }
            # A file-sourced pick points at a file, not a url, so that "make
            # issue 4 the face" keeps following issue 4's own chosen cover.
            resolved_cover_by_file: dict[int, str] = {}
            rows = list(connection.execute(
                """SELECT files.*,
                          file_identities.series_run_id,
                          file_identities.raw_title AS identity_raw_title,
                          file_identities.identity_kind,
                          file_identities.issue_number AS identity_issue,
                          file_identities.volume_number AS identity_volume,
                          file_identities.match_confidence AS identity_confidence,
                          file_identities.match_basis_json,
                          editions.edition_kind AS edition_kind,
                          file_metadata_overrides.fields_json AS override_fields_json,
                          file_cover_preferences.source AS cover_source,
                          file_cover_preferences.cover_url AS preferred_cover_url,
                          series_runs.canonical_title,
                          series_runs.start_year AS series_start_year,
                          series_runs.publisher AS series_publisher
                   FROM files
                   LEFT JOIN file_identities ON file_identities.file_id=files.id
                   LEFT JOIN file_edition_links ON file_edition_links.file_id=files.id
                   LEFT JOIN editions ON editions.id=file_edition_links.edition_id
                   LEFT JOIN file_metadata_overrides ON file_metadata_overrides.file_id=files.id
                   LEFT JOIN file_cover_preferences ON file_cover_preferences.file_id=files.id
                   LEFT JOIN series_runs ON series_runs.id=file_identities.series_run_id
                   WHERE files.present=1
                   ORDER BY files.filename COLLATE NOCASE"""
            ))
            alias_map: dict[int, list[dict[str, Any]]] = {}
            for alias in connection.execute(
                "SELECT series_run_id, alias, source, confirmed FROM series_aliases ORDER BY confirmed DESC, alias COLLATE NOCASE"
            ):
                alias_map.setdefault(int(alias["series_run_id"]), []).append(
                    {"name": alias["alias"], "source": alias["source"], "confirmed": bool(alias["confirmed"])}
                )
            creator_rows: dict[int, list[Any]] = {}
            for creator in connection.execute(
                """SELECT file_identities.series_run_id AS series_run_id, creators.name AS name,
                          file_creators.role AS role, COUNT(*) AS credits
                   FROM file_creators
                   JOIN file_identities ON file_identities.file_id=file_creators.file_id
                   JOIN files ON files.id=file_creators.file_id AND files.present=1
                   JOIN creators ON creators.id=file_creators.creator_id
                   GROUP BY file_identities.series_run_id, creators.id, file_creators.role
                   UNION ALL
                   SELECT series_run_creators.series_run_id, creators.name, series_run_creators.role,
                          COALESCE(series_run_creators.issue_count, 1)
                   FROM series_run_creators
                   JOIN creators ON creators.id=series_run_creators.creator_id"""
            ):
                creator_rows.setdefault(int(creator["series_run_id"]), []).append(creator)
            creators_by_run = {
                run_id: _summarize_run_creators(rows) for run_id, rows in creator_rows.items()
            }
            issue_catalog_map = {
                int(row["series_run_id"]): {
                    "status": row["status"], "provider": row["provider"],
                    "runEndStatus": row["run_end_status"],
                    "endYear": row["end_year"], "endYearProvider": row["end_year_provider"],
                    "providerSeriesId": row["provider_series_id"], "issueCount": row["issue_count"],
                    "lastSyncedAt": row["last_synced_at"], "titlePolicy": row["title_policy"],
                    "detail": row["detail"], "error": row["error"],
                }
                for row in connection.execute("SELECT * FROM issue_catalog_status")
            }
            for row in connection.execute(
                """SELECT issues.series_run_id, COUNT(*) AS total_count,
                          SUM(CASE WHEN COALESCE(issue_metadata_overrides.title, issues.title) IS NULL
                                        OR TRIM(COALESCE(issue_metadata_overrides.title, issues.title))=''
                                   THEN 1 ELSE 0 END) AS missing_title_count,
                          SUM(CASE WHEN COALESCE(issue_metadata_overrides.publication_year, issues.publication_year) IS NULL
                                   THEN 1 ELSE 0 END) AS missing_date_count
                   FROM issues
                   LEFT JOIN issue_metadata_overrides ON issue_metadata_overrides.issue_id=issues.id
                   GROUP BY issues.series_run_id"""
            ):
                series_run_id = int(row["series_run_id"])
                total_count = int(row["total_count"] or 0)
                provider_missing_title_count = int(row["missing_title_count"] or 0)
                title_policy = issue_catalog_map.get(series_run_id, {}).get("titlePolicy", "unknown")
                missing_title_count = 0 if title_policy == "numbered_only" else provider_missing_title_count
                missing_date_count = int(row["missing_date_count"] or 0)
                issue_catalog_map.setdefault(series_run_id, {"status": "unknown", "runEndStatus": "unknown"})
                issue_catalog_map[series_run_id].update({
                    "metadataComplete": missing_title_count == 0 and missing_date_count == 0,
                    "missingTitleCount": missing_title_count,
                    "providerMissingTitleCount": provider_missing_title_count,
                    "missingDateCount": missing_date_count,
                    "metadataIssueCount": total_count,
                })
            issue_anchor_counts: dict[int, dict[str, Any]] = {}
            for row in connection.execute(
                """SELECT issues.series_run_id, issue_provider_ids.provider,
                          COUNT(DISTINCT issue_provider_ids.provider_id) AS anchor_count
                   FROM issue_provider_ids
                   JOIN issues ON issues.id=issue_provider_ids.issue_id
                   GROUP BY issues.series_run_id, issue_provider_ids.provider"""
            ):
                summary = issue_anchor_counts.setdefault(
                    int(row["series_run_id"]), {"count": 0, "providers": []}
                )
                summary["count"] += int(row["anchor_count"] or 0)
                summary["providers"].append(str(row["provider"]))
            for series_run_id, summary in issue_anchor_counts.items():
                issue_catalog_map.setdefault(series_run_id, {"status": "unknown", "runEndStatus": "unknown"})
                issue_catalog_map[series_run_id]["anchorCount"] = summary["count"]
                issue_catalog_map[series_run_id]["anchorProviders"] = summary["providers"]
                issue_catalog_map[series_run_id]["syncReady"] = summary["count"] > 0
            for item in issue_catalog_map.values():
                item.setdefault("anchorCount", 0)
                item.setdefault("anchorProviders", [])
                item.setdefault("syncReady", False)
                item.setdefault("metadataComplete", False)
                item.setdefault("missingTitleCount", 0)
                item.setdefault("missingDateCount", 0)
                item.setdefault("metadataIssueCount", 0)
            # The cover inside the file that satisfies each issue. Shown so a
            # wrong download is visible at a glance rather than only to someone
            # who already knows to open the file. Deliberately unfiltered by
            # language: seeing the French cover is how the mistake is spotted.
            # The ratings are the asking reader's. The internal callers
            # (acquisition snapshots, merges) answer admin-only routes and get
            # the admin's, which is what they always showed; `rater_id=None`
            # asks for none at all.
            issue_ratings = {
                int(row["issue_id"]): int(row["rating"])
                for row in connection.execute(
                    "SELECT issue_id, rating FROM issue_ratings WHERE user_id=?", (rater_id,),
                )
            } if rater_id is not None else {}
            run_ratings = {
                int(row["series_run_id"]): int(row["rating"])
                for row in connection.execute(
                    "SELECT series_run_id, rating FROM series_run_ratings WHERE user_id=?", (rater_id,),
                )
            } if rater_id is not None else {}
            issue_file_covers: dict[int, str] = {}
            # The file behind an issue, so something can be read from it. Two
            # copies of one issue are possible -- a scan and a reprint -- and
            # the lowest file id wins, which means "whichever was catalogued
            # first". Arbitrary, but stable; there is no better signal, and a
            # picker for it would be a feature of its own.
            issue_file_ids: dict[int, str] = {}
            # When the issue's file was first catalogued: Comics' Recently
            # Added Issues shelf orders by it.
            issue_file_added: dict[int, str] = {}
            for link in connection.execute(
                """SELECT file_issue_links.issue_id AS issue_id, files.id AS file_id,
                          files.result_json, files.created_at AS file_created_at,
                          file_cover_preferences.source AS cover_source,
                          file_cover_preferences.cover_url AS preferred_cover_url
                   FROM file_issue_links
                   JOIN files ON files.id=file_issue_links.file_id
                   LEFT JOIN file_cover_preferences ON file_cover_preferences.file_id=files.id
                   WHERE files.present=1
                   ORDER BY files.id"""
            ):
                issue_id = int(link["issue_id"])
                # Claimed before the cover is resolved, and on its own key: a
                # file whose cover cannot be drawn is still a file that can be
                # read, and sharing the covers' key would have skipped it.
                issue_file_ids.setdefault(issue_id, str(link["file_id"]))
                issue_file_added.setdefault(issue_id, link["file_created_at"])
                if issue_id in issue_file_covers:
                    continue
                url = self._resolved_file_cover(
                    link["file_id"], _load_json(link["result_json"], {}),
                    link["cover_source"], link["preferred_cover_url"],
                )
                if url:
                    issue_file_covers[issue_id] = url
            issues_by_series: dict[int, list[dict[str, Any]]] = {}
            for issue in connection.execute(
                """SELECT issues.*,
                          issue_metadata_overrides.title AS override_title,
                          issue_metadata_overrides.publication_year AS override_publication_year,
                          issue_metadata_overrides.issue_id IS NOT NULL AS metadata_locked,
                          EXISTS(
                              SELECT 1 FROM file_issue_links
                              JOIN files ON files.id=file_issue_links.file_id
                              WHERE file_issue_links.issue_id=issues.id AND files.present=1
                          ) AS direct_owned,
                          EXISTS(
                              SELECT 1 FROM edition_coverage_claims
                              JOIN file_edition_links ON file_edition_links.edition_id=edition_coverage_claims.edition_id
                              JOIN files ON files.id=file_edition_links.file_id
                              WHERE edition_coverage_claims.issue_id=issues.id
                                AND edition_coverage_claims.resolution_status='resolved'
                                AND edition_coverage_claims.relation_kind='full_issue'
                                AND files.present=1
                                AND NOT EXISTS(
                                    SELECT 1 FROM edition_coverage_overrides
                                    WHERE edition_coverage_overrides.edition_id=edition_coverage_claims.edition_id
                                      AND edition_coverage_overrides.series_run_id=issues.series_run_id
                                      AND edition_coverage_overrides.issue_number=issues.issue_number
                                      AND edition_coverage_overrides.action='remove'
                                )
                          ) OR EXISTS(
                              SELECT 1 FROM edition_coverage_overrides
                              JOIN file_edition_links ON file_edition_links.edition_id=edition_coverage_overrides.edition_id
                              JOIN files ON files.id=file_edition_links.file_id
                              WHERE edition_coverage_overrides.series_run_id=issues.series_run_id
                                AND edition_coverage_overrides.issue_number=issues.issue_number
                                AND edition_coverage_overrides.action='add'
                                AND files.present=1
                          ) AS collection_owned
                   FROM issues
                   LEFT JOIN issue_metadata_overrides ON issue_metadata_overrides.issue_id=issues.id"""
            ):
                direct = bool(issue["direct_owned"])
                collected = bool(issue["collection_owned"])
                display_year = (
                    issue["override_publication_year"]
                    if issue["override_publication_year"] is not None
                    else issue["publication_year"]
                )
                display_date = (
                    issue["publication_date"]
                    if issue["override_publication_year"] is None else None
                )
                release_state = _issue_release_state(display_date, display_year)
                ownership = (
                    "both" if direct and collected else "direct" if direct
                    else "collection" if collected else "unowned"
                )
                issues_by_series.setdefault(int(issue["series_run_id"]), []).append(
                    {
                        "id": str(issue["id"]), "number": issue["issue_number"],
                        "title": issue["override_title"] if issue["override_title"] is not None else issue["title"],
                        "publicationYear": display_year,
                        "publicationDate": display_date,
                        "providerTitle": issue["title"],
                        "providerPublicationYear": issue["publication_year"],
                        "providerPublicationDate": issue["publication_date"],
                        "cover": issue["cover"],
                        "fileCover": issue_file_covers.get(int(issue["id"])),
                        "fileId": issue_file_ids.get(int(issue["id"])),
                        "addedAt": issue_file_added.get(int(issue["id"])),
                        "yourRating": issue_ratings.get(int(issue["id"])),
                        "metadataLocked": bool(issue["metadata_locked"]),
                        "directOwned": direct, "collectionOwned": collected,
                        "ownership": ownership, "releaseState": release_state,
                        "acquisitionState": (
                            "owned" if ownership != "unowned" else
                            "wanted" if release_state == "released" else
                            "awaiting_release" if release_state == "upcoming" else
                            "metadata_pending"
                        ),
                    }
                )
            for issue_list in issues_by_series.values():
                issue_list.sort(key=lambda item: _natural_issue_key(item["number"]))

            editions_by_series: dict[int, list[dict[str, Any]]] = {}
            edition_rows = list(connection.execute(
                """SELECT DISTINCT editions.*
                   FROM editions
                   JOIN file_edition_links ON file_edition_links.edition_id=editions.id
                   JOIN files ON files.id=file_edition_links.file_id
                   WHERE files.present=1
                   ORDER BY editions.publication_year, editions.title COLLATE NOCASE"""
            ))
            for edition in edition_rows:
                contents_payload = self._collection_contents_payload(connection, int(edition["id"]))
                claims = [
                    {
                        "seriesLabel": claim["seriesLabel"], "issueNumber": claim["issueNumber"],
                        "source": claim["source"], "confidence": claim["confidence"],
                        "resolved": claim["resolved"], "evidence": claim["evidence"],
                        "relationKind": claim["relationKind"], "manual": claim["manual"],
                    }
                    for claim in contents_payload["items"] if claim["included"]
                ]
                edition_files = [
                    {"id": str(file_row["id"]), "filename": file_row["filename"]}
                    for file_row in connection.execute(
                        """SELECT files.id, files.filename FROM files
                           JOIN file_edition_links ON file_edition_links.file_id=files.id
                           WHERE file_edition_links.edition_id=? AND files.present=1""",
                        (edition["id"],),
                    )
                ]
                editions_by_series.setdefault(int(edition["series_run_id"]), []).append(
                    {
                        "id": str(edition["id"]), "title": edition["title"], "subtitle": edition["subtitle"],
                        "volume": edition["volume_number"], "publicationYear": edition["publication_year"],
                        "publisher": edition["publisher"], "format": edition["format"],
                        "editionKind": edition["edition_kind"],
                        "isbns": _load_json(edition["isbns_json"], []), "cover": edition["cover"],
                        "source": edition["source"], "verificationStatus": edition["verification_status"],
                        "coverageStatus": edition["coverage_status"], "coverage": claims,
                        "coverageGroups": _coverage_groups(claims),
                        "fileIds": [file["id"] for file in edition_files],
                        "files": [file["filename"] for file in edition_files],
                        "coverageOverrideCount": sum(
                            1 for claim in contents_payload["items"]
                            if claim["manual"] or not claim["included"]
                        ),
                    }
                )
            editions_by_series = {
                series_run_id: _group_logical_volumes(editions)
                for series_run_id, editions in editions_by_series.items()
            }
            resolved = {
                (row["file_path"], row["reason_code"], row["fingerprint"])
                for row in connection.execute("SELECT file_path, reason_code, fingerprint FROM review_resolutions WHERE status='resolved'")
            }
            request_rows = [
                dict(row) for row in connection.execute(
                    """SELECT * FROM acquisition_requests
                       WHERE NOT EXISTS(
                           SELECT 1 FROM file_replacement_requests
                           WHERE file_replacement_requests.acquisition_request_id=acquisition_requests.id
                       )
                       ORDER BY created_at DESC, id DESC"""
                )
            ]
            acquisition_job_rows = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM acquisition_jobs ORDER BY created_at, id"
                )
            ]
            acquisition_download_rows = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM acquisition_downloads ORDER BY created_at, id"
                )
            ]
            replacement_rows = [
                dict(row) for row in connection.execute(
                    """SELECT file_replacement_requests.*, files.filename, files.path,
                              files.result_json, file_identities.identity_kind,
                              file_identities.issue_number, file_identities.series_run_id,
                              editions.title AS edition_title,
                              editions.subtitle AS edition_subtitle,
                              editions.volume_number,
                              series_runs.canonical_title AS series_title,
                              series_runs.start_year AS series_year
                       FROM file_replacement_requests
                       JOIN files ON files.id=file_replacement_requests.file_id
                       LEFT JOIN file_identities ON file_identities.file_id=files.id
                       LEFT JOIN file_edition_links ON file_edition_links.file_id=files.id
                       LEFT JOIN editions ON editions.id=file_edition_links.edition_id
                       LEFT JOIN series_runs ON series_runs.id=file_identities.series_run_id
                       ORDER BY file_replacement_requests.created_at DESC,
                                file_replacement_requests.id DESC"""
                )
            ]
            request_issue_ids: dict[int, list[int]] = {}
            for row in connection.execute(
                """SELECT request_id, issue_id FROM acquisition_request_issues
                   ORDER BY request_id, issue_id"""
            ):
                request_issue_ids.setdefault(int(row["request_id"]), []).append(int(row["issue_id"]))
            latest_scan = connection.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()

        series_groups: dict[str, dict[str, Any]] = {
            str(run["id"]): {
                "id": str(run["id"]), "canonicalKey": run["canonical_key"],
                "title": run["canonical_title"], "year": str(run["start_year"] or "Unknown"),
                "publisher": run["publisher"] or "Publisher unknown",
                "acquisitionPreference": run["acquisition_preference"],
                "monitoringStatus": run["monitoring_status"],
                # The admin's mark outranks what was found, and is said to be theirs.
                "ageRating": run["age_rating_override"] or run["age_rating"],
                "ageRatingSource": "admin" if run["age_rating_override"] else run["age_rating_source"],
                # What was found, said even under the admin's mark.
                "ageRatingNote": run["age_rating_note"],
                "ageRatingFound": run["age_rating"], "ageRatingFoundSource": run["age_rating_source"],
                "ageRatingOverride": run["age_rating_override"],
                # "medium", not "format": the series payload already uses
                # "format" for the shape of its files ("Single issues").
                "medium": run["format"] or "comic",
                # Null unless someone said otherwise; the reader falls back to
                # the medium, and an English manga edition is why it can.
                "readingDirection": run["reading_direction"],
                "yourRating": run_ratings.get(int(run["id"])),
                "monitorRefresh": monitor_refresh_by_series.get(int(run["id"])),
                "issueNumbers": set(), "files": [], "covers": [], "hasProblem": False,
                "updatedAt": run["updated_at"], "addedAt": None, "firstAddedAt": None,
                "aliases": alias_map.get(int(run["id"]), []),
                "creators": creators_by_run.get(int(run["id"]), []),
                "identityConfidences": [], "issues": issues_by_series.get(int(run["id"]), []),
                "editions": editions_by_series.get(int(run["id"]), []), "fileDetails": [],
                "issueCatalog": issue_catalog_map.get(
                    int(run["id"]), {"status": "unknown", "anchorCount": 0, "syncReady": False}
                ),
            }
            for run in series_run_rows
        }
        inbox: list[dict[str, Any]] = []
        file_summaries: list[dict[str, Any]] = []
        replacement_by_file_id = {
            int(item["file_id"]): item for item in replacement_rows
            if item["status"] not in {"fulfilled", "cancelled"}
        }
        for row in rows:
            parsed = _load_json(row["parsed_json"], {})
            result = _load_json(row["result_json"], {})
            identity = result.get("lookup_identity") or parsed
            # A lookup the file's confirmed identity or ComicInfo contradicts is
            # neither shown as its match nor held up against it as a conflict.
            recommendation = _trusted_recommendation(result, _load_json(row["override_fields_json"], {}))
            overruled = bool(result.get("recommendation")) and not recommendation
            embedded = result.get("embedded_metadata") or {}
            health = result.get("file_health") or {}
            title = row["canonical_title"] or recommendation.get("title") or identity.get("title") or parsed.get("title") or row["filename"]
            year = row["series_start_year"] or recommendation.get("publication_year") or identity.get("year")
            publisher = row["series_publisher"] or recommendation.get("publisher") or embedded.get("publisher") or "Publisher unknown"
            issue = row["identity_issue"] or recommendation.get("issue") or identity.get("issue")
            cover_info = result.get("file_cover") or {}
            provider_covers = self._candidate_cover_urls(result)
            cover = self._resolved_file_cover(
                row["id"], result, row["cover_source"], row["preferred_cover_url"],
            )
            if cover:
                resolved_cover_by_file[int(row["id"])] = cover
            key = str(row["series_run_id"] or _normalized(title))
            if not key:
                key = hashlib.sha1(row["path"].encode()).hexdigest()
            series_run_id = int(row["series_run_id"]) if row["series_run_id"] else None
            group = series_groups.setdefault(
                key,
                {
                    "id": str(series_run_id) if series_run_id else _slug(title),
                    "canonicalKey": _normalized(title),
                    "title": title, "year": str(year or "Unknown"),
                    "publisher": publisher, "issueNumbers": set(), "files": [], "covers": [],
                    "acquisitionPreference": "either", "monitoringStatus": "cataloged",
                    "monitorRefresh": monitor_refresh_by_series.get(series_run_id) if series_run_id else None,
                    "hasProblem": False, "updatedAt": row["updated_at"],
                    "addedAt": row["created_at"], "firstAddedAt": row["created_at"],
                    "aliases": alias_map.get(series_run_id, []) if series_run_id else [],
                    "identityConfidences": [],
                    "issues": issues_by_series.get(series_run_id, []) if series_run_id else [],
                    "editions": editions_by_series.get(series_run_id, []) if series_run_id else [],
                    "fileDetails": [],
                    "issueCatalog": issue_catalog_map.get(
                        series_run_id, {"status": "unknown", "anchorCount": 0, "syncReady": False}
                    ) if series_run_id else {"status": "unknown", "anchorCount": 0, "syncReady": False},
                },
            )
            if issue:
                group["issueNumbers"].add(str(issue))
            group["files"].append(row["path"])
            # A French edition filed first would otherwise become the face of
            # an English run. Its own cover is dropped; the provider's is not.
            wrong_language = self._states_another_language(
                row["filename"], embedded, preferred_language
            )
            file_covers = {cover_info.get("url")} if wrong_language else set()
            if cover and cover not in file_covers:
                group["covers"].append(cover)
            for candidate_cover in [cover_info.get("url"), *provider_covers]:
                if candidate_cover and candidate_cover not in file_covers:
                    group["covers"].append(candidate_cover)
            file_review = self._review_items(
                row, result, parsed, recommendation, embedded, resolved, preferred_language
            )
            active_replacement = replacement_by_file_id.get(int(row["id"]))
            if active_replacement:
                for review in file_review:
                    review["replacementRequestId"] = str(active_replacement["id"])
                    review["replacementStatus"] = active_replacement["status"]
            inbox.extend(file_review)
            metadata_deferred = bool((result.get("source_status") or {}).get("external_metadata"))
            group["hasProblem"] = (
                group["hasProblem"] or health.get("status") == "error"
                # Identified by its own metadata is not unmatched.
                or (not recommendation and not metadata_deferred and not overruled) or bool(file_review)
            )
            if row["identity_confidence"] is not None:
                group["identityConfidences"].append(int(row["identity_confidence"]))
            if row["updated_at"] > group["updatedAt"]:
                group["updatedAt"] = row["updated_at"]
            # When the newest comic in this run was first catalogued. "Recently
            # added" means new comics arrived, so a run takes the newest of its
            # files rather than the day the run itself appeared.
            if group["addedAt"] is None or row["created_at"] > group["addedAt"]:
                group["addedAt"] = row["created_at"]
            # And when its first comic was: Recently Added Runs means runs new
            # to the library, not runs that gained an issue this week.
            if group.get("firstAddedAt") is None or row["created_at"] < group["firstAddedAt"]:
                group["firstAddedAt"] = row["created_at"]
            if publisher != "Publisher unknown":
                group["publisher"] = publisher
            if year:
                group["year"] = str(year)

            file_summaries.append(
                {
                    "id": str(row["id"]), "path": row["path"], "filename": row["filename"], "extension": row["extension"],
                    "sizeBytes": row["size_bytes"], "fingerprint": row["fingerprint"],
                    "title": title, "issue": str(issue) if issue else None,
                    "health": health, "matched": bool(recommendation), "cover": cover,
                    "coverPreference": row["cover_source"] or "auto",
                    "canonicalSeriesId": str(series_run_id) if series_run_id else None,
                    "rawTitle": row["identity_raw_title"],
                    "identityKind": row["identity_kind"],
                    "editionKind": row["edition_kind"],
                    "metadataLocked": bool(row["override_fields_json"]),
                    "cover": cover, "coverPreference": row["cover_source"] or "auto",
                    "identityConfidence": row["identity_confidence"],
                    "identityBasis": _load_json(row["match_basis_json"], []),
                }
            )
            group["fileDetails"].append(
                {
                    "id": str(row["id"]), "path": row["path"], "filename": row["filename"], "extension": row["extension"],
                    "sizeBytes": row["size_bytes"], "health": health,
                    # Which issue this file is, so a Read button can say "#7"
                    # rather than a scene-release filename. Null on a volume.
                    "issueNumber": row["identity_issue"],
                    "identityKind": row["identity_kind"], "rawTitle": row["identity_raw_title"],
                    "editionKind": row["edition_kind"],
                    "metadataLocked": bool(row["override_fields_json"]),
                    "identityConfidence": row["identity_confidence"],
                }
            )

        series: list[dict[str, Any]] = []
        for group in series_groups.values():
            catalog_known = group["issueCatalog"].get("status") in {"complete", "complete_to_date"}
            issue_owned = sum(1 for issue in group["issues"] if issue["ownership"] != "unowned")
            owned = issue_owned if catalog_known else len(group["issueNumbers"]) if group["issueNumbers"] else len(group["files"])
            total = len(group["issues"]) if catalog_known else owned
            unowned = max(0, total - owned) if catalog_known else None
            released_missing = sum(
                1 for issue in group["issues"]
                if issue["ownership"] == "unowned" and issue["releaseState"] == "released"
            )
            upcoming = sum(
                1 for issue in group["issues"]
                if issue["ownership"] == "unowned" and issue["releaseState"] == "upcoming"
            )
            unknown_release = sum(
                1 for issue in group["issues"]
                if issue["ownership"] == "unowned" and issue["releaseState"] == "unknown"
            )
            direct_issue_files = sum(1 for item in group["fileDetails"] if item["identityKind"] == "issue")
            edition_counts: dict[str, int] = {}
            for edition in group["editions"]:
                kind = edition.get("editionKind") or "edition"
                edition_counts[kind] = edition_counts.get(kind, 0) + 1
            kind_labels = {
                "omnibus": "Omnibus", "compendium": "Compendium",
                "deluxe_edition": "Deluxe volume", "graphic_novel": "Graphic novel",
                "hardcover": "Hardcover", "collected_volume": "Collected volume",
                "collection": "Collected volume", "edition": "Volume",
            }
            format_parts = ["Single issues"] if direct_issue_files else []
            format_parts.extend(kind_labels.get(kind, kind.replace("_", " ").title()) for kind in edition_counts)
            display_format = " · ".join(format_parts) or "Unclassified"
            if group["issues"] or group["issueNumbers"]:
                run = f"{group['year']} · {total} known issue{'s' if total != 1 else ''}" if catalog_known else f"{group['year']} · {owned} issue{'s' if owned != 1 else ''} cataloged"
                unknown_ownership = f"{owned} issue{'s' if owned != 1 else ''} owned · complete-run progress unknown"
            else:
                run = f"{group['year']} · {len(group['files'])} volume{'s' if len(group['files']) != 1 else ''} cataloged"
                unknown_ownership = f"{owned} volume{'s' if owned != 1 else ''} owned · complete-series progress unknown"
            date, clock = _display_time(group["updatedAt"])
            # The pick leads; everything derived stays behind it. CoverArt walks
            # this list on error, so an uploaded cover that has gone missing
            # falls back to real art instead of a placeholder.
            preference = cover_preference_by_run.get(_run_id(group["id"]) or -1)
            run_cover_preference = (preference or {}).get("source") or "auto"
            chosen = None
            if preference and preference["source"] == "file":
                chosen = resolved_cover_by_file.get(preference["file_id"])
            elif preference and preference["source"] in {"provider", "upload"}:
                chosen = preference["cover_url"]
            # group["covers"] is gathered from the files the library owns, so a
            # run that has just been followed and has nothing downloaded yet had
            # no face at all -- the Pull List drew a placeholder next to "0 of 34
            # owned". Its issues already carry the provider's art; the run's
            # natural cover is its first issue's, so fall back to that.
            issue_covers = [
                issue["cover"] for issue in group["issues"] if issue.get("cover")
            ]
            run_covers = list(dict.fromkeys(
                ([chosen] if chosen else []) + group["covers"] + issue_covers
            ))
            status = "warning" if group["hasProblem"] else "unknown" if not catalog_known else "partial" if unowned else "complete"
            ownership = (
                "Needs attention" if status == "warning" else
                "All issues owned" if catalog_known and unowned == 0 else
                f"{unowned} issue{'s' if unowned != 1 else ''} missing" if catalog_known else
                unknown_ownership
            )
            series.append(
                {
                    "id": group["id"], "title": group["title"], "year": group["year"],
                    "publisher": group["publisher"], "run": run,
                    "acquisitionPreference": group["acquisitionPreference"],
                    "monitoringStatus": group["monitoringStatus"],
                    **{key: group.get(key) for key in (
                        "ageRating", "ageRatingSource", "ageRatingNote", "ageRatingFound",
                        "ageRatingFoundSource", "ageRatingOverride")},
                    "medium": group.get("medium") or "comic",
                    "readingDirection": group.get("readingDirection"),
                    "yourRating": group.get("yourRating"),
                    "issueRating": _issue_rating_summary(group.get("issues") or []),
                    "monitorRefresh": {
                        "status": group["monitorRefresh"]["status"],
                        "lastCheckedAt": group["monitorRefresh"]["last_checked_at"],
                        "nextCheckAt": group["monitorRefresh"]["next_check_at"],
                        "provider": group["monitorRefresh"]["last_provider"],
                        "error": group["monitorRefresh"]["last_error"],
                    } if group.get("monitorRefresh") else None,
                    "tags": [display_format],
                    "owned": owned, "total": total, "catalogKnown": catalog_known,
                    "unowned": unowned, "missing": None,
                    "ownership": ownership,
                    "format": display_format, "updated": date, "time": clock,
                    "addedAt": group["addedAt"], "firstAddedAt": group.get("firstAddedAt"),
                    "cover": run_covers[0] if run_covers else None,
                    "coverCandidates": run_covers,
                    "coverPreference": run_cover_preference,
                    "status": status, "files": group["files"],
                    "aliases": group["aliases"],
                    "creators": group.get("creators") or [],
                    "identityConfidence": min(group["identityConfidences"]) if group["identityConfidences"] else None,
                    "issues": group["issues"], "editions": group["editions"],
                    "releaseSummary": {
                        "releasedMissing": released_missing, "upcoming": upcoming,
                        "unknown": unknown_release, "owned": issue_owned,
                    },
                    "inventory": {
                        "directIssueFiles": direct_issue_files,
                        "editionCount": len(group["editions"]),
                        "editionTypes": edition_counts,
                    },
                    "fileDetails": group["fileDetails"],
                    "issueCatalog": group["issueCatalog"],
                    # Read from the evidence, not from the coverage claim.
                    # `status` also decides whether the issue list is trusted
                    # enough to count ownership against, and a run whose end
                    # year nobody has recorded is not the same as one a
                    # provider positively says is still publishing.
                    "publicationStatus": (
                        "completed" if (group["issueCatalog"] or {}).get("runEndStatus") == "ended" else
                        "ongoing" if (group["issueCatalog"] or {}).get("runEndStatus") == "ongoing" else
                        "unknown"
                    ),
                    "family": family_membership_map.get(_run_id(group["id"])),
                }
            )
        series.sort(key=lambda item: item["title"].casefold())
        series_by_id = {
            run_id: item for item in series
            if (run_id := _run_id(item["id"])) is not None
        }
        families = []
        for family_row in family_rows:
            family_id = int(family_row["id"])
            runs = [
                series_by_id[run_id]
                for run_id in family_members_by_family.get(family_id, [])
                if run_id in series_by_id
            ]
            if not runs:
                continue
            catalog_known = all(run["catalogKnown"] for run in runs)
            owned = sum(run["owned"] for run in runs)
            total = sum(run["total"] for run in runs)
            unowned = sum(run["unowned"] or 0 for run in runs) if catalog_known else None
            release_summary = {
                key: sum((run.get("releaseSummary") or {}).get(key, 0) for run in runs)
                for key in ("releasedMissing", "upcoming", "unknown", "owned")
            }
            if any(run["status"] == "warning" for run in runs):
                status = "warning"
            elif not catalog_known:
                status = "unknown"
            elif unowned:
                status = "partial"
            else:
                status = "complete"
            ownership = (
                "One or more runs need metadata review" if status == "warning" else
                "All issues owned across every run" if catalog_known and unowned == 0 else
                f"{unowned} catalog issues not owned across {len(runs)} runs" if catalog_known else
                f"{owned} owned across {len(runs)} runs · some run totals unknown"
            )
            years = sorted({int(run["year"]) for run in runs if str(run.get("year") or "").isdigit()})
            year_label = str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}" if years else "Year unknown"
            publishers = list(dict.fromkeys(run["publisher"] for run in runs if run.get("publisher")))
            covers = list(dict.fromkeys(
                cover for run in runs for cover in (run.get("coverCandidates") or []) if cover
            ))
            story_arcs = []
            for arc_row in story_arcs_by_family.get(family_id, []):
                arc_runs = [
                    series_by_id[run_id]
                    for run_id in story_arc_runs.get(int(arc_row["id"]), [])
                    if run_id in series_by_id
                ]
                arc_issues = [issue for run in arc_runs for issue in (run.get("issues") or [])]
                owned_issue_count = sum(
                    1 for issue in arc_issues if issue["ownership"] != "unowned"
                )
                volume_count = sum(len(run.get("editions") or []) for run in arc_runs)
                file_count = sum(len(run.get("fileDetails") or []) for run in arc_runs)
                if arc_issues and owned_issue_count == len(arc_issues):
                    arc_status = "complete"
                elif owned_issue_count:
                    arc_status = "partial"
                elif arc_row["arc_type"] == "specials" and volume_count:
                    arc_status = "cataloged"
                else:
                    arc_status = "missing"
                story_arcs.append({
                    "id": str(arc_row["id"]), "name": arc_row["name"],
                    "type": arc_row["arc_type"], "position": int(arc_row["position"]),
                    "startYear": arc_row["start_year"], "source": arc_row["source"],
                    "confidence": arc_row["confidence"], "runs": arc_runs,
                    "runIds": [run["id"] for run in arc_runs],
                    "issueCount": len(arc_issues), "ownedIssueCount": owned_issue_count,
                    "volumeCount": volume_count, "fileCount": file_count, "status": arc_status,
                })
            story_arcs.sort(key=lambda item: (item["type"] == "specials", item["position"], item["name"].casefold()))
            families.append({
                "id": str(family_id), "name": family_row["name"],
                "description": family_row["description"], "year": year_label,
                "acquisitionPreference": family_row["acquisition_preference"],
                "includeSpecials": bool(family_row["include_specials"]),
                "monitoringStatus": family_row["monitoring_status"],
                "publisher": publishers[0] if len(publishers) == 1 else "Multiple publishers",
                "runCount": len(runs), "runIds": [run["id"] for run in runs],
                "runs": runs, "owned": owned, "total": total, "unowned": unowned,
                "releaseSummary": release_summary,
                "catalogKnown": catalog_known, "status": status, "ownership": ownership,
                "cover": covers[0] if covers else None, "coverCandidates": covers,
                "storyArcs": story_arcs,
                "structureStatus": "confirmed" if story_arcs else "unmapped",
                "mainArcCount": sum(1 for arc in story_arcs if arc["type"] == "main"),
                "specialGroupCount": sum(1 for arc in story_arcs if arc["type"] == "specials"),
            })
        families.sort(key=lambda item: item["name"].casefold())
        series_by_string_id = {item["id"]: item for item in series}
        families_by_string_id = {item["id"]: item for item in families}
        replacement_reason_labels = {
            "corrupt": "Corrupt or unreadable file",
            "no_pages": "No readable comic pages",
            "empty": "Empty archive",
            "wrong_language": "Wrong language",
            "wrong_release": "Wrong edition or release",
            "poor_quality": "Poor scan or image quality",
        }
        replacement_requests = []
        for row in replacement_rows:
            series_item = series_by_string_id.get(str(row["series_run_id"]))
            if row["identity_kind"] == "issue":
                target_type = "issue"
                target_title = f"{row['series_title'] or row['filename']} #{row['issue_number'] or '?'}"
                coverage_target = f"Replace issue #{row['issue_number'] or '?'}"
            else:
                target_type = "volume"
                edition_title = row["edition_title"] or row["series_title"] or row["filename"]
                target_title = (
                    f"{edition_title}: {row['edition_subtitle']}"
                    if row["edition_subtitle"] else edition_title
                )
                coverage_target = "Complete the run"
            requested_date, requested_time = _display_time(row["created_at"])
            result = _load_json(row["result_json"], {})
            replacement_requests.append({
                "id": str(row["id"]), "fileId": str(row["file_id"]),
                "filename": row["filename"], "path": row["path"],
                "targetType": target_type, "targetTitle": target_title,
                "seriesId": str(row["series_run_id"]) if row["series_run_id"] else None,
                "seriesTitle": row["series_title"], "seriesYear": row["series_year"],
                "reasonCode": row["reason_code"],
                "reason": replacement_reason_labels.get(row["reason_code"], "Replacement requested"),
                "acquisitionPreference": row["acquisition_preference"],
                "acquisitionRequestId": (
                    str(row["acquisition_request_id"])
                    if row["acquisition_request_id"] is not None else None
                ),
                "coverageTarget": coverage_target,
                "desiredLanguage": row["desired_language"], "status": row["status"],
                "error": row["error"], "createdAt": row["created_at"],
                "updatedAt": row["updated_at"], "requestedDate": requested_date,
                "requestedTime": requested_time,
                "quarantinePath": row["quarantine_path"],
                "cover": (series_item or {}).get("cover") or (result.get("file_cover") or {}).get("url"),
            })
        request_issue_lookup = {
            int(issue["id"]): {
                **issue, "seriesId": run["id"], "seriesTitle": run["title"],
                "seriesYear": run["year"],
            }
            for run in series for issue in run.get("issues") or []
        }
        acquisition_jobs_by_request: dict[int, list[dict[str, Any]]] = {}
        acquisition_downloads_by_job = {
            int(row["job_id"]): row for row in acquisition_download_rows
        }
        pack_delivery_counts = self.download_import_counts()
        # How many issues are still coming in each torrent: a pack's issues
        # each have a row of their own, and the Pull List offers to stop them
        # together rather than one at a time.
        job_statuses = {int(row["id"]): row["status"] for row in acquisition_job_rows}
        live_in_torrent: dict[str, int] = {}
        for row in acquisition_download_rows:
            info_hash = _torrent_identity_hash(row["sab_nzo_id"])
            if (info_hash and row["status"] in self._LIVE_DOWNLOAD_STATUSES
                    and job_statuses.get(int(row["job_id"])) not in ("fulfilled", "cancelled")):
                live_in_torrent[info_hash] = live_in_torrent.get(info_hash, 0) + 1
        for job_row in acquisition_job_rows:
            issue = request_issue_lookup.get(int(job_row["issue_id"]))
            if not issue:
                continue
            queued_date, queued_time = _display_time(job_row["created_at"])
            download = acquisition_downloads_by_job.get(int(job_row["id"]))
            acquisition_jobs_by_request.setdefault(int(job_row["request_id"]), []).append({
                "id": str(job_row["id"]), "issueId": str(job_row["issue_id"]),
                "issueNumber": issue["number"], "issueTitle": issue.get("title"),
                "seriesId": issue["seriesId"], "seriesTitle": issue["seriesTitle"],
                "seriesYear": issue["seriesYear"], "status": job_row["status"],
                "reason": job_row["queue_reason"],
                "attemptCount": int(job_row["attempt_count"]),
                "lastAttemptAt": job_row["last_attempt_at"], "error": job_row["error"],
                "downloadStatus": download["status"] if download else None,
                # A pack answers several jobs at once. This is how many issues
                # beyond this one its download delivered, so the row can say so
                # rather than leaving twelve fulfilled issues looking unrelated.
                "deliveredWithCount": (
                    pack_delivery_counts.get(int(download["id"]), 0) if download else 0
                ),
                "downloadTitle": download["release_title"] if download else None,
                # Issues coming in this one torrent, this one included, while
                # it is still coming: more than one is a pack to stop as one.
                "downloadPackCount": (
                    live_in_torrent.get(_torrent_identity_hash(download["sab_nzo_id"]) or "", 0)
                    if download and download["status"] in self._LIVE_DOWNLOAD_STATUSES else 0
                ),
                "downloadDestination": download["destination"] if download else None,
                "downloadError": download["error"] if download else None,
                # Stopped by a person rather than failed: the Pull List keeps
                # it under Wanted, not Failed, and it needs no one's attention.
                "downloadStopped": bool(download and download["status"] == "failed"
                                        and download["error"] == DOWNLOAD_STOPPED_ERROR),
                "downloadFailureStage": download["failure_stage"] if download else None,
                "downloadTakenByHand": bool(download["taken_by_hand"]) if download else False,
                "downloadSource": download["source"] if download else None,
                # When the issue actually landed, which is not the same as when
                # the job was last touched: unfollowing a run rewrites every
                # job's updated_at, so "what arrived lately" read off that would
                # date thirty old issues to the moment they stopped being
                # followed. Null for rows imported before the column existed.
                "importedAt": (download["imported_at"] if download else None),
                "createdAt": job_row["created_at"], "updatedAt": job_row["updated_at"],
                "queuedDate": queued_date, "queuedTime": queued_time,
            })
        for replacement in replacement_requests:
            acquisition_request_id = replacement.get("acquisitionRequestId")
            jobs = acquisition_jobs_by_request.get(int(acquisition_request_id), []) if acquisition_request_id else []
            replacement["jobs"] = jobs
            replacement["jobCount"] = len(jobs)
            replacement["queuedJobCount"] = sum(job["status"] == "queued" for job in jobs)
            replacement["activeJobCount"] = sum(
                job["status"] in {"queued", "searching", "grabbed"} for job in jobs
            )
            replacement["failedJobCount"] = sum(
                job["status"] == "failed" or job.get("downloadStatus") == "failed" for job in jobs
            )
            replacement["fulfilledJobCount"] = sum(job["status"] == "fulfilled" for job in jobs)
        requests = []
        for row in request_rows:
            if row["scope_type"] == "collection":
                scope_item = families_by_string_id.get(str(row["series_family_id"]))
            else:
                scope_item = series_by_string_id.get(str(row["series_run_id"]))
            if not scope_item:
                continue
            targets = [
                request_issue_lookup[issue_id]
                for issue_id in request_issue_ids.get(int(row["id"]), [])
                if issue_id in request_issue_lookup
            ]
            owned_count = sum(issue["ownership"] != "unowned" for issue in targets)
            missing_count = sum(issue["acquisitionState"] == "wanted" for issue in targets)
            upcoming_count = sum(issue["acquisitionState"] == "awaiting_release" for issue in targets)
            unknown_count = sum(issue["acquisitionState"] == "metadata_pending" for issue in targets)
            effective_status = row["status"]
            if effective_status == "open" and targets and owned_count == len(targets):
                effective_status = "fulfilled"
            request_date, request_time = _display_time(row["created_at"])
            jobs = acquisition_jobs_by_request.get(int(row["id"]), [])
            requests.append({
                "id": str(row["id"]), "scopeType": row["scope_type"],
                "scopeId": str(row["series_family_id"] or row["series_run_id"]),
                "title": scope_item.get("name") or scope_item.get("title"),
                "publisher": scope_item.get("publisher"), "year": scope_item.get("year"),
                "cover": scope_item.get("cover"), "status": effective_status,
                "storedStatus": row["status"],
                # Following is what a 'run' request does. A request for named
                # issues is open until those comics arrive and then done; it
                # was never watching the run, so it must not report that it is.
                "coverage": row["coverage"],
                "medium": scope_item.get("medium") or "comic",
                "monitoringStatus": (
                    "monitored" if row["status"] == "open" and row["coverage"] == "run"
                    else "stopped"
                ),
                "publicationStatus": scope_item.get("publicationStatus", "unknown"),
                "monitorRefresh": scope_item.get("monitorRefresh"),
                "acquisitionPreference": row["acquisition_preference"],
                "includeSpecials": bool(row["include_specials"]),
                "targetIssueCount": len(targets), "ownedIssueCount": owned_count,
                "wantedIssueCount": missing_count, "upcomingIssueCount": upcoming_count,
                "unknownReleaseIssueCount": unknown_count, "issues": targets,
                "jobs": jobs, "jobCount": len(jobs),
                "queuedJobCount": sum(job["status"] == "queued" for job in jobs),
                "activeJobCount": sum(job["status"] in {"queued", "searching", "grabbed"} for job in jobs),
                "failedJobCount": sum(job["status"] == "failed" for job in jobs),
                "fulfilledJobCount": sum(job["status"] == "fulfilled" for job in jobs),
                "createdAt": row["created_at"], "updatedAt": row["updated_at"],
                "requestedDate": request_date, "requestedTime": request_time,
            })
        inbox.sort(key=lambda item: (item["severity"] != "error", item["file"].casefold()))
        last_scan_at = next((root["last_scan_at"] for root in reversed(roots) if root["last_scan_at"]), None)
        date, clock = _display_time(last_scan_at)
        enrichment = self.metadata_enrichment_summary()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "series": series,
            "families": families,
            "runCollections": self.run_collections(),
            "requests": requests,
            "replacementRequests": replacement_requests,
            "files": file_summaries,
            "inbox": inbox,
            "enrichment": enrichment,
            "stats": {
                "series": len(series), "families": len(families), "files": len(rows), "needAttention": len(inbox),
                "damaged": sum(1 for item in inbox if item["severity"] == "error"),
                # An issue that has not been published yet is not missing from
                # the library, and counting it as such overstated every gap.
                # `unownedIssues` is what is released and not owned;
                # `unpublishedIssues` is what has yet to come out. (The
                # `upcomingIssues` further down is a different figure: the
                # upcoming issues on open requests.)
                "unownedIssues": sum(
                    (item["releaseSummary"] or {}).get("releasedMissing") or 0
                    for item in series
                ),
                "unpublishedIssues": sum(
                    (item["releaseSummary"] or {}).get("upcoming") or 0
                    for item in series
                ),
                "missingIssues": 0,
                "directIssueFiles": sum(item["inventory"]["directIssueFiles"] for item in series),
                "editions": sum(item["inventory"]["editionCount"] for item in series),
                "omnibuses": sum(item["inventory"]["editionTypes"].get("omnibus", 0) for item in series),
                "collectedVolumes": sum(item["inventory"]["editionTypes"].get("collected_volume", 0) for item in series),
                "openRequests": (
                    sum(item["status"] == "open" for item in requests)
                    + sum(item["status"] not in {"fulfilled", "cancelled"} for item in replacement_requests)
                ),
                "queuedJobs": sum(item["queuedJobCount"] for item in requests if item["status"] == "open"),
                "wantedIssues": sum(item["wantedIssueCount"] for item in requests if item["status"] == "open"),
                "upcomingIssues": sum(item["upcomingIssueCount"] for item in requests if item["status"] == "open"),
                "unknownReleaseIssues": sum(
                    item["unknownReleaseIssueCount"] for item in requests if item["status"] == "open"
                ),
                "metadataPending": enrichment["active"],
                "metadataReview": enrichment["review"],
            },
            "roots": [
                {"id": root["id"], "path": root["path"], "recursive": bool(root["recursive"]), "lastScanAt": root["last_scan_at"]}
                for root in roots
            ],
            "lastScan": {"iso": last_scan_at, "date": date, "time": clock},
            "activeScan": dict(latest_scan) if latest_scan and latest_scan["status"] in {"queued", "scanning"} else None,
        }

    def _review_items(
        self,
        row: sqlite3.Row,
        result: dict[str, Any],
        parsed: dict[str, Any],
        recommendation: dict[str, Any],
        embedded: dict[str, Any],
        resolved: set[tuple[str, str, str]],
        preferred_language: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        def add(
            code: str, issue: str, detail: str, severity: str,
            comparison: dict[str, Any] | None = None,
            category: str = "metadata",
        ) -> None:
            identity = (row["path"], code, row["fingerprint"])
            if identity in resolved:
                return
            item = {
                "id": hashlib.sha1("|".join(identity).encode()).hexdigest()[:16],
                "fileId": str(row["id"]), "category": category,
                "path": row["path"], "file": row["filename"], "code": code,
                "fingerprint": row["fingerprint"], "issue": issue, "detail": detail,
                "severity": severity,
            }
            if comparison:
                item["comparison"] = comparison
            items.append(item)

        health = result.get("file_health") or {}
        if health.get("status") == "error":
            code = health.get("code") or "file_health"
            add(
                code, _health_label(code),
                health.get("message") or "The file failed a structural check.",
                "error", category="file",
            )
        metadata_deferred = bool((result.get("source_status") or {}).get("external_metadata"))
        # A lookup set aside because the file's own identity contradicts it is
        # not a missing match: the file is identified, by itself.
        overruled = bool(result.get("recommendation")) and not recommendation
        if not recommendation and health.get("status") != "error" and not metadata_deferred and not overruled:
            add(
                "no_match", "No confident metadata match",
                "The filename and available sources did not produce a recommendation. Review the parsed identity before accepting a match.",
                "warning",
            )
        # A comic in a language nobody asked for looks correct in every listing:
        # right series, right issue number, right place on disk. The only way to
        # find the French edition filed as Saga #2 was to open it.
        wanted = str(preferred_language or "").strip().casefold()
        if wanted:
            stated = (
                normalize_language(embedded.get("language"))
                or detect_language(row["filename"])
            )
            if stated and stated != wanted:
                add(
                    "wrong_language",
                    f"Looks like a {language_name(stated)} edition",
                    f"This file says it is {language_name(stated)}, and you asked for "
                    f"{language_name(wanted)}. Replace it with \"Wrong edition or "
                    "release\" if it is not the comic you wanted.",
                    "warning",
                    comparison={
                        "field": "Language",
                        "catalog": language_name(wanted),
                        "file": language_name(stated),
                    },
                    # The record is right and the file is wrong, so this belongs
                    # with the file problems: the fix is Replace, not Edit.
                    category="file",
                )
        rec_publisher = recommendation.get("publisher")
        embedded_publisher = embedded.get("publisher")
        rec_year = recommendation.get("publication_year")
        embedded_year = embedded.get("year") or embedded.get("date")
        publisher_conflict = rec_publisher and embedded_publisher and _normalized_publisher(rec_publisher) != _normalized_publisher(embedded_publisher)
        year_conflict = rec_year and embedded_year and str(rec_year)[:4] != str(embedded_year)[:4]
        if recommendation and (publisher_conflict or year_conflict):
            conflicts = []
            if publisher_conflict:
                conflicts.append(f"publisher is {rec_publisher} in the catalog but {embedded_publisher} in the file")
            if year_conflict:
                conflicts.append(f"year is {rec_year} in the catalog but {embedded_year} in the file")
            def value(item: Any) -> str | None:
                if isinstance(item, (list, tuple, set)):
                    cleaned = [str(part).strip() for part in item if str(part).strip()]
                    return ", ".join(cleaned) if cleaned else None
                text = str(item).strip() if item is not None else ""
                return text or None

            comparison_rows = []

            def compare(field: str, catalog_value: Any, file_value: Any) -> None:
                catalog_text = value(catalog_value)
                file_text = value(file_value)
                if catalog_text is None and file_text is None:
                    return
                if catalog_text is None or file_text is None:
                    status = "missing"
                else:
                    status = "match" if _normalized(catalog_text) == _normalized(file_text) else "conflict"
                comparison_rows.append({
                    "field": field, "catalog": catalog_text, "file": file_text, "status": status,
                })

            compare("Series", recommendation.get("title"), embedded.get("series") or parsed.get("title"))
            compare("Issue", recommendation.get("issue"), embedded.get("number") or parsed.get("issue"))
            compare(
                "Story / edition title",
                recommendation.get("subtitle") or next(iter(recommendation.get("named_contents") or []), None),
                embedded.get("title"),
            )
            compare("Volume", recommendation.get("volume"), embedded.get("volume") or parsed.get("volume"))
            compare("Publisher", rec_publisher, embedded_publisher)
            compare("Year", rec_year, embedded_year or parsed.get("year"))
            compare("Publication date", recommendation.get("publication_date"), embedded.get("date"))
            compare("ISBN", recommendation.get("isbns"), embedded.get("isbns") or parsed.get("isbn"))
            compare("Format", recommendation.get("format"), embedded.get("format") or parsed.get("format"))
            comparison = {
                "catalogLabel": recommendation.get("source") or "Recommended catalog match",
                "fileLabel": embedded.get("source") or "File metadata",
                "rows": comparison_rows,
                "catalogUrl": recommendation.get("url"),
            }
            conflict_label = (
                "Conflicting issue metadata"
                if recommendation.get("record_type") == "single_issue"
                else "Conflicting volume metadata"
            )
            add(
                "metadata_conflict", conflict_label,
                "; ".join(conflicts).capitalize() + ".", "warning", comparison,
            )
        return items

    # ---- provider response cache -----------------------------------------
    #
    # Provider responses used to live in a single JSON file that was rewritten
    # in full on every miss, so the write cost grew with the cache. It grows
    # faster now that misses are cached too, so each entry is its own row and a
    # write touches only that row.
    #
    # A row with a NULL payload is a remembered miss: the provider answered
    # definitively that it has nothing, and `status` records what it said.

    def provider_cache_get(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT saved_at, status, payload FROM provider_cache WHERE key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        entry: dict[str, Any] = {
            "saved_at": float(row["saved_at"]),
            "status": int(row["status"]),
        }
        if row["payload"] is not None:
            try:
                entry["data"] = json.loads(row["payload"])
            except ValueError:
                # A row we cannot decode is worth less than a fresh request.
                return None
        return entry

    def provider_cache_put(
        self,
        key: str,
        provider: str,
        *,
        saved_at: float,
        status: int = 200,
        data: Any = None,
    ) -> None:
        payload = None if data is None else json.dumps(data, ensure_ascii=False)
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO provider_cache(key, provider, saved_at, status, payload)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       provider=excluded.provider,
                       saved_at=excluded.saved_at,
                       status=excluded.status,
                       payload=excluded.payload""",
                (str(key), str(provider), float(saved_at), int(status), payload),
            )

    def provider_cache_load(self, cutoff: float) -> dict[str, dict[str, Any]]:
        """Warm the in-process cache. Entries older than `cutoff` stay behind."""
        warmed: dict[str, dict[str, Any]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, saved_at, status, payload FROM provider_cache WHERE saved_at >= ?",
                (float(cutoff),),
            )
            for row in rows:
                entry: dict[str, Any] = {
                    "saved_at": float(row["saved_at"]),
                    "status": int(row["status"]),
                }
                if row["payload"] is not None:
                    try:
                        entry["data"] = json.loads(row["payload"])
                    except ValueError:
                        continue
                warmed[str(row["key"])] = entry
        return warmed

    def provider_cache_prune(self, cutoff: float) -> int:
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM provider_cache WHERE saved_at < ?", (float(cutoff),)
            )
            return int(cursor.rowcount or 0)

    def provider_cache_import(self, entries: Iterable[tuple[str, str, float, int, Any]]) -> int:
        """Import the retired JSON cache in one transaction rather than per row.

        Existing rows win: anything already in SQLite was written by the current
        code path and is at least as trustworthy as the file being retired.
        """
        rows = [
            (
                str(key),
                str(provider),
                float(saved_at),
                int(status),
                None if data is None else json.dumps(data, ensure_ascii=False),
            )
            for key, provider, saved_at, status, data in entries
        ]
        if not rows:
            return 0
        with self._write_lock, self._connect() as connection:
            cursor = connection.executemany(
                """INSERT OR IGNORE INTO provider_cache(
                       key, provider, saved_at, status, payload
                   ) VALUES(?, ?, ?, ?, ?)""",
                rows,
            )
            return int(cursor.rowcount or 0)
