"""Reading lists as other apps write them, read into one shape.

Two formats: CBL, the XML list ComicRack made and that Kavita, Komga and the
community's CBL-ReadingLists repository still use, and the Comic Reading List
JSON Standard (1.0). Pure -- bytes in, a list out -- so the parsing is tested
away from the server, and nothing here reads a file or a network.

An item names its series, its number and, when the file carries one, the
issue's id at a catalog (Comic Vine in nearly every community list, Metron
in newer ones); what the library holds of it is worked out elsewhere.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

MAX_ITEMS = 2000

# How the two formats spell a catalog, mapped to how the library does. The
# order is the preference when a file names more than one: the library's
# runs are linked at Metron first, Comic Vine next.
_PROVIDERS = {
    "metron": "metron",
    "cv": "comic_vine", "comicvine": "comic_vine", "comic vine": "comic_vine", "comic_vine": "comic_vine",
    "gcd": "gcd", "grandcomicsdatabase": "gcd", "grand comics database": "gcd",
}
_PREFERRED = ("metron", "comic_vine", "gcd")

NOT_A_LIST = "This is not a CBL reading list"


def parse_reading_list(data: bytes | str) -> dict[str, Any]:
    """`{name, description, publisher, coverUrls, format, items}`; items are
    `{provider, providerSeriesId, providerIssueId, seriesTitle, seriesYear,
    number, coverDate, issueType}` in the file's order."""
    text = (data.decode("utf-8-sig", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data or "")).strip()
    if not text:
        raise ValueError("This file is empty")
    if text.startswith("{"):
        parsed = _parse_json(text)
    elif text.startswith("<"):
        parsed = _parse_xml(text)
    else:
        raise ValueError(NOT_A_LIST)
    if not parsed["items"]:
        raise ValueError("This reading list has no issues in it")
    if len(parsed["items"]) > MAX_ITEMS:
        raise ValueError(f"A story arc can hold up to {MAX_ITEMS} issues")
    return parsed


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _year(value: Any) -> int | None:
    match = re.fullmatch(r"\s*((?:19|20)\d{2})\s*", str(value or ""))
    return int(match.group(1)) if match else None


def _pick_ids(ids: dict[str, tuple[str, str]]) -> tuple[str | None, str | None, str | None]:
    for provider in _PREFERRED:
        if provider in ids:
            series_id, issue_id = ids[provider]
            return provider, series_id or None, issue_id or None
    return None, None, None


def _item(title: Any, year: Any, number: Any, ids: dict[str, tuple[str, str]], *,
          cover_date: Any = None, issue_type: Any = None) -> dict[str, Any] | None:
    series_title, issue_number = _clean(title), _clean(number)
    if not series_title or not issue_number:
        return None
    provider, series_id, issue_id = _pick_ids(ids)
    return {
        "provider": provider, "providerSeriesId": series_id, "providerIssueId": issue_id,
        "seriesTitle": series_title, "seriesYear": _year(year), "number": issue_number,
        "coverDate": _clean(cover_date) or None, "issueType": _clean(issue_type) or None,
    }


# ---- CBL (XML) ---------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _parse_xml(text: str) -> dict[str, Any]:
    # A list is data, never a document with a DTD: an entity declaration has
    # no business here and is refused rather than expanded.
    if re.search(r"<!(?:DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise ValueError(NOT_A_LIST)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"{NOT_A_LIST}: {exc}") from exc
    if _local(root.tag) != "ReadingList":
        raise ValueError(NOT_A_LIST)
    children = {_local(child.tag): child for child in root}
    books = children.get("Books")
    items = []
    for book in (list(books) if books is not None else []):
        if _local(book.tag) != "Book":
            continue
        ids: dict[str, tuple[str, str]] = {}
        for database in book:
            if _local(database.tag) != "Database":
                continue
            provider = _PROVIDERS.get(_clean(database.get("Name")).lower())
            if provider:
                ids[provider] = (_clean(database.get("Series")), _clean(database.get("Issue")))
        # `Volume` is the series' start year in every list that has one;
        # a volume number is not a year and says nothing about the era.
        item = _item(book.get("Series"), book.get("Volume") or book.get("Year"), book.get("Number"), ids)
        if item:
            items.append(item)
    return {
        "format": "cbl",
        "name": _clean(children["Name"].text if children.get("Name") is not None else ""),
        "description": None, "publisher": None, "coverUrls": [], "items": items,
    }


# ---- Comic Reading List JSON Standard 1.0 ------------------------------------

def _parse_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{NOT_A_LIST}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("issueList"), list):
        raise ValueError(NOT_A_LIST)
    details = payload.get("listDetails") if isinstance(payload.get("listDetails"), dict) else {}
    items = []
    for entry in payload["issueList"]:
        if not isinstance(entry, dict):
            continue
        ids: dict[str, tuple[str, str]] = {}
        for record in entry.get("id") or []:
            if not isinstance(record, dict):
                continue
            provider = _PROVIDERS.get(_clean(record.get("name")).lower())
            if provider:
                ids[provider] = (_clean(record.get("series")), _clean(record.get("issue")))
        item = _item(entry.get("seriesName"), entry.get("seriesStartYear"), entry.get("issueNumber"), ids,
                     cover_date=entry.get("issueCoverDate"), issue_type=entry.get("issueType"))
        if item:
            items.append(item)
    covers = [str(url) for url in (details.get("coverImageURLs") or []) if isinstance(url, str) and url.startswith("http")]
    return {
        "format": "json",
        "name": _clean(details.get("name")),
        "description": _clean(details.get("description")) or None,
        "publisher": _clean(details.get("publisher")) or None,
        "coverUrls": covers, "items": items,
    }
