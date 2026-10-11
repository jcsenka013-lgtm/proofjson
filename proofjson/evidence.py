"""Quote checks against the text of the cited page.

A quote is verified when it can be found on that page after light normalization,
or when a fuzzy window matches without changing any digits. Digit changes stay
unverified so a wrong amount cannot borrow trust from a nearby figure.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

FUZZY_THRESHOLD = 0.90
MIN_FUZZY_CHARS = 12

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)

MatchMethod = Literal["exact", "normalized", "fuzzy", "none"]


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    verified: bool
    method: MatchMethod


def normalize_text(text: str) -> str:
    """NFKC, casefold, and collapse whitespace."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\u00a0", " ")
    normalized = normalized.casefold()
    return _WHITESPACE.sub(" ", normalized).strip()


def squash_text(text: str) -> str:
    """Normalization plus punctuation removed, so ``$128.40`` and ``128.40`` agree."""
    return normalize_text(_PUNCTUATION.sub("", normalize_text(text)))


def _digits(text: str) -> str:
    return "".join(char for char in text if char.isdigit())


def _ratio_if_digits_match(needle: str, window: str, needle_digits: str) -> float:
    if needle_digits and _digits(window) != needle_digits:
        return 0.0
    if not needle or not window:
        return 0.0
    return SequenceMatcher(a=needle, b=window).ratio()


def best_fuzzy_ratio(needle: str, haystack: str) -> float:
    """Best SequenceMatcher ratio of ``needle`` against a window of ``haystack``."""
    if not needle or not haystack:
        return 0.0
    needle_digits = _digits(needle)
    if len(haystack) <= len(needle):
        return _ratio_if_digits_match(needle, haystack, needle_digits)

    best = 0.0
    widths = {len(needle), min(len(haystack), len(needle) + max(2, len(needle) // 5))}
    for width in widths:
        limit = len(haystack) - width + 1
        # Pages in v0.1 are short. Step 1 keeps a one-character typo from being skipped.
        step = 1 if limit <= 12_000 else max(1, width // 8)
        for start in range(0, limit, step):
            ratio = _ratio_if_digits_match(needle, haystack[start : start + width], needle_digits)
            if ratio > best:
                best = ratio
                if best >= 0.995:
                    return best
    return best


def verify_quote(
    quote: str,
    page_text: str,
    *,
    threshold: float = FUZZY_THRESHOLD,
) -> QuoteMatch:
    """Return whether ``quote`` is supported by ``page_text``."""
    if not quote or not quote.strip() or not page_text or not page_text.strip():
        return QuoteMatch(False, "none")
    if quote in page_text:
        return QuoteMatch(True, "exact")

    normalized_quote = normalize_text(quote)
    normalized_page = normalize_text(page_text)
    if normalized_quote and normalized_quote in normalized_page:
        return QuoteMatch(True, "normalized")

    squashed_quote = squash_text(quote)
    squashed_page = squash_text(page_text)
    if squashed_quote and squashed_quote in squashed_page:
        return QuoteMatch(True, "normalized")

    if len(squashed_quote) < MIN_FUZZY_CHARS:
        return QuoteMatch(False, "none")
    if best_fuzzy_ratio(normalized_quote, normalized_page) >= threshold:
        return QuoteMatch(True, "fuzzy")
    return QuoteMatch(False, "none")
