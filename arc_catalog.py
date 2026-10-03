"""Finding a story arc by name, forgivingly, among the names Flipparr keeps.

Two lists of names are kept locally and refreshed once a day (see app.py's
arc index): Metron's story arcs, and the community's reading lists -- the
CBL files in a public GitHub repository, fetched only when one is chosen.
Matching happens here, against those lists, so suggesting arcs as someone
types never asks a remote catalog anything.

Pure: no network, no files.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# Words that say nothing about which arc is meant: "War of Realms" finds "War
# of the Realms", and "the event" or "arc" on the end changes nothing.
STOPWORDS = frozenset({"the", "of", "a", "an", "and", "in", "on", "to", "arc", "arcs", "event", "events",
                       "story", "storyline", "saga", "crossover", "reading", "order", "list"})

# The community's library (https://github.com/DieselTech/CBL-ReadingLists):
# reading lists for every publisher, verified against Comic Vine. It carries
# no licence, so Flipparr ships none of it; a list is fetched from GitHub
# when someone picks it, as if they had downloaded it themselves.
COMMUNITY_REPO = "DieselTech/CBL-ReadingLists"
COMMUNITY_BRANCH = "main"


def fold(text: Any) -> str:
    """Lower case, accents and punctuation gone, words single-spaced."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.replace("&", " and ")).split())


def query_words(query: Any) -> list[str]:
    """The words of a search that pick out an arc: no stopwords, and no year
    -- "War of the Realms 2019" is a hint, not part of the name."""
    return [word for word in fold(query).split() if word not in STOPWORDS and not re.fullmatch(r"(19|20)\d\d", word)]


def name_matches(words: list[str], name: Any) -> bool:
    """Whether every query word starts a word of the name, in any order:
    "realms war" and "war of realm" both find "War of the Realms"."""
    if not words:
        return False
    tokens = fold(name).split()
    return all(any(token.startswith(word) for token in tokens) for word in words)


def _rank(query: str, words: list[str], name: str) -> tuple[int, int, int, str]:
    folded = fold(name)
    significant = [token for token in folded.split() if token not in STOPWORDS]
    return (
        0 if folded == fold(query) else 1,                                   # the name exactly
        0 if " ".join(words) == " ".join(significant) else 1,                # its words exactly
        len(significant) - len(words),                                       # fewest extra words
        folded,
    )


def search(entries: Iterable[dict[str, Any]], query: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    """Entries (each with a "name") whose names match, best first."""
    words = query_words(query)
    found = [entry for entry in entries if name_matches(words, entry.get("name"))]
    return sorted(found, key=lambda entry: _rank(str(query or ""), words, str(entry.get("name") or "")))[:limit]


def _clean_list_name(stem: str) -> tuple[str, list[str], str | None]:
    """A community file's name as a person would say it, its bracketed tags,
    and a year when the name leads with one.

    "[Marvel] (2019-06) War of the Realms (Official)" is "War of the Realms",
    tagged "Official", 2019; "[2019] War of the Realms (Marvel Comics)(LoCG)"
    is the same name, tagged "Marvel Comics" and "LoCG".
    """
    year = None
    text = stem.strip()
    while True:
        lead = re.match(r"^\s*[\[(]([^\])]*)[\])]\s*", text)
        if not lead:
            break
        inner = lead.group(1).strip()
        found = re.match(r"^((?:19|20)\d\d)(?:-\d\d)?$", inner)
        if found:
            year = year or found.group(1)
        text = text[lead.end():]
    tags: list[str] = []
    while True:
        tail = re.search(r"\s*[\[(]([^\])]*)[\])]\s*$", text)
        if not tail or tail.start() == 0:
            break
        tags.insert(0, tail.group(1).strip())
        text = text[:tail.start()]
    return " ".join(text.split()) or stem, tags, year


def community_lists(paths: Iterable[str], *, repo: str = COMMUNITY_REPO, branch: str = COMMUNITY_BRANCH) -> list[dict[str, Any]]:
    """The community repository's reading lists, from its file tree: each
    .cbl file with a readable name, its publisher (the top folder), the
    kind of list (the next folder: Events, Characters, ...), its tags (which
    guide it follows: Official, CBRO, LoCG), and the link to it."""
    lists = []
    for path in paths:
        if not str(path).lower().endswith(".cbl"):
            continue
        parts = str(path).split("/")
        stem = re.sub(r"\.cbl$", "", parts[-1], flags=re.IGNORECASE)
        name, tags, year = _clean_list_name(stem)
        lists.append({
            "id": str(path),
            "name": name,
            "publisher": parts[0] if len(parts) > 1 else "",
            "group": parts[1] if len(parts) > 2 else "",
            "tags": tags,
            "year": year,
            "url": f"https://github.com/{repo}/blob/{branch}/{path}",
        })
    return lists
