"""Persistent catalog storage and incremental library scans for Comicarr V1."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import difflib
import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 22
LOCAL_ANALYSIS_VERSION = 2


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


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold().replace("&", " and "))


def _normalized_publisher(value: Any) -> str:
    words = re.findall(r"[a-z0-9]+", str(value or "").casefold().replace("&", " and "))
    corporate_suffixes = {"comic", "comics", "inc", "incorporated", "llc", "ltd", "publishing", "publisher", "press"}
    meaningful = [word for word in words if word not in corporate_suffixes]
    return "".join(meaningful or words)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return cleaned or hashlib.sha1(value.encode()).hexdigest()[:12]


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


def _display_title_rank(value: str) -> tuple[int, int]:
    """Prefer concise publisher styling, including '&' over the word 'and'."""
    return (1 if "&" in value else 0, -len(value))


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


def _coverage_groups(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, bool], list[dict[str, Any]]] = {}
    for claim in claims:
        key = (claim["seriesLabel"], claim["source"], claim["confidence"], claim["resolved"])
        grouped.setdefault(key, []).append(claim)
    summaries = []
    for (series_label, source, confidence, resolved), members in grouped.items():
        numbers = sorted({member["issueNumber"] for member in members}, key=_natural_issue_key)
        issue_label = numbers[0] if len(numbers) == 1 else f"{numbers[0]}–{numbers[-1]}"
        summaries.append(
            {
                "seriesLabel": series_label, "issues": numbers, "issueLabel": issue_label,
                "source": source, "confidence": confidence, "resolved": resolved,
                "evidence": next((member["evidence"] for member in members if member.get("evidence")), None),
            }
        )
    return summaries


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


class CatalogStore:
    """SQLite-backed catalog with one connection per operation/thread."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._migrate()
        self._reconcile_all_identities()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
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
                CREATE TABLE IF NOT EXISTS edition_coverage_claims (
                    id INTEGER PRIMARY KEY,
                    edition_id INTEGER NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
                    issue_id INTEGER REFERENCES issues(id) ON DELETE SET NULL,
                    series_label TEXT NOT NULL,
                    issue_number TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    resolution_status TEXT NOT NULL,
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
                    detail TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS acquisition_requests (
                    id INTEGER PRIMARY KEY,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('series', 'collection')),
                    series_run_id INTEGER REFERENCES series_runs(id) ON DELETE CASCADE,
                    series_family_id INTEGER REFERENCES series_families(id) ON DELETE CASCADE,
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
                    release_title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'downloading', 'completed', 'importing', 'imported', 'failed')),
                    sab_storage TEXT,
                    local_source TEXT,
                    destination TEXT,
                    source_size INTEGER,
                    source_sha256 TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    imported_at TEXT
                );
                CREATE INDEX IF NOT EXISTS acquisition_downloads_status
                    ON acquisition_downloads(status, updated_at);
                CREATE TABLE IF NOT EXISTS file_replacement_requests (
                    id INTEGER PRIMARY KEY,
                    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    reason_code TEXT NOT NULL,
                    desired_language TEXT,
                    acquisition_preference TEXT NOT NULL DEFAULT 'either',
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
            replacement_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(file_replacement_requests)")
            }
            if "acquisition_preference" not in replacement_columns:
                connection.execute(
                    "ALTER TABLE file_replacement_requests ADD COLUMN acquisition_preference TEXT NOT NULL DEFAULT 'either'"
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
            row = connection.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] > SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported catalog schema version {row['version']}")
            elif row["version"] < SCHEMA_VERSION:
                connection.execute("UPDATE schema_info SET version=?", (SCHEMA_VERSION,))

    def _reconcile_all_identities(self) -> None:
        with self._connect() as connection:
            rows = list(connection.execute("SELECT id, parsed_json, result_json FROM files"))
        for row in rows:
            self._reconcile_file_identity(
                int(row["id"]),
                _load_json(row["parsed_json"], {}),
                _load_json(row["result_json"], {}),
            )

    def _identity_claim(
        self, parsed: dict[str, Any], result: dict[str, Any], override: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        override = override or {}
        identity = result.get("lookup_identity") or parsed
        recommendation = result.get("recommendation") or {}
        embedded = result.get("embedded_metadata") or {}
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
            confidence = 25
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
        if override:
            basis.append("Manual correction")
        return {
            "raw_title": str(raw_title),
            "series_title": series_title,
            "normalized_title": _normalized(raw_title),
            "kind": "issue" if is_issue else "edition",
            "issue": str(issue) if issue is not None else None,
            "volume": override.get("volumeNumber") if "volumeNumber" in override else identity.get("volume") or parsed.get("volume"),
            "year": override.get("publicationYear") if "publicationYear" in override else recommendation.get("publication_year") or identity.get("year"),
            "publisher": override.get("publisher") if "publisher" in override else recommendation.get("publisher") or embedded.get("publisher"),
            "confidence": 100 if override else confidence,
            "basis": list(dict.fromkeys(basis)),
        }

    def _resolve_series_run(self, connection: sqlite3.Connection, claim: dict[str, Any]) -> int:
        raw_key = _normalized(claim["raw_title"])
        canonical_key = _normalized(claim["series_title"])
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
            title = series["canonical_title"]
            if _normalized(title) == canonical_key and _display_title_rank(claim["series_title"]) > _display_title_rank(title):
                title = claim["series_title"]
            years = [value for value in (series["start_year"], claim.get("year")) if isinstance(value, int) and value > 0]
            start_year = min(years) if years else series["start_year"]
            publisher = series["publisher"] or claim.get("publisher")
            connection.execute(
                "UPDATE series_runs SET canonical_title=?, start_year=?, publisher=?, updated_at=? WHERE id=?",
                (title, start_year, publisher, now, series_run_id),
            )
        else:
            cursor = connection.execute(
                """INSERT INTO series_runs(canonical_title, canonical_key, start_year, publisher, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (claim["series_title"], canonical_key, claim.get("year"), claim.get("publisher"), now, now),
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
        self, file_id: int, parsed: dict[str, Any], result: dict[str, Any], override: dict[str, Any] | None = None
    ) -> None:
        if override is None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT fields_json, match_candidate_json FROM file_metadata_overrides WHERE file_id=?", (file_id,)
                ).fetchone()
            override = _load_json(row["fields_json"], {}) if row else {}
            selected_candidate = _load_json(row["match_candidate_json"], None) if row else None
            if selected_candidate:
                result = {**result, "recommendation": selected_candidate}
        claim = self._identity_claim(parsed, result, override)
        with self._write_lock, self._connect() as connection:
            series_run_id = self._resolve_series_run(connection, claim)
            connection.execute(
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
        self._sync_file_catalog_entity(file_id, parsed, result, override)

    def _sync_file_catalog_entity(
        self, file_id: int, parsed: dict[str, Any], result: dict[str, Any], override: dict[str, Any] | None = None
    ) -> None:
        override = override or {}
        recommendation = dict(result.get("recommendation") or {})
        embedded = result.get("embedded_metadata") or {}
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
        with self._write_lock, self._connect() as connection:
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
                if recommendation.get("source") == "Grand Comics Database" and recommendation.get("source_id"):
                    provider_id = str(recommendation["source_id"])
                    connection.execute(
                        """INSERT INTO issue_provider_ids(issue_id, provider, provider_id, api_url, updated_at)
                           VALUES (?, 'gcd', ?, ?, ?)
                           ON CONFLICT(provider, provider_id) DO UPDATE SET
                               issue_id=excluded.issue_id, api_url=excluded.api_url, updated_at=excluded.updated_at""",
                        (issue_id, provider_id, f"https://www.comics.org/api/issue/{provider_id}/", now),
                    )
                connection.execute("DELETE FROM file_edition_links WHERE file_id=?", (file_id,))
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
                    edition_kind, _json(isbns), (result.get("file_cover") or {}).get("url") or recommendation.get("cover"),
                    source, str(source_id) if source_id else None, recommendation.get("verification_status"),
                    recommendation.get("coverage_status"), now, now,
                ),
            )
            edition_id = int(connection.execute(
                "SELECT id FROM editions WHERE edition_key=?", (edition_key,)
            ).fetchone()["id"])
            connection.execute("DELETE FROM file_issue_links WHERE file_id=?", (file_id,))
            connection.execute(
                """INSERT INTO file_edition_links(file_id, edition_id) VALUES (?, ?)
                   ON CONFLICT(file_id) DO UPDATE SET edition_id=excluded.edition_id""",
                (file_id, edition_id),
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
                               confidence, resolution_status, evidence, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            edition_id, issue_id, series_label, str(issue_number), claim_source,
                            confidence, "resolved" if resolved else "unresolved",
                            claim.get("source_text"), now,
                        ),
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
            "covers": {
                "selectedSource": row["cover_source"] or "auto",
                "selectedUrl": row["preferred_cover_url"],
                "options": cover_options,
            },
            "collectionContents": collection_contents,
            "seriesOptions": series_options,
        }

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
            "includedCount": sum(1 for item in contents if item["included"] and item["resolved"]),
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
        return self.get_file_workbench(file_id)

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
    ) -> dict[str, Any]:
        if status not in {"partial", "complete", "complete_to_date"}:
            raise ValueError("Unsupported issue catalog status")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            if not connection.execute("SELECT id FROM series_runs WHERE id=?", (series_run_id,)).fetchone():
                raise ValueError("Canonical series was not found")
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
                    connection.execute(
                        """INSERT INTO issue_provider_ids(issue_id, provider, provider_id, api_url, updated_at)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(provider, provider_id) DO UPDATE SET
                               issue_id=excluded.issue_id, api_url=excluded.api_url, updated_at=excluded.updated_at""",
                        (issue_id, provider, str(entry["provider_id"]), entry.get("api_url"), now),
                    )
                active_requests = connection.execute(
                    """SELECT acquisition_requests.id
                       FROM acquisition_requests
                       WHERE acquisition_requests.status='open' AND (
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
                       last_synced_at, detail, error
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                   ON CONFLICT(series_run_id) DO UPDATE SET
                       status=excluded.status, provider=excluded.provider,
                       provider_series_id=excluded.provider_series_id,
                       issue_count=excluded.issue_count, last_synced_at=excluded.last_synced_at,
                       detail=excluded.detail, error=NULL""",
                (series_run_id, status, provider, provider_series_id, len(entries), now, detail),
            )
            publication_years = [
                int(entry["publication_year"])
                for entry in entries
                if entry.get("publication_year") not in {None, ""}
            ]
            if publication_years:
                connection.execute(
                    "UPDATE series_runs SET start_year=?, updated_at=? WHERE id=?",
                    (min(publication_years), now, series_run_id),
                )
        return {
            "seriesId": str(series_run_id), "status": status,
            "issueCount": len(entries), "provider": provider,
            "titleCount": sum(bool(str(entry.get("title") or "").strip()) for entry in entries),
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
    ) -> dict[str, Any]:
        """Create a catalog-only run when a provider run is not represented by a local file."""
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
                existing = connection.execute(
                    "SELECT * FROM series_runs WHERE canonical_key=?", (canonical_key,)
                ).fetchone()
            created = existing is None
            if created:
                cursor = connection.execute(
                    """INSERT INTO series_runs(
                           canonical_title, canonical_key, start_year, publisher, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (title, canonical_key, year, publisher, now, now),
                )
                run_id = int(cursor.lastrowid)
            else:
                run_id = int(existing["id"])
                connection.execute(
                    """UPDATE series_runs SET
                           start_year=COALESCE(start_year, ?),
                           publisher=COALESCE(publisher, ?), updated_at=?
                       WHERE id=?""",
                    (year, publisher, now, run_id),
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
                                   confidence, resolution_status, evidence, created_at
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                new_edition_id, claim["issue_id"], claim["series_label"],
                                claim["issue_number"], claim["source"], claim["confidence"],
                                claim["resolution_status"], claim["evidence"], now,
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
        return {
            "id": str(series_run_id), "name": run["canonical_title"],
            "acquisitionPreference": preference, "includeSpecials": True,
            "monitoringStatus": "monitored",
        }

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
                    WHERE 1=1 {request_filter}""",
                parameters,
            ).fetchall()
            existing = {
                (int(row["request_id"]), int(row["issue_id"])): row
                for row in connection.execute(
                    f"""SELECT acquisition_jobs.* FROM acquisition_jobs
                        JOIN acquisition_requests
                          ON acquisition_requests.id=acquisition_jobs.request_id
                        WHERE 1=1 {request_filter}""",
                    parameters,
                )
            }
            for row in rows:
                key = (int(row["request_id"]), int(row["issue_id"]))
                job = existing.get(key)
                owned = bool(row["direct_owned"] or row["collection_owned"])
                release_state = _issue_release_state(row["publication_date"], row["publication_year"])
                if row["request_status"] == "cancelled":
                    desired, reason = "cancelled", "Request cancelled"
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
                if current == desired and job["queue_reason"] == reason:
                    continue
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
                          series_runs.publisher
                   FROM acquisition_jobs
                   JOIN acquisition_requests
                     ON acquisition_requests.id=acquisition_jobs.request_id
                   JOIN issues ON issues.id=acquisition_jobs.issue_id
                   JOIN series_runs ON series_runs.id=issues.series_run_id
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
        return {
            "id": str(row["id"]), "requestId": str(row["request_id"]),
            "status": row["status"], "issueId": str(row["issue_id"]),
            "issueNumber": row["issue_number"], "issueTitle": row["issue_title"],
            "publicationYear": row["publication_year"],
            "publicationDate": row["publication_date"],
            "seriesId": str(row["series_id"]), "seriesTitle": row["series_title"],
            "seriesYear": row["series_year"], "publisher": row["publisher"],
            "acquisitionPreference": row["acquisition_preference"],
            "existingDirectory": str(Path(existing_file["path"]).parent) if existing_file else None,
        }

    def record_acquisition_download(
        self, job_id: int, sab_nzo_id: str, release_title: str
    ) -> dict[str, Any]:
        """Persist the SAB identity needed to reconcile queue, history, and imports."""
        queue_id = str(sab_nzo_id or "").strip()
        title = str(release_title or "").strip()
        if not queue_id or not title:
            raise ValueError("SABnzbd returned an incomplete download identity")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            if not connection.execute("SELECT id FROM acquisition_jobs WHERE id=?", (job_id,)).fetchone():
                raise ValueError("Acquisition job was not found")
            connection.execute(
                """INSERT INTO acquisition_downloads(
                       job_id, sab_nzo_id, release_title, status, created_at, updated_at
                   ) VALUES (?, ?, ?, 'queued', ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       sab_nzo_id=excluded.sab_nzo_id,
                       release_title=excluded.release_title,
                       status='queued', error=NULL, updated_at=excluded.updated_at""",
                (job_id, queue_id, title, now, now),
            )
            row = connection.execute(
                "SELECT * FROM acquisition_downloads WHERE job_id=?", (job_id,)
            ).fetchone()
        return {
            "id": str(row["id"]), "jobId": str(row["job_id"]),
            "queueId": row["sab_nzo_id"], "releaseTitle": row["release_title"],
            "status": row["status"], "updatedAt": row["updated_at"],
        }

    def pending_acquisition_downloads(self) -> list[dict[str, Any]]:
        """Return SAB downloads that still need completion tracking or a verified import."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT acquisition_downloads.*, acquisition_jobs.status AS job_status
                   FROM acquisition_downloads
                   JOIN acquisition_jobs ON acquisition_jobs.id=acquisition_downloads.job_id
                   WHERE acquisition_downloads.status IN ('queued', 'downloading', 'completed', 'importing')
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
    ) -> dict[str, Any]:
        allowed = {"queued", "downloading", "completed", "importing", "imported", "failed"}
        desired = str(status or "").strip().casefold()
        if desired not in allowed:
            raise ValueError("Unsupported acquisition download status")
        now = _utc_now()
        imported_at = now if desired == "imported" else None
        with self._write_lock, self._connect() as connection:
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
                       error=?, imported_at=COALESCE(?, imported_at), updated_at=?
                   WHERE id=?""",
                (
                    desired, sab_storage, local_source, destination, source_size,
                    source_sha256, error, imported_at, now, download_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM acquisition_downloads WHERE id=?", (download_id,)
            ).fetchone()
        return dict(row)

    def create_acquisition_request(
        self,
        scope_type: str,
        scope_id: int,
        acquisition_preference: str | None = None,
        include_specials: bool | None = None,
    ) -> dict[str, Any]:
        """Persist a monitored target and its current canonical issue set."""
        scope = str(scope_type or "").strip().casefold()
        if scope not in {"series", "collection"}:
            raise ValueError("Request scope must be a series or collection")
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
        if not any(issue.get("ownership") == "unowned" for issue in issues):
            raise ValueError("Every known issue in this selection is already covered")

        if scope == "collection":
            self.set_collection_monitoring(scope_id, preference, include)
        else:
            self.set_series_monitoring(scope_id, preference)
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            if scope == "collection":
                existing = connection.execute(
                    """SELECT id FROM acquisition_requests
                       WHERE scope_type='collection' AND series_family_id=? AND status='open'
                       ORDER BY id DESC LIMIT 1""",
                    (scope_id,),
                ).fetchone()
            else:
                existing = connection.execute(
                    """SELECT id FROM acquisition_requests
                       WHERE scope_type='series' AND series_run_id=? AND status='open'
                       ORDER BY id DESC LIMIT 1""",
                    (scope_id,),
                ).fetchone()
            if existing:
                request_id = int(existing["id"])
                connection.execute(
                    """UPDATE acquisition_requests
                       SET acquisition_preference=?, include_specials=?, updated_at=? WHERE id=?""",
                    (preference, int(include), now, request_id),
                )
            else:
                cursor = connection.execute(
                    """INSERT INTO acquisition_requests(
                           scope_type, series_run_id, series_family_id, status,
                           acquisition_preference, include_specials, created_at, updated_at
                       ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?)""",
                    (
                        scope, scope_id if scope == "series" else None,
                        scope_id if scope == "collection" else None,
                        preference, int(include), now, now,
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
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            file_row = connection.execute(
                "SELECT id FROM files WHERE id=? AND present=1", (file_id,)
            ).fetchone()
            if not file_row:
                raise ValueError("Library file was not found")
            existing = connection.execute(
                "SELECT id FROM file_replacement_requests WHERE file_id=?", (file_id,)
            ).fetchone()
            if existing:
                request_id = int(existing["id"])
                connection.execute(
                    """UPDATE file_replacement_requests
                       SET reason_code=?, desired_language=?, acquisition_preference=?,
                           status='wanted', error=NULL, updated_at=?
                       WHERE id=?""",
                    (reason, language, preference, now, request_id),
                )
            else:
                cursor = connection.execute(
                    """INSERT INTO file_replacement_requests(
                           file_id, reason_code, desired_language, acquisition_preference,
                           status, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, 'wanted', ?, ?)""",
                    (file_id, reason, language, preference, now, now),
                )
                request_id = int(cursor.lastrowid)
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
                "SELECT id FROM file_replacement_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                raise ValueError("Replacement request was not found")
            connection.execute(
                """UPDATE file_replacement_requests
                   SET status=?, error=?, updated_at=? WHERE id=?""",
                (desired, str(error or "").strip() or None if desired == "failed" else None, now, request_id),
            )
        refreshed = self.catalog()
        return next(
            item for item in refreshed["replacementRequests"] if item["id"] == str(request_id)
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

    def merge_series(self, source_id: int, target_id: int) -> None:
        if source_id == target_id:
            raise ValueError("Source and target series must be different")
        with self._write_lock, self._connect() as connection:
            source = connection.execute("SELECT * FROM series_runs WHERE id=?", (source_id,)).fetchone()
            target = connection.execute("SELECT * FROM series_runs WHERE id=?", (target_id,)).fetchone()
            if not source or not target:
                raise ValueError("One of the canonical series was not found")
            aliases = list(connection.execute("SELECT * FROM series_aliases WHERE series_run_id=?", (source_id,)))
            connection.execute("UPDATE file_identities SET series_run_id=?, updated_at=? WHERE series_run_id=?", (target_id, _utc_now(), source_id))
            for issue in connection.execute("SELECT * FROM issues WHERE series_run_id=?", (source_id,)):
                target_issue = connection.execute(
                    "SELECT id FROM issues WHERE series_run_id=? AND issue_number=?",
                    (target_id, issue["issue_number"]),
                ).fetchone()
                if target_issue:
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
            for alias in aliases:
                connection.execute(
                    """INSERT INTO series_aliases(series_run_id, alias, normalized_alias, source, confirmed, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(normalized_alias) DO NOTHING""",
                    (target_id, alias["alias"], alias["normalized_alias"], "manual merge", 1, _utc_now()),
                )
            connection.execute("DELETE FROM series_runs WHERE id=?", (source_id,))

    def register_root(self, folder: str, recursive: bool) -> int:
        resolved = str(Path(folder).expanduser().resolve())
        if not Path(resolved).is_dir():
            raise ValueError("Library folder does not exist or is not a directory")
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO library_roots(path, recursive, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET recursive=excluded.recursive""",
                (resolved, int(recursive), now),
            )
            return int(connection.execute("SELECT id FROM library_roots WHERE path=?", (resolved,)).fetchone()["id"])

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
                       COALESCE(issue_catalog_status.status, 'unknown') AS catalog_status
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
                catalog_complete = row["catalog_status"] in {"complete", "complete_to_date"}
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
    ) -> None:
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
        self._reconcile_file_identity(file_id, asdict(parsed), result)

    def resolve_review(self, file_path: str, code: str, fingerprint: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO review_resolutions(file_path, reason_code, fingerprint, status, resolved_at)
                   VALUES (?, ?, ?, 'resolved', ?)
                   ON CONFLICT(file_path, reason_code, fingerprint)
                   DO UPDATE SET status='resolved', resolved_at=excluded.resolved_at""",
                (file_path, code, fingerprint, _utc_now()),
            )

    def catalog(self) -> dict[str, Any]:
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
            issue_catalog_map = {
                int(row["series_run_id"]): {
                    "status": row["status"], "provider": row["provider"],
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
                issue_catalog_map.setdefault(series_run_id, {"status": "unknown"})
                issue_catalog_map[series_run_id].update({
                    "metadataComplete": missing_title_count == 0 and missing_date_count == 0,
                    "missingTitleCount": missing_title_count,
                    "providerMissingTitleCount": provider_missing_title_count,
                    "missingDateCount": missing_date_count,
                    "metadataIssueCount": total_count,
                })
            issue_anchor_counts = {
                int(row["series_run_id"]): int(row["anchor_count"])
                for row in connection.execute(
                    """SELECT issues.series_run_id, COUNT(DISTINCT issue_provider_ids.provider_id) AS anchor_count
                       FROM issue_provider_ids
                       JOIN issues ON issues.id=issue_provider_ids.issue_id
                       WHERE issue_provider_ids.provider='gcd'
                       GROUP BY issues.series_run_id"""
                )
            }
            for series_run_id, anchor_count in issue_anchor_counts.items():
                issue_catalog_map.setdefault(series_run_id, {"status": "unknown"})
                issue_catalog_map[series_run_id]["anchorCount"] = anchor_count
                issue_catalog_map[series_run_id]["syncReady"] = anchor_count > 0
            for item in issue_catalog_map.values():
                item.setdefault("anchorCount", 0)
                item.setdefault("syncReady", False)
                item.setdefault("metadataComplete", False)
                item.setdefault("missingTitleCount", 0)
                item.setdefault("missingDateCount", 0)
                item.setdefault("metadataIssueCount", 0)
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
                        "manual": claim["manual"],
                    }
                    for claim in contents_payload["items"] if claim["included"]
                ]
                filenames = [
                    file_row["filename"]
                    for file_row in connection.execute(
                        """SELECT files.filename FROM files
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
                        "coverageGroups": _coverage_groups(claims), "files": filenames,
                        "coverageOverrideCount": sum(
                            1 for claim in contents_payload["items"]
                            if claim["manual"] or not claim["included"]
                        ),
                    }
                )
            resolved = {
                (row["file_path"], row["reason_code"], row["fingerprint"])
                for row in connection.execute("SELECT file_path, reason_code, fingerprint FROM review_resolutions WHERE status='resolved'")
            }
            request_rows = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM acquisition_requests ORDER BY created_at DESC, id DESC"
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
                "issueNumbers": set(), "files": [], "covers": [], "hasProblem": False,
                "updatedAt": run["updated_at"], "aliases": alias_map.get(int(run["id"]), []),
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
            recommendation = result.get("recommendation") or {}
            embedded = result.get("embedded_metadata") or {}
            health = result.get("file_health") or {}
            title = row["canonical_title"] or recommendation.get("title") or identity.get("title") or parsed.get("title") or row["filename"]
            year = row["series_start_year"] or recommendation.get("publication_year") or identity.get("year")
            publisher = row["series_publisher"] or recommendation.get("publisher") or embedded.get("publisher") or "Publisher unknown"
            issue = row["identity_issue"] or recommendation.get("issue") or identity.get("issue")
            cover_info = result.get("file_cover") or {}
            provider_covers = [
                candidate.get("cover")
                for candidate in self._candidate_payloads(result)
                if candidate.get("cover")
            ]
            automatic_cover = cover_info.get("url") or recommendation.get("cover") or next(iter(provider_covers), None)
            if row["cover_source"] == "upload":
                cover = f"/api/v1/files/{row['id']}/cover/image"
            elif row["cover_source"] == "provider" and row["preferred_cover_url"]:
                cover = row["preferred_cover_url"]
            elif row["cover_source"] == "file" and cover_info.get("url"):
                cover = cover_info["url"]
            else:
                cover = automatic_cover
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
                    "hasProblem": False, "updatedAt": row["updated_at"],
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
            if cover:
                group["covers"].append(cover)
            for candidate_cover in [cover_info.get("url"), *provider_covers]:
                if candidate_cover:
                    group["covers"].append(candidate_cover)
            file_review = self._review_items(row, result, parsed, recommendation, embedded, resolved)
            active_replacement = replacement_by_file_id.get(int(row["id"]))
            if active_replacement:
                for review in file_review:
                    review["replacementRequestId"] = str(active_replacement["id"])
                    review["replacementStatus"] = active_replacement["status"]
            inbox.extend(file_review)
            metadata_deferred = bool((result.get("source_status") or {}).get("external_metadata"))
            group["hasProblem"] = (
                group["hasProblem"] or health.get("status") == "error"
                or (not recommendation and not metadata_deferred) or bool(file_review)
            )
            if row["identity_confidence"] is not None:
                group["identityConfidences"].append(int(row["identity_confidence"]))
            if row["updated_at"] > group["updatedAt"]:
                group["updatedAt"] = row["updated_at"]
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
                    "tags": [display_format],
                    "owned": owned, "total": total, "catalogKnown": catalog_known,
                    "unowned": unowned, "missing": None,
                    "ownership": ownership,
                    "format": display_format, "updated": date, "time": clock,
                    "cover": group["covers"][0] if group["covers"] else None,
                    "coverCandidates": list(dict.fromkeys(group["covers"])),
                    "status": status, "files": group["files"],
                    "aliases": group["aliases"],
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
                    "publicationStatus": (
                        "completed" if (group["issueCatalog"] or {}).get("status") == "complete" else
                        "ongoing" if (group["issueCatalog"] or {}).get("status") == "complete_to_date" else
                        "unknown"
                    ),
                    "family": family_membership_map.get(int(group["id"])),
                }
            )
        series.sort(key=lambda item: item["title"].casefold())
        series_by_id = {int(item["id"]): item for item in series}
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
                "coverageTarget": coverage_target,
                "desiredLanguage": row["desired_language"], "status": row["status"],
                "error": row["error"], "createdAt": row["created_at"],
                "updatedAt": row["updated_at"], "requestedDate": requested_date,
                "requestedTime": requested_time,
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
                "downloadTitle": download["release_title"] if download else None,
                "downloadDestination": download["destination"] if download else None,
                "downloadError": download["error"] if download else None,
                "createdAt": job_row["created_at"], "updatedAt": job_row["updated_at"],
                "queuedDate": queued_date, "queuedTime": queued_time,
            })
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
            "requests": requests,
            "replacementRequests": replacement_requests,
            "files": file_summaries,
            "inbox": inbox,
            "enrichment": enrichment,
            "stats": {
                "series": len(series), "families": len(families), "files": len(rows), "needAttention": len(inbox),
                "damaged": sum(1 for item in inbox if item["severity"] == "error"),
                "unownedIssues": sum(item["unowned"] or 0 for item in series),
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
        if not recommendation and health.get("status") != "error" and not metadata_deferred:
            add(
                "no_match", "No confident metadata match",
                "The filename and available sources did not produce a recommendation. Review the parsed identity before accepting a match.",
                "warning",
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
