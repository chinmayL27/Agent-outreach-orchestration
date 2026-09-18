"""HTML -> clean text, plus prompt-injection hygiene for untrusted site text."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

BEGIN_MARKER = "BEGIN_UNTRUSTED_WEBSITE_DATA"
END_MARKER = "END_UNTRUSTED_WEBSITE_DATA"

_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_MARKER_LIKE = re.compile(
    r"(BEGIN|END)_UNTRUSTED_WEBSITE_DATA|<\s*/?\s*(system|assistant|user)\s*>",
    re.IGNORECASE,
)


def html_to_text(markup: str) -> str:
    """Strip scripts/styles and collapse a page down to readable text."""
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = html.unescape(text)
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", text).strip()


def sanitize_untrusted(text: str, *, max_chars: int = 6000) -> str:
    """Neutralize delimiter spoofing and cap the size of untrusted input.

    Website text is treated as attacker-controlled data.  It must never be
    able to close the data envelope or impersonate a system turn.
    """
    cleaned = _MARKER_LIKE.sub("[redacted-marker]", text)
    cleaned = cleaned.replace("\x00", "")
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n[truncated]"
    return cleaned.strip()


def wrap_untrusted(text: str, *, max_chars: int = 6000) -> str:
    """Put untrusted site text inside a clearly labelled data envelope."""
    return f"{BEGIN_MARKER}\n{sanitize_untrusted(text, max_chars=max_chars)}\n{END_MARKER}"


def word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def title_case_name(value: str) -> str:
    """Title-case a SHOUTING organization name without mangling acronyms.

    NPPES stores names in upper case ("ABC DERMATOLOGY, P.C."), which reads as
    shouting in an email.  Short vowel-less tokens (ABC, PC, NW) and dotted
    initials (P.C.) stay upper case; everything else is title-cased.
    """
    if not value or not (value.isupper() or value.islower()):
        return value
    small = {"of", "and", "the", "at", "for", "in", "on", "to", "by"}
    # Short tokens are usually acronyms in clinic names (ABC, ENT, PC), but a
    # handful of ordinary short words are common too.
    short_words = {
        "our", "new", "all", "one", "two", "ear", "eye", "sun", "spa", "oak",
        "bay", "day", "sky", "elm", "top", "low", "big", "red", "hip", "jaw",
        "arm", "leg", "kid", "men", "you", "her", "his", "art", "gem", "joy",
        "old", "west", "east", "care", "kids", "san", "los", "las", "del",
        "rio", "via", "ave", "way", "st",
    }
    words: list[str] = []
    for index, word in enumerate(value.split()):
        stripped = word.strip(",.")
        letters = [c for c in stripped if c.isalpha()]
        lowered = "".join(letters).lower()
        is_acronym = (
            bool(letters)
            and lowered not in short_words
            and lowered not in small
            and (len(letters) <= 3 or not set("aeiouy") & set(lowered))
        )
        is_initials = bool(re.fullmatch(r"(?:[A-Za-z]\.){2,},?", word))
        if is_acronym or is_initials:
            words.append(word.upper())
        elif index and stripped.lower() in small:
            words.append(word.lower())
        else:
            words.append(word.capitalize())
    return " ".join(words)
