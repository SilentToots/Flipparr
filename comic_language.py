"""Reading the language a comic states about itself.

Shared because two places need the same answer from different evidence: the
acquisition path reads a release name before anything is downloaded, and the
catalog reads a file's embedded metadata after. A French edition filed as
Saga #2 passed both unremarked, so the maps behind the two checks have to be
one map.

Only what is stated is ever reported. Guessing from a publisher or a scanner's
name would refuse far more than it caught.
"""

from __future__ import annotations

import re

# Language words, matched anywhere and case-insensitively.
LANGUAGE_WORDS = {
    "english": "en", "eng": "en",
    "french": "fr", "francais": "fr", "français": "fr", "vf": "fr", "vostfr": "fr",
    "spanish": "es", "espanol": "es", "español": "es", "castellano": "es",
    "german": "de", "deutsch": "de",
    "italian": "it", "italiano": "it",
    "portuguese": "pt", "portugues": "pt", "português": "pt", "brazilian": "pt",
    "russian": "ru", "japanese": "ja", "korean": "ko", "chinese": "zh",
    "polish": "pl", "dutch": "nl", "turkish": "tr", "swedish": "sv",
    "danish": "da", "finnish": "fi", "hungarian": "hu", "czech": "cs",
    "greek": "el", "hebrew": "he", "arabic": "ar",
    # Manga scans write these; "Jpn" in mixed case escapes the capitals-only
    # code table below.
    "jpn": "ja", "indonesian": "id", "vietnamese": "vi",
}

# Short codes, matched only in capitals: "it", "de" and "no" are ordinary words,
# and a release writes a tag in capitals where a title does not.
LANGUAGE_CODES = {
    "EN": "en", "ENG": "en",
    "FR": "fr", "FRA": "fr", "FRE": "fr", "VF": "fr",
    "ES": "es", "ESP": "es", "SPA": "es",
    "DE": "de", "GER": "de", "DEU": "de",
    "IT": "it", "ITA": "it",
    "PT": "pt", "POR": "pt", "PTBR": "pt",
    "RU": "ru", "RUS": "ru",
    "JP": "ja", "JPN": "ja",
    "KR": "ko", "KOR": "ko",
    "PL": "pl", "POL": "pl",
    "NL": "nl", "NLD": "nl",
    "TR": "tr", "TUR": "tr",
}

LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "pl": "Polish", "nl": "Dutch",
    "tr": "Turkish", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "hu": "Hungarian", "cs": "Czech", "el": "Greek", "he": "Hebrew",
    "ar": "Arabic", "id": "Indonesian", "vi": "Vietnamese",
}

_WORDS = re.compile(r"[A-Za-zÀ-ÿ]+")


def language_name(code) -> str:
    value = str(code or "").strip().casefold()
    return LANGUAGE_NAMES.get(value, value.upper() or "another language")


def normalize_language(value) -> str | None:
    """A bare language tag, as embedded metadata records it.

    ComicInfo writes LanguageISO and EPUB writes a language element, so the
    value is usually already a code -- "fr", "en-GB" -- rather than prose.
    """
    text = str(value or "").strip()
    if not text:
        return None
    # "en-GB" and "pt_BR" name a language plus a region; the language is enough.
    head = re.split(r"[-_]", text)[0]
    word = LANGUAGE_WORDS.get(head.casefold())
    if word:
        return word
    code = LANGUAGE_CODES.get(head.upper())
    return code if code else None


def detect_language(text) -> str | None:
    """The language a name states, or None when it states none.

    None when nothing is said and None when two languages are said: a dual
    edition is not a wrong one, and is left to the rest of the evidence.
    """
    found: set[str] = set()
    for token in _WORDS.findall(str(text or "")):
        word = LANGUAGE_WORDS.get(token.casefold())
        if word:
            found.add(word)
            continue
        if token.isupper():
            code = LANGUAGE_CODES.get(token)
            if code:
                found.add(code)
    return found.pop() if len(found) == 1 else None
