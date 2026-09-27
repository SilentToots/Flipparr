"""Age ratings: one scale for every source, and who may read what.

A comic's rating comes from three places, in order of trust: the admin's own
mark on a run; what Metron says about its issues; and what the publisher
printed on the cover, read by the vision model. Each says it differently --
Metron's "Teen Plus", DC's "13+ TEEN", Marvel's "RATED T+", Vertigo's
"suggested for mature readers" -- so everything is mapped onto four steps:

    everyone < teen < teen_plus < mature

A profile may have a highest step it can read, and may or may not be shown
comics with no rating at all (most Image and Boom books print none, and
Metron has no rating for about half of issues). With no highest step, a
profile reads everything, rated or not.

Pure: no I/O, importable without the app, like `access_policy`.
"""

from __future__ import annotations

import re

RATINGS = ("everyone", "teen", "teen_plus", "mature")
RATING_LABELS = {"everyone": "Everyone", "teen": "Teen", "teen_plus": "Teen+", "mature": "Mature"}
_RANK = {rating: index for index, rating in enumerate(RATINGS)}


def rank(rating: str | None) -> int | None:
    return _RANK.get(rating or "")


def strictest(*ratings: str | None) -> str | None:
    """The most grown-up of the ratings known, or None when none is."""
    known = [rating for rating in ratings if rating in _RANK]
    return max(known, key=_RANK.__getitem__) if known else None


def normalize_rating(text: object) -> str | None:
    """A rating as a provider or a cover says it, on the one scale; None when
    it says nothing (blank, "Unknown", "NONE", or words that are no rating).

    Checked from the most grown-up down, so "RATED T+" is Teen+ and not Teen,
    and a cover that says both "13+" and "17+" is read as the stricter.
    """
    value = " ".join(str(text or "").upper().replace("’", "'").split())
    if not value or value in {"NONE", "UNKNOWN", "UNRATED", "NOT RATED", "N/A", "NO RATING"}:
        return None
    # A letter alone ("T", "M", "E", Marvel's old "A") is a rating only as a
    # mark -- a few words at most -- never as a word in a sentence.
    words = set(re.findall(r"[A-Z0-9+]+", value)) if len(value.split()) <= 3 else set()
    if (
        "EXPLICIT" in value or "ADULT" in value or "MATURE" in value or "PARENTAL ADVISORY" in value
        or re.search(r"\b1[78]\s*\+", value) or words & {"M", "MA", "MAX", "X18+", "R18+"}
    ):
        return "mature"
    if "TEEN PLUS" in value or "T+" in words or re.search(r"\bT\+", value) or re.search(r"\b15\s*\+", value):
        return "teen_plus"
    if "TEEN" in value or re.search(r"\b1[23]\s*\+", value) or words & {"T", "PG"}:
        return "teen"
    if "ALL AGES" in value or "EVERYONE" in value or "CCA" in words or "COMICS CODE" in value or words & {"E", "A", "G"}:
        return "everyone"
    return None


def allows(rating: str | None, highest: str | None, allow_unrated: bool) -> bool:
    """Whether a profile with this highest step (None: no limit) may see a
    comic with this rating (None: unrated)."""
    if highest not in _RANK:
        return True
    if rating not in _RANK:
        return bool(allow_unrated)
    return _RANK[rating] <= _RANK[highest]


# What the vision model is asked about a cover. It is told what the marks look
# like and where they sit, to answer with what is printed or NONE, and never to
# judge the artwork: a rating is the publisher's, not the model's.
COVER_PROMPT = (
    "This is the front cover of a comic book. Many publishers print an age rating on the cover: "
    "a small box or line of text such as 'E', 'ALL AGES', 'T', 'TEEN', '13+ TEEN', 'T+', 'RATED T+', "
    "'17+', 'M', 'MATURE', 'PARENTAL ADVISORY', 'EXPLICIT CONTENT', or the words "
    "'suggested for mature readers'. Look closely near the barcode, the price, the publisher's logo "
    "and the issue number. Reply with only the rating exactly as it is printed, or NONE if no rating "
    "is printed on this cover. Do not judge the artwork or guess."
)


def rating_from_cover_answer(answer: object) -> tuple[str | None, str | None]:
    """The rating a cover answer names, and the printed words it was read
    from (for the admin to see), or (None, None)."""
    text = " ".join(str(answer or "").strip().strip("'\"`").split())[:80]
    rating = normalize_rating(text)
    return (rating, text) if rating else (None, None)
