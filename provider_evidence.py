"""Lossless, ownership-neutral normalization at native provider boundaries.

Pure functions: they
do not fetch data, infer an edition's identity, or accept canonical coverage.
Descriptions remain untrusted source text, not HTML to render or instructions.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

EVIDENCE_VERSION = 1
METRON_ORIGINAL_TYPES = frozenset(
    {"Single Issue", "Limited Series", "Ongoing Series", "One-Shot"}
)
COMIC_VINE_ISSUE_FIELDS = (
    "id",
    "name",
    "issue_number",
    "volume",
    "description",
    "cover_date",
    "store_date",
    "image",
    "site_detail_url",
)
COMIC_VINE_VOLUME_FIELDS = (
    "id",
    "name",
    "start_year",
    "publisher",
    "count_of_issues",
    "description",
    "site_detail_url",
    "image",
)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _identifier(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    return value if re.fullmatch(r"[1-9][0-9]*", value) else None


def _number(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value).strip().lstrip("#") or None


def _name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _text(value.get("name")) or _text(value.get("series"))
    return _text(value)


def metron_reprint_evidence(reprints: Any) -> list[dict[str, Any]]:
    """Preserve each native {id, issue: display} relationship and its identity.

    Display parsing supplies review labels only. Even a well-formed label cannot
    prove the target run or completeness. Missing type means unknown, never full.
    Nested legacy fixtures remain readable, but malformed elements are retained as
    unparsed evidence instead of silently disappearing.
    """
    if reprints is None:
        return []
    if not isinstance(reprints, list):
        raise ValueError("Metron reprints must be an array")
    result = []
    for raw in reprints:
        item = raw if isinstance(raw, Mapping) else {}
        nested = item.get("issue") if isinstance(item.get("issue"), Mapping) else item
        target_id = _identifier(nested.get("id")) or _identifier(item.get("id"))
        series = _name(nested.get("series")) or _text(item.get("series_name"))
        number = _number(nested.get("number")) or _number(nested.get("issue_number"))
        display = (
            _text(item.get("issue"))
            or _text(item.get("display"))
            or _text(item.get("name"))
            or _text(item.get("label"))
            or _text(raw)
        )
        if display and (not series or not number):
            match = re.fullmatch(r"(.+?)\s+#([0-9]+(?:\.[0-9]+)?[A-Za-z]?)", display)
            if match:
                series = series or match.group(1).strip()
                number = number or match.group(2)
        # Never strip an era from the review label and accidentally bind a
        # reprint to another same-title original run in the compatibility UI.
        semantic_text = " ".join(
            str(item.get(k) or "")
            for k in (
                "relation_type",
                "reprint_type",
                "type",
                "notes",
                "note",
            )
        ).casefold()
        if re.search(r"\b(excerpt|extract|preview)\b", semantic_text):
            kind = "excerpt"
        elif item.get("partial") is True or re.search(
            r"\b(partial|story|material from)\b", semantic_text
        ):
            kind = "partial_story"
        else:
            # A bare provider reprint edge is not a completeness assertion.
            # Full claims must come from a separately validated statement.
            kind = "unknown"
        result.append(
            {
                "targetProviderId": target_id,
                "display": display,
                "seriesLabel": series,
                "issueNumber": number,
                "relationKind": kind,
                "status": "reviewable" if series and number else "unparsed",
                "raw": copy.deepcopy(raw),
            }
        )
    return result


def native_issue_evidence(provider: str, issue: Mapping[str, Any]) -> dict[str, Any]:
    """Retain edition/issue evidence without treating a provider ordinal as kind.

    Comic Vine's volume is a publication container, not a collected volume number.
    Its description is kept verbatim, including links and paragraph boundaries.
    The presence of 'collects' is deliberately not enough to create ownership.
    """
    if provider not in {"metron", "comic_vine"}:
        raise ValueError("Unsupported native issue provider")
    if not isinstance(issue, Mapping):
        raise ValueError("Provider issue must be an object")
    publication = issue.get("series" if provider == "metron" else "volume")
    publication = publication if isinstance(publication, Mapping) else {}
    description_key = "desc" if provider == "metron" else "description"
    description = issue.get(description_key)
    if description is not None and not isinstance(description, str):
        raise ValueError("Provider description must be text or null")
    return {
        "version": EVIDENCE_VERSION,
        "provider": provider,
        "providerIssueId": _identifier(issue.get("id")),
        "providerPublicationId": _identifier(publication.get("id")),
        "providerNumber": _number(
            issue.get("number" if provider == "metron" else "issue_number")
        ),
        "publication": copy.deepcopy(dict(publication)),
        "description": description,
        "descriptionFormat": "plain_text" if provider == "metron" else "html",
        "descriptionSupplied": description_key in issue,
        "reprints": (
            metron_reprint_evidence(issue.get("reprints"))
            if provider == "metron"
            else []
        ),
        "reprintsSupplied": "reprints" in issue if provider == "metron" else False,
        "gcdId": _identifier(issue.get("gcd_id")) if provider == "metron" else None,
        "comicVineId": (
            _identifier(issue.get("cv_id"))
            if provider == "metron"
            else _identifier(issue.get("id"))
        ),
        "isbn": _text(issue.get("isbn")) if provider == "metron" else None,
    }
