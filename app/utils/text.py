from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()
