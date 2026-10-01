"""Small text helpers shared across stages."""
from __future__ import annotations

import re


def normalize_title(title: str | None) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed: the dedup key for titles."""
    if not title:
        return ""
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def deinvert_abstract(inverted_index: dict | None) -> str:
    """Plain text from OpenAlex's `abstract_inverted_index` ({word: [positions]})."""
    if not inverted_index:
        return ""
    word_positions = sorted((pos, word) for word, positions in inverted_index.items()
                            for pos in positions)
    return " ".join(w for _, w in word_positions)
