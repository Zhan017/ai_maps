"""Text normalization helpers used by matching."""
from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
# Strip punctuation/symbols but keep Unicode letters + digits (Cyrillic, Kazakh, etc.)
_PUNCT = re.compile(r"[^\w ]+", flags=re.UNICODE)


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    # Normalize Unicode (e.g. accent forms) then casefold for cross-script case-insensitivity.
    s = unicodedata.normalize("NFKC", name).casefold().strip()
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def tokenize(text: str | None) -> list[str]:
    return normalize_name(text).split() if text else []


def normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return re.sub(r"\D+", "", phone)


def normalize_website(url: str | None) -> str:
    if not url:
        return ""
    s = url.lower().strip()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    s = re.sub(r"^www\.", "", s)
    return s
