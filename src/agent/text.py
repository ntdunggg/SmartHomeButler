"""Shared Vietnamese text normalization for the multi-agent pipeline."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def strip_diacritics(text: str) -> str:
    """Remove Vietnamese diacritics so matching also works for unaccented input.

    The transform is character-for-character (marks are combining code points, "đ"→"d"),
    so the folded string stays index-aligned with its source. `Pattern`/`TextView` rely on
    that alignment to look back at what the user actually typed under a folded match.
    """
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", without_marks).replace("đ", "d").replace("Đ", "D")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass(frozen=True, slots=True)
class TextView:
    """Normalized input in both accented and accent-folded forms."""

    raw: str
    folded: str

    @classmethod
    def of(cls, text: str) -> TextView:
        normalized = normalize(text)
        return cls(raw=normalized, folded=strip_diacritics(normalized))

    def accepts_folded(self, start: int, end: int) -> bool:
        """Is a folded match at [start, end) evidence about what the user WROTE?

        Folding is lossy: "mắt" (eye) and "mát" (cool) both fold to "mat", "đóng" (close)
        collides with "đồng" (agree), "bát" (bowl) with "bật" (turn on). Accepting every
        folded hit makes those collisions look like real cues and grounds the turn on the
        wrong dimension — the eval saw "cho phòng khách dịu mắt hơn" planned onto the air
        conditioner. A folded hit is trustworthy only where the user typed no diacritics
        there, which is the case folding exists for; where they DID accent the span, the
        accented pass already had its say and the folded hit is a collision, not a cue.
        """
        if len(self.raw) != len(self.folded):  # defensive: alignment is the whole premise
            return True
        return self.raw[start:end] == self.folded[start:end]

    def has(self, keyword: str) -> bool:
        return _keyword_pattern(keyword).search(self) is not None


class Pattern:
    """Regex matched against both accented and accent-folded input."""

    __slots__ = ("_accented", "_folded")

    def __init__(self, pattern: str) -> None:
        self._accented = re.compile(pattern)
        self._folded = re.compile(strip_diacritics(pattern))

    def search(self, view: TextView) -> re.Match[str] | None:
        hit = self._accented.search(view.raw)
        if hit is not None:
            return hit
        # Scan every folded hit, not just the first: a collision early in the sentence
        # must not hide a genuine unaccented cue later in it.
        for candidate in self._folded.finditer(view.folded):
            if view.accepts_folded(candidate.start(), candidate.end()):
                return candidate
        return None


_KEYWORD_CACHE: dict[str, Pattern] = {}


def _keyword_pattern(keyword: str) -> Pattern:
    """Whole-word keyword matcher.

    `keyword in text` also fires inside longer words — "to" (loud) inside "tối" (dark),
    "mở" (open) inside "mở khoá" (unlock) — so a bare substring test silently picks the
    wrong dimension. Word edges are the boundary every caller means; accent handling stays
    the shared rule in `Pattern`.
    """
    cached = _KEYWORD_CACHE.get(keyword)
    if cached is None:
        cached = Pattern(rf"(?<!\w){re.escape(normalize(keyword))}(?!\w)")
        _KEYWORD_CACHE[keyword] = cached
    return cached
